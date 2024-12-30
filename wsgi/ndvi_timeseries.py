"""
File: ndvi_timeseries.py

This script is designed to process NDVI GeoTIFF (.tif) files and extract time series data for a given query polygon.
The NDVI values are stored in a directory structure organized by dates, and this script queries the data to compute
mean NDVI values for the specified date range. The API serves the results in JSON format.

Functions:

- get_mean_ndvi(tiff_file, query_polygon):
    Extracts the mean NDVI value from a GeoTIFF file that overlaps with a query polygon.

- ndvi_timeseries():
    Handles the `/ndvi/singlepolygon.json` API endpoint to compute and return NDVI time series data
    for a GeoJSON-defined polygon and date range.

Constants:

- NDVI_DATA_DIR: The base directory containing subdirectories of NDVI data organized by date.

Usage:

The script is integrated as a Flask blueprint and can be used in a Flask application. It provides the following endpoint:

    - /ndvi/singlepolygon.json: Returns mean NDVI values for a specified polygon and date range.

API Details:

Endpoint: `/ndvi/singlepolygon.json`
HTTP Methods: GET, POST
Parameters:
    - from (required): Start date in the format `yyyy-mm-dd`.
    - to (required): End date in the format `yyyy-mm-dd`.
POST Payload: GeoJSON object defining the query polygon.

Example Usage:

GET/POST:
```
POST /ndvi/singlepolygon.json?from=2023-01-01&to=2023-01-10
Payload:
{
  "type": "Polygon",
  "coordinates": [
    [
      [ -120.1, 36.9 ],
      [ -120.2, 36.9 ],
      [ -120.2, 37.0 ],
      [ -120.1, 37.0 ],
      [ -120.1, 36.9 ]
    ]
  ]
}
```

Response:
```
{
  "query": {
    "from": "2023-01-01",
    "to": "2023-01-10"
  },
  "results": [
    {"date": "2023-01-01", "mean": 0.35},
    {"date": "2023-01-02", "mean": 0.42},
    ...
  ]
}
```
"""

from flask import Blueprint, request, jsonify
from osgeo import gdal, ogr
import os
import numpy as np
from shapely.geometry import shape
import gridex
import concurrent.futures
import shapely
from conf import NDVI_DATA_DIR

ndvi_timeseries_bp = Blueprint("ndvi_timeseries", __name__)

# Function to extract mean NDVI values for a given polygon and TIFF file
def get_mean_ndvi(tiff_file, query_polygon):
    """
    Extracts the mean NDVI value from the TIFF file that overlaps with the query polygon.

    :param tiff_file: Path to the TIFF file.
    :param query_polygon: A GeoJSON Polygon as a Shapely geometry object.
    :return: The mean NDVI value or None if no data is available.
    """
    dataset = gdal.Open(tiff_file)
    if dataset is None:
        raise FileNotFoundError(f"Could not open the TIFF file: {tiff_file}")

    # Convert query polygon to WKT for clipping
    polygon_geom = ogr.CreateGeometryFromWkt(query_polygon.wkt)

    # Create an in-memory layer for the polygon
    mem_driver = ogr.GetDriverByName("Memory")
    mem_datasource = mem_driver.CreateDataSource("memDataSource")
    mem_layer = mem_datasource.CreateLayer("memLayer", geom_type=ogr.wkbPolygon)
    feature_defn = mem_layer.GetLayerDefn()
    feature = ogr.Feature(feature_defn)
    feature.SetGeometry(polygon_geom)
    mem_layer.CreateFeature(feature)

    # Create an in-memory raster to hold the clipped data
    driver = gdal.GetDriverByName('MEM')
    out_raster = driver.Create('', dataset.RasterXSize, dataset.RasterYSize, 1, gdal.GDT_Byte)
    out_raster.SetGeoTransform(dataset.GetGeoTransform())
    out_raster.SetProjection(dataset.GetProjection())

    warp_options = gdal.WarpOptions(
        format='MEM',
        cutlineDSName=None,
        cutlineLayer=mem_layer.GetName(),
        cropToCutline=True
    )

    gdal.Warp(out_raster, dataset, options=warp_options)

    raster_band = out_raster.GetRasterBand(1)
    clipped_data = raster_band.ReadAsArray()

    # Mask NoData values (0 means NoData)
    clipped_data = clipped_data[clipped_data != 0]

    if clipped_data.size == 0:
        return None

    # Scale values from [1, 255] to [-1, +1]
    scaled_data = (clipped_data - 1) * (2 / 254) - 1

    return np.mean(scaled_data)

@ndvi_timeseries_bp.route('/ndvi/singlepolygon.json', methods=['POST', 'GET'])
def ndvi_timeseries():
    """
    Fetch NDVI time series for a given GeoJSON polygon and date range.
    Processes all directories in parallel for better performance.
    """
    # Parse GeoJSON polygon from the request body
    query_polygon = request.get_json()
    if not query_polygon:
        return jsonify({"error": "Invalid GeoJSON polygon"}), 400

    query_polygon = shape(query_polygon)

    # Parse query parameters
    query_params = request.args
    from_date = query_params.get("from")
    to_date = query_params.get("to")

    if not from_date or not to_date:
        return jsonify({"error": "Missing required date range parameters"}), 400

    # Filter directories matching the date range
    filtered_subdirs = [
        os.path.join(NDVI_DATA_DIR, subdir)
        for subdir in sorted(os.listdir(NDVI_DATA_DIR))
        if from_date <= subdir <= to_date
    ]

    if not filtered_subdirs:
        return jsonify({"error": "No data found for the given date range"}), 404

    # Define a helper function to process a single directory
    def process_directory(subdir_path):
        tiff_files = gridex.query_index(subdir_path, query_polygon)
        if not tiff_files:
            return None

        day_means = []
        for tiff_file in tiff_files:
            mean_ndvi = get_mean_ndvi(os.path.join(subdir_path, tiff_file), query_polygon)
            if mean_ndvi is not None:
                day_means.append(mean_ndvi)

        if day_means:
            date = os.path.basename(subdir_path)
            return {"date": date, "mean": np.mean(day_means)}

        return None

    # Process all directories in parallel
    results = []
    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = {
            executor.submit(process_directory, subdir_path): subdir_path
            for subdir_path in filtered_subdirs
        }

        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            if result:
                results.append(result)

    if not results:
        return jsonify({"error": "No data found for the given query"}), 404

    response = {
        "query": {
            "geometry": shapely.geometry.mapping(query_polygon),
            "from": from_date,
            "to": to_date
        },
        "results": sorted(results, key=lambda x: x["date"])
    }
    return jsonify(response)
