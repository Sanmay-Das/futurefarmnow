from flask import Blueprint, request, jsonify
from shapely.geometry import shape
import pynldas2 as nldas

nldas_bp = Blueprint("nldas", __name__)

@nldas_bp.route("/nldas", methods=["POST"])
def nldas_index():
    # dates
    try:
        date_from = request.args["from"]
        date_to   = request.args["to"]
    except KeyError as e:
        return jsonify({"error": f"Missing required parameter: {e.args[0]}"}), 400

    # AOI
    body = request.get_json(force=True)
    try:
        geom = shape(body)
    except Exception:
        return jsonify({"error": "Invalid GeoJSON body"}), 400

    # Fetch File A for whole AOI
    try:
        ds = nldas.get_bygeom(geom, date_from, date_to)
    except Exception as e:
        return jsonify({"error": "Failed to fetch NLDAS data", "details": str(e)}), 500

    # Sample at each vertex (x=lon, y=lat)
    coords = body["coordinates"][0]
    results = []
    for idx, (lon, lat) in enumerate(coords):
        point_ds = ds.sel(x=lon, y=lat, method="nearest")
        df = point_ds.to_dataframe().reset_index()
        series = {var: df[var].tolist() for var in ds.data_vars}
        series["time"] = df["time"].astype(str).tolist()
        results.append({
            "id":     idx,
            "x":      lon,
            "y":      lat,
            "series": series
        })

    return jsonify({
        "query":   {"from": date_from, "to": date_to, "geometry": body},
        "results": results
    }), 200
