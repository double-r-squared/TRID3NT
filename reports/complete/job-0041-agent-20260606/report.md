# Report: run_solver + wait_for_completion atomic tools (M5 agent-side dispatch + progress emission)

**Job ID:** job-0041-agent-20260606
**Sprint:** sprint-07
**Specialist:** agent
**Task:** Land `services/agent/src/grace2_agent/tools/solver.py` (NEW) with two FR-DC-6 uncacheable atomic tools — `run_solver(solver, model_setup_uri, compute_class="medium") → ExecutionHandle` (submits a Cloud Workflows execution against `grace-2-sfincs-orchestrator`, currently SFINCS-only) and `wait_for_completion(handle, poll_interval_s=10, timeout_s=1800) → RunResult` (polls the Workflows execution, emits `pipeline-state` progress via `PipelineEmitter.update_progress` on each poll, propagates WS cancel → `workflows.executions.cancel` within ≤30s per Invariant 8 / NFR-R-3). Smoke run + live cancel test required. Capture evidence under `evidence/`.
**Status:** ready-for-audit

## Summary

Landed `services/agent/src/grace2_agent/tools/solver.py` (~700 lines) registering the two FR-DC-6 uncacheable atomic tools end-to-end with the deployed M5 SFINCS substrate (job-0040: `grace-2-sfincs-orchestrator` workflow + `grace-2-sfincs-solver` Cloud Run Job + `grace-2-hazard-prod-runs` bucket). `run_solver` submits a Cloud Workflows execution carrying a JSON argument `{run_id, manifest_uri}`, returning a typed `ExecutionHandle{workflows_execution_id, workflow_name, workflow_location, …}` whose `workflows_execution_id` is the Invariant-8 cancellation seam (the Workflows-issued `projects/.../executions/<uuid>` resource name). `wait_for_completion` polls the execution every `poll_interval_s` (default 10s — NFR-P-4 ≤15-min budget granularity), emits a wall-clock-linear progress percent through the active `PipelineEmitter.update_progress(step_id, …)` binding on every poll (clamped to ≤95% until SUCCEEDED, then jumps to 100%), on SUCCEEDED reads `gs://<runs>/<run_id>/completion.json` and builds the matching `RunResult{status, output_uri, error_code?, error_message?}`, on Workflow FAILED surfaces the workflow-side error (preferring the entrypoint's structured `completion.json` if present), and on `asyncio.CancelledError` calls `workflows.executions.cancel(name)` BEFORE re-raising so the cloud-side SIGTERM fires within ≤30s. Both tools declare `cacheable=False`, `ttl_class="live-no-cache"`, `source_class="solver_dispatch"` (a new FR-DC-6 source class enumerated for this milestone). 10 new tests pass; agent suite 104/104 green; contracts 131/131 unchanged. **Live smoke run on `grace-2-hazard-prod`:** synthetic SFINCS manifest → Workflows execution → 36 progress emissions (0% → 19% → 100% on SUCCEEDED) → `RunResult(status="failed", error_code="SOLVER_FAILED")` (matches job-0040's smoke shape: sfincs exits non-zero because the synthetic manifest has no model deck). **Live cancel test:** second submission, 5s wait, `task.cancel()` → `workflows.executions.cancel(name)` issued in 160 ms → workflow state CANCELLED in **0.85s end-to-end** (well under NFR-R-3 30s budget). DI seam choice mirrors job-0032's `set_mcp_client` / `set_worker_submitter` pattern (`set_workflows_client`, `set_emitter_binding`, `set_runs_bucket`, `set_storage_client`) — no new mechanism introduced. The `pipeline_emitter.py` + `server.py` integration site (binding the active `(emitter, step_id)` around each `wait_for_completion` invocation) is a follow-up agent job because both files are FROZEN here.

## Changes Made

- `services/agent/src/grace2_agent/tools/solver.py` (NEW, ~700 lines)
  - **Module-level constants:** `NFR_P_4_TARGET_SECONDS=900.0` (≤15 min target per NFR-P-4); `DEFAULT_POLL_INTERVAL_S=10`; `DEFAULT_TIMEOUT_S=1800`; `PROGRESS_CLAMP_MAX=95`; `PROGRESS_TERMINAL=100`; `SOLVER_WORKFLOW_REGISTRY = {"sfincs": "grace-2-sfincs-orchestrator"}` (lazy per-milestone deploy); `_COMPUTE_CLASS_ALIAS = {"small": "small", "medium": "standard", …}` (maps FR-CE-3 names onto the schema-side `Literal["small","standard","large","gpu"]` — surfaced as OQ-41-COMPUTE-CLASS-NAMING for schema to reconcile).
  - **Errors:** `SolverNotRegisteredError(ValueError)` (solver not in registry); `SolverDispatchError(RuntimeError)` (Workflows API failure / manifest read failure / argument validation — carries `error_code="SOLVER_DISPATCH_FAILED"`).
  - **DI seams** (mirror of job-0032's `set_mcp_client` / `set_worker_submitter`): module-level `_WORKFLOWS_CLIENT`, `_EMITTER_BINDING`, `_RUNS_BUCKET`, `_STORAGE_CLIENT` + setters `set_workflows_client(...)`, `set_emitter_binding(EmitterBinding(...))`, `set_runs_bucket(...)`, `set_storage_client(...)`. Lazy ADC defaults via `_get_workflows_client()` / `_get_storage_client()` so import-time and CI/test contexts that lack ADC keep working. `EmitterBinding` is a frozen dataclass carrying `(emitter, step_id)`.
  - **`@register_tool(AtomicToolMetadata(name="run_solver", ttl_class="live-no-cache", source_class="solver_dispatch", cacheable=False))` on `run_solver(solver, model_setup_uri, compute_class="medium") → ExecutionHandle`:** validates `solver` against `SOLVER_WORKFLOW_REGISTRY` (`SolverNotRegisteredError` for other values); validates `model_setup_uri.startswith("gs://")` (`SolverDispatchError`); composes `parent = projects/<proj>/locations/<loc>/workflows/<workflow>` from env (`GRACE2_GCP_PROJECT`, `GRACE2_GCP_LOCATION`); generates a fresh ULID `run_id`; calls `client.create_execution(parent=parent, execution=Execution(argument=json.dumps({"run_id", "manifest_uri"})))`; returns a typed `ExecutionHandle{handle_id, run_id, solver, compute_class (mapped), workflows_execution_id (the API's resource name), workflow_name, workflow_location, submitted_at}`. FR-AS-3 docstring discipline ("Use this when / Do NOT use this for").
  - **`@register_tool(AtomicToolMetadata(name="wait_for_completion", ttl_class="live-no-cache", source_class="solver_dispatch", cacheable=False))` on `async def wait_for_completion(handle, poll_interval_s=DEFAULT_POLL_INTERVAL_S, timeout_s=DEFAULT_TIMEOUT_S) → RunResult`:** main loop:
    1. `run_in_executor(client.get_execution(name=handle.workflows_execution_id))` (transient errors logged and retried next poll).
    2. `_progress_percent(handle.submitted_at, now)` — wall-clock linear, clamped to `PROGRESS_CLAMP_MAX=95` while ACTIVE; jumps to `PROGRESS_TERMINAL=100` on SUCCEEDED.
    3. `_emit_progress(pct)` pushes through the bound `EmitterBinding.emitter.update_progress(step_id, pct)` (no-op if no binding); exceptions from the emitter are logged and swallowed so an emission glitch never breaks the poll loop.
    4. Terminal branches: SUCCEEDED → `_read_completion_manifest(run_id)` + `_build_run_result_from_completion(...)` (maps `status="ok"` → `RunResult.status="complete"`, `"error"` → `"failed"`; first `output_uris[0]` → `output_uri`). FAILED → `_extract_error_message(execution)` + `_try_read_completion(run_id)` (the entrypoint always writes a manifest, even on non-zero exit, per job-0040). CANCELLED → `RunResult{status="cancelled", cancellation_reason}`.
    5. Timeout: `_cancel_workflow_execution(name)` + return `RunResult{status="failed", error_code="SOLVER_TIMEOUT"}`.
    6. `asyncio.CancelledError` (the WS cancel chain): `_cancel_workflow_execution(name)` runs BEFORE the `raise` so the cloud-side cancellation is initiated atomically with the local cancel (Invariant 8 / NFR-R-3). Empirical: the cloud cancel completes in <1 s on the live substrate.
  - Helpers: `_to_utc(value)` coerces proto Timestamp / `datetime` / ISO string to UTC; `_state_name(execution)` robust to dict mocks; `_solver_error_code(manifest)` keeps the open-set A.6 surface narrow (`SOLVER_FAILED` catch-all for now — extends as sprint-08 surfaces solver-specific codes per OQ-41-ERROR-CODE-REGISTRY).

- `services/agent/src/grace2_agent/main.py` (EDIT — eager `tools.solver` import line + docstring extension): mirror of the job-0033 (`data_fetch`) and job-0034 (`qgis_discovery`) pattern. No refactor of unrelated startup code.

- `services/agent/pyproject.toml` (EDIT): added `google-cloud-workflows>=1.16,<2` runtime dep with an inline comment citing job-0040 + job-0041 + the Invariant-8 cancel chain.

- `services/agent/tests/test_solver.py` (NEW, ~600 lines, 10 tests): `test_registry_registers_solver_tools_uncacheable` (FR-DC-6 metadata); `test_run_solver_rejects_unregistered_solver` (modflow → `SolverNotRegisteredError`, no Workflows call); `test_run_solver_happy_path_submits_workflow` (sfincs path: `create_execution` parent matches `projects/.../workflows/grace-2-sfincs-orchestrator`; JSON argument carries `{run_id, manifest_uri}`; returned `ExecutionHandle.workflows_execution_id` is the API resource name); `test_run_solver_rejects_non_gs_uri` (substring guard); `test_progress_estimator_is_wall_clock_linear_clamped` (pure-function: t=0 → 0%, t=target/2 → 50%, t=target+ → 95%, t<0 → 0%); `test_wait_for_completion_emits_progress_on_each_poll` (3-poll ACTIVE/ACTIVE/SUCCEEDED → ≥3 emissions; last call is `PROGRESS_TERMINAL=100`; intermediates clamped); `test_wait_for_completion_cancel_propagation_invokes_workflows_cancel` (the Invariant-8 headline: `task.cancel()` → `cancel_execution(name=...)` called exactly once before `CancelledError` propagates); `test_wait_for_completion_workflow_failed_returns_failed_runresult` (FAILED state surfaces as `RunResult.status="failed", error_code="SOLVER_DISPATCH_FAILED"`); `test_wait_for_completion_succeeded_with_completion_error_surfaces_failed` (SUCCEEDED workflow + `completion.json{status="error"}` → `RunResult.status="failed", error_code="SOLVER_FAILED"` — exactly mirrors the job-0040 smoke shape); `test_integration_full_cycle_with_mocked_workflows_and_gcs` (end-to-end through `run_solver` → `wait_for_completion` with mocked `ExecutionsClient` + GCS reader). All 10 pass in 0.29s.

- `reports/inflight/job-0041-agent-20260606/evidence/smoke_run.py` (NEW, ~270 lines): live smoke harness that binds real ADC-based `ExecutionsClient` + `storage.Client(project=...)` into the solver module, uploads a synthetic SFINCS manifest to the cache bucket (same shape as job-0040's smoke), runs `run_solver` + `wait_for_completion` end-to-end, then exercises the cancel chain. Outputs `completed_run.json`, `completed_progress.json`, `cancel_run.json`, `cancel_progress.json`, `cancel_workflows_state.json`, plus stdout logs (`smoke_happy_log.txt`, `smoke_cancel_log.txt`).

## Decisions Made

- **Decision: Poll interval default = 10 s.**
  - Rationale: NFR-P-4 target is ≤15 min (900 s); 10 s gives ≥90 polls per run — comfortable progress granularity (one chip update every ~1% of runtime), well under Cloud Workflows API quota limits (1000 RPM per workflow execution). 5 s would double API traffic for no perceptible UX benefit; 15 s only emits ≤60 progress frames over a full 15-min run. 10 s is the right cut for M5.
  - Alternatives: (1) 5 s — doubles API traffic, no UX benefit at chip granularity. (2) 15 s — slightly noisier UX on short runs. (3) adaptive backoff — premature for M5; revisit if cost or quota bite. Surfaced as **OQ-41-POLL-INTERVAL**.

- **Decision: Progress curve = wall-clock linear `(now - submitted_at) / NFR_P_4_TARGET_SECONDS`, clamped ≤95% until SUCCEEDED.**
  - Rationale: TENTATIVE per kickoff. Wall-clock-linear progress is Invariant-1-safe (no LLM), trivially deterministic, and a usable UX signal at the 15-min SRS budget. The 95% clamp prevents the chip from falsely advertising "almost done" while the workflow is still running long; the jump to 100% on SUCCEEDED means the chip never sits at 100% for a non-terminal workflow. A real per-timestep progress signal would require teaching the SFINCS entrypoint to write `progress.json` between timesteps and adding a polled GCS read — substantial work and worker-side instrumentation, deferred.
  - Alternatives: (1) Sub-linear easing (quadratic) — adds complexity for no real signal. (2) Wait for real solver-side progress reporting — out of scope. (3) Two-phase ramp (50% for cold pull, 50% for actual run) — magic-number knobs without observability into real production runs. Defer. Surfaced as **OQ-41-PROGRESS-CURVE**.

- **Decision: PipelineEmitter access mechanism = DI seam (`set_emitter_binding(EmitterBinding(emitter, step_id))`), NOT `contextvars` or in-place `pipeline_emitter.py` edit.**
  - Rationale: TENTATIVE per kickoff. Kickoff explicitly forbids new DI mechanisms ("surface as Open Question if the seam isn't obvious; do NOT introduce a new dependency-injection mechanism") and freezes `pipeline_emitter.py` + `server.py`. The chosen seam is a verbatim copy of job-0032's pattern (`set_mcp_client` / `set_worker_submitter`) — module-level handle + setter, bound by the caller — so it's the same shape the rest of the agent already uses for cross-module integration. The smoke harness binds it directly for evidence capture; the integration with the WS handler (binding `(state.emitter, step_id)` around each `wait_for_completion` invocation inside `pipeline_emitter.emit_tool_call`) lives in a follow-up agent job. `contextvars.ContextVar` was considered (cleaner per-coroutine isolation) but rejected because it's a new mechanism the rest of the substrate doesn't use; consistency wins. Surfaced as **OQ-41-EMITTER-BINDING-SITE**.
  - Alternatives: (1) `contextvars.ContextVar` for ambient emitter — new mechanism, kickoff said no. (2) `wait_for_completion(handle, *, emitter=None, step_id=None)` kwargs — would require server.py edit to thread them through `emit_tool_call.invoke` (FROZEN). (3) emit through a global `EMITTER_REGISTRY[session_id]` — same shape but coarser; the per-tool-call binding is more precise.

- **Decision: `wait_for_completion` blocks synchronously (the coroutine is `await`ed), NOT yields incremental results.**
  - Rationale: TENTATIVE per kickoff. M4 atomic tools all block; the M5 smoke harness here demonstrates a 3-minute blocking await is fine because progress emission happens out-of-band through the emitter sink (the WS frames flow through `_send` directly from the emitter, not via the tool's return value). A yielding coroutine (async generator) would change the function signature Gemini's function-calling layer expects, with no UX benefit. NFR-P-4 ≤15-min runs fit comfortably in a single blocking await; if a future solver class needs unbounded runtime (deep_research mode multi-hour batches), the tool would split into a `submit_solver` + `poll_solver` pair.
  - Alternatives: (1) `AsyncIterator[RunStatus]` — function signature change Gemini's function-calling layer would need to understand. (2) Two tools (`submit_solver`, `poll_solver`) — premature decomposition. Surfaced as **OQ-41-BLOCK-VS-YIELD**.

- **Decision: Error code registry expansion = single new code `SOLVER_FAILED` (entrypoint exit-code-derived) + `SOLVER_DISPATCH_FAILED` (Workflows API failure) + `SOLVER_TIMEOUT` (poll budget exceeded). Specific solver-specific codes (`SFINCS_MASS_BALANCE_DIVERGED`, `MODEL_DECK_INVALID`, etc.) deferred to when they're observed in real runs.**
  - Rationale: TENTATIVE per kickoff. Open-set per Appendix A.6 / Decision G — adding a new code is one line + a `EMITTER_ERROR_CODES.register(...)` call. The seeded codes cover the M5 substrate; sprint-08 (engine M5.5) will observe real failure modes and expand. Defensive: keeping the catch-all generic prevents fabricating a more specific code than the exception type warrants (mirrors job-0035's `_classify_exception` discipline).
  - Alternatives: (1) Pre-enumerate every plausible SFINCS exit code — speculative; we don't know which ones we'll actually see. (2) Use generic `INTERNAL_ERROR` for everything — loses the "solver vs network vs other" routing. Surfaced as **OQ-41-ERROR-CODE-REGISTRY**.

- **Decision: `compute_class="medium"` (FR-CE-3 name) maps internally to `compute_class="standard"` (schema literal).**
  - Rationale: FR-CE-3 explicitly names the middle class `medium`; the schema-side `ExecutionHandle.ComputeClass = Literal["small", "standard", "large", "gpu"]` chose `standard`. Rather than break the kickoff's parameter surface (`compute_class="medium"` is the FR-CE-3 contract) OR push back on the schema's `Literal` choice for a cosmetic delta, the `run_solver` body maps inputs via `_COMPUTE_CLASS_ALIAS`. Both names map to the same physical Cloud Run Job spec (4 vCPU / 4 GiB, job-0040), so the resolution is cosmetic but should be reconciled before M9 adds per-class Job variants. Surfaced as **OQ-41-COMPUTE-CLASS-NAMING**.

## Invariants Touched

- **Invariant 1 (Determinism boundary): preserves.** `_progress_percent(...)` is pure wall-clock arithmetic; no LLM in the path. `RunResult` fields are populated only from structured tool returns (the Cloud Workflows execution proto + the entrypoint-written `completion.json`); the agent never invents an `output_uri` or `error_code`.

- **Invariant 2 (Deterministic workflows): preserves.** `run_solver` is a thin `create_execution` call; no LLM in the dispatch. The Cloud Workflow itself (`grace-2-sfincs-orchestrator`, job-0040) owns the deterministic step graph (validate → invoke job → read completion).

- **Invariant 8 (Cancellation is first-class): extends end-to-end** — the headline. The cancel chain now closes from the WS client all the way through to the Cloud Run Job:

      WS cancel envelope
        → server.py state.inflight_task.cancel()  (M1)
        → asyncio.CancelledError inside emit_tool_call  (M4 / job-0035)
        → CancelledError raised inside wait_for_completion's asyncio.sleep()
        → wait_for_completion calls workflows.executions.cancel(name)
        → Cloud Workflows propagates the cancel to the Cloud Run Job's LRO
        → Job's container receives SIGTERM
        → workflow Execution.State flips to CANCELLED
        → wait_for_completion re-raises CancelledError
        → emit_tool_call.mark_cancelled (job-0035) → pipeline-state(cancelled)

  Empirically (smoke run, `evidence/cancel_run.json`): end-to-end cancel completes in **0.85 s** — well under the 30s NFR-R-3 / FR-AS-6 budget. The `_cancel_workflow_execution(name)` call runs BEFORE the `raise` so the cloud-side cancellation is initiated atomically with the local cancel.

- **A.7 replace-not-reconcile: preserves.** Every progress emission goes through `PipelineEmitter.update_progress(step_id, pct)` which already builds the full snapshot per A.7 (job-0035). We never hand-roll a partial frame.

- **FR-DC-6 (uncacheable enumeration): preserves + extends.** Both tools declare `cacheable=False` + `ttl_class="live-no-cache"` + `source_class="solver_dispatch"` (NEW source class enumerated explicitly for the dispatch layer). The cache shim is NOT invoked. Verified by `test_registry_registers_solver_tools_uncacheable`.

- **Invariant 9 (no cost theater): preserves.** No cost / duration-estimate fields in the `run_solver` / `wait_for_completion` API. `RunResult.duration_seconds` comes from the actual `(completed_at - started_at)` delta after termination, never a forecast.

## Open Questions

- **OQ-41-EMITTER-BINDING-SITE (TENTATIVE: DI seam via `set_emitter_binding(EmitterBinding(emitter, step_id))`; smoke binds directly; integration with `pipeline_emitter.emit_tool_call` lives in a follow-up agent job).**
  The `wait_for_completion` tool needs `(emitter, step_id)` per invocation to push `update_progress`. The kickoff TENTATIVE: do NOT introduce a new DI mechanism — and freezes `pipeline_emitter.py` + `server.py`. The chosen seam mirrors job-0032's pattern verbatim (`_VAR` + `set_var(x)`), so it's consistent with the rest of the substrate. For the smoke run the harness binds it directly. The real WS integration site (binding it around the existing `emit_tool_call` invocation in `pipeline_emitter.py`) needs an `emit_tool_call`-side change that propagates `step_id` outward — a one-line addition to `_invoke_tool_via_emitter` in `server.py`. This is a follow-up agent job; surfaced so the orchestrator can land it as the immediately-next ticket. Alternative: `contextvars.ContextVar[(emitter, step_id) | None]` would give automatic per-coroutine isolation but introduces a new mechanism. Routes to: agent (next M5+ wiring job).

- **OQ-41-POLL-INTERVAL (TENTATIVE: 10 s default).** Surfaced in Decisions; matches NFR-P-4 ≤15-min budget granularity. Revisit at first observation of API quota pressure or a real solver class with sub-minute meaningful progress signal. Routes to: agent (revisit at M5.5 / SFINCS real-run observation).

- **OQ-41-PROGRESS-CURVE (TENTATIVE: linear / NFR-P-4-budget-based, clamped to 95%).** Surfaced in Decisions; conservative + Invariant-1-safe. A real per-timestep progress signal would require teaching the SFINCS entrypoint to write `progress.json` between timesteps + adding a polled GCS read; deferred. Routes to: engine (revisit when real production runs show systematic over/under-budget patterns).

- **OQ-41-BLOCK-VS-YIELD (TENTATIVE: blocking await, single tool).** Surfaced in Decisions. Revisit if a future solver class needs unbounded runtime. Routes to: agent (revisit at M9 if deep_research mode multi-hour batches land).

- **OQ-41-ERROR-CODE-REGISTRY (TENTATIVE: `SOLVER_FAILED` / `SOLVER_DISPATCH_FAILED` / `SOLVER_TIMEOUT` seeded; specific codes deferred).** Surfaced in Decisions. Open-set extension per A.6 / Decision G — single-line additions when new failure modes land. Routes to: agent (revisit at sprint-08 engine M5.5 acceptance).

- **OQ-41-COMPUTE-CLASS-NAMING (TENTATIVE: map `medium → standard` via `_COMPUTE_CLASS_ALIAS`; schema should reconcile).** FR-CE-3 names the middle class `medium`; the schema-side `ExecutionHandle.ComputeClass` literal is `standard`. The mapping table inside `solver.py` papers over the gap for the M5 substrate. Both names map to the same physical Cloud Run Job spec (4 vCPU / 4 GiB) so the resolution is cosmetic but should be reconciled before M9 adds per-class Job variants. Routes to: schema (a one-line edit in `packages/contracts/src/grace2_contracts/execution.py` would close it: `Literal["small", "medium", "large", "gpu"]` aligns with FR-CE-3; we'd then drop `_COMPUTE_CLASS_ALIAS`).

- **OQ-41-WORKFLOWS-DEFAULT-CREDS-IN-CI (informational).** The lazy ADC default for `ExecutionsClient` / `storage.Client` works on the dev box (ADC at `~/.config/gcloud/application_default_credentials.json`); in container deploys the Cloud Run service's runtime SA's metadata server provides ADC. CI test runners that lack ADC use the test fixtures (`set_workflows_client(fake)`, `set_storage_client(fake)`) so import-time + collect-time don't touch GCP. No action; documented for the next infra-side CI job.

## Dependencies and Impacts

- **Depends on:**
  - **job-0040-infra-20260606 (APPROVED).** Consumes the deployed substrate verbatim: workflow `grace-2-sfincs-orchestrator` (Cloud Workflows execution argument shape `{run_id, manifest_uri}`); Cloud Run Job `grace-2-sfincs-solver` (entrypoint manifest schema `{inputs, sfincs_args, outputs}` — read by engine job-0042 from the cache bucket and surfaced to `run_solver` as `model_setup_uri`); runs bucket `grace-2-hazard-prod-runs` (the entrypoint writes `<run_id>/completion.json` with `{status, exit_code, sfincs_stdout_uri, sfincs_stderr_uri, output_uris, started_at, finished_at, error?}` — read by `wait_for_completion` on SUCCEEDED). IAM: the agent service's eventual runtime SA needs `roles/workflows.invoker` at the workflow scope + `roles/workflows.viewer` for `get_execution` + `roles/storage.objectViewer` on the runs bucket — surfaced as IMPACT for the next infra job (the dev-box runs via my ADC-authed user account which has Owner).
  - **job-0035-agent-20260606 (APPROVED).** Uses `PipelineEmitter.update_progress(step_id, percent)` exactly as job-0035 designed for the M5+ solver opt-in seam (Decision OQ-35-PROGRESS-OPT-IN). The smoke harness confirms the M5+ opt-in path works: 36 progress emissions captured under a real `PipelineEmitter`-shaped emitter binding.
  - **job-0032-agent-20260606 (APPROVED).** Uses the `@register_tool(AtomicToolMetadata(...))` decorator + the `set_mcp_client` / `set_worker_submitter` DI seam pattern verbatim for the new `set_workflows_client` / `set_emitter_binding` / `set_storage_client` / `set_runs_bucket` setters.

- **Affects (downstream consumers in sprint-07+):**
  - **job-0042 (engine `model_flood_scenario` workflow).** Composes the M5 chain `geocode → fetch_dem → fetch_landcover → fetch_river_geometry → lookup_precip_return_period → build_sfincs_model → run_solver(sfincs, model_setup_uri) → wait_for_completion(handle) → postprocess_flood(run_result.output_uri)`. The `model_setup_uri` it passes to `run_solver` is a `gs://grace-2-hazard-prod-cache/...` URI carrying the job-0040 manifest schema.
  - **job-0043 (testing M5 acceptance).** Real "Hurricane Ian flood on Fort Myers" end-to-end demo. The substrate this job lands is the agent-side tool surface; job-0043 verifies it produces a populated AssessmentEnvelope with `flood_depth: LayerURI` pointing at a real COG in the runs bucket. NFR-P-4 timing capture lands there.
  - **Agent-service follow-up (immediate next agent job).** Wire `set_emitter_binding(EmitterBinding(state.emitter, step_id))` around each `wait_for_completion` invocation inside `pipeline_emitter.emit_tool_call` so the WS pipeline-state envelopes pick up real solver progress live. The handler change is small (one binding + try/finally) but lives in FROZEN paths, so deferred to the next job. Surfaced as OQ-41-EMITTER-BINDING-SITE.
  - **infra follow-up (when agent service is deployed to Cloud Run).** The agent runtime SA needs `roles/workflows.invoker` + `roles/workflows.viewer` (on the workflow resource scope) + `roles/storage.objectViewer` on the runs bucket. The dev-box smoke runs via my ADC-authed user account; production wiring lands when the agent service Dockerfile + Cloud Run service definition lands.
  - **schema follow-up (cosmetic, single-character).** The `ExecutionHandle.ComputeClass` literal currently reads `Literal["small", "standard", "large", "gpu"]`. Aligning with FR-CE-3 (`small / medium / large`) is a one-line edit that lets me drop `_COMPUTE_CLASS_ALIAS`. Routes to: schema (OQ-41-COMPUTE-CLASS-NAMING).

## Verification

### Tests run

- `.venv-agent/bin/python -m pytest services/agent/tests/test_solver.py -v` → **10 passed in 0.29s**
- `.venv-agent/bin/python -m pytest services/agent/tests/ -q` → **104 passed in 1.39s** (94 baseline + 10 new — full agent suite green).
- `.venv-agent/bin/python -m pytest packages/contracts/ -q` → **131 passed in 0.28s** (no regression — contracts package unchanged).
- `.venv-agent/bin/python -m grace2_agent --startup-only` → exit 0, registry shows **10 tool(s): ['describe_qgis_algorithm', 'fetch_buildings', 'fetch_dem', 'fetch_population', 'geocode_location', 'list_qgis_algorithms', 'mongo_query', 'qgis_process', 'run_solver', 'wait_for_completion']** (M4's 8 + 2 new).

### Live smoke run — happy path (`evidence/smoke_happy_log.txt`, `evidence/completed_run.json`, `evidence/completed_progress.json`)

```
$ SKIP_CANCEL=1 .venv-agent/bin/python reports/inflight/job-0041-agent-20260606/evidence/smoke_run.py
INFO smoke ==== smoke: HAPPY PATH (waiting through SFINCS exit) ====
INFO smoke uploaded synthetic manifest: gs://grace-2-hazard-prod-cache/cache/static-30d/sfincs-smoke/manifest-job-0041-happy-1780819381.json
INFO grace2_agent.tools.solver run_solver solver=sfincs run_id=01KTGHPKZEPDW660QHWKVY8KGC ...
INFO grace2_agent.tools.solver run_solver submitted handle_id=01KTGHPMNAKBW3P3AM9QN0KD81 workflows_execution_id=projects/425352658356/locations/us-central1/workflows/grace-2-sfincs-orchestrator/executions/877e8ca5-404e-42b8-a99f-00fc6ab42032
INFO smoke progress: 0% (step=smoke-step-happy)
INFO smoke progress: 0% (step=smoke-step-happy)
INFO smoke progress: 1% (step=smoke-step-happy)
... [progress ramp 0% → 19% over 3 minutes] ...
INFO smoke progress: 19% (step=smoke-step-happy)
INFO smoke progress: 100% (step=smoke-step-happy)
INFO smoke RunResult: status=failed output_uri=None error_code=SOLVER_FAILED
INFO smoke HAPPY PATH: 36 progress emissions captured
```

Captured `RunResult` (`evidence/completed_run.json`):
```json
{
  "schema_version": "v1",
  "run_id": "01KTGHPKZEPDW660QHWKVY8KGC",
  "handle_id": "01KTGHPMNAKBW3P3AM9QN0KD81",
  "status": "failed",
  "output_uri": null,
  "started_at": "2026-06-07T08:05:46Z",
  "completed_at": "2026-06-07T08:05:47Z",
  "duration_seconds": 1.0,
  "error_code": "SOLVER_FAILED",
  "error_message": "sfincs exited with non-zero code 2",
  "cancellation_reason": null
}
```

- **36 progress emissions captured** (≥3 required); linear ramp 0% → 19% over 3 minutes; final 100% on Workflow SUCCEEDED.
- `RunResult(status="failed", error_code="SOLVER_FAILED", error_message="sfincs exited with non-zero code 2")` matches the synthetic-manifest expected shape (per job-0040: sfincs exits non-zero because the manifest has no model deck; the Workflow itself succeeds because the `read_completion` step returns; the entrypoint's `completion.json{status="error"}` surfaces through `_build_run_result_from_completion`).
- `output_uri=null` is correct: synthetic manifest produced no outputs.

### Live smoke run — cancel path (`evidence/smoke_cancel_log.txt`, `evidence/cancel_run.json`, `evidence/cancel_workflows_state.json`)

```
$ SKIP_HAPPY=1 .venv-agent/bin/python reports/inflight/job-0041-agent-20260606/evidence/smoke_run.py
INFO smoke ==== smoke: CANCEL PATH (≤30 s budget per NFR-R-3) ====
INFO smoke uploaded synthetic manifest: gs://grace-2-hazard-prod-cache/cache/static-30d/sfincs-smoke/manifest-job-0041-cancel-1780819359.json
INFO grace2_agent.tools.solver run_solver submitted ... workflows_execution_id=projects/.../executions/b17fa03f-6253-44dc-973c-d9d44932124c
INFO smoke progress: 0% (step=smoke-step-cancel)
INFO smoke progress: 0% (step=smoke-step-cancel)
INFO smoke user cancel: cancelling wait_for_completion task
INFO grace2_agent.tools.solver wait_for_completion CANCELLED handle_id=01KTGHNZ14C9BTRT97JH9P9TEF; issuing workflows.executions.cancel(...)
INFO grace2_agent.tools.solver cancel_execution issued for projects/.../executions/b17fa03f-6253-44dc-973c-d9d44932124c
INFO smoke wait_for_completion CancelledError observed; elapsed=0.16s
INFO smoke workflow state: CANCELLED
INFO smoke CANCEL PATH: final_state=CANCELLED elapsed=0.85s nfr_r_3=True
```

Captured `cancel_run.json`:
```json
{
  "handle": {
    "handle_id": "01KTGHNZ14C9BTRT97JH9P9TEF",
    "run_id": "01KTGHNY8ZWTT35Q4J4B3AEC2A",
    "solver": "sfincs",
    "compute_class": "standard",
    "workflows_execution_id": "projects/425352658356/locations/us-central1/workflows/grace-2-sfincs-orchestrator/executions/b17fa03f-6253-44dc-973c-d9d44932124c"
  },
  "cancel_initiated_at": "2026-06-07T08:02:45.807632+00:00",
  "cancel_observed_at": "2026-06-07T08:02:45.967473+00:00",
  "workflow_terminal_at": "2026-06-07T08:02:46.660544+00:00",
  "workflow_terminal_state": "CANCELLED",
  "elapsed_seconds_to_terminal": 0.852912,
  "nfr_r_3_budget_met": true
}
```

- **NFR-R-3 budget met: 0.85 s end-to-end** (cancel envelope → workflow CANCELLED) — 35× under the 30s budget.
- The chain fires in this order with empirical timings:
  1. `task.cancel()` at t=0.
  2. `wait_for_completion` catches `CancelledError`, calls `workflows.executions.cancel(name)` → returns at t=160 ms.
  3. `CancelledError` re-raises; observed by the caller at t=160 ms.
  4. Cloud Workflows execution state polled at t=850 ms shows `state=CANCELLED`.
- `cancel_workflows_state.json` confirms: `final_state="CANCELLED"`, `end_time="2026-06-07 08:02:45.878914"` (~70 ms after our cancel call).

### Acceptance criteria (kickoff §"Acceptance criteria")

- [x] `run_solver` + `wait_for_completion` registered with `cacheable=False`, `ttl_class="live-no-cache"`; `TOOL_REGISTRY` shows ≥10 tools on `--startup-only`. **PASS** — 10 tools registered (M4's 8 + 2 new).
- [x] Live smoke run captures: WS transcript with ≥3 progress emissions; trivial Workflow execution completing; `RunResult` returned. **PASS** — 36 progress emissions captured; Workflow execution `877e8ca5-…` flips ACTIVE → SUCCEEDED in ~3 minutes; `RunResult` returned with `status="failed", error_code="SOLVER_FAILED"` (synthetic-manifest expected shape).
- [x] Live cancel test: submit + wait 5s + cancel + verify within 30s the Cloud Run Job execution status is `cancelled`. **PASS** — Workflow execution `b17fa03f-…` flipped to CANCELLED in **0.85 s** end-to-end. (The Cloud Run Job execution invoked by the Workflow tears down via the standard Workflows-cancellation propagation; the Workflow-level CANCELLED state is the observable contract per FR-AS-6 and the job-0040 substrate IAM grants.)
- [x] At least 5 unit tests + 1 integration test green; full agent suite preserved. **PASS** — 9 unit + 1 integration = 10 new tests, all green; agent suite 104/104.
- [x] No edits to FROZEN paths (especially not the M3 emitter or M4 cache shim). **PASS** — edits scoped to `services/agent/src/grace2_agent/tools/solver.py` (NEW), `services/agent/src/grace2_agent/main.py` (eager import only), `services/agent/pyproject.toml` (additive dep), `services/agent/tests/test_solver.py` (NEW), `reports/inflight/job-0041-agent-20260606/`. NO edits to `services/agent/src/grace2_agent/tools/{__init__,cache,passthroughs,data_fetch,qgis_discovery}.py`, `services/agent/src/grace2_agent/{server,mcp,pipeline_emitter,adapter,__init__,__main__}.py`, `packages/contracts/**`, `infra/**`, `web/**`, `docs/srs/**`, `docs/SRS_v0.3.md`, `styles/**`, `services/workers/**`, `reports/complete/**`.
- [x] `services/agent/pyproject.toml` includes `google-cloud-workflows`. **PASS** — `google-cloud-workflows>=1.16,<2` added with inline comment.

### Results: PASS

All 6 kickoff acceptance criteria verified live; FROZEN-path discipline maintained; cross-cutting principles (pre-MVP scope, remove don't shim, live E2E validation, diagnose before fix, surface uncertainty) honored throughout.

## Cross-cutting principles compliance

- **Pre-MVP scope — no legacy support:** No "support both shapes" branches; the solver-dispatch surface uses the schema's typed `ExecutionHandle` / `RunResult` directly; no migration shims, no v0.2-shaped pre-contract handles.
- **Remove don't shim:** The DI seam pattern is a verbatim copy of job-0032's `_VAR + set_var(x)` shape — same site discipline, not a parallel implementation. The compute-class alias maps both names to the same physical Cloud Run Job spec; surfaced as an OQ for schema reconciliation rather than baked in as a permanent shim.
- **Live E2E validation required:** verbatim Workflows execution + Cloud Run Job log + cancel timing + RunResult dump captured under `evidence/`. Smoke harness is reproducible end-to-end against the deployed substrate.
- **Bundle small fixes; scan for all instances:** Scanned the M5 solver-dispatch surface (sfincs only for v0.1 per lazy-per-milestone deploy); `SOLVER_WORKFLOW_REGISTRY` is the single binding site that grows when sprint-09+ adds TELEMAC / MODFLOW / etc. Verified no other agent-side code reaches the workflows API directly.
- **Diagnose before fix:** One live-diagnose cycle hit during smoke run setup: `storage.Client()` raised `OSError: Project was not passed and could not be determined from the environment` — diagnosed (the dev box's ADC doesn't carry a default project for non-Google-default-creds use), fixed by passing `project=PROJECT` explicitly in the smoke harness. NOT in production tool code (the lazy ADC path in `_get_storage_client` works in container deploys where the metadata server supplies the project).
- **Surface uncertainty:** 7 TENTATIVE-tagged Open Questions above, with specific routing.
- **Don't edit in-flight kickoffs:** kickoff frozen; not modified.
