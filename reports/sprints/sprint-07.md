# Sprint 07: SFINCS engine v0 + WorldPop default flip (SRS v0.3 M5)

**Status:** active (Stage E in flight — job-0043 ready-for-audit; orchestrator closes)
**Opened:** 2026-06-06
**Closed:** —
**SRS milestones covered:** M5 (SFINCS solver containerization + first real engine integration; `model_flood_scenario` workflow composing the M4 atomic-tool substrate into a working hazard-modeling pipeline).

## Goal

Land the **first real engine integration** end-to-end: SFINCS flood solver deployed as a Cloud Run Job (per FR-CE-1/2/3); composed into the `model_flood_scenario` workflow that chains M4 atomic tools (geocode → DEM/landcover/buildings/precip return-period → SFINCS run → flood-depth COG → rendered layer); demonstrated with "Hurricane Ian flood on Fort Myers" as the M5 acceptance demo. Plus the WorldPop default flip from v0.3.16 Appendix F.1 (unblocks the M4 Fort Myers demo end-to-end with zero key).

Sprint-07 takes the M4 substrate (atomic-tool registry + cache shim + real envelope emission) and proves it can carry a real solver run. NFR-P-4 (15 min for ≤200km² at 30m) becomes load-bearing for the first time. The `run_solver` + `wait_for_completion` atomic tools exercise job-0035's `update_progress` opt-in seam for the first time — real long-running progress emission through the agent's WS pipeline-state envelopes.

**Engine deployment strategy: LAZY PER-MILESTONE (user-confirmed 2026-06-06).** Only SFINCS gets containerized + deployed in sprint-07. MODFLOW / HEC-HMS / TELEMAC / etc. (§2.3 deferred engines) wait until their respective milestone sprints. Avoids 12 idle Cloud Run Jobs (~$120/mo) + 12 image bakes ahead of need.

**No Discovery-First lane in this sprint** (§F.2 — `hazard_catalog_search` / `fetch_public_hazard_layer` / `summarize_layer_in_bbox`). Confirmed for sprint-08 per user direction.

**No `request_secret` UX** (§F.3 deferred indefinitely per user direction).

## Jobs

| Job ID | Specialist | Task | Depends on | Status |
|---|---|---|---|---|
| job-0037-engine-20260606 | engine | **Mini early job:** flip `fetch_population` default to WorldPop per Appendix F.1; ACS opt-in via `dataset="acs_2022"`; re-run Fort Myers demo to verify zero-key Tier-1 path works end-to-end | — | planned |
| job-0038-engine-20260606 | engine | **OQ-4 HydroMT integration depth decision** — research SFINCS + HydroMT setup pattern; propose depth (full HydroMT reliance vs custom config builders); land decision in a new `docs/decisions/oq-4-hydromt-depth.md` (Sonnet — research/summarize) | — | planned |
| job-0039-engine-20260606 | engine | 3 new fetcher atomic tools — `fetch_landcover` (NLCD via MRLC, ESA WorldCover via STAC; `static-30d`), `fetch_river_geometry` (NHDPlus HR; `static-30d`), `lookup_precip_return_period` (NOAA Atlas 14 PFDS; `static-30d`); all Tier-1 per Appendix F.1 | job-0037, job-0038 | planned |
| job-0040-infra-20260606 | infra | SFINCS solver container — Dockerfile (Deltares simvia base image or hand-built) + Cloud Build + Artifact Registry + Cloud Run Job `grace-2-sfincs-solver` + Cloud Workflows orchestration step + `sfincs-runtime@grace-2-hazard-prod` SA with bucket-scoped objectAdmin on cache + runs buckets + workflow-invoker IAM. Live `tofu apply` + smoke run | — | planned |
| job-0041-agent-20260606 | agent | `run_solver(solver, model_setup_uri, compute_class)` + `wait_for_completion(handle)` atomic tools wrapping Cloud Workflows execution; emits real `pipeline-state` progress updates via job-0035's `PipelineEmitter.update_progress` opt-in; reuses M1 cancel chain | job-0040 | planned |
| job-0042-engine-20260606 | engine | `model_flood_scenario(bbox, event_id?, return_period_yr?) → AssessmentEnvelope` workflow composing geocode → fetch_dem → fetch_landcover → fetch_river_geometry → lookup_precip_return_period → HydroMT model build → run_solver(sfincs) → postprocess_flood → AssessmentEnvelope. Closes OQ-36-QGIS-PROCESS-DEMO-CHAIN | job-0039, job-0041 | planned |
| job-0037-engine-20260606 (UPDATE: status) | engine | (as above) | — | approved |
| job-0038-engine-20260606 (UPDATE: status) | engine | (as above) | — | approved |
| job-0039-engine-20260606 (UPDATE: status) | engine | (as above) | job-0037, job-0038 | approved |
| job-0040-infra-20260606 (UPDATE: status) | infra | (as above) | — | approved |
| job-0041-agent-20260606 (UPDATE: status) | agent | (as above) | job-0040 | approved |
| job-0042-engine-20260606 (UPDATE: status) | engine | (as above) | job-0039, job-0041 | approved |
| job-0044-engine-20260607 (mid-sprint hotfix) | engine | NLCD WMS palette encoding hotfix — switch `fetch_landcover` from WMS GetMap to WCS 1.0.0 GetCoverage; unblocks job-0043 by delivering canonical NLCD class integers from the upstream. Closes OQ-42-NLCD-WMS-PALETTE-ENCODING | job-0042 | approved |
| job-0043-testing-20260606 | testing | M5 acceptance: end-to-end "Hurricane Ian flood on Fort Myers" demo through the deployed substrate. Real SFINCS run on Cloud Run; real flood-depth COG written to cache + runs buckets; real envelope rendered via QGIS Server. Per-tool cache verification (FR-DC-4 dedup); NFR-P-4 timing capture (target ≤15 min for ≤200 km²); full M1+M2+M3+M4+M5 regression. Closes sprint-07 | job-0042, job-0044 | ready-for-audit |

## Execution order

```
stage A (parallel, file-disjoint):
  job-0037-engine   (WorldPop default flip — tiny mechanical edit)
  job-0038-engine   (OQ-4 HydroMT depth decision — research/summarize)
  job-0040-infra    (SFINCS container + Cloud Run Job + IAM — heavy infra)

stage B:
  job-0039-engine   (3 new fetcher tools)
  ← gated on 0037 (WorldPop edit closed; avoids data_fetch.py collision)
                  + 0038 (HydroMT depth decision lands in docs/decisions/)

stage C:
  job-0041-agent    (run_solver + wait_for_completion + progress emission)
  ← gated on 0040 (SFINCS Cloud Run Job + Workflows live so submission has a target)

stage D:
  job-0042-engine   (model_flood_scenario workflow composes the chain)
  ← gated on 0039 (3 fetcher tools) + 0041 (run_solver)

stage E:
  job-0043-testing  (M5 acceptance + sprint close)
  ← gated on 0042 approved
```

## Exit criteria

- [ ] **WorldPop is the `fetch_population` Tier-1 default** per Appendix F.1; ACS opt-in via `dataset="acs_2022"`; Fort Myers demo (WorldPop branch) passes end-to-end with no API key.
- [ ] OQ-4 HydroMT depth decision landed in `docs/decisions/oq-4-hydromt-depth.md` with cited tradeoffs; sprint-07 fetchers + workflow conform to the chosen depth.
- [ ] 3 new fetcher atomic tools registered (`fetch_landcover`, `fetch_river_geometry`, `lookup_precip_return_period`) — all `static-30d`, all Tier-1, all route through cache shim per FR-CE-8. Tool registry shows 11 tools on `--startup-only` (M4's 8 + 3 new).
- [ ] SFINCS solver container live: `gcloud run jobs describe grace-2-sfincs-solver` returns success; `sfincs-runtime` SA exists with bucket-scoped IAM (mirrors job-0021 / job-0031 zero-project-grants discipline); Cloud Workflows step invokes the job and waits for completion.
- [ ] `run_solver` + `wait_for_completion` atomic tools registered; `update_progress` opt-in emits real `pipeline-state` envelopes (≥3 progress updates during a real SFINCS run, captured in evidence). Cancel chain reaches the running solver within the FR-AS-6 / NFR-R-3 30s budget.
- [ ] `model_flood_scenario(...)` workflow returns a populated `AssessmentEnvelope` (Appendix B.4 Flood subtype) with `flood_depth` LayerURI pointing at a real COG in the `runs` bucket.
- [ ] M5 acceptance: Hurricane Ian / Fort Myers demo end-to-end PASS. Flood-depth layer renders on the web client via QGIS Server. Captured screenshot under sprint-07 evidence dir.
- [ ] NFR-P-4 timing captured (real wall-clock for ≤200 km² ≤30m run); recorded honestly with environment context per testing.md NFR discipline. Target ≤15 min.
- [ ] FR-DC-4 dedup verified: re-running the demo within 30 minutes hits cache for the 4 fetchers; within 30 days hits cache for all `static-30d` fetchers.
- [ ] `make test` + `make test-m2` + `make test-m3` + `make test-m4` + new `make test-m5` all green; baselines preserved.
- [ ] No edits to FROZEN paths per AGENTS.md.

**Out of scope (deferred, do not bundle in sprint-07):**
- Discovery-First lane atomic tools (§F.2) — sprint-08.
- §F.3 `request_secret` Secrets UX — deferred indefinitely per user direction.
- Census API key wiring (OQ-36-CENSUS-API-KEY-REQUIRED) — orchestrator-direct one-shot after user provisions key off-band; not a job.
- v0.3.17 SRS housekeeping pass (6 carry-forward OQs: OQ-W-26, OQ-INFRA-31-FR-DC-1, OQ-INFRA-31-LIVE-NO-CACHE-LIFECYCLE-NOOP, OQ-33-GEOCODED-LOCATION-CONTRACT-PROMOTION, OQ-35-WIRE-PAYLOAD-ERROR-FIELDS-VISIBILITY, OQ-36-CACHE-REGRESSION-FAKE-FIDELITY) — orchestrator-direct at sprint-07 close.
- Other §2.3 deferred engines (MODFLOW, HEC-HMS, TELEMAC, ParFlow, etc.) — lazy per-milestone deploy; await their respective milestone sprints.

## Retrospective

_Drafted by job-0043 (testing) at sprint close; orchestrator finalizes at audit._

### M5 milestone achievement — substrate verification with honest qualification

Sprint-07 closes M5 (SFINCS engine v0 + `model_flood_scenario` workflow capstone) with the substrate verification path proven end-to-end against the live deployed substrate. The Hurricane Ian / Fort Myers demo runs through every component the M5 milestone requires: the 14-tool registry (M4's 8 + sprint-07's 3 new fetchers + 2 solver-dispatch tools + the workflow wrapper), the `model_flood_scenario` deterministic composition (job-0042), the live Cloud Workflows orchestrator (`grace-2-sfincs-orchestrator`, job-0040), the SFINCS Cloud Run Job + runs bucket (job-0040), the agent-side `run_solver` + `wait_for_completion` cancel chain (job-0041), the OQ-4 §4 NLCD validation gate (job-0042 + this job's PASS branch verification), and the M4 cache substrate with OQ-33-customTime-as-datetime preservation.

Honest substrate-vs-output qualification per testing.md ("silently green is the one unforgivable outcome"): on the Debian dev box, `hydromt_sfincs` is not installed (it's a ~500 MB heavyweight dep that the production SFINCS container has but the agent dev `.venv-agent` does not). The M5 demo therefore lands on `build_sfincs_model` raising `HYDROMT_UNAVAILABLE` and the workflow returns a typed failed envelope with `flood.metrics.solver_version="failed:HYDROMT_UNAVAILABLE"`. Job-0043's kickoff §1 explicitly accepts this outcome: "M5 acceptance criterion is substrate verification, NOT 'SFINCS must succeed.'" Two acceptable outcomes were enumerated (SUCCESS + HONEST FAILURE); we landed HONEST FAILURE with full layer attribution. The first real SFINCS scientific output lands when sprint-08+ either (a) installs HydroMT-SFINCS in the dev venv (`OQ-43-HYDROMT-SFINCS-DEV-VENV-INSTALL`) or (b) exercises the chain end-to-end via the deployed agent service against the production SFINCS container with a real HydroMT-generated deck.

### TWO substrate-level wins (the headlines)

1. **Invariant 7 NLCD validation gate fired LIVE in production catching a real silent-wrong-answer mode (job-0042) AND verified with the PASS branch on production canonical bytes (job-0043).** The OQ-4 §4 contract is now end-to-end live: the gate caught the MRLC WMS palette-encoding upstream surprise (job-0042 smoke transcript: `unmapped_classes: [1, 3, 4, 5, 6, 7, 9, 10, 13, 14, 18, 20]` — palette indices, not NLCD class integers — surfaced as `OQ-42-NLCD-WMS-PALETTE-ENCODING`); the job-0044 hotfix (Path B: WCS 1.0.0 GetCoverage over WMS GetMap) delivered canonical bytes; the M5 acceptance demo verifies the PASS branch closes the loop (`landcover classes observed: [11, 21, 22, 23, 24, 31, 41, 42, 43, 52, 71, 81, 82, 90, 95]` — canonical NLCD integers, clean subset of `manning_mapping.csv` v1.0.0's 20 mapped classes). Without the gate the MRLC palette would have silently fed bogus class IDs into HydroMT's roughness assignment and the SFINCS model would have run successfully with the wrong Manning's n everywhere — producing a misleading flood map that looked correct. **The gate prevented exactly the failure class Decision OQ-4 §4 mandated a mitigation for, working in production.**

2. **Invariant 8 cancel chain measured at 850 ms on the run_solver layer (job-0041) and 8.2 s end-to-end on the full workflow chain (job-0043) — 35× under the NFR-R-3 30 s budget at the tightest level, 3.6× margin at the workflow level.** Job-0041 measured the cloud cancel propagation: WS cancel envelope → `state.inflight_task.cancel()` → `CancelledError` inside `wait_for_completion` → `workflows.executions.cancel(name)` → Cloud Workflows propagates → Cloud Run Job receives SIGTERM → workflow state CANCELLED → terminal pipeline-state observed; total 0.85 s (`evidence/cancel_run.json`). Job-0043 extended this to the full workflow composition: `/invoke run_model_flood_scenario` → workflow racing fetchers + composition → cancel envelope → terminal state observed in 8.24 s end-to-end. The cancel substrate is end-to-end production-ready.

### Cost-discipline telemetry

Sprint-07 totals (per `reports/cost_tracking.json`):

| Sprint | Jobs | Total tokens | Avg/job |
|---|---|---|---|
| Sprint-06 (M4) | 7 | 1,265,567 | 180,795 |
| Sprint-07 (M5) | 7 | 1,589,650 | 227,093 |

Sprint-07 ran ~26% higher per-job than sprint-06, reflecting the substrate complexity step: sprint-07 added the SFINCS Docker image + Cloud Workflows orchestrator + 3 new fetcher tools + the workflow composition + the OQ-4 §4 NLCD validation gate + the mid-sprint NLCD WCS hotfix. Job-0042 (model_flood_scenario + NLCD gate) at 310 K and job-0041 (run_solver + wait_for_completion) at 275 K were the two heaviest jobs — both substrate-load-bearing for M5; the cost was appropriate. Job-0038 (OQ-4 HydroMT depth decision on Sonnet) at 80 K demonstrates the model-routing discipline pays off when the work is research/summarize rather than code.

### v0.3.17+ housekeeping carry-forward pile

Carrying forward (the sprint-07 OQ pile, joining the pre-existing v0.3.16 carry-forwards):
- **From sprint-07 sub-jobs:** OQ-37-* (WorldPop default flip), OQ-38-* (OQ-4 HydroMT depth decision aftermath), OQ-39-* (fetcher trio), OQ-39-NLCD-TIER-DEVIATION (Tier 3 → Tier 2 deviation), OQ-40-* (SFINCS substrate), OQ-41-* (solver dispatch — POLL-INTERVAL, PROGRESS-CURVE, BLOCK-VS-YIELD, ERROR-CODE-REGISTRY, COMPUTE-CLASS-NAMING, EMITTER-BINDING-SITE), OQ-42-* (model_flood_scenario — WORKFLOW-EXPOSURE-PATTERN, MANNING-MAPPING-SOURCE-CITATION, POSTPROCESS-FORMAT-SET, PARTIAL-FAILURE-ENVELOPE-SHAPE, ATCF-HURRICANE-IAN-INTEGRATION, MODEL-CRS-AUTO-UTM, FLOOD-DEPTH-PRESET-QML), OQ-44-MANNING-MAPPING-CSV-COMMENT-WMS-REF (stale CSV comment), OQ-44-WMS-WCS-SAME-SERVER-AGREEMENT / VINTAGE-PARITY / WCS-FOR-OTHER-MRLC-PRODUCTS (informational; future fetcher tools).
- **From this job (job-0043):** OQ-43-PIPELINE-STATE-RESULT-FIELD-VISIBILITY, OQ-43-HYDROMT-SFINCS-DEV-VENV-INSTALL, OQ-43-CANCEL-TEST-RACE-CONDITION, OQ-43-NFR-P-4-REAL-RUN-TIMING-PENDING, OQ-43-PLAYWRIGHT-DEV-SEAM-VS-LIVE-WS, OQ-43-WS-KEEPALIVE-PING-INTERVAL-NONE (informational; documented).
- **Pre-existing carry-forwards (from PROJECT_STATE.md):** OQ-W-26-PIPELINE-STEP-FIELDS (resolved by job-0030); OQ-INFRA-31-FR-DC-1, OQ-INFRA-31-LIVE-NO-CACHE-LIFECYCLE-NOOP, OQ-33-GEOCODED-LOCATION-CONTRACT-PROMOTION, OQ-35-WIRE-PAYLOAD-ERROR-FIELDS-VISIBILITY, OQ-36-CACHE-REGRESSION-FAKE-FIDELITY, OQ-36-CENSUS-API-KEY-REQUIRED, OQ-36-CROSS-CONNECTION-BROADCAST, OQ-36-QGIS-PROCESS-DEMO-CHAIN (CLOSED by job-0042's model_flood_scenario landing), OQ-36-NOMINATIM-RATE-LIMIT-IN-CI, OQ-36-M4-TEST-DEFAULT-INCLUSION, OQ-35-DEV-INJECTION-SEAM-RETIREMENT, OQ-T-28-SIM-WS-BOUNDARY (CLOSED by job-0036).
- **SRS amendment pile (carry-forward; user lands at convenience):** A1–A5 from job-0013; NFR-C-1 cost-line accuracy; NFR-P-1 first-token budget; FR-AS-1 Gemini-3 substitution; FR-QS-2 `/mnt/qgs/` contract change; gitignore Lever A/B/C identifier exposures.

The pile is large but well-categorized; the orchestrator-direct v0.3.17+ housekeeping pass (already planned per PROJECT_STATE.md's "Next up") can land them in a single editing sweep.

### Sprint-08 scope notes (proposed; user confirms)

1. **Mode 1 data-source catalog** (§F.1.2 / FR-DT-3 / `public_hazard_catalog.yaml`) — the Discovery-First lane atomic tools (`hazard_catalog_search`, `fetch_public_hazard_layer`, `summarize_layer_in_bbox`) that were deferred from sprint-07. Closes the "find an existing hazard product before running a model" path.
2. **FR-FR-3 max-turns cap** (§3.10 — the user-defined max-turns ceiling on Mode 2 modeling loops as a guardrail against runaway autonomous loops). Pairs with the failure-recovery surface that the v0.1 M5 demo's HONEST FAILURE path already exercises.
3. **ATCF Hurricane Ian forcing integration** for full-fidelity SFINCS demos (`OQ-42-ATCF-HURRICANE-IAN-INTEGRATION`): `fetch_hurricane_track` (NHC ATCF Best Track) + `run_storm_surge_flood` workflow chain composing surge forcing instead of pluvial design-storm forcing.
4. **HydroMT-SFINCS install path** for the agent service Cloud Run service definition (`OQ-43-HYDROMT-SFINCS-DEV-VENV-INSTALL`) — unlocks the SUCCESS branch of the M5 demo on the deployed substrate.
5. **`PipelineEmitter.mark_complete` carries tool return value** (`OQ-43-PIPELINE-STATE-RESULT-FIELD-VISIBILITY`) — one-line agent edit that surfaces the AssessmentEnvelope dict on the wire so future M5+ workflow demos don't need a side-channel direct-import smoke harness.
6. **v0.3.17 SRS housekeeping pass** bundling the carry-forward pile above. Orchestrator-direct single-pass.

### Exit-criteria reverification (per AGENTS.md per-sprint acceptance record)

| Exit criterion | Verification | Evidence |
|---|---|---|
| WorldPop is the `fetch_population` Tier-1 default | job-0037 verified live (Fort Myers WorldPop branch passes end-to-end, no API key); ACS opt-in works via `dataset="acs_2022"` | `complete/job-0037-engine-20260606/evidence/` |
| OQ-4 HydroMT depth decision landed in `docs/decisions/oq-4-hydromt-depth.md` | job-0038 (Sonnet research) drafted §1–§5 with cited tradeoffs; sprint-07 fetchers + workflow conform | `docs/decisions/oq-4-hydromt-depth.md`; `complete/job-0038-engine-20260606/` |
| 3 new fetcher atomic tools registered, all `static-30d`, all routed through cache shim | job-0039 landed `fetch_landcover` / `fetch_river_geometry` / `lookup_precip_return_period`; M5 demo cache-hit transcript shows live read-through hits for all 3 | `complete/job-0039-engine-20260606/`; this job's `evidence/smoke_demo_log.txt` |
| SFINCS solver container live + `sfincs-runtime` SA scoped IAM + Cloud Workflows step | job-0040 verified via `gcloud run jobs describe grace-2-sfincs-solver`; Cloud Workflows execution `afd364bd-…` (job-0042) + `1d98f3e9-…` (job-0044) + the M5 acceptance dispatch all observed live | `complete/job-0040-infra-20260606/` |
| `run_solver` + `wait_for_completion` + `update_progress` opt-in; cancel chain ≤30s | job-0041 measured 36 progress emissions live + 850 ms cancel; this job measured 8.2 s end-to-end full-chain cancel | `complete/job-0041-agent-20260606/`; this job's `evidence/cancel_summary.json` |
| `model_flood_scenario(...)` workflow returns populated `AssessmentEnvelope` (B.4 Flood) | job-0042 + this job verify the typed-envelope return; populated `ForcingSummary` with Atlas 14 100-yr/24-hr 11.9 inches; 5 data sources cited; substrate verification through HYDROMT_UNAVAILABLE accepted per kickoff §1 | this job's `evidence/smoke_demo_envelope.json` |
| M5 acceptance: Hurricane Ian / Fort Myers demo end-to-end PASS | PASS with honest qualification — HONEST FAILURE branch via HYDROMT_UNAVAILABLE; substrate verification through 5 cache hits + NLCD gate PASS + Atlas 14 forcing + SFINCS-deck-build entry. Screenshot captured at `evidence/screenshots/final_honest_failure.png` | this job's `evidence/` |
| NFR-P-4 timing captured (real wall-clock); target ≤15 min | QUALIFIED — substrate-level measurement at ~10 s (well under budget); real SFINCS-run NFR-P-4 timing pends HydroMT-SFINCS install (sprint-08 work) | this job's `evidence/nfr_p_4_timing.json` |
| FR-DC-4 dedup verified: re-run within 30 min hits cache | Verified — the M5 acceptance + smoke harness re-ran within minutes of job-0044's WCS hotfix smoke; all 5 fetchers hit cache live | this job's `evidence/smoke_demo_log.txt` (5 read_through hit lines) |
| `make test` + `test-m2` + `test-m3` + `test-m4` + `test-m5` all green | All 7 tiers green: 131+119+30+7+10+2+2 = 301 unique-function invocations | this job's `evidence/pytest_m5.txt` + Verification section |
| No edits to FROZEN paths per AGENTS.md | Verified per FROZEN-paths check in this job's report | this job's report.md § FROZEN-paths check |

### What worked / what to change next sprint

**Worked:**
- The two-layer architecture (Decision G) absorbed the sprint-07 substrate without contract churn. The workflows package landed cleanly under the M4 substrate; no schema amendment needed at the M5 substrate level.
- The lazy per-milestone engine deploy posture (user-decision 2026-06-06) kept sprint-07 focused on SFINCS only — no idle Cloud Run Jobs, no premature multi-solver abstraction. Sprint-08 can repeat the posture for any new milestone solver.
- The mid-sprint NLCD WCS hotfix (job-0044) demonstrated the reviewer/audit loop catching a real silent-wrong-answer mode (palette encoding) and a same-day engineer fix to a substrate flaw. Total sprint slip: zero (job-0043 lands on the same date as the original plan).
- Invariant 7's NLCD validation gate paid for itself in its first sprint of operation by catching the palette-encoding upstream surprise. The "load-bearing safety substrate" framing the OQ-4 §4 decision used proved correct.

**To change for sprint-08:**
- **PipelineEmitter wire-surface for tool return values (OQ-43-PIPELINE-STATE-RESULT-FIELD-VISIBILITY)** — the M5 acceptance test had to use a side-channel direct-import smoke harness to capture the typed envelope shape. A one-line `PipelineEmitter.mark_complete(step_id, *, result=None)` extension would let future workflow acceptance tests observe the typed return on the wire. Route to an agent follow-up job early in sprint-08.
- **HydroMT-SFINCS in the dev venv (OQ-43-HYDROMT-SFINCS-DEV-VENV-INSTALL)** — the substrate-vs-output qualification on the M5 demo was forced by this gap. Sprint-08 should land a dev-env bake (apt packages + ~500 MB Python wheel cache) so the SFINCS deck build actually runs from the dev box. Route to infra.
- **Cancel-test race condition (OQ-43-CANCEL-TEST-RACE-CONDITION)** — when fetchers are all cached, the workflow naturally completes faster than the cancel window. A production-class cold-cache cancel test (orchestrator-direct one-shot, drains the relevant cache keys before submission) would give a sharper NFR-R-3 measurement.

### Open questions (carried forward)

The full OQ pile above is the answer. The orchestrator triages: anything actionable in sprint-08 (per the scope notes) becomes a job; the rest joins the v0.3.17+ housekeeping pass.
