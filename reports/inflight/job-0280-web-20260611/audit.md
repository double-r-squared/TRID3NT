# job-0280 — web — Case-open snap-to-location + mobile sheet polish (kickoff, FROZEN)

Specialist: web (Fable runner, MAX effort). Dispatched 2026-06-11.

## Mission (user-directed, three concrete items + a sleekness pass — "retain 100% functionality")

1. **CASE-OPEN SNAP-TO-LOCATION**: reopening a Case must fly the map camera to
   the Case's geography (today it stays wherever it was / resets to CONUS —
   user-reported gap). Implementation seam guidance: `CaseSummary.bbox` is null
   today, BUT every persisted `CaseChatMessage` carries `map_command_emissions`
   (typed map-command args including the original turn's `zoom-to` with bbox —
   see packages/contracts/src/grace2_contracts/case.py and the case-open
   rehydration payload `session_state.chat_history`). On case-open, replay the
   LAST `zoom-to` found in the rehydrated chat history through the existing
   map-command path (App.tsx routes map-commands via the LayerPanelBus →
   Map.tsx zoom-to machinery from job-0068/0072). If no zoom-to exists in
   history, do nothing (root/new Cases unchanged). Cover with vitest on the
   pure extraction helper.

2. **MOBILE COLLAPSED-SHEET ACTIVE-TOOL INDICATOR**: when the chat bottom sheet
   (job-0278, Chat.tsx mobile branch) is COLLAPSED and a tool is RUNNING in the
   visible stream, render a slim live-status strip directly ABOVE the composer:
   the running tool's humanized label + elapsed timer (the same data the
   PipelineCard shows — reuse its state, do not fork pipeline logic). It
   disappears when no step is running. Tapping it expands the sheet. Desktop
   unchanged.

3. **REMOVE THE REDUNDANT SHEET CHEVRON**: the sheet currently shows BOTH a
   drag-handle bar and a small chevron arrow near it (user: "the arrow near the
   adjustment bar on the top of the chat window is redundant"). Keep ONE
   affordance — the handle bar (whole handle area tappable, >=44px). Remove the
   arrow.

4. **SLEEKNESS PASS (conservative)**: consistent corner radii / spacing on the
   mobile sheet + drawer, no double borders, no redundant labels. Visual polish
   ONLY — zero behavior changes beyond items 1-3. When in doubt, leave it.

## Constraints

- WEB ONLY (web/src). Do NOT touch services/agent, packages/contracts, infra, docs/srs.
- Do NOT restart anything; Vite HMR is live and the user may be using the app —
  keep every intermediate save compiling (atomic edits).
- Do NOT send chat prompts or drive Gemini. Static UI screenshots (page load,
  drawer/sheet toggling, dev-seam pipeline-state injection for the running-tool
  strip — `__grace2InjectPipelineState` is VALID for component-state
  screenshots, never for e2e verification) are allowed.
- Desktop >=768px pixel-identical except item 1 (snap applies to both form factors).
- `git add` ONLY files touched; never `git add -A`.
- Chat.tsx just received per-Case streams (job-0266), envelope case-tag routing
  (job-0277), and the mobile sheet (job-0278) — read before editing; do not
  restructure stream logic.

## Acceptance

- Full web vitest green (`cd web && npx vitest run`, currently 616 + new tests; ZERO regressions).
- New tests: zoom-to extraction helper; collapsed-sheet strip renders when a
  step is running and hides when not; chevron gone (update any test that
  referenced it).
- Static screenshots: collapsed sheet WITH running-tool strip (injected
  pipeline-state), sheet without strip, drawer view — saved under
  reports/inflight/job-0280-web-20260611/evidence/.
- reports/inflight/job-0280-web-20260611/{audit.md,report.md,STATE=DONE};
  commit "job-0280: ..." ending Co-Authored-By trailer.
