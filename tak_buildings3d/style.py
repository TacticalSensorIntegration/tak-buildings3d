"""Building styling from OSM tags: heights, roof shape, colours and facade pattern.

Fallbacks are deterministic per building id. Colours are baked into textures because ATAK ignores COLOR_0.
"""
import colorsys
import io
import math
import re
import zlib

from PIL import Image, ImageColor, ImageDraw

LEVEL_HEIGHT = 3.3                    # m per storey
FACADE_STOREY, FACADE_BAY = 3.3, 3.0  # m per facade texture tile


def num(v):
    """Leading number of an OSM value ("12 m" -> 12.0, "3;4" -> 3.0), or None."""
    if v is None:
        return None
    m = re.match(r"\s*(-?[0-9]*\.?[0-9]+)", str(v))
    return float(m.group(1)) if m else None


def _hash(bid: str) -> int:
    """Stable per-building hash, identical across rebuilds."""
    return zlib.crc32(bid.encode())


def _pick(lst, h, shift=0):
    """Deterministic choice from lst; each shift uses different hash bits."""
    return lst[(h >> shift) % len(lst)]


def _mix(a, b, t):
    return tuple(int(round(x * (1 - t) + y * t)) for x, y in zip(a, b))


_EXTRA_COLOURS = {
    "cream": (238, 230, 205), "brick": (160, 82, 60), "terracotta": (180, 90, 60),
    "sandstone": (205, 180, 135), "light_grey": (200, 200, 200), "light_gray": (200, 200, 200),
    "dark_grey": (80, 80, 80), "dark_gray": (80, 80, 80), "lightgrey": (200, 200, 200),
    "darkgrey": (80, 80, 80), "light_brown": (170, 130, 95), "dark_brown": (90, 60, 40),
    "dark_red": (130, 40, 35), "light_yellow": (240, 230, 170), "light_blue": (170, 200, 220),
}

# Named colours depend on the surface: a "red" roof is terracotta tile, "white" walls are off-white.
ROOF_NAMED = {
    "red": (165, 82, 62), "darkred": (125, 52, 42), "brown": (112, 80, 62), "darkbrown": (82, 60, 48),
    "orange": (190, 112, 70), "terracotta": (178, 95, 65), "grey": (128, 130, 130),
    "lightgrey": (178, 180, 180), "darkgrey": (78, 80, 82), "black": (52, 54, 56),
    "white": (222, 224, 222), "silver": (190, 192, 194), "green": (82, 112, 84),
    "darkgreen": (60, 85, 64), "blue": (72, 92, 120), "darkblue": (52, 64, 88),
    "yellow": (200, 180, 110), "beige": (200, 188, 160), "cream": (215, 205, 175),
}
WALL_NAMED = {
    "white": (236, 233, 226), "beige": (222, 206, 176), "cream": (236, 226, 198),
    "lightyellow": (240, 231, 188), "yellow": (226, 202, 128), "orange": (215, 145, 90),
    "red": (172, 82, 64), "darkred": (130, 55, 45), "brown": (140, 102, 76), "darkbrown": (95, 70, 52),
    "pink": (226, 178, 168), "grey": (170, 170, 168), "lightgrey": (205, 205, 202),
    "darkgrey": (95, 96, 98), "black": (62, 62, 64), "green": (132, 160, 122),
    "blue": (122, 150, 180), "lightblue": (172, 198, 218), "silver": (192, 194, 196),
}


def _key(s):
    """Normalise a colour name for ROOF_NAMED / WALL_NAMED ("Light Gray" -> "lightgrey")."""
    return re.sub(r"[\s_-]", "", s.lower()).replace("gray", "grey")


def parse_colour(s, named=None):
    """RGB for an OSM colour value (name or hex), preferring the surface-specific `named` table."""
    if not s:
        return None
    if named and _key(s) in named:
        return named[_key(s)]
    s = s.strip().lower().replace(" ", "_")
    if s in _EXTRA_COLOURS:
        return _EXTRA_COLOURS[s]
    try:
        return ImageColor.getrgb(s.replace("_", ""))[:3]
    except ValueError:
        return None


HOUSE = {"house", "detached", "semidetached_house", "semi", "bungalow", "terrace", "residential",
         "villa", "cabin", "farm", "farmhouse", "static_caravan", "hut"}
APARTMENT = {"apartments", "dormitory", "hotel", "flats"}
OFFICE = {"office", "commercial", "retail", "university", "college", "school", "hospital", "civic",
          "public", "government", "kindergarten", "train_station", "transportation", "townhall",
          "supermarket", "mall", "bank", "library", "museum", "fire_station", "police"}
INDUSTRIAL = {"industrial", "warehouse", "factory", "hangar", "manufacture", "storage_tank",
              "barn", "farm_auxiliary", "greenhouse", "stable", "cowshed", "sports_hall", "riding_hall"}
SMALL = {"garage", "garages", "shed", "carport", "roof", "service", "kiosk", "toilets",
         "outbuilding", "hut", "container", "bunker", "gatehouse", "transformer_tower", "houseboat"}
WORSHIP = {"church", "cathedral", "chapel", "mosque", "temple", "synagogue", "shrine", "religious"}

# Per-type defaults where tags are silent. storey: metres per level; open: roof on posts, no walls.
TYPE_DEFAULTS = {
    "carport":    {"storey": 2.4, "open": True, "wall": ["metal"], "roof": ["metal", "zincalume"]},
    "roof":       {"storey": 3.0, "open": True, "wall": ["metal", "concrete"], "roof": ["metal", "flat_light"]},
    "garage":     {"levels": 1, "storey": 2.7, "facade": "plain", "wall": ["brick", "concrete", "metal"]},
    "garages":    {"levels": 1, "storey": 2.7, "facade": "plain", "wall": ["brick", "concrete"]},
    "shed":       {"levels": 1, "storey": 2.4, "shape": "gabled", "pitch": 15, "facade": "plain",
                   "wall": ["metal", "wood"], "roof": ["metal", "zincalume"]},
    "greenhouse": {"levels": 1, "storey": 3.0, "shape": "gabled", "pitch": 25, "facade": "glass",
                   "wall": ["glass"], "roof": ["glass"]},
    "houseboat":  {"levels": 1, "storey": 2.6, "shape": "flat", "facade": "house", "wall": ["wood", "plaster"]},
    "hangar":     {"levels": 1, "storey": 9.0, "facade": "industrial"},
    "parking":    {"levels": 4, "storey": 3.0, "facade": "parking", "wall": ["concrete"], "roof": ["concrete"]},
    **{t: {"facade": "plain"} for t in ("church", "cathedral", "chapel", "mosque", "temple", "synagogue")},
}


def type_defaults(tags):
    """(building type, TYPE_DEFAULTS entry); building:part wins unless it is just "yes"."""
    t = (tags.get("building:part") or "").lower()
    if t in ("", "yes"):
        t = (tags.get("building") or "").lower()
    if t == "parking" or tags.get("parking") == "multi-storey" or (
            tags.get("amenity") == "parking" and t not in TYPE_DEFAULTS):
        return "parking", TYPE_DEFAULTS["parking"]
    return t, TYPE_DEFAULTS.get(t, {})


def building_class(tags, area):
    """Building class from the building tag, else guessed from footprint area (m²) and levels."""
    t = (tags.get("building") or tags.get("building:part") or "yes").lower()
    for cls, types in (("house", HOUSE), ("apartment", APARTMENT), ("office", OFFICE),
                       ("industrial", INDUSTRIAL), ("small", SMALL), ("worship", WORSHIP)):
        if t in types:
            return cls
    if t == "parking" or tags.get("parking") == "multi-storey":
        return "office"
    lv = num(tags.get("building:levels"))
    if area < 40:
        return "small"
    if area < 300 and (lv is None or lv <= 2):
        return "house"
    if lv is not None and lv >= 4:
        return "apartment" if area < 1500 else "office"
    return "industrial" if area > 3000 else "office"


# Wall and roof palettes by material (sRGB 0-255).
WALL_MATERIAL = {
    "brick": [(150, 100, 82), (165, 118, 95), (135, 95, 80), (180, 145, 120), (120, 88, 75)],
    "concrete": [(198, 195, 188), (178, 176, 172), (210, 204, 190), (160, 158, 155)],
    "plaster": [(232, 225, 205), (225, 215, 190), (240, 236, 226), (215, 200, 175), (230, 210, 185)],
    "glass": [(105, 130, 150), (90, 115, 135), (120, 140, 150)],
    "wood": [(150, 112, 75), (125, 95, 65), (170, 135, 95)],
    "metal": [(180, 184, 188), (150, 156, 162), (200, 202, 200), (120, 128, 135)],
    "stone": [(190, 178, 155), (170, 160, 145), (205, 190, 160)],
    "sandstone": [(208, 180, 135), (195, 165, 120)],
}
_WALL_ALIAS = {"cement_block": "concrete", "reinforced_concrete": "concrete", "render": "plaster",
               "timber_framing": "wood", "timber": "wood", "steel": "metal", "metal_plates": "metal",
               "mirror": "glass", "limestone": "stone", "granite": "stone", "masonry": "brick",
               "bricks": "brick", "adobe": "plaster", "loam": "plaster", "clay": "brick"}
ROOF_MATERIAL = {
    "roof_tiles": [(160, 108, 96), (150, 110, 100), (125, 92, 84), (100, 88, 84)],
    "metal": [(70, 74, 78), (205, 208, 205), (110, 115, 112), (150, 155, 155), (90, 95, 90)],
    "concrete": [(170, 170, 165), (155, 155, 150)],
    "tar_paper": [(75, 75, 75), (90, 90, 88)],
    "slate": [(75, 80, 88), (65, 68, 75)],
    "glass": [(140, 170, 190)],
    "grass": [(95, 125, 65)],
    "copper": [(95, 150, 130)],
    "wood": [(110, 88, 65)],
    "gravel": [(160, 155, 145), (140, 135, 128)],
    "stone": [(150, 145, 135)],
    # Defaults, not OSM values: large flat roofs seen from above are mostly light.
    "flat_light": [(222, 224, 224), (205, 207, 205), (190, 190, 186), (175, 176, 172), (212, 212, 205),
                   (160, 160, 156)],
    "zincalume": [(195, 199, 200), (180, 185, 187), (210, 212, 212)],
}
_ROOF_ALIAS = {"tile": "roof_tiles", "tiles": "roof_tiles", "clay_tiles": "roof_tiles",
               "concrete_tiles": "roof_tiles", "metal_sheet": "metal", "tin": "metal", "steel": "metal",
               "zinc": "metal", "aluminium": "metal", "asphalt": "tar_paper", "bitumen": "tar_paper",
               "asphalt_shingle": "tar_paper", "shingle": "tar_paper", "membrane": "tar_paper",
               "eternit": "concrete", "plants": "grass", "thatch": "wood", "wood_shingle": "wood"}

# Fallbacks when no material/colour is tagged, by building class.
DEFAULT_WALL_MAT = {"house": ["brick", "brick", "plaster", "plaster", "wood"],
                    "apartment": ["plaster", "concrete", "brick"],
                    "office": ["concrete", "plaster", "concrete", "glass"],
                    "industrial": ["metal"], "small": ["metal", "brick", "wood"],
                    "worship": ["stone", "brick", "plaster"]}
DEFAULT_ROOF_MAT = {"house": ["roof_tiles", "metal", "metal", "roof_tiles"],
                    "apartment": ["flat_light"], "office": ["flat_light"],
                    "industrial": ["zincalume"], "small": ["metal", "zincalume"],
                    "worship": ["roof_tiles", "slate", "metal"]}


def colour_family(c):
    """Coarse colour family, for checking a roof:colour tag against imagery."""
    r, g, b = (v / 255 for v in c[:3])
    mx, mn = max(r, g, b), min(r, g, b)
    sat = (mx - mn) / mx if mx else 0.0
    if mx < 0.25:
        return "dark"
    if sat < 0.18:
        return "light" if mx > 0.7 else "grey" if mx > 0.4 else "dark"
    hue = colorsys.rgb_to_hsv(r, g, b)[0] * 360
    return "red" if hue < 45 or hue >= 300 else "yellow" if hue < 70 else "green" if hue < 170 else "blue"


_NEAR = {frozenset(p) for p in (("light", "grey"), ("grey", "dark"), ("red", "yellow"))}


def colours_agree(a, b):
    """Same or neighbouring colour family (white~grey, grey~charcoal, terracotta~beige)."""
    fa, fb = colour_family(a), colour_family(b)
    return fa == fb or frozenset((fa, fb)) in _NEAR


def weather(c, amount=0.35, lift=12):
    """Desaturate and lighten a palette colour; real roofs and walls look sun-faded from above."""
    y = 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]
    return tuple(int(min(255, round(v * (1 - amount) + y * amount + lift))) for v in c)


def _surface_colour(colour_tag, named, material_tag, alias, palettes, fallback_mats, h, shift):
    """(colour, source) from the colour tag, else the tagged material, else a default material."""
    c = parse_colour(colour_tag, named)
    if c:
        return c, "tag"
    mat = (material_tag or "").lower()
    mat = alias.get(mat, mat)
    src = "material"
    if mat not in palettes:
        mat, src = _pick(fallback_mats, h, shift), "default"
    return weather(_pick(palettes[mat], h, shift + 4)), src


def wall_colour(tags, cls, h, mats=None):
    return _surface_colour(tags.get("building:colour") or tags.get("building:facade:colour"), WALL_NAMED,
                           tags.get("building:material") or tags.get("building:facade:material"),
                           _WALL_ALIAS, WALL_MATERIAL, mats or DEFAULT_WALL_MAT[cls], h, 0)


def roof_colour(tags, cls, h, mats=None):
    return _surface_colour(tags.get("roof:colour"), ROOF_NAMED, tags.get("roof:material"),
                           _ROOF_ALIAS, ROOF_MATERIAL, mats or DEFAULT_ROOF_MAT[cls], h, 8)


def facade_pattern(tags, cls, levels, wall_mat_tag, default=None):
    """One of plain, house, apartment, office, glass, industrial, parking."""
    mat = _WALL_ALIAS.get(wall_mat_tag, wall_mat_tag)
    if tags.get("building:part") == "roof" or tags.get("building") in ("roof", "carport"):
        return "plain"
    if mat == "glass":
        return "glass"
    if default:
        return default
    if cls == "small":
        return "plain"
    if cls in ("house", "apartment", "industrial"):
        return cls
    if cls == "office":                 # untagged towers read as curtain wall
        return "glass" if levels >= 8 and mat in ("", "glass") else "office"
    return "office"


SHAPES = {"flat", "gabled", "hipped", "pyramidal", "skillion", "dome", "round",
          "gambrel", "mansard", "half-hipped", "saltbox"}
# Unmodelled shapes mapped to the closest modelled one. Saltbox variants can't be a lower
# envelope of planes, so they fall back to a plain gable or hip.
SHAPE_ALIAS = {"gable": "gabled", "hip": "hipped", "side_hipped": "hipped", "side_half-hipped": "half-hipped",
               "double_saltbox": "gabled", "quadruple_saltbox": "hipped", "hipped-and-gabled": "hipped",
               "pitched": "gabled", "crosspitched": "hipped", "onion": "dome", "cone": "pyramidal",
               "lean_to": "skillion", "monopitch": "skillion", "pyramid": "pyramidal", "barrel": "round",
               "half_hipped": "half-hipped", "mansard_roof": "mansard"}
DEFAULT_PITCH = {"gabled": 30, "hipped": 25, "pyramidal": 25, "skillion": 10, "half-hipped": 35, "saltbox": 30}
# Gambrel and mansard: roof height as a fraction of the half-width, and a cap (one tall storey).
KINKED = {"gambrel": (0.9, 6.0), "mansard": (0.7, 5.0)}
ROOF_LEVEL_FRACTION = 0.8   # a roof:levels storey is under the slope, so lower
MAX_PITCH = 60              # degrees; steeper tags are usually typos and make spikes
MAX_ROOF_HEIGHT, MAX_HOUSE_ROOF_HEIGHT = 15.0, 6.0   # caps on derived roof heights
DEFAULT_LEVELS = {"house": 1, "small": 1, "industrial": 2}   # when untagged; others get 2
SMALL_STOREY = 2.8


def roof_shape(tags, cls, area, default=None):
    """(shape, source): the tagged roof:shape mapped onto SHAPES, else a type/class default."""
    s = (tags.get("roof:shape") or "").strip().lower()
    if s:
        s = SHAPE_ALIAS.get(s, s)
        if s == "many":                 # "complex": hipped reads right on a house, flat on a block
            s = "hipped" if area < 400 else "flat"
        return (s if s in SHAPES else "flat"), "tag"
    if default:
        return default, "type"
    if cls == "house":
        return "hipped", "default"      # most AU/NZ/US suburban houses are hipped
    if cls == "worship" and area < 800:
        return "gabled", "default"
    return "flat", "default"


def _roof_height(tags, shape, cls, short_half_width, pitch=None):
    """(roof height above the eave, source) from roof:height, roof:levels, or pitch over half-width."""
    rh = num(tags.get("roof:height"))
    if rh is not None or shape == "flat":
        return rh or 0.0, "roof:height"
    if num(tags.get("roof:levels")):
        return num(tags.get("roof:levels")) * LEVEL_HEIGHT * ROOF_LEVEL_FRACTION, "roof:levels"
    if shape in ("dome", "round"):
        rh, src = short_half_width, "shape"
    elif shape in KINKED:
        frac, cap = KINKED[shape]
        rh, src = min(frac * short_half_width, cap), "shape"
    else:
        ang = num(tags.get("roof:angle")) or pitch or DEFAULT_PITCH.get(shape, 25)
        span = 2 * short_half_width if shape == "skillion" else short_half_width
        rh, src = math.tan(math.radians(min(ang, MAX_PITCH))) * span, "angle"
    return min(rh, MAX_HOUSE_ROOF_HEIGHT if cls == "house" else MAX_ROOF_HEIGHT), src


def style(b, area, short_half_width):
    """Style dict for building b; area in m², short_half_width is half the OBB short side in m."""
    tags, h = b["tags"], _hash(b["id"])
    cls = building_class(tags, area)
    btype, d = type_defaults(tags)
    is_open = bool(d.get("open")) and tags.get("building:part") in (None, "", "yes", "roof", "carport")
    shape, shape_src = roof_shape(tags, cls, area, d.get("shape"))
    rh, rh_src = _roof_height(tags, shape, cls, short_half_width, d.get("pitch"))

    total = num(tags.get("height"))
    levels = num(tags.get("building:levels"))
    if total is not None:
        rh = min(rh, 0.6 * total)
        eave, h_src = max(total - rh, 1.0), "height"
    else:
        if levels is None:
            levels = d.get("levels") or DEFAULT_LEVELS.get(cls, 2)
            h_src = "type" if "levels" in d or is_open else "default"
        else:
            h_src = "levels"
        storey = d.get("storey") or (SMALL_STOREY if cls == "small" else LEVEL_HEIGHT)
        eave = storey if is_open else max(levels, 0.8) * storey
        if shape == "flat":
            eave += (num(tags.get("roof:levels")) or 0) * LEVEL_HEIGHT
    nlev = levels if levels is not None else eave / LEVEL_HEIGHT

    min_h = num(tags.get("min_height"))
    if min_h is None and num(tags.get("building:min_level")) is not None:
        min_h = num(tags.get("building:min_level")) * LEVEL_HEIGHT
    posts = False
    if is_open and min_h is None:       # roof on posts: a thin slab at the eave, no walls
        min_h, posts = max(eave - 0.3, 0.0), True
    elif is_open:
        posts = min_h > 0.5
    min_h = min(min_h or 0.0, eave - (0.1 if is_open else 0.5))

    wc, wc_src = wall_colour(tags, cls, h, d.get("wall"))
    rc, rc_src = roof_colour(tags, cls, h, d.get("roof"))
    if is_open and wc_src == "default":
        wc = _mix(rc, (0, 0, 0), 0.15)  # slab edge
    wall_mat = (tags.get("building:material") or "").lower()
    return {
        "class": cls, "shape": shape, "shape_src": shape_src,
        "roof_height": rh, "roof_height_src": rh_src,
        "eave": eave, "min_height": max(min_h, 0.0), "height_src": h_src,
        "wall_colour": wc, "wall_colour_src": wc_src,
        "roof_colour": rc, "roof_colour_src": rc_src,
        "pattern": facade_pattern(tags, cls, nlev, wall_mat, d.get("facade")),
        "type": btype, "open": is_open, "posts": posts,
        "direction": num(tags.get("roof:direction")),
        "across": (tags.get("roof:orientation") or "").lower() == "across",
    }


GLASS = (52, 64, 78)
TEX = 32  # px per storey x bay, ~10 cm/px


def facade_image(pattern, colour) -> Image.Image:
    """One storey x one bay, v=0 at the top of the storey (glTF UV origin top-left)."""
    w = h = TEX
    img = Image.new("RGB", (w, h), colour)
    d = ImageDraw.Draw(img)
    dark = _mix(colour, (0, 0, 0), 0.18)
    glass = _mix(GLASS, colour, 0.12)
    if pattern == "house":
        d.rectangle([9, 8, 22, 21], fill=glass)
        d.line([9, 22, 22, 22], fill=_mix(colour, (255, 255, 255), 0.4))
        d.line([15, 8, 15, 21], fill=_mix(glass, (255, 255, 255), 0.25))
    elif pattern == "apartment":
        d.rectangle([7, 7, 24, 22], fill=glass)
        d.rectangle([0, 28, w, h], fill=dark)                 # balcony line
    elif pattern == "office":
        d.rectangle([0, 6, w, 22], fill=glass)
        for x in (0, 15, 31):
            d.line([x, 6, x, 22], fill=dark)
    elif pattern == "glass":
        img.paste(_mix(GLASS, colour, 0.35), [0, 0, w, h])
        d.line([0, 0, w, 0], fill=colour, width=2)
        d.line([0, 0, 0, h], fill=colour)
    elif pattern == "parking":
        d.rectangle([0, 9, w, 25], fill=_mix(colour, (0, 0, 0), 0.62))
        for x in (0, 31):
            d.line([x, 9, x, 25], fill=colour)
    elif pattern == "industrial":
        for x in range(0, w, 4):
            d.line([x, 0, x, h], fill=dark)
        d.line([0, 2, w, 2], fill=_mix(colour, (0, 0, 0), 0.3))
    return img


def png_rgba(img: Image.Image) -> bytes:
    """PNG bytes, always RGBA: GLES3 can't mipmap SRGB8 (RGB) textures, which then render black."""
    buf = io.BytesIO()
    img.convert("RGBA").save(buf, "PNG", optimize=True)
    return buf.getvalue()


def quantize(c, step=8):
    """Snap a colour to a coarse grid so near-identical walls share one facade texture."""
    return tuple(min(255, int(round(v / step) * step)) for v in c)
