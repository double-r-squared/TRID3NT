# Sprint-13.5 decision record — decided by orchestrator at user delegation

**Authority:** user 2026-06-11: "Make your best call for the decisions I'm
going to be afk think through decisions and make the best call." Plus the
standing quota constraint: "don't exhaust google ai limit like before so we
can actually demo" — ALL 13.5 jobs and panels run Gemini-FREE; live-turn
acceptance steps are deferred to user-present sessions or explicitly
micro-budgeted at dispatch.

| # | Decision | Call | Rationale |
|---|---|---|---|
| 1 | SESSIONS_TTL (MCP-4) | **60-day effective retention CONFIRMED** | Matches collections.py design; generous for demo phase; config-reversible |
| 2 | Web hosting | **Firebase Hosting (static build)** | Manifest OQ-1 recommendation: CDN, ~free, simplest auth integration |
| 3 | Domain | **Defaults** (`<project>.web.app` + `*.run.app`) | Zero DNS dependency, automatic TLS; custom domain = follow-up job when user buys one (only the user can own a domain) |
| 4 | OAuth consent | **External + Testing mode; app name "GRACE-2"; support email natealmanza3@gmail.com; privacy URL = /privacy (job-0285)** | No Google verification review needed; 100-test-user cap is ample |
| 5 | Test users | **natealmanza3@gmail.com** initially | User adds invitees on demand |
| 6 | Anonymous in prod | **Require sign-in; anonymous stays dev-only (AUTH_REQUIRED=false)** | Protects Gemini quota + spend; matches the sprint's hardening intent |
| 7 | Billing (Blaze for Identity Platform) | **Yes** | grace-2-hazard-prod already bills (Cloud Run/GCS/Vertex daily); Firebase Auth free tier is 50k MAU — no new cost commitment. Console attach step is USER-ONLY → goes on the unblock list |
| 8 | Production Atlas | **M0 free tier** | Demo-scale; no-backup risk acceptable pre-launch; documented upgrade path |
| 9 | Token budget | **~4.3M approved as ceiling** | Per standing "no token budget if it affects quality" + 4-lens panels on every job per the adversarial-verify memory rule |

**Execution notes:** prereqs verified at dispatch — job-0240 digests pinned
(modflow sha256:0b07…, sandbox sha256:0ad1…); job-0241's two fixes landed
during sprint-13 (mcp.py PDEATHSIG; job-0265 Cloud Logging transport) →
0241 re-scoped to verification + the manifest's named tests. Classifier-
blocked production mutations and console-only steps (Blaze attach, OAuth
consent screen) accumulate in reports/inflight/sprint-13-5-USER_UNBLOCK.md
for the user's return — jobs document-and-continue, never spin.

## Decision 10 (2026-06-11, post panel-job-0252) — canonical owner identity

**Call: the canonical owner identity for Cases/sessions/secrets is the
INTERNAL `users._id` ULID** (what `_resolve_or_provision_user` mints), NOT
the raw Firebase token uid. The Firebase uid lives only in
`users.firebase_uid` and is resolved to the internal ULID at every trust
boundary.

**Why:** (a) SRS H.2:42 / H.5:124 already specify the internal ULID — the
manifest's job-0252 line "sets user_id = uid from the verified token"
contradicted the SRS and loses; (b) anonymous/dev/migration identities
(sticky-anon ULIDs, `MIGRATION_ANON_UID`) have no Firebase uid, so the ULID
is the only identity that covers every mode; (c) the agent's
create→store→list chain already ships this way, panel-verified
self-consistent and leak-free (88 tests).

**Consequence:** job-0251's `mint_signed_url` must resolve the verified
Firebase uid → internal `users._id` via a users-collection lookup before the
`case_owned_by` check (fail-closed 403 when no users doc exists). Routed to
job-0251b. Manifest correction noted in sprint-13-5-manifest.md.

## Decision 11 (2026-06-11, post job-0254 design scout) — signed-URL emission rescoped (Reading X)

**Call: job-0254 rescopes from "sign every LayerURI" (~200K + panels) to the
agent-side cleanup slice (~60-80K).** The scout's read-only inventory (107K
Opus, file:line for every claim) proved the manifest's premise wrong: NO
browser-facing surface fetches a GCS object today. Rasters load via QGIS WMS
run.app URLs (job-0255's invoker-only + proxy is the actual lockdown — and
`mint_signed_url` structurally cannot sign a WMS URL, `parse_layer_uri`
rejects non-gs://); vectors are inline GeoJSON (job-0175); charts embed data
inline; ImpactPanel shows gs:// as text only. The single client-reaching raw
gs:// is the publish-failure degraded path (model_flood_scenario.py:810-819)
— and it never renders.

**Rescoped job-0254 (agent):** (a) close the degraded-path gs:// leak (never
emit raw gs:// in LayerURI.uri — drop or mark non-renderable); (b) introduce
`layer_uri_emit.py` as the single emission seam + `SIGNED_URLS` env scaffold
(default false, documented DORMANT); (c) tests. Dispatch held until job-0255
lands (server.py contention).

**Consequences:** `mint_signed_url` + job-0251b's verified contract stay as
dormant, panel-verified infrastructure — the natural consumer is a future
direct-fetch feature (signed-COG rendering or signed large-vector delivery
past the inline ceiling; web-client mints per the scout's Architecture A —
the only design that respects Decision F wire isolation AND the verified
function contract; needs CORS + browser invoker IAM when that day comes).
job-0257's manifest env line `SIGNED_URLS=true` is CORRECTED to: flag absent/
false in prod until a direct-fetch feature exists. Sprint acceptance step 4
(job-0259 "network tab shows X-Goog-Signature") is CORRECTED to: raster
requests flow through the agent's /qgis-proxy (0255) and the direct QGIS URL
403s; no signed-URL assertion until the feature exists.
