# job-0262-agent-20260610 — AUTO-CREATE CASE FROM ROOT (close report)

**Verdict: FIXED** — a chat prompt sent from the Cases root now mints a
prompt-named Case server-side BEFORE the turn dispatches; chat persistence +
layer attribution land in it, and `case-open` + `case-list` flip the web UI
into the Case view. 12 new tests green; full agent suite green modulo 5
pre-existing failures owned by concurrent jobs (see Evidence).

## Root cause

`server.py`'s `user-message` dispatcher treated `active_case_id is None` as
the legitimate M1 stateless path. Every persistence side-effect
(`_persist_chat_turn`, `_persist_case_loaded_layers`, `ensure_case_qgs`,
`publish_layer` case_id injection) silently no-ops without a Case, so a
root prompt produced results attributed to nothing, and the UI stayed on
the Cases root (no `case-open` ever emitted).

## Change (services/agent/src/grace2_agent/server.py — 3 hunks)

1. **`_auto_create_case_from_root`** (new): persistence-bound? -> mint
   `CaseSummary` named via `_derive_case_title(prompt)` (job-0260
   heuristic; "Untitled Case" fallback) -> `upsert_case` -> set
   session-scoped `active_case_id` + mark `case_context_synced_to` ->
   register in `_AUTONAMED_CASES` (creation already consumed the prompt;
   skip the job-0260 rename probe) -> D.6 session touch -> flush emitter
   layer accumulator. Deliberately NOT the `case-command(create)` reset
   path: NO `chat_history` clear, NO `turn_count` reset — the in-flight
   message IS the Case's first turn. Upsert failure -> return None ->
   stateless path continues (never blocks the turn).
2. **`_emit_auto_case_open`** (new): emits `case-open` (hydrated
   `CaseSessionState`) then `case-list`. Called AFTER the user turn is
   persisted so the rehydration carries the first message — Chat.tsx's
   case-open handler is replace-not-reconcile (flush + re-render from
   `chat_history`); emitting earlier would blank the just-typed bubble.
   On rehydration failure: SKIP case-open (a `session_state=None` frame
   would null the client's `activeCaseId`), still emit case-list.
3. **`_prepare_user_turn`** (new, extraction): the pre-dispatch sequence —
   `_sync_case_context` -> auto-create (non-directive prompts only;
   `/invoke` debug directives stay stateless) -> `_persist_chat_turn(user)`
   -> conditional `_emit_auto_case_open` — returning the parsed `/invoke`
   directive. The dispatcher's `user-message` branch now calls this single
   seam before creating the turn task, so the dispatched turn (Gemini
   stream or directive) always observes the final Case context. Behavior
   for the existing-case and stateless paths is the same sequence as
   before.

`__all__` extended with the three helpers.

## Web side — verified, NO change needed

- `web/src/ws.ts`: `case-open` is in `SESSION_SCOPED_TYPES` (~line 311) ->
  the job-0159 hub fans the frame from Chat's socket to App's socket.
- `web/src/App.tsx` (~492): `onCaseOpen` -> `useCases.onCaseOpen` ->
  `setActiveCaseId(session.case.case_id)` (`web/src/hooks/useCases.ts`
  ~146) -> App's left rail flips `cases-list` -> `case-view` (App.tsx ~649
  / ~672 branch on `activeCaseId`).
- `web/src/Chat.tsx` (~609): case-open flush + rehydrate — satisfied by
  the persist-before-emit ordering above.

No web/src edits -> no vitest run required per kickoff.

## Evidence (Gemini-free, per directive)

New: `services/agent/tests/test_auto_create_case_job0262.py` — 12 tests,
all passing (`.venv/bin/python -m pytest tests/test_auto_create_case_job0262.py`
-> `12 passed`):

- Case created + prompt-named + ACTIVE when `_prepare_user_turn` returns
  (i.e. before the LLM turn task starts); connection marked synced.
- User turn persisted as the new Case's first chat message.
- Envelope order `case-open` -> `case-list`; case-open rehydration carries
  the first message; case-list lists the new Case.
- Layer attribution: post-create `current_turn_layer_ids` ->
  `layer_emissions` on the agent turn inside the new Case; emitter
  accumulator flushed.
- No job-0245-style reset: `turn_count` + prior `chat_history` untouched.
- Degenerate prompt -> "Untitled Case"; job-0260 rename probe no-op.
- Existing-case path unchanged (no second Case, no case-open churn).
- `/invoke` directive stays stateless; Persistence-unbound stays stateless;
  upsert failure falls back stateless.
- Integration repro: two consecutive root prompts -> exactly ONE Case with
  both turns; case-open emitted once.

Regression: case suites (`test_server_case_handlers`,
`test_case_context_reset`, `test_case_layer_write_path_job0259`,
`test_case_lifecycle`, `test_case_layer_persistence`,
`test_auth_handshake`) -> 50 passed. Full suite
(`pytest tests/ -q`) -> **4275 passed, 5 failed, 72 skipped, 1 xfailed**.
The 5 failures are pre-existing and reproduce with this job's changes
absent (verified via `--ignore` run -> identical failure set):
`test_data_fetch` docstring tier x3 + `test_model_flood_scenario` x2
(live-GCS-dependent; LAYER_URI_NOT_FOUND). An earlier 11-failure run was
concurrent-edit churn — job-0261 was rewriting `test_categories.py` /
`fetch_nws` tests mid-run (failure names from that run no longer exist on
disk).

## Commit hygiene (important for the audit)

`server.py`'s working tree carried IN-FLIGHT job-0263 hunks (uri_registry
layer-handle work, uncommitted). This job staged ONLY its 3 hunks via
`git apply --cached` on a filtered patch; the staged blob compiles
(`py_compile`), references zero job-0263 symbols, and every symbol it uses
exists at HEAD.

**Environmental finding for the orchestrator** (pre-existing, out of
scope): dozens of agent sources are UNTRACKED (`git status --porcelain |
grep '^??'`): `gemini_cache.py`, `telemetry.py`, `circuit_breaker.py`,
`uri_registry.py`, ~20 fetch tools, many Wave 4.10/4.11 test files. HEAD
is NOT a runnable state of the agent service — Wave 4.10/11 jobs committed
modified files but never `git add`ed their new files. Recommend a sweep
job to track + commit them before any clone/CI attempt.

## Live-gate note

User is the live gate (no Playwright per directive). Agent on :8765 was
NOT restarted; the fix takes effect after the orchestrator's end-of-wave
restart. Live acceptance: from the Cases root, send any prompt -> left
rail flips into a Case named from the prompt, chat shows the message,
layers attribute to the Case.
