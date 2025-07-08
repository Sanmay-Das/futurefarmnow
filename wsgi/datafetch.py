import os
import json
from datetime import datetime, timedelta
from shapely.geometry import shape, mapping, Point
from shapely.wkt import dumps as to_wkt
import geopandas as gpd
import py3dep
import numpy as np
import requests
import zipfile
import tempfile
import rasterio
import rasterio.mask
import pynldas2 as nldas
from concurrent.futures import ThreadPoolExecutor, as_completed

def write_points(path, records, x_field="x", y_field="y", crs="EPSG:4326"):
    gdf = gpd.GeoDataFrame(
        records,
        geometry=[Point(r[x_field], r[y_field]) for r in records],
        crs=crs
    )
    gdf.to_file(path, driver="GeoJSON")

def fetch_3dep_elevation(polygon_geojson: dict, resolution: int = 30) -> dict:
    geom = shape(polygon_geojson)
    dem = py3dep.get_dem(geom, resolution)
    results = []
    idx = 0
    for yi, y in enumerate(dem.y.values):
        for xi, x in enumerate(dem.x.values):
            val = dem.isel(x=xi, y=yi).item()
            if np.isnan(val):
                continue
            results.append({"id": idx, "x": float(x), "y": float(y), "elevation": float(val)})
            idx += 1
    return {"query": {"resolution_m": resolution}, "results": results}

def fetch_ssurgo_components(polygon_geojson: dict) -> dict:
    wkt = to_wkt(shape(polygon_geojson))
    sql = f"""
    SELECT mu.mukey, mu.musym, co.cokey, co.compname, co.comppct_r, co.taxclname
    FROM mapunit AS mu
    JOIN component AS co ON mu.mukey = co.mukey
    WHERE mu.mukey IN (
      SELECT DISTINCT mukey FROM SDA_Get_Mukey_from_intersection_with_WktWgs84('{wkt}')
    )
    """
    resp = requests.post(
        "https://SDMDataAccess.sc.egov.usda.gov/Tabular/post.rest",
        data={"query": sql, "format": "JSON+COLUMNNAME"}
    )
    resp.raise_for_status()
    raw = resp.json()
    table = next(v for v in raw.values() if isinstance(v, list))
    cols = table[0]
    rows = table[1:]
    results = [dict(zip(cols, row)) for row in rows]
    return {"query": {}, "results": results}

def fetch_nldas_timeseries(polygon_geojson: dict, date_from: str, date_to: str) -> dict:
    ds = nldas.get_bygeom(shape(polygon_geojson), date_from, date_to)
    df = ds.to_dataframe().reset_index()
    results = []
    for idx, ((lon, lat), group) in enumerate(df.groupby(["x","y"])):
        series = {var: group[var].tolist() for var in ds.data_vars}
        series["time"] = group["time"].astype(str).tolist()
        results.append({"id": idx, "x": float(lon), "y": float(lat), "series": series})
    return {"query": {"from": date_from, "to": date_to}, "results": results}

def fetch_prism_timeseries(polygon_geojson: dict, start_date: str, end_date: str,
                           region: str = "us", resolution: str = "4km",
                           variables: list = None) -> dict:
    if variables is None:
        variables = ["ppt","tmin","tmax","tmean","tdmean","vpdmin","vpdmax"]
    times = []
    points_data = {}
    start = datetime.strptime(start_date, "%Y-%m-%d")
    stop  = datetime.strptime(end_date,   "%Y-%m-%d")
    delta = timedelta(days=1)
    poly = shape(polygon_geojson)
    current = start
    while current <= stop:
        date_str = current.strftime("%Y%m%d")
        times.append(current.strftime("%Y-%m-%d"))
        for var in variables:
            url = f"https://services.nacse.org/prism/data/get/{region}/{resolution}/{var}/{date_str}"
            r = requests.get(url, stream=True); r.raise_for_status()
            with tempfile.TemporaryDirectory() as tmpdir:
                zpath = os.path.join(tmpdir, f"{var}_{date_str}.zip")
                with open(zpath,'wb') as f: f.write(r.content)
                with zipfile.ZipFile(zpath,'r') as z: z.extractall(tmpdir)
                tif = next((os.path.join(tmpdir,f) for f in os.listdir(tmpdir) if f.lower().endswith('.tif')), None)
                if not tif: continue
                with rasterio.open(tif) as src:
                    out_img, out_transform = rasterio.mask.mask(src, [mapping(poly)], crop=True)
                    arr = out_img[0]; nodata = src.nodata
                for i in range(arr.shape[0]):
                    for j in range(arr.shape[1]):
                        val = arr[i,j]
                        if nodata is not None and val==nodata: continue
                        x,y = rasterio.transform.xy(out_transform,i,j,offset='center')
                        key=(round(x,6),round(y,6))
                        points_data.setdefault(key,{v:[] for v in variables})[var].append(float(val))
        current += delta
    results = []
    for idx,(xy,vars_) in enumerate(points_data.items()):
        results.append({"id": idx, "x": xy[0], "y": xy[1], "series": {"time": times, **vars_}})
    return {"query": {"from": start_date, "to": end_date}, "results": results}

if __name__ == '__main__':
    sample_geom = {"type":"Polygon","coordinates":[[[-117.52,33.86],[-117.20,33.86],[-117.20,34.05],[-117.52,34.05],[-117.52,33.86]]]}
    date_from, date_to = "2010-01-01", "2011-01-01"

    elevation = fetch_3dep_elevation(sample_geom, resolution=30)
    ssurgo    = fetch_ssurgo_components(sample_geom)
    nldas     = fetch_nldas_timeseries(sample_geom, date_from, date_to)
    prism     = fetch_prism_timeseries(sample_geom, date_from, date_to)

    output = {"elevation_3dep": elevation,
              "ssurgo":        ssurgo,
              "nldas2":        nldas,
              "prism":         prism}

    # Write outputs
    os.makedirs("output", exist_ok=True)
    write_points("output/elevation_3dep.geojson", elevation["results"], x_field="x", y_field="y")
    # SSURGO has no coords, writing only JSON
    with open("output/ssurgo_components.json", "w") as f:
        json.dump(ssurgo, f, indent=2)
    write_points("output/nldas2.geojson", nldas["results"], x_field="x", y_field="y")
    write_points("output/prism_timeseries.geojson", prism["results"], x_field="x", y_field="y")

    with open("output/all_data.json", "w") as f:
        json.dump(output, f, indent=2)

    print("Outputs written to output/ directory (GeoJSON and JSON files)")
    print("All fetches complete.")
