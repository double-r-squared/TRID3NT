# job-0250 — infra — Firebase / Identity Platform production auth provisioning (report)

**Specialist:** infra (Opus). **Sprint:** 13.5 Stage 1. **STATE:** IN_REVIEW.
**Date:** 2026-06-11.

## Summary

Production auth substrate for `grace-2-hazard-prod` is fully expressed in
OpenTofu and ready for the user's `tofu apply`. `tofu validate` is green; `tofu
plan` renders all four auth resources cleanly. Every production mutation and
console-only step is captured in `reports/inflight/sprint-13-5-USER_UNBLOCK.md`
(items 0250-A … 0250-G). Nothing was applied (production mutation = user's hand).

## What the Tofu provisions (provisioned-on-apply)

A new child module `infra/firebase/` (the manifest's owned directory), wired into
the flat root module via `infra/firebase.tf`. Uses the GA `hashicorp/google`
provider the repo already pins (`~> 6.0`, locked 6.50.0) — no `google-beta`
introduced (verified via `tofu providers schema -json`; only
`google_firebase_project`/`_web_app`/`_hosting_site` need beta, and those are
console/later-job concerns — evidence `provider_schema_verification.txt`).

| Resource | What it does | Decision |
|---|---|---|
| `google_identity_platform_config.auth` | email/password ON (`password_required=true`); anonymous OFF; self-service signup+deletion ON; authorized_domains = localhost + `<project>.web.app` + `.firebaseapp.com` | #3, #6 |
| `google_identity_platform_default_supported_idp_config.google` | Google sign-in IdP (`google.com`), count-guarded — provisions once OAuth client creds land in tfvars | #4 |
| `google_firestore_database.auth` | Native Firestore `(default)` in `nam5`, PITR on, delete-protected, `deletion_policy=ABANDON` | SRS §F.1 |
| `google_firebaserules_ruleset.auth` + `_release.auth` | deny-all default + per-UID isolation (`/users/{uid}/**` owner-only; `/cases/{caseId}` owned-by-`user_id`), released to `cloud.firestore` | manifest scope |

Root wiring adds four auth APIs to `local.enabled_apis` (`identitytoolkit`,
`firebase`, `firestore`, `firebaserules`) — plan confirms four
`google_project_service.enabled[...]` resources queued.

## Files

- `infra/firebase/main.tf`, `variables.tf`, `outputs.tf`, `firestore.rules`, `README.md`
- `infra/firebase.tf` (root module instantiation + re-exported outputs)
- `infra/gcp.tf` (additive: 4 auth APIs)
- `infra/variables.tf` (additive: sensitive `google_oauth_client_id/secret`)
- `infra/terraform.tfvars.example` (additive: empty OAuth placeholders)

## Verdicts

- `tofu fmt -check`: green on all job-0250 files (`atlas.tf` pre-existing drift not mine).
- `tofu init` (module + backend): clean, reused locked providers, no lockfile churn.
- `tofu validate`: Success — configuration is valid. (evidence/tofu_validate.txt)
- `tofu plan`: exit 1 ONLY due to the `mongodbatlas` provider (no Atlas API keys
  in this shell → HTTP 401; pre-existing, unrelated). The `google` provider
  authenticated and rendered all four auth resources + four API enablements.
  Output values: `firebase_anonymous_enabled=false`,
  `firebase_google_idp_enabled=false`, `firebase_firestore_database_name="(default)"`,
  `firebase_firestore_rules_release_name="cloud.firestore"`. Evidence:
  evidence/tofu_plan.txt + evidence/plan_firebase_summary.txt.

## Pending-user (USER_UNBLOCK 0250-A … 0250-G)

A verify gcloud/ADC; B enable auth APIs; C attach Blaze (console); D configure
OAuth consent screen (console); E create OAuth web client + drop creds in
gitignored tfvars; F run `tofu apply`; G (deferred to job-0253/0256) Firebase
web-app registration for the client SDK config object.

## Risks / open items

1. Plan exit 1 is an Atlas-provider artifact, not a job-0250 defect. With Atlas
   API keys set, the plan shows only the new firebase+API additions; the ~31
   other "to add" lines are the same empty-refreshed-state artifact.
2. Firestore location `nam5` is IMMUTABLE — change `firestore_location` before
   the first apply if single-region `us-central1` is preferred.
3. Google IdP is count-guarded: pre-credential only email/password is live;
   Google sign-in activates on the apply after 0250-E supplies OAuth creds.
   Intentional so validate/plan are green without secrets.
4. GA-only / no Firebase project enrollment in tofu. Identity Platform sign-in
   works regardless; the Firebase web-app client config (apiKey/appId) is a
   job-0253/0256 input from console registration (0250-G).
5. Custom claims (`{tier}`,`{case_ids}`) are SET by the Admin SDK at runtime
   (job-0252 token-minting path), not declared in Tofu — Identity Platform has
   no Terraform surface for claim schemas. The Firestore rules here are the
   enforcement half; minting is job-0252.

## Panel verdict (4-lens Opus, refute-by-default): PASS 4/4 CONFIRM
Minor doc-grade finding (contract lens): tier-gating citations should read SRS
§H.4 (not §F.1 — that's data-source tiering), and the manifest's
`{case_ids: string[]}` JWT claim is manifest-invented — Case ownership is
enforced via the Mongo user link per §H.2, not a claim. Code is correct;
citations to be corrected in the manifest by the orchestrator.
