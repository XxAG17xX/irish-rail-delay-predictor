"""
compare_to_operator.py — our model against Irish Rail's own ExpectedArrival.

This is the project's success criterion (CLAUDE.md): beat the operator's live ETA on
well-covered lines, and be honest about the lines where the data cannot support a claim.

The comparison is only meaningful if it is strictly fair, and there are four ways to get
it wrong. Each is handled explicitly below rather than assumed away.

1. Temporal leakage — the big one
---------------------------------
The operator issued its ETA at a specific instant. Our model must predict using only what
was knowable at that instant. So for a poll at time P, the vantage stop is the last stop
whose **actual arrival clock time** was before P. Not the last stop in the journey, and
not the last stop with a recorded arrival — the last one that had actually happened yet.

A stop that reported at 10:20 is invisible to a prediction made at 10:15, even though it
sits earlier in the journey and has a perfectly good delay recorded against it.

Polls taken after the train already arrived are dropped entirely. A station board can keep
listing a train past its arrival, and at that point `Exparrival` stops being a prediction.

2. Feature availability
-----------------------
`horizon_observed_stops` is **excluded**, and this is a real finding rather than a
convenience. build_examples.py counts horizons in stops that *did* report — knowable only
after the journey finished. At prediction time you know the schedule, so you know how many
stops lie ahead, but not which of them will report. A model using it could not be deployed.

It cost almost nothing to drop: 0.28% of gain in the trained model. Every remaining feature
is computable from the schedule plus stops already passed.

3. Output granularity
---------------------
`Exparrival` is minute-precision. Actual arrivals are 6-second precision (D22). Comparing a
to-the-second prediction against a to-the-minute one hands us up to 30 seconds of free
accuracy on every event. Both variants are therefore reported: raw, and with our prediction
rounded to the minute to match the operator's own granularity. The rounded one is the
defensible headline.

4. Correlated repeats
---------------------
Each event is polled ~18 times as the train approaches. Treating those as 18 independent
comparisons would inflate every count and narrow every interval. One comparison is kept per
(event, lead-time band): the last poll in that band, being the most informed prediction the
operator made at that range.

Model
-----
Trained on the `train` split only (2026-06-27 .. 07-12), the same fit whose validation
performance is known. The comparison dates are strictly later than every split, so they are
a clean holdout. `--train-splits train,val,test` widens it if you want a stronger model,
at the cost of no longer being the model you measured.

Usage (PowerShell, from the repo root, venv active):

    python scripts\\compare_to_operator.py
    python scripts\\compare_to_operator.py --start 2026-08-01 --end 2026-08-02
"""

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import lightgbm as lgb
import numpy as np
import pyarrow.dataset as ds

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LIVE = REPO_ROOT / "data" / "live" / "expected"
DEFAULT_PARSED = REPO_ROOT / "data" / "parsed"
DEFAULT_EXAMPLES = REPO_ROOT / "data" / "examples"

MONTHS = ("jan", "feb", "mar", "apr", "may", "jun",
          "jul", "aug", "sep", "oct", "nov", "dec")
PLACEHOLDER = {"", "00:00", "00:00:00"}
DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DUBLIN = ZoneInfo("Europe/Dublin")

# horizon_observed_stops deliberately absent — see note 2 in the module docstring.
NUMERIC = ["current_delay_sec", "prev_delay_sec", "prev2_delay_sec",
           "horizon_route_stops", "horizon_sched_sec",
           "vantage_hour", "vantage_minute_of_day"]
CATEGORICAL = ["day_of_week", "vantage_location", "target_location",
               "TrainOrigin", "TrainDestination"]
FEATURES = NUMERIC + CATEGORICAL

PARAMS = {"objective": "quantile", "alpha": 0.5, "learning_rate": 0.1,
          "num_leaves": 31, "min_data_in_leaf": 20, "verbose": -1,
          "seed": 42, "deterministic": True, "force_row_wise": True}
NUM_ROUNDS = 100

LEAD_BANDS = [(0, 300, "0-5 min"), (300, 900, "5-15 min"), (900, 1800, "15-30 min"),
              (1800, 3600, "30-60 min"), (3600, 10 ** 9, "60+ min")]


def iso_from_traindate(s):
    try:
        d, m, y = s.strip().split()
        return f"{int(y):04d}-{MONTHS.index(m[:3].lower()) + 1:02d}-{int(d):02d}"
    except (ValueError, AttributeError):
        return None


def hms(t):
    """'HH:MM:SS' or 'HH:MM' -> seconds since midnight. None if unusable."""
    if t in PLACEHOLDER:
        return None
    parts = t.strip().split(":")
    try:
        h, m = int(parts[0]), int(parts[1])
        s = int(parts[2]) if len(parts) > 2 else 0
        return h * 3600 + m * 60 + s
    except (ValueError, IndexError):
        return None


def unwrap(seconds_list):
    """Make a journey's times monotonic across midnight by adding days as needed."""
    out, offset, prev = [], 0, None
    for s in seconds_list:
        if s is None:
            out.append(None)
            continue
        if prev is not None and s + offset < prev - 43200:
            offset += 86400
        v = s + offset
        out.append(v)
        prev = v
    return out


# --------------------------------------------------------------------- loading

def load_journeys(parsed: Path, dates):
    """(iso_date, traincode) -> list of stops, ordered, with unwrapped clock times."""
    cols = ["TrainDate", "TrainCode", "LocationCode", "LocationOrder",
            "ScheduledArrival", "Arrival", "AutoArrival", "arrival_delay_sec",
            "TrainOrigin", "TrainDestination"]
    t = ds.dataset(parsed, format="parquet", partitioning="hive").to_table(
        columns=cols).to_pydict()
    raw = defaultdict(list)
    for i in range(len(t["TrainCode"])):
        iso = iso_from_traindate(t["TrainDate"][i] or "")
        if iso is None or iso not in dates:
            continue
        raw[(iso, (t["TrainCode"][i] or "").strip().upper())].append({
            "order": t["LocationOrder"][i],
            "loc": (t["LocationCode"][i] or "").strip().upper(),
            "sched_raw": hms(t["ScheduledArrival"][i] or ""),
            "arr_raw": hms(t["Arrival"][i] or ""),
            "auto": t["AutoArrival"][i],
            "delay": t["arrival_delay_sec"][i],
            "origin": t["TrainOrigin"][i],
            "destination": t["TrainDestination"][i],
        })
    journeys = {}
    for key, stops in raw.items():
        stops = [s for s in stops if s["order"] is not None]
        stops.sort(key=lambda s: s["order"])
        for field, dest in (("sched_raw", "sched"), ("arr_raw", "arr")):
            for s, v in zip(stops, unwrap([x[field] for x in stops])):
                s[dest] = v
        journeys[key] = stops
    return journeys


def load_polls(live: Path, dates):
    """(iso_date, code, station) -> list of (poll_seconds, operator_eta_seconds, group)."""
    events = defaultdict(list)
    for path in sorted(live.glob("*.jsonl")):
        for line in open(path, encoding="utf-8"):
            if not line.strip():
                continue
            r = json.loads(line)
            iso = iso_from_traindate(r.get("Traindate", ""))
            if iso is None or iso not in dates:
                continue
            eta = hms(r.get("Exparrival", ""))
            if eta is None:
                continue
            try:
                p = datetime.fromisoformat(r["polled_at"])
            except (ValueError, KeyError):
                continue
            # Records written before poll_live carried an offset are naive stamps from
            # an Irish host, so Europe/Dublin is the correct interpretation for both
            # forms. Anchoring the service date in the same zone keeps the subtraction
            # meaningful whatever machine wrote the record.
            if p.tzinfo is None:
                p = p.replace(tzinfo=DUBLIN)
            base = datetime.fromisoformat(iso).replace(tzinfo=DUBLIN)
            poll_s = (p - base).total_seconds()
            events[(iso, r["Traincode"].strip().upper(),
                    r["Stationcode"].strip().upper())].append(
                (poll_s, eta, r.get("station_group", "")))
    return events


# -------------------------------------------------------------------- the model

def train_model(examples: Path, splits):
    cols = FEATURES + ["target_delay_sec", "auto_vantage", "auto_target"]
    rows = defaultdict(list)
    for sp in splits:
        t = ds.dataset(examples / f"split={sp}", format="parquet",
                       partitioning="hive").to_table(columns=cols).to_pydict()
        for i in range(len(t["target_delay_sec"])):
            if t["auto_vantage"][i] == "1" and t["auto_target"][i] == "1":
                for c in cols:
                    rows[c].append(t[c][i])

    vocabs = {f: {v: i for i, v in enumerate(sorted({x for x in rows[f] if x is not None}))}
              for f in CATEGORICAL}
    n = len(rows["target_delay_sec"])
    X = np.full((n, len(FEATURES)), np.nan)
    for j, f in enumerate(FEATURES):
        if f in CATEGORICAL:
            vb = vocabs[f]
            X[:, j] = [vb.get(v, len(vb)) if v is not None else np.nan for v in rows[f]]
        else:
            X[:, j] = [np.nan if v is None else float(v) for v in rows[f]]
    y = np.array([float(v) for v in rows["target_delay_sec"]])

    dtrain = lgb.Dataset(X, label=y, feature_name=FEATURES,
                         categorical_feature=[FEATURES.index(f) for f in CATEGORICAL])
    booster = lgb.train(PARAMS, dtrain, num_boost_round=NUM_ROUNDS)
    return booster, vocabs, n


def featurise(stops, vi, ti, dow, vocabs):
    """Feature row from vantage index vi to target index ti. Mirrors build_examples.py."""
    v, s = stops[vi], stops[ti]
    obs = [k for k in range(vi) if stops[k]["delay"] is not None]
    row = {
        "current_delay_sec": v["delay"],
        "prev_delay_sec": stops[obs[-1]]["delay"] if obs else None,
        "prev2_delay_sec": stops[obs[-2]]["delay"] if len(obs) > 1 else None,
        "horizon_route_stops": s["order"] - v["order"],
        "horizon_sched_sec": (s["sched"] - v["sched"])
                             if (s["sched"] is not None and v["sched"] is not None) else None,
        "vantage_hour": (v["sched"] % 86400) // 3600 if v["sched"] is not None else None,
        "vantage_minute_of_day": (v["sched"] % 86400) // 60 if v["sched"] is not None else None,
        "day_of_week": dow,
        "vantage_location": v["loc"],
        "target_location": s["loc"],
        "TrainOrigin": v["origin"],
        "TrainDestination": v["destination"],
    }
    out = np.full(len(FEATURES), np.nan)
    for j, f in enumerate(FEATURES):
        val = row[f]
        if f in CATEGORICAL:
            vb = vocabs[f]
            out[j] = vb.get(val, len(vb)) if val is not None else np.nan
        elif val is not None:
            out[j] = float(val)
    return out


# ------------------------------------------------------------------- reporting

def stats(errs):
    a = np.abs(np.array(errs))
    return {"n": len(a), "mae": float(a.mean()), "medae": float(np.median(a))}


def block(title, op, mo, mo_r, indent="  "):
    if not op:
        return
    so, sm, sr = stats(op), stats(mo), stats(mo_r)
    print(f"\n{indent}{title}  (n={so['n']:,})")
    print(f"{indent}{'':<22}{'MAE':>9}{'medAE':>9}")
    print(indent + "-" * 40)
    print(f"{indent}{'operator ExpectedArrival':<22}{so['mae']:>9.1f}{so['medae']:>9.1f}")
    print(f"{indent}{'model (to the second)':<22}{sm['mae']:>9.1f}{sm['medae']:>9.1f}")
    print(f"{indent}{'model (minute-rounded)':<22}{sr['mae']:>9.1f}{sr['medae']:>9.1f}")
    d = 100 * (so["mae"] - sr["mae"]) / so["mae"] if so["mae"] else 0
    print(f"{indent}-> model {abs(d):.1f}% {'better' if d > 0 else 'WORSE'} "
          f"than the operator (minute-rounded)")
    w = sum(1 for a, b in zip(op, mo_r) if abs(b) < abs(a))
    t = sum(1 for a, b in zip(op, mo_r) if abs(b) == abs(a))
    n = len(op)
    print(f"{indent}   win {100*w/n:.1f}%  tie {100*t/n:.1f}%  lose {100*(n-w-t)/n:.1f}%")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", type=Path, default=DEFAULT_LIVE)
    ap.add_argument("--parsed", type=Path, default=DEFAULT_PARSED)
    ap.add_argument("--examples", type=Path, default=DEFAULT_EXAMPLES)
    ap.add_argument("--start", type=date.fromisoformat, default=date(2026, 8, 1))
    ap.add_argument("--end", type=date.fromisoformat, default=date(2026, 8, 2))
    ap.add_argument("--train-splits", default="train")
    args = ap.parse_args()

    dates = set()
    d = args.start
    while d <= args.end:
        dates.add(d.isoformat())
        d = date.fromordinal(d.toordinal() + 1)

    splits = [s.strip() for s in args.train_splits.split(",") if s.strip()]
    booster, vocabs, ntrain = train_model(args.examples, splits)
    journeys = load_journeys(args.parsed, dates)
    events = load_polls(args.live, dates)

    print(f"model trained on split(s) {', '.join(splits)} — {ntrain:,} examples, "
          f"{len(FEATURES)} features (horizon_observed_stops excluded)")
    print(f"comparison dates: {', '.join(sorted(dates))}")

    rows, drop = [], Counter()
    for (iso, code, stn), polls in events.items():
        stops = journeys.get((iso, code))
        if not stops:
            drop["no journey"] += 1
            continue
        ti = next((i for i, s in enumerate(stops) if s["loc"] == stn), None)
        if ti is None:
            drop["station not on route"] += 1
            continue
        tgt = stops[ti]
        if tgt["delay"] is None or tgt["arr"] is None:
            drop["no recorded arrival"] += 1
            continue
        if tgt["auto"] != "1":
            drop["target AutoArrival != 1"] += 1
            continue
        dow = DAY_NAMES[date.fromisoformat(iso).weekday()]

        for poll_s, eta, group in polls:
            if poll_s >= tgt["arr"]:
                drop["poll after arrival"] += 1
                continue
            # Only stops that had ACTUALLY HAPPENED by the poll instant.
            vi = None
            for i in range(ti):
                s = stops[i]
                if (s["arr"] is not None and s["arr"] < poll_s
                        and s["delay"] is not None and s["auto"] == "1"):
                    vi = i
            if vi is None:
                drop["nothing observed yet"] += 1
                continue
            lead = tgt["sched"] - poll_s if tgt["sched"] is not None else None
            if lead is None or lead < 0:
                drop["no usable lead time"] += 1
                continue
            rows.append({
                "key": (iso, code, stn), "group": group, "lead": lead, "poll": poll_s,
                "x": featurise(stops, vi, ti, dow, vocabs),
                "op_err": (eta + (86400 if eta < tgt["arr"] - 43200 else 0)) - tgt["arr"],
                "actual_delay": tgt["delay"],
            })

    if not rows:
        print("\nno comparable predictions built")
        return 2

    preds = booster.predict(np.vstack([r["x"] for r in rows]))
    for r, p in zip(rows, preds):
        r["mo_err"] = p - r["actual_delay"]
        r["mo_err_r"] = round(p / 60.0) * 60 - r["actual_delay"]

    # One comparison per (event, lead band): the last poll in that band.
    kept = {}
    for r in rows:
        band = next(lb for lo, hi, lb in LEAD_BANDS if lo <= r["lead"] < hi)
        r["band"] = band
        k = (r["key"], band)
        if k not in kept or r["poll"] > kept[k]["poll"]:
            kept[k] = r
    final = list(kept.values())

    W = 74
    print(f"\n{len(rows):,} polls yielded {len(final):,} comparisons "
          f"({len({r['key'] for r in final}):,} distinct events)")
    print("dropped: " + ", ".join(f"{k} {v:,}" for k, v in drop.most_common()))

    def cut(sel):
        s = [r for r in final if sel(r)]
        return ([r["op_err"] for r in s], [r["mo_err"] for r in s],
                [r["mo_err_r"] for r in s])

    print("\n" + "=" * W)
    print("OVERALL — errors in seconds against the same actual arrival")
    print("=" * W)
    block("all comparisons", *cut(lambda r: True))

    print("\n" + "=" * W)
    print("BY STATION GROUP")
    print("=" * W)
    for g in sorted({r["group"] for r in final},
                    key=lambda g: -sum(1 for r in final if r["group"] == g)):
        block(g, *cut(lambda r, g=g: r["group"] == g), indent="    ")

    print("\n" + "=" * W)
    print("BY LEAD TIME — how far ahead the prediction was made")
    print("=" * W)
    for _, _, lb in LEAD_BANDS:
        block(lb, *cut(lambda r, lb=lb: r["band"] == lb), indent="    ")

    print("\n" + "=" * W)
    print("Win/tie/lose is per comparison, model minute-rounded vs operator.")
    print("Ties are common: both are minute-precision against a 6-second actual.")
    print("=" * W)
    return 0


if __name__ == "__main__":
    sys.exit(main())
