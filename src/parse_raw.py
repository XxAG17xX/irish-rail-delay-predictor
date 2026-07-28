"""
parse_raw.py — raw archived XML into a Parquet dataset partitioned by date.

Reads data/raw/{YYYY-MM-DD}/*.xml.gz, extracts every objTrainMovements record, and writes
data/parsed/date={YYYY-MM-DD}/part.parquet. data/raw/current/ is skipped — those are
getCurrentTrainsXML snapshots with a different shape.

Column naming carries provenance. **PascalCase columns are verbatim from Irish Rail.**
**snake_case columns are ours.** If a number in an analysis traces back to a PascalCase
column it came out of the feed; if it traces to snake_case, this file computed it and the
logic is below. The only liberty taken with feed values is stripping whitespace, which
data-dictionary.md section 3 instructs (TrainCode carries trailing spaces).

Nothing is dropped, imputed, corrected or excluded. Suspect records are flagged and kept.
The archive is expensive to reproduce and parsing logic changes; deletion here would be
irreversible in a way that re-parsing is not.

Derived columns
---------------
arrival_delay_sec, departure_delay_sec
    Actual minus scheduled, in seconds. Null when either side is absent. Journeys cross
    midnight, so a gap beyond +/-12h is treated as a wrap, not a half-day delay.

null_reason, departure_null_reason
    Why an actual is missing, per data-dictionary 5.2, computed per journey:
      structural  — no scheduled counterpart exists (an origin has no arrival, a
                    destination has no departure)
      future      — the location sits beyond the furthest point that reported anything
      unreported  — the train demonstrably passed and nothing was recorded
    Null when the actual is present. The spec asked for one `null_reason`; that column
    covers arrivals, which are the label. `departure_null_reason` is the same logic for
    departures, kept separate because the structural case differs by end of the journey.

    Edge case, stated because it is a real ambiguity: when a journey reports no actuals at
    all there is no furthest-reported point, so future and unreported cannot be told apart.
    Those records are marked `unreported`, which is right for a completed past date and may
    over-assign on a date still in progress. The run summary counts them separately.

is_exact_match
    Arrival exactly equals ScheduledArrival. Null when not comparable. An exact match is
    suspicious, not proof — the coincidence floor on machine-captured records is 2.37%.
    See label-quality.md.

AutoArrival / AutoDepart
    Kept verbatim as first-class columns. Three states matter: "1" auto-captured,
    "0" not, "" no corresponding actual. Storing as text preserves all three; a boolean
    would collapse the last two. This is the strongest label-quality signal in the feed.

LocationType is preserved exactly as recorded and must not be treated as a property of the
location — the same location is `S` on one record and `C` on another. See
data-dictionary.md section 6.

Idempotent: a date whose partition already exists is skipped. Partitions are written to a
temp file and atomically replaced, so a partition that exists is always complete.

Usage (PowerShell, from the repo root, venv active):

    python src\\parse_raw.py
    python src\\parse_raw.py --start 2026-06-27 --end 2026-07-24
    python src\\parse_raw.py --force
"""

import argparse
import gzip
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

NS = "{http://api.irishrail.ie/realtime/}"
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RAW = REPO_ROOT / "data" / "raw"
DEFAULT_OUT = REPO_ROOT / "data" / "parsed"

DATE_DIR = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PLACEHOLDER = {"", "00:00:00"}
HALF_DAY = 43200
FULL_DAY = 86400
QUANTUM = 6  # all feed times are 6-second quantised; see decisions.md D22

# Feed fields we expect. The real set is discovered from the archive at startup; this is
# the ordering preference and a check that nothing has silently appeared or vanished.
EXPECTED_FIELDS = [
    "TrainCode", "TrainDate", "LocationCode", "LocationFullName", "LocationOrder",
    "LocationType", "TrainOrigin", "TrainDestination", "ScheduledArrival",
    "ScheduledDeparture", "ExpectedArrival", "ExpectedDeparture", "Arrival",
    "Departure", "AutoArrival", "AutoDepart", "StopType",
]

DERIVED_SCHEMA = [
    ("arrival_delay_sec", pa.int32()),
    ("departure_delay_sec", pa.int32()),
    ("null_reason", pa.string()),
    ("departure_null_reason", pa.string()),
    ("is_exact_match", pa.bool_()),
    ("file_date", pa.string()),
    ("source_file", pa.string()),
]


def to_seconds(t: str):
    try:
        h, m, s = t.split(":")
        return int(h) * 3600 + int(m) * 60 + int(s)
    except (ValueError, AttributeError):
        return None


def delay_seconds(actual: str, scheduled: str):
    """Actual minus scheduled. None when either side is absent or unparseable."""
    if actual in PLACEHOLDER or scheduled in PLACEHOLDER:
        return None
    a, s = to_seconds(actual), to_seconds(scheduled)
    if a is None or s is None:
        return None
    d = a - s
    # A journey that crosses midnight makes a small delay look like a ~24h swing.
    if d > HALF_DAY:
        d -= FULL_DAY
    elif d < -HALF_DAY:
        d += FULL_DAY
    return d


def null_reason(actual, scheduled, order, last_reported):
    """Why is this actual missing? None when it is not missing."""
    if actual not in PLACEHOLDER:
        return None
    if scheduled in PLACEHOLDER:
        return "structural"
    if last_reported is None:
        # Nothing in the journey reported anything, so there is no furthest-reported
        # point and the two cases cannot be separated. See the module docstring.
        return "unreported"
    if order is None:
        return "unreported"
    return "future" if order > last_reported else "unreported"


def discover_fields(raw: Path, days: list[Path]) -> list[str]:
    """Union of tags across one file per date, so the schema is fixed before writing.

    An inferred-per-partition schema drifts: a column that is all-null in one date gets
    a null type and stops concatenating with the others.
    """
    seen = []
    for d in days:
        for f in sorted(d.glob("*.xml.gz"))[:1]:
            try:
                with gzip.open(f, "rb") as z:
                    root = ET.fromstring(z.read())
            except (OSError, EOFError, ET.ParseError):
                continue
            for rec in root.findall(NS + "objTrainMovements"):
                for child in rec:
                    tag = child.tag.replace(NS, "")
                    if tag not in seen:
                        seen.append(tag)
    ordered = [f for f in EXPECTED_FIELDS if f in seen]
    ordered += [f for f in seen if f not in EXPECTED_FIELDS]
    return ordered


def parse_file(path: Path, fields: list[str], stats: Counter, unknown: Counter):
    """One archived response into a list of row dicts. Empty list for an empty envelope."""
    try:
        with gzip.open(path, "rb") as z:
            body = z.read()
    except (OSError, EOFError):
        stats["files_unreadable"] += 1
        return []
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        stats["files_unparseable"] += 1
        return []

    records = root.findall(NS + "objTrainMovements")
    if not records:
        stats["files_empty"] += 1
        return []

    raw_rows = []
    for rec in records:
        row = {}
        for child in rec:
            tag = child.tag.replace(NS, "")
            if tag not in fields:
                unknown[tag] += 1
            row[tag] = (child.text or "").strip()
        raw_rows.append(row)

    # Furthest location that reported anything, for the future/unreported split.
    last_reported = None
    for row in raw_rows:
        try:
            order = int(row.get("LocationOrder", ""))
        except ValueError:
            continue
        if row.get("Arrival", "") not in PLACEHOLDER or \
           row.get("Departure", "") not in PLACEHOLDER:
            if last_reported is None or order > last_reported:
                last_reported = order
    if last_reported is None:
        stats["journeys_no_actuals"] += 1

    out = []
    for row in raw_rows:
        try:
            order = int(row.get("LocationOrder", ""))
        except ValueError:
            order = None

        arrival = row.get("Arrival", "")
        departure = row.get("Departure", "")
        sched_arr = row.get("ScheduledArrival", "")
        sched_dep = row.get("ScheduledDeparture", "")

        arr_delay = delay_seconds(arrival, sched_arr)
        dep_delay = delay_seconds(departure, sched_dep)

        if arrival not in PLACEHOLDER and sched_arr not in PLACEHOLDER:
            exact = arrival == sched_arr
        else:
            exact = None

        rec_out = {f: row.get(f) for f in fields}
        rec_out["LocationOrder"] = order
        rec_out["arrival_delay_sec"] = arr_delay
        rec_out["departure_delay_sec"] = dep_delay
        rec_out["null_reason"] = null_reason(arrival, sched_arr, order, last_reported)
        rec_out["departure_null_reason"] = null_reason(departure, sched_dep, order,
                                                       last_reported)
        rec_out["is_exact_match"] = exact
        out.append(rec_out)
    return out


def build_schema(fields: list[str]) -> pa.Schema:
    cols = []
    for f in fields:
        cols.append((f, pa.int32() if f == "LocationOrder" else pa.string()))
    cols.extend(DERIVED_SCHEMA)
    return pa.schema(cols)


def write_partition(rows: list[dict], schema: pa.Schema, dest_dir: Path) -> None:
    """Atomic write: temp file then os.replace, so an existing partition is complete."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "part.parquet"
    tmp = dest.with_suffix(".parquet.tmp")
    table = pa.Table.from_pylist(rows, schema=schema)
    pq.write_table(table, tmp, compression="zstd")
    os.replace(tmp, dest)


def fmt_duration(seconds: float) -> str:
    seconds = int(max(0, seconds))
    m, s = divmod(seconds, 60)
    return f"{m}m{s:02d}s" if m else f"{s}s"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw", type=Path, default=DEFAULT_RAW)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--start", type=date.fromisoformat, help="first date, YYYY-MM-DD")
    ap.add_argument("--end", type=date.fromisoformat, help="last date, YYYY-MM-DD")
    ap.add_argument("--force", action="store_true",
                    help="rewrite partitions that already exist")
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
        print(f"no date directories under {args.raw} matching the range")
        return 0

    fields = discover_fields(args.raw, days)
    if not fields:
        print("could not discover any fields — is the archive readable?")
        return 2

    missing = [f for f in EXPECTED_FIELDS if f not in fields]
    extra = [f for f in fields if f not in EXPECTED_FIELDS]
    print(f"parse_raw — {len(days)} dates, {len(fields)} feed fields")
    if missing:
        print(f"  ! expected but absent from the feed: {', '.join(missing)}")
    if extra:
        print(f"  ! present but not expected: {', '.join(extra)}")

    schema = build_schema(fields)
    todo = [d for d in days
            if args.force or not (args.out / f"date={d.name}" / "part.parquet").exists()]
    skipped = len(days) - len(todo)
    if skipped:
        print(f"  {skipped} partitions already written, skipping")
    if not todo:
        print("nothing to do — the range is already parsed")
        return 0
    print(f"  {len(todo)} partitions to write -> {args.out}\n")

    stats = Counter()
    unknown_tags = Counter()
    null_reasons = Counter()
    dep_null_reasons = Counter()
    violations = []
    started = time.monotonic()

    for i, day_dir in enumerate(todo, start=1):
        rows = []
        files = sorted(day_dir.glob("*.xml.gz"))
        for f in files:
            parsed = parse_file(f, fields, stats, unknown_tags)
            for r in parsed:
                r["file_date"] = day_dir.name
                r["source_file"] = f"{day_dir.name}/{f.name}"
            rows.extend(parsed)

        for r in rows:
            null_reasons[r["null_reason"]] += 1
            dep_null_reasons[r["departure_null_reason"]] += 1
            if r["is_exact_match"] is True:
                stats["exact_matches"] += 1
            for col in ("arrival_delay_sec", "departure_delay_sec"):
                v = r[col]
                if v is not None:
                    stats["delays_checked"] += 1
                    if v % QUANTUM != 0:
                        stats["delay_violations"] += 1
                        if len(violations) < 20:
                            violations.append((r["source_file"], r.get("LocationCode"),
                                               col, v))

        stats["records"] += len(rows)
        stats["files"] += len(files)
        write_partition(rows, schema, args.out / f"date={day_dir.name}")

        elapsed = time.monotonic() - started
        eta = (len(todo) - i) * (elapsed / i)
        print(f"  [{i}/{len(todo)}] {day_dir.name}  {len(files):>4} files  "
              f"{len(rows):>7} records  elapsed {fmt_duration(elapsed)}  "
              f"ETA {fmt_duration(eta)}", flush=True)

    W = 72
    print("\n" + "=" * W)
    print("SUMMARY")
    print("=" * W)
    print(f"  partitions written      {len(todo):>10}")
    print(f"  files read              {stats['files']:>10}")
    print(f"  records written         {stats['records']:>10}")
    print(f"  empty envelopes         {stats['files_empty']:>10}  "
          f"(train did not run that date)")
    print(f"  exact matches           {stats['exact_matches']:>10}")

    print("\n  null_reason (arrival):")
    for k, n in null_reasons.most_common():
        label = k if k is not None else "(arrival present)"
        print(f"    {label:<22} {n:>10}")
    print("  departure_null_reason:")
    for k, n in dep_null_reasons.most_common():
        label = k if k is not None else "(departure present)"
        print(f"    {label:<22} {n:>10}")
    if stats["journeys_no_actuals"]:
        print(f"\n  ! {stats['journeys_no_actuals']} journeys reported no actuals at all;")
        print("    their missing arrivals are marked 'unreported' because future and")
        print("    unreported cannot be separated without a furthest-reported point.")

    print("\n" + "-" * W)
    print("VALIDATION — every non-null delay should be divisible by 6 (decisions.md D22)")
    print("-" * W)
    print(f"  delays checked          {stats['delays_checked']:>10}")
    print(f"  not divisible by 6      {stats['delay_violations']:>10}")
    if violations:
        print("\n  first violations (reported, not excluded — the records are in the data):")
        for src, loc, col, v in violations:
            print(f"    {src:<28} {str(loc):<8} {col:<22} {v}")
    elif stats["delays_checked"]:
        print("  PASS — no violations.")

    if unknown_tags:
        print(f"\n  ! tags seen during parsing that were not in the discovered schema:")
        for t, n in unknown_tags.most_common():
            print(f"    {t:<24} {n:>10}  (NOT written — rerun with --force after adding)")
    if stats["files_unreadable"]:
        print(f"\n! {stats['files_unreadable']} files unreadable")
    if stats["files_unparseable"]:
        print(f"! {stats['files_unparseable']} files not parseable XML")

    print(f"\ndone in {fmt_duration(time.monotonic() - started)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
