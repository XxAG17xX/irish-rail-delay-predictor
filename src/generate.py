"""
generate.py — scheduled sampled predictions, so the accuracy page has something to score.

Why this exists
---------------
The API logs what it is asked. On 27 August, after two days live, the prediction log held
exactly one row: the smoke test. A portfolio service gets no organic traffic, so a nightly
scorer would join yesterday's predictions to yesterday's outcomes and find nothing, for
ever. The scoreboard's input is demand, and demand has to be manufactured.

Manufactured demand is not a weakness to hide. Organic traffic would be whatever visitors
happened to type — biased toward Dublin, toward rush hour, toward the handful of trains
someone was actually waiting for. A uniform random sample over the in-service fleet is a
*better* sampling frame than real users would have produced, and the accuracy page says so
rather than implying these were real queries.

How a cycle works
-----------------
1. `getCurrentTrainsXML`, one request, gives the fleet.
2. Uniform random sample of `GEN_SAMPLE` trains from those with TrainStatus R.
3. One `getTrainMovementsXML` per sampled train.
4. From each journey, at most one target stop per lead-time band.
5. Every row into one S3 object.

So the request cost is `1 + sample` per cycle, and the prediction count is several times
that, because one journey answers several horizons. At 40 trains that is ~41 requests per
cycle against Irish Rail — roughly double the poller's 31 — for ~120 predictions.

Sampling, and what is deliberately not filtered
-----------------------------------------------
The sample is uniform random over the fleet, unseeded, redrawn every cycle. Not the first
N off the board: the feed returns trains in an order we do not control, and taking a
prefix would silently pin the scoreboard to whichever routes sort first. Not seeded
either — a fixed seed would draw a similar sample every cycle, which is the same bias in
slower motion. The scheme, the pool size and the sample size go onto every row so the page
can state exactly how its population was drawn.

TrainStatus R is a **scope** filter, not a quality one: the product answers for trains
currently in service, so a train that has not departed is out of scope rather than a hard
case. Within that scope nothing is pre-filtered. In particular targets are NOT screened
for whether a prediction is possible — a train with no upstream report yet produces a
`no_upstream_report` decline, and that decline is the coverage measurement. Screening
them out would delete the denominator and turn "answers ~44% of queries" into "answers
100% of the queries we knew we could answer".

Target stations are chosen uniformly among the journey's remaining stops within each band,
with no preference for the 30 stations the poller watches. Preferring them would raise the
matched-event count for the operator head-to-head, at the cost of drawing the head-to-head
population differently from the accuracy population — two populations to explain instead
of one. At ~17% of stations polled and ~22k predictions a day, the natural overlap already
supplies far more matched events than the offline comparison used.

Scheduling
----------
Deployed with its EventBridge rule DISABLED and enabled by hand after the 30 August
cutover. Doubling request volume during the last three days of the parallel run would
confound the diff that decides the cutover — the same argument that kept precompute out
of the poller (D42) and that added cycle metadata to LocalSink rather than changing the
control mid-experiment.

Configuration by environment variable
-------------------------------------
    POLL_BUCKET         required, inherited from the API stack
    PREDICTIONS_PREFIX  key prefix, default "predictions"
    GEN_SAMPLE          trains per cycle, default 40
    GEN_MAX_PER_TRAIN   target stops per train, default 5 (one per lead band)
    GEN_NO_QUIET        "1" to run through quiet hours
"""

import json
import os
import random
import time
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime

from api import journey, predict_row, state
from feedtime import LEAD_BANDS, MAX_LEAD_SEC, lead_band
from poll_live import DUBLIN, TIME_BUDGET_FLOOR_MS, Failure, fetch, in_quiet_hours

NS = "{http://api.irishrail.ie/realtime/}"
NAMESPACE = "RailDelay"
SCHEME = "uniform_random_in_service"


def running_trains(body: bytes) -> list[str]:
    """Codes with TrainStatus R, sorted for determinism before sampling.

    harvest_codes.extract_codes deliberately keeps every status, because it is answering
    "what codes exist today". This is answering "what is in service right now", which is
    the product's stated scope, so N (not yet running) and T (terminated) are excluded.

    Sorted before the sample is drawn so the randomness comes from the RNG and not from
    the feed's ordering, which we neither control nor understand.
    """
    root = ET.fromstring(body)
    out = set()
    for train in root.findall(NS + "objTrainPositions"):
        code = train.find(NS + "TrainCode")
        status = train.find(NS + "TrainStatus")
        if code is None or not (code.text or "").strip():
            continue
        if status is not None and (status.text or "").strip().upper() == "R":
            out.add(code.text.strip().upper())
    return sorted(out)


def choose_targets(stops, now_s, rng, limit):
    """At most one target stop per lead band, chosen uniformly within the band.

    One per band, not all remaining stops, because ~18 correlated observations of one
    train from one vantage would inflate every count and narrow every interval — the
    fourth fairness trap in D46. The bands are the same ones compare_to_operator.py
    deduplicates by, imported rather than restated, so the live number and the published
    offline number are computed over comparable units.

    Stops already arrived at are skipped: a station board keeps listing a train past its
    arrival, and at that point nothing is being predicted.
    """
    by_band = {}
    for s in stops:
        if s["arr"] is not None or s["sched"] is None:
            continue
        # Beyond the envelope, predict_row would decline anyway; skipping here saves the
        # decline row and keeps the coverage denominator about real questions (D56).
        if s["sched"] - now_s > MAX_LEAD_SEC:
            continue
        band = lead_band(s["sched"] - now_s)
        if band is not None:
            by_band.setdefault(band, []).append(s)
    ordered = [name for _, _, name in LEAD_BANDS if name in by_band]
    return [rng.choice(by_band[b]) for b in ordered[:limit]]


# Three metrics, not the eight this first had. CLAUDE.md records that the deployment
# already uses 8 of the 10 free CloudWatch custom metrics, past which they cost $0.30 each
# per month — a threshold that moves quietly because nothing warns you. Everything else
# worth knowing (fleet size, sample size, per-reason decline counts) goes in the handler's
# return value, which lands in CloudWatch Logs for free and is queryable in Logs Insights.
# Cycle duration is deliberately absent: Lambda publishes `Duration` itself, in the
# AWS/Lambda namespace, and a custom copy would be a paid duplicate of a free metric.
METRICS = (("PredictionsLogged", "Count"),   # is it running at all
           ("Declined", "Count"),            # the coverage numerator, watched over time
           ("TrainsFailed", "Count"))        # Irish Rail health, distinct from the poller's


def emit_metrics(values: dict) -> None:
    """One EMF line, same mechanism as lambda_poll: CloudWatch builds the metrics from
    the log itself, so there is no PutMetricData call and no cloudwatch:* permission."""
    print(json.dumps({
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [{
                "Namespace": NAMESPACE,
                "Dimensions": [["Stage"]],
                "Metrics": [{"Name": k, "Unit": u} for k, u in METRICS if k in values],
            }],
        },
        "Stage": os.environ.get("POLL_STAGE", "live"),
        **values,
    }))


def run_cycle(st, rng, sample_size, max_per_train, time_left=None):
    """One sweep. Returns (rows, stats). Writes nothing — the caller owns the log."""
    stats = Counter()
    session, pacer = st["session"], st["pacer"]
    now = datetime.now(DUBLIN)
    today = now.date()
    now_s = (now - datetime.combine(today, datetime.min.time(), DUBLIN)).total_seconds()

    body = fetch(session, "getCurrentTrainsXML", {}, pacer, "objtrainpositions")
    fleet = running_trains(body)
    stats["fleet"] = len(fleet)
    sample = rng.sample(fleet, min(sample_size, len(fleet)))

    # Provenance on every row. The accuracy page has to be able to say how its population
    # was drawn; a reader who cannot reconstruct the sampling frame cannot check the claim.
    extra = {"source": "generator", "sampling_scheme": SCHEME,
             "sample_pool_size": len(fleet), "sample_size": len(sample),
             "cycle_at": now.isoformat(timespec="seconds")}

    rows = []
    for code in sample:
        # Same discipline as poll_cycle: stop starting requests rather than be killed
        # part-way through, so a cycle is either written whole or not at all.
        if time_left is not None and time_left() < TIME_BUDGET_FLOOR_MS:
            stats["trains_skipped"] += 1
            continue
        try:
            stops = journey(session, pacer, code, today)
        except Failure as f:
            stats["trains_failed"] += 1
            print(f"  ! {code} movements {f.kind}: {f.detail}")
            continue
        stats["trains_sampled"] += 1
        if not stops:
            continue
        for target in choose_targets(stops, now_s, rng, max_per_train):
            row = predict_row(st, code, target["loc"], today=today, now_s=now_s,
                              stops=stops, extra=extra)
            rows.append(row)
            stats[row["outcome"]] += 1
            if row["outcome"] == "declined":
                stats[f"reason_{row['reason']}"] += 1
    return rows, stats


def lambda_handler(event, context):
    if os.environ.get("GEN_NO_QUIET") != "1" and in_quiet_hours(datetime.now()):
        emit_metrics({"PredictionsLogged": 0})
        return {"status": "quiet_hours"}

    started = time.monotonic()
    st = state()
    if st["log"] is None:
        raise RuntimeError("POLL_BUCKET is unset, so nothing generated could be logged.")

    rows, stats = run_cycle(
        st, random.Random(),
        int(os.environ.get("GEN_SAMPLE", "40")),
        int(os.environ.get("GEN_MAX_PER_TRAIN", str(len(LEAD_BANDS)))),
        time_left=context.get_remaining_time_in_millis if context else None)

    # One object for the whole cycle, not one per prediction: at ~22k predictions a day
    # that is 660k PUTs a month against ~190 for the batched form. The D39 corroboration
    # is unaffected, because it is the per-row CloudWatch line — stamped by AWS at
    # ingestion — and prediction_log prints one of those per row either way.
    #
    # Fail-closed granularity does change: a failed write now loses the whole cycle
    # rather than one row. For generated predictions that is the better failure. Nothing
    # is being served to anyone, so the loss is uniform across the cycle rather than a
    # subset selected by whatever was in flight when S3 faltered.
    key = st["log"].write(rows) if rows else None

    duration = time.monotonic() - started
    emit_metrics({"PredictionsLogged": len(rows), "Declined": stats["declined"],
                  "TrainsFailed": stats["trains_failed"]})
    return {"status": "ok", "key": key, "logged": len(rows),
            "predicted": stats["predicted"], "declined": stats["declined"],
            "fleet": stats["fleet"], "trains_sampled": stats["trains_sampled"],
            "reasons": {k[len("reason_"):]: v for k, v in stats.items()
                        if k.startswith("reason_")},
            "duration_sec": round(duration, 2)}


def _self_check():
    """Exercises the two pieces that are this file's own logic and could silently rot:
    status filtering and per-band target choice. The prediction itself is api.py's."""
    xml = ('<?xml version="1.0"?><ArrayOfObjTrainPositions '
           'xmlns="http://api.irishrail.ie/realtime/">'
           + "".join(f"<objTrainPositions><TrainCode>{c} </TrainCode>"
                     f"<TrainStatus>{s}</TrainStatus></objTrainPositions>"
                     for c, s in [("A220", "R"), ("E108", "N"), ("D501", "T"),
                                  ("A220", "R"), ("P642", "R")])
           + "</ArrayOfObjTrainPositions>").encode()
    assert running_trains(xml) == ["A220", "P642"], running_trains(xml)

    now_s = 12 * 3600
    stops = [
        {"loc": "AAAA", "arr": now_s - 600, "sched": now_s - 660},   # already arrived
        {"loc": "BBBB", "arr": None, "sched": now_s + 120},          # 0-5
        {"loc": "CCCC", "arr": None, "sched": now_s + 200},          # 0-5, same band
        {"loc": "DDDD", "arr": None, "sched": now_s + 1200},         # 15-30
        {"loc": "EEEE", "arr": None, "sched": now_s + 5000},         # 60+
        {"loc": "FFFF", "arr": None, "sched": None},                 # no schedule
    ]
    rng = random.Random(0)
    picked = choose_targets(stops, now_s, rng, 5)
    assert len(picked) == 3, [p["loc"] for p in picked]
    assert {p["loc"] for p in picked} <= {"BBBB", "CCCC", "DDDD", "EEEE"}
    assert len({lead_band(p["sched"] - now_s) for p in picked}) == 3, "one per band"
    assert "AAAA" not in {p["loc"] for p in picked}, "arrived stop offered as a target"

    # the limit truncates in band order, nearest first
    assert [p["loc"] for p in choose_targets(stops, now_s, random.Random(1), 1)][0] \
        in ("BBBB", "CCCC")

    # a journey with nothing ahead of it yields nothing rather than raising
    assert choose_targets([stops[0]], now_s, rng, 5) == []

    # a stop seventeen hours ahead is not a target, whatever band it would land in
    far = stops + [{"loc": "GGGG", "arr": None, "sched": now_s + 17 * 3600}]
    assert "GGGG" not in {p["loc"] for p in choose_targets(far, now_s, random.Random(2), 9)}
    print("generate.py self-check passed")


if __name__ == "__main__":
    _self_check()
