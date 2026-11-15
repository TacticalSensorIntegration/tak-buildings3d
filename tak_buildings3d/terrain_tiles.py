"""GLO-30 heightmap tiles for CesiumJS, so buildings sit on the same DEM they were built on.

Cesium GeographicTilingScheme; each tile is SIZE x SIZE float32 heights, row-major from the NW corner.
"""
import math
import threading
from pathlib import Path

import numpy as np
import rasterio
import rasterio.errors
import rasterio.windows
from rasterio.enums import Resampling
from rasterio.windows import from_bounds

from .geo import bilinear, dem_missing, dem_url, geoid_undulation, set_gdal_http_defaults

set_gdal_http_defaults(GDAL_CACHEMAX="256")   # MB of decoded DEM blocks kept between tiles

SIZE = 65
MIN_LEVEL, MAX_LEVEL = 9, 14
GEOID_GRID = 5            # geoid samples per tile side; it varies over tens of km
_MAX_OPEN = 64            # DEM files kept open before the handles are dropped


def tile_bounds(level: int, x: int, y: int) -> tuple[float, float, float, float]:
    """(west, south, east, north) degrees of a GeographicTilingScheme tile (y = 0 at the north)."""
    d = 180.0 / 2 ** level
    w, n = -180.0 + x * d, 90.0 - y * d
    return w, n - d, w + d, n


_dem_url = dem_url             # module-level so tests can point it at a local file
_open: dict = {}               # (lon_t, lat_t) -> (dataset, lock), or None at sea
_open_lock = threading.Lock()


def _dem(lon_t, lat_t):
    """DEM dataset for a 1x1 degree cell, kept open so its header is fetched once."""
    key = (lon_t, lat_t)
    with _open_lock:
        if key in _open:
            return _open[key]
    try:
        v = (rasterio.open(_dem_url(lon_t, lat_t)), threading.Lock())
    except rasterio.errors.RasterioIOError:
        dem_missing(_dem_url(lon_t, lat_t))     # raises (and isn't cached) unless the cell is open sea
        v = None
    with _open_lock:
        if len(_open) > _MAX_OPEN:
            _open.clear()
        return _open.setdefault(key, v)


def heights(level: int, x: int, y: int) -> np.ndarray:
    """SIZE x SIZE float32 ellipsoidal heights for a tile."""
    w, s, e, n = tile_bounds(level, x, y)
    lons = np.linspace(w, e, SIZE)
    lats = np.linspace(n, s, SIZE)
    LON, LAT = np.meshgrid(lons, lats)
    msl = np.zeros_like(LON)
    step = (e - w) / (SIZE - 1)                       # degrees
    for lat_t in range(math.floor(s), math.ceil(n)):
        for lon_t in range(math.floor(w), math.ceil(e)):
            # Points in this DEM file; the tile's own east/north edges go to the last file.
            m = (LON >= lon_t) & (LON < lon_t + 1) & (LAT >= lat_t) & (LAT < lat_t + 1)
            m |= (LON == lon_t + 1) & (lon_t + 1 == math.ceil(e)) & (LAT >= lat_t) & (LAT < lat_t + 1)
            m |= (LAT == lat_t + 1) & (lat_t + 1 == math.ceil(n)) & (LON >= lon_t) & (LON <= lon_t + 1)
            if not m.any():
                continue
            dem = _dem(lon_t, lat_t)
            if dem is None:
                continue                                 # sea
            ds, lock = dem
            with lock:                                   # a dataset handle isn't thread-safe
                b = ds.bounds
                pad = 2 * step
                win = from_bounds(max(w - pad, b.left), max(s - pad, b.bottom),
                                  min(e + pad, b.right), min(n + pad, b.top), ds.transform)
                win = win.round_offsets().round_lengths()
                if win.width < 2 or win.height < 2:
                    continue
                # Read at about twice the tile's density so COG overviews serve low levels.
                k = min(1.0, abs(ds.transform.e) / (step / 2))
                ow, oh = max(2, math.ceil(win.width * k)), max(2, math.ceil(win.height * k))
                z = ds.read(1, window=win, out_shape=(oh, ow), resampling=Resampling.bilinear).astype(np.float64)
                tf = ds.window_transform(win) @ rasterio.Affine.scale(win.width / ow, win.height / oh)
            col, row = ~tf @ (LON[m], LAT[m])
            msl[m] = bilinear(z, np.asarray(col) - 0.5, np.asarray(row) - 0.5)
    ng = GEOID_GRID
    gl, gt = np.linspace(w, e, ng), np.linspace(n, s, ng)
    g = np.array([[geoid_undulation(lo, la) for lo in gl] for la in gt])
    gi = np.linspace(0, ng - 1, SIZE)
    gr = np.array([np.interp(gi, np.arange(ng), row) for row in g])
    gfull = np.array([np.interp(gi, np.arange(ng), gr[:, j]) for j in range(SIZE)]).T
    return (msl + gfull).astype(np.float32)


def point_msl(lon: float, lat: float) -> float:
    """Ground height in EGM2008 metres at a point; 0 at sea."""
    dem = _dem(math.floor(lon), math.floor(lat))
    if dem is None:
        return 0.0
    ds, lock = dem
    with lock:
        col, row = ~ds.transform @ (lon, lat)
        c0 = min(max(int(math.floor(col - 0.5)), 0), ds.width - 2)
        r0 = min(max(int(math.floor(row - 0.5)), 0), ds.height - 2)
        z = ds.read(1, window=rasterio.windows.Window(c0, r0, 2, 2)).astype(np.float64)
    return float(bilinear(z, col - 0.5 - c0, row - 0.5 - r0))


def tile_bytes(cache: Path, level: int, x: int, y: int) -> bytes | None:
    """Cached float32 tile, or None outside the served levels / tile range."""
    if not (MIN_LEVEL <= level <= MAX_LEVEL) or not (0 <= x < 2 ** (level + 1)) or not (0 <= y < 2 ** level):
        return None
    p = cache / str(level) / str(x) / f"{y}.f32"
    if p.exists():
        return p.read_bytes()
    data = heights(level, x, y).tobytes()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(f".{threading.get_ident()}.part")
        tmp.write_bytes(data)
        tmp.replace(p)
    except OSError:
        pass                                  # read-only cache
    return data
