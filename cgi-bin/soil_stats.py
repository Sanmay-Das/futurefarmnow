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

from flask import Blueprint, request, jsonify
from osgeo import gdal, ogr
import numpy as np
import concurrent.futures
import os
import json
from functools import partial
import soil  # Import the soil module
import gridex
import shapely

soil_stats_bp = Blueprint("soil_stats", __name__)

# Function to get pixel values within a polygon
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
    polygon_geom = ogr.CreateGeometryFromWkt(query_polygon.wkt)
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

# Function to process TIFF files
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

# Endpoint for soil_stats
@soil_stats_bp.route('/soil/singlepolygon.json', methods=['POST', 'GET'])
def soil_stats():
    try:
        # Parse GeoJSON polygon from the request body
        query_polygon = request.get_json()
        if not query_polygon:
            return jsonify({"error": "Invalid GeoJSON polygon"}), 400
        from shapely.geometry import shape
        query_polygon = shape(query_polygon)

        # Parse query parameters
        query_params = request.args
        depth_range = query_params.get("soildepth")
        layer = query_params.get("layer")

        if not depth_range or not layer or layer not in soil.SUPPORTED_LAYERS:
            return jsonify({"error": "Invalid depth or layer parameter"}), 400

        # Get matching subdirectories
        matching_subdirs = soil.get_matching_subdirectories(soil.BASE_DIR, depth_range, layer)
        if not matching_subdirs:
            return jsonify({"error": "No subdirectories found for the given depth range and layer"}), 404

        # Collect all TIFF files and their associated depth weight
        tiff_file_infos = []
        for subdir in matching_subdirs:
            depth_str = os.path.basename(subdir).replace("_compressed", "")
            sub_from_depth, sub_to_depth = map(int, depth_str.split('_'))
            depth_weight = sub_to_depth - sub_from_depth

            # Find TIFF files that overlap with the query polygon
            tiff_files = gridex.query_index(subdir, query_polygon)
            if not tiff_files:
                continue

            # Append the TIFF file paths along with the associated depth weight
            for tiff_file in tiff_files:
                tiff_file_path = os.path.join(subdir, tiff_file)
                tiff_file_infos.append((tiff_file_path, depth_weight))

        if not tiff_file_infos:
            return jsonify({"error": "No data files found for the given query"}), 404

        # Process files in parallel
        all_pixel_values = []
        total_weight = 0
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future_to_tiff = {
                executor.submit(process_tiff_file, tiff_file_info, query_polygon): tiff_file_info
                for tiff_file_info in tiff_file_infos
            }

            for future in concurrent.futures.as_completed(future_to_tiff):
                pixel_values, depth_weight = future.result()
                if pixel_values.size > 0:
                    all_pixel_values.extend(pixel_values)
                    total_weight += depth_weight

        if not all_pixel_values:
            return jsonify({"error": "No valid data found in the queried area"}), 404

        # Calculate weighted statistics
        all_pixel_values = np.array(all_pixel_values)
        weighted_stats = calculate_statistics(all_pixel_values / total_weight)

        # Return JSON response
        import shapely
        response = {
            "query": {
                "geometry": shapely.geometry.mapping(query_polygon),
                "depth_range": depth_range,
                "layer": layer
            },
            "results": weighted_stats
        }
        return jsonify(response)

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
