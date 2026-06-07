# Audit: NLCD WMS palette encoding hotfix (OQ-42 critical blocker for M5 real SFINCS runs)

**Job ID:** job-0044-engine-20260607, **Sprint:** sprint-07 (mid-sprint addition), **Auditor:** Development Orchestrator, **Status:** assigned

## Task Assignment

**Specialist:** engine

**Prerequisites:**
- **job-0039 (APPROVED):** `fetch_landcover` lands NLCD via MRLC WMS GetMap; returns `LayerURI` + `nlcd_vintage_year` sidecar
- **job-0042 (APPROVED):** NLCD validation gate in `build_sfincs_model` fires correctly on the palette-encoded indices — the gate works; the problem is the upstream encoding

**The bug:** MRLC's WMS GetMap with `format=image/geotiff` returns a **palette-encoded** GeoTIFF — raster bytes are palette indices `[1,3,4,5,6,7,9,10,11,13,14,18,20,21]` referring to an internal colormap table, NOT the canonical NLCD class integers `[11,12,21,22,23,24,31,41,42,43,51,52,71,72,73,74,81,82,90,95]`. The Manning's mapping CSV (job-0042's `manning_mapping.csv` v1.0.0) is keyed by canonical NLCD integers, so the validation gate fires correctly + the workflow fails honest — but this means real SFINCS runs can't proceed.

### Environment
Two possible fix paths:

- **Path A: Translate palette indices to canonical NLCD integers via the colormap table.** The GeoTIFF's `ColorTable` IFD entry should have the 256-entry color palette MRLC writes; we'd extract it + invert (palette_index → RGB → canonical NLCD via a small lookup table). Adds a translation step but keeps the WMS access path.
- **Path B: Switch fetcher to an unpaletted source.** MRLC also serves NLCD via `https://www.mrlc.gov/geoserver/mrlc_display/wcs` (WCS) — IF the WCS endpoint is alive (job-0039 noted live timeout on GetCapabilities). Or via direct file download from `s3://mrlc/...` — but job-0039 found those files are 42-byte placeholder stubs. So this path may not work.
- **Path C: Add a separate `fetch_landcover_canonical` variant** that wraps the palette-decoder. Forks the API surface; less ideal.

**Live-verification discipline applies.** Don't pick a path from this kickoff; live-probe each candidate first per the §F.1.1 discipline + the job-0037 + job-0039 lessons.

### Scope

1. **Live-probe Path A**: pull a small NLCD tile via the existing `fetch_landcover` + inspect the GeoTIFF's ColorTable IFD entry (via rasterio or GDAL). Confirm the palette indices map cleanly to RGB values; reverse-engineer the RGB → canonical NLCD integer table (MRLC publishes the canonical NLCD colormap — find + cite the reference).

2. **Live-probe Path B**: try the MRLC WCS endpoint (`https://www.mrlc.gov/geoserver/mrlc_display/wcs?...REQUEST=GetCoverage&CoverageId=Annual_NLCD_LndCov_2021_CU_C1V0...`). Confirm whether it returns canonical NLCD integers without palette encoding.

3. **Pick path + implement**:
   - If Path B works → switch `fetch_landcover` to WCS as the Tier 1 source; update its docstring access tier (likely Tier 2 OGC WCS).
   - If Path B doesn't work → implement Path A: add a `_decode_nlcd_palette(geotiff_bytes) -> bytes` helper in `data_fetch.py` that reads the ColorTable, builds the palette→canonical lookup, rewrites the raster bytes; integrate before the cached write so the cached COG has canonical integers.
   - Whichever path lands, the `nlcd_vintage_year` sidecar contract from job-0039 stays intact.

4. **Re-run the job-0042 smoke** end-to-end after the fix. Validation gate should now pass (because the raster carries canonical integers); SFINCS dispatch should proceed; either SFINCS succeeds (full successful pipeline + the screenshot moment) or it fails for a different reason (which we surface honestly).

5. **Update tests** in `services/agent/tests/test_data_fetch.py` to cover the new decode path (Path A) OR the WCS path (Path B). At least 2 new tests + verify no regression in existing landcover tests.

### File ownership (exclusive)

- `services/agent/src/grace2_agent/tools/data_fetch.py` — `fetch_landcover` only
- `services/agent/tests/test_data_fetch.py` — additive
- `services/agent/pyproject.toml` — add deps if needed (rasterio likely already there)
- `reports/inflight/job-0044-engine-20260607/`

### FROZEN — no edits in this job

- All other `services/agent/src/grace2_agent/tools/*.py` files (cache.py, passthroughs.py, qgis_discovery.py, solver.py)
- `services/agent/src/grace2_agent/workflows/**` (job-0042's territory — the validation gate works as designed; don't touch)
- `services/agent/src/grace2_agent/{main,server,mcp,pipeline_emitter}.py`
- `packages/contracts/**`, `infra/**`, `web/**`, `docs/srs/**`, `docs/SRS_v0.3.md`, `styles/**`, `services/workers/**`, `reports/complete/**`

### Cross-cutting principles in force

- **Invariant 1 + 2:** preserved; palette decoding is pure deterministic raster rewrite.
- **Invariant 7:** preserved — the gate's job is to fail closed on bad data; this hotfix removes the bad-data condition, not the gate.
- **§F.1.1 access tier discipline:** if Path B (WCS) lands, update docstring tier from Tier 2 WMS to Tier 2 WCS (still Tier 2).
- **§F.1 prose alignment carry-forward:** post-fix update §F.1 NLCD entry "how to use" metadata to record the palette quirk + the chosen workaround.
- **Diagnose before fix:** the diagnosis is done (job-0042 caught it); this job picks + implements the fix.

### Acceptance criteria (reviewer re-runs)

- [ ] `fetch_landcover` returns a COG with canonical NLCD class integers (verified by inspecting the live evidence COG's pixel values).
- [ ] Job-0042's validation gate now PASSES on a real Fort Myers landcover fetch.
- [ ] Re-run job-0042's smoke workflow end-to-end; capture the result (either successful AssessmentEnvelope OR an honest different failure mode).
- [ ] At least 2 new tests covering the decode/WCS path; existing landcover tests still pass.
- [ ] Live verification of the path chosen — don't guess from the kickoff.
- [ ] No edits to FROZEN paths.

Surface contestable choices as Open Questions with TENTATIVE tags — at minimum: Path A vs Path B vs Path C decision (with live evidence cited); MRLC canonical NLCD colormap citation; whether to also re-attempt the s3 mirror after this fix (revisit OQ-39-NLCD-TIER-DEVIATION); cached-COG migration policy (do existing palette-encoded cache entries get invalidated, or is the cache key shape enough to make this a no-op?).
