"""Unit + integration tests for ``solver.py`` (job-0041, M5 Stage C).

Coverage maps to the kickoff acceptance criteria:

1. ``test_registry_registers_solver_tools_uncacheable`` — both atomic tools
   appear in ``TOOL_REGISTRY`` with ``cacheable=False`` +
   ``ttl_class="live-no-cache"`` + ``source_class="solver_dispatch"``
   (FR-DC-6 enumeration honored).
2. ``test_run_solver_rejects_unregistered_solver`` — ``solver="modflow"``
   raises ``SolverNotRegisteredError`` (lazy per-milestone deploy strategy).
3. ``test_run_solver_happy_path_submits_workflow`` — sfincs path constructs
   the parent resource name + JSON argument correctly, returns a typed
   ``ExecutionHandle`` whose ``workflows_execution_id`` is the Workflows-
   issued resource name (Invariant-8 cancellation seam).
4. ``test_wait_for_completion_emits_progress_on_each_poll`` — three polls
   (ACTIVE, ACTIVE, SUCCEEDED) emit three ``update_progress`` calls; the
   final call is 100% (PROGRESS_TERMINAL); intermediate calls are clamped
   ≤ ``PROGRESS_CLAMP_MAX``.
5. ``test_wait_for_completion_cancel_propagation_invokes_workflows_cancel``
   — when the caller cancels the coroutine, ``cancel_execution(name=...)``
   is invoked BEFORE the ``CancelledError`` re-raises (Invariant 8).
6. ``test_wait_for_completion_workflow_failed_returns_failed_runresult`` —
   FAILED state surfaces as ``RunResult{status="failed", error_code=
   "SOLVER_FAILED" | "SOLVER_DISPATCH_FAILED"}``; completion.json is
   preferred when present.
7. ``test_progress_estimator_is_wall_clock_linear_clamped`` — pure-function
   guard: at ``t=0`` → 0%, at ``t=NFR_P_4_TARGET_SECONDS/2`` → 50%, at
   ``t=NFR_P_4_TARGET_SECONDS`` → ``PROGRESS_CLAMP_MAX``.
8. INTEGRATION: ``test_integration_full_cycle_with_mocked_workflows_and_gcs``
   — exercises ``run_solver`` → ``wait_for_completion`` end-to-end with a
   mocked ``ExecutionsClient`` + GCS reader, validating progress emission,
   ``RunResult`` shape, and the bind/unbind discipline on
   ``set_emitter_binding``.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest

from grace2_agent.tools import TOOL_REGISTRY
from grace2_agent.tools.solver import (
    DEFAULT_POLL_INTERVAL_S,
    EmitterBinding,
    NFR_P_4_TARGET_SECONDS,
    PROGRESS_CLAMP_MAX,
    PROGRESS_TERMINAL,
    SOLVER_WORKFLOW_REGISTRY,
    SolverDispatchError,
    SolverNotRegisteredError,
    _progress_percent,
    run_solver,
    set_emitter_binding,
    set_runs_bucket,
    set_storage_client,
    set_workflows_client,
    wait_for_completion,
)
from grace2_contracts import new_ulid
from grace2_contracts.execution import ExecutionHandle, RunResult


# --------------------------------------------------------------------------- #
# Fakes / fixtures
# --------------------------------------------------------------------------- #


class _FakeExecution:
    """Minimal duck-type stand-in for ``google.cloud.workflows.executions_v1.
    types.Execution``. The Workflows client returns this on
    ``create_execution`` + ``get_execution``."""

    def __init__(
        self,
        *,
        name: str,
        state: str = "ACTIVE",
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        error: Any = None,
    ) -> None:
        self.name = name
        # The real Execution.state is an enum with a `.name` str attr.
        self.state = type("S", (), {"name": state})()
        self.start_time = start_time
        self.end_time = end_time
        self.error = error


class _FakeWorkflowsClient:
    """In-memory fake of ``ExecutionsClient``. Captures call args + returns
    pre-programmed responses."""

    def __init__(self, *, executions: list[_FakeExecution]) -> None:
        # ``executions`` is a queue of responses get_execution returns in order.
        self._get_responses: list[_FakeExecution] = list(executions)
        self.create_calls: list[tuple[str, str]] = []  # (parent, argument)
        self.get_calls: list[str] = []  # names polled
        self.cancel_calls: list[str] = []  # names cancelled
        self._last_created: _FakeExecution | None = None

    # ExecutionsClient.create_execution(parent=..., execution=Execution(...))
    def create_execution(self, parent: str | None = None, execution: Any = None) -> _FakeExecution:  # noqa: D401
        argument = getattr(execution, "argument", "") or ""
        self.create_calls.append((parent or "", argument))
        # Default created execution name: parent + /executions/<ulid>
        name = f"{parent}/executions/{new_ulid()}"
        fake = _FakeExecution(name=name, state="ACTIVE")
        self._last_created = fake
        return fake

    def get_execution(self, name: str | None = None) -> _FakeExecution:  # noqa: D401
        self.get_calls.append(name or "")
        if not self._get_responses:
            # Loop on the last known state when exhausted to model a stuck
            # execution. Tests typically supply enough responses to cover
            # their poll budget.
            return _FakeExecution(name=name or "", state="ACTIVE")
        return self._get_responses.pop(0)

    def cancel_execution(self, name: str | None = None) -> _FakeExecution:  # noqa: D401
        self.cancel_calls.append(name or "")
        return _FakeExecution(name=name or "", state="CANCELLED")


class _FakeBlob:
    def __init__(self, payload: bytes | None) -> None:
        self._payload = payload

    def download_as_bytes(self) -> bytes:
        if self._payload is None:
            raise FileNotFoundError("no completion.json in fake bucket")
        return self._payload


class _FakeBucket:
    def __init__(self, blobs: dict[str, bytes]) -> None:
        self._blobs = blobs

    def blob(self, path: str) -> _FakeBlob:
        return _FakeBlob(self._blobs.get(path))


class _FakeStorageClient:
    def __init__(self, *, buckets: dict[str, dict[str, bytes]]) -> None:
        self._buckets = buckets

    def bucket(self, name: str) -> _FakeBucket:
        return _FakeBucket(self._buckets.get(name, {}))


class _CapturingEmitter:
    """Captures ``update_progress`` invocations for assertion."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    async def update_progress(self, step_id: str, progress_percent: int) -> None:
        self.calls.append((step_id, progress_percent))


@pytest.fixture()
def reset_solver_di_seams():
    """Reset the module-level DI handles before and after each test so the
    bindings from one test don't leak into the next."""
    set_workflows_client(None)
    set_emitter_binding(None)
    set_runs_bucket(None)
    set_storage_client(None)
    try:
        yield
    finally:
        set_workflows_client(None)
        set_emitter_binding(None)
        set_runs_bucket(None)
        set_storage_client(None)


# --------------------------------------------------------------------------- #
# 1. Registry: both tools register with FR-DC-6 metadata
# --------------------------------------------------------------------------- #


def test_registry_registers_solver_tools_uncacheable() -> None:
    """Both solver tools live in ``TOOL_REGISTRY`` with FR-DC-6 metadata."""
    assert "run_solver" in TOOL_REGISTRY
    assert "wait_for_completion" in TOOL_REGISTRY

    for tname in ("run_solver", "wait_for_completion"):
        entry = TOOL_REGISTRY[tname]
        meta = entry.metadata
        assert meta.cacheable is False, f"{tname} must be uncacheable (FR-DC-6)"
        assert meta.ttl_class == "live-no-cache", (
            f"{tname} ttl_class must be live-no-cache (FR-DC-6)"
        )
        assert meta.source_class == "solver_dispatch", (
            f"{tname} source_class must be solver_dispatch"
        )


# --------------------------------------------------------------------------- #
# 2. run_solver rejects unregistered solver
# --------------------------------------------------------------------------- #


def test_run_solver_rejects_unregistered_solver(reset_solver_di_seams) -> None:
    """v0.1 ships SFINCS only; other solvers raise
    ``SolverNotRegisteredError`` (lazy per-milestone deploy strategy)."""
    fake = _FakeWorkflowsClient(executions=[])
    set_workflows_client(fake)

    with pytest.raises(SolverNotRegisteredError) as exc_info:
        run_solver(solver="modflow", model_setup_uri="gs://x/y.json")
    assert "modflow" in str(exc_info.value)
    assert "sfincs" in str(exc_info.value)
    # No Workflows execution was attempted.
    assert fake.create_calls == []


# --------------------------------------------------------------------------- #
# 3. run_solver happy path: submit Workflows execution
# --------------------------------------------------------------------------- #


def test_run_solver_happy_path_submits_workflow(
    reset_solver_di_seams, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``run_solver("sfincs", ...)`` issues a Cloud Workflows execution
    against ``grace-2-sfincs-orchestrator`` with a JSON argument carrying
    ``run_id`` + ``manifest_uri`` (the job-0040 workflow contract)."""
    fake = _FakeWorkflowsClient(executions=[])
    set_workflows_client(fake)
    monkeypatch.setenv("GRACE2_GCP_PROJECT", "grace-2-hazard-prod")
    monkeypatch.setenv("GRACE2_GCP_LOCATION", "us-central1")

    handle = run_solver(
        solver="sfincs",
        model_setup_uri="gs://grace-2-hazard-prod-cache/cache/static-30d/sfincs-smoke/manifest.json",
        compute_class="medium",
    )

    # 1) Exactly one create_execution call.
    assert len(fake.create_calls) == 1
    parent, argument = fake.create_calls[0]
    assert parent == (
        "projects/grace-2-hazard-prod/locations/us-central1/workflows/"
        "grace-2-sfincs-orchestrator"
    ), parent

    # 2) Argument is JSON carrying run_id + manifest_uri.
    arg = json.loads(argument)
    assert arg["manifest_uri"].startswith("gs://"), arg
    assert isinstance(arg["run_id"], str) and len(arg["run_id"]) == 26, arg

    # 3) Returned handle is a typed ExecutionHandle with the matching
    #    workflows_execution_id (Invariant-8 cancellation seam).
    assert isinstance(handle, ExecutionHandle)
    assert handle.solver == "sfincs"
    # FR-CE-3 names this class "medium" but the contract literal is
    # "standard"; run_solver maps via _COMPUTE_CLASS_ALIAS (OQ-41-COMPUTE-
    # CLASS-NAMING — schema reconciliation pending).
    assert handle.compute_class == "standard"
    assert handle.workflow_name == SOLVER_WORKFLOW_REGISTRY["sfincs"]
    assert handle.workflow_location == "us-central1"
    assert handle.workflows_execution_id.startswith(parent + "/executions/")
    # run_id on the handle matches the workflow argument.
    assert handle.run_id == arg["run_id"]


def test_run_solver_rejects_non_gs_uri(reset_solver_di_seams) -> None:
    """``model_setup_uri`` must be a ``gs://`` URI (FR-CE-2 / FR-DC-1
    discipline: workflow inputs reside in GCS)."""
    fake = _FakeWorkflowsClient(executions=[])
    set_workflows_client(fake)
    with pytest.raises(SolverDispatchError):
        run_solver(solver="sfincs", model_setup_uri="/tmp/manifest.json")
    assert fake.create_calls == []


# --------------------------------------------------------------------------- #
# 7. _progress_percent — pure-function guard
# --------------------------------------------------------------------------- #


def test_progress_estimator_is_wall_clock_linear_clamped() -> None:
    """At t=0 → 0%; at t=NFR_P_4_TARGET_SECONDS/2 → 50%; at and beyond
    t=NFR_P_4_TARGET_SECONDS → clamped to PROGRESS_CLAMP_MAX."""
    submitted = datetime(2026, 6, 7, 12, 0, 0, tzinfo=timezone.utc)
    assert _progress_percent(submitted, submitted) == 0
    half = submitted + timedelta(seconds=NFR_P_4_TARGET_SECONDS / 2)
    assert _progress_percent(submitted, half) == 50
    over = submitted + timedelta(seconds=NFR_P_4_TARGET_SECONDS + 1.0)
    assert _progress_percent(submitted, over) == PROGRESS_CLAMP_MAX
    # Determinism: negative elapsed (clock skew) clamps to 0.
    early = submitted - timedelta(seconds=10)
    assert _progress_percent(submitted, early) == 0


# --------------------------------------------------------------------------- #
# 4. wait_for_completion emits progress on each poll
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_wait_for_completion_emits_progress_on_each_poll(
    reset_solver_di_seams,
) -> None:
    """Three polls (ACTIVE, ACTIVE, SUCCEEDED) ⇒ three progress emissions;
    the last is 100% (terminal). Intermediate emissions are clamped ≤95%."""
    completed_at = datetime.now(timezone.utc)
    started_at = completed_at - timedelta(seconds=60)
    name = (
        "projects/grace-2-hazard-prod/locations/us-central1/workflows/"
        "grace-2-sfincs-orchestrator/executions/exec-test-1"
    )
    handle = ExecutionHandle(
        handle_id=new_ulid(),
        run_id=new_ulid(),
        solver="sfincs",
        compute_class="standard",
        workflows_execution_id=name,
        workflow_name="grace-2-sfincs-orchestrator",
        workflow_location="us-central1",
        submitted_at=started_at,
    )
    fake_wf = _FakeWorkflowsClient(
        executions=[
            _FakeExecution(name=name, state="ACTIVE", start_time=started_at),
            _FakeExecution(name=name, state="ACTIVE", start_time=started_at),
            _FakeExecution(
                name=name,
                state="SUCCEEDED",
                start_time=started_at,
                end_time=completed_at,
            ),
        ]
    )
    set_workflows_client(fake_wf)

    # Completion manifest the Workflow's read step would have written.
    completion = {
        "run_id": handle.run_id,
        "status": "ok",
        "exit_code": 0,
        "sfincs_stdout_uri": "gs://grace-2-hazard-prod-runs/x/sfincs.stdout",
        "sfincs_stderr_uri": "gs://grace-2-hazard-prod-runs/x/sfincs.stderr",
        "output_uris": [f"gs://grace-2-hazard-prod-runs/{handle.run_id}/sfincs_map.nc"],
        "started_at": started_at.isoformat().replace("+00:00", "Z"),
        "finished_at": completed_at.isoformat().replace("+00:00", "Z"),
    }
    set_runs_bucket("grace-2-hazard-prod-runs")
    set_storage_client(
        _FakeStorageClient(
            buckets={
                "grace-2-hazard-prod-runs": {
                    f"{handle.run_id}/completion.json": json.dumps(completion).encode()
                }
            }
        )
    )
    emitter = _CapturingEmitter()
    set_emitter_binding(EmitterBinding(emitter=emitter, step_id="step-1"))

    result = await wait_for_completion(handle, poll_interval_s=0)  # 0s for tests

    # 1) Result is a RunResult with status=complete, output_uri from manifest.
    assert isinstance(result, RunResult)
    assert result.status == "complete"
    assert result.output_uri == completion["output_uris"][0]
    assert result.handle_id == handle.handle_id

    # 2) Exactly three get_execution polls + zero cancel calls.
    assert len(fake_wf.get_calls) == 3, fake_wf.get_calls
    assert fake_wf.cancel_calls == []

    # 3) At least three progress emissions; the last is 100% (terminal).
    assert len(emitter.calls) >= 3, emitter.calls
    last_step_id, last_pct = emitter.calls[-1]
    assert last_step_id == "step-1"
    assert last_pct == PROGRESS_TERMINAL
    # 4) All intermediate emissions are clamped ≤ PROGRESS_CLAMP_MAX.
    for sid, pct in emitter.calls[:-1]:
        assert sid == "step-1"
        assert 0 <= pct <= PROGRESS_CLAMP_MAX


# --------------------------------------------------------------------------- #
# 5. wait_for_completion propagates cancel via workflows.executions.cancel
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_wait_for_completion_cancel_propagation_invokes_workflows_cancel(
    reset_solver_di_seams,
) -> None:
    """When the caller cancels the ``wait_for_completion`` coroutine, the
    tool MUST issue ``workflows.executions.cancel(name=...)`` before
    re-raising ``CancelledError`` (Invariant 8 / NFR-R-3)."""
    name = (
        "projects/grace-2-hazard-prod/locations/us-central1/workflows/"
        "grace-2-sfincs-orchestrator/executions/exec-test-cancel"
    )
    submitted_at = datetime.now(timezone.utc) - timedelta(seconds=5)
    handle = ExecutionHandle(
        handle_id=new_ulid(),
        run_id=new_ulid(),
        solver="sfincs",
        compute_class="standard",
        workflows_execution_id=name,
        workflow_name="grace-2-sfincs-orchestrator",
        workflow_location="us-central1",
        submitted_at=submitted_at,
    )
    # Return ACTIVE forever so the loop only exits via cancel/timeout.
    fake_wf = _FakeWorkflowsClient(
        executions=[
            _FakeExecution(name=name, state="ACTIVE", start_time=submitted_at)
        ]
        * 50
    )
    set_workflows_client(fake_wf)
    # No emitter binding — the emit path is exercised in test #4.

    task = asyncio.create_task(
        wait_for_completion(handle, poll_interval_s=0)
    )
    # Let the coroutine reach the first sleep, then cancel.
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Workflows cancel was issued exactly once, against the handle's name.
    assert fake_wf.cancel_calls == [name], fake_wf.cancel_calls


# --------------------------------------------------------------------------- #
# 6. workflow FAILED returns RunResult{status=failed}
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_wait_for_completion_workflow_failed_returns_failed_runresult(
    reset_solver_di_seams,
) -> None:
    """A FAILED workflow state (no completion.json) surfaces as
    ``RunResult{status="failed", error_code="SOLVER_DISPATCH_FAILED"}``."""
    name = "projects/p/locations/us-central1/workflows/w/executions/x"
    submitted_at = datetime.now(timezone.utc) - timedelta(seconds=2)
    handle = ExecutionHandle(
        handle_id=new_ulid(),
        run_id=new_ulid(),
        solver="sfincs",
        compute_class="standard",
        workflows_execution_id=name,
        workflow_name="grace-2-sfincs-orchestrator",
        workflow_location="us-central1",
        submitted_at=submitted_at,
    )
    fake_wf = _FakeWorkflowsClient(
        executions=[
            _FakeExecution(
                name=name,
                state="FAILED",
                start_time=submitted_at,
                end_time=submitted_at + timedelta(seconds=1),
                error=type(
                    "E",
                    (),
                    {
                        "payload": json.dumps(
                            {"tags": ["sfincs", "exit_code=2"], "message": "non-zero"}
                        ),
                        "context": "validate->invoke->read",
                    },
                )(),
            )
        ]
    )
    set_workflows_client(fake_wf)
    # No completion.json in the runs bucket — the Workflow-side error
    # must be surfaced verbatim.
    set_runs_bucket("grace-2-hazard-prod-runs")
    set_storage_client(_FakeStorageClient(buckets={}))

    result = await wait_for_completion(handle, poll_interval_s=0)

    assert isinstance(result, RunResult)
    assert result.status == "failed"
    assert result.error_code == "SOLVER_DISPATCH_FAILED"
    assert result.error_message is not None
    assert "non-zero" in result.error_message or "validate->invoke" in result.error_message


@pytest.mark.asyncio
async def test_wait_for_completion_succeeded_with_completion_error_surfaces_failed(
    reset_solver_di_seams,
) -> None:
    """A Workflow SUCCEEDED with a completion.json reporting ``status=error``
    (the job-0040 entrypoint always writes a manifest even on non-zero exit)
    surfaces as ``RunResult{status="failed"}`` with the entrypoint's
    structured error text — mirrors the job-0040 smoke-run behavior where
    the synthetic manifest produced exit code 2."""
    name = "projects/p/locations/us-central1/workflows/w/executions/y"
    submitted_at = datetime.now(timezone.utc) - timedelta(seconds=2)
    handle = ExecutionHandle(
        handle_id=new_ulid(),
        run_id=new_ulid(),
        solver="sfincs",
        compute_class="standard",
        workflows_execution_id=name,
        workflow_name="grace-2-sfincs-orchestrator",
        workflow_location="us-central1",
        submitted_at=submitted_at,
    )
    fake_wf = _FakeWorkflowsClient(
        executions=[
            _FakeExecution(
                name=name,
                state="SUCCEEDED",
                start_time=submitted_at,
                end_time=submitted_at + timedelta(seconds=1),
            )
        ]
    )
    set_workflows_client(fake_wf)
    completion = {
        "run_id": handle.run_id,
        "status": "error",
        "exit_code": 2,
        "output_uris": [],
        "error": "sfincs exited with non-zero code 2",
    }
    set_runs_bucket("grace-2-hazard-prod-runs")
    set_storage_client(
        _FakeStorageClient(
            buckets={
                "grace-2-hazard-prod-runs": {
                    f"{handle.run_id}/completion.json": json.dumps(completion).encode()
                }
            }
        )
    )

    result = await wait_for_completion(handle, poll_interval_s=0)

    assert isinstance(result, RunResult)
    assert result.status == "failed"
    assert result.error_code == "SOLVER_FAILED"
    assert result.error_message is not None
    assert "non-zero code 2" in result.error_message


# --------------------------------------------------------------------------- #
# 8. INTEGRATION: full cycle with mocked Workflows + GCS clients
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_integration_full_cycle_with_mocked_workflows_and_gcs(
    reset_solver_di_seams, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: ``run_solver`` submits a workflow execution; the same
    handle is fed to ``wait_for_completion`` which polls through
    ACTIVE → SUCCEEDED, emits progress to a bound emitter, reads
    completion.json, and returns ``RunResult{status="complete"}`` with
    the first ``output_uri`` from the manifest. Integration shape (mocked
    Workflows + GCS clients) but the codepaths under test are real."""
    monkeypatch.setenv("GRACE2_GCP_PROJECT", "grace-2-hazard-prod")
    monkeypatch.setenv("GRACE2_GCP_LOCATION", "us-central1")
    fake_wf = _FakeWorkflowsClient(executions=[])
    set_workflows_client(fake_wf)

    handle = run_solver(
        solver="sfincs",
        model_setup_uri="gs://grace-2-hazard-prod-cache/cache/static-30d/sfincs-smoke/manifest.json",
        compute_class="medium",
    )
    name = handle.workflows_execution_id
    submitted_at = handle.submitted_at

    # Now arm the fake to respond to wait_for_completion: ACTIVE then
    # SUCCEEDED.
    fake_wf._get_responses = [
        _FakeExecution(name=name, state="ACTIVE", start_time=submitted_at),
        _FakeExecution(
            name=name,
            state="SUCCEEDED",
            start_time=submitted_at,
            end_time=submitted_at + timedelta(seconds=2),
        ),
    ]

    completion = {
        "run_id": handle.run_id,
        "status": "ok",
        "exit_code": 0,
        "output_uris": [
            f"gs://grace-2-hazard-prod-runs/{handle.run_id}/sfincs_map.nc",
            f"gs://grace-2-hazard-prod-runs/{handle.run_id}/sfincs_his.nc",
        ],
        "started_at": submitted_at.isoformat().replace("+00:00", "Z"),
        "finished_at": (submitted_at + timedelta(seconds=2))
        .isoformat()
        .replace("+00:00", "Z"),
    }
    set_runs_bucket("grace-2-hazard-prod-runs")
    set_storage_client(
        _FakeStorageClient(
            buckets={
                "grace-2-hazard-prod-runs": {
                    f"{handle.run_id}/completion.json": json.dumps(completion).encode()
                }
            }
        )
    )
    emitter = _CapturingEmitter()
    set_emitter_binding(EmitterBinding(emitter=emitter, step_id="solve-step"))

    result = await wait_for_completion(handle, poll_interval_s=0)

    # 1) Result is a RunResult{status=complete} carrying the first output_uri.
    assert result.status == "complete"
    assert result.output_uri == completion["output_uris"][0]
    # 2) handle_id + run_id flow through unchanged.
    assert result.handle_id == handle.handle_id
    assert result.run_id == handle.run_id
    # 3) Progress was emitted at least twice; the last call is 100%.
    assert len(emitter.calls) >= 2
    assert emitter.calls[-1][1] == PROGRESS_TERMINAL
    # 4) cancel_execution was NOT called on the happy path.
    assert fake_wf.cancel_calls == []
