#!/usr/bin/env python3
"""
ETMap Data Processors Module
Handles processing of different data types (NLDAS, Landsat, PRISM, Static)
"""

import os
import glob
import numpy as np
import rasterio
from rasterio.warp import Resampling, reproject
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
        Returns: List of (hour, filepath)
        """
        date_str = date.strftime('%Y-%m-%d')
        date_folder = os.path.join(self.nldas_base_path, date_str)

        hourly_files: List[Tuple[int, str]] = []

        if not os.path.exists(date_folder):
            LoggingUtils.print_warning(f"NLDAS date folder not found: {date_folder}")
            return hourly_files

        files = glob.glob(os.path.join(date_folder, "*.tif"))
        print(f"Scanning {len(files)} files in {date_folder}")

        for file_path in files:
            filename = os.path.basename(file_path)
            hour = self._parse_hour_from_filename(filename)

            if hour is not None and 0 <= hour <= 23:
                hourly_files.append((hour, file_path))
                LoggingUtils.print_success(f"Successfully parsed hour {hour:02d}: {filename}")
            else:
                LoggingUtils.print_error(f"Could not parse hour from: {filename}")

        hourly_files.sort(key=lambda x: x[0])  # 0..23
        LoggingUtils.print_success(f"Found {len(hourly_files)} NLDAS hourly files for {date_str}")
        return hourly_files

    def _parse_hour_from_filename(self, filename: str) -> Optional[int]:
        """Support several common NLDAS naming patterns"""
        hour = None

        # Pattern 1: NLDAS_FORA_20240329_2300.tif
        m = re.search(r'NLDAS_FORA_\d{8}_(\d{4})\.tif', filename)
        if m:
            return int(m.group(1)[:2])

        # Pattern 2: NLDAS_FORA0125_H.A20240316.0200.020.NC.tif
        m = re.search(r'\.A\d{8}\.(\d{4})\.', filename)
        if hour is None and m:
            return int(m.group(1)) // 100

        # Pattern 3: ..._h00_...
        m = re.search(r'_h(\d{2})_', filename)
        if hour is None and m:
            return int(m.group(1))

        # Pattern 4: ..._00.tif
        m = re.search(r'_(\d{2})\.tif$', filename)
        if hour is None and m:
            return int(m.group(1))

        return None

    def load_nldas_data(self, file_path: str) -> Optional[np.ndarray]:
        """
        Load NLDAS data and extract relevant variables (returns °C, RH[0-1], m/s, W/m²)
        """
        try:
            with rasterio.open(file_path) as src:
                data = src.read().astype(np.float32)

                if data.shape[0] >= 5:
                    # Typical band usage (adjust if your file order differs)
                    temp = data[0]          # Air temperature (often Kelvin)
                    humidity_in = data[1]   # Specific humidity q (kg/kg) OR RH
                    u_wind = data[3]        # U wind component (m/s)
                    v_wind = data[4]        # V wind component (m/s)
                    radiation = data[5] if data.shape[0] > 5 else np.zeros_like(temp)

                    # Kelvin → °C (heuristic)
                    temp_c = temp - 273.15 if np.nanmedian(temp) > 100 else temp

                    # Humidity: convert q → RH if it looks like specific humidity
                    # (q typically < 0.03)
                    if np.nanmax(humidity_in) < 0.2:
                        q = np.clip(humidity_in, 0.0, 0.05)
                        p_kpa = 101.3  # fallback; BAITSSS also re-checks w/ elevation
                        es = 0.611 * np.exp((17.27 * temp_c) / (temp_c + 237.3))
                        e = (q * p_kpa) / (0.622 + 0.378 * q)
                        rh = np.clip(e / es, 0.01, 1.0)
                    else:
                        # already RH (0–1) or percentage (0–100)
                        rh = humidity_in / 100.0 if np.nanmax(humidity_in) > 1.0 else humidity_in
                        rh = np.clip(rh, 0.01, 1.0)

                    wind_speed = np.sqrt(u_wind**2 + v_wind**2)

                    nldas_stack = np.stack([temp_c, rh, wind_speed, radiation], axis=0).astype(np.float32)
                    return nldas_stack
                else:
                    LoggingUtils.print_warning(f"Insufficient bands in NLDAS file: {file_path}")
                    return None

        except Exception as e:
            LoggingUtils.print_error(f"Error loading NLDAS data from {file_path}: {e}")
            return None
        
    # --- New: preferred path – reproject to AOI grid using the source georeferencing ---
    def align_nldas_file_to_grid(self, file_path: str, aoi_metadata: Dict) -> Optional[np.ndarray]:
        """
        Read NLDAS from disk and reproject onto the AOI grid (CRS+transform) like Scala does.
        Returns a stack [temp, specific_humidity, wind_speed, shortwave] aligned to (H, W).
        """
        try:
            with rasterio.open(file_path) as src:
                data = src.read()
                if data.shape[0] < 5:
                    LoggingUtils.print_warning(f"Insufficient bands in NLDAS file: {file_path}")
                    return None

                temp = data[0].astype(np.float32)
                humidity = data[1].astype(np.float32)
                u_wind = data[3].astype(np.float32)
                v_wind = data[4].astype(np.float32)
                radiation = data[5].astype(np.float32) if data.shape[0] > 5 else np.zeros_like(temp, np.float32)
                wind_speed = np.sqrt(u_wind ** 2 + v_wind ** 2).astype(np.float32)

                src_crs = src.crs
                src_transform = src.transform

                dst_h, dst_w = aoi_metadata['height'], aoi_metadata['width']
                dst_crs = aoi_metadata['crs']
                dst_transform = aoi_metadata['transform']

                def _reproj_one(src_band: np.ndarray, resamp: Resampling) -> np.ndarray:
                    dst = np.empty((dst_h, dst_w), dtype=np.float32)
                    reproject(
                        source=src_band,
                        destination=dst,
                        src_transform=src_transform,
                        src_crs=src_crs,
                        dst_transform=dst_transform,
                        dst_crs=dst_crs,
                        resampling=resamp,
                    )
                    return dst

                # Continuous fields -> bilinear
                temp_a = _reproj_one(temp, Resampling.nearest)
                hum_a = _reproj_one(humidity, Resampling.nearest)
                wspd_a = _reproj_one(wind_speed, Resampling.nearest)
                rad_a = _reproj_one(radiation, Resampling.nearest)

                return np.stack([temp_a, hum_a, wspd_a, rad_a], axis=0)

        except Exception as e:
            LoggingUtils.print_error(f"Error aligning NLDAS {file_path}: {e}")
            return None

    # Kept for backwards-compat: simple resize (no reprojection). Prefer method above.
    def align_nldas_to_grid(self, nldas_data: np.ndarray, aoi_metadata: Dict) -> np.ndarray:
        target_shape = (aoi_metadata['height'], aoi_metadata['width'])
        aligned_bands = [ArrayUtils.resize_array_to_target(nldas_data[i], target_shape)
                         for i in range(nldas_data.shape[0])]
        return np.stack(aligned_bands, axis=0)


class LandsatProcessor:
    """
    Process Landsat data (B4, B5) and calculate NDVI/LAI
    Date-aware: if target_date is provided, only process files for that exact date (YYYY-MM-DD),
    pairing B4/B5 by scene_id so bands come from the same acquisition.
    """

    def __init__(self, grid_manager: UnifiedGridManager):
        self.grid_manager = grid_manager

    # ------------------------- helpers -------------------------
    @staticmethod
    def _scene_id_from_filename(path: str) -> str:
        """
        From B4_LC09_L2SP_..._2024-04-03.tif → return scene_id "LC09_L2SP_...".
        Robust to extra underscores inside the scene id.
        """
        base = os.path.basename(path)
        stem = os.path.splitext(base)[0]  # e.g., B4_LC09_L2SP_..._2024-04-03
        parts = stem.split('_')
        # band |  scene_id (1..-2)  | date
        return '_'.join(parts[1:-1]) if len(parts) > 2 else (parts[1] if len(parts) > 1 else "")

    def _list_pairs_for_date(self, date_str: str) -> List[Tuple[str, str]]:
        """
        Return list of (b4_path, b5_path) for the exact date (YYYY-MM-DD),
        pairing by scene_id. If none found, returns [].
        """
        b4_candidates = glob.glob(os.path.join(ETMapConfig.LANDSAT_B4_DIR, f"B4_*_{date_str}.tif"))
        b5_candidates = glob.glob(os.path.join(ETMapConfig.LANDSAT_B5_DIR, f"B5_*_{date_str}.tif"))

        b4_map = { self._scene_id_from_filename(p): p for p in b4_candidates }
        b5_map = { self._scene_id_from_filename(p): p for p in b5_candidates }
        common = sorted([sid for sid in b4_map if sid in b5_map])

        return [(b4_map[sid], b5_map[sid]) for sid in common]

    # --------------------------- main --------------------------
    def process_landsat_data(self,
                             aoi_metadata: Dict,
                             output_base_path: str,
                             target_date: Optional[datetime] = None,
                             prefer_scene_id: Optional[str] = None):
        """
        If target_date is provided, only process B4/B5 pairs for that exact date (YYYY-MM-DD).
        Otherwise, fall back to previous behavior (process first N matched pairs).
        """
        LoggingUtils.print_step_header("Processing Landsat Data")

        landsat_output = ETMapConfig.get_output_path(os.path.basename(output_base_path), 'landsat')
        FileManager.ensure_directory_exists(landsat_output)

        pairs: List[Tuple[str, str]] = []

        if target_date is not None:
            date_str = target_date.strftime("%Y-%m-%d")
            pairs = self._list_pairs_for_date(date_str)

            if prefer_scene_id:
                pairs = [(b4, b5) for (b4, b5) in pairs
                         if self._scene_id_from_filename(b4) == prefer_scene_id]

            if not pairs:
                LoggingUtils.print_warning(f"No Landsat B4/B5 pairs found for date {date_str}")
                return
            print(f"Found {len(pairs)} Landsat B4/B5 pairs for {date_str}")
        else:
            # Backward-compatible fallback: pair by scene_id with no date filter
            b4_files = sorted(glob.glob(os.path.join(ETMapConfig.LANDSAT_B4_DIR, "*.tif")))
            b5_files = sorted(glob.glob(os.path.join(ETMapConfig.LANDSAT_B5_DIR, "*.tif")))
            print(f"Found {len(b4_files)} B4 files and {len(b5_files)} B5 files")

            if not b4_files or not b5_files:
                LoggingUtils.print_warning("No Landsat files found")
                return

            b4_map = { self._scene_id_from_filename(p): p for p in b4_files }
            b5_map = { self._scene_id_from_filename(p): p for p in b5_files }
            common = sorted([sid for sid in b4_map if sid in b5_map])
            pairs = [(b4_map[sid], b5_map[sid]) for sid in common]
            if not pairs:
                LoggingUtils.print_warning("No matching B4/B5 scene pairs found")
                return
            print(f"Found {len(pairs)} Landsat B4/B5 pairs (no date filter)")

        # Respect MAX_LANDSAT_SCENES
        max_pairs = min(len(pairs), ETMapConfig.MAX_LANDSAT_SCENES)
        processed_count = 0

        for i, (b4_file, b5_file) in enumerate(pairs[:max_pairs]):
            print(f"Processing Landsat scene {i+1}: "
                  f"{os.path.basename(b4_file)}  &  {os.path.basename(b5_file)}")

            b4_aligned = os.path.join(landsat_output, f"landsat_b4_{i:03d}_aligned.tif")
            b5_aligned = os.path.join(landsat_output, f"landsat_b5_{i:03d}_aligned.tif")

            # Reflectance is continuous → bilinear is typically better for NDVI
            b4_success = self.grid_manager.align_raster_to_grid(b4_file, b4_aligned, aoi_metadata, Resampling.nearest)
            b5_success = self.grid_manager.align_raster_to_grid(b5_file, b5_aligned, aoi_metadata, Resampling.nearest)

            if b4_success and b5_success:
                ndvi_path = os.path.join(landsat_output, f"landsat_ndvi_{i:03d}.tif")
                self._calculate_ndvi_file(b4_aligned, b5_aligned, ndvi_path)
                processed_count += 1

        LoggingUtils.print_success(f"Processed {processed_count} Landsat scenes")

    def _calculate_ndvi_file(self, b4_path: str, b5_path: str, ndvi_path: str):
        """Calculate NDVI; auto-apply L2 scale factors if DN detected."""
        try:
            with rasterio.open(b4_path) as b4_src, rasterio.open(b5_path) as b5_src:
                b4 = b4_src.read(1).astype(np.float32)
                b5 = b5_src.read(1).astype(np.float32)

                # If values look like DN, apply L2 scale/offset (0.0000275, -0.2)
                if np.nanmax(b4) > 1.5 or np.nanmax(b5) > 1.5:
                    b4 = b4 * 0.0000275 - 0.2
                    b5 = b5 * 0.0000275 - 0.2

                ndvi = ArrayUtils.calculate_ndvi(b4, b5)

                profile = b4_src.profile.copy()
                profile.update(dtype=np.float32, nodata=-9999.0)

                with rasterio.open(ndvi_path, 'w', **profile) as dst:
                    dst.write(ndvi, 1)

                valid_pixels = int(np.sum(ndvi != -9999.0))
                LoggingUtils.print_success(f"NDVI calculated: {valid_pixels}")

        except Exception as e:
            LoggingUtils.print_error(f"Error calculating NDVI: {e}")

    def load_aligned_landsat_data(self, output_base_path: str) -> Dict[str, np.ndarray]:
        landsat_data: Dict[str, np.ndarray] = {}
        landsat_folder = ETMapConfig.get_output_path(os.path.basename(output_base_path), 'landsat')

        ndvi_files = glob.glob(os.path.join(landsat_folder, "*ndvi*.tif"))
        if ndvi_files:
            try:
                with rasterio.open(ndvi_files[0]) as src:
                    ndvi = src.read(1)
                    landsat_data['ndvi'] = ndvi
                    landsat_data['lai'] = ArrayUtils.calculate_lai_from_ndvi(ndvi)
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
        LoggingUtils.print_step_header("Processing PRISM Data")

        date_folders = FileManager.get_date_folders(ETMapConfig.PRISM_DIR, date_from, date_to)
        if not date_folders:
            LoggingUtils.print_warning("No PRISM data found for specified date range")
            return

        prism_output = ETMapConfig.get_output_path(os.path.basename(output_base_path), 'prism')
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

            normalized_date = date_folder.replace('-', '_')
            date_output = os.path.join(prism_output, normalized_date)
            FileManager.ensure_directory_exists(date_output)

            for prism_file in prism_files:
                base_name = os.path.splitext(os.path.basename(prism_file))[0]
                output_file = os.path.join(date_output, f"{base_name}_aligned.tif")

                success = self.grid_manager.align_raster_to_grid(
                    prism_file, output_file, aoi_metadata, Resampling.nearest
                )
                if success:
                    processed_count += 1

        LoggingUtils.print_success(f"Processed {processed_count}/{total_files} PRISM files")

    def load_aligned_prism_data(self, output_base_path: str, date: datetime) -> Dict[str, np.ndarray]:
        """
        Loads daily PRISM and converts precipitation from mm/day → mm/hour
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
                            arr = src.read(1).astype(np.float32)
                            if var_name == 'precipitation':
                                # PRISM ppt is daily total (mm/day) → convert to mm/hour
                                arr = arr / 24.0
                            prism_data[var_name] = arr
                    except Exception as e:
                        LoggingUtils.print_error(f"Error loading PRISM {var_name}: {e}")

        return prism_data

    def _identify_prism_variable(self, filename: str) -> Optional[str]:
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
        LoggingUtils.print_step_header("Processing Static Data")

        static_output = ETMapConfig.get_output_path(os.path.basename(output_base_path), 'static')
        FileManager.ensure_directory_exists(static_output)

        for layer_name, cfg in ETMapConfig.STATIC_LAYERS_CONFIG.items():
            print(f"Processing {layer_name}...")
            data_path = ETMapConfig.get_static_data_path(cfg['path_key'])

            if not os.path.exists(data_path):
                LoggingUtils.print_warning(f"File not found: {data_path}")
                continue

            out_file = os.path.join(static_output, cfg['output_name'])
            resamp = getattr(Resampling, cfg['resampling'])

            ok = self.grid_manager.align_raster_to_grid(data_path, out_file, aoi_metadata, resamp)
            if ok:
                LoggingUtils.print_success(f"{layer_name} processed successfully")
            else:
                LoggingUtils.print_error(f"{layer_name} processing failed")

    def load_aligned_static_data(self, output_base_path: str) -> Dict[str, np.ndarray]:
        static_data: Dict[str, np.ndarray] = {}
        static_folder = ETMapConfig.get_output_path(os.path.basename(output_base_path), 'static')

        static_files = {
            'soil_awc': 'soil_awc_aligned.tif',
            'soil_fc': 'soil_fc_aligned.tif',
            'elevation': 'elevation_aligned.tif',
            'nlcd': 'nlcd_aligned.tif'
        }

        for var_name, filename in static_files.items():
            fp = os.path.join(static_folder, filename)
            if not os.path.exists(fp):
                continue
            try:
                with rasterio.open(fp) as src:
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
        sample_paths: List[str] = []

        # Landsat B4 samples (limit count to keep cell size representative)
        b4_files = glob.glob(os.path.join(ETMapConfig.LANDSAT_B4_DIR, "*.tif"))
        if b4_files:
            sample_paths.extend(sorted(b4_files)[:3])

        # Static data
        for _, path in ETMapConfig.STATIC_DATA_PATHS.items():
            if os.path.exists(path):
                sample_paths.append(path)

        # Prefer a PRISM ppt sample (bounds are CONUS; cell size stable)
        if os.path.exists(ETMapConfig.PRISM_DIR):
            date_folders = [d for d in os.listdir(ETMapConfig.PRISM_DIR)
                            if os.path.isdir(os.path.join(ETMapConfig.PRISM_DIR, d))]
            for d in sorted(date_folders):
                cand = glob.glob(os.path.join(ETMapConfig.PRISM_DIR, d, "*ppt*.tif"))
                if cand:
                    sample_paths.append(cand[0])
                    break

        # NLDAS sample
        if os.path.exists(ETMapConfig.NLDAS_DIR):
            for date_folder in sorted(os.listdir(ETMapConfig.NLDAS_DIR)):
                date_path = os.path.join(ETMapConfig.NLDAS_DIR, date_folder)
                if os.path.isdir(date_path):
                    nldas_files = glob.glob(os.path.join(date_path, "*.tif"))
                    if nldas_files:
                        sample_paths.append(nldas_files[0])
                        break

        print(f"Found {len(sample_paths)} sample datasets for grid computation")
        for p in sample_paths:
            if os.path.exists(p):
                print(f"  - {os.path.basename(p)}")
        return sample_paths
