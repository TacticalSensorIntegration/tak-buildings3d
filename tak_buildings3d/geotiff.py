"""Imagery from the user's own GeoTIFFs, in a folder under geotiff/.

Files can be in any projection. Tiles are rendered from them in Web Mercator, for the map and for
builds, and cached like downloaded tiles.
"""
import io
import math
import threading
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from rasterio.transform import from_bounds
from rasterio.warp import Resampling, reproject, transform_bounds

from .imagery import TILE, TileSource, _write_atomic, merc_bounds, merc_to_lonlat

EXTENSIONS = {".tif", ".tiff"}
UNREADABLE = {".sid": "MrSID", ".ecw": "ECW", ".jp2": "JPEG 2000"}
MIN_ZOOM = 14          # coarser map tiles would read whole files that have no overviews


def folders(root: Path):
    """Subfolders of root that contain GeoTIFFs, by name."""
    if not root.exists():
        return []
    return sorted(d.name for d in root.iterdir()
                  if d.is_dir() and any(f.suffix.lower() in EXTENSIONS for f in d.rglob("*")))


def scan(folder: Path):
    """{"files": [{"path", "bounds"}], "coverage", "resolution_m"} for the GeoTIFFs in folder."""
    files, res, skipped = [], [], {}
    for f in sorted(folder.rglob("*")):
        ext = f.suffix.lower()
        if ext in UNREADABLE:
            skipped[UNREADABLE[ext]] = skipped.get(UNREADABLE[ext], 0) + 1
        if ext not in EXTENSIONS or not f.is_file():
            continue
        try:
            with rasterio.open(f) as ds:
                if ds.crs is None or ds.count < 3:
                    continue
                w, s, e, n = transform_bounds(ds.crs, "EPSG:4326", *ds.bounds)
                px = abs(ds.res[0])
                if ds.crs.is_geographic:
                    px *= 111320 * math.cos(math.radians((s + n) / 2))
                else:
                    px *= ds.crs.linear_units_factor[1]
        except rasterio.errors.RasterioIOError:
            continue
        files.append({"path": str(f.relative_to(folder)), "bounds": [round(v, 6) for v in (w, s, e, n)]})
        res.append(px)
    if not files:
        why = f" ({', '.join(f'{n} {k}' for k, n in skipped.items())} files need converting to GeoTIFF first)" \
            if skipped else ""
        raise ValueError(f"no georeferenced RGB GeoTIFFs found in {folder.name}{why}")
    b = [f["bounds"] for f in files]
    return {"files": files, "resolution_m": round(min(res), 3),
            "coverage": [min(x[0] for x in b), min(x[1] for x in b), max(x[2] for x in b), max(x[3] for x in b)]}


def max_zoom(resolution_m, lat):
    """Zoom whose ground resolution first matches the imagery's."""
    return max(MIN_ZOOM, min(23, math.ceil(math.log2(40075016.7 * math.cos(math.radians(lat)) / TILE / resolution_m))))


class GeoTiffSource(TileSource):
    """Tiles rendered from local GeoTIFFs; areas outside every file are transparent on the map."""

    def __init__(self, cfg, cache_dir: Path, root: Path):
        super().__init__(cfg, cache_dir)
        self.folder = root / cfg["folder"]
        self._tls = threading.local()      # open datasets per thread (rasterio isn't thread-safe)

    def _open(self, path):
        ds = getattr(self._tls, "ds", None)
        if ds is None:
            ds = self._tls.ds = {}
        if path not in ds:
            ds[path] = rasterio.open(self.folder / path)
        return ds[path]

    def render(self, z, x, y):
        """(RGBA array, fully covered) for tile z/x/y."""
        minx, miny, maxx, maxy = merc_bounds(z, x, y)
        w, s = merc_to_lonlat(minx, miny)
        e, n = merc_to_lonlat(maxx, maxy)
        out = np.zeros((4, TILE, TILE), np.uint8)
        dst = from_bounds(minx, miny, maxx, maxy, TILE, TILE)
        for f in self.cfg["files"]:
            fw, fs, fe, fn = f["bounds"]
            if fe < w or fw > e or fn < s or fs > n:
                continue
            ds = self._open(f["path"])
            part = np.zeros((4, TILE, TILE), np.uint8)
            for b in range(3):              # pixels outside the file stay 0 (black counts as no data)
                reproject(rasterio.band(ds, b + 1), part[b], dst_transform=dst, dst_crs="EPSG:3857",
                          dst_nodata=0, resampling=Resampling.bilinear, num_threads=1)
            part[3] = np.where(part[:3].max(axis=0) > 0, 255, 0)
            new = (part[3] > 0) & (out[3] == 0)
            out[:, new] = part[:, new]
        return out, bool(out[3].all())

    def tile(self, z, x, y):
        """RGB tile z/x/y (black outside the files), rendered once and cached."""
        return Image.open(io.BytesIO(self.tile_bytes(z, x, y))).convert("RGB")

    def encode(self, z, x, y):
        """JPEG for tiles inside the files, PNG with transparency at their edges."""
        arr, full = self.render(z, x, y)
        buf = io.BytesIO()
        if full:
            Image.fromarray(arr[:3].transpose(1, 2, 0)).save(buf, "JPEG", quality=88)
        else:
            Image.fromarray(arr.transpose(1, 2, 0), "RGBA").save(buf, "PNG")
        return buf.getvalue()

    def tile_bytes(self, z, x, y):
        """Encoded tile for the map, from the cache when rendered before."""
        p = self._tile_path(z, x, y)
        if p.exists():
            return p.read_bytes()
        data = self.encode(z, x, y)
        _write_atomic(p, data)
        with self._lock:
            self.fetched += 1
        return data
