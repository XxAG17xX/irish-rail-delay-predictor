"""
features.py — the model's feature set, defined once.

This existed as two independent copies: one in `train_quantile.py` and one in
`compare_to_operator.py`. They drifted, and the drift was not cosmetic — the trained
artifact carried a feature the comparison deliberately excluded, so the saved model
could not have served a live request. Nothing detected that; it took a human noticing.
Two lists that must agree, maintained separately, will diverge again. Hence one file.

## The rule that decides membership

**Every feature here must be computable at prediction time.**

At the moment of a prediction you know: the schedule (published months ahead), and every
stop the train has *already reported*. You do not know anything about stops it has yet to
reach. A feature that needs the completed journey can be computed in training and not in
production, which produces a model that validates well and cannot be deployed.

## Deliberately excluded

`horizon_observed_stops` — the number of *reporting* stops between the vantage and the
target. build_examples.py can compute it because it sees the finished journey. A live
request cannot: you know how many stops lie ahead from the timetable, but not which of
them will report. It is retained as a column in the examples Parquet (it is a fact about
the data, and harmless there) but never enters the model.

Cost of excluding it: 0.28% of gain in the 13-feature fit. `horizon_route_stops` carries
the same information in a form that is actually knowable.

`ExpectedArrival` — the operator baseline being compared against. Feeding it in makes the
comparison meaningless. See CLAUDE.md leakage rules.

`TrainCode` — identity, not situation. A service launched next March arrives as an unknown
category. See feature-ideas.md.
"""

NUMERIC = [
    "current_delay_sec",      # delay at the vantage stop — expected to dominate
    "prev_delay_sec",         # one observed stop earlier
    "prev2_delay_sec",        # two observed stops earlier
    "horizon_route_stops",    # stops from vantage to target, from the timetable
    "horizon_sched_sec",      # scheduled seconds from vantage to target
    "vantage_hour",
    "vantage_minute_of_day",
]

CATEGORICAL = [
    "day_of_week",
    "vantage_location",
    "target_location",
    "TrainOrigin",            # no `line` field exists; origin->destination is the
    "TrainDestination",       # closest available proxy for a route/corridor
]

FEATURES = NUMERIC + CATEGORICAL

# Present in the examples Parquet, deliberately not model inputs. Listed so the reason
# survives next to the code rather than only in a commit message.
EXCLUDED = {
    "horizon_observed_stops": "not computable at prediction time",
    "ExpectedArrival": "the baseline being compared against",
    "TrainCode": "identity, not situation",
}


def featurise(stops, vi, ti, dow, vocabs):
    """Build one model input row: predicting stop `ti` from vantage `vi`.

    Lives here rather than in a caller so the offline comparison and the live API cannot
    drift apart. They already did once with the feature list itself (D35), producing a
    trained artifact that could not have served a request, and nothing detected it.

    `stops` is the journey ordered by LocationOrder, each with order, loc, sched, delay,
    origin, destination. Only stops the train has already reported may appear before `vi`;
    enforcing that is the caller's job, because only the caller knows the cutoff.
    """
    import numpy as np

    v, s = stops[vi], stops[ti]
    observed = [k for k in range(vi) if stops[k]["delay"] is not None]
    row = {
        "current_delay_sec": v["delay"],
        "prev_delay_sec": stops[observed[-1]]["delay"] if observed else None,
        "prev2_delay_sec": stops[observed[-2]]["delay"] if len(observed) > 1 else None,
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
            vocab = vocabs[f]
            out[j] = vocab.get(val, len(vocab)) if val is not None else np.nan
        elif val is not None:
            out[j] = float(val)
    return out
