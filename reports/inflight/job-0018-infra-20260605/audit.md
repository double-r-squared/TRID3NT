# Audit: QGIS Server Cloud Run + GCS .qgs/COG/FGB buckets + Pub/Sub notify topic

**Job ID:** job-0018-infra-20260605
**Sprint:** sprint-04
**Auditor:** Development Orchestrator
**Status:** assigned

## Task Assignment

**Specialist:** infra
**Prerequisites:** job-0014-infra-20260605 (complete) — read the `grace-2-hazard-prod` GCP project facts in its audit + the OpenTofu layout under `infra/` (`backend.tf`, `providers.tf`, `gcp.tf`, `atlas.tf`, `secrets.tf`, `variables.tf`); job-0012-infra-20260605 (complete) — repo layout (`infra/` ownership). M1 substrate is in `PROJECT_STATE.md` § "Live cloud substrate."
**SRS references:** FR-QS-1 (QGIS Server on Cloud Run, `qgis/qgis-server` base, GRASS/SAGA/processing baked in, `qgis_process` CLI exposed); FR-QS-2 (.qgs in GCS, QGIS Server reads via `/vsigs/`); FR-QS-3 (COG raster + FlatGeobuf vector buckets); FR-QS-5 (QML preset library baked into the container — first preset stub lands in job-0019, this job provisions the bake mechanism); FR-MP-1/3/4 (.qgs in GCS canonical, GCS source of truth, independent lifecycle); FR-DT-2/5 (Tier B served via QGIS Server, never client-direct from GCS); NFR-R-4 (QGIS Server stateless and replaceable); NFR-S-2 (service-account-scoped credentials); NFR-S-5 (no public buckets); NFR-C-1 (idle <$100/mo retained); NFR-C-2 (Cloud Run Jobs scale to zero — applies to worker in 0021; QGIS Server scales to zero via `--min-instances=0` for M2 since no latency NFR is gated yet); NFR-PO-3 (IaC — all resources in OpenTofu); Decision B (QGIS Server as rendering backend — first deployment); Decision E (Google Cloud throughout); Invariant 4 (Rendering through QGIS Server); Invariant 5 (Tier separation — bucket scoping); Invariant 6 (Metadata-payload pattern — bucket non-enumerable).

### Environment

Dev + prod substrate is Linux (Debian 13 trixie, `Linux maturin 6.12.74+deb13+1-amd64`, x86_64). All container builds are `linux/amd64`-only — no `linux/arm64` matrix, no Apple Silicon dev path. Consume the live cloud substrate from `PROJECT_STATE.md` § "Live cloud substrate": GCP project `grace-2-hazard-prod` (425352658356), 12 APIs enabled, OpenTofu state in `gs://grace-2-tfstate-grace-2-hazard-prod`, agent-runtime SA + Secret Manager SRV already present. Toolchain already installed at `~/tools/` + `~/.local/bin/` (`gcloud 571.0.0`, `tofu 1.12.1`, `atlas 1.55.0`, `gh` authed). Verify with `gcloud config list project`, `tofu version`, `gcloud auth list` at job start; record verbatim. `python3-venv` is unavailable on Debian 13 by default — use `virtualenv` if you need a Python venv for any helper script (sprint-03 retrospective pattern). `gcloud auth login`/`application-default login` are the user's interactive steps if a session has regressed; surface as `STATE = blocked` only if so.

### Scope

1. **QGIS Server Cloud Run service.** Provision under `infra/qgis-server/`:
   - `Dockerfile` extending `qgis/qgis-server` (image-tag pin surfaced as TENTATIVE Open Question — recommend digest-pin of LTS 3.40 tag matching the `grace2` env QGIS 3.40.3-Bratislava). Bake GRASS + SAGA + processing-algorithm plugins (the base image carries these — verify and document). Expose `qgis_process` CLI on PATH inside the container (future FR-AS-9 Level 1a discovery). Stage a `styles/` directory inside the image and `COPY` from the repo `styles/` directory — the QML preset `engine` authors in job-0019 lands here at image build time (FR-QS-5).
   - `infra/qgis-server/cloudrun.tf` (or extend `gcp.tf` — surface placement decision in report): Cloud Run service `grace-2-qgis-server`, region `us-central1`, public ingress, request-rate autoscaling per FR-QS-1, `--min-instances=0` for M2 (no latency NFR gated yet; first-tile latency NFR-P-3 < 1s p95 lands at M3 when web client consumes tiles), `--cpu=2 --memory=2Gi` baseline. Service account `grace-2-qgis-server` with `roles/storage.objectViewer` scoped to the three buckets below (no project-wide grant). Stateless and replaceable per NFR-R-4 (instances hold no session state; `.qgs` lives in GCS).
   - Makefile targets at repo root: `make qgis-server-build`, `make qgis-server-push`, `make qgis-server-deploy` (each target a verbatim verifiable command sequence).
2. **GCS buckets.** Provision under `infra/buckets.tf` (or extend `gcp.tf` — surface placement decision):
   - `grace-2-hazard-prod-qgs` (canonical `.qgs` storage, FR-MP-1)
   - `grace-2-hazard-prod-cog` (raster outputs, FR-QS-3)
   - `grace-2-hazard-prod-fgb` (vector outputs, FR-QS-3)
   All three: location `us-central1`, uniform BLA, **public access prevention = enforced** (NFR-S-5, Invariant 5), versioning on, lifecycle policy parallel to the existing artifacts bucket (90-day noncurrent), labeled `project=grace-2 env=dev sprint=04 component=qgis-server`. No public IAM bindings. Bind `roles/storage.objectViewer` for the QGIS Server SA scoped to these three buckets; the worker SA (job-0021) gets `roles/storage.objectAdmin` on the `-qgs` bucket only — provision the binding now even though the SA is created in 0021 (use a `for_each` over future SA names or document the binding lands in 0021 — surface as Open Question).
3. **Pub/Sub completion-notify topic.** Provision under `infra/pubsub.tf`:
   - Topic `grace-2-worker-events` (FR-QS-6 step 5 substrate — durable, FR-CE-* compatible).
   - No subscriber in M2 (agent consumer wires in M3/M4). Document the absence in the resource comment.
   - Labeled identically to buckets.
4. **OpenTofu cleanliness.** All resources added to the existing IaC under `infra/`. `tofu plan` after apply must show **No changes**. State stays in the existing GCS backend bucket.
5. **Budget itemization update.** Append to `infra/README.md` the per-resource idle-cost line for the new substrate (QGIS Server at `--min-instances=0` ≈ $0/mo when idle; three new buckets < $1/mo at smoke scale; Pub/Sub topic $0 when empty). Confirm total project idle stays < $100/mo (NFR-C-1).
6. **Open Questions to surface (TENTATIVE-tagged):**
   - `qgis/qgis-server` image tag pin: digest-pin a 3.40 LTR tag (parity with grace2 env) vs `:latest` vs `:final-3_40`. TENTATIVE: digest-pin 3.40 LTR.
   - Worker-SA bucket-binding location: declare in this job's TF for the future SA name, or land it in job-0021. TENTATIVE: declare here (single bucket-IAM source of truth).
   - Whether to provision Cloud Workflows definition stub now (FR-CE-2 pre-positioning) or defer to M5. TENTATIVE: defer — M5 is the first real consumer.

### File ownership (exclusive)

- `infra/qgis-server/Dockerfile`
- `infra/qgis-server/*.tf` (or new section in existing `infra/*.tf` if you choose flat layout — declare choice)
- `infra/buckets.tf` (or addition to `infra/gcp.tf`)
- `infra/pubsub.tf` (or addition to `infra/gcp.tf`)
- `infra/README.md` (additive)
- Root `Makefile` (add `qgis-server-build`, `qgis-server-push`, `qgis-server-deploy` targets; do NOT remove or modify existing targets)
- `infra/variables.tf` (additive — new vars only)

**FROZEN (do NOT edit in this job):** `packages/contracts/**`, `services/agent/**`, `services/workers/**`, `web/**`, `styles/**` (the QML preset content is engine's in 0019), `tests/**`, `docs/SRS_v0.3.md`, `public_hazard_catalog.yaml`. Existing `infra/*.tf` files: additive edits only — do not restructure `backend.tf`/`providers.tf`/`atlas.tf`/`secrets.tf` shape.

### Cross-cutting principles in force
*Bundle small fixes; scan for all instances* — when this job touches a known class of issue (e.g., a missing label on a labeled resource), sweep the whole sprint scope for similar instances and surface in the report.

Cite by name from AGENTS.md § "Cross-cutting principles":
- **Pre-MVP scope — no legacy support.** No M0-tier or pre-pivot AWS branches; Linux/Flex/Cloud Run only.
- **Remove don't shim.** If touching existing `infra/*.tf`, no `# TODO migrate from X` placeholders.
- **Live E2E validation required.** Report must include a verbatim `curl https://<qgis-server-url>/ogc/?SERVICE=WMS&REQUEST=GetCapabilities&MAP=/vsigs/grace-2-hazard-prod-qgs/<placeholder>.qgs` transcript (even if it returns an empty-project Capabilities — the substrate is live), plus `gcloud storage buckets describe ... --format=json` showing `iamConfiguration.publicAccessPrevention: enforced` on all three buckets, plus a `tofu plan` transcript ending in **No changes**.
- **Diagnose before fix.** For ambiguous deploy failures, name the failing layer (Cloud Run vs IAM vs image vs network).
- **Surface uncertainty.** Every contestable choice → Open Question with TENTATIVE tag.
- **Don't edit in-flight kickoffs.** This kickoff is frozen.

### Acceptance criteria (reviewer re-runs)

- `gcloud run services describe grace-2-qgis-server --region=us-central1 --format='value(status.url,status.conditions)'` returns a URL and a `Ready: True` condition.
- `curl -sf "<service-url>/ogc/?SERVICE=WMS&REQUEST=GetCapabilities"` returns HTTP 200 with parseable XML naming the WMS service (no `.qgs` needed yet — empty-project Capabilities is sufficient at this stage; sample `.qgs` arrives in 0019).
- `gcloud storage buckets describe gs://grace-2-hazard-prod-qgs --format='value(iamConfiguration.publicAccessPrevention)'` == `enforced`; same for `-cog` and `-fgb`.
- `gcloud storage buckets get-iam-policy gs://grace-2-hazard-prod-qgs --format=json` shows no `allUsers` or `allAuthenticatedUsers` binding.
- `gcloud pubsub topics describe grace-2-worker-events` returns the topic; `gcloud pubsub topics list-subscriptions grace-2-worker-events` returns empty.
- `tofu plan` from `infra/` returns **No changes. Your infrastructure matches the configuration.**
- `make qgis-server-build && make qgis-server-push` (verbatim transcript) succeeds; image present in Artifact Registry; `docker manifest inspect` shows `linux/amd64` only.
- `infra/README.md` updated with budget itemization line.
- All Open Questions surfaced with TENTATIVE tags and SRS references.

Surface contestable choices as Open Questions with TENTATIVE tags.

## Assessment

## Invariant Check

## Dependency Check

## Decisions Validated

## Open Questions Resolved

## Follow-up Actions

## Sign-off
