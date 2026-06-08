## Appendix H: Authentication and Users

> *(Forward-looking — v0.3.22 amendment. **Decoupled appendix** per user direction 2026-06-08 ("decoupled enough to be an appendix"). v0.1 operates with anonymous-friendly UX; Firebase Auth integration lands in Wave 2 of sprint-12-mega and downstream M6+ identity work. This appendix pins the identity-provider choice, the user→Case ownership rule, the anonymous→authenticated upgrade flow, the tier-claim discipline, the session-validation contract, the secrets scoping rule, and the architectural decision behind the choice. Implementation refinements (Wave 2 schema fields, agent verification middleware, web UX, Identity Platform IaC) land in the binding sprints; this appendix pins the contract.)*

**Where Appendix H sits.** Appendix H is **decoupled** from the operational MongoDB substrate (Appendix D), the WebSocket protocol (Appendix A), and the data-source / secrets UX appendix (Appendix F §F.3). Identity is a transverse concern — every persisted document carries an `owner_user_id`, every WebSocket connect verifies a Firebase ID token, every per-user secret namespace keys off the `users._id`. Rather than scattering identity rules across A / D / F, this appendix is the single authoritative reference.

**Naming convention.** UI label "**User**"; storage collection name `users` (per the FR-MP-5 / FR-MP-6 nomenclature pattern where UI labels can diverge from storage labels — e.g. "Case" ↔ `projects`). The two are interchangeable in narrative; code says `User` / `UserDocument`.

---

### H.1 Identity provider choice — Firebase Authentication (managed SaaS, GCP-native)

**Selection.** GRACE-2 uses **Firebase Authentication** as the identity provider, with **GCP Identity Platform** as the enterprise-SKU upgrade path. Firebase Auth + Identity Platform is the same product family — Identity Platform is the renamed/extended SKU that adds enterprise SLA, multi-tenancy, customer-managed encryption keys (CMEK), SAML/OIDC enterprise SSO, and audit logs to the base Firebase Auth surface. A v0.1 Firebase Auth project can be upgraded to Identity Platform without re-keying users, re-issuing tokens, or changing client SDK calls — the upgrade is a project-level configuration flip in GCP console.

**What Firebase Auth provides (v0.1 surface).**

- **Email/password sign-in** — primary v0.1 authenticated mode.
- **OAuth sign-in providers** — Google, GitHub, Microsoft, Apple (zero-config; managed by Firebase).
- **Anonymous sign-in** — Firebase issues a stable anonymous user ID + ID token without requiring credentials. v0.1 landing page UX uses this so users can begin a Case without an account (see H.3).
- **Email-link sign-in** (magic-link) — passwordless flow; useful for the anonymous→authenticated upgrade.
- **ID tokens (JWT)** — short-lived (1 hour) signed JWTs the client passes to the agent on WebSocket connect; the agent verifies them via the `firebase_admin` Python SDK (see H.5).
- **Custom claims** — arbitrary key/value pairs the backend writes onto a user's ID token via the Admin SDK (`firebase_admin.auth.set_custom_user_claims`); used for tier gating (see H.4).
- **Account linking** — `linkWithCredential(...)` on the client takes an anonymous-session user and binds it to an email/OAuth credential without losing the original `uid`; this preserves Case ownership through the upgrade (see H.3).

**Why Firebase Auth over alternatives.**

1. **GCP-native, no separate identity vendor.** Decision E (§2.1) pins GRACE-2 on Google Cloud throughout; Firebase Auth lives inside the same GCP project, billing account, and IAM surface as Cloud Run / Workflows / Atlas-managed-by-MongoDB-via-GCP. No additional vendor onboarding, no separate billing setup, no separate SDK key management. Alternatives (Auth0, AWS Cognito) would each introduce a separate vendor, a separate auth-server domain, and cross-cloud network paths (Cognito particularly: AWS → GCP bridge).
2. **Anonymous-friendly by construction.** Firebase Auth's anonymous-sign-in + `linkWithCredential` upgrade flow is the cleanest fit for the v0.1 landing-page UX where users start a Case without an account (Memory rule: "inform user before persistent action" — anonymous → upgrade prompt at first save/share). Auth0 and Cognito both support anonymous flows but with more friction (Cognito requires Identity Pool; Auth0 requires custom JWT issuer).
3. **Custom-claims for tier scoping without contract changes.** Identity Platform's custom-claims surface is the native primitive for free/pro/enterprise tier gating (H.4). Tier checks run on the agent side against the verified JWT — no separate "billing" service required for v0.1.
4. **Scales to Identity Platform enterprise SKU without re-keying.** Future enterprise customers who need SLA, CMEK, SAML SSO, or audit logs get those by flipping the GCP project from Firebase Auth → Identity Platform. No data migration, no token re-issuance, no contract change. Auth0 / Cognito both require sales engagement + tier upgrades for equivalent surfaces, and Auth0's enterprise tier is significantly more expensive at v0.2 scale.
5. **Decoupled from compute.** Firebase Auth is a managed service; outage on Firebase Auth does not crash the agent, the QGIS Server, or the SFINCS engine. Only login + token-issuance is affected; existing sessions with valid ID tokens continue until token expiry (1 hour default).

**Why not the alternatives (one-line rationale each).**

- **Auth0** — separate vendor, separate billing, separate token-issuer domain; identity becomes a cross-cloud dependency. Strong product, wrong cloud-vendor seam for GCP-pinned GRACE-2.
- **AWS Cognito (via cross-cloud bridge)** — adds AWS-account onboarding, Cognito Identity Pool complexity, IAM federation across clouds; identity becomes the most operationally complex thing in the stack.
- **Custom OIDC server (Keycloak / Dex / Ory)** — self-hosted; SLA, key rotation, abuse mitigation, brute-force protection, SMTP-for-password-reset all become GRACE-2's problem. Indefensible v0.1 scope creep.
- **No-auth-at-v0.1** — feasible only with NO persistent UX; the moment Cases land (sprint-12-mega), single-owner persistence demands a stable user identity. Anonymous Firebase users satisfy this at zero credential friction.

### H.2 User → Case ownership

**Ownership rule.** Every Case (a `projects` document per FR-MP-5 / FR-MP-6) carries an `owner_user_id` field — a ULID that points at a `users._id`. The owner is set at Case creation time (FR-MP-6) and is **immutable for v0.1** — ownership transfer is a Wave 2+ feature (deferred; see "Future" below).

**Enforcement seam.** The MongoDB MCP layer (FR-MP-1 — agent's persistence interface) enforces the ownership filter at query time: `list_cases_for_user(user_id)` returns only `projects` where `owner_user_id == user_id`, and `get_case(case_id, requesting_user_id)` returns the document only if its `owner_user_id` matches. The enforcement lives in the MCP server's tool implementations, not in the agent's prompt — a misbehaving LLM cannot accidentally return another user's Case because the underlying tool refuses the query.

**Cascade scope.** A Case's `owner_user_id` cascades to every artifact rooted at that Case: the `sessions` document(s) bound to the Case (D.6), the `runs` documents produced inside it (D.3), the `events` documents (D.4) authored from a Case-scoped Hazard Event Pipeline invocation, and the `layers` documents (D.2 ProjectLayerSummary subdocuments). The ownership query in MCP is a single field check at the Case root; descendant documents inherit by reference.

**Anonymous Case ownership.** Anonymous users (per H.3) have a stable Firebase `uid` and a `users._id` ULID just like authenticated users — they can own Cases. The H.3 upgrade flow preserves the `uid` so anonymous-Case ownership survives the upgrade.

**Collaborators (v0.1 deferred).** Multi-owner / shared Cases via an explicit `case_collaborators[]` list are **deferred** — v0.1 is single-owner. Stakeholder discussion: pre-MVP scope (AGENTS.md "Pre-MVP scope, no legacy support") + Decision K ("user supplies intent and irreducible inputs") together argue that the single-owner shape is the simpler intermediate; collaboration is a v0.2+ shape change. When collaboration lands, the `projects` schema gains a `case_collaborators: list[CaseCollaboratorEntry]` field where each entry pairs a `user_id` with a permission level (read / write / admin); the MCP enforcement layer changes from a single-field equality to a membership check. Adopting this shape is additive — single-owner Cases remain valid because an empty `case_collaborators[]` is the v0.1 default.

**Future capabilities (deferred from v0.1, recorded for traceability):**

- Ownership transfer (e.g. "transfer this Case to user@example.com") — requires a confirmation modal pattern (FR-AS-8) and a notification to the receiving user.
- Shared Cases with per-collaborator permissions (above).
- Case "publish" mode — Case made public/read-only-link-shareable without account-to-account binding; requires Decision M provenance discipline + a public-Case audit log.
- Organization-scoped Cases (Identity Platform enterprise SKU) — a Case belongs to an org rather than a user; orgs have their own admin / member / billing model.

### H.3 Anonymous → authenticated upgrade

**Landing UX rule.** A user visiting GRACE-2 without any prior session is immediately signed in as a **Firebase anonymous user** — no login wall, no friction. They can:

- Chat with the agent
- Create a Case (becomes `owner_user_id = <anonymous-uid>`)
- Run modeled / discovered / impact workflows inside the Case
- Save layers into the Case's `layer_summary` (sprint-09 `publish_layer` substrate)

**Upgrade trigger.** The UX presents an inline "Save your account" / "Sign in to keep this Case" prompt at any of these moments:

1. First time the user attempts to **share** a Case (when sharing lands per H.2 deferred list).
2. First time the user attempts a **destructive** action that benefits from named-attribution (e.g. publishing a Case to a public hazard-event reference).
3. After a user explicitly clicks a "Sign in" / "Create account" UI affordance.
4. On Case re-open after a long absence (TTL threshold; surfaces the "keep this account around" prompt before the anonymous session expires — Firebase default anonymous-session expiry is provider-configurable, currently set to "never expire" for v0.1 but a 30-day-since-last-active expiry is a reasonable Wave 2+ default).

**The upgrade flow** uses Firebase's `linkWithCredential(...)` client SDK call:

1. Client UI prompts user for an email + password (or OAuth provider button).
2. Client calls `auth.currentUser.linkWithCredential(emailAuthProvider.credential(email, password))` (or the OAuth equivalent).
3. Firebase Auth atomically links the credential to the existing anonymous `uid` — no `uid` change.
4. Client emits a new `user-authenticated` envelope (Appendix A amendment in the Wave 2 schema sprint) to the agent so the agent can update its in-session state from "anonymous" → "authenticated".
5. Agent updates the corresponding `UserDocument` to set the credential metadata (email, provider, linked-at timestamp); the `_id` (ULID) and `firebase_uid` are unchanged.

**Memory rule satisfaction.** The user is informed before the persistent-account binding lands: the upgrade is always user-initiated (clicking a "Sign in" button, accepting the inline prompt), never silent. This satisfies the orchestrator-codified rule "user is informed before persistent action."

**Failure modes.**

- **Email already exists for a different account** — `linkWithCredential` rejects with `credential-already-in-use`; UI surfaces "this email is already registered; sign in to that account instead, or use a different email." Anonymous Case stays bound to anonymous `uid`; no data loss.
- **OAuth-provider conflict** (same email registered with different provider) — surfaces "this email is registered with <provider>; sign in with that provider instead." User can then sign in with the existing account and manually move the anonymous Case (Wave 2 feature: anonymous-Case import on first authenticated sign-in within N hours of the anonymous session).
- **Network failure mid-upgrade** — `linkWithCredential` is atomic; either succeeds entirely or leaves the anonymous user unchanged.

### H.4 Custom claims for tier gating (free / pro / enterprise)

**Claim shape.** Each user's Firebase ID token carries a `tier` custom claim, set via Identity Platform Admin SDK:

```
{
  "tier": "free" | "pro" | "enterprise"
}
```

**v0.1 default.** All users are `tier: "free"` at provisioning time. Tier upgrade machinery is **deferred** until the v0.2+ commercial track lands; v0.1 has no paid plan, no payment integration, and no tier-bumping admin UI.

**Why custom claims and not a Mongo field.** Tier is read by the agent on every WebSocket connect to gate which workflows / atomic tools the user can invoke. Reading from the verified JWT is O(1) on the agent side — no Mongo query, no cache invalidation race. The Mongo `UserDocument.tier` field is the durable mirror (write-on-tier-change, read-as-truth-on-token-mint); the JWT claim is the operational read-path.

**Gating discipline.** Tier-gated workflows / atomic tools enumerate their required tier in their FR-AS-3 / FR-TA-3 docstring metadata. The agent's tool-routing layer checks the request user's tier claim against the tool's required tier at dispatch time; mismatch returns a `TIER_INSUFFICIENT` error (new Appendix A.6 SCREAMING_SNAKE_CASE code, lands when the first tier-gated tool lands). v0.1: zero tier-gated tools — every workflow / atomic tool is free-tier-accessible. The machinery is in place; the gates are not yet armed.

**Why this matters now (even with no paid tier).** Pinning the tier claim shape now means a v0.2+ pro-tier flip does not require a JWT-shape change, a client-SDK upgrade, or a contract revision. The cost of adding `"tier": "free"` to v0.1 tokens is zero; the cost of retrofitting the claim later would be a coordinated agent + web + admin-tooling change.

**Future enterprise expansion (Identity Platform SKU).** The enterprise SKU surfaces additional claims:

- `organization_id` — for Cases that belong to an org rather than a user (H.2 future).
- `roles[]` — for enterprise role-based access control (admin / analyst / viewer within an org).
- `permissions[]` — for fine-grained capability flags.

None of these are v0.1 scope; they land as Identity Platform-SKU upgrades when the first enterprise customer lands.

### H.5 Session validation — agent-side token verification

**Connection flow.** Per Appendix A.5 (Connection Lifecycle), the WebSocket connect handshake carries the Firebase ID token as a connection-level credential (proposed mechanism: `Sec-WebSocket-Protocol` subprotocol header, or `Authorization: Bearer <id_token>` upgrade header — exact mechanism is a Wave 2 schema decision; this appendix pins **that** verification happens, not **how**). The agent's connection-acceptor:

1. Reads the ID token from the connect frame.
2. Calls `firebase_admin.auth.verify_id_token(id_token, check_revoked=True)` — the Admin SDK validates signature against Firebase's rotating JWKS, checks expiry, checks revocation list, and returns the decoded claims (including `uid`, `email`, `tier`).
3. Resolves the Firebase `uid` to the corresponding `UserDocument._id` via the `Persistence.get_user_by_firebase_uid(firebase_uid)` interface (lands in job-0115; the FR-MP-1 Persistence contract). If no `UserDocument` exists for the `uid`, the resolver creates one (auto-provision on first authenticated connect) with default fields (`tier="free"`, anonymous-flag mirrored from the JWT claim, provider metadata from the JWT claims).
4. Binds the resolved `User._id` into the agent's session context as the active user; every subsequent tool call, MCP query, and Case binding flows through that user.

**Token refresh.** Firebase ID tokens expire after 1 hour. The client SDK automatically refreshes them via the refresh token (handled by `firebase/auth` SDK transparently). When the agent's connection-acceptor receives a refreshed token mid-session (proposed mechanism: `token-refresh` envelope in Appendix A amendment; deferred to Wave 2 schema sprint), it re-runs `verify_id_token` and updates the in-session JWT cache. If the refresh fails (token expired and refresh token revoked), the agent closes the WebSocket with the `AUTH_TOKEN_EXPIRED` error code (new Appendix A.6 SCREAMING_SNAKE_CASE code).

**Revocation.** Firebase Auth supports user-token revocation via the Admin SDK (`auth.revoke_refresh_tokens(uid)`). On revocation, the next agent-side `verify_id_token` call with `check_revoked=True` fails; agent closes the session. v0.1 use cases: account deletion (covered below), security incident (operator-initiated mass revocation).

**Account deletion.** When a user requests account deletion, the agent (or an admin tool) calls `firebase_admin.auth.delete_user(uid)` to remove the Firebase Auth record AND marks the corresponding `UserDocument` as `deleted_at: <timestamp>` (soft-delete tombstone). The owned Cases retain `owner_user_id` pointing at the deleted user (preserved for audit trail); the MCP enforcement layer treats a soft-deleted user's Cases as inaccessible by anyone (no inheritance to a "next owner"; the Cases are tombstoned, recoverable only by admin tool).

**Why verification is agent-side, not MongoDB-side.** Decision F (MongoDB Atlas as durable knowledge layer) pins Mongo as durable storage; the operational read-path verification of credentials lives in the agent. MongoDB Atlas does support its own user/role/connection model for the database itself (D.3 IAM rules), but that is for the **agent's worker connection** to Atlas, not for end-user identity. The agent is the gate; Mongo is the substrate. (Equivalently: Atlas authentication is "is the agent allowed to talk to the database?"; Firebase Auth is "is the human allowed to use the agent?".)

### H.6 Secrets scoping — per-user vs per-Case vs deployment

**Scope hierarchy.** The `SecretRecord` schema (Appendix F §F.3 deferred substrate, lands when the per-user secrets UX lands) carries two scope fields:

- `user_id: ULIDStr` — the `User._id` the secret belongs to. **Required.**
- `case_id: ULIDStr | None` — the `Case._id` the secret is scoped to, OR `None` for user-wide secrets that cross Cases.

**Per-Case secrets** (`case_id` set) — used when the user provisions a credential that's only relevant inside one Case. Examples (Wave 2+):

- A user provides an API key for a private data source only for analysis in a specific Case (e.g. a research-collaboration NDA-bound data feed).
- A user provides an SFTP path with credentials for a specific dataset uploaded for one Case.

**Per-user secrets** (`case_id = None`) — used when the credential is the user's general-purpose key, applicable across all of the user's Cases. v0.1 Tier-2 examples (per Appendix F §F.1):

- **eBird API key** — user provisions once, used across all conservation/biodiversity Cases.
- **IUCN Red List API key** — same pattern.
- **Movebank credentials** — same pattern.
- **Census ACS key** (if the user prefers their own provisioning instead of deployment-scope) — same pattern.
- Other Tier-2 keys that a user might want personally scoped (NewsAPI, Earthdata Login, NOAA api.weather.gov keyed endpoints).

**Deployment-scope secrets (existing v0.1 substrate).** Until §F.3 lands and per-user secrets are operationally provisioned, Tier-2 keys are deployment-scope (operator-provisioned via OpenTofu + Secret Manager — one Census key per deployment, shared by all users). Deployment-scope provisioning remains as a fallback even after per-user provisioning lands; per-user provisioning is the preferred path when a user has their own key.

**Storage substrate** (deferred per §F.3; Identity-Platform-prerequisite). Per-user secrets live in **GCP Secret Manager** with the secret name `users/<user_id>/secrets/<secret_name>` or `users/<user_id>/cases/<case_id>/secrets/<secret_name>` (per-Case). The `users/` prefix gives a clean IAM boundary: a per-user Secret Manager binding scopes who can read; admin-tool reads are auditable; cross-user reads are forbidden by IAM (not just by application logic).

**Wire-level isolation (preserves Decision F discipline).** Per §F.3 the secret never transits the chat envelope to MongoDB. The agent receives a `secret-response` envelope from the client (out-of-band of the chat WebSocket per F.3 design), the secret value goes directly into Secret Manager via the Cloud Function secret-receiver, and only the `secret_name` reference (e.g. `ebird_api_key`) appears in any persisted document. The agent reads the secret value at tool-invocation time via Secret Manager IAM-scoped read.

**Storage in `UserDocument`.** The `UserDocument` does **not** store the secret values themselves — only the `secret_names` list (which secrets this user has provisioned, scoped per-user or per-Case). This list is metadata; the values live exclusively in Secret Manager.

**Why secrets care about Auth.** Without H.1's stable user identity, per-user secrets cannot exist — anonymous-session secrets would be re-prompted every session and have no durable identity to bind to. The §F.3 deferred-indefinitely status is exactly because §F.3 depends on H.1 + H.5 to be operationally landed; this appendix unblocks the per-user secrets substrate at the architectural level.

### H.7 Decision P — Firebase Authentication over alternatives *(numbered as the next available Decision letter after A–O)*

**Note on numbering.** The job-0116 kickoff text referenced "Decision E" as a placeholder; Decision E is already taken by "Google Cloud throughout" (§2.1). The next available Decision letter is **P** (A–O are claimed). This amendment records the Auth decision as **Decision P**; the §2.1 Decisions list gains this row when the user lands the amendment.

**Decision P: Firebase Authentication (Identity Platform) as the GRACE-2 identity provider.** *(Forward-looking — not in M1 / not in sprint-03; binding from Wave 2 of sprint-12-mega when the first authenticated-user flows land. Same discipline as Decisions N / O — deferred until the relevant capability lands.)*

GRACE-2 uses Firebase Authentication as the v0.1 identity provider, with GCP Identity Platform as the enterprise-SKU upgrade path. Selection rationale: GCP-native (Decision E alignment), anonymous-friendly with `linkWithCredential` upgrade preserving Case ownership through anonymous → authenticated transitions (H.3), custom-claims surface for tier gating without contract revision (H.4), scales to enterprise SKU (SLA / CMEK / SAML / audit logs) without re-keying users or re-issuing tokens, and decoupled from compute so identity-vendor outages don't crash the agent. Alternatives considered and rejected: Auth0 (cross-cloud vendor seam — wrong for GCP-pinned GRACE-2), AWS Cognito via cross-cloud bridge (operationally complex — IAM federation overhead), self-hosted OIDC (Keycloak / Dex / Ory — SLA + abuse-mitigation + key rotation become GRACE-2's problem, indefensible v0.1 scope), no-auth-at-v0.1 (infeasible once Cases require single-owner persistence at sprint-12-mega Wave 2). The user→Case ownership rule (H.2), the anonymous-upgrade flow (H.3), the tier-claim discipline (H.4), the agent-side session validation (H.5), and the per-user secrets scoping (H.6) all derive from this selection.

**Cross-references.**

- Decision E (GCP throughout) — Firebase Auth is the GCP-native realization of identity.
- Decision F (MongoDB Atlas durable knowledge layer) — `UserDocument` (Wave 2 D.x amendment) is the durable mirror of Firebase-managed identity; Firebase remains the authority for credentials.
- Decision K (user supplies intent and irreducible inputs) — user identity is an irreducible input; Firebase Auth provides it with minimum credential friction (anonymous default).
- Decision M (multi-source claim aggregation with provenance) — provenance attribution at the Case level requires a stable owner identity, which Firebase Auth supplies.
- Invariant 9 (no cost theater) — no cost field anywhere on Auth envelopes; tier is a capability claim, not a cost surface.
- Appendix A.5 (Connection Lifecycle) — Wave 2 amendment adds the ID-token-on-connect handshake.
- Appendix A.6 — Wave 2 amendment adds `AUTH_TOKEN_EXPIRED`, `AUTH_TOKEN_INVALID`, `TIER_INSUFFICIENT` SCREAMING_SNAKE_CASE error codes.
- Appendix D — Wave 2 amendment adds `users` collection (D.x) with `UserDocument` shape (`_id`, `firebase_uid`, `email`, `provider`, `tier`, `is_anonymous`, `created_at`, `last_seen_at`, `deleted_at`, `secret_names`).
- Appendix F §F.3 (Deferred Secrets UX) — H.6's per-user secret scoping is the architectural prerequisite §F.3 calls "M6+ user-identity machinery."

---
