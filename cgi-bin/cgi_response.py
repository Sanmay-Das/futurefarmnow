#!/usr/bin/python

import cgi
import cgitb
import json
import pandas as pd
import os
import tempfile
from io import StringIO
from extract_points import *
from choose_points import *

cgitb.enable()

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

    for layer in layers:
        layer_data = original_df[layer]
        sample_data = sample_df[layer]
        
        layer_stats = {
            "actual": {
                "max": layer_data.max(),
                "min": layer_data.min(),
                "sum": layer_data.sum(),
                "median": layer_data.median(),
                "stddev": layer_data.std(),
                "count": layer_data.count(),
                "mean": layer_data.mean(),
                "lowerquart": layer_data.quantile(0.25),
                "upperquart": layer_data.quantile(0.75),
            },
            "sample": {
                "max": sample_data.max(), 
                "min": sample_data.min(),
                "sum": sample_data.sum(),
                "median": sample_data.median(),
                "stddev": sample_data.std(),
                "count": sample_data.count(),
                "mean": sample_data.mean(),
                "lowerquart": sample_data.quantile(0.25),
                "upperquart": sample_data.quantile(0.75),
            }
        }
        statistics[layer] = layer_stats

    return statistics

def process_request(form):
    wkt = form.getvalue("wkt")
    soil_depth = form.getvalue("soildepth")
    layers = form.getlist("layer")
    num_points = form.getvalue("num_points")
    
    try:
        num_points = int(num_points)
    except ValueError:
        num_points = 0  # Default to 0 if there's an error

    #Calculates layer values at each point
    df = output_from_attr(input_dir='sample_data', wkt=wkt, depth_range=soil_depth,
                          attribute_list=layers, num_samples=num_points)
    
    #Chooses what points to use
    sample_df = select_points(df, epsg_code=4326)
    
    # Calculate statistics for the layers
    statistics = calculate_statistics(sample_df, df)

    response_data = {
        "query": wkt,
        "results": [{"x": row['x'], "y": row['y'], "id": row['id']} for index, row in sample_df.iterrows()],
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

def main():
    form = cgi.FieldStorage()

    if form:
        response_json = process_request(form)
        
        send_response(response_json)
    else:
        print("Content-Type: text/html\n\n")
        print("<html><body><h2>Error: No form data received.</h2></body></html>")

if __name__ == "__main__":
    main()