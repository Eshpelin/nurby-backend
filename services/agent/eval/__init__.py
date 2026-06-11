"""Agent eval harness.

The eval suite runs a fixture YAML through a mocked agent driver that
swaps in a deterministic LLM client + canned tool responses. It exists
so we can measure agent quality without burning real LLM tokens on
every PR.

Public surface.

- ``load_fixture(path)`` parses a YAML fixture into an ``EvalFixture``.
- ``run_fixture(fixture)`` executes it and returns an ``EvalResult``.
- ``MockLLMClient`` + ``MockDriver`` are the swappable mocks the runner
  builds on; production code wires the real driver in their place.
- ``format_report(results)`` renders the markdown summary CI uploads.

Real-LLM mode (``AGENT_EVAL_REAL_LLM=1``) is documented in
``docs/agent-eval.md``. Today it falls back to mock execution and
records a skipped row because Wave 2A's real driver has not landed.
The switch from mock to real is a one-line change inside
``run_fixture`` once ``services.agent.driver`` lands.
"""

from services.agent.eval.mocks import MockDriver, MockLLMClient
from services.agent.eval.report import format_report
from services.agent.eval.runner import (
    EvalFixture,
    EvalResult,
    list_fixture_paths,
    load_fixture,
    run_fixture,
)

__all__ = [
    "EvalFixture",
    "EvalResult",
    "MockDriver",
    "MockLLMClient",
    "format_report",
    "list_fixture_paths",
    "load_fixture",
    "run_fixture",
]
