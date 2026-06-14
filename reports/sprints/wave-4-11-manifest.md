# Wave 4.11: MongoDB MCP Canonical Migration + Pelicun Impact Post-Processor + Wildfire Data Top-Up

**Status:** planned
**Opened:** 2026-06-09
**Closed:** —
**Gated on:** Wave 4.10 close (all Wave 4.10 stages approved)
**SRS milestones covered:** Appendix D (MongoDB MCP canonical persistence); Decision N + Appendix B.6c (Pelicun ImpactEnvelope); M5.5 Pelicun impact post-processor; wildfire data completeness (RAWS, HRRR-Smoke, USFS canopy fuels).

## Goal

Three workstreams running in two stages. At wave close: (1) every Cases/sessions/users CRUD operation routes through MongoDB MCP — no bespoke collection wrappers; (2) `tool_call_telemetry` collection live, powering a data-driven hot-set and a routing-quality dashboard; (3) `discover_dataset` backed by a live Mongo index rather than memory-only retrieval; (4) the full Pelicun ImpactEnvelope pipeline operational on the existing Fort Myers flood COG, emitting user-visible headline numbers ("12,300 structures impacted, ~$X.YB in damages"); (5) three wildfire data gaps filled (RAWS weather observations, HRRR-Smoke plumes, USFS canopy fuels). Total projected: ~2.5M tokens.

## Model routing policy

Workstream A (MongoDB MCP infra + telemetry writer + schema design): Sonnet. `discover_dataset` live-Mongo backend and hot-set live query: Opus (routing-substrate changes). Workstream B (Pelicun): Opus for postprocess tool, composer, and live demo acceptance (physics-adjacent correctness + user-facing numbers). Wildfire fetchers: Sonnet (established fetcher pattern). Adversarial verify panel (4 × Opus): folded into high-importance jobs per `feedback_adversarial_verify_high_importance`.

## Workstream A — MongoDB MCP Server + Telemetry + Routing Substrate

Lead workstream. Establishes the canonical persistence path that sprint-13 (Cases/sessions) and sprint-13.5 (auth, signed-URL audit log) depend on.

### Stage 1A (parallel within workstream A)

| Job ID | Title | Specialist | Model | Est. Tokens | Depends on | Adv. Verify |
|--------|-------|-----------|-------|-------------|------------|-------------|
| job-0200-infra-20260609 | MongoDB MCP server provisioning + Atlas connection | infra | sonnet | 150K | Wave 4.10 close | no |
| job-0201-schema-20260609 | Schema design + collection bootstrap | schema | sonnet | 100K | Wave 4.10 close | no |

**job-0200 scope:** Install MongoDB MCP server into `services/agent/`; wire Atlas connection string from `.env`; smoke-test MCP tooling from the agent process against a `tool_call_telemetry_test` collection; document the wire protocol (connection pooling, auth, error surface). File ownership: `services/agent/mcp_config.py`, `services/agent/.env.example` (connection-string key), `infra/` secrets plumbing (Atlas URI in Secret Manager reference).

**job-0201 scope:** Define JSON Schema validators for three Atlas collections: `tool_call_telemetry` (fields: `session_id`, `user_id`, `tool_name`, `called_at_utc`, `result_ok`, `latency_ms`, `cache_content_token_count`), `description_audit` (tool description variants + routing-correctness scores), `case_telemetry` (Case-level aggregates). Create Atlas indexes (TTL 90d on `called_at_utc` for telemetry; BM25 + dense on `tool_name`/`description` for audit). Author Appendix D.X amendment proposal (orchestrator lands into `docs/srs/` after user confirms). File ownership: `packages/contracts/src/grace2_contracts/mongo_schemas/`, new collection validator YAML files.

### Stage 2A (gated on Stage 1A)

| Job ID | Title | Specialist | Model | Est. Tokens | Depends on | Adv. Verify |
|--------|-------|-----------|-------|-------------|------------|-------------|
| job-0202-agent-20260609 | Telemetry writer integration in adapter.py | agent | sonnet | 120K | job-0200, job-0201 | no |
| job-0203-agent-20260609 | Cases/sessions/users CRUD migration to MongoDB MCP | agent | opus | 200K | job-0200, job-0201 | YES |
| job-0204-agent-20260609 | discover_dataset live-Mongo backend | agent | opus | 200K | job-0202 | YES |
| job-0205-agent-20260609 | Hot-set live-Mongo query | agent | sonnet | 80K | job-0202 | no |
| job-0206-web-20260609 | Routing-quality dashboard in Settings | web | sonnet | 120K | job-0202 | no |

**job-0202 scope:** Every LLM-initiated `function_call` in `adapter.py` emits a `tool_call_telemetry` event through the MCP server (non-blocking write — fire-and-forget with error logged, never raises). Harvest `cache_content_token_count` from Gemini `usage_metadata` per turn. File ownership: `services/agent/adapter.py`, `services/agent/telemetry.py` (new).

**job-0203 scope (adversarial-verify gated):** Migrate Cases, sessions, and users CRUD from any bespoke collection wrappers to MongoDB MCP calls. Delete the old wrapper code (remove-don't-shim). Every `projects` document create/read/update, every `sessions` chat-history append, every user lookup must route through MCP. This is the data-loss-risk job. Adversarial verify: correctness + regression + contract + live-verify lenses (4 × Opus, ~200K additional). File ownership: `services/agent/case_store.py`, `services/agent/session_store.py`, `services/agent/user_store.py` — all migrated in place; old wrappers deleted.

**job-0204 scope (adversarial-verify gated):** Replace memory-only `discover_dataset` retrieval with Mongo-backed retrieval: BM25 + co-occurrence score from `tool_call_telemetry` co-call frequency. Daily index refresh (Cloud Scheduler → simple refresh script). This changes the routing substrate — adversarial verify: correctness + regression + contract + live-verify (4 × Opus, ~200K additional). File ownership: `services/agent/tool_discovery.py`.

**job-0205 scope:** `get_hot_set(user_id, session_id)` returns top-N tools from last 30 sessions × user prefs from the live `tool_call_telemetry` collection. Replaces any hard-coded hot-set list. File ownership: `services/agent/hot_set.py`.

**job-0206 scope:** Settings → Tools → routing-quality dashboard: most-called, most-failed, last-seen, average latency per tool. Read-only MCP query. File ownership: `web/src/components/SettingsDashboard.tsx` (new), `web/src/components/Settings.tsx` (new route).

## Workstream B — Pelicun Impact Post-Processor

Runs in parallel with Workstream A Stage 1A. Pelicun schema job launches immediately after Wave 4.10 close.

### Stage 1B (parallel with Stage 1A — file-disjoint)

| Job ID | Title | Specialist | Model | Est. Tokens | Depends on | Adv. Verify |
|--------|-------|-----------|-------|-------------|------------|-------------|
| job-0207-schema-20260609 | Pelicun ImpactEnvelope schema — Decision N + Appendix B.6c amendment | schema | sonnet | 80K | Wave 4.10 close | YES |

**job-0207 scope (adversarial-verify gated):** Author `ImpactEnvelope` JSON Schema: damage stats (`mean_damage_state`, `damage_state_distribution`), exposure stats (`structure_count`, `structures_impacted`, `structures_complete_loss`), population-at-risk (count + confidence interval), economic loss aggregates (`total_repair_cost_mean`, `total_repair_cost_p10`, `total_repair_cost_p90`). Author Decision N amendment + Appendix B.6c append as proposal; orchestrator lands after user confirms. This schema is downstream-consumer-sensitive — adversarial verify: correctness + contract + regression lenses (3 × Opus sufficient for schema-only job, ~150K additional). File ownership: `packages/contracts/src/grace2_contracts/impact_envelope.py` + JSON schema export.

### Stage 2B (gated on job-0207)

| Job ID | Title | Specialist | Model | Est. Tokens | Depends on | Adv. Verify |
|--------|-------|-----------|-------|-------------|------------|-------------|
| job-0208-engine-20260609 | postprocess_pelicun atomic tool | engine | opus | 200K | job-0207 | YES |
| job-0209-agent-20260609 | compute_impact_envelope workflow composer | agent | opus | 150K | job-0208 | no |
| job-0210-web-20260609 | Web impact panel — ImpactEnvelope side-panel | web | opus | 150K | job-0207 | no |

**job-0208 scope (adversarial-verify gated):** `postprocess_pelicun` atomic tool: reads Pelicun damage assessment output (DL_summary.csv + EDP_demands.csv) + Fort Myers flood COG + USACE NSI structure inventory → produces `ImpactEnvelope` JSON + summary raster (damage state per structure as COG). Returns `LayerURI` for the summary raster + inline `ImpactEnvelope`. Adversarial verify: correctness (physics — are DS numbers plausible for the Fort Myers forcing?) + regression + contract + live-verify (4 × Opus, ~200K additional). File ownership: `services/workers/tools/postprocess_pelicun.py` (new).

**job-0209 scope:** `compute_impact_envelope` workflow composer: chains `run_pelicun_with_buildings` → `postprocess_pelicun` → `publish_impact_layer`. Emits headline numbers into chat narration envelope. File ownership: `services/agent/workflows/compute_impact_envelope.py` (new).

**job-0210 scope:** Web impact panel: side-panel showing `ImpactEnvelope` statistics (structure count, impacted %, repair cost range, population at risk). Per-component fragility curve mini-chart. Pairs with existing damage assessment layer in LayerPanel. File ownership: `web/src/components/ImpactPanel.tsx` (new), `web/src/components/LayerPanel.tsx` (additive only).

### Stage 3B — Live Demo Acceptance (gated on Stages 2A + 2B)

| Job ID | Title | Specialist | Model | Est. Tokens | Depends on | Adv. Verify |
|--------|-------|-----------|-------|-------------|------------|-------------|
| job-0211-testing-20260609 | Pelicun live demo acceptance — Fort Myers ImpactEnvelope | testing | opus | 150K | job-0208, job-0209, job-0210 | no |
| job-0212-testing-20260609 | Telemetry + routing substrate live verify | testing | sonnet | 100K | job-0203, job-0204, job-0205, job-0206 | no |

**job-0211 scope:** Playwright-driven live run: send "Model flood damage for Fort Myers using the existing flood layer" → agent calls `compute_impact_envelope` → ImpactEnvelope JSON produced → chat narrates headline numbers → side-panel renders damage stats. Screenshot evidence. Verify: `structures_impacted` > 0, `total_repair_cost_mean` > 0, damage summary raster renders as WMS layer.

**job-0212 scope:** Verify telemetry writer fires (read `tool_call_telemetry` collection via Atlas CLI — confirm at least one document landed). Verify `get_hot_set` returns non-empty result after 1 session. Verify routing-quality dashboard loads in Settings. Verify Cases CRUD still round-trips correctly after migration (create Case, persist chat turn, read back — MCP trace in agent logs).

## Workstream C — Wildfire Data Top-Up (parallel with Stage 1A/1B)

Small three-job workstream, all Sonnet. No adversarial verify required (established fetcher pattern). File-disjoint from all Workstream A and B jobs.

| Job ID | Title | Specialist | Model | Est. Tokens | Depends on | Adv. Verify |
|--------|-------|-----------|-------|-------------|------------|-------------|
| job-0213-engine-20260609 | fetch_raws_weather — Iowa State IEM CGI | engine | sonnet | 80K | Wave 4.10 close | no |
| job-0214-engine-20260609 | fetch_hrrr_smoke — HRRR-Smoke Zarr plume forecasts | engine | opus | 120K | Wave 4.10 close | no |
| job-0215-engine-20260609 | fetch_usfs_canopy_fuels — USFS ArcGIS Hub semantic wrapper | engine | sonnet | 80K | Wave 4.10 close | no |

**job-0213 scope:** `fetch_raws_weather(bbox, variables=["wind_speed","rh","temp"], lookback_hours=24)` — Iowa State IEM CGI endpoint; returns GeoJSON FeatureCollection of RAWS station observations. File ownership: `services/workers/tools/fetch_raws_weather.py` (new).

**job-0214 scope:** `fetch_hrrr_smoke(bbox, forecast_hour=6)` — HRRR-Smoke Zarr on NOAA S3; smoke plume PM2.5 forecast raster; COG output; publishes via `publish_layer`; returns `LayerURI` with WMS URL. File ownership: `services/workers/tools/fetch_hrrr_smoke.py` (new). Opus justified: Zarr/HRRR-Smoke access pattern is non-trivial (chunk navigation, variable naming conventions).

**job-0215 scope:** `fetch_usfs_canopy_fuels(bbox)` — thin semantic wrapper over `fetch_from_arcgis_hub` targeting USFS canopy base height / bulk density rasters. Verifies the correct service ID and layer fields. File ownership: `services/workers/tools/fetch_usfs_canopy_fuels.py` (new).

## Wave Close

| Job ID | Title | Specialist | Model | Est. Tokens | Depends on | Adv. Verify |
|--------|-------|-----------|-------|-------------|------------|-------------|
| job-0216-testing-20260609 | Wave 4.11 close + sprint-13 pre-flight | testing | opus | 80K | job-0211, job-0212, job-0213, job-0214, job-0215 | no |

**job-0216 scope:** Full regression sweep (all suites). Sprint-13 pre-flight: verify MODFLOW container prerequisites documented in PROJECT_STATE.md; author sprint-13 manifest stub if not already present.

## Execution Order

```
[prerequisite] Wave 4.10 all stages approved
       |
       v
Stage 1 (parallel, file-disjoint):
  job-0200-infra   (MongoDB MCP provisioning)
  job-0201-schema  (collection schemas + Atlas indexes)
  job-0207-schema  (Pelicun ImpactEnvelope schema)  ← adversarial verify before Stage 2B
  job-0213-engine  (fetch_raws_weather)
  job-0214-engine  (fetch_hrrr_smoke)
  job-0215-engine  (fetch_usfs_canopy_fuels)
       |
       v
Stage 2 (gated on Stage 1):
  job-0202-agent   (telemetry writer)
  job-0203-agent   (Cases/sessions/users CRUD migration)  ← adversarial verify before Stage 3
  job-0204-agent   (discover_dataset live-Mongo)          ← adversarial verify before Stage 3
  job-0205-agent   (hot-set live-Mongo query)
  job-0206-web     (routing-quality dashboard)
  job-0208-engine  (postprocess_pelicun)                  ← adversarial verify before Stage 3
  job-0209-agent   (compute_impact_envelope composer)  [gated on 0208]
  job-0210-web     (web impact panel)
       |
       v
Stage 3 (gated on Stage 2 adversarial verify panels):
  job-0211-testing  (Pelicun live demo acceptance)
  job-0212-testing  (telemetry + routing live verify)
       |
       v
Wave close:
  job-0216-testing  (full regression sweep + pre-flight)
```

**OQ budget guard:** more than 3 blocking open questions at end of Stage 1 halts Stage 2 dispatch pending orchestrator triage.

## Adversarial Verify Schedule

Each adversarial-verify job spawns 4 parallel Opus verifiers (correctness / regression / contract / live-verify lenses). Each verifier is prompted to refute by default; ≥3 of 4 confirm → approved + advance. Estimated ~200K tokens per panel. Verdict recorded in `reports/inflight/<job-id>/adversarial_verdict.md`.

| Target Job | Panel Trigger | Est. Panel Cost |
|---|---|---|
| job-0203 (Cases/sessions CRUD migration) | after job-0203 ready-for-audit | ~200K Opus |
| job-0204 (discover_dataset live-Mongo) | after job-0204 ready-for-audit | ~200K Opus |
| job-0207 (ImpactEnvelope schema) | after job-0207 ready-for-audit | ~150K Opus |
| job-0208 (postprocess_pelicun) | after job-0208 ready-for-audit | ~200K Opus |

## Gating + Acceptance Criteria

- [ ] MongoDB MCP server smoke-test passes: agent connects, writes one document, reads it back (job-0200 evidence)
- [ ] `tool_call_telemetry` collection exists in Atlas with correct schema + TTL index (job-0201 evidence — Atlas CLI output)
- [ ] `ImpactEnvelope` JSON Schema exported and passing contract test suite (job-0207 evidence)
- [ ] Cases/sessions/users CRUD migration adversarial verify: ≥3 of 4 lenses confirm — no regressions (job-0203 verdict)
- [ ] `discover_dataset` adversarial verify: ≥3 of 4 lenses confirm — routing correctness preserved or improved (job-0204 verdict)
- [ ] `postprocess_pelicun` adversarial verify: ≥3 of 4 lenses confirm — ImpactEnvelope numbers physically plausible for Fort Myers forcing (job-0208 verdict)
- [ ] Pelicun live demo: Fort Myers run produces ImpactEnvelope with `structures_impacted > 0` and `total_repair_cost_mean > 0`; chat narrates headline numbers; side-panel renders (job-0211 Playwright screenshot)
- [ ] Telemetry live verify: at least one `tool_call_telemetry` document in Atlas after a live agent session (job-0212 Atlas CLI output)
- [ ] `fetch_raws_weather`, `fetch_hrrr_smoke`, `fetch_usfs_canopy_fuels` registered at startup and return non-empty results on a known bbox (job-0213–0215 live-run evidence)
- [ ] Full regression sweep: 0 new failures (job-0216 evidence)
- [ ] Sprint-13 pre-flight documented in PROJECT_STATE.md

## Token Budget

| Workstream | Jobs | Est. Tokens |
|---|---|---|
| A — MongoDB MCP + routing substrate | 7 jobs | ~870K |
| A — adversarial verify panels (2 panels) | — | ~400K |
| B — Pelicun ImpactEnvelope | 5 jobs | ~730K |
| B — adversarial verify panels (2 panels) | — | ~350K |
| C — wildfire data top-up | 3 jobs | ~280K |
| Wave close | 1 job | ~80K |
| **Total** | **16 jobs** | **~2.71M** |

## Open Questions

1. **Cases/sessions CRUD scope boundary**: The migration (job-0203) must delete old wrapper code. Are there any callers outside `services/agent/` (e.g., in `web/` direct DB calls via a REST endpoint) that would need updating in the same job, or are they all agent-side? Tentative: agent-side only; web uses WebSocket envelopes exclusively. If wrong, web specialist is a co-owner.

2. **`fetch_hrrr_smoke` Zarr access authentication**: NOAA HRRR-Smoke on S3 (`s3://noaa-hrrr-bdp/`) is public. Confirm no credentials are required and the agent's SA has internet egress to S3 endpoints. If the S3 endpoint is blocked, fall back to HRRR-Smoke NWP-based THREDDS OPeNDAP mirror (verify availability). TENTATIVE: public S3, no credentials.

3. **ImpactEnvelope Appendix B.6c amendment**: the schema job (0207) proposes the amendment; the orchestrator lands it in `docs/srs/`. Timing: land before Wave 4.11 wave-close or defer to sprint-13 SRS housekeeping pass? Tentative: land at wave-close (orchestrator-direct, does not block any job). Escalate if user wants to review the ImpactEnvelope numbers first.

4. **`discover_dataset` daily refresh scheduling**: Cloud Scheduler is already provisioned per sprint-12-mega. Confirm the agent SA has the IAM role to write the refresh index to Atlas. If not, this is an `infra` sub-task. Tentative: yes — Atlas SA key is already in Secret Manager from sprint-11 MCP setup.

5. **Wildfire data top-up sequencing with Wave 4.10**: if Wave 4.10's `fetch_from_arcgis_hub` tool is not yet landed when job-0215 dispatches (ArcGIS Hub semantic wrapper depends on it), job-0215 must self-block and wait. The orchestrator should confirm `fetch_from_arcgis_hub` is in `complete/` before dispatching job-0215.
