import os
import logging
from datetime import datetime
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
from tqdm import tqdm  # For progress bar

# Set up logging
logger = logging.getLogger(__name__)

def setup_logging(verbosity):
    """
    Configure logging based on verbosity level.
    Args:
        verbosity (str): Verbosity level ('quiet', 'default', 'verbose').
    """
    log_level = {
        "quiet": logging.ERROR,
        "default": logging.INFO,
        "verbose": logging.DEBUG,
    }.get(verbosity, logging.INFO)
    logging.basicConfig(level=log_level, format="%(asctime)s [%(levelname)s] %(message)s")


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


def download_sentinel2_data(date_from, date_to, roi_input, output_dir, verbosity):
    """
    Download Sentinel2 data for a given date range and region of interest.

    Args:
        date_from (str): Start date in the format 'yyyy-mm-dd'.
        date_to (str): End date in the format 'yyyy-mm-dd'.
        roi_input (str): Path to GeoJSON file or WKT text as a region of interest.
        output_dir (str): Directory to save downloaded data.
    """
    # Validate input dates
    try:
        datetime.strptime(date_from, "%Y-%m-%d")
        datetime.strptime(date_to, "%Y-%m-%d")
    except ValueError:
        logger.error("Invalid date format. Please use 'yyyy-mm-dd'.")
        return

    # Load region of interest (ROI)
    if os.path.exists(roi_input) and roi_input.lower().endswith(".geojson"):
        # If GeoJSON file
        with open(roi_input, "r") as geojson_file:
            geojson = json.load(geojson_file)
            geometry = geojson["features"][0]["geometry"]
            roi_wkt = shape(geometry).wkt  # Convert GeoJSON to WKT
    else:
        # Assume input is already a WKT string
        roi_wkt = roi_input

    # Prepare search terms
    search_terms = {
        "startDate": date_from,
        "completionDate": date_to,
        "processingLevel": "S2MSI2A",
        "geometry": roi_wkt,
    }

    # Query features
    logger.info("Querying Sentinel2 features...")
    features = list(query_features("Sentinel2", search_terms))

    if not features:
        logger.warning("No features found for the specified parameters.")
        return

    # Authenticate
    credentials = Credentials()
    results = {"success": 0, "skipped": 0, "errors": 0}

    # Download and process features in parallel
    logger.info(f"Starting download and processing of {len(features)} files...")

    def download_and_process(feature):
        try:
            # Determine paths
            date = feature["properties"]["startDate"][:10]
            date_dir = os.path.join(output_dir, date)
            os.makedirs(date_dir, exist_ok=True)

            tile_id = feature["properties"]["title"].removesuffix('.SAFE')
            output_tif = os.path.join(date_dir, f"{tile_id}.tif")
            zip_path = os.path.join(date_dir, f"{tile_id}.zip")

            # Skip download if TIF already exists
            if os.path.exists(output_tif) or os.path.exists(zip_path):
                results["skipped"] += 1
                return f"TIF already exists, skipping download: {output_tif}"

            # Download the ZIP archive
            monitor = StatusMonitor()
            zip_path = download_feature(feature, date_dir, {"credentials": credentials, "monitor": monitor})
            # Ensure full path is passed to `process_zip_to_ndvi`
            zip_path = os.path.join(date_dir, zip_path)
            ndvi_path = process_zip_to_ndvi(zip_path, date_dir)

            # Cleanup intermediate files
            os.remove(zip_path) # Delete ZIP file
            safe_dir = next((os.path.join(date_dir, d) for d in os.listdir(date_dir) if d.endswith(".SAFE")), None)
            if safe_dir and os.path.isdir(safe_dir):
                import shutil
                shutil.rmtree(safe_dir)  # Delete extracted SAFE directory

            results["success"] += 1
            return f"Feature {feature['id']} processed successfully: {ndvi_path}"
        except Exception as e:
            results["errors"] += 1
            return f"Error processing feature {feature['id']}: {e}"

    iterator = tqdm(features, desc="Processing files", unit="file") if verbosity == "default" else features
    num_workers = multiprocessing.cpu_count()
    with ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = {executor.submit(download_and_process, feature): feature for feature in iterator}
        for future in as_completed(futures):
            message = future.result()
            logger.info(message)

    logger.info(f"\nSummary: {results['success']} processed, {results['skipped']} skipped, {results['errors']} errors.")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Download Sentinel2 data for a given date range and ROI.")
    parser.add_argument("--date-from", required=True, help="Start date in the format yyyy-mm-dd.")
    parser.add_argument("--date-to", required=True, help="End date in the format yyyy-mm-dd.")
    parser.add_argument("--roi", required=True, help="Region of interest as GeoJSON file or WKT text.")
    parser.add_argument("--output", required=True, help="Directory to save downloaded data.")
    parser.add_argument("--verbosity", choices=["default", "quiet", "verbose"], default="default",
                        help="Set verbosity level: default (progress bar), quiet (no output), verbose (detailed logs).")

    args = parser.parse_args()
    setup_logging(args.verbosity)
    download_sentinel2_data(args.date_from, args.date_to, args.roi, args.output, args.verbosity)
