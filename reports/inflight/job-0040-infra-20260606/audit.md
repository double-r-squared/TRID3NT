# Audit: SFINCS solver container + Cloud Run Job + Workflows step (M5 substrate)

**Job ID:** job-0040-infra-20260606, **Sprint:** sprint-07, **Auditor:** Development Orchestrator, **Status:** assigned

## Task Assignment

**Specialist:** infra

**Prerequisites:**
- **job-0014-infra-20260605 (APPROVED)**: GCP project `grace-2-hazard-prod` + OpenTofu state + Artifact Registry repo. Substrate to extend.
- **job-0021-infra-20260605 (APPROVED)**: PyQGIS worker container pattern — SA discipline (bucket-scoped IAM; ZERO project grants), Cloud Build + Cloud Run Job lifecycle. **Read this report end-to-end** for the exact `google_storage_bucket_iam_member` + `google_cloud_run_v2_job` pattern; replicate it for SFINCS.
- **job-0031-infra-20260606 (APPROVED)**: cache bucket layout + `customTime` lifecycle pattern; SFINCS reads from cache, writes outputs to `runs/`.

**SRS references** (narrow files only):
- `docs/srs/03-functional-requirements.md` — FR-CE-1 (solver containerization) / FR-CE-2 (Cloud Run Jobs orchestrated by Cloud Workflows) / FR-CE-3 (artifact persistence to GCS) / FR-CE-7 (cancellation conformance)
- `docs/srs/02-system-overview.md` — §2.3 SFINCS v0.1 row (Python shim via HydroMT)
- `docs/srs/04-non-functional-requirements.md` — NFR-P-4 (15 min for ≤200 km² at 30m) — the latency target the deployed substrate must enable
- DO NOT load `docs/SRS_v0.3.md` monolith.

### Environment
SFINCS upstream: Deltares' `deltares/sfincs-cpu` Docker image (or hand-built from source for license / version control). Lives on Docker Hub. For our deployment we either pull-through-Artifact-Registry the upstream image, OR build our own thin layer on top with our entrypoint + ADC wiring. **Recommended: thin layer over `deltares/sfincs-cpu`** — keeps our maintenance surface tiny while pinning version.

### Scope

1. **`services/workers/sfincs/Dockerfile`** (NEW) — base on `deltares/sfincs-cpu:<pinned-version>`; install `google-cloud-storage` Python SDK + minimal entrypoint wrapper that:
   - Reads inputs from GCS (DEM, landcover, forcing) per a JSON manifest at a known GCS URI
   - Runs SFINCS in the container's working dir
   - Writes outputs (flood depth COG, water-level netCDF) back to `gs://grace-2-hazard-prod-runs/<run_id>/` (NEW runs bucket — see step 3)
   - Emits a completion manifest (JSON) to a known GCS URI so the agent's `wait_for_completion` (job-0041) can detect terminal state
2. **`services/workers/sfincs/entrypoint.py`** (NEW) — the entrypoint described above. Keep small (~150 lines). No agent-side logic; this is a worker shim.
3. **`infra/sfincs.tf`** (NEW) — OpenTofu module:
   - **`gs://grace-2-hazard-prod-runs/` bucket** (NEW — separate from cache bucket; runs/ holds solver outputs per FR-CE-3; cache/ holds atomic-tool fetches per FR-DC-1). Same UBA+PAP-enforced posture as the cache bucket. No GCS lifecycle (runs are permanent unless explicitly deleted; the user owns retention policy).
   - **Cloud Build trigger** wired to build `services/workers/sfincs/Dockerfile` and push to `us-central1-docker.pkg.dev/grace-2-hazard-prod/grace-2-images/sfincs-solver:<tag>`. Mirror the qgis-server/worker triggers from earlier jobs.
   - **Cloud Run Job `grace-2-sfincs-solver`** — image pinned by digest (no `:latest`); 4 vCPU / 4 GiB initial; `--max-retries=1` (a failed solver retries waste time); `--task-timeout=1800` (30 min hard timeout; well above NFR-P-4 ≤15 min target). No min instances (matches NFR-C-2 zero idle).
   - **`sfincs-runtime@grace-2-hazard-prod.iam.gserviceaccount.com`** SA — bucket-scoped IAM:
     - `roles/storage.objectViewer` on `grace-2-hazard-prod-cache` (read inputs)
     - `roles/storage.objectAdmin` on `grace-2-hazard-prod-runs` (write outputs)
     - **ZERO** project-scoped storage grants (mirror job-0021 + job-0031)
   - **Cloud Workflows workflow `grace-2-sfincs-orchestrator`** — a 3-step workflow (prepare-manifest → invoke-cloud-run-job → wait-and-collect) the agent submits to via `executions.create`. Mirrors the FR-CE-2 pattern.
   - **`workflow-invoker-sfincs@grace-2-hazard-prod.iam.gserviceaccount.com`** SA — minimum permissions to start the workflow + read outputs.
4. **`infra/outputs.tf`** — add `sfincs_job_name`, `sfincs_workflow_name`, `runs_bucket_name` outputs so the agent service consumes them via `tofu output` rather than hardcoding.
5. **Live verification (mandatory live E2E per AGENTS.md):**
   - `tofu init && tofu plan && tofu apply -auto-approve -target=...` against `grace-2-hazard-prod` (targeted-apply pattern from job-0031 to side-step unrelated drift; honestly document if other drifts surface)
   - Capture plan + apply stdout under `evidence/`
   - `gcloud run jobs describe grace-2-sfincs-solver --format=json` — capture
   - `gcloud workflows describe grace-2-sfincs-orchestrator --format=json` — capture
   - `gcloud storage buckets describe gs://grace-2-hazard-prod-runs --format=json` — verify UBA+PAP+versioning state
   - **Smoke run:** invoke the Cloud Workflows execution with a tiny synthetic SFINCS payload (just to verify the wiring; no real DEM/forcing). Capture the resulting Workflows execution log + Cloud Run Job execution result. The smoke output doesn't need to be a valid flood depth — just proves the chain (Workflows → Job → SFINCS binary errors out gracefully or finishes trivially).

### File ownership (exclusive)

- `services/workers/sfincs/` (NEW directory)
- `infra/sfincs.tf` (NEW)
- `infra/outputs.tf` — only the 3 new outputs
- `Makefile` — add `sfincs-build` / `sfincs-deploy` targets (mirror `worker-build` / `worker-deploy` from job-0021)
- `reports/inflight/job-0040-infra-20260606/` — kickoff frozen

### FROZEN — no edits in this job

- `infra/main.tf`, `infra/qgis-server.tf`, `infra/worker.tf`, `infra/atlas.tf`, `infra/agent.tf`, `infra/cache_bucket.tf` (existing modules — only `sfincs.tf` NEW)
- `services/agent/**`, `packages/contracts/**`, `web/**`, `styles/**`
- `services/workers/` OTHER than the new `sfincs/` subdirectory (do not edit the PyQGIS worker)
- `docs/srs/**`, `docs/SRS_v0.3.md`, `reports/complete/**`
- Stage A concurrent jobs (data_fetch.py + decisions/oq-4)

### Cross-cutting principles in force

- **Invariant 5 (Tier separation):** preserves. SFINCS runs as Cloud Run Job; reads inputs via SA-scoped IAM; writes outputs to runs bucket; no `gs://` ever exposed to client.
- **Invariant 8 (Cancellation is first-class):** preserves. Cancel chain reaches the running solver via Cloud Workflows `executions.cancel` → Cloud Run Job execution cancel. Verify in the smoke run if feasible; deeper cancel testing lands in job-0041 + job-0043.
- **NFR-S-2/3 (credentials posture):** preserves. SA grants are bucket-scoped, not project-scoped (mirror job-0021).
- **NFR-C-2 (zero idle cost):** preserves. Cloud Run Job has no min instances.
- **NFR-P-4:** the deployed substrate must SUPPORT a ≤15-min run; verification of actual timing is job-0043's responsibility.
- **Diagnose before fix:** if `tofu apply` fails, capture the error before mutating .tf.
- **Bundle small fixes:** if `infra/outputs.tf` has drift between declared outputs and live substrate, fix here.

### Acceptance criteria (reviewer re-runs)

- [ ] `services/workers/sfincs/Dockerfile` + `entrypoint.py` exist; `Dockerfile` pins a specific `deltares/sfincs-cpu` version.
- [ ] `infra/sfincs.tf` declares: runs bucket + Cloud Run Job + Workflows workflow + 2 SAs + 4 bucket-scoped IAM bindings (none project-scoped).
- [ ] `tofu plan` shows the expected NEW resources only; zero unrelated changes.
- [ ] Live: `gcloud run jobs describe grace-2-sfincs-solver` succeeds; image pinned by digest.
- [ ] Live: `gcloud workflows describe grace-2-sfincs-orchestrator` succeeds.
- [ ] Live: runs bucket exists with UBA + PAP enforced; bucket-scoped IAM verified.
- [ ] Smoke run succeeded (the Cloud Workflows execution started + the Cloud Run Job executed end-to-end, even if the smoke payload was trivial). Captured under `evidence/`.
- [ ] `gcloud projects get-iam-policy grace-2-hazard-prod` shows NO new `roles/storage.*` for `sfincs-runtime` or `workflow-invoker-sfincs` at the project level. All grants bucket-scoped.
- [ ] No edits to FROZEN paths.

Surface contestable choices as Open Questions with TENTATIVE tags — at minimum: pull-through-Artifact-Registry of `deltares/sfincs-cpu` vs Docker Hub direct (TENTATIVE: thin layer over Docker Hub pin; if rate limits bite, switch to pull-through); runs bucket lifecycle policy (TENTATIVE: none for v0.1 — user owns retention; revisit at M9 polish); workflow timeout (TENTATIVE: 30 min hard); image digest pin discipline (TENTATIVE: bump-on-build mirror of qgis-server pattern).
