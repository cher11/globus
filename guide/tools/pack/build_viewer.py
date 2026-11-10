"""Build the data viewer: one self-contained HTML page with a map and
top lists of the city's core places, read from the city pack.

The page embeds its data and basemap (it may not load tiles or data from
other hosts) and loads Leaflet from cdnjs. It is published as a private
claude.ai artifact; marks the author sets there go to the artifact's
database, where Claude reads them for the M1 clean-up.

Usage: python3 build_viewer.py [city] [data_version] [leaflet_css_path]
Needs: dist/<city>/<city>-v<N>.sqlite (export_pack.py), dist/<city>/basemap.json (build_basemap.py)
Output: dist/<city>/viewer.html
"""
import json
import os
import re
import sqlite3
import sys

GUIDE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
HERE = os.path.dirname(__file__)
ZT_RESTRICTED = "restricted-zurich-tourism"


def main():
    city = sys.argv[1] if len(sys.argv) > 1 else "zurich"
    ver = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    css_path = sys.argv[3] if len(sys.argv) > 3 else None
    dist = os.path.join(GUIDE, "dist", city)
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
    basemap = open(os.path.join(dist, "basemap.json"), encoding="utf-8").read()
    css = open(css_path, encoding="utf-8").read() if css_path else ""
    css = re.sub(r"url\([^)]*\)", "none", css)  # icon images are unused and could not load anyway
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)

    tpl = open(os.path.join(HERE, "viewer_template.html"), encoding="utf-8").read()
    js_data = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html = tpl.replace("/*__LEAFLET_CSS__*/", css).replace("/*__DATA__*/", js_data).replace("/*__BASEMAP__*/", basemap)
    out = os.path.join(dist, "viewer.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"places={len(places)} html={os.path.getsize(out)/1e6:.2f}MB")


if __name__ == "__main__":
    main()
