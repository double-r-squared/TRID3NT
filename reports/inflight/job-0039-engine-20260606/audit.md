# Audit: 3 new fetcher atomic tools (fetch_landcover, fetch_river_geometry, lookup_precip_return_period)

**Job ID:** job-0039-engine-20260606, **Sprint:** sprint-07, **Auditor:** Development Orchestrator, **Status:** assigned

## Task Assignment

**Specialist:** engine

**Prerequisites:**
- **job-0033 (APPROVED):** existing data_fetch.py pattern; cache shim integration; LayerURI return shape
- **job-0037 (APPROVED):** WorldPop Tier-1 default flip; file lock on data_fetch.py released
- **job-0038 (APPROVED):** OQ-4 HydroMT depth decision in `docs/decisions/oq-4-hydromt-depth.md` — read end-to-end before starting. **The decision establishes concrete contracts that bind this job:** `fetch_landcover` must return the NLCD vintage year alongside the LayerURI (so job-0042 `build_sfincs_model` can validate the Manning's mapping CSV covers the fetched vintage); `fetch_dem`-equivalent contract for the new fetchers (GCS-readable LayerURI with `filesystem: gcs` catalog entry for HydroMT's data catalog bridging).

**SRS references** (narrow files only):
- `docs/srs/03-functional-requirements.md` — FR-TA-2 (atomic tools), FR-AS-3 (registration discipline), §3.9 FR-DC (caching), FR-CE-8
- `docs/srs/F-data-sources-discovery-secrets.md` — §F.1 credential tiering, §F.1.1 access pattern tiering (the new v0.3.17 discipline you must record in docstrings)
- `docs/srs/02-system-overview.md` — Decision K (default-by-fetch); engine catalog (SFINCS context)
- `docs/decisions/oq-4-hydromt-depth.md` — full HydroMT decision; §4 "Immediate (job-0039)" section is yours
- DO NOT load `docs/SRS_v0.3.md` monolith.

### Environment
The three new fetchers consume from these Tier-1 (key-free) sources:
- **NLCD (National Land Cover Database)** via MRLC. Likely access tier: **Tier 3** (direct HTTPS + Range support, COG-backed) per §F.1.1. Verify live before locking. Vintage years 2019, 2021 are most-relevant; latest is 2023 (Annual NLCD Collection 1.0). Annually-released.
- **NHDPlus HR (National Hydrography Dataset Plus High Resolution)** via USGS. Likely access tier: **Tier 4** (region download — HUC4 / HUC8-scoped FlatGeobuf files; no per-bbox query) per §F.1.1. Verify live before locking. Stable across multiyear cycles.
- **NOAA Atlas 14 PFDS (Precipitation Frequency Data Server)** via NWS HDSC. Likely access tier: **Tier 3** (HTTPS endpoint per coordinate / per bbox; CSV / GeoTIFF responses) per §F.1.1. Verify live before locking. Volume re-releases every 5–10 years (stable per volume).

### Scope

1. **`services/agent/src/grace2_agent/tools/data_fetch.py`** — extend with three new fetcher tools, each `@register_tool(AtomicToolMetadata(...))`:
   - `fetch_landcover(bbox, dataset="nlcd_2021") → LayerURI` — Tier 1 access via job-0038 contract. `ttl_class="static-30d"`, `source_class="landcover"`. **MUST return the NLCD vintage year as a sidecar field** (e.g., `LayerURI.metadata["nlcd_vintage_year"] = 2021`) so job-0042 `build_sfincs_model` can validate the Manning's mapping CSV. Tier-1 default is NLCD; ESA WorldCover opt-in via `dataset="esa_worldcover_2021"`.
   - `fetch_river_geometry(bbox, source="nhdplus_hr") → LayerURI` — `ttl_class="static-30d"`, `source_class="river_geometry"`. Probably routes to NHDPlus HR HUC4-scoped FlatGeobuf (Tier 4 region download per §F.1.1 — confirm live and document).
   - `lookup_precip_return_period(location, return_period_years, duration_hours) → dict` — NOAA Atlas 14 PFDS. `ttl_class="static-30d"`, `source_class="precip_return_period"`. Returns `{precip_inches, units, location, vintage_volume}`. Routes through cache shim.

2. **Each fetcher records the §F.1.1 access tier in its FR-TA-3 docstring** — per v0.3.17 discipline. Format:
   ```
   Access pattern: Tier 3 (direct HTTPS + HTTP Range; COG-backed via /vsicurl/)
   ```
   Tier choice is informed by live verification of the upstream provider (not just "the SRS implied tier X"). If your live verification reveals a different tier than what the kickoff implied, take the live-verified answer + surface the discrepancy as an OQ.

3. **Per-source bbox quantization grids** (per OQ-32-QUANTIZATION-LOCATION):
   - NLCD: 30m native; quantize bbox to 30m before cache-key derivation
   - NHDPlus HR: HUC4-scoped (region-download Tier 4); cache key includes HUC4 region per §F.1.1 Tier-4 discipline
   - Atlas 14: point/grid value; quantize location to source-native grid (1/120 degree typically)

4. **HydroMT data-catalog bridging** (per job-0038 §4): each layer-producing tool's LayerURI must be readable by HydroMT's `raster` / `vector` drivers via `fsspec[gcs]`. This means the `uri` field is a `gs://` path (not a signed URL) and the cache bucket is configured for the agent-runtime SA to read. Job-0040 already wired this — the sfincs-runtime SA has `cache:objectViewer`; ensure the fetcher writes are also readable by sfincs-runtime through the cache bucket's bucket-scoped IAM.

5. **Live evidence** in `evidence/`:
   - `fetch_landcover((-81.92, 26.55, -81.80, 26.68), dataset="nlcd_2021")` for Fort Myers — `gcloud storage describe` showing the COG + customTime + the recorded vintage year
   - `fetch_river_geometry((-81.92, 26.55, -81.80, 26.68))` — captured response (HUC4 region if Tier 4)
   - `lookup_precip_return_period(location=(26.6, -81.9), return_period_years=100, duration_hours=24)` — captured Atlas 14 response

6. **Tests** in `services/agent/tests/test_data_fetch.py` — extend additively. At least 6 new unit tests (registration + happy path + tier-recording-in-docstring + per-source quantization + vintage-year sidecar for landcover + cache-miss → fetch → write path).

### File ownership (exclusive)

- `services/agent/src/grace2_agent/tools/data_fetch.py` — additive only; don't refactor existing fetchers
- `services/agent/tests/test_data_fetch.py` — additive
- `services/agent/pyproject.toml` — runtime deps as needed (likely `requests` already there; possibly `pyhmf` or `dataretrieval` for some Atlas 14 endpoints; verify live)
- `reports/inflight/job-0039-engine-20260606/` — kickoff frozen

### FROZEN — no edits in this job

- All other `services/agent/src/grace2_agent/tools/*.py` files (cache.py, passthroughs.py, qgis_discovery.py, __init__.py, README.md)
- `services/agent/src/grace2_agent/{main,server,mcp,pipeline_emitter}.py`
- `packages/contracts/**`, `infra/**`, `web/**`, `docs/srs/**`, `docs/SRS_v0.3.md`, `styles/**`, `services/workers/**`, `reports/complete/**`
- Stage B/C concurrent jobs: do not edit anything job-0041 owns

### Cross-cutting principles in force

- **Invariant 1 (Determinism boundary):** preserves.
- **Invariant 5 (Tier separation):** preserves — fetched layers land in cache bucket via agent-runtime SA.
- **Invariant 7 (no silent wrong answers / claims have provenance):** the NLCD vintage-year sidecar from `fetch_landcover` is exactly the Invariant 7 mitigation OQ-4 demanded for the Manning's mapping validation. DO NOT skip the sidecar.
- **FR-CE-8:** all 3 tools cacheable; route through `read_through`.
- **§F.1.1 access tier discipline:** record tier in docstring at implementation time after live verification. Don't trust the kickoff's inferred tier; verify it.
- **Diagnose before fix:** if upstream API surprises (like job-0037's WorldPop discoveries), capture before changing tool logic.

### Acceptance criteria (reviewer re-runs)

- [ ] 3 new fetcher tools registered; `TOOL_REGISTRY` shows 11 tools on `--startup-only` (M4's 8 + 3 new).
- [ ] Each tool's docstring records `Access pattern: Tier N (...)` per §F.1.1.
- [ ] `fetch_landcover` returns NLCD vintage year as sidecar metadata.
- [ ] All 3 tools route through `read_through` per FR-CE-8.
- [ ] Per-source bbox quantization applied; cache-key dedup verified by parallel-write test.
- [ ] At least 6 new unit tests; full agent suite + contracts green.
- [ ] Live evidence under `evidence/` for all 3 tools.
- [ ] No edits to FROZEN paths.

Surface contestable choices as Open Questions with TENTATIVE tags — at minimum: NLCD vintage default (2021 vs 2023); NHDPlus HR HUC4-scope per-call routing; Atlas 14 partial-bbox vs single-point behavior; any access-tier deviation from the kickoff's inferred tier (live-verified wins).
