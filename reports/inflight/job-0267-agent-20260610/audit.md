# job-0267-agent-20260610 — FULL-STREAM PERSISTENCE (kickoff, frozen)

Specialist: agent. Mode: fix agent, maximum rigor.

## Problem (user-verified)

Agent narration and tool cards are LOST on Case reopen — only the user's own
messages replay. Round-5 evidence: the reply text is never accumulated
(`_dispatch_gemini_and_persist` persisted `content=""` markers) and tool
dispatches persist no replayable record at all.

## Diagnosis first

- `services/agent/src/grace2_agent/server.py` `_persist_chat_turn` persists
  user turns; verify whether agent narration turns are persisted at all.
- `CaseChatMessage` (packages/contracts case.py) carries
  role/content/pipeline_id/layer_emissions — check what roles the replay
  renders.

## Implementation

1. Persist the agent's final narration per turn as a
   CaseChatMessage(role="agent"/"assistant" per the existing contract enum)
   — hook where the stream completes (the terminal agent-message-chunk /
   loop-terminal site), best-effort never-raise.
2. Persist a replayable TOOL-CARD record per tool dispatch: minimal shape
   {tool_name, state (complete/failed), started_at, duration_ms (job-0264
   now stamps it), label}. Storage: either CaseChatMessage with role="tool"
   + content=JSON (if the contract enum allows extension — check; a contract
   change needs packages/contracts tests) OR a parallel per-Case collection
   following the chat pattern. Choose the smallest contract-consistent
   design; document the choice.
3. Rehydration: get_session_state must return these so the web replays the
   FULL stream in order (interleaved by created_at). Coordinate with
   job-0266's renderer (it consumes the rehydrated history — agree on the
   shape via the contracts, not ad-hoc dicts).
4. Server-side case-list hardening: list_cases_for_user / the case-list
   emission excludes status deleted/archived SERVER-side (user saw a
   deleted ghost; client filter is not enough).

## Constraints

- NO Gemini/Vertex. NO Playwright live-driving (user is the live gate);
  dev-seam UI snapshots ARE allowed for visual evidence.
- Do NOT restart the agent on :8765 (user demoing; orchestrator restarts at
  the end). web/src edits are HMR-live — atomic edits.
- Python venv: services/agent/.venv. Vitest: npx vitest run in web/.
- Commit only owned files on MAIN; index.lock retry 5x.

## Verify

pytest: agent narration persists + replays; tool-card records persist with
duration; ordering interleaves correctly; deleted cases absent from the
emitted case-list; user-turn path unchanged. Gemini-free end-to-end:
simulate a full turn (user msg -> tool dispatch -> narration) against file
persistence, then get_session_state returns the complete ordered stream.
