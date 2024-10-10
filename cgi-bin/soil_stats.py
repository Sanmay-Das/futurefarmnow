#!/usr/bin/env python3

"""
File: soil_stats.py

This script is designed to process GeoTIFF (.tif) files and extract pixel statistics within a given query polygon
using weighted depth values. The script reads input as GeoJSON, locates matching TIFF files, and calculates statistics
on the pixels within the polygon using parallel processing for efficiency.

Functions:

- get_pixel_values_within_polygon(tiff_file, query_polygon):
    Extracts pixel values from a TIFF file that overlap with a GeoJSON query polygon. The values are returned as a
    NumPy array, excluding NoData values.

- calculate_statistics(values):
    Computes basic statistics (min, max, mean, standard deviation, median, quartiles, sum, and count) for the given
    set of values. Returns an empty dictionary if the input is empty.

- process_tiff_file(tiff_file_info, query_polygon):
    Processes a single TIFF file by extracting pixel values that overlap with the query polygon, applying a depth weight,
    and returning the weighted pixel values.

- main():
    The entry point of the script. It reads a GeoJSON polygon and query parameters (depth and layer) from input, finds
    matching TIFF files using `gridex.query_index`, processes them in parallel, and returns weighted statistics on the
    pixel values in JSON format.
"""


import sys
import json
import cgitb
import os
import numpy as np
from osgeo import gdal, ogr
from scipy import stats
import gridex
import concurrent.futures
from functools import partial
import soil  # Import the soil module

# Enable CGI error tracing
cgitb.enable()

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


def process_tiff_file(tiff_file_info, query_polygon):
    """
    Process a single TIFF file to extract pixel values that overlap with the query polygon,
    apply the depth weight, and return the weighted pixel values.

    :param tiff_file_info: A tuple containing the TIFF file path and depth weight.
    :param query_polygon: The GeoJSON query polygon.
    :return: A tuple containing the weighted pixel values and the depth weight.
    """
    tiff_file_path, depth_weight = tiff_file_info
    pixel_values = get_pixel_values_within_polygon(tiff_file_path, query_polygon)

    # Apply the depth weight if there are any valid pixel values
    if pixel_values.size > 0:
        return pixel_values * depth_weight, depth_weight
    return np.array([]), 0


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

        # Use soil.SUPPORTED_LAYERS and soil.BASE_DIR from the imported soil module
        if not depth_range or not layer or layer not in soil.SUPPORTED_LAYERS:
            print(json.dumps({"error": "Invalid input"}))
            return

        # Get the matching subdirectories based on depth range and layer using soil.get_matching_subdirectories
        matching_subdirs = soil.get_matching_subdirectories(soil.BASE_DIR, depth_range, layer)
        if not matching_subdirs:
            print(json.dumps({"error": "No subdirectories found for the given depth range and layer"}))
            return

        # Collect all TIFF files and their associated depth weight
        tiff_file_infos = []
        for subdir in matching_subdirs:
            depth_str = os.path.basename(subdir).replace("_compressed", "")
            sub_from_depth, sub_to_depth = map(int, depth_str.split('_'))
            depth_weight = sub_to_depth - sub_from_depth

            # Use gridex.query_index to find the TIFF files that overlap the query polygon
            tiff_files = gridex.query_index(subdir, json.dumps(query_polygon))
            if not tiff_files:
                continue

            # Append the TIFF file paths along with the associated depth weight
            for tiff_file in tiff_files:
                tiff_file_path = os.path.join(subdir, tiff_file)
                tiff_file_infos.append((tiff_file_path, depth_weight))

        if not tiff_file_infos:
            print(json.dumps({"error": "No data files found for the given query"}))
            return

        # Use ThreadPoolExecutor to process the files in parallel
        all_pixel_values = []
        total_weight = 0
        num_threads = os.cpu_count()

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
            # Process each TIFF file in parallel
            future_to_tiff = {executor.submit(process_tiff_file, tiff_file_info, query_polygon): tiff_file_info for tiff_file_info in tiff_file_infos}

            for future in concurrent.futures.as_completed(future_to_tiff):
                pixel_values, depth_weight = future.result()
                if pixel_values.size > 0:
                    all_pixel_values.extend(pixel_values)
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
