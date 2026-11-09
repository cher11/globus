"""Shared helpers: polite HTTP, file IO, text normalisation, small geo maths."""
import datetime
import json
import math
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

from config import USER_AGENT, city

_last_hit = {}  # host -> monotonic time of the last request


class Blocked(Exception):
    """The proxy or the server refused the request for policy reasons (403/407)."""


def today():
    return datetime.date.today().isoformat()


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def http_get(url, params=None, headers=None, min_interval=1.0, retries=6,
             timeout=180, data=None):
    """GET (or POST when data is given) with per-host spacing and back-off.

    Honors Retry-After on 429/503. Never retries 403/407: on this network that
    is a policy denial, not a transient error.
    """
    if params:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    host = urllib.parse.urlsplit(url).netloc
    hdrs = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
    hdrs.update(headers or {})
    delay = 5.0
    for attempt in range(retries):
        wait = _last_hit.get(host, 0) + min_interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        req = urllib.request.Request(url, headers=hdrs, data=data)
        try:
            _last_hit[host] = time.monotonic()
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (403, 407):
                raise Blocked(f"{e.code} for {host}") from e
            if e.code == 404:
                raise
            if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                ra = e.headers.get("Retry-After")
                pause = float(ra) + 1 if ra and ra.isdigit() else delay
                log(f"  HTTP {e.code} from {host}, sleeping {pause:.0f}s")
                time.sleep(pause)
                delay = min(delay * 2, 120)
                continue
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if attempt < retries - 1:
                log(f"  {type(e).__name__} from {host}: {e}; sleeping {delay:.0f}s")
                time.sleep(delay)
                delay = min(delay * 2, 120)
                continue
            raise


def http_json(url, **kw):
    return json.loads(http_get(url, **kw))


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)
    return path


def write_json(path, obj, indent=1):
    ensure_dir(os.path.dirname(path))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=indent, sort_keys=False)
        f.write("\n")


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_ndjson(path, rows):
    ensure_dir(os.path.dirname(path))
    n = 0
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")))
            f.write("\n")
            n += 1
    return n


def read_ndjson(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def in_bbox(lat, lon, bbox):
    w, s, e, n = bbox
    return s <= lat <= n and w <= lon <= e


def haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


_TAG_RE = re.compile(r"<[^>]+>")


def strip_html(s):
    """Reduce an HTML fragment to plain text with paragraph breaks."""
    if not s:
        return ""
    import html as _html
    s = re.sub(r"(?i)<\s*br\s*/?>", "\n", s)
    s = re.sub(r"(?i)</\s*(p|div|li|h[1-6])\s*>", "\n", s)
    s = _TAG_RE.sub("", s)
    s = _html.unescape(s).replace("\xa0", " ")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r" ?\n ?", "\n", s)
    return re.sub(r"\n{2,}", "\n\n", s).strip()


def fold(s):
    """Lower-case, fold umlauts and diacritics, drop punctuation."""
    s = (s or "").lower()
    s = s.replace("ß", "ss").replace("ä", "a").replace("ö", "o").replace("ü", "u")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("ae", "a").replace("oe", "o").replace("ue", "u")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def args_city():
    key = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "zurich"
    return city(key)
