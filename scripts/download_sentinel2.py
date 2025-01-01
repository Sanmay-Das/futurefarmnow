import os
import logging
from datetime import datetime, timedelta
from cdsetool.query import query_features
from cdsetool.download import download_feature
from cdsetool.credentials import Credentials
from cdsetool.monitor import StatusMonitor
from multiprocessing import Manager, cpu_count
import json
import zipfile
import rasterio
from rasterio.enums import Resampling
import numpy as np
from shapely.geometry import shape, box
from shapely.wkt import loads as parse_wkt
from queue import Queue
from threading import Thread

# Set up logging
logger = logging.getLogger(__name__)


def setup_logging(log_level):
    """
    Configure logging based on log level.
    Args:
        log_level (str): Log level ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL').
    """
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)
    logger.setLevel(numeric_level)

    # Clear existing handlers to avoid duplicate logs
    if logger.hasHandlers():
        logger.handlers.clear()

    # Create a file handler
    log_file = "sentinel2_downloader.log"
    handler = logging.StreamHandler()
    handler.setLevel(numeric_level)

    # Define a log format
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    handler.setFormatter(formatter)

    # Add the handler to the logger
    logger.addHandler(handler)

def create_grid(geometry, cell_size=10.0):
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
    Split a large date range into smaller daily date ranges.
    Args:
        start_date (str): Start date in 'yyyy-mm-dd' format.
        end_date (str): End date in 'yyyy-mm-dd' format.
    Returns:
        list of tuples: List of date strings for each day.
    """
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    ranges = []

    while start <= end:
        ranges.append(start.strftime("%Y-%m-%d"))
        start += timedelta(days=1)

    return ranges


def calculate_ndvi(nir, red):
    """
    Calculate NDVI from NIR and Red bands, normalize, and rescale.
    Args:
        nir (numpy.ndarray): NIR band array.
        red (numpy.ndarray): Red band array.
    Returns:
        numpy.ndarray: Rescaled NDVI array (8-bit).
    """
    nir = nir.astype(float)
    red = red.astype(float)

    # Avoid division by zero
    np.seterr(divide="ignore", invalid="ignore")

    numerator = nir - red
    denominator = nir + red

    # NDVI calculation with custom handling:
    # - If numerator is zero, set NDVI to zero.
    # - If any input is NaN, NDVI remains NaN.
    ndvi = np.where(
        numerator == 0, 0,  # If numerator is zero, NDVI is zero
        numerator / denominator  # Otherwise, compute NDVI as usual
    )

    # Rescale from [-1, 1] to [1, 255] (keep 0 for invalid pixels)
    ndvi_rescaled = np.round(1+(ndvi + 1.0) * 127)
    ndvi_rescaled[np.isnan(ndvi)] = 0
    ndvi_rescaled = ndvi_rescaled.astype(np.uint8)

    return ndvi_rescaled


def process_zip_to_ndvi(zip_path, output_dir):
    """
    Extract a ZIP archive and calculate NDVI from its bands, then save a GeoTIFF.
    Args:
        zip_path (str): Path to the ZIP archive.
        output_dir (str): Directory to save the processed GeoTIFF.
    Returns:
        str: Path to the processed GeoTIFF.
    """
    tile_id = os.path.basename(zip_path).split(".")[0]  # Use the tile ID from filename
    output_file = os.path.join(output_dir, f"{tile_id}.tif")
    logger.debug(f"Processing {zip_path} into {output_file}")

    # Extract the ZIP file
    with zipfile.ZipFile(zip_path, "r") as zip_ref:
        zip_ref.extractall(output_dir)

    # Locate the `.SAFE` directory (Sentinel-2 data format)
    safe_dir = next((os.path.join(output_dir, d) for d in os.listdir(output_dir) if d.endswith(".SAFE")), None)
    if not safe_dir:
        raise FileNotFoundError("Could not find the .SAFE directory in the extracted archive.")

    # Locate the GRANULE subdirectory
    granule_dir = next((os.path.join(safe_dir, "GRANULE", d) for d in os.listdir(os.path.join(safe_dir, "GRANULE"))), None)
    if not granule_dir:
        raise FileNotFoundError("Could not find the GRANULE directory in the .SAFE structure.")

    # Locate the 10m resolution folder
    r10m_dir = os.path.join(granule_dir, "IMG_DATA", "R10m")
    if not os.path.isdir(r10m_dir):
        raise FileNotFoundError("Could not find the R10m folder in the GRANULE directory.")

    nir_band = red_band = None
    for file in os.listdir(r10m_dir):
        if file.endswith("_B08_10m.jp2"):  # NIR band
            nir_band = os.path.join(r10m_dir, file)
        elif file.endswith("_B04_10m.jp2"):  # Red band
            red_band = os.path.join(r10m_dir, file)

    if not nir_band or not red_band:
        raise FileNotFoundError("Could not find NIR or Red bands in the R10m folder.")

    # Read bands with Rasterio
    with rasterio.open(nir_band) as src_nir, rasterio.open(red_band) as src_red:
        nir = src_nir.read(1, resampling=Resampling.bilinear)
        red = src_red.read(1, resampling=Resampling.bilinear)
        meta = src_nir.meta.copy()
        meta.update({"driver": "GTiff", "dtype": "uint8", "compress": "JPEG", "nodata": 0})

    # Calculate NDVI
    ndvi = calculate_ndvi(nir, red)

    # Save NDVI as a compressed GeoTIFF
    with rasterio.open(output_file, "w", **meta) as dst:
        dst.write(ndvi, 1)

    return output_file


def download_and_process(feature, credentials, output_dir):
    """
    Downloads and processes a single feature
    :param feature: the feature to download and process
    :param credentials: the login credentials to download the file
    :param output_dir: the directory to write the output file to
    :return Status of processing the file as one of {"skip", "success", "error"}
    """
    tile_id = feature["properties"]["title"].removesuffix('.SAFE')
    try:
        # Determine paths
        date = feature["properties"]["startDate"][:10]
        date_dir = os.path.join(output_dir, date)
        os.makedirs(date_dir, exist_ok=True)

        output_tif = os.path.join(date_dir, f"{tile_id}.tif")
        zip_path = os.path.join(date_dir, f"{tile_id}.zip")

        # Skip download if TIF already exists
        if os.path.exists(output_tif) or os.path.exists(zip_path):
            return "skip"

        # Download the ZIP archive
        monitor = StatusMonitor()
        zip_path = download_feature(feature, date_dir, {"credentials": credentials, "monitor": monitor})
        # Ensure full path is passed to `process_zip_to_ndvi`
        zip_path = os.path.join(date_dir, zip_path)
        ndvi_path = process_zip_to_ndvi(zip_path, date_dir)

        # Cleanup intermediate files
        os.remove(zip_path)  # Delete ZIP file
        safe_dir = next((os.path.join(date_dir, d) for d in os.listdir(date_dir) if d.endswith(".SAFE")), None)
        if safe_dir and os.path.isdir(safe_dir):
            import shutil
            shutil.rmtree(safe_dir)  # Delete extracted SAFE directory

        return "success"  # Indicating success
    except Exception as e:
        logger.error(f"Error processing feature {tile_id}: {e}")
        return "error"


def download_sentinel2_data(date_from, date_to, roi, output_dir):
    """
    Download Sentinel2 data for a given date range and region of interest.

    Args:
        date_from (str): Start date in the format 'yyyy-mm-dd'.
        date_to (str): End date in the format 'yyyy-mm-dd'.
        roi (geometry): The geographical area-of-interest to search in
        output_dir (str): Directory to save downloaded data.
    """
    max_retries = 3
    manager = Manager()
    all_files = {}  # A map from date to all files in that date
    processed_files = manager.list()  # Files that have been processed successfully
    skipped_files = manager.list()  # Files that have been skipped since they are already processed
    failed_files = manager.list()  # Files that have been failed while processing
    work_queue = Queue(maxsize=100)  # A queue of work tasks (feature, num_retries) tuples

    def producer():
        logger.debug("Starting search process...")

        # 1- Break down the geometric query using a uniform grid
        sub_geometries = create_grid(roi)
        # 2- Break down the date range day-by-day
        date_ranges = split_date_range(date_from, date_to)

        # 3- Loop over the date range, for each one loop over the sub-geometries
        for date in date_ranges:
            # If the day is marked as complete, skip this day
            complete_file_path = os.path.join(output_dir, date, ".complete")
            if os.path.exists(complete_file_path):
                logger.debug(f"Skipping completed day: {date}")
                continue

            for sub_geometry in sub_geometries:
                search_terms = {
                    "startDate": date,
                    "completionDate": (datetime.strptime(date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d"),
                    "processingLevel": "S2MSI2A",
                    "geometry": sub_geometry.wkt,
                    "cloudCover": "[0,10]",
                }

                # Run the search query and add the results the list of all_files and enqueue into the work_queue
                features = list(query_features("Sentinel2", search_terms))
                logger.debug(f"Found {len(features)} on [{search_terms['startDate']}, {search_terms['completionDate']}]"
                            f" with roi: '{sub_geometry.wkt}'")
                if features:
                    if date not in all_files:
                        all_files[date] = []

                    for feature in features:
                        if feature not in all_files[date]:
                            all_files[date].append(feature)  # Add new feature
                            work_queue.put((feature, max_retries))

            # Track the progress and mark complete days as complete
            for file in list(processed_files) + list(skipped_files):
                file_date = file["properties"]["startDate"][:10]
                if file_date in all_files:
                    all_files[file_date].remove(file)
                    if not all_files[file_date]:  # If all files for this date are processed or skipped
                        # Mark this day as complete
                        del all_files[file_date]
                        day_dir = os.path.join(output_dir, file_date)
                        with open(os.path.join(day_dir, ".complete"), "w") as complete_file:
                            complete_file.write("")

        # After done, raise a global flag that we're done
        work_queue.put(None)


    def consumer(i, credentials):
        logger.debug(f"Starting downloader #{i}")
        while True:
            # Retrieve one file from the work queue
            task = work_queue.get()
            if task is None:  # Work is already done
                logger.debug(f"Downloader #{i} is done")
                # Replace the None marker for other consumers
                work_queue.put(None)
                break

            feature, retries = task
            status = download_and_process(feature, credentials, output_dir)
            if status == "success":
                processed_files.append(feature)
            elif status == "error" and retries > 0:
                work_queue.put((feature, retries - 1))
            elif status == "error" and retries == 0:
                failed_files.append(feature)
            elif status == "skip":
                skipped_files.append(feature)
            else:
                logger.error(f"Unexpected status {status}")

            work_queue.task_done()  # Mark the task as done

    # Start one producer and # of consumers equal to number of processors - cpu_count()
    # Update: Due to API limits, we only use up-to four connections
    # See: https://documentation.dataspace.copernicus.eu/Quotas.html
    credentials = Credentials()
    producer_thread = Thread(target=producer)
    producer_thread.start()
    
    consumers = []
    for i in range(4):
        consumer_thread = Thread(target=consumer, args=[i, credentials])
        consumers.append(consumer_thread)
        consumer_thread.start()

    # Wait until all is done
    producer_thread.join()
    for consumer_thread in consumers:
        consumer_thread.join()

    # Return a final dictionary object with number of files processed, failed, and skipped
    return {
        "success": len(processed_files),
        "skipped": len(skipped_files),
        "failed": len(failed_files),
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Download Sentinel2 data for a given date range and ROI.")
    parser.add_argument("--date-from", required=True, help="Start date in the format yyyy-mm-dd.")
    parser.add_argument("--date-to", required=True, help="End date in the format yyyy-mm-dd.")
    parser.add_argument("--roi", required=True, help="Region of interest as GeoJSON file or WKT text.")
    parser.add_argument("--output", required=True, help="Directory to save downloaded data.")
    parser.add_argument("--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        default="INFO",
        help="Set logging level: DEBUG, INFO, WARNING, ERROR, CRITICAL. Default is INFO."
    )

    args = parser.parse_args()
    setup_logging(args.log_level)

    # Parse the region of interest parameter
    # Load region of interest (ROI)
    roi = args.roi
    if os.path.exists(roi) and roi.lower().endswith(".geojson"):
        # If GeoJSON file
        with open(roi, "r") as geojson_file:
            geojson = json.load(geojson_file)
            geometry = geojson["features"][0]["geometry"]
            roi = shape(geometry)
    else:
        # Assume input is already a WKT string
        roi = parse_wkt(roi)
    results = download_sentinel2_data(args.date_from, args.date_to, roi, args.output)
    logger.info(f"Summary: {results['success']} processed, {results['skipped']} skipped, {results['failed']} errors.")
