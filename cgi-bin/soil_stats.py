#!/usr/bin/env python3

import sys
import json
import cgitb
import os
import numpy as np
from osgeo import gdal, ogr
from scipy import stats
import gridex
import sys

# Enable CGI error tracing
cgitb.enable()

# Directories and root path to soil layers
BASE_DIR = "/var/www/data/POLARIS"
BASE_DIR = "/Users/eldawy/IdeaProjects/futurefarmnow/data/POLARIS"

# Supported layers
SUPPORTED_LAYERS = [
    "alpha", "bd", "clay", "hb", "ksat", "lambda", "n", "om", "ph",
    "sand", "silt", "theta_r", "theta_s"
]

def get_matching_subdirectories(polaris_path, depth_range, layer):
    """
    Get subdirectories that match the depth range query for a specific layer.

    :param polaris_path: The base path to the POLARIS dataset.
    :param depth_range: The depth range to query, e.g., "0-60".
    :param layer: The specific layer to query, e.g., "alpha", "bd", etc.
    :return: A list of subdirectories that match the depth range query.
    """
    matching_dirs = []

    # Parse the input depth range
    try:
        from_depth, to_depth = map(int, depth_range.split('-'))
    except ValueError:
        raise ValueError(f"Invalid depth range format: {depth_range}. Expected format is 'from-to'.")

    # Path to the specific layer directory
    layer_dir = os.path.join(polaris_path, layer)

    # Ensure the layer directory exists
    if not os.path.exists(layer_dir):
        raise FileNotFoundError(f"Layer directory does not exist: {layer_dir}")

    # Iterate over the subdirectories in the layer directory
    for subdir in os.listdir(layer_dir):
        if subdir.endswith("_compressed"):
            # Extract the depth range from the subdirectory name, e.g., "0_5_compressed" -> 0 and 5
            depth_str = subdir.replace("_compressed", "")
            try:
                sub_from_depth, sub_to_depth = map(int, depth_str.split('_'))
            except ValueError:
                continue  # Skip subdirectories that do not match the expected format

            # Check if the subdirectory depth range overlaps with the input range
            if (sub_from_depth <= to_depth) and (sub_to_depth >= from_depth):
                # If there is overlap, add the full subdirectory path to the matching list
                matching_dirs.append(os.path.join(layer_dir, subdir))

    return matching_dirs

def get_pixel_values_within_polygon(tiff_file, query_polygon):
    """
    Extract pixel values from the given TIFF file that overlap with the query polygon.

    :param tiff_file: Path to the TIFF file.
    :param query_polygon: A GeoJSON Polygon as a dictionary.
    :return: A NumPy array of pixel values that overlap with the polygon.
    """
    # Open the TIFF file
    dataset = gdal.Open(tiff_file)
    if dataset is None:
        raise FileNotFoundError(f"Could not open the TIFF file: {tiff_file}")

    # Convert the query GeoJSON polygon to OGR geometry
    polygon_geom = ogr.CreateGeometryFromJson(json.dumps(query_polygon))
    if polygon_geom is None:
        raise ValueError("Invalid GeoJSON polygon")

    # Create an in-memory layer to hold the polygon geometry
    mem_driver = ogr.GetDriverByName("Memory")
    mem_datasource = mem_driver.CreateDataSource("memDataSource")
    mem_layer = mem_datasource.CreateLayer("memLayer", geom_type=ogr.wkbPolygon)

    # Create a feature and set its geometry to the polygon
    feature_defn = mem_layer.GetLayerDefn()
    feature = ogr.Feature(feature_defn)
    feature.SetGeometry(polygon_geom)
    mem_layer.CreateFeature(feature)

    # Create a memory raster to hold the clipped data
    driver = gdal.GetDriverByName('MEM')
    out_raster = driver.Create('', dataset.RasterXSize, dataset.RasterYSize, 1, gdal.GDT_Float32)
    out_raster.SetGeoTransform(dataset.GetGeoTransform())
    out_raster.SetProjection(dataset.GetProjection())

    # Use WarpOptions to clip the raster with the in-memory geometry
    warp_options = gdal.WarpOptions(
        format='MEM',  # Use in-memory raster format
        cutlineDSName=None,  # No cutline DSName needed, using direct cutline
        cutlineLayer=mem_layer.GetName(),  # Use the in-memory layer for cutline
        cropToCutline=True  # Crop the raster to the cutline
    )

    # Perform the warp operation (clipping) using the cutline geometry
    gdal.Warp(out_raster, dataset, options=warp_options)

    # Read the clipped data from the raster
    raster_band = out_raster.GetRasterBand(1)
    clipped_data = raster_band.ReadAsArray()

    # Extract NoData value for the band, if any
    no_data_value = raster_band.GetNoDataValue()

    # Flatten the array and filter out NoData values
    if no_data_value is not None:
        pixel_values = clipped_data[clipped_data != no_data_value].flatten()
    else:
        pixel_values = clipped_data.flatten()

    return pixel_values

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

def main():
    print("Content-Type: application/json")
    print()

    try:
        # Get Content-Length header to determine the size of the input
        content_length = os.getenv("CONTENT_LENGTH")
        if content_length:
            content_length = int(content_length)
            # Read exactly `content_length` bytes from stdin
            payload = sys.stdin.read(content_length)
        else:
            payload = ""

        if not payload:
            print(json.dumps({"error": "No data provided"}))
            return

        # Parse the POST body (GeoJSON polygon)
        data = json.loads(payload)
        if "type" not in data or data["type"] != "Polygon":
            print(json.dumps({"error": "Invalid GeoJSON polygon"}))
            return
        query_polygon = data

        # Extract query parameters from the URL
        query_string = os.getenv("QUERY_STRING")
        if not query_string:
            print(json.dumps({"error": "Missing QUERY_STRING"}))
            return

        # Parse the query parameters safely
        params = dict(param.split('=') for param in query_string.split('&'))
        depth_range = params.get("depth")
        layer = params.get("layer")

        if not depth_range or not layer or layer not in SUPPORTED_LAYERS:
            print(json.dumps({"error": "Invalid input"}))
            return

        # Get the matching subdirectories based on depth range and layer
        matching_subdirs = get_matching_subdirectories(BASE_DIR, depth_range, layer)
        if not matching_subdirs:
            print(json.dumps({"error": "No subdirectories found for the given depth range and layer"}))
            return

        # Initialize results for weighted statistics
        all_pixel_values = []
        total_weight = 0

        # Loop through each matching subdirectory
        for subdir in matching_subdirs:
            depth_str = os.path.basename(subdir).replace("_compressed", "")
            sub_from_depth, sub_to_depth = map(int, depth_str.split('_'))
            depth_weight = sub_to_depth - sub_from_depth

            # Use gridex.query_index to find the TIFF files that overlap the query polygon
            tiff_files = gridex.query_index(subdir, json.dumps(query_polygon))
            if not tiff_files:
                continue

            # Loop over each TIFF file and extract pixel values within the polygon
            for tiff_file in tiff_files:
                tiff_file_path = os.path.join(subdir, tiff_file)
                pixel_values = get_pixel_values_within_polygon(tiff_file_path, query_polygon)

                # If we found any pixel values, apply the weight based on depth range
                if pixel_values.size > 0:
                    all_pixel_values.extend(pixel_values * depth_weight)
                    total_weight += depth_weight

        if not all_pixel_values:
            print(json.dumps({"error": "No valid data found in the queried area"}))
            return

        # Convert list to NumPy array for statistical calculations
        all_pixel_values = np.array(all_pixel_values)

        # Calculate weighted statistics
        weighted_stats = calculate_statistics(all_pixel_values / total_weight)

        # Return the response in JSON format
        response = {
            "query": {
                "depth_range": depth_range,
                "layer": layer
            },
            "results": weighted_stats
        }
        print(json.dumps(response, indent=4))

    except Exception as e:
        print(json.dumps({"error": str(e)}))

if __name__ == "__main__":
    main()
