"""
poll_live.py — capture the operator's own live estimate, which cannot be backfilled.

`ExpectedArrival` is Irish Rail's live prediction for a train at a station. It is the
benchmark this project has to beat (CLAUDE.md), and it exists **only in the moment**.
`getTrainMovementsXML` serves fifteen years of history; the station board serves the next
ninety minutes and nothing else. Every minute this is not running is a minute of benchmark
data that can never be recovered.

What it does, each cycle:

  1. `getCurrentTrainsXML` — one call. Archived raw; also the live fleet snapshot, whose
     PublicMessage delay text is equally unrecoverable.
  2. `getStationDataByCodeXML_WithNumMins` — one call per station. Archived raw, and every
     `objStationData` record appended to a JSONL log with the poll timestamp.

The `_WithNumMins` variant is used rather than plain `getStationDataByCodeXML` because it
is the one verified in docs/data-dictionary.md and it makes the lookahead window explicit.

**Repeated observations of the same train are the point, not duplication.** The operator
revises `ExpectedArrival` as a train approaches. Comparing our prediction against theirs
requires knowing what they were saying *at the moment we would have predicted*, so every
poll is kept. Nothing is deduplicated.

Pacing and retries are imported from backfill.py — the same AIMD pacer, the same
three-class retry policy (transport / throttled / permanent), the same atomic gzip writes.
See decisions.md D6, D7, D8. Only the endpoint and the body guard differ, so nothing is
reimplemented here.

Output
------
    data/raw/live/current/{UTC}.xml.gz              raw fleet snapshots
    data/raw/live/station/{CODE}/{UTC}.xml.gz       raw station boards
    data/live/expected/{YYYY-MM-DD}.jsonl           one line per (poll, station, train)

Every field the feed returns is preserved verbatim, plus `polled_at`, `station_code` and
`source_file`. JSONL rather than Parquet because this appends continuously and must
survive a kill at any instant; converting to Parquet later is trivial and safe.

Recovery
--------
There is none, and that is the point of the reliability principle in CLAUDE.md: a live
feed cannot be re-fetched. If this stops, restart it — the gap stays a gap. Interrupting is
otherwise safe: raw files are written atomically and JSONL lines are flushed per record.

Which stations
--------------
A stratified subset of 30, defined in config/poll_stations.toml, not all 171. The
stratification is a measurement decision before it is a politeness one: the claim this
project is aiming at is "we beat the operator on well-covered lines, and here is where we
cannot", and that needs comparison events on each KIND of line. A uniform thinning would
be mostly Dublin commuter stops — that is simply where most stations are — leaving the
weak-coverage lines with too few events to say anything about. Reasoning in D29.

Every record carries `station_group`, so the per-line-type comparison is a group-by rather
than a station list reconstructed by hand months later.

`--all-stations` restores the full sweep: 172 requests per cycle, ~39,000/day at a
five-minute interval, against ~6,900/day for the subset.

Politeness
----------
The 2 req/s budget is per HOST, not per script, so this takes an exclusive lock
(src/hostlock.py) and refuses to start while harvest_codes.py or backfill.py is running.
harvest_codes.py also polls getCurrentTrainsXML; this writes to a separate directory so
the archives stay distinguishable, but running both would double the rate against one
server for no extra information.

Usage (PowerShell, from the repo root, venv active):

    python src\\poll_live.py                       # 30 stratified stations, every 5 min
    python src\\poll_live.py --once                # one cycle, then exit
    python src\\poll_live.py --all-stations        # full 171-station sweep
    python src\\poll_live.py --stations CNLLY,HSTON,CORK
    python src\\poll_live.py --interval 600 --no-quiet-hours
"""

import argparse
import json
import sys
import time
import tomllib
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, time as dtime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

import hostlock
from sinks import LocalSink

# Reuse, not reimplementation — see decisions.md D6/D7/D8.
from backfill import (BASE, MAX_THROTTLE_ATTEMPTS, MAX_TRANSPORT_ATTEMPTS,
                      MAX_BADBODY_ATTEMPTS, THROTTLE_CODES, TIMEOUT, Failure, Pacer,
                      append_jsonl, fmt_duration, jittered_backoff, retry_after_seconds,
                      write_gz)

NS = "{http://api.irishrail.ie/realtime/}"
USER_AGENT = "rail-delay/0.1 (research project; live benchmark capture)"

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RAW = REPO_ROOT / "data" / "raw" / "live"
DEFAULT_OUT = REPO_ROOT / "data" / "live" / "expected"
STATION_CACHE = REPO_ROOT / "data" / "live" / "stations.json"
DEFAULT_CONFIG = REPO_ROOT / "config" / "poll_stations.toml"

# data-dictionary.md section 6: the network is quiet roughly 00:30-05:30. Polling an
# empty railway 60 times is 10,000 pointless requests a night.
QUIET_START = dtime(0, 30)
QUIET_END = dtime(5, 30)

# The railway keeps Irish time, so the quiet window must be evaluated in Irish time —
# not in whatever zone the machine running this happens to be set to.
DUBLIN = ZoneInfo("Europe/Dublin")

# When a caller imposes a deadline (Lambda), stop starting new station requests with less
# than this much left. A request plus its write needs headroom; being killed mid-write is
# how you get a truncated object that looks complete.
TIME_BUDGET_FLOOR_MS = 20_000


def looks_like_xml(body: bytes, expect: str) -> bool:
    """Byte-level guard. ASMX returns HTTP 200 with an HTML error page often enough."""
    head = body[:4096].lstrip(b"\xef\xbb\xbf")
    return head.startswith(b"<?xml") and expect.encode() in head.lower()


def fetch(session, path: str, params: dict, pacer: Pacer, expect: str):
    """One request under the backfill retry policy, parameterised by endpoint.

    Same three classes as backfill.fetch: a transport error retries without touching the
    global pace (the server never complained), a 429/503 widens the pace and waits, and
    any other 4xx is permanent and gets no retry at all.
    """
    url = f"{BASE}/{path}"
    transport = throttled = badbody = 0
    while True:
        pacer.wait()
        try:
            r = session.get(url, params=params, timeout=TIMEOUT)
        except requests.RequestException as e:
            transport += 1
            if transport >= MAX_TRANSPORT_ATTEMPTS:
                raise Failure("transport", f"{type(e).__name__}: {e}")
            time.sleep(jittered_backoff(transport))
            continue

        if r.status_code in THROTTLE_CODES:
            throttled += 1
            pacer.slow_down()
            if throttled >= MAX_THROTTLE_ATTEMPTS:
                raise Failure("throttled", f"HTTP {r.status_code} after {throttled} tries",
                              r.status_code)
            wait = retry_after_seconds(r)
            time.sleep(wait if wait is not None else pacer.interval)
            continue

        if r.status_code >= 400:
            raise Failure("permanent", f"HTTP {r.status_code}", r.status_code)

        body = r.content
        if not looks_like_xml(body, expect):
            badbody += 1
            if badbody >= MAX_BADBODY_ATTEMPTS:
                raise Failure("badbody", f"{len(body)} bytes, not {expect}", r.status_code)
            time.sleep(jittered_backoff(badbody))
            continue

        pacer.record_success()
        return body


def load_stations(session, pacer, refresh: bool) -> list[dict]:
    """Station list from getAllStationsXML, cached — it changes on a scale of years."""
    if STATION_CACHE.exists() and not refresh:
        with open(STATION_CACHE, "r", encoding="utf-8") as f:
            return json.load(f)

    body = fetch(session, "getAllStationsXML", {}, pacer, "objstation")
    root = ET.fromstring(body)
    stations = []
    for s in root.findall(NS + "objStation"):
        code = s.find(NS + "StationCode")
        desc = s.find(NS + "StationDesc")
        if code is not None and code.text and code.text.strip():
            stations.append({"code": code.text.strip(),
                             "name": (desc.text or "").strip() if desc is not None else ""})
    stations.sort(key=lambda s: s["code"])
    STATION_CACHE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATION_CACHE, "w", encoding="utf-8") as f:
        json.dump(stations, f, indent=2)
    return stations


def load_station_config(path: Path, known: dict) -> list[dict]:
    """Stratified subset from TOML. Each station carries its group — see decisions.md D29.

    Unknown codes are a hard error, not a warning: a typo would silently drop a whole
    stratum and the gap would only surface when the comparison came up short.
    """
    with open(path, "rb") as f:
        cfg = tomllib.load(f)

    out, seen, bad = [], set(), []
    for group, block in cfg.items():
        for code in block.get("stations", []):
            code = code.strip().upper()
            if code not in known:
                bad.append(f"{code} (in [{group}])")
                continue
            if code in seen:
                continue
            seen.add(code)
            out.append({"code": code, "name": known[code], "group": group})
    if bad:
        raise ValueError(f"unknown station codes in {path.name}: {', '.join(bad)}")
    return out


def utc_stamp(now: datetime) -> str:
    return now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def in_dublin(now: datetime) -> datetime:
    """Convert a possibly-naive stamp to Europe/Dublin.

    `datetime.now()` returns naive local time. On a laptop in Ireland that is already
    Irish time, so this is a no-op today. The moment it runs anywhere with a UTC clock —
    Lambda, a container, a VM in another region — anything derived from a naive stamp is
    an hour out during IST. Nothing errors and no output looks wrong, which is why the
    zone is made explicit rather than left to host configuration.
    """
    if now.tzinfo is None:
        now = now.astimezone()  # interpret a naive stamp as the host's local time
    return now.astimezone(DUBLIN)


def in_quiet_hours(now: datetime) -> bool:
    """Is it quiet-hours on the Irish railway, whatever the host clock is set to?"""
    return QUIET_START <= in_dublin(now).time() < QUIET_END


def extract_station_records(body: bytes, station_code: str, station_group: str,
                            polled_at: str, source_file: str) -> list[dict]:
    """Every objStationData record, all fields preserved verbatim plus provenance.

    `station_group` is written onto every row so the per-line-type comparison against
    the operator is a group-by later, not a manual station list.
    """
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return []
    out = []
    for rec in root.findall(NS + "objStationData"):
        row = {"polled_at": polled_at, "station_code": station_code,
               "station_group": station_group, "source_file": source_file}
        for child in rec:
            row[child.tag.replace(NS, "")] = (child.text or "").strip()
        out.append(row)
    return out


def poll_cycle(session, pacer, stations, sink, stats: Counter, num_mins: int,
               time_left=None) -> int:
    """One full sweep. Returns the number of ExpectedArrival records captured.

    All storage goes through `sink` (src/sinks.py) so this stays the single
    implementation whether output lands on local disk or in S3. Duplicating it for a
    Lambda handler would make the parallel-run diff test the duplicate rather than the
    port — the mistake D35 records.

    `time_left`, when supplied, returns remaining milliseconds. The sweep then stops
    early rather than being killed mid-write, and closes the cycle as partial. Nothing
    passes it locally: a local run has no deadline.
    """
    now = datetime.now()
    stamp = utc_stamp(now)
    # The day key must be the Irish calendar date, not the host's. On a UTC host an
    # evening's records either side of midnight would otherwise split across two files
    # an hour early, and the service day is an Irish-time concept.
    day = in_dublin(now).strftime("%Y-%m-%d")
    # Offset-aware: a bare '2026-08-10T21:44:54' is only interpretable if you also know
    # which machine wrote it. This stamp is the temporal cutoff the whole operator
    # comparison rests on, so it carries its own zone.
    polled_at = in_dublin(now).isoformat(timespec="seconds")
    sink.begin_cycle(stamp, day)
    captured = 0
    attempted, skipped, failed = [], [], []

    # 1. fleet snapshot
    try:
        body = fetch(session, "getCurrentTrainsXML", {}, pacer, "objtrainpositions")
    except Failure as f:
        stats["current_failed"] += 1
        print(f"  ! getCurrentTrainsXML {f.kind}: {f.detail}")
    else:
        sink.put_raw("current", body)
        stats["current_ok"] += 1

    # 2. station boards
    for i, st in enumerate(stations):
        code = st["code"]
        if time_left is not None and time_left() < TIME_BUDGET_FLOOR_MS:
            skipped = [s["code"] for s in stations[i:]]
            break
        attempted.append(code)

        try:
            body = fetch(session, "getStationDataByCodeXML_WithNumMins",
                         {"StationCode": code, "NumMins": num_mins},
                         pacer, "objstationdata")
        except Failure as f:
            stats["station_failed"] += 1
            failed.append(code)
            sink.put_failure({
                "ts": datetime.now().isoformat(timespec="seconds"),
                "station": code, "kind": f.kind, "detail": f.detail,
            })
            continue

        source_file = sink.put_raw("station", body, station=code)
        stats["station_ok"] += 1

        rows = extract_station_records(body, code, st.get("group", ""),
                                       polled_at, source_file)
        if not rows:
            stats["station_empty"] += 1
        sink.put_records(rows)
        captured += len(rows)

    if skipped:
        stats["partial_cycles"] += 1
        stats["stations_skipped"] += len(skipped)
        print(f"  ! partial cycle — {len(skipped)} stations skipped on the time "
              f"budget: {', '.join(skipped)}")

    # A cycle where every station failed is not "complete". On 2026-08-24 an ISP
    # interception page returned HTTP 200 for seven hours; the body guard rejected all
    # 2,460 responses correctly, but the cycle still recorded status "complete" with zero
    # records, so a total outage was indistinguishable from a quiet railway.
    if attempted and not failed:
        status = "partial" if skipped else "complete"
    elif len(failed) == len(attempted):
        status = "failed"
        stats["failed_cycles"] += 1
        print(f"  !! CYCLE FAILED — all {len(failed)} stations errored. "
              f"Nothing recorded. Check connectivity.")
    else:
        status = "degraded"
        print(f"  ! degraded cycle — {len(failed)} of {len(attempted)} stations "
              f"failed: {', '.join(failed)}")

    sink.end_cycle({
        "cycle_ts": stamp, "day": day, "polled_at": polled_at,
        "status": status,
        "stations_attempted": attempted, "stations_skipped": skipped,
        "stations_failed": failed,
        "records": captured,
        "pacer_interval_sec": round(pacer.interval, 3),
        "throttle_events": pacer.throttle_events,
    })

    stats["records"] += captured
    return captured


def sleep_until(deadline: float) -> None:
    """Short slices so Ctrl-C lands immediately rather than minutes later."""
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 1.0))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--interval", type=float, default=300,
                    help="seconds between cycle starts (default: 300)")
    ap.add_argument("--num-mins", type=int, default=90,
                    help="station board lookahead, 5-90 (default: 90)")
    ap.add_argument("--rate", type=float, default=2.0, help="requests/second (default: 2)")
    ap.add_argument("--stations", default="",
                    help="comma-separated station codes, overriding the config")
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG,
                    help=f"stratified station set (default: {DEFAULT_CONFIG})")
    ap.add_argument("--all-stations", action="store_true",
                    help="poll all 171 stations instead of the stratified subset")
    ap.add_argument("--max-stations", type=int, default=0,
                    help="cap the station count, for testing (0 = no cap)")
    ap.add_argument("--force-lock", action="store_true",
                    help="take the API lock even if another collector holds it")
    ap.add_argument("--raw", type=Path, default=DEFAULT_RAW)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--refresh-stations", action="store_true",
                    help="re-fetch the station list instead of using the cache")
    ap.add_argument("--no-quiet-hours", action="store_true",
                    help="poll through 00:30-05:30 as well")
    ap.add_argument("--once", action="store_true", help="one cycle, then exit")
    args = ap.parse_args()

    if not 5 <= args.num_mins <= 90:
        print("--num-mins must be between 5 and 90 (documented API limit)")
        return 2
    if args.rate <= 0:
        print("--rate must be positive")
        return 2

    try:
        with hostlock.acquire("poll_live", force=args.force_lock):
            return run(args)
    except hostlock.LockHeld as e:
        print(f"cannot start: {e}")
        return 2


def run(args) -> int:
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    pacer = Pacer(args.rate)

    try:
        all_stations = load_stations(session, pacer, args.refresh_stations)
    except Failure as f:
        print(f"could not load the station list ({f.kind}: {f.detail})")
        return 2
    known = {s["code"].upper(): s["name"] for s in all_stations}

    if args.stations:
        wanted = [s.strip().upper() for s in args.stations.split(",") if s.strip()]
        missing = [c for c in wanted if c not in known]
        if missing:
            print(f"  ! unknown station codes ignored: {', '.join(missing)}")
        stations = [{"code": c, "name": known[c], "group": "adhoc"}
                    for c in wanted if c in known]
        source = "--stations"
    elif args.all_stations:
        stations = [{**s, "group": "all"} for s in all_stations]
        source = "all 171 stations"
    else:
        try:
            stations = load_station_config(args.config, known)
        except (OSError, ValueError, tomllib.TOMLDecodeError) as e:
            print(f"could not read {args.config}: {e}")
            return 2
        source = args.config.name

    if args.max_stations:
        stations = stations[:args.max_stations]
    if not stations:
        print("no stations selected")
        return 2

    per_cycle = len(stations) + 1
    cycle_secs = per_cycle / args.rate
    active_hours = 24 if args.no_quiet_hours else 19
    per_day = int(per_cycle * (active_hours * 3600 / args.interval))

    print(f"poll_live — {len(stations)} stations from {source}, "
          f"every {args.interval:.0f}s, {args.num_mins} min lookahead")
    groups = Counter(s["group"] for s in stations)
    for g, n in sorted(groups.items()):
        codes = ", ".join(s["code"] for s in stations if s["group"] == g)
        print(f"    {g:<24} {n:>2}  {codes}")
    print(f"  {per_cycle} requests/cycle at {args.rate:g} req/s "
          f"-> ~{fmt_duration(cycle_secs)} per cycle")
    print(f"  ~{per_day:,} requests/day, average {per_day / (active_hours * 3600):.2f} "
          f"req/s over the polling window")
    if cycle_secs > args.interval:
        print(f"  ! a cycle takes longer than the interval; cycles will run back to back")
    print(f"  quiet hours 00:30-05:30: "
          f"{'polled anyway' if args.no_quiet_hours else 'skipped'}")
    print(f"  raw -> {args.raw}")
    print(f"  records -> {args.out}")
    print("  Ctrl-C is safe. There is no recovery for a missed poll — restart promptly.\n")

    stats = Counter()
    cycles = 0
    started = time.monotonic()
    sink = LocalSink(args.raw, args.out, args.out.parent / "poll_failures.jsonl",
                     write_gz, append_jsonl)
    try:
        while True:
            cycle_started = time.monotonic()
            now = datetime.now()

            hostlock.heartbeat()
            if not args.no_quiet_hours and in_quiet_hours(now):
                print(f"  {now:%H:%M:%S}  quiet hours, skipping")
            else:
                was_ok, was_failed = stats["station_ok"], stats["station_failed"]
                n = poll_cycle(session, pacer, stations, sink, stats, args.num_mins)
                cycles += 1
                # Per-cycle, not cumulative. The cumulative counter froze at 420 during
                # the 2026-08-24 outage and read as healthy for seven hours.
                ok = stats["station_ok"] - was_ok
                bad = stats["station_failed"] - was_failed
                print(f"  {now:%H:%M:%S}  {ok:>3}/{len(stations)} boards ok, "
                      f"{bad:>3} failed, {n:>5} records this cycle, "
                      f"{stats['records']:>7} total  pace={pacer.interval:.2f}s")

            if args.once:
                break
            sleep_until(cycle_started + args.interval)
    except KeyboardInterrupt:
        print("\ninterrupted")

    print(f"\n{cycles} cycles in {fmt_duration(time.monotonic() - started)}")
    print(f"  ExpectedArrival records captured: {stats['records']}")
    print(f"  station boards ok {stats['station_ok']}, "
          f"empty {stats['station_empty']}, failed {stats['station_failed']}")
    print(f"  fleet snapshots ok {stats['current_ok']}, failed {stats['current_failed']}")
    if pacer.throttle_events:
        print(f"  {pacer.throttle_events} throttle events — the server asked for less")
    return 0


if __name__ == "__main__":
    sys.exit(main())
