# job-0315 — Chat narration/tool-card interleave (server-side message_id segmentation)

**Owner:** agent · **Opened:** 2026-06-16 · **Priority:** P1 (user-flagged 3x) · **Sprint:** ux-batch-1 adjunct
**Execution:** Workflow (design -> implement -> 4-lens adversarial-verify gate). User-authorized 2026-06-16 ("Fix chat interleave now").

## Problem (user, 2026-06-16)

"the tool usage is not inline... still all in one vertical blob and the narration is still all together no interleaving." Expected (per `feedback_chat_tool_interleave`): narration line -> its tool card -> next narration -> its card, chronologically.

## Root cause (CONFIRMED, orchestrator-direct read)

server.py allocates `message_id = new_ulid()` ONCE per user turn (~L942) and appends EVERY `TextDeltaEvent` across all multi-turn-loop iterations to that single id (~L1071-1082). So all narration is one bubble = one client arrival-seq; every tool card sorts after it. The client (`Chat.tsx buildInterleavedStream`) already interleaves correctly by seq — it just has only one narration seq to work with.

Persistence compounds it: `_dispatch_gemini_and_persist` (~L3819-3884) writes ONE `role="agent"` row at turn close with all narration joined, while tool rows are written mid-turn via `_persist_tool_card` (~L3270). So Case replay (`Chat.tsx replayStreamFromChatHistory`, orders by array order) also bunches.

## Fix (two parts, server.py)

- **Part A (live wire):** mint a fresh `message_id` when text resumes after a tool call (close the prior bubble with done=True, start a new one). Each narration segment becomes its own bubble with its own arrival-seq -> interleaves with tool cards. No client change (buildInterleavedStream already sorts by seq).
- **Part B (persistence/replay parity):** persist each narration SEGMENT as its own `role="agent"` row in creation order, so the persisted row order interleaves with the mid-turn tool rows and `replayStreamFromChatHistory` reconstructs the SAME train. Must not break the cancel/error best-effort narration persist, the thinking indicator, narration-less turns, or the CaseChatMessage schema.

## Acceptance
1. A turn doing text -> tool -> text -> tool emits >=2 distinct agent `message_id`s whose seqs interleave with the tool-card seqs (live).
2. Persisted rows for that turn interleave agent segments with tool rows; replay order == live order.
3. Existing agent test suite green; new unit tests cover the interleave + replay parity + edge cases (no leading text; only text; multiple parallel tool calls in one round).
4. Adversarial-verify gate: >=3 of 4 lenses (correctness / regression / persistence-replay-parity / contract-schema) confirm, refute-by-default.
5. Deploy to AWS agent + user live-test confirms the followable train (after user heads-up; no restart mid-demo).

## Out of scope
- Expandable tool card (input/output) — deferred, separate.
- Roads/inline-GeoJSON render — separate live re-check post job-0314.
