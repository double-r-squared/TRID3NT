# Audit: WorldPop default flip in `fetch_population` (Appendix F.1 mini early job)

**Job ID:** job-0037-engine-20260606, **Sprint:** sprint-07, **Auditor:** Development Orchestrator, **Status:** assigned

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
