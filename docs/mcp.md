# Nurby MCP server

Query your Nurby cameras from any MCP client (Claude Desktop, third-party
agents) without opening the Nurby UI. The server re-exports Nurby's
existing read-only agent tools over the
[Model Context Protocol](https://modelcontextprotocol.io). Ask Claude
Desktop "who was at the front door today?" and it answers from your own
cameras.

This implements Learning 3 (v1.6) from `docs/vss-learnings-plan.md`.

## What it is

A thin transport adapter (`services/mcp/server.py`) over the agent
`TOOL_REGISTRY`. No new tool logic. Each MCP tool maps 1:1 to a registry
entry. The server is scoped to a single Nurby user by a token, so the
camera ACL (`accessible_camera_ids`) still applies to every call. Only
read-only tools are exposed.

## How to get a token

The MCP server authenticates with a normal Nurby JWT, the same token a
logged-in browser session uses:

1. Log in to Nurby in your browser.
2. Open the browser dev tools, Application / Storage, and copy the access
   token your session stores (the value sent as the
   `Authorization: Bearer <token>` header on API calls). Alternatively,
   any token minted by `shared.auth.create_access_token` for your user
   works.
3. Treat it like a password. It grants read access to every camera your
   user can see, counted against your daily budget.

The token scopes the whole server to that one user. To query a different
household member's cameras, launch a separate server with their token.

## Run it

### Local (stdio, for Claude Desktop)

```bash
NURBY_MCP_TOKEN=<your-jwt> \
DATABASE_URL=postgresql+asyncpg://nurby:nurby_dev@localhost:5432/nurby \
REDIS_URL=redis://localhost:6379/0 \
python -m services.mcp.server
```

stdio is the default transport. Claude Desktop launches the process and
talks to it over stdin/stdout.

### Docker Compose (behind the `mcp` profile)

The `mcp` service does NOT start with a plain `docker compose up`. Start
it explicitly:

```bash
NURBY_MCP_TOKEN=<your-jwt> docker compose --profile mcp up mcp
```

### HTTP transport (optional)

Set `NURBY_MCP_HTTP=1` (or pass `--http`) to serve over Streamable HTTP on
port 4749 instead of stdio, when the installed MCP SDK supports it. Stdio
is the must-have default; HTTP is best-effort:

```bash
NURBY_MCP_HTTP=1 NURBY_MCP_TOKEN=<your-jwt> python -m services.mcp.server --http --port 4749
```

## Claude Desktop config snippet

Add this to your `claude_desktop_config.json` (on macOS,
`~/Library/Application Support/Claude/claude_desktop_config.json`).

```json
{
  "mcpServers": {
    "nurby": {
      "command": "python",
      "args": ["-m", "services.mcp.server"],
      "env": {
        "NURBY_MCP_TOKEN": "PASTE_YOUR_NURBY_JWT_HERE",
        "DATABASE_URL": "postgresql+asyncpg://nurby:nurby_dev@localhost:5432/nurby",
        "REDIS_URL": "redis://localhost:6379/0"
      }
    }
  }
}
```

Run from the repo root (or set `PYTHONPATH` to it) so
`python -m services.mcp.server` resolves. To run the Docker image instead,
swap `command`/`args` for a `docker run` invocation that passes the same
env and mounts the thumbnails volume read-only.

After saving, restart Claude Desktop. Nurby's read tools appear in the
tool picker.

## Tools exposed

All read-only agent tools. Write / action tools are intentionally NOT
exposed in v1.6.

| Tool | What it answers |
|------|-----------------|
| `query_observations` | Semantic + filter search over indexed observations |
| `get_journeys` | Cross-camera Person sighting sessions |
| `get_camera_layout` | Static camera inventory with inferred roles |
| `get_household_snapshot` | One-call orientation (cameras, persons, active journeys) |
| `get_last_sightings` | Last-seen-at per Person and per common label (30d) |
| `get_events` | Rule firings (Events) over a time window |
| `summarize_activity` | Pre-aggregated "what happened today?" rollup |
| `list_rules` | Automation rules (id, name, enabled, trigger type) |
| `get_incidents` | Curated incident clusters with VLM summaries |
| `get_daily_digest` | The pre-computed household daily digest |
| `query_relationships` | Co-presence, revisit, path, seen-with-label, transitions |
| `analyze_clip` | VLM over a video window (expensive, cached forever) |
| `analyze_frame` | VLM over one observation's thumbnail (cached forever) |

## Which tool answers which question

A decision guide for clients (and humans) choosing a tool:

| You want to know | Call | Why |
|---|---|---|
| "What happened today, overall?" | `get_daily_digest`, else `summarize_activity` | Digest is pre-computed; zero analysis cost |
| "Where was Mom last seen?" | `get_last_sightings` | Cheapest per-entity freshness check |
| "What was Simon doing all day?" | `get_journeys` (person_name, hours=24) | Cross-camera sessions beat raw observations |
| "Was there a package at the door at 3pm?" | `query_observations` (labels, window) | Indexed search over detections + captions |
| "Did my porch rule fire? How often?" | `list_rules` then `get_events` | Rule firings are confirmed happenings; never re-analyze to count |
| "Anything unusual this week?" | `get_incidents` (hours=168) | Curated clusters with summaries |
| "Who visits together? Usual paths?" | `query_relationships` | Co-presence / transition analytics |
| "Is that a golden retriever in this frame?" | `analyze_frame` | VLM on one thumbnail (medium cost) |
| "Watch 4-5pm and tell me if the gate opened" | `analyze_clip` | VLM over a window. The expensive last resort |

Cost intuition: everything above the `analyze_*` rows is a cheap indexed
read. `analyze_frame` is one VLM call; `analyze_clip` samples several
frames per call. Both are cached forever per (target, question), so
repeating a question is free.

## Time and timezone handling

Tool time windows are **relative hours** (`hours=24`) resolved against
the server clock, and timestamps in results are ISO-8601 with offsets.
The household's display timezone is the `system_timezone` app setting;
when a client asks for "yesterday evening", it should convert to an
explicit window or pass a wide-enough `hours` and filter on the
returned timestamps. The agent's own prompt grounds "today" in
`system_timezone`; external MCP clients must do their own grounding.

## Troubleshooting

- **`invalid or expired token` at startup.** Mint a fresh token (they
  carry the standard JWT expiry) and restart the server; the token is
  read once at launch.
- **Tools list is empty.** The SDK handshake succeeded but the token's
  user resolved to nothing. Check the token was minted for an active
  user on this same Nurby instance (same `JWT_SECRET`).
- **`budget exhausted` errors.** The user's daily agent budget is spent.
  Raise `agent_daily_token_budget_per_user` /
  `agent_daily_cost_cents_per_user` in Settings, or wait for the daily
  reset. Pure reads do not consume budget; only `analyze_*` calls do.
- **Results truncated.** List tools cap at their `limit` (default 20-25,
  max 100-200). Pass a higher `limit` or narrow the window rather than
  assuming the result set is complete.
- **Empty results for something that definitely happened.** Widen the
  window: tools default to 24h. Retry with `hours=168`, then `720`.

## Security notes

- **Token-scoped to one user.** The launch token decodes to a single
  Nurby user; every call runs as that user.
- **ACL respected.** Each tool funnels results through
  `accessible_camera_ids`, exactly as `/ask` does. No tool bypasses the
  household camera ACL.
- **Read-only.** Only `side_effect == "read"` registry entries are
  exposed. There is no write surface over MCP.
- **Budget counted.** Before any tool runs, the server checks the user's
  daily budget (`services.agent.budget.check_budget`). When the budget is
  exhausted the call returns a clear error and the tool does not run.
  Pure reads record no token usage. The gate is the enforcement. The
  `analyze_*` tools do their own internal VLM budget accounting when they
  actually call a model.

## Limitations

- **No write tools.** The verify action and any future write / action
  tools stay internal until there is a confirmation flow for external
  clients. V1.6 is read-only by design.
- **One token per server.** The token is read once at launch. Refreshing
  it requires restarting the server. Launch a separate server per user.
- **HTTP transport is best-effort.** The exact Streamable-HTTP app symbol
  has churned across MCP SDK versions. Stdio is the supported path.
