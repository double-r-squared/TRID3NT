# SESSION HANDOFF — resume here

**Refreshed 2026-06-18** after a large engine + UX session. Read AFTER the normal bootstrap
(CLAUDE.md → AGENTS.md → orchestrator.md → PROJECT_STATE.md → MEMORY.md). Greeting: address
the user as **NATE ALMANZA**; **no emojis**. Auto-memory under `~/.claude/.../memory/` holds
the durable detail; this is the live "pick up here" note. HEAD = `6aab859` (origin/main); **prod agent redeployed to it + verified** (see §1).

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

## 1. PROD-DEPLOY STATE — CURRENT (redeployed 2026-06-18)
**Prod AGENT redeployed to HEAD `6aab859` and verified.** Live + healthy: `/api/health` ok,
**92 tools** (incl. fetch_topobathy, request_spatial_input, run_swmm_urban_flood), **Bedrock**
(sonnet-4-6) + **DynamoDB**, **auto-stop re-armed**. So LIVE now: PySWMM urban engine, the
**FR-WC-16 terra-draw draw UI + per-tool-card I/O** (web shipped in the earlier deploy), coastal
**P1 topobathy**, and the **GCP-decommissioned AWS-default agent** — the 7 dropped deps
(google-adk/secret-manager/workflows/logging, pymongo, gcsfs, firebase-admin) are uninstalled
from the box venv; carve-outs **google-cloud-run/storage/genai** kept. Backup on box:
`/opt/grace2/_backup-20260618-210607`.
- Web bundle (`index-C-8h3jFW.js`) live on CloudFront; NO web commits this session after the
  earlier deploy → no web redeploy needed.
- The coastal **quadtree+SnapWave** path (combined worker + agent wiring) is CODE-COMPLETE but
  **INERT** until the EC2 image build + `GRACE2_AWS_BATCH_JOB_DEF_SFINCS_QUADTREE` env (see §3).
  Same for the SWMM Batch cloud lane (`GRACE2_AWS_BATCH_JOB_DEF_SWMM`, image not pushed).

## 2. BUILT + COMMITTED THIS SESSION (origin/main, HEAD `6aab859`)
**Post-`ca35895` (the coastal + decommission batch):** cht_sfincs deck-builder worker (`2ee8298`)
→ **full GCP decommission** (`4565d02`: AWS-default flips, delete dead GCP code/IaC, rewrite
docs) → **combined coastal quadtree worker** (`13370b7`: build+solve in one job, auto-refine +
building-obstacles + SnapWave; reverted the GPL 2-job split — license now irrelevant per NATE) →
auto-refine numerics test (`10a44f5`) → **decommission finish** (`6aab859`: collapse gs:// seam
to S3-only, fresh-venv GCP-free except the QGIS carve-out). All re-verified + pushed; prod
redeployed to `6aab859`. (cht/combined images NOT built — EC2 step, §3.)
---
*(earlier this session, through `ca35895`):*
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
Mexico Beach (Hurricane Michael) coastal demo. **CODE-COMPLETE** through deck-build+solve: P0
forcing + P1 topobathy + the **combined quadtree worker** (`services/workers/sfincs_deckbuilder/`
— one Batch job: auto-refine polygons + building-obstacles + SnapWave + subprocess-solve, base
`deltares/sfincs-cpu:sfincs-v2.3.3@sha256:46b5fc9e…` which ALREADY contains the SnapWave-compiled
binary) + agent wiring (`sfincs-quadtree` job-def). Remaining = activation + polish:
- **THE GATE — build/push the combined image on EC2** (no docker on the orch box): `docker build`
  services/workers/sfincs_deckbuilder/Dockerfile → ECR → register Batch job-def → set
  `GRACE2_AWS_BATCH_JOB_DEF_SFINCS_QUADTREE` + `GRACE2_AWS_BATCH_QUEUE`. WATCH: the one build risk
  is a GDAL/PROJ clash (Ubuntu-22.04 sfincs-cpu base vs the manylinux rasterio/geopandas wheels in
  cht's closure — Dockerfile forces `--prefer-binary` + a build-smoke import to trip it at build).
  Then a ~2-min smoke run of `services/workers/sfincs_quadtree_spike/deck_cht/` confirms the v2.3.3
  binary runs `snapwave=1` on a quadtree deck end-to-end.
- INTERMEDIATE WIN (reachable NOW — forcing + topobathy live in prod): a **regular-grid Mexico
  Beach SURGE** run (de-risks before quadtree) — needs a live SFINCS Batch solve. NATE's surge
  prompt is drivable.
- Then **wave animation** (hm0/hm0ig + propagating crests — the visible moving waves) →
  **computed-vs-observed validation** ("water-level m-NAVD88" key).
- Urban: engine LIVE in prod; remaining = P8 SWMM image build/push (EC2) to light the cloud lane
  (`GRACE2_AWS_BATCH_JOB_DEF_SWMM`); the in-process local lane works now.

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
