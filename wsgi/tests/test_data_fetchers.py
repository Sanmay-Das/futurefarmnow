import pytest
from raw_data_modules.data_fetchers import LandsatFetcher, PRISMFetcher, NLDASFetcher

def test_landsat_fetcher_initialization():
    """Test LandsatFetcher can be initialized"""
    fetcher = LandsatFetcher()
    assert fetcher is not None

def test_prism_fetcher_initialization():
    """Test PRISMFetcher can be initialized"""
    fetcher = PRISMFetcher()
    assert fetcher is not None

def test_nldas_fetcher_initialization():
    """Test NLDASFetcher can be initialized"""
    fetcher = NLDASFetcher()
    assert fetcher is not None
    assert hasattr(fetcher, 'session')