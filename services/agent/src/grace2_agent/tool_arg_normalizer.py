"""Tool-argument normalizer — kwargs cleanup at the call site (job-0164).

Gemini routinely invents kwargs the tool doesn't actually accept
(``run_name``, ``scenario_id``, ``description``, ``rainfall_event``,
``return_period_years`` when the function declared ``return_period_yr``, etc.).
Strict Python signatures fail loud on every one of them as
``TypeError: <fn>() got an unexpected keyword argument <name>``. We've patched
the most common offenders piecemeal — this module is the **centralized sweep**.

What this module does, at the agent's ``_invoke_tool_via_emitter`` boundary,
BEFORE ``entry.fn(**params)``:

1. **Alias mapping** — known abbreviation pairs (``_yr`` → ``_years``,
   ``_hr`` → ``_hours``, etc.) are rewritten if the tool's signature accepts
   the canonical name but the LLM provided the alias (or vice-versa).
2. **camelCase / snake_case bridging** — if the LLM sends ``durationHours``
   and the tool accepts ``duration_hours``, we rename.
3. **String-form forcing parsing** — when the LLM stuffs the design-storm
   spec into a string like ``"atlas14_100yr"`` or ``"100-yr / 24-hr design
   storm"``, we extract ``return_period_years=100`` / ``duration_hours=24``
   so downstream tools see the canonical fields.
4. **Unknown-kwarg absorption** — params not in the tool's signature and not
   absorbed by ``**kwargs`` get logged and dropped, never raised.

The function ``normalize_args(tool_name, raw_args, fn)`` is the public entry
point; ``fn`` is the registered callable (we inspect its signature directly
rather than maintain a parallel registry of accepted params).

Design notes:

- **No tool-body changes required.** This module's whole point is to keep the
  57 tool implementations free of ``**_extra_ignored`` boilerplate. The
  normalizer reads ``inspect.signature(fn)`` and decides what to forward.
- **Logs are the audit trail.** Every alias rewrite + drop emits a single INFO
  / DEBUG line so we can spot recurring LLM mistakes and bake them into the
  alias table.
- **Idempotent + side-effect free.** Returns a fresh dict; never mutates the
  caller's params. Safe to call inside hot loops.
- **Generic aliases first; tool-specific overrides win.** The ``_ALIAS_MAP``
  is global (``return_period_years`` ↔ ``return_period_yr`` works across
  every flood tool). Tool-specific quirks live in ``_TOOL_SPECIFIC_ALIASES``.
"""

from __future__ import annotations

import inspect
import logging
import re
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("grace2_agent.tool_arg_normalizer")

__all__ = [
    "normalize_args",
    "parse_forcing_string",
    "snake_case",
]


# --------------------------------------------------------------------------- #
# Alias maps
# --------------------------------------------------------------------------- #

#: Bidirectional alias pairs. If a tool accepts the canonical (left) form and
#: the LLM provided the alias (right), we rename; and vice-versa. The pairs are
#: matched on **exact** name equality, not substring — keeps the table tight.
#:
#: Add a new entry here whenever logs show a recurring kwarg-name miss.
_BIDIRECTIONAL_ALIASES: tuple[tuple[str, str], ...] = (
    ("return_period_years", "return_period_yr"),
    ("duration_hours", "duration_hr"),
    ("simulation_duration_hours", "simulation_duration_hr"),
    ("year_range", "years_range"),
    ("days_back", "days"),
    ("species_name", "scientific_name"),
)


def _build_alias_map() -> dict[str, str]:
    """Flatten the bidirectional pairs into a directed alias→canonical map."""
    m: dict[str, str] = {}
    for canon, alias in _BIDIRECTIONAL_ALIASES:
        # Both directions land in the map keyed by the "wrong" name pointing at
        # the "right" name. At normalize time we look up params[alias] and
        # rename if the tool's signature accepts the canonical form.
        m[alias] = canon
        m[canon] = alias
    return m


#: Per-tool override aliases that don't fit the generic bidirectional table.
#:
#: Shape: ``{tool_name: {wrong_kwarg: right_kwarg}}``. Tool-specific entries
#: win over the generic alias map.
_TOOL_SPECIFIC_ALIASES: dict[str, dict[str, str]] = {
    "run_model_flood_scenario": {
        # Gemini sometimes uses "place" / "location_name" instead of
        # "location_query" because the docstring's "Examples:" block names
        # places freely.
        "place": "location_query",
        "location_name": "location_query",
        "location": "location_query",
    },
    "run_model_flood_habitat_scenario": {
        "place": "place_label",
        "location_name": "place_label",
    },
}


#: Kwargs we silently drop without warning (Gemini convenience fields that
#: never carry signal). Logged at DEBUG level only.
_SILENT_DROP: frozenset[str] = frozenset(
    {
        "run_name",
        "scenario_id",
        "scenario_name",
        "description",
        "comment",
        "user_intent",
        "explanation",
        "reasoning",
        "purpose",
        "note",
    }
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

_CAMEL_RE = re.compile(r"(?<!^)(?=[A-Z])")


def snake_case(name: str) -> str:
    """Convert ``durationHours`` → ``duration_hours``.

    No-op on already-snake_case strings; pure (no global state).
    """
    if "_" in name or name.islower():
        # Already snake-ish (or single lowercase word) — leave alone.
        return name.lower() if name.isupper() else name
    return _CAMEL_RE.sub("_", name).lower()


def parse_forcing_string(s: str) -> dict[str, int]:
    """Parse a free-text design-storm string into ``{return_period_years, duration_hours}``.

    Handles the most common LLM-invented forms:

    - ``"atlas14_100yr"`` → ``{"return_period_years": 100}``
    - ``"atlas14_100yr_24hr"`` → ``{"return_period_years": 100, "duration_hours": 24}``
    - ``"100-yr / 24-hr design storm"`` → both fields
    - ``"500 year"`` → ``{"return_period_years": 500}``
    - ``"6 hour"`` → ``{"duration_hours": 6}``

    Returns an empty dict if nothing recognizable is found — the caller still
    gets a dict and can fall back to defaults.
    """
    if not s:
        return {}
    out: dict[str, int] = {}
    lower = s.lower()
    m_yr = re.search(r"(\d+)\s*[-_]?\s*(?:yr|year)s?", lower)
    if m_yr:
        try:
            out["return_period_years"] = int(m_yr.group(1))
        except ValueError:
            pass
    m_hr = re.search(r"(\d+)\s*[-_]?\s*(?:hr|hour)s?", lower)
    if m_hr:
        try:
            out["duration_hours"] = int(m_hr.group(1))
        except ValueError:
            pass
    return out


def _accepted_params(fn: Callable[..., Any]) -> tuple[set[str], bool]:
    """Return ``(accepted_param_names, accepts_var_keyword)`` for ``fn``.

    If the function declares ``**kwargs`` (any var-keyword param), the second
    element is True and the normalizer leaves unknown kwargs alone — the
    function will absorb them.
    """
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        # Builtins / C-extension callables we can't introspect — be conservative
        # and pass everything through unchanged.
        return set(), True
    accepted: set[str] = set()
    accepts_var_keyword = False
    for name, p in sig.parameters.items():
        if p.kind is inspect.Parameter.VAR_KEYWORD:
            accepts_var_keyword = True
            continue
        if p.kind is inspect.Parameter.VAR_POSITIONAL:
            continue
        accepted.add(name)
    return accepted, accepts_var_keyword


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #


def normalize_args(
    tool_name: str,
    raw_args: dict[str, Any],
    fn: Callable[..., Any],
) -> dict[str, Any]:
    """Normalize ``raw_args`` so ``fn(**normalized)`` won't raise on Gemini quirks.

    Pipeline (each step idempotent, fresh-dict output):

    1. **camelCase → snake_case** on every key the function does not already
       accept verbatim. Bypasses the rename if the snake form is also unknown.
    2. **Tool-specific alias** (``_TOOL_SPECIFIC_ALIASES[tool_name]``) rewrites.
    3. **Generic bidirectional alias** (``_BIDIRECTIONAL_ALIASES``) rewrites —
       only fires if the rename lands the kwarg on an accepted-param name and
       the canonical name isn't already in ``raw_args``.
    4. **String-form forcing parsing** — if ``raw_args`` carries ``forcing``
       or ``rainfall_event`` as a string AND the function accepts
       ``return_period_years`` / ``duration_hours``, extract those.
    5. **Silent-drop list** — known Gemini-convenience kwargs dropped at DEBUG.
    6. **Absorb-and-log** — remaining unknown kwargs dropped at INFO (with the
       tool name) so we can surface them in logs and add them to the alias
       map / silent-drop list over time.

    If the function declares ``**kwargs``, steps 5–6 are bypassed (the
    function explicitly opted into "give me everything").

    Args:
        tool_name: ``TOOL_REGISTRY`` key — used for per-tool override lookup
            and for log attribution.
        raw_args: the params dict as the LLM produced it (already
            ``parse_arguments_string``-decoded if string-form).
        fn: the registered callable. The signature is inspected to decide
            what's accepted; we never call it here.

    Returns:
        A fresh dict safe to splat into ``fn(**…)``. Never raises.
    """
    if not raw_args:
        return {}
    accepted, accepts_var_keyword = _accepted_params(fn)
    tool_aliases = _TOOL_SPECIFIC_ALIASES.get(tool_name, {})
    out: dict[str, Any] = {}
    dropped_unknown: list[str] = []
    dropped_silent: list[str] = []

    generic_alias_map = _build_alias_map()
    for key, value in raw_args.items():
        target = key

        # Step 1: camelCase → snake_case. Always normalize the case form so
        # subsequent alias chains can match. If the snake form is in accepted
        # OR in the alias map, the rename is useful; otherwise leave alone.
        if target not in accepted:
            snake = snake_case(target)
            if snake != target and (
                snake in accepted
                or snake in tool_aliases
                or snake in generic_alias_map
            ):
                logger.debug(
                    "tool_arg_normalizer[%s]: camelCase rename %r -> %r",
                    tool_name,
                    target,
                    snake,
                )
                target = snake

        # Step 2: tool-specific alias.
        if target not in accepted and target in tool_aliases:
            mapped = tool_aliases[target]
            if mapped in accepted:
                logger.info(
                    "tool_arg_normalizer[%s]: tool-specific alias %r -> %r",
                    tool_name,
                    key,
                    mapped,
                )
                target = mapped

        # Step 3: generic bidirectional alias.
        if target not in accepted:
            cand = generic_alias_map.get(target)
            if cand and cand in accepted and cand not in out and cand not in raw_args:
                logger.info(
                    "tool_arg_normalizer[%s]: generic alias %r -> %r",
                    tool_name,
                    key,
                    cand,
                )
                target = cand

        # Step 4 helper: string-form forcing parsing handled after the loop so
        # we have the final mapped set. Track originals for that step.

        # Final placement decision.
        if target in accepted:
            # Don't overwrite an already-mapped canonical value with an alias's
            # value (canonical wins on conflict).
            if target not in out:
                out[target] = value
        elif accepts_var_keyword:
            # Function explicitly absorbs unknowns — pass through.
            out[key] = value
        elif key in _SILENT_DROP or target in _SILENT_DROP:
            dropped_silent.append(key)
        else:
            dropped_unknown.append(key)

    # Step 4: string-form forcing parsing. If the LLM sent ``forcing=…`` or
    # ``rainfall_event=…`` AND the tool accepts the canonical year/hour fields,
    # extract them. Don't overwrite explicit fields the LLM also supplied.
    forcing_str = raw_args.get("forcing") or raw_args.get("rainfall_event")
    if isinstance(forcing_str, str) and (
        "return_period_years" in accepted or "duration_hours" in accepted
    ):
        parsed = parse_forcing_string(forcing_str)
        for parsed_key, parsed_val in parsed.items():
            if parsed_key in accepted and parsed_key not in out:
                logger.info(
                    "tool_arg_normalizer[%s]: parsed forcing string %r -> %s=%s",
                    tool_name,
                    forcing_str,
                    parsed_key,
                    parsed_val,
                )
                out[parsed_key] = parsed_val

    # Logging tail.
    if dropped_silent:
        logger.debug(
            "tool_arg_normalizer[%s]: silently dropped %s (convenience kwargs)",
            tool_name,
            dropped_silent,
        )
    if dropped_unknown:
        logger.info(
            "tool_arg_normalizer[%s]: dropped unknown kwargs %s (signature accepts %s)",
            tool_name,
            dropped_unknown,
            sorted(accepted),
        )

    return out
