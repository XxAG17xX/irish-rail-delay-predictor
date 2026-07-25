"""
backfill.py — download raw getTrainMovementsXML for every (date, train code) pair.

Writes data/raw/{YYYY-MM-DD}/{CODE}.xml.gz and nothing else. It does not parse, decode,
or validate the XML beyond a byte-level check that the body is an XML document of the
expected type — parsing logic will change many times, and re-downloading a month of data
every time it does is the mistake this script exists to avoid.

Design, in the order it matters:

  Pacing. A single monotonic "next allowed" timestamp, measured from request start.
  Requests never overlap (one thread, one connection), so the rate is provable rather
  than emergent, and idle time cannot accumulate into a burst the way it would with a
  token bucket of capacity > 1.

  Adaptive rate (AIMD). The interval is state, not a constant. A 429/503 doubles it up
  to a ceiling; sustained success walks it back down in small steps. Backing off without
  recovering turns a 2.5-hour job into an overnight one after one transient 503;
  recovering instantly re-trips whatever throttle fired.

  Retries, in three classes that must not be collapsed into one loop:
    transport  (timeout, connection reset)  -> exponential backoff with full jitter.
                                               The network flaked; the server did not
                                               complain, so the global pace is unchanged.
    throttled  (429, 503, 502, 504)         -> NOT a retry-harder case. Widen the global
                                               pace, honour Retry-After, retry patiently.
    permanent  (other 4xx)                  -> zero retries. Log it and move on.

  Resumability. Skip is a plain filesystem check. Every file is written to .tmp and
  atomically replaced, so "the file exists" implies "the file is complete" — without
  that, a Ctrl-C mid-write leaves a truncated file that the skip check then treats as
  done, and the corruption only surfaces at parse time weeks later.

Iteration is date-major, oldest first: each day completes before the next begins, so an
interrupted run leaves whole usable days rather than a uniformly sparse range.

Usage (PowerShell, from the repo root, venv active):

    python src\\backfill.py --start 2026-06-25 --end 2026-07-24
    python src\\backfill.py --start 2026-06-25 --end 2026-07-24 --dry-run
    python src\\backfill.py --retry-failures
    python src\\backfill.py --start ... --end ... --rate 1.0   # be gentler

Note: the 2 req/s politeness budget is per-host, not per-script. Running this alongside
harvest_codes.py puts you above the rate you think you set.
"""

import argparse
import gzip
import json
import os
import random
import statistics
import sys
import time
from collections import deque
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

BASE = "http://api.irishrail.ie/realtime/realtime.asmx"
USER_AGENT = "rail-delay/0.1 (research project; throttled bulk fetch)"

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CODES = REPO_ROOT / "data" / "codes.json"
DEFAULT_OUT = REPO_ROOT / "data" / "raw"
DEFAULT_FAILURE_LOG = REPO_ROOT / "data" / "logs" / "backfill_failures.jsonl"

TIMEOUT = 20
THROTTLE_CODES = {429, 502, 503, 504}

MAX_TRANSPORT_ATTEMPTS = 5   # per item, network-level
MAX_THROTTLE_ATTEMPTS = 8    # per item, server-signalled load
MAX_BADBODY_ATTEMPTS = 3     # per item, 200 OK with a body that isn't our XML

BACKOFF_BASE = 1.0           # seconds; attempt N sleeps random(0, BASE * 2**(N-1))
BACKOFF_CEILING = 60.0

PACE_CEILING = 30.0          # seconds between requests, worst case
PACE_RECOVER_AFTER = 50      # consecutive successes before easing the pace back
PACE_RECOVER_STEP = 0.05     # seconds shaved per recovery step

# The API wants '25 jul 2026'. Built explicitly rather than with %b, which is
# locale-dependent and would silently emit e.g. 'juil' on a French-locale machine.
MONTHS = ("jan", "feb", "mar", "apr", "may", "jun",
          "jul", "aug", "sep", "oct", "nov", "dec")


class Failure(Exception):
    """An item we are giving up on. `kind` is one of transport/throttled/permanent/badbody."""

    def __init__(self, kind: str, detail: str, status: int | None = None):
        super().__init__(f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail
        self.status = status


# ------------------------------------------------------------------- the pacer

class Pacer:
    """Serial request pacer with additive-decrease / multiplicative-increase interval."""

    def __init__(self, rate: float):
        self.base = 1.0 / rate
        self.interval = self.base
        self._next_allowed = time.monotonic()
        self._consecutive_ok = 0
        self.throttle_events = 0

    def wait(self) -> None:
        remaining = self._next_allowed - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)
        # Measured from request START: a request that itself takes longer than the
        # interval costs no additional sleep, so slow responses self-limit us.
        self._next_allowed = time.monotonic() + self.interval

    def slow_down(self) -> None:
        self.throttle_events += 1
        self._consecutive_ok = 0
        self.interval = min(self.interval * 2, PACE_CEILING)

    def record_success(self) -> None:
        self._consecutive_ok += 1
        if self._consecutive_ok >= PACE_RECOVER_AFTER and self.interval > self.base:
            self.interval = max(self.base, self.interval - PACE_RECOVER_STEP)
            self._consecutive_ok = 0


# ------------------------------------------------------------------ fetch layer

def looks_like_movements_xml(body: bytes) -> bool:
    """Byte-level sanity check. Not parsing — no decode, no ElementTree.

    ASMX services return 200 with an HTML error page often enough that without this
    you end up archiving thousands of error pages under .xml.gz names and only find
    out at parse time. Matches both ArrayOfObjTrainMovements and objTrainMovements.
    """
    head = body[:4096].lstrip(b"\xef\xbb\xbf")  # a UTF-8 BOM is legal here
    return head.startswith(b"<?xml") and b"objtrainmovements" in head.lower()


def jittered_backoff(attempt: int) -> float:
    """Full jitter: sleep uniformly in [0, cap). Decorrelates retries; free to add."""
    return random.uniform(0, min(BACKOFF_CEILING, BACKOFF_BASE * (2 ** (attempt - 1))))


def retry_after_seconds(response: requests.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return max(0.0, float(raw.strip()))
    except ValueError:
        return None  # HTTP-date form; rare here, and our own backoff covers it


def fetch(session: requests.Session, code: str, api_date: str, pacer: Pacer) -> tuple[bytes, int, float]:
    """One (code, date) fetch with the full retry policy. Raises Failure to give up."""
    url = f"{BASE}/getTrainMovementsXML"
    params = {"TrainId": code, "TrainDate": api_date}
    transport = throttled = badbody = 0

    while True:
        pacer.wait()
        started = time.monotonic()
        try:
            r = session.get(url, params=params, timeout=TIMEOUT)
        except requests.RequestException as e:
            transport += 1
            if transport >= MAX_TRANSPORT_ATTEMPTS:
                raise Failure("transport", f"{type(e).__name__}: {e}")
            time.sleep(jittered_backoff(transport))
            continue

        elapsed = time.monotonic() - started

        if r.status_code in THROTTLE_CODES:
            throttled += 1
            pacer.slow_down()
            if throttled >= MAX_THROTTLE_ATTEMPTS:
                raise Failure("throttled", f"still HTTP {r.status_code} after "
                                           f"{throttled} attempts", r.status_code)
            wait = retry_after_seconds(r)
            time.sleep(wait if wait is not None else pacer.interval)
            continue

        if r.status_code >= 400:
            raise Failure("permanent", f"HTTP {r.status_code}", r.status_code)

        body = r.content
        if not looks_like_movements_xml(body):
            badbody += 1
            if badbody >= MAX_BADBODY_ATTEMPTS:
                raise Failure("badbody", f"HTTP {r.status_code}, {len(body)} bytes, "
                                         f"not movements XML", r.status_code)
            time.sleep(jittered_backoff(badbody))
            continue

        pacer.record_success()
        return body, r.status_code, elapsed


# -------------------------------------------------------------------- disk I/O

def write_gz(dest: Path, body: bytes) -> None:
    """Atomic: complete file or no file. See the module docstring on why this matters."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    with gzip.open(tmp, "wb") as z:
        z.write(body)
    os.replace(tmp, dest)


def append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")


def load_codes(path: Path) -> list[str]:
    """Accept harvest_codes.py state, a bare {code: ...} map, or a plain list."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, dict):
        codes = data.get("codes", data)
        if isinstance(codes, dict):
            codes = list(codes.keys())
    else:
        codes = data

    clean = sorted({str(c).strip().upper() for c in codes if str(c).strip()})
    # Codes become filenames. Anything with a separator in it is either a bug or
    # hostile; either way it does not get to choose a write path.
    safe = [c for c in clean if c.replace("_", "").replace("-", "").isalnum()]
    if len(safe) != len(clean):
        print(f"  ! dropped {len(clean) - len(safe)} codes with unsafe characters")
    return safe


# ------------------------------------------------------------------- formatting

def api_date(d: date) -> str:
    return f"{d.day:02d} {MONTHS[d.month - 1]} {d.year}"


def daterange(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def fmt_duration(seconds: float) -> str:
    seconds = int(max(0, seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


# ------------------------------------------------------------------------ main

def run(pairs: list[tuple[date, str]], out_dir: Path, pacer: Pacer,
        failure_log: Path, write_manifest: bool) -> dict:
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    total = len(pairs)
    stats = {"ok": 0, "failed": 0, "bytes": 0}
    item_times = deque(maxlen=200)  # rolling, for the ETA
    run_started = time.monotonic()
    current_day = None

    for i, (d, code) in enumerate(pairs, start=1):
        day = d.isoformat()
        if day != current_day:
            current_day = day
            print(f"\n-- {day} ({api_date(d)})")

        item_started = time.monotonic()
        dest = out_dir / day / f"{code}.xml.gz"

        try:
            body, status, elapsed = fetch(session, code, api_date(d), pacer)
        except Failure as f:
            stats["failed"] += 1
            append_jsonl(failure_log, {
                "ts": datetime.now().isoformat(timespec="seconds"),
                "date": day, "code": code,
                "kind": f.kind, "status": f.status, "detail": f.detail,
            })
            print(f"  ! {code} {f.kind}: {f.detail}")
        else:
            write_gz(dest, body)
            stats["ok"] += 1
            stats["bytes"] += len(body)
            if write_manifest:
                # Written after the file lands. The filesystem is the source of truth
                # for resume; the manifest is a survey aid, so a lost line is harmless.
                append_jsonl(out_dir / day / "_manifest.jsonl", {
                    "code": code, "status": status, "bytes": len(body),
                    "elapsed_s": round(elapsed, 3),
                    "ts": datetime.now().isoformat(timespec="seconds"),
                })

        item_times.append(time.monotonic() - item_started)

        if i % 25 == 0 or i == total:
            # Median, not mean: one 60-second backoff poisons a mean permanently.
            per_item = statistics.median(item_times)
            remaining = (total - i) * per_item
            elapsed_total = time.monotonic() - run_started
            print(f"  [{i}/{total}] {100 * i / total:5.1f}%  "
                  f"ok={stats['ok']} fail={stats['failed']}  "
                  f"{per_item:.2f}s/req  pace={pacer.interval:.2f}s  "
                  f"elapsed {fmt_duration(elapsed_total)}  "
                  f"ETA {fmt_duration(remaining)}")

    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", type=date.fromisoformat, help="first date, YYYY-MM-DD (inclusive)")
    ap.add_argument("--end", type=date.fromisoformat, help="last date, YYYY-MM-DD (inclusive)")
    ap.add_argument("--codes", type=Path, default=DEFAULT_CODES,
                    help=f"code list from harvest_codes.py (default: {DEFAULT_CODES})")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT,
                    help=f"raw output root (default: {DEFAULT_OUT})")
    ap.add_argument("--rate", type=float, default=2.0,
                    help="requests per second, before any adaptive slowdown (default: 2.0)")
    ap.add_argument("--failure-log", type=Path, default=DEFAULT_FAILURE_LOG,
                    help=f"JSONL failure log (default: {DEFAULT_FAILURE_LOG})")
    ap.add_argument("--no-manifest", action="store_true",
                    help="skip the per-day _manifest.jsonl")
    ap.add_argument("--retry-failures", action="store_true",
                    help="re-fetch the pairs in the failure log instead of a date range")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be fetched and exit without requesting anything")
    args = ap.parse_args()

    if args.rate <= 0:
        print("--rate must be positive")
        return 2

    # ---- build the work list
    if args.retry_failures:
        if not args.failure_log.exists():
            print(f"no failure log at {args.failure_log} — nothing to retry")
            return 0
        seen, pairs = set(), []
        with open(args.failure_log, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    key = (rec["date"], rec["code"])
                except (json.JSONDecodeError, KeyError):
                    continue
                if key not in seen:
                    seen.add(key)
                    pairs.append((date.fromisoformat(rec["date"]), rec["code"]))
        print(f"retrying {len(pairs)} failed pairs from {args.failure_log.name}")
        # Rotate: failures from this pass start a clean log, so a repeated
        # --retry-failures does not re-walk items that have since succeeded.
        if pairs and not args.dry_run:
            rotated = args.failure_log.with_name(
                args.failure_log.name + f".{int(time.time())}.bak")
            os.replace(args.failure_log, rotated)
            print(f"  previous log rotated to {rotated.name}")
    else:
        if not args.start or not args.end:
            print("--start and --end are required (or use --retry-failures)")
            return 2
        if args.end < args.start:
            print("--end is before --start")
            return 2
        if not args.codes.exists():
            print(f"no code list at {args.codes} — run harvest_codes.py first")
            return 2

        codes = load_codes(args.codes)
        if not codes:
            print(f"{args.codes} contains no usable codes")
            return 2
        days = list(daterange(args.start, args.end))
        # Date-major, oldest first: whole days complete before the next one starts.
        pairs = [(d, c) for d in days for c in codes]
        print(f"backfill — {len(days)} dates x {len(codes)} codes = {len(pairs)} pairs")

    # ---- drop what is already on disk
    todo = [(d, c) for d, c in pairs if not (args.out / d.isoformat() / f"{c}.xml.gz").exists()]
    already = len(pairs) - len(todo)
    if already:
        print(f"  {already} already on disk, skipping")
    if not todo:
        print("nothing to do — the range is complete")
        return 0

    est = len(todo) / args.rate
    print(f"  {len(todo)} to fetch at {args.rate:g} req/s -> ~{fmt_duration(est)} "
          f"if nothing throttles")

    if args.dry_run:
        print("\ndry run — no requests made")
        return 0

    print(f"  failures -> {args.failure_log}")
    print("  Ctrl-C is safe; completed files are never partial\n")

    pacer = Pacer(args.rate)
    started = time.monotonic()
    interrupted = False
    try:
        stats = run(todo, args.out, pacer, args.failure_log, not args.no_manifest)
    except KeyboardInterrupt:
        interrupted = True
        stats = None
        print("\n\ninterrupted — rerun the same command to resume where this stopped")

    if stats:
        elapsed = time.monotonic() - started
        mb = stats["bytes"] / (1024 * 1024)
        print(f"\ndone in {fmt_duration(elapsed)}: {stats['ok']} fetched "
              f"({mb:.1f} MB uncompressed), {stats['failed']} failed, "
              f"{pacer.throttle_events} throttle events")
        if stats["failed"]:
            print(f"  retry them with:  python src\\backfill.py --retry-failures")

    return 130 if interrupted else 0


if __name__ == "__main__":
    sys.exit(main())
