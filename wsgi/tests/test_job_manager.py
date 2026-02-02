import pytest
import tempfile
import os
from raw_data_modules.job_manager import RawDataJobManager
from raw_data_modules.database import RawDataDatabase

def test_job_manager_initialization():
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tmp:
        db_path = tmp.name
    
    try:
        db = RawDataDatabase(db_path)
        manager = RawDataJobManager(db)
        assert manager is not None
    finally:
        if os.path.exists(db_path):
            os.remove(db_path)

def test_job_creation():
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tmp:
        db_path = tmp.name
    
    try:
        db = RawDataDatabase(db_path)
        manager = RawDataJobManager(db)
        
        request_data = {
            'date_from': '2024-04-16',
            'date_to': '2024-04-17',
            'geometry': {
                'type': 'Polygon',
                'coordinates': [[
                    [-117.634048, 134.888466],
                    [-117.723999, 134.845649],
                    [-117.673874, 134.767845],
                    [-117.565384, 134.779126],
                    [-117.551651, 134.861989],
                    [-117.634048, 134.888466]
                ]]
            }
        }
        
        request_id = manager.create_job(request_data)
        assert request_id is not None
        assert len(request_id) == 36  # UUID format
    finally:
        if os.path.exists(db_path):
            os.remove(db_path)