# Report: UI tweak #1 — pipeline cards inline in chat

**Job ID:** job-0064-web-20260607
**Sprint:** sprint-09
**Specialist:** web
**Task:** Move pipeline cards from PipelineStrip into the Chat stream; delete PipelineStrip (Option A); update App.tsx bus wiring; add tests; screenshot.
**Status:** ready-for-audit

## Summary

Deleted `PipelineStrip.tsx` (Option A) and moved pipeline step visualization inline into `Chat.tsx`. Each step now renders as a one-line `PipelineCard` component (`<operation> <pct>%` while running; `<operation> ✓/✗/⊘` on completion). Cards stack in call order (snapshot order from Appendix A.7 replace-not-reconcile). The basemap is now clear — no strip overlay. The cancel button lives in Chat's footer, conditionally styled red when a pipeline is running (FR-WC-9, Invariant 8). Added Vitest (no unit test runner existed previously), 46 tests pass across 5 test files.

## Changes Made

- **DELETED `web/src/PipelineStrip.tsx`** — Option A; replaced wholesale by inline cards in Chat.
- **NEW `web/src/components/PipelineCard.tsx`** — One-line step card component. Left border in state color, progress-fill background gradient for running steps, monospace font, 11px, muted palette. States: `pending` → "pending", `running` → "47%", `complete` → "✓", `failed` → "✗", `cancelled` → "⊘" (Invariant 8 distinct from failed).
- **MODIFIED `web/src/Chat.tsx`** — Added `pipelineReducer` with replace-not-reconcile semantics (Appendix A.7). Added `PipelineStepGroup` to render a labelled set of cards. Live pipeline sits at bottom of conversation while in flight; transitions to history on terminal state. Cancel button enabled/disabled per `shouldShowCancel` (cross-envelope predicate: running step OR session current_pipeline non-null). Added dev injection seam: `window.__grace2InjectPipelineState` registered from Chat's own GraceWs handler.
- **MODIFIED `web/src/App.tsx`** — Removed `createPipelineStripBus`, `PipelineStrip` import/mount. Preserved job-0065's panel collapse toggles, `LayerLegend`, and `onLayersChange` wiring. App-level GraceWs now only routes session-state to LayerPanel bus; pipeline-state is Chat's domain.
- **NEW `web/src/components/PipelineCard.test.tsx`** — 20 tests: format per state, progress %, done markers, error_code, call order, data-state attribute.
- **NEW `web/src/Chat.test.tsx`** — 5 tests: `shouldShowCancel` predicate under all branch combinations.
- **MODIFIED `web/src/App.test.tsx`** — Removed unused `vi` import (TS6133 error blocking compilation).
- **MODIFIED `web/src/LayerPanel.test.tsx`** — Removed unused `LayerPanelBus` import and fixed `undefined` index access (TS2532).
- **MODIFIED `web/vite.config.ts`** — Added Vitest config (`environment: "happy-dom"`, `globals: true`, `setupFiles`).
- **NEW `web/src/test-setup.ts`** — Extends Vitest expect with `@testing-library/jest-dom`.
- **MODIFIED `web/package.json`** — Added `"test": "vitest run"` script and Vitest + testing-library devDependencies.

## Decisions Made

- **Option A chosen**: Delete PipelineStrip entirely per user direction "clear the basemap". No dead code kept (AGENTS.md "Remove don't shim").
- **Cancel stays in Chat footer**: avoids two affordances for the same action.
- **`shouldShowCancel` exported as pure function**: isolated unit testing without mounting Chat (which creates a WebSocket that happy-dom cannot run).
- **Terminal detection via step states**: snapshot is terminal if all steps are in complete/failed/cancelled and there is at least one step. Avoids depending on `final_state` which A.4 pipeline-state payload does not carry (only PipelineSnapshot in D.6 has it).
- **Dev seam in Chat's `useEffect`**: `window.__grace2InjectPipelineState` wired from Chat's own `dispatchPipeline`, not the App bus.
- **Fixed pre-existing TS errors in job-0065 test files**: `noUnusedLocals` tsconfig caused build failures that blocked `npm run test`. Fixed unused imports and a `possibly undefined` index — clearly bugs not design choices.

## Invariants Touched

- **Invariant 1 (Determinism boundary)**: preserved — no numbers computed client-side.
- **Invariant 8 (Cancellation is first-class)**: preserved — cancel wired to `GraceWs.sendCancel`; `cancelled` (⊘ yellow) distinct from `failed` (✗ red).
- **Replace-not-reconcile (A.7)**: preserved — `pipelineReducer` wholesale replaces live state on each envelope.

## Open Questions

- **OQ-W-64-1 (non-blocking)**: `tools/screenshot.mjs` `pipeline-running` state hook still references old PipelineStrip selectors. The tool falls back gracefully per its "best-effort" design. A follow-up job can update the hook to use `[data-testid="pipeline-step-group"]`.

## Dependencies and Impacts

- Depends on: job-0026, job-0035.
- Concurrent with job-0065: preserved its App.tsx changes (collapse toggles, LayerLegend, onLayersChange). Clean split: Chat owns pipeline-state, App owns LayerPanel routing.

## Verification

- Tests: `npm run test` → 5 test files, 46 tests, all passed.
- Build: `npm run build` → TypeScript clean + Vite bundle success.
- Live E2E: Screenshots at `reports/inflight/job-0064-web-20260607/evidence/chat_with_pipeline_cards.png` and `chat_cards_zoomed.png`. Playwright confirmed `pipeline-card` count = 3. Cards visible: `fetch_dem ✓`, `build_sfincs_model 47%`, `run_sfincs pending`. Cancel button active (red). Map clean.
- Results: **pass**
