# soil-salinity

This project combines California farmland vector data and satellite soil salinity data and displays the result in an interactive web interface.

## Features

- Aggregation function selection (minimum, maximum, average, standard deviation)
- Raster data selection
- Interactive farmland data front-end interface
- Dynamic extents

## Installation

### Dependencies

The soil salinity backend relies upon Java 1.8.0 and Scala 2.12.7.
For the Python part, you need Python 3.11 or later.
You also need gdal.

### Setup

This project expects all data files (shapefile, GeoTIFF) to be stored in the `data/` directory.
The data directory should be organized as follows:

![data directory](doc/images/directory_organization.png)

### Run in development
To run the server in development mode, run the class "`edu.ucr.cs.bdlab.beast.operations.Main`" with command line
argument `server -enableStaticFileHandling`. Open your browser and navigate to
(http://localhost:8890/frontend/index.html).

For the Python part, you need to create a virtual environment and run a [Flask](https://flask.palletsprojects.com) server on it.
```shell
# Create a virtual environment
python3 -m venv ffnenv
# Activate the virtual environment
source ffnenv/bin/activate # or ffn-env\Scripts\activate
# Install required packages in the virtual environment
pip install pandas numpy geopandas shapely pyproj rasterio scikit-learn scipy pysal esda libpysal pyDOE3 pykrige tqdm flask gdal
# Start a Python server that runs the WSGI scripts
flask --debug --app cgi-bin/server.py run
# When you're done, deactivate the virtual environment
deactivate
```

### Server deployment
1. Install Apache web server and required libraries to host the application.
    ```shell
    sudo apt install apache2 libgdal-dev gdal-bin libapache2-mod-wsgi-py3 apache2-dev -y
    pip install mod_wsgi
    ```
2. Create a directory to host the application and assign it to the right owner and group.
    ```shell
    sudo mkdir /var/www/ffn.example.com
    sudo chown user:www-data /var/www/ffn.example.com
    ```
   This creates a directory and assign your `user` as the owner and `www-data`, i.e., Apache, as the group.
3. Create a Python virtual environment in that directory to use for the Python server.
    ```shell
    cd /var/www/ffn.example.com
    # Create a virtual environment
    python3 -m venv ffnenv
    # Activate the virtual environment
    source ffnenv/bin/activate
    # Install required packages in the virtual environment
    pip install pandas numpy geopandas shapely pyproj rasterio scikit-learn scipy pysal esda libpysal pyDOE3 pykrige tqdm flask osgeo
    ```
4. Copy the static HTML files and code to the server.
    ```shell
    rsync -av --exclude=__pycache__ public_html/ remote_host:/var/www/ffn.example.com/public_html
    rsync -av --exclude=__pycache__ cgi-bin/ remote_host:/var/www/ffn.example.com/cgi-bin
    ```
    Place the `data/` on the server at which you want it to be hosted.
    Install Beast CLI and run the following command at the same directory where you have
    the `data` directory (not inside the `data` directory).
5. Start the Java server
    ```shell
    beast --jars futurefarmnow-backend-*.jar server
    ```

    In the directory where you run `beast server`, you can place a file `beast.properties` to set the default
    system parameters, e.g., `port`.

6. Configure Apache server.
    **Configure through Apache:** If you want to make the server accessible through Apache, you can add the following
    configuration to your Apache web server. Create the file `/etc/apache2/sites-available/ffn.yasemsem.com.conf`.

    ```
    <VirtualHost *:80>
        ServerAdmin admin@example.com
        ServerName ffn.example.com
        Redirect permanent / https://ffn.example.com/
    </VirtualHost>
    
    <IfModule mod_ssl.c>
    <VirtualHost *:443>
        ServerName ffn.example.com
        DocumentRoot /var/www/ffn.example.com/public_html
    
        # Serve static files from public_html
        Alias /static /var/www/ffn.example.com/public_html
    
        <Directory /var/www/ffn.example.com/public_html>
            Require all granted
            AllowOverride All
            RewriteEngine On
            #RewriteCond %{REQUEST_URI}  ^/futurefarmnow-backend-0.2-SNAPSHOT/(.*)$
            #RewriteRule ^futurefarmnow-backend-0.2-SNAPSHOT/(.*)$ http://localhost:8081/$1 [P,L]
            #RewriteCond %{REQUEST_URI}  ^/futurefarmnow-backend-[\.0-9]*(-[\w\d]+)?/(.*)$
            #RewriteRule ^(.*)$ http://localhost:8080/$1 [P,L]
       </Directory>
       # Redirect root URL to /static/
       RedirectMatch ^/$ /static/
    
    
       # Fallback for URLs not matching static files -> Send to WSGI
       WSGIDaemonProcess ffn.example.com python-home=/var/www/ffn.example.com/ffnenv threads=5
       WSGIProcessGroup ffn.example.com
       WSGIApplicationGroup %{GLOBAL}
       WSGIScriptAlias / /var/www/ffn.example.com/cgi-bin/wsgi.py
       <Directory /var/www/ffn.example.com/cgi-bin>
           Require all granted
       </Directory>

       ErrorLog ${APACHE_LOG_DIR}/ffn_error.log
       CustomLog ${APACHE_LOG_DIR}/ffn_access.log combined
    </VirtualHost>
    </IfModule>
    ```

    The first RewriteRule forwards all requests that begin with `/futurefarmnow-backend-0.2-SNAPSHOT/` to the server
    running on port 8081. The second rewrite rule forwards requests for any version to the server running on port 8080.
    This configuration allows you to deploy a different version of the API while keeping the stable version running.
7. Enable the site and restart Apache.
    ```shell
    sudo a2ensite ffn.yasemsem.com
    sudo systemctl reload apache2
    ```
### API
Check the detailed [API description here](doc/api.md).

### Add vector dataset
Check the [step-by-step instructions for adding a new vector dataset](doc/add-vector-dataset.md).

## License

Copyright 2024 University of California, Riverside

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.