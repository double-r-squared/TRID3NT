# Audit: Contracts v0 from SRS Appendices A–D (pydantic v2)

**Job ID:** job-0013-schema-20260605
**Sprint:** sprint-03
**Auditor:** Development Orchestrator
**Status:** approved

## Task Assignment

**Specialist:** schema
**Prerequisites:** none for authoring (runs parallel with job-0012; you create `packages/contracts/` yourself). Read SRS Appendices A–D in FULL — they are your authoritative stubs.
**SRS references:** FR-AS-5 + Appendix A, FR-TA-1 + Appendix B, FR-HEP-5 + Appendix C, FR-MP-5 + Appendix D, FR-PHC-2, Decision L, Decision M, OQ-7.

### Scope

`packages/contracts/` — a small installable pure package (pydantic v2; name it; surface naming as Open Question):

1. **Appendix A** → `ws.py`: discriminated envelope + every message type (A.1–A.4b), error codes (A.6). Include the **`research_mode` field on `user-message`** per the orchestrator's pinned toggle-carrier seam — this is an Appendix A amendment: write the exact proposed appendix diff in your report for the user to land (FR-WC-15).
2. **Appendix B** → `envelope.py`: `AssessmentEnvelope` with `envelope_type` + `hazard_type` discriminators, supporting types, `FloodPayload`/`FloodMetrics`, wire form via `model_dump(mode="json")`.
3. **Appendix C** → `event.py`: `EventMetadata`, `EventLocation` (granularity + precision_class), intensity discriminated union, `NumericClaim` + `ClaimSet`. Every numeric intensity quantity is `ClaimSet | None`, never a bare number (Decision M).
4. **Appendix D** → `collections.py`: the five collection models; vector index configs as documented constants. **Surface OQ-7** (embedding dimension) with a recommendation — do not lock an index config.
5. **`CatalogEntry`** (FR-PHC-2) → `catalog.py`.
6. **Solver shapes** → `execution.py`: `ModelSetup`, `RunResult`, `ExecutionHandle` (Cloud Workflows execution-id field — name it explicitly; the pinned cancellation seam), `LayerURI` aligned field-for-field with `map-command load-layer` args.
7. **JSON Schema export** scripted → `packages/contracts/schemas/*.json`; round-trip tests in `packages/contracts/tests/`.
8. **Amendment log**: every place your implementation diverges from or extends an appendix gets a proposed-SRS-diff entry in your report (the appendices are *preemptive*; divergence is expected and must be visible — you never edit the SRS).

### File ownership (exclusive)
`packages/contracts/**`. Nothing else.

### Cross-cutting principles in force
*No legacy support pre-MVP* (the dead `src/grace2_contracts` is not a reference — work from the appendices), *surface uncertainty*, *live E2E validation required*.

### Acceptance criteria (reviewer re-runs)
- Package installs in a fresh venv (`python3 -m venv /tmp/c-venv && pip install -e packages/contracts && pytest packages/contracts/tests`) — transcript in report; venv deleted after
- Round-trip (model → JSON → model) tests pass for every message type and the envelope; an intensity field with a bare float fails validation (negative test)
- Generated JSON Schemas exist and regeneration is scripted
- Report contains the Appendix-A `research_mode` amendment diff + OQ-7 surfacing + any other appendix divergences

## Assessment

`packages/contracts/` shipped clean: 10 pydantic-v2 modules, 91/91 round-trip + negative-control tests pass in a fresh virtualenv, 35 JSON schemas exported idempotently, ExecutionHandle's Cloud-Workflows execution-id field pinned, intensity discriminated union enforced (bare float rejected), and a 5-entry Amendment Log proposing precise SRS diffs back to the user. Commit `2ce9272` is namespaced + co-author-trailered; file-ownership scope clean (packages/contracts/** only). Adversarial in-workflow reviewer verdict: **approve** with three low-severity-only cosmetic notes (all accepted, none blocking).

## Invariant Check

- **Determinism boundary:** pass — `FloodMetrics` (`envelope.py:157-177`) carries every flood number as typed fields; `IntensityIndicators` numerics are `ClaimSet | None` (`event.py:184-234`); `ToolCallCompletePayload.metrics: dict` (`ws.py:274`) is the structured channel for tool outputs. No bare floats in any narrated quantity.
- **Deterministic workflows:** n/a — contracts package; no workflow Python here. Pinned `ExecutionHandle.workflows_execution_id` (`execution.py:80`) is the seam workflows will cite.
- **Engine registration, not modification:** pass — `AssessmentEnvelope.metrics: BaseMetrics` (`envelope.py:232`) empty by design, subtype payloads added via discriminator without changing the envelope. `@model_validator` enforces exactly-one subtype matching `hazard_type`; negative tests cover two-subtype, zero-subtype, wrong-subtype.
- **Rendering through QGIS Server:** n/a — no map-server code. `LoadLayerArgs` is field-for-field with `ResultLayer` / `LayerURI` so postprocess output flows to QGIS Server via WMS/WMTS/WFS with zero translation.
- **Tier separation:** n/a — no client/data-path code. Shapes preserve the seam.
- **Metadata-payload pattern:** pass — `RunDocument.assessment: dict | None` (`collections.py:165`), `ProjectDocument.qgs_uri` (`collections.py:109`), `ArticleDocument.html_uri` (`collections.py:201`) — payload URIs in Mongo, payload bytes in GCS, no bucket-enumeration path.
- **Claims carry provenance:** pass — `NumericClaim` / `ClaimSet` (`event.py:95-122`) carry per-source typed evidence; `SourceType` (`event.py:71-78`) is a closed `Literal` (never LLM-judged); `consensus_value` is the narrated number; `Provenance` / `EventProvenance` / `CatalogReference` carry typed `DataSource` records.
- **Cancellation is first-class:** pass — `ExecutionHandle.workflows_execution_id` (`execution.py:80`) is the pinned field name `agent` will pass to Workflows `terminate`; `PipelineStep.state` includes `cancelled` as a distinct terminal value (`ws.py:226-242`).
- **Confirmation before consequence — and no cost theater:** pass — `ConfirmationRequestPayload` has no cost field; reviewer grep across the package confirms no `cost`/`usd`/`cents`/`$` strings.
- **Minimal parameter surface:** pass — `map-command load-layer` args mirror `ResultLayer` exactly; tool-metadata conventions in `tool_metadata.py` document the docstring section requirements without inflating the parameter surface.

## Dependency Check

- **Prerequisites satisfied:** yes — SRS Appendices A–D + Decisions G/L/M are the only inputs; specialist read them and produced precise diffs back at the five places implementation surfaced gaps.
- **Downstream impacts:**
  - job-0014 (infra GCP+Atlas): the three Atlas Vector Search index configs (`RUNS_VECTOR_INDEX`, `ARTICLES_VECTOR_INDEX`, `EVENTS_VECTOR_INDEX`) reference `EMBEDDING_DIMENSIONS_DEFAULT = 768` as a documented constant. infra performs the recall validation gate (768 / 384 / 256 on 100–300 curated articles, recall@10 ≥ 0.85 threshold) before locking the Atlas index. Routing: infra.
  - job-0015 (agent ADK): imports `grace2_contracts.ws` (Appendix A protocol), `grace2_contracts.execution.ExecutionHandle` (cancellation chain), `grace2_contracts.tool_metadata.{REQUIRED_DOCSTRING_SECTIONS, TOOL_CATEGORIES}` (tool registry). `research_mode` field is in place from day one. Routing: agent.
  - job-0016 (web stub): codegens TS types from `schemas/*.json` (or hand-mirrors). `map-command` args, `pipeline-state`, `confirmation-request`, three user-input request/response pairs all stable. Routing: web.
  - job-0017 (testing): contract tests already there; collects them under `make test`. Routing: testing.
  - **SRS amendment proposals A1–A5** (user-only landing). Routing: user.

## Decisions Validated

- **Package naming `grace2-contracts` (PyPI) / `grace2_contracts` (import):** agree — matches PROJECT_STATE expected layout; alternatives (`grace_contracts`, `grace2_schema`) considered and rejected.
- **`extra="forbid"` everywhere + `validate_assignment=True`:** agree — catches drift at construction time, mirrors Decision G's deterministic-workflows posture.
- **ULID for every identity field via `python-ulid` + `Annotated[str, AfterValidator]`:** agree — 26-char Crockford base32, time-sortable, URL-safe; round-trips through JSON as a string.
- **UTC datetimes with literal `Z` suffix via `PlainSerializer`:** agree — pins the wire form and avoids tz-aware vs naive drift.
- **`hazard_type` open-enum Literal vs Enum:** agree — open-enum (Literal) preserves forward-extensibility per the engine-registration invariant.
- **`@model_validator(mode="after")` enforcing exactly-one subtype matching `hazard_type`:** agree — necessary guard against engine-bypass (Invariant 3). Three negative tests confirm.
- **v0.2+ subtype payloads (`groundwater`/`wildfire`/`seismic`/`spill`) as permissive `dict | None`** until each engine lands its typed payload: agree — additive; no schema bump per engine landing. Surfaced as A3 amendment.
- **`MESSAGE_TYPE` / `COMMAND` as `ClassVar[str]`:** agree — pydantic v2 treats untyped class attributes as fields; `ClassVar` is the correct disambiguation. Mid-run fix caught by round-trip suite — exactly the failure mode the tests exist to catch.
- **`EMBEDDING_DIMENSIONS_DEFAULT = 768` as documented constant, NOT locked Atlas config:** agree — defers the lock to `infra`'s validation gate; mirrors Decision L's `text-embedding-005` native dimension and gives recall headroom for HEP cross-source claim aggregation (FR-HEP-6).

## Open Questions Resolved

- **OQ-S1 (Package name `grace2-contracts`):** confirmed — keep as is. No user objection expected; PROJECT_STATE layout already names `packages/contracts/`.
- **OQ-S2 (v0.2+ subtype payload typing as `dict | None`):** confirmed — keep. Carried into Amendment A3 for user landing.
- **OQ-S3 (`event_type → intensity field` mapping for `levee_failure`/`intense_rainfall`):** confirmed — implementation maps `levee_failure → dam_failure` and `intense_rainfall → rainfall` (`event.py:254-265`) via `_EVENT_TYPE_TO_INTENSITY`. Carried into Amendment A2 for user landing.
- **OQ-S4 (cancel-path error codes not in A.6 prose):** confirmed — implementation has the codes (`ws.py:118-134`); A.6 prose lacks them. Carried into Amendment A5 for user landing.
- **OQ-S5 (35 schemas vs kickoff's "28" in the closeout instruction):** resolved — 35 is correct. Every `ALL_PAYLOADS` entry gets its own `ws_*.json` (21 ws + 14 named contracts); the kickoff's "28" was an approximate count in the closeout instructions, not a kickoff acceptance criterion. No action.
- **OQ-7 (embedding dimension):** recommendation **768 dims**, with `infra` validation gate. Rationale: matches `text-embedding-005` native dimension (Decision L); MVP corpus (1k–10k articles) fits ~30 MB per index well under Flex 5 GB ceiling; preserves recall headroom for HEP cross-source claim aggregation. Validation gate (infra performs in job-0014 or successor): embed 100–300 hand-curated articles; recall test at 768/384/256 dims with recall@10 ≥ 0.85 threshold. If 256 passes, switch to 256 for ~3× index-size savings; else stay 768. The vector-index configs in `collections.py` reference `EMBEDDING_DIMENSIONS_DEFAULT` so the lock point is a single constant change.

## Follow-up Actions

- **Surface SRS Amendment proposals A1–A5 to the user for landing in `docs/SRS_v0.3.md`** (orchestrator carries the diffs; user is the system of record per SRS-ownership rule). The 5 are:
  - **A1** Appendix A.3 — add `research_mode: Literal["research","deep_research"]="research"` to `user-message` payload (FR-WC-15 toggle carrier seam pin).
  - **A2** Appendix C.4 — explicit `event_type → intensity field` mapping table (additive doc; covers `levee_failure → dam_failure`, `intense_rainfall → rainfall`).
  - **A3** Appendix B.6b — clarify uniform `dict | None` slot pattern for v0.2+ subtype payloads until each engine lands its typed payload.
  - **A4** Appendix D.3 — tighten storage-layer note: `RunDocument.assessment: dict | None` explicitly (avoid type-narrowed-access expectations at the document layer).
  - **A5** Appendix A.6 — add cancel-path error codes (`SPATIAL_INPUT_TIMEOUT`, `DISAMBIGUATION_TIMEOUT`, `CLARIFICATION_TIMEOUT`, `USER_INPUT_CANCELLED`, `CANCELLED`) to the prose table.
  - Routing: orchestrator → user. Priority: high (small additive edits; clears divergences before downstream specialists hit them).
- **Embedding-dimension validation gate** before locking Atlas Vector Search index config (token-budget triage, infra to perform). Routing: infra (job-0014). Priority: high (blocks Atlas vector-index creation in `infra`).
- **Install `python3-venv` on the Debian dev box** (or document `virtualenv` as the canonical recipe across kickoffs and PROJECT_STATE Environment facts). Specialist substituted `virtualenv` and surfaced the substitution correctly per *diagnose before fix*. Routing: orchestrator (PROJECT_STATE update) + optional infra (apt install). Priority: low.
- **PROJECT_STATE.md updates** (this audit closure): contracts package status now `installed, 91 tests, 35 schemas, idempotent export`; the five amendment proposals tracked in known issues until landed; `EMBEDDING_DIMENSIONS_DEFAULT = 768` recorded in contracts-in-force.
  - Routing: orchestrator. Priority: high.
- **Close job-0013** (this audit). Routing: orchestrator. Priority: high.
- **Launch job-0014 (Stage B — infra: GCP project + Atlas Flex import)** after this closure. Kickoff already revised for Linux+Flex+import flow. Routing: orchestrator. Priority: high.

## Sign-off

- **Ready to move to complete:** yes
- All four kickoff acceptance criteria pass on live re-run (AC1 fresh-venv install + 91/91 pytest in 0.25s; AC2 round-trip identity + bare-float negative test; AC3 schema regen idempotent via `diff -r`; AC4 amendment log + research_mode + OQ-7 with concrete recommendation).
- Invariants 1, 3, 6, 7, 8, 9 walked and preserved with file:line citations; 2, 4, 5, 10 correctly n/a (no runtime/server surface in a contracts package).
- 5 specialist OQs resolved (S1 confirm, S2 confirm/A3, S3 confirm/A2, S4 confirm/A5, S5 cosmetic) + OQ-7 resolved with recommended 768 + validation gate.
- Reviewer's three low-severity findings accepted: (a) cosmetic test count of 11 vs 12 for `test_common.py` — no action; (b) `virtualenv` substitution for `python3 -m venv` — documented, no defect; (c) 35-vs-28 schema count clarification — surfaced as OQ-S5, resolved.
- Five SRS amendment proposals surfaced to user (A1–A5) — orchestrator carries; user lands.
- Revisions: 0.
