"""
diff_parallel.py — compare the Lambda poller against the local one (D36).

Read-only. Sync the Lambda side down first:

    aws s3 sync s3://<bucket>/parallel/lambda/ data/live/lambda/ --region eu-west-1
    python scripts\\diff_parallel.py

The local poller is the control and is never modified, so it has gaps: the laptop gets
shut down overnight and for reboots. A gap is not a miss, and conflating the two would
fail the cutover for the wrong reason. So the comparison only runs inside windows where
local was demonstrably up, and covered hours are reported next to every percentage.

Local cycle metadata is what proves local was running. Each cycle writes
data/live/cycles/{day}/{ts}.json, so the set of those timestamps is the uptime record.
Consecutive cycles more than --gap-minutes apart start a new window. Quiet hours produce
no cycles on either side, so they fall out as gaps automatically and are excluded from
active hours rather than counted as downtime.

Byte equality is not the bar (D36): the two pollers hit the API at different instants and
the answer changes between them.
"""

import argparse
import gzip
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parent.parent
DUBLIN = ZoneInfo("Europe/Dublin")
TS_RE = re.compile(r"(\d{8}T\d{6}Z)")
MONTHS = ("jan", "feb", "mar", "apr", "may", "jun",
          "jul", "aug", "sep", "oct", "nov", "dec")
QUIET_START, QUIET_END = dtime(0, 30), dtime(5, 30)


def parse_ts(s):
    return datetime.strptime(s, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)


def iso_from_traindate(s):
    try:
        d, m, y = s.strip().split()
        return f"{int(y):04d}-{MONTHS.index(m[:3].lower()) + 1:02d}-{int(d):02d}"
    except (ValueError, AttributeError):
        return None


def cycle_of(row):
    """Cycle timestamp out of source_file. Both sinks embed it; the paths differ."""
    m = TS_RE.search(row.get("source_file", ""))
    return parse_ts(m.group(1)) if m else None


def load_local(exp_dir, cyc_dir):
    """Productive local cycles only, plus how many were dropped.

    A cycle that recorded nothing is not coverage. On 2026-08-24 an ISP interception page
    returned HTTP 200 for seven hours: the poller kept cycling and writing cycle files,
    the body guard correctly rejected every response, and zero records were produced.
    Counting those 78 cycles as uptime would claim seven hours of comparison against a
    window that holds no local events at all.
    """
    cycles, barren = [], 0
    for p in sorted(cyc_dir.glob("*/*.json")):
        if not TS_RE.fullmatch(p.stem):
            continue
        try:
            meta = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if meta.get("records", 0) > 0 and meta.get("status") != "failed":
            cycles.append(parse_ts(p.stem))
        else:
            barren += 1
    rows = []
    for f in sorted(exp_dir.glob("*.jsonl")):
        rows += [json.loads(line) for line in f.open(encoding="utf-8") if line.strip()]
    return sorted(cycles), rows, barren


def load_lambda(root):
    cycles = sorted(parse_ts(p.stem) for p in (root / "cycles").glob("date=*/*.json")
                    if TS_RE.fullmatch(p.stem))
    rows = []
    for f in sorted((root / "expected").glob("date=*/*.jsonl.gz")):
        text = gzip.decompress(f.read_bytes()).decode("utf-8")
        rows += [json.loads(line) for line in text.splitlines() if line.strip()]
    return cycles, rows


def windows(cycles, gap_minutes, trim):
    """Runs of cycles with no gap longer than gap_minutes, trimmed at both ends.

    Trimming drops cycles from each edge: an event first seen right at a boundary may
    legitimately be caught by one poller and not the other, which is a boundary artefact
    rather than a disagreement.
    """
    if not cycles:
        return []
    gap = timedelta(minutes=gap_minutes)
    runs, cur = [], [cycles[0]]
    for prev, nxt in zip(cycles, cycles[1:]):
        if nxt - prev <= gap:
            cur.append(nxt)
        else:
            runs.append(cur)
            cur = [nxt]
    runs.append(cur)

    out = []
    for run in runs:
        kept = run[trim:len(run) - trim] if len(run) > 2 * trim else []
        if len(kept) >= 2:
            out.append((kept[0], kept[-1]))
    return out


def active_period(day, now):
    """Dublin 05:30 to next 00:30, the window both pollers are meant to be awake."""
    d = datetime.fromisoformat(day)
    start = datetime.combine(d, QUIET_END, DUBLIN).astimezone(timezone.utc)
    end = datetime.combine(d + timedelta(days=1), QUIET_START, DUBLIN).astimezone(timezone.utc)
    return start, min(end, now)


def overlap_hours(a, b):
    lo, hi = max(a[0], b[0]), min(a[1], b[1])
    return max(0.0, (hi - lo).total_seconds() / 3600)


def in_windows(ts, wins):
    return ts is not None and any(a <= ts <= b for a, b in wins)


def events_by_group(rows):
    out = defaultdict(set)
    for r in rows:
        day = iso_from_traindate(r.get("Traindate", ""))
        if day:
            out[r.get("station_group", "")].add(
                (day, r["Traincode"].strip().upper(), r["Stationcode"].strip().upper()))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--local-expected", type=Path, default=REPO / "data/live/expected")
    ap.add_argument("--local-cycles", type=Path, default=REPO / "data/live/cycles")
    ap.add_argument("--lambda-root", type=Path, default=REPO / "data/live/lambda")
    ap.add_argument("--gap-minutes", type=float, default=15,
                    help="gap that ends a local coverage window (default: 15)")
    ap.add_argument("--trim-cycles", type=int, default=1,
                    help="cycles dropped from each window edge (default: 1)")
    # Half the poll interval. The two pollers run on independent 5-minute schedules with
    # a fixed phase offset (85s when measured), so a tight threshold never pairs anything
    # and the check silently measures nothing.
    ap.add_argument("--pair-seconds", type=int, default=150)
    ap.add_argument("--min-hours", type=float, default=2.0,
                    help="below this many covered hours, results are edge-dominated "
                         "and reported as provisional (default: 2)")
    ap.add_argument("--overlap-target", type=float, default=99.5)
    args = ap.parse_args()

    if not args.lambda_root.exists():
        print(f"no Lambda data at {args.lambda_root}. Run the aws s3 sync first.")
        return 2

    lc, lrows, barren = load_local(args.local_expected, args.local_cycles)
    mc, mrows = load_lambda(args.lambda_root)
    if not lc or not mc:
        print(f"not enough productive cycles yet: local {len(lc)}, lambda {len(mc)}")
        return 2

    now = datetime.now(timezone.utc)
    W = 78

    # The parallel run begins when the Lambda begins. Local cycles older than that are
    # pre-run smoke tests, and counting their days as active hours would drag coverage
    # down for the whole window against days that were never part of it.
    run_start = mc[0]
    dropped = sum(1 for c in lc if c < run_start)
    lc = [c for c in lc if c >= run_start]
    lrows = [r for r in lrows if (cycle_of(r) or run_start) >= run_start]
    if not lc:
        print(f"no local cycles since the run began at {run_start:%Y-%m-%d %H:%M}Z")
        return 2

    print(f"local  {len(lc):>5} cycles  {lc[0]:%Y-%m-%d %H:%M} .. {lc[-1]:%Y-%m-%d %H:%M}Z")
    print(f"lambda {len(mc):>5} cycles  {mc[0]:%Y-%m-%d %H:%M} .. {mc[-1]:%Y-%m-%d %H:%M}Z")
    if dropped:
        print(f"       {dropped} local cycles before the run start were excluded")
    if barren:
        print(f"       {barren} local cycles recorded zero records and are excluded from")
        print(f"       coverage — the poller was up but producing nothing")

    stale_min = (lc[-1] - mc[-1]).total_seconds() / 60
    if stale_min > 30:
        print(f"\n  ! lambda data is {stale_min:.0f} min behind local. Re-run the s3 sync,")
        print("    otherwise this is a diff against a stale copy.")

    wins = windows(lc, args.gap_minutes, args.trim_cycles)

    print("\n" + "=" * W)
    print("COVERAGE — the comparison runs only where local was up")
    print("=" * W)
    print(f"  {'date':<12}{'windows':>8}{'covered h':>11}{'active h':>10}{'coverage':>10}")
    print("  " + "-" * 51)
    days = sorted({d.astimezone(DUBLIN).date().isoformat() for d in lc + mc})
    tot_cov = tot_act = 0.0
    for day in days:
        period = active_period(day, now)
        act = max(0.0, (period[1] - period[0]).total_seconds() / 3600)
        cov = sum(overlap_hours(w, period) for w in wins)
        n = sum(1 for w in wins if overlap_hours(w, period) > 0)
        tot_cov += cov
        tot_act += act
        pct = f"{100 * cov / act:.0f}%" if act else "-"
        print(f"  {day:<12}{n:>8}{cov:>11.1f}{act:>10.1f}{pct:>10}")
    print("  " + "-" * 51)
    total_pct = f"{100 * tot_cov / tot_act:.0f}%" if tot_act else "-"
    print(f"  {'TOTAL':<12}{len(wins):>8}{tot_cov:>11.1f}{tot_act:>10.1f}{total_pct:>10}")
    print("\n  Gaps are laptop downtime, not missing data. Everything below is measured")
    print("  inside the covered hours only.")

    # Window edges are local cycle timestamps, so local necessarily has a cycle at each
    # end while the Lambda's may fall just outside. The two samplers also run on
    # independent schedules with a fixed phase offset. Over a short window that skews
    # everything downstream; over a full day the difference is one cycle in ~144.
    n_local = sum(1 for c in lc if in_windows(c, wins))
    n_lambda = sum(1 for c in mc if in_windows(c, wins))
    print(f"  cycles inside windows: local {n_local}, lambda {n_lambda}")
    if n_local != n_lambda:
        skew = abs(n_local - n_lambda) / max(n_local, n_lambda) * 100
        print(f"  sampling skew {skew:.0f}% — expect that much one-sided difference "
              f"below, independent of any real disagreement")

    if not wins:
        need = 2 * args.trim_cycles + 2
        longest = 0
        gap = timedelta(minutes=args.gap_minutes)
        run = 1
        for prev, nxt in zip(lc, lc[1:]):
            run = run + 1 if nxt - prev <= gap else 1
            longest = max(longest, run)
        longest = max(longest, 1)
        print(f"\n  No comparison windows yet. Longest unbroken local run is {longest} "
              f"cycles;")
        print(f"  {need} are needed ({args.trim_cycles} trimmed from each edge, 2 left "
              f"to span a window).")
        print(f"  At a 5-minute interval that is about {need * 5} minutes of continuous "
              f"local uptime.")
        return 0

    lw = [r for r in lrows if in_windows(cycle_of(r), wins)]
    mw = [r for r in mrows if in_windows(cycle_of(r), wins)]
    if not lw or not mw:
        print(f"\nno records inside windows (local {len(lw)}, lambda {len(mw)})")
        return 2

    print("\n" + "=" * W)
    print("SCHEMA")
    print("=" * W)
    lk = {frozenset(r) for r in lw}
    mk = {frozenset(r) for r in mw}
    identical = lk == mk
    print(f"  identical: {identical}   ({len(next(iter(lk)))} fields)")
    if not identical:
        print(f"  differing names: {set().union(*lk) ^ set().union(*mk)}")

    le, me = events_by_group(lw), events_by_group(mw)
    print("\n" + "=" * W)
    print("EVENT OVERLAP — (date, train, station) triples")
    print("=" * W)
    print(f"  {'station_group':<26}{'both':>8}{'local only':>12}"
          f"{'lambda only':>13}{'overlap':>9}")
    print("  " + "-" * 68)
    only_l = only_m = both = 0
    groups = sorted(set(le) | set(me),
                    key=lambda g: -len(le.get(g, set()) | me.get(g, set())))
    for g in groups:
        x, y = le.get(g, set()), me.get(g, set())
        b, union = len(x & y), len(x | y)
        only_l += len(x - y)
        only_m += len(y - x)
        both += b
        share = f"{100 * b / union:.1f}%" if union else "-"
        print(f"  {g or '(none)':<26}{b:>8}{len(x - y):>12}{len(y - x):>13}{share:>9}")
    union_all = both + only_l + only_m
    overlap_pct = 100 * both / union_all if union_all else 0
    print("  " + "-" * 68)
    print(f"  {'TOTAL':<26}{both:>8}{only_l:>12}{only_m:>13}{overlap_pct:>8.1f}%")

    print("\n" + "=" * W)
    print("VOLUME PARITY — records per station per hour")
    print("=" * W)
    def per_hour(rows):
        c = Counter()
        for r in rows:
            t = cycle_of(r)
            if t:
                c[(r["station_code"], t.strftime("%Y-%m-%dT%H"))] += 1
        return c
    lh, mh = per_hour(lw), per_hour(mw)
    shared = set(lh) & set(mh)
    if shared:
        devs = [abs(lh[k] - mh[k]) / max(lh[k], mh[k]) for k in shared]
        print(f"  {len(shared)} shared station-hours, mean deviation "
              f"{sum(devs) / len(devs) * 100:.1f}%, worst {max(devs) * 100:.1f}%")
    else:
        print("  no shared station-hours")

    print("\n" + "=" * W)
    print(f"VALUE AGREEMENT — cycle pairs within {args.pair_seconds}s")
    print("=" * W)
    lby, mby = defaultdict(dict), defaultdict(dict)
    for rows, dest in ((lw, lby), (mw, mby)):
        for r in rows:
            day = iso_from_traindate(r.get("Traindate", ""))
            t = cycle_of(r)
            if day and t:
                key = (day, r["Traincode"].strip().upper(),
                       r["Stationcode"].strip().upper())
                dest[t][key] = r.get("Exparrival", "")
    offsets = [min(abs((t - lt).total_seconds()) for t in mby) for lt in lby] if mby else []
    if offsets:
        offsets.sort()
        print(f"  median phase offset between the two schedules: "
              f"{offsets[len(offsets) // 2]:.0f}s")

    agree = total = pairs = 0
    for lt, lmap in lby.items():
        near = [t for t in mby if abs((t - lt).total_seconds()) <= args.pair_seconds]
        if not near:
            continue
        pairs += 1
        mmap = mby[min(near, key=lambda t: abs((t - lt).total_seconds()))]
        for k in set(lmap) & set(mmap):
            total += 1
            agree += lmap[k] == mmap[k]
    rate = f"{100 * agree / total:.1f}%" if total else "-"
    print(f"  {pairs} paired cycles, {total} shared events, {rate} identical Exparrival")
    if offsets and not pairs:
        print(f"  ! nothing paired: the schedules never land within {args.pair_seconds}s "
              f"of each other.")
        print("    Raise --pair-seconds, but expect lower agreement as the operator")
        print("    legitimately revises its estimate across a wider gap.")

    print("\n" + "=" * W)
    ok = identical and overlap_pct >= args.overlap_target
    if tot_cov < args.min_hours:
        print(f"PROVISIONAL — only {tot_cov:.1f} covered hours, below the "
              f"{args.min_hours:.0f}h floor.")
        print("  At this length the numbers are dominated by window-edge sampling, not")
        print("  by agreement. Not a pass or a fail; come back with a full day.")
    else:
        print(f"{'MEETS' if ok else 'DOES NOT MEET'} the D36 bar "
              f"(schema identical, overlap >= {args.overlap_target}%)")
        print(f"  measured over {tot_cov:.1f} covered hours")
    print("=" * W)
    return 0


if __name__ == "__main__":
    sys.exit(main())
