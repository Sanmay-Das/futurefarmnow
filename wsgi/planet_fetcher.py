# wsgi/planet_fetcher.py
import os, requests
from requests.auth import HTTPBasicAuth
from flask import Blueprint, request, jsonify
from shapely.geometry import shape, Point

planet_bp = Blueprint("planet", __name__)

def _search_planet_by_geom(geom, date_from, date_to):
    API_KEY = os.getenv("PL_API_KEY")
    if not API_KEY:
        raise RuntimeError("PL_API_KEY not set")

    auth = HTTPBasicAuth(API_KEY, "")
    url = "https://api.planet.com/data/v1/quick-search"

    payload = {
        "item_types": ["PSScene","SkySatCollect"],
        "filter": {
            "type":   "AndFilter",
            "config": [
                { "type":"GeometryFilter",
                  "field_name":"geometry",
                  "config": geom },
                { "type":"DateRangeFilter",
                  "field_name":"acquired",
                  "config": { "gte": date_from, "lte": date_to } }
            ]
        }
    }

    r = requests.post(url, auth=auth, json=payload)
    r.raise_for_status()
    features = r.json().get("features", [])
    return [
        {
            "id":          f["id"],
            "acquired":    f["properties"]["acquired"],
            "item_type":   f["properties"]["item_type"],
            "cloud_cover": f["properties"].get("cloud_cover")
        }
        for f in features
    ]

@planet_bp.route("/planet", methods=["POST"])
def planet_index():
    # parse date range
    try:
        date_from = request.args["from"]
        date_to   = request.args["to"]
    except KeyError as e:
        return jsonify({"error": f"Missing required parameter: {e.args[0]}"}), 400

    # parse & validate GeoJSON from body
    geojson = request.get_json(silent=True)
    if not geojson:
        return jsonify({"error": "POST body must be a valid GeoJSON Polygon"}), 400

    try:
        poly = shape(geojson)
        if not poly.is_valid or poly.geom_type != "Polygon":
            raise ValueError()
    except Exception:
        return jsonify({"error": "Invalid GeoJSON Polygon"}), 400

    # get each vertex
    points = list(poly.exterior.coords)

    # for each point, run a point‐based search
    out = []
    for idx, (lon, lat) in enumerate(points):
        pt = {"type": "Point", "coordinates": [lon, lat]}
        scenes = _search_planet_by_geom(pt, date_from, date_to)
        out.append({
            "id":      idx,
            "x":       lon,
            "y":       lat,
            "scenes":  scenes
        })

    return jsonify({
        "count":  len(out),
        "points": out
    }), 200
