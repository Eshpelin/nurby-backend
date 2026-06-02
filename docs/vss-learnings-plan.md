# Nurby. Learnings from NVIDIA VSS, and how we adopt them

## Why this doc exists

We compared Nurby to the [NVIDIA Video Search and Summarization
blueprint](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization).
Same problem space, opposite target. VSS is a GPU-mandatory enterprise
reference stack (DeepStream, Cosmos NIM models, multi-node Helm
deploy). Nurby is a self-hostable, CPU-OK, single-household,
provider-agnostic, privacy-first product.

The pipeline shape is nonetheless the same, and VSS validates five
choices we can adopt without giving up our moat. This doc turns those
five learnings into concrete, scoped Nurby buildables grounded in the
tables and modules we already have.

We do NOT copy their infra. No Milvus, no Neo4j, no Kafka, no DeepStream.
Everything below reuses Postgres + pgvector + Redis + the agent tool
registry + the VLM analyzer we already shipped.

---

## What VSS does that we should steal (ranked)

| # | Learning | Effort | Value | Phase |
|---|----------|--------|-------|-------|
| 1 | Alert verification stage (VLM-confirm before notify) | Low | High | **v1.5** |
| 2 | Relationship / graph query for the agent | Low-Med | High | **v1.5** |
| 3 | MCP server wrapping our tools | Low-Med | High strategic | **v1.6** |
| 4 | Map-reduce long-window summarization | Med | Med | **v1.7** |
| 5 | Video / CLIP native embeddings | High | Med | **v2 (already Phase 3 in agent-design.md)** |

What we keep that VSS lacks. CPU-only operation, provider-agnostic VLM
chain, pre-VLM privacy redaction, frame-level eternal cache, one-line
self-host. None of the work below regresses any of these.

---

## Learning 1. Alert verification stage

### The VSS idea
VSS runs a dedicated Alert Verification Service. Cheap perception fires
a candidate alert, a VLM verifies the candidate before the alert is
published downstream. Kills false positives (shadow flagged as person,
swaying branch flagged as intruder).

### Where Nurby is today
`services/events/actions.py` dispatches action types. Webhook, api_call,
broadcast, notify, email, vlm_call, telegram. A rule fires on a YOLO
label or face match and immediately runs its actions. There is a
cascade refiner in the VLM queue, but no verify-before-notify gate on
the rule action chain. Our cat-eating example ("we already know the cat
ate because the rule fired") is exactly the pattern VSS formalizes,
except today our rule fires on raw detection, not a verified one.

### What we build
A new action type **`verify`** plus an opt-in rule flag that makes the
action chain conditional on a VLM confirmation.

- New action shape.
  ```json
  {
    "type": "verify",
    "question": "Is there actually a person at the door, not a shadow or reflection?",
    "provider_id": "uuid (optional, defaults to camera provider)",
    "min_confidence": 0.6,
    "on_fail": "stop"   // stop | continue
  }
  ```
- Runtime. `_execute_verify` resolves the triggering observation's
  thumbnail (or a fresh frame), calls
  `services.agent.analyzer.analyze_frame_target` with the verify
  question + the existing `ANALYZER_RESPONSE_SCHEMA` (verdict +
  confidence + cannot_tell). We already built this analyzer with the
  eternal frame cache, so a repeat verification of the same frame is
  free.
- Gate. If `verdict != "yes"` OR `confidence < min_confidence` OR
  `verdict == "cannot_tell"`, and `on_fail == "stop"`, abort the rest
  of the action chain. The engine already supports chain-abort
  (`RuntimeError` from vlm_call breaks the loop) so this reuses the
  same control flow.
- Audit. The verification result lands on the `Event.payload` so the
  timeline + the agent can see "fired but VLM rejected, suppressed."

### Why this is cheap for us
- The analyzer + response schema + frame cache already exist
  (`services/agent/analyzer.py`).
- The action chain already supports an early-abort signal.
- The rule builder already has a multi-action editor (v1.2 work), so
  the `verify` action drops into the existing card UI.

### Deliverables
1. `_execute_verify` in `services/events/actions.py` + add `verify`
   to `_VALID_ACTION_TYPES` in `shared/schemas.py`.
2. Schema validation. `verify` requires `question`, optional
   `min_confidence` (0-1), `on_fail` in {stop, continue}.
3. Rule builder. New `VerifyEditor.tsx` action card under
   `frontend/src/components/rules/actions/`.
4. Engine. Honor the abort signal from a failed verify the same way
   it honors `vlm_call` `on_error=stop`.
5. Tests. Verify passes → chain continues. Verify fails →
   chain aborts. Cannot_tell → treated as fail. Cached frame → no
   second VLM call.
6. Plain-language preview. "When a person is detected on Front Door,
   confirm with AI that it is really a person, then send Telegram."

### Exit criterion
A rule with a verify stage suppresses a known false-positive
(swaying-branch fixture) and passes a known true-positive (person
fixture) in the eval suite.

---

## Learning 2. Relationship / graph query for the agent

### The VSS idea
VSS does CA-RAG (context-aware retrieval) over a knowledge graph.
entities are nodes, events are edges, time is a dimension. This answers
relational questions flat vector search cannot. "Did the same vehicle
from Tuesday return Friday?" "Who was with Dad in the kitchen?"

### Where Nurby is today
We already have the graph. It is just relational rows we do not expose
to the agent relationally.

- `Person` ←→ `Journey` (subject_key) ←→ `Incident` ←→ `Observation`
- `Person` ←→ `FaceCluster` / `BodyCluster`
- `Journey.segments` + `Journey.transitions` JSON already encode the
  camera-to-camera path and movement gaps.
- `Event` ←→ `Rule` ←→ `Observation`.

Our agent tools (`query_observations`, `get_journeys`, `get_events`,
`summarize_activity`, etc) each query one entity type. None traverse
the relationships between them. The LLM has to stitch multiple tool
calls together, which small models do poorly.

### What we build
A new agent tool **`query_relationships`** that answers a bounded set
of relational question shapes with a single DB round-trip, by walking
the existing foreign keys + JSON segment data.

- Schema.
  ```json
  {
    "subject": "person_name | person_id | label (cat/car/...)",
    "relation": "co_present_with | revisited | path | seen_with_label | transitions",
    "object": "optional second subject for co_present_with",
    "hours": 168,
    "limit": 50
  }
  ```
- Relations to support in v1.5 (each is a SQL join or JSON walk, no new
  tables).
  - `co_present_with`. Persons/labels whose Journeys overlap in time on
    the same camera. Answers "who was with Dad?"
  - `revisited`. Same subject_key with two Journeys separated by a gap.
    Answers "did the delivery guy come back?" using BodyCluster identity
    even without a face.
  - `path`. Ordered camera transitions for a Journey from
    `Journey.transitions`. Answers "where did the cat go after the
    kitchen?"
  - `seen_with_label`. Observations where a Person's Journey window
    overlaps a detection of a given label. Answers "was Dad ever seen
    with the dog?"
  - `transitions`. Camera-to-camera movement gaps across all Journeys
    in the window. Answers "what's the usual path through the house?"
- All results filtered through `accessible_camera_ids` (same ACL helper
  every tool already uses).

### Why this is cheap for us
- Zero new tables. Pure joins + JSON traversal over Journey / Incident /
  Observation / Person / BodyCluster that already exist.
- The tool registry already converts JSON-schema tools to each provider
  dialect, so adding one tool is one registry entry.
- The driver, budget, audit, streaming all work unchanged.

### Deliverables
1. `query_relationships` in `services/agent/tools.py` + registry entry.
2. Per-relation SQL/JSON resolver functions.
3. Disambiguation reuse (same convention as `get_journeys`).
4. System-prompt nudge. "For 'who was with X', 'did X come back',
   'where did X go', use query_relationships before stitching multiple
   tools."
5. Eval fixtures. Co-presence, revisit-by-body-cluster, camera path.

### Exit criterion
The eval suite answers "did the same person come back later today?"
via a single `query_relationships(revisited)` call instead of
N `get_journeys` calls.

---

## Learning 3. MCP server wrapping our tools

### The VSS idea
VSS exposes its video-agent capabilities over the Model Context Protocol
so any MCP client (Claude Desktop, third-party agents) can drive video
search / Q&A without VSS's own UI.

### Where Nurby is today
Our 9 agent tools live behind FastAPI + our `/ask` UI. The tool registry
in `services/agent/tools.py` already emits clean JSON schemas and a
`ctx`-based call convention. We are one thin adapter away from exposing
them as an MCP server.

### What we build
A standalone **Nurby MCP server** (`services/mcp/server.py`) that
re-exports the existing tool registry over MCP stdio + HTTP transports.

- Each MCP tool maps 1:1 to a `TOOL_REGISTRY` entry. We already have
  name, description, input_schema.
- Auth. An MCP-issued token scoped to a Nurby user, so
  `accessible_camera_ids` filtering still applies. No tool bypasses
  the household ACL.
- Budget. MCP calls count against the same per-user daily token /
  cost budget as `/ask` (reuse `services/agent/budget.py`).
- Read-only by default. Only `side_effect: read` tools are exposed
  over MCP in v1.6. The verify / write tools stay internal until we
  have a confirmation flow for external clients.
- Distribution. A `docker compose` profile + a documented
  `claude_desktop_config.json` snippet so a user can point Claude
  Desktop at their own Nurby.

### Why this is high strategic value
"Your cameras, queryable from any AI app you already use." A user can
ask Claude Desktop "what did Nurby see at the front door today?" with no
Nurby UI open. This is a differentiator VSS only offers to enterprise;
we offer it to a household on a NUC.

### Why it is cheap for us
- The registry is already schema-first and `ctx`-based.
- The MCP Python SDK maps tool defs → MCP tools mechanically.
- No change to the driver, analyzer, or storage.

### Deliverables
1. `services/mcp/server.py` exposing read tools over MCP.
2. Token-scoped auth bridging to a Nurby user + ACL.
3. Budget enforcement reuse.
4. Compose profile `mcp` + docs in `docs/mcp.md` with the Claude
   Desktop config snippet.
5. Smoke. Start the MCP server, list tools, call `summarize_activity`
   through an MCP client, get a household rollup back.

### Exit criterion
A user adds Nurby to Claude Desktop with one config block and asks
"who was at the door today?" answered from their own cameras, ACL
respected, budget counted.

---

## Learning 4. Map-reduce long-window summarization

### The VSS idea
For long video, VSS chunks → dense caption per chunk → hierarchical
aggregate (map-reduce) → final summary. Bounded context per step, scales
to hours.

### Where Nurby is today
`summarize_activity` is a single DB pass over rollups (great for "today")
and `daily_digest` aggregates 24h. Neither scales to "summarize the last
7 days of the front door" without blowing the LLM context window. We
already have the building blocks. `Incident` + `Journey` rows are
natural chunks, and the VLM analyzer + frame cache handle per-chunk
captioning.

### What we build
A **hierarchical summarizer** invoked when a requested summary window
exceeds a threshold.

- Map. Partition the window into chunks (by hour, or by Incident/Journey
  boundary which is more semantic). Produce a mini-summary per chunk from
  the rollup + any cached VLM captions. Cheap, no new VLM calls when
  cache hits.
- Reduce. Fold mini-summaries pairwise / in batches into a final
  narrative, each reduce step bounded to a safe token budget.
- Surface. A new agent path (the driver detects a large-window summary
  request and runs the map-reduce loop) + reuse in `daily_digest` for
  weekly / monthly digests.

### Deliverables
1. `services/agent/summarizer.py` with `summarize_window(hours)` doing
   chunk → map → reduce.
2. Chunk boundary by Incident/Journey when available, else hourly.
3. Token-budget-aware reduce (respect `agent_max_*` settings).
4. Driver. Route "summarize the last N days" to this path.
5. Tests. A 7-day fixture produces a bounded-context summary that
   names the top entities + events per day.

### Exit criterion
"Summarize the last week at the front door" returns a coherent
day-by-day narrative without exceeding the per-run token budget.

---

## Learning 5. Video / CLIP native embeddings

### The VSS idea
Cosmos-Embed1 embeds video clips directly, so search matches motion +
temporal patterns, not just a text caption of a frame.

### Where Nurby is today
We embed the VLM's text description (`Observation.description_embedding`,
Vector(384)) plus body-reid OSNet vectors. "Show me someone running"
fails when the caption said "person in yard." This is exactly the
image-embedding item already parked in `docs/agent-design.md` Phase 3.

### What we build (deferred to v2)
- Add a CLIP image embedding per keyframe (reuse the ViT-B-32 we just
  pulled in for the v1.4 CLIP gate. No new model download).
- New `image_embeddings` table + pgvector index.
- New agent tool `search_visual(query)` doing CLIP text→image cosine,
  so visual concepts never described in text become searchable.

### Why deferred
Higher effort, and the CLIP gate (v1.4H) already proves the model loads
+ runs CPU-acceptably. We bank the dependency now, build the search
surface after the cheaper v1.5/v1.6 wins land. VSS validates it is worth
doing eventually, not that it is the next thing.

### Exit criterion (v2)
"Show me someone running" returns clips matched by CLIP image
similarity even when no caption mentioned running.

---

## What we deliberately do NOT adopt

- **Milvus / Neo4j / Kafka / DeepStream.** Our Postgres + pgvector +
  Redis stack covers every learning above without new infra. Adding a
  graph DB to answer relational questions our foreign keys already
  encode would be pure complexity.
- **GPU-mandatory models (Cosmos NIM).** Our provider-agnostic chain +
  the v1.3/v1.4 CPU-survival work (backlog, dedupe, CLIP gate) is the
  whole point. We stay CPU-viable.
- **Their always-on dense-caption-every-chunk model.** Our motion gate +
  pHash dedupe + CLIP gate + eternal frame cache mean we caption far
  fewer frames. We get the same retrieval quality at a fraction of the
  compute.

---

## Rollout sequence

```
v1.5  (this sprint)   Learning 1 (verify action)  +  Learning 2 (relationship tool)
v1.6  (next)          Learning 3 (MCP server)
v1.7  (after)         Learning 4 (map-reduce summary)
v2    (roadmap)       Learning 5 (CLIP image search)
```

Each item is independently shippable, reuses existing substrate, and
adds zero new infrastructure. The first two are small because the
analyzer, frame cache, action chain, tool registry, ACL helper, budget,
and audit are all already in place.

## Suggested first commits (v1.5)

1. `feat(rules): verify action stage with vlm confirmation gate`
2. `feat(agent): query_relationships tool (co-presence, revisit, path)`

Both land behind tests + eval fixtures. Neither touches the perception
hot path or the deploy story.
