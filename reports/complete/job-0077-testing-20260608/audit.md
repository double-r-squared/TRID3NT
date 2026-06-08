# Audit: Sprint-10 acceptance + close + sprint-11 hand-off

**Job ID:** job-0077-testing-20260608, **Sprint:** sprint-10 close, **Auditor:** Development Orchestrator, **Status:** assigned

## Task Assignment

**Specialist:** testing

**Prerequisites (ALL APPROVED):**
- Sprint-10 substantive jobs: 0068 web (UI correction) + 0069 infra Opus (worker rebuild) + 0070 engine (CRS regen + first headline) + 0071 engine (UX polish + CRS guard + auto-dispatch) + 0072 schema (D.2 bundle + ws.ts routing) + 0073 infra (Cloud Run drift) + 0074 engine (worker bug fixes + rebuild) + 0075 engine (visible-corrections verification) + 0076 web Opus (THE actual headline unblock — root cause `if (!m.isStyleLoaded()) return;`)
- Research workflow `research-crs-mismatch-recurrence-20260607` (wf_6c8d62dc-2c1)
- User direction 2026-06-07 + 2026-06-08: roadmap captured in memory (Sprint-11 = FR-MP-6 Case UX headline; sprint-12+ bundled data-coverage expansion; sprint-13+ deferred engines; defer ATCF until user-defined case)

**SRS references** (narrow file loading only):
- `docs/srs/03-functional-requirements.md` FR-MP-6 (Case UX — sprint-11 headline scope)
- DO NOT load `docs/SRS_v0.3.md` monolith.

**Required reads:**
- `reports/sprints/sprint-10.md` — manifest + exit criteria
- `reports/complete/job-0076-web-20260607/report.md` — the breakthrough diagnosis
- `reports/PROJECT_LOG.md` (tail ~80 lines) — full sprint-10 narrative

### Why this job exists

Sprint-10 is substantively complete: UI correction shipped, schema bundle landed, infra drift reconciled, worker bugs fixed, and most importantly the cascading-silent-failure pattern that misled the orchestrator + 3 Sonnet agents was broken by job-0076's Opus investigation. The user has accepted the corrected light+dark screenshots with visible flood overlay (37.6% / 26.9% bluish pixels measured).

Three follow-up visible issues remain — alignment, rotation, zoom — which the user explicitly tagged as carry-forward to sprint-11, NOT to address in this sprint close.

### Scope

#### Part 1 — Full regression sweep

Re-verify all test suites stay green:

- `cd web && npm run test` — should be 72+/72+ (was 63 pre-0076; +9 from 0076)
- `PYTHONPATH=services/agent/src:packages/contracts/src .venv-agent/bin/python -m pytest services/agent/tests/ -q` — should be 187+/187+ (was 180 pre-0071; +7 from 0071)
- `PYTHONPATH=packages/contracts/src .venv-agent/bin/python -m pytest packages/contracts/tests/ -q` — should be 145+/145+ (was 142 pre-0072; +3 from 0072)
- pyqgis worker tests — should be 13+/13+ (was 10 pre-0074; +3 from 0074)
- `cd web && npx tsc --noEmit` — should be clean on all owned files

Capture verbatim outputs to `evidence/`.

#### Part 2 — Playwright acceptance via dev-injection

Re-run the existing Playwright acceptance tests from `tests/m6/playwright/test_sprint09_acceptance.py` (from job-0066). They use dev-injection seams that survive sprint-10's UI changes.

If they fail because of sprint-10's layout changes (which is possible — App.tsx underwent significant reversion in 0068 then 0076), document the failures honestly and note as sprint-11 carry-forward. Don't try to fix tests in this acceptance job.

#### Part 3 — Sprint-10 retrospective (in-place in `reports/sprints/sprint-10.md`)

Add a `## Retrospective` section mirroring the sprint-8 close pattern. Cover:

- **Planned vs actual:** sprint manifest reserved 5 jobs (0068/0069/0070/0071/0072/0073/0074/0075/0076). Actually delivered 9 jobs + 1 research workflow + 1 ad-hoc inline workflow. The escalation from a single "worker rebuild" UI to a full debugging arc.
- **Cost telemetry:** total tokens (~1.44M), Opus vs Sonnet breakdown (2 Opus jobs + 1 research at 47% of spend; 7 Sonnet wins at 53%). For comparison: sprint-9 was 713K Sonnet-only.
- **3 false-success pattern + the breakthrough:** jobs 0070, 0074, 0075 all reported "headline screenshot captured" based on UI-chrome signals while the actual map canvas had ZERO overlay pixels. Job-0076 Opus broke the pattern with Playwright `page.on(request/response)` instrumentation that surfaced the silent `isStyleLoaded` early-bail.
- **Orchestrator lesson (binding for future sprints):** "screenshot captured" without pixel-level evidence in the map area is NOT verification. The bar is "% bluish pixels in the map area" or equivalent objective measurement, not "screenshot file exists + LayerPanel UI chrome populated".
- **Architectural wins:** sprint-10 added: contract field formalization (LayerURI.bbox + ProjectLayerSummary new fields); CRS_TAG_MISMATCH structural guard; client-side dark theme support; ws.ts production map-command routing; auto-dispatch publish_layer overrides fix; Cloud Run scaling block drift reconciliation; CartoDB DarkMatter dark-theme toggle.
- **Open OQ carry-forward list:**
  - **OQ-76-MAP-ALIGNMENT** (NEW; sprint-11 priority): user-observed alignment + rotation + zoom visible issues on the flood overlay vs basemap. Likely roots: MapLibre `bounds` on the WMS raster source; tileSize:256 vs basemap tile scale at zoom-13; EPSG:32617 → EPSG:3857 reprojection at the layer's small extent.
  - **OQ-76-CARTO-RATE-LIMIT** (sprint-11/12): CartoDB free tier may be rate-limited in production; need paid key OR self-hosted dark basemap OR alternative dark tiles.
  - **OQ-76-MAPCMD-WS** (sprint-11 small): production WebSocket envelope routing for `map-command` (dev-injection works; production agent-emitted zoom-to won't reach client).
  - **OQ-72-LAYERURI-WMS-FIELD** (next schema sprint): formalize `wms_url` on `LayerURI`.
  - **OQ-71-SQUARE-GRID-ROTATION** (low; only if HydroMT changes dim conventions).
  - **OQ-74-TSC-WS-TEST-ERRORS** (low; pre-existing tsc errors in FROZEN ws.test.tsx).
  - **OQ-74-KICKOFF-WORKER-OP-MISMATCH** (doc fix in kickoff template).
  - **v0.3.22 SRS housekeeping** (orchestrator-direct, sprint-11 opener).

#### Part 4 — Author sprint-11 manifest

Create `reports/sprints/sprint-11.md` per the saved roadmap (`memory/project_post_sprint_10_roadmap.md`):

- **Headline:** FR-MP-6 Case UX implementation (per user direction 2026-06-07). Three-pane shell behavior: Cases list left / Chat right when no Case open; in-Case state shows loaded layers list left; Cases creation on first agent prompt; per-Case persistence into `projects` collection; chat history persistence into `sessions` collection; back-to-Cases nav resets chat to fresh agent context.
- **Maintenance carry-forwards:** all the sprint-10 OQs above, especially **OQ-76-MAP-ALIGNMENT** as a sprint-11 priority since the user flagged it on 2026-06-08.
- **Hillshade as atomic tool** — user bumped up the roadmap on 2026-06-08; if not the headline then at least a parallel-stage job.
- **Sprint-12+ deferred** (per roadmap): Pelicun + Secrets UX + Mode 2 `.gov`/`.edu` + more atomic tools (bundled).
- **Sprint-13+ deferred**: 8 deferred engines.
- **Indefinitely deferred** until user-defined case: ATCF Hurricane Ian real forcing.

#### Part 5 — Final close

In `reports/sprints/sprint-10.md`:
- Status: closed
- Closed: 2026-06-08

Single commit (`testing: job-0077 sprint-10 acceptance + close + sprint-11 manifest`).

### File ownership (exclusive)

- `reports/inflight/job-0077-testing-20260608/` — your report + evidence
- `reports/sprints/sprint-10.md` — Retrospective section + Status close
- `reports/sprints/sprint-11.md` (NEW) — manifest authored per the saved roadmap

### FROZEN

- All source files (no code edits in testing/acceptance)
- All prior approved reports in `reports/complete/`
- `docs/decisions/`, `docs/srs/`
- `reports/PROJECT_LOG.md` + `reports/cost_tracking.json` (orchestrator owns)
- `memory/` (orchestrator owns)

### Acceptance criteria

- [ ] All 4 test suites pass (web 72+ / agent 187+ / contracts 145+ / worker 13+)
- [ ] Playwright tests run; pass status documented (failures noted as sprint-11 carry-forward, not fixed)
- [ ] Sprint-10 retrospective written: planned vs actual + cost telemetry + the 3-cycle false-success pattern + orchestrator lesson + architectural wins + OQ carry-forward list
- [ ] Sprint-11 manifest authored with FR-MP-6 Case UX headline + sprint-10 carry-forwards + sprint-12/13 deferred items per the roadmap
- [ ] Sprint-10.md Status: closed; Closed: 2026-06-08
- [ ] Single commit
