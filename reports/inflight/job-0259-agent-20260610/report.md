# job-0259-agent-20260610 — Case layers not rehydrating (WRITE path) — FIXED

## Verdict

FIXED (code + 7 Gemini-free regression tests). The user theory ("user-type
related") is REFUTED — it is connection identity, not user identity.
The live agent on :8765 still runs the old code; orchestrator restart
required to take effect (restart explicitly out of this job's scope).

## True root cause (file:line evidence)

The web client mounts TWO WebSocket connections per tab; the server kept
the active-Case context per connection. Case selection landed on one socket,
every tool dispatch + persistence write ran on the other — with
active_case_id=None.

1. web/src/ws.ts:280-305 (job-0159 hub comment, verbatim): "the web client
   mounts TWO GraceWs instances per tab — Chat.tsx (chat panel) and App.tsx
   (map + layer panel + secrets + cases) — each with its own connection."
   web/src/App.tsx:482 and web/src/Chat.tsx:589 are the two new GraceWs(...)
   sites. case-command is emitted via App.tsx's instance (useCases.ts ->
   sendCaseCommand); user-message via Chat.tsx's instance.
2. services/agent/src/grace2_agent/server.py (_make_handler): a fresh
   SessionState is built PER CONNECTION; active_case_id was a per-connection
   dataclass field (old server.py:470).
3. So case-command(select|create) set active_case_id on App's connection
   state only. Chat's connection state — the one that runs
   _invoke_tool_via_emitter, _persist_chat_turn, _persist_case_loaded_layers
   — stayed active_case_id=None forever:
   - _persist_chat_turn no-ops at "if not state.active_case_id: return"
     -> chat never persisted;
   - _persist_case_loaded_layers never invoked (gated on active_case_id)
     -> Case.loaded_layer_summaries stayed [] (the round-5/round-3 plume
     finding, reproduced);
   - the ensure_case_qgs injection for publish_layer never fired ->
     publishes went to the shared default grace2-sample.qgs.
4. Everything LOOKED fine live: the emitter on Chat's connection streams
   session-state and the job-0159 hub fans it out to App's instance -> the
   map renders. Reopen the Case -> get_session_state reads the empty
   persisted record -> empty LayerPanel + empty chat.
5. Additional gap (round-3 plume "publish happened but record never
   updated"): the old persist call sat AFTER the try/finally of
   emit_tool_call — if the post-invoke session-state emission raised on a
   dying WebSocket (browser reload mid-turn), the persist was skipped even
   though the layer had landed.

### Live-system evidence (no mocks)

/tmp/agent_demo_ready.log (the agent the user is demoing against, pid
3213497, file-backed dev persistence at ~/.grace2/dev_persistence):

- EVERY case-open line in the whole log shows chat=0 layers=0 while 31-33
  Cases existed — e.g. the user created+selected+renamed the "Hillshade"
  Case at 12:29:20-33, chatted in it at 12:29:44, and reopening it at
  12:34:56 showed chat=0 layers=0. Even the user message did not persist,
  and there is no "chat-persist failed" error anywhere — the writes
  silently no-opped on the active_case_id gate.
- The flood composer run published to
  qgs_uri=gs://grace-2-hazard-prod-qgs/grace2-sample.qgs (the DEFAULT
  project) — proof the dispatching connection had no Case context.
- Page loads open two sibling connections back-to-back (11:57:08.269 and
  .328) — the Chat.tsx + App.tsx GraceWs pair.

See evidence/demo_log_forensics.txt.

### User theory check (kickoff lead 2) — REFUTED

- Persistence.get_case (persistence.py:343) filters by _id only — no user
  filter on the write path.
- list_cases_for_user (persistence.py:406) includes
  {"user_id": {"$exists": False}} and Cases are created WITHOUT any user
  field — anonymous user-id churn (one anon User per connection; users.json
  has hundreds) does NOT hide cases or layer writes. User identity plays no
  role (OQ-0115-CASE-USER-LINK remains open but orthogonal).

## Fix (services/agent/src/grace2_agent/server.py)

1. Session-scoped active Case: SessionState.active_case_id is now a property
   backed by module-level _SESSION_ACTIVE_CASE keyed by session_id (bounded,
   4096). Both sockets of a tab — and any post-reconnect replacement
   connection — observe the same Case binding. Also fixes silent context
   loss on WS reconnect (client only re-sends session-resume, never
   re-selects the Case).
2. _sync_case_context(websocket, state) — called at the top of every
   user-message dispatch. If this connection's in-memory context
   (chat_history, emitter loaded_layers seed) was last synced to a different
   Case, apply the job-0245 replace-not-reconcile reset and seed the emitter
   from the persisted Case. Closes the cross-socket variant of OQ-0245 (LLM
   context carryover after a case switch on the sibling socket).
3. Persist in finally: _persist_case_loaded_layers moved into the finally of
   _invoke_tool_via_emitter — fires even when the tool or its post-invoke
   envelope emission raised (add_loaded_layer appends BEFORE it emits).
   Wrapped never-raise so it cannot mask the original exception.
4. Merge-by-layer_id in _persist_case_loaded_layers — union of persisted +
   emitter layers (emitter wins per id, append otherwise) so an unseeded
   emitter can never clobber previously persisted summaries.

tests/test_chart_tools.py updated (1 call site) — active_case_id is no
longer a constructor kwarg.

## Proof

tests/test_case_layer_write_path_job0259.py — 7 tests, all Gemini-free,
against the REAL file persistence substrate (tmpdir):

- test_split_brain_two_connections_layer_and_chat_persist — THE root-cause
  regression: case-command(create) on socket A, tool dispatch on socket B
  (same session) -> layer + chat round-trip through get_session_state.
  Fails on the old code.
- test_case_open_after_reconnect_rehydrates_layers — create -> publish ->
  fresh-session case-open rehydration loop.
- test_no_write_without_active_case — projects store untouched.
- test_persist_fires_even_when_post_invoke_emission_fails — round-3 plume
  scenario: WS dies on the session-state emission; layer persists anyway.
- test_merge_preserves_previously_persisted_layers — unseeded emitter
  cannot clobber.
- test_sync_clears_stale_llm_context_on_cross_socket_case_switch — OQ-0245
  cross-socket reset + idempotency.
- test_session_binding_survives_reconnect.

Full agent suite: 4147 passed, 71 skipped; 4 failures are OTHER agents'
in-flight uncommitted churn, pre-existing relative to this job
(data_fetch.py +306 lines uncommitted broke two docstring-tier tests;
publish_layer.py +283 lines uncommitted live-GCS validation broke two
model_flood_scenario tests). None touch this fix.

Evidence: evidence/pytest_regression_run.txt (55 passed across five
case-related suites), evidence/demo_log_forensics.txt.

## SEPARATE INCIDENT flagged for the orchestrator

At 12:38:33 today, mid-demo, the dev persistence store was WIPED:
"case-list emitted ... count=33" at 12:34 -> "count=0" at 12:38:33;
projects.json is now literally {} (mtime 12:38) and sessions.json was
recreated with a single record (created_at 19:38:33Z). users.json survived.
This destroyed the user's 33 Cases and pre-fix forensic material. Most
plausible culprit: a parallel job's script/test binding FileMCPClient/dev
persistence to the DEFAULT ~/.grace2/dev_persistence dir instead of a
tmpdir (tests MUST set GRACE2_DEV_PERSISTENCE_DIR). Not attributable from
the agent log alone — needs an orchestrator-level audit of the concurrent
batch (jobs 0252-0258).

## Open questions / follow-ups

- Restart of :8765 required for the fix to take effect (orchestrator-owned).
- Composer-internal publish_layer calls still write to the default .qgs
  even in a Case context (ensure_case_qgs injection only wraps direct
  publish_layer dispatches) — pre-existing OQ-62 scope, now visible.
- _maybe_autoname_case (server.py, job-0260) is defined but has no caller —
  presumably another in-flight agent's WIP; left untouched.
- Web-side hardening worth a small job: re-send case-command select on
  reconnect from the client (belt-and-braces with the session registry).
