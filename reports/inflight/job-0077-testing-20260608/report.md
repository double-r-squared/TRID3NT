# Report: Sprint-10 acceptance + close + sprint-11 hand-off

**Job ID:** job-0077-testing-20260608
**Sprint:** sprint-10 close
**Specialist:** testing
**Task:** Full regression sweep (4 suites); Playwright dev-injection acceptance; sprint-10 retrospective in-place; author sprint-11 manifest; final close sprint-10.md.
**Status:** ready-for-audit

---

## Summary

All 4 regression suites pass at or above threshold (web 72/72, agent 187/187, contracts 145/145, pyqgis 13/13). Playwright tests 1-3 of 4 pass; test 4 (test_panels_collapsed_e2e) fails due to the sprint-10 layout reversion removing the `grace2-left-panel-slot` / `grace2-right-panel-slot` / `grace2-map-area` data-testids from App.tsx — tagged as sprint-11 carry-forward per kickoff. Sprint-10 retrospective written in-place in `reports/sprints/sprint-10.md` covering the 3-cycle false-success pattern, cost telemetry, orchestrator lesson, and all 7 OQ carry-forwards. Sprint-11 manifest authored as `reports/sprints/sprint-11.md` with FR-MP-6 Case UX as headline, OQ-76-MAP-ALIGNMENT as sprint-11 priority, compute_hillshade as parallel atomic tool, and sprint-12/13 deferred items per the saved roadmap.

---

## Changes Made

- **`reports/sprints/sprint-10.md`**:
  - Status updated from `planned` to `closed`; Closed date added: `2026-06-08`
  - `## Retrospective` section filled in (planned vs actual; cost telemetry; 3-cycle false-success pattern; orchestrator lesson; architectural wins; OQ carry-forward list)

- **`reports/sprints/sprint-11.md`** (NEW):
  - Sprint-11 manifest authored per the saved memory roadmap `project_post_sprint_10_roadmap.md`
  - Headline: FR-MP-6 Case UX implementation; secondary jobs: Map alignment fix, compute_hillshade, OQ-76-MAPCMD-WS, Case persistence backend; closes with sprint-11 testing acceptance
  - Deferred items per roadmap: sprint-12 (Pelicun + Secrets UX + Mode 2), sprint-13+ (8 engines), indefinitely deferred (ATCF real forcing)

- **`reports/inflight/job-0077-testing-20260608/STATE`**: `created` to `in-progress` to `ready-for-audit`

- **`reports/inflight/job-0077-testing-20260608/evidence/`** (6 files):
  - `01_web_test_suite.txt` -- verbatim 72/72 pass output
  - `02_agent_test_suite.txt` -- verbatim 187/187 pass output (1 skipped, 4 warnings)
  - `03_contracts_test_suite.txt` -- verbatim 145/145 pass output
  - `04_pyqgis_worker_test_suite.txt` -- verbatim 13/13 pass output
  - `05_tsc_noEmit.txt` -- qualified: 3 pre-existing errors in frozen ws.test.tsx (OQ-74-TSC-WS-TEST-ERRORS); production source files clean
  - `06_playwright_acceptance.txt` -- 3/4 pass; test_panels_collapsed_e2e failure diagnosed + documented as sprint-11 carry-forward

---

## Decisions Made

- **Decision: test_panels_collapsed_e2e failure is sprint-11 carry-forward, not fixed here.**
  - Rationale: per kickoff Part 2 -- "If they fail because of sprint-10's layout changes, document the failures honestly and note as sprint-11 carry-forward. Don't try to fix tests in this acceptance job."
  - The failure is at the data-testid level (structural DOM change), not a logic regression -- the overlay-panel layout from job-0068 removed the flex-row slot elements the test relied on.
  - The passing 3 tests (baseline, pipeline cards, flood layer) confirm core session-state injection, LayerPanel, and LayerLegend behaviors are intact.

- **Decision: pyqgis worker tests run without PYTHONPATH override of services/workers/pyqgis.**
  - Rationale: adding `services/workers/pyqgis` to PYTHONPATH causes `types.py` in that directory to shadow the Python stdlib `types` module, producing a circular import at Python startup. The correct invocation (from repo root, conftest.py handles stub injection) runs all 13 tests cleanly.
  - This matches the invocation pattern confirmed in `reports/complete/job-0074-engine-20260607/report.md`.

- **Decision: TSC errors in ws.test.tsx documented as qualified, not as sprint-11 must-fix.**
  - Rationale: OQ-74-TSC-WS-TEST-ERRORS explicitly tags these as pre-existing and low-priority in the kickoff. The errors are in a test file only; production source is clean. Sprint-11 testing acceptance can close this.

---

## Invariants Touched

- **Testing-only job.** No source code modified. All invariants preserved by non-modification.

---

## Open Questions

- **OQ-77-PLAYWRIGHT-LAYOUT**: test_panels_collapsed_e2e needs to be rewritten for the overlay-panel layout. New data-testids for collapse are `grace2-layers-hamburger` and `grace2-chat-hamburger`. New collapse behavior shows/hides overlay panels rather than measuring flex widths. Sprint-11 testing acceptance job should close this.

---

## Dependencies and Impacts

- **Depends on:** all sprint-10 approved jobs (0068 through 0076)
- **Produces for orchestrator:** sprint-10 acceptance record (this report + evidence); sprint-10.md retrospective + close; sprint-11.md manifest ready for orchestrator to open specific job IDs at dispatch.
- **Affects:**
  - Sprint-11: manifest here is the draft; orchestrator assigns specific job IDs when dispatching
  - OQ-77-PLAYWRIGHT-LAYOUT: sprint-11 testing job owns the test_panels_collapsed_e2e rewrite

---

## Verification

### Test suite results summary

| Suite | Command | Result | Count | Threshold |
|-------|---------|--------|-------|-----------|
| Web (vitest) | `cd web && npm run test -- --run` | PASS | 72/72 | 72+ |
| Agent (pytest) | `PYTHONPATH=services/agent/src:packages/contracts/src .venv-agent/bin/python -m pytest services/agent/tests/ -q` | PASS | 187/187 (1 skip) | 187+ |
| Contracts (pytest) | `PYTHONPATH=packages/contracts/src .venv-agent/bin/python -m pytest packages/contracts/tests/ -q` | PASS | 145/145 | 145+ |
| PyQGIS worker (pytest) | `.venv-agent/bin/python -m pytest services/workers/pyqgis/tests/test_worker_raster.py -v` | PASS | 13/13 | 13+ |
| TypeScript (tsc) | `cd web && npx tsc --noEmit` | QUALIFIED | 3 errors in ws.test.tsx (pre-existing OQ-74) | clean on owned files |

### Playwright acceptance results

| Test | Status | Notes |
|------|--------|-------|
| test_baseline_empty | PASS | LayerPanel absent; Chat visible; 0 pipeline cards |
| test_mid_run_pipeline_cards | PASS | 3 cards; cancel button active; step names match fixture |
| test_final_flood_layer | PASS | LayerPanel + LayerLegend appear; legend title/tick labels correct; gradient bar visible |
| test_panels_collapsed_e2e | FAIL | data-testid="grace2-left-panel-slot" not found -- sprint-10 layout reversion removed flex-row slots. Sprint-11 carry-forward. |

### Live E2E evidence

All 6 evidence files in `reports/inflight/job-0077-testing-20260608/evidence/` contain verbatim command + output transcripts confirming all test runs.

### Results

**Overall: pass (qualified)**

- All 4 regression suites: pass at or above threshold
- Playwright: 3/4 pass; 1 failure is a known sprint-10 layout carry-forward per kickoff scope (not a regression in tested logic)
- TSC: qualified (ws.test.tsx pre-existing, production source clean)
- Sprint-10 retrospective: written
- Sprint-11 manifest: authored
- Sprint-10 status: closed (2026-06-08)
