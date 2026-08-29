"""
score.py — join yesterday's logged predictions to what actually happened.

Reads predictions, reads outcomes, writes scores. It never writes to the prediction prefix
and never recomputes a prediction: recomputing what the model "would have said" runs
today's model against a known outcome, which is leakage, and CLAUDE.md calls any code that
does it a bug rather than an optimisation. Its IAM role has no write access to
`predictions/`, so that rule is enforced by credentials and not by discipline (D39).

Where the numbers come from
---------------------------
Outcomes are refetched from `getTrainMovementsXML` for the service date. That endpoint
honours `TrainDate` and serves real history back to 2007, so a night the scorer did not
run is recoverable by running it later — the reliability principle in CLAUDE.md. Station
boards are not used for outcomes: they carry `Exparrival` and `Duein`, which are the
operator's *prediction*, not a confirmed arrival.

The operator baseline for the head-to-head does come from the archived boards, because
`ExpectedArrival` exists only live and cannot be backfilled.

Methodology is compare_to_operator.py's, not a live approximation of it
----------------------------------------------------------------------
If the live number and the published offline 27% were computed differently they would not
be comparable, and an accuracy page showing two incomparable numbers is worse than one
showing neither. So all four fairness traps from D46 apply here:

1. **Temporal leakage.** Satisfied by construction and more strongly than offline. The
   prediction was made live, from stops that had actually reported at that instant, and
   logged before the outcome existed. Nothing here reconstructs a vantage point.

2. **Feature availability.** Not this file's concern; features.py owns it. Noted so the
   list of four stays the list of four.

3. **Output granularity.** `Exparrival` is minute-precision, actual arrivals are
   6-second. Comparing our to-the-second median against their to-the-minute one hands us
   up to 30 seconds of free accuracy per event, so both variants are reported and the
   minute-rounded one is the defensible headline.

4. **Correlated repeats.** One comparison per (event, lead band), the last statement made
   in that band, on both sides. The generator already limits itself to one target per band
   per train per cycle, but a stop 20 minutes out stays in the 15-30 band for several
   cycles, so the deduplication has to happen here too.

Nothing is silently dropped
---------------------------
Every prediction row lands in exactly one `score_state` and every state is reported.
~31% of movement records never receive an actual time and the documented weak-coverage
lines echo scheduled times, so quietly keeping only the rows that scored cleanly would
bias the scoreboard toward trains that behaved — the failure mode is a *better*-looking
number, which is why it would never be noticed.

    scored              actual arrival present, AutoArrival=1
    echo_suspect        arrival present but AutoArrival != 1; the value may be an echo of
                        the schedule. Kept and reported separately, never blended (D23)
    no_actual_arrival   the stop never reported one
    not_on_route        the refetched journey no longer calls there
    train_not_found     no journey came back for that code and date
    declined            the prediction itself was a decline; no error, but it is the
                        coverage denominator and must be counted

Usage (PowerShell, from the repo root, venv active):

    python src\\score.py --date 2026-08-28
    python src\\score.py --date 2026-08-28 --dry-run
    python src\\score.py --backfill 7
"""

import argparse
import gzip
import io
import json
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from backfill import Pacer, Failure  # noqa: E402
from feedtime import (LEAD_BANDS, delay_seconds, hms,  # noqa: E402
                      iso_train_date, journey_consistent, lead_band, unwrap)
from poll_live import DUBLIN, USER_AGENT, fetch  # noqa: E402

NS = "{http://api.irishrail.ie/realtime/}"
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
TIME_BUDGET_FLOOR_MS = 30_000
# Below this many scored rows a day is too thin for "zero operator matches" to
# mean anything, so the alarm stays quiet rather than firing on a quiet Sunday.
OPERATOR_ALARM_FLOOR = 50
# How long after midnight a service date is treated as finished. Covers a late
# train still running past midnight, and clears quiet hours (00:30-05:30) so the
# feed has stopped changing. The nightly schedule fires at 03:15 UTC, which is
# 04:15 Irish summer time, so this is what actually gates it -- not the cron.
SETTLE = timedelta(hours=6)


def feed_date(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.day:02d} {MONTHS[d.month - 1]} {d.year}"


def day_seconds(stamp: str, service_day: str) -> float | None:
    """Seconds from midnight of the SERVICE date to an ISO instant.

    Deliberately allowed to exceed 86400. A train that departs on the 26th and arrives at
    00:02 on the 27th has one continuous journey, and the movements feed's times are
    unwrapped the same way. Folding this back into 0-86399 would make a post-midnight poll
    look like it happened 23 hours before the departure it followed.
    """
    try:
        t = datetime.fromisoformat(stamp)
    except (ValueError, TypeError):
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=DUBLIN)
    midnight = datetime.combine(date.fromisoformat(service_day),
                                datetime.min.time(), DUBLIN)
    return (t.astimezone(DUBLIN) - midnight).total_seconds()


# ----------------------------------------------------------------- reading

def read_rows(client, bucket, prefix):
    """Every JSONL row under a prefix, gzipped or not. Yields dicts."""
    token, keys = None, []
    while True:
        kw = {"Bucket": bucket, "Prefix": prefix}
        if token:
            kw["ContinuationToken"] = token
        page = client.list_objects_v2(**kw)
        keys += [o["Key"] for o in page.get("Contents", [])]
        if not page.get("IsTruncated"):
            break
        token = page.get("NextContinuationToken")

    for key in sorted(keys):
        body = client.get_object(Bucket=bucket, Key=key)["Body"].read()
        if key.endswith(".gz"):
            body = gzip.decompress(body)
        for line in body.decode("utf-8").splitlines():
            if line.strip():
                yield json.loads(line)


def reconstruct_lead(row):
    """Lead time for a row logged before `lead_sec` was recorded.

    api.py now stamps the lead at prediction time, where the schedule is already unwrapped
    across midnight. Rows written before that carry only the raw wall-clock
    `scheduled_arrival`, so a 23:50 prediction about a 00:20 arrival reconstructs as minus
    23 hours; the correction below puts it back. Without this such rows silently get no
    band, match no operator poll, and vanish from the head-to-head with nothing saying so.
    """
    if row.get("lead_sec") is not None:
        return row["lead_sec"]
    sched = hms(row.get("scheduled_arrival"))
    if sched is None:
        return None
    try:
        made = day_seconds(row.get("predicted_at"), iso_train_date(row["train_date"]))
    except (ValueError, IndexError, KeyError, TypeError):
        return None
    if made is None:
        return None
    lead = sched - made
    return lead + 86400 if lead < -43200 else lead


def load_predictions(client, bucket, prefix, day):
    """One row per (train, station, band): the last statement made in that band.

    Trap 4. The generator already takes at most one target per band per train per cycle,
    but a stop 20 minutes out stays in the 15-30 band across several cycles, so without
    this the same event would be counted three or four times and every interval would
    come out too narrow.
    """
    kept, seen = {}, 0
    for row in read_rows(client, bucket, f"{prefix}/date={day}/"):
        seen += 1
        lead = reconstruct_lead(row)
        band = row.get("lead_band") or lead_band(lead)
        k = (row.get("train_code"), row.get("station_code"), band)
        if k not in kept or row.get("predicted_at", "") > kept[k].get("predicted_at", ""):
            # Written back so a score row always carries the lead it was actually judged
            # on. Copying only `row.get("lead_sec")` left it null on every reconstructed
            # row, which is the value being explained away in the output.
            row["lead_band"], row["lead_sec"] = band, lead
            kept[k] = row
    return list(kept.values()), seen


def load_operator(client, bucket, prefix, days):
    """Archived board rows keyed by (service date, train, station).

    Two partitions are read for one service date, because a board row carries its own
    `Traindate`: a train that departs on the 26th and is still running at 00:30 appears in
    the 27th's partition under service date 26 Aug. Keying on the partition instead of the
    field would silently lose every late-evening comparison.
    """
    out, comp = defaultdict(list), Counter()
    for day in days:
        for row in read_rows(client, bucket, f"{prefix}/expected/date={day}/"):
            comp[board_scope(row)] += 1
            code = (row.get("Traincode") or "").strip().upper()
            stn = (row.get("station_code") or "").strip().upper()
            td = (row.get("Traindate") or "").strip()
            eta = hms(row.get("Exparrival"))
            if not (code and stn and td) or eta is None:
                continue
            try:
                service = iso_train_date(td)
            except (ValueError, IndexError):
                continue
            poll_s = day_seconds(row.get("polled_at"), service)
            if poll_s is None:
                continue
            out[(service, code, stn)].append((poll_s, eta, row.get("station_group", "")))
    return out, comp


def board_scope(row):
    """Is this board entry a train the product can answer about at all?

    The product answers for a train ALREADY IN SERVICE (CLAUDE.md). A station board also
    lists trains that have not departed, and a visitor cannot tell the difference by
    looking. `Origintime` against `polled_at` separates them without another request.

    This is what makes the visitor-facing coverage number measurable rather than asserted.
    On 27 August, 43.1% of 62,575 board rows were trains that had already departed.
    """
    o, p = hms(row.get("Origintime")), hms((row.get("polled_at") or "")[11:19])
    if o is None or p is None:
        return "unknown"
    return "departed" if (p - o) % 86400 < 43200 else "not_yet_departed"


def fetch_journeys(session, pacer, codes, day, time_left=None):
    """Refetch the movements for each distinct train code. Returns {code: stops}.

    One request per train, not per prediction: a journey answers every station on it.
    """
    feed_day, out, failed = feed_date(day), {}, []
    for i, code in enumerate(sorted(codes)):
        if time_left is not None and time_left() < TIME_BUDGET_FLOOR_MS:
            failed += sorted(codes)[i:]
            break
        try:
            body = fetch(session, "getTrainMovementsXML",
                         {"TrainId": code, "TrainDate": feed_day}, pacer,
                         "objtrainmovements")
        except Failure as f:
            failed.append(code)
            print(f"  ! {code}: {f.kind} {f.detail}")
            continue
        out[code] = parse_journey(body)
    return out, failed


def parse_journey(body: bytes):
    import xml.etree.ElementTree as ET
    root = ET.fromstring(body)

    def text(node, tag):
        el = node.find(NS + tag)
        return (el.text or "").strip() if el is not None else ""

    stops = []
    for rec in root.findall(NS + "objTrainMovements"):
        try:
            order = int(text(rec, "LocationOrder"))
        except ValueError:
            continue
        sched, arr = hms(text(rec, "ScheduledArrival")), hms(text(rec, "Arrival"))
        stops.append({"order": order, "loc": text(rec, "LocationCode").upper(),
                      "auto": text(rec, "AutoArrival"),
                      "sched_raw": sched, "arr_raw": arr})
    stops.sort(key=lambda s: s["order"])
    for field, dest in (("sched_raw", "sched"), ("arr_raw", "arr")):
        for s, v in zip(stops, unwrap([x[field] for x in stops])):
            s[dest] = v
    for s in stops:
        s["delay"] = delay_seconds(s["arr"], s["sched"])
    return stops


# ----------------------------------------------------------------- scoring

def score_rows(preds, journeys, operator, missing_codes):
    """Attach an outcome and an error to every prediction. Nothing is discarded."""
    out = []
    for row in preds:
        code, stn = row.get("train_code"), row.get("station_code")
        rec = {k: row.get(k) for k in (
            "prediction_id", "train_date", "train_code", "station_code", "station_group",
            "station_name", "weak_coverage", "lead_sec", "lead_band", "predicted_at",
            "model_version", "outcome", "reason", "source", "sampling_scheme",
            "pred_q10_sec", "pred_q50_sec", "pred_q90_sec", "scheduled_arrival")}

        if row.get("outcome") == "declined":
            out.append({**rec, "score_state": "declined"})
            continue
        if code in missing_codes or code not in journeys:
            out.append({**rec, "score_state": "train_not_found"})
            continue

        stops = journeys[code]
        if not journey_consistent(stops):
            # Reported arrivals move backwards along the route, so at least one time is
            # wrong and magnitude cannot say which. The whole journey is refused rather
            # than guessed at. See feedtime.journey_consistent.
            out.append({**rec, "score_state": "journey_inconsistent"})
            continue
        target = next((s for s in stops if s["loc"] == stn), None)
        if target is None:
            out.append({**rec, "score_state": "not_on_route"})
            continue
        if target["arr"] is None or target["delay"] is None:
            out.append({**rec, "score_state": "no_actual_arrival"})
            continue

        actual = target["delay"]
        q10, q50, q90 = (row.get("pred_q10_sec"), row.get("pred_q50_sec"),
                         row.get("pred_q90_sec"))
        rec.update(
            score_state="scored" if target["auto"] == "1" else "echo_suspect",
            auto_arrival=target["auto"],
            actual_delay_sec=actual,
            model_err_sec=q50 - actual,
            # Trap 3: matched to the operator's minute precision. This is the headline.
            model_err_rounded_sec=round(q50 / 60.0) * 60 - actual,
            interval_hit=bool(q10 <= actual <= q90))

        # Trap 4 on the operator's side: their last statement in the same band, so both
        # sides are the most informed thing each said at that range.
        key = (iso_train_date(row["train_date"]), code, stn)
        best = None
        for poll_s, eta, group in operator.get(key, ()):
            if poll_s >= target["arr"] or target["sched"] is None:
                continue  # a board keeps listing a train past arrival; that is not a
                          # prediction any more
            if lead_band(target["sched"] - poll_s) != rec["lead_band"]:
                continue
            if best is None or poll_s > best[0]:
                best = (poll_s, eta, group)
        if best is not None:
            poll_s, eta, group = best
            # Same midnight unwrap the offline comparison uses: a 00:05 ETA against a
            # 23:58 arrival is seven minutes late, not twenty-three hours early.
            eta_u = eta + (86400 if eta < target["arr"] - 43200 else 0)
            rec.update(operator_err_sec=eta_u - target["arr"],
                       operator_polled_at_sec=poll_s,
                       operator_group=group)
        out.append(rec)
    return out


def summarise(scored, day, seen, failed, board=None):
    """Aggregates for the accuracy page. Head-to-head only on matched events."""
    states = Counter(r["score_state"] for r in scored)
    declines = Counter(r.get("reason") for r in scored if r["score_state"] == "declined")

    def stats(rows, key):
        v = sorted(r[key] for r in rows if r.get(key) is not None)
        if not v:
            return None
        n = len(v)
        return {"n": n, "mae_sec": round(sum(abs(x) for x in v) / n, 1),
                "medae_sec": round(sorted(abs(x) for x in v)[n // 2], 1),
                "bias_sec": round(sum(v) / n, 1)}

    def head_to_head(rows):
        m = [r for r in rows if r.get("operator_err_sec") is not None]
        if not m:
            return None
        model = stats(m, "model_err_rounded_sec")
        oper = stats(m, "operator_err_sec")
        wins = sum(1 for r in m
                   if abs(r["model_err_rounded_sec"]) < abs(r["operator_err_sec"]))
        ties = sum(1 for r in m
                   if abs(r["model_err_rounded_sec"]) == abs(r["operator_err_sec"]))
        return {"matched_events": len(m), "model": model, "operator": oper,
                "model_raw": stats(m, "model_err_sec"),
                "improvement_pct": (round(100 * (oper["mae_sec"] - model["mae_sec"])
                                          / oper["mae_sec"], 1) if oper["mae_sec"] else None),
                "model_wins": wins, "ties": ties, "operator_wins": len(m) - wins - ties}

    clean = [r for r in scored if r["score_state"] == "scored"]
    echo = [r for r in scored if r["score_state"] == "echo_suspect"]
    # Coverage asks whether the model ANSWERED, not whether the answer could later be
    # scored. A row we predicted and then could not grade -- no actual arrival, an
    # inconsistent journey -- was still an answer the visitor received. Counting only
    # gradeable rows would understate coverage by exactly the rows the railway failed
    # to report, which has nothing to do with what we can answer.
    answerable = len(scored) - states["declined"]

    by_group, by_band = {}, {}
    for g in sorted({r.get("station_group") or "(unpolled)" for r in clean}):
        sel = [r for r in clean if (r.get("station_group") or "(unpolled)") == g]
        by_group[g] = {"accuracy": stats(sel, "model_err_rounded_sec"),
                       "interval_coverage_pct": coverage(sel),
                       "head_to_head": head_to_head(sel)}
    for _, _, lb in LEAD_BANDS:
        sel = [r for r in clean if r.get("lead_band") == lb]
        if sel:
            by_band[lb] = {"accuracy": stats(sel, "model_err_rounded_sec"),
                           "interval_coverage_pct": coverage(sel),
                           "head_to_head": head_to_head(sel)}

    warnings = []
    matched = sum(1 for r in clean if r.get("operator_err_sec") is not None)
    # A zero here is the exact signature of a stale PollerPrefix, and it wears a
    # dangerously good disguise: every accuracy figure still populates, only the
    # comparison is absent, so the page reads as a quiet railway and not a
    # misconfiguration. Loud in the summary, and fatal in the handler.
    if clean and matched == 0:
        warnings.append(
            f"NO OPERATOR MATCHES: {len(clean)} rows scored and not one found an archived "
            f"board row in the same lead band. Check the poller prefix the scorer reads "
            f"is where the poller actually writes.")
    if states["journey_inconsistent"]:
        warnings.append(f"{states['journey_inconsistent']} rows on journeys whose reported "
                        f"arrivals move backwards along the route")

    return {
        "service_date": day,
        "scored_at": datetime.now(DUBLIN).isoformat(timespec="seconds"),
        "predictions_read": seen,
        "events_after_dedup": len(scored),
        "score_states": dict(states),
        "decline_reasons": dict(declines),
        "trains_unfetched": failed,
        # Coverage, always beside accuracy: "27% better" without "answers ~44% of
        # queries" is the misleading version (CLAUDE.md reporting rules).
        # Two coverage numbers, each with its population named, because they answer
        # different questions and one of them is the one a visitor actually experiences.
        "coverage": {
            # Conditional on the train already running. This is the generator's own
            # population, and it flatters: every train in it has departed by definition.
            "in_service": {
                "population": "trains sampled while in service (TrainStatus R)",
                "answered": answerable,
                "declined": states["declined"],
                "answered_pct": (round(100 * answerable / len(scored), 1)
                                 if scored else None),
            },
            # What a visitor meets. They pick a train off a station board, and a board
            # lists trains that have not departed, which the product cannot answer for at
            # all. This is the headline number: it is the honest one.
            "visitor": visitor_coverage(board, answerable, len(scored)),
        },
        "warnings": warnings,
        "headline": {
            "accuracy": stats(clean, "model_err_rounded_sec"),
            "accuracy_raw": stats(clean, "model_err_sec"),
            "interval_coverage_pct": coverage(clean),
            "head_to_head": head_to_head(clean),
        },
        # Reported, never blended. D23: flag and keep; exclusion is an evaluation-time
        # decision and the page shows it both ways.
        "echo_suspect": {"accuracy": stats(echo, "model_err_rounded_sec"),
                         "interval_coverage_pct": coverage(echo),
                         "head_to_head": head_to_head(echo)},
        "including_echo_suspect": {
            "accuracy": stats(clean + echo, "model_err_rounded_sec"),
            "head_to_head": head_to_head(clean + echo)},
        "by_station_group": by_group,
        "by_lead_band": by_band,
    }


def visitor_coverage(board, answerable, total):
    """Coverage as a visitor experiences it, not conditional on the train running.

    Two factors, both measured live:

        P(in scope)          share of station board entries whose train has departed
        P(answered | scope)  the generator's own answer rate

    The product cannot answer for a train that has not left its origin, and a visitor
    reading a board cannot tell which those are. Reporting only the conditional rate would
    publish "we answer ~89% of queries" for a service that in practice answers about four
    in ten, which is the misleading version CLAUDE.md's reporting rules exist to prevent.

    The second factor assumes the sampled in-service trains are representative of the
    in-service trains on a board. They are drawn uniformly from the fleet, so this is
    reasonable, but it is an assumption and belongs on the page next to the number.
    """
    if not board or not total:
        return None
    known = board["departed"] + board["not_yet_departed"]
    if not known:
        return None
    in_scope = board["departed"] / known
    answered = answerable / total
    return {
        "population": "entries on a station board, as a visitor sees them",
        "board_entries": known,
        "in_scope_pct": round(100 * in_scope, 1),
        "answered_given_in_scope_pct": round(100 * answered, 1),
        "answered_pct": round(100 * in_scope * answered, 1),
        "assumption": "sampled in-service trains are representative of in-service trains "
                      "appearing on boards; the sample is uniform over the fleet",
    }


def coverage(rows):
    """Share of actuals falling inside the 80% interval. The interval's own honesty check."""
    v = [r["interval_hit"] for r in rows if r.get("interval_hit") is not None]
    return round(100 * sum(v) / len(v), 1) if v else None


# ----------------------------------------------------------------- driving

def is_complete(day: str) -> bool:
    """Plainly: is this service day over AND has the railway had time to report on it?

    A date is scored once -- `unscored_dates` skips anything that already has a summary --
    so a premature run writes a page full of `no_actual_arrival` and never revisits it.
    The freeze is silent and the frozen number looks plausible, which is the bad
    combination.

    The first version tested only `day < today`, which makes a date complete at 00:00:01,
    when a train that left at 23:50 has not arrived yet. That was too weak for the one job
    this function has. Six hours past midnight also clears Irish quiet hours (00:30-05:30),
    so the network is idle and yesterday's arrivals have all been filed.
    """
    end_of_day = datetime.combine(date.fromisoformat(day) + timedelta(days=1),
                                  datetime.min.time(), DUBLIN)
    return datetime.now(DUBLIN) >= end_of_day + SETTLE


def score_day(client, session, pacer, bucket, day, pred_prefix, poller_prefix,
              time_left=None, force=False):
    if not is_complete(day) and not force:
        raise ValueError(f"{day} is not finished. Scoring it now would record arrivals "
                         f"that have not happened yet, and the date is only scored once. "
                         f"Pass --force to override.")
    preds, seen = load_predictions(client, bucket, pred_prefix, day)
    if not preds:
        return None, []
    nxt = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
    operator, board = load_operator(client, bucket, poller_prefix, [day, nxt])
    codes = {r["train_code"] for r in preds if r.get("train_code")}
    journeys, failed = fetch_journeys(session, pacer, codes, day, time_left)
    scored = score_rows(preds, journeys, operator, set(failed))
    return summarise(scored, day, seen, failed, board), scored


def write_scores(client, bucket, prefix, day, summary, scored):
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        gz.write("".join(json.dumps(r, sort_keys=True) + "\n"
                         for r in scored).encode("utf-8"))
    client.put_object(Bucket=bucket, Key=f"{prefix}/date={day}/rows.jsonl.gz",
                      Body=buf.getvalue())
    client.put_object(Bucket=bucket, Key=f"{prefix}/date={day}/summary.json",
                      Body=json.dumps(summary, indent=2, sort_keys=True).encode("utf-8"))
    return f"{prefix}/date={day}/summary.json"


def unscored_dates(client, bucket, pred_prefix, scores_prefix, back):
    """Which recent service dates have predictions but no summary.

    The reliability principle: a scheduled job asks what it is missing rather than
    assuming last night's run succeeded. Movements are refetchable by date, so a night
    the scorer did not run is recoverable simply by noticing.
    """
    def days(prefix):
        page = client.list_objects_v2(Bucket=bucket, Prefix=f"{prefix}/date=",
                                      Delimiter="/")
        return {p["Prefix"].rstrip("/").split("date=")[-1]
                for p in page.get("CommonPrefixes", [])}

    today = datetime.now(DUBLIN).date()
    window = {(today - timedelta(days=i)).isoformat() for i in range(1, back + 1)}
    return sorted((days(pred_prefix) & window) - days(scores_prefix))


def report(summary):
    s, W = summary, 70
    print("=" * W)
    print(f"{s['service_date']}  —  {s['predictions_read']:,} rows read, "
          f"{s['events_after_dedup']:,} events after per-band dedup")
    print("=" * W)
    print("states:   " + ", ".join(f"{k} {v:,}" for k, v in
                                   sorted(s["score_states"].items(), key=lambda x: -x[1])))
    if s["decline_reasons"]:
        print("declines: " + ", ".join(f"{k} {v:,}" for k, v in
                                       s["decline_reasons"].items()))
    h = s["headline"]
    if h["accuracy"]:
        a = h["accuracy"]
        print(f"\nmodel (minute-rounded): MAE {a['mae_sec']}s, median {a['medae_sec']}s, "
              f"bias {a['bias_sec']:+}s, n={a['n']:,}")
        print(f"80% interval coverage:  {h['interval_coverage_pct']}%")
    else:
        print("\nnothing scored cleanly — see states above")
    hh = h["head_to_head"]
    if hh:
        print(f"\nhead-to-head on {hh['matched_events']:,} matched events")
        print(f"  model    MAE {hh['model']['mae_sec']}s")
        print(f"  operator MAE {hh['operator']['mae_sec']}s")
        print(f"  -> {hh['improvement_pct']}%   "
              f"W/T/L {hh['model_wins']}/{hh['ties']}/{hh['operator_wins']}")
    else:
        print("\nno matched events: no archived board row shared a lead band with a "
              "prediction. Expected while few predictions land on the 30 polled stations.")
    for w in s.get("warnings", []):
        print("\n  !! " + w)
    if s["trains_unfetched"]:
        print(f"\n{len(s['trains_unfetched'])} trains could not be refetched; "
              f"rerun to pick them up")


def lambda_handler(event, context):
    import boto3
    client = boto3.client("s3")
    bucket = os.environ["POLL_BUCKET"]
    pred = os.environ.get("PREDICTIONS_PREFIX", "predictions")
    poller = os.environ.get("POLLER_PREFIX", "parallel/lambda")
    scores = os.environ.get("SCORES_PREFIX", "scores")

    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    pacer = Pacer(float(os.environ.get("POLL_RATE", "2.0")))

    fatal = []
    days = (event or {}).get("dates") or unscored_dates(
        client, bucket, pred, scores, int(os.environ.get("SCORE_BACKFILL_DAYS", "7")))
    done = []
    for day in days:
        left = context.get_remaining_time_in_millis if context else None
        if left is not None and left() < TIME_BUDGET_FLOOR_MS * 2:
            break
        summary, scored = score_day(client, session, pacer, bucket, day, pred, poller,
                                    time_left=left, force=bool((event or {}).get("force")))
        if summary is None:
            continue
        write_scores(client, bucket, scores, day, summary, scored)
        report(summary)
        done.append({"date": day, "events": summary["events_after_dedup"],
                     "states": summary["score_states"],
                     "warnings": summary["warnings"]})
        # Raised AFTER the write, so the day's scores are preserved and can be inspected,
        # but the invocation still fails and trips the Errors alarm. A quiet zero here
        # would freeze the head-to-head at "no data" indefinitely and look deliberate.
        # Gated on a real sample so a genuinely thin day does not cry wolf.
        if (summary["score_states"].get("scored", 0) >= OPERATOR_ALARM_FLOOR
                and not summary["headline"]["head_to_head"]):
            fatal.append(day)
    if fatal:
        raise RuntimeError(
            f"no operator matches on {', '.join(fatal)} despite scoring at least "
            f"{OPERATOR_ALARM_FLOOR} rows. Scores were written; the comparison is missing. "
            f"Most likely POLLER_PREFIX ({poller}) is not where the poller now writes.")
    return {"status": "ok", "scored_dates": done, "candidates": days}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", help="service date YYYY-MM-DD, default yesterday")
    ap.add_argument("--backfill", type=int, metavar="N",
                    help="score every unscored date in the last N days")
    ap.add_argument("--bucket", default=os.environ.get("POLL_BUCKET",
                                                       "rail-delay-poller-kg"))
    ap.add_argument("--predictions-prefix", default="predictions")
    ap.add_argument("--poller-prefix", default="parallel/lambda")
    ap.add_argument("--scores-prefix", default="scores")
    ap.add_argument("--rate", type=float, default=2.0)
    ap.add_argument("--dry-run", action="store_true",
                    help="report but write nothing back to S3")
    ap.add_argument("--force", action="store_true",
                    help="score a day that has not finished; see is_complete()")
    args = ap.parse_args()

    import boto3
    client = boto3.client("s3")
    if args.backfill:
        days = unscored_dates(client, args.bucket, args.predictions_prefix,
                              args.scores_prefix, args.backfill)
        if not days:
            print("nothing unscored in the window")
            return 0
    else:
        days = [args.date or (datetime.now(DUBLIN).date()
                              - timedelta(days=1)).isoformat()]

    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    pacer = Pacer(args.rate)
    started = time.monotonic()

    for day in days:
        summary, scored = score_day(client, session, pacer, args.bucket, day,
                                    args.predictions_prefix, args.poller_prefix,
                                    force=args.force)
        if summary is None:
            print(f"{day}: no predictions logged")
            continue
        report(summary)
        if args.dry_run:
            print("\n(dry run — nothing written)")
        else:
            key = write_scores(client, args.bucket, args.scores_prefix, day,
                               summary, scored)
            print(f"\nwrote s3://{args.bucket}/{key}")
    print(f"\n{time.monotonic() - started:.1f}s")
    return 0


def _self_check():
    """The join arithmetic, against synthetic journeys.

    Real head-to-head numbers cannot exist until a day after the first predictions, so
    the parts that would fail silently — band matching, the midnight unwrap, minute
    rounding, state assignment — are checked here rather than trusted until the railway
    provides a counterexample. Everything below is in seconds from the service midnight.
    """
    def stop(loc, order, sched, arr, auto="1"):
        return {"loc": loc, "order": order, "sched": sched, "arr": arr, "auto": auto,
                "delay": None if (arr is None or sched is None) else arr - sched}

    journeys = {"A1": [stop("AAAA", 1, 36000, 36060), stop("BBBB", 2, 39600, 39780),
                       stop("CCCC", 3, 43200, None),
                       stop("DDDD", 4, 46800, 46860, auto="0")]}

    def pred(stn, q50, band, lead, q10=-60, q90=600, made="2026-08-27T10:00:00+01:00",
             **kw):
        return {"outcome": "predicted", "train_code": "A1", "station_code": stn,
                "train_date": "27 Aug 2026", "predicted_at": made, "lead_sec": lead,
                "lead_band": band, "pred_q10_sec": q10, "pred_q50_sec": q50,
                "pred_q90_sec": q90, "station_group": "dublin_hubs", **kw}

    # BBBB is 180s late. A 200s median is 20s of error; minute-rounded that is 180 -> 0.
    ops = {("2026-08-27", "A1", "BBBB"): [
        (36000, 39600, "dublin_hubs"),    # 60-min lead, wrong band, must not match
        (38000, 39660, "dublin_hubs"),    # 26-min lead, right band, earlier
        (38400, 39720, "dublin_hubs"),    # 20-min lead, right band, LAST -> wins
        (39900, 39780, "dublin_hubs"),    # after arrival, must not match
    ]}
    rows = score_rows([
        pred("BBBB", 200, "15-30 min", 1200),
        pred("CCCC", 120, "60+ min", 4000),                       # never reported
        pred("DDDD", 60, "60+ min", 7000),                        # AutoArrival != 1
        pred("ZZZZ", 60, "0-5 min", 100),                         # not on route
        {"outcome": "declined", "reason": "no_upstream_report", "train_code": "A1",
         "station_code": "AAAA", "train_date": "27 Aug 2026", "lead_band": "0-5 min"},
        pred("BBBB", 60, "5-15 min", 600, train_code="NOPE"),     # unfetched train
    ], journeys, ops, {"NOPE"})

    got = {r["station_code"]: r for r in rows}
    assert got["CCCC"]["score_state"] == "no_actual_arrival"
    assert got["DDDD"]["score_state"] == "echo_suspect", got["DDDD"]["score_state"]
    assert got["ZZZZ"]["score_state"] == "not_on_route"
    assert got["AAAA"]["score_state"] == "declined"
    assert any(r["score_state"] == "train_not_found" for r in rows)

    b = next(r for r in rows if r["station_code"] == "BBBB"
             and r["score_state"] == "scored")
    assert b["actual_delay_sec"] == 180
    assert b["model_err_sec"] == 20, b["model_err_sec"]
    assert b["model_err_rounded_sec"] == 0, b["model_err_rounded_sec"]   # trap 3
    assert b["interval_hit"] is True
    assert b["operator_polled_at_sec"] == 38400, "did not take the last poll in band"
    assert b["operator_err_sec"] == 39720 - 39780 == -60

    # an ETA just after midnight against a just-before-midnight arrival is late, not
    # nearly a day early
    late = {"A1": [stop("AAAA", 1, 86000, 86100), stop("EEEE", 2, 86300, 86340)]}
    lops = {("2026-08-27", "A1", "EEEE"): [(85000, 120, "dublin_hubs")]}
    r = score_rows([pred("EEEE", 40, "15-30 min", 1300)], late, lops, set())[0]
    assert r["operator_err_sec"] == (120 + 86400) - 86340 == 180, r["operator_err_sec"]

    # dedup keeps the latest statement in a band, not the first
    class Stub:
        def list_objects_v2(self, **kw):
            return {"Contents": [{"Key": "p/date=2026-08-27/a.jsonl"}]}

        def get_object(self, Bucket, Key):
            rows = [pred("BBBB", 111, "15-30 min", 1200,
                         made="2026-08-27T10:00:00+01:00"),
                    pred("BBBB", 222, "15-30 min", 1100,
                         made="2026-08-27T10:05:00+01:00")]
            body = "".join(json.dumps(r) + "\n" for r in rows).encode()
            return {"Body": io.BytesIO(body)}

    kept, seen = load_predictions(Stub(), "b", "p", "2026-08-27")
    assert seen == 2 and len(kept) == 1, (seen, len(kept))
    assert kept[0]["pred_q50_sec"] == 222, "kept the earlier prediction"

    # a row logged before lead_sec existed still gets a band
    old = {"scheduled_arrival": "10:20:00", "predicted_at": "2026-08-27T10:00:00+01:00",
           "train_date": "27 Aug 2026"}
    assert reconstruct_lead(old) == 1200, reconstruct_lead(old)
    midnight = {"scheduled_arrival": "00:20:00",
                "predicted_at": "2026-08-27T23:50:00+01:00", "train_date": "27 Aug 2026"}
    assert reconstruct_lead(midnight) == 1800, reconstruct_lead(midnight)

    # Deliberately not asserting on yesterday: whether yesterday has settled depends on
    # the clock, so an assertion about it would pass all afternoon and fail at 03:00.
    today = datetime.now(DUBLIN).date()
    assert not is_complete(today.isoformat()), "today is never finished"
    assert not is_complete((today + timedelta(days=1)).isoformat()), "tomorrow even less so"
    assert is_complete((today - timedelta(days=2)).isoformat()), "two days ago has settled"

    # a journey whose reported arrivals go backwards is refused whole, not guessed at
    broken = {"A1": [stop("AAAA", 1, 36000, 36060), stop("BBBB", 2, 39600, 36000)]}
    r = score_rows([pred("BBBB", 200, "15-30 min", 1200)], broken, {}, set())[0]
    assert r["score_state"] == "journey_inconsistent", r["score_state"]

    board = Counter({"departed": 431, "not_yet_departed": 569})
    s = summarise(rows, "2026-08-27", 6, [], board)
    v = s["coverage"]["visitor"]
    assert v["in_scope_pct"] == 43.1, v
    # the visitor number must be the conditional one scaled down, never the raw rate
    assert v["answered_pct"] < s["coverage"]["in_service"]["answered_pct"]
    assert round(v["in_scope_pct"] * v["answered_given_in_scope_pct"] / 100, 1)         == v["answered_pct"]

    # zero operator matches on a scored day must be shouted, not returned as a quiet zero
    quiet = summarise([r for r in rows if r["station_code"] == "DDDD"], "2026-08-27", 1,
                      [], board)
    assert not any("NO OPERATOR MATCHES" in w for w in quiet["warnings"]),         "echo_suspect-only day has no clean rows, so there is nothing to warn about"
    only = [r for r in rows if r.get("score_state") == "scored"]
    assert only, "expected a scored row in the fixture"
    for r in only:
        r.pop("operator_err_sec", None)
    assert any("NO OPERATOR MATCHES" in w for w in
               summarise(only, "2026-08-27", 1, [], board)["warnings"])

    assert s["score_states"]["declined"] == 1
    assert s["headline"]["head_to_head"]["matched_events"] == 1
    assert s["headline"]["accuracy"]["n"] == 1
    assert s["coverage"]["in_service"]["declined"] == 1
    print("score.py self-check passed")


if __name__ == "__main__":
    if "--self-check" in sys.argv:
        _self_check()
    else:
        raise SystemExit(main())
