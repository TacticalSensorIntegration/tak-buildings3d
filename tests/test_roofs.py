import pytest
from shapely.geometry import Polygon

from tak_buildings3d import roofs

L_SHAPE = Polygon([(0, 0), (18, 0), (18, 7), (8, 7), (8, 16), (0, 16)])


@pytest.mark.parametrize("shape", ["gabled", "hipped", "pyramidal", "skillion", "round", "dome",
                                   "gambrel", "mansard", "half-hipped", "saltbox"])
def test_roof_covers_footprint(shape):
    """Every roof shape covers the footprint exactly once, between eave and ridge."""
    planes = roofs.planes_for(shape, L_SHAPE, 3.0)
    faces = roofs.faces(planes, L_SHAPE)
    assert sum(r.area for _, r in faces) == pytest.approx(L_SHAPE.area, rel=1e-6)
    for x, y in L_SHAPE.exterior.coords:
        assert -1e-6 <= roofs.height(planes, x, y) <= 3.0 + 1e-6
