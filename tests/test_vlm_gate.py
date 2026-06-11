"""Tests for the CLIP zero-shot pre-classifier gate.

We don't actually load CLIP in tests (model download + GPU). Instead
patch the inner sync classifier so we can assert decision logic +
graceful fallbacks.
"""

import asyncio

import numpy as np

from services.perception import vlm_gate
from services.perception.vlm_gate import (
    CLIPGate,
    GateDecision,
    maybe_skip_via_gate,
)


def _run(c):
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(c)


def _blank_frame():
    return np.zeros((64, 64, 3), dtype=np.uint8)


def test_disabled_gate_always_allows():
    out = _run(
        maybe_skip_via_gate(
            _blank_frame(),
            enabled=False,
            interesting_prompts=None,
            boring_prompts=None,
            margin=0.05,
            min_interesting_score=0.20,
        )
    )
    assert out.allow is True
    assert out.reason == "disabled"


def test_load_failed_gate_falls_open():
    gate = CLIPGate()
    gate._load_failed = True
    out = _run(
        gate.classify(_blank_frame(), ["person"], ["empty"], 0.05, 0.20)
    )
    assert out.allow is True
    assert out.reason == "disabled"


def test_boring_score_wins_with_margin(monkeypatch):
    """When boring class beats interesting by margin, skip."""
    gate = CLIPGate()
    gate._load_failed = False  # pretend model loaded

    def fake(*args, **kwargs):
        return GateDecision(
            allow=False,
            interesting_score=0.30,
            boring_score=0.45,
            interesting_label="person",
            boring_label="leaves",
            reason="boring",
        )

    monkeypatch.setattr(gate, "_classify_sync", fake)
    out = _run(gate.classify(_blank_frame(), ["person"], ["leaves"], 0.05, 0.20))
    assert out.allow is False
    assert out.reason == "boring"


def test_interesting_wins(monkeypatch):
    gate = CLIPGate()
    gate._load_failed = False

    def fake(*args, **kwargs):
        return GateDecision(
            allow=True,
            interesting_score=0.45,
            boring_score=0.30,
            interesting_label="person",
            boring_label="leaves",
            reason="interesting",
        )

    monkeypatch.setattr(gate, "_classify_sync", fake)
    out = _run(gate.classify(_blank_frame(), ["person"], ["leaves"], 0.05, 0.20))
    assert out.allow is True


def test_below_floor_skips(monkeypatch):
    gate = CLIPGate()
    gate._load_failed = False

    def fake(*args, **kwargs):
        return GateDecision(
            allow=False,
            interesting_score=0.12,
            boring_score=0.10,
            interesting_label="person",
            boring_label="leaves",
            reason="below_floor",
        )

    monkeypatch.setattr(gate, "_classify_sync", fake)
    out = _run(gate.classify(_blank_frame(), ["person"], ["leaves"], 0.05, 0.20))
    assert out.allow is False
    assert out.reason == "below_floor"


def test_inference_error_falls_open(monkeypatch):
    gate = CLIPGate()
    gate._load_failed = False

    def fake_raise(*args, **kwargs):
        raise RuntimeError("inference died")

    monkeypatch.setattr(gate, "_classify_sync", fake_raise)
    out = _run(gate.classify(_blank_frame(), ["person"], ["leaves"], 0.05, 0.20))
    assert out.allow is True
    assert out.reason == "error"


def test_maybe_skip_via_gate_uses_default_prompts(monkeypatch):
    """When interesting/boring_prompts is None, use the module defaults."""
    captured = {}

    class FakeGate:
        async def classify(self, frame, interesting, boring, margin, min_interesting_score):
            captured["interesting"] = interesting
            captured["boring"] = boring
            return GateDecision(True, 0.5, 0.1, "x", "y", "interesting")

    monkeypatch.setattr(vlm_gate, "get_gate", lambda: FakeGate())
    _run(
        maybe_skip_via_gate(
            _blank_frame(),
            enabled=True,
            interesting_prompts=None,
            boring_prompts=None,
            margin=0.05,
            min_interesting_score=0.20,
        )
    )
    assert captured["interesting"] == vlm_gate.DEFAULT_INTERESTING_PROMPTS
    assert captured["boring"] == vlm_gate.DEFAULT_BORING_PROMPTS


def test_decision_dataclass_fields_complete():
    """Regression. don't break downstream consumers expecting reason field."""
    d = GateDecision(True, 0.1, 0.2, "a", "b", "x")
    assert d.allow is True
    assert d.reason == "x"
