# Audit: run_solver + wait_for_completion atomic tools (M5 agent-side dispatch + progress emission)

**Job ID:** job-0041-agent-20260606, **Sprint:** sprint-07, **Auditor:** Development Orchestrator, **Status:** assigned

## Task Assignment

**Specialist:** agent

**Prerequisites:**
- **job-0040 (APPROVED):** SFINCS Cloud Workflows orchestrator `grace-2-sfincs-orchestrator` + Cloud Run Job `grace-2-sfincs-solver` live. **Read job-0040's report.md end-to-end** to absorb the Workflows execution API contract + the SFINCS manifest schema (3 fields: input_uri, output_uri, options) + the `completion.json` shape the worker writes.
- **job-0035 (APPROVED):** `PipelineEmitter` class with `update_progress(step_id, progress_percent)` method — your progress emission target.
- **job-0032 (APPROVED):** registry decorator + cache shim. `run_solver` + `wait_for_completion` are uncacheable (FR-DC-6); declare `cacheable=False`, `ttl_class="live-no-cache"`, `source_class="solver_dispatch"`.

**SRS references** (narrow files only):
- `docs/srs/03-functional-requirements.md` — FR-TA-2 (`run_solver` + `wait_for_completion` definitions), FR-CE-1/2/3 (Cloud Workflows orchestration contract), FR-CE-6 (precondition pattern; relevant for job-0042 but informs your error handling), FR-CE-7 (cancellation conformance — 30s budget)
- `docs/srs/A-websocket-protocol.md` — `pipeline-state` envelope shape, cancel chain
- `docs/srs/D-mongodb-collection-schemas.md` — D.6 `PipelineStepSummary` extended fields from job-0030
- DO NOT load `docs/SRS_v0.3.md` monolith.

### Environment
Cloud Workflows execution submission via `google-cloud-workflows` Python client. The Workflow `grace-2-sfincs-orchestrator` accepts a payload `{manifest_uri: "gs://..."}` and returns an execution handle. The worker writes `completion.json` to `gs://grace-2-hazard-prod-runs/<run_id>/completion.json` with `{status, error?, outputs: [...]}`.

### Scope

1. **`services/agent/src/grace2_agent/tools/solver.py`** (NEW) — implement two atomic tools:

   - `run_solver(solver: str, model_setup_uri: str, compute_class: str = "medium") → ExecutionHandle` — registered with `@register_tool(AtomicToolMetadata(name="run_solver", ttl_class="live-no-cache", source_class="solver_dispatch", cacheable=False))` (FR-DC-6 uncacheable enumeration). Submits a Cloud Workflows execution against the deployed solver-specific orchestrator (currently only `solver="sfincs"` is supported; raise `SolverNotRegisteredError` for others). Returns `ExecutionHandle{workflow_execution_id, started_at, solver, compute_class}`.

   - `wait_for_completion(handle: ExecutionHandle, poll_interval_s: int = 10, timeout_s: int = 1800) → RunResult` — registered same TTL class. Polls the Workflows execution every `poll_interval_s` (default 10s — matches NFR-P-4 ≤15-min budget granularity); emits a `pipeline-state` progress update on each poll via `PipelineEmitter.update_progress(step_id, progress_percent)`. Progress derivation: linear in `(now - started_at) / NFR-P-4_target_seconds` while solver is running; clamp to ≤95% until the Workflow returns success (then jumps to 100%). On Workflow success, reads `completion.json` from the runs bucket + returns `RunResult{status: "complete", outputs: [LayerURI...]}`. On Workflow failure, returns `RunResult{status: "failed", error_code, error_message}` (open-set error code registry per job-0035 ErrorCodeRegistry).

2. **Cancellation integration (Invariant 8):** `wait_for_completion` must propagate cancellation. When the agent's `GraceWs.cancel` envelope arrives, the pollloop checks `cancel_token.is_set()` and calls `workflows.executions.cancel(execution_id)` if so — meeting the FR-AS-6 / NFR-R-3 30s cancellation budget. The Cloud Run Job execution receives a SIGTERM via the standard Workflows-cancellation propagation.

3. **`PipelineEmitter.update_progress` integration:** the existing emitter is wired in `server.py`; `wait_for_completion` must locate the active emitter (likely via the same DI seam pattern job-0033 used for `set_mcp_client`) and emit progress at each poll. Surface as an Open Question if the seam isn't obvious; do NOT introduce a new dependency-injection mechanism.

4. **Smoke run live evidence:** submit a trivial SFINCS execution (use the same trivial manifest job-0040 used for its smoke run) and capture:
   - WS frame transcript showing the `pipeline-state` envelope progress updates (≥3 updates from pending → running → complete or failed)
   - The returned `RunResult`
   - The cancel chain: submit, wait 5s, send a `cancel` envelope, verify the Cloud Run Job execution status flips to `cancelled` within 30s

5. **Tests** in `services/agent/tests/test_solver.py` (NEW): at least 5 unit tests (registration; happy-path mocked Workflows execution; progress emission tied to mocked poll loop; cancel propagation; Workflow-failure → RunResult error path). At least 1 integration test using a mocked Cloud Workflows client.

### File ownership (exclusive)

- `services/agent/src/grace2_agent/tools/solver.py` (NEW)
- `services/agent/src/grace2_agent/main.py` — ONLY the eager `tools.solver` import for FR-CE-8 fail-fast registration (mirror the `tools.data_fetch` import job-0033 added); do not refactor unrelated startup code
- `services/agent/pyproject.toml` — add `google-cloud-workflows` runtime dep
- `services/agent/tests/test_solver.py` (NEW)
- `reports/inflight/job-0041-agent-20260606/` — kickoff frozen

### FROZEN — no edits in this job

- All other `services/agent/src/grace2_agent/tools/*.py` (data_fetch.py from concurrent job-0039, plus all M4 files)
- `services/agent/src/grace2_agent/{server,mcp,pipeline_emitter}.py` (consume them via imports; do not modify; if `pipeline_emitter` integration requires a seam adjustment, surface as Open Question — don't shadow-edit)
- `packages/contracts/**`, `infra/**`, `web/**`, `docs/srs/**`, `docs/SRS_v0.3.md`, `styles/**`, `services/workers/**`, `reports/complete/**`
- Stage B concurrent job-0039 (data_fetch.py)

### Cross-cutting principles in force

- **Invariant 1 (Determinism boundary):** preserved. Progress estimation is wall-clock linear, not LLM-derived.
- **Invariant 2 (Deterministic workflows):** preserved. `run_solver` is a thin Workflows submission; no LLM in the dispatch.
- **Invariant 8 (Cancellation is first-class):** the headline. Cancel chain end-to-end: WS `cancel` → poll loop notices → `workflows.executions.cancel` → SIGTERM to Cloud Run Job → `PipelineEmitter.mark_cancelled` → re-emit `pipeline-state`. ≤30s per FR-AS-6 / NFR-R-3.
- **FR-DC-6 (uncacheable enumeration):** both tools declare `cacheable=False`. The DI seam pattern from job-0034 (`set_worker_submitter`) is reusable here if you need a similar shape for the Cloud Workflows client; OR use ADC + direct client construction — your call, surface as Decision Made.
- **A.7 replace-not-reconcile:** every `pipeline-state` progress emit is a full snapshot (the emitter handles this; you just call `update_progress`).
- **Diagnose before fix:** if Workflows submission fails (IAM, manifest schema), capture the exception before changing the tool.

### Acceptance criteria (reviewer re-runs)

- [ ] `run_solver` + `wait_for_completion` registered with `cacheable=False`, `ttl_class="live-no-cache"`; `TOOL_REGISTRY` shows ≥10 tools on `--startup-only` (M4's 8 + at least these 2; possibly +3 from concurrent job-0039 if it lands first).
- [ ] Live smoke run captures: WS transcript with ≥3 progress emissions; trivial Workflow execution completing; `RunResult` returned.
- [ ] Live cancel test: submit + wait 5s + cancel + verify within 30s the Cloud Run Job execution status is `cancelled`.
- [ ] At least 5 unit tests + 1 integration test green; full agent suite preserved.
- [ ] No edits to FROZEN paths (especially not the M3 emitter or M4 cache shim).
- [ ] `services/agent/pyproject.toml` includes `google-cloud-workflows`.

Surface contestable choices as Open Questions with TENTATIVE tags — at minimum: poll interval default (10s vs 5s); progress-estimation curve (linear vs NFR-P-4-budget-based); how to access the active `PipelineEmitter` from the solver tool; whether `wait_for_completion` should block synchronously or yield (M4 atomic tools block; revisit if SFINCS at NFR-P-4 budget makes this a UX problem); error code registry expansion for solver-specific errors (SFINCS_TIMEOUT, MODEL_DECK_INVALID, etc.).
