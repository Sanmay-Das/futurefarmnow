#!/usr/bin/env python3

import json
import pandas as pd
import os
import sys
import tempfile
from io import StringIO
from flask import Flask, request, jsonify, send_from_directory
from shapely.geometry import shape
from extract_points import *
from choose_points import *
from soil import BASE_DIR

SUPPORTED_LAYERS = [
    "alpha", "bd", "clay", "hb", "ksat", "lambda", "n", "om", "ph",
    "sand", "silt", "theta_r", "theta_s"
]

def calculate_statistics(sample, original_df):
    statistics = {}
    layers = original_df.columns[2:]
    sample_df = pd.DataFrame()
    for i, row in sample.iterrows():
        x_value = row['x']
        y_value = row['y']
        matching_rows = original_df[(original_df['x'] == x_value) & (original_df['y'] == y_value)]
        if not sample_df.empty:
            sample_df = pd.concat([sample_df, matching_rows], ignore_index=True)
        else:
            sample_df = matching_rows
    if sample_df.empty:
        return "No matching data found for the sample"
    elif len(sample_df) != len(sample):
        return "Duplicate Points"

    def to_python(value):
        # Convert NumPy types to Python types
        if isinstance(value, (pd.Series, pd.Index)):
            return value.to_list()  # Handles pandas series/index objects
        elif hasattr(value, "item"):
            return value.item()  # Handles numpy scalar types
        else:
            return value  # Return as is for standard Python types

    for layer in layers:
        layer_data = original_df[layer]
        sample_data = sample_df[layer]

        layer_stats = {
            "actual": {
                "max": to_python(layer_data.max()),
                "min": to_python(layer_data.min()),
                "sum": to_python(layer_data.sum()),
                "median": to_python(layer_data.median()),
                "stddev": to_python(layer_data.std()),
                "count": to_python(layer_data.count()),
                "mean": to_python(layer_data.mean()),
                "lowerquart": to_python(layer_data.quantile(0.25)),
                "upperquart": to_python(layer_data.quantile(0.75)),
            },
            "sample": {
                "max": to_python(sample_data.max()),
                "min": to_python(sample_data.min()),
                "sum": to_python(sample_data.sum()),
                "median": to_python(sample_data.median()),
                "stddev": to_python(sample_data.std()),
                "count": to_python(sample_data.count()),
                "mean": to_python(sample_data.mean()),
                "lowerquart": to_python(sample_data.quantile(0.25)),
                "upperquart": to_python(sample_data.quantile(0.75)),
            }
        }
        statistics[layer] = layer_stats

    return statistics

def process_request(query_params, query_geometry):
    soil_depth = query_params.get("soildepth")
    layers = query_params.getlist("layer")
    num_points = int(query_params.get("num_points"))

    # Calculate layer values at each point
    df = output_from_attr(
        input_dir=BASE_DIR,
        geometry=query_geometry,
        depth_range=soil_depth,
        attribute_list=layers,
        num_samples=num_points
    )

    # Choose what points to use
    sample_df = select_points(df, num_samples=num_points, epsg_code=4326)

    # Calculate statistics for the layers
    statistics = calculate_statistics(sample_df, df)

    import shapely.geometry
    response_data = {
        "query": shapely.geometry.mapping(query_geometry),
        "results": [{"x": row['x'], "y": row['y'], "id": index} for index, row in sample_df.iterrows()],
        "statistics": {
            "layers": statistics
        }
    }

    # Convert the response data to JSON format
    response_json = json.dumps(response_data, indent=4)

    return response_json

def send_response(response_json):
    content_length = len(response_json)
    print("Content-Type: application/json")
    print(f"Content-Length: {content_length}")
    print()
    print(response_json)

app = Flask(__name__)

# Define the main endpoint
@app.route('/soil/sample.json', methods=['POST', 'GET'])
def soil_sample():
    # Extract query parameters from the URL
    query_params = request.args  # Automatically handles QUERY_STRING
    try:
        # Read and parse GeoJSON geometry from the payload
        print("Request data:", str(request.data), file=sys.stderr)
        query_geometry = shape(request.get_json())

        # Simulate process_request function (implement your logic here)
        response_json = process_request(query_params, query_geometry)

        # Send the response
        return jsonify(response_json)

    except (ValueError, json.JSONDecodeError) as e:
        return jsonify({"error": "Invalid JSON payload."}), 400
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    # Serve static files only in development mode
    @app.route('/public_html/<path:filename>')
    def serve_static(filename):
        static_folder = '../public_html'
        return send_from_directory(static_folder, filename)

    app.run(debug=True)