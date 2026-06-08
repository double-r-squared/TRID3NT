"""Atomic tool ``publish_layer`` — COG → QGIS Server WMS bridge (job-0062).

This module registers one atomic tool that closes the M5→UI wiring loop:

    ``publish_layer(layer_uri, layer_id, style_preset, project_qgs_uri)``
      → ``str`` (WMS URL the MapLibre client can render)

**Why this exists**

After job-0058 produces a flood-depth COG at
``gs://grace-2-hazard-prod-runs/<run_id>/flood_depth_peak.tif`` and
job-0060 wires ``LayerURI`` emission, the COG's ``gs://`` URI is not
directly renderable in MapLibre. The client needs a **WMS URL** that QGIS
Server can serve.

This tool closes that loop by:

1. Constructing the raster_uri as a ``/vsigs/`` GDAL path.
2. Invoking the ``grace-2-pyqgis-worker`` Cloud Run Job via
   ``google.cloud.run_v2.JobsClient.run_job``, passing the
   ``--op publish-raster`` + raster URI + layer ID + style preset via the
   job's ``overrides`` env.
3. Polling until the job execution reaches a terminal state
   (``SUCCEEDED`` / ``FAILED`` / ``CANCELLED``).
4. Returning the WMS URL:
   ``<qgis-server-url>?MAP=/mnt/qgs/<qgs-key>&LAYERS=<layer_id>``

**Dependency-injection seams** (mirrors ``tools/solver.py`` pattern):

- ``_JOBS_CLIENT`` / ``set_jobs_client(client)`` — the Cloud Run v2
  ``JobsClient``. Production binding at startup uses ADC; tests inject a
  mock. Lazily defaults at first use so import-time does not require ADC.
- ``_GCP_PROJECT`` / ``set_gcp_project(project)`` — GCP project override.
- ``_QGS_URI`` / ``set_default_qgs_uri(uri)`` — default canonical
  ``.qgs`` URI override (useful for smoke harnesses and integration tests).
- ``_PYQGIS_WORKER_JOB_NAME`` / ``set_pyqgis_worker_job_name(name)`` —
  Cloud Run Job name override (default: ``grace-2-pyqgis-worker``).

**Cross-cutting principles:**

- **Invariant 4 (Rendering through QGIS Server): the headline.** This tool
  is the single sanctioned path that makes a COG renderable via QGIS Server
  WMS. No direct gs:// → client path exists.
- **Invariant 2 (Deterministic workflows): preserves.** Zero LLM calls in
  this tool's body. It dispatches a Cloud Run Job deterministically.
- **FR-DC-6 (uncacheable enumeration): preserves.** This is a side-effect
  tool (it mutates the ``.qgs``); ``cacheable=False``,
  ``ttl_class="live-no-cache"``, ``source_class="publish_layer"``.
- **NFR-R-1 (resilience):** the ``JobsClient.run_job`` call is wrapped in a
  typed error class so failures surface as ``PublishLayerError`` (not
  unhandled exceptions).

**Open Questions surfaced by this job (non-blocking):**

- OQ-62-WORKER-SA-RUNS-BUCKET-GRANT: the PyQGIS worker SA
  (``pyqgis-worker-runtime``) does not currently have
  ``roles/storage.objectViewer`` on ``grace-2-hazard-prod-runs``. Without
  this grant the worker's GDAL ``/vsigs/<runs-bucket>/...`` read will fail.
  A follow-up infra job must add the grant. Until then the tool will return
  ``PublishLayerError`` from the worker-side GDAL open failure.

- OQ-62-LAYERURI-URI-FIELD: ``LayerURI.uri`` is documented as
  ``gs://...`` but the contract has no validator rejecting WMS URLs.
  The workflow substitutes the WMS URL directly into ``uri`` so the client
  gets a renderable URL. A follow-up schema job should add a ``wms_url``
  field (or rename ``uri`` to ``source_url``) to make the field's purpose
  explicit; the workaround is backward-compatible.

- OQ-62-PUBSUB-COMPLETION-POLL: the current implementation polls
  Cloud Run Job execution status directly (not Pub/Sub) because the
  Pub/Sub subscriber path is not yet wired on the agent side. A follow-up
  job can switch to Pub/Sub once the subscriber lands.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from grace2_contracts.tool_registry import AtomicToolMetadata

from . import register_tool

__all__ = [
    "publish_layer",
    "PublishLayerError",
    "set_jobs_client",
    "set_gcp_project",
    "set_gcp_location",
    "set_default_qgs_uri",
    "set_pyqgis_worker_job_name",
    "set_qgis_server_url",
    "DEFAULT_PROJECT_QGS_URI",
    "DEFAULT_PYQGIS_WORKER_JOB_NAME",
    "DEFAULT_QGIS_SERVER_URL",
    "DEFAULT_POLL_INTERVAL_S",
    "DEFAULT_TIMEOUT_S",
]

logger = logging.getLogger("grace2_agent.tools.publish_layer")


# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

#: Default canonical project .qgs URI in GCS. The FR-MP-6 Case UX will
#: eventually own per-Case project resolution; this is the v0.1 default.
DEFAULT_PROJECT_QGS_URI: str = "gs://grace-2-hazard-prod-qgs/grace2-sample.qgs"

#: Cloud Run Job name for the PyQGIS worker (infra/worker.tf).
DEFAULT_PYQGIS_WORKER_JOB_NAME: str = "grace-2-pyqgis-worker"

#: QGIS Server WMS base URL. Matches ``web/src/Map.tsx:40``.
DEFAULT_QGIS_SERVER_URL: str = (
    "https://grace-2-qgis-server-425352658356.us-central1.run.app/ogc/wms"
)

#: Poll cadence (seconds) for Cloud Run Job execution completion.
DEFAULT_POLL_INTERVAL_S: int = 5

#: Hard timeout for the worker execution (seconds). The worker itself has a
#: 900 s task timeout (infra/worker.tf); we allow 960 s here for overhead.
DEFAULT_TIMEOUT_S: int = 960


# --------------------------------------------------------------------------- #
# Error class
# --------------------------------------------------------------------------- #


class PublishLayerError(RuntimeError):
    """Raised when ``publish_layer`` cannot complete the round-trip.

    The ``error_code`` attribute carries a SCREAMING_SNAKE_CASE code so the
    agent surface can render a useful failure narration and the pipeline strip
    shows ``UPSTREAM_API_ERROR``.

    Codes:
    - ``JOBS_CLIENT_UNAVAILABLE`` — google-cloud-run not importable / ADC missing.
    - ``WORKER_JOB_DISPATCH_FAILED`` — ``run_job`` API call failed.
    - ``WORKER_JOB_TIMEOUT`` — execution did not finish within ``timeout_s``.
    - ``WORKER_JOB_FAILED`` — execution reached FAILED terminal state.
    - ``WORKER_JOB_CANCELLED`` — execution was cancelled externally.
    - ``QGS_URI_PARSE_ERROR`` — malformed ``project_qgs_uri``.
    """

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code


# --------------------------------------------------------------------------- #
# DI seams
# --------------------------------------------------------------------------- #

_JOBS_CLIENT: Any | None = None
_GCP_PROJECT: str | None = None
_GCP_LOCATION: str | None = None
_DEFAULT_QGS_URI: str | None = None
_PYQGIS_WORKER_JOB_NAME: str | None = None
_QGIS_SERVER_URL: str | None = None


def set_jobs_client(client: Any) -> None:
    """Bind the Cloud Run v2 ``JobsClient`` for worker execution dispatch.

    Production wiring (``main.py``) binds an ADC-authenticated client at
    startup; tests inject a mock. ``None`` clears the binding (lazy default
    takes over at next use).
    """
    global _JOBS_CLIENT
    _JOBS_CLIENT = client


def set_gcp_project(project: str | None) -> None:
    """Override the GCP project used for Cloud Run Job dispatch."""
    global _GCP_PROJECT
    _GCP_PROJECT = project


def set_gcp_location(location: str | None) -> None:
    """Override the GCP region used for Cloud Run Job dispatch."""
    global _GCP_LOCATION
    _GCP_LOCATION = location


def set_default_qgs_uri(uri: str | None) -> None:
    """Override the default canonical .qgs URI.

    Useful for smoke harnesses and integration tests that target a non-production
    project. ``None`` restores the constant default.
    """
    global _DEFAULT_QGS_URI
    _DEFAULT_QGS_URI = uri


def set_pyqgis_worker_job_name(name: str | None) -> None:
    """Override the Cloud Run Job name for the PyQGIS worker."""
    global _PYQGIS_WORKER_JOB_NAME
    _PYQGIS_WORKER_JOB_NAME = name


def set_qgis_server_url(url: str | None) -> None:
    """Override the QGIS Server base URL used to compose the WMS URL."""
    global _QGIS_SERVER_URL
    _QGIS_SERVER_URL = url


def _get_jobs_client() -> Any:
    """Return the bound JobsClient or lazily construct an ADC default.

    Lazy import so the agent service can boot in CI/test environments that
    don't have google-cloud-run installed.
    """
    if _JOBS_CLIENT is not None:
        return _JOBS_CLIENT
    try:
        from google.cloud import run_v2  # type: ignore[import-not-found]
    except Exception as exc:  # noqa: BLE001
        raise PublishLayerError(
            "JOBS_CLIENT_UNAVAILABLE",
            f"google-cloud-run not importable: {exc}; "
            "agent startup should call set_jobs_client(...).",
        ) from exc
    return run_v2.JobsClient()


def _get_gcp_project() -> str:
    return (
        _GCP_PROJECT
        or os.environ.get("GRACE2_GCP_PROJECT")
        or os.environ.get("GOOGLE_CLOUD_PROJECT")
        or os.environ.get("GCP_PROJECT")
        or "grace-2-hazard-prod"
    )


def _get_gcp_location() -> str:
    return (
        _GCP_LOCATION
        or os.environ.get("GRACE2_GCP_LOCATION")
        or os.environ.get("GOOGLE_CLOUD_LOCATION")
        or "us-central1"
    )


def _get_effective_qgs_uri(project_qgs_uri: str | None) -> str:
    if project_qgs_uri is not None:
        return project_qgs_uri
    if _DEFAULT_QGS_URI is not None:
        return _DEFAULT_QGS_URI
    return DEFAULT_PROJECT_QGS_URI


def _get_qgis_server_url() -> str:
    if _QGIS_SERVER_URL is not None:
        return _QGIS_SERVER_URL.rstrip("/")
    return os.environ.get("QGIS_SERVER_URL", DEFAULT_QGIS_SERVER_URL).rstrip("/")


def _get_pyqgis_worker_job_name() -> str:
    return _PYQGIS_WORKER_JOB_NAME or DEFAULT_PYQGIS_WORKER_JOB_NAME


def _parse_qgs_key(qgs_uri: str) -> str:
    """Extract the GCS object key (no leading slash) from a gs:// URI.

    Used to build the MAP= parameter in the WMS URL.

    Examples:
        ``gs://grace-2-hazard-prod-qgs/grace2-sample.qgs``
        → ``grace2-sample.qgs``

    Raises:
        PublishLayerError: if the URI cannot be parsed as gs://.
    """
    if not qgs_uri.startswith("gs://"):
        raise PublishLayerError(
            "QGS_URI_PARSE_ERROR",
            f"project_qgs_uri must be a gs:// URI; got {qgs_uri!r}",
        )
    # gs://<bucket>/<key>
    rest = qgs_uri[len("gs://"):]
    slash_idx = rest.find("/")
    if slash_idx == -1 or slash_idx == len(rest) - 1:
        raise PublishLayerError(
            "QGS_URI_PARSE_ERROR",
            f"project_qgs_uri has no key component: {qgs_uri!r}",
        )
    key = rest[slash_idx + 1:]
    return key


def _build_wms_url(qgs_key: str, layer_id: str) -> str:
    """Compose the WMS URL the client uses to render the layer.

    Format (mirrors ``web/src/Map.tsx`` line 40 convention):
        <qgis_server_url>?MAP=/mnt/qgs/<qgs_key>&LAYERS=<layer_id>

    The ``QGIS_SERVER_URL`` env var overrides the default so the smoke
    harness can target a different server.
    """
    base = _get_qgis_server_url()
    map_param = f"/mnt/qgs/{qgs_key}"
    return f"{base}?MAP={map_param}&LAYERS={layer_id}"


def _gs_to_vsigs(gs_uri: str) -> str:
    """Convert a ``gs://<bucket>/<key>`` URI to a GDAL ``/vsigs/`` path.

    The PyQGIS worker's ``_append_raster_layer`` uses ``QgsRasterLayer`` with
    the ``gdal`` provider, which routes through GDAL's ``/vsigs/`` virtual
    filesystem for GCS access (authenticated via the Cloud Run instance
    metadata endpoint when ``CPL_MACHINE_IS_GCE=YES``).
    """
    if gs_uri.startswith("/vsigs/"):
        return gs_uri
    if not gs_uri.startswith("gs://"):
        return gs_uri  # local path — pass through unchanged
    rest = gs_uri[len("gs://"):]
    return f"/vsigs/{rest}"


def _execution_state_name(execution: Any) -> str:
    """Return the execution state as a string (handles proto enum + dict mock)."""
    state = getattr(execution, "condition", None)
    # Cloud Run v2 JobExecution uses ``conditions`` list; the terminal
    # condition type is ``"Completed"``. We inspect ``execution.reconciling``
    # and the execution's ``conditions`` to determine terminal state.
    # Prefer ``execution.reconciling`` + state enum path.
    # The v2 JobExecution object exposes ``.reconciling`` (bool) and
    # ``.conditions`` (list of Condition). Terminal when not reconciling.
    reconciling = getattr(execution, "reconciling", None)
    if reconciling is False:
        # Not reconciling → inspect conditions for outcome.
        conditions = getattr(execution, "conditions", []) or []
        for cond in conditions:
            cond_type = getattr(cond, "type_", None) or getattr(cond, "type", None)
            cond_state = getattr(cond, "state", None)
            if cond_type == "Completed":
                state_name = getattr(cond_state, "name", str(cond_state))
                return state_name  # e.g. "CONDITION_SUCCEEDED", "CONDITION_FAILED"
        return "SUCCEEDED"  # no Completed condition → treat as succeeded
    if reconciling is True:
        return "ACTIVE"
    # Dict-shaped mock (unit tests).
    if isinstance(execution, dict):
        return str(execution.get("state", "ACTIVE"))
    return "ACTIVE"


def _poll_execution(
    jobs_client: Any,
    execution_name: str,
    poll_interval_s: int,
    timeout_s: int,
) -> Any:
    """Poll the Cloud Run v2 job execution until terminal state.

    Returns the final ``Execution`` object. Raises ``PublishLayerError`` on
    timeout or failure.

    The Cloud Run v2 ``Execution`` is terminal when its ``reconciling``
    attribute is False. Terminal outcomes map as:
    - ``CONDITION_SUCCEEDED`` → success; return execution.
    - ``CONDITION_FAILED`` → raise ``WORKER_JOB_FAILED``.
    - Any state after timeout → raise ``WORKER_JOB_TIMEOUT``.
    """
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            execution = jobs_client.get_execution(name=execution_name)
        except Exception as exc:  # noqa: BLE001 — transient poll error; keep trying
            logger.warning("get_execution(%s) raised: %s — will retry", execution_name, exc)
            execution = None

        if execution is not None:
            state = _execution_state_name(execution)
            logger.debug("publish_layer poll execution_name=%s state=%s", execution_name, state)
            if state in ("SUCCEEDED", "CONDITION_SUCCEEDED"):
                return execution
            if state in ("FAILED", "CONDITION_FAILED", "CANCELLED", "CONDITION_CANCELLED"):
                raise PublishLayerError(
                    "WORKER_JOB_FAILED" if "CANCELLED" not in state else "WORKER_JOB_CANCELLED",
                    f"PyQGIS worker execution {execution_name} reached terminal state {state}",
                )

        if time.monotonic() >= deadline:
            raise PublishLayerError(
                "WORKER_JOB_TIMEOUT",
                f"publish_layer: worker execution {execution_name} did not finish "
                f"within {timeout_s} s",
            )
        time.sleep(poll_interval_s)


# --------------------------------------------------------------------------- #
# Tool registration
# --------------------------------------------------------------------------- #

_PUBLISH_LAYER_METADATA = AtomicToolMetadata(
    name="publish_layer",
    ttl_class="live-no-cache",
    source_class="publish_layer",
    cacheable=False,
)


@register_tool(_PUBLISH_LAYER_METADATA)
def publish_layer(
    layer_uri: str,
    layer_id: str,
    style_preset: str = "continuous_flood_depth",
    project_qgs_uri: str | None = None,
) -> str:
    """Publish a COG raster layer to QGIS Server via the PyQGIS worker.

    Use this when: ``postprocess_flood`` has produced a flood-depth COG at a
    ``gs://`` URI and the agent needs to make it renderable in MapLibre via
    QGIS Server WMS. This tool invokes the ``grace-2-pyqgis-worker`` Cloud Run
    Job to add the COG as a named layer in the canonical ``.qgs`` project,
    writes the updated project back to GCS, and returns the WMS URL the client
    can immediately render.

    Do NOT use this for:
    - Rendering gs:// URIs directly (MapLibre cannot read GCS objects).
    - Publishing vector layers (FlatGeobuf/GeoParquet) — a follow-up tool
      handles those.
    - Caching or re-fetching data (this is a side-effect tool; the cache shim
      is not involved).

    Params:
        layer_uri: ``gs://`` URI of the COG raster produced by
            ``postprocess_flood``. Must be a COG TIFF readable by GDAL via
            the ``/vsigs/`` virtual filesystem.
        layer_id: QGIS layer name + WMS ``LAYERS=`` value for the published
            layer. Must be stable and unique within the ``.qgs`` project
            (e.g. ``"flood-depth-peak-<run_id>"``).
        style_preset: filename stem of the QML preset to apply. Default:
            ``"continuous_flood_depth"`` (0–3.5 m Blues ramp for hmax COGs).
        project_qgs_uri: ``gs://`` URI of the ``.qgs`` project to mutate.
            Defaults to ``gs://grace-2-hazard-prod-qgs/grace2-sample.qgs``
            (the v0.1 canonical project). The FR-MP-6 Case UX will eventually
            own per-Case project resolution.

    Returns:
        WMS URL string:
        ``<qgis-server-url>?MAP=/mnt/qgs/<qgs-key>&LAYERS=<layer_id>``
        This URL is suitable for direct use as a ``LayerURI.uri`` value so
        the MapLibre client can render it without further processing.

    Raises:
        PublishLayerError: on any failure (client unavailable, job dispatch
            error, worker execution failure, timeout). The ``error_code``
            attribute carries a SCREAMING_SNAKE_CASE code for the pipeline
            strip.

    FR-DC-6: This tool is uncacheable-by-construction (side-effect tool
    that mutates GCS state). The cache shim is NOT invoked.

    Invariant 4 (Rendering through QGIS Server): this tool IS the bridge.
    The COG at ``gs://`` becomes accessible as WMS only after this call
    mutates the ``.qgs``.

    Invariant 6 (Metadata-payload pattern): the worker writes the `.qgs`
    payload to GCS; Pub/Sub notification is the metadata layer. This tool
    does not write MongoDB directly (a follow-up job wires the
    ``RunDocument`` update for the published layer).
    """
    # 1. Resolve the .qgs URI and extract the GCS key for MAP= param.
    effective_qgs_uri = _get_effective_qgs_uri(project_qgs_uri)
    qgs_key = _parse_qgs_key(effective_qgs_uri)

    # 2. Convert the gs:// layer_uri to /vsigs/ for GDAL (the worker's
    #    _append_raster_layer uses QgsRasterLayer with the "gdal" provider).
    raster_vsigs_uri = _gs_to_vsigs(layer_uri)

    # 3. Build the WMS URL now (deterministic from inputs; needed for return).
    wms_url = _build_wms_url(qgs_key, layer_id)

    logger.info(
        "publish_layer layer_id=%s raster_uri=%s qgs_uri=%s style=%s wms_url=%s",
        layer_id,
        raster_vsigs_uri,
        effective_qgs_uri,
        style_preset,
        wms_url,
    )

    # 4. Dispatch the Cloud Run Job.
    project = _get_gcp_project()
    location = _get_gcp_location()
    job_name = _get_pyqgis_worker_job_name()
    job_resource_name = f"projects/{project}/locations/{location}/jobs/{job_name}"

    jobs_client = _get_jobs_client()

    # Build env overrides for the publish-raster operation.
    # Cloud Run v2 ``RunJobRequest.overrides.container_overrides[].env``
    # accepts a list of ``EnvVar``-compatible objects; we use plain dicts
    # because the proto message accepts them via the autogenerated client.
    env_overrides = [
        {"name": "WORKER_OP", "value": "publish-raster"},
        {"name": "QGS_URI", "value": effective_qgs_uri},
        {"name": "RASTER_URI", "value": raster_vsigs_uri},
        {"name": "RASTER_LAYER_ID", "value": layer_id},
        {"name": "STYLE_PRESET_NAME", "value": style_preset},
    ]

    logger.info(
        "publish_layer: dispatching Cloud Run Job %s env_overrides=%s",
        job_resource_name,
        {e["name"]: e["value"] for e in env_overrides},
    )

    # Build the RunJobRequest with overrides using the correct proto-plus API.
    # Verified against installed google-cloud-run: JobsClient.run_job() accepts
    # a ``request`` positional/keyword arg of type RunJobRequest (or dict), but
    # does NOT accept ``name=`` + ``overrides=`` as separate keyword args —
    # that shape raises TypeError in the installed library version.
    # (Diagnosis: help(JobsClient.run_job); fix: OQ-70-AUTO-PUBLISH-DISPATCH.)
    try:
        from google.cloud.run_v2.types import RunJobRequest as _RunJobRequest
        from google.cloud.run_v2.types import EnvVar as _EnvVar

        _request = _RunJobRequest(
            name=job_resource_name,
            overrides=_RunJobRequest.Overrides(
                container_overrides=[
                    _RunJobRequest.Overrides.ContainerOverride(
                        env=[_EnvVar(name=e["name"], value=e["value"]) for e in env_overrides],
                    )
                ]
            ),
        )
    except Exception as exc:  # noqa: BLE001
        raise PublishLayerError(
            "JOBS_CLIENT_UNAVAILABLE",
            f"Could not construct RunJobRequest (google-cloud-run unavailable?): {exc}",
        ) from exc

    try:
        operation = jobs_client.run_job(request=_request)
    except Exception as exc:  # noqa: BLE001
        raise PublishLayerError(
            "WORKER_JOB_DISPATCH_FAILED",
            f"Cloud Run Jobs run_job({job_resource_name}) failed: {exc}",
        ) from exc

    # 5. The run_job LRO resolves to the execution resource. The operation
    #    result is the Execution. We use a short result() call then poll.
    try:
        execution = operation.result(timeout=DEFAULT_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001
        # If the LRO itself fails (not just the job), raise dispatch error.
        raise PublishLayerError(
            "WORKER_JOB_DISPATCH_FAILED",
            f"run_job LRO failed for {job_resource_name}: {exc}",
        ) from exc

    # 6. The LRO result is the execution; inspect its state.
    final_state = _execution_state_name(execution)
    logger.info(
        "publish_layer: execution completed state=%s layer_id=%s wms_url=%s",
        final_state,
        layer_id,
        wms_url,
    )

    if final_state not in ("SUCCEEDED", "CONDITION_SUCCEEDED"):
        raise PublishLayerError(
            "WORKER_JOB_FAILED",
            f"PyQGIS worker execution reached terminal state {final_state} "
            f"for layer_id={layer_id!r}",
        )

    # 7. Return the WMS URL.
    return wms_url
