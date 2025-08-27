import os
import glob
from datetime import datetime, timedelta
from shapely.geometry import shape, mapping, box
from shapely.ops import unary_union
import rasterio
from typing import Optional

from .config import RawDataConfig

class SpatialCoverageChecker:
    """
    Checks if existing local raw data covers the requested AOI
    """
    
    def is_covered(self, dataset: str, aoi_geometry, date_from: str, date_to: str) -> bool:
        """
        Check if dataset covers the AOI for the date range
        """
        if dataset == 'landsat':
            # ✓ Make Landsat coverage date-aware
            return self._check_landsat_coverage(aoi_geometry, date_from, date_to)
        elif dataset == 'prism':
            return self._check_prism_coverage(aoi_geometry, date_from, date_to)
        elif dataset == 'nldas':
            return self._check_nldas_coverage(aoi_geometry, date_from, date_to)
        else:
            raise ValueError(f"Unknown dataset: {dataset}")
    
    def get_coverage_summary(self, aoi_geometry, date_from: str, date_to: str) -> dict:
        """
        Get detailed coverage summary for all datasets
        """
        summary = {
            'landsat': {
                'covered': self._check_landsat_coverage(aoi_geometry, date_from, date_to),
                'details': self._get_landsat_coverage_details(date_from, date_to)
            },
            'prism': {
                'covered': self._check_prism_coverage(aoi_geometry, date_from, date_to),
                'details': self._get_prism_coverage_details(date_from, date_to)
            },
            'nldas': {
                'covered': self._check_nldas_coverage(aoi_geometry, date_from, date_to),
                'details': self._get_nldas_coverage_details(date_from, date_to)
            }
        }
        
        total_covered = sum(1 for ds in summary.values() if ds['covered'])
        summary['overall'] = {
            'datasets_covered': total_covered,
            'total_datasets': 3,
            'coverage_percentage': (total_covered / 3) * 100,
            'needs_fetching': [ds for ds in ['landsat', 'prism', 'nldas'] if not summary[ds]['covered']]
        }
        
        return summary
    
    def _check_landsat_coverage(self, aoi_geometry, date_from: Optional[str] = None, date_to: Optional[str] = None) -> bool:
        """
        Check Landsat coverage (date-aware if dates provided).
        We consider scenes (B4/B5) whose filenames end with _YYYY-MM-DD.tif within the requested date window.
        """
        print("Checking existing Landsat coverage...")
        
        # Date filter set
        dates_set = None
        if date_from and date_to:
            cur = datetime.fromisoformat(date_from).date()
            end = datetime.fromisoformat(date_to).date()
            dates_set = set()
            while cur <= end:
                dates_set.add(cur.isoformat())
                cur += timedelta(days=1)

        def _iter_files(dir_path: str):
            for tif_file in glob.glob(os.path.join(dir_path, "*.tif")):
                if dates_set:
                    base = os.path.basename(tif_file)
                    name_no_ext = os.path.splitext(base)[0]
                    parts = name_no_ext.split('_')
                    if not parts:
                        continue
                    date_part = parts[-1]
                    if date_part not in dates_set:
                        continue
                yield tif_file

        coverage_polygons = []
        
        # Check B4 files (date-filtered if dates_set)
        b4_files = list(_iter_files(RawDataConfig.LANDSAT_B4_DIR))
        print(f"Found {len(b4_files)} existing Landsat B4 files in date window" if dates_set else f"Found {len(b4_files)} existing Landsat B4 files")
        
        for tif_file in b4_files:
            try:
                with rasterio.open(tif_file) as src:
                    bounds = src.bounds
                    bounds_polygon = box(bounds.left, bounds.bottom, bounds.right, bounds.top)
                    
                    # Transform to WGS84 if needed
                    if src.crs and src.crs.to_string() != 'EPSG:4326':
                        from rasterio.warp import transform_geom
                        bounds_polygon = shape(transform_geom(src.crs, 'EPSG:4326', mapping(bounds_polygon)))
                    
                    coverage_polygons.append(bounds_polygon)
                    
            except Exception as e:
                print(f"Warning: Could not read bounds from {tif_file}: {e}")
                continue
        
        if coverage_polygons:
            total_coverage = unary_union(coverage_polygons)
            is_covered = total_coverage.contains(aoi_geometry)
            print(f"✓ Landsat coverage computed: {len(coverage_polygons)} scenes, covered: {is_covered}")
            return is_covered
        else:
            print("No existing Landsat coverage found in the requested dates" if dates_set else "No existing Landsat coverage found")
            return False
    
    def _check_prism_coverage(self, aoi_geometry, date_from: str, date_to: str) -> bool:
        """
        Check PRISM coverage
        """
        print(f"Checking existing PRISM coverage for {date_from} to {date_to}...")
        
        current_date = datetime.fromisoformat(date_from)
        end_date = datetime.fromisoformat(date_to)
        
        found_dates = []
        prism_coverage = None
        
        while current_date <= end_date:
            date_str = current_date.strftime('%Y-%m-%d')
            date_folder = os.path.join(RawDataConfig.PRISM_DIR, date_str)
            
            if os.path.exists(date_folder):
                prism_files = glob.glob(os.path.join(date_folder, "*.tif"))
                if prism_files:
                    found_dates.append(date_str)
                    
                    if prism_coverage is None:
                        try:
                            with rasterio.open(prism_files[0]) as src:
                                bounds = src.bounds
                                if src.crs and src.crs.to_string() != 'EPSG:4326':
                                    from rasterio.warp import transform_geom
                                    bounds_geom = box(bounds.left, bounds.bottom, bounds.right, bounds.top)
                                    prism_coverage = shape(transform_geom(src.crs, 'EPSG:4326', mapping(bounds_geom)))
                                else:
                                    prism_coverage = box(bounds.left, bounds.bottom, bounds.right, bounds.top)
                        except Exception as e:
                            print(f"Warning: Could not read PRISM bounds: {e}")
            
            current_date += timedelta(days=1)
        
        required_days = (datetime.fromisoformat(date_to) - datetime.fromisoformat(date_from)).days + 1
        
        if len(found_dates) >= required_days and prism_coverage and prism_coverage.contains(aoi_geometry):
            print(f"✓ PRISM coverage found for {len(found_dates)}/{required_days} required dates")
            return True
        else:
            print(f"PRISM coverage incomplete: {len(found_dates)}/{required_days} dates available")
            return False
    
    def _check_nldas_coverage(self, aoi_geometry, date_from: str, date_to: str) -> bool:
        """
        Check NLDAS coverage for date range with proper spatial caching
        """
        print(f"Checking existing NLDAS coverage for {date_from} to {date_to}...")
        
        current_date = datetime.fromisoformat(date_from)
        end_date = datetime.fromisoformat(date_to)
        
        total_required_hours = 0
        found_hours = 0
        nldas_coverage = None
        coverage_polygons = []
        
        while current_date <= end_date:
            year = current_date.year
            nldas_year_dir = RawDataConfig.get_nldas_dir(year)
            date_str = current_date.strftime('%Y-%m-%d')
            date_folder = os.path.join(nldas_year_dir, date_str)
            
            total_required_hours += 24  # 24 hours per day
            
            if os.path.exists(date_folder):
                nldas_files = glob.glob(os.path.join(date_folder, "*.tif"))
                found_hours += len(nldas_files)
                
                for nldas_file in nldas_files:
                    try:
                        with rasterio.open(nldas_file) as src:
                            bounds = src.bounds
                            bounds_polygon = box(bounds.left, bounds.bottom, bounds.right, bounds.top)
                            
                            if src.crs and src.crs.to_string() != 'EPSG:4326':
                                from rasterio.warp import transform_geom
                                bounds_polygon = shape(transform_geom(src.crs, 'EPSG:4326', mapping(bounds_polygon)))
                            
                            coverage_polygons.append(bounds_polygon)
                            
                            if nldas_coverage is None:
                                nldas_coverage = bounds_polygon
                                
                    except Exception as e:
                        print(f"Warning: Could not read NLDAS bounds from {nldas_file}: {e}")
                        continue
            
            current_date += timedelta(days=1)
        
        coverage_ratio = found_hours / total_required_hours if total_required_hours > 0 else 0
        
        if coverage_polygons:
            try:
                total_nldas_coverage = unary_union(coverage_polygons)
                spatial_coverage = total_nldas_coverage.contains(aoi_geometry)
                print(f"✓ NLDAS spatial coverage computed: {len(coverage_polygons)} files, AOI covered: {spatial_coverage}")
            except Exception as e:
                print(f"Warning: Could not compute NLDAS spatial union: {e}")
                spatial_coverage = nldas_coverage.contains(aoi_geometry) if nldas_coverage else False
        else:
            spatial_coverage = False
        
        if coverage_ratio >= 0.9 and spatial_coverage:
            print(f"✓ NLDAS coverage found: {found_hours}/{total_required_hours} hours ({coverage_ratio:.1%}) with spatial coverage")
            return True
        else:
            if coverage_ratio < 0.9:
                print(f"NLDAS temporal coverage incomplete: {found_hours}/{total_required_hours} hours ({coverage_ratio:.1%})")
            if not spatial_coverage:
                print(f"NLDAS spatial coverage incomplete: AOI not fully covered by existing files")
            return False
    
    def _get_landsat_coverage_details(self, date_from: Optional[str] = None, date_to: Optional[str] = None) -> dict:
        """Get detailed Landsat coverage information (date-aware if dates provided)"""
        dates_set = None
        if date_from and date_to:
            cur = datetime.fromisoformat(date_from).date()
            end = datetime.fromisoformat(date_to).date()
            dates_set = set()
            while cur <= end:
                dates_set.add(cur.isoformat())
                cur += timedelta(days=1)

        def _filtered(dir_path: str):
            files = glob.glob(os.path.join(dir_path, "*.tif"))
            if not dates_set:
                return files
            keep = []
            for f in files:
                base = os.path.basename(f)
                name_no_ext = os.path.splitext(base)[0]
                date_part = name_no_ext.split('_')[-1]
                if date_part in dates_set:
                    keep.append(f)
            return keep

        b4_files = _filtered(RawDataConfig.LANDSAT_B4_DIR)
        b5_files = _filtered(RawDataConfig.LANDSAT_B5_DIR)
        
        # Count unique scenes (by scene_id portion in the filename)
        def _scene_id(path):
            base = os.path.basename(path)
            name_no_ext = os.path.splitext(base)[0]
            parts = name_no_ext.split('_')
            # B4_<scene_id>_<date> → scene_id = parts[1:-1] joined in case scene_id has underscores
            return '_'.join(parts[1:-1]) if len(parts) > 2 else (parts[1] if len(parts) > 1 else "")
        
        total_scenes = len(set([_scene_id(f) for f in (b4_files + b5_files)]))
        
        return {
            'b4_scenes': len(b4_files),
            'b5_scenes': len(b5_files),
            'total_scenes': total_scenes,
            'file_paths': {
                'b4_dir': RawDataConfig.LANDSAT_B4_DIR,
                'b5_dir': RawDataConfig.LANDSAT_B5_DIR
            }
        }
    
    def _get_prism_coverage_details(self, date_from: str, date_to: str) -> dict:
        """Get detailed PRISM coverage information"""
        current_date = datetime.fromisoformat(date_from)
        end_date = datetime.fromisoformat(date_to)
        
        total_days = (end_date - current_date).days + 1
        covered_days = 0
        total_files = 0
        
        while current_date <= end_date:
            date_str = current_date.strftime('%Y-%m-%d')
            date_folder = os.path.join(RawDataConfig.PRISM_DIR, date_str)
            
            if os.path.exists(date_folder):
                prism_files = glob.glob(os.path.join(date_folder, "*.tif"))
                if prism_files:
                    covered_days += 1
                    total_files += len(prism_files)
            
            current_date += timedelta(days=1)
        
        return {
            'covered_days': covered_days,
            'total_days': total_days,
            'coverage_percentage': (covered_days / total_days * 100) if total_days > 0 else 0,
            'total_files': total_files,
            'variables': RawDataConfig.PRISM_VARIABLES,
            'base_dir': RawDataConfig.PRISM_DIR
        }
    
    def _get_nldas_coverage_details(self, date_from: str, date_to: str) -> dict:
        """Get detailed NLDAS coverage information"""
        current_date = datetime.fromisoformat(date_from)
        end_date = datetime.fromisoformat(date_to)
        
        total_required_hours = 0
        found_hours = 0
        covered_days = 0
        total_days = (end_date - current_date).days + 1
        
        while current_date <= end_date:
            year = current_date.year
            nldas_year_dir = RawDataConfig.get_nldas_dir(year)
            date_str = current_date.strftime('%Y-%m-%d')
            date_folder = os.path.join(nldas_year_dir, date_str)
            
            total_required_hours += 24
            
            if os.path.exists(date_folder):
                nldas_files = glob.glob(os.path.join(date_folder, "*.tif"))
                day_hours = len(nldas_files)
                found_hours += day_hours
                
                if day_hours >= 20:  # Consider day covered if >= 20 hours available
                    covered_days += 1
            
            current_date += timedelta(days=1)
        
        return {
            'found_hours': found_hours,
            'required_hours': total_required_hours,
            'hour_coverage_percentage': (found_hours / total_required_hours * 100) if total_required_hours > 0 else 0,
            'covered_days': covered_days,
            'total_days': total_days,
            'day_coverage_percentage': (covered_days / total_days * 100) if total_days > 0 else 0,
            'base_dir': RawDataConfig.get_nldas_dir(datetime.fromisoformat(date_from).year)
        }
