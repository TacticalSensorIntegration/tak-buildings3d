// Web-map style navigation, the status bar and place search.

const ctl = viewer.scene.screenSpaceCameraController;
const ET = C.CameraEventType, KM = C.KeyboardEventModifier;
// tilt/rotate: right-drag or Ctrl + drag; Shift + drag is box zoom
ctl.zoomEventTypes = [ET.WHEEL, { eventType: ET.WHEEL, modifier: KM.CTRL }, ET.PINCH];
ctl.tiltEventTypes = [ET.RIGHT_DRAG, ET.MIDDLE_DRAG, ET.PINCH, { eventType: ET.LEFT_DRAG, modifier: KM.CTRL }];
ctl.minimumZoomDistance = 40;
ctl.maximumZoomDistance = 3.0e7;
ctl.inertiaSpin = ctl.inertiaTranslate = 0.6;
// a touchpad pinch zooms the map, not the page
viewer.scene.canvas.addEventListener("wheel", e => { if (e.ctrlKey) e.preventDefault(); }, { passive: false });
viewer.scene.canvas.addEventListener("contextmenu", e => e.preventDefault());

function topDown() {
  const c = viewer.camera.positionCartographic;
  viewer.camera.flyTo({ destination: C.Cartesian3.fromRadians(c.longitude, c.latitude, c.height),
                        orientation: { heading: 0, pitch: -C.Math.PI_OVER_TWO, roll: 0 }, duration: 0.6 });
}
function orbit(heading, pitch, duration = 0.6) {
  const centre = viewCentre();
  if (!centre) { topDown(); return; }
  const range = C.Cartesian3.distance(viewer.camera.positionWC, centre);
  viewer.camera.flyToBoundingSphere(new C.BoundingSphere(centre, 0), {
    offset: new C.HeadingPitchRange(heading ?? viewer.camera.heading, pitch ?? viewer.camera.pitch, range), duration });
}
function zoom(factor) {
  const centre = viewCentre();
  const dist = centre ? C.Cartesian3.distance(viewer.camera.positionWC, centre) : viewer.camera.positionCartographic.height;
  const next = C.Math.clamp(dist * factor, ctl.minimumZoomDistance, ctl.maximumZoomDistance);
  viewer.camera.moveForward(dist - next);
}
function zoomAt(pos, factor) {
  const target = pickGround(pos);
  if (!target) return;
  const centre = viewCentre() || target;
  const range = C.Cartesian3.distance(viewer.camera.positionWC, centre);
  const next = C.Math.clamp(range * factor, ctl.minimumZoomDistance, ctl.maximumZoomDistance);
  viewer.camera.flyToBoundingSphere(new C.BoundingSphere(target, 0), {
    offset: new C.HeadingPitchRange(viewer.camera.heading, viewer.camera.pitch, next), duration: 0.45 });
}
function pan(dx, dy) {
  const h = viewer.camera.positionCartographic.height * 0.25;
  viewer.camera.moveRight(dx * h); viewer.camera.moveUp(dy * h);
}
document.addEventListener("keydown", e => {
  if (/INPUT|SELECT|TEXTAREA/.test(document.activeElement?.tagName) || e.ctrlKey || e.metaKey || e.altKey) return;
  const k = { ArrowLeft: () => pan(-1, 0), ArrowRight: () => pan(1, 0), ArrowUp: () => pan(0, 1), ArrowDown: () => pan(0, -1),
              "+": () => zoom(0.5), "=": () => zoom(0.5), "-": () => zoom(2), "_": () => zoom(2) }[e.key];
  if (k) { k(); e.preventDefault(); }
});

$("#zoomIn").onclick = () => zoom(0.5);
$("#zoomOut").onclick = () => zoom(2);
$("#compass").onclick = () => orbit(0, undefined);
let tilting = false;
$("#tilt").oninput = () => { tilting = true; $("#tiltVal").textContent = `${$("#tilt").value}°`;
  orbit(undefined, C.Math.toRadians(+$("#tilt").value - 90), 0); };
$("#tilt").onchange = () => { tilting = false; };
$("#zoomArea").onclick = () => { if (area) flyToBox(padBox(ringBox(area))); };
viewer.camera.percentageChanged = 0.01;
viewer.camera.changed.addEventListener(() => {
  $("#needle").style.transform = `rotate(${-C.Math.toDegrees(viewer.camera.heading)}deg)`;
  if (tilting) return;
  const t = Math.round(C.Math.clamp(pitchDeg() + 90, 0, 80));
  $("#tilt").value = t; $("#tiltVal").textContent = `${t}°`;
});

let boxZoom = null;
const boxEnt = ents.add({ show: false, rectangle: {
  coordinates: new C.CallbackProperty(() => boxZoom && boxZoom.b ? C.Rectangle.fromDegrees(...boxZoom.b) : undefined, false),
  material: BLUE.withAlpha(0.25) } });
handler.setInputAction(e => {
  if (draw) return;
  const p = pickLonLat(e.position); if (p) { boxZoom = { a: p, b: null }; ctl.enableInputs = false; }
}, C.ScreenSpaceEventType.LEFT_DOWN, KM.SHIFT);
handler.setInputAction(e => {
  showCoords(e.endPosition);
  if (!boxZoom) return;
  const p = pickLonLat(e.endPosition); if (!p) return;
  const [a, b] = [boxZoom.a, p];
  boxZoom.b = [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[0], b[0]), Math.max(a[1], b[1])];
  boxEnt.show = true;
}, C.ScreenSpaceEventType.MOUSE_MOVE, KM.SHIFT);
function endBox() {
  if (!boxZoom) return;
  const b = boxZoom.b; boxZoom = null; boxEnt.show = false; ctl.enableInputs = true;
  if (b && b[2] - b[0] > 1e-6 && b[3] - b[1] > 1e-6) flyToBox(b);
}
handler.setInputAction(endBox, C.ScreenSpaceEventType.LEFT_UP, KM.SHIFT);

function showCoords(pos) {
  const p = pickLonLat(pos);
  if (!p) { $("#coords").textContent = ""; return; }
  let m = "";
  try { if (window.mgrs && p[1] > -80 && p[1] < 84) m = " · " + mgrs.forward(p, 5).replace(/^(\d+[A-Z])([A-Z]{2})(\d{5})(\d{5})$/, "$1 $2 $3 $4"); } catch {}
  $("#coords").textContent = `${p[1].toFixed(5)}, ${p[0].toFixed(5)}${m}`;
  elevationAt(p);
}

// ground height (MSL) under the cursor: at most every 150 ms, cached per ~10 m
let altUnit = store.get("tak-buildings3d.altunit") || "m";
const altCache = new Map();
let altTimer = null, altLast = null;
function showAlt(msl) {
  $("#alt").textContent = msl == null ? "" :
    `Alt ${altUnit === "ft" ? Math.round(msl / 0.3048).toLocaleString() + " ft" : Math.round(msl).toLocaleString() + " m"} MSL`;
  $("#alt").dataset.msl = msl ?? "";
}
function elevationAt([lon, lat]) {
  const key = `${lon.toFixed(4)},${lat.toFixed(4)}`;
  if (altCache.has(key)) { showAlt(altCache.get(key)); return; }
  altLast = key;
  clearTimeout(altTimer);
  altTimer = setTimeout(async () => {
    try {
      const r = await api(`/api/elevation?lon=${lon}&lat=${lat}`);
      if (altCache.size > 2000) altCache.clear();
      altCache.set(key, r.msl);
      if (altLast === key) showAlt(r.msl);
    } catch {}
  }, 150);
}
$("#alt").onclick = () => {
  altUnit = altUnit === "m" ? "ft" : "m";
  store.set("tak-buildings3d.altunit", altUnit);
  const v = $("#alt").dataset.msl; showAlt(v ? +v : null);
};

function updateScale() {
  const cv = viewer.scene.canvas, y = cv.clientHeight / 2, x = cv.clientWidth / 2;
  const a = pickGround(new C.Cartesian2(x - 50, y)), b = pickGround(new C.Cartesian2(x + 50, y));
  if (!a || !b) { $("#scale").style.visibility = "hidden"; return; }
  const mPerPx = C.Cartesian3.distance(a, b) / 100;
  const max = mPerPx * 120, pow = 10 ** Math.floor(Math.log10(max));
  const nice = [5, 2, 1].map(k => k * pow).find(v => v <= max);
  $("#scalebar").style.width = `${nice / mPerPx}px`;
  $("#scaletxt").textContent = nice >= 1000 ? `${nice / 1000} km` : `${nice} m`;
  $("#scale").style.visibility = "visible";
}

// view in the URL: #@lat,lon,height m,heading h,tilt t
function writeHash() {
  const c = viewer.camera.positionCartographic, D = C.Math.toDegrees;
  const h = `#@${D(c.latitude).toFixed(5)},${D(c.longitude).toFixed(5)},${Math.round(c.height)}m,` +
            `${Math.round(D(viewer.camera.heading)) % 360}h,${Math.round(D(viewer.camera.pitch))}t`;
  try { history.replaceState(null, "", location.pathname + location.search + h); } catch {}
}
function readHash() {
  const m = location.hash.match(/^#@(-?[\d.]+),(-?[\d.]+),([\d.]+)m(?:,(-?[\d.]+)h)?(?:,(-?[\d.]+)t)?/);
  if (!m) return false;
  viewer.camera.setView({ destination: C.Cartesian3.fromDegrees(+m[2], +m[1], +m[3]),
    orientation: { heading: C.Math.toRadians(+(m[4] || 0)), pitch: C.Math.toRadians(+(m[5] || -90)), roll: 0 } });
  return true;
}
viewer.camera.moveEnd.addEventListener(() => { updateScale(); writeHash(); });

// Nominatim on submit only (no autocomplete), per its usage policy. A form submit, because phone
// keyboards' Go key doesn't always send an Enter keydown.
$("#q").addEventListener("keydown", e => { if (e.key === "Escape") $("#results").hidden = true; });
$("#search").addEventListener("submit", async e => {
  e.preventDefault();
  if (!$("#q").value.trim()) return;
  $("#q").blur();
  $("#results").hidden = false; $("#results").innerHTML = `<div class="sub">Searching…</div>`;
  try {
    const r = await fetch(`https://nominatim.openstreetmap.org/search?format=jsonv2&limit=6&q=${encodeURIComponent($("#q").value.trim())}`);
    const rs = await r.json();
    $("#results").innerHTML = rs.length ? rs.map((x, i) => `<div data-i="${i}">${esc(x.name || x.display_name.split(",")[0])}
        <small>${esc(x.display_name)}</small></div>`).join("") + `<div class="sub" style="cursor:default">Search © OpenStreetMap / Nominatim</div>`
      : `<div class="sub">No places found</div>`;
    $("#results").querySelectorAll("[data-i]").forEach(d => d.onclick = () => {
      const x = rs[+d.dataset.i], bb = x.boundingbox.map(Number);
      flyToBox([bb[2], bb[0], bb[3], bb[1]]); $("#results").hidden = true;
    });
  } catch (err) { $("#results").innerHTML = `<div class="error">${esc(err.message)}</div>`; }
});
