# job-0266 adversarial verification (refute-by-default) — 2026-06-10

Verifier: independent subagent. Lens: correctness re-derivation + full test
re-run + evidence audit. Constraints honored: NO Gemini, NO Playwright
(seam script inspected, not re-run; screenshots audited visually).

## Verdict: CONFIRM (with findings, none blocking)

## Re-runs from scratch

| Run | Result | Log |
|---|---|---|
| Full vitest at HEAD (1d027cf, working tree) | **582/582, 34 files** | `verify/vitest_rerun_head.log` |
| Full vitest at the 0266 commit (clean worktree of ddfabd4) | **4 FAILED files / 24 passed; 430 tests passed, 0 test failures** | `verify/vitest_rerun_at_0266_commit.log` |
| Same worktree + the 4 untracked components copied in | **523/523, 28 files** | (re-run inline) |
| Targeted `Chat.perCaseStreams.test.tsx` + `CasesPanel.test.tsx` at HEAD | **51/51** | (inline) |

The claimed "577/577 across 33 files" reproduces exactly as the *working
tree* state: 523 committed tests + 54 tests in 5 untracked test files
(= 577), 28 committed files + 5 untracked (= 33). HEAD working tree is
582/34 because job-0267 added one file/5 tests.

## Directed attacks — outcomes

### 1. Stream cross-contamination (A's envelope painting into B)
Re-derived the routing core (`Chat.tsx` lines ~480-760 at ddfabd4):
`targetKey` is set ONLY by `routeUserMessage` (submit time) and by
`routeCaseOpen` adoption (only from the `__root__` sentinel). Opening
Case B mid-turn does NOT move ownership; chunks/pipeline/errors/charts
buffer into A's stream (test "streaming envelopes follow the turn
submitted in Case A even after Case B opens" asserts both presence in A
AND absence in B — not vacuous). Code-exec results resolve in the stream
holding the request card via map scan (covers submit-elsewhere-mid-
sandbox). **Residual window (documented in report.md known-limits and
blessed by the frozen kickoff's "case context = active case at
arrival")**: `targetKey` is a single pointer, so a submit in B while A's
frames are still on the wire (~1 RTT) routes those frames to B. The
server cancels the in-flight generation on every new user-message
(server.py "simple M1 policy"), so the stray frame is A's cancelled
`llm_generation` pipeline-state — which renders invisibly (thinking steps
are filtered from the card stack; `isThinkingActive` returns false for
state `cancelled`). Practical impact ~nil. Wire-level case_id remains the
correct schema follow-up. ATTACK FAILED (within blessed semantics).

### 2. Replay ordering
Rehydration (`rehydrateMessagesFromCaseOpen`) preserves `chat_history`
order; `recordMessageSeqIn` assigns seqs 1..N in that order (test asserts
m1→1, m2→2). Persisted message ids are ULIDs (`new_ulid()` at server.py
:717/:837/:2049), so no collision with the client's positional
`user-${len}` ids (len is monotonic). Interleave seqs are per-stream
(test asserts B's counter restarts at 1). ATTACK FAILED.

### 3. Deleted-case ghosts
`CasesPanel.tsx` filters `status === "active"` against the CLOSED
`CaseStatus = Literal["active","archived","deleted"]` (contracts/case.py
:77) with pydantic default `"active"` — the field always serializes, so
the filter cannot spuriously empty the rail. Deleting/archiving the
ACTIVE case: server emits refreshed `case-list` → `useCases` effect
(useCases.ts ~:218) clears `activeCaseId` when the active case is absent
or non-active → Chat flips to root → visibleKey-transition effect resets
the root stream. The orphaned stream stays in the in-memory map,
unreachable — harmless. Rail refresh on case-list verified by test + seam
step 6. ATTACK FAILED.

### 4. Auto-create → flip-into-case hand-off (job-0262)
Server (`_prepare_user_turn`) persists the user turn and emits
`case-open` BEFORE the turn task streams; `case-open` is in
`SESSION_SCOPED_TYPES` so the hub delivers it to both GraceWs instances
exactly once each (App flips `activeCaseId`; Chat's `routeCaseOpen`
adopts: `targetKey === "__root__"` → re-point to the new case +
`clearRootStream`; first-open builds the stream from `chat_history`,
which carries the typed message — thread from turn 1). Dedicated test
covers the full sequence including subsequent chunk/pipeline routing into
the new Case and the root buffer being empty. The no-adopt guard
(`targetKey` ≠ root) is separately tested. Residual race: typing at root
then clicking an existing Case before the auto-create case-open arrives
would mis-adopt (sub-second window, same blessed-attribution family as
finding 1). ATTACK FAILED (within blessed semantics).

## Evidence audit
- `evidence/seam_assertions.log`: 16 VERIFY-OK lines (claim says 15/15 —
  understated by one; trivial). Script
  `web/tools/screenshot_job0266_per_case_streams.mjs` audited: WebSocket
  fully stubbed via `addInitScript` (CapturingWS — nothing reaches
  :8765), 16 real DOM assertions, `process.exit(1)` on any failure.
- Screenshots audited visually (3 of 5): Case A shows flood thread +
  green `run_model_flood_scenario` card; Case B shows wildfire thread
  with zero flood content; root-after-nav shows clean composer + exactly
  2 active rail rows. Match claims.
- job-0267 (HEAD) regression: 0266 core intact (routeCaseOpen/targetKey/
  ROOT_STREAM_KEY present), 51/51 targeted + 582/582 full.

## Findings (for the orchestrator)
1. **[major, PRE-EXISTING — not 0266's diff] Non-hermetic commits.** A
   clean checkout of ddfabd4 (and of its parent) FAILS 4 test files:
   committed `Chat.tsx` imports `./components/ThinkingIndicator`, but
   `ThinkingIndicator.tsx` was never committed in ANY ref (`git log
   --all` empty) — likewise `ImpactPanel.tsx`,
   `RoutingQualityDashboard.tsx`, `ToolsCatalogPopup.tsx` + 5 test files
   (all still untracked at HEAD, dating to wave-4-10/4-11 jobs). Every
   green "full vitest" evidence number in recent jobs is working-tree-
   only. A fresh clone is broken. Action: commit the orphaned web files.
2. **[minor]** Claim says 15/15 seam assertions; the log shows 16.
3. **[minor]** App.tsx payload warnings are tagged with the case active
   at ARRIVAL, while Chat routes by case active at SUBMIT — both match
   the kickoff's wording but the two attributions can diverge for a
   warning arriving after a mid-turn case switch (gate then renders in
   the wrong Case's view; bounded, user can still decide).
4. **[info]** Residual single-pointer `targetKey` contamination window
   (~1 RTT on submit-in-other-case) — already documented in report.md
   known-limits; invisible in practice because the stray frame is a
   cancelled thinking step. Wire-level case_id is the right fix.
