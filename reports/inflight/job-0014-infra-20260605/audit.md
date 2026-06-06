# Audit: Toolchain verify + GCP project + Atlas Flex import (OpenTofu, Linux)

**Job ID:** job-0014-infra-20260605
**Sprint:** sprint-03
**Auditor:** Development Orchestrator
**Status:** assigned

## Task Assignment

**Specialist:** infra
**Prerequisites:** job-0012 (layout, `infra/` dir). User decisions 2026-06-05: **create a new GCP project**; **Atlas Flex** (revised from M0 — Flex for pre-MVP, M10 at milestone M10; supersedes the prior M0 choice); **Linux (Debian 13) is both dev and prod substrate**.
**SRS references:** Decision E, NFR-PO-3 (IaC), NFR-S-2/S-3 (service accounts, Secret Manager), NFR-C-1/C-2, FR-AS-4 (MCP), OQ-2 (MCP hosting — surface with recommendation). Note: NFR-C-1's "M10 idle <$100/month" line is known numerically wrong (~$170/mo actual) and is handled by a SEPARATE follow-up amendment-proposal job — this kickoff does NOT require a corrected dollar number, only that resources are labeled so a future itemization can be produced mechanically.

### Environment: Linux dev + prod (project invariant)

Linux is both the development environment (Debian 13 trixie, `Linux maturin 6.12.74+deb13+1-amd64`, x86_64) and the production substrate (Cloud Run Linux containers). This is the user's deliberate standing decision (2026-06-05) and is now a project invariant — record it in `PROJECT_STATE.md`'s decisions log when this kickoff lands. Consequences for this job:

- **No cross-platform branching** in the Makefile or scripts. Targets assume `apt`, GNU coreutils, and `bash`. No `# macOS branch` blocks, no `if [[ "$OSTYPE" == "darwin"* ]]` shims, no Homebrew, no `darwin-*` arch matrices, no parallel install paths.
- **Container builds target `linux/amd64` only.** Drop any `linux/arm64` matrix entry if present. Accept the trade-off (no Apple Silicon dev path) explicitly per the new substrate principle; record it in the decisions log.
- **The QGIS Server container is Linux** as it always would have been.
- **The `grace2` conda env is moot for this job** — not on the machine, not required, not a side-quest. It is reserved for post-M1 PyQGIS worker dev (QGIS 3.40.3 on Linux) when worker code arrives. agents/infra.md's "repurpose grace2 conda env" clause applies then, not now.
- **Flex is the only Atlas tier in code.** No comments reserving space for an M0-tier code path. "Remove don't shim" (per AGENTS.md) — Linux is the only substrate, Flex is the only tier.

### Scope

1. **Toolchain — VERIFY, do not install.** On this Debian 13 box the toolchain is already installed at `~/tools/` and exposed on PATH via `~/.bashrc`:
   - `gcloud 571.0.0` at `~/tools/google-cloud-sdk/bin/gcloud`
   - `atlas 1.55.0` symlinked at `~/.local/bin/atlas` (from `~/tools/mongodb-atlas-cli_1.55.0_linux_x86_64/`)
   - `tofu 1.12.1` symlinked at `~/.local/bin/tofu` (from `~/tools/tofu_1.12.1/`)

   Run `uname -a`, `gcloud --version`, `atlas --version`, `tofu version` and record verbatim output as the very first transcript in the report. **Only if** a version regresses or a binary is missing from PATH does the job install — and the install path is `apt`/release-tarball, never Homebrew. **OpenTofu, not Terraform** (orchestrator decision 2026-06-05): Terraform is BUSL since 2023; OpenTofu is the MPL-2.0 drop-in (`tofu` CLI) and NFR-PO-3 says "Terraform or equivalent". All IaC in `infra/` is written for `tofu`; push back in your report only if a needed provider is OpenTofu-incompatible.

2. **Auth — VERIFY, don't unconditionally block.** Run `gcloud auth list`, `gcloud auth application-default print-access-token` (non-printing — just check exit code), and `atlas auth whoami` at the top of the run. PROJECT_STATE confirms both CLIs are already authed (gcloud: user + ADC at `~/.config/gcloud/application_default_credentials.json`; atlas: `user_account` flow). If all three succeed, log identities verbatim and proceed. **Only** if a session has regressed do you set `STATE = blocked` with the exact runbook:
   - `! gcloud auth login`
   - `! gcloud auth application-default login` (required for the OpenTofu google provider — do NOT skip)
   - `! atlas auth login`

   Preserve the discipline: never script around interactive auth. The orchestrator resumes you after the user authenticates.

3. **GCP project** (post-auth-verify): create a fresh project, link billing, enable APIs. Propose the final project ID in your report (e.g. `grace-2-hazard-prod`, or a `-dev` variant if separating envs early — surface the **env-split decision as an Open Question**: single project for dev+prod is acceptable pre-MVP since Cloud Run runs Linux either way). Reference command sequence (run from the Debian 13 shell, verbatim transcripts in the report):

   ```bash
   gcloud billing accounts list --format='value(name)'
   export BILLING=<paste-from-above>
   export PROJECT_ID=<chosen-id>
   gcloud projects create "$PROJECT_ID" --name='GRACE-2' --set-as-default
   gcloud billing projects link "$PROJECT_ID" --billing-account="$BILLING"
   gcloud config set project "$PROJECT_ID"
   gcloud services enable \
     cloudresourcemanager.googleapis.com \
     serviceusage.googleapis.com \
     iam.googleapis.com \
     iamcredentials.googleapis.com \
     run.googleapis.com \
     workflows.googleapis.com \
     storage.googleapis.com \
     aiplatform.googleapis.com \
     secretmanager.googleapis.com \
     artifactregistry.googleapis.com \
     logging.googleapis.com \
     monitoring.googleapis.com
   ```

   The five SRS-anchored APIs (`run`, `workflows`, `storage`, `aiplatform`, `secretmanager`) are unchanged. The additions are non-negotiable enablers: `cloudresourcemanager` and `serviceusage` (the OpenTofu google provider calls these on nearly every plan; without `cloudresourcemanager` `tofu plan` returns 403 on trivial reads), `iam` + `iamcredentials` (service accounts for Cloud Run / Workflows / future WIF), `artifactregistry` (Cloud Run deploys from an image), `logging` + `monitoring` (default Cloud Run telemetry). Set `STATE = blocked` and STOP if any of: `gcloud billing accounts list` returns empty; `projects create` fails with permission/quota errors; `billing projects link` returns FAILED_PRECONDITION; `services enable cloudresourcemanager` fails (canonical chicken-and-egg — once-only console enablement). These are human-in-the-loop blockers, not retry conditions.

4. **IaC skeleton (OpenTofu) in `infra/`** — provider config, the project resources captured as code, one GCS artifact bucket, one minimally-scoped service account, all labeled for the NFR-C-1 budget breakdown (per-resource labels: `project=grace-2`, `env=dev|prod`, `sprint=03`).

   **OpenTofu state backend — make the choice and document it.** Current kickoff was silent on state location, which is itself debt (IaC is the source of truth, including for the state file). **Recommended path: GCS-backed remote state from day one**, bootstrapped with a one-time manual `gsutil`/`gcloud storage` step (no local-state phase). Rationale: versioned GCS gives free PITR; GCS backend has built-in state locking via object generations since OpenTofu 1.6+ (no DynamoDB analog needed); a future collaborator just needs ADC + the backend block — no migration; tfstate routinely holds connection strings and key fingerprints and the laptop disk is the wrong home; storage cost for sprint-03 state is ~$0. Bootstrap sequence (run AFTER project + billing + APIs, BEFORE `tofu init`; bucket is tracked as a documented operational artifact, not a managed resource — bootstrapping the bucket *with* tofu requires local state and a migration with no payoff):

   ```bash
   export REGION=us-central1
   export STATE_BUCKET=grace-2-tfstate-${PROJECT_ID}
   gcloud storage buckets create gs://${STATE_BUCKET} \
     --project=${PROJECT_ID} --location=${REGION} \
     --uniform-bucket-level-access --public-access-prevention
   gcloud storage buckets update gs://${STATE_BUCKET} --versioning
   cat >/tmp/lifecycle.json <<'EOF'
   {"rule":[{"action":{"type":"Delete"},"condition":{"daysSinceNoncurrentTime":90,"isLive":false}}]}
   EOF
   gcloud storage buckets update gs://${STATE_BUCKET} --lifecycle-file=/tmp/lifecycle.json
   ```

   Then in `infra/backend.tf`:

   ```hcl
   terraform {
     backend "gcs" {
       bucket = "grace-2-tfstate-<project-id>"
       prefix = "grace-2/dev"
     }
   }
   ```

   The local-then-migrate alternative (local `terraform.tfstate`, gitignored, `tofu init -migrate-state` later) remains acceptable if you have a concrete reason to defer the bucket bootstrap — surface trade-offs (collaboration friction vs. chicken-and-egg of the bucket-for-bucket-state) in the report. Either way, **the choice is documented in `infra/README.md`** with the backend block and rationale.

5. **Atlas Flex — import the existing cluster; do not create.** The cluster ALREADY EXISTS and the infra agent's discipline ("a resource that exists but isn't in code is debt") mandates import:
   - Cluster name: `grace-2-dev`
   - Tier: **Flex** on GCP, region `CENTRAL_US` (Atlas alias for GCP `us-central1`)
   - MongoDB 8.0.24, 5 GB, backups enabled, state IDLE
   - Cluster ID: `6a234a45e40bf4c4a1177833`
   - Project ID: `6a234700a0e1295958d10cf9` (project name `grace-2`)
   - Org ID: `6a234700a0e1295958d10c99` (Nate's Org)
   - SRV: `mongodb+srv://grace-2-dev.tszeckl.mongodb.net`

   The provider resource is **`mongodbatlas_flex_cluster`** (NOT `mongodbatlas_cluster` and NOT `mongodbatlas_advanced_cluster` for this job — Flex-only import is cleanest with the dedicated resource). Pin the provider to `~> 1.27` or `~> 2.0` (the resource GA'd in v1.18; v1.27+ is stable). If the installed provider version doesn't expose `mongodbatlas_flex_cluster`, surface it as an OQ and pin a version that does. Reference HCL:

   ```hcl
   # versions.tf
   terraform {
     required_version = ">= 1.8.0"
     required_providers {
       mongodbatlas = {
         source  = "mongodb/mongodbatlas"
         version = "~> 1.27"
       }
     }
   }

   provider "mongodbatlas" {
     # reads MONGODB_ATLAS_PUBLIC_KEY / MONGODB_ATLAS_PRIVATE_KEY from env
   }

   # atlas.tf
   locals {
     atlas_org_id     = "6a234700a0e1295958d10c99"
     atlas_project_id = "6a234700a0e1295958d10cf9"
     flex_name        = "grace-2-dev"
   }

   resource "mongodbatlas_flex_cluster" "dev" {
     project_id = local.atlas_project_id
     name       = local.flex_name

     provider_settings {
       backing_provider_name = "GCP"
       region_name           = "CENTRAL_US"   # Atlas alias for GCP us-central1
     }

     termination_protection_enabled = true
     tags = {
       project = "grace-2"
       env     = "dev"
       sprint  = "03"
     }
   }
   ```

   Import (one-time; do NOT hardcode API keys — use a short-lived programmatic key set as env vars for this session and revoke after, or use the user's existing key fingerprint — record the choice in the report):

   ```bash
   export MONGODB_ATLAS_PUBLIC_KEY=...
   export MONGODB_ATLAS_PRIVATE_KEY=...
   tofu -chdir=infra import mongodbatlas_flex_cluster.dev \
     6a234700a0e1295958d10cf9-grace-2-dev
   tofu -chdir=infra plan   # MUST show 'No changes' before proceeding
   ```

   Import-ID format is `PROJECT_ID-CLUSTER_NAME` (hyphen-separated, **org ID is NOT part of it**). Quirks to record in the report: `provider_settings.backing_provider_name` and `region_name` are CREATE-only (destroy/recreate to change); on `mongodbatlas_flex_cluster` there is no `provider_name = "FLEX"` field (that pattern belongs to `mongodbatlas_advanced_cluster`); region uses Atlas naming `CENTRAL_US`, not `US_CENTRAL_1`.

   **Import all associated Atlas sub-resources too**, not just the cluster — otherwise `tofu plan` will show drift the temptation will be to silence. At minimum: the IP access list entry, the database user(s), backup configuration if it diverges from defaults. If a sub-resource was created in the UI, write the HCL, `tofu import` it, and confirm zero drift.

   **Atlas network access (Flex).** Allowlist the Debian workstation's current public IPv4 as a `/32`, **NOT `0.0.0.0/0`** (reject the latter in code review — Flex has no PrivateLink/PSC/VPC-peering fallback; the SCRAM password + TLS would be the only perimeter). Flow:

   ```bash
   MYIP=$(curl -s https://ifconfig.me)
   atlas accessLists create $MYIP/32 --type ipAddress \
     --projectId 6a234700a0e1295958d10cf9 \
     --comment 'nate-debian-dev'
   ```

   Add a 7-day temporary expiry on the entry so a stale residential IP can't linger. Document a `make atlas-allowlist-me` helper that runs the curl + atlas CLI dance. **Cloud Run egress-IP allowlisting is a follow-up** when actual Cloud Run services land (Direct VPC egress + Cloud NAT with a reserved static IP allowlisted in Atlas) — NOT blocking for this job; record it as a Follow-up Action below.

   **Provision (also via OpenTofu): the dev-IP access entry, a database user with least-privilege role, and write the SRV connection string into Secret Manager** (NFR-S-3). The SRV string never appears in the repo, in container images, or in plaintext locally.

   **MCP smoke against the Flex SRV URI.** Verify the MongoDB MCP server connects (e.g. `npx mongodb-mcp-server` or the documented package against the Flex SRV) and run a real query (list collections, or insert+find a test doc). Record the exact invocation and the transcript. Surface OQ-2 (sidecar vs hosted MCP) with a recommendation.

### File ownership (exclusive)
`infra/**`, root `Makefile` infra targets. NOT `services/`, `web/`, `packages/`.

### Cross-cutting principles in force

- **Live E2E validation required** (AGENTS.md): for this job that means transcripts from `gcloud` against the actually-created project, `tofu plan` against the live substrate with zero drift, `atlas api flexClusters` against the imported cluster, and an MCP round-trip against the Flex SRV URI. Unit-clean HCL that was never applied is not acceptance.
- **Remove don't shim** (AGENTS.md): no macOS branches, no M0 code paths "just in case", no `linux/arm64` matrix entries. Linux is the only substrate; Flex is the only tier in code.
- **Surface uncertainty in reports** (AGENTS.md). Open-question budget for this job: (a) OpenTofu state backend choice (local vs GCS — recommendation above), (b) project env-split (single project for dev+prod vs two projects), (c) OQ-2 MCP hosting recommendation, (d) where applicable, OQ-1 (Cloud Run WS support for the agent). The SRS NFR-C-1 numeric error is NOT an OQ for this job — it's a follow-up amendment-proposal job (see Follow-up Actions).
- **Diagnose before fix**, **no legacy support pre-MVP** (AWS-era / macOS-Homebrew-era / M0-era anything is dead).

### Acceptance criteria (reviewer re-runs from a Debian 13 shell)

- Transcript opens with `uname -a`, `gcloud --version`, `atlas --version`, `tofu version` (verbatim) — binds the evidence to the new substrate.
- `gcloud projects describe <project-id>` succeeds against the project this job created; `gcloud services list --enabled --project=<project-id>` output captured verbatim and includes the full API list above.
- Auth pre-check logged: `gcloud auth list` (identity + ADC), `atlas auth whoami` — recorded even on the happy path.
- `tofu -chdir=infra init` succeeds against the chosen backend (`backend.tf` committed; if GCS, the bucket was bootstrapped per the runbook above and `infra/README.md` documents it).
- `tofu -chdir=infra plan` returns **No changes** against the live GCP project AND the imported Atlas Flex cluster (and its imported sub-resources: IP access list entry, db user, etc.). Import command transcripts included for each imported resource (at minimum `mongodbatlas_flex_cluster.dev`).
- Atlas Flex listing uses the Flex API path: `atlas api flexClusters listFlexClusters --projectId 6a234700a0e1295958d10cf9` (or `atlas api flexClusters getFlexCluster --name grace-2-dev --projectId 6a234700a0e1295958d10cf9`) showing `grace-2-dev` IDLE. **Do NOT use `atlas clusters list`** — Flex clusters don't surface there.
- MCP smoke transcript: a real query (list collections, or insert+find a test doc) against the Flex SRV URI via the MongoDB MCP server, with the exact npm/uvx invocation recorded.
- Repo secret hygiene: `git ls-files | xargs grep -nE '(mongodb\+srv://|password=|api_key=)'` returns nothing. Secret Manager resource path holding the SRV string is documented in the report. If GCS backend: confirm the state bucket has uniform-bucket-level-access + public-access-prevention + versioning; if local backend: verify the SRV string is not in `terraform.tfstate` and the file is gitignored.
- Every user checkpoint that **occurred or was verified clear** is recorded. The auth pre-check logs identities on the happy path; a billing-link block (if it happens) is recorded with what the user ran.
- `infra/README.md` documents: backend choice + rationale, bootstrap steps, Atlas import commands and provider version pin, the `make atlas-allowlist-me` helper, and the Cloud Run egress-allowlist follow-up trigger.
- OQ-2 (MCP hosting: sidecar vs hosted) surfaced with a recommendation.

## Assessment

## Invariant Check

## Dependency Check

## Decisions Validated

## Open Questions Resolved

## Follow-up Actions

- **Open an amendment-proposal job (schema or infra)** for SRS NFR-C-1: the "M10 idle <$100/month" line is numerically wrong (~$170/mo actual). This kickoff suspends the absolute <$100 ceiling pending the amendment but keeps per-resource labeling and scale-to-zero proof in the Definition of Done so a future itemization can be produced mechanically.
- **Open a follow-up job for Cloud Run egress-IP allowlisting** when actual Cloud Run services land: Direct VPC egress + Cloud NAT with a reserved static external IP (`gcloud compute addresses create grace-2-nat-ip --region=us-central1`), allowlist that single `/32` in Atlas. Document the M10 upgrade trigger (PHI/PII in SRS, or sustained connections approaching Flex's 500-connection cap) as the point where PrivateLink/PSC becomes available.
- **Record in PROJECT_STATE.md decisions log** when this kickoff lands: (a) Linux as dev+prod substrate (2026-06-05), (b) `linux/amd64`-only container builds (no Apple Silicon dev path), (c) Atlas tier Flex → M10 at milestone M10 (supersedes M0), (d) the OpenTofu state backend choice this job makes.
- **Conda env recreation deferred:** the `grace2` env is not required for this job. Open a follow-up tied to the first PyQGIS worker code job (post-M1) to recreate it on Linux with QGIS 3.40.3.
- **Programmatic Atlas API key hygiene:** if a short-lived programmatic key was used for `tofu import`, revoke it after the import lands and document the user-account flow as the steady-state path.

## Sign-off

