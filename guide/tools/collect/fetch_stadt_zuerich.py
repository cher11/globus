#!/usr/bin/env python3
"""City of Zürich open geodata (WFS, GeoJSON output) for the datasets in config.

Output (raw/stadt_zuerich/):
  features.ndjson   one record per feature of every POI-like layer, geometry
                    reduced to a representative point:
                    {dataset, layer, fid, lat, lon, geom, props}
                    (empty / null properties dropped)
  kreise.geojson, quartiere.geojson   district polygons (coords rounded to 5 dp)
  districts.json    names + centroids of Kreise and Quartiere
  meta.json         per dataset: CKAN title, licence, layers, counts
"""
import collections
import os
import re

from common import (Blocked, args_city, ensure_dir, http_get, http_json, in_bbox, log,
                    today, write_json, write_ndjson)

POLYGON_SETS = {"Stadtkreise": "kreise.geojson", "Statistische_Quartiere": "quartiere.geojson"}


def rings(geom):
    t, c = geom["type"], geom["coordinates"]
    if t == "Polygon":
        return [c[0]]
    if t == "MultiPolygon":
        return [p[0] for p in c]
    return []


def ring_centroid(r):
    a = cx = cy = 0.0
    for (x1, y1, *_), (x2, y2, *_) in zip(r, r[1:]):
        f = x1 * y2 - x2 * y1
        a += f
        cx += (x1 + x2) * f
        cy += (y1 + y2) * f
    if abs(a) < 1e-14:
        return sum(p[0] for p in r) / len(r), sum(p[1] for p in r) / len(r), 0.0
    return cx / (3 * a), cy / (3 * a), abs(a) / 2


def rep_point(geom):
    """Representative lon/lat: the point, the middle vertex, or the centroid of the largest ring."""
    if not geom:
        return None
    t, c = geom["type"], geom["coordinates"]
    if t == "Point":
        return c[0], c[1]
    if t == "MultiPoint":
        return sum(p[0] for p in c) / len(c), sum(p[1] for p in c) / len(c)
    if t == "LineString":
        m = c[len(c) // 2]
        return m[0], m[1]
    if t == "MultiLineString":
        line = max(c, key=len)
        m = line[len(line) // 2]
        return m[0], m[1]
    rs = rings(geom)
    if rs:
        x, y, _ = max((ring_centroid(r) for r in rs), key=lambda v: v[2])
        return x, y
    return None


def round_geom(geom, nd=5):
    def rnd(c):
        if isinstance(c[0], (int, float)):
            return [round(c[0], nd), round(c[1], nd)]
        return [rnd(x) for x in c]
    return {"type": geom["type"], "coordinates": rnd(geom["coordinates"])}


def clean_props(p):
    return {k: v for k, v in p.items() if v not in (None, "", " ", [], "null")}


def main():
    cfg = args_city()
    sz = cfg["stadt_zuerich"]
    out = ensure_dir(os.path.join(cfg["raw"], "stadt_zuerich"))
    rows, meta, districts = [], {}, {}
    for ds, ckan_id in sz["datasets"].items():
        m = meta[ds] = {"ckan_id": ckan_id, "wfs": sz["wfs_base"] + ds, "layers": {}}
        try:
            pk = http_json(sz["ckan_base"] + "package_show", params={"id": ckan_id})["result"]
            m.update(title=pk.get("title"), license_id=pk.get("license_id"),
                     license_title=pk.get("license_title"), author=pk.get("author"),
                     modified=pk.get("metadata_modified", "")[:10],
                     url="https://data.stadt-zuerich.ch/dataset/" + ckan_id)
        except Exception as e:  # licence lookup must not stop the geodata fetch
            m["ckan_error"] = f"{type(e).__name__}: {e}"
        try:
            caps = http_get(sz["wfs_base"] + ds, params={
                "SERVICE": "WFS", "REQUEST": "GetCapabilities", "VERSION": "1.1.0"}).decode("utf-8", "replace")
        except Blocked:
            raise
        except Exception as e:
            m["error"] = f"{type(e).__name__}: {e}"
            log(f"{ds}: FAILED {m['error']}")
            continue
        layers = re.findall(r"<FeatureType>\s*<Name>([^<]+)</Name>", caps) or re.findall(r"<Name>([^<]+)</Name>", caps)
        for layer in layers:
            try:
                fc = http_json(sz["wfs_base"] + ds, params={
                    "SERVICE": "WFS", "REQUEST": "GetFeature", "VERSION": "1.1.0",
                    "TYPENAME": layer, "OUTPUTFORMAT": "GeoJSON", "SRSNAME": "EPSG:4326"})
            except Blocked:
                raise
            except Exception as e:
                m["layers"][layer] = {"error": f"{type(e).__name__}: {e}"}
                continue
            feats = fc.get("features", [])
            gt = collections.Counter((f.get("geometry") or {}).get("type") for f in feats)
            m["layers"][layer] = {"features": len(feats), "geometry": dict(gt)}
            if ds in POLYGON_SETS and gt.get("Polygon", 0) + gt.get("MultiPolygon", 0) == len(feats) and feats:
                key = POLYGON_SETS[ds]
                if key not in districts:  # first polygon layer of the dataset wins
                    for f in feats:
                        f["geometry"] = round_geom(f["geometry"])
                        f["properties"] = clean_props(f["properties"])
                    write_json(os.path.join(out, key), {"type": "FeatureCollection", "features": feats}, indent=None)
                    districts[key] = [dict(f["properties"], **dict(zip(("lon", "lat"), (round(v, 6) for v in rep_point(f["geometry"])))))
                                      for f in feats]
                    m["layers"][layer]["saved_as"] = key
                continue
            kept = 0
            for f in feats:
                pt = rep_point(f.get("geometry"))
                if not pt:
                    continue
                rows.append({"dataset": ds, "layer": layer, "fid": f.get("id"),
                             "lat": round(pt[1], 6), "lon": round(pt[0], 6),
                             "geom": f["geometry"]["type"], "props": clean_props(f.get("properties") or {})})
                kept += 1
            m["layers"][layer]["kept"] = kept
        log(ds, {k: v.get("features", v.get("error")) for k, v in m["layers"].items()})
    n = write_ndjson(os.path.join(out, "features.ndjson"), rows)
    write_json(os.path.join(out, "districts.json"), districts)
    write_json(os.path.join(out, "meta.json"), {
        "source": "https://data.stadt-zuerich.ch/ (WFS " + sz["wfs_base"] + ")",
        "fetched": today(), "features": n,
        "by_dataset": dict(collections.Counter(r["dataset"] for r in rows)),
        "datasets": meta})
    log(f"wrote {n} features")


if __name__ == "__main__":
    main()
