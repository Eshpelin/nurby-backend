"""Unit tests for idle VLM enrichment pure logic.

DB-touching paths (pass storage, summary repoint, candidate query) are
verified live against postgres. these cover the lens sequencing and the
deterministic attribute extraction.
"""

from __future__ import annotations

from services.perception.vlm_enrichment_worker import build_attributes, next_lens

# ---- lens sequencing ----------------------------------------------------

def test_first_lens_is_attributes():
    assert next_lens(set(), has_recording=False, summary_stale=True) == "attributes"


def test_temporal_runs_only_with_recording():
    have = {"attributes"}
    assert next_lens(have, has_recording=True, summary_stale=True) == "temporal"
    # no recording -> skip temporal, go to anomaly
    assert next_lens(have, has_recording=False, summary_stale=True) == "anomaly"


def test_summary_after_raw_passes_when_stale():
    have = {"attributes", "anomaly"}
    assert next_lens(have, has_recording=False, summary_stale=True) == "summary"


def test_no_summary_when_not_stale():
    have = {"attributes", "anomaly", "summary"}
    assert next_lens(have, has_recording=False, summary_stale=False) is None


def test_resummarize_when_new_raw_pass_lands():
    # a temporal pass arrived after the last summary -> summary is stale again
    have = {"attributes", "anomaly", "summary", "temporal"}
    assert next_lens(have, has_recording=True, summary_stale=True) == "summary"


def test_no_summary_before_any_raw_pass():
    assert next_lens(set(), has_recording=False, summary_stale=True) == "attributes"


# ---- attribute extraction ----------------------------------------------

def test_build_attributes_from_detections_and_text():
    text = "A white SUV with plate ABC1234 is parked in the driveway at night."
    dets = [{"label": "car"}, {"label": "person"}, {"label": "person"}]
    a = build_attributes(text, dets)
    assert a["people_count"] == 2
    assert {"label": "car", "count": 1} in a["objects"]
    assert {"label": "person", "count": 2} in a["objects"]
    assert "white" in a["colors"]
    assert "night" in a["time_of_day"]
    assert "ABC1234" in a["text_seen"]


def test_build_attributes_empty_text_is_safe():
    a = build_attributes(None, [])
    assert a["people_count"] == 0
    assert a["objects"] == []
    assert a["colors"] == []


def test_text_seen_requires_a_digit():
    a = build_attributes("A PERSON WALKS HERE", [])
    assert a["text_seen"] == []
