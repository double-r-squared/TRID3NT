# job-0266-web-20260610 — PER-CASE CHAT STREAMS (close report)

**Verdict: DONE** — the chat panel is now a per-Case conversation thread.
Switching Cases swaps the entire visible stream; navigating to the Cases
root shows a clean empty composer; streaming envelopes buffer into the
stream of the Case that owns the turn; the job-0262 auto-create flow flips
into the new Case from turn 1; the rail excludes deleted/archived Cases.
577/577 vitest green; 15/15 dev-seam UI assertions green on the live HMR
client (WebSocket stubbed — zero contact with the demo agent on :8765,
zero Gemini).

## Root cause (why the old behavior violated the shape)

Chat.tsx held ONE global conversational state (messages, pipeline
view-model, charts, sandbox maps, arrival-order refs) with partial,
divergent resets sprinkled across the case-open handler (job-0172 flush +
job-0231 charts reset + job-0234 sandbox reset). Three concrete violations:

1. **Navigate-to-root never cleared the chat.** `clearActive()` (breadcrumb
   back) only updated useCases state in App.tsx; Chat had no `activeCaseId`
   input at all, so the outgoing Case's messages stayed painted over the
   Cases root.
2. **Envelopes painted into whatever was visible.** A turn still streaming
   for Case A would land its chunks/tool cards into Case B's view after a
   switch — there was no owning-case routing or buffering.
3. **The rail rendered archived/deleted Cases** (CasesPanel sorted them
   last instead of excluding them), which is how the user saw a deleted
   Case linger.

## Change

### web/src/Chat.tsx — per-Case stream core + component rewiring

- New exported pure core (testable without mounting; Chat can't mount in
  happy-dom): `StreamState` (messages + pipeline + charts +
  sandboxRequests/Results/Decisions/Seqs + arrivalSeq/messageOrder/
  stepOrder + lastError), `ChatStreams { streams: Map<key, StreamState>,
  targetKey }`, `ROOT_STREAM_KEY`, `createChatStreams`, `getStream`,
  `streamKeyFor`, `clearRootStream`, and routers: `routeUserMessage`,
  `routeAgentChunk`, `routePipelineState`, `routeSessionState`,
  `routeError`, `routeChartEmission`, `routeCodeExecRequest`,
  `routeCodeExecResult`, `recordSandboxDecision`, `chartsFromSession`,
  `routeCaseOpen`.
- **Ownership routing**: `targetKey` = the Case visible at submit time;
  every streaming envelope routes to `targetKey`'s stream. A code-exec
  RESULT routes to whichever stream holds its request card (covers
  submit-elsewhere-mid-sandbox). Envelopes for a non-visible Case buffer
  silently — the visible stream's array identities don't change, so no
  repaint and no auto-scroll.
- **`routeCaseOpen`**: null session → reset root (App nulls activeCaseId
  on the same frame). Otherwise: if `targetKey === ROOT_STREAM_KEY`, the
  opened Case ADOPTS the in-flight root turn (job-0262 auto-create flow)
  and the root buffer clears (job-0262 persists the user turn BEFORE
  emitting case-open, so the rehydrated history carries it — thread from
  turn 1). First open this session → stream built from `chat_history` +
  persisted session charts (this also FIXES chart rehydration on Case
  re-open, which previously only landed in App's render-dead chart state).
  Re-open of a buffered Case → in-memory buffer kept (no refetch repaint;
  live tool cards survive).
- Component: `activeCaseId?: string | null` prop selects the visible
  stream (`data-stream-key` attr exposed for tests/snapshots); a
  visibleKey-transition effect clears the root stream on navigate-out
  (case → root) and closes the chart gallery on any swap; ChatInput is
  keyed by visibleKey (clean empty composer per view); all GraceWs
  handlers + dev seams (`__grace2InjectPipelineState/Error/ChartEmission/
  CodeExec`) now route through the same pure core; new dev seam
  `__grace2InjectCaseOpenChat` drives the stream map for UI snapshots.
  This UNIFIES the partial job-0172/0231/0234 case-open resets into the
  single stream-swap path.

### web/src/App.tsx

- Passes `activeCaseId` to `<Chat>`.
- Payload-warning confirmations are now keyed by Case: each warning is
  tagged with the Case active at arrival (`activeCaseIdRef`); only the
  visible Case's warnings render (`visiblePayloadWarnings`); the rest
  buffer until the user returns to that Case. Legacy
  `payload-warning-stack` marker follows the filtered list.

### web/src/components/CasesPanel.tsx

- The rail now EXCLUDES non-active Cases (`filter(status === "active")`),
  sorted most-recently-updated first. Rail refresh on case-list envelopes
  verified: ws.ts `case-list` → App `onCaseList` → `useCases.onCaseList`
  → `setCases` (and re-asserted live in the seam script, step 6).

## Job-0262 hand-off verification (kickoff item 3)

- Rail/App flip: `case-open` is in `SESSION_SCOPED_TYPES` (ws.ts) → both
  sockets receive it → `useCases.onCaseOpen` sets `activeCaseId` → left
  rail flips to Case view (already verified by job-0262).
- Chat flip (NEW): the same envelope hits Chat's `routeCaseOpen` →
  adoption + stream creation; the prop flip selects it. Both happen in the
  same WS message task → React 18 batches → one render, no blank frame.
  Covered by the "auto-create from root" vitest describe block.

## Evidence (reports/inflight/job-0266-web-20260610/evidence/)

- `vitest_full_run.log` — **577 passed / 33 files** (includes new
  `Chat.perCaseStreams.test.tsx`: 16 tests covering stream swap on
  case-open, root-nav clear, owning-case routing/buffering for chunks +
  pipeline + errors + charts + sandbox, auto-create adoption, per-stream
  interleave seqs; and updated `CasesPanel.test.tsx`: exclusion + sort).
- `seam_assertions.log` — 15/15 live dev-seam assertions PASSED.
- Screenshots (live :5173 client, stubbed WS):
  - `per_case_root_clean.png` — root: 2 active rows only (deleted +
    archived injected but excluded), clean composer.
  - `per_case_case_a_stream.png` — Case A: flood messages + completed
    `run_model_flood_scenario` tool card.
  - `per_case_case_b_stream.png` — Case B: wildfire messages; zero Case A
    content.
  - `per_case_case_a_revisit.png` — back to A: buffer intact (messages +
    tool card).
  - `per_case_root_after_nav.png` — breadcrumb back: root clean, no leak.
- New tool: `web/tools/screenshot_job0266_per_case_streams.mjs`
  (re-runnable; exits non-zero on any assertion failure).

## Constraint compliance

- NO Gemini/Vertex; NO live agent contact (WS stubbed in the snapshot
  script; agent on :8765 untouched, not restarted).
- web/src edits were atomic + HMR-live.
- tsc --noEmit: no errors in the files this job owns (pre-existing
  ws.test.tsx / App.impactEnvelope.test.tsx typing errors are untouched
  and reproduce at HEAD).

## Live-gate note for the user demo

After the orchestrator's end-of-wave agent restart, the live acceptance
is: open Case A, chat; open Case B — the thread swaps wholesale; back to
Cases root — clean composer; type from root — the UI flips into the
auto-created Case with your message as turn 1; a deleted Case disappears
from the rail immediately.

## Known limits (by design, per kickoff)

- Envelope→Case attribution uses owning-turn context (active case at
  submit/arrival); envelopes don't carry case_id on the wire. If the
  server ever interleaves turns across Cases on one session, a wire-level
  case_id would be the schema-track follow-up.
- The in-memory buffer is session-scoped; a page reload rehydrates from
  the persisted Case history (chat_history + charts) on next open.
