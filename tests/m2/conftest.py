"""Pytest fixtures for the GRACE-2 M2 acceptance suite (job-0023).

This conftest is *additive* over ``tests/conftest.py`` (M1 from job-0017).
The M1 fixtures (``agent_subprocess``, ``atlas_srv``) stay untouched; this
file adds M2-specific fixtures that exercise the live cloud substrate
brought up in sprint-04:

* QGIS Server Cloud Run service (job-0018 + job-0024, image digest
  ``@sha256:a703476…``, WMS URL contract ``MAP=/mnt/qgs/<file>.qgs``).
* GCS bucket ``grace-2-hazard-prod-qgs`` (sample ``.qgs`` uploaded in
  job-0019; mutated by the worker in job-0021).
* Pub/Sub topic ``grace-2-worker-events`` (worker completion envelope,
  job-0020 + job-0021).
* Cloud Run Job ``grace-2-pyqgis-worker`` (job-0021 — image
  ``sha256:fffd7e0f…``, SA ``pyqgis-worker-runtime``).

Live-substrate fixtures are env-var-overridable so the suite can run
against a non-default deployment (e.g. a future staging URL) without
editing tests.

Markers
-------

* ``live_qgis_server`` — opt-out skip when ``GRACE2_QGIS_SERVER_URL`` is
  unset and the deployed default is unreachable (network-gated). The
  suite uses the deployed default by default.
* ``live_worker`` — opt-out skip when ``gcloud`` is missing or ADC is
  unavailable (auth-gated). The suite uses gcloud + ADC by default.
* ``live_tofu`` — opt-out skip when ``tofu`` CLI is unavailable.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

#: Default deployed QGIS Server URL — sourced from PROJECT_STATE.md and the
#: live ``gcloud run services describe grace-2-qgis-server`` value at the
#: time of job-0023. Override via ``GRACE2_QGIS_SERVER_URL`` for staging /
#: redeployed substrates.
DEFAULT_QGIS_SERVER_URL = "https://grace-2-qgis-server-pwvcfwv55q-uc.a.run.app"

#: Canonical sample ``.qgs`` path inside the QGIS Server container, served
#: via the gen2 GCS volume mount provisioned in job-0024 (read-only).
DEFAULT_SAMPLE_QGS_URI = "/mnt/qgs/grace2-sample.qgs"

#: Canonical GCS bucket holding the ``.qgs`` payloads (job-0018).
DEFAULT_QGS_BUCKET = "grace-2-hazard-prod-qgs"

#: GCP project (job-0014).
DEFAULT_GCP_PROJECT = "grace-2-hazard-prod"
DEFAULT_GCP_REGION = "us-central1"

#: Pub/Sub topic the worker publishes completion envelopes to (job-0018).
DEFAULT_PUBSUB_TOPIC = "grace-2-worker-events"

#: Cloud Run Job name (job-0021).
DEFAULT_WORKER_JOB_NAME = "grace-2-pyqgis-worker"


def _gcloud_path() -> str | None:
    """Return the absolute path of the ``gcloud`` CLI, or None if absent.

    Honors PATH, then falls back to ``~/tools/google-cloud-sdk/bin/gcloud``
    (the canonical install location on the dev box per PROJECT_STATE.md
    "Environment facts"). Used by the ``gcloud_bin`` fixture below.
    """
    p = shutil.which("gcloud")
    if p:
        return p
    fallback = Path.home() / "tools" / "google-cloud-sdk" / "bin" / "gcloud"
    if fallback.exists():
        return str(fallback)
    return None


def pytest_configure(config: pytest.Config) -> None:
    """Register M2-specific markers."""
    config.addinivalue_line(
        "markers",
        "live_qgis_server: real HTTP roundtrip to the deployed QGIS Server "
        "Cloud Run service (skipped automatically if the service is "
        "unreachable; opt-out via GRACE2_QGIS_SERVER_URL=skip).",
    )
    config.addinivalue_line(
        "markers",
        "live_worker: real Cloud Run Jobs execution + GCS + Pub/Sub "
        "(skipped automatically if gcloud or ADC is unavailable; "
        "opt-out via GRACE2_SKIP_LIVE_WORKER=1).",
    )
    config.addinivalue_line(
        "markers",
        "live_tofu: real ``tofu plan`` invocation against the GCS-backed "
        "state (skipped automatically if tofu CLI is unavailable; opt-out "
        "via GRACE2_SKIP_LIVE_TOFU=1).",
    )


@pytest.fixture(scope="session")
def qgis_server_url() -> str:
    """Return the deployed QGIS Server URL (env-var overridable).

    The default is the live URL captured during job-0023 verification.
    Set ``GRACE2_QGIS_SERVER_URL`` to redirect against a future staging
    deployment or a redeployed service.
    """
    return os.environ.get("GRACE2_QGIS_SERVER_URL", DEFAULT_QGIS_SERVER_URL).rstrip("/")


@pytest.fixture(scope="session")
def sample_qgs_uri() -> str:
    """Return the ``MAP=`` value the WMS URL contract expects for the
    canonical sample project (``/mnt/qgs/grace2-sample.qgs``).

    Per the job-0024 contract change, ``.qgs`` files are loaded via a
    runtime GCS volume mount at ``/mnt/qgs/`` rather than ``/vsigs/`` —
    the FR-QS-2 amendment proposal in flight.
    """
    return os.environ.get("GRACE2_SAMPLE_QGS_URI", DEFAULT_SAMPLE_QGS_URI)


@pytest.fixture(scope="session")
def qgs_bucket() -> str:
    """GCS bucket name holding the canonical ``.qgs`` payloads (job-0018)."""
    return os.environ.get("GRACE2_QGS_BUCKET", DEFAULT_QGS_BUCKET)


@pytest.fixture(scope="session")
def gcp_project() -> str:
    """GCP project id (job-0014)."""
    return os.environ.get("GOOGLE_CLOUD_PROJECT", DEFAULT_GCP_PROJECT)


@pytest.fixture(scope="session")
def gcp_region() -> str:
    """GCP region (job-0014/0018)."""
    return os.environ.get("GOOGLE_CLOUD_REGION", DEFAULT_GCP_REGION)


@pytest.fixture(scope="session")
def pubsub_topic() -> str:
    """Pub/Sub completion-notify topic name (job-0018)."""
    return os.environ.get("GRACE2_PUBSUB_TOPIC", DEFAULT_PUBSUB_TOPIC)


@pytest.fixture(scope="session")
def worker_job_name() -> str:
    """Cloud Run Job name for the PyQGIS worker (job-0021)."""
    return os.environ.get("GRACE2_WORKER_JOB_NAME", DEFAULT_WORKER_JOB_NAME)


@pytest.fixture(scope="session")
def gcloud_bin() -> str:
    """Return the gcloud binary path, or skip the test if unavailable."""
    p = _gcloud_path()
    if not p:
        pytest.skip(
            "gcloud CLI not found on PATH or at ~/tools/google-cloud-sdk/bin/ "
            "— layer: dev-env (PROJECT_STATE.md § Environment facts)."
        )
    return p


@pytest.fixture(scope="session")
def adc_available(gcloud_bin: str) -> bool:
    """Return True if Application Default Credentials are usable.

    Skips ``live_worker`` tests when ADC is missing (auth-gated). The
    check runs ``gcloud auth application-default print-access-token``;
    if that succeeds, ADC works for GCS + Pub/Sub.
    """
    try:
        out = subprocess.run(
            [gcloud_bin, "auth", "application-default", "print-access-token"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return out.returncode == 0 and bool(out.stdout.strip())
    except Exception:
        return False


@pytest.fixture(scope="session")
def repo_root_m2() -> Path:
    """Repo root (path to the GRACE-2 working tree)."""
    return REPO_ROOT


@pytest.fixture(scope="session")
def artifacts_dir() -> Path:
    """Directory where test artifacts (PNGs, XML transcripts) are written."""
    d = Path(__file__).resolve().parent / "artifacts"
    d.mkdir(parents=True, exist_ok=True)
    return d
