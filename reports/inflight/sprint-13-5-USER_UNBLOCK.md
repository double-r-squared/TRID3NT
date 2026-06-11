# Sprint-13.5 — USER UNBLOCK queue

Production-mutation and console-only steps that GRACE-2 agents cannot perform
(permission-classifier-denied production mutations + steps with no complete
Terraform/API surface). Each item is the **exact** command or click-path plus a
one-line why. Append-only: jobs add entries; the user works them on return.

Convention: one ```bash``` block (or numbered click-path) per item + one line of
context. Do NOT delete worked items — strike them or annotate "DONE <date>".

---

## job-0250 — Firebase / Identity Platform production auth (infra)

Tofu code is complete and `tofu validate` is green; `tofu plan` renders all four
auth resources cleanly (evidence: `reports/inflight/job-0250-infra-20260611/evidence/`).
The following steps require the user's hand (production mutation) or are
console-only by nature.

### 0250-A — Verify gcloud auth + ADC (read-only; user session)
The agent's shell has no `gcloud` login and ADC is unavailable, so it cannot run
the production `tofu apply`. Re-establish before any apply:
```bash
gcloud auth login
gcloud auth application-default login
gcloud config set project grace-2-hazard-prod
gcloud auth application-default print-access-token >/dev/null && echo "ADC OK"
```
Context: interactive auth is always the user's step (CLAUDE.md machine-state rule).

### 0250-B — Enable the auth APIs (production mutation)
```bash
gcloud services enable \
  identitytoolkit.googleapis.com \
  firebase.googleapis.com \
  firestore.googleapis.com \
  firebaserules.googleapis.com \
  --project grace-2-hazard-prod
```
Context: Tofu also declares these (`google_project_service.enabled[...]`), but
the first `tofu apply` reads the Identity Platform config, which 403s until
`identitytoolkit` is on. Enabling up front makes the apply single-pass. The
`tofu plan` confirms these four API resources are queued to be created.

### 0250-C — Attach the Blaze (pay-as-you-go) billing plan to Firebase (console-only)
Click-path:
1. https://console.firebase.google.com/ → select project **grace-2-hazard-prod**
   (enroll the GCP project into Firebase if prompted — "Add Firebase to an
   existing Google Cloud project").
2. Gear → **Usage and billing** → **Details & settings** → **Modify plan**.
3. Choose **Blaze (Pay as you go)**, link billing account `01212A-92BE96-BB3841`.
Context: Identity Platform's Google-sign-in IdP + multi-provider features need a
Blaze project. Auth free tier is 50k MAU — no new cost commitment (decision #7).
Google exposes no Terraform/API surface to attach a billing PLAN to Firebase.

### 0250-D — Configure the OAuth consent screen (console-only)
Click-path:
1. https://console.cloud.google.com/auth/branding?project=grace-2-hazard-prod
   (APIs & Services → OAuth consent screen).
2. **User type: External**; **Publishing status: Testing** (leave in Testing —
   decision #4, no Google verification review needed; 100-test-user cap is ample).
3. App name: **GRACE-2**. User support email: **natealmanza3@gmail.com**.
4. App domain → Privacy policy URL: **https://grace-2-hazard-prod.web.app/privacy**
   (the `/privacy` page lands in job-0285).
5. Developer contact email: **natealmanza3@gmail.com**.
6. **Test users** → Add **natealmanza3@gmail.com** (decision #5; add invitees on
   demand).
Context: consent-screen branding has no complete Terraform/API surface — it is
console-only by nature.

### 0250-E — Create the OAuth 2.0 Web client + drop creds into tfvars (console + local edit)
Click-path:
1. https://console.cloud.google.com/auth/clients?project=grace-2-hazard-prod
   → **Create client** → Application type **Web application** → name e.g.
   "GRACE-2 web".
2. Authorized JavaScript origins / redirect URIs: add
   `https://grace-2-hazard-prod.firebaseapp.com/__/auth/handler` (the Firebase
   Auth handler) and `http://localhost` (dev emulator). Firebase auto-manages
   the handler redirect once the project is enrolled; add the handler URI to be
   safe.
3. Copy the **Client ID** and **Client secret**.
4. Put them in the gitignored tfvars (NEVER commit — `*.tfvars` is gitignored):
```bash
# infra/terraform.tfvars  (append; gitignored)
google_oauth_client_id     = "<paste client id>.apps.googleusercontent.com"
google_oauth_client_secret = "<paste client secret>"
```
Context: until these are set, the `google_identity_platform_default_supported_idp_config.google`
resource is skipped via `count = 0` (plan shows `firebase_google_idp_enabled =
false`) and email/password sign-in is the only path. Once set, the next apply
provisions the Google IdP.

### 0250-F — Run the production apply (production mutation — user-run)
```bash
cd infra
tofu apply   # review the firebase resources, confirm
```
Context: the permission classifier blocks `tofu apply` for agents (production
mutation). The agent's `tofu plan` (evidence dir) shows the four auth resources
ready: `module.firebase.google_identity_platform_config.auth`,
`google_firestore_database.auth`, `google_firebaserules_ruleset.auth`,
`google_firebaserules_release.auth`.
NOTE: the agent's plan also lists ~31 other "to add" resources — that is a
refresh artifact of this session lacking MongoDB Atlas API keys (the atlas
provider 401'd so state could not be refreshed). On a properly-authed user
session the plan will show ONLY the new firebase + API resources as additions.
Set `MONGODB_ATLAS_PUBLIC_KEY` / `MONGODB_ATLAS_PRIVATE_KEY` before apply so the
atlas state refreshes and the plan is clean.

### 0250-G — (deferred to job-0253/0256, noted here) Firebase project enrollment + web-app registration (console / google-beta)
The web-app client-config object (apiKey, authDomain, projectId, appId…) the web
client consumes is produced by registering a **Web app** in the Firebase console
(or via `google_firebase_web_app`, which requires the `google-beta` provider this
repo deliberately does not use). job-0250 owns only the Identity Platform + auth
surface; the web-app config is a job-0253 (web auth) / job-0256 (web deploy)
input. Click-path when those jobs run:
1. Firebase console → project **grace-2-hazard-prod** → **Project settings** →
   **Your apps** → **Add app** → **Web** → register "GRACE-2 web".
2. Copy the `firebaseConfig` object → goes into `VITE_FIREBASE_CONFIG` (job-0256).
Context: Identity Platform sign-in (provisioned by job-0250) works regardless of
this enrollment; this is only the client SDK config surface.

---

## job-0251 — Signed-URL minting Cloud Function (infra)

Tofu code is complete and `tofu validate` is green; a targeted `tofu plan`
renders all 9 signed-URL resources cleanly (13 to add incl. transitive sibling
API deps, **0 to change, 0 to destroy** — evidence:
`reports/inflight/job-0251-infra-20260611/evidence/`). Unit tests: 55 passing on
`services/agent/.venv`. The following require the user's hand.

### 0251-A — Build + upload the function source zip (gen2 deploys from a GCS archive)
Context: `infra/signed_urls.tf` references the source by object name
(`var.signed_url_source_object`, placeholder default). The real zip is built from
`infra/signed_urls/{main.py,requirements.txt}` and uploaded BEFORE applying the
function. (`archive_file` is intentionally unused — not in this module's provider
lock; `versions.tf` is a separate ownership surface.)
```bash
cd infra/signed_urls
SRC_ZIP="$(mktemp -d)/signed-urls-$(date +%Y%m%d%H%M%S).zip"
zip -j "$SRC_ZIP" main.py requirements.txt   # test file excluded from deploy
OBJ="signed-urls/$(basename "$SRC_ZIP")"
gsutil cp "$SRC_ZIP" "gs://grace-2-hazard-prod-artifacts/$OBJ"
echo "set var.signed_url_source_object = $OBJ"
```
Then set `signed_url_source_object = "<OBJ>"` in `infra/terraform.tfvars`.

### 0251-B — (conditional) signBlob self-binding — if apply lacks IAM perms
Context: runtime SA `signed-url-minter@grace-2-hazard-prod.iam.gserviceaccount.com`
must hold `roles/iam.serviceAccountTokenCreator` ON ITSELF so the function mints
V4 URLs via IAM signBlob WITHOUT a key file (manifest HARD CONSTRAINT). `tofu
apply` creates this (`...signer_token_creator`); if the deployer ADC cannot, run:
```bash
gcloud iam service-accounts add-iam-policy-binding \
  signed-url-minter@grace-2-hazard-prod.iam.gserviceaccount.com \
  --member="serviceAccount:signed-url-minter@grace-2-hazard-prod.iam.gserviceaccount.com" \
  --role="roles/iam.serviceAccountTokenCreator" \
  --project=grace-2-hazard-prod
```

### 0251-C — Apply the signed-URL function resources (production mutation — user-run)
Context: `tofu apply` is classifier-blocked for agents. Run after 0251-A (+B):
```bash
cd infra
tofu apply -var="signed_url_source_object=<OBJ-from-0251-A>" \
  -target=google_project_service.signed_url_functions \
  -target=google_service_account.signed_url_minter \
  -target=google_secret_manager_secret_iam_member.signed_url_minter_srv_accessor \
  -target=google_service_account_iam_member.signed_url_minter_signer_token_creator \
  -target=google_storage_bucket_iam_member.signed_url_minter_runs_viewer \
  -target=google_storage_bucket_iam_member.signed_url_minter_cog_viewer \
  -target=google_storage_bucket_iam_member.signed_url_minter_fgb_viewer \
  -target=google_cloudfunctions2_function.mint_signed_url \
  -target=google_cloud_run_v2_service_iam_member.signed_url_agent_invoker
# (drop -target to apply the full sprint-13.5 graph once all Stage-1 jobs land
#  and a full reviewed `tofu plan` is clean)
```

### 0251-D — Live signed-URL round-trip verify (adversarial live-verify lens)
Context: the panel's live-verify lens needs proof a minted URL works until TTL
and 403s after expiry. After deploy, mint a 900s URL for a real layer the test
user owns (real HTTP call carries a Firebase ID token in the Authorization
header; the Cloud Run invoker gate uses the caller's ADC):
```bash
URL="$(gcloud functions call grace-2-mint-signed-url --region=us-central1 --gen2 \
  --data '{"layer_uri":"gs://grace-2-hazard-prod-runs/<real-object>","user_id":"<uid>","case_id":"<case>","ttl_seconds":900}')"
# fetch immediately -> 200; wait > 900s -> the signed URL returns 403 (expired)
```
