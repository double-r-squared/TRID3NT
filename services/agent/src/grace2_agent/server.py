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

from .main import MAX_TURNS_PER_SESSION

from .adapter import GeminiSettings, build_client, load_settings, stream_reply
from .mode2_classifier import (
    Mode2CandidateEnvelope,
    append_audit_log,
    classify_for_mode2,
)
from .persistence import Persistence
from .pipeline_emitter import PipelineEmitter
from .tools import TOOL_REGISTRY

logger = logging.getLogger("grace2_agent.server")

# Confirmation triggers (FR-AS-8). Empty for M1: solver runs and non-session
# Mongo writes will populate this when those code paths land. Session-record
# writes (Appendix D.6) are NOT a trigger — that carveout is documented in
# the report, not represented as data here.
CONFIRMATION_TRIGGERS: set[str] = set()


# job-0115: app-level Persistence singleton (Wave 1.5).
#
# The MongoDB Atlas MCP server is the LLM-facing DB path (FR-AS-4, Decision F).
# ``Persistence`` wraps it with a typed surface that the agent code calls into
# (CaseSummary / User / SecretRecord / CaseChatMessage). The singleton is
# bound at startup if ``GRACE2_MONGO_MCP_URL`` is set OR a stdio MCP config is
# resolved (via the existing ``grace2_agent.mcp.MCPClient``); otherwise it
# stays ``None`` and callers fall back to in-memory state (the M1 path).
#
# Holding a module-level singleton (rather than per-connection) is intentional:
# - the MCP client is expensive to start (subprocess spawn / TLS handshake);
# - per-session writes only need a typed wrapper, not connection isolation;
# - the singleton resets on process restart so the test harness can swap it.
_PERSISTENCE: Persistence | None = None


def get_persistence() -> Persistence | None:
    """Return the app-level ``Persistence`` singleton, or ``None`` if unbound.

    Callers (chiefly the message-dispatch path in this module) MUST handle
    the ``None`` case gracefully — the M1 in-memory path is still supported
    when the MCP environment is not provisioned (e.g. CI without Atlas).
    """
    return _PERSISTENCE


def set_persistence(p: Persistence | None) -> None:
    """Bind or clear the app-level ``Persistence`` singleton.

    The agent service startup path calls this once after launching the MCP
    client; tests call it directly with a mock-backed ``Persistence`` to
    exercise the wired-in code paths.
    """
    global _PERSISTENCE
    _PERSISTENCE = p


async def init_persistence_from_env() -> Persistence | None:
    """Resolve a ``Persistence`` instance from environment configuration.

    Order:
    1. ``GRACE2_MONGO_MCP_URL`` — if set, this is the live MCP endpoint
       (Cloud Run sidecar URL once OQ-2 lands). For v0.1 the live deployment
       always uses the stdio sidecar path (FR-AS-4 + OQ-2 resolution), so
       this is reserved for the future HTTP MCP transport.
    2. ``GRACE2_MONGO_MCP_STDIO=1`` — launch the ``mongodb-mcp-server``
       stdio subprocess using ``MCPClient.start`` and the SRV from
       Secret Manager. This is the production deployment path.
    3. Otherwise — return None; the agent service still starts (M1
       in-memory chat/pipeline path keeps working), and any caller that
       requires persistence raises a clear error.

    Returns the ``Persistence`` instance or ``None``.
    """
    url = os.environ.get("GRACE2_MONGO_MCP_URL")
    if url:
        logger.warning(
            "GRACE2_MONGO_MCP_URL=%s is reserved for the HTTP MCP transport (not yet wired); "
            "falling through to stdio resolution",
            url,
        )
    if os.environ.get("GRACE2_MONGO_MCP_STDIO") == "1":
        from .mcp import MCPClient, fetch_srv_from_secret_manager

        srv = fetch_srv_from_secret_manager()
        client = await MCPClient.start(srv)
        logger.info("MCP client started; binding Persistence singleton")
        p = Persistence(client)
        set_persistence(p)
        return p
    logger.info(
        "MCP not provisioned (set GRACE2_MONGO_MCP_STDIO=1 to enable); "
        "Persistence singleton remains unbound"
    )
    return None


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
    # FR-FR-3 (job-0048): per-session turn counter.  Increments on every
    # user-message dispatch (Gemini stream or /invoke directive). When
    # turn_count > MAX_TURNS_PER_SESSION the agent refuses further dispatch
    # and emits a ``session-state(status="max_turns_reached")`` envelope.
    # New WebSocket connection → new SessionState → fresh counter at 0.
    turn_count: int = 0


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


async def _handle_max_turns_reached(
    websocket: ServerConnection, state: SessionState
) -> None:
    """FR-FR-3 (job-0048): emit the cap-hit envelope sequence.

    1. Emit ``session-state`` with ``status="max_turns_reached"`` so the
       client knows the session is at its turn limit.
    2. Send a closing ``agent-message-chunk`` summarising what's been done
       and directing the user to start a new session.

    Called instead of the normal dispatch when ``state.turn_count`` exceeds
    ``MAX_TURNS_PER_SESSION``. No tool calls are dispatched.
    """
    _ensure_emitter(websocket, state)
    # Re-emit session-state with the cap status so the client can render a
    # "session full" indicator.
    closing_payload = SessionStatePayload(
        chat_history=state.chat_history,
        status="max_turns_reached",
    )
    await websocket.send(
        _new_envelope("session-state", state.session_id, closing_payload)
    )
    # Send a closing agent-message-chunk so the user sees a human-readable
    # explanation in the chat panel.
    message_id = new_ulid()
    closing_text = (
        "This session has reached its turn limit "
        f"({MAX_TURNS_PER_SESSION} turns). "
        "No further tool calls will be dispatched. "
        "Start a new session to continue working."
    )
    await websocket.send(
        _new_envelope(
            "agent-message-chunk",
            state.session_id,
            AgentMessageChunkPayload(
                message_id=message_id, delta=closing_text, done=False
            ),
        )
    )
    await websocket.send(
        _new_envelope(
            "agent-message-chunk",
            state.session_id,
            AgentMessageChunkPayload(message_id=message_id, delta="", done=True),
        )
    )
    logger.info(
        "max-turns-reached session=%s turn_count=%d limit=%d",
        state.session_id,
        state.turn_count,
        MAX_TURNS_PER_SESSION,
    )


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
    # job-0101: Mode 2 .gov/.edu classifier — when web_fetch returns a dict
    # that looks like a structured-data candidate, emit a `mode2-candidate`
    # envelope and append an audit-log line. Deterministic side-effect; the
    # web modal (Wave 2/3) renders the offer. See mode2_classifier.py.
    if tool_name == "web_fetch" and isinstance(result, dict):
        await _maybe_emit_mode2_candidate(websocket, state, result)
    return result


async def _maybe_emit_mode2_candidate(
    websocket: ServerConnection, state: SessionState, result: dict
) -> None:
    """Run ``classify_for_mode2`` and emit ``mode2-candidate`` if it lands.

    Best-effort: a classifier or send failure is logged but never raised — the
    caller already returned the tool result and we will not let a side-effect
    take down a perfectly good ``web_fetch`` invocation (FR-AS-7 boundary).
    """
    import json as _json

    try:
        candidate = classify_for_mode2(result)
        if candidate is None:
            return
        envelope = Mode2CandidateEnvelope(candidate=candidate)
        await websocket.send(
            _json.dumps(
                {
                    "type": "mode2-candidate",
                    "session_id": state.session_id,
                    "payload": envelope.to_wire_dict(),
                }
            )
        )
        append_audit_log(candidate, session_id=state.session_id)
        logger.info(
            "mode2-candidate session=%s url=%s confidence=%.2f patterns=%s",
            state.session_id,
            candidate.url,
            candidate.confidence,
            candidate.detected_patterns,
        )
    except Exception:  # noqa: BLE001 — side effect, never bubble up
        logger.exception("mode2-candidate emission failed")


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
                        # FR-FR-3 (job-0048): check the turn cap BEFORE
                        # dispatching. Increment first so "26th turn" fires
                        # on turn_count == MAX_TURNS_PER_SESSION + 1 (i.e.
                        # the (MAX+1)th call). Sessions that have already hit
                        # the cap continue to be refused on every subsequent
                        # user-message with the same cap-hit envelope.
                        state.turn_count += 1
                        if (
                            MAX_TURNS_PER_SESSION > 0
                            and state.turn_count > MAX_TURNS_PER_SESSION
                        ):
                            await _handle_max_turns_reached(websocket, state)
                            continue
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
    """Serve forever. Override port via ``GRACE2_AGENT_PORT``.

    job-0115: best-effort init of the ``Persistence`` singleton. If the MCP
    environment is not provisioned (the typical local-dev case), the agent
    service starts anyway — the M1 in-memory chat/pipeline path keeps
    working, and any caller that requires persistence raises a clear error.
    """
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
    try:
        await init_persistence_from_env()
    except Exception as exc:  # noqa: BLE001 — startup must not abort on MCP issues
        logger.warning("Persistence init failed (continuing without MCP): %s", exc)
    handler = _make_handler(settings)
    async with serve(handler, host, port):
        await asyncio.Future()  # serve forever


__all__ = [
    "run_server",
    "SessionState",
    "_invoke_tool_via_emitter",
    "_parse_invoke_directive",
    "get_persistence",
    "set_persistence",
    "init_persistence_from_env",
]
