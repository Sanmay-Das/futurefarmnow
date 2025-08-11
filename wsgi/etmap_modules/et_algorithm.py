import os
import glob
import numpy as np
import rasterio
from typing import Dict, List, Optional
from datetime import datetime, timedelta
import json
from .config import ETMapConfig
from .utils import FileManager, LoggingUtils


class ETAlgorithm:
    """
    Complete ET Algorithm implementation with BAITSSS model integration
    """
    
    def __init__(self):
        self.constants = {
            'stefan_boltzmann': 5.67e-8,
            'cp': 1013,
            'lambda_v': 2.45e6,
            'gamma': 0.665,
            'albedo_default': 0.23,
            'soil_heat_flux_ratio': 0.1
        }
    
    def create_enhanced_hourly_files_with_et(self, hourly_files_dir: str, output_dir: str) -> bool:
        LoggingUtils.print_step_header("Creating Enhanced Hourly Files with BAITSSS ET")
        
        try:
            # Find all hourly files
            hourly_pattern = os.path.join(hourly_files_dir, "*.tif")
            hourly_files = sorted(glob.glob(hourly_pattern))
            
            if not hourly_files:
                LoggingUtils.print_error(f"No hourly files found in {hourly_files_dir}")
                return False
            
            LoggingUtils.print_success(f"Found {len(hourly_files)} hourly files to enhance with ET")
            
            # Create output directory
            FileManager.ensure_directory_exists(output_dir)
            
            # Process each hourly file to add ET calculations
            processed_count = 0
            
            for hourly_file in hourly_files:
                filename = os.path.basename(hourly_file)
                print(f"Processing {filename}")
                
                # Extract date and hour from filename
                date_str, hour_str = self._extract_date_hour_from_filename(filename)
                if not date_str or hour_str is None:
                    LoggingUtils.print_warning(f"Could not parse filename: {filename}")
                    continue
                
                # Create enhanced hourly file with ET calculations
                success = self._create_enhanced_hourly_file_with_baitsss(hourly_file, output_dir)
                
                if success:
                    processed_count += 1
                    LoggingUtils.print_success(f"Enhanced hourly file: {filename}")
                else:
                    LoggingUtils.print_error(f"Failed to enhance: {filename}")
            
            LoggingUtils.print_success(f"Enhanced {processed_count}/{len(hourly_files)} hourly files")
            LoggingUtils.print_success(f"Location: {output_dir}")
            LoggingUtils.print_success("Each file contains ALL datasets + BAITSSS ET calculations per pixel")
            
            # Create summary ET maps
            self._create_summary_et_maps(output_dir)
            
            return processed_count > 0
                
        except Exception as e:
            LoggingUtils.print_error(f"Enhanced hourly files creation failed: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def _create_enhanced_hourly_file_with_baitsss(self, input_hourly_file: str, output_dir: str) -> bool:
        try:
            filename = os.path.basename(input_hourly_file)
            output_filename = filename.replace('.tif', '_enhanced.tif')
            output_path = os.path.join(output_dir, output_filename)
            
            with rasterio.open(input_hourly_file) as src:
                # Read all input bands
                input_data = src.read()  # Shape: (bands, height, width)
                height, width = input_data.shape[1], input_data.shape[2]
                
                # Load band metadata
                band_names = self._load_band_metadata(input_hourly_file)
                
                # Create variables dictionary for ET calculation
                variables = self._create_variables_dictionary(input_data, band_names)
                
                # Calculate ET using BAITSSS algorithm
                print(f"  Running BAITSSS ET calculation...")
                baitsss_results = self._run_baitsss_et_calculation(variables)
                
                if baitsss_results is None:
                    LoggingUtils.print_error(f"BAITSSS calculation failed for {filename}")
                    return False
                
                # Create enhanced multi-band array
                total_bands = input_data.shape[0] + 5  # Original + 5 BAITSSS outputs
                enhanced_data = np.full((total_bands, height, width), -9999.0, dtype=np.float32)
                
                # Copy original data
                enhanced_data[:input_data.shape[0]] = input_data.astype(np.float32)
                
                # Add BAITSSS results
                if baitsss_results.shape[0] == 5:
                    enhanced_data[input_data.shape[0]:input_data.shape[0]+5] = baitsss_results
                else:
                    # Fallback: single ET value
                    enhanced_data[input_data.shape[0]] = baitsss_results[0] if baitsss_results.ndim > 0 else baitsss_results
                    enhanced_data[input_data.shape[0]+1:input_data.shape[0]+5] = 0.0
                
                # Create enhanced band names
                enhanced_band_names = band_names + [
                    'baitsss_et_cumulative',
                    'baitsss_precipitation_sum', 
                    'baitsss_irrigation_sum',
                    'baitsss_soil_moisture_surface',
                    'baitsss_soil_moisture_root'
                ]
                
                # Write enhanced multi-band GeoTIFF
                profile = src.profile.copy()
                profile.update({
                    'dtype': 'float32',
                    'count': total_bands,
                    'nodata': -9999.0,
                    'compress': 'lzw',
                    'tiled': True,
                    'blockxsize': 256,
                    'blockysize': 256
                })
                
                with rasterio.open(output_path, 'w', **profile) as dst:
                    dst.write(enhanced_data)
                    dst.descriptions = tuple(enhanced_band_names)
                
                # Save enhanced band metadata
                self._save_enhanced_metadata(output_dir, output_filename, enhanced_band_names, filename)
                
                print(f"  ✓ Created enhanced file: {output_filename} ({total_bands} bands)")
                print(f"  ✓ Each pixel contains: static + prism + landsat + nldas + BAITSSS ET")
                
                return True
                
        except Exception as e:
            LoggingUtils.print_error(f"Error creating enhanced hourly file: {e}")
            return False
    
    def _run_baitsss_et_calculation(self, variables: Dict[str, np.ndarray]) -> Optional[np.ndarray]:
        """
        Run complete BAITSSS ET calculation for entire raster
        Returns all 5 BAITSSS outputs per pixel
        """
        try:
            from .baitsss_algorithm import BAITSSSAlgorithm
            
            # Get dimensions
            height, width = next(iter(variables.values())).shape
            
            # Initialize BAITSSS algorithm
            baitsss = BAITSSSAlgorithm()
            
            # Initialize output array for all 5 BAITSSS outputs
            baitsss_results = np.full((5, height, width), -9999.0, dtype=np.float32)
            
            print(f"    Processing {height}x{width} pixels with BAITSSS algorithm...")
            
            # Process each pixel
            for i in range(height):
                if i % max(1, height // 10) == 0:  # Progress indicator
                    print(f"    Progress: {i}/{height} rows ({100*i//height}%)")
                
                for j in range(width):
                    try:
                        # Extract and validate pixel values
                        pixel_values = self._extract_pixel_values(variables, i, j, height, width)
                        
                        # Check for valid data
                        if not self._validate_pixel_data(pixel_values):
                            continue
                        
                        # Apply defaults for missing data
                        pixel_values = self._apply_default_values(pixel_values)
                        
                        # Call BAITSSS algorithm
                        result = baitsss.iterative_calculation(
                            pixel_values['ndvi'], pixel_values['lai'], 
                            pixel_values['soil_awc'], pixel_values['soil_fc'], 
                            pixel_values['nlcd_u'], pixel_values['precip_prism'], 
                            pixel_values['elev_array'], pixel_values['tair_oc'], 
                            pixel_values['s_hum'], pixel_values['uz_in'], 
                            pixel_values['in_short'], pixel_values['et_sum'], 
                            pixel_values['precip_prism_sum'], pixel_values['irri_sum'], 
                            pixel_values['soilm_sur_pre'], pixel_values['soilm_root_pre']
                        )
                        
                        # Store all 5 BAITSSS outputs
                        baitsss_results[:, i, j] = result
                        
                    except Exception as e:
                        # Keep as nodata for failed pixels
                        continue
            
            print(f"    ✓ BAITSSS calculation completed")
            return baitsss_results
            
        except Exception as e:
            LoggingUtils.print_error(f"Error in BAITSSS ET calculation: {e}")
            return None
    
    def _extract_pixel_values(self, variables: Dict[str, np.ndarray], i: int, j: int, height: int, width: int) -> Dict:
        """Extract all variable values for a single pixel"""
        return {
            'soil_awc': variables.get('static_soil_awc', np.zeros((height, width)))[i, j],
            'soil_fc': variables.get('static_soil_fc', np.zeros((height, width)))[i, j],
            'nlcd_u': variables.get('static_nlcd', np.zeros((height, width)))[i, j],
            'elev_array': variables.get('static_elevation', np.zeros((height, width)))[i, j],
            'precip_prism': variables.get('prism_precipitation', np.zeros((height, width)))[i, j],
            'tair_oc': variables.get('nldas_temp', np.zeros((height, width)))[i, j],
            's_hum': variables.get('nldas_humidity', np.zeros((height, width)))[i, j],
            'uz_in': variables.get('nldas_wind_speed', np.zeros((height, width)))[i, j],
            'in_short': variables.get('nldas_radiation', np.zeros((height, width)))[i, j],
            'ndvi': variables.get('landsat_ndvi', np.zeros((height, width)))[i, j],
            'lai': variables.get('landsat_lai', np.zeros((height, width)))[i, j],
            'et_sum': 0.0,
            'precip_prism_sum': 0.0,
            'irri_sum': 0.0,
            'soilm_sur_pre': 0.2,
            'soilm_root_pre': 0.3
        }
    
    def _validate_pixel_data(self, pixel_values: Dict) -> bool:
        """Check if pixel data is valid for processing"""
        tair_oc = pixel_values['tair_oc']
        ndvi = pixel_values['ndvi']
        
        return not (np.isnan(tair_oc) or tair_oc < -50 or tair_oc > 60 or
                   np.isnan(ndvi) or ndvi < -1 or ndvi > 1)
    
    def _apply_default_values(self, pixel_values: Dict) -> Dict:
        """Apply default values for invalid/missing data"""
        defaults = {
            'soil_awc': (0.1, lambda x: x <= 0 or np.isnan(x)),
            'soil_fc': (0.3, lambda x: x <= 0 or np.isnan(x)),
            'nlcd_u': (42.0, lambda x: x <= 0 or np.isnan(x)),
            'elev_array': (100.0, lambda x: x <= 0 or np.isnan(x)),
            'precip_prism': (0.0, lambda x: np.isnan(x)),
            's_hum': (0.6, lambda x: x <= 0 or x > 1 or np.isnan(x)),
            'uz_in': (2.0, lambda x: x <= 0 or np.isnan(x)),
            'in_short': (300.0, lambda x: x <= 0 or np.isnan(x)),
            'lai': (2.0, lambda x: x <= 0 or x > 10 or np.isnan(x))
        }
        
        for key, (default_val, condition) in defaults.items():
            if condition(pixel_values[key]):
                pixel_values[key] = default_val
        
        return pixel_values
    
    def _load_band_metadata(self, hourly_file: str) -> List[str]:
        """Load band metadata from JSON file or create fallback"""
        metadata_file = hourly_file.replace('.tif', '_bands.json')
        if os.path.exists(metadata_file):
            with open(metadata_file, 'r') as f:
                band_info = json.load(f)
            return [band['name'] for band in band_info['bands']]
        else:
            # Fallback band order
            band_names = []
            for category in ['static', 'prism', 'landsat', 'nldas']:
                for var in ETMapConfig.BAND_ORDER[category]:
                    band_names.append(f"{category}_{var}")
            return band_names
    
    def _create_variables_dictionary(self, input_data: np.ndarray, band_names: List[str]) -> Dict[str, np.ndarray]:
        """Create variables dictionary from input data and band names"""
        variables = {}
        for i, name in enumerate(band_names):
            if i < input_data.shape[0]:
                variables[name] = input_data[i]
        return variables
    
    def _save_enhanced_metadata(self, output_dir: str, output_filename: str, 
                              enhanced_band_names: List[str], original_filename: str):
        """Save enhanced band metadata to JSON file"""
        enhanced_metadata_path = os.path.join(output_dir, output_filename.replace('.tif', '_bands.json'))
        enhanced_band_metadata = {
            'date': original_filename.split('_')[0],
            'hour': int(original_filename.split('_')[1].split('.')[0]),
            'bands': [{'index': i+1, 'name': name} for i, name in enumerate(enhanced_band_names)],
            'total_bands': len(enhanced_band_names),
            'description': 'Enhanced hourly file with ALL datasets + BAITSSS ET calculations per pixel'
        }
        
        FileManager.save_json(enhanced_band_metadata, enhanced_metadata_path)
    
    def _create_summary_et_maps(self, output_dir: str):
        """Create summary ET maps from enhanced hourly files"""
        try:
            LoggingUtils.print_step_header("Creating Summary ET Maps")
            
            # Find enhanced files
            enhanced_pattern = os.path.join(output_dir, "*_enhanced.tif")
            enhanced_files = sorted(glob.glob(enhanced_pattern))
            
            if not enhanced_files:
                LoggingUtils.print_warning("No enhanced files found for summary")
                return
            
            # Group by date
            daily_files = {}
            for file_path in enhanced_files:
                filename = os.path.basename(file_path)
                date_str = filename.split('_')[0]
                if date_str not in daily_files:
                    daily_files[date_str] = []
                daily_files[date_str].append(file_path)
            
            # Create daily summaries
            daily_et_files = []
            for date_str, files in daily_files.items():
                daily_et_path = self._create_daily_et_summary(files, output_dir, date_str)
                if daily_et_path:
                    daily_et_files.append(daily_et_path)
            
            # Create final period summary
            if daily_et_files:
                self._create_final_et_summary(daily_et_files, output_dir)
            
        except Exception as e:
            LoggingUtils.print_error(f"Error creating summary ET maps: {e}")
    
    def _create_daily_et_summary(self, hourly_files: List[str], output_dir: str, date_str: str) -> Optional[str]:
        """Create daily ET summary from hourly files"""
        try:
            # Read ET band from all hourly files for this date
            et_arrays = []
            template_file = hourly_files[0]
            
            for file_path in hourly_files:
                with rasterio.open(file_path) as src:
                    # BAITSSS ET is typically the first ET band after original data
                    et_band_index = src.count - 5  # ET is first of 5 BAITSSS outputs
                    et_data = src.read(et_band_index + 1)  # 1-indexed
                    et_arrays.append(et_data)
            
            # Calculate daily sum
            et_stack = np.stack(et_arrays, axis=0)
            valid_mask = et_stack != -9999
            daily_et = np.full(et_stack.shape[1:], -9999.0, dtype=np.float32)
            
            for i in range(et_stack.shape[1]):
                for j in range(et_stack.shape[2]):
                    pixel_values = et_stack[:, i, j]
                    valid_values = pixel_values[valid_mask[:, i, j]]
                    if len(valid_values) > 0:
                        daily_et[i, j] = np.sum(valid_values)
            
            # Save daily ET
            daily_et_path = os.path.join(output_dir, f"ET_daily_{date_str}.tif")
            with rasterio.open(template_file) as template:
                profile = template.profile.copy()
                profile.update({'count': 1, 'dtype': 'float32'})
                
                with rasterio.open(daily_et_path, 'w', **profile) as dst:
                    dst.write(daily_et, 1)
                    dst.set_band_description(1, f"Daily ET Sum - {date_str}")
            
            # Create PNG
            png_path = os.path.join(output_dir, f"ET_daily_{date_str}.png")
            self._create_et_visualization(daily_et, png_path, f"Daily ET - {date_str}")
            
            LoggingUtils.print_success(f"Created daily ET summary: {date_str}")
            return daily_et_path
            
        except Exception as e:
            LoggingUtils.print_error(f"Error creating daily summary for {date_str}: {e}")
            return None
    
    def _create_final_et_summary(self, daily_et_files: List[str], output_dir: str):
        """Create final period ET summary"""
        try:
            LoggingUtils.print_step_header("Creating Final ET Summary")
            
            # Read all daily ET files
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
            
            for i in range(daily_stack.shape[1]):
                for j in range(daily_stack.shape[2]):
                    pixel_values = daily_stack[:, i, j]
                    valid_values = pixel_values[valid_mask[:, i, j]]
                    if len(valid_values) > 0:
                        mean_et[i, j] = np.mean(valid_values)
            
            # Save final ET TIF
            final_et_tif = os.path.join(output_dir, "ET_final_result.tif")
            with rasterio.open(template_file) as template:
                profile = template.profile.copy()
                
                with rasterio.open(final_et_tif, 'w', **profile) as dst:
                    dst.write(mean_et, 1)
                    dst.set_band_description(1, f"Mean ET over {len(daily_et_files)} days")
            
            # Create final PNG
            final_et_png = os.path.join(output_dir, "ET_final_result.png")
            self._create_et_visualization(mean_et, final_et_png, f"Mean ET over {len(daily_et_files)} days")
            
            LoggingUtils.print_success(f"Final ET results created:")
            LoggingUtils.print_success(f"  TIF: {final_et_tif}")
            LoggingUtils.print_success(f"  PNG: {final_et_png}")
            
        except Exception as e:
            LoggingUtils.print_error(f"Error creating final ET summary: {e}")
    
    def _create_et_visualization(self, et_data: np.ndarray, png_path: str, title: str):
        """Create PNG visualization of ET map"""
        try:
            import matplotlib.pyplot as plt
            
            # Mask nodata values
            et_masked = np.ma.masked_where(et_data == -9999, et_data)
            
            # Create figure
            plt.figure(figsize=(12, 8))
            
            # Create colormap
            cmap = plt.cm.viridis
            cmap.set_bad('white', 1.0)
            
            # Plot ET map
            im = plt.imshow(et_masked, cmap=cmap, interpolation='nearest')
            plt.colorbar(im, label='ET (mm/day)', shrink=0.8)
            plt.title(title, fontsize=14, fontweight='bold')
            plt.axis('off')
            
            # Save PNG
            plt.savefig(png_path, dpi=300, bbox_inches='tight', facecolor='white')
            plt.close()
            
            LoggingUtils.print_success(f"Created ET visualization: {png_path}")
            
        except ImportError:
            LoggingUtils.print_warning("Matplotlib not available - skipping PNG creation")
        except Exception as e:
            LoggingUtils.print_error(f"Error creating PNG: {e}")
    
    def _extract_date_hour_from_filename(self, filename: str) -> tuple:
        """Extract date and hour from hourly filename"""
        try:
            # Expected format: 2024-03-16_00.tif
            base_name = os.path.splitext(filename)[0]
            date_str, hour_str = base_name.split('_')
            hour = int(hour_str)
            return date_str, hour
        except:
            return None, None


class ETResultsManager:
    """
    Manages ET results and creates summary reports
    """
    
    @staticmethod
    def create_comprehensive_et_summary(output_dir: str, request_data: Dict):
        """Create comprehensive summary report of ET results"""
        try:
            # Find final results
            final_tif = os.path.join(output_dir, "ET_final_result.tif")
            final_png = os.path.join(output_dir, "ET_final_result.png")
            
            if not os.path.exists(final_tif):
                LoggingUtils.print_warning("Final ET result not found")
                return
            
            # Create comprehensive summary
            summary = {
                'request_info': request_data,
                'processing_date': datetime.now().isoformat(),
                'algorithm': {
                    'name': 'BAITSSS (Biosphere-Atmosphere Interactions Two-Source Surface)',
                    'type': 'Two-source energy balance model',
                    'description': 'Complete implementation of BAITSSS ET algorithm'
                },
                'results': {
                    'final_et_map_tif': final_tif,
                    'final_et_visualization_png': final_png,
                    'format': 'GeoTIFF and PNG',
                    'units': 'mm/day',
                    'spatial_alignment': 'All datasets perfectly aligned',
                    'temporal_resolution': 'Hourly calculations aggregated to daily and period means'
                }
            }
            
            # Calculate comprehensive statistics
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
            
            # Count enhanced hourly files
            enhanced_files = glob.glob(os.path.join(output_dir, "*_enhanced.tif"))
            summary['file_counts'] = {
                'enhanced_hourly_files': len(enhanced_files),
                'description': 'Each enhanced file contains all datasets + BAITSSS ET per pixel'
            }
            
            # Save comprehensive summary
            summary_path = os.path.join(output_dir, "ET_comprehensive_summary.json")
            FileManager.save_json(summary, summary_path)
            
            LoggingUtils.print_success(f"Comprehensive ET summary created: {summary_path}")
            
        except Exception as e:
            LoggingUtils.print_error(f"Error creating comprehensive ET summary: {e}")