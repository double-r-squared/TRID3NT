"""Host-side dispatch shim for the Python sandbox Cloud Run Job (job-0232).

``submit_sandbox_job(python_code, layer_refs)`` is the agent-side entry point the
``code_exec_request`` tool (job-0233) calls. It has two modes:

* **Cloud mode (default).** Stages the ``{python_code, layer_refs}`` payload to a
  GCS object, submits a ``grace-2-python-sandbox`` Cloud Run Job execution with
  ``GRACE2_SANDBOX_PAYLOAD_URI`` pointing at it, and returns a
  :class:`SandboxExecutionHandle` (the sandbox analogue of the solver's
  ``ExecutionHandle`` — a pending-result handle whose execution id is the seam the
  agent polls / cancels on). The Cloud Run Job in ``infra/python-sandbox.tf`` runs
  ``infra/python-sandbox/executor.py`` inside the egress-denied container; the
  result envelope lands in the runs/cache bucket (job-0233 wires the readback).

* **Local-subprocess fallback (``GRACE2_SANDBOX_LOCAL=1``).** Runs the SAME
  ``executor.py`` harness in a child ``python`` subprocess on this machine — no
  docker daemon, no gcloud needed. Used for dev + the job-0232 / job-0238 tests.
  The local fallback enforces the SAME 60s wallclock cap (via the executor's
  in-process SIGALRM watchdog) AND an outer subprocess hard-kill at cap+grace, and
  the same output bounds (the executor truncates; the runner caps the JSON it
  parses). ``run_sandbox_local`` returns the parsed result envelope directly
  (synchronous) — there is no Cloud Workflows indirection in local mode.

Why the executor is invoked as a subprocess (not imported) even in local mode
-----------------------------------------------------------------------------
1. The 60s wallclock cap + the in-process net guard MONKEYPATCH process-global
   state (``socket.socket.connect``, proxy env). Running it inline in the agent
   process would poison the agent's own socket stack. A child process is disposed
   after each run — clean isolation.
2. The outer ``subprocess`` hard-kill (``communicate(timeout=...)`` + ``kill()``)
   is a belt-and-suspenders wallclock bound that survives even if user code
   installs its own SIGALRM handler or blocks signals in a C extension. An inline
   call has no such outer bound.
3. ``executor.py`` lives in ``infra/python-sandbox/`` (the container build
   context), NOT on the agent's import path — running it by file path keeps that
   single source of truth for the harness logic without copying it into the agent
   package.

The executor module is located by walking up from this file to the repo root and
joining ``infra/python-sandbox/executor.py``; an env override
(``GRACE2_SANDBOX_EXECUTOR``) lets tests / the container point elsewhere.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOG = logging.getLogger("grace2.agent.sandbox_runner")

# Wallclock cap (seconds) — matches infra/python-sandbox.tf's 60s Job timeout and
# the executor's GRACE2_SANDBOX_TIMEOUT. The runner's OUTER subprocess timeout is
# this + a grace window so the executor's own SIGALRM fires first (cleaner error:
# status="timeout" with captured partial output) and the outer kill is the
# backstop only.
WALLCLOCK_CAP_SECONDS = int(os.environ.get("GRACE2_SANDBOX_TIMEOUT", "60"))
# Grace window added to the outer subprocess timeout so the in-process alarm wins
# the race in the normal case (and the executor can flush its JSON envelope).
SUBPROCESS_GRACE_SECONDS = int(os.environ.get("GRACE2_SANDBOX_SUBPROC_GRACE", "10"))
# Max bytes of the child's stdout we will read/parse. The executor truncates its
# own stdout/stderr fields; this is the outer bound on the JSON envelope line.
MAX_ENVELOPE_BYTES = int(os.environ.get("GRACE2_SANDBOX_MAX_ENVELOPE_BYTES", str(8 * 1024 * 1024)))

# Cloud Run Job name (must match infra/python-sandbox.tf).
SANDBOX_JOB_NAME = os.environ.get("GRACE2_SANDBOX_JOB_NAME", "grace-2-python-sandbox")


def _is_local_mode() -> bool:
    return os.environ.get("GRACE2_SANDBOX_LOCAL", "").strip() in ("1", "true", "TRUE", "yes")


def _repo_root() -> Path:
    """Walk up from this file to the repo root (the dir containing ``infra/``)."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "infra" / "python-sandbox" / "executor.py").exists():
            return parent
    # Fallback: four levels up from services/agent/src/grace2_agent/sandbox_runner.py
    return here.parents[4]


def _executor_path() -> Path:
    override = os.environ.get("GRACE2_SANDBOX_EXECUTOR", "").strip()
    if override:
        return Path(override)
    return _repo_root() / "infra" / "python-sandbox" / "executor.py"


# --------------------------------------------------------------------------- #
# Result + handle shapes
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SandboxExecutionHandle:
    """Pending-result handle for a cloud sandbox dispatch (the sandbox analogue of
    the solver ``ExecutionHandle``). The agent polls/cancels on
    ``execution_name`` — the Cloud Run Job execution resource name.

    Fields:
      - ``handle_id`` — ULID for this dispatch (agent-side key).
      - ``execution_name`` — Cloud Run Job execution resource name
        (``projects/.../jobs/grace-2-python-sandbox/executions/...``) — the
        cancellation/poll seam.
      - ``payload_uri`` — gs:// staging file the Job reads (python_code + refs).
      - ``result_uri`` — gs:// object the Job's result envelope lands at
        (job-0233 wires the readback; the executor writes nothing today — this is
        the agreed location).
      - ``submitted_at`` — UTC submit time.
      - ``mode`` — "cloud" (always, for this handle type).
    """

    handle_id: str
    execution_name: str
    payload_uri: str
    result_uri: str
    submitted_at: datetime
    mode: str = "cloud"


# --------------------------------------------------------------------------- #
# Local-subprocess fallback — reuses executor.py
# --------------------------------------------------------------------------- #


def run_sandbox_local(
    python_code: str,
    layer_refs: dict[str, str] | None = None,
    *,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    """Run ``python_code`` through ``executor.py`` in a child subprocess.

    Returns the parsed result envelope (the same shape the container emits):
        {"stdout", "stderr", "result", "status", "error",
         "stdout_truncated", "stderr_truncated", "wallclock_cap_seconds", ...}

    Enforces:
      - the executor's in-process 60s SIGALRM cap (via GRACE2_SANDBOX_TIMEOUT), AND
      - an OUTER subprocess hard-kill at cap + grace (belt-and-suspenders), AND
      - the executor's output truncation + this runner's MAX_ENVELOPE_BYTES bound.

    On the outer hard-kill (the in-process alarm was defeated) we synthesize a
    ``status="timeout"`` envelope so the caller always gets a well-formed result.
    """
    cap = timeout_seconds if timeout_seconds is not None else WALLCLOCK_CAP_SECONDS
    executor = _executor_path()
    if not executor.exists():
        raise FileNotFoundError(f"sandbox executor not found at {executor}")

    payload = {"python_code": python_code, "layer_refs": layer_refs or {}}

    # Write the payload to a temp file the child reads via --payload-file.
    with tempfile.NamedTemporaryFile(
        "w", suffix=".json", prefix="grace2_sandbox_", delete=False, encoding="utf-8"
    ) as fh:
        json.dump(payload, fh)
        payload_path = fh.name

    # Child env: pin the executor's own cap to `cap` so the in-process alarm
    # matches the outer timeout's intent. Keep the rest of the env (PATH, venv,
    # PYTHONPATH) so the child resolves the same interpreter deps as the agent.
    child_env = dict(os.environ)
    child_env["GRACE2_SANDBOX_TIMEOUT"] = str(cap)
    child_env.setdefault("MPLBACKEND", "Agg")
    # Run the executor as a script by path — its __main__ guard calls main().
    cmd = [sys.executable, str(executor), "--payload-file", payload_path]

    LOG.info("sandbox local run: %s (cap=%ds)", " ".join(cmd), cap)
    proc = subprocess.Popen(  # noqa: S603 — fixed cmd, no shell
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=child_env,
        text=True,
    )
    outer_timeout = cap + SUBPROCESS_GRACE_SECONDS
    try:
        out, err = proc.communicate(timeout=outer_timeout)
    except subprocess.TimeoutExpired:
        # In-process alarm was defeated; hard-kill the child and synthesize a
        # timeout envelope. This is the wallclock backstop the kickoff requires.
        proc.kill()
        try:
            out, err = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            out, err = "", ""
        LOG.warning("sandbox local run exceeded outer timeout %ds; child killed", outer_timeout)
        return {
            "stdout": (out or "")[:MAX_ENVELOPE_BYTES],
            "stderr": (err or "")[:MAX_ENVELOPE_BYTES],
            "result": {"kind": "none", "value": None},
            "status": "timeout",
            "error": f"sandbox exceeded {cap}s wallclock cap (outer subprocess kill at {outer_timeout}s)",
            "stdout_truncated": False,
            "stderr_truncated": False,
            "wallclock_cap_seconds": cap,
        }
    finally:
        try:
            os.unlink(payload_path)
        except OSError:
            pass

    # The executor prints exactly one JSON envelope line on stdout. Parse the LAST
    # non-empty line (defensive: user code may have leaked to the real stdout in a
    # pathological case, though the executor redirects it; the envelope is last).
    out = (out or "")[:MAX_ENVELOPE_BYTES]
    envelope = _parse_envelope(out, err or "", proc.returncode)
    return envelope


def _parse_envelope(stdout: str, stderr: str, returncode: int | None) -> dict[str, Any]:
    """Parse the executor's JSON envelope from stdout; synthesize on parse failure."""
    candidate: str | None = None
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            candidate = line
            break
    if candidate is not None:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict) and "status" in parsed:
                return parsed
        except (TypeError, ValueError):
            pass
    # No well-formed envelope — the child crashed before emitting one.
    return {
        "stdout": stdout,
        "stderr": stderr,
        "result": {"kind": "none", "value": None},
        "status": "error",
        "error": (
            f"sandbox child produced no parseable result envelope "
            f"(returncode={returncode}); stderr tail: {stderr[-500:]!r}"
        ),
        "stdout_truncated": False,
        "stderr_truncated": False,
        "wallclock_cap_seconds": WALLCLOCK_CAP_SECONDS,
    }


# --------------------------------------------------------------------------- #
# Cloud dispatch
# --------------------------------------------------------------------------- #


def submit_sandbox_job(
    python_code: str,
    layer_refs: dict[str, str] | None = None,
    *,
    timeout_seconds: int | None = None,
) -> SandboxExecutionHandle | dict[str, Any]:
    """Dispatch a sandbox run.

    * In local mode (``GRACE2_SANDBOX_LOCAL=1``) this runs synchronously via
      :func:`run_sandbox_local` and returns the parsed RESULT ENVELOPE dict
      directly (no handle — the run is already complete).
    * In cloud mode it stages the payload + submits the Cloud Run Job execution
      and returns a :class:`SandboxExecutionHandle` (pending result).

    The caller (job-0233) branches on the return type: a dict is a finished local
    result; a ``SandboxExecutionHandle`` is a pending cloud dispatch to poll.
    """
    if _is_local_mode():
        return run_sandbox_local(python_code, layer_refs, timeout_seconds=timeout_seconds)
    return _submit_cloud(python_code, layer_refs)


def _submit_cloud(
    python_code: str,
    layer_refs: dict[str, str] | None,
) -> SandboxExecutionHandle:
    """Stage the payload to GCS + submit the Cloud Run Job execution.

    Lazy-imports google-cloud clients so the agent boots in CI/test without ADC
    (mirror of solver.py discipline). The result-readback (job-0233) reads the
    envelope the container writes to ``result_uri``.
    """
    from grace2_contracts import new_ulid  # local import: keeps module import light

    handle_id = new_ulid()
    run_key = new_ulid()
    submitted_at = datetime.now(timezone.utc)

    cache_bucket = os.environ.get("GRACE2_CACHE_BUCKET", "grace-2-hazard-prod-cache")
    project = os.environ.get("GCP_PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT")
    location = os.environ.get("GCP_REGION", "us-central1")

    payload = {"python_code": python_code, "layer_refs": layer_refs or {}}
    payload_blob = f"sandbox/{run_key}/payload.json"
    result_blob = f"sandbox/{run_key}/result.json"
    payload_uri = f"gs://{cache_bucket}/{payload_blob}"
    result_uri = f"gs://{cache_bucket}/{result_blob}"

    # Stage the payload object.
    storage_client = _get_storage_client(project)
    bucket = storage_client.bucket(cache_bucket)
    bucket.blob(payload_blob).upload_from_string(
        json.dumps(payload), content_type="application/json"
    )

    # Submit the Cloud Run Job execution with the payload URI + result URI as
    # per-execution env overrides.
    run_client = _get_run_jobs_client()
    job_name = f"projects/{project}/locations/{location}/jobs/{SANDBOX_JOB_NAME}"
    execution_name = _run_job_with_overrides(
        run_client,
        job_name,
        env_overrides={
            "GRACE2_SANDBOX_PAYLOAD_URI": payload_uri,
            "GRACE2_SANDBOX_RESULT_URI": result_uri,
        },
    )

    handle = SandboxExecutionHandle(
        handle_id=handle_id,
        execution_name=execution_name,
        payload_uri=payload_uri,
        result_uri=result_uri,
        submitted_at=submitted_at,
    )
    LOG.info(
        "submit_sandbox_job (cloud) handle_id=%s execution=%s payload=%s",
        handle.handle_id,
        handle.execution_name,
        handle.payload_uri,
    )
    return handle


def _get_storage_client(project: str | None) -> Any:
    from google.cloud import storage  # type: ignore[import-not-found]  # noqa: PLC0415

    return storage.Client(project=project)


def _get_run_jobs_client() -> Any:
    from google.cloud import run_v2  # type: ignore[import-not-found]  # noqa: PLC0415

    return run_v2.JobsClient()


def _run_job_with_overrides(
    client: Any,
    job_name: str,
    env_overrides: dict[str, str],
) -> str:
    """Submit ``jobs.run`` with container env overrides; return the execution name.

    Uses the run_v2 RunJobRequest.Overrides shape. Returns the long-running
    operation's metadata execution name (the seam the agent polls/cancels).
    """
    from google.cloud import run_v2  # type: ignore[import-not-found]  # noqa: PLC0415

    overrides = run_v2.RunJobRequest.Overrides(
        container_overrides=[
            run_v2.RunJobRequest.Overrides.ContainerOverride(
                env=[run_v2.EnvVar(name=k, value=v) for k, v in env_overrides.items()]
            )
        ]
    )
    operation = client.run_job(
        request=run_v2.RunJobRequest(name=job_name, overrides=overrides)
    )
    # The operation's metadata carries the Execution resource being created.
    meta = getattr(operation, "metadata", None)
    name = getattr(meta, "name", None)
    if not name:
        # Fall back to the operation name if metadata isn't populated yet.
        name = getattr(operation, "operation", None)
        name = getattr(name, "name", None) or str(operation)
    return name
