# Report: fetch_glm_lightning -- GOES GLM optical-lightning group-energy-density fetcher

**Job ID:** tools-seed
**Sprint:** (tools-session standalone seed)
**Specialist:** Tools / Agent-Config Specialist
**Task:** Promote the validated `/tmp/glm_proto` GLM lightning prototype to a registered atomic tool `fetch_glm_lightning` (GLM-L2-LCFA group-energy-density -> transparent purple RGBA raster LayerURI), per `reports/inflight/tools-seed/KICKOFF.md`.
**Status:** ready-for-audit

## Summary
Promoted the local-first GLM prototype into a registered, cached atomic data tool. It lists `GLM-L2-LCFA` granules in a time window from the anonymous public `noaa-goesNN` S3 archive, bins GROUP energy density (GED) onto the SAME ~2 km EPSG:4326 ABI grid the GOES products use, bakes a purple log-ramp into a 4-band transparent RGBA COG, and returns a `LayerURI` (or a `step <N>` animation when `accumulation_window_s` is set). Fully in-seam: no contracts/server/publish_layer/adapter edits. Proven end-to-end against REAL data including a live S3 cache round-trip; output matches the prototype pixel-for-pixel.

## Changes Made
- **NEW `services/agent/src/grace2_agent/tools/fetch_glm_lightning.py`** -- the tool. Reuses the proven shared helpers from `fetch_goes_archive_animation` (`_grid_for_bbox`, `_rgba_array_to_cog_bytes`, `_OUT_RES_DEG`, `_round_bbox`, `_parse_utc`, `_iso_z`); ports the prototype's GED binning + purple log-ramp verbatim; own typed-error hierarchy (`GLMBboxRequiredError`=`BBOX_REQUIRED`, `GLMInputError`, `GLMUpstreamError`, `GLMEmptyError`); `estimate_payload_mb`; honesty floor (no granules / no in-AOI groups -> typed empty, never a blank overlay).
- **`tools/__init__.py`** -- one eager-import line registering the tool (additive).
- **`categories.py`** -- PRIMARY `weather_atmosphere` + SECONDARY `("fire",)` (lightning = dominant wildfire ignition source).
- **`data/tool_query_corpus.yaml`** -- 7 routing queries.
- **NEW `services/agent/tests/test_fetch_glm_lightning.py`** -- 22 unit tests.

## Decisions Made
- **Baked transparent RGBA COG, not data-COG + TiTiler colormap.** Mirrors `fetch_goes_active_fire`'s transparent-overlay pattern. publish_layer's RGBA/multiband passthrough fires first in `_resolve_titiler_style_params`, so the purple log-ramp renders directly with no colormap, no autoscale, and -- critically -- **no `publish_layer.py` edit** (stays in-seam). A log ramp also can't be expressed by TiTiler's linear `colormap_name` without pre-log baking anyway.
  - Alternative considered: single-band GED data-COG + a new `_TITILER_STYLE_REGISTRY` purple row. Rejected: needs a publish_layer edit (shared render chokepoint) and a linear ramp can't honor the log stretch.
- **Default returns a single `LayerURI`** (whole-window accumulation) to satisfy the acceptance verbatim and match `fetch_goes_satellite`; `accumulation_window_s` opt-in returns an ordered `list[LayerURI]` with `step <N>` names + identical preset/bbox for the web scrubber (matches `detectSequentialGroups`).
- **Default satellite `goes-19`** (GOES-East, current operational) per kickoff; goes-18 West, goes-16/17 historical.
- **`ttl_class="dynamic-1h"`** matching the GOES sibling fetchers (cache key carries the explicit window, so a past window's COG is stable regardless).
- **Single-frame window capped at 20 min** + a 180-granule hard cap to bound the granule download; longer spans should use `accumulation_window_s` (keeps accumulation honest and bounds I/O).

## Invariants Touched
- **Metadata-payload pattern:** preserves -- `AtomicToolMetadata` + `@register_tool` + `read_through` cache shim + `estimate_payload_mb` + `supports_global_query=False` (bbox mandatory).
- **Claims carry provenance / honesty floor:** preserves -- no-data is a typed `GLMEmptyError`, never a fabricated/blank overlay.
- **Minimal parameter surface:** preserves -- bbox + satellite + window + optional accumulation; `**_extra_ignored` absorbs LLM-invented kwargs.
- **Tier separation:** preserves -- pure data fetcher; no engine/contract/server coupling.

## Open Questions
- None blocking. (Style preset `glm_lightning` is cosmetic under the RGBA passthrough; it functions only as the animation grouping key, which is satisfied.)

## Dependencies and Impacts
- **Depends on:** `fetch_goes_archive_animation` shared helpers (already on main).
- **Affects / coordination (NON-blocking, for the Orchestrator deploy batch):**
  - **Heavy sync fetcher** -- `fetch_glm_lightning` downloads GLM granules (seconds of blocking I/O). It should be added to `server.py:_ALWAYS_OFFLOAD_SYNC_TOOLS` (Orchestrator-owned) before prod so it never starves the WS heartbeat. Flagged for the deploy batch.
  - Box deploy is the Orchestrator-gated batch (this lands to `main` only).

## Verification
- **Tests run:** `tests/test_fetch_glm_lightning.py` -> **22 passed (0.18s)**. Full agent suite `pytest tests -m "not live_gemini"` -> **7256 passed, 3 failed, 93 skipped, 1 xfailed (355s)**. The 3 failures are `test_granularity_gate.py` only and are **PRE-EXISTING env drift** (`SWMMMeshError: No module named 'swmm_api'`), confirmed by stash-rerun on clean HEAD (same 3 fail with my edits stashed). `test_categories.py` global registry/category sweeps -> 17 passed (every registered tool has a primary category; no orphan entries).
- **Live E2E evidence (real data + real S3 round-trip):** `TOOL_REGISTRY["fetch_glm_lightning"].fn(bbox=(-83.5,25.5,-79.5,31.5), satellite="goes-19", start_utc="2025-09-07T18:00:00Z", end_utc="2025-09-07T18:03:00Z")` returned a `LayerURI` -> COG written to `s3://grace2-hazard-cache-226996537797/cache/dynamic-1h/goes_glm/5782a3a49ee9c56dce1a20dfd47b4a51.tif` (5341 bytes), read back as a **4-band uint8 EPSG:4326 200x300 COG**; 677 lit cells (1.13%); lightning-over-cloud ratio **1.55** (>1 = correctly registered over the bright convective cores). Composited PNG `/tmp/glm_promote/ged_over_visible_TOOL.png` matches the prototype target `/tmp/glm_proto/ged_over_visible.png` pixel-for-pixel.
- **Results:** **pass** (the only suite failures are pre-existing, unrelated to this work).
