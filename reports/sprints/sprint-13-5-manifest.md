# Sprint 13.5: Production Deployment + Auth Hardening

**Status:** planned
**Opened:** 2026-06-10 (manifest refreshed; sprint-13 Stage 3 acceptance running concurrently)
**Closed:** —
**Concurrent with:** Sprint 13 (file ownership disjoint — see boundary table below)
**Gated on:** Sprint-13 Stage 3 acceptance closed (all sprint-13 Stage 3 jobs approved); M4 persistence migration APPROVED (job-0203 panel returned 4/4 CONFIRM)
**SRS milestones covered:** §F.1–F.3 Auth + Secrets; NFR-S-1 authenticated access; NFR-O-1 production Cloud Run; §3.8 signed-URL LayerURI emission; Sprint-13.5 tracks per `project_post_sprint_10_roadmap` 2026-06-08.

## Goal

At sprint close, GRACE-2 is shareable to non-developers: a real production deployment where a new user can sign in via Firebase Auth, create a Case, run a flood model, and see a layer served via short-lived signed URL — all over HTTPS on a real domain. No raw `gs://` or public Cloud Run URLs in the client. QGIS Server is invoker-only (no unauthenticated WMS). Agent and web client are deployed to production Cloud Run / Firebase Hosting environments, not just dev. Container image digests are pinned (job-0240 Cloud Build running concurrently). Persistence layer speaks the real MCP surface (M4, job-0203 APPROVED). Session TTL policy confirmed by user.

This sprint is entirely high-stakes infrastructure and auth surface. EVERY job carries an adversarial verify gate per `feedback_adversarial_verify_high_importance`. There are no exceptions.

## What sprint-13 produced (inputs to this sprint)

Sprint-13 ran 20 jobs (job-0220 through job-0239; some READY_FOR_AUDIT, job-0236 RUNNING, job-0238/0239 not yet dispatched). The following are now locked substrate for sprint-13.5:

| Artifact | Sprint-13 Job | Relevance to 13.5 |
|---|---|---|
| `MCPSurfaceTranslator` + `Persistence` real MCP surface | job-0203 (APPROVED 4/4) | Persistence path stable for all auth/session writes in 13.5 |
| `CodeExecRequestPayload` + `CodeExecResultPayload` contracts | job-0223/job-0233 | Sandbox cloud transport decision needed (13.5 job) |
| `python-sandbox.tf` + `infra/python-sandbox/` + `sandbox_runner.py` | job-0232 (READY) | Cloud transport OQ-SANDBOX-3 lands in 13.5 |
| MODFLOW container + Cloud Run Job substrate | job-0220 (READY) | Image build being pinned by job-0240 (concurrent) |
| Chart-emission contracts + tools | job-0223/job-0230 (READY) | No 13.5 dependency |
| ImpactPanel + ImpactEnvelope + Pelicun chain (Wave 4.11 P1–P4) | Wave 4.11 | P5 live acceptance deferred into 13.5 (conditional on Gemini quota) |
| `touch_session` / `upsert_session_record` + D.6 wiring | job-0203 (APPROVED) | Session persistence confirmed stable |
| MCP process-lifecycle: **PDEATHSIG + process-group teardown NOT yet in MCPClient.start** | job-0203 OQ | 43 orphan processes found on this box; fix required before production deploy |

## New items folded in since original manifest

The following 8 items were accumulated during sprint-13 execution and are formally added to sprint-13.5 scope:

**MCP-1 — MCP sidecar lifecycle hardening (PDEATHSIG + process-group):** 43 orphaned `mongodb-mcp-server` processes found on this development box (~2.9 GB RSS). `MCPClient.start` must set `PDEATHSIG` and launch via a new process group so that agent termination (normal or crash) propagates to the sidecar. Scope: `services/agent/src/grace2_agent/mcp.py` — additive (~5 lines). Owned by `job-0241-agent`. Must land before the production deploy (job-0257) so the production Cloud Run container does not leak MCP sidecars across request cycles.

**MCP-2 — Pin `mongodb-mcp-server` version in deploy + smoke re-run on bump:** The npm server's tool surface changed names at least once historically; job-0203 found the real surface by running `mcp_protocol_smoke.py`. Production must pin `mongodb-mcp-server@<exact-version>` in the deploy configuration (Cloud Run env or startup script) and re-run `evidence/mcp_protocol_smoke.py` as a pre-deploy gate whenever the pin is bumped. Owned by job-0257-infra (add pin + smoke-run step to its scope).

**MCP-3 — `MDB_MCP_READ_ONLY=false` in production deploy env:** `init_persistence_from_env` explicitly documents that the production deploy MUST set `MDB_MCP_READ_ONLY=false`. This is not a default. Must be present in the Cloud Run service env vars for `grace2-agent-prod`. Owned by job-0257-infra (verify env present in Tofu resource; adversarial panel correctness lens will check this explicitly).

**MCP-4 — SESSIONS_TTL retention decision (user sign-off required):** Effective session retention is 60 days after last activity (`expires_at = now + 30d` on each `touch_session` call + 30-day MongoDB TTL index = last-touch + 60d). This is consistent with the `collections.py:408` comment but has not received explicit user sign-off. Surface as a decision item before job-0257 dispatches. **Block-and-ask the user**: confirm 60-day effective TTL, or propose an alternative. This is not an agent-decidable policy.

**SANDBOX-1 — Cloud sandbox result transport: Cloud Logging read path (job-0233 OQ-SANDBOX-3):** The python-sandbox container writes its JSON result envelope to stdout, which flows to Cloud Logging. The agent (in cloud mode) must read the result back via the Cloud Logging API after the Cloud Run Job execution completes. `job-0233` explicitly recommends this option (zero new write identity; preserves read-only SA invariant). A new job (`job-0241a-agent` or folded into job-0257 scope) must wire the Cloud Logging read path in `sandbox_runner.py:read_sandbox_result`. Owned by `job-0241-agent` (same job as MCP-1, additive scope).

**OQ-0115-CASE-USER-LINK — Pre-Auth cases visible to all users:** `list_cases_for_user` backward-compat `$exists:False` clause shows pre-Auth cases to all users. Closes with the Auth track (job-0252). No separate job needed; existing job-0252 scope updated to include a migration step that assigns `user_id = MIGRATION_ANON_UID` to pre-Auth cases.

**DEPLOY-1 — Container image digest pinning (job-0240, running in parallel):** `job-0240-infra` is currently RUNNING: it submits MODFLOW and python-sandbox container builds via Cloud Build and pins the resulting digests into `infra/modflow.tf` and `infra/python-sandbox.tf`. Sprint-13.5 does NOT need to do a separate `tofu apply` for image creation; it only needs: (a) `tofu apply` for the new production Cloud Run services (job-0257), (b) a Cloud Run Job smoke test for the python-sandbox once job-0241 lands, and (c) live-verify of the production deploy. Job-0240's digest pins are a prereq for job-0257.

**OQ-227-PLUME-PRESET-QML — Continuous plume concentration QML authoring (engine, DEFERRED):** Sprint-13 produced `PlumeLayerURI` + the groundwater contamination chain (job-0221/0222/0227/0228). The `continuous_plume_concentration` QML style file has not been authored yet. Deferred to sprint-14 (engine specialist). Not a 13.5 blocker; plume layers render with a default QGIS grayscale QML until the engine job lands.

## Model routing policy

Opus for all substantive jobs (auth, signed URL, production deploy, end-to-end acceptance, MCP lifecycle hardening). Sonnet only for: onboarding polish (web-only component work), regression sweeps, and staging-environment smoke tests where the code path is already tested.

## File Ownership Boundaries (sprint-13.5 vs sprint-13 disjoint)

Sprint-13.5 owns **all** of the following paths. Sprint-13 must not touch them. If a sprint-13 job needs to cross into these files, it must route through the orchestrator.

| Path | Sprint-13.5 job |
|---|---|
| `infra/firebase/` (new directory) | job-0250-infra |
| `infra/signed_urls/` (new directory — Cloud Function) | job-0251-infra |
| `services/agent/src/grace2_agent/auth.py` | job-0252-agent |
| `web/src/auth.ts`, `web/src/hooks/useAuth.ts` (new) | job-0253-web |
| `infra/main.tf` (production Cloud Run service resources) | job-0255-infra, job-0256-infra, job-0257-infra |
| `web/src/index.tsx`, `web/src/App.tsx` (auth guard wiring only) | job-0253-web |
| `services/agent/src/grace2_agent/layer_uri_emit.py` (signed-URL emission) | job-0254-agent |
| `services/workers/qgis_proxy.py` (new — QGIS Server auth proxy) | job-0255-infra |
| `services/agent/src/grace2_agent/mcp.py` (PDEATHSIG + process-group only) | job-0241-agent |
| `services/agent/src/grace2_agent/sandbox_runner.py` (Cloud Logging read path only) | job-0241-agent |

Sprint-13 owns everything in `services/workers/modflow/`, `services/agent/workflows/model_*.py`, `services/agent/tools/chart_tools.py`, `services/agent/tools/code_exec_tool.py`, `infra/modflow/`, `infra/python-sandbox/`, and all chart/sandbox web components. No overlap.

## Wave Structure

```
PREREQ — MCP lifecycle + sandbox cloud transport (must land before Stage 1 auth work)
  job-0241  (agent: MCPClient PDEATHSIG + sandbox Cloud Logging result transport)
       ↓ fast track — no adversarial panel (small targeted fix, ~5 lines each)
STAGE 0 — Sprint-13 acceptance + container digest pinning (running concurrently)
  job-0236  (Case 3 acceptance — NWS/Idaho — RUNNING)
  job-0237  (conversational analysis acceptance — P5 Pelicun conditional)
  job-0238  (Python sandbox acceptance)
  job-0240  (Cloud Build: MODFLOW + python-sandbox image push + digest pin — RUNNING)
       ↓ sprint-13 Stage 3 closed; digests pinned; SESSIONS_TTL user sign-off obtained
STAGE 1 — Auth substrate (parallel, all file-disjoint)
  Firebase production project provisioning
  Signed-URL minting Cloud Function
  Sticky-user-id → real auth migration (agent) [+pre-Auth case migration]
  Firebase Auth SDK wiring (web)
       ↓ adversarial verify: ALL FOUR Stage 1 jobs
STAGE 2 — Signed URL emission + QGIS Server lockdown (parallel)
  LayerURI signed-URL emission in agent
  Flip QGIS Server Cloud Run to invoker-only + proxy
       ↓ adversarial verify: BOTH Stage 2 jobs
STAGE 3 — Production deploy (sequential by dependency)
  Web Cloud Run / Firebase Hosting deploy
  Agent WebSocket Cloud Run deploy  [+mongodb-mcp-server version pin + MDB_MCP_READ_ONLY env]
       ↓ adversarial verify: BOTH Stage 3 jobs
STAGE 4 — Onboarding polish (parallel with Stage 3 acceptance)
  User onboarding polish (web)
STAGE 5 — End-to-end production acceptance
  Production E2E (real user, real sign-in, real Case, real model run, P5 Pelicun conditional)
       ↓ adversarial verify: E2E acceptance job
STAGE 6 — Close
  Sprint-13.5 close + concurrent sprint-13 verify + OQ-227-PLUME-PRESET-QML carry-forward
```

## Prereq job: MCP lifecycle + sandbox cloud transport

| Job ID | Title | Specialist | Model | Est. Tokens | Depends on | Adv. Verify |
|--------|-------|-----------|-------|-------------|------------|-------------|
| job-0241-agent-TBD | MCPClient PDEATHSIG + sandbox Cloud Logging result transport | agent | sonnet | 80K | sprint-13 Stage 2 approved | no |

**job-0241 scope:** Two small targeted fixes:

1. **PDEATHSIG + process-group in `MCPClient.start`** (`services/agent/src/grace2_agent/mcp.py`): set `PR_SET_PDEATHSIG` via `ctypes` in the subprocess preexec_fn so the MCP sidecar receives SIGTERM when the Python parent exits. Also start the sidecar in a new process group (`os.setpgrp`) so that `os.killpg` on teardown terminates the whole group. Regression-test: existing `test_mcp_surface_translator.py` suite must stay green. Add one test: start MCPClient, kill the parent process group, assert sidecar is gone.
2. **Cloud Logging result transport in `sandbox_runner.py`** (`services/agent/src/grace2_agent/sandbox_runner.py`): implement `read_sandbox_result(execution_name)` using `google.cloud.logging_v2` to read the structured log entry emitted by the executor's stdout. The read-only-SA invariant is preserved (no GCS write). Raises `SandboxCloudModeUnavailable` only if the Cloud Logging API is not reachable. Regression: `test_sandbox_runner.py` 19 cases must stay green; add 2 new: (a) mock Cloud Logging returns envelope → result parsed; (b) Cloud Logging error → SandboxCloudModeUnavailable raised.

No adversarial panel. Both changes are small and targeted with explicit regression coverage. This job MUST be approved before job-0257 (production deploy).

## Stage 1 — Auth Substrate

Gated on: sprint-13 Stage 3 closed + job-0241 approved + job-0240 approved (digest pins in .tf) + user sign-off on SESSIONS_TTL (MCP-4 decision).

| Job ID | Title | Specialist | Model | Est. Tokens | Depends on | Adv. Verify |
|--------|-------|-----------|-------|-------------|------------|-------------|
| job-0250-infra-TBD | Firebase production project provisioning | infra | opus | 250K | sprint-13 close + job-0241 | YES |
| job-0251-infra-TBD | Signed-URL minting Cloud Function | infra | opus | 200K | sprint-13 close + job-0241 | YES |
| job-0252-agent-TBD | Sticky-user-id → real Firebase Auth migration + pre-Auth case migration | agent | opus | 220K | sprint-13 close + job-0241 | YES |
| job-0253-web-TBD | Firebase Auth SDK wiring (web client) | web | opus | 150K | sprint-13 close | YES |

**job-0250 scope (adversarial-verify gated):** Firebase production project via Tofu: OAuth client IDs, email/Google sign-in providers enabled, custom claims schema for tier gating (`{ tier: "free" | "pro" }`, `{ case_ids: string[] }`), production Firestore rules (deny-all default, user can only read/write their own UID namespace), production Cloud Run IAM roles. Auth domain configured. File ownership: `infra/firebase/` (new — `main.tf`, `variables.tf`, `outputs.tf`). Adversarial verify: correctness (do the Tofu resources provision cleanly without plan drift?) + contract (do custom claims match SRS §F.1 tier-gating spec?) + regression (no changes to dev environment) + live-verify (Firebase console shows providers enabled, test user can sign in via emulator). ~200K Opus panel.

**job-0251 scope (adversarial-verify gated):** Cloud Function (Python 3.12): `mint_signed_url(layer_uri: str, user_id: str, case_id: str, ttl_seconds: int = 3600)`. Validates that `user_id` owns `case_id` (MongoDB MCP lookup via job-0203 `Persistence` singleton — the translator is now the stable seam). Returns a GCS signed URL scoped to the layer's GCS object. Deployed to Cloud Functions gen2, HTTPS-triggered, authenticated (Firebase ID token required in Authorization header). 15-minute minimum TTL, 60-minute maximum TTL. File ownership: `infra/signed_urls/` (new — Cloud Function source + Tofu deploy resource). Adversarial verify: correctness (does the signed URL expire correctly? does a user with wrong `case_id` get rejected?) + contract (does the TTL cap match SRS §F.1 §3.8?) + regression (no impact to dev bucket access) + live-verify (real GCS signed URL fetched and expired correctly). ~200K Opus panel.

**job-0252 scope (adversarial-verify gated):** Two-part scope:
1. Migrate agent session identity from sticky anonymous `user_id` (ULIDs from Wave 4.8 job-0172 Part C) to real Firebase ID tokens. On WebSocket connection, client sends Firebase ID token in `Authorization` header; agent verifies token via Firebase Admin SDK; sets `user_id = uid` from the verified token. Unauthenticated connections rejected (401). Preserve sticky anonymous `user_id` behavior in dev mode only (env var `AUTH_REQUIRED=false` bypasses for local dev). Remove anonymous fallback from prod code path (remove-don't-shim).
2. Pre-Auth case migration (OQ-0115-CASE-USER-LINK): update `list_cases_for_user` to remove the `$exists:False` backward-compat clause; assign `user_id = MIGRATION_ANON_UID` constant to all existing cases that lack a `user_id` field (one-time migration step on first authenticated startup). This prevents pre-Auth cases from leaking to all users.
File ownership: `services/agent/src/grace2_agent/auth.py` (new), `services/agent/src/grace2_agent/server.py` (additive — auth header extraction). Adversarial verify: correctness (does a forged token get rejected?) + regression (dev mode still works with `AUTH_REQUIRED=false`; pre-Auth migration does not corrupt existing sessions) + contract (does `user_id` in Cases/sessions match Firebase UID?) + live-verify (Playwright with real Firebase sign-in emulator → WebSocket connection accepted). ~200K Opus panel.

**job-0253 scope (adversarial-verify gated):** Firebase Auth SDK integration in the web client: `useAuth` hook (`firebase/auth` SDK), sign-in with Google (production) + email/password (dev), `AuthGuard` component wrapping the App root (unauthenticated → sign-in page), `user.getIdToken()` injected into WebSocket connection header. Sign-in page minimal (not onboarding polish — that's job-0258). File ownership: `web/src/auth.ts` (new), `web/src/hooks/useAuth.ts` (new), `web/src/components/AuthGuard.tsx` (new), `web/src/App.tsx` (auth guard wiring only — additive import). Adversarial verify: correctness (does the ID token reach the agent's auth.py?) + regression (existing UI flows unaffected for authenticated users) + contract (token shape matches Firebase Admin SDK expectations) + live-verify (Playwright drives sign-in → WebSocket connects → chat works). ~200K Opus panel.

## Stage 2 — Signed URL Emission + QGIS Server Lockdown

Gated on all Stage 1 jobs approved (adversarial verify panels passed).

| Job ID | Title | Specialist | Model | Est. Tokens | Depends on | Adv. Verify |
|--------|-------|-----------|-------|-------------|------------|-------------|
| job-0254-agent-TBD | LayerURI signed-URL emission — replace raw gs:// + public Cloud Run URLs | agent | opus | 200K | job-0251, job-0252 | YES |
| job-0255-infra-TBD | Flip QGIS Server Cloud Run to invoker-only + thin agent proxy | infra | opus | 200K | job-0252 | YES |

**job-0254 scope (adversarial-verify gated):** Every `LayerURI` emitted by the agent (from `publish_layer`, `postprocess_flood`, `postprocess_pelicun`, and all future postprocess tools) must carry a signed URL instead of a raw `gs://` object path or a public-invoker Cloud Run URL. At emission time: call the `mint_signed_url` Cloud Function (job-0251) with the authenticated user's UID + Case ID + TTL. Client renders the signed URL in MapLibre as the WMS source base URL. Remove raw `gs://` and public Cloud Run URL paths from `LayerURI` (remove-don't-shim). File ownership: `services/agent/src/grace2_agent/layer_uri_emit.py` (new — replaces inline URL construction in postprocess tools). Downstream: all postprocess tools that currently hardcode URL construction need updating (job-0254 owns those updates). Adversarial verify: correctness (does the client receive a valid signed URL that MapLibre can load?) + contract (does `LayerURI.wms_url` now always carry a signed URL in prod mode?) + regression (dev mode with `SIGNED_URLS=false` still emits public Cloud Run URLs for local dev) + live-verify (Playwright: layer renders from signed URL; URL expires correctly after TTL). ~200K Opus panel.

**job-0255 scope (adversarial-verify gated):** QGIS Server Cloud Run service: change `--allow-unauthenticated` to invoker-only in Tofu. Add a thin QGIS proxy in the agent service: `GET /qgis-proxy?{WMS_params}` — the agent service (which has invoker permission) forwards the request to QGIS Server and streams the response. Client's MapLibre WMS `tiles` URL changes from `https://<qgis-run-url>/...` to `https://<agent-url>/qgis-proxy?...`. Ensure the proxy strips user credentials before forwarding (no UID leaks to QGIS Server). File ownership: `infra/main.tf` (QGIS Server Cloud Run IAM binding update), `services/workers/qgis_proxy.py` (new), `services/agent/src/grace2_agent/server.py` (new proxy route). Adversarial verify: correctness (can a direct unauthenticated WMS request to QGIS Server now return 403?) + regression (MapLibre WMS tiles still load after proxy wiring) + contract (proxy must not cache tile responses in agent memory — must stream) + live-verify (Playwright: flood layer renders via proxy; direct QGIS URL returns 403). ~200K Opus panel.

## Stage 3 — Production Deploy

Gated on all Stage 2 jobs approved.

| Job ID | Title | Specialist | Model | Est. Tokens | Depends on | Adv. Verify |
|--------|-------|-----------|-------|-------------|------------|-------------|
| job-0256-infra-TBD | Web client Cloud Run / Firebase Hosting production deploy | infra | opus | 200K | job-0253, job-0255 | YES |
| job-0257-infra-TBD | Agent WebSocket Cloud Run production deploy | infra | opus | 250K | job-0241, job-0252, job-0254, job-0255, job-0240 | YES |

**job-0256 scope (adversarial-verify gated):** Cloud Build trigger for `web/` → Docker image → Cloud Run service (production) OR Firebase Hosting deploy (static build). Decision per original OQ-1: Firebase Hosting for the static React build (cheaper, CDN-backed, simpler auth integration). Production environment variables: `VITE_AGENT_WS_URL`, `VITE_FIREBASE_CONFIG` (production Firebase config object). File ownership: `infra/main.tf` (Firebase Hosting config or Cloud Run deploy resource), `web/cloudbuild.yaml` (new), `.github/workflows/deploy-web.yml` (new, if GitHub Actions path chosen). Adversarial verify: correctness (does the production URL serve the React app over HTTPS?) + regression (dev `npm run dev` still works) + contract (production Firebase config matches the provisioned project from job-0250) + live-verify (load `https://<production-domain>` → sign-in page renders). ~200K Opus panel.

**job-0257 scope (adversarial-verify gated):** Agent WebSocket Cloud Run production service: separate service name from dev (`grace2-agent-prod` vs `grace2-agent-dev`). Env vars: `AUTH_REQUIRED=true`, `SIGNED_URLS=true`, **`MDB_MCP_READ_ONLY=false`** (required per MCP-3 above; adversarial correctness lens checks this explicitly), `MONGODB_MCP_SERVER_PIN=<exact-version>` (MCP-2 above). Min-instances: 1 (always-warm for demo). HTTPS/WSS via Cloud Run domain mapping. Service account has: Secret Manager access (Atlas URI, Firebase service account key), Cloud Workflows invoker, GCS runs+cache bucket access, Cloud Functions invoker (signed-URL minting), Cloud Logging read access (sandbox result transport). Startup gate: run `mcp_protocol_smoke.py` (from job-0203 evidence) against the pinned `mongodb-mcp-server` version before marking the revision healthy. File ownership: `infra/main.tf` (new production Cloud Run service resource). Adversarial verify: correctness (does the agent accept authenticated WebSocket connections and reject unauthenticated ones? is `MDB_MCP_READ_ONLY=false` present? is `mongodb-mcp-server` pinned?) + regression (dev service unaffected) + contract (all env vars from Secret Manager — no plaintext secrets in Cloud Run config) + live-verify (Playwright from a real browser: sign in → WebSocket connects to prod agent → send a chat message → receive a response). ~200K Opus panel.

## Stage 4 — Onboarding Polish

Can run in parallel with Stage 3 acceptance, since it only touches new web components.

| Job ID | Title | Specialist | Model | Est. Tokens | Depends on | Adv. Verify |
|--------|-------|-----------|-------|-------------|------------|-------------|
| job-0258-web-TBD | User onboarding polish — first-login UX + sample Case + tour | web | sonnet | 150K | job-0253 | no |

**job-0258 scope:** First-login flow: detect new user (no Cases in MongoDB) → show brief welcome modal (3 slides: what GRACE-2 is, how to create a Case, how to ask the agent to run a model) → auto-create a "Sample Case — Fort Myers flood demo" with pre-loaded chat history showing the Fort Myers flow + the existing flood layer. Tier-aware secrets UX: if user is `tier=free`, grey out Tier-2 tool cards in the tool browser with "Requires API key — go to Settings to add" tooltip. File ownership: `web/src/components/Onboarding.tsx` (new), `web/src/components/WelcomeModal.tsx` (new). No new backend calls required (uses existing Cases MCP from Wave 4.11/job-0203 + existing chat envelope shapes).

## Stage 5 — End-to-End Production Acceptance

Gated on all Stage 3 jobs approved + Stage 4 job approved.

| Job ID | Title | Specialist | Model | Est. Tokens | Depends on | Adv. Verify |
|--------|-------|-----------|-------|-------------|------------|-------------|
| job-0259-testing-TBD | End-to-end production acceptance — real user, real sign-in, real Case, real model + P5 Pelicun conditional | testing | opus | 300K | job-0256, job-0257, job-0258 | YES |

**job-0259 scope (adversarial-verify gated):** Full production E2E Playwright run against `https://<production-domain>` (not localhost, not dev Cloud Run):
1. Sign in as a real test user (test account provisioned in Firebase production project)
2. Create a new Case
3. Ask the agent to model flood scenario for Fort Myers
4. Verify: layer renders on map via signed URL (network tab: request URL is a `storage.googleapis.com/...&X-Goog-Signature=...` signed URL, not a public URL)
5. Verify: direct QGIS Server URL returns 403 (no unauthenticated access)
6. Verify: signed URL expires correctly (attempt to use the URL after TTL → 403)
7. Verify: onboarding modal fires for a NEW test account (no Cases in MongoDB)
8. Verify: Case created in sprint-13 with `user_id` from pre-Auth migration is NOT visible to a different test user
9. **P5 conditional**: if Gemini quota available, run "What's the flood damage for Hurricane Ian on Fort Myers?" → verify `compute_impact_envelope` chains and `ImpactPanel` slides out with headline stats. Screenshot evidence. If quota unavailable, mark P5 as "deferred — quota" in the report (not a 13.5 close blocker).
10. Screenshot evidence at each step.

Adversarial verify: correctness (signed URL signature valid? expiry correct? pre-Auth isolation verified?) + regression (did any sprint-13 jobs break in the production environment?) + contract (LayerURI shape in prod matches contracts v0.1?) + live-verify (all non-conditional steps produce the expected output). ~250K Opus panel. This panel is the final gate before the sprint closes.

## Stage 6 — Close

Gated on Stage 5 adversarial verify panel passed.

| Job ID | Title | Specialist | Model | Est. Tokens | Depends on | Adv. Verify |
|--------|-------|-----------|-------|-------------|------------|-------------|
| job-0260-testing-TBD | Sprint-13.5 close + concurrent sprint-13 verify + sprint-14 stub | testing | opus | 100K | job-0259 adversarial panel | no |

**job-0260 scope:** Full regression sweep (production-equivalent smoke tests + all dev suite). Confirm sprint-13 (concurrent) has no file-ownership collisions. Sprint-13.5 retrospective. Sprint-14 manifest stub (confirmed carry-forwards: InVEST, HRRR-Smoke, TELEMAC, Tier-2 conservation, OQ-227-PLUME-PRESET-QML continuous plume QML authoring). Update PROJECT_STATE.md: production URL, auth mode active, signed-URL minting live, QGIS Server invoker-only, MCP lifecycle hardened, sandbox Cloud Logging transport live.

## Execution Order

```
[prerequisite] Sprint-13 Stage 2 all approved
       |
CONCURRENT (no gate between these):
  job-0240  (Cloud Build: MODFLOW + sandbox image + digest pin — already RUNNING)
  sprint-13 Stage 3 acceptance (job-0236 RUNNING, job-0237/0238 pending dispatch)
       |
job-0241  (MCPClient PDEATHSIG + sandbox Cloud Logging — fast track, no panel)
       |
DECISION GATE: user confirms SESSIONS_TTL = 60d effective retention
       |
STAGE 1 (parallel, file-disjoint):
  job-0250  (Firebase production project)    ← adversarial verify
  job-0251  (signed-URL Cloud Function)      ← adversarial verify
  job-0252  (agent auth migration + pre-Auth case migration)  ← adversarial verify
  job-0253  (web Firebase Auth SDK)          ← adversarial verify
       |
       ↓ ALL FOUR adversarial verify panels passed
STAGE 2 (parallel, file-disjoint):
  job-0254  (LayerURI signed-URL emission)   ← adversarial verify
  job-0255  (QGIS Server invoker-only flip)  ← adversarial verify
       |
       ↓ BOTH adversarial verify panels passed
STAGE 3 (parallel):
  job-0256  (web production deploy)          ← adversarial verify
  job-0257  (agent production deploy)        ← adversarial verify
       |
       ↓ BOTH adversarial verify panels passed
STAGE 4 (parallel with Stage 3 acceptance):
  job-0258  (onboarding polish)
       |
       ↓ Stage 3 + Stage 4 both approved
STAGE 5:
  job-0259  (E2E production acceptance)      ← adversarial verify (FINAL GATE)
       |
       ↓ adversarial verify panel passed
STAGE 6:
  job-0260  (close + sprint-14 stub)
```

**Zero-exception adversarial verify rule for this sprint:** no job advances to the next stage without its adversarial verify panel returning ≥3 of 4 lenses confirm. Any refutation triggers a focused fix job before re-running the panel. This is the deployment surface — there is no "fix it in the next sprint."

## Adversarial Verify Schedule

| Target Job | Panel Trigger | Lenses | Est. Panel Cost |
|---|---|---|---|
| job-0250 (Firebase project) | after ready-for-audit | 4 lenses | ~200K Opus |
| job-0251 (signed-URL function) | after ready-for-audit | 4 lenses | ~200K Opus |
| job-0252 (agent auth + pre-Auth migration) | after ready-for-audit | 4 lenses | ~200K Opus |
| job-0253 (web auth SDK) | after ready-for-audit | 4 lenses | ~200K Opus |
| job-0254 (LayerURI emission) | after ready-for-audit | 4 lenses | ~200K Opus |
| job-0255 (QGIS Server lockdown) | after ready-for-audit | 4 lenses | ~200K Opus |
| job-0256 (web prod deploy) | after ready-for-audit | 4 lenses | ~200K Opus |
| job-0257 (agent prod deploy) | after ready-for-audit | 4 lenses (MDB_MCP_READ_ONLY + version-pin explicit) | ~200K Opus |
| job-0259 (E2E production acceptance) | after ready-for-audit | 4 lenses (final gate) | ~250K Opus |

Total adversarial verify: 9 panels × ~200–250K = ~1,850K Opus tokens.

## Gating + Acceptance Criteria

Pre-Stage-1 prereqs:
- [ ] job-0241: `MCPClient.start` has PDEATHSIG + process-group; sandbox `read_sandbox_result` wired to Cloud Logging; regression suites green (job-0241 evidence)
- [ ] job-0240: MODFLOW + python-sandbox container digests pinned in `.tf` files; `tofu validate` green (job-0240 evidence)
- [ ] User confirms SESSIONS_TTL: 60-day effective retention after last activity acknowledged (orchestrator decision gate)

Auth substrate:
- [ ] Firebase production project: test user signs in via Google OAuth in a real browser (not emulator) (job-0250 evidence — screenshot)
- [ ] Signed-URL Cloud Function: `mint_signed_url(layer_uri, user_id, case_id)` returns a valid GCS signed URL; the URL works until TTL and returns 403 after expiry (job-0251 live-run evidence)
- [ ] Agent auth migration: unauthenticated WebSocket connection rejected with 401; authenticated connection accepted and `user_id` set to Firebase UID; pre-Auth cases migrated to `MIGRATION_ANON_UID` (job-0252 evidence — log transcript)
- [ ] Web auth SDK: sign-in page renders; Google sign-in flow completes; ID token delivered to agent WebSocket (job-0253 Playwright screenshot)

Signed URL emission + QGIS lockdown:
- [ ] Every `LayerURI.wms_url` in production mode carries a signed URL (no raw `gs://` or public Cloud Run URL) (job-0254 network-tab evidence)
- [ ] Direct unauthenticated request to QGIS Server Cloud Run URL returns 403 (job-0255 curl evidence)
- [ ] MapLibre WMS tiles still render correctly via the agent proxy (job-0255 Playwright screenshot)

Production deploy:
- [ ] Web client loads at `https://<production-domain>` with valid TLS (job-0256 evidence)
- [ ] Agent WebSocket accepts authenticated connection at `wss://<agent-production-domain>` (job-0257 evidence)
- [ ] No plaintext secrets in Cloud Run environment variables — all from Secret Manager (job-0257 Tofu plan evidence)
- [ ] `MDB_MCP_READ_ONLY=false` confirmed present in production Cloud Run env (job-0257 adversarial correctness lens)
- [ ] `mongodb-mcp-server` version pin confirmed in production startup; smoke script passes (job-0257 evidence)

Onboarding:
- [ ] New-user onboarding modal fires on first login (no existing Cases) (job-0258 Playwright screenshot)
- [ ] Sample Case auto-created with Fort Myers flood history pre-loaded (job-0258 evidence)

End-to-end production acceptance:
- [ ] Real test user signs in → creates Case → runs model → layer renders from signed URL on production domain (job-0259 Playwright screenshot — 10-step checklist)
- [ ] Signed URL expiry verified: URL fails after TTL (job-0259 evidence)
- [ ] Pre-Auth case isolation: case without user_id NOT visible to a different signed-in user (job-0259 evidence)
- [ ] Sprint-13 (concurrent) has no regressions in production environment (job-0260 evidence)

## Token Budget

| Stage | Jobs | Est. Tokens |
|---|---|---|
| Prereq — MCP lifecycle + sandbox transport (1 job) | job-0241 | ~80K |
| Stage 1 — auth substrate (4 jobs) | Firebase + signed-URL + agent auth + web auth | ~820K |
| Stage 1 — adversarial verify (4 panels) | — | ~800K |
| Stage 2 — signed URL + QGIS lockdown (2 jobs) | LayerURI + proxy | ~400K |
| Stage 2 — adversarial verify (2 panels) | — | ~400K |
| Stage 3 — production deploy (2 jobs) | web + agent | ~450K |
| Stage 3 — adversarial verify (2 panels) | — | ~400K |
| Stage 4 — onboarding (1 job) | web UX | ~150K |
| Stage 5 — E2E acceptance (1 job) | | ~300K |
| Stage 5 — adversarial verify (1 panel) | final gate | ~250K |
| Stage 6 — close (1 job) | | ~100K |
| **Total** | **12 jobs** | **~4.15M** |

Note: the large token budget is dominated by adversarial verify panels (9 × ~200–250K = ~1.85M). Specialist work itself is ~2.3M. This is the correct cost posture for an irreversible environment shift. The slight increase over the original estimate (~3.9M) reflects the new prereq job-0241 + expanded job-0257 scope (MCP-2/3) + expanded job-0252 scope (OQ-0115 migration) + expanded job-0259 scope (P5 conditional + pre-Auth isolation check).

## Open Questions

1. **SESSIONS_TTL user confirmation (DECISION-GATE — must resolve before Stage 1):** Effective session retention is 60 days after last activity (`expires_at = now + 30d` per `touch_session` + 30-day TTL = last-touch + 60d). This was flagged by the job-0203 contract lens. The user must explicitly confirm this is the intended retention policy, or the policy must be changed before the production deploy. Options: (a) confirm 60d effective retention; (b) change to a shorter horizon (e.g., 30d effective = 15d expires_at + 15d TTL); (c) add user-visible "remember this device" toggle that extends TTL. Orchestrator blocks Stage 1 until the user acknowledges.

2. **Firebase Hosting vs Cloud Run for web client** (unchanged from original): this manifest defaults to Firebase Hosting for the static React build. If the user prefers Cloud Run (more control, easier custom headers), job-0256 scope changes slightly (Dockerfile + Cloud Run instead of `firebase deploy`). TENTATIVE: Firebase Hosting. Escalate if user has a strong preference.

3. **Production domain name** (unchanged from original): no custom domain is specified in this manifest. The production URL may be a Cloud Run auto-assigned URL or a Firebase Hosting default. If the user has a custom domain, provide before job-0256 dispatches. TENTATIVE: use Firebase Hosting default domain; custom domain is a follow-up job.

4. **Signed-URL TTL for WMS tiles** (unchanged from original, now clarified by job-0255): signed URLs cover GCS COG download links; WMS tile access is auth-gated via the QGIS proxy (no per-tile signed URL needed). This was the tentative answer in the original manifest; confirmed by the job-0255 proxy architecture.

5. **Sprint-13 concurrent coordination** (unchanged from original): file ownership is disjoint. The orchestrator must verify that the sprint-13.5 auth changes don't break the sprint-13 multi-turn loop. Mitigation: the orchestrator runs a targeted integration smoke test before closing either sprint.

6. **Secret Manager migration for Atlas URI** (unchanged from original): job-0252 owns the Atlas URI → Secret Manager migration; job-0257 reads from Secret Manager.

7. **OQ-SANDBOX-1 (Atlas egress rule — keep or drop?):** The python-sandbox VPC firewall has a placeholder Atlas CIDR (RFC-5737 non-routable `203.0.113.0/32`). At v0.1, the agent persists charts via the Mongo MCP path, not the sandbox. Orchestrator should confirm before job-0257 whether the sandbox needs direct Mongo access, and update `sandbox_atlas_cidr` in `terraform.tfvars` accordingly. TENTATIVE: keep non-routable; no direct Mongo from sandbox at v0.1.

8. **P5 Pelicun live acceptance (conditional):** P5 was deferred from Wave 4.11 to sprint-13.5. It is now a conditional step within job-0259 (not a blocking gate). If Gemini quota is available at production acceptance time, the tester runs the Pelicun chain and screenshots the ImpactPanel. If quota is exhausted, the tester marks P5 deferred in the report; sprint-13.5 closes anyway. P5 is NOT a close-blocker for sprint-13.5.

## Deferred to Sprint 14

- OQ-227-PLUME-PRESET-QML: `continuous_plume_concentration` QML style authoring (engine specialist) — plume layers render with default QGIS grayscale until this lands
- InVEST analytical tools (`run_invest_carbon_storage` and sub-models)
- HRRR-Smoke air-quality overlay
- TELEMAC-2D coastal surge engine
- HEC-HMS urban watershed engine
- Tier-2 conservation fetchers (eBird, IUCN, Movebank) in production
- Per-species color-coded layer chrome UX
- OQ-SANDBOX-2: relocate CIDR vars + API enablements from `python-sandbox.tf` into `variables.tf` + `gcp.tf` (cosmetic; functionally correct as-is)
- OQ-CODE-EXEC-CATEGORY: distinct `data_analysis` category (requires deliberate CATEGORIES re-bucketing, not a one-tool exception)
- OQ-0203-FIND-PAGINATION: cursor pagination for chat histories beyond 1000 documents
- Tool card expand output (V-chevron showing raw function_response) — per `feedback_tool_card_expand_output`
- Synthetic close-out design — per `feedback_synthetic_close_out_design`
