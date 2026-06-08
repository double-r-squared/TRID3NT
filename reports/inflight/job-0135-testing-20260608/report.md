# Report: Case 2 partial acceptance — news/event ingest demo

**Job ID:** job-0135-testing-20260608
**Sprint:** sprint-12-mega Wave 3
**Specialist:** testing
**Task:** Run Case 2 partial composer end-to-end demonstrating news to derived spill parameters; validate EventIngestResult contract; capture demo flow with screenshot.
**Status:** ready-for-audit

## Summary

The model_news_event_ingest workflow (job-0119) was exercised end-to-end against
two fixture text sources about the February 2023 East Palestine, Ohio vinyl chloride
spill. All 6 acceptance criteria passed in 4 pytest tests (0.31s). A Playwright
screenshot captures 4 ingest pipeline cards (complete) plus the EventIngestResult
presentation text in the live chat surface with the STOP/review prompt.

## Changes Made

- tests/m6/test_case2_event_ingest.py (NEW): 4 async pytest tests covering A1-A6.
  Registry-swap technique for external-API boundary mocking (web_fetch + geocode_location).
  aggregate_claims_across_sources runs live on fixture text.
- web/tools/screenshot_case2_ingest_chat.mjs (NEW): Playwright screenshot script.
- reports/inflight/job-0135-testing-20260608/evidence/case2_event_ingest_result.json (NEW)
- reports/inflight/job-0135-testing-20260608/evidence/case2_ingest_chat.png (NEW)
- reports/inflight/job-0135-testing-20260608/evidence/case2_acceptance.md (NEW)

## Decisions Made

- Decision: Registry-swap rather than unittest.mock.patch for web_fetch/geocode_location.
  Rationale: read_through() calls storage.Client() immediately on entry; module-level
  patches hit after GCS initialization raises OSError with no project set.
  Swapping the TOOL_REGISTRY entry intercepts at the exact seam the workflow uses.

- Decision: East Palestine, OH as the demo event (vs Longview, TX from kickoff example).
  Rationale: kickoff says "e.g. Longview-style". East Palestine has clear vinyl chloride
  contaminant, extractable "City, State" location, and a real Nominatim bbox.

- Decision: Playwright overlay for the Case 2 review modal since Wave 3 web component
  (job-0107) is not yet complete. The overlay renders the EventIngestResult content
  in the existing chat surface via the live dev-injection seam.

## Invariants Touched

- Invariant 1 (Determinism boundary): preserves - format strings from derived_params, exact
  contaminant value asserted verbatim in presentation_text.
- Invariant 2 (Deterministic workflows): preserves - 0 LLM calls (no Gemini adapter present).
- Invariant 7 (Claims carry provenance): preserves - all provenance entries have identifier,
  source_type, citation_snippet, source_authority_tier.
- Invariant 9 (Confirmation before consequence): preserves - STOP sentinel asserted;
  no solver_run_id or execution_handle in result.

## Open Questions

OQ-0135-1 (non-blocking): Location regex produces multiple candidates (East Palestine Ohio,
Spill Near East Palestine Ohio, Columbiana County Ohio). Aggregator picks highest-support
correctly. Sprint-13 LLM upgrade (OQ-93) will normalize.

OQ-0135-2 (non-blocking): "lead" extracted as contaminant alternative from "leading to
the emergency release". Known v0.1 false positive in keyword-bag approach.

OQ-0135-3 (non-blocking): casualties confidence=0.50 because "at least 3 people" in
source 2 doesn't match the exact pattern used in source 1. Sprint-13 LLM normalization.

OQ-0135-4 (non-blocking, surfacing to orchestrator): geocode_location is NOT in
__init__.py eager imports (lives in data_fetch.py). If model_news_event_ingest runs
without data_fetch previously imported, geocode_location is absent from TOOL_REGISTRY
and the workflow raises EventIngestError. Tests work around this with an explicit import.
Recommend adding data_fetch to eager imports in __init__.py. Pre-existing gap from job-0033.

## Dependencies and Impacts

- Depends on: job-0093, job-0092, job-0090, job-0091, job-0033, job-0119
- Affects: sprint-13 MODFLOW kickoff (case2_event_ingest_result.json is the hand-off input)

## Verification

Tests run:
  PYTHONPATH=services/agent/src:packages/contracts/src \
  .venv-agent/bin/python -m pytest tests/m6/test_case2_event_ingest.py -v --tb=short
  4 passed in 0.31s (all PASSED)

Playwright: cd web && node tools/screenshot_case2_ingest_chat.mjs
  pipeline-card elements: 4
  STOP sentinel visible: true, vinyl chloride visible: true, EPSG:4326 visible: true
  All DOM content checks PASS

Results: pass
