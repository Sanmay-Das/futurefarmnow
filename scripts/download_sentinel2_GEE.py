import os
import logging
import json
from datetime import datetime, timedelta
from shapely.geometry import shape, box
from shapely.wkt import loads as parse_wkt
from threading import Thread
from queue import Queue
import ee
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google.oauth2.credentials import Credentials


def setup_logging(log_level):
    """
    Configure logging based on log level.
    Args:
        log_level (str): Log level ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL').
    """
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)
    logger.setLevel(numeric_level)

    if logger.hasHandlers():
        logger.handlers.clear()

    handler = logging.StreamHandler()
    handler.setLevel(numeric_level)

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    handler.setFormatter(formatter)

    logger.addHandler(handler)

def create_grid(geometry, cell_size=10.0):
    """
    Create a uniform grid of polygons over the bounding box of the input geometry.
    Args:
        geometry (shapely.geometry.Polygon): Input geometry.
        cell_size (float): Size of each cell in degrees.
    Returns:
        list: List of smaller polygons intersecting with the input geometry.
    """
    bounds = geometry.bounds
    minx, miny, maxx, maxy = bounds

    grid_cells = []
    x = minx
    while x < maxx:
        y = miny
        while y < maxy:
            grid_cell = box(x, y, x + cell_size, y + cell_size)
            grid_cells.append(grid_cell)
            y += cell_size
        x += cell_size

    sub_geometries = [geometry.intersection(cell) for cell in grid_cells if geometry.intersects(cell)]
    return sub_geometries

def split_date_range(start_date, end_date):
    """
    Split a large date range into smaller daily date ranges.
    Args:
        start_date (str): Start date in 'yyyy-mm-dd' format.
        end_date (str): End date in 'yyyy-mm-dd' format.
    Returns:
        list: List of date strings for each day.
    """
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    ranges = []

    while start <= end:
        ranges.append(start.strftime("%Y-%m-%d"))
        start += timedelta(days=1)

    return ranges

def download_from_drive(file_name, local_folder, service):
    """
    Download a file from Google Drive by name.
    Args:
        file_name (str): The name of the file to download.
        local_folder (str): The local folder to save the file.
        service: Google Drive API service instance.
    """
    results = service.files().list(q=f"name='{file_name}'", spaces='drive').execute()
    items = results.get('files', [])

    if not items:
        logger.warning(f"File {file_name} not found in Google Drive.")
        return

    file_id = items[0]['id']
    local_path = os.path.join(local_folder, file_name)
    with open(local_path, "wb") as f:
        request = service.files().get_media(fileId=file_id)
        downloader = MediaIoBaseDownload(f, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
            logger.info(f"Download {file_name}: {int(status.progress() * 100)}% complete.")

def download_ndvi(date_from, date_to, roi, output_dir):
    """
    Process NDVI data using Google Earth Engine and export to Google Drive, then download locally.

    Args:
        date_from (str): Start date in the format 'yyyy-mm-dd'.
        date_to (str): End date in the format 'yyyy-mm-dd'.
        roi (shapely.geometry.Polygon): The geographical area-of-interest to search in.
        output_dir (str): Directory to save downloaded data.
    """
    ee.Authenticate()  # Ensures the user is authenticated
    ee.Initialize(project='gen-lang-client-0690015714')    # Initializes the Earth Engine API

    #credentials = Credentials.from_authorized_user_file('credentials.json')
    #service = build('drive', 'v3', credentials=credentials)

    date_ranges = split_date_range(date_from, date_to)
    sub_geometries = create_grid(roi)

    work_queue = Queue()
    for date in date_ranges:
        for sub_geometry in sub_geometries:
            work_queue.put((date, sub_geometry))

    def producer():
        while not work_queue.empty():
            date, sub_geometry = work_queue.get()

            # Check if the day is complete
            complete_file_path = os.path.join(output_dir, date, ".complete")
            if os.path.exists(complete_file_path):
                logger.info(f"Skipping completed day: {date}")
                continue

            region = ee.Geometry.Polygon(list(sub_geometry.exterior.coords))
            start_date = ee.Date(date)
            end_date = start_date.advance(1, 'day')

            # Load Sentinel-2 data
            sentinel2 = ee.ImageCollection('COPERNICUS/S2') \
                .filterBounds(region) \
                .filterDate(start_date, end_date) \
                .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 10))

            if sentinel2.size().getInfo() == 0:
                logger.info(f"No data found for {date} in this region.")
                continue

            # Calculate NDVI
            ndvi = sentinel2.map(lambda image: image.normalizedDifference(['B8', 'B4']).rename('NDVI'))
            composite = ndvi.mean().clip(region)

            # Export the NDVI result to Google Drive
            task = ee.batch.Export.image.toDrive(
                image=composite,
                description=f"NDVI_{date}",
                folder=f"NDVI_Export_{date}",  # Specify Google Drive folder
                fileNamePrefix=f"ndvi_{date}",
                scale=10,
                region=region.getInfo()['coordinates'],
                fileFormat='GeoTIFF',
                maxPixels=1e13  # Increase this limit to handle larger exports
            )
            task.start()
            logger.info(f"Export task started for {date}: ndvi_{date}.tif in Google Drive folder 'EarthEngineExports'")

            # Wait for the task to complete
            task_status = task.status()
            while task_status['state'] in ['READY', 'RUNNING']:
                task_status = task.status()

            if task_status['state'] == 'COMPLETED':
                logger.info(f"Task completed for {date}. File available in Google Drive.")
                file_name = f"ndvi_{date}.tif"
                #download_from_drive(file_name, os.path.join(output_dir, date), service)
                # Mark the day as complete
                with open(complete_file_path, "w") as complete_file:
                    complete_file.write("Processed")
            else:
                logger.error(f"Task failed for {date}: {task_status['error_message']}.")

    def consumer():
        while not work_queue.empty():
            try:
                producer()
            except Exception as e:
                logger.error(f"Error occurred: {e}")

    threads = []
    for _ in range(4):  # Use 4 parallel threads
        thread = Thread(target=consumer)
        thread.start()
        threads.append(thread)

    for thread in threads:
        thread.join()

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Download NDVI data using Google Earth Engine.")
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

    if os.path.exists(args.roi) and args.roi.lower().endswith(".geojson"):
        with open(args.roi, "r") as geojson_file:
            geojson = json.load(geojson_file)
            geometry = geojson["features"][0]["geometry"]
            roi = shape(geometry)
    else:
        roi = parse_wkt(args.roi)

    os.makedirs(args.output, exist_ok=True)
    download_ndvi(args.date_from, args.date_to, roi, args.output)

if __name__ == "__main__":
    logger = logging.getLogger(__name__)
    main()
