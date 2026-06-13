# job-0294 — chart UX + humanized tool labels + desktop chat expand + bbox-on-map (FROZEN KICKOFF)

**Specialist:** web
**Model:** Opus
**Opened:** 2026-06-13
**Context:** AWS deployment is live (site = S3, agent = EC2 Bedrock). These are pure web/UX tweaks from a live user-testing pass — NO server/agent changes. Verify against SAVED cases (reopen — charts + layers already persist & rehydrate, server job-0230 + App.tsx) to avoid Bedrock reruns; the orchestrator runs any live re-prompt sparingly.

## Investigation already done (build on it — don't re-discover)
- `components/PipelineCard.tsx`: `humanizeStepName(raw)` exists but `HUMANIZED_STEP_NAMES` is a ONE-ENTRY stub (`llm_generation:"Thinking…"`) — every tool falls through to its raw snake_case name. This is why the chat shows `fetch_dem` / `geocode_location` / `generate_histogram`.
- `components/ChartGallery.tsx`: full-viewport popup with dim backdrop (`rgba(0,0,0,0.65)`, position:fixed, inset:0) ALREADY EXISTS + Esc/backdrop-click dismiss. Do NOT rebuild it.
- `components/ChartStack.tsx`: the in-chat chart is a small ~200×150 stacked mini-card (Chat.tsx:1705 `buildChartStacks(charts).map(<ChartStack>)`). Clicking opens ChartGallery.
- Charts persist: `_persist_chart`/SessionChartRecord (server) + `activeSession.charts` rehydration (App.tsx ~:640). Reopen replays them — verify, don't rebuild.
- `App.tsx` expandLeft/expandRight = left/right PANEL collapse toggles, NOT a chat-width control.

## Scope (4 items, all web/src)

### A. State-aware humanized tool labels (`PipelineCard.tsx`)
Populate `HUMANIZED_STEP_NAMES` for the full live tool set (the 89 tools — names enumerable from `services/agent/src/grace2_agent/tools/` or the registry log; cover at minimum every tool a demo hits: geocode_location, fetch_dem, fetch_landcover, fetch_population, fetch_buildings, fetch_roads_osm, fetch_administrative_boundaries, fetch_usace_nsi, fetch_fema_nfhl_zones, fetch_nws_alerts_conus, fetch_statsgo_soils, compute_hillshade/slope/aspect/colored_relief/zonal_statistics/building_density/impervious_surface, clip_raster_to_bbox/clip_raster_to_polygon/clip_vector_to_polygon, extract_landcover_class, generate_histogram/time_series/damage_distribution, summarize_layer_statistics, publish_layer, run_model_flood_scenario, run_model_groundwater_contamination_scenario, run_modflow_job, run_pelicun_damage_assessment, run_solver, wait_for_completion, run_model_news_event_ingest, plus llm_generation). Make labels **state-aware**: a present-tense RUNNING label ("Fetching DEM…", "Modeling flood [SFINCS]…", "Computing hillshade…", "Building damage estimate…") and a terminal COMPLETE label ("Loaded DEM", "Flood modeled", "Hillshade ready"). Extend `humanizeStepName` to `(rawName, state)` (default state preserves current single-arg call sites, or update them). Unmapped tools → a graceful title-cased fallback ("fetch_x" → "Fetch X"), never the raw snake_case. Keep the running rainbow-gradient + timer behavior intact.

### B. In-chat chart = full chat width + click → existing gallery
The in-chat chart should span the **entire chat column width** (not the 200px mini-card), rendered legibly inline. Clicking it opens the existing `ChartGallery` overlay (already dim — just wire/keep the onClick). Keep the multi-chart grouping concept, but each visible chart is full-width. Vega-embed must re-fit to the wider container (responsive width; re-embed on resize).

### C. Desktop chat expand
A desktop-only affordance to widen the chat panel (e.g. an expand/collapse-width toggle button in the chat header, persisted to localStorage like the panel-collapse flags). Mobile unaffected (`useIsMobile`). Default width unchanged; expanded ~ a wider reading column. The full-width chart (B) tracks whichever width is active.

### D. bbox-on-map for the active analysis extent
When the agent emits a `zoom-to` map-command with a bbox (it already does — `Map.tsx` consumes it for fitBounds), ALSO draw that bbox as a styled rectangle outline on the map (thin accent stroke, no/!light fill, e.g. a dashed line) so the user sees exactly what extent is being measured. Persisted-case reopen replays the last zoom-to (App.tsx job-0280) → the rectangle should appear on reopen too. Source the bbox from the SAME map-command the Map already handles — no agent change. A single "analysis extent" rectangle (replace on new bbox) is fine for v0.1.

## Constraints
- NO server/agent/contracts changes. web/src only + vitest. Charts/layers persistence already works — do NOT touch it; just consume it.
- `useIsMobile` gates desktop-only chat expand. Dev/disabled-Firebase mode behavior must stay intact.
- Full web suite green (vitest). `npx tsc --noEmit` no NEW errors in changed files.
- Never `git add -A` (tree carries unrelated dirty files — SettingsPopup.tsx etc.); stage only what you change. Commit `job-0294: ...` + `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

## Deliverables
report.md + STATE=IN_REVIEW (return full report in final message if the write is blocked). Final message: the humanized-label map size + a few examples, the chart full-width + gallery wiring, the chat-expand affordance, the bbox-rectangle mechanism (file:line each), vitest + tsc counts, commit hash. The orchestrator deploys to S3 and verifies against the saved Boulder chart case (no Bedrock rerun).
