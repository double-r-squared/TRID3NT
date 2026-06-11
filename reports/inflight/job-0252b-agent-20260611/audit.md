# job-0252b — gate-ordering hygiene (no junk user rows on rejected connections) + stale comment (FROZEN KICKOFF)

**Specialist:** agent
**Sprint:** 13.5 Stage 1 (fix-pair closing the job-0252 panel's minor findings)
**Model:** Opus
**Opened:** 2026-06-11
**Depends on:** job-0252 DONE (panel 3/4 — read the panel verdict appended to `reports/inflight/job-0252-agent-20260611/report.md` first).

## Finding 1 (panel-job-0252 live-verify lens, minor — production hygiene)
Under `AUTH_REQUIRED=true`, `auth_handshake.authenticate_token()` runs `_provision_anonymous_user()` — which PERSISTS an ephemeral anonymous `UserDocument` to the users collection — on every failure path (`auth_handshake.py:227/233/239`) BEFORE the server-side gate (`server.py` `_handle_auth_token` / `_ensure_auth_handshake`) inspects `result.is_anonymous` and rejects the socket. Verified on disk by the panel: every forged/unauthenticated connection writes a junk anonymous user row. Under hostile/bot load on the production gate that is unbounded junk-row growth + write amplification.

**Fix:** when `auth_required()` is on, the rejection must short-circuit BEFORE any anonymous-user provisioning/persistence. Design is yours (e.g. `authenticate_token` consults `auth_required()` on its failure paths and returns an unprovisioned anonymous `AuthResult` (no persistence write), or the callers gate before provisioning) — but these properties are non-negotiable:
- `AUTH_REQUIRED` off (dev/demo): behavior byte-identical to today — anonymous provisioning, sticky-anon reuse, auth-ack — the live demo agent must be unaffected on its next restart.
- `AUTH_REQUIRED` on + invalid/missing token: NO write to the users collection (or any collection); the wire behavior stays exactly the panel-verified A.5/A.6 sequence (AUTH_FAILED error envelope, then close 4401); no session bind.
- `AUTH_REQUIRED` on + valid token: unchanged (resolve-or-provision real user, bind, ack).

## Finding 2 (job-0252 report R1, cosmetic)
`services/agent/src/grace2_agent/secrets_handler.py:416` comment still describes the removed `{"user_id": {"$exists": False}}` backward-compat branch of `list_secrets_refs`. Runtime is correct; fix the comment to describe the current owner-scoped semantics (job-0252 removed the leak clause).

## Tests
- New: gate-ON + forged token → users collection unchanged (count before == after) AND 4401+AUTH_FAILED still emitted; gate-ON + no-token first envelope → same; gate-OFF → anonymous user IS provisioned + persisted exactly as today (regression pin); gate-ON + valid token → real user provisioned/bound.
- Existing `tests/test_auth_required_gate.py` (24) must stay green unmodified unless an assertion is genuinely strengthened — never weakened.
- Full agent suite: only the 5 proven pre-existing failures allowed (3x test_data_fetch docstring-tier, 2x test_model_flood_scenario GCS).

## Hard constraints
- NO Gemini/Vertex calls. Do NOT restart or disturb the running dev agent.
- Files owned: `services/agent/src/grace2_agent/auth_handshake.py`, `server.py` (only if the gate-ordering design needs it), `secrets_handler.py` (comment only), tests. Nothing in `infra/` (job-0251b owns the mint function — running in parallel; no file overlap exists, keep it that way).
- `git add` only files you touched; commit `job-0252b: ...` + `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

## Deliverables
`reports/inflight/job-0252b-agent-20260611/{report.md,STATE=IN_REVIEW}`; report quotes the before/after provisioning order and the test evidence. Orchestrator folds this into the Stage-1 re-panel.
