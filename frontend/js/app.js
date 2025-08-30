/**
 * ETMap Frontend Application
 * Dynamic dataset labels + special ET Calculation status
 */

console.log('ETMap application starting...');

// ========================== GLOBALS ==========================
let map = null;
let drawnItems = null;
let drawControl = null;
let polygon = null;

let etResultsLayer = null;
let etLayerControl = null;

let currentRequestId = null;
let pollInterval = null;
let lastRequestData = null;

const API_BASE_URL = 'http://127.0.0.1:5000';

// ============================ INIT ============================
document.addEventListener('DOMContentLoaded', () => {
  updateStatus('DOM loaded');
  initializeApp();
});

function initializeApp() {
  try {
    updateStatus('Initializing map...');
    initializeMap();

    updateStatus('Setting up event listeners...');
    initializeEventListeners();

    updateStatus('Ready! Draw a polygon to get started.');
  } catch (e) {
    console.error(e);
    updateStatus('ERROR: ' + e.message);
  }
}

// ============================= MAP ===========================
function initializeMap() {
  try {
    map = L.map('map').setView([35, -118.9], 10);
    document.getElementById('map-status').textContent = 'Created';

    const osm = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '© OpenStreetMap contributors'
    }).addTo(map);

    const sat = L.tileLayer(
      'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
      { attribution: 'Tiles © Esri and contributors' }
    );

    etLayerControl = L.control.layers({ OpenStreetMap: osm, Satellite: sat }, {}).addTo(map);
    document.getElementById('map-status').textContent = 'Tiles loaded';

    initializeCoordinateDisplay();
    initializeDrawControls();
  } catch (e) {
    document.getElementById('map-status').textContent = 'ERROR: ' + e.message;
    throw e;
  }
}

function initializeCoordinateDisplay() {
  map.on('mousemove', (e) => {
    const lat = e.latlng.lat.toFixed(6);
    const lng = e.latlng.lng.toFixed(6);
    document.getElementById('coordinates').textContent = `${lat}, ${lng}`;
  });
  map.on('mouseout', () => {
    document.getElementById('coordinates').textContent = 'Move mouse over map';
  });
}

function initializeDrawControls() {
  drawnItems = new L.FeatureGroup();
  map.addLayer(drawnItems);

  drawControl = new L.Control.Draw({
    draw: { polygon: true, polyline: false, rectangle: true, circle: false, marker: false, circlemarker: false },
    edit: { featureGroup: drawnItems }
  });
  map.addControl(drawControl);
  document.getElementById('draw-status').textContent = 'Ready';

  map.on(L.Draw.Event.CREATED, handleDrawCreated);
  map.on(L.Draw.Event.EDITED, handleDrawEdited);
  map.on(L.Draw.Event.DELETED, handleDrawDeleted);
}

// ========================= EVENT WIRING =======================
function initializeEventListeners() {
  const fromInput = document.getElementById('fromDate');
  const toInput = document.getElementById('toDate');
  const submitBtn = document.getElementById('submitBtn');
  const clearBtn = document.getElementById('clearBtn');
  const testBtn = document.getElementById('testBtn');

  if (fromInput) fromInput.addEventListener('change', validateForm);
  if (toInput) toInput.addEventListener('change', validateForm);
  if (submitBtn) submitBtn.addEventListener('click', handleSubmit);
  if (clearBtn) clearBtn.addEventListener('click', handleClear);
  if (testBtn) testBtn.addEventListener('click', handleTest);
}

// draw events
function handleDrawCreated(e) {
  drawnItems.clearLayers();
  polygon = e.layer;
  drawnItems.addLayer(polygon);
  updateGeometryInfo();
  validateForm();
}
function handleDrawEdited() {
  updateGeometryInfo();
  validateForm();
}
function handleDrawDeleted() {
  polygon = null;
  updateGeometryInfo();
  validateForm();
}

// ======================= FORM / GEOMETRY ======================
function updateGeometryInfo() {
  const info = document.getElementById('geometry-info');
  if (!info) return;
  if (polygon) {
    const b = polygon.getBounds();
    const area = (b.getNorth() - b.getSouth()) * (b.getEast() - b.getWest()) * 111 * 111;
    info.innerHTML = `
      <strong>Type:</strong> ${polygon.toGeoJSON().geometry.type}<br>
      <strong>Bounds:</strong> ${b.getSouth().toFixed(3)}, ${b.getWest().toFixed(3)} to ${b.getNorth().toFixed(3)}, ${b.getEast().toFixed(3)}<br>
      <strong>Area:</strong> ~${area.toFixed(1)} km²
    `;
  } else {
    info.innerHTML = 'Draw a polygon on the map to define your area of interest';
  }
}

function validateForm() {
  const fromInput = document.getElementById('fromDate');
  const toInput = document.getElementById('toDate');
  const submitBtn = document.getElementById('submitBtn');

  const validDates = fromInput?.value && toInput?.value && fromInput.value <= toInput.value;
  const ok = !!polygon && !!validDates;
  if (submitBtn) submitBtn.disabled = !ok;
}

// ======================== BUTTON HANDLERS =====================
async function handleSubmit() {
  const fromInput = document.getElementById('fromDate');
  const toInput = document.getElementById('toDate');
  const submitBtn = document.getElementById('submitBtn');

  if (!polygon || !fromInput?.value || !toInput?.value) {
    showError('Please fill all fields and draw an area on the map');
    return;
  }

  const requestData = {
    date_from: fromInput.value,
    date_to: toInput.value,
    geometry: polygon.toGeoJSON().geometry
  };
  lastRequestData = requestData;

  try {
    if (submitBtn) { submitBtn.disabled = true; submitBtn.textContent = 'Submitting...'; }
    const resp = await fetch(`${API_BASE_URL}/etmap`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(requestData)
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${resp.statusText}`);
    const result = await resp.json();
    currentRequestId = result.request_id;

    showStatusSection();
    updateRequestInfo(requestData);
    startPolling();
  } catch (e) {
    showError(`Failed to submit request: ${e.message}`);
    if (submitBtn) { submitBtn.disabled = false; submitBtn.textContent = 'Submit Request'; }
  }
}

function handleClear() {
  if (drawnItems) drawnItems.clearLayers();
  polygon = null;
  updateGeometryInfo();
  validateForm();
}

function handleTest() {
  const msg = `
Map: ${!!map}, Draw: ${!!drawControl}, Items: ${!!drawnItems}, Poly: ${!!polygon}
Request ID: ${currentRequestId || 'None'}
API: ${API_BASE_URL}
`;
  alert(msg);
}

// ====================== NAV SECTION TOGGLES ===================
function showStatusSection() {
  document.getElementById('form-section').classList.remove('active');
  document.getElementById('status-section').classList.add('active');
  document.getElementById('results-section').classList.remove('active');
}
function showResultsSection() {
  document.getElementById('form-section').classList.remove('active');
  document.getElementById('status-section').classList.remove('active');
  document.getElementById('results-section').classList.add('active');
}
function showFormSection() {
  document.getElementById('form-section').classList.add('active');
  document.getElementById('status-section').classList.remove('active');
  document.getElementById('results-section').classList.remove('active');
}

// ========================== ET OVERLAY ========================
function addETOverlayToMap(imageUrl) {
  try {
    if (etResultsLayer) {
      map.removeLayer(etResultsLayer);
      etLayerControl.removeLayer(etResultsLayer);
    }
    const etBounds = L.latLngBounds(
      [33.8152785, -117.430115],
      [33.939372, -117.2365819]
    );
    etResultsLayer = L.imageOverlay(imageUrl, etBounds, {
      opacity: 0.7, interactive: true, alt: 'ET Results Map'
    }).addTo(map);
    etLayerControl.addOverlay(etResultsLayer, 'ET Results');

    const combined = polygon ? etBounds.extend(polygon.getBounds()) : etBounds;
    map.fitBounds(combined, { padding: [20, 20] });

    updateStatus('ET results overlay added to map');

    etResultsLayer.bindPopup(`
      <div style="text-align:center;">
        <h4>ET Results</h4>
        <p><strong>Request ID:</strong> ${currentRequestId}</p>
        <button onclick="openETMapInNewTab()" style="padding:5px 10px;margin-top:5px;">View Full Size</button>
      </div>
    `);
  } catch (e) {
    showError('Failed to add ET overlay: ' + e.message);
  }
}

function showETOverlayControls() {
  const resultsInfo = document.getElementById('results-info');
  if (!document.getElementById('et-overlay-controls')) {
    const el = document.createElement('div');
    el.id = 'et-overlay-controls';
    el.innerHTML = `
      <div style="margin:1rem 0;padding:1rem;border:2px solid #28a745;border-radius:8px;background:#f8fff9;">
        <h4 style="color:#28a745;margin-bottom:1rem;">Map Overlay Controls</h4>
        <div style="display:flex;gap:.5rem;flex-wrap:wrap;">
          <button class="btn btn-secondary" onclick="toggleETOverlay()">Toggle Overlay</button>
          <button class="btn btn-secondary" onclick="adjustETOpacity(-0.1)">Less Transparent</button>
          <button class="btn btn-secondary" onclick="adjustETOpacity(0.1)">More Transparent</button>
          <button class="btn btn-secondary" onclick="fitToETResults()">Zoom to Results</button>
          <button class="btn btn-secondary" onclick="removeETOverlay()">Remove Overlay</button>
        </div>
      </div>`;
    resultsInfo.appendChild(el);
  }
}

function toggleETOverlay() {
  if (etResultsLayer) {
    if (map.hasLayer(etResultsLayer)) map.removeLayer(etResultsLayer);
    else map.addLayer(etResultsLayer);
  }
}
function adjustETOpacity(d) {
  if (etResultsLayer) {
    const o = etResultsLayer.options.opacity || 0.7;
    etResultsLayer.setOpacity(Math.max(0.1, Math.min(1.0, o + d)));
  }
}
function fitToETResults() {
  const etBounds = L.latLngBounds([33.8152785, -117.430115], [33.939372, -117.2365819]);
  map.fitBounds(etBounds, { padding: [20, 20] });
}
function removeETOverlay() {
  if (etResultsLayer) {
    map.removeLayer(etResultsLayer);
    etLayerControl.removeLayer(etResultsLayer);
    etResultsLayer = null;
    const c = document.getElementById('et-overlay-controls');
    if (c) c.remove();
    updateStatus('ET overlay removed from map');
  }
}

// ===================== API + STATUS POLLING ===================
function updateRequestInfo(requestData) {
  const info = document.getElementById('request-info');
  const b = polygon.getBounds();
  const area = (b.getNorth() - b.getSouth()) * (b.getEast() - b.getWest()) * 111 * 111;
  info.innerHTML = `
    <strong>Request ID:</strong> ${currentRequestId}<br>
    <strong>Date Range:</strong> ${requestData.date_from} to ${requestData.date_to}<br>
    <strong>Area:</strong> ~${area.toFixed(1)} km²
  `;
}

async function startPolling() {
  if (pollInterval) clearInterval(pollInterval);
  pollInterval = setInterval(checkStatus, 3000);
  await checkStatus();
}

async function checkStatus() {
  try {
    const resp = await fetch(`${API_BASE_URL}/etmap/${currentRequestId}.json`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${resp.statusText}`);
    const st = await resp.json();
    updateStatusDisplay(st);

    if (st.status === 'calculation_complete') {
      clearInterval(pollInterval);
      showResults();
    } else if (['failed', 'error', 'calculation_failed'].includes(st.status)) {
      clearInterval(pollInterval);
      showError(`Request failed: ${st.error_message || 'Unknown error'}`);
    } else if (st.status === 'success') {
      enableViewResultsIfAvailable();
    }
  } catch (e) {
    showError('Status check failed: ' + e.message);
  }
}

async function enableViewResultsIfAvailable() {
  if (!currentRequestId) return;
  try {
    const url = `${API_BASE_URL}/etmap/${currentRequestId}.png`;
    const head = await fetch(url, { method: 'HEAD' });
    if (head.ok) {
      clearInterval(pollInterval);
      showResults();
    }
  } catch (_) {}
}

// --------- Dynamic status (datasets: Downloading/Available; ET Calc: In Progress/Done) ---------
function updateStatusDisplay(status) {
  const statusList = document.getElementById('status-list');
  const progressFill = document.getElementById('progress-fill');

  const stages = [
    { key: 'queued',            label: 'Request Queued',    progress: 10 },
    { key: 'checking_coverage', label: 'Checking Coverage', progress: 15 },
    { key: 'landsat',           label: 'Landsat Data',      progress: 35 },
    { key: 'prism',             label: 'PRISM Data',        progress: 55 },
    { key: 'nldas',             label: 'NLDAS Data',        progress: 75 },
    { key: 'success',           label: 'Data Collection',   progress: 80 },
    { key: 'calculation',       label: 'ET Calculation',    progress: 100 }
  ];
  const order = stages.map(s => s.key);

  const groupOf = (s) => {
    if (!s) return null;
    if (s.startsWith('landsat_')) return 'landsat';
    if (s.startsWith('prism_'))   return 'prism';
    if (s.startsWith('nldas_'))   return 'nldas';
    if (s.startsWith('calculation_')) return 'calculation';
    if (['queued','checking_coverage','success'].includes(s)) return s;
    return null;
  };

  // Datasets: Downloading/Available; ET Calc: In Progress/Done
  const suffixFor = (s, key) => {
    if (!s) return '';
    if (key === 'calculation') {
      if (s === 'calculation_started')  return 'In Progress';
      if (s === 'calculation_complete') return 'Done';
      if (s === 'calculation_failed')   return 'Failed';
      return '';
    }
    if (s.startsWith(key + '_')) {
      const tail = s.slice(key.length + 1);
      if (tail === 'started') return 'Downloading';
      if (tail === 'done' || tail === 'skipped') return 'Available';
    }
    if (key === 'success' && s === 'success') return 'Available';
    return '';
  };

  const iconFor = (sfx) => {
    if (sfx === 'Downloading' || sfx === 'In Progress') return { icon: '⚡', cls: 'status-progress' };
    if (sfx === 'Available' || sfx === 'Done')          return { icon: '✅', cls: 'status-success' };
    if (sfx === 'Failed')                                return { icon: '❌', cls: 'status-error' };
    return { icon: '⏳', cls: 'status-waiting' };
  };

  const currentGroup = groupOf(status.status);
  const currentIndex = currentGroup ? order.indexOf(currentGroup) : -1;

  let currentProgress = 0;
  let html = '';

  stages.forEach((stage, idx) => {
    const isCurrent = stage.key === currentGroup;
    const isPast = currentIndex > idx;

    // Past datasets appear as Available; past calculation appears as Done once complete
    let suffix = '';
    if (isCurrent) {
      suffix = suffixFor(status.status, stage.key);
    } else if (isPast) {
      suffix = stage.key === 'calculation' ? 'Done'
             : stage.key === 'success'     ? 'Available'
             : 'Available';
    }

    const label = stage.label + (suffix ? ` (${suffix})` : '');
    const { icon, cls } = iconFor(suffix);

    if (isPast) currentProgress = Math.max(currentProgress, stage.progress);
    else if (isCurrent) currentProgress = stage.progress;

    const shouldShow = isPast || isCurrent || ['queued','checking_coverage'].includes(stage.key);
    if (shouldShow) {
      html += `
        <div class="status-item">
          <div class="status-icon ${cls}">${icon}</div>
          <span>${label}</span>
        </div>
      `;
    }
  });

  statusList.innerHTML = html;
  progressFill.style.width = `${currentProgress}%`;

  const cs = stages.find(s => s.key === currentGroup);
  if (cs) {
    const sfx = suffixFor(status.status, cs.key);
    updateStatus(cs.label + (sfx ? ` (${sfx})` : ''));
  }
}

// ============================ RESULTS =========================
function showResults() {
  showResultsSection();
  const resultsInfo = document.getElementById('results-info');
  resultsInfo.innerHTML = `
    <div class="request-info">
      <strong>Request ID:</strong> ${currentRequestId}<br>
      <strong>Status:</strong> Completed<br>
      <strong>Output:</strong> ET calculations ready
    </div>
    <div style="margin:1rem 0; text-align:center;">
      <img id="et-map-image"
           src="${API_BASE_URL}/etmap/${currentRequestId}.png"
           alt="ET Map Result"
           style="max-width:100%; height:auto; border-radius:4px; cursor:pointer;"
           onclick="openETMapInNewTab()"
           onerror="handleETMapError(this)">
      <p style="margin-top:.5rem; font-size:.8rem; color:#666;">Click image to view full size</p>
    </div>
  `;
}

function openETMapInNewTab() {
  window.open(`${API_BASE_URL}/etmap/${currentRequestId}.png`, '_blank');
}

function handleETMapError(img) {
  img.style.display = 'none';
  img.parentElement.innerHTML = `
    <h4 style="color:#dc3545; margin-bottom:1rem;">ET Map Not Available</h4>
    <p>Try refreshing in a few moments or check the processing logs.</p>
  `;
}

// ============================== UTIL ==========================
function updateStatus(msg) {
  const el = document.getElementById('status');
  if (el) el.textContent = msg;
  console.log('Status:', msg);
}
function showError(msg) {
  const box = document.getElementById('error-container');
  if (box) box.innerHTML = `<div class="error-message"><strong>Error:</strong> ${msg}</div>`;
  console.error('Error:', msg);
}

// ========================= GLOBAL EXPORTS =====================
function goBackHome() {
  if (pollInterval) { clearInterval(pollInterval); pollInterval = null; }
  document.getElementById('form-section').classList.add('active');
  document.getElementById('status-section').classList.remove('active');
  document.getElementById('results-section').classList.remove('active');

  const submitBtn = document.getElementById('submitBtn');
  if (submitBtn) { submitBtn.disabled = true; submitBtn.textContent = 'Submit Request'; }
  const err = document.getElementById('error-container'); if (err) err.innerHTML = '';
  validateForm();
  updateStatus('Ready! Draw a polygon to get started.');
}
function cancelAndGoBack() { if (pollInterval) { clearInterval(pollInterval); pollInterval = null; } goBackHome(); }
function viewResultsOnMap() {
  if (!currentRequestId) { showError('No current request ID available'); return; }
  const url = `${API_BASE_URL}/etmap/${currentRequestId}.png`;
  const img = new Image();
  img.onload = () => { addETOverlayToMap(url); showETOverlayControls(); };
  img.onerror = () => showError('ET map image is not available yet.');
  img.src = url;
}
function createAnotherRequest() {
  if (pollInterval) { clearInterval(pollInterval); pollInterval = null; }
  currentRequestId = null;
  lastRequestData = null;
  if (drawnItems) drawnItems.clearLayers();
  polygon = null;
  if (etResultsLayer) {
    map.removeLayer(etResultsLayer);
    if (etLayerControl) etLayerControl.removeLayer(etResultsLayer);
    etResultsLayer = null;
  }
  const c = document.getElementById('et-overlay-controls'); if (c) c.remove();
  const err = document.getElementById('error-container'); if (err) err.innerHTML = '';
  const submitBtn = document.getElementById('submitBtn');
  if (submitBtn) { submitBtn.disabled = true; submitBtn.textContent = 'Submit Request'; }
  updateGeometryInfo();
  validateForm();
  showFormSection();
  updateStatus('Ready! Draw a polygon to get started.');
}

window.goBackHome = goBackHome;
window.cancelAndGoBack = cancelAndGoBack;
window.viewResultsOnMap = viewResultsOnMap;
window.createAnotherRequest = createAnotherRequest;
window.toggleETOverlay = toggleETOverlay;
window.adjustETOpacity = adjustETOpacity;
window.fitToETResults = fitToETResults;
window.removeETOverlay = removeETOverlay;
window.openETMapInNewTab = openETMapInNewTab;
window.handleETMapError = handleETMapError;
