# SESSION HANDOFF — resume here

**Written 2026-06-17 at session wind-down (NATE asked to clear/compact).** This file
is the authoritative "pick up exactly here" note for the next session. Read it AFTER
the normal bootstrap (CLAUDE.md → AGENTS.md → orchestrator.md → PROJECT_STATE.md →
MEMORY.md). The auto-memory under `~/.claude/.../memory/` has the durable detail;
this file is the live in-flight state + the immediate next moves.

Greeting reminder: address the user as **NATE ALMANZA**; **no emojis** anywhere.

---

## 1. SWMM vs SFINCS ENGINE VERDICT — RESOLVED 2026-06-17 (`wf_88798825-738`)

**The verification that was in-flight at the last wind-down COMPLETED. Verdict captured
here durably (raw output was ephemeral `/tmp/.../tasks/wlcbkoqyd.output`). Confidence
HIGH — confirmed against EPA Reference Manual Vol I/II primary sources, not just
secondary reports.**

NUMERICS (settled): the EPA-SWMM5 engine pyswmm/swmm-toolkit link = 0D lumped
nonlinear-reservoir hydrology + 1D St. Venant node-link hydraulics, with NO native 2D
mesh shallow-water overland solver. "SWMM 2D" exists ONLY as proprietary GUI quasi-2D
(PCSWMM 2D — a storage-node-per-DEM-cell grid; its DEM->mesh auto-builder is
closed-source, NO open/headless equivalent) or as external coupling to a real 2D engine
(Iber-SWMM). So the lecture's "2D depth around buildings" was almost certainly PCSWMM 2D
— not replicable headless. FLAP GATES ARE NATIVE to SWMM (one-way-flow attribute on
conduits/orifices/weirs/outlets) — the cleanest match for the green barrier segments.

NATE'S DECISION 2026-06-17 (AUTHORITATIVE — overrides any "use SFINCS for the urban
demo" reading of the numerics above; he was emphatic, do NOT steer the urban demo back
to SFINCS): the URBAN flood demo is built with **PySWMM, NOT SFINCS.** The numerics fact
stands (no native 2D mesh), so the honest PySWMM path is the QUASI-2D node-link mesh —
exactly what PCSWMM (NATE's best guess for the lecture tool) does, but hand-built because
there is no open auto-mesher. Concretely:
- 100-year DESIGN STORM (Atlas-14 hyetograph -> SWMM rain series).
- BUILDINGS as OBSTRUCTIONS: OSM footprints (job-0331) drop/elevate the mesh cells they
  cover so water routes AROUND them.
- WALLS + FLAP GATES, USER-DEFINED via a draw UI (NATE: "most wiring/data is the agent;
  the walls and flap gates are defined by the user"): red wall segment = blocked/omitted
  link; green flap-gate segment = NATIVE SWMM flap-gate attribute (one-way pass) — clean
  literal match. See [[project-river-to-shapefile-tool]] draw-lib glue + the per-segment
  metadata notes in `project_pyswmm_north_star_demo`.
- ENGINE: PySWMM headless on AWS Batch (new Class-B engine; first of the
  [[project-tool-integration-paradigm]] explicitly-defined frameworks). The DEM ->
  quasi-2D storage-node + overland-conduit mesh is built by COMPOSING OPEN TOOLS (NATE
  2026-06-17, correcting an earlier "no open auto-mesher" overstatement — this is exactly
  what GRACE-2 links tools for): QGIS Processing (create grid, sample DEM to cells,
  centroids, polygon adjacency) via the EXISTING `qgis_process` Class-A seam + a GIS->INP
  converter (open Generate_SWMM_inp QGIS plugin / GISSWMM / swmm-api) + thin glue. The
  STEPS are all standard open GIS ops; only PCSWMM's one-click auto-mesher BUNDLE has no
  off-the-shelf open clone -> this is tool-linking, NOT a from-scratch numerical mesher.
  Ties directly into job-0308 (QGIS worker). Per-node depth/step -> rasterize ->
  per-frame COG -> Wave-1 animation.
- MESHER CONFIRMED (research agent, 2026-06-17): ~70% link / 20% glue / 10% custom.
  pysheds `tools/swmm.py` `SwmmIngester` (GPLv3+, headless) ALREADY builds DEM-cell nodes
  + inverts + adjacency conduits + storage + `.inp` writer = the adopt/adapt hit; alts =
  GisToSWMM5 (MIT), Generate_SWMM_inp (GPL-2.0 QGIS plugin via qgis_process), swmm-api
  (MIT). SWMM 5.2 has a native quasi-2D wide-channel wall-ignore flag; flap gates native
  (CONDUITS FlapGate). Seam = `qgis_process` (passthroughs.py:194) + discovery triple.
  SRS already names it: docs/srs/02-system-overview.md:144 ("EPA SWMM via PySWMM — v0.2").
- SFINCS = the COASTAL demo ONLY (Florence/Michael). It is NOT the urban demo.
- Phase-1 ANIMATION (per-frame COG -> Wave-1 sequential group, from `postprocess_flood`)
  serves the COASTAL demo directly AND yields the reusable per-frame-COG sequential
  PUBLISHING path the urban PySWMM demo also needs -> still built first (workflow running,
  §3). A PySWMM urban-engine SCOPING workflow is also running (concrete phased build plan
  for the quasi-2D mesh path).

## 1B. EARLIER FRAMING NOTES (superseded by the §1 verdict above; kept for context)

**IMPORTANT FRAMING (NATE corrected this at wind-down — do NOT inherit the false
"versus"):** NATE wants BOTH North Star demos (urban AND coastal) AND BOTH engines.
PySWMM is a wanted, real, headless engine (drainage networks, pipes, surcharge,
flap-gate control, real-time stepping) — it is NOT being replaced by SFINCS. SFINCS
owns coastal/broad-2D. The ONLY narrow open question is: for the URBAN demo, HOW to
render animated 2D depth flowing AROUND buildings — via SWMM's own node-link surface
representation, SWMM coupled to a thin 2D surface step, or a hybrid. The verification
below answers "how to build the urban demo honestly, SWMM-centric," NOT "which engine
wins." Earlier session notes that say "urban-2D = SFINCS, not PySWMM" OVER-corrected —
treat PySWMM as the urban-demo engine of record, with the 2D-sheet-flow mechanism the
open sub-question.

**Engine/toolchain verification workflow — run `wf_88798825-738`.** Launched because
NATE (correctly) pushed back on an over-broad "PySWMM can't do 2D" claim (the real
question is the 2D-mesh SOLVER, not headless-ness — pyswmm IS headless). It verifies
what EPA-SWMM5/PySWMM can/cannot do for the animated-2D-around-buildings demo, what
tools produce such a demo, and recommends the honest toolchain (incl. PySWMM's role +
a hybrid option) + the 1-2 questions that pin the 2D mechanism. **The urban North Star
BUILD is gated on reading this + NATE's answer to §2 — but the engine (PySWMM) and
both demos are NOT in question.**
- Result file: `/tmp/claude-1000/-home-nate-Documents-GRACE-2/<sessionId>/tasks/wlcbkoqyd.output`
  (the task-id is `wlcbkoqyd`; if gone, re-run the script at
  `.../workflows/scripts/swmm-2d-engine-verify-wf_88798825-738.js`).
- The correct framing (do NOT repeat the earlier mistake): pyswmm IS fully headless
  (links the SWMM5 C engine; no GUI). The ONLY real question is whether SWMM5 has a
  native 2D MESH overland solver (engine question), NOT headless-ness. SWMM5 = 1D
  St. Venant drainage network + lumped subcatchment runoff; flap gates ARE a native
  SWMM feature (maps to NATE's red/green barrier). See `project_pyswmm_north_star_demo`.

## 2. TWO OPEN QUESTIONS FOR NATE (ask when he returns)
- The lecture demo he is replicating — **what tool produced it?** (PCSWMM 2D /
  InfoSWMM 2D / a research SWMM setup / something else). This single answer largely
  settles the engine choice.
- Coastal North Star AOI: **Hurricane Florence 2018** (replicate a real published
  SnapWave figure, lower risk) **vs keep Mexico-Beach/Michael** (a GRACE-2-authored
  deck, not a reproduction).

## 3. AUTHORIZED + QUEUED (NATE said "all three" — go on all)
- **Wave 3 — auto-stop/wake (NEXT; I was about to author it).** Design is settled
  (see `project_model_selector_and_agent_tier` + below). Pieces: (a) agent — add a
  live `active_connections` count to `/api/health` in `tool_catalog_http.py` (today
  it returns only `{"ok":true}` at ~line 1090); (b) infra — a NEW self-contained
  tofu root `infra/aws-autostop/`: EventBridge schedule → idle-check Lambda (polls
  `/api/health`; `StopInstances` only after N consecutive zero-connection checks AND
  only if running) + API-Gateway HTTP endpoint → `StartInstances` wake Lambda + IAM;
  (c) web — wake-on-load / reconnect-retry that calls the wake endpoint when the WS
  is down (SKIP the fancy shimmer UI per the AgentCore eval — a ~1-2 min cold start
  is fine for dev). Bundle the agent `/api/health` change's deploy with the pending
  `mongo_query` removal (see §4).
- **Wave 4 — QGIS worker (job-0308).** The big infra lift; 7-phase plan in
  `project_qgis_worker_job0308_design`. ECR mirror + IAM + S3 .qgs bucket + EC2 QGIS
  container + CloudFront /ogc/wms + env flips + a publish_layer vector branch. Touches
  the LIVE box + CloudFront — do it methodically, plan/apply/verify each step.
- **North Star builds — BUILD BOTH (not versus):**
  - **Urban demo** (NATE's "PySWMM" showcase): engine of record = **PySWMM/SWMM5**
    (drainage + flap-gate barrier are SWMM's home turf); the 2D-sheet-flow-around-
    buildings MECHANISM is the open sub-question §1 answers. PySWMM is a new Class-B
    engine to stand up (headless on Batch; see `project_tool_integration_paradigm`).
  - **Coastal demo**: **SFINCS + SnapWave** (quadtree); deck-authoring is the risk
    (HydroMT can't author quadtree+SnapWave — spike it). Florence-vs-Michael = §2.
  - **START with Phase 1 (animation) — it is ENGINE-AGNOSTIC and serves BOTH:**
    `postprocess_flood.py` currently np.squeeze's timemax to ONE max COG (~L305-330);
    change to emit N per-frame COGs (+ set `dtout`) → feed the just-shipped Wave-1
    sequential-group + scrubber + legend. Same-week standalone win, demoable on ANY
    existing time-series flood output (SFINCS today, SWMM later). 5-phase urban plan +
    the corrected PySWMM framing in `project_pyswmm_north_star_demo`.

## 4. COMMITTED-NOT-DEPLOYED
- `mongo_query` removal (commit `8e3f8e8`, pushed): registry 94→93. The agent is NOT
  yet redeployed with it (prod still has the dead stub — harmless). DEPLOY it bundled
  with the Wave-3 `/api/health` agent change (one restart). Deploy = tar the changed
  files → `s3://grace2-agent-bundle-226996537797/` → SSM AWS-RunShellScript: cp down,
  rm `__pycache__`, untar into `/opt/grace2/services/agent/src/grace2_agent/`,
  `systemctl restart grace2-agent`, grep-verify. (SSM gotcha: no parens in echo lines;
  sleep>=5 before checking is-active, it can race startup.)

## 5. DEPLOYED + LIVE THIS SESSION (NATE to live-verify)
Agent batch + web shipped across commits `80ac536`→`8e3f8e8`, all on prod:
- Duplicate flood layer fixed (one styled "Peak flood depth"); stuck hillshade card
  completes; closing narration lands; WS keepalive stops the ~10s cycling; terminal
  failures persist across reconnect.
- Chat-chrome rework (all 8): model button in header (icon-only), connection dot left
  of "GRACE-2 vX", send circle/square + model color, mode icon, popover overlay,
  chat-to-bottom, breadcrumb cutoff.
- Models: Sonnet 4.6 / Haiku 4.5 / Nova Pro / Nova Lite selectable + working
  (cachePoint is now Anthropic-ONLY — Nova/DeepSeek reject it). DeepSeek-R1 OUT
  (rejects toolConfig). Version hash restored + auto-injected (vite.config).
- Wave 1 (viz): sequential temporal-layer grouping + bottom-center scrubber +
  draggable/snapping gradient legend keys. Wave 2 (telemetry): by-model comparison
  table in the routing/accuracy panel.
- Gemini→Bedrock + MongoDB→DynamoDB on the public Landing/Privacy pages.
- Live-verify nudges: WS no longer cycles; scrubber + a legend key both want
  bottom-center (may overlap — nudge if so); sequential grouping keys on layer-NAME
  tokens (HRRR F+NNh) + same run-dir.

## 6. INFRA STATE (live AWS, account 226996537797 / us-west-2)
- **Agent box DOWNSIZED**: `i-0251879a278df797f` is now **t3.large** (was c7i.2xlarge;
  ~$257→~$60/mo). EIP `54.185.114.233` (stable), CloudFront `E2L74AS56MVZ87` origin
  intact. grace2-agent + titiler both boot-enabled + active. ~6.4GB free.
- **SFINCS on AWS Batch** = PROVEN working (Spot, scale-to-zero, min=desired=0 idle).
  Two IAM gaps fixed earlier this session (task-role cache-bucket read; agent-role
  DescribeJobs/ListJobs must be Resource="*"). `infra/aws-batch/` tofu.
- Persistence = DynamoDB (no Mongo — Atlas torn down by NATE; verified no live dep).
- Temp `grace2-agent-ecr-push` IAM grant still present → remove after the QGIS image
  work is done (post-Wave-4) — see `project_sfincs_autoscale_and_batch`.

## 7. KEY DECISIONS THIS SESSION (don't relitigate)
- **AgentCore eval = HYBRID** (`project_agentcore_evaluation`): downsize+auto-stop
  bridge (done downsize) + adopt **Code Interpreter** for the data-analysis sandbox
  (replaces the planned Lambda sandbox) + Runtime = gated SPIKE only (3 hard gates).
- **Scale-to-zero via auto-stop/wake** chosen over a tiny always-warm box (NATE:
  latency is relative to minute-long sims). EC2 cold start is ~1-2 min (NOT the 2-5s
  Lambda figure). Skip the wake shimmer UI for dev.
- **North Star = BOTH demos + BOTH engines** (NATE corrected the false "versus" at
  wind-down). Urban demo engine = PySWMM (the 2D-sheet-flow mechanism is the only
  open sub-question, §1); coastal = SFINCS+SnapWave. Build both; Phase-1 animation is
  engine-agnostic and goes first. (An earlier subagent's "urban = SFINCS not PySWMM"
  was an over-correction — superseded.)

## 8. OUTSTANDING HYGIENE
- **cost_tracking.json**: this session ran MANY workflows/agents (diagnosis, 2 bug-fix
  waves, AgentCore eval ~394k, NorthStar design ~264k, SWMM verify, Wave-1/Wave-2
  agents, mongo_query agent ~78k, etc.). Per `feedback_log_all_subagent_token_costs`,
  log a session summary entry. Token totals are in each workflow's completion
  notification (subagent_tokens field).
- 1 pre-existing agent test failure (`test_qgis_process_pass_through_invokes_bound_submitter`)
  is stale mock debt (asserts an old NotImplementedError the real job-0308 impl no
  longer raises) — fix opportunistically; NOT a regression.
- Internal Gemini naming still in agent logs/symbols (`model=gemini-2.5-pro` startup
  line, `_dispatch_gemini_and_persist`, etc.) — cosmetic, not user-facing.

## 9. WORKFLOW ORCHESTRATION NOTE
NATE opted INTO workflows this session ("use workflows so you keep a clean context
window"). The no-subagent rule is rescinded; deploys are continuous/no-gate. When
parallelizing agent-codebase work, give each agent DISJOINT files and have it run
ONLY targeted tests (concurrent full-suite runs collide); the orchestrator runs the
full suite once before deploy. Adversarial-verify high-importance landings (this
session's stash-baseline diff caught 2 real server.py regressions).
