"""Nurby MCP server. read-only agent tools over the Model Context Protocol.

This module re-exports the existing agent ``TOOL_REGISTRY`` (defined in
``services.agent.tools``, never modified here) over MCP so any MCP client
(Claude Desktop and friends) can query a household's cameras.

Design.

* Token-scoped. A single Nurby JWT, read from ``NURBY_MCP_TOKEN`` at
  launch, is decoded to a user_id. The resolved ``User`` is placed in the
  per-call ``ctx``, so every tool's ``accessible_camera_ids`` ACL applies
  exactly as it does inside ``/ask``. No tool can see data outside that
  user's scope.
* Read-only. Only ``side_effect == "read"`` registry entries are exposed.
  Write / action tools stay internal in v1.6.
* Budget. Before running any tool we call ``check_budget``. If the user's
  daily budget is exhausted we return a clean MCP error and do NOT run
  the tool. We do NOT record token usage for pure tool reads. the tools
  themselves do no LLM work (only the ``analyze_*`` tools call a VLM, and
  those are gated by the analyzer's own internal budget accounting).
* No new tool logic. This is a thin transport adapter over the registry.

The SDK-independent core (auth resolution, tool listing, dispatch) lives
in plain functions so it is unit-testable without the ``mcp`` package
installed. The ``mcp`` SDK is only imported inside ``build_server`` and
``main`` so importing this module never requires the SDK.

SDK API (VERIFIED against the installed package, mcp 1.27.2):
  * Package import name is ``mcp`` (PyPI ``mcp>=1.2.0``).
  * Low-level server. ``from mcp.server import Server``. [confirmed]
  * stdio transport. ``from mcp.server.stdio import stdio_server`` yielding
    ``(read_stream, write_stream)``. [confirmed]
  * Types. ``from mcp.types import Tool, TextContent``. ``Tool`` takes
    ``name``, ``description``, ``inputSchema`` (camelCase. confirmed via
    ``Tool.model_fields``). ``TextContent`` takes ``type="text"`` and
    ``text=...``. [confirmed]
  * Handlers registered via ``@server.list_tools()`` returning
    ``list[Tool]`` and ``@server.call_tool()`` returning
    ``list[TextContent]``. ``server.run(read, write, init_options)`` with
    ``init_options`` from ``server.create_initialization_options()``.
    [confirmed: list_tools/call_tool/create_initialization_options all
    present; ListToolsRequest handler registers]
  The two SDK-dependent tests now run (no longer importorskip) and pass.
  The HTTP transport factory path (streamable_http) still varies across
  versions and stays best-effort; stdio is the supported transport.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from services.agent.budget import check_budget
from services.agent.tools import TOOL_REGISTRY, get_tool
from services.guardian.mcp_tools import GUARDIAN_MCP_TOOLS, get_guardian_tool
from shared.auth import decode_access_token
from shared.database import async_session
from shared.models import User

logger = logging.getLogger("nurby.mcp.server")

SERVER_NAME = "nurby"
DEFAULT_HTTP_PORT = 4749
TOKEN_ENV = "NURBY_MCP_TOKEN"


# ── Errors ────────────────────────────────────────────────────────────


class McpAuthError(RuntimeError):
    """Raised when the launch token is missing or does not resolve to an
    active Nurby user."""


# ── Read-only tool surface ────────────────────────────────────────────


def read_tools() -> list[dict[str, Any]]:
    """The subset of ``TOOL_REGISTRY`` we expose over MCP.

    Only ``side_effect == "read"`` entries. ``analyze_clip`` /
    ``analyze_frame`` are read per their registry entry, so they ARE
    included. Write / action tools (none exist in the registry today;
    ``verify`` is a rule action, not an agent tool) are excluded.
    """
    return [t for t in TOOL_REGISTRY if t.get("side_effect") == "read"]


def read_tool_names() -> list[str]:
    return [t["name"] for t in read_tools()]


def tool_definitions() -> list[dict[str, Any]]:
    """MCP-compatible tool definitions. ``name`` + ``description`` +
    ``input_schema`` (the same Draft JSON schema the Anthropic dialect
    uses, so it drops straight into an MCP ``Tool``)."""
    return [
        {
            "name": t["name"],
            "description": t["description"],
            "input_schema": t["input_schema"],
        }
        for t in (read_tools() + GUARDIAN_MCP_TOOLS)
    ]


# ── Auth ──────────────────────────────────────────────────────────────


async def resolve_user(token: str | None, db: Any) -> User:
    """Decode an MCP launch token to its Nurby ``User``.

    Raises ``McpAuthError`` with a clear message when the token is
    missing, malformed, or does not resolve to an active user. The token
    is a normal Nurby JWT the user copies from a logged-in session.
    """
    if not token:
        raise McpAuthError(
            f"{TOKEN_ENV} is not set. Paste a Nurby JWT from a logged-in "
            "session into the MCP server environment."
        )
    user_id = decode_access_token(token)
    if user_id is None:
        raise McpAuthError(
            f"{TOKEN_ENV} is not a valid Nurby token (decode failed or "
            "expired). Copy a fresh token from your Nurby session."
        )
    user = await db.get(User, user_id)
    if user is None or not getattr(user, "is_active", False):
        raise McpAuthError(
            "The Nurby user for this token was not found or is "
            "deactivated."
        )
    return user


def _token_from_env() -> str | None:
    return os.environ.get(TOKEN_ENV)


# ── Dispatch ──────────────────────────────────────────────────────────


async def dispatch_tool_call(
    name: str,
    arguments: dict[str, Any] | None,
    *,
    token: str | None = None,
) -> dict[str, Any]:
    """Run one read tool, fully scoped to the launch token's user.

    Returns a plain dict. On success ``{"ok": True, "result": <dict>}``.
    On any handled failure ``{"ok": False, "error": <str>, "kind": <str>}``.
    Never raises. the caller wraps the dict into an MCP ``TextContent``.

    Steps. (a) resolve the user from the token, (b) open a request-scoped
    session, (c) build ``ctx``, (d) ``check_budget`` gate, (e) look up the
    tool and confirm it is read-only, (f) call it, (g) return the result.
    """
    arguments = arguments or {}

    async with async_session() as db:
        # (a) auth
        try:
            user = await resolve_user(token, db)
        except McpAuthError as exc:
            return {"ok": False, "error": str(exc), "kind": "auth"}

        # (e) tool lookup + read-only guard happens before budget so an
        # unknown-tool call fails fast with the right error. Guardian-scoped
        # tools live in a separate registry; they self-scope to the user's
        # links so they need no camera ACL.
        tool = get_tool(name) or get_guardian_tool(name)
        if tool is None:
            return {
                "ok": False,
                "error": f"unknown tool: {name!r}",
                "kind": "unknown_tool",
            }
        if tool.get("side_effect") != "read":
            return {
                "ok": False,
                "error": (
                    f"tool {name!r} is not read-only and is not exposed "
                    "over MCP"
                ),
                "kind": "not_exposed",
            }

        # (d) budget gate. no token recording for pure reads; the gate
        # alone enforces the per-user daily cap.
        try:
            status = await check_budget(user.id, db)
        except Exception:  # noqa: BLE001
            logger.exception("budget check failed for user %s", user.id)
            return {
                "ok": False,
                "error": "budget check failed",
                "kind": "budget_error",
            }
        if not status.ok:
            return {
                "ok": False,
                "error": (
                    f"daily budget exhausted ({status.reason}). try again "
                    "tomorrow or raise the per-user budget in Nurby "
                    "settings."
                ),
                "kind": "budget_exhausted",
            }

        # (b)+(c) ctx with a real User so the camera ACL applies.
        ctx = {"user": user, "run_id": None, "db": db}

        # (f) run the tool. any tool error is returned cleanly rather
        # than crashing the server.
        try:
            result = await tool["fn"](ctx, **arguments)
        except TypeError as exc:
            return {
                "ok": False,
                "error": f"bad arguments for {name!r}: {exc}",
                "kind": "bad_arguments",
            }
        except Exception as exc:  # noqa: BLE001
            logger.exception("tool %s failed", name)
            return {
                "ok": False,
                "error": f"tool {name!r} failed: {exc}",
                "kind": "tool_error",
            }

        return {"ok": True, "result": result}


def serialize_result(payload: dict[str, Any]) -> str:
    """JSON text for an MCP ``TextContent``. ``default=str`` so UUIDs and
    datetimes serialize."""
    return json.dumps(payload, default=str)


# ── MCP SDK wiring (imported lazily) ──────────────────────────────────


def build_server() -> Any:
    """Build and return the configured low-level MCP ``Server``.

    Tests use this to introspect the registered tools without launching
    a transport. Importing the ``mcp`` SDK happens here, not at module
    import, so the dispatch / auth / budget logic stays importable (and
    testable) without the SDK installed.

    Validates the launch token eagerly so a misconfigured server fails
    fast with a clear message at startup rather than on the first call.
    """
    # VERIFIED (mcp 1.27.2): low-level Server + types live at these paths.
    from mcp.server import Server  # type: ignore
    from mcp.types import TextContent, Tool  # type: ignore

    # Fail fast on a missing token so the operator sees the problem at
    # launch. We only check presence here. full resolution needs a DB
    # session and happens per call.
    if not _token_from_env():
        raise McpAuthError(
            f"{TOKEN_ENV} is not set. The Nurby MCP server needs a Nurby "
            "JWT to scope tool calls to one user. See docs/mcp.md."
        )

    server = Server(SERVER_NAME)

    @server.list_tools()  # VERIFIED (mcp 1.27.2): decorator returns list[Tool]
    async def _list_tools() -> list[Any]:
        return [
            Tool(
                name=d["name"],
                description=d["description"],
                inputSchema=d["input_schema"],  # VERIFIED (mcp 1.27.2): camelCase field
            )
            for d in tool_definitions()
        ]

    @server.call_tool()  # VERIFIED (mcp 1.27.2): (name, arguments) -> list[content]
    async def _call_tool(name: str, arguments: dict[str, Any] | None) -> list[Any]:
        payload = await dispatch_tool_call(
            name, arguments, token=_token_from_env()
        )
        return [TextContent(type="text", text=serialize_result(payload))]

    return server


async def run_stdio() -> None:
    """Serve over stdio. the must-have transport for Claude Desktop."""
    # VERIFIED (mcp 1.27.2): stdio_server yields (read_stream, write_stream).
    from mcp.server.stdio import stdio_server  # type: ignore

    server = build_server()
    async with stdio_server() as (read_stream, write_stream):
        # VERIFIED (mcp 1.27.2): create_initialization_options() exists.
        init_options = server.create_initialization_options()
        await server.run(read_stream, write_stream, init_options)


async def run_http(port: int = DEFAULT_HTTP_PORT) -> None:
    """Serve over Streamable HTTP when the SDK supports it.

    Optional transport. stdio is the must-have. If the installed SDK does
    not expose a streamable-http app we raise a clear error pointing the
    operator back at stdio.
    """
    server = build_server()
    try:
        # MCP-SDK-ASSUMPTION: streamable HTTP app factory lives here in
        # recent SDK releases. Symbol name has churned across versions, so
        # we probe a couple of known spellings and fall back to a clear
        # message rather than guessing wrong.
        import uvicorn  # type: ignore

        app = None
        try:
            from mcp.server.streamable_http import (  # type: ignore
                create_streamable_http_app,
            )

            app = create_streamable_http_app(server)
        except Exception:  # noqa: BLE001
            # Older / different SDK layout. try the FastMCP-style helper.
            try:
                app = server.streamable_http_app()  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                app = None

        if app is None:
            raise RuntimeError(
                "The installed mcp SDK does not expose a streamable-HTTP "
                "app under a name this server knows. Use stdio "
                "(the default) instead, or pin an mcp version that ships "
                "create_streamable_http_app. See the MCP-SDK-ASSUMPTION "
                "notes in services/mcp/server.py."
            )

        config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
        await uvicorn.Server(config).serve()
    except ImportError as exc:
        raise RuntimeError(
            f"HTTP transport needs uvicorn + a streamable-http-capable mcp "
            f"SDK. {exc}. Falling back to stdio is recommended."
        ) from exc


def main() -> None:
    """Entrypoint. ``python -m services.mcp.server``.

    stdio by default. Set ``NURBY_MCP_HTTP=1`` (or pass ``--http``) to
    serve over HTTP on ``NURBY_MCP_PORT`` (default 4749) when the SDK
    supports it.
    """
    import argparse
    import asyncio

    parser = argparse.ArgumentParser(prog="nurby-mcp", description=__doc__)
    parser.add_argument(
        "--http",
        action="store_true",
        help="serve over Streamable HTTP instead of stdio (SDK permitting)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("NURBY_MCP_PORT", DEFAULT_HTTP_PORT)),
        help=f"HTTP port (default {DEFAULT_HTTP_PORT})",
    )
    args = parser.parse_args()

    use_http = args.http or os.environ.get("NURBY_MCP_HTTP") == "1"

    logging.basicConfig(level=logging.INFO)
    if use_http:
        asyncio.run(run_http(args.port))
    else:
        asyncio.run(run_stdio())


if __name__ == "__main__":
    main()
