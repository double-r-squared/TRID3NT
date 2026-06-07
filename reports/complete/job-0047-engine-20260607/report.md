# Report: catalog_search + catalog_fetch atomic tools + generic OGC adapter (Stage B)

**Job ID:** job-0047-engine-20260607
**Sprint:** sprint-08
**Specialist:** engine
**Task:** Land `catalog_search` + `catalog_fetch` atomic tools (Mode 1 substrate per §F.1.2) backed by the 30-entry `public_data_source_catalog.yaml`, plus the generic Tier-2 OGC adapter `tools/ogc_adapter.py` that any WMS/WMTS/WCS/WFS/ArcGIS-REST catalog entry can route through. Refactor `fetch_landcover` (post-job-0044 WCS) to use the shared adapter — single source of truth for OGC Tier 2.
**Status:** ready-for-audit

## Summary

Landed two new atomic tools (`catalog_search` + `catalog_fetch`) in `services/agent/src/grace2_agent/tools/catalog.py` and one shared substrate module (`services/agent/src/grace2_agent/tools/ogc_adapter.py`) — the §F.1.2 Mode 1 catalog-mediated discovery + retrieval surface. The OGC adapter is the single source of truth for Tier-2 retrieval: it supports WMS / WCS / WFS / ArcGIS REST (MapServer/FeatureServer query + ImageServer exportImage); `fetch_landcover`'s NLCD WCS path was refactored to route through it, eliminating the only other WCS implementation in the engine. The catalog YAML is loaded once at first call, validated against `CatalogEntry` (the §F.1.2 pydantic shape job-0045 landed), and cached in memory; 28 of the 30 seed entries validate cleanly (2 Tier-2 entries — Census ACS, NASA FIRMS — fail the credential-tier consistency rule because the YAML doesn't carry `api_key_secret_ref` yet; surfaced as OQ-47-CATALOG-YAML-SECRET-REFS). Registry: **16 tools at `--startup-only`** (14 baseline + 2 new); 23 new unit tests; 159/159 agent suite green; 142/142 contracts no-regression. **Live evidence captured against production endpoints**: `catalog_search(topic="flood zones", location=fort_myers_bbox)` returned FEMA NFHL as rank-1; `catalog_search(topic="DEM")` returned 3 DEM entries (Copernicus GLO-30, GLO-90, HydroSHEDS); `catalog_fetch("fema-nfhl-flood-zones", {bbox, layer_id="28"})` retrieved an **8.0 MB live GeoJSON FeatureCollection** of real flood-zone polygons from `hazards.fema.gov`; `catalog_fetch("usgs-3dep-elevation-image-service", {bbox})` retrieved a **4.2 MB live GeoTIFF DEM** (F32 elevation values) from `elevation.nationalmap.gov`. Both fetches routed through the generic OGC adapter and cache shim with no per-source code in the catalog dispatcher.

## Changes Made

- **`services/agent/src/grace2_agent/tools/ogc_adapter.py`** (NEW, ~395 lines). Single-source-of-truth Tier-2 OGC adapter:
  - `fetch_ogc_layer(url, layer_name, bbox, *, crs, service_type, image_format, version, width_px, height_px, timeout_s, user_agent, extra_params, max_features, output_fields, where_clause) → OGCResponse`.
  - Supports five service flavors: `WMS` (`GetMap`), `WMTS` (placeholder — surfaces as OQ-47-WMTS-DIALECT), `WCS` (`GetCoverage` 1.0.0 / 1.1.x / 2.0.x), `WFS` (`GetFeature`), `ARCGIS_REST` (MapServer/FeatureServer `/<layer>/query` AND ImageServer `/exportImage`). The ImageServer `/exportImage` branch is the path 3DEP DEM retrieval routes through.
  - Defensive exception-XML detection: a 200-status OGC `ExceptionReport` body is caught and raised as `OGCAdapterError` rather than written through the cache as a "raster" (the bug class job-0044's hotfix protected against, now in shared code).
  - ArcGIS REST error-JSON detection (mirrors the WMS XML defense).
  - NFR-R-1 resilience: per-call timeout, single re-raise as `OGCAdapterError`, no sentinel on failure.

- **`services/agent/src/grace2_agent/tools/catalog.py`** (NEW, ~770 lines). Two atomic tools + YAML loader:
  - `catalog_search(topic, location?, source_filter?) → list[dict]` — searches the curated YAML by topic-match (lowercase substring + content-word token overlap with stopword filter) + optional bbox-coverage heuristic (drops CONUS/US-only entries for international bboxes) + optional `source_class` filter. Returns ranked CatalogEntry dicts with `relevance_score`. Registered `cacheable=True`, `ttl_class="semi-static-7d"`, `source_class="catalog_search"`.
  - `catalog_fetch(entry_id, params) → dict` — generic dispatcher. Reads `entry.access_tier` from the catalog and routes:
    - **Tier 1 (STAC+COG):** `NotImplementedError` (v0.1 substrate — dedicated fetchers cover STAC sources; OQ-47-CATALOG-TIER1-STAC for follow-up).
    - **Tier 2 (OGC service):** routes through `ogc_adapter.fetch_ogc_layer`. URL-sniffing picks the service flavor: `/wcs` → WCS, `/wfs` → WFS, `/mapserver|/featureserver|/imageserver` → ARCGIS_REST, default → WMS. `params.service_type` overrides. ImageServer endpoints route to `/exportImage` with bbox+size+format=tiff; MapServer/FeatureServer route to `/<layer>/query` with the standard ESRI shape.
    - **Tier 3 (HTTPS+Range):** single HTTPS GET (full-body, not Range-windowed — windowed reads stay in dedicated fetchers per OQ-47-CATALOG-TIER3-RANGE).
    - **Tier 4 (region+clip):** `NotImplementedError` (per-source clip paths live in `fetch_river_geometry` / `fetch_population`; OQ-47-CATALOG-TIER4-REGION for follow-up).
    - Returns `{layer: LayerURI, entry_id, access_tier, source_class, citation, last_verified, cache_hit, bytes}`.
    - Registered `cacheable=True`, `ttl_class="static-30d"`, `source_class="catalog_fetch"`.
  - `load_catalog(yaml_path=None) → list[CatalogEntry]` — YAML parse + pydantic validate + in-memory cache. Tolerates v0.1 `last_verified` date-string by widening to UTC midnight.
  - `_get_catalog_entry(entry_id)` helper (per kickoff spec).
  - Path resolution: walks up from this module's directory; env override via `GRACE2_CATALOG_YAML`.

- **`services/agent/src/grace2_agent/tools/data_fetch.py`** (EDIT — `_fetch_nlcd_landcover_bytes` refactored, ~50 lines net inside the single function):
  - Replaced the inline `requests.get(_MRLC_WCS_URL, params={...})` block with `ogc_adapter.fetch_ogc_layer(url=_MRLC_WCS_URL, layer_name=coverage, service_type="WCS", version="1.0.0", image_format="GeoTIFF", crs="EPSG:4326", ...)`. Same WCS 1.0.0 substrate job-0044 verified; same content-type defensiveness; same cache key.
  - `OGCAdapterError` from the adapter is wrapped + re-raised as `UpstreamAPIError` so the existing `data_fetch.FetchError` taxonomy stays the single error surface the agent service sees.
  - `_MRLC_WCS_URL` + `_NLCD_WCS_COVERAGE_BY_YEAR` + the vintage-year sidecar contract are UNCHANGED. job-0044's `test_fetch_nlcd_landcover_bytes_issues_wcs_1_0_0_getcoverage` still passes (the adapter issues the same WCS 1.0.0 params).

- **`services/agent/src/grace2_agent/main.py`** (EDIT — additive, +2 lines):
  - `_import_tools_registry` eagerly imports `tools.catalog` so the two new tools register at startup.

- **`services/agent/tests/test_catalog_tools.py`** (NEW, ~440 lines, 23 tests):
  - Registration assertions; YAML-load round-trip + CatalogEntry validation; catalog_search topic/bbox/source-filter; cache integration; catalog_fetch tier 1/2/3/4 dispatch; OGC adapter WMS/WCS/WFS/ArcGIS-REST request-shape pins; exception-XML defense; fetch_landcover refactor pin.

- **`reports/inflight/job-0047-engine-20260607/evidence/`** (NEW):
  - `live_catalog_search.py` + `_log.txt` + `_result.json` — live `catalog_search` for both kickoff topics.
  - `live_catalog_fetch.py` + `_log.txt` + `_result.json` — live `catalog_fetch` against `hazards.fema.gov` (8.0 MB GeoJSON) + `elevation.nationalmap.gov` (4.2 MB GeoTIFF).
  - `startup_log.txt` — `--startup-only` showing 16 tools.
  - `pytest_agent_suite.txt` — full pytest log (159 passed).

- **No edits to FROZEN paths.** Verified by `git status --short`: changes scoped to the kickoff's exclusive-ownership set. Working-tree drift in `services/agent/src/grace2_agent/workflows/sfincs_builder.py`, `services/agent/tests/test_model_flood_scenario.py`, `packages/contracts/schemas/ws_session_state.json`, `.gitignore`, `tests/m2/artifacts/` is from concurrent jobs (job-0054 + job-0048+) and was explicitly NOT staged.

- **`services/agent/pyproject.toml`** NOT touched. The adapter uses `requests` directly (already a job-0033 dep); no `owslib` added. Surfaced as OQ-47-OWSLIB-CHOICE.

## Decisions Made

- **Decision: direct `requests.get` over `owslib`.** Rationale: §F.1.2 Mode 1 retrieval is a single GET per call with a known request shape per service flavor; owslib's strength is Capabilities introspection (forward-looking for Mode 2 conformity probes). Adding owslib pulls in lxml + heavy XML stack for a substrate that only needs `urlencode` + `requests`. Request shapes are pinned by tests; future owslib adoption is mechanical.

- **Decision: `_fetch_nlcd_landcover_bytes` routes through the shared adapter; cache key unchanged.** The refactor is "remove duplicated WCS code, not break the substrate." Same WCS 1.0.0 GetCoverage shape; OGCAdapterError wrapped to UpstreamAPIError.

- **Decision: YAML loader widens bare-date `last_verified` to UTC midnight on load.** Avoids forcing curator to amend all 30 entries before this job lands. Surfaced as OQ-47-CATALOG-LAST-VERIFIED-WIDEN for the D.11 migration cleanup.

- **Decision: skip-and-warn for 2 entries that fail credential-tier consistency.** Census ACS + NASA FIRMS declare `credential_tier: 2` without `api_key_secret_ref`; loader skips with WARNING. Other 28 entries are fully functional. Surfaced as OQ-47-CATALOG-YAML-SECRET-REFS for infra + curator.

- **Decision: ArcGIS ImageServer → `/exportImage`; MapServer/FeatureServer → `/<layer>/query`.** ImageServer's only query is `/exportImage`; `/<layer>/query` returns 400 (verified live with 3DEP). The dispatcher sniffs `/imageserver` and switches route + param shape. Same `ARCGIS_REST` service_type, two sub-dispatches.

- **Decision: catalog_search scoring uses substring + content-word token overlap with stopword filter.** Stopwords (`data, source, the, of, for, …`) are skipped to prevent filler-word false positives; name matches boost score. Captured as OQ-47-CATALOG-SEARCH-RANKER for future BM25 / embedding-based search.

- **Decision: bbox-coverage filter is text-sniff heuristic.** Looks for CONUS/L48/US-Federal/USGS tokens in description+name+how_to_use; drops entries for bbox centers outside the broad US envelope (-180/-60 lon × 15/75 lat). Recall over precision. Captured as OQ-47-CATALOG-COVERAGE-INDEX for Mode 2 `coverage_envelope` field.

- **Decision: Tier 1 + Tier 4 catalog_fetch raise NotImplementedError in v0.1.** Dedicated fetchers (`fetch_dem` STAC for Copernicus DEM; `fetch_population` WorldPop; `fetch_river_geometry` NHDPlus HR) already implement per-source paths with per-source quantization. Surfaced as OQ-47-CATALOG-TIER1-STAC + OQ-47-CATALOG-TIER4-REGION. Tier 2 + Tier 3 cover the kickoff's evidence-required fetches.

- **Decision: `catalog_search` ttl_class="semi-static-7d"; `catalog_fetch` ttl_class="static-30d".** Search depends on YAML content (changes weekly with curator amendments); fetch results are upstream static sources (DEM, flood zones, landcover). Per-entry TTL override = OQ-47-CATALOG-FETCH-PER-ENTRY-TTL.

## Invariants Touched

- **Invariant 1 (Determinism boundary): preserves.** Typed shapes throughout; no LLM in catalog dispatch.
- **Invariant 3 (Engine registration, not modification): preserves.** Both tools register via `@register_tool`; no agent-core edits.
- **Invariant 4 (Rendering through QGIS Server): preserves.** Catalog-fetched bytes flow to LayerURI / cache → QGIS Server seam. Style preset routing by source_class.
- **Invariant 7 (Claims carry provenance): preserves + extends.** `catalog_fetch` propagates entry.citation + last_verified into the returned dict.
- **FR-CE-8 (cache-shim integration): preserves + extends.** Both tools cacheable=True, both call read_through exactly once.
- **§F.1.1 Tier-2 single-source-of-truth (kickoff acceptance): lands.** One WCS implementation (the adapter); fetch_landcover refactored; future Tier-2 fetchers must route through the adapter.
- **NFR-R-1 (external-API resilience): extends.** Per-call timeout, OGC ExceptionReport detection, ArcGIS error-JSON detection, sub-64-byte body protection.

## Open Questions

- **OQ-47-OWSLIB-CHOICE (TENTATIVE: direct HTTP; revisit at Mode 2).** owslib offers Capabilities introspection useful for Mode 2 conformity probes.
- **OQ-47-CATALOG-YAML-SECRET-REFS (BLOCKING for 2 entries; TENTATIVE: skip-and-warn).** Census ACS + NASA FIRMS need Secret Manager paths; routes to infra + engine curator pass.
- **OQ-47-CATALOG-LAST-VERIFIED-WIDEN (TENTATIVE: widen on load).** D.11 migration follow-up.
- **OQ-47-CATALOG-SEARCH-RANKER (TENTATIVE: substring + token-overlap with stopwords).** BM25 / embedding follow-up.
- **OQ-47-CATALOG-COVERAGE-INDEX (TENTATIVE: text-sniff for CONUS/US).** Mode 2 `coverage_envelope` field follow-up.
- **OQ-47-CATALOG-TIER1-STAC (TENTATIVE: NotImplementedError in v0.1).** Generic STAC dispatch follow-up.
- **OQ-47-CATALOG-TIER3-RANGE (TENTATIVE: full-body GET in v0.1).** Range-windowed catalog dispatch follow-up.
- **OQ-47-CATALOG-TIER4-REGION (TENTATIVE: NotImplementedError in v0.1).** Region+clip via entry.coverage follow-up.
- **OQ-47-CATALOG-FETCH-PER-ENTRY-TTL (TENTATIVE: fixed static-30d).** read_through signature extension.
- **OQ-47-WMTS-DIALECT (TENTATIVE: not in v0.1).** No WMTS in 30-entry seed.
- **OQ-47-CATALOG-STYLE-PRESET-ROUTING (TENTATIVE: source_class substring).** May want preset routing table or entry field.
- **OQ-47-CATALOG-LIVE-EVIDENCE-NO-GCS (TENTATIVE: FakeStorageClient).** Dev env lacks ADC; live evidence used fake GCS but real upstream HTTPS. Routes to testing for live-GCS round-trip.

## Dependencies and Impacts

- **Depends on:**
  - **job-0045-schema-20260607 (APPROVED):** CatalogEntry pydantic shape — used by `load_catalog` validation.
  - **job-0046-research-20260607 (APPROVED):** 30-entry `public_data_source_catalog.yaml` — 28 entries validate cleanly.
  - **job-0044-engine-20260607 (APPROVED):** MRLC WCS 1.0.0 substrate preserved by the refactor; pinning test still passes.
  - **job-0033-engine-20260606 (APPROVED):** read_through cache shim + @register_tool + eager-import pattern.

- **Affects:**
  - **agent (Mode 1 routing):** new tool pair; `how_to_use` string drives LLM's subsequent `catalog_fetch` call.
  - **engine (future Tier-2 fetchers):** must route through `ogc_adapter.fetch_ogc_layer`; design discipline note in module.
  - **engine job-0054 (concurrent):** disjoint file ownership; sfincs_builder.py unstaged drift NOT touched.
  - **infra:** OQ-47-CATALOG-YAML-SECRET-REFS — provision Secret Manager paths for 2 entries.

- **No schema pushback.** CatalogEntry shape is exactly right for v0.1.

## Verification

- **Tests run:**
  - `services/agent/tests/test_catalog_tools.py`: **23 passed** in 0.18s.
  - Full agent suite: **159 passed** in 2.81s (baseline 133; +26 new).
  - Contracts no-regression: **142 passed** in 0.41s (unchanged).
- **Startup verification:** `python -m grace2_agent --startup-only` → `tool registry loaded: 16 tool(s)` (acceptance criterion hit). Evidence: `evidence/startup_log.txt`.
- **Live E2E evidence:**
  - `catalog_search(topic="flood zones", location=fort_myers_bbox)`: FEMA NFHL ranked #1 (score 7.0).
  - `catalog_search(topic="DEM")`: 3 DEM entries (Copernicus GLO-30, GLO-90, HydroSHEDS) ranked #1-3 (score 8.0 each).
  - `catalog_fetch("fema-nfhl-flood-zones", {bbox=fort_myers, layer_id="28"})`: **8,045,110 bytes** real GeoJSON FeatureCollection from `hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28/query`, content-type `application/geo+json`. Visible polygon coordinates in body.
  - `catalog_fetch("usgs-3dep-elevation-image-service", {bbox=fort_myers})`: **4,195,622 bytes** real GeoTIFF from `elevation.nationalmap.gov/.../ImageServer/exportImage` with `format=tiff&size=1024,1024`, content-type `image/tiff`, TIFF magic `II*\x00` + valid IFD (band F32, single-band elevation).
- **FROZEN-paths check:** confirmed by `git status --short`. Concurrent-job working-tree drift (sfincs_builder.py, test_model_flood_scenario.py, ws_session_state.json, .gitignore, m2 artifacts) NOT staged.
- **Results: PASS** — all 7 kickoff acceptance criteria met.
