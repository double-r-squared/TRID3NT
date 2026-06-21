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
import contextvars
import logging
import os
import re
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
    SolveProgressPayload,
    ToolIoPayload,
)

from .layer_uri_emit import emit_layer_uri

__all__ = [
    "ErrorCodeRegistry",
    "EMITTER_ERROR_CODES",
    "EmitterError",
    "StepNotFoundError",
    "PipelineEmitter",
    "EmissionSink",
    "current_emitter",
    "bind_turn_case",
    "current_turn_case",
    "mint_dispatch_and_sim_cards",
    "route_sim_terminal",
]


# --------------------------------------------------------------------------- #
# Per-turn Case binding for envelope tagging (job-0277)
# --------------------------------------------------------------------------- #
#
# The dispatch wrappers (server._dispatch_gemini_and_persist /
# _dispatch_tool_and_persist) bind the turn's pinned Case into this
# ContextVar at task entry. EVERY envelope constructed inside the turn —
# server._new_envelope AND PipelineEmitter._send — reads it and stamps
# ``Envelope.case_id`` (proposed A.1 amendment), so the web client routes
# live streaming envelopes to the OWNING Case's stream even when the user
# has switched Cases and a concurrent turn re-pointed submit-time routing.
# A ContextVar is per-task: concurrent turns (job-0269) cannot cross-tag.

_TURN_CASE: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "grace2_turn_case", default=None
)


def bind_turn_case(case_id: str | None) -> contextvars.Token:
    """Bind the turn's owning Case for envelope tagging; returns the token."""
    return _TURN_CASE.set(case_id)


def current_turn_case() -> str | None:
    """The Case bound to the current task's turn, or None outside a turn."""
    return _TURN_CASE.get()


# --------------------------------------------------------------------------- #
# Active-emitter ContextVar (job-0160)
# --------------------------------------------------------------------------- #
#
# ``emit_tool_call`` binds the active ``PipelineEmitter`` into a ContextVar
# for the lifetime of the tool/workflow invocation. Workflow bodies (e.g.
# ``model_flood_scenario``) read ``current_emitter()`` to fire transient
# map-command verbs (zoom-to bbox immediately after geocode resolves, BEFORE
# the long SFINCS solve) — invariant 8's "responsive design" complement.
#
# Why a ContextVar, not a module-level binding (cf. ``tools.solver._EMITTER_BINDING``):
# multiple sessions may be servicing tool calls concurrently in the same
# process; a ContextVar is per-task and never leaks across asyncio tasks.
# The solver-side binding is module-level because it was scoped to a single
# wait-loop owned by the same task; the broader workflow surface needs the
# per-task isolation.

_CURRENT_EMITTER: contextvars.ContextVar["PipelineEmitter | None"] = (
    contextvars.ContextVar("grace2_current_emitter", default=None)
)


def current_emitter() -> "PipelineEmitter | None":
    """Return the ``PipelineEmitter`` bracketing the current tool/workflow call.

    Returns ``None`` outside an ``emit_tool_call`` scope (direct calls, unit
    tests without an emitter, smoke harnesses). Callers MUST handle ``None``
    gracefully — emitting a transient verb is a UX nice-to-have, not a
    correctness gate.
    """
    return _CURRENT_EMITTER.get()

logger = logging.getLogger("grace2_agent.pipeline_emitter")


# --------------------------------------------------------------------------- #
# Dead-socket terminal-send resilience (J-B-part-i)
# --------------------------------------------------------------------------- #
#
# The TERMINAL pipeline-state send (mark_failed / mark_complete / mark_cancelled)
# can raise ConnectionClosed* on a dead / mid-cycling WS, which aborts the
# terminal transition and LOSES the red/green card. We swallow ONLY the
# connection-closed class on the terminal path (never real logic errors) so the
# state transition itself always completes. ``websockets`` is a hard agent dep,
# but we import defensively (empty tuple) so the emitter module is importable in
# any minimal env / unit-test context that lacks it.
try:  # pragma: no cover — import shape, not behavior
    from websockets.exceptions import (
        ConnectionClosedError,
        ConnectionClosedOK,
    )

    _CONNECTION_CLOSED_EXC: tuple[type[BaseException], ...] = (
        ConnectionClosedError,
        ConnectionClosedOK,
    )
except Exception:  # pragma: no cover — websockets absent in a minimal env
    _CONNECTION_CLOSED_EXC = ()


# --------------------------------------------------------------------------- #
# Layer dedup-by-identity (job duplicate-flood-layer, SAFETY NET)
# --------------------------------------------------------------------------- #
#
# ``add_loaded_layer`` historically deduped by the DISPLAY ``uri`` only. That is
# too weak for the duplicate-flood-layer bug: the workflow's INTERNAL publish and
# a redundant LLM re-publish of the SAME underlying COG arrive with DIFFERENT
# display URLs (two distinct TiTiler tile templates / WMS LAYERS for one run) and
# DIFFERENT layer_ids, so the uri-only dedup never merged them — two rows for one
# flood result. The fix: derive a stable IDENTITY key from the underlying
# COG/run, not the display URL, so the two publishes of the same COG collide and
# merge into ONE loaded_layer.
#
# Identity precedence (most-specific first):
#   1. the COG path carried in a TiTiler tile template's ``url=<quoted s3/gs>``
#      query param — both publishes of the same COG embed the SAME ``url=`` value
#      even when the surrounding template (rescale/colormap) differs;
#   2. otherwise the raw ``uri`` itself (preserves the legacy uri-only behavior
#      for plain gs:///s3:// COGs, QGIS WMS display URLs, and non-raster vectors).
# Conservative by construction: an unrecognized display URL degrades to the full
# ``uri`` key (== the prior behavior), so nothing that did NOT previously dedup
# starts collapsing unexpectedly. In particular the QGIS WMS ``LAYERS=`` param is
# NOT used as a key — it carries a GENERIC layer name (e.g. ``LAYERS=wdpa``) that
# is shared across genuinely-distinct fetches (F97), so collapsing on it would
# wrongly merge two independent map layers.

def _layer_identity_key(uri: str) -> str:
    """Stable cross-publish identity for a layer's display ``uri``.

    Two publishes of the SAME underlying COG (workflow-internal + a redundant LLM
    re-publish) yield the SAME key because each TiTiler tile template embeds the
    SAME ``url=<COG>`` query param even when the surrounding rescale/colormap
    differs. Everything else falls back to the raw ``uri`` (legacy behavior)."""
    from urllib.parse import parse_qs, unquote, urlsplit

    if not isinstance(uri, str) or not uri:
        return uri
    try:
        parts = urlsplit(uri)
    except Exception:  # noqa: BLE001 — a malformed URL degrades to itself
        return uri
    if not parts.query:
        return uri
    qs = parse_qs(parts.query)
    # TiTiler tile template — the underlying COG is the ``url=`` param. This is
    # the SHARED identity across the workflow publish and a redundant LLM
    # re-publish of the same COG (the duplicate-flood-layer mechanism).
    cog = qs.get("url")
    if cog and cog[0]:
        return unquote(cog[0])
    # Unrecognized — keep the full uri so behavior is unchanged.
    return uri


# --------------------------------------------------------------------------- #
# Cross-RUN animation-frame identity (D3 — re-run frame accumulation)
# --------------------------------------------------------------------------- #
#
# A re-run of a flood scenario emits the SAME "Flood depth step N" name + role
# "context" but a NEW run-id-suffixed layer_id (...-frame-NN-<runB>) and a NEW
# per-run COG uri (.../<runB>/..._depth_frame_NN.tif). The COG-identity dedup in
# ``_layer_identity_key`` therefore NEVER collapses run B's frame N against run
# A's frame N -> the connection's ``_loaded_layers`` (and the persisted case)
# accumulate [step1, step1, step2, step2, ...] on every re-run (the live
# 50-layer case 01KVH4MZ9JF7GGHQ88D5PSWZVH). The stable cross-run token is the
# ``name`` ("Flood depth step N") + role="context", identical across runs for
# BOTH SWMM (postprocess_swmm) and SFINCS (postprocess_flood). Keying frames on
# that lets the NEWEST run's step N SUPERSEDE the prior run's step N in place.

#: Matches the EXACT frame name token both engines emit ("Flood depth step N").
_FLOOD_FRAME_NAME_RE = re.compile(r"^Flood depth step \d+$")


def _frame_series_key(summary: "ProjectLayerSummary") -> str | None:
    """Stable cross-RUN identity for an animation frame, else ``None``.

    Returns ``"flood-frame::<name>"`` for a flood animation frame (role
    "context" AND a ``"Flood depth step N"`` name); ``None`` for every other
    layer (peak, vectors, basemaps) so they keep the COG-identity dedup
    unchanged. Engine-agnostic: SWMM and SFINCS frames share the name token, so
    both de-accumulate uniformly. A frame only ever matches another frame.
    """
    if (
        summary.role == "context"
        and isinstance(summary.name, str)
        and _FLOOD_FRAME_NAME_RE.match(summary.name)
    ):
        return f"flood-frame::{summary.name}"
    return None


# --------------------------------------------------------------------------- #
# Terminal-on-RETURN detector (job — terminal-pipeline-card hardening)
# --------------------------------------------------------------------------- #
#
# A tool/workflow can FAIL or be CANCELLED yet still RETURN a value rather than
# raising — the solver poll path is the headline case. When the docker
# container is killed (user cancel, transient WS blip, or SOLVER_TIMEOUT), the
# supervisor writes a terminal completion.json and ``wait_for_completion``
# RETURNS a ``RunResult`` with ``status != "complete"`` instead of raising. The
# flood composer then returns a typed *failed* ``AssessmentEnvelope`` (via
# ``_build_failed_envelope``) whose only honesty signal is the ``:FAILED:<CODE>``
# infix on ``workflow_name`` (job-0327) — a NORMAL return. The MODFLOW tool
# returns a raw ``{"status": "error", ...}`` dict on the same path.
#
# Without inspecting the RETURN value, ``emit_tool_call`` falls through to
# ``mark_complete`` → a GREEN card on a dead solve (NATE's "silent green on a
# cancelled/timed-out run" symptom). This detector recognises ALL THREE failed-
# but-returned shapes so the wrapper can mark the card failed/cancelled instead.

_FAILED_DICT_STATUSES = frozenset({"error", "failed", "cancelled"})


def _classify_tool_return(result: Any) -> tuple[str, str, str] | None:
    """Inspect a tool RETURN value for a non-success terminal outcome.

    Returns ``None`` when the result is a healthy/success shape (the common
    case → the wrapper marks the card complete unchanged). Otherwise returns
    ``(terminal_state, error_code, error_message)`` where ``terminal_state`` is
    ``"cancelled"`` or ``"failed"`` — so the wrapper can call ``mark_cancelled``
    or ``mark_failed`` and the UI card reaches a visible terminal state instead
    of spinning forever.

    Recognised failed-but-RETURNED shapes (all key off STRUCTURE, never on a
    raised exception):

    1. ``RunResult`` (duck-typed: has ``status`` + ``run_id`` + ``handle_id``)
       with ``status != "complete"`` — the solver poll returned a killed/timed-
       out run. ``status == "cancelled"`` maps to the cancelled card.
    2. A ``dict`` with ``status`` in {error, failed, cancelled} — the MODFLOW
       tool's ``{"status": "error", "error_code": ..., "error_message": ...}``
       shape (run_modflow_tool.py).
    3. A failed ``AssessmentEnvelope`` (duck-typed via ``workflow_name``, or a
       dict with that key) whose ``workflow_name`` carries the ``:FAILED:<CODE>``
       honesty anchor (model_flood_scenario.py ``_build_failed_envelope``).

    Deliberately conservative: ANY ambiguous / unrecognised shape returns
    ``None`` (treated as success) so a healthy run is NEVER mislabelled failed.
    """

    def _from_workflow_name(wf: Any) -> tuple[str, str, str] | None:
        if isinstance(wf, str) and ":FAILED:" in wf:
            code = wf.split(":FAILED:", 1)[1].strip() or "MODEL_RUN_FAILED"
            state = "cancelled" if code.upper() == "CANCELLED" else "failed"
            return (state, code, f"workflow reported {code}")
        return None

    # --- Shape 1: RunResult (or any object with the same terminal fields) ---
    status = getattr(result, "status", None)
    if (
        isinstance(status, str)
        and not isinstance(result, dict)
        and hasattr(result, "run_id")
        and hasattr(result, "handle_id")
    ):
        if status == "complete":
            return None
        code = (
            getattr(result, "error_code", None)
            or (status.upper() if status else "SOLVER_FAILED")
        )
        message = (
            getattr(result, "error_message", None)
            or getattr(result, "cancellation_reason", None)
            or f"solver run {status}"
        )
        terminal = "cancelled" if status == "cancelled" else "failed"
        return (terminal, str(code), str(message))

    # --- Shape 3 (object): failed AssessmentEnvelope (duck-typed) ----------
    # Check BEFORE the generic dict branch so the envelope's ``:FAILED:`` infix
    # is the authoritative signal (a failed envelope's ``status`` field, if any,
    # is unrelated to the run outcome).
    if not isinstance(result, dict):
        wf_hit = _from_workflow_name(getattr(result, "workflow_name", None))
        if wf_hit is not None:
            return wf_hit

    # --- Shapes 2 & 3 (dict) ----------------------------------------------
    if isinstance(result, dict):
        wf_hit = _from_workflow_name(result.get("workflow_name"))
        if wf_hit is not None:
            return wf_hit
        dstatus = result.get("status")
        if isinstance(dstatus, str) and dstatus.lower() in _FAILED_DICT_STATUSES:
            code = result.get("error_code") or dstatus.upper()
            message = (
                result.get("error_message")
                or result.get("message")
                or result.get("error")
                or f"tool reported {dstatus}"
            )
            terminal = "cancelled" if dstatus.lower() == "cancelled" else "failed"
            return (terminal, str(code), str(message))

    return None


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


def _elapsed_ms(started_at: datetime | None, completed_at: datetime | None) -> int | None:
    """Compute wall-clock elapsed time in whole milliseconds (job-0264).

    Returns ``None`` when either endpoint is missing (can't attribute a
    duration without both). Clamped at 0 so a clock-skew / non-monotonic
    wall-clock never yields a negative duration on the wire (the contract is
    ``ge=0``). Rounds to the nearest millisecond.
    """
    if started_at is None or completed_at is None:
        return None
    delta = (completed_at - started_at).total_seconds() * 1000.0
    if delta < 0:
        return 0
    return int(round(delta))


# --------------------------------------------------------------------------- #
# tool-io serialization (tool-card-expand-output spec)
# --------------------------------------------------------------------------- #


def _json_for_tool_io(value: Any) -> tuple[str, bool, int]:
    """Serialize a tool-io field to a (json_string, truncated, orig_bytes) tuple.

    Pretty-prints (indent=2, sort_keys) so the expander renders readable JSON.
    A non-JSON-serializable value degrades to its ``str()`` rather than raising
    (``default=str`` covers nested non-serializable leaves too). Truncated to
    ``ToolIoPayload.MAX_FIELD_BYTES`` so a multi-MB result never rides the chat
    socket just to back an expander (large-payload norm); the returned byte
    count is the ORIGINAL length so the UI shows an honest "truncated, N bytes".
    UTF-8 byte counts (not char counts) so multibyte text is measured honestly;
    truncation is applied on the character string but bounded by the byte cap.
    """
    import json

    try:
        text = json.dumps(value, indent=2, sort_keys=True, default=str)
    except Exception:  # noqa: BLE001 — last-resort: never raise on serialization
        text = str(value)
    orig_bytes = len(text.encode("utf-8"))
    cap = ToolIoPayload.MAX_FIELD_BYTES
    if orig_bytes <= cap:
        return text, False, orig_bytes
    # Truncate on the UTF-8 byte boundary, then decode back ignoring a split
    # multibyte tail so the JSON-ish prefix stays valid text (the UI shows it
    # as raw text + a truncation note, so it need not remain valid JSON).
    truncated = text.encode("utf-8")[:cap].decode("utf-8", errors="ignore")
    return truncated, True, orig_bytes


# --------------------------------------------------------------------------- #
# Vector layer inline-GeoJSON helper (job-0175)
# --------------------------------------------------------------------------- #


def _fgb_bytes_to_geojson(fgb_bytes: bytes) -> dict[str, Any] | None:
    """Convert FlatGeobuf bytes to a GeoJSON FeatureCollection dict via
    pyogrio + geopandas. Returns None if read fails."""
    import os
    import tempfile
    try:
        import geopandas as gpd  # type: ignore[import-not-found]
    except ImportError as exc:
        logger.warning("_fgb_bytes_to_geojson: geopandas missing: %s", exc)
        return None
    tmp_path: str | None = None
    try:
        try:
            with tempfile.NamedTemporaryFile(
                suffix=".fgb", delete=False, prefix="grace2_inline_"
            ) as f:
                f.write(fgb_bytes)
                tmp_path = f.name
            gdf = gpd.read_file(tmp_path, engine="pyogrio")
        finally:
            if tmp_path is not None:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
    except Exception as exc:  # noqa: BLE001
        logger.warning("_fgb_bytes_to_geojson: read failed: %s", exc)
        return None
    if gdf is None or len(gdf) == 0:
        return {"type": "FeatureCollection", "features": []}
    try:
        gdf = gdf[gdf.geometry.notna()]
    except Exception:  # noqa: BLE001
        pass
    try:
        if gdf.crs is None:
            gdf = gdf.set_crs("EPSG:4326")
        elif str(gdf.crs).upper() not in {"EPSG:4326", "WGS84"}:
            gdf = gdf.to_crs("EPSG:4326")
    except Exception as exc:  # noqa: BLE001
        logger.warning("_fgb_bytes_to_geojson: CRS reproj failed: %s", exc)
    try:
        import json
        return json.loads(gdf.to_json())
    except Exception as exc:  # noqa: BLE001
        logger.warning("_fgb_bytes_to_geojson: GeoJSON dump failed: %s", exc)
        return None


async def _read_vector_uri_as_geojson(uri: str) -> dict[str, Any] | None:
    """Read a vector LayerURI from S3, parse FlatGeobuf, return GeoJSON dict.

    Supports ``s3://`` URIs (and local paths) for FlatGeobuf (`.fgb`) and
    GeoJSON (`.json` / `.geojson`). Returns ``None`` and logs a warning on any
    failure. Runs in a thread pool so the synchronous read + pyogrio call
    doesn't block the asyncio loop.
    """
    # GCP decommissioned: s3:// reads go through boto3 (EC2 instance-role);
    # everything else is a local path read via fsspec.
    if "://" in uri:
        key = uri.split("://", 1)[1].split("/", 1)[-1]
    else:
        key = uri
    ext = key.rsplit(".", 1)[-1].lower() if "." in key else ""

    loop = asyncio.get_running_loop()

    def _read_and_parse() -> dict[str, Any] | None:
        try:
            if uri.startswith("s3://"):
                # sprint-14-aws (job-0289): boto3 resolves the EC2 instance role
                # (s3fs falls back to anonymous here).
                import boto3
                rest = uri[len("s3://"):]
                b, _, k = rest.partition("/")
                data = boto3.client(
                    "s3", region_name=os.environ.get("AWS_REGION", "us-west-2")
                ).get_object(Bucket=b, Key=k)["Body"].read()
            else:
                # Local path (test / dev convenience).
                import fsspec  # type: ignore[import-not-found]
                with fsspec.open(uri, "rb") as f:
                    data = f.read()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "_read_vector_uri_as_geojson: object read failed uri=%s: %s", uri, exc,
            )
            return None
        if ext == "fgb":
            obj = _fgb_bytes_to_geojson(data)
        elif ext in {"json", "geojson"}:
            try:
                import json
                obj = json.loads(data)
                if not isinstance(obj, dict) or obj.get("type") != "FeatureCollection":
                    logger.warning(
                        "_read_vector_uri_as_geojson: not a FeatureCollection uri=%s", uri,
                    )
                    return None
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "_read_vector_uri_as_geojson: JSON parse failed uri=%s: %s", uri, exc,
                )
                return None
        else:
            logger.warning(
                "_read_vector_uri_as_geojson: unsupported extension '%s' for uri=%s",
                ext, uri,
            )
            return None
        # F94 + WS-30s fix: the densify is CPU-heavy (topology-preserving
        # simplify + feature cap over thousands of footprints) and session-resume
        # re-inlines + re-densifies the active-case layers on EVERY ~30s
        # reconnect. It MUST run here in the executor thread, NOT back on the
        # asyncio loop after the executor returns — running it on the loop blocked
        # the WS keepalive and contributed to the 30s drop cycle. The read above
        # was already off-loop; the densify is now folded into the same thread so
        # the entire read+densify path is off-loop for both callers
        # (``add_loaded_layer`` + ``reinline_vector_layers``).
        return _densify_off_loop(obj, uri)

    return await loop.run_in_executor(None, _read_and_parse)


def _densify_off_loop(geojson_obj: Any, uri: str) -> Any:
    """Densify a just-read FeatureCollection and stamp the URI-keyed side-table.

    Runs INSIDE the ``run_in_executor`` thread (never on the asyncio loop) so the
    CPU-heavy simplify/cap cannot block the WS keepalive. Behavior is identical to
    the prior loop-side block: below the threshold the FC is returned unchanged;
    above it the FC is topology-preserving-simplified + capped, and the
    per-layer ``DensifyMeta`` is recorded out-of-band in
    ``_LAST_DENSITY_META_BY_URI`` (FIFO-bounded) so ``emit_session_state`` can
    stamp the wire layer honestly. Densify failures fall through to the
    undensified FC (best-effort; a vector render always wins over a tag).
    """
    if not (isinstance(geojson_obj, dict)
            and geojson_obj.get("type") == "FeatureCollection"):
        return geojson_obj
    try:
        from .tools.vector_tiles import densify_if_needed

        geojson_obj, _density_meta = densify_if_needed(geojson_obj, layer_id=uri)
        if _density_meta is not None:
            # Bound this module-global side-table so the always-on agent
            # process never grows it without limit (F94 verifier: the
            # per-emitter table is pruned on reset, but this URI-keyed one
            # was not). FIFO-evict the oldest entry past the cap; dict
            # preserves insertion order.
            if uri in _LAST_DENSITY_META_BY_URI:
                del _LAST_DENSITY_META_BY_URI[uri]
            _LAST_DENSITY_META_BY_URI[uri] = _density_meta
            while len(_LAST_DENSITY_META_BY_URI) > _MAX_DENSITY_META_ENTRIES:
                _LAST_DENSITY_META_BY_URI.pop(
                    next(iter(_LAST_DENSITY_META_BY_URI))
                )
    except Exception as exc:  # noqa: BLE001 — never block a vector render
        logger.warning(
            "_read_vector_uri_as_geojson: densify failed uri=%s: %s", uri, exc,
        )
    return geojson_obj


#: F94: side-table of the most-recent dense-vector ``DensifyMeta`` keyed by the
#: vector artifact URI. ``_read_vector_uri_as_geojson`` is a module function (not
#: a method), so it stashes the meta here; ``add_loaded_layer`` /
#: ``reinline_vector_layers`` lift it into the per-emitter
#: ``_density_meta_by_layer_id`` keyed by layer_id. Module scope is safe: the URI
#: is content-addressed (cache key) so two concurrent sessions reading the same
#: dense artifact compute identical meta. Bounded by ``_MAX_DENSITY_META_ENTRIES``
#: (FIFO eviction at the write site) so it cannot grow unbounded over the
#: lifetime of the always-on agent process.
_MAX_DENSITY_META_ENTRIES: int = 256
_LAST_DENSITY_META_BY_URI: dict[str, Any] = {}


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
    #: Authoritative wall-clock elapsed time in milliseconds (job-0264).
    #: Stamped on the terminal transition from ``started_at``→``completed_at``;
    #: ``None`` while pending/running. Deterministic — never an LLM estimate.
    duration_ms: int | None = None
    #: Two-card sim observability (task-149): card-kind discriminator + Batch
    #: binding. ``role`` defaults to ``"tool"`` (the on-box atomic-tool card —
    #: every existing step); ``"compute"`` is the off-box solver card bound to an
    #: AWS Batch job. ``batch_job_id`` is the Batch ``jobId`` the compute card
    #: tracks; ``batch_status`` mirrors the last ``DescribeJobs`` status verbatim
    #: (SUBMITTED / RUNNABLE / STARTING / RUNNING / SUCCEEDED / FAILED). Never an
    #: LLM estimate (Invariant 1). Both ids ``None`` for a plain tool card so the
    #: wire shape is byte-identical (back-compat).
    role: str = "tool"
    batch_job_id: str | None = None
    batch_status: str | None = None


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

        #: Inline GeoJSON side-table for vector layers (job-0175).
        #: Keyed by ``layer_id``; merged into ``loaded_layers`` wire payload
        #: in ``emit_session_state`` as additive ``inline_geojson`` field.
        #: Preserves ``ProjectLayerSummary`` extra="forbid" strictness.
        self._inline_geojson_by_layer_id: dict[str, dict[str, Any]] = {}

        #: F94: dense-vector density tag side-table, keyed by ``layer_id``.
        #: When a vector layer crossed ``DENSE_VECTOR_THRESHOLD`` and was
        #: simplified/capped, its ``DensifyMeta`` is stored here and merged into
        #: ``emit_session_state`` as the additive ``vector_density`` field so the
        #: client surfaces the degradation honestly. Cleared/pruned alongside the
        #: inline side-table (same lifecycle).
        self._density_meta_by_layer_id: dict[str, Any] = {}

        #: job-0267: terminal summary of the most recent ``emit_tool_call``
        #: step. Carries the AUTHORITATIVE job-0264 stamps (``started_at`` /
        #: ``duration_ms``) so the tool-card persistence hook in
        #: ``server._invoke_tool_via_emitter`` records exactly the duration
        #: the live card displayed — no second clock. Set on every terminal
        #: transition of ``emit_tool_call`` (complete / failed / cancelled);
        #: read-only everywhere else.
        self.last_tool_step: PipelineStepSummary | None = None

        #: J-B-part-i: the most recent TERMINAL pipeline-state payload (set on
        #: every terminal transition via ``_emit_terminal_pipeline_state``).
        #: ``rebind_sink`` replays it onto a reconnected socket so a
        #: RENDERED/terminal card stays surfaced across a WS blip
        #: (per-Case-durability / replay-on-reconnect). ``None`` until the first
        #: terminal transition.
        self._last_terminal_pipeline_payload: PipelineStatePayload | None = None

    # ------------------------------------------------------------------ #
    # Session-state seeding (#147 reconnect-resync)
    # ------------------------------------------------------------------ #

    def seed_chat_history(self, history: list[dict]) -> None:
        """Replace the chat-history mirror this emitter ships in session-state.

        #147 reconnect-resync: the next ``emit_session_state`` snapshot carries
        ``list(self._chat_history)``, so seeding this mirror with a rehydrated
        per-Case history lets a reconnecting client resync its transcript from
        the server's authoritative copy. A defensive ``list(...)`` copy is taken
        so the caller's list cannot later mutate the emitter's mirror.

        Dormant until a call-site invokes it: the constructor still seeds
        ``_chat_history`` exactly as before, so an emitter that is never seeded
        behaves byte-identically to the prior version.
        """
        self._chat_history = list(history or [])

    # ------------------------------------------------------------------ #
    # Sink rebinding (job-SOLVE-SURVIVE: WS-disconnect survival)
    # ------------------------------------------------------------------ #

    def rebind_sink(self, sink: EmissionSink) -> None:
        """Swap the wire sink this emitter pushes frames to.

        job-SOLVE-SURVIVE: a long-running solver turn (``run_model_flood_scenario``
        -> ``wait_for_completion``) is driven by ONE ``PipelineEmitter`` instance
        whose ``_sink`` closes over the WebSocket that LAUNCHED the turn. The web
        client opens multiple sockets per session (StrictMode double-mount +
        reconnect) — when the launching socket closes, its sink silently drops
        every subsequent progress / terminal frame. When a NEW socket for the
        SAME session connects, the integration site rebinds this emitter's sink
        to the new socket's ``send`` so the still-running solve's progress and
        its terminal ``session-state`` (the published flood layer) reach the
        user on their live connection. The next ``emit_*`` call uses the new
        sink.

        J-B-part-i (replay-on-reconnect / per-Case durability): if a TERMINAL
        pipeline-state was already emitted (the red/green/yellow card) but the
        launching socket was dead when it went out, the still-running turn may
        emit NOTHING further — so the next ``emit_*`` never repaints the card and
        the terminal state is lost on the new socket. To make the terminal card
        survive a WS blip, we REPLAY the last terminal pipeline-state snapshot
        onto the NEW sink here, schedule-and-forget (this method is sync and the
        sink is async). The replay is best-effort: it swallows a dead-socket
        failure on the new sink (it too may have just cycled) and never raises
        out of the rebind."""
        self._sink = sink
        snapshot = self._last_terminal_pipeline_payload
        if snapshot is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop (e.g. a sync test rebind) — nothing to schedule.
            # The snapshot stays stashed; a later emit still carries the full
            # A.7 view, and a loop-bound rebind replays it.
            return
        loop.create_task(self._replay_terminal_pipeline_state(snapshot))

    async def _replay_terminal_pipeline_state(
        self, payload: PipelineStatePayload
    ) -> None:
        """Replay a stashed terminal pipeline-state onto the (rebound) sink.

        J-B-part-i: best-effort — the new sink may also be mid-cycle, so a
        ConnectionClosed* is swallowed (the card replays on the NEXT rebind);
        any other error propagates from ``_send`` as usual."""
        try:
            await self._send("pipeline-state", payload)
        except _CONNECTION_CLOSED_EXC:  # type: ignore[misc]
            logger.debug(
                "emitter: terminal pipeline-state replay failed on the rebound "
                "socket (best-effort drop) session=%s pipeline_id=%s",
                self.session_id,
                self._pipeline_id,
            )

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

    @property
    def inline_geojson_by_layer_id(self) -> dict[str, dict[str, Any]]:
        """Return a defensive shallow copy of the inline-GeoJSON side-table.

        Lane A1 (pen=agent / paper=case): the materialized case-view snapshot
        written to S3 on every mutation needs the inline vector GeoJSON so a
        cold view (agent box OFF) paints vectors. That side-table is in-memory
        only on this emitter (``add_loaded_layer`` / ``reinline_vector_layers``
        populate it; ``emit_session_state`` merges it onto the wire). Exposing a
        read-only copy lets ``server._persist_case_view_snapshot`` source the
        SAME GeoJSON the live ``session-state`` carries, so the snapshot is
        byte-identical to the live ``case-open`` payload plus inline vectors.
        Keyed by ``layer_id``; values are GeoJSON FeatureCollection dicts.
        """
        return {k: v for k, v in self._inline_geojson_by_layer_id.items()}

    @property
    def density_meta_by_layer_id(self) -> dict[str, Any]:
        """Return a defensive shallow copy of the dense-vector tag side-table.

        Companion to ``inline_geojson_by_layer_id`` — ``emit_session_state``
        stamps each layer's ``DensifyMeta.as_wire_tag()`` alongside the inline
        GeoJSON, so the materialized snapshot replicates that merge to stay
        byte-identical to the live wire. Keyed by ``layer_id``; values are the
        opaque ``DensifyMeta`` objects (the snapshot writer calls
        ``as_wire_tag()`` defensively, matching ``emit_session_state``).
        """
        return {k: v for k, v in self._density_meta_by_layer_id.items()}

    def reset_loaded_layers(self, layers: list[dict] | None) -> None:
        """Replace the in-memory ``_loaded_layers`` from a persisted snapshot.

        job-0172 Part B: called on ``case-open`` to seed the per-connection
        accumulator with whatever ``CaseSessionState.loaded_layers`` held.
        Each input dict is validated through ``ProjectLayerSummary`` so a
        malformed entry doesn't corrupt the in-memory state. Malformed
        entries are skipped (logged) — partial seeding is preferable to
        wholesale rollback because the next legitimate emission will
        re-stabilize the wire shape via the existing dedup-by-uri rule.

        Pass ``None`` or ``[]`` to flush (used on ``case-command(create)``).
        Does NOT emit a ``session-state`` — the caller decides when to send
        the next snapshot.
        """
        if not layers:
            self._loaded_layers = []
            # job-0175: flush inline side-table alongside loaded_layers.
            self._inline_geojson_by_layer_id.clear()
            # F94: flush the dense-vector density tags too.
            self._density_meta_by_layer_id.clear()
            return
        seeded: list[ProjectLayerSummary] = []
        for layer_dict in layers:
            if not isinstance(layer_dict, dict):
                continue
            try:
                seeded.append(ProjectLayerSummary.model_validate(layer_dict))
            except Exception:  # noqa: BLE001
                logger.warning(
                    "reset_loaded_layers: skipping malformed layer dict"
                )
                continue
        self._loaded_layers = seeded
        # job-0175: keep only inline entries that match a still-loaded layer.
        active_ids = {layer.layer_id for layer in seeded}
        self._inline_geojson_by_layer_id = {
            k: v for k, v in self._inline_geojson_by_layer_id.items() if k in active_ids
        }
        # F94: prune density tags to the still-loaded layers too.
        self._density_meta_by_layer_id = {
            k: v for k, v in self._density_meta_by_layer_id.items() if k in active_ids
        }

    async def reinline_vector_layers(self) -> int:
        """Rebuild ``_inline_geojson_by_layer_id`` for persisted vector layers.

        sprint-14-aws (job-0290d): the inline-GeoJSON side-table is in-memory
        only — a Case reopen seeds ``_loaded_layers`` from the persisted
        snapshot via ``reset_loaded_layers`` but the inline payloads are gone,
        so the browser (which never fetches gs://"/s3:// directly, job-0175)
        rehydrates vector layers it cannot render. Re-read each vector layer's
        object-store artifact and repopulate the side-table; the caller emits
        a fresh ``session-state`` so the wire carries ``inline_geojson`` again.

        Best-effort per layer (a missing/corrupt artifact skips that layer,
        never raises). Returns the number of layers re-inlined.
        """
        count = 0
        for layer in self._loaded_layers:
            if layer.layer_type != "vector":
                continue
            if layer.layer_id in self._inline_geojson_by_layer_id:
                continue
            uri = layer.uri or ""
            if not uri:
                continue
            try:
                geojson_obj = await _read_vector_uri_as_geojson(uri)
            except Exception:  # noqa: BLE001 — per-layer best-effort
                logger.warning(
                    "reinline_vector_layers: read failed layer_id=%s uri=%s",
                    layer.layer_id,
                    uri,
                )
                continue
            if geojson_obj is not None:
                self._inline_geojson_by_layer_id[layer.layer_id] = geojson_obj
                # F94: lift any dense-vector density tag from the module-level
                # stash (keyed by uri) into the per-emitter map (keyed by
                # layer_id) so the wire layer is stamped on re-inline too.
                _meta = _LAST_DENSITY_META_BY_URI.get(uri)
                if _meta is not None:
                    self._density_meta_by_layer_id[layer.layer_id] = _meta
                count += 1
        return count

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

    async def update_current_progress(self, progress_percent: int) -> None:
        """Bump ``progress_percent`` on the CURRENTLY-running step; emit.

        Convenience for workflow bodies that hold a ``current_emitter()`` handle
        but NOT the ``step_id`` (the step is created inside ``emit_tool_call``).
        Targets the most-recently-added step that is in ``running`` state; no-op
        (best-effort) when no step is running — emitting pre-solver progress is a
        UX nice-to-have, never a correctness gate. Used by
        ``model_flood_scenario`` to keep the card from sitting silently during
        the multi-second pre-solver fetcher chain + SFINCS build (so a stall is
        VISIBLE and a hang is bounded by the per-phase timeout).
        """
        running = [
            sid for sid in self._step_order if self._steps[sid].state == "running"
        ]
        if not running:
            return
        step = self._steps[running[-1]]
        step.progress_percent = self._coerce_progress(progress_percent)
        await self._emit_pipeline_state()

    # ------------------------------------------------------------------ #
    # Two-card sim observability (task-149) — the off-box compute card
    # ------------------------------------------------------------------ #

    async def add_compute_step(
        self,
        *,
        name: str,
        tool_name: str,
        batch_job_id: str,
        batch_status: str | None = None,
    ) -> str:
        """Append a ``role="compute"`` step bound to an AWS Batch job; emit.

        Thin helper over ``add_step`` + ``mark_running`` that mints the SECOND
        of the two sim cards (task-149): the off-box solver card the composer
        opens right BEFORE ``wait_for_completion``. The first card is a plain
        ``add_step`` → ``mark_complete`` recording the submit; this one tracks
        the live Batch job. The step lands in ``running`` state immediately
        (``started_at`` stamped) so the card shows forward motion while the
        ephemeral Batch worker (no inbound WS) runs and the agent-side poll loop
        feeds ``batch_status`` via ``update_compute_status``. Returns the new
        ``step_id``.

        ``batch_status`` mirrors the Batch control-plane verbatim — never an LLM
        estimate (Invariant 1); ``None`` until the first ``DescribeJobs`` tick.
        """
        step_id = await self.add_step(name=name, tool_name=tool_name)
        step = self._require_step(step_id)
        step.role = "compute"
        step.batch_job_id = batch_job_id
        step.batch_status = batch_status
        # Flip to running (stamps started_at + re-emits) so the compute card is
        # live the moment the solve begins.
        await self.mark_running(step_id)
        return step_id

    async def update_compute_status(
        self, step_id: str, batch_status: str
    ) -> None:
        """Patch a compute step's ``batch_status`` and re-emit; best-effort.

        task-149 sibling of ``update_current_progress``: the agent-side solver
        wait-loop calls this each poll tick with the latest ``DescribeJobs``
        status so the off-box compute card reflects the Batch control-plane
        (SUBMITTED / RUNNABLE / STARTING / RUNNING / SUCCEEDED / FAILED) verbatim
        — never an LLM estimate (Invariant 1). No-op (best-effort) when the
        ``step_id`` is unknown OR when nothing changed, so a steady poll does not
        spam an identical frame and a stale binding never raises out of the poll
        loop (live status is a UX signal, not a correctness gate). Does NOT alter
        the step's ``state`` — the terminal ``mark_complete`` / ``mark_failed``
        owns that transition.
        """
        step = self._steps.get(step_id)
        if step is None:
            return
        if step.batch_status == batch_status:
            return
        step.batch_status = batch_status
        await self._emit_pipeline_state()

    async def mark_complete(self, step_id: str) -> None:
        """Flip ``step_id`` to ``complete``, stamp ``completed_at``, emit."""
        step = self._require_step(step_id)
        step.state = "complete"
        step.completed_at = self._now_fn()
        # job-0264: stamp authoritative wall-clock duration on the terminal
        # transition (started_at→completed_at). Deterministic; the client
        # locks its cosmetic ticker to this number once it arrives.
        step.duration_ms = _elapsed_ms(step.started_at, step.completed_at)
        # Per D.6 discipline: clear progress_percent on terminal states so
        # the client doesn't render a stale "99%" alongside a green chip.
        # We leave it set when the tool deliberately reported 100 — that's a
        # legitimate workflow signal.
        # J-B-part-i: terminal emit is best-effort on a dead socket + snapshots
        # for replay-on-rebind so the green card survives a WS cycle.
        await self._emit_terminal_pipeline_state()

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
        # job-0264: failed cards show the final duration too (mm:ss of how
        # long the tool ran before failing). started_at may be None if the
        # step failed before mark_running — _elapsed_ms returns None then.
        step.duration_ms = _elapsed_ms(step.started_at, step.completed_at)
        step.error_code = error_code
        step.error_message = self._truncate_message(error_message)
        # J-B-part-i: terminal emit is best-effort on a dead socket + snapshots
        # for replay-on-rebind so the red card survives a WS cycle.
        await self._emit_terminal_pipeline_state()

    async def mark_cancelled(self, step_id: str) -> None:
        """Flip ``step_id`` to ``cancelled``; emit. Distinct from ``failed``
        per Invariant 8. The M1 cancel chain calls this from the tool-call
        wrapper's ``asyncio.CancelledError`` branch before re-raising."""
        step = self._require_step(step_id)
        step.state = "cancelled"
        step.completed_at = self._now_fn()
        # job-0264: cancelled is terminal — stamp duration so the yellow card
        # locks to the elapsed-before-cancel time rather than ticking forever.
        step.duration_ms = _elapsed_ms(step.started_at, step.completed_at)
        # J-B-part-i: terminal emit is best-effort on a dead socket + snapshots
        # for replay-on-rebind so the yellow card survives a WS cycle.
        await self._emit_terminal_pipeline_state()

    # ------------------------------------------------------------------ #
    # session-state — current_pipeline + loaded_layers
    # ------------------------------------------------------------------ #

    async def add_loaded_layer(self, layer: LayerURI) -> None:
        """Translate a ``LayerURI`` (tool return) into a ``ProjectLayerSummary``
        and append to the session's ``loaded_layers``, then emit a fresh
        ``session-state`` envelope (A.7 replace-not-reconcile).

        Dedup policy (job duplicate-flood-layer, SAFETY NET): by the underlying
        COG/run IDENTITY (``_layer_identity_key``), NOT by the display ``uri``
        alone. The workflow's internal publish and a redundant LLM re-publish of
        the SAME COG arrive with different display URLs (distinct TiTiler tile
        templates / WMS LAYERS) and different layer_ids; keying on the shared COG
        identity makes them COLLIDE and MERGE into ONE row instead of painting a
        styleless duplicate. The existing entry is REPLACED in place with the
        fresh metadata (e.g. a styled re-publish supersedes a styleless one). A
        plain gs:///s3:// COG (no query string) keys to its own uri, so the
        legacy uri-only behavior is preserved for everything not display-wrapped.
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
        # Dedup by underlying-COG identity — in-place replace if present, else
        # append. ``_layer_identity_key`` collapses two display URLs of the same
        # COG to one key; for a plain COG it is the uri itself (legacy behavior).
        # D3: animation frames ALSO supersede the prior run's same-step frame via
        # the (role + "Flood depth step N") series key, because a re-run's frame N
        # is a DISTINCT COG (new run-id) and would otherwise accumulate. A frame
        # only ever matches another frame; everything else keeps COG-identity
        # dedup (the ``_match`` guard prevents frame/non-frame cross-collapse).
        _new_frame_key = _frame_series_key(summary)
        _new_key = _layer_identity_key(summary.uri)
        for i, existing in enumerate(self._loaded_layers):
            if _new_frame_key is not None:
                _match = _frame_series_key(existing) == _new_frame_key
            else:
                _match = (
                    _frame_series_key(existing) is None
                    and _layer_identity_key(existing.uri) == _new_key
                )
            if _match:
                # Drop the SUPERSEDED layer_id's side tables (inline GeoJSON /
                # density meta) so a merge cannot leave an orphan keyed on the
                # old id. No-op for raster flood layers (no inline GeoJSON).
                if existing.layer_id != summary.layer_id:
                    self._inline_geojson_by_layer_id.pop(existing.layer_id, None)
                    self._density_meta_by_layer_id.pop(existing.layer_id, None)
                self._loaded_layers[i] = summary
                break
        else:
            self._loaded_layers.append(summary)
        # Vector inline-GeoJSON (job-0175). Best-effort; failure is non-fatal.
        # Logs loudly so the audit can grep for "inlined GeoJSON layer_id=...".
        if layer.layer_type == "vector":
            try:
                geojson_obj = await _read_vector_uri_as_geojson(layer.uri)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "add_loaded_layer: inline GeoJSON conversion failed for "
                    "layer_id=%s uri=%s; falling back to URI-only delivery: %s",
                    layer.layer_id, layer.uri, exc,
                )
                self._inline_geojson_by_layer_id.pop(layer.layer_id, None)
            else:
                if geojson_obj is not None:
                    self._inline_geojson_by_layer_id[layer.layer_id] = geojson_obj
                    feat_count = len(geojson_obj.get("features") or [])
                    logger.info(
                        "add_loaded_layer: inlined GeoJSON layer_id=%s features=%d",
                        layer.layer_id, feat_count,
                    )
                    # F94: lift any dense-vector density tag (keyed by uri in the
                    # module stash) into the per-emitter map (keyed by layer_id)
                    # so the wire layer carries the honest simplified/capped tag.
                    _meta = _LAST_DENSITY_META_BY_URI.get(layer.uri)
                    if _meta is not None:
                        self._density_meta_by_layer_id[layer.layer_id] = _meta
                    else:
                        self._density_meta_by_layer_id.pop(layer.layer_id, None)
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

        Vector inline-GeoJSON merge (job-0175): for any vector layer whose
        ``layer_id`` has an inline GeoJSON entry, the field ``inline_geojson``
        is appended to the wire dict (additive to the strict schema).
        """
        snap = self.current_snapshot()
        # Build loaded_layers dump with inline_geojson merged in.
        loaded_dump_with_inline: list[dict[str, Any]] = []
        for _layer in self._loaded_layers:
            _d = _layer.model_dump(mode="json")
            _inline = self._inline_geojson_by_layer_id.get(_layer.layer_id)
            if _inline is not None:
                _d["inline_geojson"] = _inline
            # F94: stamp the dense-vector density tag (additive, like
            # inline_geojson) so the client can surface "simplified for
            # performance" honestly. Best-effort; a malformed meta is skipped.
            _meta = self._density_meta_by_layer_id.get(_layer.layer_id)
            if _meta is not None:
                try:
                    _d.update(_meta.as_wire_tag())
                except Exception:  # noqa: BLE001
                    pass
            loaded_dump_with_inline.append(_d)
        payload = SessionStatePayload(
            chat_history=list(self._chat_history),
            loaded_layers=loaded_dump_with_inline,
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

    async def emit_solve_progress(self, progress: dict) -> None:
        """Emit a ``solve-progress`` envelope (live big-sim telemetry).

        ``progress`` is the dict from ``telemetry.build_live_solve_progress``
        (run_id / solver / grid_resolution_m / active_cell_count / vcpus /
        elapsed_seconds / eta_seconds). The web track renders these inline on
        the running tool/pipeline card so a multi-minute solve shows live
        grid/cells/vCPU/elapsed/ETA rather than a silent spinner. Best-effort:
        a malformed dict is logged + dropped (live telemetry is a UX hint, never
        a correctness gate — mirrors ``update_current_progress``)."""
        try:
            payload = SolveProgressPayload(**progress)
        except Exception as exc:  # noqa: BLE001 — never break the solve loop
            logger.warning("emit_solve_progress: bad payload dropped: %s", exc)
            return
        await self._send("solve-progress", payload)

    async def emit_tool_io(
        self,
        *,
        step_id: str,
        tool_name: str,
        raw_args: Any,
        function_response: Any,
        is_error: bool = False,
    ) -> None:
        """Emit a ``tool-io`` envelope (tool-card-expand-output spec).

        The sidecar that carries the RAW input args + the RAW
        ``function_response`` for one tool dispatch so the chat tool-card's
        expander can reveal them (keyed by ``step_id`` to the dispatch's card).
        Both payloads are json-dumped to STRINGS here — a non-serializable value
        degrades to its ``repr`` rather than breaking the envelope — and
        TRUNCATED to ``ToolIoPayload.MAX_FIELD_BYTES`` (large-payload norm: the
        chat must never ship a multi-MB blob for an expander). The original byte
        length + a truncation flag ride along so the UI renders an honest
        "truncated, N bytes" note.

        Best-effort: a serialization / send failure is logged and dropped — the
        expander is a debugging affordance, never a correctness gate, so it must
        not break the dispatch loop (mirrors ``emit_solve_progress``)."""
        try:
            args_str, args_trunc, args_bytes = _json_for_tool_io(raw_args)
            resp_str, resp_trunc, resp_bytes = _json_for_tool_io(function_response)
            payload = ToolIoPayload(
                step_id=step_id,
                tool_name=tool_name,
                raw_args=args_str,
                function_response=resp_str,
                is_error=bool(is_error),
                args_truncated=args_trunc,
                response_truncated=resp_trunc,
                args_bytes=args_bytes,
                response_bytes=resp_bytes,
            )
        except Exception as exc:  # noqa: BLE001 — never break the dispatch loop
            logger.warning("emit_tool_io: bad payload dropped: %s", exc)
            return
        await self._send("tool-io", payload)

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
        # Bind self as the active emitter for the lifetime of the invoke so
        # workflow bodies can fire transient map-command verbs (job-0160 —
        # zoom-on-area-first UX). reset_token ensures the binding is unwound
        # exactly once, even on cancellation / exception paths.
        token = _CURRENT_EMITTER.set(self)
        try:
            try:
                result = invoke()
                if asyncio.iscoroutine(result):
                    result = await result
            except asyncio.CancelledError:
                await self.mark_cancelled(step_id)
                # job-0267: record the terminal step even on cancel — the
                # persistence hook skips cancelled cards, but the accessor
                # must never carry a STALE prior step past this dispatch.
                self.last_tool_step = self._to_summary(step_id)
                raise
            except Exception as exc:  # noqa: BLE001 — classify-and-re-raise
                code, message = self._classify_exception(exc)
                await self.mark_failed(step_id, error_code=code, error_message=message)
                self.last_tool_step = self._to_summary(step_id)  # job-0267
                raise
            # TERMINAL FRAME FIRST (stuck-running-card fix): emit the terminal
            # pipeline-state frame (complete / failed / cancelled) BEFORE the
            # LayerURI's session-state emission. Previously add_loaded_layer ran
            # first and emitted a session-state snapshot that captured the step
            # while it was STILL "running"; that snapshot could arrive at/after
            # the terminal frame, leaving the tool card stuck "Computing
            # hillshade..." (running) forever for every compute_*/LayerURI tool.
            # The terminal classification depends only on the tool RESULT, not
            # on the layer being added, so we can safely flip the card first and
            # have add_loaded_layer's session-state snapshot reflect the
            # terminal state.
            #
            # job (terminal-pipeline-card hardening): a tool can FAIL or be
            # CANCELLED yet still RETURN (the solver poll path — a docker-killed
            # / timed-out run returns a RunResult or a failed AssessmentEnvelope
            # rather than raising). Inspect the return value: if it carries a
            # non-success terminal outcome, flip the card to cancelled/failed
            # instead of green. This kills NATE's "silent green on a cancelled
            # solve" + "card spins forever then mislabels success" symptom for
            # BOTH the flood envelope (:FAILED: anchor) and the MODFLOW dict.
            terminal = _classify_tool_return(result)
            if terminal is not None:
                state, error_code, error_message = terminal
                if state == "cancelled":
                    await self.mark_cancelled(step_id)
                else:
                    await self.mark_failed(
                        step_id,
                        error_code=error_code,
                        error_message=error_message,
                    )
                logger.info(
                    "emit_tool_call: tool %r RETURNED a non-success terminal "
                    "outcome state=%s code=%s; card marked %s (not complete)",
                    tool_name, state, error_code, state,
                )
            else:
                await self.mark_complete(step_id)
            self.last_tool_step = self._to_summary(step_id)  # job-0267
            # Honor LayerURI return shape — append to loaded_layers + emit
            # session-state. This runs AFTER the terminal frame above so the
            # session-state snapshot captures the step as complete/failed, never
            # "running" (the stuck-card bug). job-0254: route through the single
            # emission seam first. The seam drops (returns None) a renderable
            # raster carrying a raw gs:// uri (the publish-failure degraded path)
            # so it never paints a broken layer row; vector inline-GeoJSON
            # LayerURIs (job-0175) and WMS-URL rasters pass untouched. The tool
            # result is unaffected — a dropped layer is still narrated honestly
            # and the retry loop can act.
            if isinstance(result, LayerURI):
                emit_layer = emit_layer_uri(result)
                if emit_layer is not None:
                    await self.add_loaded_layer(emit_layer)
            return result
        finally:
            _CURRENT_EMITTER.reset(token)

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
            duration_ms=s.duration_ms,
            # task-149: carry the two-card discriminator + Batch binding onto the
            # wire (defaults keep a plain tool card byte-identical).
            role=s.role,  # type: ignore[arg-type]
            batch_job_id=s.batch_job_id,
            batch_status=s.batch_status,
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
            duration_ms=s.duration_ms,
            # task-149: mirror the card-kind fields onto the persisted/replayed
            # summary so the compute card survives a reconnect / cold-case view.
            role=s.role,  # type: ignore[arg-type]
            batch_job_id=s.batch_job_id,
            batch_status=s.batch_status,
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

    async def _emit_terminal_pipeline_state(self) -> None:
        """Emit the pipeline-state for a TERMINAL transition, best-effort.

        J-B-part-i: a terminal ``mark_failed`` / ``mark_complete`` /
        ``mark_cancelled`` emits the red/green/yellow card. If the WS is dead or
        mid-cycling, the underlying ``_send`` raises ConnectionClosed* — that
        would ABORT the terminal transition and LOSE the card. We:

          1. snapshot the terminal payload so ``rebind_sink`` can REPLAY it onto a
             reconnected socket (per-Case durability / replay-on-reconnect), and
          2. swallow ONLY the connection-closed class (mirrors the best-effort
             pattern in ``workflows.solve_progress`` and the server sink) so the
             state transition itself always completes; any OTHER exception (a
             real logic/serialization error) still propagates loudly.
        """
        if self._pipeline_id is None:
            # Same defensive contract as _emit_pipeline_state — a terminal emit
            # with no open pipeline is a programming error at the call site.
            raise EmitterError(
                "_emit_terminal_pipeline_state called with no open pipeline; "
                "call start_pipeline / add_step first"
            )
        payload = PipelineStatePayload(
            pipeline_id=self._pipeline_id,
            steps=[self._to_wire_step(sid) for sid in self._step_order],
        )
        # Stash the LAST terminal snapshot so a sink rebind (reconnect) can
        # replay it — a RENDERED/terminal card stays surfaced across a WS blip.
        self._last_terminal_pipeline_payload = payload
        try:
            await self._send("pipeline-state", payload)
        except _CONNECTION_CLOSED_EXC:  # type: ignore[misc]
            # Dead / cycling socket — best-effort drop. The terminal STATE is
            # already recorded on the step; the snapshot above replays on the
            # next sink rebind so the card is not lost.
            logger.debug(
                "emitter: terminal pipeline-state send failed on a closed "
                "socket (best-effort drop; will replay on rebind) session=%s "
                "pipeline_id=%s",
                self.session_id,
                self._pipeline_id,
            )

    async def _send(self, message_type: str, payload: Any) -> None:
        env = Envelope(
            type=message_type,
            session_id=self.session_id,
            # job-0277: stamp the owning Case so the web routes this to the
            # right stream even after a mid-turn Case switch.
            case_id=current_turn_case(),
            payload=payload,
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


# --------------------------------------------------------------------------- #
# Two-card sim observability composer helpers (task-149)
# --------------------------------------------------------------------------- #
#
# Shared by BOTH off-box-solver composers (model_urban_flood_swmm /
# model_flood_scenario) so the SWMM and SFINCS Batch dispatches mint the same
# two cards: a "Dispatch" tool card recording the submit (lands complete
# immediately) + a "Sim" compute card bound to the Batch jobId whose live
# ``batch_status`` the wait-loop poller feeds. Pure thin orchestration over the
# emitter transition methods; lives here (not in a composer) so the logic is
# defined + tested once and the composer edits stay minimal.


async def mint_dispatch_and_sim_cards(
    *,
    emitter: "PipelineEmitter | None",
    solver: str,
    handle: Any,
    compute_class: str | None = None,
) -> str | None:
    """Mint the Dispatch (tool) + Sim (compute) cards for an off-box solve.

    task-149: ``handle`` is the ``ExecutionHandle`` from ``run_solver`` /
    ``submit_sfincs_quadtree`` — its ``workflows_execution_id`` is the AWS Batch
    ``jobId`` the sim card binds to and the wait-loop describes. Card 1 is a
    plain tool step (``add_step`` -> ``mark_complete``) recording the submit;
    card 2 is the ``role="compute"`` step bound to the jobId, left running.

    Returns the SIM step's id so the composer can point the solver emitter
    binding at it (so the wait-loop's phase ticks land on the right card) and
    route the terminal there. Best-effort: ``emitter is None`` (direct/smoke/unit
    call) OR any emit failure returns ``None`` and the solve proceeds unchanged —
    the two cards are an observability affordance, never a correctness gate.
    """
    if emitter is None:
        return None
    job_id = str(getattr(handle, "workflows_execution_id", "") or "")
    backend = str(getattr(handle, "workflow_name", "") or "aws-batch")
    try:
        # Card 1 "Dispatch": a normal tool step recording the submit.
        dispatch_label = f"Dispatch {solver} solve"
        if compute_class:
            dispatch_label = f"{dispatch_label} ({compute_class})"
        dispatch_id = await emitter.add_step(
            name=dispatch_label, tool_name=f"{solver}:dispatch"
        )
        await emitter.mark_running(dispatch_id)
        await emitter.mark_complete(dispatch_id)
        # Card 2 "Sim": the off-box compute card bound to the Batch jobId.
        sim_id = await emitter.add_compute_step(
            name=f"{solver} solve",
            tool_name=f"{solver}:solve",
            batch_job_id=job_id,
            batch_status="SUBMITTED",
        )
        logger.info(
            "two-card sim observability: minted dispatch + compute cards "
            "solver=%s backend=%s jobId=%s sim_step_id=%s",
            solver,
            backend,
            job_id,
            sim_id,
        )
        return sim_id
    except Exception as exc:  # noqa: BLE001 — observability, never break the solve
        logger.warning("mint_dispatch_and_sim_cards failed (non-fatal): %s", exc)
        return None


async def route_sim_terminal(
    emitter: "PipelineEmitter | None",
    sim_step_id: str | None,
    *,
    run_result: Any,
) -> None:
    """Drive the SIM compute card to its terminal state (task-149).

    ``run_result`` is the ``RunResult`` from ``wait_for_completion`` (or ``None``
    on a cancel): ``status == "complete"`` -> ``mark_complete`` (green),
    ``status == "cancelled"`` / a cancel (``run_result is None``) ->
    ``mark_cancelled`` (yellow), anything else -> ``mark_failed`` (red, carrying
    the RunResult's open-set error_code/message). Uses the emitter's terminal
    transition methods, which are J-B-i best-effort on a dead socket (the red /
    green card survives a WS cycle + replays on rebind). No-op when the emitter
    or the sim step is absent. Best-effort: an emit failure is swallowed so the
    composer's own non-complete guard still raises the typed workflow error."""
    if emitter is None or not sim_step_id:
        return
    try:
        status = str(getattr(run_result, "status", "") or "") if run_result is not None else ""
        if run_result is None or status == "cancelled":
            await emitter.mark_cancelled(sim_step_id)
        elif status == "complete":
            await emitter.mark_complete(sim_step_id)
        else:
            error_code = (
                getattr(run_result, "error_code", None) or (status.upper() if status else "SOLVER_FAILED")
            )
            error_message = (
                getattr(run_result, "error_message", None)
                or getattr(run_result, "cancellation_reason", None)
                or f"solver run {status or 'failed'}"
            )
            await emitter.mark_failed(
                sim_step_id, error_code=str(error_code), error_message=str(error_message)
            )
    except Exception as exc:  # noqa: BLE001 — observability, never break the solve
        logger.warning("route_sim_terminal failed (non-fatal): %s", exc)
