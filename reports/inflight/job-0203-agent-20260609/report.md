# job-0203 report — M4: Cases/sessions/users CRUD migration to MongoDB MCP

**Executed:** 2026-06-09, orchestrator-direct (user-authorized), Fable 5 main loop.
**Verdict:** PASS (one BLOCKED-ENV item documented: Atlas-connected verification).
**State:** READY_FOR_AUDIT — 4-lens adversarial panel required before ADVANCE.

## Headline finding (the reason this job existed)

**The entire Persistence layer was written against a fictional MCP tool surface.** Live protocol evidence (`evidence/mcp_protocol_smoke.log`) against `mongodb-mcp-server@latest` (the real npm package, via the production `MCPClient` stdio JSON-RPC path): the server exposes **no `find-one`, `insert-one`, or `update-one` at all**. Its document surface is `find` / `insert-many` / `update-many` (+ `delete-many`, `count`, ...; 18 tools read-only, 29 read-write). Three additional compounding breaks:

1. **Response shape**: `find` results come back EJSON-stringified inside `<untrusted-user-data-{uuid}>` tags in the SECOND content entry; the old `_unwrap_mcp_result` parsed the first entry (a "Found N documents" banner) — read path returned garbage.
2. **Default limit 10**: the real `find` defaults to `limit=10`; our unbounded logical reads (chat history, case lists) would silently truncate at 10 documents.
3. **Read-only default**: `MCPClient.start` sets `MDB_MCP_READ_ONLY=true` (job-0015 hello-world safety) — in that mode write tools aren't even exposed, so every production write would fail.

Every one of these would have surfaced as a production data-loss/corruption incident on first deployment. None was catchable by the existing test suite because `FileMCPClient` and every mock implement the same fictional surface.

## Resolution architecture

`MCPSurfaceTranslator` (persistence.py) — a single translation boundary implementing `MCPClientProtocol`, wrapping the live `MCPClient`:
- `find-one` → `find {limit:1}` → `{"document": doc|None}`
- `find` → `find` with explicit `limit=1000` + `responseBytesLimit=8MiB` → `{"documents":[...]}`
- `insert-one` → `insert-many {documents:[doc]}`
- `update-one` → `update-many` (all GRACE-2 updates filter unique keys; semantics coincide)
- Untrusted-tag EJSON extraction (`_extract_untrusted_payload`) with newline-anchored regex — the warning prose mentions both tags inline BEFORE the payload block; a lazy match captures the prose word "and" instead of the documents (found live, fixed, regression-tested with the verbatim real format).
- `_ejson_normalize` collapses `$oid`/`$date`/`$numberLong|Int|Double` wrappers.

The logical surface (`find-one`/`insert-one`/`update-one`/`find`) is OUR seam contract: `FileMCPClient`, all 23 call sites across 6 modules, and every test mock keep speaking it unchanged. When MongoDB renames tools again, the translator is the only file that changes. `init_persistence_from_env` wires `Persistence(MCPSurfaceTranslator(MCPClient))` and sets `MDB_MCP_READ_ONLY=false` (explicit env still wins; FR-AS-8 confirmation policy is enforced at OUR layer via CONFIRMATION_TRIGGERS, not by crippling the server).

## D.6 session record goes live

- `Persistence.touch_session(session_id, client_fingerprint=, case_id=)` — one upsert: `$set last_active_at/expires_at` (TTL per `SESSIONS_TTL`), `$setOnInsert schema_version/created_at`, `$addToSet project_ids`; plus a header-repair pass (a doc created by an early chart `$push` is headerless FOREVER under real Mongo `$setOnInsert` semantics — detected and repaired with `created_at=now` best-approximation).
- `Persistence.upsert_session_record` / `get_session_record` (tolerant read: storage-only extras like the job-0230 `charts` array dropped before `SessionDocument` validation).
- server.py wiring: heartbeat on auth bind (both handshake paths), case create, case open/select, and every persisted chat turn. All best-effort, never raise.

## Mode-2 audit migration (remove-don't-shim)

- `mode2_classifier.append_audit_log` + `default_audit_log_path` (bespoke `~/.grace2/mode2_audit.log` JSONL writer — the last CRUD path bypassing MCP) **deleted**.
- server.py call site routes through `Persistence.append_audit("mode2-candidate", ...)` → `audit_log` collection (D.15). Unbound Persistence → logged-and-dropped (same policy as telemetry M3 / charts job-0230). Grep-clean: no caller remains.

## FileMCPClient operator gap (live bug fixed)

`update-one` honored only `$set` — **job-0230's chart `$push` was silently dropped on the dev substrate** (upsert created a bare `{_id}` doc; the chart vanished). `_apply_update` now implements `$set` / `$setOnInsert` (insert-only) / `$push` / `$addToSet` (dict-equality dedupe) Mongo-faithfully; unknown operators raise loudly.

## Evidence (all in evidence/)

| Artifact | What it proves |
|---|---|
| `m4_real_roundtrip.log` + `m4_roundtrip.py` | **GOLD**: 6/6 CRUD assertions against REAL mongod 7.0.14 + REAL mongodb-mcp-server + production MCPClient + translator + typed Persistence: Case upsert/get/list, session touch×2 ($setOnInsert held, activity advanced, project_ids deduped), chart `$push` coexisting with header, User round-trip, mode2 audit append+read |
| `mcp_protocol_smoke.log` + `mcp_protocol_smoke.py` | Real server tool surface: 18 read-only / 29 read-write tools; `find-one`/`insert-one`/`update-one` ABSENT in both modes |
| `mcp_schema_dump.py` | Authoritative inputSchemas for find/insert-many/update-many/count/delete-many (limit default 10, upsert flag, responseBytesLimit) |

Tests: 38 new across `test_mcp_surface_translator.py` (13), `test_persistence_sessions.py` (17), `test_mode2_audit_mcp.py` (3), mode2 updates. Full M4-adjacent set 141 passed / 1 skipped. Full services/agent suite: 585 passed, 10 skipped, **1 pre-existing failure** (`test_data_fetch.py::test_fetch_landcover_docstring_records_access_tier` — uncommitted Wave 4.10 docstring working-tree state in data_fetch.py, untouched by this job).

## Ownership notes

- `mcp.py` (2-line lazy-import fix): out of declared ownership, orchestrator-granted in-flight — the module-level `google.cloud.secretmanager` import made `MCPClient` unusable on dev boxes; moved inside `fetch_srv_from_secret_manager` (same pattern as `Persistence.get_secret_value`).
- server.py edits were sequenced AFTER Track CH's job-0230 commit (4f78f5c) to avoid sweeping concurrent work.

## BLOCKED-ENV / user unblock

Atlas-connected verification (real SRV from Secret Manager): needs gcloud ADC on this box. Runbook: `gcloud auth application-default login` → `GRACE2_MONGO_MCP_STDIO=1` agent start → create a Case in the UI → verify documents in Atlas (`projects`, `sessions`, `audit_log`). The local-mongod round-trip covers the full code path except Atlas transport/auth.

## Open questions

- **OQ-0203-FIND-PAGINATION**: translator caps logical `find` at limit=1000 / 8MiB responseBytesLimit. Chat histories beyond that need cursor pagination (post-v0.1; sessions are 90-turn-capped today so headroom is ~10×).
- **OQ-0203-MCP-VERSION-PIN**: the npm server's tool surface changed names at least once historically. Production should pin `mongodb-mcp-server@<version>` in the deploy (sprint-13.5 infra) and re-run `evidence/mcp_protocol_smoke.py` on every bump.
- **OQ-0115-CASE-USER-LINK** (carried): `list_cases_for_user` `$exists:False` backward-compat clause still shows pre-Auth cases to all users — owned by sprint-13.5 Auth track.
