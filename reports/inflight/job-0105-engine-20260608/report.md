# Report: `fetch_nws_alerts_conus` atomic tool — NWS CONUS-wide active-alerts companion to `fetch_nws_event`

**Job ID:** job-0105-engine-20260608
**Sprint:** sprint-12-mega Wave 1.5
**Specialist:** engine
**Task:** Add NEW atomic tool `fetch_nws_alerts_conus(event_types, status)` that fetches ALL active CONUS NWS alerts in a single api.weather.gov call (companion to `fetch_nws_event(area=...)` from job-0090).
**Status:** ready-for-audit

## Summary

Added `fetch_nws_alerts_conus(event_types=None, status='actual') -> LayerURI`, an atomic tool that issues a single CONUS-wide GET against `https://api.weather.gov/alerts/active?status={status}` (no `area`/`point` filter), client-side filters by `event_types`, and writes a FlatGeobuf of all current alert polygons + properties through the FR-DC-3 `read_through` cache shim (ttl_class="dynamic-1h", source_class="nws_alerts_conus"). Live verification against the real api.weather.gov returned 264 active alerts CONUS-wide on 2026-06-08; the geographic-correctness gate (job-0086 lesson) passes with 59 polygon-bearing features all centroid-inside the US envelope (-180..-64 lon, 13..72 lat) and 0 outside. All 21 unit + 2 live tests pass. Tool count rose from 25 to 26 (eager `tools/__init__.py` import); `_import_tools_registry()` (main.py startup) registers 41 tools cleanly.

## Changes Made

- **NEW** `services/agent/src/grace2_agent/tools/fetch_nws_alerts_conus.py`
  - `fetch_nws_alerts_conus(event_types: list[str] | None = None, status: str = 'actual') -> LayerURI`
  - Endpoint: `https://api.weather.gov/alerts/active?status={status}` (no area param — CONUS-wide variant).
  - Client-side `event_types` filter applied AFTER fetch.
  - Required `User-Agent: grace2-agent/0.1 (...; contact: grace2-ops@local)` per NWS policy.
  - Returns `LayerURI(layer_type="vector", role="primary", units=None, style_preset="nws_alerts")`.
  - Typed errors: `NWSConusError` (base) / `NWSConusInputError(retryable=False)` / `NWSConusUpstreamError(retryable=True)` / `NWSConusEmptyError`.
  - FlatGeobuf converter preserves 19 NWS property fields; nested objects coerced to JSON strings.
  - **Pyogrio NULL-geometry fix**: when any feature has null geometry, write FGB with `SPATIAL_INDEX="NO"` (pyogrio rejects null geom on the indexed path). Preserves the 80%-of-real-world zone-only-reference rows.
- **NEW** `services/agent/tests/test_fetch_nws_alerts_conus.py` (21 unit tests + 2 live tests, env-guarded by `GRACE2_TEST_LIVE_NWS_CONUS=1`)
  - Registration metadata, URL builder (status only, no area/point), client-side filter, User-Agent header verification, mocked 50-feature CONUS response, input validation, 403/network → typed upstream errors, cache miss-then-hit dedup, event_types sort invariance, LayerURI shape, **geographic-correctness gate** (centroids in US envelope), empty FeatureCollection edge case, LIVE: real api.weather.gov returns >=0 features + geographic gate.
- **MODIFIED** `services/agent/src/grace2_agent/tools/__init__.py` — one-line idempotent eager import.
- **MODIFIED** `services/agent/src/grace2_agent/main.py` — one-line idempotent eager import in `_import_tools_registry`.

## Decisions Made

- **Decision:** Client-side `event_types` filter rather than NWS server-side `?event=` repeats.
  - **Rationale:** A future refactor could cache the RAW CONUS sweep (one fetch per hour services all filter variations). For Wave 1.5 the cache key still includes `event_types_sorted` because the cached artifact is the FILTERED FGB; the deferred refactor is logged as `OQ-0105-CACHE-RAW-VS-FILTERED`.
  - **Alternatives:** Server-side `?event=` repeats (matches `fetch_nws_event`'s pattern) — rejected to preserve the raw-sweep cache optimization runway.
- **Decision:** `role="primary"` per kickoff spec; contrasted with `fetch_nws_event`'s `role="context"`.
  - **Rationale:** A CONUS-wide nationwide-warnings overlay IS the user's primary content layer for "show me warnings across America" prompts.
- **Decision:** Do NOT pass `supports_global_query=True` to `AtomicToolMetadata`.
  - **Rationale:** Wave 1.5 schema amendment job-0114 adds that field; not yet present in `grace2_contracts.tool_registry.AtomicToolMetadata`. Passing an unknown field crashes at import with `pydantic.ValidationError`. Logged as `OQ-0105-GLOBAL-QUERY-FIELD` for 1-line follow-up after job-0114 lands.
- **Decision:** `message_type` is NOT sent on the URL.
  - **Rationale:** For CONUS sweeps, omitting it returns the union of `alert`/`update` messages — matches "most-current state of every alert nationwide" semantics.
- **Decision:** Disable FGB spatial index when null geometries are present.
  - **Rationale:** NWS alerts with zone-only references carry full property tables (severity, headline, areaDesc); dropping them would lose 78% of real-world responses (205/264 features on the 2026-06-08 sweep).

## Invariants Touched

- **Invariant 1 (Determinism boundary): preserves.** Every field returned carries through to the FGB; no LLM-judged numbers.
- **Invariant 3 (Engine registration, not modification): preserves.** New atomic tool via existing `@register_tool` decorator; no agent-core change.
- **Invariant 4 (Rendering through QGIS Server): preserves.** Tool produces FlatGeobuf in cache bucket; no `.qgs` mutation.
- **Invariant 7 (Claims carry provenance): supports.** Returned features are tier-1 (federal NWS); each carries `id`/`senderName`/`effective` for downstream `ClaimSet`.
- **External-API resilience (NFR-R-1): preserves.** Typed errors with `error_code`+`retryable` for every failure mode.
- **Invariant 10 (Minimal parameter surface): preserves.** Tool exposes only `event_types` (intent) and `status` (NWS-fixed enum); no fetchable params demanded.

## Open Questions

- **OQ-0105-GLOBAL-QUERY-FIELD** *(non-blocking; tentative resolution applied)*. The kickoff specifies `supports_global_query=True` in the metadata literal, but that field does not yet exist in `grace2_contracts.tool_registry.AtomicToolMetadata` — it is being added by parallel sibling job-0114-schema-20260608 (still in `assigned` state at handoff). Passing it would crash the agent service at import. **Tentative resolution:** committed without it; recommend orchestrator open a tiny follow-up after job-0114 lands to add `supports_global_query=True` to the `_METADATA` literal here.
- **OQ-0105-CACHE-RAW-VS-FILTERED** *(non-blocking; deferred)*. Current design caches the FILTERED FGB. A future refactor could cache the RAW CONUS sweep and re-filter on hit: 1 fetch/hour services all filter variations. Trade-off: extra work on every hit (re-load + re-filter). Defer until demand profile clearer.
- **OQ-0105-AS-LATITUDE-GATE** *(non-blocking)*. Geographic-correctness envelope excludes American Samoa (~-14° lat). NWS issues very few AS alerts and a Pacific-spanning envelope would be too permissive. Recommend SRS either enumerate explicit exclusions or accept asymmetry. Live test: 0 alerts outside envelope on 2026-06-08 sweep.
- **OQ-0105-EMPTY-NULL-GEOM-RATIO** *(informational)*. Live sweep: 264 features / 59 polygon / **205 null-geometry** (78% null geom). NWS zone/county references could resolve to polygons via FIPS join with `fetch_administrative_boundaries` (TIGER 2024 already cached). Recommend adding an opt-in `resolve_zones=True` flag in a follow-up job — would dramatically increase spatial coverage.

## Dependencies and Impacts

- **Depends on:** job-0090 (`fetch_nws_event` — per-state sibling); job-0032 (registry + cache shim); job-0031 (cache bucket).
- **Affects:** Hazard Event Pipeline (FR-HEP-2 tier-1) — claims derived from this tool should carry `source_authority_tier=1`. Future `model_news_event` / `show_hazard_layer` workflows can call this for nationwide situational awareness.

## Verification

- **Tests run:**
  - `pytest services/agent/tests/test_fetch_nws_alerts_conus.py -v` → **21 passed, 2 skipped**.
  - `GRACE2_TEST_LIVE_NWS_CONUS=1 pytest ... test_fetch_nws_alerts_conus.py -v` → **23 passed** (2 live tests pass).
  - `pytest services/agent/tests/test_fetch_nws_event.py services/agent/tests/test_fetch_nws_alerts_conus.py services/agent/tests/test_main_startup.py -q` → **57 passed, 4 skipped** (no sibling regression).
- **Live E2E evidence:**
  ```
  Live FGB bytes: 374776
  Features: 264
  Distinct event types: 22
  Top 5 events: [('Small Craft Advisory', 106), ('Flood Warning', 37), ('Flood Watch', 22), ('Fire Weather Watch', 16), ('Red Flag Warning', 15)]
  Geographic gate: inside=59 outside=0 null_geom=205
  ```
  - Verbatim transcript at `reports/inflight/job-0105-engine-20260608/evidence/nws_conus_live.txt`.
- **Geographic-correctness gate (job-0086 lesson):** every one of 59 polygon-bearing features has centroid inside the US envelope. 0 outside. Sign-flip / axis-swap regression would surface here.
- **Registry:** `len(TOOL_REGISTRY) == 26` after eager package import; `_import_tools_registry()` registers 41 tools cleanly.
- **Results:** **pass**.
