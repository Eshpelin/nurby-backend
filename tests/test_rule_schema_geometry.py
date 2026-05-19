"""Schema validation guard rails for geometry-bound triggers.

Loitering needs a polygon (>= 3 points) plus a camera. Line cross
needs exactly 2 points plus a camera. speech_phrase needs at least one
phrase. The fixes land in Pass B; until then these assertions are
xfailed because the current schema lets the bad shapes through.
"""

import pytest

from shared.schemas import RuleCreate


def _mkrule(trigger_pattern):
    return RuleCreate(
        name="t",
        trigger_pattern=trigger_pattern,
        actions=[{"type": "broadcast"}],
    )


# Pass B turns these into proper rejections by adding a model
# validator to RuleCreate. Until then the schema lets them through.
_XFAIL_GEOMETRY = pytest.mark.xfail(
    strict=True,
    reason="Pass B. schema validator must reject geometry-less triggers",
)


@_XFAIL_GEOMETRY
def test_loitering_without_points_rejected():
    with pytest.raises(Exception) as exc:
        _mkrule({"type": "loitering", "threshold_seconds": 30, "camera_id": "cam"})
    assert "loiter" in str(exc.value).lower() or "point" in str(exc.value).lower()


@_XFAIL_GEOMETRY
def test_loitering_with_two_points_rejected():
    with pytest.raises(Exception):
        _mkrule({
            "type": "loitering",
            "points": [[0, 0], [10, 10]],
            "camera_id": "cam",
        })


@_XFAIL_GEOMETRY
def test_loitering_without_camera_rejected():
    with pytest.raises(Exception):
        _mkrule({
            "type": "loitering",
            "points": [[0, 0], [10, 0], [10, 10]],
        })


@_XFAIL_GEOMETRY
def test_line_cross_without_points_rejected():
    with pytest.raises(Exception):
        _mkrule({"type": "line_cross", "direction": "any", "camera_id": "cam"})


@_XFAIL_GEOMETRY
def test_line_cross_with_three_points_rejected():
    with pytest.raises(Exception):
        _mkrule({
            "type": "line_cross",
            "points": [[0, 0], [10, 0], [20, 0]],
            "camera_id": "cam",
        })


@_XFAIL_GEOMETRY
def test_line_cross_without_camera_rejected():
    with pytest.raises(Exception):
        _mkrule({"type": "line_cross", "points": [[0, 0], [10, 0]]})


@_XFAIL_GEOMETRY
def test_speech_phrase_empty_phrases_rejected():
    with pytest.raises(Exception):
        _mkrule({"type": "speech_phrase", "phrases": []})


@_XFAIL_GEOMETRY
def test_speech_phrase_missing_phrases_rejected():
    with pytest.raises(Exception):
        _mkrule({"type": "speech_phrase"})


def test_loitering_valid_shape_accepted():
    rule = _mkrule({
        "type": "loitering",
        "points": [[0, 0], [10, 0], [10, 10]],
        "camera_id": "cam-uuid",
        "threshold_seconds": 30,
    })
    assert rule.trigger_pattern["type"] == "loitering"


def test_line_cross_valid_shape_accepted():
    rule = _mkrule({
        "type": "line_cross",
        "points": [[0, 0], [10, 10]],
        "camera_id": "cam-uuid",
    })
    assert rule.trigger_pattern["type"] == "line_cross"


def test_speech_phrase_valid_shape_accepted():
    rule = _mkrule({"type": "speech_phrase", "phrases": ["help"]})
    assert rule.trigger_pattern["type"] == "speech_phrase"
