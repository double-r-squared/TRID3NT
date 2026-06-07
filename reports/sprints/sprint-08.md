# Sprint 08: Mode 1 data-source catalog + small carry-forwards (SRS v0.3 post-M5)

**Status:** planned
**Opened:** 2026-06-07
**Closed:** —
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
| job-0051-testing-20260607 | testing | Sprint-08 acceptance — catalog demo (catalog_search for FEMA NFHL flood zones in Florida → catalog_fetch returns layer URI); max-turns cap demo (drive agent past 25 turns → session-state status flips); hydromt-sfincs install verification (re-run the M5 Fort Myers demo end-to-end — if all goes well, this is the first successful flood-depth map + screenshot moment); full M1+M2+M3+M4+M5+M6 regression. Closes sprint-08 | job-0047, job-0048, job-0049, job-0050 (if landed) | planned |
| job-0052-engine-20260607 | engine | **Mid-sprint hotfix #1:** HYDROMT_BUILD_FAILED one-line `yaml.safe_load` fix at sfincs_builder.py:692. Closes OQ-49-HYDROMT-BUILD-OPT-ARGUMENT-SHAPE. | job-0049 | **approved** (commit 1b7bf88) |
| job-0053-engine-20260607 | engine | **Mid-sprint hotfix #2:** `setup_manning_roughness` v1.2.x kwarg fix via live signature inspection. Closes OQ-52. Escalation rule: if 4th 1.2.x mismatch surfaces, comprehensive migration audit replaces a 4th hotfix. | job-0052 | **approved** (commit a6399d2) |
| job-0054-engine-20260607 | engine | **Escalation:** comprehensive hydromt-sfincs 1.2.x API-migration audit. Inspect every setup_* signature; cross-walk to 1.2.2 source; audit DataCatalog wiring; land all mismatches in one commit. | job-0053 | **approved** (commit bc30638) |
| job-0055-engine-20260607 | engine | **Mid-sprint follow-up to 0054 audit recommendation (b):** drop `setup_river_inflow` from v0.1 pluvial deck — pluvial-only is intended M5 shape; river inflow is M5+ scope. Bypasses upstream pandas-3 incompat in hydromt-sfincs 1.2.2 `set_forcing_1d`. If chain produces real flood-depth COG → first M5 SUCCESS. | job-0054 | **approved** (commit cf9bfd3; chain advances further; NEW pandas-3 incompat OQ-55 freq="10T" at sfincs.py:2456) |

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

- [ ] **v0.3.20 SRS housekeeping pass landed** — carry-forwards from v0.3.17–v0.3.19 reconciled into the prose.
- [ ] **`CatalogEntry` pydantic model + MongoDB collection D.8 + audit log D.9 in schema** — JSON Schemas re-exported idempotently; contracts test count grows.
- [ ] **Appendix A envelope amendments for `offer-catalog-addition` + `catalog-addition-response`** — forward-looking; sprint-09 Mode 2 consumer.
- [ ] **`public_data_source_catalog.yaml` v0.1.0 with 30–60 entries** across the 8 domains; each entry's access_tier live-verified before commit per §F.1.1 discipline.
- [ ] **`catalog_search` + `catalog_fetch` atomic tools registered** — startup shows ≥16 tools (14 + 2 new); both route through cache shim per FR-CE-8.
- [ ] **Generic Tier-2 OGC adapter** — single implementation that `fetch_landcover` (WCS, post-job-0044) and `catalog_fetch` (any OGC entry) both route through.
- [ ] **FR-FR-3 MAX_TURNS_PER_SESSION cap** — single config line + session-state enum value + closing message on cap hit.
- [ ] **hydromt-sfincs install in agent service Cloud Run** — verified by re-running the M5 Fort Myers demo and getting past `HYDROMT_UNAVAILABLE` (either to a successful flood-depth COG or to the next honest blocker).
- [ ] **ATCF Hurricane Ian forcing** (if landed) — `fetch_hurricane_track` + `model_flood_scenario` design-storm-vs-real-forcing branch.
- [ ] **Sprint-08 acceptance**: catalog demo + max-turns demo + hydromt-sfincs install verification + M5 re-run + full regression preserved.
- [ ] **Screenshot moment** — if hydromt-sfincs + ATCF land successfully and the M5 chain produces a real flood-depth render, capture + surface via SendUserFile proactive (orchestrator-direct per memory feedback_orchestrator_drives_ui_verification).

## Retrospective

_Filled at close._
