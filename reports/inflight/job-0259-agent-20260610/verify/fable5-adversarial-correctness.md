# job-0259 adversarial verification — correctness / root-cause lens (Fable 5)

Verifier stance: REFUTE by default. No Gemini calls. All checks re-run from
scratch against commit `ea8feda` ("job-0259: Case layers not rehydrating —
session-scoped active-Case context (two-socket split-brain fix)").

## Verdict: CONFIRM

## 1. Root cause re-derived independently from source (not from the report)

- `web/src/Chat.tsx:589` — `new GraceWs(wsUrl, ...)`; `web/src/Chat.tsx:874`
  sends `user-message` on this instance's own `wsRef`.
- `web/src/App.tsx:482` — a SECOND `new GraceWs(WS_URL, ...)`;
  `web/src/App.tsx:308-314` routes `sendCaseCommand` through it.
- `web/src/ws.ts:280-305` (job-0159 hub comment) documents verbatim that the
  client mounts TWO GraceWs instances per tab; `ws.ts:197`
  (`SESSION_KEY = "grace2.session_id"`) means both share one localStorage
  session_id — confirmed live: both sibling connections in the demo log carry
  session `01KTQK7RA0Y3GDKS3YH40EXHYH`.
- Server side pre-fix (`git show ea8feda^:services/agent/src/grace2_agent/server.py`):
  `_make_handler` builds `SessionState(session_id=...)` PER CONNECTION
  (line ~3354 in current file) and `active_case_id` was a per-connection
  dataclass field. `_persist_chat_turn` short-circuits silently on
  `if not state.active_case_id: return` — no error line, matching the zero
  `chat-persist failed` entries in the live log.

Conclusion: `case-command(create|select)` bound the Case on App.tsx's
connection; every `user-message` dispatch + persistence write ran on
Chat.tsx's connection with `active_case_id=None`. Root-cause claim is
correct and is NOT a symptom patch — the fix moves the binding to the
session scope, which is exactly the granularity at which the client shares
identity.

## 2. Live-log evidence re-checked against /tmp/agent_demo_ready.log (raw, not the runner's excerpt)

- All 34 `case-open` lines show `chat=0 layers=0` while case-list count was
  31-33.
- Decisive sequence reproduced from the raw log: create `01KTSG5DNJQ5...` at
  12:29:20 → case-open 12:29:24 → rename 'Hillshade' 12:29:33 →
  `user-message ... text='show me the hillshade of seattle'` 12:29:44 →
  full geocode/fetch_dem/compute_hillshade/publish_layer chain ran → reopen
  12:34:56 shows `chat=0 layers=0`. No persist-failure errors anywhere.
- `publish_layer` lines (11:54:59, 12:00:57, 12:05:31, 12:31:27) all show
  `qgs_uri=gs://grace-2-hazard-prod-qgs/grace2-sample.qgs` (the default
  project) — consistent with no Case context on the dispatching connection.
- Two sibling `connection open` lines at 11:57:08.269/.328, same session_id.

## 3. Proof re-run from scratch

- `services/agent/tests/test_case_layer_write_path_job0259.py`: 7/7 passed
  (re-run via `.venv/bin/python -m pytest`, 0.71s).
- Case-related suites (`test_case_layer_write_path_job0259` +
  `test_case_layer_persistence` + `test_case_context_reset` +
  `test_case_lifecycle` + `test_chart_tools`): 55/55 passed — matches the
  runner's claim.
- Tests run against the REAL file persistence substrate
  (`make_file_persistence` → `FileMCPClient` JSON files in a pytest tmpdir),
  read back through the same `get_session_state` path the live server uses.
  Not mocks.

## 4. Vacuous-test check — behavioral A/B against the PRE-FIX code

Wrote an independent repro (`/tmp/prefix_repro_job0259.py`) using only APIs
that exist both pre- and post-fix (case-command create on socket A, chat
persist + tool dispatch on socket B, same session_id, real file persistence),
then swapped `server.py` to `ea8feda^` and back:

- PRE-FIX:  `socket B active_case_id = None`, readback `layers=0 chat=0`
  → exactly the production signature (PREFIX-STYLE FAILURE, exit 1).
- POST-FIX: `socket B active_case_id = <case>`, readback `layers=1 chat=1`
  (FIX-STYLE SUCCESS, exit 0).

The regression tests exercise this same path — they are NOT vacuous; the
core split-brain test would fail on the pre-fix code.

(`server.py` restored to HEAD afterwards; `git status` clean.)

## 5. User-theory refutation verified

`services/agent/src/grace2_agent/persistence.py:343` — `get_case` filters by
id only; `persistence.py:406-428` — `list_cases_for_user` ORs in
`{"user_id": {"$exists": False}}`. Anonymous-user churn (each connection
minting an implicit anon id before the sticky rebind, visible in the log)
cannot gate Case reads or writes. The runner's REFUTE of the user theory is
correct.

## 6. Residual paths examined (none refute the fix; none reproduce the original failure)

1. **Agent restart mid-session**: `_SESSION_ACTIVE_CASE` is in-memory;
   `_handle_session_resume` does not rebind the active Case, and the client's
   reconnect replays `session-resume` but not `case-command(select)`. After a
   server restart, writes silently no-op again until the user re-clicks a
   Case. Pre-existing in nature (pre-fix there was no working cross-socket
   binding at all) and outside the observed failure (no restart between
   12:29:20 and 12:34:56). Recommend a follow-up: rebind from the D.6 session
   record on session-resume, or have the client replay select on reconnect.
2. **Multi-instance deployment**: a module-level dict is per-process; two WS
   connections landing on different Cloud Run instances would re-split. This
   matches the existing single-process architecture (per-connection emitters,
   in-memory chat history) — the agent currently runs as one local process
   for the demo and infra defines no multi-instance agent service. Sticky
   sessions are a pre-existing deployment constraint, not a regression.
3. **Two tabs, one localStorage session_id**: post-fix they share one active
   Case (a select in tab 1 redirects tab 2's writes + clears its LLM context
   on next dispatch via `_sync_case_context`). Coherent with session-scoped
   semantics; not the reported bug.
4. **12:38:33 dev-store wipe (case-list 33→0)**: separate incident, properly
   escalated in the runner's report.md §"SEPARATE INCIDENT"; not caused by
   and not claimed fixed by this job.
5. The live demo agent (pid 3213497) still runs PRE-fix code — kickoff
   forbade restarting it; orchestrator must restart to activate the fix.

## Commands re-run

```
.venv/bin/python -m pytest tests/test_case_layer_write_path_job0259.py -v   # 7 passed
.venv/bin/python -m pytest tests/test_case_layer_write_path_job0259.py \
  tests/test_case_layer_persistence.py tests/test_case_context_reset.py \
  tests/test_case_lifecycle.py tests/test_chart_tools.py                    # 55 passed
git show ea8feda^:services/agent/src/grace2_agent/server.py > src/.../server.py
.venv/bin/python /tmp/prefix_repro_job0259.py   # PREFIX-STYLE FAILURE (exit 1)
git checkout -- src/.../server.py
.venv/bin/python /tmp/prefix_repro_job0259.py   # FIX-STYLE SUCCESS (exit 0)
```
