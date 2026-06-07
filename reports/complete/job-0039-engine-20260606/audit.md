# Audit: 3 new fetcher atomic tools (fetch_landcover, fetch_river_geometry, lookup_precip_return_period)

**Job ID:** job-0039-engine-20260606, **Sprint:** sprint-07, **Auditor:** Development Orchestrator, **Status:** approved

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

## Assessment

**Verdict:** approved.

Three new fetcher tools land in `data_fetch.py` (+1029 lines additive, no refactor of existing M4 fetchers). Tool registry now reports 13 tools — exact expected count (2 passthroughs + 4 M4 fetchers + 2 QGIS discovery + 3 new this job + 2 solver tools from concurrent job-0041). Tests: 22 new + 82 prior = 104/104 agent suite green in 1.32s. Contracts still 131/131.

**Significant live-verification discovery — NLCD is Tier 2, not Tier 3.** The kickoff inferred direct HTTPS + Range based on documentation; live probing revealed the MRLC direct file mirror at `s3-us-west-2.amazonaws.com/mrlc/Annual_NLCD_LndCov_*.tif` returns a **42-byte placeholder stub** (1×1 TIFF IFD with two `0xFFFFFFFF` strip offsets — not a real raster), and the MRLC WCS GetCapabilities endpoint times out. The actual usable surface is **MRLC GeoServer WMS GetMap** with `format=image/geotiff` — that's Tier 2 per §F.1.1 (OGC service), not Tier 3.

This is exactly the live-verification discipline the §F.1.1 amendment demands and the second sprint-07 example (after job-0037's WorldPop discoveries) of the kickoff-inferred-tier ≠ live-reality pattern. Surfaced as **OQ-39-NLCD-TIER-DEVIATION**. The §F.1 prose alignment carry-forward for v0.3.17+ housekeeping now grows by one more item: update §F.1 NLCD entry to reflect Tier 2 WMS GetMap as the canonical access path.

**Invariant 7 mitigation verified.** `fetch_landcover` returns the NLCD vintage year (`nlcd_vintage_year: 2021`) as sidecar metadata per the OQ-4 HydroMT decision contract. Job-0042's `build_sfincs_model` will consume this for the Manning's mapping CSV validation gate that prevents silent-wrong Manning's grids. The sidecar is delivered as a dict top-level field (not via `LayerURI.metadata`) because `LayerURI` is `extra="forbid"` per contracts FROZEN — correct boundary preservation; routed as OQ-39-LANDCOVER-RETURN-SHAPE-CONTRACT-PROMOTION.

**Live evidence solid** — all 3 tools exercised against Fort Myers bbox with real GCS writes:
- NLCD 2021 GeoTIFF: 194,837 bytes COG via WMS GetMap, datetime customTime ✓
- NHDPlus HR HUC4 0309: 567-feature NHDFlowline FlatGeobuf 229,296 bytes after 144 MB HUC4 GDB region download + bbox clip (Tier 4 confirmed)
- NOAA Atlas 14 V9V2 100-yr 24-hr precipitation at Fort Myers: **12.1 inches**, 1597-byte CSV cached; second call hit cache verified.

**Commit attribution race noted.** Commit `ea70c1d` carries this job's files (data_fetch.py + tests + 9 evidence files) but was authored under job-0041's commit message because both jobs ran concurrently and the git-add operation was misdirected. Commit `c7ce917` then landed job-0041's actual files with a "previous misfile" note. Per AGENTS.md Completed Job Immutability, the commit messages stay as-is; PROJECT_LOG clarifies which commit holds which job's work. No code or evidence is lost.

## Invariant Check

- **Invariant 1 (Determinism boundary):** preserved. Quantization is pure-function; no LLM in the data path.
- **Invariant 5 (Tier separation):** preserved.
- **Invariant 7 (no silent wrong answers / claims have provenance):** verified by NLCD vintage-year sidecar — exactly the OQ-4 mitigation requirement. Job-0042 inherits the validation gate.
- **FR-CE-8 fail-fast registration:** verified.
- **§F.1.1 access tier discipline:** honored — tier recorded in each tool's docstring from live verification, not kickoff inference. NLCD tier deviation correctly surfaced.

## Dependency Check

- **job-0033, 0037** (data_fetch.py pattern) — extended additively.
- **job-0038 OQ-4 decision** — sidecar contract honored; downstream consumer ready for job-0042.
- **v0.3.17 §F.1.1 + v0.3.18 §F.1.2** — access tier discipline applied; NLCD deviation feeds catalog entries when sprint-08 lands Mode 1.

## Decisions Validated

All key decisions reviewed and accepted: NLCD WMS GetMap pivot (Tier 2 over the broken Tier 3 mirror); vintage default 2021 (MRLC catalog tops at L48 NLCD); HUC4-scoped region download for NHDPlus HR; Atlas 14 single-point CSV via PFDS endpoint.

## Open Questions Resolved

Filed for triage:
- **OQ-39-NLCD-TIER-DEVIATION** — feeds §F.1 prose housekeeping (NLCD now Tier 2 WMS not Tier 3) at planned sprint-07-close pass.
- **OQ-39-LANDCOVER-RETURN-SHAPE-CONTRACT-PROMOTION** — `LayerURI` extra="forbid" forces sidecar on dict top-level; future schema sprint can promote to proper field with `metadata: dict[str, Any]`.
- **OQ-39-NLCD-VINTAGE-DEFAULT** — 2021 picked; revisit at sprint-09+ when MRLC publishes 2023.
- **OQ-39-ESA-WORLDCOVER-SUBSTRATE** — opt-in path scaffolded but not exercised live.
- **OQ-39-NHDPLUSHR-HUC4-ROUTING-HEURISTIC** — current bbox→HUC4 routing is a simple envelope; revisit if multi-HUC4 bboxes surface.
- **OQ-39-NHDPLUSHR-TWO-STAGE-CACHE** — region-file caching strategy follow-up (mirrors OQ-37-COUNTRY-FILE-CACHING-STRATEGY).
- **OQ-39-ATLAS14-SINGLE-POINT-VS-BBOX** — current API supports single-point only; bbox averaging is a follow-up if needed.

## Follow-up Actions

1. **Unblock Stage D (job-0042 `model_flood_scenario` workflow)** — job-0039 + 0041 both approved.
2. **v0.3.17 housekeeping pass at sprint-07 close** — add NLCD Tier 2 prose alignment to the pile.
3. **PROJECT_LOG commit attribution clarification** — note that `ea70c1d` carries job-0039 files (under job-0041's accidentally-attributed message); `c7ce917` is job-0041's real commit.

## Sign-off

**Approved 2026-06-07 by Development Orchestrator.**

All 8 acceptance criteria met. Live NLCD tier discovery is exactly the kind of catch §F.1.1 was designed to surface. Invariant 7 sidecar contract delivered. 13 tools registered on startup. 22 new tests; 104/104 agent suite green.

Sprint-07 Stage B complete.
