"""
coverage_by_location.py — per-location arrival coverage and echo signature.

This is the analysis data-dictionary.md section 5.1 flags as the highest-priority open
question. Irish Rail documents that on several lines "your query will return the
scheduled time only", which means the Arrival field can hold the timetable presented as
an observation. That is far more dangerous than a null: the field is populated, the value
is plausible, and no naive validity check flags it. Left alone it teaches a model that
those lines are perfectly punctual.

The test: for every location, what fraction of actual arrivals exactly equal the
scheduled arrival. A genuine observation almost never lands on the scheduled second. A
high exact-match rate is the echo signature. The comparison that matters is flagged lines
against well-covered ones, not the absolute number.

Reads every file in the archive — no sampling. Writes nothing.

Usage (PowerShell, from the repo root, venv active):

    python scripts\\coverage_by_location.py
    python scripts\\coverage_by_location.py --start 2026-06-25 --end 2026-07-24
    python scripts\\coverage_by_location.py --min-comparable 100 --top 40
"""

import argparse
import gzip
import re
import sys
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import date
from pathlib import Path

NS = "{http://api.irishrail.ie/realtime/}"
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RAW = REPO_ROOT / "data" / "raw"

DATE_DIR = re.compile(r"^\d{4}-\d{2}-\d{2}$")

PLACEHOLDER_TIMES = {"", "00:00:00"}

# Lines documented as having weak real-time coverage — data-dictionary.md 5.1.
# Matched against LocationFullName on a word boundary, NOT as a plain substring:
# 'Ballina' is a prefix of 'Ballinasloe' (Dublin-Galway line, not flagged) and
# 'Ennis' is a prefix of 'Enniscorthy'. A substring match would silently mislabel both.
FLAG_KEYWORDS = ["cork", "cobh", "midleton", "tralee", "ennis",
                 "rosslare", "belfast", "westport", "ballina", "athlone"]
FLAG_RE = re.compile(r"\b(" + "|".join(FLAG_KEYWORDS) + r")\b", re.IGNORECASE)

# LocationType: O origin, S stop, D destination, T non-stopping timing point.
PASSENGER_TYPES = {"O", "S", "D"}


class Counter:
    __slots__ = ("records", "arrivals", "comparable", "exact", "names", "types")

    def __init__(self):
        self.records = 0
        self.arrivals = 0      # Arrival populated
        self.comparable = 0    # Arrival populated AND a real ScheduledArrival to compare
        self.exact = 0         # of comparable, Arrival == ScheduledArrival
        self.names = defaultdict(int)
        self.types = defaultdict(int)

    def add(self, other):
        self.records += other.records
        self.arrivals += other.arrivals
        self.comparable += other.comparable
        self.exact += other.exact

    @property
    def name(self) -> str:
        if not self.names:
            return ""
        return max(self.names.items(), key=lambda kv: kv[1])[0]

    @property
    def dominant_type(self) -> str:
        if not self.types:
            return "?"
        return max(self.types.items(), key=lambda kv: kv[1])[0]


def text(node, tag: str) -> str:
    el = node.find(NS + tag)
    return (el.text or "").strip() if el is not None else ""


def pct(part: int, whole: int) -> str:
    return f"{100 * part / whole:5.1f}%" if whole else "    -"


def fmt_duration(seconds: float) -> str:
    seconds = int(max(0, seconds))
    m, s = divmod(seconds, 60)
    return f"{m}m{s:02d}s" if m else f"{s}s"


def collect_files(raw: Path, start: date | None, end: date | None) -> list[Path]:
    days = sorted(d for d in raw.iterdir() if d.is_dir() and DATE_DIR.match(d.name))
    if start:
        days = [d for d in days if date.fromisoformat(d.name) >= start]
    if end:
        days = [d for d in days if date.fromisoformat(d.name) <= end]
    files = []
    for d in days:
        files.extend(sorted(d.glob("*.xml.gz")))
    return files


def scan(files: list[Path], progress_every: int):
    """Parse every file. Returns (per-location counters, by-type counters, error tallies)."""
    by_location = defaultdict(Counter)
    by_type = defaultdict(Counter)        # keyed by raw LocationType
    unreadable = unparseable = 0
    started = time.monotonic()

    for i, path in enumerate(files, start=1):
        try:
            with gzip.open(path, "rb") as z:
                body = z.read()
        except (OSError, EOFError):
            unreadable += 1
            continue

        try:
            root = ET.fromstring(body)
        except ET.ParseError:
            unparseable += 1
            continue

        for rec in root.findall(NS + "objTrainMovements"):
            code = text(rec, "LocationCode").upper()
            if not code:
                continue

            loc_type = text(rec, "LocationType").upper() or "?"
            arrival = text(rec, "Arrival")
            scheduled = text(rec, "ScheduledArrival")

            c = by_location[code]
            t = by_type[loc_type]
            c.records += 1
            t.records += 1
            c.types[loc_type] += 1
            name = text(rec, "LocationFullName")
            if name:
                c.names[name] += 1

            if arrival in PLACEHOLDER_TIMES:
                continue
            c.arrivals += 1
            t.arrivals += 1

            # 00:00:00 scheduled means structurally absent (an origin has no scheduled
            # arrival), not missing. Comparing against it would manufacture a difference.
            if scheduled in PLACEHOLDER_TIMES:
                continue
            c.comparable += 1
            t.comparable += 1
            if arrival == scheduled:
                c.exact += 1
                t.exact += 1

        if i % progress_every == 0 or i == len(files):
            elapsed = time.monotonic() - started
            rate = i / elapsed if elapsed else 0
            eta = (len(files) - i) / rate if rate else 0
            print(f"  {i}/{len(files)} files  {100 * i / len(files):5.1f}%  "
                  f"{rate:.0f} files/s  elapsed {fmt_duration(elapsed)}  "
                  f"ETA {fmt_duration(eta)}", flush=True)

    return by_location, by_type, unreadable, unparseable


def is_flagged(name: str) -> bool:
    return bool(name) and bool(FLAG_RE.search(name))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw", type=Path, default=DEFAULT_RAW,
                    help=f"raw archive root (default: {DEFAULT_RAW})")
    ap.add_argument("--start", type=date.fromisoformat, help="first date, YYYY-MM-DD")
    ap.add_argument("--end", type=date.fromisoformat, help="last date, YYYY-MM-DD")
    ap.add_argument("--min-comparable", type=int, default=30,
                    help="locations with fewer comparable arrivals are held out of the "
                         "ranked table, since a percentage over 3 records is noise "
                         "(default: 30)")
    ap.add_argument("--top", type=int, default=0,
                    help="show only the top N rows; 0 = all (default: 0)")
    ap.add_argument("--progress-every", type=int, default=2000,
                    help="print progress every N files (default: 2000)")
    args = ap.parse_args()

    if not args.raw.exists():
        print(f"no raw archive at {args.raw}")
        return 2

    files = collect_files(args.raw, args.start, args.end)
    if not files:
        print(f"no files under {args.raw} matching the range")
        return 0

    print(f"coverage_by_location — reading all {len(files)} files under {args.raw}")
    print("  this parses every record; no sampling\n")

    by_location, by_type, unreadable, unparseable = scan(files, args.progress_every)

    if not by_location:
        print("\nno movement records found")
        return 0

    # ---- per-location table
    rows = []
    for code, c in by_location.items():
        rows.append((code, c))
    # Undefined exact% (no comparable arrivals at all) sorts last, not first.
    rows.sort(key=lambda kv: (kv[1].exact / kv[1].comparable) if kv[1].comparable else -1,
              reverse=True)

    shown = [(code, c) for code, c in rows if c.comparable >= args.min_comparable]
    held = len(rows) - len(shown)
    if args.top:
        shown = shown[:args.top]

    header = (f"{'code':<7} {'name':<26} {'typ':<4} {'records':>8} {'arr%':>7} "
              f"{'cmp':>8} {'exact%':>7}  flag")
    print(f"\n{'=' * len(header)}")
    print("PER-LOCATION, sorted by exact-match % descending")
    print("=" * len(header))
    print(header)
    print("-" * len(header))

    for code, c in shown:
        name = c.name or "(no name in feed)"
        print(f"{code:<7} {name[:26]:<26} {c.dominant_type:<4} {c.records:>8} "
              f"{pct(c.arrivals, c.records):>7} {c.comparable:>8} "
              f"{pct(c.exact, c.comparable):>7}  "
              f"{'FLAG' if is_flagged(c.name) else ''}")

    if held:
        print(f"\n  {held} locations held out of the table with fewer than "
              f"{args.min_comparable} comparable arrivals.")
        print("  They are still counted in every summary below.")

    # ---- flagged vs unflagged
    flagged, unflagged, unnamed = Counter(), Counter(), Counter()
    matched_names = defaultdict(set)
    for code, c in by_location.items():
        if not c.name:
            unnamed.add(c)
        elif is_flagged(c.name):
            flagged.add(c)
            m = FLAG_RE.search(c.name)
            matched_names[m.group(1).lower()].add(c.name)
        else:
            unflagged.add(c)

    print(f"\n{'=' * len(header)}")
    print("FLAGGED vs UNFLAGGED LOCATIONS (data-dictionary 5.1)")
    print("=" * len(header))
    print(f"{'group':<22} {'locations':>10} {'records':>10} {'arr%':>8} "
          f"{'cmp':>10} {'exact%':>8}")
    print("-" * len(header))
    groups = [
        ("flagged lines", flagged, sum(1 for c in by_location.values() if is_flagged(c.name))),
        ("unflagged", unflagged, sum(1 for c in by_location.values()
                                     if c.name and not is_flagged(c.name))),
        ("unnamed in feed", unnamed, sum(1 for c in by_location.values() if not c.name)),
    ]
    for label, c, n_locs in groups:
        print(f"{label:<22} {n_locs:>10} {c.records:>10} "
              f"{pct(c.arrivals, c.records):>8} {c.comparable:>10} "
              f"{pct(c.exact, c.comparable):>8}")

    if flagged.comparable and unflagged.comparable:
        f_rate = 100 * flagged.exact / flagged.comparable
        u_rate = 100 * unflagged.exact / unflagged.comparable
        gap = f_rate - u_rate
        print(f"\n  exact-match rate: flagged {f_rate:.1f}% vs unflagged {u_rate:.1f}% "
              f"({gap:+.1f} points)")
        if gap > 10:
            print("  -> Flagged lines echo the schedule materially more often. This supports")
            print("     treating exact matches there as MISSING rather than observed.")
        elif gap < -10:
            print("  -> Flagged lines echo LESS than unflagged. Unexpected — check the")
            print("     keyword matching below before drawing any conclusion.")
        else:
            print("  -> No material difference in this archive. That is a real result, but")
            print("     check the record counts: a thin or Dublin-heavy code list may simply")
            print("     not cover the flagged lines enough to say anything.")

    # ---- which names actually matched, so the heuristic is checkable
    print("\n  keyword matches (verify these are the right locations):")
    for kw in FLAG_KEYWORDS:
        names = sorted(matched_names.get(kw, []))
        print(f"    {kw:<10} {', '.join(names) if names else '(no location matched)'}")

    # ---- by LocationType
    print(f"\n{'=' * len(header)}")
    print("BY LOCATION TYPE — labels come from passenger stops, not timing points")
    print("=" * len(header))
    print(f"{'type':<22} {'records':>10} {'arr%':>8} {'cmp':>10} {'exact%':>8}")
    print("-" * len(header))

    type_labels = {"O": "O origin", "S": "S stop", "D": "D destination",
                   "T": "T timing point"}
    passenger, timing = Counter(), Counter()
    for t, c in sorted(by_type.items()):
        print(f"{type_labels.get(t, t + ' unknown'):<22} {c.records:>10} "
              f"{pct(c.arrivals, c.records):>8} {c.comparable:>10} "
              f"{pct(c.exact, c.comparable):>8}")
        (passenger if t in PASSENGER_TYPES else timing).add(c)

    print("-" * len(header))
    print(f"{'O/S/D passenger':<22} {passenger.records:>10} "
          f"{pct(passenger.arrivals, passenger.records):>8} {passenger.comparable:>10} "
          f"{pct(passenger.exact, passenger.comparable):>8}")
    print(f"{'T timing points':<22} {timing.records:>10} "
          f"{pct(timing.arrivals, timing.records):>8} {timing.comparable:>10} "
          f"{pct(timing.exact, timing.comparable):>8}")

    # ---- caveats
    print(f"\n{'=' * len(header)}")
    print("HOW TO READ THIS")
    print("=" * len(header))
    print("  arr%    share of movement records with a populated Arrival. Low here means")
    print("          the location is silent, which is a different problem from echoing.")
    print("  cmp     arrivals that also had a real ScheduledArrival to compare against.")
    print("          00:00:00 is structurally absent at an origin, so it is excluded.")
    print("  exact%  of those, how many matched the schedule to the second. High is the")
    print("          echo signature: a real observation rarely lands on the exact second.")
    print()
    print("  CAVEAT ON FLAGGING. data-dictionary 5.1 lists weakly-covered LINES; this")
    print("  script flags LOCATIONS whose name matches a keyword. Intermediate stops on a")
    print("  flagged line — Little Island or Carrigtwohill on the Cobh line, say — are NOT")
    print("  flagged, so the flagged group understates the affected set. Treat the")
    print("  comparison as indicative and confirm against a real line map before relying")
    print("  on it.")

    if unreadable:
        print(f"\n! {unreadable} files unreadable or truncated")
    if unparseable:
        print(f"! {unparseable} files were not parseable XML")

    return 0


if __name__ == "__main__":
    sys.exit(main())
