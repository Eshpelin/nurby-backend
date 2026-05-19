"""End-to-end-ish test for action chains.

Asserts.

1. The engine threads a shared ``vars`` dict through every action in a
   rule firing. If action 0 writes ``vars["threat"] = {...}`` then
   action 1 receives the same dict.
2. Template rendering in ``_execute_webhook`` resolves
   ``{{vars.threat.level}}`` against that shared dict.
"""

import asyncio
import uuid
from types import SimpleNamespace

import httpx

from tests._engine_helpers import FakeRule, install_engine


def test_vars_shared_across_action_chain(monkeypatch):
    """First action writes a var, second action sees it via the shared
    observation_data dict the engine passes positionally."""

    actions = [
        {"type": "vlm_call", "provider": "openai", "model": "m", "prompt": "p", "output": "threat"},
        {
            "type": "webhook",
            "url": "https://example.com/hook",
            "payload_template": {"level": "{{vars.threat.level}}"},
        },
    ]
    rule = FakeRule(name="r", trigger_pattern={"type": "any"}, actions=actions, cooldown_seconds=0)
    eng, rec = install_engine(monkeypatch, [rule])

    # Stub execute_action with a recorder that mutates vars on the
    # first call so the second call sees the new state.
    seen_payloads = []

    async def fake_exec(action, obs, r, eid):
        if action["type"] == "vlm_call":
            obs.setdefault("vars", {})["threat"] = {"level": "high", "reason": "tested"}
        seen_payloads.append((action["type"], dict(obs.get("vars", {}))))

    monkeypatch.setattr("services.events.engine.execute_action", fake_exec)
    asyncio.run(eng.evaluate({}))

    assert [t for t, _ in seen_payloads] == ["vlm_call", "webhook"]
    # Second action observed the vars written by the first.
    assert seen_payloads[1][1]["threat"]["level"] == "high"


def test_webhook_payload_renders_chained_var(monkeypatch):
    """Drive ``_execute_webhook`` directly and prove the templated
    payload picks up the value placed by an earlier vlm_call."""

    from services.events import actions as actions_mod

    captured = {}

    class _FakeResponse:
        status_code = 200

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, json=None, headers=None, timeout=None):
            captured["url"] = url
            captured["json"] = json
            return _FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: _FakeClient())

    async def fake_update(*a, **kw):
        return None

    monkeypatch.setattr(actions_mod, "_update_event_status", fake_update)

    rule = SimpleNamespace(id=uuid.uuid4(), name="r")
    event_id = uuid.uuid4()
    obs = {"vars": {"threat": {"level": "high"}}}
    action = {
        "type": "webhook",
        "url": "https://example.com/{{vars.threat.level}}",
        "payload_template": {"level": "{{vars.threat.level}}", "rule": "{{rule_name}}"},
    }

    async def run():
        ctx = actions_mod._build_template_context(obs, rule, event_id)
        await actions_mod._execute_webhook(action, obs, rule, event_id, ctx)

    asyncio.run(run())

    assert captured["url"] == "https://example.com/high"
    assert captured["json"]["level"] == "high"
    assert captured["json"]["rule"] == "r"
