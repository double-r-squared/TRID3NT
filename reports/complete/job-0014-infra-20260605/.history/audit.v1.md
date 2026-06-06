# Audit: Toolchain + GCP project + Atlas M0 bootstrap (Terraform)

**Job ID:** job-0014-infra-20260605
**Sprint:** sprint-03
**Auditor:** Development Orchestrator
**Status:** assigned

## Task Assignment

**Specialist:** infra
**Prerequisites:** job-0012 (layout, `infra/` dir). User decisions 2026-06-05: **create a new GCP project**; **Atlas free tier M0**.
**SRS references:** Decision E, NFR-PO-3 (IaC), NFR-S-2/S-3 (service accounts, Secret Manager), NFR-C-1/C-2, FR-AS-4 (MCP), OQ-2 (MCP hosting — surface with recommendation).

### Scope

1. **Toolchain:** `brew install --cask google-cloud-sdk` (or formula if cask unavailable), `brew install opentofu mongodb-atlas-cli` (record versions). **OpenTofu, not Terraform** (orchestrator decision 2026-06-05): Terraform is BUSL since 2023 and out of homebrew-core; OpenTofu is the MPL-2.0 drop-in (`tofu` CLI) and NFR-PO-3 says "Terraform or equivalent". All IaC in `infra/` is written for `tofu`; push back in your report only if a needed provider is OpenTofu-incompatible.
2. **USER CHECKPOINTS — handle exactly like this:** after installing, check `gcloud auth list` and `atlas auth whoami` (or equivalent). If unauthenticated, set `STATE = blocked` and write in your report the exact commands the user must run in-session (`! gcloud auth login`, `! atlas auth login`) plus what to do after. Do NOT attempt to script around interactive auth. The orchestrator resumes you after the user authenticates.
3. **GCP project** (post-auth): create a fresh project (id like `grace2-hazard-<suffix>`; surface naming), link the user's billing account (if no billing account is linkable from CLI, that's another user checkpoint — block with instructions), enable APIs: `run.googleapis.com`, `workflows.googleapis.com`, `storage.googleapis.com`, `aiplatform.googleapis.com`, `secretmanager.googleapis.com`.
4. **IaC skeleton (OpenTofu)** in `infra/`: provider config, the project resources you created captured as code (import or recreate — your call, surface it), one GCS bucket for artifacts, one service account with minimal roles. Everything labeled for the NFR-C-1 budget breakdown.
5. **Atlas M0:** create cluster via atlas CLI under the user's account, network access for dev, a database user; connection string into Secret Manager (NFR-S-3). Verify the **MongoDB MCP server** connects locally (e.g. `npx mongodb-mcp-server` or the documented package against the M0 URI — record exactly what worked). Surface OQ-2 (sidecar vs hosted MCP) with a recommendation.

### File ownership (exclusive)
`infra/**`, root `Makefile` infra targets. NOT `services/`, `web/`, `packages/`.

### Cross-cutting principles in force
*Live E2E validation required*, *diagnose before fix*, *surface uncertainty*, *no legacy support pre-MVP* (AWS-era anything is dead).

### Acceptance criteria (reviewer re-runs)
- `gcloud projects describe <id>` succeeds; enabled-APIs list in report (verbatim)
- `tofu -chdir=infra plan` runs clean against the real project
- `atlas clusters list` shows the M0; MCP server connection transcript (a real query, e.g. list collections) in report
- No secret value appears in any file in the repo (reviewer greps); Secret Manager path documented
- Every user checkpoint that occurred is recorded with what the user ran

## Assessment

## Invariant Check

## Dependency Check

## Decisions Validated

## Open Questions Resolved

## Follow-up Actions

## Sign-off
