"""
build_network.py: the data behind the landing page's network plate.

Writes site/network.json, which the site serves as is: every station at its true position in Irish Transverse
Mercator, the links between stations that follow each other on real services, and a
simplified coastline of the island of Ireland. Writes nothing else except the one archived
station-list response.

Sources:
  - stations: Irish Rail getAllStationsXML, fetched once and archived gzipped under
    data/raw/stations/ before parsing. Later runs reuse the newest archive unless
    --fetch-stations is passed.
  - links: the parsed journey archive, data/parsed/date=*/part.parquet.
  - coast: Natural Earth 1:10m land (public domain), ne_10m_land.geojson from
    https://github.com/nvkelso/natural-earth-vector/tree/master/geojson. Downloaded by hand
    and passed with --land; it is not kept in the repo.

Usage (PowerShell, from the repo root, venv active):

    python scripts\\build_network.py --land C:\\path\\to\\ne_10m_land.geojson
    python scripts\\build_network.py --land ...\\ne_10m_land.geojson --fetch-stations
    python scripts\\build_network.py --land ...\\ne_10m_land.geojson --self-test
"""

from __future__ import annotations

import argparse
import collections
import gzip
import heapq
import json
import math
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import requests

REPO = Path(__file__).resolve().parent.parent
STATIONS_URL = "http://api.irishrail.ie/realtime/realtime.asmx/getAllStationsXML"
STATIONS_DIR = REPO / "data" / "raw" / "stations"
PARSED_DIR = REPO / "data" / "parsed"
OUT = REPO / "site" / "network.json"
NS = "{http://api.irishrail.ie/realtime/}"
USER_AGENT = "rail-delay/0.1 (research project; single station-list fetch)"

# Lon/lat box around the island. Generous on every side except the east, where -5.40 sits
# just beyond Burr Point (-5.43, the island's easternmost point) and short of Scotland.
IRELAND_BOX = (-10.8, 51.3, -5.40, 55.5)

COAST_TOLERANCE_M = 500
MIN_ISLAND_KM2 = 10.0
# A link is an express skip when another chain of links joins the same two stations, is at
# most this much longer in straight-line terms, and the direct run passed no timing point
# the chain does not. Observed ratios run 1.001, 1.033, 1.214, then 1.539 and up; the first
# three are Bray-Shankill, Connolly-Broombridge and Limerick-Thurles past Limerick Junction.
SKIP_DETOUR_RATIO = 1.3


# ------------------------------------------------------------------ projection

GRS80 = (6378137.0, 1 / 298.257222101)
ITM = dict(lat0=53.5, lon0=-8.0, k0=0.99982, fe=600000.0, fn=750000.0)


def tm_forward(lat: float, lon: float, ellipsoid=GRS80, lat0=53.5, lon0=-8.0,
               k0=0.99982, fe=600000.0, fn=750000.0) -> tuple[float, float]:
    """Transverse Mercator by Krueger's series in n to fourth order (Karney 2011, eqs 7-11),
    sub-millimetre within a few degrees of the central meridian."""
    a, f = ellipsoid
    n = f / (2 - f)
    e = math.sqrt(f * (2 - f))
    big_a = a / (1 + n) * (1 + n**2 / 4 + n**4 / 64)
    alpha = (
        n / 2 - 2 * n**2 / 3 + 5 * n**3 / 16 + 41 * n**4 / 180,
        13 * n**2 / 48 - 3 * n**3 / 5 + 557 * n**4 / 1440,
        61 * n**3 / 240 - 103 * n**4 / 140,
        49561 * n**4 / 161280,
    )

    def xi_eta(phi: float, dlam: float) -> tuple[float, float]:
        s = math.sin(phi)
        t = math.sinh(math.atanh(s) - e * math.atanh(e * s))
        xi_p = math.atan2(t, math.cos(dlam))
        eta_p = math.atanh(math.sin(dlam) / math.sqrt(1 + t * t))
        xi = xi_p + sum(al * math.sin(2 * j * xi_p) * math.cosh(2 * j * eta_p)
                        for j, al in enumerate(alpha, 1))
        eta = eta_p + sum(al * math.cos(2 * j * xi_p) * math.sinh(2 * j * eta_p)
                          for j, al in enumerate(alpha, 1))
        return xi, eta

    xi0, _ = xi_eta(math.radians(lat0), 0.0)
    xi, eta = xi_eta(math.radians(lat), math.radians(lon - lon0))
    return fe + k0 * big_a * eta, fn + k0 * big_a * (xi - xi0)


def tm_snyder(lat: float, lon: float, ellipsoid=GRS80, lat0=53.5, lon0=-8.0,
              k0=0.99982, fe=600000.0, fn=750000.0) -> tuple[float, float]:
    """Snyder's series (USGS PP 1395, eqs 8-9 to 8-13). An independent formulation, used
    only to cross-check tm_forward."""
    a, f = ellipsoid
    e2 = f * (2 - f)
    ep2 = e2 / (1 - e2)

    def meridian(phi: float) -> float:
        return a * ((1 - e2 / 4 - 3 * e2**2 / 64 - 5 * e2**3 / 256) * phi
                    - (3 * e2 / 8 + 3 * e2**2 / 32 + 45 * e2**3 / 1024) * math.sin(2 * phi)
                    + (15 * e2**2 / 256 + 45 * e2**3 / 1024) * math.sin(4 * phi)
                    - (35 * e2**3 / 3072) * math.sin(6 * phi))

    phi = math.radians(lat)
    nu = a / math.sqrt(1 - e2 * math.sin(phi) ** 2)
    t = math.tan(phi) ** 2
    c = ep2 * math.cos(phi) ** 2
    aa = math.radians(lon - lon0) * math.cos(phi)
    x = k0 * nu * (aa + (1 - t + c) * aa**3 / 6
                   + (5 - 18 * t + t * t + 72 * c - 58 * ep2) * aa**5 / 120)
    y = k0 * (meridian(phi) - meridian(math.radians(lat0)) + nu * math.tan(phi) * (
        aa**2 / 2 + (5 - t + 9 * c + 4 * c * c) * aa**4 / 24
        + (61 - 58 * t + t * t + 600 * c - 330 * ep2) * aa**6 / 720))
    return fe + x, fn + y


def itm(lat: float, lon: float) -> tuple[float, float]:
    return tm_forward(lat, lon, GRS80, **ITM)


def self_test() -> list[str]:
    """Published reference points. Returns report lines; raises if any is off."""
    lines = []
    # EPSG Guidance Note 7-2, Transverse Mercator worked example (OSGB 1936 / British
    # National Grid, Airy 1830), with k0 as the example states it (0.9996013, not EPSG's
    # 0.9996012717; the longer value moves the answer 5 mm). Tests the formulas with
    # someone else's parameters.
    e, n = tm_forward(50.5, 0.5, (6377563.396, 1 / 299.3249646),
                      lat0=49.0, lon0=-2.0, k0=0.9996013, fe=400000.0, fn=-100000.0)
    lines.append(f"EPSG GN7-2 example: got E {e:.3f} N {n:.3f}, published E 577274.99 "
                 f"N 69740.50, error {math.hypot(e - 577274.99, n - 69740.50):.3f} m")
    assert math.hypot(e - 577274.99, n - 69740.50) < 0.02
    # Spire of Dublin. ITM from Wikipedia's ITM article (stated to about 1 m), WGS84
    # position from the same landmark's commonly published coordinates. WGS84 and ETRS89
    # (ITM's datum) have drifted about 0.9 m apart since 1989, so expect an error near 1 m.
    e, n = itm(53.349799, -6.260265)
    err = math.hypot(e - 715830, n - 734697)
    lines.append(f"Spire of Dublin: got E {e:.1f} N {n:.1f}, published E 715830 N 734697, "
                 f"error {err:.2f} m")
    assert err < 2.0
    worst = 0.0
    for lat in (51.4, 52.5, 53.5, 54.5, 55.4):
        for lon in (-10.6, -9.0, -8.0, -7.0, -5.5):
            worst = max(worst, math.dist(itm(lat, lon), tm_snyder(lat, lon, GRS80, **ITM)))
    lines.append(f"Kruger vs Snyder over a 5x5 grid covering the island: max difference "
                 f"{worst * 1000:.2f} mm")
    assert worst < 0.05
    return lines


# --------------------------------------------------------------------- stations

def fetch_stations() -> Path:
    """One request. Honours 429/503 with at most two further attempts, backing off."""
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    for attempt in range(3):
        r = session.get(STATIONS_URL, timeout=30)
        if r.status_code in (429, 503):
            wait = r.headers.get("Retry-After")
            time.sleep(float(wait) if wait and wait.isdigit() else 10 * 2**attempt)
            continue
        r.raise_for_status()
        body = r.content
        if b"objStation" not in body[:2000]:
            raise RuntimeError(f"not a station list: {body[:200]!r}")
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        dest = STATIONS_DIR / f"getAllStationsXML-{stamp}.xml.gz"
        dest.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(dest, "wb") as z:
            z.write(body)
        return dest
    raise RuntimeError("station list still throttled after 3 attempts; try later")


def load_stations(path: Path) -> list[dict]:
    root = ET.fromstring(gzip.decompress(path.read_bytes()))
    out = []
    for s in root.iter(f"{NS}objStation"):
        get = lambda tag: (s.findtext(f"{NS}{tag}") or "").strip()  # noqa: E731
        out.append(dict(code=get("StationCode").upper(), name=get("StationDesc"),
                         alias=get("StationAlias"), id=get("StationId"),
                         lat=float(get("StationLatitude") or 0),
                         lon=float(get("StationLongitude") or 0)))
    return out


# ----------------------------------------------------------------------- links

def load_journeys(dates: list[str] | None) -> tuple[dict, list[str]]:
    files = sorted(PARSED_DIR.glob("date=*/part.parquet"))
    if dates:
        files = [f for f in files if f.parent.name[5:] in dates]
    cols = ["TrainCode", "LocationCode", "LocationOrder", "LocationType", "source_file"]
    t = pa.concat_tables([pq.read_table(f, columns=cols) for f in files])
    journeys = collections.defaultdict(list)
    for code, loc, order, ltype, src in zip(*(t.column(c).to_pylist() for c in cols)):
        journeys[src].append((order, loc.strip().upper(), ltype, code.strip()))
    for v in journeys.values():
        v.sort()
    return journeys, [f.parent.name[5:] for f in files]


def observed_links(journeys: dict, station_codes: set[str], placed: set[str]):
    """Consecutive stations in each journey's own LocationOrder, every LocationType kept.

    Non-station timing points are stepped over, so CNLLY, SUBJN, EWALL, CTARF gives the
    link CNLLY-CTARF. Stations a train passes without stopping are kept, because the feed
    lists them as T (or C) records in their place in the order, and they are physically
    on the route. A station with no usable position cannot be drawn, so it is stepped over
    too but recorded on the link it sits on (Moira, between Lurgan and Lisburn).
    """
    services = collections.defaultdict(set)
    runs = collections.Counter()
    between = collections.defaultdict(set)
    points = collections.defaultdict(set)  # every location code passed between the two
    for recs in journeys.values():
        train = recs[0][3]
        prev, pending, passed = None, [], set()
        for _, loc, _, _ in recs:
            if loc not in placed:
                passed.add(loc)
                if loc in station_codes:
                    pending.append(loc)
                continue
            if prev is not None and prev != loc:
                key = tuple(sorted((prev, loc)))
                services[key].add(train)
                runs[key] += 1
                between[key].add(tuple(pending if key[0] == prev else pending[::-1]))
                points[key] |= passed
            prev, pending, passed = loc, [], set()
    return services, runs, between, points


def shortest(adj: dict, length: dict, src: str, dst: str, skip: tuple | None = None):
    dist, prev, heap = {src: 0.0}, {}, [(0.0, src)]
    while heap:
        d, u = heapq.heappop(heap)
        if u == dst:
            path = [dst]
            while path[-1] != src:
                path.append(prev[path[-1]])
            return d, path[::-1]
        if d > dist[u]:
            continue
        for v in adj[u]:
            if skip and tuple(sorted((u, v))) == skip:
                continue
            nd = d + length[tuple(sorted((u, v)))]
            if nd < dist.get(v, math.inf):
                dist[v], prev[v] = nd, u
                heapq.heappush(heap, (nd, v))
    return math.inf, None


def remove_skips(links: dict, xy: dict, points: dict):
    """Drop links that another chain of links already covers almost in a straight line,
    unless the direct run passed a timing point that the chain never passes, which means
    it ran over different track (Connolly-Broombridge by the Royal Canal, via NEWCJ, is
    real track with no station on it, beside the usual route through Drumcondra).

    Longest first, against the graph as it stands, so an express skip is judged against the
    stopping pattern and never against another skip that is about to go.
    """
    length = {k: math.dist(xy[k[0]], xy[k[1]]) for k in links}
    adj = collections.defaultdict(set)
    for a, b in links:
        adj[a].add(b)
        adj[b].add(a)
    removed, kept = [], []
    for key in sorted(links, key=lambda k: -length[k]):
        d, path = shortest(adj, length, key[0], key[1], skip=key)
        if not (path and len(path) > 2 and d <= SKIP_DETOUR_RATIO * length[key]):
            continue
        chain = set(path[1:-1]).union(*(points[tuple(sorted(p))] for p in zip(path, path[1:])))
        own_route = points[key] - chain
        if own_route:
            kept.append((key, length[key], d, path, sorted(own_route)))
            continue
        adj[key[0]].discard(key[1])
        adj[key[1]].discard(key[0])
        removed.append((key, length[key], d, path))
    return removed, kept, length


def detour_ratios(links: dict, xy: dict) -> list[tuple]:
    """For every link, the best alternative chain's length over the link's own length."""
    length = {k: math.dist(xy[k[0]], xy[k[1]]) for k in links}
    adj = collections.defaultdict(set)
    for a, b in links:
        adj[a].add(b)
        adj[b].add(a)
    out = []
    for key in links:
        d, path = shortest(adj, length, key[0], key[1], skip=key)
        if path:
            out.append((d / length[key], key, len(path) - 2))
    return sorted(out)


# ------------------------------------------------------------------------ coast

def ring_area(ring: list) -> float:
    return 0.5 * sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1]))


def douglas_peucker(points: list, tol: float) -> list:
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        i, j = stack.pop()
        (x0, y0), (x1, y1) = points[i], points[j]
        dx, dy = x1 - x0, y1 - y0
        seg = math.hypot(dx, dy)
        best, idx = -1.0, None
        for k in range(i + 1, j):
            px, py = points[k]
            d = (abs(dy * (px - x0) - dx * (py - y0)) / seg if seg
                 else math.hypot(px - x0, py - y0))
            if d > best:
                best, idx = d, k
        if idx is not None and best > tol:
            keep[idx] = True
            stack += [(i, idx), (idx, j)]
    return [p for p, k in zip(points, keep) if k]


def simplify_ring(ring: list, tol: float) -> list:
    """A closed ring has no natural endpoints, so split it at its first vertex and the
    vertex farthest from it and simplify the two halves."""
    far = max(range(len(ring)), key=lambda k: math.dist(ring[0], ring[k]))
    a = douglas_peucker(ring[: far + 1], tol)
    b = douglas_peucker(ring[far:] + ring[:1], tol)
    return a[:-1] + b[:-1]


def load_coast(path: Path):
    lo_lon, lo_lat, hi_lon, hi_lat = IRELAND_BOX
    gj = json.loads(path.read_text(encoding="utf-8"))
    rings, dropped = [], []
    for feat in gj["features"]:
        geom = feat["geometry"]
        polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
        for poly in polys:
            outer = poly[0]
            lons = [p[0] for p in outer]
            lats = [p[1] for p in outer]
            # Whole polygons only: Great Britain's polygon pokes into the box at Kintyre
            # and would otherwise be clipped into a fragment.
            if not (min(lons) >= lo_lon and max(lons) <= hi_lon
                    and min(lats) >= lo_lat and max(lats) <= hi_lat):
                continue
            proj = [itm(lat, lon) for lon, lat in outer[:-1]]
            area = abs(ring_area(proj)) / 1e6
            if len(poly) > 1:
                raise RuntimeError("land polygon with holes; handle before trusting output")
            centre = (sum(lats) / len(lats), sum(lons) / len(lons))
            (rings if area >= MIN_ISLAND_KM2 else dropped).append((area, centre, proj))
    rings.sort(key=lambda r: -r[0])
    dropped.sort(key=lambda r: -r[0])
    return rings, dropped


def point_in_ring(x: float, y: float, ring: list) -> bool:
    inside = False
    for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1]):
        if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
            inside = not inside
    return inside


def dist_to_ring(x: float, y: float, ring: list) -> float:
    best = math.inf
    for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1]):
        dx, dy = x1 - x0, y1 - y0
        t = 0.0 if not (dx or dy) else max(0.0, min(1.0, ((x - x0) * dx + (y - y0) * dy)
                                                   / (dx * dx + dy * dy)))
        best = min(best, math.hypot(x - x0 - t * dx, y - y0 - t * dy))
    return best


# ------------------------------------------------------------------------- main

def r10(v: float) -> int:
    return int(round(v / 10.0)) * 10


CORRIDORS = [
    ("Heuston-Cork", "HSTON", "CORK"), ("Heuston-Limerick", "HSTON", "LMRCK"),
    ("Heuston-Galway", "HSTON", "GALWY"), ("Heuston-Westport", "HSTON", "WPORT"),
    ("Heuston-Ballina", "HSTON", "BALNA"), ("Heuston-Tralee", "HSTON", "TRLEE"),
    ("Heuston-Waterford", "HSTON", "WFORD"), ("Connolly-Sligo", "CNLLY", "SLIGO"),
    ("Connolly-Belfast", "CNLLY", "BFSTC"), ("Connolly-Rosslare", "CNLLY", "RLEPT"),
    ("Howth-Greystones", "HOWTH", "GSTNS"), ("Malahide-Greystones", "MHIDE", "GSTNS"),
    ("Limerick Jn-Waterford", "LMRKJ", "WFORD"), ("Limerick-Ennis", "LMRCK", "ENNIS"),
    ("Ennis-Galway (WRC south)", "ENNIS", "GALWY"), ("Cork-Cobh", "CORK", "COBH"),
    ("Cork-Midleton", "CORK", "MDLTN"), ("Dublin-Maynooth", "CNLLY", "MYNTH"),
    ("Dublin-M3 Parkway", "CNLLY", "M3WAY"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--land", type=Path, required=True, help="ne_10m_land.geojson")
    ap.add_argument("--fetch-stations", action="store_true",
                    help="fetch getAllStationsXML now instead of reusing the newest archive")
    ap.add_argument("--dates", nargs="*", help="limit journeys to these service dates")
    ap.add_argument("--self-test", action="store_true", help="projection checks only")
    args = ap.parse_args()

    for line in self_test():
        print(line)
    if args.self_test:
        return 0

    archives = sorted(STATIONS_DIR.glob("getAllStationsXML-*.xml.gz"))
    src = fetch_stations() if args.fetch_stations or not archives else archives[-1]
    print(f"\nstation list: {src.relative_to(REPO)}")
    raw = load_stations(src)
    by_code = {}
    for s in raw:
        if s["code"] in by_code:
            print(f"  duplicate code in feed: {s['code']}")
        by_code[s["code"]] = s
    print(f"  {len(raw)} records, {len(by_code)} distinct codes")

    live = {s["code"]: s["name"] for s in json.loads(
        (REPO / "data" / "live" / "stations.json").read_text(encoding="utf-8"))}
    print(f"  data/live/stations.json: {len(live)} codes")
    print(f"  in feed only: {sorted(set(by_code) - set(live))}")
    print(f"  in stations.json only: {sorted(set(live) - set(by_code))}")
    renamed = [(c, live[c], by_code[c]["name"]) for c in live
               if c in by_code and live[c] != by_code[c]["name"]]
    print(f"  same code, different name: {renamed}")
    names = collections.defaultdict(list)
    for s in raw:
        names[s["name"].lower()].append(s["code"])
    print(f"  names shared by several codes: "
          f"{ {n: c for n, c in names.items() if len(c) > 1} }")

    stations = by_code
    unplaced = {}
    lo_lon, lo_lat, hi_lon, hi_lat = IRELAND_BOX
    for c, s in stations.items():
        if not (lo_lon <= s["lon"] <= hi_lon and lo_lat <= s["lat"] <= hi_lat):
            unplaced[c] = f"feed gives lat {s['lat']}, lon {s['lon']}"
    # Codes sharing a name are one station (platform or line variants), so a member far
    # from the others carries a bad coordinate. ADAMS was the case: lon exactly -7.0.
    for codes in names.values():
        for c in codes:
            others = [o for o in codes if o != c and o not in unplaced]
            far = [o for o in others if math.dist(itm(stations[c]["lat"], stations[c]["lon"]),
                                                  itm(stations[o]["lat"], stations[o]["lon"])) > 1000]
            if c not in unplaced and len(others) >= 2 and len(far) == len(others):
                o = others[0]
                unplaced[c] = (f"feed gives lat {stations[c]['lat']}, lon {stations[c]['lon']}; "
                               f"{', '.join(others)} with the same name give "
                               f"{stations[o]['lat']}, {stations[o]['lon']}")
    print(f"  no usable position: {unplaced}")
    xy = {c: itm(s["lat"], s["lon"]) for c, s in stations.items() if c not in unplaced}
    same_spot = collections.defaultdict(list)
    for c, (x, y) in xy.items():
        same_spot[(r10(x), r10(y))].append(c)
    print(f"  codes sharing one position (10 m): "
          f"{[v for v in same_spot.values() if len(v) > 1]}")

    journeys, dates = load_journeys(args.dates)
    weekdays = collections.Counter(datetime.strptime(d, "%Y-%m-%d").strftime("%a") for d in dates)
    print(f"\njourneys: {len(journeys)} from {len(dates)} dates {dates[0]}..{dates[-1]}, "
          f"weekdays {dict(weekdays)}")
    locs = collections.Counter(loc for recs in journeys.values() for _, loc, _, _ in recs)
    print(f"  {len(locs)} distinct location codes, "
          f"{len(set(locs) & set(stations))} of them stations")
    unseen = sorted(set(stations) - set(locs))
    print(f"  stations never in any journey: "
          f"{[(c, stations[c]['name']) for c in unseen]}")
    by_type = collections.Counter(lt for recs in journeys.values() for _, loc, lt, _ in recs
                                  if loc in stations)
    print(f"  LocationType on station records: {dict(by_type)}")

    services, runs, between, points = observed_links(journeys, set(stations), set(xy))
    print(f"  observed links: {len(services)}")
    for k, v in between.items():
        if any(v):
            print(f"  {k[0]}-{k[1]} passes unplaced stations: {sorted(v)}")

    ratios = detour_ratios(services, xy)
    print("\ndetour ratio of the best alternative chain, lowest 55:")
    for ratio, key, hops in ratios[:55]:
        print(f"  {ratio:6.3f}  {key[0]}-{key[1]}  {math.dist(xy[key[0]], xy[key[1]]) / 1000:6.1f} km"
              f"  via {hops} stations  services {len(services[key])}")

    removed, kept_parallel, length = remove_skips(services, xy, points)
    print(f"\nremoved as express skips (ratio <= {SKIP_DETOUR_RATIO}): {len(removed)}")
    for key, d, alt, path in removed:
        print(f"  {key[0]}-{key[1]} {d / 1000:.1f} km, services {len(services[key])} "
              f"(e.g. {sorted(services[key])[:3]}), chain {alt / 1000:.1f} km via "
              f"{'-'.join(path)}; direct run passed {sorted(points[key]) or 'nothing'}")
    print(f"kept despite the ratio, own timing points off the chain: {len(kept_parallel)}")
    for key, d, alt, path, own in kept_parallel:
        print(f"  {key[0]}-{key[1]} {d / 1000:.1f} km, services {sorted(services[key])}, "
              f"chain {'-'.join(path)} {alt / 1000:.1f} km, own points {own}")
    gone = {k for k, *_ in removed}
    links = {k: v for k, v in services.items() if k not in gone}

    print("\nkept links over 40 km straight-line:")
    for k in sorted(links, key=lambda k: -length[k]):
        if length[k] > 40000:
            print(f"  {k[0]}-{k[1]} {stations[k[0]]['name']} - {stations[k[1]]['name']} "
                  f"{length[k] / 1000:.1f} km, services {len(links[k])}, runs {runs[k]}")
    thin = sorted((len(v), runs[k], k) for k, v in links.items() if len(v) <= 2)
    print(f"\nkept links seen on at most 2 services: {len(thin)}")
    for n, rn, k in thin:
        print(f"  {k[0]}-{k[1]} services {n} runs {rn} {sorted(links[k])}")

    adj = collections.defaultdict(set)
    for a, b in links:
        adj[a].add(b)
        adj[b].add(a)
    print("\ncorridors (shortest path by distance over kept links):")
    for label, a, b in CORRIDORS:
        if a not in stations or b not in stations:
            print(f"  {label}: code missing ({a} or {b})")
            continue
        d, path = shortest(adj, length, a, b)
        print(f"  {label}: " + (f"{d / 1000:.0f} km, {len(path)} stations: {'-'.join(path)}"
                                if path else "NO PATH"))

    seen, comps = set(), []
    for c in sorted(xy):
        if c in seen:
            continue
        comp, stack = [], [c]
        seen.add(c)
        while stack:
            u = stack.pop()
            comp.append(u)
            for v in adj[u]:
                if v not in seen:
                    seen.add(v)
                    stack.append(v)
        comps.append(sorted(comp))
    comps.sort(key=len, reverse=True)
    print(f"\nconnected components: {len(comps)}, sizes {[len(c) for c in comps]}")
    for comp in comps[1:]:
        print(f"  {[(c, stations[c]['name']) for c in comp]}")
    print(f"  plus {len(unplaced)} stations with no position: {sorted(unplaced)}")

    rings, dropped = load_coast(args.land)
    print(f"\ncoast: kept {len(rings)} polygons >= {MIN_ISLAND_KM2} km2: "
          f"{[(round(a, 1), tuple(round(v, 2) for v in c)) for a, c, _ in rings]}")
    print(f"  dropped {len(dropped)} smaller, largest "
          f"{[(round(a, 2), tuple(round(v, 2) for v in c)) for a, c, _ in dropped[:8]]}")
    raw_vertices = sum(len(r) for *_, r in rings)
    spacing = sorted(math.dist(p, q) for *_, r in rings for p, q in zip(r, r[1:]))
    print(f"  raw vertices {raw_vertices}, median spacing {spacing[len(spacing) // 2]:.0f} m")
    simple = []
    for area, _, ring in rings:
        s = [(r10(x), r10(y)) for x, y in simplify_ring(ring, COAST_TOLERANCE_M)]
        dedup = [p for i, p in enumerate(s) if p != s[i - 1]]
        if len(dedup) >= 4:
            simple.append(dedup)
    print(f"  simplified at {COAST_TOLERANCE_M} m: {sum(len(r) for r in simple)} vertices; "
          f"main island area {abs(ring_area(rings[0][2])) / 1e6:.0f} km2 raw, "
          f"{abs(ring_area(simple[0])) / 1e6:.0f} km2 simplified")
    offshore = []
    for c, (x, y) in xy.items():
        if not any(point_in_ring(x, y, r) for r in simple):
            offshore.append((c, stations[c]["name"],
                             round(min(dist_to_ring(x, y, r) for r in simple))))
    print(f"  stations outside the simplified coast: {sorted(offshore, key=lambda t: -t[2])}")

    doc = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sources": {
            "stations": {
                "url": STATIONS_URL,
                "fetched": src.name.removeprefix("getAllStationsXML-").removesuffix(".xml.gz"),
                "terms": "Irish Rail realtime API, free and public, no key; offered as-is "
                         "with no published licence or support (api.irishrail.ie/realtime)",
            },
            "links": {
                "url": "http://api.irishrail.ie/realtime/realtime.asmx/getTrainMovementsXML",
                "dates": [dates[0], dates[-1]], "days": len(dates), "journeys": len(journeys),
                "terms": "as stations",
            },
            "coast": {
                "url": "https://github.com/nvkelso/natural-earth-vector/blob/master/geojson/"
                       "ne_10m_land.geojson",
                "terms": "Natural Earth, public domain (naturalearthdata.com/about/terms-of-use)",
                "scale": "1:10m", "tolerance_m": COAST_TOLERANCE_M,
                "min_island_km2": MIN_ISLAND_KM2,
            },
        },
        "projection": {
            "crs": "EPSG:2157 Irish Transverse Mercator", "ellipsoid": "GRS80",
            "lat0": ITM["lat0"], "lon0": ITM["lon0"], "k0": ITM["k0"],
            "false_easting": ITM["fe"], "false_northing": ITM["fn"],
            "units": "metres, rounded to 10", "axes": "x east, y north",
        },
        "stations": [
            {"code": c, "name": s["name"], "lat": round(s["lat"], 5), "lon": round(s["lon"], 5),
             "x": r10(xy[c][0]), "y": r10(xy[c][1])} if c in xy else
            {"code": c, "name": s["name"], "lat": None, "lon": None, "x": None, "y": None,
             "unplaced": unplaced[c]}
            for c, s in sorted(stations.items())
        ],
        "links": [
            {"a": a, "b": b, "services": len(v),
             **({"passes_unplaced": sorted({c for t in between[(a, b)] for c in t})}
                if any(between[(a, b)]) else {})}
            for (a, b), v in sorted(links.items())
        ],
        "skipped_links": [
            {"a": a, "b": b, "services": len(services[(a, b)]), "via": path[1:-1]}
            for (a, b), _, _, path in removed
        ],
        "coast": [[[x, y] for x, y in r] for r in simple],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"\nwrote {OUT.relative_to(REPO)}, {OUT.stat().st_size:,} bytes "
          f"({len(doc['stations'])} stations, {len(doc['links'])} links, "
          f"{len(doc['coast'])} coast rings)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
