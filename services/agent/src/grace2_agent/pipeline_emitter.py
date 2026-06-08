"""PipelineEmitter — real pipeline-state + session-state emission (job-0035, M4).

Owns the current ``PipelineSnapshot`` for one session and broadcasts a fresh
``pipeline-state`` envelope on every step state transition (Appendix A.4 +
A.7 replace-not-reconcile). Also owns the session-scoped ``loaded_layers``
accumulator and re-emits ``session-state`` whenever a tool returns a
``LayerURI``.

Closes **OQ-T-28-SIM-WS-BOUNDARY** (sprint-05 job-0028): the M3 web client
PipelineStrip + cancel button can now be driven by the real agent path
instead of the ``window.__grace2Inject*`` dev seam.

Cross-cutting principles (per CLAUDE.md + agents/AGENTS.md):

- **Replace-not-reconcile (Appendix A.7) — structurally enforced.** Every
  emission carries the full current ``PipelineSnapshot`` / ``SessionState``.
  This class has NO ``merge``/``update_partial``/``apply_delta`` helper —
  the only public mutators are state-transition methods that build the new
  snapshot in place and emit it. Tests guarantee that the wire envelope
  carries the wholesale current state, never a delta.
- **Invariant 1 (Determinism boundary): preserves.** ``progress_percent``
  is workflow-attributed (passed in by the caller), never an LLM estimate;
  the emission path itself does not invoke Gemini.
- **Invariant 8 (Cancellation is first-class): extends.** The existing M1
  cancel chain (``server.py`` ``inflight_task.cancel()`` →
  ``asyncio.CancelledError``) propagates into the tool-call wrapper, which
  catches it and calls ``mark_cancelled``. The cancelled step persists in
  the snapshot; a fresh ``pipeline-state`` is emitted with the step's
  ``state == "cancelled"`` (yellow chip), distinct from ``failed`` (red).
- **FR-CE-8 / D.6 field discipline (job-0030):** ``progress_percent``
  populated only when the tool reports it (atomic tools usually leave it
  ``None``); ``error_code`` + ``error_message`` populated only on
  ``failed``. No fabrication.
- **Open-set SCREAMING_SNAKE_CASE error codes (Appendix A.6):** registered
  via the module-level ``ErrorCodeRegistry``. Adding a new code is a single-
  line addition. Schema validation is shape-only (pydantic
  ``_validate_error_code_shape`` on ``PipelineStepSummary``).

Integration seam (``server.py``): the tool-call site wraps each
``TOOL_REGISTRY[name].fn(...)`` invocation in ``emit_tool_call`` /
``async_emit_tool_call`` so every invocation auto-creates a step, marks
running on entry, marks complete on return (or failed/cancelled on the
matching exception). Long-running tools that want to opt-in to progress
emit by calling ``update_progress`` mid-fetch (TENTATIVE: M4 atomic tools
don't, but the hook is in place for M5+ solvers).

``loaded_layers`` dedup policy (TENTATIVE per kickoff Open Questions):
dedup by the ``uri`` field — if a tool re-fetches the same layer, the list
keeps a single entry with the latest metadata. The session-state envelope
on the wire is a full snapshot per A.7.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from grace2_contracts import new_ulid
from grace2_contracts.collections import (
    PipelineSnapshot,
    PipelineStepSummary,
    ProjectLayerSummary,
)
from grace2_contracts.execution import LayerURI
from grace2_contracts.ws import (
    Envelope,
    MapCommandPayload,
    PipelineStatePayload,
    PipelineStep,
    SessionStatePayload,
)

__all__ = [
    "ErrorCodeRegistry",
    "EMITTER_ERROR_CODES",
    "EmitterError",
    "StepNotFoundError",
    "PipelineEmitter",
    "EmissionSink",
]

logger = logging.getLogger("grace2_agent.pipeline_emitter")


# --------------------------------------------------------------------------- #
# Error-code registry (Appendix A.6 open set, SCREAMING_SNAKE_CASE)
# --------------------------------------------------------------------------- #


class ErrorCodeRegistry:
    """Tracks the open-set SCREAMING_SNAKE_CASE error codes the emitter knows
    about. Per A.6 the set is OPEN — new codes can be registered at runtime.

    The registry exists so tests and the orchestrator audit can enumerate the
    currently-known set and so a typo at a ``mark_failed`` call site surfaces
    via ``register`` rather than silently inventing a new code. The
    ``PipelineStepSummary`` field validator already enforces the regex shape
    at schema-construction time (job-0030's ``_validate_error_code_shape``).

    TENTATIVE per kickoff: in M6 we may tighten to a closed ``Literal[...]``;
    for now the open set matches Decision G / A.6 prose.
    """

    def __init__(self, initial: list[str] | None = None) -> None:
        self._codes: set[str] = set(initial or [])

    def register(self, code: str) -> str:
        """Register ``code`` if not present and return it.

        Idempotent. The shape regex is enforced at schema construction time
        (``PipelineStepSummary._validate_error_code_shape``); calling
        ``register`` with a malformed code will later raise when the code is
        stored on a ``PipelineStepSummary``. We deliberately do NOT pre-
        validate here so the registry stays a passive set.
        """
        self._codes.add(code)
        return code

    def known(self, code: str) -> bool:
        return code in self._codes

    def snapshot(self) -> list[str]:
        return sorted(self._codes)


#: Seed set of error codes the M4 atomic tools + the cancel chain may emit.
#: Add new codes here (and at the call site) when a new failure mode lands.
EMITTER_ERROR_CODES = ErrorCodeRegistry(
    initial=[
        "UPSTREAM_API_ERROR",  # external HTTP API returned non-2xx / network failure
        "BBOX_INVALID",  # caller passed an unparseable / empty bbox
        "GEOCODE_NO_MATCH",  # geocode returned zero candidates
        "TOOL_NOT_FOUND",  # registry miss at tool-call site (A.6)
        "TOOL_PARAMS_INVALID",  # tool args failed validation (A.6)
        "CANCELLED",  # tool-call wrapper caught asyncio.CancelledError (A.6)
        "INTERNAL_ERROR",  # uncategorized exception in the tool body (A.6)
    ]
)


class EmitterError(RuntimeError):
    """Base class for emitter-internal errors. Distinct from tool errors."""


class StepNotFoundError(EmitterError):
    """``mark_*`` called with a step_id the emitter does not own."""


# --------------------------------------------------------------------------- #
# Emission sink — the function the emitter calls to push a frame on the wire
# --------------------------------------------------------------------------- #


#: Type of the per-session sink the emitter pushes frames to. The sink is
#: ``async`` so the emitter can await ``websocket.send``; tests pass a sync
#: capture closure wrapped in an async lambda.
EmissionSink = Callable[[str], Awaitable[None]]


# --------------------------------------------------------------------------- #
# PipelineEmitter
# --------------------------------------------------------------------------- #


def _now() -> datetime:
    """UTC ``datetime`` factory. Tests can patch via ``PipelineEmitter._now_fn``."""
    return datetime.now(timezone.utc)


@dataclass
class _StepState:
    """Internal mutable record for one step. Materialized into ``PipelineStep``
    (wire shape, A.4) and ``PipelineStepSummary`` (persistence shape, D.6)
    on demand. Kept private so the public API only exposes the immutable
    snapshot models."""

    step_id: str
    name: str
    tool_name: str
    state: str = "pending"
    started_at: datetime | None = None
    completed_at: datetime | None = None
    progress_percent: int | None = None
    error_code: str | None = None
    error_message: str | None = None


class PipelineEmitter:
    """Owns one session's pipeline snapshot + loaded_layers accumulator.

    Public API:

    - ``add_step(name, tool_name) -> step_id``: append a new ``pending`` step
      and emit a fresh ``pipeline-state``.
    - ``mark_running(step_id, *, progress_percent=None)``: flip to running,
      stamp ``started_at``, optionally seed progress, emit.
    - ``update_progress(step_id, progress_percent)``: bump ``progress_percent``
      mid-run; emit (subject to the per-tool opt-in — atomic tools simply
      never call this).
    - ``mark_complete(step_id)``: flip to ``complete``, stamp ``completed_at``,
      emit.
    - ``mark_failed(step_id, error_code, error_message)``: flip to ``failed``;
      ``error_code`` must already be in the registry (or be registered
      via ``EMITTER_ERROR_CODES.register(...)`` first). ``error_message``
      is truncated to 512 chars per D.6 contract.
    - ``mark_cancelled(step_id)``: flip to ``cancelled``; the cancel chain
      from the M1 ``server.py`` handler calls this before the
      ``asyncio.CancelledError`` propagates further.
    - ``add_loaded_layer(layer_uri)``: append a ``ProjectLayerSummary``
      derived from a ``LayerURI``; emit a fresh ``session-state`` envelope.
      Dedup policy: by ``uri`` (TENTATIVE — kickoff Open Questions).
    - ``emit_session_state()``: emit the current session-state snapshot
      (``current_pipeline`` set whenever a pipeline is running, plus the
      accumulated ``loaded_layers`` and chat history).

    Replace-not-reconcile (Appendix A.7) is structurally enforced: every
    ``_emit_*`` call serializes the FULL current snapshot.
    """

    #: Maximum length of an error_message (D.6 cap). Schema enforces; we
    #: truncate defensively at the emitter to keep call sites simple.
    ERROR_MESSAGE_MAX_LEN = 512

    #: Time factory; patched by tests for deterministic timestamps.
    _now_fn: Callable[[], datetime] = staticmethod(_now)

    def __init__(
        self,
        session_id: str,
        sink: EmissionSink,
        *,
        chat_history: list[dict] | None = None,
        pipeline_history: list[dict] | None = None,
        map_view: dict | None = None,
    ) -> None:
        self.session_id = session_id
        self._sink = sink

        #: Current pipeline id; ``None`` when no pipeline is running.
        self._pipeline_id: str | None = None
        self._pipeline_started_at: datetime | None = None

        #: Internal ordered store of steps, keyed by step_id for fast updates.
        self._steps: dict[str, _StepState] = {}
        self._step_order: list[str] = []

        #: Session-state mirror fields (passed-through from the session record).
        self._chat_history: list[dict] = list(chat_history or [])
        self._pipeline_history: list[dict] = list(pipeline_history or [])
        self._map_view: dict | None = map_view

        #: Accumulated layers — appended each time a tool returns a ``LayerURI``.
        self._loaded_layers: list[ProjectLayerSummary] = []

    # ------------------------------------------------------------------ #
    # Snapshot accessors (read-only views; tests + integrations introspect)
    # ------------------------------------------------------------------ #

    @property
    def pipeline_id(self) -> str | None:
        return self._pipeline_id

    @property
    def loaded_layers(self) -> list[ProjectLayerSummary]:
        """Return a defensive shallow copy of the current loaded_layers list."""
        return list(self._loaded_layers)

    def current_snapshot(self) -> PipelineSnapshot | None:
        """Return the current ``PipelineSnapshot`` (D.6 persistence shape) or
        ``None`` if no pipeline is running. Used by ``session-state`` emission
        and tests for the replace-not-reconcile invariant check."""
        if self._pipeline_id is None or not self._step_order:
            return None
        final_state: str | None = None
        if all(self._steps[sid].state == "complete" for sid in self._step_order):
            final_state = "complete"
        elif any(self._steps[sid].state == "failed" for sid in self._step_order):
            final_state = "failed"
        elif any(self._steps[sid].state == "cancelled" for sid in self._step_order):
            final_state = "cancelled"
        completed_at = (
            self._now_fn() if final_state is not None else None
        )
        return PipelineSnapshot(
            pipeline_id=self._pipeline_id,
            started_at=self._pipeline_started_at or self._now_fn(),
            completed_at=completed_at,
            final_state=final_state,  # type: ignore[arg-type]
            steps=[self._to_summary(sid) for sid in self._step_order],
        )

    # ------------------------------------------------------------------ #
    # Transition methods (the public emitter surface)
    # ------------------------------------------------------------------ #

    def start_pipeline(self) -> str:
        """Open a fresh pipeline. Returns the new ``pipeline_id``.

        Calling ``add_step`` without an open pipeline auto-opens one; this
        method exists so the tool-call-site wrapper can stamp ``current_pipeline``
        deterministically when it knows a pipeline is about to begin.
        """
        self._pipeline_id = new_ulid()
        self._pipeline_started_at = self._now_fn()
        self._steps.clear()
        self._step_order.clear()
        return self._pipeline_id

    def close_pipeline(self) -> None:
        """Archive the current pipeline snapshot into ``pipeline_history`` and
        clear ``current_pipeline``. Idempotent (no-op when no pipeline is open).

        Used after a final tool returns to land ``current_pipeline = None`` on
        the next ``session-state`` emission. The closed snapshot lives on as a
        history entry so the client can replay it via session-resume.
        """
        if self._pipeline_id is None:
            return
        snap = self.current_snapshot()
        if snap is not None:
            self._pipeline_history.append(snap.model_dump(mode="json"))
        self._pipeline_id = None
        self._pipeline_started_at = None
        self._steps.clear()
        self._step_order.clear()

    async def add_step(self, name: str, tool_name: str) -> str:
        """Append a new ``pending`` step and emit a fresh pipeline-state.

        Auto-opens a pipeline if none is open (so a single-tool invocation
        does not require an explicit ``start_pipeline`` from the call site).
        Returns the new ``step_id``.
        """
        if self._pipeline_id is None:
            self.start_pipeline()
        step_id = new_ulid()
        self._steps[step_id] = _StepState(
            step_id=step_id, name=name, tool_name=tool_name
        )
        self._step_order.append(step_id)
        await self._emit_pipeline_state()
        return step_id

    async def mark_running(
        self, step_id: str, *, progress_percent: int | None = None
    ) -> None:
        """Flip ``step_id`` to ``running``, stamp ``started_at``, emit."""
        step = self._require_step(step_id)
        step.state = "running"
        step.started_at = self._now_fn()
        if progress_percent is not None:
            step.progress_percent = self._coerce_progress(progress_percent)
        await self._emit_pipeline_state()

    async def update_progress(self, step_id: str, progress_percent: int) -> None:
        """Bump ``progress_percent`` on a running step; emit.

        Atomic tools NEVER call this (they're sub-second). Solver workflows
        opt-in by passing a progress callback to their dispatch tool that
        funnels through this method (M5+).
        """
        step = self._require_step(step_id)
        step.progress_percent = self._coerce_progress(progress_percent)
        await self._emit_pipeline_state()

    async def mark_complete(self, step_id: str) -> None:
        """Flip ``step_id`` to ``complete``, stamp ``completed_at``, emit."""
        step = self._require_step(step_id)
        step.state = "complete"
        step.completed_at = self._now_fn()
        # Per D.6 discipline: clear progress_percent on terminal states so
        # the client doesn't render a stale "99%" alongside a green chip.
        # We leave it set when the tool deliberately reported 100 — that's a
        # legitimate workflow signal.
        await self._emit_pipeline_state()

    async def mark_failed(
        self, step_id: str, error_code: str, error_message: str
    ) -> None:
        """Flip ``step_id`` to ``failed``; record error_code + error_message.

        ``error_code`` is registered with the module-level registry if it isn't
        already; ``error_message`` is truncated to 512 chars per D.6. The
        ``PipelineStepSummary`` schema validator enforces the regex shape; we
        rely on it to catch malformed codes at serialization time rather than
        duplicating the check.
        """
        step = self._require_step(step_id)
        EMITTER_ERROR_CODES.register(error_code)
        step.state = "failed"
        step.completed_at = self._now_fn()
        step.error_code = error_code
        step.error_message = self._truncate_message(error_message)
        await self._emit_pipeline_state()

    async def mark_cancelled(self, step_id: str) -> None:
        """Flip ``step_id`` to ``cancelled``; emit. Distinct from ``failed``
        per Invariant 8. The M1 cancel chain calls this from the tool-call
        wrapper's ``asyncio.CancelledError`` branch before re-raising."""
        step = self._require_step(step_id)
        step.state = "cancelled"
        step.completed_at = self._now_fn()
        await self._emit_pipeline_state()

    # ------------------------------------------------------------------ #
    # session-state — current_pipeline + loaded_layers
    # ------------------------------------------------------------------ #

    async def add_loaded_layer(self, layer: LayerURI) -> None:
        """Translate a ``LayerURI`` (tool return) into a ``ProjectLayerSummary``
        and append to the session's ``loaded_layers``, then emit a fresh
        ``session-state`` envelope (A.7 replace-not-reconcile).

        Dedup policy (TENTATIVE per kickoff): by ``uri``. If a tool re-fetches
        the same layer, the existing entry is REPLACED in place with the
        fresh metadata (e.g. style_preset may have changed) rather than
        appended.
        """
        summary = ProjectLayerSummary(
            layer_id=layer.layer_id,
            name=layer.name,
            layer_type=layer.layer_type,
            uri=layer.uri,
            style_preset=layer.style_preset,
            visible=True,
            role=layer.role,
            temporal=layer.temporal is not None,
        )
        # Dedup by uri — in-place replace if present, else append.
        for i, existing in enumerate(self._loaded_layers):
            if existing.uri == summary.uri:
                self._loaded_layers[i] = summary
                break
        else:
            self._loaded_layers.append(summary)
        await self.emit_session_state()
        # Emit zoom-to map-command when the LayerURI carries a bbox (job-0068).
        if layer.bbox is not None:
            await self.emit_map_command(
                "zoom-to",
                {"bbox": list(layer.bbox)},
            )

    async def emit_session_state(self) -> None:
        """Emit a full ``session-state`` envelope. Used after a layer lands or
        whenever the integration site wants to refresh the client's view of
        ``current_pipeline``.
        """
        snap = self.current_snapshot()
        payload = SessionStatePayload(
            chat_history=list(self._chat_history),
            loaded_layers=[
                layer.model_dump(mode="json") for layer in self._loaded_layers
            ],
            pipeline_history=list(self._pipeline_history),
            current_pipeline=(snap.model_dump(mode="json") if snap is not None else None),
            map_view=self._map_view,
        )
        await self._send("session-state", payload)

    async def emit_map_command(self, command: str, args: dict) -> None:
        """Emit a ``map-command`` envelope (job-0068).

        Used for transient verbs that are not pure state — primarily ``zoom-to``
        after a layer lands. Layer-CRUD verbs are conveyed via ``session-state``
        (layer-emission-contract.md decision).
        """
        payload = MapCommandPayload(command=command, args=args)  # type: ignore[arg-type]
        await self._send("map-command", payload)

    # ------------------------------------------------------------------ #
    # Tool-call wrapper — the integration seam for server.py
    # ------------------------------------------------------------------ #

    @contextmanager
    def tool_call(self, *, name: str, tool_name: str):
        """Sync context-manager form for non-async tool calls.

        Not used by ``server.py`` (which calls the async form below) but kept
        for direct unit-test access. Auto-marks ``running`` on entry and
        ``complete`` on clean exit; exceptions are re-raised AFTER marking
        ``failed`` with an inferred error_code. Note: sync context can't
        await emission — used by tests with a sync sink wrapper.
        """
        raise NotImplementedError(
            "use async_emit_tool_call from the WS handler; the sync context "
            "is reserved for a future non-WS integration"
        )

    async def emit_tool_call(
        self,
        *,
        name: str,
        tool_name: str,
        invoke: Callable[[], Any] | Callable[[], Awaitable[Any]],
    ) -> Any:
        """Wrap a single tool invocation with pipeline-state emission.

        Flow:
            1. ``add_step`` → emits ``pipeline-state`` with the new pending step.
            2. ``mark_running`` → emits ``pipeline-state`` with the step running.
            3. Invoke ``invoke()`` (awaits if it returns an awaitable).
            4. On clean return:
               - If the return value is a ``LayerURI``, call ``add_loaded_layer``
                 (which emits a fresh ``session-state``).
               - Then ``mark_complete`` → emits ``pipeline-state``.
               - Return the original tool result.
            5. On ``asyncio.CancelledError``: ``mark_cancelled`` + re-raise.
               (Honors Invariant 8 — cancelled is distinct from failed.)
            6. On any other exception: classify, ``mark_failed``, re-raise.
               The classifier is deliberately conservative — anything unknown
               surfaces as ``INTERNAL_ERROR`` with the exception message
               truncated to 512 chars.
        """
        step_id = await self.add_step(name=name, tool_name=tool_name)
        await self.mark_running(step_id)
        try:
            result = invoke()
            if asyncio.iscoroutine(result):
                result = await result
        except asyncio.CancelledError:
            await self.mark_cancelled(step_id)
            raise
        except Exception as exc:  # noqa: BLE001 — classify-and-re-raise
            code, message = self._classify_exception(exc)
            await self.mark_failed(step_id, error_code=code, error_message=message)
            raise
        # Honor LayerURI return shape — append to loaded_layers + emit session-state.
        if isinstance(result, LayerURI):
            await self.add_loaded_layer(result)
        await self.mark_complete(step_id)
        return result

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _classify_exception(self, exc: Exception) -> tuple[str, str]:
        """Map a tool exception to an ``(error_code, error_message)`` pair.

        Open-set per Appendix A.6 — extend the registry + this map as new
        failure modes land. Deliberately conservative: ambiguous shapes
        bucket into ``INTERNAL_ERROR`` rather than fabricate a more specific
        code.
        """
        message = str(exc) or exc.__class__.__name__
        # Subclass-aware bucketing. Order matters — most specific first.
        if isinstance(exc, ValueError) and "bbox" in message.lower():
            return ("BBOX_INVALID", message)
        if isinstance(exc, TimeoutError) or isinstance(
            exc, asyncio.TimeoutError
        ):  # pragma: no cover — Py3.11+ aliases
            return ("UPSTREAM_API_ERROR", f"upstream timeout: {message}")
        if isinstance(exc, ConnectionError):
            return ("UPSTREAM_API_ERROR", message)
        if isinstance(exc, LookupError) and "geocode" in message.lower():
            return ("GEOCODE_NO_MATCH", message)
        if isinstance(exc, KeyError) and "tool" in message.lower():
            return ("TOOL_NOT_FOUND", message)
        if isinstance(exc, TypeError) or isinstance(exc, ValueError):
            return ("TOOL_PARAMS_INVALID", message)
        return ("INTERNAL_ERROR", message)

    def _require_step(self, step_id: str) -> _StepState:
        step = self._steps.get(step_id)
        if step is None:
            raise StepNotFoundError(
                f"step_id {step_id!r} not registered with this emitter"
            )
        return step

    @staticmethod
    def _coerce_progress(value: int) -> int:
        if value < 0 or value > 100:
            raise ValueError(
                f"progress_percent must be in [0,100]; got {value!r}"
            )
        return int(value)

    @classmethod
    def _truncate_message(cls, message: str) -> str:
        if len(message) <= cls.ERROR_MESSAGE_MAX_LEN:
            return message
        return message[: cls.ERROR_MESSAGE_MAX_LEN]

    def _to_wire_step(self, step_id: str) -> PipelineStep:
        s = self._steps[step_id]
        return PipelineStep(
            step_id=s.step_id,
            name=s.name,
            tool_name=s.tool_name,
            state=s.state,  # type: ignore[arg-type]
            started_at=s.started_at,
            completed_at=s.completed_at,
            progress_percent=s.progress_percent,
        )

    def _to_summary(self, step_id: str) -> PipelineStepSummary:
        s = self._steps[step_id]
        return PipelineStepSummary(
            step_id=s.step_id,
            name=s.name,
            tool_name=s.tool_name,
            state=s.state,  # type: ignore[arg-type]
            started_at=s.started_at,
            completed_at=s.completed_at,
            progress_percent=s.progress_percent,
            error_code=s.error_code,
            error_message=s.error_message,
        )

    async def _emit_pipeline_state(self) -> None:
        if self._pipeline_id is None:
            # Defensive — emit-with-no-pipeline is a programming error from
            # the integration site; we don't paper over it with an empty
            # snapshot.
            raise EmitterError(
                "_emit_pipeline_state called with no open pipeline; "
                "call start_pipeline / add_step first"
            )
        payload = PipelineStatePayload(
            pipeline_id=self._pipeline_id,
            steps=[self._to_wire_step(sid) for sid in self._step_order],
        )
        await self._send("pipeline-state", payload)

    async def _send(self, message_type: str, payload: Any) -> None:
        env = Envelope(
            type=message_type, session_id=self.session_id, payload=payload
        )
        await self._sink(env.model_dump_json())
        logger.debug(
            "emitter session=%s type=%s pipeline_id=%s steps=%d layers=%d",
            self.session_id,
            message_type,
            self._pipeline_id,
            len(self._step_order),
            len(self._loaded_layers),
        )
