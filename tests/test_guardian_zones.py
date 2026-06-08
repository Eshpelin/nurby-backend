"""Tests for guardian named-zone enter/exit geometry."""

from services.perception.guardian_zones import (
    _bbox_center,
    _point_in_polygon,
    diff_zones,
    zones_for_point,
)

SQUARE = [[0, 0], [100, 0], [100, 100], [0, 100]]
FAR = [[200, 200], [300, 200], [300, 300], [200, 300]]


def test_point_in_polygon():
    assert _point_in_polygon((50, 50), SQUARE)
    assert not _point_in_polygon((150, 50), SQUARE)
    assert not _point_in_polygon((0, 0), [])  # degenerate


def test_bbox_center():
    assert _bbox_center([10, 20, 30, 40]) == (20.0, 30.0)


def test_zones_for_point_named_polygons_only():
    zones = [
        {"name": "Playground", "type": "include", "points": SQUARE},
        {"name": "Gate", "type": "tripwire", "points": SQUARE},  # ignored
        {"name": "Far", "type": "include", "points": FAR},
        {"type": "include", "points": SQUARE},  # unnamed, ignored
    ]
    assert zones_for_point((50, 50), zones) == {"Playground"}
    assert zones_for_point((250, 250), zones) == {"Far"}
    assert zones_for_point((500, 500), zones) == set()


def test_diff_zones():
    assert diff_zones({"A"}, {"A", "B"}) == ({"B"}, set())
    assert diff_zones({"A", "B"}, {"B"}) == (set(), {"A"})
    assert diff_zones(set(), set()) == (set(), set())
