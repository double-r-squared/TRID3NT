# Report: Case 3 workflow composer — NWS alert → MRMS → SFINCS

**Job ID:** job-0229-agent-20260609
**Sprint:** sprint-13 (Stage 2)
**Specialist:** agent
**Task:** `model_nws_flood_event_scenario` composer — fetch NWS active flood warning → MRMS observed precip over the warning polygon → SFINCS inundation forced by the observed precip → 3-layer accumulation contract. Graceful no-warning degrade.
**Status:** ready-for-audit

## Summary

Authored the Case 3 composer `model_nws_flood_event_scenario` (deterministic, LLM-free) chaining `fetch_nws_alerts_conus` → flood-warning selection + polygon-bbox extraction → `fetch_mrms_qpe` → `model_flood_scenario(forcing_raster_uri=mrms_uri)` → a 3-layer accumulation contract `{warning_polygon_layer, mrms_precip_layer, flood_depth_layer}`. Added an LLM-facing `run_model_nws_flood_event_scenario` wrapper (workflow_dispatch / uncacheable). 27 new unit+integration tests pass; the live best-effort path hit the real NWS API (selected an active Severe Flood Warning in Labette/Neosho County, KS) and ran a real MRMS QPE fetch over its polygon bbox (real observed precip: mean 4.94 mm / max 13.5 mm over 24h, valid_time 2026-06-10T01:00Z). SFINCS mocked per kickoff (solver runs are Stage 3 scope).

## Changes Made

- **File: `services/agent/src/grace2_agent/workflows/model_nws_flood_event_scenario.py` (NEW)** — the 5-step composer + pure helpers `select_flood_warning`, `extract_polygon_bbox`, `_narrow_candidates`, `_accumulation_hours`, degrade-result builder, `_format_case3_summary`; graceful degrade statuses `no_active_flood_warning` / `mrms_fetch_failed`; `run_model_nws_flood_event_scenario` `@register_tool` wrapper (workflow_dispatch / live-no-cache / cacheable=False). Never raises for no-warnings/upstream hiccups; does NOT loop/retry NWS.
- **File: `services/agent/tests/test_model_nws_flood_event_scenario.py` (NEW)** — 27 tests.
- **File: `services/agent/src/grace2_agent/workflows/__init__.py` (surgical, +1 line)** — import the new module so its decorator fires.
- **File: `services/agent/src/grace2_agent/categories.py` (surgical, +2 anchors)** — primary `hazard_modeling`, secondary `weather_atmosphere`. Required by `test_categories.py`.

## Open Questions

- **OQ-0229-POLYGON-CLIP-VS-BBOX (TENTATIVE):** SFINCS runs over the warning polygon's bbox, not the polygon itself. Future refinement could clip the flood layer to the exact warning shape via `clip_raster_to_polygon`. Recommend bbox for v0.1; flag for job-0236 to confirm visually.
- **OQ-0229-SECONDARY-CATEGORY (TENTATIVE):** cross-listed under `weather_atmosphere` secondary in addition to `hazard_modeling` primary, mirroring `run_model_news_event_ingest` → `news_events`.

## Dependencies and Impacts

- Depends on: job-0226 (`fetch_mrms_qpe`), job-0225 (`model_flood_scenario` v2 forcing branch), `fetch_nws_alerts_conus` (job-0105).
- Affects: testing job-0236 (Case 3 acceptance); web (3-layer accumulation render contract).

## Verification

- **Tests:** `pytest tests/test_model_nws_flood_event_scenario.py` → 27 passed (0.08s). `test_categories.py` + `test_tools_registry.py` + `test_catalog_tools.py` green (73 combined). Registry import confirms wrapper registration (83 tools) with workflow_dispatch / uncacheable / correct category metadata.
- **Live E2E (best-effort, no Gemini, no key):**
  - `nws_live_probe.log` — LIVE NWS CONUS sweep: 254 active alerts; 31 Flood Warnings; selected Severe Flood Warning over Labette/Neosho County, KS; bbox `(-95.21, 37.26, -95.08, 37.54)`. Single API call.
  - `mrms_live_probe.log` — LIVE NOAA MRMS QPE over that bbox: 29×14 EPSG:4326 GeoTIFF, 406/406 valid cells, observed 24h mean 4.94 mm / max 13.5 mm, valid_time 2026-06-10T01:00Z. Real Case 3 forcing path proven through step 2.
  - SFINCS step mocked (Stage 3 scope; no docker/gcloud on box).
- **Regression:** pre-existing full-suite failures (`test_solver`, `test_web_fetch`, `test_publish_layer`, pelicun tests) are `ModuleNotFoundError`s for uninstalled deps (`google.cloud.workflows`, `trafilatura`, `pelicun`); confirmed unrelated by reverting my shared edits to baseline and observing identical failures.
- **Concurrency note:** a git-stash diagnostic produced conflicts in concurrently-edited `main.py` + `tools/__init__.py`; resolved by keeping the upstream (current concurrent) version — no content lost, no edits to non-owned files.
- **Results:** pass.
