// Drawing the area to build, and editing it afterwards.

ents.add({
  polygon: { hierarchy: new C.CallbackProperty(() => {
      const r = currentRing(); return r && r.length > 2 ? new C.PolygonHierarchy(C.Cartesian3.fromDegreesArray(r.flat())) : undefined;
    }, false), material: BLUE.withAlpha(0.15) },
  polyline: { positions: new C.CallbackProperty(() => {
      const r = currentRing(); return r && r.length > 1 ? C.Cartesian3.fromDegreesArray([...r, r[0]].flat()) : [];
    }, false), width: 2, material: BLUE, clampToGround: true },
});
function currentRing() {
  if (!draw) return area;
  const pts = draw.cursor ? [...draw.pts, draw.cursor] : draw.pts;
  if (draw.mode === "rect") {
    if (pts.length < 2) return null;
    const [[x0, y0], [x1, y1]] = pts;
    return rectRing(x0, y0, x1, y1);
  }
  return pts;
}
function hint(t) { $("#drawhint").textContent = t; $("#drawhint").style.display = t ? "block" : "none"; }
function startDraw(mode) {
  draw = { mode, pts: [], cursor: null };
  $("#drawRect").classList.toggle("on", mode === "rect");
  $("#drawPoly").classList.toggle("on", mode === "poly");
  hint(mode === "rect" ? "Click two opposite corners. Esc cancels."
                       : "Click each corner. Double-click or click the first point to finish. Right-click undoes. Esc cancels.");
}
function endDraw(ring) {             // null cancels
  const mode = draw && draw.mode;
  draw = null;
  $("#drawRect").classList.remove("on"); $("#drawPoly").classList.remove("on");
  hint("");
  if (ring) { area = ring; areaKind = mode === "rect" ? "rect" : "poly"; updateArea(); checkData(); }
}
function finishPolygon() { draw.cursor = null; endDraw(draw.pts); }
function updateArea() {
  $("#areaInfo").textContent = area ? `${(ringArea(area) / 1e6).toFixed(2)} km² selected` : "";
  $("#build").disabled = !area || !dataSource;
  $("#zoomArea").hidden = !area;
  refreshHandles();
  refreshRoofImagery();
}
$("#drawRect").onclick = () => startDraw("rect");
$("#drawPoly").onclick = () => startDraw("poly");
$("#clearArea").onclick = () => { endDraw(null); area = null; dataSource = null; updateArea(); $("#dataInfo").textContent = ""; };
document.addEventListener("keydown", e => {
  if (e.key !== "Escape") return;
  if (!$("#modal").hidden) closeModal(); else if (draw) endDraw(null);
});

handler.setInputAction(e => {
  showCoords(e.endPosition);
  if (dragging) { const p = pickLonLat(e.endPosition); if (p) { dragTo(p); dragging.moved = true; } return; }
  if (!draw) { if (area) viewer.scene.canvas.style.cursor = handleAt(e.endPosition) ? "grab" : ""; return; }
  draw.cursor = pickLonLat(e.endPosition);
}, C.ScreenSpaceEventType.MOUSE_MOVE);
handler.setInputAction(e => {
  const p = pickLonLat(e.position);
  if (!p) return;
  if (!draw) {
    const panel = extractsPanel();
    if (panel) showExtractsAt(p, panel);
    else { const n = packAt(e.position); if (n) selectPack(n, !shown[n]); }
    return;
  }
  if (draw.mode === "rect") {
    draw.pts.push(p);
    if (draw.pts.length === 2) { draw.cursor = null; endDraw(currentRing()); }
    return;
  }
  const first = draw.pts[0];
  if (first && draw.pts.length > 2) {
    const a = C.SceneTransforms.worldToWindowCoordinates(viewer.scene, C.Cartesian3.fromDegrees(...first));
    if (a && Math.hypot(a.x - e.position.x, a.y - e.position.y) < 10) { finishPolygon(); return; }
  }
  draw.pts.push(p);
}, C.ScreenSpaceEventType.LEFT_CLICK);
// double-click finishes a polygon, otherwise zooms in (Shift: out)
const onDouble = shift => e => {
  if (draw) {
    if (draw.mode === "poly" && draw.pts.length > 2) finishPolygon();
    return;
  }
  zoomAt(e.position, shift ? 2 : 0.5);
};
handler.setInputAction(onDouble(false), C.ScreenSpaceEventType.LEFT_DOUBLE_CLICK);
handler.setInputAction(onDouble(true), C.ScreenSpaceEventType.LEFT_DOUBLE_CLICK, KM.SHIFT);

let handles = [];
let dragging = null;
function refreshHandles() {
  const n = area && !draw ? area.length : 0;
  if (handles.length === 2 * n) return;
  handles.forEach(h => ents.remove(h)); handles = [];
  for (let i = 0; i < n; i++) {
    for (const mid of [false, true]) {
      handles.push(ents.add({
        handle: { i, mid },
        position: new C.CallbackProperty(() => {
          if (!area || i >= area.length) return undefined;
          const a = area[i];
          return C.Cartesian3.fromDegrees(...(mid ? midpoint(a, area[(i + 1) % area.length]) : a));
        }, false),
        point: { heightReference: C.HeightReference.CLAMP_TO_GROUND, pixelSize: mid ? 8 : 11, color: mid ? BLUE.withAlpha(0.5) : C.Color.WHITE,
                 outlineColor: BLUE, outlineWidth: 2, disableDepthTestDistance: Number.POSITIVE_INFINITY },
      }));
    }
  }
}
const handleAt = pos => { const o = viewer.scene.pick(pos); return o && o.id && o.id.handle ? o.id.handle : null; };
handler.setInputAction(e => {
  if (draw || !area) return;
  const h = handleAt(e.position); if (!h) return;
  dragging = { ...h, moved: false };
  if (h.mid && areaKind === "poly") {
    area.splice(h.i + 1, 0, midpoint(area[h.i], area[(h.i + 1) % area.length]));
    dragging = { i: h.i + 1, mid: false, moved: false };
    refreshHandles();
  }
  ctl.enableInputs = false;
}, C.ScreenSpaceEventType.LEFT_DOWN);
function dragTo(p) {
  const { i, mid } = dragging;
  if (areaKind === "rect") {                   // corners 0..3 = (x0,y0) (x1,y0) (x1,y1) (x0,y1)
    let [[x0, y0], , [x1, y1]] = area;
    const setX = j => { if (j === 0 || j === 3) x0 = p[0]; else x1 = p[0]; };
    const setY = j => { if (j === 0 || j === 1) y0 = p[1]; else y1 = p[1]; };
    if (!mid) { setX(i); setY(i); }
    else if (i === 0 || i === 2) setY(i);        // edges 0-1 and 2-3 are horizontal
    else setX(i === 1 ? 1 : 0);
    area = rectRing(x0, y0, x1, y1);
  } else area[i] = p;
}
function endHandleDrag() {
  if (!dragging) return;
  const moved = dragging.moved; dragging = null; ctl.enableInputs = true;
  if (areaKind === "rect") area = rectRing(...ringBox(area));
  updateArea();
  if (moved) checkData();
}
handler.setInputAction(() => { endBox(); endHandleDrag(); }, C.ScreenSpaceEventType.LEFT_UP);
handler.setInputAction(e => {
  if (draw) { if (draw.mode === "poly") draw.pts.pop(); return; }
  if (!area || area.length <= 3 || areaKind === "rect") return;
  const h = handleAt(e.position);
  if (h && !h.mid) { area.splice(h.i, 1); updateArea(); checkData(); }
}, C.ScreenSpaceEventType.RIGHT_CLICK);
