"""
validate_join.py — can live ExpectedArrival records be matched to movements records?

Everything downstream — the whole "we beat the operator" claim — rests on being able to
line up, for the same train at the same station on the same date, what Irish Rail
predicted against what actually happened. If that join is lossy, every comparison built
on it inherits the loss silently, and the bias is not random: whatever fails to join
fails for a reason, and that reason will correlate with line type.

So this checks the join before anything is built on it, and reports the rate per
`station_group`, because a 95% overall rate hiding 60% on weak-coverage lines is exactly
the failure this project is supposed to be honest about.

Read-only. Writes nothing.

Join key
--------
`(TrainDate, TrainCode, station)` — live `Stationcode` against movements `LocationCode`.

`TrainDate` rather than the partition date deliberately: it is the feed's own field on
both sides and carries the same meaning ("the date this service began"), so services
running past midnight line up without special handling.

Two units are reported and they answer different questions:

  events  distinct (date, train, station) triples. This is what the eventual comparison
          consumes — one prediction, one operator ETA, one actual. The number that matters.
  records raw live rows. Each event is polled repeatedly as the train approaches, which is
          the point (the operator revises its estimate), so this is inflated by poll count
          and is reported only to show how much observation each event carries.

Usage (PowerShell, from the repo root, venv active):

    python scripts\\validate_join.py
    python scripts\\validate_join.py --start 2026-08-01 --end 2026-08-02
"""

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import pyarrow.dataset as ds

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LIVE = REPO_ROOT / "data" / "live" / "expected"
DEFAULT_PARSED = REPO_ROOT / "data" / "parsed"
DEFAULT_CODES = REPO_ROOT / "data" / "codes.json"

MONTHS = ("jan", "feb", "mar", "apr", "may", "jun",
          "jul", "aug", "sep", "oct", "nov", "dec")


def traindate_to_iso(s: str):
    """'01 Aug 2026' -> '2026-08-01'. None if unparseable."""
    try:
        d, m, y = s.strip().split()
        return f"{int(y):04d}-{MONTHS.index(m[:3].lower()) + 1:02d}-{int(d):02d}"
    except (ValueError, AttributeError):
        return None


PLACEHOLDER = {"", "00:00", "00:00:00"}


def load_live(live_dir: Path, start, end):
    """Live rows grouped into events keyed by (iso_date, traincode, stationcode)."""
    events = defaultdict(lambda: {"records": 0, "group": "", "sched": set(),
                                  "eta": False})
    records = 0
    skipped_date = 0
    for path in sorted(live_dir.glob("*.jsonl")):
        for line in open(path, encoding="utf-8"):
            if not line.strip():
                continue
            r = json.loads(line)
            iso = traindate_to_iso(r.get("Traindate", ""))
            if iso is None:
                skipped_date += 1
                continue
            if (start and iso < start.isoformat()) or (end and iso > end.isoformat()):
                continue
            code = (r.get("Traincode") or "").strip().upper()
            stn = (r.get("Stationcode") or "").strip().upper()
            if not code or not stn:
                continue
            records += 1
            e = events[(iso, code, stn)]
            e["records"] += 1
            e["group"] = r.get("station_group", "") or e["group"]
            if r.get("Scharrival"):
                e["sched"].add(r["Scharrival"])
            # A real operator ETA at any poll makes the event comparable.
            if r.get("Exparrival", "") not in PLACEHOLDER:
                e["eta"] = True
    return events, records, skipped_date


def load_movements(parsed: Path):
    """Movements indexed for lookup: journey keys, and (journey, location) keys."""
    table = ds.dataset(parsed, format="parquet", partitioning="hive").to_table(
        columns=["TrainDate", "TrainCode", "LocationCode", "file_date",
                 "arrival_delay_sec", "AutoArrival"]).to_pydict()
    journeys = set()
    stops = {}
    dates_present = set()
    for td, tc, lc, fd, dl, au in zip(
            table["TrainDate"], table["TrainCode"], table["LocationCode"],
            table["file_date"], table["arrival_delay_sec"], table["AutoArrival"]):
        iso = traindate_to_iso(td or "")
        if iso is None:
            continue
        code = (tc or "").strip().upper()
        journeys.add((iso, code))
        stops[(iso, code, (lc or "").strip().upper())] = (dl, au)
        dates_present.add(fd)
    return journeys, stops, dates_present


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", type=Path, default=DEFAULT_LIVE)
    ap.add_argument("--parsed", type=Path, default=DEFAULT_PARSED)
    ap.add_argument("--codes", type=Path, default=DEFAULT_CODES)
    ap.add_argument("--start", type=date.fromisoformat)
    ap.add_argument("--end", type=date.fromisoformat)
    ap.add_argument("--threshold", type=float, default=95.0,
                    help="match rate below which the join is treated as unsafe "
                         "(default: 95.0)")
    args = ap.parse_args()

    if not args.live.exists():
        print(f"no live records at {args.live}")
        return 2
    if not args.parsed.exists():
        print(f"no parsed movements at {args.parsed} — run src\\parse_raw.py")
        return 2

    events, records, bad_dates = load_live(args.live, args.start, args.end)
    if not events:
        print("no live events in range")
        return 2
    journeys, stops, parsed_dates = load_movements(args.parsed)

    known_codes = set()
    if args.codes.exists():
        with open(args.codes, encoding="utf-8") as f:
            known_codes = {c.strip().upper()
                           for c in json.load(f).get("codes", {})}

    live_dates = sorted({k[0] for k in events})
    parsed_iso = {d for d in parsed_dates}
    missing_dates = [d for d in live_dates if d not in parsed_iso]

    W = 76
    print("=" * W)
    print("COVERAGE PRE-FLIGHT")
    print("=" * W)
    print(f"  live dates   : {', '.join(live_dates)}")
    print(f"  parsed dates : {min(parsed_dates)} .. {max(parsed_dates)} "
          f"({len(parsed_dates)} dates)")
    print(f"  live events  : {len(events):,} from {records:,} polled records "
          f"({records / len(events):.1f} polls/event)")
    if bad_dates:
        print(f"  ! {bad_dates} live rows had an unparseable Traindate")

    if missing_dates:
        print(f"\n  !! {len(missing_dates)} live date(s) are ABSENT from the parsed "
              f"movements: {', '.join(missing_dates)}")
        print("  !! Every event on those dates can only fail. The match rate below is")
        print("  !! measuring a collection gap, not a join defect. Backfill and parse")
        print("  !! those dates before reading anything into it.")

    # ---- the join
    matched, unmatched = 0, []
    for (iso, code, stn), e in events.items():
        if (iso, code, stn) in stops:
            matched += 1
        else:
            unmatched.append((iso, code, stn, e))

    rate = 100 * matched / len(events)
    print("\n" + "=" * W)
    print("MATCH RATE")
    print("=" * W)
    print(f"  events matched: {matched:,} / {len(events):,}   {rate:.2f}%")

    by_group = defaultdict(lambda: [0, 0])
    for (iso, code, stn), e in events.items():
        g = by_group[e["group"] or "(none)"]
        g[1] += 1
        if (iso, code, stn) in stops:
            g[0] += 1
    print(f"\n  {'station_group':<26} {'matched':>9} {'events':>9} {'rate':>8}")
    print("  " + "-" * 55)
    for g in sorted(by_group, key=lambda k: -by_group[k][1]):
        ok, tot = by_group[g]
        print(f"  {g:<26} {ok:>9,} {tot:>9,} {100 * ok / tot:>7.2f}%")

    # ---- usability: matching is necessary, not sufficient
    print("\n" + "=" * W)
    print("USABILITY — a matched event is only comparable with BOTH sides present")
    print("=" * W)
    use = defaultdict(Counter)
    for (iso, code, stn), e in events.items():
        c = use[e["group"] or "(none)"]
        c["events"] += 1
        dl, au = stops.get((iso, code, stn), (None, None))
        if e["eta"]:
            c["eta"] += 1
        if dl is not None:
            c["actual"] += 1
        if e["eta"] and dl is not None:
            c["usable"] += 1
            if au == "1":
                c["auto"] += 1
    print(f"\n  {'station_group':<26}{'events':>8}{'ETA':>8}{'actual':>8}"
          f"{'usable':>8}{'auto=1':>8}{'usable%':>9}")
    print("  " + "-" * 75)
    grand = Counter()
    for g in sorted(use, key=lambda k: -use[k]["events"]):
        c = use[g]
        grand.update(c)
        print(f"  {g:<26}{c['events']:>8,}{c['eta']:>8,}{c['actual']:>8,}"
              f"{c['usable']:>8,}{c['auto']:>8,}"
              f"{100 * c['usable'] / c['events']:>8.1f}%")
    print("  " + "-" * 75)
    print(f"  {'TOTAL':<26}{grand['events']:>8,}{grand['eta']:>8,}{grand['actual']:>8,}"
          f"{grand['usable']:>8,}{grand['auto']:>8,}"
          f"{100 * grand['usable'] / grand['events']:>8.1f}%")
    print("\n  ETA     = the operator issued a real ExpectedArrival at some poll")
    print("  actual  = movements recorded an arrival, so there is something to score against")
    print("  usable  = both. Only these can enter a comparison.")
    print("  auto=1  = of those, the ones whose label is machine-captured rather than")
    print("            possibly an echoed schedule (label-quality.md).")

    usable_pct = 100 * grand["usable"] / grand["events"] if grand["events"] else 0
    thin = [g for g in use if use[g]["auto"] < 200]
    if thin:
        print(f"\n  ! thin for any per-group claim (<200 trustworthy events): "
              f"{', '.join(sorted(thin))}")

    # ---- diagnosis
    print("\n" + "=" * W)
    print("DIAGNOSIS OF UNMATCHED EVENTS")
    print("=" * W)
    if not unmatched:
        print("  none — every live event found its movements record.")
    else:
        reasons = Counter()
        examples = defaultdict(list)
        for iso, code, stn, e in unmatched:
            if iso not in parsed_iso:
                why = "date not in parsed movements (collection gap)"
            elif (iso, code) not in journeys:
                why = ("train code absent from codes.json (harvest gap)"
                       if code not in known_codes
                       else "code known but no movements that date (service did not run,"
                            " or backfill missing)")
            else:
                why = "journey present but station not on its route"
            reasons[why] += 1
            if len(examples[why]) < 4:
                examples[why].append(f"{iso} {code}@{stn}")
        for why, n in reasons.most_common():
            print(f"\n  {n:,} events ({100 * n / len(events):.2f}% of all) — {why}")
            print(f"     e.g. {', '.join(examples[why])}")

    print("\n" + "=" * W)
    if rate >= args.threshold and not missing_dates:
        print(f"JOIN PASSES — {rate:.2f}% >= {args.threshold:.0f}%.")
        print(f"But only {usable_pct:.1f}% of matched events are actually comparable, and")
        print("the join rate says nothing about that. Both endpoints are views of the same")
        print("timetable, so a high match rate is close to guaranteed once the codes and")
        print("dates line up — read the usability table above, not this number.")
    else:
        print(f"DO NOT BUILD ON THIS JOIN YET — {rate:.2f}% against a "
              f"{args.threshold:.0f}% threshold.")
        print("Fix the causes above first; a lossy join biases every downstream number,")
        print("and it does not lose events at random.")
    print("=" * W)
    return 0


if __name__ == "__main__":
    sys.exit(main())
