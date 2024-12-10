# Setup
```shell
# Create Virtual Environment
python3 -m venv ffnenv

# Activate
source ffnenv/bin/activate

# Install required packages
# pip install --upgrade pip # If you need to upgrade pip
pip install -r requirements.txt
```

# Setup Credentials
If you don't already have an account, create a Copernicus Data Space Ecosystem
on [this page](https://dataspace.copernicus.eu).


Place your credentials in the file `~/.netrc` which is similar to the file below:
```
# ~/.netrc
machine https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token
login konata@izumi.com
password password123
```

Add your login and password and adjust the permissions to the file `chmod 600 ~/.netrc`

*Note:* Never share this file or push it to your git repository to avoid illegal access.

To test that your credentials are correct, run `python cdse_auth.py`

# Download Data
To download data, use the `download_sentintel2.py` script. A sample usage is provided below.

```shell
python download_sentinel2.py --date-from 2023-01-01 --date-to 2023-02-28 --roi "POLYGON((-117.4105 33.9463, -117.4105 34.0024, -117.3011 34.0024, -117.3011 33.9463, -117.4105 33.9463))" --output ./sentinel_data --verbosity default
```

The region of interest (ROI) can also point to a GeoJSON file that contains one polygon.

Below is a sample GeoJSON file you can use:
```json
{
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [
                        [-120.0, 35.0],
                        [-119.0, 35.0],
                        [-119.0, 36.0],
                        [-120.0, 36.0],
                        [-120.0, 35.0]
                    ]
                ]
            },
            "properties": {}
        }
    ]
}
```

# References
[CDSE Tool](https://github.com/CDSETool/CDSETool)