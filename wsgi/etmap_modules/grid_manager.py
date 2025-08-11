#!/usr/bin/env python3
"""
ETMap Grid Manager Module
Handles unified grid computation and raster alignment
"""

import os
import numpy as np
import rasterio
from rasterio.warp import calculate_default_transform, reproject, Resampling, transform_geom
from affine import Affine
from shapely.geometry import shape, mapping
from pyproj import CRS, Transformer
from typing import Dict, List

from .config import ETMapConfig
from .utils import LoggingUtils


class UnifiedGridManager:
    """
    Manages unified grid computation and alignment for all datasets.
    Based on your Scala code's Raster_metadata and AOI_metadata functions.
    """
    
    def __init__(self, target_crs: str = None):
        self.target_crs = target_crs or ETMapConfig.TARGET_CRS
        self.grid_metadata = None
        
    def compute_unified_grid(self, aoi_geometry, sample_datasets: List[str], aoi_crs: str = 'EPSG:4326') -> Dict:
        """
        Compute unified grid covering AOI + all input datasets.
        Equivalent to Scala's Raster_metadata function.
        
        Args:
            aoi_geometry: Area of interest geometry
            sample_datasets: List of sample dataset paths
            aoi_crs: CRS of AOI geometry
            
        Returns:
            Grid metadata dictionary
        """
        LoggingUtils.print_step_header("Computing Unified Grid Metadata")
        
        # Initialize bounds tracking - equivalent to Scala's minX1, maxX2, etc.
        min_x = float('inf')
        max_x = float('-inf')
        min_y = float('inf')
        max_y = float('-inf')
        min_cell_x = float('inf')
        min_cell_y = float('inf')
        
        # Handle AOI bounds - transform if needed
        if aoi_crs != self.target_crs:
            try:
                aoi_bounds_geometry = transform_geom(aoi_crs, self.target_crs, mapping(aoi_geometry))
                aoi_bounds = shape(aoi_bounds_geometry).bounds
            except Exception as e:
                LoggingUtils.print_warning(f"Could not transform AOI geometry: {e}")
                aoi_bounds = aoi_geometry.bounds
        else:
            aoi_bounds = aoi_geometry.bounds
            
        # Update bounds with AOI
        min_x = min(min_x, aoi_bounds[0])
        min_y = min(min_y, aoi_bounds[1])
        max_x = max(max_x, aoi_bounds[2])
        max_y = max(max_y, aoi_bounds[3])
        
        # Analyze sample datasets - equivalent to Scala's allMetadata.foreach loop
        valid_datasets_count = 0
        for dataset_path in sample_datasets:
            if os.path.exists(dataset_path):
                try:
                    with rasterio.open(dataset_path) as source:
                        print(f"Processing sample: {os.path.basename(dataset_path)}")
                        
                        # Get source CRS and bounds
                        source_crs = source.crs
                        bounds = source.bounds
                        
                        # Transform bounds to target CRS if needed
                        if source_crs != CRS.from_string(self.target_crs):
                            corners = [
                                [bounds.left, bounds.bottom],
                                [bounds.right, bounds.bottom],
                                [bounds.right, bounds.top],
                                [bounds.left, bounds.top]
                            ]
                            
                            transformer = Transformer.from_crs(source_crs, self.target_crs, always_xy=True)
                            transformed_corners = [transformer.transform(x, y) for x, y in corners]
                            
                            xs, ys = zip(*transformed_corners)
                            bounds_minx, bounds_maxx = min(xs), max(xs)
                            bounds_miny, bounds_maxy = min(ys), max(ys)
                            
                            dst_transform, dst_width, dst_height = calculate_default_transform(
                                source_crs, self.target_crs, source.width, source.height, *bounds
                            )
                            cell_x = abs(dst_transform.a)
                            cell_y = abs(dst_transform.e)
                        else:
                            bounds_minx, bounds_miny = bounds.left, bounds.bottom
                            bounds_maxx, bounds_maxy = bounds.right, bounds.top
                            cell_x = abs(source.transform.a)
                            cell_y = abs(source.transform.e)
                        
                        # Update global bounds
                        min_x = min(min_x, bounds_minx)
                        min_y = min(min_y, bounds_miny)
                        max_x = max(max_x, bounds_maxx)
                        max_y = max(max_y, bounds_maxy)
                        
                        # Track minimum cell size
                        min_cell_x = min(min_cell_x, cell_x)
                        min_cell_y = min(min_cell_y, cell_y)
                        
                        valid_datasets_count += 1
                        
                except Exception as e:
                    LoggingUtils.print_warning(f"Could not read {dataset_path}: {e}")
                    continue
        
        if valid_datasets_count == 0:
            min_cell_x = min_cell_y = ETMapConfig.DEFAULT_CELL_SIZE
            LoggingUtils.print_warning("No valid sample datasets, using default 30m resolution")
            
        # Calculate grid dimensions
        grid_width = int(np.floor(abs(max_x - min_x) / min_cell_x))
        grid_height = int(np.floor(abs(max_y - min_y) / min_cell_y))
        
        # Create affine transform for unified grid
        grid_transform = Affine(min_cell_x, 0.0, min_x, 0.0, -min_cell_y, max_y)
        
        self.grid_metadata = {
            'crs': self.target_crs,
            'transform': grid_transform,
            'width': grid_width,
            'height': grid_height,
            'bounds': (min_x, min_y, max_x, max_y),
            'cell_size': (min_cell_x, min_cell_y),
            'valid_datasets_count': valid_datasets_count
        }
        
        print(f"Unified grid computed:")
        print(f"  Dimensions: {grid_width} x {grid_height} pixels")
        print(f"  Cell size: {min_cell_x:.8f} x {min_cell_y:.8f} degrees")
        print(f"  Bounds: ({min_x:.6f}, {min_y:.6f}, {max_x:.6f}, {max_y:.6f})")
        print(f"  Sample datasets used: {valid_datasets_count}")
        
        return self.grid_metadata
    
    def clip_to_aoi(self, aoi_geometry, aoi_crs: str = 'EPSG:4326') -> Dict:
        """
        Refine grid to AOI bounds only.
        Equivalent to Scala's AOI_metadata function.
        
        Args:
            aoi_geometry: Area of interest geometry
            aoi_crs: CRS of AOI geometry
            
        Returns:
            Clipped AOI metadata dictionary
        """
        if not self.grid_metadata:
            raise ValueError("Must compute unified grid first")
            
        LoggingUtils.print_step_header("Clipping Grid to AOI")
        
        # Get AOI bounds in target CRS
        if aoi_crs != self.target_crs:
            try:
                aoi_bounds_geometry = transform_geom(aoi_crs, self.target_crs, mapping(aoi_geometry))
                aoi_bounds = shape(aoi_bounds_geometry).bounds
            except Exception as e:
                LoggingUtils.print_warning(f"Could not transform AOI geometry: {e}")
                aoi_bounds = aoi_geometry.bounds
        else:
            aoi_bounds = aoi_geometry.bounds
            
        # Intersect with global bounds
        global_bounds = self.grid_metadata['bounds']
        clipped_bounds = (
            max(aoi_bounds[0], global_bounds[0]),
            max(aoi_bounds[1], global_bounds[1]),
            min(aoi_bounds[2], global_bounds[2]),
            min(aoi_bounds[3], global_bounds[3])
        )
        
        # Calculate new grid dimensions for clipped area
        cell_x, cell_y = self.grid_metadata['cell_size']
        clipped_width = int(np.floor((clipped_bounds[2] - clipped_bounds[0]) / cell_x))
        clipped_height = int(np.floor((clipped_bounds[3] - clipped_bounds[1]) / cell_y))
        
        # Create new transform for clipped grid
        clipped_transform = Affine(cell_x, 0.0, clipped_bounds[0],
                                 0.0, -cell_y, clipped_bounds[3])
        
        aoi_metadata = {
            'crs': self.target_crs,
            'transform': clipped_transform,
            'width': clipped_width,
            'height': clipped_height,
            'bounds': clipped_bounds,
            'cell_size': (cell_x, cell_y),
            'geometry': aoi_geometry
        }
        
        print(f"AOI grid clipped:")
        print(f"  Dimensions: {clipped_width} x {clipped_height} pixels")
        print(f"  Bounds: ({clipped_bounds[0]:.6f}, {clipped_bounds[1]:.6f}, "
              f"{clipped_bounds[2]:.6f}, {clipped_bounds[3]:.6f})")
        
        return aoi_metadata
    
    def align_raster_to_grid(self, source_path: str, output_path: str,
                           grid_metadata: Dict, resampling_method=Resampling.nearest) -> bool:
        """
        Align a single raster to the unified grid.
        Equivalent to Scala's RasterOperationsFocal.reshapeNN.
        
        Args:
            source_path: Path to source raster
            output_path: Path to output aligned raster
            grid_metadata: Grid metadata dictionary
            resampling_method: Resampling method to use
            
        Returns:
            True if successful, False otherwise
        """
        try:
            with rasterio.open(source_path) as source:
                print(f"Aligning {os.path.basename(source_path)} to unified grid...")
                
                # Create output array
                output_array = np.empty(
                    (source.count, grid_metadata['height'], grid_metadata['width']),
                    dtype=source.dtypes[0]
                )
                
                # Reproject to unified grid
                reproject(
                    source=rasterio.band(source, list(range(1, source.count + 1))),
                    destination=output_array,
                    src_transform=source.transform,
                    src_crs=source.crs,
                    dst_transform=grid_metadata['transform'],
                    dst_crs=grid_metadata['crs'],
                    resampling=resampling_method
                )
                
                # Write aligned raster
                profile = ETMapConfig.GEOTIFF_PROFILE.copy()
                profile.update({
                    'dtype': source.dtypes[0],
                    'nodata': source.nodata if source.nodata is not None else -9999,
                    'width': grid_metadata['width'],
                    'height': grid_metadata['height'],
                    'count': source.count,
                    'crs': grid_metadata['crs'],
                    'transform': grid_metadata['transform']
                })
                
                os.makedirs(os.path.dirname(output_path), exist_ok=True)
                with rasterio.open(output_path, 'w', **profile) as destination:
                    destination.write(output_array)
                
                LoggingUtils.print_success(f"Successfully aligned to {grid_metadata['width']}x{grid_metadata['height']} grid")
                return True
                
        except Exception as e:
            LoggingUtils.print_error(f"Error aligning {source_path}: {e}")
            return False


class RasterProcessor:
    """
    Additional raster processing utilities
    """
    
    @staticmethod
    def get_raster_info(raster_path: str) -> Dict:
        """
        Get basic information about a raster file
        
        Args:
            raster_path: Path to raster file
            
        Returns:
            Dictionary with raster information
        """
        try:
            with rasterio.open(raster_path) as src:
                return {
                    'width': src.width,
                    'height': src.height,
                    'count': src.count,
                    'crs': src.crs.to_string() if src.crs else None,
                    'bounds': src.bounds,
                    'transform': src.transform,
                    'dtype': src.dtypes[0],
                    'nodata': src.nodata
                }
        except Exception as e:
            LoggingUtils.print_error(f"Error reading raster info from {raster_path}: {e}")
            return {}
    
    @staticmethod
    def validate_raster_alignment(raster_paths: List[str]) -> bool:
        """
        Check if multiple rasters are aligned (same grid)
        
        Args:
            raster_paths: List of raster file paths
            
        Returns:
            True if all rasters are aligned, False otherwise
        """
        if len(raster_paths) < 2:
            return True
        
        reference_info = RasterProcessor.get_raster_info(raster_paths[0])
        if not reference_info:
            return False
        
        for raster_path in raster_paths[1:]:
            info = RasterProcessor.get_raster_info(raster_path)
            if not info:
                return False
            
            # Check critical alignment parameters
            if (info['width'] != reference_info['width'] or
                info['height'] != reference_info['height'] or
                info['transform'] != reference_info['transform'] or
                info['crs'] != reference_info['crs']):
                LoggingUtils.print_error(f"Raster {raster_path} not aligned with reference")
                return False
        
        LoggingUtils.print_success("All rasters are properly aligned")
        return True