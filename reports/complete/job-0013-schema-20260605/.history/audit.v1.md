# Audit: Contracts v0 from SRS Appendices A–D (pydantic v2)

**Job ID:** job-0013-schema-20260605
**Sprint:** sprint-03
**Auditor:** Development Orchestrator
**Status:** assigned

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

## Invariant Check

## Dependency Check

## Decisions Validated

## Open Questions Resolved

## Follow-up Actions

## Sign-off
