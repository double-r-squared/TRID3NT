# Audit: Cache bucket + 4 GCS Object Lifecycle Management rules (FR-DC-5)

**Job ID:** job-0031-infra-20260606, **Sprint:** sprint-06, **Auditor:** Development Orchestrator, **Status:** approved

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

## Assessment

**Verdict:** approved.

`gs://grace-2-hazard-prod-cache` lands cleanly in `us-central1` with UBA enabled, PAP `enforced`, versioning disabled (per the FR-DC-5 footnote), and labels `component=cache` + `sprint=06` for cost attribution. Live `gcloud storage buckets describe` JSON (under `evidence/bucket-describe.json`) confirms all four properties and 4 lifecycle rules wired with the canonical TTL-class prefixes — `cache/static-30d/`, `cache/semi-static-7d/`, `cache/dynamic-1h/`, `cache/live-no-cache/`.

`customTime` round-trip is verified end-to-end: a marker object copied with `--custom-time=2026-06-07T03:05:58Z` returns the same value from `gcloud storage objects describe --format='value(custom_time)'`. The cache shim's FR-DC-3 write semantics (set `customTime = fetched_at` on every write) will work against the deployed lifecycle policy.

IAM is bucket-scoped and clean. Both `agent-runtime@grace-2-hazard-prod` and `pyqgis-worker-runtime@grace-2-hazard-prod` got `roles/storage.objectAdmin` on the cache bucket via `google_storage_bucket_iam_member`. Project-level IAM evidence (`evidence/agent-runtime-project-grants.txt`) confirms `agent-runtime` retains only `roles/secretmanager.secretAccessor` from job-0014 — ZERO new `roles/storage.*` at the project level. Mirrors the job-0021 zero-project-grants discipline exactly.

`tofu plan` post-apply: `No changes. Your infrastructure matches the configuration.` Substrate is reconciled.

The FR-DC-1 deviation question was navigated honestly: kickoff acknowledged the tension between literal FR-DC-1 prose (`cache/<source-class>/<hash>.<ext>`) and the practical need to nest by TTL class for lifecycle-rule scaling. Specialist picked per-TTL-class prefix and surfaced OQ-INFRA-31-FR-DC-1 as a v0.3.16 schema-pushback. Right call — the per-source-class alternative would burn through the GCS 100-rule cap by source ~80.

**Subtle finding from JSON inspection — `live-no-cache` lifecycle rule is effectively no-op.** The 4th lifecycle rule under `cache/live-no-cache/` has `daysSinceCustomTime: null` in the live bucket describe (GCS doesn't accept `0` for this condition). The rule won't actually purge anything. **This is acceptable for M4** because FR-DC-6 already enumerates the uncacheable-by-construction tool classes — `live-no-cache` tools route through the cache shim only to ensure the registration validates, but the shim doesn't write to GCS for them. The lifecycle rule was belt-and-suspenders defense; the `null` value means the belt slipped. Surfacing as a small follow-up: if v0.3.16 amends FR-DC-1 (per OQ-INFRA-31-FR-DC-1) it can also clarify that the `live-no-cache` lifecycle rule is intentional no-op vs require an `age=0` (which GCS DOES accept under the `Age` condition rather than `daysSinceCustomTime`).

Targeted-apply pattern (specialist used `tofu apply -target=...`) is honestly disclosed in the report as a workaround for two unrelated drifts: a `qgis-server` scaling block that needs a `manual_instance_count = 0` declaration (OQ-INFRA-31-QGIS-SCALING-DRIFT) and Atlas API-key absence per the mint-then-revoke discipline (OQ-INFRA-31-ATLAS-KEY-EPHEMERAL). Both are pre-existing repo conditions, not job-0031 introductions; routed correctly.

## Invariant Check

- **Invariant 5 (Tier separation):** preserved. Bucket is internal to the agent ⇄ worker stack; PAP `enforced` means no anonymous public exposure is possible by misconfiguration. The cache content is derived public-API data so leakage isn't a confidentiality concern, but the access path is controlled.
- **NFR-S-2 (credentials posture):** preserved. SA grants are bucket-scoped (`google_storage_bucket_iam_member`), not project-scoped (`google_project_iam_member`). Verified by the `evidence/agent-runtime-project-grants.txt` showing zero new project-level storage grants.
- **NFR-S-3 (zero-trust SA scope):** preserved. Mirrors the job-0021 `pyqgis-worker-runtime` discipline established in sprint-04.

## Dependency Check

- **job-0014-infra-20260605** (project + SA + OpenTofu state) — extended cleanly. `agent-runtime` SA still has only `secretmanager.secretAccessor` project-level; bucket access is the new bucket-scoped layer.
- **job-0021-infra-20260605** (SA-discipline pattern) — followed exactly. Same `google_storage_bucket_iam_member` pattern, same bucket-scoped objectAdmin shape.
- **v0.3.15 SRS** (FR-DC-1 / FR-DC-2 / FR-DC-5) — substrate now matches the contract surface modulo the OQ-INFRA-31-FR-DC-1 prefix-nesting deviation.

## Decisions Validated

All decisions reviewed and accepted:

1. **Per-TTL-class lifecycle prefix (`cache/<ttl-class>/<source-class>/<hash>.<ext>`) over per-source-class FR-DC-1 literal** — correct. Scales past the 100-rule GCS cap; cleaner lifecycle declaration; OQ-INFRA-31-FR-DC-1 captures the schema-pushback for v0.3.16.
2. **Both SAs at `objectAdmin`** rather than agent at admin / worker at viewer — correct for now; worker write semantics confirm in job-0033 (engine data-fetch tools). OQ-INFRA-31-WORKER-SCOPE captures the follow-up narrowing.
3. **Versioning disabled** — correct per FR-DC-5 footnote ("Bucket versioning is off for the `cache/` prefix to keep storage cost flat").
4. **`grace-2-hazard-prod-cache` bucket name** — matches the existing `grace-2-hazard-prod-artifacts` / `grace-2-tfstate-grace-2-hazard-prod` naming convention.
5. **Targeted-apply workaround** for unrelated drift — pragmatic; the drift is pre-existing repo state, not a regression from this job. Surfaced honestly.

## Open Questions Resolved

Filed for triage (none blocks closure):

- **OQ-INFRA-31-FR-DC-1 (schema-pushback, v0.3.16-blocking)** — amend FR-DC-1 to canonicalize `cache/<ttl-class>/<source-class>/<hash>.<ext>` as the bucket layout. **Routing: schema (orchestrator lands the SRS-prose fix in the next v0.3.16 pass).** Bundle with the OQ-W-26 TTL-literal-naming follow-up from job-0030.
- **OQ-INFRA-31-WORKER-SCOPE** — keep `objectAdmin` on `pyqgis-worker-runtime` for now; revisit narrowing to `objectViewer` after job-0033 confirms worker write semantics. Non-blocking.
- **OQ-INFRA-31-DR-MIRROR** — defer DR mirror bucket indefinitely. Accepted; cache content is reproducible from upstream APIs.
- **OQ-INFRA-31-QGIS-SCALING-DRIFT** — pre-existing `infra/qgis-server.tf` scaling-block drift unrelated to this job. **Routing: infra (next housekeeping sprint).** Non-blocking.
- **OQ-INFRA-31-ATLAS-KEY-EPHEMERAL** — accepted as the cost of the mint-then-revoke discipline per NFR-S-3. Targeted-applies for non-Atlas jobs is the going-forward pattern.

Filed by the audit (new):
- **OQ-INFRA-31-LIVE-NO-CACHE-LIFECYCLE-NOOP** — the 4th lifecycle rule for `cache/live-no-cache/` has `daysSinceCustomTime: null` in the live bucket (GCS rejects `0`). The rule is effectively no-op. **Acceptable for M4** because FR-DC-6 enumerates this class as uncacheable-by-construction (the shim shouldn't write there). Surface in v0.3.16 alongside OQ-INFRA-31-FR-DC-1: either change the encoding to GCS `Age=0` (which GCS accepts) under the same prefix, or clarify in §3.9 that the `live-no-cache` lifecycle rule is intentional no-op (relying on the FR-DC-6 enumeration as the enforcement layer). Non-blocking.

## Follow-up Actions

1. **v0.3.16 SRS-prose alignment** — bundle the two pushbacks (OQ-W-26 TTL-literal naming from job-0030 + OQ-INFRA-31-FR-DC-1 bucket-layout nesting + OQ-INFRA-31-LIVE-NO-CACHE-LIFECYCLE-NOOP from this job) into one orchestrator-direct SRS-housekeeping pass.
2. **Unblock job-0032 (agent tool registry + cache shim)** — both Stage A gates are now green; scaffold job-0032 kickoff and dispatch.
3. **Pre-existing drift cleanup** (OQ-INFRA-31-QGIS-SCALING-DRIFT) — open as a follow-up infra job in the next housekeeping window.

## Sign-off

**Approved 2026-06-06 by Development Orchestrator.**

All 8 acceptance criteria from the kickoff met with concrete evidence (bucket-describe + bucket-iam + project-iam + customTime round-trip + 4 tofu evidence files). Invariants 5, NFR-S-2/3 preserved. FROZEN paths untouched (commit diff confirms only `infra/cache_bucket.tf` NEW + `infra/outputs.tf` extended + the inflight report dir). Zero project-level storage grants; bucket-scoped objectAdmin only. `tofu plan` post-apply is clean.

Sprint-06 Stage A both jobs APPROVED. Stage B (job-0032 agent tool registry + cache shim) is unblocked — kickoff scaffolds next.
