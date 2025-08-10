import os

class RawDataConfig:
    """
    Configuration class for raw data fetching paths and settings
    """
    
    # Base output directory
    BASE_OUTPUT_DIR = os.path.join(os.path.dirname(__file__), '..', 'ETmap_data')
    
    # Database and results paths
    DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'etmap.db')
    RESULTS_DIR = os.path.join(os.path.dirname(__file__), '..', 'results')
    
    # Dataset-specific paths
    LANDSAT_B4_DIR = os.path.join(BASE_OUTPUT_DIR, 'Landsat_B4')
    LANDSAT_B5_DIR = os.path.join(BASE_OUTPUT_DIR, 'Landsat_B5')
    PRISM_DIR = os.path.join(BASE_OUTPUT_DIR, 'Prism_Daily')
    NLDAS_DIR = os.path.join(BASE_OUTPUT_DIR, 'NLDAS_2024_GeoTiff')  # Year will be dynamic
    
    # Fetching settings
    MAX_LANDSAT_SCENES = 50
    LANDSAT_COLLECTION = "landsat-c2-l2"
    
    # PRISM variables
    PRISM_VARIABLES = ["ppt", "tmin", "tmax", "tmean", "tdmean", "vpdmin", "vpdmax"]
    PRISM_BASE_URL = "https://services.nacse.org/prism/data/get/us/4km"
    
    # NLDAS settings
    NLDAS_BASE_URL = "https://hydro1.gesdisc.eosdis.nasa.gov/data/NLDAS/NLDAS_FORA0125_H.2.0"
    
    # Network settings
    DOWNLOAD_TIMEOUT = 120
    MAX_RETRIES = 2
    THROTTLE_SECONDS = 0.0
    
    @classmethod
    def ensure_directories(cls):
        """Create all necessary directories"""
        for directory in [cls.BASE_OUTPUT_DIR, cls.LANDSAT_B4_DIR, cls.LANDSAT_B5_DIR, 
                         cls.PRISM_DIR, cls.RESULTS_DIR]:
            os.makedirs(directory, exist_ok=True)
    
    @classmethod
    def get_nldas_dir(cls, year: int) -> str:
        """Get NLDAS directory for specific year"""
        return os.path.join(cls.BASE_OUTPUT_DIR, f'NLDAS_{year}_GeoTiff')
