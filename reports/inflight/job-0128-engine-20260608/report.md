# Report: fetch_ebird_observations atomic tool

**Job ID:** job-0128-engine-20260608
**Sprint:** sprint-12-mega Wave 2
**Specialist:** engine
**Task:** Cornell Lab eBird recent observations Tier-2 fetcher with bbox tiling, subId dedup, three-path API-key resolution, FlatGeobuf output.
**Status:** ready-for-audit

## Summary

Landed `fetch_ebird_observations` — a Cornell Lab eBird Tier-2 fetcher that returns recent species sightings clipped to a bbox as a CRS-tagged FlatGeobuf. Implements the per-Case secret-resolution priority chain (explicit `api_key` kwarg > `secret_ref` via `Persistence.get_secret_value` > `GRACE2_EBIRD_API_KEY` env var > `EBirdMissingKeyError` pre-network), bbox tile-cover (50 km radius circles, dedup by `subId`), full FR-AS-11 typed-error surface (Input / Auth / MissingKey / Upstream), and the FR-DC-3 `dynamic-1h` cache class with a cache key that intentionally omits the `api_key` so callers share entries.

## Changes Made

- File: `services/agent/src/grace2_agent/tools/fetch_ebird_observations.py` (NEW, ~620 lines)
  - `fetch_ebird_observations(species_code, bbox, days_back=30, api_key=None, secret_ref=None) -> LayerURI`
  - Tile-cover bbox → list of `(lat, lng)` circle centers (50 km radius); hard cap 200 tiles so continent-scale bboxes raise `EBirdInputError` rather than burning quota.
  - Per-tile fetch via real `httpx.Client` against `https://api.ebird.org/v2/data/obs/geo/recent/{species_code}` with the `X-eBirdApiToken` header; dedup by eBird `subId` across tiles.
  - FlatGeobuf serialization with documented schema (`subId`, `obsDt`, `locName`, `howMany`, `comName`, `sciName`, `speciesCode`, geometry `Point` EPSG:4326).
  - Geographic-correctness gate (job-0086 codified lesson): every emitted point hard-filtered to lie inside the requested bbox before serialization.
  - Three-path key resolution (`_resolve_api_key`): kwarg > `secret_ref` via `Persistence.get_secret_value` > env var > `EBirdMissingKeyError` raised pre-network.
  - `set_persistence_for_secrets(persistence)` hook for the agent service to bind `Persistence` once at startup; tests inject a mock.
  - Sync-from-async bridge `_run_coro_sync` handles both `asyncio.run` (test/CLI) and the running-loop case (worker thread with its own loop).
  - FR-AS-11 typed errors: `EBirdError` (base), `EBirdInputError` (retryable=False), `EBirdUpstreamError` (retryable=True), `EBirdAuthError` (retryable=False), `EBirdMissingKeyError` (retryable=False); each carries `error_code` for A.6 mapping.
  - Cache: `ttl_class="dynamic-1h"`; cache key SHA-256 of `(species_code, bbox-6dp, days_back)` — api_key intentionally omitted.

- File: `services/agent/src/grace2_agent/tools/__init__.py` (1-line idempotent append)
- File: `services/agent/src/grace2_agent/main.py` (1-line idempotent append)
- File: `services/agent/tests/test_fetch_ebird_observations.py` (NEW, ~640 lines) — 38 unit tests + 1 live env-gated test.

## Decisions Made

- **Cache key omits api_key** — observations don't vary by caller; keying by api_key would defeat the cache. Tested in `test_cache_key_omits_api_key`.
- **Pre-network MissingKey fail-fast** — a tool call landing without any key surfaces as a typed envelope the agent surface routes to the secrets panel; no wasted network round-trip.
- **Tile-cover via row/col grid (sprint-13 → hex)** — audit.md explicitly defers hex packing; v0.1 grid overlaps deliberately so we don't miss slivers.
- **bbox hard-filter post-fetch** — corner-tile circles overlap bbox edges by design; geographic-correctness gate (job-0086) requires the contract to be clean.
- **secret_ref accepts both SecretRecord and str shortcut** — `str` is a test-only convenience for injecting a known key value without standing up Persistence.
- **Sync/async bridge via worker-thread loop** — `read_through` is sync but `Persistence.get_secret_value` is async; worker-thread fallback handles the running-loop case.

## Invariants Touched

- **1. Determinism boundary**: preserves — typed `LayerURI` return; per-feature properties are the deterministic fields the agent narrates from.
- **3. Engine registration, not modification**: preserves — added via `@register_tool`, no agent-core edits.
- **4. Rendering through QGIS Server**: preserves — emits FlatGeobuf at `gs://...cache/dynamic-1h/ebird/<key>.fgb`; CRS=EPSG:4326 tagged authoritatively.
- **5. Tier separation**: preserves — Tier-2 secret path resolves the per-Case key without leaking into the cache substrate.
- **8. Cancellation is first-class**: preserves — `read_through` is sync I/O cancellable via `asyncio.CancelledError`; `_run_coro_sync` worker thread is a daemon.
- **9. Confirmation before consequence**: not triggered — read-only fetch; no runs write, no solver.
- **10. Minimal parameter surface**: preserves — `species_code`, `bbox`, `days_back` are the only irreducible inputs.

## Open Questions

- **OQ-0128-EBIRD-TAXON-CODE-DRIFT**: should we maintain a curated common-name → species-code map (analogous to `_species_reference.py` for GBIF)? Tentative: defer until sprint-13.
- **OQ-0128-EBIRD-TILE-PACKING**: v0.1 row/col grid wastes ~30% of API calls vs hex packing. Audit.md defers; no action this job.
- **OQ-0128-EBIRD-DAYS-BACK-DEFAULT**: defaulted to 30 (audit.md max); a shorter default (7) would consume less quota. Tentative: keep 30 — dynamic-1h cache amortizes.
- **OQ-0128-EBIRD-SYNC-BRIDGE-DURABILITY**: `_run_coro_sync` worker-thread is tactical; a larger refactor moving `read_through` to async would eliminate the bridge. Surfacing for the broader sync/async-substrate question.

## Dependencies and Impacts

- Depends on: job-0100 (`SecretRecord` schema), job-0115 (`Persistence` skeleton), job-0124 (`Persistence.get_secret_value`), job-0087 (`fetch_gbif_occurrences` pattern), FR-DC-3 cache shim (job-0032).
- Affects: `agent` payload-mb estimator (no entry yet — uses default), `web` Tier-2 unlock badge (job-0125 surfaces eBird once a `SecretRecord` for `provider="ebird"` is persisted), `testing` (live test needs `GRACE2_EBIRD_API_KEY` in CI for sprint-12 capstone).

## Verification

### Tests run

- `pytest services/agent/tests/test_fetch_ebird_observations.py -x -q` → **38 passed, 1 skipped** (skipped = live env-gated Bewick's Wren over CA bbox).
- `pytest services/agent/tests/ -q` → **920 passed, 44 skipped, 0 failures** in 448s.
- Tool import + registration via `main.py._import_tools_registry()` → **55 tools** registered; `fetch_ebird_observations` present with `ttl_class="dynamic-1h"`, `source_class="ebird"`, `cacheable=True`, `supports_global_query=False`.

### Live E2E evidence

The full happy-path live test requires a registered eBird API key (not provisioned in this env). As alternative live verification, exercised the real eBird API with a dummy key:

    GRACE2_EBIRD_API_KEY=INVALID-DUMMY-KEY-FOR-AUTH-PATH-TEST \
        .venv-agent/bin/python -c "
            from grace2_agent.tools.fetch_ebird_observations import fetch_ebird_observations
            fetch_ebird_observations(species_code='bewwre',
                                     bbox=(-122.4, 38.0, -122.0, 38.4),
                                     days_back=7)
        "

→ **Real network call hit api.ebird.org, returned HTTP 403; tool mapped to `EBirdAuthError(error_code="EBIRD_AUTH_ERROR", retryable=False)`.** The FR-AS-11 typed-error surface is live.

Evidence: `reports/inflight/job-0128-engine-20260608/evidence/ebird_live.txt`

The unit-suite live path (`test_live_bewickwren_over_ca_bbox`, gated by `GRACE2_TEST_LIVE_EBIRD=1` + `GRACE2_EBIRD_API_KEY`) writes evidence to `services/agent/evidence/ebird_live_job_0128.txt` when a real key is present. The test is `pytest.mark.skipif`-guarded per audit.md so it does NOT fail the unit suite when the key is missing.

### Results

**Pass** — qualified on the full happy-path live test pending eBird API key registration. All other live evidence (real eBird API auth-rejection path, real network round-trip, typed-error envelope) in hand. No regressions to the broader agent test suite.
