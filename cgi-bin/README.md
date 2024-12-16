# Development Setup

Create a virtual environment:
```shell
# Create a virtual environment
python3 -m venv ffn-env
# Activate the virtual environment
source ffn-env/bin/activate # or ffn-env\Scripts\activate
# Install gdal
brew install gdal # or pip install gdal==3.9.2
# Install required packages in the virtual environment
pip install numpy gdal geojson scipy flask
# Start a Python server that runs the CGI scripts
python3 cgi-bin/soil_sample.py
# When you're done, deactivate the virtual environment
deactivate
```

# Soil Sample
To test soil sample in development mode, run the server:
```shell
pip install pandas numpy geopandas shapely pyproj rasterio scikit-learn scipy pysal esda libpysal pyDOE3 pykrige tqdm flask
python3 cgi-bin/soil_sample.py
```

Navigate to (http://localhost:8000/public_html/soil_sample.html)