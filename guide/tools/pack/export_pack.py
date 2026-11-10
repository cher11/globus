"""Export a city pack: one read-only SQLite file built from places.ndjson.

The pack is the contract between the city pipeline, the app and the backend
(docs/architecture.md, section 8). This is schema version 0: the tables and
columns of the target design exist, but the fields that the M1 clean-up step
derives (kind, rank, visibility, status, parent_pid, stable pid) are still
empty. The place id is the merge id from places.ndjson and is NOT stable
across rebuilds yet.

Licences are carried per text, image and source row; nothing is filtered out
here. Filtering by licence happens when a pack is built for distribution.

Usage: python3 export_pack.py [city] [data_version]
Output: guide/dist/<city>/<city>-v<data_version>.sqlite (+ .gz, manifest.json)
"""
import gzip
import hashlib
import json
import math
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone

GUIDE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(GUIDE, "tools", "collect"))
from config import CITIES  # noqa: E402

SCHEMA_VERSION = 0
CELL_DEG_LAT = 0.009  # about 1 km
LANGS = ("de", "en", "fr", "it", "ru")
RESTRICTED = {"restricted-zurich-tourism", "CC-BY-NC-ND-4.0", "CC-BY-NC-4.0", "CC BY-NC-ND", "CC BY-NC"}

SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE place (
  pid TEXT PRIMARY KEY,          -- merge id for now; stable ids arrive in M1
  name TEXT NOT NULL,
  lat REAL NOT NULL, lon REAL NOT NULL,
  cell TEXT NOT NULL,            -- ~1 km grid cell, 'row_col'
  kind TEXT,                     -- M1: venue, sight, area, institution, transport, route, non_destination
  primary_cat TEXT NOT NULL,     -- 'group/sub'
  grp TEXT NOT NULL,
  rank REAL,                     -- M1: 0..100
  visibility TEXT,               -- M1: recommended | catalog | hidden
  status TEXT,                   -- M1: open | closed | unknown
  tier TEXT NOT NULL,            -- core | minor (from the merge; replaced by visibility in M1)
  score REAL NOT NULL,           -- interim popularity heuristic from the merge
  sitelinks INTEGER, n_sources INTEGER NOT NULL,
  in_city INTEGER NOT NULL, kreis INTEGER, quartier TEXT,
  address TEXT, website TEXT, phone TEXT, price_text TEXT,
  hours_json TEXT,               -- M1: normalised intervals; raw variants are in place_hours
  geom TEXT,                     -- 'area' when lat/lon is a centroid
  wikidata TEXT, wikipedia_en TEXT, wikipedia_de TEXT,
  restricted INTEGER NOT NULL,   -- 1 if any row of this place carries a restricted licence
  parent_pid TEXT
);
CREATE INDEX place_cell ON place(cell);
CREATE INDEX place_grp ON place(grp, score DESC);
CREATE TABLE place_name (pid TEXT NOT NULL, lang TEXT NOT NULL, name TEXT NOT NULL, PRIMARY KEY (pid, lang));
CREATE TABLE place_category (pid TEXT NOT NULL, cat TEXT NOT NULL, ord INTEGER NOT NULL,
  confidence REAL, origin TEXT NOT NULL, PRIMARY KEY (pid, cat));
CREATE TABLE place_tag (pid TEXT NOT NULL, tag TEXT NOT NULL, PRIMARY KEY (pid, tag));
CREATE TABLE place_text (pid TEXT NOT NULL, lang TEXT NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL,
  origin TEXT NOT NULL, license TEXT, url TEXT);
CREATE INDEX place_text_pid ON place_text(pid);
CREATE TABLE place_image (pid TEXT NOT NULL, url TEXT NOT NULL, license TEXT, author TEXT,
  origin TEXT NOT NULL, page_url TEXT);
CREATE TABLE place_hours (pid TEXT NOT NULL, value TEXT NOT NULL, format TEXT NOT NULL, origin TEXT NOT NULL);
CREATE TABLE place_source (pid TEXT NOT NULL, source TEXT NOT NULL, source_id TEXT, url TEXT,
  license TEXT, last_edit TEXT);
CREATE INDEX place_source_pid ON place_source(pid);
CREATE VIRTUAL TABLE place_fts USING fts5(pid UNINDEXED, name, names, text, tags, tokenize='unicode61 remove_diacritics 2');
"""


def cell_of(lat, lon):
    deg_lon = CELL_DEG_LAT / math.cos(math.radians(lat))
    return f"{math.floor(lat / CELL_DEG_LAT)}_{math.floor(lon / deg_lon)}"


def tags_of(p):
    a = p.get("attributes") or {}
    tags = set()
    if a.get("outdoor_seating") in ("yes", True):
        tags.add("terrace")
    if a.get("takeaway") in ("yes", "only", True):
        tags.add("takeaway")
    for k, v in a.items():
        if k.startswith("diet:") and v in ("yes", "only", True):
            tags.add(k.replace(":", "_"))
    if a.get("brand"):
        tags.add("chain")
    if a.get("fee") == "no":
        tags.add("free")
    if a.get("zurich_card"):
        tags.add("zurich_card")
    wc = (p.get("accessibility") or {}).get("wheelchair")
    if wc in ("yes", "limited", "no"):
        tags.add(f"wheelchair_{wc}")
    return sorted(tags)


def address_of(p):
    a = p.get("address") or {}
    street = " ".join(x for x in (a.get("street"), a.get("housenumber")) if x)
    town = " ".join(x for x in (a.get("postcode"), a.get("city")) if x)
    return ", ".join(x for x in (street, town) if x) or None


def main():
    city = sys.argv[1] if len(sys.argv) > 1 else "zurich"
    data_version = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    cfg = CITIES[city]
    src = os.path.join(GUIDE, "data", city, "places.ndjson")
    out_dir = os.path.join(GUIDE, "dist", city)
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"{city}-v{data_version}.sqlite")
    if os.path.exists(out):
        os.remove(out)

    con = sqlite3.connect(out)
    con.executescript(SCHEMA)
    n = {"places": 0, "core": 0, "texts": 0, "images": 0, "sources": 0, "restricted": 0}
    with open(src, encoding="utf-8") as f:
        for line in f:
            p = json.loads(line)
            pid = p["id"]
            src_rows = p.get("sources") or []
            lic_by_source = {}
            for s in src_rows:
                lic_by_source.setdefault(s["source"], s.get("license"))
            restricted = int(any(l in RESTRICTED for l in (p.get("licenses") or [])))
            pop = p.get("popularity") or {}
            dist = p.get("district") or {}
            wp = p.get("wikipedia") or {}
            cats = p.get("categories") or [f"{p['group']}/other"]
            con.execute(
                "INSERT INTO place VALUES (?,?,?,?,?,NULL,?,?,NULL,NULL,NULL,?,?,?,?,?,?,?,?,?,?,?,NULL,?,?,?,?,?,NULL)",
                (pid, p["name"], p["lat"], p["lon"], cell_of(p["lat"], p["lon"]), cats[0], p["group"],
                 p.get("tier", "minor"), pop.get("score") or 0, pop.get("wikidata_sitelinks"),
                 pop.get("n_sources") or len(src_rows), int(bool(dist)), dist.get("kreis"), dist.get("quartier"),
                 address_of(p), p.get("website"), p.get("phone"), p.get("price"),
                 p.get("geom"), p.get("wikidata"), wp.get("enwiki"), wp.get("dewiki"), restricted))
            for lang, name in (p.get("names") or {}).items():
                if lang in LANGS and name:
                    con.execute("INSERT OR IGNORE INTO place_name VALUES (?,?,?)", (pid, lang, name))
            for i, c in enumerate(cats):
                con.execute("INSERT OR IGNORE INTO place_category VALUES (?,?,?,NULL,'merge')", (pid, c, i))
            tags = tags_of(p)
            for t in tags:
                con.execute("INSERT INTO place_tag VALUES (?,?)", (pid, t))
            texts = []
            for d in p.get("descriptions") or []:
                con.execute("INSERT INTO place_text VALUES (?,?,?,?,?,?,?)",
                            (pid, d["lang"], d["kind"], d["text"], d["source"], lic_by_source.get(d["source"]), d.get("url")))
                texts.append(d["text"])
                n["texts"] += 1
            for im in p.get("images") or []:
                con.execute("INSERT INTO place_image VALUES (?,?,?,?,?,?)",
                            (pid, im["url"], im.get("license"), im.get("author"), im.get("source"), im.get("page")))
                n["images"] += 1
            for h in p.get("opening_hours") or []:
                con.execute("INSERT INTO place_hours VALUES (?,?,?,?)", (pid, h["value"], h["format"], h["source"]))
            for s in src_rows:
                con.execute("INSERT INTO place_source VALUES (?,?,?,?,?,?)",
                            (pid, s["source"], s.get("id"), s.get("url"), s.get("license"),
                             s.get("last_edit") or s.get("modified")))
                n["sources"] += 1
            names = " ".join(v for v in (p.get("names") or {}).values() if v)
            con.execute("INSERT INTO place_fts VALUES (?,?,?,?,?)", (pid, p["name"], names, " ".join(texts), " ".join(tags + cats)))
            n["places"] += 1
            n["core"] += p.get("tier") == "core"
            n["restricted"] += restricted

    built = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    meta = {"city": city, "name": cfg["name"], "tz": "Europe/Zurich", "bbox": json.dumps(cfg["bbox"]),
            "schema_version": str(SCHEMA_VERSION), "data_version": str(data_version), "built": built,
            "source_file": "data/%s/places.ndjson" % city,
            "attribution": "© OpenStreetMap contributors (ODbL); Stadt Zürich Open Data (CC0); Wikidata (CC0); "
                           "Wikipedia, Wikivoyage (CC BY-SA 4.0); Zürich Tourismus, www.zuerich.com"}
    con.executemany("INSERT INTO meta VALUES (?,?)", meta.items())
    con.commit()
    con.execute("VACUUM")
    con.close()

    gz = out + ".gz"
    with open(out, "rb") as fi, gzip.open(gz, "wb", compresslevel=9) as fo:
        shutil.copyfileobj(fi, fo)
    sha = hashlib.sha256(open(gz, "rb").read()).hexdigest()
    manifest = {"cities": [{"id": city, "name": cfg["name"], "tz": "Europe/Zurich", "bbox": cfg["bbox"],
                            "pack": {"data_version": data_version, "schema_version": SCHEMA_VERSION,
                                     "url": os.path.basename(gz), "sha256": sha, "bytes": os.path.getsize(gz)},
                            "map_style": "https://tiles.openfreemap.org/styles/liberty",
                            "langs": ["en", "de", "ru"]}],
                "min_app_version": "0.1.0", "built": built}
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(json.dumps(n), f"sqlite={os.path.getsize(out)/1e6:.1f}MB gz={os.path.getsize(gz)/1e6:.1f}MB")


if __name__ == "__main__":
    main()
