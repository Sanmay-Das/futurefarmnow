import os
import logging
from datetime import datetime, timedelta
from cdsetool.query import query_features
from cdsetool.download import download_feature
from cdsetool.credentials import Credentials
from cdsetool.monitor import StatusMonitor
from concurrent.futures import ThreadPoolExecutor, as_completed
import multiprocessing
import json
import zipfile
import rasterio
from rasterio.enums import Resampling
import numpy as np
from shapely.geometry import shape
from tqdm import tqdm
from download_sentinel2 import setup_logging, download_sentinel2_data
from shapely.geometry import shape
import geopandas as gpd

from shapely.geometry import box
from shapely.ops import split
from shapely.affinity import translate

# Set up logging
logger = logging.getLogger(__name__)

def create_grid(geometry, cell_size=3.0):
    """
    Create a uniform grid of polygons over the bounding box of the input geometry.
    Args:
        geometry (shapely.geometry.Polygon): Input geometry.
        cell_size (float): Size of each cell in degrees (3').
    Returns:
        list: List of smaller polygons intersecting with the input geometry.
    """
    bounds = geometry.bounds
    minx, miny, maxx, maxy = bounds

    # Create grid cells
    grid_cells = []
    x = minx
    while x < maxx:
        y = miny
        while y < maxy:
            grid_cell = box(x, y, x + cell_size, y + cell_size)
            grid_cells.append(grid_cell)
            y += cell_size
        x += cell_size

    # Split geometry with the grid and retain intersections
    sub_geometries = [geometry.intersection(cell) for cell in grid_cells if geometry.intersects(cell)]
    return sub_geometries

def split_date_range(start_date, end_date):
    """
    Split a large date range into smaller monthly date ranges.
    Args:
        start_date (str): Start date in 'yyyy-mm-dd' format.
        end_date (str): End date in 'yyyy-mm-dd' format.
    Returns:
        list of tuples: List of (start_date, end_date) strings for each month.
    """
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")

    ranges = []
    current = start

    while current < end:
        month_end = (current + timedelta(days=31)).replace(day=1) - timedelta(days=1)
        month_end = min(month_end, end)
        ranges.append((current.strftime("%Y-%m-%d"), month_end.strftime("%Y-%m-%d")))
        current = month_end + timedelta(days=1)

    return ranges


def is_month_complete(output_dir, date_range):
    """
    Check if a month is complete by verifying the presence of .complete files.
    Args:
        output_dir (str): The root output directory.
        date_range (tuple): Tuple of (start_date, end_date) for the month.
    Returns:
        bool: True if the month is complete, False otherwise.
    """
    start_date = datetime.strptime(date_range[0], "%Y-%m-%d")
    year_month = start_date.strftime("%Y-%m")
    month_dir = os.path.join(output_dir, year_month)

    if not os.path.exists(month_dir):
        return False

    # Check if all subdirectories have a .complete file
    for subdir in os.listdir(month_dir):
        subdir_path = os.path.join(month_dir, subdir)
        if os.path.isdir(subdir_path) and not os.path.exists(os.path.join(subdir_path, ".complete")):
            return False

    return True


def mark_month_complete(output_dir, date_range):
    """
    Mark a month as complete by adding .complete files in all subdirectories.
    Args:
        output_dir (str): The root output directory.
        date_range (tuple): Tuple of (start_date, end_date) for the month.
    """
    start_date = datetime.strptime(date_range[0], "%Y-%m-%d")
    year_month = start_date.strftime("%Y-%m")
    month_dir = os.path.join(output_dir, year_month)

    for subdir in os.listdir(month_dir):
        subdir_path = os.path.join(month_dir, subdir)
        if os.path.isdir(subdir_path):
            with open(os.path.join(subdir_path, ".complete"), "w") as f:
                f.write("")


def process_month(date_range, roi, output_dir, verbosity):
    """
    Process Sentinel-2 data for a specific month, splitting large geometries into smaller grids.
    Args:
        date_range (tuple): Tuple of (start_date, end_date) for the month.
        roi (str): Path to GeoJSON file or WKT text.
        output_dir (str): Directory to save the data.
        verbosity (str): Verbosity level.
    """
    logger.info(f"Processing month: {date_range[0]} to {date_range[1]}")

    # Load ROI geometry
    if roi.endswith(".geojson"):
        geojson = gpd.read_file(roi)
        geometry = shape(geojson.geometry[0])
    else:
        geometry = shape(roi)  # Parse WKT

    # Split the geometry into smaller grids
    sub_geometries = create_grid(geometry)

    for i, sub_geometry in enumerate(sub_geometries):
        logger.info(f"Processing subgeometry {i + 1}/{len(sub_geometries)}")

        sub_geometry_wkt = sub_geometry.wkt  # Convert to WKT for processing

        # Use the existing download_sentinel2_data logic
        download_sentinel2_data(date_range[0], date_range[1], sub_geometry_wkt, output_dir, verbosity)

    # Mark the month as complete if all sub-geometries are processed
    if is_month_complete(output_dir, date_range):
        mark_month_complete(output_dir, date_range)

def process_date_range(date_from, date_to, roi, output_dir, verbosity):
    """
    Process Sentinel-2 data over a split date range.
    Args:
        date_from (str): Start date in 'yyyy-mm-dd' format.
        date_to (str): End date in 'yyyy-mm-dd' format.
        roi (str): Path to GeoJSON file or WKT text.
        output_dir (str): Directory to save the data.
        verbosity (str): Verbosity level.
    """
    monthly_ranges = split_date_range(date_from, date_to)

    for date_range in monthly_ranges:
        process_month(date_range, roi, output_dir, verbosity)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Process Sentinel-2 data by splitting into monthly ranges.")
    parser.add_argument("--date-from", required=True, help="Start date in the format yyyy-mm-dd.")
    parser.add_argument("--date-to", required=True, help="End date in the format yyyy-mm-dd.")
    parser.add_argument("--roi", required=True, help="Region of interest as GeoJSON file or WKT text.")
    parser.add_argument("--output", required=True, help="Directory to save downloaded data.")
    parser.add_argument("--verbosity", choices=["default", "quiet", "verbose"], default="default",
                        help="Set verbosity level: default (progress bar), quiet (no output), verbose (detailed logs).")

    args = parser.parse_args()
    setup_logging(args.verbosity)
    process_date_range(args.date_from, args.date_to, args.roi, args.output, args.verbosity)
