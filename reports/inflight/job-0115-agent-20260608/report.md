# Report: MongoDB MCP persistence foundation + Auth backend wiring

**Job ID:** job-0115-agent-20260608
**Sprint:** sprint-12-mega Wave 1.5
**Specialist:** agent
**Status:** ready-for-audit

## Summary

Landed the LLM-facing MongoDB persistence foundation: a typed `Persistence`
wrapper over the MongoDB Atlas MCP server (FR-AS-4), wired as an app-level
singleton via `init_persistence_from_env()` at agent-service startup with
env-gated resolution. Added the Auth-stub `User` contract and resolved the
sprint-12 Wave-1 OQ-0100-WS-REGISTRY-WIRING by splatting the per-Case
secrets payloads into ws.CLIENT_TO_AGENT_PAYLOADS / AGENT_TO_CLIENT_PAYLOADS
/ ALL_PAYLOADS. Tests: 13 unit (persistence) + 6 unit (User) + extended
ws.py round-trip coverage. Live MCP integration env-guarded as OQ-0115.

## Changes Made

- `packages/contracts/src/grace2_contracts/user.py` (NEW): Wave-1.5 Auth-stub
  User GraceModel (user_id, firebase_uid, email, display_name, created_at,
  is_active, prefs); forward-compat shape.
- `packages/contracts/src/grace2_contracts/__init__.py`: append user module
  import + __all__ entry (idempotent-append).
- `packages/contracts/src/grace2_contracts/ws.py`: resolve OQ-0100-WS-REGISTRY-WIRING.
  Splat SECRET_CLIENT_TO_AGENT_PAYLOADS / SECRET_AGENT_TO_CLIENT_PAYLOADS
  into the routing dicts. ws.ALL_PAYLOADS grows 25 -> 28 payload types.
  Cases envelope wiring NOT added (Wave-2 scope per kickoff).
- `packages/contracts/tests/test_user.py` (NEW): 6 unit tests (round-trip,
  minimal construction, extra='forbid', ULID, Invariant-9, Z-suffix).
- `packages/contracts/tests/test_ws.py`: extend round-trip factory with the
  three secrets payloads + new regression-guard test for registry inclusion.
- `services/agent/src/grace2_agent/persistence.py` (NEW): Persistence class
  with 12 async methods over the MCP tool surface (update-one / insert-one /
  find-one / find). Methods: get/upsert/list/archive/delete_case;
  append_chat_message / get_session_state; get_user_by_firebase_uid /
  upsert_user; list/upsert/revoke secret_ref; append_audit. Decision F
  backstop in upsert_secret_ref. Duck-typed MCPClientProtocol.
- `services/agent/src/grace2_agent/server.py`: module-level _PERSISTENCE
  singleton + get/set/init_persistence_from_env accessors. Best-effort
  init in run_server. Env: GRACE2_MONGO_MCP_URL (reserved HTTP transport) /
  GRACE2_MONGO_MCP_STDIO=1 (stdio sidecar).
- `services/agent/tests/test_persistence.py` (NEW): 13 unit tests with
  MockMCPClient + 1 live env-guarded test (OQ-0115-MCP-NOT-PROVISIONED skip).

## Decisions Made

- Decision: duck-typed MCPClientProtocol for Persistence(mcp_client) rather
  than concrete MCPClient import. Rationale: mock-friendly tests, matches
  the existing tools-registry pattern.
- Decision: list_cases_for_user permissive $or filter that also matches
  documents with no user_id field at all. Rationale: ProjectDocument is
  pre-Auth; the filter narrows automatically when the Auth track adds the
  field. OQ-0115-CASE-USER-LINK.
- Decision: app-level singleton Persistence (not per-connection).
  Rationale: MCP client subprocess is expensive to start; tests swap it
  through set_persistence.
- Decision: init_persistence_from_env is best-effort at startup.
  Rationale: keeps M1 in-memory path working in CI/dev.

## Invariants Touched

- 8. Cancellation is first-class — preserves; no new long-running paths.
- 9. No cost theater — preserves; Invariant-9 negative-control test on User.
- Decision F (wire isolation) — preserves; upsert_secret_ref runtime-rejects
  key_value-shaped fields before MCP write.
- MCP is the LLM-facing DB path — preserves; no direct PyMongo driver added.

## Open Questions

- OQ-0115-MCP-NOT-PROVISIONED (TENTATIVE skip): live MongoDB MCP server
  requires WI + Secret Manager access; Wave-1.5 environment lacks
  GRACE2_MONGO_MCP_STDIO=1. Unit-test coverage through MockMCPClient is
  exhaustive. Recommended resolution: infra job that flips the env var in
  the agent deploy manifest.
- OQ-0115-CASE-USER-LINK: projects collection has no user_id field
  (FR-MP-5 pre-Auth). list_cases_for_user filter shape stays compatible
  with either (a) adding user_id to ProjectDocument or (b) project_membership
  side-collection. Auth track schema job decides.
- OQ-0115-USER-FIREBASE-UID-REQUIRED: User.firebase_uid is currently nullable;
  full Auth track should flip to required.
- OQ-0115-AUDIT-FIRE-AND-FORGET-VS-QUEUE: append_audit is synchronous;
  M5+ audit volume probably wants a background task queue.

## Dependencies and Impacts

- Depends on: job-0099 (Case contracts), job-0100 (Secret contracts).
- Affects:
  - Wave-2 Case UX agent: get_session_state drives case-open rehydration.
  - Wave-2 web Case UX: secret-* envelopes registered, client can submit.
  - Auth/Users track: User stub in place; additive growth lands there.

## Verification

- Tests run:
  - packages/contracts/tests/test_user.py: 6/6 pass
  - packages/contracts/tests/test_ws.py: 34/34 pass
  - packages/contracts/tests/test_secrets.py: 10/10 pass
  - services/agent/tests/test_persistence.py: 13 unit pass + 1 skipped
  - packages/contracts/tests/ full sweep: 216 passed
  - services/agent/tests/ full sweep: 764 passed, 37 skipped

- Live E2E evidence — registry surface verification (Python REPL transcript):
  ```
  OK — User wire shape: {'schema_version': 'v1', 'user_id': '01KTKP57DK1GVVR14PPJZAHXZJ',
    'firebase_uid': None, 'email': None, 'display_name': None,
    'created_at': '2026-06-08T00:00:00Z', 'is_active': True, 'prefs': {}}
  OK — ws.ALL_PAYLOADS has 28 payload types
  OK — CLIENT_TO_AGENT includes secret-add, secret-revoke
  OK — AGENT_TO_CLIENT includes secrets-list
  ```
- Live E2E evidence — Mock-MCP round-trip: realistic CaseSummary (Hurricane
  Ian Fort Myers, primary_hazard=flood, bbox=(-82.0,26.5,-81.8,26.7))
  upserted then re-fetched; returned model field-for-field equal. Mock
  records MCP call as update-one with upsert=True against projects collection.
- Live MCP integration: skipped (env-guarded), surfacing OQ-0115-MCP-NOT-PROVISIONED.

- Results: pass.
