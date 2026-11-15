"""3D Tiles writer: an LOD tileset of b3dm tiles, root transform ENU->ECEF at the anchor.

glTF is Y-up with no gltfUpAxis (ATAK/TAK-X ignore it), and colour lives only in textures
because ATAK drops COLOR_0. Each level above the leaves keeps only larger/taller buildings.
"""
import io
import json
import math
import struct
import zipfile
from collections import defaultdict
from pathlib import Path

import numpy as np
import pygltflib as gl
from PIL import Image

from . import style
from .geo import enu_to_ecef_matrix
from .mesh import Part

ATLAS_W = 2048          # mobile-safe texture width (aerial roof atlas)
ATLAS_MAX_H = 4096
PAD = 2                 # px of stretched border so LINEAR filtering never samples a neighbour
CELL_PX = 4             # palette cell size
B3DM_HEADER = 28        # bytes: magic, version, byteLength + 4 table lengths

# LOD levels above the leaves: (min footprint m², min height m, geometricError m)
LOD_LEVELS = [(250, 12, 6.0), (2000, 25, 24.0), (8000, 50, 96.0), (30000, 100, 384.0)]
FANOUT = 4
# LOD levels that keep window facades; coarser ones use the solid wall colour to save draw calls.
FACADE_LOD_LEVELS = 2


def _pad(b: bytes, n=4, fill=b"\x00"):
    return b + fill * ((n - len(b) % n) % n)


def _jpeg(img: Image.Image, q: int) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=q, optimize=True)
    return buf.getvalue()


def pack_roofs(parts):
    """Shelf-pack roof images; returns (atlas, {part name: (x, y, w, h)}, layout (W, H)).

    The atlas image is scaled down past ATLAS_MAX_H, so UVs must use the layout size."""
    order = sorted(parts, key=lambda p: -p.image.size[1])
    x = y = shelf_h = 0
    rects = {}
    for p in order:
        w, h = p.image.size
        w = min(w, ATLAS_W - 2 * PAD)
        if x + w + 2 * PAD > ATLAS_W:
            x, y, shelf_h = 0, y + shelf_h, 0
        rects[p.name] = (x + PAD, y + PAD, w, h)
        x += w + 2 * PAD
        shelf_h = max(shelf_h, h + 2 * PAD)
    H = max(4, y + shelf_h)
    atlas = Image.new("RGB", (ATLAS_W, H), (128, 128, 128))
    for p in order:
        rx, ry, w, h = rects[p.name]
        im = p.image.resize((w, h)) if p.image.size != (w, h) else p.image
        atlas.paste(im.resize((w + 2 * PAD, h + 2 * PAD)), (rx - PAD, ry - PAD))
        atlas.paste(im, (rx, ry))
    if H > ATLAS_MAX_H:
        atlas = atlas.resize((max(4, int(ATLAS_W * ATLAS_MAX_H / H)), ATLAS_MAX_H), Image.LANCZOS)
    return atlas, rects, (ATLAS_W, H)


def palette_atlas(colours):
    """Square POT RGBA image with one CELL_PX cell per colour; returns (png, {colour: (u, v)})."""
    cols = sorted(set(colours))
    n = max(1, math.ceil(math.sqrt(len(cols))))
    size = max(8, 1 << math.ceil(math.log2(n * CELL_PX)))
    per_row = size // CELL_PX
    img = Image.new("RGB", (size, size), (128, 128, 128))
    uv = {}
    for i, c in enumerate(cols):
        x, y = (i % per_row) * CELL_PX, (i // per_row) * CELL_PX
        img.paste(c, [x, y, x + CELL_PX, y + CELL_PX])
        uv[c] = ((x + CELL_PX / 2) / size, (y + CELL_PX / 2) / size)
    return style.png_rgba(img), uv


_FACADE_CACHE = {}


def facade_png(key):
    """Cached PNG for a (pattern, colour) facade key."""
    if key not in _FACADE_CACHE:
        _FACADE_CACHE[key] = style.png_rgba(style.facade_image(*key))
    return _FACADE_CACHE[key]


def _lod_part(p: Part, level=1) -> Part:
    """Cheaper copy of a part for LOD level: plain walls past FACADE_LOD_LEVELS, roof photo at 1/2^level."""
    if p.kind == "palette" or (p.kind == "facade" and level <= FACADE_LOD_LEVELS):
        return p
    if p.kind == "roof_img":
        k = 0.5 ** level
        w, h = p.image.size
        img = p.image.resize((max(4, round(w * k)), max(4, round(h * k))), Image.LANCZOS)
        return Part(p.name, "roof_img", image=img, verts=p.verts, uvs=p.uvs, faces=p.faces, centroid=p.centroid)
    return Part(p.name, "palette", colour=p.colour, verts=p.verts, uvs=p.uvs, faces=p.faces, centroid=p.centroid)


def build_glb(parts, roof_q=80) -> bytes:
    """One GLB with one primitive per material: palette, each facade key, roof atlas."""
    blob = bytearray()
    g = gl.GLTF2(asset=gl.Asset(version="2.0", generator="tak-buildings3d"), scene=0)
    # Samplers: palette (NEAREST, exact colours), facade (REPEAT, mips need RGBA on GLES3),
    # roof atlas (no mips: RGB JPEG mips fail on GLES3).
    g.samplers += [
        gl.Sampler(magFilter=gl.NEAREST, minFilter=gl.NEAREST, wrapS=gl.CLAMP_TO_EDGE, wrapT=gl.CLAMP_TO_EDGE),
        gl.Sampler(magFilter=gl.LINEAR, minFilter=gl.LINEAR_MIPMAP_LINEAR, wrapS=gl.REPEAT, wrapT=gl.REPEAT),
        gl.Sampler(magFilter=gl.LINEAR, minFilter=gl.LINEAR, wrapS=gl.CLAMP_TO_EDGE, wrapT=gl.CLAMP_TO_EDGE)]

    def add_view(data: bytes, target=None):
        nonlocal blob
        blob = bytearray(_pad(bytes(blob)))
        g.bufferViews.append(gl.BufferView(buffer=0, byteOffset=len(blob), byteLength=len(data), target=target))
        blob += data
        return len(g.bufferViews) - 1

    def add_material(img_bytes, mime, sampler, unlit=False):
        g.images.append(gl.Image(bufferView=add_view(img_bytes), mimeType=mime))
        g.textures.append(gl.Texture(sampler=sampler, source=len(g.images) - 1))
        m = gl.Material(pbrMetallicRoughness=gl.PbrMetallicRoughness(
            baseColorTexture=gl.TextureInfo(index=len(g.textures) - 1),
            metallicFactor=0.0, roughnessFactor=1.0))
        if unlit:
            m.extensions = {"KHR_materials_unlit": {}}
            g.extensionsUsed = ["KHR_materials_unlit"]
        g.materials.append(m)
        return len(g.materials) - 1

    def add_primitive(ps, material, uv_map=None):
        verts, uvs, faces = [], [], []
        for p in ps:
            base = len(verts)
            verts += p.verts
            uvs += [uv_map(p, q) for q in p.uvs] if uv_map else p.uvs
            faces += [(a + base, b + base, c + base) for a, b, c in p.faces]
        v = np.array([(x, z, -y) for x, y, z in verts], dtype=np.float32)   # ENU -> glTF Y-up
        uv = np.array(uvs, dtype=np.float32)
        f = np.array(faces, dtype=np.uint32)
        tri = v[f]
        fn = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
        n = np.zeros_like(v)
        for k in range(3):
            np.add.at(n, f[:, k], fn)
        n = (n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)).astype(np.float32)
        acc = {}
        for key, arr, typ in (("POSITION", v, "VEC3"), ("NORMAL", n, "VEC3"), ("TEXCOORD_0", uv, "VEC2")):
            kw = dict(min=arr.min(0).tolist(), max=arr.max(0).tolist()) if key == "POSITION" else {}
            g.accessors.append(gl.Accessor(bufferView=add_view(arr.tobytes(), gl.ARRAY_BUFFER),
                                           componentType=gl.FLOAT, count=len(arr), type=typ, **kw))
            acc[key] = len(g.accessors) - 1
        if len(v) < 65536:
            idx, ctype = f.astype(np.uint16), gl.UNSIGNED_SHORT
        else:
            idx, ctype = f, gl.UNSIGNED_INT
        g.accessors.append(gl.Accessor(bufferView=add_view(idx.tobytes(), gl.ELEMENT_ARRAY_BUFFER),
                                       componentType=ctype, count=idx.size, type="SCALAR"))
        return gl.Primitive(attributes=gl.Attributes(**acc), indices=len(g.accessors) - 1, material=material)

    prims = []
    pal = [p for p in parts if p.kind == "palette"]
    if pal:
        png, cell_uv = palette_atlas([p.colour for p in pal])
        prims.append(add_primitive(pal, add_material(png, "image/png", 0),
                                   lambda p, q: cell_uv[p.colour]))
    by_key = defaultdict(list)
    for p in parts:
        if p.kind == "facade":
            by_key[p.key].append(p)
    for key in sorted(by_key):
        prims.append(add_primitive(by_key[key], add_material(facade_png(key), "image/png", 1)))
    imgs = [p for p in parts if p.kind == "roof_img"]
    if imgs:
        atlas, rects, (W, H) = pack_roofs(imgs)

        def remap(p, q):
            rx, ry, w, h = rects[p.name]
            return ((rx + q[0] * w) / W, (ry + q[1] * h) / H)
        prims.append(add_primitive(imgs, add_material(_jpeg(atlas, roof_q), "image/jpeg", 2, unlit=True), remap))

    g.meshes.append(gl.Mesh(primitives=prims))
    g.nodes.append(gl.Node(mesh=0))
    g.scenes.append(gl.Scene(nodes=[0]))
    blob = _pad(bytes(blob))
    g.buffers.append(gl.Buffer(byteLength=len(blob)))
    g.set_binary_blob(blob)
    return b"".join(g.save_to_bytes())


def b3dm(glb: bytes) -> bytes:
    """Wrap a GLB in a b3dm with an empty batch table (no per-building metadata)."""
    ft = json.dumps({"BATCH_LENGTH": 0}).encode()
    while (B3DM_HEADER + len(ft)) % 8:      # the GLB must start 8-byte aligned
        ft += b" "
    glb = _pad(glb, 8)
    total = B3DM_HEADER + len(ft) + len(glb)
    return struct.pack("<4sIIIIII", b"b3dm", 1, total, len(ft), 0, 0, 0) + ft + glb


def _bounds(buildings):
    lo = np.full(3, np.inf)
    hi = np.full(3, -np.inf)
    for b in buildings:
        for p in b.parts:
            v = np.asarray(p.verts)
            lo, hi = np.minimum(lo, v.min(0)), np.maximum(hi, v.max(0))
    return lo, hi


def _box(lo, hi):
    """3D Tiles `box` bounding volume, at least 0.5 m thick on each axis."""
    c, h = (lo + hi) / 2, np.maximum((hi - lo) / 2, 0.5)
    return [*c.tolist(), h[0], 0, 0, 0, h[1], 0, 0, 0, h[2]]


def write_3dtiles(buildings, out_dir: Path, anchor, cell=150.0, roof_q=80):
    """Write tileset.json and b3dm files; anchor is (lat, lon, hae). Returns [(name, size, level)]."""
    out_dir.mkdir(parents=True, exist_ok=True)
    stats = []

    def write(name, parts, level):
        data = b3dm(build_glb(parts, roof_q))
        (out_dir / name).write_bytes(data)
        stats.append((name, len(data), level))
        return {"uri": name}

    nodes = {}
    groups = defaultdict(list)
    x0 = min(b.centroid[0] for b in buildings)
    y0 = min(b.centroid[1] for b in buildings)
    for b in buildings:   # non-negative indices so // FANOUT always converges to one root
        groups[(int((b.centroid[0] - x0) // cell), int((b.centroid[1] - y0) // cell))].append(b)
    for (i, j), bs in groups.items():
        lo, hi = _bounds(bs)
        nodes[(i, j)] = {"tile": {"boundingVolume": {"box": _box(lo, hi)}, "geometricError": 0,
                                  "content": write(f"tile_0_{i}_{j}.b3dm", [p for b in bs for p in b.parts], 0)},
                         "lo": lo, "hi": hi, "blds": bs}

    level = 0
    while len(nodes) > 1 or level == 0:
        level += 1
        min_area, min_h, gerr = LOD_LEVELS[min(level - 1, len(LOD_LEVELS) - 1)]
        gerr *= FANOUT ** max(0, level - len(LOD_LEVELS))
        parents = defaultdict(list)
        for (i, j), n in nodes.items():
            parents[(i // FANOUT, j // FANOUT)].append(n)
        new = {}
        for (i, j), kids in parents.items():
            lo = np.min([k["lo"] for k in kids], axis=0)
            hi = np.max([k["hi"] for k in kids], axis=0)
            blds = [b for k in kids for b in k["blds"] if b.area >= min_area or b.height >= min_h]
            tile = {"boundingVolume": {"box": _box(lo, hi)}, "geometricError": gerr, "refine": "REPLACE",
                    "children": [k["tile"] for k in kids]}
            if blds:
                tile["content"] = write(f"tile_{level}_{i}_{j}.b3dm",
                                        [_lod_part(p, level) for b in blds for p in b.parts], level)
            new[(i, j)] = {"tile": tile, "lo": lo, "hi": hi, "blds": blds}
        nodes = new

    root = next(iter(nodes.values()))["tile"]
    root["transform"] = enu_to_ecef_matrix(*anchor)
    tileset = {"asset": {"version": "1.0"}, "geometricError": root["geometricError"] * FANOUT, "root": root}
    (out_dir / "tileset.json").write_text(json.dumps(tileset, separators=(",", ":")))
    return stats


def zip_tileset(src: Path, zip_path: Path):
    """tileset.json at the zip root (ATAK's zip import searches only one level deep)."""
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as z:
        for f in sorted(src.iterdir()):
            if f.is_file():
                z.write(f, f.name)
