#!/usr/bin/env python3
# ETmap blueprint with Landsat + PRISM + NLDAS + NLCD integration

import os
import sys
import json
import uuid
import threading
import sqlite3
import numpy as np
import requests
import zipfile
import tempfile
from datetime import datetime, timedelta
from flask import Blueprint, request, jsonify, send_file, redirect, url_for
from shapely.geometry import shape, mapping
import rasterio
from rasterio.mask import mask
from rasterio.warp import reproject, Resampling, transform_geom
from affine import Affine
from pystac_client import Client
import planetary_computer
import xarray as xr
import pandas as pd
import rioxarray  # noqa: F401
import shutil
import pynldas2 as nldas

# Blueprint for ET mapping endpoint
etmap_bp = Blueprint('etmap_bp', __name__)

# Paths & DB
db_path      = os.path.join(os.path.dirname(__file__), 'etmap.db')
gridspec     = os.path.join(os.path.dirname(__file__), 'grid_meta.json')
ETMAP_DATA_DIR = os.path.join(os.path.dirname(__file__), 'ETmap_data')
RESULTS_DIR    = os.path.join(os.path.dirname(__file__), 'results')
NLCD_FILE      = os.path.join(os.path.dirname(__file__),
    'output', 'NLCD',
    'Annual_NLCD_LndCov_2024_CU_C1V1',
    'Annual_NLCD_LndCov_2024_CU_C1V1.tif'
)
# Ensure dirs exist
os.makedirs(ETMAP_DATA_DIR, exist_ok=True)
os.makedirs(RESULTS_DIR, exist_ok=True)

# Initialize SQLite connection
conn   = sqlite3.connect(db_path, check_same_thread=False)
cursor = conn.cursor()

# Create table if not exists
cursor.execute('''
CREATE TABLE IF NOT EXISTS etmap_jobs (
    uniqueid      TEXT PRIMARY KEY,
    date_from     TEXT,
    date_to       TEXT,
    geometry      TEXT,
    status        TEXT,
    request_json  TEXT,
    created_at    TEXT
)
''')
conn.commit()

# Load global grid metadata
def load_grid():
    with open(gridspec) as f:
        return json.load(f)
grid_meta = load_grid()

# Helper to update job status
def update_status(job_id, status):
    cursor.execute('UPDATE etmap_jobs SET status=? WHERE uniqueid=?', (status, job_id))
    conn.commit()

# ------------------- Landsat -------------------
def run_landsat_job(job_id, date_from, date_to, geom_json):
    update_status(job_id, 'landsat: started')
    outdir = os.path.join(ETMAP_DATA_DIR, job_id, 'landsat')
    os.makedirs(outdir, exist_ok=True)
    aoi = shape(json.loads(geom_json))

    catalog = Client.open(
        "https://planetarycomputer.microsoft.com/api/stac/v1",
        modifier=planetary_computer.sign_inplace
    )
    search = catalog.search(
        collections=["landsat-c2-l2"],
        intersects=mapping(aoi),
        datetime=f"{date_from}/{date_to}"
    )
    items = list(search.item_collection())

    gm = grid_meta
    dst_affine = Affine(*gm['transform'])
    dst_width, dst_height = gm['size_px']
    dst_crs = gm['crs']

    for item in items:
        date_str = item.datetime.date().isoformat()
        href = planetary_computer.sign_url(item.assets['red'].href)
        try:
            with rasterio.open(href) as src:
                poly = transform_geom('EPSG:4326', src.crs, mapping(aoi))
                clipped, t_clip = mask(src, [poly], crop=True)
                arr = clipped[0]
                out_arr = np.empty((dst_height, dst_width), dtype=arr.dtype)
                reproject(
                    source=arr,
                    destination=out_arr,
                    src_transform=t_clip,
                    src_crs=src.crs,
                    dst_transform=dst_affine,
                    dst_crs=dst_crs,
                    resampling=Resampling.bilinear
                )
                profile = src.profile.copy()
                profile.update({
                    'crs': dst_crs,
                    'transform': dst_affine,
                    'width': dst_width,
                    'height': dst_height,
                    'count': 1
                })
                out_fp = os.path.join(outdir, f"{date_str}_red.tif")
                with rasterio.open(out_fp, 'w', **profile) as dst:
                    dst.write(out_arr, 1)
        except Exception as e:
            print(f"Error fetching Landsat {date_str}: {e}", file=sys.stderr)
    update_status(job_id, 'landsat: done')

# ------------------- PRISM -------------------
PRISM_VARS = ["ppt","tmin","tmax","tmean","tdmean","vpdmin","vpdmax"]
REGION = "us"
RESOLUTION = "4km"

def run_prism_job(job_id, date_from, date_to, geom_json):
    update_status(job_id, 'prism: started')
    outdir = os.path.join(ETMAP_DATA_DIR, job_id, 'prism')
    os.makedirs(outdir, exist_ok=True)
    aoi = shape(json.loads(geom_json))

    cur = datetime.fromisoformat(date_from)
    end = datetime.fromisoformat(date_to)
    while cur <= end:
        ymd = cur.strftime('%Y%m%d')
        mm = cur.strftime('%m-%d')
        day_dir = os.path.join(outdir, mm)
        os.makedirs(day_dir, exist_ok=True)
        rasters = []
        for var in PRISM_VARS:
            url = f"https://services.nacse.org/prism/data/get/{REGION}/{RESOLUTION}/{var}/{ymd}"
            try:
                r = requests.get(url, stream=True)
                r.raise_for_status()
                content = r.content
                ct = r.headers.get('Content-Type','')
                with tempfile.TemporaryDirectory() as td:
                    p = os.path.join(td, f"{var}_{ymd}")
                    with open(p, 'wb') as f:
                        f.write(content)
                    if 'zip' in ct or content.startswith(b'PK'):
                        with zipfile.ZipFile(p) as z:
                            z.extractall(td)
                        tifs = [f for f in os.listdir(td) if f.lower().endswith('.tif')]
                        p = os.path.join(td, tifs[0])
                    elif not p.lower().endswith('.tif'):
                        newp = p + '.tif'
                        os.rename(p, newp)
                        p = newp
                    da = rioxarray.open_rasterio(p).squeeze('band', drop=True)
                    rasters.append(da)
            except Exception as e:
                print(f"Error PRISM {var} {ymd}: {e}", file=sys.stderr)
        if rasters:
            idx = pd.Index(PRISM_VARS, name='band')
            stack = xr.concat(rasters, dim=idx)
            stack.rio.write_crs('EPSG:4326', inplace=True)
            clipped = stack.rio.clip([mapping(aoi)], crs='EPSG:4326', drop=True)
            clipped.rio.to_raster(os.path.join(day_dir, f"prism_{mm}.tif"))
        cur += timedelta(days=1)
    update_status(job_id, 'prism: done')

# ------------------- NLDAS -------------------
def run_nldas_job(job_id, date_from, date_to, geom_json):
    update_status(job_id, 'nldas: started')
    outdir = os.path.join(ETMAP_DATA_DIR, job_id, 'nldas')
    os.makedirs(outdir, exist_ok=True)
    aoi = shape(json.loads(geom_json))
    ds = nldas.get_bygeom(aoi, date_from, date_to).rio.write_crs('EPSG:4326', inplace=False)
    for var in ds.data_vars:
        var_dir = os.path.join(outdir, var)
        os.makedirs(var_dir, exist_ok=True)
        for t in ds.time.values:
            da = ds[var].sel(time=t).rio.clip([mapping(aoi)], crs='EPSG:4326', drop=True)
            ts = np.datetime_as_string(t, unit='h').replace('T','')
            da.rio.to_raster(os.path.join(var_dir, f"{var}_{ts}.tif"))
    update_status(job_id, 'nldas: done')

# ------------------- NLCD -------------------
def run_nlcd_job(job_id, date_from, date_to, geom_json):
    update_status(job_id, 'nlcd: started')
    outdir = os.path.join(ETMAP_DATA_DIR, job_id, 'nlcd')
    os.makedirs(outdir, exist_ok=True)
    aoi = shape(json.loads(geom_json))
    try:
        with rasterio.open(NLCD_FILE) as src:
            poly = transform_geom('EPSG:4326', src.crs, mapping(aoi))
            clipped, t_clip = mask(src, [poly], crop=True)
            arr = clipped[0]
            gm = grid_meta
            dst_affine = Affine(*gm['transform'])
            w, h = gm['size_px']
            out_arr = np.empty((h, w), dtype=arr.dtype)
            reproject(
                source=arr,
                destination=out_arr,
                src_transform=t_clip,
                src_crs=src.crs,
                dst_transform=dst_affine,
                dst_crs=gm['crs'],
                resampling=Resampling.nearest
            )
            profile = src.profile.copy()
            profile.update({
                'crs': gm['crs'],
                'transform': dst_affine,
                'width': w,
                'height': h,
                'count': 1
            })
            out_fp = os.path.join(outdir, 'nlcd_resamp.tif')
            with rasterio.open(out_fp, 'w', **profile) as dst:
                dst.write(out_arr, 1)
    except Exception as e:
        print(f"Error NLCD: {e}", file=sys.stderr)
    update_status(job_id, 'nlcd: done')

# ------------------- Combined runner -------------------
def run_all_jobs(job_id, date_from, date_to, geom_json):
    for name, fn in [
        ('landsat', run_landsat_job),
        ('prism', run_prism_job),
        ('nldas', run_nldas_job),
        ('nlcd',  run_nlcd_job)
    ]:
        try:
            fn(job_id, date_from, date_to, geom_json)
        except Exception as e:
            print(f"{name} job failed: {e}", file=sys.stderr)
            update_status(job_id, f"{name}: failed")
    update_status(job_id, 'success')
    # After successful completion, copy placeholder to results folder
    placeholder_src = os.path.join(os.path.dirname(__file__), 'placeholder.png')
    placeholder_dst = os.path.join(RESULTS_DIR, f"{job_id}.png")
    if os.path.isfile(placeholder_src):
        shutil.copy(placeholder_src, placeholder_dst)

# ------------------- Flask routes -------------------
@etmap_bp.route('/ETmap', methods=['POST'])
def etmap_start():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'Invalid JSON'}), 400
    for fld in ('date_from','date_to','geometry'):
        if fld not in data:
            return jsonify({'error': f'Missing {fld}'}), 400
    try:
        shape(data['geometry'])
    except Exception as e:
        return jsonify({'error': 'Invalid geometry', 'details': str(e)}), 400

    date_from = data['date_from']
    date_to   = data['date_to']
    cursor.execute(
        'SELECT uniqueid, request_json FROM etmap_jobs WHERE date_from=? AND date_to=?',
        (date_from, date_to)
    )
    for existing_uid, req_json in cursor.fetchall():
        prev = json.loads(req_json)
        if prev.get('geometry') == data['geometry']:
            return jsonify({'uniqueid': existing_uid}), 200

    job_id   = str(uuid.uuid4())
    now      = datetime.utcnow().isoformat()
    geom_json= json.dumps(data['geometry'], sort_keys=True)
    req_json = json.dumps(data, sort_keys=True)
    cursor.execute(
        'INSERT INTO etmap_jobs(uniqueid,date_from,date_to,geometry,status,request_json,created_at) VALUES (?,?,?,?,?,?,?)',
        (job_id, date_from, date_to, geom_json, 'queued', req_json, now)
    )
    conn.commit()
    threading.Thread(
        target=run_all_jobs,
        args=(job_id, date_from, date_to, geom_json),
        daemon=True
    ).start()
    return jsonify({'uniqueid': job_id}), 200


@etmap_bp.route('/ETmap/<string:job_id>.json', methods=['GET'])
def etmap_status(job_id):
    try:
        uuid.UUID(job_id)
    except ValueError:
        return jsonify({'error':'Invalid UUID'}), 400
    cursor.execute('SELECT status, created_at, request_json FROM etmap_jobs WHERE uniqueid=?', (job_id,))
    row = cursor.fetchone()
    if not row:
        return jsonify({'error':'Unknown job ID'}), 404
    status, created_at, req_json = row
    return jsonify({'status': status, 'created_at': created_at, 'request': json.loads(req_json)}), 200


@etmap_bp.route('/ETmap/<string:job_id>.png', methods=['GET'])
def etmap_result_png(job_id):
    try:
        uuid.UUID(job_id)
    except ValueError:
        return jsonify({'error':'Invalid UUID'}), 400
    cursor.execute('SELECT status FROM etmap_jobs WHERE uniqueid=?', (job_id,))
    row = cursor.fetchone()
    if not row:
        return jsonify({'error':'Unknown job ID'}), 404
    status = row[0]
    if status != 'success':
        return redirect(url_for('etmap_bp.etmap_status', job_id=job_id))
    placeholder = os.path.join(os.path.dirname(__file__),'dummysoilmap.png')
    if not os.path.isfile(placeholder):
        return jsonify({'error':'No result image'}), 500
    return send_file(placeholder, mimetype='image/png')
