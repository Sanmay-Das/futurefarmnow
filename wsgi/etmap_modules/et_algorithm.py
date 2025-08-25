#!/usr/bin/env python3
"""
ET Algorithm - Main Processing Workflow
Handles file I/O, temporal continuity, and block coordination
Uses BAITSSSAlgorithm for pure physics calculations
"""

import os
import glob
import numpy as np
import rasterio
from typing import Dict, List, Optional
from datetime import datetime, timedelta
import json
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
        """
        Main workflow: Process hourly files with temporal continuity
        """
        print(f"🚀 Starting BAITSSS ET Processing...")
        print(f"📂 Input: {hourly_files_dir}")
        print(f"📁 Output: {output_dir}")
        
        try:
            # Get sorted hourly files
            hourly_files = self._get_sorted_hourly_files(hourly_files_dir)
            
            if not hourly_files:
                print(f"❌ ERROR: No hourly files found in {hourly_files_dir}")
                return False
            
            print(f"📊 Found {len(hourly_files)} hourly files to process")
            os.makedirs(output_dir, exist_ok=True)
            
            # Process with temporal continuity
            processed_count = self._process_hourly_sequence(hourly_files, output_dir)
            
            print(f"\n🎉 Processing Summary:")
            print(f"   ✅ Successfully processed: {processed_count}/{len(hourly_files)} files")
            
            if processed_count > 0:
                self._create_comprehensive_et_summary(output_dir)
                return True
            else:
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
            
            # Process this hour with state from previous hour
            current_state = self._process_single_hourly_file(
                hourly_file, output_dir, previous_state
            )
            
            if current_state is not None:
                processed_count += 1
                previous_state = current_state  # Temporal continuity!
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
                input_data = src.read()
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
                updated_state = self._run_blockwise_baitsss(
                    variables, current_state, height, width
                )
                
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
            'soil_moisture_root': np.full((height, width), 0.3, dtype=np.float32)
        }

    def _extract_variables_from_bands(self, input_data: np.ndarray) -> Dict[str, np.ndarray]:
        """Extract variables from input raster bands - CORRECTED BAND ORDER"""
        # CORRECTED band mapping based on your actual hourly file structure
        # From your logs: 11 bands per hourly file
        band_mapping = {
            0: 'soil_awc',           # Band 1: Static soil available water capacity
            1: 'soil_fc',            # Band 2: Static soil field capacity  
            2: 'elevation',          # Band 3: Static elevation
            3: 'nlcd',               # Band 4: Static NLCD land cover
            4: 'precipitation',      # Band 5: PRISM precipitation
            5: 'ndvi',               # Band 6: Landsat NDVI
            6: 'lai',                # Band 7: Landsat LAI
            7: 'temperature',        # Band 8: NLDAS temperature
            8: 'humidity',           # Band 9: NLDAS humidity
            9: 'wind_speed',         # Band 10: NLDAS wind speed
            10: 'radiation'          # Band 11: NLDAS radiation
        }
        
        print(f"    📊 Extracting variables from {input_data.shape[0]} input bands...")
        
        variables = {}
        for band_idx, var_name in band_mapping.items():
            if band_idx < input_data.shape[0]:
                variables[var_name] = input_data[band_idx]
                print(f"      Band {band_idx+1}: {var_name} - Shape: {input_data[band_idx].shape}")
            else:
                # Provide defaults for missing bands
                variables[var_name] = self._get_default_array(
                    var_name, input_data.shape[1], input_data.shape[2]
                )
                print(f"      Band {band_idx+1}: {var_name} - Using defaults")
        
        # Validate extracted data
        print(f"    🔍 Data validation:")
        for var_name, var_data in variables.items():
            valid_pixels = np.sum(~np.isnan(var_data) & (var_data != -9999))
            print(f"      {var_name}: {valid_pixels:,} valid pixels")
        
        return variables

    def _get_default_array(self, var_name: str, height: int, width: int) -> np.ndarray:
        """Get default values for missing variables - REALISTIC DEFAULTS"""
        # Improved defaults based on typical values
        defaults = {
            'soil_awc': 0.15,        # 15% available water capacity
            'soil_fc': 35.0,         # 35% field capacity (will be converted to fraction)
            'elevation': 200.0,      # 200m elevation (reasonable for California)
            'nlcd': 42.0,           # Evergreen forest (common NLCD class)
            'precipitation': 0.0,    # No precipitation (hourly)
            'ndvi': 0.4,            # Moderate vegetation
            'lai': 3.0,             # Reasonable leaf area index
            'temperature': 15.0,     # 15°C (reasonable for California)
            'humidity': 0.65,       # 65% relative humidity
            'wind_speed': 3.0,      # 3 m/s wind speed
            'radiation': 400.0      # 400 W/m² solar radiation
        }
        
        default_val = defaults.get(var_name, 0.0)
        return np.full((height, width), default_val, dtype=np.float32)

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
            
            # Initialize output arrays
            et_hourly = np.zeros((height, width), dtype=np.float32)
            new_soil_surface = state['soil_moisture_surface'].copy()
            new_soil_root = state['soil_moisture_root'].copy()
            precipitation_hour = variables.get('precipitation', np.zeros((height, width)))
            irrigation_hour = np.zeros((height, width), dtype=np.float32)
            
            blocks_processed = 0
            total_blocks = ((height + self.block_size - 1) // self.block_size) * \
                          ((width + self.block_size - 1) // self.block_size)
            
            print(f"    📊 Total blocks to process: {total_blocks}")
            
            # Process in blocks
            for b_i in range(0, height, self.block_size):
                for b_j in range(0, width, self.block_size):
                    # Calculate actual block dimensions
                    block_height = min(self.block_size, height - b_i)
                    block_width = min(self.block_size, width - b_j)
                    
                    # Extract block variables
                    block_vars = self._extract_block_data(
                        variables, state, b_i, b_j, block_height, block_width
                    )
                    
                    # Use BAITSSS physics module for this block
                    block_results = self.baitsss.process_block(block_vars, block_height, block_width)
                    
                    if block_results is not None:
                        # Store results back to full arrays
                        et_hourly[b_i:b_i+block_height, b_j:b_j+block_width] = block_results['et_hour']
                        new_soil_surface[b_i:b_i+block_height, b_j:b_j+block_width] = block_results['soil_surface']
                        new_soil_root[b_i:b_i+block_height, b_j:b_j+block_width] = block_results['soil_root']
                        irrigation_hour[b_i:b_i+block_height, b_j:b_j+block_width] = block_results['irrigation']
                    else:
                        print(f"      ⚠️  Block at ({b_i}, {b_j}) failed - using defaults")
                        # Fill with default values
                        et_hourly[b_i:b_i+block_height, b_j:b_j+block_width] = 2.0  # 2 mm/day default
                        # Soil moisture remains unchanged
                    
                    blocks_processed += 1
                    if blocks_processed % 10 == 0 or blocks_processed == total_blocks:
                        progress = 100 * blocks_processed / total_blocks
                        print(f"      🔄 Progress: {progress:.1f}% ({blocks_processed}/{total_blocks} blocks)")
            
            # Validate results
            et_valid = np.sum((et_hourly > 0) & (et_hourly < 50))  # Reasonable ET range
            et_mean = np.mean(et_hourly[et_hourly > 0]) if np.any(et_hourly > 0) else 0
            
            print(f"    📈 ET Results Summary:")
            print(f"      Valid ET pixels: {et_valid:,}")
            print(f"      Mean hourly ET: {et_mean:.3f} mm/hour")
            print(f"      ET range: {np.min(et_hourly):.3f} - {np.max(et_hourly):.3f} mm/hour")
            
            # Update cumulative state (temporal continuity)
            updated_state = {
                'et_cumulative': state['et_cumulative'] + et_hourly,
                'precip_cumulative': state['precip_cumulative'] + precipitation_hour,
                'irrigation_cumulative': state['irrigation_cumulative'] + irrigation_hour,
                'soil_moisture_surface': new_soil_surface,
                'soil_moisture_root': new_soil_root
            }
            
            print(f"    ✅ Block-wise processing completed successfully!")
            return updated_state
            
        except Exception as e:
            print(f"    💥 Block-wise processing failed: {e}")
            import traceback
            traceback.print_exc()
            return None

    def _extract_block_data(self, variables: Dict[str, np.ndarray], 
                           state: Dict[str, np.ndarray],
                           b_i: int, b_j: int, block_height: int, block_width: int) -> Dict[str, np.ndarray]:
        """Extract block data for BAITSSS processing"""
        block_vars = {}
        
        # Extract block from each variable
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
            state['soil_moisture_root'][np.newaxis, :, :]
        ]
        
        return np.concatenate(enhanced_bands, axis=0)

    def _save_enhanced_file(self, enhanced_data: np.ndarray, output_path: str, profile: dict):
        """Save enhanced raster file"""
        profile.update({
            'count': enhanced_data.shape[0],
            'dtype': 'float32',
            'nodata': -9999,
            'compress': 'lzw'
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
            daily_et_files = []
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
        daily_groups = {}
        for file_path in enhanced_files:
            filename = os.path.basename(file_path)
            date_part = filename.split('_')[0]  # Extract YYYY-MM-DD
            if date_part not in daily_groups:
                daily_groups[date_part] = []
            daily_groups[date_part].append(file_path)
        return daily_groups

    def _create_daily_et_summary(self, hourly_files: List[str], output_dir: str, date_str: str) -> Optional[str]:
        """Create daily ET summary from hourly files"""
        try:
            et_arrays = []
            template_file = hourly_files[0]
            
            for file_path in hourly_files:
                with rasterio.open(file_path) as src:
                    # ET cumulative is first BAITSSS band (after original bands)
                    original_bands = src.count - 5
                    et_data = src.read(original_bands + 1)  # 1-indexed
                    et_arrays.append(et_data)
            
            # Calculate daily sum (last hour's cumulative for the day)
            et_stack = np.stack(et_arrays, axis=0)
            daily_et = et_stack[-1]  # Last hour has the daily cumulative
            
            # Save daily ET
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
            
            daily_arrays = []
            template_file = daily_et_files[0]
            
            for file_path in daily_et_files:
                with rasterio.open(file_path) as src:
                    daily_et = src.read(1)
                    daily_arrays.append(daily_et)
            
            # Calculate mean ET over period
            daily_stack = np.stack(daily_arrays, axis=0)
            valid_mask = daily_stack != -9999
            mean_et = np.full(daily_stack.shape[1:], -9999.0, dtype=np.float32)
            
            # Calculate mean only where we have valid data
            for i in range(daily_stack.shape[1]):
                for j in range(daily_stack.shape[2]):
                    pixel_values = daily_stack[:, i, j]
                    valid_values = pixel_values[valid_mask[:, i, j]]
                    if len(valid_values) > 0:
                        mean_et[i, j] = np.mean(valid_values)
            
            # Save final ET results
            final_et_tif = os.path.join(output_dir, "ET_final_result.tif")
            with rasterio.open(template_file) as template:
                profile = template.profile.copy()
                profile.update({'count': 1, 'dtype': 'float32', 'nodata': -9999})
                
                with rasterio.open(final_et_tif, 'w', **profile) as dst:
                    dst.write(mean_et, 1)
                    dst.set_band_description(1, f"Mean ET over {len(daily_et_files)} days")
            
            # Create visualization if matplotlib available
            try:
                self._create_et_visualization(mean_et, output_dir)
            except ImportError:
                print("    ⚠️  Matplotlib not available - skipping PNG creation")
            
            print(f"    🎯 Final ET TIF created: {final_et_tif}")
            
        except Exception as e:
            print(f"    💥 Error creating final summary: {e}")

    def _create_et_visualization(self, et_data: np.ndarray, output_dir: str):
        """Create PNG visualization of ET map"""
        try:
            import matplotlib.pyplot as plt
            
            et_masked = np.ma.masked_where(et_data == -9999, et_data)
            
            plt.figure(figsize=(12, 8))
            cmap = plt.cm.viridis
            cmap.set_bad('white', 1.0)
            
            im = plt.imshow(et_masked, cmap=cmap, interpolation='nearest')
            plt.colorbar(im, label='ET (mm/day)', shrink=0.8)
            plt.title('BAITSSS Evapotranspiration Map', fontsize=14, fontweight='bold')
            plt.axis('off')
            
            png_path = os.path.join(output_dir, "ET_final_result.png")
            plt.savefig(png_path, dpi=300, bbox_inches='tight', facecolor='white')
            plt.close()
            
            print(f"    🖼️  ET visualization created: {png_path}")
            
        except Exception as e:
            print(f"    💥 Error creating PNG: {e}")

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
                    'workflow_module': 'ETAlgorithm'
                },
                'output_files': {
                    'final_et_map': final_tif,
                    'format': 'GeoTIFF',
                    'units': 'mm/day',
                    'description': 'Mean ET over processing period'
                }
            }
            
            # Calculate statistics if final file exists
            if os.path.exists(final_tif):
                with rasterio.open(final_tif) as src:
                    et_data = src.read(1)
                    valid_data = et_data[et_data != -9999]
                    
                    if len(valid_data) > 0:
                        summary['statistics'] = {
                            'min_et_mm_day': float(np.min(valid_data)),
                            'max_et_mm_day': float(np.max(valid_data)),
                            'mean_et_mm_day': float(np.mean(valid_data)),
                            'median_et_mm_day': float(np.median(valid_data)),
                            'std_et_mm_day': float(np.std(valid_data)),
                            'valid_pixels': int(len(valid_data)),
                            'total_pixels': int(et_data.size),
                            'coverage_percent': float(100 * len(valid_data) / et_data.size)
                        }
            
            # Save summary
            summary_path = os.path.join(output_dir, "ET_comprehensive_summary.json")
            with open(summary_path, 'w') as f:
                json.dump(summary, f, indent=2)
            
            print(f"    📋 JSON summary created: {summary_path}")
            
        except Exception as e:
            print(f"    💥 Error creating JSON summary: {e}")


class ETResultsManager:
    """
    Manages ET results and creates summary reports
    Legacy compatibility class
    """
    
    @staticmethod
    def create_comprehensive_et_summary(output_dir: str, request_data: Dict):
        """Create comprehensive summary report of ET results"""
        et_algorithm = ETAlgorithm()
        et_algorithm._create_json_summary(output_dir, [])