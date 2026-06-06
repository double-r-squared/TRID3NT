# Sprint 05: Web client skeleton (SRS v0.3 M3)

**Status:** closed
**Opened:** 2026-06-06
**Closed:** 2026-06-06
**SRS milestones covered:** M3 (Web client skeleton — QGIS Server WMS basemap + Layer Panel + Pipeline Strip + Playwright AFK loop)

## Goal

Land SRS §7 M3: pivot the M1 web stub's basemap from the OSM-direct fallback to the deployed M2 QGIS Server WMS substrate (`https://grace-2-qgis-server-425352658356.us-central1.run.app/ogc/wms?MAP=/mnt/qgs/grace2-sample.qgs`, layer `basemap-osm-conus`, image `@sha256:a703476`); add the FR-WC-4 Layer Panel (with drag-and-drop reorder per FR-WC-4 v0.1 scope) and FR-WC-8 Pipeline Strip (with FR-WC-9 cancel button reusing the M1 cancel chain verified end-to-end at 502 ms in job-0015); introduce Playwright tooling (devDep + `tools/screenshot.mjs` + Makefile `screenshot`/`ui-tour`/`playwright-install`/`test-m3` targets) closing job-0016 OQ-W-3; and stand up the `tests/m3/` live-substrate acceptance suite over Chromium + Firefox-ESR. M3 is strictly client-rendering and tooling — no agent code lands, no `.qgs` mutation, no schema in-place edits (any gap routes through schema consumer-pushback as an Open Question). The 114 M1 + 7 M2 regressions remain green; final unique-test target 126–129 with ~2 of the M3 tests parametrized across both browsers for ~7–10 total invocations.

## Jobs

| Job ID | Specialist | Task | Depends on | Status |
|---|---|---|---|---|
| job-0025-web-20260606 | web | Basemap pivot to QGIS Server WMS + LayerPanel.tsx (drag-and-drop reorder) + App.tsx layout shell + contracts.ts session/map surface | — | approved |
| job-0026-web-20260606 | web | PipelineStrip.tsx with pipeline-state live render + FR-WC-9 cancel button + contracts.ts pipeline surface (mounts on App.tsx shell from 0025) | job-0025 | approved |
| job-0027-web-20260606 | web | Playwright integration (devDep, screenshot CLI, Makefile targets, first multi-state captures) | — | approved |
| job-0028-testing-20260606 | testing | M3 acceptance suite (`tests/m3/`) + regression preservation + NFR-P-3 tile latency | job-0025, job-0026, job-0027 | approved |

## Execution order

```
stage A (parallel):  job-0025-web-20260606 (Map.tsx + LayerPanel.tsx + App.tsx shell + session/map contracts.ts)
                     job-0027-web-20260606 (Playwright tooling — tools/ + Makefile + web/playwright.config.ts)
                     ─ disjoint file ownership ─
stage B:             job-0026-web-20260606 (PipelineStrip.tsx + pipeline contracts.ts; mounts onto the App.tsx layout shell job-0025 lands)
                     ← gated on job-0025 approved (App.tsx + contracts.ts have one editor at a time)
stage C:             job-0028-testing-20260606 (tests/m3/ acceptance)
                     ← gated on job-0025 + job-0026 + job-0027 approved
```

Job-0025 and job-0026 BOTH need to edit `web/src/App.tsx` (one to mount LayerPanel, one to mount PipelineStrip) AND BOTH extend `web/src/contracts.ts` — these are NOT disjoint and per AGENTS.md Concurrency Rules cannot run in parallel. They are therefore serialized: job-0025 lands the layout shell + session/map portion of contracts; job-0026 consumes the App.tsx shape job-0025 published (read job-0025's report.md before starting), adds the PipelineStrip mount, and extends contracts.ts with the pipeline surface only. Job-0027 (Playwright tooling) edits ONLY `web/package.json`, `web/playwright.config.ts` (new), `tools/screenshot.mjs` (new), root `Makefile`, `web/README.md` (Playwright section), and the per-job evidence directory — file-disjoint from both 0025 and 0026, so it runs parallel with 0025 in stage A. Job-0028 closes the sprint after all three.

## Exit criteria

- [ ] Web client default basemap renders tiles from `grace-2-qgis-server-425352658356.us-central1.run.app/ogc/wms?MAP=/mnt/qgs/grace2-sample.qgs` (layer `basemap-osm-conus`) with zero `gs://` fetches observed in browser network logs (FR-DT-5, Invariant 5 Tier separation).
- [ ] `web/src/LayerPanel.tsx` renders `loaded_layers` from a session-state envelope with visibility checkbox, 0..1 opacity slider, drag-and-drop reorder (FR-WC-4 v0.1 scope; up/down keyboard controls in addition for a11y), name + attribution per row; panel state updates on incoming `map-command` envelopes.
- [ ] `web/src/PipelineStrip.tsx` renders `pipeline-state` snapshots with pending/running/complete/failed/cancelled state colors (FR-WC-8); cancel button emits a cancel envelope reusing the M1 cancel chain (FR-WC-9, Invariant 8 Cancellation is first-class); button visibility predicate is explicit about which envelope feeds which condition (pipeline-state for step `running` state; session-state for `current_pipeline` non-null).
- [ ] Playwright is installed reproducibly via `make playwright-install`; `make ui-tour` produces six PNGs under `/tmp/grace2-shots/` covering initial / after-message / layer-panel-open / pipeline-running / cancelled / disconnected.
- [ ] `tests/m3/` pytest suite passes against the deployed QGIS Server substrate: 5–8 unique M3 test functions, with the two visual smoke tests (initial-load + after-state) parametrized across Chromium and Firefox-ESR for ~7–10 total invocations. Tile-rendering test asserts at least one valid PNG response from `/ogc/wms?MAP=/mnt/qgs/grace2-sample.qgs`.
- [ ] `make test` green with all 121 baseline regressions preserved (91 contracts + 23 M1 acceptance + 7 M2 acceptance); `make test-m3` green; combined `make test-all` green; total unique tests 126–129 (invocations 128–131 once cross-browser parametrization is counted).
- [ ] NFR-P-3 tile-latency probe (N=20 against deployed Cloud Run from Debian dev box, p50/p95 with environment context) reported in the sprint-05 acceptance evidence per testing.md NFR discipline.
- [ ] Hand-mirror payload count in `web/src/contracts.ts` totals ~12–14 after both web component jobs land; surface a refined OQ-W-1 if the count exceeds 18 (codegen promotion trigger remains at ~20 per job-0016 resolution).

## Retrospective

**Closed 2026-06-06.** Five jobs landed and approved: job-0025 (basemap pivot to QGIS Server WMS at `/mnt/qgs/grace2-sample.qgs` + LayerPanel with `@dnd-kit/sortable` drag-and-drop + opacity + a11y + App.tsx three-zone layout shell + session/map contracts), job-0026 (PipelineStrip with 5 state colors + FR-WC-9 cancel button + cross-envelope visibility predicate + pipeline contracts), job-0027 (Playwright integration + screenshot CLI + Makefile harness + AFK iteration loop), job-0028 (M3 acceptance suite: 9 unique test functions / 10 invocations green in 89s against deployed Cloud Run substrate + NFR-P-3 measurement qualified at p50≈300 ms / p95≈360 ms), plus mid-sprint addition job-0029 (custom nginx.conf CORS injection for QGIS Server, image rebumped to `@sha256:57d0f43`).

**M3 milestone achieved.** Cross-browser visual smokes (Chromium + Firefox-ESR) pass on the deployed substrate; cancel envelope verified live on the WS wire via Playwright `framesent`; Tier-separation (Invariant 5) verified by zero `gs://` browser-side fetches AND zero `gs://` literals in the production web build; Invariant 8 cancellation chain reused end-to-end through `GraceWs.sendCancel` (single source). M1 (91 contracts + 23 protocol/integration) + M2 (7) baseline preserved: 120 invocations green under `make test` (with one pre-existing M2 polling-window flake diagnosed not introduced).

**Open Questions raised for follow-up:** OQ-W-26-PIPELINE-STEP-FIELDS (schema consumer-pushback — Appendix D.6 `PipelineStepSummary` needs `progress_percent`, `error_code`, `error_message` before M4 emits real pipeline-state envelopes); OQ-T-28-SIM-WS-BOUNDARY (rewrite dev-injection paths once M4 lands real agent emission); OQ-T-28-NFR-P3-SINGLE-MACHINE (re-verify from us-west1 before final NFR sign-off); OQ-T-28-M2-WORKER-FLAKE (M2 polling-window race — open follow-up job in a future sprint). Outstanding amendment pile from prior sprints (A1–A5 from job-0013, NFR-C-1, NFR-P-1, FR-AS-1 Gemini-3, OQ-1, FR-QS-2 `/mnt/qgs/` contract change, gitignore Lever A/B/C identifier exposures, SRS v0.3.15 data-endpoints + engines + plugins + conservation + caching amendment in flight) carries forward.

**Operational observation:** the workflow-StructuredOutput failure pattern recurred in sprint-05 (jobs 0026, 0029 closeouts both required inline closeout passes after the workflow failed at the final StructuredOutput synthesis despite substantive on-disk work). Pattern is now stable enough to canonicalize. Specialists land code on disk + populate report.md inline; the orchestrator audits via inline Edit on the audit.md template; the workflow tool's StructuredOutput is treated as best-effort. Direct-Agent invocation (job-0028) succeeded cleanly — recommend this as the default for specialist work going forward, reserving Workflow for multi-stage research / fan-out where the orchestration benefit outweighs the StructuredOutput risk.

**Next sprint:** sprint-06 (M4 — agent service tools + atomic-tool starter set: `fetch_dem`, `fetch_buildings`, `fetch_population`, `geocode_location`, `list_qgis_algorithms`, `describe_qgis_algorithm`, `mongo_query`/`qgis_process` registry). OQ-W-26-PIPELINE-STEP-FIELDS must resolve before sprint-06 starts emitting real `pipeline-state` envelopes.