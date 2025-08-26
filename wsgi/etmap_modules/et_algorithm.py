#!/usr/bin/env python3
"""
ET Algorithm - Main Processing Workflow (Scala-aligned band order)
Handles file I/O, temporal continuity, and block coordination.
Uses BAITSSSAlgorithm for pure physics calculations.

This version matches the Scala pipeline band order:
  1: soil_awc
  2: soil_fc
  3: nlcd              <-- swapped with elevation
  4: elevation         <-- swapped with nlcd
  5: precipitation (PRISM)
  6: ndvi   (Landsat)
  7: lai    (Landsat)
  8: temperature (NLDAS)
  9: humidity    (NLDAS)
 10: wind_speed  (NLDAS)
 11: radiation   (NLDAS)
"""

import os
import glob
import json
from typing import Dict, List, Optional
from datetime import datetime, timedelta

import numpy as np
import rasterio

from .baitsss_algorithm import BAITSSSAlgorithm
from .config import ETMapConfig
from .utils import FileManager, LoggingUtils


class ETAlgorithm:
    """
    Main ET Algorithm workflow coordinator
    Handles file processing and coordinates with BAITSSS physics
    """

    def __init__(self):
        self.block_size = 200
        self.baitsss = BAITSSSAlgorithm()  # Pure physics module

    def create_enhanced_hourly_files_with_et(self, hourly_files_dir: str, output_dir: str) -> bool:
        """Main workflow: Process hourly files with temporal continuity"""
        print("🚀 Starting BAITSSS ET Processing...")
        print(f"📂 Input: {hourly_files_dir}")
        print(f"📁 Output: {output_dir}")

        try:
            hourly_files = self._get_sorted_hourly_files(hourly_files_dir)
            if not hourly_files:
                print(f"❌ ERROR: No hourly files found in {hourly_files_dir}")
                return False

            print(f"📊 Found {len(hourly_files)} hourly files to process")
            os.makedirs(output_dir, exist_ok=True)

            processed_count = self._process_hourly_sequence(hourly_files, output_dir)

            print("\n🎉 Processing Summary:")
            print(f"   ✅ Successfully processed: {processed_count}/{len(hourly_files)} files")

            if processed_count > 0:
                self._create_comprehensive_et_summary(output_dir)
                return True
            return False

        except Exception as e:
            print(f"💥 ERROR: ET processing failed: {e}")
            import traceback
            traceback.print_exc()
            return False

    def _get_sorted_hourly_files(self, hourly_files_dir: str) -> List[str]:
        """Get hourly files sorted chronologically"""
        pattern = os.path.join(hourly_files_dir, "*.tif")
        files = glob.glob(pattern)
        return sorted(files, key=lambda x: os.path.basename(x))

    def _process_hourly_sequence(self, hourly_files: List[str], output_dir: str) -> int:
        """Process hourly files in temporal sequence"""
        previous_state = None
        processed_count = 0

        for i, hourly_file in enumerate(hourly_files):
            filename = os.path.basename(hourly_file)
            print(f"⏰ Processing hour {i+1}/{len(hourly_files)}: {filename}")

            current_state = self._process_single_hourly_file(hourly_file, output_dir, previous_state)
            if current_state is not None:
                processed_count += 1
                previous_state = current_state  # Temporal continuity
                print(f"   ✅ Completed: {filename}")
            else:
                print(f"   ❌ Failed: {filename}")

        return processed_count

    def _process_single_hourly_file(self, hourly_file: str, output_dir: str,
                                    previous_state: Optional[Dict]) -> Optional[Dict]:
        """Process single hourly file with BAITSSS physics"""
        try:
            filename = os.path.basename(hourly_file)
            output_filename = filename.replace('.tif', '_enhanced.tif')
            output_path = os.path.join(output_dir, output_filename)

            with rasterio.open(hourly_file) as src:
                input_data = src.read()  # (bands, H, W)
                height, width = input_data.shape[1], input_data.shape[2]
                profile = src.profile

                print(f"    📐 Processing {height}x{width} pixels in blocks...")

                # Initialize or use previous state
                if previous_state is None:
                    print("    🌱 Initializing first hour state...")
                    current_state = self._initialize_et_state(height, width)
                else:
                    current_state = {k: v.copy() for k, v in previous_state.items()}
                    print("    🔄 Using previous hour state...")

                # Extract variables from input bands
                variables = self._extract_variables_from_bands(input_data)

                # Process using block-wise BAITSSS
                updated_state = self._run_blockwise_baitsss(variables, current_state, height, width)
                if updated_state is None:
                    return None

                # Create and save enhanced output
                enhanced_data = self._create_enhanced_output(input_data, updated_state)
                self._save_enhanced_file(enhanced_data, output_path, profile)

                print(f"    💾 Saved: {output_filename} ({enhanced_data.shape[0]} bands)")
                return updated_state

        except Exception as e:
            print(f"    💥 Error processing {hourly_file}: {e}")
            return None

    def _initialize_et_state(self, height: int, width: int) -> Dict[str, np.ndarray]:
        """Initialize ET state variables for first hour"""
        return {
            'et_cumulative': np.zeros((height, width), dtype=np.float32),
            'precip_cumulative': np.zeros((height, width), dtype=np.float32),
            'irrigation_cumulative': np.zeros((height, width), dtype=np.float32),
            'soil_moisture_surface': np.full((height, width), 0.2, dtype=np.float32),
            'soil_moisture_root': np.full((height, width), 0.3, dtype=np.float32),
        }

    def _extract_variables_from_bands(self, input_data: np.ndarray) -> Dict[str, np.ndarray]:
        """
        Extract variables from input raster bands using Scala-aligned order.

        Band indices (0-based):
          0: soil_awc
          1: soil_fc
          2: nlcd          <-- swapped with elevation
          3: elevation     <-- swapped with nlcd
          4: precipitation
          5: ndvi
          6: lai
          7: temperature
          8: humidity
          9: wind_speed
         10: radiation
        """
        band_mapping = {
            0: 'soil_awc',
            1: 'soil_fc',
            2: 'nlcd',          # <-- was elevation before; now matches Scala
            3: 'elevation',     # <-- was nlcd before; now matches Scala
            4: 'precipitation',
            5: 'ndvi',
            6: 'lai',
            7: 'temperature',
            8: 'humidity',
            9: 'wind_speed',
            10: 'radiation',
        }

        print(f"    📊 Extracting variables from {input_data.shape[0]} input bands...")

        variables: Dict[str, np.ndarray] = {}
        H, W = input_data.shape[1], input_data.shape[2]

        for band_idx, var_name in band_mapping.items():
            if band_idx < input_data.shape[0]:
                arr = input_data[band_idx].astype(np.float32)
                # Replace NoData with NaN to avoid contaminating physics
                arr = np.where(arr == -9999, np.nan, arr)
                variables[var_name] = arr
                print(f"      Band {band_idx+1}: {var_name} - Shape: {arr.shape}")
            else:
                variables[var_name] = self._get_default_array(var_name, H, W)
                print(f"      Band {band_idx+1}: {var_name} - Using defaults")

        # Report data quality
        print("    🔍 Data validation:")
        for var_name, var_data in variables.items():
            valid_pixels = np.sum(~np.isnan(var_data))
            print(f"      {var_name}: {valid_pixels:,} valid pixels")

        # Replace remaining NaNs with sensible defaults before physics
        for var_name in list(variables.keys()):
            if np.isnan(variables[var_name]).any():
                fill = self._get_default_scalar(var_name)
                variables[var_name] = np.nan_to_num(variables[var_name], nan=fill)

        return variables

    def _get_default_scalar(self, var_name: str) -> float:
        defaults = {
            'soil_awc': 0.15,
            'soil_fc': 35.0,
            'elevation': 200.0,
            'nlcd': 42.0,
            'precipitation': 0.0,
            'ndvi': 0.4,
            'lai': 3.0,
            'temperature': 15.0,
            'humidity': 0.65,
            'wind_speed': 3.0,
            'radiation': 400.0,
        }
        return float(defaults.get(var_name, 0.0))

    def _get_default_array(self, var_name: str, height: int, width: int) -> np.ndarray:
        """Get default values for missing variables - REALISTIC DEFAULTS"""
        return np.full((height, width), self._get_default_scalar(var_name), dtype=np.float32)

    def _run_blockwise_baitsss(self, variables: Dict[str, np.ndarray],
                               state: Dict[str, np.ndarray],
                               height: int, width: int) -> Optional[Dict[str, np.ndarray]]:
        """
        Run BAITSSS algorithm using block-wise processing
        Coordinates with BAITSSSAlgorithm for pure physics
        """
        try:
            print(f"    🔧 Starting block-wise BAITSSS processing...")
            print(f"    📐 Raster size: {height}x{width} pixels")
            print(f"    🧱 Block size: {self.block_size}x{self.block_size}")

            et_hourly = np.zeros((height, width), dtype=np.float32)
            new_soil_surface = state['soil_moisture_surface'].copy()
            new_soil_root = state['soil_moisture_root'].copy()
            precipitation_hour = variables.get('precipitation', np.zeros((height, width), dtype=np.float32))
            irrigation_hour = np.zeros((height, width), dtype=np.float32)

            blocks_processed = 0
            total_blocks = ((height + self.block_size - 1) // self.block_size) * \
                            ((width + self.block_size - 1) // self.block_size)

            print(f"    📊 Total blocks to process: {total_blocks}")

            for b_i in range(0, height, self.block_size):
                for b_j in range(0, width, self.block_size):
                    block_height = min(self.block_size, height - b_i)
                    block_width = min(self.block_size, width - b_j)

                    block_vars = self._extract_block_data(variables, state, b_i, b_j, block_height, block_width)

                    block_results = self.baitsss.process_block(block_vars, block_height, block_width)
                    if block_results is not None:
                        et_hourly[b_i:b_i+block_height, b_j:b_j+block_width] = block_results['et_hour']
                        new_soil_surface[b_i:b_i+block_height, b_j:b_j+block_width] = block_results['soil_surface']
                        new_soil_root[b_i:b_i+block_height, b_j:b_j+block_width] = block_results['soil_root']
                        irrigation_hour[b_i:b_i+block_height, b_j:b_j+block_width] = block_results['irrigation']
                    else:
                        print(f"      ⚠️  Block at ({b_i}, {b_j}) failed - using defaults")
                        et_hourly[b_i:b_i+block_height, b_j:b_j+block_width] = 2.0  # mm/hour default

                    blocks_processed += 1
                    if blocks_processed % 10 == 0 or blocks_processed == total_blocks:
                        progress = 100 * blocks_processed / total_blocks
                        print(f"      🔄 Progress: {progress:.1f}% ({blocks_processed}/{total_blocks} blocks)")

            # Summaries
            et_valid = np.sum((et_hourly > 0) & (et_hourly < 50))
            et_mean = float(np.mean(et_hourly[et_hourly > 0])) if np.any(et_hourly > 0) else 0.0
            print("    📈 ET Results Summary:")
            print(f"      Valid ET pixels: {et_valid:,}")
            print(f"      Mean hourly ET: {et_mean:.3f} mm/hour")
            print(f"      ET range: {np.min(et_hourly):.3f} - {np.max(et_hourly):.3f} mm/hour")

            # Update cumulative state
            updated_state = {
                'et_cumulative': state['et_cumulative'] + et_hourly,
                'precip_cumulative': state['precip_cumulative'] + precipitation_hour,
                'irrigation_cumulative': state['irrigation_cumulative'] + irrigation_hour,
                'soil_moisture_surface': new_soil_surface,
                'soil_moisture_root': new_soil_root,
            }

            print("    ✅ Block-wise processing completed successfully!")
            return updated_state

        except Exception as e:
            print(f"    💥 Block-wise processing failed: {e}")
            import traceback
            traceback.print_exc()
            return None

    def _extract_block_data(self, variables: Dict[str, np.ndarray], state: Dict[str, np.ndarray],
                            b_i: int, b_j: int, block_height: int, block_width: int) -> Dict[str, np.ndarray]:
        """Extract block data for BAITSSS processing"""
        block_vars: Dict[str, np.ndarray] = {}

        for var_name, var_data in variables.items():
            block_vars[var_name] = var_data[b_i:b_i+block_height, b_j:b_j+block_width]

        # Add previous state for temporal continuity
        block_vars['soil_moisture_surface_prev'] = state['soil_moisture_surface'][b_i:b_i+block_height, b_j:b_j+block_width]
        block_vars['soil_moisture_root_prev'] = state['soil_moisture_root'][b_i:b_i+block_height, b_j:b_j+block_width]

        return block_vars

    def _create_enhanced_output(self, input_data: np.ndarray, state: Dict[str, np.ndarray]) -> np.ndarray:
        """Create enhanced output with original bands + BAITSSS results"""
        enhanced_bands = [
            input_data,
            state['et_cumulative'][np.newaxis, :, :],
            state['precip_cumulative'][np.newaxis, :, :],
            state['irrigation_cumulative'][np.newaxis, :, :],
            state['soil_moisture_surface'][np.newaxis, :, :],
            state['soil_moisture_root'][np.newaxis, :, :],
        ]
        return np.concatenate(enhanced_bands, axis=0)

    def _save_enhanced_file(self, enhanced_data: np.ndarray, output_path: str, profile: dict):
        """Save enhanced raster file"""
        profile.update({
            'count': enhanced_data.shape[0],
            'dtype': 'float32',
            'nodata': -9999,
            'compress': 'lzw',
        })
        with rasterio.open(output_path, 'w', **profile) as dst:
            dst.write(enhanced_data)

    def _create_comprehensive_et_summary(self, output_dir: str):
        """Create comprehensive ET summary maps and statistics"""
        try:
            print("📊 Creating comprehensive ET summary...")

            enhanced_files = sorted(glob.glob(os.path.join(output_dir, "*_enhanced.tif")))
            if not enhanced_files:
                print("❌ No enhanced files found for summary")
                return

            # Group files by day
            daily_groups = self._group_files_by_day(enhanced_files)

            # Create daily summaries
            daily_et_files: List[str] = []
            for date_str, files in daily_groups.items():
                daily_et_path = self._create_daily_et_summary(files, output_dir, date_str)
                if daily_et_path:
                    daily_et_files.append(daily_et_path)

            # Create final period summary
            if daily_et_files:
                self._create_final_et_summary(daily_et_files, output_dir)

            # Create comprehensive JSON summary
            self._create_json_summary(output_dir, enhanced_files)

            print("🎯 Comprehensive ET summary completed!")

        except Exception as e:
            print(f"💥 Error creating ET summary: {e}")

    def _group_files_by_day(self, enhanced_files: List[str]) -> Dict[str, List[str]]:
        """Group enhanced files by day"""
        daily_groups: Dict[str, List[str]] = {}
        for file_path in enhanced_files:
            filename = os.path.basename(file_path)
            date_part = filename.split('_')[0]  # Extract YYYY-MM-DD
            daily_groups.setdefault(date_part, []).append(file_path)
        return daily_groups

    def _create_daily_et_summary(self, hourly_files: List[str], output_dir: str, date_str: str) -> Optional[str]:
        """Create daily ET summary from hourly files"""
        try:
            et_arrays: List[np.ndarray] = []
            template_file = hourly_files[0]

            for file_path in hourly_files:
                with rasterio.open(file_path) as src:
                    original_bands = src.count - 5  # 5 appended BAITSSS layers
                    et_data = src.read(original_bands + 1)  # et_cumulative band (1-indexed)
                    et_arrays.append(et_data)

            et_stack = np.stack(et_arrays, axis=0)
            daily_et = et_stack[-1]  # Last hour cumulative

            daily_et_path = os.path.join(output_dir, f"ET_daily_{date_str}.tif")
            with rasterio.open(template_file) as template:
                profile = template.profile.copy()
                profile.update({'count': 1, 'dtype': 'float32', 'nodata': -9999})
                with rasterio.open(daily_et_path, 'w', **profile) as dst:
                    dst.write(daily_et, 1)
                    dst.set_band_description(1, f"Daily ET Sum - {date_str}")

            print(f"    📅 Created daily summary: {date_str}")
            return daily_et_path

        except Exception as e:
            print(f"    💥 Error creating daily summary for {date_str}: {e}")
            return None

    def _create_final_et_summary(self, daily_et_files: List[str], output_dir: str):
        """Create final period ET summary"""
        try:
            print("🎯 Creating final ET summary...")

            daily_arrays: List[np.ndarray] = []
            template_file = daily_et_files[0]

            for file_path in daily_et_files:
                with rasterio.open(file_path) as src:
                    daily_et = src.read(1)
                    daily_arrays.append(daily_et)

            daily_stack = np.stack(daily_arrays, axis=0)
            valid_mask = daily_stack != -9999
            mean_et = np.full(daily_stack.shape[1:], -9999.0, dtype=np.float32)

            # pixelwise mean over valid values
            for i in range(daily_stack.shape[1]):
                for j in range(daily_stack.shape[2]):
                    vals = daily_stack[:, i, j]
                    vals = vals[valid_mask[:, i, j]]
                    if vals.size > 0:
                        mean_et[i, j] = float(np.mean(vals))

            final_et_tif = os.path.join(output_dir, "ET_final_result.tif")
            with rasterio.open(template_file) as template:
                profile = template.profile.copy()
                profile.update({'count': 1, 'dtype': 'float32', 'nodata': -9999})
                bounds = template.bounds  # used for PNG extent
                with rasterio.open(final_et_tif, 'w', **profile) as dst:
                    dst.write(mean_et, 1)
                    dst.set_band_description(1, f"Mean ET over {len(daily_et_files)} days")

            # Visualization with geographic extent
            try:
                self._create_et_visualization(mean_et, output_dir, bounds)
            except ImportError:
                print("    ⚠️  Matplotlib not available - skipping PNG creation")

            print(f"    🎯 Final ET TIF created: {final_et_tif}")

        except Exception as e:
            print(f"    💥 Error creating final summary: {e}")

    def _create_et_visualization(self, et_data: np.ndarray, output_dir: str, bounds=None):
        """Create PNG visualization with correct geographic extent and aspect."""
        import matplotlib.pyplot as plt

        et_masked = np.ma.masked_where(et_data == -9999, et_data)

        if bounds is not None:
            minx, miny, maxx, maxy = bounds.left, bounds.bottom, bounds.right, bounds.top
            extent = [minx, maxx, miny, maxy]
            mean_lat = 0.5 * (miny + maxy)
            aspect = 1.0 / np.cos(np.deg2rad(mean_lat))
        else:
            h, w = et_data.shape
            extent = [0, w, 0, h]
            aspect = 'equal'

        plt.figure(figsize=(10, 7))
        cmap = plt.cm.viridis
        cmap.set_bad(alpha=0.0)

        im = plt.imshow(
            et_masked,
            cmap=cmap,
            interpolation='nearest',
            extent=extent,
            origin='upper',
            aspect=aspect,
        )
        plt.colorbar(im, label='ET (mm/day)', shrink=0.8)
        plt.title('BAITSSS Evapotranspiration Map', fontsize=13, weight='bold')
        plt.xlabel('Longitude' if bounds is not None else '')
        plt.ylabel('Latitude' if bounds is not None else '')
        plt.grid(True, linewidth=0.3, alpha=0.3)

        png_path = os.path.join(output_dir, "ET_final_result.png")
        plt.savefig(png_path, dpi=220, bbox_inches='tight', facecolor='white')
        plt.close()
        print(f"    🖼️  ET visualization created: {png_path}")

    def _create_json_summary(self, output_dir: str, enhanced_files: List[str]):
        """Create comprehensive JSON summary"""
        try:
            final_tif = os.path.join(output_dir, "ET_final_result.tif")

            summary = {
                'processing_info': {
                    'algorithm': 'BAITSSS (Modular Implementation)',
                    'processing_date': datetime.now().isoformat(),
                    'block_size': self.block_size,
                    'temporal_continuity': True,
                    'total_enhanced_files': len(enhanced_files),
                    'physics_module': 'BAITSSSAlgorithm',
                    'workflow_module': 'ETAlgorithm',
                },
                'output_files': {
                    'final_et_map': final_tif,
                    'format': 'GeoTIFF',
                    'units': 'mm/day',
                    'description': 'Mean ET over processing period',
                },
            }

            if os.path.exists(final_tif):
                with rasterio.open(final_tif) as src:
                    et_data = src.read(1)
                    valid_data = et_data[et_data != -9999]
                    if valid_data.size > 0:
                        summary['statistics'] = {
                            'min_et_mm_day': float(np.min(valid_data)),
                            'max_et_mm_day': float(np.max(valid_data)),
                            'mean_et_mm_day': float(np.mean(valid_data)),
                            'median_et_mm_day': float(np.median(valid_data)),
                            'std_et_mm_day': float(np.std(valid_data)),
                            'valid_pixels': int(valid_data.size),
                            'total_pixels': int(et_data.size),
                            'coverage_percent': float(100 * valid_data.size / et_data.size),
                        }

            summary_path = os.path.join(output_dir, "ET_comprehensive_summary.json")
            with open(summary_path, 'w') as f:
                json.dump(summary, f, indent=2)
            print(f"    📋 JSON summary created: {summary_path}")

        except Exception as e:
            print(f"    💥 Error creating JSON summary: {e}")


class ETResultsManager:
    """
    Manages ET results and creates summary reports (legacy compatibility)
    """

    @staticmethod
    def create_comprehensive_et_summary(output_dir: str, request_data: Dict):
        et_algorithm = ETAlgorithm()
        et_algorithm._create_json_summary(output_dir, [])
