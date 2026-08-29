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
from datetime import date, datetime
from pathlib import Path

import lightgbm as lgb
import numpy as np
import requests
from fastapi import FastAPI, Query

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
from feedtime import (delay_seconds, feed_train_date, hms,  # noqa: E402
                      lead_band, unwrap)
from poll_live import (DUBLIN, USER_AGENT, Failure, fetch, in_dublin,  # noqa: E402
                       load_station_config)
from prediction_log import LogWriteFailed, PredictionLog  # noqa: E402

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

        _state.update(boosters=boosters, vocabs=vocabs, manifest=manifest,
                      session=session, pacer=Pacer(2.0), log=log,
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
            "horizon_route_stops": target["order"] - stops[vi]["order"],
            "horizon_sched_sec": sched - stops[vi]["sched"],
            "pred_q10_sec": q10, "pred_q50_sec": q50, "pred_q90_sec": q90,
            "confidence": ("weak coverage on this line, treat with caution" if weak
                           else "good coverage on this line")}


@app.get("/predict")
def predict(train: str = Query(..., description="Train code, e.g. A220"),
            station: str = Query(..., description="Location code, e.g. THRLS")):
    return _respond(predict_row(state(), train, station))


def _respond(row):
    """Log before returning. A prediction that could not be logged is not served."""
    st = state()
    if st["log"] is not None:
        try:
            st["log"].write([dict(row)])
        except LogWriteFailed as e:
            from fastapi import HTTPException
            raise HTTPException(status_code=503, detail=f"prediction not logged: {e}")
    public = {k: v for k, v in row.items()
              if k not in ("train_code", "station_code", "train_date", "outcome")}
    return public


handler = None
if os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
    from mangum import Mangum
    handler = Mangum(app)
