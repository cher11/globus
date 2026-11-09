#!/usr/bin/env python3
"""Merge all raw sources into data/<city>/places.ndjson (+ merge_report.json).

Pipeline
  1. adapters turn every raw record into a *candidate* (one source object) and apply
     the guide-worthiness rules (unnamed / infrastructure objects are dropped here);
  2. hard links: shared Wikidata QID, Zuerich Tourism `osm_id` -> OSM object,
     OSM `wikipedia` tag -> Wikidata sitelink;
  3. Wikivoyage listings without coordinates are placed through a unique exact name match;
  4. fuzzy links: normalised-name similarity + distance, cross-source only, conservative;
  5. clusters become places (field priority, categories, licences, district assignment).
Run: python3 build_places.py [city]
"""
import collections
import difflib
import hashlib
import json
import math
import os
import re
import urllib.parse

from common import (args_city, fold, haversine_m, in_bbox, log, read_json, read_ndjson,
                    strip_html, today, write_json, write_ndjson)

LANGS = ("de", "en", "fr", "it", "ru")
ODBL = "ODbL-1.0"
CC0 = "CC0-1.0"
CCBYSA = "CC-BY-SA-4.0"
SRC_PRIORITY = {"zuerich_tourism": 0, "wikivoyage": 1, "osm": 2, "wikidata": 3, "stadt_zuerich": 4}
TOURISM_RESTRICTED = "restricted-zurich-tourism"  # editorial use promoting Zurich tourism only

# ---------------------------------------------------------------- taxonomy
# group -> allowed sub categories (documented in the README; subs are advisory)
TAXONOMY = {
    "sights": ["attraction", "landmark", "church", "historic", "architecture", "monument", "public_art",
               "fountain", "square", "bridge", "tower", "cemetery"],
    "museums_art": ["museum", "gallery"],
    "nature_views": ["park", "garden", "viewpoint", "peak", "lake_river", "nature_reserve", "trail"],
    "sports_active": ["swim", "fitness", "tennis", "football_pitch", "skate", "climbing", "ice", "golf",
                      "bike_hike", "winter", "pitch", "hall", "other"],
    "sports_watch": ["stadium", "arena"],
    "food": ["restaurant", "fast_food", "food_court", "farm_shop", "unclassified"],
    "cafes": ["cafe", "bakery", "ice_cream", "tea_room"],
    "bars_nightlife": ["bar", "pub", "club", "live_music", "winery", "brewery", "casino"],
    "shopping": ["fashion", "mall", "market", "books", "food_specialty", "souvenir", "other"],
    "culture_events": ["theatre", "cinema", "concert_venue", "event_venue", "festival", "arts_centre",
                       "library", "community_centre", "activity"],
    "family": ["zoo", "playground", "entertainment"],
    "wellness": ["spa", "sauna", "bath"],
    "lodging": ["hotel", "hostel", "camp", "apartment"],
    "practical": ["transport", "grocery", "education", "info", "other"],
}
RELATED = [{"food", "cafes", "bars_nightlife", "shopping"},
           {"sights", "museums_art", "culture_events", "nature_views", "family"},
           {"sports_active", "sports_watch", "wellness", "nature_views", "family"},
           {"lodging", "food", "bars_nightlife"},
           {"practical", "shopping", "sights", "culture_events"}]


def compatible(g1, g2):
    return g1 == g2 or any(g1 in r and g2 in r for r in RELATED)


def kw_category(text):
    """Keyword fallback on free text (lower-case)."""
    t = text.lower()
    rules = [
        (("museum", "museen"), ("museums_art", "museum")), (("galerie", "gallery", "kunsthaus"), ("museums_art", "gallery")),
        (("kirche", "church", "münster", "munster", "cathedral", "kapelle", "chapel"), ("sights", "church")),
        (("café", "cafe", "coffee", "kaffee", "tearoom", "tea room", "confiserie", "konditorei", "bakery", "bäckerei"), ("cafes", "cafe")),
        (("club", "disco", "nachtclub"), ("bars_nightlife", "club")), (("pub", "brauerei", "brewery"), ("bars_nightlife", "pub")),
        (("bar", "lounge", "cocktail"), ("bars_nightlife", "bar")),
        (("restaurant", "ristorante", "pizzeria", "bistro", "trattoria", "stübli", "wirtschaft", "gasthaus", "grill", "beiz"), ("food", "restaurant")),
        (("hotel", "hostel", "pension"), ("lodging", "hotel")), (("zoo",), ("family", "zoo")),
        (("stadion", "stadium"), ("sports_watch", "stadium")),
        (("bad ", "badi", "seebad", "schwimm", "swim", "pool", "lido"), ("sports_active", "swim")),
        (("sauna", "spa", "therme", "wellness"), ("wellness", "spa")),
        (("park", "garten", "garden", "wald", "forest"), ("nature_views", "park")),
        (("aussicht", "viewpoint", "view ", "berg", "hill", "uetliberg"), ("nature_views", "viewpoint")),
        (("theater", "theatre", "oper", "kino", "cinema"), ("culture_events", "theatre")),
        (("markt", "market"), ("shopping", "market")), (("shop", "laden", "store", "boutique"), ("shopping", "other")),
        (("brücke", "bridge"), ("sights", "bridge")), (("turm", "tower"), ("sights", "tower")),
        (("platz", "square"), ("sights", "square")),
    ]
    for keys, cat in rules:
        if any(k in t for k in keys):
            return cat
    return None


# ---------------------------------------------------------------- geometry: districts
def _ring_contains(ring, x, y):
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


class Districts:
    def __init__(self, raw):
        self.kreise = self._load(os.path.join(raw, "stadt_zuerich", "kreise.geojson"))
        self.quartiere = self._load(os.path.join(raw, "stadt_zuerich", "quartiere.geojson"))

    @staticmethod
    def _load(path):
        out = []
        for f in read_json(path)["features"]:
            g = f["geometry"]
            polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
            xs = [p[0] for poly in polys for p in poly[0]]
            ys = [p[1] for poly in polys for p in poly[0]]
            out.append((f["properties"], polys, (min(xs), min(ys), max(xs), max(ys))))
        return out

    @staticmethod
    def _find(items, lat, lon):
        for props, polys, (x0, y0, x1, y1) in items:
            if not (x0 <= lon <= x1 and y0 <= lat <= y1):
                continue
            for poly in polys:
                if _ring_contains(poly[0], lon, lat) and not any(_ring_contains(h, lon, lat) for h in poly[1:]):
                    return props
        return None

    def lookup(self, lat, lon):
        q = self._find(self.quartiere, lat, lon)
        k = self._find(self.kreise, lat, lon)
        if not q and not k:
            return None
        d = {}
        if k:
            d["kreis"] = int(k["name"])
        elif q:
            d["kreis"] = int(q["knr"])
        if q:
            d["quartier"] = q["qname"]
        return d


# ---------------------------------------------------------------- candidate helpers
def new_cand(src, sid, url, lic, name, lat, lon, cats, tier, **kw):
    c = {"src": src, "sid": str(sid), "url": url, "license": lic, "name": name, "names": kw.pop("names", {}),
         "lat": lat, "lon": lon, "geom": kw.pop("geom", "point"), "cats": cats, "tier": tier,
         "orig": kw.pop("orig", []), "wd": kw.pop("wd", None), "desc": kw.pop("desc", []),
         "addr": kw.pop("addr", {}), "hours": kw.pop("hours", []), "website": kw.pop("website", None),
         "phone": kw.pop("phone", None), "email": kw.pop("email", None), "cuisine": kw.pop("cuisine", []),
         "price": kw.pop("price", None), "access": kw.pop("access", None), "images": kw.pop("images", []),
         "attrs": kw.pop("attrs", {}), "extra": kw.pop("extra", {}), "alts": kw.pop("alts", []),
         "meta": kw.pop("meta", {})}
    c["names"] = {k: v for k, v in c["names"].items() if v}
    c.update(kw)
    return c


def commons_image(filename, source):
    if not filename:
        return None
    fn = filename.split("/")[-1]
    fn = urllib.parse.unquote(fn).replace("_", " ")
    if fn.lower().startswith(("file:", "datei:")):
        fn = fn.split(":", 1)[1]
    q = urllib.parse.quote(fn.replace(" ", "_"))
    return {"url": f"https://commons.wikimedia.org/wiki/Special:FilePath/{q}",
            "page": f"https://commons.wikimedia.org/wiki/File:{q}", "license": "see Commons file page",
            "author": None, "source": source}


# ---------------------------------------------------------------- OSM adapter
OSM_SPORT_SUB = {"swimming": "swim", "tennis": "tennis", "soccer": "football_pitch", "fitness": "fitness",
                 "climbing": "climbing", "skateboard": "skate", "ice_skating": "ice", "ice_hockey": "ice",
                 "golf": "golf", "yoga": "fitness", "gymnastics": "fitness", "multi": "other",
                 "athletics": "other", "basketball": "pitch", "beachvolleyball": "pitch", "volleyball": "pitch",
                 "handball": "pitch", "table_tennis": "other", "running": "other", "shooting": "other",
                 "equestrian": "other", "scuba_diving": "swim", "cycling": "bike_hike", "boules": "pitch"}
OSM_SHOP_SUB = {"clothes": "fashion", "shoes": "fashion", "boutique": "fashion", "bag": "fashion",
                "fashion_accessories": "fashion", "jewelry": "fashion", "watches": "fashion", "mall": "mall",
                "department_store": "mall", "books": "books", "bakery": "food_specialty", "pastry": "food_specialty",
                "confectionery": "food_specialty", "chocolate": "food_specialty", "deli": "food_specialty",
                "cheese": "food_specialty", "wine": "food_specialty", "coffee": "food_specialty",
                "tea": "food_specialty", "butcher": "food_specialty", "alcohol": "food_specialty",
                "farm": "food_specialty", "gift": "souvenir", "souvenir": "souvenir", "marketplace": "market"}
OSM_TABLE = {
    ("tourism", "museum"): ("museums_art", "museum", "core"), ("tourism", "gallery"): ("museums_art", "gallery", "core"),
    ("tourism", "attraction"): ("sights", "attraction", "core"), ("tourism", "viewpoint"): ("nature_views", "viewpoint", "core"),
    ("tourism", "zoo"): ("family", "zoo", "core"), ("tourism", "aquarium"): ("family", "zoo", "core"),
    ("tourism", "theme_park"): ("family", "entertainment", "core"),
    ("tourism", "hotel"): ("lodging", "hotel", "core"), ("tourism", "hostel"): ("lodging", "hostel", "core"),
    ("tourism", "guest_house"): ("lodging", "hotel", "minor"), ("tourism", "motel"): ("lodging", "hotel", "minor"),
    ("tourism", "apartment"): ("lodging", "apartment", "minor"), ("tourism", "chalet"): ("lodging", "apartment", "minor"),
    ("tourism", "camp_site"): ("lodging", "camp", "core"), ("tourism", "artwork"): ("sights", "public_art", "minor"),
    ("amenity", "restaurant"): ("food", "restaurant", "core"), ("amenity", "fast_food"): ("food", "fast_food", "core"),
    ("amenity", "food_court"): ("food", "food_court", "core"), ("amenity", "cafe"): ("cafes", "cafe", "core"),
    ("amenity", "ice_cream"): ("cafes", "ice_cream", "core"), ("amenity", "bar"): ("bars_nightlife", "bar", "core"),
    ("amenity", "pub"): ("bars_nightlife", "pub", "core"), ("amenity", "biergarten"): ("bars_nightlife", "bar", "core"),
    ("amenity", "nightclub"): ("bars_nightlife", "club", "core"), ("amenity", "casino"): ("bars_nightlife", "casino", "core"),
    ("amenity", "theatre"): ("culture_events", "theatre", "core"), ("amenity", "cinema"): ("culture_events", "cinema", "core"),
    ("amenity", "arts_centre"): ("culture_events", "arts_centre", "core"),
    ("amenity", "events_venue"): ("culture_events", "event_venue", "core"),
    ("amenity", "concert_hall"): ("culture_events", "concert_venue", "core"),
    ("amenity", "library"): ("culture_events", "library", "minor"),
    ("amenity", "community_centre"): ("culture_events", "community_centre", "minor"),
    ("amenity", "public_bath"): ("wellness", "bath", "core"), ("amenity", "marketplace"): ("shopping", "market", "core"),
    ("amenity", "fountain"): ("sights", "fountain", "minor"), ("amenity", "university"): ("practical", "education", "minor"),
    ("amenity", "ferry_terminal"): ("practical", "transport", "minor"),
    ("leisure", "park"): ("nature_views", "park", "core"), ("leisure", "nature_reserve"): ("nature_views", "nature_reserve", "core"),
    ("leisure", "garden"): ("nature_views", "garden", "minor"), ("leisure", "playground"): ("family", "playground", "minor"),
    ("leisure", "swimming_area"): ("sports_active", "swim", "core"), ("leisure", "water_park"): ("family", "entertainment", "core"),
    ("leisure", "beach_resort"): ("sports_active", "swim", "core"), ("leisure", "stadium"): ("sports_watch", "stadium", "core"),
    ("leisure", "golf_course"): ("sports_active", "golf", "core"), ("leisure", "ice_rink"): ("sports_active", "ice", "core"),
    ("leisure", "sports_centre"): ("sports_active", "other", "core"), ("leisure", "sports_hall"): ("sports_active", "hall", "minor"),
    ("leisure", "fitness_centre"): ("sports_active", "fitness", "minor"), ("leisure", "pitch"): ("sports_active", "pitch", "minor"),
    ("leisure", "track"): ("sports_active", "other", "minor"), ("leisure", "miniature_golf"): ("family", "entertainment", "core"),
    ("leisure", "bowling_alley"): ("family", "entertainment", "core"), ("leisure", "escape_game"): ("family", "entertainment", "core"),
    ("leisure", "trampoline_park"): ("family", "entertainment", "core"), ("leisure", "amusement_arcade"): ("family", "entertainment", "minor"),
    ("leisure", "dance"): ("culture_events", "activity", "minor"), ("leisure", "marina"): ("nature_views", "lake_river", "minor"),
    ("leisure", "bandstand"): ("culture_events", "event_venue", "minor"),
    ("historic", "castle"): ("sights", "historic", "minor"), ("historic", "monument"): ("sights", "monument", "minor"),
    ("historic", "memorial"): ("sights", "monument", "minor"), ("historic", "ruins"): ("sights", "historic", "minor"),
    ("historic", "archaeological_site"): ("sights", "historic", "minor"), ("historic", "city_gate"): ("sights", "historic", "minor"),
    ("historic", "tower"): ("sights", "tower", "minor"), ("historic", "building"): ("sights", "architecture", "minor"),
    ("historic", "church"): ("sights", "church", "minor"), ("historic", "fountain"): ("sights", "fountain", "minor"),
    ("historic", "yes"): ("sights", "historic", "minor"), ("historic", "manor"): ("sights", "historic", "minor"),
    ("natural", "peak"): ("nature_views", "peak", "minor"), ("natural", "beach"): ("sports_active", "swim", "core"),
    ("natural", "water"): ("nature_views", "lake_river", "minor"),
    ("man_made", "tower"): ("sights", "tower", "minor"), ("man_made", "bridge"): ("sights", "bridge", "minor"),
    ("man_made", "observatory"): ("sights", "attraction", "minor"), ("man_made", "lighthouse"): ("sights", "tower", "minor"),
    ("railway", "station"): ("practical", "transport", "minor"),
}
OSM_KEY_ORDER = ("tourism", "amenity", "leisure", "historic", "natural", "man_made", "railway")
PRIVATE_ACCESS = {"private", "no", "customers", "permit", "delivery"}


def osm_classify(t):
    """-> (group, sub, tier, reason) or (None, None, None, reason_for_exclusion)."""
    if t.get("tourism") == "information" and t.get("information") != "office":
        return None, None, None, "tourism=information (boards, guideposts)"
    if t.get("tourism") == "information":
        return "practical", "info", "minor", ""
    for k in OSM_KEY_ORDER:
        v = t.get(k)
        if v is None:
            continue
        if k == "leisure" and v == "swimming_pool":
            if t.get("access") in ("yes", "public", "permissive") and t.get("name"):
                return "sports_active", "swim", "minor", ""
            return None, None, None, "leisure=swimming_pool (mostly private)"
        hit = OSM_TABLE.get((k, v))
        if hit:
            g, s, tier = hit
            if k in ("leisure", "tourism", "amenity") and t.get("access") in PRIVATE_ACCESS and tier == "minor":
                return None, None, None, "access restricted/private"
            if k == "leisure" and v in ("sports_centre", "pitch", "fitness_centre", "sports_hall", "track"):
                sp = (t.get("sport") or "").split(";")[0]
                s = OSM_SPORT_SUB.get(sp, s)
            if k == "railway" and v == "station" and "name" not in t:
                return None, None, None, "unnamed"
            return g, s, tier, ""
    if t.get("shop"):
        s = OSM_SHOP_SUB.get(t["shop"], "other")
        if t["shop"] in ("supermarket", "convenience"):
            return "practical", "grocery", "minor", ""
        tier = "core" if t["shop"] in ("mall", "department_store", "marketplace", "souvenir") else "minor"
        return "shopping", s, tier, ""
    if t.get("sport"):
        sp = t["sport"].split(";")[0]
        return "sports_active", OSM_SPORT_SUB.get(sp, "other"), "minor", ""
    return None, None, None, "no guide-relevant tag"


def adapt_osm(cfg, stats):
    out = []
    drops = stats.setdefault("osm_dropped", collections.Counter())
    for o in read_ndjson(os.path.join(cfg["raw"], "osm", "pois.ndjson")):
        t = o["tags"]
        g, s, tier, why = osm_classify(t)
        if not g:
            drops[why] += 1
            continue
        name = t.get("name")
        if not name and not (t.get("tourism") == "viewpoint" and False):
            drops["unnamed"] += 1
            continue
        ref = {"node": "n", "way": "w", "relation": "r"}[o["osm_type"]] + str(o["osm_id"])
        orig = [f"{k}={t[k]}" for k in ("tourism", "amenity", "leisure", "shop", "historic", "natural", "man_made", "railway", "sport")
                if k in t]
        names = {l: t.get(f"name:{l}") for l in LANGS}
        addr = {"street": t.get("addr:street"), "housenumber": t.get("addr:housenumber"),
                "postcode": t.get("addr:postcode"), "city": t.get("addr:city")}
        desc = []
        for l in ("de", "en"):
            if t.get(f"description:{l}"):
                desc.append({"lang": l, "kind": "short", "text": t[f"description:{l}"][:600], "source": "osm", "license": ODBL})
        if t.get("description") and not desc:
            desc.append({"lang": "und", "kind": "short", "text": t["description"][:600], "source": "osm", "license": ODBL})
        attrs = {k: t[k] for k in ("outdoor_seating", "indoor_seating", "takeaway", "delivery", "smoking", "internet_access",
                                   "dog", "fee", "stars", "brand", "operator", "branch", "start_date", "level", "reservation",
                                   "diet:vegetarian", "diet:vegan", "diet:halal", "diet:gluten_free", "capacity", "ele")
                 if k in t}
        img = commons_image(t.get("wikimedia_commons") if str(t.get("wikimedia_commons", "")).startswith(("File:", "Datei:")) else None, "osm")
        c = new_cand("osm", ref, f"https://www.openstreetmap.org/{o['osm_type']}/{o['osm_id']}", ODBL, name, o["lat"], o["lon"],
                     [(g, s)], tier, geom=o["geom"], names=names, orig=orig, wd=t.get("wikidata") if re.fullmatch(r"Q\d+", t.get("wikidata", "")) else None,
                     desc=desc, addr={k: v for k, v in addr.items() if v},
                     hours=[{"value": t["opening_hours"], "format": "osm", "source": "osm"}] if t.get("opening_hours") else [],
                     website=t.get("website") or t.get("contact:website"), phone=t.get("phone") or t.get("contact:phone"),
                     email=t.get("email") or t.get("contact:email"),
                     cuisine=[x.strip() for x in t.get("cuisine", "").replace(",", ";").split(";") if x.strip()],
                     access={"wheelchair": t["wheelchair"], "source": "osm"} if t.get("wheelchair") else None,
                     images=[img] if img else [], attrs=attrs, alts=[x.strip() for x in t.get("alt_name", "").split(";") if x.strip()],
                     meta={"last_edit": o.get("last_edit"), "wikipedia": t.get("wikipedia"), "chain": bool(t.get("brand"))})
        out.append(c)
    return out


# ---------------------------------------------------------------- Zuerich Tourism adapter
TOURISM_TYPE = {
    "Restaurant": ("food", "restaurant"), "FastFoodRestaurant": ("food", "fast_food"), "BarOrPub": ("bars_nightlife", "bar"),
    "NightClub": ("bars_nightlife", "club"), "CafeOrCoffeeShop": ("cafes", "cafe"), "Winery": ("bars_nightlife", "winery"),
    "Casino": ("bars_nightlife", "casino"), "Hotel": ("lodging", "hotel"), "LodgingBusiness": ("lodging", "hotel"),
    "Hostel": ("lodging", "hostel"), "BedAndBreakfast": ("lodging", "hotel"), "Campground": ("lodging", "camp"),
    "Store": ("shopping", "other"), "ClothingStore": ("shopping", "fashion"), "FurnitureStore": ("shopping", "other"),
    "DepartmentStore": ("shopping", "mall"), "ShoppingCenter": ("shopping", "mall"),
    "Museum": ("museums_art", "museum"), "ArtGallery": ("museums_art", "gallery"),
    "TouristAttraction": ("sights", "attraction"), "CivicStructure": ("sights", "landmark"), "Church": ("sights", "church"),
    "Landform": ("nature_views", "viewpoint"), "Park": ("nature_views", "park"), "Zoo": ("family", "zoo"),
    "PerformingArtsTheater": ("culture_events", "theatre"), "MusicVenue": ("culture_events", "concert_venue"),
    "EventVenue": ("culture_events", "event_venue"), "EntertainmentBusiness": ("family", "entertainment"),
    "SportsActivityLocation": ("sports_active", "other"), "PublicSwimmingPool": ("sports_active", "swim"),
    "ExerciseGym": ("sports_active", "fitness"), "DaySpa": ("wellness", "spa"),
    "TouristInformationCenter": ("practical", "info"), "Place": ("sights", "attraction"), "LocalBusiness": ("practical", "other"),
}
TOURISM_CAT_OVERRIDE = {
    "Vantage Points": ("nature_views", "viewpoint"), "Hikes": ("sports_active", "bike_hike"),
    "Mountain Biking": ("sports_active", "bike_hike"), "Tobogganing": ("sports_active", "winter"),
    "Swimming": ("sports_active", "swim"), "Climbing": ("sports_active", "climbing"), "Ice Skating": ("sports_active", "ice"),
    "Stadiums": ("sports_watch", "stadium"), "Clubs & Discos": ("bars_nightlife", "club"),
    "Music Bar / Live Music": ("bars_nightlife", "live_music"), "Live Music": ("bars_nightlife", "live_music"),
    "Cocktail Bar": ("bars_nightlife", "bar"), "Coffee Houses & Tea Rooms": ("cafes", "tea_room"),
    "Wellness": ("wellness", "spa"), "Sauna": ("wellness", "sauna"), "Parks & Gardens": ("nature_views", "park"),
    "Squares & Streets": ("sights", "square"), "Architecture": ("sights", "architecture"),
    "Galleries": ("museums_art", "gallery"), "Children": ("family", "entertainment"),
}
CUISINES = {"Italian", "Asian", "Swiss Specialties", "Fondue", "Vegetarian", "Vegan friendly", "Mediterranean", "International",
            "French", "American", "Fish", "BBQ", "Gourmet", "Pasta", "Zürcher Geschnetzeltes", "Mexican", "Indian", "Thai",
            "Japanese", "Chinese", "Greek", "Spanish", "Turkish", "Lebanese", "Burger", "Pizza", "Sushi", "Steak"}


def schema_hours(lst):
    out = []
    for h in lst or []:
        h = re.sub(r":00(?=[-\s,]|$)", "", h)
        h = re.sub(r"(\d\d:\d\d):00", r"\1", h)
        out.append(h)
    return "; ".join(out)


def adapt_tourism(cfg, stats):
    out = []
    for r in read_ndjson(os.path.join(cfg["raw"], "zuerich_tourism", "objects.ndjson")):
        a = r.get("address") or {}
        gc = r.get("geoCoordinates") or {}
        names = {l: (r["name"].get(l) or "").strip() for l in ("de", "en", "fr", "it")}
        name = names.get("de") or names.get("en")
        if not name or gc.get("latitude") is None:
            stats.setdefault("tourism_dropped", collections.Counter())["no name/coords"] += 1
            continue
        lic = r["license"] if r["license"].startswith("CC") else TOURISM_RESTRICTED
        cats = [TOURISM_TYPE.get(r["@type"], ("practical", "other"))]
        catnames = list(r["category"].keys())
        for cn in catnames:
            if cn in TOURISM_CAT_OVERRIDE and TOURISM_CAT_OVERRIDE[cn] not in cats:
                cats.append(TOURISM_CAT_OVERRIDE[cn])
        if r["@type"] == "LocalBusiness" or (r["@type"] == "Restaurant" and "Coffee" in catnames and "Restaurants" not in catnames):
            kc = kw_category(name)
            if kc and kc not in cats:
                cats.insert(0, kc)
        if len(cats) > 1 and cats[0] == ("practical", "other"):
            cats.pop(0)
        # sports sub-category from the more specific override (put it first)
        if cats[0] == ("sports_active", "other") and len(cats) > 1 and cats[1][0].startswith("sports"):
            cats.pop(0)
        desc = []
        for l in ("en", "de"):
            sd = (r.get("disambiguatingDescription") or {}).get(l)
            ld = strip_html((r.get("description") or {}).get(l) or "")
            if sd:
                desc.append({"lang": l, "kind": "short", "text": sd.strip()[:300], "source": "zuerich_tourism", "license": lic})
            if ld:
                desc.append({"lang": l, "kind": "long", "text": ld[:500], "source": "zuerich_tourism", "license": lic})
        imgs = []
        author = (r.get("copyrightHolder") or {}).get("en")
        for im in ([r.get("image")] if r.get("image") else []) + (r.get("photo") or []):
            if im and im.get("url") and all(im["url"] != x["url"] for x in imgs):
                cap = im.get("caption") or {}
                imgs.append({"url": im["url"], "license": lic, "author": author, "source": "zuerich_tourism", "caption": cap.get("en") or cap.get("de") or None})
            if len(imgs) >= 4:
                break
        price = strip_html((r.get("price") or {}).get("en") or (r.get("price") or {}).get("de") or "")
        attrs = {}
        if r.get("amenityFeature"):
            attrs["amenities"] = [x if isinstance(x, str) else json.dumps(x, ensure_ascii=False) for x in r["amenityFeature"]][:20]
        if r.get("zurichCard"):
            attrs["zurich_card"] = True
        tags_en = [c for c in catnames if c not in ("Restaurants",)]
        out.append(new_cand(
            "zuerich_tourism", r["identifier"], "https://www.zuerich.com/en/api/v2/data", lic, name,
            float(gc["latitude"]), float(gc["longitude"]), cats, "core", names=names, orig=catnames + [f"@type={r['@type']}"],
            desc=desc, addr={"street": a.get("streetAddress"), "postcode": a.get("postalCode"), "city": a.get("addressLocality")},
            hours=([{"value": schema_hours(r.get("openingHours")), "format": "schema_org", "source": "zuerich_tourism"}]
                   if r.get("openingHours") else []),
            website=a.get("url"), phone=a.get("telephone"), email=a.get("email"),
            cuisine=[c for c in catnames if c in CUISINES], price=price[:400] or None, images=imgs, attrs=attrs,
            meta={"osm_id": str(r["osm_id"]) if r.get("osm_id") else None, "modified": r.get("dateModified"), "tags": tags_en[:12]}))
    return out


# ---------------------------------------------------------------- Stadt Zuerich adapter
SZH_LAYER = {  # layer -> (group, sub, tier)
    "poi_sporthalle_view": ("sports_active", "hall", "minor"), "poi_fussballplatz_view": ("sports_active", "football_pitch", "minor"),
    "poi_schulschwimmanlage_view": None, "poi_beachvolleyball_view": ("sports_active", "pitch", "minor"),
    "poi_planschbecken_view": ("family", "playground", "minor"), "poi_zuerifit_view": ("sports_active", "fitness", "minor"),
    "poi_skateranlage_view": ("sports_active", "skate", "minor"), "poi_tennisplatz_view": ("sports_active", "tennis", "minor"),
    "poi_freibad_view": ("sports_active", "swim", "core"), "poi_hallenbad_view": ("sports_active", "swim", "core"),
    "poi_seebad_view": ("sports_active", "swim", "core"), "poi_flussbad_view": ("sports_active", "swim", "core"),
    "poi_eisbahn_view": ("sports_active", "ice", "core"), "poi_parkour_view": ("sports_active", "other", "minor"),
    "poi_bikepark_view": ("sports_active", "bike_hike", "minor"), "poi_slackline_view": ("sports_active", "other", "minor"),
    "poi_stadion_view": ("sports_watch", "stadium", "core"), "poi_sportgarderobe_view": None,
    "poi_aussichtspunkt_view": ("nature_views", "viewpoint", "minor"), "poi_vitaparcours_view": ("sports_active", "bike_hike", "minor"),
    "poi_mountainbike_view": ("sports_active", "bike_hike", "minor"), "poi_finnenbahn_view": ("sports_active", "bike_hike", "minor"),
    "poi_waldlauf_view": ("sports_active", "bike_hike", "minor"), "poi_schlittelweg_view": ("sports_active", "winter", "minor"),
    "poi_park_view": ("nature_views", "park", "minor"), "poi_spielplatz_view": ("family", "playground", "minor"),
    "poi_gemeinschaftszentrum_view": ("culture_events", "community_centre", "minor"),
    "poi_bauernhofladen_view": ("food", "farm_shop", "minor"), "poi_friedhof_view": ("sights", "cemetery", "minor"),
}
GASTRO_KEEP = {"Gastwirtschaft": ("food", "unclassified"), "Kleinwirtschaft": ("food", "unclassified"),
               "Take Away": ("food", "fast_food"), "Dancing / Disco": ("bars_nightlife", "club"),
               "Cabaret / Nachtclub": ("bars_nightlife", "club"), "Veranstaltungsraum": ("culture_events", "event_venue"),
               "Aussenliegende Saisonwirtschaft": ("food", "unclassified")}
DAYS = (("mo", "Mo"), ("di", "Tu"), ("mi", "We"), ("do", "Th"), ("fr", "Fr"), ("sa", "Sa"), ("so", "Su"))


def szh_hours(p):
    parts = []
    for k, lab in DAYS:
        v = p.get(f"oeffnungszeiten_gebaeude_{k}") or p.get(f"oeffnungszeiten_schalter_{k}")
        if v and v.strip().lower() not in ("n.a.", "closed", "null", "") and re.search(r"\d", v):
            parts.append(f"{lab} {v.strip()}")
    return "; ".join(parts)


def adapt_szh(cfg, stats):
    out = []
    drops = stats.setdefault("szh_dropped", collections.Counter())
    meta = read_json(os.path.join(cfg["raw"], "stadt_zuerich", "meta.json"))["datasets"]
    ds_url = {k.replace("Liste_der_Informationstafeln_an_Denkmalschutz_Objekten", "Liste_der_Informationstafeln_an_Denkmalschutz_Obje"): v.get("url")
              for k, v in meta.items()}
    for f in read_ndjson(os.path.join(cfg["raw"], "stadt_zuerich", "features.ndjson")):
        ds, layer, p = f["dataset"], f["layer"], f["props"]
        if ds in ("Stadtkreise", "Statistische_Quartiere", "Brunnen", "Zueri_WC", "Standorte_ZueriVelo", "Hundezonen"):
            drops[f"{ds} (infrastructure, not a guide place)"] += 1
            continue
        if layer.endswith("_l"):
            drops["line layers (trail geometry)"] += 1
            continue
        url = ds_url.get(ds[:50]) or ds_url.get(ds)
        lic = CC0
        sid = p.get("poi_id") or f["fid"]
        kw = dict(geom="area" if "Polygon" in f["geom"] else "point")
        if ds == "Gastwirtschaftsbetriebe":
            art = p.get("betriebsart")
            if art not in GASTRO_KEEP or p.get("betriebsstatus") not in (None, "Offen"):
                drops[f"Gastwirtschaft type {art} (canteens, kiosks, shops...)"] += 1
                continue
            name = (p.get("betriebsname") or "").strip()
            if not name:
                drops["unnamed"] += 1
                continue
            g, s = GASTRO_KEEP[art]
            kc = kw_category(name) if art in ("Gastwirtschaft", "Kleinwirtschaft") else None
            cats = [kc if kc and kc[0] in ("food", "cafes", "bars_nightlife") else (g, s)]
            addr = {"street": p.get("strasselang"), "housenumber": str(p.get("hnr") or "") or None,
                    "postcode": str(p.get("plz") or "") or None, "city": p.get("ort")}
            c = new_cand("stadt_zuerich", f"gastro:{p.get('objectid')}", url, lic, name, f["lat"], f["lon"], cats, "minor",
                         orig=[f"Gastwirtschaftsbetriebe/{art}"], addr={k: v for k, v in addr.items() if v},
                         hours=[{"value": p["oeffnungszeit"].strip(), "format": "text_closing_hours_de", "source": "stadt_zuerich"}] if p.get("oeffnungszeit") else [],
                         meta={"kreis": p.get("kreissort"), "quartier": p.get("quarlang"), "quartier_nr": p.get("quarsort")})
            out.append(c)
            continue
        if ds == "Kunst_im_Stadtraum":
            name = (p.get("titel") or "").strip("[] ").strip()
            if not name or name.lower() in ("ohne titel", "undatiert"):
                name = (p.get("titel") or "").strip() or None
            if not name:
                drops["unnamed"] += 1
                continue
            d = "; ".join(x for x in (p.get("autoren"), p.get("datierung"), p.get("material")) if x and x != "undatiert")
            out.append(new_cand("stadt_zuerich", f"kunst:{p.get('inventarnummer') or p.get('objectid')}", url, lic, name, f["lat"], f["lon"],
                                [("sights", "public_art")], "minor", orig=["Kunst_im_Stadtraum"],
                                desc=[{"lang": "de", "kind": "short", "text": d[:300], "source": "stadt_zuerich", "license": lic}] if d else [],
                                addr={"street": p.get("standort")} if p.get("standort") else {}))
            continue
        if ds == "Stadtspaziergaenge_Zueri_zFuess":
            if layer != "view_zzf_fuss_poi":
                drops["walking tour routes (lines/tours)"] += 1
                continue
            name = p.get("poi_name")
            if not name:
                drops["unnamed"] += 1
                continue
            desc = []
            for l, k in (("de", "poi_description"), ("en", "poi_description_en")):
                if p.get(k):
                    desc.append({"lang": l, "kind": "long", "text": strip_html(p[k])[:900], "source": "stadt_zuerich", "license": lic})
            out.append(new_cand("stadt_zuerich", f"zzf:{p.get('poi_id')}", url, lic, name.strip(), f["lat"], f["lon"], [("sights", "historic")], "minor",
                                names={"en": p.get("poi_name_en")}, orig=["Stadtspaziergaenge Zueri z'Fuess"], desc=desc,
                                meta={"tour_id": p.get("tour_id")}))
            continue
        if ds.startswith("Liste_der_Informationstafeln"):
            name = (p.get("titel_tafel") or "").strip()
            if not name:
                drops["unnamed"] += 1
                continue
            out.append(new_cand("stadt_zuerich", f"blau:{p.get('bt_id')}", url, lic, name, f["lat"], f["lon"], [("sights", "architecture")], "minor",
                                orig=["Informationstafeln Denkmalschutz (Blaue Tafeln)"],
                                desc=[{"lang": "de", "kind": "long", "text": strip_html(p.get("text_tafel") or "")[:900], "source": "stadt_zuerich", "license": lic}]
                                if p.get("text_tafel") else [],
                                addr={"street": p.get("av_adresse")} if p.get("av_adresse") else {}, extra={"plaque_year": p.get("ds_tafel")}))
            continue
        if ds == "Auszeichnung_fuer_gute_Bauten":
            name = (p.get("objektname") or "").strip()
            if not name or p.get("abbruch") == "ja":
                drops["unnamed/demolished"] += 1
                continue
            out.append(new_cand("stadt_zuerich", f"agb:{p.get('poi_id') or p.get('objektnummer')}", url, lic, name, f["lat"], f["lon"],
                                [("sights", "architecture")], "minor", geom="area", orig=["Auszeichnung fuer gute Bauten"],
                                addr={"street": p.get("adresse")} if p.get("adresse") else {},
                                extra={"architect": p.get("architektur"), "period": p.get("zeitperiode")}))
            continue
        spec = SZH_LAYER.get(layer)
        if spec is None:
            drops[f"layer {layer} (not guide-relevant)"] += 1
            continue
        name = (p.get("name") or "").strip()
        if not name:
            drops["unnamed"] += 1
            continue
        g, s, tier = spec
        hours = szh_hours(p)
        addr = {"street": p.get("strasse") or p.get("adresse"), "housenumber": p.get("hausnummer"), "postcode": p.get("plz"), "city": p.get("ort")}
        desc = []
        for k in ("besonderes",):
            if p.get(k):
                desc.append({"lang": "de", "kind": "short", "text": strip_html(str(p[k]))[:500], "source": "stadt_zuerich", "license": lic})
                break
        attrs = {}
        if p.get("hindernisfreiheit"):
            attrs["accessibility_code_szh"] = p["hindernisfreiheit"][:80]
        if p.get("eignung"):
            attrs["age_range"] = p["eignung"]
        out.append(new_cand("stadt_zuerich", sid, url, lic, name, f["lat"], f["lon"], [(g, s)], tier, orig=[f"{ds}/{p.get('kategorie') or layer}"],
                            addr={k: v for k, v in addr.items() if v}, desc=desc,
                            hours=[{"value": hours, "format": "text", "source": "stadt_zuerich"}] if hours else [],
                            website=p.get("www"), phone=p.get("tel"), email=p.get("mail"), attrs=attrs, **kw))
    return out


# ---------------------------------------------------------------- Wikivoyage adapter
WV_KIND = {"see": ("sights", "attraction"), "do": None, "eat": ("food", "restaurant"), "drink": ("bars_nightlife", "bar"),
           "buy": ("shopping", "other"), "sleep": ("lodging", "hotel"), "go": ("practical", "transport"), "listing": None,
           "hotel": ("lodging", "hotel"), "restaurant": ("food", "restaurant"), "restaurant, brewery": ("bars_nightlife", "brewery"),
           "museum": ("museums_art", "museum"), "park": ("nature_views", "park"), "church": ("sights", "church"),
           "memorial": ("sights", "monument"), "festival": ("culture_events", "festival"), "discotheque": ("bars_nightlife", "club"),
           "zoo": ("family", "zoo"), "viewpoint": ("nature_views", "viewpoint"), "shop": ("shopping", "other"),
           "bar": ("bars_nightlife", "bar"), "market": ("shopping", "market"), "flea market": ("shopping", "market"),
           "campsite": ("lodging", "camp"), "do": None}
WV_SKIP = {"taxi", "tram", "bus", "train", "school", "consulate", "university", "airport"}


def adapt_wikivoyage(cfg, stats):
    out, drops = [], stats.setdefault("wv_dropped", collections.Counter())
    for i, l in enumerate(read_ndjson(os.path.join(cfg["raw"], "wikivoyage", "listings.ndjson"))):
        kind = l["kind"]
        if kind in WV_SKIP or kind == "marker":
            drops[f"kind {kind}"] += 1
            continue
        if not l.get("name"):
            drops["unnamed"] += 1
            continue
        cat = WV_KIND.get(kind) or kw_category(l["name"] + " " + l.get("content", "")) or (
            ("culture_events", "activity") if kind == "do" else ("practical", "other"))
        lang = l["lang"]
        lic = CCBYSA
        desc = [{"lang": lang, "kind": "long", "text": l["content"][:1200], "source": "wikivoyage", "license": lic}] if l.get("content") else []
        addr = {"street": l["address"]} if l.get("address") else {}
        c = new_cand("wikivoyage", f"{lang}:{l['article']}:{i}", l["article_url"], lic, l["name"], l.get("lat"), l.get("lon"), [cat], "core",
                     names={lang: l["name"]}, orig=[f"{lang}.wikivoyage:{kind}", f"section={l.get('section')}"], wd=l.get("wikidata"), desc=desc,
                     addr=addr, hours=[{"value": l["hours"], "format": "text", "source": "wikivoyage"}] if l.get("hours") else [],
                     website=l.get("url"), phone=l.get("phone"), email=l.get("email"), price=l.get("price"),
                     images=[commons_image(l["image"], "wikivoyage")] if l.get("image") else [],
                     alts=[l["alt"]] if l.get("alt") else [], meta={"lang": lang, "directions": l.get("directions"), "section": l.get("section"),
                                                                  "lastedit": l.get("lastedit")})
        out.append(c)
    return out


# ---------------------------------------------------------------- Wikidata adapter
WD_TYPES = [  # (substring of lower-case English type label, group, sub); first match wins
    ("art museum", "museums_art", "museum"), ("museum", "museums_art", "museum"), ("art gallery", "museums_art", "gallery"),
    ("gallery", "museums_art", "gallery"), ("cathedral", "sights", "church"), ("church", "sights", "church"), ("chapel", "sights", "church"),
    ("minster", "sights", "church"), ("abbey", "sights", "church"), ("monastery", "sights", "church"), ("synagogue", "sights", "church"),
    ("mosque", "sights", "church"), ("castle", "sights", "historic"), ("palace", "sights", "historic"), ("fortress", "sights", "historic"),
    ("city gate", "sights", "historic"), ("guild house", "sights", "historic"), ("town hall", "sights", "historic"),
    ("tower", "sights", "tower"), ("bridge", "sights", "bridge"), ("monument", "sights", "monument"), ("memorial", "sights", "monument"),
    ("statue", "sights", "monument"), ("sculpture", "sights", "public_art"), ("fountain", "sights", "fountain"),
    ("public square", "sights", "square"), ("square", "sights", "square"), ("plaza", "sights", "square"),
    ("cemetery", "sights", "cemetery"), ("tourist attraction", "sights", "attraction"), ("botanical garden", "nature_views", "garden"),
    ("zoo", "family", "zoo"), ("aquarium", "family", "zoo"), ("amusement park", "family", "entertainment"),
    ("park", "nature_views", "park"), ("garden", "nature_views", "garden"), ("nature reserve", "nature_views", "nature_reserve"),
    ("forest", "nature_views", "nature_reserve"), ("viewpoint", "nature_views", "viewpoint"), ("lookout", "nature_views", "viewpoint"),
    ("mountain", "nature_views", "peak"), ("hill", "nature_views", "peak"), ("peak", "nature_views", "peak"),
    ("lake", "nature_views", "lake_river"), ("river", "nature_views", "lake_river"), ("island", "nature_views", "lake_river"),
    ("beach", "sports_active", "swim"), ("lido", "sports_active", "swim"), ("swimming pool", "sports_active", "swim"),
    ("bathing", "sports_active", "swim"), ("stadium", "sports_watch", "stadium"), ("arena", "sports_watch", "arena"),
    ("ice rink", "sports_active", "ice"), ("concert hall", "culture_events", "concert_venue"), ("opera house", "culture_events", "theatre"),
    ("theatre", "culture_events", "theatre"), ("theater", "culture_events", "theatre"), ("cinema", "culture_events", "cinema"),
    ("library", "culture_events", "library"), ("arts centre", "culture_events", "arts_centre"), ("festival", "culture_events", "festival"),
    ("nightclub", "bars_nightlife", "club"), ("brewery", "bars_nightlife", "brewery"), ("winery", "bars_nightlife", "winery"),
    ("bar", "bars_nightlife", "bar"), ("pub", "bars_nightlife", "pub"), ("restaurant", "food", "restaurant"),
    ("cafe", "cafes", "cafe"), ("coffeehouse", "cafes", "cafe"), ("hotel", "lodging", "hotel"),
    ("shopping centre", "shopping", "mall"), ("shopping mall", "shopping", "mall"), ("department store", "shopping", "mall"),
    ("market", "shopping", "market"), ("train station", "practical", "transport"), ("railway station", "practical", "transport"),
    ("university", "practical", "education"),
]
WD_HERITAGE_PROPS = ("P1435", "P381")


def adapt_wikidata(cfg, stats):
    out, drops = [], stats.setdefault("wd_dropped", collections.Counter())
    for it in read_ndjson(os.path.join(cfg["raw"], "wikidata", "items.ndjson")):
        labels = it["labels"]
        name = labels.get("de") or labels.get("en")
        if not name or re.fullmatch(r"Q\d+", name):
            drops["no label"] += 1
            continue
        tl = " | ".join((t["label"] or "").lower() for t in it["types"])
        cat = None
        for sub, g, s in WD_TYPES:
            if re.search(r"(^|[ |-])" + re.escape(sub) + r"(s|es)?($|[ |-])", tl):
                cat = (g, s)
                break
        heritage = any(p in it["claims"] for p in WD_HERITAGE_PROPS)
        sl = it["sitelinks"]
        if cat is None and heritage and sl >= 2:
            cat = ("sights", "architecture")
        if cat is None:
            drops["type not guide-relevant"] += 1
            continue
        if sl < 1 and not heritage:
            drops["no sitelinks"] += 1
            continue
        tier = "core" if sl >= 3 else "minor"
        cl = it["claims"]

        def first(p):
            return cl[p][0]["value"] if cl.get(p) else None
        extra = {k: v for k, v in {
            "inception": (first("P571") or "")[:10] or None, "opened": (first("P1619") or "")[:10] or None,
            "architects": [x.get("label") or x["value"] for x in cl.get("P84", [])] or None,
            "heritage": [x.get("label") or x["value"] for x in cl.get("P1435", [])] or None,
            "style": [x.get("label") or x["value"] for x in cl.get("P149", [])] or None,
            "elevation_m": first("P2044"), "height_m": first("P2048"), "capacity": first("P1083"),
            "visitors_per_year": first("P1174"), "commons_category": first("P373"),
            "demolished": (first("P576") or "")[:10] or None}.items() if v}
        imgs = []
        for x in cl.get("P18", [])[:2]:
            im = commons_image(x["value"], "wikidata")
            if im:
                imgs.append(im)
        desc = [{"lang": l, "kind": "short", "text": it["descriptions"][l], "source": "wikidata", "license": CC0}
                for l in ("de", "en", "ru") if it["descriptions"].get(l)]
        alts = (it["aliases"].get("de") or []) + (it["aliases"].get("en") or [])
        addr = {"street": first("P6375")} if first("P6375") else {}
        out.append(new_cand("wikidata", it["qid"], f"https://www.wikidata.org/wiki/{it['qid']}", CC0, name, it["lat"], it["lon"], [cat], tier,
                            names=labels, orig=[t["label"] or t["qid"] for t in it["types"]][:6], wd=it["qid"], desc=desc, alts=alts, images=imgs,
                            website=first("P856"), phone=first("P1329"), addr=addr, extra=extra,
                            meta={"sitelinks": sl, "wiki": it["wiki"], "heritage": heritage}))
    return out


# ---------------------------------------------------------------- matching
STOP = {"restaurant", "cafe", "bar", "bistro", "ristorante", "zurich", "zuerich", "hotel", "the", "der", "die", "das", "und", "and",
        "am", "im", "in", "de", "la", "le", "zum", "zur", "beim", "ag", "gmbh", "kirche", "church"}
# extra tokens that may differ between two names of the same place ("Hotel Ibis" / "Ibis")
EXTRA_OK = STOP | {"museum", "theater", "theatre", "garten", "club", "lounge", "boutique", "spa", "pub", "shop", "store",
                   "grill", "kitchen", "pizzeria", "trattoria", "haus", "stadion", "park", "platz", "see", "seebad", "bad",
                   "brasserie", "tea", "room", "house", "hostel", "airport", "flughafen", "city", "west", "ost", "nord"}


def name_forms(c):
    forms = set()
    for n in [c["name"]] + list(c["names"].values()) + c["alts"]:
        f = fold(n)
        if f:
            forms.add(f)
    return forms


def toks(f):
    t = [x for x in f.split() if x not in STOP or x in ("kirche",)]
    return t


def name_sim(fa, fb):
    """Best similarity of two sets of folded names, 0..1."""
    best = 0.0
    for a in fa:
        for b in fb:
            if a == b:
                return 1.0
            ta, tb = toks(a), toks(b)
            if not ta or not tb:
                continue
            da, db = {x for x in ta if x.isdigit()}, {x for x in tb if x.isdigit()}
            if da != db:
                continue
            sa, sb = set(ta), set(tb)
            if ta == tb:
                best = max(best, 0.98)
                continue
            short, long_ = (sa, sb) if len(sa) <= len(sb) else (sb, sa)
            if short <= long_ and (len(short) >= 2 or sum(len(x) for x in short) >= 8) and (long_ - short) <= EXTRA_OK:
                best = max(best, 0.92)
                continue
            if abs(len(a) - len(b)) <= max(4, len(a) // 3):
                r = difflib.SequenceMatcher(None, a, b).ratio()
                if r >= 0.85:
                    best = max(best, r * 0.97)
    return best


class UF:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        a, b = self.find(a), self.find(b)
        if a != b:
            self.p[b] = a
        return a


def fuzzy_ok(sim, dist, a, b, name_count):
    big = a["geom"] != "point" or b["geom"] != "point" or "wikidata" in (a["src"], b["src"])
    thr = 300 if big else 120
    chain = a["meta"].get("chain") or b["meta"].get("chain") or name_count.get(fold(a["name"]), 0) >= 3
    if chain:
        thr = 40
    if sim >= 0.9 and dist <= thr:
        return True
    return sim >= 0.8 and dist <= 40


def cluster(cands, report):
    n = len(cands)
    uf = UF(n)
    srcs = [{c["src"]} for c in cands]  # cluster root -> sources (kept on root index)
    qids = [({c["wd"]} if c["wd"] else set()) for c in cands]
    forms = [name_forms(c) for c in cands]
    hard = report.setdefault("hard_links", collections.Counter())
    hard_pairs = []

    def merge(i, j, kind=None, force=False):
        ri, rj = uf.find(i), uf.find(j)
        if ri == rj:
            return False
        if qids[ri] and qids[rj] and qids[ri] != qids[rj] and not force:
            return False
        if not force and (srcs[ri] & srcs[rj]):
            return False
        r = uf.union(ri, rj)
        o = rj if r == ri else ri
        srcs[r] = srcs[ri] | srcs[rj]
        qids[r] = qids[ri] | qids[rj]
        return True

    # (a) shared Wikidata QID: same real-world entity even within one source
    byq = collections.defaultdict(list)
    for i, c in enumerate(cands):
        if c["wd"]:
            byq[c["wd"]].append(i)
    for q, idx in byq.items():
        for j in idx[1:]:
            if merge(idx[0], j, force=True):
                hard["shared QID"] += 1
                hard_pairs.append((idx[0], j))
    # (b) OSM wikipedia tag -> Wikidata sitelink
    wiki_title = {}
    for i, c in enumerate(cands):
        if c["src"] == "wikidata":
            for site, t in c["meta"].get("wiki", {}).items():
                if site in ("enwiki", "dewiki"):
                    wiki_title[(site[:2], t)] = i
    for i, c in enumerate(cands):
        w = c["meta"].get("wikipedia") if c["src"] == "osm" else None
        if w and ":" in w and (w.split(":", 1)[0], w.split(":", 1)[1]) in wiki_title:
            j = wiki_title[(w.split(":", 1)[0], w.split(":", 1)[1])]
            if haversine_m(c["lat"], c["lon"], cands[j]["lat"], cands[j]["lon"]) < 1500 and merge(i, j):
                hard["OSM wikipedia tag"] += 1
                hard_pairs.append((i, j))
    # (c) Zuerich Tourism osm_id -> OSM object
    osm_by_num = collections.defaultdict(list)
    for i, c in enumerate(cands):
        if c["src"] == "osm":
            osm_by_num[c["sid"][1:]].append(i)
    for i, c in enumerate(cands):
        oid = c["meta"].get("osm_id") if c["src"] == "zuerich_tourism" else None
        if oid and oid in osm_by_num:
            best = min(osm_by_num[oid], key=lambda j: haversine_m(c["lat"], c["lon"], cands[j]["lat"], cands[j]["lon"]))
            d = haversine_m(c["lat"], c["lon"], cands[best]["lat"], cands[best]["lon"])
            if d <= 400:
                if merge(i, best):
                    hard["Tourism osm_id"] += 1
                    hard_pairs.append((i, best))
            else:
                hard["Tourism osm_id rejected (>400 m)"] += 1
        elif oid:
            hard["Tourism osm_id not found in OSM extract"] += 1

    # (d) Wikivoyage listings without coordinates: unique exact name match
    placed = unplaced = 0
    name_idx = collections.defaultdict(list)
    for i, c in enumerate(cands):
        if c["lat"] is not None and c["src"] != "wikivoyage":
            for f in forms[i]:
                name_idx[f].append(i)
    drop = set()
    for i, c in enumerate(cands):
        if c["src"] == "wikivoyage" and c["lat"] is None:
            roots = {uf.find(j) for f in forms[i] for j in name_idx.get(f, []) if compatible(c["cats"][0][0], cands[j]["cats"][0][0])}
            if len(roots) == 1 and merge(i, next(iter(roots))):
                j = next(iter(roots))
                c["lat"], c["lon"] = cands[j]["lat"], cands[j]["lon"]
                placed += 1
                hard["Wikivoyage unique exact name (no coords)"] += 1
            else:
                drop.add(i)
                unplaced += 1
    report["wikivoyage_no_coords"] = {"placed": placed, "dropped": unplaced}

    # (e) fuzzy: name + distance
    name_count = collections.Counter(fold(c["name"]) for c in cands if c["src"] == "osm")
    grid = collections.defaultdict(list)
    cell = 0.003
    for i, c in enumerate(cands):
        if i in drop or c["lat"] is None:
            continue
        grid[(int(c["lat"] / cell), int(c["lon"] / cell))].append(i)
    pairs = []
    for i, c in enumerate(cands):
        if i in drop or c["lat"] is None:
            continue
        gx, gy = int(c["lat"] / cell), int(c["lon"] / cell)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for j in grid.get((gx + dx, gy + dy), ()):
                    if j <= i:
                        continue
                    o = cands[j]
                    same = o["src"] == c["src"]
                    if same and not (forms[i] & forms[j]):
                        continue
                    if not same and not compatible(c["cats"][0][0], o["cats"][0][0]) and "wikidata" not in (c["src"], o["src"]) \
                            and not (forms[i] & forms[j]):
                        continue
                    d = haversine_m(c["lat"], c["lon"], o["lat"], o["lon"])
                    if d > 600:
                        continue
                    s = name_sim(forms[i], forms[j])
                    if s >= 0.8:
                        pairs.append((s, d, i, j))
    pairs.sort(key=lambda x: (-x[0], x[1]))
    fz = report.setdefault("fuzzy", {"accepted": 0, "by_pair": collections.Counter(), "blocked_qid_conflict": 0,
                                     "blocked_source_overlap": 0, "rejected_by_threshold": 0, "samples": []})
    fuzzy_pairs = []
    exact_pairs = [p for p in pairs if p[0] >= 1.0]
    for s, d, i, j in pairs:
        if cands[i]["src"] == cands[j]["src"]:
            continue
        if not fuzzy_ok(s, d, cands[i], cands[j], name_count):
            fz["rejected_by_threshold"] += 1
            continue
        ri, rj = uf.find(i), uf.find(j)
        if ri == rj:
            continue
        if qids[ri] and qids[rj] and qids[ri] != qids[rj]:
            fz["blocked_qid_conflict"] += 1
            continue
        if srcs[ri] & srcs[rj]:
            fz["blocked_source_overlap"] += 1
            continue
        merge(i, j)
        fz["accepted"] += 1
        fz["by_pair"]["+".join(sorted((cands[i]["src"], cands[j]["src"])))] += 1
        fuzzy_pairs.append((i, j, s, d))
    # (f) strong exact pass: identical folded name within 80 m merges even across source overlap or
    # different QIDs (Wikidata often has separate items for the museum, the building and the organisation)
    strong = report.setdefault("strong_exact", {"merged": 0, "qid_conflict_merged": 0, "same_source_merged": 0, "samples": []})
    strong_groups = [{"food", "cafes", "bars_nightlife"}, {"sports_active", "wellness"}, {"lodging", "wellness"},
                     {"sights", "museums_art", "culture_events"}]

    def gastro(c):
        return c["src"] == "stadt_zuerich" and c["cats"][0] == ("food", "unclassified")
    for s, d, i, j in sorted(exact_pairs, key=lambda x: x[1]):
        a, b = cands[i], cands[j]
        if d > (250 if "point" != a["geom"] or "point" != b["geom"] else 80) or uf.find(i) == uf.find(j):
            continue
        if a["meta"].get("chain") or b["meta"].get("chain") or name_count.get(fold(a["name"]), 0) >= 3:
            continue
        ga, gb = a["cats"][0][0], b["cats"][0][0]
        if not (ga == gb or any(ga in g and gb in g for g in strong_groups) or gastro(a) or gastro(b)):
            continue
        ri, rj = uf.find(i), uf.find(j)
        conflict = bool(qids[ri] and qids[rj] and qids[ri] != qids[rj])
        merge(i, j, force=True)
        strong["merged"] += 1
        strong["qid_conflict_merged"] += conflict
        strong["same_source_merged"] += a["src"] == b["src"]
        if len(strong["samples"]) < 25:
            strong["samples"].append(f"{a['src']}:{a['name']} | {b['src']}:{b['name']} d={d:.0f}m qidconflict={conflict}")
    # recall proxy: would the fuzzy rule have found the pairs that hard IDs linked?
    ok = tot = 0
    for i, j in hard_pairs:
        a, b = cands[i], cands[j]
        if a["lat"] is None or b["lat"] is None:
            continue
        d = haversine_m(a["lat"], a["lon"], b["lat"], b["lon"])
        tot += 1
        if fuzzy_ok(name_sim(forms[i], forms[j]), d, a, b, name_count):
            ok += 1
    report["fuzzy_recall_on_hard_links"] = {"hard_pairs": tot, "found_by_fuzzy_rule": ok, "share": round(ok / max(tot, 1), 3)}
    import random
    random.Random(1).shuffle(fuzzy_pairs)
    fz["samples"] = [f"{cands[i]['src']}:{cands[i]['name']} | {cands[j]['src']}:{cands[j]['name']} | sim={s:.2f} d={d:.0f}m"
                     for i, j, s, d in fuzzy_pairs[:25]]
    fz["sim_ge_0.98"] = sum(1 for p in fuzzy_pairs if p[2] >= 0.98)
    fz["sim_0.9_0.98"] = sum(1 for p in fuzzy_pairs if 0.9 <= p[2] < 0.98)
    fz["sim_0.8_0.9"] = sum(1 for p in fuzzy_pairs if p[2] < 0.9)
    groups = collections.defaultdict(list)
    for i in range(n):
        if i not in drop:
            groups[uf.find(i)].append(i)
    return list(groups.values()), hard_pairs, fuzzy_pairs


# ---------------------------------------------------------------- build places
def pick(cl, field, order=None):
    for c in sorted(cl, key=lambda c: SRC_PRIORITY[c["src"]] if order is None else order.index(c["src"])):
        if c.get(field):
            return c[field]
    return None


WP_EXTRACTS = {}


def load_wikipedia(cfg):
    for r in read_ndjson(os.path.join(cfg["raw"], "wikipedia", "extracts.ndjson")):
        WP_EXTRACTS.setdefault(r["qid"], []).append(r)


def make_place(cl, districts):
    cl = sorted(cl, key=lambda c: (SRC_PRIORITY[c["src"]], c["sid"]))
    # position: OSM > Tourism > Stadt ZH > Wikidata > Wikivoyage
    pos_order = ["osm", "zuerich_tourism", "stadt_zuerich", "wikidata", "wikivoyage"]
    pc = sorted([c for c in cl if c["lat"] is not None], key=lambda c: (pos_order.index(c["src"]), c["geom"] != "point"))[0]
    lat, lon = round(pc["lat"], 6), round(pc["lon"], 6)
    # names: local default = OSM name, Tourism de, Wikidata de label, others
    name_order = ["osm", "zuerich_tourism", "wikidata", "stadt_zuerich", "wikivoyage"]
    best_wd = max((c for c in cl if c["src"] == "wikidata"), key=lambda c: c["meta"].get("sitelinks", 0), default=None)
    if best_wd and best_wd["meta"].get("sitelinks", 0) >= 3:
        name_order = ["wikidata", "osm", "zuerich_tourism", "stadt_zuerich", "wikivoyage"]
    default = next(c["name"] for c in sorted(cl, key=lambda c: (name_order.index(c["src"]), c is not best_wd)) if c["name"])
    names = {}
    for l in LANGS:
        for c in sorted(cl, key=lambda c: name_order.index(c["src"])):
            v = c["names"].get(l)
            if v and (l not in names):
                names[l] = v
    names = {l: v for l, v in names.items() if fold(v) != fold(default) or l in ("de", "en")}
    names = {l: v for l, v in names.items() if v}
    # categories: priority order of sources, dedupe
    cats, seen = [], set()
    for c in cl:
        for g, s in c["cats"]:
            if (g, s) not in seen:
                seen.add((g, s))
                cats.append((g, s))
    # an unclassified (food/unclassified, practical/other, activity) primary yields to a better one
    weak = {("food", "unclassified"), ("practical", "other"), ("shopping", "other"), ("sights", "attraction"),
            ("sights", "landmark"), ("sports_active", "other"), ("culture_events", "activity"), ("culture_events", "event_venue")}
    cats.sort(key=lambda gs: gs in weak)
    tier = "core" if any(c["tier"] == "core" for c in cl) else "minor"
    # descriptions: all, deduped
    desc, dseen = [], set()
    for c in cl:
        for d in c["desc"]:
            k = (d["lang"], d["kind"], d["source"], d["text"][:60])
            if k not in dseen and d["text"]:
                dseen.add(k)
                desc.append({a: b for a, b in d.items() if a != "license"})
    # address
    addr = {}
    for c in sorted(cl, key=lambda c: ["osm", "zuerich_tourism", "stadt_zuerich", "wikivoyage", "wikidata"].index(c["src"])):
        for k, v in c["addr"].items():
            addr.setdefault(k, v)
    dist = districts.lookup(lat, lon)
    if not dist:
        for c in cl:
            if c["meta"].get("kreis"):
                dist = {"kreis": int(c["meta"]["kreis"])}
                break
    hours = [h for c in cl for h in c["hours"]]
    imgs, useen = [], set()
    for c in cl:
        for im in c["images"]:
            if im["url"] not in useen:
                useen.add(im["url"])
                imgs.append(im)
    wdc = next((c for c in cl if c["src"] == "wikidata"), None)
    wds = sorted({c["wd"] for c in cl if c["wd"]},
                 key=lambda q: -max((c["meta"].get("sitelinks", 0) for c in cl if c["src"] == "wikidata" and c["wd"] == q), default=0))
    qid = wds[0] if wds else None
    wdc = next((c for c in cl if c["src"] == "wikidata" and c["wd"] == qid), wdc)
    for r in WP_EXTRACTS.get(qid, []):
        first_para = r["extract"].split("\n\n")[0][:700]
        desc.append({"lang": r["lang"], "kind": "long", "text": first_para, "source": "wikipedia", "url": r["url"]})
    osmc = [c for c in cl if c["src"] == "osm"]
    # id
    if qid:
        pid = "wd:" + qid
    elif osmc:
        pid = "osm:" + osmc[0]["sid"]
    else:
        c0 = cl[0]
        if c0["src"] == "zuerich_tourism":
            pid = "zt:" + c0["sid"]
        elif c0["src"] == "wikivoyage":
            pid = "wv:" + hashlib.sha1((c0["sid"] + c0["name"]).encode()).hexdigest()[:10]
        else:
            pid = "szh:" + c0["sid"]
    sources = []
    for c in cl:
        s_ = {"source": c["src"], "id": c["sid"], "license": c["license"]}
        if c["src"] in ("osm", "wikidata", "wikivoyage"):
            s_["url"] = c["url"]
        if c["src"] == "osm":
            s_["last_edit"] = c["meta"].get("last_edit")
        elif c["src"] == "zuerich_tourism":
            s_["modified"] = c["meta"].get("modified")
        sources.append({k: v for k, v in s_.items() if v})
    for r in WP_EXTRACTS.get(qid, []):
        sources.append({"source": "wikipedia", "id": f"{r['lang']}:{r['title']}", "url": r["url"], "license": CCBYSA})
    cuisine = []
    for c in cl:
        for x in c["cuisine"]:
            if x.lower() not in [y.lower() for y in cuisine]:
                cuisine.append(x)
    attrs = {}
    for c in reversed(cl):
        attrs.update(c["attrs"])
    extra = {}
    for c in reversed(cl):
        extra.update(c["extra"])
    sl = wdc["meta"]["sitelinks"] if wdc else 0
    wv = [c for c in cl if c["src"] == "wikivoyage"]
    n_src = len({c["src"] for c in cl})
    tour = any(c["src"] == "zuerich_tourism" for c in cl)
    p = {
        "id": pid, "name": default, "names": names, "lat": lat, "lon": lon,
        "geom": "area" if pc["geom"] != "point" else None, "tier": tier,
        "group": cats[0][0], "categories": [f"{g}/{s}" for g, s in cats],
        "source_categories": None if tier == "minor" else {src: sorted({o for c in cl if c["src"] == src for o in c["orig"] if not o.startswith("section=")})
                              for src in dict.fromkeys(c["src"] for c in cl)},
        "descriptions": desc, "address": addr, "district": dist, "opening_hours": hours,
        "website": pick(cl, "website"), "phone": pick(cl, "phone"), "email": pick(cl, "email"),
        "cuisine": cuisine, "price": pick(cl, "price"), "accessibility": pick(cl, "access"),
        "images": imgs[:4], "attributes": attrs, "wikidata": qid, "wikidata_also": wds[1:], "wikipedia": (wdc["meta"]["wiki"] if wdc else {}) or None,
        "wikidata_facts": extra or None,
        "popularity": {k: v for k, v in {"wikidata_sitelinks": sl, "in_zuerich_tourism": tour or None,
                                         "in_wikivoyage": [c["meta"]["lang"] for c in wv], "n_sources": n_src,
                                         "score": sl + (6 if tour else 0) + 5 * len(wv) + 2 * (n_src - 1)}.items() if v},
        "mentions": [], "sources": sources,
        "licenses": sorted({s["license"] for s in sources}),
    }
    return {k: v for k, v in p.items() if v not in (None, "", [], {})} 


def main():
    cfg = args_city()
    raw = cfg["raw"]
    stats = {}
    adapters = [("osm", adapt_osm), ("zuerich_tourism", adapt_tourism), ("stadt_zuerich", adapt_szh),
                ("wikivoyage", adapt_wikivoyage), ("wikidata", adapt_wikidata)]
    cands = []
    per_src = {}
    for name, fn in adapters:
        got = fn(cfg, stats)
        per_src[name] = len(got)
        cands += got
        log(f"{name}: {len(got)} candidates")
    bbox = cfg["bbox"]
    before = len(cands)
    cands = [c for c in cands if c["lat"] is None or in_bbox(c["lat"], c["lon"], bbox)]
    report = {"built": today(), "candidates_by_source": per_src, "outside_bbox_dropped": before - len(cands),
              "dropped": {k: dict(v.most_common()) for k, v in stats.items()}}
    groups, hard_pairs, fuzzy_pairs = cluster(cands, report)
    districts = Districts(raw)
    load_wikipedia(cfg)
    ws_path = os.path.join(raw, "websearch", "websearch.json")
    ws = read_json(ws_path)["items"] if os.path.exists(ws_path) else []
    ws_idx = collections.defaultdict(list)
    for w in ws:
        for m in w["match_names"]:
            ws_idx[fold(m)].append(w)
    places = [make_place([cands[i] for i in g], districts) for g in groups]
    # unique ids
    seen = collections.Counter()
    for p in places:
        seen[p["id"]] += 1
        if seen[p["id"]] > 1:
            p["id"] += f"~{seen[p['id']]}"
    for p in places:
        for w in ws_idx.get(fold(p["name"]), []):
            if w["category"].split("/")[0] == p["group"] or p["group"] in ("sports_active", "sports_watch"):
                p["mentions"] = p.get("mentions", []) + [{"text": w["summary"], "source_url": w["source_url"], "date": w["date"]}]
    report["websearch_mentions_attached"] = sum(len(p.get("mentions", [])) for p in places)
    places.sort(key=lambda p: (p["tier"] != "core", -p["popularity"].get("score", 0), p["name"]))
    n = write_ndjson(os.path.join(cfg["dir"], "places.ndjson"), places)
    reg = {}
    for key, url, lic, attr, meta in (
            ("osm", "https://download.bbbike.org/osm/bbbike/Zuerich/Zuerich.osm.pbf", "ODbL-1.0", "© OpenStreetMap contributors", "osm/meta.json"),
            ("zuerich_tourism", "https://www.zuerich.com/en/api/v2/data", "per record: CC BY-SA / CC BY-NC-ND / CC BY-NC / restricted-zurich-tourism",
             "Zürich Tourismus, www.zuerich.com (copyrightHolder per record)", "zuerich_tourism/meta.json"),
            ("stadt_zuerich", "https://data.stadt-zuerich.ch/", "CC0-1.0 (all fetched datasets, CKAN license_id cc-zero)", "Stadt Zürich Open Data (attribution appreciated, not required)", "stadt_zuerich/meta.json"),
            ("wikidata", "https://query.wikidata.org/sparql", "CC0-1.0", "Wikidata (none required)", "wikidata/meta.json"),
            ("wikipedia", "https://en.wikipedia.org/ , https://de.wikipedia.org/", "CC-BY-SA-4.0", "Wikipedia contributors, link to the article and license", "wikipedia/meta.json"),
            ("wikivoyage", "https://en.wikivoyage.org/ , https://de.wikivoyage.org/", "CC-BY-SA-4.0", "Wikivoyage contributors, link to the article and license", "wikivoyage/meta.json")):
        try:
            fetched = read_json(os.path.join(raw, meta)).get("fetched")
        except Exception:
            fetched = None
        reg[key] = {"url": url, "license": lic, "attribution": attr, "fetched": fetched,
                    "places_with_source": sum(1 for p in places if any(x["source"] == key for x in p["sources"]))}
    write_json(os.path.join(cfg["dir"], "sources.json"), reg)
    report["places"] = n
    report["places_by_tier"] = dict(collections.Counter(p["tier"] for p in places))
    report["sources_per_place"] = dict(sorted(collections.Counter(p["popularity"]["n_sources"] for p in places).items()))
    report["candidates_total"] = len(cands)
    report["merged_away"] = len(cands) - n
    report["hard_links"] = dict(report["hard_links"])
    report["fuzzy"]["by_pair"] = dict(report["fuzzy"]["by_pair"])
    write_json(os.path.join(cfg["dir"], "merge_report.json"), report)
    log(f"wrote {n} places; merged away {len(cands) - n}")


if __name__ == "__main__":
    main()
