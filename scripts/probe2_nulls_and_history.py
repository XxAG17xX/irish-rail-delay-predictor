"""
probe2_nulls_and_history.py — resolve three open questions the first probe left.

Q1. What fields does getTrainMovementsXML ACTUALLY return? (don't trust my guess)
Q2. Are nulls at already-passed locations permanent, or do they fill in later?
Q3. Does the API serve HISTORICAL dates? If yes, the whole project timeline changes.

Usage:
    python probe2_nulls_and_history.py                 # runs all three, picks a train itself
    python probe2_nulls_and_history.py A220            # investigate a specific train
"""

import sys
import xml.etree.ElementTree as ET
from datetime import date, timedelta

import requests

BASE = "http://api.irishrail.ie/realtime/realtime.asmx"
NS = "{http://api.irishrail.ie/realtime/}"
TIMEOUT = 20


def fmt_date(d):
    """API wants '25 jul 2026' — lowercase, day not zero-padded on some systems."""
    return d.strftime("%d %b %Y").lower()


def get_xml(path, **params):
    r = requests.get(f"{BASE}/{path}", params=params, timeout=TIMEOUT)
    r.raise_for_status()
    return ET.fromstring(r.content)


def tag(el):
    return el.tag.replace(NS, "")


def movements(train_code, d):
    root = get_xml("getTrainMovementsXML", TrainId=train_code, TrainDate=fmt_date(d))
    return root.findall(NS + "objTrainMovements")


def pick_a_running_train():
    root = get_xml("getCurrentTrainsXML")
    for t in root.findall(NS + "objTrainPositions"):
        st = t.find(NS + "TrainStatus")
        code = t.find(NS + "TrainCode")
        if st is not None and st.text == "R" and code is not None:
            return code.text.strip()
    return None


# ---------------------------------------------------------------- Q1: real schema
def q1_true_schema(train_code):
    print("\n" + "=" * 70)
    print(f"Q1. TRUE SCHEMA of getTrainMovementsXML (train {train_code})")
    print("=" * 70)
    stops = movements(train_code, date.today())
    if not stops:
        print("  no records returned")
        return
    print(f"  {len(stops)} records. Every field on the FIRST record:\n")
    for child in stops[0]:
        print(f"    {tag(child):22} = {child.text!r}")
    print("\n  Fields present across the whole response:")
    seen = []
    for s in stops:
        for c in s:
            if tag(c) not in seen:
                seen.append(tag(c))
    print("   ", ", ".join(seen))
    print("\n  -> If AutoArrival / AutoDepart exist, they tell you whether a time was")
    print("     captured automatically by signalling or entered by hand. That is the")
    print("     likely explanation for permanently-null timing points.")


# ------------------------------------------------- Q2: null semantics at passed stops
def q2_null_semantics(train_code):
    print("\n" + "=" * 70)
    print(f"Q2. NULL SEMANTICS — passed-but-unreported vs not-yet-happened ({train_code})")
    print("=" * 70)
    stops = movements(train_code, date.today())
    if not stops:
        print("  no records returned")
        return

    def f(s, name):
        el = s.find(NS + name)
        return el.text if el is not None else None

    # Find the furthest point with a recorded actual — everything before it has been passed.
    last_reported_idx = -1
    for i, s in enumerate(stops):
        if f(s, "Arrival") or f(s, "Departure"):
            last_reported_idx = i

    if last_reported_idx < 0:
        print("  train has no actuals yet (not departed). Re-run later.")
        return

    passed = stops[: last_reported_idx + 1]
    silent = [s for s in passed if not f(s, "Arrival") and not f(s, "Departure")]

    print(f"  furthest reported location: index {last_reported_idx} "
          f"({f(stops[last_reported_idx], 'LocationFullName')})")
    print(f"  locations the train has definitely passed: {len(passed)}")
    print(f"  of those, SILENT (both actuals null): {len(silent)}\n")

    if silent:
        print(f"  {'location':24} {'code':10} {'type':5} {'autoArr':8} {'autoDep':8}")
        for s in silent:
            print(f"  {(f(s,'LocationFullName') or '(blank)')[:24]:24} "
                  f"{str(f(s,'LocationCode')):10} "
                  f"{str(f(s,'LocationType')):5} "
                  f"{str(f(s,'AutoArrival')):8} "
                  f"{str(f(s,'AutoDepart')):8}")
        print("\n  -> These are CATEGORY 3: passed but unreported. Distinct from future nulls.")
        print("  -> ACTION: re-run this script tonight after the train terminates.")
        print("     If these are still null, they are permanent and should be excluded")
        print("     from the graph rather than imputed.")
    else:
        print("  every passed location reported. Category 3 may not apply to this train.")


# ---------------------------------------------------- Q3: is there any history at all?
def q3_history(train_code):
    print("\n" + "=" * 70)
    print("Q3. HISTORICAL AVAILABILITY — testing my claim that only today works")
    print("=" * 70)
    print(f"  using train code {train_code} as the probe (it may not have run on past")
    print("  dates, so an empty result is suggestive, not conclusive — see note below)\n")

    for days_back in [0, 1, 2, 3, 7, 14, 30]:
        d = date.today() - timedelta(days=days_back)
        label = "today" if days_back == 0 else f"-{days_back}d"
        try:
            stops = movements(train_code, d)
            verdict = f"{len(stops):4} records"
            if stops:
                arr = [s for s in stops if s.find(NS + "Arrival") is not None
                       and s.find(NS + "Arrival").text]
                verdict += f"  ({len(arr)} with actual arrivals)"
        except requests.HTTPError as e:
            verdict = f"HTTP error {e.response.status_code}"
        except requests.RequestException as e:
            verdict = f"error: {e}"
        print(f"  {label:>6}  {fmt_date(d):14}  {verdict}")

    print("\n  INTERPRETING THIS:")
    print("   - records WITH actual arrivals on past dates => real history exists.")
    print("     That would be a major win: you would not have to wait weeks to collect.")
    print("   - 0 records everywhere but today => confirms today-only, and the poller")
    print("     is genuinely urgent.")
    print("   - CAVEAT: a given train code may simply not have operated on a past date")
    print("     (weekends, engineering works). Before concluding, retest with 2-3 other")
    print("     codes, ideally a daily commuter service.")


if __name__ == "__main__":
    try:
        code = sys.argv[1].strip().upper() if len(sys.argv) > 1 else pick_a_running_train()
        if not code:
            print("No running trains found (network is quiet ~00:30-05:30). "
                  "Pass a train code explicitly, e.g. 'python probe2... A220'.")
            sys.exit(0)
        q1_true_schema(code)
        q2_null_semantics(code)
        q3_history(code)
        print("\nDone. Q3 is the one that could change the project plan — read it first.")
    except requests.RequestException as e:
        print(f"Network error: {e}")
