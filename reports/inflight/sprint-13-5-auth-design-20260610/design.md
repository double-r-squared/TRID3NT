# Sprint-13.5 AUTH + SIGNED-URL DESIGN

**Job:** sprint-13-5-auth-design-20260610 (schema+agent cross-specialist, design-only)
**Date:** 2026-06-10
**Status:** dispatch-ready design. NO application/infra code written by this job.
**Scope guard:** this document is the single source of truth for the architecture the Stage-1/2/3 runners hook into. Runners MUST NOT re-derive the seams cited below.

> **Design-time discovery that re-shapes the manifest.** The web auth layer is
> already substantially built (`web/src/auth.ts`, `web/src/ws.ts`, `web/src/components/AuthGate.tsx`)
> from a prior wave (job-0172 Part C + the H.5 handshake). The web client *already*
> signs in (anonymous + Google), already fetches an ID token via `getIdToken()`, and
> already emits the `auth-token` envelope on connect (`web/src/ws.ts:861-900`). The agent
> *already* verifies the token (`auth_handshake.py:_verify_id_token_sync`) and binds the
> resolved user. **Sprint-13.5 auth is therefore mostly a HARDENING + ENFORCEMENT job, not a
> greenfield build.** The three real holes are: (1) no production Firebase project provisioned;
> (2) the anonymous-fallback is unconditional even in prod (no `AUTH_REQUIRED` gate); (3) the
> **ownership write/enforce chain is entirely missing** (OQ-0115). Sections below recut the
> per-job scope to reflect this. The manifest's job IDs and seams are preserved; the *internal*
> scope of job-0252 / job-0253 shifts from "build" to "harden + close OQ-0115".

---

## 1. Identity provider decision

### Recommendation: **Firebase Authentication console/SDK surface, provisioned via the `google-beta` Terraform provider's `google_identity_platform_config` + `google_identity_platform_default_supported_idp_config` resources.**

This is not a contradiction — Firebase Auth and GCP Identity Platform are the same backend (per SRS H.1, H.7 Decision P). The decision is about **which API surface Tofu and the SDKs target**:

- **Client + Admin SDK surface = Firebase.** The web client uses the `firebase/auth` JS SDK (already wired in `web/src/auth.ts:101-106` against `VITE_FIREBASE_*` config). The agent uses the `firebase-admin` Python SDK (already wired in `auth_handshake.py:88-95` via `firebase_admin.initialize_app(credentials.ApplicationDefault())`). **No change to either SDK choice.** Decision P (H.7) pins this; the implementations already conform.
- **Provisioning surface = Identity Platform via `google-beta`.** Firebase project config has no first-class Tofu resource in the GA `google` provider; the clean IaC path is the Identity Platform resources in `google-beta`:
  - `google_identity_platform_config` — enables Identity Platform on the project (this is the "upgrade Firebase Auth → Identity Platform" flip H.1 describes; required to manage providers via Tofu rather than console).
  - `google_identity_platform_default_supported_idp_config` (`idp_id = "google.com"`) — enables Google sign-in.
  - Email/password is enabled via the `email` block on `google_identity_platform_config` (`enabled = true`, `password_required = true`).
  - Anonymous sign-in is NOT a Tofu resource — it is a project setting toggled in the Firebase console OR via the Identity Platform REST `projects.config` patch (`signIn.anonymous.enabled = true`). **Flag this as a manual/REST step in job-0250** (see §7 decision items + §6 job-0250 scope).

**Why Identity-Platform-resources-over-Firebase-resources:** the `google` GA provider has no usable Firebase Auth resource; the community `google_firebase_*` resources (in `google-beta`) cover Hosting and project linkage but provider/sign-in config is cleanest through `google_identity_platform_*`. Identity Platform resources are GA-quality in `google-beta`, idempotent, and `plan`-stable. Going through Identity Platform from day one also means the H.1 enterprise-SKU upgrade path is already on the managed surface (no re-keying — consistent with Decision P).

**Pinned OAuth providers for v0.1:**
- **Google sign-in** (`google.com`) — the production primary, per manifest job-0253 + already wired in `web/src/auth.ts:198-200` (`GoogleAuthProvider` + `signInWithPopup`).
- **Email/password** — enabled, used as the dev/test sign-in (E2E test account in job-0259) and as a fallback. Already referenced as the dev path in `auth.ts` and manifest job-0253.
- **Anonymous** — enabled, but **only honored when `AUTH_REQUIRED=false`** (dev). In production (`AUTH_REQUIRED=true`) the agent rejects anonymous (see §2). Web already does `signInAnonymously` (`auth.ts:221`); this design keeps the anonymous *upgrade* path (H.3) but gates its acceptance server-side by deployment mode.

**Anonymous upgrade path (v0.1):** keep H.3 `linkWithCredential` preserving `uid`. Because the web already signs in anonymously and the agent already supports sticky-anonymous reuse (`auth_handshake.py:_try_reuse_anonymous_user`), the upgrade in production is: anonymous Firebase user → `linkWithCredential(Google)` → SAME `uid`. Since the agent resolves `uid`→`User` by `firebase_uid` (`persistence.get_user_by_firebase_uid`), the User record and its Cases survive the link automatically. **The only migration concern is for the dev-era sticky-ULID anonymous users that have `firebase_uid=None`** — those are handled by the §3 backfill, not by `linkWithCredential`.

**Hooks (cite for job-0250 runner):**
- SRS: `docs/srs/H-auth-and-users.md:11-39` (H.1 provider), `:164-183` (H.7 Decision P).
- Provider config consumed by web: `web/src/auth.ts:66-67,101-106` (`VITE_FIREBASE_*`).
- Admin SDK init: `services/agent/src/grace2_agent/auth_handshake.py:72-114` (`init_firebase_admin`, ADC).
- Tofu conventions: `infra/gcp.tf:16-21` (common_labels), `:26-40` (enabled_apis — add `identitytoolkit.googleapis.com`), provider blocks in `infra/providers.tf` (add `google-beta`).

**SRS amendment flagged:** none required for the provider decision (H.1/H.7 already pin Firebase). **One additive amendment proposed** to record that v0.1 production provisioning goes through `google_identity_platform_*` (Identity Platform surface) rather than console-only — narrow file `docs/srs/H-auth-and-users.md` (append a sentence to H.1's "What Firebase Auth provides" or a new H.1.1 "Provisioning surface" note). Specialist proposes; user lands.

---

## 2. Token verification at the WS boundary

### 2a. Verification mechanism: **keep the `firebase-admin` SDK (`verify_id_token`), do NOT hand-roll JWKS.**

Already in force at `auth_handshake.py:117-135` (`_verify_id_token_sync` → `firebase_admin.auth.verify_id_token(token, check_revoked=True)`). **Recommendation: keep it.** Justification:
- `firebase-admin` handles JWKS rotation, `iss`/`aud`/`exp`/`iat`/`sub` validation, and `check_revoked=True` (revocation-list + account-deletion tombstoning) — exactly the H.5 contract (`docs/srs/H-auth-and-users.md:122,128,130`). A manual JWKS implementation would re-derive all of this and own the key-cache TTL bug surface.
- `check_revoked=True` requires a Firestore read per verification; this is acceptable at v0.1 connect-frequency (one verify per WS connect + one per refresh). If connect QPS ever makes this a latency problem, the mitigation is `check_revoked=False` on the hot path + a periodic revocation sweep — **deferred, not v0.1**.
- Cost: the SDK is already a dependency (`auth_handshake.py:89`), already initialized via ADC.

**Change required (job-0252):** none to the verification *call*. The change is to the **fallback policy** around it (§2b).

### 2b. The anonymous→authenticated semantics change (the real job-0252 work)

**Current behavior (`auth_handshake.py:authenticate_token`):** the function is a 3-branch fallback machine. Branch 1 (empty token + anon hint → sticky reuse), branch 2 (verify fails → anonymous), branch 3 (no usable hint → fresh anonymous). **Every failure is a path to an anonymous user — there is no rejection path.** In production this is a security hole: a forged/absent token silently becomes a usable anonymous session.

**Designed change — introduce `AUTH_REQUIRED` deployment gate (env var, default `false`):**

```
AUTH_REQUIRED=false  (dev, CI, local)   → current 3-branch behavior unchanged (sticky anon preserved)
AUTH_REQUIRED=true   (production)        → verification is MANDATORY:
                                            - empty/missing token  → REJECT (close WS with AUTH_FAILED)
                                            - verify_id_token fails → REJECT (close WS with AUTH_TOKEN_INVALID)
                                            - verified anonymous-provider token → ACCEPTED (Firebase anonymous
                                              users carry a real verified uid; they are NOT the ULID-fallback path)
                                            - sticky-ULID anon hint with no token → REJECT (no credential)
```

**Critical distinction the runner must preserve:** there are TWO kinds of "anonymous":
1. **Firebase anonymous sign-in** (`signInAnonymously`, `auth.ts:221`) — issues a REAL verified ID token with a real `uid` and `firebase.sign_in_provider == "anonymous"` in the JWT claims. **This is acceptable in production** — it is verified, has a stable `uid`, and supports `linkWithCredential` upgrade (H.3). The agent sets `is_anonymous` from the verified claim, not from the ULID-fallback path.
2. **Sticky-ULID fallback** (`auth_handshake.py:_provision_anonymous_user`, the `is_anonymous=True` ULID User with `firebase_uid=None`) — this is the dev-era no-Firebase path. **This MUST be rejected in production** (`AUTH_REQUIRED=true`) because it has no credential at all.

So the production policy is: **a verified Firebase token is required (anonymous-provider OR Google OR password); the credential-less ULID fallback is disabled.** This is the "remove-don't-shim" the manifest job-0252 calls for, scoped precisely.

**Implementation seam (job-0252 owns `services/agent/src/grace2_agent/auth.py` NEW + `server.py` additive):**
- Manifest names `auth.py` as the new owned file. **Recommendation: do NOT fork a parallel module — make `auth.py` a thin policy layer that wraps the existing `auth_handshake.authenticate_token`.** `auth_handshake.py` is battle-tested (the H.3/H.5 implementation in force) and owned by no other 13.5 job. `auth.py` adds: (a) `auth_required() -> bool` reading `AUTH_REQUIRED`; (b) a `resolve_or_reject(token_envelope, persistence) -> AuthResult | AuthRejection` that calls `authenticate_token` and then, if `AUTH_REQUIRED` and the result is the ULID-fallback (`result.is_anonymous and result.firebase_uid is None`), converts it to a rejection. Keep `authenticate_token` unchanged so dev/CI and the 32 existing tests stay green.
- Wire at `server.py:_handle_auth_token` (`server.py:1094-1136`) and `_ensure_auth_handshake` (`server.py:1186-1212`). In `AUTH_REQUIRED` mode, `_ensure_auth_handshake`'s implicit-anonymous path (`server.py:1200`) MUST become a close-with-`AUTH_FAILED` instead of an anonymous bind.

**Claims → User mapping (unchanged, already correct):**
- `claims["uid"]` → `User.firebase_uid` and resolution key (`auth_handshake.py:235,251-256`, `persistence.get_user_by_firebase_uid`).
- `claims["email"]` → `User.email`; `claims["name"]` → `User.display_name`.
- `claims["tier"]` → `AuthResult.tier` with `free` default + validation (`auth_handshake.py:242-248`). **Note the H.4 contract:** tier is a *custom claim* on the JWT, written by an Admin-SDK `set_custom_user_claims` call. v0.1 has no tier-bumping path (all `free`), so the claim is usually absent and the `free` default fires. **No change needed for v0.1.**
- `User.user_id` (ULID) is minted server-side on first connect (`auth_handshake._resolve_or_provision_user:390`). This is the canonical id for ownership.

### 2c. Anonymous→authenticated UPGRADE migration (the localStorage sticky-ULID case)

The manifest says: "the existing anonymous_user_id reconnect hint must migrate cases owned by the anonymous ULID to the authenticated user."

**Two sub-cases, with different mechanisms:**

1. **Firebase-anonymous → Google link (production happy path).** `linkWithCredential` preserves `uid`. Because Cases are owned by `firebase_uid`-resolved `User.user_id` (a stable ULID tied to the uid), and the uid does not change, **the Case ownership survives with zero migration.** The agent's next verify after the link returns the same `uid` → same `User` → same Cases. **No agent code change beyond accepting the post-link token.** (Web emits a fresh `auth-token` after link — already supported by `ws.ts`'s re-auth path.)

2. **Dev-era sticky-ULID anon → first real sign-in (the only case needing a migration).** A User created by `_provision_anonymous_user` has `user_id=<ULID-A>`, `firebase_uid=None`. The web persisted `<ULID-A>` in localStorage (`ws.ts:207` `grace2.anonymous_user_id`). On first authenticated connect, the client still sends both the verified token AND the `anonymous_user_id` hint (`ws.ts:900`). **Design the agent-side merge:**

```
on authenticated connect (verified uid → User_B with user_id=ULID-B):
  if token_envelope.anonymous_user_id is present AND != ULID-B:
    anon_user = persistence.get_user_by_id(anonymous_user_id)
    if anon_user is not None and anon_user.is_anonymous and anon_user.firebase_uid is None:
      # MERGE: re-point that anon user's Cases to the authenticated user.
      migrate_cases(from_user_id=ULID-A, to_user_id=ULID-B)
      mark anon_user.is_active=False  (tombstone; do not delete — audit trail)
      append_audit("anon-case-migration", {from: ULID-A, to: ULID-B, n_cases: ...})
```

`migrate_cases` is an `update-many` on `projects` setting `owner_user_id=ULID-B` where the doc's `owner_user_id == ULID-A` (see §3 for the ownership field). This is **idempotent** (running twice is a no-op since ULID-A's Cases are gone after the first run) and **best-effort** (a failure logs + continues; the user keeps their authenticated session, the migration retries next connect because the localStorage hint persists until the client clears it on `auth-ack(is_anonymous=false)`).

**Guard (carry the existing security gate):** only migrate when `anon_user.is_anonymous and anon_user.firebase_uid is None` — the same gate `_try_reuse_anonymous_user` already enforces (`auth_handshake.py:339-346`) so an attacker cannot replay a logged ULID to steal another authenticated user's Cases.

**Hooks (cite for job-0252):**
- `auth_handshake.authenticate_token:179-263`, `_try_reuse_anonymous_user:308-353`, `_provision_anonymous_user:266-305`.
- `persistence.get_user_by_id:759-789`, `upsert_user:743-757`.
- Web hint: `web/src/ws.ts:207,861-900`; `web/src/auth.ts:176-185` (`getIdToken`).
- Server bind: `server.py:_bind_auth_result:1139-1155`, `_handle_auth_token:1094-1136`.

### 2d. Token refresh mid-session

H.5 (`docs/srs/H-auth-and-users.md:126`) pins token refresh. Firebase ID tokens expire after 1h; the web `firebase/auth` SDK refreshes transparently and `getIdToken()` returns the fresh token. **Current gap:** the agent verifies once on connect and never re-verifies. For v0.1 with sessions typically < 1h, the existing token stays valid for the connection lifetime; a long-lived WS could outlive the token.

**Design (job-0252, minimal):** add a `token-refresh` envelope handler (the H.5-named mechanism). The web re-sends `auth-token` (same envelope, `ws.ts` already has the emit path) when `onIdTokenChanged` fires. Agent re-runs `resolve_or_reject`; on success updates `state.firebase_uid`/`state.tier`; on failure (refresh-token revoked) closes WS with `AUTH_TOKEN_EXPIRED`. **For v0.1, the simpler acceptable posture is: accept refreshed tokens via the existing `auth-token` handler (it re-runs verification idempotently) and add the `AUTH_TOKEN_EXPIRED` close path only.** Full mid-stream token-cache invalidation is deferred unless E2E (job-0259) shows a >1h session breaking. **Flag as job-0252 sub-scope, low-risk.**

**SRS amendment flagged:** Appendix A.6 error-code table (`docs/srs/A-websocket-protocol.md:610-628`) is **missing** `AUTH_TOKEN_INVALID`, `AUTH_TOKEN_EXPIRED`, `TIER_INSUFFICIENT` — H.5/H.7 reference them as "new A.6 codes" but they were never landed (only `AUTH_FAILED` exists). The agent code already emits `AUTH_TOKEN_INVALID` (`server.py:1117`) against an undocumented code. **Propose adding the three codes to the A.6 table** — narrow file `docs/srs/A-websocket-protocol.md` (the §A.6 table at line ~616). Schema specialist proposes; user lands. This is a documentation-catch-up, not a behavior change.

---

## 3. Case ownership closure (OQ-0115) — the highest-stakes correctness item

### The hole, precisely (verified live in this job)

1. **`CaseSummary` has NO `owner_user_id`/`user_id` field at all** (`packages/contracts/src/grace2_contracts/case.py:80-124`).
2. **The case-create handler writes NO owner** (`server.py:1355-1363` builds `CaseSummary` with only `case_id/title/created_at/updated_at/status`).
3. **`upsert_case` serializes only `CaseSummary.model_fields`** (`persistence.py:392`) — so even if you wanted to, no owner field reaches the `projects` doc on write.
4. **`_doc_to_case_summary` actively STRIPS `user_id`/`owner_user_id`** on read (`persistence.py:377`).
5. **`list_cases_for_user` is called with `state.session_id`, NOT `authenticated_user_id`** (`server.py:1238` `cases = await p.list_cases_for_user(state.session_id)`), and the query's `$or` includes `{"user_id": {"$exists": False}}` (`persistence.py:426`).

**Net effect:** no Case has ever been written with an owner; the *only* clause that returns any Case is the `$exists:False` backward-compat clause; therefore **every user (and every session) sees every Case.** This is a complete absence of ownership, not a partial one. The manifest frames OQ-0115 as "flip the query"; in truth the **write side must be built first** or the flip returns zero Cases for everyone.

### Designed fix (job-0252, two coordinated layers — one is a contract change)

**Layer A — write ownership (schema + agent):**
- **Contract amendment (schema, SRS-flagged):** add `owner_user_id: ULIDStr | None = None` to `CaseSummary` (`packages/contracts/src/grace2_contracts/case.py`). Nullable default keeps every existing test + the M1 in-memory path valid; `extra="forbid"` discipline (per `user.py` pattern) means the field is explicit. **This is the `owner_user_id` that H.2 (`docs/srs/H-auth-and-users.md:42`) already specifies as the canonical ownership field — the contract is finally catching up to the appendix.**
- **Agent write (job-0252):** at `server.py:1355` set `owner_user_id=state.authenticated_user_id` on the created `CaseSummary`. Update `persistence._doc_to_case_summary` to STOP stripping `owner_user_id` (`persistence.py:377` — keep `user_id` strip for the legacy alias but pass `owner_user_id` through). Update `upsert_case` body to persist it (automatic once it is a model field).

**Layer B — enforce ownership (agent):**
- Change the `_emit_case_list` call from `list_cases_for_user(state.session_id)` to `list_cases_for_user(state.authenticated_user_id)` (`server.py:1238`).
- Remove the `{"user_id": {"$exists": False}}` clause from `list_cases_for_user` (`persistence.py:426`) — **but only after backfill (below).** Keep the `owner_user_id` clause as the primary filter; keep the legacy `user_id` clause for the secrets-collection symmetry but the projects filter becomes strict on `owner_user_id`.
- `get_case` (`persistence.py:343`) should gain an optional `requesting_user_id` arg per H.2's `get_case(case_id, requesting_user_id)` contract (`docs/srs/H-auth-and-users.md:44`); return `None` if the doc's `owner_user_id` mismatches. **Recommendation: add it but make it optional (default None = no check) so M1/dev and the existing call sites stay valid; the production `case-command(select)` path passes `state.authenticated_user_id`.**

### Backfill strategy for `{owner_user_id: {$exists: false}}` legacy cases

**One-time migration on first authenticated production startup (job-0252, idempotent):**

```
MIGRATION_ANON_UID = "00000000000000000000000000"   # a fixed sentinel ULID (26 zeros), reserved
on agent startup when AUTH_REQUIRED=true and persistence is bound:
  update-many projects where owner_user_id absent AND user_id absent:
    $set owner_user_id = MIGRATION_ANON_UID
  append_audit("preauth-case-backfill", {sentinel: MIGRATION_ANON_UID, n: ...})
```

**Why a sentinel constant, not a real user:** the pre-Auth Cases have no real owner; assigning them to a sentinel UID (a) makes the strict-ownership query exclude them from every real user automatically (no real user has the sentinel uid), (b) preserves them for admin recovery (a future admin tool can list `owner_user_id == MIGRATION_ANON_UID`), (c) is auditable, (d) is idempotent (re-running matches zero docs after the first run).

**Recommendation on the orphan-case question (manifest asks: assign-to-first-authenticated-user vs admin pool):** **admin pool (the sentinel), NOT first-authenticated-user.** Assigning pre-Auth Cases to "the first user who signs in" is a data-leak footgun — the first production sign-in would be the developer's test account, which would then silently own (and see) every demo Case from the dev era. The sentinel pool keeps them invisible-to-all-users-but-recoverable, which is the conservative correct posture for a one-way deployment. The dev-era demo Cases (Fort Myers etc.) are re-creatable via the onboarding sample-Case path (job-0258), so nothing of value is lost.

**Sequencing within job-0252:** backfill MUST run BEFORE the `$exists` clause is removed from the live query, or there is a window where pre-Auth Cases are orphaned-and-queryable. Since both land in the same job + same deploy, the safe order is: (1) backfill on startup, (2) query is already strict in the deployed code. The startup backfill is gated on `AUTH_REQUIRED=true` so it never fires in dev.

**Hooks (cite for job-0252 + the schema contract job):**
- `case.py:80-124` (CaseSummary — add `owner_user_id`).
- `server.py:1355-1363` (write owner on create), `:1238` (list with authenticated_user_id).
- `persistence.py:_doc_to_case_summary:364-384`, `list_cases_for_user:406-447`, `get_case:343-362`.
- `auth_handshake.py:16` (already documents the `owner_user_id` cascade rule as the intent).
- H.2: `docs/srs/H-auth-and-users.md:42-48`.

**SRS amendment flagged:** Appendix D `ProjectDocument` shape should record `owner_user_id` as a persisted field if it does not already (verify against `docs/srs/D-mongodb-collection-schemas.md` §D.2). H.2 already prose-specifies it; D may need the field row. Narrow file `docs/srs/D-mongodb-collection-schemas.md`. Schema specialist proposes; user lands. The `CaseSummary` contract field itself is a code change (contracts package), not an SRS change, but it is a **contract amendment** — gate it through the schema specialist's contract-change discipline + the adversarial contract lens on job-0252.

---

## 4. Signed-URL minting service

### 4a. Recommendation: **Cloud Run service endpoint, NOT a Cloud Function — colocated as a route family on the agent service, with a dedicated minting SA via IAM impersonation.**

The manifest tentatively names a Cloud Function (`infra/signed_urls/`). **This design recommends a Cloud Run HTTPS endpoint instead**, for three reasons:
1. **The QGIS lockdown (job-0255) already requires the agent to host a `/qgis-proxy` route** that forwards authenticated WMS requests. Signed-URL minting is the same shape (authenticated HTTPS request → scoped GCS/credential operation). Hosting both as routes on the agent's existing Cloud Run service (or a sibling `grace2-signer` Cloud Run service) avoids a second cold-start surface and a second deploy artifact.
2. **The ownership check needs the `Persistence` singleton** (validate `user_id` owns `case_id` via the job-0203 `MCPSurfaceTranslator` seam). A Cloud Function would have to either re-instantiate the whole MCP-sidecar stack (heavy, and the MCP sidecar is a stdio subprocess — awkward in a Function) or call back into the agent. Colocating with the agent reuses `get_persistence()` directly.
3. Signing GCS URLs requires either the SA's private key OR the IAM Credentials API (`signBlob`) — both are simpler to wire on a Cloud Run service that already has a service-account identity.

**However — honor the manifest's file-ownership seam.** The manifest pins `infra/signed_urls/` to job-0251 and `layer_uri_emit.py` to job-0254. **Reconciliation: keep `infra/signed_urls/` as the directory but make it a tiny Cloud Run service (`grace2-signer`) rather than a Function**, OR fold the mint route into the agent service and repurpose `infra/signed_urls/` to hold only the Tofu IAM for the signer SA. **Recommended: standalone `grace2-signer` Cloud Run service in `infra/signed_urls/`** (clean SA boundary, independent scaling, no entanglement with the WS session lifecycle), invoked by the agent (which holds invoker permission). This keeps job-0251 (infra: the service + SA + IAM) and job-0254 (agent: call the signer at emit time) cleanly disjoint per the manifest.

### 4b. Minting contract

```
POST https://<signer-run-url>/mint           (Cloud Run, invoker-only; agent SA has run.invoker)
Authorization: Bearer <agent-SA-id-token>    (NOT the user's Firebase token — the agent is the caller)
body: { layer_uri: "gs://...", user_id: "<ULID>", case_id: "<ULID>", ttl_seconds: 3600 }
→ 200 { signed_url: "https://storage.googleapis.com/...&X-Goog-Signature=...", expires_at: "...Z" }
→ 403 if user_id does not own case_id (Persistence ownership lookup)
→ 400 if layer_uri is not a gs:// in an allowed bucket
```

**TTL policy:** 15-minute minimum, 60-minute maximum (per manifest job-0251), default 3600s. Reject out-of-range. COG download URLs use the longer end (60m) because a single MapLibre raster session may re-fetch; per-request validation uses the short end.

**SA + IAM (job-0251 owns `infra/signed_urls/`):**
- New SA `grace-2-signer` (mirror naming `infra/gcp.tf:72` `agent_runtime` / `infra/qgis-server.tf` `qgis_server`).
- The signer SA needs `roles/iam.serviceAccountTokenCreator` ON ITSELF (to call `signBlob` for V4 signed URLs without a downloaded private key — the keyless signing path; this is the NFR-S-2 "no plaintext key material" posture consistent with `secrets.tf`'s discipline).
- `roles/storage.objectViewer` is NOT needed by the signer (signed URLs delegate the *caller's* read; but for V4 `signBlob` the signing identity must be the SA whose key signs — the URL grants read as that SA, so the **signer SA needs `storage.objectViewer` on the runs + cog + fgb buckets** so the signed URL actually resolves). Scope to the specific buckets (`infra/buckets.tf` pattern, `:136-188`), NOT project-level (Invariant 5 tier separation).
- Buckets in scope: the COG/runs bucket (`grace-2-hazard-prod-cog` / runs) and the FGB bucket (`-fgb`). NOT the `.qgs` bucket (those are served via the QGIS proxy, §5, not via signed URL). NOT the cache bucket.
- Agent SA (`agent_runtime`) gets `roles/run.invoker` on `grace2-signer`.

**Audit log events for mints (Decision F + Decision M provenance):** every successful mint appends an `audit_log` event via `Persistence.append_audit("signed-url-mint", {user_id, case_id, layer_uri, bucket, ttl_seconds, expires_at})` (`persistence.append_audit:975-996`). The signed URL value itself is NOT logged (it is a bearer credential — Decision F wire-isolation discipline, same as the secret-value scrub). On a 403 ownership-reject, append `audit_log` `signed-url-denied`. This gives a tamper-evident record of who minted access to which layer.

### 4c. How the web client swaps `gs://`-derived WMS/COG access for signed URLs

**Two distinct rendering paths today — they need different treatment:**

1. **Raster layers (COG via QGIS Server WMS).** Today `LayerURI.wms_url` is built as a public QGIS Server URL (`publish_layer.py:_build_wms_url:297-308`, `DEFAULT_QGIS_SERVER_URL` = a public `run.app` URL). **These do NOT get a signed GCS URL** — the COG bytes never reach the browser directly; QGIS Server renders tiles. **The lockdown for these is the QGIS proxy (§5), not signed URLs.** Manifest OQ-4 confirms: "signed URLs cover GCS COG download links; WMS tile access is auth-gated via the QGIS proxy." So for raster WMS layers, job-0254's change is: rewrite the WMS base from the public QGIS `run.app` URL to the agent `/qgis-proxy?...` URL (job-0255 territory), not a signed URL.

2. **Direct COG/FGB download links + vector GeoJSON inlining.** Vector layers are currently inlined as GeoJSON read server-side from `gs://` (`pipeline_emitter.py:_read_vector_layer:277-303`, the job-0175 root-cause fix) — these never expose `gs://` to the client, so **vectors need no signed URL** (the bytes are already proxied through the agent as inline GeoJSON). The signed-URL path matters for any **direct COG download** the client offers (e.g. a "download this layer" affordance) and for any future client-side COG reader. **At v0.1 the load-bearing signed-URL consumer is narrow.** job-0254's `layer_uri_emit.py` is the single place that, in `SIGNED_URLS=true` mode, replaces a raw `gs://` in any client-facing field with a freshly-minted signed URL.

**Designed `layer_uri_emit.py` (NEW, job-0254 owns):** a single function `emit_layer_uri(layer: LayerURI, *, user_id, case_id, mode) -> LayerURI` that the pipeline emitter calls before sending a layer to the client (`pipeline_emitter.add_loaded_layer:620`). Behavior by `SIGNED_URLS` env:
- `SIGNED_URLS=false` (dev): pass through unchanged (current behavior — public QGIS URL for raster, inline GeoJSON for vector). Preserves local dev with no signer.
- `SIGNED_URLS=true` (prod): (a) raster `wms_url` → agent `/qgis-proxy` URL (no signed URL — proxy handles auth); (b) any client-exposed `gs://` (the `uri` field if it is ever surfaced for download) → mint a signed URL. The `uri` field's raw `gs://` should NOT reach the client in prod — strip it or replace with the signed URL.

**Critical scoping note for job-0254 (flag to runner):** the LayerURI `uri` field (`execution.py:129`) is `gs://...` and IS currently sent to the client (it is a model field, serialized in `session-state`). **In production this raw `gs://` leaks the bucket layout.** job-0254 must either (a) drop `uri` from the client-facing serialization in prod and rely on `wms_url` (proxy) for rendering, or (b) replace it with a signed URL. Recommendation: (a) — vectors render from inline GeoJSON and rasters from the proxy, so the client does not need the raw `gs://` at all. This is the cleanest "no raw gs:// in the client" posture the manifest goal demands.

**Hooks (cite for job-0254):**
- `publish_layer.py:_build_wms_url:297-308`, `_get_qgis_server_url:258-261`, `DEFAULT_QGIS_SERVER_URL:118`, `set_qgis_server_url:206`.
- `pipeline_emitter.py:add_loaded_layer:620-668`, `_read_vector_layer:277-303` (vector inline path).
- `execution.py:LayerURI:113-134` (the `uri` + `wms_url`... note: `wms_url` is NOT on the `LayerURI` contract — it is added downstream in the `ProjectLayerSummary` translation; verify in `pipeline_emitter.add_loaded_layer` + `web/src/contracts.ts:190`).
- Web consumer: `web/src/Map.tsx:77-87` (WMS base URL), `web/src/contracts.ts:190` (`wms_url` field).

**SRS amendment flagged:** §3.8 / Appendix A `map-command load-layer` + the LayerURI/`wms_url` field documentation should record that in production the `wms_url` is the agent proxy URL and raw `gs://` is not client-exposed. Narrow files: `docs/srs/03-functional-requirements.md` (§3.8 signed-URL emission) and/or `docs/srs/A-websocket-protocol.md` (load-layer args). Schema specialist proposes; user lands.

---

## 5. Deployment topology for 13.5

### 5a. Service placement

| Component | Target | Notes / constraints |
|---|---|---|
| **Agent (WebSocket)** | Cloud Run v2 service `grace2-agent-prod`, **min-instances=1** | See §5b WS-on-Cloud-Run constraints. Multi-container (MCP sidecar §5d). |
| **QGIS Server** | Cloud Run v2 `grace-2-qgis-server`, **flip to invoker-only** | Remove `allUsers roles/run.invoker` (`infra/qgis-server.tf` head comment lines describe the current public posture). job-0255. |
| **Signer** | Cloud Run v2 `grace2-signer` (`infra/signed_urls/`), invoker-only | §4. Agent SA has invoker. |
| **Web static** | **Firebase Hosting** (recommended, manifest OQ-1 tentative) | CDN-backed, cheapest, native Firebase Auth domain integration. `infra/firebase/` Tofu via `google_firebase_hosting_site` (`google-beta`). job-0256. |
| **Python sandbox** | Cloud Run Job (already exists, `infra/python-sandbox.tf`) | Cloud Logging result transport (job-0241). No 13.5 topology change. |
| **MODFLOW / SFINCS** | Cloud Run Jobs (exist) | Digest-pinned by job-0240. No 13.5 topology change. |

**Web hosting recommendation: Firebase Hosting.** Rationale beyond manifest OQ-1: the web client already depends on Firebase Auth, and Firebase Hosting auto-provisions an `*.web.app` / `*.firebaseapp.com` domain that is automatically an authorized OAuth redirect/auth domain for the Firebase Auth project — this removes a manual "add authorized domain" console step that a Cloud-Run-hosted web would require. Static React build (`web/` is Vite) is a pure CDN artifact; no server-side rendering need. **Escalate to user only if they want a custom domain now (§7).**

### 5b. WebSocket-on-Cloud-Run constraints (load-bearing for job-0257)

- **Request timeout:** Cloud Run's max request timeout caps a single WS connection's lifetime (default 5m, **max 60m** on Cloud Run; for a long agent session set timeout to the 3600s max). A WS that must outlive 60m needs client-side reconnect — the web `ws.ts` already has reconnect + sticky-anonymous re-bind, so this is tolerable. **Set `timeout=3600s` on the agent service.** Flag: a model run that streams for >60m on one socket will drop; the client reconnects and rehydrates from `case-open` (FR-MP-6). Acceptable for v0.1.
- **Session affinity:** Cloud Run supports session affinity (`--session-affinity`), but it is best-effort (cookie-based) and **WS state is per-connection in `SessionState`** — there is no cross-instance shared session, so affinity matters only for reconnect-to-same-instance optimizations. With **min-instances=1 + max low** for the demo, most traffic lands on one warm instance anyway. **Recommendation: enable session affinity AND min-instances=1**; do not rely on affinity for correctness (the `sessions` Mongo doc + `case-open` rehydration is the real durability path).
- **Concurrency:** Cloud Run default concurrency=80; WS connections each hold an instance slot for their lifetime. For a demo, concurrency can stay default; each WS is one of the 80 slots. The MCP sidecar (one per instance) is shared across that instance's WS connections — fine, since `Persistence` is a singleton per process and MCP calls are serialized through the stdio client.
- **HTTPS/WSS:** Cloud Run terminates TLS; `wss://` works natively over the `*.run.app` domain. No domain mapping needed unless a custom domain is chosen (§7).

### 5c. Env / secrets per service (job-0257 for agent; mirror for signer)

**`grace2-agent-prod` env (job-0257):**
| Var | Value | Source | Why |
|---|---|---|---|
| `AUTH_REQUIRED` | `true` | plain env | §2b — mandatory verification. |
| `SIGNED_URLS` | `true` | plain env | §4c — prod URL rewriting. |
| `MDB_MCP_READ_ONLY` | `false` | plain env | **MCP-3.** Without it, MCP server hides write tools → every Case/session/user write fails (`server.py:300-306` documents this; `init_persistence_from_env` `setdefault`s it but the deploy MUST set it explicitly so it is visible in the Tofu resource for the adversarial correctness lens). |
| `GRACE2_MONGO_MCP_STDIO` | `1` | plain env | Selects the live MCP sidecar path (`server.py:296`). |
| `MONGODB_MCP_SERVER_PIN` | `mongodb-mcp-server@<exact-version>` | plain env / startup script | **MCP-2 / OQ-0203-MCP-VERSION-PIN.** The npm server renamed its tool surface historically; pin + re-run `evidence/mcp_protocol_smoke.py` as a pre-deploy gate. |
| Atlas SRV | (read at runtime) | **Secret Manager** `mongodb-srv-*` | `secrets.tf:26-44` already provisions this; agent reads via `fetch_srv_from_secret_manager` (`server.py:308`). Prod needs a `mongodb-srv-prod` secret (job-0257 or job-0250 provisions; §7 — confirm prod Atlas cluster). |
| Firebase Admin creds | (ADC) | **Workload Identity** (the agent SA) | `auth_handshake.init_firebase_admin:88-95` uses `ApplicationDefault()` — no JSON key. The agent SA must be in the Firebase project + have token-verify permission. NO plaintext Firebase service-account key (NFR-S-2). |
| `QGIS_SERVER_URL` | the agent's own `/qgis-proxy` base | plain env | §5e — point publish_layer's WMS base at the proxy, not the public QGIS URL. |
| `GRACE2_MONGO_DB` | prod db name | plain env / Secret | `persistence.py:68` (`DEFAULT_DATABASE`). Prod uses a distinct db (e.g. `grace2_prod`) from dev's `grace2_dev`. |

**Secrets discipline (adversarial contract lens checks):** ALL secret *values* (Atlas SRV, any signing key if used) come from Secret Manager via Workload Identity — NO plaintext secret in the Cloud Run env. The env vars above are non-secret config flags (`AUTH_REQUIRED`, `SIGNED_URLS`, pins) — those are fine as plain env. The distinction: flags = plain env; credentials = Secret Manager. This matches `secrets.tf`'s stated discipline and the manifest job-0257 acceptance criterion.

**Agent SA IAM (job-0257, additive to `agent_runtime` in `infra/gcp.tf:72`):**
- Secret Manager accessor on `mongodb-srv-prod` (+ any per-deployment Tier-2 keys).
- `roles/run.invoker` on `grace2-signer` (§4) and on `grace-2-qgis-server` (the proxy needs to call the now-invoker-only QGIS).
- Cloud Workflows invoker (existing).
- GCS object access on runs + cache buckets (existing `cache_bucket.tf:160`).
- Cloud Run Jobs invoker on python-sandbox/modflow/sfincs (existing `python-sandbox.tf:447`).
- **Cloud Logging read** (`roles/logging.viewer` scoped) — sandbox result transport (job-0241 / SANDBOX-1).
- Firebase project membership / `roles/firebaseauth.admin` (or at minimum token-verify) for `verify_id_token`.

### 5d. MCP sidecar-in-Cloud-Run shape (FR-AS-4 OQ-2 resolution = sidecar container)

The agent currently launches `mongodb-mcp-server` as a **stdio subprocess** (`MCPClient.start`, `server.py:309`). For Cloud Run, OQ-2 resolves to a **multi-container Cloud Run service** (sidecar pattern). **But there is a shape decision the runner must get right:**

- **Option A (recommended): keep stdio, sidecar-in-same-container is NOT needed.** The stdio subprocess model means the MCP server is a child process of the agent process *inside the same container*. This is NOT a Cloud Run "sidecar container" — it is a subprocess. job-0241's PDEATHSIG + process-group fix (MCP-1) exists precisely to make this subprocess model safe in a container (no orphan leak across instances). **This is the simplest correct shape: single container, MCP as a managed subprocess, PDEATHSIG-guarded.** The `mongodb-mcp-server` npm binary must be baked into the agent image (a Node runtime layer in the agent Dockerfile).
- **Option B (the literal "sidecar container"): a second container in the Cloud Run service running `mongodb-mcp-server` over HTTP**, with the agent talking to it via `GRACE2_MONGO_MCP_URL` (`server.py:289` — reserved for "the HTTP MCP transport, not yet wired"). This requires the MCP server to run in HTTP/SSE transport mode (it supports it) and the agent's `MCPClient` to speak HTTP instead of stdio (NOT yet implemented — `server.py:292` explicitly says it "falls through to stdio").

**Recommendation: Option A (stdio subprocess + baked npm binary + PDEATHSIG), NOT a literal sidecar container, for v0.1.** Reasons: (1) the stdio path is the only one implemented and live-verified (job-0203 round-trip evidence); (2) the HTTP transport path (`GRACE2_MONGO_MCP_URL`) is explicitly un-wired; (3) MCP-1 (job-0241) already hardens the subprocess lifecycle for the container case. The literal multi-container sidecar (Option B) is more "cloud-native" but requires un-built HTTP-transport plumbing — **defer to a future job; do not block 13.5 on it.** Record this as the OQ-2 v0.1 resolution: **subprocess-in-container, not sidecar-container.** If the manifest's "multi-container Cloud Run service spec" is a hard requirement, flag the gap to the user (§7) — it adds an un-scoped HTTP-transport agent job.

**Multi-container spec (only if Option B is mandated):** Cloud Run v2 `containers` block with two entries — `agent` (ingress container, port 8080) and `mongo-mcp` (sidecar, no ingress, `mongodb-mcp-server --transport http --port 3000`), `agent` env `GRACE2_MONGO_MCP_URL=http://localhost:3000`. Shared loopback; the sidecar reads the Atlas SRV from the same Secret Manager mount. **This is the documented shape but NOT recommended for v0.1 given the un-wired transport.**

### 5e. QGIS Server lockdown topology (job-0255)

- Remove the `allUsers → roles/run.invoker` binding on `grace-2-qgis-server` (the public posture in the `infra/qgis-server.tf` header comment). Grant `roles/run.invoker` to the agent SA only.
- The agent hosts `GET /qgis-proxy?{WMS_params}` (`server.py` new route + `services/workers/qgis_proxy.py` new) that attaches the agent SA's identity token and forwards to QGIS Server, streaming the tile response back. **Stream, do not buffer** (manifest contract lens). Strip any user-identity header before forwarding (no UID leak to QGIS Server).
- Web `Map.tsx:77-87` WMS base flips from the public QGIS `run.app` URL to `wss/https://<agent>/qgis-proxy?...` (set via `VITE_GRACE2_WMS_URL` at build, job-0256 web env).

### 5f. SESSIONS_TTL (MCP-4) — surface, do not decide (see §7)

The effective retention is 60 days (`touch_session` sets `expires_at=now+30d`, `persistence.py:624`; the Mongo TTL index adds another 30d per `collections.py` SESSIONS_TTL). This is a **user sign-off gate before Stage 1** per the manifest. Carried to §7.

---

## 6. Job decomposition (paste-ready into kickoffs)

All Stage-1/2/3 jobs carry an adversarial-verify gate (zero-exception rule). IDs match the manifest. File ownership reflects the §1-5 reconciliations.

| Job ID | Title | Specialist | Model | Est tokens | Depends-on | Adv gate | File ownership |
|---|---|---|---|---|---|---|---|
| **job-0241-agent** | MCPClient PDEATHSIG + process-group; sandbox Cloud Logging result transport | agent | sonnet | 80K | sprint-13 Stage 2 | no | `services/agent/src/grace2_agent/mcp.py` (PDEATHSIG only), `sandbox_runner.py` (Cloud Logging read only) |
| **job-0249-schema** (NEW — pull out of job-0252) | `CaseSummary.owner_user_id` contract field + A.6 auth error codes + D.2 owner_user_id row (SRS proposals) | schema | opus | 90K | sprint-13 close | YES (contract lens) | `packages/contracts/src/grace2_contracts/case.py`; SRS proposals only for `docs/srs/A-`, `docs/srs/D-`, `docs/srs/H-` |
| **job-0250-infra** | Firebase/Identity Platform prod project: `google_identity_platform_config` (email+anon) + `..._default_supported_idp_config` (google.com) + authorized domains + prod Firestore deny-all rules + agent SA Firebase membership | infra | opus | 220K | sprint-13 close + job-0241 | YES | `infra/firebase/` (new), `infra/providers.tf` (add google-beta — additive) |
| **job-0251-infra** | `grace2-signer` Cloud Run service + signer SA (`iam.serviceAccountTokenCreator` self + `storage.objectViewer` on cog/fgb buckets) + agent `run.invoker` binding + mint/deny audit | infra | opus | 200K | sprint-13 close + job-0241 + job-0249 (Persistence ownership seam) | YES | `infra/signed_urls/` (new — service source + Tofu) |
| **job-0252-agent** | `auth.py` policy layer (AUTH_REQUIRED gate, resolve-or-reject) + OQ-0115 ownership write/enforce + sentinel backfill + anon-ULID→authenticated case migration + token-refresh/AUTH_TOKEN_EXPIRED close | agent | opus | 230K | sprint-13 close + job-0241 + job-0249 | YES | `services/agent/src/grace2_agent/auth.py` (new), `server.py` (additive: AUTH_REQUIRED wiring, owner-on-create, list-by-authenticated-uid, backfill-on-startup), `persistence.py` (stop stripping owner_user_id; strict list query; optional requesting_user_id on get_case) |
| **job-0253-web** | Harden existing web auth: AuthGate prod path (Google required when AUTH_REQUIRED), drop dev anonymous-only affordance in prod build, clear sticky-ULID on `auth-ack(is_anonymous=false)`, surface AUTH_FAILED/AUTH_TOKEN_EXPIRED | web | opus | 130K | sprint-13 close | YES | `web/src/auth.ts`, `web/src/ws.ts` (additive), `web/src/components/AuthGate.tsx`, `web/src/App.tsx` (guard wiring) |
| **job-0254-agent** | `layer_uri_emit.py`: SIGNED_URLS mode — raster wms_url→proxy URL, drop raw gs:// from client serialization, mint signed URL for any client COG download; wire into pipeline_emitter | agent | opus | 200K | job-0251, job-0252 | YES | `services/agent/src/grace2_agent/layer_uri_emit.py` (new), `pipeline_emitter.py` (call emit_layer_uri), `publish_layer.py` (WMS base via QGIS_SERVER_URL→proxy) |
| **job-0255-infra** | QGIS Server invoker-only flip + agent `/qgis-proxy` streaming route + strip-creds-before-forward | infra | opus | 200K | job-0252 | YES | `infra/qgis-server.tf` (IAM), `services/workers/qgis_proxy.py` (new), `server.py` (proxy route — additive) |
| **job-0256-infra** | Web Firebase Hosting deploy + prod VITE_* env (FIREBASE_CONFIG, WS_URL, WMS_URL=proxy) | infra | opus | 200K | job-0253, job-0255 | YES | `infra/firebase/` (hosting), `web/firebase.json` / `web/cloudbuild.yaml` (new) |
| **job-0257-infra** | `grace2-agent-prod` Cloud Run: AUTH_REQUIRED=true, SIGNED_URLS=true, MDB_MCP_READ_ONLY=false, MONGODB_MCP_SERVER_PIN, MCP subprocess+PDEATHSIG image, secrets via Workload Identity, min-instances=1, timeout=3600s, smoke gate | infra | opus | 250K | job-0241, job-0252, job-0254, job-0255, job-0240 | YES (MDB_MCP_READ_ONLY + pin explicit) | `infra/main.tf` / agent Cloud Run resource (new), agent Dockerfile (Node+npm MCP bake) |
| **job-0258-web** | Onboarding polish: first-login modal + sample Fort Myers Case + tier-aware tool greyout | web | sonnet | 150K | job-0253 | no | `web/src/components/Onboarding.tsx`, `WelcomeModal.tsx` (new) |
| **job-0259-testing** | Prod E2E: sign-in → Case → flood model → signed/proxy render → QGIS 403 → expiry → onboarding → pre-Auth isolation → P5 conditional | testing | opus | 300K | job-0256, job-0257, job-0258 | YES (final gate) | test artifacts only |
| **job-0260-testing** | Close + concurrent sprint-13 verify + sprint-14 stub + PROJECT_STATE update | testing | opus | 100K | job-0259 panel | no | reports + PROJECT_STATE |

**Key decomposition delta vs manifest:** added **job-0249-schema** (~90K) to pull the `CaseSummary.owner_user_id` contract change + A.6 code amendments OUT of job-0252 — a contract change must go through the schema specialist + contract-lens gate, not be buried in an agent job. job-0252 then depends on job-0249 (the field must exist before the agent writes it). This adds ~90K + one panel (~200K) to the budget but is the correct ownership seam (schema owns contracts; agent consumes). Net new vs manifest ~290K.

**Sequencing within Stage 1:** job-0249 (schema contract) is a fast prereq that job-0250/0251/0253 do NOT depend on, but job-0252 + job-0254 DO. Run job-0249 first (cheap), then the four Stage-1 jobs fan out (0250/0251/0252/0253) with 0252 gated on 0249.

---

## 7. Decision items for the user (collect, do not decide)

1. **SESSIONS_TTL retention (MCP-4 — DECISION GATE, blocks Stage 1).** Effective 60-day retention after last activity (`expires_at=now+30d` per `touch_session` + 30-day Mongo TTL index). Confirm 60d, or choose a shorter horizon (e.g. 30d = 15d+15d), or add a "remember this device" toggle. **Requires interactive sign-off before job-0250 dispatches.**
2. **Production domain / DNS.** Default = Firebase Hosting `*.web.app` auto-domain (also auto-authorized for OAuth). If a custom domain is wanted, it adds a domain-mapping + DNS-verification + OAuth-authorized-domain step (interactive console + DNS records — the user's step). TENTATIVE: Firebase default domain; custom domain is a follow-up job.
3. **OAuth consent screen branding.** Google sign-in shows a consent screen with the project's app name + support email + logo. The consent screen config is a **manual GCP console step** (cannot be fully Tofu'd) and for production (non-`@gmail.com` external users) may require Google verification. Confirm app name / support email / logo, and whether the app stays in "testing" (limited to allow-listed test users — fine for the demo) or needs "published" status (Google review). **For the demo, recommend "testing" mode with the user's test accounts allow-listed — no Google review needed.** Interactive console step either way.
4. **Billing tier for Identity Platform.** Enabling Identity Platform (the `google_identity_platform_config` flip) moves the project from free Firebase Auth to Identity Platform pricing (free tier: 50k MAU, then per-MAU). For a demo this is free-tier. Confirm acceptance of the Identity Platform pricing model (it is a billing-account-attached service — `gcp.tf` already has billing linked, but enabling the API is a deliberate choice).
5. **Production Atlas cluster + `mongodb-srv-prod` secret.** Is the prod deploy reusing the existing dev Atlas Flex cluster (with `GRACE2_MONGO_DB=grace2_prod` for isolation) or a separate prod cluster? `secrets.tf` provisions `mongodb-srv-dev`; a `mongodb-srv-prod` secret + prod DB user is needed. **Interactive Atlas CLI auth is the user's step** (per CLAUDE.md machine-state rule).
6. **MCP sidecar shape (OQ-2 confirmation).** §5d recommends subprocess-in-container (Option A) over a literal multi-container sidecar (Option B, requires un-wired HTTP transport). Confirm Option A is acceptable for v0.1, OR direct that the literal sidecar-container is required (adds an un-scoped agent HTTP-transport job).
7. **Orphan pre-Auth Case disposition (OQ-0115).** §3 recommends the `MIGRATION_ANON_UID` sentinel (invisible-to-all, admin-recoverable) over assign-to-first-user. Confirm, or direct otherwise. (Agent-recommendable but it touches data the user may have opinions on — surfacing per the "inform before persistent action" rule.)
8. **`gcloud auth application-default login` on the deploy box.** job-0250/0257 Tofu applies + the agent's prod ADC need gcloud auth on whatever machine runs the deploy. Interactive — the user's step (CLAUDE.md).
9. **Anonymous sign-in in production: keep or drop?** §1/§2 keep Firebase-anonymous sign-in accepted in prod (verified uid, upgradeable). The alternative is a hard sign-in wall (no anonymous). H.3's landing-UX rule wants anonymous-first. Confirm anonymous-first is the intended prod UX (recommended), or direct a hard wall.

---

## Cross-cutting SRS amendment proposals (consolidated — specialists propose, user lands)

| Amendment | Narrow file | Owner | Trigger |
|---|---|---|---|
| Add `AUTH_TOKEN_INVALID`, `AUTH_TOKEN_EXPIRED`, `TIER_INSUFFICIENT` to A.6 error-code table | `docs/srs/A-websocket-protocol.md` (§A.6 ~line 616) | schema (job-0249) | code already emits `AUTH_TOKEN_INVALID` against an undocumented code (`server.py:1117`) |
| Record `owner_user_id` as a persisted `ProjectDocument`/D.2 field | `docs/srs/D-mongodb-collection-schemas.md` (§D.2) | schema (job-0249) | OQ-0115 closure makes it a written field |
| Note v0.1 provisioning goes through Identity Platform resources (H.1.1) | `docs/srs/H-auth-and-users.md` (H.1) | schema (job-0250 author proposes) | clarifies the Tofu surface choice |
| Record prod `wms_url`=agent proxy + no client-exposed raw `gs://` (§3.8) | `docs/srs/03-functional-requirements.md` (§3.8) and/or `docs/srs/A-websocket-protocol.md` (load-layer) | schema (job-0254 author proposes) | signed-URL emission + QGIS lockdown |
| Record OQ-2 v0.1 resolution = MCP subprocess-in-container (not sidecar-container) | `docs/srs/03-functional-requirements.md` (FR-AS-4) | schema (job-0257 author proposes) | §5d decision |

**Contract change (code, not SRS) gated through schema specialist:** `CaseSummary.owner_user_id` field (`packages/contracts/src/grace2_contracts/case.py`) — job-0249, adversarial contract lens.
