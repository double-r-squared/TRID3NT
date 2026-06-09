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
            declarations.append(decl)
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
    loop = asyncio.get_running_loop()

    # Build contents list: prior turns (if any) + current user message.
    contents: list[genai_types.Content] = []
    if chat_history:
        for entry in chat_history:
            role = entry.get("role", "user")
            text = entry.get("text", "")
            if not text:
                continue
            # Gemini uses "user" and "model" roles.
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
    "GeminiSettings",
    "StreamEvent",
    "TextDeltaEvent",
    "FunctionCallEvent",
    "SYSTEM_PROMPT",
    "build_client",
    "build_tool_declarations",
    "load_settings",
    "stream_events",
    "stream_reply",
]
