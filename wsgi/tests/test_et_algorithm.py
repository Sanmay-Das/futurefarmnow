import pytest
from etmap_modules.et_algorithm import ETAlgorithm

def test_et_algorithm_initialization():
    """Test ETAlgorithm can be initialized"""
    algo = ETAlgorithm()
    assert algo is not None

def test_et_algorithm_has_required_methods():
    """Test ETAlgorithm has expected methods"""
    algo = ETAlgorithm()
    assert hasattr(algo, 'create_enhanced_hourly_files_with_et')