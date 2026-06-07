"""Pytest fixtures for the GRACE-2 M4 acceptance suite (job-0036).

This conftest is *additive* over ``tests/conftest.py`` (M1) — the
``agent_subprocess`` + ``free_port`` fixtures from M1 are reused for the
real-agent WS round-trips that drive the Fort Myers demo.

Owned fixtures
--------------

* ``gcs_storage_client``: returns a configured ``google.cloud.storage.Client``
  authed via ADC for the live cache bucket round-trip checks. Returns ``None``
  when ``google-cloud-storage`` is missing OR ADC is unavailable — tests
  self-qualify rather than silently pass.
* ``cache_bucket_name``: the production cache bucket name
  (``grace-2-hazard-prod-cache``); override via ``GRACE2_CACHE_BUCKET`` for a
  non-prod run.
* ``fort_myers_expected``: parsed ``tests/m4/fixtures/expected_fort_myers.json``
  with the pinned Nominatim bbox and demo-query parameters.
* ``qgis_process_binary``: path to a local ``qgis_process`` binary (resolves
  via ``shutil.which`` against PATH first, then the well-known conda env
  path from PROJECT_STATE.md). Returns ``None`` when neither is present so
  the demo test can self-qualify the qgis_process leg.

Markers
-------

* ``live_m4``: requires the live M4 substrate (agent service + GCS cache
  bucket + Nominatim reachable). Auto-skipped under default ``make test``
  collection; opt-in via ``make test-m4`` or ``-m live_m4``.
* ``live_qgis_process``: requires a local ``qgis_process`` binary
  (PROJECT_STATE Environment facts § ``grace2`` conda env, Mac-local in the
  original env). Auto-qualifies on this Debian box where no binary is
  installed.

Boundary discipline (testing.md)
--------------------------------

The M4 demo drives the **real agent service** (subprocess via
``agent_subprocess``) using real Appendix-A WebSocket envelopes. The
``/invoke <tool> <json>`` debug directive job-0035 landed is used as the
tool-invocation path — it bypasses the M4-follow-up Gemini function-calling
work but exercises the real ``PipelineEmitter`` -> ``TOOL_REGISTRY[name].fn``
seam end-to-end. The cache writes hit the **real GCS bucket** (read after
write) — no fake storage in this suite.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "live_m4: requires the live M4 substrate (agent + GCS cache bucket + "
        "Nominatim). Opt-in via `make test-m4` or `-m live_m4`.",
    )
    config.addinivalue_line(
        "markers",
        "live_qgis_process: requires a local `qgis_process` binary. "
        "Auto-qualifies on machines where the grace2 conda env is absent.",
    )


@pytest.fixture(scope="session")
def cache_bucket_name() -> str:
    return os.environ.get("GRACE2_CACHE_BUCKET", "grace-2-hazard-prod-cache")


@pytest.fixture(scope="session")
def gcs_storage_client():
    """Return an ADC-authed GCS Client, or None when unreachable.

    Tests check for None and self-qualify their result rather than failing.
    """
    try:
        from google.cloud import storage  # type: ignore[import-not-found]
    except Exception:
        return None
    try:
        return storage.Client()
    except Exception:
        return None


@pytest.fixture(scope="session")
def fort_myers_expected() -> dict:
    raw = (FIXTURES / "expected_fort_myers.json").read_text()
    return json.loads(raw)


@pytest.fixture(scope="session")
def qgis_process_binary() -> str | None:
    """Return a path to a local `qgis_process` binary if reachable.

    Lookup order:
    1. ``GRACE2_QGIS_PROCESS_BINARY`` env override.
    2. ``shutil.which("qgis_process")`` against the running PATH.
    3. ``~/miniforge3/envs/grace2/bin/qgis_process`` (PROJECT_STATE.md
       canonical Mac-local conda env from job-0022).

    Returns ``None`` when none of these are present — the Debian dev box
    used to run sprint-06 does not have this binary; the substrate is
    Cloud Run Jobs in production. Tests that need it self-qualify.
    """
    env_override = os.environ.get("GRACE2_QGIS_PROCESS_BINARY")
    if env_override and Path(env_override).exists():
        return env_override
    found = shutil.which("qgis_process")
    if found:
        return found
    fallback = Path.home() / "miniforge3" / "envs" / "grace2" / "bin" / "qgis_process"
    if fallback.exists():
        return str(fallback)
    return None
