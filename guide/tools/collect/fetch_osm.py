#!/usr/bin/env python3
"""OpenStreetMap POIs from a regional .pbf extract.

Downloads the extract into cache/ (kept out of git) unless it is already there,
then writes every object inside the city bbox that carries one of the guide
relevant tags, with ALL of its tags:

  raw/osm/pois.ndjson   {osm_type, osm_id, lat, lon, geom, area_m2?, last_edit, tags}
  raw/osm/meta.json     extract timestamp, counts per key/value, skipped relations

Ways become centroids (polygon centroid when closed, middle node otherwise),
multipolygon relations become the centroid of their largest outer ring.
Relations of other types (site, route ...) have no geometry of their own and
are only listed in meta.json.
"""
import collections
import math
import os
import sys
import urllib.request

import osmium

from common import args_city, ensure_dir, in_bbox, log, today, write_json, write_ndjson
from config import USER_AGENT

# Keys where every value is kept.
KEEP_ANY = ("tourism", "historic", "leisure", "sport", "attraction")

KEEP_VALUES = {
    "amenity": {
        # food and drink
        "restaurant", "cafe", "bar", "pub", "biergarten", "nightclub", "fast_food",
        "ice_cream", "food_court",
        # culture
        "theatre", "cinema", "arts_centre", "library", "community_centre",
        "events_venue", "conference_centre", "exhibition_centre", "music_venue",
        "planetarium", "casino", "gambling", "social_centre", "public_bookcase",
        "place_of_worship", "monastery", "townhall", "university", "studio",
        # nightlife, adult venues are kept raw and dropped in the merge
        "stripclub", "hookah_lounge",
        # outdoors and practical
        "marketplace", "fountain", "drinking_water", "water_point", "toilets",
        "shower", "bbq", "public_bath", "sauna", "dive_centre", "dojo",
        "bicycle_rental", "boat_rental", "ferry_terminal", "luggage_locker",
        "locker", "pharmacy", "hospital", "police", "bureau_de_change",
        "grave_yard", "clock", "lounger",
    },
    "shop": {
        "bakery", "pastry", "confectionery", "chocolate", "cheese", "wine", "alcohol",
        "beverages", "deli", "coffee", "tea", "farm", "butcher", "seafood",
        "greengrocer", "spices", "health_food", "supermarket",
        "books", "mall", "department_store", "gift", "souvenir", "art", "antiques",
        "second_hand", "charity", "watches", "jewelry", "music", "musical_instrument",
        "outdoor", "sports", "bicycle", "toys", "games", "comic", "stationery",
        "clothes", "shoes", "boutique", "bag", "fashion_accessories", "leather",
        "interior_decoration", "cosmetics", "perfumery", "photo", "craft", "pottery",
        "ticket",
    },
    "natural": {"peak", "beach", "cave_entrance", "hill", "saddle", "spring", "rock",
                "stone", "water"},
    "waterway": {"waterfall"},
    "man_made": {"tower", "obelisk", "lighthouse", "observatory", "telescope",
                 "bridge", "watermill", "windmill", "pier"},
    "railway": {"station", "halt"},
    "aerialway": {"station"},
    "craft": {"brewery", "winery", "distillery", "confectionery", "chocolate",
              "bakery", "coffee_roaster"},
    "place": {"square", "island", "islet"},
    "landuse": {"cemetery", "recreation_ground", "vineyard"},
    "club": {"sport"},
}

ALL_KEYS = KEEP_ANY + tuple(KEEP_VALUES)


def wanted(tags):
    for k in KEEP_ANY:
        if k in tags:
            return True
    for k, vals in KEEP_VALUES.items():
        v = tags.get(k)
        if v is not None and v in vals:
            # unnamed water bodies, rocks, bridges and vineyards are just map furniture
            if (k, v) in (("natural", "water"), ("natural", "rock"), ("natural", "stone"),
                          ("man_made", "bridge"), ("landuse", "vineyard"),
                          ("man_made", "pier"), ("landuse", "recreation_ground")) \
                    and "name" not in tags:
                continue
            return True
    return False


def ring_centroid_area(pts):
    """Centroid (lat, lon) and area in m² of a closed ring of (lon, lat)."""
    if len(pts) < 4:
        return None
    lat0 = sum(p[1] for p in pts) / len(pts)
    kx = 111320.0 * math.cos(math.radians(lat0))
    ky = 110540.0
    x0, y0 = pts[0]
    xy = [((x - x0) * kx, (y - y0) * ky) for x, y in pts]
    a = cx = cy = 0.0
    for (x1, y1), (x2, y2) in zip(xy, xy[1:]):
        cr = x1 * y2 - x2 * y1
        a += cr
        cx += (x1 + x2) * cr
        cy += (y1 + y2) * cr
    if abs(a) < 1e-6:
        return None
    cx /= 3 * a
    cy /= 3 * a
    return (y0 + cy / ky, x0 + cx / kx, abs(a) / 2)


def node_list(nodes):
    out = []
    for n in nodes:
        if n.location.valid():
            out.append((n.location.lon, n.location.lat))
    return out


def download(url, path):
    if os.path.exists(path) and os.path.getsize(path) > 1_000_000:
        log(f"using cached {path} ({os.path.getsize(path) / 1e6:.1f} MB)")
        return
    log(f"downloading {url}")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    tmp = path + ".part"
    with urllib.request.urlopen(req, timeout=600) as r, open(tmp, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    os.replace(tmp, path)


def main():
    cfg = args_city()
    bbox = cfg["bbox"]
    out = ensure_dir(os.path.join(cfg["raw"], "osm"))
    pbf = os.path.join(ensure_dir(cfg["cache"]), cfg["osm"]["pbf_name"])
    download(cfg["osm"]["pbf_url"], pbf)

    reader = osmium.io.Reader(pbf)
    extract_ts = reader.header().get("osmosis_replication_timestamp") or reader.header().get("timestamp")
    reader.close()

    rows = []
    kv = collections.Counter()
    skipped_rel = []
    fp = (osmium.FileProcessor(pbf)
          .with_locations()
          .with_areas()
          .with_filter(osmium.filter.KeyFilter(*ALL_KEYS)))
    for o in fp:
        tags = {t.k: t.v for t in o.tags}
        if not wanted(tags):
            continue
        rec = None
        if o.is_node():
            if not o.location.valid():
                continue
            rec = {"osm_type": "node", "osm_id": o.id, "lat": o.location.lat,
                   "lon": o.location.lon, "geom": "point"}
        elif o.is_way():
            pts = node_list(o.nodes)
            if not pts:
                continue
            closed = len(pts) >= 4 and pts[0] == pts[-1] and tags.get("area") != "no"
            ca = ring_centroid_area(pts) if closed else None
            if ca:
                rec = {"osm_type": "way", "osm_id": o.id, "lat": ca[0], "lon": ca[1],
                       "geom": "area", "area_m2": round(ca[2])}
            else:
                mid = pts[len(pts) // 2]
                rec = {"osm_type": "way", "osm_id": o.id, "lat": mid[1], "lon": mid[0],
                       "geom": "line"}
        elif o.is_area():
            if o.from_way():
                continue  # closed ways are handled above from their own nodes
            best, total = None, 0.0
            for outer in o.outer_rings():
                ca = ring_centroid_area(node_list(outer))
                if not ca:
                    continue
                total += ca[2]
                if best is None or ca[2] > best[2]:
                    best = ca
                for inner in o.inner_rings(outer):
                    ia = ring_centroid_area(node_list(inner))
                    if ia:
                        total -= ia[2]
            if not best:
                continue
            rec = {"osm_type": "relation", "osm_id": o.orig_id(), "lat": best[0],
                   "lon": best[1], "geom": "area", "area_m2": round(max(total, 0))}
        elif o.is_relation():
            if tags.get("type") not in ("multipolygon", "boundary"):
                skipped_rel.append({"osm_id": o.id, "type": tags.get("type"),
                                    "name": tags.get("name"),
                                    "tags": {k: tags[k] for k in ALL_KEYS if k in tags}})
            continue
        if rec is None or not in_bbox(rec["lat"], rec["lon"], bbox):
            continue
        rec["lat"] = round(rec["lat"], 7)
        rec["lon"] = round(rec["lon"], 7)
        try:
            rec["last_edit"] = o.timestamp.strftime("%Y-%m-%d")
        except Exception:
            pass
        rec["tags"] = tags
        rows.append(rec)
        for k in ALL_KEYS:
            if k in tags:
                kv[f"{k}={tags[k]}"] += 1

    rows.sort(key=lambda r: (r["osm_type"], r["osm_id"]))
    n = write_ndjson(os.path.join(out, "pois.ndjson"), rows)
    write_json(os.path.join(out, "meta.json"), {
        "source": cfg["osm"]["pbf_url"],
        "extract_timestamp": extract_ts,
        "fetched": today(),
        "bbox_wsen": bbox,
        "objects": n,
        "by_type": dict(collections.Counter(r["osm_type"] for r in rows)),
        "named": sum(1 for r in rows if "name" in r["tags"]),
        "by_tag": dict(kv.most_common()),
        "relations_without_geometry": skipped_rel,
    })
    log(f"wrote {n} OSM objects ({sum(1 for r in rows if 'name' in r['tags'])} named); "
        f"{len(skipped_rel)} non-area relations listed in meta.json")


if __name__ == "__main__":
    main()
