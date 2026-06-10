# job-0262-agent-20260610 — AUTO-CREATE CASE FROM ROOT (kickoff, frozen)

## Problem

Live demo (2026-06-10): the user twice sent a chat prompt from the **Cases
root** (no active Case). The turn ran stateless — no Case was created, the
left rail never flipped into the Case view / layer panel, and every result
(chat turns, published layers) was orphaned (attributed to no Case).

Root cause: the `user-message` dispatcher in
`services/agent/src/grace2_agent/server.py` treats `active_case_id is None`
as the legitimate M1 stateless path — `_persist_chat_turn` and the
layer-attribution writes (`_persist_case_loaded_layers`, `ensure_case_qgs`,
`publish_layer` case_id injection) all silently no-op without a Case.

## Design (v0.1, per the deferred `project_auto_case_name_derivation` memory, simplified)

When a non-directive `user-message` arrives on a session with NO
`active_case_id`:

1. Server auto-creates a Case (reusing the existing create flow: upsert
   `CaseSummary`, set active). Do **NOT** clear the LLM context — the
   in-flight message IS the Case's first turn (`chat_history` / FR-FR-3
   `turn_count` untouched, unlike `case-command(create)`).
2. Auto-name from the prompt via the existing `_derive_case_title`
   (job-0260 heuristic); fallback "Untitled Case".
3. Create happens BEFORE the turn dispatches so chat persistence + layer
   attribution land in the new Case.
4. Persist the user turn, THEN emit `case-open` (rehydration now carries the
   first message — Chat.tsx's case-open handler is replace-not-reconcile and
   would otherwise blank the just-typed bubble) + `case-list` so the UI
   switches from root into the Case view.

Web side: verify (in code) that `case-open` arriving from root navigates —
`SESSION_SCOPED_TYPES` hub fan-out → App.tsx `onCaseOpen` →
`useCases.onCaseOpen` → `setActiveCaseId` → CaseView. Fix only if broken.

Out of scope: `/invoke` debug directives (stay stateless), pseudo
"Making case..." pipeline card (deferred), Gemini-lite title derivation (v0.2).

## Acceptance (Gemini-free; user is the live gate)

- Unit: message-with-no-case → Case created + named + active BEFORE the LLM
  turn starts; user turn persisted into it; case-open(chat=[user msg]) +
  case-list emitted in order; layer attribution lands in the new Case.
- Unit: existing-case path unchanged (no second Case minted).
- Unit: no-Persistence path stays stateless (no envelopes, no Case).
- Unit: `/invoke` directive from root does not mint a Case.
- Full agent pytest suite green; web vitest only if web/src touched.

## Constraints

- NO Gemini/Vertex calls, NO Playwright, do NOT restart :8765.
- Owner: agent specialist. Owned files: `services/agent/src/grace2_agent/server.py`,
  `services/agent/tests/test_auto_create_case_job0262.py`, this report dir.
