# Agent eval suite

The agent eval suite measures whether `services/agent/driver` answers
realistic household questions correctly. It is the gate that backs the
Phase 1 exit criterion in `docs/agent-design.md` section 11.3.
Twenty-seven of thirty fixtures must pass before Phase 1 ships.

## Layout

```
tests/agent_fixtures/             # 30 YAML cases
tests/test_agent_eval.py          # pytest entry point (parametrized)
services/agent/eval/
    runner.py                     # load + execute + score
    mocks.py                      # MockLLMClient + MockDriver + MockToolRegistry
    report.py                     # markdown report formatter
scripts/seed_eval_db.py           # optional dev DB seeder for /ask UI
.github/workflows/agent-eval.yml  # nightly + PR CI workflow
```

The runner does NOT touch a real Postgres or Redis. Each fixture
carries its own canned tool responses in the `seed` block, the runner
replays them through `MockDriver`, and the result is scored against
`expected`.

## Running locally

Mocked (fast, deterministic, the path CI uses).

```
python -m pytest tests/test_agent_eval.py -v
```

Real LLM (manual sanity check on a single fixture).

```
AGENT_EVAL_REAL_LLM=1 python -m pytest tests/test_agent_eval.py -v \
    -k q01_package_today
```

The real-LLM path is currently a stub. It returns `skipped` until
Wave 2A's `services.agent.driver` lands; once it does, swap
`MockDriver` for the real driver inside `services/agent/eval/runner.py`
in the marked one-line spot and the suite works end-to-end.

## Fixture format

```yaml
id: q01_package_today
question: "Did a package arrive at the front door today?"
tags: [indexed, household, package]

# Canned data the in-process tool registry returns. Top-level shortcuts
# (observations / cameras / journeys) auto-wrap into the canonical
# tool result envelope. Tool_results lets you override per-tool with
# either a dict (single response) or a list (sequence on repeat calls,
# used for cache simulation).
seed:
  observations:
    - id: "..."
      camera_id: "..."
      camera_name: "Front Door"
      timestamp: "2026-05-24T14:02:00Z"
      description: "A package was left on the porch"
      detections: { objects: [{ label: "package", confidence: 0.91 }] }

# Scripted LLM transcript. Each entry is one assistant turn. Tool_uses
# triggers tool dispatch; text + stop_reason ends the loop with the
# final synthesis.
mocked_llm:
  - tool_uses:
      - { name: query_observations, arguments: { query: "package", hours: 24 } }
  - { text: "Yes, a package was delivered to the front door at 2:02 PM today.", stop_reason: end_turn }

# Scoring contract. All keys are optional; absence means the dimension
# is not checked.
expected:
  final_answer_contains: ["package", "front door"]   # case-insensitive substrings
  final_answer_forbidden: ["I'm sure"]               # never appears
  status: completed                                  # AgentRun.status
  tools_called: [query_observations]                 # all must appear at least once
  tools_not_called: [analyze_clip]                   # must NOT appear
  tool_calls_min: 1
  tool_calls_max: 6
  vlm_calls_min: 0
  vlm_calls_max: 0
  vlm_cached: true                                   # at least one cache-hit VLM call
  cost_cents_max: 10
  turns_max: 5
  citations_min: 1                                   # tool calls that returned data

# Optional. Per-fixture run budget. The driver aborts with
# status=budget_exhausted when crossed. Used by q19.
budget_cents: 1

# Optional. Multi-turn conversation. Each entry is one question; the
# scripted LLM transcript must cover ALL turns end to end. Used by
# q23, q24, q30.
conversation:
  - { question: "Did Daddy eat dinner today?" }
  - { question: "What about yesterday?" }
```

## Adding a new fixture

1. Pick the next `q{NN}_short_slug.yaml` filename. The slug is the
   single source of truth for the human-readable id.
2. Write the question the way a real household member would ask it.
   Avoid "test data" phrasing.
3. Decide if the question needs the analyzer. The system prompt tells
   the LLM to call `query_observations` first; the fixture should
   reflect that ordering.
4. Fill `seed` with the minimum data the tool calls need to look
   real. A package-detection fixture should include a `package` label
   and a description that mentions a package.
5. Script every LLM turn including the final synthesis. Under-scripted
   fixtures fail loudly with `MockLLMClient exhausted`.
6. Set `expected` tightly. `final_answer_contains` should be the
   2-3 words that prove the model got the right answer; tighter checks
   on `tools_called` + `cost_cents_max` catch regressions later.

## Interpreting the report

Each run produces `.eval-report.md` at the repo root.

```
# Agent Eval Report

## Summary
- Passed. 28 / 30
- Failed. 2
- Skipped. 0
- Cost. $0.00 (mocked)
- Avg turns per fixture. 2.6
- Phase 1 exit threshold. 27 / 30
- Threshold met. Yes

## Failures
- q15_disambiguation_two_johns. Final_answer missing substring 'two'; got 'I found people...'
- q22_camera_access_denied. Expected zero results, got 1

## By tag
- indexed. 8/8
- analyzer. 5/5
- failure_mode. 7/9
- ...
```

`Threshold met. No` makes the workflow fail. CI also uploads the file
as a build artifact and (for pull requests) posts it as a PR comment
so reviewers do not have to dig through job logs.

## Currently expected to skip or fail

Until Wave 2A lands the real driver every fixture runs through
`MockDriver`. Two classes of fixture exercise features that depend on
the real driver's behavior and may need touch-up once it lands.

- `q19_budget_exhausted`. The mock driver checks `budget_cents` before
  each turn. The real driver should call the same hook from
  `services.agent.budget.check_user_budget`; if the field name there
  differs we will need to map them.
- `q23_followup_yesterday` + `q24_clarification_then_answer`. The
  mock driver treats `parent_run_id` as an opaque token; the real
  driver loads parent context to ground "yesterday" / "this morning"
  phrases. Today the YAML is fully scripted so the test passes as a
  contract check on the multi-turn API; the real driver landing
  upgrades it to a real grounding test.

All thirty fixtures should pass under the mocked harness on day one.
When the real driver lands, re-run the suite in real-LLM mode against
a staging key to validate end-to-end.
