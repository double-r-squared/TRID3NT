# GRACE-2 QGIS Server (sprint-04 / M2 / FR-QS-1; job-0024 image rebuild).
#
# Extends the official qgis/qgis-server image pinned by digest so a build that
# rolls forward only does so deliberately. The base tag is 3.40.15-noble — the
# latest patch on the QGIS 3.40 LTR line (matches the QGIS 3.40.3-Bratislava
# referenced for the future grace2 conda env on Linux; same minor series, so
# PyQGIS-worker projects opened in the env load cleanly into the server).
#
# Bake-in layout (job-0024 picks up OQ-19C):
#   - QML preset directory canonical path: /etc/qgis/styles/ — the path the
#     worker code's `apply_style_preset(layer, name)` resolves preset names
#     against, and the path Cloud Run env GRACE2_STYLES_DIR points to (set
#     in infra/qgis-server.tf to /etc/qgis/styles).
#   - /opt/grace2/styles/ kept as a back-compat alias for any tooling that
#     still reads the prior GRACE2_STYLES_DIR. Both are populated from
#     repo styles/ at build time so the engine-authored basemap.qml
#     (job-0019) bakes into the image with this build.
#   - GRASS + SAGA processing-algorithm plugins: ON DISK in the base image's
#     QGIS Server libraries (libqgis-analysis3.40.15 etc.) and reachable when
#     `.qgs` projects reference them. `qgis_process` CLI installed via the
#     full `qgis` package for FR-AS-9 Level 1a discovery.
#
# /vsigs/ vs filesystem path for .qgs (OQ-19A, resolved live in this job):
#   - PATH (c) — GDAL VSI env vars (CPL_MACHINE_IS_GCE=YES,
#     CPL_GS_USE_INSTANCE_PROFILE=YES) — tried first, DEPLOYED, then FAILED:
#     server still logged `CRITICAL Server[18]: Error when loading project
#     file '/vsigs/...': Unable to open /vsigs/...`. Root cause: QGIS Server
#     loads the .qgs via Qt's QFile (not GDAL VSI); env vars only help
#     internal data-layer references inside the project, not the project
#     file itself. Env vars left in place (zero-cost) because they may help
#     `.qgs` projects that reference COG/FlatGeobuf layers via /vsigs/.
#   - PATH (b) — Cloud Run native GCS volume mount (gen2 service mounts the
#     .qgs bucket at /mnt/qgs via the Cloud Run runtime — no gcsfuse install
#     in the image, no startup-wrapper PID-1 gymnastics, no service-account
#     key file). The mount uses the Cloud Run runtime SA (the qgis-server
#     SA with bucket-scoped roles/storage.objectViewer). WMS URL becomes
#     `MAP=/mnt/qgs/<file>.qgs` (filesystem path). This is provisioned in
#     infra/qgis-server.tf via the google_cloud_run_v2_service `volumes` +
#     `volume_mounts` blocks — no Dockerfile change needed for the FUSE
#     plumbing.
#
# No "support both /vsigs/ and /mnt/" code path exists anywhere — the WMS
# canonical URL is filesystem-path now (per "No legacy support pre-MVP").

FROM qgis/qgis-server@sha256:cd29c271e45f0a0791078e755b9dec48d9e07902db68cd06e1dab95e23b84793

LABEL org.opencontainers.image.title="grace-2-qgis-server" \
      org.opencontainers.image.source="https://github.com/double-r-squared/GRACE-2" \
      org.opencontainers.image.description="GRACE-2 QGIS Server (3.40 LTR) — bakes QML presets at /etc/qgis/styles/; .qgs bucket arrives via Cloud Run GCS volume mount at /mnt/qgs/." \
      org.opencontainers.image.licenses="MIT"

# Install `qgis_process` (FR-AS-9 / FR-QS-1: "qgis_process CLI exposed").
# The bare qgis/qgis-server image ships only the FCGI mapserver — `qgis_process`
# is in the `qgis` package alongside the desktop binary.
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        qgis \
    && rm -rf /var/lib/apt/lists/* \
    && which qgis_process \
    && qgis_process --version

# QML preset library (FR-QS-5). job-0019 authored styles/basemap.qml; this
# image bakes the file into the canonical preset path AND the legacy alias
# so either lookup resolves.
#
# Provenance: COPY runs against the Cloud Build context (repo root — see
# infra/qgis-server/cloudbuild.yaml), so styles/ is the repo's engine-owned
# directory at HEAD.
RUN mkdir -p /etc/qgis/styles /opt/grace2/styles
COPY styles/ /etc/qgis/styles/
COPY styles/ /opt/grace2/styles/

# QGIS Server tuning. Map env vars are also declared in infra/qgis-server.tf
# (Cloud Run env block); deploy-time vars override these defaults.
ENV QGIS_SERVER_LOG_LEVEL=0 \
    QGIS_SERVER_LOG_STDERR=true \
    QGIS_SERVER_PARALLEL_RENDERING=true \
    QGIS_SERVER_MAX_THREADS=2 \
    QGIS_SERVER_CACHE_DIRECTORY=/tmp/qgis-server-cache \
    QGIS_SERVER_CACHE_SIZE=50000000 \
    QGIS_PROJECT_FILE="" \
    QGIS_SERVER_IGNORE_BAD_LAYERS=false \
    GRACE2_STYLES_DIR=/etc/qgis/styles

# Smoke at build time: `qgis_process` reachable.
RUN qgis_process --version >/dev/null \
    && echo "qgis_process: OK"

# Smoke at build time: the engine-authored basemap.qml is present at the
# canonical preset path. Fails the build if styles/basemap.qml was somehow
# missing from the Cloud Build context (caught earlier than a runtime error).
RUN test -f /etc/qgis/styles/basemap.qml \
    && echo "QML preset basemap.qml: baked" \
    && ls -la /etc/qgis/styles/

# Upstream CMD/ENTRYPOINT remain unchanged: xvfb-run -a /usr/local/bin/start-server.sh
# spawns the FCGI mapserver behind nginx on port 80. Cloud Run probes port 80
# via the infra/qgis-server.tf `ports { container_port = 80 }` block. The
# .qgs bucket is mounted at /mnt/qgs by the Cloud Run runtime (declared in
# the TF service's volumes + volume_mounts blocks) BEFORE the container
# starts — no init/wrapper script needed.
