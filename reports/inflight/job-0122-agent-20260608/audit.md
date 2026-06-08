# Audit: Auth backend — Firebase ID-token verifier + WS connect handshake

**Job ID:** job-0122-agent-20260608, **Sprint:** sprint-12-mega Wave 2, **Specialist:** agent

**Required reads:**
- `docs/srs/H-auth-and-users.md` (Wave 1.5 — Appendix H)
- `services/agent/src/grace2_agent/persistence.py` (User CRUD)
- `packages/contracts/src/grace2_contracts/user.py` (Wave 1.5 — User dataclass)
- `services/agent/src/grace2_agent/server.py` (WS connect hook)
- Firebase Admin SDK docs (verify_id_token)

### Scope

Wire Firebase Auth into the WebSocket connect handshake.

1. **Add `firebase-admin` dep** to `services/agent/pyproject.toml`
2. **Initialize Firebase Admin SDK** on agent startup using GCP application default credentials (no separate service account JSON needed in v0.1)
3. **WS connect handshake** (server.py):
   - Client sends `auth-token` envelope with Firebase ID token immediately after WS connect (defined in `packages/contracts/.../auth.py` envelopes — NEW small file)
   - Server calls `firebase_admin.auth.verify_id_token(token)` → firebase_uid
   - Server calls `Persistence.get_user_by_firebase_uid(firebase_uid)`; if None, `Persistence.upsert_user(User(firebase_uid=..., created_at=...))` (first-login auto-create)
   - Server stores `authenticated_user_id` on the connection context
   - All subsequent envelopes for this connection are scoped to this user_id
4. **Anonymous fallback**: if no `auth-token` envelope arrives within 5s OR token is anonymous, server creates an ephemeral `User(firebase_uid=None, is_anonymous=True, ...)` — anonymous users CAN use Cases but Persistence.list_cases_for_user filters; web UI prompts to upgrade on save
5. **Server emits `auth-ack`** envelope back to client confirming authenticated user_id

**Tests** (≥8 unit + 1 integration):
- verify_id_token mocked → user_id resolved
- First login: User auto-created in MongoDB via Persistence
- Existing user: User looked up, not re-created
- Anonymous fallback: ephemeral user created without firebase_uid
- Auth envelope contracts round-trip
- Connection context retains authenticated_user_id across subsequent envelopes
- Integration: full WS connect → auth-token → auth-ack flow with mocked Firebase + Persistence

### File ownership (exclusive)

- `packages/contracts/src/grace2_contracts/auth.py` (NEW — auth envelopes: AuthTokenEnvelope, AuthAckEnvelope)
- `packages/contracts/src/grace2_contracts/__init__.py` — append exports
- `packages/contracts/tests/test_auth.py` (NEW)
- `services/agent/src/grace2_agent/auth_handshake.py` (NEW — Firebase verify + Persistence wiring)
- `services/agent/src/grace2_agent/server.py` — connect-handshake hook + authenticated_user_id propagation (~80 lines)
- `services/agent/src/grace2_agent/main.py` — Firebase Admin init on startup
- `services/agent/pyproject.toml` — add firebase-admin dep
- `services/agent/tests/test_auth_handshake.py` (NEW)
- `reports/inflight/job-0122-agent-20260608/`


### FROZEN

All files outside the explicit file-ownership list. Especially: every sibling Wave 2 job's exclusive files; `reports/complete/**`; `docs/SRS_v0.3.md` monolith (regenerated only); all Wave 1/1.5 atomic tool files (additive use only — don't modify their signatures).

### Concurrency note (Wave 2 fan-out — 16 parallel)

Same idempotent-append pattern + `git pull --rebase` pre-commit mitigation as Wave 1.5. Files all land correctly in HEAD; only commit-message labels may drift. Use marker commits if your changes get swept into a sibling's commit hash.

### Codified lessons (do NOT violate)

1. **Geographic-correctness gate (job-0086)**: verify against real geography, not URL/render consistency.
2. **Kickoff-front-loaded design**: orchestrator did the design — execute, don't redesign. Surface OQs in your report rather than expanding scope.
3. **MongoDB MCP canonical persistence (job-0115 foundation)**: ALL CRUD goes through `Persistence.*`. Do NOT design custom collection wrappers. If your job needs a new method on Persistence, ADD it (additive) rather than bypassing.

### Acceptance criteria

- [ ] All deliverables landed per scope
- [ ] ≥4 unit tests + ≥1 live test (env-guarded if external)
- [ ] Geographic-correctness / behavioral-correctness verified
- [ ] No FROZEN edits; single commit prefix `<job-id>:`; co-author line
- [ ] Returns commit SHA + outcome + 1-paragraph headline + evidence + OQs

