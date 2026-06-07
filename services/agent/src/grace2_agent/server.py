"""Appendix-A WebSocket server (FR-AS-5, Appendix A core subset for M1/job-0015).

Implements the M1 hello-world subset of Appendix A:

  client -> agent (A.3):
    - session-resume          -> session-state
    - user-message            -> agent-message-chunk* (terminal done=True)
    - cancel                  -> pipeline-state(cancelled) within NFR-R-3 30s

  agent -> client (A.4):
    - session-state           initial replay on session-resume
    - agent-message-chunk     streamed deltas + terminal frame
    - pipeline-state          for cancel; also a one-step "thinking" snapshot
    - error                   A.6 codes

Every wire envelope is validated through ``grace2_contracts.ws.Envelope`` —
NEVER hand-roll JSON. Per Invariant 8 cancellation is first-class: any
in-flight Gemini stream is cancelled via asyncio task cancellation; the LLM
side of the chain completes within 30s. Cloud Workflows ``terminate`` is the
v0.2/M5 side of the chain (no solver yet in M1).

FR-WC-15 ``research_mode``: pass-through pinned. For job-0015 v0.1 the field is
logged and forwarded as-is — there is no second pipeline strategy yet.

FR-AS-8 confirmation hooks: scaffolded as ``CONFIRMATION_TRIGGERS`` (empty in
M1). Session-record writes (Appendix D.6) are explicitly carved out per FR-AS-8.

OQ-1 (Cloud Run WS vs Agent Engine) — see report's Open Questions section.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError
from websockets.asyncio.server import ServerConnection, serve

from grace2_contracts import new_ulid
from grace2_contracts.ws import (
    AgentMessageChunkPayload,
    CancelPayload,
    Envelope,
    ErrorPayload,
    PipelineStatePayload,
    PipelineStep,
    SessionResumePayload,
    SessionStatePayload,
    UserMessagePayload,
)

from .adapter import GeminiSettings, build_client, load_settings, stream_reply
from .pipeline_emitter import PipelineEmitter
from .tools import TOOL_REGISTRY

logger = logging.getLogger("grace2_agent.server")

# Confirmation triggers (FR-AS-8). Empty for M1: solver runs and non-session
# Mongo writes will populate this when those code paths land. Session-record
# writes (Appendix D.6) are NOT a trigger — that carveout is documented in
# the report, not represented as data here.
CONFIRMATION_TRIGGERS: set[str] = set()


@dataclass
class SessionState:
    """Per-session in-memory state. M1 keeps everything in-process; Mongo-backed
    session restore (NFR-R-2) lands when the LLM-facing DB seam is wired.

    job-0035 (M4): adds the per-session ``PipelineEmitter`` that owns the
    current ``PipelineSnapshot`` + ``loaded_layers`` accumulator and broadcasts
    real ``pipeline-state`` / ``session-state`` envelopes (Appendix A.7
    replace-not-reconcile). ``current_pipeline_id`` / ``current_pipeline_steps``
    stay as the M1 mirror for the LLM-streaming reply path (which doesn't go
    through the emitter — there are no tool calls there)."""

    session_id: str
    chat_history: list[dict] = field(default_factory=list)
    current_pipeline_id: str | None = None
    current_pipeline_steps: list[PipelineStep] = field(default_factory=list)
    inflight_task: asyncio.Task | None = None
    emitter: PipelineEmitter | None = None


def _new_envelope(message_type: str, session_id: str, payload: Any) -> str:
    """Construct + validate an Envelope and return its JSON wire form."""
    env = Envelope(type=message_type, session_id=session_id, payload=payload)
    return env.model_dump_json()


async def _send_error(
    websocket: ServerConnection,
    session_id: str,
    code: str,
    message: str,
    *,
    retryable: bool = False,
) -> None:
    payload = ErrorPayload(error_code=code, message=message, retryable=retryable)
    await websocket.send(_new_envelope("error", session_id, payload))


async def _stream_gemini_reply(
    websocket: ServerConnection,
    state: SessionState,
    settings: GeminiSettings,
    user_text: str,
    research_mode: str,
) -> None:
    """Stream one user-message reply. Cancellable via asyncio cancellation."""
    logger.info(
        "user-message session=%s research_mode=%s text=%r",
        state.session_id,
        research_mode,
        user_text[:80],
    )

    message_id = new_ulid()
    pipeline_id = new_ulid()
    step_id = new_ulid()
    state.current_pipeline_id = pipeline_id

    # Emit a one-step "thinking" pipeline snapshot so the client has a
    # cancellable handle. When the solver lands the step list will grow.
    thinking_step = PipelineStep(
        step_id=step_id,
        name="llm_generation",
        tool_name="gemini_generate",
        state="running",
    )
    state.current_pipeline_steps = [thinking_step]
    await websocket.send(
        _new_envelope(
            "pipeline-state",
            state.session_id,
            PipelineStatePayload(pipeline_id=pipeline_id, steps=[thinking_step]),
        )
    )

    client = build_client(settings)
    first_token_logged = False
    started_at = asyncio.get_running_loop().time()

    try:
        async for delta in stream_reply(client, settings.model, user_text):
            if not first_token_logged:
                first_token_logged = True
                elapsed_ms = (asyncio.get_running_loop().time() - started_at) * 1000.0
                logger.info(
                    "first-token session=%s elapsed_ms=%.1f model=%s",
                    state.session_id,
                    elapsed_ms,
                    settings.model,
                )
            chunk = AgentMessageChunkPayload(
                message_id=message_id, delta=delta, done=False
            )
            await websocket.send(_new_envelope("agent-message-chunk", state.session_id, chunk))

        # Terminal frame.
        terminal = AgentMessageChunkPayload(message_id=message_id, delta="", done=True)
        await websocket.send(_new_envelope("agent-message-chunk", state.session_id, terminal))

        # Complete the pipeline snapshot.
        thinking_step = PipelineStep(
            step_id=step_id,
            name="llm_generation",
            tool_name="gemini_generate",
            state="complete",
        )
        state.current_pipeline_steps = [thinking_step]
        await websocket.send(
            _new_envelope(
                "pipeline-state",
                state.session_id,
                PipelineStatePayload(pipeline_id=pipeline_id, steps=[thinking_step]),
            )
        )
        state.chat_history.append({"role": "user", "text": user_text})

    except asyncio.CancelledError:
        # Invariant 8 — distinct cancelled step state, not failed.
        cancelled_step = PipelineStep(
            step_id=step_id,
            name="llm_generation",
            tool_name="gemini_generate",
            state="cancelled",
        )
        state.current_pipeline_steps = [cancelled_step]
        try:
            await websocket.send(
                _new_envelope(
                    "pipeline-state",
                    state.session_id,
                    PipelineStatePayload(pipeline_id=pipeline_id, steps=[cancelled_step]),
                )
            )
        except Exception:  # noqa: BLE001 — socket may be down on cancel
            pass
        raise
    except Exception as exc:  # noqa: BLE001 — surface as A.6 LLM_UNAVAILABLE
        logger.exception("gemini stream failed: %s", exc)
        await _send_error(
            websocket,
            state.session_id,
            "LLM_UNAVAILABLE",
            f"Gemini generation failed: {exc}",
            retryable=True,
        )


async def _handle_session_resume(
    websocket: ServerConnection, state: SessionState
) -> None:
    """Reply with a fresh session-state. M1 in-memory only; Mongo replay lands
    when the session-records seam is wired.

    job-0035: routes through the emitter so the initial ``session-state`` is
    A.7-snapshot-shaped (current_pipeline mirrors the live emitter state)."""
    _ensure_emitter(websocket, state)
    await state.emitter.emit_session_state()


def _ensure_emitter(websocket: ServerConnection, state: SessionState) -> None:
    """Bind a ``PipelineEmitter`` to this session if one isn't already.

    The emitter's sink is the WebSocket ``send`` — every transition method
    writes one envelope on the wire (Appendix A.7 replace-not-reconcile)."""
    if state.emitter is not None:
        return

    async def _sink(text: str) -> None:
        await websocket.send(text)

    state.emitter = PipelineEmitter(
        session_id=state.session_id,
        sink=_sink,
        chat_history=state.chat_history,
    )


async def _invoke_tool_via_emitter(
    websocket: ServerConnection,
    state: SessionState,
    tool_name: str,
    params: dict,
) -> Any:
    """Tool-call site (job-0035 integration with the M4 registry).

    Every ``TOOL_REGISTRY[name].fn(...)`` invocation goes through this
    wrapper so that:

    - the per-session ``PipelineEmitter`` auto-creates a step,
    - emits ``pipeline-state`` on every state transition (Appendix A.7),
    - re-emits ``session-state`` whenever the tool returns a ``LayerURI``,
    - propagates ``asyncio.CancelledError`` (Invariant 8) and classifies
      arbitrary exceptions into the open-set A.6 error-code registry.

    The kickoff scopes this to the M4 tool registry; M5+ solver dispatch
    keeps the same shape, simply yielding ``progress_percent`` updates
    through ``emitter.update_progress`` between solver chunks.
    """
    _ensure_emitter(websocket, state)
    if tool_name not in TOOL_REGISTRY:
        # FR-AS-3: unknown tool name surfaces as A.6 TOOL_NOT_FOUND. The
        # emitter classifier already encodes this when a KeyError is raised
        # inside emit_tool_call; we surface up-front so the step never opens.
        await _send_error(
            websocket,
            state.session_id,
            "TOOL_NOT_FOUND",
            f"tool {tool_name!r} not in TOOL_REGISTRY",
        )
        return None
    entry = TOOL_REGISTRY[tool_name]
    state.current_pipeline_id = state.emitter.start_pipeline()
    try:
        result = await state.emitter.emit_tool_call(
            name=entry.metadata.name,
            tool_name=tool_name,
            invoke=lambda: entry.fn(**params),
        )
    finally:
        state.emitter.close_pipeline()
        state.current_pipeline_id = None
    return result


def _parse_invoke_directive(text: str) -> tuple[str, dict] | None:
    """If ``text`` is an ``/invoke <tool_name> <json-params>`` directive,
    return ``(tool_name, params)``; else return None.

    Used by the M4 live-evidence harness to drive real tool invocations
    end-to-end through the registry + emitter. NOT the LLM tool-call path —
    that lands when Gemini-side function-calling is wired (M4 follow-up).
    The directive shape is debug-only; intentionally not in Appendix A.
    """
    if not text.startswith("/invoke "):
        return None
    rest = text[len("/invoke ") :].strip()
    # Split on first whitespace: "<tool_name> <json>"
    parts = rest.split(None, 1)
    if not parts:
        return None
    tool_name = parts[0]
    if len(parts) == 1:
        return tool_name, {}
    import json as _json

    try:
        params = _json.loads(parts[1])
        if not isinstance(params, dict):
            return None
    except Exception:  # noqa: BLE001
        return None
    return tool_name, params


def _make_handler(settings: GeminiSettings):
    """Build the per-connection coroutine, closing over the resolved settings."""

    async def handler(websocket: ServerConnection) -> None:
        # The session_id will be set on the first inbound envelope; we surface
        # an error if the client speaks before establishing one.
        state: SessionState | None = None

        try:
            async for raw in websocket:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")

                # Pre-validate the envelope. Bad shapes get a typed error.
                try:
                    # We don't know the payload type yet; parse generically.
                    import json as _json

                    parsed = _json.loads(raw)
                    msg_type = parsed.get("type")
                    session_id = parsed.get("session_id")
                except Exception as exc:  # noqa: BLE001
                    await websocket.send(
                        _new_envelope(
                            "error",
                            "00000000000000000000000000",
                            ErrorPayload(
                                error_code="INTERNAL_ERROR",
                                message=f"malformed envelope: {exc}",
                            ),
                        )
                    )
                    continue

                if state is None:
                    state = SessionState(session_id=session_id)
                elif state.session_id != session_id:
                    await _send_error(
                        websocket,
                        state.session_id,
                        "INTERNAL_ERROR",
                        "session_id changed mid-connection",
                    )
                    continue

                payload_dict = parsed.get("payload", {})

                # Dispatch on message type. Every payload is re-validated
                # through its concrete grace2_contracts model.
                try:
                    if msg_type == "session-resume":
                        SessionResumePayload.model_validate(payload_dict)
                        await _handle_session_resume(websocket, state)

                    elif msg_type == "user-message":
                        um = UserMessagePayload.model_validate(payload_dict)
                        # Cancel any in-flight generation for this session
                        # before starting a new one (simple M1 policy).
                        if state.inflight_task and not state.inflight_task.done():
                            state.inflight_task.cancel()
                        # job-0035 M4 live-evidence path: ``/invoke <tool>
                        # <json>`` drives real tool invocation through the
                        # PipelineEmitter so the Gemini-side function-calling
                        # path (M4 follow-up) lands on top of an already-
                        # verified emission seam. Non-directive user-messages
                        # still stream through the M1 Gemini path.
                        directive = _parse_invoke_directive(um.text)
                        if directive is not None:
                            tool_name, params = directive
                            task = asyncio.create_task(
                                _invoke_tool_via_emitter(
                                    websocket, state, tool_name, params
                                )
                            )
                        else:
                            task = asyncio.create_task(
                                _stream_gemini_reply(
                                    websocket,
                                    state,
                                    settings,
                                    um.text,
                                    um.research_mode,
                                )
                            )
                        state.inflight_task = task

                    elif msg_type == "cancel":
                        CancelPayload.model_validate(payload_dict)
                        logger.info("cancel session=%s", state.session_id)
                        if state.inflight_task and not state.inflight_task.done():
                            state.inflight_task.cancel()
                            # Wait briefly so the cancel completes deterministically
                            # within NFR-R-3 (30s budget). The pipeline-state
                            # cancelled frame is emitted from inside the task's
                            # CancelledError branch.
                            try:
                                await asyncio.wait_for(state.inflight_task, timeout=5.0)
                            except (asyncio.CancelledError, asyncio.TimeoutError):
                                pass

                    elif msg_type in (
                        "confirm-response",
                        "spatial-input-response",
                        "disambiguation-response",
                        "clarification-response",
                    ):
                        # M1: scaffolding only — no triggers yet. Log and
                        # acknowledge without acting.
                        logger.info("noop M1 message_type=%s", msg_type)

                    else:
                        await _send_error(
                            websocket,
                            state.session_id,
                            "INTERNAL_ERROR",
                            f"unknown message type: {msg_type!r}",
                        )

                except ValidationError as ve:
                    await _send_error(
                        websocket,
                        state.session_id,
                        "TOOL_PARAMS_INVALID",
                        f"payload validation failed: {ve.errors()[0]['msg']}",
                    )

        except Exception:
            logger.exception("connection handler crashed")
        finally:
            if state and state.inflight_task and not state.inflight_task.done():
                state.inflight_task.cancel()

    return handler


async def run_server(host: str = "127.0.0.1", port: int | None = None) -> None:
    """Serve forever. Override port via ``GRACE2_AGENT_PORT``."""
    if port is None:
        port = int(os.environ.get("GRACE2_AGENT_PORT", "8765"))
    settings = load_settings()
    logger.info(
        "starting agent server host=%s port=%d model=%s project=%s location=%s",
        host,
        port,
        settings.model,
        settings.project,
        settings.location,
    )
    handler = _make_handler(settings)
    async with serve(handler, host, port):
        await asyncio.Future()  # serve forever


__all__ = [
    "run_server",
    "SessionState",
    "_invoke_tool_via_emitter",
    "_parse_invoke_directive",
]
