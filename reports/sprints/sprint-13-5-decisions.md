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
