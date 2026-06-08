"""Publish-layer bridge smoke harness (job-0062).

Drives ``publish_layer`` unit-style against the deployed Cloud Run Jobs
substrate (grace-2-pyqgis-worker) without triggering a full SFINCS run.

Two modes:
  1. DRY-RUN (default) — uses mock client; verifies helper functions and
     DI seams resolve correctly without touching GCP.  Safe on any machine.
  2. LIVE (``--live``) — uses the real Google Cloud Run Jobs v2 client and
     fires the actual ``grace-2-pyqgis-worker`` job with a seed flood-depth
     COG from ``gs://grace-2-hazard-prod-runs/smoke-seed/``.

Run (dry-run, always works):

    .venv-agent/bin/python \
      reports/inflight/job-0062-engine-20260607/evidence/smoke_demo.py

Run (live — requires gcloud auth and the deployed Cloud Run Job):

    GOOGLE_CLOUD_PROJECT=grace-2-hazard-prod \
      .venv-agent/bin/python \
      reports/inflight/job-0062-engine-20260607/evidence/smoke_demo.py --live

Outputs:
    smoke_demo_envelope.json — summary dict with outcome and WMS URL (if live).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "grace-2-hazard-prod")
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
QGIS_SERVER_URL = os.environ.get(
    "GRACE2_QGIS_SERVER_URL",
    "https://grace-2-qgis-server-425352658356.us-central1.run.app/ogc/wms",
)
DEFAULT_QGS_URI = os.environ.get(
    "GRACE2_DEFAULT_QGS_URI",
    "gs://grace-2-hazard-prod-qgs/grace2-sample.qgs",
)
PYQGIS_JOB_NAME = os.environ.get(
    "GRACE2_PYQGIS_WORKER_JOB_NAME",
    "grace-2-pyqgis-worker",
)
# Seed COG: a small pre-baked flood-depth raster uploaded to the prod bucket
# for smoke testing (does not require a real SFINCS run).
SEED_RASTER_URI = os.environ.get(
    "GRACE2_SMOKE_RASTER_URI",
    "gs://grace-2-hazard-prod-runs/smoke-seed/flood_depth_peak.tif",
)
SEED_LAYER_ID = "smoke-flood-depth-peak-seed"

EVIDENCE_DIR = Path(__file__).parent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("smoke_demo_0062")

os.environ.setdefault("GOOGLE_CLOUD_PROJECT", PROJECT)
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", LOCATION)


def _make_mock_jobs_client() -> MagicMock:
    """Return a mock Cloud Run Jobs v2 client that simulates a SUCCEEDED execution."""
    execution = MagicMock()
    execution.reconciling = False
    cond = MagicMock()
    cond.type_ = "Completed"
    cond.state.name = "CONDITION_SUCCEEDED"
    execution.conditions = [cond]

    operation = MagicMock()
    operation.result.return_value = execution

    client = MagicMock()
    client.run_job.return_value = operation
    return client


def _run_dry_run() -> dict:
    """Verify helper functions and DI seams without touching GCP."""
    from grace2_agent.main import _import_tools_registry

    n_tools = _import_tools_registry()
    log.info("registered %d agent tools", n_tools)

    from grace2_agent.tools.publish_layer import (
        _build_wms_url,
        _gs_to_vsigs,
        _parse_qgs_key,
        publish_layer,
        set_default_qgs_uri,
        set_gcp_location,
        set_gcp_project,
        set_jobs_client,
        set_pyqgis_worker_job_name,
        set_qgis_server_url,
    )

    # Verify helpers independently.
    vsigs = _gs_to_vsigs("gs://bucket/path/to/file.tif")
    assert vsigs == "/vsigs/bucket/path/to/file.tif", f"_gs_to_vsigs: {vsigs}"
    log.info("_gs_to_vsigs OK: %s", vsigs)

    qgs_key = _parse_qgs_key("gs://grace-2-hazard-prod-qgs/grace2-sample.qgs")
    assert qgs_key == "grace2-sample.qgs", f"_parse_qgs_key: {qgs_key}"
    log.info("_parse_qgs_key OK: %s", qgs_key)

    set_qgis_server_url(QGIS_SERVER_URL)
    wms_url = _build_wms_url("grace2-sample.qgs", SEED_LAYER_ID)
    expected = f"{QGIS_SERVER_URL}?MAP=/mnt/qgs/grace2-sample.qgs&LAYERS={SEED_LAYER_ID}"
    assert wms_url == expected, f"_build_wms_url: {wms_url}"
    log.info("_build_wms_url OK: %s", wms_url)
    set_qgis_server_url(None)

    # Drive publish_layer with a mock client.
    mock_client = _make_mock_jobs_client()
    set_jobs_client(mock_client)
    set_qgis_server_url(QGIS_SERVER_URL)
    set_default_qgs_uri(DEFAULT_QGS_URI)
    set_gcp_project(PROJECT)
    set_gcp_location(LOCATION)
    set_pyqgis_worker_job_name(PYQGIS_JOB_NAME)

    try:
        start = time.monotonic()
        result_url = publish_layer(
            layer_uri=SEED_RASTER_URI,
            layer_id=SEED_LAYER_ID,
            style_preset="continuous_flood_depth",
        )
        elapsed = time.monotonic() - start
    finally:
        set_jobs_client(None)
        set_qgis_server_url(None)
        set_default_qgs_uri(None)
        set_gcp_project(None)
        set_gcp_location(None)
        set_pyqgis_worker_job_name(None)

    log.info("publish_layer (dry-run) returned: %s in %.3fs", result_url, elapsed)

    assert "MAP=" in result_url, f"WMS URL missing MAP= param: {result_url}"
    assert "LAYERS=" in result_url, f"WMS URL missing LAYERS= param: {result_url}"
    assert SEED_LAYER_ID in result_url, f"WMS URL missing layer_id: {result_url}"

    return {
        "mode": "dry-run",
        "outcome": "SUCCESS",
        "wms_url": result_url,
        "elapsed_seconds": elapsed,
        "helpers_verified": {
            "gs_to_vsigs": vsigs,
            "parse_qgs_key": qgs_key,
            "build_wms_url": wms_url,
        },
        "substrate_verification": (
            "Dry-run: mock Cloud Run Jobs client returned CONDITION_SUCCEEDED; "
            "helpers verified; DI seams wire correctly. "
            "Live run requires --live flag and gcloud auth."
        ),
    }


def _run_live() -> dict:
    """Drive the real Cloud Run Jobs v2 client against the deployed substrate."""
    try:
        from google.cloud import run_v2
    except ImportError as exc:
        log.error("google-cloud-run not installed: %s", exc)
        return {"mode": "live", "outcome": "SKIPPED", "reason": str(exc)}

    from grace2_agent.main import _import_tools_registry

    n_tools = _import_tools_registry()
    log.info("registered %d agent tools", n_tools)

    from grace2_agent.tools.publish_layer import (
        PublishLayerError,
        publish_layer,
        set_default_qgs_uri,
        set_gcp_location,
        set_gcp_project,
        set_jobs_client,
        set_pyqgis_worker_job_name,
        set_qgis_server_url,
    )

    jobs_client = run_v2.JobsClient()
    set_jobs_client(jobs_client)
    set_qgis_server_url(QGIS_SERVER_URL)
    set_default_qgs_uri(DEFAULT_QGS_URI)
    set_gcp_project(PROJECT)
    set_gcp_location(LOCATION)
    set_pyqgis_worker_job_name(PYQGIS_JOB_NAME)

    try:
        start = time.monotonic()
        wms_url = publish_layer(
            layer_uri=SEED_RASTER_URI,
            layer_id=SEED_LAYER_ID,
            style_preset="continuous_flood_depth",
        )
        elapsed = time.monotonic() - start
        log.info("LIVE publish_layer succeeded: %s in %.2fs", wms_url, elapsed)
        outcome = "SUCCESS"
        error_info = None
    except PublishLayerError as exc:
        elapsed = time.monotonic() - start
        wms_url = None
        outcome = f"PUBLISH_LAYER_ERROR:{exc.error_code}"
        error_info = str(exc)
        log.warning("LIVE publish_layer raised %s: %s", exc.error_code, exc)
    finally:
        set_jobs_client(None)
        set_qgis_server_url(None)
        set_default_qgs_uri(None)
        set_gcp_project(None)
        set_gcp_location(None)
        set_pyqgis_worker_job_name(None)

    summary: dict = {
        "mode": "live",
        "outcome": outcome,
        "wms_url": wms_url,
        "elapsed_seconds": elapsed,
        "gcp_project": PROJECT,
        "gcp_location": LOCATION,
        "job_name": PYQGIS_JOB_NAME,
        "seed_raster_uri": SEED_RASTER_URI,
    }
    if error_info:
        summary["error"] = error_info

    if wms_url:
        summary["substrate_verification"] = (
            f"Cloud Run Job {PYQGIS_JOB_NAME} executed successfully; "
            f"PyQGIS worker appended raster layer to .qgs and returned WMS URL. "
            f"M6 bridge wired: COG → QGIS Server rendering."
        )
    else:
        summary["substrate_verification"] = (
            f"Cloud Run Job dispatch or execution failed with {outcome}. "
            f"Check OQ list in report.md for known blockers "
            f"(SA permissions, seed COG availability)."
        )

    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="job-0062 publish-layer smoke harness")
    parser.add_argument("--live", action="store_true", help="Run against real GCP substrate")
    args = parser.parse_args(argv)

    if args.live:
        log.info("==== LIVE smoke: publish_layer → Cloud Run Jobs ====")
        summary = _run_live()
    else:
        log.info("==== DRY-RUN smoke: publish_layer (mock client) ====")
        summary = _run_dry_run()

    out_path = EVIDENCE_DIR / "smoke_demo_envelope.json"
    out_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    log.info("smoke transcript written to %s", out_path)
    log.info("outcome: %s", summary.get("outcome"))
    if wms := summary.get("wms_url"):
        log.info("wms_url: %s", wms)
    return 0


if __name__ == "__main__":
    sys.exit(main())
