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

Solver backend seam (job-0291, sprint-14-aws)
---------------------------------------------

``GRACE2_SOLVER_BACKEND`` selects the dispatch substrate at call time:

- ``gcp-workflows`` (default) — today's behavior verbatim: Cloud Workflows
  ``create_execution`` → Cloud Run Job (``services/workers/sfincs/
  entrypoint.py``) → completion.json in the GCS runs bucket. Byte-identical
  to the pre-job-0291 path.
- ``local-docker`` — the AWS EC2 path. The GCS-IN → sfincs → GCS-OUT
  envelope the GCP container implements moves INTO the agent (testable
  Python), and the container is the PLAIN upstream ``deltares/sfincs-cpu``
  binary image run via ``docker run`` on the same instance:

      run_solver: mint run_id → download the setup manifest from S3 (boto3)
        → stage every ``inputs[]`` object into ``$GRACE2_RUNS_DIR/<run_id>/``
        (manifest field name stays the legacy ``gs_uri``; the VALUE is
        resolved by scheme — s3:// via boto3, gs:// via google-cloud-storage)
        → launch ``docker run --rm --name <run_id> -v <rundir>:/data -w /data
        $GRACE2_SFINCS_IMAGE [sfincs_args]`` DETACHED (Popen — mirrors the
        non-blocking Cloud Workflows submit) → return ExecutionHandle
        immediately (``workflow_name="local-docker"``,
        ``workflows_execution_id="local-docker:<run_id>"`` — the container
        name IS the run_id, which is the Invariant-8 cancellation seam).

      supervisor (daemon thread): waits on the docker process, expands the
        manifest's ``outputs[]`` globs in the rundir, uploads outputs +
        sfincs.stdout/sfincs.stderr to ``s3://$GRACE2_RUNS_BUCKET/<run_id>/``
        (boto3), and ALWAYS writes ``completion.json`` (exact entrypoint.py
        schema: run_id/status/exit_code/sfincs_stdout_uri/sfincs_stderr_uri/
        output_uris/started_at/finished_at/error) — even on crash
        (status="error") or cancel (status="cancelled").

      wait_for_completion: dispatches on ``handle.workflow_name`` — local
        handles poll the completion.json object on S3 (same cadence/timeout/
        progress-ramp semantics as the Workflows poll) and build the
        RunResult with ``output_uri = s3://<runs_bucket>/<run_id>/``.

      cancel chain: ``asyncio.CancelledError`` in the poll sleep → mark the
        run cancelled + ``docker kill <run_id>`` (≤30 s, Invariant-8) → the
        supervisor wakes on process exit and writes the status="cancelled"
        completion.json → re-raise.

  ``GRACE2_RUNS_BUCKET`` has NO default under local-docker (we never
  silently write to a GCP-named bucket from AWS); a missing value raises
  ``SolverDispatchError``. boto3 is used for ALL S3 I/O (s3fs falls back to
  anonymous credentials on the EC2 instance role — job-0289 lesson).

  Production scale-up to AWS Batch is a later job; it slots in as a third
  ``GRACE2_SOLVER_BACKEND`` value behind the same dispatch seam.

Generalized local backend (job-0292b, sprint-14-aws)
----------------------------------------------------

job-0292b extends the job-0291 machinery to MODFLOW without forking it. The
staging → detached launch → supervisor → completion.json → S3-poll envelope is
solver-agnostic; the solver-specific knobs are bundled into a
``LocalSolverSpec`` (manifest argv key, launch argv builder, stdout/stderr
artifact names, completion-manifest field names, an optional post-exit
classifier for solver-specific status resolution, and the cancel kind):

- SFINCS keeps the job-0291 ``docker run`` path verbatim
  (``_run_solver_local_docker`` builds the SFINCS spec; the completion.json
  is byte-identical to ``services/workers/sfincs/entrypoint.py``).
- MODFLOW (``workflows/run_modflow.py``) launches the **mf6 binary directly**
  (``exec_kind="exec"`` — no public MODFLOW image exists; the instance gets
  the same SHA-pinned USGS 6.5.0 static binary the GCP Dockerfile installs).
  Its spec's ``classify_exit`` reproduces the MODFLOW entrypoint's
  list-file convergence guard, and the completion.json carries the EXACT
  ``services/workers/modflow/entrypoint.py`` key set (``mf6_stdout_uri`` /
  ``mf6_stderr_uri`` / ``converged`` / ``model_crs``).

Cancel kinds: ``"docker"`` → ``docker kill <run_id>`` (container name ==
run_id, job-0291); ``"exec"`` → ``os.killpg`` on the detached process group
(``start_new_session=True`` makes pgid == pid). Both terminal ≤30 s
(Invariant 8). ``wait_for_completion`` dispatches on the handle's
``workflow_name`` ∈ {``local-docker``, ``local-exec``} — the poll loop is
shared.
"""

from __future__ import annotations

import asyncio
import glob as _glob
import json
import logging
import os
import signal
import subprocess
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
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
    "set_s3_client",
    "solver_backend",
    "SOLVER_BACKEND_GCP_WORKFLOWS",
    "SOLVER_BACKEND_LOCAL_DOCKER",
    "LOCAL_DOCKER_WORKFLOW_NAME",
    "LOCAL_EXEC_WORKFLOW_NAME",
    "LocalSolverSpec",
    "launch_local_solver",
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


# --- Solver backend seam (job-0291, sprint-14-aws) --- #

#: Default backend — Cloud Workflows dispatch, byte-identical pre-job-0291.
SOLVER_BACKEND_GCP_WORKFLOWS: str = "gcp-workflows"

#: AWS EC2 backend — plain upstream ``deltares/sfincs-cpu`` via ``docker run``
#: on the same instance; staging/upload envelope lives in this module.
SOLVER_BACKEND_LOCAL_DOCKER: str = "local-docker"

#: ``ExecutionHandle.workflow_name`` sentinel for local-docker handles —
#: ``wait_for_completion`` dispatches on it (the handle pins its backend so
#: env churn between submit and wait cannot mis-route the poll).
LOCAL_DOCKER_WORKFLOW_NAME: str = "local-docker"

#: ``ExecutionHandle.workflow_name`` sentinel for image-less local runs that
#: exec a solver binary directly (job-0292b — MODFLOW's mf6 has no public
#: image; the USGS static binary runs on the instance). Same poll loop as
#: local-docker; the cancel chain kills the detached process group instead
#: of a container.
LOCAL_EXEC_WORKFLOW_NAME: str = "local-exec"

#: The two local workflow_name sentinels ``wait_for_completion`` accepts.
_LOCAL_WORKFLOW_NAMES: tuple[str, str] = (
    LOCAL_DOCKER_WORKFLOW_NAME,
    LOCAL_EXEC_WORKFLOW_NAME,
)

#: ``ExecutionHandle.workflow_location`` for local-docker handles.
LOCAL_DOCKER_WORKFLOW_LOCATION: str = "local"

#: Default rundir root under local-docker (env ``GRACE2_RUNS_DIR``).
DEFAULT_LOCAL_RUNS_DIR: str = "/opt/grace2/runs"

#: Default SFINCS image under local-docker (env ``GRACE2_SFINCS_IMAGE``).
DEFAULT_SFINCS_IMAGE: str = "deltares/sfincs-cpu:latest"

#: Budget for the ``docker kill`` subprocess on cancel — comfortably inside
#: the ≤30 s Invariant-8 / NFR-R-3 envelope.
DOCKER_KILL_TIMEOUT_S: float = 25.0


def solver_backend() -> str:
    """Return the active solver backend (job-0291 dispatch seam).

    ``GRACE2_SOLVER_BACKEND=local-docker`` → ``"local-docker"``; anything
    else (unset, ``gcp-workflows``, typos) → ``"gcp-workflows"`` so the
    default path stays byte-identical. Read at call time so an AWS deploy /
    test env injection takes effect without re-import (mirrors
    ``cache.storage_scheme``).
    """
    b = (os.environ.get("GRACE2_SOLVER_BACKEND") or "").strip().lower()
    return (
        SOLVER_BACKEND_LOCAL_DOCKER
        if b == SOLVER_BACKEND_LOCAL_DOCKER
        else SOLVER_BACKEND_GCP_WORKFLOWS
    )


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
_S3_CLIENT: Any | None = None


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


def set_s3_client(client: Any) -> None:
    """Bind the boto3 S3 client used for ALL local-docker S3 I/O (job-0291).

    Production wiring leaves this ``None`` (the lazy default builds
    ``boto3.client("s3", region_name=$AWS_REGION)``, which resolves the EC2
    instance-role credentials via IMDS — the job-0289 boto3-not-s3fs lesson).
    Tests inject a tmpdir-backed fake exposing ``get_object`` /
    ``put_object``. ``None`` restores the lazy default.

    The deck-assembly (``sfincs_builder``) and run-output
    (``postprocess_flood``) S3 paths share this seam so one injection covers
    the whole staged-manifest → solve → postprocess chain.
    """
    global _S3_CLIENT
    _S3_CLIENT = client


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
    return storage.Client(
        project=os.environ.get("GOOGLE_CLOUD_PROJECT", "grace-2-hazard-prod")
    )


def _get_s3_client() -> Any:
    """Return the bound S3 client or lazily construct the boto3 default.

    boto3 (NOT s3fs) for all S3 I/O — s3fs falls back to anonymous
    credentials on the EC2 instance role (job-0289). Lazy import so
    GCP-only / CI environments never pay for boto3 at module load.
    """
    if _S3_CLIENT is not None:
        return _S3_CLIENT
    try:
        import boto3  # type: ignore[import-not-found]
    except Exception as exc:  # noqa: BLE001
        raise SolverDispatchError(
            f"boto3 not importable: {exc}; the local-docker solver backend "
            "requires boto3 for S3 staging/upload (job-0291)."
        ) from exc
    return boto3.client("s3", region_name=os.environ.get("AWS_REGION", "us-west-2"))


def _get_runs_bucket() -> str:
    """Return the overridden runs bucket or the env-default
    (``GRACE2_RUNS_BUCKET`` if set, else ``grace-2-hazard-prod-runs``)."""
    if _RUNS_BUCKET is not None:
        return _RUNS_BUCKET
    return os.environ.get("GRACE2_RUNS_BUCKET", "grace-2-hazard-prod-runs")


def _get_local_runs_bucket() -> str:
    """Runs bucket under local-docker — NO default to a GCP bucket name.

    ``set_runs_bucket`` override wins (test seam); otherwise
    ``GRACE2_RUNS_BUCKET`` must be set explicitly (on AWS the orchestrator
    provisions e.g. ``grace2-hazard-runs-226996537797``). A silent fallback
    to the GCP-named default would make every local run upload to a bucket
    that does not exist on AWS — fail loudly instead.
    """
    if _RUNS_BUCKET is not None:
        return _RUNS_BUCKET
    bucket = (os.environ.get("GRACE2_RUNS_BUCKET") or "").strip()
    if not bucket:
        raise SolverDispatchError(
            "GRACE2_RUNS_BUCKET must be set when GRACE2_SOLVER_BACKEND="
            "local-docker (no GCP-named default on AWS; job-0291)."
        )
    return bucket


def _gcp_project() -> str:
    """Return the GCP project id (env-driven; mirrors the substrate)."""
    return os.environ.get("GRACE2_GCP_PROJECT", "grace-2-hazard-prod")


def _gcp_location() -> str:
    """Return the GCP region for the workflows execution (env-driven)."""
    return os.environ.get("GRACE2_GCP_LOCATION", "us-central1")


# --------------------------------------------------------------------------- #
# local-docker backend (job-0291, sprint-14-aws)
#
# The GCS-IN → sfincs → GCS-OUT envelope from
# ``services/workers/sfincs/entrypoint.py`` ported into the agent: staging,
# detached ``docker run`` of the plain upstream image, a supervisor thread
# that uploads outputs and ALWAYS writes the entrypoint-schema
# completion.json, S3 completion polling, and the docker-kill cancel chain.
# --------------------------------------------------------------------------- #


def _utc_now_iso() -> str:
    """ISO8601-Z timestamp matching the entrypoint's ``_utc_now`` format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _split_object_uri(uri: str) -> tuple[str, str, str]:
    """Split ``s3://bucket/key`` / ``gs://bucket/key`` → (scheme, bucket, key).

    Raises ``SolverDispatchError`` on malformed or unsupported URIs.
    """
    for scheme in ("s3", "gs"):
        prefix = f"{scheme}://"
        if uri.startswith(prefix):
            bucket, _, key = uri[len(prefix):].partition("/")
            if not bucket or not key:
                raise SolverDispatchError(f"malformed {scheme}:// URI: {uri!r}")
            return scheme, bucket, key
    raise SolverDispatchError(
        f"unsupported object URI scheme: {uri!r} (expected s3:// or gs://)"
    )


def _read_object_bytes(uri: str) -> bytes:
    """Read one object's bytes, resolved BY SCHEME (job-0291 kickoff):
    ``s3://`` via boto3, ``gs://`` via google-cloud-storage (legacy),
    ``file://`` / local path via the filesystem (the sfincs_builder
    local-manifest fallback)."""
    if uri.startswith("file://"):
        return Path(uri[len("file://"):]).read_bytes()
    if not (uri.startswith("s3://") or uri.startswith("gs://")):
        return Path(uri).read_bytes()
    scheme, bucket, key = _split_object_uri(uri)
    if scheme == "s3":
        resp = _get_s3_client().get_object(Bucket=bucket, Key=key)
        return resp["Body"].read()
    return _get_storage_client().bucket(bucket).blob(key).download_as_bytes()


def _download_object(uri: str, dest: Path) -> None:
    """Download one staged input to ``dest``, resolved by scheme.

    The manifest's input entries keep the LEGACY field name ``gs_uri`` but
    the VALUE may be ``s3://`` (the job-0289 storage backend) or ``gs://``
    — we dispatch on the URI scheme, never the field name.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    if uri.startswith("file://") or not (
        uri.startswith("s3://") or uri.startswith("gs://")
    ):
        src = Path(uri[len("file://"):] if uri.startswith("file://") else uri)
        dest.write_bytes(src.read_bytes())
        return
    scheme, bucket, key = _split_object_uri(uri)
    logger.info("local-docker staging %s -> %s", uri, dest)
    if scheme == "s3":
        resp = _get_s3_client().get_object(Bucket=bucket, Key=key)
        import shutil

        with dest.open("wb") as fh:
            shutil.copyfileobj(resp["Body"], fh)
        return
    _get_storage_client().bucket(bucket).blob(key).download_to_filename(str(dest))


def _upload_file_s3(s3: Any, src: Path, bucket: str, key: str) -> str:
    """Upload ``src`` to ``s3://bucket/key`` via boto3; return the s3:// URI."""
    with src.open("rb") as fh:
        s3.put_object(Bucket=bucket, Key=key, Body=fh)
    return f"s3://{bucket}/{key}"


@dataclass(frozen=True)
class LocalSolverSpec:
    """Solver-specific knobs for the shared local backend (job-0292b).

    The job-0291 staging → detached launch → supervisor → completion.json
    envelope is solver-agnostic; this spec carries everything that is not:

    Fields:
        solver: lowercase solver identifier carried on the handle (and used in
            the generic non-zero-exit error message — ``"sfincs exited with
            non-zero code N"`` stays byte-identical for SFINCS).
        workflow_name: the ``ExecutionHandle.workflow_name`` sentinel —
            ``"local-docker"`` (container launch) or ``"local-exec"``
            (direct binary launch). ``wait_for_completion`` accepts both.
        args_key: the manifest key carrying the solver argv tail
            (``"sfincs_args"`` / ``"mf6_args"`` — worker-entrypoint parity).
        build_argv: ``(run_id, rundir, manifest_args) -> argv`` — the full
            launch command. SFINCS builds the ``docker run --rm --name
            <run_id> ...`` line; MODFLOW returns ``[mf6, *args]``.
        stdout_name / stderr_name: the rundir artifact filenames (and the
            runs-prefix upload keys) — ``sfincs.stdout`` / ``mf6.stdout`` etc.
        stdout_uri_field / stderr_uri_field: the completion.json field names
            (``sfincs_stdout_uri`` vs ``mf6_stdout_uri`` — exact entrypoint
            schemas).
        exec_kind: ``"docker"`` → cancel via ``docker kill <run_id>``;
            ``"exec"`` → cancel via ``os.killpg`` on the detached group.
        classify_exit: optional ``(rundir, exit_code) -> (status, exit_code,
            error, extra_completion_fields)`` post-exit hook for
            solver-specific status resolution (MODFLOW's mfsim.lst
            convergence guard + the ``converged``/``model_crs`` completion
            fields). ``None`` → the plain exit-code rule (SFINCS). A user
            cancel overrides whatever the classifier returned.
    """

    solver: str
    workflow_name: str
    args_key: str
    build_argv: Callable[[str, Path, list[str]], list[str]]
    stdout_name: str
    stderr_name: str
    stdout_uri_field: str
    stderr_uri_field: str
    exec_kind: str = "docker"
    classify_exit: (
        Callable[[Path, int], tuple[str, int, str | None, dict[str, Any]]] | None
    ) = None


def _sfincs_local_spec() -> LocalSolverSpec:
    """The job-0291 SFINCS local-docker spec — behavior verbatim."""
    image = os.environ.get("GRACE2_SFINCS_IMAGE") or DEFAULT_SFINCS_IMAGE

    def build_argv(run_id: str, rundir: Path, args: list[str]) -> list[str]:
        return [
            "docker",
            "run",
            "--rm",
            "--name",
            run_id,
            "-v",
            f"{rundir}:/data",
            "-w",
            "/data",
            image,
            *args,
        ]

    return LocalSolverSpec(
        solver="sfincs",
        workflow_name=LOCAL_DOCKER_WORKFLOW_NAME,
        args_key="sfincs_args",
        build_argv=build_argv,
        stdout_name="sfincs.stdout",
        stderr_name="sfincs.stderr",
        stdout_uri_field="sfincs_stdout_uri",
        stderr_uri_field="sfincs_stderr_uri",
        exec_kind="docker",
        classify_exit=None,
    )


@dataclass
class _LocalRun:
    """In-process registry entry for one local-backend solver run."""

    run_id: str
    rundir: Path
    runs_bucket: str
    proc: subprocess.Popen
    output_patterns: list[str]
    started_at: str  # ISO8601-Z, entrypoint format
    stdout_path: Path
    stderr_path: Path
    spec: LocalSolverSpec
    cancel_requested: threading.Event = field(default_factory=threading.Event)
    supervisor: threading.Thread | None = None


#: run_id → live local run. In-process only: ``run_solver`` and the cancel
#: chain are co-located in the agent process (the deployed topology). The
#: supervisor pops its entry when the completion.json is written.
_LOCAL_RUNS: dict[str, _LocalRun] = {}


def _expand_local_outputs(patterns: list[str], rundir: Path) -> list[Path]:
    """Glob-expand the manifest ``outputs[]`` in the rundir — mirrors the
    entrypoints' ``_expand_outputs`` (files only, de-duplicated, sorted).
    ``recursive=True`` so ``**`` patterns behave like the SFINCS/MODFLOW
    worker entrypoints (job-0292b — the MODFLOW manifest carries
    ``**/gwt_model.ucn`` / ``**/*.lst`` belt-and-suspenders nets)."""
    seen: set[Path] = set()
    for pat in patterns:
        for hit in _glob.glob(str(rundir / pat), recursive=True):
            p = Path(hit)
            if p.is_file():
                seen.add(p.resolve())
    return sorted(seen)


def _write_local_completion(
    s3: Any,
    *,
    runs_bucket: str,
    run_id: str,
    status: str,
    exit_code: int,
    output_uris: list[str],
    stdout_uri: str | None,
    stderr_uri: str | None,
    started_at: str,
    error: str | None,
    stdout_uri_field: str = "sfincs_stdout_uri",
    stderr_uri_field: str = "sfincs_stderr_uri",
    extra: dict[str, Any] | None = None,
) -> None:
    """Write ``s3://<runs_bucket>/<run_id>/completion.json`` — EXACT
    worker-entrypoint schema (the ``wait_for_completion`` terminal signal).

    job-0292b: the stdout/stderr field names + an ``extra`` field dict are
    spec-driven so the MODFLOW completion carries ``mf6_stdout_uri`` /
    ``mf6_stderr_uri`` / ``converged`` / ``model_crs`` exactly like
    ``services/workers/modflow/entrypoint.py``; the SFINCS defaults are
    byte-identical to job-0291.
    """
    payload = {
        "run_id": run_id,
        "status": status,
        "exit_code": exit_code,
        **(extra or {}),
        stdout_uri_field: stdout_uri,
        stderr_uri_field: stderr_uri,
        "output_uris": output_uris,
        "started_at": started_at,
        "finished_at": _utc_now_iso(),
        "error": error,
    }
    s3.put_object(
        Bucket=runs_bucket,
        Key=f"{run_id}/completion.json",
        Body=json.dumps(payload, indent=2).encode("utf-8"),
        ContentType="application/json",
    )
    logger.info(
        "local-docker wrote completion -> s3://%s/%s/completion.json (status=%s)",
        runs_bucket,
        run_id,
        status,
    )


def _supervise_local_run(run: _LocalRun) -> None:
    """Supervisor body (daemon thread): wait on the solver process, upload
    stdout/stderr + glob-expanded outputs to the S3 runs prefix, and ALWAYS
    write completion.json — even on crash (status="error") or cancel
    (status="cancelled"). Mirrors the entrypoints' best-effort discipline:
    no upload failure may prevent the terminal completion write."""
    status = "error"
    exit_code = 1
    error_msg: str | None = None
    output_uris: list[str] = []
    stdout_uri: str | None = None
    stderr_uri: str | None = None
    completion_extra: dict[str, Any] = {}

    try:
        exit_code = run.proc.wait()
        # Solver-specific post-exit classification first (job-0292b — the
        # MODFLOW spec's mfsim.lst convergence guard); the plain exit-code
        # rule otherwise (SFINCS, byte-identical to job-0291). A user cancel
        # overrides either verdict below.
        if run.spec.classify_exit is not None:
            try:
                status, exit_code, error_msg, completion_extra = (
                    run.spec.classify_exit(run.rundir, exit_code)
                )
            except Exception as exc:  # noqa: BLE001 — classifier must not kill the write
                logger.exception(
                    "local classify_exit failed run_id=%s", run.run_id
                )
                status = "error"
                error_msg = f"classify_exit raised {type(exc).__name__}: {exc}"
        elif exit_code == 0:
            status = "ok"
            error_msg = None
        else:
            status = "error"
            error_msg = f"{run.spec.solver} exited with non-zero code {exit_code}"
        if run.cancel_requested.is_set():
            status = "cancelled"
            error_msg = (
                "run cancelled (docker kill via Invariant-8 cancel chain)"
                if run.spec.exec_kind == "docker"
                else "run cancelled (process-group kill via Invariant-8 cancel chain)"
            )
    except Exception as exc:  # noqa: BLE001 — defensive: wait() itself failed
        logger.exception("local-docker supervisor wait failed run_id=%s", run.run_id)
        status = "error"
        error_msg = f"{type(exc).__name__}: {exc}"

    try:
        s3 = _get_s3_client()
    except Exception as exc:  # noqa: BLE001 — no client ⇒ nothing more we can do
        logger.error(
            "local-docker supervisor could not build S3 client run_id=%s: %s "
            "— completion.json NOT written (poller will time out)",
            run.run_id,
            exc,
        )
        _LOCAL_RUNS.pop(run.run_id, None)
        return

    # Always upload stdout/stderr (entrypoint parity — evidence even on error).
    try:
        if run.stdout_path.exists():
            stdout_uri = _upload_file_s3(
                s3,
                run.stdout_path,
                run.runs_bucket,
                f"{run.run_id}/{run.spec.stdout_name}",
            )
        if run.stderr_path.exists():
            stderr_uri = _upload_file_s3(
                s3,
                run.stderr_path,
                run.runs_bucket,
                f"{run.run_id}/{run.spec.stderr_name}",
            )
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.warning(
            "local-docker stdout/stderr upload failed run_id=%s: %s", run.run_id, exc
        )

    try:
        for path in _expand_local_outputs(run.output_patterns, run.rundir):
            rel = path.relative_to(run.rundir).as_posix()
            uri = _upload_file_s3(s3, path, run.runs_bucket, f"{run.run_id}/{rel}")
            output_uris.append(uri)
    except Exception as exc:  # noqa: BLE001 — reflect, but still write completion
        logger.exception(
            "local-docker output upload failed run_id=%s: %s", run.run_id, exc
        )
        if status == "ok":
            status = "error"
            error_msg = f"output upload to s3://{run.runs_bucket}/{run.run_id}/ failed: {exc}"

    try:
        _write_local_completion(
            s3,
            runs_bucket=run.runs_bucket,
            run_id=run.run_id,
            status=status,
            exit_code=exit_code,
            output_uris=output_uris,
            stdout_uri=stdout_uri,
            stderr_uri=stderr_uri,
            started_at=run.started_at,
            error=error_msg,
            stdout_uri_field=run.spec.stdout_uri_field,
            stderr_uri_field=run.spec.stderr_uri_field,
            extra=completion_extra,
        )
    except Exception:  # noqa: BLE001 — terminal-signal write failed; log loudly
        logger.exception(
            "local-docker completion.json write FAILED run_id=%s — "
            "wait_for_completion will hit its timeout",
            run.run_id,
        )
    finally:
        _LOCAL_RUNS.pop(run.run_id, None)


def launch_local_solver(
    spec: LocalSolverSpec,
    model_setup_uri: str,
    *,
    run_id: str | None = None,
    compute_class: str = "medium",
) -> ExecutionHandle:
    """Generic local-backend launcher (job-0291 envelope, job-0292b spec seam).

    Non-blocking — mirrors the Cloud Workflows submit semantics: stage the
    manifest's inputs from the object store, launch the solver detached
    (``spec.build_argv`` — a ``docker run`` line or a direct binary), hand the
    supervisor to a daemon thread, return the ``ExecutionHandle`` immediately.

    Args:
        spec: the solver-specific knobs (see ``LocalSolverSpec``).
        model_setup_uri: ``s3://`` / ``gs://`` / ``file://`` URI of the
            worker-contract manifest; input URIs inside resolve by scheme.
        run_id: optional pre-minted run id (the MODFLOW deck is staged under
            ``modflow/<run_id>/`` BEFORE submit, so its run_id must flow
            through — GCP parity with the ``{run_id, manifest_uri}`` workflow
            argument). Minted fresh when ``None`` (the SFINCS path).
        compute_class: FR-CE-3 class, alias-mapped onto the schema literal.
    """
    if not (
        model_setup_uri.startswith("s3://")
        or model_setup_uri.startswith("gs://")
        or model_setup_uri.startswith("file://")
    ):
        raise SolverDispatchError(
            f"model_setup_uri must be an s3:// / gs:// / file:// URI under "
            f"the local-docker backend; got {model_setup_uri!r}"
        )
    schema_compute_class = _COMPUTE_CLASS_ALIAS.get(compute_class)
    if schema_compute_class is None:
        raise SolverDispatchError(
            f"compute_class {compute_class!r} not recognized; allowed: "
            f"{sorted(_COMPUTE_CLASS_ALIAS)}"
        )
    runs_bucket = _get_local_runs_bucket()  # fail fast on missing env

    run_id = run_id or new_ulid()
    submitted_at = datetime.now(timezone.utc)
    rundir = (
        Path(os.environ.get("GRACE2_RUNS_DIR") or DEFAULT_LOCAL_RUNS_DIR) / run_id
    )
    rundir.mkdir(parents=True, exist_ok=True)

    # --- Manifest read + input staging (the entrypoint's download phase) ---
    try:
        manifest = json.loads(_read_object_bytes(model_setup_uri))
    except SolverDispatchError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise SolverDispatchError(
            f"local-docker manifest read failed {model_setup_uri}: {exc}"
        ) from exc
    if not isinstance(manifest, dict):
        raise SolverDispatchError(
            f"manifest at {model_setup_uri} must be a JSON object"
        )
    inputs = manifest.get("inputs", []) or []
    solver_args = [str(a) for a in (manifest.get(spec.args_key, []) or [])]
    output_patterns = [str(p) for p in (manifest.get("outputs", []) or [])]

    rundir_resolved = rundir.resolve()
    for item in inputs:
        try:
            input_uri = item["gs_uri"]  # legacy field NAME; value resolved by scheme
            dest_rel = item["dest"]
        except (TypeError, KeyError) as exc:
            raise SolverDispatchError(
                f"manifest input entry malformed (need gs_uri + dest): {item!r}"
            ) from exc
        dest = rundir / dest_rel
        # Host-side path-traversal guard (the GCP entrypoint runs sandboxed in
        # its container; here we stage on the instance filesystem).
        if rundir_resolved not in dest.resolve().parents:
            raise SolverDispatchError(
                f"manifest input dest escapes the rundir: {dest_rel!r}"
            )
        try:
            _download_object(input_uri, dest)
        except SolverDispatchError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise SolverDispatchError(
                f"local-docker input staging failed {input_uri} -> {dest}: {exc}"
            ) from exc

    # --- Detached launch (docker: container name == run_id is the cancel
    # seam; exec: the detached process group is — start_new_session=True
    # makes pgid == pid for os.killpg) ---
    stdout_path = rundir / spec.stdout_name
    stderr_path = rundir / spec.stderr_name
    cmd = spec.build_argv(run_id, rundir, solver_args)
    logger.info("local-%s exec: %s", spec.exec_kind, " ".join(cmd))
    try:
        with stdout_path.open("wb") as out, stderr_path.open("wb") as err:
            proc = subprocess.Popen(  # noqa: S603 — argv list, no shell
                cmd,
                stdout=out,
                stderr=err,
                cwd=str(rundir),
                start_new_session=True,  # detach from the agent's signal group
            )
    except Exception as exc:  # noqa: BLE001 — docker/solver binary missing, etc.
        raise SolverDispatchError(
            f"local-{spec.exec_kind} launch failed ({' '.join(cmd[:6])} ...): {exc}"
        ) from exc

    run = _LocalRun(
        run_id=run_id,
        rundir=rundir,
        runs_bucket=runs_bucket,
        proc=proc,
        output_patterns=output_patterns,
        started_at=_utc_now_iso(),
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        spec=spec,
    )
    _LOCAL_RUNS[run_id] = run
    supervisor = threading.Thread(
        target=_supervise_local_run,
        args=(run,),
        name=f"{spec.solver}-local-supervisor-{run_id}",
        daemon=True,
    )
    run.supervisor = supervisor
    supervisor.start()

    handle = ExecutionHandle(
        handle_id=new_ulid(),
        run_id=run_id,
        solver=spec.solver,
        compute_class=schema_compute_class,  # type: ignore[arg-type]
        workflows_execution_id=f"{spec.workflow_name}:{run_id}",
        workflow_name=spec.workflow_name,
        workflow_location=LOCAL_DOCKER_WORKFLOW_LOCATION,
        submitted_at=submitted_at,
    )
    logger.info(
        "local-%s submitted run_id=%s handle_id=%s argv0=%s inputs=%d",
        spec.exec_kind,
        run_id,
        handle.handle_id,
        cmd[0] if cmd else "?",
        len(inputs),
    )
    return handle


def _run_solver_local_docker(
    solver: str, model_setup_uri: str, compute_class: str
) -> ExecutionHandle:
    """``run_solver`` body under ``GRACE2_SOLVER_BACKEND=local-docker`` — the
    job-0291 SFINCS docker path, now a thin spec over the shared launcher."""
    return launch_local_solver(
        _sfincs_local_spec(),
        model_setup_uri,
        compute_class=compute_class,
    )


def _docker_kill(run_id: str) -> None:
    """Best-effort ``docker kill <run_id>`` (container name == run_id)."""
    try:
        proc = subprocess.run(  # noqa: S603 — argv list, no shell
            ["docker", "kill", run_id],
            capture_output=True,
            timeout=DOCKER_KILL_TIMEOUT_S,
            check=False,
        )
        logger.info(
            "docker kill %s rc=%d stderr=%s",
            run_id,
            proc.returncode,
            proc.stderr.decode(errors="replace").strip()[:200],
        )
    except Exception as exc:  # noqa: BLE001 — cancel chain still propagates
        logger.warning("docker kill %s raised %s", run_id, exc)


def _killpg_local_run(run: _LocalRun) -> None:
    """Best-effort SIGKILL to the detached process group of an exec-kind run
    (``start_new_session=True`` at launch makes pgid == pid). job-0292b."""
    try:
        os.killpg(run.proc.pid, signal.SIGKILL)
        logger.info("killpg(%d) issued for run_id=%s", run.proc.pid, run.run_id)
    except ProcessLookupError:
        logger.info(
            "killpg for run_id=%s: process group already gone", run.run_id
        )
    except Exception as exc:  # noqa: BLE001 — cancel chain still propagates
        logger.warning("killpg for run_id=%s raised %s", run.run_id, exc)


def _kill_local_run(run_id: str) -> None:
    """Kind-aware best-effort kill (job-0292b): exec-kind runs get a
    process-group SIGKILL; docker-kind (and unknown — e.g. after an agent
    restart, where ``docker kill`` against the container name is the only
    remaining lever) get ``docker kill <run_id>``."""
    run = _LOCAL_RUNS.get(run_id)
    if run is not None and run.spec.exec_kind == "exec":
        _killpg_local_run(run)
        return
    if run is None:
        logger.warning(
            "local kill for unknown run_id=%s (no in-process supervisor); "
            "issuing docker kill only — an exec-kind run cannot be reached "
            "after an agent restart (OQ-291-LOCAL-CANCEL-CROSS-PROCESS)",
            run_id,
        )
    _docker_kill(run_id)


def _request_local_cancel(run_id: str) -> None:
    """Invariant-8 local cancel: flag the run cancelled, then kill the
    container / process group (kind-aware, job-0292b). The supervisor wakes
    on process exit and writes the status="cancelled" completion.json —
    terminal within ≤30 s."""
    run = _LOCAL_RUNS.get(run_id)
    if run is not None:
        run.cancel_requested.set()
    _kill_local_run(run_id)


def _try_get_completion_s3(runs_bucket: str, run_id: str) -> dict[str, Any] | None:
    """Poll ``s3://<runs_bucket>/<run_id>/completion.json`` once.

    Returns the parsed manifest, ``None`` when the object is not there yet
    (or on a transient read error — the timeout catches persistent faults,
    mirroring the Workflows-poll resilience). Malformed JSON raises
    ``SolverDispatchError`` (S3 PUTs are atomic, so a parse failure is real
    corruption, not a partial write).
    """
    s3 = _get_s3_client()
    try:
        resp = s3.get_object(Bucket=runs_bucket, Key=f"{run_id}/completion.json")
        data = resp["Body"].read()
    except Exception as exc:  # noqa: BLE001
        code = ""
        response = getattr(exc, "response", None)
        if isinstance(response, dict):
            code = str(response.get("Error", {}).get("Code", ""))
        if code in ("NoSuchKey", "404", "NoSuchBucket"):
            return None
        logger.warning(
            "local-docker completion poll degraded s3://%s/%s/completion.json: %s; "
            "will retry next poll",
            runs_bucket,
            run_id,
            exc,
        )
        return None
    try:
        manifest = json.loads(data)
    except Exception as exc:  # noqa: BLE001
        raise SolverDispatchError(
            f"completion manifest s3://{runs_bucket}/{run_id}/completion.json "
            f"is not valid JSON: {exc}"
        ) from exc
    if not isinstance(manifest, dict):
        raise SolverDispatchError(
            f"completion manifest s3://{runs_bucket}/{run_id}/completion.json "
            "is not a JSON object"
        )
    return manifest


def _build_local_run_result(
    handle: ExecutionHandle, manifest: dict[str, Any], runs_bucket: str
) -> RunResult:
    """Map a local-docker completion manifest onto a ``RunResult``.

    ``status="ok"`` → ``complete`` with ``output_uri = s3://<runs_bucket>/
    <run_id>/`` (the runs PREFIX, kickoff-pinned — ``postprocess_flood``
    resolves ``sfincs_map.nc`` inside it); ``"cancelled"`` → ``cancelled``;
    anything else → ``failed`` with the manifest's structured error.
    """
    manifest_status = str(manifest.get("status", "")).lower()
    started_at = _to_utc(manifest.get("started_at"))
    completed_at = _to_utc(manifest.get("finished_at")) or datetime.now(timezone.utc)

    if manifest_status == "ok":
        return RunResult(
            run_id=handle.run_id,
            handle_id=handle.handle_id,
            status="complete",
            output_uri=f"s3://{runs_bucket}/{handle.run_id}/",
            started_at=started_at,
            completed_at=completed_at,
            duration_seconds=_duration(started_at, completed_at),
        )
    if manifest_status == "cancelled":
        return RunResult(
            run_id=handle.run_id,
            handle_id=handle.handle_id,
            status="cancelled",
            output_uri=None,
            started_at=started_at,
            completed_at=completed_at,
            duration_seconds=_duration(started_at, completed_at),
            cancellation_reason=str(
                manifest.get("error") or "local-docker run cancelled"
            ),
        )
    return RunResult(
        run_id=handle.run_id,
        handle_id=handle.handle_id,
        status="failed",
        output_uri=None,
        started_at=started_at,
        completed_at=completed_at,
        duration_seconds=_duration(started_at, completed_at),
        error_code=_solver_error_code(manifest),
        error_message=str(manifest.get("error") or "solver reported failure"),
    )


async def _wait_for_completion_local(
    handle: ExecutionHandle, poll_interval_s: int, timeout_s: int
) -> RunResult:
    """``wait_for_completion`` body for local-docker handles: poll the
    completion.json object on S3 with the same cadence/timeout/progress-ramp
    semantics as the Cloud Workflows poll (job-0291)."""
    runs_bucket = _get_local_runs_bucket()
    deadline = handle.submitted_at.timestamp() + float(timeout_s)
    loop = asyncio.get_running_loop()

    logger.info(
        "wait_for_completion(local-docker) handle_id=%s run_id=%s "
        "poll_interval=%ds timeout=%ds",
        handle.handle_id,
        handle.run_id,
        poll_interval_s,
        timeout_s,
    )

    try:
        while True:
            manifest = await loop.run_in_executor(
                None, _try_get_completion_s3, runs_bucket, handle.run_id
            )
            now = datetime.now(timezone.utc)

            if manifest is not None:
                if str(manifest.get("status", "")).lower() == "ok":
                    await _emit_progress(PROGRESS_TERMINAL)
                else:
                    await _emit_progress(
                        _progress_percent(handle.submitted_at, now)
                    )
                return _build_local_run_result(handle, manifest, runs_bucket)

            await _emit_progress(_progress_percent(handle.submitted_at, now))

            if now.timestamp() >= deadline:
                logger.warning(
                    "wait_for_completion(local-docker) timed out handle_id=%s "
                    "after %ds; killing container %s",
                    handle.handle_id,
                    timeout_s,
                    handle.run_id,
                )
                # Timeout ≠ user cancel: kill WITHOUT the cancelled flag so the
                # supervisor records status="error" (mirrors the GCP path's
                # best-effort cancel + SOLVER_TIMEOUT result). Kind-aware
                # (job-0292b): docker kill or process-group kill.
                await loop.run_in_executor(None, _kill_local_run, handle.run_id)
                return RunResult(
                    run_id=handle.run_id,
                    handle_id=handle.handle_id,
                    status="failed",
                    output_uri=None,
                    started_at=None,
                    completed_at=now,
                    duration_seconds=None,
                    error_code="SOLVER_TIMEOUT",
                    error_message=(
                        f"wait_for_completion exceeded {timeout_s}s budget while "
                        f"polling s3://{runs_bucket}/{handle.run_id}/completion.json"
                    ),
                )

            await asyncio.sleep(poll_interval_s)

    except asyncio.CancelledError:
        # Invariant 8: docker kill + cancelled completion within ≤30 s, then
        # re-raise so emit_tool_call's mark_cancelled branch fires.
        logger.info(
            "wait_for_completion(local-docker) CANCELLED handle_id=%s; "
            "issuing docker kill %s",
            handle.handle_id,
            handle.run_id,
        )
        _request_local_cancel(handle.run_id)
        raise


# --------------------------------------------------------------------------- #
# run_solver
# --------------------------------------------------------------------------- #


_RUN_SOLVER_METADATA = AtomicToolMetadata(
    name="run_solver",
    ttl_class="live-no-cache",
    source_class="solver_dispatch",
    cacheable=False,
)


@register_tool(
    _RUN_SOLVER_METADATA,
    # Annotations: readOnlyHint=False (submits a Cloud Workflows execution
    # that ultimately writes output artifacts to the runs bucket),
    # openWorldHint=False (intra-GCP Cloud Workflows + Cloud Run only),
    # destructiveHint=False (writes go to a new runs/ prefix; no existing
    # state overwritten), idempotentHint=False (each call creates a new
    # Workflow execution with a distinct run_id).
    read_only_hint=False,
    open_world_hint=False,
    destructive_hint=False,
    idempotent_hint=False,
)
def run_solver(
    solver: str,
    model_setup_uri: str,
    compute_class: str = "medium",
    # job-0164: absorb LLM-invented kwargs (centralized at server.py via
    # tool_arg_normalizer, but kept as belt-and-suspenders).
    **_extra_ignored: Any,
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
        model_setup_uri: URI of the manifest the solver envelope will read
            (the job-0040 manifest schema: ``{"inputs":[...],
            "sfincs_args":[...], "outputs":[...]}``). ``gs://`` under the
            default gcp-workflows backend; ``s3://`` under
            ``GRACE2_SOLVER_BACKEND=local-docker`` (job-0291 — input URIs
            inside the manifest are resolved by scheme). Engine job-0042's
            ``model_flood_scenario`` workflow composes this from the M4
            atomic-tool substrate.
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
    if not isinstance(model_setup_uri, str) or not model_setup_uri:
        raise SolverDispatchError(
            f"model_setup_uri must be a non-empty string; got {model_setup_uri!r}"
        )

    # --- job-0291 backend seam: local-docker dispatch (AWS EC2). The default
    # gcp-workflows path below is byte-identical to pre-job-0291 behavior. ---
    if solver_backend() == SOLVER_BACKEND_LOCAL_DOCKER:
        return _run_solver_local_docker(
            solver=solver,
            model_setup_uri=model_setup_uri,
            compute_class=compute_class,
        )

    if not model_setup_uri.startswith("gs://"):
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


@register_tool(
    _WAIT_FOR_COMPLETION_METADATA,
    # Annotations: readOnlyHint=False (emits pipeline-state progress envelopes
    # as a side effect on every poll tick — stateful even though it does not
    # write GCS directly), openWorldHint=False (polls intra-GCP Cloud Workflows
    # execution status; no public external API), destructiveHint=False (reads
    # completion.json from runs bucket; does not overwrite anything),
    # idempotentHint=False (each call emits progress events; cancellation path
    # calls cancel_execution on the live workflow).
    read_only_hint=False,
    open_world_hint=False,
    destructive_hint=False,
    idempotent_hint=False,
)
async def wait_for_completion(
    handle: ExecutionHandle,
    poll_interval_s: int = DEFAULT_POLL_INTERVAL_S,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    # job-0164: absorb LLM-invented kwargs (centralized at server.py via
    # tool_arg_normalizer, but kept as belt-and-suspenders).
    **_extra_ignored: Any,
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

    # --- job-0291 backend seam: a local handle pins its backend (the
    # handle's workflow_name, not the env, decides — env churn between submit
    # and wait cannot mis-route the poll). ``local-exec`` (job-0292b, MODFLOW
    # direct-binary) shares the same S3 completion poll. GCP handles take the
    # Cloud Workflows poll below, byte-identical to pre-job-0291 behavior. ---
    if handle.workflow_name in _LOCAL_WORKFLOW_NAMES:
        return await _wait_for_completion_local(handle, poll_interval_s, timeout_s)

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
