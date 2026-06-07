"""Unit tests for the ``publish_layer`` atomic tool (job-0062).

Coverage:
1. ``test_publish_layer_registered`` — tool appears in TOOL_REGISTRY with
   the correct metadata (cacheable=False, ttl_class="live-no-cache",
   source_class="publish_layer").
2. ``test_publish_layer_returns_wms_url`` — with the Cloud Run Jobs client
   mocked, ``publish_layer`` returns the expected WMS URL.
3. ``test_publish_layer_raises_on_dispatch_failure`` — when
   ``jobs_client.run_job`` raises, ``publish_layer`` raises
   ``PublishLayerError`` with error_code ``WORKER_JOB_DISPATCH_FAILED``.
4. ``test_publish_layer_raises_on_worker_failure`` — when the LRO result
   yields a FAILED execution state, ``publish_layer`` raises
   ``PublishLayerError`` with error_code ``WORKER_JOB_FAILED``.
5. ``test_publish_layer_gs_to_vsigs_conversion`` — ``_gs_to_vsigs`` converts
   ``gs://`` URIs to ``/vsigs/`` correctly.
6. ``test_publish_layer_wms_url_format`` — ``_build_wms_url`` produces the
   MAP= + LAYERS= query string matching the Map.tsx convention.
7. ``test_publish_layer_qgs_key_parsing`` — ``_parse_qgs_key`` extracts the
   correct key from ``gs://bucket/path/to/file.qgs``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from grace2_agent.tools import TOOL_REGISTRY
from grace2_agent.tools.publish_layer import (
    PublishLayerError,
    _build_wms_url,
    _gs_to_vsigs,
    _parse_qgs_key,
    publish_layer,
    set_jobs_client,
    set_qgis_server_url,
    set_default_qgs_uri,
    set_gcp_project,
    set_gcp_location,
    set_pyqgis_worker_job_name,
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _make_succeeded_execution() -> MagicMock:
    """Return a mock Cloud Run v2 Execution in SUCCEEDED state."""
    execution = MagicMock()
    execution.reconciling = False
    cond = MagicMock()
    cond.type_ = "Completed"
    cond.state.name = "CONDITION_SUCCEEDED"
    execution.conditions = [cond]
    return execution


def _make_failed_execution() -> MagicMock:
    """Return a mock Cloud Run v2 Execution in FAILED state."""
    execution = MagicMock()
    execution.reconciling = False
    cond = MagicMock()
    cond.type_ = "Completed"
    cond.state.name = "CONDITION_FAILED"
    execution.conditions = [cond]
    return execution


def _make_jobs_client(execution: Any) -> MagicMock:
    """Return a mock JobsClient whose run_job().result() yields ``execution``."""
    client = MagicMock()
    operation = MagicMock()
    operation.result.return_value = execution
    client.run_job.return_value = operation
    return client


# --------------------------------------------------------------------------- #
# Test 1 — tool registration
# --------------------------------------------------------------------------- #


def test_publish_layer_registered() -> None:
    """publish_layer is in TOOL_REGISTRY with correct metadata."""
    # Import the module to trigger registration (mirrors _import_tools_registry).
    import grace2_agent.tools.publish_layer  # noqa: F401

    assert "publish_layer" in TOOL_REGISTRY, (
        f"publish_layer not found in TOOL_REGISTRY; keys={sorted(TOOL_REGISTRY)}"
    )
    entry = TOOL_REGISTRY["publish_layer"]
    assert entry.metadata.cacheable is False
    assert entry.metadata.ttl_class == "live-no-cache"
    assert entry.metadata.source_class == "publish_layer"
    assert entry.fn is publish_layer


# --------------------------------------------------------------------------- #
# Test 2 — happy path: returns WMS URL
# --------------------------------------------------------------------------- #


def test_publish_layer_returns_wms_url() -> None:
    """With a mocked Jobs client, publish_layer returns the expected WMS URL."""
    execution = _make_succeeded_execution()
    mock_client = _make_jobs_client(execution)

    set_jobs_client(mock_client)
    set_qgis_server_url("https://qgis.test.example.com/ogc/wms")
    set_default_qgs_uri("gs://test-qgs-bucket/grace2-sample.qgs")
    set_gcp_project("test-project")
    set_gcp_location("us-central1")
    set_pyqgis_worker_job_name("grace-2-pyqgis-worker")

    try:
        result = publish_layer(
            layer_uri="gs://grace-2-hazard-prod-runs/run-abc/flood_depth_peak.tif",
            layer_id="flood-depth-peak-run-abc",
            style_preset="continuous_flood_depth",
        )
    finally:
        # Tear down DI bindings.
        set_jobs_client(None)
        set_qgis_server_url(None)
        set_default_qgs_uri(None)
        set_gcp_project(None)
        set_gcp_location(None)
        set_pyqgis_worker_job_name(None)

    assert result == (
        "https://qgis.test.example.com/ogc/wms"
        "?MAP=/mnt/qgs/grace2-sample.qgs"
        "&LAYERS=flood-depth-peak-run-abc"
    ), f"unexpected WMS URL: {result}"

    # Verify run_job was called once with the correct job name and env overrides.
    mock_client.run_job.assert_called_once()
    call_kwargs = mock_client.run_job.call_args
    assert "name" in call_kwargs.kwargs or len(call_kwargs.args) > 0
    # Extract job name from positional or keyword arg.
    job_name_arg = (
        call_kwargs.kwargs.get("name")
        or (call_kwargs.args[0] if call_kwargs.args else None)
    )
    assert "grace-2-pyqgis-worker" in job_name_arg, f"unexpected job name: {job_name_arg}"


# --------------------------------------------------------------------------- #
# Test 3 — dispatch failure → PublishLayerError
# --------------------------------------------------------------------------- #


def test_publish_layer_raises_on_dispatch_failure() -> None:
    """When run_job raises, publish_layer raises PublishLayerError(WORKER_JOB_DISPATCH_FAILED)."""
    mock_client = MagicMock()
    mock_client.run_job.side_effect = RuntimeError("quota exceeded")

    set_jobs_client(mock_client)
    set_default_qgs_uri("gs://test-qgs-bucket/grace2-sample.qgs")
    set_gcp_project("test-project")

    try:
        with pytest.raises(PublishLayerError) as exc_info:
            publish_layer(
                layer_uri="gs://runs/run-abc/flood_depth_peak.tif",
                layer_id="flood-depth-peak-run-abc",
            )
    finally:
        set_jobs_client(None)
        set_default_qgs_uri(None)
        set_gcp_project(None)

    assert exc_info.value.error_code == "WORKER_JOB_DISPATCH_FAILED"
    assert "quota exceeded" in str(exc_info.value)


# --------------------------------------------------------------------------- #
# Test 4 — worker execution fails → PublishLayerError
# --------------------------------------------------------------------------- #


def test_publish_layer_raises_on_worker_failure() -> None:
    """When the execution reaches FAILED state, publish_layer raises PublishLayerError."""
    execution = _make_failed_execution()
    mock_client = _make_jobs_client(execution)

    set_jobs_client(mock_client)
    set_default_qgs_uri("gs://test-qgs-bucket/grace2-sample.qgs")
    set_gcp_project("test-project")

    try:
        with pytest.raises(PublishLayerError) as exc_info:
            publish_layer(
                layer_uri="gs://runs/run-abc/flood_depth_peak.tif",
                layer_id="flood-depth-peak-run-abc",
            )
    finally:
        set_jobs_client(None)
        set_default_qgs_uri(None)
        set_gcp_project(None)

    assert exc_info.value.error_code == "WORKER_JOB_FAILED"


# --------------------------------------------------------------------------- #
# Test 5 — _gs_to_vsigs conversion
# --------------------------------------------------------------------------- #


def test_gs_to_vsigs_conversion() -> None:
    """_gs_to_vsigs converts gs:// URIs to /vsigs/ and passes through others."""
    assert _gs_to_vsigs("gs://bucket/path/to/file.tif") == "/vsigs/bucket/path/to/file.tif"
    assert _gs_to_vsigs("/vsigs/bucket/path/to/file.tif") == "/vsigs/bucket/path/to/file.tif"
    assert _gs_to_vsigs("/local/path/file.tif") == "/local/path/file.tif"


# --------------------------------------------------------------------------- #
# Test 6 — _build_wms_url format
# --------------------------------------------------------------------------- #


def test_build_wms_url_format() -> None:
    """_build_wms_url produces the MAP= + LAYERS= query string."""
    set_qgis_server_url("https://qgis.example.com/ogc/wms")
    try:
        url = _build_wms_url("grace2-sample.qgs", "flood-depth-peak-01")
    finally:
        set_qgis_server_url(None)

    assert url == (
        "https://qgis.example.com/ogc/wms"
        "?MAP=/mnt/qgs/grace2-sample.qgs"
        "&LAYERS=flood-depth-peak-01"
    )


# --------------------------------------------------------------------------- #
# Test 7 — _parse_qgs_key
# --------------------------------------------------------------------------- #


def test_parse_qgs_key() -> None:
    """_parse_qgs_key extracts the GCS object key from a gs:// URI."""
    assert _parse_qgs_key("gs://grace-2-hazard-prod-qgs/grace2-sample.qgs") == "grace2-sample.qgs"
    assert _parse_qgs_key("gs://bucket/subdir/project.qgs") == "subdir/project.qgs"

    with pytest.raises(PublishLayerError) as exc_info:
        _parse_qgs_key("/vsigs/bucket/file.qgs")
    assert exc_info.value.error_code == "QGS_URI_PARSE_ERROR"

    with pytest.raises(PublishLayerError) as exc_info:
        _parse_qgs_key("gs://no-key-here/")
    assert exc_info.value.error_code == "QGS_URI_PARSE_ERROR"
