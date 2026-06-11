# job-0250 — infra — Firebase / Identity Platform production auth provisioning (kickoff, frozen)

Specialist: infra (Opus runner). Sprint: 13.5, Stage 1. Dispatched 2026-06-11.

## Mission

Provision the production auth substrate for `grace-2-hazard-prod` via OpenTofu:
Firebase Auth / Identity Platform sign-in providers, custom-claims-backed
Firestore tier-gating store, and deny-all/per-UID Firestore security rules. Code
complete + `tofu validate` green + `tofu plan` captured; every production
mutation / console-only step recorded to the USER_UNBLOCK queue. STATE ends
IN_REVIEW (adversarial 4-lens panel follows).

## Locked decisions (sprint-13-5-decisions.md — binding)

- #3 Domains: defaults only (`<project>.web.app` + `.firebaseapp.com` + localhost).
- #4 OAuth consent: External + Testing; app "GRACE-2"; support
  natealmanza3@gmail.com; privacy `https://<hosting-domain>/privacy`.
- #5 Test user: natealmanza3@gmail.com.
- #6 Anonymous in prod: OFF (dev-only via agent `AUTH_REQUIRED=false`).
- #7 Billing: Blaze attach = console-only user step.
- Project = grace-2-hazard-prod.

## File ownership

`infra/firebase/` (new — main.tf, variables.tf, outputs.tf [+firestore.rules,
README.md]) + minimal root wiring (`infra/firebase.tf`, additive APIs in
`infra/gcp.tf`, OAuth vars in `infra/variables.tf` + `terraform.tfvars.example`).
`git add` only job-0250 files.

## Execution reality

Read-only gcloud/tofu permitted; `tofu apply` + mutating gcloud denied →
captured VERBATIM in `reports/inflight/sprint-13-5-USER_UNBLOCK.md`. OAuth
consent screen + Blaze attach are console-only by nature → click-paths in the
same file. No Gemini/Vertex generate calls.

## Definition of done

Tofu code complete + `tofu validate` green + `tofu plan` evidence captured +
USER_UNBLOCK entries complete + honest report (provisioned vs pending-user) +
STATE=IN_REVIEW + commit "job-0250: ..." with Co-Authored-By: Claude Fable 5.
