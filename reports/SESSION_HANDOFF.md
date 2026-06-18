# SESSION HANDOFF — resume here

**Refreshed 2026-06-17** after a large build session. Read AFTER the normal bootstrap
(CLAUDE.md → AGENTS.md → orchestrator.md → PROJECT_STATE.md → MEMORY.md). Greeting: address
the user as **NATE ALMANZA**; **no emojis**. Auto-memory under `~/.claude/.../memory/` holds
the durable detail; this is the live "pick up here" note.

---

## 0. CURRENT LIVE INFRA (verified 2026-06-17, account 226996537797 / us-west-2)
- **Agent box** `i-0251879a278df797f` (t3.large, EIP 54.185.114.233): runs grace2-agent
  (WS :8765) + catalog/health (:8766). **Auto-stop ARMED** (Wave-3) → it STOPS when idle
  (zero WS connections, no in-flight solve, 3 idle polls ~15 min) and is woken via the
  API-Gateway `/wake` endpoint (the web "Wake up agent" overlay calls it on load). It was
  observed `stopped` after going idle — working as designed.
- **NEW tiles box** `i-06cfdd3d6c66b2126` (t3.small, EIP 44.247.187.124): **always-on**,
  runs TiTiler (:8080), serves CloudFront `/tiles*`+`/cog/*` 24/7 (`infra/aws-titiler/`).
  The map stays alive even when the agent box is stopped (verified 200). pip needs `httpx`
  (in `titiler_pip_spec` now).
- **CloudFront** E2L74AS56MVZ87 (d125yfbyjrpbre.cloudfront.net): `/ws`,`/api/*` → agent box;
  `/tiles*`,`/cog/*` → tiles box; SPA from S3 (grace2-hazard-web-226996537797).
- **SFINCS** solves: AWS Batch (Spot, scale-to-zero). **Persistence**: DynamoDB. **LLM**:
  Bedrock (Sonnet default; Haiku/Nova selectable; cachePoint Anthropic-only).
- **Auto compute-class per case** (small→xlarge by mesh element count): CODE committed; the
  `infra/aws-batch` CE instance-type bump (c7i ladder + 48-vCPU xlarge, max_vcpus 96) is
  AUTHORED but **not yet `tofu apply`-ed**.

## 1. PROD-DEPLOY STATE — IMPORTANT
The LIVE agent on the box runs PRE-this-session code + the Wave-3 `/api/health` liveness
change ONLY. This session's NEW engine work is **committed but NOT deployed to the agent box**:
Phase-1 animation, the full PySWMM urban engine, coastal SFINCS forcing/obstacles, auto-class.
To deploy: **wake the agent box**, add `pyswmm` + `swmm-api` + `httpx` (+ hydromt forcing deps)
to the PROD venv, run targeted tests, then SSM-deploy. Deliberately not done yet.

## 2. BUILT + COMMITTED THIS SESSION (origin/main, HEAD `bf1d443`)
- **TiTiler isolation + agent auto-stop/wake** — LIVE (commits 85d36e2, bf1d443).
- **PySWMM urban engine, full local lane** (b5013cf, 88a1e5c): P0 spike (GO) → P1 contracts +
  Atlas-14 nested hyetograph → P2 DEM→quasi-2D mesh builder → P3 postprocess_swmm → P4
  model_urban_flood_swmm workflow + run_swmm_urban_flood tool + local pyswmm lane. Runs
  prompt→mesh→solve→peak+24 animation frames locally; adversarially verified. Red wall =
  omit conduit; green flap = Orifice has_flap_gate (one-way); buildings = dropped cells.
- **Phase-1 flood animation** (505fedc): postprocess_flood per-frame COGs → Wave-1 scrubber.
- **Coastal SFINCS** (166c3ba): surge/tide/discharge/wind/pressure forcing + building
  obstacles in sfincs_builder; SnapWave deck format decoded (spike in
  services/workers/sfincs_snapwave_spike/).
- **Auto compute-class + xlarge tier** (ce9ef74).

## 3. NEXT — COASTAL SFINCS IS THE LEAD NORTH STAR (NATE)
Remake the Deltares **Hurricane Michael / Mexico Beach 2018** demo: SFINCS quadtree + SnapWave
(incident + infragravity waves, wave forces, 2 m-contour paddles for run-up), **animated
waves**. Forcing is DONE; the GATE is quadtree+SnapWave deck authoring (FEASIBLE-WITH-CAVEATS;
adopt **Deltares cht_sfincs** for the nr_levels=6 connectivity). Sequence: P0 hydrograph→CSV
forcing glue → Mexico Beach topobathy (CUDEM + 3DEP, NAVD88) → quadtree + SnapWave → wave
animation (hm0/hm0ig energy heatmap + wavemaker propagating crests — the fidelity choice is
NATE's call at that step) → computed-vs-observed validation. Full plan: tasks/wdsicm75a.output
+ `project_sfincs_north_star_demo`. Building obstacles also give a rough urban-flood look in
SFINCS. Urban PySWMM demo continues in parallel (engine done; remaining = P5/P6 walls/flap
draw UI [needs the one-line SRS amendment], P7 SWMM Batch worker, P8 live).

## 4. DECIDED + SCOPED (not built)
- **HEC stack** (`reports/hec_stack_recommendation.md`): HMS first → HEC-RAS. Both
  compute-feasible headless on Linux (USACE public-domain binaries). v0.1 = templated decks +
  safe-knob parameter-sweep; licensing OK to bake binaries into ECR. Queued behind the North
  Stars. HMS hydrographs also feed SFINCS discharge forcing (compound).
- **Flood-pipeline reference**: `reports/flood_pipeline_reference.md`.
- **PySWMM urban build plan**: `reports/pyswmm_urban_build_plan.md`.
- **Sediment/dye minor North Star**: HEC-RAS sediment + water-quality modules + tracer
  advection + the MODFLOW-GWT surface→river→groundwater thread. Frame = the Baird "datasets"
  slide bottom-right (`reports/references/lecture_baird_coastal/14_datasets_used.png`).

## 5. GOTCHAS / NORMS
- Container hygiene (NATE): INSPECT any image (size + `docker history` + .dockerignore) before
  an ECR push; multi-stage + minimal base. Applies to SWMM worker, HEC binaries, QGIS worker.
- auto-stop never stops an active box (fail-safe to busy). Wake = POST the `/wake` API-GW URL.
  Runbook: `infra/aws-autostop/RUNBOOK.md`. variables.tf default dry_run=false — always pass
  `-var=dry_run=...` explicitly.
- Pre-existing test-ordering leak: test_sfincs_autoscale before test_model_flood_scenario (NOT
  from this session's changes) — testing-specialist item.
- Pending hygiene: log this session's heavy workflow token spend to cost_tracking.json.

## 6. STANDING DIRECTIVES
Greet NATE ALMANZA; no emojis; commit + push per big change (origin/main, double-r-squared/
GRACE-2); continuous deploy (no per-deploy gate); data-source fallback norm; use workflows to
keep context lean; multiSelect decision pickers; **research real reference pipelines before
building each engine**; cost discipline (but suspend it for architecture-grounding).
