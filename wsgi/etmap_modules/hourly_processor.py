import os
import json
import numpy as np
import rasterio
import uuid
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from shapely.geometry import shape
from .config import ETMapConfig
from .utils import DatabaseManager, FileManager, GeospatialUtils, LoggingUtils
import argparse
import sys    
from .parsers import CurlCommandParser, RequestDataParser
from .grid_manager import UnifiedGridManager
from .data_processors import (
    NLDASProcessor, LandsatProcessor, PRISMProcessor, 
    StaticDataProcessor, DataCollector
)


class CompleteETMapProcessor:
    def __init__(self, data_base_path: str = None, db_path: str = None):
        self.data_base_path = data_base_path or ETMapConfig.DATA_BASE_PATH
        self.grid_manager = UnifiedGridManager()
        self.nldas_processor = NLDASProcessor()
        
        # Database manager for UUID lookups
        if db_path is None:
            db_path = ETMapConfig.DB_PATH
        self.db_manager = DatabaseManager(db_path)
        
        # Data processors
        self.landsat_processor = LandsatProcessor(self.grid_manager)
        self.prism_processor = PRISMProcessor(self.grid_manager)
        self.static_processor = StaticDataProcessor(self.grid_manager)
        
    def process_by_request_id(self, request_id: str, output_base_path: str = None) -> bool:
        """
        Process ETMap data using request_id from database
        Creates output folder structure using the UUID
        
        Args:
            request_id: UUID of the request
            output_base_path: Optional custom output path
            
        Returns:
            True if successful, False otherwise
        """
        LoggingUtils.print_progress_header("COMPLETE ETMAP PROCESSOR - UUID INTEGRATION")
        
        # Get job info from database
        job_info = self.db_manager.get_job_info(request_id)
        if not job_info:
            LoggingUtils.print_error(f"Request ID {request_id} not found in database")
            return False
            
        LoggingUtils.print_success(f"Found job in database: {request_id}")
        print(f"  Status: {job_info['status']}")
        print(f"  Date range: {job_info['date_from']} to {job_info['date_to']}")
        
        # Create UUID-based output structure
        if output_base_path is None:
            output_base_path = ETMapConfig.get_output_path(request_id)
        
        print(f"  Output path: {output_base_path}")
        
        # Reconstruct request data
        request_data = {
            'date_from': job_info['date_from'],
            'date_to': job_info['date_to'],
            'geometry': job_info['geometry']
        }
        
        # Process the data
        return self.process_complete_etmap_data(request_data, output_base_path)
    
    def process_by_curl_command(self, curl_command: str, output_base_path: str = None) -> bool:
        """
        Process ETMap data using curl command
        
        Args:
            curl_command: Curl command string
            output_base_path: Optional custom output path
            
        Returns:
            True if successful, False otherwise
        """
        LoggingUtils.print_progress_header("COMPLETE ETMAP PROCESSOR - CURL PROCESSING")
        
        # Parse curl command
        try:
            request_data = CurlCommandParser.parse_curl_command(curl_command)
            
            # Validate parsed data
            if not CurlCommandParser.validate_parsed_data(request_data):
                LoggingUtils.print_error("Invalid request data. Exiting.")
                return False
                
        except Exception as e:
            LoggingUtils.print_error(f"Error parsing curl command: {e}")
            return False
        
        # Generate UUID for this processing run if no output path specified
        if output_base_path is None:
            processing_uuid = str(uuid.uuid4())
            output_base_path = ETMapConfig.get_output_path(processing_uuid)
            print(f"Generated processing UUID: {processing_uuid}")
        
        # Process the data
        return self.process_complete_etmap_data(request_data, output_base_path)
    
    def process_complete_etmap_data(self, request_data: Dict, output_base_path: str) -> bool:
        """
        Complete processing: basic alignment + hourly aligned files
        Now organized by UUID
        
        Args:
            request_data: Request data dictionary
            output_base_path: Base output path
            
        Returns:
            True if successful, False otherwise
        """
        try:
            LoggingUtils.print_progress_header("COMPLETE ETMAP PROCESSOR")
            print("Creates basic aligned data + hourly aligned files")
            
            # Validate request data
            validated_data = RequestDataParser.validate_request_data(request_data)
            
            # Extract parameters from request
            date_from = validated_data['date_info']['date_from']
            date_to = validated_data['date_info']['date_to']
            geometry_info = validated_data['geometry_info']
            aoi_geometry = geometry_info['geometry']
            
            print(f"Processing request:")
            print(f"  Date range: {date_from} to {date_to}")
            print(f"  AOI bounds: {geometry_info['bounds']}")
            print(f"  AOI area: ~{geometry_info['area_km2']:.1f} km²")
            print(f"  Output path: {output_base_path}")
            
            # Create AOI shapefile
            aoi_shapefile = os.path.join(output_base_path, "AOI/dynamic_aoi.shp")
            GeospatialUtils.create_aoi_shapefile(request_data['geometry'], aoi_shapefile)
            
            # Collect sample datasets
            LoggingUtils.print_step_header("Collecting Sample Datasets")
            sample_datasets = DataCollector.collect_sample_datasets()
            
            if not sample_datasets:
                LoggingUtils.print_error("No sample datasets found. Check your data paths.")
                return False
            
            # Compute unified grid
            LoggingUtils.print_step_header("Computing Unified Grid")
            global_metadata = self.grid_manager.compute_unified_grid(aoi_geometry, sample_datasets)
            
            # Clip to AOI
            LoggingUtils.print_step_header("Clipping to AOI")
            aoi_metadata = self.grid_manager.clip_to_aoi(aoi_geometry)
            
            # Save request and grid metadata
            self._save_processing_metadata(request_data, aoi_metadata, output_base_path)
            
            # Process basic datasets first
            LoggingUtils.print_step_header("STEP 1: Creating Basic Aligned Datasets")
            self.landsat_processor.process_landsat_data(aoi_metadata, output_base_path)
            self.static_processor.process_static_data(aoi_metadata, output_base_path)
            self.prism_processor.process_prism_data_by_dates(aoi_metadata, date_from, date_to, output_base_path)
            
            # Create hourly aligned files
            LoggingUtils.print_step_header("STEP 2: Creating Hourly Aligned Files")
            self._create_hourly_aligned_files(request_data, aoi_metadata, output_base_path)
            
            LoggingUtils.print_progress_header("COMPLETE PROCESSING FINISHED!")
            LoggingUtils.print_success(f"Basic aligned data: {output_base_path}/")
            LoggingUtils.print_success(f"Hourly aligned files: {output_base_path}/hourly_aligned/")
            LoggingUtils.print_success(f"UUID: {os.path.basename(output_base_path)}")
            LoggingUtils.print_success("Ready for QGIS verification!")
            
            return True
            
        except Exception as e:
            LoggingUtils.print_error(f"Processing failed: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def _create_hourly_aligned_files(self, request_data: Dict, aoi_metadata: Dict, output_base_path: str):
        """
        Create hourly aligned raster files combining all datasets
        Stored in UUID-based folder structure
        
        Args:
            request_data: Request data dictionary
            aoi_metadata: AOI metadata dictionary
            output_base_path: Base output path
        """
        print("Creating hourly aligned raster files...")
        
        # Create hourly output directory with UUID organization
        hourly_output_dir = ETMapConfig.get_output_path(
            os.path.basename(output_base_path), 'hourly_aligned'
        )
        FileManager.ensure_directory_exists(hourly_output_dir)
        
        # Load pre-aligned data
        static_data = self.static_processor.load_aligned_static_data(output_base_path)
        landsat_data = self.landsat_processor.load_aligned_landsat_data(output_base_path)
        
        # Process each day
        date_from = datetime.strptime(request_data['date_from'], '%Y-%m-%d')
        date_to = datetime.strptime(request_data['date_to'], '%Y-%m-%d')
        
        current_date = date_from
        total_hours_processed = 0
        
        while current_date <= date_to:
            print(f"\n--- Processing Date: {current_date.strftime('%Y-%m-%d')} ---")
            
            # Load PRISM daily data for this date
            prism_data = self.prism_processor.load_aligned_prism_data(output_base_path, current_date)
            
            # Find NLDAS hourly files for this date
            hourly_files = self.nldas_processor.find_nldas_hourly_files(current_date)
            
            if not hourly_files:
                LoggingUtils.print_warning(f"No NLDAS hourly data found for {current_date.strftime('%Y-%m-%d')}")
                # Still create hourly files without NLDAS data
                hourly_files = [(h, None) for h in range(0, 24, 6)]  # Every 6 hours as placeholder
            
            # Process each hour
            for hour, nldas_file_path in hourly_files:
                print(f"Processing Hour {hour:02d}")
                
                # Load NLDAS data if available
                nldas_data = None
                if nldas_file_path:
                    nldas_data = self.nldas_processor.load_nldas_data(nldas_file_path)
                    if nldas_data is not None:
                        nldas_data = self.nldas_processor.align_nldas_to_grid(nldas_data, aoi_metadata)
                
                # Create hourly aligned raster
                success = self._create_single_hourly_file(
                    current_date, hour, static_data, prism_data,
                    landsat_data, nldas_data, aoi_metadata, hourly_output_dir
                )
                
                if success:
                    total_hours_processed += 1
            
            current_date += timedelta(days=1)
        
        LoggingUtils.print_success(f"Created {total_hours_processed} hourly aligned files")
        LoggingUtils.print_success(f"Location: {hourly_output_dir}")
    
    def _create_single_hourly_file(self, date: datetime, hour: int,
                                 static_data: Dict, prism_data: Dict,
                                 landsat_data: Dict, nldas_data: Optional[np.ndarray],
                                 aoi_metadata: Dict, output_dir: str) -> bool:
        """
        Create single hourly file with all datasets aligned
        
        Args:
            date: Date for the hourly file
            hour: Hour (0-23)
            static_data: Static data dictionary
            prism_data: PRISM data dictionary
            landsat_data: Landsat data dictionary
            nldas_data: NLDAS data array (optional)
            aoi_metadata: AOI metadata dictionary
            output_dir: Output directory
            
        Returns:
            True if successful, False otherwise
        """
        print(f"  Creating {date.strftime('%Y-%m-%d')}_{hour:02d}.tif")
        
        # Get dimensions from metadata
        height = aoi_metadata['height']
        width = aoi_metadata['width']
        transform = aoi_metadata['transform']
        crs = aoi_metadata['crs']
        
        # Collect all data layers in order
        all_layers = []
        layer_names = []
        
        # 1. Static data layers
        for var_name in ETMapConfig.BAND_ORDER['static']:
            if var_name in static_data:
                all_layers.append(static_data[var_name])
                layer_names.append(f"static_{var_name}")
        
        # 2. PRISM daily data layers 
        for var_name in ETMapConfig.BAND_ORDER['prism']:
            if var_name in prism_data:
                all_layers.append(prism_data[var_name])
                layer_names.append(f"prism_{var_name}")
        
        # 3. Landsat data layers
        for var_name in ETMapConfig.BAND_ORDER['landsat']:
            if var_name in landsat_data:
                all_layers.append(landsat_data[var_name])
                layer_names.append(f"landsat_{var_name}")
        
        # 4. NLDAS hourly data layers (if available)
        if nldas_data is not None and nldas_data.shape[0] >= 4:
            for i, var_name in enumerate(ETMapConfig.BAND_ORDER['nldas']):
                if i < nldas_data.shape[0]:
                    all_layers.append(nldas_data[i])
                    layer_names.append(f"nldas_{var_name}")
        
        if not all_layers:
            LoggingUtils.print_error("No data layers available")
            return False
        
        # Ensure all layers have same dimensions
        target_shape = (height, width)
        aligned_layers = []
        
        for i, layer in enumerate(all_layers):
            if layer.shape != target_shape:
                print(f"    Resizing {layer_names[i]} from {layer.shape} to {target_shape}")
                from .utils import ArrayUtils
                layer = ArrayUtils.resize_array_to_target(layer, target_shape)
            
            aligned_layers.append(layer.astype(np.float32))
        
        # Create output filename
        output_filename = f"{date.strftime('%Y-%m-%d')}_{hour:02d}.tif"
        output_path = os.path.join(output_dir, output_filename)
        
        # Write multi-band GeoTIFF
        profile = ETMapConfig.GEOTIFF_PROFILE.copy()
        profile.update({
            'width': width,
            'height': height,
            'count': len(aligned_layers),
            'crs': crs,
            'transform': transform
        })
        
        try:
            with rasterio.open(output_path, 'w', **profile) as dst:
                for i, layer in enumerate(aligned_layers):
                    dst.write(layer, i + 1)
                dst.descriptions = tuple(layer_names)
            
            # Save band metadata
            metadata_path = os.path.join(output_dir, f"{date.strftime('%Y-%m-%d')}_{hour:02d}_bands.json")
            band_metadata = {
                'date': date.strftime('%Y-%m-%d'),
                'hour': hour,
                'bands': [{'index': i+1, 'name': name} for i, name in enumerate(layer_names)],
                'total_bands': len(layer_names)
            }
            
            FileManager.save_json(band_metadata, metadata_path)
            
            LoggingUtils.print_success(f"Created {output_filename} ({len(aligned_layers)} bands)")
            return True
            
        except Exception as e:
            LoggingUtils.print_error(f"Error creating {output_filename}: {e}")
            return False
    
    def _save_processing_metadata(self, request_data: Dict, aoi_metadata: Dict, output_base_path: str):
        """
        Save processing metadata and request info
        
        Args:
            request_data: Original request data
            aoi_metadata: AOI metadata dictionary
            output_base_path: Base output path
        """
        # Save original request
        request_file = os.path.join(output_base_path, "original_request.json")
        FileManager.save_json(request_data, request_file)
        
        # Save grid metadata
        metadata_file = os.path.join(output_base_path, "grid_metadata.json")
        serializable_metadata = GeospatialUtils.serialize_geometry_metadata(aoi_metadata)
        FileManager.save_json(serializable_metadata, metadata_file)
        
        LoggingUtils.print_success(f"Metadata saved: {request_file}, {metadata_file}")


def main():
    """
    Main function - creates both basic aligned data AND hourly aligned files
    Now supports both curl command and request_id lookup
    """
    parser = argparse.ArgumentParser(description='Complete ETMap Processor with UUID Integration')
    
    # Mutually exclusive group for input method
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument('--curl', type=str, help='Curl command as string')
    input_group.add_argument('--request-id', type=str, help='Request ID from database')
    
    parser.add_argument('--output-path', type=str, help='Custom output directory (optional)')
    parser.add_argument('--db-path', type=str, help='Path to SQLite database (optional)')
    
    args = parser.parse_args()
    
    try:
        # Ensure directories exist
        ETMapConfig.ensure_directories_exist()
        
        processor = CompleteETMapProcessor(db_path=args.db_path)
        
        if args.request_id:
            # Process using request_id from database
            print(f"Processing using request ID: {args.request_id}")
            success = processor.process_by_request_id(args.request_id, args.output_path)
            
        elif args.curl:
            # Process using curl command
            print("Processing using curl command")
            success = processor.process_by_curl_command(args.curl, args.output_path)
        
        if success:
            LoggingUtils.print_progress_header("✓ PROCESSING COMPLETED SUCCESSFULLY!")
        else:
            LoggingUtils.print_error("Processing failed")
            sys.exit(1)
        
    except Exception as e:
        LoggingUtils.print_error(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()