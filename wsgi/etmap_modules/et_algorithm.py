import os
import glob
import numpy as np
import rasterio
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
import json
from .baitsss_algorithm import BAITSSSAlgorithm
from .config import ETMapConfig
from .utils import FileManager, LoggingUtils


class ETAlgorithm:
    def __init__(self, debug: bool = False):
        self.block_size = 200
        self.baitsss = BAITSSSAlgorithm()  # Pure physics module
        self.debug = debug
        
        if self.debug:
            print("DEBUG MODE ENABLED: Will save all intermediate enhanced files to disk")
        else:
            print("NORMAL MODE: Intermediate files kept in memory only")
        
    def create_enhanced_hourly_files_with_et(self, hourly_files_dir: str, output_dir: str) -> bool:
        mode_str = "DEBUG" if self.debug else "OPTIMIZED"
        print(f"Starting BAITSSS ET Processing ({mode_str} MODE)...")
        print(f"Input: {hourly_files_dir}")
        print(f"Output: {output_dir}")
        
        if not self.debug:
            print(f"Note: Intermediate files kept in memory, only final result saved to disk")
        else:
            print(f"Note: All intermediate enhanced files will be saved for debugging")
        
        try:
            # Get sorted hourly files
            hourly_files = self._get_sorted_hourly_files(hourly_files_dir)
            
            if not hourly_files:
                print(f"ERROR: No hourly files found in {hourly_files_dir}")
                return False
            
            print(f"Found {len(hourly_files)} hourly files to process")
            os.makedirs(output_dir, exist_ok=True)
            
            # Process based on debug mode
            if self.debug:
                processed_count = self._process_hourly_sequence_with_disk_save(hourly_files, output_dir)
                success = processed_count > 0
                if success:
                    self._create_comprehensive_et_summary(output_dir)
            else:
                enhanced_data_list = self._process_hourly_sequence_in_memory(hourly_files)
                success = len(enhanced_data_list) > 0
                if success:
                    success = self._create_final_result_from_memory(enhanced_data_list, output_dir)
            
            if success:
                # Create JSON summary
                files_processed = processed_count if self.debug else len(enhanced_data_list)
                self._create_json_summary(output_dir, files_processed)
                print(f"✓ Final ET result saved to: {output_dir}/ET_final_result.tif")
                
                if self.debug:
                    print(f"✓ All intermediate enhanced files saved in: {output_dir}/")
                
                return True
            else:
                print("ERROR: Processing failed")
                return False
                
        except Exception as e:
            print(f"ERROR: ET processing failed: {e}")
            import traceback
            traceback.print_exc()
            return False

    def _get_sorted_hourly_files(self, hourly_files_dir: str) -> List[str]:
        """Get hourly files sorted chronologically"""
        pattern = os.path.join(hourly_files_dir, "*.tif")
        files = glob.glob(pattern)
        return sorted(files, key=lambda x: os.path.basename(x))

    def _process_hourly_sequence_with_disk_save(self, hourly_files: List[str], output_dir: str) -> int:
        previous_state = None
        processed_count = 0
        
        print("🐛 DEBUG MODE: Saving each enhanced file to disk...")
        
        for i, hourly_file in enumerate(hourly_files):
            filename = os.path.basename(hourly_file)
            print(f"Processing hour {i+1}/{len(hourly_files)}: {filename}")
            
            # Process this hour with state from previous hour
            current_state = self._process_single_hourly_file_with_disk_save(
                hourly_file, output_dir, previous_state
            )
            
            if current_state is not None:
                processed_count += 1
                previous_state = current_state  # Temporal continuity!
                print(f"✓ Completed: {filename} (saved to disk)")
            else:
                print(f"✗ Failed: {filename}")
        
        print(f"DEBUG MODE: Processed {processed_count} files, all saved to disk")
        return processed_count

    def _process_single_hourly_file_with_disk_save(self, hourly_file: str, output_dir: str, 
                                                  previous_state: Optional[Dict]) -> Optional[Dict]:
        try:
            filename = os.path.basename(hourly_file)
            output_filename = filename.replace('.tif', '_enhanced.tif')
            output_path = os.path.join(output_dir, output_filename)
            
            with rasterio.open(hourly_file) as src:
                input_data = src.read()
                height, width = input_data.shape[1], input_data.shape[2]
                profile = src.profile
                
                print(f"Processing {height}x{width} pixels in blocks...")
                
                # Initialize or use previous state
                if previous_state is None:
                    print("Initializing first hour state...")
                    current_state = self._initialize_et_state(height, width)
                else:
                    current_state = {k: v.copy() for k, v in previous_state.items()}
                    print("Using previous hour state...")
                
                # Extract variables from input bands
                variables = self._extract_variables_from_bands(input_data)
                
                # Process using block-wise BAITSSS
                updated_state = self._run_blockwise_baitsss(
                    variables, current_state, height, width
                )
                
                if updated_state is None:
                    return None
                
                # Create and save enhanced output to disk
                enhanced_data = self._create_enhanced_output(input_data, updated_state)
                self._save_enhanced_file(enhanced_data, output_path, profile)
                
                print(f"🐛 DEBUG: Saved {output_filename} ({enhanced_data.shape[0]} bands)")
                return updated_state
                
        except Exception as e:
            print(f"Error processing {hourly_file}: {e}")
            return None

    def _save_enhanced_file(self, enhanced_data: np.ndarray, output_path: str, profile: dict):
        """Save enhanced raster file to disk (DEBUG MODE)"""
        profile.update({
            'count': enhanced_data.shape[0],
            'dtype': 'float32',
            'nodata': -9999,
            'compress': 'lzw'
        })
        
        with rasterio.open(output_path, 'w', **profile) as dst:
            dst.write(enhanced_data)

    def _process_hourly_sequence_in_memory(self, hourly_files: List[str]) -> List[Dict]:
        previous_state = None
        enhanced_data_list = []
        
        print("🚀 NORMAL MODE: Processing files in memory...")
        
        for i, hourly_file in enumerate(hourly_files):
            filename = os.path.basename(hourly_file)
            print(f"Processing hour {i+1}/{len(hourly_files)}: {filename}")
            
            # Process this hour with state from previous hour
            result = self._process_single_hourly_file_in_memory(
                hourly_file, previous_state, filename
            )
            
            if result is not None:
                enhanced_data, current_state = result
                enhanced_data_list.append(enhanced_data)
                previous_state = current_state  # Temporal continuity!
                print(f"✓ Completed: {filename} (kept in memory)")
            else:
                print(f"✗ Failed: {filename}")
        
        print(f"NORMAL MODE: Processed {len(enhanced_data_list)} files in memory")
        return enhanced_data_list

    def _process_single_hourly_file_in_memory(self, hourly_file: str, 
                                            previous_state: Optional[Dict], 
                                            filename: str) -> Optional[Tuple[Dict, Dict]]:
        try:
            with rasterio.open(hourly_file) as src:
                input_data = src.read()
                height, width = input_data.shape[1], input_data.shape[2]
                profile = src.profile.copy()
                
                print(f"Processing {height}x{width} pixels in blocks...")
                
                # Initialize or use previous state
                if previous_state is None:
                    print("Initializing first hour state...")
                    current_state = self._initialize_et_state(height, width)
                else:
                    current_state = {k: v.copy() for k, v in previous_state.items()}
                    print("Using previous hour state...")
                
                # Extract variables from input bands
                variables = self._extract_variables_from_bands(input_data)
                
                # Process using block-wise BAITSSS
                updated_state = self._run_blockwise_baitsss(
                    variables, current_state, height, width
                )
                
                if updated_state is None:
                    return None
                
                # Create enhanced output data (but don't save to disk)
                enhanced_data_array = self._create_enhanced_output(input_data, updated_state)
                
                # Store enhanced data in memory with metadata
                enhanced_data_dict = {
                    'filename': filename,
                    'data': enhanced_data_array,
                    'profile': profile,
                    'timestamp': datetime.now().isoformat(),
                    'shape': enhanced_data_array.shape,
                    'bands': enhanced_data_array.shape[0]
                }
                
                print(f"Processed: {filename} ({enhanced_data_array.shape[0]} bands) - stored in memory")
                return enhanced_data_dict, updated_state
                
        except Exception as e:
            print(f"Error processing {hourly_file}: {e}")
            return None

    def _create_final_result_from_memory(self, enhanced_data_list: List[Dict], output_dir: str) -> bool:
        try:
            print("Creating final ET result from in-memory data...")
            
            if not enhanced_data_list:
                print("ERROR: No enhanced data available")
                return False
            
            # Group by day and calculate daily summaries in memory
            daily_summaries = self._calculate_daily_summaries_from_memory(enhanced_data_list)
            
            if not daily_summaries:
                print("ERROR: No daily summaries calculated")
                return False
            
            # Calculate final mean ET over the period
            final_et_data = self._calculate_final_mean_et(daily_summaries)
            
            # Save ONLY the final result to disk
            final_et_path = os.path.join(output_dir, "ET_final_result.tif")
            template_profile = enhanced_data_list[0]['profile']
            
            self._save_final_result_to_disk(final_et_data, final_et_path, template_profile)
            
            # Create visualization if possible
            try:
                self._create_et_visualization(final_et_data, output_dir)
            except ImportError:
                print("Matplotlib not available - skipping PNG creation")
            
            print(f"✓ Final ET result saved: {final_et_path}")
            return True
            
        except Exception as e:
            print(f"ERROR: Failed to create final result: {e}")
            import traceback
            traceback.print_exc()
            return False

    def _calculate_daily_summaries_from_memory(self, enhanced_data_list: List[Dict]) -> Dict[str, np.ndarray]:
        """Calculate daily ET summaries from in-memory enhanced data"""
        try:
            print("Calculating daily summaries from memory...")
            
            # Group files by day
            daily_groups = {}
            for enhanced_data in enhanced_data_list:
                filename = enhanced_data['filename']
                date_part = filename.split('_')[0]  # Extract YYYY-MM-DD
                if date_part not in daily_groups:
                    daily_groups[date_part] = []
                daily_groups[date_part].append(enhanced_data)
            
            daily_summaries = {}
            
            for date_str, day_data_list in daily_groups.items():
                print(f"Processing day: {date_str} ({len(day_data_list)} hours)")
                
                # Extract ET cumulative data from each hour
                et_arrays = []
                for enhanced_data in day_data_list:
                    data_array = enhanced_data['data']
                    # ET cumulative is first BAITSSS band (after original bands)
                    original_bands = data_array.shape[0] - 5
                    et_cumulative = data_array[original_bands]  # 0-indexed
                    et_arrays.append(et_cumulative)
                
                # Use last hour's cumulative as daily total
                et_stack = np.stack(et_arrays, axis=0)
                daily_et = et_stack[-1]  # Last hour has the daily cumulative
                daily_summaries[date_str] = daily_et
                
                print(f"✓ Daily summary for {date_str}: {np.mean(daily_et[daily_et > 0]):.3f} mm/day average")
            
            return daily_summaries
            
        except Exception as e:
            print(f"ERROR: Failed to calculate daily summaries: {e}")
            return {}

    def _calculate_final_mean_et(self, daily_summaries: Dict[str, np.ndarray]) -> np.ndarray:
        """Calculate final mean ET from daily summaries"""
        try:
            print("Calculating final mean ET over period...")
            
            daily_arrays = list(daily_summaries.values())
            daily_stack = np.stack(daily_arrays, axis=0)
            
            # Calculate mean ET over period
            valid_mask = daily_stack != -9999
            mean_et = np.full(daily_stack.shape[1:], -9999.0, dtype=np.float32)
            
            # Calculate mean only where we have valid data
            for i in range(daily_stack.shape[1]):
                for j in range(daily_stack.shape[2]):
                    pixel_values = daily_stack[:, i, j]
                    valid_values = pixel_values[valid_mask[:, i, j]]
                    if len(valid_values) > 0:
                        mean_et[i, j] = np.mean(valid_values)
            
            # Calculate statistics
            valid_data = mean_et[mean_et != -9999]
            if len(valid_data) > 0:
                print(f"Final ET Statistics:")
                print(f"  Days processed: {len(daily_summaries)}")
                print(f"  Valid pixels: {len(valid_data):,}")
                print(f"  Mean ET: {np.mean(valid_data):.3f} mm/day")
                print(f"  Range: {np.min(valid_data):.3f} - {np.max(valid_data):.3f} mm/day")
            
            return mean_et
            
        except Exception as e:
            print(f"ERROR: Failed to calculate final mean ET: {e}")
            return np.array([])

    def _save_final_result_to_disk(self, et_data: np.ndarray, output_path: str, template_profile: dict):
        """Save ONLY the final ET result to disk"""
        try:
            profile = template_profile.copy()
            profile.update({
                'count': 1,
                'dtype': 'float32',
                'nodata': -9999,
                'compress': 'lzw'
            })
            
            with rasterio.open(output_path, 'w', **profile) as dst:
                dst.write(et_data, 1)
                dst.set_band_description(1, "BAITSSS Mean ET over processing period")
            
            print(f"✓ Saved final result: {output_path}")
            
        except Exception as e:
            print(f"ERROR: Failed to save final result: {e}")
            raise

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
        """Extract variables from input raster bands"""
        band_mapping = {
            0: 'soil_awc', 1: 'soil_fc', 2: 'elevation', 3: 'nlcd', 4: 'precipitation',
            5: 'ndvi', 6: 'lai', 7: 'temperature', 8: 'humidity', 9: 'wind_speed', 10: 'radiation'
        }
        
        variables = {}
        for band_idx, var_name in band_mapping.items():
            if band_idx < input_data.shape[0]:
                variables[var_name] = input_data[band_idx]
            else:
                variables[var_name] = self._get_default_array(var_name, input_data.shape[1], input_data.shape[2])
        
        return variables

    def _get_default_array(self, var_name: str, height: int, width: int) -> np.ndarray:
        """Get default values for missing variables"""
        defaults = {
            'soil_awc': 0.15, 'soil_fc': 35.0, 'elevation': 200.0, 'nlcd': 42.0,
            'precipitation': 0.0, 'ndvi': 0.4, 'lai': 3.0, 'temperature': 15.0,
            'humidity': 0.65, 'wind_speed': 3.0, 'radiation': 400.0
        }
        return np.full((height, width), defaults.get(var_name, 0.0), dtype=np.float32)

    def _run_blockwise_baitsss(self, variables: Dict[str, np.ndarray], 
                              state: Dict[str, np.ndarray], 
                              height: int, width: int) -> Optional[Dict[str, np.ndarray]]:
        """Run BAITSSS algorithm using block-wise processing"""
        try:
            et_hourly = np.zeros((height, width), dtype=np.float32)
            new_soil_surface = state['soil_moisture_surface'].copy()
            new_soil_root = state['soil_moisture_root'].copy()
            precipitation_hour = variables.get('precipitation', np.zeros((height, width)))
            irrigation_hour = np.zeros((height, width), dtype=np.float32)
            
            blocks_processed = 0
            total_blocks = ((height + self.block_size - 1) // self.block_size) * \
                          ((width + self.block_size - 1) // self.block_size)
            
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
                        et_hourly[b_i:b_i+block_height, b_j:b_j+block_width] = 2.0
                    
                    blocks_processed += 1
                    if blocks_processed % 10 == 0 or blocks_processed == total_blocks:
                        progress = 100 * blocks_processed / total_blocks
                        print(f"Progress: {progress:.1f}% ({blocks_processed}/{total_blocks} blocks)")
            
            return {
                'et_cumulative': state['et_cumulative'] + et_hourly,
                'precip_cumulative': state['precip_cumulative'] + precipitation_hour,
                'irrigation_cumulative': state['irrigation_cumulative'] + irrigation_hour,
                'soil_moisture_surface': new_soil_surface,
                'soil_moisture_root': new_soil_root
            }
            
        except Exception as e:
            print(f"Block-wise processing failed: {e}")
            return None

    def _extract_block_data(self, variables: Dict[str, np.ndarray], state: Dict[str, np.ndarray],
                           b_i: int, b_j: int, block_height: int, block_width: int) -> Dict[str, np.ndarray]:
        """Extract block data for BAITSSS processing"""
        block_vars = {}
        for var_name, var_data in variables.items():
            block_vars[var_name] = var_data[b_i:b_i+block_height, b_j:b_j+block_width]
        
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

    def _create_comprehensive_et_summary(self, output_dir: str):
        """DEBUG MODE: Create comprehensive ET summary from saved files"""
        try:
            print("Creating comprehensive ET summary from saved files...")
            
            enhanced_files = sorted(glob.glob(os.path.join(output_dir, "*_enhanced.tif")))
            if not enhanced_files:
                print("No enhanced files found for summary")
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
                self._create_final_et_summary_from_files(daily_et_files, output_dir)
            
            print("Comprehensive ET summary completed!")
            
        except Exception as e:
            print(f"Error creating ET summary: {e}")

    def _group_files_by_day(self, enhanced_files: List[str]) -> Dict[str, List[str]]:
        """Group enhanced files by day"""
        daily_groups = {}
        for file_path in enhanced_files:
            filename = os.path.basename(file_path)
            date_part = filename.split('_')[0]
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
                    original_bands = src.count - 5
                    et_data = src.read(original_bands + 1)
                    et_arrays.append(et_data)
            
            et_stack = np.stack(et_arrays, axis=0)
            daily_et = et_stack[-1]
            
            daily_et_path = os.path.join(output_dir, f"ET_daily_{date_str}.tif")
            with rasterio.open(template_file) as template:
                profile = template.profile.copy()
                profile.update({'count': 1, 'dtype': 'float32', 'nodata': -9999})
                
                with rasterio.open(daily_et_path, 'w', **profile) as dst:
                    dst.write(daily_et, 1)
                    dst.set_band_description(1, f"Daily ET Sum - {date_str}")
            
            print(f"Created daily summary: {date_str}")
            return daily_et_path
            
        except Exception as e:
            print(f"Error creating daily summary for {date_str}: {e}")
            return None

    def _create_final_et_summary_from_files(self, daily_et_files: List[str], output_dir: str):
        """Create final period ET summary from daily files"""
        try:
            print("Creating final ET summary...")
            
            daily_arrays = []
            template_file = daily_et_files[0]
            
            for file_path in daily_et_files:
                with rasterio.open(file_path) as src:
                    daily_et = src.read(1)
                    daily_arrays.append(daily_et)
            
            daily_stack = np.stack(daily_arrays, axis=0)
            valid_mask = daily_stack != -9999
            mean_et = np.full(daily_stack.shape[1:], -9999.0, dtype=np.float32)
            
            for i in range(daily_stack.shape[1]):
                for j in range(daily_stack.shape[2]):
                    pixel_values = daily_stack[:, i, j]
                    valid_values = pixel_values[valid_mask[:, i, j]]
                    if len(valid_values) > 0:
                        mean_et[i, j] = np.mean(valid_values)
            
            final_et_tif = os.path.join(output_dir, "ET_final_result.tif")
            with rasterio.open(template_file) as template:
                profile = template.profile.copy()
                profile.update({'count': 1, 'dtype': 'float32', 'nodata': -9999})
                
                with rasterio.open(final_et_tif, 'w', **profile) as dst:
                    dst.write(mean_et, 1)
                    dst.set_band_description(1, f"Mean ET over {len(daily_et_files)} days")
            
            try:
                self._create_et_visualization(mean_et, output_dir)
            except ImportError:
                print("Matplotlib not available - skipping PNG creation")
            
            print(f"Final ET TIF created: {final_et_tif}")
            
        except Exception as e:
            print(f"Error creating final summary: {e}")

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
            
            print(f"✓ ET visualization created: {png_path}")
            
        except Exception as e:
            print(f"Could not create PNG: {e}")

    def _create_json_summary(self, output_dir: str, processed_files_count: int):
        """Create comprehensive JSON summary"""
        try:
            final_tif = os.path.join(output_dir, "ET_final_result.tif")
            
            summary = {
                'processing_info': {
                    'algorithm': f'BAITSSS ({"Debug" if self.debug else "Memory Optimized"} Mode)',
                    'processing_date': datetime.now().isoformat(),
                    'block_size': self.block_size,
                    'temporal_continuity': True,
                    'total_processed_files': processed_files_count,
                    'physics_module': 'BAITSSSAlgorithm',
                    'workflow_module': 'ETAlgorithm',
                    'debug_mode': self.debug,
                    'intermediate_files_saved': self.debug,
                    'memory_optimization': not self.debug,
                    'final_files_saved': ['ET_final_result.tif']
                },
                'output_files': {
                    'final_et_map': final_tif,
                    'format': 'GeoTIFF',
                    'units': 'mm/day',
                    'description': 'Mean ET over processing period'
                }
            }
            
            if self.debug:
                summary['processing_info']['intermediate_files_location'] = output_dir
                summary['processing_info']['note'] = 'All intermediate enhanced files saved for debugging'
            else:
                summary['processing_info']['note'] = 'Intermediate files kept in memory only'
            
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
            
            print(f"JSON summary created: {summary_path}")
            
        except Exception as e:
            print(f"Error creating JSON summary: {e}")


class ETResultsManager:
    @staticmethod
    def create_comprehensive_et_summary(output_dir: str, request_data: Dict):
        """Create comprehensive summary report of ET results"""
        et_algorithm = ETAlgorithm()
        et_algorithm._create_json_summary(output_dir, 0)