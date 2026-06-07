## Appendix D: MongoDB Collection Schemas

> **Status: preemptive.** This appendix is a working specification drafted before implementation. Concrete schemas, field names, indexes, and storage strategies are subject to revision once implementation surfaces real constraints (MongoDB MCP query patterns, Atlas Vector Search performance, actual document sizes, real query patterns under load, etc.). Treat as the starting point, not the contract — changes flow back into this appendix as they're learned.

### D.1 Overview

Five collections in MongoDB Atlas, each with a Pydantic schema that maps directly to BSON documents. The collections instantiate the metadata-payload pattern (§3.7 FR-MP): some are pure metadata indexes over GCS payloads (`projects`); some embed full data alongside metadata (`runs` embeds `AssessmentEnvelope`); some are authoritative documents with no GCS payload by default (`events`, `articles`, `sessions`).

| Collection | Purpose | Source of truth | GCS payload |
|---|---|---|---|
| `projects` | Index over `.qgs` files | GCS for `.qgs`; Mongo for ownership/classification | `.qgs` |
| `runs` | Every solver execution or discovery operation | Embedded `assessment` document | COGs, vectors via `assessment.layers[].uri` |
| `articles` | News article corpus | Mongo document | Optional `html_uri` for long HTML |
| `events` | Extracted `EventMetadata` documents | Mongo document | Optional forcing data referenced from event |
| `sessions` | Chat sessions, state, history | Mongo document | None |

Schemas are defined as Pydantic models for use in application code; the BSON representation is `model.model_dump(mode="json")` with ULIDs as `_id`. Connection from the agent is via the MongoDB MCP server (Decision F); internal worker services may use direct PyMongo for performance.

### D.2 Collection: `projects`

Metadata index over `.qgs` project files in GCS. Rebuildable from GCS bucket walks if Mongo is lost.

```python
class ProjectDocument(BaseModel):
    schema_version: Literal["v1"] = "v1"

    # Identity (_id is the project_id used everywhere)
    _id: str                          # ULID

    # Ownership
    session_id: str                   # owning session

    # Storage pointer
    qgs_uri: str                      # gs://.../project_<id>.qgs (canonical)

    # Display metadata
    name: str                         # human-readable, e.g., "Hurricane Ian flood analysis"
    description: str | None

    # Spatial
    bbox: tuple[float, float, float, float] | None  # current project extent (EPSG:4326)

    # Classification
    hazard_types: list[str]           # all hazards represented in current layers

    # Layer index (denormalized from .qgs for queries; .qgs is authoritative)
    layers: list[ProjectLayerSummary]

    # Lifecycle
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None       # soft delete

class ProjectLayerSummary(BaseModel):
    layer_id: str
    name: str
    layer_type: Literal["raster", "vector"]
    uri: str
    style_preset: str
    visible: bool
    role: Literal["primary", "context", "input"]
    temporal: bool                    # has WMS-T config
```

**Indexes:**
```
{ session_id: 1, updated_at: -1 }         // "show this session's recent projects"
{ deleted_at: 1 } (sparse)                 // efficient exclusion of soft-deleted
{ hazard_types: 1, updated_at: -1 }       // "find recent flood projects"
2dsphere on bbox                           // spatial queries (optional, lazy-created)
```

### D.3 Collection: `runs`

Every solver execution or discovery operation. Embeds the full `AssessmentEnvelope` (when complete) alongside denormalized top-level fields for indexing.

```python
class RunDocument(BaseModel):
    schema_version: Literal["v1"] = "v1"

    # Identity
    _id: str                          # ULID; this is the solver_run_id
    project_id: str
    session_id: str

    # Status lifecycle: pending → running → complete | failed | cancelled
    status: Literal["pending", "running", "complete", "failed", "cancelled"]
    started_at: datetime | None
    completed_at: datetime | None
    duration_seconds: float | None

    # Type discriminator: "modeled" and "discovered" mirror AssessmentEnvelope.envelope_type;
    # "impact" mirrors ImpactEnvelope.envelope_type (Appendix B.6c).
    # Forward-looking (not in M1 / not in sprint-03): "impact" is added post-M5 once Pelicun lands;
    # when run_type == "impact", the `assessment` field carries an `ImpactEnvelope` (Appendix B.6c)
    # rather than an `AssessmentEnvelope`. See FR-MP-5 and Decision N.
    run_type: Literal["modeled", "discovered", "impact"]
    hazard_type: str                  # denormalized from envelope
    workflow_name: str                # denormalized from envelope

    # Spatial (denormalized for queries)
    bbox: tuple[float, float, float, float]

    # Event time (denormalized when applicable)
    event_time_start: datetime | None
    event_time_end: datetime | None

    # Canonical assessment — full AssessmentEnvelope as dict
    # None until status == "complete"
    assessment: dict | None

    # Embedding over a text representation of the envelope (for "similar runs")
    embedding: list[float] | None
    embedding_model: str | None

    # Failure details (when status == "failed")
    error_code: str | None
    error_message: str | None

    # Cancellation
    cancellation_reason: str | None
    cancelled_at: datetime | None

    # User-provided spatial inputs (FR-AS-10)
    user_spatial_inputs: list[UserSpatialInput]

    # Provenance shortcuts (denormalized from assessment.provenance)
    event_id: str | None              # if news-derived
    article_ids: list[str]            # if news-derived

class UserSpatialInput(BaseModel):
    request_id: str                   # the WebSocket request that solicited this input
    geometry_type: Literal["point", "bbox"]
    coordinates: list[float]          # [lon, lat] for point; [minLon, minLat, maxLon, maxLat] for bbox
    prompt_title: str                 # the title shown to the user
    submitted_at: datetime
```

**Indexes:**
```
{ session_id: 1, started_at: -1 }                            // session's run history
{ project_id: 1, started_at: -1 }                            // project's run history
{ status: 1, started_at: -1 }                                 // partial: status in ["pending","running"]
{ hazard_type: 1, started_at: -1 }                            // "recent flood runs"
{ run_type: 1, hazard_type: 1, completed_at: -1 }            // "recent modeled wildfire runs"
{ event_id: 1 } (sparse)                                      // runs derived from a specific event
2dsphere on bbox                                              // spatial run queries
```

**Atlas Vector Search index:**
```yaml
name: runs_embedding_vsi
type: vectorSearch
fields:
  - { type: vector, path: embedding, numDimensions: 768, similarity: cosine }
  - { type: filter, path: hazard_type }
  - { type: filter, path: run_type }
```

### D.4 Collection: `articles`

Fetched news article corpus. Text inlined for v0.1; large HTML may move to GCS via the optional `html_uri`.

```python
class ArticleDocument(BaseModel):
    schema_version: Literal["v1"] = "v1"

    # Identity
    _id: str                          # ULID

    # Source
    url: str                          # canonical URL
    url_hash: str                     # SHA-256 of normalized URL for dedup
    title: str
    publisher: str | None             # extracted from URL or metadata
    author: str | None

    # Content
    text: str                         # extracted article text (cleaned)
    text_length: int                  # character count
    html_uri: str | None              # GCS URI if full HTML retained

    # Time
    published_at: datetime | None     # article publication time
    fetched_at: datetime              # when this system fetched it

    # Search support
    embedding: list[float] | None
    embedding_model: str | None

    # Extraction lifecycle
    extraction_status: Literal["pending", "extracted", "failed", "no_events"]
    extracted_event_ids: list[str]    # events derived from this article (may be 0..N)
    last_processed_at: datetime | None
```

**Indexes:**
```
{ url_hash: 1 } (unique)                          // dedup on URL
{ fetched_at: -1 }                                 // recently fetched
{ published_at: -1 } (sparse)                     // recently published
{ publisher: 1, published_at: -1 } (sparse)       // recent from a source
{ extraction_status: 1, fetched_at: -1 }          // find articles to process
```

**Atlas Vector Search index:**
```yaml
name: articles_embedding_vsi
type: vectorSearch
fields:
  - { type: vector, path: embedding, numDimensions: 768, similarity: cosine }
  - { type: filter, path: extraction_status }
```

### D.5 Collection: `events`

`EventMetadata` documents (full schema in Appendix C). The document is authoritative. The collection schema *is* the `EventMetadata` schema; no wrapper needed.

```python
class EventDocument(EventMetadata):
    # All fields inherited from EventMetadata (Appendix C)
    pass
```

**Indexes:**
```
{ event_type: 1, "time_range.start": -1 }                   // recent events of a type
{ canonical_id: 1 } (sparse, unique)                         // storm lookup by ATCF ID
{ canonical_name: 1 } (sparse)                               // storm lookup by name
{ "location.admin_unit.region": 1, "time_range.start": -1 } // events by state
{ extracted_at: -1 }                                         // recently extracted
{ "provenance.article_ids": 1 }                              // find events derived from an article
2dsphere on location.bbox                                    // spatial event queries
```

**Atlas Vector Search index:**
```yaml
name: events_embedding_vsi
type: vectorSearch
fields:
  - { type: vector, path: embedding, numDimensions: 768, similarity: cosine }
  - { type: filter, path: event_type }
  - { type: filter, path: time_classification }
```

### D.6 Collection: `sessions`

Chat session state. Holds the full session: ownership, chat history, current map state, pipeline history. Read on resume; written incrementally during the session. TTL-driven cleanup.

```python
class SessionDocument(BaseModel):
    schema_version: Literal["v1"] = "v1"

    # Identity
    _id: str                          # ULID; this is the session_id

    # Ownership (anonymous in v0.1; user_id added later)
    client_fingerprint: str | None    # opaque client identifier (cookie-derived)

    # Lifecycle
    created_at: datetime
    last_active_at: datetime
    expires_at: datetime              # used for TTL cleanup; updated on each interaction

    # Conversation
    chat_history: list[ChatMessage]   # bounded; oldest truncated when > max (default 200 messages)
    project_ids: list[str]            # projects created in this session
    pipeline_history: list[PipelineSnapshot]  # bounded; recent pipelines (default last 20)
    current_pipeline: PipelineSnapshot | None

    # Current map state (mirrors what the client shows)
    loaded_layers: list[ProjectLayerSummary]   # current layers
    map_view: MapView                          # current center/zoom/bbox

class ChatMessage(BaseModel):
    message_id: str                   # ULID; matches the WebSocket message ID for agent messages
    role: Literal["user", "agent"]
    content: str                      # for agent messages, the final accumulated text after streaming
    tool_calls: list[ToolCallSummary] # for agent messages; empty list for user
    created_at: datetime

class ToolCallSummary(BaseModel):
    call_id: str
    tool_name: str
    state: Literal["complete", "failed", "cancelled"]
    result_summary: str | None
    result_uri: str | None
    error_code: str | None
    started_at: datetime
    completed_at: datetime | None

class PipelineSnapshot(BaseModel):
    pipeline_id: str
    started_at: datetime
    completed_at: datetime | None
    final_state: Literal["complete", "failed", "cancelled"] | None
    steps: list[PipelineStepSummary]

class PipelineStepSummary(BaseModel):
    step_id: str
    name: str
    tool_name: str
    state: Literal["pending", "running", "complete", "failed", "cancelled"]
    started_at: datetime | None
    completed_at: datetime | None
    progress_percent: int | None     # 0..100; workflow-attributed, never LLM-estimated
    error_code: str | None           # SCREAMING_SNAKE_CASE per Appendix A.6; present only when state == "failed"
    error_message: str | None        # short human-readable; capped at 512 chars to discourage stack-trace leakage

class MapView(BaseModel):
    center: tuple[float, float]       # [lon, lat]
    zoom: float
    bbox: tuple[float, float, float, float]
```

**Indexes:**
```
{ last_active_at: -1 }                                       // recently active sessions
{ expires_at: 1 }                                            // TTL cleanup driver
{ client_fingerprint: 1, last_active_at: -1 } (sparse)      // a client's sessions
```

**TTL configuration:** documents are eligible for auto-deletion 30 days after `expires_at`. Active sessions update `expires_at` on each interaction (sliding-window expiry). Inactive sessions naturally age out.

**PipelineStepSummary progress + error fields (additive, all optional).** Three optional fields support the M3 PipelineStrip render and M4's real `pipeline-state` emission (Appendix A.4 `pipeline-state` payload). All three default to `None` and never appear on a healthy `pending` / `complete` step.

- `progress_percent: int | None` — integer in `[0, 100]` (pydantic `Field(ge=0, le=100)`). Populated by the workflow when it can reasonably attribute progress (solver chunk N of M, n-of-M dataset rows processed). Never an LLM estimate (Invariant 1: determinism boundary). Tightening to required when `state == "running"` is a future amendment; for v0.1 the field stays optional everywhere.
- `error_code: str | None` — `SCREAMING_SNAKE_CASE` literal aligned with the Appendix A.6 error-code convention; populated only when `state == "failed"`. The set of valid codes is **open** per A.6 (every workflow may register its own); the schema validates shape, not membership.
- `error_message: str | None` — short human-readable explanation accompanying `error_code`. Free text, capped at 512 characters by `Field(max_length=512)` to discourage stack-trace leakage through the WebSocket envelope.

No cost / dollar / duration-estimate field is added anywhere (Invariant 9: no cost theater). The web client's `PipelineStepSummary` mirror already carries the three fields as optional from job-0026; this amendment lands them in the canonical schema, closing OQ-W-26-PIPELINE-STEP-FIELDS.

**AtomicToolMetadata (collateral, not a collection document).** A separate pydantic model defined in `grace2_contracts.tool_registry` carries the FR-DC-2 TTL-class declaration every external-API atomic tool registers at definition time (`name` / `ttl_class` / `source_class` / `cacheable`, with a cross-field `model_validator` enforcing the FR-DC-6 consistency rule). It is not persisted to MongoDB — it lives in the agent service's tool registry — but the schema is owned alongside the Appendix D collection schemas so the contract surface is single-sourced. See §3.9 for the cache architecture this metadata feeds.

### D.7 Cross-cutting decisions

- **Schema versioning per collection**: every document has `schema_version: Literal["v1"] = "v1"` as the first field. Migrations bump independently per collection.
- **ULIDs as `_id`**: consistent with Appendix A. Time-sortable, URL-safe, no central coordination.
- **Embedding storage strategy**: same model (`text-embedding-005`, 768-dim) across collections; text representation varies:
  - `runs.embedding`: text rep over envelope fields (hazard + location + metrics + provenance)
  - `articles.embedding`: article text truncated to first ~8000 tokens
  - `events.embedding`: canonical event description (name + type + location + time + intensity)
- **Soft deletes**: only `projects` supports soft delete (via `deleted_at`). `runs`, `events`, `articles` are append-and-modify-once. `sessions` are TTL-cleaned.
- **Run status terminal states**: `complete`, `failed`, `cancelled` are terminal; no transitions out.
- **`assessment` as `dict` not nested Pydantic model**: trades schema validation at the document level for forward compatibility (envelope schema changes don't require migrations). Validation happens at API boundaries (in the agent service, before write).
- **Cross-collection references as raw string IDs**: not `DBRef`. Validators check existence on write where needed.
- **MCP access vs direct PyMongo**: agent reads/writes through MongoDB MCP server (Decision F); worker services write results directly with PyMongo for throughput.
- **No cost fields on runs**: cost-tracking and cost-estimation are deferred indefinitely. Surfacing approximate cost figures to users is worse than not surfacing them; cents-precise tracking is not currently achievable.

### D.8 Storage sizing (v0.1 baseline)

Rough per-document sizes:
- `projects`: ~5 KB
- `runs`: ~50–200 KB (varies with layer count and embedded envelope size)
- `articles`: ~20–100 KB (text + embedding)
- `events`: ~10 KB
- `sessions`: variable, up to ~1 MB for very long sessions

A reasonable v0.1 baseline (1000 articles, 200 events, 100 runs, 50 sessions, 50 projects) fits within an Atlas M10 cluster (10 GB storage). Atlas Vector Search indexes are billed separately; three indexes (runs, articles, events) is the minimum useful set.

If infrastructure budget is constrained in early v0.1, dropping the `runs` vector index is the cheapest cut — "similar past runs" is a nice-to-have, not load-bearing.

### D.9 Design rationale

- **Five collections, not one**: each has distinct query patterns and lifecycle policies. Mongo's $lookup makes joins workable, but separate collections give cleaner indexes and TTLs.
- **Embedding the envelope in `runs.assessment` instead of normalizing into separate collections**: a run is naturally self-contained (one envelope, one set of metrics, one set of layers). Normalizing into a `layers` collection or `metrics` collection would multiply joins without adding query power.
- **Denormalized top-level fields on `runs`**: `hazard_type`, `bbox`, `event_time_start/end` are copied from the embedded envelope so indexes work without needing computed indexes over `assessment.*`. Storage cost is negligible (<100 bytes per run); query benefit is large.
- **TTL only on `sessions`**: long-running sessions naturally expire; runs and events are reference data that should persist indefinitely (or be archived deliberately, not auto-pruned).
- **Anonymous session ownership via `client_fingerprint`**: v0.1 has no user accounts. A cookie-derived opaque identifier lets returning clients see their prior sessions without authentication; adding real user IDs later replaces this field cleanly.
- **`UserSpatialInput` typed and stored on the run**: reproducibility and audit. If the model run depends on user-placed pin coordinates, future viewers of the run need to see where the pin was.
- **Vector search filters in addition to vector field**: filtering by `hazard_type` or `event_type` makes vector queries faster and more relevant; Atlas Vector Search supports this natively at index creation.
- **No cost tracking fields**: cost estimation is deferred indefinitely; tracking actual costs per run is a feature waiting on that decision.

### D.10 Known open choices

- **Article text storage**: inline by default; `html_uri` for very long content. Threshold for switching (size, character count) TBD.
- **Run embedding text representation**: what string actually gets embedded? Could be deterministic from envelope fields or LLM-summarized. Affects similarity quality; decide during M7.
- **Session TTL value**: 30 days is a guess. Real number depends on usage patterns. Adjustable per environment.
- **Anonymous client fingerprint mechanism**: cookie-based vs IP-based vs fully ephemeral (per-tab). Affects whether returning users see their prior sessions. Likely cookie-based in v0.1.
- **Index review cadence**: indexes will need pruning or addition as real query patterns emerge. Schedule a review after M7 when news pipeline is operational and query patterns are observable.
- **Vector index dimension choices**: `text-embedding-005` defaults to 768; smaller dimensions (256, 128) trade recall for index size/cost. Verify on a small corpus before committing.
- **Whether to extend soft delete to `runs`**: useful for "I made a mistake, let me delete this run from my history" but adds complexity. Currently no.

### D.11 Collection: `catalog_entries` *(sprint-08 amendment — landed by job-0045-schema-20260607)*

The Mode 1 curated data-source catalog (§F.1.2). Each document is a `CatalogEntry` (FR-PHC-2 binding shape — see Appendix F §F.1.2 Mode 1). The collection schema *is* the `CatalogEntry` schema; no wrapper fields are added.

```python
class CatalogEntryDocument(CatalogEntry):
    # All fields inherited from CatalogEntry (FR-PHC-2 + §F.1.2 Mode 1):
    #   schema_version: Literal["v1"]
    #   id: str                          # stable identifier; the Mongo _id
    #   name: str
    #   description: str
    #   urls: list[str]                  # primary URL + alternative mirrors
    #   access_tier: Literal[1, 2, 3, 4]  # §F.1.1
    #   credential_tier: Literal[1, 2, 3] # §F.1
    #   ttl_class: Literal["static-30d", "semi-static-7d", "dynamic-1h", "live-no-cache"]
    #   source_class: str                # FR-DC-1 bucket-prefix
    #   license: str
    #   citation: str
    #   vintage: str | None
    #   last_verified: datetime
    #   status: Literal["active", "deprecated", "user_proposed_pending_curator_review"]
    #   how_to_use: str                  # invocation examples + quirks
    #   api_key_secret_ref: str | None   # required when credential_tier >= 2
    pass
```

The Mongo `_id` is the entry `id` (a free-form stable string identifier curated at entry-creation time, e.g. `"usgs-3dep-dem-1m"`, `"worldpop-1km-aggregated"`); the write path sets `_id = id` at insert time. No `_id` alias on the model — `CatalogEntry` stays a single shape across wire / YAML / Mongo, and the entry `id` is not a ULID.

**Indexes:**
```
{ source_class: 1 }                                  // catalog_search by domain
{ status: 1, source_class: 1 }                       // active-only by source (the common query path)
```

**TTL configuration:** none. Catalog entries are durable until a curator deprecates them (the `status` lifecycle does the soft-delete work).

**Status lifecycle:**
- `active`: curator-vetted; `catalog_search` returns this entry.
- `deprecated`: curator-removed; retained for audit / historical run-provenance lookups but excluded from active search results.
- `user_proposed_pending_curator_review`: a §F.1.2 Mode 2 user-accepted `offer-catalog-addition` entry; included in `catalog_search` results but surfaced as provisional until a curator flips it to `active`.

**Cross-field rule** (enforced by the `CatalogEntry` model validator): when `credential_tier == 1`, `api_key_secret_ref` must be `None`; when `credential_tier >= 2`, `api_key_secret_ref` is required (non-empty string — typically the Secret Manager resource path).

### D.12 Collection: `catalog_audit_log` *(sprint-08 amendment — landed by job-0045-schema-20260607)*

Append-only audit trail for the catalog. Every catalog mutation lands one document here. Mode 2 user-proposed entries produce a `user_proposed` event at acceptance; curator-side approval / rejection produce a `curator_approved` / `curator_rejected` event against the same `entry_id`. Decision M (claim provenance) requires this trail to be inspectable: the catalog query path may surface user-proposed entries as provisional, and downstream `RunDocument` references to a catalog entry can be resolved back through this collection to recover the proposal + review context.

```python
class CatalogAuditLogDocument(BaseModel):
    schema_version: Literal["v1"] = "v1"

    # Identity
    _id: str                          # ULID; the audit-event id

    # Subject
    entry_id: str                     # references CatalogEntry.id

    # Origin (optional — populated when the event happened inside a session
    # or when user identity is available; v0.1 leaves user_id None since
    # identity machinery is not yet wired)
    session_id: str | None
    user_id: str | None

    # Event
    event_type: Literal[
        "add",                        # curator added a new entry directly (Mode 1)
        "update",                     # curator edited an existing entry's metadata
        "deprecate",                  # curator flipped status to "deprecated"
        "user_proposed",              # Mode 2 user accepted an offer-catalog-addition
        "curator_approved",           # curator flipped a user-proposed entry to "active"
        "curator_rejected",           # curator removed a user-proposed entry
    ]
    event_payload: dict               # shape varies by event_type; see below

    # Time
    timestamp: datetime
```

**`event_payload` shape (varies by `event_type`):**
- `add` / `update`: the diff (`{ "fields_changed": [...], "before": {...}, "after": {...} }`).
- `deprecate`: the curator note (`{ "note": "..." }`).
- `user_proposed`: conformity-probe findings + the originating `offer-catalog-addition` request id (`{ "probe_findings": {...}, "request_id": "01HX..." }`).
- `curator_approved`: the curator note + reviewing-curator identifier (post-M6+).
- `curator_rejected`: the curator note + rejection reason.

**Indexes:**
```
{ entry_id: 1, timestamp: -1 }                       // audit-trail-for-an-entry query path
```

**TTL configuration:** none. Audit-log entries are durable indefinitely per Decision M (claim provenance must survive across all retention windows).

