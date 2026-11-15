"""OSM building footprints from Overpass or a local .osm.pbf, as lon/lat shapely polygons.

Both sources yield dicts with id ("w123" / "r456"), tags, geom and is_part (a building:part)."""
import hashlib
import json
import os
import time
from pathlib import Path

import requests
from shapely.geometry import MultiPolygon, Polygon
from shapely.ops import unary_union

from . import USER_AGENT

UA = USER_AGENT
# The public instance is for light use; heavy users should set their own server.
OVERPASS = os.environ.get("TAK_BUILDINGS3D_OVERPASS_URL", "https://overpass-api.de/api/interpreter")
BUSY_CODES = (429, 502, 503, 504)   # busy or overloaded; the usage policy asks for a 30 s wait
RETRY_WAIT, RETRIES = 30, 2
CACHE_MAX_AGE = 7 * 86400    # s; Overpass results are reused for a week, like the Geofabrik index


def fetch(area, cache_dir: Path):
    """Overpass JSON of buildings and parts in ("around", lat, lon, r_m) or ("bbox", s, w, n, e),
    cached per query for a week."""
    if area[0] == "around":
        f = f"(around:{area[3]},{area[1]},{area[2]})"
    else:
        f = f"({area[1]},{area[2]},{area[3]},{area[4]})"
    q = f"""[out:json][timeout:600][maxsize:1073741824];
(
  way["building"]{f};
  relation["building"]{f};
  way["building:part"]{f};
  relation["building:part"]{f};
);
out body;
>;
out skel qt;"""
    cache = cache_dir / f"{hashlib.sha1(q.encode()).hexdigest()[:16]}.json"
    if cache.exists() and time.time() - cache.stat().st_mtime < CACHE_MAX_AGE:
        return json.loads(cache.read_text())
    for attempt in range(RETRIES + 1):
        r = requests.post(OVERPASS, data={"data": q}, headers={"User-Agent": UA}, timeout=900)
        if r.status_code not in BUSY_CODES or attempt == RETRIES:
            break
        time.sleep(RETRY_WAIT)
    r.raise_for_status()
    data = r.json()
    remark = data.get("remark", "")
    if "error" in remark or "timed out" in remark:       # partial results: don't build or cache them
        raise RuntimeError(f"Overpass: {remark.strip()}")
    cache.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache.with_suffix(".part")
    tmp.write_text(r.text)
    tmp.replace(cache)
    return data


def _join_rings(segments):
    """Join open way segments (lists of (lon,lat)) into closed rings."""
    segs = [list(s) for s in segments if len(s) >= 2]
    rings = []
    while segs:
        ring = segs.pop(0)
        changed = True
        while ring[0] != ring[-1] and changed:
            changed = False
            for i, s in enumerate(segs):
                if s[0] == ring[-1]:
                    ring += s[1:]
                elif s[-1] == ring[-1]:
                    ring += s[::-1][1:]
                elif s[-1] == ring[0]:
                    ring = s[:-1] + ring
                elif s[0] == ring[0]:
                    ring = s[::-1][:-1] + ring
                else:
                    continue
                segs.pop(i)
                changed = True
                break
        if ring[0] == ring[-1] and len(ring) >= 4:
            rings.append(ring)
    return rings


def from_pbf(path: Path, clip):
    """Buildings from a local .osm.pbf that intersect clip (lon/lat), in the same form as buildings()."""
    import osmium
    from shapely import wkb
    from shapely.prepared import prep

    fab = osmium.geom.WKBFactory()
    pclip = prep(clip)
    minx, miny, maxx, maxy = clip.bounds
    out = []
    fp = (osmium.FileProcessor(str(path))
          .with_locations()
          .with_areas(osmium.filter.KeyFilter("building", "building:part"))
          .with_filter(osmium.filter.KeyFilter("building", "building:part")))
    for o in fp:
        if not o.is_area():
            continue
        try:
            geom = wkb.loads(fab.create_multipolygon(o), hex=True)
        except Exception:      # broken multipolygon in the data
            continue
        bx0, by0, bx1, by1 = geom.bounds
        if bx1 < minx or bx0 > maxx or by1 < miny or by0 > maxy or not pclip.intersects(geom):
            continue
        tags = {t.k: t.v for t in o.tags}
        oid = f"{'w' if o.from_way() else 'r'}{o.orig_id()}"
        f = _feature(oid, tags, geom.geoms[0] if len(geom.geoms) == 1 else geom)
        if f:
            out.append(f)
    return out


def buildings(data):
    """Building dicts from Overpass JSON."""
    nodes = {e["id"]: (e["lon"], e["lat"]) for e in data["elements"] if e["type"] == "node"}
    ways = {e["id"]: e for e in data["elements"] if e["type"] == "way"}
    out = []
    for e in data["elements"]:
        tags = e.get("tags") or {}
        if e["type"] not in ("way", "relation"):
            continue
        if not (tags.get("building") or tags.get("building:part")):
            continue
        if e["type"] == "way":
            coords = [nodes[n] for n in e["nodes"] if n in nodes]
            if len(coords) < 4 or coords[0] != coords[-1]:
                continue
            geom = Polygon(coords)
        else:
            if tags.get("type") not in ("multipolygon", "building", None):
                continue
            outers, inners = [], []
            for m in e.get("members", []):
                if m["type"] != "way" or m["ref"] not in ways:
                    continue
                pts = [nodes[n] for n in ways[m["ref"]]["nodes"] if n in nodes]
                (inners if m.get("role") == "inner" else outers).append(pts)
            polys = [Polygon(r) for r in _join_rings(outers)]
            holes = [Polygon(r) for r in _join_rings(inners)]
            if not polys:
                continue
            geom = unary_union(polys)
            if holes:
                geom = geom.difference(unary_union(holes))
        f = _feature(f"{e['type'][0]}{e['id']}", tags, geom)
        if f:
            out.append(f)
    return out


def _feature(oid, tags, geom):
    """The building dict, or None for building=no outlines and empty geometry."""
    if tags.get("building") == "no" and not tags.get("building:part"):
        return None
    geom = geom.buffer(0)
    if geom.is_empty:
        return None
    return {"id": oid, "tags": tags, "geom": geom, "is_part": bool(tags.get("building:part"))}


def polygons(geom):
    """The Polygons of a (Multi)Polygon or geometry collection."""
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, MultiPolygon):
        return list(geom.geoms)
    return [g for g in getattr(geom, "geoms", []) if isinstance(g, Polygon)]
