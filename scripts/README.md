# Development Setup
For first time, setup the development environment as [indicated here](setup.md).

# Download Sentinel-2 data
Use the script `download_sentinel2.py` for downloading data.

```shell
python download_sentinel2.py --date-from 2023-01-01 --date-to 2023-01-31 --roi "POLYGON((-117.4105 33.9463, -117.4105 34.0024, -117.3011 34.0024, -117.3011 33.9463, -117.4105 33.9463))" --output ./sentinel_data
```