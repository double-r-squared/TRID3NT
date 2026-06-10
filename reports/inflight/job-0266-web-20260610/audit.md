# job-0266-web-20260610 — PER-CASE CHAT STREAMS (kickoff, frozen)

Specialist: web. Mode: fix agent, maximum rigor.

## Directive (the product shape is LAW)

1. Each Case owns its chat stream: messages + tool cards + sandbox cards +
   charts + confirmations, all keyed by case_id. Switching Cases swaps the
   ENTIRE visible stream.
2. Root view (no active Case) = clean empty composer. Navigating out of a
   Case clears the visible chat (the stream persists server-side in its
   Case).
3. Typing from root: the server auto-creates a Case (job-0262, landed —
   case-open arrives with the new Case + first message). The web must flip
   into the Case view on that case-open and show the stream from turn 1.
   Verify job-0262's web hand-off works; fix the client side if it does not.

## Implementation scope

- web/src/Chat.tsx (+ App.tsx as needed): chat state becomes per-Case —
  Map<case_id, StreamState> in memory for the session, or clear+rehydrate
  on case-open (implementer's choice fitting the existing rehydration
  flow). On case-open: swap to that Case's stream (messages, tool cards,
  sandboxRequests/Results, charts — UNIFY the partial job-0231/0234
  resets). On navigate-to-root: clear the visible stream, empty composer.
  Streaming envelopes route to the stream of the case they belong to
  (case context = active case at arrival; envelopes for a non-visible case
  BUFFER to that case's stream, never painted into the visible one).
- ALSO (small, same files): left-rail case list must EXCLUDE status
  deleted/archived client-side; verify the rail refreshes on case-list
  envelopes.

## Constraints

- NO Gemini/Vertex. NO Playwright live-driving (user is the live gate);
  dev-seam UI snapshots ARE allowed for visual evidence.
- Do NOT restart the agent on :8765. web/src edits are HMR-live.
- vitest: stream swap on case-open; root nav clears; envelopes route to
  the owning case's stream; deleted cases filtered from rail; auto-create
  flow flips into the new Case.
- Dev-seam screenshots: two cases with distinct streams + the root clean
  state → reports/inflight/job-0266-web-20260610/evidence/.
- Commit only owned files on main.
