"""
probe_irishrail.py — one-off feasibility check for the Irish Rail Realtime API.

Purpose: answer four questions before you write any ingestion code.
  1. Does the API respond at all, without a key?
  2. Does it expose a usable delay signal?
  3. Does it expose SCHEDULED vs ACTUAL times per stop? (the label you need)
  4. How stale/messy is it?

Run:  pip install requests   then   python probe_irishrail.py
"""

import xml.etree.ElementTree as ET
from datetime import date

import requests

BASE = "http://api.irishrail.ie/realtime/realtime.asmx"
NS = "{http://api.irishrail.ie/realtime/}"
TIMEOUT = 20


def get_xml(path, **params):
    r = requests.get(f"{BASE}/{path}", params=params, timeout=TIMEOUT)
    r.raise_for_status()
    return ET.fromstring(r.content), len(r.content)


def field(node, name):
    el = node.find(NS + name)
    return el.text if el is not None else None


def check_1_stations():
    print("\n=== 1. getAllStationsXML — network topology / node list ===")
    root, nbytes = get_xml("getAllStationsXML")
    stations = root.findall(NS + "objStation")
    print(f"HTTP OK, {nbytes} bytes, {len(stations)} stations")
    for s in stations[:3]:
        print(f"  {field(s,'StationCode'):8} {field(s,'StationDesc'):25} "
              f"lat={field(s,'StationLatitude')} lon={field(s,'StationLongitude')}")
    return [field(s, "StationCode") for s in stations]


def check_2_current_trains():
    print("\n=== 2. getCurrentTrainsXML — live fleet snapshot ===")
    root, nbytes = get_xml("getCurrentTrainsXML")
    trains = root.findall(NS + "objTrainPositions")
    print(f"HTTP OK, {nbytes} bytes, {len(trains)} train records")

    running = [t for t in trains if field(t, "TrainStatus") == "R"]
    print(f"  status breakdown: "
          f"R(running)={len(running)}, "
          f"N(not yet running)={sum(1 for t in trains if field(t,'TrainStatus')=='N')}, "
          f"T(terminated)={sum(1 for t in trains if field(t,'TrainStatus')=='T')}")
    print("  NOTE: delay is only inside the free-text PublicMessage field, e.g.")
    for t in running[:3]:
        print(f"    {field(t,'TrainCode')}: {field(t,'PublicMessage')!r}")
    print("  -> you must regex '(-?\\d+) mins late' out of that string. Not a clean numeric field.")
    return [field(t, "TrainCode") for t in running]


def check_3_station_board(station_code="CNLLY"):
    print(f"\n=== 3. getStationDataByCodeXML_WithNumMins — board for {station_code} ===")
    root, _ = get_xml("getStationDataByCodeXML_WithNumMins",
                      StationCode=station_code, NumMins=90)
    rows = root.findall(NS + "objStationData")
    print(f"HTTP OK, {len(rows)} upcoming movements")
    if rows:
        r = rows[0]
        print("  first row — these ARE clean numeric/typed fields:")
        for f in ["Traincode", "Origin", "Destination", "Scharrival", "Schdepart",
                  "Exparrival", "Expdepart", "Duein", "Late", "Status", "Lastlocation"]:
            print(f"    {f:14} = {field(r, f)}")
    return rows


def check_4_train_movements(train_code):
    """The important one: per-stop scheduled vs actual = your training labels."""
    print(f"\n=== 4. getTrainMovementsXML — stop-by-stop for {train_code} (THE label source) ===")
    train_date = date.today().strftime("%d %b %Y").lower()  # e.g. '25 jul 2026'
    root, _ = get_xml("getTrainMovementsXML", TrainId=train_code, TrainDate=train_date)
    stops = root.findall(NS + "objTrainMovements")
    print(f"HTTP OK, {len(stops)} stop records for TrainDate={train_date!r}")
    if not stops:
        print("  EMPTY — check the date format, or the train code is no longer valid today.")
        return
    print(f"  {'stop':22} {'schArr':8} {'actArr':8} {'schDep':8} {'actDep':8} {'type':5}")
    for s in stops:
        print(f"  {(field(s,'LocationFullName') or '')[:22]:22} "
              f"{str(field(s,'ScheduledArrival'))[:8]:8} "
              f"{str(field(s,'Arrival'))[:8]:8} "
              f"{str(field(s,'ScheduledDeparture'))[:8]:8} "
              f"{str(field(s,'Departure'))[:8]:8} "
              f"{str(field(s,'LocationType')):5}")
    print("  -> If actArr/actDep are populated for passed stops, you have real labels.")
    print("  -> Only available for TODAY. There is NO historical archive. This is the")
    print("     single most important constraint: history exists only if you collect it.")


if __name__ == "__main__":
    try:
        check_1_stations()
        running = check_2_current_trains()
        check_3_station_board("CNLLY")
        if running:
            check_4_train_movements(running[0])
        else:
            print("\nNo running trains right now (network is quiet ~00:30-05:30). "
                  "Re-run during service hours to complete check 4.")
        print("\nVERDICT: if all four blocks printed data, the feed is viable "
              "and your only real blocker is elapsed collection time.")
    except requests.HTTPError as e:
        print(f"HTTP error: {e} — endpoint may be down or renamed.")
    except requests.RequestException as e:
        print(f"Network error: {e}")
