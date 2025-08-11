#!/usr/bin/env python3
"""
ETMap Utilities Module
Common utility functions and database management
"""

import os
import json
import sqlite3
import glob
import numpy as np
import geopandas as gpd
from typing import Dict, List, Optional
from shapely.geometry import shape, mapping
from datetime import datetime


class DatabaseManager:
    """
    Manages database connections and job lookups
    """
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        
    def get_job_info(self, request_id: str) -> Optional[Dict]:
        """
        Get job information from database using request_id
        
        Args:
            request_id: UUID of the request
            
        Returns:
            Dictionary with job information or None if not found
        """
        try:
            print(f"Looking for database at: {self.db_path}")
            print(f"Database file exists: {os.path.exists(self.db_path)}")
            
            connection = sqlite3.connect(self.db_path)
            cursor = connection.cursor()
            
            # First, check what tables exist in the database
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
            tables = cursor.fetchall()
            print(f"Available tables in database: {[table[0] for table in tables]}")
            
            # Query the etmap_jobs table
            cursor.execute(
                'SELECT date_from, date_to, geometry, request_json, status FROM etmap_jobs WHERE request_id=?',
                (request_id,)
            )
            row = cursor.fetchone()
            connection.close()
            
            if row:
                date_from, date_to, geometry, request_json, status = row
                print(f"Found job in database: {request_id}")
                return {
                    'date_from': date_from,
                    'date_to': date_to,
                    'geometry': json.loads(geometry),
                    'request_json': json.loads(request_json),
                    'status': status
                }
            else:
                print(f"Job not found in database: {request_id}")
                return None
                
        except Exception as e:
            print(f"Error querying database: {e}")
            print(f"Database path attempted: {self.db_path}")
            return None
    
    def update_job_status(self, request_id: str, status: str, error_message: str = None):
        """
        Update job status in database
        
        Args:
            request_id: UUID of the request
            status: New status
            error_message: Optional error message
        """
        try:
            connection = sqlite3.connect(self.db_path)
            cursor = connection.cursor()
            
            updated_at = datetime.utcnow().isoformat()
            cursor.execute(
                'UPDATE etmap_jobs SET status=?, updated_at=?, error_message=? WHERE request_id=?',
                (status, updated_at, error_message, request_id)
            )
            connection.commit()
            connection.close()
            
        except Exception as e:
            print(f"Error updating job status: {e}")


class FileManager:
    """
    Handles file operations and path management
    """
    
    @staticmethod
    def ensure_directory_exists(directory_path: str):
        """
        Create directory if it doesn't exist
        
        Args:
            directory_path: Path to directory
        """
        os.makedirs(directory_path, exist_ok=True)
    
    @staticmethod
    def find_files_by_pattern(pattern: str) -> List[str]:
        """
        Find files matching a glob pattern
        
        Args:
            pattern: Glob pattern
            
        Returns:
            List of file paths
        """
        return sorted(glob.glob(pattern))
    
    @staticmethod
    def get_date_folders(base_path: str, date_from: str, date_to: str) -> List[str]:
        """
        Get date folders within a date range
        
        Args:
            base_path: Base directory path
            date_from: Start date (YYYY-MM-DD)
            date_to: End date (YYYY-MM-DD)
            
        Returns:
            List of date folder names
        """
        if not os.path.exists(base_path):
            return []
        
        # Convert dates to comparable format
        start_date = date_from.replace('-', '_')  # 2024-03-16 -> 2024_03_16
        end_date = date_to.replace('-', '_')
        
        filtered_folders = []
        for item in os.listdir(base_path):
            item_path = os.path.join(base_path, item)
            if os.path.isdir(item_path):
                # Handle both 2024-03-16 and 2024_03_16 formats
                normalized_item = item.replace('-', '_')
                if start_date <= normalized_item <= end_date:
                    filtered_folders.append(item)
        
        return sorted(filtered_folders)
    
    @staticmethod
    def save_json(data: Dict, file_path: str):
        """
        Save data as JSON file
        
        Args:
            data: Data to save
            file_path: Output file path
        """
        FileManager.ensure_directory_exists(os.path.dirname(file_path))
        with open(file_path, 'w') as f:
            json.dump(data, f, indent=2)
    
    @staticmethod
    def load_json(file_path: str) -> Optional[Dict]:
        """
        Load JSON file
        
        Args:
            file_path: File path to load
            
        Returns:
            Loaded data or None if error
        """
        try:
            with open(file_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading JSON file {file_path}: {e}")
            return None


class GeospatialUtils:
    """
    Geospatial utility functions
    """
    
    @staticmethod
    def create_aoi_shapefile(geometry_dict: dict, output_shapefile: str):
        """
        Create AOI shapefile from geometry
        
        Args:
            geometry_dict: Geometry dictionary
            output_shapefile: Output shapefile path
        """
        aoi_geom = shape(geometry_dict)
        gdf = gpd.GeoDataFrame(
            {'id': [1], 'name': ['Dynamic_AOI'], 'source': ['curl_command']},
            geometry=[aoi_geom],
            crs='EPSG:4326'
        )
        
        FileManager.ensure_directory_exists(os.path.dirname(output_shapefile))
        gdf.to_file(output_shapefile)
        print(f"✓ AOI shapefile created: {output_shapefile}")
    
    @staticmethod
    def serialize_geometry_metadata(aoi_metadata: Dict) -> Dict:
        """
        Serialize geometry metadata for JSON storage
        
        Args:
            aoi_metadata: AOI metadata dictionary
            
        Returns:
            Serializable metadata dictionary
        """
        serializable_metadata = aoi_metadata.copy()
        
        # Convert affine transform to list
        if 'transform' in serializable_metadata:
            serializable_metadata['transform'] = list(aoi_metadata['transform'])[:6]
        
        # Convert geometry to GeoJSON
        if 'geometry' in serializable_metadata:
            serializable_metadata['geometry'] = mapping(aoi_metadata['geometry'])
        
        return serializable_metadata


class ArrayUtils:
    """
    Array manipulation utilities
    """
    
    @staticmethod
    def resize_array_to_target(array: np.ndarray, target_shape: tuple) -> np.ndarray:
        """
        Resize array to target shape
        
        Args:
            array: Input array
            target_shape: Target (height, width) shape
            
        Returns:
            Resized array
        """
        if array.shape == target_shape:
            return array.astype(np.float32)
        
        try:
            from scipy.ndimage import zoom
            zoom_factors = (target_shape[0] / array.shape[0], target_shape[1] / array.shape[1])
            resized_array = zoom(array, zoom_factors, order=1)
        except ImportError:
            # Fallback to simple numpy resizing if scipy not available
            resized_array = np.resize(array, target_shape)
        
        return resized_array.astype(np.float32)
    
    @staticmethod
    def calculate_lai_from_ndvi(ndvi: np.ndarray) -> np.ndarray:
        """
        Calculate LAI from NDVI - matching Scala's CalculationLAI().ndviLaiFunc
        
        Args:
            ndvi: NDVI array
            
        Returns:
            LAI array
        """
        # Using the same formula as in Scala code
        lai = np.where(ndvi > 0, 3.618 * ndvi - 0.118, 0.0)
        lai = np.clip(lai, 0.0, 8.0)
        return lai.astype(np.float32)
    
    @staticmethod
    def calculate_ndvi(b4_data: np.ndarray, b5_data: np.ndarray, nodata: float = -9999.0) -> np.ndarray:
        """
        Calculate NDVI from B4 (red) and B5 (NIR) bands
        
        Args:
            b4_data: Band 4 (red) data
            b5_data: Band 5 (NIR) data
            nodata: NoData value
            
        Returns:
            NDVI array
        """
        b4_data = b4_data.astype(np.float32)
        b5_data = b5_data.astype(np.float32)
        
        denominator = b5_data + b4_data
        valid_mask = (denominator != 0) & (b4_data != nodata) & (b5_data != nodata)
        
        ndvi = np.full(b4_data.shape, nodata, dtype=np.float32)
        ndvi[valid_mask] = (b5_data[valid_mask] - b4_data[valid_mask]) / denominator[valid_mask]
        ndvi = np.clip(ndvi, -1.0, 1.0, out=ndvi, where=valid_mask)
        
        return ndvi


class ValidationUtils:
    """
    Data validation utilities
    """
    
    @staticmethod
    def validate_uuid(uuid_string: str) -> bool:
        """
        Validate UUID format
        
        Args:
            uuid_string: UUID string to validate
            
        Returns:
            True if valid UUID, False otherwise
        """
        import uuid
        try:
            uuid.UUID(uuid_string)
            return True
        except ValueError:
            return False
    
    @staticmethod
    def validate_date_format(date_string: str) -> bool:
        """
        Validate date format (YYYY-MM-DD)
        
        Args:
            date_string: Date string to validate
            
        Returns:
            True if valid date format, False otherwise
        """
        try:
            datetime.fromisoformat(date_string)
            return True
        except ValueError:
            return False
    
    @staticmethod
    def validate_file_exists(file_path: str) -> bool:
        """
        Check if file exists
        
        Args:
            file_path: Path to file
            
        Returns:
            True if file exists, False otherwise
        """
        return os.path.exists(file_path) and os.path.isfile(file_path)


class LoggingUtils:
    """
    Logging and progress utilities
    """
    
    @staticmethod
    def print_progress_header(title: str, width: int = 60):
        """
        Print formatted progress header
        
        Args:
            title: Header title
            width: Header width
        """
        print("=" * width)
        print(title)
        print("=" * width)
    
    @staticmethod
    def print_step_header(step_name: str):
        """
        Print step header
        
        Args:
            step_name: Name of the step
        """
        print(f"\n=== {step_name} ===")
    
    @staticmethod
    def print_success(message: str):
        """
        Print success message
        
        Args:
            message: Success message
        """
        print(f"{message}")
    
    @staticmethod
    def print_error(message: str):
        """
        Print error message
        
        Args:
            message: Error message
        """
        print(f"{message}")
    
    @staticmethod
    def print_warning(message: str):
        """
        Print warning message
        
        Args:
            message: Warning message
        """
        print(f"{message}")