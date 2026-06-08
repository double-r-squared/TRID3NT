# Case 2 Partial Acceptance — News/Event Ingest Demo

**Job:** job-0135-testing-20260608
**Sprint:** sprint-12-mega Wave 3
**Date:** 2026-06-08
**Sprint-13 hand-off note** at bottom.

---

## Summary

The `model_news_event_ingest` workflow (job-0119) was exercised end-to-end against
two fixture text sources about the February 2023 East Palestine, Ohio vinyl chloride
spill (Norfolk Southern train derailment). All four acceptance criteria passed; the
workflow STOPS before any solver dispatch as designed.

---

## Acceptance Criteria Verification

### A1 — EventIngestResult.event_type matches input

**PASS.** `result.event_type == "spill"` — confirmed by pytest assertion.

```
event_type: "spill"
```

### A2 — derived_params populated with confidence scores

**PASS.** Five targets extracted, all with structured confidence values:

| Target | Value | Confidence | Sources |
|--------|-------|-----------|---------|
| location | East Palestine, Ohio | 0.80 | 2 (cross-source agreement) |
| date | 2023-02-03 | 0.80 | 2 (cross-source agreement) |
| scale | 100,000 gallon | 0.80 | 2 (cross-source agreement) |
| contaminant | vinyl chloride | 0.80 | 2 (cross-source agreement) |
| casualties | 3 | 0.50 | 1 (single source, below 0.6 threshold) |

FR-HEP-3 scoring rule verified separately (test_claim_aggregation_unit_cross_source_agreement):
- 1 source → 0.5 ✓
- 2 sources → 0.80 ✓
- 3 sources → 0.85 ✓

### A3 — bbox derived from location

**PASS.** `geocode_location` was called with the derived location string
`"East Palestine, Ohio"` (the aggregator's output, not a raw URL).
The returned bbox `[-80.5562, 40.8151, -80.5021, 40.8562] EPSG:4326` correctly
places East Palestine, OH in the Ohio/Pennsylvania border region (~40.8°N, 80.5°W).

Geographic-correctness gate (codified job-0086): the geocoder receives the
DERIVED location value from the aggregator, not a URL or input string. This
verifies the algebraic identity: `geocode_query == derived_params["location"].value`.

### A4 — presentation_text reads naturally

**PASS.** The deterministic format-string output:

```
Event ingest summary — spill
  - location: East Palestine, Ohio (confidence 0.80; 2 sources)
  - date: 2023-02-03 (confidence 0.80; 2 sources)
  - scale: 100000 gallon (confidence 0.80; 2 sources)
  - contaminant: vinyl chloride (confidence 0.80; 2 sources)
  - casualties: 3 (confidence 0.50; 1 source)
Resolved bbox: (-80.5562, 40.8151, -80.5021, 40.8562) EPSG:4326
Sources consulted: 2
STOP — review derived parameters before downstream modeling.
```

- Mentions event_type "spill" ✓
- References source count (2) ✓
- STOP sentinel present (Invariant 9) ✓
- EPSG:4326 bbox present ✓
- Exact contaminant value matches DerivedEventParam.value (Invariant 1) ✓

### A5 — No solver dispatch leaked

**PASS.** `EventIngestResult.model_dump(mode="json")` contains no
`solver_run_id` or `execution_handle` fields. The workflow STOPS at the
result construction step. Verified for both spill and flood event types.

### A6 — presentation_text references all provided sources

**PASS.** `len(result.provenance) == 2`. Both source identifiers present:
- `https://www.example-news.com/norfolk-southern-spill-2023` ✓
- `https://apnews.example.com/east-palestine-derailment-2023` ✓

Each provenance entry carries:
- `citation_snippet` (first 280 chars of the fetched text) ✓
- `source_authority_tier = 2` (news URLs) ✓
- `fetched_at` timestamp ✓

---

## Invariant Checks

| Invariant | Status | Evidence |
|-----------|--------|---------|
| 1 — Determinism boundary | PASS | `presentation_text` contains exact `contaminant_param.value`; format strings only, no LLM call |
| 2 — Deterministic workflow | PASS | No Gemini adapter present; workflow ran with `pipeline_emitter=None`; 0 LLM calls |
| 7 — Claims carry provenance | PASS | Each provenance entry has `identifier`, `source_type`, `citation_snippet`, `source_authority_tier` |
| 9 — Confirmation before consequence | PASS | STOP sentinel in `presentation_text`; workflow returns before any solver is dispatched |

---

## Test Results

```
tests/m6/test_case2_event_ingest.py::test_case2_event_ingest_spill_end_to_end PASSED
tests/m6/test_case2_event_ingest.py::test_claim_aggregation_unit_cross_source_agreement PASSED
tests/m6/test_case2_event_ingest.py::test_workflow_no_solver_dispatch_on_flood_event PASSED
tests/m6/test_case2_event_ingest.py::test_event_ingest_contract_round_trip PASSED
4 passed in 0.31s
```

---

## UI Evidence

Screenshot `case2_ingest_chat.png` shows:
- 4 pipeline cards rendered in the existing chat surface (web_fetch x2,
  aggregate_claims_across_sources, geocode_location — all complete)
- EventIngestResult presentation text rendered in a chat-card overlay
  showing the derived parameters with confidence scores and EPSG:4326 bbox
- STOP sentinel banner with "Yes — Proceed to model groundwater plume" / "No"
  review prompt, consistent with the kickoff's confirmation-before-consequence design
- All key text verified in the live DOM: STOP sentinel, "vinyl chloride", EPSG:4326

Pixel-level DOM checks (geographic-correctness gate applied to UI layer):
- `STOP` sentinel visible in DOM: **true**
- `vinyl chloride` visible in DOM: **true**
- `EPSG:4326` bbox visible in DOM: **true**

---

## Mock Boundary Documentation

Per testing.md discipline, only external-API boundaries are mocked:

| Tool | Mock | Rationale |
|------|------|-----------|
| `web_fetch` | Registry swap → fixture text | GCS cache shim fires before the HTTP fetch; mocking at the registry level intercepts the right boundary |
| `geocode_location` | Registry swap → fixture bbox | Nominatim HTTP call is external; fixture bbox is a real Nominatim result for "East Palestine, Ohio" |
| `aggregate_claims_across_sources` | NOT mocked — runs live | The entire point: real regex extraction on fixture text |
| `fetch_nws_event` | NOT used (url-type sources only) | |
| `fetch_storm_events_db` | NOT used (url-type sources only) | |

---

## Open Questions

**OQ-0135-1 (non-blocking):** The `location` regex extractor (`_LOCATION_RE`) matched
"East Palestine, Ohio" correctly in both sources, but the `alternatives` list shows
"EAST PALESTINE, Ohio" and "Spill Near East Palestine, Ohio" as separate candidates.
This is expected v0.1 behaviour (case-sensitive + multi-word match produces fragments);
the best-supported value wins and is correct. The OQ-93-NEEDS-LLM-EXTRACTION upgrade
in sprint-13 will handle these variants via entity normalization.

**OQ-0135-2 (non-blocking):** The `lead` contaminant was extracted as an alternative
from source 2 because the word "lead" appears in "leading to the emergency release".
This is a known false-positive risk in the keyword-bag approach. The v0.1 aggregator
correctly selects `vinyl chloride` as the primary (2-source agreement); `lead` is
single-source and falls below the 0.6 threshold. Same sprint-13 upgrade path.

**OQ-0135-3 (non-blocking):** `casualties = 3` is `confidence=0.50` (below the 0.60
threshold, flagged `below_threshold=True`) because "3 people were injured" only
appears with that exact count in source 1. Source 2's "at least 3 people" triggered
a different extraction path. A sprint-13 LLM upgrade would normalize these; for v0.1
the value is still surfaced with its below-threshold flag.

---

## Sprint-13 Hand-Off Note

Sprint-12-mega STOPS HERE by design (Invariant 9). The `EventIngestResult` produced
by this workflow is the review envelope that the user must approve before any
downstream solver runs. When the user approves:

1. The `derived_params["location"].value` ("East Palestine, Ohio") feeds the MODFLOW
   domain setup (geocode → bbox → model grid extents).
2. The `derived_params["scale"].value` (`{"value": 100000.0, "unit": "gallon"}`) feeds
   the release volume parameter for the MODFLOW contaminant transport model.
3. The `derived_params["contaminant"].value` ("vinyl chloride") identifies the
   contaminant class for the transport/decay parameterization.

Sprint-13's opening job should consume this `EventIngestResult` envelope (deserialized
from `case2_event_ingest_result.json`) as the MODFLOW kickoff input. The MODFLOW
runner must NOT re-run the ingest; it reads the approved envelope.

The sprint-13 acceptance job should verify:
- MODFLOW grid is constructed around the approved bbox
- Release volume parameter matches `derived_params["scale"].value.value` (100,000 gal)
- Contaminant is parameterized as vinyl chloride (or the appropriate surrogate)
- The plume result layer is published and visible in the web client
