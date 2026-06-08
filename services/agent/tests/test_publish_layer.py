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

# Test 8 is imported here (job-0071 auto-dispatch shape guard).
# The import of RunJobRequest validates the library is present.
try:
    from google.cloud.run_v2.types import RunJobRequest as _RunJobRequest, EnvVar as _EnvVar
    _RUN_V2_AVAILABLE = True
except Exception:
    _RUN_V2_AVAILABLE = False


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

    # Verify run_job was called once with request=RunJobRequest(...) (job-0071 fix).
    mock_client.run_job.assert_called_once()
    call_kwargs = mock_client.run_job.call_args
    # After the job-0071 auto-dispatch fix, run_job is called with request=RunJobRequest(...)
    # not name=/overrides= as direct kwargs.
    assert "request" in call_kwargs.kwargs, (
        f"job-0071: run_job must be called with request=RunJobRequest(...); "
        f"got kwargs={list(call_kwargs.kwargs)}"
    )
    req = call_kwargs.kwargs["request"]
    # The job name must appear in the RunJobRequest.name field.
    assert "grace-2-pyqgis-worker" in req.name, (
        f"job-0071: RunJobRequest.name must include the job name; got {req.name!r}"
    )


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


# --------------------------------------------------------------------------- #
# Test 8 — publish_layer auto-dispatch fix (job-0071, OQ-70-AUTO-PUBLISH-DISPATCH)
#
# Pre-fix: publish_layer called
#   jobs_client.run_job(name=..., overrides={...})
# which raises TypeError because JobsClient.run_job() does NOT accept
# ``name`` and ``overrides`` as separate kwargs — it expects a ``request``
# positional arg (or ``request=`` kwarg) of type RunJobRequest.
#
# Post-fix: the code constructs a RunJobRequest proto with the env overrides
# and passes it as ``jobs_client.run_job(request=request)``.
#
# This test asserts:
# 1. The mock client's run_job is called with ``request=`` (not ``name=``,
#    ``overrides=``).
# 2. The ``request`` is a RunJobRequest (or dict-shaped equivalent with the
#    correct structure).
# 3. The env overrides list contains the expected WORKER_OP and QGS_URI keys.
# --------------------------------------------------------------------------- #


def test_publish_layer_dispatch_uses_run_job_request_not_kwargs() -> None:
    """Auto-dispatch fix (job-0071): run_job is called with request=RunJobRequest.

    Regression guard for OQ-70-AUTO-PUBLISH-DISPATCH: the pre-fix code called
    ``jobs_client.run_job(name=..., overrides=...)`` which raises TypeError in
    the installed google-cloud-run version.  The fix uses:
        ``jobs_client.run_job(request=RunJobRequest(...))``
    """
    if not _RUN_V2_AVAILABLE:
        pytest.skip("google-cloud-run not installed; cannot validate RunJobRequest shape")

    import inspect

    execution = _make_succeeded_execution()
    mock_client = _make_jobs_client(execution)

    set_jobs_client(mock_client)
    set_qgis_server_url("https://qgis.test.example.com/ogc/wms")
    set_default_qgs_uri("gs://test-qgs-bucket/grace2-sample.qgs")
    set_gcp_project("test-project")
    set_gcp_location("us-central1")
    set_pyqgis_worker_job_name("grace-2-pyqgis-worker")

    try:
        publish_layer(
            layer_uri="gs://grace-2-hazard-prod-runs/run-xyz/flood_depth_peak.tif",
            layer_id="flood-depth-peak-run-xyz",
            style_preset="continuous_flood_depth",
        )
    finally:
        set_jobs_client(None)
        set_qgis_server_url(None)
        set_default_qgs_uri(None)
        set_gcp_project(None)
        set_gcp_location(None)
        set_pyqgis_worker_job_name(None)

    # Assert run_job was called exactly once.
    mock_client.run_job.assert_called_once()
    call_args = mock_client.run_job.call_args

    # Critical: must NOT have 'overrides' as a direct kwarg (the pre-fix bug).
    assert "overrides" not in call_args.kwargs, (
        "job-0071 auto-dispatch fix regression: run_job was called with "
        "'overrides=' as a direct kwarg. This raises TypeError in the installed "
        "google-cloud-run version. Use request=RunJobRequest(...) instead."
    )

    # Must be called with ``request=`` keyword arg (the fixed shape).
    assert "request" in call_args.kwargs, (
        f"job-0071: run_job must be called with 'request=RunJobRequest(...)'; "
        f"got kwargs={list(call_args.kwargs)}"
    )

    req = call_args.kwargs["request"]

    # The request must be a RunJobRequest instance (proto-plus message).
    assert isinstance(req, _RunJobRequest), (
        f"job-0071: request must be a RunJobRequest instance; got {type(req).__name__!r}"
    )

    # The request must carry the correct job name.
    assert "grace-2-pyqgis-worker" in req.name, (
        f"job-0071: RunJobRequest.name must include the job name; got {req.name!r}"
    )

    # The overrides must include at least one ContainerOverride with env vars.
    container_overrides = list(req.overrides.container_overrides)
    assert container_overrides, (
        "job-0071: RunJobRequest.overrides.container_overrides must be non-empty"
    )
    env_list = list(container_overrides[0].env)
    assert env_list, (
        "job-0071: ContainerOverride.env must be non-empty"
    )
    env_names = {e.name for e in env_list}
    assert "WORKER_OP" in env_names, (
        f"job-0071: env overrides must include WORKER_OP; got {env_names}"
    )
    assert "QGS_URI" in env_names, (
        f"job-0071: env overrides must include QGS_URI; got {env_names}"
    )
    assert "RASTER_URI" in env_names, (
        f"job-0071: env overrides must include RASTER_URI; got {env_names}"
    )
