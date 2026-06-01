"""Tests for the `verify` rule action (VLM confirmation gate).

The verify action calls the agent analyzer to confirm the triggering
observation actually shows what the rule claims, and aborts the rest of
the action chain when the VLM rejects it. The analyzer is mocked at its
import boundary (`services.agent.analyzer.analyze_frame_target`) so no
real VLM call happens.
"""

import asyncio
import uuid

import pytest

from shared.schemas import RuleCreate


# ── helpers ──────────────────────────────────────────────────────────


def _mkrule(actions):
    return RuleCreate(
        name="test",
        trigger_pattern={"type": "any"},
        actions=actions,
    )


class _FakeAnalyzerResult:
    """Mirror of services.agent.analyzer.AnalyzerResult for the bits the
    verify action reads."""

    def __init__(self, answer, error=None):
        self.answer = answer
        self.error = error


class R:
    def __init__(self):
        self.id = uuid.uuid4()
        self.name = "r"


def _patch_common(monkeypatch):
    """Patch the event-side effects so the action runs without a DB.

    Returns the list that records every verify result stamped onto the
    event so tests can assert Event.payload contents.
    """
    from services.events import actions as actions_mod

    recorded = []

    async def fake_record(event_id, verify_result):
        recorded.append(verify_result)

    async def fake_update(*a, **kw):
        pass

    monkeypatch.setattr(actions_mod, "_record_verify_on_event", fake_record)
    monkeypatch.setattr(actions_mod, "_update_event_status", fake_update)
    return recorded


def _patch_analyzer(monkeypatch, *, verdict=None, confidence=None, error=None, summary="ok"):
    import services.agent.analyzer as analyzer_mod

    async def fake_analyze(ctx, observation_id, question, provider_id=None):
        if error is not None:
            return _FakeAnalyzerResult({"error": error})
        return _FakeAnalyzerResult(
            {"verdict": verdict, "confidence": confidence, "summary": summary}
        )

    monkeypatch.setattr(analyzer_mod, "analyze_frame_target", fake_analyze)


def _run_chain(actions_mod, chain, obs):
    """Run a list of actions through execute_action, breaking the chain
    on RuntimeError exactly like RuleEngine.evaluate does. Returns the
    list of downstream urls that fired."""
    rule = R()
    event_id = rule.id

    async def run():
        for action in chain:
            try:
                await actions_mod.execute_action(action, obs, rule, event_id)
            except RuntimeError:
                break

    asyncio.run(run())


# ── execution. pass / fail gating ────────────────────────────────────


def test_verify_pass_runs_downstream(monkeypatch):
    from services.events import actions as actions_mod

    recorded = _patch_common(monkeypatch)
    _patch_analyzer(monkeypatch, verdict="yes", confidence=0.9)

    fired = []

    async def fake_webhook(action, obs, rule, event_id, ctx):
        fired.append(action["url"])

    monkeypatch.setattr(actions_mod, "_execute_webhook", fake_webhook)

    obs = {"observation_id": str(uuid.uuid4()), "vars": {}}
    chain = [
        {"type": "verify", "question": "real person?", "min_confidence": 0.6},
        {"type": "webhook", "url": "downstream"},
    ]
    _run_chain(actions_mod, chain, obs)

    assert fired == ["downstream"]
    assert recorded[0]["passed"] is True
    assert recorded[0]["verdict"] == "yes"


def test_verify_fail_stop_suppresses_downstream(monkeypatch):
    from services.events import actions as actions_mod

    recorded = _patch_common(monkeypatch)
    _patch_analyzer(monkeypatch, verdict="no", confidence=0.95)

    fired = []

    async def fake_webhook(action, obs, rule, event_id, ctx):
        fired.append(action["url"])

    monkeypatch.setattr(actions_mod, "_execute_webhook", fake_webhook)

    obs = {"observation_id": str(uuid.uuid4()), "vars": {}}
    chain = [
        {"type": "verify", "question": "real person?", "on_fail": "stop"},
        {"type": "webhook", "url": "downstream"},
    ]
    _run_chain(actions_mod, chain, obs)

    assert fired == []  # telegram/webhook suppressed
    assert recorded[0]["passed"] is False
    assert recorded[0]["verdict"] == "no"


def test_verify_cannot_tell_treated_as_fail(monkeypatch):
    from services.events import actions as actions_mod

    recorded = _patch_common(monkeypatch)
    _patch_analyzer(monkeypatch, verdict="cannot_tell", confidence=0.1)

    fired = []

    async def fake_webhook(action, obs, rule, event_id, ctx):
        fired.append(action["url"])

    monkeypatch.setattr(actions_mod, "_execute_webhook", fake_webhook)

    obs = {"observation_id": str(uuid.uuid4()), "vars": {}}
    chain = [
        {"type": "verify", "question": "real person?", "on_fail": "stop"},
        {"type": "webhook", "url": "downstream"},
    ]
    _run_chain(actions_mod, chain, obs)

    assert fired == []
    assert recorded[0]["passed"] is False
    assert recorded[0]["verdict"] == "cannot_tell"


def test_verify_fail_continue_runs_downstream(monkeypatch):
    from services.events import actions as actions_mod

    recorded = _patch_common(monkeypatch)
    _patch_analyzer(monkeypatch, verdict="no", confidence=0.9)

    fired = []

    async def fake_webhook(action, obs, rule, event_id, ctx):
        fired.append(action["url"])

    monkeypatch.setattr(actions_mod, "_execute_webhook", fake_webhook)

    obs = {"observation_id": str(uuid.uuid4()), "vars": {}}
    chain = [
        {"type": "verify", "question": "real person?", "on_fail": "continue"},
        {"type": "webhook", "url": "downstream"},
    ]
    _run_chain(actions_mod, chain, obs)

    assert fired == ["downstream"]
    assert recorded[0]["passed"] is False


def test_verify_low_confidence_fails(monkeypatch):
    from services.events import actions as actions_mod

    recorded = _patch_common(monkeypatch)
    # verdict yes but confidence below the 0.6 threshold.
    _patch_analyzer(monkeypatch, verdict="yes", confidence=0.3)

    fired = []

    async def fake_webhook(action, obs, rule, event_id, ctx):
        fired.append(action["url"])

    monkeypatch.setattr(actions_mod, "_execute_webhook", fake_webhook)

    obs = {"observation_id": str(uuid.uuid4()), "vars": {}}
    chain = [
        {"type": "verify", "question": "real person?", "min_confidence": 0.6, "on_fail": "stop"},
        {"type": "webhook", "url": "downstream"},
    ]
    _run_chain(actions_mod, chain, obs)

    assert fired == []
    assert recorded[0]["passed"] is False
    assert recorded[0]["confidence"] == 0.3


def test_verify_no_observation_fails(monkeypatch):
    from services.events import actions as actions_mod

    recorded = _patch_common(monkeypatch)
    # analyzer should never be called when there's no observation.
    _patch_analyzer(monkeypatch, verdict="yes", confidence=1.0)

    fired = []

    async def fake_webhook(action, obs, rule, event_id, ctx):
        fired.append(action["url"])

    monkeypatch.setattr(actions_mod, "_execute_webhook", fake_webhook)

    obs = {"vars": {}}  # no observation_id
    chain = [
        {"type": "verify", "question": "real person?", "on_fail": "stop"},
        {"type": "webhook", "url": "downstream"},
    ]
    _run_chain(actions_mod, chain, obs)

    assert fired == []
    assert recorded[0]["passed"] is False
    assert recorded[0]["verdict"] == "cannot_tell"


def test_verify_analyzer_error_fails(monkeypatch):
    from services.events import actions as actions_mod

    recorded = _patch_common(monkeypatch)
    _patch_analyzer(monkeypatch, error="media_evicted")

    fired = []

    async def fake_webhook(action, obs, rule, event_id, ctx):
        fired.append(action["url"])

    monkeypatch.setattr(actions_mod, "_execute_webhook", fake_webhook)

    obs = {"observation_id": str(uuid.uuid4()), "vars": {}}
    chain = [
        {"type": "verify", "question": "real person?", "on_fail": "stop"},
        {"type": "webhook", "url": "downstream"},
    ]
    _run_chain(actions_mod, chain, obs)

    assert fired == []
    assert recorded[0]["passed"] is False
    assert recorded[0]["verdict"] == "cannot_tell"


def test_verify_records_full_result_shape(monkeypatch):
    from services.events import actions as actions_mod

    recorded = _patch_common(monkeypatch)
    _patch_analyzer(monkeypatch, verdict="yes", confidence=0.8, summary="a person is at the door")

    obs = {"observation_id": str(uuid.uuid4()), "vars": {}}
    chain = [
        {"type": "verify", "question": "real person?", "min_confidence": 0.6},
    ]
    _run_chain(actions_mod, chain, obs)

    result = recorded[0]
    assert set(result.keys()) == {"passed", "verdict", "confidence", "question", "summary"}
    assert result["question"] == "real person?"
    assert result["summary"] == "a person is at the door"


# ── schema validation ────────────────────────────────────────────────


def test_verify_empty_question_rejected():
    with pytest.raises(Exception) as exc:
        _mkrule([{"type": "verify", "question": "  "}])
    assert "question" in str(exc.value)


def test_verify_missing_question_rejected():
    with pytest.raises(Exception) as exc:
        _mkrule([{"type": "verify"}])
    assert "question" in str(exc.value)


def test_verify_bad_on_fail_rejected():
    with pytest.raises(Exception) as exc:
        _mkrule([{"type": "verify", "question": "q", "on_fail": "explode"}])
    assert "on_fail" in str(exc.value)


def test_verify_min_confidence_out_of_range_rejected():
    with pytest.raises(Exception) as exc:
        _mkrule([{"type": "verify", "question": "q", "min_confidence": 1.5}])
    assert "min_confidence" in str(exc.value)


def test_verify_valid_action_accepted():
    rule = _mkrule(
        [
            {"type": "verify", "question": "real person?", "min_confidence": 0.6, "on_fail": "stop"},
            {"type": "notify", "message": "person confirmed"},
        ]
    )
    assert len(rule.actions) == 2
