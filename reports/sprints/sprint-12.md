# Sprint 12 (mega-sprint): Case UX + Case 1/Case 2 demo substrate + Pelicun + broad atomic-tool expansion

**Status:** open
**Opened:** 2026-06-08
**Closed:** —
**SRS milestones covered:** FR-MP-6 Case UX (was sprint-11 deferred); M6 FR-HEP news/event pipeline; M5.5 Pelicun impact post-processor; conservation/biodiversity atomic tools (Tier-1 substrate); NLCD-derivative atomic tools; OSM atomic tools; Mode 2 .gov/.edu offer-to-add; Secrets UX (§F.3).

## Goal

Per user direction 2026-06-08 ("pivot put that on the back burner and focus on demo related features… execute the next stage + sprints into one big sprint basically… maximize the additions to the agents tool usage"): collapse Stage 2 + sprint-12 + sprint-13's non-MODFLOW work (Pelicun on existing Fort Myers COG) into one wide-parallel mega-sprint. MODFLOW + Case 2 full E2E carved off into sprint-13 (groundwater engine integration too heavy to bundle safely).

Deliverables at sprint close:
1. **Case 1 live E2E demo** — Everglades/Big Cypress/Apalachicola: find habitat → model rainfall via SFINCS → show flooding + impacted habitat (per-species layers + zonal_statistics impact metrics).
2. **Case 2 partial demo** — news → derived spill parameters surfaced to user (STOPS before MODFLOW, sprint-13's deliverable).
3. **Pelicun damage demo** — fragility-curve-driven damage assessment on existing Fort Myers flood COG.
4. **Case UX shell** — Cases list left + per-Case persistence + chat replay rehydration default.
5. **Broad atomic-tool expansion** — ~12 new fetcher/analytical tools (conservation, news/event, NLCD-derivatives, OSM, Pelicun).
6. **Secrets UX + Mode 2 .gov/.edu** in production.

## Model routing policy (user direction 2026-06-08)

**Opus by default**; Sonnet narrowed to: Playwright runs, sub-200-LOC mechanical edits, research reads, acceptance verification only. Codified job-0086 lesson in every Opus kickoff: "verify against geography/data, not URL/render consistency."

## Wave structure (deterministic, deterministically gated)

```
WAVE 1 — fan-out (15 jobs, ALL Opus, parallel)
  ↓ all approved
WAVE 2 — composers (5 jobs, ALL Opus, parallel)
  ↓ all approved
WAVE 3 — UX + acceptance (4 jobs, 1 Opus + 3 Sonnet, parallel)
  ↓ all approved
WAVE 4 — close (1 job, Opus)
  → sprint-13 MODFLOW manifest authored
```

Total projected: ~5M tokens (vs sprint-11's ~1M; sprint-10's 1.54M). Wall-clock: ~3-5 hours.

## Wave 1 — fan-out (parallel)

| Job | Specialist | Tool / Item | Model |
|---|---|---|---|
| 0087 | engine | `fetch_gbif_occurrences` Tier-1 — biodiversity points by species + bbox | opus |
| 0088 | engine | `fetch_inaturalist_observations` Tier-1 — citizen-science observations | opus |
| 0089 | engine | `fetch_wdpa_protected_areas` Tier-1 — World Database on Protected Areas polygons | opus |
| 0090 | engine | `fetch_nws_event` — National Weather Service active alerts | opus |
| 0091 | engine | `fetch_storm_events_db` — NOAA Storm Events Database | opus |
| 0092 | engine | `web_fetch` — generic HTML+JSON ingest with content extraction (BeautifulSoup) | opus |
| 0093 | engine | `aggregate_claims_across_sources` — LLM-reasoning cross-source aggregation | opus |
| 0094 | engine | `extract_landcover_class` — NLCD-derived class mask | opus |
| 0095 | engine | `compute_impervious_surface` — NLCD impervious surface fraction | opus |
| 0096 | engine | `compute_building_density` — Microsoft Building Footprints density | opus |
| 0097 | engine | `fetch_roads_osm` — OSM Overpass roads fetcher | opus |
| 0098 | engine | Pelicun atomic stub — fragility-curve runner skeleton (Decision N) | opus |
| 0099 | schema | Case schema (FR-MP-6 + Case-persistence envelopes) — priority-first Wave 1 | opus |
| 0100 | schema | Secrets schema (§F.3 envelope shape + at-rest contract) | opus |
| 0101 | agent | Mode 2 .gov/.edu classifier (offer-to-add flow) | opus |

## Wave 2 — composers (parallel, gated on Wave 1)

| Job | Specialist | Item | Model |
|---|---|---|---|
| 0102 | agent | Case UX agent (per-Case `.qgs` lazy-init; OQ-62 concurrency) | opus |
| 0103 | web | Secrets UX web (key-entry UI + Tier-2 unlock) | opus |
| 0104 | engine | `model_flood_habitat_scenario` workflow (Case 1 composer) | opus |
| 0105 | agent | `model_news_event_ingest` workflow (Case 2 partial — news → spill params) | opus |
| 0106 | engine | Pelicun composer + Fort Myers damage E2E run | opus |

## Wave 3 — UX + acceptance (gated on Wave 2)

| Job | Specialist | Item | Model |
|---|---|---|---|
| 0107 | web | Case UX web (Cases-list + chat-replay rehydration) | opus |
| 0108 | testing | Case 1 acceptance — Everglades/Big Cypress live E2E + screenshot | sonnet |
| 0109 | testing | Case 2 partial acceptance — news-ingest demo | sonnet |
| 0110 | testing | Pelicun acceptance — damage-map screenshot | sonnet |

## Wave 4 — close

| Job | Specialist | Item | Model |
|---|---|---|---|
| 0111 | testing | Sprint-12-mega close + sprint-13 MODFLOW manifest authoring | opus |

## Safety guards

1. **Stagger Wave 1 dispatch** to reduce idempotent-append contention on `tools/__init__.py` + `main.py`
2. **Case schema is priority-first** in Wave 1 — Wave 2 Case UX agent rebases on its envelope
3. **Pre-flight dep probe** for `web_fetch` (BeautifulSoup4, httpx in `.venv-agent`) before Wave 1 dispatch
4. **OQ budget per wave**: >3 blocking OQs halts before next wave
5. **All Opus kickoffs** carry the codified job-0086 lesson: "verify against geography/data, not URL/render consistency"
6. **Kickoff-front-loading discipline**: orchestrator (me) does the design; runners execute. No "Opus, figure it out" prompts.

## Demo cases enabled

**Case 1 substrate flow** (sprint-12-mega acceptance):
```
agent.geocode("Everglades / Big Cypress / Apalachicola")  → bbox
agent.fetch_wdpa_protected_areas(bbox)                     → protected_areas_uri (separate layer)
agent.fetch_gbif_occurrences(species="Florida panther", bbox)
                                                            → panther_uri (own layer, own color per memory)
agent.fetch_gbif_occurrences(species="Roseate spoonbill", bbox)
                                                            → spoonbill_uri (own layer)
agent.fetch_inaturalist_observations(species="American alligator", bbox)
                                                            → alligator_uri (own layer)
agent.model_flood_scenario(bbox, forcing="atlas14_100yr")  → flood_depth_uri (sprint-7 existing)
agent.compute_zonal_statistics(value_raster_uri=flood_depth_uri,
                               zone_input_uri=protected_areas_uri,
                               statistics=["max","mean","count"], zone_threshold=0.5)
                                                            → "max depth 1.2m within protected area;
                                                               240 panther occurrence points in flooded zone"
publish_layer + visualization
```

**Case 2 partial flow** (sprint-12-mega partial acceptance — STOPS before MODFLOW):
```
agent.web_fetch(url="<news article about real spill>")      → raw_html + extracted_text
agent.fetch_nws_event(area="Longview-style bbox")           → active_alerts list
agent.aggregate_claims_across_sources([extracted_text, ...])
                                                            → derived_params {location, scale, contaminant}
agent presents derived_params to user with provenance
[sprint-13 will continue: agent.fetch_landcover + agent.model_groundwater_contamination_scenario]
```

## Exit criteria

- [ ] All 25 Wave 1+2+3+4 jobs approved
- [ ] Tool registry shows ≥36 atomic tools (24 entering + 12 new)
- [ ] Case 1 live demo screenshot: flood + ≥3 per-species habitat layers + protected-area zonal-stats output on a real Florida bbox
- [ ] Case 2 partial demo: news → derived spill params surfaced to user with provenance log
- [ ] Pelicun damage demo: damage map screenshot on Fort Myers
- [ ] Case UX shell: Cases list renders + per-Case persistence + chat replay verified via Playwright
- [ ] Mode 2 .gov/.edu offer-to-add: classifier fires + popup modal renders + audit log captures
- [ ] Secrets UX: key-entry flow + Tier-2 unlock verified via Playwright
- [ ] Full regression sweep: 0 new failures
- [ ] Sprint-13 MODFLOW manifest authored

## Deferred to sprint-13

- MODFLOW 6 container + Cloud Run Job + Workflows
- MT3D-USGS or mf6-gwt solute transport (decision TBD)
- `model_groundwater_contamination_scenario` workflow
- Case 2 full E2E (news → MODFLOW + plume render)

## Deferred to sprint-14+

- Wildfire data fetchers (FIRMS, MTBS, NIFC, LANDFIRE, HRRR-Smoke)
- Tier-2 conservation fetchers (eBird, IUCN, Movebank — needs Secrets UX from sprint-12 in production)
- Maxent / Circuitscape / animal-movement analytical tools
- 8 deferred §2.3 engines (TELEMAC, HEC-HMS, ParFlow, pywatershed, SWMM, QUIC-Fire, wrf-python, etc.)
- Per-species color-coded chrome UX (deferred per user 2026-06-08 — "get it up first then think about chrome")

## Retrospective

_Filled at close._
