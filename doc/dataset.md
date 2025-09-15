# FutureFarmNow Dataset Documentation
## NLDAS
1. Login to https://urs.earthdata.nasa.gov/home

2. Go to Applications -> Authorised Apps and approve **NASA GESDISC DATA ARCHIVE**

Terminal:
```
printf "machine urs.earthdata.nasa.gov login <USER> password <PASS>\n" > ~/.netrc
chmod 600 ~/.netrc
touch ~/.urs_cookies
printf "HTTP.COOKIEJAR=$HOME/.urs_cookies\nHTTP.NETRC=$HOME/.netrc\n" > ~/.dodsrc
```
If issues occur, try adding:

Terminal:
```
export NETRC=$HOME/.netrc
```
## Elevation
1. Go to https://landfire.gov/topographic/elevation 

2. Select CONUS

3. Filter Theme as Topographic

4. Download elevation data under the Elevation - ELEV section

## NLCD
Go to https://www.sciencebase.gov/catalog/item/6810c1a4d4be022940554075 and download the latest year NLCD data


