# job-0252 — Sticky-user-id → real Firebase Auth migration + pre-Auth case migration (FROZEN KICKOFF)

**Specialist:** agent
**Sprint:** 13.5 Stage 1
**Model:** Opus
**Opened:** 2026-06-11
**Depends on:** sprint-13 close + job-0241; adversarial panel follows.

## Binding decisions
- `reports/sprints/sprint-13-5-manifest.md` (job-0252 scope) + `reports/sprints/sprint-13-5-decisions.md` Decision #6: production REQUIRES sign-in; anonymous stays dev-only behind `AUTH_REQUIRED=false`.
- Standing quota constraint: NO Gemini/Vertex generate calls (user demo quota reserved).

## Pre-existing substrate (verify-then-harden — NOT greenfield)
sprint-12 Wave 2 (job-0122) already built:
- `services/agent/src/grace2_agent/auth_handshake.py`: Firebase ID-token verification (`init_firebase_admin`, `_verify_id_token_sync`, `_verify_id_token_hook`/`set_verify_hook` test seam), first-login auto-create (`_resolve_or_provision_user`), anonymous fallback (`_provision_anonymous_user`), sticky-anonymous reuse (`_try_reuse_anonymous_user`), `authenticate_token`, `build_auth_ack`.
- `services/agent/src/grace2_agent/server.py`: `_handle_auth_token` (auth-token envelope → auth-ack), `_bind_auth_result` (binds user_id/tier/anon into SessionState), `_ensure_auth_handshake` (implicit anonymous fallback on first non-auth envelope), `SessionState` auth fields, `_send_error` (A.6 error envelope), `handler` connection loop.
- `services/agent/src/grace2_agent/persistence.py`: `list_cases_for_user` (CASES) + `list_secrets_refs` (SECRETS) both carry a `{"user_id": {"$exists": False}}` backward-compat clause. `MCPSurfaceTranslator` (update-one → update-many; EJSON unwrap). `init_persistence_from_env` builds the singleton.
- `firebase_admin` 6.9.0 IS installed in `.venv`.

## The production-hardening DELTA (what THIS job adds)
1. **`AUTH_REQUIRED` gate** (new env). When required + an unauthenticated WS connect (no valid Firebase ID token via the auth-token envelope within the handshake window) → REJECT: close the socket with the A.5 close code **4401** + an A.6 `AUTH_FAILED` error envelope. No anonymous fallback on the required path (remove-don't-shim from prod path; dev path keeps the anonymous behavior verbatim).
   - **DEFAULT-FLIP DECISION (shipped):** default `AUTH_REQUIRED="false"` in code with a loud TODO. Rationale: the running dev agent (pid verified, NO `AUTH_REQUIRED` in `/proc/<pid>/environ`) would have every connection rejected on next restart if default were "true" — breaking the user's live demo. job-0257 (production deploy) flips it to `true` via Cloud Run env. Clear precedence: explicit env wins; absent → "false".
2. **Pre-Auth case migration (OQ-0115-CASE-USER-LINK):** remove the `$exists:False` clause from `list_cases_for_user` (and the parallel one in `list_secrets_refs`); add a one-time, idempotent startup migration that assigns `user_id = MIGRATION_ANON_UID` to every CASE lacking a `user_id`. Uses the logical `update-one`→`update-many` MCP surface with a `{"user_id": {"$exists": False}}` filter + `$set`. Idempotent: re-running matches nothing.
3. **Tests:** forged/expired token rejected when `AUTH_REQUIRED=true`; `AUTH_REQUIRED=false` preserves today's anonymous behavior exactly (existing auth tests stay green); migration idempotent + non-corrupting; `$exists` clause gone (cases without user_id invisible to other users). Full agent suite: only the 5 proven pre-existing failures allowed.

## Hard constraints
- Do NOT restart the running agent; do NOT change the default behavior it runs under (default `AUTH_REQUIRED="false"`).
- `firebase_admin` lazy-imported (already is); tests mock it.
- `git add` only files touched.
- Files owned: `services/agent/src/grace2_agent/auth.py` (new), `server.py` (additive), `persistence.py` (clause removal + migration).

## Deliverables
`reports/inflight/job-0252-agent-20260611/{audit.md,report.md,STATE=IN_REVIEW}`; commit `job-0252: ...` + Co-Authored-By trailer. Adversarial panel follows.
