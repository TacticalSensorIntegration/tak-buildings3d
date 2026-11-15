// OSM extracts: finding, downloading and managing them, and checking the drawn area has data.

const queueDownload = id => post("/api/downloads", { id });
let outlineDs = null, clickEnt = null;
function showOutline(geojson) {
  if (outlineDs) { viewer.dataSources.remove(outlineDs, true); outlineDs = null; }
  if (geojson) geojsonOutline(geojson, BLUE).then(ds => outlineDs = ds);
}
// while Settings is open, clicking the map lists the extracts there
const extractsPanel = () => !$("#settings").hidden ? $("#here") : null;
async function showExtractsAt([lon, lat], panel) {
  if (clickEnt) ents.remove(clickEnt);
  clickEnt = ents.add({ position: C.Cartesian3.fromDegrees(lon, lat),
    point: { heightReference: C.HeightReference.CLAMP_TO_GROUND, disableDepthTestDistance: Number.POSITIVE_INFINITY,
             pixelSize: 8, color: BLUE, outlineColor: C.Color.WHITE, outlineWidth: 2 } });
  panel.innerHTML = `<div class="sub">Finding OSM extracts at ${lat.toFixed(3)}, ${lon.toFixed(3)}…</div>`;
  try {
    const rs = await api(`/api/regions/at?lat=${lat}&lon=${lon}`);
    if (!rs.length) { panel.innerHTML = `<div class="sub">No Geofabrik extract covers this point.</div>`; return; }
    panel.innerHTML = `<div class="sub">Extracts here, smallest first</div>
      <div class="list">${rs.map((r, i) => `<div class="item" data-i="${i}"><span class="t">${esc(r.path)}<br><small>${mb(r.bytes)}</small></span>
        ${r.downloaded ? `<span class="done">✓ have</span>` : `<button data-id="${esc(r.id)}">Download</button>`}</div>`).join("")}</div>`;
    panel.querySelectorAll(".item").forEach(it => it.onmouseenter = () => showOutline(rs[+it.dataset.i].outline));
    panel.querySelector(".list").onmouseleave = () => showOutline(null);
    panel.querySelectorAll("button[data-id]").forEach(b => b.onclick = async () => {
      b.disabled = true; b.textContent = "Queued";
      try { await queueDownload(b.dataset.id); pollJobs(); }
      catch (err) { b.textContent = "Error"; alert(err.message); }
    });
  } catch (err) { panel.innerHTML = `<div class="error">${esc(err.message)}</div>`; }
}

let downloads = [];
async function loadDownloads() {
  downloads = await api("/api/downloads");
  $("#downloads").innerHTML = downloads.map(d => `<div class="item"><span class="t" title="${esc(d.path)}">${esc(d.path.split(" / ").pop())}<br>
      <small>${mb(d.bytes)} · ${esc((d.downloaded || "").slice(0, 10))}</small></span>
      <span style="white-space:nowrap"><a href="#" data-zoom="${esc(d.id)}">zoom</a> ·
        <a href="#" data-update="${esc(d.id)}" title="Download the latest from Geofabrik (updated daily)">update</a> ·
        <a href="#" data-del="${esc(d.id)}" class="del" title="Delete this extract">delete</a></span></div>`).join("")
    || `<div class="item sub">Nothing downloaded yet</div>`;
  $("#downloads").querySelectorAll("[data-update]").forEach(a => a.onclick = async ev => {
    ev.preventDefault(); a.textContent = "queued";
    try { await queueDownload(a.dataset.update); pollJobs(); }
    catch (err) { a.textContent = "update"; alert(err.message); }
  });
  $("#downloads").querySelectorAll("[data-zoom]").forEach(a => a.onclick = ev => {
    ev.preventDefault();
    const b = downloads.find(d => d.id === a.dataset.zoom).bbox;
    if (b) flyToBox(b);
  });
  $("#downloads").querySelectorAll("[data-del]").forEach(a => a.onclick = async ev => {
    ev.preventDefault();
    if (confirm(`Delete the ${a.dataset.del} extract?`)) { await del(`/api/downloads/${encodeURIComponent(a.dataset.del)}`); loadDownloads(); }
  });
}

// no downloaded extract covers the drawn area: offer the Geofabrik ones that do, or Overpass
const areaGeometry = () => ({ type: "Polygon", coordinates: [[...area, area[0]]] });
const downloadingNote = name => { $("#dataInfo").textContent = `Downloading ${name}… you can build when it finishes.`; };
async function checkData() {
  dataSource = null; updateArea();
  $("#dataInfo").textContent = "Checking OSM data…";
  let r;
  try { r = await post("/api/regions/for", { geometry: areaGeometry() }); }
  catch (e) { $("#dataInfo").innerHTML = `<span class="error">${esc(e.message)}</span>`; return; }
  if (r.have) {
    dataSource = r.have;
    $("#dataInfo").innerHTML = `OSM data: <span class="done">✓ ${esc(r.options.find(o => o.id === r.have).name)}</span>`;
    updateArea(); return;
  }
  const pending = pendingDownload(r.options);
  if (pending) { downloadingNote(pending); return; }
  $("#dataInfo").innerHTML = `No OSM data here yet. <a href="#" id="getData">Get it</a>`;
  $("#getData").onclick = ev => { ev.preventDefault(); offerData(r); };
  offerData(r);
}
function pendingDownload(opts) {
  const busy = lastJobs.filter(j => j.kind === "download" && (j.status === "queued" || j.status === "running"));
  const o = opts.find(o => busy.some(j => j.label === o.path));
  return o ? o.name : null;
}
function offerData(r) {
  $("#modalTitle").textContent = "No OSM data for this area yet";
  $("#modalBody").innerHTML = (r.options.length ? `
    <div class="list">${r.options.map((o, i) => `<div class="item" data-i="${i}"><span class="t">${esc(o.path)}<br>
      <small>${mb(o.bytes)}</small></span><button class="${i ? "" : "primary"}" data-id="${esc(o.id)}">Download</button></div>`).join("")}</div>`
    : `<div class="sub">No single extract covers this area (does it cross a border?)</div>`) +
    (r.overpass_ok ? `<div class="sub" style="margin-top:10px">Or <a href="#" id="useOverpass">fetch just this area</a>
      (${r.area_km2.toFixed(2)} km², Overpass API)</div>` : "");
  $("#modal").hidden = false;
  $("#modalBody").querySelectorAll(".item").forEach(it => it.onmouseenter = () => showOutline(r.options[+it.dataset.i].outline));
  $("#modalBody").querySelectorAll("button[data-id]").forEach(b => b.onclick = async () => {
    b.disabled = true; b.textContent = "Queued";
    try {
      await queueDownload(b.dataset.id);
      closeModal();
      downloadingNote(r.options.find(o => o.id === b.dataset.id).name);
      pollJobs();
    } catch (err) { b.textContent = "Error"; alert(err.message); }
  });
  const ov = $("#useOverpass");
  if (ov) ov.onclick = ev => {
    ev.preventDefault(); closeModal();
    dataSource = "overpass";
    $("#dataInfo").innerHTML = `OSM data: <span class="done">✓ Overpass API (this area only)</span>`;
    updateArea();
  };
}
function closeModal() { $("#modal").hidden = true; showOutline(null); }
$("#modalClose").onclick = closeModal;
$("#modal").onclick = e => { if (e.target.id === "modal") closeModal(); };

function openSettings(on) {
  $("#settings").hidden = !on; $("#main").hidden = on;
  if (!on) { $("#here").innerHTML = ""; showOutline(null); if (clickEnt) { ents.remove(clickEnt); clickEnt = null; } }
}
