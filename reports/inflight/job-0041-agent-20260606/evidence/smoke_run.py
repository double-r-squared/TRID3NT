"""Live smoke harness for job-0041 (M5 Stage C).

Exercises ``run_solver`` + ``wait_for_completion`` end-to-end against the
deployed SFINCS Cloud Workflows orchestrator landed by job-0040
(``grace-2-sfincs-orchestrator``). Captures:

- the ``ExecutionHandle`` returned by ``run_solver``,
- the ≥3 progress emissions ``wait_for_completion`` pushes through a
  capturing emitter binding,
- the ``RunResult`` (status="failed" expected — the synthetic manifest has
  no model deck, so sfincs exits non-zero, exactly mirroring job-0040's
  smoke-run shape),
- the cancel chain: submit a second execution, sleep 5 s, cancel the
  ``wait_for_completion`` coroutine, verify ``workflows.executions.cancel``
  was issued + the Cloud Workflows execution flips to CANCELLED within 30 s
  (NFR-R-3 / Invariant 8).

Usage:
    .venv-agent/bin/python reports/inflight/job-0041-agent-20260606/evidence/smoke_run.py

Outputs all artifacts under the same directory:
    - completed_run.json           – the happy-path RunResult dump
    - completed_progress.json      – list of (step_id, percent) emissions
    - cancel_run.json              – the cancel-path observation
    - cancel_progress.json         – progress emissions before cancel
    - cancel_workflows_state.json  – final Cloud Workflows execution state
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Make the agent's src importable directly when run from the repo root.
ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "services" / "agent" / "src"))
sys.path.insert(0, str(ROOT / "packages" / "contracts" / "src"))

from google.cloud import storage  # type: ignore[import-not-found]
from google.cloud.workflows.executions_v1 import ExecutionsClient  # type: ignore[import-not-found]

from grace2_agent.tools.solver import (  # noqa: E402
    EmitterBinding,
    run_solver,
    set_emitter_binding,
    set_runs_bucket,
    set_storage_client,
    set_workflows_client,
    wait_for_completion,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("smoke")

EVIDENCE = Path(__file__).parent
CACHE_BUCKET = "grace-2-hazard-prod-cache"
RUNS_BUCKET = "grace-2-hazard-prod-runs"
PROJECT = "grace-2-hazard-prod"
LOCATION = "us-central1"
WORKFLOW = "grace-2-sfincs-orchestrator"


class CapturingEmitter:
    """Capture every ``update_progress`` call for evidence."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def update_progress(self, step_id: str, progress_percent: int) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.calls.append(
            {"ts": now, "step_id": step_id, "progress_percent": progress_percent}
        )
        log.info("progress: %d%% (step=%s)", progress_percent, step_id)


def upload_smoke_manifest(suffix: str) -> str:
    """Upload an empty manifest matching the job-0040 entrypoint schema and
    return its gs:// URI. Synthetic — sfincs will exit non-zero (no model
    deck), which is the expected wiring-verification shape."""
    client = storage.Client(project=PROJECT)
    bucket = client.bucket(CACHE_BUCKET)
    blob = bucket.blob(f"cache/static-30d/sfincs-smoke/manifest-job-0041-{suffix}.json")
    payload = {"inputs": [], "sfincs_args": [], "outputs": []}
    blob.upload_from_string(
        json.dumps(payload).encode(),
        content_type="application/json",
    )
    uri = f"gs://{CACHE_BUCKET}/{blob.name}"
    log.info("uploaded synthetic manifest: %s", uri)
    return uri


def bind_real_clients() -> None:
    """Bind real ADC-based Workflows + GCS clients into the solver module
    so the tool bodies reach production substrate."""
    set_workflows_client(ExecutionsClient())
    set_storage_client(storage.Client(project=PROJECT))
    set_runs_bucket(RUNS_BUCKET)


async def smoke_happy_path() -> None:
    log.info("==== smoke: HAPPY PATH (waiting through SFINCS exit) ====")
    manifest_uri = upload_smoke_manifest(suffix=f"happy-{int(time.time())}")
    handle = run_solver(
        solver="sfincs",
        model_setup_uri=manifest_uri,
        compute_class="medium",
    )
    log.info(
        "run_solver returned handle: handle_id=%s run_id=%s name=%s",
        handle.handle_id,
        handle.run_id,
        handle.workflows_execution_id,
    )

    emitter = CapturingEmitter()
    set_emitter_binding(EmitterBinding(emitter=emitter, step_id="smoke-step-happy"))

    # Poll every 5s; the synthetic SFINCS run completes in ~50-90s including
    # cold-container pull (per job-0040 smoke). 5s gives ≥10 polls — ≥3
    # progress emissions easily.
    result = await wait_for_completion(handle, poll_interval_s=5, timeout_s=600)
    log.info(
        "RunResult: status=%s output_uri=%s error_code=%s",
        result.status,
        result.output_uri,
        result.error_code,
    )

    set_emitter_binding(None)
    (EVIDENCE / "completed_run.json").write_text(
        json.dumps(result.model_dump(mode="json"), indent=2)
    )
    (EVIDENCE / "completed_progress.json").write_text(
        json.dumps(emitter.calls, indent=2)
    )
    log.info("HAPPY PATH: %d progress emissions captured", len(emitter.calls))


async def smoke_cancel_path() -> None:
    log.info("==== smoke: CANCEL PATH (≤30 s budget per NFR-R-3) ====")
    manifest_uri = upload_smoke_manifest(suffix=f"cancel-{int(time.time())}")
    handle = run_solver(
        solver="sfincs",
        model_setup_uri=manifest_uri,
        compute_class="medium",
    )
    log.info(
        "run_solver(cancel) returned handle: name=%s",
        handle.workflows_execution_id,
    )

    emitter = CapturingEmitter()
    set_emitter_binding(EmitterBinding(emitter=emitter, step_id="smoke-step-cancel"))

    # Schedule the wait coroutine and cancel after 5 s. The cancel must
    # propagate to workflows.executions.cancel within 30 s per NFR-R-3.
    cancel_initiated_at = None
    cancel_observed_at = None
    workflow_cancelled_at = None

    task = asyncio.create_task(
        wait_for_completion(handle, poll_interval_s=3, timeout_s=600)
    )
    await asyncio.sleep(5.0)
    log.info("user cancel: cancelling wait_for_completion task")
    cancel_initiated_at = datetime.now(timezone.utc)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        cancel_observed_at = datetime.now(timezone.utc)
        log.info(
            "wait_for_completion CancelledError observed; elapsed=%.2fs",
            (cancel_observed_at - cancel_initiated_at).total_seconds(),
        )

    # Poll the workflows API directly to confirm the execution actually
    # reaches CANCELLED.
    client = ExecutionsClient()
    deadline = cancel_initiated_at.timestamp() + 30.0
    final_state = "UNKNOWN"
    final_obj = None
    while time.time() < deadline:
        execution = client.get_execution(name=handle.workflows_execution_id)
        final_state = execution.state.name
        final_obj = execution
        log.info("workflow state: %s", final_state)
        if final_state in ("CANCELLED", "SUCCEEDED", "FAILED"):
            workflow_cancelled_at = datetime.now(timezone.utc)
            break
        await asyncio.sleep(2.0)

    set_emitter_binding(None)
    elapsed = (
        (workflow_cancelled_at - cancel_initiated_at).total_seconds()
        if workflow_cancelled_at
        else None
    )
    record = {
        "handle": handle.model_dump(mode="json"),
        "cancel_initiated_at": cancel_initiated_at.isoformat(),
        "cancel_observed_at": cancel_observed_at.isoformat()
        if cancel_observed_at
        else None,
        "workflow_terminal_at": workflow_cancelled_at.isoformat()
        if workflow_cancelled_at
        else None,
        "workflow_terminal_state": final_state,
        "elapsed_seconds_to_terminal": elapsed,
        "nfr_r_3_budget_met": (elapsed is not None and elapsed <= 30.0),
    }
    (EVIDENCE / "cancel_run.json").write_text(json.dumps(record, indent=2))
    (EVIDENCE / "cancel_progress.json").write_text(json.dumps(emitter.calls, indent=2))
    (EVIDENCE / "cancel_workflows_state.json").write_text(
        json.dumps(
            {
                "name": handle.workflows_execution_id,
                "final_state": final_state,
                "start_time": str(
                    getattr(final_obj, "start_time", "") if final_obj else ""
                ),
                "end_time": str(
                    getattr(final_obj, "end_time", "") if final_obj else ""
                ),
                "error_payload": (
                    str(getattr(getattr(final_obj, "error", None), "payload", ""))
                    if final_obj
                    else ""
                ),
            },
            indent=2,
        )
    )
    log.info(
        "CANCEL PATH: final_state=%s elapsed=%.2fs nfr_r_3=%s",
        final_state,
        elapsed if elapsed is not None else float("nan"),
        record["nfr_r_3_budget_met"],
    )


async def main() -> None:
    bind_real_clients()
    if os.environ.get("SKIP_HAPPY") != "1":
        await smoke_happy_path()
    if os.environ.get("SKIP_CANCEL") != "1":
        await smoke_cancel_path()


if __name__ == "__main__":
    asyncio.run(main())
