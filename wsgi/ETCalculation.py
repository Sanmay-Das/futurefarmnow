import argparse
import sys
import os
import json
import uuid
import requests
import time
from datetime import datetime, timedelta
from typing import Dict, Optional
from etmap_modules.grid_manager import UnifiedGridManager
from etmap_modules.parsers import CurlCommandParser
from etmap_modules.data_processors import NLDASProcessor, LandsatProcessor, PRISMProcessor, StaticDataProcessor, DataCollector
from etmap_modules.hourly_processor import CompleteETMapProcessor as ModularProcessor
from etmap_modules.utils import DatabaseManager, FileManager, GeospatialUtils
from etmap_modules.config import ETMapConfig


class ETCalculationManager:
    def __init__(self, db_path: str = None):
        self.db_path = db_path or ETMapConfig.DB_PATH
        self.db_manager = DatabaseManager(self.db_path)
        self.processor = ModularProcessor(db_path=self.db_path)
    
    def process_with_curl_integration(self, curl_command: str, output_path: str = None) -> bool:
        """
        Process ETMap calculation with full curl integration
        """
        print("="*60)
        print("ETMAP CALCULATION WITH UUID INTEGRATION")
        print("="*60)
        
        try:
            # Step 1: Parse the curl command
            print("\n=== Step 1: Parsing Curl Command ===")
            request_data = CurlCommandParser.parse_curl_command(curl_command)
            
            if not CurlCommandParser.validate_parsed_data(request_data):
                print("ERROR: Invalid request data in curl command")
                return False
            
            # Step 2: Extract API endpoint and submit request
            print("\n=== Step 2: Submitting Data Collection Request ===")
            api_url = self._extract_api_url(curl_command)
            
            if not api_url:
                print("ERROR: Could not extract API URL from curl command")
                return False
            
            # Submit the request to get UUID
            request_id = self._submit_data_collection_request(api_url, request_data)
            
            if not request_id:
                print("ERROR: Failed to submit data collection request")
                return False
            
            print(f"Data collection request submitted with UUID: {request_id}")
            
            # Step 3: Wait for data collection to complete
            print("\n=== Step 3: Waiting for Data Collection ===")
            if not self._wait_for_data_collection(api_url, request_id):
                print("ERROR: Data collection failed or timed out")
                return False
            
            # Step 4: Process the collected data using the same UUID
            print("\n=== Step 4: Processing Collected Data ===")
            
            # Determine output path - use UUID if not specified
            if output_path is None:
                output_path = ETMapConfig.get_output_path(request_id)
            
            print(f"Processing data with UUID: {request_id}")
            print(f"Output will be stored in: {output_path}")
            
            # Process using the UUID from data collection
            success = self.processor.process_by_request_id(request_id, output_path)
            
            if success:
                print("\n" + "="*60)
                print("COMPLETE ETMAP CALCULATION FINISHED!")
                print(f"Results stored in UUID folder: {request_id}")
                print(f"Location: {output_path}")
                print(f"Hourly aligned files: {output_path}/hourly_aligned/")
                print("Ready for QGIS and ET model calculations!")
                print("="*60)
                return True
            else:
                print("ERROR: Data processing failed")
                return False
                
        except Exception as e:
            print(f"ERROR: ETCalculation failed: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def process_with_existing_uuid(self, request_id: str, output_path: str = None) -> bool:
        """
        Process data using an existing UUID (when data collection already completed)
        """
        print("="*60)
        print("ETMAP CALCULATION WITH EXISTING UUID")
        print("="*60)
        
        print(f"Processing existing request: {request_id}")
        
        # Check if request exists in database
        job_info = self.db_manager.get_job_info(request_id)
        if not job_info:
            print(f"ERROR: Request ID {request_id} not found in database")
            return False
        
        # Determine output path
        if output_path is None:
            output_path = ETMapConfig.get_output_path(request_id)
        
        print(f"Output will be stored in: {output_path}")
        
        # Process the data
        success = self.processor.process_by_request_id(request_id, output_path)
        
        if success:
            print("\n" + "="*60)
            print("ETMAP CALCULATION COMPLETED!")
            print(f"Results stored in UUID folder: {request_id}")
            print(f"Location: {output_path}")
            print("="*60)
            return True
        else:
            print("ERROR: Data processing failed")
            return False
    
    def _extract_api_url(self, curl_command: str) -> Optional[str]:
        """Extract API URL from curl command"""
        try:
            # First try the parser method
            url = CurlCommandParser.extract_url_from_curl(curl_command)
            
            # If that fails, try a more direct approach
            if not url or not url.startswith('http'):
                # Look for http/https URLs directly in the command
                import re
                url_match = re.search(r'(https?://[^\s\'"\\]+)', curl_command)
                if url_match:
                    url = url_match.group(1)
            
            if url and url.startswith('http'):
                print(f"Extracted API URL: {url}")
                return url
            else:
                print(f"ERROR: Could not extract valid HTTP URL from curl command")
                print(f"Found: '{url}' - this is not a valid URL")
                return None
        except Exception as e:
            print(f"ERROR: Error extracting URL: {e}")
            return None
    
    def _submit_data_collection_request(self, api_url: str, request_data: Dict) -> Optional[str]:
        """Submit data collection request to API"""
        try:
            headers = {'Content-Type': 'application/json'}
            
            print("Submitting request to data collection API...")
            response = requests.post(api_url, json=request_data, headers=headers, timeout=30)
            
            if response.status_code in [200, 201]:
                response_data = response.json()
                request_id = response_data.get('request_id')
                
                if request_id:
                    print("Request submitted successfully!")
                    print(f"Request ID: {request_id}")
                    return request_id
                else:
                    print("ERROR: No request_id in API response")
                    return None
            else:
                print(f"ERROR: API request failed: {response.status_code}")
                print(f"Response: {response.text}")
                return None
                
        except requests.exceptions.RequestException as e:
            print(f"ERROR: Request failed: {e}")
            return None
        except Exception as e:
            print(f"ERROR: Unexpected error submitting request: {e}")
            return None
    
    def _wait_for_data_collection(self, api_url: str, request_id: str, max_wait_minutes: int = 30) -> bool:
        """Wait for data collection to complete by polling status"""
        status_url = f"{api_url}/{request_id}"
        max_wait_seconds = max_wait_minutes * 60
        poll_interval = 10  # seconds
        elapsed_time = 0
        
        print("Monitoring data collection progress...")
        print(f"Status URL: {status_url}")
        print(f"Maximum wait time: {max_wait_minutes} minutes")
        
        while elapsed_time < max_wait_seconds:
            try:
                response = requests.get(status_url, timeout=10)
                
                if response.status_code == 200:
                    status_data = response.json()
                    current_status = status_data.get('status', 'unknown')
                    
                    print(f"[{elapsed_time//60:02d}:{elapsed_time%60:02d}] Status: {current_status}")
                    
                    # Check for completion
                    if current_status == 'success':
                        print("Data collection completed successfully!")
                        return True
                    
                    # Check for failure
                    elif current_status in ['failed', 'error']:
                        error_msg = status_data.get('error_message', 'Unknown error')
                        print(f"ERROR: Data collection failed: {error_msg}")
                        return False
                    
                    # Still in progress - continue waiting
                    elif current_status in ['queued', 'checking_coverage', 'landsat_started', 'prism_started']:
                        time.sleep(poll_interval)
                        elapsed_time += poll_interval
                    
                    else:
                        print(f"WARNING: Unknown status: {current_status}")
                        time.sleep(poll_interval)
                        elapsed_time += poll_interval
                
                else:
                    print(f"WARNING: Status check failed: {response.status_code}")
                    time.sleep(poll_interval)
                    elapsed_time += poll_interval
                    
            except requests.exceptions.RequestException as e:
                print(f"WARNING: Status check error: {e}")
                time.sleep(poll_interval)
                elapsed_time += poll_interval
            
            except Exception as e:
                print(f"ERROR: Unexpected error during status check: {e}")
                return False
        
        print(f"ERROR: Data collection timed out after {max_wait_minutes} minutes")
        return False


class CompleteETMapProcessor:
    """
    Complete processor that creates both basic aligned datasets AND hourly aligned files
    Based on the original ETCalculation.py logic but with UUID integration
    """
    
    def __init__(self, db_path: str = None):
        self.grid_manager = UnifiedGridManager()
        self.nldas_processor = NLDASProcessor()
        self.landsat_processor = LandsatProcessor(self.grid_manager)
        self.prism_processor = PRISMProcessor(self.grid_manager)
        self.static_processor = StaticDataProcessor(self.grid_manager)
        
        # Database manager for UUID lookups
        self.db_path = db_path or ETMapConfig.DB_PATH
        self.db_manager = DatabaseManager(self.db_path)
        
    def process_complete_etmap_data(self, request_data: dict, output_base_path: str):
        """
        Complete processing: basic alignment + hourly aligned files
        Maintains the original logic flow from ETCalculation.py
        """
        print("="*60)
        print("COMPLETE ETMAP PROCESSOR")
        print("Creates basic aligned data + hourly aligned files")
        print("="*60)
        
        # Extract parameters from request
        date_from = request_data['date_from']
        date_to = request_data['date_to']
        geometry_dict = request_data['geometry']
        
        from shapely.geometry import shape
        aoi_geometry = shape(geometry_dict)
        
        print(f"Processing request:")
        print(f"  Date range: {date_from} to {date_to}")
        print(f"  AOI bounds: {aoi_geometry.bounds}")
        print(f"  AOI area: ~{aoi_geometry.area * 111000 * 111000:.1f} km²")
        
        # Create AOI shapefile
        aoi_shapefile = os.path.join(output_base_path, "AOI/dynamic_aoi.shp")
        GeospatialUtils.create_aoi_shapefile(geometry_dict, aoi_shapefile)
        
        # Collect sample datasets
        print("\n=== Collecting Sample Datasets ===")
        sample_datasets = DataCollector.collect_sample_datasets()
        
        if not sample_datasets:
            print("ERROR: No sample datasets found. Check your data paths.")
            return
        
        # Compute unified grid
        print("\n=== Computing Unified Grid ===")
        global_metadata = self.grid_manager.compute_unified_grid(aoi_geometry, sample_datasets)
        
        # Clip to AOI
        print("\n=== Clipping to AOI ===")
        aoi_metadata = self.grid_manager.clip_to_aoi(aoi_geometry)
        
        # Save request and grid metadata
        self._save_processing_metadata(request_data, aoi_metadata, output_base_path)
        
        # Process basic datasets first
        print("\n=== STEP 1: Creating Basic Aligned Datasets ===")
        self._process_landsat_data(aoi_metadata, output_base_path)
        self._process_static_data(aoi_metadata, output_base_path)
        self._process_prism_data_by_dates(aoi_metadata, date_from, date_to, output_base_path)
        
        # Create hourly aligned files
        print("\n=== STEP 2: Creating Hourly Aligned Files ===")
        self._create_hourly_aligned_files(request_data, aoi_metadata, output_base_path)

        print("\n=== STEP 3: Running BAITSSS ET Algorithm ===")
        self._run_et_algorithm_processing(output_base_path)
        
        print("\n" + "="*60)
        print("COMPLETE PROCESSING FINISHED!")
        print(f"Basic aligned data: {output_base_path}/")
        print(f"Hourly aligned files: {output_base_path}/hourly_aligned/")
        print("Ready for QGIS verification!")
        print("="*60)
    
    def _process_landsat_data(self, aoi_metadata: dict, output_base_path: str):
        """Process Landsat data following the original logic"""
        print("Processing Landsat Data...")
        self.landsat_processor.process_landsat_data(aoi_metadata, output_base_path)
    
    def _process_static_data(self, aoi_metadata: dict, output_base_path: str):
        """Process static data following the original logic"""
        print("Processing Static Data...")
        self.static_processor.process_static_data(aoi_metadata, output_base_path)
    
    def _process_prism_data_by_dates(self, aoi_metadata: dict, date_from: str, date_to: str, output_base_path: str):
        """Process PRISM data following the original logic"""
        print("Processing PRISM Data...")
        self.prism_processor.process_prism_data_by_dates(aoi_metadata, date_from, date_to, output_base_path)
    
    def _create_hourly_aligned_files(self, request_data: dict, aoi_metadata: dict, output_base_path: str):
        """Create hourly aligned files using modular processor"""
        # Use the modular processor's hourly file creation
        modular_processor = ModularProcessor(db_path=self.db_path)
        modular_processor._create_hourly_aligned_files(request_data, aoi_metadata, output_base_path)
    
    def _save_processing_metadata(self, request_data: dict, aoi_metadata: dict, output_base_path: str):
        """Save processing metadata and request info"""
        # Save original request
        request_file = os.path.join(output_base_path, "original_request.json")
        FileManager.save_json(request_data, request_file)
        
        # Save grid metadata
        metadata_file = os.path.join(output_base_path, "grid_metadata.json")
        serializable_metadata = GeospatialUtils.serialize_geometry_metadata(aoi_metadata)
        FileManager.save_json(serializable_metadata, metadata_file)
        
        print(f"Metadata saved: {request_file}, {metadata_file}")


def main():
    """
    Main function - creates both basic aligned data AND hourly aligned files
    Now supports both curl command and request_id lookup
    """
    parser = argparse.ArgumentParser(description='Complete ETMap Processor with UUID Integration')
    
    # Mutually exclusive group for input method
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument('--curl', type=str, help='Curl command as string')
    input_group.add_argument('--uuid', type=str, help='Request ID from database')
    
    parser.add_argument('--output-path', type=str, help='Custom output directory (optional)')
    parser.add_argument('--db-path', type=str, help='Path to SQLite database (optional)')
    parser.add_argument('--max-wait', type=int, default=30, help='Maximum wait time for data collection in minutes')
    
    args = parser.parse_args()
    
    print("="*60)
    print("COMPLETE ETMAP PROCESSOR - UUID INTEGRATION")
    print("Creates basic aligned data + hourly aligned files")
    print("="*60)
    
    try:
        # Ensure directories exist
        ETMapConfig.ensure_directories_exist()
        
        # Create calculation manager
        calc_manager = ETCalculationManager(args.db_path)
        
        success = False
        
        if args.curl:
            # Full workflow: submit request, wait for data collection, then process
            print("Starting full ETMap calculation workflow...")
            success = calc_manager.process_with_curl_integration(args.curl, args.output_path)
            
        elif args.uuid:
            # Process existing UUID (data collection already completed)
            print("Processing existing UUID...")
            success = calc_manager.process_with_existing_uuid(args.uuid, args.output_path)
        
        if success:
            print("\n" + "="*60)
            print("ETMAP CALCULATION COMPLETED SUCCESSFULLY!")
            print("="*60)
            sys.exit(0)
        else:
            print("\n" + "="*60)
            print("ETMAP CALCULATION FAILED!")
            print("="*60)
            sys.exit(1)
            
    except KeyboardInterrupt:
        print("\nProcess interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()