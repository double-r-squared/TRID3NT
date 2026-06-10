# GRACE-2 Python sandbox Cloud Run Job (sprint-13 Stage 2 / job-0232).
#
# Builds the runtime image for the conversational-analysis Python sandbox — a
# Cloud Run Job that executes LLM-emitted / user-confirmed Python against layers
# already on the map (see memory `project_conversational_data_analysis_layer` +
# sprint-13 manifest job-0232). The image bakes the executor harness
# (infra/python-sandbox/executor.py) and a fixed analytical-Python toolkit; the
# code it runs is supplied per-execution as a job payload (NOT baked in).
#
# Base-image + venv discipline (mirror of services/workers/modflow/Dockerfile):
#   python:3.11-slim (Debian Bookworm slim). Bookworm's pip 23+ enforces PEP 668,
#   so we install deps into a venv at /opt/grace2/.venv rather than using
#   --break-system-packages — identical to the MODFLOW worker. GDAL/PROJ system
#   libs are pulled in for rasterio/geopandas (they need libgdal at run time; the
#   manylinux wheels bundle their own GDAL but geopandas' pyogrio + rasterio are
#   most robust with the system libgdal present — we install libgdal-dev to be
#   safe and let the wheels link against the bundled copy).
#
# Image digest discipline (mirror of infra/sfincs.tf + infra/modflow.tf): after
# `make python-sandbox-build` Cloud Build logs the resolved AR image digest;
# record it in infra/python-sandbox.tf's `python_sandbox_image_digest` local so
# `tofu plan` stays an honest source of truth for what's deployed.
#
# Dependency pins are kept CONSISTENT with services/agent/pyproject.toml where the
# package is shared (google-cloud-storage>=2.18,<3; pandas>=2.2,<2.3;
# geopandas>=1.0,<2; rasterio>=1.3,<2) so the host-side local-subprocess fallback
# (GRACE2_SANDBOX_LOCAL=1, runs in the agent venv) and the container agree on
# behavior. numpy/matplotlib/scikit-learn/networkx/gcsfs are sandbox-only.

FROM python:3.11-slim

LABEL org.opencontainers.image.title="grace-2-python-sandbox" \
      org.opencontainers.image.source="https://github.com/double-r-squared/GRACE-2" \
      org.opencontainers.image.description="GRACE-2 conversational-analysis Python sandbox (Cloud Run Job). Executes a user-confirmed python_code payload against pre-opened gcsfs-backed layer handles under a 60s wallclock cap + bounded output, with network egress denied at the VPC layer. Read-only GCS (cache/ + runs/) via a dedicated objectViewer-only SA." \
      org.opencontainers.image.licenses="MIT"

# --- System deps (single layer) ------------------------------------------
# ca-certificates (HTTPS to GCS/Atlas), plus GDAL/GEOS/PROJ runtime libs for
# rasterio + geopandas (pyogrio/shapely). The manylinux wheels bundle their own
# copies, but installing the system libs avoids the occasional dlopen mismatch on
# slim bases and gives geopandas' pyogrio a stable GDAL to bind.
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        ca-certificates \
        libgdal-dev \
        libgeos-dev \
        libproj-dev \
    && rm -rf /var/lib/apt/lists/*

# --- Python venv + deps (single layer) -----------------------------------
# venv at /opt/grace2/.venv to sidestep PEP 668 on Bookworm (mirror of the
# MODFLOW worker). The build-time import smoke asserts every dep loads before the
# image is pushed — catches a wheel/ABI mismatch at build, not at Cloud Run exec.
ENV VENV=/opt/grace2/.venv
RUN python3 -m venv "$VENV" \
    && "$VENV/bin/pip" install --no-cache-dir --upgrade pip \
    && "$VENV/bin/pip" install --no-cache-dir \
        "numpy>=1.26,<3" \
        "pandas>=2.2,<2.3" \
        "rasterio>=1.3,<2" \
        "geopandas>=1.0,<2" \
        "matplotlib>=3.8,<4" \
        "scikit-learn>=1.4,<2" \
        "networkx>=3.2,<4" \
        "gcsfs>=2024.2,<2026" \
        "google-cloud-storage>=2.18,<3" \
    && "$VENV/bin/python" -c "import numpy, pandas, rasterio, geopandas, matplotlib, sklearn, networkx, gcsfs; from google.cloud import storage; print('sandbox deps import OK — numpy', numpy.__version__, 'geopandas', geopandas.__version__)"

# venv on PATH so python3/pip resolve to the venv for all later layers + run time.
ENV PATH="$VENV/bin:$PATH"

# --- COPY the executor harness -------------------------------------------
# Build context is the repo root (see infra/python-sandbox/cloudbuild.yaml); the
# COPY path is relative to repo root. ONLY the executor + this dir are baked; the
# code the sandbox RUNS is supplied at exec time as a job payload, never baked.
RUN mkdir -p /opt/grace2/sandbox
COPY infra/python-sandbox/executor.py /opt/grace2/sandbox/executor.py

WORKDIR /opt/grace2/sandbox

# Headless matplotlib + unbuffered logs. MPLBACKEND=Agg is mandatory: the
# container has no display, and matplotlib's default backend probe would fail.
ENV PYTHONUNBUFFERED=1 \
    MPLBACKEND=Agg \
    PYTHONPATH=/opt/grace2/sandbox \
    GRACE2_SANDBOX_TIMEOUT=60

# Runtime config — overridable from the Cloud Run Job env block + Workflow
# per-execution overrides.
#   GCP_PROJECT                    — google-cloud-storage client project.
#   GRACE2_CACHE_BUCKET            — read-only source bucket (layer refs may point here).
#   GRACE2_RUNS_BUCKET             — read-only source bucket (solver outputs).
#   GRACE2_SANDBOX_PAYLOAD_URI     — gs:// staging file with the python_code +
#                                    layer_refs payload (set per execution).
#   GRACE2_SANDBOX_NET_ALLOW       — comma-sep host-suffix allowlist for the
#                                    in-process net guard (GCS + Atlas).
ENV GCP_PROJECT=grace-2-hazard-prod \
    GRACE2_CACHE_BUCKET=grace-2-hazard-prod-cache \
    GRACE2_RUNS_BUCKET=grace-2-hazard-prod-runs \
    GRACE2_SANDBOX_PAYLOAD_URI="" \
    GRACE2_SANDBOX_NET_ALLOW="googleapis.com,google.internal,mongodb.net,localhost,127.0.0.1,::1"

# Build-time smoke (mirror of the SFINCS/MODFLOW Dockerfile asserts): the executor
# module imports cleanly AND a trivial in-process run succeeds. Catches a syntax
# error or import mistake at build time instead of at Cloud Run exec.
RUN python3 -c "import executor; env = executor.run_user_code('result = 1 + 1', {}, install_guard=True); assert env['status']=='ok' and env['result']['value']==2, env; print('executor harness smoke OK')"

# ENTRYPOINT runs this image as a one-shot sandbox Job. The harness reads its
# payload from GRACE2_SANDBOX_PAYLOAD_URI (gs://) or a --payload-file arg; it
# prints exactly one JSON envelope line to stdout. CMD empty so Cloud Run Job task
# args feed straight through as argv.
ENTRYPOINT ["python3", "-m", "executor"]
CMD []
