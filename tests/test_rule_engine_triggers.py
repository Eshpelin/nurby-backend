"""Trigger pattern coverage for RuleEngine._match_trigger.

Each test prefills the engine with one FakeRule, feeds a matching and a
non-matching observation, and asserts the recorded execute_action call
count. The legacy zone_name modes for loitering/line_cross are covered
alongside the inline geometry modes.
"""

import asyncio
import time
import uuid

import pytest

from tests._engine_helpers import FakeRule, install_engine


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


# ── Basic dispatch ────────────────────────────────────────────────

def test_any_trigger_always_fires(monkeypatch):
    rule = FakeRule(name="r", trigger_pattern={"type": "any"})
    eng, rec = install_engine(monkeypatch, [rule])
    asyncio.run(eng.evaluate({}))
    asyncio.run(eng.evaluate({"foo": "bar"}))
    assert rec.call_count == 2


def test_unknown_trigger_never_fires(monkeypatch):
    rule = FakeRule(name="r", trigger_pattern={"type": "no_such_trigger"})
    eng, rec = install_engine(monkeypatch, [rule])
    asyncio.run(eng.evaluate({"motion_score": 1.0}))
    assert rec.call_count == 0


def test_disabled_rule_skipped(monkeypatch):
    rule = FakeRule(name="r", trigger_pattern={"type": "any"}, enabled=False)
    eng, rec = install_engine(monkeypatch, [rule])
    asyncio.run(eng.evaluate({}))
    assert rec.call_count == 0


# ── object_detected ───────────────────────────────────────────────

def test_object_detected_label_match(monkeypatch):
    rule = FakeRule(name="r", trigger_pattern={"type": "object_detected", "label": "person"})
    eng, rec = install_engine(monkeypatch, [rule])

    asyncio.run(eng.evaluate({
        "object_detections": {"objects": [{"label": "person", "confidence": 0.9}]},
    }))
    asyncio.run(eng.evaluate({
        "object_detections": {"objects": [{"label": "car", "confidence": 0.9}]},
    }))
    assert rec.call_count == 1


def test_object_detected_no_label_matches_any(monkeypatch):
    rule = FakeRule(name="r", trigger_pattern={"type": "object_detected"})
    eng, rec = install_engine(monkeypatch, [rule])
    asyncio.run(eng.evaluate({"object_detections": {"objects": [{"label": "cat"}]}}))
    asyncio.run(eng.evaluate({"object_detections": {"objects": []}}))
    assert rec.call_count == 1


# ── face_detected / recognized / unknown ──────────────────────────

def test_face_detected(monkeypatch):
    rule = FakeRule(name="r", trigger_pattern={"type": "face_detected"})
    eng, rec = install_engine(monkeypatch, [rule])
    asyncio.run(eng.evaluate({"person_detections": {"count": 2, "faces": []}}))
    asyncio.run(eng.evaluate({"person_detections": {"count": 0, "faces": []}}))
    assert rec.call_count == 1


def test_face_recognized_specific_person(monkeypatch):
    pid = str(uuid.uuid4())
    rule = FakeRule(name="r", trigger_pattern={"type": "face_recognized", "person_id": pid})
    eng, rec = install_engine(monkeypatch, [rule])
    asyncio.run(eng.evaluate({"person_detections": {"count": 1, "faces": [{"person_id": pid}]}}))
    asyncio.run(eng.evaluate({"person_detections": {"count": 1, "faces": [{"person_id": str(uuid.uuid4())}]}}))
    asyncio.run(eng.evaluate({"person_detections": {"count": 1, "faces": [{"person_id": None}]}}))
    assert rec.call_count == 1


def test_face_recognized_any_person(monkeypatch):
    rule = FakeRule(name="r", trigger_pattern={"type": "face_recognized"})
    eng, rec = install_engine(monkeypatch, [rule])
    asyncio.run(eng.evaluate({"person_detections": {"count": 1, "faces": [{"person_id": "p1"}]}}))
    asyncio.run(eng.evaluate({"person_detections": {"count": 1, "faces": [{"person_id": None}]}}))
    assert rec.call_count == 1


def test_face_unknown(monkeypatch):
    rule = FakeRule(name="r", trigger_pattern={"type": "face_unknown"})
    eng, rec = install_engine(monkeypatch, [rule])
    asyncio.run(eng.evaluate({"person_detections": {"count": 1, "faces": [{"person_id": None}]}}))
    asyncio.run(eng.evaluate({"person_detections": {"count": 1, "faces": [{"person_id": "p1"}]}}))
    asyncio.run(eng.evaluate({"person_detections": {"count": 0, "faces": []}}))
    assert rec.call_count == 1


# ── motion ────────────────────────────────────────────────────────

def test_motion_min_score(monkeypatch):
    rule = FakeRule(name="r", trigger_pattern={"type": "motion", "min_score": 0.5})
    eng, rec = install_engine(monkeypatch, [rule])
    asyncio.run(eng.evaluate({"motion_score": 0.8}))
    asyncio.run(eng.evaluate({"motion_score": 0.1}))
    asyncio.run(eng.evaluate({"motion_score": 0.5}))  # boundary inclusive
    assert rec.call_count == 2


# ── audio_event / clap_pattern / speech_phrase ────────────────────

def test_audio_event(monkeypatch):
    rule = FakeRule(name="r", trigger_pattern={"type": "audio_event", "label": "baby_cry", "min_score": 0.4})
    eng, rec = install_engine(monkeypatch, [rule])
    asyncio.run(eng.evaluate({"audio_event": {"label": "baby_cry", "score": 0.9}}))
    asyncio.run(eng.evaluate({"audio_event": {"label": "scream", "score": 0.9}}))
    asyncio.run(eng.evaluate({"audio_event": {"label": "baby_cry", "score": 0.2}}))
    assert rec.call_count == 1


def test_clap_pattern(monkeypatch):
    rule = FakeRule(name="r", trigger_pattern={"type": "clap_pattern", "count": 2})
    eng, rec = install_engine(monkeypatch, [rule])
    asyncio.run(eng.evaluate({"clap_pattern": {"count": 2}}))
    asyncio.run(eng.evaluate({"clap_pattern": {"count": 3}}))
    asyncio.run(eng.evaluate({}))
    assert rec.call_count == 1


def test_speech_phrase_any(monkeypatch):
    rule = FakeRule(name="r", trigger_pattern={
        "type": "speech_phrase",
        "phrases": ["help", "fire"],
    })
    eng, rec = install_engine(monkeypatch, [rule])
    asyncio.run(eng.evaluate({"transcript": {"text": "please help me"}}))
    asyncio.run(eng.evaluate({"transcript": {"text": "nothing to see"}}))
    assert rec.call_count == 1


def test_speech_phrase_all(monkeypatch):
    rule = FakeRule(name="r", trigger_pattern={
        "type": "speech_phrase",
        "phrases": ["help", "fire"],
        "match": "all",
    })
    eng, rec = install_engine(monkeypatch, [rule])
    asyncio.run(eng.evaluate({"transcript": {"text": "help fire alarm"}}))
    asyncio.run(eng.evaluate({"transcript": {"text": "help me"}}))
    assert rec.call_count == 1


# ── loitering & line_cross (inline geometry) ──────────────────────

def test_loitering_inline_geometry(monkeypatch):
    poly = [[0, 0], [100, 0], [100, 100], [0, 100]]
    rule = FakeRule(
        name="r",
        trigger_pattern={
            "type": "loitering",
            "points": poly,
            "threshold_seconds": 0.05,
            "label": "person",
        },
    )
    eng, rec = install_engine(monkeypatch, [rule])

    track_inside = {"track_id": 1, "label": "person", "bbox": [10, 10, 30, 30]}
    track_outside = {"track_id": 2, "label": "person", "bbox": [500, 500, 520, 520]}

    # First observation arms the timer but does not fire.
    asyncio.run(eng.evaluate({"tracks": [track_inside]}))
    assert rec.call_count == 0

    time.sleep(0.07)

    asyncio.run(eng.evaluate({"tracks": [track_inside]}))
    assert rec.call_count == 1

    # Track outside the polygon never fires.
    eng2, rec2 = install_engine(monkeypatch, [rule])
    asyncio.run(eng2.evaluate({"tracks": [track_outside]}))
    time.sleep(0.07)
    asyncio.run(eng2.evaluate({"tracks": [track_outside]}))
    assert rec2.call_count == 0


def test_loitering_legacy_events(monkeypatch):
    rule = FakeRule(
        name="r",
        trigger_pattern={"type": "loitering", "zone_name": "porch"},
    )
    eng, rec = install_engine(monkeypatch, [rule])
    asyncio.run(eng.evaluate({"loitering_events": [{"zone_name": "porch"}]}))
    asyncio.run(eng.evaluate({"loitering_events": [{"zone_name": "garage"}]}))
    assert rec.call_count == 1


def test_line_cross_inline_geometry(monkeypatch):
    rule = FakeRule(
        name="r",
        trigger_pattern={
            "type": "line_cross",
            "points": [[50, 0], [50, 100]],
            "direction": "any",
        },
    )
    eng, rec = install_engine(monkeypatch, [rule])

    crossing = {
        "track_id": 1,
        "prev_bbox": [0, 40, 20, 60],
        "bbox": [80, 40, 100, 60],
    }
    not_crossing = {
        "track_id": 2,
        "prev_bbox": [0, 40, 20, 60],
        "bbox": [10, 40, 30, 60],
    }
    asyncio.run(eng.evaluate({"tracks": [crossing]}))
    asyncio.run(eng.evaluate({"tracks": [not_crossing]}))
    assert rec.call_count == 1


def test_line_cross_legacy_events(monkeypatch):
    rule = FakeRule(
        name="r",
        trigger_pattern={"type": "line_cross", "zone_name": "front_gate"},
    )
    eng, rec = install_engine(monkeypatch, [rule])
    asyncio.run(eng.evaluate({
        "line_cross_events": [{"zone_name": "front_gate", "direction": "in"}]
    }))
    asyncio.run(eng.evaluate({
        "line_cross_events": [{"zone_name": "other", "direction": "in"}]
    }))
    assert rec.call_count == 1


# ── vehicle_detected ──────────────────────────────────────────────

def test_vehicle_detected_specific_plate(monkeypatch):
    rule = FakeRule(name="r", trigger_pattern={"type": "vehicle_detected", "plate": "ABC"})
    eng, rec = install_engine(monkeypatch, [rule])
    # Plate contains "ABC" -> fires.
    asyncio.run(eng.evaluate({
        "vehicle_detections": {"vehicles": [{"plate_text": "ABC123", "vehicle_id": "v1"}], "count": 1},
    }))
    # Different plate -> no fire.
    asyncio.run(eng.evaluate({
        "vehicle_detections": {"vehicles": [{"plate_text": "XYZ999", "vehicle_id": "v2"}], "count": 1},
    }))
    assert rec.call_count == 1


def test_vehicle_detected_identified_only(monkeypatch):
    rule = FakeRule(name="r", trigger_pattern={"type": "vehicle_detected", "identified_only": True})
    eng, rec = install_engine(monkeypatch, [rule])
    # Has a vehicle_id -> fires.
    asyncio.run(eng.evaluate({
        "vehicle_detections": {"vehicles": [{"plate_text": "ABC123", "vehicle_id": "v1"}], "count": 1},
    }))
    # Plateless (no vehicle_id) -> no fire.
    asyncio.run(eng.evaluate({
        "vehicle_detections": {"vehicles": [{"plate_text": None, "vehicle_id": None}], "count": 1},
    }))
    assert rec.call_count == 1


def test_vehicle_detected_any(monkeypatch):
    rule = FakeRule(name="r", trigger_pattern={"type": "vehicle_detected"})
    eng, rec = install_engine(monkeypatch, [rule])
    asyncio.run(eng.evaluate({"vehicle_detections": {"vehicles": [{"label": "car"}], "count": 1}}))
    asyncio.run(eng.evaluate({"vehicle_detections": {"vehicles": [], "count": 0}}))
    asyncio.run(eng.evaluate({}))  # no vehicle_detections key
    assert rec.call_count == 1
