"""Building meshes in a local ENU frame (metres, z relative to the anchor's ellipsoidal height).

Each building yields roof, facade, gable and post Parts. UVs have a top-left origin (glTF).
"""
import math
from dataclasses import dataclass, field

import mapbox_earcut as earcut
import numpy as np
from PIL import Image
from shapely.geometry import Point, Polygon
from shapely.geometry.polygon import orient
from shapely.ops import unary_union
from shapely.strtree import STRtree

from . import roofs, style
from .osm import polygons

SIMPLIFY_M = 0.3        # Douglas-Peucker tolerance; ~30% fewer wall quads
ROOF_MODES = ("osm", "colour", "texture")
WINGED = {"hipped", "gabled", "gambrel", "mansard", "half-hipped"}   # one roof per wing on L/T/U footprints
POST_R, POST_SPACING, POST_COLOUR = 0.12, 6.0, (150, 152, 154)
POST_ANGLES = (0.5236, 2.618, 4.712)   # triangular prism: 30°, 150°, 270°
MIN_WALL_H = 0.02       # m; gable wall slivers lower than this are skipped


@dataclass
class Part:
    """One textured piece of a building; export.py merges parts of the same kind per tile."""
    name: str
    kind: str                      # "palette" | "facade" | "roof_img"
    colour: tuple = (200, 200, 200)
    key: tuple = None              # facade: (pattern, colour)
    image: Image.Image | None = None
    verts: list = field(default_factory=list)   # (x, y, z)
    uvs: list = field(default_factory=list)     # (u, v), glTF top-left origin
    faces: list = field(default_factory=list)   # (i, j, k)
    centroid: tuple = (0.0, 0.0)

    def add(self, v, uv=(0.5, 0.5)):
        self.verts.append(v)
        self.uvs.append(uv)
        return len(self.verts) - 1

    def tri(self, a, b, c, uva=(0.5, 0.5), uvb=(0.5, 0.5), uvc=(0.5, 0.5)):
        i = self.add(a, uva)
        self.add(b, uvb)
        self.add(c, uvc)
        self.faces.append((i, i + 1, i + 2))

    def quad(self, a, b, c, d, uva=(0.5, 0.5), uvb=(0.5, 0.5), uvc=(0.5, 0.5), uvd=(0.5, 0.5)):
        """Two triangles a-b-c, a-c-d (CCW seen from the front)."""
        i = self.add(a, uva)
        self.add(b, uvb)
        self.add(c, uvc)
        self.add(d, uvd)
        self.faces += [(i, i + 1, i + 2), (i, i + 2, i + 3)]


@dataclass
class Building:
    id: str
    parts: list
    area: float            # footprint (m²)
    height: float          # top above ground (m)
    centroid: tuple        # local (x, y)


def drop_outlines_with_parts(blds):
    """Drop outlines mostly covered by building:parts, which replace them (Simple 3D Buildings)."""
    parts = [b for b in blds if b["is_part"]]
    if not parts:
        return blds
    tree = STRtree([p["geom"] for p in parts])
    keep = []
    for b in blds:
        if not b["is_part"]:
            g = b["geom"]
            hits = [parts[i]["geom"] for i in tree.query(g)]
            covered = sum(h.intersection(g).area for h in hits) if hits else 0.0
            if g.area > 0 and covered / g.area > 0.5:
                continue
        keep.append(b)
    return keep


def _local_poly(poly, frame):
    poly = orient(poly, 1.0)  # exterior CCW, holes CW
    ext = [frame.to_local(*p) for p in list(poly.exterior.coords)[:-1]]
    holes = [[frame.to_local(*p) for p in list(r.coords)[:-1]] for r in poly.interiors]
    lp = Polygon(ext, holes)
    if not lp.is_valid:
        lp = lp.buffer(0)
    s = lp.simplify(SIMPLIFY_M, preserve_topology=True)
    return s if not s.is_empty and s.area > 0.8 * lp.area else lp


def _triangulate(poly):
    """Triangulate a polygon -> (verts Nx2, tris) with CCW-up winding."""
    rings = [list(poly.exterior.coords)[:-1]] + [list(r.coords)[:-1] for r in poly.interiors]
    rings = [r for r in rings if len(r) >= 3]
    flat = np.array([p for r in rings for p in r], dtype=np.float64)
    ends = np.cumsum([len(r) for r in rings]).astype(np.uint32)
    tri = earcut.triangulate_float64(flat, ends).reshape(-1, 3)
    out = []
    for i, j, k in tri:
        a, b, c = flat[i], flat[j], flat[k]
        cross = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        if abs(cross) < 1e-9:
            continue
        out.append((i, j, k) if cross > 0 else (i, k, j))
    return flat, out


def _as_polys(g):
    if g.is_empty:
        return []
    if isinstance(g, Polygon):
        return [g]
    return [p for p in getattr(g, "geoms", []) if isinstance(p, Polygon) and p.area > 1e-4]


def _edges(ring):
    """Consecutive vertex pairs around a closed ring."""
    pts = list(ring.coords)[:-1]
    for a, b in zip(pts, pts[1:] + pts[:1]):
        yield np.array(a), np.array(b)


def post_positions(poly, inset=0.3, max_posts=16):
    """Post positions at the outline corners plus one every POST_SPACING m, set in from the edge."""
    ring = poly.simplify(0.5).exterior
    pts = np.array(ring.coords)[:-1]
    if len(pts) > 8:
        pts = np.array(poly.minimum_rotated_rectangle.exterior.coords)[:-1]
    out = []
    for a, b in zip(pts, np.roll(pts, -1, axis=0)):
        n = max(1, int(np.hypot(*(b - a)) // POST_SPACING))
        out += [a + (b - a) * k / n for k in range(n)]
    c = np.array(poly.centroid.coords[0])
    res = []
    for p in out:
        d = c - p
        L = float(np.hypot(*d))
        q = p + d / L * min(inset, L / 2) if L > 1e-6 else p
        if poly.contains(Point(q)) or len(res) < 3:
            res.append((float(q[0]), float(q[1])))
    if len(res) > max_posts:
        res = res[:: math.ceil(len(res) / max_posts)]
    return res


def _roof_pieces(lp, st):
    """[(piece polygon, planes)]: one piece, or one rectangle per wing for L/T/U pitched roofs."""
    pieces = []
    if st["shape"] in WINGED and roofs.rectangularity(lp) < 0.85:
        rects = roofs.decompose(lp)
        w_main = roofs.obb(lp)[4]
        for r in rects:
            rh = st["roof_height"] * min(1.0, roofs.obb(r)[4] / max(w_main, 0.1))
            pieces.append((r, roofs.planes_for(st["shape"], r, rh, None, st["across"])))
        if rects and lp.difference(unary_union(rects)).area > 0.02 * lp.area:
            pieces.insert(0, (lp, []))       # flat cap under any part no wing covers
    if not pieces:
        pieces = [(lp, roofs.planes_for(st["shape"], lp, st["roof_height"], st["direction"], st["across"]))]
    return pieces


def _add_roof(roof, pieces, ze, roof_uv):
    for piece, planes in pieces:
        for plane, region in roofs.faces(planes, piece):
            for rp in _as_polys(region):
                flat, tris = _triangulate(rp)
                zs = ze + np.maximum(0.0, flat @ np.array(plane[:2]) + plane[2])
                base = len(roof.verts)
                for (x, y), zz in zip(flat, zs):
                    roof.add((x, y, float(zz)), roof_uv(x, y) if roof_uv else (0.5, 0.5))
                roof.faces += [(base + i, base + j, base + k) for i, j, k in tris]


def _add_underside(roof, lp, z0):
    """Downward-facing floor for raised parts (bridges, overhangs)."""
    flat, tris = _triangulate(lp)
    base = len(roof.verts)
    for x, y in flat:
        roof.add((x, y, z0), (0.5, 0.5))
    roof.faces += [(base + i, base + k, base + j) for i, j, k in tris]


def _add_walls(facade, lp, z0, ze):
    """Walls z0..ze. Facade UVs repeat per storey x bay, counted down from the eave so windows
    line up with the roof edge."""
    for ring in [lp.exterior] + list(lp.interiors):
        u = 0.0
        for a, b in _edges(ring):
            L = float(np.hypot(*(b - a)))
            if L < 1e-3:
                continue
            if facade.kind == "palette":
                u0 = u1 = v0 = v1 = 0.5
            else:
                u0, u1 = u / style.FACADE_BAY, (u + L) / style.FACADE_BAY
                v0, v1 = (ze - z0) / style.FACADE_STOREY, 0.0
            facade.quad((a[0], a[1], z0), (b[0], b[1], z0), (b[0], b[1], ze), (a[0], a[1], ze),
                        (u0, v0), (u1, v0), (u1, v1), (u0, v1))
            u += L


def _add_gable_walls(upper, lp, pieces, ze):
    """Walls from the eave up to the roof surface (gable ends, hip gaps)."""
    single = len(pieces) == 1
    for piece, planes in pieces:
        if not planes:
            continue
        for ring in ([lp.exterior] + list(lp.interiors)) if single else [orient(piece, 1.0).exterior]:
            for a, b in _edges(ring):
                ts = roofs.edge_breaks(planes, a, b)
                for t0, t1 in zip(ts, ts[1:]):
                    p, q = a + (b - a) * t0, a + (b - a) * t1
                    hp, hq = roofs.height(planes, *p), roofs.height(planes, *q)
                    if hp < MIN_WALL_H and hq < MIN_WALL_H:
                        continue
                    P0, Q0 = (p[0], p[1], ze), (q[0], q[1], ze)
                    P1, Q1 = (p[0], p[1], ze + hp), (q[0], q[1], ze + hq)
                    if hq >= MIN_WALL_H:
                        upper.tri(P0, Q0, Q1)
                    if hp >= MIN_WALL_H:
                        upper.tri(P0, Q1, P1)


def _add_posts(posts, poly, zg, z0):
    for x, y in post_positions(poly):
        ring = [(x + POST_R * math.cos(t), y + POST_R * math.sin(t)) for t in POST_ANGLES]
        for a, b in zip(ring, ring[1:] + ring[:1]):
            posts.quad((a[0], a[1], zg), (b[0], b[1], zg), (b[0], b[1], z0), (a[0], a[1], z0))


def _hex(c):
    return f"#{c[0]:02x}{c[1]:02x}{c[2]:02x}"


def build(blds, frame, terrain, anchor_hae, geoid_hae, roof_mode="osm", tiles=None, roof_mpp=0.5, log=None,
          imagery_fine=True):
    """Mesh OSM building dicts; returns ([Building], report rows).

    terrain.sample gives ground MSL and geoid_hae converts it to HAE. roof_mode "colour" and
    "texture" need tiles. In "colour" mode only fine imagery (imagery_fine) overrides a
    roof:colour tag, and not where it sees green (a tree)."""
    if roof_mode not in ROOF_MODES:
        raise ValueError(f"roof_mode must be one of {ROOF_MODES}")
    out, report = [], []
    blds = drop_outlines_with_parts(blds)

    for n, b in enumerate(blds):
        if log and roof_mode != "osm" and n and n % 500 == 0:
            log(f"      {n}/{len(blds)} buildings")
        geom = b["geom"]
        c = geom.centroid
        cx, cy = frame.to_local(c.x, c.y)
        lpolys = [p for p in (_local_poly(p, frame) for p in polygons(geom)) for p in _as_polys(p)]
        if not lpolys:
            continue
        area = sum(p.area for p in lpolys)
        largest = max(lpolys, key=lambda p: p.area)
        st = style.style(b, area, roofs.obb(largest)[4])
        tag_check = None
        if roof_mode == "colour":
            col = tiles.footprint_colour(polygons(geom), mpp=max(roof_mpp, 0.3))
            if col and st["roof_colour_src"] == "tag":
                tag_check = "agrees" if style.colours_agree(col, st["roof_colour"]) else "differs"
                if not (imagery_fine and style.colour_family(col) != "green"):
                    col = None
            if col:
                st["roof_colour"], st["roof_colour_src"] = col, "imagery"

        # Base on the lowest ground sample; GLO-30 is coarse (30 m).
        samples = [p for poly in polygons(geom) for p in poly.exterior.coords] + [(c.x, c.y)]
        ground_msl = min(terrain.sample(lon, lat) for lon, lat in samples)
        ground_hae = geoid_hae(c.x, c.y, ground_msl)
        zg = ground_hae - anchor_hae
        z0, ze = zg + st["min_height"], zg + st["eave"]

        if roof_mode == "texture":   # top-down UV into the aerial crop
            img, uv = tiles.roof_image(*geom.bounds, roof_mpp)

            def roof_uv(x, y, uv=uv):
                return uv(*frame.to_lonlat(x, y))
            roof = Part(f"roof_{b['id']}", "roof_img", image=img, centroid=(cx, cy))
        else:
            roof = Part(f"roof_{b['id']}", "palette", colour=st["roof_colour"], centroid=(cx, cy))
            roof_uv = None

        pattern = st["pattern"]
        wcol = st["wall_colour"]
        if pattern == "plain":
            facade = Part(f"walls_{b['id']}", "palette", colour=wcol, centroid=(cx, cy))
        else:
            q = style.quantize(wcol)
            facade = Part(f"walls_{b['id']}", "facade", colour=q, key=(pattern, q), centroid=(cx, cy))
        upper = Part(f"gable_{b['id']}", "palette", colour=wcol, centroid=(cx, cy))

        for lp in lpolys:
            pieces = _roof_pieces(lp, st)
            _add_roof(roof, pieces, ze, roof_uv)
            if st["min_height"] > 0:
                _add_underside(roof, lp, z0)
            _add_walls(facade, lp, z0, ze)
            _add_gable_walls(upper, lp, pieces, ze)

        posts = None
        if st["posts"] and z0 - zg > 0.5:
            posts = Part(f"posts_{b['id']}", "palette", colour=POST_COLOUR, centroid=(cx, cy))
            _add_posts(posts, largest, zg, z0)

        out.append(Building(b["id"], [p for p in (roof, facade, upper, posts) if p and p.faces],
                            area, st["eave"] + st["roof_height"], (cx, cy)))
        report.append({
            "id": b["id"], "name": b["tags"].get("name"), "class": st["class"],
            "eave_m": round(st["eave"], 1), "roof_height_m": round(st["roof_height"], 1),
            "height_source": st["height_src"], "roof_shape": st["shape"], "roof_shape_source": st["shape_src"],
            "roof_colour": _hex(st["roof_colour"]), "roof_colour_source": st["roof_colour_src"],
            **({"roof_colour_tag": b["tags"].get("roof:colour"), "roof_colour_tag_check": tag_check}
               if tag_check else {}),
            "wall_colour": _hex(st["wall_colour"]), "wall_colour_source": st["wall_colour_src"],
            "facade": pattern, "ground_hae": round(ground_hae, 2),
            **({"open": True} if st["open"] else {}),
            **({"roof_px": list(roof.image.size)} if roof.kind == "roof_img" else {}),
        })
    return out, report
