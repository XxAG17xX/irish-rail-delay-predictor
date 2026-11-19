"""
fetch_station_names.py: the Irish form of each station's name, from Logainm.

Writes site/stations_ga.json, which the site reads beside network.json.

Logainm (the Placenames Database of Ireland, logainm.ie) has no entries for railway stations
as such, so a station gets the official Irish form of the place it is named after, under four
conditions, or nothing:

  - the place's English name equals the station's name exactly (case aside), so "Limerick
    Junction" or "Dublin Connolly" match nothing rather than borrowing "Limerick" or "Dublin";
  - the place is a city, town, population centre, village, locality, hamlet, townland, parish
    or municipal district, preferred in that order;
  - it lies within MAX_KM of the station, so a townland of the same name in another county is
    never chosen;
  - Northern Ireland is not covered by Logainm, so stations there find no match and show
    English only.

The API needs a key (https://www.gaois.ie/en/technology/developers/), read from .env as
GAOIS_API_KEY and sent as the X-Api-Key header. One request per distinct name, a second
apart; responses are cached in the session scratchpad, never in the repo.

    python scripts\\fetch_station_names.py --cache <dir>

Data: Logainm.ie, CC BY 4.0. The site credits it wherever an Irish name is shown.
"""

from __future__ import annotations

import argparse
import json
import math
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "site" / "stations_ga.json"
API = "https://www.logainm.ie/api/v1.0/"
MAX_KM = 6.0
# A municipal district is named after its town but its point is the district's middle, which
# sits further from the station: Cobh's is 11.9 km away, Portlaoise's 6.8 km.
MAX_KM_DISTRICT = 15.0
PREFER = ["CTH", "B", "ID", "SRB", "D", "GR", "BF", "PAR", "CBA"]
# How Logainm spells a name the feed writes differently. Only the search changes: the answer
# still has to match the station's own name, compared without accents or hyphens.
QUERY = {"Dun Laoghaire": "Dún Laoghaire", "LittleIsland": "Little Island"}


def key() -> str:
    for line in (REPO / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("GAOIS_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("GAOIS_API_KEY is not in .env")


def same(a: str, b: str) -> bool:
    """Equal names, ignoring case, accents and hyphens: "Carrick-on-Suir", "Dún Laoghaire"."""
    def norm(x):
        x = unicodedata.normalize("NFKD", x).encode("ascii", "ignore").decode()
        return " ".join(x.replace("-", " ").casefold().split())
    return norm(a) == norm(b) or norm(a).replace(" ", "") == norm(b).replace(" ", "")


def km(lat1, lon1, lat2, lon2) -> float:
    return math.hypot((lat1 - lat2) * 111.2, (lon1 - lon2) * 111.2 * math.cos(math.radians(lat1)))


def search(session, name: str, cache: Path) -> dict:
    path = cache / ("q-" + "".join(c if c.isalnum() else "-" if c == "-" else "_" for c in name) + ".json")
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    for attempt in range(4):
        r = session.get(API, params={"Query": name}, timeout=30)
        if r.status_code in (429, 503):
            time.sleep(2 ** (attempt + 2))
            continue
        r.raise_for_status()
        path.write_text(r.text, encoding="utf-8")
        time.sleep(1.0)
        return r.json()
    raise RuntimeError(f"Logainm refused {name!r} four times; stopping rather than pushing")


def best(results: list[dict], name: str, lat: float, lon: float):
    """The nearest exact-name place of the most preferred category, within MAX_KM."""
    found = []
    for x in results:
        en = [p["wording"] for p in x.get("placenames", []) if p.get("language") == "en"]
        ga = [p["wording"] for p in x.get("placenames", []) if p.get("language") == "ga"]
        cats = [c.get("id") for c in x.get("categories", [])]
        coords = (x.get("geography") or {}).get("coordinates") or []
        if not ga or not coords or not any(same(e, name) for e in en):
            continue
        rank = min((PREFER.index(c) for c in cats if c in PREFER), default=None)
        if rank is None:
            continue
        d = min(km(lat, lon, c["latitude"], c["longitude"]) for c in coords)
        if d <= (MAX_KM_DISTRICT if cats == ["CBA"] else MAX_KM):
            found.append((rank, d, x, ga[0], cats))
    return min(found, key=lambda f: (f[0], f[1])) if found else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=Path, required=True, help="directory for raw responses")
    args = ap.parse_args()
    args.cache.mkdir(parents=True, exist_ok=True)

    net = json.loads((REPO / "site" / "network.json").read_text(encoding="utf-8"))
    session = requests.Session()
    session.headers.update({"X-Api-Key": key(), "Accept": "application/json",
                            "User-Agent": "RailCast station-name check (research)"})

    out, none = {}, []
    by_name: dict[str, list[dict]] = {}
    for st in net["stations"]:
        if st.get("lat") is not None:
            by_name.setdefault(st["name"], []).append(st)
    for name, sts in sorted(by_name.items()):
        lat, lon = sts[0]["lat"], sts[0]["lon"]
        results = search(session, QUERY.get(name, name), args.cache).get("results", [])
        if not results and " " in name:
            results = search(session, name.replace(" ", "-"), args.cache).get("results", [])
        hit = best(results, name, lat, lon)
        if not hit:
            none.append(name)
            continue
        rank, d, x, ga, cats = hit
        for st in sts:
            out[st["code"]] = {"en": name, "ga": ga, "logainm": x["id"],
                               "url": x.get("permalink"), "category": cats, "km": round(d, 2)}

    OUT.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "Logainm.ie, the Placenames Database of Ireland (CC BY 4.0)",
        "rule": "Irish form of the place the station is named after: exact English name, "
                f"town first, within {MAX_KM:g} km; otherwise English only",
        "stations": out,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(out)} codes with an Irish form, from {len(by_name) - len(none)} of {len(by_name)} names")
    print("English only:", ", ".join(none))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
