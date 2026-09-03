"""
build_examples.py — turn parsed journeys into supervised training examples.

One example is one prediction: standing at stop V with everything observed up to there,
predict the arrival delay at a downstream stop S.

Design (decisions.md D24)
------------------------
**Fixed horizons of 1, 3, 5 and 10 observed stops ahead.** Not all (V,S) pairs: the median
journey has 18 observed stops, so all-pairs would yield 187 examples per journey, all
sharing one underlying delay realisation. That inflates the apparent sample size roughly
200-fold and tilts the fit toward long intercity runs, which produce 595 examples against
a commuter hop's 3. Fixed horizons bound the multiplier to ~4 per vantage point and let
evaluation report MAE at each horizon separately rather than as one blended number.

**Horizon is sampled in stops but carried in minutes too.** One stop is ~2 minutes on the
DART and ~40 on a Heuston-Cork run, so a stop count alone mixes two unrelated prediction
problems. `horizon_sched_sec` is the scheduled time from V to S and is the input a model
should actually use.

**Horizons count OBSERVED stops, not route positions.** `h=3` means the third next stop
that reported an arrival, not the stop three positions along. Indexing by route position
would require that exact position to be observed and would sample only well-covered
stretches — conditioning the training set on the coverage problem the project exists to
handle. The true route distance is kept as `horizon_route_stops` so nothing is hidden.

**Nothing is filtered by AutoArrival here.** Both the vantage and target flags are carried
as columns so downstream code can choose. Per decisions.md D23 the exclusion decision
belongs at evaluation time, not baked into the dataset.

Split (temporal, never random)
------------------------------
    train        2026-06-27 .. 2026-07-12   (16 dates)
    validation   2026-07-13 .. 2026-07-19   (7 dates, one calendar week)
    test         2026-07-20 .. 2026-07-26   (7 dates, one calendar week)

2026-06-25 and 06-26 are excluded: 34 journeys each from the original 36-code test slice,
against ~880 on a normal weekday.

Both held-out windows are whole calendar weeks with every weekday appearing exactly once.
Sundays run 332 journeys against 880 on a weekday, so a window that is not week-aligned
would skew the result by service mix alone.

**All tuning goes against validation. The test week is opened once, at the end.**

Usage (PowerShell, from the repo root, venv active):

    python src\\build_examples.py
    python src\\build_examples.py --horizons 1,2,3,5,10 --force
"""

import argparse
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parent))
from feedtime import journey_consistent  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PARSED = REPO_ROOT / "data" / "parsed"
DEFAULT_OUT = REPO_ROOT / "data" / "examples"

HALF_DAY = 43200
FULL_DAY = 86400
DEFAULT_HORIZONS = (1, 3, 5, 10)

SPLITS = {
    "train": (date(2026, 6, 27), date(2026, 7, 12)),
    "val":   (date(2026, 7, 13), date(2026, 7, 19)),
    "test":  (date(2026, 7, 20), date(2026, 7, 26)),
}
EXCLUDED_DATES = {"2026-06-25", "2026-06-26"}

DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

SCHEMA = pa.schema([
    ("file_date", pa.string()),
    ("TrainCode", pa.string()),
    ("day_of_week", pa.string()),
    ("vantage_order", pa.int32()),
    ("target_order", pa.int32()),
    ("vantage_location", pa.string()),
    ("target_location", pa.string()),
    ("vantage_sched_arrival", pa.string()),
    ("target_sched_arrival", pa.string()),
    ("vantage_hour", pa.int32()),
    ("vantage_minute_of_day", pa.int32()),
    # No `line` field exists in the feed. Origin->destination is the closest available
    # proxy for route, and unlike a station code it identifies the whole corridor.
    ("TrainOrigin", pa.string()),
    ("TrainDestination", pa.string()),
    ("current_delay_sec", pa.int32()),      # feature: delay at V
    ("prev_delay_sec", pa.int32()),         # feature: one observed stop before V
    ("prev2_delay_sec", pa.int32()),        # feature: two observed stops before V
    ("horizon_observed_stops", pa.int32()),  # the sampling axis
    ("horizon_route_stops", pa.int32()),    # true LocationOrder distance
    ("horizon_sched_sec", pa.int32()),      # scheduled seconds from V to S
    ("auto_vantage", pa.string()),
    ("auto_target", pa.string()),
    ("target_delay_sec", pa.int32()),       # THE LABEL
])


def to_seconds(t: str):
    try:
        h, m, s = t.split(":")
        return int(h) * 3600 + int(m) * 60 + int(s)
    except (ValueError, AttributeError):
        return None


def wrapped_gap(later: str, earlier: str):
    """Scheduled seconds between two times, handling a journey crossing midnight."""
    a, b = to_seconds(later), to_seconds(earlier)
    if a is None or b is None:
        return None
    d = a - b
    if d > HALF_DAY:
        d -= FULL_DAY
    elif d < -HALF_DAY:
        d += FULL_DAY
    return d


def split_for(day: str):
    d = date.fromisoformat(day)
    for name, (lo, hi) in SPLITS.items():
        if lo <= d <= hi:
            return name
    return None


def build(parsed: Path, horizons, keep_inconsistent: bool = False):
    """Returns {(split, date): [example dicts]} plus counters."""
    dataset = ds.dataset(parsed, format="parquet", partitioning="hive")
    cols = ["file_date", "TrainCode", "LocationOrder", "LocationCode",
            "ScheduledArrival", "AutoArrival", "arrival_delay_sec",
            "TrainOrigin", "TrainDestination"]
    table = dataset.to_table(columns=cols).to_pydict()

    journeys = defaultdict(list)
    for i in range(len(table["file_date"])):
        day = table["file_date"][i]
        if day in EXCLUDED_DATES or split_for(day) is None:
            continue
        if table["arrival_delay_sec"][i] is None or table["LocationOrder"][i] is None:
            continue
        journeys[(day, table["TrainCode"][i])].append({
            "order": table["LocationOrder"][i],
            "loc": table["LocationCode"][i],
            "sched": table["ScheduledArrival"][i],
            "auto": table["AutoArrival"][i],
            "delay": table["arrival_delay_sec"][i],
            "origin": table["TrainOrigin"][i],
            "destination": table["TrainDestination"][i],
        })

    out = defaultdict(list)
    stats = Counter()
    stats["journeys"] = len(journeys)

    for (day, code), recs in journeys.items():
        recs.sort(key=lambda r: r["order"])
        # A journey whose reported arrivals go backwards along the route contains at
        # least one time that belongs to a different train (D56). Applied here, before
        # any example is cut, so train, validation and test are filtered identically and
        # the criterion never sees a model output. It reads only what the feed reported.
        if not keep_inconsistent and not journey_consistent(
                [{"order": r["order"], "sched": to_seconds(r["sched"]), "delay": r["delay"]}
                 for r in recs]):
            stats["journeys_inconsistent"] += 1
            stats[f"journeys_inconsistent_{split_for(day)}"] += 1
            continue
        split = split_for(day)
        dow = DAY_NAMES[date.fromisoformat(day).weekday()]
        n = len(recs)
        if n < 2:
            stats["journeys_too_short"] += 1
            continue

        for i, v in enumerate(recs):
            for h in horizons:
                j = i + h
                if j >= n:
                    continue
                s = recs[j]
                sched_gap = wrapped_gap(s["sched"], v["sched"])
                hour = to_seconds(v["sched"])
                out[(split, day)].append({
                    "file_date": day,
                    "TrainCode": code,
                    "day_of_week": dow,
                    "vantage_order": v["order"],
                    "target_order": s["order"],
                    "vantage_location": v["loc"],
                    "target_location": s["loc"],
                    "vantage_sched_arrival": v["sched"],
                    "target_sched_arrival": s["sched"],
                    "vantage_hour": (hour // 3600) if hour is not None else None,
                    "vantage_minute_of_day": (hour // 60) if hour is not None else None,
                    "TrainOrigin": v["origin"],
                    "TrainDestination": v["destination"],
                    "current_delay_sec": v["delay"],
                    "prev_delay_sec": recs[i - 1]["delay"] if i >= 1 else None,
                    "prev2_delay_sec": recs[i - 2]["delay"] if i >= 2 else None,
                    "horizon_observed_stops": h,
                    "horizon_route_stops": s["order"] - v["order"],
                    "horizon_sched_sec": sched_gap,
                    "auto_vantage": v["auto"],
                    "auto_target": s["auto"],
                    "target_delay_sec": s["delay"],
                })
                stats["examples"] += 1
    return out, stats


def write_partition(rows, dest_dir: Path):
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "part.parquet"
    tmp = dest.with_suffix(".parquet.tmp")
    pq.write_table(pa.Table.from_pylist(rows, schema=SCHEMA), tmp, compression="zstd")
    os.replace(tmp, dest)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--parsed", type=Path, default=DEFAULT_PARSED)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--horizons", default=",".join(str(h) for h in DEFAULT_HORIZONS),
                    help="observed stops ahead, comma separated "
                         f"(default: {','.join(str(h) for h in DEFAULT_HORIZONS)})")
    ap.add_argument("--force", action="store_true", help="rewrite existing partitions")
    ap.add_argument("--keep-inconsistent", action="store_true",
                    help="do NOT drop journeys whose arrivals go backwards along the "
                         "route. Reproduces the pre-D56 example set.")
    args = ap.parse_args()

    if not args.parsed.exists():
        print(f"no parsed dataset at {args.parsed} — run src\\parse_raw.py first")
        return 2

    horizons = tuple(sorted(int(h) for h in args.horizons.split(",") if h.strip()))
    if not horizons or min(horizons) < 1:
        print("--horizons must be positive integers")
        return 2

    print("build_examples")
    print(f"  horizons (observed stops ahead): {horizons}")
    for name, (lo, hi) in SPLITS.items():
        print(f"  {name:<5} {lo} .. {hi}  ({(hi - lo).days + 1} dates)")
    print(f"  excluded dates: {', '.join(sorted(EXCLUDED_DATES))} "
          f"(thin 36-code test slice)\n")

    started = time.monotonic()
    grouped, stats = build(args.parsed, horizons, args.keep_inconsistent)
    if not grouped:
        print("no examples built — check the parsed dataset covers the split ranges")
        return 2

    per_split = Counter()
    per_split_journeys = defaultdict(set)
    written = skipped = 0
    for (split, day), rows in sorted(grouped.items()):
        per_split[split] += len(rows)
        for r in rows:
            per_split_journeys[split].add((r["file_date"], r["TrainCode"]))
        dest_dir = args.out / f"split={split}" / f"date={day}"
        if not args.force and (dest_dir / "part.parquet").exists():
            skipped += 1
            continue
        write_partition(rows, dest_dir)
        written += 1

    W = 66
    print("=" * W)
    print("SUMMARY")
    print("=" * W)
    print(f"  journeys with >=1 observed arrival {stats['journeys']:>10}")
    print(f"  journeys too short to use          {stats['journeys_too_short']:>10}")
    print(f"  journeys dropped as inconsistent   {stats['journeys_inconsistent']:>10}"
          + ("  (kept: --keep-inconsistent)" if args.keep_inconsistent else
             "  train %d / val %d / test %d" % tuple(stats[f"journeys_inconsistent_{s}"] for s in ("train","val","test"))))
    print(f"  examples built                     {stats['examples']:>10}")
    print(f"  partitions written                 {written:>10}")
    if skipped:
        print(f"  partitions already present         {skipped:>10}  (use --force)")

    print(f"\n  {'split':<8} {'examples':>10} {'journeys':>10} {'share':>8}")
    print("  " + "-" * 40)
    total = sum(per_split.values())
    for name in ("train", "val", "test"):
        n = per_split.get(name, 0)
        print(f"  {name:<8} {n:>10} {len(per_split_journeys[name]):>10} "
              f"{100 * n / total if total else 0:7.1f}%")

    print(f"\ndone in {time.monotonic() - started:.1f}s -> {args.out}")
    print("Reminder: all tuning against validation. The test week opens once, at the end.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
