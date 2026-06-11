# job-0280 — web — report

STATE: DONE. All four kickoff items landed, web-only, 638/638 vitest green
(616 baseline + 22 new), Vite HMR kept compiling at every intermediate save
(verified by `tsc --noEmit` on touched files + live screenshot run against the
running dev server — never restarted).

## What landed

### 1. Case-open snap-to-location

- **`web/src/lib/case_zoom.ts` (NEW)** — pure extraction module:
  - `asBbox(x)` — narrows to exactly 4 finite numbers (rejects NaN/Infinity/
    strings/wrong arity);
  - `asZoomToCommand(entry)` — narrows one persisted `map_command_emissions`
    dict to the Map.tsx wire shape. Accepts the canonical persisted form
    `{command:"zoom-to", args:{bbox}}` plus a defensive flattened
    `{command:"zoom-to", bbox}` form;
  - `extractLastZoomTo(chat)` — walks the rehydrated `chat_history`
    newest-first (and each message's emissions last-first) returning the most
    recent valid zoom-to, or null.
- **`web/src/App.tsx`** — in the existing Case-rehydration effect, when
  `activeSession.case.bbox` is absent (it is null in practice today), the
  extracted zoom-to is pushed through the SAME `bus.pushMapCommand` →
  Map.tsx `fitBounds` path the live envelope takes (job-0068/0072 machinery).
  No zoom-to in history → camera untouched (root/new Cases unchanged).
  Applies to both form factors (it's a map behavior).

### 2. Mobile collapsed-sheet active-tool strip

- **`web/src/Chat.tsx`** — `findRunningToolStep(history, live, stepOrder)`
  (exported, pure): most-recent RUNNING step by first-arrival seq over the
  SAME `mergeStepsByStepId` view-model the inline cards render — no forked
  pipeline logic. Excludes the `llm_generation` thinking pseudo-step (the
  strip is an active-TOOL indicator; thinking has its own ephemeral surface).
- `SheetActiveToolStrip` (exported component): slim strip
  (`grace2-sheet-tool-strip`) rendered directly ABOVE the composer when
  `mobile && !sheetExpanded && running`. Shows `humanizeStepName(step.name)` +
  live m:ss elapsed + spinner — all REUSED from PipelineCard (see below).
  Tap → `setSheetExpanded(true)`. Disappears the moment the merged view-model
  has no running tool. Desktop never renders it (gated on the `mobile` prop).
- **`web/src/components/PipelineCard.tsx`** — exported (no behavior change)
  `useRunningElapsedMs` (the started_at-anchored 1 Hz ticker — strip and card
  show the SAME elapsed value), `prefersReducedMotion`, and `Spinner`.

### 3. Redundant sheet chevron removed

- `SheetToggleHandle` now renders the handle bar ONLY (chevron `⌃`/`⌄` span
  deleted; padding normalized to center the bar). Whole handle stays a
  >=44px full-width tap target with the same aria-label/aria-expanded.

### 4. Sleekness pass (conservative, mobile-scoped)

- **`web/src/styles/global.css`** — inside the mobile drawer
  (`.grace2-mobile-touch`, mounted ONLY by MobileDrawer below 768px), the
  nested CasesPanel / CaseView no longer float as desktop cards: own border /
  background / fixed desktop width dropped so they lay into the drawer
  surface (kills the box-within-a-box double border + mismatched gutters).
  `!important` used deliberately — these components style inline, which beats
  stylesheets otherwise (documented in the CSS block). Desktop untouched.
- Deliberately LEFT alone (kickoff: "when in doubt, leave it"): the "M1 stub"
  header chip (desktop-shared — pixel-parity constraint), the dashed
  empty-layers placeholder, drawer corner radii (square edge-anchored drawer
  vs rounded floating sheet are different idioms, both internally consistent).

## Tests — 638 passed (40 files), zero regressions

- `web/src/lib/case_zoom.test.ts` (NEW, 12 tests): asBbox narrowing (2),
  asZoomToCommand canonical + flattened + rejection shapes (3),
  extractLastZoomTo empty/no-emission/single/last-across-messages/
  last-within-message/malformed-skip/non-array-tolerance (7).
- `web/src/Chat.mobileSheet.test.tsx` (+10 tests): chevron-GONE pin (bar is
  the single child, no chevron glyphs, >=44px); findRunningToolStep (6 —
  null/terminal/live-running/thinking-excluded/most-recent-wins/
  pipeline-reissue-collapse); SheetActiveToolStrip (3 — label + ticking m:ss
  timer + spinner; tap fires onExpand; Chat-wiring harness shows strip while
  running and hides on terminal). No existing test referenced the chevron
  (verified by glyph grep).

## Evidence (static screenshots, injected component state — NOT e2e)

`reports/inflight/job-0280-web-20260611/evidence/` via `snapshot_0280.mjs`
(390x844 mobile + 1280x800 desktop, against the live Vite dev server;
`__grace2InjectPipelineState` seam per its blessed component-state use):

- `s1_sheet_collapsed_no_strip.png` — collapsed sheet, handle bar only (no
  chevron), no strip.
- `s2_sheet_collapsed_with_strip.png` — running step injected → strip above
  composer: `fetch_3dep_dem  1:24  (spinner)`.
- `s3_sheet_expanded_after_strip_tap.png` — tapping the strip expanded the
  sheet; full PipelineCard visible inline.
- `s4_sheet_collapsed_strip_hidden_after_complete.png` — terminal envelope →
  strip gone.
- `s5_drawer.png` — drawer with CasesPanel laid into the surface (no double
  border, full-width header row).
- `s6_desktop_unchanged.png` — desktop layout intact; running step injected
  and NO strip rendered (script asserts this).

Script also hard-asserts: no strip with nothing running, strip present when
running, strip absent after terminal, desktop has no sheet state + no strip.

## Risks / findings for the orchestrator

1. **CROSS-SEAM GAP (item 1 will not fire on live data yet):** the kickoff
   states every persisted `CaseChatMessage` carries `map_command_emissions`
   including the turn's zoom-to. Verified by grep: the agent NEVER populates
   that field — `services/agent/src/grace2_agent/server.py::_persist_chat_turn`
   constructs `CaseChatMessage` without `map_command_emissions` (contract
   default `[]`), and no writer exists anywhere in services/agent. The web
   replay path is implemented exactly per kickoff, fully tested, and will
   light up the moment the agent persists the emission (a small agent-side
   job: accumulate `emit_map_command("zoom-to", ...)` calls per turn the same
   way `current_turn_layer_ids` accumulates, and pass them to
   `_persist_chat_turn`). Until then, case-open snap stays a no-op on real
   Cases — same observable behavior as today, no regression. Also noted:
   persisted `loaded_layer_summaries` drop `LayerURI.bbox` (the
   `ProjectLayerSummary` conversion in pipeline_emitter.py has no bbox field),
   so a layer-derived fallback wasn't available web-side either; and
   `CaseSummary.bbox` has no writer. Recommend a follow-up agent job; the
   contract and web sides are both ready.
2. Pre-existing (NOT from this job): bare `npx tsc --noEmit` fails on
   unrelated test files (`ws.test.tsx`, `ws.stickyAnon.test.tsx`,
   `Chat.caseTagRouting.test.tsx`, `Chat.perCaseStreams.test.tsx`) — vitest
   mock typing drift. Vitest itself doesn't type-check, so the suite is
   green; `npm run build` (which runs tsc) would trip on these. Touched files
   verified clean.
3. `web/src/components/SettingsPopup.tsx` has an unrelated uncommitted
   modification in the working tree — NOT included in this commit.
4. The strip excludes `llm_generation` by design; if a turn is purely in the
   Gemini thinking phase while collapsed, no strip shows (matches the
   "active-TOOL indicator" framing; the thinking indicator remains
   expanded-scroll-only per `feedback_thinking_state_ephemeral`).

## Files changed

- `web/src/lib/case_zoom.ts` (new)
- `web/src/lib/case_zoom.test.ts` (new)
- `web/src/App.tsx`
- `web/src/Chat.tsx`
- `web/src/Chat.mobileSheet.test.tsx`
- `web/src/components/PipelineCard.tsx`
- `web/src/styles/global.css`
- `reports/inflight/job-0280-web-20260611/{audit.md,report.md,STATE,evidence/*}`
