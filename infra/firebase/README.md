# infra/firebase/ — production auth substrate (job-0250)

**Owner:** infra specialist. **Sprint:** 13.5, Stage 1.

Identity Platform (Firebase Auth backend) + Firestore (custom-claims / tier
store) + Firestore security rules for `grace-2-hazard-prod`. Provisioned via
the GA `hashicorp/google` provider the root module already pins (`~> 6.0`,
locked at 6.50.0) — **no `google-beta` is introduced**.

## What Tofu provisions here (apply-able once ADC + APIs are live)

| Resource | Purpose |
|---|---|
| `google_identity_platform_config.auth` | Sign-in providers (email/password ON, anonymous OFF), authorized domains, self-service permissions |
| `google_identity_platform_default_supported_idp_config.google` | Google sign-in IdP — `count`-guarded; provisions only once OAuth client creds are supplied |
| `google_firestore_database.auth` | Native Firestore (`(default)`, `nam5`), PITR on, delete-protected, ABANDON on destroy |
| `google_firebaserules_ruleset.auth` + `google_firebaserules_release.auth` | Production rules from `firestore.rules` (deny-all + per-UID isolation), released to `cloud.firestore` |

Root wiring: `../firebase.tf` (module block + re-exported outputs). Auth APIs
(`identitytoolkit`, `firebase`, `firestore`, `firebaserules`) added to
`local.enabled_apis` in `../gcp.tf`.

## Locked decisions honored (sprint-13-5-decisions.md)

- **#4 OAuth consent**: External + Testing; app "GRACE-2"; support email
  natealmanza3@gmail.com; privacy URL `https://<hosting-domain>/privacy`.
- **#5 Test users**: natealmanza3@gmail.com.
- **#6 Anonymous in prod**: OFF (`sign_in.anonymous.enabled = false`). Dev-only
  anonymous lives behind the agent's `AUTH_REQUIRED=false`, never here.
- **#3 Domains**: defaults only (`<project>.web.app` + `.firebaseapp.com` +
  localhost). No custom DNS.
- **#7 Billing**: Blaze attach is a console-only user step.

## USER / CONSOLE steps (cannot be Tofu — see `reports/inflight/sprint-13-5-USER_UNBLOCK.md`)

1. **Enable auth APIs** — `gcloud services enable identitytoolkit.googleapis.com
   firebase.googleapis.com firestore.googleapis.com firebaserules.googleapis.com`.
   (Tofu also declares these, but the first `tofu apply` needs them, and `tofu
   plan` against a project where `identitytoolkit` is off will error on the
   Identity Platform read.)
2. **Attach Blaze billing plan** to the Firebase project — console-only.
3. **Configure the OAuth consent screen** (app name / support email / privacy
   URL / test users) — console-only; Google exposes no full Terraform/API
   surface for consent branding.
4. **Create the OAuth 2.0 web client**, copy its client ID + secret into the
   gitignored `infra/firebase/terraform.tfvars` (or root `terraform.tfvars`) as
   `google_oauth_client_id` / `google_oauth_client_secret`. Until then the
   Google IdP resource is skipped and email/password sign-in is the only path.
5. **Firebase project enrollment + web-app registration** (`google_firebase_*`,
   which require `google-beta`) — done in console; the web app's client config
   object is consumed by job-0253 (web auth) / job-0256 (web deploy), not by
   job-0250. Identity Platform sign-in works regardless of Firebase enrollment.
6. **`tofu apply`** (production mutation) — user-run.

## Why GA-only (not google-beta)

`tofu providers schema -json` against the locked provider confirms
`google_identity_platform_*`, `google_firestore_*`, and `google_firebaserules_*`
are all present in GA. Only `google_firebase_project` / `google_firebase_web_app`
/ `google_firebase_hosting_site` require `google-beta` — and those are project
*enrollment* / app *registration*, which are console-doable and consumed by
later jobs. Staying GA-only avoids a new provider dependency, a forced re-init,
and `.terraform.lock.hcl` churn, preserving the repo's single-provider posture.
