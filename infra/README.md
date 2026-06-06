# infra/ — Infrastructure as code (OpenTofu)

**Owner:** `infra` specialist.

The GCP substrate everything else deploys onto (SRS v0.3 Decision E, NFR-PO-3,
NFR-C-*, NFR-S-*). Declared as OpenTofu (`tofu`) configuration — the MPL-2.0,
drop-in-compatible fork chosen over BUSL Terraform (PROJECT_STATE decision
2026-06-05; NFR-PO-3 permits "or equivalent", and all-OSI tooling matches the
NFR-L posture).

**IaC is the source of truth** — no console-clicked resource that the code does
not capture. Every resource is labeled (`project`, `env`, `sprint`) so the
NFR-C-1 idle-cost breakdown can be produced mechanically.

## Current state (after job-0014)

- **GCP project:** `grace-2-hazard-prod` (env-split deferred — single project
  for dev+prod, pre-MVP).
- **Region:** `us-central1`.
- **Atlas project:** `6a234700a0e1295958d10cf9` ("grace-2") under org
  `6a234700a0e1295958d10c99` (Nate's Org).
- **Atlas cluster:** `grace-2-dev` — Flex on GCP, region `CENTRAL_US`,
  MongoDB 8.0.24, 5 GB disk, backups enabled, IDLE.
- **Atlas access list:** dev IPv4 only (`67.160.98.51/32`). Refresh via the
  `make atlas-allowlist-me` helper when the IP rotates.
- **Atlas DB user:** `grace2-worker` (SCRAM-SHA-256), `readWrite` on
  `grace2_dev`, scoped to the `grace-2-dev` cluster only.
- **SRV connection string** (with credentials): `projects/425352658356/secrets/mongodb-srv-dev`
  (Secret Manager, auto-replication). Single version. Reach via Workload
  Identity once Cloud Run lands.
- **GCS artifact bucket:** `grace-2-hazard-prod-artifacts` (uniform BLA,
  enforced public-access-prevention, versioning, 90d noncurrent cleanup).
- **Service account:** `agent-runtime@grace-2-hazard-prod.iam.gserviceaccount.com`
  with `roles/secretmanager.secretAccessor` only (Cloud Run wiring later).
- **State bucket:** `grace-2-tfstate-grace-2-hazard-prod` (uniform BLA,
  enforced public-access-prevention, versioning, 90d noncurrent cleanup).
  Out-of-IaC by design (chicken-and-egg).

## State backend decision (job-0014)

**Choice: GCS-backed remote state from day one** (no local-then-migrate phase).

Backend block in `backend.tf`:

```hcl
terraform {
  backend "gcs" {
    bucket = "grace-2-tfstate-grace-2-hazard-prod"
    prefix = "grace-2/dev"
  }
}
```

### Rationale

- Versioned GCS gives free PITR.
- GCS backend has object-generation-based state locking since OpenTofu 1.6+
  (no DynamoDB-analog needed).
- `terraform.tfstate` routinely holds connection strings, password
  fingerprints, etc. — the laptop disk is the wrong home (NFR-S-3).
- A future collaborator only needs ADC + this backend block — no
  migration step.

### Trade-offs

- One-time manual bucket bootstrap (the `make tofu-bootstrap` runbook below).
- The state bucket itself is not in `tofu plan` — it would have to be, which
  means accepting the bootstrap cost or accepting a stale state bucket. We
  accept the bootstrap as a documented operational artifact.

## Bootstrap (one-time, by infra specialist or new dev box)

Prerequisites:
- `gcloud`, `atlas`, `tofu` on PATH (verified in PROJECT_STATE "Environment facts").
- `gcloud auth login` and `gcloud auth application-default login` done.
- `atlas auth login` done.

```bash
# 1. GCP project + APIs + state bucket (one-time, see Makefile `tofu-bootstrap`)
make tofu-bootstrap

# 2. Atlas API key for the mongodbatlas provider (short-lived; revoke after)
#    Create a project-scoped key with role GROUP_OWNER on project
#    6a234700a0e1295958d10cf9 via `atlas projects apiKeys create`. Export the
#    pair as MONGODB_ATLAS_PUBLIC_KEY / MONGODB_ATLAS_PRIVATE_KEY.

# 3. Bring up the infra
cd infra
tofu init
tofu apply
```

## Atlas import (job-0014, recorded for future reproduction)

The Atlas cluster, IP access list entry, and project were created out-of-band
via the Atlas UI / `gcloud projects create` before tofu existed in this repo,
so they are imported, not created:

```bash
# 1. Programmatic API key (short-lived, project-scoped — REVOKE after)
atlas projects apiKeys create \
  --projectId 6a234700a0e1295958d10cf9 \
  --desc 'grace-2 OpenTofu (job-0014)' \
  --role GROUP_OWNER
export MONGODB_ATLAS_PUBLIC_KEY=<from above>
export MONGODB_ATLAS_PRIVATE_KEY=<from above>

# 2. Imports
cd infra
tofu init
tofu import google_project.grace2 grace-2-hazard-prod
# Each enabled API service (see gcp.tf `local.enabled_apis`)
for api in cloudresourcemanager.googleapis.com serviceusage.googleapis.com \
           iam.googleapis.com iamcredentials.googleapis.com \
           run.googleapis.com workflows.googleapis.com storage.googleapis.com \
           aiplatform.googleapis.com secretmanager.googleapis.com \
           artifactregistry.googleapis.com logging.googleapis.com \
           monitoring.googleapis.com; do
  tofu import "google_project_service.enabled[\"$api\"]" "grace-2-hazard-prod/$api"
done
# Atlas Flex cluster — import ID is PROJECT_ID-CLUSTER_NAME
tofu import mongodbatlas_flex_cluster.dev "6a234700a0e1295958d10cf9-grace-2-dev"
# Atlas IP access list
tofu import mongodbatlas_project_ip_access_list.dev_ip \
  "6a234700a0e1295958d10cf9-67.160.98.51/32"

# 3. Verify zero drift
tofu plan   # MUST show "No changes."

# 4. Revoke the API key
atlas projects apiKeys delete <KEY_ID> --projectId 6a234700a0e1295958d10cf9 --force
```

## Atlas networking (Flex)

- **Allowlist the dev IPv4 only**, never `0.0.0.0/0`. Flex has no
  PrivateLink/PSC/VPC-peering — the SCRAM password + TLS would be the only
  perimeter against a wide-open allowlist.
- Refresh the dev IP with `make atlas-allowlist-me` (runbook below).
- **Cloud Run egress allowlisting** is a follow-up when actual Cloud Run
  services land: Direct VPC egress + Cloud NAT with a reserved static
  external IP, that single `/32` allowlisted in Atlas.
- **M10 upgrade trigger** (for PrivateLink/PSC availability): PHI/PII in the
  SRS, or sustained connections approaching Flex's 500-connection cap.

## Atlas provider quirks (recorded for the audit)

- Use `mongodbatlas_flex_cluster`, NOT `mongodbatlas_cluster` or
  `mongodbatlas_advanced_cluster`. Flex has its own dedicated resource
  since provider v1.18 (we pin `~> 1.27`).
- Import ID format: `PROJECT_ID-CLUSTER_NAME` (hyphen-separated). Org ID
  is NOT part of the import ID.
- Region naming: `CENTRAL_US` (Atlas alias), NOT `US_CENTRAL_1`.
- `provider_settings.backing_provider_name` and `region_name` are
  CREATE-only — change requires destroy/recreate.
- There is no `provider_name = "FLEX"` field on `mongodbatlas_flex_cluster`
  (that pattern belongs to `mongodbatlas_advanced_cluster`).
- `mongodbatlas_database_user` requires `scopes { name = ..., type = "CLUSTER" }`
  to bind a user to a single cluster (least-privilege).

## What lives here (provisioned incrementally across infra jobs)

- GCP project bootstrap, enabled APIs, service accounts + Workload Identity
- Cloud Run services (agent, QGIS Server) and Cloud Run Jobs (workers, solver) — later
- Cloud Workflows definitions (multi-step runs; the `terminate` cancel path) — later
- GCS buckets + lifecycle (`.qgs`, COG/FlatGeobuf/GeoParquet, cache) — later
- MongoDB Atlas provisioning + the three Vector Search indexes + MCP hosting
- Secret Manager (connection strings; never in code/repo/images)
- WSS/TLS termination, web hosting / CDN, CI plumbing, budget labels — later

## Local PyQGIS dev environment (`grace2` conda env)

**Owner:** `infra` (env spec); `engine` (worker code that runs inside).
**Job of origin:** job-0022-infra-20260605.
**Scope:** LOCAL worker iteration ONLY. Production worker ships as the container
built in job-0021 (`infra/worker/Dockerfile`). This env is the substrate for
editing `services/workers/pyqgis/worker.py` and running it against `/vsigs/` +
Pub/Sub on the Debian 13 dev box before pushing the image.

### One-time install (Miniforge3)

`mamba`/`conda` are not in apt or in this repo's tool set. Install Miniforge3
(conda-forge-only, MIT/BSD posture — Mambaforge is deprecated; Miniforge3 is
the maintained replacement):

```bash
curl -fsSL -o /tmp/Miniforge3-Linux-x86_64.sh \
  "https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh"
bash /tmp/Miniforge3-Linux-x86_64.sh -b -p "$HOME/miniforge3"
# Activate the shell hook (one-time; sources from $HOME/miniforge3/etc/profile.d/conda.sh):
"$HOME/miniforge3/bin/conda" init bash   # or zsh
# Re-open the shell (or `source ~/.bashrc`).
```

### Create the env

```bash
cd /path/to/GRACE-2
mamba env create -f infra/conda/environment.yml
# (or: conda env create -f infra/conda/environment.yml — mamba is faster)
```

To recreate clean-slate:

```bash
mamba env remove -n grace2 && mamba env create -f infra/conda/environment.yml
```

### Per-session activation

```bash
conda activate grace2
python -c "import qgis.core; print(qgis.core.Qgis.QGIS_VERSION)"
# → 3.40.3-Bratislava
python -c "from google.cloud import storage, pubsub_v1; print('ok')"
# → ok
```

### What's in the env

- `python=3.12` (FR-AS-1)
- `qgis=3.40.3` (Bratislava LTR — matches the QGIS Server image pin for M2)
- `gdal` (transitive via QGIS, pinned explicit for /vsigs/ access in scripts)
- `google-cloud-storage`, `google-cloud-pubsub` (worker GCS + Pub/Sub paths)
- `pytest` (so local unit tests run inside the env without `pip install`)
- `pip` (escape hatch for any pure-python helper not on conda-forge)

### What's NOT in the env (dead-dep strip)

Per AGENTS.md "Remove don't shim" + `agents/infra.md` "Repurpose the grace2
conda env; strip dead dependencies", the env spec deliberately omits the
following v0.2-era dependencies:

- `boto3`, `aws-cli`, `s3fs` — SRS v0.3 Decision E is GCP-only; no AWS SDKs.
- `strands` — former agent-provider abstraction; SRS v0.3 FR-AS-1 pins Google
  ADK + Gemini 3 directly.
- `ollama`, `llama-cpp-python` — local-LLM stack from v0.2; not in SRS v0.3.
- `litellm`, `anthropic-bedrock` — provider-abstraction packages; Gemini via
  Vertex AI directly.

These are removed — not commented out, not behind a feature flag. A future PR
that re-adds any of them is a regression.

### Docker-is-authoritative-runtime decision

This conda env is **not** the production worker runtime. The production
worker ships as `infra/worker/Dockerfile` (job-0021), built `linux/amd64`-only
and deployed as a Cloud Run Job. The conda env exists so an engine can edit
`services/workers/pyqgis/worker.py` and iterate against `/vsigs/` + Pub/Sub
without rebuilding the container every time. When the worker code lands, the
acceptance gate is the Cloud Run Job execution log (job-0023), not a local
`python` run. The conda env is convenience, not contract.

## M2 substrate idle-cost itemization

Per-resource idle delta added by job-0018 (sprint-04 M2 substrate), itemized
for the NFR-C-1 budget ceiling:

- **QGIS Server Cloud Run service** (`grace-2-qgis-server`, `min-instances=0`,
  request-rate autoscaling) — ~$0/mo idle (scale-to-zero; first revision
  charge is only on request).
- **Three GCS buckets** (`-qgs`, `-cog`, `-fgb`; uniform BLA + PAP enforced +
  90d noncurrent lifecycle) — <$1/mo at M2 smoke scale (each holds a handful
  of MB until job-0019/0020 populate them; us-central1 standard storage at
  $0.02/GB-mo).
- **Pub/Sub topic** `grace-2-worker-events` — $0/mo at zero published volume
  (no subscriber wired until M3/M4; topics themselves carry no idle charge).
- **Artifact Registry repo** `grace-2-containers` — $0/mo idle until images
  stored at meaningful scale (one ~1 GB QGIS Server image ≈ $0.10/mo at
  $0.10/GB-mo; negligible).
- **Total M2 substrate idle delta:** <$1/mo.
- **Project total idle** stays <$100/mo NFR-C-1 ceiling — dominated by the
  Atlas Flex line (carried from job-0014); this M2 delta is negligible
  against it.
