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

from grace2_contracts import new_ulid, now_utc
from grace2_contracts.case import (
    CaseChatMessage,
    CaseCommandEnvelopePayload,
    CaseListEnvelopePayload,
    CaseOpenEnvelopePayload,
    CaseSummary,
)
from grace2_contracts.payload_warning import (
    HARD_CAP_MB_DEFAULT,
    WARNING_THRESHOLD_MB_DEFAULT,
    PayloadConfirmationEnvelopePayload,
    PayloadWarningEnvelopePayload,
)
from grace2_contracts.secrets import (
    SecretAddEnvelopePayload,
    SecretRevokeEnvelopePayload,
    SecretsListEnvelopePayload,
)
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

from .adapter import (
    FunctionCallEvent,
    GeminiSettings,
    SYSTEM_PROMPT,
    TextDeltaEvent,
    build_client,
    build_tool_declarations,
    load_settings,
    stream_events,
    stream_reply,  # noqa: F401 — retained for any callers that use it directly
)
from .auth_handshake import (
    AuthResult,
    authenticate_token,
    build_auth_ack,
    get_auth_token_timeout_s,
)
from .case_lifecycle import CaseLifecycleError, ensure_case_qgs
from .mode2_classifier import (
    Mode2CandidateEnvelope,
    append_audit_log,
    classify_for_mode2,
)
from .persistence import Persistence
from .pipeline_emitter import PipelineEmitter
from .secrets_handler import (
    SecretError,
    handle_secret_add,
    handle_secret_revoke,
    handle_secrets_list,
)
from .tools import TOOL_REGISTRY

# job-0122: auth-token envelope (Appendix H.5 connect handshake).
from grace2_contracts.auth import AuthTokenEnvelope

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
    # job-0121 (FR-MP-6): per-connection active-Case context.
    #
    # ``None`` for fresh sessions (no Case selected yet — the M1 stateless
    # demo path remains supported). Updated by ``case-command(create|select)``;
    # cleared (left as-is) on ``archive``/``delete``. When non-None, the
    # tool-call wrapper (``_invoke_tool_via_emitter``) carries the case
    # context into tools that opt in via a ``case_id`` parameter
    # (currently ``publish_layer``); chat persistence routes every
    # user-message + agent reply into Mongo via ``Persistence``.
    active_case_id: str | None = None
    # job-0121: per-turn layer + map-command emission accumulators. Reset at
    # the start of every dispatch (Gemini stream or /invoke tool). The
    # CaseChatMessage write at turn close reads from these so a Case replay
    # can re-bind layers via the same emission sequence.
    current_turn_layer_ids: list[str] = field(default_factory=list)
    current_turn_pipeline_id: str | None = None
    # job-0122 (Appendix H.5): per-connection authenticated user context.
    #
    # Populated by the connect-handshake (``_perform_auth_handshake``) after
    # the ``auth-token`` envelope verifies (or after the 5-second anonymous
    # fallback timeout). When set, every subsequent envelope for this
    # connection is scoped to ``authenticated_user_id`` — Case lookups
    # (``Persistence.list_cases_for_user``) filter by it, and Case creation
    # binds it as ``owner_user_id``. ``None`` only between connect and the
    # handshake completion; never ``None`` after handshake.
    authenticated_user_id: str | None = None
    is_anonymous: bool = True
    firebase_uid: str | None = None
    tier: str = "free"
    auth_handshake_complete: bool = False
    # job-0127 (Wave 2): per-session pending payload-warning gates.
    # Key is the ``warning_id`` ULID; value is an asyncio.Future that the
    # inbound ``tool-payload-confirmation`` handler completes with the user's
    # decision payload. ``_invoke_tool_via_emitter`` awaits it before
    # dispatching (or skipping) the underlying tool.
    pending_payload_warnings: dict[str, asyncio.Future] = field(default_factory=dict)
    # job-0127 (Wave 2): per-session audit log of payload-warning events.
    # Each entry is a dict carrying ``warning_id``, ``tool_name``,
    # ``estimated_mb``, ``threshold_mb``, ``decision`` (set on confirmation),
    # and the ULID timestamps. Surfaces in tests + post-mortem; persisted
    # to the active Case as part of the chat turn record (best-effort).
    payload_warning_audit_log: list[dict] = field(default_factory=list)


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
    """Stream one user-message reply. Cancellable via asyncio cancellation.

    job-0154: now passes the full TOOL_REGISTRY as FunctionDeclarations +
    a focused system prompt to Gemini so it can call tools (e.g.
    ``run_model_flood_scenario``) instead of emitting prose refusals.

    Event loop:
    - ``TextDeltaEvent`` → wrapped in ``agent-message-chunk`` and sent.
    - ``FunctionCallEvent`` → dispatched via ``_invoke_tool_via_emitter``;
      the result is NOT fed back to Gemini in this v0.1 implementation
      (single-shot function-call; the emitter owns the pipeline-state +
      session-state side effects).  A follow-up job adds multi-turn
      function-call / function-response cycling.
    """
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

    # Build tool declarations + system prompt for this request.
    tool_decls = build_tool_declarations(TOOL_REGISTRY)

    try:
        async for event in stream_events(
            client,
            settings.model,
            user_text,
            tool_declarations=tool_decls,
            system_prompt=SYSTEM_PROMPT,
            chat_history=state.chat_history,
        ):
            if not first_token_logged:
                first_token_logged = True
                elapsed_ms = (asyncio.get_running_loop().time() - started_at) * 1000.0
                logger.info(
                    "first-token session=%s elapsed_ms=%.1f model=%s",
                    state.session_id,
                    elapsed_ms,
                    settings.model,
                )

            if isinstance(event, TextDeltaEvent):
                chunk = AgentMessageChunkPayload(
                    message_id=message_id, delta=event.delta, done=False
                )
                await websocket.send(
                    _new_envelope("agent-message-chunk", state.session_id, chunk)
                )

            elif isinstance(event, FunctionCallEvent):
                logger.info(
                    "gemini function-call session=%s tool=%s call_id=%s args=%r",
                    state.session_id,
                    event.name,
                    event.call_id,
                    event.args,
                )
                # Dispatch through the registry + emitter (Invariant 2 — the
                # LLM's tool choice IS the classification).  The terminal
                # agent-message-chunk + pipeline-state(complete) are emitted
                # by the emitter; we close the outer "thinking" step here.
                await _invoke_tool_via_emitter(
                    websocket, state, event.name, event.args
                )

        # Terminal frame for any streamed text.
        terminal = AgentMessageChunkPayload(message_id=message_id, delta="", done=True)
        await websocket.send(_new_envelope("agent-message-chunk", state.session_id, terminal))

        # Complete the pipeline snapshot (LLM generation phase).
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
    A.7-snapshot-shaped (current_pipeline mirrors the live emitter state).

    job-0121: also emits a ``case-list`` so the client renders the left-rail
    Case list on initial connect (FR-MP-6 landing state). Best-effort — if
    Persistence is unbound the case-list emission is skipped and the M1
    in-memory path keeps working."""
    _ensure_emitter(websocket, state)
    await state.emitter.emit_session_state()
    await _emit_case_list(websocket, state)


# --------------------------------------------------------------------------- #
# job-0122: Connect-handshake (Appendix H.5 + H.3)
# --------------------------------------------------------------------------- #


async def _handle_auth_token(
    websocket: ServerConnection,
    state: SessionState,
    payload_dict: dict,
) -> None:
    """Process the client's ``auth-token`` envelope and emit ``auth-ack``.

    Per Appendix H.5 (job-0122 scope):

    1. Validate the payload through ``AuthTokenEnvelope``.
    2. Call ``authenticate_token`` → resolves to a ``User`` via Persistence
       (or provisions an anonymous fallback).
    3. Bind the resolved ``user_id`` + tier + anonymous-flag into the
       SessionState — every subsequent envelope is scoped to this user.
    4. Emit ``auth-ack`` so the client knows its session identity.
    """
    tok: AuthTokenEnvelope | None
    try:
        tok = AuthTokenEnvelope.model_validate(payload_dict)
    except ValidationError as ve:
        await _send_error(
            websocket,
            state.session_id,
            "AUTH_TOKEN_INVALID",
            f"auth-token validation failed: {ve.errors()[0]['msg']}",
        )
        # Even on validation failure we run the anonymous fallback so the
        # connection is still usable (per H.3).
        tok = None

    result = await authenticate_token(tok, get_persistence())
    _bind_auth_result(state, result)
    ack = build_auth_ack(result)
    await websocket.send(_new_envelope("auth-ack", state.session_id, ack))
    logger.info(
        "auth-ack session=%s user_id=%s anonymous=%s tier=%s firebase_uid=%s",
        state.session_id,
        result.user.user_id,
        result.is_anonymous,
        result.tier,
        result.firebase_uid,
    )


def _bind_auth_result(state: SessionState, result: AuthResult) -> None:
    """Copy the resolved auth identity into the SessionState.

    Separate from ``_handle_auth_token`` so tests can drive the bind
    directly without parsing an envelope.
    """
    state.authenticated_user_id = result.user.user_id
    state.is_anonymous = result.is_anonymous
    state.firebase_uid = result.firebase_uid
    state.tier = result.tier
    state.auth_handshake_complete = True


async def _ensure_auth_handshake(
    websocket: ServerConnection,
    state: SessionState,
) -> None:
    """Synchronous fallback: if the handshake hasn't run, run it as anonymous.

    Called when a non-``auth-token`` envelope arrives before the handshake
    has completed (the client either didn't send auth-token, or another
    envelope raced ahead). Mirrors the 5-second timeout path from H.3 —
    instead of waiting 5 seconds we trip the anonymous fallback inline so
    the user is bound before their first real interaction.
    """
    if state.auth_handshake_complete:
        return
    result = await authenticate_token(None, get_persistence())
    _bind_auth_result(state, result)
    ack = build_auth_ack(result)
    try:
        await websocket.send(_new_envelope("auth-ack", state.session_id, ack))
    except Exception:  # noqa: BLE001 — socket may be down
        pass
    logger.info(
        "auth-ack(implicit-anonymous) session=%s user_id=%s",
        state.session_id,
        result.user.user_id,
    )


# --------------------------------------------------------------------------- #
# Case lifecycle handlers (job-0121, FR-MP-6)
# --------------------------------------------------------------------------- #


async def _emit_case_list(websocket: ServerConnection, state: SessionState) -> None:
    """Emit the ``case-list`` envelope for the client's left rail.

    Best-effort: if Persistence is unbound (M1 in-memory path) we silently
    skip. If the listing call fails we log + skip; the case-list is a
    derivable view, so failing it should not break the chat path.

    Auth-stub note: ``list_cases_for_user`` currently passes the session_id
    as the user_id placeholder. The Auth/Users track will replace this with
    the resolved Firebase UID; the persistence layer's filter is already
    backward-compatible (``$or`` includes ``user_id: {$exists: False}``).
    """
    p = get_persistence()
    if p is None:
        logger.debug("case-list: Persistence unbound; skipping emit")
        return
    try:
        cases = await p.list_cases_for_user(state.session_id)
    except Exception:  # noqa: BLE001 — best-effort
        logger.exception("case-list: list_cases_for_user failed")
        return
    payload = CaseListEnvelopePayload(cases=cases)
    await websocket.send(_new_envelope("case-list", state.session_id, payload))
    logger.info(
        "case-list emitted session=%s count=%d", state.session_id, len(cases)
    )


async def _emit_case_open(
    websocket: ServerConnection,
    state: SessionState,
    case_id: str,
) -> None:
    """Emit a ``case-open`` envelope hydrating ``CaseSessionState`` from Mongo.

    Sets ``state.active_case_id`` BEFORE emitting so subsequent tool calls
    (and chat persistence) carry the Case context. If the Case is missing
    or Persistence is unbound, emits a ``case-open`` with ``session_state=None``
    so the client falls back to the empty state per
    ``CaseOpenEnvelopePayload`` semantics.
    """
    state.active_case_id = case_id
    p = get_persistence()
    if p is None:
        logger.warning(
            "case-open session=%s case=%s: Persistence unbound; emitting empty",
            state.session_id,
            case_id,
        )
        payload = CaseOpenEnvelopePayload(session_state=None)
        await websocket.send(
            _new_envelope("case-open", state.session_id, payload)
        )
        return
    try:
        session_state = await p.get_session_state(case_id)
    except Exception:  # noqa: BLE001
        logger.exception(
            "case-open: get_session_state failed for case=%s", case_id
        )
        payload = CaseOpenEnvelopePayload(session_state=None)
        await websocket.send(
            _new_envelope("case-open", state.session_id, payload)
        )
        return
    payload = CaseOpenEnvelopePayload(session_state=session_state)
    await websocket.send(_new_envelope("case-open", state.session_id, payload))
    logger.info(
        "case-open session=%s case=%s chat=%d",
        state.session_id,
        case_id,
        len(session_state.chat_history),
    )


async def _handle_case_command(
    websocket: ServerConnection,
    state: SessionState,
    cmd: CaseCommandEnvelopePayload,
) -> None:
    """Dispatch one ``case-command`` (FR-MP-6 Case lifecycle).

    Commands:

    - ``create`` — generate a new ``CaseSummary``, persist via
      ``Persistence.upsert_case``, set as active, emit ``case-open`` with
      the fresh (empty) session state, then refresh ``case-list``.
    - ``select`` — load the persisted ``CaseSessionState`` and emit
      ``case-open`` with the full rehydration (chat history, loaded
      layers, pipeline history — per FR-MP-6 chat-replay default).
    - ``rename`` — update ``CaseSummary.title``, persist, emit
      ``case-list`` updated.
    - ``archive`` — soft-archive via ``Persistence.archive_case``, emit
      ``case-list`` updated.
    - ``delete`` — soft-delete via ``Persistence.delete_case``, emit
      ``case-list`` updated. Memory rule: the web UI confirms with the
      user BEFORE firing this command; the server does not double-confirm.

    Errors surface as ``error`` envelopes with ``error_code=INTERNAL_ERROR``
    (the case-lifecycle commands are NOT a confirmation trigger per
    FR-AS-8; only solver runs and non-session-collection Mongo writes are).
    """
    p = get_persistence()
    if p is None:
        await _send_error(
            websocket,
            state.session_id,
            "INTERNAL_ERROR",
            "case-command requires Persistence; the agent service was started "
            "without GRACE2_MONGO_MCP_STDIO=1 and cannot satisfy FR-MP-6.",
        )
        return

    command = cmd.command

    if command == "create":
        # Generate a fresh ULID and persist. ``args.title`` is an optional hint.
        new_case_id = new_ulid()
        title = (cmd.args or {}).get("title") or "Untitled Case"
        if not isinstance(title, str) or not title.strip():
            title = "Untitled Case"
        now = now_utc()
        case = CaseSummary(
            case_id=new_case_id,
            title=title.strip(),
            created_at=now,
            updated_at=now,
            status="active",
        )
        try:
            await p.upsert_case(case)
        except Exception as exc:  # noqa: BLE001
            logger.exception("case-command(create) upsert failed: %s", exc)
            await _send_error(
                websocket,
                state.session_id,
                "INTERNAL_ERROR",
                f"case create failed: {exc}",
            )
            return
        state.active_case_id = new_case_id
        # Emit case-open with the empty session state for the fresh Case.
        payload = CaseOpenEnvelopePayload(
            session_state=await p.get_session_state(new_case_id)
        )
        await websocket.send(
            _new_envelope("case-open", state.session_id, payload)
        )
        await _emit_case_list(websocket, state)
        logger.info(
            "case-command create session=%s case=%s title=%r",
            state.session_id,
            new_case_id,
            title,
        )
        return

    if command == "select":
        if not cmd.case_id:
            await _send_error(
                websocket,
                state.session_id,
                "INTERNAL_ERROR",
                "case-command(select) requires case_id",
            )
            return
        await _emit_case_open(websocket, state, cmd.case_id)
        return

    if command == "rename":
        if not cmd.case_id:
            await _send_error(
                websocket,
                state.session_id,
                "INTERNAL_ERROR",
                "case-command(rename) requires case_id",
            )
            return
        new_title = (cmd.args or {}).get("title")
        if not isinstance(new_title, str) or not new_title.strip():
            await _send_error(
                websocket,
                state.session_id,
                "INTERNAL_ERROR",
                "case-command(rename) requires args.title (non-empty string)",
            )
            return
        existing = await p.get_case(cmd.case_id)
        if existing is None:
            await _send_error(
                websocket,
                state.session_id,
                "INTERNAL_ERROR",
                f"case-command(rename): case {cmd.case_id!r} not found",
            )
            return
        updated = existing.model_copy(
            update={"title": new_title.strip(), "updated_at": now_utc()}
        )
        try:
            await p.upsert_case(updated)
        except Exception as exc:  # noqa: BLE001
            logger.exception("case-command(rename) upsert failed: %s", exc)
            await _send_error(
                websocket,
                state.session_id,
                "INTERNAL_ERROR",
                f"case rename failed: {exc}",
            )
            return
        await _emit_case_list(websocket, state)
        logger.info(
            "case-command rename session=%s case=%s title=%r",
            state.session_id,
            cmd.case_id,
            new_title,
        )
        return

    if command == "archive":
        if not cmd.case_id:
            await _send_error(
                websocket,
                state.session_id,
                "INTERNAL_ERROR",
                "case-command(archive) requires case_id",
            )
            return
        try:
            await p.archive_case(cmd.case_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("case-command(archive) failed: %s", exc)
            await _send_error(
                websocket,
                state.session_id,
                "INTERNAL_ERROR",
                f"case archive failed: {exc}",
            )
            return
        await _emit_case_list(websocket, state)
        logger.info(
            "case-command archive session=%s case=%s",
            state.session_id,
            cmd.case_id,
        )
        return

    if command == "delete":
        if not cmd.case_id:
            await _send_error(
                websocket,
                state.session_id,
                "INTERNAL_ERROR",
                "case-command(delete) requires case_id",
            )
            return
        try:
            await p.delete_case(cmd.case_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("case-command(delete) failed: %s", exc)
            await _send_error(
                websocket,
                state.session_id,
                "INTERNAL_ERROR",
                f"case delete failed: {exc}",
            )
            return
        # If the deleted Case was the active one, clear the context — any
        # subsequent publish will fall through to the single-tenant default
        # rather than mutate a soft-deleted ``.qgs``.
        if state.active_case_id == cmd.case_id:
            state.active_case_id = None
        await _emit_case_list(websocket, state)
        logger.info(
            "case-command delete session=%s case=%s",
            state.session_id,
            cmd.case_id,
        )
        return

    # Closed enum guard — pydantic should have rejected before we got here.
    await _send_error(
        websocket,
        state.session_id,
        "INTERNAL_ERROR",
        f"unknown case-command: {command!r}",
    )


async def _persist_chat_turn(
    state: SessionState,
    *,
    role: str,
    content: str,
    pipeline_id: str | None = None,
) -> None:
    """Append one ``CaseChatMessage`` to Mongo for the active Case.

    Best-effort: a missing Persistence binding OR no active Case context
    short-circuits (the M1 in-memory chat keeps working). A failed write
    is logged but not raised — chat persistence is a side-effect, not the
    happy path of message delivery.

    Per FR-AS-8 / Decision F the chat-message collection is part of the
    agent's own session record (it is per-turn replay material, not a
    solver result); the confirmation-hook carveout in ``CONFIRMATION_TRIGGERS``
    means this write does NOT pause for user approval.
    """
    if not state.active_case_id:
        return
    p = get_persistence()
    if p is None:
        return
    msg = CaseChatMessage(
        message_id=new_ulid(),
        case_id=state.active_case_id,
        role=role,  # type: ignore[arg-type]
        content=content,
        pipeline_id=pipeline_id,
        layer_emissions=list(state.current_turn_layer_ids),
        created_at=now_utc(),
    )
    try:
        await p.append_chat_message(msg)
        logger.debug(
            "chat-persist session=%s case=%s role=%s msg_id=%s pipeline_id=%s layers=%d",
            state.session_id,
            state.active_case_id,
            role,
            msg.message_id,
            pipeline_id,
            len(msg.layer_emissions),
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "chat-persist failed session=%s case=%s role=%s",
            state.session_id,
            state.active_case_id,
            role,
        )


# --------------------------------------------------------------------------- #
# Payload-warning gate (job-0127, sprint-12-mega Wave 2).
# --------------------------------------------------------------------------- #


def _get_warning_threshold_mb() -> float:
    """Read the warning threshold from env, falling back to the default."""
    raw = os.environ.get("GRACE2_PAYLOAD_WARNING_MB")
    if raw is None:
        return WARNING_THRESHOLD_MB_DEFAULT
    try:
        return float(raw)
    except ValueError:
        logger.warning(
            "GRACE2_PAYLOAD_WARNING_MB=%r is not a float; using default %s",
            raw,
            WARNING_THRESHOLD_MB_DEFAULT,
        )
        return WARNING_THRESHOLD_MB_DEFAULT


def _get_hard_cap_mb() -> float:
    """Read the hard cap from env, falling back to the default."""
    raw = os.environ.get("GRACE2_PAYLOAD_HARDCAP_MB")
    if raw is None:
        return HARD_CAP_MB_DEFAULT
    try:
        return float(raw)
    except ValueError:
        logger.warning(
            "GRACE2_PAYLOAD_HARDCAP_MB=%r is not a float; using default %s",
            raw,
            HARD_CAP_MB_DEFAULT,
        )
        return HARD_CAP_MB_DEFAULT


def _resolve_payload_estimator(tool_name: str, estimator_name: str) -> Any | None:
    """Look up the named estimator callable on the tool's module.

    The Wave 1.5 ``AtomicToolMetadata.payload_mb_estimator_name`` field
    carries a Python identifier (not the callable itself) so the metadata
    stays serializable. Resolution at gate-time walks
    ``RegisteredTool.module`` to find the callable. Returns ``None`` if the
    module/attribute lookup fails — the gate then skips for this call.
    """
    try:
        from importlib import import_module

        entry = TOOL_REGISTRY.get(tool_name)
        if entry is None:
            return None
        mod = import_module(entry.module)
        fn = getattr(mod, estimator_name, None)
        if not callable(fn):
            return None
        return fn
    except Exception:  # noqa: BLE001 — defensive; gate must never raise
        logger.exception(
            "payload-warning: estimator lookup failed tool=%s name=%s",
            tool_name,
            estimator_name,
        )
        return None


async def _maybe_gate_on_payload_warning(
    websocket: ServerConnection,
    state: SessionState,
    tool_name: str,
    params: dict,
) -> tuple[bool, dict]:
    """Run the payload-warning gate before dispatching ``tool_name``.

    Returns ``(should_dispatch, effective_params)``:

    - ``(True, params)`` — no warning needed (no estimator, estimate below
      threshold) OR user picked ``proceed``. Dispatch with ``params``.
    - ``(True, revised_args)`` — user picked ``narrow_scope``. Dispatch with
      the user's revised args.
    - ``(False, params)`` — user picked ``cancel`` OR the gate timed out.
      Skip the dispatch; the caller surfaces a typed failure to chat.

    Audit-log entries are appended to ``state.payload_warning_audit_log``
    on both emission AND decision. Never raises — a gate failure logs +
    falls through to dispatch (the gate is a UX nudge, not a hard
    invariant; a broken estimator should not break the tool).
    """
    entry = TOOL_REGISTRY.get(tool_name)
    if entry is None:
        return True, params
    estimator_name = entry.metadata.payload_mb_estimator_name
    if not estimator_name:
        return True, params
    estimator_fn = _resolve_payload_estimator(tool_name, estimator_name)
    if estimator_fn is None:
        return True, params
    try:
        estimated_mb = float(estimator_fn(**params))
    except Exception:  # noqa: BLE001 — never let the gate kill a tool
        logger.exception(
            "payload-warning: estimator raised tool=%s name=%s; skipping gate",
            tool_name,
            estimator_name,
        )
        return True, params

    threshold_mb = _get_warning_threshold_mb()
    hard_cap_mb = _get_hard_cap_mb()
    if estimated_mb < threshold_mb:
        return True, params

    over_hard_cap = estimated_mb > hard_cap_mb
    options = (
        ["cancel", "narrow_scope"]
        if over_hard_cap
        else ["proceed", "cancel", "narrow_scope"]
    )
    recommendation = (
        f"Estimated payload {estimated_mb:.1f} MB exceeds the "
        f"{'hard cap' if over_hard_cap else 'warning threshold'} "
        f"({hard_cap_mb if over_hard_cap else threshold_mb:.0f} MB). "
        "Consider narrowing bbox or other scope parameters."
    )

    warning_id = new_ulid()
    warning_payload = PayloadWarningEnvelopePayload(
        warning_id=warning_id,
        tool_name=tool_name,
        tool_args=params,
        estimated_mb=estimated_mb,
        threshold_mb=hard_cap_mb if over_hard_cap else threshold_mb,
        recommendation=recommendation,
        options=options,
    )

    # Audit-log the emission.
    audit_entry: dict = {
        "warning_id": warning_id,
        "tool_name": tool_name,
        "estimated_mb": estimated_mb,
        "threshold_mb": warning_payload.threshold_mb,
        "options": list(options),
        "emitted_at": now_utc().isoformat(),
        "decision": None,
    }
    state.payload_warning_audit_log.append(audit_entry)

    # Create the future the inbound handler will complete.
    loop = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()
    state.pending_payload_warnings[warning_id] = fut

    await websocket.send(
        _new_envelope("tool-payload-warning", state.session_id, warning_payload)
    )
    logger.info(
        "payload-warning emitted session=%s tool=%s warning_id=%s estimated_mb=%.2f over_hard_cap=%s",
        state.session_id,
        tool_name,
        warning_id,
        estimated_mb,
        over_hard_cap,
    )

    # Await the confirmation (TTL on the envelope is advisory; we honour it
    # with an asyncio timeout so the dispatch coroutine doesn't hang forever).
    try:
        decision_payload: PayloadConfirmationEnvelopePayload = await asyncio.wait_for(
            fut, timeout=warning_payload.ttl_seconds
        )
    except asyncio.TimeoutError:
        audit_entry["decision"] = "timeout"
        logger.warning(
            "payload-warning timeout session=%s tool=%s warning_id=%s",
            state.session_id,
            tool_name,
            warning_id,
        )
        await _send_error(
            websocket,
            state.session_id,
            "CONFIRMATION_TIMEOUT",
            f"tool {tool_name!r} payload-warning gate timed out",
        )
        return False, params
    finally:
        state.pending_payload_warnings.pop(warning_id, None)

    audit_entry["decision"] = decision_payload.decision
    audit_entry["decided_at"] = now_utc().isoformat()
    logger.info(
        "payload-warning decision session=%s tool=%s warning_id=%s decision=%s",
        state.session_id,
        tool_name,
        warning_id,
        decision_payload.decision,
    )

    if decision_payload.decision == "cancel":
        await _send_error(
            websocket,
            state.session_id,
            "USER_INPUT_CANCELLED",
            f"tool {tool_name!r} cancelled by user at payload-warning gate "
            f"(estimated {estimated_mb:.1f} MB)",
        )
        return False, params
    if decision_payload.decision == "proceed":
        if over_hard_cap:
            # Defense in depth: the warning envelope omitted ``proceed`` so a
            # well-behaved client can't pick it. Refuse if it does anyway.
            await _send_error(
                websocket,
                state.session_id,
                "TOOL_PARAMS_INVALID",
                f"tool {tool_name!r} exceeds hard cap "
                f"({estimated_mb:.1f} > {hard_cap_mb:.0f} MB); "
                "'proceed' is not an allowed response",
            )
            return False, params
        return True, params
    # narrow_scope
    revised = decision_payload.revised_args or {}
    return True, revised


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

    # job-0121: per-Case ``.qgs`` lazy-init for ``publish_layer``.
    #
    # When invoked inside a Case context (``state.active_case_id`` set) we
    # resolve (or initialize) the per-Case ``.qgs`` URI BEFORE the tool body
    # runs, then substitute it into ``project_qgs_uri`` so the worker mutates
    # the case-scoped file rather than the shared default. This is the
    # OQ-62-QGS-MUTATION-CONFLICT resolution path.
    if tool_name == "publish_layer" and state.active_case_id:
        try:
            case_qgs = await ensure_case_qgs(
                get_persistence(), state.active_case_id
            )
        except CaseLifecycleError as exc:
            logger.warning(
                "case-qgs lazy-init failed code=%s case=%s err=%s; "
                "falling back to default .qgs",
                exc.error_code,
                state.active_case_id,
                exc,
            )
        else:
            # Substitute (additively) without clobbering an explicit override.
            params = dict(params)
            params.setdefault("project_qgs_uri", case_qgs)
            params.setdefault("case_id", state.active_case_id)
            logger.info(
                "publish_layer routed to case-scoped qgs case=%s qgs=%s",
                state.active_case_id,
                case_qgs,
            )

    # Drop ``case_id`` for tools that don't declare it — defense in depth.
    # ``publish_layer`` accepts it; other tools do not.
    if tool_name != "publish_layer" and "case_id" in params:
        params = {k: v for k, v in params.items() if k != "case_id"}

    # job-0127 (Wave 2): payload-warning gate. When the tool declares a
    # ``payload_mb_estimator_name`` and the estimate exceeds the warning
    # threshold, emit ``tool-payload-warning`` and await
    # ``tool-payload-confirmation``. Skip / revise dispatch per the user's
    # decision. No-op when the tool didn't declare an estimator.
    should_dispatch, params = await _maybe_gate_on_payload_warning(
        websocket, state, tool_name, params
    )
    if not should_dispatch:
        return None

    state.current_pipeline_id = state.emitter.start_pipeline()
    state.current_turn_pipeline_id = state.current_pipeline_id
    try:
        result = await state.emitter.emit_tool_call(
            name=entry.metadata.name,
            tool_name=tool_name,
            invoke=lambda: entry.fn(**params),
        )
    finally:
        state.emitter.close_pipeline()
        state.current_pipeline_id = None

    # Track layer emissions on the active turn so the next ``CaseChatMessage``
    # write captures them. ``publish_layer`` returns a WMS URL string; we use
    # the tool's ``layer_id`` parameter as the canonical layer identifier.
    if tool_name == "publish_layer" and "layer_id" in params:
        lid = params.get("layer_id")
        if isinstance(lid, str) and lid:
            state.current_turn_layer_ids.append(lid)

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


# --------------------------------------------------------------------------- #
# Dispatch wrappers with chat persistence (job-0121, FR-MP-6)
# --------------------------------------------------------------------------- #


async def _dispatch_gemini_and_persist(
    websocket: ServerConnection,
    state: SessionState,
    settings: GeminiSettings,
    user_text: str,
    research_mode: str,
) -> None:
    """Stream Gemini reply, then persist the agent's reply to the active Case.

    Wraps ``_stream_gemini_reply`` so the Case chat-history append happens
    after the stream completes (the streamed text is the canonical
    ``content`` field on ``CaseChatMessage``). On cancel/error we still
    attempt a best-effort persist of whatever the chat-history accumulator
    captured (the stream pushes a ``{role, text}`` dict on completion).
    """
    pre_chat_len = len(state.chat_history)
    try:
        await _stream_gemini_reply(
            websocket, state, settings, user_text, research_mode
        )
    finally:
        # The current Gemini streaming path appends ``{role: user, text: ...}``
        # to ``state.chat_history`` on stream complete (line ~362 above).
        # The agent's reply text itself isn't accumulated into chat_history
        # — we record a placeholder content marker so Case replay knows the
        # turn happened. A future job (full reply accumulation) will
        # capture the actual streamed deltas; for now the per-turn record
        # carries the user message + tool emissions, which is sufficient
        # for FR-MP-6 replay (chat-replay default per user 2026-06-08).
        if state.active_case_id and len(state.chat_history) > pre_chat_len:
            # Best-effort: append an agent-reply CaseChatMessage so a Case
            # replay shows a marker for the turn. ``content`` is empty
            # because the Gemini stream isn't currently accumulated; the
            # ``layer_emissions`` accumulator carries the side-effects.
            await _persist_chat_turn(
                state,
                role="agent",
                content="",  # reply text not currently accumulated
                pipeline_id=state.current_turn_pipeline_id,
            )


async def _dispatch_tool_and_persist(
    websocket: ServerConnection,
    state: SessionState,
    tool_name: str,
    params: dict,
    raw_user_text: str,
) -> None:
    """Invoke a tool, then persist the agent's reply (tool result) to the
    active Case.

    Wraps ``_invoke_tool_via_emitter`` so the Case chat-history append
    happens after the tool returns. The persisted ``content`` is a
    user-readable summary of the tool result (the stringified result for
    primitive returns, or a marker for complex returns).
    """
    try:
        await _invoke_tool_via_emitter(
            websocket, state, tool_name, params
        )
    finally:
        if state.active_case_id:
            await _persist_chat_turn(
                state,
                role="agent",
                content=f"[invoked {tool_name}]",
                pipeline_id=state.current_turn_pipeline_id,
            )


# --------------------------------------------------------------------------- #
# Secrets envelope handlers (job-0124, FR-AS-4 + §F.3)
# --------------------------------------------------------------------------- #


async def _emit_secrets_list(
    websocket: ServerConnection,
    state: SessionState,
    *,
    case_id: str | None = None,
) -> None:
    """Emit a fresh ``secrets-list`` envelope for the caller.

    Multi-tenant isolation: scopes the listing on
    ``state.authenticated_user_id``. Falls back to the session_id when
    auth-handshake hasn't completed (the in-flight handshake fallback
    elsewhere in the dispatcher ensures this is rare).

    Best-effort on Persistence unbound — emits an empty list rather than
    raising so the client UI can render the "no secrets yet" empty state.
    """
    p = get_persistence()
    user_id = state.authenticated_user_id or state.session_id
    if p is None:
        logger.warning(
            "secrets-list session=%s: Persistence unbound; emitting empty",
            state.session_id,
        )
        empty = SecretsListEnvelopePayload(secrets=[])
        await websocket.send(
            _new_envelope("secrets-list", state.session_id, empty)
        )
        return
    try:
        payload = await handle_secrets_list(
            user_id=user_id, case_id=case_id, persistence=p
        )
    except SecretError as exc:
        await _send_error(
            websocket,
            state.session_id,
            "INTERNAL_ERROR",
            f"secrets-list failed: {exc}",
        )
        return
    except Exception as exc:  # noqa: BLE001
        logger.exception("secrets-list failed session=%s", state.session_id)
        await _send_error(
            websocket,
            state.session_id,
            "INTERNAL_ERROR",
            f"secrets-list failed: {exc}",
        )
        return
    await websocket.send(
        _new_envelope("secrets-list", state.session_id, payload)
    )
    logger.info(
        "secrets-list emitted session=%s case=%s count=%d",
        state.session_id,
        case_id,
        len(payload.secrets),
    )


async def _handle_secret_add(
    websocket: ServerConnection,
    state: SessionState,
    envelope: SecretAddEnvelopePayload,
) -> None:
    """Process a ``secret-add`` envelope and emit a refreshed ``secrets-list``.

    Per Decision F the raw ``key_value`` field on the inbound envelope is
    consumed by the handler (written to GCP Secret Manager) and **never**
    echoed back. The handler returns a vault-ref-only ``SecretRecord``;
    we drop it on the floor and re-emit a full ``secrets-list`` so the
    client renders the full collection (including the new entry).

    Per FR-AS-8 this is NOT a confirmation trigger (the user explicitly
    typed the key into the form — the action itself IS the user's
    confirmation). The handler proceeds without a ``confirmation-request``
    pause, matching the Case-lifecycle command pattern.
    """
    p = get_persistence()
    user_id = state.authenticated_user_id or state.session_id
    if p is None:
        await _send_error(
            websocket,
            state.session_id,
            "INTERNAL_ERROR",
            "secret-add requires Persistence; the agent service was started "
            "without GRACE2_MONGO_MCP_STDIO=1.",
        )
        return
    try:
        await handle_secret_add(
            envelope, user_id=user_id, persistence=p,
        )
    except SecretError as exc:
        await _send_error(
            websocket,
            state.session_id,
            "INTERNAL_ERROR",
            f"secret-add failed: {exc}",
        )
        return
    except Exception as exc:  # noqa: BLE001
        logger.exception("secret-add failed session=%s", state.session_id)
        await _send_error(
            websocket,
            state.session_id,
            "INTERNAL_ERROR",
            f"secret-add failed: {exc}",
        )
        return
    # Re-emit the full secrets-list so the client refreshes its panel.
    await _emit_secrets_list(
        websocket, state, case_id=envelope.case_id
    )


async def _handle_secret_revoke(
    websocket: ServerConnection,
    state: SessionState,
    envelope: SecretRevokeEnvelopePayload,
) -> None:
    """Process a ``secret-revoke`` envelope (soft-revoke + refresh list).

    The GCP Secret Manager entry is intentionally NOT deleted — preserves
    audit trail. Re-emits a refreshed ``secrets-list`` so the client UI
    drops the revoked entry from its active list.
    """
    p = get_persistence()
    user_id = state.authenticated_user_id or state.session_id
    if p is None:
        await _send_error(
            websocket,
            state.session_id,
            "INTERNAL_ERROR",
            "secret-revoke requires Persistence; the agent service was "
            "started without GRACE2_MONGO_MCP_STDIO=1.",
        )
        return
    try:
        await handle_secret_revoke(
            envelope.secret_id, user_id=user_id, persistence=p,
        )
    except SecretError as exc:
        await _send_error(
            websocket,
            state.session_id,
            "INTERNAL_ERROR",
            f"secret-revoke failed: {exc}",
        )
        return
    except Exception as exc:  # noqa: BLE001
        logger.exception("secret-revoke failed session=%s", state.session_id)
        await _send_error(
            websocket,
            state.session_id,
            "INTERNAL_ERROR",
            f"secret-revoke failed: {exc}",
        )
        return
    await _emit_secrets_list(websocket, state)


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
                    # job-0122 (Appendix H.5 / H.3): the auth-token envelope
                    # is the connect-handshake. If we receive it, run the
                    # full handshake. If we receive anything else and the
                    # handshake has not completed, trip the anonymous
                    # fallback inline so the SessionState.authenticated_user_id
                    # is bound before any user-scoped action runs.
                    if msg_type == "auth-token":
                        await _handle_auth_token(websocket, state, payload_dict)
                        continue
                    # Implicit anonymous fallback when any other envelope
                    # arrives before the handshake — keeps the legacy
                    # no-auth-token clients working.
                    if not state.auth_handshake_complete:
                        await _ensure_auth_handshake(websocket, state)

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
                        # job-0121: reset per-turn layer accumulator before
                        # the dispatch so the CaseChatMessage write captures
                        # only this turn's emissions.
                        state.current_turn_layer_ids = []
                        state.current_turn_pipeline_id = None
                        # job-0121: persist user message to the active Case
                        # (FR-MP-6). Best-effort — no active Case OR no
                        # Persistence = no-op; the M1 stateless path keeps
                        # working. Per FR-AS-8 this is a session-record
                        # write and is NOT a confirmation trigger.
                        await _persist_chat_turn(
                            state, role="user", content=um.text
                        )
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
                                _dispatch_tool_and_persist(
                                    websocket, state, tool_name, params, um.text
                                )
                            )
                        else:
                            task = asyncio.create_task(
                                _dispatch_gemini_and_persist(
                                    websocket,
                                    state,
                                    settings,
                                    um.text,
                                    um.research_mode,
                                )
                            )
                        state.inflight_task = task

                    elif msg_type == "case-command":
                        # job-0121 (FR-MP-6): Case lifecycle dispatch. The
                        # envelope is validated through the pydantic model
                        # so an unknown command raises ValidationError and
                        # surfaces TOOL_PARAMS_INVALID via the outer block
                        # (closed enum — see CaseCommand Literal).
                        cmd = CaseCommandEnvelopePayload.model_validate(
                            payload_dict
                        )
                        await _handle_case_command(websocket, state, cmd)

                    elif msg_type == "secret-add":
                        # job-0124 (FR-AS-4 + §F.3): per-Case secret add.
                        # Key value is consumed by the handler (written to
                        # GCP Secret Manager) and never echoed back. The
                        # reply is a refreshed ``secrets-list`` envelope.
                        sa = SecretAddEnvelopePayload.model_validate(
                            payload_dict
                        )
                        await _handle_secret_add(websocket, state, sa)

                    elif msg_type == "secret-revoke":
                        # job-0124: soft-revoke a per-Case secret.
                        sr = SecretRevokeEnvelopePayload.model_validate(
                            payload_dict
                        )
                        await _handle_secret_revoke(websocket, state, sr)

                    elif msg_type == "secrets-list-request":
                        # job-0124: explicit list-refresh request. The
                        # envelope payload is loosely-shaped (an empty
                        # object for global list; optional ``case_id`` to
                        # scope) — kept untyped on the schema side for
                        # forward-compat. We read case_id directly here.
                        req_case_id = None
                        if isinstance(payload_dict, dict):
                            cid = payload_dict.get("case_id")
                            if isinstance(cid, str) and cid:
                                req_case_id = cid
                        await _emit_secrets_list(
                            websocket, state, case_id=req_case_id
                        )

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

                    elif msg_type == "tool-payload-confirmation":
                        # job-0127: route the confirmation to the paused
                        # dispatch coroutine. Validate the envelope here so
                        # malformed payloads don't poison the future.
                        try:
                            conf = (
                                PayloadConfirmationEnvelopePayload.model_validate(
                                    payload_dict
                                )
                            )
                        except ValidationError as ve:
                            await _send_error(
                                websocket,
                                state.session_id,
                                "TOOL_PARAMS_INVALID",
                                f"tool-payload-confirmation invalid: {ve.errors()[0]['msg']}",
                            )
                            continue
                        fut = state.pending_payload_warnings.get(conf.warning_id)
                        if fut is None or fut.done():
                            logger.warning(
                                "tool-payload-confirmation for unknown/closed "
                                "warning_id=%s session=%s",
                                conf.warning_id,
                                state.session_id,
                            )
                            continue
                        fut.set_result(conf)
                        logger.info(
                            "tool-payload-confirmation accepted session=%s "
                            "warning_id=%s decision=%s",
                            state.session_id,
                            conf.warning_id,
                            conf.decision,
                        )

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
    "_maybe_gate_on_payload_warning",
    "_parse_invoke_directive",
    "get_persistence",
    "set_persistence",
    "init_persistence_from_env",
    # job-0121: Case lifecycle handlers + chat persistence.
    "_emit_case_list",
    "_emit_case_open",
    "_handle_case_command",
    "_persist_chat_turn",
    "_dispatch_tool_and_persist",
    "_dispatch_gemini_and_persist",
    # job-0124: secrets envelope handlers.
    "_emit_secrets_list",
    "_handle_secret_add",
    "_handle_secret_revoke",
]
