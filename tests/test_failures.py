import pytest
import rasterio.errors
import requests

from tak_buildings3d import geo, imagery, pipeline


def test_self_crossing_area_is_repaired():
    """A bow-tie ring has zero signed area; it's repaired so area limits see its real size."""
    g = pipeline.geojson_geometry({"type": "Polygon", "coordinates": [[[0, 0], [1, 1], [1, 0], [0, 1], [0, 0]]]})
    assert g.is_valid and g.area == pytest.approx(0.5)
    with pytest.raises(ValueError):
        pipeline.geojson_geometry({"type": "LineString", "coordinates": [[0, 0], [1, 1]]})


def test_dem_network_failure_is_not_sea(monkeypatch):
    """Only a 404 DEM cell reads as sea; any other failure stops the build instead of using 0 m."""
    def fail(*a, **k):
        raise rasterio.errors.RasterioIOError("timeout")
    monkeypatch.setattr(geo.rasterio, "open", fail)
    monkeypatch.setattr(geo.requests, "head", lambda *a, **k: (_ for _ in ()).throw(requests.ConnectionError("down")))
    with pytest.raises(OSError):
        geo.Terrain(149.0, -35.4, 149.1, -35.3)
    monkeypatch.setattr(geo.requests, "head", lambda *a, **k: type("R", (), {"status_code": 404})())
    assert geo.Terrain(149.0, -35.4, 149.1, -35.3).sample(149.05, -35.35) == 0.0


def test_wms_error_is_not_cached(tmp_path, monkeypatch):
    """A WMS error returned as HTTP 200 XML gives a grey tile and isn't cached."""
    src = imagery.TileSource({"url": "https://x/{bbox}", "max_zoom": 19, "licence_confirmed": True}, tmp_path)
    resp = type("R", (), {"status_code": 200, "content": b"<ServiceExceptionReport/>"})()
    monkeypatch.setattr(src.session, "get", lambda *a, **k: resp)
    monkeypatch.setattr(imagery.time, "sleep", lambda s: None)
    src.tile(18, 1, 1)
    assert src.failed == 1 and not any(tmp_path.rglob("*.img"))
