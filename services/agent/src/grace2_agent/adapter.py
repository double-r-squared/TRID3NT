"""Gemini-only containment layer (Domain Discipline: agent.md).

Every Gemini- / google-genai-specific construct lives here. The server.py and
mcp.py modules call into this module with Gemini-naive shapes (strings,
async-iterators of strings). This is **containment, not abstraction** — no
``LLMProvider`` protocol, no provider branches, no Bedrock/Strands shapes.
FR-AS-1: Gemini-only. The deferred multi-provider future (§5) is not
foreclosed cheaply because the seam exists, but no abstraction is paid for now.

Model selection (job-0015):
  GRACE2_GEMINI_MODEL env override, defaulting to ``GEMINI_DEFAULT_MODEL``
  below. As of 2026-06-05 Gemini 3 (``gemini-3-pro*``) is not yet GA on Vertex
  for this project (verified 404 from generate_content); ``gemini-2.5-pro`` is
  the current best stable. When Gemini 3 lands on Vertex this constant — and
  the env override path — flips with no other code change.

Auth: ADC via ``GOOGLE_GENAI_USE_VERTEXAI=True`` + ``GOOGLE_CLOUD_PROJECT`` +
``GOOGLE_CLOUD_LOCATION``. No API key path. (job-0014 substrate.)

job-0154: tool-dispatch fix.  ``stream_reply`` sent Gemini no function
declarations and no system prompt, so Gemini had no knowledge of any tool and
responded with a prose refusal.  The new ``stream_events`` replaces it: it
passes the tool catalog (``FunctionDeclaration`` from each registered tool's
callable + docstring) plus a focused system prompt to ``generate_content_stream``,
then demultiplexes each chunk into either a ``TextDeltaEvent`` or a
``FunctionCallEvent`` so the server can dispatch the tool through the registry.
``stream_reply`` is retained as a thin compatibility shim (text-only calls).

job-0169: multi-turn function_call → function_response loop.  job-0154 stopped
after the first function_call (single-shot dispatch) — every multi-tool prompt
("Show me protected areas in Fort Myers" → geocode_location → fetch_wdpa) hung
because Gemini never saw the result of its first call and so never decided to
call the next tool.  This module now exposes:

  * ``stream_events`` (single-turn primitive — unchanged contract; still
    accepts ``user_text`` for backward compatibility).  Existing tests use it.
  * ``stream_events_with_contents`` (new primitive used by the loop driver):
    accepts a fully-built ``contents: list[Content]`` and streams one turn.
  * ``build_contents_from_history`` — converts ``state.chat_history`` plus the
    current user_text into the initial ``contents`` list.
  * ``summarize_tool_result`` — compacts a tool result into the dict that
    becomes the ``function_response.response`` payload Gemini reads on the
    next turn.  Per the kickoff: SUMMARY shape (LayerURI metadata, key
    metrics, error code) — NEVER the full raw tool result (which can be MB
    of GeoJSON).
  * ``build_function_call_content`` / ``build_function_response_content`` —
    typed helpers for appending the model+function turn pair after a
    dispatch.

The loop driver itself lives in ``server.py`` (``_stream_gemini_reply``) so it
can dispatch tools through ``_invoke_tool_via_emitter`` (registry + emitter
side effects).  This file stays the Gemini-containment seam.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from google import genai
from google.genai import types as genai_types

logger = logging.getLogger("grace2_agent.adapter")

# Default Gemini model id. See module docstring for the Gemini-3-on-Vertex
# availability note. Override at runtime via ``GRACE2_GEMINI_MODEL``.
GEMINI_DEFAULT_MODEL = "gemini-2.5-pro"


# ---------------------------------------------------------------------------
# Typed stream events (job-0154)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TextDeltaEvent:
    """A streamed text fragment from Gemini."""
    delta: str


@dataclass(frozen=True)
class FunctionCallEvent:
    """Gemini decided to call a tool.

    ``name`` matches the registered tool name in ``TOOL_REGISTRY``.
    ``call_id`` is Gemini's per-call identifier (used when feeding back the
    function response in a multi-turn loop — future work; v0.1 single-shot).
    ``args`` is the deserialized argument dict.
    """
    name: str
    call_id: str | None
    args: dict[str, Any] = field(default_factory=dict)


StreamEvent = TextDeltaEvent | FunctionCallEvent


# ---------------------------------------------------------------------------
# System prompt builder (job-0154)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are GRACE — a geospatial hazard-modeling assistant. You help users analyze,
visualize, and model natural hazards (flooding, fire, hurricanes, etc.) using
real data and physics-based simulation tools.

When a user asks you to model, analyze, simulate, or compute anything related
to a hazard or geographic data, call the appropriate tool. Do not say you
cannot help with modeling requests — you have tools for that.

Key behaviors:
- If the user asks to model a flood scenario, run a flood simulation, compute
  flood depth, or analyze inundation for any location, call
  run_model_flood_scenario immediately.
- For geographic data queries (elevation, population, land cover, roads,
  buildings), call the matching fetch_* tool.
- For QGIS geoprocessing (clip, slope, hillshade, zonal statistics), call the
  matching compute_* or clip_* tool.
- Never fabricate numbers. All depth, area, and count values in your replies
  must come from the tool result, not from your own generation.
- When a tool result contains a flood depth layer, describe the results from
  the returned metrics — do not invent values.
- Keep responses concise and focused on the hazard modeling context.
"""


# ---------------------------------------------------------------------------
# Tool declaration builder (job-0154)
# ---------------------------------------------------------------------------

def _strip_private_params(decl: genai_types.FunctionDeclaration) -> genai_types.FunctionDeclaration:
    """Remove underscore-prefixed parameters from a generated FunctionDeclaration.

    job-0163 finding: 16+ atomic tools (``compute_zonal_statistics``,
    ``compute_impervious_surface``, ``extract_landcover_class``,
    ``clip_raster_to_*``, ``compute_hillshade``/``slope``/``aspect``, etc.)
    accept underscore-prefixed test-injection kwargs such as
    ``_storage_client: object | None = None`` and ``_bucket: str | None = None``.
    These are Python's standard "internal/private" naming convention and exist
    only so unit tests can pass a mock GCS client — they must NEVER be visible
    to the LLM.

    ``FunctionDeclaration.from_callable_with_api_option`` includes them in the
    generated schema; ``_storage_client: object | None`` becomes a Schema with
    only ``nullable=True`` (no ``type`` field), which Vertex Gemini rejects
    with ``400 INVALID_ARGUMENT: schema didn't specify the schema type field``,
    blocking the ENTIRE tool catalog — Gemini cannot dispatch any tool. This
    function surgically removes every underscore-prefixed property from the
    schema (and from ``required``) before the declaration is returned.

    Bug-class fix (per AGENTS.md "Bundle small fixes; scan for all instances"):
    the filter is keyed on the underscore prefix, so any future tool with a
    test-injection kwarg automatically gets the same treatment.
    """
    if decl.parameters is None or decl.parameters.properties is None:
        return decl
    cleaned_props = {
        n: s for n, s in decl.parameters.properties.items() if not n.startswith("_")
    }
    cleaned_required = (
        [r for r in (decl.parameters.required or []) if not r.startswith("_")]
        if decl.parameters.required is not None
        else decl.parameters.required
    )
    new_parameters = decl.parameters.model_copy(
        update={"properties": cleaned_props, "required": cleaned_required}
    )
    return decl.model_copy(update={"parameters": new_parameters})


def build_tool_declarations(
    tool_registry: dict[str, Any],
) -> list[genai_types.FunctionDeclaration]:
    """Build Gemini ``FunctionDeclaration`` objects from the TOOL_REGISTRY.

    Uses ``FunctionDeclaration.from_callable_with_api_option`` so the
    docstring discipline enforced at registration time (FR-AS-3 "Use this
    when:" / "Do NOT use this for:" / param/return descriptions) is the
    sole source of Gemini's tool-selection signal — the same text that a
    human reviewer sees is exactly what Gemini reasons over.

    Falls back to a docstring-only declaration for any tool whose signature
    has complex types that ``from_callable_with_api_option`` cannot serialize
    (e.g. ``tuple[float, float, float, float] | None``, pydantic models).
    The fallback captures the full docstring (up to 1000 chars) so the
    "Use this when:" / "Params:" sections are available to Gemini even without
    a machine-readable parameter schema.  Gemini can infer arg names from the
    "Params:" section and the calling context.

    Every generated declaration is post-processed through
    ``_strip_private_params`` to remove underscore-prefixed kwargs (job-0163;
    see that helper's docstring for the Vertex 400 trace).

    OQ-0154-DECL-FALLBACK: Many tools with complex type annotations fall back
    here.  A follow-up job should add a ``@simple_schema`` decorator or a
    hand-authored ``schema: dict`` class attribute on the tool functions so
    ``from_callable`` succeeds.  This is logged at DEBUG to avoid spamming
    startup logs with expected fallbacks.
    """
    declarations: list[genai_types.FunctionDeclaration] = []
    for name, entry in sorted(tool_registry.items()):
        try:
            decl = genai_types.FunctionDeclaration.from_callable_with_api_option(
                callable=entry.fn,
                api_option="VERTEX_AI",
            )
            declarations.append(_strip_private_params(decl))
        except Exception as exc:  # noqa: BLE001 — fallback gracefully
            logger.debug(
                "tool declaration fallback for %r (complex signature): %s", name, exc
            )
            doc = inspect.getdoc(entry.fn) or f"Tool: {name}"
            declarations.append(
                genai_types.FunctionDeclaration(
                    name=name,
                    # 1 000 chars captures "Use this when:" + "Do NOT" + "Params:"
                    # sections from well-documented tools (FR-AS-3 discipline).
                    description=doc[:1000],
                )
            )
    return declarations


# ---------------------------------------------------------------------------
# GeminiSettings
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GeminiSettings:
    """Resolved Gemini configuration (env-derived; no implicit fallbacks)."""

    model: str
    project: str
    location: str
    use_vertex: bool


def load_settings() -> GeminiSettings:
    """Resolve Gemini settings from the environment.

    Required env (job-0014 substrate):
    - ``GOOGLE_GENAI_USE_VERTEXAI=True``
    - ``GOOGLE_CLOUD_PROJECT`` (default: ``grace-2-hazard-prod``)
    - ``GOOGLE_CLOUD_LOCATION`` (default: ``us-central1``)

    Optional:
    - ``GRACE2_GEMINI_MODEL`` (default: ``GEMINI_DEFAULT_MODEL``)
    """
    return GeminiSettings(
        model=os.environ.get("GRACE2_GEMINI_MODEL", GEMINI_DEFAULT_MODEL),
        project=os.environ.get("GOOGLE_CLOUD_PROJECT", "grace-2-hazard-prod"),
        location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
        use_vertex=os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "True").lower()
        in ("true", "1", "yes"),
    )


def build_client(settings: GeminiSettings) -> genai.Client:
    """Build a google-genai Client configured for Vertex AI.

    Containment: nothing outside this module imports ``genai`` or
    ``genai_types``. The server consumes the async-iterator of deltas this
    module returns.
    """
    if not settings.use_vertex:
        # Pre-MVP: Vertex-only path. Surface the misconfiguration loudly
        # rather than silently falling back to API-key mode.
        raise RuntimeError(
            "GRACE2 agent runs Vertex-only (FR-AS-1). "
            "Set GOOGLE_GENAI_USE_VERTEXAI=True."
        )
    return genai.Client(
        vertexai=True, project=settings.project, location=settings.location
    )


# ---------------------------------------------------------------------------
# Content / function_response builders (job-0169)
# ---------------------------------------------------------------------------

# Hard upper bound on chars we send back to Gemini per function_response.
# Anything bigger gets clipped — Gemini doesn't need megabytes of GeoJSON to
# decide the next tool call; it needs the LayerURI, key metrics, error code,
# and a couple of identifying fields.
_FUNCTION_RESPONSE_CHAR_BUDGET = 4_000

# Maximum loop iterations for the multi-turn driver.  Each iteration is one
# Gemini stream + (optionally) one dispatched tool call.  ~8 turns is enough
# for the longest documented Mode-1 workflow chain
# (geocode → fetch_dem → fetch_landcover → fetch_river_geom → lookup_precip →
# run_solver → wait → postprocess) plus headroom; if Gemini somehow loops past
# this, that's a Gemini-side runaway and the cap is the right fail-stop.
MAX_TURN_ITERATIONS = 8


def build_contents_from_history(
    user_text: str,
    chat_history: list[dict] | None = None,
) -> list[genai_types.Content]:
    """Convert ``chat_history`` + a new ``user_text`` into Gemini ``Content``s.

    Chat history entries are ``{role, text}`` dicts where role is one of
    ``user`` / ``agent`` / ``assistant`` / ``model``.  Gemini only understands
    ``user`` and ``model`` roles — agent/assistant collapse into ``model``.
    Empty-text entries are dropped (the persistence layer writes empty rows
    for the LLM's reply-turn marker; those carry no signal for Gemini).
    """
    contents: list[genai_types.Content] = []
    if chat_history:
        for entry in chat_history:
            role = entry.get("role", "user")
            text = entry.get("text", "")
            if not text:
                continue
            gem_role = "model" if role in ("agent", "assistant", "model") else "user"
            contents.append(
                genai_types.Content(
                    role=gem_role,
                    parts=[genai_types.Part(text=text)],
                )
            )
    contents.append(
        genai_types.Content(
            role="user",
            parts=[genai_types.Part(text=user_text)],
        )
    )
    return contents


def _coerce_to_summary_value(value: Any, depth: int = 0) -> Any:
    """Recursive helper for ``summarize_tool_result``.

    Walks the tool-result structure; converts non-JSON-native types to strings,
    truncates long lists and strings, drops nested dicts past depth 2.  The
    goal isn't fidelity — it's giving Gemini enough signal to decide the next
    call without sending it megabytes of GeoJSON.
    """
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        # Long strings (HTML bodies, base64 payloads) get clipped.
        if len(value) > 500:
            return value[:500] + "…[truncated]"
        return value
    if isinstance(value, (list, tuple)):
        if depth >= 2:
            return f"[list len={len(value)}]"
        # Keep up to 5 items; summarize the rest by count.
        items = [_coerce_to_summary_value(v, depth + 1) for v in list(value)[:5]]
        if len(value) > 5:
            items.append(f"…[+{len(value) - 5} more items]")
        return items
    if isinstance(value, dict):
        if depth >= 2:
            return f"{{dict keys={list(value.keys())[:8]}}}"
        out: dict[str, Any] = {}
        for k, v in value.items():
            if not isinstance(k, str):
                k = str(k)
            # Filter obviously huge / opaque fields the LLM doesn't need.
            if k in {"raw_bytes", "raw_body", "binary", "geometry_wkb", "pixels"}:
                out[k] = f"[{k} suppressed]"
                continue
            out[k] = _coerce_to_summary_value(v, depth + 1)
        return out
    # Pydantic models / dataclasses / arbitrary objects — repr-coerce, clip.
    s = repr(value)
    if len(s) > 200:
        s = s[:200] + "…"
    return s


def _classify_error(error: BaseException) -> tuple[str, bool]:
    """Derive ``(error_code, retryable)`` for a tool-dispatch exception.

    job-0177: typed tool exceptions across the registry already declare
    ``error_code`` (str) and ``retryable`` (bool) class attributes
    (``WDPAError``, ``HRSLError``, ``MTBSError``, ``MRMSError``,
    ``INatError``, ``IUCNError``, ``FIRMSError``, ``GTSMError``,
    ``LANDFIREError``, ``OSMRoadsError``, ``GBIFError``,
    ``CAMaFloodError``, ``GOESError``, ``CompFireError``,
    ``ColoredReliefError``, ``NIFCError``, ``NWSAlertsError``, etc.).
    Harvest those directly so the function_response the multi-turn loop
    feeds back to Gemini carries the retry signal the tool already knew.

    For untyped exceptions, fall back to a conservative heuristic:

    - ``asyncio.TimeoutError`` / ``TimeoutError``  → retryable
    - ``ConnectionError`` / ``OSError`` (network-ish) → retryable
    - ``ValueError`` / ``TypeError`` / ``KeyError`` / ``AttributeError``
      (programmer / arg shape error) → NOT retryable
    - everything else (``RuntimeError`` and friends) → retryable
      (Gemini reads ``message`` and decides; the cap is
      ``MAX_TURN_ITERATIONS`` either way).

    Never raises — even pathological exceptions yield a stable dict shape
    so the multi-turn loop keeps going.
    """
    # 1. Honour typed-tool exception class attributes when present.
    code_attr = getattr(error, "error_code", None)
    retry_attr = getattr(error, "retryable", None)
    if isinstance(code_attr, str) and code_attr:
        code = code_attr
    else:
        code = type(error).__name__.upper()
    if isinstance(retry_attr, bool):
        return code, retry_attr

    # 2. Heuristic fallback for untyped exceptions.
    import asyncio as _asyncio

    if isinstance(error, (_asyncio.TimeoutError, TimeoutError)):
        return code, True
    if isinstance(error, (ConnectionError, OSError)):
        return code, True
    if isinstance(error, (ValueError, TypeError, KeyError, AttributeError)):
        return code, False
    # Default: retryable so Gemini gets one more shot (capped by
    # MAX_TURN_ITERATIONS).
    return code, True


def summarize_tool_result(
    tool_name: str,
    result: Any,
    error: BaseException | None = None,
) -> dict[str, Any]:
    """Compact a tool result into the ``function_response.response`` payload.

    Per the kickoff: SUMMARY, not full result.  Gemini reads this between
    turns to decide its next move; it needs LayerURI metadata, key metrics,
    error codes, and counts — not the raw GeoJSON bytes.

    Conventions enforced:

    * Errors (job-0177) become
      ``{"status": "error", "error_code": str, "message": str, "retryable": bool, "error_type": str}``.
      ``error_code`` + ``retryable`` are harvested from the tool's typed
      exception class (FR-AS-11 surface) when present, else derived
      from the exception class name / runtime kind via ``_classify_error``.
      Gemini reads this and either retries with corrected args, calls a
      different tool, or narrates the failure honestly. The
      ``MAX_TURN_ITERATIONS`` cap protects against runaway retry.
      The legacy ``"error"`` field is retained as an alias of ``message``
      so older tests / consumers don't break.
    * ``None`` results (the ``_invoke_tool_via_emitter`` path returns ``None``
      on payload-warning skip, TOOL_NOT_FOUND, etc.) become
      ``{"status": "no_result"}``.
    * Dict results are walked through ``_coerce_to_summary_value`` and then
      JSON-clipped to ``_FUNCTION_RESPONSE_CHAR_BUDGET`` chars.
    * Primitive / string results become ``{"result": value}``.
    * The final dict always carries ``"tool"`` and ``"status"`` keys so the
      LLM has a stable shape to reason over.
    """
    import json as _json

    if error is not None:
        code, retryable = _classify_error(error)
        message = str(error)[:500]
        return {
            "tool": tool_name,
            "status": "error",
            "error_code": code,
            "message": message,
            "retryable": retryable,
            # Legacy alias — preserved so existing tests / callers that
            # read ``error`` continue to work.  ``message`` is the new
            # canonical field; both carry the same string.
            "error": message,
            "error_type": type(error).__name__,
        }

    if result is None:
        return {"tool": tool_name, "status": "no_result"}

    if isinstance(result, dict):
        summary = _coerce_to_summary_value(result)
        payload: dict[str, Any] = {
            "tool": tool_name,
            "status": "ok",
            "result": summary,
        }
    else:
        payload = {
            "tool": tool_name,
            "status": "ok",
            "result": _coerce_to_summary_value(result),
        }

    # Final char-budget clip: serialize, if oversized clip and re-wrap.
    try:
        encoded = _json.dumps(payload, default=str)
    except Exception:  # noqa: BLE001 — pathological non-serializable
        return {
            "tool": tool_name,
            "status": "ok",
            "result_repr": repr(result)[:1000],
            "note": "result not JSON-serializable; coerced via repr",
        }
    if len(encoded) > _FUNCTION_RESPONSE_CHAR_BUDGET:
        return {
            "tool": tool_name,
            "status": "ok",
            "result_summary": encoded[:_FUNCTION_RESPONSE_CHAR_BUDGET] + "…[clipped]",
            "note": "full result exceeded char budget; clipped for LLM context",
        }
    return payload


def build_function_call_content(
    name: str,
    args: dict[str, Any],
    call_id: str | None = None,
) -> genai_types.Content:
    """Build the ``model``-role Content wrapping the function_call.

    This is appended to ``contents`` after a dispatch so the next Gemini
    stream sees its own prior tool-call decision.
    """
    fn_call = genai_types.FunctionCall(name=name, args=args or {}, id=call_id)
    return genai_types.Content(
        role="model",
        parts=[genai_types.Part(function_call=fn_call)],
    )


def build_function_response_content(
    name: str,
    response: dict[str, Any],
    call_id: str | None = None,
) -> genai_types.Content:
    """Build the ``function``-role Content wrapping the function_response.

    Appended right after the matching ``model`` function_call content so
    Gemini has the (call, response) pair before deciding its next turn.
    """
    fn_resp = genai_types.FunctionResponse(name=name, response=response, id=call_id)
    return genai_types.Content(
        role="user",
        parts=[genai_types.Part(function_response=fn_resp)],
    )


# ---------------------------------------------------------------------------
# stream_events — tool-aware streaming (job-0154, root fix)
# ---------------------------------------------------------------------------

async def stream_events(
    client: genai.Client,
    model: str,
    user_text: str,
    tool_declarations: list[genai_types.FunctionDeclaration] | None = None,
    system_prompt: str | None = None,
    chat_history: list[dict] | None = None,
) -> AsyncIterator[StreamEvent]:
    """Stream Gemini's reply as typed ``StreamEvent`` objects.

    Replaces the text-only ``stream_reply`` path.  When ``tool_declarations``
    is supplied (non-empty list), Gemini receives the full function catalog so
    it can emit ``FunctionCallEvent`` objects instead of prose refusals.

    Each yielded item is either:
    - ``TextDeltaEvent(delta)`` — a streamed text fragment; caller wraps it
      in ``agent-message-chunk``.
    - ``FunctionCallEvent(name, call_id, args)`` — Gemini wants to call a
      tool; caller dispatches through ``_invoke_tool_via_emitter``.

    Cancellation semantics are identical to ``stream_reply``.

    Args:
        client: google-genai ``Client`` built by ``build_client``.
        model: model identifier string (e.g. ``"gemini-2.5-pro"``).
        user_text: the user's message text.
        tool_declarations: optional list of ``FunctionDeclaration`` objects
            built by ``build_tool_declarations``; pass an empty list or
            ``None`` to send no tool catalog (text-only mode).
        system_prompt: optional system instruction string; passed as
            ``GenerateContentConfig.systemInstruction``.
        chat_history: optional list of prior ``{role, text}`` dicts from
            ``SessionState.chat_history``.  Included as prior ``Content``
            turns so Gemini has conversational context.
    """
    contents = build_contents_from_history(user_text, chat_history)
    async for event in stream_events_with_contents(
        client,
        model,
        contents,
        tool_declarations=tool_declarations,
        system_prompt=system_prompt,
    ):
        yield event


# ---------------------------------------------------------------------------
# stream_events_with_contents — single-turn primitive for the multi-turn loop
# ---------------------------------------------------------------------------


async def stream_events_with_contents(
    client: genai.Client,
    model: str,
    contents: list[genai_types.Content],
    tool_declarations: list[genai_types.FunctionDeclaration] | None = None,
    system_prompt: str | None = None,
) -> AsyncIterator[StreamEvent]:
    """Stream one Gemini turn from a fully-built ``contents`` list (job-0169).

    This is the primitive the multi-turn loop driver in ``server.py`` uses.
    Each call corresponds to exactly one ``generate_content_stream`` round —
    the driver appends function_call + function_response Content entries to
    ``contents`` and re-calls this until Gemini emits no further function
    calls (only text → terminal turn).

    ``stream_events`` (the user-text variant) now delegates here after
    building ``contents`` via ``build_contents_from_history``.

    Cancellation: ``asyncio.CancelledError`` cancels the underlying producer
    thread and re-raises.
    """
    loop = asyncio.get_running_loop()

    # Build the tool list for the config.
    gem_tools: list[genai_types.Tool] | None = None
    if tool_declarations:
        gem_tools = [genai_types.Tool(function_declarations=tool_declarations)]

    def _open_stream():
        cfg = genai_types.GenerateContentConfig(
            temperature=0.7,
            system_instruction=system_prompt or None,
            tools=gem_tools or None,
            # Disable automatic function calling — we handle dispatch ourselves.
            automatic_function_calling=genai_types.AutomaticFunctionCallingConfig(
                disable=True
            ) if gem_tools else None,
        )
        return client.models.generate_content_stream(
            model=model,
            contents=contents,
            config=cfg,
        )

    # Use a typed queue: items are StreamEvent | None (sentinel) | BaseException.
    queue: asyncio.Queue[StreamEvent | None | BaseException] = asyncio.Queue()

    def _producer() -> None:
        try:
            for chunk in _open_stream():
                # Walk parts: each chunk may carry text OR function_call parts.
                cands = getattr(chunk, "candidates", None) or []
                emitted_something = False
                for cand in cands:
                    content = getattr(cand, "content", None)
                    if content is None:
                        continue
                    parts = getattr(content, "parts", None) or []
                    for part in parts:
                        fn_call = getattr(part, "function_call", None)
                        if fn_call is not None and getattr(fn_call, "name", None):
                            event = FunctionCallEvent(
                                name=fn_call.name,
                                call_id=getattr(fn_call, "id", None),
                                args=dict(fn_call.args or {}),
                            )
                            loop.call_soon_threadsafe(queue.put_nowait, event)
                            emitted_something = True
                        else:
                            text = getattr(part, "text", None)
                            if text:
                                loop.call_soon_threadsafe(
                                    queue.put_nowait, TextDeltaEvent(delta=text)
                                )
                                emitted_something = True
                # Fallback: some SDK versions expose chunk.text directly.
                if not emitted_something:
                    delta = getattr(chunk, "text", None)
                    if delta:
                        loop.call_soon_threadsafe(
                            queue.put_nowait, TextDeltaEvent(delta=delta)
                        )
            loop.call_soon_threadsafe(queue.put_nowait, None)
        except BaseException as exc:  # noqa: BLE001 — surface any error to caller
            loop.call_soon_threadsafe(queue.put_nowait, exc)

    producer_task = loop.run_in_executor(None, _producer)

    try:
        while True:
            item = await queue.get()
            if item is None:
                return
            if isinstance(item, BaseException):
                raise item
            yield item
    except asyncio.CancelledError:
        producer_task.cancel()
        raise


# ---------------------------------------------------------------------------
# stream_reply — text-only shim (kept for backward-compat; delegates to
# stream_events without tool declarations)
# ---------------------------------------------------------------------------

async def stream_reply(
    client: genai.Client, model: str, user_text: str
) -> AsyncIterator[str]:
    """Stream Gemini's reply as a sequence of delta strings (text-only).

    Retained for callers that only want text.  Internally delegates to
    ``stream_events`` with no tool declarations.

    Cancellation: ``asyncio.CancelledError`` is the cancel path.
    """
    async for event in stream_events(client, model, user_text):
        if isinstance(event, TextDeltaEvent):
            yield event.delta


__all__ = [
    "GEMINI_DEFAULT_MODEL",
    "MAX_TURN_ITERATIONS",
    "GeminiSettings",
    "StreamEvent",
    "TextDeltaEvent",
    "FunctionCallEvent",
    "SYSTEM_PROMPT",
    "build_client",
    "build_contents_from_history",
    "build_function_call_content",
    "build_function_response_content",
    "build_tool_declarations",
    "load_settings",
    "stream_events",
    "stream_events_with_contents",
    "stream_reply",
    "summarize_tool_result",
]
