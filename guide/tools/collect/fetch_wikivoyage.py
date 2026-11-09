#!/usr/bin/env python3
"""Wikivoyage (en, de): city article plus sub-articles, as listings and context text.

Output:
  raw/wikivoyage/<lang>_<Title>.wikitext   article source as fetched
  raw/wikivoyage/listings.ndjson           one record per listing template:
      {lang, article, section, subsection, kind, name, alt, wikidata, lat, lon,
       address, directions, phone, email, url, hours, price, content, image, lastedit}
  raw/wikivoyage/meta.json
  context/wikivoyage_<lang>_<Title>.md     prose of every section, listings reduced
                                           to their names (CC BY-SA 4.0)
"""
import collections
import os
import re
import urllib.parse

from common import args_city, ensure_dir, http_get, http_json, log, today, write_json, write_ndjson

EN_KINDS = {"see", "do", "buy", "eat", "drink", "sleep", "listing", "go", "marker"}
DE_KINDS = {"vcard", "marker"}
GAP = 4.0  # seconds between requests to one wiki


def find_templates(text):
    """Yield (start, end, body) for every top-level {{...}} in text."""
    i, n = 0, len(text)
    while True:
        i = text.find("{{", i)
        if i < 0:
            return
        depth, j = 0, i
        while j < n:
            if text.startswith("{{", j):
                depth += 1
                j += 2
            elif text.startswith("}}", j):
                depth -= 1
                j += 2
                if depth == 0:
                    break
            else:
                j += 1
        if depth:
            return
        yield i, j, text[i + 2:j - 2]
        i = j


def split_args(body):
    parts, cur, depth = [], [], 0
    i = 0
    while i < len(body):
        two = body[i:i + 2]
        if two in ("{{", "[["):
            depth += 1
            cur.append(two)
            i += 2
        elif two in ("}}", "]]"):
            depth -= 1
            cur.append(two)
            i += 2
        elif body[i] == "|" and depth == 0:
            parts.append("".join(cur))
            cur = []
            i += 1
        else:
            cur.append(body[i])
            i += 1
    parts.append("".join(cur))
    return parts


def plain(s):
    """Wikitext fragment -> plain text."""
    if not s:
        return ""
    s = re.sub(r"<!--.*?-->", "", s, flags=re.S)
    s = re.sub(r"<ref[^>]*/>|<ref[^>]*>.*?</ref>", "", s, flags=re.S)
    for _ in range(4):  # innermost templates first
        s = re.sub(r"\{\{([^{}]*)\}\}", _tpl_text, s)
    s = re.sub(r"\[\[(?:File|Image|Datei|Bild):[^\[\]]*(?:\[\[[^\]]*\]\][^\[\]]*)*\]\]", "", s, flags=re.I)
    s = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", s)
    s = re.sub(r"\[https?://\S+\s+([^\]]+)\]", r"\1", s)
    s = re.sub(r"\[https?://\S+\]", "", s)
    s = re.sub(r"'{2,}", "", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = s.replace("&nbsp;", " ")
    return re.sub(r"[ \t]+", " ", s).strip()


def _tpl_text(m):
    parts = m.group(1).split("|")
    name = parts[0].strip().lower()
    kv = {p.split("=", 1)[0].strip().lower(): p.split("=", 1)[1].strip() for p in parts[1:] if "=" in p}
    pos = [p.strip() for p in parts[1:] if "=" not in p]
    if name in EN_KINDS | DE_KINDS | {"station"}:
        return kv.get("name", "")
    if name in ("km", "m", "mi", "ft", "kg", "ha", "km2", "°c", "chf", "eur", "usd", "gbp"):
        return (pos[0] + " " if pos else "") + parts[0].strip()
    if name in ("convert",) and len(pos) >= 2:
        return pos[0] + " " + pos[1]
    if name in ("rint", "rail-interchange", "bahnlinie", "s-bahn", "tram", "bus") and pos:
        return " ".join(pos[-1:])
    if name in ("lang", "sprache") and len(pos) >= 2:
        return pos[1]
    if name in ("infobox", "cautionbox", "warningbox") and pos:
        return " ".join(pos)
    return ""


def parse_listing(body, lang):
    parts = split_args(body)
    kind = parts[0].strip().lower()
    kv = {}
    for p in parts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            if v.strip():
                kv[k.strip().lower()] = v.strip()
    if lang == "de":
        if kind not in DE_KINDS:
            return None
        kind = kv.get("type", kind).lower()
    elif kind not in EN_KINDS:
        return None
    else:
        kind = kv.get("type", kind).lower() if kind in ("listing", "marker") else kind
    name = plain(kv.get("name", ""))
    if not name and not kv.get("wikidata"):
        return None

    def num(k):
        try:
            return round(float(kv[k]), 6)
        except (KeyError, ValueError):
            return None
    rec = {
        "kind": kind, "name": name, "alt": plain(kv.get("alt", "")),
        "wikidata": kv.get("wikidata") if re.fullmatch(r"Q\d+", kv.get("wikidata", "")) else None,
        "lat": num("lat"), "lon": num("long"),
        "address": plain(kv.get("address", "")), "directions": plain(kv.get("directions", "")),
        "phone": plain(kv.get("phone", "")), "email": kv.get("email", ""),
        "url": kv.get("url", ""), "hours": plain(kv.get("hours", "")),
        "price": plain(kv.get("price", "")),
        "content": plain(kv.get("content", "") or kv.get("description", "")),
        "image": kv.get("image", ""), "lastedit": kv.get("lastedit", ""),
    }
    return {k: v for k, v in rec.items() if v not in ("", None)}


def process(lang, title, text):
    listings, out_lines = [], []
    section = subsection = ""
    # walk the text heading by heading so each listing knows its section
    chunks = re.split(r"^(={2,4}[^=\n]+={2,4})\s*$", text, flags=re.M)
    for chunk in chunks:
        h = re.fullmatch(r"(={2,4})\s*([^=\n]+?)\s*\1", chunk.strip())
        if h:
            if len(h.group(1)) == 2:
                section, subsection = plain(h.group(2)), ""
            else:
                subsection = plain(h.group(2))
            out_lines.append("\n" + "#" * len(h.group(1)) + " " + plain(h.group(2)) + "\n")
            continue

        def scan(t):  # listings may be nested inside other templates
            for _, _, body in find_templates(t):
                rec = parse_listing(body, lang)
                if rec:
                    listings.append({"lang": lang, "article": title, "section": section,
                                     "subsection": subsection, **rec})
                scan(body)
        scan(chunk)
        for line in chunk.split("\n"):
            raw = line.strip()
            if not raw or raw.startswith(("{|", "|}", "|-", "|", "!", "[[Category", "[[Kategorie", "__")):
                continue
            bullet = re.match(r"^([*#:;]+)\s*", raw)
            txt = plain(raw[bullet.end():] if bullet else raw)
            txt = txt.strip(" .,;:—–-") if len(txt) < 3 else txt
            if txt:
                out_lines.append(("- " if bullet else "") + txt)
    return listings, "\n".join(out_lines)


def main():
    cfg = args_city()
    out = ensure_dir(os.path.join(cfg["raw"], "wikivoyage"))
    ctx = ensure_dir(cfg["context"])
    all_listings, meta = [], {"fetched": today(), "license": "CC BY-SA 4.0", "articles": []}
    for lang, titles in cfg["wikivoyage"].items():
        base = f"https://{lang}.wikivoyage.org"
        titles = list(titles)
        try:  # sub-articles "City/District"
            r = http_json(base + "/w/api.php", min_interval=GAP, params={
                "action": "query", "list": "allpages", "apprefix": titles[0] + "/",
                "apfilterredir": "nonredirects", "aplimit": "100", "format": "json"})
            for p in r["query"]["allpages"]:
                if p["title"] not in titles:
                    titles.append(p["title"])
        except Exception as e:
            meta.setdefault("warnings", []).append(f"{lang} subpage listing failed: {e}")
        for title in titles:
            url = base + "/wiki/" + urllib.parse.quote(title.replace(" ", "_"))
            info = {"lang": lang, "title": title, "url": url}
            meta["articles"].append(info)
            try:
                text = http_get(url + "?action=raw", min_interval=GAP).decode("utf-8")
            except Exception as e:
                info["status"] = f"failed: {type(e).__name__}: {e}"
                continue
            if re.match(r"\s*#(REDIRECT|WEITERLEITUNG)", text, flags=re.I):
                info["status"] = "redirect: " + text.strip()[:80]
                continue
            safe = re.sub(r"[^\w.-]+", "_", title)
            with open(os.path.join(out, f"{lang}_{safe}.wikitext"), "w", encoding="utf-8") as f:
                f.write(text)
            listings, prose = process(lang, title, text)
            for l in listings:
                l["article_url"] = url
            all_listings += listings
            with open(os.path.join(ctx, f"wikivoyage_{lang}_{safe}.md"), "w", encoding="utf-8") as f:
                f.write(f"<!-- Source: {url} (Wikivoyage, CC BY-SA 4.0), fetched {today()}. "
                        f"Listings are reduced to their names; details are in places.ndjson. -->\n# {title}\n{prose}\n")
            info.update(status="ok", bytes=len(text.encode()), listings=len(listings),
                        with_coords=sum(1 for l in listings if "lat" in l),
                        with_wikidata=sum(1 for l in listings if "wikidata" in l))
            log(info)
    n = write_ndjson(os.path.join(out, "listings.ndjson"), all_listings)
    meta["listings"] = n
    meta["by_lang_kind"] = {lang: dict(collections.Counter(l["kind"] for l in all_listings if l["lang"] == lang).most_common())
                            for lang in cfg["wikivoyage"]}
    write_json(os.path.join(out, "meta.json"), meta)
    log(f"wrote {n} listings")


if __name__ == "__main__":
    main()
