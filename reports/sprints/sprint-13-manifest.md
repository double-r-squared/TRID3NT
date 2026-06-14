# Sprint 13: MODFLOW Engine + Case 2 + Case 3 + Conversational Data Analysis Layer

**Status:** planned
**Opened:** — (opens at Wave 4.11 close)
**Closed:** —
**Gated on:** Wave 4.11 close (all Wave 4.11 jobs approved)
**SRS milestones covered:** §2.3 MODFLOW 6 engine integration (OQ-9 decision: mf6-gwt solute transport); FR-HEP Case 2 full E2E (news → MODFLOW → plume render); Case 3 demo (NWS alert → MRMS → SFINCS inundation); conversational data analysis layer (chart emission, gallery popup, Python sandbox — per `project_conversational_data_analysis_layer`); `model_flood_scenario` v2 (real-precip forcing branch for Case 3).

## Goal

Sprint 13 delivers three headline milestones that together constitute "the platform is a real multi-hazard workbench, not a demo":

1. **MODFLOW 6 + mf6-gwt groundwater engine** — containerized solver, Cloud Run Job, Workflows integration. Establishes the engine-substrate pattern for all future non-SFINCS solvers.
2. **Case 2 full E2E** — news article describing a real spill event → agent extracts location + contaminant parameters → MODFLOW groundwater flow + mf6-gwt solute transport → plume layer rendered in MapLibre. First non-hydrology, non-Florida demo.
3. **Case 3 demo** — NWS active flood warning in Idaho → agent fetches MRMS accumulated precip over warning polygon → SFINCS inundation → flood layer rendered over warning polygon. Proves FR-HEP ingest path and non-Florida geography.
4. **Conversational data analysis layer** — chart emission (Vega-Lite wire format, inline stacked preview + gallery popup), 4 analytical Q&A tools, Python Cloud Run Job sandbox. Users can ask "how many structures?" and get a data-backed answer with a histogram.

Total projected: ~3.5M tokens. Wall-clock: ~4–6 hours.

## Model routing policy

Opus by default for: MODFLOW container + workflow jobs, mf6-gwt solute transport, Case 2 composer, Case 3 `model_flood_scenario` v2 amendment, chart-emission envelope design, Python sandbox infra, all adversarial-verify panels. Sonnet for: analytical Q&A tools (established tool pattern), web chart/gallery rendering (component work), acceptance Playwright jobs, regression sweeps.

## Wave Structure

```
STAGE 1 — Engine substrate (parallel, file-disjoint)
  MODFLOW container + Cloud Run Job
  mf6-gwt schema + forcing adapter
  chart-emission envelope schema
  analytical Q&A tool set (3 tools)
  model_flood_scenario v2 design
       ↓ adversarial verify: MODFLOW container + chart-emission schema
STAGE 2 — Workflows + composers (parallel within stage)
  MODFLOW Cloud Workflows integration
  Case 2 agent composer (news → MODFLOW)
  Case 3 agent composer (NWS alert → MRMS → SFINCS)
  chart-generation tools + gallery popup web
  Python sandbox infra + Cloud Run Job container
       ↓ adversarial verify: Case 2 composer + Python sandbox
STAGE 3 — Live gate (parallel acceptance)
  Case 2 full E2E acceptance
  Case 3 acceptance
  conversational analysis layer acceptance
  Python sandbox acceptance
STAGE 4 — Close
  sprint-13 regression sweep + close + sprint-13.5 pre-flight
```

## Stage 1 — Engine Substrate

### MODFLOW sub-track

| Job ID | Title | Specialist | Model | Est. Tokens | Depends on | Adv. Verify |
|--------|-------|-----------|-------|-------------|------------|-------------|
| job-0220-infra-TBD | MODFLOW 6 container + Cloud Run Job + Workflows skeleton | infra | opus | 300K | Wave 4.11 close | YES |
| job-0221-engine-TBD | mf6-gwt solute transport schema + forcing adapter | engine | opus | 200K | Wave 4.11 close | YES |
| job-0222-schema-TBD | Groundwater model contract — MODFLOWRunArgs + PlumeLayerURI | schema | opus | 100K | Wave 4.11 close | no |

**job-0220 scope (adversarial-verify gated):** MODFLOW 6 Docker image (`flopy` + MF6 binary from USGS; pinned version in Dockerfile). Cloud Run Job definition in Tofu. Cloud Workflows definition mirroring the SFINCS pattern (job-0040 reference): `submit_modflow_job` → poll → fetch output → trigger postprocess. Adversarial verify: correctness (Dockerfile builds and MF6 binary executes a minimal test model) + regression (SFINCS workflow unaffected) + contract (Cloud Workflows shape matches `ExecutionHandle` contract) + live-verify (container + job submission smoke test). File ownership: `infra/modflow/` (new dir), `infra/main.tf` (new Cloud Run Job resource), `services/workers/modflow/` (new dir).

**job-0221 scope (adversarial-verify gated):** `mf6-gwt solute transport`: adapter that maps spill parameters (location, contaminant, release rate, duration) to MODFLOW 6 MF6-GWT input files via `flopy`. Produces a minimal but physically meaningful model (steady-state flow + transient GWT). Adversarial verify: correctness (are GWT concentration fields non-zero and plausible for the parameter space?) + regression + contract + live-verify (run model on synthetic inputs, inspect output). File ownership: `services/workers/modflow/gwt_adapter.py` (new).

**job-0222 scope:** `MODFLOWRunArgs` (forcing parameters: `spill_location_latlon`, `contaminant`, `release_rate_kg_s`, `duration_days`, `aquifer_k_ms`, `porosity`), `PlumeLayerURI` (extends `LayerURI` with `max_concentration_mgl`, `plume_area_km2`). File ownership: `packages/contracts/src/grace2_contracts/modflow_contracts.py` (new).

### Conversational analysis sub-track

| Job ID | Title | Specialist | Model | Est. Tokens | Depends on | Adv. Verify |
|--------|-------|-----------|-------|-------------|------------|-------------|
| job-0223-schema-TBD | chart-emission envelope schema + Vega-Lite wire format contract | schema | opus | 80K | Wave 4.11 close | YES |
| job-0224-engine-TBD | Analytical Q&A tool set (3 tools) | engine | sonnet | 150K | Wave 4.11 close | no |
| job-0225-engine-TBD | model_flood_scenario v2 — real-precip forcing branch | engine | opus | 150K | Wave 4.11 close | no |

**job-0223 scope (adversarial-verify gated):** New envelope type `chart-emission(chart_id, vega_lite_spec, title, caption)`. Vega-Lite spec as the wire format (JSON, LLM-friendly). Schema for chart persistence in MongoDB `sessions` collection (charts replay on Case rehydration). Adversarial verify: correctness + contract lenses (2-lens panel sufficient for schema-only job; ~100K). File ownership: `packages/contracts/src/grace2_contracts/chart_contracts.py` (new).

**job-0224 scope:** Three analytical Q&A tools:
- `summarize_layer_statistics(layer_uri)` → `{min, max, mean, sum, count, distribution}` for raster; `{feature_count, attribute_summary}` for vector.
- `count_features_above_threshold(layer_uri, property, threshold)` → integer count.
- `aggregate_property_within_zone(value_layer_uri, zone_layer_uri, property, agg="sum")` → aggregate value.
File ownership: `services/workers/tools/analytical_qa.py` (new, all 3 tools in one file).

**job-0225 scope:** `model_flood_scenario` v2: add `forcing_raster_uri: LayerURI | None` parameter alongside existing `forcing="atlas14_100yr"` design-storm flag. When `forcing_raster_uri` is set, map the precip raster to SFINCS `netamt` or `spw` boundary condition input instead of the Atlas-14 LUT. Case 3 depends on this branch. File ownership: `services/agent/workflows/model_flood_scenario.py` (additive amendment).

### Case 3 sub-track (Stage 1 component)

| Job ID | Title | Specialist | Model | Est. Tokens | Depends on | Adv. Verify |
|--------|-------|-----------|-------|-------------|------------|-------------|
| job-0226-engine-TBD | fetch_mrms_qpe — MRMS accumulated QPE fetcher | engine | sonnet | 100K | Wave 4.11 close | no |

**job-0226 scope:** `fetch_mrms_qpe(bbox, accumulation="24h")` — MRMS QPE from NOAA MRMS archive (GRIB2 via THREDDS or S3 mirror); accumulation options `"1h"`, `"6h"`, `"24h"`, `"72h"`; COG output; publishes via `publish_layer`; returns `LayerURI`. File ownership: `services/workers/tools/fetch_mrms_qpe.py` (new).

## Stage 2 — Workflows + Composers + UI

All Stage 2 jobs are gated on their Stage 1 prerequisites. Within Stage 2, sub-tracks are file-disjoint and run in parallel.

### MODFLOW workflow sub-track

| Job ID | Title | Specialist | Model | Est. Tokens | Depends on | Adv. Verify |
|--------|-------|-----------|-------|-------------|------------|-------------|
| job-0227-agent-TBD | MODFLOW Cloud Workflows integration + agent run_solver binding | agent | opus | 200K | job-0220, job-0221, job-0222 | no |
| job-0228-agent-TBD | Case 2 workflow composer — news → MODFLOW → plume | agent | opus | 200K | job-0227 | YES |

**job-0227 scope:** Agent-side `run_modflow_job(run_args: MODFLOWRunArgs)` tool: submits Cloud Workflows execution, returns `ExecutionHandle`, emits progress envelopes (same pattern as `run_solver` for SFINCS per job-0041). Postprocess step: reads MODFLOW concentration output, reprojects to EPSG:4326 COG, publishes via `publish_layer`, returns `PlumeLayerURI`. File ownership: `services/agent/workflows/run_modflow.py` (new), `services/agent/tools/run_modflow_tool.py` (new).

**job-0228 scope (adversarial-verify gated):** `model_groundwater_contamination_scenario` workflow composer: chains `web_fetch`/`fetch_nws_event` (news ingest, already from sprint-12-mega) → `aggregate_claims_across_sources` (parameter extraction) → `run_modflow_job` → `publish_impact_layer`. Confirms derived parameters with user before MODFLOW submission (confirmation-before-consequence invariant). Adversarial verify: correctness (does the chain produce a plume layer from a real news article?) + regression + contract + live-verify (4 × Opus, ~200K). File ownership: `services/agent/workflows/model_groundwater_contamination_scenario.py` (new).

### Case 3 workflow sub-track

| Job ID | Title | Specialist | Model | Est. Tokens | Depends on | Adv. Verify |
|--------|-------|-----------|-------|-------------|------------|-------------|
| job-0229-agent-TBD | Case 3 workflow composer — NWS alert → MRMS → SFINCS | agent | opus | 150K | job-0225, job-0226 | no |

**job-0229 scope:** `model_nws_flood_event_scenario` workflow composer: chains `fetch_nws_alerts_conus(bbox)` → extract flood-warning polygon → `fetch_mrms_qpe(polygon_bbox, accumulation="24h")` → `model_flood_scenario(forcing_raster_uri=mrms_uri)` → `publish_layer`. Warning polygon displayed as a separate layer alongside the MRMS precip raster and the SFINCS flood depth layer (3-layer accumulation on map). File ownership: `services/agent/workflows/model_nws_flood_event_scenario.py` (new).

### Conversational analysis UI sub-track

| Job ID | Title | Specialist | Model | Est. Tokens | Depends on | Adv. Verify |
|--------|-------|-----------|-------|-------------|------------|-------------|
| job-0230-agent-TBD | Chart-generation tools (4 tools) + agent chart-emission loop | agent | opus | 200K | job-0223 | no |
| job-0231-web-TBD | Chart inline stacked preview + gallery popup web | web | sonnet | 200K | job-0223, job-0230 | no |
| job-0232-infra-TBD | Python sandbox Cloud Run Job container | infra | opus | 200K | Wave 4.11 close | YES |
| job-0233-agent-TBD | code_exec_request envelope + agent sandbox dispatch | agent | opus | 150K | job-0232 | no |
| job-0234-web-TBD | Sandbox result card + user-confirm gate web | web | sonnet | 100K | job-0233 | no |

**job-0230 scope:** Four chart-generation agent tools: `generate_histogram(layer_uri, property)`, `generate_choropleth_legend(layer_uri)`, `generate_time_series(layer_uri)` (for temporal rasters), `generate_damage_distribution(damage_layer_uri)`. Each tool: reads layer data → computes chart data → emits `chart-emission` envelope (Vega-Lite spec). Agent main loop feeds chart data summary back to Gemini as `function_response` for narration. File ownership: `services/agent/tools/chart_tools.py` (new), `services/agent/adapter.py` (chart-emission dispatch, additive).

**job-0231 scope:** Web rendering: `vega-embed` integration. Inline stacked-image preview: top chart visible at ~200×150px; subsequent charts stack behind with ~4px offset; `+N` badge if >3 charts in stack. Stack assembly rule: same agent turn / same tool-call sequence → one stack. Click → full-viewport gallery overlay with prev/next arrows and save-as-PNG button. Charts persist in MongoDB sessions (replay on Case rehydration). File ownership: `web/src/components/ChartStack.tsx` (new), `web/src/components/ChartGallery.tsx` (new), `web/src/components/ChatMessage.tsx` (additive — chart-emission envelope rendering hook).

**job-0232 scope (adversarial-verify gated):** Cloud Run Job container for the Python sandbox: rasterio + geopandas + numpy + pandas + matplotlib + scikit-learn + networkx pre-installed; network egress denied (except whitelisted GCS + Atlas endpoints); 60s wallclock cap; 2GB memory cap; read-only GCS mount for `cache/` + `runs/` buckets. Layer references injected as Python variables (LayerURI → gcsfs rasterio/geopandas handles). Returns: stdout + stderr + final `result` variable (auto-converted to chart-emission envelope if figure/DataFrame). Adversarial verify: correctness (does network egress deny actually block?) + regression + contract + live-verify (4 × Opus, ~200K). File ownership: `infra/python-sandbox/` (new dir), `services/agent/sandbox_runner.py` (new).

**job-0233 scope:** `code_exec_request(python_code: str)` envelope (server → client): triggers Cloud Run Job sandbox, feeds `function_response` back to Gemini with result variable contents. Emits user-facing card with code + result + Save button. Includes explicit user-confirm gate (same pattern as large-payload warning) before executing LLM-emitted code. File ownership: `services/agent/tools/code_exec_tool.py` (new), `packages/contracts/` (new envelope type).

**job-0234 scope:** Sandbox result card web: code block display + result display (inline or chart-emission link) + Save button + user-confirm modal before execution. File ownership: `web/src/components/SandboxCard.tsx` (new), `web/src/components/ChatMessage.tsx` (additive).

## Stage 3 — Live Gate (Acceptance)

All acceptance jobs gated on all Stage 2 jobs approved.

| Job ID | Title | Specialist | Model | Est. Tokens | Depends on | Adv. Verify |
|--------|-------|-----------|-------|-------------|------------|-------------|
| job-0235-testing-TBD | Case 2 full E2E acceptance — news → MODFLOW → plume | testing | opus | 200K | job-0228, adversarial panel | YES |
| job-0236-testing-TBD | Case 3 acceptance — NWS alert → MRMS → SFINCS Idaho | testing | sonnet | 150K | job-0229 | no |
| job-0237-testing-TBD | Conversational analysis layer acceptance | testing | opus | 150K | job-0230, job-0231, job-0233, job-0234 | YES |
| job-0238-testing-TBD | Python sandbox acceptance | testing | sonnet | 100K | job-0232, job-0233, job-0234 | no |

**job-0235 scope (adversarial-verify gated):** Playwright live run: paste a real or realistic news URL about a groundwater contamination event → agent extracts spill parameters → confirms with user (confirmation gate fires) → runs MODFLOW → plume layer renders on map → chat narrates plume extent and concentration. Screenshot evidence. Adversarial verify: correctness (are extracted parameters geographically plausible?) + live-verify (4 × Opus, ~200K).

**job-0236 scope:** Playwright live run: send "Show me flood warnings in Idaho, then model the flood" → agent calls `fetch_nws_alerts_conus` → selects a flood warning → fetches MRMS precip → runs SFINCS → 3 layers render: alerts polygon, MRMS precip raster, flood depth. Non-Florida geography confirmed. Screenshot evidence.

**job-0237 scope (adversarial-verify gated):** Playwright live run: after a flood or damage layer is on the map, send "how many structures are impacted?" and "show me a damage distribution" → agent calls `count_features_above_threshold` → narrates count → calls `generate_damage_distribution` → chart-emission lands inline in chat → gallery popup opens. Also verify chart persistence: reload Case → charts replay. Adversarial verify: correctness + live-verify lenses (4 × Opus, ~200K).

**job-0238 scope:** Playwright live run: agent calls `code_exec_request` with a simple numpy script (e.g., compute mean raster value) → user-confirm modal fires → sandbox executes → result narrated. Screenshot evidence showing the user-confirm gate and result card.

## Stage 4 — Close

| Job ID | Title | Specialist | Model | Est. Tokens | Depends on | Adv. Verify |
|--------|-------|-----------|-------|-------------|------------|-------------|
| job-0239-testing-TBD | Sprint-13 close — full regression sweep + sprint-14 stub | testing | opus | 100K | job-0235, job-0236, job-0237, job-0238 | no |

**job-0239 scope:** Full regression sweep (all suites + tsc). Sprint-13 retrospective. Sprint-14 manifest stub. Update PROJECT_STATE.md (tool count, sprint close, known issues).

## Execution Order

```
[prerequisite] Wave 4.11 all jobs approved
       |
STAGE 1 (parallel, file-disjoint):
  job-0220  (MODFLOW container)          ← adversarial verify before Stage 2 MODFLOW
  job-0221  (mf6-gwt adapter)            ← adversarial verify before Stage 2 MODFLOW
  job-0222  (MODFLOW contracts schema)
  job-0223  (chart-emission schema)      ← adversarial verify before Stage 2 charts
  job-0224  (analytical Q&A tools)
  job-0225  (model_flood_scenario v2)
  job-0226  (fetch_mrms_qpe)
       |
STAGE 2 (gated on Stage 1, parallel within sub-tracks):
  MODFLOW sub-track:
    job-0227  (Cloud Workflows + run_solver binding)
    job-0228  (Case 2 composer)           ← adversarial verify before Stage 3
  Case 3 sub-track:
    job-0229  (Case 3 composer)
  Conversational analysis sub-track:
    job-0230  (chart-generation tools)
    job-0231  (chart web UI)              [gated on job-0223 + 0230]
    job-0232  (Python sandbox container)  ← adversarial verify before Stage 3
    job-0233  (code_exec_request)         [gated on job-0232]
    job-0234  (sandbox result card web)   [gated on job-0233]
       |
STAGE 3 (gated on Stage 2 adversarial panels + all Stage 2 approvals):
  job-0235  (Case 2 acceptance)          ← adversarial verify
  job-0236  (Case 3 acceptance)
  job-0237  (conversational analysis acceptance)  ← adversarial verify
  job-0238  (Python sandbox acceptance)
       |
STAGE 4:
  job-0239  (close + sprint-14 stub)
```

**OQ budget guard:** more than 4 blocking open questions at end of Stage 1 halts Stage 2 dispatch pending orchestrator triage.

## Adversarial Verify Schedule

| Target Job | Panel Trigger | Lenses | Est. Panel Cost |
|---|---|---|---|
| job-0220 (MODFLOW container) | after ready-for-audit | 4 lenses | ~200K Opus |
| job-0221 (mf6-gwt adapter) | after ready-for-audit | 4 lenses | ~200K Opus |
| job-0223 (chart-emission schema) | after ready-for-audit | 2 lenses (correctness + contract) | ~100K Opus |
| job-0228 (Case 2 composer) | after ready-for-audit | 4 lenses | ~200K Opus |
| job-0232 (Python sandbox) | after ready-for-audit | 4 lenses (network egress focus) | ~200K Opus |
| job-0235 (Case 2 acceptance) | after ready-for-audit | 4 lenses (geo-plausibility focus) | ~200K Opus |
| job-0237 (conv. analysis acceptance) | after ready-for-audit | 4 lenses | ~200K Opus |

## Gating + Acceptance Criteria

Engine substrate:
- [ ] MODFLOW 6 container builds and MF6 binary executes a minimal test model in Cloud Run Job (job-0220 evidence — Cloud Build log + test execution log)
- [ ] mf6-gwt adapter produces non-zero concentration fields on synthetic spill inputs (job-0221 evidence — output file inspection)
- [ ] `model_flood_scenario` v2 accepts `forcing_raster_uri` and produces a flood COG distinct from the Atlas-14 design storm (job-0225 evidence — two-run comparison)

Case demos:
- [ ] Case 2 full E2E: news input → agent-confirmed spill parameters → MODFLOW run completes → plume layer renders on map → chat narrates plume extent + max concentration (job-0235 Playwright screenshot)
- [ ] Case 2 is NOT Fort Myers — new geography, new hazard domain (groundwater) verified
- [ ] Case 3: Idaho NWS flood warning polygon rendered + MRMS precip raster overlay + SFINCS flood depth layer — 3-layer accumulation on map (job-0236 Playwright screenshot)
- [ ] Case 3 geography: non-Florida bbox confirmed in layer extents

Conversational analysis:
- [ ] `summarize_layer_statistics`, `count_features_above_threshold`, `aggregate_property_within_zone` registered and return non-empty results on known layers (job-0224 live-run evidence)
- [ ] Chart emission: `generate_damage_distribution` produces a valid Vega-Lite spec; client renders it inline; gallery popup opens on click (job-0237 Playwright screenshot)
- [ ] Charts persist across Case rehydration: chart emitted in one session reappears on Case reload (job-0237 evidence)
- [ ] Python sandbox: user-confirm gate fires before code execution; sandbox executes simple numpy script; result narrated in chat (job-0238 Playwright screenshot)
- [ ] Python sandbox network egress: attempt to import `requests` or fetch an external URL fails inside sandbox (job-0238 evidence)

Close:
- [ ] Full regression sweep: 0 new failures (job-0239 evidence)
- [ ] Sprint-14 manifest stub authored (job-0239 deliverable)

## Token Budget

| Stage | Jobs | Est. Tokens |
|---|---|---|
| Stage 1 — engine substrate (7 jobs) | MODFLOW + schema + analysis tools | ~780K |
| Stage 2 — workflows + composers + UI (8 jobs) | MODFLOW workflows + Case composers + chart/sandbox UI | ~1,200K |
| Stage 3 — live gate acceptance (4 jobs) | Case 2 + Case 3 + analysis + sandbox | ~600K |
| Stage 4 — close (1 job) | regression sweep | ~100K |
| Adversarial verify panels (7 panels) | ~200K each | ~1,300K |
| **Total** | **20 jobs** | **~3.98M** |

## File Ownership Boundaries (sprint-13 vs sprint-13.5 disjoint)

Sprint-13 owns:
- `services/workers/modflow/` (new directory — all MODFLOW engine files)
- `services/agent/workflows/model_groundwater_contamination_scenario.py`
- `services/agent/workflows/model_nws_flood_event_scenario.py`
- `services/agent/workflows/model_flood_scenario.py` (additive amendment to v2 forcing branch)
- `services/agent/tools/chart_tools.py`, `services/agent/tools/code_exec_tool.py`
- `services/agent/sandbox_runner.py`
- `web/src/components/ChartStack.tsx`, `ChartGallery.tsx`, `SandboxCard.tsx`
- `infra/modflow/` (new directory), `infra/python-sandbox/` (new directory)
- `packages/contracts/` (MODFLOW contracts + chart contracts + sandbox envelope)

Sprint-13.5 owns:
- `infra/firebase/` (production project provisioning)
- `infra/signed_urls/` (Cloud Function minting service)
- `services/agent/auth.py` (sticky-user-id → real auth migration)
- `web/src/auth.ts` (Firebase Auth SDK integration)
- All `infra/main.tf` changes to Cloud Run services for production deploy

No overlap. If a job touches both ownership zones, it must be escalated to the orchestrator for routing decision before dispatch.

## Deferred to Sprint 14

- InVEST analytical tools (`run_invest_carbon_storage` and sub-models) — no specific case driving yet
- HRRR-Smoke air-quality overlay (if not landed in Wave 4.11 job-0214)
- TELEMAC-2D coastal surge engine — awaiting user-defined coastal case
- HEC-HMS urban watershed engine — awaiting user-defined watershed case
- Tier-2 conservation fetchers (eBird, IUCN, Movebank) in production — Secrets UX from sprint-12-mega must be live and verified first
- Per-species color-coded layer chrome UX — deferred per user direction 2026-06-08 until demo is stable

## Open Questions

1. **MT3D-USGS vs mf6-gwt decision**: SRS §2.3 leaves solute transport solver as TBD (OQ-9). This manifest defaults to mf6-gwt (native MODFLOW 6 solute transport module, no separate binary required). If user prefers MT3D-USGS (more mature, richer reaction kinetics), job-0221 scope changes significantly (different binary, different flopy API). TENTATIVE: mf6-gwt. Escalate if user has a specific contaminant chemistry case that requires MT3D reaction network.

2. **Case 2 news article source**: the composer (job-0228) needs a real or realistic news article URL to demonstrate the news-ingest path. If no real event is suitable at dispatch time, the testing job (job-0235) must use a synthetic article fixture that exercises the full parameter-extraction chain. TENTATIVE: synthetic fixture acceptable for acceptance; the testing job should document the fixture and flag it as synthetic.

3. **MODFLOW aquifer parameterization for Case 2 demo**: the Fort Myers area (saturated sandy coastal plain) has published hydrogeologic parameters. The Longview-style spill scenario may require different aquifer types. At dispatch time, confirm whether the composer (job-0228) should use a hard-coded demo-parameterization or expose aquifer properties as confirmed parameters to the user. TENTATIVE: hard-coded demo defaults (K=1e-4 m/s, porosity=0.3) with an explicit user-narrated caveat that these are demo values.

4. **chart-emission + MongoDB sessions integration**: charts must persist and replay on Case rehydration. The `sessions` collection schema was designed in Wave 4.11 (job-0201). Confirm that the chart-emission JSON blob fits within the sessions append-only pattern before job-0223 dispatches. If it requires a separate `charts` collection, that is a schema amendment (escalate to orchestrator + schema specialist). TENTATIVE: chart specs are stored as a field array in the session document (same collection, no new collection needed).

5. **Python sandbox pre-approve vs LLM-emit**: the spec calls for a user-confirm modal before executing LLM-emitted code. Confirm the correct UX: (a) show full code + summary + Proceed/Cancel, or (b) show only a summary card + "Show code" toggle + Proceed/Cancel. TENTATIVE: option (a) — show full code, consistent with the large-payload warning pattern.

6. **`model_flood_scenario` v2 SFINCS forcing integration**: SFINCS accepts precipitation as `netamt` (uniform mm/hr) or `spw` (spatially-variable precip via NetCDF). Mapping a MRMS QPE raster to one of these input types is non-trivial. If the SFINCS container version (from sprint-7 job-0040) does not support `spw`, the v2 forcing branch will need a fallback (compute area-mean QPE → single `netamt` value). This decision should be locked in job-0225. TENTATIVE: fallback to area-mean `netamt` for v0.1 (simpler, still demonstrates the real-data forcing path); upgrade to `spw` spatial input in a future job when the SFINCS container version is confirmed.
