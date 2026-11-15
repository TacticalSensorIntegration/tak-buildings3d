import time
import json
import struct

import pygltflib as gl
import pytest
from shapely.geometry import box

from conftest import LAT, LON


def _glb(path):
    b = path.read_bytes()
    assert b[:4] == b"b3dm"
    ftj, ftb, btj, btb = struct.unpack("<4I", b[12:28])
    return gl.GLTF2.load_from_bytes(b[28 + ftj + ftb + btj + btb:])


def test_pbf_reader(sample_pbf):
    """Buildings are read from a .pbf and clipped to the area."""
    from tak_buildings3d import osm
    blds = osm.from_pbf(sample_pbf, box(LON - 0.01, LAT - 0.01, LON + 0.01, LAT + 0.01))
    assert len(blds) == 12
    assert osm.from_pbf(sample_pbf, box(0, 0, 1, 1)) == []


def test_build(sample_pbf, offline):
    """End to end on the synthetic town: tag rules in report.json, and the tileset conventions ATAK needs."""
    from tak_buildings3d import pipeline
    clip = pipeline.circle(LAT, LON, 400)
    s = pipeline.run(pipeline.Params(name="sample", clip=clip, pbf=sample_pbf), log=lambda m: None)
    assert s["buildings"] == 12

    out = pipeline.OUT / "sample"
    rep = json.loads((out / "report.json").read_text())
    by_id = {b["id"]: b for b in rep["buildings"]}
    assert by_id["w1"]["roof_shape"] == "hipped"          # untagged house
    assert by_id["w2"]["roof_colour"] == "#a5523e"        # roof:colour=red: terracotta, not pure red
    assert by_id["w3"]["wall_colour"] == "#c8c0b0" and by_id["w3"]["roof_shape"] == "flat"
    assert by_id["w3"]["eave_m"] == pytest.approx(6 * 3.3)
    assert by_id["w4"]["eave_m"] + by_id["w4"]["roof_height_m"] == pytest.approx(8.0, abs=0.1)
    assert by_id["w7"]["roof_shape"] == "dome"
    # building rules
    assert by_id["w8"]["open"] and by_id["w8"]["eave_m"] == pytest.approx(2.4)       # carport: roof on posts
    assert by_id["w9"]["class"] == "office" and by_id["w9"]["eave_m"] == pytest.approx(12.0)   # car park: 4 decks
    assert by_id["w9"]["facade"] == "parking"
    assert by_id["w10"]["roof_shape"] == "mansard" and by_id["w10"]["wall_colour"] == "#deceb0"
    assert by_id["w11"]["roof_shape"] == "gabled" and by_id["w11"]["eave_m"] == pytest.approx(2.4)   # shed
    assert by_id["w12"]["roof_shape"] == "gabled"                                     # double_saltbox: was flat

    ts = json.loads((out / "sample_buildings" / "tileset.json").read_text())
    assert "gltfUpAxis" not in json.dumps(ts["asset"])    # ATAK/TAK-X ignore it; content is Y-up
    assert len(ts["root"]["transform"]) == 16
    for f in (out / "sample_buildings").glob("*.b3dm"):
        g = _glb(f)
        for prim in g.meshes[0].primitives:
            assert prim.attributes.COLOR_0 is None           # ATAK ignores vertex colours
            mat = g.materials[prim.material]
            assert mat.pbrMetallicRoughness.baseColorTexture is not None
    # coarser LOD tiles keep window facades (REPEAT sampler 1), not just solid colours
    lod = _glb(next((out / "sample_buildings").glob("tile_1_*.b3dm")))
    assert any(lod.textures[m.pbrMetallicRoughness.baseColorTexture.index].sampler == 1 for m in lod.materials)
    assert (out / "sample_buildings.zip").exists()


def test_web_api(sample_pbf, offline):
    """Web app: list downloads, reject bad requests, build a drawn area, then list, zip and delete it."""
    from fastapi.testclient import TestClient
    from tak_buildings3d import regions
    from tak_buildings3d.web.app import app

    regions.REGIONS.mkdir(parents=True, exist_ok=True)
    (regions.REGIONS / "sample.osm.pbf").write_bytes(sample_pbf.read_bytes())
    (regions.REGIONS / "sample.json").write_text(json.dumps(
        {"id": "sample", "name": "Sample", "path": "Sample", "bbox": None, "bytes": 1}))

    c = TestClient(app)
    assert c.get("/").status_code == 200
    assert [d["id"] for d in c.get("/api/downloads").json()] == ["sample"]

    poly = box(LON - 0.003, LAT - 0.003, LON + 0.003, LAT + 0.003).__geo_interface__
    huge = box(LON - 1, LAT - 1, LON + 1, LAT + 1).__geo_interface__
    assert c.post("/api/builds", json={"name": "x", "geometry": huge, "region": "sample"}).status_code == 400
    assert c.post("/api/builds", json={"name": "x", "geometry": poly, "region": "nope"}).status_code == 400

    job = c.post("/api/builds", json={"name": "web test", "geometry": poly, "region": "sample"}).json()
    import time
    for _ in range(100):
        j = c.get(f"/api/jobs/{job['id']}").json()
        if j["status"] in ("done", "error"):
            break
        time.sleep(0.1)
    assert j["status"] == "done", j
    names = [b["name"] for b in c.get("/api/builds").json()]
    assert "web_test" in names
    z = c.get("/api/builds/web_test/zip")
    assert z.status_code == 200 and z.content[:2] == b"PK"
    assert c.delete("/api/builds/web_test").status_code == 200


def test_zip_carries_attribution(sample_pbf, offline, tmp_path):
    """Credits travel with the data: ATTRIBUTION.txt sits next to tileset.json in the zip."""
    import zipfile
    from tak_buildings3d import pipeline
    s = pipeline.run(pipeline.Params(name="attr", clip=pipeline.bbox(-180, -85, 180, 85), pbf=sample_pbf),
                     log=lambda m: None)
    with zipfile.ZipFile(pipeline.OUT / "attr" / s["zip"]) as z:
        names = z.namelist()
        txt = z.read("ATTRIBUTION.txt").decode()
    assert "tileset.json" in names and "OpenStreetMap contributors" in txt and "Copernicus" in txt


def test_regions_for_area(offline, monkeypatch, tmp_path):
    """Drawing an area: report the downloaded extract covering it, else the extracts that would."""
    from fastapi.testclient import TestClient
    from shapely.geometry import box
    from shapely.prepared import prep
    from tak_buildings3d import regions
    from tak_buildings3d.web.app import app
    shapes = [({"id": "act", "name": "ACT", "parent": "australia", "urls": {"pbf": "u/act"}}, box(148.7, -35.95, 149.45, -35.1)),
              ({"id": "australia", "name": "Australia", "urls": {"pbf": "u/au"}}, box(112, -44, 154, -10))]
    monkeypatch.setattr(regions, "_shapes", lambda: [(p, g, prep(g)) for p, g in shapes])
    monkeypatch.setattr(regions, "_pbf_size", lambda url: {"u/act": 19_000_000, "u/au": 975_000_000}[url])
    monkeypatch.setattr(regions, "local_pbf", lambda rid: None)
    c = TestClient(app)
    geo = lambda b: {"type": "Polygon", "coordinates": [list(b.exterior.coords)]}
    r = c.post("/api/regions/for", json={"geometry": geo(box(149.10, -35.29, 149.11, -35.28))}).json()
    assert r["have"] is None and [o["id"] for o in r["options"]] == ["act", "australia"]
    assert r["options"][0]["path"] == "Australia / ACT" and r["options"][0]["bytes"] == 19_000_000 and r["overpass_ok"]
    r = c.post("/api/regions/for", json={"geometry": geo(box(149.3, -35.3, 149.6, -35.0))}).json()   # crosses the ACT border
    assert [o["id"] for o in r["options"]] == ["australia"] and not r["overpass_ok"]
    monkeypatch.setattr(regions, "local_pbf", lambda rid: tmp_path if rid == "australia" else None)
    r = c.post("/api/regions/for", json={"geometry": geo(box(149.10, -35.29, 149.11, -35.28))}).json()
    assert r["have"] == "australia"


def test_download_resumes_after_drop(tmp_path, monkeypatch):
    """A dropped connection mid-download resumes with a Range request instead of failing."""
    import requests
    from tak_buildings3d import regions
    data = bytes(range(256)) * 40_000                      # ~10 MB
    calls = []

    class Resp:
        def __init__(self, start):
            self.start, self.status_code = start, 206 if start else 200
            self.headers = {"content-length": str(len(data) - start)}
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def raise_for_status(self): pass
        def iter_content(self, n):
            for i in range(self.start, len(data), n):
                if not calls[1:] and i - self.start >= 3 * n:   # first attempt dies after 3 chunks
                    raise requests.exceptions.ConnectionError("read timed out")
                yield data[i:i + n]

    def get(url, headers, stream, timeout):
        start = int(headers["Range"][6:-1]) if "Range" in headers else 0
        calls.append(start)
        return Resp(start)
    monkeypatch.setattr(regions.requests, "get", get)
    monkeypatch.setattr(regions.time, "sleep", lambda s: None)
    dest = tmp_path / "x.osm.pbf"
    regions._fetch_resumable("https://example/x.osm.pbf", dest, log=lambda m: None)
    assert dest.read_bytes() == data and calls == [0, 3 << 20]


def test_rebuild_and_params(sample_pbf, offline, monkeypatch):
    """Builds record their settings and OSM date; rebuild reuses them; a failed rebuild keeps the old one."""
    import time
    from fastapi.testclient import TestClient
    from tak_buildings3d import pipeline, regions
    from tak_buildings3d.web.app import app
    regions.REGIONS.mkdir(parents=True, exist_ok=True)
    (regions.REGIONS / "sample.osm.pbf").write_bytes(sample_pbf.read_bytes())
    (regions.REGIONS / "sample.json").write_text(json.dumps(
        {"id": "sample", "name": "Sample", "path": "Sample", "bbox": None, "bytes": 1, "downloaded": "2026-01-01T00:00:00Z"}))
    clip = box(LON - 0.003, LAT - 0.003, LON + 0.003, LAT + 0.003)
    pipeline.run(pipeline.Params(name="rb", clip=clip, pbf=regions.local_pbf("sample")), log=lambda m: None)
    rep = json.loads((pipeline.OUT / "rb" / "report.json").read_text())
    assert rep["params"]["region"] == "sample" and rep["params"]["roofs"] == "osm"
    assert rep["osm"]["downloaded"] == "2026-01-01T00:00:00Z"

    c = TestClient(app)
    b = next(x for x in c.get("/api/builds").json() if x["name"] == "rb")
    assert b["osm_date"] == "2026-01-01T00:00:00Z" and not b["osm_newer"]
    (regions.REGIONS / "sample.json").write_text(json.dumps(
        {"id": "sample", "name": "Sample", "path": "Sample", "bbox": None, "bytes": 1, "downloaded": "2026-02-01T00:00:00Z"}))
    assert next(x for x in c.get("/api/builds").json() if x["name"] == "rb")["osm_newer"]

    job = c.post("/api/builds/rb/rebuild").json()
    for _ in range(100):
        j = c.get(f"/api/jobs/{job['id']}").json()
        if j["status"] in ("done", "error"):
            break
        time.sleep(0.1)
    assert j["status"] == "done", j
    assert json.loads((pipeline.OUT / "rb" / "report.json").read_text())["osm"]["downloaded"] == "2026-02-01T00:00:00Z"

    # a failing rebuild must leave the previous tileset in place
    monkeypatch.setattr(pipeline.export, "write_3dtiles", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        pipeline.run(pipeline.Params(name="rb", clip=clip, pbf=regions.local_pbf("sample")), log=lambda m: None)
    assert (pipeline.OUT / "rb" / "report.json").exists() and not (pipeline.OUT / ".rb.building").exists()


def test_jobs_cancel_and_same_name_replaces():
    """A queued job can be cancelled; building the same name again replaces the older job;
    a running job stops at its next log line."""
    import threading
    from tak_buildings3d.web import app as A
    gate, started = threading.Event(), threading.Event()

    def slow(log, prog):
        started.set(); gate.wait(5); log("still going")
        return "finished"
    first = A._submit("build", "x", slow, key="build:x")
    assert started.wait(5)
    second = A._submit("build", "x", lambda log, prog: "second", key="build:x")   # replaces first
    third = A._submit("build", "y", lambda log, prog: "y")
    A.cancel_job(third["id"])
    gate.set()
    for _ in range(100):
        if second["status"] == "done":
            break
        time.sleep(0.05)
    assert first["status"] == "cancelled" and "newer build" in first["error"]
    assert second["status"] == "done" and third["status"] == "cancelled"


def test_download_resumes_in_a_later_attempt(tmp_path, monkeypatch):
    """A partial download is resumed by the next attempt if the server's file is unchanged,
    and restarted if it changed meanwhile."""
    import requests
    from tak_buildings3d import regions
    data = bytes(range(256)) * 20_000
    etag = ['"v1"']
    seen = []

    class Resp:
        def __init__(self, headers):
            rng, same = headers.get("Range"), headers.get("If-Range") == etag[0]
            self.start = int(rng[6:-1]) if rng and same else 0
            self.status_code = 206 if self.start else 200
            self.headers = {"content-length": str(len(data) - self.start), "ETag": etag[0]}
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def raise_for_status(self): pass
        def iter_content(self, n):
            for i in range(self.start, len(data), n):
                if fail[0] and i - self.start >= n:
                    raise requests.exceptions.ConnectionError("dropped")
                yield data[i:i + n]

    def get(url, headers, stream, timeout):
        seen.append(headers.get("Range"))
        return Resp(headers)
    monkeypatch.setattr(regions.requests, "get", get)
    monkeypatch.setattr(regions.time, "sleep", lambda s: None)
    dest, fail = tmp_path / "x.download.osm.pbf", [True]
    with pytest.raises(requests.exceptions.ConnectionError):
        regions._fetch_resumable("https://example/x", dest, log=lambda m: None, retries=0)
    assert dest.stat().st_size == 1 << 20
    fail[0] = False
    seen.clear()
    regions._fetch_resumable("https://example/x", dest, log=lambda m: None)      # same version: resumes
    assert dest.read_bytes() == data and seen == [f"bytes={1 << 20}-"]
    fail[0] = True
    with pytest.raises(requests.exceptions.ConnectionError):
        regions._fetch_resumable("https://example/x", dest, log=lambda m: None, retries=0)
    etag[0], fail[0] = '"v2"', False                                              # newer extract published
    regions._fetch_resumable("https://example/x", dest, log=lambda m: None)
    assert dest.read_bytes() == data
