"""Pytest fixtures for the GRACE-2 M5 acceptance suite (job-0043).

This conftest is *additive* over ``tests/conftest.py`` (M1) — the
``agent_subprocess`` + ``free_port`` fixtures from M1 are reused for the
real-agent WS round-trips that drive the Hurricane Ian / Fort Myers
demo through the ``run_model_flood_scenario`` workflow-wrapper tool.

Owned fixtures
--------------

* ``gcs_storage_client``: returns a configured ``google.cloud.storage.Client``
  authed via ADC for the live cache + runs bucket round-trip checks.
  Returns ``None`` when ``google-cloud-storage`` is missing OR ADC is
  unavailable — tests self-qualify rather than silently pass.
* ``cache_bucket_name`` / ``runs_bucket_name``: the production bucket
  names (``grace-2-hazard-prod-cache`` / ``grace-2-hazard-prod-runs``);
  override via ``GRACE2_CACHE_BUCKET`` / ``GRACE2_RUNS_BUCKET`` for a
  non-prod run.
* ``hurricane_ian_fort_myers_demo``: parsed
  ``tests/m5/fixtures/hurricane_ian_fort_myers.json`` with the pinned
  Fort Myers bbox + 100-yr / 24-hr Atlas 14 design-storm parameters used
  to anchor the Hurricane Ian flood scenario (ATCF integration deferred
  per OQ-42-ATCF-HURRICANE-IAN-INTEGRATION).

Markers
-------

* ``live_m5``: requires the live M5 substrate — agent service +
  GCS cache + Cloud Workflows orchestrator + Nominatim/3DEP/NHDPlus HR/
  Atlas 14/MRLC WCS. Auto-skipped under default ``make test`` collection;
  opt-in via ``make test-m5`` or ``-m live_m5``.

Boundary discipline (testing.md)
--------------------------------

The M5 demo drives the **real agent service** (subprocess via
``agent_subprocess``) using real Appendix-A WebSocket envelopes. The
``/invoke <tool> <json>`` debug directive job-0035 landed is used to
fire ``run_model_flood_scenario`` end-to-end — bypassing Gemini's
function-calling layer but exercising the real
``workflows.model_flood_scenario`` composition chain through the real
``PipelineEmitter`` -> ``TOOL_REGISTRY[name].fn`` seam.

The cache writes hit the **real GCS bucket** (the fetchers' ``read_through``
shim). The live ``run_solver`` path submits to the **real
Cloud Workflows orchestrator** unless ``build_sfincs_model`` short-circuits
via ``HYDROMT_UNAVAILABLE`` (the substrate-vs-output qualification the
kickoff accepts on the Debian dev box).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "live_m5: requires the live M5 substrate (agent + GCS cache + "
        "Cloud Workflows + Nominatim/3DEP/NHDPlus HR/Atlas 14/MRLC WCS). "
        "Opt-in via `make test-m5` or `-m live_m5`.",
    )


@pytest.fixture(scope="session")
def cache_bucket_name() -> str:
    return os.environ.get("GRACE2_CACHE_BUCKET", "grace-2-hazard-prod-cache")


@pytest.fixture(scope="session")
def runs_bucket_name() -> str:
    return os.environ.get("GRACE2_RUNS_BUCKET", "grace-2-hazard-prod-runs")


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
def hurricane_ian_fort_myers_demo() -> dict:
    raw = (FIXTURES / "hurricane_ian_fort_myers.json").read_text()
    return json.loads(raw)
