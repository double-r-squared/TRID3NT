# SESSION HANDOFF — resume here

**Written 2026-06-17 at session wind-down (NATE asked to clear/compact).** This file
is the authoritative "pick up exactly here" note for the next session. Read it AFTER
the normal bootstrap (CLAUDE.md → AGENTS.md → orchestrator.md → PROJECT_STATE.md →
MEMORY.md). The auto-memory under `~/.claude/.../memory/` has the durable detail;
this file is the live in-flight state + the immediate next moves.

Greeting reminder: address the user as **NATE ALMANZA**; **no emojis** anywhere.

---

## 1. ONE IN-FLIGHT BACKGROUND TASK (read its result first)

**SWMM-vs-SFINCS engine verification workflow — run `wf_88798825-738`.** Launched
because NATE (correctly) pushed back on an over-broad "PySWMM can't do 2D" claim.
It adversarially verifies what EPA-SWMM5/PySWMM can/cannot do HEADLESS for the
animated-2D-urban-flood-around-buildings demo, vs SFINCS, and returns an honest
engine recommendation + the 1-2 questions that pin the choice. **The North Star
build is GATED on this verdict — do not start building the urban-2D demo until you
read this result and NATE weighs in.**
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
- **North Star build** — GATED on §1's verdict. If it confirms SFINCS: start with
  Phase 1 (animation): `postprocess_flood.py` currently np.squeeze's timemax to ONE
  max COG (~L305-330); change to emit N per-frame COGs (+ set `dtout` in setup_config)
  → feed the just-shipped Wave-1 sequential-group + scrubber + legend. Same-week
  standalone win, demoable on ANY existing SFINCS run. Full 5-phase plan in
  `project_pyswmm_north_star_demo`.

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
- **North Star reframe**: the urban-2D demo is a SFINCS generalization, NOT PySWMM
  (pending §1 final verdict). Coastal = second, Florence-not-Michael.

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
