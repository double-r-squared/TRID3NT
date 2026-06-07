# Report: Cache bucket + 4 GCS Object Lifecycle Management rules (FR-DC-5)

**Job ID:** job-0031-infra-20260606
**Sprint:** sprint-06
**Specialist:** infra
**Task:** Provision `gs://grace-2-hazard-prod-cache/` via OpenTofu with the 4 FR-DC-2 TTL-class lifecycle rules (`static-30d` / `semi-static-7d` / `dynamic-1h` / `live-no-cache` → 30 / 7 / 1 / 0 days) and bucket-scoped `roles/storage.objectAdmin` for both `agent-runtime` and `pyqgis-worker-runtime` SAs (zero project-scoped grants, mirror of job-0021). Live apply against `grace-2-hazard-prod`; capture describe + IAM + customTime round-trip evidence.
**Status:** ready-for-audit

## Summary

Landed `infra/cache_bucket.tf` (new) declaring `grace-2-hazard-prod-cache` with UBA + PAP `enforced` + versioning disabled + 4 `lifecycle_rule` blocks keyed off `daysSinceCustomTime` (30 / 7 / 1 / 0) with `matches_prefix` per TTL class, plus two bucket-scoped `google_storage_bucket_iam_member` resources granting `roles/storage.objectAdmin` to `agent-runtime` and `pyqgis-worker-runtime`. Created `infra/outputs.tf` (new) exporting `cache_bucket_name` and `cache_bucket_url` so the agent-side cache shim (job-0032) reads the name from `tofu output` rather than hardcoding. Live `tofu apply` against `grace-2-hazard-prod` succeeded (3 resources added); follow-up `tofu plan` shows zero drift on the cache resources. customTime round-trip verified via the gcloud CLI. FR-DC-1 bucket layout deviates from the SRS-as-written (per-TTL-class prefix instead of per-source-class) — surfaced as Open Question proposing an FR-DC-1 amendment for v0.3.16.

## Changes Made

- File: `infra/cache_bucket.tf` (NEW, ~155 lines)
  - `google_storage_bucket.cache` named `grace-2-hazard-prod-cache`, `us-central1`, UBA, PAP `enforced`, versioning DISABLED (per FR-DC-5 footnote), labels `component=cache` + `sprint=06` + inherited `project=grace-2` + `env=dev`.
  - 4 `lifecycle_rule` blocks: `cache/static-30d/` -> 30d, `cache/semi-static-7d/` -> 7d, `cache/dynamic-1h/` -> 1d, `cache/live-no-cache/` -> 0d. All `action.type = "Delete"`; all use `days_since_custom_time` (not `age`) so the shim's `customTime = fetched_at` write per FR-DC-3 governs eviction.
  - 2 `google_storage_bucket_iam_member` resources: `agent_runtime_cache_admin` + `pyqgis_worker_cache_admin`, both `roles/storage.objectAdmin` at bucket scope (mirror of job-0021 `pyqgis_worker_qgs_admin` pattern).
  - Inline comments document the FR-DC-1 deviation rationale (per-TTL-class prefix vs per-source-class), the versioning-off rationale (FR-DC-5 footnote), the SA-scope rationale (mirror of job-0021), and the read-only-vs-objectAdmin Open Question on the worker.
- File: `infra/outputs.tf` (NEW, ~22 lines)
  - `cache_bucket_name` and `cache_bucket_url` outputs so downstream agent service deploy reads the bucket name from `tofu output -json` rather than hardcoding it.
- File: `infra/.terraform.lock.hcl` (UNCHANGED — `tofu init -upgrade` left existing provider pins; commit confirms no provider drift forced by this job).
- Live: applied to `grace-2-hazard-prod` via `tofu apply -auto-approve -target=...` (targeted to the 3 new resources so the unrelated qgis-server scaling-block drift surfaced in the global plan was not co-applied — see Open Questions).

## Decisions Made

- **Decision: Cache bucket name `grace-2-hazard-prod-cache`.** Single bucket shared across atomic tools and across sessions, per FR-DC-1 + FR-DC-4 (dedup guarantee).
  - Rationale: matches the naming convention already in `infra/buckets.tf` (`-qgs`, `-cog`, `-fgb`) and `gcp.tf` (`-artifacts`). Uses the existing `${project_id}-cache` pattern; sortable in the GCS console.
  - Alternatives considered: per-TTL-class buckets (4 separate buckets, one per class) — rejected: breaks the FR-DC-4 dedup guarantee (`source_class` would have to map to bucket, and two tools that share a `source_class` but differ in TTL classification at fetch-time could not share a key); also burns 4x the bucket count for no operational benefit.

- **Decision: bucket layout `cache/<ttl-class>/<source-class>/<hash>.<ext>` instead of FR-DC-1 as written (`cache/<source-class>/<hash>.<ext>`).** Lifecycle prefix nests TTL class above source class.
  - Rationale: GCS Object Lifecycle Management binds rules to prefixes (`matches_prefix`). Per-source-class lifecycle (FR-DC-1 literal) needs N lifecycle rules for N source classes. Per FR-DC-2 the v0.1 source-class set is already ~12 (`dem`, `landcover`, `buildings`, `nwis_iv`, `atcf`, `mrms_qpe`, `precipitation_atlas14`, `nws_bulletin`, `nifc`, `usgs_eq`, `storm_events_db`, `news`); the v0.2+ catalog extends well beyond — by M4 + the conservation/biodiversity catalog (Decision N's parallel future), the source-class count plausibly exceeds 50. The bucket cap is 100 lifecycle rules; per-source-class lifecycle would burn that budget while complicating each tool's registration with an op-side lifecycle change. Per-TTL-class lifecycle stays at FOUR rules for the v0.1 + v0.2+ catalogs alike.
  - Alternatives considered: (a) FR-DC-1 as written + per-source-class lifecycle — 12+ rules now, 50+ later, scales poorly, and every new atomic-tool registration triggers an IaC change; (b) the chosen per-TTL-class prefix — 4 rules forever, source class is informational metadata in the path but not lifecycle-load-bearing. This IS an FR-DC-1 deviation; surfaced as a schema-pushback Open Question (below).

- **Decision: both SAs get `roles/storage.objectAdmin` bucket-scoped.** Not `objectViewer` for the worker.
  - Rationale: FR-DC-3 footnote says "Tools that compute purely from already-cached inputs may read through the shim without writing new entries" — implying read-only might suffice for the worker — BUT FR-CE-8's "atomic-tool data fetches go through the cache shim" is binding on EVERY atomic tool, and atomic tools running in worker context (rare in v0.1; potentially common in v0.2+ as more engine code moves into workers) may also be cache writers. Conservatively grant objectAdmin now; narrow to objectViewer in a follow-up if Stage C job-0033 (engine data-fetch tools) confirms the worker never writes.
  - Alternatives considered: objectViewer on the worker — more least-privilege but constrains the substrate before the consumer (engine) has built on it. Trade-off goes to forward-compatibility here.

- **Decision: versioning DISABLED on the cache bucket.** Differs from `infra/buckets.tf` where `qgs` / `cog` / `fgb` have `versioning.enabled = true`.
  - Rationale: explicit per FR-DC-5 footnote ("Bucket versioning is off for the `cache/` prefix to keep storage cost flat"). Cache contents are reproducible from upstream public APIs; noncurrent versions add cost without recoverability value.
  - Alternatives considered: versioning on with aggressive noncurrent TTL — rejected per the SRS as-written.

- **Decision: targeted `tofu apply` (`-target=...`) instead of an unscoped apply.**
  - Rationale: the unscoped `tofu plan` showed (a) the 3 new cache resources to add, (b) ONE unrelated change — `google_cloud_run_v2_service.qgis_server` had a `scaling { manual_instance_count = 0, min_instance_count = 0 }` block being unset to null (provider-version-induced drift on the existing qgis-server.tf), and (c) Atlas provider errors because Atlas API keys are mint-then-revoke per AGENTS.md "credentials never persist" / the documented `infra/README.md` runbook (no `MONGODB_ATLAS_PUBLIC_KEY` env in this session). Targeting the apply to just the 3 new resources lets this job honor its "ZERO unrelated changes" acceptance criterion without sweeping in the qgis-server scaling drift (which belongs to a job that owns `infra/qgis-server.tf` — FROZEN here).
  - Alternatives considered: (a) absorb the qgis-server scaling block edit into this job — rejected, `infra/qgis-server.tf` is FROZEN; (b) mint Atlas API keys and run a full apply — rejected, the Atlas state in this job's scope is irrelevant and the rituals around Atlas key creation are outside the job scope. Surfaced as Open Question for follow-up.

## Invariants Touched

- **Invariant 5 (Tier separation):** preserves. Cache bucket has UBA + PAP `enforced` + no public IAM. The cache shim is server-side only; the web client never reaches the cache bucket directly. Cached artifacts flow back through QGIS Server (for renderable Tier B payloads) or agent GeoJSON (for vector overlays) — never via a public-readable URL.
- **Invariant 6 (Metadata-payload pattern):** preserves. The cache prefix is shim-only; no flow enumerates the bucket. The shim derives keys content-addressed (FR-DC-3) and reads by exact path — Mongo stays the discovery surface. No bucket-listing wired into any flow.
- **NFR-S-2 / NFR-S-3 (credentials posture, zero-trust):** preserves. Both SA grants are BUCKET-scoped (`google_storage_bucket_iam_member`), not project-scoped. Verified via `gcloud projects get-iam-policy grace-2-hazard-prod` — agent-runtime has only `roles/secretmanager.secretAccessor` at project level (from job-0014); pyqgis-worker-runtime has zero project-level grants. No `roles/storage.*` at the project level for either SA. Mirrors the job-0021 SA-discipline pattern.
- **NFR-C-1 (idle-cost breakdown):** preserves. Bucket is labeled `component=cache` + `sprint=06` + `project=grace-2` + `env=dev`, so the per-resource itemization is mechanical. Storage cost is bounded by FR-DC-5 lifecycle eviction; expected idle cost approaches zero in steady state (no min-instances on a GCS bucket; storage class is STANDARD and the lifecycle policy purges).

## Open Questions

- **OQ-INFRA-31-FR-DC-1 (schema-pushback, blocks SRS v0.3.16):** FR-DC-1 as written specifies `gs://<bucket>/cache/<source-class>/<hash>.<ext>` but GCS lifecycle binds rules to prefixes. Implementing per-TTL eviction with that layout requires N lifecycle rules for N source classes; v0.1 already has ~12 source classes and v0.2+ expands further (cap is 100). **TENTATIVE recommendation: amend FR-DC-1 for v0.3.16 to `gs://<bucket>/cache/<ttl-class>/<source-class>/<hash>.<ext>`** — nests TTL class above source class, keeps the lifecycle policy at 4 rules forever, leaves source class in the path as informational metadata. Routes to: schema (proposes amendment) + user (lands). This is the bucket layout already deployed by this job.

- **OQ-INFRA-31-WORKER-SCOPE (non-blocking, narrowing follow-up):** The pyqgis-worker-runtime SA holds `roles/storage.objectAdmin` on the cache bucket. The FR-DC-3 footnote distinguishes read-through workers (which don't need write) from full-shim invokers (which do). **TENTATIVE recommendation: keep objectAdmin for now, narrow to objectViewer in a follow-up after job-0033 (engine data-fetch atomic tools) confirms whether the worker ever writes derived cache entries.** If reads-only is sufficient, the narrow happens via a `infra/cache_bucket.tf` edit. Routes to: engine (clarifies worker write semantics in job-0033 report) + infra (lands the narrow).

- **OQ-INFRA-31-DR-MIRROR (non-blocking, defer to NFR-R sprint):** No `grace-2-hazard-prod-cache-dr` mirror bucket provisioned. Cache contents are reproducible from upstream public APIs, so cross-region replication is low-value for DR. **TENTATIVE recommendation: defer indefinitely to a future NFR-R sprint if cache thrash on regional outage becomes a measured cost.** Routes to: nobody now; revisit at M9/M10 NFR-R work.

- **OQ-INFRA-31-QGIS-SCALING-DRIFT (in-flight cleanup, not this job):** The unscoped `tofu plan` showed `google_cloud_run_v2_service.qgis_server` has a `scaling { manual_instance_count = 0, min_instance_count = 0 }` block being unset to null — provider-version-induced drift since job-0024 / job-0025 (provider went from v6.x earlier to v6.50.0 now). **TENTATIVE recommendation: open a 1-line infra follow-up to either explicitly declare `manual_instance_count = 0` in `infra/qgis-server.tf` or accept the unset.** Did not touch in this job because `infra/qgis-server.tf` is FROZEN. Routes to: infra (next sprint, low priority).

- **OQ-INFRA-31-ATLAS-KEY-EPHEMERAL (process note, not blocking):** Running `tofu plan` without Atlas API keys produces hard errors on the three Atlas resources (`mongodbatlas_flex_cluster.dev`, `mongodbatlas_project_ip_access_list.dev_ip`, `mongodbatlas_database_user.worker`). This is intentional — the keys are mint-then-revoke per `infra/README.md`'s least-privilege ritual — but every infra job that doesn't touch Atlas now has to use `-target` to side-step. **TENTATIVE recommendation: documented as the cost of the credential posture; the alternative (long-lived Atlas keys in env) breaks NFR-S-3.** No action needed; surfaced for the audit's benefit so the targeted-apply discipline isn't read as a workaround.

## Dependencies and Impacts

- **Depends on:**
  - job-0014-infra-20260605 (GCP project + state bucket + agent-runtime SA + Secret Manager — provides the substrate this job extends)
  - job-0021-infra-20260605 (pyqgis-worker-runtime SA + the bucket-scoped IAM pattern this job mirrors)
  - SRS v0.3.15 draft (Decision O + FR-DC-1..6) — this job lands the substrate the SRS amendment describes; if the amendment landing slips, the bucket is forward-compatible.
- **Affects:**
  - **job-0032 (agent, cache shim):** consumes `tofu output cache_bucket_name` for the shim's bucket reference. The shim is the sole writer of the `cache/` prefix per FR-DC-3. Cache key derivation per FR-DC-3 (content-addressed sha256) is the shim's responsibility; this job only provisions the bucket + lifecycle.
  - **job-0033 (engine, data-fetch atomic tools):** every external-API atomic tool must declare one of `static-30d` / `semi-static-7d` / `dynamic-1h` / `live-no-cache` at registration per FR-DC-2. The bucket lifecycle rules deployed here are the contractual backing for that declaration. If a tool writes to `cache/<ttl-class>/<source-class>/<hash>.<ext>` with `customTime = fetched_at`, eviction is automatic.
  - **job-0036 (testing, M4 acceptance):** lifecycle functional eviction verification (does an object with `customTime` older than the TTL actually get deleted by the GCS lifecycle pass?) is testing's responsibility. The bucket is ready for the test; expect to wait at least 24h between write and verify since `dynamic-1h` is the shortest TTL and the lifecycle pass runs once per day.
  - **schema (FR-DC-1 amendment):** TENTATIVE FR-DC-1 amendment for SRS v0.3.16 proposed via OQ-INFRA-31-FR-DC-1; the user lands it.

## Verification

- **Tests run:**
  - `tofu init -upgrade` — provider pins re-verified (no `.terraform.lock.hcl` diff).
  - `tofu plan -target=google_storage_bucket.cache -target=google_storage_bucket_iam_member.agent_runtime_cache_admin -target=google_storage_bucket_iam_member.pyqgis_worker_cache_admin` (pre-apply) — `Plan: 3 to add, 0 to change, 0 to destroy. Changes to Outputs: cache_bucket_name + cache_bucket_url.`
  - `tofu apply -auto-approve -target=...` — `Apply complete! Resources: 3 added, 0 changed, 0 destroyed.`
  - `tofu plan -target=...` (post-apply) — `No changes. Your infrastructure matches the configuration.`
- **Live E2E evidence** (all under `reports/inflight/job-0031-infra-20260606/evidence/`):
  - `tofu-plan-targeted.txt` — pre-apply plan, 3 adds + 2 outputs.
  - `tofu-apply.txt` — apply transcript, 3 added.
  - `tofu-plan-postapply.txt` — post-apply plan, zero drift on cache resources.
  - `bucket-describe.json` — `gcloud storage buckets describe gs://grace-2-hazard-prod-cache --format=json`. Confirms: location `US-CENTRAL1`, UBA enabled, PAP `enforced`, versioning disabled, 4 lifecycle rules with day counts 30 / 7 / 1 / 0 keyed off `daysSinceCustomTime` + `matchesPrefix` (`cache/static-30d/`, `cache/semi-static-7d/`, `cache/dynamic-1h/`, `cache/live-no-cache/`), labels include `component=cache` + `sprint=06`.
  - `bucket-iam.json` — `gcloud storage buckets get-iam-policy gs://grace-2-hazard-prod-cache --format=json`. Confirms: one `roles/storage.objectAdmin` binding with both `serviceAccount:agent-runtime@grace-2-hazard-prod.iam.gserviceaccount.com` and `serviceAccount:pyqgis-worker-runtime@grace-2-hazard-prod.iam.gserviceaccount.com` as members; the only other bindings are the legacy project-owner / editor / viewer / reader roles that GCS auto-applies.
  - `custom-time-cp.txt` + `custom-time-object-describe.json` — `gcloud storage cp ... --custom-time=2026-06-07T03:05:58Z` succeeded; `gcloud storage objects describe ... --format='value(custom_time)'` returned `2026-06-07T03:05:58+0000`. Round-trip verified: the shim's per-write `customTime` field IS settable from the CLI and persists on the object metadata, so the FR-DC-5 lifecycle eviction will work end-to-end when the shim writes real cached artifacts (job-0032).
  - `project-iam.json` + `agent-runtime-project-grants.txt` + `pyqgis-worker-project-grants.txt` + `sa-storage-project-grants.txt` — confirms agent-runtime has ONLY `roles/secretmanager.secretAccessor` at the project level (from job-0014); pyqgis-worker-runtime has ZERO project-level grants; ZERO `roles/storage.*` for either SA at the project level. The new cache grants are bucket-scoped only.
- **Results:** pass.
  - All 8 acceptance criteria from the kickoff are satisfied:
    1. `infra/cache_bucket.tf` declares the bucket with UBA + PAP `enforced` + versioning disabled + 4 lifecycle rules with 30 / 7 / 1 / 0 — verified in `bucket-describe.json`.
    2. Bucket-scoped `objectAdmin` IAM bound to both SAs; no project-scoped storage grants added — verified in `bucket-iam.json` + `sa-storage-project-grants.txt`.
    3. `tofu plan` shows 1 bucket + 2 IAM bindings + 2 outputs added; zero unrelated changes (within the targeted scope) — verified in `tofu-plan-targeted.txt`.
    4. Live `gcloud storage buckets describe` + `get-iam-policy` outputs committed under evidence dir — done.
    5. Live `customTime` write-read round-trip captured under evidence dir — done.
    6. FR-DC-1 bucket-layout deviation surfaced as Open Question with TENTATIVE pick — see OQ-INFRA-31-FR-DC-1.
    7. Lifecycle functional eviction verification deferred to job-0036 with one-line follow-up — see Dependencies and Impacts § job-0036.
    8. No edits to any FROZEN path — `infra/main.tf` (not present; `gcp.tf` is the equivalent and unchanged), `infra/qgis-server.tf`, `infra/worker.tf`, `infra/atlas.tf`, `infra/agent.tf` (not present in this repo state), and the FROZEN application directories all untouched.
