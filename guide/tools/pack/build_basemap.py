"""Build a small, self-contained basemap for the data viewer.

The viewer is published as a page that cannot load map tiles from other
hosts, so it draws its own map: water, rivers, green areas, main roads and
railways from the OSM extract, plus city district borders. Geometry is
simplified (Douglas-Peucker in metres) and rounded to 5 decimals.

Usage: python3 build_basemap.py [city]
Input:  data/<city>/cache/<pbf>, data/<city>/raw/stadt_zuerich/kreise.geojson
Output: dist/<city>/basemap.json  {layer: [[ring or line as [lon,lat]...], ...]}
"""
import json
import math
import os
import sys

import osmium

GUIDE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(GUIDE, "tools", "collect"))
from config import CITIES  # noqa: E402

ROADS = {"motorway": "road_major", "trunk": "road_major", "primary": "road_major",
         "secondary": "road_minor", "tertiary": "road_minor"}
TOL = {"water": 12, "green": 20, "river": 10, "road_major": 8, "road_minor": 10, "rail": 10, "border": 15}
MIN_AREA = {"water": 3000, "green": 40000}


def proj(lat0):
    k = math.cos(math.radians(lat0))
    return lambda lon, lat: (lon * 111320 * k, lat * 110540)


def dp(pts, tol, P):
    """Douglas-Peucker on [lon, lat] points, distance in metres."""
    if len(pts) < 3:
        return pts
    xy = [P(*p) for p in pts]
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        (x1, y1), (x2, y2) = xy[a], xy[b]
        dx, dy = x2 - x1, y2 - y1
        L = math.hypot(dx, dy)
        best, idx = -1.0, -1
        for i in range(a + 1, b):
            x, y = xy[i]
            # closed ring: the base segment is a point, measure from it instead
            d = math.hypot(x - x1, y - y1) if L < 1e-6 else abs(dy * x - dx * y + x2 * y1 - y2 * x1) / L
            if d > best:
                best, idx = d, i
        if best > tol:
            keep[idx] = True
            stack += [(a, idx), (idx, b)]
    return [p for p, k in zip(pts, keep) if k]


def ring_area(pts, P):
    xy = [P(*p) for p in pts]
    return abs(sum(xy[i][0] * xy[i - 1][1] - xy[i - 1][0] * xy[i][1] for i in range(len(xy)))) / 2


class H(osmium.SimpleHandler):
    def __init__(self, bbox):
        super().__init__()
        self.w, self.s, self.e, self.n = bbox
        self.out = {k: [] for k in ("water", "green", "river", "road_major", "road_minor", "rail")}
        self.P = proj((self.s + self.n) / 2)

    def inside(self, pts):
        return any(self.w <= x <= self.e and self.s <= y <= self.n for x, y in pts)

    def add(self, layer, pts):
        pts = dp(pts, TOL[layer], self.P)
        if len(pts) >= 2 and self.inside(pts):
            self.out[layer].append([[round(x, 5), round(y, 5)] for x, y in pts])

    def way(self, w):
        t = w.tags
        layer = None
        if t.get("waterway") in ("river", "canal"):
            layer = "river"
        elif t.get("highway") in ROADS:
            layer = ROADS[t.get("highway")]
        elif t.get("railway") == "rail" and not t.get("service"):
            layer = "rail"
        if not layer:
            return
        try:
            pts = [(n.lon, n.lat) for n in w.nodes]
        except osmium.InvalidLocationError:
            return
        self.add(layer, pts)

    def area(self, a):
        t = a.tags
        if t.get("natural") == "water" or t.get("waterway") == "riverbank" or t.get("landuse") == "reservoir":
            layer = "water"
        elif t.get("landuse") == "forest" or t.get("natural") == "wood" or t.get("leisure") in ("park", "nature_reserve") \
                or t.get("landuse") == "cemetery":
            layer = "green"
        else:
            return
        try:
            for outer in a.outer_rings():
                pts = [(n.lon, n.lat) for n in outer]
                if ring_area(pts, self.P) < MIN_AREA[layer]:
                    continue
                self.add(layer, pts)
        except osmium.InvalidLocationError:
            return


class WaterRelations(osmium.SimpleHandler):
    """Large lakes are multipolygon relations cut by the extract boundary, so
    osmium cannot assemble them. Collect their outer member ways instead."""
    def __init__(self):
        super().__init__()
        self.rel_ways = {}

    def relation(self, r):
        t = r.tags
        if t.get("type") == "multipolygon" and (t.get("natural") == "water" or t.get("water") == "lake"):
            self.rel_ways[r.id] = [m.ref for m in r.members if m.type == "w" and m.role in ("outer", "")]


class WayCoords(osmium.SimpleHandler):
    def __init__(self, ids):
        super().__init__()
        self.ids, self.coords = ids, {}

    def way(self, w):
        if w.id in self.ids:
            try:
                self.coords[w.id] = [(n.lon, n.lat) for n in w.nodes]
            except osmium.InvalidLocationError:
                pass


def join_chains(parts):
    """Join way polylines that share end points; return chains."""
    chains = [list(p) for p in parts if len(p) >= 2]
    merged = True
    while merged:
        merged = False
        for i in range(len(chains)):
            for j in range(i + 1, len(chains)):
                a, b = chains[i], chains[j]
                if a[-1] == b[0]:
                    chains[i] = a + b[1:]
                elif a[-1] == b[-1]:
                    chains[i] = a + b[::-1][1:]
                elif a[0] == b[-1]:
                    chains[i] = b + a[1:]
                elif a[0] == b[0]:
                    chains[i] = b[::-1] + a[1:]
                else:
                    continue
                del chains[j]
                merged = True
                break
            if merged:
                break
    return chains


def close_cut_polygon(chains, P):
    """Turn the open shore chains of a cut lake into one ring by joining
    them end to end (closing lines run outside the extract)."""
    chains = sorted(chains, key=len, reverse=True)
    ring = list(chains[0])
    for c in chains[1:]:
        d = lambda p, q: math.dist(P(*p), P(*q))
        if d(ring[-1], c[-1]) < d(ring[-1], c[0]):
            c = c[::-1]
        ring += c
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    return ring


def main():
    city = sys.argv[1] if len(sys.argv) > 1 else "zurich"
    cfg = CITIES[city]
    pbf = os.path.join(GUIDE, "data", city, "cache", cfg["osm"]["pbf_name"])
    h = H(cfg["bbox"])
    h.apply_file(pbf, locations=True)
    out = h.out
    wr = WaterRelations()
    wr.apply_file(pbf)
    ids = {i for ws in wr.rel_ways.values() for i in ws}
    wc = WayCoords(ids)
    wc.apply_file(pbf, locations=True)
    for rid, ways in wr.rel_ways.items():
        parts = [wc.coords[w] for w in ways if w in wc.coords]
        if len(parts) < len(ways) and parts:  # incomplete: assembly failed, rebuild it
            chains = join_chains(parts)
            closed = [c for c in chains if c[0] == c[-1]]
            open_ = [c for c in chains if c[0] != c[-1]]
            rings = closed + ([close_cut_polygon(open_, h.P)] if open_ else [])
            for ring in rings:
                if ring_area(ring, h.P) >= 1e5:
                    h.add("water", ring)
    kreise = json.load(open(os.path.join(GUIDE, "data", city, "raw", "stadt_zuerich", "kreise.geojson"), encoding="utf-8"))
    out["border"], labels = [], []
    for f in kreise["features"]:
        g = f["geometry"]
        polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        for poly in polys:
            ring = [p[:2] for p in poly[0]]
            out["border"].append([[round(x, 5), round(y, 5)] for x, y in dp(ring, TOL["border"], h.P)])
            if ring_area(ring, h.P) > 1e6:
                cx = sum(p[0] for p in ring) / len(ring)
                cy = sum(p[1] for p in ring) / len(ring)
                props = f.get("properties") or {}
                num = props.get("name") or props.get("bezeichnung")
                labels.append({"t": str(int(float(num))) if str(num).replace(".", "").isdigit() else str(num), "lon": round(cx, 5), "lat": round(cy, 5)})
    out["labels"] = labels
    out["bbox"] = cfg["bbox"]
    dst = os.path.join(GUIDE, "dist", city, "basemap.json")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"))
    print({k: len(v) for k, v in out.items() if isinstance(v, list)}, f"{os.path.getsize(dst)/1e6:.2f}MB",
          "kreis props:", list((kreise['features'][0].get('properties') or {}).keys())[:8])


if __name__ == "__main__":
    main()
