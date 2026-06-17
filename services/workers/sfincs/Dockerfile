# GRACE-2 SFINCS solver worker (sprint-07 / M5 / FR-CE-1 / job-0040).
#
# Builds the runtime image for the SFINCS Cloud Run Job. Thin layer over the
# upstream Deltares `deltares/sfincs-cpu` image — we add Python + google-cloud-
# storage + a slim entrypoint shim that performs the GCS-IN -> SFINCS-RUN ->
# GCS-OUT envelope every solver invocation needs. We do NOT modify the SFINCS
# binary or its bundled libraries.
#
# Base-image choice (TENTATIVE — surfaced in report Open Questions):
#   We pull deltares/sfincs-cpu directly from Docker Hub at build time. The
#   alternative is an Artifact Registry pull-through cache (eliminates the
#   Docker Hub anonymous-pull rate limit and gives us a sovereign, audited copy
#   of the upstream digest). Direct pull is fine for sprint-07 v0.1 frequency
#   (~1 build/week max); if rate limits bite, the migration is a one-line
#   Dockerfile change (FROM us-central1-docker.pkg.dev/.../sfincs-cpu@...).
#
# Version pin: `sfincs-v2.3.3` (current upstream stable as of 2026-05-12),
# digest-pinned to keep `tofu plan` an honest source of truth for what's
# deployed. Bump-on-upstream-release is a deliberate, IaC-visible step (mirror
# of the qgis-server.tf + worker.tf digest discipline from job-0018 + job-0021).
#
# Decisions worth surfacing here (also in report.md § Decisions Made):
#   - Thin layer over the official sfincs-cpu image vs hand-built from source:
#     thin layer. Deltares ships the upstream image with the right Fortran
#     toolchain, OpenMP runtime, and SFINCS executable already wired; building
#     from source would shift maintenance load onto us with zero correctness
#     benefit. Trade-off: we depend on Deltares' release cadence.
#   - System pip + --break-system-packages vs venv: system pip. Single-purpose
#     container, no host-Python contamination concern, no PATH dance, matches
#     the job-0021 worker pattern.

FROM deltares/sfincs-cpu:sfincs-v2.3.3@sha256:46b5fc9e324d3d9a0d0a7728390e3413bb1ce57ef74c4201a3003d21ef1efb71

LABEL org.opencontainers.image.title="grace-2-sfincs-solver" \
      org.opencontainers.image.source="https://github.com/double-r-squared/GRACE-2" \
      org.opencontainers.image.description="GRACE-2 SFINCS solver runtime (Cloud Run Job). Reads model setup from gs://grace-2-hazard-prod-cache/, runs SFINCS, writes outputs + completion manifest to gs://grace-2-hazard-prod-runs/<run_id>/." \
      org.opencontainers.image.licenses="MIT"

# Install Python 3 + google-cloud-storage SDK. The upstream image is
# Ubuntu 22.04 (Jammy) with /usr/local/bin/sfincs (the SFINCS binary, sourced from
# https://github.com/Deltares/SFINCS). 22.04's pip 22.x predates PEP 668, so
# system pip installs work directly — we do NOT need the
# `--break-system-packages` flag the job-0021 worker uses on the
# Debian/qgis-server base (pip 23+).
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && pip3 install --no-cache-dir \
        "google-cloud-storage>=2.18,<4" \
        "boto3>=1.34,<2" \
    && python3 -c "from google.cloud import storage; import boto3; print('storage SDKs import OK (gcs + boto3)')"

# COPY the entrypoint into a stable runtime path. Build context is the repo
# root (see infra/sfincs/cloudbuild.yaml); paths below are relative to repo
# root.
RUN mkdir -p /opt/grace2/services/workers/sfincs
COPY services/workers/sfincs/ /opt/grace2/services/workers/sfincs/

# WORKDIR controls where SFINCS writes its scratch outputs before the
# entrypoint uploads them. /opt/grace2/work is a writable scratch dir; the
# entrypoint clears it on start and re-creates it for each invocation.
WORKDIR /opt/grace2

ENV PYTHONPATH=/opt/grace2 \
    PYTHONUNBUFFERED=1 \
    GRACE2_SFINCS_BIN=/usr/local/bin/sfincs \
    GRACE2_SFINCS_SCRATCH=/opt/grace2/work

# Runtime config — overridable from the Cloud Run Job env block.
#   GCP_PROJECT          — passed to google-cloud-storage client (also
#                          available via metadata server, kept for symmetry).
#   GRACE2_CACHE_BUCKET  — source bucket (the cache shim populates this with
#                          the per-tool fetches the workflow stages).
#   GRACE2_RUNS_BUCKET   — destination bucket (this image is the only sanctioned
#                          writer of `gs://grace-2-hazard-prod-runs/<run_id>/`).
#   GRACE2_RUN_ID        — the run ID under which outputs land. The Cloud
#                          Workflows orchestrator sets this per execution.
#   GRACE2_MANIFEST_URI  — gs:// URI of the JSON setup manifest the entrypoint
#                          reads (lists input GCS objects + bbox + scenario).
ENV GCP_PROJECT=grace-2-hazard-prod \
    GRACE2_CACHE_BUCKET=grace-2-hazard-prod-cache \
    GRACE2_RUNS_BUCKET=grace-2-hazard-prod-runs \
    GRACE2_RUN_ID="" \
    GRACE2_MANIFEST_URI=""

# Build-time smoke: the SFINCS binary exists where we expect, and the
# entrypoint module imports cleanly. Catches a base-image path shift or a
# Python import mistake at build time instead of at Cloud Run Job exec time.
RUN test -x /usr/local/bin/sfincs \
    && echo "SFINCS binary OK at /usr/local/bin/sfincs" \
    && python3 -c "from services.workers.sfincs.entrypoint import main; print('entrypoint import OK')"

# ENTRYPOINT overrides the upstream sfincs default so this image runs as a
# one-shot solver Job, not an interactive container. CMD is empty so Cloud
# Run Job task args feed straight through as argv to the module. Env-var
# fallbacks (GRACE2_RUN_ID, GRACE2_MANIFEST_URI) are parsed by the
# entrypoint if no CLI flags are passed (mirror of the worker pattern).
ENTRYPOINT ["python3", "-m", "services.workers.sfincs.entrypoint"]
CMD []
