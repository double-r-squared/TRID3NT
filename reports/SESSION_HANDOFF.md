# SESSION HANDOFF — resume here

**Refreshed 2026-06-18** after a large engine + UX session. Read AFTER the normal bootstrap
(CLAUDE.md → AGENTS.md → orchestrator.md → PROJECT_STATE.md → MEMORY.md). Greeting: address
the user as **NATE ALMANZA**; **no emojis**. Auto-memory under `~/.claude/.../memory/` holds
the durable detail; this is the live "pick up here" note. HEAD = `ca35895` (origin/main).

---

## 0. CURRENT LIVE INFRA (account 226996537797 / us-west-2)
- **Agent box** `i-0251879a278df797f` (t3.large, EIP 54.185.114.233): grace2-agent (WS :8765)
  + catalog/health (:8766). **Auto-stop ARMED** (stops when idle; woken via the API-Gateway
  `/wake` endpoint the web "Wake up agent" overlay calls).
- **Tiles box** `i-06cfdd3d6c66b2126` (t3.small, EIP 44.247.187.124): always-on, TiTiler (:8080),
  serves CloudFront `/tiles*`+`/cog/*` 24/7. Map stays alive when the agent box is stopped.
- **CloudFront** E2L74AS56MVZ87 (d125yfbyjrpbre.cloudfront.net): `/ws`,`/api/*`→agent box;
  `/tiles*`,`/cog/*`→tiles box; SPA from S3.
- **AWS Batch solvers** (Spot, scale-to-zero). **AUTO-CLASS CE NOW LIVE** (this session): CE
  replaced zero-gap (name_prefix + create_before_destroy) → `grace2-solvers-spot-20260618...001`,
  ENABLED/VALID, **maxvCpus 96, c7i.{xlarge,2xlarge,4xlarge,12xlarge}** — so the per-case
  compute-class (small→xlarge) can actually schedule big instances. Queue `grace2-solvers`
  repointed. **SWMM Batch lane registered**: ECR `grace2-swmm` + job-def `grace2-swmm:1`
  (image NOT pushed; agent inert until `GRACE2_AWS_BATCH_JOB_DEF_SWMM` set). SFINCS job-def
  `grace2-sfincs:1` unchanged.
- **Persistence** DynamoDB. **LLM** Bedrock (Sonnet default; Haiku/Nova selectable; cachePoint
  Anthropic-only).

## 1. PROD-DEPLOY STATE — IMPORTANT
The LIVE agent box runs PRE-this-session engine code. Everything below is **committed + pushed
but NOT deployed to the box** — committed work outpaces the deployed binary:
- Phase-1 flood animation, the full **PySWMM urban engine** (local lane), coastal SFINCS
  forcing/obstacles + **P1 topobathy (fetch_topobathy)**, **FR-WC-16 urban draw UI** (web+agent),
  per-tool-card I/O expander. (Auto-class + SWMM-lane INFRA is applied; the agent CODE that uses
  them is not deployed.)
- **To make the urban + draw-UI demo drivable LIVE:** wake the agent box → add `pyswmm` +
  `swmm-api` + `httpx` to the PROD venv (topobathy uses py3dep/rasterio already present; confirm)
  → run targeted tests → SSM-deploy agent + deploy web (terra-draw). **OPEN DECISION for NATE:**
  do this prod-venv engine deploy now, or hold until the cht_sfincs quadtree+SnapWave worker
  lands so urban + coastal go live together. (Continuous-deploy norm permits deploying as green;
  held only pending NATE's "now vs batch with coastal" call.)

## 2. BUILT + COMMITTED THIS SESSION (origin/main, HEAD `ca35895`)
- **SRS FR-WC-16 amendment** (`06f1801`): vector draw + tag mode (v0.2) scoped IN; out-of-scope
  split (decorative annotation stays deferred). `make srs` regenerated.
- **Per-tool-card I/O expander** (`861075f`): chevron reveals raw args + function_response,
  surfaces hidden server/upstream errors, red-tints typed errors.
- **P7 SWMM Batch cloud lane** (`5f5261f`,`c44521f`): per-solver job-def routing in solver.py +
  multi-stage SWMM worker image + swmm.tf (applied). ECR-tag-parens gotcha (again).
- **Auto-class infra finished** (`dd109b3`): CE c7i ladder applied zero-gap (see §0).
- **FR-WC-16 urban walls/flap-gate draw UI** (`1704e04`) + hang-fix (`9b66a3f`): terra-draw
  barrier/AOI drawing, per-segment wall/flap-gate tagging → FeatureCollection → urban PySWMM
  barriers (wall=omit conduit, flap_gate=one-way orifice). Verdict pass.
- **Coastal SFINCS P1 — fetch_topobathy** (`1e1beb4`): NOAA NCEI CUDEM + 3DEP → NAVD88
  EPSG:32616 SFINCS-ready COG + model_flood_scenario coastal branch; honest land-only fallback;
  LIVE CUDEM endpoint confirmed.
- Housekeeping: stale qgis_process test fixed (`ca21c46`); sprint-16 cost log (`ca35895`).

## 3. NEXT — COASTAL SFINCS IS THE LEAD NORTH STAR
Mexico Beach (Hurricane Michael) coastal demo. Done: P0 forcing + **P1 topobathy**. Remaining:
- **cht_sfincs quadtree + SnapWave deck-builder WORKER CONTAINER** (the gate). cht_sfincs v1.0.0
  is GPL-3.0 + 1.2 GB → build the deck in a worker container (2nd stage of
  `services/workers/sfincs/Dockerfile`), SOLVE on `deltares/sfincs-cpu:v2.3.3`, agent NEVER
  imports cht_sfincs. Spike proven in `services/workers/sfincs_quadtree_spike/`. CAVEAT: **no
  docker on the orchestrator box** → the image build/push is an EC2/SSM step (like the SFINCS
  image). Author the worker code/wiring + fix the two spike caveats (SnapWave time-col
  epoch→tref; snapwave_use_herbers=1 for IG run-up) here; build/push on EC2.
- Then: **wave animation** (hm0/hm0ig + wavemaker propagating crests — the visible moving waves
  NATE wants) → **computed-vs-observed validation** (graph + rasters, "water-level m-NAVD88" key).
- INTERMEDIATE WIN now reachable (forcing + topobathy both done): a **regular-grid Mexico Beach
  SURGE** run end-to-end (de-risks before the quadtree spike) — needs a live SFINCS Batch solve.
- Urban PySWMM demo continues in parallel: engine done; remaining = the prod-venv deploy (§1) +
  P8 SWMM image build/push (EC2) to light up the cloud lane.

## 4. DECIDED + SCOPED (not built)
- **HEC stack** (`reports/hec_stack_recommendation.md`): HMS first → HEC-RAS. v0.1 = templated
  decks + safe-knob param-sweep. Queued behind the North Stars. HMS hydrographs feed SFINCS.
- **Sediment/dye minor North Star**: HEC-RAS sediment + WQ + tracer advection + MODFLOW-GWT thread.
- References: `reports/flood_pipeline_reference.md`, `reports/pyswmm_urban_build_plan.md`.

## 5. GOTCHAS / NORMS
- **Single-writer tree**: only ONE workflow/agent edits the working tree at a time (they share the
  filesystem). Run build workflows sequentially or use worktree isolation. Workflows do NOT
  self-commit; orchestrator re-runs tests + commits explicit paths.
- **Container hygiene** (NATE): INSPECT any image (size + `docker history` + .dockerignore) before
  ECR push; multi-stage + minimal base. `.dockerignore` at repo root covers worker builds.
- **AWS ECR tag VALUES reject parentheses** (and non-Latin-1) — recurring InvalidTagParameterException.
- **Batch CE** changes to instance_type force a REPLACEMENT — keep name_prefix + create_before_destroy
  (now in place) so the queue is never stranded.
- auto-stop never stops a busy box (fail-safe). Wake = POST the `/wake` API-GW URL.

## 6. STANDING DIRECTIVES
Greet NATE ALMANZA; no emojis; commit + push per big change (origin/main, double-r-squared/
GRACE-2); continuous deploy (no per-deploy gate, web+agent); data-source fallback norm; use
workflows to keep context lean; multiSelect decision pickers; **research real reference pipelines
before building each engine**; cost discipline (suspend for architecture-grounding); SRS —
specialists propose, only NATE lands (edit narrow `docs/srs/*` then `make srs`, never the monolith).
