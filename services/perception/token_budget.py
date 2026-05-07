"""Token budget helpers for VLM and text LLM calls.

Tokenization is provider-specific and exact counts require model
tokenizers we do not bundle. The 4-chars-per-token approximation is
within ~20% of GPT/Claude tokenizers for English prose and good
enough for the ceiling we apply on the way IN to a provider call.

Resolution rules.

- ``resolve_output_cap`` takes any number of caps (per-camera,
  per-provider, per-call) and returns the smallest non-NULL value.
  Returns None if all caps are NULL, in which case the caller should
  omit ``max_tokens`` from the request and let the provider use its
  model default. That's the "default = maximum" behavior the user
  asked for.

- ``trim_sections_to_budget`` shrinks an ordered list of prompt
  sections so that the estimated total fits a token budget. Sections
  earlier in the list have lower priority and get dropped first.
  When only the highest-priority section is left, it gets character-
  truncated rather than dropped.
"""

from __future__ import annotations

from typing import Iterable


# Rough char-to-token ratio. Whisper output / English prose lands
# around 3.6-4.2 chars per token in practice. Using 4 keeps us
# slightly conservative (we estimate fewer tokens than reality), but
# the safety overhead in :func:`trim_sections_to_budget` already
# leaves headroom for fence cases.
CHARS_PER_TOKEN = 4


def estimate_tokens(text: str | None) -> int:
    if not text:
        return 0
    return max(1, len(text) // CHARS_PER_TOKEN)


def resolve_output_cap(*caps: int | None) -> int | None:
    """Return the smallest non-NULL cap, or None if every cap is NULL."""
    real = [c for c in caps if c is not None and c > 0]
    return min(real) if real else None


# Same shape as resolve_output_cap. Kept named separately so call
# sites read intentionally and so the input-cap rules can diverge
# later (e.g. honor model context window) without disturbing output
# cap callers.
resolve_input_cap = resolve_output_cap


def trim_sections_to_budget(
    sections: list[tuple[str, str]],
    budget_tokens: int | None,
    safety_margin: int = 64,
) -> list[tuple[str, str]]:
    """Trim ``sections`` to fit within ``budget_tokens``.

    ``sections`` is a list of ``(name, text)`` tuples in
    ``keep_priority_low_to_high`` order. The tail of the list is the
    highest-priority block and is preserved (or character-truncated)
    when everything else has been dropped.

    ``safety_margin`` reserves token headroom for the system prompt
    and instruction wrappers that the call sites add around the
    sections we control here. Without it, a budget of, say, 4096 with
    a 200-token system prompt would silently exceed the real ceiling.
    """
    if budget_tokens is None or budget_tokens <= 0:
        return list(sections)

    effective_budget = max(0, budget_tokens - safety_margin)
    out = list(sections)
    total = sum(estimate_tokens(t) for _, t in out)
    if total <= effective_budget:
        return out

    # Drop from the front (lowest priority) until everything fits or
    # only the highest-priority section remains.
    while len(out) > 1 and total > effective_budget:
        _, dropped_text = out.pop(0)
        total -= estimate_tokens(dropped_text)

    if total <= effective_budget:
        return out

    # Last block. character-truncate to the remaining budget. Cut on
    # the nearest sentence boundary to keep the prompt readable.
    name, text = out[0]
    target_chars = max(0, effective_budget * CHARS_PER_TOKEN)
    if target_chars <= 0:
        return []
    truncated = text[:target_chars]
    cut = max(truncated.rfind("."), truncated.rfind("\n"))
    if cut > 0 and cut > target_chars * 0.6:
        truncated = truncated[: cut + 1]
    out[0] = (name, truncated.rstrip() + "...")
    return out


def join_sections(sections: Iterable[tuple[str, str]], separator: str = "\n\n") -> str:
    return separator.join(text for _, text in sections if text)
