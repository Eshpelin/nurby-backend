"""HAR <-> VLM fusion helpers (pure).

Two directions, both small and testable:

- ``format_har_context`` turns the live per-person HAR actions into a short ground-truth line
  for the VLM prompt, so the caption is grounded ("Mum appears to be eating") instead of
  guessed. Only named (recognised) people are included; unknown actions are dropped.
- ``har_vlm_agreement`` checks whether a VLM caption corroborates a HAR action. Agreement
  raises confidence; disagreement is a labelled hard case for a future fine-tuning set. It is
  deliberately conservative: returns ``None`` ("can't tell") unless the caption clearly
  supports or contradicts the action.

No model calls, no I/O.
"""

from __future__ import annotations

# Caption cue words that corroborate a given action. Conservative; only high-signal words.
_ACTION_CUES: dict[str, tuple[str, ...]] = {
    "eating": ("eating", "eats", "meal", "lunch", "dinner", "breakfast", "feeding", "plate"),
    "drinking": ("drinking", "drinks", "cup", "glass", "mug"),
    "walking": ("walking", "walks", "strolling", "pacing"),
    "sitting": ("sitting", "seated", "sits"),
    "standing": ("standing", "stands", "upright"),
    "lying_down": ("lying", "laying", "reclining", "on the bed", "on the sofa", "on the couch"),
    "sleeping": ("sleeping", "asleep", "napping"),
    "fallen": ("fallen", "fell", "collapsed", "on the floor", "on the ground"),
    "playing": ("playing", "plays"),
}


def _pretty(action: str) -> str:
    return action.replace("_", " ")


def format_har_context(live: list[dict] | None) -> str | None:
    """Ground-truth line for the VLM from live HAR actions. Named people only; skips
    unknown actions. Returns None when there is nothing useful to add."""
    named = [
        (e.get("person_name"), e.get("action"))
        for e in (live or [])
        if e.get("person_name") and e.get("action") not in (None, "unknown")
    ]
    if not named:
        return None
    parts = [f"{name} appears to be {_pretty(action)}" for name, action in named]
    return (
        "A motion-analysis model reads the following about people in view: "
        + ", ".join(parts)
        + ". Use this as a strong prior but describe only what you can see."
    )


def har_vlm_agreement(action: str | None, caption: str | None) -> bool | None:
    """Does the caption corroborate the HAR action? True (agrees), False (contradicts), or
    None (can't tell). Conservative: a caption that mentions a DIFFERENT action's cue words but
    not this action's is a contradiction; silence is None."""
    if not action or action == "unknown" or not caption:
        return None
    text = caption.lower()
    own = _ACTION_CUES.get(action)
    if own and any(w in text for w in own):
        return True
    # Contradiction: caption clearly describes a different, mutually-exclusive posture/action.
    mutually_exclusive = {
        "standing": {"lying_down", "sleeping", "sitting"},
        "walking": {"lying_down", "sleeping", "sitting"},
        "sitting": {"walking", "standing", "lying_down"},
        "lying_down": {"standing", "walking", "sitting"},
        "sleeping": {"standing", "walking"},
        "fallen": {"standing", "walking", "sitting"},
    }
    for other in mutually_exclusive.get(action, set()):
        cues = _ACTION_CUES.get(other, ())
        if any(w in text for w in cues):
            return False
    return None
