# Report: Appendix D.6 PipelineStepSummary fields + FR-DC-2 AtomicToolMetadata

**Job ID:** job-0030-schema-20260606
**Sprint:** sprint-06
**Specialist:** schema
**Task:** Extend Appendix D.6 `PipelineStepSummary` with `progress_percent: int | None` (0..100), `error_code: str | None` (SCREAMING_SNAKE_CASE), `error_message: str | None` (<=512 chars), and add a NEW `AtomicToolMetadata` pydantic model carrying the FR-DC-2 four-class TTL field every external-API atomic tool declares at registration (with `source_class`, `cacheable`, and a cross-field `model_validator`). Regenerate JSON Schemas idempotently, edit Appendix D.6 prose, regenerate the SRS monolith, land >=5 new contracts tests.
**Status:** ready-for-audit

## Summary

Extended `PipelineStepSummary` (D.6) with three optional fields and shipped a new `AtomicToolMetadata` model in a fresh `grace2_contracts.tool_registry` module — closing OQ-W-26-PIPELINE-STEP-FIELDS and providing the FR-CE-8 fail-fast registration shape the M4 atomic-tool starter set will consume. JSON Schema export auto-discovers the new model and re-emits `pipeline_step_summary.json` standalone so the new field surface is independently inspectable. All 91 prior tests still pass; +40 new tests land (parametrized cross-product), for 131 passed in 0.59s. SRS Appendix D.6 prose updated; `make srs` regenerates the monolith cleanly (+13 lines, matching the section delta).

## Changes Made

- `packages/contracts/src/grace2_contracts/collections.py`
  - Added module-level `_ERROR_CODE_RE` (compiled `^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*$`) + `_ERROR_MESSAGE_MAX_LEN = 512`.
  - Extended `PipelineStepSummary` with `progress_percent: int | None = Field(default=None, ge=0, le=100)`, `error_code: str | None = None` (with `@field_validator` enforcing SCREAMING_SNAKE_CASE shape), and `error_message: str | None = Field(default=None, max_length=512)`.
  - Updated the model docstring to call out the new fields, Invariant 1 (workflow-attributed progress, not LLM-estimated), Invariant 9 (no cost field), open-set A.6 error-code semantics, and the future tightening follow-up (`required` when `state in {"running","failed"}`).
- `packages/contracts/src/grace2_contracts/tool_registry.py` (NEW)
  - `TTLClass = Literal["static-30d","semi-static-7d","dynamic-1h","live-no-cache"]` and matching `TTL_CLASSES` tuple for parametrized tests and agent-side registry assertions.
  - `AtomicToolMetadata(GraceModel)` with `name: str (min_length=1)`, `ttl_class: TTLClass`, `source_class: str | None = None`, `cacheable: bool = True`, and an `@model_validator(mode="after")` `_validate_cacheable_consistency` enforcing FR-DC-6: `cacheable=True => ttl_class != "live-no-cache" AND source_class is non-empty`; `cacheable=False => ttl_class == "live-no-cache"`. Inherits `extra="forbid"` so no cost / latency-estimate fields can leak in.
- `packages/contracts/src/grace2_contracts/__init__.py` — added `tool_registry` to the modules import + `__all__` so consumers reach `AtomicToolMetadata` via `grace2_contracts.tool_registry.AtomicToolMetadata`.
- `packages/contracts/src/grace2_contracts/export_schemas.py`
  - Imported `tool_registry`.
  - Added two new explicit exports: `("pipeline_step_summary", collections.PipelineStepSummary)` (D.6 standalone so the new field surface is diff-friendly without spelunking inside `session_document.json`) and `("atomic_tool_metadata", tool_registry.AtomicToolMetadata)`.
- `packages/contracts/schemas/session_document.json` — regenerated; the nested `PipelineStepSummary` definition now carries `progress_percent` / `error_code` / `error_message`.
- `packages/contracts/schemas/pipeline_step_summary.json` (NEW) — standalone D.6 model schema.
- `packages/contracts/schemas/atomic_tool_metadata.json` (NEW) — FR-DC-2 registration metadata schema.
- `packages/contracts/tests/test_collections.py` — appended 6 new test functions (5 of them parametrized across 2-10 cases each) covering the three new D.6 fields: round-trip + default-None, `progress_percent` accepts 0/1/42/99/100 and rejects -1/101/200/1_000_000, `error_code` accepts SFINCS_TIMEOUT / DEM_SOURCE_UNAVAILABLE / RATE_LIMITED / A / X_1 / FOO_BAR_BAZ_42 and rejects 10 malformed shapes (`camelCase`, `snake_case`, `kebab-case`, `lower_UPPER`, `_LEADING_UNDERSCORE`, `TRAILING_`, `DOUBLE__UNDERSCORE`, `1_LEADING_DIGIT`, `WITH SPACE`, empty string), and `error_message` accepts exactly 512 chars and rejects 513.
- `packages/contracts/tests/test_tool_registry.py` (NEW) — 9 test functions (one parametrized over the 4 TTL classes) covering `AtomicToolMetadata`: all four TTL classes round-trip on appropriately-cacheable tools, the `TTL_CLASSES` tuple matches the `Literal`, the cross-field validator rejects both inconsistent combos, `cacheable=True` requires non-empty `source_class` (None and empty string both rejected), uncacheable tools may omit `source_class`, `cacheable` defaults to `True`, JSON round-trip is idempotent, `extra="forbid"` rejects a sneak-in `cost_usd` (Invariant 9 negative control), and unknown TTL classes are rejected by the closed `Literal`.
- `docs/srs/D-mongodb-collection-schemas.md`
  - Added `progress_percent` / `error_code` / `error_message` to the `PipelineStepSummary` class block (with inline comments naming the constraint + A.6 alignment + 512-char cap).
  - Appended a "PipelineStepSummary progress + error fields (additive, all optional)" paragraph + bullet list to D.6 after the TTL configuration paragraph, calling out Invariant 1/9 alignment, the open-set A.6 semantics, and that this closes OQ-W-26-PIPELINE-STEP-FIELDS.
  - Appended an "AtomicToolMetadata (collateral, not a collection document)" paragraph noting the model lives in `grace2_contracts.tool_registry`, is the FR-DC-2 carrier consumed by the agent service tool registry (not persisted to MongoDB), and points to section 3.9 for the cache architecture.
- `docs/SRS_v0.3.md` — regenerated by `make srs` (3045 -> 3058 lines, +13 — matches the D.6 section delta byte-for-byte; lossless concat invariant preserved).

## Decisions Made

- **Decision: place `AtomicToolMetadata` in a new `grace2_contracts.tool_registry` module rather than extending `tool_metadata.py` (or putting it in a non-existent `agent.py`).**
  - **Rationale:** `tool_metadata.py` is intentionally convention-only — required docstring sections, the open `tool_category` vocabulary, and a single `is_known_tool_category` helper. It carries no pydantic models. `AtomicToolMetadata` is a pydantic v2 model with a non-trivial cross-field `model_validator`, a different shape of contract surface. Mixing a model + validator into a convention-only module would obscure both. There is no `agent.py` module under `grace2_contracts` (the kickoff floated it tentatively); the agent service consumes contracts but `schema` doesn't author its module layout. A dedicated `tool_registry.py` keeps the seam clean and leaves room for the registry to accrete other tool-registration models (tool-result envelopes, retry-policy descriptors) without churn.
  - **Alternatives considered:** (a) extend `tool_metadata.py` — rejected, mixes shapes; (b) extend `collections.py` — rejected, that module is for collection-document models and this is not persisted to MongoDB; (c) put it on a new `agent.py` — rejected, no such module exists in `grace2_contracts` and the kickoff says "your call".

- **Decision: keep `error_code` validation as a regex shape check (open set per A.6), not a closed `Literal` registry.**
  - **Rationale:** Appendix A.6 explicitly says "Codes use `SCREAMING_SNAKE_CASE`. The list will grow as tools and failure modes are added." A closed `Literal` would force a schema bump every time a workflow registers a new code — the exact friction Decision G's open-enum discipline exists to avoid. The shape regex catches the most common malformations (camelCase, kebab-case, leading underscore, double underscore, trailing underscore, leading digit, embedded space, empty) while keeping the registration door open.
  - **Alternatives considered:** A closed `Literal` of the codes seen in the SRS so far (`DEM_SOURCE_UNAVAILABLE`, `RATE_LIMITED`, `SFINCS_TIMEOUT`, ...) — surfaced in Open Questions as a TENTATIVE option the user can land later as an SRS amendment.

- **Decision: keep `progress_percent` as `int`, not `Decimal` or `float`.**
  - **Rationale:** Workflows attribute progress as `chunk N of M` or `n-of-M rows processed` — both inherently integer-discretized. Sub-percent precision is not what M4's PipelineStrip needs (it renders a bar, not a sparkline). `Decimal` would add JSON-serialization complexity for zero rendering benefit and would invite "almost-finished" mid-narration ambiguity. `Field(ge=0, le=100)` gives clean inclusive endpoints.
  - **Alternatives considered:** `Decimal | None` (sub-percent precision), `float | None` (matches FR-CE-2 `duration_seconds` precedent), `Annotated[int, Field(ge=0, le=100)]` separate from the model field declaration (cosmetic only). All surfaced as TENTATIVE Open Questions.

- **Decision: also export `pipeline_step_summary.json` as a standalone top-level schema.**
  - **Rationale:** The web client mirror (job-0026) and the agent service emitter (job-0035) both read field-level deltas, not nested-definition diffs. A standalone schema file makes the new-field surface inspectable via `git diff packages/contracts/schemas/pipeline_step_summary.json` without spelunking into `session_document.json`'s `$defs`. Idempotence still holds.

## Invariants Touched

- **Invariant 1 (Determinism boundary): preserves.** `progress_percent` is an integer the workflow computes (solver-chunk count, row count) — never an LLM-estimated "looks about 60% done." The docstring says so explicitly. `ttl_class` on `AtomicToolMetadata` is workflow-declared, never LLM-judged; the cross-field validator runs at registration time, so a misconfigured tool fails at import (FR-CE-8) before it can be invoked.
- **Invariant 9 (Confirmation before consequence — no cost theater): preserves.** Neither model contains a cost / dollar / latency-estimate field. The `test_atomic_tool_metadata_forbids_extra_fields` negative control sneaks `cost_usd: 0.01` into the validator payload and asserts `ValidationError`; `test_run_doc_has_no_cost_field` (pre-existing) continues to pass on the run document side.

## Open Questions

- **OQ-30-TTL-LITERAL-NAMING (TENTATIVE: keep kickoff-frozen "live-no-cache").** SRS FR-DC-2 prose at `docs/srs/03-functional-requirements.md:605` describes the live class as "encoded as `ttl_class: 'none'` with `expires_at = fetched_at`", while the kickoff specifies the pydantic `Literal["static-30d", "semi-static-7d", "dynamic-1h", "live-no-cache"]`. I followed the kickoff verbatim (frozen). The result is a naming mismatch between the SRS prose ("none") and the pydantic literal ("live-no-cache"). **Proposed SRS amendment:** update FR-DC-2 prose at line 605 to read `Encoded as ttl_class: "live-no-cache"` (or alternatively rename the pydantic literal to `"none"`). Route to user for landing; the kickoff is frozen so the schema-side won't change without a follow-up job. Recommendation: rename the SRS prose to match the literal — `"live-no-cache"` reads better at registration sites (`@cacheable(ttl="none")` is ambiguous between "no TTL set" and "TTL of zero").
- **OQ-30-ERROR-CODE-CLOSED-LITERAL (TENTATIVE: keep open shape-only).** Should `error_code` migrate from a regex-validated open set to a closed `Literal[...]` registry once the M4 starter set stabilizes? Open keeps the door wide for workflow churn (Decision G open-enum discipline); closed gives the web client + agent service a known set to switch on. Recommendation: keep open through M4-M5; reconsider at M6 when the code set has had two milestones to stabilize.
- **OQ-30-PROGRESS-INT-VS-DECIMAL (TENTATIVE: keep `int`).** Sub-percent precision (`Decimal | None` or `float | None`) is cheap to add later if a long-running solver wants finer granularity, and expensive to remove if the wire format ships it. Recommendation: keep `int` for v0.1.
- **OQ-30-PROGRESS-TOTAL-COMPANION (TENTATIVE: defer).** Some workflows naturally know "chunk N of M" without computing percent; a `progress_total: int | None` / `progress_current: int | None` pair would express that directly and let the client compute the percent. Recommendation: defer until M4's actual solver dispatch tells us whether the workflows have `N` cleanly. If yes, add as a separate amendment; if `progress_percent` covers every use, leave alone.
- **OQ-30-PIPELINE-STEP-FIELDS-REQUIRED-WHEN (TENTATIVE: keep all optional).** Should the three new fields tighten to required when `state in {"running","failed"}` via a `@model_validator`? Stricter validation catches workflow bugs at the schema boundary but blocks legitimate "we got a `failed` state with no error_code because the workflow timed out at the bus" emission. Recommendation: keep optional for v0.1; revisit after sprint-06 M4 sees real emissions.
- **OQ-30-TOOL-REGISTRY-MODULE-PLACEMENT (TENTATIVE: keep new `tool_registry.py`).** Decision made above; surfacing here per kickoff instruction. The alternative (extend `tool_metadata.py`) would have mixed convention-only + model contracts, which `schema.md`'s Domain Discipline section discourages. The agent service may discover at integration time that it wants the convention + model in one import statement — if so, route a follow-up consolidation job. Recommendation: leave as-is.
- **OQ-30-DEFAULT-TTL-FOR-DUAL-CLASS-TOOLS (TENTATIVE: per-call override).** FR-DC-2's worked example — `fetch_hurricane_track("IAN")` defaults to `static-30d` for closed storms, `dynamic-1h` for active — is a per-call override pattern that lives in the cache shim, not in the registration metadata. The registration declares the *default* TTL class; the shim inspects response metadata at fetch time and writes with an effective shorter TTL. The current `AtomicToolMetadata` shape doesn't carry the override-policy declaration. **Proposed extension:** add `dynamic_ttl_keys: list[str] | None = None` to declare which response-metadata fields the shim should inspect for downgrade. Surfaced here for the agent + engine teams (jobs 0032 / 0033) to push back on through the Consumer Pushback motion if needed.

## Dependencies and Impacts

- **Depends on:** job-0013-schema-20260605 (`grace2-contracts` v0.1.0 — 10-module surface + 91/91 tests + JSON Schema export pipeline). All depended-on artifacts intact.
- **Resolves:** **OQ-W-26-PIPELINE-STEP-FIELDS** (surfaced by job-0026-web-20260606 PipelineStrip work, sprint-05). The web client's `web/src/contracts.ts` mirror already carries `progress_percent` / `error_code` / `error_message` as optional from job-0026; this job lands them in the canonical schema. The mirror does **not** need a change — the fields are still optional in both. A future tightening to required (see OQ-30-PIPELINE-STEP-FIELDS-REQUIRED-WHEN) is a follow-up.
- **Affects (downstream consumers in sprint-06):**
  - **job-0032 (agent tool registry + cache shim):** consumes `AtomicToolMetadata` to validate atomic-tool registrations at import time per FR-CE-8. Reads from `grace2_contracts.tool_registry`.
  - **job-0033 (engine data-fetch tools):** each `fetch_dem` / `fetch_buildings` / `fetch_population` / `geocode_location` declares an `AtomicToolMetadata(ttl_class=..., source_class=...)` at registration. Should pick from `static-30d` / `dynamic-1h` per FR-DC-2 worked examples.
  - **job-0035 (agent pipeline-state emission):** populates `progress_percent` from solver progress callbacks, populates `error_code` / `error_message` only on `failed` state per the open-set A.6 convention.
  - **job-0031 (infra cache bucket):** the bucket layout `gs://<bucket>/cache/<source-class>/<hash>.<ext>` keys on the `source_class` field this job adds; the 4 lifecycle rules (30d / 7d / 1d / 0d) correspond to the 4 TTL classes.

## Verification

- **Tests run:** `cd packages/contracts && /home/nate/Documents/GRACE-2/.venv-agent/bin/python -m pytest -q` -> output: `131 passed in 0.59s` (baseline was 91 passed in 0.27s; net +40 across +6 new D.6 functions and +9 new tool_registry functions, with parametrized cross-products).
- **JSON Schema export idempotence:**
  - First post-edit run: `python -m grace2_contracts.export_schemas` -> wrote 36 schema files (was 35; +1 for `atomic_tool_metadata.json`, +1 for `pipeline_step_summary.json`; -0 because `session_document.json` is a modify-in-place). Working-tree diff against HEAD after first run: `M packages/contracts/schemas/session_document.json` + 2 untracked new files.
  - Second consecutive run (no source changes): `cp -r packages/contracts/schemas /tmp/schemas-run1 && python -m grace2_contracts.export_schemas > /dev/null && diff -qr /tmp/schemas-run1 packages/contracts/schemas/` -> no output (byte-identical). **IDEMPOTENT confirmed.**
  - Third run, then `git diff --stat packages/contracts/schemas/` -> `1 file changed, 40 insertions(+), 1 deletion(-)` — exactly the same diff as the first run, no creep.
  - SHA-256 of the three relevant schema files after final run:
    - `atomic_tool_metadata.json` -> `e2fa5d876a9c88237d179ce5f16107675c42f240bfa22c57e94a1e51f12935c1`
    - `pipeline_step_summary.json` -> `9931e916cb95ef9843d950b074e986be8e9d3b4f85728112dd4c0c3ee11a4d4f`
    - `session_document.json` -> `6bda134ac8abbb0fdaeb07b62ac5ecb2c0bcb0f2494db54e25cd0f42f2e71673`
- **SRS monolith regeneration:** `make srs` -> output `==> docs/SRS_v0.3.md regenerated (3058 lines)` (was 3045; +13 lines, matching the section file delta byte-for-byte — lossless concat invariant preserved). `git diff --stat docs/SRS_v0.3.md` -> `1 file changed, 13 insertions(+)`.
- **FROZEN-paths check:** `git status` shows my changes scoped to `packages/contracts/{src,tests,schemas}/`, `docs/srs/D-mongodb-collection-schemas.md`, `docs/SRS_v0.3.md`, and `reports/inflight/job-0030-schema-20260606/`. Other dirty paths (`infra/cache_bucket.tf`, `infra/outputs.tf`, `.gitignore`, `reports/inflight/job-0031-infra-20260606/`) belong to the concurrent job-0031 infra runner — I did NOT touch them. No edits to `services/agent/**`, `services/workers/**`, `web/src/**`, `styles/**`, `reports/complete/**`, `docs/srs/` files other than D, or any pydantic model not named `PipelineStepSummary` / `AtomicToolMetadata` (which is new).
- **Results:** **pass.** 131/131 contracts tests green in 0.59s (<=1s acceptance criterion met); export idempotent on consecutive runs; SRS monolith regenerates cleanly; D.6 prose + class block carry the three new fields and the `AtomicToolMetadata` cross-reference paragraph. OQ-W-26-PIPELINE-STEP-FIELDS closed.

## Live E2E evidence

Verbatim transcripts:

```
$ cd packages/contracts && .venv-agent/bin/python -m pytest -q
........................................................................ [ 54%]
...........................................................              [100%]
131 passed in 0.59s
```

```
$ .venv-agent/bin/python -m grace2_contracts.export_schemas | tail -3
  ws_tool_call_progress.json
  ws_tool_call_start.json
  ws_user_message.json
$ cp -r packages/contracts/schemas /tmp/schemas-run1
$ .venv-agent/bin/python -m grace2_contracts.export_schemas > /dev/null
$ diff -qr /tmp/schemas-run1 packages/contracts/schemas/
(no output — byte-identical)
```

```
$ make srs
==> regenerating docs/SRS_v0.3.md from docs/srs/* (DO NOT EDIT MONOLITH; edit parts under docs/srs/)
cat docs/srs/00-preamble.md ... docs/srs/E-qgis-plugins-inventory.md > docs/SRS_v0.3.md
==> docs/SRS_v0.3.md regenerated (3058 lines)
$ git diff --stat docs/SRS_v0.3.md
 docs/SRS_v0.3.md | 13 +++++++++++++
 1 file changed, 13 insertions(+)
```
