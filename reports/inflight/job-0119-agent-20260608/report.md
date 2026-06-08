# Report: `model_news_event_ingest` workflow — Case 2 composer

**Job ID:** job-0119-agent-20260608
**Sprint:** sprint-12-mega Wave 2
**Specialist:** agent
**Task:** Compose news/alert source ingest + cross-source claim aggregation + geocode → typed `EventIngestResult` for user review BEFORE any downstream solver (Case 2 partial composer; sprint-13 picks up with MODFLOW).
**Status:** ready-for-audit

## Summary

Landed `model_news_event_ingest` — a deterministic, review-gated composer that dispatches per-source fetches (`web_fetch` for URLs, `fetch_nws_event` for alert area codes, `fetch_storm_events_db` for year-state identifiers), runs cross-source claim aggregation across `location` / `scale` / `contaminant` / `date` / `casualties` targets, geocodes the derived location to a bbox, and returns a typed `EventIngestResult` whose `presentation_text` is composed deterministically (no LLM in the chain). The workflow STOPS BEFORE any solver dispatch — the returned envelope IS the Invariant 9 confirmation substrate the user reviews before sprint-13 MODFLOW (or another downstream modeler) consumes it.

## Changes Made

- `packages/contracts/src/grace2_contracts/case_results.py` — appended `EventIngestResult` + helper models `DerivedEventParam` and `EventIngestProvenance`. Idempotent w/ job-0118 (`CaseOneResult` untouched).
- `packages/contracts/src/grace2_contracts/__init__.py` — appended the three new symbols to imports + `__all__`. Idempotent w/ job-0118.
- `services/agent/src/grace2_agent/workflows/model_news_event_ingest.py` (NEW, ~530 lines) — workflow body + LLM-exposed `run_model_news_event_ingest` atomic-tool wrapper (`workflow_dispatch`, `cacheable=False`, `ttl_class="live-no-cache"`). All atomic tool calls go through `TOOL_REGISTRY[name].fn` per the kickoff hard rule.
- `services/agent/src/grace2_agent/workflows/__init__.py` — appended one-line import (idempotent w/ job-0118).
- `services/agent/tests/workflows/test_model_news_event_ingest.py` (NEW) — 12 unit + 1 env-gated live (`GRACE2_TEST_LIVE_CASE2=1`).

## Decisions Made

- **Source identifier shape**: NWS identifier interpreted as `area` arg (2-letter state OR 5-digit FIPS); storm-event identifier as `"YYYY"` or `"YYYY:STATE"`. Surfaced as OQ-0119-NWS-IDENTIFIER-SHAPE.
- **Text extraction for NWS / storm-event sources**: v0.1 uses LayerURI `name` as aggregator-input text (richer FGB-feature extraction deferred — OQ-0119-NWS-DESCRIPTION-EXTRACTION).
- **Per-event-type claim_targets**: `spill` → 5 targets incl. contaminant; `flood`/`wildfire`/`hurricane` → drop contaminant.
- **Source-authority tiering**: Tier 1 = NWS / storm-event / `.gov`; Tier 2 = `.com` news.
- **Bbox is best-effort**: geocode failure → `bbox=None` (not a raise).
- **Presentation_text is deterministic**: format-string output keyed on structured fields. Two identical inputs → byte-identical text.

## Invariants Touched

- Invariant 1 (Determinism boundary): preserves — no LLM in chain.
- Invariant 2 (Deterministic workflows): preserves.
- Invariant 7 (Claims carry provenance): extends — per-source `EventIngestProvenance` with citation snippets.
- Invariant 8 (Cancellation is first-class): preserves — every await is a propagation site.
- Invariant 9 (Confirmation before consequence): EXTENDS — the workflow STOPS before solver dispatch; envelope IS confirmation substrate. Test 10 structurally asserts no `run_solver`/`run_model_flood_scenario`/etc. dispatch.

## Open Questions

- **OQ-0119-NWS-IDENTIFIER-SHAPE** — source identifier is bare string; promotion to typed union (incl. bbox tuple) deferred. TENTATIVE for v0.1: string-only.
- **OQ-0119-NWS-DESCRIPTION-EXTRACTION** — v0.1 uses LayerURI `name` as aggregator input text; richer per-feature FGB extraction is a sprint-13 follow-up.
- **OQ-0119-NEWS-SOURCE-TIERING** — Tier-2 = any `.com`; doesn't distinguish AP/Reuters/blogs. Follow-up if narration starts using tier weights.
- **OQ-0119-CLAIM-TARGETS-PER-EVENT-TYPE** — `flood`/`wildfire`/`hurricane` drop `contaminant`. If a flood involves a contaminant release, user must invoke as `target_event_type="spill"`.
- **OQ-0119-WORKFLOW-RESULT-WIRE-ENVELOPE** — Kickoff names `case2-event-ingest-result` WS envelope; the typed `EventIngestResult` is landed here; WS envelope type + web review modal are sibling Wave 2 jobs.

## Dependencies and Impacts

- **Depends on (Wave 1)**: `web_fetch`, `fetch_nws_event`, `fetch_storm_events_db`, `aggregate_claims_across_sources`, `geocode_location`. All in HEAD.
- **Depends on (Wave 2 siblings)**: `case_results.py` shared with job-0118 — idempotent-append discipline applied; pre-commit rebase required.
- **Affects**: schema (WS envelope), web (review modal), engine sprint-13 (MODFLOW consumes approved envelope).

## Verification

### Tests run

`.venv-agent/bin/python -m pytest services/agent/tests/workflows/test_model_news_event_ingest.py -v`
→ **12 passed, 1 skipped** (live test, `GRACE2_TEST_LIVE_CASE2` not set). 0.05s.

`.venv-agent/bin/python -m pytest services/agent/tests/workflows/ services/agent/tests/test_tools_registry.py services/agent/tests/test_aggregate_claims_across_sources.py services/agent/tests/test_pipeline_emitter.py -q`
→ **60 passed, 2 skipped** in 1.01s.

`.venv-agent/bin/python -m pytest packages/contracts/tests/test_case_results.py -v`
→ **4 passed** (job-0118's `CaseOneResult` tests still green after my append).

### Live invocation evidence

Direct in-process workflow invocation with mocked atomic-tool registry stand-ins; the REAL `aggregate_claims_across_sources` runs against `web_fetch` stub output:

```
event_type: spill
derived_params keys: ['location', 'scale', 'contaminant', 'date', 'casualties']
  location: value='Longview, Texas' confidence=0.80 supporting=2
  scale: value={'value': 15000.0, 'unit': 'gallon'} confidence=0.80 supporting=2
  contaminant: value='vinyl chloride' confidence=0.80 supporting=2
  date: value='2026-02-15' confidence=0.80 supporting=2
  casualties: value=None confidence=0.00 supporting=0
bbox: (-94.85, 32.4, -94.6, 32.6)
n_provenance: 2

presentation_text:
Event ingest summary — spill
  - location: Longview, Texas (confidence 0.80; 2 sources)
  - date: 2026-02-15 (confidence 0.80; 2 sources)
  - scale: 15000 gallon (confidence 0.80; 2 sources)
  - contaminant: vinyl chloride (confidence 0.80; 2 sources)
  - casualties: unknown (confidence 0.00; 0 sources)
Resolved bbox: (-94.8500, 32.4000, -94.6000, 32.6000) EPSG:4326
Sources consulted: 2
STOP — review derived parameters before downstream modeling.

json.dumps round trip OK? True
```

Two URL sources, real aggregator → 4/5 spill targets resolved with cross-source confidence 0.80; geocoder resolves "Longview, Texas" → bbox `(-94.85, 32.40, -94.60, 32.60)` EPSG:4326; presentation_text composed deterministically; envelope round-trips through `model_dump(mode="json")`.

### Registry sanity

`run_model_news_event_ingest` in `TOOL_REGISTRY`: **True**. Total registered tools: **46**. `run_model_flood_scenario` and `run_model_flood_habitat_scenario` siblings remain registered.

### Results

**pass** — all unit tests green; regression-clean on impacted modules; live invocation produces a fully-typed `EventIngestResult` with correct derived params + bbox + provenance + deterministic presentation_text.
