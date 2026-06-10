"""Case persistence envelopes (FR-MP-6, Appendix A.6/A.7 amendments, sprint-12).

A "Case" is the user-facing name for a `projects` document (FR-MP-5 nomenclature
stays canonical in storage). This module owns the **wire-shape envelopes** that
back the FR-MP-6 Case UX flow:

- ``CaseSummary`` — the left-rail entity (denormalized from ``ProjectDocument``).
- ``CaseChatMessage`` — a single persisted chat exchange in a Case session
  (extends ``ChatMessage`` semantics with per-turn layer/map-command emissions
  so the rehydration replay can re-bind a Case session deterministically).
- ``CaseSessionState`` — the rehydration envelope returned when a user opens
  a Case (the "replay envelope").
- ``CaseListEnvelope`` / ``CaseOpenEnvelope`` — server -> client A.4 messages
  for the left-rail listing and Case open/rehydrate transitions.
- ``CaseCommandEnvelope`` — client -> server A.3 message for Case lifecycle
  commands (``create`` / ``select`` / ``rename`` / ``archive`` / ``delete``).

This module is **Wave 1 of sprint-12-mega**: every downstream Wave 2 Case UX job
(agent and web specialists) consumes these shapes. The shapes are pydantic v2
``GraceModel`` subclasses (the project-wide convention; see ``common.py`` and
``schema.md`` "pydantic v2, not tentative anymore"). The kickoff sketched the
shapes as ``dataclass``; the conservative-interpretation translation to
``GraceModel`` is logged as ``OQ-0099-DATACLASS-VS-PYDANTIC`` in the report.

Invariants this module is responsible for:

- **8. Cancellation is first-class.** ``case-command`` carries no ad-hoc
  cancellation field; cancellation flows through the existing ``cancel``
  message (Appendix A.3), not a Case lifecycle command.
- **9. No cost theater.** No cost field anywhere on Case envelopes — neither
  on ``CaseSummary`` (no aggregate cost), nor on ``CaseCommandEnvelope``, nor
  on the rehydration replay. Cost surfacing is forbidden everywhere (A.4 /
  invariant 9).

SRS references:
- FR-MP-6 (Case UX flow) — `docs/srs/03-functional-requirements.md`.
- Appendix A.3 (client -> server messages) and A.4 (server -> client messages)
  for the envelope-type discipline.
- Appendix D.2 (``projects``) and D.6 (``sessions``) for the underlying storage
  shapes the Case envelopes denormalize from.
"""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import Field

from .common import (
    BBox,
    GraceModel,
    ULIDStr,
    UTCDatetime,
)

__all__ = [
    # Case persistence envelopes (FR-MP-6)
    "CaseStatus",
    "CaseSummary",
    "CaseChatMessage",
    "CaseSessionState",
    "ToolCardRecord",
    "ToolCardState",
    # WebSocket envelopes (A.4 / A.3 amendments)
    "CaseListEnvelopePayload",
    "CaseOpenEnvelopePayload",
    "CaseCommand",
    "CaseCommandEnvelopePayload",
]


# --------------------------------------------------------------------------- #
# Case persistence envelopes (FR-MP-6)
# --------------------------------------------------------------------------- #

# Closed enum: Case lifecycle status. ``deleted`` is a soft-delete tombstone
# that mirrors ``ProjectDocument.deleted_at`` (D.2). The list is intentionally
# closed at v0.1 — a new status is an SRS amendment, not a silent open-enum.
CaseStatus = Literal["active", "archived", "deleted"]


class CaseSummary(GraceModel):
    """Top-level Case record — the left-rail entity (FR-MP-6 landing state).

    Denormalized from ``ProjectDocument`` (D.2) so the client can render the
    Cases list without joining sessions/runs. The Case identifier maps 1:1 to
    ``projects._id`` (FR-MP-6: UI labels say "Case", schema/code say
    "Project"); ``case_id`` here IS the ``project_id``.

    ``qgs_project_uri`` is lazy-init by design — a fresh Case has no published
    ``.qgs`` yet; ``publish_layer`` writes the URI on first layer emission
    (see ``ProjectDocument.qgs_uri``, FR-MP-3).

    Invariant 9: no cost field anywhere. The summary carries no aggregate
    cost / spent / quota fields.
    """

    schema_version: Literal["v1"] = "v1"

    case_id: ULIDStr  # ULID; maps 1:1 to projects._id (FR-MP-5 / FR-MP-6)
    title: str  # user-edited; ``ProjectDocument.name`` is the storage field
    created_at: UTCDatetime  # ISO-8601 UTC
    updated_at: UTCDatetime  # ISO-8601 UTC
    status: CaseStatus = "active"

    bbox: BBox | None = None  # [minLon, minLat, maxLon, maxLat] EPSG:4326
    # Primary hazard label is denormalized from the Case's runs; open enum so
    # registering a new hazard does not break the Case envelope (Decision G).
    primary_hazard: str | None = None

    # Layer summary is a flat list of layer_ids the Case currently has loaded.
    # Full layer detail lives in CaseSessionState.loaded_layers on Case open;
    # the left-rail summary stays cheap.
    layer_summary: list[str] = Field(default_factory=list)

    # job-0172 Part B: per-Case persisted ``ProjectLayerSummary`` dicts. The
    # PipelineEmitter holds these per-connection in memory; we mirror them
    # onto the Case document so a Case re-open (fresh connection, fresh
    # emitter) rehydrates ``loaded_layers`` deterministically rather than
    # showing an empty LayerPanel. Entries are full ``ProjectLayerSummary``
    # ``model_dump(mode="json")`` shapes (matches Appendix D.2 envelope
    # discipline + ``CaseSessionState.loaded_layers``). Dedup is by ``uri``:
    # republishing the same layer overwrites the existing entry in place.
    loaded_layer_summaries: list[dict] = Field(default_factory=list)

    qgs_project_uri: str | None = None  # gs://.../{case_id}.qgs (lazy-init)


# Closed enum: terminal tool-card outcomes that are worth replaying. The set
# is intentionally minimal at v0.1 (job-0267): a cancelled dispatch produces
# NO persisted card (Invariant 8 — cancellation aborts the turn; there is no
# completed exchange to replay), and transient pending/running states are
# live-wire-only (``pipeline-state`` envelopes), never persisted.
ToolCardState = Literal["complete", "failed"]


class ToolCardRecord(GraceModel):
    """Replayable record of ONE tool dispatch inside a Case turn (job-0267).

    The live UI renders tool usage cards inline in the chat scroll from
    ``pipeline-state`` envelopes (``feedback_chat_tool_interleave``); those
    envelopes are wire-only and were LOST on Case reopen. This record is the
    persisted twin: minimal terminal state of one dispatched registry tool so
    the rehydration replay (``CaseSessionState.chat_history``) can re-render
    the card without replaying the live pipeline.

    ``duration_ms`` / ``started_at`` mirror the authoritative job-0264 stamps
    on ``PipelineStepSummary`` (the agent copies them from the emitter's
    terminal step, falling back to a wall-clock measure around the dispatch).
    ``label`` is the human-facing step name the live card showed (the registry
    display name); the web client MAY override it with its own humanizer keyed
    on ``tool_name``.
    """

    schema_version: Literal["v1"] = "v1"

    tool_name: str  # registry tool name (e.g. "fetch_3dep_dem")
    state: ToolCardState
    started_at: UTCDatetime | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    label: str | None = None  # human-facing card label at dispatch time


class CaseChatMessage(GraceModel):
    """One persisted chat exchange in a Case session (FR-MP-6 persistence).

    Mirrors ``ChatMessage`` (D.6) but carries the per-turn **layer / map-command
    emissions** so Case rehydration can replay deterministically: when a user
    re-opens a Case, the client re-binds layers via the same emission sequence
    the original turn produced.

    Invariant 1 (determinism boundary): ``map_command_emissions`` carries the
    typed map-command args (``LoadLayerArgs`` / ``ZoomToArgs`` / etc., dumped
    via ``model_dump(mode="json")``) so the replay path doesn't re-parse free
    text. We hold them as ``dict`` here to avoid a cross-module import cycle
    (ws.py imports from common only). The agent service round-trips each entry
    through ``MAP_COMMAND_ARGS`` validation before write.

    job-0267 (full-stream persistence): ``role`` gains the ``"tool"`` value —
    one ``role="tool"`` message per dispatched registry tool, interleaved with
    the ``user`` / ``agent`` turns by ``created_at``, so a Case reopen replays
    the FULL stream (user prompt → tool cards → agent narration) in arrival
    order. For tool messages the typed payload is ``tool_card``
    (``ToolCardRecord``) — the contract-blessed access path the web renderer
    consumes — and ``content`` carries the same record as a JSON string
    (belt-and-suspenders for non-contract consumers; never free text).
    ``tool_card`` is ``None`` for every other role, and pre-job-0267 documents
    (no ``tool_card`` field at all) validate unchanged.
    """

    schema_version: Literal["v1"] = "v1"

    message_id: ULIDStr  # matches the WS envelope id for agent messages
    case_id: ULIDStr  # owning Case
    role: Literal["user", "agent", "system", "tool"]
    content: str  # accumulated text after streaming completes

    # job-0267: typed tool-card payload; set IFF ``role == "tool"``.
    tool_card: ToolCardRecord | None = None

    # Link to the PipelineRecord (D.6 PipelineSnapshot) this turn dispatched,
    # if any. None for pure-chat turns that emitted no pipeline.
    pipeline_id: ULIDStr | None = None

    # Per-turn layer emissions: layer_ids the agent surfaced this turn so the
    # rehydration replay knows which layers to re-register.
    layer_emissions: list[str] = Field(default_factory=list)

    # Per-turn map-command emissions: ``[{"command": "...", "args": {...}}, ...]``.
    # The agent validates each entry against ``ws.MAP_COMMAND_ARGS`` at emit
    # time; here they are dicts to keep the contract acyclic.
    map_command_emissions: list[dict] = Field(default_factory=list)

    created_at: UTCDatetime


class CaseSessionState(GraceModel):
    """The rehydration envelope returned when a user opens a Case (FR-MP-6 resume).

    The client uses this to reconstruct the full Case session: the chat panel
    re-renders ``chat_history``, the LayerPanel re-registers ``loaded_layers``
    against QGIS Server (the published ``.qgs`` is the source-of-truth per
    FR-MP-3), the PipelineStrip reflects ``current_pipeline`` and the audit
    history reflects ``pipeline_history``.

    ``loaded_layers`` and ``pipeline_history`` / ``current_pipeline`` are kept
    as ``dict`` / ``list[dict]`` here to mirror the ``SessionStatePayload``
    shape (ws.SessionStatePayload) — collections.py owns the concrete
    ``ProjectLayerSummary`` / ``PipelineSnapshot`` / ``PipelineStepSummary``
    shapes; the agent serializes them into this envelope via
    ``model_dump(mode="json")`` before sending.
    """

    schema_version: Literal["v1"] = "v1"

    case: CaseSummary
    chat_history: list[CaseChatMessage] = Field(default_factory=list)
    loaded_layers: list[dict] = Field(default_factory=list)  # ProjectLayerSummary[]
    pipeline_history: list[dict] = Field(default_factory=list)  # PipelineSnapshot[]
    current_pipeline: dict | None = None  # PipelineSnapshot | None


# --------------------------------------------------------------------------- #
# WebSocket envelopes for Case lifecycle (A.4 / A.3 amendments)
# --------------------------------------------------------------------------- #


class CaseListEnvelopePayload(GraceModel):
    """``case-list`` (A.4 amendment): server -> client list of all Cases.

    Emitted on session connect (initial landing state per FR-MP-6) and
    refreshed after any Case lifecycle command (``create`` / ``rename`` /
    ``archive`` / ``delete``). The client renders the left rail from this list.

    ``envelope_type`` is a Literal discriminator (the message ``type`` on the
    A.1 envelope is ``"case-list"``); we mirror the field here as a typed
    literal so the payload is self-describing when serialized standalone.
    """

    MESSAGE_TYPE: ClassVar[str] = "case-list"

    envelope_type: Literal["case-list"] = "case-list"
    cases: list[CaseSummary] = Field(default_factory=list)


class CaseOpenEnvelopePayload(GraceModel):
    """``case-open`` (A.4 amendment): server -> client rehydrate selected Case.

    Emitted in response to a ``case-command`` with ``command=select`` (or on
    successful ``create``). ``session_state`` is ``None`` when the server
    cannot rehydrate (e.g. the Case was archived/deleted between list and
    select); the client falls back to the empty state in that case.
    """

    MESSAGE_TYPE: ClassVar[str] = "case-open"

    envelope_type: Literal["case-open"] = "case-open"
    session_state: CaseSessionState | None = None


# Closed enum: Case lifecycle commands. The set is closed at v0.1 — a new
# command is an SRS amendment (FR-MP-6) not a silent open-enum, because the
# server-side dispatch table needs to enumerate handlers.
CaseCommand = Literal["create", "select", "rename", "archive", "delete"]


class CaseCommandEnvelopePayload(GraceModel):
    """``case-command`` (A.3 amendment): client -> server Case lifecycle command.

    Fields:

    - ``command`` — one of ``create`` / ``select`` / ``rename`` / ``archive``
      / ``delete`` (closed enum).
    - ``case_id`` — required for every command except ``create`` (the server
      generates the ULID on create and replies with a ``case-open``).
    - ``args`` — command-specific args dict. For ``rename`` it carries
      ``{"title": "<new title>"}``; for ``create`` it MAY carry an initial
      ``{"title": "..."}`` hint. The server validates the args dict against
      the command-specific schema before dispatch; we keep it as ``dict`` at
      the envelope level to mirror the ``MapCommandPayload`` pattern (one
      umbrella type with a ``command`` discriminator).

    No cost field anywhere (invariant 9). No cancellation field — cancellation
    flows through the existing A.3 ``cancel`` message (invariant 8).
    """

    MESSAGE_TYPE: ClassVar[str] = "case-command"

    envelope_type: Literal["case-command"] = "case-command"
    command: CaseCommand
    case_id: ULIDStr | None = None
    args: dict = Field(default_factory=dict)
