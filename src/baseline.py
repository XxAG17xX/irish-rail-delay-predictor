"""
baseline.py — the numbers a model has to beat.

Two naive predictors, evaluated on held-out data:

  zero         predict 0 delay: the train arrives exactly on schedule.
               Beating this only proves the timetable is not a perfect predictor.
  persistence  predict the delay observed at the vantage stop carries forward unchanged.
               This is the real bar. Delay is strongly autocorrelated within a journey,
               so persistence is a genuinely strong naive predictor and any model that
               cannot beat it is not earning its complexity.

Neither has parameters, so neither is fitted. Both simply read the held-out set.

**Defaults to validation, not test.** The split design (build_examples.py, decisions.md
D24) holds the test week back to be opened once at the end. A zero-parameter baseline
leaks nothing by itself, but a test number seen now starts informing later choices, which
is exactly what a held-out week exists to prevent. `--split test` is available and prints
a warning when used, so opening it is a deliberate act.

Headline numbers restrict to `AutoArrival = 1` at **both** the vantage and target stop.
The target flag governs label quality, the vantage flag governs feature quality, and using
the same subset for both predictors keeps them comparable. Non-auto records echo the
schedule 29.43% of the time (label-quality.md), which would flatter the zero-delay
predictor specifically. The all-records figures are printed alongside, never instead of.

Metrics are in seconds. MAE is the headline; median absolute error is reported next to it
because the delay distribution has a long tail — 0.16% of arrivals are more than an hour
late — and a mean alone hides how the typical case behaves.

Usage (PowerShell, from the repo root, venv active):

    python src\\baseline.py
    python src\\baseline.py --split test      # deliberate; see above
"""

import argparse
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import pyarrow.dataset as ds

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_EXAMPLES = REPO_ROOT / "data" / "examples"

PREDICTORS = {
    "zero": lambda r: 0,
    "persistence": lambda r: r["current_delay_sec"],
}

TIME_BANDS = [
    (0, 300, "0-5 min"),
    (300, 900, "5-15 min"),
    (900, 1800, "15-30 min"),
    (1800, 3600, "30-60 min"),
    (3600, 10 ** 9, "60+ min"),
]


def metrics(rows, predict):
    """MAE, median absolute error, and mean signed error, all in seconds."""
    errs = []
    signed = []
    for r in rows:
        p = predict(r)
        if p is None:
            continue
        e = p - r["target_delay_sec"]
        signed.append(e)
        errs.append(abs(e))
    if not errs:
        return None
    return {
        "n": len(errs),
        "mae": sum(errs) / len(errs),
        "medae": statistics.median(errs),
        "bias": sum(signed) / len(signed),
    }


def print_block(title, rows, indent="  "):
    print(f"\n{indent}{title}  (n={len(rows)})")
    print(f"{indent}{'predictor':<14} {'n':>9} {'MAE':>9} {'medAE':>9} {'bias':>9}")
    print(indent + "-" * 53)
    results = {}
    for name, fn in PREDICTORS.items():
        m = metrics(rows, fn)
        results[name] = m
        if m is None:
            print(f"{indent}{name:<14} {'-':>9}")
            continue
        print(f"{indent}{name:<14} {m['n']:>9} {m['mae']:>9.1f} "
              f"{m['medae']:>9.1f} {m['bias']:>+9.1f}")
    z, p = results.get("zero"), results.get("persistence")
    if z and p and z["mae"]:
        print(f"{indent}persistence improves MAE by "
              f"{100 * (z['mae'] - p['mae']) / z['mae']:.1f}% over zero")
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--examples", type=Path, default=DEFAULT_EXAMPLES)
    ap.add_argument("--split", default="val", choices=["train", "val", "test"],
                    help="which split to evaluate (default: val)")
    args = ap.parse_args()

    split_dir = args.examples / f"split={args.split}"
    if not split_dir.exists():
        print(f"no examples at {split_dir} — run src\\build_examples.py first")
        return 2

    if args.split == "test":
        print("!" * 72)
        print("! OPENING THE TEST WEEK. Per decisions.md D24 this happens once, at the")
        print("! end. Any tuning decision made after reading these numbers invalidates")
        print("! them as an unbiased estimate. Use --split val for iteration.")
        print("!" * 72)

    rows = ds.dataset(split_dir, format="parquet", partitioning="hive").to_table(
        columns=["current_delay_sec", "target_delay_sec", "horizon_observed_stops",
                 "horizon_sched_sec", "auto_vantage", "auto_target",
                 "day_of_week", "file_date"]).to_pylist()

    auto = [r for r in rows if r["auto_vantage"] == "1" and r["auto_target"] == "1"]

    dates = sorted({r["file_date"] for r in rows})
    print(f"\nbaseline — split '{args.split}', {dates[0]} .. {dates[-1]} "
          f"({len(dates)} dates)")
    print(f"  {len(rows)} examples, {len(auto)} with AutoArrival=1 at both ends "
          f"({100 * len(auto) / len(rows):.1f}%)")
    print("  MAE / medAE / bias in seconds. bias>0 means the predictor says later "
          "than reality.")

    W = 72
    print("\n" + "=" * W)
    print("HEADLINE — AutoArrival=1 at both vantage and target")
    print("=" * W)
    print_block("overall", auto)

    print("\n  by horizon (observed stops ahead):")
    by_h = defaultdict(list)
    for r in auto:
        by_h[r["horizon_observed_stops"]].append(r)
    for h in sorted(by_h):
        print_block(f"horizon {h}", by_h[h], indent="    ")

    print("\n  by scheduled time from vantage to target:")
    by_b = defaultdict(list)
    for r in auto:
        g = r["horizon_sched_sec"]
        if g is None:
            continue
        for lo, hi, label in TIME_BANDS:
            if lo <= g < hi:
                by_b[label].append(r)
                break
    for _, _, label in TIME_BANDS:
        if by_b.get(label):
            print_block(label, by_b[label], indent="    ")

    print("\n" + "=" * W)
    print("ALL RECORDS — no AutoArrival restriction, for comparison")
    print("=" * W)
    print_block("overall", rows)
    print("\n  Non-auto records echo the schedule 29.43% of the time, which flatters the")
    print("  zero-delay predictor specifically. If 'zero' looks better here than in the")
    print("  headline block, that is the echo, not punctuality. See label-quality.md.")

    print("\n" + "=" * W)
    print("These are the numbers to beat. Persistence, not zero, is the real bar.")
    print("=" * W)
    return 0


if __name__ == "__main__":
    sys.exit(main())
