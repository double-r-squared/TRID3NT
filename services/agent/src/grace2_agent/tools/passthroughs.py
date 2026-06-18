"""Registry pass-through atomic tool (job-0032, M4 substrate).

This module registers ``qgis_process``: a pass-through to the PyQGIS worker
invocation path established by job-0021 (Cloud Run Jobs submission, later the
job-0308 AWS docker stage-then-mount path). Solver dispatch is
uncacheable-by-construction per FR-DC-6 — results land under
``gs://<bucket>/runs/<run_id>/`` per FR-CE-4, not under ``cache/``.

The tool declares:

    ttl_class = "live-no-cache"
    cacheable = False
    source_class = None  # uncacheable; no bucket prefix

per FR-DC-6's "Solver dispatchers and their result fetches" enumeration entry.

(A ``mongo_query`` pass-through formerly lived here for the MongoDB Atlas/MCP
path. Atlas was torn down; the tool was an unbound stub that only ever raised
"MCP client not bound", so it was removed to stop the model from picking a
dead tool.)
"""

from __future__ import annotations

import logging
from typing import Any

from grace2_contracts.tool_registry import AtomicToolMetadata

from . import register_tool

__all__ = ["qgis_process"]

logger = logging.getLogger("grace2_agent.tools.passthroughs")


# Module-level handle for dependency injection. Production wiring sets this
# at startup; tests overwrite it with a stub. Kept as module-level so the
# registered function stays zero-arg-bindable from ADK's perspective.
_WORKER_SUBMITTER: Any | None = None


def set_worker_submitter(submitter: Any) -> None:
    """Bind the Cloud Run Jobs submitter used by ``qgis_process`` at call time.

    The submitter is a callable matching the worker-side API established by
    job-0021; binding it here keeps Cloud Run Jobs SDK imports out of this
    module's import graph (so tests can exercise the registry without GCP
    libs installed).
    """
    global _WORKER_SUBMITTER
    _WORKER_SUBMITTER = submitter


# ---------------------------------------------------------------------------
# qgis_process RUN substrate (job-0308, sprint-16, Decision Q).
#
# Execution mirrors the SFINCS solver's local-docker stage-then-mount pattern:
# stage s3:// input params into a host rundir, mount it into the grace2-qgis
# container, `qgis_process run <alg> --PARAM=…`, upload OUTPUT* artifacts back
# to s3://<runs>/runs/<run_id>/. The host stages via boto3, so no GDAL-/vsis3/-
# in-container credential problem (the recurring instance-role lesson).
# ---------------------------------------------------------------------------


def _stage_qgis_input(value: Any, rundir: str) -> str | None:
    """Download an s3://|gs:// input param into ``rundir``; return the container
    path ``/data/<basename>``. Return None for non-URI values (literals)."""
    import os

    if not (isinstance(value, str) and value.startswith(("s3://", "gs://"))):
        return None
    from .cache import read_object_bytes_s3

    base = os.path.basename(value.split("?")[0]) or "input.dat"
    with open(os.path.join(rundir, base), "wb") as fh:
        fh.write(read_object_bytes_s3(value))
    return f"/data/{base}"


def _build_qgis_run_args(
    params: dict, rundir: str, stager: Any
) -> tuple[list[str], dict[str, str]]:
    """Translate a ``params`` dict into ``qgis_process run`` CLI args (pure;
    ``stager`` injected for testability).

    - s3://|gs:// values → staged via ``stager(value, rundir)`` and rewritten to
      the in-container path.
    - keys starting with ``OUTPUT`` → output sinks ``/data/<key><ext>`` (ext from
      the agent-provided value if it has one, else ``.tif``); collected for upload.
    - everything else → literal ``--KEY=VALUE`` (numbers / strings / enums / bools).

    Returns ``(cli_args, {param_key: output_basename})``.
    """
    import os

    cli_args: list[str] = []
    outputs: dict[str, str] = {}
    for k, v in (params or {}).items():
        staged = stager(v, rundir)
        if staged is not None:
            cli_args.append(f"--{k}={staged}")
            continue
        if str(k).upper().startswith("OUTPUT"):
            ext = os.path.splitext(str(v))[1] if isinstance(v, str) else ""
            outname = f"{str(k).lower()}{ext or '.tif'}"
            outputs[k] = outname
            cli_args.append(f"--{k}=/data/{outname}")
            continue
        cli_args.append(f"--{k}={v}")
    return cli_args, outputs


def _run_qgis_process_docker(
    algorithm: str, params: dict, image: str, timeout_s: int
) -> dict[str, Any]:
    """Stage → `docker run -v rundir:/data <image> qgis_process run` → upload."""
    import os
    import shutil
    import subprocess
    import tempfile
    import time
    from pathlib import Path

    from grace2_contracts import new_ulid

    run_id = new_ulid()
    rundir = tempfile.mkdtemp(prefix="qgisproc-")
    try:
        cli_args, output_keys = _build_qgis_run_args(params, rundir, _stage_qgis_input)
        cmd = [
            "docker", "run", "--rm", "-v", f"{rundir}:/data",
            "-e", "QT_QPA_PLATFORM=offscreen", image,
            "qgis_process", "run", algorithm, *cli_args,
        ]
        start = time.monotonic()
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout_s, check=False)
        dur = time.monotonic() - start
        stdout = proc.stdout.decode("utf-8", errors="replace")
        stderr = proc.stderr.decode("utf-8", errors="replace")
        outputs: dict[str, str] = {}
        if proc.returncode == 0 and output_keys:
            from .solver import _get_s3_client, _upload_file_s3

            bucket = (os.environ.get("GRACE2_RUNS_BUCKET") or "").strip()
            if not bucket:
                raise RuntimeError(
                    "GRACE2_RUNS_BUCKET must be set for qgis_process output upload"
                )
            s3 = _get_s3_client()
            for key, outname in output_keys.items():
                p = Path(rundir) / outname
                if p.exists() and p.stat().st_size > 0:
                    outputs[key] = _upload_file_s3(
                        s3, p, bucket, f"runs/{run_id}/{outname}"
                    )
        return {
            "status": "succeeded" if proc.returncode == 0 else "failed",
            "tool": "qgis_process",
            "algorithm": algorithm,
            "run_id": run_id,
            "outputs": outputs,
            "returncode": proc.returncode,
            "duration_s": round(dur, 2),
            "stdout_tail": stdout[-2000:],
            "stderr_tail": "" if proc.returncode == 0 else stderr[-1500:],
        }
    finally:
        shutil.rmtree(rundir, ignore_errors=True)


# ---------------------------------------------------------------------------
# qgis_process (registered tool)
# ---------------------------------------------------------------------------


@register_tool(
    AtomicToolMetadata(
        name="qgis_process",
        ttl_class="live-no-cache",
        source_class=None,
        cacheable=False,
    ),
    # Annotations: readOnlyHint=False (dispatches Cloud Run Job → writes runs/
    # bucket), openWorldHint=False (intra-GCP Cloud Run only),
    # destructiveHint=False (outputs land in a new run dir; existing state
    # is not overwritten), idempotentHint=False (each dispatch starts a new
    # execution with a new run_id).
    read_only_hint=False,
    open_world_hint=False,
    destructive_hint=False,
    idempotent_hint=False,
)
def qgis_process(
    algorithm: str,
    params: dict[str, Any],
    # job-0164: absorb LLM-invented kwargs (centralized at server.py via
    # tool_arg_normalizer, but kept as belt-and-suspenders).
    **_extra_ignored: Any,
) -> dict[str, Any]:
    """Submit a PyQGIS Processing algorithm for execution on the worker.

    Use this when: the agent needs to run a QGIS Processing algorithm
    (vector / raster / GDAL / GRASS / SAGA / plugin) that maps to one
    discovered via ``list_qgis_algorithms`` / ``describe_qgis_algorithm``.
    The worker runs the algorithm and persists outputs under
    ``gs://<bucket>/runs/<run_id>/`` per FR-CE-4.

    Do NOT use this for: solver runs that have a dedicated workflow
    (``run_sfincs_solver``, ``run_pelicun_impact``, etc. — those go through
    their own dispatchers); render-only requests (use the layer-style /
    map-command path).

    Params:
        algorithm: QGIS algorithm id (e.g. ``"native:reprojectlayer"``).
        params: algorithm parameters as a JSON-serializable dict.

    Returns:
        A dict carrying the worker's ``ExecutionHandle`` (run_id, output
        URIs, status). Shape comes from
        ``grace2_contracts.execution.ExecutionHandle`` once wired.

    FR-DC-6: This tool is uncacheable-by-construction (solver / dispatcher
    outputs live under ``runs/`` not ``cache/``); the cache shim is
    deliberately bypassed.
    """
    import os
    import shutil

    if not isinstance(params, dict):
        params = {}
    logger.info(
        "qgis_process algorithm=%s param_keys=%s", algorithm, sorted(params.keys())
    )

    # AWS path (Decision Q / job-0308): run inside the grace2-qgis container via
    # stage-then-mount. Engages when an image is configured OR when no local
    # qgis_process exists but docker + the image are present (the EC2 box).
    image = os.environ.get("GRACE2_QGIS_DOCKER_IMAGE")
    if not image and shutil.which("qgis_process") is None and shutil.which("docker"):
        image = "grace2-qgis:ltr"
    if image:
        return _run_qgis_process_docker(algorithm, params, image, timeout_s=1800)

    # Dev fallback: local qgis_process on PATH (params are local paths; no S3).
    if shutil.which("qgis_process") is None:
        raise RuntimeError(
            "qgis_process unavailable: set GRACE2_QGIS_DOCKER_IMAGE (docker path), "
            "ensure docker + the grace2-qgis image are present, or install "
            "qgis_process on PATH."
        )
    import subprocess
    import time

    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    cli_args = [f"--{k}={v}" for k, v in params.items()]
    start = time.monotonic()
    proc = subprocess.run(
        ["qgis_process", "run", algorithm, *cli_args],
        capture_output=True, timeout=1800, check=False, env=env,
    )
    out = proc.stdout.decode("utf-8", errors="replace")
    return {
        "status": "succeeded" if proc.returncode == 0 else "failed",
        "tool": "qgis_process",
        "algorithm": algorithm,
        "returncode": proc.returncode,
        "duration_s": round(time.monotonic() - start, 2),
        "stdout_tail": out[-2000:],
        "stderr_tail": "" if proc.returncode == 0 else proc.stderr.decode("utf-8", "replace")[-1500:],
    }
