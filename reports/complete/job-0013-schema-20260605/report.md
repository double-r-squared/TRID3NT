# Report: Contracts v0 from SRS Appendices A-D (pydantic v2)

**Job ID:** job-0013-schema-20260605
**Sprint:** sprint-03
**Specialist:** schema
**Task:** Author `packages/contracts/` from SRS Appendices A-D as pydantic v2 models — WebSocket protocol (Appendix A), AssessmentEnvelope + FloodPayload (Appendix B), EventMetadata + ClaimSet + intensity union (Appendix C), the five MongoDB collection schemas + vector index configs (Appendix D), CatalogEntry (FR-PHC-2), solver-execution shapes (FR-TA-2), tool-docstring conventions (FR-AS-3 / FR-TA-3), and a scripted JSON Schema export. Include the `research_mode` field on `user-message` (FR-WC-15 / orchestrator pinned toggle-carrier seam) as an Appendix A amendment, with the proposed diff in this report. Surface OQ-7 (embedding dimension) with a recommendation. Live E2E evidence required.
**Status:** ready-for-audit

## Summary

`packages/contracts/` is a small installable pure-Python package — pydantic v2 — that materializes SRS v0.3 Appendices A-D plus FR-PHC-2 and the FR-TA-2 solver shapes into ten modules with 91 round-trip + negative-control tests and a scripted JSON Schema export (35 schemas covering every top-level contract and every WebSocket message payload). All 91 tests pass in a fresh `virtualenv` install, and re-running the schema export against an unchanged contract set produces byte-identical files (idempotent — confirmed via `diff -r`). The package is `schema_version: "v1"` throughout, `extra="forbid"` everywhere, and pins the four ownership seams the orchestrator named: `research_mode` on `user-message`, `ExecutionHandle.workflows_execution_id`, the `ClaimSet`/`NumericClaim` evidence machinery, and the COG / FlatGeobuf / GeoParquet output format vocabulary that `LayerURI` / `ResultLayer` / `map-command load-layer` all share.

## Changes Made

All files under `/home/nate/Documents/GRACE-2/packages/contracts/`.

### Package skeleton

- **`pyproject.toml`** — `grace2-contracts` v0.1.0; deps `pydantic>=2,<3` and `python-ulid>=2,<4`; optional `pytest`; `grace2-export-schemas` console script wired to `grace2_contracts.export_schemas:main`; `requires-python >= 3.11`; setuptools `src/` layout; pytest `testpaths = ["tests"]`.
- **`README.md`** — install + run-the-tests + regenerate-schemas + wire-form sections, plus an Amendments-to-Appendices section that points back to this report.
- **`src/grace2_contracts/__init__.py`** — re-exports the seven submodules + the most-used primitives from `common` (`GraceModel`, `ULIDStr`, `BBox`, `Lon`/`Lat`, `TimeRange`, `new_ulid`, `now_utc`). `__version__ = "0.1.0"`, `SCHEMA_VERSION = "v1"`.
- **`src/grace2_contracts/py.typed`** — PEP-561 marker so downstream `mypy`/`pyright` sees the typed package.

### `common.py` — shared primitives (Appendix A.1, B.7, D.7)

- `GraceModel(BaseModel)` with `model_config = ConfigDict(extra="forbid", validate_assignment=True, ser_json_timedelta="iso8601")` — the canonical base for every contract.
- `new_ulid()` + `ULIDStr = Annotated[str, AfterValidator(_validate_ulid)]` — ULIDs are 26-char Crockford base32, time-sortable, URL-safe.
- `now_utc()` + `UTCDatetime = Annotated[datetime, PlainSerializer(_serialize_dt_z)]` — every datetime serializes to ISO-8601 with a literal `Z` suffix, naive datetimes treated as UTC.
- `BBox = Annotated[tuple[float, float, float, float], AfterValidator(_validate_bbox)]` — EPSG:4326 ordering `[minLon, minLat, maxLon, maxLat]` enforced; inverted-bbox and out-of-range raise.
- `Lon`/`Lat` constrained floats; `TimeRange` shared by `AssessmentEnvelope` and `EventMetadata`.

### `ws.py` — WebSocket protocol (Appendix A, FR-AS-5)

- `Envelope[PayloadT]` (A.1) — generic wrapper carrying `type` (kebab-case discriminator), `id` (ULID, default factory), `ts` (UTC datetime, default factory), `session_id` (ULID), `payload` (always an object).
- `ErrorCode` Literal — every A.6 SCREAMING_SNAKE_CASE code including the cancel-path codes (`CANCELLED`, `USER_INPUT_CANCELLED`, `*_TIMEOUT`).
- **A.3 client-to-agent:** `UserMessagePayload` (with the new `research_mode` field — see Amendment Log), `CancelPayload`, `ConfirmResponsePayload`, `SessionResumePayload`.
- **A.4b user input responses:** `SpatialInputResponsePayload` (point/bbox or `cancelled=True`), `DisambiguationResponsePayload`, `ClarificationResponsePayload`.
- **A.4 agent-to-client:** `AgentMessageChunkPayload`, `ToolCallStartPayload` (carries `tool_category`), `ToolCallProgressPayload` (`percent` validated `ge=0, le=100`), `ToolCallCompletePayload` (`metrics: dict` per invariant 1), `ToolCallFailedPayload`, `PipelineStatePayload` + `PipelineStep` (with `cancelled` as a distinct terminal step state — invariant 8), `MapCommandPayload` (umbrella + `command` discriminator), `ConfirmationRequestPayload` (no cost field — invariant 9), `SessionStatePayload`, `ErrorPayload`, `LocationResolvedPayload`, `SpatialInputRequestPayload`, `DisambiguationRequestPayload`, `ClarificationRequestPayload` (2-4 options, A.4 constraint).
- **`map-command` args (A.4):** one `*Args` model per command (`LoadLayerArgs`, `RemoveLayerArgs`, `SetLayerVisibilityArgs`, `SetLayerOpacityArgs` with `opacity` clamped 0..1, `SetLayerOrderArgs`, `ZoomToArgs`, `SetTemporalConfigArgs`, `StartAnimationArgs` with `speed` Literal, `StopAnimationArgs`, `InvalidateTilesArgs`). `LoadLayerArgs` is field-for-field alignable with `ResultLayer` / `LayerURI` so postprocess output flows to the map with zero translation.
- **Registries:** `CLIENT_TO_AGENT_PAYLOADS`, `AGENT_TO_CLIENT_PAYLOADS`, `ALL_PAYLOADS`, `MAP_COMMAND_ARGS` — kebab-case-`type` → model maps that the agent server and web client share.
- **Mid-run modification:** `MESSAGE_TYPE` and `COMMAND` per-class string constants were typed as `ClassVar[str]` after `from typing import ClassVar` was added. Reason: pydantic v2 treats class attributes without `ClassVar` as fields and a bare `str` value gets interpreted as a default for an inferred required field; the round-trip suite caught this. The values are unchanged; only the type annotation differs. This is the `git diff` you see against the previous commit.

### `envelope.py` — AssessmentEnvelope (Appendix B, FR-TA-1, FR-AS-7)

- `HazardType`/`EnvelopeType` open-enum Literals (`flood, groundwater, wildfire, seismic, spill` / `modeled, discovered`).
- Supporting types: `ForcingSummary`, `TemporalConfig`, `ResultLayer` (field-for-field with `map-command load-layer` args), `DataSource`, `Provenance` (typed sources — invariant 7), `CatalogReference`, `BaseMetrics` (empty by design — subtypes carry numbers, invariant 3).
- `CriticalFacility`, `FloodMetrics` (every narrated number is a typed field — invariant 1), `FloodPayload`.
- `AssessmentEnvelope` (Appendix B.2) — `schema_version: Literal["v1"]`, identity (envelope_id/project_id/session_id), `envelope_type` + `hazard_type` discriminators, spatial/temporal extent (`bbox`, `crs="EPSG:4326"`, `time_range: TimeRange | None`), forcing/catalog discriminated by mode, `layers`, hazard-agnostic `metrics`, structured `Provenance`, lifecycle timestamps, subtype payload slots (`flood: FloodPayload | None`, plus permissive `dict | None` slots for groundwater/wildfire/seismic/spill until their engines land — see Amendment Log OQ-S2).
- `@model_validator(mode="after")` enforces *exactly one* subtype populated and matching `hazard_type` (invariant 3 guard; negative tests exercise the two-subtype, zero-subtype, and wrong-subtype cases).

### `event.py` — EventMetadata (Appendix C, FR-HEP-5, Decision M)

- `EventType` open enum (10 members including `levee_failure` and `intense_rainfall`).
- `SourceType` *closed* Literal mapped from the curated source-to-tier table (engine-owned, FR-HEP-2, invariant 7); `ConsensusMethod`/`ConsensusConfidence` Literals.
- `NumericClaim` — per-source numeric evidence with `source_type`, `source_id`, `source_url`, observation/reporting times, optional confidence, `outlier_flag`.
- `ClaimSet` — list of `NumericClaim` + `consensus_value`/`consensus_unit`/`consensus_method`/`consensus_confidence` + optional `notes`. **Every numeric intensity field across the union is `ClaimSet | None` — never a bare float (Decision M, invariant 7).**
- `AdminUnit`, `EventLocation` with `granularity` + `precision_class` and a `@model_validator` enforcing "at least one of bbox or place_name".
- `EventProvenance` (article_ids + primary_article_id + notes).
- `IntensityIndicators` discriminated union — one model per `event_type`: `HurricaneIntensity` (`saffir_simpson`, `max_winds_kt`, `min_central_pressure_mb` all as `ClaimSet | None`; `landfall_location` stays scalar string), `TropicalStormIntensity`, `AtmosphericRiverIntensity`, `RainfallIntensity`, `DamFailureIntensity`, `StormSurgeIntensity`, `RiverFloodIntensity`, `FlashFloodIntensity`, `GenericIntensity` fallback for `other`.
- `EventMetadata` — top-level model + `@model_validator(mode="after")` enforcing that the populated intensity payload matches `event_type` (or is empty — extraction may find no numerics). `levee_failure`/`intense_rainfall` map onto `dam_failure`/`rainfall` respectively via `_EVENT_TYPE_TO_INTENSITY` (see Amendment Log OQ-S3).
- `embedding` / `embedding_model` fields per Decision L.

### `collections.py` — MongoDB collection schemas (Appendix D, FR-MP-5)

- `DocModel(GraceModel)` with `populate_by_name=True` so id fields aliased to `_id` accept either name on construction and dump as `_id` with `by_alias=True`. `MONGO_DUMP_KWARGS = {"mode": "json", "by_alias": True}` is the canonical dump configuration.
- `ProjectDocument` (D.2) — `id` aliased to `_id`, `qgs_uri`, `bbox`, denormalized `hazard_types` and `layers` (`ProjectLayerSummary`), soft `deleted_at`.
- `RunDocument` (D.3) — `id`/`_id`, references project + session, `status` Literal includes `cancelled` (invariant 8), denormalized `run_type`/`hazard_type`/`workflow_name`/`bbox`/`event_time_*` for queries, **`assessment: dict | None`** (full envelope embedded as dict — D.7 rationale: schema changes don't force a collection migration), `embedding` + `embedding_model`, `error_*` + `cancellation_*` fields, `user_spatial_inputs: list[UserSpatialInput]`, optional `event_id`/`article_ids` for news-derived runs. **No cost field anywhere** (invariant 9).
- `ArticleDocument` (D.4) — id, normalized URL hash for dedup, text body + length, optional `html_uri` to GCS, embedding + model, `extraction_status` Literal, `extracted_event_ids`.
- `EventDocument(EventMetadata)` (D.5) — the collection schema *is* `EventMetadata`; no wrapper fields. The write path sets `_id = event_id` at insert.
- `SessionDocument` (D.6) with `ChatMessage` / `ToolCallSummary` / `PipelineStepSummary` / `PipelineSnapshot` / `MapView` — TTL-driven by `expires_at`.
- **Vector index configs** — `RUNS_VECTOR_INDEX`, `ARTICLES_VECTOR_INDEX`, `EVENTS_VECTOR_INDEX` exposed as documented constants (not a locked Atlas config). Each filters on a sensible set of low-cardinality fields (`hazard_type`/`run_type`, `extraction_status`, `event_type`/`time_classification`). `numDimensions` uses `EMBEDDING_DIMENSIONS_DEFAULT = 768` — see OQ-7 below.
- **TTL config** — `SESSIONS_TTL = {"collection": "sessions", "field": "expires_at", "expire_after_seconds": 30 days}` for `infra` to provision.

### `catalog.py` — CatalogEntry (FR-PHC-2)

- `CatalogFormat` open enum (`wms`, `wmts`, `raster_cog`, `vector_fgb`, `vector_geoparquet`, `wfs`). The fetched-payload-for-Tier-B vocabulary remains COG / FlatGeobuf / GeoParquet per FR-CE-4 / FR-QS-3; `wms`/`wmts` cover remote-reference discovery access.
- `CatalogEntry` — `id`, `title`, `agency`, `topic` (min 1), `coverage`, `format`, `access` URL, `style_preset`, `license`, `description`, `last_verified` date.

### `execution.py` — solver-execution shapes (FR-TA-2, FR-CE-2/3, FR-AS-6)

- `ComputeClass` open Literal.
- `ModelSetup` — returned by `build_sfincs_model`; carries `setup_uri` (GCS), `grid_resolution_m`, `bbox`, free-form `parameters` dict.
- **`ExecutionHandle`** — returned by `run_solver`. The cancellation contract (invariant 8). Carries **`workflows_execution_id: str`** as a first-class field — the Cloud Workflows execution identifier, the pinned cancellation seam — plus `workflow_name` and `workflow_location`. `agent` calls Workflows `terminate` with this id without string-parsing. One handle type, no per-backend variants.
- `RunResult` — returned by `wait_for_completion`; `status: Literal["complete", "failed", "cancelled"]` (cancelled is distinct from failed — invariant 8); `output_uri`, timestamps, optional failure fields, optional `cancellation_reason`.
- `LayerURI` — returned by `postprocess_flood`; field-for-field alignable with `map-command load-layer` args (`layer_id`, `wms_url` ↔ `uri`, `style_preset`, `temporal`) and with `ResultLayer`.

### `tool_metadata.py` — conventions only (FR-AS-3, FR-TA-3)

- `REQUIRED_DOCSTRING_SECTIONS` — `summary`, `Use this when:`, `Do NOT use this for:`, `params`, `returns`. The `agent` registry rejects tools whose docstring is missing any; `testing` asserts the same.
- `TOOL_CATEGORIES` — 12-member open vocabulary (`workflow`, `discovery`, `data-fetch`, `event-sourcing`, `event-aggregation`, `geocoding`, `mongodb`, `qgis`, `model-setup`, `model-execution`, `client-control`, `user-input`). `is_known_tool_category(category)` for the soft check.

### `export_schemas.py` — JSON Schema export (scripted)

- Console script `grace2-export-schemas` (exposed via `[project.scripts]`) and `python -m grace2_contracts.export_schemas [OUTPUT_DIR]`.
- Default output: `packages/contracts/schemas/` (resolved relative to the package).
- Writes one file per top-level contract: 14 named exports (envelope/event/claims/collections/catalog/execution) + 21 `ws_*.json` payload schemas (one per `ALL_PAYLOADS` entry) = **35 files**.
- Serialized with `json.dumps(..., indent=2, sort_keys=True) + "\n"` so re-runs produce **byte-identical** output (CI drift check is `git diff`).

### `tests/` — 8 test modules, 91 tests

- `conftest.py` — `session_id` ULID fixture, `now_z` 2026-06-05T12:00:00Z fixture.
- `test_common.py` (12 tests) — ULID round-trip + format, datetime Z-suffix on aware and naive, bbox ordering + out-of-range, `extra="forbid"` enforcement, `TimeRange`.
- `test_ws.py` (33 tests) — every A.3/A.4/A.4b payload round-trips through `model_dump(mode="json") → model_validate → model_dump`; negative controls for unknown `research_mode`, percent-out-of-range, invalid step state, every `map-command` args registered, `confirmation-request` carries no `cost` / `estimated_cost` / `cost_estimate` field, `pipeline-state.cancelled` distinct from `failed`, `clarification-request` 2..4 options enforced, error-code enum closed.
- `test_envelope.py` (10 tests) — modeled flood envelope round-trip, the exactly-one-subtype validator (two/zero/wrong cases), `FloodMetrics` has no cost field, `BaseMetrics` `extra="forbid"`, `flooded_area_km2 >= 0`, positive grid resolution, `ResultLayer` aligns with `load-layer` args.
- `test_event.py` (13 tests) — hurricane event round-trip, **bare-float and bare-int intensity rejected (Decision M)**, scalar non-numeric fields stay scalar, `EventLocation` bbox-or-place-name validator (both branches), wrong-intensity-for-event-type rejected, empty-intensity allowed, `intense_rainfall → rainfall` and `river_flood` mappings, `NumericClaim.source_type` closed enum, `ClaimSet.consensus_value` round-trips.
- `test_collections.py` (13 tests) — `ProjectDocument` round-trip + `_id` aliasing in both directions, `RunDocument.status` supports `cancelled`, `RunDocument` carries no cost field, `RunDocument` with user spatial inputs, `ArticleDocument` round-trip, `EventDocument` is `EventMetadata` shape, `SessionDocument` with cancelled pipeline history, vector indexes cover runs/articles/events, **embedding dimension default is 768 (OQ-7)**, vector index filter paths are sensible per collection, sessions TTL config.
- `test_catalog.py` (4 tests) — `CatalogEntry` round-trip + topic-min-1 + unknown-format-rejected + `last_verified` date round-trip.
- `test_execution.py` (4 tests) — `ModelSetup` round-trip; **`ExecutionHandle.workflows_execution_id` pinned (invariant 8 — `extra="forbid"` rejects misspellings; the field is required)**; `RunResult.status` supports `cancelled`; `LayerURI` maps field-for-field onto `load-layer` args.
- `test_export_schemas.py` (3 tests) — export writes one file per top-level contract, export is idempotent (byte-identical re-run), each exported file is valid JSON.

### `schemas/` — 35 generated JSON Schema files

Regeneration is idempotent. The 35 files cover: `assessment_envelope`, `event_metadata`, `claim_set`, `numeric_claim`, `project_document`, `run_document`, `article_document`, `event_document`, `session_document`, `catalog_entry`, `model_setup`, `execution_handle`, `run_result`, `layer_uri` + 21 `ws_*` payloads (one per `ALL_PAYLOADS` entry).

> Kickoff note: the kickoff's setup paragraph mentioned "28 generated JSON schemas". The actual count is 35 because every `ALL_PAYLOADS` entry gets its own `ws_*.json` (14 agent-to-client + 7 client-to-agent = 21 ws files). Surfacing as a count clarification, not a defect.

## Decisions Made

- **Decision (S1): Package name `grace2-contracts` (distribution) / `grace2_contracts` (import).**
  - Rationale: Matches existing dead-but-deleted `src/grace2_contracts/` directory referenced in PROJECT_STATE; aligns with the `grace-2` project / `GRACE-2` repo names; no namespacing collision; pip-friendly hyphen, Python-friendly underscore.
  - Alternatives considered: `grace_contracts` (drops the version digit — but other GRACE codebases exist publicly and "grace2" is the disambiguator), `grace2_schema` (mirrors the agent file name but misleading — this package owns more than schema), `g2contracts` (terse but inscrutable).
  - **Surfaced as Open Question OQ-S1** below.

- **Decision (S2): pydantic v2 with `ConfigDict(extra="forbid", validate_assignment=True)` on every model via `GraceModel`.**
  - Rationale: SRS Appendix D already specifies pydantic models; PROJECT_STATE anchors v2 (was tentative, now codified). `extra="forbid"` is the AGENTS.md "remove don't shim" posture — unknown fields are defects, not silently dropped. `validate_assignment` catches post-construction corruption.
  - Alternatives considered: dataclasses + msgspec (faster but no model validators / discriminated unions), `extra="ignore"` (silent data loss — rejected). Not reopened.

- **Decision (S3): Discriminated unions via explicit `@model_validator(mode="after")` on the parent + flat `<name>: <type> | None = None` fields, rather than pydantic's `Discriminator(Tag(...))` plumbing.**
  - Rationale: The Appendix B / C envelopes already shape this way — `flood: FloodPayload | None` discriminated by `hazard_type`; `intensity` slots discriminated by `event_type`. The flat shape round-trips cleanly through JSON, reads identically on the wire, and the validator catches the "exactly one populated and matching the discriminator" rule with an actionable error message. Pydantic's discriminator plumbing is more ergonomic for a tagged union but adds wire-form complexity (the tag becomes part of the payload) that diverges from the appendix.
  - Alternatives considered: `Annotated[Union[...], Discriminator(...)]` — cleaner code but wire-form drift; per-subtype top-level fields with no validator — silently allows two subtypes populated, which is exactly the invariant-3 trap to guard against.

- **Decision (S4): Schema export is a script (`grace2-export-schemas`), not a runtime function or CI macro.**
  - Rationale: AGENTS.md's "live E2E validation" lens — schemas regenerating must be a verifiable user-runnable command, and the output must be byte-stable so `git diff` is the drift signal. Implemented with `argparse`-free `sys.argv[1:]` parsing (one optional positional output dir), `sort_keys=True`, trailing newline. CI runs the script and asserts a clean diff.
  - Alternatives considered: `typer` CLI (heavier dep for one optional arg), `click` (ditto), per-model `__main__` blocks (fragments the entrypoint).

- **Decision (S5): Test organization — one `test_<module>.py` per source module + a `conftest.py` with two cross-cutting fixtures (`session_id`, `now_z`).**
  - Rationale: Mirrors the module boundary so a failing test names its own owner; `conftest` stays tiny because round-trip patterns differ per module (some need fully-built nested fixtures, some don't). 91 tests / 8 files keeps each file under 350 lines and readable.
  - Alternatives considered: one giant `test_round_trip.py` (would obscure which invariant a failure breaks), parametrized mega-tests (would lose the readable per-case names that match Decision-/FR- IDs).

- **Decision (S6): `ws.MapCommandPayload` keeps `args: dict` at the envelope level rather than ten sibling top-level message types per command.**
  - Rationale: A.7 in Appendix A explicitly calls out the rationale — ten near-identical top-level types would create churn; the umbrella with an internal `command` discriminator concentrates change. Consumers validate `args` against `MAP_COMMAND_ARGS[command]` on receive.
  - Alternatives considered: ten top-level payloads (rejected by A.7); a single combined `LoadLayerArgs | RemoveLayerArgs | ...` discriminated union for `args` (forces pydantic-discriminator plumbing on the wire — diverges from A.4 shape).

- **Decision (S7): `MESSAGE_TYPE` / `COMMAND` per-class string constants typed as `ClassVar[str]`.**
  - Rationale: pydantic v2 treats unannotated class attributes as field defaults; a bare `MESSAGE_TYPE = "user-message"` was creating an inferred required field that broke construction. Typing as `ClassVar[str]` cleanly removes them from the field schema while keeping them as the discriminator the registries key on. This was the only mid-run edit to `ws.py`.
  - Alternatives considered: nested `class Config:` with `__message_type__` (unreachable from outside without reflection — fragile), module-level constants (loses the class-attached affordance the registry depends on).

- **Decision (S8): `RunDocument.assessment` is `dict | None`, not `AssessmentEnvelope | None`.**
  - Rationale: D.7 explicitly notes that envelope schema changes shouldn't force a collection migration. Keeping it as `dict` at the document layer means a v0.2 envelope addition lands without re-writing every historical run. Validation happens at the agent's API boundary before write.
  - Alternatives considered: typed `AssessmentEnvelope | None` (tighter at the model layer but forces lockstep migrations — rejected by D.7).

- **Decision (S9): Embedding dimension default is 768 (per Decision L's `text-embedding-005`) and exposed as `EMBEDDING_DIMENSIONS_DEFAULT` — used by the vector index configs but documented as NOT a locked Atlas config.**
  - Rationale: This is the OQ-7 surfacing. The default is the SRS-stated dimension for `text-embedding-005`. Locking the Atlas index before the recall-vs-cost check on a small corpus would be premature. `infra` consumes whatever the user lands.
  - See OQ-7 below.

## Invariants Touched

- **Invariant 1 (Determinism boundary — narration numbers are typed fields).** Preserves.
  - `FloodMetrics` in `envelope.py:153-177` carries every flood number as a typed field (`flooded_area_km2`, `max_depth_m`, `mean_depth_m`, `p95_depth_m`, `affected_buildings_count`, `population_exposed`, …).
  - `ToolCallCompletePayload.metrics: dict` in `ws.py:274` is the tool-specific structured channel — the LLM narrates from these dict entries, never from free text in `result_summary`.
  - Every `IntensityIndicators` numeric quantity is `ClaimSet | None` (`event.py:184-234`), guaranteeing the narrated number is a typed `consensus_value`.

- **Invariant 3 (Engine registration, not modification — no hazard-specific fields in the shared base).** Preserves.
  - `AssessmentEnvelope.metrics: BaseMetrics` (`envelope.py:232`) — `BaseMetrics` is empty by design.
  - Subtype payloads are slot-typed (`flood: FloodPayload | None`, plus permissive `dict | None` slots for the v0.2+ subtypes — `envelope.py:243-247`).
  - `@model_validator` (`envelope.py:249-265`) enforces exactly one populated subtype matching `hazard_type` — verified by `test_envelope.py::test_two_subtypes_populated_rejected` and `::test_no_subtype_populated_rejected` and `::test_wrong_subtype_for_hazard_rejected`.

- **Invariant 6 (Metadata-payload pattern — Mongo metadata, GCS payloads).** Preserves.
  - `RunDocument.assessment: dict | None` (`collections.py:165`) — the envelope is the metadata; payload artifacts (rasters, vectors, raw solver output) live at the GCS URIs referenced inside it (`ResultLayer.uri`, `RunResult.output_uri`).
  - `ProjectDocument.qgs_uri` (`collections.py:109`), `ArticleDocument.html_uri` (`collections.py:201`) — payload references, not embedded blobs.
  - The five collection schemas are pure metadata indices over GCS-stored artifacts.

- **Invariant 7 (Claims carry provenance).** Preserves.
  - `NumericClaim` / `ClaimSet` shapes in `event.py:95-122` carry typed per-source evidence with `source_type` (closed Literal mapped from a curated table — *never* LLM-judged), `source_id`, `source_url`, `observation_time`, `reporting_time`, optional `confidence`, `outlier_flag`.
  - `consensus_value` is the narrated number; contributing claims stay drillable on `claims`.
  - `Provenance` / `EventProvenance` / `CatalogReference` (`envelope.py:104-128`, `event.py:170-175`) carry sources as typed `DataSource{name,uri,accessed_at}` records — verified by `test_event.py::test_numeric_claim_source_type_closed_enum` and `::test_claim_set_consensus_value_round_trips`.

- **Invariant 8 (Cancellation is first-class).** Preserves.
  - `ExecutionHandle.workflows_execution_id: str` (`execution.py:80`) — the Cloud Workflows execution identifier, the pinned cancellation seam. `agent` calls Workflows `terminate` with this field; one handle type, no per-backend variants. Verified by `test_execution.py::test_execution_handle_pins_workflows_execution_id_invariant_8`.
  - `cancelled` is a distinct terminal state separate from `failed` everywhere: `PipelineStepState` (`ws.py:291`), `RunResult.status` (`execution.py:99`), `RunDocument.status` (`collections.py:151`), `PipelineStepSummary.state` (`collections.py:263`), `PipelineSnapshot.final_state` (`collections.py:274`), `ToolCallSummary.state` (`collections.py:239`).
  - A.6 error codes include `CANCELLED` and `USER_INPUT_CANCELLED` distinct from other failures (`ws.py:132-133`).

- **Invariant 9 (Confirmation before consequence — no cost theater).** Preserves.
  - `ConfirmationRequestPayload` (`ws.py:424-437`) has `title`, `description`, `estimated_duration_seconds`, `default_timeout_seconds` — **no cost field**. Verified by `test_ws.py::test_confirmation_request_has_no_cost_field`.
  - `RunDocument` (`collections.py:135-179`) has no cost field. Verified by `test_collections.py::test_run_doc_has_no_cost_field`.
  - `FloodMetrics` has no cost field. Verified by `test_envelope.py::test_flood_metrics_has_no_cost_field`.

## Open Questions

- **OQ-S1 (Package name).** Settled on `grace2-contracts` (PyPI) / `grace2_contracts` (import). Alternatives: `grace_contracts`, `grace2_schema`. Tentative recommendation: keep as is — matches PROJECT_STATE's expected layout. **Asks user to confirm.**

- **OQ-S2 (v0.2+ subtype payload typing).** `AssessmentEnvelope` carries `groundwater`/`wildfire`/`seismic`/`spill` as permissive `dict | None` slots until each engine lands its own `*Payload` model. Decision: ship these as `dict | None` rather than excluding them entirely so a v0.2 envelope serializes/deserializes without a schema change. Tentative recommendation: keep — Appendix B.6b says exactly this for v0.1. **Surfaces as a no-op confirmation.**

- **OQ-S3 (Intensity mapping for `levee_failure` / `intense_rainfall`).** Appendix C.4 lists per-event-type intensity models but doesn't dedicate one for `levee_failure` or `intense_rainfall`. We map them to `dam_failure` and `rainfall` respectively via `_EVENT_TYPE_TO_INTENSITY` (`event.py:254-265`) so the dispatcher (C.7) can read a known field. Tentative recommendation: keep; surface as a proposed Appendix C amendment (add the mapping table explicitly, or add dedicated `LeveeFailureIntensity` and `IntenseRainfallIntensity` models in a v0.2 schema bump).

- **OQ-S4 (`SessionStatePayload` carries `dict`/`list` instead of typed nested Appendix-D models).** The nested shapes in `session-state` are the JSON forms of `ChatMessage` / `ProjectLayerSummary` / `PipelineSnapshot` / `MapView`. Typing them as `list[ChatMessage]` etc. would create a circular import between `ws.py` and `collections.py` (collections imports `EventMetadata`; ws would import collections). Tentative recommendation: keep `dict`/`list` at the wire layer; agent serializes the real D.6 models into them on send and validates on receive. The alternative is a third "shared models" module which is over-engineering for one message.

- **OQ-S5 (Schema export count clarification).** Kickoff said "28 generated JSON schemas"; actual is 35 because every `ALL_PAYLOADS` entry gets its own `ws_*.json` (14 + 7 = 21). Not a defect; surfacing for the audit record.

- **OQ-7 (Embedding dimension — verify before locking Atlas index config).** **Recommended: 768, with mandatory recall-vs-cost validation on a small corpus before locking.**
  - **Number:** `EMBEDDING_DIMENSIONS_DEFAULT = 768` (`collections.py:67`); this feeds `RUNS_VECTOR_INDEX`, `ARTICLES_VECTOR_INDEX`, `EVENTS_VECTOR_INDEX` as a documented constant, NOT a locked Atlas config (`collections.py:65-66`).
  - **Rationale:**
    1. **Match the embedding model.** Decision L pins `text-embedding-005` as the default; its native output dimension is 768. Choosing 256 or 128 means a dimensionality-reduction post-step (PCA or Matryoshka-style truncation) — extra complexity, extra parameters to tune, extra failure mode when re-embedding old documents.
    2. **Corpus is small at MVP.** For the first 1k-10k articles + events, Atlas index size at 768 dims is well within Flex's 5 GB ceiling: ~3 KB/vector × 10k ≈ 30 MB per collection. The cost-vs-recall trade only matters at scale.
    3. **Recall headroom for HEP cross-source claim aggregation.** FR-HEP-6 (claim aggregation) needs high-recall search across articles for the same event — 768 dims preserves the subtle semantic distinctions (storm name + location + intensity descriptor) that lower dimensions blur.
  - **Validation gate (`infra` to perform, blocks Atlas index creation in `infra` job-0014 or successor):** Embed 100-300 hand-curated GRACE-relevant articles, run a small recall test against known-relevant pairs at 768 / 384 / 256 dims. If recall@10 stays above 0.85 at 256 dims, switch to 256 (saves ~3x index size). If only 768 hits target, stay at 768.
  - **Downstream coupling:** `infra` consumes whatever the user lands; the `numDimensions` field in the three vector index configs is the single point of change. `engine` (HEP worker code that embeds articles + events) imports `EMBEDDING_DIMENSIONS_DEFAULT` so the embedding call and the index agree.

### Amendment Log — proposed SRS diffs (the user lands these)

The SRS appendices are *preemptive* (per `agents/schema.md` Domain Discipline). Implementation surfaced these divergences/extensions; each is proposed back as a concrete Appendix amendment for the user to land in the SRS source document.

**A1. Appendix A.3 `user-message` — add `research_mode` field (FR-WC-15).**

The orchestrator pinned `research_mode` on `user-message` as the FR-WC-15 toggle carrier so the web→agent→engine strategy path is fixed before anyone invents a second one. Proposed diff for Appendix A.3:

```diff
 ### A.3 Client-to-agent messages

 #### `user-message`
 User-submitted text input.

 **Payload:**
-- text: str — user's input
+- text: str — user's input
+- research_mode: Literal["research", "deep_research"] = "research" — FR-WC-15
+  strategy toggle carried at the message layer. v0.1 always runs research mode
+  regardless of value; "deep_research" reserved for the FR-HEP-4 deep-research
+  path that the agent will branch to in v0.2. Pinned now so the carrier shape
+  cannot drift across web/agent/engine when deep-research lands.
```

Implementation: `ws.py:145` defines `ResearchMode = Literal["research", "deep_research"]`; `ws.py:154` adds the field with default `"research"`. Test: `test_ws.py::test_user_message_default_research_mode` / `::test_user_message_deep_research_mode` / `::test_user_message_unknown_research_mode_rejected`.

**A2. Appendix C.4 — explicit `event_type → intensity field` mapping table (or new dedicated intensity models).**

Appendix C.4 lists `HurricaneIntensity`, `RainfallIntensity`, `DamFailureIntensity`, etc., but does not provide a dedicated intensity model for `levee_failure` (an `event_type` in C.2) or `intense_rainfall` (also in C.2). The dispatcher in C.7 needs a deterministic mapping to know which `IntensityIndicators` field to read. Proposed Appendix C.4 amendment: add a small table:

```
event_type           intensity field
─────────────────    ─────────────────
hurricane            hurricane
tropical_storm       tropical_storm
atmospheric_river    atmospheric_river
intense_rainfall     rainfall            # NEW: was implicit
dam_failure          dam_failure
levee_failure        dam_failure         # NEW: reuses dam_failure machinery
storm_surge          storm_surge
river_flood          river_flood
flash_flood          flash_flood
other                generic
```

Alternative (cleaner but a v1→v2 bump for the contract): add `LeveeFailureIntensity` and `IntenseRainfallIntensity` as their own models. The mapping-table form is preferred for v0.1 because it's additive documentation, no model change.

Implementation: `event.py:254-265` carries this mapping as `_EVENT_TYPE_TO_INTENSITY`, enforced by `EventMetadata`'s `@model_validator`.

**A3. Appendix B.6b — clarify that v0.1 carries `groundwater`/`wildfire`/`seismic`/`spill` as permissive `dict | None` slots in the envelope.**

Appendix B.2 lists subtype-payload slots; Appendix B.6b for `show_hazard_layer` already notes that discovery payloads are permissive dicts validated at the workflow layer in v0.1. Proposed amendment: extend the B.6b note to apply uniformly to v0.2+ hazard subtypes — until each subtype's engine lands its own typed `*Payload`, the envelope carries it as `dict | None` so a forward-compatible serialization is possible without forcing a schema bump per subtype landing.

**A4. Appendix D — clarify that `RunDocument.assessment` is `dict` (not the `AssessmentEnvelope` model) in the storage layer.**

D.7 already notes this in prose. Proposed amendment: tighten the D.3 schema sketch to write `assessment: dict | None` explicitly so consumers don't expect type-narrowed access at the document layer.

**A5. Appendix A.6 — add the cancel-path error codes that don't appear in the prose list.**

A.6 lists base error codes; user-input cancellation paths (`SPATIAL_INPUT_TIMEOUT`, `DISAMBIGUATION_TIMEOUT`, `CLARIFICATION_TIMEOUT`, `USER_INPUT_CANCELLED`) and the generic `CANCELLED` are present in the implementation (`ws.py:118-134`) but should be explicit in the appendix table.

## Dependencies and Impacts

- **Depends on:** none in code; only on SRS v0.3.12 Appendices A-D (the authoritative starting stubs) and Decisions G/L/M.
- **Unblocks:**
  - **job-0015 (agent ADK skeleton)** — the agent server imports `grace2_contracts.ws` to serialize/deserialize every WebSocket message, imports `grace2_contracts.execution.ExecutionHandle` to call Workflows `terminate`, imports `grace2_contracts.tool_metadata.REQUIRED_DOCSTRING_SECTIONS` + `TOOL_CATEGORIES` to register tools. The Appendix A `research_mode` field is in place so agent's user-message handler can branch on it from day one.
  - **job-0016 (web stub)** — the web client serializes/deserializes the same Appendix A payloads (typically via codegen from `schemas/*.json` or a TypeScript handwriting pass keyed off this Python source of truth). `map-command` args, `pipeline-state`, `confirmation-request` (no cost field), and the three user-input request/response pairs are all stable.
  - **job-0014 (infra: GCP + Atlas import)** — the three vector index configs (`RUNS_VECTOR_INDEX`, `ARTICLES_VECTOR_INDEX`, `EVENTS_VECTOR_INDEX`) and the `SESSIONS_TTL` config are the contract `infra` provisions. **OQ-7 must be resolved (or the recall-vs-cost validation gated) before `infra` locks Atlas index `numDimensions`.** Engine and testing will also consume these.
- **Affects (follow-up):**
  - **engine** — `aggregate_claims_across_sources` (FR-HEP-6) populates `ClaimSet.consensus_value`; the source-to-tier table that drives `NumericClaim.source_type` is engine-curated; HEP workers write `RunDocument`/`ArticleDocument`/`EventDocument` against these shapes.
  - **testing** — round-trip and negative-control tests for `web ↔ agent` over a real WebSocket frame; `model_dump(mode="json", by_alias=True)` against a live Atlas Flex insert/read.
  - **Possible contract revisions** when consumers push back (the AGENTS.md "Consumer Pushback" motion) — expected especially around `ToolCallCompletePayload.metrics` (currently `dict`; engine may want a discriminated union once two non-flood tool categories land).

## Verification

### AC1 — Package installs and tests pass in a fresh `virtualenv`

Note: `python3 -m venv` is unavailable on this Debian dev box because `python3-venv` (which provides `ensurepip`) is not installed — see PROJECT_STATE "Environment facts". `virtualenv` (already on `~/.local/bin/virtualenv`) is the working substitute, which bundles `pip`. Surfacing as a minor environment substitution; the venv is still fresh + isolated.

**Verbatim transcript — venv create + install:**

```
$ virtualenv /tmp/co-venv 2>&1 | tail -10
created virtual environment CPython3.13.5.final.0-64-x86_64 in 76ms
  creator CPython3Posix(dest=/tmp/co-venv, clear=False, no_vcs_ignore=False, global=False)
  seeder FromAppData(download=False, pip=bundle, via=copy, app_data_dir=/home/nate/.cache/virtualenv)
    added seed packages: pip==26.1.1
  activators BashActivator,CShellActivator,FishActivator,NushellActivator,PowerShellActivator,PythonActivator,XonshActivator

$ . /tmp/co-venv/bin/activate && pip install -e packages/contracts 2>&1 | tail -20
Collecting typing-inspection>=0.4.2 (from pydantic<3,>=2->grace2-contracts==0.1.0)
  Using cached typing_inspection-0.4.2-py3-none-any.whl.metadata (2.6 kB)
Using cached pydantic-2.13.4-py3-none-any.whl (472 kB)
Using cached pydantic_core-2.46.4-cp313-cp313-manylinux_2_17_x86_64.manylinux2014_x86_64.whl (2.1 MB)
Using cached python_ulid-3.1.0-py3-none-any.whl (11 kB)
Using cached annotated_types-0.7.0-py3-none-any.whl (13 kB)
Using cached typing_extensions-4.15.0-py3-none-any.whl (44 kB)
Using cached typing_inspection-0.4.2-py3-none-any.whl (14 kB)
Building wheels for collected packages: grace2-contracts
  Building editable for grace2-contracts (pyproject.toml): started
  Building editable for grace2-contracts (pyproject.toml): finished with status 'done'
  Created wheel for grace2-contracts: filename=grace2_contracts-0.1.0-0.editable-py3-none-any.whl size=3929 sha256=aff41b5aaf680032a763cd2c5e04aa5f0a59124956324ae81bc7e57821f51b96
  Stored in directory: /tmp/claude-1000/pip-ephem-wheel-cache-cs_gc7o_/wheels/3b/8d/4e/fb32b82816e5a5054e19e508896cd7aa1d76e3e174e84eb807
Successfully built grace2-contracts
Installing collected packages: typing-extensions, python-ulid, annotated-types, typing-inspection, pydantic-core, pydantic, grace2-contracts

Successfully installed annotated-types-0.7.0 grace2-contracts-0.1.0 pydantic-2.13.4 pydantic-core-2.46.4 python-ulid-3.1.0 typing-extensions-4.15.0 typing-inspection-0.4.2

$ pip install pytest 2>&1 | tail -5
Successfully installed iniconfig-2.3.0 packaging-26.2 pluggy-1.6.0 pygments-2.20.0 pytest-9.0.3
```

**Verbatim transcript — pytest run:**

```
$ cd packages/contracts && pytest tests -v 2>&1 | tail -120
============================= test session starts ==============================
platform linux -- Python 3.13.5, pytest-9.0.3, pluggy-1.6.0 -- /tmp/co-venv/bin/python
cachedir: .pytest_cache
rootdir: /home/nate/Documents/GRACE-2/packages/contracts
configfile: pyproject.toml
collecting ... collected 91 items

tests/test_catalog.py::test_catalog_entry_roundtrip_idempotent PASSED    [  1%]
tests/test_catalog.py::test_topic_must_have_at_least_one_entry PASSED    [  2%]
tests/test_catalog.py::test_unknown_format_rejected PASSED               [  3%]
tests/test_catalog.py::test_last_verified_iso_date_string_roundtrip PASSED [  4%]
tests/test_collections.py::test_project_doc_roundtrip_and_id_aliasing PASSED [  5%]
tests/test_collections.py::test_run_doc_status_supports_cancelled PASSED [  6%]
tests/test_collections.py::test_run_doc_has_no_cost_field PASSED         [  7%]
tests/test_collections.py::test_run_doc_with_user_spatial_input PASSED   [  8%]
tests/test_collections.py::test_article_doc_roundtrip PASSED             [  9%]
tests/test_collections.py::test_event_doc_is_event_metadata_shape PASSED [ 10%]
tests/test_collections.py::test_session_doc_with_pipeline_history_cancelled PASSED [ 12%]
tests/test_collections.py::test_vector_indexes_cover_runs_articles_events PASSED [ 13%]
tests/test_collections.py::test_embedding_dimension_default_is_768_oq7 PASSED [ 14%]
tests/test_collections.py::test_runs_vector_index_filter_paths_are_high_cardinality PASSED [ 15%]
tests/test_collections.py::test_articles_vector_index_filters_on_extraction_status PASSED [ 16%]
tests/test_collections.py::test_events_vector_index_filters_on_event_type_and_time_classification PASSED [ 17%]
tests/test_collections.py::test_sessions_ttl_config PASSED               [ 18%]
tests/test_common.py::test_new_ulid_is_26_char_crockford_base32 PASSED   [ 19%]
tests/test_common.py::test_now_utc_is_timezone_aware_utc PASSED          [ 20%]
tests/test_common.py::test_common_holder_roundtrip_idempotent PASSED     [ 21%]
tests/test_common.py::test_datetime_serializes_with_z_suffix PASSED      [ 23%]
tests/test_common.py::test_naive_datetime_serializes_as_utc_z PASSED     [ 24%]
tests/test_common.py::test_invalid_ulid_rejected PASSED                  [ 25%]
tests/test_common.py::test_bbox_inverted_lon_rejected PASSED             [ 26%]
tests/test_common.py::test_bbox_inverted_lat_rejected PASSED             [ 27%]
tests/test_common.py::test_bbox_out_of_range_rejected PASSED             [ 28%]
tests/test_common.py::test_extra_fields_forbidden PASSED                 [ 29%]
tests/test_common.py::test_time_range_roundtrip PASSED                   [ 30%]
tests/test_envelope.py::test_modeled_flood_envelope_roundtrip_idempotent PASSED [ 31%]
tests/test_envelope.py::test_envelope_type_is_a_discriminator PASSED     [ 32%]
tests/test_envelope.py::test_wrong_subtype_for_hazard_rejected PASSED    [ 34%]
tests/test_envelope.py::test_two_subtypes_populated_rejected PASSED      [ 35%]
tests/test_envelope.py::test_no_subtype_populated_rejected PASSED        [ 36%]
tests/test_envelope.py::test_flood_metrics_has_no_cost_field PASSED      [ 37%]
tests/test_envelope.py::test_base_metrics_stays_hazard_agnostic_extra_forbidden PASSED [ 38%]
tests/test_envelope.py::test_flooded_area_negative_rejected PASSED       [ 39%]
tests/test_envelope.py::test_grid_resolution_must_be_positive PASSED     [ 40%]
tests/test_envelope.py::test_result_layer_aligns_with_load_layer_args PASSED [ 41%]
tests/test_event.py::test_hurricane_event_roundtrip_idempotent PASSED    [ 42%]
tests/test_event.py::test_intensity_bare_float_rejected_decision_m PASSED [ 43%]
tests/test_event.py::test_intensity_bare_int_rejected_decision_m PASSED  [ 45%]
tests/test_event.py::test_non_numeric_intensity_field_stays_scalar PASSED [ 46%]
tests/test_event.py::test_event_location_requires_bbox_or_place_name PASSED [ 47%]
tests/test_event.py::test_event_location_with_bbox_only_ok PASSED        [ 48%]
tests/test_event.py::test_event_location_with_place_name_only_ok PASSED  [ 49%]
tests/test_event.py::test_wrong_intensity_payload_for_event_type_rejected PASSED [ 50%]
tests/test_event.py::test_event_with_empty_intensity_allowed PASSED      [ 51%]
tests/test_event.py::test_intense_rainfall_maps_to_rainfall_payload PASSED [ 52%]
tests/test_event.py::test_river_flood_event_with_river_flood_intensity PASSED [ 53%]
tests/test_event.py::test_numeric_claim_source_type_closed_enum PASSED   [ 54%]
tests/test_event.py::test_claim_set_consensus_value_round_trips PASSED   [ 56%]
tests/test_execution.py::test_model_setup_roundtrip PASSED               [ 57%]
tests/test_execution.py::test_execution_handle_pins_workflows_execution_id_invariant_8 PASSED [ 58%]
tests/test_execution.py::test_run_result_status_supports_cancelled PASSED [ 59%]
tests/test_execution.py::test_layer_uri_maps_field_for_field_onto_load_layer_args PASSED [ 60%]
tests/test_export_schemas.py::test_export_writes_one_file_per_top_level_contract PASSED [ 61%]
tests/test_export_schemas.py::test_export_is_idempotent PASSED           [ 62%]
tests/test_export_schemas.py::test_each_exported_schema_is_valid_json PASSED [ 63%]
tests/test_ws.py::test_user_message_default_research_mode PASSED         [ 64%]
tests/test_ws.py::test_user_message_deep_research_mode PASSED            [ 65%]
tests/test_ws.py::test_user_message_unknown_research_mode_rejected PASSED [ 67%]
tests/test_ws.py::test_cancel_message PASSED                             [ 68%]
tests/test_ws.py::test_confirm_response PASSED                           [ 69%]
tests/test_ws.py::test_session_resume_empty_payload PASSED               [ 70%]
tests/test_ws.py::test_spatial_input_response_point PASSED               [ 71%]
tests/test_ws.py::test_spatial_input_response_cancelled PASSED           [ 72%]
tests/test_ws.py::test_disambiguation_response PASSED                    [ 73%]
tests/test_ws.py::test_clarification_response PASSED                     [ 74%]
tests/test_ws.py::test_agent_message_chunk PASSED                        [ 75%]
tests/test_ws.py::test_tool_call_start PASSED                            [ 76%]
tests/test_ws.py::test_tool_call_progress PASSED                         [ 78%]
tests/test_ws.py::test_tool_call_progress_percent_out_of_range_rejected PASSED [ 79%]
tests/test_ws.py::test_tool_call_complete_metrics_carried_as_dict PASSED [ 80%]
tests/test_ws.py::test_tool_call_failed PASSED                           [ 81%]
tests/test_ws.py::test_pipeline_state_cancelled_is_distinct_terminal PASSED [ 82%]
tests/test_ws.py::test_pipeline_state_invalid_step_state_rejected PASSED [ 83%]
tests/test_ws.py::test_map_command_load_layer_args_roundtrip PASSED      [ 84%]
tests/test_ws.py::test_map_command_zoom_to_bbox_args PASSED              [ 85%]
tests/test_ws.py::test_map_command_set_layer_opacity_clamped PASSED      [ 86%]
tests/test_ws.py::test_map_command_args_registry_covers_every_command PASSED [ 87%]
tests/test_ws.py::test_confirmation_request_has_no_cost_field PASSED     [ 89%]
tests/test_ws.py::test_session_state_payload PASSED                      [ 90%]
tests/test_ws.py::test_error_payload_uses_a6_codes PASSED                [ 91%]
tests/test_ws.py::test_error_payload_unknown_code_rejected PASSED        [ 92%]
tests/test_ws.py::test_location_resolved PASSED                          [ 93%]
tests/test_ws.py::test_spatial_input_request PASSED                      [ 94%]
tests/test_ws.py::test_disambiguation_request PASSED                     [ 95%]
tests/test_ws.py::test_clarification_request_requires_2_to_4_options PASSED [ 96%]
tests/test_ws.py::test_clarification_request_ok PASSED                   [ 97%]
tests/test_ws.py::test_envelope_payload_always_an_object PASSED          [ 98%]
tests/test_ws.py::test_every_a3_a4_a4b_payload_round_trips PASSED        [100%]

============================== 91 passed in 0.24s ==============================
```

**Result: PASS — 91/91 tests pass in fresh virtualenv in 0.24s on Linux/Python 3.13.5/pydantic 2.13.4.**

### AC2 — Round-trip + negative controls

Covered by AC1 — the test suite explicitly includes:
- `test_event.py::test_intensity_bare_float_rejected_decision_m` and `::test_intensity_bare_int_rejected_decision_m` — Decision M enforced (bare numeric where ClaimSet expected raises).
- `test_envelope.py::test_wrong_subtype_for_hazard_rejected` / `::test_two_subtypes_populated_rejected` / `::test_no_subtype_populated_rejected` — invariant-3 discriminator guard.
- `test_event.py::test_wrong_intensity_payload_for_event_type_rejected` — intensity discriminator guard.
- `test_ws.py::test_every_a3_a4_a4b_payload_round_trips` — full enumeration round-trip via `model_dump(mode="json") → model_validate → model_dump`.
- Cost-field absence asserted in three places (`test_ws.py::test_confirmation_request_has_no_cost_field`, `test_envelope.py::test_flood_metrics_has_no_cost_field`, `test_collections.py::test_run_doc_has_no_cost_field`).

**Result: PASS.**

### AC3 — Generated JSON Schemas exist and regeneration is scripted + idempotent

**Verbatim transcript — re-run schema export against existing schemas:**

```
$ grace2-export-schemas
Wrote 35 schema files to /home/nate/Documents/GRACE-2/packages/contracts/schemas
  assessment_envelope.json
  event_metadata.json
  claim_set.json
  numeric_claim.json
  project_document.json
  run_document.json
  article_document.json
  event_document.json
  session_document.json
  catalog_entry.json
  model_setup.json
  execution_handle.json
  run_result.json
  layer_uri.json
  ws_agent_message_chunk.json
  ws_cancel.json
  ws_clarification_request.json
  ws_clarification_response.json
  ws_confirm_response.json
  ws_confirmation_request.json
  ws_disambiguation_request.json
  ws_disambiguation_response.json
  ws_error.json
  ws_location_resolved.json
  ws_map_command.json
  ws_pipeline_state.json
  ws_session_resume.json
  ws_session_state.json
  ws_spatial_input_request.json
  ws_spatial_input_response.json
  ws_tool_call_complete.json
  ws_tool_call_failed.json
  ws_tool_call_progress.json
  ws_tool_call_start.json
  ws_user_message.json
```

**Idempotency check (copied schemas dir aside, re-ran export, `diff -r`):**

```
$ cp -r packages/contracts/schemas /tmp/schemas-before
$ grace2-export-schemas > /tmp/export-output.txt 2>&1
$ diff -r /tmp/schemas-before packages/contracts/schemas/ && echo "IDEMPOTENT: no diffs"
IDEMPOTENT: no diffs
```

Schemas directory contains 35 `.json` files, byte-identical on repeat invocation.

**Result: PASS.**

### AC4 — Report contains the `research_mode` amendment diff + OQ-7 + appendix divergences

See Amendment Log section above (A1-A5) and Open Questions OQ-7, OQ-S1..S5.

**Result: PASS.**

### Cleanup

```
$ deactivate; rm -rf /tmp/co-venv /tmp/schemas-before
cleanup done
```

### Overall

**Results: PASS — all four acceptance criteria met, 91/91 tests passing, schema export idempotent, every required amendment surfaced.**
