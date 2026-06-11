# infra/firebase/main.tf — production auth substrate (Identity Platform + Firestore).
#
# Job-0250 (sprint-13.5 Stage 1). Owner: infra specialist.
#
# WHAT THIS MODULE PROVISIONS (all via the GA `hashicorp/google` provider the
# root module already pins at ~> 6.0 — see ../versions.tf):
#   1. Identity Platform tenant config for the project: sign-in providers
#      (email/password for dev; Google OAuth for prod), authorized domains,
#      anonymous-sign-in posture, end-user self-service permissions.
#   2. The Google sign-in IdP (`google.com`) wired to an OAuth web client.
#   3. A native Firestore database (custom-claims tier-gating store; SRS §F.1).
#   4. Production Firestore security rules: deny-all default + per-UID
#      namespace isolation (a user can read/write ONLY documents under their
#      own UID), released to the database.
#
# WHY A CHILD MODULE (and why GA provider only):
#   The root module is flat (every ../*.tf references google_project.grace2 +
#   local.common_labels directly). To honor the manifest's `infra/firebase/`
#   directory ownership WITHOUT polluting the root namespace, this is a true
#   child module receiving project_id / region / common_labels as inputs from
#   ../firebase.tf. It uses ONLY GA `google` resources
#   (google_identity_platform_*, google_firestore_*, google_firebaserules_*),
#   all present in the locked provider (6.50.0) — verified via
#   `tofu providers schema -json`. No `google-beta` is introduced; the repo's
#   single-GA-provider convention is preserved and no re-init / lockfile churn
#   is forced.
#
# WHAT IS *NOT* HERE (deliberately — see ./README.md + USER_UNBLOCK):
#   - Firebase *project enrollment* (google_firebase_project) and web-app
#     registration (google_firebase_web_app) require `google-beta`. The web
#     app's client config object is consumed by job-0253/job-0256, not job-0250.
#     These are captured as console / unblock steps. Identity Platform (which
#     IS what gates sign-in) is fully provisioned here regardless of Firebase
#     project enrollment.
#   - The OAuth *consent screen* branding (app name, support email, privacy
#     URL, test users) — Google exposes no complete Terraform/API surface for
#     consent branding; it is console-only by nature. Captured in USER_UNBLOCK.
#   - The OAuth *web client* itself (client_id / client_secret). Created in the
#     console alongside the consent screen; the values are fed in as the
#     (gitignored) sensitive variables google_oauth_client_id/secret. When
#     unset, the Google IdP resource is skipped via count (email/password still
#     works), so `tofu plan` is green pre-credential.
#   - Blaze billing plan attach for Identity Platform — console-only (USER_UNBLOCK).

# --- Identity Platform project config -------------------------------------
#
# google_identity_platform_config is the project-level Identity Platform tenant
# settings resource. It controls which first-party sign-in methods are enabled
# and which domains may host the sign-in widget.
#
# Decisions locked in sprint-13-5-decisions.md:
#   - email/password ENABLED (decision: dev convenience; password_required=true)
#   - Google sign-in ENABLED (separate IdP resource below)
#   - anonymous DISABLED in prod (decision #6: require sign-in; anonymous stays
#     dev-only behind AUTH_REQUIRED=false in the agent, never in this prod
#     Identity Platform config). autodelete_anonymous_users left at provider
#     default; no anonymous users will exist to delete.
#   - authorized_domains: the default Firebase Hosting domains
#     (<project>.web.app, <project>.firebaseapp.com) plus localhost for the dev
#     emulator + dev server. No custom DNS (decision #3).
resource "google_identity_platform_config" "auth" {
  project = var.project_id

  # Domains permitted to host the Identity Platform sign-in flow. Decision #3:
  # defaults only — the two Firebase Hosting domains the project gets for free
  # plus localhost (dev emulator / `npm run dev`). A custom domain is a
  # follow-up job when the user buys one (only the user can own DNS).
  authorized_domains = var.authorized_domains

  sign_in {
    # Email/password — enabled for dev/test convenience (decision #4/#6: prod
    # primary path is Google, but email/password is retained for local dev and
    # for a fallback test login). password_required=true forces a real password
    # (no email-link-only accounts).
    email {
      enabled           = true
      password_required = true
    }

    # Anonymous sign-in — DISABLED in production (decision #6). The agent's
    # dev-only sticky-anonymous behavior is gated by AUTH_REQUIRED=false at the
    # service layer (job-0252), NOT by enabling anonymous auth here. Keeping it
    # off at the Identity Platform level is the hard guarantee that no
    # anonymous credential can be minted against the prod project.
    anonymous {
      enabled = false
    }
  }

  # End-user self-service permissions. Both left ENABLED (disabled_* = false):
  #   - disabled_user_signup=false  → new users may self-register (required:
  #     the demo flow is "a new user can sign in"; manifest goal line 11).
  #   - disabled_user_deletion=false → a user may delete their own account
  #     (privacy hygiene; no reason to block).
  client {
    permissions {
      disabled_user_signup   = false
      disabled_user_deletion = false
    }
  }

  # Identity Platform requires the API to be enabled first (wired via the root
  # module's google_project_service set — see ../firebase.tf depends_on).
  depends_on = [var.api_enable_dependency]
}

# --- Google sign-in IdP ----------------------------------------------------
#
# google_identity_platform_default_supported_idp_config wires the `google.com`
# federated IdP to an OAuth 2.0 web client. The client_id/client_secret are
# created in the GCP console (OAuth consent screen flow — console-only) and fed
# in as sensitive, gitignored variables.
#
# count guard: when credentials are not yet provided (the pre-unblock state on
# this dev box), the resource is SKIPPED so `tofu plan`/`validate` stay green.
# Email/password sign-in (above) works without it. Once the user creates the
# OAuth client in the console and drops the values into the (gitignored)
# terraform.tfvars, this resource provisions the Google provider.
resource "google_identity_platform_default_supported_idp_config" "google" {
  count = var.google_oauth_client_id != "" && var.google_oauth_client_secret != "" ? 1 : 0

  project       = var.project_id
  enabled       = true
  idp_id        = "google.com"
  client_id     = var.google_oauth_client_id
  client_secret = var.google_oauth_client_secret

  depends_on = [google_identity_platform_config.auth]
}

# --- Firestore database (custom-claims / tier-gating store) ----------------
#
# SRS §F.1 tier gating: custom claims { tier: "free"|"pro" } and per-user
# case_ids are stored/derived in Firestore. A native-mode database is required
# for security-rules-enforced per-UID isolation.
#
# Notes:
#   - name="(default)" is the canonical single-database id; Firebase tooling
#     and the Admin SDK target the default DB unless told otherwise.
#   - location_id = nam5 (multi-region US) for durability; matches us-central1
#     residency expectations without being a single-zone SPOF. (Firestore
#     location is IMMUTABLE after creation — chosen deliberately.)
#   - delete_protection + deletion_policy=ABANDON: never let a `tofu destroy`
#     wipe user identity data. The DB outlives the IaC lifecycle.
resource "google_firestore_database" "auth" {
  project     = var.project_id
  name        = "(default)"
  location_id = var.firestore_location
  type        = "FIRESTORE_NATIVE"

  # Production durability posture.
  point_in_time_recovery_enablement = "POINT_IN_TIME_RECOVERY_ENABLED"

  # Safety: this DB holds user identity / tier data. Block accidental deletion
  # at the API level, and ABANDON (don't delete) if the resource leaves state.
  delete_protection_state = "DELETE_PROTECTION_ENABLED"
  deletion_policy         = "ABANDON"

  depends_on = [var.api_enable_dependency]
}

# --- Production Firestore security rules -----------------------------------
#
# The rules content lives in ./firestore.rules (authored as real CEL-like
# Firestore Security Rules so it can be linted / diffed as a first-class file,
# not buried in an HCL heredoc). Loaded via file().
#
# Policy (SRS §F.1; manifest job-0250 scope "deny-all default, user can only
# read/write their own UID namespace"):
#   - default: deny all reads + writes
#   - /users/{uid}/**          : allow iff request.auth.uid == uid
#   - /cases/{caseId}          : allow iff resource.data.user_id == auth.uid
#                                (or, on create, the new doc's user_id == uid)
# Enforced server-side by Firestore regardless of client code.
resource "google_firebaserules_ruleset" "auth" {
  project = var.project_id

  source {
    files {
      name    = "firestore.rules"
      content = file("${path.module}/firestore.rules")
    }
  }

  depends_on = [google_firestore_database.auth]
}

# Bind the ruleset to the (default) Firestore database. The release name for
# Firestore rules is the fixed string "cloud.firestore"; pointing it at the new
# ruleset activates the rules. Updating ./firestore.rules → new ruleset →
# tofu re-points this release (zero-downtime rules roll).
resource "google_firebaserules_release" "auth" {
  project      = var.project_id
  name         = "cloud.firestore"
  ruleset_name = google_firebaserules_ruleset.auth.name

  # Replace-in-place when the ruleset changes (matches Firebase CLI behavior).
  lifecycle {
    replace_triggered_by = [google_firebaserules_ruleset.auth.name]
  }
}
