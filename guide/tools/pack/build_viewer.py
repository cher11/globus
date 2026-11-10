"""Build the data viewer: one self-contained HTML page with a map and
top lists of the city's core places, read from the city pack.

The page embeds its data and the district borders (it may not load tiles or
data from other hosts) and draws the map with MapLibre GL JS (jsDelivr, pinned)
from vector tiles and glyphs that it downloads whole from its own artifact:
three files uploaded as artifact assets (build_tiles.py makes them), whose
URLs are baked into the page here. It is published as a private claude.ai
artifact; marks the author sets there go to the artifact's database, where
Claude reads them for the M1 clean-up.

Usage: python3 build_viewer.py [city] [data_version] --maplibre-css PATH
           [--tiles-lo-url URL] [--tiles-hi-url URL] [--glyphs-url URL]
  --maplibre-css  dist/maplibre-gl.css of the npm package maplibre-gl@5.24.0
  *-url           where the page fetches tiles-lo.pmtiles, tiles-hi.pmtiles, glyphs.bin
                  (the asset URLs the upload returns); default: next to the page
Needs: dist/<city>/<city>-v<N>.sqlite (export_pack.py), dist/<city>/basemap.json (build_basemap.py,
       only its district borders and labels are used)
Output: dist/<city>/viewer.html
"""
import argparse
import json
import os
import re
import sqlite3
import sys

GUIDE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
HERE = os.path.dirname(__file__)
ZT_RESTRICTED = "restricted-zurich-tourism"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("city", nargs="?", default="zurich")
    ap.add_argument("version", nargs="?", type=int, default=1)
    ap.add_argument("--maplibre-css", required=True)
    ap.add_argument("--tiles-lo-url", default="tiles-lo.pmtiles")
    ap.add_argument("--tiles-hi-url", default="tiles-hi.pmtiles")
    ap.add_argument("--glyphs-url", default="glyphs.bin")
    ap.add_argument("--b64", action="store_true",
                    help="write <file>.b64.txt copies of the map files and point the page at them "
                         "(claude.ai artifacts serve no binary file types)")
    a = ap.parse_args()
    city, ver, css_path = a.city, a.version, a.maplibre_css
    dist = os.path.join(GUIDE, "dist", city)
    if a.b64:
        import base64
        for attr in ("tiles_lo_url", "tiles_hi_url", "glyphs_url"):
            name = getattr(a, attr)
            with open(os.path.join(dist, name), "rb") as fi, open(os.path.join(dist, name + ".b64.txt"), "wb") as fo:
                fo.write(base64.b64encode(fi.read()))
            setattr(a, attr, name + ".b64.txt")
    con = sqlite3.connect(os.path.join(dist, f"{city}-v{ver}.sqlite"))
    con.row_factory = sqlite3.Row
    meta = dict(con.execute("SELECT key, value FROM meta").fetchall())

    def many(sql):
        out = {}
        for r in con.execute(sql):
            out.setdefault(r[0], []).append(r)
        return out

    cats = many("SELECT pid, cat FROM place_category ORDER BY pid, ord")
    srcs = many("SELECT pid, source, license FROM place_source")
    tags = many("SELECT pid, tag FROM place_tag")
    texts = many("SELECT pid, lang, kind, text, origin, license FROM place_text")
    hours = many("SELECT pid, value, format FROM place_hours")
    imgs = many("SELECT pid, origin FROM place_image")
    names = many("SELECT pid, lang, name FROM place_name")

    def pick_text(rows):
        if not rows:
            return None
        order = {("en", "short"): 0, ("de", "short"): 1, ("en", "long"): 2, ("de", "long"): 3}
        r = sorted(rows, key=lambda r: order.get((r["lang"], r["kind"]), 9))[0]
        t = r["text"] if len(r["text"]) <= 420 else r["text"][:417].rsplit(" ", 1)[0] + "…"
        return {"t": t, "o": r["origin"], "lic": r["license"]}

    places = []
    for r in con.execute("SELECT * FROM place WHERE tier = 'core'"):
        pid = r["pid"]
        src_rows = srcs.get(pid, [])
        hrs = hours.get(pid, [])
        h = next((x["value"] for x in hrs if x["format"] == "osm"), None) or next((x["value"] for x in hrs if x["format"] != "text_closing_hours_de"), None)
        alt = " ".join(sorted({x["name"] for x in names.get(pid, []) if x["name"] != r["name"]}))
        wp = ["en", r["wikipedia_en"]] if r["wikipedia_en"] else (["de", r["wikipedia_de"]] if r["wikipedia_de"] else None)
        p = {"i": pid, "n": r["name"], "a": round(r["lat"], 5), "o": round(r["lon"], 5), "g": r["grp"],
             "c": [x["cat"] for x in cats.get(pid, [])], "s": int(r["score"]), "k": r["sitelinks"], "ns": r["n_sources"],
             "src": sorted({x["source"] for x in src_rows}), "kr": r["kreis"], "q": r["quartier"], "ad": r["address"],
             "w": r["website"], "h": h, "d": pick_text(texts.get(pid)), "fd": bool(texts.get(pid)),
             "fh": any(x["format"] in ("osm", "schema_org") for x in hrs),
             "ff": any(x["origin"] != "zuerich_tourism" for x in imgs.get(pid, [])),
             "t": [x["tag"] for x in tags.get(pid, [])], "r": bool(r["restricted"]),
             "lic": sorted({x["license"] for x in src_rows if x["license"]}), "wp": wp, "alt": alt}
        places.append({k: v for k, v in p.items() if v not in (None, "", [])} | {"c": p["c"], "src": p["src"], "s": p["s"], "ns": p["ns"]})

    counts = {"all": con.execute("SELECT COUNT(*) FROM place").fetchone()[0], "core": len(places)}
    data = {"places": places, "counts": counts, "version": ver, "built": meta["built"], "city": meta["name"]}
    bm = json.load(open(os.path.join(dist, "basemap.json"), encoding="utf-8"))
    basemap = json.dumps({k: bm[k] for k in ("bbox", "border", "labels")}, separators=(",", ":"))
    css = open(css_path, encoding="utf-8").read()
    css = re.sub(r"url\([^)]*\)", "none", css)  # control icons are redrawn as text in the template
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)

    tpl = open(os.path.join(HERE, "viewer_template.html"), encoding="utf-8").read()
    js_data = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html = tpl.replace("/*__MAPLIBRE_CSS__*/", css).replace("/*__DATA__*/", js_data).replace("/*__BASEMAP__*/", basemap)
    for ph, url in (("__TILES_LO_URL__", a.tiles_lo_url), ("__TILES_HI_URL__", a.tiles_hi_url), ("__GLYPHS_URL__", a.glyphs_url)):
        assert ph in html and re.fullmatch(r"[A-Za-z0-9._~:/?#@!$&'()*+,;=%-]+", url), ph
        html = html.replace(ph, url)
    out = os.path.join(dist, "viewer.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"places={len(places)} html={os.path.getsize(out)/1e6:.2f}MB")


if __name__ == "__main__":
    main()
