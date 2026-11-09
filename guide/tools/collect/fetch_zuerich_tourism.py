#!/usr/bin/env python3
"""Zürich Tourism Open Data API v2: category tree plus every category's objects.

Output (raw/zuerich_tourism/):
  categories.json  the tree exactly as served, plus object counts per category
  objects.ndjson   one object per identifier (objects repeat across categories);
                   "_category_ids" lists every category the object was served under
  meta.json        fetch date, counts, licence breakdown

Per-category responses are kept in cache/zuerich_tourism/ so a re-run only
re-downloads with --refresh.
"""
import collections
import json
import os
import sys

from common import (args_city, ensure_dir, http_get, log, today, write_json,
                    write_ndjson)


def main():
    cfg = args_city()
    refresh = "--refresh" in sys.argv
    zt = cfg["zuerich_tourism"]
    out = ensure_dir(os.path.join(cfg["raw"], "zuerich_tourism"))
    cache = ensure_dir(os.path.join(cfg["cache"], "zuerich_tourism"))

    tree = json.loads(http_get(zt["base"] + zt["tree_path"]))
    log(f"category tree: {len(tree)} nodes")

    objects = {}
    cats_of = collections.defaultdict(list)
    counts = {}
    failed = []
    for node in sorted(tree, key=lambda n: int(n["id"])):
        cid = node["id"]
        cpath = os.path.join(cache, f"{cid}.json")
        if refresh or not os.path.exists(cpath):
            try:
                body = http_get(zt["base"] + "/en" + node["path"], min_interval=1.5)
            except Exception as e:  # keep going, report at the end
                log(f"  category {cid} failed: {e}")
                failed.append(cid)
                continue
            with open(cpath, "wb") as f:
                f.write(body)
        with open(cpath, encoding="utf-8") as f:
            try:
                items = json.load(f)
            except json.JSONDecodeError:
                log(f"  category {cid}: not JSON")
                failed.append(cid)
                continue
        if not isinstance(items, list):
            items = []
        counts[cid] = len(items)
        for it in items:
            oid = str(it.get("identifier"))
            objects.setdefault(oid, it)
            cats_of[oid].append(cid)
        log(f"  {cid:>6} {node['name']['en']:<40} {len(items):>5}  (unique so far {len(objects)})")

    for node in tree:
        node["_object_count"] = counts.get(node["id"])
    write_json(os.path.join(out, "categories.json"), tree)

    rows = []
    for oid in sorted(objects, key=lambda x: (len(x), x)):
        o = dict(objects[oid])
        o["_category_ids"] = sorted(set(cats_of[oid]), key=int)
        rows.append(o)
    n = write_ndjson(os.path.join(out, "objects.ndjson"), rows)

    lic = collections.Counter((o.get("license") or "")[:60] for o in rows)
    types = collections.Counter(o.get("@type") for o in rows)
    write_json(os.path.join(out, "meta.json"), {
        "source": zt["base"] + zt["tree_path"],
        "fetched": today(),
        "categories": len(tree),
        "categories_failed": failed,
        "unique_objects": n,
        "by_license": dict(lic.most_common()),
        "by_schema_type": dict(types.most_common()),
    })
    log(f"wrote {n} unique objects; failed categories: {failed}")


if __name__ == "__main__":
    main()
