#!/usr/bin/env python3
"""
ETMap Data Processors Module
Handles processing of different data types (NLDAS, Landsat, PRISM, Static)
"""

import os
import glob
import numpy as np
import rasterio
from rasterio.warp import Resampling
from typing import Dict, List, Tuple, Optional
from datetime import datetime
import re

from .config import ETMapConfig
from .utils import FileManager, ArrayUtils, LoggingUtils
from .grid_manager import UnifiedGridManager


class NLDASProcessor:
    """
    Process NLDAS hourly data for ET calculations
    """
    
    def __init__(self, nldas_base_path: str = None):
        self.nldas_base_path = nldas_base_path or ETMapConfig.NLDAS_DIR
        
    def find_nldas_hourly_files(self, date: datetime) -> List[Tuple[int, str]]:
        """
        Find all hourly NLDAS files for a given date
        
        Args:
            date: Date to find files for
            
        Returns:
            List of (hour, filepath) tuples
        """
        date_str = date.strftime('%Y-%m-%d')
        date_folder = os.path.join(self.nldas_base_path, date_str)
        
        hourly_files = []
        
        if not os.path.exists(date_folder):
            LoggingUtils.print_warning(f"NLDAS date folder not found: {date_folder}")
            return hourly_files
        
        # Look for all .tif files in the date folder
        pattern = os.path.join(date_folder, "*.tif")
        files = glob.glob(pattern)
        
        print(f"Scanning {len(files)} files in {date_folder}")
        
        for file_path in files:
            filename = os.path.basename(file_path)
            hour = self._parse_hour_from_filename(filename)
            
            # If we found a valid hour, add to list
            if hour is not None and 0 <= hour <= 23:
                hourly_files.append((hour, file_path))
                LoggingUtils.print_success(f"Successfully parsed hour {hour:02d}: {filename}")
            else:
                LoggingUtils.print_error(f"Could not parse hour from: {filename}")
        
        hourly_files.sort(key=lambda x: x[0])  # Sort by hour
        LoggingUtils.print_success(f"Found {len(hourly_files)} NLDAS hourly files for {date_str}")
        
        return hourly_files
    
    def _parse_hour_from_filename(self, filename: str) -> Optional[int]:
        """
        Parse hour from NLDAS filename using multiple patterns
        
        Args:
            filename: NLDAS filename
            
        Returns:
            Hour as integer or None if not found
        """
        hour = None
        
        # Pattern 1: NLDAS_FORA_20240329_2300.tif (your actual format!)
        if 'NLDAS_FORA' in filename:
            try:
                hour_match = re.search(r'NLDAS_FORA_\d{8}_(\d{4})\.tif', filename)
                if hour_match:
                    hour_code = hour_match.group(1)
                    hour = int(hour_code[:2])  # Extract first 2 digits (23 from 2300)
                    print(f"  Parsed NLDAS_FORA format: {filename} -> hour {hour}")
            except Exception as e:
                print(f"  Error parsing NLDAS_FORA format: {filename} - {e}")
        
        # Pattern 2: NLDAS_DAILY.A20240316.002.tif (original format)
        if hour is None and 'NLDAS' in filename and '.A' in filename:
            try:
                hour_match = re.search(r'\.A\d{8}\.(\d{3})\.', filename)
                if hour_match:
                    hour_code = int(hour_match.group(1))
                    hour = hour_code - 2  # NLDAS uses 002=hour0, 003=hour1, etc.
                    print(f"  Parsed NLDAS daily format: {filename} -> hour {hour}")
            except Exception as e:
                print(f"  Error parsing NLDAS daily format: {filename} - {e}")
        
        # Pattern 3: NLDAS_FORA0125_H.A20240316.0200.020.NC.tif
        if hour is None and 'NLDAS' in filename:
            try:
                hour_match = re.search(r'\.A\d{8}\.(\d{4})\.', filename)
                if hour_match:
                    hour_code = int(hour_match.group(1))
                    hour = hour_code // 100  # 0200 = hour 2, 1300 = hour 13, etc.
                    print(f"  Parsed NLDAS extended format: {filename} -> hour {hour}")
            except Exception as e:
                print(f"  Error parsing NLDAS extended format: {filename} - {e}")
        
        # Pattern 4: Simple hour in filename like _h00_, _h01_, etc.
        if hour is None:
            try:
                hour_match = re.search(r'_h(\d{2})_', filename)
                if hour_match:
                    hour = int(hour_match.group(1))
                    print(f"  Parsed simple hour format: {filename} -> hour {hour}")
            except Exception as e:
                print(f"  Error parsing simple hour format: {filename} - {e}")
        
        # Pattern 5: Hour at end like _00.tif, _01.tif
        if hour is None:
            try:
                hour_match = re.search(r'_(\d{2})\.tif$', filename)
                if hour_match:
                    hour = int(hour_match.group(1))
                    print(f"  Parsed end hour format: {filename} -> hour {hour}")
            except Exception as e:
                print(f"  Error parsing end hour format: {filename} - {e}")
        
        return hour
    
    def load_nldas_data(self, file_path: str) -> Optional[np.ndarray]:
        """
        Load NLDAS data and extract relevant variables
        
        Args:
            file_path: Path to NLDAS file
            
        Returns:
            Stacked array of NLDAS variables or None if error
        """
        try:
            with rasterio.open(file_path) as src:
                # NLDAS typically has multiple bands
                data = src.read()
                
                if data.shape[0] >= 5:  # Need at least 5 bands
                    # Extract key variables (following Scala code pattern)
                    temp = data[0]          # Air temperature
                    humidity = data[1]      # Specific humidity 
                    u_wind = data[3]        # U wind component
                    v_wind = data[4]        # V wind component
                    radiation = data[5] if data.shape[0] > 5 else np.zeros_like(temp)
                    
                    # Calculate wind speed (hypot from Scala code)
                    wind_speed = np.sqrt(u_wind**2 + v_wind**2)
                    
                    # Stack relevant variables
                    nldas_stack = np.stack([temp, humidity, wind_speed, radiation], axis=0)
                    return nldas_stack
                else:
                    LoggingUtils.print_warning(f"Insufficient bands in NLDAS file: {file_path}")
                    return None
                    
        except Exception as e:
            LoggingUtils.print_error(f"Error loading NLDAS data from {file_path}: {e}")
            return None
    
    def align_nldas_to_grid(self, nldas_data: np.ndarray, aoi_metadata: Dict) -> np.ndarray:
        """
        Align NLDAS data to the same grid as other datasets
        
        Args:
            nldas_data: NLDAS data array
            aoi_metadata: AOI metadata dictionary
            
        Returns:
            Aligned NLDAS data array
        """
        target_shape = (aoi_metadata['height'], aoi_metadata['width'])
        
        aligned_bands = []
        for band_idx in range(nldas_data.shape[0]):
            band = nldas_data[band_idx]
            aligned_band = ArrayUtils.resize_array_to_target(band, target_shape)
            aligned_bands.append(aligned_band)
        
        return np.stack(aligned_bands, axis=0)


class LandsatProcessor:
    """
    Process Landsat data (B4, B5) and calculate NDVI/LAI
    """
    
    def __init__(self, grid_manager: UnifiedGridManager):
        self.grid_manager = grid_manager
        
    def process_landsat_data(self, aoi_metadata: Dict, output_base_path: str):
        """
        Process Landsat data and calculate NDVI
        
        Args:
            aoi_metadata: AOI metadata dictionary
            output_base_path: Base output path
        """
        LoggingUtils.print_step_header("Processing Landsat Data")
        
        b4_pattern = os.path.join(ETMapConfig.LANDSAT_B4_DIR, "*.tif")
        b5_pattern = os.path.join(ETMapConfig.LANDSAT_B5_DIR, "*.tif")
        
        b4_files = sorted(glob.glob(b4_pattern))
        b5_files = sorted(glob.glob(b5_pattern))
        
        print(f"Found {len(b4_files)} B4 files and {len(b5_files)} B5 files")
        
        if len(b4_files) == 0 or len(b5_files) == 0:
            LoggingUtils.print_warning("No Landsat files found")
            return
        
        landsat_output = ETMapConfig.get_output_path(
            os.path.basename(output_base_path), 'landsat'
        )
        FileManager.ensure_directory_exists(landsat_output)
        
        processed_count = 0
        min_files = min(len(b4_files), len(b5_files))
        
        for i in range(min(min_files, ETMapConfig.MAX_LANDSAT_SCENES)):
            b4_file = b4_files[i]
            b5_file = b5_files[i]
            
            print(f"Processing Landsat scene {i+1}: {os.path.basename(b4_file)}")
            
            # Align bands
            b4_aligned = os.path.join(landsat_output, f"landsat_b4_{i:03d}_aligned.tif")
            b5_aligned = os.path.join(landsat_output, f"landsat_b5_{i:03d}_aligned.tif")
            
            b4_success = self.grid_manager.align_raster_to_grid(
                b4_file, b4_aligned, aoi_metadata, Resampling.nearest
            )
            b5_success = self.grid_manager.align_raster_to_grid(
                b5_file, b5_aligned, aoi_metadata, Resampling.nearest
            )
            
            # Calculate NDVI
            if b4_success and b5_success:
                ndvi_path = os.path.join(landsat_output, f"landsat_ndvi_{i:03d}.tif")
                self._calculate_ndvi_file(b4_aligned, b5_aligned, ndvi_path)
                processed_count += 1
        
        LoggingUtils.print_success(f"Processed {processed_count} Landsat scenes")
    
    def _calculate_ndvi_file(self, b4_path: str, b5_path: str, ndvi_path: str):
        """
        Calculate NDVI from B4 and B5 band files
        
        Args:
            b4_path: Path to B4 (red) band file
            b5_path: Path to B5 (NIR) band file
            ndvi_path: Output NDVI file path
        """
        try:
            with rasterio.open(b4_path) as b4_src, rasterio.open(b5_path) as b5_src:
                b4_data = b4_src.read(1).astype(np.float32)
                b5_data = b5_src.read(1).astype(np.float32)
                
                ndvi = ArrayUtils.calculate_ndvi(b4_data, b5_data)
                
                profile = b4_src.profile.copy()
                profile.update(dtype=np.float32, nodata=-9999.0)
                
                with rasterio.open(ndvi_path, 'w', **profile) as dst:
                    dst.write(ndvi, 1)
                
                valid_pixels = np.sum((ndvi != -9999.0))
                LoggingUtils.print_success(f"NDVI calculated: {valid_pixels} valid pixels")
                
        except Exception as e:
            LoggingUtils.print_error(f"Error calculating NDVI: {e}")
    
    def load_aligned_landsat_data(self, output_base_path: str) -> Dict[str, np.ndarray]:
        """
        Load pre-aligned Landsat data
        
        Args:
            output_base_path: Base output path
            
        Returns:
            Dictionary of loaded Landsat data
        """
        landsat_data = {}
        landsat_folder = ETMapConfig.get_output_path(
            os.path.basename(output_base_path), 'landsat'
        )
        
        # Load NDVI files
        ndvi_files = glob.glob(os.path.join(landsat_folder, "*ndvi*.tif"))
        if ndvi_files:
            try:
                with rasterio.open(ndvi_files[0]) as src:
                    ndvi = src.read(1)
                    landsat_data['ndvi'] = ndvi
                    
                    # Calculate LAI from NDVI (matching Scala code)
                    lai = ArrayUtils.calculate_lai_from_ndvi(ndvi)
                    landsat_data['lai'] = lai
                    
                    LoggingUtils.print_success("Loaded Landsat data: NDVI and LAI")
            except Exception as e:
                LoggingUtils.print_error(f"Error loading Landsat data: {e}")
        
        return landsat_data


class PRISMProcessor:
    """
    Process PRISM daily climate data
    """
    
    def __init__(self, grid_manager: UnifiedGridManager):
        self.grid_manager = grid_manager
        
    def process_prism_data_by_dates(self, aoi_metadata: Dict, date_from: str, date_to: str, output_base_path: str):
        """
        Process PRISM data for specific date range
        
        Args:
            aoi_metadata: AOI metadata dictionary
            date_from: Start date string
            date_to: End date string
            output_base_path: Base output path
        """
        LoggingUtils.print_step_header("Processing PRISM Data")
        
        # Filter PRISM folders by date range
        date_folders = FileManager.get_date_folders(ETMapConfig.PRISM_DIR, date_from, date_to)
        
        if not date_folders:
            LoggingUtils.print_warning("No PRISM data found for specified date range")
            return
        
        prism_output = ETMapConfig.get_output_path(
            os.path.basename(output_base_path), 'prism'
        )
        FileManager.ensure_directory_exists(prism_output)
        
        processed_count = 0
        total_files = 0
        
        for date_folder in date_folders:
            date_path = os.path.join(ETMapConfig.PRISM_DIR, date_folder)
            prism_files = glob.glob(os.path.join(date_path, "*.tif"))
            
            if not prism_files:
                continue
            
            print(f"Processing {date_folder} ({len(prism_files)} files)")
            total_files += len(prism_files)
            
            # Create date-specific output folder
            normalized_date = date_folder.replace('-', '_')
            date_output = os.path.join(prism_output, normalized_date)
            FileManager.ensure_directory_exists(date_output)
            
            for prism_file in prism_files:
                file_name = os.path.basename(prism_file)
                base_name = os.path.splitext(file_name)[0]
                
                output_file = os.path.join(date_output, f"{base_name}_aligned.tif")
                success = self.grid_manager.align_raster_to_grid(
                    prism_file, output_file, aoi_metadata, Resampling.bilinear
                )
                
                if success:
                    processed_count += 1
        
        LoggingUtils.print_success(f"Processed {processed_count}/{total_files} PRISM files")
    
    def load_aligned_prism_data(self, output_base_path: str, date: datetime) -> Dict[str, np.ndarray]:
        """
        Load pre-aligned PRISM daily data for specific date
        
        Args:
            output_base_path: Base output path
            date: Date to load data for
            
        Returns:
            Dictionary of loaded PRISM data
        """
        prism_data = {}
        date_str = date.strftime('%Y_%m_%d')
        prism_date_folder = ETMapConfig.get_output_path(
            os.path.basename(output_base_path), 'prism'
        )
        prism_date_folder = os.path.join(prism_date_folder, date_str)
        
        if not os.path.exists(prism_date_folder):
            date_str_alt = date.strftime('%Y-%m-%d')
            prism_date_folder = ETMapConfig.get_output_path(
                os.path.basename(output_base_path), 'prism'
            )
            prism_date_folder = os.path.join(prism_date_folder, date_str_alt)
        
        if os.path.exists(prism_date_folder):
            prism_files = glob.glob(os.path.join(prism_date_folder, "*aligned.tif"))
            
            for file_path in prism_files:
                filename = os.path.basename(file_path)
                var_name = self._identify_prism_variable(filename)
                
                if var_name:
                    try:
                        with rasterio.open(file_path) as src:
                            prism_data[var_name] = src.read(1)
                    except Exception as e:
                        LoggingUtils.print_error(f"Error loading PRISM {var_name}: {e}")
        
        return prism_data
    
    def _identify_prism_variable(self, filename: str) -> Optional[str]:
        """
        Identify PRISM variable from filename
        
        Args:
            filename: PRISM filename
            
        Returns:
            Variable name or None if not identified
        """
        if 'ppt' in filename:
            return 'precipitation'
        elif 'tmin' in filename:
            return 'temp_min'
        elif 'tmax' in filename:
            return 'temp_max'
        elif 'tmean' in filename:
            return 'temp_mean'
        elif 'vpdmin' in filename:
            return 'vpd_min'
        elif 'vpdmax' in filename:
            return 'vpd_max'
        else:
            return None


class StaticDataProcessor:
    """
    Process static layers (elevation, soil, NLCD)
    """
    
    def __init__(self, grid_manager: UnifiedGridManager):
        self.grid_manager = grid_manager
        
    def process_static_data(self, aoi_metadata: Dict, output_base_path: str):
        """
        Process static layers (elevation, soil, NLCD)
        
        Args:
            aoi_metadata: AOI metadata dictionary
            output_base_path: Base output path
        """
        LoggingUtils.print_step_header("Processing Static Data")
        
        static_output = ETMapConfig.get_output_path(
            os.path.basename(output_base_path), 'static'
        )
        FileManager.ensure_directory_exists(static_output)
        
        for layer_name, config in ETMapConfig.STATIC_LAYERS_CONFIG.items():
            print(f"Processing {layer_name}...")
            
            data_path = ETMapConfig.get_static_data_path(config['path_key'])
            
            if os.path.exists(data_path):
                output_file = os.path.join(static_output, config['output_name'])
                
                # Get resampling method
                resampling_method = getattr(Resampling, config['resampling'])
                
                success = self.grid_manager.align_raster_to_grid(
                    data_path, output_file, aoi_metadata, resampling_method
                )
                
                if success:
                    LoggingUtils.print_success(f"{layer_name} processed successfully")
                else:
                    LoggingUtils.print_error(f"{layer_name} processing failed")
            else:
                LoggingUtils.print_warning(f"File not found: {data_path}")
    
    def load_aligned_static_data(self, output_base_path: str) -> Dict[str, np.ndarray]:
        """
        Load pre-aligned static data
        
        Args:
            output_base_path: Base output path
            
        Returns:
            Dictionary of loaded static data
        """
        static_data = {}
        static_folder = ETMapConfig.get_output_path(
            os.path.basename(output_base_path), 'static'
        )
        
        static_files = {
            'soil_awc': 'soil_awc_aligned.tif',
            'soil_fc': 'soil_fc_aligned.tif',
            'elevation': 'elevation_aligned.tif',
            'nlcd': 'nlcd_aligned.tif'
        }
        
        for var_name, filename in static_files.items():
            file_path = os.path.join(static_folder, filename)
            if os.path.exists(file_path):
                try:
                    with rasterio.open(file_path) as src:
                        static_data[var_name] = src.read(1)
                        LoggingUtils.print_success(f"Loaded static data: {var_name}")
                except Exception as e:
                    LoggingUtils.print_error(f"Error loading {var_name}: {e}")
        
        return static_data


class DataCollector:
    """
    Collects sample datasets for grid computation
    """
    
    @staticmethod
    def collect_sample_datasets() -> List[str]:
        """
        Collect sample dataset paths for grid computation
        
        Returns:
            List of sample dataset paths
        """
        sample_paths = []
        
        # Landsat B4 samples
        landsat_b4_pattern = os.path.join(ETMapConfig.LANDSAT_B4_DIR, "*.tif")
        b4_files = glob.glob(landsat_b4_pattern)
        if b4_files:
            sample_paths.extend(b4_files[:5])
            
        # Static data samples
        for data_type, path in ETMapConfig.STATIC_DATA_PATHS.items():
            if os.path.exists(path):
                sample_paths.append(path)
        
        # PRISM sample
        if os.path.exists(ETMapConfig.PRISM_DIR):
            date_folders = []
            for item in os.listdir(ETMapConfig.PRISM_DIR):
                item_path = os.path.join(ETMapConfig.PRISM_DIR, item)
                if os.path.isdir(item_path) and '2024' in item:
                    date_folders.append(item)
            
            if date_folders:
                first_date_folder = sorted(date_folders)[0]
                prism_pattern = os.path.join(ETMapConfig.PRISM_DIR, first_date_folder, "*.tif")
                prism_files = glob.glob(prism_pattern)
                if prism_files:
                    sample_paths.append(prism_files[0])
        
        # NLDAS sample
        if os.path.exists(ETMapConfig.NLDAS_DIR):
            for date_folder in os.listdir(ETMapConfig.NLDAS_DIR):
                date_path = os.path.join(ETMapConfig.NLDAS_DIR, date_folder)
                if os.path.isdir(date_path):
                    nldas_files = glob.glob(os.path.join(date_path, "*.tif"))
                    if nldas_files:
                        sample_paths.append(nldas_files[0])
                        break
        
        print(f"Found {len(sample_paths)} sample datasets for grid computation")
        for path in sample_paths:
            if os.path.exists(path):
                print(f"  - {os.path.basename(path)}")
        
        return sample_paths