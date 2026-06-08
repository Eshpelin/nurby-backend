"""Tests for the standardised action vocabulary, parsing, and fall confirm."""

import uuid

import pytest

from services.perception import actions
from services.perception import guardian_fall as gf


# ── parse_action ──────────────────────────────────────────────────────────────

def test_parse_strict_json():
    p = actions.parse_action('{"action": "eating", "posture": "sitting", "confidence": 0.9}')
    assert p["action"] == "eating"
    assert p["posture"] == "sitting"
    assert p["confidence"] == 0.9
    assert p["detail"] is None


def test_parse_open_world_detail():
    p = actions.parse_action(
        '{"action": "eating", "detail": "holding a cup of tea by the window"}'
    )
    assert p["action"] == "eating"
    assert p["detail"] == "holding a cup of tea by the window"


def test_parse_detail_length_capped():
    long = "x" * 500
    p = actions.parse_action('{"action": "walking", "detail": "' + long + '"}')
    assert len(p["detail"]) == actions.DETAIL_MAX_CHARS


def test_parse_blank_detail_is_none():
    p = actions.parse_action('{"action": "walking", "detail": "   "}')
    assert p["detail"] is None


def test_parse_json_in_code_fence_and_prose():
    raw = 'Sure! ```json\n{"action": "fallen", "confidence": 0.7}\n``` hope that helps'
    p = actions.parse_action(raw)
    assert p["action"] == "fallen"
    assert p["confidence"] == 0.7


def test_parse_rejects_unknown_action_falls_back_to_keyword():
    # "lying on the floor" -> not valid JSON action, keyword scan finds "on the floor"
    p = actions.parse_action("the person appears to be lying on the floor")
    assert p["action"] == "fallen"


def test_parse_sleeping_keyword():
    assert actions.parse_action("they are asleep in bed")["action"] == "sleeping"


def test_parse_clamps_confidence_and_defaults_unknown():
    p = actions.parse_action('{"action": "eating", "confidence": 5}')
    assert p["confidence"] == 1.0
    assert actions.parse_action("")["action"] == "unknown"
    assert actions.parse_action("complete gibberish xyzzy")["action"] == "unknown"


def test_parse_invalid_action_value_to_unknown():
    # valid JSON, but action not in vocabulary -> unknown (no keyword either)
    assert actions.parse_action('{"action": "teleporting"}')["action"] == "unknown"


# ── confirms_fall (the sleeping-resident fix) ────────────────────────────────

def test_confirms_fall_alerts_on_fallen_and_unknown():
    assert actions.confirms_fall({"action": "fallen"}) is True
    assert actions.confirms_fall({"action": "unknown"}) is True
    assert actions.confirms_fall({}) is True  # missing -> unknown -> fail open
    assert actions.confirms_fall(None) is True


def test_confirms_fall_suppresses_on_confident_non_fall():
    assert actions.confirms_fall({"action": "sleeping"}) is False
    assert actions.confirms_fall({"action": "lying_down"}) is False
    assert actions.confirms_fall({"action": "sitting"}) is False
    assert actions.confirms_fall({"action": "eating"}) is False


# ── coarse_action_from_caption (backfill) ────────────────────────────────────

def test_coarse_action_from_caption():
    assert actions.coarse_action_from_caption("An elderly man eating soup") == "eating"
    assert actions.coarse_action_from_caption("She is asleep in bed") == "sleeping"
    assert actions.coarse_action_from_caption("A resident collapsed on the floor") == "fallen"
    assert actions.coarse_action_from_caption("A person walking down the hall") == "walking"
    assert actions.coarse_action_from_caption("An empty room with chairs") is None
    assert actions.coarse_action_from_caption("") is None
    assert actions.coarse_action_from_caption(None) is None


def test_coarse_caption_prefers_fall_over_weaker_cue():
    # "sitting" appears, but a fall cue must win
    assert (
        actions.coarse_action_from_caption("was sitting then fell down on the floor")
        == "fallen"
    )


# ── body_box_for_face ────────────────────────────────────────────────────────

def test_body_box_for_face_picks_containing_box():
    face = [300, 780, 360, 840]  # centre 330,810
    big = [100, 700, 500, 900]
    far = [0, 0, 50, 50]
    assert actions.body_box_for_face(face, [far, big]) == big
    assert actions.body_box_for_face(face, [far]) is None
    assert actions.body_box_for_face(None, [big]) is None


def test_body_box_for_face_prefers_tightest_containing():
    face = [300, 780, 360, 840]
    loose = [50, 600, 600, 950]
    tight = [250, 750, 450, 880]
    assert actions.body_box_for_face(face, [loose, tight]) == tight


# ── dependant_faces ──────────────────────────────────────────────────────────

def test_dependant_faces_filters_incomplete():
    pid = str(uuid.uuid4())
    pd = {
        "faces": [
            {"person_id": pid, "person_name": "Mum", "bbox": [1, 2, 3, 4]},
            {"person_id": None, "person_name": "x", "bbox": [1, 2, 3, 4]},  # no id
            {"person_id": pid, "person_name": "Mum", "bbox": [1, 2]},       # bad bbox
            "not a dict",
        ]
    }
    out = actions.dependant_faces(pd)
    assert out == [(pid, "Mum", [1, 2, 3, 4])]
    assert actions.dependant_faces(None) == []
    assert actions.dependant_faces({}) == []


# ── fall confirm wired through guardian_fall.process ─────────────────────────

class _Cam:
    id = uuid.uuid4()
    motion_zones = None


def _face(pid, bbox):
    return {"person_id": pid, "person_name": "Inara", "bbox": bbox}


@pytest.mark.asyncio
async def test_fall_confirm_suppresses_sleeping(monkeypatch):
    gf.reset_state()
    calls = []

    async def fake_emit(name, camera):
        calls.append(name)

    monkeypatch.setattr(gf, "_safe_emit", fake_emit)

    # confirm returns False -> VLM says not a fall (e.g. sleeping)
    async def confirm(_cam, _bbox):
        return False

    cam = _Cam()
    body = [100, 700, 500, 900]
    faces = [_face(str(uuid.uuid4()), [300, 780, 360, 840])]
    await gf.process(cam, [body], faces, 1000, now=0.0, confirm=confirm)
    await gf.process(cam, [body], faces, 1000, now=10.0, confirm=confirm)
    assert calls == []  # held fall, but VLM suppressed it


@pytest.mark.asyncio
async def test_fall_confirm_allows_real_fall(monkeypatch):
    gf.reset_state()
    calls = []

    async def fake_emit(name, camera):
        calls.append(name)

    monkeypatch.setattr(gf, "_safe_emit", fake_emit)

    async def confirm(_cam, _bbox):
        return actions.confirms_fall({"action": "fallen"})

    cam = _Cam()
    body = [100, 700, 500, 900]
    faces = [_face(str(uuid.uuid4()), [300, 780, 360, 840])]
    await gf.process(cam, [body], faces, 1000, now=0.0, confirm=confirm)
    await gf.process(cam, [body], faces, 1000, now=10.0, confirm=confirm)
    assert calls == ["Inara"]


@pytest.mark.asyncio
async def test_fall_confirm_fails_open_on_error(monkeypatch):
    gf.reset_state()
    calls = []

    async def fake_emit(name, camera):
        calls.append(name)

    monkeypatch.setattr(gf, "_safe_emit", fake_emit)

    async def confirm(_cam, _bbox):
        raise RuntimeError("vlm down")

    cam = _Cam()
    body = [100, 700, 500, 900]
    faces = [_face(str(uuid.uuid4()), [300, 780, 360, 840])]
    await gf.process(cam, [body], faces, 1000, now=0.0, confirm=confirm)
    await gf.process(cam, [body], faces, 1000, now=10.0, confirm=confirm)
    assert calls == ["Inara"]  # error must not silence a fall
