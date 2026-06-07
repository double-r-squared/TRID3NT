# Audit: Appendix D.6 PipelineStepSummary fields + FR-DC TTL-class metadata on FunctionTool registration

**Job ID:** job-0030-schema-20260606, **Sprint:** sprint-06, **Auditor:** Development Orchestrator, **Status:** assigned

## Task Assignment

**Specialist:** schema

**Prerequisites:**
- job-0013-schema-20260605 (`grace2-contracts` v0.1.0 installed; 91 pydantic-v2 modules + JSON Schema export pipeline operational — provides the contract surface this job extends)
- v0.3.15 SRS amendment landed at commit `e435d8a` (Decision O + FR-DC-1..6 + FR-CE-8 + §3.9 caching architecture — provides the binding contract surface the FunctionTool TTL-class field must conform to)
- job-0026-web-20260606 OQ-W-26-PIPELINE-STEP-FIELDS surfaced this Appendix D.6 gap: the M3 PipelineStrip render currently treats `progress_percent` / `error_code` / `error_message` as client-side optional fields; the SRS Appendix D.6 model does not carry them. Web client mirror added `?` on these fields with explicit note that schema must define them before M4 emits real `pipeline-state` envelopes (job-0035 work).

**SRS references:**
- **Appendix D.6 `PipelineStepSummary`** (`docs/srs/D-mongodb-collection-schemas.md`) — the target model. Currently carries `step_id` / `name` / `state` / `started_at` / `completed_at`. This job extends with three optional fields.
- **§3.9 FR-DC-2** (`docs/srs/03-functional-requirements.md`) — four TTL classes (`static-30d` / `semi-static-7d` / `dynamic-1h` / `live-no-cache`) that every external-API atomic tool declares at registration time.
- **FR-CE-8** (`docs/srs/03-functional-requirements.md`) — atomic-tool routing through the cache shim; cache class is a required property validated at tool-registration time.
- **FR-AS-3** (`docs/srs/03-functional-requirements.md`) — ADK FunctionTool registration discipline; docstrings include "Use this when / Do NOT use this for" sections.
- **Appendix A `pipeline-state`** envelope payload — the wire-level consumer of the extended `PipelineStepSummary`.

### Environment
Linux Debian dev host. `grace2-contracts` editable-installed in the test venv at `.venv-agent/`; the JSON Schema export pipeline at `packages/contracts/src/grace2_contracts/_export.py` regenerates `packages/contracts/schemas/*.json` idempotently per the v0.1.0 pattern. The web client mirror at `web/src/contracts.ts` (job-0025 + job-0026 surface) consumes the renamed `PipelineStepSummary` already; once this job lands, the `error_code?` / `error_message?` / `progress_percent?` fields stop being client-side optional and become canonical Appendix D.6 fields. No deployed substrate changes; no cloud cost.

### Scope

1. **Appendix D.6 `PipelineStepSummary` extension.** Add three optional fields to the pydantic model and the SRS Appendix D.6 text (`docs/srs/D-mongodb-collection-schemas.md`):
   - `progress_percent: int | None = None` — integer 0–100, validated with pydantic `Field(ge=0, le=100)`. Represents the running step's progress when the agent can reasonably attribute one (solver chunk N of M; n-of-M dataset rows processed). Optional everywhere.
   - `error_code: str | None = None` — `SCREAMING_SNAKE_CASE` literal matching the Appendix A.6 error-code convention. Present only when the step is in `failed` state. The set of valid codes is open per A.6 (every workflow may register its own codes); validation is "string in SCREAMING_SNAKE_CASE" — pydantic field validator with a regex.
   - `error_message: str | None = None` — short human-readable explanation accompanying `error_code`. Free text but capped at 512 chars to discourage stack traces leaking through.

   Edit BOTH the pydantic model in `packages/contracts/src/grace2_contracts/mongo_documents.py` (or whichever module currently owns `PipelineStepSummary`) AND the §D.6 prose in `docs/srs/D-mongodb-collection-schemas.md`. Update the JSON Schema export by running `python -m grace2_contracts._export` (or equivalent); commit the regenerated `packages/contracts/schemas/PipelineStepSummary.json`.

2. **`AtomicToolMetadata` (NEW) — TTL-class field for FunctionTool registration.** Add a new pydantic model `AtomicToolMetadata` to `packages/contracts/src/grace2_contracts/agent.py` (or a new `tool_registry.py` module — your call; surface the choice in the report's Decisions Made). Fields:
   - `name: str` — atomic-tool function name.
   - `ttl_class: Literal["static-30d", "semi-static-7d", "dynamic-1h", "live-no-cache"]` — REQUIRED per FR-DC-2 + FR-CE-8 for every external-API tool; agent.md role file will codify the discipline. The literal values match §3.9 verbatim.
   - `source_class: str` — the `<source-class>` prefix used in the cache bucket layout per FR-DC-1 (`gs://<bucket>/cache/<source-class>/<hash>.<ext>`). E.g. `"dem"`, `"buildings"`, `"population"`, `"geocode"`. Required for any tool with a `ttl_class` other than `"live-no-cache"`.
   - `cacheable: bool = True` — explicit boolean for FR-DC-6 enumeration. `False` for interactive solicitation tools, envelope emitters, MongoDB writes, solver dispatchers per FR-DC-6. When `False`, `source_class` MAY be omitted and `ttl_class` MUST be `"live-no-cache"`.

   Add a pydantic `model_validator` enforcing: if `cacheable=True` then `ttl_class != "live-no-cache"` and `source_class` is present; if `cacheable=False` then `ttl_class == "live-no-cache"`. Tool-registration in the agent service consumes this model; misconfigured tools fail-fast at import time per FR-CE-8.

3. **Appendix C / Appendix A cross-references — consult only, do not edit.** The new `error_code` field aligns with the Appendix A.6 SCREAMING_SNAKE_CASE convention. No edits to A.6 required (the new codes register through the same open-set discipline). The new fields do NOT change the Appendix C `EventMetadata` / `ClaimSet` surface — those are independent.

4. **Re-export JSON Schemas.** Run the existing export pipeline; verify `packages/contracts/schemas/PipelineStepSummary.json` includes the three new fields and `packages/contracts/schemas/AtomicToolMetadata.json` is newly emitted. Commit both. Verify idempotence: run the export twice; `git diff` should be empty after the second run (v0.1.0 invariant).

5. **Regression suite.** All 91 contracts unit tests must pass. Add at least:
   - One test that constructs `PipelineStepSummary` with the new fields and validates serialization round-trip.
   - One test that exercises the `Field(ge=0, le=100)` constraint (rejects 101, accepts 0 and 100).
   - One test that exercises the `error_code` regex (rejects `"camelCase"`, accepts `"SFINCS_TIMEOUT"`).
   - One test that constructs `AtomicToolMetadata` with each of the 4 TTL classes.
   - One test that exercises the `model_validator` cross-field rule (rejects `cacheable=True` + `ttl_class="live-no-cache"`; rejects `cacheable=False` + `ttl_class="static-30d"`).

   Total contracts-test delta: at least +5 tests. New baseline: 96+ contracts tests.

6. **Document amendment proposal in the report.** Per AGENTS.md "Architecture / Schema Consumer Pushback", any consumer-side discovered field needs a documented amendment proposal back to the user. The web client (job-0026) DID surface OQ-W-26 already — this job IS the user-landed resolution; the report should cite OQ-W-26 as the closure rationale.

### File ownership (exclusive)

- `packages/contracts/src/grace2_contracts/mongo_documents.py` (or wherever `PipelineStepSummary` currently lives) — `PipelineStepSummary` extension only; do NOT touch unrelated models.
- `packages/contracts/src/grace2_contracts/agent.py` OR new `packages/contracts/src/grace2_contracts/tool_registry.py` — new `AtomicToolMetadata` model.
- `packages/contracts/src/grace2_contracts/_export.py` — only if the new model is not auto-discovered by the existing export machinery; prefer auto-discovery.
- `packages/contracts/schemas/*.json` — regenerated; commit the diff.
- `packages/contracts/tests/test_*.py` — at least one new test file for the schema additions OR extensions to existing test files following the established pattern.
- `docs/srs/D-mongodb-collection-schemas.md` — D.6 prose extension only; run `make srs` after.
- `reports/inflight/job-0030-schema-20260606/` — kickoff frozen, report + evidence land here.

### FROZEN — no edits in this job

- `services/agent/**`, `services/workers/**`, `web/src/**`, `infra/**`, `styles/**` (consumer surfaces — they consume the extended contracts in later jobs; not edited here)
- `docs/SRS_v0.3.md` (regenerated by `make srs`, never hand-edited — edit `docs/srs/<section>.md`)
- `docs/srs/` files OTHER than `D-mongodb-collection-schemas.md` (Appendix D.6 only this job)
- `reports/complete/**` (immutable per AGENTS.md "Completed Job Immutability")
- Any pre-existing pydantic model not named `PipelineStepSummary`

### Cross-cutting principles in force (cited by NUMBER+name from agents/orchestrator.md)

- **Invariant 1 (Determinism boundary):** preserves. `progress_percent` is an integer from the workflow, not an LLM-generated estimate. `error_code` literals are workflow-registered, not LLM-narrated.
- **Invariant 9 (Confirmation before consequence — no cost theater / no cost fields):** preserves. The three new D.6 fields contain no cost/dollar/duration-estimate semantics; field names + descriptions deliberately avoid the banned vocabulary.
- **Schema Consumer Pushback** — this job IS the schema-side resolution of the web client's OQ-W-26 push from sprint-05.
- **Diagnose before fix** — if the JSON Schema export is non-idempotent, capture the diff before changing the export machinery.
- **Bundle small fixes** — if `PipelineStepSummary` has any drift between the pydantic source and the Appendix D.6 prose discovered while editing, fix the drift in this job rather than spawning a follow-up.
- **Remove don't shim** — do NOT add backwards-compat field aliases like `error: str | None` mirroring `error_code`/`error_message`. The two new fields are the truth.

### Acceptance criteria (reviewer re-runs)

- [ ] `PipelineStepSummary` carries `progress_percent: int | None` with `Field(ge=0, le=100)`, `error_code: str | None` with SCREAMING_SNAKE_CASE validator, `error_message: str | None` with 512-char cap. All optional, all `None` by default.
- [ ] `AtomicToolMetadata` exists with `name` / `ttl_class` (4-class Literal) / `source_class` / `cacheable` / `model_validator` cross-field rule.
- [ ] JSON Schemas regenerated; idempotent (running the export twice → empty diff).
- [ ] At least 5 new pydantic unit tests; full contracts suite green (96+ tests).
- [ ] `docs/srs/D-mongodb-collection-schemas.md` Appendix D.6 prose updated to describe the three new fields; `make srs` regenerates the monolith without error; `git diff docs/SRS_v0.3.md` shows the corresponding monolith additions.
- [ ] No edits to any FROZEN path listed above.
- [ ] OQ-W-26-PIPELINE-STEP-FIELDS closed; cited in the report's "Open Questions Resolved" section.
- [ ] Web client `web/src/contracts.ts` `PipelineStepSummary` already carries the three fields as optional from job-0026 — surface in the report that the web mirror does NOT need a change (the fields are still optional in the canonical schema), but note that future tightening (making them required when state == "running" / state == "failed") is a follow-up for a later schema amendment.
- [ ] Pydantic model placement decision (`agent.py` vs new `tool_registry.py`) surfaced as a Decision Made in the report with rationale.

Surface contestable choices as Open Questions with TENTATIVE tags — at minimum: pydantic model placement for `AtomicToolMetadata`, whether `progress_percent` should be `Decimal` instead of `int` for finer-grained tracking, whether to add a `progress_total` companion field, whether `error_code` should be a closed `Literal` registry vs the current open-set discipline, the per-tool default for tools that genuinely could be either `static-30d` or `dynamic-1h` (e.g., earthquake catalog).
