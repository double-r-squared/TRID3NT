# Audit: MongoDB MCP persistence foundation + Auth backend wiring

**Job ID:** job-0115-agent-20260608, **Sprint:** sprint-12-mega Wave 1.5, **Specialist:** agent

**Required reads:**
- Memory: `feedback_mongodb_mcp_canonical_persistence.md`
- `services/agent/src/grace2_agent/server.py` (existing M1 WebSocket substrate)
- `packages/contracts/src/grace2_contracts/case.py` (job-0099 — Case schema)
- `packages/contracts/src/grace2_contracts/secrets.py` (job-0100 — Secrets schema)
- MongoDB Atlas MCP server documentation (look up via WebSearch if needed)

### Scope

NEW file `services/agent/src/grace2_agent/persistence.py`:

```python
"""Thin typed wrapper around MongoDB Atlas MCP server CRUD operations.

Pattern: agent code calls Persistence.upsert_case(case_dataclass) — this module
calls the MongoDB MCP server's insert_one/update_one/find_one/find tools and
serializes/deserializes through the GraceModel contracts (NEVER raw dicts)."""

class Persistence:
    def __init__(self, mcp_client):
        self._mcp = mcp_client  # the configured MongoDB MCP client

    async def get_case(self, case_id: str) -> CaseSummary | None: ...
    async def upsert_case(self, case: CaseSummary) -> CaseSummary: ...
    async def list_cases_for_user(self, user_id: str) -> list[CaseSummary]: ...
    async def archive_case(self, case_id: str) -> None: ...
    async def delete_case(self, case_id: str) -> None: ...

    async def append_chat_message(self, msg: CaseChatMessage) -> None: ...
    async def get_session_state(self, case_id: str) -> CaseSessionState: ...

    async def get_user_by_firebase_uid(self, uid: str) -> User | None: ...
    async def upsert_user(self, user: User) -> User: ...

    async def list_secrets_refs(self, user_id, case_id=None) -> list[SecretRecord]: ...
    async def upsert_secret_ref(self, sec: SecretRecord) -> SecretRecord: ...
    async def revoke_secret(self, secret_id: str) -> None: ...

    async def append_audit(self, event_type: str, payload: dict) -> None: ...
```

ADD `services/agent/src/grace2_agent/server.py`:
- Instantiate Persistence on startup, hold in app-level singleton
- Resolve MongoDB MCP connection from env: `GRACE2_MONGO_MCP_URL` or stdio config

ALSO wire the OQ-0100-WS-REGISTRY-WIRING follow-up:
- ADD secrets payloads to `packages/contracts/.../ws.py` `CLIENT_TO_AGENT_PAYLOADS` / `AGENT_TO_CLIENT_PAYLOADS` / `ALL_PAYLOADS` dicts (idempotent-append)
- Add corresponding factories so `test_ws.py::test_every_a3_a4_a4b_payload_round_trips` passes
- DO NOT touch the Cases envelope wiring — that lands in Wave 2 with Case UX agent job

ALSO add a STUB User schema (small contract addition) at `packages/contracts/src/grace2_contracts/user.py`:
```python
class User(GraceModel):
    user_id: str  # ULID
    firebase_uid: str | None
    email: str | None
    display_name: str | None
    created_at: str
    is_active: bool = True
    prefs: dict = Field(default_factory=dict)
```
Export from `__init__.py` (idempotent-append).

**Tests** (≥6 unit + 1 integration):
- Persistence with mock MCP client: get_case, upsert_case, list_cases_for_user round-trips
- User serialization round-trip
- Secrets WS payload registration: factories dict matches ALL_PAYLOADS after appending
- test_ws.py::test_every_a3_a4_a4b_payload_round_trips passes
- Mock chat append + session_state hydration

**Live verification**:
- If MongoDB MCP server is reachable (env probe): perform a write-then-read on a test_case_id and assert round-trip
- Else: surface OQ-0115-MCP-NOT-PROVISIONED and skip live test; document expected env variable

### File ownership (exclusive)

- `services/agent/src/grace2_agent/persistence.py` (NEW)
- `services/agent/src/grace2_agent/server.py` (additive: MCP init on startup)
- `packages/contracts/src/grace2_contracts/user.py` (NEW — small)
- `packages/contracts/src/grace2_contracts/__init__.py` — append exports
- `packages/contracts/src/grace2_contracts/ws.py` — secrets factory registration (additive)
- `services/agent/tests/test_persistence.py` (NEW)
- `packages/contracts/tests/test_user.py` (NEW)
- `packages/contracts/tests/test_ws.py` — extend secrets-payload coverage
- `reports/inflight/job-0115-agent-20260608/`

### FROZEN

- All tools/* (additions land in sibling Wave 1.5 jobs)
- All workflows/*
- web/, infra/, docs/srs/, reports/complete/**


### FROZEN

All other `tools/*` (each Wave 1.5 sibling owns one); all `workflows/`, `services/workers/`, `web/`, `infra/`, `docs/srs/`, `styles/`, `reports/complete/**`. For schema/agent jobs, FROZEN is the inverse of their declared file ownership.

### Concurrency note (Wave 1.5 fan-out — 16 parallel)

~16 Wave 1.5 jobs in parallel. Idempotent-append works for `tools/__init__.py` + `main.py` + `packages/contracts/__init__.py` but Wave 1 produced 3 commit-label-swap patterns under load. **Required mitigation**: before `git commit`, run `git pull --rebase=true origin main 2>/dev/null || git stash && git pull --rebase && git stash pop` to handle sibling concurrent landings cleanly. If conflict on registration site, re-apply your import line.

### Codified lessons (do NOT violate)

1. **Geographic-correctness gate (job-0086)**: if your tool emits geometry, verify against actual geography (river mouth where it should be, not just bbox/URL consistency). Every fetcher's live test must check that emitted features fall inside requested bbox AND match the named place's actual outline if applicable.

2. **Kickoff-front-loaded design**: orchestrator did the design — execute, don't redesign. Surface OQs in your report rather than expanding scope.

### Acceptance criteria

- [ ] New tool/contract registered + visible at appropriate test surface
- [ ] ≥4 unit tests + ≥1 live test (env-guarded if external)
- [ ] Geographic-correctness check where applicable
- [ ] No FROZEN edits; single commit prefix `<job-id>:`; co-author line
- [ ] Returns commit SHA + outcome + 1-paragraph headline + evidence + OQs

