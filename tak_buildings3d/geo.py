"""Coordinate frames, geoid conversion and terrain sampling.

The DEM gives EGM2008 heights but ATAK and Cesium place tiles by ellipsoidal height, hence msl_to_hae().
"""
import math
import os
import threading

import numpy as np
import pyproj
import rasterio
import rasterio.errors
import requests
from pyproj import Transformer
from rasterio.windows import from_bounds

from . import USER_AGENT

# GDAL settings for reading COGs over HTTP.
GDAL_HTTP_DEFAULTS = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR", "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
    "GDAL_HTTP_MULTIRANGE": "YES", "GDAL_HTTP_TIMEOUT": "60", "GDAL_HTTP_MAX_RETRY": "3",
}


def set_gdal_http_defaults(**extra: str) -> None:
    """Set GDAL_HTTP_DEFAULTS plus extra in the environment, without overriding existing values."""
    for k, v in {**GDAL_HTTP_DEFAULTS, **extra}.items():
        os.environ.setdefault(k, v)


def bilinear(z, col, row):
    """Bilinear interpolation of z at (col, row), integers being pixel centres; clamps at the edges."""
    c0 = np.clip(np.floor(col).astype(int), 0, z.shape[1] - 2)
    r0 = np.clip(np.floor(row).astype(int), 0, z.shape[0] - 2)
    fc, fr = np.clip(col - c0, 0, 1), np.clip(row - r0, 0, 1)
    return ((z[r0, c0] * (1 - fc) + z[r0, c0 + 1] * fc) * (1 - fr)
            + (z[r0 + 1, c0] * (1 - fc) + z[r0 + 1, c0 + 1] * fc) * fr)


def _sample(z, transform, lon, lat):
    """Bilinear sample of a north-up raster window (pixel-is-area)."""
    col, row = ~transform @ (lon, lat)
    return bilinear(z, col - 0.5, row - 0.5)


class LocalFrame:
    """East-north-up metric frame: transverse Mercator centred on the anchor, accurate over a few km."""

    def __init__(self, lat, lon):
        self.lat, self.lon = lat, lon
        self.crs = pyproj.CRS.from_proj4(
            f"+proj=tmerc +lat_0={lat} +lon_0={lon} +k=1 +x_0=0 +y_0=0 +ellps=WGS84 +units=m +no_defs")
        self._fwd = Transformer.from_crs("EPSG:4326", self.crs, always_xy=True)
        self._inv = Transformer.from_crs(self.crs, "EPSG:4326", always_xy=True)

    def to_local(self, lon, lat):
        """lon/lat (degrees) -> (east, north) metres from the anchor."""
        return self._fwd.transform(lon, lat)

    def to_lonlat(self, x, y):
        """(east, north) metres from the anchor -> lon/lat (degrees)."""
        return self._inv.transform(x, y)


# EGM2008 2.5' grid, read directly because PROJ network access doesn't work in worker threads.
GEOID_URL = "/vsicurl/https://cdn.proj.org/us_nga_egm08_25.tif"
_geoid_cells = {}
_geoid_lock = threading.Lock()


def _geoid_cell(lon0, lat0):
    """Cached geoid window covering a 1x1 degree cell plus a margin."""
    key = (lon0, lat0)
    with _geoid_lock:
        if key not in _geoid_cells:
            try:
                with rasterio.open(GEOID_URL) as ds:
                    win = from_bounds(lon0 - 0.1, lat0 - 0.1, lon0 + 1.1, lat0 + 1.1, ds.transform)
                    win = win.round_offsets().round_lengths()
                    _geoid_cells[key] = (ds.read(1, window=win, boundless=True).astype(np.float64),
                                         ds.window_transform(win))
            except rasterio.errors.RasterioIOError as e:
                url = GEOID_URL.removeprefix("/vsicurl/")
                raise RuntimeError(f"can't read the EGM2008 geoid grid from {url}: {e}") from e
        return _geoid_cells[key]


def geoid_undulation(lon: float, lat: float) -> float:
    """EGM2008 geoid height N above the WGS84 ellipsoid (m), bilinear."""
    z, tf = _geoid_cell(math.floor(lon), math.floor(lat))
    return float(_sample(z, tf, lon, lat))


def msl_to_hae(lon: float, lat: float, h: float) -> float:
    """EGM2008 orthometric height -> WGS84 ellipsoidal height."""
    return float(h + geoid_undulation(lon, lat))


def enu_to_ecef_matrix(lat, lon, h):
    """Flattened column-major 4x4 ENU->ECEF transform for a 3D Tiles root."""
    lat_r, lon_r = math.radians(lat), math.radians(lon)
    to_ecef = Transformer.from_crs("EPSG:4979", "EPSG:4978", always_xy=True)
    x, y, z = to_ecef.transform(lon, lat, h)
    e = [-math.sin(lon_r), math.cos(lon_r), 0.0]
    n = [-math.sin(lat_r) * math.cos(lon_r), -math.sin(lat_r) * math.sin(lon_r), math.cos(lat_r)]
    u = [math.cos(lat_r) * math.cos(lon_r), math.cos(lat_r) * math.sin(lon_r), math.sin(lat_r)]
    return [*e, 0.0, *n, 0.0, *u, 0.0, x, y, z, 1.0]


DEM_BASE = "https://copernicus-dem-30m.s3.amazonaws.com"


def dem_url(lon_t: int, lat_t: int) -> str:
    """GDAL path of the GLO-30 file for the 1x1 degree cell with SW corner (lon_t, lat_t)."""
    ns = f"{'N' if lat_t >= 0 else 'S'}{abs(lat_t):02d}"
    ew = f"{'E' if lon_t >= 0 else 'W'}{abs(lon_t):03d}"
    name = f"Copernicus_DSM_COG_10_{ns}_00_{ew}_00_DEM"
    return f"/vsicurl/{DEM_BASE}/{name}/{name}.tif"


_NO_DEM = set()


def dem_missing(path: str) -> bool:
    """True if a DEM cell doesn't exist (open sea). Raises if it can't be read for another reason, so
    a network failure never turns into a flat 0 m DEM."""
    if path in _NO_DEM or not path.startswith("/vsicurl/"):
        return True
    try:
        code = requests.head(path.removeprefix("/vsicurl/"), headers={"User-Agent": USER_AGENT}, timeout=20).status_code
    except requests.RequestException as e:
        raise OSError(f"Copernicus DEM unreachable: {e}") from e
    if code == 404:
        _NO_DEM.add(path)
        return True
    raise OSError(f"Copernicus DEM read failed (HTTP {code}); try again")


class Terrain:
    """Copernicus GLO-30 DEM (EGM2008 heights) from the public AWS bucket; missing tiles (ocean) read 0 m."""

    def __init__(self, lon_min, lat_min, lon_max, lat_max, pad=0.005):
        self.tiles = []   # (lon0, lat0, z array, affine transform)
        for lat_t in range(math.floor(lat_min - pad), math.floor(lat_max + pad) + 1):
            for lon_t in range(math.floor(lon_min - pad), math.floor(lon_max + pad) + 1):
                try:
                    with rasterio.open(dem_url(lon_t, lat_t)) as ds:
                        b = ds.bounds
                        win = from_bounds(max(lon_min - pad, b.left), max(lat_min - pad, b.bottom),
                                          min(lon_max + pad, b.right), min(lat_max + pad, b.top), ds.transform)
                        win = win.round_offsets().round_lengths()
                        if win.width < 2 or win.height < 2:
                            continue
                        self.tiles.append((lon_t, lat_t, ds.read(1, window=win).astype(np.float64),
                                           ds.window_transform(win)))
                except rasterio.errors.RasterioIOError:
                    dem_missing(dem_url(lon_t, lat_t))      # raises unless the cell is open sea

    def sample(self, lon: float, lat: float) -> float:
        """Bilinear sample, EGM2008 metres."""
        for lon_t, lat_t, z, tf in self.tiles:
            if lon_t <= lon < lon_t + 1 and lat_t <= lat < lat_t + 1:
                return float(_sample(z, tf, lon, lat))
        return 0.0
