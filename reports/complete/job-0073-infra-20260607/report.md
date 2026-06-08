# Report: Cloud Run scaling block drift reconciliation (OQ-61 / OQ-67 closure)

**Job ID:** job-0073-infra-20260607
**Sprint:** sprint-10 Stage 1
**Specialist:** infra
**Task:** Reconcile the pre-existing Cloud Run scaling block drift so untargeted `tofu plan` returns clean.
**Status:** ready-for-audit

## Summary

Diagnosed the long-standing `google_cloud_run_v2_service.qgis_server` scaling drift: the Google provider ~6.x schema exposes two distinct `scaling` blocks (service-level and template-level), and the GCP API auto-fills the service-level block with `manual_instance_count=0` and `min_instance_count=0` even for AUTOMATIC-mode services. Because the Tofu config had no service-level `scaling` block, the provider detected those API-auto-filled values as drift each plan cycle. Reconciled via **Option A** (codify live state): added the service-level `scaling` block to `infra/qgis-server.tf` matching the GCP API defaults. Untargeted plan for all GCP resources is now clean. QGIS Server smoke test passes (HTTP 200 WMS GetCapabilities). Atlas 401 errors are pre-existing machine-specific credential absence, not new drift.

## Changes Made

- **`infra/qgis-server.tf`**
  - Added a service-level `scaling {}` block at the `google_cloud_run_v2_service.qgis_server` resource level (not inside `template {}`) with `min_instance_count = 0` and `manual_instance_count = 0`.
  - Added an explanatory comment block (job-0073) documenting the two-scaling-block schema distinction, why these values are GCP API auto-fills, and why Option A is the correct reconciliation.

## Diagnosis

### Root cause

The `google_cloud_run_v2_service` resource in Google provider 6.50.0 (locked in `.terraform.lock.hcl`) has **two distinct `scaling` blocks** in its schema:

1. **Service-level `scaling {}`** — at the resource top level; attributes: `min_instance_count`, `manual_instance_count`, `scaling_mode`. Controls whole-service scaling mode (AUTOMATIC/MANUAL).
2. **Template-level `scaling {}`** — inside `template {}`; attributes: `min_instance_count`, `max_instance_count`. Controls per-revision instance limits.

The Tofu config had only the template-level block (`scaling { min_instance_count = 0; max_instance_count = 5 }` inside `template {}`). GCP API was auto-filling the service-level block with:
- `manual_instance_count = 0`
- `min_instance_count = 0`

Since `scaling_mode` was not set (defaults to AUTOMATIC), GCP still populated the service-level block with these zero defaults. Tofu had no corresponding service-level block in config, so each `tofu plan` computed: "live state has these values, config says null them out — propose to remove the service-level scaling block."

### Confirmed by `tofu state show`

```
# Service-level scaling (live state — not in Tofu config before this job)
scaling {
    manual_instance_count = 0
    min_instance_count    = 0
}

template {
    ...
    # Template-level scaling (already in Tofu config, no drift here)
    scaling {
        max_instance_count = 5
        min_instance_count = 0
    }
    ...
}
```

## Reconciliation Strategy: Option A (codify live state)

**Choice:** Option A — add the service-level `scaling` block to Tofu config.

**Rationale:**
- The live state values (`min_instance_count=0`, `manual_instance_count=0`) are GCP API defaults for an AUTOMATIC-mode service — they are correct and intentional (NFR-C-2 scale-to-zero preserved).
- Option B (push Tofu state to GCP — null out the fields) would require removing `manual_instance_count` from the API response, which the GCP API will simply re-populate next time, making the drift reappear.
- Option C (import/adjust) is unnecessary — the resource is already imported; the issue is only the service-level `scaling` sub-block not being declared in code.
- The fix is a pure IaC code addition — no GCP resource mutation, no apply step needed, no runtime behavioral change.

**Alternatives considered:**
- Option B (tofu apply to null the values): rejected — GCP API re-fills these immediately; drift would reappear next plan.
- Lifecycle `ignore_changes`: rejected — masks real drift instead of resolving it.
- Moving `max_instance_count` to the service-level block: rejected — `max_instance_count` is only a template-level attribute; service-level schema does not include it.

## Verification

### Baseline plan (before fix)

From `evidence/tofu_plan_baseline.log`:

```
  # google_cloud_run_v2_service.qgis_server will be updated in-place
  ~ resource "google_cloud_run_v2_service" "qgis_server" {
        ...
      - scaling {
          - manual_instance_count = 0 -> null
          - min_instance_count    = 0 -> null
        }
    }

Plan: 0 to add, 1 to change, 0 to destroy.
```

### Targeted plan (after fix — confirms isolated change is clean)

```
$ cd infra && tofu plan -target=google_cloud_run_v2_service.qgis_server -no-color

No changes. Your infrastructure matches the configuration.
```

### Final untargeted plan (GCP resources — clean)

From `evidence/tofu_plan_clean.log`: zero GCP resource changes planned. Atlas 401 errors are pre-existing machine-specific authentication absence (no `atlas auth login` on this machine), documented in `reports/PROJECT_STATE.md` since sprint-7 and in every prior infra job. Not new drift from this job.

### QGIS Server smoke test

```
$ gcloud run services describe grace-2-qgis-server --region=us-central1 \
    --format='value(status.url,status.conditions[0].type,status.conditions[0].status)'
https://grace-2-qgis-server-pwvcfwv55q-uc.a.run.app	Ready	True

$ curl -s -o /dev/null -w "HTTP %{http_code} size=%{size_download}\n" \
  "https://grace-2-qgis-server-pwvcfwv55q-uc.a.run.app/ogc/wms?SERVICE=WMS&REQUEST=GetCapabilities&MAP=/mnt/qgs/grace2-sample.qgs"
HTTP 200 size=8346
```

WMS GetCapabilities: HTTP 200, 8346 bytes. Service healthy.

### pyqgis-worker and sfincs-solver (untouched)

```
$ gcloud run jobs describe grace-2-pyqgis-worker --region=us-central1 \
    --format='value(metadata.name,status.conditions[0].type,status.conditions[0].status)'
grace-2-pyqgis-worker	Ready	True

$ gcloud run jobs describe grace-2-sfincs-solver --region=us-central1 \
    --format='value(metadata.name,status.conditions[0].type,status.conditions[0].status)'
grace-2-sfincs-solver	Ready	True
```

Both Cloud Run Jobs healthy. Image digests unchanged.

## Decisions Made

- **Option A chosen** (codify live state, not push Tofu config to GCP): rationale above. The service-level scaling values are GCP API defaults that match our desired posture; the correct fix is to declare them in Tofu, not remove them from GCP.

- **Service-level `scaling` comment block added**: documents the two-scaling-block distinction so future maintainers understand why both blocks exist and which attributes belong where. Prevents re-introducing the drift.

- **No apply needed for the fix**: the Tofu config change causes `tofu plan` to report "No changes" for GCP because the live state already matches. No `tofu apply` was required — this is the cleanest possible outcome.

- **Atlas 401 errors are out of scope**: the untargeted plan still fails due to Atlas 401s (no Atlas credentials on this machine). This is documented pre-existing machine-specific state, not the drift this job was tasked to fix.

## Invariants Touched

- **NFR-C-2 (scale-to-zero):** preserves — `min_instance_count = 0` codified at both service level and template level matches the original intent and live state.
- **IaC as source of truth (Domain Discipline):** extends — the service-level scaling block is now in code, so the deployed GCP state is fully captured in IaC.
- **No runtime behavior change:** the reconciliation adds a declaration of values already present in live state; GCP does not apply any change.

## Open Questions

- **Atlas 401 untargeted plan errors (pre-existing):** The untargeted `tofu plan` still errors on Atlas resources due to missing `atlas auth login` on this machine. Documented in `PROJECT_STATE.md` § Environment facts since sprint-7. Cloud Run scaling drift (this job's scope) is fully resolved. Recommended: when Atlas credentials are available on this machine, run untargeted plan to confirm it returns "No changes" cleanly. TENTATIVE: the Atlas resources themselves are likely clean; the 401s are purely auth failures.

## Dependencies and Impacts

- Depends on: job-0061, job-0067, job-0069 — each carried forward OQ-61-CLOUD-RUN-SCALING-BLOCK-DRIFT; this job closes it.
- Affects: All future infra jobs — untargeted GCP plans are now usable without the `-target` workaround. Atlas still requires `atlas auth login` before a fully-clean untargeted plan.

## Verification

- Tests run:
  - `tofu plan -no-color` (untargeted, baseline) — 1 GCP change (scaling block)
  - `tofu state show google_cloud_run_v2_service.qgis_server` — confirmed two distinct scaling blocks
  - `tofu providers schema -json` (targeted grep) — confirmed service-level vs template-level schema distinction
  - `tofu plan -target=google_cloud_run_v2_service.qgis_server -no-color` (post-fix) — "No changes"
  - `tofu plan -no-color` (untargeted, post-fix) — 0 GCP changes; Atlas 401s only
  - `gcloud run services describe grace-2-qgis-server` — Ready True
  - `curl WMS GetCapabilities` — HTTP 200, 8346 bytes
  - `gcloud run jobs describe grace-2-pyqgis-worker` — Ready True
  - `gcloud run jobs describe grace-2-sfincs-solver` — Ready True

- Live E2E evidence:
  - `evidence/tofu_plan_baseline.log` — baseline plan (1 to change: scaling drift)
  - `evidence/tofu_plan_clean.log` — post-fix plan (0 GCP changes)
  - QGIS Server WMS GetCapabilities: HTTP 200, 8346 bytes
  - pyqgis-worker: Ready True
  - sfincs-solver: Ready True

- Results: **pass**
