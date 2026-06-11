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

from ..uri_registry import observe_published_layer
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
    "set_storage_client",
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
    shows ``UPSTREAM_API_ERROR``. ``retryable`` (job-0177 contract; harvested
    by ``adapter._classify_error``) tells Gemini whether re-issuing the call
    with corrected args can succeed.

    Codes:
    - ``JOBS_CLIENT_UNAVAILABLE`` — google-cloud-run not importable / ADC missing.
    - ``WORKER_JOB_DISPATCH_FAILED`` — ``run_job`` API call failed.
    - ``WORKER_JOB_TIMEOUT`` — execution did not finish within ``timeout_s``.
    - ``WORKER_JOB_FAILED`` — execution reached FAILED terminal state.
    - ``WORKER_JOB_CANCELLED`` — execution was cancelled externally.
    - ``QGS_URI_PARSE_ERROR`` — malformed ``project_qgs_uri``.
    - ``LAYER_URI_NOT_FOUND`` (job-0257, retryable) — ``layer_uri`` does not
      exist in GCS and no unambiguous auto-correction was found. The message
      lists the real objects under the same prefix so the LLM can retry with
      the exact URI from the producing tool's function_response.
    - ``WORKER_PUBLISH_NOT_APPLIED`` (job-0257) — the worker execution
      completed (exit-0-on-error policy, NFR-R-1) but the layer is absent
      from the ``.qgs`` — i.e. the worker envelope carried ``status=error``
      (e.g. QgsRasterLayer failed to open the raster). Without this check the
      tool reported false success and the map silently showed nothing.
    """

    def __init__(self, error_code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.retryable = retryable


# --------------------------------------------------------------------------- #
# DI seams
# --------------------------------------------------------------------------- #

_JOBS_CLIENT: Any | None = None
_GCP_PROJECT: str | None = None
_GCP_LOCATION: str | None = None
_DEFAULT_QGS_URI: str | None = None
_PYQGIS_WORKER_JOB_NAME: str | None = None
_QGIS_SERVER_URL: str | None = None
_STORAGE_CLIENT: Any | None = None


def set_storage_client(client: Any) -> None:
    """Bind the GCS ``storage.Client`` used for layer_uri validation +
    post-publish ``.qgs`` verification (job-0257).

    Production callers leave this unset (an ADC default is built lazily);
    tests inject a fake. ``None`` clears the binding.
    """
    global _STORAGE_CLIENT
    _STORAGE_CLIENT = client


def _get_storage_client() -> Any | None:
    """Return the bound storage client, lazily building an ADC default.

    Returns ``None`` (instead of raising) when google-cloud-storage is not
    importable or ADC is missing — validation/verification then degrade to
    no-ops (fail-open) so environments without GCS access keep the legacy
    behavior.
    """
    global _STORAGE_CLIENT
    if _STORAGE_CLIENT is not None:
        return _STORAGE_CLIENT
    try:
        from google.cloud import storage  # type: ignore[import-not-found]

        _STORAGE_CLIENT = storage.Client(
            project=os.environ.get("GOOGLE_CLOUD_PROJECT", "grace-2-hazard-prod")
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "publish_layer: storage client unavailable (%s) — "
            "layer_uri validation + .qgs verification skipped",
            exc,
        )
        return None
    return _STORAGE_CLIENT


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


#: job-0269b: token vocabulary marking TERRAIN-family rasters. These are
#: RGBA (colored relief) or single-band grayscale/Float32 (hillshade, slope,
#: aspect, raw DEM) products — QGIS DEFAULT rendering visualizes them
#: correctly, while the flood-depth pseudocolor ramp clamps them to a
#: uniform/transparent tile (live 2026-06-10 "can't see the overlay").
#: Token-boundary matching (not substring) so e.g. a layer_id like
#: ``"demo-flood"`` does NOT match ``dem``.
_TERRAIN_STYLE_TOKENS = frozenset(
    {"dem", "relief", "hillshade", "slope", "aspect", "terrain", "elevation"}
)


def _infer_style_preset(layer_uri: str, layer_id: str) -> str:
    """Family-aware default style preset (job-0269b).

    Returns ``""`` (no preset → QGIS default rendering) for terrain-family
    rasters, else ``"continuous_flood_depth"`` — the pre-0269b default, so
    flood/plume publishes that relied on it are unchanged. Tokenizes BOTH
    the resolved URI and the layer_id on non-alphanumerics and matches
    whole tokens against ``_TERRAIN_STYLE_TOKENS``.
    """
    import re as _re

    tokens = set(
        _re.split(r"[^a-z0-9]+", f"{layer_uri} {layer_id}".lower())
    )
    if tokens & _TERRAIN_STYLE_TOKENS:
        return ""
    return "continuous_flood_depth"


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


def _split_object_uri(uri: str) -> tuple[str, str] | None:
    """Split a ``gs://`` or ``/vsigs/`` URI into ``(bucket, key)``.

    Returns ``None`` for local paths / unparseable shapes (validation then
    passes through unchanged).
    """
    if uri.startswith("gs://"):
        rest = uri[len("gs://"):]
    elif uri.startswith("/vsigs/"):
        rest = uri[len("/vsigs/"):]
    else:
        return None
    slash = rest.find("/")
    if slash <= 0 or slash == len(rest) - 1:
        return None
    return rest[:slash], rest[slash + 1:]


#: Minimum shared-prefix length (characters of the object basename) required
#: before a missing layer_uri is auto-corrected to an existing object. Cache
#: keys are 32-hex digests; 8 shared leading chars (16^8 ≈ 4.3e9) is unique
#: in practice while the observed hallucinations preserved 14+ chars.
_URI_CORRECTION_MIN_PREFIX: int = 8


def _lcp_len(a: str, b: str) -> int:
    """Length of the longest common prefix of two strings."""
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


def _validate_and_correct_layer_uri(layer_uri: str) -> str:
    """Verify ``layer_uri`` exists in GCS; deterministically correct it if not.

    job-0257 root-cause fix (hillshade no-render): Gemini reliably mangles the
    tail of 32-hex cache keys when echoing a ``gs://`` URI from a previous
    function_response into ``publish_layer`` args (observed 3/3 in the
    2026-06-10 demo session: e.g. real ``090a4ff8d9a083f67c0b355caf40241a.tif``
    requested as ``090a4ff8d9a083b28499252309d12999.tif``). The worker then
    "succeeded" (exit-0-on-error policy) while publishing nothing.

    Strategy:
    1. If the object exists → return the (gs://-normalized) URI unchanged.
    2. If missing → list the objects under the same directory prefix and find
       the candidate whose basename shares the longest common prefix with the
       requested basename. If that prefix is >= ``_URI_CORRECTION_MIN_PREFIX``
       chars and the winner is unique, substitute it (logged at WARNING).
    3. Otherwise raise ``PublishLayerError(LAYER_URI_NOT_FOUND, retryable=True)``
       whose message lists the real objects so the LLM can retry with the
       exact URI (job-0177 retry loop).

    Fail-open: storage-client construction errors / transient GCS failures log
    a warning and return the URI unchanged (legacy behavior) — the post-publish
    ``.qgs`` verification is the second line of defense.
    """
    parsed = _split_object_uri(layer_uri)
    if parsed is None:
        return layer_uri  # local path / unparseable — let the worker decide
    bucket_name, key = parsed
    client = _get_storage_client()
    if client is None:
        return layer_uri

    try:
        if client.bucket(bucket_name).blob(key).exists():
            return f"gs://{bucket_name}/{key}"

        dir_prefix = key.rsplit("/", 1)[0] + "/" if "/" in key else ""
        requested_base = key.rsplit("/", 1)[-1]
        candidates = [
            blob.name
            for blob in client.list_blobs(bucket_name, prefix=dir_prefix)
            if blob.name != dir_prefix
        ][:256]

        scored = sorted(
            ((_lcp_len(name.rsplit("/", 1)[-1], requested_base), name) for name in candidates),
            reverse=True,
        )
        if scored and scored[0][0] >= _URI_CORRECTION_MIN_PREFIX and (
            len(scored) == 1 or scored[0][0] > scored[1][0]
        ):
            corrected = f"gs://{bucket_name}/{scored[0][1]}"
            logger.warning(
                "publish_layer: layer_uri %r does not exist in GCS — "
                "auto-corrected to %r (%d-char shared basename prefix; "
                "LLM-hallucinated URI tail, job-0257)",
                layer_uri,
                corrected,
                scored[0][0],
            )
            return corrected

        listing = ", ".join(sorted(n.rsplit("/", 1)[-1] for n in candidates)[:10]) or "<none>"
        raise PublishLayerError(
            "LAYER_URI_NOT_FOUND",
            f"layer_uri {layer_uri!r} does not exist in GCS and no unambiguous "
            f"correction was found. Objects under gs://{bucket_name}/{dir_prefix}: "
            f"[{listing}]. Re-issue publish_layer with the EXACT `uri` value "
            f"returned by the tool that produced the layer (copy it verbatim "
            f"from that function_response).",
            retryable=True,
        )
    except PublishLayerError:
        raise
    except Exception as exc:  # noqa: BLE001 — fail-open on transient GCS errors
        logger.warning(
            "publish_layer: layer_uri validation errored (%s: %s) — "
            "proceeding without validation",
            type(exc).__name__,
            exc,
        )
        return layer_uri


def _verify_layer_in_qgs(qgs_uri: str, layer_id: str) -> bool | None:
    """Check that ``layer_id`` is actually present in the published ``.qgs``.

    job-0257: the PyQGIS worker exits 0 even when the publish failed (the
    Pub/Sub envelope is the designed source of truth — NFR-R-1) and the agent
    does not consume that envelope yet (OQ-62-PUBSUB-COMPLETION-POLL). Reading
    the ``.qgs`` back and checking for ``<layername>{layer_id}</layername>``
    closes the false-success gap without requiring a worker image rebuild.

    Returns True/False on a successful check, ``None`` when verification is
    unavailable (no storage client / non-gs URI / download error) — callers
    treat ``None`` as "cannot verify" and do not fail the publish.
    """
    parsed = _split_object_uri(qgs_uri)
    if parsed is None:
        return None
    client = _get_storage_client()
    if client is None:
        return None
    try:
        data = client.bucket(parsed[0]).blob(parsed[1]).download_as_bytes()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "publish_layer: post-publish .qgs verification download failed "
            "(%s: %s) — skipping verification",
            type(exc).__name__,
            exc,
        )
        return None
    from xml.sax.saxutils import escape

    needle = f"<layername>{escape(layer_id)}</layername>".encode("utf-8")
    return needle in data


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


@register_tool(
    _PUBLISH_LAYER_METADATA,
    # Annotations: readOnlyHint=False (mutates the .qgs GCS project file),
    # openWorldHint=False (intra-GCP Cloud Run Job + GCS only; no public API),
    # destructiveHint=True (overwrites an existing layer entry in the shared
    # .qgs project — the overwrite is potentially irreversible without a
    # backup), idempotentHint=False (each call starts a new Cloud Run Job
    # execution and unconditionally mutates the .qgs).
    read_only_hint=False,
    open_world_hint=False,
    destructive_hint=True,
    idempotent_hint=False,
)
def publish_layer(
    layer_uri: str,
    layer_id: str,
    style_preset: str | None = None,
    project_qgs_uri: str | None = None,
    case_id: str | None = None,
    # job-0164: absorb LLM-invented kwargs (centralized at server.py via
    # tool_arg_normalizer, but kept as belt-and-suspenders).
    **_extra_ignored: Any,
) -> str:
    """Publish a COG raster layer to QGIS Server via the PyQGIS worker.

    Dispatches the ``grace-2-pyqgis-worker`` Cloud Run Job to add a COG raster
    at a ``gs://`` URI as a named layer in the canonical ``.qgs`` project. Polls
    until the job completes and returns a WMS URL string the MapLibre client can
    render immediately. Not cacheable (side-effect tool; mutates GCS project state).

    When to use:
        - After ``postprocess_flood``, ``compute_hillshade``, ``compute_slope``,
          ``compute_colored_relief``, ``compute_aspect``, or any other tool that
          returns a ``LayerURI`` with a ``gs://`` COG, when the user needs the
          layer displayed on the map.
        - As the final step in any workflow that produces a raster output —
          the COG is not visible until this tool runs.

    When NOT to use:
        - Rendering ``gs://`` URIs directly in MapLibre (not supported; use this
          tool to go through QGIS Server WMS first).
        - Publishing vector layers (FlatGeobuf/GeoParquet; a follow-up tool
          handles vector publication).
        - Caching or re-fetching data (this is a side-effect tool; the cache
          shim is not invoked).

    Params:
        layer_uri: the producing tool's ``layer_id`` HANDLE (PREFERRED —
            job-0263 layer-handle indirection: the server resolves it to the
            exact ``gs://`` COG it recorded), or the ``gs://`` URI copied
            VERBATIM from the producing tool's result. NEVER construct or
            re-type a gs:// path from memory. Must resolve to a COG TIFF
            readable by GDAL via the ``/vsigs/`` virtual filesystem.
        layer_id: QGIS layer name + WMS ``LAYERS=`` value for the published
            layer. Must be stable and unique within the ``.qgs`` project
            (e.g. ``"flood-depth-peak-<run_id>"``).
        style_preset: filename stem of the QML preset to apply, or omit for
            AUTO selection (recommended): flood/plume depth COGs get the
            ``"continuous_flood_depth"`` Blues ramp; terrain products
            (colored relief, hillshade, slope, aspect, raw DEM) get QGIS
            default rendering, which is correct for RGBA/grayscale rasters
            — the flood ramp painted them invisible.
        project_qgs_uri: ``gs://`` URI of the ``.qgs`` project to mutate.
            Defaults to ``gs://grace-2-hazard-prod-qgs/grace2-sample.qgs``
            (the v0.1 canonical project). The FR-MP-6 Case UX will eventually
            own per-Case project resolution.
        case_id: optional Case identifier (FR-MP-6 / job-0121). When passed,
            the server wrapper resolves the case-scoped ``.qgs`` URI via
            ``case_lifecycle.ensure_case_qgs`` BEFORE invoking this tool;
            this parameter is a transport-only carrier so the LLM-visible
            tool surface is honest about Case context. The atomic tool body
            itself does not perform Persistence I/O — the server-side
            wrapper does the lazy-init and substitutes the resolved URI
            into ``project_qgs_uri``. Defaults to ``None`` (single-tenant
            demo path; OQ-62-QGS-MUTATION-CONFLICT preserved verbatim).

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

    Cross-tool dependencies:
        Upstream (consumes):
        - ``postprocess_flood`` (via ``run_model_flood_scenario``) — flood-depth
          COG ``LayerURI`` is the most common ``layer_uri`` input.
        - ``compute_hillshade`` / ``compute_colored_relief`` / ``compute_slope`` /
          ``compute_aspect`` / ``compute_impervious_surface`` — any tool that
          returns a raster ``LayerURI`` with a ``gs://`` URI.
        - ``clip_raster_to_polygon`` / ``clip_raster_to_bbox`` — clipped rasters
          passed to this tool for display-extent-scoped publication.
        Downstream (feeds):
        - Web client MapLibre layer panel — the returned WMS URL is used
          directly as a ``LayerURI.uri`` value for WMS tile rendering.
        - ``run_model_flood_scenario`` / ``run_model_flood_habitat_scenario`` —
          call this as the final step of the workflow chain.
    """
    # 1. Resolve the .qgs URI and extract the GCS key for MAP= param.
    effective_qgs_uri = _get_effective_qgs_uri(project_qgs_uri)
    qgs_key = _parse_qgs_key(effective_qgs_uri)

    # 1b. job-0257: validate the layer_uri actually exists in GCS BEFORE
    #     dispatching a 2-minute worker round-trip. Gemini hallucinates the
    #     tail of 32-hex cache keys when copying URIs between turns; without
    #     this gate the worker "succeeds" (exit-0-on-error) while publishing
    #     nothing and the map silently stays empty. An unambiguous
    #     prefix-match is auto-corrected; otherwise LAYER_URI_NOT_FOUND
    #     (retryable) feeds the real object listing back to the LLM.
    layer_uri = _validate_and_correct_layer_uri(layer_uri)

    # 1c. job-0269b: AUTO style selection. Hardcoding the flood-depth ramp on
    #     every raster painted terrain products invisible — live 2026-06-10:
    #     a colored relief published CONDITION_SUCCEEDED but WMS served a
    #     uniform/transparent tile because the depth pseudocolor clamped the
    #     RGBA bands. Composers pass their preset explicitly; un-presetted
    #     publishes (the LLM path) get a family-aware default, and terrain
    #     families get NO preset — QGIS default multiband-RGBA/singleband-
    #     gray rendering is the correct visualization for them (the worker
    #     treats a missing QML as non-fatal by design).
    if style_preset is None or style_preset == "auto":
        style_preset = _infer_style_preset(layer_uri, layer_id)

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

    # 7. job-0257: verify the layer actually landed in the .qgs. The worker
    #    exits 0 even when the publish failed internally (its Pub/Sub envelope
    #    carries status=error, but the agent does not consume it yet —
    #    OQ-62-PUBSUB-COMPLETION-POLL), so CONDITION_SUCCEEDED alone proved
    #    nothing. ``None`` means "cannot verify" (no storage access) — keep
    #    the legacy trust-the-exit-code behavior in that case.
    applied = _verify_layer_in_qgs(effective_qgs_uri, layer_id)
    if applied is False:
        raise PublishLayerError(
            "WORKER_PUBLISH_NOT_APPLIED",
            f"PyQGIS worker execution completed, but layer {layer_id!r} is NOT "
            f"present in {effective_qgs_uri} — the worker swallowed an internal "
            f"error (exit-0-on-error policy; most commonly QgsRasterLayer could "
            f"not open raster_uri={raster_vsigs_uri!r}). The layer was NOT "
            f"published; do not tell the user it is visible on the map.",
            retryable=False,
        )

    # 8. job-0263: record BOTH faces of the published layer (validated gs://
    #    COG + WMS display URL) in the session URI registry so downstream
    #    *_uri params resolve via the layer_id handle. This is the seam that
    #    captures composer-internal publishes (run_model_flood_scenario calls
    #    this function directly; its envelope only carries the WMS URL).
    #    No-op outside an active dispatch context (tests / direct calls).
    observe_published_layer(layer_id, gcs_uri=layer_uri, wms_url=wms_url)

    # 9. Return the WMS URL.
    return wms_url
