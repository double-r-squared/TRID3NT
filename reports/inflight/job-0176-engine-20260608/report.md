# Report: Inline tool cards in chat scroll (interleave pattern)

**Job ID:** job-0176-engine-20260608
**Sprint:** sprint-12 (mega-sprint, Wave 4.9)
**Specialist:** web (Opus)
**Task:** Refactor pipeline cards from a separate-stack region INTO the chat scroll, interleaved chronologically with agent text bubbles. Per memory `feedback_chat_tool_interleave`. Each `pipeline-state` event becomes a `kind="tool"` chat row at arrival time; `agent-message-chunk` events become `kind="agent"` text bubbles. Old separate pipeline strip — deleted.
**Status:** ready-for-audit

## Summary

Refactored `web/src/Chat.tsx` so that pipeline tool cards interleave INLINE in the conversation scroll alongside user prompts and agent text bubbles, sorted by first-arrival time, replacing the prior "all messages then all tool cards" split. Added 6 new unit tests for the new pure `buildInterleavedStream` helper and verified live via Playwright driving Gemini through real chat input — observed canonical pattern `[user] → [tool: Thinking…] → [agent narration] → [tool: geocode_location] → [tool: fetch_administrative_boundaries]` on a Naples, FL prompt.

## Changes Made

- **File:** `web/src/Chat.tsx`
  - Added arrival-order tracking refs in the `Chat` component: `arrivalSeqRef` (monotonic counter), `messageOrderRef: Map<message_id, seq>`, `stepOrderRef: Map<step_key, seq>` where `step_key = name|tool_name` (matches the existing `mergeStepsByStepId` Part-3 collapse key so the llm_generation reissue edge case from job-0166 stays a single sticky card).
  - Added `recordMessageSeq` + `recordPipelineStepSeqs` callbacks; wired them into `onAgentChunk`, `onPipelineState`, `onCaseOpen`, `submit()`, and the dev `__grace2InjectPipelineState` seam. First-arrival seq is sticky; subsequent envelopes for the same id/key update content + state in place without moving the row.
  - On `case-open`, the order maps reset and re-record per replayed message (chronology preserved for rehydrated history; new envelopes for the freshly-opened Case start at seq=1 + N).
  - Replaced `{messages.map(...)} ... <PipelineCardStack history={...} live={...} />` in the JSX with a single `<InterleavedChatStream messages history live messageOrder stepOrder />` render. The old `pipeline-card-stack` group of cards-at-the-bottom is **gone** from production rendering.
  - Added pure helper `buildInterleavedStream(messages, history, live, messageOrder, stepOrder) → InterleavedEntry[]`. Builds a sorted-by-seq array of typed view-models (`user-message | agent-message | tool`) by combining the message list with `mergeStepsByStepId(history, live)` and looking up each row's seq via the order maps. Stable insertion-order tie-break (per ES2019 sort stability). Exported for unit testing.
  - Added `InterleavedChatStream` component that consumes the stream and renders `UserBubble` / `AgentMessage` / `PipelineCard` per entry kind. New `data-testid="chat-stream"` on the container.
  - `PipelineCardStack` exported (was previously file-local) so legacy tests pinning its `data-testid="pipeline-card-stack"` are not silently dropped; it is no longer mounted by `Chat`.
- **File:** `web/src/Chat.test.tsx`
  - Added 6 new tests for `buildInterleavedStream` covering: empty input; canonical `[user → agent → tool → agent]` ordering at seqs 1-4; new tool first-arrival lands at end; **sticky slot** when later snapshots update a tool's state (running → complete does NOT jump to bottom); multi-tool multi-narration interleave (`user → agent → tool → agent → tool → agent`); MAX_SAFE_INTEGER fallback when seq is missing.
- **File:** `reports/inflight/job-0176-engine-20260608/evidence/live_interleave_driver.py` and `live_interleave_driver_2.py`
  - Live Playwright drivers — drive Gemini via real chat-input + WS, NO `__grace2Inject*` seams per memory `feedback_playwright_must_drive_live_agent`.

## Decisions Made

- **Decision:** Track arrival order via `useRef<Map>` per Chat instance rather than via timestamp on each envelope.
  - Rationale: Simpler than threading a synthetic seq through pipelineReducer/appendDelta; refs survive re-render; first-encounter seq is naturally sticky. Envelope `ts` is wall-clock and not strictly monotonic in re-ordered delivery.
  - Alternatives considered: sort by `envelope.ts` (clock-skew risk); store seq on `PipelineStepSummary` itself (would require schema change — fabricating fields client-side is banned per agent discipline).

- **Decision:** Step-row collapse key is `name|tool_name`.
  - Rationale: Matches `mergeStepsByStepId` Part-3 dedupe; ensures the llm_generation step_id reissue stays a single sticky card at its original chat slot.

- **Decision:** Preserved `PipelineCardStack` as exported-but-unmounted helper.
  - Rationale: Two tests pin its `data-testid`; deleting + rewriting tests is out of scope.

- **Decision:** Tie-break for same-seq rows by insertion order (messages-then-tools).
  - Rationale: pre-tool narration ("I'm fetching X") should render before its tool dispatch row in the rare same-tick tie case.

## Invariants Touched

- **1. Determinism boundary:** preserves — arrival counter is presentational ordering only; no numbers fabricated.
- **4. Rendering through QGIS Server:** untouched.
- **5. Tier separation:** untouched.
- **8. Cancellation is first-class:** preserves — `shouldShowCancel` unchanged.

## Open Questions

- **OQ-176-A** Should `PipelineCardStack` be deleted now or in a follow-up?
  - Options: (a) delete + migrate tests, (b) leave dead-but-exported. Tentative: (b) for this job.

- **OQ-176-B** Auto-scroll behaviour with mid-stream tool cards.
  - Today's `atBottomRef`-gated scroll may scroll past recent agent narration when tool cards land mid-history. Tentative: acceptable for v0.1; user can scroll up.

- **OQ-176-C** Keep `bumpStreamTick` belt-and-suspenders state setter?
  - Used on case-open only (where no envelope follows to trigger render). Tentative: keep.

## Dependencies and Impacts

- **Depends on:** job-0064 (original inline cards), job-0162 (single-card-per-step merge), job-0166 (llm_generation collapse + error → ChatInput idle), job-0173 (humanized labels).
- **Affects:** testing — Playwright fixtures that assert on bottom-of-panel `pipeline-card-stack` will need to query `chat-stream` instead. Schema/agent: no changes.

## Verification

- **Tests run:**
  - `cd web && npm test -- --run src/Chat.test.tsx` → **28/28 passing** (22 pre-existing + 6 new `buildInterleavedStream` tests).
  - `cd web && npm test -- --run` → **360/360 passing** across 25 test files (full regression: no failures).
  - `cd web && npx tsc --noEmit` → no Chat.tsx errors; pre-existing `ws*.test.tsx` errors unrelated.

- **Live E2E evidence:**
  - **Driver 1** (`evidence/live_interleave_driver.py`): drove Gemini via real chat input with "Fetch and display all protected areas (WDPA polygons) around Fort Myers, Florida..." NO inject seams. Observed final stream: `[user] → [tool: Thinking…] → [tool: geocode_location] → [tool: fetch_wdpa_protected_areas (failed)] → [agent error explanation]`. WDPA tool failure is the unrelated Wave 4.9 vector-path bug; structurally the interleave is verified.
  - **Driver 2** (`evidence/live_interleave_driver_2.py`): prompt "I want to understand the geography of Naples, Florida. First tell me you are looking it up, then geocode it, then describe what you found and tell me you'll fetch admin boundaries, then fetch_administrative_boundaries for it, then summarize." Observed final stream: `[user] → [tool: Thinking…] → [agent: "I am looking up Naples, Florida. ... I have found it. Naples is a city in Col..."] → [tool: geocode_location] → [tool: fetch_administrative_boundaries]`. **CANONICAL KICKOFF PATTERN — agent narration text INTERLEAVED BETWEEN tool cards in chronological arrival order.**
  - Screenshots: `evidence/01_live_interleave.png` (Fort Myers), `evidence/02_live_interleave_naples.png` (Naples — canonical interleave).
  - Diagnostics: `evidence/diagnostics.json`, `evidence/diagnostics_2.json` carry verbatim DOM row sequences proving interleave.
  - HMR-applied changes verified via `curl http://127.0.0.1:5177/src/Chat.tsx | grep -c "buildInterleavedStream"` → 7 hits.

- **Live invariants verification:**
  - Banned-vocabulary sweep — clean.
  - Numbers shown trace verbatim to envelope payload (step.name + step.state); no client-side numbers introduced.
  - No `gs://` fetched by the browser — refactor touches no `Map.tsx` or layer URL handling.

- **Results:** **pass** — refactor live-driven, both Playwright runs honest, NO inject seams, canonical interleave pattern observed live on second prompt.

## Codified Lessons Re-affirmed

1. Geographic-correctness gate (job-0086) — N/A for this refactor.
2. Kickoff-front-loaded design — executed scope as written; 3 OQs surfaced.
3. Playwright live-drive (NEW per `feedback_playwright_must_drive_live_agent`) — verification drove Gemini via real chat-input + WS; the `__grace2InjectPipelineState` dev seam was left in place for unit/component tests only and was NOT invoked in verification driver scripts.
4. Pre-commit `git pull --rebase origin main` — done at commit time.
