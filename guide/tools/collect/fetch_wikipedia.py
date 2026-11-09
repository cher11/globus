#!/usr/bin/env python3
"""Wikipedia intro extracts (en, de) for the most notable places (by Wikidata sitelink count).

Reads places.ndjson (so run build_places.py first, then again afterwards to merge the extracts).
Batches 20 titles per API call, honours Retry-After, stops early if the API keeps refusing.
Output: raw/wikipedia/extracts.ndjson  {qid, lang, title, extract, url, revid?}  (CC BY-SA 4.0)
        raw/wikipedia/meta.json
"""
import json
import os
import sys
import urllib.parse

from common import args_city, ensure_dir, http_json, log, read_ndjson, today, write_json, write_ndjson

TOP = 150
GAP = 5.0


def main():
    cfg = args_city()
    out = ensure_dir(os.path.join(cfg["raw"], "wikipedia"))
    places = [p for p in read_ndjson(os.path.join(cfg["dir"], "places.ndjson")) if p.get("wikipedia")]
    places.sort(key=lambda p: -p["popularity"].get("wikidata_sitelinks", 0))
    top = places[:TOP]
    rows, failed = [], []
    for lang in cfg["wikipedia"]["langs"]:
        key = lang + "wiki"
        items = [(p["wikidata"], p["wikipedia"][key]) for p in top if key in p["wikipedia"]]
        for i in range(0, len(items), 20):
            chunk = items[i:i + 20]
            try:
                r = http_json(f"https://{lang}.wikipedia.org/w/api.php", min_interval=GAP, retries=4, params={
                    "action": "query", "prop": "extracts", "exintro": "1", "explaintext": "1", "exlimit": "20",
                    "redirects": "1", "titles": "|".join(t for _, t in chunk), "format": "json", "formatversion": "2"})
            except Exception as e:
                log(f"{lang} chunk {i}: {type(e).__name__}: {e}")
                failed.append({"lang": lang, "from": i, "error": str(e)})
                continue
            q = r.get("query", {})
            alias = {}
            for k in ("normalized", "redirects"):
                for m in q.get(k, []):
                    alias[m["to"]] = m["from"]
            by_title = {}
            for pg in q.get("pages", []):
                src_title = alias.get(pg["title"], pg["title"])
                src_title = alias.get(src_title, src_title)
                by_title[src_title] = pg
            for qid, t in chunk:
                pg = by_title.get(t)
                if pg and pg.get("extract"):
                    rows.append({"qid": qid, "lang": lang, "title": pg["title"], "extract": pg["extract"].strip()[:1500],
                                 "url": f"https://{lang}.wikipedia.org/wiki/" + urllib.parse.quote(pg["title"].replace(" ", "_"))})
            log(f"{lang} {min(i + 20, len(items))}/{len(items)}: {len(rows)} extracts")
    write_ndjson(os.path.join(out, "extracts.ndjson"), rows)
    write_json(os.path.join(out, "meta.json"), {"fetched": today(), "license": "CC BY-SA 4.0 (Wikipedia contributors)",
                                                "source": "https://{en,de}.wikipedia.org/w/api.php (prop=extracts)",
                                                "requested": len(top), "extracts": len(rows), "failed_chunks": failed})


if __name__ == "__main__":
    main()
