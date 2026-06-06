# Audit: M3 acceptance suite (tests/m3/) + regression preservation + NFR-P-3 tile latency

**Job ID:** job-0028-testing-20260606, **Sprint:** sprint-05, **Auditor:** Development Orchestrator, **Status:** assigned

## Task Assignment

**Specialist:** testing

**Prerequisites:**
- job-0025-web-20260606 (basemap pivot to QGIS Server WMS + LayerPanel.tsx + App.tsx layout shell + session/map contracts.ts — provides the WMS-tile and layer-panel surfaces under test)
- job-0026-web-20260606 (PipelineStrip.tsx with pipeline-state live render + FR-WC-9 cancel button + pipeline contracts.ts — provides the pipeline-strip surface and cancel envelope under test)
- job-0027-web-20260606 (Playwright integration: devDep + screenshot CLI + Makefile `playwright-install` / `ui-tour` / `test-m3` targets — provides the harness)
- job-0015 (M1 cancel chain agent-side reference: 502 ms)
- job-0024 (M2 deployed QGIS Server — substrate under test)

**SRS references:** §7 M3; FR-WC-1 (Chromium + Firefox-ESR cross-browser); FR-WC-2 / FR-WC-4 / FR-WC-8 / FR-WC-9; FR-DT-2 / FR-DT-3 / FR-DT-5; NFR-P-3 (tile-latency carry-forward OQ-23E); Appendix A `session-state`, `map-command`, `pipeline-state`, `cancel`; Appendix D.2 `ProjectLayerSummary`, D.6 `PipelineSnapshot` / `PipelineStepSummary` / `MapView`.

### Environment
Linux Debian dev host. Real Vite dev server. Real deployed Cloud Run QGIS Server (`https://grace-2-qgis-server-425352658356.us-central1.run.app/ogc/wms?MAP=/mnt/qgs/grace2-sample.qgs`, image `@sha256:a703476`, layer `basemap-osm-conus`, us-central1) consumed live from `PROJECT_STATE.md`. Headless Playwright (Chromium + Firefox-ESR) captures real WebGL paint. The simulated WS server is permitted ONLY for `layer-panel` and `pipeline-strip` state-seeding tests because the agent does not yet emit `session-state` with populated `loaded_layers` or `pipeline-state` envelopes in M3 (M4 work); the WMS-tile test hits the real QGIS Server with no simulation; the cancel-envelope test hits the real M1 cancel chain. Failure-naming discipline: every assertion attributes failure to web client, agent, QGIS Server, or network.

### Scope
1. Create harness layout:
   - `tests/m3/__init__.py`
   - `tests/m3/conftest.py` — fixtures for Vite dev server lifecycle, headless browser launch (Chromium + Firefox via Playwright), simulated WS server emitting Appendix A envelopes for component state seeding, artifacts dir `tests/m3/artifacts/`.
   - `tests/m3/fixtures/session-state-seeded.json` — sample `session-state` with 2–3 `ProjectLayerSummary` rows AND a non-null `current_pipeline` field for cancel-button visibility test.
   - `tests/m3/fixtures/pipeline-state-running.json`, `tests/m3/fixtures/pipeline-state-cancelled.json`, `tests/m3/fixtures/pipeline-state-failed.json` — sample snapshots using `PipelineStepSummary` (canonical Appendix D.6 name).
2. Tests (5–8 UNIQUE test functions; cross-browser parametrization rules in Scope item 4):
   - `tests/m3/test_wms_tiles.py` — open web client; wait for tile network requests; assert at least one `/ogc/wms?MAP=/mnt/qgs/grace2-sample.qgs` tile response with valid PNG magic bytes; anti-control: assert zero `gs://` fetches (FR-DT-5, Invariant 5 Tier separation). **Parametrized across Chromium + Firefox-ESR** (visual smoke #1, initial-load).
   - `tests/m3/test_layer_panel.py` — drive `session-state-seeded.json` through simulated WS; assert panel renders seeded rows; toggle a visibility checkbox; assert local intent fires (console captured); screenshot the populated panel. **Parametrized across Chromium + Firefox-ESR** (visual smoke #2, after-state).
   - `tests/m3/test_pipeline_strip.py` — drive sequence: `running` → `complete`; then `running` → cancel-click → assert real cancel envelope on WS wire → `cancelled`; assert state colors; verify cross-envelope cancel-button visibility predicate (combined `pipeline-state` step `running` AND `session-state.current_pipeline` non-null); screenshot per state. **Chromium only.**
   - `tests/m3/test_screenshot_smoke.py` — invoke `make ui-tour`; assert six PNGs produced under `/tmp/grace2-shots/` with expected names; smoke for the AFK loop tooling. **Chromium only** (`make ui-tour` already exercises both browsers per its config).
   - `tests/m3/test_wms_tile_latency.py` — NFR-P-3 measurement: p50/p95 of GetMap response time over N=20 tile requests against deployed Cloud Run from the Debian dev box; report with environment context (Linux x86_64, us-central1, cold-vs-warm). Honest qualified-status acceptable if methodology is single-machine, not cloud-region-aware — note the limitation in the assertion message per testing.md NFR discipline. **Chromium only** (Python HTTP client; not browser-dependent).
3. Regression preservation:
   - `make test` continues to run M1 (114 tests: 91 contracts + 23 acceptance) + M2 (7 acceptance) = 121 baseline.
   - `make test-m3` runs the new M3 suite (5 unique functions, ~7–10 invocations once parametrization counts).
   - `make test-all` runs everything (target final unique 126–129 functions; ~128–131 total invocations).
4. **Cross-browser scope clarification** (resolves prior ambiguity): a SUBSET of M3 tests — the two visual smoke tests (`test_wms_tiles.py` initial-load + `test_layer_panel.py` after-state) — runs under BOTH Chromium and Firefox-ESR via pytest parametrize. The remaining three tests (`test_pipeline_strip.py`, `test_screenshot_smoke.py`, `test_wms_tile_latency.py`) run under Chromium only. Totals: 5 unique test functions, ~7–10 invocations (2 functions × 2 browsers + 3 functions × 1 browser = 7 invocations; up to ~10 if additional fine-grained parametrize over fixture snapshots within `test_pipeline_strip.py`). Manifest exit-criterion `126–129 total` references unique-function counts; invocation count is reported alongside.
5. Failure-naming discipline (testing.md): every assertion message includes the layer attribution (`web client | agent | QGIS Server | network`).
6. `tests/m3/README.md` (additive, minimal): one paragraph naming `make playwright-install`, `make test-m3`, the simulated-WS-boundary rationale, and the Chromium-vs-cross-browser test split.
7. `tests/m3/artifacts/` is gitignored except a single canonical PNG per state committed under `reports/inflight/job-0028-testing-20260606/evidence/` for the audit trail.

### File ownership (exclusive)
- `tests/m3/__init__.py`
- `tests/m3/conftest.py`
- `tests/m3/test_wms_tiles.py`
- `tests/m3/test_layer_panel.py`
- `tests/m3/test_pipeline_strip.py`
- `tests/m3/test_screenshot_smoke.py`
- `tests/m3/test_wms_tile_latency.py`
- `tests/m3/fixtures/*.json`
- `tests/m3/artifacts/**` (gitignored beyond the canonical commits below)
- `tests/m3/README.md` (NEW, additive only)
- `reports/inflight/job-0028-testing-20260606/evidence/*` (canonical screenshots + latency report)
- `.gitignore` — verify `tests/m3/artifacts/` and `/tmp/grace2-shots/` ignored; do not duplicate existing rules.

### FROZEN — no edits in this job
- `packages/contracts/**` (schema-owned)
- `services/agent/**` (M4 work)
- `services/workers/**` (M2 owned)
- `infra/**` (M2 owned)
- `docs/SRS_v0.3.md` (user-owned)
- `styles/**` (engine-owned)
- `reports/complete/**` (immutable per AGENTS.md "Completed Job Immutability")
- `web/src/**` (web-owned, all editing windows closed by the time this job starts)
- `web/package.json`, `web/playwright.config.ts`, `web/README.md` (job-0027-owned, closed)
- `tools/screenshot.mjs` (job-0027-owned, closed)
- Root `Makefile` — read-only here. **If the `make test-m3` target is missing or mis-wired, do NOT silently add it and do NOT attempt to re-open job-0027 (AGENTS.md "Completed Job Immutability" forbids editing a closed job). Instead, surface the gap as an Open Question and open a NEW follow-up job (e.g. `job-0029-web-20260606`) tasked with adding the missing target; this M3 acceptance suite blocks on the new follow-up before resuming.** Same pattern for any other gap surfaced by closed prerequisite jobs.
- `web/src/Chat.tsx` (M1-owned, untouched)

### Cross-cutting principles in force (cited by NUMBER+name from agents/orchestrator.md)
- **Invariant 5 (Tier separation)** — WMS basemap, cancel envelope, and tile-latency tests hit the real deployed substrate. Simulated WS is permitted ONLY for component state-seeding where the agent does not yet emit (testing.md "mocks live ONLY at external boundaries" — this is technically an internal seam, surface as Open Question with the rationale that the agent surface is M4).
- ***Diagnose before fix* (cross-cutting principle)** — if a tile-rendering test fails, capture the failing GetMap request/response before changing the assertion.
- **Invariant 8 (Cancellation is first-class)** — the pipeline-strip cancel test must verify the REAL cancel envelope on the WS wire, not a simulated cancel, reusing the M1 cancel chain.
- **Surface uncertainty as Open Questions** — TENTATIVE choices below surface as Open Questions.
- **No legacy support pre-MVP** — no parallel "M3 with mocked QGIS Server" path; the real Cloud Run is the substrate.
- **Remove don't shim** — if M2 acceptance scaffolding contains stubs the M3 suite would duplicate, delete the stubs and consolidate.
- **Bundle small fixes** — if `tests/m1/` or `tests/m2/` has a trivial flake fix that surfaces while shoring up regression preservation, ship it here with a one-line note rather than spawning a follow-up (bounded by the FROZEN list).
- **Completed Job Immutability** — closed jobs (0025/0026/0027) cannot be re-opened from here; gaps in their deliverables route through a NEW numbered follow-up job, not back into the closed job's audit.

### Acceptance criteria (reviewer re-runs)
- [ ] `make test` green: M1 (114) + M2 (7) = 121 baseline preserved.
- [ ] `make test-m3` green: 5 unique M3 test functions pass; total invocations ~7–10 once the two visual smoke tests' Chromium + Firefox-ESR parametrization is counted.
- [ ] `make test-all` green: combined 126–129 unique functions (~128–131 invocations).
- [ ] `test_wms_tiles.py` asserts at least one PNG response from `/ogc/wms?MAP=/mnt/qgs/grace2-sample.qgs` and zero `gs://` fetches (Invariant 5 Tier separation, FR-DT-5). Runs under Chromium AND Firefox-ESR.
- [ ] `test_layer_panel.py` renders seeded `session-state` rows under both Chromium and Firefox-ESR; screenshot committed under evidence dir.
- [ ] `test_pipeline_strip.py` verifies the real cancel envelope on the WS wire (not a simulated cancel); reuses job-0015's M1 cancel chain (Invariant 8 Cancellation is first-class); verifies the cross-envelope cancel-button visibility predicate (pipeline-state `running` step + session-state `current_pipeline` non-null union). Chromium only.
- [ ] `test_screenshot_smoke.py` confirms six PNGs from `make ui-tour`. Chromium only.
- [ ] `test_wms_tile_latency.py` reports p50/p95 over N=20 with explicit environment context per testing.md NFR discipline (NFR-P-3 / OQ-23E). Chromium only (Python HTTP client).
- [ ] Every assertion message names the failing layer (web client / agent / QGIS Server / network).
- [ ] Canonical evidence (screenshots + latency report) committed under `reports/inflight/job-0028-testing-20260606/evidence/`.
- [ ] `tests/m3/fixtures/*.json` use the canonical `PipelineStepSummary` name from Appendix D.6 — no occurrences of standalone `PipelineStep`.
- [ ] No edits to any FROZEN path listed above; any prerequisite-job gap routed as a NEW follow-up job (e.g. `job-0029`), NOT a re-open of a closed job.

Surface contestable choices as Open Questions with TENTATIVE tags — at minimum: simulated-WS-server boundary for layer-panel/pipeline-strip state seeding (testing.md mocks-at-boundaries vs internal-seam-with-rationale), NFR-P-3 single-machine measurement methodology limits, Safari spot-check deferral, ephemeral `/tmp/grace2-shots/` vs per-job evidence retention, Playwright CI runner integration deferral to post-M3 infra sprint, follow-up job-0029 if any prerequisite-job gap surfaces.

## Assessment

## Invariant Check

## Dependency Check

## Decisions Validated

## Open Questions Resolved

## Follow-up Actions

## Sign-off
