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
pip install numpy gdal geojson scipy
# Start a Python server that runs the CGI scripts
python3 -m http.server --cgi 8000
# When you're done, deactivate the virtual environment
deactivate
```
