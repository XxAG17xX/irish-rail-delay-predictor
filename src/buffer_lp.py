"""
buffer_lp.py — redistribute a train's schedule padding by linear programming.

A timetable schedules more running time than a train needs; the excess absorbs delay. It is
currently spread however the timetable spreads it. Keeping the SAME TOTAL, is there a better
allocation? That is a decision with a budget and a measurable objective, so it is a linear
program. The full derivation is in docs/optimization-revision.pdf and decisions.md D59-D61.

The program, and where each piece lives in this file
-----------------------------------------------------
Stops i = 0..n along one route; segment i runs from stop i-1 to stop i. Scenarios are
historical days.

    variables     b_i >= 0                    buffer on segment i          -> `solve`
                  L_i^s >= 0                  lateness at stop i on day s

    objective     min (1/S) sum_s sum_i w_i L_i^s                          -> `solve`

    C1            L_i^s >= L_{i-1}^s + d_i^s - b_i                         -> `solve`
    C2            L_i^s >= 0                                              (variable bound)
    C3            sum_i b_i <= B                                           -> `solve`
    C4            0 <= b_i <= cap_i                                       (variable bound)
    C5            L_0^s = l0^s                                            (constant in C1)

The one step that makes this a formulation rather than a solver call
---------------------------------------------------------------------
The true dynamics are L_i = max(0, L_{i-1} + d_i - b_i), which is not linear. C1 and C2 are
that max written as two INEQUALITIES, which is weaker: they permit an L_i larger than the
max. The relaxation is tight because the objective drives every L_i down, C1 and C2 are its
only lower bounds, and reducing an L_i only ever RELAXES the constraint at i+1 (it appears
there with coefficient +1). So nothing is gained by inflating one.

That argument needs care under terminus-only weighting, where w_i = 0 at every intermediate
stop and "the objective pushes it down" is false for those variables. It is still tight: a
reduction propagates forward along the chain to the terminus, where the weight is positive.
The condition is `w_j >= 0 for all j` AND `sum_{j>=i} w_j > 0`, not `w_i > 0`.

`_self_check` verifies this empirically: it solves the LP, replays the solution through the
true nonlinear recursion in `simulate`, and asserts the two agree. If the linearisation were
wrong the LP objective would sit BELOW the simulated cost, because the LP would have found a
cheaper point that the real dynamics do not permit.

A negative weight anywhere breaks it for real, and `solve` refuses one.
"""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pyarrow.dataset as ds
import pyarrow.compute as pc
from scipy.optimize import linprog
from scipy.sparse import coo_matrix

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from feedtime import hms, journey_consistent  # noqa: E402

REPO_ROOT = HERE.parent
DEFAULT_PARSED = REPO_ROOT / "data" / "parsed"

# The technical minimum is estimated as a low PERCENTILE of observed running times, never
# the minimum. The minimum of a sample is not robust: taking it produced ETOWN->MLGAR
# scheduled at 1050s and apparently run in 156s, about 580 km/h, because ETOWN is one of the
# four stations whose arrival records belong to other trains (D56). Same defect, new
# calculation.
MIN_PERCENTILE = 5


# ----------------------------------------------------------------- the instance

def _gap(a, b):
    """b - a in seconds, tolerating a journey crossing midnight."""
    if a is None or b is None:
        return None
    g = b - a
    return g + 86400 if g < -43200 else g


def build_instance(train_code, parsed=DEFAULT_PARSED, min_days=15,
                   pct=MIN_PERCENTILE):
    """Everything the program consumes, for one train, from the parsed archive.

    Returns None when the train has too few days on which EVERY segment was observed. A
    scenario with a hole in it is not a scenario: the recursion needs a complete chain, and
    filling a gap with an average would invent the very quantity being optimised over.
    """
    cols = ["TrainCode", "date", "LocationCode", "LocationOrder", "LocationType",
            "ScheduledArrival", "ScheduledDeparture", "Arrival", "Departure",
            "AutoArrival", "AutoDepart", "TrainOrigin", "TrainDestination",
            "arrival_delay_sec", "departure_delay_sec"]
    tbl = ds.dataset(parsed, format="parquet", partitioning="hive").to_table(
        columns=cols, filter=pc.equal(ds.field("TrainCode"), train_code))

    days = defaultdict(list)
    for r in tbl.to_pylist():
        try:
            r["_o"] = int(r["LocationOrder"])
        except (TypeError, ValueError):
            continue
        days[r["date"]].append(r)

    route, stops_ref = None, None
    scenarios, dates, l0, dropped = [], [], [], defaultdict(int)
    sched_run, obs_run = None, defaultdict(list)

    for day in sorted(days):
        rows = days[day]
        # D52/D56: a journey whose reported arrivals move backwards along the route holds at
        # least one time belonging to another train. Excluded before anything is measured.
        if not journey_consistent([{"order": r["_o"], "sched": hms(r["ScheduledArrival"]),
                                    "delay": r["arrival_delay_sec"]} for r in rows]):
            dropped["inconsistent_journey"] += 1
            continue
        st = [r for r in sorted(rows, key=lambda x: x["_o"])
              if r["LocationType"] in ("O", "S", "D")]
        if len(st) < 3:
            dropped["too_few_stops"] += 1
            continue

        codes = tuple(r["LocationCode"] for r in st)
        if stops_ref is None:
            stops_ref, route = codes, f"{st[0]['TrainOrigin']}->{st[0]['TrainDestination']}"
            sched_run = [_gap(hms(st[i - 1]["ScheduledDeparture"]),
                              hms(st[i]["ScheduledArrival"])) for i in range(1, len(st))]
        elif codes != stops_ref:
            # The stopping pattern changed. Pooling two patterns would index segment i to
            # different pieces of track on different days.
            dropped["different_stopping_pattern"] += 1
            continue

        runs = []
        for i in range(1, len(st)):
            a, b = st[i - 1], st[i]
            run = _gap(hms(a["Departure"]), hms(b["Arrival"]))
            # AutoArrival/AutoDepart at BOTH ends: a hand-entered time is the echo risk
            # D20-D23 is about, and a running time is the difference of two of them.
            if (run is None or not 0 < run < 7200
                    or a["AutoDepart"] != "1" or b["AutoArrival"] != "1"):
                runs = None
                break
            runs.append(run)
        if runs is None:
            dropped["incomplete_or_unverified_run"] += 1
            continue

        scenarios.append(runs)
        dates.append(day)
        # C5. The origin's departure delay is the lateness the train starts with; running
        # buffer downstream is what has to absorb it. Absent, assume it started on time.
        d0 = st[0].get("departure_delay_sec")
        l0.append(float(d0) if d0 is not None else 0.0)
        for i, run in enumerate(runs):
            obs_run[i].append(run)

    if len(scenarios) < min_days or sched_run is None:
        return None
    if any(s is None or s <= 0 for s in sched_run):
        return None

    rho = np.array(scenarios, dtype=float)                    # (S, n) observed running time
    r = np.array(sched_run, dtype=float)                      # (n,)   scheduled
    m = np.percentile(rho, pct, axis=0)

    # A schedule tighter than the data says is achievable would give a NEGATIVE existing
    # buffer, and there is no such thing as negative padding. Three segments in the archive
    # are like this (CRLOW->ATHY is scheduled at 600s against a 5th-percentile actual of
    # 702s). Taking m_i = min(m_i, r_i) treats the schedule as achievable by definition
    # where the data disagrees. That is an assumption, and the count is reported.
    infeasible = int((m > r).sum())
    m = np.minimum(m, r)

    b0 = r - m                                                # existing buffer = baseline
    delta = np.maximum(0.0, rho - m)                          # primary delay per scenario

    return {
        "train_code": train_code, "route": route,
        "stops": list(stops_ref), "n_segments": len(r), "n_scenarios": len(dates),
        "dates": dates,
        "m": m, "r": r, "b0": b0, "B": float(b0.sum()),
        "delta": delta, "l0": np.array(l0, dtype=float),
        "pct": pct, "segments_schedule_infeasible": infeasible,
        "dropped_days": dict(dropped),
    }


# ----------------------------------------------------------------- the program

def weight_vector(kind, n):
    """w_i. Two defensible extremes, and no third option by design.

    Boardings are not in the feed, so any 'realistic' weighting would be invented numbers
    inside the objective — the exact objection that ruled out the delay-management
    formulation (D59). Both extremes are run and both published; they bracket the answer.
    """
    if kind == "terminus":
        w = np.zeros(n)
        w[-1] = 1.0
    elif kind == "uniform":
        w = np.ones(n)
    else:
        raise ValueError(f"unknown weighting {kind!r}; use 'terminus' or 'uniform'")
    return w


def solve(delta, l0, B, w, cap=None):
    """Solve the LP. Returns (buffers, lp_objective, result).

    Variable layout, flat, because linprog takes one vector:

        x[0 : n]                       b_1 .. b_n
        x[n + s*n : n + (s+1)*n]       L_1^s .. L_n^s      for scenario s

    Built sparse: C1 contributes n*S rows with at most 3 non-zeros each, so the dense form
    is almost entirely zeros and grows as (n*S)^2.
    """
    S, n = delta.shape
    if np.any(w < 0):
        # Not defensive padding. A negative weight makes the objective REWARD a larger
        # L_i, and since C1 and C2 are only lower bounds the solver will inflate it to
        # collect that reward. The lateness variables then stop representing lateness and
        # the buffer vector is optimal for a problem nobody posed. Rewarding earliness
        # needs a separate non-negative variable, not a sign flip.
        raise ValueError("negative weights break the linearisation; see D60")
    if w.sum() <= 0:
        raise ValueError("at least one weight must be positive")

    nvar = n + n * S

    def L(i, s):
        return n + s * n + i

    # --- objective: (1/S) * sum_s sum_i w_i L_i^s ; buffers cost nothing directly
    c = np.zeros(nvar)
    for s in range(S):
        c[n + s * n: n + (s + 1) * n] = w / S

    # --- C1, as A_ub x <= b_ub:
    #        L_i^s >= L_{i-1}^s + d_i^s - b_i
    #     -> -L_i^s + L_{i-1}^s - b_i <= -d_i^s
    #     with L_0^s = l0^s a constant, folded into the right-hand side at i = 0.
    rows, cols, vals, rhs = [], [], [], []
    k = 0
    for s in range(S):
        for i in range(n):
            rows.append(k); cols.append(L(i, s)); vals.append(-1.0)
            rows.append(k); cols.append(i);       vals.append(-1.0)
            if i == 0:
                rhs.append(-delta[s, 0] - l0[s])
            else:
                rows.append(k); cols.append(L(i - 1, s)); vals.append(1.0)
                rhs.append(-delta[s, i])
            k += 1

    # --- C3: sum_i b_i <= B
    for i in range(n):
        rows.append(k); cols.append(i); vals.append(1.0)
    rhs.append(B)
    k += 1

    A = coo_matrix((vals, (rows, cols)), shape=(k, nvar)).tocsr()

    # --- C2 and C4 as bounds. L_i^s >= 0 is C2; b_i in [0, cap_i] is C4.
    caps = [None] * n if cap is None else [float(cap[i]) for i in range(n)]
    bounds = [(0.0, caps[i]) for i in range(n)] + [(0.0, None)] * (n * S)

    res = linprog(c, A_ub=A, b_ub=np.array(rhs), bounds=bounds, method="highs")
    if not res.success:
        raise RuntimeError(f"LP did not solve: {res.message}")
    return res.x[:n], float(res.fun), res


def simulate(b, delta, l0, w):
    """The TRUE nonlinear recursion, per scenario. Not used to optimise — used to check the
    LP against, and to evaluate any buffer vector on held-out days.

        L_i = max(0, L_{i-1} + d_i - b_i)

    Returns the weighted lateness of each scenario, shape (S,), so a bootstrap can resample
    whole days.
    """
    S, n = delta.shape
    L = l0.astype(float).copy()
    cost = np.zeros(S)
    for i in range(n):
        L = np.maximum(0.0, L + delta[:, i] - b[i])
        cost += w[i] * L
    return cost


def bootstrap_days(cost_a, cost_b, reps=10_000, seed=0):
    """Paired bootstrap of mean(cost_a) - mean(cost_b), resampling whole DAYS.

    The day is the independent unit, not the stop-arrival. Within one day, lateness at
    consecutive stops is strongly correlated — that correlation IS the propagation this
    model describes — so resampling stops would treat dependent observations as independent
    and understate the variance, giving a confident answer that is not warranted.

    The number that governs the statistics is therefore the number of days. A 29-stop
    service with 32 days has 896 lateness variables and 32 independent scenarios.
    """
    rng = np.random.default_rng(seed)
    S = len(cost_a)
    idx = rng.integers(0, S, size=(reps, S))
    diff = cost_a[idx].mean(axis=1) - cost_b[idx].mean(axis=1)
    return {"point": float(cost_a.mean() - cost_b.mean()),
            "lo": float(np.percentile(diff, 2.5)),
            "hi": float(np.percentile(diff, 97.5)),
            "n_days": S,
            "significant": bool(np.percentile(diff, 97.5) < 0)}


# ----------------------------------------------------------------- evaluation

def evaluate(inst, weighting="terminus", cap_alpha=None, holdout=0.5, seed=0):
    """Fit buffers on early days, evaluate on later ones, against the timetable's own.

    Split is TEMPORAL, matching D25: fitting and evaluating on the same days measures the
    ability to fit noise. Early days fit, later days evaluate.
    """
    n, S = inst["n_segments"], inst["n_scenarios"]
    w = weight_vector(weighting, n)
    cut = int(S * (1 - holdout))
    if cut < 3 or S - cut < 3:
        raise ValueError(f"{S} scenarios is too few to split")

    d_fit, d_test = inst["delta"][:cut], inst["delta"][cut:]
    l_fit, l_test = inst["l0"][:cut], inst["l0"][cut:]
    cap = None if cap_alpha is None else cap_alpha * inst["m"]

    b_opt, lp_obj, _ = solve(d_fit, l_fit, inst["B"], w, cap)

    # The linearisation, checked rather than asserted in prose: the LP's objective must equal
    # the true recursion replayed on the same data. If C1/C2 were too loose the LP would
    # report a cost the real dynamics cannot achieve, and this would separate.
    sim_fit = simulate(b_opt, d_fit, l_fit, w).mean()
    tightness_gap = abs(lp_obj - sim_fit)

    cost_opt = simulate(b_opt, d_test, l_test, w)
    cost_base = simulate(inst["b0"], d_test, l_test, w)
    boot = bootstrap_days(cost_opt, cost_base, seed=seed)

    binding = None
    if cap is not None:
        binding = int(np.sum(b_opt > cap - 1e-6))

    return {
        "weighting": weighting, "cap_alpha": cap_alpha,
        "fit_days": cut, "test_days": S - cut,
        "lp_objective_fit": lp_obj, "simulated_fit": float(sim_fit),
        "tightness_gap": float(tightness_gap),
        "baseline_test": float(cost_base.mean()),
        "optimised_test": float(cost_opt.mean()),
        "improvement_sec": float(cost_base.mean() - cost_opt.mean()),
        "improvement_pct": (float(100 * (cost_base.mean() - cost_opt.mean())
                                  / cost_base.mean()) if cost_base.mean() > 0 else None),
        "bootstrap": boot,
        "cap_binding_segments": binding,
        "b_opt": b_opt, "b0": inst["b0"],
    }


def report(inst, results):
    W = 78
    print("=" * W)
    print(f"{inst['train_code']}  {inst['route']}")
    print("=" * W)
    print(f"  {inst['n_segments']} segments, {inst['n_scenarios']} complete days "
          f"({inst['dates'][0]} .. {inst['dates'][-1]})")
    print(f"  total buffer to redistribute: {inst['B']:.0f}s "
          f"(median {np.median(inst['b0']):.0f}s per segment, "
          f"max {inst['b0'].max():.0f}s)")
    if inst["segments_schedule_infeasible"]:
        print(f"  ! {inst['segments_schedule_infeasible']} segment(s) scheduled tighter "
              f"than the {inst['pct']}th percentile of observed runs; m clipped to r there")
    if inst["dropped_days"]:
        print(f"  days dropped: {inst['dropped_days']}")

    for r in results:
        cap = "uncapped" if r["cap_alpha"] is None else f"cap {r['cap_alpha']:.2f}*m"
        print(f"\n  --- {r['weighting']} weighting, {cap} ---")
        print(f"  fit on {r['fit_days']} days, evaluated on {r['test_days']} held out")
        print(f"  baseline (timetable) {r['baseline_test']:>9.1f}s")
        print(f"  optimised            {r['optimised_test']:>9.1f}s")
        b = r["bootstrap"]
        print(f"  difference           {r['improvement_sec']:>9.1f}s"
              f"  ({r['improvement_pct']:.1f}%)" if r["improvement_pct"] is not None else "")
        print(f"  95% CI on the difference: [{b['lo']:.1f}, {b['hi']:.1f}]s "
              f"over {b['n_days']} resampled days")
        print(f"  -> {'SIGNIFICANT improvement' if b['significant'] else 'NOT distinguishable from noise'}")
        if r["cap_binding_segments"] is not None:
            print(f"  cap binds on {r['cap_binding_segments']} of {inst['n_segments']} segments")
        print(f"  linearisation check: LP objective {r['lp_objective_fit']:.3f} vs "
              f"simulated {r['simulated_fit']:.3f}  (gap {r['tightness_gap']:.2e})")


# ----------------------------------------------------------------- checks

def _self_check():
    rng = np.random.default_rng(0)
    S, n = 40, 6

    # --- the linearisation, on random data. This is the executable form of the argument in
    # the module docstring: solve the LP, replay the answer through the true recursion, and
    # require them to agree. A too-loose relaxation shows up as LP objective < simulated.
    delta = rng.gamma(2.0, 30.0, size=(S, n))
    l0 = rng.gamma(1.0, 20.0, size=S)
    for kind in ("uniform", "terminus"):
        w = weight_vector(kind, n)
        b, obj, _ = solve(delta, l0, B=300.0, w=w)
        sim = simulate(b, delta, l0, w).mean()
        assert abs(obj - sim) < 1e-6, f"{kind}: LP {obj} != recursion {sim}"
        assert b.sum() <= 300.0 + 1e-6, "budget violated"
        assert (b >= -1e-9).all(), "negative buffer"

    # terminus-only is the case where the naive "objective pushes it down" argument fails,
    # because w_i = 0 at every intermediate stop. It is still tight, which the assert above
    # has just demonstrated -- that is the whole point of testing both weightings here.

    # --- a negative weight is refused rather than silently producing a wrong optimum
    try:
        solve(delta, l0, 300.0, np.array([1.0, -1.0, 1.0, 1.0, 1.0, 1.0]))
        raise AssertionError("negative weight should have been refused")
    except ValueError as e:
        assert "negative weights" in str(e)

    # --- more budget cannot make the optimum worse (the feasible set only grows)
    w = weight_vector("uniform", n)
    _, small, _ = solve(delta, l0, 100.0, w)
    _, large, _ = solve(delta, l0, 400.0, w)
    assert large <= small + 1e-9, "extra budget made it worse"

    # --- with budget enough to absorb everything, lateness goes to zero
    huge = float((delta.sum(axis=1) + l0).max()) * n
    b, obj, _ = solve(delta, l0, huge, w)
    assert obj < 1e-6, f"unbounded budget should remove all lateness, got {obj}"

    # --- the cap is respected, and binds when it is tight
    cap = np.full(n, 5.0)
    b, _, _ = solve(delta, l0, 300.0, w, cap=cap)
    assert (b <= 5.0 + 1e-6).all(), "cap violated"
    assert b.sum() <= 30.0 + 1e-6

    # --- simulate matches a hand-computed recursion
    d = np.array([[10.0, 0.0, 50.0]])
    b = np.array([4.0, 0.0, 20.0])
    w1 = np.ones(3)
    # L1 = max(0, 0+10-4) = 6 ; L2 = max(0, 6+0-0) = 6 ; L3 = max(0, 6+50-20) = 36
    assert simulate(b, d, np.zeros(1), w1)[0] == 6 + 6 + 36

    # --- bootstrap: identical costs cannot be significant; a large gap must be
    same = np.arange(30, dtype=float)
    assert not bootstrap_days(same, same.copy())["significant"]
    assert bootstrap_days(same, same + 500.0)["significant"]

    print("buffer_lp.py self-check passed")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--train", help="train code, e.g. E828")
    ap.add_argument("--parsed", type=Path, default=DEFAULT_PARSED)
    ap.add_argument("--min-days", type=int, default=15)
    ap.add_argument("--holdout", type=float, default=0.5)
    ap.add_argument("--cap-alpha", type=float, default=None,
                    help="per-segment cap as a multiple of the minimum running time; "
                         "omit to run uncapped, which is the reference")
    ap.add_argument("--json", type=Path, help="write the results here")
    args = ap.parse_args()

    if not args.train:
        print("--train is required")
        return 2
    inst = build_instance(args.train, args.parsed, args.min_days)
    if inst is None:
        print(f"{args.train}: not enough complete days")
        return 2

    results = [evaluate(inst, weighting=k, cap_alpha=args.cap_alpha,
                        holdout=args.holdout)
               for k in ("terminus", "uniform")]
    report(inst, results)

    if args.json:
        out = {"train_code": inst["train_code"], "route": inst["route"],
               "stops": inst["stops"], "n_scenarios": inst["n_scenarios"],
               "dates": inst["dates"], "B": inst["B"],
               "m": inst["m"].tolist(), "r": inst["r"].tolist(),
               "b0": inst["b0"].tolist(),
               "results": [{k: (v.tolist() if isinstance(v, np.ndarray) else v)
                            for k, v in r.items()} for r in results]}
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(out, indent=2), encoding="utf-8")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    if "--self-check" in sys.argv:
        _self_check()
    else:
        raise SystemExit(main())
