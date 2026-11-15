"""Roof surfaces as the lower envelope of planes, clipped to the footprint.

Each shape is a concave height field f = min_i plane_i over the footprint's OBB, so a roof face
is footprint ∩ {plane_i <= plane_j for all j} for any footprint, and walls simply rise to f.
"""
import math

import numpy as np
from shapely import affinity
from shapely.geometry import Polygon, box

# A plane is (a, b, c): height above the eave = a*x + b*y + c (local metres).
MIN_FACE_AREA = 1e-4    # m²; smaller slivers are clipping noise


def obb(poly):
    """(centre, long_axis_unit, short_axis_unit, half_long, half_short) of the min-area rect."""
    r = poly.minimum_rotated_rectangle
    pts = np.array(r.exterior.coords)[:4]
    e0, e1 = pts[1] - pts[0], pts[2] - pts[1]
    l0, l1 = np.linalg.norm(e0), np.linalg.norm(e1)
    c = pts.mean(0)
    if l0 < 1e-6 or l1 < 1e-6:
        return c, np.array([1.0, 0.0]), np.array([0.0, 1.0]), max(l0, l1) / 2 + 0.5, 0.5
    if l0 >= l1:
        a, L, W = e0 / l0, l0 / 2, l1 / 2
    else:
        a, L, W = e1 / l1, l1 / 2, l0 / 2
    return c, a, np.array([-a[1], a[0]]), L, W


def _plane(c, axis, k0, k):
    """Plane h = k0 + k * ((p - c) . axis) expressed as (a, b, c)."""
    return (k * axis[0], k * axis[1], k0 - k * float(np.dot(c, axis)))


def planes_for(shape, poly, h, direction=None, across=False):
    """Planes whose lower envelope is a `shape` roof of height h over poly's OBB.

    direction: roof:direction bearing (skillion only); across: ridge along the short axis."""
    if shape == "flat" or h <= 0.05:
        return []
    c, a, b, L, W = obb(poly)
    if across:
        a, b, L, W = b, a, W, L
    if shape == "gabled":
        return [_plane(c, b, h, -h / W), _plane(c, b, h, h / W)]
    if shape == "hipped":
        s = h / W
        return [_plane(c, b, h, -s), _plane(c, b, h, s), _plane(c, a, s * L, -s), _plane(c, a, s * L, s)]
    if shape in ("gambrel", "mansard", "half-hipped", "saltbox"):
        def side(k0, k):    # rises from the long eaves at slope k (m/m), starting at height k0
            return [_plane(c, b, k0 + k * W, -k), _plane(c, b, k0 + k * W, k)]

        def end(k0, k):     # the same from the two short ends
            return [_plane(c, a, k0 + k * L, -k), _plane(c, a, k0 + k * L, k)]

        if shape == "saltbox":       # ridge 40% of the half-width off-centre
            return [_plane(c, b, h / 1.4, h / (1.4 * W)), _plane(c, b, h / 0.6, -h / (0.6 * W))]
        if shape == "half-hipped":   # gable walls rise to 60% of the roof height, then small hips
            return side(0.0, h / W) + end(0.6 * h, 0.8 * h / W)
        # kink at 40% of the half-width in from the eave, at 70% of the roof height
        out = side(0.0, 1.75 * h / W) + side(0.5 * h, 0.5 * h / W)
        if shape == "mansard":
            out += end(0.0, 1.75 * h / W) + end(0.5 * h, 0.5 * h / W)
        return out
    if shape == "pyramidal":
        return [_plane(c, b, h, -h / W), _plane(c, b, h, h / W),
                _plane(c, a, h, -h / L), _plane(c, a, h, h / L)]
    if shape == "skillion":
        if direction is not None:   # roof:direction = compass bearing the slope faces (downhill)
            d = np.array([math.sin(math.radians(direction)), math.cos(math.radians(direction))])
        else:
            d = b
        s = np.array(poly.exterior.coords) @ d
        lo, hi = float(s.min()), float(s.max())
        span = max(hi - lo, 0.5)
        return [(-h / span * d[0], -h / span * d[1], h * hi / span)]
    if shape == "round":            # elliptic cylinder along a, semi-axes W (across), h (up)
        out = []
        for t in np.linspace(-0.85, 0.85, 8) * (math.pi / 2):
            # tangent at v = W sin t, z = h cos t : z = h/cos t - (h/W) tan t * v
            out.append(_plane(c, b, h / math.cos(t), -(h / W) * math.tan(t)))
        return out
    if shape == "dome":             # ellipsoid just enclosing the footprint (round footprint: base at eave)
        pts = np.array(poly.exterior.coords) - c
        k = float(np.sqrt((pts @ a / L) ** 2 + (pts @ b / W) ** 2).max())
        A, B = L * k, W * k
        # 17 planes: each extra plane adds wall breaks on every edge.
        out = [(0.0, 0.0, h)]
        for phi in (0.55, 1.1):                    # polar angle of the tangent point
            for i in range(8):                     # 8 azimuths, the outer ring offset by half a step
                th = 2 * math.pi * (i + 0.5 * (phi > 1)) / 8
                # tangent plane at (A sinφ cosθ, B sinφ sinθ, h cosφ) on x²/A²+y²/B²+z²/h²=1
                nx, ny, nz = math.sin(phi) * math.cos(th) / A, math.sin(phi) * math.sin(th) / B, math.cos(phi) / h
                # nx*u + ny*v + nz*z = 1  ->  z = (1 - nx*u - ny*v) / nz
                p1 = _plane(c, a, 0.0, -nx / nz)
                p2 = _plane(c, b, 0.0, -ny / nz)
                out.append((p1[0] + p2[0], p1[1] + p2[1], p1[2] + p2[2] + 1.0 / nz))
        return out
    return []


def height(planes, x, y):
    """Roof height above the eave at (x, y): the lowest plane, never below the eave."""
    if not planes:
        return 0.0
    return max(0.0, min(a * x + b * y + c for a, b, c in planes))


def _halfplane(a, b, c, bounds):
    """Polygon of {a x + b y + c <= 0} within an enlarged bounds box."""
    x0, y0, x1, y1 = bounds
    pad = max(x1 - x0, y1 - y0) + 10
    big = box(x0 - pad, y0 - pad, x1 + pad, y1 + pad)
    n = math.hypot(a, b)
    if n < 1e-12:
        return big if c <= 0 else Polygon()
    # Anchor on the bounds centre: the footprint can be kilometres from the frame origin.
    m = np.array([(x0 + x1) / 2, (y0 + y1) / 2])
    p = m - (a * m[0] + b * m[1] + c) / (n * n) * np.array([a, b])
    d = np.array([-b, a]) / n
    inward = -np.array([a, b]) / n
    R = 4 * pad + float(np.linalg.norm(p - m))
    q = [p - d * R, p + d * R, p + d * R + inward * R, p - d * R + inward * R]
    return big.intersection(Polygon(q))


def faces(planes, poly):
    """[(plane, region polygon)] partitioning poly by which plane is lowest."""
    if len(planes) <= 1:
        return [(planes[0] if planes else (0.0, 0.0, 0.0), poly)]
    out = []
    for i, pi in enumerate(planes):
        region = poly
        for j, pj in enumerate(planes):
            if i == j:
                continue
            # pi <= pj  <=>  (pi - pj) <= 0
            hp = _halfplane(pi[0] - pj[0], pi[1] - pj[1], pi[2] - pj[2], poly.bounds)
            region = region.intersection(hp)
            if region.is_empty or region.area < MIN_FACE_AREA:
                break
        if not region.is_empty and region.area >= MIN_FACE_AREA:
            out.append((pi, region))
    return out


def edge_breaks(planes, p, q):
    """Parameters t in [0,1] along p->q where the lowest plane changes (plus ends)."""
    ts = {0.0, 1.0}
    for i in range(len(planes)):
        for j in range(i + 1, len(planes)):
            da = [planes[i][k] - planes[j][k] for k in range(3)]
            fp = da[0] * p[0] + da[1] * p[1] + da[2]
            fq = da[0] * q[0] + da[1] * q[1] + da[2]
            if (fp < 0) != (fq < 0) and abs(fp - fq) > 1e-12:
                ts.add(fp / (fp - fq))
    return sorted(t for t in ts if -1e-9 <= t <= 1 + 1e-9)


def rectangularity(poly):
    """Footprint area / its min-area rectangle's area (1.0 for a rectangle)."""
    r = poly.minimum_rotated_rectangle
    return poly.area / r.area if r.area > 0 else 1.0


def decompose(poly, max_rects=4, min_cover=0.95):
    """Cover an L/T/U footprint with OBB-aligned rectangles, one per wing; [] if it doesn't decompose."""
    c, a, _, _, _ = obb(poly)
    ang = math.degrees(math.atan2(a[1], a[0]))
    rp = affinity.rotate(poly, -ang, origin=tuple(c)).simplify(0.3)
    if rp.is_empty or not isinstance(rp, Polygon):
        return []
    xs = sorted({round(x, 1) for x, _ in rp.exterior.coords})
    ys = sorted({round(y, 1) for _, y in rp.exterior.coords})
    if len(xs) * len(ys) > 400:     # too ragged to be a few wings
        return []
    nx, ny = len(xs) - 1, len(ys) - 1
    inside = np.zeros((nx, ny), bool)
    area = np.zeros((nx, ny))
    for i in range(nx):
        for j in range(ny):
            cell = box(xs[i], ys[j], xs[i + 1], ys[j + 1])
            area[i, j] = cell.area
            inside[i, j] = cell.area > 0 and rp.intersection(cell).area >= 0.6 * cell.area
    covered = np.zeros_like(inside)
    total = area[inside].sum()
    rects = []
    # Greedy: take the rectangle of inside cells that covers the most uncovered area.
    while len(rects) < max_rects and total > 0 and area[covered & inside].sum() < min_cover * total:
        best, gain_best = None, 0.0
        for i0 in range(nx):
            for j0 in range(ny):
                if not inside[i0, j0]:
                    continue
                jmax = ny
                for i1 in range(i0, nx):
                    if not inside[i1, j0]:
                        break
                    j1 = j0
                    while j1 + 1 < jmax and inside[i1, j1 + 1]:
                        j1 += 1
                    jmax = j1 + 1
                    gain = area[i0:i1 + 1, j0:jmax][~covered[i0:i1 + 1, j0:jmax]].sum()
                    if gain > gain_best:
                        best, gain_best = (i0, j0, i1, jmax - 1), gain
        if best is None or gain_best < 4.0:     # m²; less is noise, not a wing
            break
        i0, j0, i1, j1 = best
        covered[i0:i1 + 1, j0:j1 + 1] = True
        w, h = xs[i1 + 1] - xs[i0], ys[j1 + 1] - ys[j0]
        if min(w, h) >= 2.0:                     # thinner strips get no roof of their own
            rects.append(box(xs[i0], ys[j0], xs[i1 + 1], ys[j1 + 1]))
    if not rects:
        return []
    return [affinity.rotate(r, ang, origin=tuple(c)) for r in rects]
