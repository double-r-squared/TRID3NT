"""MongoDB collection schemas (SRS Appendix D, FR-MP-5, Decision F/L).

Five collections, each a pydantic model mapping to a BSON document. The wire/
storage form is ``model.model_dump(mode="json", by_alias=True)`` with the
document id serialized as ``_id``.

pydantic forbids a field literally named ``_id`` (leading underscore), so each
document model exposes ``id`` with ``alias="_id"`` and ``populate_by_name=True``:
construct with ``id=...`` (or ``_id=...``), dump with ``by_alias=True`` to get
``{"_id": ...}`` for Mongo. ``MONGO_DUMP_KWARGS`` captures the canonical dump
options.

Invariants this module is responsible for:
- **6. Metadata-payload pattern.** These schemas are the MongoDB side of the
  metadata-payload split; GCS holds payloads keyed by URIs stored here.
- **8. Cancellation is first-class.** ``RunDocument.status`` carries
  ``cancelled`` as a distinct terminal state.
- **9. No cost theater.** No cost field on ``runs`` or anywhere (D.7).

OQ-7 (embedding dimension) is surfaced in the report. The vector index configs
below use the SRS-stated default of 768 dims; ``infra`` provisions the indexes
to whatever the user lands after the recall-vs-cost check. They are documented
constants here, NOT a locked Atlas config.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import ConfigDict, Field

from .common import GraceModel, ULIDStr, UTCDatetime
from .event import EventMetadata

__all__ = [
    "DocModel",
    "ProjectLayerSummary",
    "ProjectDocument",
    "UserSpatialInput",
    "RunDocument",
    "ArticleDocument",
    "EventDocument",
    "ChatMessage",
    "ToolCallSummary",
    "PipelineStepSummary",
    "PipelineSnapshot",
    "MapView",
    "SessionDocument",
    "MONGO_DUMP_KWARGS",
    "EMBEDDING_MODEL_DEFAULT",
    "EMBEDDING_DIMENSIONS_DEFAULT",
    "RUNS_VECTOR_INDEX",
    "ARTICLES_VECTOR_INDEX",
    "EVENTS_VECTOR_INDEX",
    "VECTOR_INDEXES",
    "SESSIONS_TTL",
]


#: Canonical kwargs for producing the BSON/wire form of any document model.
MONGO_DUMP_KWARGS: dict[str, Any] = {"mode": "json", "by_alias": True}

#: Embedding model + default dimension shared across collections (D.7).
#: OQ-7: 768 is the SRS default; 256/128 trade recall for index size/cost. The
#: index configs below use this default and are NOT a locked Atlas config.
EMBEDDING_MODEL_DEFAULT = "text-embedding-005"
EMBEDDING_DIMENSIONS_DEFAULT = 768


class DocModel(GraceModel):
    """Base for collection documents that use ``_id`` aliasing.

    Adds ``populate_by_name=True`` on top of ``GraceModel`` so the id field can
    be set by either ``id`` or ``_id`` and dumped to ``_id`` with ``by_alias``.
    """

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        populate_by_name=True,
    )


# --------------------------------------------------------------------------- #
# D.2 projects
# --------------------------------------------------------------------------- #


class ProjectLayerSummary(GraceModel):
    """Denormalized layer entry on a project (and on session map state)."""

    layer_id: str
    name: str
    layer_type: Literal["raster", "vector"]
    uri: str
    style_preset: str
    visible: bool
    role: Literal["primary", "context", "input"]
    temporal: bool  # has WMS-T config


class ProjectDocument(DocModel):
    """``projects`` (D.2): metadata index over .qgs files in GCS."""

    schema_version: Literal["v1"] = "v1"

    id: ULIDStr = Field(alias="_id")  # the project_id used everywhere
    session_id: ULIDStr  # owning session
    qgs_uri: str  # gs://.../project_<id>.qgs (canonical)
    name: str  # human-readable
    description: str | None = None
    bbox: tuple[float, float, float, float] | None = None  # EPSG:4326
    hazard_types: list[str] = Field(default_factory=list)
    layers: list[ProjectLayerSummary] = Field(default_factory=list)
    created_at: UTCDatetime
    updated_at: UTCDatetime
    deleted_at: UTCDatetime | None = None  # soft delete


# --------------------------------------------------------------------------- #
# D.3 runs
# --------------------------------------------------------------------------- #


class UserSpatialInput(GraceModel):
    """A user-provided spatial input recorded on a run (FR-AS-10)."""

    request_id: ULIDStr  # the WebSocket request that solicited this input
    geometry_type: Literal["point", "bbox"]
    coordinates: list[float]  # [lon, lat] for point; bbox 4-tuple for bbox
    prompt_title: str
    submitted_at: UTCDatetime


class RunDocument(DocModel):
    """``runs`` (D.3): every solver execution or discovery operation.

    Embeds the full ``AssessmentEnvelope`` as ``assessment: dict`` (None until
    complete) — a dict, not a nested model, so envelope schema changes don't
    force a collection migration (D.7). Validation happens at the API boundary
    in the agent before write. ``status`` carries ``cancelled`` as a distinct
    terminal state (invariant 8). No cost field (invariant 9 / D.7).
    """

    schema_version: Literal["v1"] = "v1"

    id: ULIDStr = Field(alias="_id")  # this is the solver_run_id
    project_id: ULIDStr
    session_id: ULIDStr

    status: Literal["pending", "running", "complete", "failed", "cancelled"]
    started_at: UTCDatetime | None = None
    completed_at: UTCDatetime | None = None
    duration_seconds: float | None = None

    run_type: Literal["modeled", "discovered"]  # mirrors envelope_type
    hazard_type: str  # denormalized from envelope
    workflow_name: str  # denormalized from envelope

    bbox: tuple[float, float, float, float]  # denormalized for queries
    event_time_start: UTCDatetime | None = None
    event_time_end: UTCDatetime | None = None

    # Full AssessmentEnvelope as dict; None until status == "complete".
    assessment: dict | None = None

    embedding: list[float] | None = None
    embedding_model: str | None = None

    error_code: str | None = None
    error_message: str | None = None

    cancellation_reason: str | None = None
    cancelled_at: UTCDatetime | None = None

    user_spatial_inputs: list[UserSpatialInput] = Field(default_factory=list)

    event_id: ULIDStr | None = None  # if news-derived
    article_ids: list[ULIDStr] = Field(default_factory=list)  # if news-derived


# --------------------------------------------------------------------------- #
# D.4 articles
# --------------------------------------------------------------------------- #


class ArticleDocument(DocModel):
    """``articles`` (D.4): fetched news article corpus."""

    schema_version: Literal["v1"] = "v1"

    id: ULIDStr = Field(alias="_id")
    url: str
    url_hash: str  # SHA-256 of normalized URL for dedup
    title: str
    publisher: str | None = None
    author: str | None = None

    text: str  # extracted article text (cleaned)
    text_length: int = Field(ge=0)
    html_uri: str | None = None  # GCS URI if full HTML retained

    published_at: UTCDatetime | None = None
    fetched_at: UTCDatetime

    embedding: list[float] | None = None
    embedding_model: str | None = None

    extraction_status: Literal["pending", "extracted", "failed", "no_events"]
    extracted_event_ids: list[ULIDStr] = Field(default_factory=list)
    last_processed_at: UTCDatetime | None = None


# --------------------------------------------------------------------------- #
# D.5 events  (the collection schema *is* EventMetadata)
# --------------------------------------------------------------------------- #


class EventDocument(EventMetadata):
    """``events`` (D.5): an EventMetadata document. ``event_id`` is the ``_id``.

    The collection schema *is* the ``EventMetadata`` schema (Appendix C); no
    wrapper fields are added. The Mongo ``_id`` is ``event_id`` (a ULID); the
    write path sets ``_id = event_id`` at insert time. We do not alias here to
    keep ``EventMetadata`` a single shape across wire and storage.
    """


# --------------------------------------------------------------------------- #
# D.6 sessions
# --------------------------------------------------------------------------- #


class ToolCallSummary(GraceModel):
    """A completed/failed/cancelled tool call recorded in chat history."""

    call_id: ULIDStr
    tool_name: str
    state: Literal["complete", "failed", "cancelled"]
    result_summary: str | None = None
    result_uri: str | None = None
    error_code: str | None = None
    started_at: UTCDatetime
    completed_at: UTCDatetime | None = None


class ChatMessage(GraceModel):
    """One chat turn. ``message_id`` matches the WS message id for agent msgs."""

    message_id: ULIDStr
    role: Literal["user", "agent"]
    content: str  # for agent messages, final accumulated text after streaming
    tool_calls: list[ToolCallSummary] = Field(default_factory=list)
    created_at: UTCDatetime


class PipelineStepSummary(GraceModel):
    """A step in a persisted pipeline snapshot. ``cancelled`` is distinct."""

    step_id: ULIDStr
    name: str
    tool_name: str
    state: Literal["pending", "running", "complete", "failed", "cancelled"]
    started_at: UTCDatetime | None = None
    completed_at: UTCDatetime | None = None


class PipelineSnapshot(GraceModel):
    """A persisted pipeline run."""

    pipeline_id: ULIDStr
    started_at: UTCDatetime
    completed_at: UTCDatetime | None = None
    final_state: Literal["complete", "failed", "cancelled"] | None = None
    steps: list[PipelineStepSummary] = Field(default_factory=list)


class MapView(GraceModel):
    """Current client map view."""

    center: tuple[float, float]  # [lon, lat]
    zoom: float
    bbox: tuple[float, float, float, float]


class SessionDocument(DocModel):
    """``sessions`` (D.6): chat session state. TTL-cleaned via ``expires_at``."""

    schema_version: Literal["v1"] = "v1"

    id: ULIDStr = Field(alias="_id")  # this is the session_id
    client_fingerprint: str | None = None  # cookie-derived opaque identifier

    created_at: UTCDatetime
    last_active_at: UTCDatetime
    expires_at: UTCDatetime  # TTL cleanup driver; updated on each interaction

    chat_history: list[ChatMessage] = Field(default_factory=list)
    project_ids: list[ULIDStr] = Field(default_factory=list)
    pipeline_history: list[PipelineSnapshot] = Field(default_factory=list)
    current_pipeline: PipelineSnapshot | None = None

    loaded_layers: list[ProjectLayerSummary] = Field(default_factory=list)
    map_view: MapView | None = None


# --------------------------------------------------------------------------- #
# Atlas Vector Search index configs (documented constants — NOT locked) (D.3-5)
# --------------------------------------------------------------------------- #
# OQ-7: numDimensions uses the SRS default (768). infra provisions to whatever
# the user lands after the recall-vs-cost check on a small corpus.


def _vector_index(name: str, *filter_paths: str) -> dict[str, Any]:
    fields: list[dict[str, Any]] = [
        {
            "type": "vector",
            "path": "embedding",
            "numDimensions": EMBEDDING_DIMENSIONS_DEFAULT,
            "similarity": "cosine",
        }
    ]
    for path in filter_paths:
        fields.append({"type": "filter", "path": path})
    return {"name": name, "type": "vectorSearch", "fields": fields}


RUNS_VECTOR_INDEX = _vector_index("runs_embedding_vsi", "hazard_type", "run_type")
ARTICLES_VECTOR_INDEX = _vector_index("articles_embedding_vsi", "extraction_status")
EVENTS_VECTOR_INDEX = _vector_index("events_embedding_vsi", "event_type", "time_classification")

#: The three Atlas Vector Search indexes (the minimum useful set, D.8).
VECTOR_INDEXES: dict[str, dict[str, Any]] = {
    "runs": RUNS_VECTOR_INDEX,
    "articles": ARTICLES_VECTOR_INDEX,
    "events": EVENTS_VECTOR_INDEX,
}


# --------------------------------------------------------------------------- #
# sessions TTL config (D.6)
# --------------------------------------------------------------------------- #
#: Mongo TTL index spec for sessions: delete documents 30 days after
#: ``expires_at``. ``infra`` creates the actual index; this is the contract.
SESSIONS_TTL: dict[str, Any] = {
    "collection": "sessions",
    "field": "expires_at",
    "expire_after_seconds": 30 * 24 * 60 * 60,  # 30 days past expires_at
}
