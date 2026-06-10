# job-0264-web-20260610 — LAYER UI POLISH + TOOL TIMERS

Frozen kickoff (web specialist, quality-over-tokens). Two committed user requirements.

## Part 1 — LayerPanel polish ("simple to use, sleek look")
Build ON job-0258 wiring (opacity/visibility/order reach MapLibre). Polish LayerPanel
into a clean modern dark-theme panel. Per-layer row: drag handle, visibility eye toggle,
name (truncate w/ tooltip), kind chip (flood/plume/hillshade/vector), inline opacity slider
(compact, appears on hover/expand), smooth transitions. Empty state: subtle "No layers yet".
Keep ALL existing test ids working; extend vitest. NO redesign of data flow.

Owned: web/src/LayerPanel.tsx, web/src/LayerPanel.test.tsx

## Part 2 — tool timers (feedback_pipeline_card_humanized_labels ELEVATED 2026-06-10)
(a) Running tool cards: live (mm:ss) ticker next to spinner, client-side from first 'running'.
(b) Backend: stamp duration_ms on terminal pipeline-state step. Record started_at per step,
    include duration in terminal transition payload. Add optional duration_ms to PipelineStep
    (wire) + PipelineStepSummary (persist) in packages/contracts with tests.
(c) Completed/failed cards show authoritative duration (e.g. "2:34").

Owned: packages/contracts/src/grace2_contracts/ws.py (PipelineStep),
       packages/contracts/src/grace2_contracts/collections.py (PipelineStepSummary),
       packages/contracts/tests/test_ws.py, packages/contracts/tests/test_collections.py,
       services/agent/src/grace2_agent/pipeline_emitter.py,
       services/agent/tests/test_pipeline_emitter.py,
       web/src/contracts.ts, web/src/components/PipelineCard.tsx,
       web/src/components/PipelineCard.test.tsx

## Constraints
- NO Gemini/Vertex calls. NO Playwright. Verification = unit/integration + Gemini-free proofs.
- Do NOT restart agent on :8765 (user demoing). web/src edits HMR-live, atomic.
- Quality over tokens.
- Commit only owned files on MAIN; index.lock retry 5x.

## Plan
1. Contracts: add optional duration_ms (ge=0) to PipelineStep + PipelineStepSummary. Tests.
2. Emitter: stamp duration_ms on terminal transition (complete/failed/cancelled) from
   started_at→now. Wire it into _to_wire_step / _to_summary. Tests.
3. contracts.ts: mirror duration_ms?. PipelineCard: live ticker (running) + authoritative
   final duration (terminal). vitest (fake timers for ticker; final render).
4. LayerPanel polish: rework SortableRow presentation (eye toggle, kind chip, hover opacity,
   transitions, empty state). Preserve all test ids. Extend vitest.
5. Screenshots via dev-seam injection saved to evidence/.
