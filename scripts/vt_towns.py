"""
Vermont town lookups backed by data/Vermont_Town_GEOID_RPC_County.geojson.

    load_town_county_map()   -> {town: county}
    load_town_centroids()    -> {town: (lat, lng)}

Both include the entries in CITY_ALIASES.
"""

import json
from pathlib import Path

GEOJSON_PATH = Path(__file__).resolve().parent.parent / "data" / "Vermont_Town_GEOID_RPC_County.geojson"

# Additional aliases for common alternate spellings not in the GeoJSON
CITY_ALIASES = {
    "St. Albans": "Saint Albans City",
    "Saint Albans": "Saint Albans City",
    "St Albans": "Saint Albans City",
    "Rutland": "Rutland City",
    "Barre": "Barre City",
    "Hyde Park": "Hyde Park",  # Lamoille County
}


def _town_name(props):
    return (props.get("TOWNNAMEMC") or props.get("Municipal_Name") or "").strip()


def load_town_county_map():
    with GEOJSON_PATH.open(encoding="utf-8") as f:
        gj = json.load(f)
    mapping = {}
    for feat in gj["features"]:
        props = feat["properties"]
        town = _town_name(props)
        county = (props.get("County") or "").strip()
        if town and county:
            mapping[town] = county
    # Add aliases
    for alias, canonical in CITY_ALIASES.items():
        if canonical in mapping:
            mapping[alias] = mapping[canonical]
    return mapping


def _ring_centroid(ring):
    """Area-weighted centroid of a closed [lng, lat] ring -> (lat, lng)."""
    area2 = cx = cy = 0.0
    for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1]):
        cross = x0 * y1 - x1 * y0
        area2 += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    if area2 == 0:  # degenerate ring: fall back to the vertex mean
        return (sum(p[1] for p in ring) / len(ring), sum(p[0] for p in ring) / len(ring))
    return (cy / (3 * area2), cx / (3 * area2))


def _outer_ring(geometry):
    if geometry["type"] == "Polygon":
        ring = geometry["coordinates"][0]
    else:  # MultiPolygon: largest outer ring by vertex count
        ring = max((poly[0] for poly in geometry["coordinates"]), key=len)
    # GeoJSON rings repeat the first point at the end; drop it
    return ring[:-1] if ring[0] == ring[-1] else ring


def load_town_centroids():
    with GEOJSON_PATH.open(encoding="utf-8") as f:
        gj = json.load(f)
    centroids = {}
    for feat in gj["features"]:
        town = _town_name(feat["properties"])
        if town:
            lat, lng = _ring_centroid(_outer_ring(feat["geometry"]))
            centroids[town] = (round(lat, 4), round(lng, 4))
    for alias, canonical in CITY_ALIASES.items():
        if canonical in centroids:
            centroids[alias] = centroids[canonical]
    return centroids
