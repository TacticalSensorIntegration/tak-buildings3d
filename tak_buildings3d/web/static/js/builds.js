// Starting builds, following jobs, the list of builds, and startup.

let submitting = false;
async function submitBuild() {
  const name = $("#name").value.trim();
  if (!name) { $("#buildMsg").textContent = "Give the build a name."; return; }
  $("#buildMsg").textContent = "";
  if (submitting) return;
  submitting = true; $("#build").disabled = true;
  try {
    await post("/api/builds", {
      name, geometry: areaGeometry(), region: dataSource,
      roofs: roofs(), imagery: $("#imagery").value || "auto",
      roof_mpp: roofs() === "texture" ? +$("#mpp").value : null });
    pollJobs();
  } catch (e) {
    $("#buildMsg").innerHTML = `<span class="error">${esc(e.message)}</span>`;
  } finally { submitting = false; updateArea(); }
}
$("#build").onclick = submitBuild;
const safeName = n => n.trim().replace(/[^\p{L}\p{N}_-]/gu, "_").slice(0, 64);    // as pipeline.safe_name
function nameNote() {
  const n = safeName($("#name").value), upd = !!(n && builds[n]);
  $("#build").textContent = upd ? "Update" : "Build";
  $("#build").title = upd ? `Rebuild ${n} with these settings` : "";
  $("#buildMsg").innerHTML = upd ? `Updates <b>${esc(n)}</b>. The current tileset stays until the new one finishes.` : "";
}
$("#name").addEventListener("input", nameNote);

// polled every 1.5 s while anything is queued or running
let pollTimer, wasActive = new Set();     // ids queued or running at the last poll
const isActive = j => j.status === "queued" || j.status === "running";
async function pollJobs() {
  clearTimeout(pollTimer);
  const js = await api("/api/jobs");
  lastJobs = js;
  const recent = js.filter(j => isActive(j) || (j.status === "error" && Date.now() / 1000 - j.finished < 600));
  $("#jobs").innerHTML = recent.map(j => `<div class="job">
      <b>${esc(j.kind)}</b> ${esc(j.label)}: <span class="${j.status}">${esc(j.status)}</span>
      ${j.error ? `<div class="error">${esc(j.error)}</div>` : ""}
      ${j.status === "running" ? `<div class="bar"><div style="width:${j.progress * 100}%"></div></div>` : ""}
      ${isActive(j) ? `<button class="link" data-cancel="${j.id}">${j.cancel ? "cancelling…" : "Cancel"}</button>` : ""}
      ${j.status !== "done" ? `<pre>${esc(j.log.slice(-8).join("\n"))}</pre>` : ""}
    </div>`).join("");
  $("#jobs").querySelectorAll("[data-cancel]").forEach(b => b.onclick = async () => {
    b.disabled = true; await post(`/api/jobs/${b.dataset.cancel}/cancel`); pollJobs(); });
  const active = js.some(isActive);
  if (active || wasActive.size) renderPacks();
  if (wasActive.size && !active) {
    loadDownloads(); await loadBuilds();
    if (area && dataSource !== "overpass") checkData();
    const last = js.find(j => j.kind === "build" && j.status === "done" && wasActive.has(j.id));
    if (last && last.result && last.result.name) {
      hideTileset(last.result.name);
      selectPack(last.result.name, false);
      showBuild(last.result.name, true);
    }
  }
  wasActive = new Set(js.filter(isActive).map(j => j.id));
  if (active) pollTimer = setTimeout(pollJobs, 1500);
}

let buildsDs = null, selDs = null, selPack = null, selToken = 0;
// setTab(true/false) is shorthand for Builds / New build
function setTab(tab) {
  tab = tab === true ? "packs" : tab === false ? "new" : tab;
  for (const [t, id] of [["new", "#tabNew"], ["packs", "#tabPacks"], ["settings", "#tabSettings"]])
    $(id).classList.toggle("on", t === tab);
  $("#flow").hidden = tab !== "new"; $("#packs").hidden = tab !== "packs";
  openSettings(tab === "settings");
}
$("#tabNew").onclick = () => setTab("new");
$("#tabPacks").onclick = () => setTab("packs");
$("#tabSettings").onclick = () => setTab("settings");
$("#packFilter").addEventListener("input", renderPacks);

const geomBox = g => ringBox((g.type === "MultiPolygon" ? g.coordinates.map(p => p[0]) : [g.coordinates[0]]).flat());
function selectPack(name, fly = true) {
  selPack = name;
  if (name) setTab(true);
  renderPacks(); outlineSelected();
  const b = builds[name];
  if (b && fly && b.area) flyToBox(padBox(geomBox(b.area)));
  if (b) document.querySelector(`.bcard[data-name="${CSS.escape(name)}"]`)?.scrollIntoView({ block: "nearest" });
}
function outlineSelected() {
  const token = ++selToken, b = builds[selPack];
  if (selDs) { viewer.dataSources.remove(selDs, true); selDs = null; }
  if (b && b.area) geojsonOutline({ type: "Feature", geometry: b.area, properties: { name: b.name } }, BLUE, 0.1)
    .then(ds => { if (token === selToken) selDs = ds; else viewer.dataSources.remove(ds, true); });
}
// a labelled pin per build for when it's too small to see; hidden once the outline is clickable
function addPackPins(ds, bs) {
  const spots = {};                    // builds at (nearly) the same centre share a pin
  for (const b of bs.filter(b => b.area)) {
    const [w, s, e, n] = geomBox(b.area), lon = (w + e) / 2, lat = (s + n) / 2;
    (spots[`${lon.toFixed(2)},${lat.toFixed(2)}`] ||= { lon, lat, names: [] }).names.push(b.name);
  }
  const near = 2e6;
  for (const { lon, lat, names } of Object.values(spots))
    ds.entities.add({ position: C.Cartesian3.fromDegrees(lon, lat), properties: { name: names[0] },
      point: { pixelSize: 9, color: GREEN, outlineColor: C.Color.WHITE, outlineWidth: 2,
               heightReference: C.HeightReference.CLAMP_TO_GROUND, disableDepthTestDistance: near,
               distanceDisplayCondition: new C.DistanceDisplayCondition(25000, Number.POSITIVE_INFINITY) },
      label: { text: names.join(", "), font: "600 12px system-ui, sans-serif", fillColor: C.Color.WHITE, outlineColor: C.Color.BLACK,
               outlineWidth: 3, style: C.LabelStyle.FILL_AND_OUTLINE, pixelOffset: new C.Cartesian2(0, -16),
               heightReference: C.HeightReference.CLAMP_TO_GROUND, disableDepthTestDistance: near,
               distanceDisplayCondition: new C.DistanceDisplayCondition(25000, 3000000) } });
}
function packAt(pos) {
  for (const o of viewer.scene.drillPick(pos, 5)) {
    if (o instanceof C.Cesium3DTileFeature) {
      const n = Object.keys(shown).find(k => shown[k] === o.tileset);
      if (n) return n;
    }
    const n = o.id?.properties?.name?.getValue?.();
    if (n && builds[n]) return n;
  }
  return null;
}

function hideTileset(name) {
  if (shown[name]) { viewer.scene.primitives.remove(shown[name]); delete shown[name]; }
}
async function showBuild(name, on) {
  if (!on) { hideTileset(name); renderPacks(); return; }
  if (shown[name]) { viewer.flyTo(shown[name]); return; }
  const base = `/out/${encodeURIComponent(name)}/`;
  try {
    const rep = await (await fetch(base + "report.json", { cache: "no-store" })).json();
    const ts = await C.Cesium3DTileset.fromUrl(base + `${encodeURIComponent(name)}_buildings/tileset.json`);
    viewer.scene.primitives.add(ts);
    shown[name] = ts;
    if (rep.imagery && imgSources[rep.imagery.id]) setLayer(rep.imagery.id);
    viewer.flyTo(ts, { offset: new C.HeadingPitchRange(0, C.Math.toRadians(-35), 0) });
  } catch (e) { alert(`Couldn't show ${name}: ${e.message || e}`); }
  renderPacks();
}
const ROOFS_TEXT = { osm: "basic roofs", colour: "single-colour roofs", texture: "textured roofs" };
const day = t => t ? new Date(t).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "";
function buildCard(b) {
  const n = encodeURIComponent(b.name), e = esc(b.name), sel = b.name === selPack;
  const roofsTxt = ROOFS_TEXT[b.roofs || "osm"] || b.roofs;
  const mpp = b.roofs === "texture" && b.params?.roof_mpp ? ` ${b.params.roof_mpp} m/px` : "";
  const job = (lastJobs || []).find(j => j.key === `build:${b.name}` && isActive(j));
  return `<div class="bcard${sel ? " sel" : ""}" data-name="${e}">
    <div class="top"><b>${e}</b><span class="sub">${esc(day(b.built * 1000))}</span></div>
    <div class="sub">${b.buildings ?? "?"} buildings · ${b.zip_bytes ? mb(b.zip_bytes) : ""} · ${esc(roofsTxt)}${mpp}${b.osm_newer
      ? ` · <span class="newer">newer OSM data</span>` : ""}</div>
    ${job ? `<div class="sub">${job.status === "running" ? "Rebuilding…" : "Rebuild queued"}</div>
      ${job.status === "running" ? `<div class="bar"><div style="width:${job.progress * 100}%"></div></div>` : ""}` : ""}
    ${sel ? `<div class="sub">OSM data ${b.osm_date ? esc(day(b.osm_date)) : "date unknown"}${b.imagery ? " · imagery " + esc(b.imagery) : ""}</div>
    <div class="acts">
      <button data-act="show" class="${shown[b.name] ? "on" : ""}" title="Show the tileset on the map">${shown[b.name] ? "Hide 3D" : "View 3D"}</button>
      <a class="dl" href="/api/builds/${n}/zip" title="tileset.json + .b3dm, for ATAK / TAK-X import">⬇ Zip for ATAK</a>
      <button data-act="copy" data-url="${esc(location.origin)}/out/${n}/${n}_buildings/tileset.json" title="For CesiumJS or Cesium for Unreal">Copy tileset URL</button>
    </div>
    <div class="acts">
      <button data-act="rebuild" class="${b.osm_newer ? "hot" : ""}" ${job ? "disabled" : ""} title="Rebuild with the same settings and the latest downloaded OSM data">↻ Rebuild</button>
      <button data-act="edit" title="Load the area and settings to change them (e.g. roof detail), then build again">✎ Edit</button>
      <button class="del" data-act="del" title="Delete this build">Delete</button>
    </div>` : ""}</div>`;
}
function renderPacks() {
  const all = Object.values(builds), f = $("#packFilter").value.trim().toLowerCase();
  const list = f ? all.filter(b => b.name.toLowerCase().includes(f)) : all;
  $("#packCount").textContent = all.length;
  $("#packFilter").hidden = all.length < 6;
  $("#builds").innerHTML = list.map(buildCard).join("");
  $("#packs .none")?.remove();
  if (!list.length) $("#builds").insertAdjacentHTML("afterend",
    `<div class="sub none" style="margin-top:8px">${all.length ? "No build matches." : "Nothing built yet."}</div>`);
}
$("#builds").onclick = async e => {
  const card = e.target.closest(".bcard");
  if (!card || e.target.closest("a")) return;
  const n = card.dataset.name, btn = e.target.closest("button[data-act]");
  if (!btn) { if (n !== selPack) selectPack(n); return; }
  switch (btn.dataset.act) {
    case "show": showBuild(n, !shown[n]); break;
    case "copy":
      try { await navigator.clipboard.writeText(btn.dataset.url); btn.textContent = "Copied"; }
      catch { prompt("Tileset URL", btn.dataset.url); }
      break;
    case "rebuild":
      btn.disabled = true; btn.textContent = "Queued";
      try { await post(`/api/builds/${encodeURIComponent(n)}/rebuild`); pollJobs(); }
      catch (err) { btn.textContent = "↻ Rebuild"; btn.disabled = false; alert(err.message); }
      break;
    case "edit": editBuild(builds[n]); break;
    case "del":
      if (confirm(`Delete the ${n} build? This removes its files and zip.`)) {
        hideTileset(n);
        await del(`/api/builds/${encodeURIComponent(n)}`); loadBuilds();
      }
  }
};
async function loadBuilds() {
  const bs = await api("/api/builds");
  if (buildsDs) viewer.dataSources.remove(buildsDs, true);
  buildsDs = null;
  builds = Object.fromEntries(bs.map(b => [b.name, b]));
  if (selPack && !builds[selPack]) selPack = null;
  const areas = bs.filter(b => b.area).map(b => ({ type: "Feature", geometry: b.area, properties: { name: b.name } }));
  if (areas.length) geojsonOutline({ type: "FeatureCollection", features: areas }, GREEN, 0.04)
    .then(ds => { buildsDs = ds; addPackPins(ds, bs); outlineSelected(); });
  else outlineSelected();
  nameNote();
  renderPacks();
}
function editBuild(b) {
  if (!b || !b.area) return;
  selectPack(null); setTab(false);
  const poly = b.area.type === "MultiPolygon" ? b.area.coordinates[0] : b.area.coordinates;
  endDraw(null);
  area = poly[0].slice(0, -1); areaKind = "poly";
  $("#name").value = b.name;
  const p = b.params || {};
  updateArea();
  if (p.imagery && [...$("#imagery").options].some(o => o.value === p.imagery)) { $("#imagery").value = p.imagery; showLicence(); }
  const d = p.roofs === "texture" ? String(p.roof_mpp) : "colour";
  if ([...$("#mpp").options].some(o => o.value === d)) { $("#mpp").value = d; detailPicked = true; }
  checkData();
  flyToBox(padBox(ringBox(area)));
  $("#side").scrollTop = 0;
  nameNote();
}

readHash();
updateScale();
(async () => {
  await loadImagery();
  loadDownloads(); await loadBuilds(); pollJobs();
  const b = new URLSearchParams(location.search).get("build");
  if (b) showBuild(b, true);
})();
