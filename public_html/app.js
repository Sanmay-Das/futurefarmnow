const { useState, useEffect, useRef } = React;
//const baseURL = "http://127.0.0.1:8890/"
const baseURL = "/futurefarmnow-backend-0.3-RC1/"

const calculateLegendRanges = (globalMin, globalMax, numRanges) => {
const range = globalMax - globalMin;
const step = range / numRanges;
const legendRanges = [];

 for (let i = 0; i < numRanges; i++) {
  const minValue = globalMin + step * i;
  const maxValue = minValue + step;
  const color = valueToColor((minValue + maxValue) / 2, globalMin, globalMax); // Color based on the midpoint
  legendRanges.push({ range: `[${minValue.toFixed(2)}, ${maxValue.toFixed(2)}]`, color });
 }

 return legendRanges;
};

function valueToColor(value, globalMin, globalMax) {
 // Normalize the value between 0 and 1
 const normalized = (value - globalMin) / (globalMax - globalMin);
 const intensity = Math.round(normalized * 255);
 return `rgb(${intensity}, ${intensity}, ${intensity})`;
}

const Legend = ({ legendRanges }) => {
 console.log("legend ranges", legendRanges);
 if (legendRanges === 'undefined' || legendRanges.length == 0)
  return null;
 return (
  <div className="legend">
   <h3>Soil Value Legend</h3>
   {legendRanges.map((entry, index) => (
    <div className="entry" key={index}>
     <div className="key" style={{ backgroundColor: entry.color }}></div>
     <span>{entry.range}</span>
    </div>
   ))}
  </div>);
}

const BoxAndWhiskerPlot = ({ results }) => {
 // Check if results are not null and contain necessary data
 if (!results || typeof results.min === 'undefined' || typeof results.max === 'undefined' || typeof results.mean === 'undefined' || typeof results.median === 'undefined' || typeof results.lowerquart === 'undefined' || typeof results.upperquart === 'undefined') {
  // Optionally, return a message or null if the data is insufficient
  return <div>No data available for plot.</div>;
 }

 const svgWidth = 200; // Adjust as needed
 const svgHeight = 100; // Adjust as needed
 const { min, max, mean, median, lowerquart, upperquart } = results;

 // Calculate scale based on data range
 const padding = 20; // Padding to avoid drawing on the edge
 const range = max - min;
 const scale = (value) => ((value - min) / range) * (svgWidth - (padding * 2)) + padding;

 return (
  <svg width={svgWidth} height={svgHeight} style={{ border: '1px solid #ccc', display: 'block', margin: '0 auto' }}>
   {/* Whiskers */}
   <line x1={scale(min)} y1="50" x2={scale(lowerquart)} y2="50" stroke="black" />
   <line x1={scale(upperquart)} y1="50" x2={scale(max)} y2="50" stroke="black" />
   <line x1={scale(min)} y1="40" x2={scale(min)} y2="60" stroke="black" />
   <line x1={scale(max)} y1="40" x2={scale(max)} y2="60" stroke="black" />

   {/* Box */}
   <rect x={scale(lowerquart)} y="30" width={scale(upperquart) - scale(lowerquart)} height="40" fill="grey" />
   {/* Median Line */}
   <line x1={scale(median)} y1="30" x2={scale(median)} y2="70" stroke="blue" strokeWidth="2" />

   {/* Text */}
   <text x={scale(min)} y="75" fontSize="10" textAnchor="middle">{`Min ${min.toFixed(2)}`}</text>
   <text x={scale(max)} y="75" fontSize="10" textAnchor="middle">{`Max ${max.toFixed(2)}`}</text>
   <text x={scale(lowerquart)} y="20" fontSize="10" textAnchor="middle">{`${lowerquart.toFixed(2)}`}</text>
   <text x={scale(upperquart)} y="20" fontSize="10" textAnchor="middle">{`${upperquart.toFixed(2)}`}</text>
   <text x={scale(median)} y="85" fontSize="10" textAnchor="middle">{`Median ${median.toFixed(2)}`}</text>
  </svg>
 );
};

const calculatePolygonExtents = (polygonCoordinates) => {
 let minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity;

 polygonCoordinates[0].forEach(([lon, lat]) => { // Using the first ring which represents the outer boundary
  if (lon < minx) minx = lon;
  if (lon > maxx) maxx = lon;
  if (lat < miny) miny = lat;
  if (lat > maxy) maxy = lat;
 });

 return { minx, miny, maxx, maxy };
};

function App() {
 const [vectors, setVectors] = useState([]);
 const [selectedVectorId, setSelectedVectorId] = useState('');
 const mapRef = useRef(null); // Use useRef to hold the map instance
 const vectorLayerRef = useRef(null); // Reference to the vector layer for drawings
 const [drawnGeometry, setDrawnGeometry] = useState(null); // For storing the drawn polygon geometry
 const [minSoilDepth, setMinSoilDepth] = useState('0');
 const [maxSoilDepth, setMaxSoilDepth] = useState('60'); // Default max depth value
 const [layer, setLayer] = useState('alpha'); // Default layer
 const [queryResults, setQueryResults] = useState(null); // To store the query results
 const [legendRanges, setLegendRanges] = useState([]); // To store the query results
 const [queryType, setQueryType] = useState("POLARIS"); // To store the query results
 const soilImageLayerRef = useRef(null);
 const datasetTileLayerRef = useRef(null);
 // Define parameters for the NDVI query
 const [fromDate, setFromDate] = useState('');
 const [toDate, setToDate] = useState('');

 const handleQueryTypeChange = (event) => { setQueryType(event.target.value); };

 useEffect(() => {
  fetch(baseURL + 'vectors.json')
   .then(response => response.json())
   .then(data => setVectors(data.vectors))
   .catch(error => console.error('Error fetching vector list:', error));
 }, []);

 // Initialize the map and the drawing interaction
 useEffect(() => {
  // Define the vector source for drawings
  const vectorSource = new ol.source.Vector();

  // Define and assign the vector layer for drawings using the vector source
  vectorLayerRef.current = new ol.layer.Vector({ source: vectorSource });

  const osmLayer = new ol.layer.Tile({ source: new ol.source.OSM() });

  const vectorLayer = new ol.layer.Vector({ source: new ol.source.Vector() });

  const soilImageLayer = new ol.layer.Image({
   source: new ol.source.ImageStatic({
    url: '', // Placeholder or a transparent PNG as initial value
    imageExtent: ol.proj.get('EPSG:3857').getExtent(), // Default extent
   }),
  });

  const datasetTileLayer = new ol.layer.Tile({
   source: new ol.source.XYZ({
    url: '' // Placeholder URL; you'll set the actual source dynamically
   }),
  });

  const map = new ol.Map({
   target: 'map',
   layers: [osmLayer, vectorLayer, soilImageLayer, datasetTileLayer],
   view: new ol.View({
    center: ol.proj.fromLonLat([-119.4179, 36.7783]),
    zoom: 6,
   }),
  });

  mapRef.current = map;
  vectorLayerRef.current = vectorLayer;
  soilImageLayerRef.current = soilImageLayer;
  datasetTileLayerRef.current = datasetTileLayer;

  // Define the draw interaction with the type 'Polygon'
  const drawInteraction = new ol.interaction.Draw({
   source: vectorSource, // Use the same vector source for the draw interaction
   type: 'Polygon',
  });

  drawInteraction.on('drawstart', () => {
   vectorLayerRef.current.getSource().clear();
  });

  // Update the draw end event handler to store the drawn polygon geometry
  drawInteraction.on('drawend', (event) => {
   const vectorSource = vectorLayerRef.current.getSource();
   vectorSource.addFeature(event.feature);

   const drawnGeometry = event.feature.getGeometry().clone().transform('EPSG:3857', 'EPSG:4326').getCoordinates();
   setDrawnGeometry(drawnGeometry);
  });

  mapRef.current.addInteraction(drawInteraction);

  return () => {
   // Cleanup: remove the draw interaction when the component unmounts
   if (mapRef.current) {
    mapRef.current.removeInteraction(drawInteraction);
   }
  };
 }, []);

/*
 * Handle single query polygon for POLARIS soil statistics
 */
 const handleQuerySubmit = async (e) => {
  e.preventDefault(); // Prevent the default form submission behavior

  if (parseInt(minSoilDepth) >= parseInt(maxSoilDepth)) {
   alert("Min soil depth must be less than max soil depth.");
   return; // Prevent the form submission
  }

  // Prepare the GeoJSON object from the drawn geometry
  const geoJson = {
   type: 'Polygon',
   coordinates: drawnGeometry
  };

  // Construct the API URL
  const apiUrl = `${baseURL}soil/singlepolygon.json?soildepth=${minSoilDepth}-${maxSoilDepth}&layer=${layer}`;

  try {
   const response = await fetch(apiUrl, {
    method: 'POST', // You can change this to 'GET' if you manage to send the payload in query parameters
    headers: {
     'Content-Type': 'application/json',
    },
    body: JSON.stringify(geoJson),
   });
   const data = await response.json();
   setQueryResults(data); // Store the results in state

   // Update the legend based on these new min and max values
   const newLegendRanges = calculateLegendRanges(data.results.min, data.results.max, 5);
   setLegendRanges(newLegendRanges);
  } catch (error) {
   console.error('Error fetching soil statistics:', error);
  }

  // Calculate extents from the drawn polygon
  const { minx, miny, maxx, maxy } = calculatePolygonExtents(drawnGeometry);

  // Assuming soilDepth and layer state variables hold the current selections
  const soilImageUrl = `${baseURL}soil/image.png?soildepth=${minSoilDepth}-${maxSoilDepth}&layer=${layer}`;

  try {
   const response = await fetch(soilImageUrl, {
    method: 'POST',
    headers: {
     'Content-Type': 'application/json',
    },
    body: JSON.stringify(geoJson),
   });

   if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`);
   }

   // Handle the binary response data
   const blob = await response.blob();

   // Create an object URL for the blob
   const imageObjectUrl = URL.createObjectURL(blob);
   soilImageLayerRef.current.setSource(new ol.source.ImageStatic({
    url: imageObjectUrl,
    imageExtent: ol.proj.transformExtent([minx, miny, maxx, maxy], 'EPSG:4326', 'EPSG:3857'),
   }));
  } catch (error) {
   console.error("Error fetching soil image:", error);
  }
 };

 /*
  * Handle single query polygon for NDVI time series
  */
 const handleNDVIQuerySubmit = async (e) => {
   e.preventDefault();

   if (!fromDate || !toDate || !drawnGeometry) {
     alert('Please ensure all fields are filled and a geometry is drawn.');
     return;
   }

   const geoJson = {
     type: 'Polygon',
     coordinates: drawnGeometry,
   };

   const apiUrl = `${baseURL}ndvi/singlepolygon.json?from=${fromDate}&to=${toDate}`;

   try {
     const response = await fetch(apiUrl, {
       method: 'POST',
       headers: {
         'Content-Type': 'application/json',
       },
       body: JSON.stringify(geoJson),
     });

     const data = await response.json();
     setQueryResults(data); // Store the NDVI results
   } catch (error) {
     console.error('Error fetching NDVI data:', error);
   }
 };

 /** Handle NDVI time series for all visible polygons */
 const handleNDVIForFarmlands = async () => {
   const view = mapRef.current.getView();
   const projection = view.getProjection();
   const extent = view.calculateExtent(mapRef.current.getSize());
   const [minx, miny, maxx, maxy] = ol.proj.transformExtent(extent, projection, 'EPSG:4326');

   const apiUrl = `${baseURL}ndvi/${selectedVectorId}.json?minx=${minx}&miny=${miny}&maxx=${maxx}&maxy=${maxy}&from=${fromDate}&to=${toDate}`;

   try {
     const response = await fetch(apiUrl);
     const data = await response.json();
     setQueryResults(data); // Store the NDVI results for farmlands
   } catch (error) {
     console.error('Error fetching NDVI for farmlands:', error);
   }
 };


 // Dynamically update the map when selectedVectorId changes
 // Assuming this useEffect hook handles adding the dataset layer based on the selectedVectorId
 useEffect(() => {
  if (selectedVectorId && mapRef.current) {
   datasetTileLayerRef.current.setSource(new ol.source.XYZ({
    url: `${baseURL}vectors/${selectedVectorId}/tile-{z}-{x}-{y}.png`
   }));
  }
 }, [selectedVectorId]);

const NDVIChart = ({ results }) => {
 if (!results || results.length === 0) return <div>No NDVI data available.</div>;

 return (
   <div>
     <h3>NDVI Time Series</h3>
     <ul>
       {results.map(({ date, mean }) => (
         <li key={date}>
           {date}: {mean.toFixed(3)}
         </li>
       ))}
     </ul>
   </div>
 );
};

 const handleQueryFarmlands = async () => {
  const view = mapRef.current.getView();
  const projection = view.getProjection();
  const extent = view.calculateExtent(mapRef.current.getSize());
  const [minx, miny, maxx, maxy] = ol.proj.transformExtent(extent, projection, 'EPSG:4326');

  // Soil query URL setup
  const soilQueryUrl = `${baseURL}soil/${selectedVectorId}.json?minx=${minx}&miny=${miny}&maxx=${maxx}&maxy=${maxy}&soildepth=${minSoilDepth}-${maxSoilDepth}&layer=${layer}`;
  // Vector data URL setup
  const vectorDataUrl = `${baseURL}vectors/${selectedVectorId}.geojson?minx=${minx}&miny=${miny}&maxx=${maxx}&maxy=${maxy}`;

  try {
   // Fetch soil statistics
   const soilResponse = await fetch(soilQueryUrl);
   const soilData = await soilResponse.json();

   // Fetch vector geometries
   const response = await fetch(vectorDataUrl);
   const vectorData = await response.json();

   const vectorSource = new ol.source.Vector({
    features: (new ol.format.GeoJSON()).readFeatures(vectorData, {
     featureProjection: 'EPSG:3857' // Ensure features are in the map projection
    })
   });

   let globalMin = Infinity, globalMax = -Infinity;

   // Assuming soilData.results is an array of objects with a 'value' property
   soilData.results.forEach(result => {
    if (result.average < globalMin) globalMin = result.average;
    if (result.average > globalMax) globalMax = result.average;
   });

   // After determining globalMin and globalMax
   // Assuming you have a state variable set up to hold the legend values
   setLegendRanges(calculateLegendRanges(globalMin, globalMax, 5));

   // Now, use soilData and vectorData to create a Choropleth map
   // This part depends on how you plan to visualize the data, which might involve updating an existing map layer or creating a new one
   // Example of setting styles based on mean values
   vectorSource.getFeatures().forEach(feature => {
    // Find the corresponding soil statistic
    // Assuming `soilData.results` contains an array of objects with `objectid` and `value`
    const soilStat = soilData.results.find(stat => stat.objectid === feature.get('OBJECTID'));
    if (soilStat) {
     // Calculate color based on soilStat.value
     const value = soilStat.average
     const color = valueToColor(value, globalMin, globalMax);

     // Apply style to feature
     const style = new ol.style.Style({
      fill: new ol.style.Fill({ color: color }),
      stroke: new ol.style.Stroke({ color: '#000', width: 1 })
     });
     feature.setStyle(style);
    }
   });

   // Add styled features to a vector source and layer, then add to the map
   // This part needs to be adjusted according to your exact setup and mapping library
   vectorLayerRef.current.setSource(vectorSource);
  } catch (error) {
   console.error("Error fetching data: ", error);
  }
 };

 const handleVectorSelection = (event) => {
  setSelectedVectorId(event.target.value);
 };

 return (
  <div className="container">
   <div className="sidebar">
    <div className="logo" title="FutureFarmNow">
     <img src="future_farm_now_logo.png" alt="FutureFarmNow Logo" />
    </div>

    <label>Farmlands:
     <select onChange={handleVectorSelection}>
      <option value="">Select a dataset</option>
      {vectors.map(vector => (
       <option key={vector.id} value={vector.id}>{vector.title}</option>
      ))}
     </select>
    </label>

    {/* Form for submitting a soil query */}
    <form onSubmit={queryType === 'POLARIS' ? handleQuerySubmit : handleNDVIQuerySubmit}>
     <div className="tabs">
       <label className={queryType === 'POLARIS' ? "checked" : undefined}><input type="radio" name="queryType" value="POLARIS" checked={queryType === 'POLARIS'} onChange={handleQueryTypeChange} /> POLARIS</label>
       <label className={queryType === 'NDVI' ? "checked" : undefined}><input type="radio" name="queryType" value="NDVI" checked={queryType === 'NDVI'} onChange={handleQueryTypeChange} /> NDVI</label>
     </div>

     { queryType === 'POLARIS' && (
      <div className="polaris">
       <div className="form-group">
        <label>Soil Depth:</label>
        <select id="minSoilDepth" value={minSoilDepth} onChange={(e) => setMinSoilDepth(e.target.value)}>
         <option value="0">0 cm</option>
         <option value="5">5 cm</option>
         <option value="15">15 cm</option>
         <option value="30">30 cm</option>
         <option value="60">60 cm</option>
         <option value="100">100 cm</option>
        </select>-
        <select id="maxSoilDepth" value={maxSoilDepth} onChange={(e) => setMaxSoilDepth(e.target.value)}>
         <option value="5">5 cm</option>
         <option value="15">15 cm</option>
         <option value="30">30 cm</option>
         <option value="60">60 cm</option>
         <option value="100">100 cm</option>
         <option value="200">200 cm</option>
        </select>
       </div>
       <div className="form-group">
        <label htmlFor="layer">Soil Layer:</label>
        <select id="layer" value={layer} onChange={(e) => setLayer(e.target.value)}>
         <option value="alpha">Alpha</option>
         <option value="bd">Bulk density (g/cm3)</option>
         <option value="clay">Clay (%)</option>
         <option value="hb">Hb (Bubbling Pressure)</option>
         <option value="ksat">Ksat (Saturated Hydraulic Conductivity)</option>
         <option value="lambda">Lambda (Pore size distribution index)</option>
         <option value="n">N (Measure of the pore size distribution)</option>
         <option value="om">OM (Organic Matter)</option>
         <option value="ph">Ph (Soil pH in H2O)</option>
         <option value="sand">Sand (%)</option>
         <option value="silt">Silty (%)</option>
         <option value="theta_r">theta_r (Residual soil water content)</option>
         <option value="theta_s">theta_s (Saturated soil water content)</option>
        </select>
       </div>
      </div>
     )}

     {queryType === 'NDVI' && (
       <div className="ndvi">
         <div className="form-group">
           <label>From Date:
             <input
               type="date"
               value={fromDate}
               onChange={(e) => setFromDate(e.target.value)}
             />
           </label>
         </div>
         <div className="form-group">
           <label>To Date:
             <input
               type="date"
               value={toDate}
               onChange={(e) => setToDate(e.target.value)}
             />
           </label>
         </div>
       </div>
     )}

     <button type="submit">Run Selected Query</button>
     <button onClick={handleQueryFarmlands}>Query All Farmlands in View</button>
     <button onClick={handleNDVIForFarmlands}>Query NDVI for All Farmlands in View</button>
    </form>

    {/* Optionally display the query results */}
    {queryResults && (
     <BoxAndWhiskerPlot results={queryResults.results} />
    )}

    // Render the chart if queryType is NDVI
    {queryType === 'NDVI' && queryResults && <NDVIChart results={queryResults.results} />}

    <Legend legendRanges={legendRanges} />

   </div>
   <div id="map" className="map"></div>
  </div>
 );
}

ReactDOM.render(<App />, document.getElementById('root'));
