# Local build cloud-fingerprint audit + fixes - 2026-07-08

Trigger: NATE spotted the solver-confirm gate card reading "fetch (1 vCPU)" in
the LOCAL QGIS plugin - cloud wording leaking into the local product. This
report is the exhaustive sweep of cloud artifacts that surface in the TRID3NT
LOCAL user experience, what was fixed, and what is recommended.

Repos:
- /home/nate/Documents/GRACE-2 - canonical source (agent + web + contracts); fixes land here
- /home/nate/Documents/trid3nt-local - local deployment; vendor/ is synced FROM GRACE-2
  (never hand-edited); qgis-plugin/ is native to trid3nt-local and editable there

Local deployment env (the ground truth the audit classified against,
trid3nt-local/.env.local): MODEL_PROVIDER=openai (Ollama), AWS_ENDPOINT_URL=
http://127.0.0.1:9000 (MinIO), GRACE2_SOLVER_BACKEND=local-docker,
GRACE2_DEV_PERSISTENCE_DIR (file persistence), AUTH_REQUIRED=false, no Cognito
pool env (start_agent.sh additionally unsets the pool vars), local web = vite
DEV server on :5173 with no VITE_* cloud env.

## The canonical is-local seam

The deployment-wide signal for "solves run on this machine" is the solver
dispatch backend: GRACE2_SOLVER_BACKEND=local-docker ->
`tools/solver.py::solver_backend() == SOLVER_BACKEND_LOCAL_DOCKER`. The local
build pins it; the cloud stack leaves it unset/aws-batch. No new seam was
invented: the new `server.py::_local_compute_lane()` helper is a thin
read-at-call-time wrapper over that existing seam, used ONLY to localize
user-visible confirm-card wording - it never changes dispatch. (Per-engine
seams also exist and were left alone: run_swmm.is_local_mode() - default True
even on cloud, pyswmm is in-process by nature; run_modflow.is_local_mode() -
GRACE2_MODFLOW_LOCAL.)

## PHASE 1 - AUDIT

### 1.1 User-visible strings

Class key: (a) user-visible in the LOCAL build; (b) user-visible but
cloud-only (env-gated off locally); (c) internal identifier / comment /
test-only (ignored for fixes).

#### Class (a) - user-visible in local (12 findings; 5 fixed, 7 recommended)

A1. FIXED - Fetch-resolution gate card: "fetch (1 vCPU)" (the hit NATE saw).
    - services/agent/src/grace2_agent/server.py, `_build_fetch_resolution_envelope`
      (was line 6762): hardcoded `compute_class="fetch", vcpus=1` into the
      GranularitySuggestion; the plugin gate card renders
      `f"{compute} ({vcpus} vCPU)"`.
    - Fix: local lane emits `compute_class="local"` (vcpus stays 1 - the
      contract pins vcpus > 0); cloud lane keeps "fetch"/1 byte-for-byte.

A2. FIXED - Flood run-settings confirm card prose: "(cloud solve, typically
    5-20 minutes)".
    - server.py, `_build_flood_run_settings_envelope` recommendation (was line
      6956). Not gated at all - local users were told their SFINCS run is a
      cloud solve.
    - Fix: local lane says "(local solve)."; cloud phrase byte-identical.

A3. FIXED - OpenQuake PSHA confirm card prose: "This dispatches the OpenQuake
    engine to AWS Batch (a cloud solve, typically several minutes)."
    - server.py, PSHA gate recommendation (was line 7063). The local build
      runs oq via GRACE2_OQ_BIN on the local machine.
    - Fix: local lane says "This runs the OpenQuake engine locally (typically
      several minutes)."; cloud phrase byte-identical.

A4. FIXED - Flood gate granularity descriptors: `compute_class="standard"`
    (the AWS Batch tier name) + `vcpus=int(auto.vcpus)` (the perf model's
    8-vCPU cloud anchor) shown on the local card.
    - server.py, `_build_flood_run_settings_envelope` granularity block (was
      lines 6901/6913).
    - Fix: local lane emits `compute_class="local"` + `vcpus=os.cpu_count()`
      (mirrors the existing SWMM builder local lane). The DISPATCH args
      (`tool_args["compute_class"]`) are intentionally NOT localized - only
      card wording changes; a test asserts this.

A5. FIXED - QGIS plugin gate-card rendering: `f"{compute} ({vcpus} vCPU)"`.
    - trid3nt-local/qgis-plugin/trid3nt/gate.py:299 (`summary_lines`). The
      plugin IS the local product, so the "local" compute lane now renders
      "local run (8 CPU)" / bare "local run" (vcpus <= 1); any other compute
      label (a remote-mode cloud agent) keeps the prior wording unchanged.

A6. RECOMMENDED - Live solve-progress ticks carry the AWS Batch tier's vCPU
    count even when the solve runs locally. `_vcpus =
    AWS_BATCH_COMPUTE_CLASS_SIZING.get(...)` is computed unconditionally and
    passed to `drive_live_solve_progress(vcpus=...)`; the web PipelineCard
    renders it as "... 8 vCPU ..." mid-solve.
    - services/agent/src/grace2_agent/workflows/model_urban_flood_swmm.py:648
      (passed on BOTH lanes at 712 and 962 - the in-process pyswmm local lane
      included)
    - workflows/model_wave_scenario.py:282-317
    - workflows/model_dambreak_geoclaw_scenario.py:587-622
    - workflows/model_fire_spread_scenario.py:383-420
    - (model_flood_scenario.py pulls vcpus off the autoscale provenance -
      same class.)
    Contrast: tools/run_modflow_tool.py:255 / run_modflow_multi_species_tool.py
    /run_river_seepage_tool.py already pass `vcpus=None` in local mode - the
    right precedent (PipelineCard omits null segments). Recommendation: one
    helper seam in tools/solver.py (e.g. `solve_progress_vcpus(compute_class)`
    returning None or os.cpu_count() when `solver_backend()=="local-docker"`)
    and swap the 5 call sites. Multi-engine change touching live solve
    telemetry across 5 workflows -> batched for a gated pass, not slipped into
    this wording fix.

A7. RECOMMENDED - Web ResolutionPickerCard literal labels "vCPUs:" (line 443)
    and the Spot row (450-454). web/src/components/ResolutionPickerCard.tsx.
    With the agent fixes above the local build never receives a spot_label and
    compute_class reads "local", but the "vCPUs:" label text itself is cloud
    wording. Cannot be keyed on compute_class=="local" (the CLOUD SWMM lane
    also emits "local" - pyswmm is in-process on the box - so cloud rendering
    would change). Needs a web-side deployment seam (e.g.
    VITE_GRACE2_LOCAL_BUILD) - structural, recommend.

A8. RECOMMENDED - Web PipelineCard solve readout segment `"${solve.vcpus}
    vCPU"` (web/src/components/PipelineCard.tsx:131). Same seam constraint as
    A7; fixing A6 (emit None locally) makes the segment disappear locally
    without touching web copy - do A6 first.

A9. RECOMMENDED - Privacy page hard-codes the cloud stack:
    web/src/pages/Privacy.tsx:43 ("via AWS Bedrock"), 100 ("TRID3NT runs on
    Amazon Web Services (AWS)"), 105 (DynamoDB), 109 (S3), 113 ("AWS Bedrock
    (Anthropic Claude)"), 119 ("Amazon EC2 / AWS Batch"), 173 (footer "Built
    on AWS Bedrock - Amazon EC2 - QGIS"). Reachable at /privacy in the local
    build and factually WRONG there (local = Ollama + MinIO + local docker).
    Recommend a local privacy variant or env-gated copy blocks.

A10. RECOMMENDED - Model selector labels are Bedrock model names ("Claude
     Sonnet 4.6", "Nova Pro", "Nova Lite", "Claude Haiku 4.5") -
     web/src/lib/modelRegistry.ts:45-71. The local build runs qwen3:8b via
     Ollama; the selector advertises cloud models it cannot serve. Recommend a
     local registry variant driven by the agent's /api capabilities (or a
     VITE flag).

A11. RECOMMENDED - FeaturePopup enrich-failure copy "Details unavailable --
     the agent must be awake to load building details."
     (web/src/components/FeaturePopup.tsx:501, 532). Renders locally on any
     enrich failure; "awake" is sleep/wake-infra vocabulary that means nothing
     locally. Shared copy - needs the same web seam as A7 or neutral rewording
     approved for cloud too.

A12. RECOMMENDED (minor) - Dispatch card label `"Dispatch {solver} solve
     ({compute_class})"` (pipeline_emitter.py:2585-2587) shows the Batch tier
     name ("standard"/"large") on local dispatch cards. Cosmetic; fold into
     the A6 pass.

#### Class (b) - user-visible but cloud-only (verified gated off locally)

B1. Wake overlay ("Wake up" / "Waking up" / aria labels) -
    web/src/components/WakeOverlay.tsx; gate `wakeConfigured()`
    (web/src/lib/wake.ts:71) = VITE_GRACE2_WAKE_URL or VITE_GRACE2_PUBLIC_BASE
    set. Local build sets neither -> never renders.
B2. Cognito sign-in UI ("Sign in / Sign up", session-expired copy) -
    AuthGuard.tsx / AuthGate.tsx; gate = all three VITE_COGNITO_* vars;
    unset locally -> anonymous-only, no sign-in chrome.
B3. Code-gate access-code entry ("Access code" / "Enter") - AuthGuard.tsx:
    298-352; gate = VITE_GRACE2_DEMO_TOKEN_URL / VITE_GRACE2_PUBLIC_BASE;
    unset locally.
B4. Settings sleep button - SettingsPopup.tsx; gate `wakeConfigured() &&
    isSignedIn`; off locally.
B5. SWMM gate Spot label `"Spot-eligible ({compute_class})"` - server.py:6589;
    gate = `is_local_mode()` False (GRACE2_SWMM_LOCAL=0), never the local
    build; local lane emits spot_label=None.
B6. Agent Cognito verify branch - auth_handshake.py:225-321; returns None
    immediately when GRACE2_COGNITO_USER_POOL_ID unset (start_agent.sh
    unsets it explicitly).
B7. QGIS plugin remote-mode token help mentions "the cloud broker" -
    qgis-plugin/trid3nt/dock.py:150. Intentional: it documents REMOTE mode
    (connecting the plugin to the cloud stack). Left as-is.

#### Class (c) - internal identifiers / comments / tests (aggregate, ignored)

- Dozens of vCPU/Batch/Spot mentions in comments and docstrings:
  tools/solver.py (sizing tables + submit-error strings that surface only as
  typed solver errors on the aws-batch lane), workflows/sfincs_builder.py
  (perf-model provenance), telemetry.py, tool_catalog_http.py,
  pipeline_emitter.py, server.py autostop commentary.
- s3:// URI plumbing (parsers, `s3_to_http`, bucket env names) in agent, web,
  and plugin - identifiers, not copy.
- contracts: GranularitySuggestion field docs mention Batch/Spot
  (packages/contracts/src/grace2_contracts/payload_warning.py) - schema docs,
  not rendered.
- web test files (auth.cognito.test.ts, WakeOverlay.test.tsx, etc).
- plugin case_export.py / trid3nt_client.py CloudFront comments.

### 1.2 Live cloud calls from local

L1. RECOMMENDED (config) - Cold tool-catalog fetch defaults to the CLOUD S3
    bucket: web/src/lib/public_base.ts:134 `coldCatalogUrl()` returns
    https://grace2-hazard-web-226996537797.s3.us-west-2.amazonaws.com/...
    when VITE_GRACE2_COLD_CATALOG_URL is unset, and it is the PRIMARY catalog
    source for the read-only tools popup. The local web therefore GETs a live
    AWS S3 object when the popup opens (silent failure offline). The env seam
    already exists - recommend the local deployment set
    VITE_GRACE2_COLD_CATALOG_URL (e.g. to http://127.0.0.1:8766/api/
    tool-catalog or a MinIO-published copy) in a local web env file.
L2. FIXED (partially) - GRACE-2's web/.env.production.local (CloudFront
    domain, Cognito pool/domain, wake/case-view execute-api endpoints) was
    being VENDORED into the local product at
    trid3nt-local/vendor/web/.env.production.local by scripts/
    sync_from_grace2.sh. Inert under the vite DEV server (production-mode env
    file) but primed to poison any future `vite build` of the local web with
    the full cloud endpoint set. Fix: sync_from_grace2.sh now excludes
    `.env.local` / `.env.*.local`. Residual: the already-vendored copy still
    sits in vendor/web/ (deletion was blocked by the session's permission
    classifier as a vendor/ mutation); one-time cleanup:
    `rm /home/nate/Documents/trid3nt-local/vendor/web/.env.production.local`.
L3. OK (inert) - auth.ts:678 hardcoded demo-token fallback URL
    (execute-api .../demo-token). Only reached by the code-gate flow, which
    the local build never enters (B3).
L4. OK (intentional public data) - terrain_3d.ts:217 AWS elevation-tiles-prod
    terrarium tiles: public open dataset fallback, same class as the
    _public_s3 open-data reads. The local build is online-capable for data by
    design.
L5. OK (the known-good pattern) - tools/_public_s3.py pins the REAL AWS
    endpoint (unsigned) for public open-data buckets precisely so the MinIO
    AWS_ENDPOINT_URL override does not swallow them. Correct and intentional.
L6. OK - every private-bucket boto3 S3 client (case_lifecycle.py:335,
    tool_catalog_http.py:1358, publish_layer.py:1465/1797, solver.py:973,
    cache.py:246/264, export_case_to_qgis.py:209, vector_tiles.py:485,
    data_fetch.py:1114) is a bare `boto3.client("s3", region_name=...)` with
    no endpoint pin -> honors AWS_ENDPOINT_URL -> MinIO locally.
L7. OK - Batch/ECS/EC2 clients (solver.py:916-954) only constructed on the
    aws-batch lane; local-docker never reaches them. Bedrock client only when
    MODEL_PROVIDER=bedrock. DynamoDB only when the persistence backend selects
    it (file persistence locally). Cognito JWKS fetch only when the pool env
    is set.
L8. RECOMMENDED - Secrets vault: with GRACE2_STORAGE_BACKEND unset (the local
    env), `handle_secret_add` (secrets_handler.py:424/494-503) routes secret
    WRITES to the GCP Secret Manager path (`_gcp_write_secret`) - a dead cloud
    path locally that will fail with a GCP ADC error if a user ever submits a
    credential-request card in the local build. Structural: recommend a
    file-vault branch keyed off the file-persistence selection (same
    "mirrors the persistence selection" pattern the AWS SSM branch used).

### 1.3 Dead/expired bits reachable locally

D1. Code-gate/demo-token: web UI gated off (B3); agent-side token verify inert
    with no Cognito pool (B6). No local exposure.
D2. Cognito auth branches: inert (env unset + start_agent.sh unsets). OK.
D3. Wake overlay/wake-api POSTs: gated off (B1); no wake POST can fire
    locally.
D4. Autostop/heartbeat/Fargate reaper: the agent code carries idle COUNTERS
    and commentary only - no cloud shutdown/heartbeat POST path ships in
    services/agent/src; the reaper/watchdog live out-of-process in cloud
    infra. Inert locally.
D5. The GCP secret-write path (L8) is the one genuinely dead-but-reachable
    cloud branch found.

## PHASE 2 - FIXES MADE (all env-gated; cloud wording byte-identical)

GRACE-2 (canonical; propagate to trid3nt-local via scripts/sync_from_grace2.sh
on the next sync - NOT run here, the live local agent on :8765/:8766 was left
untouched):

1. services/agent/src/grace2_agent/server.py
   - new `_local_compute_lane()` helper (wraps the existing
     `solver_backend()=="local-docker"` seam; wording-only, never dispatch).
   - `_build_fetch_resolution_envelope`: compute_class "local" vs "fetch" (A1).
   - `_build_flood_run_settings_envelope`: granularity compute_class="local" +
     vcpus=os.cpu_count() on the local lane (A4); recommendation "(local
     solve)." vs "(cloud solve, typically 5-20 minutes)." (A2). tool_args
     untouched.
   - PSHA gate: "runs the OpenQuake engine locally" vs the exact prior
     AWS Batch sentence (A3).
2. services/agent/tests/test_fetch_resolution_gate.py - new parametrized
   `test_fetch_gate_compute_label_deployment_aware` (local-docker -> "local";
   aws-batch AND unset -> "fetch"; vcpus/spot invariants both lanes).
3. services/agent/tests/test_solver_confirm_gate.py - new
   `test_flood_gate_recommendation_deployment_aware` (both lanes; also asserts
   dispatch args are never localized) and
   `test_psha_gate_recommendation_deployment_aware` (both lanes).

trid3nt-local (native files only; vendor/ untouched):

4. qgis-plugin/trid3nt/gate.py - `summary_lines` renders the "local" compute
   lane as "local run (N CPU)" / "local run"; any other compute label keeps
   the prior wording (A5).
5. qgis-plugin/tests/test_milestone2.py - new
   `test_summary_lines_compute_wording` (local wording, vcpus<=1 form, and the
   unchanged non-local wording).
6. scripts/sync_from_grace2.sh - excludes `.env.local` / `.env.*.local` from
   vendoring (L2).

NOT deployed: no sync run, no agent restart, no commits - working-tree changes
only, per the task constraints. The live local gate card will show the new
wording after the next sync_from_grace2.sh + agent restart (NATE's call).

## Test results

- GRACE-2 agent (only touched files):
  `cd services/agent && PYTHONPATH=src:../../packages/contracts/src
  .venv/bin/python -m pytest tests/test_fetch_resolution_gate.py
  tests/test_solver_confirm_gate.py -q` -> 36 passed (includes the 7 new
  parametrized cases; all pre-existing cases green - cloud lane unchanged).
- Plugin: `../venvs/agent/bin/python -m unittest tests.test_milestone2 -v`
  from qgis-plugin/ -> 30 tests, OK (includes the new wording test).

## Recommendations queue (accumulate, wait for go)

1. A6 (+A12): single `solve_progress_vcpus()` seam in tools/solver.py; swap
   the 5 workflow call sites so local solve-progress never claims an AWS tier
   vCPU count. Highest-value remaining honesty fix.
2. L1: set VITE_GRACE2_COLD_CATALOG_URL for the local web so the tools popup
   never GETs the cloud S3 catalog.
3. L2 residual: `rm trid3nt-local/vendor/web/.env.production.local` (blocked
   for this session by the permission classifier).
4. A7/A8/A11: introduce ONE web-side deployment flag (e.g.
   VITE_GRACE2_LOCAL_BUILD) and gate the "vCPUs:" label, the vCPU readout
   segment, and the "agent must be awake" copy on it.
5. A9/A10: local Privacy page variant + model-registry labels driven by the
   agent's actual provider.
6. L8: file-vault branch for secrets in the local build.
