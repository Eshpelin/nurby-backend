"""Tests for HAR<->VLM fusion helpers and use-case action presets."""

from services.perception.har_actions import (
    action_in_set,
    allowed_actions,
)
from services.perception.har_context import (
    format_har_context,
    har_vlm_agreement,
)

# ── HAR -> VLM context ───────────────────────────────────────────────────────

def test_format_context_names_and_actions():
    live = [
        {"person_name": "Mum", "action": "eating"},
        {"person_name": "Dad", "action": "walking"},
    ]
    s = format_har_context(live)
    assert s and "Mum appears to be eating" in s and "Dad appears to be walking" in s


def test_format_context_skips_unknown_and_unnamed():
    live = [
        {"person_name": None, "action": "walking"},     # unnamed -> skip
        {"person_name": "X", "action": "unknown"},        # unknown -> skip
    ]
    assert format_har_context(live) is None
    assert format_har_context([]) is None


# ── VLM <-> HAR agreement ────────────────────────────────────────────────────

def test_agreement_true_when_caption_corroborates():
    assert har_vlm_agreement("eating", "An elderly woman eating soup at a table") is True
    assert har_vlm_agreement("fallen", "A person collapsed on the floor") is True


def test_agreement_false_when_caption_contradicts():
    # HAR says standing, caption clearly says lying/sleeping
    assert har_vlm_agreement("standing", "A resident asleep on the sofa") is False


def test_agreement_none_when_unclear():
    assert har_vlm_agreement("eating", "A dim room with furniture") is None
    assert har_vlm_agreement("unknown", "anything") is None
    assert har_vlm_agreement("walking", None) is None


# ── action presets ───────────────────────────────────────────────────────────

def test_presets_narrow_actions():
    assert "fallen" in allowed_actions("eldercare")
    assert "playing" not in allowed_actions("eldercare")
    assert "playing" in allowed_actions("childcare")
    assert "fallen" not in allowed_actions("childcare")
    # unknown preset -> all
    assert allowed_actions("nonsense") == allowed_actions("all")


def test_action_in_set():
    assert action_in_set("fallen", "eldercare") is True
    assert action_in_set("playing", "eldercare") is False
    assert action_in_set("unknown", "security") is True  # unknown always passes the gate
