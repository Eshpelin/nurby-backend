"""Agentic Q&A subsystem. See docs/agent-design.md.

Wave 1A owns the schema + budget + runs lifecycle. Wave 1B owns the tool
registry + privacy redaction + user access filter. Wave 1C owns the VLM
analyzer callables. Each wave loads independently of the others. The
re-exports below try-import so this package keeps building while sibling
modules are still in flight.
"""

from services.agent.access import accessible_camera_ids

__all__ = ["accessible_camera_ids"]

# Wave 1B. privacy + tool registry. Optional until that wave lands.
try:
    from services.agent.privacy import RedactionReport, redact_frame  # noqa: F401

    __all__ += ["redact_frame", "RedactionReport"]
except Exception:  # pragma: no cover
    pass

try:
    from services.agent.tools import (  # noqa: F401
        TOOL_REGISTRY,
        all_tools_for_provider,
        get_tool,
    )

    __all__ += ["TOOL_REGISTRY", "get_tool", "all_tools_for_provider"]
except Exception:  # pragma: no cover
    pass

# Wave 1A. budget + runs lifecycle. Always available once the v1 schema
# migration lands.
try:
    from services.agent.budget import (  # noqa: F401
        BudgetStatus,
        check_budget,
        estimate_cost,
        record_usage,
    )

    __all__ += ["BudgetStatus", "check_budget", "estimate_cost", "record_usage"]
except Exception:  # pragma: no cover
    pass

try:
    from services.agent import runs  # noqa: F401

    __all__ += ["runs"]
except Exception:  # pragma: no cover
    pass

# Wave 1C. analyzer entry points. Optional until that wave lands.
try:
    from services.agent.analyzer import (  # noqa: F401
        ANALYZER_RESPONSE_SCHEMA,
        ANALYZER_SYSTEM_PROMPT,
        AnalyzerResult,
        analyze_clip_target,
        analyze_frame_target,
    )

    __all__ += [
        "AnalyzerResult",
        "analyze_clip_target",
        "analyze_frame_target",
        "ANALYZER_RESPONSE_SCHEMA",
        "ANALYZER_SYSTEM_PROMPT",
    ]
except Exception:  # pragma: no cover
    pass
