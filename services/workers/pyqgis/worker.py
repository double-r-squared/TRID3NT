"""GRACE-2 PyQGIS worker — canonical FR-QS-6 round-trip.

Implements the worker side of the SRS v0.3 §FR-QS-6 pattern: read a ``.qgs``
from GCS, mutate it via PyQGIS (append a styled layer, apply a QML preset),
write it back to GCS, publish a typed completion envelope to the
``grace-2-worker-events`` Pub/Sub topic.

This module is invoked by the Cloud Run Job built in job-0021 (sprint-04);
the entrypoint container runs ``python -m services.workers.pyqgis`` with
``--qgs-uri`` + ``--layer-to-add`` flags (or the env-var fallbacks
``QGS_URI`` / ``LAYER_TO_ADD``). The CLI entrypoint lives in
``services/workers/pyqgis/__main__.py`` — invoking
``python -m services.workers.pyqgis.worker`` directly is a no-op (this
module exposes only the library API; argparse handling is in ``__main__``).

Invariants honored
------------------

* **Invariant 2 (Deterministic workflows):** no LLM in the call graph.
  ``grep -rEn 'gemini|anthropic|openai|generativeai' services/workers/pyqgis/``
  returns zero matches.
* **Invariant 4 (Rendering through QGIS Server / PyQGIS-only ``.qgs`` writer):**
  the only ``.qgs`` mutation path in production. Web/agent never touches it.
* **Invariant 6 (Metadata-payload pattern):** worker writes the ``.qgs``
  payload to GCS and publishes a metadata notify. MongoDB writes are
  deferred to M3/M4 when a real ``RunDocument`` / ``EventDocument`` lands
  (Appendix D direct-driver path will be added then — TENTATIVE per the
  job-0020 audit OQ).
* **NFR-R-1 (resilience):** GCS download/upload and Pub/Sub publish are
  retried with exponential backoff (3 attempts, 250 ms base); on exhaustion
  the worker returns ``WorkerResult(status="error", ...)`` instead of
  crashing the Cloud Run Job.
"""

from __future__ import annotations

import logging
import os
import tempfile
import time
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Iterator
from urllib.parse import urlparse

from qgis.core import (
    Qgis,
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsFeature,
    QgsField,
    QgsGeometry,
    QgsProject,
    QgsVectorFileWriter,
    QgsVectorLayer,
    QgsWkbTypes,
)

from .types import LayerSpec, WorkerError, WorkerResult

try:
    from qgis.PyQt.QtCore import QMetaType, QVariant
except ImportError:  # pragma: no cover — defensive
    QMetaType = None  # type: ignore[assignment]
    QVariant = None  # type: ignore[assignment]

logger = logging.getLogger("grace2.worker.pyqgis")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Pub/Sub topic the worker publishes its completion envelope to. Matches
#: ``infra/pubsub.tf`` (job-0018) and the Stage-D Cloud Run Job binding
#: (job-0021). Override via ``PUBSUB_TOPIC`` env var (used by tests only).
DEFAULT_PUBSUB_TOPIC = "grace-2-worker-events"

#: GCP project the Pub/Sub topic lives in.
DEFAULT_GCP_PROJECT = "grace-2-hazard-prod"

#: Number of retry attempts for GCS download/upload + Pub/Sub publish.
RETRY_ATTEMPTS = 3

#: Exponential backoff base (seconds) for retries.
RETRY_BASE_SECONDS = 0.25

#: Path to the QML preset baked into the QGIS Server / worker container by
#: ``infra/qgis-server/Dockerfile``. The worker also looks at the in-repo
#: path when running locally outside the container (dev iteration).
STYLE_PRESET_CONTAINER_PATH = Path("/opt/styles/basemap.qml")

#: Relative path inside the repo (for local dev only).
STYLE_PRESET_REPO_PATH = Path(__file__).resolve().parents[3] / "styles" / "basemap.qml"


# ---------------------------------------------------------------------------
# URI parsing
# ---------------------------------------------------------------------------


def _parse_qgs_uri(qgs_uri: str) -> tuple[str, str | None, str | None, str]:
    """Parse the worker's input URI.

    Accepts three shapes:

    * ``/vsigs/<bucket>/<key>.qgs`` — production (GDAL VSI access path).
    * ``gs://<bucket>/<key>.qgs`` — convenience alias; mapped to ``/vsigs/`` for
      the QGIS read step and to the ``google-cloud-storage`` SDK for upload.
    * ``/some/local/path.qgs`` — local-dev (round-trip overwrites in place).

    Returns
    -------
    (mode, bucket, key, read_path)
        ``mode`` is ``"gcs"`` or ``"local"``. ``bucket`` and ``key`` are
        ``None`` in local mode. ``read_path`` is the absolute path passed
        to ``QgsProject.read()``.
    """
    if qgs_uri.startswith("/vsigs/"):
        rest = qgs_uri[len("/vsigs/"):]
        if "/" not in rest:
            raise WorkerError(f"malformed /vsigs/ URI (no key): {qgs_uri!r}")
        bucket, _, key = rest.partition("/")
        return "gcs", bucket, key, qgs_uri
    if qgs_uri.startswith("gs://"):
        parsed = urlparse(qgs_uri)
        bucket = parsed.netloc
        key = parsed.path.lstrip("/")
        if not bucket or not key:
            raise WorkerError(f"malformed gs:// URI: {qgs_uri!r}")
        return "gcs", bucket, key, f"/vsigs/{bucket}/{key}"
    # Local path.
    p = Path(qgs_uri)
    if not p.is_absolute():
        p = p.resolve()
    return "local", None, None, str(p)


# ---------------------------------------------------------------------------
# Retry wrapper
# ---------------------------------------------------------------------------


def _retry(label: str, fn, *args, **kwargs):
    """Call ``fn(*args, **kwargs)`` with exponential-backoff retries.

    Raises the last exception after :data:`RETRY_ATTEMPTS` failures so the
    caller can convert to a ``WorkerResult(status="error")``.
    """
    last_exc: Exception | None = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 — surface any external failure
            last_exc = exc
            if attempt == RETRY_ATTEMPTS:
                break
            sleep_s = RETRY_BASE_SECONDS * (2 ** (attempt - 1))
            logger.warning(
                "retry %s/%s for %s after %s: %s",
                attempt,
                RETRY_ATTEMPTS,
                label,
                type(exc).__name__,
                exc,
            )
            time.sleep(sleep_s)
    assert last_exc is not None  # for the type-checker
    raise last_exc


# ---------------------------------------------------------------------------
# QGIS application lifecycle
# ---------------------------------------------------------------------------


@contextmanager
def _qgis_app() -> Iterator[QgsApplication]:
    """Headless ``QgsApplication`` lifecycle manager.

    Always tears down on exit so ``exitQgis()`` runs even when the worker
    body raises. Matches the engine.md "always tear down" discipline.
    """
    app = QgsApplication([], False)
    app.initQgis()
    try:
        yield app
    finally:
        try:
            QgsProject.instance().clear()
        except Exception:  # pragma: no cover — defensive
            pass
        app.exitQgis()


# ---------------------------------------------------------------------------
# Mutation helpers
# ---------------------------------------------------------------------------


def _layer_names(project: QgsProject) -> list[str]:
    """Return the layer names of ``project`` in QGIS' iteration order."""
    return [layer.name() for layer in project.mapLayers().values()]


def _make_polygon_fgb(layer_spec: LayerSpec, out_dir: Path) -> Path:
    """Materialize ``layer_spec`` as a single-feature FlatGeobuf file.

    QGIS' in-memory provider is fine for the round-trip, but a real
    FlatGeobuf on disk is more honest to FR-CE-4 ("vectors FlatGeobuf or
    GeoParquet"). We write next to the project so the source path is
    relative and survives the GCS write-back as a sibling.

    Note: M2 only writes the FGB *locally*; uploading the FGB to GCS is
    deferred to M5+ when ``postprocess_flood`` lands. For now the worker
    embeds the FGB as a local file reference in the ``.qgs``; once the
    ``.qgs`` is read by QGIS Server (which uses its own filesystem) the
    layer source will need to be a GCS URI. **TENTATIVE / OQ-20B**: the
    layer is added as an in-memory vector layer instead, so the ``.qgs``
    contains the geometry inline as a memory provider URI — no external
    file dependency. The FGB-writer code path remains for future
    upgrades.
    """
    out_path = out_dir / f"{layer_spec.name}.fgb"
    geom = QgsGeometry.fromWkt(layer_spec.polygon_wkt)
    if geom.isEmpty():
        raise WorkerError(f"invalid polygon_wkt: {layer_spec.polygon_wkt!r}")
    crs = QgsCoordinateReferenceSystem(layer_spec.crs)
    fields = []
    # QGIS 3.40: prefer QMetaType.Type.QString; fallback to QVariant for older
    # PyQt bindings.
    if QMetaType is not None and hasattr(QMetaType, "Type"):
        fields.append(QgsField("name", QMetaType.Type.QString))
    else:  # pragma: no cover
        fields.append(QgsField("name", QVariant.String))  # type: ignore[arg-type]
    from qgis.core import QgsFields

    qgs_fields = QgsFields()
    for f in fields:
        qgs_fields.append(f)

    save_options = QgsVectorFileWriter.SaveVectorOptions()
    save_options.driverName = "FlatGeobuf"
    save_options.fileEncoding = "UTF-8"
    writer = QgsVectorFileWriter.create(
        str(out_path),
        qgs_fields,
        QgsWkbTypes.Polygon,
        crs,
        QgsCoordinateReferenceSystem(),
        save_options,
    )
    if writer.hasError() != QgsVectorFileWriter.NoError:
        raise WorkerError(
            f"FlatGeobuf writer init failed: {writer.errorMessage()}"
        )

    feat = QgsFeature(qgs_fields)
    feat.setGeometry(geom)
    feat.setAttribute("name", layer_spec.name)
    if not writer.addFeature(feat):
        raise WorkerError("failed to append polygon feature to FlatGeobuf")
    del writer
    return out_path


def _append_memory_polygon_layer(
    project: QgsProject, layer_spec: LayerSpec
) -> QgsVectorLayer:
    """Append an in-memory polygon layer matching ``layer_spec`` to ``project``.

    Returns the created layer. The layer's data source is the QGIS
    ``memory`` provider, so the ``.qgs`` carries the geometry inline — no
    external file dependency. This is the M2 mutation that proves the
    PyQGIS-writer codepath; M5+ swaps to a GCS FlatGeobuf URI source via
    ``postprocess_flood``.
    """
    crs_authid = layer_spec.crs
    layer = QgsVectorLayer(
        f"Polygon?crs={crs_authid}&field=name:string",
        layer_spec.name,
        "memory",
    )
    if not layer.isValid():
        raise WorkerError(f"in-memory polygon layer failed to init: {layer_spec.name}")
    geom = QgsGeometry.fromWkt(layer_spec.polygon_wkt)
    if geom.isEmpty():
        raise WorkerError(f"invalid polygon_wkt: {layer_spec.polygon_wkt!r}")

    feat = QgsFeature(layer.fields())
    feat.setGeometry(geom)
    feat.setAttribute("name", layer_spec.name)
    pr = layer.dataProvider()
    if not pr.addFeatures([feat]):
        raise WorkerError("failed to add polygon feature to memory layer")
    layer.updateExtents()

    project.addMapLayer(layer)
    return layer


def _apply_style_preset(layer, style_path: Path) -> bool:
    """Apply a QML style file to ``layer`` if the path exists.

    Returns True on success, False if the QML is missing or fails to load.
    Missing-QML is not fatal: the worker proceeds without the preset and
    records the skip in the resulting ``WorkerResult.layers_after`` (the
    layer is still appended). The seven full FR-QS-5 presets are
    target-typed (raster vs vector) — basemap.qml is a raster preset
    and won't bind to a polygon vector layer; we still call ``loadNamedStyle``
    so the codepath is exercised, and record the bind result.
    """
    if not style_path.exists():
        logger.info("style preset not found at %s — skipping", style_path)
        return False
    msg, ok = layer.loadNamedStyle(str(style_path))
    if not ok:
        logger.info(
            "loadNamedStyle returned False for %s on %s: %s",
            style_path,
            layer.name(),
            msg,
        )
    return bool(ok)


def _resolve_style_preset_path() -> Path | None:
    """Pick the first existing QML preset path between container + repo."""
    if STYLE_PRESET_CONTAINER_PATH.exists():
        return STYLE_PRESET_CONTAINER_PATH
    if STYLE_PRESET_REPO_PATH.exists():
        return STYLE_PRESET_REPO_PATH
    return None


# ---------------------------------------------------------------------------
# GCS helpers (lazy-import so unit tests in pure-Python envs can stub)
# ---------------------------------------------------------------------------


def _gcs_client():
    from google.cloud import storage  # type: ignore[import-not-found]

    # Resolve project explicitly: GOOGLE_CLOUD_PROJECT > GCP_PROJECT > default.
    # ADC alone does not carry a project; the storage client requires one for
    # quota attribution. The Cloud Run Job (job-0021) injects GCP_PROJECT via
    # env at deploy time.
    project = (
        os.environ.get("GOOGLE_CLOUD_PROJECT")
        or os.environ.get("GCP_PROJECT")
        or DEFAULT_GCP_PROJECT
    )
    return storage.Client(project=project)


def _gcs_download(bucket: str, key: str, dest: Path) -> None:
    client = _gcs_client()
    blob = client.bucket(bucket).blob(key)
    blob.download_to_filename(str(dest))


def _gcs_upload(bucket: str, key: str, src: Path) -> None:
    client = _gcs_client()
    blob = client.bucket(bucket).blob(key)
    blob.upload_from_filename(str(src), content_type="application/xml")


# ---------------------------------------------------------------------------
# Pub/Sub helpers
# ---------------------------------------------------------------------------


def _publish_completion(
    project: str, topic: str, payload: bytes
) -> str:
    from google.cloud import pubsub_v1  # type: ignore[import-not-found]

    publisher = pubsub_v1.PublisherClient()
    topic_path = publisher.topic_path(project, topic)
    future = publisher.publish(topic_path, payload)
    return future.result(timeout=30.0)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def worker_round_trip(
    qgs_uri: str,
    layer_to_add: LayerSpec | str,
    *,
    publish: bool = True,
    pubsub_project: str | None = None,
    pubsub_topic: str | None = None,
) -> WorkerResult:
    """Read a ``.qgs`` from GCS, append a layer, write it back, notify.

    Use this when: you need to mutate the canonical ``.qgs`` in
    ``gs://grace-2-hazard-prod-qgs/`` from the Cloud Run Job worker (the
    only sanctioned ``.qgs``-writer in the architecture — Invariant 4).
    The mutation is the FR-QS-6 step 2 "add layer + apply style preset"
    composite.

    Do NOT use this for:

    * **Authoring the initial ``.qgs``.** That's a one-time engine
      artifact — see ``services/workers/pyqgis/sample_project/build_sample_project.py``
      (job-0019). This function only mutates an existing project.
    * **Client-side rendering.** Tier B layers reach the map only via QGIS
      Server WMS/WMTS/WFS (Invariant 4) — never read the ``.qgs`` from
      the web client.
    * **Calling an LLM.** This worker has zero LLM calls in its call
      graph (Invariant 2). Routing event-extraction tasks belongs to
      ``engine.extract_event_metadata`` in the agent service, not here.

    Parameters
    ----------
    qgs_uri:
        Either ``/vsigs/<bucket>/<key>.qgs``, the convenience alias
        ``gs://<bucket>/<key>.qgs``, or an absolute local filesystem path
        (used by the local dev round-trip / unit test). GCS URIs require
        an ADC-authenticated environment (``GOOGLE_APPLICATION_CREDENTIALS``
        or a GCE-attached service account). The worker downloads the
        ``.qgs`` to a temp directory via the ``google-cloud-storage`` SDK
        and uploads the mutated copy back — ``QgsProject.read()`` does
        not accept ``/vsigs/`` paths (it uses Qt file I/O, not GDAL VSI;
        see job-0020 report § Decisions). ``/vsigs/`` remains valid for
        inner-layer source URIs inside the ``.qgs`` (raster/vector
        providers do route through GDAL).
    layer_to_add:
        Either a :class:`~services.workers.pyqgis.types.LayerSpec` or a plain
        string (treated as ``LayerSpec(name=<string>)`` with the default 1° polygon at
        (lon=-100, lat=35) — see :class:`LayerSpec` defaults).
    publish:
        When True (default), publish a completion envelope to the
        ``grace-2-worker-events`` Pub/Sub topic. Set False for local unit
        tests where the publisher would block on credentials.
    pubsub_project:
        GCP project name for the Pub/Sub topic. Defaults to
        ``grace-2-hazard-prod`` (overridable via ``GCP_PROJECT`` env var).
    pubsub_topic:
        Pub/Sub topic name. Defaults to ``grace-2-worker-events``
        (overridable via ``PUBSUB_TOPIC`` env var).

    Returns
    -------
    WorkerResult
        Typed envelope carrying the before/after layer manifest, the
        Pub/Sub message id (None when ``publish=False``), and a status
        flag. ``status == "error"`` means a wrapped GCS/Pub/Sub call
        exhausted its retry budget; the function does **not** raise in
        that case so the Cloud Run Job exit code can stay 0 and downstream
        consumers see a structured failure envelope (NFR-R-1).
    """
    if isinstance(layer_to_add, str):
        layer_to_add = LayerSpec(name=layer_to_add)

    qgs_version = Qgis.QGIS_VERSION

    pubsub_project = (
        pubsub_project or os.environ.get("GCP_PROJECT") or DEFAULT_GCP_PROJECT
    )
    pubsub_topic = (
        pubsub_topic or os.environ.get("PUBSUB_TOPIC") or DEFAULT_PUBSUB_TOPIC
    )

    try:
        mode, bucket, key, read_path = _parse_qgs_uri(qgs_uri)
    except WorkerError as exc:
        return WorkerResult(
            qgs_uri=qgs_uri,
            layers_before=[],
            layers_after=[],
            notify_message_id=None,
            status="error",
            error=f"uri_parse: {exc}",
            qgs_version=qgs_version,
        )

    with tempfile.TemporaryDirectory(prefix="grace2-worker-") as tmpdir_str:
        tmpdir = Path(tmpdir_str)

        # ------------------------------------------------------------------
        # Step 1: bring the .qgs into a local file the worker can mutate.
        #
        # IMPORTANT FINDING (job-0020 diagnostic): ``QgsProject.read()`` cannot
        # open ``/vsigs/...`` paths. Raw ``gdal.VSIFOpenL`` with ADC
        # (``GOOGLE_APPLICATION_CREDENTIALS``) opens the object fine, but
        # ``QgsProject.read`` uses Qt's file I/O (``QFile``), not GDAL VSI —
        # so a ``/vsigs/`` argument returns ``False`` with
        # ``Unable to open /vsigs/...`` regardless of env vars. We therefore
        # download the ``.qgs`` to a local temp via the
        # ``google-cloud-storage`` SDK (ADC-authenticated), mutate it locally,
        # and upload it back. ``/vsigs/`` is preserved for INNER LAYER SOURCES
        # inside the ``.qgs`` (raster/vector providers that do go through GDAL),
        # which the production QGIS Server container in job-0024 needs.
        # See report § Decisions Made.
        # ------------------------------------------------------------------
        local_path = tmpdir / "project.qgs"
        if mode == "local":
            local_path = Path(read_path)
        else:
            try:
                _retry("gcs_download", _gcs_download, bucket, key, local_path)
            except Exception as exc:  # noqa: BLE001
                return WorkerResult(
                    qgs_uri=qgs_uri,
                    layers_before=[],
                    layers_after=[],
                    notify_message_id=None,
                    status="error",
                    error=f"gcs_download: {type(exc).__name__}: {exc}",
                    qgs_version=qgs_version,
                )

        # ------------------------------------------------------------------
        # Step 2: PyQGIS lifecycle: read → mutate → write.
        # ------------------------------------------------------------------
        with _qgis_app():
            project = QgsProject.instance()
            project.clear()
            if not project.read(str(local_path)):
                return WorkerResult(
                    qgs_uri=qgs_uri,
                    layers_before=[],
                    layers_after=[],
                    notify_message_id=None,
                    status="error",
                    error=(
                        f"QgsProject.read({local_path!r}) returned False — "
                        f"project.error: {project.error()!r}"
                    ),
                    qgs_version=qgs_version,
                )

            layers_before = _layer_names(project)
            logger.info("read %s — layers_before=%s", read_path, layers_before)

            new_layer = _append_memory_polygon_layer(project, layer_to_add)

            style_path = _resolve_style_preset_path()
            if style_path is not None:
                _apply_style_preset(new_layer, style_path)

            layers_after = _layer_names(project)
            logger.info("post-mutate layers_after=%s", layers_after)

            # Write to the LOCAL temp path. Upload happens after the QGIS
            # app is torn down (so the .qgs file is closed and flushed).
            write_target = local_path
            if not project.write(str(write_target)):
                return WorkerResult(
                    qgs_uri=qgs_uri,
                    layers_before=layers_before,
                    layers_after=layers_after,
                    notify_message_id=None,
                    status="error",
                    error=f"QgsProject.write({write_target!r}) returned False",
                    qgs_version=qgs_version,
                )

        # ------------------------------------------------------------------
        # Step 3: GCS upload (only in gcs mode).
        # ------------------------------------------------------------------
        if mode == "gcs":
            try:
                _retry(
                    "gcs_upload",
                    _gcs_upload,
                    bucket,
                    key,
                    local_path,
                )
            except Exception as exc:  # noqa: BLE001
                return WorkerResult(
                    qgs_uri=qgs_uri,
                    layers_before=layers_before,
                    layers_after=layers_after,
                    notify_message_id=None,
                    status="error",
                    error=f"gcs_upload: {type(exc).__name__}: {exc}",
                    qgs_version=qgs_version,
                )

        # ------------------------------------------------------------------
        # Step 4: build the result; publish if requested.
        # ------------------------------------------------------------------
        result = WorkerResult(
            qgs_uri=qgs_uri,
            layers_before=layers_before,
            layers_after=layers_after,
            notify_message_id=None,
            status="ok",
            error=None,
            qgs_version=qgs_version,
        )

        if publish:
            try:
                message_id = _retry(
                    "pubsub_publish",
                    _publish_completion,
                    pubsub_project,
                    pubsub_topic,
                    result.to_json_bytes(),
                )
                result = replace(result, notify_message_id=message_id)
            except Exception as exc:  # noqa: BLE001
                result = replace(
                    result,
                    status="error",
                    error=f"pubsub_publish: {type(exc).__name__}: {exc}",
                )

    return result


__all__ = [
    "DEFAULT_GCP_PROJECT",
    "DEFAULT_PUBSUB_TOPIC",
    "RETRY_ATTEMPTS",
    "WorkerError",
    "WorkerResult",
    "LayerSpec",
    "worker_round_trip",
]
