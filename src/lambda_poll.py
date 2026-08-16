"""
lambda_poll.py — one poll cycle as an AWS Lambda invocation.

EventBridge fires this every five minutes. It does no polling of its own: it builds an
S3Sink and hands it to `poll_cycle()` in poll_live.py, which is the same function the
local poller calls. That is the whole design. A handler that reimplemented
fetch-and-extract against S3 would make the parallel-run diff (D36) test the
reimplementation instead of the port — the failure D35 records.

What this file actually owns
----------------------------
1. **The time budget.** Lambda's timeout is ~4 minutes, deliberately below the 5-minute
   schedule: with reserved concurrency 1, an invocation still running when the next fires
   blocks it, so the function must finish before its own successor arrives.
   `context.get_remaining_time_in_millis` is passed straight into `poll_cycle`, which
   stops starting station requests once too little is left and closes the cycle as
   partial rather than being killed mid-write.

2. **Quiet hours, in the function.** Not in the EventBridge schedule, because cron is
   UTC-only: "00:30–05:30 Irish time" would need two expressions and someone swapping
   them on the last Sundays of March and October. `in_quiet_hours()` already handles it
   with zoneinfo and is unit-tested across IST, GMT and UTC hosts.

3. **Metrics, via Embedded Metric Format.** A structured JSON line on stdout that
   CloudWatch turns into metrics — no PutMetricData call, no extra latency, and no
   `cloudwatch:*` IAM permission. `ThrottleEvents` is the one that matters: "Irish Rail
   has never throttled us" stays a claim being checked rather than an assumption.

Deliberately absent
-------------------
`hostlock` — it is a file on local disk and cannot see across machines. Reserved
concurrency 1 is what prevents this function overlapping itself. During the parallel run
both pollers are live on purpose; see D36, which gives that exception an end date.

Cross-invocation pacer state is not persisted. The AIMD interval resets whenever a cold
container starts, so a 429 in one cycle does not slow the next. That is accepted because
zero throttle events have ever been observed, and the alarm above is what makes accepting
it defensible rather than merely convenient. A warm container happens to retain the pacer
through the module-level global, but that is opportunistic and nothing depends on it.

Configuration by environment variable
-------------------------------------
    POLL_BUCKET     required, S3 bucket for output
    POLL_PREFIX     required, key prefix — the parallel run writes to its own
    POLL_STAGE      metric dimension, e.g. "parallel" or "live"
    POLL_NUM_MINS   station board lookahead, default 90
    POLL_RATE       requests/second, default 2.0
    POLL_NO_QUIET   set to "1" to poll through quiet hours
"""

import json
import os
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import boto3
import requests

from poll_live import (USER_AGENT, in_dublin, in_quiet_hours, load_station_config,
                       poll_cycle)
from backfill import Pacer
from sinks import S3Sink

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG = Path(os.environ.get("POLL_CONFIG", REPO_ROOT / "config" / "poll_stations.toml"))
STATIONS = Path(os.environ.get("POLL_STATIONS",
                               REPO_ROOT / "data" / "live" / "stations.json"))

NAMESPACE = "RailDelay"

# Module-level so a warm container reuses the connection pool and, incidentally, the
# pacer's adapted interval. Neither is relied upon — a cold start rebuilds both.
_session = requests.Session()
_session.headers["User-Agent"] = USER_AGENT
_pacer = Pacer(float(os.environ.get("POLL_RATE", "2.0")))
_stations = None
_s3 = None


def s3_client():
    """Created on first use, then reused for the container's life.

    Not built at import time: `boto3.client("s3")` needs a resolvable region, so a
    module-level client makes this file unimportable on a laptop without AWS config —
    which would mean the handler could only ever be tested by deploying it.
    """
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3")
    return _s3


def stations():
    """Loaded once per container. The station list and config ship inside the deployment
    package, so there is no S3 read and no getAllStationsXML call at runtime — one fewer
    request per cold start and one fewer IAM permission."""
    global _stations
    if _stations is None:
        known = {s["code"].upper(): s["name"]
                 for s in json.loads(STATIONS.read_text(encoding="utf-8"))}
        _stations = load_station_config(CONFIG, known)
    return _stations


def emit_metrics(stage: str, values: dict) -> None:
    """One EMF line on stdout. CloudWatch extracts the metrics from the log itself."""
    print(json.dumps({
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [{
                "Namespace": NAMESPACE,
                "Dimensions": [["Stage"]],
                "Metrics": [{"Name": k, "Unit": u} for k, u in (
                    ("ThrottleEvents", "Count"), ("RequestFailures", "Count"),
                    ("StationsCompleted", "Count"), ("StationsSkipped", "Count"),
                    ("RecordsCaptured", "Count"), ("PartialCycle", "Count"),
                    ("CycleDurationSec", "Seconds"), ("QuietSkip", "Count"),
                ) if k in values],
            }],
        },
        "Stage": stage,
        **values,
    }))


def lambda_handler(event, context):
    stage = os.environ.get("POLL_STAGE", "parallel")
    bucket = os.environ["POLL_BUCKET"]
    prefix = os.environ["POLL_PREFIX"]
    num_mins = int(os.environ.get("POLL_NUM_MINS", "90"))
    now = datetime.now()

    if os.environ.get("POLL_NO_QUIET") != "1" and in_quiet_hours(now):
        emit_metrics(stage, {"QuietSkip": 1, "RecordsCaptured": 0})
        return {"status": "quiet_hours", "dublin_time": in_dublin(now).isoformat()}

    started = time.monotonic()
    stats = Counter()
    sink = S3Sink(bucket, prefix, s3_client())
    before_throttles = _pacer.throttle_events

    captured = poll_cycle(_session, _pacer, stations(), sink, stats, num_mins,
                          time_left=context.get_remaining_time_in_millis)

    duration = time.monotonic() - started
    throttles = _pacer.throttle_events - before_throttles
    partial = 1 if stats["partial_cycles"] else 0
    emit_metrics(stage, {
        "ThrottleEvents": throttles,
        "RequestFailures": stats["station_failed"] + stats["current_failed"],
        "StationsCompleted": stats["station_ok"],
        "StationsSkipped": stats["stations_skipped"],
        "RecordsCaptured": captured,
        "PartialCycle": partial,
        "CycleDurationSec": round(duration, 2),
    })

    return {
        "status": "partial" if partial else "complete",
        "records": captured,
        "stations_ok": stats["station_ok"],
        "failures": stats["station_failed"] + stats["current_failed"],
        "throttles": throttles,
        "duration_sec": round(duration, 2),
        "remaining_ms": context.get_remaining_time_in_millis(),
    }
