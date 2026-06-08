# Audit: Secrets agent backend — vault write to GCP Secret Manager + ws handlers

**Job ID:** job-0124-agent-20260608, **Sprint:** sprint-12-mega Wave 2, **Specialist:** agent

**Required reads:**
- `packages/contracts/src/grace2_contracts/secrets.py` (Wave 1 — Secret envelopes)
- `services/agent/src/grace2_agent/persistence.py` (Wave 1.5 — Persistence)
- `packages/contracts/src/grace2_contracts/ws.py` (Wave 1.5 — secrets payloads registered)

### Scope

Implement the agent-side secret lifecycle: receive `secret-add` envelope from client → write actual key to GCP Secret Manager → store reference in MongoDB via Persistence → never echo the key.

1. **Add `google-cloud-secret-manager` dep** to `services/agent/pyproject.toml`
2. **NEW `services/agent/src/grace2_agent/secrets_handler.py`**:
   - `handle_secret_add(envelope, user_id, case_id) -> SecretRecord`: writes key_value to GCP Secret Manager (project resolved from env), returns SecretRecord with vault_ref (the `projects/.../secrets/.../versions/latest` URI)
   - `handle_secret_revoke(secret_id) -> None`: marks SecretRecord.is_active=False in MongoDB; does NOT delete the vault entry (audit trail)
   - `handle_secrets_list(user_id) -> SecretsListEnvelopePayload`: queries Persistence.list_secrets_refs; NEVER returns vault key value
   - Add method to Persistence: `get_secret_value(secret_ref: SecretRecord) -> str` — reads from GCP Secret Manager using vault_ref; called by Tier-2 fetchers
3. **server.py wiring**: route `secret-add` / `secret-revoke` / `secrets-list-request` envelopes to the handler
4. **Audit log**: each secret operation appended to MongoDB `audit_log` via Persistence

**Tests** (≥8 unit + 1 integration):
- Mocked Secret Manager client + Persistence: secret-add writes vault entry + creates SecretRecord
- get_secret_value: vault read returns the original key
- Revoked secret: get_secret_value raises typed error
- secrets-list returns SecretsListEnvelopePayload with NO key_value field
- audit_log entries created on add/revoke
- Multi-tenant isolation: user A cannot see user B's secrets via list
- Integration: full lifecycle (add → list → use → revoke → list again)

**Live verification** (env-gated GRACE2_TEST_LIVE_SECRETS=1):
- Add a test secret to a test project; verify vault write succeeded; revoke; verify is_active=False

### File ownership (exclusive)

- `services/agent/src/grace2_agent/secrets_handler.py` (NEW)
- `services/agent/src/grace2_agent/server.py` — additive: secret-* envelope routing (~60 lines)
- `services/agent/src/grace2_agent/persistence.py` — ADD `get_secret_value` method (additive)
- `services/agent/pyproject.toml` — add google-cloud-secret-manager dep
- `services/agent/tests/test_secrets_handler.py` (NEW)
- `reports/inflight/job-0124-agent-20260608/`


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

