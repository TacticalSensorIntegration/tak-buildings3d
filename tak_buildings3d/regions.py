"""Geofabrik extracts: list them, download once and reduce to a buildings-only .pbf.

Downloads are kept in DATA/regions/ as <id>.osm.pbf plus <id>.json metadata.
"""
import json
import re
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests
from shapely.geometry import Point, shape
from shapely.prepared import prep

from . import USER_AGENT
from .pipeline import DATA

INDEX_URL = "https://download.geofabrik.de/index-v1.json"
UA = USER_AGENT
REGIONS = DATA / "regions"
CACHE_MAX_AGE = 7 * 86400     # s; the Geofabrik index and extract sizes are refreshed weekly


def run_osmium(args):
    """Run osmium-tool, raising RuntimeError with the tail of its stderr on failure."""
    r = subprocess.run(["osmium", *args], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"osmium {args[0]} failed: {r.stderr.strip()[-500:]}")


def _index_raw():
    """Geofabrik's index (GeoJSON of every extract), cached in DATA."""
    cache = DATA / "geofabrik-index-v1.json"
    if not cache.exists() or time.time() - cache.stat().st_mtime > CACHE_MAX_AGE:
        r = requests.get(INDEX_URL, headers={"User-Agent": UA}, timeout=120)
        r.raise_for_status()
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(r.text)
    return json.loads(cache.read_text())


def _bounds(geom):
    """[min_lon, min_lat, max_lon, max_lat] of a GeoJSON geometry, or None if it has none."""
    xs, ys = [], []

    def walk(c):
        if isinstance(c[0], (int, float)):
            xs.append(c[0])
            ys.append(c[1])
        else:
            for x in c:
                walk(x)
    if geom:
        walk(geom["coordinates"])
    return [min(xs), min(ys), max(xs), max(ys)] if xs else None


def _props(p):
    """Geofabrik properties with US states renamed ("us/california") and moved under "us"."""
    p = dict(p)
    if "/" in p.get("name", ""):
        p["name"] = p["name"].rsplit("/", 1)[-1].replace("-", " ").title()
    if p["id"].startswith("us/") and p.get("parent") == "north-america":
        p["parent"] = "us"
    return p


def index():
    """[{id, name, parent, pbf_url, bbox}] sorted with parents before children."""
    out = []
    for f in _index_raw()["features"]:
        p = _props(f["properties"])
        if "pbf" not in p.get("urls", {}):
            continue
        out.append({"id": p["id"], "name": p["name"], "parent": p.get("parent"),
                    "pbf_url": p["urls"]["pbf"], "bbox": _bounds(f.get("geometry"))})
    names = {r["id"]: r for r in out}

    def path(r):
        chain = [r["name"]]
        while r.get("parent") in names:
            r = names[r["parent"]]
            chain.insert(0, r["name"])
        return " / ".join(chain)
    for r in out:
        r["path"] = path(r)
    return sorted(out, key=lambda r: r["path"])


_SHAPES = None
_SIZES_LOCK = threading.Lock()


def _shapes():
    """[(props, shapely geometry, prepared)] for every extract, built once per process."""
    global _SHAPES
    if _SHAPES is None:
        out = []
        for f in _index_raw()["features"]:
            p = _props(f["properties"])
            if "pbf" not in p.get("urls", {}) or not f.get("geometry"):
                continue
            g = shape(f["geometry"])
            out.append((p, g, prep(g)))
        _SHAPES = out
    return _SHAPES


def _pbf_size(url):
    """Download size via HEAD, cached in DATA/geofabrik-sizes.json."""
    cache = DATA / "geofabrik-sizes.json"
    with _SIZES_LOCK:
        sizes = json.loads(cache.read_text()) if cache.exists() else {}
        hit = sizes.get(url)
        if hit and time.time() - hit["t"] < CACHE_MAX_AGE:
            return hit["bytes"]
    try:
        r = requests.head(url, headers={"User-Agent": UA}, timeout=10, allow_redirects=True)
        n = int(r.headers.get("content-length", 0)) or None
    except requests.RequestException:
        n = None
    if n:
        with _SIZES_LOCK:
            sizes = json.loads(cache.read_text()) if cache.exists() else {}
            sizes[url] = {"bytes": n, "t": time.time()}
            cache.write_text(json.dumps(sizes))
    return n


def at(lon, lat, with_sizes=True):
    """Extracts covering a point, smallest first (city/state -> country -> continent)."""
    return covering(Point(lon, lat), with_sizes)


def covering(geom, with_sizes=True):
    """Extracts containing the whole lon/lat geometry, smallest first, with path, outline and size."""
    hits = [(p, g) for p, g, pg in _shapes() if pg.contains(geom)]
    hits.sort(key=lambda h: h[1].area)
    names = {p["id"]: p["name"] for p, _, _ in _shapes()}
    parents = {p["id"]: p.get("parent") for p, _, _ in _shapes()}
    out = []
    for p, g in hits:
        chain, par = [p["name"]], p.get("parent")
        while par in names:
            chain.insert(0, names[par])
            par = parents.get(par)
        out.append({"id": p["id"], "name": p["name"], "path": " / ".join(chain), "url": p["urls"]["pbf"],
                    "bbox": list(g.bounds), "outline": g.simplify(0.02).__geo_interface__,
                    "downloaded": local_pbf(p["id"]) is not None})
    if with_sizes and out:
        with ThreadPoolExecutor(8) as ex:
            for o, n in zip(out, ex.map(_pbf_size, [o["url"] for o in out])):
                o["bytes"] = n
    return out


def best_for(geom):
    """Smallest downloaded extract containing geom (lon/lat shapely), or None."""
    have = {d["id"] for d in downloaded()}
    if not have:
        return None
    try:
        shapes = _shapes()
    except Exception:          # index unreachable
        return None
    cands = [(g.area, p["id"]) for p, g, pg in shapes if p["id"] in have and pg.contains(geom)]
    if not cands:   # partially covered: use the one covering the centroid
        c = geom.centroid
        cands = [(g.area, p["id"]) for p, g, pg in shapes if p["id"] in have and pg.contains(c)]
    return min(cands)[1] if cands else None


def region(rid):
    """Index entry for a Geofabrik id; KeyError if unknown."""
    for r in index():
        if r["id"] == rid:
            return r
    raise KeyError(f"unknown Geofabrik region: {rid}")


def _file(rid, suffix):
    """Local file for a region id; "/" is flattened and anything unsafe rejected (ids come from URLs)."""
    name = rid.replace("/", "__")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", name) or name.startswith("."):
        raise ValueError(f"bad region id: {rid!r}")
    return REGIONS / f"{name}{suffix}"


def local_pbf(rid):
    """Path of a downloaded extract, or None."""
    p = _file(rid, ".osm.pbf")
    return p if p.exists() else None


def downloaded():
    """Metadata of every downloaded extract (see download())."""
    out = []
    for m in sorted(REGIONS.glob("*.json")):
        meta = json.loads(m.read_text())
        if _file(meta["id"], ".osm.pbf").exists():
            out.append(meta)
    return out


def _fetch_resumable(url, dest, log=print, progress=None, retries=6):
    """Stream url to dest, resuming with HTTP Range requests after a dropped connection, also from
    a partial file left by an earlier failed attempt. Gives up after `retries` drops in a row with
    no progress."""
    tag = dest.with_name(dest.name + ".etag")        # version of the file the partial belongs to
    version = tag.read_text() if tag.exists() else None
    if version is None:                              # a partial of unknown version can't be resumed
        dest.unlink(missing_ok=True)
    total, attempt = 0, 0
    while True:
        have = dest.stat().st_size if dest.exists() else 0
        start = have
        headers = {"User-Agent": UA}
        if have:                                     # the rest, only if the server's file is unchanged
            headers["Range"] = f"bytes={have}-"
            if version:
                headers["If-Range"] = version
        try:
            with requests.get(url, headers=headers, stream=True, timeout=(30, 60)) as resp:
                resp.raise_for_status()
                if have and resp.status_code != 206:     # changed, or no range support: start over
                    have = start = 0
                if resp.status_code == 200:
                    version = resp.headers.get("ETag") or resp.headers.get("Last-Modified")
                    if version:
                        tag.write_text(version)
                    else:
                        tag.unlink(missing_ok=True)
                if not total:
                    total = int(resp.headers.get("content-length", 0)) + have
                    if total:
                        log(f"  {total / 1e6:.0f} MB")
                with open(dest, "ab" if have else "wb") as f:
                    for chunk in resp.iter_content(1 << 20):
                        f.write(chunk)
                        have += len(chunk)
                        if progress and total:
                            progress(have / total)
            if total and have < total:
                raise requests.exceptions.ConnectionError(f"connection closed at {have} of {total} bytes")
            tag.unlink(missing_ok=True)
            return
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout,
                requests.exceptions.ChunkedEncodingError) as e:
            attempt = 1 if have > start else attempt + 1
            if attempt > retries:
                raise
            log(f"  connection dropped ({type(e).__name__}); resuming from {have / 1e6:.0f} MB "
                f"(retry {attempt}/{retries})")
            time.sleep(min(30, 2 ** attempt))


def download(rid, log=print, progress=None):
    """Download a Geofabrik extract and reduce it to buildings. Returns the local .pbf path."""
    r = region(rid)
    REGIONS.mkdir(parents=True, exist_ok=True)
    raw = _file(rid, ".download.osm.pbf")   # osmium detects format from the suffix
    tmp = _file(rid, ".tmp.osm.pbf")
    final = _file(rid, ".osm.pbf")
    log(f"downloading {r['pbf_url']}")
    try:
        _fetch_resumable(r["pbf_url"], raw, log, progress)
        full_bytes = raw.stat().st_size
        if shutil.which("osmium"):
            log("reducing to buildings (osmium tags-filter)")
            run_osmium(["tags-filter", str(raw), "a/building", "a/building:part", "--overwrite", "-o", str(tmp)])
            raw.unlink()
            reduced = True
        else:
            log("osmium-tool not installed: keeping the full extract (slower builds)")
            raw.replace(tmp)
            reduced = False
        tmp.replace(final)                  # an older extract stays usable until this point
    except Exception as e:
        tmp.unlink(missing_ok=True)
        if type(e).__name__ == "Cancelled":  # a failed download is kept, so a retry can resume it
            raw.unlink(missing_ok=True)
            raw.with_name(raw.name + ".etag").unlink(missing_ok=True)
        raise
    meta = {"id": rid, "name": r["name"], "path": r["path"], "bbox": r["bbox"], "url": r["pbf_url"],
            "download_bytes": full_bytes, "bytes": final.stat().st_size, "buildings_only": reduced,
            "downloaded": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    _file(rid, ".json").write_text(json.dumps(meta, indent=1))
    log(f"ready: {final} ({meta['bytes'] / 1e6:.0f} MB)")
    return final


def delete(rid):
    """Remove a downloaded extract and its metadata."""
    for p in (_file(rid, ".osm.pbf"), _file(rid, ".json")):
        if p.exists():
            p.unlink()
