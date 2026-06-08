# Sprint 11: FR-MP-6 Case UX + sprint-10 carry-forwards + compute_hillshade atomic tool

**Status:** planned
**Opened:** 2026-06-08
**Closed:** —
**SRS milestones covered:** FR-MP-6 Case UX implementation (SRS v0.3.21, §3.7); sprint-10 OQ carry-forwards (OQ-76-MAP-ALIGNMENT top priority; OQ-76-MAPCMD-WS small); compute_hillshade atomic tool (SRS §3.5 Tier A hillshade overlay, bumped up per user direction 2026-06-08).

## Goal

Land the FR-MP-6 Case UX shell that makes GRACE-2 feel like a persistent workbench instead of a demo: Cases list left, Chat right, per-Case persistence into `projects` + `sessions` MongoDB collections, chat history saved on each exchange, back-to-Cases nav that resets chat to fresh agent context. In parallel, investigate and fix the OQ-76-MAP-ALIGNMENT visible issue the user flagged after the job-0076 unblock (overlay renders but doesn't geometrically align with the basemap), ship the `compute_hillshade` atomic tool (user-prioritized on 2026-06-08), and close the production WebSocket routing gap for `map-command` (OQ-76-MAPCMD-WS small job). Sprint closes with a testing acceptance job that re-runs all suites and produces the sprint-11 acceptance record.

## Pre-flight (orchestrator-direct)

- **v0.3.22 SRS housekeeping pass** — bundle sprint-10 architectural decisions: CRS_TAG_MISMATCH guard, ws.ts map-command routing, CartoDB DarkMatter dark-theme basemap swap pattern, Map.tsx idle-retry session-state subscriber. Bundle OQ-76-* OQs formally. Keep amendment short. Orchestrator-direct, opens sprint-11 before any specialist job dispatches.

## Jobs (planned — orchestrator assigns job IDs at dispatch time)

| Job ID | Specialist | Task | Depends on | Status |
|--------|-----------|------|------------|--------|
| TBD-web | web | **Case UX shell** — Cases list left-panel view + per-Case state lifting in App.tsx; "new Case" creation flow; in-Case left-panel switches to loaded-layers detail view (per layer-emission-contract); back-to-Cases nav that clears chat context; cases list empty state. No backend wiring yet — uses local React state + MockCase fixtures for the shell verification. | — | planned |
| TBD-schema+engine | schema / engine | **Case persistence backend** — `projects` collection schema wiring (ULID `_id`, bbox, hazard, layer_summary); `sessions` collection chat-history persistence (per exchange, append-only); first agent prompt in a no-Case session creates a `projects` document and binds the session to it; `sessions` `project_id` linkage; back-to-Cases nav triggers fresh agent context (no conversation memory). Requires schema contract for projects/sessions Mongo docs per Appendix D. | TBD-web | planned |
| TBD-web | web | **Map alignment fix (OQ-76-MAP-ALIGNMENT)** — diagnose MapLibre source bounds vs WMS layer geographic bbox at zoom-13; compare overlay pixel grid to reference WMS tile from QGIS Server at matching extent; investigate tileSize:256 vs basemap tile scale and EPSG:32617 → EPSG:3857 reprojection at the layer's small UTM extent. Fix the alignment issue. Pixel-level evidence (overlay bbox vs basemap street grid alignment). Sprint-11 priority per user direction 2026-06-08. | — | planned |
| TBD-engine | engine | **`compute_hillshade` atomic tool** — `gdaldem hillshade` wrapper with style presets ("standard" / "swiss_double" / "multidirectional" / "combined" / "smooth"); COG output to runs bucket; auto-publishes via `publish_layer` (same auto-dispatch pattern as `postprocess_flood`); returns `LayerURI` with WMS URL. Tier A hillshade overlay (SRS §FR-WC-7 reference). User-bumped priority on 2026-06-08. | — | planned |
| TBD-web | web | **Production map-command WS routing (OQ-76-MAPCMD-WS)** — close the dev-injection-only gap: `App.tsx → MapView` onMapCommand callback currently only wired via the dev-only `window.__grace2InjectMapCommand` seam; agent-emitted `map-command(zoom-to)` over the real WebSocket reaches `GraceWs` → bus (job-0072 done) but the App.tsx prop-drilling step is missing. Small job: one prop wire in App.tsx + test + live WS round-trip evidence. | — | planned |
| TBD-testing | testing | **Sprint-11 acceptance** — full regression sweep (all 4 suites + tsc); rewrite test_panels_collapsed_e2e for the new overlay layout; Playwright acceptance of Case UX shell + map alignment verification (pixel-level bluish overlay % + basemap street-grid alignment check); sprint-11 retrospective + sprint-12 manifest stub. Closes sprint-11. | TBD-web (Case UX), TBD-schema+engine, TBD-web (alignment), TBD-engine, TBD-web (WS) | planned |

## Execution order

```
pre-flight (orchestrator-direct):
  v0.3.22 SRS housekeeping pass
  ← before any specialist job dispatches

stage A (parallel, file-disjoint):
  TBD-web        (Case UX shell — App.tsx + new Cases components)
  TBD-web        (Map alignment fix — Map.tsx diagnosis + fix)
  TBD-engine     (compute_hillshade atomic tool — engine only)
  TBD-web        (OQ-76-MAPCMD-WS — App.tsx 1-line prop wire)

stage B (gated on Case UX shell):
  TBD-schema+engine  (Case persistence backend — Mongo wiring)
  ← gated on TBD-web (Case UX shell approved)

stage C (acceptance):
  TBD-testing    (sprint-11 acceptance + close)
  ← gated on all stage A + stage B jobs approved
```

Notes:
- The 3 web jobs (Case UX shell, alignment fix, WS routing) touch different files and are file-disjoint:
  - Case UX: new `Cases.tsx` / `CaseList.tsx` components + App.tsx left-panel switching logic
  - Alignment: Map.tsx source bounds / tile URL parameters only
  - WS routing: App.tsx prop drill for onMapCommand (1-2 lines — verify no collision with Case UX changes; if collision, serialize after Case UX)
- compute_hillshade is engine-only (new tool file, no web or schema changes needed for the tool itself)
- Case persistence backend gates on Case UX shell because it needs the Cases React shape to define the Mongo document contracts accurately

## Exit criteria

- [ ] **v0.3.22 SRS housekeeping** landed (orchestrator-direct, before any specialist job).
- [ ] **Case UX shell**: Cases list renders in left panel; in-Case left panel shows loaded-layers detail; back-to-Cases nav clears chat context; empty state renders correctly. Dev-injection Playwright evidence showing Cases list + per-Case state.
- [ ] **Case persistence backend**: first agent prompt creates a `projects` document (verified via MongoDB MCP read); chat history appends to `sessions` document; session binds to project_id; back-to-Cases nav emits fresh agent context (no prior conversation messages on new prompt).
- [ ] **Map alignment fix (OQ-76-MAP-ALIGNMENT)**: flood overlay aligns with basemap street grid at zoom-13 Fort Myers. Pixel-level evidence: overlay bbox corners within ≤50px of basemap feature positions (or equivalent objective measurement). Root cause documented.
- [ ] **compute_hillshade**: tool registered at startup (18 tools); hillshade COG produced from a DEM fixture; WMS layer published; Playwright screenshot showing hillshade overlay on basemap. Style presets: at minimum "standard" implemented; others documented.
- [ ] **OQ-76-MAPCMD-WS closed**: agent-emitted `map-command(zoom-to)` over real WebSocket triggers `fitBounds` in the client. WS round-trip log evidence.
- [ ] **Full regression sweep**: web 72+ / agent 187+ / contracts 145+ / pyqgis 13+ / tsc clean on owned files.
- [ ] **Playwright acceptance**: test_panels_collapsed_e2e rewritten for overlay layout (passes green); sprint-09 tests 1-3 still pass.
- [ ] **Sprint-11 retrospective** written and sprint-12 manifest stub authored.

## Deferred to sprint-12 (per roadmap)

- Pelicun impact post-processor (Decision N / M5.5 from SRS)
- Secrets UX (§F.3) — blocks Tier-2 API key entry
- Mode 2 `.gov`/`.edu` offer-to-add (envelope shapes + agent emission detection + popup modal + audit log)
- Additional atomic tools (bundled with sprint-12 data-coverage expansion)
- OQ-62-QGS-MUTATION-CONFLICT (per-Case `.qgs` isolation — deferred until real multi-Case usage surfaces the conflict)
- OQ-62-PUBSUB-COMPLETION-POLL (async polling refinement)

## Deferred to sprint-13+ (per roadmap)

8 deferred engines from §2.3:
- TELEMAC (closest to ready per §2.3 amendment in v0.3.14)
- MODFLOW
- HEC-HMS
- ParFlow
- pywatershed
- SWMM/PySWMM
- QUIC-Fire stack
- wrf-python

Order pending user-defined case need; TELEMAC is likely first if technical readiness drives sequencing.

## Indefinitely deferred (user-defined case required)

- ATCF Hurricane Ian real storm forcing (`fetch_hurricane_track` + `model_flood_scenario` real-forcing branch) — per user direction 2026-06-07: "defer that and maybe surface my input later on when the time is right." Will be a sprint with a clear deliverable shape when a real-event case is defined.

## Retrospective

_Filled at close._
