# Audit: SFINCS solver container + Cloud Run Job + Workflows step (M5 substrate)

**Job ID:** job-0040-infra-20260606, **Sprint:** sprint-07, **Auditor:** Development Orchestrator, **Status:** approved

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

## Assessment

**Verdict:** approved.

The SFINCS substrate lands cleanly and end-to-end-verified: thin Dockerfile over `deltares/sfincs-cpu:sfincs-v2.3.3` (240 MB built image, digest-pinned `@sha256:89ce6e27...`); `entrypoint.py` shim handles input download + solver invocation + output upload + structured completion manifest; `infra/sfincs.tf` provisions the runs bucket + Cloud Run Job + Cloud Workflows orchestrator + 2 SAs with proper IAM scoping. **Smoke run proven end-to-end** via Workflow execution `dde07ade-0277-42d7-bfca-3b6cbe4c2b94` → Cloud Run Job execution `grace-2-sfincs-solver-94lpv` → typed error envelope (exit 2 expected — no real model deck). The Workflows → Job → Workflow-completion chain works.

**Live verification highlights:**
- Runs bucket `grace-2-hazard-prod-runs`: UBA ✓ + PAP `enforced` ✓ + **versioning ON** (deliberate choice — protects against accidental overwrite of solver outputs; differs from cache bucket where versioning OFF was correct because cache contents are reproducible).
- SA scoping: `sfincs-runtime` has **ZERO** project-level grants ✓ — verified via `gcloud projects get-iam-policy`. All storage access is bucket-scoped (cache:objectViewer + runs:objectAdmin + qgs:objectViewer).
- `workflow-invoker-sfincs` SA has two non-storage project-level grants: `roles/logging.logWriter` + `roles/run.viewer`. **Honestly disclosed as OQ-INFRA-40-WORKFLOW-INVOKER-SCOPE.** Both are non-storage and required by Cloud Logging (writer needs project-level by API design) + Cloud Run LRO poll path (no resource-scoped binding exists in the v2 API). The zero-storage-grants discipline from job-0021 + job-0031 is preserved; these grants are infrastructure-API requirements, not architectural slips.

**Two false-start builds before success** — Dockerfile iteration through:
1. PEP-668 `--break-system-packages` flag rejected by Ubuntu 22.04's pip 22.x (it's a 23+ flag). Resolved by switching to a venv-based install.
2. Initial entrypoint assumed `/sfincs/sfincs` binary path; actual location is `/usr/local/bin/sfincs`. Resolved by inspecting the upstream image.

These iterations are exactly the "diagnose before fix" discipline working — capture the build error, inspect the substrate, adjust. No silent workarounds.

23 evidence files captured: 2 plan logs + 2 apply logs + Cloud Build trigger + image listing + Workflow describe + Job describe + 3 IAM JSON files + 4 smoke execution artifacts. Comprehensive.

`tofu plan` post-apply: zero drift. Substrate is reconciled.

**Decisions Made (all accepted):**
- Thin layer over `deltares/sfincs-cpu:sfincs-v2.3.3` via Docker Hub direct (not pull-through Artifact Registry) — pragmatic for v0.1; switch to pull-through only if rate limits bite. Surfaced as OQ-INFRA-40-PULL-THROUGH-AR.
- Runs bucket versioning ON, no lifecycle — user owns retention policy. Revisit at sprint-09 NFR-C cost work. Surfaced as OQ-INFRA-40-RUNS-LIFECYCLE.
- 3-field SFINCS manifest schema (input_uri, output_uri, options) — locked as v0.1 contract. Job-0042 (`model_flood_scenario` workflow) may push back when it integrates HydroMT — surfaced as OQ-INFRA-40-SFINCS-MANIFEST-CONTRACT.
- Base image Ubuntu 22.04 with Python 3.10 (EOL 2026-10-04) — informational only; SFINCS is the workload, Python is incidental.

## Invariant Check

- **Invariant 5 (Tier separation):** preserved. Runs bucket internal to agent ⇄ worker stack; PAP enforced.
- **Invariant 8 (Cancellation):** the Workflows execution + Cloud Run Job execution support cancel via the standard APIs; full end-to-end cancel verification is job-0041's responsibility (when `run_solver` + `wait_for_completion` atomic tools land and exercise the cancel chain through the real running solver).
- **NFR-S-2/3 (credentials):** preserved with full honesty. Bucket-scoped storage IAM only; non-storage project grants documented + justified by upstream API requirements.
- **NFR-C-2 (zero idle):** preserved. Cloud Run Job has no min instances.
- **NFR-P-4 (≤15 min for ≤200 km²):** the substrate enables this; actual timing verification is job-0043's M5 acceptance scope.
- **Decision E (Google Cloud throughout):** consistent — single project, single region, OpenTofu-managed.

## Dependency Check

- **job-0021 + job-0031** (SA discipline patterns): mirrored exactly. The IAM shape is identical to the PyQGIS worker + cache bucket patterns.
- **v0.3.15 SRS §3.9 caching architecture**: cache bucket consumed via `cache:objectViewer` (read-only) per FR-DC-1 substrate. Runs bucket is the new sibling for solver outputs per FR-CE-3.
- **job-0038 OQ-4 HydroMT decision**: the Dockerfile does NOT yet bundle `hydromt-sfincs` per the decision — that's job-0042's container responsibility when `build_sfincs_model` lands. Job-0040's image is solver-only.
- **Unblocks job-0041** (run_solver + wait_for_completion atomic tools): the SFINCS Cloud Workflows is reachable via the `executions.create` API; job-0041 wires the submission path.

## Open Questions Resolved

Filed for triage (none blocks closure):

- **OQ-INFRA-40-RUNS-LIFECYCLE** — no lifecycle on runs bucket. Defer to sprint-09 NFR-C cost work. Non-blocking.
- **OQ-INFRA-40-PULL-THROUGH-AR** — Docker Hub direct for v0.1; switch to pull-through Artifact Registry only if rate limits bite. Monitor; non-blocking.
- **OQ-INFRA-40-WORKFLOW-INVOKER-SCOPE** — `workflow-invoker-sfincs` has 2 non-storage project-level grants (logging.logWriter + run.viewer). Required by API design; no resource-scoped alternative exists. Accepted as cost of operating Cloud Workflows + Cloud Run LRO poll. Surface in any future security review.
- **OQ-INFRA-40-SFINCS-MANIFEST-CONTRACT** — 3-field manifest schema locked. Job-0042 may push back when it composes the HydroMT layer; if so, route as schema consumer-pushback to a follow-up infra/0040.5 mini-job.
- **OQ-INFRA-40-IMAGE-BASE-UBUNTU-22-PYTHON-3-10** — Python 3.10 EOL 2026-10-04. Informational; rebuild image with newer base when SFINCS upstream provides one.

## Follow-up Actions

1. **Unblock Stage B (job-0039 — 3 new fetcher tools)** AND **Stage C (job-0041 — run_solver + wait_for_completion atomic tools)** — both can launch now in parallel. Stage B is gated on 0037 + 0038 (both approved); Stage C is gated on 0040 (this job, approved).
2. **GPLv3 documentation per OQ-4 decision** — `infra/THIRD_PARTY_LICENSES.md` must document `hydromt-sfincs` GPLv3 (and the SFINCS solver license) at the point where the HydroMT layer lands in job-0042's container. Route reminder to job-0042.
3. **Cold-start measurement** — capture during job-0043 acceptance per the OQ-4 decision's noted concern.

## Sign-off

**Approved 2026-06-07 by Development Orchestrator.**

All 9 acceptance criteria met with concrete live evidence (23 evidence files, smoke run end-to-end, IAM scoping verified, zero drift post-apply). Two false-start build iterations correctly diagnosed + resolved (PEP-668 + binary path). Five OQs honestly surfaced with proper triage routing. The SFINCS substrate is live and ready to be invoked by the agent-side tools (job-0041) and composed into the `model_flood_scenario` workflow (job-0042).

Sprint-07 Stage A complete (all 3 jobs approved). Stage B + Stage C parallel launch unblocked.
