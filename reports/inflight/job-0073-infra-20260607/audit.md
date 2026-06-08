# Audit: Cloud Run scaling block drift reconciliation (OQ-61 / OQ-67 closure)

**Job ID:** job-0073-infra-20260607, **Sprint:** sprint-10 Stage 1, **Auditor:** Development Orchestrator, **Status:** assigned

## Task Assignment

**Specialist:** infra

**Prerequisites:**
- jobs 0061, 0067, 0069 — each surfaced the same "pre-existing Cloud Run scaling block drift" when running untargeted `tofu plan`; each used `-target=...` to work around it; OQ-61-CLOUD-RUN-SCALING-BLOCK-DRIFT carried forward.

**SRS references:** none beyond NFR-S-2 (already in force).

### Why this job exists

Untargeted `tofu plan` has been showing drift on one or more `google_cloud_run_v2_*` resources' scaling blocks since approximately sprint-7 (job-0040 era). Three subsequent infra jobs (0061, 0067, 0069) worked around it with `-target` flags and noted the drift as carry-forward. With sprint-10's worker rebuild (job-0074) and any future infra change, the drift becomes a hazard — operators can't trust untargeted plans, and the targeted-flag pattern is a fragile habit.

### Scope

1. **Diagnose the drift.** Run untargeted `tofu plan` from `infra/`. Capture the full plan output. Identify which resource(s) and which field(s) drift. Common drift causes for Cloud Run scaling blocks:
   - GCP API auto-fills a default `min_instance_count` or `max_instance_count` that isn't in the Tofu config
   - A manual `gcloud` adjustment was made out-of-band
   - The library version mismatch causes a re-serialization difference

2. **Decide the reconciliation:**
   - **Option A (codify the live state):** add the field to Tofu so plan returns clean. Cleanest if the live state is what we want.
   - **Option B (push Tofu state to GCP):** `tofu apply` to overwrite the live state with what Tofu thinks it should be. Safe only if the live state was an unintended drift.
   - **Option C (terraform import + adjust):** if it's a brand new field that didn't exist when the resource was created, import + reconcile.

Pick based on what the diagnosis reveals. Document the choice in the report.

3. **Apply the reconciliation.** Untargeted `tofu plan` should return "No changes" after.

4. **Smoke test the affected service** to confirm reconciliation didn't break behavior. For Cloud Run services (QGIS Server, pyqgis-worker, sfincs-solver, agent if deployed), this means a simple invocation or a `gcloud run services describe` showing healthy revisions serving traffic.

### File ownership (exclusive)

- `infra/*.tf` files that need the reconciliation edit
- `reports/inflight/job-0073-infra-20260607/`

### FROZEN

- All services/, web/, packages/, docs/, styles/
- The image digests of any Cloud Run service (don't touch those; image pinning is its own ritual)

### Acceptance criteria

- [ ] Untargeted `tofu plan` returns "No changes"
- [ ] Smoke test confirms affected services still serve healthy
- [ ] Single commit explaining the reconciliation choice
- [ ] No FROZEN edits
