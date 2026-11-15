import os
import tempfile
from pathlib import Path

import osmium
import pytest

# Isolate data/ and out/ before tak_buildings3d.pipeline is imported.
_HOME = Path(tempfile.mkdtemp(prefix="tak-buildings3d-test-"))
os.environ["TAK_BUILDINGS3D_HOME"] = str(_HOME)

LAT, LON = -35.2626, 149.1440
M = 1 / 111320.0                      # ~1 m in degrees of latitude


def _ring(pts, nid):
    """pts in metres around (LAT, LON) -> node list; returns (nodes, ids)."""
    import math
    nodes, ids = [], []
    for x, y in pts:
        nid[0] += 1
        nodes.append(osmium.osm.mutable.Node(
            id=nid[0], version=1,
            location=(LON + x * M / math.cos(math.radians(LAT)), LAT + y * M)))
        ids.append(nid[0])
    return nodes, ids + ids[:1]


BUILDINGS = {
    # id: (footprint metres, tags)
    1: ([(0, 0), (14, 0), (14, 9), (0, 9)], {"building": "house"}),
    2: ([(30, 0), (48, 0), (48, 7), (38, 7), (38, 16), (30, 16)], {"building": "house", "roof:colour": "red"}),
    3: ([(0, 40), (40, 40), (40, 70), (0, 70)], {"building": "office", "building:levels": "6",
                                                 "building:colour": "#c8c0b0", "roof:material": "gravel"}),
    4: ([(60, 0), (72, 0), (72, 10), (60, 10)], {"building": "yes", "roof:shape": "gabled", "height": "8"}),
    5: ([(60, 30), (70, 30), (70, 40), (60, 40)], {"building": "yes", "roof:shape": "pyramidal"}),
    6: ([(60, 50), (72, 50), (72, 58), (60, 58)], {"building": "shed", "roof:shape": "skillion"}),
    7: ([(80, 0), (100, 0), (100, 20), (80, 20)], {"building": "church", "roof:shape": "dome"}),
    8: ([(110, 0), (116, 0), (116, 12), (110, 12)], {"building": "carport"}),
    9: ([(110, 30), (140, 30), (140, 60), (110, 60)], {"building": "parking"}),
    10: ([(150, 0), (162, 0), (162, 9), (150, 9)], {"building": "yes", "roof:shape": "mansard",
                                                    "building:colour": "beige"}),
    11: ([(150, 30), (156, 30), (156, 34), (150, 34)], {"building": "shed"}),
    12: ([(150, 50), (162, 50), (162, 58), (150, 58)], {"building": "yes", "roof:shape": "double_saltbox"}),
}


@pytest.fixture(scope="session")
def sample_pbf():
    path = _HOME / "sample.osm.pbf"
    if not path.exists():
        nid = [0]
        nodes, ways = [], []
        for wid, (pts, tags) in BUILDINGS.items():
            n, ids = _ring(pts, nid)
            nodes += n
            ways.append(osmium.osm.mutable.Way(id=wid, version=1, nodes=ids, tags=tags))
        w = osmium.SimpleWriter(str(path))
        for n in nodes:
            w.add_node(n)
        for way in ways:
            w.add_way(way)
        w.close()
    return path


class FlatTerrain:
    def __init__(self, *a, **k):
        pass

    def sample(self, lon, lat):
        return 600.0


@pytest.fixture
def offline(monkeypatch):
    """No network: flat 600 m terrain, geoid separation 0."""
    from tak_buildings3d import pipeline
    monkeypatch.setattr(pipeline, "Terrain", FlatTerrain)
    monkeypatch.setattr(pipeline, "msl_to_hae", lambda lon, lat, h: h)
