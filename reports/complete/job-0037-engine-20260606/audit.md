# Audit: WorldPop default flip in `fetch_population` (Appendix F.1 mini early job)

**Job ID:** job-0037-engine-20260606, **Sprint:** sprint-07, **Auditor:** Development Orchestrator, **Status:** approved

## Task Assignment

**Specialist:** engine

**Prerequisites:**
- **job-0033-engine-20260606 (APPROVED)**: provides the existing `fetch_population(bbox, dataset="acs_2022")` implementation. **Read the report end-to-end** to absorb the existing ACS code path, the per-source bbox quantization grid (100m for population), the `LayerURI` return shape, and the cache-shim integration pattern.
- **v0.3.16 SRS amendment** (commit `38a7a28`): provides the Appendix F.1 Tier-1 default rule. WorldPop becomes the Tier-1 default; ACS becomes Tier-2 opt-in via `dataset="acs_2022"`.

**SRS references** (narrow files only):
- `docs/srs/F-data-sources-discovery-secrets.md` — §F.1 Tier-1-preference rule; WorldPop is the new default for `fetch_population` per v0.3.16.
- `docs/srs/03-functional-requirements.md` — FR-TA-2 atomic-tool surface; FR-AS-3 / FR-TA-3 docstring discipline.
- DO NOT load `docs/SRS_v0.3.md` monolith.

### Environment
WorldPop data is published as raster COGs through a STAC catalog. The most accessible STAC endpoint is the WorldPop Hub at `https://hub.worldpop.org/` or the Microsoft Planetary Computer STAC at `https://planetarycomputer.microsoft.com/api/stac/v1/collections/worldpop-100m`. Either works. The 100m-resolution global gridded population dataset (`ppp_2020_100m_Binary` or year-stamped equivalent) is the closest match to the M4 demo's tract-level ACS query.

### Scope

1. **Edit `services/agent/src/grace2_agent/tools/data_fetch.py`** — the `fetch_population` tool currently defaults to `dataset="acs_2022"` with WorldPop as a noted alternative. Flip the default:
   - New default: `dataset="worldpop_2020"` (or whatever vintage matches the STAC endpoint at fetch time)
   - ACS opt-in: `dataset="acs_2022"` — preserved as before, but no longer the default
   - Both still register under the same `AtomicToolMetadata` (TTL `static-30d`, source_class `population`)
   - WorldPop branch writes a COG to `gs://grace-2-hazard-prod-cache/cache/static-30d/population/<hash>.tif`
   - ACS branch keeps writing to `<hash>.json` per the existing pattern
   - Per-source bbox quantization stays at 100m (matches WorldPop native resolution; preserves dedup guarantee per OQ-32-QUANTIZATION-LOCATION)

2. **Docstring update** — FR-AS-3 / FR-TA-3 metadata discipline: name the Tier-1 default (WorldPop) and list the Tier-2 alternative (`dataset="acs_2022"` requires Census API key). "Use this when / Do NOT use this for" remains; add a "Default behavior" line.

3. **Tests** in `services/agent/tests/test_data_fetch.py` — extend (NOT replace) the existing `fetch_population` tests:
   - At least 2 new WorldPop branch tests (happy path with stubbed STAC; cache write to `.tif` path)
   - At least 1 test asserting the default route (when called without `dataset=`) now routes to WorldPop, NOT to ACS
   - Preserve existing ACS tests (they exercise the opt-in path)

4. **Live re-run the Fort Myers demo with WorldPop** — invoke `fetch_population(bbox=fort_myers_bbox)` without `dataset=` arg; verify the cache write lands at `gs://grace-2-hazard-prod-cache/cache/static-30d/population/<hash>.tif` with `customTime` set. Capture under `evidence/`.

5. **Carry-forward note in the report:** OQ-36-CENSUS-API-KEY-REQUIRED can NOT be fully closed by this job — Census ACS still needs a key when the user opts into it. This job CLOSES the no-key Tier-1 default path (M4 demo unblocks); the Tier-2 opt-in path remains gated on user-provided Census key (orchestrator-direct one-shot at user's convenience, NOT a job).

### File ownership (exclusive)

- `services/agent/src/grace2_agent/tools/data_fetch.py` — `fetch_population` only (do not touch `fetch_dem`, `fetch_buildings`, `geocode_location`, or the module's import block beyond what WorldPop needs)
- `services/agent/tests/test_data_fetch.py` — additive
- `services/agent/pyproject.toml` — add `pystac-client` if not already there from M4
- `reports/inflight/job-0037-engine-20260606/` — kickoff frozen

### FROZEN — no edits in this job

- All other `services/agent/src/grace2_agent/tools/*.py` files (cache.py, passthroughs.py, qgis_discovery.py, __init__.py, README.md)
- `services/agent/src/grace2_agent/{main,server,mcp,pipeline_emitter}.py`
- `packages/contracts/**`, `infra/**`, `web/**`, `docs/srs/**`, `docs/SRS_v0.3.md`, `styles/**`, `services/workers/**`, `reports/complete/**`
- Stage A concurrent jobs: do not edit anything job-0038 or job-0040 owns (HydroMT decision doc + infra/sfincs.tf)

### Cross-cutting principles in force

- **Invariant 1 (Determinism boundary):** preserves.
- **Invariant 5 (Tier separation):** preserves — WorldPop COG lands in cache bucket via agent-runtime SA.
- **FR-CE-8 fail-fast registration:** unchanged — `AtomicToolMetadata` shape didn't change.
- **FR-DC-4 dedup:** preserved via 100m bbox quantization.
- **Diagnose before fix:** if STAC integration fails, capture the request/response before adjusting.
- **Bundle small fixes:** trivial cleanup OK; do NOT use this as cover to address other OQ-33-*.

### Acceptance criteria (reviewer re-runs)

- [ ] `fetch_population(bbox)` (no `dataset` arg) routes to WorldPop branch; writes `.tif` COG to cache bucket.
- [ ] `fetch_population(bbox, dataset="acs_2022")` still routes to ACS (Tier-2 opt-in preserved).
- [ ] Tool docstring names WorldPop as Tier-1 default + ACS as Tier-2 opt-in per FR-AS-3.
- [ ] At least 3 new tests; full agent suite green; contracts 131/131; M3 + M4 still green.
- [ ] Live Fort Myers WorldPop fetch captured under `evidence/` with `gcloud storage describe` showing the new COG + `customTime`.
- [ ] No edits to FROZEN paths.

Surface contestable choices as Open Questions with TENTATIVE tags — at minimum: WorldPop STAC endpoint choice (WorldPop Hub vs MS Planetary Computer); vintage year selection (2020 vs latest available); whether to expose a `vintage_year` parameter or hard-code the latest.

## Assessment

**Verdict:** approved (with two honest architectural deviations from the kickoff, correctly routed as Open Questions).

WorldPop is now the `fetch_population` Tier-1 default per Appendix F.1 — call without `dataset=` routes to `worldpop_2020`. ACS opt-in via `dataset="acs_2022"` preserved. Tool docstrings updated to name the Tier-1 default + Tier-2 alternative per FR-AS-3 / FR-TA-3 discipline.

**Live verification — end-to-end on production GCS.** `fetch_population((-81.88, 26.62, -81.85, 26.65))` (Fort Myers) wrote a 2556-byte COG to `gs://grace-2-hazard-prod-cache/cache/static-30d/population/b195dff80ae34eb50d24ba0d4dc855f3.tif` with `customTime=2026-06-07T06:51:16.033982+00:00` as a datetime (OQ-33 hotfix still verifies). Cache-control header `public, max-age=2592000` (30 days) matches the static-30d TTL class. Wall-clock 78.7s — slow but expected for the country-download-then-windowed-clip pattern (see Decision 2 below).

72/72 agent tests green in 1.07s (was 70; +3 new WorldPop tests, −1 obsolete; +2 cumulative). Contracts still 131/131. `--startup-only` reports 8 tools — no registry drift.

**Two architectural deviations from the kickoff, honestly disclosed:**

1. **STAC endpoint doesn't exist (OQ-37-WORLDPOP-STAC-ENDPOINT).** Kickoff suggested Microsoft Planetary Computer's `worldpop-100m` collection OR WorldPop Hub STAC. **Neither exists.** MS Planetary Computer's 134 collections include zero with "worldpop"; the WorldPop Hub STAC at `hub.worldpop.org/stac/catalog.json` returns HTTP 404. Specialist correctly pivoted to direct WorldPop REST + GeoTIFF download. This is the right "diagnose before fix" move — verify the substrate before building against it.

2. **100m product can't be windowed (OQ-37-WORLDPOP-RESOLUTION-VS-RANGE).** WorldPop's HTTP server returns HTTP 200 with the full response body for HTTP Range requests (instead of HTTP 206 Partial Content). GDAL `/vsicurl/` therefore can't windowed-read the 4 GB 100m product. Specialist substituted the **50 MB 1km Aggregated product** as a practical compromise. This is a real resolution downgrade (100m → 1km) that affects precision for fine-scale demos but is acceptable for the Fort Myers M5 demo target. Revisit when a range-capable mirror or a native STAC catalog appears.

These are not failures of execution — they're discoveries about what's actually available in the upstream public-data ecosystem. The kickoff was written based on what the SRS amendment (v0.3.16 Appendix F) listed; the specialist's live verification caught that the listed sources don't deliver as described. This is exactly the live-evidence discipline that caught OQ-33 in sprint-06. The audit accepts both deviations + their resolutions.

OQ-36-CENSUS-API-KEY-REQUIRED is **NOT closed** by this job (correctly noted). The no-key Tier-1 path is now operational (this job's deliverable); the Tier-2 ACS opt-in still requires the operator-provisioned Census key to function. That's a future orchestrator-direct one-shot.

## Invariant Check

- **Invariant 1 (Determinism boundary):** preserved. Country download + windowed clip is deterministic; same bbox + same vintage → same cache key (verified by the 100m bbox quantization, OQ-32-QUANTIZATION-LOCATION).
- **Invariant 5 (Tier separation):** preserved. COG lands in cache bucket via agent-runtime SA; no `gs://` leaks to client.
- **Invariant 8 (Cancellation is first-class):** preserved. The 78.7s country download is interruptible via the M1 cancel chain through the WS layer; the COG write is atomic at the GCS level.
- **FR-CE-8 fail-fast registration:** preserved. `AtomicToolMetadata` shape unchanged; registry validates at import.
- **FR-DC-4 dedup:** preserved via 100m bbox quantization (specialist held the quantization grid from job-0033).

## Dependency Check

- **job-0033 (M4 fetch_population)** — extended additively; ACS path preserved.
- **v0.3.16 Appendix F.1** — Tier-1 preference rule honored; WorldPop is now the default; ACS is opt-in per `dataset="acs_2022"`.
- **OQ-33 hotfix (commit ca48256)** — still in effect; `customTime` is datetime on the live write. Re-verifies the regression test holds.

## Decisions Validated

All decisions reviewed and accepted:

1. **WorldPop REST + direct GeoTIFF download** (no STAC) — pragmatic fallback after live verification of the kickoff's suggested STAC endpoints. The substrate works; the STAC future-fork is captured as OQ-37-WORLDPOP-STAC-ENDPOINT.
2. **1km Aggregated product** instead of 100m — necessary because WorldPop's HTTP server doesn't honor Range requests. Resolution-vs-fetch-cost tradeoff resolved in favor of fetch cost; precision-sensitive use cases can opt into a different dataset (or 100m via a future range-capable mirror).
3. **Vintage exposed via dataset string suffix** (`worldpop_<YEAR>`) rather than a separate `vintage_year` parameter — preserves minimal parameter surface; matches the job-0033 `acs_2022` convention. Accepted.
4. **`pystac-client>=0.8,<1`** added to pyproject as a forward dep for job-0039 (NHDPlus HR / NLCD may use STAC where WorldPop didn't). Reasonable lookahead.

## Open Questions Resolved

Filed for triage:

- **OQ-37-WORLDPOP-STAC-ENDPOINT** — neither MS Planetary Computer nor WorldPop Hub STAC are available. Revisit when an alternative materializes. Non-blocking for M5.
- **OQ-37-WORLDPOP-RESOLUTION-VS-RANGE** — 1km Aggregated as current substrate; 100m needs a range-capable mirror. Track upstream; revisit at M9+ precision pass.
- **OQ-37-VINTAGE-YEAR** — 2020 hardwired (R2018A/R2020 trees); R2024B/R2025A trees exist. Bump follow-up when downstream data quality justifies.
- **OQ-37-ISO3-ROUTING-HEURISTIC** — current 9-country bbox envelope; Natural Earth PIP would be more robust. Non-blocking for M5 demo.
- **OQ-37-COUNTRY-FILE-CACHING-STRATEGY** — current per-fetch 50 MB country download is suboptimal; two-stage cache (full country file cached separately, then windowed clips) would amortize. Performance follow-up.
- **OQ-37-WORLDPOP-COG-CRS-AND-UNITS** — units = "people per 1km cell" — the LayerURI says `units: people`. Should clarify ("people-per-cell" semantics matter for zonalstatistics aggregation). Bundle into v0.3.17 housekeeping.
- **OQ-37-PYSTAC-CLIENT-LOOKAHEAD** — added pystac-client as a forward dep for job-0039. Confirm at job-0039 time.

Still open:
- **OQ-36-CENSUS-API-KEY-REQUIRED** explicitly NOT closed. Tier-2 ACS opt-in still requires the operator-provisioned Census key. Orchestrator-direct one-shot when user provides the key.

## Follow-up Actions

1. **Unblock job-0039 (3 new fetcher tools)** — Stage A two of three approved; only job-0040 (SFINCS infra) remains in flight. Stage B scaffolds when 0040 closes.
2. **v0.3.17 SRS housekeeping pass** — bundle the "people per 1km cell" semantics clarification (OQ-37-WORLDPOP-COG-CRS-AND-UNITS) and the WorldPop entry in §F.1 prose noting "1km Aggregated product, R2018A/R2020 vintage by default".
3. **OQ-37-COUNTRY-FILE-CACHING-STRATEGY** — performance follow-up. Current per-fetch 50 MB download is fine for M5 demo but worth optimizing at M9+ polish.
4. **Style preset for population layers** — currently inherits `continuous_dem` from job-0033's preset (a DEM preset; works visually for any continuous raster but semantically wrong). Bundle into M5+ visual polish / job-0033's prior OQ-33-POPULATION-QML-PRESET follow-up.

## Sign-off

**Approved 2026-06-06 by Development Orchestrator.**

All 6 acceptance criteria met. Live Fort Myers WorldPop fetch verified end-to-end on production GCS. 72/72 agent tests green. STAC-endpoint pivot + 100m-can't-be-windowed deviations are honest discoveries about the upstream ecosystem, not execution failures. Substrate works for the M5 demo target; precision/performance follow-ups routed.

Sprint-07 Stage A two of three complete. Job-0040 (SFINCS infra) remains in flight.
