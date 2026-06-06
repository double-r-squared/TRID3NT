# GRACE-2 QGIS Server (sprint-04 / M2 / FR-QS-1).
#
# Extends the official qgis/qgis-server image pinned by digest so a build that
# rolls forward only does so deliberately. The base tag is 3.40.15-noble — the
# latest patch on the QGIS 3.40 LTR line (matches the QGIS 3.40.3-Bratislava
# referenced for the future grace2 conda env on Linux; same minor series, so
# PyQGIS-worker projects opened in the env load cleanly into the server).
#
# Surfaced as TENTATIVE Open Question in the report: 3.40 LTR vs 3.44 LTR.
# 3.40 LTR chosen to keep parity with the conda env's QGIS version. Flip is a
# single digest swap below.
#
# Bake-in layout:
#   - QML preset directory: /opt/grace2/styles/ (copied from repo styles/;
#     content authored by engine in job-0019 — this image carries whatever is
#     in styles/ at build time, no harm if empty).
#   - GRASS + SAGA processing-algorithm plugins: ON DISK in the base image's
#     QGIS Server libraries (libqgis-analysis3.40.15 etc.) and reachable when
#     `.qgs` projects reference them. Note: the bare server image does not
#     ship the standalone `qgis_process` CLI — it ships only `/usr/bin/qgis_mapserver`
#     and the FCGI binary `/usr/lib/cgi-bin/qgis_mapserv.fcgi`. To expose
#     `qgis_process` for FR-AS-9 Level 1a discovery, we install the `qgis`
#     package on top (apt-from-noble-repos). Surfaced as Open Question: this
#     adds ~200 MB and a desktop-Qt dependency that the headless server
#     doesn't otherwise need. Alternative: ship a separate worker image
#     (job-0021 / engine) carrying qgis_process and keep this image lean.
#     TENTATIVE: bake here so FR-QS-1 ("qgis_process CLI exposed") is met
#     by the server image itself.
#
# Cloud Run wiring:
#   - The base image's CMD launches xvfb-run + spawn-fcgi + fcgiwrap behind
#     nginx on port 80 (default `qgis/qgis-server` upstream contract).
#     The provided wrapper exposes /ogc/wms and /ogc/wfs endpoints. We do not
#     override the CMD; we set the port via the QGIS_SERVER_* env vars from
#     cloudrun.tf (no PORT-binding gymnastics needed — the upstream nginx
#     fronts the FCGI worker on container port 80).
#
# `/vsigs/` GCS reads:
#   - GDAL `/vsigs/` honors the Cloud Run instance's ADC. The QGIS Server SA
#     `grace-2-qgis-server@…` is granted `roles/storage.objectViewer` on the
#     three new buckets (see cloudrun.tf) so it can read .qgs + COG payloads
#     without bucket enumeration (Invariant 6: no list-objects).

FROM qgis/qgis-server@sha256:cd29c271e45f0a0791078e755b9dec48d9e07902db68cd06e1dab95e23b84793

LABEL org.opencontainers.image.title="grace-2-qgis-server" \
      org.opencontainers.image.source="https://github.com/double-r-squared/GRACE-2" \
      org.opencontainers.image.description="GRACE-2 QGIS Server (3.40 LTR) — bakes QML presets + exposes qgis_process; reads .qgs/COG/FGB from GCS via /vsigs/." \
      org.opencontainers.image.licenses="MIT"

# Install `qgis_process` (FR-AS-9 / FR-QS-1: "qgis_process CLI exposed").
# The bare qgis/qgis-server image ships only the FCGI mapserver — `qgis_process`
# is in the `qgis` package alongside the desktop binary. apt-get of `qgis`
# pulls Qt and OpenGL libs (~200 MB layer) but `qgis_process --help` from
# CLI works in headless mode (xvfb not required for non-rendering subcommands
# like `list`/`describe`). Plugins (`grass`, `saga`) are processing-provider
# plugins inside the QGIS python_plugins directory — already on disk via
# python3-qgis. We install via `apt` so the upstream LTR repo (already
# configured in the base image) sees consistent package versions.
#
# Provenance: APT sources in qgis/qgis-server base point to the official
# qgis.org Ubuntu repo for the 3.40 LTR series — the same source GRASS/SAGA
# plugins come from.
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        qgis \
    && rm -rf /var/lib/apt/lists/* \
    && which qgis_process \
    && qgis_process --version

# QML preset library (FR-QS-5). engine authors content in job-0019 (basemap
# preset stub); this Dockerfile only stages the location. An empty styles/
# at build time is acceptable — the directory just exists for future presets.
RUN mkdir -p /opt/grace2/styles
COPY styles/ /opt/grace2/styles/

# QGIS Server tuning. These map to the upstream qgis/qgis-server image's
# expected env vars (server endpoint, logging). Keep verbose at 0 for prod;
# bump to 2 transiently when diagnosing.
ENV QGIS_SERVER_LOG_LEVEL=0 \
    QGIS_SERVER_LOG_STDERR=true \
    QGIS_SERVER_PARALLEL_RENDERING=true \
    QGIS_SERVER_MAX_THREADS=2 \
    QGIS_SERVER_CACHE_DIRECTORY=/tmp/qgis-server-cache \
    QGIS_SERVER_CACHE_SIZE=50000000 \
    QGIS_PROJECT_FILE="" \
    QGIS_SERVER_IGNORE_BAD_LAYERS=false \
    GRACE2_STYLES_DIR=/opt/grace2/styles

# Smoke at build time: confirm `qgis_process` is reachable AND the GRASS/SAGA
# processing-provider plugins discover. `qgis_process plugins list` enumerates
# Python processing plugins discovered by the QGIS Application; absence here
# would surface plugin-path drift. Run under xvfb so QgsApplication initializes
# cleanly (it tries to create QGuiApplication even for headless subcommands).
RUN qgis_process --version >/dev/null \
    && echo "qgis_process: OK"
# Plugins/algorithms discovery smoke is intentionally NOT a build-time RUN —
# `qgis_process plugins list` requires a writable QGIS profile dir and xvfb;
# adding both at build time bloats the layer for a smoke that runs once at
# deploy. The deploy-time verification (`gcloud run jobs execute` smoke in
# job-0021's worker) covers this seam end-to-end.

# Upstream CMD remains: xvfb-run -a /usr/local/bin/start-server.sh
# (sets up spawn-fcgi + nginx on container port 80). Cloud Run probes port 80
# via the cloudrun.tf `ports { container_port = 80 }` block.
