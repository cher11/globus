"""Cut the vector tiles and the label glyphs for the data viewer.

The viewer page may not load tiles or fonts from other hosts, so both ship as
files that Claude uploads as artifact assets; the page keeps them in memory and
answers MapLibre's tile and glyph requests from there.

Tiles: BBBike's ready Zurich extract in the Shortbread schema (PMTiles, 33 MB,
https://download.bbbike.org/osm/bbbike/Zuerich/Zuerich.osm.pmtiles-shortbread.zip,
built by tilemaker from OSM, (c) OpenStreetMap contributors, ODbL). The script
rewrites the archive: only tiles that touch the viewer bounding box, zoom <= 14
(MapLibre overzooms the rest), and only the layers the style draws; written as two files (zoom <= 13 and zoom 14).

Glyphs: pre-built SDF glyph PBFs of Noto Sans (npm package smp-noto-glyphs,
SIL OFL). Only the ranges needed for German/Latin, Cyrillic and punctuation,
bundled into one file, gzip members as they are, read by the page with
DecompressionStream.

Usage: python3 build_tiles.py [city] [--src PATH] [--glyphs-dir PATH] [--report]
  --src         the .pmtiles file or the .zip from BBBike (default: downloaded into dist/<city>/_src/)
  --glyphs-dir  .../node_modules/smp-noto-glyphs/fixtures/glyphs (default: ./node_modules or `npm pack`ed copy)
Needs: pip install pmtiles
Output: dist/<city>/tiles-lo.pmtiles, tiles-hi.pmtiles, glyphs.bin

glyphs.bin layout: u32 LE header length, JSON header {"<first>-<last>": [offset, length]}
(offsets from the end of the header), then the concatenated .pbf.gz members.
"""
import argparse
import gzip
import json
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

from pmtiles.reader import MmapSource, all_tiles
from pmtiles.tile import Compression, TileType, zxy_to_tileid
from pmtiles.writer import Writer

GUIDE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SRC_URL = "https://download.bbbike.org/osm/bbbike/Zuerich/Zuerich.osm.pmtiles-shortbread.zip"
BBOX = (8.44, 47.30, 8.63, 47.465)  # W, S, E, N of the viewer (basemap.json bbox plus a margin)
MAX_Z = 14
# Layers the style draws. Dropped: parking/school sites, boundaries (the page draws
# its own district borders), dams, aerialways, piers, ferries, bridge outlines.
# The archive is written as two files, so the page can show the map after the small
# one and fetch the big one (all of the detail is in zoom 14) in the background.
SPLIT_Z = 13  # tiles-lo.pmtiles: zoom <= 13, tiles-hi.pmtiles: zoom 14
KEEP = {"land", "water_polygons", "water_lines", "water_lines_labels", "water_polygons_labels",
        "streets", "street_polygons", "street_labels", "street_labels_points",
        "buildings", "addresses", "pois", "public_transport", "place_labels",
        "streets_polygons_labels"}
# Glyph ranges: Basic Latin + Latin-1, Latin Extended-A, Greek/Cyrillic (768-1023 is
# combining marks + Greek), Cyrillic, General Punctuation.
GLYPH_RANGES = ["0-255", "256-511", "768-1023", "1024-1279", "8192-8447"]


def tile_bounds(z, x, y):
    n = 2 ** z
    lon = lambda xx: xx / n * 360 - 180
    lat = lambda yy: math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * yy / n))))
    return lon(x), lat(y + 1), lon(x + 1), lat(y)


def varint(b, i):
    r = s = 0
    while True:
        c = b[i]
        i += 1
        r |= (c & 0x7F) << s
        if c < 0x80:
            return r, i
        s += 7


def layer_name(body):
    j = 0
    while j < len(body):
        tag, j = varint(body, j)
        wt = tag & 7
        if wt == 0:
            _, j = varint(body, j)
        elif wt == 2:
            ln, j = varint(body, j)
            if tag >> 3 == 1:
                return body[j:j + ln].decode()
            j += ln
        elif wt == 5:
            j += 4
        elif wt == 1:
            j += 8
        else:
            raise ValueError("wire type %d" % wt)
    raise ValueError("layer without a name")


def filter_layers(raw, keep, sizes=None):
    """Drop layers by name straight on the protobuf (tile field 3 = layer, layer field 1 = name)."""
    out = bytearray()
    i = 0
    while i < len(raw):
        start = i
        tag, i = varint(raw, i)
        wt = tag & 7
        if wt == 2:
            ln, i = varint(raw, i)
            body = raw[i:i + ln]
            i += ln
            if tag >> 3 == 3:
                name = layer_name(body)
                if sizes is not None:
                    sizes[name] = sizes.get(name, 0) + len(raw[start:i])
                if name not in keep:
                    continue
            out += raw[start:i]
        elif wt == 0:
            _, i = varint(raw, i)
            out += raw[start:i]
        else:
            raise ValueError("unexpected wire type %d" % wt)
    return bytes(out)


def fetch_pmtiles(src, workdir):
    if src and src.endswith(".pmtiles"):
        return src
    zpath = src
    if not zpath:
        zpath = os.path.join(workdir, "Zuerich.pmtiles.zip")
        if not os.path.exists(zpath):
            print("downloading", SRC_URL)
            urllib.request.urlretrieve(SRC_URL, zpath)
    with zipfile.ZipFile(zpath) as z:
        name = next(n for n in z.namelist() if n.endswith(".pmtiles"))
        out = os.path.join(workdir, os.path.basename(name))
        if not os.path.exists(out):
            with z.open(name) as r, open(out, "wb") as w:
                shutil.copyfileobj(r, w)
    return out


def cut_tiles(src_pm, outs, report):
    W, S, E, N = BBOX
    f = open(src_pm, "rb")
    src = MmapSource(f)
    tiles = []
    sizes = {}
    for (z, x, y), data in all_tiles(src):
        if z > MAX_Z:
            continue
        x0, y0, x1, y1 = tile_bounds(z, x, y)
        if x1 < W or x0 > E or y1 < S or y0 > N:
            continue
        raw = gzip.decompress(data)
        kept = filter_layers(raw, KEEP, sizes if report else None)
        if not kept:
            continue
        tiles.append((zxy_to_tileid(z, x, y), z, gzip.compress(kept, 9, mtime=0)))
    tiles.sort()
    if report:
        print("raw bytes per layer in the cut area (before dropping):")
        for k, v in sorted(sizes.items(), key=lambda kv: -kv[1]):
            print("  %-26s %8.0f KB %s" % (k, v / 1024, "" if k in KEEP else "(dropped)"))
    for out_path, pick in outs:
        write_archive(out_path, [t for t in tiles if pick(t[1])])


def write_archive(out_path, tiles):
    W, S, E, N = BBOX
    with open(out_path, "wb") as of:
        w = Writer(of)
        for tid, z, data in tiles:
            w.write_tile(tid, data)
        zs = [t[1] for t in tiles]
        header = {
            "version": 3, "clustered": True,
            "internal_compression": Compression.GZIP, "tile_compression": Compression.GZIP,
            "tile_type": TileType.MVT, "min_zoom": min(zs), "max_zoom": max(zs),
            "min_lon_e7": int(W * 1e7), "min_lat_e7": int(S * 1e7), "max_lon_e7": int(E * 1e7), "max_lat_e7": int(N * 1e7),
            "center_zoom": 12, "center_lon_e7": int((W + E) / 2 * 1e7), "center_lat_e7": int((S + N) / 2 * 1e7),
        }
        metadata = {"name": "Zurich Shortbread cut", "format": "pbf", "type": "baselayer",
                    "attribution": "(c) OpenStreetMap contributors, tilemaker/Shortbread via BBBike",
                    "vector_layers": [{"id": k} for k in sorted(KEEP)]}
        w.finalize(header, metadata)
    print("%s: %d tiles, z%d-%d, %.2f MB" % (os.path.basename(out_path), len(tiles), min(zs), max(zs), os.path.getsize(out_path) / 1e6))


def find_glyphs_dir(arg):
    if arg:
        return arg
    for base in (os.getcwd(), os.path.join(GUIDE, "tools", "pack")):
        d = os.path.join(base, "node_modules", "smp-noto-glyphs", "fixtures", "glyphs")
        if os.path.isdir(d):
            return d
    tmp = tempfile.mkdtemp()
    subprocess.run(["npm", "pack", "smp-noto-glyphs@2.0.0", "--silent"], cwd=tmp, check=True)
    tgz = next(n for n in os.listdir(tmp) if n.endswith(".tgz"))
    subprocess.run(["tar", "xzf", tgz], cwd=tmp, check=True)
    return os.path.join(tmp, "package", "fixtures", "glyphs")


def build_glyphs(gdir, out_path):
    members, index, off = [], {}, 0
    for r in GLYPH_RANGES:
        b = open(os.path.join(gdir, r + ".pbf.gz"), "rb").read()
        index[r] = [off, len(b)]
        members.append(b)
        off += len(b)
    head = json.dumps(index, separators=(",", ":")).encode()
    with open(out_path, "wb") as f:
        f.write(struct.pack("<I", len(head)) + head + b"".join(members))
    print("glyphs.bin: %d ranges, %.0f KB" % (len(index), os.path.getsize(out_path) / 1024))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("city", nargs="?", default="zurich")
    ap.add_argument("--src")
    ap.add_argument("--glyphs-dir")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    dist = os.path.join(GUIDE, "dist", a.city)
    cache = os.path.join(dist, "_src")
    os.makedirs(cache, exist_ok=True)
    src = fetch_pmtiles(a.src, cache)
    cut_tiles(src, [(os.path.join(dist, "tiles-lo.pmtiles"), lambda z: z <= SPLIT_Z),
                    (os.path.join(dist, "tiles-hi.pmtiles"), lambda z: z > SPLIT_Z)], a.report)
    build_glyphs(find_glyphs_dir(a.glyphs_dir), os.path.join(dist, "glyphs.bin"))


if __name__ == "__main__":
    main()
