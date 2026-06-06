# GRACE-2 PyQGIS worker (sprint-04 / M2 / FR-QS-6 / job-0021).
#
# Builds the runtime image for the Cloud Run Job that executes the canonical
# six-step PyQGIS worker round-trip (FR-QS-6): read `.qgs` from GCS, mutate
# via PyQGIS, write it back, publish a typed completion envelope to Pub/Sub.
#
# Base-image choice — extend the SAME qgis/qgis-server image digest the
# QGIS Server container (infra/qgis-server/Dockerfile) pins. Same Qt + GDAL
# + PROJ + QGIS 3.40 LTR runtime → eliminates a "QGIS Server reads a `.qgs`
# the worker wrote in a different QGIS version" drift class. PyQGIS Python
# bindings are installed on top via the `python3-qgis` apt package; the
# server image's `qgis` install already covers most of the dependency
# closure.
#
# Decisions worth surfacing here (also in report.md § Decisions Made):
#   - Extend qgis/qgis-server vs python:3.12 + conda-forge PyQGIS:
#     extend the server image. Symmetry with infra/qgis-server/Dockerfile,
#     one Qt/GDAL stack, no conda layer in production. Trade-off: the
#     image carries the FCGI + nginx surface the Job will never use (the
#     Job overrides ENTRYPOINT to `python -m services.workers.pyqgis`).
#     Acceptable at M2; revisit if image-size becomes operational.
#   - python3-qgis vs `qgis` only: install `python3-qgis` explicitly so
#     `from qgis.core import QgsApplication` resolves inside the container.
#     (The bare server image has libqgis libraries but does not always
#     expose the Python bindings on PYTHONPATH.)
#   - System pip vs venv: install google-cloud-* via system pip with
#     `--break-system-packages` (Debian PEP 668 guard). A venv adds a layer
#     and a PATH dance for no gain in a single-purpose container.

FROM qgis/qgis-server@sha256:cd29c271e45f0a0791078e755b9dec48d9e07902db68cd06e1dab95e23b84793

LABEL org.opencontainers.image.title="grace-2-pyqgis-worker" \
      org.opencontainers.image.source="https://github.com/double-r-squared/GRACE-2" \
      org.opencontainers.image.description="GRACE-2 PyQGIS worker (Cloud Run Job) — FR-QS-6 round-trip. Reads .qgs from GCS via /mnt/qgs mount or google-cloud-storage SDK, mutates via PyQGIS, writes back, publishes completion to Pub/Sub." \
      org.opencontainers.image.licenses="MIT"

# Install Python 3 + PyQGIS bindings + google cloud SDKs.
# - `python3` + `python3-pip` give us a python interpreter (the bare
#   qgis/qgis-server image does not include one).
# - `python3-qgis` exposes `from qgis.core import ...` on PYTHONPATH and
#   pins to the same QGIS major+minor as the FCGI server in the image.
# - `--break-system-packages` is the PEP 668 escape hatch — required on
#   Debian-derived images where `/usr/lib/python3.*/EXTERNALLY-MANAGED`
#   blocks system-pip installs. We accept the warning: this is a single-
#   purpose container, not a host Python.
# Versions are pinned to match what's currently published on PyPI and what
# the agent + worker code expect:
#   - google-cloud-storage>=2.18 (matches `grace2` env, job-0022)
#   - google-cloud-pubsub>=2.21 (matches `grace2` env, job-0022)
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        python3-qgis \
    && rm -rf /var/lib/apt/lists/* \
    && pip3 install --no-cache-dir --break-system-packages \
        "google-cloud-storage>=2.18,<4" \
        "google-cloud-pubsub>=2.21,<3" \
    && python3 -c "from qgis.core import QgsApplication; print('PyQGIS import OK')" \
    && python3 -c "from google.cloud import storage, pubsub_v1; print('google-cloud import OK')"

# COPY the worker code into a stable runtime path. The repo root is the
# Cloud Build context (see infra/worker/cloudbuild.yaml); paths below are
# relative to repo root.
#
# `services/workers/pyqgis/` is owned by job-0020 (frozen for this job).
# `styles/` is owned by job-0019 (frozen for this job). Both are copied
# verbatim — no editing.
RUN mkdir -p /opt/grace2/services/workers/pyqgis /opt/styles
COPY services/workers/pyqgis/ /opt/grace2/services/workers/pyqgis/
COPY styles/ /opt/styles/

# WORKDIR controls where `python -m services.workers.pyqgis` resolves the
# module from. Setting WORKDIR=/opt/grace2 and prepending it to PYTHONPATH
# (PYTHONPATH=/opt/grace2:$PYTHONPATH) lets `-m services.workers.pyqgis`
# discover the package without an editable pip install.
WORKDIR /opt/grace2
ENV PYTHONPATH=/opt/grace2 \
    PYTHONUNBUFFERED=1

# Runtime config — overridable from the Cloud Run Job env block.
#
# GCP_PROJECT / PUBSUB_TOPIC are read by services/workers/pyqgis/worker.py
# (job-0020). The Cloud Run Job sets them too; the defaults here are a
# defensive backstop so a manual `docker run` still hits the right topic.
ENV GCP_PROJECT=grace-2-hazard-prod \
    PUBSUB_TOPIC=grace-2-worker-events

# Headless Qt platform plugin — required so QgsApplication([], False) does
# not SIGSEGV on Cloud Run (no X server, no Wayland). The qgis/qgis-server
# upstream uses `xvfb-run` for its FCGI server CMD; the worker bypasses
# that wrapper (ENTRYPOINT below) and so must request the offscreen Qt
# platform explicitly. Without this, the first QgsApplication() ctor
# crashes the process with signal 11 before any Python output reaches
# stdout (diagnosed live in job-0021).
ENV QT_QPA_PLATFORM=offscreen

# GDAL VSI auth env vars — mirror infra/qgis-server.tf so any /vsigs/
# layer-data references inside `.qgs` projects resolve under the worker's
# Cloud Run runtime SA without a service-account-key file. These also help
# the worker if a future task switches the read path back to /vsigs/.
ENV CPL_MACHINE_IS_GCE=YES \
    CPL_GS_USE_INSTANCE_PROFILE=YES \
    GDAL_HTTP_USERAGENT=grace-2-pyqgis-worker/0.1

# Build-time smoke: the worker module imports cleanly. Catches a missing
# `services/workers/pyqgis/__init__.py` or a PYTHONPATH mistake at build
# time instead of at Cloud Run Job exec time.
RUN python3 -c "from services.workers.pyqgis.worker import worker_round_trip; print('worker import OK')" \
    && python3 -c "from services.workers.pyqgis.types import LayerSpec, WorkerResult; print('worker types OK')"

# Build-time smoke: the QML preset baked into /opt/styles/ matches what the
# worker code looks for (services/workers/pyqgis/worker.py
# STYLE_PRESET_CONTAINER_PATH = /opt/styles/basemap.qml).
RUN test -f /opt/styles/basemap.qml \
    && echo "QML preset basemap.qml: baked at /opt/styles/basemap.qml" \
    && ls -la /opt/styles/

# ENTRYPOINT overrides the upstream `xvfb-run start-server.sh` so this
# image runs as a one-shot worker, not a server. CMD is empty so Cloud Run
# Job task args (`--qgs-uri ... --layer-to-add ...`) feed straight through
# as argv to the module. Env-var fallbacks (QGS_URI, LAYER_TO_ADD) are
# parsed by services/workers/pyqgis/__main__.py.
ENTRYPOINT ["python3", "-m", "services.workers.pyqgis"]
CMD []
