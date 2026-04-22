"""Tests for action-chain validation and condition evaluation."""

import pytest

from shared.schemas import RuleCreate


def _mkrule(actions):
    return RuleCreate(
        name="test",
        trigger_pattern={"type": "any"},
        actions=actions,
    )


def test_vlm_call_output_then_webhook_reference_ok():
    actions = [
        {
            "type": "vlm_call",
            "provider": "openai",
            "model": "gpt-4o-mini",
            "prompt": "rate the threat",
            "output": "threat",
            "response_schema": {
                "type": "object",
                "properties": {"level": {"type": "string"}, "reason": {"type": "string"}},
            },
        },
        {
            "type": "webhook",
            "url": "https://example.com/hook",
            "payload_template": {"level": "{{vars.threat.level}}"},
        },
    ]
    rule = _mkrule(actions)
    assert len(rule.actions) == 2


def test_forward_reference_rejected():
    actions = [
        {
            "type": "webhook",
            "url": "https://example.com/{{vars.threat.level}}",
        },
        {
            "type": "vlm_call",
            "provider": "openai",
            "model": "gpt-4o-mini",
            "prompt": "rate",
            "output": "threat",
        },
    ]
    with pytest.raises(Exception) as exc:
        _mkrule(actions)
    assert "threat" in str(exc.value)


def test_unknown_output_rejected():
    actions = [
        {
            "type": "vlm_call", "provider": "openai", "model": "m",
            "prompt": "p", "output": "foo",
        },
        {
            "type": "email", "to": "a@b.co",
            "subject": "s", "body": "{{vars.bar.x}}",
        },
    ]
    with pytest.raises(Exception) as exc:
        _mkrule(actions)
    assert "bar" in str(exc.value)


def test_unknown_action_type_rejected():
    with pytest.raises(Exception):
        _mkrule([{"type": "dance"}])


def test_schema_top_level_key_check():
    actions = [
        {
            "type": "vlm_call", "provider": "openai", "model": "m", "prompt": "p",
            "output": "o",
            "response_schema": {"type": "object", "properties": {"level": {"type": "string"}}},
        },
        {
            "type": "webhook", "url": "http://x",
            "payload_template": {"val": "{{vars.o.nope}}"},
        },
    ]
    with pytest.raises(Exception) as exc:
        _mkrule(actions)
    assert "nope" in str(exc.value)


def test_condition_evaluated_by_runner(monkeypatch):
    import asyncio
    from services.events import actions as actions_mod

    calls = []

    async def fake_webhook(action, obs, rule, event_id, ctx):
        calls.append(action["url"])

    async def fake_update(*a, **kw):
        pass

    monkeypatch.setattr(actions_mod, "_execute_webhook", fake_webhook)
    monkeypatch.setattr(actions_mod, "_update_event_status", fake_update)

    class R:
        id = __import__("uuid").uuid4()
        name = "r"

    async def run():
        obs = {"vars": {"out": {"level": "high"}}}
        skip_action = {
            "type": "webhook", "url": "skip",
            "condition": "vars.out.level == 'low'",
        }
        go_action = {
            "type": "webhook", "url": "go",
            "condition": "vars.out.level == 'high'",
        }
        await actions_mod.execute_action(skip_action, obs, R(), R.id)
        await actions_mod.execute_action(go_action, obs, R(), R.id)

    asyncio.run(run())
    assert calls == ["go"]


def test_chained_output_writes_vars(monkeypatch):
    import asyncio
    from services.events import actions as actions_mod

    async def fake_call_vlm(*args, **kwargs):
        return '{"level": "high", "reason": "ok"}'

    async def fake_update(*a, **kw):
        pass

    class FakeProvider:
        api_key = "k"
        base_url = "http://x"
        default_model = "m"

    async def fake_provider(kind):
        return FakeProvider()

    monkeypatch.setattr(actions_mod, "_call_vlm", fake_call_vlm)
    monkeypatch.setattr(actions_mod, "_get_provider_by_kind", fake_provider)
    monkeypatch.setattr(actions_mod, "_update_event_status", fake_update)

    class R:
        id = __import__("uuid").uuid4()
        name = "r"

    async def run():
        obs = {"vars": {}}
        action = {
            "type": "vlm_call",
            "provider": "openai",
            "model": "gpt-4o-mini",
            "prompt": "p",
            "output": "threat",
            "response_schema": {
                "type": "object",
                "properties": {"level": {"type": "string"}, "reason": {"type": "string"}},
                "required": ["level", "reason"],
            },
        }
        await actions_mod.execute_action(action, obs, R(), R.id)
        return obs["vars"]

    vars_bag = asyncio.run(run())
    assert vars_bag["threat"]["level"] == "high"
