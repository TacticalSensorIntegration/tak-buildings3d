"""NAIP roofs read from the original 0.6 m UTM GeoTIFFs on Microsoft Planetary Computer.

Files are found via STAC and read in chunks with range requests, using a free anonymous SAS token;
the USGS ImageServer is the fallback. Terms: https://planetarycomputer.microsoft.com/terms
"""
import calendar
import io
import json
import math
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from pyproj import Transformer
from rasterio.transform import Affine, from_bounds
from rasterio.warp import Resampling, reproject
from rasterio.windows import Window
from shapely.geometry import box, shape

from .geo import set_gdal_http_defaults
from .imagery import PREFETCH_WORKERS, TILE, TileSource, fetch_parallel, merc_bounds, merc_to_lonlat

# GDAL waits forever on a stalled connection unless told otherwise
set_gdal_http_defaults(GDAL_HTTP_RETRY_DELAY="1", GDAL_HTTP_CONNECTTIMEOUT="20", GDAL_HTTP_LOW_SPEED_TIME="30",
                       GDAL_HTTP_LOW_SPEED_LIMIT="1000", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif,.TIF")

STAC = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
SAS = "https://planetarycomputer.microsoft.com/api/sas/v1/token/{collection}"
CHUNK = 1024                 # px; files are read and cached in CHUNK x CHUNK blocks
DECODED_CHUNKS = 48          # decoded chunks kept in memory (~3 MB each)
STAC_MAX_AGE = 30 * 86400    # s; cached search results are reused this long
SEARCH_PAD = 0.02            # deg; search beyond the box so neighbouring roofs hit the cache
NODATA_MAX = 0.05            # roof cuts with more no-data (black) pixels than this use the tiles


class PlanetaryComputerSource(TileSource):
    """A Planetary Computer collection read from its own GeoTIFFs, with tile fallback."""

    def __init__(self, cfg, cache_dir: Path):
        super().__init__(cfg, cache_dir)
        self.collection = cfg["pc_collection"]
        self._token, self._token_exp = None, 0.0
        self._items = []                  # newest first: dicts id, href, date, geom (lon/lat)
        self._searched = []               # lon/lat boxes already searched
        self._meta = {}                   # item id -> (crs, transform, width, height)
        self._transformers = {}           # crs -> lon/lat to crs Transformer
        self._tls = threading.local()     # per-thread open datasets (rasterio isn't thread-safe)
        self._chunk_locks = {}            # chunk path -> lock, so each chunk is fetched once
        self._decoded = OrderedDict()     # chunk path -> decoded array, most recently used last
        self._mlock = threading.Lock()
        self.direct = self.fallback = 0   # roofs cut from the UTM grid / via Web Mercator tiles
        self.pc_failed = 0

    def token(self):
        """SAS read token for the collection's blob storage, renewed 10 minutes before expiry."""
        with self._mlock:
            if time.time() > self._token_exp - 600:
                r = self.session.get(SAS.format(collection=self.collection), timeout=30)
                r.raise_for_status()
                d = r.json()
                self._token = d["token"]
                exp = d.get("msft:expiry", "")
                try:
                    self._token_exp = calendar.timegm(time.strptime(exp[:19], "%Y-%m-%dT%H:%M:%S"))
                except ValueError:
                    self._token_exp = time.time() + 1800
            return self._token

    def items_for(self, lon_min, lat_min, lon_max, lat_max):
        """Files whose footprint intersects the box, newest first."""
        want = box(lon_min, lat_min, lon_max, lat_max)
        with self._mlock:
            known = any(b.contains(want) for b in self._searched)
        if not known:
            pad = SEARCH_PAD
            area = box(lon_min - pad, lat_min - pad, lon_max + pad, lat_max + pad)
            found = self._search(area.bounds)
            with self._mlock:
                have = {i["id"] for i in self._items}
                self._items += [i for i in found if i["id"] not in have]
                self._items.sort(key=lambda i: i["date"], reverse=True)
                self._searched.append(area)
        with self._mlock:
            return [i for i in self._items if i["geom"].intersects(want)]

    def _search(self, bbox):
        """STAC items in a lon/lat bbox (all pages), cached on disk for STAC_MAX_AGE."""
        cache = self.cache / "stac"
        cache.mkdir(parents=True, exist_ok=True)
        f = cache / ("%.4f_%.4f_%.4f_%.4f.json" % bbox)
        if f.exists() and time.time() - f.stat().st_mtime < STAC_MAX_AGE:
            feats = json.loads(f.read_text())
        else:
            feats, body = [], {"collections": [self.collection], "bbox": list(bbox), "limit": 250,
                               "sortby": [{"field": "datetime", "direction": "desc"}]}
            url = STAC
            for _ in range(20):                       # pages of 250
                r = self.session.post(url, json=body, timeout=60)
                r.raise_for_status()
                d = r.json()
                feats += d["features"]
                nxt = next((ln for ln in d.get("links", []) if ln.get("rel") == "next"), None)
                if not nxt:
                    break
                url, body = nxt["href"], nxt.get("body", body)
            f.write_text(json.dumps(feats))
        return [{"id": ft["id"], "href": ft["assets"]["image"]["href"], "date": ft["properties"]["datetime"],
                 "geom": shape(ft["geometry"])} for ft in feats if "image" in ft.get("assets", {})]

    def _ds(self, item):
        """This thread's open dataset for an item; remote URLs get the SAS token as query string."""
        dss = getattr(self._tls, "ds", None)
        if dss is None:
            dss = self._tls.ds = {}
        if item["id"] not in dss:
            tok = self.token() if item["href"].startswith("http") else None
            dss[item["id"]] = rasterio.open(f"{item['href']}?{tok}" if tok else item["href"])
        return dss[item["id"]]

    def meta(self, item):
        """(crs, transform, width, height) of an item's file."""
        m = self._meta.get(item["id"])
        if m is None:
            ds = self._ds(item)
            m = self._meta[item["id"]] = (ds.crs.to_string(), ds.transform, ds.width, ds.height)
        return m

    def _chunk_path(self, item_id, cx, cy) -> Path:
        return self.cache / "pc" / item_id / f"{cx}_{cy}.png"

    def chunk(self, item, cx, cy):
        """RGB array of one CHUNK x CHUNK block of a file (smaller at its edges), cached as PNG."""
        p = self._chunk_path(item["id"], cx, cy)
        if not p.exists():
            with self._mlock:
                lock = self._chunk_locks.setdefault(p, threading.Lock())
            with lock:
                if not p.exists():
                    _, _, W, H = self.meta(item)
                    win = Window(cx * CHUNK, cy * CHUNK, min(CHUNK, W - cx * CHUNK), min(CHUNK, H - cy * CHUNK))
                    a = self._ds(item).read([1, 2, 3], window=win)
                    p.parent.mkdir(parents=True, exist_ok=True)
                    tmp = p.with_suffix(".part")
                    Image.fromarray(np.moveaxis(a, 0, -1)).save(tmp, "PNG", compress_level=1)
                    tmp.replace(p)
                    with self._mlock:
                        self.fetched += 1
        with self._mlock:
            a = self._decoded.get(p)
            if a is not None:
                self._decoded.move_to_end(p)
                return a
        a = np.asarray(Image.open(p).convert("RGB"))
        with self._mlock:
            self._decoded[p] = a
            while len(self._decoded) > DECODED_CHUNKS:
                self._decoded.popitem(last=False)
        return a

    def _transformer(self, crs):
        if crs not in self._transformers:
            self._transformers[crs] = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
        return self._transformers[crs]

    def _window(self, item, lon_min, lat_min, lon_max, lat_max, pad=1):
        """Pixel window (col0, row0, col1, row1) of the box in the file, or None if outside."""
        crs, T, W, H = self.meta(item)
        tr = self._transformer(crs)
        xs, ys = tr.transform([lon_min, lon_max, lon_max, lon_min], [lat_min, lat_min, lat_max, lat_max])
        c0 = math.floor((min(xs) - T.c) / T.a) - pad
        c1 = math.ceil((max(xs) - T.c) / T.a) + pad
        r0 = math.floor((T.f - max(ys)) / -T.e) - pad
        r1 = math.ceil((T.f - min(ys)) / -T.e) + pad
        c0, r0, c1, r1 = max(c0, 0), max(r0, 0), min(c1, W), min(r1, H)
        return (c0, r0, c1, r1) if c1 > c0 and r1 > r0 else None

    def _chunks_of(self, win):
        """(cx, cy) of the chunks a pixel window touches."""
        c0, r0, c1, r1 = win
        return [(cx, cy) for cx in range(c0 // CHUNK, (c1 - 1) // CHUNK + 1)
                for cy in range(r0 // CHUNK, (r1 - 1) // CHUNK + 1)]

    def read(self, item, win):
        """RGB array of a pixel window (col0, row0, col1, row1), assembled from cached chunks."""
        c0, r0, c1, r1 = win
        out = np.zeros((r1 - r0, c1 - c0, 3), np.uint8)
        for cx, cy in self._chunks_of(win):
            a = self.chunk(item, cx, cy)
            x0, y0 = cx * CHUNK, cy * CHUNK
            sx0, sy0 = max(c0, x0), max(r0, y0)
            sx1, sy1 = min(c1, x0 + a.shape[1]), min(r1, y0 + a.shape[0])
            if sx1 > sx0 and sy1 > sy0:
                out[sy0 - r0:sy1 - r0, sx0 - c0:sx1 - c0] = a[sy0 - y0:sy1 - y0, sx0 - x0:sx1 - x0]
        return out

    def _covering(self, lon_min, lat_min, lon_max, lat_max):
        """Newest file whose footprint contains the whole box."""
        want = box(lon_min, lat_min, lon_max, lat_max)
        for it in self.items_for(lon_min, lat_min, lon_max, lat_max):
            if it["geom"].contains(want):
                return it
        return None

    def roof_image(self, lon_min, lat_min, lon_max, lat_max, mpp):
        """Roof cut from the newest covering file's UTM grid, falling back to Web Mercator tiles."""
        try:
            it = self._covering(lon_min, lat_min, lon_max, lat_max)
            win = it and self._window(it, lon_min, lat_min, lon_max, lat_max)
            a = self.read(it, win) if win else None
        except Exception:
            win = a = None
            self.pc_failed += 1
        if a is None or (a.max(axis=2) == 0).mean() > NODATA_MAX:   # none, or a no-data collar
            self.fallback += 1
            return super().roof_image(lon_min, lat_min, lon_max, lat_max, mpp)
        crs, T, _, _ = self.meta(it)
        c0, r0, c1, r1 = win
        img = Image.fromarray(a)
        res = abs(T.a)
        if mpp > res * 1.05:                              # coarser than native: shrink
            img = img.resize((max(4, math.ceil(img.width * res / mpp)), max(4, math.ceil(img.height * res / mpp))),
                             Image.LANCZOS)
        E0, E1 = T.c + c0 * T.a, T.c + c1 * T.a
        N0, N1 = T.f + r0 * T.e, T.f + r1 * T.e         # top, bottom
        tr = self._transformer(crs)
        self.direct += 1

        def uv(lon, lat):
            e, n = tr.transform(lon, lat)
            return (e - E0) / (E1 - E0), (N0 - n) / (N0 - N1)
        return img, uv

    def tile(self, z, x, y):
        """Web Mercator tile built from the files' pixels."""
        p = self._tile_path(z, x, y)
        if p.exists():
            return super().tile(z, x, y)
        try:
            img = self._mercator_tile(z, x, y)
        except Exception:
            img = None
            self.pc_failed += 1
        if img is None:                      # no file here: use the USGS service
            return super().tile(z, x, y)
        p.parent.mkdir(parents=True, exist_ok=True)
        buf = io.BytesIO()
        img.save(buf, "PNG", compress_level=1)
        p.write_bytes(buf.getvalue())
        return img

    def _mercator_tile(self, z, x, y):
        """Tile z/x/y reprojected from every file under it (newest on top), or None if none."""
        mb = merc_bounds(z, x, y)
        lon0, lat1 = merc_to_lonlat(mb[0], mb[3])
        lon1, lat0 = merc_to_lonlat(mb[2], mb[1])
        dst = np.zeros((3, TILE, TILE), np.uint8)
        hit = False
        for it in reversed(self.items_for(lon0, lat0, lon1, lat1)):      # newest painted last
            win = self._window(it, lon0, lat0, lon1, lat1, pad=2)
            if not win:
                continue
            crs, T, _, _ = self.meta(it)
            a = np.moveaxis(self.read(it, win), -1, 0)
            src_T = T @ Affine.translation(win[0], win[1])
            layer = np.zeros_like(dst)
            reproject(a, layer, src_transform=src_T, src_crs=crs, dst_transform=from_bounds(*mb, TILE, TILE),
                      dst_crs="EPSG:3857", src_nodata=0, dst_nodata=0, resampling=Resampling.bilinear)
            m = layer.max(axis=0) > 0
            dst[:, m] = layer[:, m]
            hit = hit or m.any()
        return Image.fromarray(np.moveaxis(dst, 0, -1)) if hit else None

    def prefetch_area(self, bounds, mpp, texture, log=None, progress=None):
        """Fetch file chunks under textured roofs, or Web Mercator tiles for colour roofs."""
        if not bounds:
            return
        if not texture:
            return super().prefetch_area(bounds, mpp, texture, log=log, progress=progress)
        try:
            self.items_for(min(b[0] for b in bounds), min(b[1] for b in bounds),
                           max(b[2] for b in bounds), max(b[3] for b in bounds))
            covering = [(b, self._covering(*b)) for b in bounds]
            files = {it["id"]: it for _, it in covering if it}
            with ThreadPoolExecutor(PREFETCH_WORKERS) as ex:    # read each file's header once
                list(ex.map(self.meta, files.values()))
            keys = set()
            for b, it in covering:
                win = it and self._window(it, *b)
                if win:
                    keys |= {(it["id"], cx, cy) for cx, cy in self._chunks_of(win)}
        except Exception as e:
            self.pc_failed += 1
            if log:
                log(f"      Planetary Computer unavailable ({type(e).__name__}); using the USGS service")
            return super().prefetch_area(bounds, mpp, texture, log=log, progress=progress)
        todo = sorted(k for k in keys if not self._chunk_path(*k).exists())
        if log:
            dates = sorted({files[k[0]]["date"][:4] for k in keys})
            log(f"      NAIP {'/'.join(dates)} from Planetary Computer: {len(files)} files, "
                f"{len(todo)} of {len(keys)} chunks to fetch")
        if todo:   # failed chunks are retried per roof, then fall back to tiles
            self.pc_failed += fetch_parallel(self.chunk, [(files[i], cx, cy) for i, cx, cy in todo], "chunks",
                                             log, progress)
