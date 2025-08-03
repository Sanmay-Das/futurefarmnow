#!/usr/bin/env python3
# ETmap blueprint with unified grid alignment system - Refactored with standard naming conventions

import os
import sys
import json
import uuid
import threading
import sqlite3
import numpy as np
import requests
import zipfile
import tempfile
from datetime import datetime, timedelta
from enum import Enum
from flask import Blueprint, request, jsonify, send_file, redirect, url_for
from shapely.geometry import shape, mapping
import rasterio
from rasterio.mask import mask
from rasterio.warp import reproject, Resampling, transform_geom, calculate_default_transform
from rasterio.windows import from_bounds
from affine import Affine
from pystac_client import Client
import planetary_computer
import xarray as xr
import pandas as pd
import rioxarray  # noqa: F401
import shutil
import pynldas2 as nldas
import py3dep
from pyproj import CRS, Transformer
from typing import Dict, List, Tuple, Optional
import glob

# Add scipy for soil data processing
try:
    from scipy.ndimage import gaussian_filter
except ImportError:
    print("Warning: scipy not available. Installing...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "scipy"])
    from scipy.ndimage import gaussian_filter

# Status enumeration
class JobStatus(Enum):
    QUEUED = "queued"
    COMPUTING_GRID = "computing_grid"
    SSURGO_STARTED = "ssurgo_started"
    SSURGO_DONE = "ssurgo_done"
    SSURGO_ERROR = "ssurgo_error"
    LANDSAT_STARTED = "landsat_started"
    LANDSAT_DONE = "landsat_done"
    LANDSAT_ERROR = "landsat_error"
    PRISM_STARTED = "prism_started"
    PRISM_DONE = "prism_done"
    PRISM_ERROR = "prism_error"
    NLDAS_STARTED = "nldas_started"
    NLDAS_DONE = "nldas_done"
    NLDAS_ERROR = "nldas_error"
    NLDAS_SERVICE_UNAVAILABLE = "nldas_service_unavailable"
    ELEVATION_STARTED = "elevation_started"
    ELEVATION_DONE = "elevation_done"
    ELEVATION_ERROR = "elevation_error"
    NLCD_STARTED = "nlcd_started"
    NLCD_DONE = "nlcd_done"
    NLCD_ERROR = "nlcd_error"
    SUCCESS = "success"
    FAILED = "failed"

# Blueprint for ET mapping endpoint
etmap_bp = Blueprint('etmap', __name__)

# Paths & DB
DB_PATH = os.path.join(os.path.dirname(__file__), 'etmap.db')
ETMAP_DATA_DIR = os.path.join(os.path.dirname(__file__), 'etmap_data')
RESULTS_DIR = os.path.join(os.path.dirname(__file__), 'results')
NLCD_FILE_PATH = os.path.join(
    os.path.dirname(__file__),
    'output', 'NLCD',
    'Annual_NLCD_LndCov_2024_CU_C1V1',
    'Annual_NLCD_LndCov_2024_CU_C1V1.tif'
)

# Ensure directories exist
os.makedirs(ETMAP_DATA_DIR, exist_ok=True)
os.makedirs(RESULTS_DIR, exist_ok=True)

# Initialize SQLite connection
connection = sqlite3.connect(DB_PATH, check_same_thread=False)
cursor = connection.cursor()

# Create table with standard naming conventions
cursor.execute('''
CREATE TABLE IF NOT EXISTS etmap_jobs (
    request_id TEXT PRIMARY KEY,
    date_from TEXT NOT NULL,
    date_to TEXT NOT NULL,
    geometry TEXT NOT NULL,
    status TEXT NOT NULL,
    request_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT,
    error_message TEXT
)
''')
connection.commit()

class UnifiedGridManager:
    """
    Manages unified grid computation and alignment for all datasets.
    Similar to the Scala Raster_metadata and AOI_metadata functions.
    """
    
    def __init__(self, target_crs: str = 'EPSG:4326'):
        self.target_crs = target_crs
        self.grid_metadata = None
        
    def compute_unified_grid(self, aoi_geometry, sample_datasets: List[str], aoi_crs: str = 'EPSG:4326') -> Dict:
        """
        Compute unified grid covering AOI + all input datasets.
        Equivalent to Scala's Raster_metadata function.
        
        Args:
            aoi_geometry: Shapely geometry for area of interest
            sample_datasets: List of file paths to sample datasets for extent calculation
            aoi_crs: CRS of the AOI geometry (default EPSG:4326)
            
        Returns:
            Dictionary with unified grid metadata
        """
        print("Computing unified grid metadata...")
        
        # Initialize bounds tracking
        min_x = float('inf')
        max_x = float('-inf')
        min_y = float('inf')
        max_y = float('-inf')
        min_cell_x = float('inf')
        min_cell_y = float('inf')
        
        # Handle AOI bounds - assume AOI is in EPSG:4326 unless specified otherwise
        if aoi_crs != self.target_crs:
            try:
                aoi_bounds_geometry = transform_geom(aoi_crs, self.target_crs, mapping(aoi_geometry))
                aoi_bounds = shape(aoi_bounds_geometry).bounds
            except Exception as e:
                print(f"Warning: Could not transform AOI geometry: {e}")
                aoi_bounds = aoi_geometry.bounds
        else:
            aoi_bounds = aoi_geometry.bounds
            
        # Update bounds with AOI
        min_x = min(min_x, aoi_bounds[0])
        min_y = min(min_y, aoi_bounds[1])
        max_x = max(max_x, aoi_bounds[2])
        max_y = max(max_y, aoi_bounds[3])
        
        # Analyze sample datasets to determine optimal resolution and extent
        valid_datasets_count = 0
        for dataset_path in sample_datasets:
            if os.path.exists(dataset_path):
                try:
                    with rasterio.open(dataset_path) as source:
                        # Transform dataset bounds to target CRS
                        if source.crs != CRS.from_string(self.target_crs):
                            dst_transform, dst_width, dst_height = calculate_default_transform(
                                source.crs, self.target_crs, source.width, source.height, *source.bounds
                            )
                            # Calculate bounds in target CRS
                            left, bottom, right, top = rasterio.transform.array_bounds(
                                dst_height, dst_width, dst_transform
                            )
                        else:
                            left, bottom, right, top = source.bounds
                            dst_transform = source.transform
                            
                        # Update global bounds
                        min_x = min(min_x, left)
                        min_y = min(min_y, bottom)
                        max_x = max(max_x, right)
                        max_y = max(max_y, top)
                        
                        # Track minimum cell size (highest resolution)
                        cell_x = abs(dst_transform.a)
                        cell_y = abs(dst_transform.e)
                        min_cell_x = min(min_cell_x, cell_x)
                        min_cell_y = min(min_cell_y, cell_y)
                        
                        valid_datasets_count += 1
                        
                except Exception as e:
                    print(f"Warning: Could not read {dataset_path}: {e}")
                    continue
        
        if valid_datasets_count == 0:
            # Fallback to reasonable defaults if no sample data
            min_cell_x = min_cell_y = 0.01  # ~1km at equator
            
        # Calculate grid dimensions
        grid_width = int(np.ceil((max_x - min_x) / min_cell_x))
        grid_height = int(np.ceil((max_y - min_y) / min_cell_y))
        
        # Create affine transform for unified grid
        grid_transform = Affine(min_cell_x, 0.0, min_x, 0.0, -min_cell_y, max_y)
        
        self.grid_metadata = {
            'crs': self.target_crs,
            'transform': grid_transform,
            'width': grid_width,
            'height': grid_height,
            'bounds': (min_x, min_y, max_x, max_y),
            'cell_size': (min_cell_x, min_cell_y),
            'valid_datasets_count': valid_datasets_count
        }
        
        print(f"Unified grid: {grid_width}x{grid_height} pixels, "
              f"cell size: {min_cell_x:.6f}x{min_cell_y:.6f}, "
              f"extent: ({min_x:.6f}, {min_y:.6f}, {max_x:.6f}, {max_y:.6f})")
        
        return self.grid_metadata
    
    def clip_to_aoi(self, aoi_geometry, aoi_crs: str = 'EPSG:4326') -> Dict:
        """
        Refine grid to AOI bounds only.
        Equivalent to Scala's AOI_metadata function.
        
        Args:
            aoi_geometry: Shapely geometry for area of interest
            aoi_crs: CRS of the AOI geometry (default EPSG:4326)
        """
        if not self.grid_metadata:
            raise ValueError("Must compute unified grid first")
            
        # Get AOI bounds in target CRS
        if aoi_crs != self.target_crs:
            try:
                aoi_bounds_geometry = transform_geom(aoi_crs, self.target_crs, mapping(aoi_geometry))
                aoi_bounds = shape(aoi_bounds_geometry).bounds
            except Exception as e:
                print(f"Warning: Could not transform AOI geometry: {e}")
                aoi_bounds = aoi_geometry.bounds
        else:
            aoi_bounds = aoi_geometry.bounds
            
        # Intersect with global bounds
        global_bounds = self.grid_metadata['bounds']
        clipped_bounds = (
            max(aoi_bounds[0], global_bounds[0]),  # min_x
            max(aoi_bounds[1], global_bounds[1]),  # min_y  
            min(aoi_bounds[2], global_bounds[2]),  # max_x
            min(aoi_bounds[3], global_bounds[3])   # max_y
        )
        
        # Calculate new grid dimensions for clipped area
        cell_x, cell_y = self.grid_metadata['cell_size']
        clipped_width = int(np.ceil((clipped_bounds[2] - clipped_bounds[0]) / cell_x))
        clipped_height = int(np.ceil((clipped_bounds[3] - clipped_bounds[1]) / cell_y))
        
        # Create new transform for clipped grid
        clipped_transform = Affine(cell_x, 0.0, clipped_bounds[0], 
                                 0.0, -cell_y, clipped_bounds[3])
        
        aoi_metadata = {
            'crs': self.target_crs,
            'transform': clipped_transform,
            'width': clipped_width,
            'height': clipped_height,
            'bounds': clipped_bounds,
            'cell_size': (cell_x, cell_y)
        }
        
        print(f"AOI grid: {clipped_width}x{clipped_height} pixels, "
              f"bounds: ({clipped_bounds[0]:.6f}, {clipped_bounds[1]:.6f}, "
              f"{clipped_bounds[2]:.6f}, {clipped_bounds[3]:.6f})")
        
        return aoi_metadata
    
    def align_raster_to_grid(self, source_path: str, output_path: str, 
                           grid_metadata: Dict, resampling_method=Resampling.bilinear) -> bool:
        """
        Align a single raster to the unified grid.
        Equivalent to Scala's RasterOperationsFocal.reshapeNN.
        """
        try:
            with rasterio.open(source_path) as source:
                # Create output array
                output_array = np.empty((source.count, grid_metadata['height'], grid_metadata['width']), 
                                      dtype=source.dtypes[0])
                
                # Reproject to unified grid
                reproject(
                    source=rasterio.band(source, list(range(1, source.count + 1))),
                    destination=output_array,
                    src_transform=source.transform,
                    src_crs=source.crs,
                    dst_transform=grid_metadata['transform'],
                    dst_crs=grid_metadata['crs'],
                    resampling=resampling_method
                )
                
                # Write aligned raster
                profile = {
                    'driver': 'GTiff',
                    'dtype': source.dtypes[0],
                    'nodata': source.nodata,
                    'width': grid_metadata['width'],
                    'height': grid_metadata['height'],
                    'count': source.count,
                    'crs': grid_metadata['crs'],
                    'transform': grid_metadata['transform'],
                    'compress': 'lzw'
                }
                
                os.makedirs(os.path.dirname(output_path), exist_ok=True)
                with rasterio.open(output_path, 'w', **profile) as destination:
                    destination.write(output_array)
                    
                return True
                
        except Exception as e:
            print(f"Error aligning {source_path}: {e}")
            return False

# Helper functions for database operations
def update_job_status(request_id: str, status: JobStatus, error_message: str = None):
    """Update job status with timestamp"""
    updated_at = datetime.utcnow().isoformat()
    cursor.execute(
        'UPDATE etmap_jobs SET status=?, updated_at=?, error_message=? WHERE request_id=?', 
        (status.value, updated_at, error_message, request_id)
    )
    connection.commit()

def collect_sample_datasets(request_id: str) -> List[str]:
    """Collect sample dataset paths for grid computation"""
    sample_paths = []
    base_directory = os.path.join(ETMAP_DATA_DIR, request_id)
    
    # Add NLCD if exists
    if os.path.exists(NLCD_FILE_PATH):
        sample_paths.append(NLCD_FILE_PATH)
    
    # Look for any existing Landsat files
    landsat_directory = os.path.join(base_directory, 'landsat')
    if os.path.exists(landsat_directory):
        landsat_files = glob.glob(os.path.join(landsat_directory, '*.tif'))
        sample_paths.extend(landsat_files[:3])  # Sample a few files
    
    # Look for existing SSURGO files
    ssurgo_directory = os.path.join(base_directory, 'ssurgo')
    if os.path.exists(ssurgo_directory):
        ssurgo_files = glob.glob(os.path.join(ssurgo_directory, '*.tif'))
        sample_paths.extend(ssurgo_files[:3])  # Sample a few files
        
    return sample_paths

# ------------------- SSURGO Data Collection -------------------
def process_ssurgo_data(request_id: str, date_from: str, date_to: str, geometry_json: str, 
                       grid_manager: UnifiedGridManager, grid_metadata: Dict):
    """
    SSURGO soil data collection using multiple sources
    Fetches soil properties like AWC (Available Water Capacity) and Field Capacity
    """
    update_job_status(request_id, JobStatus.SSURGO_STARTED)
    output_directory = os.path.join(ETMAP_DATA_DIR, request_id, 'ssurgo')
    aligned_directory = os.path.join(output_directory, 'aligned')
    os.makedirs(output_directory, exist_ok=True)
    os.makedirs(aligned_directory, exist_ok=True)
    
    area_of_interest = shape(json.loads(geometry_json))
    
    print("Fetching SSURGO soil data...")
    
    try:
        # Try multiple approaches for SSURGO data
        ssurgo_data_files = None
        
        # Approach 1: Try Microsoft Planetary Computer
        print("Attempting Microsoft Planetary Computer...")
        ssurgo_data_files = fetch_ssurgo_from_planetary_computer(area_of_interest, output_directory)
        
        # Approach 2: Create realistic placeholder data for demonstration
        if not ssurgo_data_files:
            print("Creating realistic SSURGO placeholder data...")
            ssurgo_data_files = create_ssurgo_placeholder_data(area_of_interest, output_directory)
        
        if ssurgo_data_files:
            # Align each soil property to unified grid
            for property_name, file_path in ssurgo_data_files.items():
                if os.path.exists(file_path):
                    aligned_path = os.path.join(aligned_directory, f"ssurgo_{property_name}_aligned.tif")
                    
                    # Use nearest neighbor for categorical soil data, bilinear for continuous
                    categorical_properties = ['hydric', 'drainage', 'hydgrp']
                    resampling_method = (Resampling.nearest if property_name in categorical_properties 
                                       else Resampling.bilinear)
                    
                    if grid_manager.align_raster_to_grid(file_path, aligned_path, grid_metadata, resampling_method):
                        print(f"✓ Successfully aligned SSURGO {property_name}")
                        
                        # Verify alignment
                        with rasterio.open(aligned_path) as source:
                            data = source.read(1)
                            nodata_value = source.nodata if source.nodata is not None else -9999
                            valid_mask = ~np.isnan(data) & (data != nodata_value)
                            valid_pixels_count = np.sum(valid_mask)
                            print(f"  Valid pixels: {valid_pixels_count} / {data.size}")
                    else:
                        print(f"✗ Failed to align SSURGO {property_name}")
                else:
                    print(f"Warning: SSURGO file not found: {file_path}")
        
        update_job_status(request_id, JobStatus.SSURGO_DONE)
        
    except Exception as e:
        print(f"Error in SSURGO processing: {e}")
        import traceback
        traceback.print_exc()
        update_job_status(request_id, JobStatus.SSURGO_ERROR, str(e))

def fetch_ssurgo_from_planetary_computer(area_of_interest, output_directory: str) -> Optional[Dict[str, str]]:
    """
    Attempt to fetch SSURGO data from Microsoft Planetary Computer
    """
    try:
        catalog = Client.open(
            "https://planetarycomputer.microsoft.com/api/stac/v1",
            modifier=planetary_computer.sign_inplace
        )
        
        # Get all available collections
        all_collections = [collection.id for collection in catalog.get_collections()]
        
        # Look for soil-related collections
        soil_keywords = ['soil', 'ssurgo', 'gssurgo', 'usda', 'nrcs']
        soil_collections = [collection for collection in all_collections 
                          if any(keyword in collection.lower() for keyword in soil_keywords)]
        
        if soil_collections:
            print(f"Found potential soil collections: {soil_collections}")
            
            # Try to search the most promising collection
            for collection_id in soil_collections:
                try:
                    search_results = catalog.search(
                        collections=[collection_id],
                        intersects=mapping(area_of_interest),
                        limit=10
                    )
                    
                    items = list(search_results.item_collection())
                    if items:
                        return process_planetary_computer_soil_items(items, area_of_interest, output_directory)
                        
                except Exception as e:
                    print(f"Error searching collection {collection_id}: {e}")
                    continue
        
        return None
            
    except Exception as e:
        print(f"Error accessing Planetary Computer for SSURGO: {e}")
        return None

def process_planetary_computer_soil_items(items, area_of_interest, output_directory: str) -> Dict[str, str]:
    """Process soil data items from Planetary Computer"""
    fetched_files = {}
    
    for item in items[:3]:  # Limit to first 3 items
        # Look for soil property assets
        soil_assets = {}
        for asset_key, asset in item.assets.items():
            if any(property_name in asset_key.lower() for property_name in ['awc', 'fc', 'clay', 'sand', 'ksat', 'bd']):
                soil_assets[asset_key] = asset
        
        if not soil_assets:
            continue
            
        # Process soil assets
        for asset_key, asset in soil_assets.items():
            try:
                href = planetary_computer.sign_url(asset.href)
                output_path = os.path.join(output_directory, f"pc_soil_{asset_key}.tif")
                
                # Download and clip to AOI
                with rasterio.open(href) as source:
                    if source.crs and source.crs.to_string() != 'EPSG:4326':
                        aoi_transformed = transform_geom('EPSG:4326', source.crs, mapping(area_of_interest))
                        clipped_data, clipped_transform = mask(source, [aoi_transformed], crop=True)
                    else:
                        clipped_data, clipped_transform = mask(source, [mapping(area_of_interest)], crop=True)
                    
                    # Save clipped raster
                    profile = source.profile.copy()
                    profile.update({
                        'transform': clipped_transform,
                        'width': clipped_data.shape[2] if len(clipped_data.shape) > 2 else clipped_data.shape[1],
                        'height': clipped_data.shape[1] if len(clipped_data.shape) > 2 else clipped_data.shape[0]
                    })
                    
                    with rasterio.open(output_path, 'w', **profile) as destination:
                        destination.write(clipped_data)
                    
                    fetched_files[asset_key] = output_path
                    
            except Exception as e:
                print(f"Error processing soil asset {asset_key}: {e}")
                continue
    
    return fetched_files if fetched_files else None

def create_ssurgo_placeholder_data(area_of_interest, output_directory: str) -> Dict[str, str]:
    print("Creating realistic SSURGO placeholder data...")
    
    # Key soil properties for ET modeling (matching Scala code)
    soil_properties = {
        'awc': {'range': (0.05, 0.25), 'units': 'cm/cm', 'description': 'Available Water Capacity'},
        'fc': {'range': (0.15, 0.45), 'units': 'cm3/cm3', 'description': 'Field Capacity'},
        'pwp': {'range': (0.05, 0.25), 'units': 'cm3/cm3', 'description': 'Permanent Wilting Point'},
        'ksat': {'range': (0.1, 100.0), 'units': 'μm/s', 'description': 'Saturated Hydraulic Conductivity'},
        'bd': {'range': (1.0, 1.8), 'units': 'g/cm3', 'description': 'Bulk Density'},
        'clay': {'range': (5.0, 60.0), 'units': '%', 'description': 'Clay Percentage'},
        'sand': {'range': (10.0, 85.0), 'units': '%', 'description': 'Sand Percentage'},
        'om': {'range': (0.5, 8.0), 'units': '%', 'description': 'Organic Matter'}
    }
    
    created_files = {}
    
    for property_name, property_info in soil_properties.items():
        output_path = os.path.join(output_directory, f"ssurgo_{property_name}.tif")
        
        if create_realistic_soil_raster(area_of_interest, output_path, property_name, property_info):
            created_files[property_name] = output_path
            print(f"✓ Created {property_name}: {property_info['description']}")
    
    return created_files

def create_realistic_soil_raster(area_of_interest, output_path: str, property_name: str, property_info: Dict) -> bool:
    """
    Create a realistic soil property raster with spatial correlation
    """
    try:
        # Get AOI bounds
        bounds = area_of_interest.bounds
        
        # Create reasonable resolution (30m to match Landsat)
        pixel_size = 0.0002778  # ~30m at equator in degrees
        raster_width = int((bounds[2] - bounds[0]) / pixel_size)
        raster_height = int((bounds[3] - bounds[1]) / pixel_size)
        
        # Limit size for demonstration
        raster_width = min(raster_width, 1000)
        raster_height = min(raster_height, 1000)
        
        # Create transform
        raster_transform = Affine(pixel_size, 0.0, bounds[0], 0.0, -pixel_size, bounds[3])
        
        # Generate realistic spatially correlated soil data
        min_value, max_value = property_info['range']
        
        # Create base random field with spatial correlation
        np.random.seed(42 + hash(property_name) % 1000)
        base_field = np.random.random((raster_height, raster_width))
        
        # Apply Gaussian smoothing for spatial correlation
        smoothed_field = gaussian_filter(base_field, sigma=3.0)
        
        # Add landscape-scale patterns
        y_coordinates, x_coordinates = np.mgrid[0:raster_height, 0:raster_width]
        y_normalized = y_coordinates / raster_height
        x_normalized = x_coordinates / raster_width
        
        # Add terrain-based patterns
        if property_name in ['clay', 'fc', 'pwp']:
            # Clay and water holding capacity higher in valleys/depressions
            terrain_effect = 0.3 * (np.sin(y_normalized * 2 * np.pi) * 0.5 + 
                                   np.cos(x_normalized * 1.5 * np.pi) * 0.3)
            smoothed_field += terrain_effect
            
        elif property_name in ['sand', 'ksat']:
            # Sand and permeability higher on ridges/well-drained areas
            terrain_effect = 0.3 * (np.cos(y_normalized * 2 * np.pi) * 0.5 + 
                                   np.sin(x_normalized * 1.5 * np.pi) * 0.3)
            smoothed_field += terrain_effect
        
        # Normalize and scale to property range
        smoothed_field = (smoothed_field - smoothed_field.min()) / (smoothed_field.max() - smoothed_field.min())
        soil_data = min_value + (max_value - min_value) * smoothed_field
        
        # Create profile for output
        profile = {
            'driver': 'GTiff',
            'dtype': 'float32',
            'nodata': -9999.0,
            'width': raster_width,
            'height': raster_height,
            'count': 1,
            'crs': 'EPSG:4326',
            'transform': raster_transform,
            'compress': 'lzw'
        }
        
        # Write the raster
        with rasterio.open(output_path, 'w', **profile) as destination:
            destination.write(soil_data.astype(np.float32), 1)
        
        return True
        
    except Exception as e:
        print(f"Error creating soil raster for {property_name}: {e}")
        return False

# ------------------- Landsat with Alignment -------------------
def process_landsat_data(request_id: str, date_from: str, date_to: str, geometry_json: str, 
                        grid_manager: UnifiedGridManager, grid_metadata: Dict):
    update_job_status(request_id, JobStatus.LANDSAT_STARTED)
    output_directory = os.path.join(ETMAP_DATA_DIR, request_id, 'landsat')
    aligned_directory = os.path.join(output_directory, 'aligned')
    os.makedirs(output_directory, exist_ok=True)
    os.makedirs(aligned_directory, exist_ok=True)
    
    area_of_interest = shape(json.loads(geometry_json))

    catalog = Client.open(
        "https://planetarycomputer.microsoft.com/api/stac/v1",
        modifier=planetary_computer.sign_inplace
    )
    search_results = catalog.search(
        collections=["landsat-c2-l2"],
        intersects=mapping(area_of_interest),
        datetime=f"{date_from}/{date_to}"
    )
    items = list(search_results.item_collection())

    # Process and align each Landsat scene
    for item in items:
        date_string = item.datetime.date().isoformat()
        
        # Process multiple bands (Red, NIR for NDVI)
        band_mapping = {'red': 'red', 'nir08': 'nir'}
        band_arrays = {}
        
        for band_key, band_name in band_mapping.items():
            if band_key in item.assets:
                href = planetary_computer.sign_url(item.assets[band_key].href)
                try:
                    with rasterio.open(href) as source:
                        # Initial clip to AOI
                        polygon = transform_geom('EPSG:4326', source.crs, mapping(area_of_interest))
                        clipped_data, clipped_transform = mask(source, [polygon], crop=True)
                        array_data = clipped_data[0]
                        
                        # Save temporary file
                        temp_path = os.path.join(output_directory, f"temp_{date_string}_{band_name}.tif")
                        profile = source.profile.copy()
                        profile.update({
                            'transform': clipped_transform,
                            'width': array_data.shape[1],
                            'height': array_data.shape[0],
                            'count': 1
                        })
                        
                        with rasterio.open(temp_path, 'w', **profile) as destination:
                            destination.write(array_data, 1)
                        
                        # Align to unified grid
                        aligned_path = os.path.join(aligned_directory, f"{date_string}_{band_name}_aligned.tif")
                        if grid_manager.align_raster_to_grid(temp_path, aligned_path, grid_metadata):
                            # Load aligned data for processing
                            with rasterio.open(aligned_path) as aligned_source:
                                band_arrays[band_name] = aligned_source.read(1)
                        
                        # Clean up temp file
                        os.remove(temp_path)
                        
                except Exception as e:
                    print(f"Error processing Landsat {band_name} {date_string}: {e}", file=sys.stderr)
        
        # Calculate NDVI if both bands available
        if 'red' in band_arrays and 'nir' in band_arrays:
            red_band = band_arrays['red'].astype(np.float32)
            nir_band = band_arrays['nir'].astype(np.float32)
            
            # Handle nodata values and invalid pixels
            nodata_value = -9999.0
            
            # Create masks for valid data
            red_valid = np.isfinite(red_band) & (red_band > 0) & (red_band != nodata_value)
            nir_valid = np.isfinite(nir_band) & (nir_band > 0) & (nir_band != nodata_value)
            valid_mask = red_valid & nir_valid
            
            # Initialize NDVI array with nodata
            ndvi_array = np.full(red_band.shape, nodata_value, dtype=np.float32)
            
            # Calculate NDVI only for valid pixels
            if np.any(valid_mask):
                red_clean = red_band[valid_mask]
                nir_clean = nir_band[valid_mask]
                denominator = nir_clean + red_clean
                
                # Additional check for non-zero denominator
                denominator_valid = denominator > 0
                
                if np.any(denominator_valid):
                    # Calculate NDVI with proper bounds checking
                    ndvi_values = (nir_clean[denominator_valid] - red_clean[denominator_valid]) / denominator[denominator_valid]
                    
                    # Clip NDVI to valid range [-1, 1]
                    ndvi_values = np.clip(ndvi_values, -1.0, 1.0)
                    
                    # Create a temporary array to hold the valid NDVI values
                    temp_indices = np.where(valid_mask)
                    valid_indices = np.where(denominator_valid)
                    
                    # Map back to original array positions
                    final_row_indices = temp_indices[0][valid_indices[0]]
                    final_col_indices = temp_indices[1][valid_indices[0]]
                    
                    ndvi_array[final_row_indices, final_col_indices] = ndvi_values
            
            # Save NDVI
            ndvi_path = os.path.join(aligned_directory, f"{date_string}_ndvi_aligned.tif")
            profile = {
                'driver': 'GTiff',
                'dtype': 'float32',
                'nodata': nodata_value,
                'width': grid_metadata['width'],
                'height': grid_metadata['height'],
                'count': 1,
                'crs': grid_metadata['crs'],
                'transform': grid_metadata['transform'],
                'compress': 'lzw'
            }
            
            with rasterio.open(ndvi_path, 'w', **profile) as destination:
                destination.write(ndvi_array, 1)
                
            print(f"NDVI calculated for {date_string}: "
                  f"{np.sum(valid_mask)} valid pixels out of {ndvi_array.size} total")
    
    update_job_status(request_id, JobStatus.LANDSAT_DONE)

# ------------------- PRISM with Alignment -------------------
def process_prism_data(request_id: str, date_from: str, date_to: str, geometry_json: str, 
                      grid_manager: UnifiedGridManager, grid_metadata: Dict):
    update_job_status(request_id, JobStatus.PRISM_STARTED)
    output_directory = os.path.join(ETMAP_DATA_DIR, request_id, 'prism')
    aligned_directory = os.path.join(output_directory, 'aligned')
    os.makedirs(output_directory, exist_ok=True)
    os.makedirs(aligned_directory, exist_ok=True)
    
    area_of_interest = shape(json.loads(geometry_json))
    PRISM_VARIABLES = ["ppt", "tmin", "tmax", "tmean", "tdmean", "vpdmin", "vpdmax"]

    current_date = datetime.fromisoformat(date_from)
    end_date = datetime.fromisoformat(date_to)
    
    while current_date <= end_date:
        year_month_day = current_date.strftime('%Y%m%d')
        month_day = current_date.strftime('%m-%d')
        day_directory = os.path.join(aligned_directory, month_day)
        os.makedirs(day_directory, exist_ok=True)
        
        aligned_files = []
        
        for variable in PRISM_VARIABLES:
            url = f"https://services.nacse.org/prism/data/get/us/4km/{variable}/{year_month_day}"
            try:
                response = requests.get(url, stream=True)
                response.raise_for_status()
                content = response.content
                content_type = response.headers.get('Content-Type', '')
                
                with tempfile.TemporaryDirectory() as temp_directory:
                    temp_file = os.path.join(temp_directory, f"{variable}_{year_month_day}")
                    with open(temp_file, 'wb') as file:
                        file.write(content)
                    
                    if 'zip' in content_type or content.startswith(b'PK'):
                        with zipfile.ZipFile(temp_file) as zip_file:
                            zip_file.extractall(temp_directory)
                        tif_files = [f for f in os.listdir(temp_directory) if f.lower().endswith('.tif')]
                        if tif_files:
                            temp_file = os.path.join(temp_directory, tif_files[0])
                    elif not temp_file.lower().endswith('.tif'):
                        new_temp_file = temp_file + '.tif'
                        os.rename(temp_file, new_temp_file)
                        temp_file = new_temp_file
                    
                    # Align to unified grid
                    aligned_path = os.path.join(day_directory, f"prism_{variable}_{month_day}_aligned.tif")
                    if grid_manager.align_raster_to_grid(temp_file, aligned_path, grid_metadata):
                        aligned_files.append(aligned_path)
                        
            except Exception as e:
                print(f"Error PRISM {variable} {year_month_day}: {e}", file=sys.stderr)
        
        current_date += timedelta(days=1)
    
    update_job_status(request_id, JobStatus.PRISM_DONE)

# ------------------- Enhanced NLDAS with Alignment -------------------
def process_nldas_data(request_id: str, date_from: str, date_to: str, geometry_json: str, 
                      grid_manager: UnifiedGridManager, grid_metadata: Dict):
    update_job_status(request_id, JobStatus.NLDAS_STARTED)
    output_directory = os.path.join(ETMAP_DATA_DIR, request_id, 'nldas')
    aligned_directory = os.path.join(output_directory, 'aligned')
    os.makedirs(output_directory, exist_ok=True)
    os.makedirs(aligned_directory, exist_ok=True)
    
    area_of_interest = shape(json.loads(geometry_json))
    
    # Retry logic for NLDAS service outages
    max_retries = 3
    retry_delay = 30  # seconds
    
    for attempt in range(max_retries):
        try:
            print(f"Fetching NLDAS data (attempt {attempt + 1}/{max_retries})...")
            dataset = nldas.get_bygeom(area_of_interest, date_from, date_to).rio.write_crs('EPSG:4326', inplace=False)
            print(f"NLDAS dataset shape: {dataset.dims}")
            print(f"NLDAS variables: {list(dataset.data_vars)}")
            break  # Success, exit retry loop
            
        except Exception as e:
            error_message = str(e).lower()
            if 'service unavailable' in error_message or '503' in error_message or 'timeout' in error_message:
                if attempt < max_retries - 1:
                    print(f"NLDAS service unavailable (attempt {attempt + 1}). Retrying in {retry_delay} seconds...")
                    import time
                    time.sleep(retry_delay)
                    retry_delay *= 1.5  # Exponential backoff
                    continue
                else:
                    print("NLDAS service unavailable after all retries. Skipping NLDAS processing.")
                    update_job_status(request_id, JobStatus.NLDAS_SERVICE_UNAVAILABLE)
                    return
            else:
                # Different error, don't retry
                print(f"NLDAS error (non-retryable): {e}")
                update_job_status(request_id, JobStatus.NLDAS_ERROR, str(e))
                return
    
    try:
        for variable in dataset.data_vars:
            variable_directory = os.path.join(aligned_directory, variable)
            os.makedirs(variable_directory, exist_ok=True)
            print(f"Processing NLDAS variable: {variable}")
            
            for i, time_step in enumerate(dataset.time.values):
                try:
                    data_array = dataset[variable].sel(time=time_step)
                    timestamp = np.datetime_as_string(time_step, unit='h').replace('T', '').replace(':', '')
                    
                    # Check if data array has valid values
                    if data_array.isnull().all():
                        print(f"Warning: All NaN values for {variable} at {timestamp}, skipping...")
                        continue
                    
                    # Create temporary netCDF file (more reliable than direct TIF conversion)
                    temp_netcdf = os.path.join(output_directory, f"temp_{variable}_{timestamp}.nc")
                    
                    # Ensure data has proper attributes
                    data_array.attrs.update({
                        'long_name': f'NLDAS {variable}',
                        '_FillValue': -9999.0,
                        'grid_mapping': 'crs'
                    })
                    
                    # Add CRS information
                    data_array = data_array.rio.write_crs('EPSG:4326', inplace=True)
                    
                    # Save to temporary netCDF
                    data_array.to_netcdf(temp_netcdf, mode='w')
                    
                    # Convert to GeoTIFF using rasterio for better control
                    temp_tif = os.path.join(output_directory, f"temp_{variable}_{timestamp}.tif")
                    
                    with xr.open_dataset(temp_netcdf) as temp_dataset:
                        temp_data_array = temp_dataset[variable]
                        
                        # Convert to numpy array and handle NaN values
                        data_array_values = temp_data_array.values
                        data_array_values = np.where(np.isnan(data_array_values), -9999, data_array_values)
                        
                        # Get spatial coordinates
                        if 'x' in temp_data_array.dims and 'y' in temp_data_array.dims:
                            x_coordinates = temp_data_array.x.values
                            y_coordinates = temp_data_array.y.values
                        elif 'lon' in temp_data_array.dims and 'lat' in temp_data_array.dims:
                            x_coordinates = temp_data_array.lon.values
                            y_coordinates = temp_data_array.lat.values
                        else:
                            print(f"Warning: Could not find coordinate dimensions for {variable} at {timestamp}")
                            continue
                        
                        # Calculate transform
                        x_resolution = float(x_coordinates[1] - x_coordinates[0]) if len(x_coordinates) > 1 else 0.125
                        y_resolution = float(y_coordinates[1] - y_coordinates[0]) if len(y_coordinates) > 1 else 0.125
                        
                        data_transform = Affine(
                            x_resolution, 0.0, float(x_coordinates[0]) - x_resolution/2,
                            0.0, y_resolution, float(y_coordinates[0]) - y_resolution/2
                        )
                        
                        # Write temporary GeoTIFF
                        profile = {
                            'driver': 'GTiff',
                            'height': data_array_values.shape[0],
                            'width': data_array_values.shape[1],
                            'count': 1,
                            'dtype': data_array_values.dtype,
                            'crs': 'EPSG:4326',
                            'transform': data_transform,
                            'nodata': -9999,
                            'compress': 'lzw'
                        }
                        
                        with rasterio.open(temp_tif, 'w', **profile) as destination:
                            destination.write(data_array_values, 1)
                    
                    # Now align to unified grid
                    aligned_path = os.path.join(variable_directory, f"{variable}_{timestamp}_aligned.tif")
                    
                    if grid_manager.align_raster_to_grid(temp_tif, aligned_path, grid_metadata):
                        # Verify the aligned file was created properly
                        try:
                            with rasterio.open(aligned_path) as check_source:
                                if check_source.count > 0 and check_source.width > 0 and check_source.height > 0:
                                    print(f"✓ Successfully aligned {variable} for {timestamp}")
                                else:
                                    print(f"✗ Aligned file appears empty for {variable} at {timestamp}")
                        except Exception as e:
                            print(f"✗ Error verifying aligned file {aligned_path}: {e}")
                    else:
                        print(f"✗ Failed to align {variable} for {timestamp}")
                    
                    # Clean up temporary files
                    for temp_file in [temp_netcdf, temp_tif]:
                        if os.path.exists(temp_file):
                            try:
                                os.remove(temp_file)
                            except Exception as e:
                                print(f"Warning: Could not remove {temp_file}: {e}")
                                
                except Exception as e:
                    print(f"Error processing NLDAS {variable} at time {time_step}: {e}")
                    continue
                    
    except Exception as e:
        print(f"Error in NLDAS processing: {e}")
        import traceback
        traceback.print_exc()
        update_job_status(request_id, JobStatus.NLDAS_ERROR, str(e))
        return
    
    update_job_status(request_id, JobStatus.NLDAS_DONE)

# ------------------- Elevation with Alignment -------------------
def process_elevation_data(request_id: str, date_from: str, date_to: str, geometry_json: str, 
                          grid_manager: UnifiedGridManager, grid_metadata: Dict):
    update_job_status(request_id, JobStatus.ELEVATION_STARTED)
    output_directory = os.path.join(ETMAP_DATA_DIR, request_id, 'elevation')
    os.makedirs(output_directory, exist_ok=True)
    
    area_of_interest = shape(json.loads(geometry_json))
    
    try:
        print("Fetching elevation data using py3dep...")
        print(f"AOI bounds: {area_of_interest.bounds}")
        
        # Check if AOI is within CONUS (py3dep coverage area)
        min_longitude, min_latitude, max_longitude, max_latitude = area_of_interest.bounds
        if not (-130 <= min_longitude <= -60 and 20 <= min_latitude <= 50):
            print("Warning: AOI appears to be outside CONUS. py3dep may not have data.")
            
        # Set up caching environment variables for better performance
        cache_directory = os.path.join(output_directory, 'cache')
        os.makedirs(cache_directory, exist_ok=True)
        os.environ["HYRIVER_CACHE_NAME"] = os.path.join(cache_directory, "py3dep_cache.sqlite")
        os.environ["HYRIVER_CACHE_EXPIRE"] = "86400"  # 24 hours
        
        # Check 3DEP availability first
        try:
            print("Checking 3DEP data availability...")
            availability = py3dep.check_3dep_availability(area_of_interest)
            print(f"Available resolutions: {availability}")
        except Exception as e:
            print(f"Could not check availability: {e}")
            availability = {}
        
        # Try different resolutions in order of preference
        # 30m matches Landsat resolution, 100m as fallback
        resolutions_to_try = [30, 100]  # meters
        elevation_data = None
        successful_resolution = None
        
        for resolution in resolutions_to_try:
            try:
                print(f"Attempting to fetch elevation data at {resolution}m resolution...")
                
                # Get elevation data for the AOI using recommended parameters
                elevation_data = py3dep.get_map(
                    "DEM",                    # Layer type
                    area_of_interest,         # Geometry
                    resolution=resolution,     # Resolution in meters
                    geo_crs="EPSG:4326",      # Input geometry CRS
                    crs="EPSG:4326"           # Output CRS (supported by 3DEP)
                )
                
                print(f"✓ Successfully fetched elevation data at {resolution}m resolution")
                print(f"  Data shape: {elevation_data.rio.width} x {elevation_data.rio.height}")
                print(f"  Data CRS: {elevation_data.rio.crs}")
                print(f"  Data bounds: {elevation_data.rio.bounds()}")
                
                successful_resolution = resolution
                break
                
            except Exception as e:
                print(f"Failed at {resolution}m resolution: {e}")
                if resolution == resolutions_to_try[-1]:
                    # Last resolution failed, try alternative approach
                    print("All standard resolutions failed. Trying elevation_bygrid approach...")
                    
                    try:
                        # Use elevation_bygrid as fallback
                        print("Trying elevation_bygrid approach...")
                        
                        # Create a grid over the AOI
                        bounds = area_of_interest.bounds
                        # Use grid size that roughly matches 100m resolution
                        grid_size = 0.001  # ~100m at equator in degrees
                        x_coordinates = np.arange(bounds[0], bounds[2], grid_size)
                        y_coordinates = np.arange(bounds[1], bounds[3], grid_size)
                        
                        if len(x_coordinates) > 200 or len(y_coordinates) > 200:
                            print("AOI too large for fine grid, using coarser grid...")
                            grid_size = 0.005  # ~500m at equator
                            x_coordinates = np.arange(bounds[0], bounds[2], grid_size)
                            y_coordinates = np.arange(bounds[1], bounds[3], grid_size)
                        
                        print(f"Grid size: {len(x_coordinates)} x {len(y_coordinates)} points")
                        
                        # Get elevation for grid points (use 100m resolution for grid method)
                        elevation_grid = py3dep.elevation_bygrid(
                            x_coordinates, y_coordinates, 
                            crs="EPSG:4326", 
                            resolution=100  # meters
                        )
                        
                        print(f"✓ Successfully fetched elevation using grid approach")
                        print(f"  Grid shape: {elevation_grid.rio.width} x {elevation_grid.rio.height}")
                        
                        elevation_data = elevation_grid
                        successful_resolution = f"grid_{grid_size*111000:.0f}m"  # Convert degrees to approx meters
                        break
                        
                    except Exception as grid_error:
                        print(f"Grid approach also failed: {grid_error}")
                        
                continue
        
        if elevation_data is None:
            print("All elevation retrieval methods failed")
            update_job_status(request_id, JobStatus.ELEVATION_ERROR, "All elevation retrieval methods failed")
            return
            
        # Check for valid data
        elevation_values = elevation_data.values
        
        # Handle different data structures
        if elevation_values.ndim > 2:
            elevation_values = elevation_values[0]  # Take first band if multi-band
            
        valid_mask = ~np.isnan(elevation_values)
        number_of_valid = np.sum(valid_mask)
        
        print(f"Valid elevation pixels: {number_of_valid} out of {elevation_values.size}")
        print(f"Coverage: {number_of_valid/elevation_values.size*100:.1f}%")
        
        if number_of_valid == 0:
            print("No valid elevation data found in AOI")
            update_job_status(request_id, JobStatus.ELEVATION_ERROR, "No valid elevation data found in AOI")
            return
        
        # Show raw statistics
        valid_elevations = elevation_values[valid_mask]
        print(f"Raw elevation range: {valid_elevations.min():.1f}m to {valid_elevations.max():.1f}m")
        print(f"Raw mean elevation: {valid_elevations.mean():.1f}m")
        print(f"Raw std elevation: {valid_elevations.std():.1f}m")
        
        # Save raw elevation data first
        temp_path = os.path.join(output_directory, "elevation_raw.tif")
        
        try:
            elevation_data.rio.to_raster(temp_path)
            print(f"Saved raw elevation data to {temp_path}")
            
            # Verify raw file
            with rasterio.open(temp_path) as verify_source:
                raw_data = verify_source.read(1)
                raw_nodata = verify_source.nodata
                if raw_nodata is not None:
                    raw_valid = ~np.isnan(raw_data) & (raw_data != raw_nodata)
                else:
                    raw_valid = ~np.isnan(raw_data)
                print(f"Raw file verification: {np.sum(raw_valid)} valid pixels")
                
        except Exception as e:
            print(f"Failed to save raw elevation data: {e}")
            update_job_status(request_id, JobStatus.ELEVATION_ERROR, f"Failed to save raw elevation data: {e}")
            return
        
        # Align to unified grid
        aligned_path = os.path.join(output_directory, 'elevation_aligned.tif')
        
        print(f"Aligning elevation data to unified grid...")
        print(f"Target grid: {grid_metadata['width']}x{grid_metadata['height']} pixels")
        
        # Use bilinear resampling for elevation data (preserves smooth transitions)
        if grid_manager.align_raster_to_grid(temp_path, aligned_path, grid_metadata, 
                                           resampling_method=Resampling.bilinear):
            # Verify the aligned file
            try:
                with rasterio.open(aligned_path) as check_source:
                    if check_source.count > 0 and check_source.width > 0 and check_source.height > 0:
                        aligned_data = check_source.read(1)
                        
                        # Check for valid data in aligned file
                        nodata_value = check_source.nodata if check_source.nodata is not None else -9999
                        valid_aligned = ~np.isnan(aligned_data) & (aligned_data != nodata_value)
                        number_of_valid_aligned = np.sum(valid_aligned)
                        
                        print(f"Aligned file verification: {number_of_valid_aligned} valid pixels out of {aligned_data.size}")
                        
                        if number_of_valid_aligned > 0:
                            valid_aligned_elevations = aligned_data[valid_aligned]
                            elevation_statistics = {
                                'min': float(valid_aligned_elevations.min()),
                                'max': float(valid_aligned_elevations.max()),
                                'mean': float(valid_aligned_elevations.mean()),
                                'std': float(valid_aligned_elevations.std()),
                                'valid_pixels': int(number_of_valid_aligned),
                                'total_pixels': int(aligned_data.size),
                                'coverage_percent': float(number_of_valid_aligned / aligned_data.size * 100),
                                'resolution_used': successful_resolution,
                                'data_source': '3DEP',
                                'fetch_method': 'get_map' if isinstance(successful_resolution, int) else 'elevation_bygrid'
                            }
                            
                            print(f"✓ Successfully aligned elevation data")
                            print(f"  Resolution used: {successful_resolution}")
                            print(f"  Elevation range: {elevation_statistics['min']:.1f}m to {elevation_statistics['max']:.1f}m")
                            print(f"  Mean elevation: {elevation_statistics['mean']:.1f}m ± {elevation_statistics['std']:.1f}m")
                            print(f"  Coverage: {elevation_statistics['coverage_percent']:.1f}% of AOI")
                            
                            # Save elevation statistics
                            statistics_path = os.path.join(output_directory, 'elevation_statistics.json')
                            with open(statistics_path, 'w') as file:
                                json.dump(elevation_statistics, file, indent=2)
                                
                            # Save metadata about the fetch
                            metadata = {
                                'source': '3DEP via py3dep',
                                'resolution_requested': [30, 100],
                                'resolution_used': successful_resolution,
                                'aoi_bounds': area_of_interest.bounds,
                                'availability_check': availability,
                                'cache_location': os.environ.get("HYRIVER_CACHE_NAME"),
                                'processing_date': datetime.utcnow().isoformat(),
                                'note': 'Resolutions chosen to match Landsat (30m) with 100m fallback'
                            }
                            
                            metadata_path = os.path.join(output_directory, 'elevation_metadata.json')
                            with open(metadata_path, 'w') as file:
                                json.dump(metadata, file, indent=2)
                                
                        else:
                            print(f"✗ Aligned elevation file contains no valid data")
                            update_job_status(request_id, JobStatus.ELEVATION_ERROR, "No valid data after alignment")
                            return
                            
                    else:
                        print(f"✗ Aligned elevation file appears empty")
                        update_job_status(request_id, JobStatus.ELEVATION_ERROR, "Empty aligned file")
                        return
                        
            except Exception as e:
                print(f"✗ Error verifying aligned elevation file: {e}")
                update_job_status(request_id, JobStatus.ELEVATION_ERROR, f"Verification error: {e}")
                return
        else:
            print(f"✗ Failed to align elevation data")
            update_job_status(request_id, JobStatus.ELEVATION_ERROR, "Alignment failed")
            return
        
        # Clean up temporary file
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
                print("✓ Cleaned up temporary files")
            except Exception as e:
                print(f"Warning: Could not remove {temp_path}: {e}")
                
    except Exception as e:
        print(f"✗ Error in elevation processing: {e}")
        import traceback
        traceback.print_exc()
        update_job_status(request_id, JobStatus.ELEVATION_ERROR, str(e))
        return
    
    update_job_status(request_id, JobStatus.ELEVATION_DONE)

def process_nlcd_data(request_id: str, date_from: str, date_to: str, geometry_json: str, 
                     grid_manager: UnifiedGridManager, grid_metadata: Dict):
    update_job_status(request_id, JobStatus.NLCD_STARTED)
    output_directory = os.path.join(ETMAP_DATA_DIR, request_id, 'nlcd')
    os.makedirs(output_directory, exist_ok=True)
    
    try:
        aligned_path = os.path.join(output_directory, 'nlcd_aligned.tif')
        grid_manager.align_raster_to_grid(NLCD_FILE_PATH, aligned_path, grid_metadata, 
                                        resampling_method=Resampling.nearest)
        update_job_status(request_id, JobStatus.NLCD_DONE)
    except Exception as e:
        print(f"Error NLCD: {e}", file=sys.stderr)
        update_job_status(request_id, JobStatus.NLCD_ERROR, str(e))

# ------------------- Combined runner with unified grid -------------------
def execute_all_processing_jobs(request_id: str, date_from: str, date_to: str, geometry_json: str):
    """Enhanced job runner with unified grid alignment including SSURGO"""
    area_of_interest = shape(json.loads(geometry_json))
    
    # Initialize grid manager
    grid_manager = UnifiedGridManager()
    
    # Collect sample datasets for grid computation
    sample_datasets = collect_sample_datasets(request_id)
    
    # Compute unified grid covering AOI + all inputs
    update_job_status(request_id, JobStatus.COMPUTING_GRID)
    unified_metadata = grid_manager.compute_unified_grid(area_of_interest, sample_datasets, 'EPSG:4326')
    
    # Clip grid to AOI bounds
    aoi_metadata = grid_manager.clip_to_aoi(area_of_interest, 'EPSG:4326')
    
    # Save grid metadata for reference
    grid_file_path = os.path.join(ETMAP_DATA_DIR, request_id, 'grid_metadata.json')
    os.makedirs(os.path.dirname(grid_file_path), exist_ok=True)
    with open(grid_file_path, 'w') as file:
        # Convert Affine to serializable format
        metadata_serializable = aoi_metadata.copy()
        metadata_serializable['transform'] = list(aoi_metadata['transform'])[:6]
        json.dump(metadata_serializable, file, indent=2)
    
    # Run all data collection jobs with alignment (SSURGO added first)
    processing_jobs = [
        ('ssurgo', lambda: process_ssurgo_data(request_id, date_from, date_to, geometry_json, grid_manager, aoi_metadata)),
        ('landsat', lambda: process_landsat_data(request_id, date_from, date_to, geometry_json, grid_manager, aoi_metadata)),
        ('prism', lambda: process_prism_data(request_id, date_from, date_to, geometry_json, grid_manager, aoi_metadata)),
        ('nldas', lambda: process_nldas_data(request_id, date_from, date_to, geometry_json, grid_manager, aoi_metadata)),
        ('elevation', lambda: process_elevation_data(request_id, date_from, date_to, geometry_json, grid_manager, aoi_metadata)),
        ('nlcd', lambda: process_nlcd_data(request_id, date_from, date_to, geometry_json, grid_manager, aoi_metadata))
    ]
    
    for job_name, job_function in processing_jobs:
        try:
            job_function()
        except Exception as e:
            print(f"{job_name} job failed: {e}", file=sys.stderr)
            update_job_status(request_id, JobStatus.FAILED, f"{job_name}: {str(e)}")
            return
    
    update_job_status(request_id, JobStatus.SUCCESS)
    
    # Copy placeholder result
    placeholder_source = os.path.join(os.path.dirname(__file__), 'placeholder.png')
    placeholder_destination = os.path.join(RESULTS_DIR, f"{request_id}.png")
    if os.path.isfile(placeholder_source):
        shutil.copy(placeholder_source, placeholder_destination)

# ------------------- Flask Routes with Standard REST API Naming -------------------
@etmap_bp.route('/v1/etmap', methods=['POST'])
def create_etmap_request():
    """Create a new ETMap processing request"""
    request_data = request.get_json(silent=True)
    if not request_data:
        return jsonify({'error': 'Invalid JSON payload'}), 400
    
    # Validate required fields
    required_fields = ['date_from', 'date_to', 'geometry']
    for field in required_fields:
        if field not in request_data:
            return jsonify({'error': f'Missing required field: {field}'}), 400
    
    # Validate geometry
    try:
        shape(request_data['geometry'])
    except Exception as e:
        return jsonify({'error': 'Invalid geometry', 'details': str(e)}), 400

    date_from = request_data['date_from']
    date_to = request_data['date_to']
    
    # Check for existing job with same parameters
    cursor.execute(
        'SELECT request_id, request_json FROM etmap_jobs WHERE date_from=? AND date_to=?',
        (date_from, date_to)
    )
    for existing_request_id, existing_request_json in cursor.fetchall():
        previous_request = json.loads(existing_request_json)
        if previous_request.get('geometry') == request_data['geometry']:
            return jsonify({'request_id': existing_request_id}), 200

    # Create new job
    request_id = str(uuid.uuid4())
    current_timestamp = datetime.utcnow().isoformat()
    geometry_json = json.dumps(request_data['geometry'], sort_keys=True)
    request_json = json.dumps(request_data, sort_keys=True)
    
    cursor.execute(
        '''INSERT INTO etmap_jobs(request_id, date_from, date_to, geometry, status, request_json, created_at) 
           VALUES (?,?,?,?,?,?,?)''',
        (request_id, date_from, date_to, geometry_json, JobStatus.QUEUED.value, request_json, current_timestamp)
    )
    connection.commit()
    
    # Start processing in background thread
    threading.Thread(
        target=execute_all_processing_jobs,
        args=(request_id, date_from, date_to, geometry_json),
        daemon=True
    ).start()
    
    return jsonify({'request_id': request_id}), 201


@etmap_bp.route('/v1/etmap/<string:request_id>', methods=['GET'])
def get_etmap_status(request_id: str):
    """Get the status of an ETMap processing request"""
    try:
        uuid.UUID(request_id)
    except ValueError:
        return jsonify({'error': 'Invalid request ID format'}), 400
    
    cursor.execute(
        'SELECT status, created_at, updated_at, request_json, error_message FROM etmap_jobs WHERE request_id=?', 
        (request_id,)
    )
    row = cursor.fetchone()
    if not row:
        return jsonify({'error': 'Request not found'}), 404
    
    status, created_at, updated_at, request_json, error_message = row
    
    response_data = {
        'request_id': request_id,
        'status': status,
        'created_at': created_at,
        'updated_at': updated_at,
        'request': json.loads(request_json)
    }
    
    if error_message:
        response_data['error_message'] = error_message
    
    return jsonify(response_data), 200


@etmap_bp.route('/v1/etmap/<string:request_id>/result', methods=['GET'])
def get_etmap_result(request_id: str):
    """Get the result of a completed ETMap processing request"""
    try:
        uuid.UUID(request_id)
    except ValueError:
        return jsonify({'error': 'Invalid request ID format'}), 400
    
    cursor.execute('SELECT status FROM etmap_jobs WHERE request_id=?', (request_id,))
    row = cursor.fetchone()
    if not row:
        return jsonify({'error': 'Request not found'}), 404
    
    job_status = row[0]
    
    # 302 redirect logic: If job is not complete, redirect to status endpoint
    if job_status != JobStatus.SUCCESS.value:
        return redirect(url_for('etmap.get_etmap_status', request_id=request_id))
    
    # Job is complete, serve the PNG result
    result_file_path = os.path.join(RESULTS_DIR, f"{request_id}.png")
    if os.path.isfile(result_file_path):
        return send_file(result_file_path, mimetype='image/png')
    
    # Fallback to placeholder if specific result doesn't exist
    placeholder_path = os.path.join(os.path.dirname(__file__), 'dummysoilmap.png')
    if not os.path.isfile(placeholder_path):
        return jsonify({'error': 'Result image not available'}), 500
    return send_file(placeholder_path, mimetype='image/png')