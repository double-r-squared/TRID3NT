# Audit: Playwright integration — devDep + screenshot CLI + Makefile targets + multi-state captures

**Job ID:** job-0027-web-20260606, **Sprint:** sprint-05, **Auditor:** Development Orchestrator, **Status:** assigned

## Task Assignment

**Specialist:** web

**Prerequisites:**
- job-0016 (M1 web stub: Vite dev server, baseURL convention via `VITE_GRACE2_WS_URL`; closes OQ-W-3 here as a side effect)
- job-0024 (M2 deployed QGIS Server — provides the substrate the screenshots paint against)

**SRS references:** §7 M3; FR-WC-1 (NFR-PO-1 spot-check Chromium + Firefox-ESR); FR-WC-2 (visual verification of MapLibre + QGIS Server WMS rendering); supports FR-WC-4 / FR-WC-8 acceptance via screenshots; testing.md (live E2E discipline — real Vite dev server, real Cloud Run substrate, headless real WebGL paint).

### Environment
Linux Debian dev + prod (linux/amd64). Playwright drives Chromium (project-owned Chrome-for-Testing install per memory file, closing OQ-W-3 on fresh dev boxes) and Firefox-ESR. Vite dev server runs locally. The headless browsers paint real WebGL against the live deployed QGIS Server WMS substrate (`https://grace-2-qgis-server-425352658356.us-central1.run.app/ogc/wms?MAP=/mnt/qgs/grace2-sample.qgs`, image `@sha256:a703476`). Screenshot artifacts land in `/tmp/grace2-shots/` (already gitignored, verified) for the AFK loop; canonical evidence captures may also land under `reports/inflight/<job-id>/evidence/` per AGENTS.md memory file pattern.

### Scope
1. `web/package.json`: add `@playwright/test` as devDep (latest stable). No runtime dependency changes.
2. `web/playwright.config.ts` (NEW): viewport 1440 x 900; projects = `chromium` + `firefox` (Firefox-ESR via Playwright's Firefox channel); `baseURL` from `VITE_GRACE2_WS_URL` companion env or the Vite dev server URL; screenshot output directory `/tmp/grace2-shots/`.
3. `tools/screenshot.mjs` (NEW): CLI that opens a route, optionally drives the app via WebSocket fixture or DOM injection (`--state=initial|after-message|layer-panel-open|pipeline-running|cancelled|disconnected`), captures PNG to `--out`. Honors the memory-file pattern for naming and path conventions.
4. Root `Makefile` additions (additive only — do not refactor existing targets):
   - `make screenshot ROUTE=... STATE=... OUT=...` — single capture.
   - `make ui-tour` — walks the six-state sequence (initial / after-message / layer-panel-open / pipeline-running / cancelled / disconnected) producing six PNGs in `/tmp/grace2-shots/` with deterministic filenames.
   - `make playwright-install` — `npx playwright install chromium firefox` reproducibly (closes OQ-W-3).
   - `make test-m3` — invokes the M3 pytest suite (defined in job-0028); placeholder target that the testing job will populate the assertion side of, but the Make target itself lands here so all four targets ship together. The wired pytest path is `pytest tests/m3/`.
5. `web/README.md` (additive only): one short section pointing at `make playwright-install` and `make ui-tour`; do not rewrite existing README content. Do not edit it in the part touched by job-0025 (the `VITE_GRACE2_WMS_URL` env var note) — append a separate Playwright section beneath.
6. First multi-state captures: commit one canonical PNG per state under `reports/inflight/job-0027-web-20260606/evidence/` (per-job evidence trail). `/tmp/grace2-shots/` remains the ephemeral AFK target, gitignored.

### File ownership (exclusive)
- `web/package.json` (devDeps only; do not modify runtime deps or scripts beyond a `playwright` script alias if needed)
- `web/playwright.config.ts` (NEW)
- `tools/screenshot.mjs` (NEW)
- `Makefile` (root — additive targets only: `screenshot`, `ui-tour`, `playwright-install`, `test-m3`)
- `web/README.md` (additive Playwright section only — append, do not edit job-0025's `VITE_GRACE2_WMS_URL` section)
- `reports/inflight/job-0027-web-20260606/evidence/*.png`
- `.gitignore` (verify `/tmp/grace2-shots/` already ignored; do not duplicate)

### FROZEN — no edits in this job
- `packages/contracts/**` (no schema-side edits this sprint)
- `services/agent/**` (M4 work)
- `services/workers/**` (M2 owned)
- `infra/**` (M2 owned)
- `docs/SRS_v0.3.md` (user-owned)
- `styles/**` (engine-owned)
- `reports/complete/**` (immutable)
- `web/src/Map.tsx`, `web/src/LayerPanel.tsx`, `web/src/PipelineStrip.tsx`, `web/src/Chat.tsx`, `web/src/ws.ts`, `web/src/contracts.ts`, `web/src/App.tsx` (all reserved to jobs 0025/0026; this job is tooling-only, no `web/src/**` source edits)
- `tests/m3/**` (testing-owned by job-0028)

### Cross-cutting principles in force (cited by NUMBER+name from agents/orchestrator.md)
- **Invariant 5 (Tier separation)** — screenshots paint against the deployed Cloud Run substrate, not a stub. Where component states (LayerPanel populated, PipelineStrip running) require WS-injected fixture data because the agent does not yet emit those envelopes, document the boundary explicitly per testing.md.
- ***Diagnose before fix* (cross-cutting principle)** — if a browser fails to launch on the Debian box, capture the actual error before re-running install scripts.
- **Surface uncertainty as Open Questions** — TENTATIVE choices below surface as Open Questions.
- **No legacy support pre-MVP** — no `puppeteer` or alternate browser-automation fallback; Playwright only.
- **Remove don't shim** — if a placeholder `screenshot.sh` exists from M1 scaffolding, replace it; do not coexist.
- **Bundle small fixes** — if the existing `Makefile` has trivial `.PHONY` cleanup needed to add four targets cleanly, fix it here (bounded by the FROZEN list).

### Acceptance criteria (reviewer re-runs)
- [ ] `make playwright-install` succeeds on a fresh Debian dev box (closes job-0016 OQ-W-3 reproducibly).
- [ ] `make ui-tour` produces six PNGs under `/tmp/grace2-shots/` with deterministic filenames covering initial / after-message / layer-panel-open / pipeline-running / cancelled / disconnected.
- [ ] `make screenshot ROUTE=/ STATE=initial OUT=/tmp/grace2-shots/initial.png` produces a valid PNG (magic bytes check).
- [ ] `web/playwright.config.ts` declares Chromium + Firefox-ESR projects with viewport 1440 x 900.
- [ ] `tools/screenshot.mjs` honors `--state`, `--out`, `--route` flags.
- [ ] `web/package.json` shows `@playwright/test` in `devDependencies` only.
- [ ] `reports/inflight/job-0027-web-20260606/evidence/` contains the six canonical PNGs from `make ui-tour`.
- [ ] `/tmp/grace2-shots/` remains gitignored (no committed paths under it).
- [ ] `make test-m3` target exists and invokes `pytest tests/m3/` (the assertion side is job-0028's deliverable; this target wires the invocation only).
- [ ] No edits to `web/src/Map.tsx`, `web/src/LayerPanel.tsx`, `web/src/PipelineStrip.tsx`, `web/src/Chat.tsx`, `web/src/ws.ts`, `web/src/contracts.ts`, `web/src/App.tsx`, or any FROZEN path listed above.

Surface contestable choices as Open Questions with TENTATIVE tags — at minimum: ephemeral `/tmp` vs per-PR `reports/inflight/` retention policy, CI runner integration deferral to post-M3 infra sprint, Safari spot-check still deferred (no macOS substrate), Firefox-ESR channel vs Firefox-Developer-Edition choice.

## Assessment

## Invariant Check

## Dependency Check

## Decisions Validated

## Open Questions Resolved

## Follow-up Actions

## Sign-off
