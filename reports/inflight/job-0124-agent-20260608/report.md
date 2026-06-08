# Report: Secrets agent backend — vault write to GCP Secret Manager + ws handlers

**Job ID:** job-0124-agent-20260608
**Sprint:** sprint-12-mega Wave 2
**Specialist:** agent
**Status:** ready-for-audit

## Summary

Landed `services/agent/src/grace2_agent/secrets_handler.py` (the
`handle_secret_add` / `handle_secret_revoke` / `handle_secrets_list`
trio) bridging Wave-1 `grace2_contracts.secrets` envelopes to GCP
Secret Manager + Wave-1.5 `Persistence`. Added
`Persistence.get_secret_value` for Tier-2 fetchers to read live key
values via `vault_ref`. Wired `secret-add` / `secret-revoke` /
`secrets-list-request` envelope routing into `server.py`. The raw
`key_value` is consumed by the handler (written to Secret Manager) and
never echoed in any reply envelope or persisted to MongoDB. Multi-tenant
isolation enforced via `user_id` stamping on the persisted document.

## Changes Made

- **`services/agent/src/grace2_agent/secrets_handler.py` (NEW, ~390 LOC)**
  - `handle_secret_add` — creates Secret Manager secret + version, persists
    vault-ref-only `SecretRecord`, stamps `user_id`, appends `secret-add`
    audit-log row.
  - `handle_secret_revoke` — soft-revoke via `Persistence.revoke_secret`;
    SM entry not deleted (audit trail). Appends `secret-revoke` audit-log row.
  - `handle_secrets_list` — `Persistence.list_secrets_refs` -> typed
    `SecretsListEnvelopePayload` with defensive no-key-value assertion.
  - `SecretError` / `SecretRevokedError` / `SecretNotFoundError` typed errors.
- **`services/agent/src/grace2_agent/persistence.py`**: added
  `Persistence.get_secret_value(secret_ref, *, secret_manager_client=None)`
  — fail-closed on `is_active=False` (raises `SecretRevokedError` before
  vault read). Lazy-imports the GCP SDK. Tolerates legacy `gcp-sm://` prefix.
- **`services/agent/src/grace2_agent/server.py`**: imported the three
  secrets envelope payloads + `secrets_handler` functions; added
  `_emit_secrets_list`, `_handle_secret_add`, `_handle_secret_revoke`
  helpers; wired `secret-add` / `secret-revoke` / `secrets-list-request`
  dispatch in `_make_handler`; exported the three helpers in `__all__`.
- **`services/agent/tests/test_secrets_handler.py` (NEW, ~440 LOC)**: 10
  unit tests + 1 integration test + 1 env-gated live test.
- **`services/agent/pyproject.toml`**: no change — `google-cloud-secret-manager>=2.20,<3`
  was already pinned by an earlier job.

## Invariants Touched

- **Determinism boundary**: preserves.
- **Confirmation before consequence — no cost theater**: preserves. Per
  FR-AS-8 secret writes are not solver runs; the wire and storage
  envelopes carry no cost/quota field.
- **MongoDB MCP canonical persistence**: preserves — every CRUD via
  `Persistence.*`.

## Open Questions

- **OQ-0124-PERSISTENCE-USER-ID-STAMP-API** — Persistence.upsert_secret_ref
  could grow a `user_id` parameter so the user_id stamping happens in one
  round-trip; current impl uses a second `update-one`. Tentative: defer.
- **OQ-0124-SECRETS-LIST-REQUEST-SCHEMA** — `grace2_contracts.secrets`
  doesn't currently register a typed payload for `secrets-list-request`
  (client→server). Current shape: "any object; we read `case_id` if
  present" — kept the schema FROZEN per kickoff. Tentative: add a Wave-3
  schema follow-up.
- **OQ-0124-SECRET-OWNER-CHECK** — `revoke_secret` doesn't verify caller
  ownership; defense-in-depth gap. Tentative: follow-up job adds
  `user_id` filter to `Persistence.revoke_secret`.
- **OQ-0124-LAST-USED-AT-UPDATE** — `get_secret_value` doesn't update
  `SecretRecord.last_used_at` on successful fetch. Tentative: better
  done in the Tier-2 fetcher post-call than inside `get_secret_value`.

## Dependencies and Impacts

- **Depends on:** job-0100 (Wave 1 — secrets envelopes),
  job-0115 (Wave 1.5 — Persistence + audit_log).
- **Affects:** Tier-2 fetcher engine specialists (now have
  `Persistence.get_secret_value`); web client (Wave-3 needs to render
  the secrets panel and fire the three envelopes).

## Verification

### Tests run

`services/agent/tests/test_secrets_handler.py` — 11 pass + 1 skipped:

```
test_secret_add_writes_vault_and_persists_record PASSED
test_secret_add_never_logs_or_echoes_key_value PASSED
test_get_secret_value_returns_original_key PASSED
test_get_secret_value_raises_on_revoked PASSED
test_secrets_list_no_key_value_field PASSED
test_secret_add_appends_audit_log PASSED
test_secret_revoke_appends_audit_log PASSED
test_multi_tenant_isolation_list PASSED
test_secret_add_empty_user_id_fail_closed PASSED
test_secret_add_empty_key_value_fail_closed PASSED
test_full_lifecycle_add_list_use_revoke_list PASSED
test_live_secret_manager_roundtrip SKIPPED (env-gated)
```

Regression: `test_persistence.py` 13 pass + 1 skip;
`test_server_case_handlers.py` 13 pass.

### Live E2E evidence (server.py dispatch transcript)

```
after add: sent=1 envelope; first contains secrets-list=True
after list user-A: env={"type":"secrets-list",...}
after list user-B: env={"type":"secrets-list",...}
user-B sees providers: ['openweathermap']
get_secret_value returned len=18 (matches input: True)
after revoke: sent=1 envs; last is secrets-list=True
after revoke list count=0
audit-log events: ['secret-add', 'secret-add', 'secret-revoke']
LIVE DISPATCH EVIDENCE: PASS
```

Concrete numbers:
1. `secret-add` with `key_value="RAW-LIVE-EBIRD-KEY"` -> reply envelope
   contains "secrets-list" but **does not contain the raw key string**
   (asserted; would have raised AssertionError).
2. Multi-tenant: User A sees `['ebird']`; User B sees `['openweathermap']` only.
3. `Persistence.get_secret_value` returned the original 18-char key verbatim.
4. After revoke: `secrets-list` empty list (`.secrets == []`).
5. `audit_log`: 3 events written total — `['secret-add', 'secret-add',
   'secret-revoke']`.

### Geographic-correctness gate

N/A — no geometry emitted by this job.

### Results: pass.
