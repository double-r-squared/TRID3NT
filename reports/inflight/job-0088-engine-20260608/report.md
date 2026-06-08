# Report: ``fetch_inaturalist_observations`` atomic tool

**Job ID:** job-0088-engine-20260608
**Sprint:** sprint-12-mega (Wave 1 parallel fan-out)
**Specialist:** engine
**Task:** Verbatim per `audit.md` — implement the iNaturalist Tier-1 citizen-science observation point fetcher, register it, ≥4 unit tests + ≥1 live test, geography-correctness verified per the codified job-0086 lesson.
**Status:** ready-for-audit

## Summary

Landed a new atomic tool `fetch_inaturalist_observations(taxon_id, bbox, quality_grade='research', days_back=None, max_records=5000) -> LayerURI` that wraps iNaturalist API v1 (https://api.inaturalist.org/v1/observations + /v1/taxa for name resolution), paginates observations, projects to a FlatGeobuf with the audit.md property schema (`id`, `observed_on`, `user_login`, `photo_url`, `species_guess`, `place_guess`), routes through `read_through` (FR-DC-3) for the 30-day cache, and returns a `LayerURI(layer_type="vector", role="context", units=None)`. 24 unit tests pass; the env-guarded live test against the real iNat API passes (American alligator over Everglades bbox → 100 features, all inside bbox). Tool is registered and visible at `--startup-only`: registry shows 30 tools including `fetch_inaturalist_observations`.

## Changes Made

- File: `services/agent/src/grace2_agent/tools/fetch_inaturalist_observations.py` (NEW)
  - Module docstring + typed-error hierarchy (`INatError` → `INatInputError` `retryable=False`, `INatUpstreamError` `retryable=True`) following the FR-AS-11 + job-0084 pattern.
  - `_validate_bbox` + `_round_bbox_to_6dp` (cache-key stability).
  - `_resolve_taxon_id(name, client=None)` → calls `/v1/taxa?q=...&per_page=1`, returns top-hit `id`.
  - `_coerce_taxon_id(taxon_id_or_name)` → int / digit-string / name dispatch.
  - `_build_observations_params` → assembles `?taxon_id=&swlat=&swlng=&nelat=&nelng=&quality_grade=&per_page=200&page=&geo=true` (and `&d1=YYYY-MM-DD` when `days_back` is set).
  - `_fetch_observation_records(...)` → pagination loop: walks pages until `(page*200) >= total_results`, or empty page, or `len(records) >= max_records`. Defensive `max_pages` cap.
  - `_extract_observation_record` → safely projects the iNat dict to the audit.md record shape; drops observations without a usable WGS84 point.
  - `_records_to_flatgeobuf_bytes` → geopandas → pyogrio FlatGeobuf at EPSG:4326. Handles the empty-records case explicitly.
  - `_fetch_inat_bytes` → the miss-path callable passed to `read_through`.
  - `@register_tool(_METADATA)` decorated public function with FR-TA-3-complete docstring (Use this when / Do NOT use this for / params / returns / typed errors).
  - Cache key uses the **resolved** integer taxon id — so calling with `"American alligator"` and with `26159` (the resolved id) collide onto the same cache path. Hash inputs: `(taxon_id_int, bbox_6dp, quality_grade, days_back, max_records)`.

- File: `services/agent/src/grace2_agent/tools/__init__.py` (1-line append, idempotent)
  - Added `from . import fetch_inaturalist_observations  # noqa: E402,F401 — job-0088`.

- File: `services/agent/src/grace2_agent/main.py` (1-line append, idempotent)
  - Added `from .tools import fetch_inaturalist_observations  # noqa: F401` to `_import_tools_registry`.

- File: `services/agent/tests/test_fetch_inaturalist_observations.py` (NEW)
  - 24 unit tests covering registration, validation, taxon-name resolution, single-page happy path (200 records with geographic-correctness assertion that all points fall inside the requested bbox), pagination (400 records / 2 pages, asserts page numbers progress), `max_records` cap, cache miss/hit, cache-key collapse for `name` vs `int`, cache-key fan-out for distinct `quality_grade`, HTTP 503 → `INatUpstreamError`.
  - 1 live test (`test_live_alligator_everglades_returns_geographically_valid_points`) gated by `GRACE2_TEST_LIVE_INAT=1`.
  - Uses a `_FakeHttpxClient` plain class instead of `MagicMock(spec=httpx.Client)` to sidestep the "spec a Mock" pitfall when the patched `httpx.Client` constructor returns mocked clients.

- File: `reports/inflight/job-0088-engine-20260608/evidence/inat_live.txt` (NEW)
  - Live invocation transcript per audit.md: real iNat API call against `('American alligator', (-81.5, 25.5, -80.5, 26.5))` → 100 features, all 100 inside the requested bbox; FlatGeobuf 30,720 bytes; LayerURI populated; 5 distinct `species_guess` values (sub-taxon variants of American alligator), 54 distinct user logins, 100/100 with photo URLs.

## Decisions Made

- **Decision: Resolve taxon-name BEFORE computing the cache key.**
  - Rationale: per audit.md cache-key spec — `SHA256 of (taxon_id resolved, bbox, quality_grade, days_back, max_records)`. Collapsing `"American alligator"` and `26159` onto the same key avoids duplicate cache entries for the same logical request.
  - Alternatives considered: hash the name string as-is. Rejected — would let the same taxon land in two cache slots.

- **Decision: Empty records → still write a valid (empty) FlatGeobuf to the cache.**
  - Rationale: the FR-DC-3 shim contract is "presence == valid". A `None` or sentinel write would poison future reads; a missing write would force a re-fetch every 30 days for genuinely-empty bbox-taxon combos. Empty FGB with a fixed schema is the cleanest.
  - Alternatives considered: raise a typed "empty result" error. Rejected — empty is a legitimate result for a citizen-science fetcher, not an error condition.

- **Decision: Use `geo=true` in the iNat query.**
  - Rationale: observations without coordinates cannot become FlatGeobuf points. Server-side filter is cheaper than client-side drop.

- **Decision: `_FakeHttpxClient` test scaffold instead of `MagicMock(spec=httpx.Client)`.**
  - Rationale: when `httpx.Client` itself is patched to a `MagicMock`, constructing a second `MagicMock(spec=...)` with the patched class as spec hits `InvalidSpecError: Cannot spec a Mock object`. A plain class skips this entirely.

## Invariants Touched

- **1. Determinism boundary:** preserves — the tool returns a typed `LayerURI`; no narration prose carrying numbers.
- **2. Deterministic workflows:** preserves — this is an atomic tool, no LLM in the loop; the only HTTP calls are to the iNat REST API.
- **3. Engine registration, not modification:** preserves — adds a new tool via `@register_tool`; no change to the agent core or contract shapes.
- **6. Metadata-payload pattern:** preserves — bytes land in GCS keyed by `cache/static-30d/inaturalist/<hash>.fgb`; no bucket enumeration; MongoDB is not touched.
- **7. Claims carry provenance:** N/A — this is a data-fetch tool that produces a LayerURI, not a claim-aggregation tool. The FlatGeobuf carries per-record `id`/`user_login`/`photo_url` provenance for downstream attribution.

## Open Questions

- **OQ-0088-OBSCURED-COORDS** (non-blocking): iNat policy obfuscates exact coordinates for legally-sensitive taxa (e.g. threatened/endangered species). When the agent fetches such a taxon, returned points may be displaced by km from field truth. The tool docstring's "Do NOT use this for" section flags this for the LLM; no code change made — the obfuscated points ARE inside the bbox (iNat displaces within a ~10 km cell of the true location), so the geography-correctness check still passes. A future job may want to add a `flag_obscured` property by joining the per-record `obscured`/`geoprivacy` fields the iNat API exposes.
- **OQ-0088-STYLE-PRESET-CONTENT** (non-blocking): `style_preset="inaturalist_observations"` is referenced in the returned `LayerURI` but no `.qml` file by that name exists yet. The other Wave 1 sibling tools (GBIF, WDPA) likely do the same. A sprint-12 follow-up job should land a generic "context-point" QML preset that covers iNat/GBIF/WDPA observations uniformly.

## Dependencies and Impacts

- Depends on: job-0031 (cache bucket), job-0032 (tool registry + cache shim), job-0084 (pattern reference). All complete.
- Affects: web (LayerPanel will eventually show iNat layers; needs the style preset above), schema (no contract change; existing `LayerURI` shape sufficient), conservation-tooling roadmap (this is one of the 3 Tier-1 fetchers the sprint-12 Case 1 minimum requires per the memory note).

## Verification

- **Tests run:** `pytest services/agent/tests/test_fetch_inaturalist_observations.py -v` → 24 passed, 1 skipped (live, gated). Live re-run with `GRACE2_TEST_LIVE_INAT=1` → 1 passed.
- **`--startup-only` transcript:**
  ```
  2026-06-08 04:47:30,128 INFO grace2_agent.main tool registry loaded: 30 tool(s): [..., 'fetch_administrative_boundaries', 'fetch_buildings', 'fetch_dem', 'fetch_gbif_occurrences', 'fetch_inaturalist_observations', 'fetch_landcover', ...]
  2026-06-08 04:47:30,128 INFO grace2_agent.main --startup-only: tool registry verified; exiting without serving
  ```
  Registry contains 30 tools including `fetch_inaturalist_observations`. Other Wave 1 siblings (`fetch_gbif_occurrences`, `fetch_wdpa_protected_areas`, `fetch_nws_event`, `fetch_storm_events_db`, `web_fetch`) are also present — sibling imports in both `__init__.py` and `main.py` were preserved.
- **Live invocation evidence:** `reports/inflight/job-0088-engine-20260608/evidence/inat_live.txt`. Real iNat API call returned 100 American-alligator observations over the Everglades bbox `(-81.5, 25.5, -80.5, 26.5)`; geographic-correctness check passed — **100/100 points inside the requested bbox** (codified job-0086 lesson). 1.18 s duration. LayerURI populated with all expected fields including `bbox=(-81.5, 25.5, -80.5, 26.5)`.
- **Results:** pass.
