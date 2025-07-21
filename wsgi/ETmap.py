#!/usr/bin/env python3
import sys
import json
import uuid
import os
import sqlite3
from flask import Blueprint, request, jsonify, send_file
from shapely.geometry import shape

# Blueprint for ET mapping endpoint
etmap_bp = Blueprint('etmap_bp', __name__)

# Database initialization
module_dir = os.path.dirname(__file__)
db_path = os.path.join(module_dir, 'etmap.db')
conn = sqlite3.connect(db_path, check_same_thread=False)
cursor = conn.cursor()

# Create table if not exists without dropping existing data
cursor.execute('''
CREATE TABLE IF NOT EXISTS etmap_jobs (
    uniqueid TEXT PRIMARY KEY,
    date_from TEXT,
    date_to TEXT,
    geometry TEXT,
    status TEXT
)
''')
conn.commit()

@etmap_bp.route('/ETmap', methods=['POST'])
def etmap_start():
    """
    Start ETmap job: POST /ETmap with JSON containing date_from, date_to, and geometry.
    Generates a UUID, stores job in SQLite with status 'in progress', and returns the UUID.
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'Request body must be valid JSON'}), 400
    for field in ('date_from', 'date_to', 'geometry'):
        if field not in data:
            return jsonify({'error': f"Missing '{field}'"}), 400
    date_from = data['date_from']
    date_to = data['date_to']
    geom = data['geometry']
    if not isinstance(geom, dict) or geom.get('type') != 'Polygon':
        return jsonify({'error': 'Geometry must be a GeoJSON Polygon'}), 400
    try:
        _ = shape(geom)
    except Exception as e:
        return jsonify({'error': 'Invalid geometry', 'details': str(e)}), 400

    geom_json = json.dumps(geom, sort_keys=True)
    # Check for existing job
    cursor.execute(
        'SELECT uniqueid, geometry FROM etmap_jobs WHERE date_from = ? AND date_to = ?',
        (date_from, date_to)
    )
    for existing_uid, geom_str in cursor.fetchall():
        try:
            existing_geom = json.loads(geom_str)
        except json.JSONDecodeError:
            continue
        if existing_geom == geom:
            print(f"ETmap job already exists: {existing_uid}", file=sys.stderr)
            return jsonify({'uniqueid': existing_uid}), 200

    # Create new job
    uniqueid = str(uuid.uuid4())
    cursor.execute(
        'INSERT INTO etmap_jobs(uniqueid, date_from, date_to, geometry, status) VALUES (?, ?, ?, ?, ?)',
        (uniqueid, date_from, date_to, geom_json, 'in progress')
    )
    conn.commit()
    print(f"ETmap job created: {uniqueid}", file=sys.stderr)
    return jsonify({'uniqueid': uniqueid}), 200

@etmap_bp.route('/ETmap/<string:uniqueid>.json', methods=['GET'])
def etmap_status(uniqueid):
    """
    GET /ETmap/<uniqueid>.json returns the job status.
    """
    try:
        uuid.UUID(uniqueid)
    except ValueError:
        return jsonify({'error': 'Invalid UUID'}), 400
    cursor.execute('SELECT status FROM etmap_jobs WHERE uniqueid = ?', (uniqueid,))
    row = cursor.fetchone()
    if not row:
        return jsonify({'error': f"Unknown uniqueid '{uniqueid}'"}), 404
    return jsonify({'status': row[0]}), 200

@etmap_bp.route('/ETmap/<string:uniqueid>.png', methods=['GET'])
def etmap_result_png(uniqueid):
    """
    GET /ETmap/<uniqueid>.png serves the placeholder image and updates status to 'done'.
    """
    try:
        uuid.UUID(uniqueid)
    except ValueError:
        return jsonify({'error': 'Invalid UUID'}), 400
    cursor.execute('SELECT status FROM etmap_jobs WHERE uniqueid = ?', (uniqueid,))
    if not cursor.fetchone():
        return jsonify({'error': f"Unknown uniqueid '{uniqueid}'"}), 404
    image_path = os.path.join(module_dir, 'placeholder.png')
    if not os.path.isfile(image_path):
        return jsonify({'error': 'Result image not found'}), 500

    # Update status to done
    cursor.execute('UPDATE etmap_jobs SET status = ? WHERE uniqueid = ?', ('done', uniqueid))
    conn.commit()
    return send_file(image_path, mimetype='image/png')
