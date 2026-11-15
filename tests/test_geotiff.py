import numpy as np
import rasterio
from rasterio.transform import from_origin

from tak_buildings3d import geotiff, imagery


def _write(path, crs="EPSG:32633", x0=500000.0, y0=5800000.0, px=0.1, n=512):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.full((3, n, n), 120, np.uint8)
    with rasterio.open(path, "w", driver="GTiff", width=n, height=n, count=3, dtype="uint8",
                       crs=crs, transform=from_origin(x0, y0, px, px)) as ds:
        ds.write(data)


def test_scan_and_render(tmp_path):
    """A UTM GeoTIFF is found, measured, and rendered into Web Mercator tiles inside its outline."""
    root = tmp_path / "geotiff"
    _write(root / "city" / "a.tif")
    (root / "city" / "b.ecw").write_bytes(b"")
    assert geotiff.folders(root) == ["city"]
    info = geotiff.scan(root / "city")
    assert info["resolution_m"] == 0.1 and len(info["files"]) == 1
    w, s, e, n = info["coverage"]
    cfg = {"id": "city", "url": "x", "folder": "city", "licence_confirmed": True, "max_zoom": 21, **info}
    src = geotiff.GeoTiffSource(cfg, tmp_path / "tiles", root)
    x, y = imagery.lonlat_to_pixel((w + e) / 2, (s + n) / 2, 21)
    arr, full = src.render(21, int(x) // 256, int(y) // 256)
    assert full and abs(int(arr[0].mean()) - 120) <= 2
    x, y = imagery.lonlat_to_pixel(w - 0.01, n + 0.01, 21)
    arr, full = src.render(21, int(x) // 256, int(y) // 256)
    assert not full and arr[3].max() == 0


def test_unreadable_formats_explained(tmp_path):
    (tmp_path / "v").mkdir()
    (tmp_path / "v" / "O8.sid").write_bytes(b"")
    try:
        geotiff.scan(tmp_path / "v")
    except ValueError as e:
        assert "1 MrSID" in str(e)
    else:
        raise AssertionError("expected ValueError")
