// Map imagery layers, roof imagery and detail, and imagery sources in Settings.

let imgSources = {};
const cov = s => s.coverage || WORLD;
function imgProvider(src) {
  const url = src.url.replace("{-y}", "{reverseY}")
    .replace("{bbox}", "{westProjected},{southProjected},{eastProjected},{northProjected}");
  const c = cov(src);
  const p = new C.UrlTemplateImageryProvider({ url, minimumLevel: src.min_zoom || 0, maximumLevel: src.max_zoom, credit: new C.Credit(esc(src.attribution), true),
    rectangle: C.Rectangle.fromDegrees(Math.max(c[0], -180), Math.max(c[1], -85), Math.min(c[2], 180), Math.min(c[3], 85)) });
  p.errorEvent.addEventListener(() => {});
  return p;
}
// "auto" (Aerial): Sentinel-2, with the sharpest source for the view on top from web zoom 15
const HIRES_FROM = 14;               // Cesium's geographic terrain level 14 ~ web-map zoom 15
let layerMode = "auto", hires = null;
const sharpestHere = () => { const s = bestFor(viewBox(), "texture"); return s && s.id !== "s2cloudless" ? s.id : null; };
function updateHires() {
  const id = sharpestHere();
  if (hires && hires.id === id) return;
  if (hires) viewer.imageryLayers.remove(hires.layer);
  hires = id ? { id, layer: new C.ImageryLayer(imgProvider(imgSources[id]), { minimumTerrainLevel: HIRES_FROM, alpha: opacity() }) } : null;
  if (hires) viewer.imageryLayers.add(hires.layer);
}
function setLayer(id) {
  const layers = viewer.imageryLayers;
  layers.removeAll();
  layers.addImageryProvider(osmProvider());
  hires = null; layerMode = id;
  if (id === "auto") {
    if (imgSources.s2cloudless) layers.addImageryProvider(imgProvider(imgSources.s2cloudless)).alpha = opacity();
    updateHires();
  } else if (id !== "osm" && imgSources[id]) layers.addImageryProvider(imgProvider(imgSources[id])).alpha = opacity();
  $("#layer").value = id;
  $("#opacity").disabled = id === "osm";
}
function refreshLayerList() {
  const vb = viewBox(), cur = layerMode;
  const here = Object.values(imgSources).filter(s => boxesOverlap(cov(s), vb));
  $("#layer").innerHTML = `<option value="auto">Aerial (sharpest from zoom 15)</option><option value="osm">OpenStreetMap</option>` +
    here.map(s => `<option value="${esc(s.id)}">${esc(s.name)}${s.builtin ? "" : " (yours)"}</option>`).join("");
  if (cur === "auto") { if (viewer.imageryLayers.length < 2 && imgSources.s2cloudless) setLayer("auto"); else updateHires(); }
  else if (cur !== "osm" && !here.some(s => s.id === cur)) setLayer("auto");
  $("#layer").value = layerMode;
}
$("#layer").onchange = () => setLayer($("#layer").value);
const opacity = () => +$("#opacity").value / 100;
if (store.get("tak-buildings3d.opacity") !== null) $("#opacity").value = store.get("tak-buildings3d.opacity");
$("#opacityVal").textContent = $("#opacity").value + "%";
$("#opacity").oninput = () => {
  $("#opacityVal").textContent = $("#opacity").value + "%";
  const layers = viewer.imageryLayers;
  for (let i = 1; i < layers.length; i++) layers.get(i).alpha = opacity();
  store.set("tak-buildings3d.opacity", $("#opacity").value);
};
viewer.camera.moveEnd.addEventListener(() => { refreshLayerList(); if (!area) refreshRoofImagery(); });

// Detail runs from the sharpest the imagery has down to Single colour; the default follows the
// area's size. Mirrors imagery.py.
const TEXTURE_MAX_MPP = 2;
const DETAIL = [[0.1, "Finest"], [0.25, "Sharp"], [0.5, "Standard"], [1, "Light"], [2, "Lightest"]];
const DETAIL_FOR_KM2 = [[5, 0.25], [20, 0.5], [Infinity, 1]];
let detailPicked = false;
let limits = { photo: 50 };
api("/api/limits").then(l => { limits = l; refreshRoofImagery(); }).catch(() => {});
const roofs = () => $("#mpp").value === "colour" ? "colour" : "texture";
const nativeMpp = (s, lat) => Math.max(s.resolution_m || 0, 156543.034 * Math.cos(lat * Math.PI / 180) / 2 ** s.max_zoom);
const targetBox = () => area ? ringBox(area) : viewBox();
const areaKm2 = () => area ? ringArea(area) / 1e6 : 0;
const usable = (s, mode, lat) => mode !== "texture" || nativeMpp(s, lat) <= TEXTURE_MAX_MPP;
function inGeom(g, x, y) {
  const polys = g.type === "Polygon" ? [g.coordinates] : g.type === "MultiPolygon" ? g.coordinates : [];
  let inside = false;
  for (const ring of polys.flat())
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const [xi, yi] = ring[i], [xj, yj] = ring[j];
      if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) inside = !inside;
    }
  return inside;
}
const hasImagery = (s, x, y) => cov(s)[0] <= x && x <= cov(s)[2] && cov(s)[1] <= y && y <= cov(s)[3]
  && (!s.footprint || inGeom(s.footprint.geometry, x, y));
function bestFor(box, mode) {        // as imagery.best_source
  const cx = (box[0] + box[2]) / 2, cy = (box[1] + box[3]) / 2;
  const a = s => s.footprint ? s.footprint.area : (cov(s)[2] - cov(s)[0]) * (cov(s)[3] - cov(s)[1]);
  return Object.values(imgSources)
    .filter(s => (s.builtin || s.coverage) && hasImagery(s, cx, cy))
    .filter(s => usable(s, mode, cy))
    .sort((p, q) => q.max_zoom - p.max_zoom || a(p) - a(q))[0];
}
const bestHere = box => bestFor(box, "texture") || bestFor(box, "colour");
function chosenSource() {
  const v = $("#imagery").value;
  return v === "auto" ? bestHere(targetBox()) : imgSources[v];
}
function refreshRoofImagery(select) {
  const box = targetBox(), lat = boxLat(box);
  const cur = select || $("#imagery").value || "auto";
  const here = Object.values(imgSources).filter(s => boxesOverlap(cov(s), box));
  const best = bestHere(box);
  const label = s => `${esc(s.name)} · ~${fmtM(nativeMpp(s, lat))}`;
  $("#imagery").innerHTML =
    (best ? `<option value="auto">Auto: ${label(best)}</option>` : "") +
    here.map(s => `<option value="${esc(s.id)}">${label(s)}</option>`).join("") ||
    `<option value="">No imagery covers this area</option>`;
  $("#imagery").value = [...$("#imagery").options].some(o => o.value === cur) ? cur : ($("#imagery").options[0]?.value || "");
  showLicence();
}
function showLicence() {
  const src = chosenSource();
  $("#imgLic").innerHTML = src
    ? `Licence: ${esc(src.licence || "confirmed by you")}${src.licence_url ? ` · <a href="${esc(src.licence_url)}" target="_blank" rel="noopener">terms</a>` : ""}`
    : "";
  refreshDetail();
  if (src && area && $("#imagery").value !== "auto" && layerMode !== src.id) setLayer(src.id);
}
function refreshDetail() {
  const src = chosenSource(), cur = $("#mpp").value, km2 = areaKm2();
  const native = src ? nativeMpp(src, boxLat(targetBox())) : Infinity;
  const textures = native <= TEXTURE_MAX_MPP && km2 <= limits.photo;
  const opts = textures ? DETAIL.filter(([m]) => m >= native * 0.9) : [];
  // add the native resolution (e.g. NAIP 0.6 m) if no step is within 10% of it
  const nat = +native.toPrecision(2);
  if (textures && src.resolution_m && !opts.some(([m]) => Math.abs(m - nat) <= 0.1 * nat)) {
    opts.unshift([nat, "Native"]);
    opts.sort((a, b) => a[0] - b[0]);
  }
  $("#mpp").innerHTML = opts.map(([m, n]) => `<option value="${m}">${n} · ${m} m/px</option>`).join("") +
    `<option value="colour">Single colour · smallest</option>`;
  const target = DETAIL_FOR_KM2.find(([k]) => km2 <= k)[1];
  const auto = opts.find(([m]) => m >= target - 1e-9) || opts[opts.length - 1];
  const keep = detailPicked && [...$("#mpp").options].some(o => o.value === cur) ? cur : null;
  $("#mpp").value = keep ?? (auto ? String(auto[0]) : "colour");
  $("#roofNote").textContent = !src ? "" : native > TEXTURE_MAX_MPP ? "Only coarse imagery here, so roofs get a single colour."
    : km2 > limits.photo ? `Over ${limits.photo} km², roofs get a single colour.` : "";
}
$("#imagery").onchange = () => { detailPicked = false; showLicence(); };
$("#mpp").onchange = () => { detailPicked = true; };

const openGroups = {};
async function loadImagery(select) {
  let [ss, cache] = await Promise.all([api("/api/imagery"), api("/api/imagery-cache").catch(() => [])]);
  imgSources = Object.fromEntries(ss.map(x => [x.id, x]));
  const cached = Object.fromEntries(cache.filter(c => c.id).map(c => [c.id, c]));
  const order = ["Worldwide", "Europe", "Asia", "North America", "Central America", "South America", "Africa", "Oceania", "Antarctica", "Added by you"];
  const grp = x => x.group || "Added by you";
  ss = [...ss].sort((a, b) => order.indexOf(grp(a)) - order.indexOf(grp(b)) || a.name.localeCompare(b.name));
  const groups = order.map(g => [g, ss.filter(x => grp(x) === g)]).filter(([, xs]) => xs.length);
  const country = x => x.country || (x.builtin ? x.name.split(" – ")[0].split(",")[0] : x.name);
  const row = (x, c) => `<div class="item"><span class="t">${esc(c && x.name.startsWith(c + ", ") ? x.name.slice(c.length + 2) : x.name)}<br>
      <small>${esc(x.licence || "licence confirmed by you")} · z${x.max_zoom}${x.coverage ? "" : " · worldwide"}${
        cached[x.id] && cached[x.id].tiles ? ` · ${mb(cached[x.id].bytes)} cached <a href="#" data-clear="${esc(x.id)}"
          title="Delete this source's downloaded tiles; they're fetched again when a build needs them">clear</a>` : ""}</small></span>
      <span style="white-space:nowrap"><a href="#" data-view="${esc(x.id)}">view</a>${x.builtin ? "" :
        ` · <a href="#" data-del="${esc(x.id)}" class="del" title="Remove this source">delete</a>`}</span></div>`;
  const fold = (key, title, n, body, dflt) =>
    `<details class="grp" data-g="${esc(key)}"${(openGroups[key] ?? dflt) ? " open" : ""}><summary>${esc(title)} (${n})</summary>${body}</details>`;
  $("#sources").innerHTML = groups.map(([g, xs]) => {
    const cs = [...new Set(xs.map(country))];
    const body = cs.map(c => {
      const ys = xs.filter(x => country(x) === c);
      if (ys.length === 1) return row(ys[0]);
      return fold(`${g}/${c}`, c, ys.length, ys.map(x => row(x, c)).join(""), false);
    }).join("");
    return fold(g, g, xs.length, body, cs.length <= 3);
  }).join("");
  $("#sources").querySelectorAll("details").forEach(d => d.ontoggle = () => { openGroups[d.dataset.g] = d.open; });
  const total = cache.reduce((t, c) => t + c.bytes, 0), orphan = cache.filter(c => !c.id).reduce((t, c) => t + c.bytes, 0);
  $("#cacheInfo").innerHTML = total ? `Imagery tiles are kept so rebuilds and edits don't download them again:
      ${mb(total)}${orphan ? ` (${mb(orphan)} from removed sources)` : ""}. <a href="#" id="clearCache">Clear all</a>` : "";
  const clear = async (key, what) => {
    if (!confirm(`Delete ${what}? Tiles are downloaded again when a build needs them.`)) return;
    await del(`/api/imagery-cache${key ? "?key=" + encodeURIComponent(key) : ""}`);
    loadImagery();
  };
  $("#sources").querySelectorAll("[data-clear]").forEach(a => a.onclick = ev => {
    ev.preventDefault(); clear(a.dataset.clear, `cached tiles for ${imgSources[a.dataset.clear].name}`); });
  if ($("#clearCache")) $("#clearCache").onclick = ev => { ev.preventDefault(); clear(null, `all cached imagery (${mb(total)})`); };
  $("#sources").querySelectorAll("[data-view]").forEach(a => a.onclick = ev => {
    ev.preventDefault(); const x = imgSources[a.dataset.view];
    if (x.coverage && x.id !== "s2cloudless") flyToBox(x.coverage);
    setTimeout(() => setLayer(x.id), x.coverage ? 1300 : 0);
  });
  $("#sources").querySelectorAll("[data-del]").forEach(a => a.onclick = async ev => {
    ev.preventDefault();
    if (confirm(`Remove imagery source ${a.dataset.del}?`)) { await del(`/api/imagery/${encodeURIComponent(a.dataset.del)}`); loadImagery(); }
  });
  refreshLayerList();
  refreshRoofImagery(select);
}
$("#imgCovView").onclick = () => { $("#imgCov").value = viewBox().map(v => v.toFixed(4)).join(","); };
async function loadFolders() {
  const fs = await api("/api/geotiff-folders").catch(() => []);
  $("#imgFolder").innerHTML = `<option value="">From a tile server URL</option>` +
    fs.map(f => `<option value="${esc(f)}">GeoTIFF folder: ${esc(f)}</option>`).join("");
  $("#imgFolder").onchange();
}
$("#imgFolder").onchange = () => {
  const f = !!$("#imgFolder").value;
  document.querySelectorAll("#settings .url").forEach(r => r.hidden = f);
  $("#imgFolderHint").hidden = !f;
};
$("#imgFolder").closest("details").addEventListener("toggle", e => e.target.open && loadFolders());
$("#imgSave").onclick = async () => {
  $("#imgMsg").textContent = "";
  try {
    const c = $("#imgCov").value.trim();
    const s = await post("/api/imagery", {
      name: $("#imgName").value, url: $("#imgUrl").value, max_zoom: +$("#imgZoom").value || 19,
      attribution: $("#imgAttr").value, licence_confirmed: $("#imgOk").checked,
      coverage: c ? c.split(",").map(Number) : null, folder: $("#imgFolder").value || null });
    $("#imgMsg").innerHTML = `<span class="done">Saved</span>`;
    await loadImagery(s.id);
  } catch (e) { $("#imgMsg").innerHTML = `<span class="error">${esc(e.message)}</span>`; }
};
