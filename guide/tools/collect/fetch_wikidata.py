#!/usr/bin/env python3
"""Wikidata items with coordinates in the city (P131 chain) or in the bbox.

Several narrow SPARQL queries are joined in Python instead of one wide query;
that keeps each under the WDQS timeout and avoids row explosion.

Output (raw/wikidata/):
  items.ndjson  one consolidated record per item:
                {qid, lat, lon, sitelinks, labels, descriptions, aliases,
                 types:[{qid,label}], claims:{Pxxx:[{value,label?}]}, wiki:{site:title}}
  meta.json     fetch date, counts, the queries used
"""
import collections
import json
import os
import re
import urllib.parse

from common import args_city, ensure_dir, http_get, log, today, write_json, write_ndjson

ENDPOINT = "https://query.wikidata.org/sparql"
LANGS = ("de", "en", "ru", "fr", "it")

# property -> short name, only for documentation; values are stored under the P-id.
PROPS = {
    "P18": "image", "P856": "official website", "P571": "inception",
    "P1619": "date of official opening", "P576": "dissolved or demolished",
    "P3999": "date of official closure", "P5817": "state of use",
    "P84": "architect", "P149": "architectural style", "P170": "creator",
    "P1435": "heritage designation", "P381": "Swiss PCP inventory number",
    "P131": "located in administrative entity", "P276": "location",
    "P361": "part of", "P669": "located on street", "P670": "house number",
    "P6375": "street address", "P281": "postal code", "P1329": "phone",
    "P2044": "elevation", "P2048": "height", "P1083": "maximum capacity",
    "P466": "occupant", "P641": "sport", "P127": "owned by", "P137": "operator",
    "P138": "named after", "P1174": "visitors per year", "P373": "Commons category",
    "P402": "OSM relation id", "P10689": "OSM way id", "P11693": "OSM node id",
    "P3134": "TripAdvisor id", "P1566": "GeoNames id", "P154": "logo image",
    "P5775": "image of interior", "P3025": "open days", "P2795": "directions",
}

SITES = {
    "https://en.wikipedia.org/": "enwiki", "https://de.wikipedia.org/": "dewiki",
    "https://ru.wikipedia.org/": "ruwiki", "https://fr.wikipedia.org/": "frwiki",
    "https://it.wikipedia.org/": "itwiki", "https://als.wikipedia.org/": "alswiki",
    "https://en.wikivoyage.org/": "enwikivoyage", "https://de.wikivoyage.org/": "dewikivoyage",
    "https://commons.wikimedia.org/": "commonswiki",
}


def sparql(query):
    body = urllib.parse.urlencode({"query": query}).encode()
    raw = http_get(ENDPOINT, data=body, min_interval=2.0, timeout=120, headers={
        "Accept": "application/sparql-results+json",
        "Content-Type": "application/x-www-form-urlencoded",
    })
    return json.loads(raw)["results"]["bindings"]


def qid(uri):
    return uri.rsplit("/", 1)[-1]


def main():
    cfg = args_city()
    w, s, e, n = cfg["bbox"]
    city_q = cfg["wikidata"]["city_qid"]
    out = ensure_dir(os.path.join(cfg["raw"], "wikidata"))

    item_set = f"""
  {{ SELECT DISTINCT ?item WHERE {{
      {{ SERVICE wikibase:box {{ ?item wdt:P625 ?c0 .
          bd:serviceParam wikibase:cornerSouthWest "Point({w} {s})"^^geo:wktLiteral .
          bd:serviceParam wikibase:cornerNorthEast "Point({e} {n})"^^geo:wktLiteral . }} }}
      UNION {{ ?item wdt:P131+ wd:{city_q} ; wdt:P625 ?c1 . }}
  }} }}"""
    queries = {}
    items = {}

    queries["core"] = f"SELECT ?item ?coord ?sl WHERE {{ {item_set} ?item wdt:P625 ?coord ; wikibase:sitelinks ?sl . }}"
    for b in sparql(queries["core"]):
        m = re.match(r"Point\(([-\d.eE]+) ([-\d.eE]+)\)", b["coord"]["value"])
        if not m:
            continue
        q = qid(b["item"]["value"])
        if q in items:
            continue  # several coordinates: keep the first
        items[q] = {"qid": q, "lat": round(float(m.group(2)), 7), "lon": round(float(m.group(1)), 7),
                    "sitelinks": int(b["sl"]["value"]), "labels": {}, "descriptions": {},
                    "aliases": {}, "types": [], "claims": {}, "wiki": {}}
    log(f"core: {len(items)} items")

    lang_filter = ", ".join(f'"{l}"' for l in LANGS)
    queries["labels"] = f"SELECT ?item ?l WHERE {{ {item_set} ?item rdfs:label ?l . FILTER(LANG(?l) IN ({lang_filter})) }}"
    for b in sparql(queries["labels"]):
        it = items.get(qid(b["item"]["value"]))
        if it:
            it["labels"][b["l"]["xml:lang"]] = b["l"]["value"]
    queries["descriptions"] = f"SELECT ?item ?d WHERE {{ {item_set} ?item schema:description ?d . FILTER(LANG(?d) IN ({lang_filter})) }}"
    for b in sparql(queries["descriptions"]):
        it = items.get(qid(b["item"]["value"]))
        if it:
            it["descriptions"][b["d"]["xml:lang"]] = b["d"]["value"]
    queries["aliases"] = f'SELECT ?item ?a WHERE {{ {item_set} ?item skos:altLabel ?a . FILTER(LANG(?a) IN ("de", "en")) }}'
    for b in sparql(queries["aliases"]):
        it = items.get(qid(b["item"]["value"]))
        if it:
            it["aliases"].setdefault(b["a"]["xml:lang"], []).append(b["a"]["value"])
    log("labels, descriptions, aliases done")

    queries["types"] = f"""SELECT ?item ?t ?tl WHERE {{ {item_set} ?item wdt:P31 ?t .
      OPTIONAL {{ ?t rdfs:label ?tl . FILTER(LANG(?tl) = "en") }} }}"""
    for b in sparql(queries["types"]):
        it = items.get(qid(b["item"]["value"]))
        if it:
            t = {"qid": qid(b["t"]["value"]), "label": b.get("tl", {}).get("value")}
            if t not in it["types"]:
                it["types"].append(t)
    log("types done")

    props = sorted(PROPS, key=lambda p: int(p[1:]))
    for i in range(0, len(props), 8):
        chunk = props[i:i + 8]
        values = " ".join(f"wdt:{p}" for p in chunk)
        q = f"""SELECT ?item ?p ?v ?vl WHERE {{ {item_set} VALUES ?p {{ {values} }} ?item ?p ?v .
          OPTIONAL {{ ?v rdfs:label ?vl . FILTER(LANG(?vl) = "en") }} }}"""
        queries[f"claims_{i // 8}"] = q
        for b in sparql(q):
            it = items.get(qid(b["item"]["value"]))
            if not it:
                continue
            p = qid(b["p"]["value"])
            v = b["v"]["value"]
            entry = {"value": qid(v) if v.startswith("http://www.wikidata.org/entity/") else v}
            if "vl" in b:
                entry["label"] = b["vl"]["value"]
            if entry not in it["claims"].setdefault(p, []):
                it["claims"][p].append(entry)
        log(f"claims {chunk} done")

    site_values = " ".join(f"<{s_}>" for s_ in SITES)
    queries["sitelinks"] = f"""SELECT ?item ?site ?title WHERE {{ {item_set}
      VALUES ?site {{ {site_values} }}
      ?article schema:about ?item ; schema:isPartOf ?site ; schema:name ?title . }}"""
    for b in sparql(queries["sitelinks"]):
        it = items.get(qid(b["item"]["value"]))
        if it:
            it["wiki"][SITES[b["site"]["value"]]] = b["title"]["value"]
    log("sitelinks done")

    rows = sorted(items.values(), key=lambda r: int(r["qid"][1:]))
    nrows = write_ndjson(os.path.join(out, "items.ndjson"), rows)
    tc = collections.Counter(t["label"] or t["qid"] for r in rows for t in r["types"])
    write_json(os.path.join(out, "meta.json"), {
        "source": ENDPOINT,
        "fetched": today(),
        "items": nrows,
        "with_enwiki": sum(1 for r in rows if "enwiki" in r["wiki"]),
        "with_dewiki": sum(1 for r in rows if "dewiki" in r["wiki"]),
        "with_image": sum(1 for r in rows if "P18" in r["claims"]),
        "sitelinks_ge_1": sum(1 for r in rows if r["sitelinks"] >= 1),
        "properties": PROPS,
        "top_types": dict(tc.most_common(150)),
        "queries": queries,
    })
    log(f"wrote {nrows} Wikidata items")


if __name__ == "__main__":
    main()
