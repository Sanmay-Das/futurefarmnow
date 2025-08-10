import os
import sys
import json
import uuid
import threading
import sqlite3
from datetime import datetime
from enum import Enum
from flask import Blueprint, request, jsonify, send_file, redirect, url_for

# Import modular components from raw_data_modules
from raw_data_modules.config import RawDataConfig
from raw_data_modules.database import RawDataDatabase
from raw_data_modules.job_manager import RawDataJobManager, JobStatus
from raw_data_modules.coverage_checker import SpatialCoverageChecker
from raw_data_modules.data_fetchers import LandsatFetcher, PRISMFetcher, NLDASFetcher
from raw_data_modules.fetch_manager import DataFetchManager
from raw_data_modules.utils import RawDataUtils

# Blueprint for raw data fetching (can be imported as etrawdata_bp in server.py)
etrawdata_bp = Blueprint('etrawdata', __name__)

# Initialize modular components
db = RawDataDatabase()
job_manager = RawDataJobManager(db)
coverage_checker = SpatialCoverageChecker()

# Initialize fetch manager and fetchers
fetch_manager = DataFetchManager()
fetch_manager.register_fetcher('landsat', LandsatFetcher())
fetch_manager.register_fetcher('prism', PRISMFetcher())
fetch_manager.register_fetcher('nldas', NLDASFetcher())

@etrawdata_bp.route('/v1/etmap', methods=['POST'])
def create_etmap_request():
    """
    Create a new ETMap data collection request
    """
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
        RawDataUtils.validate_geometry(request_data['geometry'])
    except Exception as e:
        return jsonify({'error': 'Invalid geometry', 'details': str(e)}), 400

    date_from = request_data['date_from']
    date_to = request_data['date_to']
    
    # Check for existing job with same parameters
    existing_request_id = job_manager.find_existing_job(date_from, date_to, request_data['geometry'])
    if existing_request_id:
        return jsonify({'request_id': existing_request_id}), 200

    # Create new job
    request_id = job_manager.create_job(request_data)
    
    # Start data collection in background thread
    threading.Thread(
        target=execute_data_collection,
        args=(request_id, request_data),
        daemon=True
    ).start()
    
    return jsonify({'request_id': request_id}), 201

@etrawdata_bp.route('/v1/etmap/<string:request_id>', methods=['GET'])
def get_etmap_status(request_id: str):
    """
    Get the status of an ETMap data collection request
    """
    try:
        uuid.UUID(request_id)
    except ValueError:
        return jsonify({'error': 'Invalid request ID format'}), 400
    
    job_data = job_manager.get_job_status(request_id)
    if not job_data:
        return jsonify({'error': 'Request not found'}), 404
    
    return jsonify(job_data), 200

@etrawdata_bp.route('/v1/etmap/<string:request_id>/result', methods=['GET'])
def get_etmap_result(request_id: str):
    """
    Get the result of a completed ETMap request
    """
    try:
        uuid.UUID(request_id)
    except ValueError:
        return jsonify({'error': 'Invalid request ID format'}), 400
    
    job_status_data = job_manager.get_job_status(request_id)
    if not job_status_data:
        return jsonify({'error': 'Request not found'}), 404
    
    job_status = job_status_data['status']
    
    if job_status != JobStatus.SUCCESS.value:
        return redirect(url_for('etrawdata.get_etmap_status', request_id=request_id))
    
    # Return info about collected raw data
    return jsonify({
        'message': 'Data collection completed',
        'data_folders': {
            'landsat_b4': RawDataConfig.LANDSAT_B4_DIR,
            'landsat_b5': RawDataConfig.LANDSAT_B5_DIR,
            'prism_daily': RawDataConfig.PRISM_DIR
        },
        'note': 'Raw data ready for processing/alignment phase. Only missing data was fetched.',
        'caching': 'Spatial coverage checking enabled - overlapping requests use existing data'
    }), 200

def execute_data_collection(request_id: str, request_data: dict):
    """
    Execute data collection with spatial coverage checking
    """
    print(f"Starting data collection for request {request_id}")
    
    try:
        date_from = request_data['date_from']
        date_to = request_data['date_to']
        geometry_json = json.dumps(request_data['geometry'])
        
        # Parse AOI
        area_of_interest = RawDataUtils.parse_geometry(geometry_json)
        print(f"AOI bounds: {area_of_interest.bounds}")
        
        # Step 1: Check spatial coverage
        job_manager.update_status(request_id, JobStatus.CHECKING_COVERAGE)
        
        datasets_to_fetch = []
        
        # Check Landsat coverage
        if not coverage_checker.is_covered('landsat', area_of_interest, date_from, date_to):
            datasets_to_fetch.append('landsat')
        else:
            print("Landsat data already covered - skipping download")
            job_manager.update_status(request_id, JobStatus.LANDSAT_SKIPPED)
        
        # Check PRISM coverage
        if not coverage_checker.is_covered('prism', area_of_interest, date_from, date_to):
            datasets_to_fetch.append('prism')
        else:
            print("PRISM data already covered - skipping download")
            job_manager.update_status(request_id, JobStatus.PRISM_SKIPPED)
        
        # Check NLDAS coverage
        if not coverage_checker.is_covered('nldas', area_of_interest, date_from, date_to):
            datasets_to_fetch.append('nldas')
        else:
            print("NLDAS data already covered - skipping download")
            job_manager.update_status(request_id, JobStatus.NLDAS_SKIPPED)
        
        # Step 2: Execute needed collections
        if not datasets_to_fetch:
            print("All data already available locally - no fetching needed!")
            job_manager.update_status(request_id, JobStatus.SUCCESS)
            return
        
        print(f"Need to fetch {len(datasets_to_fetch)} datasets: {datasets_to_fetch}")
        
        # Fetch each dataset
        for dataset in datasets_to_fetch:
            try:
                # Update status to started
                if dataset == 'landsat':
                    job_manager.update_status(request_id, JobStatus.LANDSAT_STARTED)
                elif dataset == 'prism':
                    job_manager.update_status(request_id, JobStatus.PRISM_STARTED)
                elif dataset == 'nldas':
                    job_manager.update_status(request_id, JobStatus.NLDAS_STARTED)
                
                print(f"Starting {dataset} raw data collection...")
                success = fetch_manager.fetch_dataset(dataset, date_from, date_to, geometry_json)
                
                if success:
                    # Update status to done
                    if dataset == 'landsat':
                        job_manager.update_status(request_id, JobStatus.LANDSAT_DONE)
                    elif dataset == 'prism':
                        job_manager.update_status(request_id, JobStatus.PRISM_DONE)
                    elif dataset == 'nldas':
                        job_manager.update_status(request_id, JobStatus.NLDAS_DONE)
                    
                    print(f"✓ Completed {dataset} raw data collection")
                else:
                    # Update status to error
                    if dataset == 'landsat':
                        job_manager.update_status(request_id, JobStatus.LANDSAT_ERROR, f"{dataset} fetch failed")
                    elif dataset == 'prism':
                        job_manager.update_status(request_id, JobStatus.PRISM_ERROR, f"{dataset} fetch failed")
                    elif dataset == 'nldas':
                        job_manager.update_status(request_id, JobStatus.NLDAS_ERROR, f"{dataset} fetch failed")
                    
                    print(f"✗ {dataset} job failed")
                    job_manager.update_status(request_id, JobStatus.FAILED, f"{dataset}: fetch failed")
                    return
                    
            except Exception as e:
                print(f"✗ {dataset} job failed with exception: {e}")
                if dataset == 'landsat':
                    job_manager.update_status(request_id, JobStatus.LANDSAT_ERROR, str(e))
                elif dataset == 'prism':
                    job_manager.update_status(request_id, JobStatus.PRISM_ERROR, str(e))
                elif dataset == 'nldas':
                    job_manager.update_status(request_id, JobStatus.NLDAS_ERROR, str(e))
                
                job_manager.update_status(request_id, JobStatus.FAILED, f"{dataset}: {str(e)}")
                return
        
        job_manager.update_status(request_id, JobStatus.SUCCESS)
        print(f"Data collection completed for request {request_id}")
        
    except Exception as e:
        print(f"Error in data collection: {e}")
        job_manager.update_status(request_id, JobStatus.FAILED, str(e))
