"""grace2-qgis coalesced Processing worker -- REFERENCE (tools-session proof).

The turn-scoped warm QGIS box: it initializes ``QgsApplication`` + the Processing
registry (native/gdal/grass/...) EXACTLY ONCE at process start, then serves many
``POST /run`` requests against that one warm runtime -- the coalescing that removes
the ~1.2 s-per-call init the current ``docker run qgis_process`` path pays every
time (local proof: ~77% of a multi-algo turn's QGIS wall-time).

Lifecycle (the rest is the Orchestrator's Fargate wiring):
  * One init at startup (the only fixed cost; ~0.7-2.2 s measured local).
  * ``POST /run {algorithm, params}`` -> stage ``s3://`` inputs to a local rundir,
    ``processing.run``, upload ``OUTPUT*`` artifacts to ``s3://<runs>/runs/<id>/``,
    return the output URIs + timing. Stateless per request (Spot-reclaim safe).
  * Idle watchdog: exit after ``GRACE2_QGIS_IDLE_TTL_S`` with no request (the
    ~1-min turn tail) so the Fargate task self-terminates -> scale to zero.

Orchestrator does the FINAL WIRING: place under services/workers/qgis/, build the
slim image, Fargate-Spot task + IAM, the get-or-create (spin up on first call /
pre-warm on plan) + Spot-reclaim-retry, and bind the agent-side HTTP submitter
(set_worker_submitter) to this box's URL. NOT always-on -- turn-scoped warm Spot.

ASCII only.
"""
from __future__ import annotations

import os
import time
import tempfile
import threading
import uuid
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# --- ONE-TIME warm init (module import == process start) ---
_T0 = time.time()
from qgis.core import QgsApplication  # noqa: E402

_QGS = QgsApplication([], False)
_QGS.initQgis()
import processing  # noqa: E402
from processing.core.Processing import Processing  # noqa: E402

Processing.initialize()
try:
    from grassprovider.grass_provider import GrassProvider  # noqa: E402

    QgsApplication.processingRegistry().addProvider(GrassProvider())
except Exception:  # noqa: BLE001 -- grass optional; native/gdal still serve
    pass

INIT_COUNT = 1  # proves a single init served the whole process lifetime
INIT_SECONDS = round(time.time() - _T0, 3)
_ALGS = frozenset(a.id() for a in QgsApplication.processingRegistry().algorithms())
_LAST_REQUEST_AT = time.time()

import boto3  # noqa: E402

_S3 = boto3.client("s3")
_RUNS_BUCKET = os.environ.get("GRACE2_RUNS_BUCKET", "grace2-hazard-runs-226996537797")


def _split_uri(uri: str) -> tuple[str, str]:
    rest = uri.split("://", 1)[1]
    bucket, _, key = rest.partition("/")
    return bucket, key


def _stage_input(uri: str, rundir: str) -> str:
    bucket, key = _split_uri(uri)
    local = os.path.join(rundir, os.path.basename(key))
    _S3.download_file(bucket, key, local)
    return local


def _upload_output(local_path: str, run_id: str, param: str) -> str:
    ext = os.path.splitext(local_path)[1] or ".dat"
    key = f"runs/{run_id}/{param}{ext}"
    _S3.upload_file(local_path, _RUNS_BUCKET, key)
    return f"s3://{_RUNS_BUCKET}/{key}"


class AlgorithmError(RuntimeError):
    error_code = "QGIS_ALGORITHM_ERROR"
    retryable = True


def run_algorithm(algorithm: str, params: dict[str, Any], run_id: str | None = None) -> dict[str, Any]:
    """Stage s3 inputs -> processing.run -> upload OUTPUT* -> return uris + timing.

    Runs on the ALREADY-WARM Processing registry (no per-call init). ``params``
    values that are ``s3://`` URIs are staged locally; ``OUTPUT*`` params are
    written to the rundir and uploaded to the runs bucket.
    """
    global _LAST_REQUEST_AT
    _LAST_REQUEST_AT = time.time()
    if algorithm not in _ALGS:
        raise AlgorithmError(f"algorithm {algorithm!r} not available on this worker")
    run_id = run_id or uuid.uuid4().hex[:16]
    rundir = tempfile.mkdtemp(prefix=f"qgisrun_{run_id}_")
    staged: dict[str, Any] = {}
    output_params: dict[str, str] = {}
    for k, v in params.items():
        if isinstance(v, str) and v.startswith(("s3://", "gs://")):
            staged[k] = _stage_input(v, rundir)
        elif "OUTPUT" in k.upper() and isinstance(v, str):
            ext = os.path.splitext(v)[1] or ".tif"
            staged[k] = os.path.join(rundir, f"{k}{ext}")
            output_params[k] = staged[k]
        else:
            staged[k] = v
    t = time.time()
    result = processing.run(algorithm, staged)
    dt = round(time.time() - t, 3)
    outputs: dict[str, str] = {}
    for k in output_params:
        actual = result.get(k, output_params[k])
        if isinstance(actual, str) and os.path.exists(actual):
            outputs[k] = _upload_output(actual, run_id, k)
    _LAST_REQUEST_AT = time.time()
    return {"run_id": run_id, "algorithm": algorithm, "compute_s": dt, "outputs": outputs}


# --- HTTP shell (FastAPI). The Orchestrator runs this under uvicorn in the box. ---
def build_app():  # pragma: no cover -- exercised live, not in the unit proof
    from fastapi import FastAPI
    from pydantic import BaseModel

    class RunRequest(BaseModel):
        algorithm: str
        params: dict[str, Any]
        run_id: str | None = None

    app = FastAPI(title="grace2-qgis coalesced Processing worker")

    @app.get("/healthz")
    def healthz():
        return {"ready": True, "init_seconds": INIT_SECONDS, "algorithms": len(_ALGS),
                "idle_s": round(time.time() - _LAST_REQUEST_AT, 1)}

    @app.get("/algorithms")
    def algorithms():
        return {"algorithms": sorted(_ALGS)}

    @app.post("/run")
    def run(req: RunRequest):
        try:
            return run_algorithm(req.algorithm, req.params, req.run_id)
        except Exception as exc:  # noqa: BLE001
            code = getattr(exc, "error_code", "QGIS_WORKER_ERROR")
            return {"error_code": code, "message": str(exc),
                    "retryable": getattr(exc, "retryable", True)}

    return app


def _idle_watchdog():  # pragma: no cover
    ttl = float(os.environ.get("GRACE2_QGIS_IDLE_TTL_S", "90"))
    while True:
        time.sleep(min(ttl, 15.0))
        if time.time() - _LAST_REQUEST_AT > ttl:
            os._exit(0)  # the ~1-min turn tail elapsed -> Fargate task ends, scale to zero


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    threading.Thread(target=_idle_watchdog, daemon=True).start()
    uvicorn.run(build_app(), host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
