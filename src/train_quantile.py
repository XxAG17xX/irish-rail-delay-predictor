"""
train_quantile.py — LightGBM quantile regression, first honest number.

Fits three models at quantiles 0.1, 0.5 and 0.9 so the output is an interval rather than
a point estimate, which is the whole premise of the project. The 0.5 model is the point
prediction and is compared directly against the persistence baseline.

**Nothing is tuned.** LightGBM defaults throughout: learning_rate 0.1, num_leaves 31,
min_data_in_leaf 20, 100 boosting rounds, fixed seed. No early stopping, no search. A
tuned number arrived at before an untuned one is a number you cannot interpret.

Features — Tier 1 from feature-ideas.md only
--------------------------------------------
  delay history      current_delay_sec, prev_delay_sec, prev2_delay_sec
  horizon            horizon_observed_stops, horizon_route_stops, horizon_sched_sec
  time of day        vantage_hour, vantage_minute_of_day
  calendar           day_of_week
  route              vantage_location, target_location, TrainOrigin, TrainDestination

Two Tier 1 entries are not what feature-ideas.md asks for, and it matters:

  `line` does not exist in the feed. TrainOrigin -> TrainDestination is the closest
  available proxy: unlike a single station code it identifies a whole corridor. It is a
  proxy, not the thing itself.

  `train_type` does not exist either. `getTrainMovementsXML` carries no type field —
  see feature-ideas.md. **It is deliberately absent from this model.** TrainCode prefixes
  (E, A, D, P, B) look like a class marker and would probably work, but nobody here knows
  what they mean, and CLAUDE.md is explicit that anything undefendable does not belong in
  the repo. Testing it is a separate, deliberate decision.

Trained and evaluated on AutoArrival=1 at both vantage and target, matching baseline.py's
headline subset so the comparison is like for like. See decisions.md D23, D26.

Usage (PowerShell, from the repo root, venv active):

    python src\\train_quantile.py
    python src\\train_quantile.py --eval-split test    # deliberate; opens the test week
"""

import argparse
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pyarrow.dataset as ds

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_EXAMPLES = REPO_ROOT / "data" / "examples"

NUMERIC = [
    "current_delay_sec", "prev_delay_sec", "prev2_delay_sec",
    "horizon_observed_stops", "horizon_route_stops", "horizon_sched_sec",
    "vantage_hour", "vantage_minute_of_day",
]
CATEGORICAL = [
    "day_of_week", "vantage_location", "target_location",
    "TrainOrigin", "TrainDestination",
]
FEATURES = NUMERIC + CATEGORICAL
LABEL = "target_delay_sec"

QUANTILES = (0.1, 0.5, 0.9)

PARAMS = {
    "objective": "quantile",
    "learning_rate": 0.1,
    "num_leaves": 31,
    "min_data_in_leaf": 20,
    "verbose": -1,
    "seed": 42,
    "deterministic": True,
    "force_row_wise": True,
}
NUM_ROUNDS = 100

TIME_BANDS = [
    (0, 300, "0-5 min"),
    (300, 900, "5-15 min"),
    (900, 1800, "15-30 min"),
    (1800, 3600, "30-60 min"),
    (3600, 10 ** 9, "60+ min"),
]


def load(examples: Path, split: str):
    cols = FEATURES + [LABEL, "auto_vantage", "auto_target", "file_date"]
    table = ds.dataset(examples / f"split={split}", format="parquet",
                       partitioning="hive").to_table(columns=cols).to_pydict()
    keep = [i for i in range(len(table[LABEL]))
            if table["auto_vantage"][i] == "1" and table["auto_target"][i] == "1"]
    return {c: [table[c][i] for i in keep] for c in cols}, len(table[LABEL])


def build_matrix(data, vocabs):
    """Feature matrix as float64. NaN for missing; LightGBM handles it natively."""
    n = len(data[LABEL])
    X = np.full((n, len(FEATURES)), np.nan, dtype=np.float64)
    for j, f in enumerate(FEATURES):
        col = data[f]
        if f in CATEGORICAL:
            vocab = vocabs[f]
            unseen = len(vocab)
            X[:, j] = [vocab.get(v, unseen) if v is not None else np.nan for v in col]
        else:
            X[:, j] = [np.nan if v is None else float(v) for v in col]
    y = np.array([float(v) for v in data[LABEL]], dtype=np.float64)
    return X, y


def metrics(pred, actual):
    err = pred - actual
    ae = np.abs(err)
    return {"n": len(ae), "mae": float(ae.mean()),
            "medae": float(np.median(ae)), "bias": float(err.mean())}


def row(label, m, indent="  "):
    print(f"{indent}{label:<16} {m['n']:>9} {m['mae']:>9.1f} "
          f"{m['medae']:>9.1f} {m['bias']:>+9.1f}")


def compare_block(title, idx, persist, model_p50, actual, indent="  "):
    """Model against persistence on the same subset, so the delta is unambiguous."""
    if len(idx) == 0:
        return
    mp = metrics(persist[idx], actual[idx])
    mm = metrics(model_p50[idx], actual[idx])
    print(f"\n{indent}{title}  (n={len(idx)})")
    print(f"{indent}{'predictor':<16} {'n':>9} {'MAE':>9} {'medAE':>9} {'bias':>9}")
    print(indent + "-" * 55)
    row("persistence", mp, indent)
    row("lgbm q0.50", mm, indent)
    delta = 100 * (mp["mae"] - mm["mae"]) / mp["mae"] if mp["mae"] else 0
    verdict = "better" if delta > 0 else "WORSE"
    print(f"{indent}-> model MAE {abs(delta):.1f}% {verdict} than persistence")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--examples", type=Path, default=DEFAULT_EXAMPLES)
    ap.add_argument("--train-split", default="train")
    ap.add_argument("--eval-split", default="val", choices=["train", "val", "test"])
    args = ap.parse_args()

    if args.eval_split == "test":
        print("!" * 72)
        print("! OPENING THE TEST WEEK — decisions.md D25 says once, at the end.")
        print("!" * 72)

    tr, tr_total = load(args.examples, args.train_split)
    ev, ev_total = load(args.examples, args.eval_split)

    vocabs = {f: {v: i for i, v in enumerate(sorted({x for x in tr[f] if x is not None}))}
              for f in CATEGORICAL}

    Xtr, ytr = build_matrix(tr, vocabs)
    Xev, yev = build_matrix(ev, vocabs)

    print(f"\ntrain '{args.train_split}': {len(ytr)} of {tr_total} examples "
          f"(AutoArrival=1 both ends)")
    print(f"eval  '{args.eval_split}': {len(yev)} of {ev_total} examples")
    print(f"features: {len(FEATURES)} — {len(NUMERIC)} numeric, "
          f"{len(CATEGORICAL)} categorical")
    print(f"params: LightGBM defaults, {NUM_ROUNDS} rounds, lr "
          f"{PARAMS['learning_rate']}, num_leaves {PARAMS['num_leaves']}, "
          f"seed {PARAMS['seed']} — NOT tuned")
    print("train type: not in the feed, deliberately absent (see module docstring)")

    cat_idx = [FEATURES.index(f) for f in CATEGORICAL]
    raw = {}
    for q in QUANTILES:
        dtrain = lgb.Dataset(Xtr, label=ytr, feature_name=FEATURES,
                             categorical_feature=cat_idx, free_raw_data=False)
        booster = lgb.train({**PARAMS, "alpha": q}, dtrain, num_boost_round=NUM_ROUNDS)
        raw[q] = booster.predict(Xev)
        if q == 0.5:
            gains = sorted(zip(FEATURES, booster.feature_importance("gain")),
                           key=lambda kv: -kv[1])

    # Each quantile is fitted independently, so nothing forces q0.10 <= q0.50 <= q0.90.
    # Sorting each row restores monotonicity. This is the standard remedy and it cannot
    # make calibration worse: sorting a set of quantile estimates weakly reduces the
    # pinball loss of every one of them (Chernozhukov et al., quantile rearrangement).
    stacked = np.vstack([raw[q] for q in QUANTILES])
    reordered = int((np.diff(stacked, axis=0) < 0).any(axis=0).sum())
    stacked = np.sort(stacked, axis=0)
    preds = {q: stacked[i] for i, q in enumerate(QUANTILES)}

    persist = np.array([np.nan if v is None else float(v)
                        for v in ev["current_delay_sec"]])
    p50 = preds[0.5]
    all_idx = np.arange(len(yev))

    W = 72
    print("\n" + "=" * W)
    print(f"POINT PREDICTION — lgbm q0.50 vs persistence, split '{args.eval_split}'")
    print("=" * W)
    compare_block("overall", all_idx, persist, p50, yev)

    print("\n  by horizon (observed stops ahead):")
    h = np.array(ev["horizon_observed_stops"], dtype=np.float64)
    for hv in sorted(set(ev["horizon_observed_stops"])):
        compare_block(f"horizon {hv}", np.where(h == hv)[0], persist, p50, yev, "    ")

    print("\n  by scheduled time from vantage to target:")
    g = np.array([np.nan if v is None else float(v) for v in ev["horizon_sched_sec"]])
    for lo, hi, label in TIME_BANDS:
        compare_block(label, np.where((g >= lo) & (g < hi))[0], persist, p50, yev, "    ")

    print("\n" + "=" * W)
    print("INTERVAL COVERAGE — target is 80% inside the 10-90 range")
    print("=" * W)
    lo_p, hi_p = preds[0.1], preds[0.9]
    inside = (yev >= lo_p) & (yev <= hi_p)
    width = hi_p - lo_p
    print(f"\n  {'subset':<16} {'n':>9} {'coverage':>10} {'target':>8} "
          f"{'med width':>11}")
    print("  " + "-" * 58)
    print(f"  {'overall':<16} {len(yev):>9} {100 * inside.mean():>9.1f}% "
          f"{'80.0%':>8} {np.median(width):>10.0f}s")
    for hv in sorted(set(ev["horizon_observed_stops"])):
        m = h == hv
        print(f"  {'horizon ' + str(hv):<16} {int(m.sum()):>9} "
              f"{100 * inside[m].mean():>9.1f}% {'80.0%':>8} "
              f"{np.median(width[m]):>10.0f}s")
    for lo, hi, label in TIME_BANDS:
        m = (g >= lo) & (g < hi)
        if m.sum():
            print(f"  {label:<16} {int(m.sum()):>9} {100 * inside[m].mean():>9.1f}% "
                  f"{'80.0%':>8} {np.median(width[m]):>10.0f}s")

    below = float((yev < lo_p).mean())
    above = float((yev > hi_p).mean())
    print(f"\n  misses below q0.10: {100 * below:.1f}%  "
          f"(expected 10.0%)   above q0.90: {100 * above:.1f}%  (expected 10.0%)")
    print(f"  quantile rearrangement: {reordered} of {len(yev)} rows "
          f"({100 * reordered / len(yev):.3f}%) were non-monotonic before sorting; "
          f"all rows now satisfy q0.10 <= q0.50 <= q0.90.")

    print("\n" + "=" * W)
    print("FEATURE IMPORTANCE (gain, q0.50 model)")
    print("=" * W)
    total = sum(v for _, v in gains) or 1
    for f, v in gains:
        print(f"  {f:<26} {100 * v / total:6.2f}%")

    return 0


if __name__ == "__main__":
    sys.exit(main())
