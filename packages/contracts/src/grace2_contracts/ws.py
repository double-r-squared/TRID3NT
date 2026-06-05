"""WebSocket protocol: envelope + every message type (SRS Appendix A, FR-AS-5).

All messages share the A.1 envelope (``type``/``id``/``ts``/``session_id``/
``payload``). ``type`` is kebab-case; ``id`` is a ULID; ``ts`` is ISO-8601 ``Z``;
``payload`` is always an object (``{}`` when empty).

This module defines:
- ``Envelope[PayloadT]``: the generic wire wrapper.
- One ``*Payload`` model per message type (A.3, A.4, A.4b).
- The ``map-command`` internal ``command`` discriminator (A.4) with one args
  model per command.
- ``ErrorCode``: the A.6 SCREAMING_SNAKE_CASE error-code enum.
- The ``research_mode`` field on ``user-message`` (orchestrator pinned
  toggle-carrier seam, FR-WC-15) — an Appendix A amendment; see the report's
  amendment log for the exact proposed SRS diff.

Invariants this module is responsible for:
- **9. No cost theater.** ``ConfirmationRequestPayload`` carries no cost field.
- **8. Cancellation is first-class.** ``cancelled`` is a distinct ``state`` in
  ``pipeline-state`` step states, separate from ``failed``.
"""

from __future__ import annotations

from typing import Generic, Literal, TypeVar

from pydantic import Field

from .common import (
    BBox,
    GraceModel,
    ULIDStr,
    UTCDatetime,
    new_ulid,
    now_utc,
)

__all__ = [
    "Envelope",
    "ErrorCode",
    # client -> agent (A.3)
    "ResearchMode",
    "UserMessagePayload",
    "CancelPayload",
    "ConfirmResponsePayload",
    "SessionResumePayload",
    # client -> agent (A.4b)
    "SpatialInputResponsePayload",
    "DisambiguationResponsePayload",
    "ClarificationResponsePayload",
    # agent -> client (A.4)
    "AgentMessageChunkPayload",
    "ToolCallStartPayload",
    "ToolCallProgressPayload",
    "ToolCallCompletePayload",
    "ToolCallFailedPayload",
    "PipelineStepState",
    "PipelineStep",
    "PipelineStatePayload",
    "MapCommandPayload",
    "ConfirmationRequestPayload",
    "SessionStatePayload",
    "ErrorPayload",
    "LocationResolvedPayload",
    "ReferenceLayer",
    "SuggestedView",
    "SpatialInputRequestPayload",
    "DisambiguationCandidate",
    "DisambiguationRequestPayload",
    "ClarificationOption",
    "ClarificationRequestPayload",
    # map-command args (A.4)
    "LoadLayerArgs",
    "RemoveLayerArgs",
    "SetLayerVisibilityArgs",
    "SetLayerOpacityArgs",
    "SetLayerOrderArgs",
    "ZoomToArgs",
    "SetTemporalConfigArgs",
    "StartAnimationArgs",
    "StopAnimationArgs",
    "InvalidateTilesArgs",
    "MapTemporal",
    # registry
    "CLIENT_TO_AGENT_PAYLOADS",
    "AGENT_TO_CLIENT_PAYLOADS",
    "ALL_PAYLOADS",
]


# --------------------------------------------------------------------------- #
# Envelope (A.1)
# --------------------------------------------------------------------------- #

PayloadT = TypeVar("PayloadT", bound=GraceModel)


class Envelope(GraceModel, Generic[PayloadT]):
    """The shared message envelope (A.1).

    ``type`` is the kebab-case discriminator; it is set per message type by the
    caller (the agent service / web client serialize the right value). ``id`` and
    ``ts`` default to a fresh ULID and current UTC. ``payload`` is always an
    object.
    """

    type: str  # kebab-case discriminator (see ``*_TYPE`` on payloads)
    id: ULIDStr = Field(default_factory=new_ulid)
    ts: UTCDatetime = Field(default_factory=now_utc)
    session_id: ULIDStr
    payload: PayloadT


# --------------------------------------------------------------------------- #
# Error codes (A.6)
# --------------------------------------------------------------------------- #

ErrorCode = Literal[
    "AUTH_FAILED",
    "RATE_LIMITED",
    "INTERNAL_ERROR",
    "LLM_UNAVAILABLE",
    "TOOL_NOT_FOUND",
    "TOOL_PARAMS_INVALID",
    "TOOL_TIMEOUT",
    "DEM_SOURCE_UNAVAILABLE",
    "SOLVER_FAILED",
    "CONFIRMATION_TIMEOUT",
    "SPATIAL_INPUT_TIMEOUT",
    "DISAMBIGUATION_TIMEOUT",
    "CLARIFICATION_TIMEOUT",
    "USER_INPUT_CANCELLED",
    "CANCELLED",
]


# =========================================================================== #
# Client -> Agent messages (A.3)
# =========================================================================== #

# Research-mode toggle carrier (FR-WC-15 / orchestrator pinned seam). v0.1
# always runs research mode regardless; the carrier is pinned now so nobody
# invents a second path. "deep_research" selection in v0.1 proceeds in research
# mode (FR-HEP-4). This is an Appendix A amendment — see report amendment log.
ResearchMode = Literal["research", "deep_research"]


class UserMessagePayload(GraceModel):
    """``user-message`` (A.3): user-submitted text input."""

    MESSAGE_TYPE = "user-message"

    text: str
    research_mode: ResearchMode = "research"  # Appendix A amendment (FR-WC-15)


class CancelPayload(GraceModel):
    """``cancel`` (A.3): cancel the in-flight pipeline."""

    MESSAGE_TYPE = "cancel"

    reason: str | None = None


class ConfirmResponsePayload(GraceModel):
    """``confirm-response`` (A.3): user response to a confirmation-request."""

    MESSAGE_TYPE = "confirm-response"

    request_id: ULIDStr
    approved: bool


class SessionResumePayload(GraceModel):
    """``session-resume`` (A.3): resume an existing session (id in envelope)."""

    MESSAGE_TYPE = "session-resume"


# =========================================================================== #
# Client -> Agent (user input responses) (A.4b)
# =========================================================================== #


class SpatialInputResponsePayload(GraceModel):
    """``spatial-input-response`` (A.4b): user picked a geometry, or cancelled.

    For a geometry response: ``geometry_type`` + ``coordinates`` are set
    (``[lon, lat]`` for point, ``[minLon, minLat, maxLon, maxLat]`` for bbox).
    For a cancellation: ``cancelled=True`` and the geometry fields stay None.
    """

    MESSAGE_TYPE = "spatial-input-response"

    request_id: ULIDStr
    geometry_type: Literal["point", "bbox"] | None = None
    coordinates: list[float] | None = None
    cancelled: bool = False


class DisambiguationResponsePayload(GraceModel):
    """``disambiguation-response`` (A.4b): user chose a candidate, or cancelled."""

    MESSAGE_TYPE = "disambiguation-response"

    request_id: ULIDStr
    candidate_id: str | None = None
    cancelled: bool = False


class ClarificationResponsePayload(GraceModel):
    """``clarification-response`` (A.4b): user chose an option, or cancelled."""

    MESSAGE_TYPE = "clarification-response"

    request_id: ULIDStr
    option_id: str | None = None
    cancelled: bool = False


# =========================================================================== #
# Agent -> Client messages (A.4)
# =========================================================================== #


class AgentMessageChunkPayload(GraceModel):
    """``agent-message-chunk`` (A.4): a streamed token group from the LLM."""

    MESSAGE_TYPE = "agent-message-chunk"

    message_id: ULIDStr
    delta: str  # new content since the last chunk (not accumulated)
    done: bool = False


class ToolCallStartPayload(GraceModel):
    """``tool-call-start`` (A.4): a tool invocation has begun."""

    MESSAGE_TYPE = "tool-call-start"

    call_id: ULIDStr
    step_id: ULIDStr
    tool_name: str
    # tool_category vocabulary (FR-TA-3 convention; open enum). Mirrors the tool
    # categories in FR-TA-2. See report OQ-S5 for the documented vocabulary.
    tool_category: str
    params: dict = Field(default_factory=dict)  # sanitized parameters


class ToolCallProgressPayload(GraceModel):
    """``tool-call-progress`` (A.4): optional progress for an in-flight tool."""

    MESSAGE_TYPE = "tool-call-progress"

    call_id: ULIDStr
    percent: int | None = Field(default=None, ge=0, le=100)
    status: str | None = None


class ToolCallCompletePayload(GraceModel):
    """``tool-call-complete`` (A.4): a tool finished successfully.

    ``metrics`` is tool-specific structured data (invariant 1: the numbers the
    narrative cites live here, never free text). For the flood depth tool this
    carries ``FloodMetrics``-shaped fields; the full result body lives in GCS /
    MongoDB and is referenced by ``result_uri``.
    """

    MESSAGE_TYPE = "tool-call-complete"

    call_id: ULIDStr
    result_summary: str  # human-readable one-liner for chat display
    result_uri: str | None = None  # present when the result is a stored artifact
    metrics: dict = Field(default_factory=dict)  # tool-specific structured data


class ToolCallFailedPayload(GraceModel):
    """``tool-call-failed`` (A.4): a tool errored out."""

    MESSAGE_TYPE = "tool-call-failed"

    call_id: ULIDStr
    error_code: str  # enum-like string (per tool category; open)
    message: str  # human-readable, surfaced in chat
    retryable: bool = False


# pipeline-state (A.4) ------------------------------------------------------- #

# cancelled is a distinct terminal state, separate from failed (invariant 8).
PipelineStepState = Literal["pending", "running", "complete", "failed", "cancelled"]


class PipelineStep(GraceModel):
    """One step in the pipeline snapshot."""

    step_id: ULIDStr
    name: str
    tool_name: str
    state: PipelineStepState
    started_at: UTCDatetime | None = None
    completed_at: UTCDatetime | None = None
    progress_percent: int | None = Field(default=None, ge=0, le=100)


class PipelineStatePayload(GraceModel):
    """``pipeline-state`` (A.4): full snapshot of the current pipeline.

    The full snapshot replaces the client's pipeline view on each message;
    deltas are not used.
    """

    MESSAGE_TYPE = "pipeline-state"

    pipeline_id: ULIDStr
    steps: list[PipelineStep] = Field(default_factory=list)


# map-command (A.4) ---------------------------------------------------------- #


class MapTemporal(GraceModel):
    """Temporal block for ``load-layer`` args."""

    start: UTCDatetime
    end: UTCDatetime
    step_seconds: int = Field(gt=0)


class LoadLayerArgs(GraceModel):
    """``load-layer`` args. Field-for-field alignable with ``LayerURI``."""

    COMMAND = "load-layer"

    layer_id: str
    wms_url: str
    style_preset: str
    temporal: MapTemporal | None = None


class RemoveLayerArgs(GraceModel):
    COMMAND = "remove-layer"
    layer_id: str


class SetLayerVisibilityArgs(GraceModel):
    COMMAND = "set-layer-visibility"
    layer_id: str
    visible: bool


class SetLayerOpacityArgs(GraceModel):
    COMMAND = "set-layer-opacity"
    layer_id: str
    opacity: float = Field(ge=0.0, le=1.0)


class SetLayerOrderArgs(GraceModel):
    COMMAND = "set-layer-order"
    layer_ids: list[str]  # ordered, top to bottom


class ZoomToArgs(GraceModel):
    COMMAND = "zoom-to"
    bbox: BBox


class SetTemporalConfigArgs(GraceModel):
    COMMAND = "set-temporal-config"
    layer_id: str
    start: UTCDatetime
    end: UTCDatetime
    step_seconds: int = Field(gt=0)
    current: UTCDatetime | None = None


class StartAnimationArgs(GraceModel):
    COMMAND = "start-animation"
    layer_id: str
    speed: Literal[0.5, 1, 2, 5, 10] | None = None


class StopAnimationArgs(GraceModel):
    COMMAND = "stop-animation"
    layer_id: str


class InvalidateTilesArgs(GraceModel):
    COMMAND = "invalidate-tiles"
    layer_id: str | None = None  # omit to invalidate all


# map-command command vocabulary (open enum).
MapCommand = Literal[
    "load-layer",
    "remove-layer",
    "set-layer-visibility",
    "set-layer-opacity",
    "set-layer-order",
    "zoom-to",
    "set-temporal-config",
    "start-animation",
    "stop-animation",
    "invalidate-tiles",
]


class MapCommandPayload(GraceModel):
    """``map-command`` (A.4): one umbrella type with a ``command`` discriminator.

    ``args`` is the command-specific args object (one of the ``*Args`` models
    above). It is kept as a ``dict`` at the envelope level; the consumer
    validates it against the matching ``*Args`` model by ``command``. This is
    intentional: ten near-identical sibling top-level types would create churn
    (A.7 rationale).
    """

    MESSAGE_TYPE = "map-command"

    command: MapCommand
    args: dict = Field(default_factory=dict)


class ConfirmationRequestPayload(GraceModel):
    """``confirmation-request`` (A.4): agent needs user approval.

    No cost field anywhere (invariant 9 / A.4): surfacing approximate cost is
    worse than none.
    """

    MESSAGE_TYPE = "confirmation-request"

    request_id: ULIDStr
    title: str
    description: str
    estimated_duration_seconds: int | None = None
    default_timeout_seconds: int = 60


class SessionStatePayload(GraceModel):
    """``session-state`` (A.4): lets the client reconstruct the session.

    The nested shapes are the JSON serialization of the Appendix D.6 models
    (``ChatMessage``, ``ProjectLayerSummary``, ``PipelineSnapshot``,
    ``MapView``). They are carried as plain ``dict``/``list`` here to avoid a
    circular contract dependency between ws.py and collections.py; the agent
    serializes the real D.6 models into them. See report OQ-S4.
    """

    MESSAGE_TYPE = "session-state"

    chat_history: list[dict] = Field(default_factory=list)
    loaded_layers: list[dict] = Field(default_factory=list)
    pipeline_history: list[dict] = Field(default_factory=list)
    current_pipeline: dict | None = None
    map_view: dict | None = None


class ErrorPayload(GraceModel):
    """``error`` (A.4): global error not tied to a specific tool call."""

    MESSAGE_TYPE = "error"

    error_code: ErrorCode
    message: str
    retryable: bool = False
    retry_after_seconds: int | None = None


class LocationResolvedPayload(GraceModel):
    """``location-resolved`` (A.4): a meaningful location was resolved.

    Emitted as a side effect of resolution-producing tools; the client
    auto-snaps the map to ``bbox``.
    """

    MESSAGE_TYPE = "location-resolved"

    resolved_id: ULIDStr
    label: str
    bbox: BBox
    granularity: Literal["country", "region", "state", "city", "facility", "bbox"]
    source: Literal[
        "news_extraction", "user_prompt", "disambiguation", "geocoding", "tool_result"
    ]
    animate: bool = True


# spatial-input-request (A.4) ------------------------------------------------ #


class ReferenceLayer(GraceModel):
    """An optional helper layer shown only during a spatial-input request."""

    layer_id: str
    wms_url: str
    style_preset: str


class SuggestedView(GraceModel):
    """Where the client zooms to make picking easier."""

    bbox: BBox
    zoom: float


class SpatialInputRequestPayload(GraceModel):
    """``spatial-input-request`` (A.4): agent needs the user to pick a geometry."""

    MESSAGE_TYPE = "spatial-input-request"

    request_id: ULIDStr
    mode: Literal["point", "bbox"]  # polygon deferred
    title: str
    description: str
    suggested_view: SuggestedView | None = None
    reference_layers: list[ReferenceLayer] = Field(default_factory=list)
    default_timeout_seconds: int = 300


# disambiguation-request (A.4) ----------------------------------------------- #


class DisambiguationCandidate(GraceModel):
    """One enumerated candidate for an ambiguous entity."""

    id: str
    label: str
    bbox: BBox
    context: str | None = None


class DisambiguationRequestPayload(GraceModel):
    """``disambiguation-request`` (A.4): pick one of several candidates."""

    MESSAGE_TYPE = "disambiguation-request"

    request_id: ULIDStr
    title: str
    description: str
    candidates: list[DisambiguationCandidate]
    default_timeout_seconds: int = 120


# clarification-request (A.4) ------------------------------------------------ #


class ClarificationOption(GraceModel):
    """One substantively-different path the agent could take. ``description``
    is required (A.4): it shows the user what each path produces."""

    id: str
    label: str
    description: str


class ClarificationRequestPayload(GraceModel):
    """``clarification-request`` (A.4): choose between different response paths."""

    MESSAGE_TYPE = "clarification-request"

    request_id: ULIDStr
    question: str
    options: list[ClarificationOption] = Field(min_length=2, max_length=4)
    default_timeout_seconds: int = 60


# =========================================================================== #
# Registries: kebab-case type -> payload model
# =========================================================================== #

CLIENT_TO_AGENT_PAYLOADS: dict[str, type[GraceModel]] = {
    UserMessagePayload.MESSAGE_TYPE: UserMessagePayload,
    CancelPayload.MESSAGE_TYPE: CancelPayload,
    ConfirmResponsePayload.MESSAGE_TYPE: ConfirmResponsePayload,
    SessionResumePayload.MESSAGE_TYPE: SessionResumePayload,
    SpatialInputResponsePayload.MESSAGE_TYPE: SpatialInputResponsePayload,
    DisambiguationResponsePayload.MESSAGE_TYPE: DisambiguationResponsePayload,
    ClarificationResponsePayload.MESSAGE_TYPE: ClarificationResponsePayload,
}

AGENT_TO_CLIENT_PAYLOADS: dict[str, type[GraceModel]] = {
    AgentMessageChunkPayload.MESSAGE_TYPE: AgentMessageChunkPayload,
    ToolCallStartPayload.MESSAGE_TYPE: ToolCallStartPayload,
    ToolCallProgressPayload.MESSAGE_TYPE: ToolCallProgressPayload,
    ToolCallCompletePayload.MESSAGE_TYPE: ToolCallCompletePayload,
    ToolCallFailedPayload.MESSAGE_TYPE: ToolCallFailedPayload,
    PipelineStatePayload.MESSAGE_TYPE: PipelineStatePayload,
    MapCommandPayload.MESSAGE_TYPE: MapCommandPayload,
    ConfirmationRequestPayload.MESSAGE_TYPE: ConfirmationRequestPayload,
    SessionStatePayload.MESSAGE_TYPE: SessionStatePayload,
    ErrorPayload.MESSAGE_TYPE: ErrorPayload,
    LocationResolvedPayload.MESSAGE_TYPE: LocationResolvedPayload,
    SpatialInputRequestPayload.MESSAGE_TYPE: SpatialInputRequestPayload,
    DisambiguationRequestPayload.MESSAGE_TYPE: DisambiguationRequestPayload,
    ClarificationRequestPayload.MESSAGE_TYPE: ClarificationRequestPayload,
}

ALL_PAYLOADS: dict[str, type[GraceModel]] = {
    **CLIENT_TO_AGENT_PAYLOADS,
    **AGENT_TO_CLIENT_PAYLOADS,
}

# map-command command -> args model
MAP_COMMAND_ARGS: dict[str, type[GraceModel]] = {
    LoadLayerArgs.COMMAND: LoadLayerArgs,
    RemoveLayerArgs.COMMAND: RemoveLayerArgs,
    SetLayerVisibilityArgs.COMMAND: SetLayerVisibilityArgs,
    SetLayerOpacityArgs.COMMAND: SetLayerOpacityArgs,
    SetLayerOrderArgs.COMMAND: SetLayerOrderArgs,
    ZoomToArgs.COMMAND: ZoomToArgs,
    SetTemporalConfigArgs.COMMAND: SetTemporalConfigArgs,
    StartAnimationArgs.COMMAND: StartAnimationArgs,
    StopAnimationArgs.COMMAND: StopAnimationArgs,
    InvalidateTilesArgs.COMMAND: InvalidateTilesArgs,
}
