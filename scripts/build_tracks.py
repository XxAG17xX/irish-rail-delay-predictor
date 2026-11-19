"""
build_tracks.py: real track geometry for the network plate's links.

Writes site/tracks.json: for every link in site/network.json, the line the track actually
takes between its two stations, in the same Irish Transverse Mercator metres. Also places
the stations network.json could not, where OpenStreetMap has them. Writes nothing else
except the one archived Overpass response.

Source: OpenStreetMap through the Overpass API, one query, archived gzipped under
data/raw/osm/ before parsing. Later runs reuse the newest archive unless --fetch is passed.
OSM data is ODbL 1.0; the site must credit "© OpenStreetMap contributors".

Requires site/network.json and the parsed journey archive (data/parsed/), which give the
links and each link's services.

Usage (PowerShell, from the repo root, venv active):

    python scripts\\build_tracks.py            # newest archive, or fetch if there is none
    python scripts\\build_tracks.py --fetch    # query Overpass again
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
from datetime import datetime, timezone
from pathlib import Path

import requests

from build_network import IRELAND_BOX, douglas_peucker, itm, load_journeys

REPO = Path(__file__).resolve().parent.parent
NETWORK = REPO / "site" / "network.json"
OUT = REPO / "site" / "tracks.json"
RAW_DIR = REPO / "data" / "raw" / "osm"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
USER_AGENT = "RailCast track geometry build (research)"

_W, _S, _E, _N = IRELAND_BOX
# Every railway=rail way, sidings included, so the archive can be reprocessed under other
# rules; the filter is applied in parse_ways(). Station, halt and junction nodes for the
# stations the feed gives no position.
QUERY = f"""[out:json][timeout:300][bbox:{_S},{_W},{_N},{_E}];
(
  way["railway"="rail"];
  node["railway"~"^(station|halt|junction)$"];
  node["public_transport"="station"];
);
out geom;"""

EXCLUDED_SERVICE = {"yard", "siding", "spur", "crossover"}
# Lifecycle markers that survive on a way still tagged railway=rail. Prefix keys only:
# "proposed:electrified" is about the wires and sits on 1,765 working ways.
LIFECYCLE = ("disused", "abandoned", "construction", "razed", "dismantled")
# Reported, not weighted: only 12 km of kept track lacks usage=main|branch, and costing it
# 1.3x per metre changed no link's path, so the shortest path is by length alone.
PASSENGER_USAGE = {"main", "branch"}
DENSIFY_M = 25          # no graph edge longer than this, so a station finds track beside it
SNAP_M = 300            # a station joins any track within this distance
RATIO_MAX = 1.6         # path length over straight line
NEAR_M = 300            # a third station this close to the path means another branch...
ON_TRACK_M = 50         # ...if the path also runs within this of that station's own track
SIMPLIFY_M = 10         # Douglas-Peucker tolerance; half a pixel at 1 px = 20 m


# ------------------------------------------------------------------------ fetch

def fetch() -> Path:
    """One POST. On refusal or timeout, wait and retry once, then give up."""
    for attempt in (1, 2):
        r = None
        try:
            r = requests.post(OVERPASS_URL, data={"data": QUERY},
                              headers={"User-Agent": USER_AGENT}, timeout=420)
            if r.status_code == 200:
                break
            reason = f"HTTP {r.status_code}: {r.text[:300]!r}"
        except requests.RequestException as e:
            reason = repr(e)
        print(f"  attempt {attempt} failed: {reason}")
        if attempt == 2:
            raise SystemExit("Overpass refused twice; stopping rather than retrying harder.")
        retry_after = r.headers.get("Retry-After", "") if r is not None else ""
        wait = int(retry_after) if retry_after.isdigit() else 120
        print(f"  waiting {wait} s")
        time.sleep(wait)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    path = RAW_DIR / f"overpass-rail-{stamp}.json.gz"
    path.write_bytes(gzip.compress(r.content))
    print(f"  archived {len(r.content):,} bytes as {path.relative_to(REPO)} "
          f"({path.stat().st_size:,} gzipped)")
    return path


# ------------------------------------------------------------------------ parse

def excluded(tags: dict) -> str | None:
    if tags.get("service") in EXCLUDED_SERVICE:
        return f"service={tags['service']}"
    for key in LIFECYCLE:
        if tags.get(key) == "yes" or f"{key}:railway" in tags:
            return key
    return None


def parse_ways(doc: dict):
    """Running-line ways as (way id, passenger?, [(node id, x, y)]), plus counts of what
    was dropped and why."""
    ways, dropped = [], collections.Counter()
    for e in doc["elements"]:
        if e["type"] != "way":
            continue
        reason = excluded(e["tags"])
        if reason:
            dropped[reason] += 1
            continue
        pts = [(n, *itm(g["lat"], g["lon"])) for n, g in zip(e["nodes"], e["geometry"])]
        ways.append((e["id"], e["tags"].get("usage") in PASSENGER_USAGE, pts))
    return ways, dropped


class Graph:
    """OSM nodes joined along their ways. A node shared by two ways joins them, as OSM
    connects track. Long segments are split so that no edge exceeds DENSIFY_M and a station
    beside a straight run still has track within reach; the extra nodes get negative ids."""

    def __init__(self, ways):
        self.xy, self.adj = {}, collections.defaultdict(list)
        self.metres = collections.Counter()
        fresh = -1
        for _, passenger, pts in ways:
            for (u, ux, uy), (v, vx, vy) in zip(pts, pts[1:]):
                self.xy[u], self.xy[v] = (ux, uy), (vx, vy)
                seg = math.hypot(vx - ux, vy - uy)
                self.metres[passenger] += seg
                k = max(1, math.ceil(seg / DENSIFY_M))
                chain = [u]
                for i in range(1, k):
                    self.xy[fresh] = (ux + (vx - ux) * i / k, uy + (vy - uy) * i / k)
                    chain.append(fresh)
                    fresh -= 1
                chain.append(v)
                d = seg / k
                for p, q in zip(chain, chain[1:]):
                    self.adj[p].append((q, d))
                    self.adj[q].append((p, d))
        self.cell = collections.defaultdict(list)
        for n, (x, y) in self.xy.items():
            self.cell[(int(x // SNAP_M), int(y // SNAP_M))].append(n)

    def near(self, x: float, y: float, radius: float) -> list[tuple[float, int]]:
        r = math.ceil(radius / SNAP_M)
        cx, cy = int(x // SNAP_M), int(y // SNAP_M)
        out = []
        for i in range(cx - r, cx + r + 1):
            for j in range(cy - r, cy + r + 1):
                for n in self.cell.get((i, j), ()):
                    d = math.dist((x, y), self.xy[n])
                    if d <= radius:
                        out.append((d, n))
        return sorted(out)

    def snap(self, x: float, y: float) -> list[tuple[float, int]]:
        """Every node within SNAP_M; failing that, the nearest node within 5 km; failing
        that, nothing, and the station's links fall back to straight lines."""
        return self.near(x, y, SNAP_M) or self.near(x, y, 5000)[:1]

    def route(self, src: list, dst: list):
        """Shortest track between any snap node of one station and any of the other. Each
        station pays its straight distance to the node it uses, so it joins the nearest
        suitable track but can take the parallel one when that is the one going its way.
        Returns the node path and its length in metres, those connectors included."""
        exit_cost = {n: d for d, n in dst}
        cost, prev, heap = {}, {}, []
        for d, n in src:
            if d < cost.get(n, math.inf):
                cost[n], prev[n] = d, None
                heapq.heappush(heap, (d, n))
        best, best_node = math.inf, None
        while heap:
            c, u = heapq.heappop(heap)
            if c >= best:
                break
            if c > cost[u]:
                continue
            if u in exit_cost and c + exit_cost[u] < best:
                best, best_node = c + exit_cost[u], u
            for v, d in self.adj[u]:
                if c + d < cost.get(v, math.inf):
                    cost[v], prev[v] = c + d, u
                    heapq.heappush(heap, (c + d, v))
        if best_node is None:
            return None, math.inf
        path = [best_node]
        while prev[path[-1]] is not None:
            path.append(prev[path[-1]])
        return path[::-1], best


# --------------------------------------------------------------------- checks

def seg_dist(p, a, b) -> float:
    (px, py), (ax, ay), (bx, by) = p, a, b
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    t = 0.0 if l2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / l2))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


def poly_dist(p, line) -> float:
    return min(seg_dist(p, a, b) for a, b in zip(line, line[1:]))


def service_patterns(placed: set[str]) -> dict:
    """For each link, every placed station that any journey running over it calls at or
    passes."""
    journeys, _ = load_journeys(None)
    pattern = collections.defaultdict(set)
    for recs in journeys.values():
        seq = [loc for _, loc, _, _ in recs if loc in placed]
        seq = [c for i, c in enumerate(seq) if i == 0 or c != seq[i - 1]]
        for a, b in zip(seq, seq[1:]):
            pattern[tuple(sorted((a, b)))] |= set(seq)
    return pattern


def osm_stations(doc: dict) -> dict:
    """Station and junction nodes by the Irish Rail code OSM tags them with."""
    by_code = collections.defaultdict(list)
    for e in doc["elements"]:
        if e["type"] != "node":
            continue
        t = e["tags"]
        for key in ("railway:ref:Irish Rail", "railway:ref"):
            if key in t:
                by_code[t[key].strip().upper()].append(e)
                break
    return by_code


def r1(v: float) -> int:
    return int(round(v))


# ------------------------------------------------------------------------- main

SAMPLES = [("HSTON", "PWESF"), ("CURAH", "KDARE"), ("BTSTN", "BROCK"), ("CORK", "MLLOW")]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true", help="query Overpass even if an archive exists")
    args = ap.parse_args()
    archives = sorted(RAW_DIR.glob("overpass-rail-*.json.gz"))
    src = fetch() if args.fetch or not archives else archives[-1]
    print(f"osm: {src.relative_to(REPO)}")
    doc = json.loads(gzip.decompress(src.read_bytes()))
    osm_base = doc["osm3s"]["timestamp_osm_base"]
    print(f"  data as of {osm_base}, {len(doc['elements'])} elements")

    ways, dropped = parse_ways(doc)
    g = Graph(ways)
    print(f"  kept {len(ways)} ways, dropped {sum(dropped.values())}: {dict(dropped)}")
    print(f"  track: {g.metres[True] / 1000:.0f} km usage=main|branch, "
          f"{g.metres[False] / 1000:.0f} km other; "
          f"{len(g.xy)} graph nodes at <= {DENSIFY_M} m")

    net = json.loads(NETWORK.read_text(encoding="utf-8"))
    xy = {s["code"]: (s["x"], s["y"]) for s in net["stations"] if s["x"] is not None}
    names = {s["code"]: s["name"] for s in net["stations"]}

    # How far the feed's station positions sit from OSM's node for the same code.
    tagged = osm_stations(doc)
    gaps = sorted((math.dist(xy[c], itm(tagged[c][0]["lat"], tagged[c][0]["lon"])), c)
                  for c in xy if c in tagged)
    print(f"\nfeed position vs OSM node with the same code: {len(gaps)} of {len(xy)} codes, "
          f"median {gaps[len(gaps) // 2][0]:.0f} m, over 300 m: "
          f"{[(c, round(d)) for d, c in gaps if d > 300]}")

    snaps = {c: g.snap(*p) for c, p in xy.items()}
    far = sorted(((s[0][0] if s else math.inf, c) for c, s in snaps.items()
                  if not s or s[0][0] > SNAP_M), reverse=True)
    nearest = sorted(s[0][0] for s in snaps.values() if s)
    print(f"\nstation to nearest running-line track: median {nearest[len(nearest) // 2]:.0f} m, "
          f"max {nearest[-1]:.0f} m; beyond {SNAP_M} m: "
          f"{[(c, names[c], round(d) if d < math.inf else 'none within 5 km') for d, c in far]}")

    pattern = service_patterns(set(xy))
    tracks, fallback, kept, near_only = {}, {}, [], []
    for link in net["links"]:
        a, b = link["a"], link["b"]
        key, straight = f"{a}|{b}", math.dist(xy[a], xy[b])
        nodes, length = g.route(snaps[a], snaps[b])
        reason = None
        if not snaps[a] or not snaps[b]:
            reason = "no running-line track within 5 km of " + " and ".join(
                c for c in (a, b) if not snaps[c])
        elif nodes is None:
            reason = "no connected track between the two stations"
        else:
            line = [xy[a]] + [g.xy[n] for n in nodes] + [xy[b]]
            ratio = length / straight
            if ratio > RATIO_MAX:
                reason = f"path {length / 1000:.1f} km is {ratio:.2f}x the straight line"
            else:
                # Codes at an endpoint's exact position are that station under another
                # code (ADAMF/ADMTN: fast and slow lines), not a third station.
                allowed = (pattern.get((a, b), set()) | {a, b}
                           | {c for c, p in xy.items() if p in (xy[a], xy[b])})
                xs, ys = [p[0] for p in line], [p[1] for p in line]
                box = (min(xs) - NEAR_M, max(xs) + NEAR_M, min(ys) - NEAR_M, max(ys) + NEAR_M)
                hits = []
                for c, p in xy.items():
                    if c in allowed or not (box[0] <= p[0] <= box[1] and box[2] <= p[1] <= box[3]):
                        continue
                    d = poly_dist(p, line)
                    if d >= NEAR_M:
                        continue
                    # Near is not enough: the path must run on the track serving that
                    # station. Drumcondra sits 4 m from its own line and 170 m from the
                    # Royal Canal line beside it, which Docklands trains use.
                    own = snaps[c][0][0] if snaps[c] else math.inf
                    if d <= own + ON_TRACK_M:
                        hits.append((round(d), c))
                    else:
                        near_only.append((key, c, round(d), round(own)))
                if hits:
                    reason = ("passes stations off its own services: "
                              + ", ".join(f"{c} {d} m" for d, c in sorted(hits)))
        if reason:
            fallback[key] = reason
            tracks[key] = [[r1(xy[a][0]), r1(xy[a][1])], [r1(xy[b][0]), r1(xy[b][1])]]
            continue
        simple = [(r1(x), r1(y)) for x, y in douglas_peucker(line, SIMPLIFY_M)]
        simple = [p for i, p in enumerate(simple) if i == 0 or p != simple[i - 1]]
        tracks[key] = [list(p) for p in simple]
        kept.append((ratio, key, length, straight, len(line), len(simple), link["services"]))

    services = {f"{l['a']}|{l['b']}": l["services"] for l in net["links"]}
    print(f"\nlinks: {len(net['links'])}, on track {len(kept)}, "
          f"straight-line fallback {len(fallback)}")
    for k, why in fallback.items():
        print(f"  {k} ({services[k]} services): {why}")
    print(f"  third stations within {NEAR_M} m but off the path's track (not rejected): "
          + "; ".join(f"{k} {c} {d} m (its own track {o} m)" for k, c, d, o in near_only))
    kept.sort()
    print("  highest ratios kept:")
    for ratio, key, length, straight, *_ in kept[-8:]:
        print(f"    {ratio:.3f} {key} {length / 1000:.2f} km vs {straight / 1000:.2f} km")
    print(f"  lowest ratio kept: {kept[0][0]:.4f} {kept[0][1]}")
    by_key = {s[1]: s for s in kept}
    print("  samples:")
    for a, b in SAMPLES:
        k = f"{min(a, b)}|{max(a, b)}"
        s = by_key.get(k)
        print(f"    {k} {names[a]} - {names[b]}: " + (
            f"track {s[2] / 1000:.2f} km, straight {s[3] / 1000:.2f} km, ratio {s[0]:.3f}, "
            f"vertices {s[4]} -> {s[5]}" if s else fallback.get(k, "not a link")))

    print("\nunplaced stations:")
    placed = {}
    for s in net["stations"]:
        if s["x"] is not None:
            continue
        c, hits = s["code"], tagged.get(s["code"], [])
        if len(hits) != 1:
            same_name = [(e["id"], e["tags"].get("railway:ref")) for e in doc["elements"]
                         if e["type"] == "node"
                         and e["tags"].get("name", "").lower() == s["name"].lower()]
            print(f"  {c} {s['name']}: {len(hits)} OSM nodes with this code, not placed; "
                  f"same-name nodes (id, railway:ref): {same_name}")
            continue
        e = hits[0]
        x, y = itm(e["lat"], e["lon"])
        placed[c] = {"x": r1(x), "y": r1(y), "lat": round(e["lat"], 6), "lon": round(e["lon"], 6),
                     "osm_id": e["id"], "osm_name": e["tags"].get("name"),
                     "osm_railway": e["tags"].get("railway")}
        print(f"  {c} {s['name']}: {placed[c]}")

    out = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "license": "ODbL 1.0, © OpenStreetMap contributors",
        "source": {
            "url": OVERPASS_URL, "query": QUERY, "osm_base": osm_base,
            "fetched": src.name.removeprefix("overpass-rail-").removesuffix(".json.gz"),
            "kept": "railway=rail without service=yard|siding|spur|crossover or a "
                    "disused/abandoned/construction/razed/dismantled marker",
            "snap_m": SNAP_M, "max_ratio": RATIO_MAX, "near_station_m": NEAR_M,
            "on_track_m": ON_TRACK_M, "simplify_m": SIMPLIFY_M,
            "units": "ITM metres (EPSG:2157), rounded to 1; each line starts and ends at "
                     "its stations' network.json positions",
        },
        "links": tracks,
        "fallback": fallback,
        "stations": placed,
        "stations_note": "Positions from the OSM node tagged with the same Irish Rail code "
                         "(railway:ref or railway:ref:Irish Rail), for codes network.json "
                         "leaves unplaced; network.json itself is unchanged. CENTJ and CITYJ "
                         "are junctions (railway=junction), not stations.",
    }
    body = json.dumps(out, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    OUT.write_bytes(body)
    print(f"\nwrote {OUT.relative_to(REPO)}: {len(body):,} bytes, "
          f"{len(gzip.compress(body, 9)):,} gzipped, "
          f"{sum(len(v) for v in tracks.values()):,} vertices")
    return 0


if __name__ == "__main__":
    sys.exit(main())
