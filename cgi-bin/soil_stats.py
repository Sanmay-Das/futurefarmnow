#!/usr/bin/env python3

import os
import cgi
import cgitb
import json
import numpy as np
from osgeo import gdal, ogr
from scipy import stats
import geojson

# Enable CGI error tracing
cgitb.enable()

# Directories and root path to soil layers
BASE_DIR = "/var/www/data/POLARIS"

# Supported layers
SUPPORTED_LAYERS = [
    "alpha", "bd", "clay", "hb", "ksat", "lambda", "n", "om", "ph",
    "sand", "silt", "theta_r", "theta_s"
]

# Updated helper function to read _index.csv and get relevant TIFF files based on MBR overlap
def get_tiff_files(layer_dir, soil_depth, query_polygon):
    """
    Reads the _index.csv file and retrieves relevant TIFF files whose MBR
    intersects with the given GeoJSON polygon.

    :param layer_dir: The directory for the specific soil layer
    :param soil_depth: The depth range in the format "from_to"
    :param query_polygon: GeoJSON polygon as a dictionary
    :return: List of TIFF file paths that overlap with the query polygon
    """
    index_file = os.path.join(layer_dir, f"{soil_depth}_compressed/_index.csv")
    tiff_files = []

    if not os.path.exists(index_file):
        return tiff_files

    # Convert the GeoJSON polygon to an OGR geometry object
    polygon_geom = ogr.CreateGeometryFromJson(json.dumps(query_polygon))
    polygon_mbr = polygon_geom.GetEnvelope()  # (minX, maxX, minY, maxY)

    with open(index_file, "r") as f:
        next(f)  # Skip the header line

        for line in f:
            parts = line.strip().split(";")
            tiff_file_name = parts[1]

            # Extract MBR from the _index.csv (x1, y1, x2, y2 are in the file)
            file_mbr = (
                float(parts[3]),  # x1 (minX)
                float(parts[5]),  # x2 (maxX)
                float(parts[4]),  # y1 (minY)
                float(parts[6])   # y2 (maxY)
            )

            # Check for MBR overlap between the polygon and the TIFF file's MBR
            if mbr_overlap(polygon_mbr, file_mbr):
                tiff_files.append(os.path.join(layer_dir, f"{soil_depth}_compressed", tiff_file_name))

    return tiff_files

# Helper function to check if two MBRs overlap
def mbr_overlap(polygon_mbr, file_mbr):
    """
    Checks if two bounding boxes overlap.

    :param polygon_mbr: Tuple representing the polygon's MBR (minX, maxX, minY, maxY)
    :param file_mbr: Tuple representing the TIFF file's MBR (minX, maxX, minY, maxY)
    :return: True if the bounding boxes overlap, False otherwise
    """
    # Unpack the bounding boxes
    p_min_x, p_max_x, p_min_y, p_max_y = polygon_mbr
    f_min_x, f_max_x, f_min_y, f_max_y = file_mbr

    # Check for overlap
    return not (p_max_x < f_min_x or p_min_x > f_max_x or p_max_y < f_min_y or p_min_y > f_max_y)

# Function to extract the values for a given GeoJSON polygon from the TIFF files
def extract_polygon_data(tiff_files, query_polygon):
    raster_values = []

    # Convert GeoJSON polygon to OGR geometry
    polygon_geom = ogr.CreateGeometryFromJson(json.dumps(query_polygon))

    for tiff_file in tiff_files:
        dataset = gdal.Open(tiff_file)
        if not dataset:
            continue

        # Get the spatial reference and transformation
        geo_transform = dataset.GetGeoTransform()
        projection = dataset.GetProjection()

        # Rasterize the polygon and get pixel values
        raster_band = dataset.GetRasterBand(1)
        no_data_value = raster_band.GetNoDataValue()

        for row in range(dataset.RasterYSize):
            for col in range(dataset.RasterXSize):
                x = geo_transform[0] + col * geo_transform[1]
                y = geo_transform[3] + row * geo_transform[5]

                # Check if the point (x, y) lies within the polygon
                point = ogr.Geometry(ogr.wkbPoint)
                point.AddPoint(x, y)
                if polygon_geom.Contains(point):
                    value = raster_band.ReadAsArray(col, row, 1, 1)[0][0]
                    if value != no_data_value:
                        raster_values.append(value)

        dataset = None  # Close the file

    return np.array(raster_values)

# Function to calculate statistics
def calculate_statistics(values):
    if len(values) == 0:
        return {}

    return {
        "min": float(np.min(values)),
        "max": float(np.max(values)),
        "mean": float(np.mean(values)),
        "stddev": float(np.std(values)),
        "median": float(np.median(values)),
        "lowerquart": float(np.percentile(values, 25)),
        "upperquart": float(np.percentile(values, 75)),
        "sum": float(np.sum(values)),
        "count": int(len(values))
    }

# Main function to handle the HTTP request
def main():
    print("Content-Type: application/json")
    print()

    form = cgi.FieldStorage()

    # Extract query parameters
    soil_depth = form.getvalue("soildepth")
    layer = form.getvalue("layer")
    payload = json.loads(sys.stdin.read())

    if not soil_depth or not layer or layer not in SUPPORTED_LAYERS:
        print(json.dumps({"error": "Invalid input"}))
        return

    # Validate and parse soil depth
    try:
        from_depth, to_depth = soil_depth.split("-")
        from_depth = int(from_depth)
        to_depth = int(to_depth)
    except ValueError:
        print(json.dumps({"error": "Invalid soil depth format"}))
        return

    # Get the GeoJSON query polygon
    if "type" not in payload or payload["type"] != "Polygon":
        print(json.dumps({"error": "Invalid GeoJSON polygon"}))
        return

    query_polygon = payload

    # Build the directory path for the requested layer and depth
    layer_dir = os.path.join(BASE_DIR, layer)
    depth_str = f"{from_depth}_{to_depth}"

    # Retrieve relevant TIFF files that overlap with the query polygon's MBR
    tiff_files = get_tiff_files(layer_dir, depth_str, query_polygon)
    if not tiff_files:
        print(json.dumps({"error": "No TIFF files found for the given layer and depth"}))
        return

    # Extract data for the given polygon
    polygon_values = extract_polygon_data(tiff_files, query_polygon)

    # Calculate statistics
    stats_result = calculate_statistics(polygon_values)

    if not stats_result:
        print(json.dumps({"error": "No valid data found in the queried area"}))
        return

    # Return the response in JSON format
    response = {
        "query": {
            "soildepth": soil_depth,
            "layer": layer
        },
        "results": stats_result
    }
    print(json.dumps(response, indent=4))

if __name__ == "__main__":
    main()
