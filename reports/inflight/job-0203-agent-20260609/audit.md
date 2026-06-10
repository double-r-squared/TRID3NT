# Kickoff (frozen)

**Job:** job-0203-agent-20260609 — M4: Cases/sessions/users CRUD migration to MongoDB MCP (Wave 4.11 carry-over, executed during sprint-13 window)
**Specialist:** agent — **executed orchestrator-direct by user authorization 2026-06-09** ("For the next heavy high impact single agent job can you accomplish the job?" → "Yes please"). Authorship: orchestrator (Fable 5 main loop). Adversarial verification remains with independent agents (4-lens Opus panel, reviewer ≠ author preserved).
**Adversarial-verify:** YES — 4 lenses (correctness / regression / contract / live-verify), refute-by-default, ≥3-of-4 to advance, one fix round.

## Pre-implementation audit findings (what M4 actually is)

The manifest scope ("migrate Cases/sessions/users CRUD from bespoke wrappers to MCP; delete old wrapper code") was written before Wave 1.5 job-0115 / Wave 4.6 job-0161 landed. Reality on disk 2026-06-09:

- `persistence.py` is ALREADY the typed MCP-routed wrapper: Cases (get/upsert/list/archive/delete), chat append + session-state hydration, Users (firebase_uid/id/upsert), Secrets refs, audit append — all via `mcp_client.call_tool`. The manifest's `case_store.py`/`session_store.py`/`user_store.py` never existed.
- `FileMCPClient` (job-0161) is the LOCAL DEV substrate behind the same `MCPClientProtocol` — it is NOT a bespoke wrapper to delete; it is the dev seam the live `MCPClient` replaces in production.

**Remaining genuine M4 gaps (this job's scope):**

1. **D.6 session record never written.** `SESSIONS_COLLECTION` is declared; `SessionDocument` (D.6, collections.py:351) exists with TTL contract (`SESSIONS_TTL`, expires_at + 30d); but no code ever creates/updates a session record. job-0230 (today) started `$push`-ing chart records onto `sessions` docs — creating headerless documents via upsert. M4 makes the session record real:
   - `Persistence.upsert_session_record(doc: SessionDocument)`
   - `Persistence.touch_session(session_id, *, client_fingerprint=None, case_id=None)` — single upsert round-trip: `$set last_active_at/expires_at`, `$setOnInsert _id/schema_version/created_at`, `$addToSet project_ids` (when case_id given)
   - `Persistence.get_session_record(session_id)` — tolerant read (drops storage-only extras like the `charts` array, same normalization discipline as `_doc_to_case_summary`)
   - server.py wiring (Phase 2): fire-and-forget touch on (a) WS auth bind, (b) case-open/create, (c) every persisted chat turn.
2. **Mode-2 audit log is the bespoke store to remove (remove-don't-shim).** `mode2_classifier.append_audit_log` writes local JSONL (`~/.grace2/mode2_audit.log`) bypassing MCP. Migrate: server.py call site routes through `Persistence.append_audit("mode2-candidate", …)` (audit_log collection, D.15); DELETE `append_audit_log` + `default_audit_log_path` from mode2_classifier.py; update its tests. When Persistence is unbound (explicit CI path) the event is logged-and-dropped — same policy as telemetry (M3) and charts (job-0230).
3. **FileMCPClient operator gap (live bug).** `update-one` supports only `$set`+upsert. job-0230's chart `$push` is SILENTLY DROPPED on the dev substrate (empty `$set` upsert creates `{_id}` only). Fix: support `$push`, `$setOnInsert`, `$addToSet` with Mongo-faithful semantics ($setOnInsert applies only on insert; $addToSet dedupes; $push appends to possibly-missing array).
4. **Live MCP path verification.** `init_persistence_from_env` → `MCPClient.start` (stdio npx mongodb-mcp-server, SRV from Secret Manager). On this box: no gcloud ADC → Secret Manager BLOCKED-ENV. Verify what is verifiable: real `mongodb-mcp-server` npm package JSON-RPC handshake (initialize + tools/list) via `MDB_MCP_CONNECTION_STRING` pointing at a bogus-but-well-formed URI (the server starts lazily; protocol round-trip is real evidence). Atlas-connected CRUD remains a documented user-unblock step.

## Out of scope (explicitly)

- `list_cases_for_user` `$exists:False` backward-compat hole (every user sees pre-Auth cases) — OQ-0115-CASE-USER-LINK, owned by sprint-13.5 Auth track.
- `SessionDocument.chat_history` duplication question (chat canonically lives in `case_chat_messages` per FR-MP-6; the session-doc field stays empty at v0.1, documented).
- case_lifecycle.py GCS `.qgs` lifecycle (not Mongo CRUD).

## Phasing (concurrency discipline)

- **Phase 1 (now):** persistence.py (session methods + FileMCPClient operators) + new tests. No server.py/mode2 edits — Stage 2 Track CH has uncommitted server.py edits on disk.
- **Phase 2 (after Stage 2 workflow commits):** server.py session-touch wiring + mode-2 call-site swap + mode2_classifier deletion + test updates.
- Commit per phase, surgical `git add` of owned files only.

## File ownership

`services/agent/src/grace2_agent/persistence.py`, `services/agent/src/grace2_agent/mode2_classifier.py` (deletion), `services/agent/src/grace2_agent/server.py` (Phase 2 surgical: session-touch wiring + mode-2 call site + import line), `services/agent/tests/test_persistence_sessions.py` (new), `services/agent/tests/test_mode2_audit_mcp.py` (new), existing mode2 test updates.

## Acceptance

- [REQUIRED] Session-record round-trip live on the FileMCPClient substrate: touch → get → SessionDocument validates; second touch advances `last_active_at`/`expires_at`, preserves `created_at`, dedupes `project_ids`; chart `$push` then `get_session_record` still validates (extras dropped).
- [REQUIRED] `$push`/`$setOnInsert`/`$addToSet` Mongo-faithful semantics covered by tests (incl. the job-0230 chart-drop regression: `$push` on the dev substrate now lands).
- [REQUIRED] Mode-2 candidate audit lands in `audit_log` collection via Persistence; JSONL writer deleted; no caller remains (grep-clean).
- [REQUIRED] Real `mongodb-mcp-server` JSON-RPC handshake evidence (initialize + tools/list) OR documented BLOCKED-ENV if npx/npm fetch unavailable.
- [REQUIRED] Full services/agent pre-existing suite: no new failures.
- NO Gemini calls anywhere in this job. NEVER push.
