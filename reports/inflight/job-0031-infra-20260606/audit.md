# Audit: Cache bucket + 4 GCS Object Lifecycle Management rules (FR-DC-5)

**Job ID:** job-0031-infra-20260606, **Sprint:** sprint-06, **Auditor:** Development Orchestrator, **Status:** assigned

## Task Assignment

**Specialist:** infra

**Prerequisites:**
- job-0014-infra-20260605 (`grace-2-hazard-prod` GCP project + OpenTofu state bucket + agent-runtime SA + Secret Manager — provides the OpenTofu substrate + SA + project ID this job extends)
- job-0021-infra-20260605 (PyQGIS worker `pyqgis-worker-runtime` SA pattern; bucket-scoped objectAdmin with zero project grants — this job mirrors that pattern for the new cache bucket and agent-runtime SA)
- v0.3.15 SRS amendment at commit `e435d8a` (Decision O + FR-DC-1 bucket layout `gs://<bucket>/cache/<source-class>/<hash>.<ext>` + FR-DC-2 4 TTL classes + FR-DC-5 lifecycle eviction tied to `customTime`).

**SRS references:**
- **§3.9 FR-DC-1** (`docs/srs/03-functional-requirements.md`) — bucket layout: `gs://<project-bucket>/cache/<source-class>/<hash>.<ext>`. Bucket name target: `grace-2-hazard-prod-cache` (consistent with the existing `grace-2-hazard-prod-artifacts` + `grace-2-tfstate-grace-2-hazard-prod` naming).
- **§3.9 FR-DC-2** — four TTL classes (`static-30d` / `semi-static-7d` / `dynamic-1h` / `live-no-cache`) with day counts 30 / 7 / 1 / 0.
- **§3.9 FR-DC-5** — lifecycle eviction at the bucket level; objects under `cache/<source-class>/` inherit `daysSinceCustomTime > N` deletion. The shim writes `customTime = fetched_at` per FR-DC-3.
- **§3.9 FR-DC-6** — uncacheable enumeration; out of scope for this job (covered by the shim implementation in job-0032).
- **NFR-S-2 / NFR-S-3** — credentials and SA grants. Mirror the job-0021 zero-project-grants discipline.

### Environment
Linux Debian dev host with OpenTofu 1.12.1 at `~/.local/bin/tofu` and `gcloud 571.0.0` authed. Existing OpenTofu state in `gs://grace-2-tfstate-grace-2-hazard-prod`. Atlas Flex cluster `grace-2-dev` already imported (job-0014). The agent-runtime SA `agent-runtime@grace-2-hazard-prod.iam.gserviceaccount.com` exists with `roles/secretmanager.secretAccessor` from job-0014; this job ADDS bucket-scoped `objectAdmin` for the new cache bucket per the job-0021 SA-discipline pattern.

### Scope

1. **OpenTofu module: `infra/cache_bucket.tf`** (NEW). Declares:
   - `google_storage_bucket.cache` named `grace-2-hazard-prod-cache`, location `us-central1` (matching the existing artifact bucket + Cloud Run region), uniform bucket-level access enabled, public access prevention `enforced`, versioning DISABLED (per FR-DC-5 footnote "Bucket versioning is off for the `cache/` prefix to keep storage cost flat"). Project: `grace-2-hazard-prod`.
   - **Four `lifecycle_rule` blocks** mirroring the FR-DC-2 TTL classes:
     - `condition { matches_prefix = ["cache/static-30d/"] days_since_custom_time = 30 } action { type = "Delete" }`
     - `condition { matches_prefix = ["cache/semi-static-7d/"] days_since_custom_time = 7 } action { type = "Delete" }`
     - `condition { matches_prefix = ["cache/dynamic-1h/"] days_since_custom_time = 1 } action { type = "Delete" }`
     - `condition { matches_prefix = ["cache/live-no-cache/"] days_since_custom_time = 0 } action { type = "Delete" }`

     The `<source-class>` prefix per FR-DC-1 SHOULD be nested under the TTL class for lifecycle simplicity: actual bucket layout becomes `gs://grace-2-hazard-prod-cache/cache/<ttl-class>/<source-class>/<hash>.<ext>` rather than `gs://grace-2-hazard-prod-cache/cache/<source-class>/<hash>.<ext>`. **This is a deviation from FR-DC-1 as written.** Surface as an Open Question with TENTATIVE recommendation: either (a) propose an FR-DC-1 amendment for v0.3.16 to nest by TTL class, OR (b) keep FR-DC-1 as written and implement per-source-class lifecycle rules (one rule per registered source — scales poorly past ~10 sources). The bucket lifecycle rules engine supports up to 100 rules so (b) is technically viable; (a) is cleaner. Pick one in the report and surface as schema-pushback for user landing.

2. **IAM**: bind `roles/storage.objectAdmin` on the `grace-2-hazard-prod-cache` bucket to the existing `agent-runtime@grace-2-hazard-prod.iam.gserviceaccount.com` SA — bucket-scoped, NOT project-scoped (mirror the job-0021 `pyqgis-worker-runtime` pattern). Also grant `roles/storage.objectAdmin` to the `pyqgis-worker-runtime@grace-2-hazard-prod.iam.gserviceaccount.com` SA on the SAME bucket — workers may eventually need to write derived cache entries (per FR-DC-3 footnote "Tools that compute purely from already-cached inputs may read through the shim").

3. **`infra/outputs.tf`** (existing — extend): add `cache_bucket_name`, `cache_bucket_url` outputs so the agent service deploy reads the bucket name from `tofu output` rather than hardcoding.

4. **Live verification (mandatory live E2E per AGENTS.md):**
   - `tofu init && tofu plan && tofu apply -auto-approve` against the live `grace-2-hazard-prod` project; capture the plan output as evidence.
   - `gcloud storage buckets describe gs://grace-2-hazard-prod-cache --format=json` — capture; verify location, UBA, PAP, versioning, lifecycle rule count = 4.
   - `gcloud storage buckets get-iam-policy gs://grace-2-hazard-prod-cache --format=json` — capture; verify `objectAdmin` bound to both SAs at the bucket scope and NOT at the project scope.
   - `gcloud storage cp /tmp/marker.txt gs://grace-2-hazard-prod-cache/cache/dynamic-1h/test/marker.txt --custom-time=$(date -u +%Y-%m-%dT%H:%M:%SZ) && sleep 5 && gcloud storage objects describe gs://grace-2-hazard-prod-cache/cache/dynamic-1h/test/marker.txt --format='value(customTime)'` — verify `customTime` is settable from the CLI; this is the shim's per-write field per FR-DC-3.
   - **Do NOT wait 24 hours to verify lifecycle actually evicts.** Capture the rule configuration as evidence; functional eviction verification is a job-0036 (testing M4 acceptance) responsibility against the live bucket after Stage C tools have populated it.

### File ownership (exclusive)

- `infra/cache_bucket.tf` (NEW)
- `infra/outputs.tf` — only the two new cache-bucket outputs
- `reports/inflight/job-0031-infra-20260606/` — kickoff frozen, report + evidence land here

### FROZEN — no edits in this job

- `infra/main.tf`, `infra/qgis-server.tf`, `infra/worker.tf`, `infra/atlas.tf`, `infra/agent.tf` (project/cluster/agent/QGIS/worker infra — not in this job's scope; tower of SA grants stays put)
- `infra/.terraform.lock.hcl` (regenerated by `tofu init`; commit the diff if it changes)
- `infra/.terraform/` (gitignored)
- `services/agent/**`, `services/workers/**`, `packages/contracts/**`, `web/**`, `styles/**`, `docs/SRS_v0.3.md`, `docs/srs/**`, `reports/complete/**`

### Cross-cutting principles in force

- **Invariant 5 (Tier separation):** preserves. The cache bucket is internal to the agent-service ⇄ worker stack; no public read. PAP enforced.
- **NFR-S-2 / S-3 (credentials + zero-trust):** preserves. SA grants are bucket-scoped, not project-scoped; mirror job-0021 SA-discipline pattern.
- **Diagnose before fix** — if `tofu apply` fails (lifecycle rule schema mismatch, IAM propagation race), capture the error before mutating the .tf.
- **Bundle small fixes** — if `infra/outputs.tf` has any drift between declared outputs and currently-deployed substrate discovered while editing, fix the drift in this job.
- **Surface uncertainty as Open Questions** — the FR-DC-1 bucket-layout-vs-lifecycle-prefix tension above MUST surface as an Open Question with a TENTATIVE pick.

### Acceptance criteria (reviewer re-runs)

- [ ] `infra/cache_bucket.tf` declares `grace-2-hazard-prod-cache` with UBA + PAP `enforced` + versioning disabled + 4 lifecycle rules with the day counts 30 / 7 / 1 / 0.
- [ ] Bucket-scoped `objectAdmin` IAM bound to BOTH `agent-runtime` and `pyqgis-worker-runtime` SAs; no project-scoped storage grants added.
- [ ] `tofu plan` shows 1 bucket + 2 IAM bindings + 2 outputs added; zero unrelated changes.
- [ ] Live: `gcloud storage buckets describe` + `get-iam-policy` outputs committed under evidence dir.
- [ ] Live: `customTime` write-read round-trip captured under evidence dir.
- [ ] FR-DC-1 bucket-layout deviation surfaced as Open Question with TENTATIVE pick (recommend (a) FR-DC-1 amendment for v0.3.16 to nest by TTL class — cleaner long-term).
- [ ] Lifecycle functional eviction verification deferred to job-0036 (testing) with a one-line follow-up in the report.
- [ ] `make tofu-plan` and `make tofu-apply` (if present) work clean against the updated module.
- [ ] No edits to any FROZEN path listed above.

Surface contestable choices as Open Questions with TENTATIVE tags — at minimum: bucket-layout FR-DC-1 deviation (above), whether to add a second `grace-2-hazard-prod-cache-dr` mirror bucket for disaster recovery (TENTATIVE: defer to NFR-R sprint), whether the `pyqgis-worker-runtime` SA should get read-only on the cache bucket vs `objectAdmin` (TENTATIVE: objectAdmin per FR-DC-3 footnote — verify with workers as they land in Stage C).
