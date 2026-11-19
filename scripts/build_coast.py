"""
build_coast.py: an accurate coastline for the network map's zoomed explorer.

Writes site/coast.json: the island of Ireland and its offshore islands as closed rings in
Irish Transverse Mercator metres, from OpenStreetMap's natural=coastline ways. The overview
plate keeps network.json's Natural Earth coast; this file is for the explorer, which zooms to
about 1 px = 20 m, where Natural Earth draws Howth as a triangle and has no Great Island, so
Cobh station sits 2 km out in Cork Harbour. Writes nothing else except the archived Overpass
response.

Source: OpenStreetMap through the Overpass API, one query (or one per tile with --tiles),
archived gzipped under data/raw/osm/ before parsing. Later runs reuse the newest archive
unless --fetch is passed. OSM data is ODbL 1.0; the site must credit "© OpenStreetMap
contributors".

Coastline ways are directed with land on the left, so they are joined end node to start
node into rings, and a land ring runs anticlockwise.

Usage (PowerShell, from the repo root, venv active):

    python scripts\\build_coast.py                  # newest archive, or fetch if there is none
    python scripts\\build_coast.py --fetch          # query Overpass again, one request
    python scripts\\build_coast.py --fetch --tiles  # four requests, if one times out
    python scripts\\build_coast.py --compare 10 20 40   # sizes at other tolerances too
"""

from __future__ import annotations

import argparse
import collections
import gzip
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from build_network import itm, point_in_ring, ring_area, simplify_ring

REPO = Path(__file__).resolve().parent.parent
NETWORK = REPO / "site" / "network.json"
OUT = REPO / "site" / "coast.json"
RAW_DIR = REPO / "data" / "raw" / "osm"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
USER_AGENT = "RailCast coastline build (research)"

# South, west, north, east. Wider than build_network.IRELAND_BOX, whose east edge sits 2 km
# beyond Burr Point (-5.43), too close for islets off the Ards. The price is two pieces of
# Great Britain inside the box; is_britain() removes them. The Isle of Man is outside it.
BOX = (51.2, -11.0, 55.5, -5.3)
TILES = [(51.2, -11.0, 53.35, -8.15), (51.2, -8.15, 53.35, -5.3),
         (53.35, -11.0, 55.5, -8.15), (53.35, -8.15, 55.5, -5.3)]

TOLERANCE_M = 25
MIN_ISLAND_KM2 = 0.05


def query(box) -> str:
    s, w, n, e = box
    return (f'[out:json][timeout:600];\nway["natural"="coastline"]({s},{w},{n},{e});\n'
            f'out geom;')


def is_britain(lat: float, lon: float) -> bool:
    """BOX reaches two pieces of Great Britain: the south of Kintyre with its islets (north
    of 55.24 and east of -6.0; westernmost the Mull of Kintyre, -5.79) and the islets off
    Pembrokeshire (south of 53.0 and east of -5.9; westernmost the Smalls, -5.67). Ireland's
    land in those latitudes reaches east only to -6.17 (Rathlin) and -6.01 (Wicklow Head),
    so either line clears both coasts by 7 km or more. Judged on a ring's mean vertex."""
    return (lat > 55.24 and lon > -6.0) or (lat < 53.0 and lon > -5.9)


# ------------------------------------------------------------------------ fetch

def post(q: str) -> bytes:
    """One POST, retried with exponential backoff on 429, 502, 503, 504 or a timeout,
    honouring Retry-After. Five attempts, then stop rather than retry harder."""
    wait = 60
    for attempt in range(1, 6):
        r = None
        try:
            r = requests.post(OVERPASS_URL, data={"data": q},
                              headers={"User-Agent": USER_AGENT}, timeout=660)
            if r.status_code == 200 and r.content.lstrip()[:1] == b"{":
                return r.content
            reason = f"HTTP {r.status_code}: {r.text[:200]!r}"
        except requests.RequestException as e:
            reason = repr(e)
        print(f"  attempt {attempt} failed: {reason}")
        if r is not None and r.status_code not in (200, 429, 502, 503, 504):
            break
        retry_after = r.headers.get("Retry-After", "") if r is not None else ""
        pause = int(retry_after) if retry_after.isdigit() else wait
        print(f"  waiting {pause} s")
        time.sleep(pause)
        wait *= 2
    raise SystemExit("Overpass refused; stopping rather than retrying harder.")


def fetch(tiles: bool) -> list[Path]:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, box in enumerate(TILES if tiles else [BOX]):
        if i:
            time.sleep(30)
        body = post(query(box))
        name = f"overpass-coast-{stamp}" + (f"-tile{i + 1}" if tiles else "") + ".json.gz"
        path = RAW_DIR / name
        path.write_bytes(gzip.compress(body))
        print(f"  archived {len(body):,} bytes as {path.relative_to(REPO)} "
              f"({path.stat().st_size:,} gzipped)")
        paths.append(path)
    return paths


def newest_archive() -> list[Path]:
    files = sorted(RAW_DIR.glob("overpass-coast-*.json.gz"))
    if not files:
        return []
    stamp = files[-1].name.split("-")[2].removesuffix(".json.gz")
    return [f for f in files if f.name.split("-")[2].removesuffix(".json.gz") == stamp]


# --------------------------------------------------------------------- assemble

def load_ways(paths: list[Path]):
    """Coastline ways by id, deduplicated across tiles, as (node ids, [(lat, lon)])."""
    ways, bases, queries = {}, set(), []
    for p in paths:
        doc = json.loads(gzip.decompress(p.read_bytes()))
        bases.add(doc["osm3s"]["timestamp_osm_base"])
        for e in doc["elements"]:
            if e["type"] == "way":
                ways[e["id"]] = (e["nodes"], [(g["lat"], g["lon"]) for g in e["geometry"]])
    return ways, sorted(bases)


def assemble(ways: dict):
    """Join ways end node to start node. Returns closed rings as [(lat, lon)] without the
    repeated closing vertex, each with its way ids, plus the chains that never closed and
    any node where two ways start (OSM's coastline should have exactly one)."""
    starts = collections.defaultdict(list)
    for wid, (nodes, _) in ways.items():
        starts[nodes[0]].append(wid)
    forks = {n: w for n, w in starts.items() if len(w) > 1}
    used, rings, open_chains = set(), [], []
    for wid in sorted(ways):
        if wid in used:
            continue
        chain, pts = [wid], list(ways[wid][1])
        used.add(wid)
        first, last = ways[wid][0][0], ways[wid][0][-1]
        while last != first:
            nxt = [w for w in starts.get(last, []) if w not in used]
            if not nxt:
                break
            w = nxt[0]
            used.add(w)
            chain.append(w)
            pts.extend(ways[w][1][1:])
            last = ways[w][0][-1]
        if last == first:
            rings.append((chain, pts[:-1]))
        else:
            open_chains.append((chain, pts))
    # A chain started mid-way along a ring that a later way closes would be split in two;
    # rejoin open chains whose ends meet.
    joined = True
    while joined:
        joined = False
        ends = {ways[c[-1]][0][-1]: i for i, (c, _) in enumerate(open_chains)}
        for i, (c, p) in enumerate(open_chains):
            j = ends.get(ways[c[0]][0][0])
            if j is None:
                continue
            cj, pj = open_chains[j]
            if j == i:
                rings.append((c, p[:-1]))
                open_chains.pop(i)
            else:
                merged = (cj + c, pj + p[1:])
                for k in sorted((i, j), reverse=True):
                    open_chains.pop(k)
                open_chains.append(merged)
            joined = True
            break
    return rings, open_chains, forks


# ------------------------------------------------------------------------ checks

def bbox(ring):
    xs, ys = [p[0] for p in ring], [p[1] for p in ring]
    return min(xs), min(ys), max(xs), max(ys)


def dist_to_rings(x, y, rings, boxes, reach=50000.0) -> float:
    best = math.inf
    for ring, (x0, y0, x1, y1) in zip(rings, boxes):
        if x < x0 - reach or x > x1 + reach or y < y0 - reach or y > y1 + reach:
            continue
        for (ax, ay), (bx, by) in zip(ring, ring[1:] + ring[:1]):
            dx, dy = bx - ax, by - ay
            l2 = dx * dx + dy * dy
            t = 0.0 if not l2 else max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / l2))
            best = min(best, math.hypot(x - ax - t * dx, y - ay - t * dy))
    return best


def inside_any(x, y, rings, boxes) -> bool:
    return any(x0 <= x <= x1 and y0 <= y <= y1 and point_in_ring(x, y, r)
               for r, (x0, y0, x1, y1) in zip(rings, boxes))


def crossings(rings: list, cell: float = 200.0) -> set[tuple[int, int]]:
    """(ring, segment) for every segment that properly crosses another, any ring. Segment k
    of a ring runs from vertex k to vertex k + 1, wrapping."""
    segs = [(r, k, ring[k], ring[(k + 1) % len(ring)])
            for r, ring in enumerate(rings) for k in range(len(ring))]
    grid = collections.defaultdict(list)
    for i, (_, _, a, b) in enumerate(segs):
        for gx in range(int(min(a[0], b[0]) // cell), int(max(a[0], b[0]) // cell) + 1):
            for gy in range(int(min(a[1], b[1]) // cell), int(max(a[1], b[1]) // cell) + 1):
                grid[(gx, gy)].append(i)

    def side(a, b, c):
        v = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        return (v > 0) - (v < 0)

    bad = set()
    for members in grid.values():
        for i, m in enumerate(members):
            _, _, a, b = segs[m]
            for n in members[i + 1:]:
                _, _, c, d = segs[n]
                if side(a, b, c) * side(a, b, d) < 0 and side(c, d, a) * side(c, d, b) < 0:
                    bad |= {segs[m][:2], segs[n][:2]}
    return bad


def simplify(rings: list, tol: float) -> tuple[list, int, int, int]:
    """Douglas-Peucker on each ring, rounded to 1 m. Simplifying rings one at a time can
    make an outline cross itself or a neighbour across a narrow inlet (the raw OSM rings
    cross nowhere; at 25 m, 62 crossings appear). Every segment that crosses gets back the
    raw vertices it skipped, and the check repeats until nothing crosses or nothing is left
    to restore. Returns rings, rings collapsed, vertices restored, crossings left."""
    keep = []
    for ring in rings:
        index = {p: i for i, p in enumerate(ring)}
        keep.append(sorted({index[p] for p in simplify_ring(ring, tol)}))

    def draw(r):
        pts, idx = [], []
        for i in keep[r]:
            p = (int(round(rings[r][i][0])), int(round(rings[r][i][1])))
            if not pts or p != pts[-1]:
                pts.append(p)
                idx.append(i)
        if len(pts) > 1 and pts[0] == pts[-1]:
            pts.pop()
            idx.pop()
        return pts, idx

    restored = 0
    while True:
        drawn = [draw(r) for r in range(len(rings))]
        bad = crossings([pts for pts, _ in drawn])
        added = 0
        for r, k in bad:
            idx, n = drawn[r][1], len(rings[r])
            i, j = idx[k], idx[(k + 1) % len(idx)]
            span = range(i + 1, j) if j > i else [*range(i + 1, n), *range(0, j)]
            new = set(span) - set(keep[r])
            if new:
                keep[r] = sorted(set(keep[r]) | new)
                added += len(new)
        restored += added
        if not bad or not added:
            break
    out, collapsed = [], 0
    for pts, _ in drawn:
        if len(pts) >= 3 and abs(ring_area(pts)) > 0:
            out.append(pts)
        else:
            collapsed += 1
    return out, collapsed, restored, len(bad)


def encode(doc: dict) -> bytes:
    return json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


# -------------------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true", help="query Overpass even if an archive exists")
    ap.add_argument("--tiles", action="store_true", help="with --fetch: four tiles, not one box")
    ap.add_argument("--compare", nargs="*", type=float, default=[],
                    help="also report sizes at these tolerances (m)")
    args = ap.parse_args()

    paths = fetch(args.tiles) if args.fetch or not newest_archive() else newest_archive()
    print(f"osm: {', '.join(str(p.relative_to(REPO)) for p in paths)}")
    ways, bases = load_ways(paths)
    print(f"  data as of {bases}, {len(ways)} coastline ways, "
          f"{sum(len(n) for n, _ in ways.values()):,} way-nodes")

    rings, open_chains, forks = assemble(ways)
    print(f"\nassembled {len(rings)} closed rings; {len(open_chains)} chains did not close; "
          f"{len(forks)} nodes where two ways start")
    for n, w in forks.items():
        print(f"  fork at node {n}: ways {w}")
    for chain, pts in open_chains:
        (la0, lo0), (la1, lo1) = pts[0], pts[-1]
        side = "Britain" if is_britain(la0, lo0) and is_britain(la1, lo1) else "IRELAND?"
        print(f"  open [{side}] {len(chain)} ways {chain[:4]}{'...' if len(chain) > 4 else ''}, "
              f"{len(pts)} nodes, from {la0:.4f},{lo0:.4f} to {la1:.4f},{lo1:.4f}")

    kept, foreign, small, holes = [], [], [], []
    for chain, pts in rings:
        lat = sum(p[0] for p in pts) / len(pts)
        lon = sum(p[1] for p in pts) / len(pts)
        xy = [itm(la, lo) for la, lo in pts]
        area = ring_area(xy) / 1e6
        entry = (abs(area), (lat, lon), xy, chain)
        if is_britain(lat, lon):
            foreign.append(entry)
        elif area < 0:
            holes.append(entry)
        elif area < MIN_ISLAND_KM2:
            small.append(entry)
        else:
            kept.append(entry)
    kept.sort(key=lambda r: -r[0])
    print(f"\nrings in Great Britain (dropped): {len(foreign)}, largest "
          f"{[(round(a, 3), tuple(round(v, 3) for v in c)) for a, c, *_ in sorted(foreign, key=lambda r: -r[0])[:5]]}")
    print(f"clockwise rings (water enclosed by coastline): {len(holes)} "
          f"{[(round(a, 4), tuple(round(v, 3) for v in c), ch[:3]) for a, c, _, ch in holes[:10]]}")
    print(f"islands under {MIN_ISLAND_KM2} km2 (dropped): {len(small)}, "
          f"total {sum(a for a, *_ in small):.2f} km2")
    sizes = sorted(a for a, *_ in kept[1:] + small)
    for t in (0.001, 0.01, 0.05, 0.1, 1.0):
        print(f"  islands >= {t} km2: {sum(1 for a in sizes if a >= t)}")
    main_area = kept[0][0]
    print(f"kept {len(kept)} rings: main island {main_area:,.1f} km2 "
          f"(Natural Earth in network.json: 83,397 km2), {len(kept) - 1} islands")
    for a, (lat, lon), xy, _ in kept[1:13]:
        print(f"  {a:8.2f} km2 at {lat:.3f}, {lon:.3f}, {len(xy):,} vertices")
    raw_rings = [xy for _, _, xy, _ in kept]
    print(f"  raw vertices kept: {sum(len(r) for r in raw_rings):,}")

    meta = {
        "url": OVERPASS_URL, "query": query(BOX) if len(paths) == 1 else
        [query(b) for b in TILES], "osm_base": bases[0] if len(bases) == 1 else bases,
        "fetched": paths[0].name.split("-")[2].removesuffix(".json.gz"),
        "min_island_km2": MIN_ISLAND_KM2,
        "excluded": "Great Britain: rings whose mean vertex is north of 55.24 and east of -6.0 "
                    "(Kintyre) or south of 53.0 and east of -5.9 (Pembrokeshire)",
        "units": "ITM metres (EPSG:2157), rounded to 1; each ring is closed, its last "
                 "vertex joining its first (not repeated), anticlockwise with land inside",
    }
    for tol in sorted(set(args.compare) | {TOLERANCE_M}):
        simple, collapsed, restored, left = simplify(raw_rings, tol)
        body = encode({"rings": simple})
        print(f"\ntolerance {tol:g} m: {len(simple)} rings ({collapsed} collapsed), "
              f"{sum(len(r) for r in simple):,} vertices ({restored} restored to undo "
              f"crossings, {left} crossing segments left), {len(body):,} bytes, "
              f"{len(gzip.compress(body, 9)):,} gzipped")
        if tol != TOLERANCE_M:
            continue
        doc = {
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "license": "ODbL 1.0, © OpenStreetMap contributors",
            "source": meta, "tolerance_m": TOLERANCE_M,
            "rings": [[list(p) for p in r] for r in simple],
        }
        final = simple

    print(f"\nmain island after simplification: {abs(ring_area(final[0])) / 1e6:,.1f} km2")
    net = json.loads(NETWORK.read_text(encoding="utf-8"))
    boxes = [bbox(r) for r in final]
    # Signed: negative inland, positive out at sea.
    where = sorted((dist_to_rings(s["x"], s["y"], final, boxes, reach=5000)
                    * (-1 if inside_any(s["x"], s["y"], final, boxes) else 1), s["code"], s["name"])
                   for s in net["stations"] if s["x"] is not None)
    outside = [w for w in where if w[0] > 0]
    print(f"stations outside the coast: {len(outside)} of {len(where)}"
          + "".join(f"\n  {n} ({c}) {d:.0f} m" for d, c, n in reversed(outside)))
    print("  nearest the shore from inland: "
          + ", ".join([f"{n} ({c}) {-d:.0f} m" for d, c, n in where[::-1] if d <= 0][:12]))
    print("  Natural Earth's offshore eight now: " + ", ".join(
        f"{n} {-d:.0f} m inland" if d <= 0 else f"{n} {d:.0f} m OUT"
        for d, c, n in where if c in ("COBH", "CGLOE", "RBROK", "FOTA", "WXFRD", "RLEPT",
                                      "MHIDE", "DLERY")))

    body = encode(doc)
    OUT.write_bytes(body)
    print(f"\nwrote {OUT.relative_to(REPO)}: {len(body):,} bytes, "
          f"{len(gzip.compress(body, 9)):,} gzipped, {len(final)} rings, "
          f"{sum(len(r) for r in final):,} vertices")
    return 0


if __name__ == "__main__":
    sys.exit(main())
