# FutureFarmNow Dataset Documentation
## Initial Setup
### Configure Base Paths
Update your base paths in **etmap_modules/config.py** to point to your futurefarmnow project directory
```
DATA_BASE_PATH = "/path/to/your/futurefarmnow/wsgi/ETmap_data"
RESULTS_BASE_PATH = "/path/to/your/futurefarmnow/wsgi/results"
```

## NLDAS
1. Login to https://urs.earthdata.nasa.gov/home
2. Go to Applications -> Authorised Apps and approve **NASA GESDISC DATA ARCHIVE**

#### Terminal:
```
printf "machine urs.earthdata.nasa.gov login <USER> password <PASS>\n" > ~/.netrc
chmod 600 ~/.netrc
touch ~/.urs_cookies
printf "HTTP.COOKIEJAR=$HOME/.urs_cookies\nHTTP.NETRC=$HOME/.netrc\n" > ~/.dodsrc
```
If issues occur, try adding:

#### Terminal:
```
export NETRC=$HOME/.netrc
```
## Elevation
1. Go to https://landfire.gov/topographic/elevation 
2. Select CONUS
3. Filter Theme as Topographic
4. Download elevation data under the Elevation - ELEV section

### Folder Structure
```
ETmap_data/LF2020_Elev_220_CONUS/Tif/LC20_Elev_220.tif
```


## NLCD
Go to https://www.sciencebase.gov/catalog/item/6810c1a4d4be022940554075 and download the desired year NLCD data

### Folder Structure
```
ETmap_data/NLCD/Annual_NLCD_LndCov_{YEAR}_CU_C1V1/Annual_NLCD_LndCov_{YEAR}_CU_C1V1.tif
```
### Examples
#### For 2019
```
ETmap_data/NLCD/Annual_NLCD_LndCov_2019_CU_C1V1/Annual_NLCD_LndCov_2019_CU_C1V1.tif
```
#### For 2024
```
ETmap_data/NLCD/Annual_NLCD_LndCov_2024_CU_C1V1/Annual_NLCD_LndCov_2024_CU_C1V1.tif
```
### Configuration Update after downloading
1. Open **etmap_modules/config.py**
2. Find the line **AVAILABLE_NLCD_YEARS = [2019, 2024]**
3. Add your downloaded year to the list

### Example
If you download 2023 NLCD data:

#### Extract to
```
ETmap_data/NLCD/Annual_NLCD_LndCov_2023_CU_C1V1/Annual_NLCD_LndCov_2023_CU_C1V1.tif
```
#### Update config
```
AVAILABLE_NLCD_YEARS = [2019, 2024, 2023]
```
*Note*: You must update the config file each time you add new NLCD data, otherwise the system won't recognize the new year and will fall back to the closest available year.


## SSURGO
1. Go to https://www.sciencebase.gov/catalog/item/5fd7c19cd34e30b9123cb51f
2. Navigate to **Attached Files** and download **awc_gNATSGO.zip** and **fc_gNATSGO.zip**
### Folder Structure
```
ETmap/Soil_Data/awc_gNATSGO_US.tif
ETmap/Soil_Data/fc_gNATSGO_US.tif
```
