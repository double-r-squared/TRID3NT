# Audit: Sprint-09 acceptance — Playwright end-to-end + headline screenshots + sprint close

**Job ID:** job-0066-testing-20260607, **Sprint:** sprint-09 (Stage D — sprint close), **Auditor:** Development Orchestrator, **Status:** assigned

## Task Assignment

**Specialist:** testing

**Prerequisites (ALL APPROVED):**
- job-0060 (engine): `run_model_flood_scenario` returns LayerURI → PipelineEmitter auto-emit → session-state.loaded_layers populates
- job-0061 (infra): QGIS Server SA reads runs bucket
- job-0062 (engine): publish_layer atomic tool + PyQGIS worker raster path + continuous_flood_depth.qml
- job-0063 (engine): OQ-59 CRS-label fix (EPSG:32617)
- job-0064 (web): pipeline cards inline in chat (PipelineStrip deleted)
- job-0065 (web): LayerLegend colorbar + hide-empty LayerPanel + collapse toggles
- job-0067 (infra): pyqgis-worker SA reads runs bucket (NEW carry-forward OQ-67-WORKER-IMAGE-REBUILD — worker image stale; sprint-10 fix; SPRINT-9 PLAYWRIGHT USES DEV-INJECTION HOOKS to mock loaded_layers)

Plus orchestrator-direct decision: `docs/decisions/layer-emission-contract.md` + SRS v0.3.21 FR-MP-6 Case UX.

**SRS references** (narrow file loading only):
- `docs/srs/03-functional-requirements.md` FR-WC (web client UX) + FR-MP-6 (Case UX — forward-looking; sprint-9 acceptance just validates current scope)
- DO NOT load `docs/SRS_v0.3.md` monolith.

**Required reads:**
- `reports/complete/job-0064-web-20260607/report.md` + `evidence/` — chat-inline cards UI baseline
- `reports/complete/job-0065-web-20260607/report.md` + `evidence/` — colorbar + collapse toggle baseline
- `reports/complete/job-0062-engine-20260607/report.md` — publish_layer atomic tool + the OQs surfaced
- `reports/sprints/sprint-09.md` — manifest + exit criteria
- `web/src/App.tsx` — dev-injection hooks `window.__grace2InjectSessionState`, `window.__grace2InjectPipelineState`, `window.__grace2InjectMapCommand` (per job-0064 + job-0065 they're registered at startup)

### Why this job exists

Sprint-09 closes with Playwright acceptance verifying the whole M5→UI loop works end-to-end UI-wise. The deployed PyQGIS worker is one image revision behind (OQ-67 — deferred to sprint-10), so the **live worker round-trip** isn't on the sprint-9 critical path. Instead, drive the UI via the existing dev-injection hooks (the same approach job-0065 used to capture its screenshots): inject a synthetic `session-state` envelope with `loaded_layers[0]` pointing at a real-but-pre-existing QGIS Server WMS URL (e.g., the basemap or a known good layer), inject a synthetic `pipeline-state` envelope that exercises the chat-inline cards, screenshot at three states. The substrate proof (live worker round-trip) is documented in job-0062 + job-0067 reports; sprint-9 acceptance gates the UI integration.

### Scope

1. **Playwright tests** (`tests/m6/` or wherever the existing Playwright tooling lives — check `tests/` for the existing layout from job-0027):
   - **Test 1 — baseline empty state.** App mounted, no session-state injected yet. Assert: LayerPanel is hidden (per job-0065 hide-when-empty); LayerLegend is hidden; basemap is visible; chat panel is visible; no pipeline cards in chat. **Screenshot:** `evidence/baseline_empty.png`.
   - **Test 2 — mid-run with pipeline cards.** Inject a pipeline-state via `window.__grace2InjectPipelineState` with 3 steps: `fetch_dem` (status=complete, 100%), `build_sfincs_model` (status=running, 47%), `run_sfincs` (status=pending, 0%). Assert: 3 `[data-testid="pipeline-card"]` elements visible in chat; cancel button visible. **Screenshot:** `evidence/mid_run_pipeline_cards.png`.
   - **Test 3 — final with flood layer rendered + colorbar.** Inject a session-state via `window.__grace2InjectSessionState` with `loaded_layers: [{layer_id: "flood-demo-1", uri: "<a real WMS URL — use a known good QGIS Server WMS URL serving any layer to avoid the OQ-67 worker-image-rebuild blocker; if needed, fall back to a synthetic URL that the MapLibre source can attempt to add even if it won't render real tiles>", style_preset: "continuous_flood_depth", visible: true, role: "primary"}]`. Assert: LayerPanel is visible with 1 row; LayerLegend is visible at bottom-center showing "Max flood depth (m)" + 0 m / 3.5 m ticks. **Screenshot:** `evidence/final_flood_layer.png` — THE HEADLINE SPRINT-9 DELIVERABLE.
   - **Test 4 — collapse toggles work.** Click left collapse chevron; assert left panel reduces to 28px strip + map expands + legend stays centered. Click right collapse chevron; assert same on right side. Reload the page; assert collapse states restored from localStorage (per job-0065). **Screenshot:** `evidence/panels_collapsed_e2e.png`.

2. **Full regression sweep:**
   - Web suite: `cd web && npm run test` — must stay 46+/46+ passing.
   - Agent suite: `PYTHONPATH=services/agent/src:packages/contracts/src .venv-agent/bin/python -m pytest services/agent/tests/ -q` — must stay 180+/180+ passing.
   - Contracts suite: `PYTHONPATH=packages/contracts/src .venv-agent/bin/python -m pytest packages/contracts/tests/ -q` — must stay 142+/142+ passing.
   - PyQGIS worker suite: `pytest services/workers/pyqgis/tests/ -q` (or wherever they live) — must stay green.

3. **Honest disclosure** in the report:
   - Sprint-9 acceptance verifies the **UI integration** end-to-end using dev-injection-driven session-state.
   - The **live worker round-trip** (real WMS URL from a fresh .qgs mutation) is deferred to sprint-10 per OQ-67-WORKER-IMAGE-REBUILD.
   - Document each test's pass/fail status with evidence references.

4. **Sprint-09 retrospective**:
   - Planned vs actual: 5 reserved jobs (0060/0061/0062/0063/0064/0065/0066) + 1 added mid-sprint (0067 IAM follow-up). 7 jobs delivered.
   - Cost telemetry: total sprint-9 tokens from `reports/cost_tracking.json`; Opus vs Sonnet breakdown.
   - Architectural decisions landed: `docs/decisions/layer-emission-contract.md` (2026-06-07), SRS v0.3.21 FR-MP-6 Case UX.
   - Open OQ carry-forward list:
     - OQ-62-LAYERURI-URI-FIELD + OQ-W-65-STYLE-PRESET (bundle as schema D.2 amendment in sprint-10)
     - OQ-67-WORKER-IMAGE-REBUILD (infra, sprint-10 opener)
     - OQ-62-PUBSUB-COMPLETION-POLL (defer)
     - OQ-62-QGS-MUTATION-CONFLICT (FR-MP-6 scope when Case persistence lands)
     - OQ-61-CLOUD-RUN-SCALING-BLOCK-DRIFT (pre-existing; sprint-10 infra)
   - Sprint-10 opening hand-off: list the 3–4 likely jobs (worker image rebuild + schema cleanup + Cloud Run scaling reconciliation + Mode 2 .gov/.edu offer-to-add OR ATCF Hurricane Ian forcing).

5. **Sprint manifest close-out**: Add Retrospective section to `reports/sprints/sprint-09.md` (mirroring the sprint-8 close pattern); orchestrator will flip Status: closed + Closed: <date> in the closing commit.

### File ownership (exclusive)
- `reports/inflight/job-0066-testing-20260607/` — your report + evidence
- `tests/m6/` (or wherever the existing Playwright tests live) — additive tests only
- `reports/sprints/sprint-09.md` — Retrospective section + Exit criteria checkboxes ONLY (don't restructure)

### FROZEN
- All source files (no code edits in a testing-acceptance job)
- All prior approved reports in `reports/complete/`
- `docs/decisions/layer-emission-contract.md`
- `reports/PROJECT_LOG.md` (orchestrator owns)
- `reports/cost_tracking.json` (orchestrator owns)

### Acceptance criteria
- [ ] 4 Playwright tests pass (baseline / mid-run / final flood layer / collapse toggles)
- [ ] 4 screenshots captured + saved to evidence/
- [ ] Sprint-9 acceptance honestly distinguishes UI-integration verified vs live-worker-round-trip deferred
- [ ] Full regression: web + agent + contracts + worker suites all green
- [ ] Sprint-09 retrospective written: planned vs actual, cost telemetry with Opus/Sonnet breakdown, decisions landed, OQ carry-forward list, sprint-10 hand-off
- [ ] Single commit (`testing: job-0066 sprint-09 acceptance + close note`)
- [ ] No edits to FROZEN paths
