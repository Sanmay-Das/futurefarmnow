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
pip install pandas numpy geopandas shapely pyproj rasterio scikit-learn scipy pysal esda libpysal pyDOE3 pykrige tqdm flask
# Start a Python server that runs the CGI scripts
python3 cgi-bin/server.py
# When you're done, deactivate the virtual environment
deactivate
```

Navigate to (http://localhost:8000/public_html/soil_sample.html)

# Production Mode
This part describes how to deploy this app behind Apache webserver.

1. Install required packages.
    ```shell
    sudo apt-get install apache2 libapache2-mod-wsgi-py3 # On Centos: sudo yum install mod_wsgi -y
    ```
2. Copy the `cgi-bin` and `public_html` directories to your server, e.g., under `/var/www/sites/raptor.cs.ucr.edu`.
    ```shell
    rsync -av --exclude=__pycache__ public_html/ remote_host:/var/www/sites/raptor.cs.ucr.edu/public_html/ffn
    rsync -av --exclude=__pycache__ cgi-bin/ remote_host:/var/www/sites/raptor.cs.ucr.edu/cgi-bin
    ```
3. Install mod_wsgi in the virtual environment:
   ```shell
   pip install mod_wsgi
   ```
4. Generate the configuration for your virtual environment:
   ```shell
   mod_wsgi-express module-config
   ```
4. Configure Apache. You should already have a VirtualHost for your website. Add the following part to your configuration.
    ```
    <VirtualHost *:80>
        # Flask Applications (mod_wsgi for soil_stats and soil_sample)
        WSGIScriptAlias /soil /path/to/your/project/wsgi.py
        WSGIDaemonProcess soil_services python-path=/path/to/your/project:/path/to/your/project/venv/lib/python3.x/site-packages
        WSGIProcessGroup soil_services
        <Directory /path/to/your/project>
            Require all granted
        </Directory>
    </VirtualHost>
    ```