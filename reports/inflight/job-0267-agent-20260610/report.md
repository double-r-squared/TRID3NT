# job-0267-agent-20260610 — report

## Verdict: DONE (all four implementation items + verify matrix green)

## Root cause (confirmed, two independent losses)

1. **Agent narration was never accumulated.** `_stream_gemini_reply`
   forwarded `TextDeltaEvent` deltas to the wire and dropped them;
   `_dispatch_gemini_and_persist` then persisted the agent turn with
   `content=""` and an explicit "reply text not currently accumulated"
   comment (server.py, pre-fix ~line 3270). The web replay
   (`rehydrateMessagesFromCaseOpen`) faithfully rendered the empty string —
   i.e. nothing. Hence "only my own messages replay".
2. **Tool dispatches persisted no replayable record.** Inline tool cards
   render from `pipeline-state` envelopes, which are wire-only. Nothing in
   `_invoke_tool_via_emitter` wrote a per-dispatch record, so a reopened
   Case had no tool cards to replay.

Additionally (item 4): `Persistence.list_cases_for_user` had **no status
filter at all** — deleted/archived Cases reached the `case-list` wire and
only a client-side filter (job-0266) hid them; any other consumer saw
ghosts.

## What changed

### Contract — `packages/contracts/src/grace2_contracts/case.py`

- `CaseChatMessage.role`: `Literal["user","agent","system"]` →
  `Literal["user","agent","system","tool"]` (closed enum extension; the
  kickoff's role="tool" option — chosen as the smallest design because the
  chat collection already interleaves by `created_at` with zero new
  collections/queries).
- New `ToolCardRecord` (`tool_name`, `state: Literal["complete","failed"]`,
  `started_at`, `duration_ms (ge=0)`, `label`) + new optional
  `CaseChatMessage.tool_card: ToolCardRecord | None` — the TYPED payload the
  web renderer consumes (kickoff: "agree on the shape via the contracts,
  not ad-hoc dicts"). `content` of a tool row carries the identical record
  as a JSON string (the kickoff's content=JSON belt-and-suspenders).
  Back-compat: pre-job-0267 docs (no `tool_card` key) validate unchanged
  (test included). Cancelled dispatches persist NOTHING (Invariant 8);
  pending/running are live-wire-only.

### Agent — `services/agent/src/grace2_agent/server.py`

- `SessionState.current_turn_narration: list[str]` — reset at stream start,
  appended on every `TextDeltaEvent` across ALL loop iterations (they share
  one message_id bubble).
- `_dispatch_gemini_and_persist`: persists the JOINED narration as the
  `role="agent"` row in its `finally` (best-effort on cancel/error: whatever
  accumulated before the stream died is persisted; no narration + no clean
  terminal = no phantom row).
- `_persist_tool_card` helper + hook in `_invoke_tool_via_emitter`:
  complete/failed terminal dispatches persist one `role="tool"` row
  (cancelled → none). Timing source of truth: the emitter's new
  `last_tool_step` (the authoritative job-0264 `started_at`/`duration_ms`
  stamps the live card displayed); wall-clock fallback only if the wire died
  before the terminal transition. `label` = registry display name.
  Tool rows carry `layer_emissions=[]` so the turn's layer ids stay
  attributed to the closing agent row exactly as before. Never raises,
  never masks the original exception.

### Agent — `services/agent/src/grace2_agent/pipeline_emitter.py`

- `PipelineEmitter.last_tool_step: PipelineStepSummary | None`, set on every
  terminal transition of `emit_tool_call` (complete/failed/cancelled — set
  on cancel too so the accessor never carries a stale prior step).

### Agent — `services/agent/src/grace2_agent/persistence.py`

- `list_cases_for_user`: Mongo-side `"status": {"$nin": ["deleted",
  "archived"]}` + Python-side guard after validation (covers MCP backends
  whose filter dialect ignores the operator). `$nin` matches docs with no
  status field (pre-status records are live; `CaseSummary.status` defaults
  "active") — test included.
- `get_session_state`: stable Python sort of the validated chat list by
  `(created_at, message_id)` — deterministic full-stream replay order
  regardless of backend sort support; ULID tiebreak preserves write order.
- `FileMCPClient._matches`: `$nin` support (Mongo-faithful: missing field
  matches). Mirrored in the test `MockMCPClient`.

### Web (coordination with job-0266 — it landed before its renderer could
### know this shape)

job-0266 committed (ddfabd4) while this job was in flight; its
`rehydrateMessagesFromCaseOpen` drops all non-user/agent roles, so persisted
tool rows were safe (no crash, no JSON bubbles) but would not RENDER on
reopen. Minimal renderer delta, consuming the typed contract:

- `web/src/contracts.ts`: `ToolCardRecord` interface; `CaseChatMessage.role`
  + `tool_card` mirrors.
- `web/src/Chat.tsx`: `replayStreamFromChatHistory` — single ordered walk of
  `chat_history` on first Case open: user/agent rows → bubbles (seq
  recorded), tool rows → synthesized single-step terminal
  `PipelineStatePayload` appended to `s.pipeline.history` (the exact live
  envelope shape, so replayed cards render through the SAME PipelineCard
  path: green/red tint + authoritative duration). `routeCaseOpen` now calls
  it; `rehydrateMessagesFromCaseOpen` retained (exported, tested,
  back-compat).

## Verification (all Gemini-free, no agent restart, no Playwright)

- **Contracts**: `packages/contracts` pytest **391 passed** (8 new
  job-0267 tests: ToolCardRecord roundtrip/closed-enum/ge-0, tool-role
  roundtrip with JSON twin, tool_card back-compat default, role enum still
  closed, full interleaved CaseSessionState).
- **Agent**: new `tests/test_full_stream_persistence_job0267.py` —
  **13 passed**: narration persists+replays (clean, mid-stream death,
  nothing-said), tool cards persist with duration/label (complete + failed
  + cancelled-none + no-case-no-write), created_at interleave with
  out-of-order inserts, deleted+archived excluded from
  `list_cases_for_user` AND from the emitted `case-list` envelope,
  pre-status legacy docs stay listed, user-turn shape unchanged, and the
  kickoff's E2E: `_prepare_user_turn` → fake stream that narrates +
  dispatches a real registry tool via `_invoke_tool_via_emitter` →
  `_dispatch_gemini_and_persist` → `get_session_state` returns
  user→tool→agent in order with non-decreasing stamps.
- **Agent full suite**: 4288 passed, 5 failed, 72 skipped. The 5 failures
  (3x test_data_fetch docstring-discipline, 2x test_model_flood_scenario
  live-GCS publish guardrail) were **proven pre-existing**: reproduced
  identically on a HEAD worktree + all non-job-0267 uncommitted changes
  synced (i.e. the live tree minus exactly this job's edits) — same 5
  failures. Updated 1 existing assertion
  (test_case_layer_write_path_job0259): a tool dispatch now persists 2 chat
  rows (user + tool card), asserted explicitly.
- **Web**: vitest **582/582** (5 new in Chat.replayToolCards.test.tsx:
  rebuild bubbles+cards, interleave user→tool→agent, failed state, skip
  untyped/unknown rows, routeCaseOpen first-open replay). `tsc --noEmit`: 0
  errors in files this job touched (48 pre-existing errors elsewhere,
  untouched).

## Design decisions (documented per kickoff)

- **role="tool" in the chat collection** over a parallel collection: zero
  new collections/queries; `created_at` (+ULID tiebreak) is the interleave
  key; the contract enum was extended WITH packages/contracts tests as the
  kickoff required.
- **Typed `tool_card` field in addition to content=JSON**: the contract is
  the renderer agreement point; JSON-in-content alone would have been an
  ad-hoc dict for the web.
- **Emitter stamp as timing truth**: `last_tool_step` reuses the job-0264
  authoritative duration instead of a second clock; wall-clock fallback only
  for wire-death paths.
- **One narration row per turn** (kickoff shape): text emitted across loop
  iterations joins into a single `role="agent"` row persisted at the
  loop-terminal site; replay order is user → tool cards → narration.
- **Gemini context unaffected**: `state.chat_history` (the LLM context) is
  cleared on case-open (job-0245 replace-not-reconcile) and never seeded
  from persisted rows, so tool rows can never poison
  `build_contents_from_history`. Verified by reading both seeding paths.

## Known repo-hygiene finding (NOT this job's to fix)

HEAD alone is un-runnable: ~89 source files are untracked but imported by
committed code (e.g.
`packages/contracts/src/grace2_contracts/impact_envelope.py`,
`mongo_collections.py`, `circuit_breaker.py`, `gemini_cache.py`,
`telemetry.py`, many tools/fetch_*.py), and committed tests depend on
uncommitted `tool_registry.py` fields. Surfaced for the orchestrator — a
hygiene commit should land these.

## Live-gate note for the user demo

The agent process on :8765 predates this commit — server-side persistence
of narration + tool rows activates at the orchestrator's end-of-wave
restart. The web renderer delta is HMR-live now and is a harmless no-op
until then (it only consumes rows the old agent never wrote).
