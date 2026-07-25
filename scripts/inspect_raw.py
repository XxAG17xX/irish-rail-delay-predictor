"""
inspect_raw.py — read-only survey of what is actually sitting in data/raw/.

Answers the questions you want before writing a parser: how much did we get, how much of
it is the empty envelope a train returns for a date it did not run, and on a sample of
the real files, how often is there an actual arrival time — and how often does that
arrival differ from the scheduled one.

That last column is the one that matters. Irish Rail documents that on several lines
"your query will return the scheduled time only", which means Arrival can hold the
timetable presented as an observation. A file where every arrival exactly equals its
schedule is more likely echoing than reporting. See docs/data-dictionary.md section 5.1.

This script never writes, moves, or deletes anything. It opens files for reading only.

Usage (PowerShell, from the repo root, venv active):

    python scripts\\inspect_raw.py                                  # every date present
    python scripts\\inspect_raw.py --start 2026-06-25 --end 2026-07-24
    python scripts\\inspect_raw.py --sample 20                      # parse more per date
    python scripts\\inspect_raw.py --sample 0                       # sizes only, no parsing
"""

import argparse
import gzip
import random
import re
import statistics
import struct
import sys
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

NS = "{http://api.irishrail.ie/realtime/}"
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RAW = REPO_ROOT / "data" / "raw"

DATE_DIR = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# The empty response is a self-closing <ArrayOfObjTrainMovements/> at 209 bytes.
# The threshold is deliberately loose — a single movement record is ~500 bytes on
# its own, so there is a wide gap between "empty" and "one stop".
DEFAULT_EMPTY_MAX = 400


def uncompressed_size(path: Path) -> int | None:
    """Read the gzip ISIZE trailer instead of decompressing.

    The last 4 bytes of a gzip member are the uncompressed length mod 2**32. Our files
    are single-member and far below 4 GB, so this is exact and costs one seek per file
    rather than a full inflate of every archive on disk.
    """
    try:
        with open(path, "rb") as f:
            if path.stat().st_size < 4:
                return None
            f.seek(-4, 2)
            return struct.unpack("<I", f.read(4))[0]
    except OSError:
        return None


def read_body(path: Path) -> bytes | None:
    try:
        with gzip.open(path, "rb") as z:
            return z.read()
    except (OSError, EOFError):
        return None


def text(node, name: str) -> str:
    el = node.find(NS + name)
    return (el.text or "").strip() if el is not None else ""


def inspect_file(path: Path) -> dict | None:
    """Parse one archived response. Returns None if it cannot be read or parsed."""
    body = read_body(path)
    if body is None:
        return None
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return None

    records = root.findall(NS + "objTrainMovements")
    with_arrival = comparable = differing = 0

    for r in records:
        arrival = text(r, "Arrival")
        if not arrival:
            continue
        with_arrival += 1

        scheduled = text(r, "ScheduledArrival")
        # 00:00:00 is structurally absent, not missing — an origin has no scheduled
        # arrival. Comparing against it would manufacture a spurious difference.
        if not scheduled or scheduled == "00:00:00":
            continue
        comparable += 1
        if arrival != scheduled:
            differing += 1

    return {
        "records": len(records),
        "with_arrival": with_arrival,
        "comparable": comparable,
        "differing": differing,
    }


def survey_date(day_dir: Path, sample_n: int, rng: random.Random,
                empty_max: int) -> dict:
    files = sorted(day_dir.glob("*.xml.gz"))
    strays = list(day_dir.glob("*.tmp"))

    gz_bytes = 0
    sizes = []          # uncompressed, for the median
    empties = []        # exact uncompressed sizes seen below the threshold
    unreadable = 0
    non_empty = []

    for f in files:
        try:
            gz_bytes += f.stat().st_size
        except OSError:
            unreadable += 1
            continue
        size = uncompressed_size(f)
        if size is None:
            unreadable += 1
            continue
        sizes.append(size)
        if size <= empty_max:
            empties.append(size)
        else:
            non_empty.append(f)

    agg = {"sampled": 0, "records": 0, "with_arrival": 0,
           "comparable": 0, "differing": 0, "unparseable": 0}

    if sample_n and non_empty:
        chosen = rng.sample(non_empty, min(sample_n, len(non_empty)))
        for f in chosen:
            result = inspect_file(f)
            if result is None:
                agg["unparseable"] += 1
                continue
            agg["sampled"] += 1
            for k in ("records", "with_arrival", "comparable", "differing"):
                agg[k] += result[k]

    return {
        "files": len(files),
        "empty": len(empties),
        "empty_sizes": sorted(set(empties)),
        "non_empty": len(non_empty),
        "unreadable": unreadable,
        "strays": len(strays),
        "gz_bytes": gz_bytes,
        "median_size": statistics.median(sizes) if sizes else 0,
        **agg,
    }


def pct(part: int, whole: int) -> str:
    return f"{100 * part / whole:.1f}%" if whole else "  -  "


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw", type=Path, default=DEFAULT_RAW,
                    help=f"raw archive root (default: {DEFAULT_RAW})")
    ap.add_argument("--start", type=date.fromisoformat, help="first date, YYYY-MM-DD")
    ap.add_argument("--end", type=date.fromisoformat, help="last date, YYYY-MM-DD")
    ap.add_argument("--sample", type=int, default=5,
                    help="non-empty files to parse per date; 0 = sizes only (default: 5)")
    ap.add_argument("--seed", type=int, default=0,
                    help="RNG seed for the sample, so runs are comparable (default: 0)")
    ap.add_argument("--empty-max-bytes", type=int, default=DEFAULT_EMPTY_MAX,
                    help=f"uncompressed size at or below which a file is an empty "
                         f"envelope (default: {DEFAULT_EMPTY_MAX})")
    args = ap.parse_args()

    if not args.raw.exists():
        print(f"no raw archive at {args.raw}")
        return 2

    days = sorted(d for d in args.raw.iterdir()
                  if d.is_dir() and DATE_DIR.match(d.name))
    if args.start:
        days = [d for d in days if date.fromisoformat(d.name) >= args.start]
    if args.end:
        days = [d for d in days if date.fromisoformat(d.name) <= args.end]

    if not days:
        print(f"no date directories in {args.raw} matching the range")
        return 0

    rng = random.Random(args.seed)
    print(f"inspecting {len(days)} dates under {args.raw}")
    print(f"  empty threshold {args.empty_max_bytes} bytes uncompressed, "
          f"sample {args.sample}/date, seed {args.seed}\n")

    header = (f"{'date':11} {'files':>6} {'empty':>6} {'real':>6} {'gzMB':>7} "
              f"{'medKB':>7} | {'smp':>4} {'recs':>6} {'arr':>6} {'cmp':>6} "
              f"{'diff':>6} {'diff%':>7}")
    print(header)
    print("-" * len(header))

    totals = {k: 0 for k in ("files", "empty", "non_empty", "unreadable", "strays",
                             "gz_bytes", "sampled", "records", "with_arrival",
                             "comparable", "differing", "unparseable")}
    all_empty_sizes = set()

    for day_dir in days:
        s = survey_date(day_dir, args.sample, rng, args.empty_max_bytes)
        for k in totals:
            totals[k] += s[k]
        all_empty_sizes |= set(s["empty_sizes"])

        print(f"{day_dir.name:11} {s['files']:6} {s['empty']:6} {s['non_empty']:6} "
              f"{s['gz_bytes'] / 1048576:7.2f} {s['median_size'] / 1024:7.1f} | "
              f"{s['sampled']:4} {s['records']:6} {s['with_arrival']:6} "
              f"{s['comparable']:6} {s['differing']:6} "
              f"{pct(s['differing'], s['comparable']):>7}")

    print("-" * len(header))
    print(f"{'TOTAL':11} {totals['files']:6} {totals['empty']:6} {totals['non_empty']:6} "
          f"{totals['gz_bytes'] / 1048576:7.2f} {'':7} | "
          f"{totals['sampled']:4} {totals['records']:6} {totals['with_arrival']:6} "
          f"{totals['comparable']:6} {totals['differing']:6} "
          f"{pct(totals['differing'], totals['comparable']):>7}")

    print("\ncolumns: real = non-empty files. smp = files parsed. recs = movement records")
    print("         in those. arr = records with a populated Arrival. cmp = of those, ones")
    print("         with a real ScheduledArrival to compare against (00:00:00 at an origin")
    print("         is structurally absent, not missing). diff = arrival differs from")
    print("         schedule, i.e. an observation rather than a possible echo.")

    if all_empty_sizes:
        sizes = ", ".join(str(s) for s in sorted(all_empty_sizes))
        print(f"\nempty envelope sizes seen: {sizes} bytes uncompressed")
        if len(all_empty_sizes) > 1:
            print("  more than one distinct size — check these are all genuinely empty")

    if totals["files"]:
        print(f"\n{pct(totals['empty'], totals['files'])} of files are empty envelopes "
              f"(a code that did not run that day)")
    if totals["comparable"]:
        exact = totals["comparable"] - totals["differing"]
        print(f"{pct(exact, totals['comparable'])} of comparable arrivals exactly equal "
              f"their schedule.")
        print("  This is the headline label-quality number. It is an aggregate across all")
        print("  lines and proves nothing on its own — the real test is per-line, comparing")
        print("  flagged lines against well-covered ones. See data-dictionary.md 5.1.")

    if totals["unreadable"]:
        print(f"\n! {totals['unreadable']} files unreadable or truncated")
    if totals["unparseable"]:
        print(f"! {totals['unparseable']} sampled files were not parseable XML")
    if totals["strays"]:
        print(f"! {totals['strays']} stray .tmp files — an interrupted write. Safe to")
        print("  delete; the atomic-replace design means they are never live data.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
