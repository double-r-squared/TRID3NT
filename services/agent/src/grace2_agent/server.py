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
import weakref
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import ValidationError
from websockets.asyncio.server import ServerConnection, serve

from grace2_contracts import new_ulid, now_utc
from grace2_contracts.execution import LayerURI
from grace2_contracts.case import (
    CaseChatMessage,
    CaseCommandEnvelopePayload,
    CaseListEnvelopePayload,
    CaseOpenEnvelopePayload,
    CaseSessionState,
    CaseSummary,
    ToolCardRecord,
)
from grace2_contracts.payload_warning import (
    HARD_CAP_MB_DEFAULT,
    WARNING_THRESHOLD_MB_DEFAULT,
    PayloadConfirmationEnvelopePayload,
    PayloadWarningEnvelopePayload,
)
from grace2_contracts.sandbox_contracts import CodeExecRequestPayload
from grace2_contracts.secrets import (
    CredentialProvidedEnvelopePayload,
    CredentialRequestEnvelopePayload,
    SecretAddEnvelopePayload,
    SecretRevokeEnvelopePayload,
    SecretsListEnvelopePayload,
)
from grace2_contracts.region_choice import (
    RegionCandidate,
    RegionChoiceProvidedEnvelopePayload,
    RegionChoiceRequestEnvelopePayload,
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
    rehydrate_history_from_case,
    REHYDRATE_HISTORY_CAP,
    stream_events,  # noqa: F401 — retained for tests / direct text-only callers
    stream_events_with_contents,
    stream_reply,  # noqa: F401 — retained for any callers that use it directly
    summarize_tool_result,
)
from .gemini_cache import get_or_create_cache
from .auth import (
    AUTH_CLOSE_CODE,
    AUTH_FAILED_ERROR_CODE,
    MIGRATION_ANON_UID,
    auth_required,
)
from .auth_handshake import (
    AuthResult,
    authenticate_token,
    build_auth_ack,
    get_auth_token_timeout_s,
)
from .case_lifecycle import CaseLifecycleError, ensure_case_qgs
from .credential_registry import (
    CredentialProvider,
    is_credential_error,
    provider_for_tool,
)
from .layer_uri_emit import emit_layer_uri
from .mode2_classifier import (
    Mode2CandidateEnvelope,
    classify_for_mode2,
)
from .persistence import Persistence
from .pipeline_emitter import (
    PipelineEmitter,
    bind_turn_case,
    current_turn_case,
)
from .secrets_handler import (
    SecretError,
    handle_secret_add,
    handle_secret_revoke,
    handle_secrets_list,
)
from .telemetry import compute_args_hash, emit_tool_call_event
from .tool_arg_normalizer import normalize_args
from .uri_registry import (
    activate_registry,
    deactivate_registry,
    get_uri_registry,
)
from .scenario_reuse import (
    get_scenario_index,
    scenario_signature,
    scenario_type_for_tool,
)
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
    # job-0256: flood solvers gated too — a live sandbox-only session was
    # observed running an unrequested SFINCS solve (~10-20 min). The card is
    # built from the call args (location/return period/duration).
    "run_model_flood_scenario",
    "run_model_flood_habitat_scenario",
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


# --------------------------------------------------------------------------- #
# Session-scoped pending-CREDENTIAL registry (job VAULT-READ)
# --------------------------------------------------------------------------- #
#
# Mirrors ``_PENDING_CONFIRMATIONS`` (the payload-warning / code-exec / solver
# gate registry) but for the credential-request flow: when a keyed tool
# dispatch hits a missing/invalid credential the dispatch coroutine pauses on a
# future keyed by the credential ``request_id``, having emitted a
# ``credential-request`` envelope. The inbound ``credential-provided`` handler
# (which may arrive on a DIFFERENT WebSocket connection of the same session —
# StrictMode double-mount / reconnect, exactly as for confirmations) resolves
# the future, and the paused dispatch retries the tool (which now reads the
# user's freshly-saved vault key). Tagged with the owning session_id so a
# cross-session credential-provided is refused.
_PENDING_CREDENTIALS: dict[str, tuple[str, asyncio.Future]] = {}


def _register_pending_credential(
    session_id: str, request_id: str, fut: "asyncio.Future"
) -> None:
    _PENDING_CREDENTIALS[request_id] = (session_id, fut)


def _pop_pending_credential(request_id: str) -> None:
    _PENDING_CREDENTIALS.pop(request_id, None)


def _resolve_pending_credential(
    session_id: str, provided: "CredentialProvidedEnvelopePayload"
) -> bool:
    """Complete the pending credential future for ``provided.request_id``.

    Returns True when a live future was resolved. False when the request_id is
    unknown/already-resolved, or when the answering session is not the owner
    (refused loudly — the request_id is an unguessable ULID, but the string
    compare is cheap defense-in-depth, matching ``_resolve_pending_confirmation``).
    """
    entry = _PENDING_CREDENTIALS.get(provided.request_id)
    if entry is None:
        return False
    owner_session, fut = entry
    if owner_session != session_id:
        logger.warning(
            "credential-provided REFUSED: session=%s is not the owner "
            "(owner=%s) for request_id=%s",
            session_id,
            owner_session,
            provided.request_id,
        )
        return False
    if fut.done():
        _PENDING_CREDENTIALS.pop(provided.request_id, None)
        return False
    fut.set_result(provided)
    _PENDING_CREDENTIALS.pop(provided.request_id, None)
    return True


# --------------------------------------------------------------------------- #
# Session-scoped pending-REGION-CHOICE registry (region-disambiguation picker)
# --------------------------------------------------------------------------- #
#
# Mirrors ``_PENDING_CREDENTIALS`` exactly, but for the region-choice flow: when
# a ``geocode_location`` result comes back as a state-bbox-fallback snap, the
# dispatch coroutine emits a ``region-choice-request`` envelope (whole-state
# default + candidate counties) and pauses on a future keyed by the choice
# ``request_id``. The inbound ``region-choice-provided`` handler (which may
# arrive on a DIFFERENT WebSocket connection of the same session — StrictMode
# double-mount / reconnect) resolves the future, and the paused dispatch either
# narrows the geocode bbox to the picked region or keeps the whole-state bbox.
# Fail-open: on timeout / no client the whole-state bbox (already the geocode
# result) is used unchanged so the automated path never blocks. Tagged with the
# owning session_id so a cross-session region-choice-provided is refused.
_PENDING_REGION_CHOICES: dict[str, tuple[str, asyncio.Future]] = {}


def _register_pending_region_choice(
    session_id: str, request_id: str, fut: "asyncio.Future"
) -> None:
    _PENDING_REGION_CHOICES[request_id] = (session_id, fut)


def _pop_pending_region_choice(request_id: str) -> None:
    _PENDING_REGION_CHOICES.pop(request_id, None)


def _resolve_pending_region_choice(
    session_id: str, provided: "RegionChoiceProvidedEnvelopePayload"
) -> bool:
    """Complete the pending region-choice future for ``provided.request_id``.

    Returns True when a live future was resolved. False when the request_id is
    unknown/already-resolved, or when the answering session is not the owner
    (refused loudly — mirrors ``_resolve_pending_credential``).
    """
    entry = _PENDING_REGION_CHOICES.get(provided.request_id)
    if entry is None:
        return False
    owner_session, fut = entry
    if owner_session != session_id:
        logger.warning(
            "region-choice-provided REFUSED: session=%s is not the owner "
            "(owner=%s) for request_id=%s",
            session_id,
            owner_session,
            provided.request_id,
        )
        return False
    if fut.done():
        _PENDING_REGION_CHOICES.pop(provided.request_id, None)
        return False
    fut.set_result(provided)
    _PENDING_REGION_CHOICES.pop(provided.request_id, None)
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

    job credential-pipeline-generic: also binds the SAME ``Persistence`` into
    EVERY keyed-tool secret-resolution seam (FIRMS / eBird / ERA5 / GTSM /
    IUCN — each exposes ``set_persistence_for_secrets``) so a tool dispatched
    with a per-Case ``secret_ref`` can materialize the user's vault key without
    importing the MCP client. (Movebank constructs its own MCP-less Persistence
    inline, so it needs no seam.) Binding here keeps every persistence-set path
    (production MCP, dev file-backed, test mocks) in sync without editing each
    call site.
    """
    global _PERSISTENCE
    _PERSISTENCE = p
    _bind_secret_seams(p)


# Keyed tools that expose a ``set_persistence_for_secrets(p)`` seam. The server
# binds the live Persistence into all of them so any keyed tool can resolve a
# per-Case ``secret_ref`` (vault -> env). Movebank is intentionally absent: it
# builds its own MCP-less Persistence inline for credential resolution.
_SECRET_SEAM_TOOL_MODULES: tuple[str, ...] = (
    "fetch_firms_active_fire",
    "fetch_ebird_observations",
    "fetch_era5_reanalysis",
    "fetch_gtsm_tide_surge",
    "fetch_iucn_red_list_range",
)


def _bind_secret_seams(p: "Persistence | None") -> None:
    """Bind ``p`` into every keyed tool's ``set_persistence_for_secrets`` seam.

    Best-effort per tool: a missing module / seam logs at debug and does not
    abort binding the rest (one tool's import hiccup must not starve the others
    of their vault resolver).
    """
    import importlib

    for mod_name in _SECRET_SEAM_TOOL_MODULES:
        try:
            mod = importlib.import_module(f".tools.{mod_name}", __package__)
            mod.set_persistence_for_secrets(p)
        except Exception:  # noqa: BLE001 — secret-seam binding is best-effort
            logger.debug(
                "set_persistence: could not bind secret seam for %s",
                mod_name,
                exc_info=True,
            )


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


async def _run_preauth_case_migration() -> None:
    """One-time idempotent pre-Auth case migration (job-0252, OQ-0115).

    Calls ``Persistence.migrate_preauth_cases(MIGRATION_ANON_UID)`` if a
    Persistence singleton is bound. Cases written before the Auth track had
    no ``user_id`` field and used to leak to every signed-in user via a
    ``$exists:false`` clause (now removed). This stamps them with the
    synthetic owner so each Case is visible only to its owner.

    Idempotent: the migration's filter is ``{"user_id": {"$exists": False}}``,
    so a second startup matches nothing. Best-effort: a failure is logged at
    WARNING and never aborts server startup (mirrors the Persistence-init and
    session-touch postures).
    """
    p = get_persistence()
    if p is None:
        logger.info(
            "pre-Auth case migration skipped: no Persistence singleton bound"
        )
        return
    try:
        n = await p.migrate_preauth_cases(MIGRATION_ANON_UID)
        logger.info("pre-Auth case migration complete: %s case(s) stamped", n)
    except Exception:  # noqa: BLE001 — startup must not abort on migration
        logger.warning("pre-Auth case migration failed (continuing)", exc_info=True)


# job-0259: session-scoped active-Case registry. The web client mounts TWO
# WebSocket connections per tab (Chat.tsx + App.tsx, both bound to the same
# localStorage session_id — web/src/ws.ts job-0159 hub). The server builds a
# fresh ``SessionState`` PER CONNECTION, so any Case context stored on the
# connection object splits brain: ``case-command`` arrives on one socket,
# ``user-message`` (and therefore every tool dispatch + persistence write) on
# the other. This registry keys the active Case by ``session_id`` so all
# connections of a session — including post-reconnect replacements — observe
# the same Case context. Bounded: oldest entries evicted past the cap (the
# value is one short string per browser session; eviction only means a stale
# session's next case-command re-establishes context).
_SESSION_ACTIVE_CASE: dict[str, str | None] = {}
_SESSION_ACTIVE_CASE_CAP = 4096

#: Sentinel for ``SessionState.case_context_synced_to`` — distinct from None
#: because ``None`` is a legitimate "no active Case" binding.
_CASE_SYNC_NEVER = "__case-context-never-synced__"

#: job-0269: stream key for turns dispatched with no active Case (mirrors the
#: web client's ROOT_STREAM_KEY in Chat.tsx).
_ROOT_STREAM_KEY = "__root__"

#: job-0269: per-task narration-list registry. ``_stream_gemini_reply``
#: registers its turn's narration list under the running asyncio task (in the
#: synchronous prefix, so crash/cancel still leaves the entry) and
#: ``_dispatch_gemini_and_persist`` pops it in its finally — the wrapper then
#: joins THIS turn's list even when a concurrent turn has re-pointed
#: ``state.current_turn_narration``. Weak keys: an entry whose task was never
#: popped (direct stream callers) vanishes with the task, no leak.
_TURN_NARRATION_BY_TASK: "weakref.WeakKeyDictionary[asyncio.Task, list[str]]" = (
    weakref.WeakKeyDictionary()
)

#: job-0315: per-task OPEN-segment registry. ``_stream_gemini_reply`` registers
#: the list backing the CURRENTLY OPEN narration segment (the bubble that has
#: received text but not yet been finalized). On each finalize the in-loop code
#: ``.clear()``s this same list object (never rebinds it) so the wrapper always
#: reads the live open buffer. ``_dispatch_gemini_and_persist`` pops it in its
#: finally and persists the un-finalized remainder as the tail row — exactly the
#: narration NO ``_finalize_segment`` ever wrote (crash/cancel mid-segment), so
#: no narration is lost and finalized segments are never double-persisted.
_TURN_OPEN_SEGMENT_BY_TASK: "weakref.WeakKeyDictionary[asyncio.Task, list[str]]" = (
    weakref.WeakKeyDictionary()
)

#: job-0315: per-task count of narration SEGMENTS finalized+persisted this turn.
#: ``_finalize_segment`` increments it only when it actually writes a non-empty
#: ``role="agent"`` row. The wrapper's finally reads it to decide whether the
#: legacy single marker row (narration-less completed turn / pre-fix one-row
#: contract) still needs writing (segments_done == 0) or whether the per-segment
#: rows already carried the narration (segments_done > 0 -> skip the marker).
_TURN_SEGMENTS_PERSISTED_BY_TASK: "weakref.WeakKeyDictionary[asyncio.Task, int]" = (
    weakref.WeakKeyDictionary()
)

#: job-0315 (contract fix): per-task flag set True ONLY when a row that
#: snapshotted the turn's zoom-to/layer accumulator was actually persisted —
#: i.e. the in-loop TERMINAL ``_finalize_segment`` wrote a non-empty
#: ``role="agent"`` row (``is_terminal=True`` -> ``layer_emissions=None`` ->
#: ``_persist_chat_turn`` snapshots ``current_turn_layer_ids`` +
#: ``current_turn_map_commands``). The wrapper's finally reads it to decide
#: whether a tool-terminal turn (final round ended in tool calls with NO
#: trailing narration -> no terminal finalize fired -> accumulator orphaned)
#: still needs a closing accumulator-bearing marker row so the Case-reopen
#: zoom-snap (job-0280/0281 web ``extractLastZoomTo``) + job-0259 layer
#: attribution survive. NOT set when the terminal segment was empty/whitespace
#: (``_finalize_segment`` skips the row) — that turn's accumulator is likewise
#: unwritten and the marker is needed.
_TURN_TERMINAL_ACC_PERSISTED_BY_TASK: "weakref.WeakKeyDictionary[asyncio.Task, bool]" = (
    weakref.WeakKeyDictionary()
)


def _set_session_active_case(session_id: str, case_id: str | None) -> None:
    """Bind ``case_id`` as the active Case for every connection of ``session_id``."""
    if (
        session_id not in _SESSION_ACTIVE_CASE
        and len(_SESSION_ACTIVE_CASE) >= _SESSION_ACTIVE_CASE_CAP
    ):
        # Evict oldest (insertion order) — bounded memory, see note above.
        _SESSION_ACTIVE_CASE.pop(next(iter(_SESSION_ACTIVE_CASE)))
    _SESSION_ACTIVE_CASE[session_id] = case_id


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
    # job-0269: in-flight turns keyed by STREAM (case_id, or _ROOT_STREAM_KEY
    # for the Cases root). The M1 single-slot policy cancelled ANY running
    # turn on a new user-message — live 2026-06-10 that killed a cloud SFINCS
    # solve when the user asked a terrain question from the root. Now only a
    # re-prompt in the SAME stream replaces (cancels) that stream's turn;
    # turns in other Cases keep running. Their persistence follows the
    # job-0268 turn pin and their Gemini context is the per-turn captured
    # history list (see _stream_gemini_reply), so a concurrent turn cannot
    # re-aim either. KNOWN v0.1 LIMIT (display only): the web routes live
    # streaming envelopes to the last-submitted stream, so a still-running
    # turn's late envelopes may PAINT in the newer stream until envelope
    # case-tagging lands (13.5) — the persisted replay is always correct.
    inflight_tasks: dict[str, asyncio.Task] = field(default_factory=dict)
    emitter: PipelineEmitter | None = None
    # FR-FR-3 (job-0048): per-session turn counter.  Increments on every
    # user-message dispatch (Gemini stream or /invoke directive). When
    # turn_count > MAX_TURNS_PER_SESSION the agent refuses further dispatch
    # and emits a ``session-state(status="max_turns_reached")`` envelope.
    # New WebSocket connection → new SessionState → fresh counter at 0.
    turn_count: int = 0
    # job-0259: ``active_case_id`` is now a PROPERTY backed by the module-level
    # ``_SESSION_ACTIVE_CASE`` registry (keyed by ``session_id``), NOT a
    # per-connection dataclass field. Root cause of the "Case layers not
    # rehydrating" bug: the web client mounts TWO GraceWs sockets per tab
    # (Chat.tsx carries ``user-message``; App.tsx carries ``case-command`` —
    # see web/src/ws.ts job-0159 hub comment). With a per-connection field,
    # ``case-command(select)`` set the case on App's connection while every
    # tool dispatch ran on Chat's connection with ``active_case_id=None`` —
    # so ``_persist_chat_turn`` + ``_persist_case_loaded_layers`` +
    # ``ensure_case_qgs`` all silently no-opped and a Case re-open came back
    # empty. Keying by session_id makes the Case context shared across every
    # connection of the session (and survive reconnects). See
    # ``case_context_synced_to`` + ``_sync_case_context`` for the
    # per-connection in-memory catch-up (chat_history / emitter seed).
    #
    # job-0259: per-connection marker of which Case this connection's
    # in-memory context (chat_history + emitter loaded_layers) was last
    # synced to. A string sentinel (never a valid case id) means "never
    # synced"; ``None`` is a legitimate value (no active Case).
    case_context_synced_to: str | None = _CASE_SYNC_NEVER
    # job-0121: per-turn layer + map-command emission accumulators. Reset at
    # the start of every dispatch (Gemini stream or /invoke tool). The
    # CaseChatMessage write at turn close reads from these so a Case replay
    # can re-bind layers via the same emission sequence.
    current_turn_layer_ids: list[str] = field(default_factory=list)
    current_turn_pipeline_id: str | None = None
    # job-0281: per-turn zoom-to accumulator — persisted into the closing
    # agent row's ``map_command_emissions`` so Case reopen can snap the
    # camera back (job-0280 web replays the LAST persisted zoom-to).
    current_turn_map_commands: list[dict] = field(default_factory=list)
    # job-0267: per-turn narration accumulator. ``_stream_gemini_reply``
    # resets it at stream start and appends every ``TextDeltaEvent`` delta
    # (across ALL loop iterations — they share one ``message_id`` bubble on
    # the wire). ``_dispatch_gemini_and_persist`` joins it at turn close and
    # persists the agent's narration as a ``CaseChatMessage(role="agent")``
    # so a Case reopen replays what the agent actually said — round-5 user
    # evidence showed only user turns survived because this text was never
    # accumulated (the old code persisted ``content=""`` markers).
    current_turn_narration: list[str] = field(default_factory=list)
    # job-0268: the Case this TURN is bound to. Pinned by ``_prepare_user_turn``
    # at dispatch time (after the auto-create-from-root hand-off, before the
    # first write). Every turn-scoped persistence write — chat rows, tool
    # cards, layer attribution, per-Case .qgs routing, charts — targets THIS
    # binding via ``_turn_case_id``, never the live ``active_case_id``, which
    # a mid-stream ``case-command(select)`` re-points. Pre-fix, Case A's
    # narration + tool cards persisted into Case B permanently when the user
    # switched Cases during a long-running turn (job-0267 verifier probes A+B;
    # the window is minutes-long for SFINCS-class tools).
    current_turn_case_id: str | None = None
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
    # job VAULT-READ: per-TURN set of tools that have already surfaced a
    # credential-request this turn. The credential pipeline pauses + prompts +
    # retries ONCE per tool per turn: after the single retry the tool either
    # succeeds (key now in vault) or fails through the normal typed-error
    # surface. Without this guard a still-invalid key (user pasted a bad MAP_KEY)
    # would re-trip the auth error and re-prompt forever. Reset at the start of
    # every ``_stream_gemini_reply`` turn (the prompt is a per-request decision).
    credential_prompted_tools: set[str] = field(default_factory=set)

    # ------------------------------------------------------------------ #
    # job-0259: active-Case context — session-scoped, NOT per-connection.
    # ------------------------------------------------------------------ #

    @property
    def active_case_id(self) -> str | None:
        """The active Case for this SESSION (shared across its connections).

        ``None`` for fresh sessions (no Case selected yet — the M1 stateless
        demo path remains supported). Updated by ``case-command(create|select)``
        on ANY connection of the session; cleared on ``delete`` of the active
        Case. When non-None, the tool-call wrapper
        (``_invoke_tool_via_emitter``) carries the case context into tools
        that opt in via ``case_id`` (currently ``publish_layer``); chat +
        layer persistence route every turn into the Case record.
        """
        return _SESSION_ACTIVE_CASE.get(self.session_id)

    @active_case_id.setter
    def active_case_id(self, value: str | None) -> None:
        _set_session_active_case(self.session_id, value)


def _new_envelope(message_type: str, session_id: str, payload: Any) -> str:
    """Construct + validate an Envelope and return its JSON wire form.

    job-0277: stamps ``case_id`` from the turn's ContextVar binding (set by
    the dispatch wrappers) so the web routes live envelopes to the OWNING
    Case's stream. None outside a turn — lifecycle envelopes are untagged.
    """
    env = Envelope(
        type=message_type,
        session_id=session_id,
        case_id=current_turn_case(),
        payload=payload,
    )
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

    # job-0315: one bubble per CONTIGUOUS narration run. A fresh message_id is
    # minted lazily the FIRST time text arrives in a segment (A2); finalized
    # (done=True + per-segment persist) when the next function-call round is
    # about to dispatch (A3); a brand-new segment opens for the next text after
    # that round. ``None`` => no open segment (the "no leading text before the
    # first tool call -> no empty bubble" edge falls out for free). Do NOT
    # pre-mint — the first segment's id is minted on first text exactly like
    # every later segment.
    current_message_id: str | None = None
    pipeline_id = new_ulid()
    step_id = new_ulid()
    state.current_pipeline_id = pipeline_id
    # job-0267: fresh narration accumulator for this stream. One stream ==
    # one ``message_id`` bubble on the wire == one persisted ``role="agent"``
    # CaseChatMessage at turn close (``_dispatch_gemini_and_persist``).
    # job-0269: capture BOTH per-turn lists as locals in the coroutine's
    # synchronous prefix (before any await). With per-Case turn concurrency
    # a newer turn (or a case-open/deselect) re-points the SessionState
    # fields mid-stream — this turn must keep appending to ITS OWN lists.
    # The narration list is also registered under the running task so the
    # dispatch wrapper's finally joins THIS turn's list (never the live
    # field) — even on crash/cancel, since registration precedes any await.
    state.current_turn_narration = []
    # job VAULT-READ: reset the per-turn credential-prompt guard. A new user
    # turn is a fresh request — a tool that prompted for a key last turn may
    # legitimately prompt again this turn (the key may still be missing).
    state.credential_prompted_tools = set()
    turn_narration = state.current_turn_narration
    turn_history = state.chat_history
    # job-0315: per-segment buffer for the CURRENTLY OPEN bubble only (reset by
    # _finalize_segment via .clear() at each boundary — same list object stays
    # registered). Captured in the synchronous prefix and registered under the
    # running task so a crash/cancel mid-segment lets the wrapper's finally
    # persist the un-finalized tail. Counter init at 0 so the wrapper's
    # ``segments_done`` read is well-defined even on instant death.
    _segment_buf: list[str] = []
    _reg_task = asyncio.current_task()
    if _reg_task is not None:
        _TURN_NARRATION_BY_TASK[_reg_task] = turn_narration
        _TURN_OPEN_SEGMENT_BY_TASK[_reg_task] = _segment_buf
        _TURN_SEGMENTS_PERSISTED_BY_TASK[_reg_task] = 0
        # job-0315 contract fix: False until the terminal finalize actually
        # snapshots the accumulator onto a persisted row (see registry doc).
        _TURN_TERMINAL_ACC_PERSISTED_BY_TASK[_reg_task] = False

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

    # sprint-14-aws (job-0287): under Bedrock there is no Vertex client to build —
    # build_client() requires GCP ADC, which run-local and the AWS deploy do not
    # have. stream_events_with_contents' bedrock branch ignores ``client``.
    # Provider resolved once here and reused by the cache guard below.
    from .bedrock_adapter import model_provider as _model_provider

    _provider = _model_provider()
    client = None if _provider == "bedrock" else build_client(settings)
    first_token_logged = False
    started_at = asyncio.get_running_loop().time()

    # Build tool declarations + system prompt for this request.
    tool_decls = build_tool_declarations(TOOL_REGISTRY)

    # job-B6 (Wave 4.10): lazy-create the per-session Gemini CachedContent
    # entry on the first user-message. Subsequent turns reuse the cached
    # ``name``. A creation failure (None return) drops us back to the
    # non-cached path automatically — the multi-turn loop is otherwise
    # unchanged. See ``gemini_cache.get_or_create_cache``.
    if _provider == "bedrock":
        # Bedrock uses its own cachePoint prompt caching (job-0288); the Gemini
        # CachedContent fast-path is skipped entirely under MODEL_PROVIDER=bedrock.
        state.gemini_cache_name = None
    elif state.gemini_cache_name is None:
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
    # job-0269: the entry-captured list — a mid-stream case switch rebinds
    # ``state.chat_history`` to the new Case's list, never mutates this one.
    contents = build_contents_from_history(user_text, turn_history)

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
                    # job-0315: open a NEW bubble on the first text of a segment.
                    if current_message_id is None:
                        current_message_id = new_ulid()
                    chunk = AgentMessageChunkPayload(
                        message_id=current_message_id, delta=event.delta, done=False
                    )
                    await websocket.send(
                        _new_envelope("agent-message-chunk", state.session_id, chunk)
                    )
                    turn_text_parts.append(event.delta)
                    # job-0267: accumulate across ALL iterations — the turn
                    # close persists the full narration for Case replay.
                    # job-0269: entry-captured list, never the live field.
                    turn_narration.append(event.delta)
                    # job-0315: also feed the OPEN-segment buffer so the
                    # boundary finalize (A3 / A4) persists exactly this run's
                    # text, and a crash leaves the un-finalized tail for the
                    # wrapper. Same registered list object — never rebound.
                    _segment_buf.append(event.delta)

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

            # job-0315: a function-call round is about to dispatch — close the
            # current narration bubble (if any text was emitted) BEFORE the
            # tool cards for this round land on the wire / in the chat store, so
            # the next run of text AFTER the tools opens a fresh bubble that
            # interleaves AFTER them (its own message_id -> its own client
            # arrivalSeq -> sorts between the surrounding tool stepOrder seqs).
            # Fires ONCE per round, before ALL calls dispatch, so multiple
            # function calls in one generation round close exactly one prior
            # bubble (not one per call). ``_finalize_segment`` sends the
            # done=True frame AND persists this segment's own role="agent" row.
            if current_message_id is not None:
                await _finalize_segment(
                    websocket, state, current_message_id, _segment_buf
                )
                current_message_id = None  # next text opens a fresh segment

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
                    # region-disambiguation picker: when geocode_location came
                    # back as a state-bbox-fallback snap (job-0346), offer the
                    # user a narrower sub-region (default: counties) ON TOP of
                    # the whole-state default. PAUSES the turn awaiting the
                    # region-choice-provided reply; on a "region" pick this
                    # MUTATES ``result["bbox"]`` in place so the immediate
                    # zoom-to below AND the function_response Gemini reads next
                    # turn use the narrowed extent. Fail-open: headless client /
                    # timeout / whole-state pick keeps the state bbox unchanged
                    # (the honest, already-resolved automated answer). MUST run
                    # BEFORE the zoom-to so the camera snaps to the final extent.
                    if (
                        call.name == "geocode_location"
                        and isinstance(result, dict)
                    ):
                        await _maybe_handle_region_choice(
                            websocket, state, result
                        )
                    # job-0260 (demo UX): snap the map to a geocoded location
                    # IMMEDIATELY — the user should not wait for a downstream
                    # layer publish to see the map move. Best-effort.
                    if (
                        call.name == "geocode_location"
                        and isinstance(result, dict)
                        and result.get("bbox")
                        and state.emitter is not None
                    ):
                        try:
                            await state.emitter.emit_map_command(
                                "zoom-to", {"bbox": list(result["bbox"])}
                            )
                            # job-0281: accumulate the turn's zoom-to so the
                            # closing CaseChatMessage persists it in
                            # ``map_command_emissions`` — the Case-reopen
                            # snap-to-location (job-0280 web) replays the
                            # LAST persisted zoom-to. Field existed since
                            # job-0099 but never had a writer.
                            state.current_turn_map_commands.append(
                                {
                                    "command": "zoom-to",
                                    "args": {"bbox": list(result["bbox"])},
                                }
                            )
                        except Exception:  # noqa: BLE001 — UX nicety only
                            logger.debug("geocode zoom-to emit failed", exc_info=True)
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
                    # job-B8 + 2026-06-17 fix: record failure, passing the
                    # exception so the breaker counts ONLY upstream/transient
                    # faults toward the trip threshold. Deterministic CLIENT/arg
                    # errors (*ArgError, BboxInvalidError, ValueError/TypeError
                    # arg-shape errors) are model-side faults the model can
                    # self-correct and retry — they must NOT trip a breaker that
                    # would then BLOCK the corrected-args retry (Oklahoma-tornado
                    # bug). CircuitBreakerError is excluded entirely: it means
                    # the breaker already fired and we must not increment again.
                    if not isinstance(exc, CircuitBreakerError):
                        state.circuit_breaker.record_failure(call.name, exc)
                    dispatch_error = exc
                _tool_latency_ms = (asyncio.get_running_loop().time() - _tool_start) * 1000.0

                summary = summarize_tool_result(
                    call.name, result, error=dispatch_error
                )
                # job-0263: surface the layer handles this dispatch registered
                # so Gemini passes HANDLES (layer_id) — never raw gs:// paths —
                # into downstream *_uri params. The server resolves handles to
                # the exact URIs it recorded (uri_registry.py).
                _new_handles = get_uri_registry(state.session_id).drain_announcements()
                if _new_handles and dispatch_error is None:
                    summary["layer_handles"] = _new_handles
                    # job-0270: the note must make the publish step explicit —
                    # a computed/fetched layer is invisible until publish_layer
                    # adds it to the QGIS project (live finding: Gemini ended
                    # the colored-relief turn without publishing).
                    summary["layer_handles_note"] = (
                        "A layer is NOT visible on the user's map until "
                        "publish_layer(layer_uri=<handle>, "
                        "layer_id=<descriptive-id>) has run for it — if the "
                        "user asked to see this layer, call publish_layer "
                        "with the handle before finishing. Pass these handle "
                        "strings (layer_id) for any *_uri tool parameter — "
                        "the server resolves them to exact storage URIs. Do "
                        "NOT construct or echo gs:// paths."
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
                # job-0327 R2 (MUST-FIX 3): a workflow that swallowed its own
                # exception and returned a failed/partial envelope raises NO
                # ``dispatch_error`` — but ``summarize_tool_result`` stamps the
                # function_response ``status="error"`` (honesty floor). Derive
                # the telemetry success flag and error_code from that summary so
                # a returned-failure is recorded as a FAILURE (with code) in
                # telemetry/routing, not a silent success. A genuinely-raised
                # exception (dispatch_error) still wins and keeps its own code.
                _tel_error_code: str | None = None
                _tel_success = dispatch_error is None
                if dispatch_error is not None:
                    _tel_error_code = str(
                        getattr(dispatch_error, "error_code", None)
                        or type(dispatch_error).__name__.upper()
                    )
                elif isinstance(summary, dict) and summary.get("status") == "error":
                    _tel_success = False
                    _summary_code = summary.get("error_code")
                    _tel_error_code = (
                        str(_summary_code) if _summary_code is not None else None
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
                    success=_tel_success,
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
            # job-0315: the runaway loop ALWAYS exits with the last round in a
            # tool dispatch (it never emits trailing narration), so
            # ``current_message_id is None`` and the segment finalize below
            # no-ops. The client still waits for a stream-closing done=True to
            # stop spinning, so emit a standalone terminator with a fresh id —
            # this preserves the pre-fix contract where the unconditional
            # terminal frame closed the stream on the cap-hit path too. The
            # web ``appendDelta`` renders this empty closing frame as a no-op
            # bubble next to the loop_exhausted error card.
            if current_message_id is None:
                await websocket.send(
                    _new_envelope(
                        "agent-message-chunk",
                        state.session_id,
                        AgentMessageChunkPayload(
                            message_id=new_ulid(), delta="", done=True
                        ),
                    )
                )

        # job-0315: terminal frame for the FINAL narration segment. Only fire
        # if a segment is actually open (text was emitted after the last tool
        # round). A turn whose final round ended in tool calls with NO trailing
        # narration, or a turn with zero text, has ``current_message_id is None``
        # — so no phantom empty bubble + no phantom empty agent row. This is the
        # de-facto closing row, so ``is_terminal=True`` lets it snapshot the
        # turn's layer/zoom accumulator (job-0259/0281 attribution).
        if current_message_id is not None:
            await _finalize_segment(
                websocket,
                state,
                current_message_id,
                _segment_buf,
                is_terminal=True,
            )
            current_message_id = None

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
        # job-0269: append to the entry-captured list — after a mid-stream
        # case switch this turn's text must not leak into the NEW Case's
        # LLM context (the carryover class, 74fc0d6).
        turn_history.append({"role": "user", "text": user_text})
        # job-0260: name an Untitled Case from its first prompt + refresh
        # the left rail so accumulated demo Cases are distinguishable.
        if await _maybe_autoname_case(state, user_text):
            await _emit_case_list(websocket, state)

    except asyncio.CancelledError:
        # Invariant 8 — distinct cancelled step state, not failed. job-0315: a
        # partially-open narration segment's done=True is intentionally NOT
        # sent here (a cancelled stream has no clean terminal). The job-0267
        # ``current_turn_narration`` still holds the partial text and the
        # dispatch wrapper's finally persists the un-finalized open-segment tail
        # best-effort (one row), so no narration is lost.
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


async def _reject_unauthenticated(
    websocket: ServerConnection,
    state: SessionState,
    *,
    reason: str,
) -> None:
    """Reject an unauthenticated connection under the ``AUTH_REQUIRED`` gate.

    job-0252 (sprint-13.5 Decision #6): production REQUIRES sign-in. When the
    gate is engaged and no valid Firebase ID token resolves, we must NOT fall
    through to the anonymous path. Per SRS Appendix A.5 step 2 we emit an A.6
    ``AUTH_FAILED`` error envelope and close the socket with code ``4401``.

    Best-effort: a socket that is already closing may raise on send/close; we
    swallow so the handler loop can terminate cleanly.
    """
    logger.info(
        "AUTH_REQUIRED gate: rejecting unauthenticated connection "
        "session=%s reason=%s (close %d)",
        state.session_id,
        reason,
        AUTH_CLOSE_CODE,
    )
    try:
        await _send_error(
            websocket,
            state.session_id,
            AUTH_FAILED_ERROR_CODE,
            f"authentication required: {reason}",
        )
    except Exception:  # noqa: BLE001 — socket may already be down
        pass
    try:
        await websocket.close(code=AUTH_CLOSE_CODE, reason="unauthorized")
    except Exception:  # noqa: BLE001 — close is best-effort
        pass


async def _handle_auth_token(
    websocket: ServerConnection,
    state: SessionState,
    payload_dict: dict,
) -> bool:
    """Process the client's ``auth-token`` envelope and emit ``auth-ack``.

    Per Appendix H.5 (job-0122 scope):

    1. Validate the payload through ``AuthTokenEnvelope``.
    2. Call ``authenticate_token`` → resolves to a ``User`` via Persistence
       (or provisions an anonymous fallback).
    3. Bind the resolved ``user_id`` + tier + anonymous-flag into the
       SessionState — every subsequent envelope is scoped to this user.
    4. Emit ``auth-ack`` so the client knows its session identity.

    job-0252 (sprint-13.5): under the ``AUTH_REQUIRED`` gate, an unverified
    token (or no token) resolves to an anonymous result — which we REJECT
    instead of binding (remove-don't-shim from the prod path). Returns
    ``True`` when the connection may proceed, ``False`` when the caller must
    stop processing (the connection was rejected + closed).
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
        # connection is still usable (per H.3) — UNLESS the AUTH_REQUIRED
        # gate is engaged, in which case the result is rejected below.
        tok = None

    result = await authenticate_token(tok, get_persistence())

    # job-0252 AUTH_REQUIRED gate: when sign-in is mandatory, an anonymous
    # result means verification did not produce a real Firebase identity —
    # reject (A.5 close 4401 + A.6 AUTH_FAILED). No anonymous fallback on the
    # required path. Dev (gate off) preserves the Wave 2 behavior verbatim.
    if result.is_anonymous and auth_required():
        await _reject_unauthenticated(
            websocket, state, reason="no valid Firebase ID token"
        )
        return False

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
    return True


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
) -> bool:
    """Synchronous fallback: if the handshake hasn't run, run it as anonymous.

    Called when a non-``auth-token`` envelope arrives before the handshake
    has completed (the client either didn't send auth-token, or another
    envelope raced ahead). Mirrors the 5-second timeout path from H.3 —
    instead of waiting 5 seconds we trip the anonymous fallback inline so
    the user is bound before their first real interaction.

    job-0252 (sprint-13.5): under the ``AUTH_REQUIRED`` gate, a client that
    speaks a non-``auth-token`` envelope first (i.e. never sent a valid
    Firebase ID token) is REJECTED — there is no implicit anonymous bind on
    the required path. Returns ``True`` when the connection may proceed,
    ``False`` when the caller must stop (the connection was rejected +
    closed).
    """
    if state.auth_handshake_complete:
        return True
    if auth_required():
        await _reject_unauthenticated(
            websocket,
            state,
            reason="auth-token envelope required before any other message",
        )
        return False
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
    return True


# --------------------------------------------------------------------------- #
# Case lifecycle handlers (job-0121, FR-MP-6)
# --------------------------------------------------------------------------- #


async def _emit_case_list(websocket: ServerConnection, state: SessionState) -> None:
    """Emit the ``case-list`` envelope for the client's left rail.

    Best-effort: if Persistence is unbound (M1 in-memory path) we silently
    skip. If the listing call fails we log + skip; the case-list is a
    derivable view, so failing it should not break the chat path.

    job-0252 (OQ-0115-CASE-USER-LINK): the list is now scoped by
    ``state.authenticated_user_id`` (the resolved Firebase UID, or the
    sticky-anonymous ULID in dev), matching the owner stamped onto Cases at
    creation (``upsert_case(owner_user_id=...)``). The old ``$exists:false``
    leak clause is gone, so a Case is visible only to its owner. We fall back
    to ``session_id`` only when the handshake hasn't bound a user yet — the
    same ``authenticated_user_id or session_id`` posture as the secrets /
    chat-persist paths.
    """
    p = get_persistence()
    if p is None:
        logger.debug("case-list: Persistence unbound; skipping emit")
        return
    user_id = state.authenticated_user_id or state.session_id
    try:
        cases = await p.list_cases_for_user(user_id)
    except Exception:  # noqa: BLE001 — best-effort
        logger.exception("case-list: list_cases_for_user failed")
        return
    payload = CaseListEnvelopePayload(cases=cases)
    await websocket.send(_new_envelope("case-list", state.session_id, payload))
    logger.info(
        "case-list emitted session=%s user=%s count=%d",
        state.session_id,
        user_id,
        len(cases),
    )


def _rehydrate_case_history(
    state: SessionState,
    session_state: CaseSessionState,
    case_id: str,
) -> None:
    """Refill ``state.chat_history`` from a Case's PERSISTED messages (F17).

    Called right after the ``state.chat_history = []`` reset in both
    ``_emit_case_open`` and ``_sync_case_context``. Converts the per-Case
    persisted ``CaseChatMessage`` list (oldest-first) into the lightweight
    TEXT-turn dict shape ``build_contents_from_history`` consumes, appends a
    compact "layers already present" model turn (built from
    ``session_state.loaded_layers``), and bounds the replay to the last
    ``REHYDRATE_HISTORY_CAP`` rows so a long Case cannot blow the context
    window. Best-effort: any failure leaves the (empty) reset history intact
    rather than breaking the Case open / turn.

    Guardrail (job-0245): ``session_state`` belongs to exactly ONE
    ``case_id`` (the persisted store is keyed by Case). Switching Cases loads
    THAT Case's ``session_state``, so this cannot reintroduce the in-memory
    cross-case leak job-0245 fixed.
    """
    try:
        # F20 / panel-fix: pass the Case AOI bbox so the layers-present note
        # carries the exact extent. It survives history capping, so a long
        # Case whose head turn (which named the place) was dropped can still
        # reuse the original AOI for follow-up fetch/clip instead of
        # re-geocoding / mis-scoping.
        case_bbox = getattr(getattr(session_state, "case", None), "bbox", None)
        history, dropped = rehydrate_history_from_case(
            session_state.chat_history,
            session_state.loaded_layers,
            case_bbox=case_bbox,
        )
        # job-0269: REBIND, never extend the entry-captured list — assigning a
        # fresh object keeps an in-flight turn's captured history untouched.
        state.chat_history = history
        if dropped:
            logger.info(
                "case-history-rehydrate session=%s case=%s dropped_head=%d "
                "kept=%d (cap=%d)",
                state.session_id,
                case_id,
                dropped,
                len(history),
                REHYDRATE_HISTORY_CAP,
            )
    except Exception:  # noqa: BLE001 — rehydration is best-effort
        logger.exception(
            "case-history-rehydrate failed session=%s case=%s",
            state.session_id,
            case_id,
        )


async def _sync_case_context(
    websocket: ServerConnection, state: SessionState
) -> None:
    """Catch this CONNECTION's in-memory context up to the session's active Case.

    job-0259: ``active_case_id`` is session-scoped (see ``_SESSION_ACTIVE_CASE``)
    but ``chat_history`` (the Gemini context) and the emitter's
    ``loaded_layers`` accumulator are per-connection. When a ``case-command``
    was handled on a SIBLING connection (the web client's App.tsx socket) —
    or when this is a fresh reconnect — this connection never ran the
    ``_emit_case_open`` resets. Called at the top of every ``user-message``
    dispatch: if the connection's context was last synced to a different
    Case, apply the job-0245 replace-not-reconcile reset (clear LLM history)
    and seed the emitter from the persisted Case so subsequent
    ``add_loaded_layer`` dedup + ``_persist_case_loaded_layers`` writes
    operate on the full persisted truth set.

    Best-effort: a Persistence failure logs and leaves the emitter seeded
    empty — the merge in ``_persist_case_loaded_layers`` prevents an
    unseeded accumulator from clobbering previously persisted layers.
    """
    current = state.active_case_id
    if state.case_context_synced_to == current:
        return
    state.case_context_synced_to = current
    # Replace-not-reconcile (job-0245, applied cross-connection): this
    # connection's LLM context belongs to whatever Case it was last driving.
    # job-0269: REBIND, never clear() — an in-flight turn holds the old list
    # (captured at its stream entry) and must keep its own context intact.
    state.chat_history = []
    state.turn_count = 1  # count the in-flight turn that triggered the sync
    _ensure_emitter(websocket, state)
    if state.emitter is None:  # pragma: no cover — _ensure_emitter always binds
        return
    if current is None:
        state.emitter.reset_loaded_layers([])
        return
    p = get_persistence()
    if p is None:
        state.emitter.reset_loaded_layers([])
        return
    try:
        session_state = await p.get_session_state(current)
        state.emitter.reset_loaded_layers(session_state.loaded_layers)
        # sprint-14-aws (job-0290d): repopulate the inline-GeoJSON side-table
        # so this connection's next session-state emission carries renderable
        # vectors (mirrors the case-open path; best-effort).
        try:
            await state.emitter.reinline_vector_layers()
        except Exception:  # noqa: BLE001
            logger.warning("case-context-sync vector re-inline failed")
        # job-0263: seed the URI registry from the persisted Case layers so
        # handle-indirection works for layers produced in PRIOR sessions of
        # this Case (the LLM history was just cleared; the registry is the
        # only place the layer_id → uri association survives).
        get_uri_registry(state.session_id).seed_from_layers(
            session_state.loaded_layers
        )
        # F17 (ux-batch-1 J8): rehydrate this connection's LLM context from the
        # SAME persisted per-Case store. The ``state.chat_history = []`` above
        # is the job-0259/0245 cross-connection clean-slate; refilling it from
        # ``current``'s persisted messages (already fetched into
        # ``session_state``; do NOT re-fetch) lets a sibling-connection /
        # reconnect turn see prior work and stop recomputing. Per-Case store
        # ⇒ case-correct; switching Cases loads THAT Case's history.
        _rehydrate_case_history(state, session_state, current)
        logger.info(
            "case-context-sync session=%s case=%s layers=%d rehydrated=%d",
            state.session_id,
            current,
            len(session_state.loaded_layers),
            len(state.chat_history),
        )
    except Exception:  # noqa: BLE001 — best-effort, never break the turn
        logger.exception(
            "case-context-sync failed session=%s case=%s",
            state.session_id,
            current,
        )
        state.emitter.reset_loaded_layers([])


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
    # job-0259: this connection runs the full case-open reset below, so its
    # context is (about to be) synced to ``case_id`` — record it so the next
    # ``user-message`` on THIS connection skips the redundant re-sync.
    # Sibling connections of the same session keep their stale marker and
    # catch up via ``_sync_case_context`` on their next dispatch.
    state.case_context_synced_to = case_id
    # job-0245 (OQ-0245-CONTEXT-CARRYOVER-MISROUTE): a Case switch must reset
    # the per-connection LLM conversation, not just the case state — round-3
    # live testing proved every post-switch prompt re-routed to the PREVIOUS
    # Case's composer (a Fort Myers flood ask and a numpy ask both got the
    # Twin Falls groundwater gate) because build_contents_from_history kept
    # feeding the old turns to Gemini. Clean slate per Case (the Wave 4.8 A.7
    # replace-not-reconcile rule, applied server-side); the visible chat
    # replay comes from the persisted Case history, not this list.
    # job-0269: REBIND, never clear() — see _sync_case_context.
    state.chat_history = []
    state.turn_count = 0
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
        # sprint-14-aws (job-0290d): persisted VECTOR layers carry no inline
        # GeoJSON (the side-table is in-memory only), so the case-open payload
        # above rehydrated entries the browser cannot render (it never fetches
        # object-store uris directly — job-0175). Re-inline from the artifact
        # and emit one follow-up session-state through the proven merge path;
        # the client lifts layers from session-state, so vectors repaint.
        try:
            _reinlined = await state.emitter.reinline_vector_layers()
            if _reinlined:
                await state.emitter.emit_session_state()
        except Exception:  # noqa: BLE001 — rehydration is best-effort
            logger.exception(
                "case-open vector re-inline failed case=%s", case_id
            )

    # F17 (ux-batch-1 J8): rehydrate the LLM conversation from THIS Case's
    # persisted messages so a follow-up turn in a reopened Case sees prior
    # work and stops recomputing (e.g. a hillshade ask in the Fort Myers flood
    # Case no longer re-runs the whole flood). The ``state.chat_history = []``
    # reset above is the job-0245 cross-case clean-slate; we refill it from the
    # PER-CASE persisted store (``session_state`` — already loaded; do NOT
    # re-fetch). The store is keyed by Case, so this is inherently case-correct
    # and cannot reintroduce the job-0245 in-memory cross-case leak.
    _rehydrate_case_history(state, session_state, case_id)

    logger.info(
        "case-open session=%s case=%s chat=%d layers=%d rehydrated=%d",
        state.session_id,
        case_id,
        len(session_state.chat_history),
        len(session_state.loaded_layers),
        len(state.chat_history),
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
            # job-0252 (OQ-0115-CASE-USER-LINK): stamp the creator as owner so
            # the Case is visible to them via list_cases_for_user (the
            # $exists:false leak clause is gone). authenticated_user_id is set
            # by the auth handshake (real Firebase UID or the sticky-anonymous
            # ULID in dev); None only on the M1 unbound-Persistence path.
            await p.upsert_case(case, owner_user_id=state.authenticated_user_id)
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
        # job-0259: see _emit_case_open — this connection is now synced.
        state.case_context_synced_to = new_case_id
        # job-0245: fresh Case = fresh LLM context (see _emit_case_open note).
        # job-0269: REBIND, never clear() — see _sync_case_context.
        state.chat_history = []
        state.turn_count = 0
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

    if command == "deselect":
        # job-0269: the client navigated OUT of the active Case to the Cases
        # root. Without this command the session-scoped active Case silently
        # kept pointing at the last-opened Case: prompts sent from the root
        # view skipped auto-create and dispatched INTO the stale Case (live
        # 2026-06-10: a terrain prompt landed in the flood Case), and
        # re-selecting that same Case looked like a no-op. Clears the binding
        # + this connection's LLM context so the next root prompt auto-creates
        # a fresh Case (job-0262). Does NOT touch any in-flight turn — its
        # persistence follows the job-0268 turn pin, not this binding.
        prev = state.active_case_id
        state.active_case_id = None
        state.case_context_synced_to = None
        # job-0269: REBIND, never clear() — see _sync_case_context.
        state.chat_history = []
        state.turn_count = 0
        if state.emitter is not None:
            state.emitter.reset_loaded_layers([])
        logger.info(
            "case-command deselect session=%s prev_case=%s",
            state.session_id,
            prev,
        )
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
            # job-0259: preserve pre-existing behavior on THIS connection (no
            # chat clear on delete); siblings re-sync on their next dispatch.
            state.case_context_synced_to = None
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


#: job-0260: Cases already auto-named this process (avoid a get_case read
#: on every user turn — only the first turn per Case checks the title).
_AUTONAMED_CASES: set[str] = set()

_TITLE_STOPWORDS = frozenset(
    "a an the and or of for with to in on at by from using use run model "
    "show me my please can you what how is are this that".split()
)


def _derive_case_title(prompt: str) -> str | None:
    """Heuristic 3-6 word Case title from the first user prompt (job-0260).

    v0.1 of the deferred auto-case-name feature: significant tokens,
    title-cased, capped at ~48 chars. Returns None for degenerate prompts.
    """
    words = [
        w.strip(".,!?:;()[]\"'")
        for w in prompt.split()
    ]
    keep = [
        w for w in words if w and w.lower() not in _TITLE_STOPWORDS
    ][:6]
    if len(keep) < 2:
        return None
    title = " ".join(w if w[:1].isupper() else w.capitalize() for w in keep)
    return title[:48].rstrip() or None


async def _maybe_autoname_case(state: SessionState, prompt: str) -> bool:
    """Name an 'Untitled Case' from its first user prompt (job-0260).

    Demo finding: accumulated untitled Cases are indistinguishable in the
    left rail. Best-effort, once per Case per process; never raises.
    """
    case_id = state.active_case_id
    if not case_id or case_id in _AUTONAMED_CASES:
        return False
    _AUTONAMED_CASES.add(case_id)
    p = get_persistence()
    if p is None:
        return False
    try:
        case = await p.get_case(case_id)
        if case is None or case.title != "Untitled Case":
            return False
        title = _derive_case_title(prompt)
        if not title:
            return False
        await p.upsert_case(case.model_copy(update={"title": title}))
        logger.info("case auto-named case=%s title=%r", case_id, title)
        return True
    except Exception:  # noqa: BLE001 — naming is a nicety
        logger.debug("case auto-name failed case=%s", case_id, exc_info=True)
    return False


async def _auto_create_case_from_root(
    websocket: ServerConnection,
    state: SessionState,
    prompt: str,
) -> str | None:
    """Create + activate a Case for a chat prompt arriving with NO active Case.

    job-0262 (AUTO-CREATE CASE FROM ROOT): live demo showed prompts sent from
    the Cases root ran stateless — no Case, no Case view / layer panel, and
    orphaned results (chat turns + published layers attributed nowhere).
    When a non-directive ``user-message`` arrives and the session has no
    active Case, mint one server-side BEFORE the turn dispatches so
    ``_persist_chat_turn`` + ``_persist_case_loaded_layers`` +
    ``ensure_case_qgs`` + the ``publish_layer`` case_id injection all land in
    it. The Case is named from the prompt via ``_derive_case_title``
    (job-0260 heuristic; "Untitled Case" fallback for degenerate prompts).

    Deliberately NOT the ``case-command(create)`` reset path: the in-flight
    message IS the Case's first turn, so the per-connection LLM context
    (``chat_history``) and the FR-FR-3 ``turn_count`` are left untouched
    (v0.1 of the deferred auto-case-name design, simplified).

    Returns the new ``case_id``, or ``None`` when Persistence is unbound or
    the upsert fails — the M1 stateless path keeps working either way.
    """
    p = get_persistence()
    if p is None:
        return None
    title = _derive_case_title(prompt) or "Untitled Case"
    now = now_utc()
    case = CaseSummary(
        case_id=new_ulid(),
        title=title,
        created_at=now,
        updated_at=now,
        status="active",
    )
    try:
        # job-0252 (OQ-0115-CASE-USER-LINK): stamp the creator as owner so the
        # auto-created Case is visible to them via list_cases_for_user.
        await p.upsert_case(case, owner_user_id=state.authenticated_user_id)
    except Exception:  # noqa: BLE001 — fall back to the stateless path
        logger.exception(
            "auto-create-case upsert failed session=%s", state.session_id
        )
        return None
    state.active_case_id = case.case_id
    # This connection's in-memory context IS the new Case's context (the
    # triggering message is its first turn) — mark synced so the next
    # dispatch skips the _sync_case_context reset.
    state.case_context_synced_to = case.case_id
    # The creating prompt already named the Case — skip the job-0260
    # first-turn rename probe (it would be a wasted get_case round-trip).
    _AUTONAMED_CASES.add(case.case_id)
    await _touch_session_record(state, case_id=case.case_id)  # D.6 heartbeat
    # Fresh Case starts with zero layers — flush the per-connection
    # accumulator (replace-not-reconcile server-side; mirrors
    # ``case-command(create)``).
    _ensure_emitter(websocket, state)
    if state.emitter is not None:
        state.emitter.reset_loaded_layers([])
    logger.info(
        "auto-created case from root session=%s case=%s title=%r",
        state.session_id,
        case.case_id,
        title,
    )
    return case.case_id


async def _emit_auto_case_open(
    websocket: ServerConnection,
    state: SessionState,
    case_id: str,
) -> None:
    """Emit ``case-open`` + ``case-list`` for an auto-created Case (job-0262).

    Distinct from ``_emit_case_open``: NO context reset (no ``chat_history``
    clear, no ``turn_count`` reset, no emitter re-seed) — the in-flight user
    message IS the first turn of this Case and
    ``_auto_create_case_from_root`` already established the connection
    context. Must be called AFTER the user turn is persisted so the
    rehydration payload carries it: Chat.tsx's case-open handler is
    replace-not-reconcile (it flushes the local message buffer and re-renders
    from ``session_state.chat_history``), so emitting before the persist
    would blank the just-typed message bubble. The web client's ws.ts hub
    fans ``case-open`` out to App.tsx's socket (SESSION_SCOPED_TYPES), where
    ``useCases.onCaseOpen`` sets ``activeCaseId`` and the left rail flips
    from the Cases root into the Case view.

    Best-effort: when rehydration fails we SKIP case-open (a
    ``session_state=None`` frame would null the client's activeCaseId and
    flush the chat panel) but still refresh ``case-list`` so the left rail
    at least shows the new Case.
    """
    p = get_persistence()
    if p is not None:
        try:
            payload = CaseOpenEnvelopePayload(
                session_state=await p.get_session_state(case_id)
            )
            await websocket.send(
                _new_envelope("case-open", state.session_id, payload)
            )
        except Exception:  # noqa: BLE001 — emission is best-effort
            logger.exception(
                "auto-case-open emission failed session=%s case=%s",
                state.session_id,
                case_id,
            )
    await _emit_case_list(websocket, state)


async def _prepare_user_turn(
    websocket: ServerConnection,
    state: SessionState,
    text: str,
) -> tuple[str, dict] | None:
    """Pre-dispatch sequence for one ``user-message`` (job-0262 extraction).

    Runs, in order, BEFORE the turn task is created (so the dispatched turn —
    Gemini stream or ``/invoke`` directive — observes the final Case
    context):

    1. ``_sync_case_context`` — catch this connection up to the session's
       active Case (job-0259 sibling-connection sync).
    2. job-0262 auto-create: a non-directive prompt with NO active Case
       mints + activates a prompt-named Case (see
       ``_auto_create_case_from_root``). ``/invoke`` debug directives stay on
       the stateless path.
    3. ``_persist_chat_turn`` — the user turn lands in the (possibly brand
       new) active Case. Best-effort; no Case / no Persistence = no-op.
    4. For an auto-created Case: emit ``case-open`` + ``case-list`` so the
       web client switches from the Cases root into the Case view (after the
       persist — see ``_emit_auto_case_open``).

    Returns the parsed ``/invoke`` directive (``(tool_name, params)``) or
    ``None`` for the Gemini path — the caller branches on it.
    """
    await _sync_case_context(websocket, state)
    directive = _parse_invoke_directive(text)
    auto_case_id: str | None = None
    if directive is None and state.active_case_id is None:
        auto_case_id = await _auto_create_case_from_root(
            websocket, state, text
        )
    # job-0268: pin the turn's Case binding NOW — after the auto-create
    # hand-off, before the first write. Everything this turn persists
    # (user row, tool cards, narration, layers, charts, .qgs routing)
    # follows this pin; a mid-stream case switch must not re-aim it.
    state.current_turn_case_id = state.active_case_id
    await _persist_chat_turn(state, role="user", content=text)
    if auto_case_id is not None:
        await _emit_auto_case_open(websocket, state, auto_case_id)
    return directive


def _turn_case_id(state: SessionState) -> str | None:
    """The Case the current turn is bound to (job-0268).

    Prefers the pin set by ``_prepare_user_turn`` at dispatch time; falls
    back to the live ``active_case_id`` for callers outside a prepared turn
    (direct tool invocations in tests, legacy paths). The fallback IS the
    pre-fix behavior — every persistence site read ``active_case_id`` at
    WRITE time, so a ``case-command(select)`` arriving mid-stream re-aimed
    in-flight writes at the newly selected Case (job-0267 verifier).
    """
    return state.current_turn_case_id or state.active_case_id


def _turn_case_bbox(state: SessionState) -> Any:
    """The current turn's Case AOI bbox (job-0326), or None.

    Used by the expensive-simulation reuse guard as the AOI anchor when a
    persistence-seeded result has no recorded bbox: a bbox-keyed re-run in a
    single-result Case whose request bbox equals the Case AOI is a clear match.
    """
    p = get_persistence()
    case_id = _turn_case_id(state)
    if p is None or not case_id:
        return None
    try:
        # Cheap synchronous read of the already-cached active case summary.
        case = getattr(state, "active_case", None)
        bbox = getattr(case, "bbox", None) if case is not None else None
        return bbox
    except Exception:  # noqa: BLE001 — best-effort
        return None


@dataclass
class _ReuseEntry:
    """A drop-in ``RegisteredTool``-shaped shim for the reuse short-circuit
    (job-0326).

    Carries the real tool's ``metadata`` (so the tool card / telemetry label is
    unchanged) but a ``fn`` that returns the EXISTING layer instead of launching
    the solver. ``_invoke_tool_via_emitter`` swaps the registry entry for this so
    the SAME ``emit_tool_call`` LayerURI gate fires with the reused layer.
    """

    metadata: Any
    layer: LayerURI

    @property
    def fn(self) -> Any:
        layer = self.layer

        def _return_existing(**_ignored: Any) -> LayerURI:
            return layer

        return _return_existing


async def _finalize_segment(
    websocket: ServerConnection,
    state: SessionState,
    message_id: str,
    segment_parts: list[str],
    *,
    is_terminal: bool = False,
) -> None:
    """job-0315: close ONE narration bubble + persist it as its own agent row.

    Each contiguous run of agent text between tool-call rounds is a SEGMENT.
    Closing a segment does two things at the boundary "agent text is about to
    be interrupted by tool cards (or the turn is ending)":

    (1) Send the terminal ``done=True`` ``agent-message-chunk`` for THIS
        bubble's ``message_id`` so the live client marks the bubble complete
        (web ``appendDelta`` sets ``done``). This MUST only fire for an id
        that already received text — the caller guarantees that by only
        calling here when ``current_message_id is not None``.
    (2) Persist a ``role="agent"`` ``CaseChatMessage`` carrying ONLY this
        segment's text, so the persisted row order interleaves with the
        mid-turn tool rows (``_persist_tool_card``) and the replay
        reconstructs the live interleaved train. An empty segment persists
        NOTHING (no phantom bubble on replay; no row-count regression).

    ``layer_emissions``: non-terminal segments pass ``[]`` so they do NOT each
    duplicate the whole-turn ``current_turn_layer_ids`` / ``current_turn_map_commands``
    accumulators. The TERMINAL segment (``is_terminal=True`` — the final
    narration run of the turn) passes ``None`` so ``_persist_chat_turn``
    snapshots the accumulators onto it, keeping job-0259 layer attribution +
    job-0281 zoom-to on the de-facto closing row.

    Best-effort persist (inherits ``_persist_chat_turn``'s swallow); the wire
    ``done=True`` still fires even if persistence is unbound. Clears the
    segment buffer and bumps the per-task finalized-count on a non-empty write.
    """
    text = "".join(segment_parts).strip()
    # (1) wire terminal for this bubble — always fires (id has text).
    await websocket.send(
        _new_envelope(
            "agent-message-chunk",
            state.session_id,
            AgentMessageChunkPayload(message_id=message_id, delta="", done=True),
        )
    )
    # (2) per-segment persist — only when there is real text.
    if text:
        await _persist_chat_turn(
            state,
            role="agent",
            content=text,
            pipeline_id=state.current_turn_pipeline_id,
            # Terminal segment owns the layer/zoom attribution; non-terminal
            # segments carry none (the accumulator rides the last row only).
            layer_emissions=None if is_terminal else [],
            case_id=_turn_case_id(state),
        )
        _task = asyncio.current_task()
        if _task is not None:
            _TURN_SEGMENTS_PERSISTED_BY_TASK[_task] = (
                _TURN_SEGMENTS_PERSISTED_BY_TASK.get(_task, 0) + 1
            )
            # job-0315 contract fix: a TERMINAL non-empty segment row just
            # snapshotted the turn's zoom-to/layer accumulator
            # (``layer_emissions=None`` above). Record that so the wrapper's
            # finally does NOT also write a duplicate closing marker row — the
            # marker is ONLY for the tool-terminal shape where this never fires.
            if is_terminal:
                _TURN_TERMINAL_ACC_PERSISTED_BY_TASK[_task] = True
    # The open buffer is now closed: clear the SAME list object (do not rebind)
    # so the task-registered open buffer the wrapper reads is always current.
    segment_parts.clear()


async def _persist_chat_turn(
    state: SessionState,
    *,
    role: str,
    content: str,
    pipeline_id: str | None = None,
    tool_card: ToolCardRecord | None = None,
    layer_emissions: list[str] | None = None,
    case_id: str | None = None,
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

    job-0267: ``tool_card`` carries the typed ``ToolCardRecord`` for
    ``role="tool"`` rows; ``layer_emissions`` overrides the default
    per-turn accumulator snapshot (tool rows pass ``[]`` so the turn's
    layer ids stay attributed to the closing agent row, exactly as before).

    job-0268: ``case_id`` pins the target Case explicitly (the dispatch
    wrappers capture it at task entry so even a cancel-and-redispatch race
    cannot re-aim the write); when omitted it resolves via ``_turn_case_id``
    — never the raw write-time ``active_case_id``.
    """
    target_case = case_id if case_id is not None else _turn_case_id(state)
    if not target_case:
        return
    p = get_persistence()
    if p is None:
        return
    msg = CaseChatMessage(
        message_id=new_ulid(),
        case_id=target_case,
        role=role,  # type: ignore[arg-type]
        content=content,
        pipeline_id=pipeline_id,
        tool_card=tool_card,
        layer_emissions=(
            list(state.current_turn_layer_ids)
            if layer_emissions is None
            else list(layer_emissions)
        ),
        # job-0281: persist the turn's zoom-to emissions (geocode snap) on
        # rows that snapshot the accumulator (agent/user rows) — the
        # Case-reopen snap-to-location replays the LAST one (job-0280 web).
        # Tool rows pass layer_emissions=[] and get [] here too.
        map_command_emissions=(
            list(state.current_turn_map_commands)
            if layer_emissions is None
            else []
        ),
        created_at=now_utc(),
    )
    try:
        await p.append_chat_message(msg)
        # Per-turn D.6 heartbeat (job-0203 / M4): the chat turn is the
        # activity signal that keeps the session record's TTL fresh and
        # the turn's Case registered in ``project_ids``.
        await _touch_session_record(state, case_id=target_case)
        logger.debug(
            "chat-persist session=%s case=%s role=%s msg_id=%s pipeline_id=%s layers=%d",
            state.session_id,
            target_case,
            role,
            msg.message_id,
            pipeline_id,
            len(msg.layer_emissions),
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "chat-persist failed session=%s case=%s role=%s",
            state.session_id,
            target_case,
            role,
        )


async def _persist_tool_card(
    state: SessionState,
    *,
    tool_name: str,
    label: str,
    card_state: str,
    started_at_fallback: datetime,
    duration_ms_fallback: int,
    case_id: str | None = None,
) -> None:
    """Persist one replayable tool-card row for the active Case (job-0267).

    Written by ``_invoke_tool_via_emitter`` on every terminal tool dispatch
    (complete OR failed; cancelled dispatches persist nothing — Invariant 8).
    Storage shape: ``CaseChatMessage(role="tool")`` in the SAME chat
    collection as user/agent turns, so the rehydration replay interleaves
    the full stream by ``created_at`` with zero extra queries. The typed
    payload is ``tool_card`` (``ToolCardRecord``); ``content`` carries the
    identical record as a JSON string for non-contract consumers.

    Timing source of truth: the emitter's ``last_tool_step`` (the job-0264
    authoritative ``started_at`` / ``duration_ms`` stamps the live card
    displayed). The wall-clock fallbacks only engage when the emitter stamp
    is unavailable (e.g. the wire died before the terminal transition).

    Best-effort, never raises: record construction is wrapped here and the
    underlying ``_persist_chat_turn`` already swallows write failures.
    """
    try:
        started_at = started_at_fallback
        duration_ms: int = max(0, int(duration_ms_fallback))
        emitter_step = (
            state.emitter.last_tool_step if state.emitter is not None else None
        )
        if emitter_step is not None and emitter_step.tool_name == tool_name:
            if emitter_step.started_at is not None:
                started_at = emitter_step.started_at
            if emitter_step.duration_ms is not None:
                duration_ms = emitter_step.duration_ms
        record = ToolCardRecord(
            tool_name=tool_name,
            state=card_state,  # type: ignore[arg-type]
            started_at=started_at,
            duration_ms=duration_ms,
            label=label,
        )
        await _persist_chat_turn(
            state,
            role="tool",
            content=record.model_dump_json(),
            pipeline_id=state.current_turn_pipeline_id,
            tool_card=record,
            layer_emissions=[],
            case_id=case_id,
        )
    except Exception:  # noqa: BLE001 — replay material, never the happy path
        logger.exception(
            "tool-card persist failed session=%s case=%s tool=%s",
            state.session_id,
            case_id if case_id is not None else _turn_case_id(state),
            tool_name,
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
    try:
        if tool_name == "run_model_groundwater_contamination_scenario":
            from .workflows.model_groundwater_contamination_scenario import (
                _build_confirmation_envelope,
                extract_spill_parameters,
            )
            from grace2_contracts.modflow_contracts import MODFLOWRunArgs

            article_text = params.get("article_text")
            if not isinstance(article_text, str) or not article_text.strip():
                # source_url path or missing text: let the composer surface
                # its own typed error (v0.1 live path supplies article_text).
                return True, params
            # extract_spill_parameters is synchronous (pure extraction +
            # cached geocode); off the event loop so the WS heartbeat lives.
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
            envelope = _build_confirmation_envelope(
                derived, MODFLOWRunArgs(**kwargs)
            )
        elif tool_name in ("run_model_flood_scenario",
                           "run_model_flood_habitat_scenario"):
            # job-0256 (live finding: a flood solver ran in a sandbox-only
            # session): a ~10-20 min SFINCS solve is a consequence — show
            # the user what is about to run. Card built straight from the
            # call args (no extraction needed).
            from grace2_contracts.payload_warning import (
                PayloadWarningEnvelopePayload,
            )

            where = params.get("location_query") or params.get("bbox") or "?"
            envelope = PayloadWarningEnvelopePayload(
                warning_id=new_ulid(),
                tool_name=tool_name,
                tool_args={
                    "location": str(where),
                    "return_period_yr": params.get("return_period_yr"),
                    "duration_hr": params.get("duration_hr"),
                    "forcing_raster_uri": params.get("forcing_raster_uri"),
                    "compute_class": params.get("compute_class", "standard"),
                },
                estimated_mb=0.0,
                threshold_mb=0.0,
                recommendation=(
                    f"Run a SFINCS flood simulation for {where} "
                    "(cloud solve, typically 5-20 minutes). Confirm to start."
                )[:512],
                options=["proceed", "cancel"],
            )
        else:  # unknown gated tool: fail open to the tool's own validation
            return True, params
    except Exception:  # noqa: BLE001 — never mask param errors with a gate
        logger.warning(
            "solver-confirm gate could not build the confirm card for %s; "
            "falling through so the tool raises its typed error",
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
        # job (terminal-pipeline-card hardening / Gap 1): the WS may be mid-close
        # when a terminal pipeline-state frame (mark_cancelled / mark_failed) is
        # emitted on the cancel path — ``websocket.send`` then raises
        # ConnectionClosed straight out of the emitter, swallowing the terminal
        # frame AND letting the exception escape the cancel chain. Best-effort:
        # swallow send failures so the card-state transition is always recorded
        # server-side and the CancelledError propagates cleanly for any clients
        # still attached. Mirrors the existing swallow at the outer-loop cancel
        # emit (the gemini-cancel pipeline-state send).
        try:
            await websocket.send(text)
        except Exception:  # noqa: BLE001 — socket may be closing on cancel/fail
            logger.debug(
                "emitter sink: websocket.send failed (socket closing?); "
                "frame dropped best-effort (session=%s)",
                state.session_id,
            )

    state.emitter = PipelineEmitter(
        session_id=state.session_id,
        sink=_sink,
        chat_history=state.chat_history,
    )


# --------------------------------------------------------------------------- #
# Credential pipeline (job VAULT-READ): secret_ref injection + auth-error ->
# credential-request -> retry.
# --------------------------------------------------------------------------- #


async def _resolve_active_secret_ref(
    state: SessionState, tool_name: str, case_id: str | None
) -> Any | None:
    """Return the user's active ``SecretRecord`` for ``tool_name``'s provider.

    Looks up the per-Case secret first (scoped to the turn's Case) then falls
    back to user-level secrets, filtering by the provider the tool needs
    (``credential_registry.provider_for_tool``). Returns the freshest active
    record or ``None`` when the tool is not keyed, no Persistence is bound, or
    no matching active secret exists.

    Best-effort: a Persistence/MCP wobble logs and returns ``None`` so the tool
    falls back to its env path / typed auth-error (which the credential-request
    flow then acts on) — a vault lookup hiccup must not crash the dispatch.
    """
    provider = provider_for_tool(tool_name)
    if provider is None:
        return None
    p = get_persistence()
    if p is None:
        return None
    user_id = state.authenticated_user_id or state.session_id
    try:
        # Prefer Case-scoped secrets; fall back to user-level (case_id=None)
        # records so a key the user added outside a Case still resolves.
        records = []
        if case_id:
            records = await p.list_secrets_refs(user_id=user_id, case_id=case_id)
        if not records:
            records = await p.list_secrets_refs(user_id=user_id, case_id=None)
    except Exception:  # noqa: BLE001 — vault lookup is best-effort
        logger.debug(
            "secret_ref lookup failed tool=%s case=%s", tool_name, case_id,
            exc_info=True,
        )
        return None
    # Filter to the tool's provider. ``provider_id`` on the registry may carry a
    # value not yet in the ``ProviderID`` Literal (FIRMS pre-amendment); match
    # the SecretRecord.provider string directly.
    matches = [
        r for r in records
        if getattr(r, "provider", None) == provider.provider_id and r.is_active
    ]
    if not matches:
        return None
    # Freshest by added_at (records are SecretRecords with UTC added_at).
    matches.sort(key=lambda r: getattr(r, "added_at", None) or "", reverse=True)
    return matches[0]


async def _inject_secret_ref(
    state: SessionState,
    tool_name: str,
    params: dict,
    case_id: str | None,
) -> dict:
    """Thread the user's active per-Case ``secret_ref`` into a keyed tool's params.

    No-op for non-keyed tools, when the caller already supplied an explicit
    ``secret_ref`` / key kwarg, or when no active secret exists. The tool's
    ``_resolve_*_key`` then reads the VAULT key first (then env), per the
    eBird secret_ref convention.
    """
    if provider_for_tool(tool_name) is None:
        return params
    # Respect an explicit override already on params (dev/test path).
    if params.get("secret_ref") is not None:
        return params
    record = await _resolve_active_secret_ref(state, tool_name, case_id)
    if record is None:
        return params
    params = dict(params)
    params["secret_ref"] = record
    logger.info(
        "secret_ref injected tool=%s provider=%s secret_id=%s",
        tool_name,
        getattr(record, "provider", None),
        getattr(record, "secret_id", None),
    )
    return params


async def _maybe_handle_credential_error(
    websocket: ServerConnection,
    state: SessionState,
    tool_name: str,
    params: dict,
    error: BaseException,
    case_id: str | None,
) -> dict | None:
    """Handle a keyed-tool credential error: prompt + await + re-resolve.

    Returns:
    - ``dict`` (retry params with a freshly-resolved ``secret_ref``) when the
      user supplied a key (``credential-provided`` with ``provided=True``) —
      the caller retries the tool ONCE.
    - ``None`` when the error is NOT a credential error, the provider is not
      registered, the tool already prompted this turn (one-prompt-per-tool-per-
      turn guard), or the user declined / the gate timed out. The caller then
      re-raises the original error so it flows through the normal typed-error
      surface (FR-AS-11) and Gemini narrates the failure honestly.
    """
    if not is_credential_error(tool_name, error):
        return None
    provider = provider_for_tool(tool_name)
    if provider is None:
        return None
    # One prompt per tool per turn — don't loop forever on a still-bad key.
    if tool_name in state.credential_prompted_tools:
        logger.info(
            "credential-request suppressed (already prompted this turn) tool=%s",
            tool_name,
        )
        return None
    state.credential_prompted_tools.add(tool_name)

    provided = await _emit_credential_request_and_wait(
        websocket, state, tool_name, provider, error
    )
    if provided is None or not provided.provided:
        # Declined / timed out: surface the original typed error.
        return None

    # Key saved to the vault: re-resolve the secret_ref so the retry reads the
    # NEW key. Strip any stale secret_ref/map_key from params first.
    retry_params = {
        k: v for k, v in params.items()
        if k not in ("secret_ref", "map_key", "api_key")
    }
    retry_params = await _inject_secret_ref(
        state, tool_name, retry_params, case_id
    )
    return retry_params


async def _emit_credential_request_and_wait(
    websocket: ServerConnection,
    state: SessionState,
    tool_name: str,
    provider: CredentialProvider,
    error: BaseException,
) -> "CredentialProvidedEnvelopePayload | None":
    """Emit a ``credential-request`` envelope and await ``credential-provided``.

    Blocks on a future keyed by the minted ``request_id`` (registered in the
    session-scoped ``_PENDING_CREDENTIALS`` registry so a reply on a sibling
    connection still resolves it). Returns the ``CredentialProvidedEnvelopePayload``
    on reply, or ``None`` on timeout (the gate gets the same 300s read-decision
    TTL as the payload-warning / code-exec gates — fail-open to the original
    typed error so the turn is not hung).
    """
    request_id = new_ulid()
    # Prefer the tool's typed-error message (honest, specific) over the
    # registry default; both name that a key is needed (no silent dead-end).
    err_detail = str(error).strip()
    message = provider.default_message
    if err_detail:
        message = f"{provider.default_message} ({err_detail[:400]})"

    # Build the envelope scoped to the REAL provider (every registered
    # provider_id is now a valid ``ProviderID`` Literal member). If validation
    # fails for an unregistered provider, ``_build_credential_request_payload``
    # returns ``None`` — we abandon the prompt rather than mis-scope the
    # secret-add (which would save the key where the retry can't re-resolve it).
    # The caller then surfaces the original typed error (honest narration).
    payload = _build_credential_request_payload(
        request_id=request_id,
        provider=provider,
        tool_name=tool_name,
        message=message,
    )
    if payload is None:
        return None

    loop = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()
    _register_pending_credential(state.session_id, request_id, fut)

    await websocket.send(
        _new_envelope("credential-request", state.session_id, payload)
    )
    logger.info(
        "credential-request emitted session=%s tool=%s provider=%s request_id=%s",
        state.session_id,
        tool_name,
        provider.provider_id,
        request_id,
    )

    try:
        provided: CredentialProvidedEnvelopePayload = await asyncio.wait_for(
            fut, timeout=CODE_EXEC_CONFIRM_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        logger.warning(
            "credential-request timeout session=%s tool=%s request_id=%s",
            state.session_id,
            tool_name,
            request_id,
        )
        return None
    finally:
        _pop_pending_credential(request_id)

    logger.info(
        "credential-provided received session=%s tool=%s request_id=%s provided=%s",
        state.session_id,
        tool_name,
        request_id,
        provided.provided,
    )
    return provided


def _build_credential_request_payload(
    *,
    request_id: str,
    provider: CredentialProvider,
    tool_name: str,
    message: str,
) -> "CredentialRequestEnvelopePayload | None":
    """Build a validated ``CredentialRequestEnvelopePayload``.

    Every registered provider's ``provider_id`` is now a member of the closed
    ``ProviderID`` Literal (the schema amendment landed with this job), so the
    payload is scoped to the REAL provider — the same scope the resulting
    ``secret-add`` writes under and the same scope ``_resolve_active_secret_ref``
    re-reads on retry, so the round-trip closes (no more
    ``"openweathermap"`` fallback mis-scoping the saved key).

    If a ``provider.provider_id`` is somehow NOT a valid Literal member (an
    unregistered provider slipped into the registry), we DO NOT fabricate a
    fallback scope — emitting under the wrong provider would save the key where
    the retry can't re-resolve it. We log and return ``None`` so the caller
    abandons the prompt and lets the original typed error surface (the agent
    narrates honestly that it cannot request a key for an unknown provider).
    """
    try:
        return CredentialRequestEnvelopePayload(
            request_id=request_id,
            provider_id=provider.provider_id,  # type: ignore[arg-type]
            provider_label=provider.label,
            signup_url=provider.signup_url,
            secret_key_name=provider.secret_key_name,
            message=message,
            tool_name=tool_name,
        )
    except ValidationError:
        logger.error(
            "credential-request: provider_id=%r (%r) is not a member of the "
            "ProviderID Literal — cannot scope a secret-add that re-resolves "
            "on retry; abandoning prompt and surfacing the original error",
            provider.provider_id,
            provider.label,
        )
        return None


# --------------------------------------------------------------------------- #
# Region-disambiguation picker (state-bbox-fallback narrowing).
# --------------------------------------------------------------------------- #
#
# job-0346 made ``geocode_location`` snap a vague/regional query ("south
# Florida") to the WHOLE state bbox and stamp ``source="state-bbox-fallback"``
# + an honest ``fallback_reason``. That state bbox stays the DEFAULT/automated
# answer. ON TOP of it, when an interactive client is connected, surface a user
# choice to NARROW to a sub-region (default: counties). This MIRRORS the
# credential-request pause/resume seam above: emit a ``region-choice-request``,
# pause the turn on a future keyed by the choice request_id, and on
# ``region-choice-provided`` either narrow the geocode bbox (choice="region")
# or keep the state bbox (choice="whole_state"). Fail-open: a headless client /
# timeout keeps the state bbox unchanged, so the automated path never blocks.

# Default candidate granularity. Counties ship at v0.1; structured as a module
# constant so a light state-size/goal heuristic can override it per request.
# TODO(region-choice): coarser ("state_region" groupings) / finer ("place" /
# "zcta") levels are a follow-up — the RegionAdminLevel Literal + the TIGER
# fetch plumbing in fetch_administrative_boundaries gate that expansion.
_DEFAULT_REGION_ADMIN_LEVEL = "county"

# How many candidate regions to surface at most. A large state (e.g. Texas =
# 254 counties) would otherwise flood the in-chat card list + the map
# choropleth; the cap keeps the picker legible. The whole-state default is
# always available regardless, so a capped list never hides the honest answer.
_MAX_REGION_CANDIDATES = 254


def _region_admin_level_for(state_code: str, query: str) -> str:
    """Choose the candidate admin granularity for ``state_code`` + ``query``.

    DEFAULT is ``"county"`` for every state (the v0.1 shipping behaviour). This
    is the single seam a future heuristic (or the agent) hooks to pick a
    coarser/finer level by state size + query goal — kept as a function so the
    policy lives in one place. Today it returns the county default unchanged;
    the ``RegionAdminLevel`` Literal is closed to ``"county"`` so any other
    return value would fail envelope validation (a deliberate guard until the
    finer-level fetch plumbing lands).
    """
    return _DEFAULT_REGION_ADMIN_LEVEL


def _build_region_candidates(
    state_bbox: tuple[float, float, float, float],
    admin_level: str,
) -> list[RegionCandidate]:
    """Build the candidate sub-regions for a snapped state via TIGER boundaries.

    Fetches the administrative boundaries for ``admin_level`` (default
    ``"county"``) clipped to the whole-state ``state_bbox`` through the EXISTING
    ``fetch_administrative_boundaries`` fetch path, reads the resulting
    FlatGeobuf back with geopandas, and emits one ``RegionCandidate`` per
    feature: ``region_id`` from the TIGER GEOID, ``name`` from the feature
    NAME(LSAD), ``bbox`` from the feature polygon's ``total_bounds``.

    Best-effort: any failure (geopandas missing, TIGER download hiccup, empty
    clip) returns an EMPTY list — the caller then offers only the whole-state
    default (honest degrade, fallback norm). Never raises.

    Calls ``_fetch_admin_boundaries_bytes`` directly (rather than the
    cache-wrapped ``fetch_administrative_boundaries``) so the candidate build
    is decoupled from the layer-publish path: we only need the geometry +
    attributes in-process, not a published LayerURI. The TIGER download is
    itself cached for the published-boundary path, so this does not add a new
    uncached fetch in practice.
    """
    try:
        import geopandas as gpd  # type: ignore[import-not-found]
        from io import BytesIO

        from .tools.fetch_administrative_boundaries import (
            _fetch_admin_boundaries_bytes,
        )
    except ImportError:
        logger.debug("region-choice: geopandas unavailable", exc_info=True)
        return []

    try:
        fgb_bytes = _fetch_admin_boundaries_bytes(admin_level, tuple(state_bbox))
    except Exception:  # noqa: BLE001 — boundary fetch is best-effort
        logger.warning(
            "region-choice: fetch_admin_boundaries failed level=%s bbox=%s; "
            "offering whole-state default only",
            admin_level,
            state_bbox,
            exc_info=True,
        )
        return []

    try:
        gdf = gpd.read_file(BytesIO(fgb_bytes), engine="pyogrio")
    except Exception:  # noqa: BLE001 — parse is best-effort
        logger.warning("region-choice: FlatGeobuf read failed", exc_info=True)
        return []

    candidates: list[RegionCandidate] = []
    seen_ids: set[str] = set()
    for _, row in gdf.iterrows():
        geom = row.get("geometry")
        if geom is None or geom.is_empty:
            continue
        geoid = (
            row.get("GEOID")
            or row.get("GEOIDFQ")
            or row.get("COUNTYFP")
            or ""
        )
        region_id = f"{admin_level}-{geoid}" if geoid else f"{admin_level}-{len(candidates)}"
        if region_id in seen_ids:
            continue
        seen_ids.add(region_id)
        name = (
            row.get("NAMELSAD")
            or row.get("NAME")
            or region_id
        )
        minx, miny, maxx, maxy = (float(v) for v in geom.bounds)
        try:
            candidate = RegionCandidate(
                region_id=str(region_id)[:120],
                name=str(name)[:200],
                bbox=(minx, miny, maxx, maxy),
                admin_level=admin_level,  # type: ignore[arg-type]
            )
        except ValidationError:
            # A degenerate / out-of-range polygon bbox — skip it rather than
            # abort the whole set (one bad TIGER feature must not kill the pick).
            continue
        candidates.append(candidate)
        if len(candidates) >= _MAX_REGION_CANDIDATES:
            break

    candidates.sort(key=lambda c: c.name)
    logger.info(
        "region-choice: built %d candidate region(s) level=%s",
        len(candidates),
        admin_level,
    )
    return candidates


def _build_region_choice_request_payload(
    *,
    request_id: str,
    geocode_result: dict,
) -> "RegionChoiceRequestEnvelopePayload | None":
    """Build a validated ``region-choice-request`` from a state-snap geocode dict.

    Derives the state name + 2-letter code from the geocode result's ``name``
    (``"<State>, United States"``), uses its ``bbox`` as the whole-state extent,
    builds the candidate sub-regions (default: counties), and composes an honest
    prompt that says the agent snapped to the whole state and is offering a
    narrower pick (the fallback honesty floor).

    Returns ``None`` when the state cannot be resolved or the result is not a
    valid state-snap shape — the caller then leaves the state bbox unchanged.
    """
    from .tools.us_states import resolve_state_code, state_display_name

    bbox = geocode_result.get("bbox")
    if not (isinstance(bbox, (list, tuple)) and len(bbox) == 4):
        return None
    # The state-snap name is "<State>, United States"; strip the suffix to get
    # the state name, then resolve the 2-letter code.
    raw_name = str(geocode_result.get("name") or "")
    state_name = raw_name.split(",")[0].strip()
    state_code = resolve_state_code(state_name)
    if state_code is None:
        logger.info(
            "region-choice: could not resolve state from name=%r; "
            "keeping whole-state bbox",
            raw_name,
        )
        return None
    # Prefer the canonical display name for the resolved code.
    state_name = state_display_name(state_code)

    admin_level = _region_admin_level_for(
        state_code, str(geocode_result.get("query") or "")
    )
    candidates = _build_region_candidates(tuple(bbox), admin_level)

    # Honest prompt — name the snap + the offer (fallback norm). Prefer the
    # geocode's own fallback_reason as the lead so the narration is consistent.
    reason = str(geocode_result.get("fallback_reason") or "").strip()
    level_word = "county" if admin_level == "county" else admin_level
    if candidates:
        offer = (
            f" Pick a {level_word} below to narrow the area, or keep the whole "
            f"state of {state_name}."
        )
    else:
        offer = (
            f" I could not load {level_word} boundaries right now, so I will "
            f"use the whole state of {state_name} unless you refine the area."
        )
    lead = reason or (
        f"No precise match for that location; I snapped to the whole state of "
        f"{state_name}."
    )
    message = (lead + offer)[:1024]

    try:
        return RegionChoiceRequestEnvelopePayload(
            request_id=request_id,
            state_name=state_name,
            state_code=state_code,
            state_bbox=(float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])),
            candidates=candidates,
            message=message,
        )
    except ValidationError:
        logger.warning(
            "region-choice: request payload validation failed name=%r bbox=%s",
            raw_name,
            bbox,
            exc_info=True,
        )
        return None


async def _emit_region_choice_and_wait(
    websocket: ServerConnection,
    state: SessionState,
    payload: "RegionChoiceRequestEnvelopePayload",
) -> "RegionChoiceProvidedEnvelopePayload | None":
    """Emit a ``region-choice-request`` and await ``region-choice-provided``.

    Blocks on a future keyed by ``payload.request_id`` (registered in the
    session-scoped ``_PENDING_REGION_CHOICES`` registry so a reply on a sibling
    connection still resolves it). Returns the ``RegionChoiceProvidedEnvelopePayload``
    on reply, or ``None`` on timeout (the gate gets the same read-decision TTL
    as the credential / payload-warning / code-exec gates — fail-open to the
    whole-state default so the turn is never hung).
    """
    loop = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()
    _register_pending_region_choice(state.session_id, payload.request_id, fut)

    await websocket.send(
        _new_envelope("region-choice-request", state.session_id, payload)
    )
    logger.info(
        "region-choice-request emitted session=%s state=%s candidates=%d request_id=%s",
        state.session_id,
        payload.state_code,
        len(payload.candidates),
        payload.request_id,
    )

    try:
        provided: RegionChoiceProvidedEnvelopePayload = await asyncio.wait_for(
            fut, timeout=CODE_EXEC_CONFIRM_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        logger.info(
            "region-choice-request timeout session=%s request_id=%s; "
            "using whole-state default",
            state.session_id,
            payload.request_id,
        )
        return None
    finally:
        _pop_pending_region_choice(payload.request_id)

    logger.info(
        "region-choice-provided received session=%s request_id=%s choice=%s",
        state.session_id,
        payload.request_id,
        provided.choice,
    )
    return provided


async def _maybe_handle_region_choice(
    websocket: ServerConnection,
    state: SessionState,
    geocode_result: dict,
) -> None:
    """If ``geocode_result`` is a state-snap, offer + apply a narrower region.

    No-op unless the geocode came back as a state-bbox-fallback (job-0346
    ``source == "state-bbox-fallback"``). When it did, this:

    1. Builds the candidate sub-regions (default: counties of the state) and
       emits a ``region-choice-request`` (whole-state default + candidates +
       an honest prompt).
    2. PAUSES the turn awaiting ``region-choice-provided`` (fail-open: a
       headless client / timeout keeps the whole-state bbox).
    3. On ``choice == "region"`` MUTATES ``geocode_result`` in place to the
       picked region's bbox (re-resolved by ``selected_region_id`` against the
       candidate set — authoritative over a client-sent bbox; falls back to
       ``selected_bbox`` only when the id is unknown) and stamps narrowing
       provenance so downstream tools + the function_response Gemini reads use
       the narrowed extent. On ``choice == "whole_state"`` leaves the state
       bbox unchanged.

    Best-effort: any failure leaves the whole-state bbox intact (the honest
    default) — the narrowing is a UX nicety layered ON TOP of an already-correct
    result, so it must never break the turn. Never raises.
    """
    if geocode_result.get("source") != "state-bbox-fallback":
        return
    if state.emitter is None:
        # No interactive surface bound; keep the whole-state default.
        return
    try:
        request_id = new_ulid()
        payload = _build_region_choice_request_payload(
            request_id=request_id, geocode_result=geocode_result
        )
        if payload is None:
            return
        provided = await _emit_region_choice_and_wait(websocket, state, payload)
        if provided is None or provided.choice == "whole_state":
            # Declined / timed out / explicit whole-state — keep the state bbox.
            geocode_result["region_choice"] = "whole_state"
            return
        # choice == "region": resolve the picked candidate. Prefer re-resolving
        # by region_id against the candidate set (a tampered client bbox cannot
        # redirect the workflow); fall back to the echoed bbox only if unknown.
        chosen = None
        if provided.selected_region_id:
            chosen = next(
                (
                    c
                    for c in payload.candidates
                    if c.region_id == provided.selected_region_id
                ),
                None,
            )
        new_bbox: tuple[float, float, float, float] | None = None
        chosen_name: str | None = None
        if chosen is not None:
            new_bbox = chosen.bbox
            chosen_name = chosen.name
        elif provided.selected_bbox is not None:
            new_bbox = provided.selected_bbox
        if new_bbox is None:
            # The client said "region" but supplied neither a known id nor a
            # bbox — keep the state default rather than guess.
            geocode_result["region_choice"] = "whole_state"
            return
        # Mutate the geocode result IN PLACE so the immediate zoom-to AND the
        # function_response Gemini reads (and any downstream bbox consumer) use
        # the narrowed extent.
        geocode_result["bbox"] = list(new_bbox)
        # The result is no longer a whole-state snap — drop the fallback source
        # so a downstream re-trigger does not re-offer the picker, and record
        # honest provenance of the narrowing.
        geocode_result["source"] = "region-choice-narrowed"
        geocode_result["region_choice"] = "region"
        geocode_result["selected_region_id"] = provided.selected_region_id
        if chosen_name:
            geocode_result["name"] = chosen_name
            geocode_result["region_name"] = chosen_name
        # Recompute a rough centroid for the narrowed bbox so map snaps + any
        # centroid consumer stay consistent with the new extent.
        geocode_result["longitude"] = (new_bbox[0] + new_bbox[2]) / 2.0
        geocode_result["latitude"] = (new_bbox[1] + new_bbox[3]) / 2.0
        logger.info(
            "region-choice: narrowed to region_id=%s name=%r bbox=%s",
            provided.selected_region_id,
            chosen_name,
            new_bbox,
        )
    except Exception:  # noqa: BLE001 — narrowing is a best-effort UX layer
        logger.warning(
            "region-choice handling failed; keeping whole-state bbox",
            exc_info=True,
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

    # job-0268: bind this dispatch to the turn's Case ONCE, up front. The
    # .qgs routing, tool-card persist, and layer attribution below all use
    # this capture — a mid-dispatch ``case-command(select)`` must not re-aim
    # them at the newly visible Case (verified contamination, job-0267).
    turn_case_id = _turn_case_id(state)

    # job-0121: per-Case ``.qgs`` lazy-init for ``publish_layer``.
    #
    # When invoked inside a Case context (turn bound to a Case) we
    # resolve (or initialize) the per-Case ``.qgs`` URI BEFORE the tool body
    # runs, then substitute it into ``project_qgs_uri`` so the worker mutates
    # the case-scoped file rather than the shared default. This is the
    # OQ-62-QGS-MUTATION-CONFLICT resolution path.
    if tool_name == "publish_layer" and turn_case_id:
        try:
            case_qgs = await ensure_case_qgs(
                get_persistence(), turn_case_id
            )
        except CaseLifecycleError as exc:
            logger.warning(
                "case-qgs lazy-init failed code=%s case=%s err=%s; "
                "falling back to default .qgs",
                exc.error_code,
                turn_case_id,
                exc,
            )
        else:
            # Substitute (additively) without clobbering an explicit override.
            params = dict(params)
            params.setdefault("project_qgs_uri", case_qgs)
            params.setdefault("case_id", turn_case_id)
            logger.info(
                "publish_layer routed to case-scoped qgs case=%s qgs=%s",
                turn_case_id,
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
    # Invariant 9 (job-0301): STRIP the model-supplied confirmed/code_exec_id
    # BEFORE gating — the gate is server-owned, exactly like the solver gate below.
    # The prior `and not params.get("confirmed")` condition let a model that passed
    # confirmed=True SKIP the gate and self-approve code execution (those params are
    # NOT underscore-hidden from its tool schema, so it could supply them). Popping
    # makes the user-confirmation gate MANDATORY on every model-issued code_exec
    # call; only an explicit user "proceed" inside _gate_on_code_exec re-injects
    # confirmed + the minted code_exec_id. (Trusted programmatic callers/tests that
    # must bypass invoke the tool function directly, not via this server gate.)
    if tool_name == "code_exec_request":
        params.pop("confirmed", None)
        params.pop("code_exec_id", None)
        should_run, params = await _gate_on_code_exec(websocket, state, params)
        if not should_run:
            raise CodeExecConfirmationCancelledError(
                params.get("code_exec_id", "unknown")
            )

    # job-0164: centralized kwarg sweep. Gemini routinely invents kwargs that
    # don't exist on our tools (``run_name``, ``scenario_id``,
    # ``return_period_years`` when the tool accepts ``return_period_yr``, etc.).
    # ``normalize_args`` inspects ``entry.fn``'s signature and rewrites
    # bidirectional aliases (``_yr`` ↔ ``_years``, ``_hr`` ↔ ``_hours``,
    # ``durationHours`` ↔ ``duration_hours``), parses string-form forcing specs
    # (``forcing="atlas14_100yr"`` → ``return_period_years=100``), absorbs
    # silent-drop convenience kwargs, and logs+drops the rest — never raises.
    # See ``tool_arg_normalizer.py``. job-0326: run this BEFORE the solver-confirm
    # gate AND the reuse guard so both see canonicalized (_yr/_hr) param names.
    params = normalize_args(tool_name, params, entry.fn)

    # job-0326: DETERMINISTIC expensive-simulation reuse guard (NATE 2026-06-16).
    # The F54 prompt steer ("reuse the existing layer; do NOT re-run") was being
    # IGNORED by the live model, so the agent re-ran ~10-20-minute SFINCS/MODFLOW
    # solves whose output layer was ALREADY on the map. This guard is the HARD
    # backstop: before launching an expensive solver composer, it checks the
    # session's already-produced results (the per-Case loaded_layers + the
    # in-session scenario index) for a CLEAR match (same scenario family + same
    # AOI + same key params). On a clear match it SHORT-CIRCUITS — returning the
    # EXISTING layer instead of launching the solver — and tags a "reusing
    # existing result (not re-running)" note for the model. CONSERVATIVE by
    # construction: any ambiguity falls through to RUN (see scenario_reuse.py).
    # ``force_rerun``/``rerun``/``force`` truthy kwargs are the explicit-re-run
    # escape hatch (user asked to re-run) — stripped before the real dispatch.
    _reuse_note: str | None = None
    if scenario_type_for_tool(tool_name) is not None:
        _force_rerun = any(
            bool(params.get(k))
            for k in ("force_rerun", "rerun", "re_run", "force")
        )
        # These are guard-control kwargs, never real tool params — strip them so
        # the downstream tool body never sees an unexpected kwarg.
        for _k in ("force_rerun", "rerun", "re_run", "force"):
            params.pop(_k, None)
        if not _force_rerun:
            scenario_index = get_scenario_index(state.session_id)
            # Seed the index from this Case's durable loaded_layers so reuse
            # survives a reconnect / sibling connection (the in-memory index may
            # be cold while the layer persists on the Case).
            try:
                if state.emitter is not None:
                    scenario_index.seed_from_loaded_layers(
                        state.emitter.loaded_layers
                    )
            except Exception:  # noqa: BLE001 — seeding is best-effort
                logger.debug("scenario_reuse seed failed", exc_info=True)
            request_sig = scenario_signature(tool_name, params)
            case_bbox = _turn_case_bbox(state)
            reuse = scenario_index.find_reuse(request_sig, case_bbox=case_bbox)
            if reuse is not None:
                logger.info(
                    "scenario_reuse[%s]: SHORT-CIRCUIT %s -> reusing layer_id=%s "
                    "(not re-running solver)",
                    state.session_id, tool_name, reuse.layer_id,
                )
                _reuse_note = (
                    f"Reusing the existing {reuse.scenario_type} result already "
                    f"on the map (layer '{reuse.name}', handle={reuse.layer_id}) "
                    "for this AOI and parameters — the simulation was NOT re-run. "
                    "Narrate from this existing layer; do not launch the solver "
                    "again unless the user changes the area or parameters or "
                    "explicitly asks to re-run."
                )
                _reused_layer = LayerURI(
                    layer_id=reuse.layer_id,
                    name=reuse.name,
                    layer_type=reuse.layer_type,  # type: ignore[arg-type]
                    uri=reuse.uri,
                    style_preset="",
                    bbox=reuse.bbox,
                )
                # Replace the dispatch with a synchronous return of the existing
                # layer so the SAME emission / card / persistence machinery
                # (emit_tool_call's LayerURI gate) fires with the reused layer.
                entry = _ReuseEntry(entry.metadata, _reused_layer)

    # Confirmation-before-consequence for solver composers (job-0241,
    # Invariant 9 / FR-AS-8). The LLM-supplied ``confirmed`` is STRIPPED first
    # — the gate is server-owned; only an explicit user "proceed" injects it.
    # job-0326: SKIPPED on a reuse short-circuit (``_ReuseEntry``) — there is no
    # solver to confirm; we are handing back an already-produced layer.
    if tool_name in SOLVER_CONFIRM_TOOLS and not isinstance(entry, _ReuseEntry):
        params.pop("confirmed", None)
        should_run, params = await _gate_on_solver_confirm(
            websocket, state, tool_name, params
        )
        if not should_run:
            raise SolverConfirmationCancelledError(tool_name)

    # job-0263: layer-handle indirection — kill the LLM-URI-mangling class
    # (5 live incidents: invented cache paths, WMS-URL-as-hazard, hash-tail
    # hallucination x3, NSI layer_id-as-basename, runs/ prefix mangle).
    # Every URI-consuming param resolves through the session-scoped registry:
    # known handle → registered URI; exact known URI → pass; close mangle →
    # substitute + WARNING; unknown managed-bucket path → typed retryable
    # URI_HANDLE_UNRESOLVED listing the real handles so Gemini self-corrects
    # without inventing. See uri_registry.py.
    uri_registry = get_uri_registry(state.session_id)
    params = uri_registry.resolve_params(tool_name, params)

    # job VAULT-READ: thread the user's per-Case ``secret_ref`` into a keyed
    # tool so its ``_resolve_*_key`` reads the VAULT key first (then env). This
    # mirrors the eBird secret_ref convention. No-op for non-keyed tools and
    # when no active secret exists (the tool falls back to env / typed
    # auth-error, which the credential-request flow below acts on).
    params = await _inject_secret_ref(state, tool_name, params, turn_case_id)

    state.current_pipeline_id = state.emitter.start_pipeline()
    state.current_turn_pipeline_id = state.current_pipeline_id
    # job-0263: bind the registry as the ambient observation sink for the
    # lifetime of the invoke so composer-internal publishes (publish_layer
    # called inside run_model_flood_scenario) register the gs:// COG ↔ WMS
    # association even though the composer's envelope only carries the WMS URL.
    _uri_reg_token = activate_registry(uri_registry)
    # job-0267: tool-card persistence bookkeeping. ``_card_state`` stays None
    # on cancellation (Invariant 8 — no replayable outcome); the wall-clock
    # pair is only the FALLBACK timing — ``_persist_tool_card`` prefers the
    # emitter's authoritative job-0264 ``last_tool_step`` stamps.
    _card_state: str | None = None
    _card_started_at = now_utc()
    _card_t0 = asyncio.get_running_loop().time()
    try:
        # job VAULT-READ: dispatch with a credential-request retry. The first
        # attempt runs the tool; if it raises a missing/invalid-credential
        # error for a keyed provider (e.g. FIRMS_AUTH_ERROR) we PAUSE, emit a
        # ``credential-request`` envelope, and await the user's
        # ``credential-provided`` reply. On provided=True we re-resolve the
        # (now-saved) vault key and retry the tool ONCE. The guard is one
        # prompt per tool per turn (``credential_prompted_tools``) so a
        # still-bad key fails through the normal typed-error surface instead of
        # re-prompting forever.
        try:
            result = await state.emitter.emit_tool_call(
                name=entry.metadata.name,
                tool_name=tool_name,
                invoke=lambda: entry.fn(**params),
            )
        except (asyncio.CancelledError, GeneratorExit):
            raise
        except BaseException as exc:  # noqa: BLE001 — classify below
            retry_params = await _maybe_handle_credential_error(
                websocket, state, tool_name, params, exc, turn_case_id
            )
            if retry_params is None:
                raise
            # Key provided + vault re-resolved: retry the tool ONCE.
            params = retry_params
            result = await state.emitter.emit_tool_call(
                name=entry.metadata.name,
                tool_name=tool_name,
                invoke=lambda: entry.fn(**params),
            )
        _card_state = "complete"
    except asyncio.CancelledError:
        raise
    except BaseException:
        _card_state = "failed"
        raise
    finally:
        deactivate_registry(_uri_reg_token)
        state.emitter.close_pipeline()
        state.current_pipeline_id = None
        # job-0267: persist the replayable tool-card row so a Case reopen
        # re-renders the inline tool card (user-verified loss: only user
        # messages survived). Fires for complete AND failed terminal states,
        # BEFORE the narration row that closes the turn — the chat
        # collection's ``created_at`` order IS the replay order. Best-effort,
        # never raises, never masks the original exception.
        if _card_state is not None and turn_case_id:
            await _persist_tool_card(
                state,
                tool_name=tool_name,
                label=entry.metadata.name,
                card_state=_card_state,
                started_at_fallback=_card_started_at,
                duration_ms_fallback=int(
                    (asyncio.get_running_loop().time() - _card_t0) * 1000.0
                ),
                case_id=turn_case_id,
            )
        # job-0259: persist the Case layer accumulator in the FINALLY block —
        # the round-3 plume evidence showed a published layer vanishing from
        # the reopened Case because the post-invoke ``session-state`` emission
        # raised on a dying WebSocket, which skipped a persist placed after
        # the try-block. ``add_loaded_layer`` appends to ``_loaded_layers``
        # BEFORE it emits, so persisting here captures the layer even when
        # the wire write failed. Never raises (and never masks the original
        # exception) — persistence is a side-effect, not the happy path.
        if turn_case_id and state.emitter is not None:
            try:
                await _persist_case_loaded_layers(state, case_id=turn_case_id)
            except Exception:  # noqa: BLE001 — best-effort, never mask
                logger.exception(
                    "case-layer-persist (finally) failed case=%s",
                    turn_case_id,
                )

    # job-0263: register every URI the result carries (LayerURI layer_id↔uri
    # pairs + bare gs:// strings) so the NEXT tool call can resolve handles /
    # detect mangles. Best-effort — registration never breaks the dispatch.
    uri_registry.register_tool_result(tool_name, result)

    # job-0326: record a FRESHLY-PRODUCED expensive-scenario result into the
    # session reuse index so a later identical request short-circuits instead of
    # re-running the solver. Skip when this dispatch WAS the short-circuit (the
    # _ReuseEntry path) — the layer is already indexed. Only index a real
    # success (a LayerURI return), never a failure dict. Best-effort.
    if (
        not isinstance(entry, _ReuseEntry)
        and scenario_type_for_tool(tool_name) is not None
        and isinstance(result, LayerURI)
    ):
        try:
            get_scenario_index(state.session_id).record_result(
                scenario_signature(tool_name, params),
                layer_id=result.layer_id,
                name=result.name,
                layer_type=result.layer_type,
                uri=result.uri,
                bbox=result.bbox,
            )
        except Exception:  # noqa: BLE001 — indexing must never break dispatch
            logger.debug("scenario_reuse record failed", exc_info=True)

    # job-0326: when this dispatch was a reuse short-circuit, the emitter has
    # ALREADY re-loaded the existing layer onto the map (the emit_tool_call
    # LayerURI gate fired with the reused LayerURI). What's left is to give
    # Gemini an UNAMBIGUOUS function_response that says "this is the EXISTING
    # result, the simulation was NOT re-run" so it narrates honestly and does not
    # try again. Return a compact dict (summarize_tool_result handles dicts) that
    # carries both the reuse flag/note and the reused layer's identity. This
    # REPLACES the bare LayerURI return on the short-circuit path; the map update
    # already happened, so nothing renderable is lost.
    if _reuse_note is not None and isinstance(result, LayerURI):
        logger.info("scenario_reuse note=%s", _reuse_note)
        return {
            "status": "reused_existing",
            "reused": True,
            "note": _reuse_note,
            "layer_id": result.layer_id,
            "name": result.name,
            "layer_type": result.layer_type,
            "uri": result.uri,
            "handle": result.layer_id,
        }

    # Track layer emissions on the active turn so the next ``CaseChatMessage``
    # write captures them. ``publish_layer`` returns a WMS URL string; we use
    # the tool's ``layer_id`` parameter as the canonical layer identifier.
    if tool_name == "publish_layer" and "layer_id" in params:
        lid = params.get("layer_id")
        if isinstance(lid, str) and lid:
            state.current_turn_layer_ids.append(lid)
            # job-0272: the MISSING LINK between an atomic publish and the
            # map. ``emit_tool_call`` only feeds ``add_loaded_layer`` (and
            # thus the ``session-state`` envelope the web renders WMS layers
            # from) when a tool RETURNS a typed LayerURI — composers do, but
            # the atomic ``publish_layer`` returns a bare WMS string, so an
            # LLM-driven fetch→compute→publish chain published server-side
            # while the map stayed empty (live x3: hillshade Wave 4.8,
            # Seattle + Boulder reliefs 2026-06-10). Wrap the WMS URL in a
            # LayerURI here so the existing emission/persistence machinery
            # announces the layer exactly as composer layers are announced.
            if isinstance(result, str) and result.startswith("http"):
                try:
                    # job-0254: route through the single emission seam. The WMS
                    # URL here is always http(s) (guarded above), so the seam
                    # passes it through; the seam exists so this site can never
                    # regress into emitting a renderable raw gs:// raster.
                    _emit_layer = emit_layer_uri(
                        LayerURI(
                            layer_id=lid,
                            name=lid,
                            layer_type="raster",
                            uri=result,
                            style_preset=params.get("style_preset") or "",
                        )
                    )
                    if _emit_layer is not None:
                        await state.emitter.add_loaded_layer(_emit_layer)
                        # sprint-14-aws (job-0290c): re-persist AFTER this add.
                        # The dispatch's finally-persist above ran BEFORE this
                        # wrap-site emission, so the published tile layer only
                        # lived in memory — a Case switch + reopen rehydrated
                        # WITHOUT it (observed live: flood Case kept its layer
                        # because composers add inside the dispatch; hillshade
                        # chains lost theirs because publish_layer is the LAST
                        # tool call and nothing persisted afterwards).
                        if turn_case_id:
                            await _persist_case_loaded_layers(
                                state, case_id=turn_case_id
                            )
                except Exception:  # noqa: BLE001 — emission is best-effort
                    logger.exception(
                        "publish_layer loaded-layer emission failed "
                        "layer_id=%s",
                        lid,
                    )

    # job-0172 Part B / job-0259: per-Case layer persistence now happens in
    # the ``finally`` block above so it ALSO fires when the tool (or its
    # post-invoke envelope emission on a dying WebSocket) raised — the
    # emitter's accumulator already contains the layer at that point.

    # job-0101: Mode 2 .gov/.edu classifier — when web_fetch returns a dict
    # that looks like a structured-data candidate, emit a `mode2-candidate`
    # envelope and append an audit-log line. Deterministic side-effect; the
    # web modal (Wave 2/3) renders the offer. See mode2_classifier.py.
    if tool_name == "web_fetch" and isinstance(result, dict):
        await _maybe_emit_mode2_candidate(websocket, state, result)
    return result


async def _persist_case_loaded_layers(
    state: SessionState, *, case_id: str | None = None
) -> None:
    """Sync the emitter's ``_loaded_layers`` onto the turn's ``CaseSummary``.

    job-0172 Part B: writes the current ``ProjectLayerSummary[]`` accumulator
    into ``Case.loaded_layer_summaries`` (full dicts for rehydration) and
    keeps ``Case.layer_summary`` (the lightweight ``layer_id[]`` projection)
    in lockstep. Idempotent and dedup-by-uri because the emitter already
    dedups upstream; the persisted shape mirrors the in-memory shape.

    Best-effort: a Persistence failure is logged but never raised. The
    Case lookup gates the write — if the Case was archived / deleted
    mid-turn we silently skip (no surprise resurrection of a tombstoned
    Case via this side-channel).

    job-0268: ``case_id`` pins the target Case explicitly (callers inside a
    tool dispatch pass their entry-time capture); default resolves via
    ``_turn_case_id`` so a mid-turn Case switch never re-aims attribution.
    """
    target_case = case_id if case_id is not None else _turn_case_id(state)
    p = get_persistence()
    if p is None or state.emitter is None or not target_case:
        return
    try:
        case = await p.get_case(target_case)
    except Exception:  # noqa: BLE001
        logger.exception(
            "case-layer-persist: get_case failed case=%s",
            target_case,
        )
        return
    if case is None:
        logger.debug(
            "case-layer-persist: case=%s missing; skipping",
            target_case,
        )
        return

    loaded = state.emitter.loaded_layers  # defensive copy from the emitter
    emitter_dicts: list[dict] = [layer.model_dump(mode="json") for layer in loaded]

    # job-0259: MERGE (append + replace-by-layer_id) instead of wholesale
    # replace. An emitter that was never seeded with the Case's persisted
    # layers (fresh connection, sync failure, sibling-socket dispatch) must
    # never CLOBBER previously persisted summaries down to its own partial
    # view — union them, with the emitter's fresher entry winning on a
    # layer_id collision. There is no server-side layer-remove flow at v0.1,
    # so union semantics lose nothing.
    merged: list[dict] = [
        dict(d) for d in case.loaded_layer_summaries if isinstance(d, dict)
    ]
    index_by_layer_id = {
        d.get("layer_id"): i for i, d in enumerate(merged) if d.get("layer_id")
    }
    for d in emitter_dicts:
        lid = d.get("layer_id")
        pos = index_by_layer_id.get(lid)
        if pos is None:
            index_by_layer_id[lid] = len(merged)
            merged.append(d)
        else:
            merged[pos] = d
    layer_ids: list[str] = [
        d.get("layer_id") for d in merged if isinstance(d.get("layer_id"), str)
    ]

    # If nothing has changed, skip the round-trip.
    if (
        case.loaded_layer_summaries == merged
        and case.layer_summary == layer_ids
    ):
        return

    updated = case.model_copy(
        update={
            "loaded_layer_summaries": merged,
            "layer_summary": layer_ids,
            "updated_at": now_utc(),
        }
    )
    try:
        await p.upsert_case(updated)
        logger.debug(
            "case-layer-persist case=%s layers=%d",
            target_case,
            len(layer_ids),
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "case-layer-persist: upsert failed case=%s",
            target_case,
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

        # job-0268: charts are turn-scoped emissions — key them by the Case
        # that OWNS the turn, not whatever Case is visible at write time.
        doc_id = _turn_case_id(state) or state.session_id
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
    attempt a best-effort persist of whatever the narration accumulator
    captured before the stream died.

    job-0267 (full-stream persistence): the persisted ``content`` is now the
    REAL accumulated narration — ``_stream_gemini_reply`` resets
    ``state.current_turn_narration`` at stream start and appends every
    ``TextDeltaEvent`` delta across all loop iterations. Pre-fix this wrote
    ``content=""`` markers, which the web replay (rightly) rendered as
    nothing — user-verified: only their own messages survived a Case reopen.
    """
    # job-0268: capture the turn's Case at task entry — the finally-persist
    # below must land in the Case that OWNED this turn even when the user
    # switched Cases (or a newer turn re-pinned the binding) mid-stream.
    turn_case_id = _turn_case_id(state)
    # job-0277: bind the owning Case into the per-task ContextVar so EVERY
    # envelope this turn emits (chunks, pipeline-state, session-state, …)
    # carries Envelope.case_id and the web routes it to the right stream.
    bind_turn_case(turn_case_id)
    # job-0269: per-turn object capture. A concurrent turn (or a case
    # switch) re-points both SessionState fields mid-stream — this wrapper
    # must gauge completion against THIS turn's history list, and join the
    # narration list THIS turn's stream registered under the running task
    # (mocked streams in tests don't register; the field fallback preserves
    # their job-0267 contract).
    turn_history = state.chat_history
    pre_chat_len = len(turn_history)
    try:
        await _stream_gemini_reply(
            websocket, state, settings, user_text, research_mode
        )
    finally:
        # job-0267 / job-0315: close out the turn's narration persistence.
        # With job-0315 each FINALIZED narration segment is already persisted
        # in-loop by ``_finalize_segment`` (interleaved with the mid-turn tool
        # rows). This wrapper must therefore NOT re-persist finalized segments —
        # it only owns the un-finalized remainder + the legacy fallbacks:
        #
        #   * ``open_tail``     — text in a segment the stream NEVER finalized
        #                         (crash/cancel mid-segment). Persist it as ONE
        #                         agent row so no narration is lost; it is the
        #                         de-facto terminal row, so layer_emissions=None
        #                         lets it carry the layer/zoom accumulator.
        #   * ``segments_done`` — count of finalized agent rows this turn. When
        #                         it is 0 AND the stream completed cleanly with
        #                         no open tail, write the legacy single marker
        #                         row (content == joined narration, possibly "")
        #                         — preserving the narration-LESS completed-turn
        #                         row count and the pre-fix one-row contract.
        #
        # All three per-task registries are popped (mocked-stream tests that
        # never registered fall back to the live field, preserving job-0267).
        _own_task = asyncio.current_task()
        if _own_task is not None:
            turn_narration = _TURN_NARRATION_BY_TASK.pop(_own_task, None)
            open_segment = _TURN_OPEN_SEGMENT_BY_TASK.pop(_own_task, None)
            segments_done = _TURN_SEGMENTS_PERSISTED_BY_TASK.pop(_own_task, 0)
            terminal_acc_persisted = _TURN_TERMINAL_ACC_PERSISTED_BY_TASK.pop(
                _own_task, False
            )
        else:
            turn_narration = None
            open_segment = None
            segments_done = 0
            terminal_acc_persisted = False
        if turn_narration is None:
            turn_narration = state.current_turn_narration
        narration = "".join(turn_narration).strip()
        open_tail = "".join(open_segment or []).strip()
        stream_completed = len(turn_history) > pre_chat_len
        if turn_case_id:
            if open_tail:
                # Crash/cancel left an un-finalized open segment carrying text
                # (its done=True never fired). Persist the tail so the partial
                # narration survives; as the de-facto terminal row it also
                # captures the turn's layer/zoom accumulator (layer_emissions
                # default None). No double-persist: finalized segments already
                # cleared their buffer, so this is ONLY the un-finalized text.
                await _persist_chat_turn(
                    state,
                    role="agent",
                    content=open_tail,
                    pipeline_id=state.current_turn_pipeline_id,
                    case_id=turn_case_id,
                )
            elif segments_done == 0 and (narration or stream_completed):
                # No segment was finalized AND no open tail: either a clean
                # narration-LESS completed turn (content="" marker — replay row
                # count unchanged from pre-fix), or a mocked-stream test that
                # populated only ``current_turn_narration`` (legacy one-row
                # contract). Mirror the pre-job-0315 single-row write exactly.
                await _persist_chat_turn(
                    state,
                    role="agent",
                    content=narration,
                    pipeline_id=state.current_turn_pipeline_id,
                    case_id=turn_case_id,
                )
            elif (
                not terminal_acc_persisted
                and (state.current_turn_map_commands or state.current_turn_layer_ids)
            ):
                # job-0315 contract fix: segments_done > 0 (interleaved rows
                # already persisted) and no open tail, BUT the turn's FINAL
                # generation round ended in tool calls with NO trailing
                # narration (the COMMON flood/publish turn shape — e.g. the
                # last call is publish_layer, then the stream ends). In that
                # shape the in-loop terminal finalize never fired
                # (``current_message_id is None`` at turn close, so
                # ``terminal_acc_persisted`` stayed False), and NONE of the
                # persisted segment rows carried the turn's zoom-to/layer
                # accumulator (each non-terminal segment passed
                # ``layer_emissions=[]``). Pre-job-0315 the single closing
                # role="agent" row carried ``layers=[...]`` + the zoom-to;
                # without this row the web ``extractLastZoomTo(chat_history)``
                # (case_zoom.ts) finds no zoom-to and a Case reopen does NOT
                # snap the camera to the AOI — regressing job-0259 (layer
                # attribution) + job-0280/0281 (zoom-snap). Restore the
                # invariant: EVERY turn that emitted a zoom-to/layer must
                # persist at least one chat row carrying it. We write an EMPTY
                # marker row (content="" — the web renders no phantom bubble
                # for blank agent text, exactly like the narration-LESS
                # completed-turn marker) with ``layer_emissions=None`` so
                # ``_persist_chat_turn`` SNAPSHOTS ``current_turn_layer_ids``
                # into ``layer_emissions`` and ``current_turn_map_commands``
                # into ``map_command_emissions``. ``terminal_acc_persisted``
                # guards against a double-write when the turn DID end in
                # narration (the terminal segment already carried it); the
                # NON-EMPTY accumulator guard means an accumulator-less +
                # text-less tool-terminal turn writes NOTHING (no phantom
                # empty bubble).
                await _persist_chat_turn(
                    state,
                    role="agent",
                    content="",
                    pipeline_id=state.current_turn_pipeline_id,
                    case_id=turn_case_id,
                )
            # else: either the terminal segment already snapshotted the
            # accumulator (segments_done > 0 ending in narration), or the turn
            # emitted no zoom-to/layer accumulator at all -> every narration run
            # was already persisted as its own interleaved row. Nothing to add.


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
    # job-0268: entry-time Case capture — see _dispatch_gemini_and_persist.
    turn_case_id = _turn_case_id(state)
    bind_turn_case(turn_case_id)  # job-0277: envelope tagging
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
        if turn_case_id:
            await _persist_chat_turn(
                state,
                role="agent",
                content=f"[invoked {tool_name}]",
                pipeline_id=state.current_turn_pipeline_id,
                case_id=turn_case_id,
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


async def _delete_case_loaded_layer(
    state: SessionState, layer_id: str, *, case_id: str | None = None
) -> None:
    """Persist a layer deletion AUTHORITATIVELY (replace, not union).

    job-0325 (F53): the in-memory emitter has already dropped ``layer_id``
    from its ``_loaded_layers``; this mirrors that onto the persisted
    ``CaseSummary`` so the layer cannot RESURRECT on the next turn or on a
    Case reopen.

    Deliberately bypasses ``_persist_case_loaded_layers`` — that path UNIONs
    the emitter view with ``case.loaded_layer_summaries`` (so a partial
    emitter never clobbers the persisted set), which would re-add the deleted
    layer from the persisted list. Here we want the opposite: REMOVE the
    layer_id from both ``loaded_layer_summaries`` (full dicts) and
    ``layer_summary`` (the layer_id[] projection) and write the result.

    Best-effort: a Persistence failure is logged but never raised. The Case
    lookup gates the write — a missing / tombstoned Case is silently skipped.

    ``case_id`` pins the target Case explicitly; default resolves via
    ``_turn_case_id`` (never the raw live ``active_case_id``).
    """
    target_case = case_id if case_id is not None else _turn_case_id(state)
    p = get_persistence()
    if p is None or not target_case:
        return
    try:
        case = await p.get_case(target_case)
    except Exception:  # noqa: BLE001
        logger.exception(
            "layer-delete-persist: get_case failed case=%s", target_case
        )
        return
    if case is None:
        logger.debug(
            "layer-delete-persist: case=%s missing; skipping", target_case
        )
        return

    surviving_summaries: list[dict] = [
        dict(d)
        for d in case.loaded_layer_summaries
        if isinstance(d, dict) and d.get("layer_id") != layer_id
    ]
    surviving_ids: list[str] = [
        d.get("layer_id")
        for d in surviving_summaries
        if isinstance(d.get("layer_id"), str)
    ]

    # Nothing referenced this layer_id in the persisted set — no write needed.
    if (
        case.loaded_layer_summaries == surviving_summaries
        and case.layer_summary == surviving_ids
    ):
        return

    updated = case.model_copy(
        update={
            "loaded_layer_summaries": surviving_summaries,
            "layer_summary": surviving_ids,
            "updated_at": now_utc(),
        }
    )
    try:
        await p.upsert_case(updated)
        logger.debug(
            "layer-delete-persist case=%s layer=%s remaining=%d",
            target_case,
            layer_id,
            len(surviving_ids),
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "layer-delete-persist: upsert failed case=%s layer=%s",
            target_case,
            layer_id,
        )


async def _handle_layer_delete(
    websocket: ServerConnection,
    state: SessionState,
    payload_dict: Any,
) -> None:
    """Process a ``layer-delete`` envelope (job-0325 F53).

    Removes ``layer_id`` from the live emitter's ``loaded_layers``, emits a
    refreshed ``session-state`` (Map.tsx replace-not-reconcile then drops the
    overlay — no Map.tsx change), and persists the post-deletion list
    authoritatively. The deletion propagates to the agent's loaded-layers
    awareness because the layer is now absent from BOTH the emitter's
    in-memory ``_loaded_layers`` (the mid-session ``build_layers_present_note``
    source) and the persisted ``loaded_layer_summaries`` (the Case-reopen
    note source).

    The payload is loosely-shaped ``{layer_id: str}`` (read inline for
    forward-compat). A malformed / empty ``layer_id`` surfaces a typed
    ``TOOL_PARAMS_INVALID`` error.
    """
    layer_id: str | None = None
    if isinstance(payload_dict, dict):
        lid = payload_dict.get("layer_id")
        if isinstance(lid, str) and lid:
            layer_id = lid
    if not layer_id:
        await _send_error(
            websocket,
            state.session_id,
            "TOOL_PARAMS_INVALID",
            "layer-delete requires a non-empty string layer_id.",
        )
        return

    # Pin the target Case the same way every persistence site does so a
    # mid-turn Case switch never mis-aims the delete.
    target_case = _turn_case_id(state)

    _ensure_emitter(websocket, state)
    if state.emitter is None:  # pragma: no cover — _ensure_emitter always binds
        return

    # Drop the layer from the live accumulator. reset_loaded_layers also
    # prunes the inline-GeoJSON side-table to the surviving ids (job-0175).
    survivors: list[dict] = [
        layer.model_dump(mode="json")
        for layer in state.emitter.loaded_layers
        if layer.layer_id != layer_id
    ]
    state.emitter.reset_loaded_layers(survivors)

    # Emit the refreshed session-state. Map.tsx removes the now-absent layer
    # from MapLibre via replace-not-reconcile (Appendix A.7). session-state is
    # session-scoped fan-out on the client, so every connection of this
    # session converges on the new loaded_layers list.
    await state.emitter.emit_session_state()

    # Persist authoritatively (replace, not the union merge — see helper).
    await _delete_case_loaded_layer(state, layer_id, case_id=target_case)

    logger.info(
        "layer-delete session=%s case=%s layer=%s survivors=%d",
        state.session_id,
        target_case,
        layer_id,
        len(survivors),
    )


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
                        ok = await _handle_auth_token(
                            websocket, state, payload_dict
                        )
                        # job-0252 AUTH_REQUIRED gate: a rejected handshake
                        # has already closed the socket; stop the loop.
                        if not ok:
                            return
                        continue
                    # Implicit anonymous fallback when any other envelope
                    # arrives before the handshake — keeps the legacy
                    # no-auth-token clients working. Under the AUTH_REQUIRED
                    # gate this REJECTS instead (job-0252).
                    if not state.auth_handshake_complete:
                        ok = await _ensure_auth_handshake(websocket, state)
                        if not ok:
                            return

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
                        # job-0121: reset per-turn layer accumulator before
                        # the dispatch so the CaseChatMessage write captures
                        # only this turn's emissions. (job-0269 KNOWN LIMIT:
                        # these two slots are still session-shared — a turn
                        # running concurrently in ANOTHER Case may interleave
                        # layer-id/pipeline-id attribution on the closing
                        # agent row. Case targeting itself is safe via the
                        # job-0268 pin; full per-turn context is 13.5 scope.)
                        state.current_turn_layer_ids = []
                        state.current_turn_pipeline_id = None
                        state.current_turn_map_commands = []
                        # job-0259 + job-0121 + job-0262 pre-dispatch
                        # sequence (see ``_prepare_user_turn``): sibling-
                        # connection Case sync, AUTO-CREATE Case for a
                        # non-directive prompt from the Cases root (named
                        # via _derive_case_title; case-open + case-list
                        # emitted so the UI flips into the Case view), and
                        # the user-turn chat persist — all BEFORE the turn
                        # task starts so chat + layer attribution land on
                        # the right (possibly brand-new) Case. Returns the
                        # parsed ``/invoke`` directive for the M4
                        # live-evidence path; None streams through Gemini.
                        directive = await _prepare_user_turn(
                            websocket, state, um.text
                        )
                        # job-0269: stream-scoped cancellation replaces the
                        # M1 "cancel anything running" policy. Only a
                        # re-prompt in the SAME stream (Case, or root)
                        # replaces that stream's in-flight turn; turns in
                        # other Cases keep running (live 2026-06-10: a root
                        # terrain prompt cancelled a cloud SFINCS solve).
                        # The key comes from the job-0268 turn pin set by
                        # _prepare_user_turn (auto-created Cases get a fresh
                        # ULID, so they never collide with a running turn).
                        turn_key = (
                            state.current_turn_case_id or _ROOT_STREAM_KEY
                        )
                        prior = state.inflight_tasks.get(turn_key)
                        if prior is not None and not prior.done():
                            prior.cancel()
                        for _done_key in [
                            k
                            for k, t in state.inflight_tasks.items()
                            if t.done()
                        ]:
                            state.inflight_tasks.pop(_done_key, None)
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
                        state.inflight_tasks[turn_key] = task

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

                    elif msg_type == "layer-delete":
                        # job-0325 (F53): per-layer delete. The client sends
                        # ``{layer_id}``; we drop it from the live emitter's
                        # loaded_layers, emit a fresh session-state (Map.tsx
                        # replace-not-reconcile removes the overlay), and
                        # persist the post-deletion list AUTHORITATIVELY
                        # (replace, NOT the union of _persist_case_loaded_layers
                        # which would resurrect it). The deletion also leaves
                        # the agent's loaded-layers awareness — both the
                        # emitter's _loaded_layers (mid-session note source) and
                        # the persisted loaded_layer_summaries (reopen note
                        # source) — so build_layers_present_note stops listing
                        # it. payload is loosely-shaped; read inline.
                        await _handle_layer_delete(
                            websocket, state, payload_dict
                        )

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
                        # job-0269: target the VISIBLE stream's turn (the
                        # stop button lives in the active Case's composer);
                        # fall back to any live turn so the pre-0269
                        # "cancel cancels the run" contract still holds
                        # when the binding moved.
                        cancel_key = (
                            state.active_case_id or _ROOT_STREAM_KEY
                        )
                        cancel_task = state.inflight_tasks.get(cancel_key)
                        if cancel_task is None or cancel_task.done():
                            live = [
                                t
                                for t in state.inflight_tasks.values()
                                if not t.done()
                            ]
                            cancel_task = live[-1] if live else None
                        if cancel_task is not None and not cancel_task.done():
                            cancel_task.cancel()
                            # Wait briefly so the cancel completes deterministically
                            # within NFR-R-3 (30s budget). The pipeline-state
                            # cancelled frame is emitted from inside the task's
                            # CancelledError branch.
                            try:
                                await asyncio.wait_for(cancel_task, timeout=5.0)
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

                    elif msg_type == "credential-provided":
                        # job VAULT-READ: the user saved (or declined) a key the
                        # agent asked for via ``credential-request``. Resolve the
                        # paused dispatch coroutine's future so it retries the
                        # tool (provided=True) or re-raises the original typed
                        # error (provided=False). The ``secret-add`` that saved
                        # the key already ran on its own envelope path — this
                        # carries NO key material (Decision F).
                        try:
                            cp = (
                                CredentialProvidedEnvelopePayload.model_validate(
                                    payload_dict
                                )
                            )
                        except ValidationError as ve:
                            await _send_error(
                                websocket,
                                state.session_id,
                                "TOOL_PARAMS_INVALID",
                                f"credential-provided invalid: {ve.errors()[0]['msg']}",
                            )
                            continue
                        if not _resolve_pending_credential(state.session_id, cp):
                            logger.warning(
                                "credential-provided for unknown/closed "
                                "request_id=%s session=%s",
                                cp.request_id,
                                state.session_id,
                            )
                            continue
                        logger.info(
                            "credential-provided accepted session=%s "
                            "request_id=%s provided=%s",
                            state.session_id,
                            cp.request_id,
                            cp.provided,
                        )

                    elif msg_type == "region-choice-provided":
                        # region-disambiguation picker: the user narrowed the
                        # state-bbox-fallback geocode to a sub-region (or kept
                        # the whole state). Resolve the paused dispatch
                        # coroutine's future so it applies the picked bbox (or
                        # keeps the state bbox). Mirrors credential-provided —
                        # may arrive on a sibling connection of the session.
                        try:
                            rc = (
                                RegionChoiceProvidedEnvelopePayload.model_validate(
                                    payload_dict
                                )
                            )
                        except ValidationError as ve:
                            await _send_error(
                                websocket,
                                state.session_id,
                                "TOOL_PARAMS_INVALID",
                                f"region-choice-provided invalid: {ve.errors()[0]['msg']}",
                            )
                            continue
                        if not _resolve_pending_region_choice(
                            state.session_id, rc
                        ):
                            logger.warning(
                                "region-choice-provided for unknown/closed "
                                "request_id=%s session=%s",
                                rc.request_id,
                                state.session_id,
                            )
                            continue
                        logger.info(
                            "region-choice-provided accepted session=%s "
                            "request_id=%s choice=%s",
                            state.session_id,
                            rc.request_id,
                            rc.choice,
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
            if state:
                for _t in state.inflight_tasks.values():
                    if not _t.done():
                        _t.cancel()

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
    # job-0275: bind host override so the dev agent is reachable from the
    # LAN / tailnet (phone demos). Default stays loopback-only; opt in via
    # GRACE2_AGENT_HOST=0.0.0.0. The real public surface is sprint-13.5.
    host = os.environ.get("GRACE2_AGENT_HOST", host)
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
    # job-0252 (sprint-13.5, OQ-0115-CASE-USER-LINK): one-time idempotent
    # migration — stamp every pre-Auth Case (no ``user_id``) with the
    # MIGRATION_ANON_UID sentinel so those Cases belong to one synthetic
    # owner instead of leaking to every signed-in user. Idempotent: a second
    # run matches nothing. Best-effort: a migration hiccup must not abort
    # server startup (the same posture as the Persistence init above).
    await _run_preauth_case_migration()
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
    # job-0268: turn-start Case binding (cross-Case contamination fix).
    "_turn_case_id",
    "_dispatch_tool_and_persist",
    "_dispatch_gemini_and_persist",
    # job-0262: auto-create Case from the Cases root.
    "_auto_create_case_from_root",
    "_emit_auto_case_open",
    "_prepare_user_turn",
    # job-0124: secrets envelope handlers.
    "_emit_secrets_list",
    "_handle_secret_add",
    "_handle_secret_revoke",
    # job VAULT-READ: credential pipeline (secret_ref injection + JIT prompt).
    "_inject_secret_ref",
    "_resolve_active_secret_ref",
    "_maybe_handle_credential_error",
    "_emit_credential_request_and_wait",
    "_build_credential_request_payload",
    "_resolve_pending_credential",
    # job-B8+B9 (Wave 4.10 Stage 3): circuit breaker + loop_exhausted.
    "_send_loop_exhausted",
    "CircuitBreakerError",
    "ToolCircuitBreaker",
]
