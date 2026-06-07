# Sprint 08: Mode 1 data-source catalog + small carry-forwards (SRS v0.3 post-M5)

**Status:** closed
**Opened:** 2026-06-07
**Closed:** 2026-06-07
**SRS milestones covered:** Post-M5 substrate enablement — the Mode 1 (§F.1.2) data-source catalog as the "data stores in the wild" headline; FR-FR-3 MAX_TURNS_PER_SESSION cap (§3.10) as cheap insurance; hydromt-sfincs install resolves OQ-43 so the M5 demo can produce a real flood map next time it runs.

## Goal

Land the **Mode 1 data-source catalog substrate** per v0.3.18 §F.1.2 — a curated, research-driven `public_data_source_catalog.yaml` with 30–60 vetted endpoints across the major hazard / hydrology / weather / building / population / land-cover / fire / seismic domains; new `CatalogEntry` pydantic + MongoDB collection D.8; `catalog_search` + `catalog_fetch` atomic tools; generic Tier-2 OGC adapter that any WMS/WMTS/WCS/WFS source can route through. The catalog is the architectural spine for the "agent encounters data wherever it lives" capability — Mode 1 ships first, Mode 2 (`.gov`/`.edu` offer-to-add) is sprint-09 if not bundled here.

Plus two small carry-forwards that need to land before the next M5 demo run can produce a successful pipeline: FR-FR-3 `MAX_TURNS_PER_SESSION = 25` cap (single-line config) and the `hydromt-sfincs` install in the agent service Cloud Run (OQ-43 resolution).

Sprint-08 is the **first sprint where Sonnet research carries serious weight** — the catalog seed job is exactly the shape Sonnet excels at (read a bunch of source docs, summarize into structured entries, recommend). Expect a lower per-job token average than sprint-7.

**Deferred to sprint-09 (or fast-follow if sprint-08 scope clearly permits):**
- **Mode 2 (`.gov`/`.edu` offer-to-add)** — new envelope shapes + agent emission detection + web client popup modal + audit log.
- **ATCF Hurricane Ian real storm forcing** — replaces the Atlas 14 design storm in the M5 demo with real NHC ATCF track data; lifts the demo from substrate-verification to full-fidelity hazard modeling.
- **M5.1 acceptance run** — once hydromt-sfincs lands and ATCF lifts the forcing, re-run the Fort Myers demo end-to-end with the goal of producing a real flood-depth COG + rendered layer.

## Pre-flight (orchestrator-direct, lands in parallel with Stage A)

- **v0.3.20 SRS housekeeping pass** — bundle the v0.3.17–v0.3.19 carry-forwards (WorldPop prose alignment to 1km Aggregated REST not 100m STAC; NLCD prose to Tier 2 WCS not Tier 3 WMS; FR-CE-3 compute-class naming reconciliation `medium`↔`standard`; OQ-37-WORLDPOP-COG-CRS-AND-UNITS "people-per-cell" semantics; OQ-44-MANNING-MAPPING-CSV-COMMENT-WMS-REF stale comment; OQ-INFRA-31-LIVE-NO-CACHE-LIFECYCLE-NOOP). Single focused amendment; orchestrator-direct.

## Jobs

| Job ID | Specialist | Task | Depends on | Status |
|---|---|---|---|---|
| job-0045-schema-20260607 | schema | `CatalogEntry` pydantic model + new MongoDB collection D.8 (`catalog_entries`) + `catalog_audit_log` D.9 + extend Appendix A with the `offer-catalog-addition` + `catalog-addition-response` envelope shapes from v0.3.18 §F.1.2 (forward-looking — used by sprint-09 Mode 2 work but lands now so Mode 1 atomic tools have the typed targets). JSON Schema re-export | — | planned |
| job-0046-research-20260607 | engine (Sonnet) | Seed catalog research — pull 30–60 vetted endpoints across 8 domains (terrain, hydrology, weather, building, population, landcover, fire/wildfire, seismic) into `public_data_source_catalog.yaml` v0.1.0 with full per-entry metadata (access_tier per §F.1.1, credential_tier per §F.1, ttl_class, source_class, license, citation, vintage, last_verified, "how to use" notes per the v0.3.18 §F.1.2 schema). Live-verify each entry's access tier before commit | — | planned |
| job-0047-engine-20260607 | engine | `catalog_search(topic, location?, source_filter?)` + `catalog_fetch(entry_id, params)` atomic tools — `catalog_search` queries the seed YAML (and the future MongoDB collection); `catalog_fetch` dispatches to the entry's access_tier with cache-shim integration (Tier 1 STAC item query / Tier 2 OGC GetMap-or-GetCoverage via the generic adapter / Tier 3 `/vsicurl/` windowed read / Tier 4 region download + clip). Generic Tier-2 OGC adapter is the same module that backs `fetch_landcover` post-job-0044's WCS migration | job-0045, job-0046 | **approved** (commit 034dfd5; 16 tools live; live FEMA NFHL + 3DEP fetch verified) |
| job-0048-agent-20260607 | agent | **Small carry-forward:** FR-FR-3 MAX_TURNS_PER_SESSION cap (TENTATIVE default 25 per OQ-FR-1) — single config line + new `session-state.status="max_turns_reached"` enum value + closing `agent-message` summary on cap hit + refusal of further tool calls | — | planned |
| job-0049-infra-20260607 | infra | hydromt-sfincs install in agent service Cloud Run — update services/agent/pyproject.toml + Dockerfile (or equivalent) to bundle `hydromt-sfincs >= 1.1.2, < 2.0` per OQ-4 §4 contract; re-deploy agent service; closes OQ-43-HYDROMT-SFINCS-DEV-VENV-INSTALL | — | planned |
| job-0050-engine-20260607 | engine | **(Optional — scope-permitting):** ATCF Hurricane Ian forcing — new `fetch_hurricane_track(storm_name_or_id, source)` atomic tool against NOAA NHC ATCF; integrate into `model_flood_scenario` workflow forcing path; design-storm path stays as the `dataset="atlas14_design"` opt-out | job-0049 | planned |
| job-0051-testing-20260607 | testing | Sprint-08 acceptance — catalog demo (catalog_search for FEMA NFHL flood zones in Florida → catalog_fetch returns layer URI); max-turns cap demo (drive agent past 25 turns → session-state status flips); hydromt-sfincs install verification (re-run the M5 Fort Myers demo end-to-end — if all goes well, this is the first successful flood-depth map + screenshot moment); full M1+M2+M3+M4+M5+M6 regression. Closes sprint-08 | job-0047, job-0048, job-0049, job-0050 (if landed) | **superseded by job-0059-testing-20260607** (counter advanced past 0051 during mid-sprint hotfix chain) |
| job-0059-testing-20260607 | testing | **Stage D acceptance (supersedes 0051 reservation).** Verify Mode 1 catalog substrate live + MAX_TURNS cap + **PRODUCTION M5 SUCCESS holds end-to-end** + Invariant 7 gate live + full regression + sprint-08 retrospective with layer-emission-contract hand-off to sprint-9. | all prior sprint-08 jobs approved | **approved** (commit 89587c1; reproducible M5 run at gs://.../01KTJ3PP1JMF96WR4CCZZ4JRYS/; closing screenshot surfaced) |
| job-0052-engine-20260607 | engine | **Mid-sprint hotfix #1:** HYDROMT_BUILD_FAILED one-line `yaml.safe_load` fix at sfincs_builder.py:692. Closes OQ-49-HYDROMT-BUILD-OPT-ARGUMENT-SHAPE. | job-0049 | **approved** (commit 1b7bf88) |
| job-0053-engine-20260607 | engine | **Mid-sprint hotfix #2:** `setup_manning_roughness` v1.2.x kwarg fix via live signature inspection. Closes OQ-52. Escalation rule: if 4th 1.2.x mismatch surfaces, comprehensive migration audit replaces a 4th hotfix. | job-0052 | **approved** (commit a6399d2) |
| job-0054-engine-20260607 | engine | **Escalation:** comprehensive hydromt-sfincs 1.2.x API-migration audit. Inspect every setup_* signature; cross-walk to 1.2.2 source; audit DataCatalog wiring; land all mismatches in one commit. | job-0053 | **approved** (commit bc30638) |
| job-0055-engine-20260607 | engine | **Mid-sprint follow-up to 0054 audit recommendation (b):** drop `setup_river_inflow` from v0.1 pluvial deck — pluvial-only is intended M5 shape; river inflow is M5+ scope. Bypasses upstream pandas-3 incompat in hydromt-sfincs 1.2.2 `set_forcing_1d`. If chain produces real flood-depth COG → first M5 SUCCESS. | job-0054 | **approved** (commit cf9bfd3; chain advances further; NEW pandas-3 incompat OQ-55 freq="10T" at sfincs.py:2456) |
| job-0056-infra-20260607 | infra | **Mid-sprint follow-up #4:** pandas pin to resolve hydromt-sfincs 1.2.2 pandas-3 incompat (OQ-54 is_integer() + OQ-55 freq="10T"). Single pandas downgrade-pin should unblock BOTH. Try >=2.1,<2.2 sweet spot first; fallback <2.0; last-resort comprehensive pandas-3 audit. Re-run M5 smoke for SUCCESS / PARTIAL / STILL-BLOCKED disclosure. | job-0055 | **approved** (commit ad915bf; pandas 2.2.3 pinned; **HydroMT build completes; Cloud Workflows real-manifest execution dispatched ~3.8 min**; new blocker SOLVER_FAILED on REAL manifest — different class) |
| job-0057-engine-20260607 | engine | **Mid-sprint follow-up #5 (OQ-56 fix):** agent emits manifest.json + worker-compliant URI for SFINCS deck. Orchestrator-direct gcloud diagnostic of Cloud Run logs showed worker fails at `_read_manifest` with 404 because agent passes directory URI but worker expects single JSON file URI matching `{inputs, sfincs_args, outputs}` schema. | job-0056 | **approved** (commit 15e7832; **🎉 first M5 SUCCESS — SFINCS ran 287s exit 0; sfincs_map.nc 38 MB hmax 3.52 m at Fort Myers; PNG surfaced to user**) |
| job-0058-engine-20260607 | engine | **Tiny follow-up:** postprocess_flood squeeze singleton timemax dim before COG write. hmax shape is (1, n, m) from HydroMT-SFINCS 1.2.2; rasterio.write expects 2D. Closes the production COG path (job-0057's screenshot used an orchestrator-direct PNG render). | job-0057 | **approved** (commit 49494af; **🎉 PRODUCTION M5 SUCCESS — AssessmentEnvelope.outcome=SUCCESS; flood_depth_peak.tif at gs://.../runs/; production PNG render surfaced**) |

## Execution order

```
stage A (parallel, file-disjoint):
  job-0045-schema    (CatalogEntry pydantic + new D.8/D.9 collections + envelope shapes)
  job-0046-research  (Sonnet — catalog seed YAML, 30–60 entries; live-verify each)
  job-0048-agent     (FR-FR-3 max-turns cap — single config line, tiny job)
  job-0049-infra     (hydromt-sfincs install in agent service Cloud Run)

stage B:
  job-0047-engine    (catalog_search + catalog_fetch + generic OGC adapter)
  ← gated on 0045 (schema) + 0046 (seed YAML)

stage C (optional):
  job-0050-engine    (ATCF Hurricane Ian forcing — only if scope permits)
  ← gated on 0049 (hydromt-sfincs installed; full SFINCS chain now testable)

stage D:
  job-0051-testing   (sprint-08 acceptance + sprint close)
  ← gated on 0047 + 0048 + 0049 + 0050 (if attempted)
```

Plus orchestrator-direct **v0.3.20 SRS housekeeping pass** lands in parallel with Stage A — focused single amendment bundling ~6 carry-forward prose alignments. No specialist job; orchestrator owns it.

## Exit criteria

- [ ] **v0.3.20 SRS housekeeping pass landed** — carry-forwards from v0.3.17–v0.3.19 reconciled into the prose. _(orchestrator-direct; pending)_
- [x] **`CatalogEntry` pydantic model + MongoDB collection D.8 + audit log D.9 in schema** — JSON Schemas re-exported idempotently; contracts test count grows. _(job-0045; D.11/D.12 numbering; 142/142 contracts green)_
- [x] **Appendix A envelope amendments for `offer-catalog-addition` + `catalog-addition-response`** — forward-looking; sprint-09 Mode 2 consumer. _(job-0045; 4 envelopes registered in routing dicts)_
- [x] **`public_data_source_catalog.yaml` v0.1.0 with 30–60 entries** across the 8 domains; each entry's access_tier live-verified before commit per §F.1.1 discipline. _(job-0046; 30 entries; 9 URL deviations caught live; 2 skipped at load time per OQ-47-CATALOG-YAML-SECRET-REFS)_
- [x] **`catalog_search` + `catalog_fetch` atomic tools registered** — startup shows ≥16 tools (14 + 2 new); both route through cache shim per FR-CE-8. _(job-0047; confirmed 16 tools live 2026-06-07; fema-nfhl 7.74 MB + 3DEP 4.00 MB fetched live)_
- [x] **Generic Tier-2 OGC adapter** — single implementation that `fetch_landcover` (WCS, post-job-0044) and `catalog_fetch` (any OGC entry) both route through. _(job-0047; `tools/ogc_adapter.py`)_
- [x] **FR-FR-3 MAX_TURNS_PER_SESSION cap** — single config line + session-state enum value + closing message on cap hit. _(job-0048; 11 tests + 2 acceptance integration tests; all pass)_
- [x] **hydromt-sfincs install in agent service Cloud Run** — verified by re-running the M5 Fort Myers demo and getting past `HYDROMT_UNAVAILABLE`. _(jobs 0049→0058; chain advanced through full 1.2.x migration + PRODUCTION M5 SUCCESS)_
- [ ] **ATCF Hurricane Ian forcing** (if landed) — `fetch_hurricane_track` + `model_flood_scenario` design-storm-vs-real-forcing branch. _(job-0050 DESCOPED — sprint-09/sprint-10)_
- [x] **Sprint-08 acceptance**: catalog demo + max-turns demo + hydromt-sfincs install verification + M5 re-run + full regression preserved. _(job-0059; 165/165 agent + 142/142 contracts; all live verifications PASS)_
- [x] **Screenshot moment** — M5 chain produced real flood-depth COG. _(job-0057 orchestrator-direct PNG; job-0058 production COG render; job-0059 re-run COG at gs://grace-2-hazard-prod-runs/01KTJ3PP1JMF96WR4CCZZ4JRYS/flood_depth_peak.tif)_

## Notes on sprint-08 vs originally planned scope

Sprint-08 was planned as a 6-job sprint (4-parallel Stage A + Stage B catalog tools + Stage D testing).
Actual execution was 12 jobs. Three scopes delivered:

1. **Mode 1 catalog substrate** (planned): CatalogEntry, 30-entry YAML, catalog_search + catalog_fetch,
   generic OGC adapter, 16 tools live.
2. **hydromt-sfincs 1.2.x migration through-line** (unplanned but necessary): 7 sequential hotfixes
   (jobs 0052–0058) resolving 1.2.x API mismatches, pandas-3 incompatibilities, manifest.json emission,
   and COG squeeze. The escalation rule at job-0054 (comprehensive audit after 3rd mismatch) was
   the correct call.
3. **First PRODUCTION M5 SUCCESS** (unplanned, emergent): job-0057 first flood-depth output
   (SFINCS exit 0, hmax 3.52 m); job-0058 production COG path closed; job-0059 re-run confirms
   reproducibility.

The reserved job-0051-testing slot was superseded by job-0059-testing because the counter advanced
past 0051 during the hotfix chain. The sprint manifest reflects this correctly.

ATCF Hurricane Ian forcing (job-0050) was descoped — it was always marked optional (Stage C) and the
hotfix chain consumed the capacity.

## Retrospective

**What worked:**
- Escalation rule fired correctly at job-0054: 3 consecutive 1.2.x mismatches triggered a comprehensive
  audit rather than a 4th point hotfix. One audit job replaced at least 3 more one-at-a-time hotfixes.
- Sonnet routing discipline delivered 6 wins (50% of sprint jobs by count) at 38.9% of tokens.
  Sprint-08 is 10% cheaper than sprint-07 despite more jobs delivered.
- The NLCD validation gate (Invariant 7) continued to fire correctly across all production runs
  (PASS branch confirmed; FAIL branch from job-0042 remains the canonical positive control).
- Live catalog probe discipline in job-0046 caught 9 stale/wrong URLs before commit, avoiding
  9 sprint-09 hotfix cascades.
- The layer-emission-contract.md decision (orchestrator-direct) landed cleanly between job-0058
  and job-0059, giving sprint-09 a frozen architectural contract before the sprint opens.

**What to change:**
- Parallel job file ownership enforcement: jobs 0045 and 0048 both touched ws.py concurrently.
  Reconciliation worked but was unclean. Next sprint: serialize or split file ownership for
  concurrent ws.py changes (OQ-48-PARALLEL-SCHEMA-FILE-OWNERSHIP).
- Sprint-08 planned capacity was tight for a "small carry-forwards" sprint that uncovered a
  full 1.2.x migration. OQ-4 from sprint-07 surface-treated the HydroMT depth decision but
  did not expose the API-migration scope. Future hydromt upgrades warrant a pre-sprint
  migration audit job before committing the install.
- Counter management: 8 hotfix/follow-up jobs advancing the counter 0052–0059 past the
  reserved testing slot (0051) is fine mechanically, but retroactively patching the sprint
  manifest mid-sprint is harder than forward-planning. Add "hotfix counter reserve" of 3–5
  slots to sprint manifests for sprints that touch external library installs.

**Cost telemetry:** **1,786,238** total sprint-08 tokens (job-0059 added 110,490 post-testing closure). Opus: 1,023,435 (57.3%), Sonnet: 762,803 (42.7%). Compared to sprint-07 (1,863,284), sprint-08 ran ~4% cheaper despite delivering 12 jobs vs 8. See `reports/cost_tracking.json` for per-job breakdown.

**Open OQs carried forward:**
- OQ-59-FLOOD-COG-CRS-LABEL-VS-COORDS (NEW — sprint-09 engine housekeeping)
- OQ-47-CATALOG-YAML-SECRET-REFS (infra + engine, not blocking)
- OQ-47-OWSLIB-CHOICE (needs formal decision doc)
- OQ-49-AGENT-CLOUD-RUN-DEPLOY-PENDING (infra, not blocking)
- OQ-48-PARALLEL-SCHEMA-FILE-OWNERSHIP (process recommendation)
- OQ-W-26, OQ-33, OQ-35, OQ-36, OQ-41-COMPUTE-CLASS-NAMING, OQ-44, OQ-45-D-NUMBERING (pre-sprint-08)

**Sprint-09 hand-off:** Three jobs implied by layer-emission-contract.md — (1) engine: change
`run_model_flood_scenario` return to `LayerURI`; (2) engine/infra: atomic `publish_layer` tool via
PyQGIS worker + `.qgs` mutation; (3) infra: IAM grant on runs bucket for qgis-server-runtime SA.
Orchestrator scopes sprint-09; this testing job confirms the hand-off only.
