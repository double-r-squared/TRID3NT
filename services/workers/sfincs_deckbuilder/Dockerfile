# GRACE-2 SFINCS deck-builder worker (coastal North Star gate).
#
# Authors a MULTI-LEVEL refined SFINCS quadtree + SnapWave deck from scratch via
# Deltares `cht_sfincs` (GPL-3.0, pinned git commit), then writes the deck + a
# run_solver-compatible manifest.json back to the object store.
#
# GPL ISOLATION — load-bearing:
#   cht_sfincs is GPL-3.0 and lives ONLY in THIS image. It is NEVER installed in
#   the agent venv and NEVER imported by agent code; the agent reaches this worker
#   arms-length over the object-store + AWS-Batch-submit seam (same pattern as the
#   MIT-licensed solve worker `services/workers/sfincs/`). This image is therefore
#   a distinct Batch job-definition (e.g. GRACE2_AWS_BATCH_JOB_DEF_SFINCS_DECK),
#   never cross-routed with the solve image.
#
# MULTI-STAGE (container-hygiene norm — pre-push inspect for bare-bones size):
#   Stage 1 (builder): full build toolchain (gcc/g++/gfortran, GDAL/GEOS/PROJ
#     -dev headers) compiles every wheel into an isolated venv at /opt/venv,
#     including the 4 cht_* git deps + cht_sfincs @ the pinned commit.
#   Stage 2 (runtime): slim python base + only the RUNTIME GDAL/GEOS/PROJ shared
#     libs + the copied /opt/venv. No compilers, no -dev headers, no apt cache,
#     no git, no pip cache. cht's ~128-package closure (numba/llvmlite, dask,
#     datashader, netcdf4, rasterio, geopandas, xugrid, scipy, ...) dominates the
#     size — pruned __pycache__/tests/.pyc keeps it lean.
#
# IMAGE SIZE ESTIMATE (would-be — NO docker on this box; build is a deferred
# EC2/SSM step): the cht closure venv is ~1.2 GB on disk (measured in the spike
# venv). With the python:3.12-slim runtime base (~150 MB), runtime GDAL/GEOS/PROJ
# shared libs (~120 MB), and __pycache__/tests pruning (~-200 MB), the runtime
# image is ESTIMATED at ~1.3-1.5 GB compressed-on-disk (uncompressed). This is a
# heavy-but-bounded scientific Python image (numba/llvmlite + GDAL stack are the
# bulk); it stays under the 2 GB AgentCore Runtime ceiling noted in MEMORY.

# --------------------------------------------------------------------------- #
# Stage 1 — builder: compile the full cht_sfincs closure into /opt/venv.
# --------------------------------------------------------------------------- #
FROM python:3.12-slim-bookworm AS builder

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Build-time system deps: compilers + GDAL/GEOS/PROJ dev headers (rasterio,
# pyogrio, shapely, fiona-less geopandas all build against these) + git (the 4
# cht_* deps + cht_sfincs are git installs).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        gfortran \
        git \
        libgdal-dev \
        libgeos-dev \
        libproj-dev \
        proj-bin \
        libnetcdf-dev \
        libhdf5-dev \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Isolated venv so the runtime stage copies a single self-contained tree.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --upgrade pip setuptools wheel

# cht_sfincs PINNED to the exact commit proven in the spike venv. Installing
# cht_sfincs pulls its 4 cht_* git deps (cht_utils/cht_bathymetry/cht_tide/
# cht_tiling) + the ~120 PyPI deps (numpy/xarray/rasterio/geopandas/xugrid/
# datashader/numba/scipy/netcdf4/...). boto3 + google-cloud-storage are the
# object-store SDKs the entrypoint dispatches on by URI scheme.
ARG CHT_SFINCS_COMMIT=159df40df35042b37c906242bbbe47938652efe9
RUN pip install --no-cache-dir \
        "cht_sfincs @ git+https://github.com/Deltares-research/cht_sfincs.git@${CHT_SFINCS_COMMIT}" \
        "boto3>=1.34,<2" \
        "google-cloud-storage>=2.18,<4" \
    && python -c "from cht_sfincs import SFINCS; import rasterio, geopandas, xugrid, boto3; print('cht_sfincs + geo stack + boto3 import OK')"

# Prune the venv: byte-compiled caches, tests, and matplotlib/contextily sample
# data are dead weight in a one-shot batch worker.
RUN find /opt/venv -depth -type d -name '__pycache__' -exec rm -rf {} + \
    && find /opt/venv -depth -type d -name 'tests' -exec rm -rf {} + \
    && find /opt/venv -type f -name '*.pyc' -delete

# --------------------------------------------------------------------------- #
# Stage 2 — runtime: slim base + runtime geo libs + the prebuilt venv only.
# --------------------------------------------------------------------------- #
FROM python:3.12-slim-bookworm AS runtime

LABEL org.opencontainers.image.title="grace-2-sfincs-deckbuilder" \
      org.opencontainers.image.source="https://github.com/double-r-squared/GRACE-2" \
      org.opencontainers.image.description="GRACE-2 SFINCS quadtree+SnapWave deck-builder (AWS Batch). Reads a build-spec JSON, authors the deck via cht_sfincs (GPL-3.0), writes the deck + run_solver manifest.json to the object store. GPL code is image-only; never the agent." \
      org.opencontainers.image.licenses="GPL-3.0-or-later"

ENV DEBIAN_FRONTEND=noninteractive

# RUNTIME-ONLY shared libs (no -dev headers, no compilers). These satisfy the
# dynamic links rasterio/pyogrio/shapely/netcdf4 baked at build time.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libgdal32 \
        libgeos-c1v5 \
        libproj25 \
        proj-data \
        libnetcdf19 \
        libhdf5-103-1 \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy the self-contained venv from the builder.
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Worker code lands on PYTHONPATH so `-m services.workers.sfincs_deckbuilder.entrypoint`
# resolves. Build context is the repo root; paths are repo-root-relative.
RUN mkdir -p /opt/grace2/services/workers/sfincs_deckbuilder
COPY services/workers/sfincs_deckbuilder/ /opt/grace2/services/workers/sfincs_deckbuilder/

WORKDIR /opt/grace2

ENV PYTHONPATH=/opt/grace2 \
    PYTHONUNBUFFERED=1 \
    GRACE2_DECK_SCRATCH=/opt/grace2/work \
    GCP_PROJECT=grace-2-hazard-prod \
    GRACE2_CACHE_BUCKET=grace-2-hazard-prod-cache \
    GRACE2_RUNS_BUCKET=grace-2-hazard-prod-runs \
    GRACE2_RUN_ID="" \
    GRACE2_BUILD_SPEC_URI=""

# Build-time smoke: cht_sfincs imports + the entrypoint module imports cleanly.
# Catches a base-image path shift or an import mistake at build time.
RUN python -c "from cht_sfincs import SFINCS; print('cht_sfincs OK')" \
    && python -c "from services.workers.sfincs_deckbuilder.entrypoint import main, validate_build_spec, compose_manifest, normalize_snapwave_time_columns; print('entrypoint import OK')"

# One-shot Batch worker: argv flows straight to the entrypoint module; env-var
# fallbacks (GRACE2_RUN_ID, GRACE2_BUILD_SPEC_URI) are parsed if no CLI flags.
ENTRYPOINT ["python", "-m", "services.workers.sfincs_deckbuilder.entrypoint"]
CMD []
