"""City-specific configuration for the collection scripts.

Everything that changes from one city to the next lives here. To add a city,
add another entry to CITIES; the scripts take the city key as their first
argument (default: "zurich").
"""
import os

GUIDE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DATA_ROOT = os.path.join(GUIDE_ROOT, "data")

USER_AGENT = "GlobusGuidePrototype/0.1 (one-off low-volume city guide data collection)"

CITIES = {
    "zurich": {
        "name": "Zürich",
        # west, south, east, north (WGS84). City of Zürich plus what a visitor
        # reaches on a day in town: Uetliberg/Felsenegg ridge, both lake shores
        # down to Kilchberg/Rüschlikon and Küsnacht, Glattzentrum, the airport.
        "bbox": [8.44, 47.30, 8.63, 47.465],
        "osm": {
            "pbf_url": "https://download.bbbike.org/osm/bbbike/Zuerich/Zuerich.osm.pbf",
            "pbf_name": "Zuerich.osm.pbf",
        },
        "zuerich_tourism": {
            "base": "https://www.zuerich.com",
            "tree_path": "/en/api/v2/data",
        },
        "wikidata": {
            "city_qid": "Q72",
        },
        "wikivoyage": {
            # Candidate articles; redirects and missing pages are skipped.
            "en": ["Zurich", "Zurich Airport", "Zurich Hauptbahnhof"],
            "de": ["Zürich", "Zürich/Flughafen", "Zürich Hauptbahnhof", "Zürichsee"],
        },
        "stadt_zuerich": {
            "wfs_base": "https://www.ogd.stadt-zuerich.ch/wfs/geoportal/",
            "ckan_base": "https://data.stadt-zuerich.ch/api/3/action/",
            # WFS dataset name -> CKAN package id (for licence / metadata lookup).
            # All layers of each dataset are fetched.
            "datasets": {
                "Stadtkreise": "geo_stadtkreise",
                "Statistische_Quartiere": "geo_statistische_quartiere",
                "Sport": "geo_sport",
                "Erholungs__und_Sporteinrichtungen": "geo_erholungs__und_sporteinrichtungen",
                "Brunnen": "geo_brunnen",
                "Park": "geo_park",
                "POI_oeffentliche_Spielplaetze": "geo_poi_oeffentliche_spielplaetze",
                "Zueri_WC": "geo_zueri_wc",
                "Kunst_im_Stadtraum": "geo_kunst_im_stadtraum",
                "Gastwirtschaftsbetriebe": "geo_gastwirtschaftsbetriebe",
                "Gemeinschaftszentrum": "geo_gemeinschaftszentrum",
                "Bauernhofladen": "geo_bauernhofladen",
                "Lehrpfade": "geo_lehrpfade",
                "Stadtspaziergaenge_Zueri_zFuess": "geo_stadtspaziergaenge_zueri_zfuess",
                "Liste_der_Informationstafeln_an_Denkmalschutz_Objekten":
                    "geo_liste_der_informationstafeln_an_denkmalschutz_objekten",
                "Auszeichnung_fuer_gute_Bauten": "geo_auszeichnung_fuer_gute_bauten",
                "Gemeindefriedhoefe": "geo_gemeindefriedhoefe",
                "Standorte_ZueriVelo": "geo_standorte_zuerivelo",
                "Hundezonen": "geo_hundezonen",
            },
        },
        "wikipedia": {
            "langs": ["en", "de"],
        },
    },
}


def city(key="zurich"):
    cfg = dict(CITIES[key])
    cfg["key"] = key
    cfg["dir"] = os.path.join(DATA_ROOT, key)
    cfg["raw"] = os.path.join(cfg["dir"], "raw")
    cfg["cache"] = os.path.join(cfg["dir"], "cache")
    cfg["context"] = os.path.join(cfg["dir"], "context")
    return cfg
