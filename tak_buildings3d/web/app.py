"""tak-buildings3d web app: download Geofabrik extracts, draw areas, build 3D Tiles.

Downloads and builds run one at a time on a background thread; the page polls /api/jobs.
"""
import json
import os
import queue
import shutil
import threading
import time
import traceback
import uuid
from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from pyproj import Geod
from shapely.geometry import box

from .. import geotiff, imagery, pipeline, regions, terrain_tiles

STATIC = Path(__file__).parent / "static"

# area limits in km²; Overpass is a shared public API, so only small areas come from it
MAX_AREA_KM2 = float(os.environ.get("TAK_BUILDINGS3D_MAX_AREA_KM2", "400"))
MAX_OVERPASS_KM2 = float(os.environ.get("TAK_BUILDINGS3D_MAX_OVERPASS_KM2", "4"))
# textured roofs pull many tiles from public imagery servers, so keep those builds modest
MAX_TEXTURE_KM2 = float(os.environ.get("TAK_BUILDINGS3D_MAX_TEXTURE_KM2", "50"))
JOB_LOG_LINES = 200          # log lines kept per job
JOBS_LISTED = 30             # most recent jobs returned by /api/jobs

app = FastAPI(title="tak-buildings3d", version="0.1.0")
pipeline.OUT.mkdir(parents=True, exist_ok=True)
app.mount("/out", StaticFiles(directory=pipeline.OUT), name="out")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.middleware("http")
async def revalidate_app_files(request, call_next):
    """Make browsers revalidate the page and scripts so an upgrade never mixes old and new files."""
    resp = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp

_geod = Geod(ellps="WGS84")


def area_km2(geom):
    """Geodesic area of a lon/lat shapely geometry."""
    return abs(_geod.geometry_area_perimeter(geom)[0]) / 1e6


def overpass_km2(geom):
    """Area Overpass is asked for: the bounding box, not just the drawn shape."""
    return area_km2(box(*geom.bounds))


JOBS: dict[str, dict] = {}
_queue: "queue.Queue[tuple[str, Callable]]" = queue.Queue()
_lock = threading.Lock()                 # guards job status changes and logs


class Cancelled(Exception):
    """Raised inside a running job once it has been cancelled."""


def _worker():
    """Run queued jobs one at a time; each is fn(log, progress) -> result."""
    while True:
        jid, fn = _queue.get()
        job = JOBS[jid]
        with _lock:
            if job["status"] == "cancelled":
                continue
            job["status"], job["started"] = "running", time.time()

        # cancellation is cooperative: the job's next log or progress call raises
        def check():
            if job.get("cancel"):
                raise Cancelled(job["cancel"])

        def log(msg):
            check()
            with _lock:
                job["log"].append(str(msg))
                del job["log"][:-JOB_LOG_LINES]

        def progress(f):
            check()
            job["progress"] = round(f, 3)

        try:
            job["result"] = fn(log, progress)
            job["status"], job["progress"] = "done", 1.0
        except Cancelled as e:
            job["status"], job["error"] = "cancelled", str(e)
        except Exception as e:
            job["status"], job["error"] = "error", f"{type(e).__name__}: {e}"
            with _lock:
                job["log"].append(traceback.format_exc(limit=3))
        job["finished"] = time.time()


threading.Thread(target=_worker, daemon=True).start()


def _cancel(job, why):
    """Cancel a queued job now, or ask a running one to stop."""
    with _lock:
        if job["status"] == "queued":
            job["status"], job["error"], job["finished"] = "cancelled", why, time.time()
        elif job["status"] == "running":
            job["cancel"] = why


def _submit(kind, label, fn, key=None):
    """Queue a job, cancelling any queued or running job with the same key."""
    jid = uuid.uuid4().hex[:12]
    if key:
        for j in list(JOBS.values()):
            if j.get("key") == key and j["status"] in ("queued", "running"):
                _cancel(j, "replaced by a newer build of the same name")
    JOBS[jid] = {"id": jid, "kind": kind, "label": label, "key": key, "status": "queued", "progress": 0.0,
                 "log": [], "result": None, "error": None, "created": time.time()}
    _queue.put((jid, fn))
    return JOBS[jid]


@app.get("/api/limits")
def limits():
    """Area limits in km²."""
    return {"area": MAX_AREA_KM2, "overpass": MAX_OVERPASS_KM2, "photo": MAX_TEXTURE_KM2}


@app.get("/api/jobs")
def jobs():
    """Most recent jobs first."""
    return sorted(JOBS.values(), key=lambda j: -j["created"])[:JOBS_LISTED]


def _job(jid):
    if jid not in JOBS:
        raise HTTPException(404, "no such job")
    return JOBS[jid]


@app.get("/api/jobs/{jid}")
def job(jid: str):
    return _job(jid)


@app.post("/api/jobs/{jid}/cancel")
def cancel_job(jid: str):
    _cancel(_job(jid), "cancelled")
    return JOBS[jid]


@app.get("/api/regions")
def list_regions(q: str = "", limit: int = 50):
    """Search Geofabrik extracts by path or id."""
    ql = q.lower()
    rs = [r for r in regions.index() if ql in r["path"].lower() or ql in r["id"]]
    return [{k: r[k] for k in ("id", "name", "path", "bbox")} for r in rs[:limit]]


@app.get("/api/regions/at")
def regions_at(lat: float, lon: float):
    """Geofabrik extracts covering a point, smallest first, with sizes."""
    return regions.at(lon, lat)


class AreaReq(BaseModel):
    geometry: dict


@app.post("/api/regions/for")
def regions_for(req: AreaReq):
    """Downloaded extract for an area if any, the extracts that would cover it, and Overpass eligibility."""
    try:
        clip = pipeline.geojson_geometry(req.geometry)
    except Exception as e:
        raise HTTPException(400, f"bad geometry: {e}")
    opts = regions.covering(clip)
    have = next((o["id"] for o in opts if o["downloaded"]), None)
    km2 = area_km2(clip)
    return {"have": have, "options": opts, "area_km2": round(km2, 3), "overpass_ok": overpass_km2(clip) <= MAX_OVERPASS_KM2}


@app.get("/api/downloads")
def list_downloads():
    """Extracts downloaded so far."""
    return regions.downloaded()


class DownloadReq(BaseModel):
    id: str


@app.post("/api/downloads")
def start_download(req: DownloadReq):
    """Queue a download (or refresh) of a Geofabrik extract."""
    try:
        r = regions.region(req.id)
    except KeyError as e:
        raise HTTPException(404, str(e))
    for j in JOBS.values():                  # already queued or running: don't fetch it twice
        if j["kind"] == "download" and j["label"] == r["path"] and j["status"] in ("queued", "running"):
            return j
    return _submit("download", r["path"], lambda log, prog: regions.download(req.id, log, prog).name)


@app.delete("/api/downloads/{rid:path}")     # ids can contain "/" (us/california)
def delete_download(rid: str):
    try:
        regions.delete(rid)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.get("/api/imagery-cache")
def imagery_cache():
    """Disk used by cached imagery tiles, per source."""
    return imagery.cache_usage(pipeline.DATA)


@app.delete("/api/imagery-cache")
def clear_imagery_cache(key: str | None = None):
    """Clear one source's tiles (?key=<source id or cache key>) or the whole imagery cache."""
    try:
        imagery.clear_cache(pipeline.DATA, key)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.get("/api/imagery")
def list_imagery(bbox: str | None = None):
    """Imagery sources, optionally only those overlapping bbox=min_lon,min_lat,max_lon,max_lat."""
    if not bbox:
        srcs = list(imagery.list_sources(pipeline.DATA).values())
    else:
        try:
            w, s, e, n = (float(v) for v in bbox.split(","))
        except ValueError:
            raise HTTPException(400, "bbox must be min_lon,min_lat,max_lon,max_lat")
        srcs = imagery.available(pipeline.DATA, box(w, s, e, n))
    return [{**v, "footprint": imagery.footprint_geojson(v)} if v.get("regions") or v.get("outline")
            else v if v.get("builtin") else {**v, **imagery.place(v)} for v in srcs]


class ImageryReq(BaseModel):
    name: str
    url: str = ""
    max_zoom: int = Field(19, ge=0, le=24)
    attribution: str
    licence_confirmed: bool = False
    coverage: list[float] | None = None   # [min_lon, min_lat, max_lon, max_lat]; None = worldwide
    folder: str | None = None             # a GeoTIFF folder instead of a URL


@app.post("/api/imagery")
def add_imagery(req: ImageryReq):
    """Save a user imagery source; its licence must be confirmed to allow derived works."""
    try:
        _forget_geotiff(req.name)
        return imagery.save_source(pipeline.DATA, req.name, req.url, req.max_zoom, req.attribution,
                                   req.licence_confirmed, req.coverage, req.folder)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/geotiff-folders")
def geotiff_folders():
    """Folders under tak-buildings3d-data/geotiff/ that hold GeoTIFFs."""
    return geotiff.folders(imagery.geotiff_root(pipeline.DATA))


_GEOTIFFS = {}     # open GeoTIFF sources, so map tiles don't reopen the files each time


def _forget_geotiff(sid_or_name):
    for k in [k for k in _GEOTIFFS if k[0] == imagery.source_id(sid_or_name)]:
        del _GEOTIFFS[k]


@app.get("/api/imagery/{sid}/tiles/{z}/{x}/{y}")
def geotiff_tile(sid: str, z: int, x: int, y: int):
    """Map tile rendered from a GeoTIFF source."""
    try:
        src = imagery.get_source(pipeline.DATA, sid)
    except KeyError:
        raise HTTPException(404, "unknown imagery source")
    if src.get("backend") != "geotiff" or not src["min_zoom"] <= z <= 24 or not 0 <= x < 2 ** z or not 0 <= y < 2 ** z:
        raise HTTPException(404, "not a GeoTIFF source, or no tile there")
    key = (sid, src.get("confirmed_at"))
    if key not in _GEOTIFFS:
        _GEOTIFFS[key] = imagery.open_source(src, pipeline.DATA / "tiles")
    data = _GEOTIFFS[key].tile_bytes(z, x, y)
    return Response(data, media_type="image/jpeg" if data[:2] == b"\xff\xd8" else "image/png",
                    headers={"Cache-Control": "max-age=86400"})


@app.delete("/api/imagery/{sid}")
def delete_imagery(sid: str):
    """Remove a user-added imagery source."""
    if sid in imagery.BUILTIN:
        raise HTTPException(400, "built-in source")
    _forget_geotiff(sid)
    imagery.delete_source(pipeline.DATA, sid)
    return {"ok": True}


class BuildReq(BaseModel):
    name: str
    geometry: dict                 # GeoJSON Polygon/MultiPolygon/Feature
    region: str | None = "auto"    # "auto", a downloaded Geofabrik id, or "overpass"
    roofs: str = "osm"             # "osm" | "colour" | "texture"
    imagery: str | None = None     # imagery source id or "auto"
    roof_mpp: float | None = None  # texture m/px; None = imagery.default_mpp
    tile_cell: float = Field(150.0, ge=20, le=2000)   # m


@app.post("/api/builds")
def start_build(req: BuildReq):
    """Validate a build request and queue it."""
    try:
        name = pipeline.safe_name(req.name)
        clip = pipeline.geojson_geometry(req.geometry)
    except Exception as e:
        raise HTTPException(400, f"bad request: {e}")
    if clip.is_empty or clip.geom_type not in ("Polygon", "MultiPolygon"):
        raise HTTPException(400, "draw a polygon or rectangle")
    if req.roofs not in ("osm", "colour", "texture"):
        raise HTTPException(400, "roofs must be osm, colour or texture")
    if req.roofs != "texture":
        req.roof_mpp = None
    src = None
    if req.roofs != "osm":
        if (req.imagery or "auto") == "auto":
            src = "auto"
        else:
            try:
                src = imagery.get_source(pipeline.DATA, req.imagery)
            except KeyError:
                raise HTTPException(400, "unknown imagery source")
            if not imagery.covers(src, clip):
                raise HTTPException(400, f"{src['name']} doesn't cover this area")
        if req.roof_mpp is not None and not 0.1 <= req.roof_mpp <= 5:
            raise HTTPException(400, "roof resolution must be 0.1-5 m/px")
    if src == "auto":
        src = imagery.best_source(pipeline.DATA, clip, req.roofs)
        if src is None:
            raise HTTPException(400, "no suitable imagery covers this area" +
                                (" (textured roofs need imagery sharper than 2 m)" if req.roofs == "texture" else ""))
    if req.roofs == "texture":
        native = imagery.native_mpp(src, clip.centroid.y)
        if native > imagery.TEXTURE_MAX_MPP:
            raise HTTPException(400, f"{src['name']} is ~{native:.0f} m/px here: too coarse for textured roofs")
        if req.roof_mpp is None:
            req.roof_mpp = imagery.default_mpp(src, clip.centroid.y)
        if req.roof_mpp < native * 0.9:
            raise HTTPException(400, f"{src['name']} is ~{native:.2g} m/px here: choose {native:.2g} m/px or coarser")
    km2 = area_km2(clip)
    region = req.region or "auto"
    if region == "auto":
        region = regions.best_for(clip)
        if region is None:
            if overpass_km2(clip) > MAX_OVERPASS_KM2:
                raise HTTPException(400, "no downloaded extract covers this area: click the map inside it "
                                         "and download one first")
            region = "overpass"
    limits = [(MAX_AREA_KM2 if region != "overpass" else MAX_OVERPASS_KM2,
               "" if region != "overpass" else " (download an extract to build larger areas)")]
    if req.roofs == "texture":
        limits.append((MAX_TEXTURE_KM2, " for textured roofs (use Single colour for larger areas)"))
    for (limit, hint), a in zip(limits, [overpass_km2(clip) if region == "overpass" else km2, km2]):
        if a > limit:
            raise HTTPException(400, f"area {a:.1f} km² is over the {limit:g} km² limit{hint}")
    pbf = None
    if region != "overpass":
        pbf = regions.local_pbf(region)
        if pbf is None:
            raise HTTPException(400, f"region {region} isn't downloaded")
    params = pipeline.Params(name=name, clip=clip, pbf=pbf, roofs=req.roofs, imagery=src,
                             roof_mpp=req.roof_mpp, tile_cell=req.tile_cell)
    label = f"{name} ({km2:.1f} km², {region}, roofs: {req.roofs}{', ' + src['id'] if src else ''})"
    return _submit("build", label, lambda log, prog: pipeline.run(params, log, prog), key=f"build:{name}")


@app.get("/api/builds")
def list_builds():
    """Finished builds, newest first, with their summary and whether newer OSM data exists."""
    out = []
    have = {d["id"]: d.get("downloaded") for d in regions.downloaded()}
    reps = []
    for rep in pipeline.OUT.glob("*/report.json"):
        try:
            reps.append((rep.stat().st_mtime, rep))
        except OSError:                              # replaced by a rebuild meanwhile
            pass
    for _, rep in sorted(reps, key=lambda r: -r[0]):
        if rep.parent.name.startswith("."):          # build in progress
            continue
        try:
            meta = json.loads(rep.read_text())
            meta["anchor"]
        except (OSError, ValueError, KeyError):
            continue
        s = meta.get("summary") or {}
        osm = meta.get("osm") or {}
        newer = bool(osm.get("downloaded") and have.get(osm.get("region"))
                     and have[osm["region"]] > osm["downloaded"])
        out.append({"name": rep.parent.name, "source": meta.get("source"), "anchor": meta["anchor"],
                    "imagery": (meta.get("imagery") or {}).get("name"), "params": meta.get("params"),
                    "osm_date": osm.get("downloaded"), "osm_newer": newer,
                    "area": meta.get("area"), "built": rep.stat().st_mtime, **s})
    return out


def _build_dir(name):
    d = pipeline.OUT / pipeline.safe_name(name)
    if not (d / "report.json").exists():
        raise HTTPException(404, "no such build")
    return d


@app.post("/api/builds/{name}/rebuild")
def rebuild(name: str):
    """Rebuild with the same area and settings, using the latest downloaded OSM data."""
    meta = json.loads((_build_dir(name) / "report.json").read_text())
    p = meta["params"]
    region = p["region"]
    if region is None or region not in ("overpass", "auto") and regions.local_pbf(region) is None:
        region = "auto"                               # extract was deleted, or a CLI build from a file
    try:
        return start_build(BuildReq(name=name, geometry=meta["area"], region=region, roofs=p["roofs"],
                                    imagery=p["imagery"] or "auto", roof_mpp=p["roof_mpp"], tile_cell=p["tile_cell"]))
    except HTTPException as e:
        if e.status_code == 400:                       # old settings no longer allowed
            raise HTTPException(400, f"Can't rebuild with its old settings: {e.detail}. "
                                     "Use Edit to choose other roofs, then Update.")
        raise


@app.get("/api/builds/{name}/zip")
def build_zip(name: str):
    """The ATAK import zip (tileset.json at its root)."""
    d = _build_dir(name)
    z = d / f"{d.name}_buildings.zip"
    return FileResponse(z, filename=z.name, media_type="application/zip")


@app.delete("/api/builds/{name}")
def delete_build(name: str):
    shutil.rmtree(_build_dir(name))
    return {"ok": True}


@app.get("/api/elevation")
def elevation(lon: float, lat: float):
    """Ground height from GLO-30, in metres above mean sea level."""
    if not (-180 <= lon <= 180 and -90 <= lat <= 90):
        raise HTTPException(400, "lon/lat out of range")
    return {"msl": round(terrain_tiles.point_msl(lon, lat), 1)}


@app.get("/api/terrain/{level}/{x}/{y}")
def terrain_tile(level: int, x: int, y: int):
    """GLO-30 heightmap tile for Cesium: 65 x 65 float32."""
    data = terrain_tiles.tile_bytes(pipeline.DATA / "terrain", level, x, y)
    if data is None:
        raise HTTPException(404, "no terrain at this level")
    return Response(data, media_type="application/octet-stream",
                    headers={"Cache-Control": "public, max-age=2592000"})      # 30 days


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")
