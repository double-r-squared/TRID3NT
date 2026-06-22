# GRACE-2 GeoClaw (Clawpack) solver AWS Batch worker (sprint-17 / FR-CE-1).
#
# Builds the runtime image for the GeoClaw AWS Batch job — the scale-to-zero
# CLOUD LANE for the GeoClaw shallow-water inundation engine (tsunami / dam-break
# / surge run-up). GeoClaw is the Fortran solver inside Clawpack; unlike SWMM
# (a pure-python pip wheel) Clawpack COMPILES its Fortran from source at pip
# install (it has NO prebuilt wheel with the GeoClaw binaries), so the image
# needs a Fortran toolchain (gfortran) at BUILD time and the standard Clawpack
# Makefile machinery + gfortran at RUN time (``make .output`` recompiles the
# per-deck Fortran driver). The entrypoint shim performs the
# S3/GCS-IN -> author-setrun.py -> Clawpack-RUN -> S3/GCS-OUT envelope; we do NOT
# modify the GeoClaw Fortran.
#
# DESIGN NOTE (why NOT multi-stage venv-copy like SWMM): GeoClaw's runtime solve
# is ``make .output``, which RECOMPILES a small per-deck Fortran driver against
# the installed clawpack Fortran sources + libs each run. So gfortran + make +
# the clawpack source tree must be PRESENT at run time, not just build time.
# Stripping them (the SWMM venv-copy hygiene) would break the solve. We instead
# keep the single-stage image lean by: a slim base, --no-install-recommends,
# PIP_NO_CACHE_DIR, and apt-list cleanup — the container-hygiene contract adapted
# to a compile-at-runtime engine. (Cost note: this is the heaviest worker base;
# the engine is scale-to-zero so it is paid only per solve.)
#
# Version pins (mirror the SFINCS/MODFLOW/SWMM digest discipline — bumps are a
# deliberate, IaC-visible step): python:3.11-slim-bookworm + clawpack 5.x.

FROM python:3.11-slim-bookworm AS runtime

LABEL org.opencontainers.image.title="grace-2-geoclaw-solver" \
      org.opencontainers.image.source="https://github.com/double-r-squared/GRACE-2" \
      org.opencontainers.image.description="GRACE-2 GeoClaw (Clawpack) shallow-water inundation solver runtime (AWS Batch). Reads a build_spec + topo DEM from s3://...-cache/, authors a setrun.py, runs the headless Clawpack solver, writes fort.q frames + completion manifest to s3://...-runs/<run_id>/." \
      org.opencontainers.image.licenses="BSD-3-Clause"

ENV VENV=/opt/grace2/.venv \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DEBIAN_FRONTEND=noninteractive

# Build + run toolchain. gfortran + make + build-essential are REQUIRED at run
# time (``make .output`` recompiles the per-deck Fortran driver each solve), so
# they stay in the final image — see the design note above. ca-certificates for
# boto3 HTTPS; libexpat1 for rasterio's GDAL XML driver (the job-0240 lesson the
# MODFLOW image hit). git: clawpack's pip build pulls submodules.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        gfortran \
        make \
        git \
        ca-certificates \
        libexpat1 \
    && rm -rf /var/lib/apt/lists/*

# --- Python venv + Clawpack (compiles GeoClaw Fortran at install) ----------
# clawpack pulls numpy + matplotlib (its plotting/IO deps); we add boto3 for the
# S3 envelope and rasterio for the topo<->ASCII handling the entrypoint may do.
# The clawpack install compiles the GeoClaw Riemann solvers via f2py (gfortran),
# so this is the long layer. CLAW is the clawpack source root the Makefile
# machinery references at run time.
RUN python3 -m venv "$VENV" \
    && "$VENV/bin/pip" install --no-cache-dir --upgrade pip setuptools wheel \
    && "$VENV/bin/pip" install --no-cache-dir \
        "numpy>=1.26,<3" \
        "boto3>=1.34,<2" \
        "rasterio>=1.3,<2" \
        "clawpack>=5.9,<6" \
    && "$VENV/bin/python" -c "import clawpack, numpy, rasterio, boto3; from clawpack import geoclaw; print('geoclaw deps import OK - clawpack', clawpack.__version__)"

ENV PATH="$VENV/bin:$PATH"

# CLAW points at the installed clawpack package root so the per-deck ``make
# .output`` resolves $(CLAW)/clawutil/src/Makefile.common + the GeoClaw libs.
# Resolved at build time from the installed package location.
RUN CLAW_DIR="$("$VENV/bin/python" -c 'import os, clawpack; print(os.path.dirname(clawpack.__file__))')" \
    && echo "export CLAW=${CLAW_DIR}" > /etc/profile.d/claw.sh
ENV CLAW=/opt/grace2/.venv/lib/python3.11/site-packages/clawpack \
    FC=gfortran

# COPY the worker package (entrypoint + setrun_builder) onto a stable runtime
# path. Build context is the repo root (mirrors the SFINCS/MODFLOW/SWMM context);
# paths below are relative to repo root.
RUN mkdir -p /opt/grace2/services/workers/geoclaw
COPY services/workers/geoclaw/ /opt/grace2/services/workers/geoclaw/

# WORKDIR controls where GeoClaw writes its scratch _output/ before the
# entrypoint uploads it. /opt/grace2/work is a writable scratch dir; the
# entrypoint clears + re-creates it per invocation.
WORKDIR /opt/grace2

ENV PYTHONPATH=/opt/grace2 \
    PYTHONUNBUFFERED=1 \
    GRACE2_GEOCLAW_SCRATCH=/opt/grace2/work

# Runtime config — overridable from the AWS Batch container env block.
ENV GRACE2_OBJECT_STORE=s3 \
    GRACE2_RUNS_BUCKET="" \
    GRACE2_RUN_ID="" \
    GRACE2_MANIFEST_URI="" \
    AWS_REGION=us-west-2

# Build-time smoke: clawpack/geoclaw import + the entrypoint + the (clawpack-free)
# deck-author module import cleanly, AND the deck author can render a setrun.py
# from a synthetic build_spec (no Fortran run). Catches a path/import mistake at
# build time instead of at Batch exec time.
RUN python3 -c "import clawpack; from clawpack import geoclaw; print('clawpack OK', clawpack.__version__)" \
    && python3 -c "from services.workers.geoclaw.entrypoint import main; print('entrypoint import OK')" \
    && python3 -c "from services.workers.geoclaw.setrun_builder import render_setrun_py, GeoClawBuildSpec; \
print('setrun render OK len=', len(render_setrun_py(GeoClawBuildSpec(scenario='dam_break', bbox=(-85.5,29.5,-85.0,30.0), topo_file='topo.asc'))))"

# ENTRYPOINT runs this image as a one-shot solver job. CMD is empty so AWS Batch
# containerOverrides command feeds straight through as argv; env-var fallbacks
# (GRACE2_RUN_ID, GRACE2_MANIFEST_URI) are parsed by the entrypoint if no CLI
# flags are passed (mirror of the SFINCS/SWMM/MODFLOW pattern).
ENTRYPOINT ["python3", "-m", "services.workers.geoclaw.entrypoint"]
CMD []
