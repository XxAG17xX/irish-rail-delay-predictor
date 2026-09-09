"""
api.py — the prediction service.

Runs two ways from one file: `uvicorn api:app` locally, and as a Lambda handler through
Mangum. That was the deciding reason for FastAPI over a bare handler — the whole service
is testable without deploying, the same way the S3 client is lazy so lambda_poll imports
on a laptop and the time budget is a callable so it can be stubbed.

Model version
-------------
Pinned, never LATEST. The serving version arrives as an environment variable set from a
CloudFormation parameter, so promoting a model is a stack deploy: a changeset showing the
old and new version, timestamped, in CloudTrail. If the API followed LATEST then saving an
artifact would silently change what serves, and the accuracy page could not attribute a
shift to anything.

That splits a question D33 originally answered with one pointer: LATEST now means "what
did I last train", the parameter means "what is serving".

The artifact is baked into the deployment package rather than fetched from S3. With a
pinned version there is nothing to fetch that a redeploy would not already carry, so this
removes the cold-start download, the cache-invalidation logic, the GetObject permission
and the failure mode where S3 is unreachable and the API cannot start.

Baking plus pinning does create one hazard: the version now exists twice, in the parameter
and in the baked manifest. `load_model` refuses to start unless they match, and the build
script takes its version from the parameter rather than from LATEST so a mismatch cannot
originate there either.

Declining
---------
About 56% of queries cannot be answered: the features derive from upstream reported
delays, so a train that has not reported anywhere yet has nothing to predict from. That is
the majority case, not an edge case, and it gets a first-class response shape with a
machine-readable `reason` rather than an error or a guess.

Declines are logged alongside predictions. Coverage needs a denominator, and CLAUDE.md
requires accuracy and coverage published together.
"""

import json
import os
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

import lightgbm as lgb
import numpy as np
import requests
from fastapi import FastAPI, HTTPException, Query, Request

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def _staged(packaged: str, checkout: str) -> Path:
    """The Lambda package stages these beside the modules; a checkout leaves them where
    they live. Same trap lambda_poll.py documents: a `parent.parent` repo root resolves
    to /var under Lambda and every lookup fails on the first deploy."""
    p = HERE / packaged
    return p if p.exists() else HERE / checkout

from backfill import Pacer  # noqa: E402
from features import CATEGORICAL, FEATURES, featurise  # noqa: E402
from feedtime import (MAX_LEAD_SEC, MIN_VANTAGE_DELAY_SEC, board_scope,  # noqa: E402
                      delay_seconds, feed_train_date, hms, journey_consistent,
                      lead_band, unwrap)
from poll_live import (DUBLIN, USER_AGENT, Failure, extract_station_records,  # noqa: E402
                       fetch, in_dublin, load_station_config)
from prediction_log import LogWriteFailed, PredictionLog  # noqa: E402
from ratelimit import Limiter, client_key  # noqa: E402

NS = "{http://api.irishrail.ie/realtime/}"
MODEL_DIR = Path(os.environ.get("MODEL_DIR", HERE / "model"))
CONFIG = Path(os.environ.get("POLL_CONFIG")
              or _staged("config/poll_stations.toml", "../config/poll_stations.toml"))
STATIONS = Path(os.environ.get("POLL_STATIONS")
                or _staged("stations.json", "../data/live/stations.json"))
SERVING_VERSION = os.environ.get("SERVING_MODEL_VERSION", "")
QUANTILES = (0.1, 0.5, 0.9)
DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# Lines Irish Rail documents as weakly covered. Used only to caveat a response, never to
# filter or refuse one: label quality follows AutoArrival, not line identity (D20-D23).
WEAK = {"CORK", "MLLOW", "TRLEE", "FFORE", "COBH", "MDLTN", "LMRCK", "ENNIS",
        "ATLNE", "WFORD", "BALNA", "WPORT"}


def load_model(model_dir=MODEL_DIR, expect_version=SERVING_VERSION):
    """Load the baked artifact, refusing anything that is not the pinned version.

    Fail closed rather than falling back to whatever is on disk. A silent fallback is
    exactly the class of failure D31's bundling exists to prevent: the model would load,
    predict, and be wrong with nothing raising.
    """
    manifest = json.loads((model_dir / "manifest.json").read_text(encoding="utf-8"))
    if expect_version and manifest["version"] != expect_version:
        raise RuntimeError(
            f"model version mismatch: SERVING_MODEL_VERSION is {expect_version!r} but the "
            f"baked artifact is {manifest['version']!r}. The package and the stack "
            f"parameter have drifted; rebuild with the pinned version.")
    if manifest["features"] != FEATURES:
        raise RuntimeError(f"artifact feature set differs from this code:\n"
                           f"  artifact: {manifest['features']}\n  code: {FEATURES}")
    boosters = {q: lgb.Booster(model_file=str(model_dir / f"q{int(q * 100):02d}.txt"))
                for q in manifest["quantiles"]}
    return boosters, manifest["vocabs"], manifest


def journey(session, pacer, train_code, when):
    """Today's stops for one train, ordered, with clock times unwrapped across midnight."""
    body = fetch(session, "getTrainMovementsXML",
                 {"TrainId": train_code, "TrainDate": feed_train_date(when)},
                 pacer, "objtrainmovements")
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
        stops.append({
            "order": order,
            "loc": text(rec, "LocationCode").upper(),
            "name": text(rec, "LocationFullName"),
            "sched_raw": sched, "arr_raw": arr,
            "auto": text(rec, "AutoArrival"),
            # Anchored to this stop's own schedule, matching parse_raw and therefore the
            # delays the model was trained on. Subtracting two independently unwrapped
            # series instead returns a spurious extra day on journeys whose reported
            # arrivals go backwards. See feedtime.delay_seconds.
            "delay": delay_seconds(arr, sched),
            "origin": text(rec, "TrainOrigin"),
            "destination": text(rec, "TrainDestination"),
            "sched_text": text(rec, "ScheduledArrival"),
        })
    stops.sort(key=lambda s: s["order"])
    for field, dest in (("sched_raw", "sched"), ("arr_raw", "arr")):
        for s, v in zip(stops, unwrap([x[field] for x in stops])):
            s[dest] = v
    return stops


def hhmmss(seconds):
    seconds %= 86400
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


app = FastAPI(title="rail-delay", version="0.1",
              description="Irish Rail delay prediction with intervals, not point estimates.")

# Rate limits, in requests the caller may make before waiting. The numbers are set from
# what each endpoint costs upstream rather than from a round figure.
#
#   /board   fans out to as many as `limit` movement requests plus the board itself, so it
#            is the expensive one. Three in a burst then one every twenty seconds is far
#            more than a person reading a board needs, and stops a loop dead.
#   /predict is one movement request. Ten in a burst, one every three seconds after that.
#
# The shared budget is the one Irish Rail actually feels: whatever the mix of callers, this
# container will not start more than one endpoint request a second against the feed, and
# the Pacer already holds each of those to 2/s inside the request.
BOARD_LIMITER = Limiter(rate=1 / 20, burst=3, shared_rate=1.0, shared_burst=20)
PREDICT_LIMITER = Limiter(rate=1 / 3, burst=10, shared_rate=2.0, shared_burst=40)


def enforce(limiter, request):
    """Refuse with a 429 and a Retry-After when the caller is over its budget.

    A refusal says how long to wait. A client told to wait does not poll; a client refused
    with no number polls immediately and makes the thing it is being protected from worse.
    """
    key = client_key(request.client.host if request.client else "",
                     request.headers.get("x-forwarded-for"))
    wait = limiter.check(key)
    if wait:
        seconds = max(1, int(wait + 0.999))
        raise HTTPException(
            status_code=429,
            detail=f"Too many requests. Try again in {seconds} seconds.",
            headers={"Retry-After": str(seconds), "Cache-Control": "no-store"},
        )

_state = {}


def state():
    """Built once per container and reused while warm."""
    if not _state:
        boosters, vocabs, manifest = load_model()
        session = requests.Session()
        session.headers["User-Agent"] = USER_AGENT

        bucket = os.environ.get("POLL_BUCKET", "")
        # Unlogged predictions are only tolerable on a laptop. In Lambda a missing bucket
        # would silently serve predictions the accuracy page can never account for, which
        # is the fail-open behaviour D39 rejected — so refuse to start instead.
        if not bucket and os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
            raise RuntimeError("POLL_BUCKET is unset, so predictions could not be logged. "
                               "Refusing to serve.")
        log = PredictionLog(bucket, os.environ.get("PREDICTIONS_PREFIX", "predictions"),
                            _s3()) if bucket else None

        # Raised rather than defaulted to empty: a missing config is a packaging error
        # that would otherwise surface as an accuracy page silently missing its line
        # split, weeks later, in data that cannot be rebuilt.
        known = {s["code"].upper(): s["name"]
                 for s in json.loads(STATIONS.read_text(encoding="utf-8"))}
        polled = load_station_config(CONFIG, known)

        # The same platform is listed under several codes -- Adamstown is ADMTN on the
        # mainline list and ADAMF on the suburban one -- and BOTH appear in movement
        # records, because which one a service reports under depends on the service. So
        # neither is "the" code: a request for one has to be able to fall back to the
        # others, or half the trains at those stops decline as not on the route.
        aliases = alias_map(known)

        _state.update(boosters=boosters, vocabs=vocabs, manifest=manifest,
                      session=session, pacer=Pacer(2.0), log=log, stations=known,
                      aliases=aliases,
                      polled=polled, groups={s["code"]: s["group"] for s in polled})
    return _state


def _s3():
    import boto3
    return boto3.client("s3")


@app.get("/health")
def health():
    st = state()
    return {"status": "ok", "model_version": st["manifest"]["version"],
            "trained": st["manifest"]["created_at"],
            "features": len(st["manifest"]["features"]),
            "metrics": st["manifest"]["metrics"]}


def predict_row(st, train, station, today=None, now_s=None, stops=None, extra=None):
    """One prediction, or one reasoned decline. Returns the row; logging is the caller's.

    Split out of the HTTP handler so the scheduled generator calls this identical function
    rather than a copy of it. A second implementation is the D35 failure: two things that
    must agree, maintained apart, diverge and nothing notices.

    `stops` is accepted pre-fetched because the generator predicts several stations per
    train and must not refetch the journey once per station. `extra` carries provenance
    the caller knows and this function cannot — which sampling scheme selected this row.
    """
    train, station = train.strip().upper(), station.strip().upper()
    if today is None:
        today = datetime.now(DUBLIN).date()
    if now_s is None:
        now_s = (datetime.now(DUBLIN) - datetime.combine(
            today, datetime.min.time(), DUBLIN)).total_seconds()

    base = {"outcome": "declined", "train": train, "station": station,
            "train_date": feed_train_date(today), "train_code": train,
            "station_code": station, "predicted": None,
            "model_version": st["manifest"]["version"],
            # Groupable route handles. `confidence` says the same thing in prose, which
            # cannot be grouped by; the accuracy page must report the documented
            # weak-coverage lines separately rather than blended (CLAUDE.md).
            "station_group": st["groups"].get(station, ""),
            "weak_coverage": station in WEAK,
            "source": "api",
            "predicted_at": in_dublin(datetime.now()).isoformat(timespec="seconds")}
    if extra:
        base.update(extra)

    if stops is None:
        try:
            stops = journey(st["session"], st["pacer"], train, today)
        except Failure as f:
            return {**base, "reason": "upstream_unavailable",
                    "explanation": f"Could not reach Irish Rail for {train} ({f.kind})."}

    if not stops:
        return {**base, "reason": "not_in_service",
                "explanation": f"{train} is not running today."}

    # A journey whose reported arrivals go backwards along the route contains at least
    # one time that belongs to a different train (D56). Predicting from it would be
    # predicting from someone else's journey, so it is declined with a reason, the same
    # way a train with nothing to reason from is. Effective from 2026-09-03.
    if not journey_consistent(stops):
        return {**base, "reason": "journey_inconsistent",
                "explanation": f"{train}'s reported arrivals are out of order along its "
                               f"route, so at least one of them is not this train's."}

    ti = next((i for i, s in enumerate(stops) if s["loc"] == station), None)
    if ti is None:
        return {**base, "reason": "station_not_on_route",
                "explanation": f"{train} does not call at {station} today."}

    target = stops[ti]
    base["scheduled_arrival"] = target["sched_text"] or None
    base["station_name"] = target["name"]
    # Recorded rather than left for the scorer to reconstruct. `sched` here is unwrapped
    # across midnight; `scheduled_arrival` is the raw wall clock, so a 23:50 prediction
    # about a 00:20 arrival reconstructs as a lead of minus 23 hours. Compute it once
    # where the information is complete. The band is the unit the offline comparison
    # deduplicates by (D46 trap 4), so it has to mean the same thing on both sides.
    if target["sched"] is not None:
        base["lead_sec"] = int(target["sched"] - now_s)
        base["lead_band"] = lead_band(base["lead_sec"])
        # Out of envelope. No journey on the network is this long, so a lead this far
        # ahead means the feed's picture of this train is wrong (D56). Effective 2026-09-03.
        if base["lead_sec"] > MAX_LEAD_SEC:
            return {**base, "reason": "lead_out_of_range",
                    "explanation": f"{station} is {base['lead_sec'] // 3600} hours ahead, "
                                   f"longer than any journey on the network."}

    if target["arr"] is not None:
        return {**base, "reason": "already_arrived",
                "explanation": f"{train} has already arrived at {station}."}

    # Only stops that have actually reported by now may inform the prediction. The same
    # cutoff the offline comparison enforces, for the same reason.
    vi = None
    for i in range(ti):
        s = stops[i]
        if s["arr"] is not None and s["arr"] <= now_s and s["delay"] is not None:
            vi = i
    if vi is None:
        return {**base, "reason": "no_upstream_report",
                "explanation": f"{train} has not reported at any stop yet, so "
                               f"there is nothing to predict from."}

    # A single wrong-train arrival at the vantage passes journey_consistent, because one
    # point cannot be out of order. Hours early is impossible on its own (D58).
    if stops[vi]["delay"] < MIN_VANTAGE_DELAY_SEC:
        return {**base, "reason": "vantage_delay_out_of_range",
                "vantage_location": stops[vi]["loc"], "vantage_delay_sec": stops[vi]["delay"],
                "explanation": f"{train}'s last reported stop shows it "
                               f"{-stops[vi]['delay'] // 60} minutes early, which no "
                               f"scheduled service is; that arrival belongs to another train."}

    dow = DAY_NAMES[today.weekday()]
    x = featurise(stops, vi, ti, dow, st["vocabs"]).reshape(1, -1)
    q = np.sort(np.vstack([st["boosters"][a].predict(x) for a in QUANTILES]), axis=0)
    q10, q50, q90 = (int(round(v)) for v in q[:, 0])

    sched = target["sched"]
    weak = station in WEAK or stops[vi]["loc"] in WEAK
    return {**base, "outcome": "predicted",
            "predicted": hhmmss(sched + q50),
            "interval_80pct": [hhmmss(sched + q10), hhmmss(sched + q90)],
            "current_delay_min": round(stops[vi]["delay"] / 60, 1),
            "vantage_location": stops[vi]["loc"],
            "vantage_delay_sec": stops[vi]["delay"],
            # Offline evaluation requires AutoArrival=1 at the vantage as well as the
            # target; live scoring could not check the vantage because it was not logged.
            "vantage_auto": stops[vi]["auto"],
            "horizon_route_stops": target["order"] - stops[vi]["order"],
            "horizon_sched_sec": sched - stops[vi]["sched"],
            "pred_q10_sec": q10, "pred_q50_sec": q50, "pred_q90_sec": q90,
            "confidence": ("weak coverage on this line, treat with caution" if weak
                           else "good coverage on this line")}


@app.get("/predict")
def predict(request: Request,
            train: str = Query(..., description="Train code, e.g. A220"),
            station: str = Query(..., description="Location code, e.g. THRLS")):
    enforce(PREDICT_LIMITER, request)
    return _respond(predict_row(state(), train, station))


BOARD_MINS = 90


def board_clock(rec, *fields):
    """First of `fields` carrying a real time, or "".

    The board writes 00:00, not empty, for whichever half of the arrival/departure pair
    does not apply -- arrival at an origin, departure at a destination -- so a plain `or`
    chain returns midnight for every terminus on the board.
    """
    for f in fields:
        v = (rec.get(f) or "").strip()
        if v and v != "00:00":
            return v
    return ""


def alias_map(known):
    """{code: other codes for the same station name}. See the note in state()."""
    same_name = defaultdict(list)
    for code, name in known.items():
        same_name[name].append(code)
    return {c: [o for o in same_name[n] if o != c] for c, n in known.items()}


@app.get("/board")
def board(request: Request,
          station: str = Query(..., description="Station code, e.g. THRLS"),
          limit: int = Query(6, ge=1, le=10, description="Trains to predict for")):
    """What is due at one station, each entry with a prediction or a reason there is none.

    Every journey costs one request at 2/second, so `limit` is the real cost control:
    the board itself is one request and each predictable train is one more. Trains that
    have not left their origin are listed but never fetched — the product cannot answer
    for them (board_scope), and spending a request to be told so would halve how many
    real trains fit inside the 30-second timeout.

    Rate limited before anything else happens: this is the endpoint that costs Irish Rail
    something, so a refusal must be cheap.
    """
    enforce(BOARD_LIMITER, request)
    st = state()
    code = station.strip().upper()
    if code not in st["stations"]:
        raise HTTPException(status_code=404, detail=f"unknown station code {code!r}")

    try:
        body = fetch(st["session"], "getStationDataByCodeXML_WithNumMins",
                     {"StationCode": code, "NumMins": BOARD_MINS},
                     st["pacer"], "objstationdata")
    except Failure as f:
        raise HTTPException(status_code=502,
                            detail=f"Irish Rail board unavailable ({f.kind})")

    polled_at = in_dublin(datetime.now()).isoformat(timespec="seconds")
    recs = extract_station_records(body, code, st["groups"].get(code, ""), polled_at, "")

    def due(rec):
        try:
            return int(rec.get("Duein", "999"))
        except ValueError:
            return 999

    entries, to_log, spent = [], [], 0
    for rec in sorted(recs, key=due):
        scope = board_scope(rec)
        entry = {
            "train": (rec.get("Traincode") or "").strip().upper(),
            "origin": rec.get("Origin", ""),
            "destination": rec.get("Destination", ""),
            "due_in_min": due(rec) if due(rec) < 999 else None,
            "scheduled": board_clock(rec, "Scharrival", "Schdepart"),
            "operator_eta": board_clock(rec, "Exparrival", "Expdepart"),
            "operator_late_min": rec.get("Late", ""),
            "scope": scope,
            "last_location": rec.get("Lastlocation", ""),
        }
        if scope != "departed":
            # Listed, not predicted, and the page says which. Silently dropping these is
            # what makes a coverage figure look better than the thing a visitor meets.
            entry["prediction"] = None
            entry["reason"] = "not_yet_departed"
            entry["explanation"] = "Has not left its origin yet, so there is nothing to predict from."
        elif spent >= limit:
            entry["prediction"] = None
            entry["reason"] = "not_asked"
            entry["explanation"] = f"Beyond the first {limit} trains this request predicts for."
        else:
            spent += 1
            row = predict_row(st, entry["train"], code, extra={"source": "api_board"})
            # This train may report under one of the station's other codes. Only retried
            # on that one reason, so a train that genuinely does not call here still costs
            # a single request.
            for alt in st["aliases"].get(code, []):
                if row.get("reason") != "station_not_on_route":
                    break
                row = predict_row(st, entry["train"], alt, extra={"source": "api_board"})
            to_log.append(row)
            entry["prediction"] = (None if row.get("outcome") != "predicted" else
                                   {k: v for k, v in row.items() if k not in _PRIVATE})
            if entry["prediction"]:
                # Added to the response, not to the row: the log keeps codes, which is what
                # the scorer joins on. A name is presentation.
                v = entry["prediction"].get("vantage_location")
                entry["prediction"]["vantage_name"] = st["stations"].get(v, v)
            entry["reason"] = row.get("reason")
            entry["explanation"] = row.get("explanation")
        entries.append(entry)

    _log(to_log)
    return {"station": code, "station_name": st["stations"][code],
            "generated_at": polled_at, "model_version": st["manifest"]["version"],
            "board_minutes": BOARD_MINS, "trains": entries}


@app.get("/stations")
def stations():
    """One entry per station name, for the picker. Cached in the page, not per request.

    The feed lists the same physical station under several codes -- Hazelhatch is HZLCH,
    HAZEF and HAZES -- because it appears in the mainline, suburban and DART lists. A
    picker showing "Hazelhatch" three times is unusable, so each name is offered under one
    code, preferring one the poller watches, then one the model was actually trained on.
    The rest are returned as `aliases` rather than hidden.
    """
    st = state()
    polled = {s["code"] for s in st["polled"]}
    vocab = set(st["vocabs"].get("target_location", {}))
    by_name = defaultdict(list)
    for code, name in st["stations"].items():
        by_name[name].append(code)
    out = []
    for name, codes in by_name.items():
        # Prefer a code the poller watches, since those are the ones with an operator
        # comparison behind them. Which of the remaining codes is offered does not matter
        # much: /board falls back through the aliases when a train reports under another.
        codes.sort(key=lambda c: (c not in polled, c not in vocab, c))
        out.append({"code": codes[0], "name": name, "polled": codes[0] in polled,
                    "model_known": codes[0] in vocab, "aliases": codes[1:]})
    return {"stations": sorted(out, key=lambda s: s["name"])}


# Bookkeeping the log needs and a public response should not echo. `source` stays in:
# it says whether an answer came from a visitor or the scheduled generator, which the
# accuracy page's "these are samples, not traffic" claim depends on being checkable.
_PRIVATE = ("train_code", "station_code", "train_date", "outcome")


def _log(rows):
    """A prediction that could not be logged is not served (D39). Raises 503 if it cannot."""
    st = state()
    if st["log"] is None or not rows:
        return
    try:
        st["log"].write([dict(r) for r in rows])
    except LogWriteFailed as e:
        raise HTTPException(status_code=503, detail=f"prediction not logged: {e}")


def _respond(row):
    """Log before returning. A prediction that could not be logged is not served."""
    _log([row])
    return {k: v for k, v in row.items() if k not in _PRIVATE}


handler = None
if os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
    from mangum import Mangum
    handler = Mangum(app)


def _self_check():
    """No network, no model: the two board helpers that are easy to get quietly wrong."""
    assert board_clock({"Exparrival": "00:00", "Expdepart": "17:20"},
                       "Exparrival", "Expdepart") == "17:20", "00:00 must not win"
    assert board_clock({"Exparrival": "17:05", "Expdepart": "17:07"},
                       "Exparrival", "Expdepart") == "17:05", "first real field wins"
    assert board_clock({"Exparrival": " ", "Expdepart": ""},
                       "Exparrival", "Expdepart") == "", "nothing real means empty"

    known = {"ADMTN": "Adamstown", "ADAMF": "Adamstown", "ADAMS": "Adamstown",
             "KDARE": "Kildare"}
    al = alias_map(known)
    assert al["ADMTN"] == ["ADAMF", "ADAMS"] or set(al["ADMTN"]) == {"ADAMF", "ADAMS"}
    assert al["KDARE"] == [], "a station with one code has no aliases"
    assert "ADMTN" not in al["ADMTN"], "a code is not its own alias"
    for code, others in al.items():
        for o in others:
            assert code in al[o], f"aliasing must be symmetric: {code} <-> {o}"

    print("api.py self-check passed")


if __name__ == "__main__":
    _self_check()
