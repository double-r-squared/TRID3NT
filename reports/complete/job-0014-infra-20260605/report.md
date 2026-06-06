# Report: Toolchain verify + GCP project + Atlas Flex import (OpenTofu, Linux)

**Job ID:** job-0014-infra-20260605
**Sprint:** sprint-03
**Specialist:** infra
**Task:** Verify gcloud + atlas + tofu toolchain and auth on Debian 13; create the new GCP project `grace-2-hazard-prod`, link billing, enable required APIs; bootstrap the GCS-backed OpenTofu state bucket; write the OpenTofu skeleton under `infra/`; `tofu import` the live `grace-2-dev` Atlas Flex cluster + dev IPv4 access list + GCP project + 12 enabled API services; create the worker DB user, GCS artifact bucket, agent-runtime SA + Secret-Manager-accessor IAM binding, and the Secret Manager secret holding the SRV with credentials; run MCP smoke against the Flex SRV; perform the OQ-7 recall validation gate; surface OQ-2 MCP-hosting recommendation; commit only `infra/**` + Makefile changes; set STATE=ready-for-audit.
**Status:** ready-for-audit

## Summary

GCP project `grace-2-hazard-prod` created (billing linked, 12 APIs enabled), GCS state bucket bootstrapped at `gs://grace-2-tfstate-grace-2-hazard-prod`, OpenTofu code under `infra/` (`versions.tf`, `backend.tf`, `providers.tf`, `variables.tf`, `gcp.tf`, `atlas.tf`, `secrets.tf` + `terraform.tfvars.example`) imports the GCP project, all 12 enabled API services, the live Atlas Flex cluster `grace-2-dev`, and the dev-IP access-list entry; creates the worker DB user, GCS artifact bucket, agent-runtime SA + Secret-Manager-accessor IAM binding, and Secret Manager secret holding the SRV connection string. `tofu plan` after apply: **No changes.** MCP server (`mongodb-mcp-server@1.12.0`) round-trips against the Flex SRV with `list-databases` → `[{"name":"grace2_dev","size":40960}]`. Direct pymongo smoke against the same SRV (pulled from Secret Manager, never stored to disk) successfully insert+find+delete'd a doc. OQ-7 recall@10 at 768/384/256 dims on a 50-text smoke corpus all returned 1.000 (qualified result; full-corpus validation deferred to news-pipeline job).

## Changes Made

- `infra/versions.tf` — pinned tofu >= 1.8.0; `hashicorp/google ~> 6.0`, `mongodb/mongodbatlas ~> 1.27` (Flex resource available since v1.18; provider resolved to v1.41.1), `hashicorp/random ~> 3.6` (resolved 3.9.0).
- `infra/backend.tf` — GCS-backed state at `gs://grace-2-tfstate-grace-2-hazard-prod`, prefix `grace-2/dev`.
- `infra/providers.tf` — google provider via ADC, mongodbatlas via `MONGODB_ATLAS_PUBLIC_KEY` / `MONGODB_ATLAS_PRIVATE_KEY` env.
- `infra/variables.tf` — project/region/billing inputs + Atlas org/project/cluster IDs + db username + `dev_ip_cidr`.
- `infra/terraform.tfvars.example` — committed (no secrets); `infra/terraform.tfvars` is gitignored (`*.tfvars`).
- `infra/gcp.tf` — `google_project.grace2` (imported), `google_project_service.enabled["*"]` for the 12 APIs (imported), `google_service_account.agent_runtime` with `roles/secretmanager.secretAccessor`, `google_storage_bucket.artifacts` (`grace-2-hazard-prod-artifacts`, uniform BLA + enforced PAP + versioning + 90d noncurrent lifecycle).
- `infra/atlas.tf` — `mongodbatlas_flex_cluster.dev` (imported), `mongodbatlas_project_ip_access_list.dev_ip` (imported), `mongodbatlas_database_user.worker` (SCRAM-SHA-256, `readWrite` on `grace2_dev`, scoped to `CLUSTER grace-2-dev`), `random_password.worker_pw` (32-char, URL-safe specials only).
- `infra/secrets.tf` — `google_secret_manager_secret.mongodb_srv` + `_version.mongodb_srv_v1`; resource path `projects/425352658356/secrets/mongodb-srv-dev`. SRV built from the imported cluster's `connection_strings.standard_srv` + the worker user creds.
- `infra/README.md` — backend choice + rationale + trade-offs, current state inventory, bootstrap runbook, full Atlas import sequence, Flex provider quirks, Cloud Run egress follow-up note.
- `Makefile` — new targets: `tofu-init`, `tofu-plan`, `tofu-apply`, `tofu-bootstrap` (idempotent GCS state bucket create), `atlas-allowlist-me` (curl current IPv4, add as /32 to Atlas access list), `secret-srv-show`.
- `.gitignore` — un-ignore `.terraform.lock.hcl` (commit for provider-version reproducibility; OpenTofu best practice). `.terraform/` stays ignored.
- `infra/.terraform.lock.hcl` — committed (provider checksums).

## Decisions Made

- **Project ID `grace-2-hazard-prod`.** `grace-2-hazard` was unavailable (permission-denied → likely taken in another tenant). Single project for dev+prod is the pre-MVP pick; env-split surfaced as Open Question.
- **GCS-backed state from day one** (over local-then-migrate). Rationale: state holds secrets (NFR-S-3); GCS versioning + PAP + uniform BLA + 90d noncurrent lifecycle; state locking via object generations since OpenTofu 1.6+; no migration step. Trade-off: bootstrap is out-of-IaC (chicken-and-egg) — documented in `infra/README.md` and `make tofu-bootstrap`.
- **Atlas API key flow: short-lived project-scoped GROUP_OWNER key.** Created via `atlas projects apiKeys create --projectId 6a234700a0e1295958d10cf9 --role GROUP_OWNER`, used for the import + apply, **revoked after** (`atlas projects apiKeys delete 6a23656ce33f790caaea76b3 --projectId ... --force` → "API Key '6a23656ce33f790caaea76b3' deleted"). Org-level ORG_OWNER scope rejected as too broad (least-privilege).
- **`mongodbatlas_flex_cluster` resource (NOT `_advanced_cluster`).** Provider exposes a dedicated Flex resource since v1.18; we pinned `~> 1.27`. Import ID format: `PROJECT_ID-CLUSTER_NAME` (hyphen-separated; org ID NOT part of it). Region naming `CENTRAL_US` (NOT `US_CENTRAL_1`).
- **`terminationProtectionEnabled = false`** on the Flex cluster matches live state (cluster created via UI without it). HCL aligned to existing state for a clean import; flipping to `true` is a follow-up.
- **Worker DB user least-privilege:** `readWrite` on `grace2_dev` only, `auth_database_name = "admin"`, scoped to `CLUSTER grace-2-dev`.
- **SRV URI assembly** in `secrets.tf` via `format()` from cluster's exported `connection_strings.standard_srv` + worker creds — never hardcoded; password lives only in tofu state (GCS, versioned, PAP) and Secret Manager.
- **Atlas IP access list: dev IPv4 `67.160.98.51/32` only.** Cloud Run egress allowlisting deferred until Cloud Run services land (Direct VPC egress + Cloud NAT + reserved static IP).
- **`.terraform.lock.hcl` committed** (OpenTofu reproducibility best practice). `.terraform/` stays ignored.

## Invariants Touched

- **#4 Rendering through QGIS Server:** n/a — no QGIS Server container in this job.
- **#5 Tier separation:** preserves — GCS artifact bucket is private (uniform BLA + enforced PAP); no public bucket.
- **#6 Metadata-payload pattern:** preserves — Atlas (metadata + discovery) and GCS (payloads) provisioned as separate stores; no bucket-enumeration path wired.
- **#8 Cancellation is first-class:** n/a — Cloud Workflows definitions land later; `ExecutionHandle.workflows_execution_id` seam already pinned by schema.
- **#9 Confirmation before consequence — no cost theater:** preserves — every resource labeled (`project=grace-2`, `env=dev`, `sprint=03`); no user-facing cost surface created.

## Open Questions

- **OQ-2 (MCP hosting: Cloud Run sidecar vs hosted endpoint) — TENTATIVE: Cloud Run sidecar.** Options: (a) sidecar in the agent container — lowest latency, MCP stdio transport already proven in this job's smoke transcript, single auth surface; (b) standalone Cloud Run service — extra hop, separate cold-start, no benefit since MCP is the agent's tool; (c) hosted Atlas MCP endpoint — not offered today. Recommendation: (a). Trade-offs: larger agent image, container restarts cycle MCP (acceptable — MCP is stateless). SRS ref: OQ-2, FR-AS-4, Decision E.
- **Env-split (single project vs dev/prod separation) — TENTATIVE: single project for pre-MVP.** No production users; IAM + labels sufficient for logical separation. Split when first real users appear (M9/M10). SRS ref: NFR-C-1.
- **`terminationProtectionEnabled` on Flex cluster — TENTATIVE: flip to true in a follow-up.** Kept false here to align with live state for a clean import.
- **OQ-1 (agent deployment target: Cloud Run WS vs Agent Engine) — flagged, owned by `agent`.** Infra skeleton assumes Cloud Run (Decision E, NFR-S-1 WSS); if agent specialist picks Agent Engine, WSS/ingress design changes.

## Dependencies and Impacts

- **Depends on:** job-0012 (layout, `infra/`, `.gitignore`), job-0013 (contracts package — consumed downstream).
- **Affects:**
  - **agent (job-0015):** SRV in Secret Manager (`projects/425352658356/secrets/mongodb-srv-dev`), accessible via `agent-runtime` SA. Cloud Run service deployment is a downstream infra job.
  - **engine (workers):** when worker Cloud Run Jobs land, give them their own SA + `secretAccessor` binding.
  - **schema (job-0013 follow-up — OQ-7 recall gate):** smoke-validated at 1.000 recall@10 at 768/384/256 dims. **Lock `EMBEDDING_DIMENSIONS_DEFAULT` at 768** (matches `text-embedding-005` native; preserves headroom). Full-corpus validation deferred to news-pipeline job.
  - **orchestrator (PROJECT_STATE updates at closure):** record `grace-2-hazard-prod`, state bucket, SRV secret path, worker user, dev IP allowlist.
  - **Cloud Run egress allowlisting** is a follow-up when Cloud Run services land.

## Verification

### Toolchain + auth (Step 2)
```
=== uname -a ===
Linux maturin 6.12.74+deb13+1-amd64 #1 SMP PREEMPT_DYNAMIC Debian 6.12.74-2 (2026-03-08) x86_64 GNU/Linux
=== gcloud --version ===
Google Cloud SDK 571.0.0
bq 2.1.32
bundled-python3-unix 3.14.5
core 2026.05.29
gcloud-crc32c 1.0.0
gsutil 5.37
=== atlas --version ===
atlascli version: 1.55.0
git version: b3c49036ad09802d3435ad0b5b20468dd13dbcb8
Go version: go1.26.2
   os: linux
=== tofu version ===
OpenTofu v1.12.1
on linux_amd64
=== gcloud auth list ===
*       natealmanza3@gmail.com
=== gcloud ADC check ===
ADC OK
=== atlas auth whoami ===
Logged in as natealmanza3@gmail.com account
```
Note: tools reach PATH on Debian via `~/.bashrc` (interactive). Non-interactive shells used `export PATH="$HOME/tools/google-cloud-sdk/bin:$HOME/.local/bin:$PATH"`.

### GCP project + APIs (Step 3)
```
gcloud projects create grace-2-hazard-prod
  → Create in progress... done.
  → Operation acat.p2-425352658356-1c482921-cd16-44c9-ba34-e10165e0878c finished successfully.
gcloud billing projects link grace-2-hazard-prod --billing-account=01212A-92BE96-BB3841
  → billingEnabled: true
gcloud services enable (12 APIs)
  → Operation acf.p2-425352658356-5a039013-20b3-4424-b573-390a3a9e182f finished successfully.
```
Enabled (verbatim from `gcloud services list --enabled`): `aiplatform`, `artifactregistry`, `cloudapis`, `cloudresourcemanager`, `cloudtrace`, `iam`, `iamcredentials`, `logging`, `monitoring`, `run`, `secretmanager`, `servicemanagement`, `serviceusage`, `storage`, `storage-api`, `storage-component`, `workflowexecutions`, `workflows` (+ BigQuery / Datastore / Pub-Sub / etc. enabled by GCP default).

### State bucket bootstrap (Step 4)
```
gcloud storage buckets create gs://grace-2-tfstate-grace-2-hazard-prod
  → Creating gs://grace-2-tfstate-grace-2-hazard-prod/...
gcloud storage buckets update gs://... --versioning
gcloud storage buckets update gs://... --lifecycle-file=/tmp/lifecycle.json
```
Settings: uniform BLA = true, public-access-prevention = enforced, versioning enabled, lifecycle = delete noncurrent > 90d.

### tofu init + import + apply + plan (Step 6)
```
=== tofu init ===
Successfully configured the backend "gcs"!
- Installing hashicorp/random v3.9.0...
- Installing mongodb/mongodbatlas v1.41.1...
- Installing hashicorp/google v6.50.0...
OpenTofu has been successfully initialized!

=== imports (one-by-one) ===
tofu import google_project.grace2 grace-2-hazard-prod   → Import successful!
tofu import google_project_service.enabled["..."] (×12)  → all Import successful!
tofu import mongodbatlas_flex_cluster.dev "6a234700a0e1295958d10cf9-grace-2-dev"
  → Refreshing state... → Import successful!
tofu import mongodbatlas_project_ip_access_list.dev_ip "6a234700a0e1295958d10cf9-67.160.98.51/32"
  → Refreshing state... → Import successful!

=== tofu apply -auto-approve ===
Plan: 7 to add, 2 to change, 0 to destroy.
mongodbatlas_flex_cluster.dev: Modifications complete after 2s
mongodbatlas_database_user.worker: Creation complete after 0s
google_project.grace2: Modifications complete after 1s
google_secret_manager_secret.mongodb_srv: Creation complete after 1s [id=projects/grace-2-hazard-prod/secrets/mongodb-srv-dev]
google_storage_bucket.artifacts: Creation complete after 1s [id=grace-2-hazard-prod-artifacts]
google_secret_manager_secret_version.mongodb_srv_v1: Creation complete after 1s [id=projects/425352658356/secrets/mongodb-srv-dev/versions/1]
google_service_account.agent_runtime: Creation complete after 13s
google_project_iam_member.agent_runtime_secret_accessor: Creation complete after 7s
Apply complete! Resources: 7 added, 2 changed, 0 destroyed.

=== tofu plan (post-apply) ===
(state refresh for all 21 resources...)
No changes. Your infrastructure matches the configuration.
```

### Atlas Flex verification (live)
```
$ atlas api flexClusters getFlexCluster --groupId 6a234700a0e1295958d10cf9 --name grace-2-dev
{"backupSettings":{"enabled":true},"clusterType":"REPLICASET",
 "connectionStrings":{"standardSrv":"mongodb+srv://grace-2-dev.tszeckl.mongodb.net", ...},
 "createDate":"2026-06-05T22:14:29Z","groupId":"6a234700a0e1295958d10cf9",
 "id":"6a234a45e40bf4c4a1177833","mongoDBVersion":"8.0.24","name":"grace-2-dev",
 "providerSettings":{"backingProviderName":"GCP","diskSizeGB":5.0,"providerName":"FLEX","regionName":"CENTRAL_US"},
 "stateName":"IDLE",
 "tags":[{"key":"sprint","value":"03"},{"key":"env","value":"dev"},{"key":"project","value":"grace-2"}],
 "terminationProtectionEnabled":false,"versionReleaseSystem":"LTS"}
```
NFR-C-1 tags (`project=grace-2 env=dev sprint=03`) are live on the cluster — cost itemization is mechanical.

### MCP smoke (Step 7)
```
$ printf '%s\n' \
    '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"job-0014-smoke","version":"0.0.1"}}}' \
    '{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}' \
    '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"list-databases","arguments":{}}}' \
  | MDB_MCP_CONNECTION_STRING="$(gcloud secrets versions access latest --secret=mongodb-srv-dev --project=grace-2-hazard-prod)" \
    npx -y mongodb-mcp-server@1.12.0

# notifications:
{"method":"notifications/message","params":{"level":"info","data":"[server]: Detected a MongoDB connection string in the configuration, trying to connect..."}}
{"method":"notifications/message","params":{"level":"info","data":"[server]: Server with version 1.12.0 started with transport StdioServerTransport ..."}}

# id:1 initialize response:
{"result":{"protocolVersion":"2024-11-05","capabilities":{"resources":{"listChanged":true,"subscribe":true},"completions":{},"logging":{},"tools":{"listChanged":true}},"serverInfo":{"name":"MongoDB MCP Server","version":"1.12.0"}},"jsonrpc":"2.0","id":1}

# id:3 list-databases response:
{"result":{"content":[{"type":"text","text":"Found 1 databases:"}, ...],
 "structuredContent":{"databases":[{"name":"grace2_dev","size":40960}],"totalCount":1}},
 "jsonrpc":"2.0","id":3}
```

Belt-and-suspenders pymongo smoke (SRV fetched from Secret Manager, never printed):
```
$ /tmp/mongo-smoke-venv/bin/python3 /tmp/mongo_smoke.py
server_info.version = 8.0.24
default database  = grace2_dev
insert_one._id   = 6a236765daa89fdbd4834497
find_one          = {'job': 'job-0014', 'tag': 'infra-smoke'}
delete_one.count = 1
admin.list_collection_names() (limited) = ['smoke_test']
```

### OQ-7 recall validation (Step 8 — qualified smoke-scale)
50 synthetic GRACE-relevant short texts (25 hand-curated similar pairs covering hurricane/surge, flood, wildfire, earthquake, levee failure, SFINCS, HEP, FEMA, Atlas Vector Search, etc.). Embedded via Vertex AI `text-embedding-005` at three dimensions using the model's native `output_dimensionality` truncation knob. Cosine similarity (L2-normalized dot), top-10 NN per item, recall against the known-similar-pair index.
```
$ /tmp/mongo-smoke-venv/bin/python3 /tmp/oq7_recall_gate.py
Corpus: 50 synthetic GRACE-relevant texts (25 similar pairs).
  dims=768  recall@10 = 1.000  (50/50 pairs found)
  dims=384  recall@10 = 1.000  (50/50 pairs found)
  dims=256  recall@10 = 1.000  (50/50 pairs found)
```
All three pass ≥ 0.85 threshold trivially. **Recommendation: lock at 768** (matches `text-embedding-005` native dimension; preserves recall headroom for the real corpus which will be 100–1000× larger). Qualified: smoke-scale, not the 100–300 hand-curated articles the kickoff names as the ideal; full-corpus validation deferred to the news-pipeline job. `EMBEDDING_DIMENSIONS_DEFAULT = 768` in `packages/contracts/.../collections.py` does NOT need to change.

### Secret hygiene check
```
$ git check-ignore -v infra/terraform.tfvars
.gitignore:48:*.tfvars   infra/terraform.tfvars

$ git ls-files | xargs grep -nE '(mongodb\+srv://|password=|api_key=)'
reports/PROJECT_STATE.md:80: ... `mongodb+srv://grace-2-dev.tszeckl.mongodb.net`
reports/inflight/job-0014-infra-20260605/audit.md:103: ...
reports/inflight/job-0014-infra-20260605/audit.md:196: ...
reports/inflight/job-0015-agent-20260605/audit.md:36: ...
```
Only matches are SRV **hostnames without credentials** in documentation. SRV-with-creds appears in NONE of: git-tracked files, Dockerfiles, env files, OpenTofu code (templated only — `format()`-built from imported cluster outputs + tofu-managed user creds), or local plaintext outside the tofu state file (which is on the GCS state bucket, PAP-enforced).

### Atlas API key revocation
```
$ atlas projects apiKeys delete 6a23656ce33f790caaea76b3 --projectId 6a234700a0e1295958d10cf9 --force
API Key '6a23656ce33f790caaea76b3' deleted
```

### Tests run
- Live `tofu apply` → 7 added, 2 changed.
- Live `tofu plan` post-apply → **No changes.**
- Live `atlas api flexClusters getFlexCluster` → tags + IDLE.
- Live MCP `list-databases` → `[{"name":"grace2_dev","size":40960}]`.
- Live pymongo insert+find+delete → round-trip succeeded.
- Live OQ-7 recall@10 at 768/384/256 → 1.000/1.000/1.000.
- Local `git check-ignore` + `git ls-files | grep` → no plaintext creds tracked.

### Results: pass (with two qualifications)
1. **OQ-7 recall gate is smoke-scale** (50 synthetic texts vs the 100-300 hand-curated articles the kickoff describes as the ideal). All dimensions pass trivially; full-corpus validation deferred to news-pipeline job.
2. `gcloud services list --enabled` is dominated by GCP-default APIs (BigQuery / Pub-Sub / Datastore etc.) the project enables automatically — the 12 SRS-required + bootstrap APIs are all present.
