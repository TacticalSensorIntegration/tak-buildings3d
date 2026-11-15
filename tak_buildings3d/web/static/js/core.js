// Helpers, shared state, the Cesium map and its terrain.
// The js/ files are plain scripts loaded in order by index.html and share one global scope.

const $ = s => document.querySelector(s);
const api = async (url, opt = {}) => {
  const r = await fetch(url, { headers: { "Content-Type": "application/json" }, ...opt });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(typeof j.detail === "string" ? j.detail : r.statusText), { status: r.status });
  return j;
};
const post = (url, body) => api(url, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
const del = url => api(url, { method: "DELETE" });
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const mb = b => b ? (b >= 1e9 ? (b / 1e9).toFixed(1) + " GB" : (b / 1e6).toFixed(b < 1e7 ? 1 : 0) + " MB") : "? MB";
const fmtM = m => m < 1 ? `${+m.toPrecision(2)} m` : `${Math.round(m)} m`;
const store = {                      // localStorage, which may be unavailable (private mode, file://)
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch {} },
};

// Geometry on [lon, lat] points; boxes are [west, south, east, north] degrees.
const WORLD = [-180, -85, 180, 85];
const ringArea = ring => {           // geodesic area (m²) of a lon/lat ring, spherical excess
  const R = 6371008.8, rad = Math.PI / 180;
  let s = 0;
  for (let i = 0; i < ring.length; i++) {
    const [l1, p1] = ring[i], [l2, p2] = ring[(i + 1) % ring.length];
    s += (l2 - l1) * rad * (2 + Math.sin(p1 * rad) + Math.sin(p2 * rad));
  }
  return Math.abs(s * R * R / 2);
};
const ringBox = ring => [Math.min(...ring.map(p => p[0])), Math.min(...ring.map(p => p[1])),
                         Math.max(...ring.map(p => p[0])), Math.max(...ring.map(p => p[1]))];
const rectRing = (x0, y0, x1, y1) => [[x0, y0], [x1, y0], [x1, y1], [x0, y1]];
const midpoint = (a, b) => [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
const padBox = b => { const dx = (b[2] - b[0]) * .25, dy = (b[3] - b[1]) * .25; return [b[0] - dx, b[1] - dy, b[2] + dx, b[3] + dy]; };
const boxesOverlap = (a, b) => a[0] <= b[2] && a[2] >= b[0] && a[1] <= b[3] && a[3] >= b[1];
const boxLat = b => (b[1] + b[3]) / 2;

let area = null;                     // the drawn area: [[lon, lat], ...] (open ring)
let areaKind = "poly";               // "rect" keeps its corners square while editing
let draw = null;                     // while drawing: { mode: "rect" | "poly", pts, cursor }
let dataSource = null;               // OSM data for the area: downloaded extract id, "overpass", or null
let builds = {};                     // name → build summary, from /api/builds
let lastJobs = [];                   // latest /api/jobs
const shown = {};                    // build name → Cesium3DTileset on the map

// Cesium's built-in OSM credit is out of date, so OSM is credited here.
const C = Cesium;
const BLUE = C.Color.fromCssColorString("#1f6feb"), GREEN = C.Color.fromCssColorString("#2e7d32");
const OSM_CREDIT = '© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors';
const osmProvider = () => new C.OpenStreetMapImageryProvider({ url: "https://tile.openstreetmap.org/",
                                                               credit: new C.Credit(OSM_CREDIT, true) });
const viewer = new C.Viewer("map", {
  baseLayer: new C.ImageryLayer(osmProvider()),
  baseLayerPicker: false, geocoder: false, homeButton: false, sceneModePicker: false,
  navigationHelpButton: false, timeline: false, animation: false, infoBox: false,
  selectionIndicator: false, fullscreenButton: false,
});
viewer.cesiumWidget.screenSpaceEventHandler.removeInputAction(C.ScreenSpaceEventType.LEFT_DOUBLE_CLICK);
// phone: the folded attribution opens on tap
document.querySelector(".cesium-viewer-bottom").addEventListener("click", e => {
  if (!e.target.closest("a")) e.currentTarget.classList.toggle("open"); });
viewer.camera.setView({ destination: C.Cartesian3.fromDegrees(10, 25, 2.2e7) });
const ents = viewer.entities;
const handler = new C.ScreenSpaceEventHandler(viewer.scene.canvas);

function pickGround(pos) {
  const ray = viewer.camera.getPickRay(pos);
  return (ray && viewer.scene.globe.pick(ray, viewer.scene)) || viewer.camera.pickEllipsoid(pos, C.Ellipsoid.WGS84);
}
function pickLonLat(pos) {
  const c = pickGround(pos);
  if (!c) return null;
  const g = C.Cartographic.fromCartesian(c);
  return [C.Math.toDegrees(g.longitude), C.Math.toDegrees(g.latitude)];
}
function viewCentre() {
  const cv = viewer.scene.canvas;
  return pickGround(new C.Cartesian2(cv.clientWidth / 2, cv.clientHeight / 2));
}
function viewBox() {
  const r = viewer.camera.computeViewRectangle();
  if (!r) return WORLD.slice();
  const b = [r.west, r.south, r.east, r.north].map(C.Math.toDegrees);
  if (b[0] > b[2]) { b[0] = -180; b[2] = 180; }          // view crosses the antimeridian
  return b;
}
function flyToBox(b) {
  viewer.camera.flyTo({ destination: C.Rectangle.fromDegrees(b[0], b[1], b[2], b[3]), duration: 1.2 });
}
function geojsonOutline(geojson, color, fill = 0.05) {
  // Cesium can't outline polygons clamped to terrain, so each ring is drawn as a ground polyline
  return C.GeoJsonDataSource.load(geojson, { fill: color.withAlpha(fill), clampToGround: true }).then(ds => {
    const now = C.JulianDate.now();
    for (const e of [...ds.entities.values]) {
      if (!e.polygon) continue;
      e.polygon.outline = false;
      const h = e.polygon.hierarchy.getValue(now);
      for (const ring of [h.positions, ...(h.holes || []).map(x => x.positions)])
        ds.entities.add({ properties: e.properties, polyline: { positions: [...ring, ring[0]], width: 2, material: color, clampToGround: true } });
    }
    viewer.dataSources.add(ds);
    return ds;
  });
}

// GLO-30 from /api/terrain, the same heights the builds use. Beyond level 14 tiles are resampled
// from their level-14 parent: if terrain stops refining, Cesium stops sharpening the imagery too.
const TS = 65, TERRAIN_MIN = 9, TERRAIN_MAX = 14, FLAT = new Float32Array(TS * TS);
const GLO30_CREDIT = 'Terrain: Copernicus DEM GLO-30 © DLR e.V., © Airbus Defence and Space GmbH, provided under COPERNICUS by the EU and ESA';
const terrainCache = new Map();      // "level/x/y" -> Promise<Float32Array>, LRU
function terrainTile(level, x, y) {
  const k = `${level}/${x}/${y}`;
  let t = terrainCache.get(k);
  if (!t) {
    t = fetch(`/api/terrain/${k}`).then(r => r.ok ? r.arrayBuffer() : null)
      .then(b => b ? new Float32Array(b) : FLAT).catch(() => FLAT);
    if (terrainCache.size > 400) terrainCache.delete(terrainCache.keys().next().value);
  } else terrainCache.delete(k);
  terrainCache.set(k, t);
  return t;
}
function finerTile(level, x, y) {
  const d = level - TERRAIN_MAX, n = 2 ** d, ax = Math.floor(x / n), ay = Math.floor(y / n);
  const ox = x - ax * n, oy = y - ay * n, S = TS - 1;
  return terrainTile(TERRAIN_MAX, ax, ay).then(h => {
    if (h === FLAT) return FLAT;
    const out = new Float32Array(TS * TS);
    for (let i = 0; i < TS; i++) {
      const v = (oy + i / S) / n * S, r = Math.min(Math.floor(v), S - 1), fv = v - r;
      for (let j = 0; j < TS; j++) {
        const u = (ox + j / S) / n * S, c = Math.min(Math.floor(u), S - 1), fu = u - c, a = r * TS + c;
        out[i * TS + j] = (h[a] * (1 - fu) + h[a + 1] * fu) * (1 - fv) + (h[a + TS] * (1 - fu) + h[a + TS + 1] * fu) * fv;
      }
    }
    return out;
  });
}
const glo30 = () => new C.CustomHeightmapTerrainProvider({ width: TS, height: TS, credit: new C.Credit(GLO30_CREDIT, true),
  callback: (x, y, level) => level < TERRAIN_MIN ? FLAT : level > TERRAIN_MAX ? finerTile(level, x, y) : terrainTile(level, x, y) });
viewer.terrainProvider = glo30();
viewer.scene.globe.depthTestAgainstTerrain = true;
const pitchDeg = () => C.Math.toDegrees(viewer.camera.pitch);
