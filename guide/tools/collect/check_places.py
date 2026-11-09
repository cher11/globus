#!/usr/bin/env python3
"""Validate places.ndjson, compute coverage statistics, spot-check well-known places.

Writes data/<city>/stats.json and prints a compact summary.
"""
import collections
import json
import os
import sys

from common import args_city, fold, haversine_m, in_bbox, write_json

SPOT = ["Grossmünster", "Fraumünster", "Kunsthaus", "Landesmuseum", "Lindenhof", "Uetliberg", "Zoo Zürich", "FIFA",
        "Letzigrund", "Hallenstadion", "Bahnhofstrasse", "Frau Gerolds", "Rote Fabrik", "Seebad Enge", "Sprüngli",
        "Kronenhalle", "Opernhaus", "Prime Tower", "Hiltl", "Cabaret Voltaire", "Dolder", "Zürichhorn", "Lake Zurich|Zürichsee",
        "Josef", "Street Parade", "Hardturm", "Swiss National Museum", "Niederdorf", "Sechseläutenplatz", "Botanischer Garten"]


def main():
    cfg = args_city()
    path = os.path.join(cfg["dir"], "places.ndjson")
    places, bad = [], 0
    for ln, line in enumerate(open(path, encoding="utf-8"), 1):
        try:
            places.append(json.loads(line))
        except Exception:
            bad += 1
    st = {"lines": len(places), "unparsable": bad}
    st["outside_bbox"] = sum(1 for p in places if not in_bbox(p["lat"], p["lon"], cfg["bbox"]))
    ids = collections.Counter(p["id"] for p in places)
    st["duplicate_ids"] = sum(1 for v in ids.values() if v > 1)
    st["missing_required"] = sum(1 for p in places if not all(k in p for k in ("id", "name", "lat", "lon", "group", "categories", "sources", "tier")))
    st["by_tier"] = dict(collections.Counter(p["tier"] for p in places))
    st["by_group"] = {g: {"all": n, "core": sum(1 for p in places if p["group"] == g and p["tier"] == "core")}
                      for g, n in collections.Counter(p["group"] for p in places).most_common()}
    st["by_category"] = dict(collections.Counter(c for p in places for c in p["categories"]).most_common())
    st["by_source_places"] = dict(collections.Counter(s["source"] for p in places for s in p["sources"]).most_common())
    st["by_source_set"] = dict(collections.Counter("+".join(sorted(s["source"] for s in p["sources"])) for p in places).most_common(15))
    st["by_license"] = dict(collections.Counter(l for p in places for l in p["licenses"]).most_common())

    def cov(sel):
        n = len(sel) or 1
        has = {
            "opening_hours": sum(1 for p in sel if p.get("opening_hours")),
            "description": sum(1 for p in sel if p.get("descriptions")),
            "description_long": sum(1 for p in sel if any(d["kind"] == "long" for d in p.get("descriptions", []))),
            "image": sum(1 for p in sel if p.get("images")),
            "website": sum(1 for p in sel if p.get("website")),
            "phone": sum(1 for p in sel if p.get("phone")),
            "address_street": sum(1 for p in sel if p.get("address", {}).get("street")),
            "district": sum(1 for p in sel if p.get("district")),
            "wikidata": sum(1 for p in sel if p.get("wikidata")),
            "cuisine": sum(1 for p in sel if p.get("cuisine")),
            "price": sum(1 for p in sel if p.get("price")),
            "accessibility": sum(1 for p in sel if p.get("accessibility")),
            "name_en": sum(1 for p in sel if "en" in p.get("names", {})),
        }
        return {"n": len(sel), **{k: round(v / n, 3) for k, v in has.items()}}
    st["coverage_all"] = cov(places)
    st["coverage_core"] = cov([p for p in places if p["tier"] == "core"])
    st["coverage_by_group_core"] = {g: cov([p for p in places if p["tier"] == "core" and p["group"] == g])
                                    for g in st["by_group"]}
    st["coverage_by_source_set"] = {}
    # within-data leftover duplicates: same folded name within 60 m
    grid = collections.defaultdict(list)
    for i, p in enumerate(places):
        grid[(int(p["lat"] / 0.001), int(p["lon"] / 0.001))].append(i)
    dups = []
    for i, p in enumerate(places):
        fp = fold(p["name"])
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for j in grid.get((int(p["lat"] / 0.001) + dx, int(p["lon"] / 0.001) + dy), ()):
                    if j > i and fold(places[j]["name"]) == fp and haversine_m(p["lat"], p["lon"], places[j]["lat"], places[j]["lon"]) <= 60:
                        dups.append((p["id"], places[j]["id"], p["name"]))
    st["same_name_within_60m_pairs"] = len(dups)
    st["same_name_within_60m_core_core"] = sum(1 for a, b, _ in dups if ids[a] and places[[q["id"] for q in places].index(a)]["tier"] == "core"
                                               and places[[q["id"] for q in places].index(b)]["tier"] == "core") if len(dups) < 400 else None
    st["same_name_within_60m_samples"] = [d[2] for d in dups[:15]]
    spot = {}
    for q in SPOT:
        keys = [fold(x) for x in q.split("|")]
        hits = [p for p in places if any(k in fold(p["name"]) or any(k in fold(v) for v in p.get("names", {}).values()) for k in keys)]
        hits.sort(key=lambda p: (p["tier"] != "core", -p.get("popularity", {}).get("score", 0)))
        spot[q] = {"n": len(hits), "top": [f"{p['name']} [{p['id']}] {p['tier']} {p['categories'][0]} src={'+'.join(s['source'][:4] for s in p['sources'])}"
                                          f" d={len(p.get('descriptions', []))} im={len(p.get('images', []))} oh={'y' if p.get('opening_hours') else 'n'}"
                                          for p in hits[:3]]}
    st["spot"] = spot
    write_json(os.path.join(cfg["dir"], "stats.json"), st)
    print(json.dumps({k: v for k, v in st.items() if k not in ("spot", "coverage_by_group_core", "by_category", "same_name_within_60m_samples")}, ensure_ascii=False))
    print(json.dumps(st["coverage_by_group_core"], ensure_ascii=False)[:2500])
    for q, v in spot.items():
        print(f"## {q}: {v['n']}")
        for t in v["top"]:
            print("   ", t)
    print("dups sample:", st["same_name_within_60m_samples"])


if __name__ == "__main__":
    main()
