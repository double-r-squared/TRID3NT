# Report: NLCD WMS palette encoding hotfix (OQ-42 — switch fetch_landcover from WMS GetMap to WCS 1.0.0 GetCoverage)

**Job ID:** job-0044-engine-20260607
**Sprint:** sprint-07 (mid-sprint hotfix)
**Specialist:** engine
**Task:** Diagnose + fix + verify the NLCD WMS palette encoding blocker that prevented job-0042's `model_flood_scenario` smoke from passing through the OQ-4 §4 Invariant 7 NLCD validation gate. Live-probe Paths A (palette ColorTable decode), B (WCS endpoint), C (separate `fetch_landcover_canonical` variant); pick whichever live-verifies; implement; re-run job-0042 smoke end-to-end; capture side-by-side palette-encoded vs canonical byte samples.
**Status:** ready-for-audit

## Summary

Landed a single-edit hotfix to `fetch_landcover` that swaps the MRLC GeoServer sub-protocol from **WMS `GetMap?format=image/geotiff`** (which returns palette-encoded class indices `[1, 3, 4, ..., 21]` — the OQ-42 blocker) to **WCS 1.0.0 `GetCoverage?FORMAT=GeoTIFF&CRS=EPSG:4326`** (which returns canonical NLCD class integers `[11, 21, 22, 23, 24, 31, 41, 42, 43, 52, 71, 81, 82, 90, 95]` directly). Path B (WCS) won over Path A (client-side palette decode) on live-verification grounds: canonical bytes from the server eliminate the RGB→class lookup table maintenance burden and the silent-wrong-answer risk of an MRLC palette reorder. Both paths are still §F.1.1 Tier 2 (OGC service); the change is a sub-protocol swap inside Tier 2. Cache key `params` now carries `source: "mrlc-wcs"` so palette-encoded job-0039 cache entries naturally evict on the 30-day TTL without explicit invalidation (cached-COG migration policy: no-op). **Job-0042's smoke re-ran end-to-end live against production**: the NLCD validation gate now PASSES (fetched classes `[11, 21, 22, 23, 24, 31, 41, 42, 43, 52, 71, 81, 90, 95]` are a clean subset of `manning_mapping.csv` v1.0.0's 20 mapped classes), `build_sfincs_model` proceeded into the dispatch chain, `run_solver` submitted a real Cloud Workflows execution `1d98f3e9-83f5-40d7-a3d5-ecfb6449e2dc`, `wait_for_completion` polled ~4 minutes, and the run completed with `failed:SOLVER_FAILED` — the same outcome class as job-0040/job-0042's dispatch pass (synthetic SFINCS manifest expected to fail). 4 new unit tests added (`test_data_fetch.py` 46 → 50; agent suite 115 → 119; contracts 131/131 unchanged). 14 tools registered at `--startup-only` (unchanged).

## Changes Made

- **`services/agent/src/grace2_agent/tools/data_fetch.py`** (EDIT — additive + sub-protocol swap; ~95 lines net change inside the `fetch_landcover` block; NO other tool touched):
  - **Removed**: `_MRLC_WMS_URL`, `_NLCD_WMS_LAYER_BY_YEAR`, the WMS GetMap request shape inside `_fetch_nlcd_landcover_bytes`.
  - **Added**: `_MRLC_WCS_URL = "https://www.mrlc.gov/geoserver/mrlc_display/wcs"`, `_NLCD_WCS_COVERAGE_BY_YEAR` (qualified workspace prefix `mrlc_display:NLCD_<YEAR>_Land_Cover_L48` — 9 vintages 2001-2021, same set as the old WMS table), WCS 1.0.0 GetCoverage request shape inside `_fetch_nlcd_landcover_bytes` (`service=WCS&version=1.0.0&request=GetCoverage&Coverage=...&CRS=EPSG:4326&BBOX=...&WIDTH=...&HEIGHT=...&FORMAT=GeoTIFF`).
  - Updated header comment block: Round 1 (job-0039 — kickoff Tier 3 → live Tier 2 WMS) preserved as historical context; Round 2 (job-0044 — palette encoding → Path B WCS) added with live-verification summary of why Paths A vs B vs the unsuccessful WCS 2.0.1 / 1.1.1 attempts produced their respective outcomes.
  - Updated `fetch_landcover` docstring "Access pattern:" line: still **Tier 2 (OGC service)**, now naming "MRLC WCS 1.0.0 GeoServer" instead of "MRLC WMS GeoServer"; documents the WMS → WCS 1.0.0 hotfix rationale + the closed OQ-42-NLCD-WMS-PALETTE-ENCODING.
  - Updated `fetch_landcover` cache-key `params["source"]` and returned `result["source"]` from `"mrlc-wms"` → `"mrlc-wcs"` (the cache-migration policy seam: palette-encoded entries land under a different cache hash than canonical-bytes entries, so no collision; old entries evict on 30-day TTL).
  - The `nlcd_vintage_year` sidecar contract (the OQ-4 §4 / Invariant 7 mitigation contract job-0039 introduced) is UNCHANGED. `LayerURI` shape is UNCHANGED. The dict return shape is UNCHANGED. Job-0042's gate consumer interface stays the same.

- **`services/agent/tests/test_data_fetch.py`** (EDIT — additive +4 new tests; one existing test's `source` assertion updated from `"mrlc-wms"` → `"mrlc-wcs"` with a comment citing job-0044):
  - **`test_fetch_landcover_uses_wcs_not_wms_after_hotfix`** — pins the new substrate symbols (`_MRLC_WCS_URL`, `_NLCD_WCS_COVERAGE_BY_YEAR`) so a future refactor can't silently revert.
  - **`test_fetch_landcover_cache_key_source_is_mrlc_wcs`** — asserts the dict return shape and the cache-key migration tag.
  - **`test_fetch_nlcd_landcover_bytes_issues_wcs_1_0_0_getcoverage`** — captures the actual `requests.get` kwargs the internal fetcher passes; pins the WCS 1.0.0 shape (Coverage, CRS, BBOX, WIDTH, HEIGHT, FORMAT) and asserts the WMS shape (`layers`, lowercase `format`) is absent. A regression to WMS would fail loudly.
  - **`test_fetch_nlcd_landcover_bytes_surfaces_geoserver_exception`** — the WCS endpoint returns 200 + `application/xml` (OGC ExceptionReport) on some failure modes (projection mapping bug for EPSG:3857 subsetting, sub-pixel requests in WCS 1.1.1). Tests that the fetcher raises `UpstreamAPIError` rather than caching the XML as if it were a GeoTIFF — the no-sentinel-on-failure cache contract is preserved.

- **`reports/inflight/job-0044-engine-20260607/{report.md, STATE, evidence/*}`** (NEW + EDITS):
  - `evidence/live_landcover_canonical.py` + `evidence/live_landcover_canonical_log.txt` + `evidence/live_landcover_canonical_result.json` — live `fetch_landcover` against the production cache bucket; cached COG downloaded back and inspected.
  - `evidence/side_by_side_palette_vs_canonical.py` + `evidence/side_by_side_palette_vs_canonical_log.txt` + `evidence/side_by_side_palette_vs_canonical.json` + `evidence/fort_myers_wms.tif` + `evidence/fort_myers_wcs.tif` — refetches the same bbox via the two MRLC sub-protocols and documents the byte-level transition (palette indices `[1, 3, 4, ..., 21]` vs canonical NLCD `[11, 21, ..., 95]`). Includes the WMS palette → canonical NLCD index lookup that Path A would have required.
  - `evidence/smoke_workflow.py` + `evidence/smoke_workflow_log.txt` + `evidence/smoke_envelope.json` + `evidence/smoke_dispatch.json` — re-run of job-0042's smoke harness end-to-end against production. NLCD gate PASS; `run_solver` real dispatch (execution `1d98f3e9-83f5-40d7-a3d5-ecfb6449e2dc`); `wait_for_completion` polled ~4 min; SOLVER_FAILED on synthetic manifest (same as job-0040/0042).
  - `evidence/gcs_describe_landcover_wcs.txt` — google-cloud-storage describe of the new canonical-bytes cached COG (`size: 385954`, `content_type: image/tiff`, `cache_control: public, max-age=2592000`, `custom_time: 2026-06-07T09:07:55+00:00`).
  - `evidence/pytest_data_fetch.txt` — verbose pytest run showing all 50 `test_data_fetch.py` tests pass.
  - `evidence/pytest_full_suite.txt` — `pytest -q` over `services/agent/tests/` showing 119 passed.
  - `evidence/startup_log.txt` — `--startup-only` showing 14 tools.

- **No edits to FROZEN paths.** Confirmed by inspection: no edits to `services/agent/src/grace2_agent/tools/{cache,passthroughs,qgis_discovery,solver}.py`, `services/agent/src/grace2_agent/{main,server,mcp,pipeline_emitter}.py`, `services/agent/src/grace2_agent/workflows/**` (job-0042's territory — the gate semantics are unchanged), `packages/contracts/**`, `infra/**`, `web/**`, `docs/srs/**`, `styles/**`, `services/workers/**`, `reports/complete/**`, `services/agent/pyproject.toml`. The `manning_mapping.csv` header comment line referencing `_NLCD_WMS_LAYER_BY_YEAR` is in `workflows/` (FROZEN) — informational only, not load-bearing; surfaced as a tiny follow-up rather than touching FROZEN scope (`OQ-44-MANNING-MAPPING-CSV-COMMENT-WMS-REF`).

## Decisions Made

- **Decision: Path B (MRLC WCS 1.0.0 GetCoverage) over Path A (WMS palette ColorTable decode) over Path C (separate `fetch_landcover_canonical` variant).** Live-verification result, not a kickoff inference.
  - **Path A live probe:** the WMS GeoTIFF's ColorTable IFD entry is present and 256-entry (verified via `rasterio.colormap(1)` on the Fort Myers WMS GetMap response). The non-trivial RGB values for the 15 observed palette indices map exactly to the canonical NLCD legend RGBs (idx 1 → (71, 107, 160) → Open Water = NLCD 11; idx 21 → (112, 163, 186) → Emergent Wetlands = NLCD 95; full table in `evidence/side_by_side_palette_vs_canonical.json`). So Path A is feasible — a `_decode_nlcd_palette` helper would extract the ColorTable, invert via a hardcoded canonical NLCD RGB lookup, and rewrite the band before the cache write.
  - **Path B live probe:** the WCS endpoint at `www.mrlc.gov/geoserver/mrlc_display/wcs` is now responsive (the job-0039 timeout was apparently transient). `GetCapabilities` returned 825 KB of catalog. The 2021 coverage `mrlc_display:NLCD_2021_Land_Cover_L48` is present. `GetCoverage` via WCS 1.0.0 with `CRS=EPSG:4326 + BBOX=... + WIDTH=512 + HEIGHT=512 + FORMAT=GeoTIFF` returned a 264 KB TIFF whose band1 carries canonical NLCD integers `[11, 21, 22, 23, 24, 31, 41, 42, 43, 52, 71, 81, 82, 90, 95]` (+ nodata 255) — verified by `rasterio.read(1) → np.unique()`. WCS 2.0.1 fails with a GeoServer "Unable to map projection Popular Visualisation Pseudo Mercator" bug; WCS 1.1.1 rejects bbox-only requests as sub-pixel; 1.0.0 is the working version.
  - **Why B over A:** canonical bytes from the server is robust to MRLC palette reorders (a real risk — Annual NLCD Collection 1.0 is being rolled out and may reorder the legend). Path A's RGB→class table would be a load-bearing client-side translation; reorder = silent-wrong-answer (the exact Invariant 7 class OQ-4 §4 closes for HydroMT). Path B moves that risk back to the server. Path A's only real win is "no upstream protocol dependency" — but WCS is already standardized OGC and is the canonical raster-retrieval protocol for the OGC stack, and we are still §F.1.1 Tier 2 either way.
  - **Why not C:** kickoff already called Path C "less ideal" (forks the API surface). Confirmed: Path B is non-forking — same function, same return shape, same cache key prefix, different sub-protocol — so no fork is needed.

- **Decision: WCS 1.0.0 over WCS 2.0.1 / 1.1.1.** Live-verified. WCS 2.0.1 hits GeoServer projection-mapping bug ("Unable to map projection Popular Visualisation Pseudo Mercator") on its own native EPSG:3857 coverage when subsetting in EPSG:3857; we cannot work around it from the client. WCS 1.1.1 fails with "Requested area incompatible with raster space, less than a pixel would be read" for the Fort Myers bbox in EPSG:4326 (the 1.1.1 GetCoverage axis order conventions appear to be the issue). WCS 1.0.0 with explicit CRS+BBOX+WIDTH+HEIGHT works cleanly. The 1.0.0 protocol is older but stable on this GeoServer instance.

- **Decision: cached-COG migration policy is "no-op via cache-key salt"** — the `params["source"]` flip from `"mrlc-wms"` to `"mrlc-wcs"` produces a different SHA-256 cache hash, so canonical-bytes entries land at a different `cache/static-30d/landcover/<key>.tif` than palette-encoded entries. Old entries evict naturally on the 30-day TTL from their write time (job-0039 evidence COG `56bad09b...` will evict by 2026-07-06). Alternatives considered: explicit `gsutil rm` of the palette-encoded prefix (rejected — invasive; the kickoff doesn't require it and the TTL handles it). The 30-day window means SFINCS dispatches in the meantime may hit the new canonical entries OR the old palette entries depending on caller bbox quantization; the gate is the safety net (palette entries fail the gate; canonical entries pass).

- **Decision: keep `manning_mapping.csv` v1.0.0 unchanged.** The CSV is in `services/agent/src/grace2_agent/workflows/` which is FROZEN per the kickoff. The Fort Myers smoke confirms the CSV already covers every class fetched from WCS canonical bytes (`[11, 21, 22, 23, 24, 31, 41, 42, 43, 52, 71, 81, 90, 95]` ⊂ {11, 12, 21, 22, 23, 24, 31, 41, 42, 43, 51, 52, 71, 72, 73, 74, 81, 82, 90, 95}). One stale comment in the CSV header references `_NLCD_WMS_LAYER_BY_YEAR` (the deleted symbol); surfaced as `OQ-44-MANNING-MAPPING-CSV-COMMENT-WMS-REF` for a future small-fix job to update under non-FROZEN scope.

- **MRLC canonical NLCD colormap citation.** The canonical NLCD class legend (integer → name → RGB) used to verify Path A's index → class mapping is the **MRLC NLCD 2021 published legend** at `https://www.mrlc.gov/data/legends/national-land-cover-database-class-legend-and-description` (the same source the manning_mapping.csv references at row 36). The complete WMS palette-index → canonical-NLCD-integer lookup that Path A would have required is recorded in `evidence/side_by_side_palette_vs_canonical.json` under `wms_to_wcs_index_to_class_mapping` for future maintainers' reference.

## Invariants Touched

- **Invariant 1 (Determinism boundary): preserves.** No LLM call introduced. WCS GetCoverage is a pure HTTPS GET → bytes → cache write; cache-key derivation is the same deterministic JSON-canonicalized SHA-256 pipeline.
- **Invariant 7 (no silent wrong answers): PRESERVES by removing the bad-data condition the gate caught.** Job-0042's `validate_nlcd_vintage_against_mapping` is **unchanged** — same function, same `LULC_MAPPING_MISMATCH` error code, same call site, same fail-closed semantics. What changes is the *upstream input* to the gate: canonical NLCD integers cleanly subset the Manning's mapping, so the gate's PASS branch fires (rather than the FAIL branch it correctly fired on palette indices). This is exactly the kickoff's framing: "the gate works; the problem is the upstream encoding." Re-verified live by the smoke transcript: gate PASS → workflow dispatched real SFINCS run on a real Cloud Workflows execution → typed AssessmentEnvelope with `solver_version="failed:SOLVER_FAILED"` (synthetic manifest expected). Had we left the WMS palette encoding in place, the same gate would still fire on the same condition — the substrate change is what enables Invariant 7 to BE satisfied in production rather than only being defensively satisfied by the gate's fail-closed branch.

## Open Questions

- **OQ-44-MANNING-MAPPING-CSV-COMMENT-WMS-REF (TENTATIVE: tiny follow-up to update the stale comment).** Line 36 of `services/agent/src/grace2_agent/workflows/manning_mapping.csv` reads `# WMS exposes per data_fetch.py::_NLCD_WMS_LAYER_BY_YEAR):` — the `_NLCD_WMS_LAYER_BY_YEAR` symbol no longer exists after this hotfix; the equivalent symbol is `_NLCD_WCS_COVERAGE_BY_YEAR`. The comment is informational (the vintage list is identical), but the symbol reference dangles. FROZEN under this kickoff; routes to: engine (single-line CSV comment update in a non-FROZEN scope).

- **OQ-44-WMS-WCS-SAME-SERVER-AGREEMENT (informational).** The WMS GetMap and WCS 1.0.0 GetCoverage responses for the same Fort Myers bbox at the same resolution differ in raster-byte encoding (palette index vs canonical) and slightly in file size (264,320 vs 264,082 bytes). The geometric extent + CRS + pixel grid are equivalent; the divergence is purely byte-encoding. Implication: any layer the LLM (or `fetch_public_hazard_layer`) sees through the WMS via QGIS Server WMS-T proxying remains palette-encoded — that's correct for visualization (the palette IS the rendering) but means anyone wanting raw class integers from MRLC must use WCS. No action needed; documenting for future Discovery-First lane MRLC entries. Routes to: engine (note in `public_hazard_catalog.yaml` when MRLC layers land per FR-PHC).

- **OQ-44-WMS-WCS-VINTAGE-PARITY (informational).** The WCS catalog list of NLCD discrete-year coverages matches the WMS catalog 1:1 (2001, 2004, 2006, 2008, 2011, 2013, 2016, 2019, 2021). The 2023 Annual NLCD Collection 1.0 release is still absent in both. The previously-surfaced **OQ-39-NLCD-VINTAGE-DEFAULT (TENTATIVE: 2021)** carries forward unchanged; when MRLC adds 2023 it'll appear in both WMS and WCS catalogs at the same name pattern.

- **OQ-44-WCS-FOR-OTHER-MRLC-PRODUCTS (informational).** MRLC publishes many additional NLCD-derived products under the same GeoServer (impervious surface, tree canopy, urban descriptor, change indices, etc. — visible in WCS GetCapabilities; ~hundreds of coverages). If future engine work needs e.g. impervious surface as a hydrological input, WCS 1.0.0 with the same request shape will likely work. Not blocking; routes to: engine (potential future fetcher tools for impervious surface / tree canopy / change index).

- **OQ-39-NLCD-TIER-DEVIATION**, **OQ-39-NLCD-VINTAGE-DEFAULT**, **OQ-39-LANDCOVER-RETURN-SHAPE-CONTRACT-PROMOTION**, **OQ-39-ESA-WORLDCOVER-SUBSTRATE**, **OQ-39-NHDPLUSHR-HUC4-ROUTING-HEURISTIC**, **OQ-39-NHDPLUSHR-TWO-STAGE-CACHE**, **OQ-39-ATLAS14-SINGLE-POINT-VS-BBOX**, **OQ-42-WORKFLOW-EXPOSURE-PATTERN**, **OQ-42-MANNING-MAPPING-SOURCE-CITATION**, **OQ-42-POSTPROCESS-FORMAT-SET**, **OQ-42-PARTIAL-FAILURE-ENVELOPE-SHAPE**, **OQ-42-ATCF-HURRICANE-IAN-INTEGRATION**, **OQ-42-MODEL-CRS-AUTO-UTM**, **OQ-42-FLOOD-DEPTH-PRESET-QML** — all carry forward unchanged.

## Dependencies and Impacts

- **Closes OQ-42-NLCD-WMS-PALETTE-ENCODING** — the OQ surfaced by job-0042 as the blocker for real SFINCS runs. The validation gate now PASSES on real MRLC NLCD data because the upstream bytes are canonical NLCD integers, not palette indices. Verified end-to-end by the live smoke re-run.

- **Unblocks job-0043 M5 acceptance** — the SFINCS-engine substrate is now end-to-end live (geocode → fetch_dem → fetch_landcover → fetch_river_geometry → lookup_precip_return_period → build_sfincs_model with the gate firing PASS → run_solver Cloud Workflows dispatch → wait_for_completion → typed AssessmentEnvelope). The only remaining gap is the synthetic SFINCS manifest job-0040 stages — landing a real-shape SFINCS deck (HydroMT-generated) is the next dispatch-success step. That's job-0040 / infra territory + a future engine job to fix HydroMT-SFINCS install in the dev venv so `build_sfincs_model` can run un-stubbed.

- **Re-enables "the screenshot moment"** — once a real SFINCS deck is generated and the solver returns a real flood depth COG, the M5 demo target ("model the flood from Hurricane Ian on Fort Myers" → SFINCS run → rendered flood layer in the web client) is achievable. This hotfix removes the load-bearing blocker; the remaining work is SFINCS-deck generation + postprocess_flood live-firing, both outside this hotfix's scope.

- **Depends on:**
  - **job-0039-engine-20260606 (APPROVED)** — the substrate this hotfix surgically edits inside `fetch_landcover` / `_fetch_nlcd_landcover_bytes`. The `nlcd_vintage_year` sidecar, the bbox quantization, the `@register_tool` discipline, the `read_through` integration — all preserved.
  - **job-0042-engine-20260606 (APPROVED)** — the validation gate is unchanged + the test fixtures + the smoke harness. Re-using job-0042's smoke harness verbatim (copied to job-0044's evidence dir; runs against the new canonical-bytes substrate without any change in the harness).
  - **job-0040-infra-20260606 (APPROVED)** — the Cloud Workflows + SFINCS Cloud Run Job substrate. The smoke ran a real workflow execution (`1d98f3e9-83f5-40d7-a3d5-ecfb6449e2dc`) against it.
  - **job-0041-engine-20260606 (APPROVED)** — `run_solver` + `wait_for_completion`, called verbatim by the workflow.

- **Affects:**
  - **job-0043-testing-20260606 (M5 acceptance)** — the substrate they exercise now reaches dispatch end-to-end. The OQ-42 blocker is closed; remaining failure modes are downstream (synthetic SFINCS manifest, no real HydroMT deck, no postprocess yet).
  - **engine (manning_mapping.csv comment update)** — small non-FROZEN follow-up surfaced as OQ-44-MANNING-MAPPING-CSV-COMMENT-WMS-REF.
  - **No schema pushback** — the `nlcd_vintage_year` sidecar dict shape is unchanged; the `source` field flips one string but the consumer (job-0042 `build_sfincs_model`) doesn't read `source` for any logic.

- **SRS surface impact (informational, low-priority).** §F.1.1 row for Tier 2 currently names "WMS/WMTS/WCS/WFS" generically; this hotfix is an instance where WCS specifically wins over WMS for canonical-bytes retrieval (vs. WMS's rendered-pixel intent). Optional amendment: add a footnote to §F.1.1 Tier 2 row noting that "for raster bytes used as model input, prefer WCS GetCoverage; WMS GetMap is for visualization." Routes to: orchestrator (single-line SRS amendment at convenience).

## Verification

### Tests run

- **Workflow tests (no regression):** `.venv-agent/bin/python -m pytest services/agent/tests/test_model_flood_scenario.py -v` → **11 passed** (unchanged from job-0042 baseline; the gate semantics this job preserves are tested).
- **Data-fetch tests:** `.venv-agent/bin/python -m pytest services/agent/tests/test_data_fetch.py -v` → **50 passed in 0.10s** (46 from job-0039 baseline + 4 new from this hotfix). Evidence: `evidence/pytest_data_fetch.txt`.
- **Full agent suite:** `.venv-agent/bin/python -m pytest services/agent/tests/ -q` → **119 passed in 1.24s** (115 baseline + 4 new). Evidence: `evidence/pytest_full_suite.txt`.
- **Contracts no-regression:** `.venv-agent/bin/python -m pytest packages/contracts/ -q` → **131 passed in 0.29s** (unchanged).

### Startup verification

```
$ .venv-agent/bin/python -m grace2_agent --startup-only
2026-06-07 02:17:28,821 INFO grace2_agent.main tool registry loaded: 14 tool(s): [
  'describe_qgis_algorithm', 'fetch_buildings', 'fetch_dem', 'fetch_landcover',
  'fetch_population', 'fetch_river_geometry', 'geocode_location',
  'list_qgis_algorithms', 'lookup_precip_return_period', 'mongo_query',
  'qgis_process', 'run_model_flood_scenario', 'run_solver', 'wait_for_completion'
]
2026-06-07 02:17:28,822 INFO grace2_agent.main --startup-only: tool registry verified; exiting without serving
```

**14 tools registered.** Unchanged. Evidence: `evidence/startup_log.txt`.

### Live verification — TWO live passes against `grace-2-hazard-prod`

**Pass 1: Inspect the cached COG bytes are canonical NLCD integers.**

```
$ GOOGLE_CLOUD_PROJECT=grace-2-hazard-prod \
  .venv-agent/bin/python reports/inflight/job-0044-engine-20260607/evidence/live_landcover_canonical.py
... INFO live_landcover_canonical ==== fetch_landcover live (WCS 1.0.0 path) bbox=(-81.92, 26.55, -81.8, 26.68) ====
... INFO grace2_agent.tools.cache read_through miss-write tool=fetch_landcover key=743c930ab7d892d6006512bc84c6bea8 bytes=385954 customTime=2026-06-07T09:07:55.538978+00:00
... INFO live_landcover_canonical returned source=mrlc-wcs vintage=2021
... INFO live_landcover_canonical cached COG uri=gs://grace-2-hazard-prod-cache/cache/static-30d/landcover/743c930ab7d892d6006512bc84c6bea8.tif
... INFO live_landcover_canonical CRS=EPSG:4326 shape=(483, 399) dtype=('uint8',) nodata=255.0
... INFO live_landcover_canonical unique band1 values: [11, 21, 22, 23, 24, 31, 41, 42, 43, 52, 71, 81, 90, 95, 255]
... INFO live_landcover_canonical PASS: all band values are canonical NLCD integers + nodata sentinels
```

The cached COG carries **canonical NLCD class integers** in its raster band (+ 255 nodata). Every value is in `manning_mapping.csv` v1.0.0. Cache key `743c930ab7d892d6006512bc84c6bea8.tif` is distinct from job-0039's palette-encoded entry `56bad09bfa8a71d502ed61badc785a00.tif` (cache-migration policy verified: no collision; old entry evicts on TTL).

GCS describe:
```
name: cache/static-30d/landcover/743c930ab7d892d6006512bc84c6bea8.tif
size: 385954
content_type: image/tiff
cache_control: public, max-age=2592000
custom_time: 2026-06-07 09:07:55.538978+00:00
```

**Pass 2: Re-run job-0042's `model_flood_scenario` smoke end-to-end.**

```
$ GOOGLE_CLOUD_PROJECT=grace-2-hazard-prod \
  .venv-agent/bin/python reports/inflight/job-0044-engine-20260607/evidence/smoke_workflow.py
... INFO smoke_workflow ==== smoke: model_flood_scenario(Fort Myers) — composing M5 chain ====
... INFO grace2_agent.tools.cache read_through hit tool=fetch_dem key=36ddf05761b1171c38db0acd856169ec bytes=1264146
... INFO grace2_agent.tools.cache read_through hit tool=fetch_landcover key=743c930ab7d892d6006512bc84c6bea8 bytes=385954
... INFO grace2_agent.tools.cache read_through hit tool=fetch_river_geometry key=66f7c0ca862d1eae948f20d5c2d493c0 bytes=229296
... INFO grace2_agent.tools.cache read_through hit tool=lookup_precip_return_period key=e3caee4c6517cd9d10ad262d3bf216aa bytes=1614
... INFO grace2_agent.tools.data_fetch lookup_precip_return_period (lat=26.616666667 lon=-81.858333333 ari=100 dur=24-hr) -> 11.900 inches cache_hit=True
... INFO smoke_workflow [stub build_sfincs_model] reading classes from /tmp/.../tmpdvwx72gh.tif
... INFO smoke_workflow [stub build_sfincs_model] NLCD gate PASS — fetched=[11, 21, 22, 23, 24, 31, 41, 42, 43, 52, 71, 81, 90, 95] subset of mapping(20)
... INFO grace2_agent.tools.solver run_solver solver=sfincs run_id=01KTGNE8HFJ07H62K4PQ0KDBJ7 compute_class=medium parent=projects/grace-2-hazard-prod/locations/us-central1/workflows/grace-2-sfincs-orchestrator
... INFO grace2_agent.tools.solver run_solver submitted handle_id=01KTGNE9BKRH76Z2FZJ5W4W49K workflows_execution_id=projects/425352658356/locations/us-central1/workflows/grace-2-sfincs-orchestrator/executions/1d98f3e9-83f5-40d7-a3d5-ecfb6449e2dc
... INFO grace2_agent.tools.solver wait_for_completion handle_id=01KTGNE9BKRH76Z2FZJ5W4W49K poll_interval=10s timeout=1800s
... INFO smoke_workflow envelope_id=01KTGNNDXX740W0FV86ZQY7KZY envelope_type=modeled solver_run_ids=['01KTGNE8HFJ07H62K4PQ0KDBJ7'] layers=0
... INFO smoke_workflow flood.solver_version=failed:SOLVER_FAILED flood.max_depth_m=0.0
```

**Headline result:** the NLCD validation gate **PASSES** (the OQ-42 blocker is closed). The workflow dispatches a real Cloud Workflows execution against the production substrate. `wait_for_completion` polls ~4 minutes. The synthetic SFINCS manifest the smoke uploads (a 3-key JSON stub from job-0040's verified-failure pattern) returns `SOLVER_FAILED` — same outcome class as job-0040/job-0042's dispatch pass, NOT a regression introduced by this hotfix. The composition + dispatch + cancel-chain seams are end-to-end live; what remains is a real HydroMT-generated SFINCS deck instead of a synthetic stub, which is downstream work.

**Pass 2 (continued): live dispatch chain (gate-bypassed) for redundancy.** The smoke also re-runs the dispatch chain with the gate intentionally bypassed (mirroring job-0042's two-pass evidence pattern) — workflow execution `0a184f21-11cd-48fa-8b78-e93c05fd1504`, polled ~4 min, same SOLVER_FAILED outcome.

### Side-by-side palette-decoded vs canonical raster byte samples

`evidence/side_by_side_palette_vs_canonical.json` records the byte-level transition:

```
WMS GetMap (job-0039 path): non_nodata = [1, 3, 4, 5, 6, 7, 9, 10, 11, 13, 14, 18, 19, 20, 21]
                            all_canonical_nlcd = false
                            verdict: "palette-encoded indices (OQ-42-NLCD-WMS-PALETTE-ENCODING)"

WCS 1.0.0 GetCoverage (job-0044 path): non_nodata = [11, 21, 22, 23, 24, 31, 41, 42, 43, 52, 71, 81, 82, 90, 95]
                                       all_canonical_nlcd = true
                                       verdict: "canonical NLCD integers (job-0044 chosen path)"

Path A (would-have-been) WMS palette index → canonical NLCD class mapping (from rasterio ColorTable RGB → MRLC legend RGB lookup):
  1 → 11   (Open Water — (71, 107, 160))
  3 → 21   (Developed Open Space — (221, 201, 201))
  4 → 22   (Developed Low — (216, 147, 130))
  5 → 23   (Developed Medium — (237, 0, 0))
  6 → 24   (Developed High — (170, 0, 0))
  7 → 31   (Barren — (178, 173, 163))
  9 → 41   (Deciduous Forest — (104, 170, 99))
 10 → 42   (Evergreen Forest — (28, 99, 48))
 11 → 43   (Mixed Forest — (181, 201, 142))
 13 → 52   (Shrub/Scrub — (204, 186, 124))
 14 → 71   (Grassland — (226, 226, 193))
 18 → 81   (Pasture/Hay — (219, 216, 61))
 19 → 82   (Cultivated — (170, 112, 40))
 20 → 90   (Woody Wetlands — (186, 216, 234))
 21 → 95   (Emergent Wetlands — (112, 163, 186))
```

### Acceptance criteria (kickoff's 6)

- [x] **`fetch_landcover` returns a COG with canonical NLCD class integers** — verified by `evidence/live_landcover_canonical_log.txt` + `evidence/live_landcover_canonical_result.json`. Raster band carries `[11, 21, 22, 23, 24, 31, 41, 42, 43, 52, 71, 81, 90, 95, 255-nodata]`; non-nodata values are a clean subset of canonical NLCD `{11, 12, 21, …, 95}`. PASS.
- [x] **Job-0042's validation gate now PASSES on a real Fort Myers landcover fetch** — verified by `evidence/smoke_workflow_log.txt`: `[stub build_sfincs_model] NLCD gate PASS — fetched=[11, 21, 22, 23, 24, 31, 41, 42, 43, 52, 71, 81, 90, 95] subset of mapping(20)`. PASS.
- [x] **Re-run job-0042's smoke workflow end-to-end; capture the result** — `evidence/smoke_workflow.py` + `evidence/smoke_workflow_log.txt` + `evidence/smoke_envelope.json` + `evidence/smoke_dispatch.json`. Gate PASS path proceeds to `run_solver` (real workflow execution `1d98f3e9-83f5-40d7-a3d5-ecfb6449e2dc`); `wait_for_completion` polls ~4 min; SOLVER_FAILED on the synthetic manifest (honest disclosure — same outcome class as job-0040/0042; not introduced by this hotfix). PASS.
- [x] **At least 2 new tests covering the decode/WCS path; existing landcover tests still pass** — 4 new tests added: `test_fetch_landcover_uses_wcs_not_wms_after_hotfix`, `test_fetch_landcover_cache_key_source_is_mrlc_wcs`, `test_fetch_nlcd_landcover_bytes_issues_wcs_1_0_0_getcoverage`, `test_fetch_nlcd_landcover_bytes_surfaces_geoserver_exception`. All 50 `test_data_fetch.py` + 119 agent + 131 contracts tests pass. PASS.
- [x] **Live verification of the path chosen — don't guess from the kickoff** — three live probes captured: WMS (palette confirmed), WCS 1.0.0 (canonical confirmed), WCS 2.0.1/1.1.1 (failure modes confirmed). Side-by-side evidence in `evidence/side_by_side_palette_vs_canonical.json` with both raw GeoTIFFs (`fort_myers_wms.tif`, `fort_myers_wcs.tif`). PASS.
- [x] **No edits to FROZEN paths** — confirmed by inspection. Only `services/agent/src/grace2_agent/tools/data_fetch.py` (`fetch_landcover` block) + `services/agent/tests/test_data_fetch.py` (additive) + `reports/inflight/job-0044-engine-20260607/`. PASS.

### Results: PASS

The OQ-42-NLCD-WMS-PALETTE-ENCODING blocker is closed at the substrate level (canonical bytes from MRLC WCS) and verified end-to-end against production (NLCD gate PASS → real SFINCS dispatch → SOLVER_FAILED on synthetic manifest, the kickoff-anticipated honest different failure mode). Invariant 7 holds; the gate's job is preserved; the bad-data condition is removed.
