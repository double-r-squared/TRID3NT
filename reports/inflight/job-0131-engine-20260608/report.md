# Report: fetch_era5_reanalysis atomic tool — Copernicus ERA5 Tier-2 compound-flood substrate

**Job ID:** job-0131-engine-20260608
**Sprint:** sprint-12-mega Wave 2
**Specialist:** engine
**Task:** NEW `fetch_era5_reanalysis(bbox, variable, start_date, end_date, api_key=None, secret_ref=None) -> LayerURI` wrapping Copernicus CDS `cdsapi`, with static-30d caching, Tier-2 secret handling, NetCDF→COG conversion, supports_global_query=True. Plus payload-MB estimator; >=4 unit tests + 1 env-gated live test.
**Status:** ready-for-audit

## Summary

Landed the `fetch_era5_reanalysis` Tier-2 atomic tool — a Copernicus CDS `cdsapi`-backed fetcher for ERA5 single-level hourly reanalysis (six allowed variables: wind u/v at 10 m, 2 m temperature, total precipitation, runoff, significant wave height). The tool routes through the FR-DC cache shim with `ttl_class="static-30d"` / `source_class="era5"`, returns a CRS-tagged COG carrying the time-mean across the requested date range, applies the geographic-correctness gate (job-0086) via `rio.clip_box` on the requested bbox, and resolves API keys through the four-path Tier-2 chain (explicit kwarg → SecretRecord via Persistence → env var → `~/.cdsapirc` fallback). The CDS retrieve is wrapped in a 5-minute wall-clock watchdog so a stuck queue surfaces as a retryable `ERA5UpstreamError` instead of hanging the agent process. 23 unit tests pass; 1 live test is env-gated per kickoff. Full agent suite (`pytest services/agent/tests/`) post-landing: 981 passed, 46 skipped.

## Changes Made

- **File: `services/agent/src/grace2_agent/tools/fetch_era5_reanalysis.py` (NEW, ~685 lines)**
  - `AtomicToolMetadata(name="fetch_era5_reanalysis", ttl_class="static-30d", source_class="era5", cacheable=True)`.
  - Decorator: `@register_tool(_METADATA, supports_global_query=True, payload_mb_estimator_name="estimate_payload_mb")`.
  - Six typed errors: `ERA5Error` (base, retryable=True), `ERA5InputError` (retryable=False), `ERA5UpstreamError` (retryable=True), `ERA5MissingKeyError` (retryable=False), `ERA5AuthError` (retryable=False), `ERA5EmptyError` (retryable=False).
  - Six allowed variables enumerated as a `frozenset` (10m_u, 10m_v, 2m_temperature, total_precipitation, runoff, significant_height_of_combined_wind_waves_and_swell); the variable validator rejects anything else with `ERA5InputError`.
  - bbox / date-range / variable validators (pure Python, no network).
  - `_build_cds_request()` emits CDS-shape dict with `area=[N,W,S,E]`, explicit year/month/day string lists, all 24 hourly slots, `format="netcdf"`, `product_type="reanalysis"`.
  - `_cds_retrieve_with_timeout()` wraps `cdsapi.Client.retrieve` in a `threading.Thread` watchdog with `_RETRIEVE_TIMEOUT_S=300` budget; timeout surfaces as `ERA5UpstreamError`; auth-flavored exception messages (401/403/authentication/unauthorized) re-raise as `ERA5AuthError`.
  - `_netcdf_to_cog_bytes()` opens the returned NetCDF with `xarray`, picks the data variable (long-name match preferred, fallback to first non-coord var), reduces non-spatial dims via `mean(skipna=True)`, normalizes longitudes from ERA5's 0..360 to -180..180 when present, sorts latitudes ascending, clips to the requested bbox via `rioxarray.clip_box` (geographic-correctness gate), writes COG via `rioxarray.to_raster(driver="COG", dtype="float32", nodata=NaN, compress="DEFLATE")` with a GTiff fallback.
  - `estimate_payload_mb()` implements the audit-md spec (0.5 MB / variable / day / 1 deg square); covers `bbox=None` global case as 360 deg x 180 deg.
  - Four-path API-key resolution + sync-bridge to async `Persistence.get_secret_value` mirroring the `fetch_ebird_observations` pattern (lazy `Persistence` binding via `set_persistence_for_secrets`).
  - Returns `LayerURI(layer_type="raster", role="primary", units=<per-variable>, style_preset="era5_<variable>", uri=gs://...static-30d/era5/<key>.tif)`.

- **File: `services/agent/tests/test_fetch_era5_reanalysis.py` (NEW, ~460 lines)**
  - 23 unit tests + 1 env-gated live test.
  - Registration tests (FR-DC-6 consistency, Wave 1.5 flag verification).
  - Validation tests (bad bbox / lon / lat / variable / non-ISO dates / inverted range / over-cap range / typed-error retryable=False).
  - `_build_cds_request` shape test (verifies area=[N,W,S,E] convention, explicit y/m/d, 24 hourly slots).
  - `estimate_payload_mb` matches audit-md spec (1-day 1-deg -> ~0.5 MB; 2-day 4-deg -> ~4 MB; global -> >1000 MB).
  - API-key resolution priority tests (explicit > secret_ref > env var > None for `~/.cdsapirc` fallback).
  - Mocked happy-path CDS roundtrip using a fake `cdsapi` module + synthetic ERA5-shaped NetCDF writer (24-hour, 0.25-deg-grid xarray Dataset) -> COG bytes (TIFF magic verified) -> fake-GCS-shim path `cache/static-30d/era5/<key>.tif`.
  - FR-DC-3 cache-key separation: `total_precipitation` and `runoff` produce distinct cache URIs.
  - FR-DC-4 dedup: second identical call returns the cached URI without re-invoking the fake cdsapi.
  - Failure-path tests: generic upstream exception -> `ERA5UpstreamError` (retryable); 401-flavored exception -> `ERA5AuthError` (retryable=False); no artifact written on failure.
  - `LayerURI` shape test verifies `layer_type=raster`, `role=primary`, `units="K"` for 2m_temperature.
  - Live test (`test_live_fort_myers_total_precipitation`) gated by `GRACE2_TEST_LIVE_ERA5=1`, hits the kickoff-specified `(-82,26,-81,27)` x `total_precipitation` x `2024-09-26`, asserts CRS-tagged COG bounds intersect bbox, writes evidence to `evidence/era5_live.txt`.

- **File: `services/agent/src/grace2_agent/tools/__init__.py` (1-line append)**
  - Eager submodule import of `fetch_era5_reanalysis`.

- **File: `services/agent/src/grace2_agent/main.py` (1-line append)**
  - Eager `from .tools import fetch_era5_reanalysis` in `_import_tools_registry()`.

- **File: `services/agent/pyproject.toml`**
  - Added `cdsapi>=0.7,<1` dependency with rationale comment (Tier-2 Copernicus CDS client; secret resolution via job-0124 path).

- **File: `reports/inflight/job-0131-engine-20260608/evidence/`** (NEW dir)
  - `pytest_output.txt` — captured pytest output (23 passed, 1 skipped).
  - `registry_check.txt` — `main._import_tools_registry()` returns 55, era5 entry confirmed with the Wave 1.5 flags set.
  - `era5_live.txt` — live-test status: QUALIFIED (no CDS key in sandbox env per AGENTS.md "Live E2E validation"); documents how to run the live test on a machine with `~/.cdsapirc`, what the live test asserts, and explicitly cites the mocked CDS-roundtrip evidence the unit suite provides (`test_mocked_happy_path_total_precipitation` writes a real COG to the fake GCS path).

## Decisions Made

- **Decision: Time-mean across the date-range window, one band per call.**
  - Rationale: ERA5 hourly data over a multi-day window produces ~24 timesteps x n_days. Single-band time-mean is the canonical compound-flood substrate representation (the NHESS 2023 paper consumes mean wind / mean precip / mean significant wave height as boundary forcing). Composers that want per-timestep multi-band output can call this tool multiple times with single-day windows.
  - Alternatives considered: multi-band (one per timestep) — rejected because COG band counts above ~24 become awkward, and SFINCS / HydroMT consumers typically already expect time-averaged forcing surfaces.

- **Decision: cdsapi wrapped in a 5-minute watchdog rather than a native cdsapi timeout.**
  - Rationale: cdsapi exposes a `timeout` constructor kwarg but it controls the HTTP transport timeout for each individual poll, NOT the wall-clock budget for the full queue -> run -> stream cycle. The kickoff says "poll up to 5 min for completion"; the watchdog matches that semantics. Surfaced as `OQ-0131-CDS-ORPHAN-JOB`: a timed-out request leaves an orphan job server-side.
  - Alternatives considered: using cdsapi's `wait_until_complete=False` API to manually poll — rejected because the manual polling API is undocumented across cdsapi releases and breaks between 0.6.x / 0.7.x.

- **Decision: longitude normalization 0..360 -> -180..180 inside `_netcdf_to_cog_bytes`.**
  - Rationale: ERA5 ships some variables on 0..360 longitudes (legacy convention) and others on -180..180; `rio.clip_box` with the user's GeoJSON-convention bbox would otherwise return an empty raster for a Pacific bbox. We detect-and-flip before clipping.
  - Alternatives considered: requiring callers to pre-rotate their bbox — rejected as a foot-gun (Decision K, FR-AS-12 minimal parameter surface).

- **Decision: provider key for `secret_ref` is treated as a forward-compat shape (no `copernicus_cds` member in `ProviderID` yet).**
  - Rationale: `grace2_contracts.secrets.ProviderID` is a closed Literal landed in Wave 1 (job-0100) with Tier-2 conservation + weather + LLM + basemap providers; `copernicus_cds` is NOT a member. We accept `secret_ref: Any` and document the gap in the report — surfaced as `OQ-0131-PROVIDER-COPERNICUS-CDS`.
  - Alternatives considered: pushing back to schema (consumer-pushback motion per AGENTS.md) — rationale to defer: the tool needs to land in Wave 2 for the compound-flood composers; the schema amendment can land additively without breaking this tool's signature.

## Invariants Touched

- **1. Determinism boundary**: extends — the tool returns a structured `LayerURI` with typed fields (`layer_type`, `role`, `units`, `uri`); no prose-number returns. The COG itself carries `units` tags so downstream narration cites typed metadata, not LLM-generated numbers.
- **2. Deterministic workflows**: preserves — this is an atomic tool, not a workflow; no LLM call in the loop. Composers that consume it can compose with zero LLM round-trips.
- **3. Engine registration, not modification**: preserves — added via `@register_tool` decorator; no agent-core or contract surgery.
- **6. Metadata-payload pattern**: preserves — COG bytes go to the FR-DC cache bucket via `read_through`; URI is returned in the typed result.
- **7. Claims carry provenance**: preserves — the COG carries `source="ERA5_reanalysis-era5-single-levels"`, `variable`, `tool="fetch_era5_reanalysis"` tags so any downstream narration (mean wind, mean precip) traces to ERA5 provenance.
- **10. Minimal parameter surface**: preserves — five params (bbox, variable, start_date, end_date, plus the Tier-2 secret pair). No CRS / resolution / Manning's / wave parameters required; ERA5 is global at 0.25 deg native res.

## Open Questions

- **OQ-0131-PROVIDER-COPERNICUS-CDS** — `grace2_contracts.secrets.ProviderID` (closed Literal landed by Wave 1 job-0100) currently lists Tier-2 conservation + weather + LLM + basemap providers but NOT `copernicus_cds`. Adding `copernicus_cds` requires an SRS Appendix F.3 amendment + schema bump. Proposed resolution: orchestrator routes a follow-up `schema` job to extend `ProviderID` with `"copernicus_cds"`; until then this tool accepts `secret_ref: Any` and the production secret resolution path (`Persistence.get_secret_value` with a `SecretRecord(provider="copernicus_cds")`) cannot be exercised end-to-end. Mitigation: explicit `api_key` kwarg + `GRACE2_COPERNICUS_CDS_API_KEY` env var + `~/.cdsapirc` fallback paths all work without the provider being in the closed Literal.
- **OQ-0131-CDS-ORPHAN-JOB** — when our wall-clock watchdog fires, the CDS job is still queued server-side; the cdsapi client has no cancel endpoint. The user will see the orphan in their CDS dashboard. Proposed resolution: add a "cancel orphan" pass-through tool (`cancel_cds_job(request_id)`) in a follow-up if the orphan rate becomes operationally noisy; for v0.1 the 5-minute budget should be large enough that legitimate retrievals complete inside it.
- **OQ-0131-TIME-AGGREGATION** — the tool reduces all non-spatial dims via `mean(skipna=True)` to produce a single-band COG. For compound-flood composers that need per-hour boundary forcing (SFINCS boundary timeseries), this aggregation is destructive. Tentative resolution: keep single-band as the v0.1 surface (matches the kickoff's "GeoTIFF" output), and surface a `multi_band: bool = False` opt-in in a follow-up if a real composer asks. Documented in the docstring "Returns" section.
- **OQ-0131-EXPVER-MERGE** — ERA5T preliminary data carries an `expver` axis (1 = ERA5, 5 = ERA5T) that we mean-collapse without flagging. For dates within ~3 months of "now" this can produce a half-ERA5T result. Proposed resolution: surface the `expver` membership in the LayerURI provenance and let the composer decide (out of scope for v0.1).

## Dependencies and Impacts

- **Depends on:**
  - job-0124 (`Persistence.get_secret_value`) for the secret_ref resolution path — landed.
  - job-0032 (`AtomicToolMetadata` + `register_tool` + `read_through`) — landed.
  - job-0114 (Wave 1.5 metadata flags: `supports_global_query`, `payload_mb_estimator_name`) — landed.
  - job-0031 cache bucket `gs://grace-2-hazard-prod-cache` with the `cache/<ttl>/<source>/<key>.<ext>` layout — landed.
- **Affects:**
  - Future compound-flood composer (forward-looking; sprint-13+) — consumes the `LayerURI` to drive SFINCS / HydroMT boundary forcing.
  - schema follow-up — `ProviderID` Literal needs `copernicus_cds` for the secret_ref end-to-end path (OQ-0131-PROVIDER-COPERNICUS-CDS).
  - infra follow-up — when the deployed agent image is rebuilt, `cdsapi` will be pulled into the wheel cache; the production Cloud Run image's egress allowlist must include `cds.climate.copernicus.eu`.

## Verification

- **Tests run:**
  - `.venv-agent/bin/python -m pytest services/agent/tests/test_fetch_era5_reanalysis.py -v` -> **23 passed, 1 skipped** (live test gated on `GRACE2_TEST_LIVE_ERA5=1`).
  - Full agent suite: `.venv-agent/bin/python -m pytest services/agent/tests/ -x -q` -> **981 passed, 46 skipped** (post-landing, sanity).
  - Tool-registry startup check: `main._import_tools_registry()` returns 55 (vs 54 pre-job), `TOOL_REGISTRY["fetch_era5_reanalysis"]` carries the four expected metadata fields.

- **Live E2E evidence (QUALIFIED per AGENTS.md):**
  - The kickoff specifies an env-gated live test against the real Copernicus CDS. No CDS API key is available in this sandbox (no `~/.cdsapirc`, no `GRACE2_COPERNICUS_CDS_API_KEY`), and the kickoff explicitly mandates the unit suite NOT fail if the key is missing.
  - The live test (`test_live_fort_myers_total_precipitation`) is wired with the kickoff-exact args `(-82,26,-81,27) x total_precipitation x 2024-09-26 -> 2024-09-26` and asserts CRS-tagged COG bounds intersect bbox + >=1 finite pixel + writes evidence to `evidence/era5_live.txt`.
  - The mocked CDS roundtrip (`test_mocked_happy_path_total_precipitation`) provides the closest available evidence: it runs the entire pipeline end-to-end with a fake `cdsapi.Client.retrieve` that writes a synthetic 0.25-deg-grid ERA5-shaped NetCDF; the tool successfully converts that to a CRS-tagged COG and writes it through the FR-DC cache shim to the expected `gs://grace-2-hazard-prod-cache/cache/static-30d/era5/<key>.tif` path. The output bytes are TIFF-magic-verified.
  - Evidence captured to `reports/inflight/job-0131-engine-20260608/evidence/`: `pytest_output.txt`, `registry_check.txt`, `era5_live.txt` (qualified status + reproduction command).

- **Results:** **qualified** (unit suite + mocked end-to-end fully passes; live CDS verification deferred to a machine with a CDS key per kickoff env-gate discipline).

- **Tool count:** 55 tools registered (vs 54 pre-job). `fetch_era5_reanalysis` confirmed via `TOOL_REGISTRY["fetch_era5_reanalysis"]` with `ttl_class="static-30d"`, `source_class="era5"`, `cacheable=True`, `supports_global_query=True`, `payload_mb_estimator_name="estimate_payload_mb"`.

- **Concrete numbers:**
  - **23 unit tests** + **1 env-gated live test**.
  - **6 ERA5 variables** supported (10m_u_component_of_wind, 10m_v_component_of_wind, 2m_temperature, total_precipitation, runoff, significant_height_of_combined_wind_waves_and_swell).
  - **5-minute** wall-clock watchdog on CDS retrieve.
  - **366-day** hard cap on date range per call.
  - **0.25 deg** native ERA5 resolution.
  - Payload estimator: **0.5 MB / variable / day / 1 deg square** per audit-md spec (verified by `test_estimate_payload_mb_matches_audit_md_spec`).
  - Cache path: `gs://grace-2-hazard-prod-cache/cache/static-30d/era5/<32-hex-key>.tif`.
