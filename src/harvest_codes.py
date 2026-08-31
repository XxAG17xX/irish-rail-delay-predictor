"""
harvest_codes.py — build the list of train codes that exist on the network.

The problem this solves: no Irish Rail endpoint answers "which trains ran on date X".
getCurrentTrainsXML only shows trains currently moving, or starting within 10 minutes.
So we poll it across a full service day and accumulate the union of TrainCode values.
That accumulated list is what backfill.py replays against past dates.

Run it across a full service day (~05:30-00:30) on a weekday AND on a weekend — the
timetables differ, and a Saturday-only harvest will miss the Mon-Fri commuter services.

State is a single JSON file, merged and rewritten atomically after every poll. Killing
the script with Ctrl-C loses at most the current poll; restarting picks up the existing
file and keeps accumulating. Running it on ten separate days just widens the same file.

Each raw getCurrentTrainsXML response is also archived gzipped. Unlike train movements,
this endpoint has no history — PublicMessage delay text and live positions are gone the
moment the poll passes. Archiving costs a few KB per poll.

Usage (PowerShell, from the repo root, venv active):

    python src\\harvest_codes.py                    # poll every 5 min until Ctrl-C
    python src\\harvest_codes.py --interval 120     # every 2 minutes
    python src\\harvest_codes.py --once             # single poll, then exit
    python src\\harvest_codes.py --no-snapshots     # codes only, don't archive bodies

Note: the 2 req/s politeness budget is per-host, not per-script. This script is far
below it on its own, but don't assume that still holds if backfill.py is running too.
"""

import argparse
import gzip
import json
import os
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import requests

import hostlock

BASE = "http://api.irishrail.ie/realtime/realtime.asmx"
NS = "{http://api.irishrail.ie/realtime/}"
USER_AGENT = "rail-delay/0.1 (research project; low-volume polling)"

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STATE = REPO_ROOT / "data" / "codes.json"
DEFAULT_SNAPSHOT_DIR = REPO_ROOT / "data" / "raw" / "current"

# Where poll_live archived its getCurrentTrainsXML responses. A DIFFERENT directory from
# DEFAULT_SNAPSHOT_DIR above, which is where this script's own live mode writes. Two
# producers, two archives, and --from-snapshots wants the poller's one because it ran far
# longer. It used to require the path be typed correctly from memory; getting it wrong
# yields "0 new codes", which reads as a network with no new services.
DEFAULT_LIVE_SNAPSHOTS = REPO_ROOT / "data" / "raw" / "live" / "current"

# Past this, a snapshot archive is treated as dead rather than quiet. Both local archives
# froze when the local poller stopped at the 2026-08-31 cutover (D54), so every run from
# now on trips this until snapshots come from somewhere live again.
MAX_SNAPSHOT_AGE_HOURS = 24

TIMEOUT = 20
POLL_RETRIES = 3           # attempts within a single poll before giving up on it
POLL_RETRY_BACKOFF = 5     # seconds, multiplied by attempt number
THROTTLE_CODES = {429, 503, 502, 504}

DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


# ----------------------------------------------------------------- state file

def load_state(path: Path) -> dict:
    """Read the accumulated code list. A missing or corrupt file is not fatal."""
    if not path.exists():
        return {"_meta": {"polls": 0, "updated": None}, "codes": {}}
    try:
        with open(path, "r", encoding="utf-8") as f:
            state = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        # Only reachable if a write was interrupted before atomic replace, which
        # shouldn't happen — but losing the harvest to a parse error would.
        backup = path.with_suffix(f".corrupt-{int(time.time())}.json")
        print(f"  ! state file unreadable ({e}); moved to {backup.name}, starting fresh")
        os.replace(path, backup)
        return {"_meta": {"polls": 0, "updated": None}, "codes": {}}

    state.setdefault("_meta", {"polls": 0, "updated": None})
    state.setdefault("codes", {})
    return state


def save_state(path: Path, state: dict) -> None:
    """Write via temp file + atomic replace, so an interrupt can never truncate it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)
    os.replace(tmp, path)


def merge_codes(state: dict, codes: set, day: str) -> int:
    """Fold today's observations into the state. Returns the count of codes never seen before."""
    weekday = DAY_NAMES[datetime.strptime(day, "%Y-%m-%d").weekday()]
    new = 0
    for code in codes:
        entry = state["codes"].get(code)
        if entry is None:
            state["codes"][code] = {
                "first_seen": day,
                "last_seen": day,
                "days_of_week": [weekday],
            }
            new += 1
            continue
        entry["first_seen"] = min(entry["first_seen"], day)
        entry["last_seen"] = max(entry["last_seen"], day)
        days = set(entry.get("days_of_week", []))
        days.add(weekday)
        # Keep in weekday order rather than alphabetical — it reads as a timetable.
        entry["days_of_week"] = [d for d in DAY_NAMES if d in days]
    return new


# --------------------------------------------------------------------- polling

def poll(session: requests.Session) -> bytes | None:
    """One getCurrentTrainsXML request, with a few retries. None means give up on this poll.

    There is no adaptive slowdown here: the next poll is minutes away, so a 429 is
    answered by skipping this poll entirely rather than by widening an interval.
    """
    url = f"{BASE}/getCurrentTrainsXML"
    for attempt in range(1, POLL_RETRIES + 1):
        try:
            r = session.get(url, timeout=TIMEOUT)
        except requests.RequestException as e:
            print(f"  ! network error ({type(e).__name__}), attempt {attempt}/{POLL_RETRIES}")
        else:
            if r.status_code in THROTTLE_CODES:
                print(f"  ! HTTP {r.status_code} — server asking for less load, skipping this poll")
                return None
            if r.status_code >= 400:
                print(f"  ! HTTP {r.status_code} — skipping this poll")
                return None
            return r.content

        if attempt < POLL_RETRIES:
            time.sleep(POLL_RETRY_BACKOFF * attempt)
    return None


def extract_codes(body: bytes) -> set:
    """Pull TrainCode values out of a getCurrentTrainsXML body.

    All TrainStatus values count (N not-yet-running, R running, T terminated) — every
    one is a real code that operated today, which is the only thing we're after.
    TrainCode carries trailing whitespace in the feed; strip it or you get duplicates.
    """
    root = ET.fromstring(body)
    codes = set()
    for train in root.findall(NS + "objTrainPositions"):
        el = train.find(NS + "TrainCode")
        if el is not None and el.text and el.text.strip():
            codes.add(el.text.strip().upper())
    return codes


def archive_snapshot(snapshot_dir: Path, body: bytes, now: datetime) -> None:
    """Gzip the raw response. Atomic, same as the state file."""
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    name = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".xml.gz"
    dest = snapshot_dir / name
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    with gzip.open(tmp, "wb") as z:
        z.write(body)
    os.replace(tmp, dest)


def newest_snapshot(directory: Path):
    """UTC stamp of the most recent snapshot in `directory`, or None if there are none.

    Read from the FILENAME, not the file's mtime. An `aws s3 sync`, a copy between drives
    or a restore from backup all rewrite mtimes, and the whole point of this check is to
    know when the data was captured rather than when the bytes last moved.
    """
    newest = None
    for path in directory.glob("*.xml.gz"):
        try:
            stamp = datetime.strptime(path.name.split(".")[0], "%Y%m%dT%H%M%SZ")
        except ValueError:
            continue
        stamp = stamp.replace(tzinfo=timezone.utc)
        if newest is None or stamp > newest:
            newest = stamp
    return newest


def snapshot_age_hours(directory: Path, now: datetime = None):
    """Hours since the newest snapshot was captured. None if the directory holds none."""
    newest = newest_snapshot(directory)
    if newest is None:
        return None
    now = now or datetime.now(timezone.utc)
    return (now - newest).total_seconds() / 3600


def merge_from_snapshots(state: dict, directory: Path) -> tuple[int, int, int]:
    """Fold codes out of already-archived getCurrentTrainsXML snapshots.

    poll_live.py calls the same endpoint on the same cadence and archives every response,
    so a weekend of live polling has already collected exactly what a weekend of harvesting
    would have. This reads those files instead of re-requesting anything — no network, so
    no API lock needed.

    The observation date comes from the filename's UTC stamp converted to local time,
    matching what a live harvest would have recorded via datetime.now().
    """
    files = sorted(directory.glob("*.xml.gz"))
    read = failed = new_total = 0
    for path in files:
        try:
            stamp = datetime.strptime(path.name.split(".")[0], "%Y%m%dT%H%M%SZ")
            day = stamp.replace(tzinfo=timezone.utc).astimezone().strftime("%Y-%m-%d")
        except ValueError:
            failed += 1
            continue
        try:
            with gzip.open(path, "rb") as z:
                codes = extract_codes(z.read())
        except (OSError, EOFError, ET.ParseError):
            failed += 1
            continue
        read += 1
        if codes:
            new_total += merge_codes(state, codes, day)
    return read, failed, new_total


def sleep_until(deadline: float) -> None:
    """Sleep in short slices so Ctrl-C lands immediately rather than up to 5 minutes later."""
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 1.0))


# ------------------------------------------------------------------------ main

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--interval", type=float, default=300,
                    help="seconds between polls (default: 300)")
    ap.add_argument("--state", type=Path, default=DEFAULT_STATE,
                    help=f"code list JSON (default: {DEFAULT_STATE})")
    ap.add_argument("--snapshot-dir", type=Path, default=DEFAULT_SNAPSHOT_DIR,
                    help=f"where raw responses are archived (default: {DEFAULT_SNAPSHOT_DIR})")
    ap.add_argument("--no-snapshots", action="store_true",
                    help="extract codes only, do not archive the raw bodies")
    ap.add_argument("--once", action="store_true", help="single poll, then exit")
    ap.add_argument("--force-lock", action="store_true",
                    help="take the API lock even if another collector holds it")
    ap.add_argument("--from-snapshots", type=Path, nargs="?",
                    const=DEFAULT_LIVE_SNAPSHOTS, default=None,
                    help=f"merge codes from archived getCurrentTrainsXML snapshots "
                         f"instead of polling, defaulting to {DEFAULT_LIVE_SNAPSHOTS} "
                         f"(the poller's archive, not this script's). No network, so no "
                         f"API lock is taken.")
    ap.add_argument("--max-snapshot-age-hours", type=float,
                    default=MAX_SNAPSHOT_AGE_HOURS,
                    help=f"refuse an archive whose newest snapshot is older than this "
                         f"(default: {MAX_SNAPSHOT_AGE_HOURS})")
    ap.add_argument("--allow-stale", action="store_true",
                    help="merge from an archive that has stopped growing anyway. For a "
                         "deliberate one-off backfill of a frozen archive.")
    args = ap.parse_args()

    if args.from_snapshots:
        if not args.from_snapshots.exists():
            print(f"no snapshot directory at {args.from_snapshots}")
            return 2

        # The guard, and the reason this script needed one. A frozen archive yields "0 new
        # codes", which is exactly what a healthy network with no new services yields. The
        # two are indistinguishable from the output, so the failure is silent and the
        # script keeps reporting success over a dead directory for as long as anyone runs
        # it. Checking the age turns "nothing new" into "nothing since <date>".
        age = snapshot_age_hours(args.from_snapshots)
        if age is None:
            print(f"no readable snapshots in {args.from_snapshots} — nothing to merge")
            return 2
        newest = newest_snapshot(args.from_snapshots)
        if age > args.max_snapshot_age_hours and not args.allow_stale:
            print(f"STALE ARCHIVE — refusing to merge.\n"
                  f"  {args.from_snapshots}\n"
                  f"  newest snapshot {newest:%Y-%m-%d %H:%M}Z, {age:.1f} hours old "
                  f"(limit {args.max_snapshot_age_hours:.0f}h)\n"
                  f"  Nothing is writing here. Merging would report '0 new codes', which\n"
                  f"  is indistinguishable from a network with no new services.\n"
                  f"  Pass --allow-stale for a deliberate one-off merge of a dead archive.")
            return 3

        state = load_state(args.state)
        before = len(state["codes"])
        print(f"harvest_codes — merging from {args.from_snapshots}")
        print(f"  newest snapshot {newest:%Y-%m-%d %H:%M}Z, {age:.1f} hours old"
              f"{'  (STALE, allowed explicitly)' if age > args.max_snapshot_age_hours else ''}")
        print(f"  {before} codes already known")
        read, failed, new = merge_from_snapshots(state, args.from_snapshots)
        state["_meta"]["updated"] = datetime.now().isoformat(timespec="seconds")
        save_state(args.state, state)
        print(f"  {read} snapshots read, {failed} unreadable")
        print(f"  {new} new codes  ->  {len(state['codes'])} total in {args.state}")
        weekend = sum(1 for e in state["codes"].values()
                      if {"Sat", "Sun"} & set(e.get("days_of_week", [])))
        print(f"  {weekend} codes have now been seen on a weekend, "
              f"{len(state['codes']) - weekend} still weekday-only")
        return 0

    # The 2 req/s budget is per host. See src/hostlock.py and decisions.md D29.
    try:
        lock = hostlock.acquire("harvest_codes", force=args.force_lock)
        lock.__enter__()
    except hostlock.LockHeld as e:
        print(f"cannot start: {e}")
        return 2

    try:
        return _run(args)
    finally:
        lock.__exit__(None, None, None)


def _run(args) -> int:
    state = load_state(args.state)
    print(f"harvest_codes — polling every {args.interval:.0f}s, state at {args.state}")
    print(f"  loaded {len(state['codes'])} known codes from {state['_meta'].get('polls', 0)} "
          f"previous polls")
    if not args.no_snapshots:
        print(f"  archiving raw snapshots to {args.snapshot_dir}")
    print("  Ctrl-C to stop; progress is saved after every poll\n")

    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    polls_this_run = 0
    try:
        while True:
            started = time.monotonic()
            now = datetime.now()
            hostlock.heartbeat()
            body = poll(session)

            if body is not None:
                try:
                    codes = extract_codes(body)
                except ET.ParseError as e:
                    print(f"  ! {now:%H:%M:%S}  malformed XML ({e}) — poll discarded")
                    codes = None

                if codes is not None:
                    new = merge_codes(state, codes, now.strftime("%Y-%m-%d"))
                    state["_meta"]["polls"] = state["_meta"].get("polls", 0) + 1
                    state["_meta"]["updated"] = now.isoformat(timespec="seconds")
                    save_state(args.state, state)
                    polls_this_run += 1

                    if not args.no_snapshots:
                        archive_snapshot(args.snapshot_dir, body, now)

                    print(f"  {now:%H:%M:%S}  {len(codes):3} trains live, "
                          f"{new:3} new  ->  {len(state['codes'])} known")

            if args.once:
                break
            sleep_until(started + args.interval)

    except KeyboardInterrupt:
        print("\ninterrupted")

    print(f"\n{polls_this_run} poll{'' if polls_this_run == 1 else 's'} this run. "
          f"{len(state['codes'])} codes total in {args.state}")
    if state["codes"]:
        weekday_only = sum(1 for e in state["codes"].values()
                           if not ({"Sat", "Sun"} & set(e.get("days_of_week", []))))
        print(f"  {weekday_only} of them have only ever been seen on a weekday — "
              f"harvest on a weekend too before trusting that.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
