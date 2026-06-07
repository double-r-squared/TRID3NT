"""Solver dispatch atomic tools (job-0041, M5 Stage C).

This module registers two atomic tools that drive the Cloud Workflows
orchestration substrate landed by job-0040 (``grace-2-sfincs-orchestrator``
workflow + ``grace-2-sfincs-solver`` Cloud Run Job + ``grace-2-hazard-prod-runs``
bucket). Together they implement the **FR-TA-2 solver-dispatch surface**:

    - ``run_solver(solver, model_setup_uri, compute_class="medium")
       -> ExecutionHandle`` — submits a Cloud Workflows execution against the
      solver-specific orchestrator. Currently only ``solver="sfincs"`` is
      supported; other values raise ``SolverNotRegisteredError`` (FR-TA-2).

    - ``wait_for_completion(handle, poll_interval_s=10, timeout_s=1800)
       -> RunResult`` — polls the Cloud Workflows execution every
      ``poll_interval_s`` seconds, emits a ``pipeline-state`` progress update
      on every poll via ``PipelineEmitter.update_progress`` (the opt-in seam
      job-0035 surfaced for M5+ solvers), and on Workflow success reads
      ``completion.json`` from the runs bucket and returns a populated
      ``RunResult``. On Workflow failure or cancellation the matching
      terminal ``RunResult`` is returned.

Both tools are uncacheable-by-construction per FR-DC-6 (solver dispatchers
are explicitly enumerated): ``cacheable=False``, ``ttl_class="live-no-cache"``,
``source_class="solver_dispatch"``. They never touch the cache shim.

Cross-cutting principles (per CLAUDE.md + agents/AGENTS.md):

- **Invariant 1 (Determinism boundary): preserves.** Progress estimation is
  a wall-clock linear ramp keyed off ``handle.submitted_at`` and the
  NFR-P-4 target (900 s for ``≤15 min``) — not an LLM estimate. The ramp
  is clamped at 95% until the Workflow returns SUCCEEDED (then jumps to
  100%) so we never falsely advertise completion.

- **Invariant 2 (Deterministic workflows): preserves.** ``run_solver`` is a
  thin Cloud Workflows ``create_execution`` call; no LLM in the dispatch.
  The Workflow itself owns the deterministic step graph (validate → invoke
  job → read completion). FR-CE-2.

- **Invariant 8 (Cancellation is first-class): the headline.** Cancel chain
  end-to-end:

      WS cancel -> server.py inflight_task.cancel()
                -> asyncio.CancelledError inside emit_tool_call
                -> emit_tool_call CALLs invoke() which is our
                    wait_for_completion coroutine
                -> wait_for_completion sees CancelledError in its poll
                    sleep, calls workflows.executions.cancel(name)
                -> Cloud Workflows propagates SIGTERM to the Cloud Run Job
                -> Execution.State flips to CANCELLED within ≤30 s
                -> wait_for_completion re-raises CancelledError so
                    emit_tool_call's mark_cancelled branch fires

  FR-AS-6 / NFR-R-3 30s budget. We additionally call
  ``cancel_execution`` *before* re-raising the ``CancelledError`` so the
  cloud-side cancellation is initiated atomically with the local cancel.

- **A.7 replace-not-reconcile: preserves.** Every progress emission goes
  through ``PipelineEmitter.update_progress(step_id, ...)``, which already
  builds the full snapshot per A.7. We never hand-roll a partial frame.

- **FR-DC-6 (uncacheable enumeration): preserves.** Both tools declare
  ``cacheable=False`` + ``ttl_class="live-no-cache"`` + a new source class
  ``"solver_dispatch"``. The kickoff explicitly enumerates them.

Dependency-injection seams (mirrors job-0032's ``passthroughs.py`` pattern):

- ``_WORKFLOWS_CLIENT`` / ``set_workflows_client(client)`` — the Cloud
  Workflows ``ExecutionsClient``. Production wiring binds it at startup
  using Application Default Credentials; tests pass a Mock. Lazily-default
  at first use (so import-time does not require ADC) — see
  ``_get_workflows_client``.

- ``_EMITTER_BINDING`` / ``set_emitter_binding(emitter, step_id)`` — the
  active ``PipelineEmitter`` + the step_id this ``wait_for_completion``
  invocation is bracketed by. Set by the integration site (``server.py``)
  in a follow-up job that wires ``emit_tool_call`` to surface its
  ``step_id`` to the tool body. **TENTATIVE per kickoff Open Questions:**
  for the M5 smoke run we set the binding explicitly from the smoke
  harness; the integration with the WS handler lives in a follow-up agent
  job because ``pipeline_emitter.py`` + ``server.py`` are FROZEN here.

- ``_RUNS_BUCKET`` / ``set_runs_bucket(name)`` — overrides the default
  ``grace-2-hazard-prod-runs`` bucket name. Used by the smoke harness to
  reach a fixture bucket; production wiring leaves it at the default.

- ``_STORAGE_CLIENT`` / ``set_storage_client(client)`` — the GCS client
  ``wait_for_completion`` uses to read ``completion.json``. Lazily-default
  to the application's ADC-bound client.

Run id generation: the agent service generates a ULID per ``run_solver``
call. The same id flows into the workflow execution argument
(``run_id``) and is used to compose the runs-bucket completion path
(``gs://<runs_bucket>/<run_id>/completion.json``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from grace2_contracts import new_ulid
from grace2_contracts.execution import ExecutionHandle, RunResult
from grace2_contracts.tool_registry import AtomicToolMetadata

from . import register_tool

__all__ = [
    "run_solver",
    "wait_for_completion",
    "SolverNotRegisteredError",
    "SolverDispatchError",
    "set_workflows_client",
    "set_emitter_binding",
    "set_runs_bucket",
    "set_storage_client",
    "SOLVER_WORKFLOW_REGISTRY",
    "EmitterBinding",
    "NFR_P_4_TARGET_SECONDS",
    "DEFAULT_POLL_INTERVAL_S",
    "DEFAULT_TIMEOUT_S",
    "PROGRESS_CLAMP_MAX",
    "PROGRESS_TERMINAL",
]

logger = logging.getLogger("grace2_agent.tools.solver")


# --------------------------------------------------------------------------- #
# Constants / configuration
# --------------------------------------------------------------------------- #


#: Target run-time budget for ≤200 km² at 30m per NFR-P-4 (15 min).
#: Progress is wall-clock linear in (now - submitted_at) / target.
NFR_P_4_TARGET_SECONDS: float = 900.0

#: Default poll cadence — matches NFR-P-4 ≤15-min budget granularity (≥9 polls).
DEFAULT_POLL_INTERVAL_S: int = 10

#: Default overall timeout (30 min — mirrors the Cloud Run Job task_timeout
#: from job-0040, gives 2× headroom over NFR-P-4).
DEFAULT_TIMEOUT_S: int = 1800

#: Highest progress we ever advertise before the Workflow is SUCCEEDED.
#: Clamp keeps us honest under late runs — the chip never jumps to 100% on
#: estimate alone.
PROGRESS_CLAMP_MAX: int = 95

#: Final progress when the Workflow reports SUCCEEDED.
PROGRESS_TERMINAL: int = 100


#: Solver → workflow name registry. Currently SFINCS only; other solvers
#: register their workflow names here in their own milestone sprints (M9+).
SOLVER_WORKFLOW_REGISTRY: dict[str, str] = {
    "sfincs": "grace-2-sfincs-orchestrator",
}


#: Map the kickoff-named compute classes (small/medium/large) onto the
#: ``ExecutionHandle.ComputeClass`` literal contract
#: (``Literal["small", "standard", "large", "gpu"]``). FR-CE-3 names the
#: middle class ``medium`` but the schema-side contract chose ``standard``;
#: rather than break the kickoff parameter surface we pin a mapping here.
#: Surfaced as OQ-41-COMPUTE-CLASS-NAMING for schema to reconcile.
_COMPUTE_CLASS_ALIAS: dict[str, str] = {
    "small": "small",
    "medium": "standard",  # FR-CE-3 medium == schema-side standard
    "standard": "standard",
    "large": "large",
    "gpu": "gpu",
}


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class SolverNotRegisteredError(ValueError):
    """Raised by ``run_solver`` when ``solver`` is not in
    ``SOLVER_WORKFLOW_REGISTRY``. Distinct from a tool-params-invalid error
    so the agent surface can render a useful "solver X not supported in v0.1
    (sprint-07 ships sfincs only — TELEMAC / MODFLOW / HEC-HMS land in
    their respective milestones)" message."""


class SolverDispatchError(RuntimeError):
    """Raised when the Cloud Workflows ``create_execution`` call fails or the
    completion-manifest read fails. The agent's emitter classifier maps this
    to ``UPSTREAM_API_ERROR``. The ``error_code`` attribute carries the
    open-set A.6 code so a downstream wrapper can re-emit it verbatim."""

    error_code: str = "SOLVER_DISPATCH_FAILED"


# --------------------------------------------------------------------------- #
# DI seams (mirrors passthroughs.set_mcp_client / set_worker_submitter)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class EmitterBinding:
    """Tuple of (emitter, step_id) the active ``wait_for_completion`` invocation
    should drive progress emissions through.

    The integration site (``server.py``'s ``emit_tool_call`` wrapper) is
    responsible for binding this around each ``wait_for_completion`` call;
    until that follow-up job lands, the smoke harness binds it directly per
    the kickoff TENTATIVE recommendation. Surfaced as
    OQ-41-EMITTER-BINDING-SITE."""

    emitter: Any
    step_id: str


_WORKFLOWS_CLIENT: Any | None = None
_EMITTER_BINDING: EmitterBinding | None = None
_RUNS_BUCKET: str | None = None
_STORAGE_CLIENT: Any | None = None


def set_workflows_client(client: Any) -> None:
    """Bind the Cloud Workflows ``ExecutionsClient`` used by both solver tools.

    Production wiring (``main.py``) binds an ADC-authenticated client at
    startup; tests pass a Mock. ``None`` disables the binding (the lazy
    default takes over at next use).
    """
    global _WORKFLOWS_CLIENT
    _WORKFLOWS_CLIENT = client


def set_emitter_binding(binding: EmitterBinding | None) -> None:
    """Bind the active ``(emitter, step_id)`` pair for progress emission.

    See class docstring for the integration-site discipline. ``None`` clears
    the binding (the polling loop falls back to no-op progress emission).
    """
    global _EMITTER_BINDING
    _EMITTER_BINDING = binding


def set_runs_bucket(name: str | None) -> None:
    """Override the runs-bucket name. ``None`` restores the env-based default."""
    global _RUNS_BUCKET
    _RUNS_BUCKET = name


def set_storage_client(client: Any) -> None:
    """Bind the GCS client used to read ``completion.json``. ``None`` restores
    the lazy ADC-defaulted client."""
    global _STORAGE_CLIENT
    _STORAGE_CLIENT = client


def _get_workflows_client() -> Any:
    """Return the bound ExecutionsClient or lazily construct an ADC default.

    Lazy import so the agent service can boot in CI/test environments that
    don't have ADC configured — the import path only resolves
    ``google.cloud.workflows`` when a tool is actually invoked.
    """
    if _WORKFLOWS_CLIENT is not None:
        return _WORKFLOWS_CLIENT
    try:
        from google.cloud.workflows.executions_v1 import ExecutionsClient  # type: ignore[import-not-found]
    except Exception as exc:  # noqa: BLE001
        raise SolverDispatchError(
            f"google-cloud-workflows not importable: {exc}; "
            "agent service startup should call set_workflows_client(...) "
            "or install google-cloud-workflows."
        ) from exc
    client = ExecutionsClient()
    return client


def _get_storage_client() -> Any:
    """Return the bound GCS Client or lazily construct an ADC default."""
    if _STORAGE_CLIENT is not None:
        return _STORAGE_CLIENT
    try:
        from google.cloud import storage  # type: ignore[import-not-found]
    except Exception as exc:  # noqa: BLE001
        raise SolverDispatchError(
            f"google-cloud-storage not importable: {exc}; "
            "agent service startup should call set_storage_client(...)."
        ) from exc
    return storage.Client()


def _get_runs_bucket() -> str:
    """Return the overridden runs bucket or the env-default
    (``GRACE2_RUNS_BUCKET`` if set, else ``grace-2-hazard-prod-runs``)."""
    if _RUNS_BUCKET is not None:
        return _RUNS_BUCKET
    return os.environ.get("GRACE2_RUNS_BUCKET", "grace-2-hazard-prod-runs")


def _gcp_project() -> str:
    """Return the GCP project id (env-driven; mirrors the substrate)."""
    return os.environ.get("GRACE2_GCP_PROJECT", "grace-2-hazard-prod")


def _gcp_location() -> str:
    """Return the GCP region for the workflows execution (env-driven)."""
    return os.environ.get("GRACE2_GCP_LOCATION", "us-central1")


# --------------------------------------------------------------------------- #
# run_solver
# --------------------------------------------------------------------------- #


_RUN_SOLVER_METADATA = AtomicToolMetadata(
    name="run_solver",
    ttl_class="live-no-cache",
    source_class="solver_dispatch",
    cacheable=False,
)


@register_tool(_RUN_SOLVER_METADATA)
def run_solver(
    solver: str,
    model_setup_uri: str,
    compute_class: str = "medium",
) -> ExecutionHandle:
    """Submit a solver execution to the deployed Cloud Workflows orchestrator.

    Use this when: the agent has a staged model (e.g. from
    ``build_sfincs_model``) and needs to actually run the solver on Cloud
    Run. Returns an ``ExecutionHandle`` whose ``workflows_execution_id``
    field is the Invariant-8 cancellation seam — feed it to
    ``wait_for_completion`` to poll progress and obtain the
    ``RunResult``.

    Do NOT use this for: cancelling a running execution (use the WS
    ``cancel`` envelope — the cancel chain reaches the Cloud Run Job
    automatically via ``wait_for_completion``'s cancel handler); polling
    a running execution (use ``wait_for_completion``); inspecting a
    completed run's outputs (those land in ``RunResult.output_uri`` per
    FR-CE-4).

    Params:
        solver: lowercase solver identifier. v0.1 supports ``"sfincs"``
            only; other values raise ``SolverNotRegisteredError`` per
            the kickoff's lazy-per-milestone deploy strategy.
        model_setup_uri: ``gs://...`` URI of the manifest the worker
            entrypoint will read (the job-0040 manifest schema:
            ``{"inputs":[...], "sfincs_args":[...], "outputs":[...]}``).
            Engine job-0042's ``model_flood_scenario`` workflow composes
            this from the M4 atomic-tool substrate.
        compute_class: FR-CE-3 compute class. Currently a tag carried on
            the handle for provenance; the deployed Cloud Run Job (job-
            0040) is fixed at 4 vCPU / 4 GiB (the FR-CE-3 ``medium``
            baseline). Other classes land when sprint-09+ adds the
            per-class Job variants. Defaults to ``"medium"``.

    Returns:
        ``ExecutionHandle{handle_id, run_id, solver, compute_class,
        workflows_execution_id, workflow_name, workflow_location,
        submitted_at}`` — the Invariant-8 cancellation contract. The
        ``workflows_execution_id`` is the fully-qualified resource name
        (``projects/.../locations/.../workflows/.../executions/...``)
        the Cloud Workflows API operates on.

    FR-DC-6: This tool is uncacheable-by-construction (solver dispatch is
    explicitly enumerated). The cache shim is NOT invoked.

    Invariant 8 (cancellation): the returned handle carries everything
    ``wait_for_completion`` needs to call
    ``workflows.executions.cancel(name)`` on the matching cancel envelope.

    Raises:
        SolverNotRegisteredError: ``solver`` not in
            ``SOLVER_WORKFLOW_REGISTRY``.
        SolverDispatchError: the Cloud Workflows API call failed (IAM,
            quota, malformed manifest). The exception is re-raised so the
            emitter classifier surfaces ``UPSTREAM_API_ERROR`` to the
            client.
    """
    if not isinstance(solver, str) or not solver.strip():
        raise SolverNotRegisteredError(
            f"solver must be a non-empty string; got {solver!r}"
        )
    workflow_name = SOLVER_WORKFLOW_REGISTRY.get(solver)
    if workflow_name is None:
        raise SolverNotRegisteredError(
            f"solver {solver!r} not registered for v0.1; supported: "
            f"{sorted(SOLVER_WORKFLOW_REGISTRY)} (lazy per-milestone deploy "
            "per sprint-07 strategy — TELEMAC / MODFLOW / HEC-HMS land in "
            "their respective milestones)."
        )
    if not isinstance(model_setup_uri, str) or not model_setup_uri.startswith("gs://"):
        raise SolverDispatchError(
            f"model_setup_uri must be a gs:// URI; got {model_setup_uri!r}"
        )

    project = _gcp_project()
    location = _gcp_location()
    parent = f"projects/{project}/locations/{location}/workflows/{workflow_name}"

    run_id = new_ulid()
    submitted_at = datetime.now(timezone.utc)
    argument = json.dumps({"run_id": run_id, "manifest_uri": model_setup_uri})

    logger.info(
        "run_solver solver=%s run_id=%s compute_class=%s parent=%s",
        solver,
        run_id,
        compute_class,
        parent,
    )

    client = _get_workflows_client()
    try:
        # Lazy import the Execution type so test environments without
        # google-cloud-workflows can still load the registry.
        from google.cloud.workflows.executions_v1 import Execution  # type: ignore[import-not-found]

        execution_obj = Execution(argument=argument)
        execution = client.create_execution(parent=parent, execution=execution_obj)
    except SolverDispatchError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise SolverDispatchError(
            f"Cloud Workflows create_execution failed for parent={parent}: {exc}"
        ) from exc

    workflows_execution_id = getattr(execution, "name", None)
    if not workflows_execution_id:
        raise SolverDispatchError(
            f"Cloud Workflows execution returned no resource name: {execution!r}"
        )

    schema_compute_class = _COMPUTE_CLASS_ALIAS.get(compute_class)
    if schema_compute_class is None:
        raise SolverDispatchError(
            f"compute_class {compute_class!r} not recognized; allowed: "
            f"{sorted(_COMPUTE_CLASS_ALIAS)}"
        )
    handle = ExecutionHandle(
        handle_id=new_ulid(),
        run_id=run_id,
        solver=solver,
        compute_class=schema_compute_class,  # type: ignore[arg-type]
        workflows_execution_id=workflows_execution_id,
        workflow_name=workflow_name,
        workflow_location=location,
        submitted_at=submitted_at,
    )
    logger.info(
        "run_solver submitted handle_id=%s workflows_execution_id=%s",
        handle.handle_id,
        handle.workflows_execution_id,
    )
    return handle


# --------------------------------------------------------------------------- #
# wait_for_completion
# --------------------------------------------------------------------------- #


_WAIT_FOR_COMPLETION_METADATA = AtomicToolMetadata(
    name="wait_for_completion",
    ttl_class="live-no-cache",
    source_class="solver_dispatch",
    cacheable=False,
)


def _progress_percent(handle_submitted_at: datetime, now: datetime) -> int:
    """Compute the wall-clock-linear progress estimate clamped to
    ``PROGRESS_CLAMP_MAX`` while the Workflow is still running.

    Invariant 1 (Determinism boundary): this is wall-clock arithmetic, not
    an LLM estimate. The ramp is intentionally simple and conservative —
    a real per-step progress signal would require teaching the SFINCS
    entrypoint to write running progress to ``progress.json`` between
    timesteps, which is a follow-up job (OQ-41-PROGRESS-CURVE).
    """
    elapsed = max(0.0, (now - handle_submitted_at).total_seconds())
    raw = (elapsed / NFR_P_4_TARGET_SECONDS) * 100.0
    capped = min(PROGRESS_CLAMP_MAX, max(0, int(raw)))
    return capped


async def _read_completion_manifest(run_id: str) -> dict[str, Any]:
    """Read ``gs://<runs_bucket>/<run_id>/completion.json`` and parse to dict.

    Raises ``SolverDispatchError`` on any read failure (bucket missing, blob
    absent, JSON parse fail) so the caller surfaces a typed error rather
    than masking it as a generic exception. Blob reads run on the default
    executor so the asyncio event loop is not blocked.
    """
    bucket = _get_runs_bucket()
    blob_path = f"{run_id}/completion.json"
    client = _get_storage_client()
    try:
        loop = asyncio.get_running_loop()
        # blob.download_as_bytes is blocking I/O; defer to executor.

        def _read() -> bytes:
            b = client.bucket(bucket)
            blob_obj = b.blob(blob_path)
            return blob_obj.download_as_bytes()

        data_bytes = await loop.run_in_executor(None, _read)
    except SolverDispatchError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise SolverDispatchError(
            f"completion manifest read failed gs://{bucket}/{blob_path}: {exc}"
        ) from exc

    try:
        manifest = json.loads(data_bytes)
    except Exception as exc:  # noqa: BLE001
        raise SolverDispatchError(
            f"completion manifest gs://{bucket}/{blob_path} is not valid JSON: {exc}"
        ) from exc
    if not isinstance(manifest, dict):
        raise SolverDispatchError(
            f"completion manifest gs://{bucket}/{blob_path} is not a JSON object"
        )
    return manifest


def _state_name(execution: Any) -> str:
    """Return the execution.State as a string identifier irrespective of how
    the underlying proto enum repr's. Robust to dict-shaped mocks too.

    Cloud Workflows ``Execution.State`` values: ``STATE_UNSPECIFIED``,
    ``ACTIVE``, ``SUCCEEDED``, ``FAILED``, ``CANCELLED``, ``UNAVAILABLE``,
    ``QUEUED``.
    """
    state = getattr(execution, "state", None)
    if state is None and isinstance(execution, dict):
        state = execution.get("state")
    if state is None:
        return "STATE_UNSPECIFIED"
    name = getattr(state, "name", None)
    if isinstance(name, str):
        return name
    return str(state)


async def _emit_progress(progress_percent: int) -> None:
    """Push a progress update to the active emitter binding (if any)."""
    binding = _EMITTER_BINDING
    if binding is None:
        return
    try:
        await binding.emitter.update_progress(binding.step_id, progress_percent)
    except Exception as exc:  # noqa: BLE001 — emission must never fail the poll
        logger.warning("emitter.update_progress raised: %s", exc)


async def _cancel_workflow_execution(name: str) -> None:
    """Best-effort ``workflows.executions.cancel(name)`` call. Logs and
    swallows exceptions; the underlying ``CancelledError`` propagates from
    the caller regardless."""
    client = _get_workflows_client()
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, lambda: client.cancel_execution(name=name))
        logger.info("cancel_execution issued for %s", name)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "cancel_execution(%s) raised %s; cancel chain still propagates locally",
            name,
            exc,
        )


@register_tool(_WAIT_FOR_COMPLETION_METADATA)
async def wait_for_completion(
    handle: ExecutionHandle,
    poll_interval_s: int = DEFAULT_POLL_INTERVAL_S,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> RunResult:
    """Poll the Cloud Workflows execution backing ``handle`` until terminal.

    Use this when: the agent has an ``ExecutionHandle`` from ``run_solver``
    and needs the ``RunResult`` (and the ``output_uri``) before continuing
    the pipeline. The tool blocks while the solver runs but is cancellable
    via the WS ``cancel`` chain (Invariant 8 — see module docstring).

    Do NOT use this for: starting a new run (use ``run_solver``); polling
    a generic Cloud Workflow execution unrelated to a solver (this tool
    expects the run-bucket completion manifest schema landed by job-0040);
    short, synchronous tool calls (atomic tools are sub-second; this is the
    solver-class blocking pattern).

    Params:
        handle: the ``ExecutionHandle`` returned by ``run_solver``. The
            ``workflows_execution_id`` field is the Cloud Workflows
            resource name we ``get_execution`` / ``cancel_execution`` on.
        poll_interval_s: seconds between ``get_execution`` polls. Default
            10s — matches NFR-P-4 ≤15-min budget granularity (≥9 polls per
            run). Surfaced as OQ-41-POLL-INTERVAL.
        timeout_s: hard ceiling. Defaults to 1800 s (30 min — mirrors the
            Cloud Run Job ``task_timeout`` job-0040 set; gives 2× headroom
            over NFR-P-4). On timeout the tool returns
            ``RunResult{status="failed", error_code="SOLVER_TIMEOUT"}``
            and best-effort cancels the workflow execution.

    Returns:
        ``RunResult{run_id, handle_id, status, output_uri?, started_at,
        completed_at, duration_seconds, error_code?, error_message?,
        cancellation_reason?}`` — terminal outcome. ``status="complete"``
        carries the ``output_uri`` parsed from ``completion.json``;
        ``"failed"`` carries the error code/message; ``"cancelled"``
        carries a ``cancellation_reason``.

    FR-DC-6: This tool is uncacheable-by-construction. The cache shim is
    NOT invoked.

    Invariant 8 (cancellation): when the M1 WS cancel chain raises
    ``asyncio.CancelledError`` inside this coroutine's poll-sleep, we call
    ``workflows.executions.cancel(name)`` *before* re-raising so the
    cloud-side cancellation is initiated within ≤30 s per NFR-R-3.
    """
    if poll_interval_s < 0:
        raise SolverDispatchError(
            f"poll_interval_s must be non-negative; got {poll_interval_s!r}"
        )
    if timeout_s <= 0:
        raise SolverDispatchError(
            f"timeout_s must be positive; got {timeout_s!r}"
        )

    client = _get_workflows_client()
    name = handle.workflows_execution_id
    deadline = handle.submitted_at.timestamp() + float(timeout_s)
    loop = asyncio.get_running_loop()

    logger.info(
        "wait_for_completion handle_id=%s name=%s poll_interval=%ds timeout=%ds",
        handle.handle_id,
        name,
        poll_interval_s,
        timeout_s,
    )

    last_state: str = "STATE_UNSPECIFIED"
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_message: str | None = None
    cancellation_reason: str | None = None

    try:
        while True:
            # --- Poll the execution.
            try:
                execution = await loop.run_in_executor(
                    None, lambda: client.get_execution(name=name)
                )
            except Exception as exc:  # noqa: BLE001
                # Transient errors (network blip) shouldn't immediately fail —
                # but a malformed name / missing resource will keep failing.
                # We treat a single failure as a poll-cycle warning and let
                # the timeout catch a persistent fault.
                logger.warning(
                    "get_execution(%s) raised %s; will retry next poll",
                    name,
                    exc,
                )
                execution = None

            now = datetime.now(timezone.utc)

            if execution is not None:
                last_state = _state_name(execution)
                # Cloud Workflows populates start_time / end_time on its
                # execution model; preserve them if present.
                start_time = getattr(execution, "start_time", None)
                end_time = getattr(execution, "end_time", None)
                if start_time is not None and started_at is None:
                    started_at = _to_utc(start_time)
                if end_time is not None and completed_at is None:
                    completed_at = _to_utc(end_time)

            # --- Emit progress (clamped to ≤95% until SUCCEEDED).
            if last_state == "SUCCEEDED":
                pct = PROGRESS_TERMINAL
            else:
                pct = _progress_percent(handle.submitted_at, now)
            await _emit_progress(pct)

            # --- Terminal-state branches.
            if last_state == "SUCCEEDED":
                # Read the completion manifest and build the RunResult.
                manifest = await _read_completion_manifest(handle.run_id)
                return _build_run_result_from_completion(
                    handle=handle,
                    manifest=manifest,
                    started_at=started_at,
                    completed_at=completed_at or now,
                )
            if last_state == "FAILED":
                error_message = _extract_error_message(execution)
                # On Workflow failure the entrypoint may still have written
                # a completion.json with a structured error. Attempt to read
                # it; absence is non-fatal here (the Workflow's own error
                # surfaces).
                manifest = await _try_read_completion(handle.run_id)
                return _build_failed_result(
                    handle=handle,
                    manifest=manifest,
                    workflow_error_message=error_message,
                    started_at=started_at,
                    completed_at=completed_at or now,
                )
            if last_state == "CANCELLED":
                cancellation_reason = (
                    _extract_error_message(execution)
                    or "Cloud Workflows execution cancelled"
                )
                return RunResult(
                    run_id=handle.run_id,
                    handle_id=handle.handle_id,
                    status="cancelled",
                    output_uri=None,
                    started_at=started_at,
                    completed_at=completed_at or now,
                    duration_seconds=_duration(started_at, completed_at or now),
                    cancellation_reason=cancellation_reason,
                )

            # --- Timeout check.
            if now.timestamp() >= deadline:
                logger.warning(
                    "wait_for_completion timed out handle_id=%s after %ds; "
                    "cancelling workflow execution",
                    handle.handle_id,
                    timeout_s,
                )
                await _cancel_workflow_execution(name)
                return RunResult(
                    run_id=handle.run_id,
                    handle_id=handle.handle_id,
                    status="failed",
                    output_uri=None,
                    started_at=started_at,
                    completed_at=now,
                    duration_seconds=_duration(started_at, now),
                    error_code="SOLVER_TIMEOUT",
                    error_message=(
                        f"wait_for_completion exceeded {timeout_s}s budget "
                        f"while polling {name}"
                    ),
                )

            # --- Sleep until the next poll. Cancellation propagates here.
            await asyncio.sleep(poll_interval_s)

    except asyncio.CancelledError:
        # Invariant 8: best-effort cancel the workflow execution before
        # re-raising so the cloud-side SIGTERM fires within ≤30 s.
        logger.info(
            "wait_for_completion CANCELLED handle_id=%s; "
            "issuing workflows.executions.cancel(%s)",
            handle.handle_id,
            name,
        )
        await _cancel_workflow_execution(name)
        raise


# --------------------------------------------------------------------------- #
# Result-building helpers
# --------------------------------------------------------------------------- #


def _to_utc(value: Any) -> datetime | None:
    """Coerce a value that may be a ``datetime``, a proto Timestamp, or a
    string into a UTC ``datetime``. Returns ``None`` on failure."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    # Proto Timestamp has a ``ToDatetime`` method.
    to_datetime = getattr(value, "ToDatetime", None)
    if callable(to_datetime):
        try:
            dt = to_datetime()
            return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
        except Exception:  # noqa: BLE001
            return None
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt.astimezone(timezone.utc)
        except ValueError:
            return None
    return None


def _duration(started_at: datetime | None, completed_at: datetime) -> float | None:
    if started_at is None:
        return None
    return max(0.0, (completed_at - started_at).total_seconds())


def _extract_error_message(execution: Any) -> str:
    """Pull the error message off an ``Execution`` (or a dict mock).

    Cloud Workflows surfaces failure context in ``execution.error.payload``
    (JSON-encoded) and ``execution.error.context``. We prefer the payload
    when present.
    """
    err = getattr(execution, "error", None)
    if err is None and isinstance(execution, dict):
        err = execution.get("error")
    if err is None:
        return ""
    payload = getattr(err, "payload", None)
    if payload is None and isinstance(err, dict):
        payload = err.get("payload")
    context = getattr(err, "context", None)
    if context is None and isinstance(err, dict):
        context = err.get("context")
    if payload:
        return str(payload)
    if context:
        return str(context)
    return str(err)


async def _try_read_completion(run_id: str) -> dict[str, Any] | None:
    """Like ``_read_completion_manifest`` but returns ``None`` on failure."""
    try:
        return await _read_completion_manifest(run_id)
    except SolverDispatchError as exc:
        logger.info(
            "no completion.json available for run_id=%s (%s); "
            "workflow-side error will surface alone",
            run_id,
            exc,
        )
        return None


def _build_run_result_from_completion(
    handle: ExecutionHandle,
    manifest: dict[str, Any],
    started_at: datetime | None,
    completed_at: datetime,
) -> RunResult:
    """Build a ``RunResult`` from a parsed completion manifest.

    The job-0040 entrypoint emits a manifest like::

        {
          "run_id": "smoke-...",
          "status": "ok" | "error",
          "exit_code": int,
          "sfincs_stdout_uri": "gs://...",
          "sfincs_stderr_uri": "gs://...",
          "output_uris": ["gs://..."],
          "started_at": "ISO-Z",
          "finished_at": "ISO-Z",
          "error": "..."
        }

    We map ``status="ok"`` → ``RunResult.status="complete"`` and ``"error"``
    → ``"failed"``. The first ``output_uri`` in ``output_uris`` populates
    ``RunResult.output_uri`` (FR-CE-4 single canonical raw-output gs:// URI);
    additional outputs are post-processed by ``postprocess_flood`` (engine,
    follow-up).
    """
    manifest_status = str(manifest.get("status", "")).lower()
    output_uris = manifest.get("output_uris") or []
    output_uri: str | None = (
        str(output_uris[0]) if isinstance(output_uris, list) and output_uris else None
    )
    started_at_eff = _to_utc(manifest.get("started_at")) or started_at
    completed_at_eff = _to_utc(manifest.get("finished_at")) or completed_at

    if manifest_status == "ok":
        return RunResult(
            run_id=handle.run_id,
            handle_id=handle.handle_id,
            status="complete",
            output_uri=output_uri,
            started_at=started_at_eff,
            completed_at=completed_at_eff,
            duration_seconds=_duration(started_at_eff, completed_at_eff),
        )

    error_message = str(manifest.get("error") or "solver reported failure")
    return RunResult(
        run_id=handle.run_id,
        handle_id=handle.handle_id,
        status="failed",
        output_uri=output_uri,
        started_at=started_at_eff,
        completed_at=completed_at_eff,
        duration_seconds=_duration(started_at_eff, completed_at_eff),
        error_code=_solver_error_code(manifest),
        error_message=error_message,
    )


def _build_failed_result(
    handle: ExecutionHandle,
    manifest: dict[str, Any] | None,
    workflow_error_message: str,
    started_at: datetime | None,
    completed_at: datetime,
) -> RunResult:
    """Build a ``RunResult{status="failed"}`` when the Workflow itself reports
    FAILED. If a completion.json is present we prefer its structured error;
    otherwise we surface the Workflow's own error message."""
    if manifest is not None:
        result_from_completion = _build_run_result_from_completion(
            handle, manifest, started_at, completed_at
        )
        # The completion may say "ok" even if the workflow reports FAILED
        # (rare: race between Workflow read_completion and Job exit). Honor
        # the WORKFLOW's FAILED verdict and downgrade.
        if result_from_completion.status == "complete":
            return RunResult(
                run_id=handle.run_id,
                handle_id=handle.handle_id,
                status="failed",
                output_uri=result_from_completion.output_uri,
                started_at=result_from_completion.started_at,
                completed_at=result_from_completion.completed_at,
                duration_seconds=result_from_completion.duration_seconds,
                error_code="SOLVER_DISPATCH_FAILED",
                error_message=workflow_error_message
                or "Cloud Workflows execution reported FAILED",
            )
        return result_from_completion

    return RunResult(
        run_id=handle.run_id,
        handle_id=handle.handle_id,
        status="failed",
        output_uri=None,
        started_at=started_at,
        completed_at=completed_at,
        duration_seconds=_duration(started_at, completed_at),
        error_code="SOLVER_DISPATCH_FAILED",
        error_message=workflow_error_message
        or "Cloud Workflows execution reported FAILED",
    )


def _solver_error_code(manifest: dict[str, Any]) -> str:
    """Map a completion-manifest error to an open-set A.6 SCREAMING_SNAKE_CASE
    error code. Keep narrow; the catch-all bucket is ``SOLVER_FAILED``.

    Surfaced as OQ-41-ERROR-CODE-REGISTRY — when sprint-08 lands more
    solver-specific failure modes (SFINCS_MASS_BALANCE_DIVERGED,
    MODEL_DECK_INVALID, etc.) the registry expands here.
    """
    exit_code = manifest.get("exit_code")
    if exit_code is not None and exit_code != 0:
        # Surface the most common known SFINCS exit shapes once we observe
        # them in real runs; for now we surface a generic code carrying the
        # exit code in the message.
        return "SOLVER_FAILED"
    return "SOLVER_FAILED"
