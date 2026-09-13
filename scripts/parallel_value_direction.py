"""
parallel_value_direction.py — is the Exparrival disagreement between the two pollers revision?

Read-only. Reproduces the measurement recorded in decisions.md D38, taken 2026-09-13. Needs
the parallel-run data on disk: data/live/expected, data/live/cycles and a synced
data/live/lambda/ (diff_parallel.py has the sync command).

    python scripts\\parallel_value_direction.py

Local fired about 85 seconds before the Lambda. If a disagreement is the operator revising
its estimate in that gap, the Lambda's newer value should reappear in local's NEXT capture of
the same event, and should not be what local showed on its PREVIOUS capture. Error or noise
has no direction, so it would appear on either side. A second, independent bound is how often
one poller's own value changes between captures five minutes apart, scaled to 85 seconds.
"""

import bisect
import sys
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import diff_parallel as dp  # noqa: E402

DAYS = {f"2026-08-{d:02d}" for d in range(23, 31)}   # the D36 parallel run
PAIR_SECONDS = 150                                    # diff_parallel's pairing window
ADJACENT = timedelta(minutes=10)


def series(rows):
    """Per poller: {cycle: {event: value}} and {event: [(cycle, value), ...] sorted}."""
    by_cycle, by_key = defaultdict(dict), defaultdict(list)
    for r in rows:
        day = dp.iso_from_traindate(r.get("Traindate", ""))
        t = dp.cycle_of(r)
        if not (day in DAYS and t):
            continue
        k = (day, r["Traincode"].strip().upper(), r["Stationcode"].strip().upper())
        v = (r.get("Exparrival") or "").strip()
        by_cycle[t][k] = v
        by_key[k].append((t, v))
    for k in by_key:
        by_key[k].sort()
    return by_cycle, by_key


def neighbour(seq, t, direction):
    times = [x[0] for x in seq]
    if direction > 0:
        j = bisect.bisect_right(times, t)
        return seq[j] if j < len(seq) else None
    i = bisect.bisect_left(times, t)
    return seq[i - 1] if i > 0 else None


def main():
    _, local_rows, _ = dp.load_local(dp.REPO / "data/live/expected", dp.REPO / "data/live/cycles")
    _, lambda_rows = dp.load_lambda(dp.REPO / "data/live/lambda")
    lc, lk = series(local_rows)
    mc, _ = series(lambda_rows)
    mtimes = sorted(mc)

    shared = agree = unjudged = 0
    counts = {"next": 0, "previous": 0, "both": 0, "neither": 0}
    for lt in sorted(lc):
        i = bisect.bisect_left(mtimes, lt)
        near = [mtimes[j] for j in (i - 1, i) if 0 <= j < len(mtimes)
                and abs((mtimes[j] - lt).total_seconds()) <= PAIR_SECONDS]
        if not near:
            continue
        mt = min(near, key=lambda t: abs((t - lt).total_seconds()))
        for k in set(lc[lt]) & set(mc[mt]):
            lv, mv = lc[lt][k], mc[mt][k]
            shared += 1
            if lv == mv:
                agree += 1
                continue
            nxt, prv = neighbour(lk[k], lt, +1), neighbour(lk[k], lt, -1)
            if nxt is None or nxt[0] - lt > ADJACENT:
                unjudged += 1
                continue
            f = nxt[1] == mv
            b = prv is not None and prv[1] == mv and lt - prv[0] <= ADJACENT
            counts["both" if f and b else "next" if f else "previous" if b else "neither"] += 1

    dis = shared - agree
    judged = sum(counts.values())
    print(f"{shared:,} shared events in cycle pairs within {PAIR_SECONDS}s, "
          f"{100 * agree / shared:.1f}% identical, {dis:,} disagreements")
    print(f"judged (local captured the event again within 10 min): {judged:,}; "
          f"unjudged: {unjudged:,}")
    for name, label in (("next", "local's NEXT value only (the revision signature)"),
                        ("previous", "local's PREVIOUS value only (a revision cannot do this)"),
                        ("both", "both local's previous and next value"),
                        ("neither", "never seen by local on either side")):
        print(f"  {counts[name]:>7,}  {100 * counts[name] / judged:5.1f}%  {label}")

    changed = pairs = 0
    for seq in lk.values():
        for (t0, v0), (t1, v1) in zip(seq, seq[1:]):
            if timedelta(minutes=4) <= t1 - t0 <= timedelta(minutes=6):
                pairs += 1
                changed += v0 != v1
    rate = changed / pairs
    print(f"control: local's own value changes between captures ~5 min apart "
          f"{100 * rate:.1f}% of the time over {pairs:,} pairs, which at 85s implies "
          f"~{100 * rate * 85 / 300:.1f}% disagreement from revision, "
          f"against {100 * dis / shared:.1f}% observed")


if __name__ == "__main__":
    main()
