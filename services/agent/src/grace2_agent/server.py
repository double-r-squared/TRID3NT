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
from grace2_contracts.sandbox_contracts import CodeExecRequestPayload
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
    MAX_TURN_ITERATIONS,
    SYSTEM_PROMPT,
    TextDeltaEvent,
    UsageMetadataEvent,
    build_client,
    build_contents_from_history,
    build_function_call_content,
    build_function_response_content,
    build_tool_declarations,
    load_settings,
    stream_events,  # noqa: F401 — retained for tests / direct text-only callers
    stream_events_with_contents,
    stream_reply,  # noqa: F401 — retained for any callers that use it directly
    summarize_tool_result,
)
from .gemini_cache import get_or_create_cache
from .auth_handshake import (
    AuthResult,
    authenticate_token,
    build_auth_ack,
    get_auth_token_timeout_s,
)
from .case_lifecycle import CaseLifecycleError, ensure_case_qgs
from .mode2_classifier import (
    Mode2CandidateEnvelope,
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
from .telemetry import compute_args_hash, emit_tool_call_event
from .tool_arg_normalizer import normalize_args
from .tools import TOOL_REGISTRY
from .tools.chart_tools import is_chart_emission_result
from .tools.code_exec_tool import (
    CODE_EXEC_RESULT_KEY,
    is_code_exec_result,
)
from .categories import (
    AllowedToolSet,
    OutOfAllowedSetError,
    validate_function_call,
)
from .circuit_breaker import CircuitBreakerError, ToolCircuitBreaker

# job-0122: auth-token envelope (Appendix H.5 connect handshake).
from grace2_contracts.auth import AuthTokenEnvelope

logger = logging.getLogger("grace2_agent.server")

# Confirmation triggers (FR-AS-8). Empty for M1: solver runs and non-session
# Mongo writes will populate this when those code paths land. Session-record
# writes (Appendix D.6) are NOT a trigger — that carveout is documented in
# the report, not represented as data here.
CONFIRMATION_TRIGGERS: set[str] = set()

# job-0233: the ``code_exec_request`` confirm gate validity window (seconds).
# Running arbitrary Python is a deliberate user decision; the gate gets the same
# 300s read-decision TTL as the payload-warning gate. On expiry the gate fails
# closed (CONFIRMATION_TIMEOUT) and the sandbox does not run.
CODE_EXEC_CONFIRM_TIMEOUT_SECONDS: int = int(
    os.environ.get("GRACE2_CODE_EXEC_CONFIRM_TIMEOUT", "300")
)


# ---------------------------------------------------------------------------
# Routing-layer typed exceptions (B-rev job, FR-AS-11 surface).
#
# These live here rather than in a shared exceptions module because they are
# raised exclusively inside ``_invoke_tool_via_emitter`` — the server-side
# routing layer. They follow the same FR-AS-11 contract as the tool-level
# typed exceptions (``WDPAError``, ``HRSLError``, etc.): ``error_code`` is a
# SCREAMING_SNAKE_CASE string and ``retryable`` is False for both (the LLM
# cannot retry its way out of a missing tool registration; it must revise its
# function-call decision).
#
# ``summarize_tool_result`` in ``adapter.py`` harvests ``error_code`` +
# ``retryable`` from any exception that carries them (job-0177 logic), so
# these propagate as a full structured error envelope to Gemini — the same
# shape as any ``fetch_*`` / ``compute_*`` typed exception.
# ---------------------------------------------------------------------------


class ToolNotFoundError(RuntimeError):
    """Raised when ``_invoke_tool_via_emitter`` receives a tool name that is
    not registered in ``TOOL_REGISTRY``.

    ``retryable=False``: Gemini cannot retry its way to a registration it
    invented — it must revise its call (use a different tool, narrate that
    it cannot help, or ask for clarification).

    The ``valid_tools`` attribute carries the first 20 registered names so
    the Gemini function-response payload gives the LLM a correction hint
    without blowing the response character budget.
    """

    error_code: str = "TOOL_NOT_FOUND"
    retryable: bool = False

    def __init__(self, tool_name: str, valid_tools: list[str]) -> None:
        # Limit to first 20 names to stay within _FUNCTION_RESPONSE_CHAR_BUDGET.
        hint = valid_tools[:20]
        super().__init__(
            f"tool {tool_name!r} not in TOOL_REGISTRY; "
            f"valid tools (first 20): {hint}"
        )
        self.tool_name = tool_name
        self.valid_tools = hint


class PayloadWarningCancelledError(RuntimeError):
    """Raised when the payload-warning gate skips dispatch because the user
    chose ``cancel`` or the gate timed out.

    ``retryable=False``: the user explicitly declined; Gemini should narrate
    the cancellation honestly and not re-issue the same call without narrower
    scope.
    """

    error_code: str = "PAYLOAD_WARNING_CANCELLED"
    retryable: bool = False

    def __init__(self, tool_name: str) -> None:
        super().__init__(
            f"tool {tool_name!r} dispatch cancelled via payload-warning gate "
            "(user chose 'cancel' or gate timed out)"
        )
        self.tool_name = tool_name


class CodeExecConfirmationCancelledError(RuntimeError):
    """Raised when the ``code_exec_request`` confirm gate denies the run because
    the user chose ``cancel`` or the gate timed out (job-0233).

    Running arbitrary Python is a consequential action; the gate fails closed.
    ``retryable=False``: the user explicitly declined to run THIS code — Gemini
    should narrate the decline honestly and not re-issue the identical snippet
    without the user changing course.
    """

    error_code: str = "CODE_EXEC_CANCELLED"
    retryable: bool = False

    def __init__(self, code_exec_id: str) -> None:
        super().__init__(
            f"code_exec_request {code_exec_id!r} cancelled at the confirm gate "
            "(user chose 'cancel' or gate timed out); the sandbox did not run"
        )
        self.code_exec_id = code_exec_id


class SolverConfirmationCancelledError(RuntimeError):
    """Raised when a solver confirm gate denies the dispatch (job-0241).

    A solver run is a consequence (FR-AS-8 / Invariant 9): the user must
    approve the derived forcing parameters before the model executes. Cancel,
    timeout, and disconnect all fail closed. ``retryable=False`` so Gemini
    narrates the decline honestly instead of re-dispatching the same run.
    """

    error_code: str = "SOLVER_CONFIRMATION_CANCELLED"
    retryable: bool = False

    def __init__(self, tool_name: str) -> None:
        super().__init__(
            f"{tool_name} declined at the parameter-confirmation gate "
            "(user chose 'cancel' or the gate timed out); the solver did not run"
        )
        self.tool_name = tool_name


# Tools whose dispatch is a consequence (a solver run, FR-AS-8 / Invariant 9)
# and MUST pass a parameter-confirmation gate on the LLM path (job-0241 — the
# Stage 3 live gate caught run_model_groundwater_contamination_scenario
# dispatching MODFLOW with zero user confirmation). The gate runs the
# composer's PURE extraction to build the confirm card, blocks on the same
# pending_payload_warnings future seam as payload-warning/code-exec, and
# injects confirmed=True only after the user approves. Extensible: the flood
# composers join once they grow confirm-envelope builders (OQ-FIXWAVE-FLOOD-GATE).
SOLVER_CONFIRM_TOOLS: set[str] = {
    "run_model_groundwater_contamination_scenario",
}


# --------------------------------------------------------------------------- #
# Session-scoped confirmation registry (job-0243)
# --------------------------------------------------------------------------- #
#
# The Stage 3 re-verify (job-0242) proved the per-connection seam structurally
# broken on the live path: ``pending_payload_warnings`` lived on the
# per-CONNECTION ``SessionState``, but the web client opens MULTIPLE WebSocket
# connections per browser session (React StrictMode double-mount + reconnect —
# four "connection open" events observed in one session). A gate registered on
# connection A could never be resolved by the Proceed click arriving on
# connection B: the lookup hit a different, empty dict and the click was
# dropped ("unknown/closed warning_id"). EVERY confirmation gate — payload
# warning, code-exec, solver — shared the hole.
#
# Fix: ONE module-level registry keyed on the (globally unique, unguessable
# ULID) warning_id, tagged with the owning session_id. Any connection's
# inbound ``tool-payload-confirmation`` handler can resolve a pending gate as
# long as the session matches — reconnects mid-gate now work instead of
# soft-locking the gate until timeout.

_PENDING_CONFIRMATIONS: dict[str, tuple[str, asyncio.Future]] = {}


def _register_pending_confirmation(
    session_id: str, warning_id: str, fut: "asyncio.Future"
) -> None:
    _PENDING_CONFIRMATIONS[warning_id] = (session_id, fut)


def _pop_pending_confirmation(warning_id: str) -> None:
    _PENDING_CONFIRMATIONS.pop(warning_id, None)


def _resolve_pending_confirmation(
    session_id: str, conf: "PayloadConfirmationEnvelopePayload"
) -> bool:
    """Complete the pending gate future for ``conf.warning_id``.

    Returns True when a live future was resolved. False when the warning_id is
    unknown/already-resolved, or when the confirming session is not the owner
    (cross-session confirmation is refused loudly — the warning_id is an
    unguessable ULID, but defense-in-depth costs one string compare).
    """
    entry = _PENDING_CONFIRMATIONS.get(conf.warning_id)
    if entry is None:
        return False
    owner_session, fut = entry
    if owner_session != session_id:
        logger.warning(
            "tool-payload-confirmation REFUSED: session=%s is not the owner "
            "(owner=%s) for warning_id=%s",
            session_id,
            owner_session,
            conf.warning_id,
        )
        return False
    if fut.done():
        _PENDING_CONFIRMATIONS.pop(conf.warning_id, None)
        return False
    fut.set_result(conf)
    _PENDING_CONFIRMATIONS.pop(conf.warning_id, None)
    return True


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
        from .persistence import MCPSurfaceTranslator

        # job-0203 (M4): MCPClient.start defaults MDB_MCP_READ_ONLY=true (a
        # job-0015 hello-world safety). In read-only mode the server does not
        # even EXPOSE insert/update tools — every Case/session/user write
        # would fail. Persistence is the write path; FR-AS-8 confirmation
        # policy is enforced at OUR layer (CONFIRMATION_TRIGGERS), not by
        # crippling the MCP server. Explicit env still wins (setdefault).
        os.environ.setdefault("MDB_MCP_READ_ONLY", "false")

        srv = fetch_srv_from_secret_manager()
        client = await MCPClient.start(srv)
        logger.info("MCP client started; binding Persistence singleton")
        # job-0203 (M4): the live server's document surface is find/
        # insert-many/update-many, with results EJSON-wrapped in untrusted-
        # data tags. The translator adapts our logical surface (find-one/
        # insert-one/update-one/find) at this single boundary — without it
        # every CRUD call fails on first contact with production.
        p = Persistence(MCPSurfaceTranslator(client))
        set_persistence(p)
        return p
    # job-0161: this method does NOT clear a pre-bound singleton. The agent
    # startup path (``main._maybe_bind_dev_persistence``) may have already
    # bound a file-backed ``Persistence`` for LOCAL DEV; we preserve it.
    if get_persistence() is not None:
        logger.info(
            "MCP not provisioned (set GRACE2_MONGO_MCP_STDIO=1 to enable); "
            "pre-bound Persistence singleton retained"
        )
        return get_persistence()
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
    # job-0127 (Wave 2): per-session audit log of payload-warning events.
    # Each entry is a dict carrying ``warning_id``, ``tool_name``,
    # ``estimated_mb``, ``threshold_mb``, ``decision`` (set on confirmation),
    # and the ULID timestamps. Surfaces in tests + post-mortem; persisted
    # to the active Case as part of the chat turn record (best-effort).
    payload_warning_audit_log: list[dict] = field(default_factory=list)
    # job-B5 (Wave 4.10 Stage 2): per-session post-hoc allowed-set tracker.
    #
    # Under Wave 4.10 CachedContent Option A, the full tool catalog is cached
    # in the Gemini ``CachedContent.tools[]`` slot at session start and the
    # ``allowed_function_names`` filter is enforced in OUR code, not in
    # Gemini's request (Vertex 400s when ``tool_config`` is passed alongside
    # ``cached_content``). Every Gemini-emitted ``function_call`` is validated
    # against this set via ``categories.validate_function_call`` before
    # dispatch. The set is **monotonically growing** within a session — it
    # starts at the 8-tool hot set and widens as the LLM opens categories
    # (``list_tools_in_category``) or successfully dispatches tools.
    allowed_tool_set: AllowedToolSet = field(default_factory=AllowedToolSet)
    # job-B6 (Wave 4.10 Stage 2): per-session Gemini CachedContent reference.
    #
    # Lazy-initialised on the first ``user-message``: ``get_or_create_cache``
    # caches the full tool catalog + system instruction in Vertex once per
    # session, and every subsequent ``generate_content_stream`` call sets
    # ``GenerateContentConfig.cached_content=<this>`` (skipping ``tools[]``
    # and ``tool_config``). ``None`` when cache creation failed (transient
    # Vertex error, catalog below cache minimum, kill-switch set) — the
    # adapter falls back to the non-cached path automatically.
    #
    # Refreshed transparently by ``get_or_create_cache`` when within 60s of
    # expiry; the stored value here is the *latest* name observed so the
    # stream call always sees the freshest cache.
    gemini_cache_name: str | None = None
    # job-B8 (Wave 4.10 Stage 3): per-session circuit breaker.
    #
    # Tracks consecutive failures per tool; trips after GRACE2_CIRCUIT_THRESHOLD
    # (default 3) consecutive failures, enforcing a GRACE2_CIRCUIT_COOLDOWN_S
    # (default 60s) cooldown.  ``_stream_gemini_reply`` checks ``is_tripped``
    # before every ``_invoke_tool_via_emitter`` dispatch and records success/
    # failure after each attempt.  A tripped breaker raises ``CircuitBreakerError``
    # which ``summarize_tool_result`` surfaces as the Wave 4.9 structured envelope
    # so Gemini reads the signal and narrates the outage honestly.
    circuit_breaker: ToolCircuitBreaker = field(default_factory=ToolCircuitBreaker)


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


async def _send_loop_exhausted(
    websocket: ServerConnection,
    session_id: str,
) -> None:
    """Emit the distinct ``loop_exhausted`` envelope (job-B9, Wave 4.10 Stage 3).

    Fires when the multi-turn loop hits ``MAX_TURN_ITERATIONS`` without a
    natural termination (no tool-call-free turn).  Sends a raw-JSON envelope
    typed ``"loop_exhausted"`` — distinct from the generic ``"error"`` type —
    so the web UI can render "Agent ran out of steps" rather than a generic
    failure indicator.

    Wire shape:
        {
          "type": "loop_exhausted",
          "session_id": str,
          "payload": {
            "status": "loop_exhausted",
            "error_code": "MAX_ITERATIONS_REACHED",
            "message": "Agent reached max iteration limit (N) before completing the request.",
            "retryable": False
          }
        }

    The ``payload.error_code`` key follows the Wave 4.9 SCREAMING_SNAKE_CASE
    convention but lives in the ``loop_exhausted`` typed envelope, not the
    ``error`` envelope, so clients can distinguish "tool chain too long" from
    "Gemini API failed" (LLM_UNAVAILABLE). ``retryable=False`` because the
    agent already consumed all its turns; the user should rephrase or narrow
    scope.

    Best-effort: a wire failure is logged but not re-raised so the terminal
    agent-message-chunk can still fire.
    """
    import json as _json

    try:
        payload = {
            "status": "loop_exhausted",
            "error_code": "MAX_ITERATIONS_REACHED",
            "message": (
                f"Agent reached max iteration limit ({MAX_TURN_ITERATIONS}) "
                "before completing the request. "
                "Try rephrasing your request with a narrower scope."
            ),
            "retryable": False,
        }
        await websocket.send(
            _json.dumps(
                {
                    "type": "loop_exhausted",
                    "session_id": session_id,
                    "payload": payload,
                }
            )
        )
        logger.info(
            "loop_exhausted envelope sent session=%s max_iter=%d",
            session_id,
            MAX_TURN_ITERATIONS,
        )
    except Exception:  # noqa: BLE001 — observability; never break the reply path
        logger.exception(
            "loop_exhausted envelope send failed session=%s", session_id
        )


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


async def _emit_cache_status(
    websocket: ServerConnection,
    state: SessionState,
    usage: UsageMetadataEvent,
) -> None:
    """Emit a ``cache-status`` envelope so the UI can render live cache hit rate.

    Job-B6 (Wave 4.10): forwarded once per Gemini stream after the
    ``UsageMetadataEvent`` lands. Payload shape:

        {
            "cache_hit":     bool,
            "cached_tokens": int,
            "total_tokens":  int,
            "prompt_tokens": int | null,
            "candidates_tokens": int | null,
            "cache_name":    str | null   (the cached_content name in use this turn),
        }

    The envelope is intentionally raw-JSON (no contract model) — it is
    observability surface, not a wire-API contract. Mirrors the existing
    pattern for ``mode2-candidate`` (server.py line ~1685). A wire-side
    failure is logged but never raised: cache-status reporting must not
    break the agent loop.
    """
    import json as _json

    try:
        payload = {
            "cache_hit": bool(usage.cache_hit),
            "cached_tokens": int(usage.cached_content_token_count or 0),
            "total_tokens": int(usage.total_token_count or 0),
            "prompt_tokens": usage.prompt_token_count,
            "candidates_tokens": usage.candidates_token_count,
            "cache_name": state.gemini_cache_name,
        }
        await websocket.send(
            _json.dumps(
                {
                    "type": "cache-status",
                    "session_id": state.session_id,
                    "payload": payload,
                }
            )
        )
    except Exception:  # noqa: BLE001 — observability, never bubble up
        logger.exception(
            "cache-status emission failed session=%s", state.session_id
        )


async def _stream_gemini_reply(
    websocket: ServerConnection,
    state: SessionState,
    settings: GeminiSettings,
    user_text: str,
    research_mode: str,
) -> None:
    """Stream one user-message reply with multi-turn tool dispatch (job-0169).

    The previous (job-0154) shape dispatched the first function_call but never
    fed the result back to Gemini, so every multi-tool prompt
    ("Show me protected areas in Fort Myers" → geocode → fetch_wdpa) stopped
    after the first call.  The fix is the canonical Gemini agent loop:

        contents = history + user_text
        for _ in range(MAX_TURN_ITERATIONS):
            stream Gemini:
                text deltas → forward as agent-message-chunk
                function_calls → collect (this turn)
            if no function_calls this turn:
                break  # final narrative turn
            for each call:
                result = await _invoke_tool_via_emitter(...)
                summary = summarize_tool_result(name, result, error)
                append model Content (function_call) + function Content (response)
            # then loop: Gemini now sees the call + result and decides next
            # tool call OR narrates the answer.

    Cancellation: ``asyncio.CancelledError`` aborts the whole loop and emits a
    cancelled ``pipeline-state`` for the outer ``llm_generation`` step.
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
    # cancellable handle. The loop driver keeps this single outer step; each
    # dispatched tool gets its own step through the emitter.
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

    # job-B6 (Wave 4.10): lazy-create the per-session Gemini CachedContent
    # entry on the first user-message. Subsequent turns reuse the cached
    # ``name``. A creation failure (None return) drops us back to the
    # non-cached path automatically — the multi-turn loop is otherwise
    # unchanged. See ``gemini_cache.get_or_create_cache``.
    if state.gemini_cache_name is None:
        try:
            state.gemini_cache_name = await get_or_create_cache(
                client, state.session_id
            )
        except Exception:  # noqa: BLE001 — cache is best-effort
            logger.warning(
                "gemini-cache: get_or_create_cache raised session=%s; "
                "falling back to non-cached path",
                state.session_id,
                exc_info=True,
            )
            state.gemini_cache_name = None
    else:
        # Refresh on every turn so an expiry mid-session triggers a recreate.
        try:
            refreshed = await get_or_create_cache(client, state.session_id)
            if refreshed:
                state.gemini_cache_name = refreshed
        except Exception:  # noqa: BLE001
            pass

    # Seed the multi-turn contents list with chat history + this user_text.
    contents = build_contents_from_history(user_text, state.chat_history)

    # Wave 4.11 M6: refresh the dynamic hot set once per user-message dispatch
    # so the allowed set is primed with the user's most-dispatched tools before
    # any Gemini function_call arrives.  No-op when ``GRACE2_DYNAMIC_HOT_SET``
    # is unset (delegates synchronously to the static path).  Failure is silent
    # — the static fallback is always available inside ``as_frozenset_async``.
    try:
        await state.allowed_tool_set.as_frozenset_async()
    except Exception:  # noqa: BLE001 — dynamic hot-set is best-effort
        pass

    # Per-turn usage metadata harvested from the stream (job-B6).
    last_usage: UsageMetadataEvent | None = None

    iterations = 0
    try:
        while iterations < MAX_TURN_ITERATIONS:
            iterations += 1
            # Per-turn collectors: text emitted, function-calls Gemini requested.
            turn_text_parts: list[str] = []
            turn_function_calls: list[FunctionCallEvent] = []
            last_usage = None

            async for event in stream_events_with_contents(
                client,
                settings.model,
                contents,
                tool_declarations=tool_decls,
                system_prompt=SYSTEM_PROMPT,
                cached_content_name=state.gemini_cache_name,
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
                    turn_text_parts.append(event.delta)

                elif isinstance(event, FunctionCallEvent):
                    logger.info(
                        "gemini function-call session=%s iter=%d tool=%s call_id=%s args=%r",
                        state.session_id,
                        iterations,
                        event.name,
                        event.call_id,
                        event.args,
                    )
                    turn_function_calls.append(event)

                elif isinstance(event, UsageMetadataEvent):
                    # job-B6: Gemini surfaces aggregate usage on the terminal
                    # chunk. Cache the event so the post-turn block can:
                    #  (a) pipe ``cached_content_token_count`` into the
                    #      telemetry record for each dispatched tool, and
                    #  (b) emit a single ``cache-status`` envelope so the
                    #      web UI can render the live cache hit rate.
                    last_usage = event
                    logger.info(
                        "gemini usage session=%s iter=%d cached=%s total=%s "
                        "prompt=%s candidates=%s hit=%s",
                        state.session_id,
                        iterations,
                        event.cached_content_token_count,
                        event.total_token_count,
                        event.prompt_token_count,
                        event.candidates_token_count,
                        event.cache_hit,
                    )

            # Emit a cache-status envelope so the UI can render the cache
            # hit-rate live. Best-effort — a serialization failure logs but
            # does not break the turn (the envelope is observability, not
            # part of the agent loop's correctness contract).
            if last_usage is not None:
                await _emit_cache_status(websocket, state, last_usage)

            # Turn ended.  If Gemini emitted no function_calls this turn, it
            # is finished — either narrated the answer or had nothing more to
            # do.  Break out of the loop.
            if not turn_function_calls:
                logger.info(
                    "gemini loop terminal session=%s iter=%d text_chunks=%d",
                    state.session_id,
                    iterations,
                    len(turn_text_parts),
                )
                break

            # Otherwise: dispatch each call, then append the call + summarized
            # response back into contents so the next Gemini turn sees them.
            for call in turn_function_calls:
                # Dispatch through the registry + emitter (Invariant 2 — the
                # LLM's tool choice IS the classification).  Routing failures
                # (TOOL_NOT_FOUND, PAYLOAD_WARNING_CANCELLED) now raise typed
                # exceptions (B-rev) so the except-block below routes them
                # through summarize_tool_result(error=...) — a structured
                # {status: "error", error_code: str, retryable: bool} envelope
                # that Gemini can distinguish from "tool ran and returned
                # nothing" (FR-AS-11).
                dispatch_error: BaseException | None = None
                result: Any = None
                _tool_start = asyncio.get_running_loop().time()
                try:
                    # job-B8 (Wave 4.10 Stage 3): per-session circuit breaker.
                    # Short-circuit before allowed-set validation and dispatch
                    # if the tool has failed repeatedly this session. Raises
                    # ``CircuitBreakerError`` which the except-block routes
                    # through ``summarize_tool_result(error=...)`` so Gemini
                    # reads the structured cooldown signal (not retryable).
                    if state.circuit_breaker.is_tripped(call.name):
                        remaining = state.circuit_breaker.cooldown_remaining_s(call.name)
                        raise CircuitBreakerError(call.name, remaining)
                    # job-B5 (Wave 4.10): post-hoc allowed-set validation. Per
                    # the CachedContent Option A architecture, Gemini sees the
                    # full catalog but our code enforces the per-turn allowed
                    # set. A function_call outside the allowed set raises
                    # ``OutOfAllowedSetError``, which the except-block below
                    # routes through ``summarize_tool_result(error=...)`` as a
                    # Wave 4.9 structured envelope so Gemini can retry
                    # (typically by first calling ``list_tools_in_category``).
                    validate_function_call(call.name, state.allowed_tool_set)
                    result = await _invoke_tool_via_emitter(
                        websocket, state, call.name, call.args
                    )
                    # Wave 4.11 Follow-up A: emit ``impact-envelope`` WS envelope
                    # whenever ``compute_impact_envelope`` returns a result that
                    # carries a valid ImpactEnvelope (key signal: ``raw_envelope``
                    # dict with ``n_structures_total`` inside).  Fires IN ADDITION
                    # to the standard ``function_response`` — the web client gets
                    # both: function_response for Gemini-loop replay,
                    # impact-envelope for ImpactPanel state.
                    if (
                        call.name == "compute_impact_envelope"
                        and isinstance(result, dict)
                        and isinstance(result.get("raw_envelope"), dict)
                        and "n_structures_total" in result["raw_envelope"]
                    ):
                        await _maybe_emit_impact_envelope(websocket, state, result["raw_envelope"])
                    # job-0230 (sprint-13 Stage 2): emit a ``chart-emission`` WS
                    # envelope whenever a chart-generation tool returns a
                    # ChartEmissionPayload-shaped dict (key signal:
                    # ``envelope_type == "chart-emission"`` + a dict
                    # ``vega_lite_spec``). Fires IN ADDITION to the standard
                    # ``function_response`` — the web client gets both: the full
                    # Vega-Lite spec on the chart-emission envelope (for
                    # vega-embed rendering + the stacked gallery), and a COMPACT
                    # data summary on the function_response (the spec is stripped
                    # by ``summarize_tool_result`` so Gemini narrates from the
                    # numbers, not the inline rows). Also persists a
                    # SessionChartRecord so the chart replays on Case rehydration.
                    if is_chart_emission_result(result):
                        await _maybe_emit_chart(websocket, state, result)
                    # job-0233 (sprint-13 Stage 2): emit a ``code-exec-result`` WS
                    # envelope whenever ``code_exec_request`` returns a result
                    # carrying the full code-exec-result payload (key signal:
                    # ``_code_exec_result`` with ``envelope_type ==
                    # "code-exec-result"``). Fires IN ADDITION to the standard
                    # function_response — the web client gets the full result
                    # card via the envelope, and Gemini gets the COMPACT summary
                    # (the full payload is stripped by ``summarize_tool_result``).
                    if is_code_exec_result(result):
                        await _maybe_emit_code_exec_result(websocket, state, result)
                    # job-B8: record success so the consecutive-failure counter
                    # resets — a recovered tool should not stay penalised.
                    state.circuit_breaker.record_success(call.name)
                    # On a successful dispatch, mark the tool sticky so the
                    # LLM can re-issue the same tool on a later turn with
                    # refined args without re-opening its category.
                    state.allowed_tool_set.record_dispatch(call.name)
                    # If the call was ``list_tools_in_category``, open the
                    # requested category (sticky-after-list) — every member
                    # tool of that category is now reachable for the rest of
                    # the session.
                    if (
                        call.name == "list_tools_in_category"
                        and isinstance(result, dict)
                    ):
                        cat_id = result.get("category_id")
                        if isinstance(cat_id, str) and cat_id:
                            state.allowed_tool_set.open_category(cat_id)
                except asyncio.CancelledError:
                    # Propagate cancel through the loop — handled below.
                    raise
                except Exception as exc:  # noqa: BLE001 — surface to Gemini
                    logger.exception(
                        "tool dispatch raised session=%s tool=%s err=%s",
                        state.session_id,
                        call.name,
                        exc,
                    )
                    # job-B8: record failure for ANY exception (not just
                    # upstream errors) — repeated dispatch failures for the
                    # same tool indicate a runaway loop we want to break.
                    # CircuitBreakerError is excluded: it means the breaker
                    # already fired and we must not increment again.
                    if not isinstance(exc, CircuitBreakerError):
                        state.circuit_breaker.record_failure(call.name)
                    dispatch_error = exc
                _tool_latency_ms = (asyncio.get_running_loop().time() - _tool_start) * 1000.0

                summary = summarize_tool_result(
                    call.name, result, error=dispatch_error
                )
                logger.info(
                    "function-response queued session=%s iter=%d tool=%s summary_keys=%s",
                    state.session_id,
                    iterations,
                    call.name,
                    sorted(summary.keys()),
                )

                # B-tel: fire-and-forget telemetry for this LLM-initiated
                # function_call. Non-blocking — ``emit_tool_call_event`` wraps
                # the write in ``asyncio.ensure_future`` so no await is needed
                # here. A write failure is logged at WARNING by the module and
                # NEVER raises (telemetry must not break the dispatch loop).
                _tel_error_code: str | None = None
                if dispatch_error is not None:
                    _tel_error_code = str(
                        getattr(dispatch_error, "error_code", None)
                        or type(dispatch_error).__name__.upper()
                    )
                # job-B6 (Wave 4.10): the adapter now surfaces
                # ``UsageMetadataEvent`` at the end of each Gemini stream;
                # ``last_usage`` carries the most recent observation. Pipe
                # ``cached_content_token_count`` through so the telemetry
                # record empirically reflects the Vertex 90% discount.
                _tel_cached_tokens = (
                    last_usage.cached_content_token_count
                    if last_usage is not None
                    else None
                )
                await emit_tool_call_event(
                    session_id=state.session_id,
                    ts=now_utc().isoformat(),
                    tool_name=call.name,
                    source="llm",
                    args_hash=compute_args_hash(call.args),
                    success=dispatch_error is None,
                    latency_ms=_tool_latency_ms,
                    error_code=_tel_error_code,
                    cached_content_token_count=_tel_cached_tokens,
                )
                # job-B10: pass the thought_signature harvested off the
                # function_call Part through to the replayed model turn.
                # Gemini 3 requires the same opaque byte-blob on the replayed
                # function_call Part or generate_content_stream errors with
                # ``thought-signature mismatch``. Gemini 2.5 surfaces None
                # here (no signatures in 2.5) — the helper treats None as a
                # no-op, so this is forward-compat with no behavior change
                # on the current default model.
                contents.append(
                    build_function_call_content(
                        call.name,
                        call.args,
                        call.call_id,
                        thought_signature=call.thought_signature,
                    )
                )
                contents.append(
                    build_function_response_content(call.name, summary, call.call_id)
                )

            # Loop: re-stream with the appended call + response so Gemini can
            # decide its next move (another tool call OR a narrative wrap-up).
        else:
            # Loop fell through the cap.  This is a fail-stop for runaway
            # Gemini loops, not a normal exit.  job-B9: emit a distinct
            # ``loop_exhausted`` envelope (error_code=MAX_ITERATIONS_REACHED)
            # so the web UI can render "Agent ran out of steps" rather than a
            # generic failure or silent stop.
            logger.warning(
                "gemini loop hit MAX_TURN_ITERATIONS=%d session=%s — "
                "emitting loop_exhausted envelope",
                MAX_TURN_ITERATIONS,
                state.session_id,
            )
            await _send_loop_exhausted(websocket, state.session_id)

        # Terminal frame for the message stream.
        terminal = AgentMessageChunkPayload(message_id=message_id, delta="", done=True)
        await websocket.send(_new_envelope("agent-message-chunk", state.session_id, terminal))

        # Complete the outer pipeline snapshot (LLM generation phase).
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
    await _touch_session_record(state)  # D.6 heartbeat (job-0203 / M4)
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

    Wave 4.11 M6: also propagates the resolved ``user_id`` into
    ``state.allowed_tool_set.user_id`` so ``get_dynamic_hot_set`` can
    filter telemetry per-user when ``GRACE2_DYNAMIC_HOT_SET=1``.
    """
    state.authenticated_user_id = result.user.user_id
    state.is_anonymous = result.is_anonymous
    state.firebase_uid = result.firebase_uid
    state.tier = result.tier
    state.auth_handshake_complete = True
    # Propagate user_id so dynamic hot-set queries are per-user scoped.
    state.allowed_tool_set.user_id = result.user.user_id


async def _touch_session_record(
    state: SessionState, *, case_id: str | None = None
) -> None:
    """D.6 session-record heartbeat (job-0203 / Wave 4.11 M4).

    Upserts the agent's own ``sessions`` document: ``last_active_at`` +
    ``expires_at`` advance (TTL driver per ``SESSIONS_TTL``), the active
    Case lands in ``project_ids``. Fired on auth bind, Case open/create,
    and every persisted chat turn — the session-record carveout (FR-AS-8)
    means none of these touches is a confirmable write.

    Best-effort like telemetry (M3) and chart persistence (job-0230): a
    persistence hiccup is logged at WARNING and never reaches the caller.
    """
    p = get_persistence()
    if p is None:
        return
    try:
        await p.touch_session(
            state.session_id,
            case_id=case_id if case_id is not None else state.active_case_id,
        )
    except Exception:  # noqa: BLE001 — side effect, never bubble up
        logger.warning(
            "session-touch failed session=%s", state.session_id, exc_info=True
        )


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
    await _touch_session_record(state)  # D.6 heartbeat (job-0203 / M4)
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
    await _touch_session_record(state, case_id=case_id)  # D.6 heartbeat (M4)
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

    # job-0172 Part B: seed the emitter with the persisted loaded_layers
    # so any subsequent ``session-state`` emission (e.g. from the next
    # tool call inside this Case) carries them rather than overwriting
    # with an empty list. The emitter's _loaded_layers is the truth set
    # the next ``add_loaded_layer`` dedups against; without seeding, a
    # republish of an existing layer would be treated as a fresh append.
    _ensure_emitter(websocket, state)
    if state.emitter is not None:
        state.emitter.reset_loaded_layers(session_state.loaded_layers)

    logger.info(
        "case-open session=%s case=%s chat=%d layers=%d",
        state.session_id,
        case_id,
        len(session_state.chat_history),
        len(session_state.loaded_layers),
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
        await _touch_session_record(state, case_id=new_case_id)  # D.6 (M4)
        # Emit case-open with the empty session state for the fresh Case.
        payload = CaseOpenEnvelopePayload(
            session_state=await p.get_session_state(new_case_id)
        )
        await websocket.send(
            _new_envelope("case-open", state.session_id, payload)
        )
        # job-0172 Part B: a fresh Case starts with NO loaded layers; flush
        # the emitter's per-connection accumulator so a subsequent tool call
        # in this Case doesn't accidentally inherit layers from whatever Case
        # the user just left (replace-not-reconcile applied server-side).
        _ensure_emitter(websocket, state)
        if state.emitter is not None:
            state.emitter.reset_loaded_layers([])
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
        # Per-turn D.6 heartbeat (job-0203 / M4): the chat turn is the
        # activity signal that keeps the session record's TTL fresh and
        # the active Case registered in ``project_ids``.
        await _touch_session_record(state)
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
    _register_pending_confirmation(state.session_id, warning_id, fut)

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
        _pop_pending_confirmation(warning_id)

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


async def _gate_on_code_exec(
    websocket: ServerConnection,
    state: SessionState,
    params: dict,
) -> tuple[bool, dict]:
    """Confirm gate for ``code_exec_request`` (job-0233) — MANDATORY, fail-closed.

    Running arbitrary Python is a consequential action; the user MUST approve the
    exact code before the sandbox runs. This gate emits a ``code-exec-request``
    confirm card and blocks on the SAME ``pending_payload_warnings`` future seam
    the payload-warning gate uses (the ``code_exec_id`` is the correlation key,
    carried back as the ``tool-payload-confirmation.warning_id``) — no new
    confirm plumbing.

    Returns ``(should_dispatch, effective_params)``:

    - ``(True, params + {confirmed: True, code_exec_id})`` — user approved
      (``decision="proceed"``). The tool body runs the sandbox.
    - ``(False, params)`` — user chose ``cancel`` / gate timed out. The caller
      raises :class:`CodeExecConfirmationCancelledError` so Gemini sees a typed,
      non-retryable error and narrates the decline honestly.

    ``narrow_scope`` is NOT offered for code-exec (you don't "narrow" a code
    snippet — you cancel and the agent rewrites it); a ``narrow_scope`` reply is
    treated as a cancel (fail-closed).
    """
    python_code = params.get("python_code")
    if not isinstance(python_code, str) or not python_code.strip():
        # No code to confirm — let the tool body raise its own params error.
        return True, params

    code_exec_id = new_ulid()
    request_payload = CodeExecRequestPayload(
        code_exec_id=code_exec_id,
        python_code=python_code,
        layer_refs=params.get("layer_refs") or {},
        rationale=params.get("rationale"),
    )

    # Create the future the inbound ``tool-payload-confirmation`` handler completes
    # (keyed on code_exec_id == warning_id). Same seam as the payload-warning gate.
    loop = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()
    _register_pending_confirmation(state.session_id, code_exec_id, fut)

    await websocket.send(
        _new_envelope("code-exec-request", state.session_id, request_payload)
    )
    logger.info(
        "code-exec-request emitted session=%s code_exec_id=%s code_len=%d n_layers=%d",
        state.session_id,
        code_exec_id,
        len(python_code),
        len(request_payload.layer_refs),
    )

    try:
        decision_payload: PayloadConfirmationEnvelopePayload = await asyncio.wait_for(
            fut, timeout=CODE_EXEC_CONFIRM_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        logger.warning(
            "code-exec confirm gate timeout session=%s code_exec_id=%s",
            state.session_id,
            code_exec_id,
        )
        await _send_error(
            websocket,
            state.session_id,
            "CONFIRMATION_TIMEOUT",
            f"code_exec_request {code_exec_id!r} confirm gate timed out; "
            "the sandbox did not run",
        )
        return False, params
    finally:
        _pop_pending_confirmation(code_exec_id)

    logger.info(
        "code-exec confirm decision session=%s code_exec_id=%s decision=%s",
        state.session_id,
        code_exec_id,
        decision_payload.decision,
    )

    if decision_payload.decision != "proceed":
        # cancel OR narrow_scope (the latter is meaningless for code; fail-closed).
        await _send_error(
            websocket,
            state.session_id,
            "USER_INPUT_CANCELLED",
            f"code_exec_request {code_exec_id!r} declined by user "
            f"(decision={decision_payload.decision!r}); the sandbox did not run",
        )
        return False, params

    # Approved: inject the gate-cleared flags so the tool body dispatches with the
    # SAME code_exec_id the request card carried (so request/result cards correlate).
    approved = dict(params)
    approved["confirmed"] = True
    approved["code_exec_id"] = code_exec_id
    return True, approved


async def _gate_on_solver_confirm(
    websocket: ServerConnection,
    state: SessionState,
    tool_name: str,
    params: dict,
) -> tuple[bool, dict]:
    """Parameter-confirmation gate for solver composers (job-0241) — fail-closed.

    Mirrors :func:`_gate_on_code_exec`: build the confirm card, emit it as a
    ``tool-payload-warning`` (the inline card the web client already renders),
    block on the ``pending_payload_warnings`` future seam (``warning_id`` is
    the correlation key the ``tool-payload-confirmation`` reply carries), and
    inject ``confirmed=True`` only after an explicit ``proceed``.

    The card is built from the composer's PURE extraction (no emitter, no
    solver) so the user confirms the actual derived forcing — "12,000 gal TCE
    over 6 h → 3.07 kg/s at (42.56, -114.47)" — plus the demo-aquifer caveat.
    The composer re-runs the (cache-backed) extraction after approval; the
    confirmed values are deterministic, so card and run cannot diverge.

    An extraction failure here falls through to dispatch (``True``) so the
    composer raises its own typed extraction error — the gate must not mask
    parameter problems behind a confusing confirm card.
    """
    from .workflows.model_groundwater_contamination_scenario import (
        _build_confirmation_envelope,
        extract_spill_parameters,
    )
    from grace2_contracts.modflow_contracts import MODFLOWRunArgs

    article_text = params.get("article_text")
    if not isinstance(article_text, str) or not article_text.strip():
        # source_url path or missing text: let the composer fetch/validate and
        # surface its own typed error; gating happens on the derived params at
        # the next dispatch once article_text is materialized by the composer.
        # (v0.1: the live path always supplies article_text — see job-0235.)
        return True, params

    try:
        # extract_spill_parameters is synchronous (pure extraction + cached
        # geocode); run it off the event loop so the WS heartbeat stays live.
        derived = await asyncio.to_thread(
            extract_spill_parameters, article_text, geocode=True
        )
        kwargs: dict[str, Any] = dict(
            spill_location_latlon=derived["spill_location_latlon"],
            contaminant=derived["contaminant"],
            release_rate_kg_s=derived["release_rate_kg_s"],
            duration_days=derived["duration_days"],
        )
        if params.get("aquifer_k_ms") is not None:
            kwargs["aquifer_k_ms"] = float(params["aquifer_k_ms"])
        if params.get("porosity") is not None:
            kwargs["porosity"] = float(params["porosity"])
        envelope = _build_confirmation_envelope(derived, MODFLOWRunArgs(**kwargs))
    except Exception:  # noqa: BLE001 — never mask extraction errors with a gate
        logger.warning(
            "solver-confirm gate could not build the confirm card for %s; "
            "falling through so the composer raises its typed error",
            tool_name,
            exc_info=True,
        )
        return True, params

    warning_id = envelope.warning_id
    loop = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()
    _register_pending_confirmation(state.session_id, warning_id, fut)

    await websocket.send(
        _new_envelope("tool-payload-warning", state.session_id, envelope)
    )
    logger.info(
        "solver-confirm gate emitted session=%s tool=%s warning_id=%s "
        "contaminant=%r location=%r",
        state.session_id,
        tool_name,
        warning_id,
        envelope.tool_args.get("contaminant"),
        envelope.tool_args.get("location_name"),
    )

    try:
        decision_payload: PayloadConfirmationEnvelopePayload = await asyncio.wait_for(
            fut, timeout=CODE_EXEC_CONFIRM_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        logger.warning(
            "solver-confirm gate timeout session=%s tool=%s warning_id=%s",
            state.session_id,
            tool_name,
            warning_id,
        )
        await _send_error(
            websocket,
            state.session_id,
            "CONFIRMATION_TIMEOUT",
            f"{tool_name} parameter-confirmation gate timed out; "
            "the solver did not run",
        )
        return False, params
    finally:
        _pop_pending_confirmation(warning_id)

    logger.info(
        "solver-confirm decision session=%s tool=%s warning_id=%s decision=%s",
        state.session_id,
        tool_name,
        warning_id,
        decision_payload.decision,
    )

    if decision_payload.decision != "proceed":
        # cancel OR narrow_scope (meaningless for a solver run; fail-closed).
        await _send_error(
            websocket,
            state.session_id,
            "USER_INPUT_CANCELLED",
            f"{tool_name} declined by user "
            f"(decision={decision_payload.decision!r}); the solver did not run",
        )
        return False, params

    approved = dict(params)
    approved["confirmed"] = True
    return True, approved


def _ensure_emitter(websocket: ServerConnection, state: SessionState) -> None:
    """Bind a ``PipelineEmitter`` to this session if one isn't already.

    The emitter's sink is the WebSocket ``send`` — every transition method
    writes one envelope on the wire (Appendix A.7 replace-not-reconcile)."""


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
        # B-rev: raise ToolNotFoundError so the existing exception handler at
        # the call site (server.py:500-507) routes through
        # summarize_tool_result(error=...) which emits the full Wave 4.9
        # structured envelope — error_code + retryable + message — so Gemini
        # can distinguish "tool ran and returned nothing" from "tool name was
        # never registered". The _send_error side-channel is NOT needed here;
        # the function_response envelope IS the signal Gemini reads between
        # turns. (FR-AS-3, FR-AS-11, job B-rev.)
        raise ToolNotFoundError(tool_name, list(TOOL_REGISTRY))
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
        # B-rev: raise PayloadWarningCancelledError so Gemini sees a structured
        # envelope ({status: "error", error_code: "PAYLOAD_WARNING_CANCELLED",
        # retryable: False}) instead of {"status": "no_result"} which it cannot
        # interpret. retryable=False because the user explicitly cancelled; the
        # LLM should narrate the cancellation and not re-issue the call unless
        # the user provides a narrower scope. (FR-AS-11, job B-rev.)
        raise PayloadWarningCancelledError(tool_name)

    # job-0233: code_exec_request confirm gate. Running arbitrary Python is a
    # consequential action — the user MUST approve the exact code first. The gate
    # emits a ``code-exec-request`` card, blocks on the SAME
    # ``pending_payload_warnings`` future seam (code_exec_id == warning_id), and
    # on approval injects ``confirmed=True`` + the minted ``code_exec_id`` into
    # params so the tool body dispatches the sandbox. A direct programmatic call
    # that already carries ``confirmed=True`` (a trusted composer / test) is NOT
    # re-gated — but a LLM-issued call never carries it, so the gate is mandatory
    # on the LLM path. Fail-closed: cancel / timeout raises a typed, non-retryable
    # error so Gemini narrates the decline and does not re-run the same snippet.
    if tool_name == "code_exec_request" and not params.get("confirmed"):
        should_run, params = await _gate_on_code_exec(websocket, state, params)
        if not should_run:
            raise CodeExecConfirmationCancelledError(
                params.get("code_exec_id", "unknown")
            )

    # Confirmation-before-consequence for solver composers (job-0241,
    # Invariant 9 / FR-AS-8). The LLM-supplied ``confirmed`` is STRIPPED first
    # — the gate is server-owned; only an explicit user "proceed" injects it.
    if tool_name in SOLVER_CONFIRM_TOOLS:
        params.pop("confirmed", None)
        should_run, params = await _gate_on_solver_confirm(
            websocket, state, tool_name, params
        )
        if not should_run:
            raise SolverConfirmationCancelledError(tool_name)

    # job-0164: centralized kwarg sweep. Gemini routinely invents kwargs that
    # don't exist on our tools (``run_name``, ``scenario_id``,
    # ``return_period_years`` when the tool accepts ``return_period_yr``, etc.).
    # ``normalize_args`` inspects ``entry.fn``'s signature and rewrites
    # bidirectional aliases (``_yr`` ↔ ``_years``, ``_hr`` ↔ ``_hours``,
    # ``durationHours`` ↔ ``duration_hours``), parses string-form forcing specs
    # (``forcing="atlas14_100yr"`` → ``return_period_years=100``), absorbs
    # silent-drop convenience kwargs, and logs+drops the rest — never raises.
    # See ``tool_arg_normalizer.py``.
    params = normalize_args(tool_name, params, entry.fn)

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

    # job-0172 Part B: per-Case layer persistence.
    #
    # The PipelineEmitter holds ``_loaded_layers`` per-connection in memory;
    # without persistence, a Case re-open (fresh WS, fresh emitter) loses
    # everything the prior session published. Sync the current
    # ``ProjectLayerSummary[]`` accumulator onto the Case document so the
    # next ``case-open`` hydration replays them deterministically. Dedup is
    # by ``uri`` (matches the emitter's own dedup policy) and the lighter
    # ``layer_summary: list[str]`` field is kept in lockstep for the
    # left-rail cheap summary.
    #
    # Best-effort: a Persistence failure is logged but never raised — chat
    # persistence is a side-effect, not the happy path. Only fires inside an
    # active Case context; the demo / single-tenant path stays untouched.
    if state.active_case_id and state.emitter is not None:
        await _persist_case_loaded_layers(state)

    # job-0101: Mode 2 .gov/.edu classifier — when web_fetch returns a dict
    # that looks like a structured-data candidate, emit a `mode2-candidate`
    # envelope and append an audit-log line. Deterministic side-effect; the
    # web modal (Wave 2/3) renders the offer. See mode2_classifier.py.
    if tool_name == "web_fetch" and isinstance(result, dict):
        await _maybe_emit_mode2_candidate(websocket, state, result)
    return result


async def _persist_case_loaded_layers(state: SessionState) -> None:
    """Sync the emitter's ``_loaded_layers`` onto the active ``CaseSummary``.

    job-0172 Part B: writes the current ``ProjectLayerSummary[]`` accumulator
    into ``Case.loaded_layer_summaries`` (full dicts for rehydration) and
    keeps ``Case.layer_summary`` (the lightweight ``layer_id[]`` projection)
    in lockstep. Idempotent and dedup-by-uri because the emitter already
    dedups upstream; the persisted shape mirrors the in-memory shape.

    Best-effort: a Persistence failure is logged but never raised. The
    Case lookup gates the write — if the Case was archived / deleted
    mid-turn we silently skip (no surprise resurrection of a tombstoned
    Case via this side-channel).
    """
    p = get_persistence()
    if p is None or state.emitter is None or not state.active_case_id:
        return
    try:
        case = await p.get_case(state.active_case_id)
    except Exception:  # noqa: BLE001
        logger.exception(
            "case-layer-persist: get_case failed case=%s",
            state.active_case_id,
        )
        return
    if case is None:
        logger.debug(
            "case-layer-persist: case=%s missing; skipping",
            state.active_case_id,
        )
        return

    loaded = state.emitter.loaded_layers  # defensive copy from the emitter
    summaries_dicts: list[dict] = [layer.model_dump(mode="json") for layer in loaded]
    layer_ids: list[str] = [layer.layer_id for layer in loaded]

    # If nothing has changed, skip the round-trip.
    if (
        case.loaded_layer_summaries == summaries_dicts
        and case.layer_summary == layer_ids
    ):
        return

    updated = case.model_copy(
        update={
            "loaded_layer_summaries": summaries_dicts,
            "layer_summary": layer_ids,
            "updated_at": now_utc(),
        }
    )
    try:
        await p.upsert_case(updated)
        logger.debug(
            "case-layer-persist case=%s layers=%d",
            state.active_case_id,
            len(layer_ids),
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "case-layer-persist: upsert failed case=%s",
            state.active_case_id,
        )


async def _maybe_emit_impact_envelope(
    websocket: ServerConnection,
    state: SessionState,
    raw_envelope: dict,
) -> None:
    """Emit an ``impact-envelope`` WS envelope for the ImpactPanel (Wave 4.11 Follow-up A).

    Called when ``compute_impact_envelope`` returns a result that contains a
    valid ``raw_envelope`` dict (ImpactEnvelope shape, key signal:
    ``n_structures_total`` present at the top level).

    The envelope is emitted IN ADDITION to the standard ``function_response``
    so the web client gets both:

    - ``function_response`` → Gemini-loop replay (Gemini reads the summary).
    - ``impact-envelope``   → ImpactPanel state update (P4 UI surface).

    Wire shape::

        {
          "type": "impact-envelope",
          "session_id": str,
          "payload": { ...full ImpactEnvelope dict... }
        }

    Best-effort: a serialization / wire failure is logged but never raised —
    the ``function_response`` path (and thus the agent loop) must not be
    interrupted by a side-channel emission failure.
    """
    import json as _json

    try:
        await websocket.send(
            _json.dumps(
                {
                    "type": "impact-envelope",
                    "session_id": state.session_id,
                    "payload": raw_envelope,
                }
            )
        )
        logger.info(
            "impact-envelope emitted session=%s n_structures_total=%s",
            state.session_id,
            raw_envelope.get("n_structures_total"),
        )
    except Exception:  # noqa: BLE001 — side effect, never bubble up
        logger.exception(
            "impact-envelope emission failed session=%s", state.session_id
        )


async def _maybe_emit_code_exec_result(
    websocket: ServerConnection,
    state: SessionState,
    code_exec_result: dict,
) -> None:
    """Emit a ``code-exec-result`` WS envelope (job-0233).

    Called when ``code_exec_request`` returns a result carrying the full
    code-exec-result payload under ``_code_exec_result``
    (``is_code_exec_result(result)`` is True). Fires IN ADDITION to the standard
    ``function_response``:

    - ``code-exec-result`` → the FULL result payload (status + stdout/stderr
      tails + the structured result descriptor + truncated flag + duration) for
      the web client to render the result card. The function_response Gemini
      reads is the COMPACT summary (the full payload is stripped by
      ``adapter.summarize_tool_result`` via the ``_code_exec_result`` key) so
      narration sources the structured ``result``, not the raw logs.

    Wire shape mirrors ``chart-emission`` (the precedent)::

        {
          "type": "code-exec-result",
          "session_id": str,
          "payload": { ...full CodeExecResultPayload dict... }
        }

    Best-effort: a serialization / wire failure is logged but never raised — the
    function_response path (and thus the agent loop) must not be interrupted by a
    side-channel emission failure. Code-exec results are ephemeral (not persisted
    to the session ``charts`` array) — a re-opened Case replays the chat + charts,
    not transient computations.
    """
    import json as _json

    payload = code_exec_result.get(CODE_EXEC_RESULT_KEY)
    if not isinstance(payload, dict):
        return
    try:
        await websocket.send(
            _json.dumps(
                {
                    "type": "code-exec-result",
                    "session_id": state.session_id,
                    "payload": payload,
                }
            )
        )
        logger.info(
            "code-exec-result emitted session=%s code_exec_id=%s status=%s truncated=%s",
            state.session_id,
            payload.get("code_exec_id"),
            payload.get("status"),
            payload.get("truncated"),
        )
    except Exception:  # noqa: BLE001 — side effect, never bubble up
        logger.exception(
            "code-exec-result emission failed session=%s", state.session_id
        )


async def _maybe_emit_chart(
    websocket: ServerConnection,
    state: SessionState,
    chart_result: dict,
) -> None:
    """Emit a ``chart-emission`` WS envelope + persist the chart (job-0230).

    Called when a chart-generation tool (``generate_histogram`` /
    ``generate_choropleth_legend`` / ``generate_time_series`` /
    ``generate_damage_distribution``) returns a ChartEmissionPayload-shaped dict
    (``is_chart_emission_result(result)`` is True). Fires IN ADDITION to the
    standard ``function_response``:

    - ``chart-emission`` → the FULL Vega-Lite spec for the web client to render
      via vega-embed (inline stacked preview + gallery). The function_response
      Gemini reads is a COMPACT summary with the spec stripped
      (``adapter.summarize_tool_result``) so narration sources the numbers, not
      the inline rows.
    - ``SessionChartRecord`` persisted to the ``sessions`` collection so the
      chart replays on Case rehydration.

    The ``created_turn_id`` is stamped here (from the per-turn pipeline id) when
    the tool did not set one, so the client groups charts emitted in the same
    turn into one UI stack.

    Wire shape::

        {
          "type": "chart-emission",
          "session_id": str,
          "payload": { ...full ChartEmissionPayload dict... }
        }

    Best-effort: a serialization / wire / persistence failure is logged but
    never raised — the ``function_response`` path (and thus the agent loop) must
    not be interrupted by a side-channel emission failure.
    """
    import json as _json

    payload = dict(chart_result)
    # Stamp the UI stack-grouping key from the current turn if the tool left it
    # unset, so charts from the same turn render as one stack (chart_contracts
    # ``created_turn_id`` semantics).
    if not payload.get("created_turn_id"):
        turn_id = (
            state.current_turn_pipeline_id
            or state.current_pipeline_id
            or state.session_id
        )
        payload["created_turn_id"] = turn_id

    try:
        await websocket.send(
            _json.dumps(
                {
                    "type": "chart-emission",
                    "session_id": state.session_id,
                    "payload": payload,
                }
            )
        )
        logger.info(
            "chart-emission emitted session=%s chart_id=%s title=%r",
            state.session_id,
            payload.get("chart_id"),
            payload.get("title"),
        )
    except Exception:  # noqa: BLE001 — side effect, never bubble up
        logger.exception(
            "chart-emission emission failed session=%s", state.session_id
        )

    # Persist the chart so it replays on Case rehydration (best-effort).
    await _persist_chart_record(state, payload)


async def _persist_chart_record(state: SessionState, payload: dict) -> None:
    """Append a ``SessionChartRecord`` to the session document (job-0230).

    Same pattern as the telemetry writer (M3): resolve the ``Persistence``
    singleton and ``$push`` the record onto the session document's append-only
    ``charts`` array via the underlying MCP ``update-one`` call (the typed
    Persistence methods own Case/User/Secret shapes; charts go directly on the
    MCP client like telemetry, keeping the Persistence public API narrow).

    Keyed by the active Case id when one is selected (so charts replay on Case
    rehydration via the same document the chat history lives on), else by the
    session id (the M1 stateless path). ``upsert=True`` so the first chart on a
    fresh session document creates it.

    Never raises — a persistence failure is logged at WARNING. Replay (the read
    side that rehydrates the ``charts`` array) is web/agent-rehydration scope
    (job-0231 / session-resume); this is the write half of the contract.
    """
    persistence = get_persistence()
    if persistence is None:
        # M1 in-memory / CI-without-Atlas path: charts live only in-flight.
        logger.debug(
            "chart persistence skipped (no Persistence bound) session=%s",
            state.session_id,
        )
        return

    try:
        from grace2_contracts.chart_contracts import (
            ChartEmissionPayload,
            SessionChartRecord,
        )
        from .persistence import DEFAULT_DATABASE, SESSIONS_COLLECTION

        doc_id = state.active_case_id or state.session_id
        record = SessionChartRecord(
            session_id=doc_id,
            payload=ChartEmissionPayload.model_validate(payload),
            emitted_at=now_utc(),
        )
        body = record.model_dump(mode="json")
        await persistence._mcp.call_tool(  # noqa: SLF001 — telemetry-writer pattern
            "update-one",
            {
                "database": DEFAULT_DATABASE,
                "collection": SESSIONS_COLLECTION,
                "filter": {"_id": doc_id},
                "update": {"$push": {"charts": body}},
                "upsert": True,
            },
        )
        logger.info(
            "chart persisted session=%s doc_id=%s chart_id=%s",
            state.session_id,
            doc_id,
            payload.get("chart_id"),
        )
    except Exception:  # noqa: BLE001 — persistence must not break the loop
        logger.warning(
            "chart persistence failed session=%s chart_id=%s",
            state.session_id,
            payload.get("chart_id"),
            exc_info=True,
        )


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
        # job-0203 (M4): Mode-2 candidate audit routes through the MCP
        # ``audit_log`` collection (D.15) — the bespoke JSONL file writer
        # was deleted (remove-don't-shim). When Persistence is unbound
        # (explicit CI path) the event is logged-and-dropped, same policy
        # as telemetry (M3) and chart persistence (job-0230).
        p_audit = get_persistence()
        if p_audit is not None:
            try:
                await p_audit.append_audit(
                    "mode2-candidate",
                    {
                        "session_id": state.session_id,
                        "candidate": envelope.to_wire_dict()["candidate"],
                    },
                )
            except Exception:  # noqa: BLE001 — audit is best-effort
                logger.warning(
                    "mode2 audit write failed session=%s",
                    state.session_id,
                    exc_info=True,
                )
        else:
            logger.debug(
                "mode2 audit skipped (no Persistence bound) session=%s",
                state.session_id,
            )
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

    B-rev FIX: ``_invoke_tool_via_emitter`` now raises ``ToolNotFoundError``
    when the directive references an unregistered tool name. This caller is
    the ``/invoke`` directive path — a manual operator-debug surface dispatched
    via ``asyncio.create_task`` (no awaiter exists to catch propagated
    exceptions). To prevent the typed exception from surfacing as an
    unhandled-task "exception was never retrieved" warning, we catch it here
    and route it through ``_send_error`` so the operator's chat surface
    receives a structured ``error`` envelope (``TOOL_NOT_FOUND`` /
    ``retryable=False``) — the same shape Gemini's multi-turn loop produces
    via ``summarize_tool_result``. Other typed routing exceptions
    (``PayloadWarningCancelledError``) are also caught so the manual surface
    sees the cancellation reason explicitly instead of disappearing.
    """
    try:
        try:
            await _invoke_tool_via_emitter(
                websocket, state, tool_name, params
            )
        except asyncio.CancelledError:
            raise
        except ToolNotFoundError as exc:
            logger.info(
                "/invoke directive references unregistered tool "
                "session=%s tool=%s",
                state.session_id,
                tool_name,
            )
            await _send_error(
                websocket,
                state.session_id,
                exc.error_code,
                str(exc),
                retryable=exc.retryable,
            )
        except PayloadWarningCancelledError as exc:
            logger.info(
                "/invoke directive cancelled via payload-warning gate "
                "session=%s tool=%s",
                state.session_id,
                tool_name,
            )
            await _send_error(
                websocket,
                state.session_id,
                exc.error_code,
                str(exc),
                retryable=exc.retryable,
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
                        # job-0243: resolve via the SESSION-scoped module
                        # registry — the gate may have been registered on a
                        # DIFFERENT WebSocket connection of this same session
                        # (StrictMode double-mount / reconnect).
                        if not _resolve_pending_confirmation(
                            state.session_id, conf
                        ):
                            logger.warning(
                                "tool-payload-confirmation for unknown/closed "
                                "warning_id=%s session=%s",
                                conf.warning_id,
                                state.session_id,
                            )
                            continue
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

    Wave 4.10 job-C1: also mounts the read-only HTTP catalog endpoint at
    ``GRACE2_AGENT_HTTP_PORT`` (default 8766) so the web Tools page can
    fetch the full tool catalog without going through the WS path. The
    HTTP server is a sibling of the WS server (same asyncio loop, same
    process). A failure to start the HTTP listener logs but does not abort
    WS startup — the catalog page is a discovery convenience, not a
    requirement for the chat path.
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

    # Wave 4.10 C1: best-effort mount of the catalog HTTP listener.
    http_server = None
    try:
        from .tool_catalog_http import serve_catalog_http

        http_server = await serve_catalog_http(host=host)
    except Exception:  # noqa: BLE001 — discovery surface, never blocks WS
        logger.exception(
            "tool-catalog HTTP listener failed to start; "
            "continuing without /api/tool-catalog"
        )

    try:
        async with serve(handler, host, port):
            await asyncio.Future()  # serve forever
    finally:
        if http_server is not None:
            http_server.close()
            try:
                await http_server.wait_closed()
            except Exception:  # noqa: BLE001
                pass


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
    # job-B8+B9 (Wave 4.10 Stage 3): circuit breaker + loop_exhausted.
    "_send_loop_exhausted",
    "CircuitBreakerError",
    "ToolCircuitBreaker",
]
