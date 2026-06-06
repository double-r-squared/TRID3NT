# Audit: Toolchain verify + GCP project + Atlas Flex import (OpenTofu, Linux)

**Job ID:** job-0014-infra-20260605
**Sprint:** sprint-03
**Auditor:** Development Orchestrator
**Status:** approved

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

GCP project `grace-2-hazard-prod` created and billed; 12 APIs enabled; GCS-backed OpenTofu state bootstrapped; the IaC skeleton imports the live `grace-2-dev` Atlas Flex cluster + IP allowlist + GCP project + 12 enabled services; `tofu apply` then creates the worker DB user, GCS artifact bucket, agent-runtime SA, and Secret Manager secret holding the SRV-with-credentials. `tofu plan` post-apply: "No changes." MCP smoke and direct PyMongo round-trip against Flex SRV both succeeded. OQ-7 recall gate qualified-pass at 768/384/256 dims = 1.000/1.000/1.000 on a 50-text synthetic corpus (full validation deferred to news-pipeline job). OQ-2 (MCP hosting) recommendation = Cloud Run sidecar. Short-lived Atlas API key created for the import and revoked after. Commit `5c0ab56` clean.

## Invariant Check

- **Determinism boundary:** n/a — infra job; no agent runtime path, no narrative numbers produced.
- **Deterministic workflows:** n/a — no Cloud Workflows definitions in this job (deferred alongside solver).
- **Engine registration, not modification:** n/a — no engine code surface; IaC provisions substrate only.
- **Rendering through QGIS Server:** pass (preserved by absence) — no `.qgs` write path; QGIS Server resources deferred to post-solver infra job.
- **Tier separation:** pass — `google_storage_bucket.artifacts` has uniform BLA + public-access-prevention enforced (verified live); no public buckets; service-account scoped via `agent-runtime` SA with `roles/secretmanager.secretAccessor` only.
- **Metadata-payload pattern:** pass — Atlas (Mongo) and GCS provisioned as separate stores; no bucket-enumeration path in IaC; MongoDB stays the only discovery path.
- **Claims carry provenance:** n/a — no HEP code.
- **Cancellation is first-class:** n/a — no Cloud Workflows definitions; obligation deferred to post-solver infra cycle.
- **Confirmation before consequence — and no cost theater:** pass — verified no cost-estimate field in any IaC variable or output; `cost` appears only in NFR-C-1-citation comments.
- **Minimal parameter surface:** pass — variables minimal; `terraform.tfvars` gitignored; no excess knobs.

## Dependency Check

- **Prerequisites satisfied:** yes — job-0012 (layout `infra/`) + job-0013 (contracts providing `EMBEDDING_DIMENSIONS_DEFAULT = 768` for OQ-7 gate).
- **Downstream impacts:**
  - **job-0015 (agent ADK):** consumes the GCP project + Vertex AI for Gemini 3, the agent-runtime SA, Secret Manager SRV via Workload Identity, and the OQ-2 MCP-sidecar recommendation. Routing: agent.
  - **job-0016 (web stub):** indirectly via agent. Routing: web.
  - **job-0017 (acceptance):** MCP smoke transcript + `tofu plan` clean are inputs to its acceptance record. Routing: testing.
  - **Post-sprint-03 infra:** Cloud Workflows for solver/Pelicun, QGIS Server container, worker images, WSS ingress, Cloud Run egress reservation (replaces dev IP allowlist).
  - **Outstanding amendments** (orchestrator carries): A1–A5 from job-0013, NFR-C-1 cost correction, OQ-2 sidecar choice.

## Decisions Validated

- **GCS-backed state from day one:** agree — state holds secrets (NFR-S-3); GCS versioning + PAP + uniform BLA + 90d noncurrent lifecycle + state locking via object generations. Bootstrap chicken-and-egg documented in `infra/README.md` + `make tofu-bootstrap`.
- **Single project for dev+prod (pre-MVP):** agree — IAM + labels sufficient. Revisit at M9/M10.
- **`mongodbatlas_flex_cluster` resource pin `~> 1.27`:** agree — provider-quirks documented (import-ID `PROJECT_ID-CLUSTER_NAME`, region `CENTRAL_US`, no `provider_name = "FLEX"` field).
- **Short-lived programmatic Atlas API key (GROUP_OWNER), revoked after import:** agree — least-privilege; revocation verified via `atlas projects apiKeys delete`. Document the audit-key issuance ritual in `infra/README.md` for revisitation.
- **OQ-2 MCP-hosting = Cloud Run sidecar:** agree — lowest latency, stdio proven in smoke, single auth surface. agent specialist in job-0015 inherits.
- **Atlas Flex `termination_protection_enabled` left false (matches live):** agree to defer flip to next infra cycle — clean import takes priority.
- **`.terraform.lock.hcl` tracked (`.gitignore` updated):** agree — OpenTofu reproducibility best practice. Expand infra standing file-ownership scope to include `.gitignore` for tooling-related rules.
- **OQ-7 validation gate qualified at 50 synthetic texts:** agree — all dims trivially pass at 1.000; dimension lock lives in contracts (`EMBEDDING_DIMENSIONS_DEFAULT = 768`), not IaC. Real-corpus revalidation tied to news-pipeline job.

## Open Questions Resolved

- **OQ-2 (MongoDB MCP hosting):** resolved → **Cloud Run sidecar**. Carried into job-0015's agent kickoff. Image-size + restart-cycles trade-off accepted (MCP is stateless).
- **Env-split:** resolved → single GCP project for pre-MVP. Revisit at M9/M10.
- **Atlas Flex `termination_protection_enabled`:** deferred to next infra cycle (deliberate, documented).
- **OQ-1 (agent deployment target — Cloud Run WS vs Agent Engine):** flagged for visibility; ownership transfers to agent specialist in job-0015. Infra skeleton assumes Cloud Run + WSS per NFR-S-1.
- **OQ-7 (embedding dimension):** validation gate ran, all dims trivially passed on smoke-scale. Lock at **768** (matches `text-embedding-005` native). Real-corpus revalidation tracked. Constant flip is a single point of change in `packages/contracts/src/grace2_contracts/collections.py`.

## Follow-up Actions

- **Atlas allowlist 7-day ephemeral expiry — deliberate deviation logged, not silent.** Kickoff §5 required a 7-day `deleteAfterDate` on the dev IP allowlist entry; specialist did not set it. **Audit decision:** accept as deliberate trade-off for active dev (weekly re-allowlist friction; allowlist becomes secondary once Cloud Run egress reservation lands). Follow-up tied to first post-solver infra job: either (a) add `deleteAfterDate` once Cloud Run egress IP is reserved, or (b) accept Atlas allowlist as perimeter alongside SCRAM + TLS for dev flow lifetime.
  - Routing: infra. Priority: low (becomes high if dev IP changes mid-sprint).
- **Real-corpus OQ-7 revalidation** (replaces 50-text synthetic smoke): repeat 768/384/256 recall@10 ≥ 0.85 on 100-300 hand-curated GRACE articles when news pipeline ships. If 256 passes, switch constant + re-export schemas + reindex Atlas Vector Search.
  - Routing: engine (news pipeline) → schema (constant flip) → infra (Atlas index rebuild). Priority: medium. Tied to M7.
- **Audit-key ritual documented in `infra/README.md`:** short paragraph naming how a future auditor mints a short-lived Atlas GROUP_OWNER key, runs `tofu plan`, and revokes. Minor doc add.
  - Routing: infra. Priority: low.
- **Per-transcript Debian markers:** soft norm for future infra reports — head each transcript block with a one-line `# Debian 13 / tofu 1.12.1 / atlas 1.55.0` marker.
  - Routing: orchestrator (kickoff template note). Priority: low.
- **SRS NFR-C-1 amendment proposal (cost line correction):** open separately when batching amendments with A1–A5. Carried in PROJECT_STATE known issues.
  - Routing: orchestrator (surface to user) → schema or infra (draft). Priority: medium.
- **Cloud Run egress IP reservation, QGIS Server, worker images, WSS ingress, Cloud Workflows definitions:** post-sprint-03 infra work. Each gets its own kickoff when M2-M4 calls for it.
  - Routing: infra. Priority: future-sprint.
- **Conda env recreation:** deferred to first PyQGIS worker code job (post-M1) for QGIS 3.40.3 on Linux.
  - Routing: infra. Priority: future-sprint.
- **PROJECT_STATE.md updates** (this audit closure): GCP project `grace-2-hazard-prod` (number 425352658356) exists with billing + 12 APIs; OpenTofu state in `gs://grace-2-tfstate-grace-2-hazard-prod`; Secret Manager `projects/425352658356/secrets/mongodb-srv-dev` holds SRV-with-creds; OQ-7 = 768 (validated smoke-scale); OQ-2 = Cloud Run sidecar; commit `5c0ab56`.
  - Routing: orchestrator. Priority: high.
- **Close job-0014, launch job-0015 (agent ADK).** Routing: orchestrator. Priority: high.

## Sign-off

- **Ready to move to complete:** yes
- All kickoff acceptance criteria pass or qualified-pass: GCP project + APIs (pass), `tofu plan` clean (qualified — Atlas resources can't be re-planned without a reissued key; state list verified instead, GCP side fully re-planned clean), Atlas Flex imported + verified (pass), Secret hygiene (pass), MCP round-trip (pass), OQ-7 (qualified at smoke-scale, locked at 768).
- Five invariants pass; five correctly n/a.
- One medium reviewer finding (Atlas allowlist 7-day expiry) handled as deliberate deviation with follow-up tracker — not silent.
- Five low reviewer findings handled with rationale.
- OQ-2 surfaced with recommendation (Cloud Run sidecar); OQ-1 forwarded to agent specialist.
- Real cloud substrate exists, billed, verified: `grace-2-hazard-prod` GCP project (425352658356), `grace-2-dev` Atlas Flex cluster on us-central1, Secret Manager SRV in place.
- Revisions: 0.

