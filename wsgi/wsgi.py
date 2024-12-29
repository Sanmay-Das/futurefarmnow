#!/var/www/sites/raptor.cs.ucr.edu/ffnenv/bin/python3

import sys
import os

# Add the application directory to the Python path
sys.path.insert(0, os.path.dirname(__file__))

from server import app as application
