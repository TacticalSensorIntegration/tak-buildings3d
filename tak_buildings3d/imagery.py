"""XYZ (Web Mercator) imagery sources, tile fetching and per-roof crops.

Only sources whose licence allows derived 3D models are used: built-ins, or ones the user confirmed.
"""
import hashlib
import io
import json
import math
import re
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import requests
from PIL import Image, ImageDraw
from shapely.geometry import Point, box, mapping, shape
from shapely.ops import unary_union
from shapely.prepared import prep

from . import USER_AGENT

UA = USER_AGENT
TILE = 256                         # px per Web Mercator tile
MERC_R = 20037508.342789244        # half the Web Mercator world width (m): pi * 6378137
Z0_MPP = 2 * MERC_R / TILE         # ground resolution of zoom 0 at the equator (m/px)
PREFETCH_WORKERS = 6               # parallel downloads, kept low to stay polite
COLOUR_MPP = 1.0                   # enough for a median colour per roof
TEXTURE_MAX_MPP = 2.0              # textured roofs need imagery at least this sharp
PHOTO_DEFAULT_MPP = 0.25           # finest default roof texture resolution
WORLD = [-180.0, -85.0, 180.0, 85.0]

# Built-in open imagery, one entry per source in sources.json, each with its licence, a link to the
# provider's terms and the attribution to show. Every licence allows commercial use and derived works.
# coverage = [min_lon, min_lat, max_lon, max_lat]; regions/outline/exclude refine it, see footprint().
BUILTIN = json.loads((Path(__file__).parent / "sources.json").read_text(encoding="utf-8"))
for _k, _v in BUILTIN.items():
    _v.update(id=_k, licence_confirmed=True, builtin=True, group=_v["continent"])


# user sources live in data/imagery.json
_URL_RE = re.compile(r"^https?://[^\s]+$")


def validate_url(url):
    """Raise ValueError unless url is an XYZ/TMS template ({z} {x} {y} or {-y}) or a {bbox} URL."""
    if not _URL_RE.match(url or ""):
        raise ValueError("imagery URL must start with http:// or https://")
    if "{bbox}" in url:          # WMS / ArcGIS exportImage: EPSG:3857 minx,miny,maxx,maxy
        return
    for k in ("{z}", "{x}"):
        if k not in url:
            raise ValueError(f"imagery URL needs {k} (or use {{bbox}} for WMS-style URLs)")
    if "{y}" not in url and "{-y}" not in url:
        raise ValueError("imagery URL needs {y} (or {-y} for TMS)")


def _store(data_dir: Path) -> Path:
    return data_dir / "imagery.json"


def _user_sources(data_dir: Path) -> dict:
    p = _store(data_dir)
    return json.loads(p.read_text()) if p.exists() else {}


_STORE_LOCK = threading.Lock()


def _write_store(data_dir: Path, user: dict):
    _write_atomic(_store(data_dir), json.dumps(user, indent=1).encode())


def source_id(name: str) -> str:
    """Id of a user source, from its name."""
    sid = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "imagery"
    return sid + "-custom" if sid in BUILTIN else sid


def list_sources(data_dir: Path) -> dict:
    """{id: config} of built-in and user sources."""
    return {**BUILTIN, **_user_sources(data_dir)}


def get_source(data_dir: Path, sid) -> dict:
    s = list_sources(data_dir).get(sid)
    if s is None:
        raise KeyError(f"unknown imagery source: {sid}")
    return s


def _check_coverage(cov):
    if cov is None:
        return None
    try:
        w, s, e, n = (float(v) for v in cov)
    except (TypeError, ValueError):
        raise ValueError("coverage must be [min_lon, min_lat, max_lon, max_lat]")
    if not (-180 <= w < e <= 180 and -90 <= s < n <= 90):
        raise ValueError("coverage must be [min_lon, min_lat, max_lon, max_lat] in degrees")
    return [round(v, 5) for v in (w, s, e, n)]


def geotiff_root(data_dir: Path) -> Path:
    """Folder the user copies GeoTIFF folders into: tak-buildings3d-data/geotiff/<name>/."""
    return data_dir.parent / "geotiff"


def save_source(data_dir: Path, name, url, max_zoom, attribution, licence_confirmed, coverage=None, folder=None):
    """Register a user imagery source (a tile URL or a GeoTIFF folder); the user must confirm its licence."""
    if not licence_confirmed:
        raise ValueError("confirm that this imagery's licence allows derived 3D models")
    if not attribution.strip():
        raise ValueError("attribution text is required")
    sid = source_id(name)
    if folder:
        from . import geotiff
        root = geotiff_root(data_dir)
        if folder not in geotiff.folders(root):
            raise ValueError(f"no GeoTIFF folder called {folder!r} in {root.name}/")
        info = geotiff.scan(root / folder)
        lat = (info["coverage"][1] + info["coverage"][3]) / 2
        extra = {"backend": "geotiff", "folder": folder, "url": f"/api/imagery/{sid}/tiles/{{z}}/{{x}}/{{y}}",
                 "min_zoom": geotiff.MIN_ZOOM, "max_zoom": geotiff.max_zoom(info["resolution_m"], lat), **info}
    else:
        validate_url(url)
        extra = {"url": url.strip(), "max_zoom": int(max_zoom), "coverage": _check_coverage(coverage)}
    with _STORE_LOCK:
        user = _user_sources(data_dir)
        if sid in user:                 # saved again: its old tiles may be out of date
            clear_cache(data_dir, sid)
        _FOOTPRINTS.pop(sid, None)
        user[sid] = {"id": sid, "name": name.strip(), **extra,
                     "attribution": attribution.strip(), "licence_confirmed": True,
                     "confirmed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        _write_store(data_dir, user)
    return user[sid]


def delete_source(data_dir: Path, sid):
    """Remove a user source and its cached tiles."""
    with _STORE_LOCK:
        user = _user_sources(data_dir)
        if sid in user:
            clear_cache(data_dir, sid)
        user.pop(sid, None)
        _FOOTPRINTS.pop(sid, None)
        _write_store(data_dir, user)


def coverage(src):
    """[min_lon, min_lat, max_lon, max_lat]; user sources saved without one count as worldwide."""
    return src.get("coverage") or WORLD


# Coverage boxes overlap at borders (France's reaches Barcelona), so a built-in source can name
# Geofabrik regions whose outlines, clipped to the box and minus "exclude" boxes, are its footprint.
# Without the Geofabrik index (offline) the box is used. City sources instead name an "outline" in
# outlines.json: the city boundary from OpenStreetMap, simplified.
_FOOTPRINTS = {}
_FOOTPRINT_LOCK = threading.Lock()


def footprint(src):
    """(geometry, prepared geometry) where the source has imagery, or None for its coverage box."""
    if not src.get("regions") and not src.get("outline") and not src.get("files"):
        return None
    with _FOOTPRINT_LOCK:
        if src["id"] not in _FOOTPRINTS and src.get("files"):
            g = unary_union([box(*f["bounds"]) for f in src["files"]])
            _FOOTPRINTS[src["id"]] = (g, prep(g))
        if src["id"] not in _FOOTPRINTS and src.get("outline"):
            g = shape(json.loads((Path(__file__).parent / "outlines.json").read_text())[src["outline"]])
            _FOOTPRINTS[src["id"]] = (g, prep(g))
        if src["id"] not in _FOOTPRINTS:
            try:
                from . import regions
                if not (regions.DATA / "geofabrik-index-v1.json").exists():
                    return None                  # don't download the index just for this
                parts = [g for p, g, _ in regions._shapes() if p["id"] in src["regions"]]
            except Exception:
                return None
            g = unary_union(parts).intersection(box(*coverage(src))) if parts else None
            for ex in src.get("exclude", []):
                g = g.difference(box(*ex)) if g is not None else None
            _FOOTPRINTS[src["id"]] = (g, prep(g)) if g is not None and not g.is_empty else None
        return _FOOTPRINTS[src["id"]]


_CONTINENTS = {"Australia and Oceania": "Oceania", "Russian Federation": "Europe"}
_PLACES = {}


def place(src):
    """{"group": continent, "country": name} for a user source, from the Geofabrik outlines its
    coverage box overlaps most. Empty without the index; "Worldwide" without a coverage box."""
    if not src.get("coverage"):
        return {"group": "Worldwide"}
    key = tuple(src["coverage"])
    if key not in _PLACES:
        try:
            from . import regions
            if not (regions.DATA / "geofabrik-index-v1.json").exists():
                return {}
            shapes = regions._shapes()
        except Exception:
            return {}
        parent = {p["id"]: p.get("parent") for p, _, _ in shapes}
        name = {p["id"]: p["name"] for p, _, _ in shapes}
        b = box(*key)
        cands = []                 # countries: children of a continent, or Russia's districts
        for p, g, _ in shapes:
            par = parent.get(p["id"])
            if par is not None and (parent.get(par) is None or par == "russia") and g.intersects(b):
                cands.append(("russia" if par == "russia" else p["id"], g))
        # skip groupings like "Alps" or "DACH" that contain another candidate country
        cands = [(r, g) for r, g in cands
                 if not any(h.area < g.area and g.intersection(h).area > h.area / 2 for _, h in cands)]
        best = max(((g.intersection(b).area, r) for r, g in cands), default=None)
        if best is None:
            _PLACES[key] = {}
        else:
            rid = best[1]
            top = parent.get(rid) or rid
            _PLACES[key] = {"group": _CONTINENTS.get(name[top], name[top]),
                            "country": "Russia" if rid == "russia" else name[rid]}
    return _PLACES[key]


def footprint_geojson(src):
    """Footprint simplified to ~1 km for the map, as GeoJSON, or None."""
    fp = footprint(src)
    if fp is None:
        return None
    g = fp[0].simplify(0.01, preserve_topology=True)
    return {"geometry": mapping(g), "area": round(g.area, 4)}


def covers(src, geom):
    """True if the source's coverage overlaps a shapely geometry."""
    w, s, e, n = coverage(src)
    gw, gs, ge, gn = geom.bounds
    if not (gw <= e and ge >= w and gs <= n and gn >= s):
        return False
    fp = footprint(src)
    return fp is None or fp[1].intersects(geom)


def contains(src, x, y):
    """True if the source has imagery at lon x, lat y."""
    w, s, e, n = coverage(src)
    if not (w <= x <= e and s <= y <= n):
        return False
    fp = footprint(src)
    return fp is None or fp[1].contains(Point(x, y))


def _size(src):
    """Area in square degrees: of all sources covering a point, the smallest is the most local."""
    fp = footprint(src)
    if fp is not None:
        return fp[0].area
    w, s, e, n = coverage(src)
    return (e - w) * (n - s)


def available(data_dir: Path, geom):
    """Sources (built-in and user) whose coverage overlaps the geometry."""
    return [v for v in list_sources(data_dir).values() if covers(v, geom)]


def native_mpp(src, lat) -> float:
    """Finest useful ground resolution (m/px): the imagery's own, or what its max zoom gives."""
    return max(src.get("resolution_m") or 0.0, Z0_MPP * math.cos(math.radians(lat)) / 2 ** src["max_zoom"])


def default_mpp(src, lat) -> float:
    """Default roof texture m/px: the imagery's own, but not finer than PHOTO_DEFAULT_MPP."""
    return max(round(native_mpp(src, lat), 2), PHOTO_DEFAULT_MPP)


def best_source(data_dir: Path, geom, roofs="colour"):
    """Highest-resolution source covering the area's centroid, or None.

    User sources count only if saved with a coverage box; texture roofs skip coarse imagery."""
    c = geom.centroid
    cands = [v for v in list_sources(data_dir).values() if (v.get("builtin") or v.get("coverage"))
             and (roofs != "texture" or native_mpp(v, c.y) <= TEXTURE_MAX_MPP) and contains(v, c.x, c.y)]
    return max(cands, key=lambda v: (v["max_zoom"], -_size(v))) if cands else None


# tiles are cached in data/tiles/<key>/z/x/y.img, one folder per source URL
def cache_key(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()[:12]


def source_cache_key(src) -> str:
    """Cache folder name for a source, from its URL or Planetary Computer collection."""
    if src.get("backend") == "planetary_computer":
        return cache_key(f"planetarycomputer:{src['pc_collection']}")
    if src.get("backend") == "geotiff":
        return cache_key(f"geotiff:{src['folder']}")
    return cache_key(src["url"])


def cache_usage(data_dir: Path):
    """[{key, id, name, tiles, bytes}] per cache folder; id/name None for removed sources."""
    root = data_dir / "tiles"
    by_key = {source_cache_key(v): v for v in list_sources(data_dir).values()}
    out = []
    for d in sorted(root.iterdir()) if root.exists() else []:
        if not d.is_dir():
            continue
        files = [f for f in d.rglob("*") if f.is_file()]   # map tiles, NAIP chunks, STAC searches
        tiles = sum(f.suffix in (".img", ".png") for f in files)
        src = by_key.get(d.name)
        out.append({"key": d.name, "id": src and src["id"], "name": src and src["name"],
                    "tiles": tiles, "bytes": sum(f.stat().st_size for f in files)})
    return out


def clear_cache(data_dir: Path, key: str | None = None):
    """Delete one source's cached tiles (by cache key or source id), or all of them."""
    root = data_dir / "tiles"
    if key is None:
        shutil.rmtree(root, ignore_errors=True)
        return
    srcs = list_sources(data_dir)
    if key in srcs:
        key = source_cache_key(srcs[key])
    if not re.fullmatch(r"[0-9a-f]{12}", key):       # also keeps "../" out of the path
        raise ValueError("bad cache key")
    shutil.rmtree(root / key, ignore_errors=True)


def lonlat_to_pixel(lon, lat, z):
    """Global Web Mercator pixel coordinates at zoom z."""
    n = TILE * 2 ** z
    x = (lon + 180.0) / 360.0 * n
    s = math.sin(math.radians(lat))
    y = (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n
    return x, y


def zoom_for_mpp(lat, mpp, max_zoom) -> int:
    """Coarsest zoom whose ground resolution is at least as fine as mpp."""
    return max(0, min(max_zoom, math.ceil(math.log2(Z0_MPP * math.cos(math.radians(lat)) / mpp))))


def merc_bounds(z, x, y):
    """(minx, miny, maxx, maxy) of tile z/x/y in EPSG:3857 metres."""
    s = 2 * MERC_R / 2 ** z
    return (-MERC_R + x * s, MERC_R - (y + 1) * s, -MERC_R + (x + 1) * s, MERC_R - y * s)


def merc_to_lonlat(x, y):
    """EPSG:3857 metres -> lon/lat degrees."""
    return x / MERC_R * 180.0, math.degrees(2 * math.atan(math.exp(y / MERC_R * math.pi)) - math.pi / 2)


def open_source(src, cache_dir: Path):
    """TileSource for a source config (or built-in id), using its backend if it has one."""
    if isinstance(src, str):
        src = BUILTIN[src]
    if src.get("backend") == "planetary_computer":
        from .naip_pc import PlanetaryComputerSource   # avoids a circular import
        return PlanetaryComputerSource(src, cache_dir)
    if src.get("backend") == "geotiff":
        from .geotiff import GeoTiffSource
        return GeoTiffSource(src, cache_dir, geotiff_root(cache_dir.parent))
    return TileSource(src, cache_dir)


def fetch_parallel(fn, jobs, unit, log=None, progress=None, workers=PREFETCH_WORKERS) -> int:
    """Run fn(*job) for each job on a thread pool; returns how many raised."""
    done, bad, last = 0, 0, time.time()
    ex = ThreadPoolExecutor(workers)
    try:
        for fut in as_completed([ex.submit(fn, *job) for job in jobs]):
            bad += fut.exception() is not None
            done += 1
            if progress:
                progress(done / len(jobs))
            if log and (time.time() - last > 15 or done == len(jobs)):
                log(f"      imagery: {done}/{len(jobs)} {unit}" + (f" ({bad} failed)" if bad else ""))
                last = time.time()
    finally:
        ex.shutdown(wait=False, cancel_futures=True)
    return bad


_M_PER_DEG_LON = 111320          # at the equator; times cos(lat) elsewhere
_M_PER_DEG_LAT = 110540


def _is_image(data: bytes) -> bool:
    try:
        Image.open(io.BytesIO(data)).verify()
        return True
    except Exception:
        return False


def _write_atomic(p: Path, data: bytes):
    """Write via a temp file, so a reader never sees half a tile."""
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(f".{threading.get_ident()}.part")
    tmp.write_bytes(data)
    tmp.replace(p)


class TileSource:
    """XYZ / TMS / {bbox} imagery source with an on-disk tile cache; failed tiles come back grey."""

    def __init__(self, cfg, cache_dir: Path):
        if isinstance(cfg, str):
            cfg = BUILTIN[cfg]
        if not cfg.get("licence_confirmed"):
            raise ValueError("imagery source has no licence confirmation")
        self.cfg = cfg
        self.cache = cache_dir / source_cache_key(cfg)
        self.session = requests.Session()
        self.session.headers["User-Agent"] = UA
        self.failed = 0
        self.fetched = 0
        self._lock = threading.Lock()

    def _tile_path(self, z, x, y) -> Path:
        return self.cache / str(z) / str(x) / f"{y}.img"

    def _blank(self):
        with self._lock:
            self.failed += 1
        return Image.new("RGB", (TILE, TILE), (128, 128, 128))

    def tile(self, z, x, y):
        """RGB tile z/x/y, from the cache or downloaded; grey if unavailable."""
        p = self._tile_path(z, x, y)
        if not p.exists():
            bb = ",".join(f"{v:.3f}" for v in merc_bounds(z, x, y))
            url = (self.cfg["url"].replace("{z}", str(z)).replace("{x}", str(x)).replace("{bbox}", bb)
                   .replace("{-y}", str(2 ** z - 1 - y)).replace("{y}", str(y)))
            data = None
            for attempt in range(3):
                try:
                    r = self.session.get(url, timeout=30)
                    if r.status_code == 200 and _is_image(r.content):    # WMS errors come back as 200 XML
                        data = r.content
                        break
                except requests.RequestException:
                    pass
                time.sleep(1 + attempt)
            if data is None:
                return self._blank()
            _write_atomic(p, data)
            with self._lock:
                self.fetched += 1
        try:
            return Image.open(io.BytesIO(p.read_bytes())).convert("RGB")
        except Exception:
            return self._blank()

    def tiles_for(self, bounds, z):
        """{(z, x, y)} covering each (lon_min, lat_min, lon_max, lat_max) box."""
        out = set()
        for lon_min, lat_min, lon_max, lat_max in bounds:
            x0, y0 = lonlat_to_pixel(lon_min, lat_max, z)
            x1, y1 = lonlat_to_pixel(lon_max, lat_min, z)
            for tx in range(int(x0) // TILE, int(math.ceil(x1) - 1) // TILE + 1):
                for ty in range(int(y0) // TILE, int(math.ceil(y1) - 1) // TILE + 1):
                    out.add((z, tx, ty))
        return out

    def prefetch(self, keys, workers=PREFETCH_WORKERS, log=None, progress=None):
        """Download uncached (z, x, y) tiles in parallel; render-on-request servers are slow per tile."""
        todo = sorted(k for k in keys if not self._tile_path(*k).exists())
        if not todo:
            return
        if log:
            log(f"      imagery: fetching {len(todo)} tiles ({len(keys) - len(todo)} cached)")
        fetch_parallel(self.tile, todo, "tiles", log, progress, workers)

    def prefetch_area(self, bounds, mpp, texture, log=None, progress=None):
        """Fetch the imagery roofs in these lon/lat boxes need at mpp; `texture` is for other backends."""
        lat = (min(b[1] for b in bounds) + max(b[3] for b in bounds)) / 2 if bounds else 0.0
        z = zoom_for_mpp(lat, mpp, self.cfg["max_zoom"])
        self.prefetch(self.tiles_for(bounds, z), log=log, progress=progress)

    def crop(self, lon_min, lat_min, lon_max, lat_max, z):
        """Mosaic covering bbox at zoom z. Returns (image, (px0, py0)) in global pixels."""
        x0, y0 = lonlat_to_pixel(lon_min, lat_max, z)
        x1, y1 = lonlat_to_pixel(lon_max, lat_min, z)
        px0, py0, px1, py1 = int(math.floor(x0)), int(math.floor(y0)), int(math.ceil(x1)), int(math.ceil(y1))
        img = Image.new("RGB", (max(1, px1 - px0), max(1, py1 - py0)))
        for tx in range(px0 // TILE, (px1 - 1) // TILE + 1):
            for ty in range(py0 // TILE, (py1 - 1) // TILE + 1):
                img.paste(self.tile(z, tx, ty), (tx * TILE - px0, ty * TILE - py0))
        return img, (px0, py0)

    def roof_image(self, lon_min, lat_min, lon_max, lat_max, mpp):
        """(image at ~mpp m/px covering the box, uv(lon, lat) -> (u, v) into it)."""
        lat = (lat_min + lat_max) / 2
        z = zoom_for_mpp(lat, mpp, self.cfg["max_zoom"])
        img, (px0, py0) = self.crop(lon_min, lat_min, lon_max, lat_max, z)
        Wp, Hp = img.size
        w_m = (lon_max - lon_min) * _M_PER_DEG_LON * math.cos(math.radians(lat))
        h_m = (lat_max - lat_min) * _M_PER_DEG_LAT
        img = img.resize((max(4, math.ceil(w_m / mpp)), max(4, math.ceil(h_m / mpp))), Image.LANCZOS)

        def uv(lon, lat_):
            gx, gy = lonlat_to_pixel(lon, lat_, z)
            return (gx - px0) / Wp, (gy - py0) / Hp
        return img, uv

    def footprint_colour(self, polys_lonlat, mpp=1.0):
        """Median RGB inside the footprint polygons (lon/lat), or the pixel under a tiny roof."""
        lon_min = min(p.bounds[0] for p in polys_lonlat)
        lat_min = min(p.bounds[1] for p in polys_lonlat)
        lon_max = max(p.bounds[2] for p in polys_lonlat)
        lat_max = max(p.bounds[3] for p in polys_lonlat)
        z = zoom_for_mpp((lat_min + lat_max) / 2, mpp, self.cfg["max_zoom"])
        img, (px0, py0) = self.crop(lon_min, lat_min, lon_max, lat_max, z)

        def pixels(ring):
            return [tuple(v - o for v, o in zip(lonlat_to_pixel(x, y, z), (px0, py0))) for x, y in ring.coords]
        mask = Image.new("L", img.size, 0)
        d = ImageDraw.Draw(mask)
        for poly in polys_lonlat:
            d.polygon(pixels(poly.exterior), fill=255, outline=0)   # outline=0 trims ~1 px so walls don't bleed in
            for ring in poly.interiors:
                d.polygon(pixels(ring), fill=0)
        px = np.asarray(img)[np.asarray(mask) > 0]
        if len(px) < 6:          # too few pixels for a median
            pt = max(polys_lonlat, key=lambda p: p.area).representative_point()
            x, y = (v - o for v, o in zip(lonlat_to_pixel(pt.x, pt.y, z), (px0, py0)))
            return img.getpixel((min(max(int(x), 0), img.width - 1), min(max(int(y), 0), img.height - 1)))[:3]
        return tuple(int(v) for v in np.median(px, axis=0))
