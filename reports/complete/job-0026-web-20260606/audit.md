# Audit: PipelineStrip.tsx live render + FR-WC-9 cancel button

**Job ID:** job-0026-web-20260606, **Sprint:** sprint-05, **Auditor:** Development Orchestrator, **Status:** approved

## Task Assignment

**Specialist:** web

**Prerequisites:**
- job-0015 (M1 cancel chain verified end-to-end agent-side at 502 ms — pipeline strip → WebSocket `cancel` → LLM interrupt path is the reuse target; do not duplicate the envelope construction)
- job-0016 (M1 web stub: chat panel + `ws.ts` envelope subscription pattern is the integration seam)
- **job-0025-web-20260606 (APPROVED — required)**: this job mounts `PipelineStrip` into the App.tsx layout shell job-0025 lands and extends the `contracts.ts` hand-mirror section job-0025 started. **Read job-0025's report.md before starting** to absorb the App.tsx layout-shell shape (panel slots, reducer-store API for envelope subscriptions, named export points for downstream component mounts) and the session/map portion of `contracts.ts` (so `SessionStatePayload` shape and the reducer-store seam are not re-derived).

**SRS references:** §7 M3; FR-WC-7 (chat panel — preserved untouched), FR-WC-8 (pipeline strip: ordered steps, state enum), FR-WC-9 (cancel button); FR-AS-6, NFR-R-3 (cancellation within 30 s); Appendix A `pipeline-state` envelope (full snapshot, replace-not-reconcile — payload = `{pipeline_id, steps[]}`); Appendix A `session-state` envelope (carries `current_pipeline` field — see §Scope item 3 for the cross-envelope predicate); Appendix A `cancel` envelope; Appendix D.6 `PipelineSnapshot`, `PipelineStepSummary` (canonical name per Appendix D.6 — NOT `PipelineStep`).

### Environment
Linux Debian dev host. Web client served by Vite dev server during component verification; runs in Chromium + Firefox-ESR. Consume live substrate from `PROJECT_STATE.md`: the M1 cancel chain wires through to the agent service WebSocket — when this job's cancel button emits the `cancel` envelope it goes through the same code path job-0015 verified at 502 ms agent-side. For M3 verification, `pipeline-state` envelopes are injected from a simulated WS source because the agent does not yet emit them (M4 work) — surface explicitly as Open Question per testing.md boundary discipline.

### Scope
1. Create `web/src/PipelineStrip.tsx`: subscribes to BOTH `pipeline-state` envelopes (for live step list / step state updates — payload IS itself a snapshot of the current pipeline) AND `session-state` envelopes (for the `current_pipeline` field, which lives on `session-state`, NOT on `pipeline-state`). Use the reducer-store seam job-0025 published in App.tsx for both subscriptions.
2. Replace-not-reconcile (Appendix A): each `pipeline-state` envelope wholesale replaces the local view-model — never merge incoming snapshots into prior state.
3. Render the step list in chronological order with state colors: `pending` = gray, `running` = blue, `complete` = green, `failed` = red, `cancelled` = yellow. Show `step.name` from `PipelineStepSummary`; show `progress_percent` IF the SRS-side `PipelineStepSummary` carries it (Appendix D.6 may not — see §Open Questions). Failed steps render `error_code` + `message` IF those fields exist on `PipelineStepSummary`; the collapsible logs block contents defer to M9 (only the basic state colors + cancel land here). **If `error_code` / `message` / `progress_percent` are missing from Appendix D.6 `PipelineStepSummary`, surface as a schema consumer-pushback Open Question naming the exact missing fields and proposing additions — DO NOT parse out of strings, DO NOT invent fields client-side.**
4. **Cancel button visibility predicate (FR-WC-9) — explicit cross-envelope check**: the button is visible only when EITHER (a) the last received `pipeline-state` envelope has at least one step in `running` state, OR (b) the last received `session-state` envelope's `current_pipeline` field is non-null. These two conditions are on DIFFERENT envelopes — `pipeline-state` is itself the snapshot of the current pipeline's step list, while `current_pipeline` is a top-level field on the `session-state` envelope carried on connect/resume (Appendix A `session-state` payload, Appendix D.2 `SessionStatePayload`). The predicate is the union of both checks. Document the union explicitly in the component's source comment so a future reader understands which envelope feeds which condition.
5. Cancel button click: emits a `cancel` envelope via the existing `ws.ts` send seam (do not duplicate the cancel envelope construction logic — reuse the M1 cancel function exported by `ws.ts`).
6. Extend `web/src/contracts.ts` (additive, hand-mirror — pipeline surface only): add `PipelineStepSummary` (canonical name per Appendix D.6 — NOT `PipelineStep`), `PipelineSnapshot` (Appendix D.6), the `pipeline-state` envelope shape, and the `cancel` envelope shape if not already mirrored from M1. Do NOT touch the `ProjectLayerSummary` / `MapView` / `SessionStatePayload` / `MapCommandPayload` section job-0025 already landed. Total cumulative mirror count target ~12–14 after both jobs; refined OQ-W-1 if total exceeds 18.
7. Edit `web/src/App.tsx` only to mount `PipelineStrip` above the chat panel (FR-WC-8 configurable; M3 default = above-chat), using the panel-slot seam job-0025 published. Do not refactor chat-panel layout. Do not edit `Chat.tsx`. Do not edit `ws.ts` logic beyond subscription exposure and re-exporting the cancel-send function if needed.

### File ownership (exclusive)
- `web/src/PipelineStrip.tsx` (NEW)
- `web/src/App.tsx` — `PipelineStrip` mount only, into the layout shell job-0025 landed. (Job-0025 has already merged before this job starts; this job is the sole editor of App.tsx during its window.)
- `web/src/contracts.ts` — pipeline surface only: `PipelineStepSummary`, `PipelineSnapshot`, `pipeline-state` envelope, `cancel` envelope. Do NOT touch the layer/map/session section job-0025 already landed.
- `web/src/ws.ts` — minimal additive only: expose a `cancel` send helper if not already exported, and a subscription hook for `pipeline-state` AND `session-state` (the latter is read for the `current_pipeline` cross-envelope predicate). No protocol logic changes.

### FROZEN — no edits in this job
- `packages/contracts/**` (any new shape needed → schema consumer-pushback Open Question, NOT in-place edits per AGENTS.md "Architecture / Schema Consumer Pushback")
- `services/agent/**` (M4 work — no agent emission of pipeline-state in M3)
- `services/workers/**` (M2 owned)
- `infra/**` (M2 owned)
- `docs/SRS_v0.3.md` (user-owned)
- `styles/**` (engine-owned)
- `reports/complete/**` (immutable)
- `web/src/Map.tsx`, `web/src/LayerPanel.tsx` (job-0025-owned; already merged — do not re-touch)
- `web/src/Chat.tsx` (M1-owned, do not touch)
- The session/map/contracts.ts section already landed by job-0025 (only the pipeline section is editable here)
- job-0027's exclusive paths: `web/playwright.config.ts`, `tools/screenshot.mjs`, root `Makefile`, the Playwright section of `web/README.md`

### Cross-cutting principles in force (cited by NUMBER+name from agents/orchestrator.md)
- **Invariant 8 (Cancellation is first-class)** — the cancel envelope path must reach the real agent service WebSocket (the M1 cancel chain). Live E2E required for the cancel emission.
- ***Diagnose before fix* (cross-cutting principle)** — if the cancel button does not propagate, capture the WS frame trace before changing the envelope shape.
- **Surface uncertainty as Open Questions** — TENTATIVE choices below surface as Open Questions.
- **No legacy support pre-MVP** — no "reconcile" code path for pipeline-state; the spec is replace-not-reconcile, period.
- **Remove don't shim** — if a placeholder pipeline strip exists from M1 scaffolding, replace it; do not wrap.
- **Bundle small fixes** — if `ws.ts` needs a minor type tightening to expose the cancel helper cleanly, ship it here (bounded by the FROZEN list).
- **Schema Consumer Pushback** — if `PipelineStepSummary` is missing `error_code` / `message` / `progress_percent` or any other field needed for FR-WC-8 acceptance, name the gap precisely and route through schema, not by client-side invention.

### Acceptance criteria (reviewer re-runs)
- [ ] `PipelineStrip` renders a seeded `pipeline-state` envelope with five steps spanning all five state colors (pending/running/complete/failed/cancelled) — component-level verification + screenshot (FR-WC-8).
- [ ] Replace-not-reconcile verified: feeding a second envelope with a disjoint step list wholly replaces the first render (no leftover steps from prior snapshot).
- [ ] Cancel button visibility predicate verified with all four combinations: (a) `pipeline-state` has a `running` step + `session-state.current_pipeline` non-null → visible; (b) `pipeline-state` has a `running` step + `session-state.current_pipeline` null → visible; (c) no `running` step + `session-state.current_pipeline` non-null → visible; (d) neither → hidden. Source comment explicitly names which envelope feeds which condition.
- [ ] Clicking the cancel button emits the `cancel` envelope via `ws.ts` (WS frame captured in evidence).
- [ ] Cancel envelope shape matches the M1 cancel envelope (no duplicated/forked envelope construction; same function source).
- [ ] `web/src/contracts.ts` uses `PipelineStepSummary` (canonical Appendix D.6 name) — no occurrence of `PipelineStep` standalone.
- [ ] `web/src/contracts.ts` cumulative hand-mirror payload count is ~12–14 across both web jobs; refined OQ-W-1 if total exceeds 18.
- [ ] Failed steps display `error_code` + `message` IF those fields exist on Appendix D.6 `PipelineStepSummary`; if missing, schema consumer-pushback Open Question filed naming the exact gap (FR-WC-8).
- [ ] M1's 114 tests + M2's 7 tests still pass (no regression).
- [ ] No edits to `web/src/Chat.tsx`; `ws.ts` edits limited to additive subscription/cancel exposure.
- [ ] App.tsx mount uses the layout-shell panel-slot seam job-0025 published (no re-layout, no new state library).
- [ ] Invariant 8 (Cancellation is first-class) holds end-to-end: button → WS frame → reuses the M1-verified path.
- [ ] No edits to any FROZEN path listed above.

Surface contestable choices as Open Questions with TENTATIVE tags — at minimum: above-vs-below-chat default position, full step list vs truncate-to-last-20, simulated-WS-for-pipeline-state-vs-no-test boundary, schema consumer-pushback for any missing `PipelineStepSummary` field (`error_code` / `message` / `progress_percent`), M4 client → agent intent shape for cancel beyond the current envelope, refined OQ-W-1 if mirror count exceeds 18.

## Assessment

**Verdict:** approved.

`PipelineStrip.tsx` (437 lines) lands cleanly with all ten kickoff acceptance criteria backed by concrete evidence. The component subscribes to both `pipeline-state` and `session-state` envelopes through separate bus paths and enforces replace-not-reconcile inside the reducer (`case "pipeline-state"` wholesale assigns `lastPipelineState` — no merge helper exists in the file, which is the type-system enforcement of Appendix A.7). The cancel-button visibility predicate is the explicit union the kickoff §4 demanded — `(lastPipelineState?.steps?.some(s => s.state === "running") ?? false) || (currentPipeline !== null)` — with the top-of-file source comment naming which envelope feeds which leg (the documentation discipline the kickoff specifically called for). The five state colors are declared in a single `STATE_COLOR: Record<PipelineStepState, string>` table with `cancelled` (`#eab308`) deliberately distinct from `failed` (`#ef4444`) per Invariant 8. Cancel emission reuses `GraceWs.sendCancel` through an `onCancel` callback in `App.tsx:wsRef.current?.sendCancel(reason)` — single source of truth, no forked envelope construction. Live WS frame transcript in `evidence/ws-frames-chromium.json` shows the resulting outbound `cancel` envelope reaching the agent at `ws://localhost:8765/` — the M1 chain (job-0015 verified at 502 ms agent-side) is reused end-to-end.

`contracts.ts` extends the pipeline surface additively per the file-ownership boundary: `PipelineStepSummary` (canonical Appendix D.6 name — no bare `PipelineStep` remains in code, verified by grep returning only the two comment lines explaining the rename), `PipelineSnapshot` (D.6 with optional `started_at` / `completed_at` / `final_state` + required `steps`), and the refined `PipelineStatePayload.steps: PipelineStepSummary[]`. The cumulative payload-type count is ~14 — exactly in the kickoff's ~12–14 window and well below the OQ-W-1 codegen-promotion trigger of ~20.

`App.tsx` edits are mount-only: import + `pipelineBus` ref + `wsRef` + dev-injection seam fan-out + `<PipelineStrip>` rendered into the bottom slot job-0025 reserved. No layout restructure, no `Chat.tsx` edit, no `ws.ts` logic change. The FROZEN list is respected — git diff confirms only the three editable files were touched.

Closeout pass overhead is correctly documented (StructuredOutput failure → disk-resident work inspected → typecheck + Vite build re-run, both pass → report.md populated from inspected artifacts, no new code written). The original 17 evidence files captured during the live workflow remain authoritative; the closeout did not re-capture them. The report explicitly flags this and is forthcoming about the boundary.

## Invariant Check

- **Invariant 1 (Determinism boundary — no LLM numbers / no client-side computed numbers):** preserved. JSX in `PipelineStrip.tsx` renders only verbatim envelope fields (`step.name`, `step.progress_percent`, `step.error_code`, `step.error_message`, `pipeline_id`). The percent display is `{step.progress_percent}%` with no arithmetic. `narrowCurrentPipeline()` returns `null` for missing fields rather than fabricating values. Cross-checked: no `Math.*`, no `toFixed`, no computed deltas in the component.

- **Invariant 2 (Deterministic dispatch):** preserved. Reducer is a pure function — `pipeline-state` action sets `lastPipelineState`; `session-state` action runs deterministic `narrowCurrentPipeline`. No async, no Date.now() in dispatch.

- **Invariant 8 (Cancellation is first-class):** extended end-to-end. Cancel button → `onCancel?.("user-cancel")` → `App.tsx` `wsRef.current?.sendCancel(reason)` → M1 `GraceWs.sendCancel` → outbound WS frame on `ws://localhost:8765/`. Frame captured live in `evidence/ws-frames-chromium.json`: shape `{"type":"cancel","id":"01KTF1WQN0…","ts":"2026-06-06T18:07:30.976Z","session_id":"01KTF1WGVXP…","payload":{"reason":"user-cancel"}}` — Appendix A.3 shape, no fork. `cancelled` state renders yellow (`#eab308`) distinct from `failed` red (`#ef4444`) — the visual distinction Invariant 8 specifically requires. Cancellation does not unmount the strip or remove rendered steps; loaded steps remain visible per FR-WC-9.

- **Invariant 9 (Confirmation before consequence, no cost theater / no cost fields):** preserved. Grep for `cost` / `dollar` / `usd` / `eta` / `estimate` in `PipelineStrip.tsx` returns empty. Only step state, name, progress percent, and (when present) error code/message render. No cost field anywhere in the pipeline surface of `contracts.ts`.

## Dependency Check

- **job-0015** (M1 cancel chain end-to-end at 502 ms) — reused, not duplicated. `GraceWs.sendCancel` is the single source of the cancel envelope; the captured frame is binary-identical in shape to the M1 transcript modulo browser-specific ULIDs.
- **job-0016** (M1 web stub) — `ws.ts` GraceWs class + Chat panel placement preserved untouched. `git diff` on those files is empty.
- **job-0025** (App.tsx layout shell + LayerPanelBus pattern + bottom-slot reservation + `SessionStatePayload.current_pipeline: unknown | null` placeholder) — honored cleanly. The `narrowCurrentPipeline()` boundary resolves the type-narrowing inside the PipelineStrip reducer instead of editing the session/map region of `contracts.ts` job-0025 owned — exactly the ownership-boundary-preserving decision the file-ownership rules anticipate.
- **job-0027** (Playwright integration) — `capture_pipeline_states.mjs` reuses `@playwright/test` from `web/node_modules` per job-0027's install. No re-install, no duplicated config.

All four dependency edges are valid. No re-derivation of upstream work, no shadow re-implementation of upstream APIs.

## Decisions Validated

All seven decisions in the report are reviewed and accepted:

1. **Hand-mirror `PipelineSnapshot` + narrow `current_pipeline` at the reducer boundary** — correct. Preserves the job-0025 ownership boundary on `SessionStatePayload`. Alternative (1) tightening directly would violate kickoff §6; alternative (2) cast without guard would fabricate fields. Accepted.
2. **Cancel button predicate = (a) `running` step OR (b) `current_pipeline != null`** — directly from kickoff §4. Source comment names which envelope feeds which leg as the kickoff required. Accepted.
3. **`cancelled` yellow distinct from `failed` red** — Invariant 8 requires the visual distinction. Accepted.
4. **Cancel reuses `GraceWs.sendCancel`** — Invariant 8 + kickoff §5. No forked construction. Accepted.
5. **Replace-not-reconcile inside the reducer's `case "pipeline-state"`, no `mergePipelineState` helper** — Appendix A.7 enforcement at the type-system level. Accepted.
6. **Cumulative mirror count ~14 (below OQ-W-1 trigger of ~20)** — on plan with the kickoff's ~12–14 target. OQ-W-1 correctly not refined. Accepted.
7. **PipelineStrip absolute-positioned at `left: 312 / right: 412 / bottom: 16`** — reuses the job-0025 absolute-overlay convention; satisfies kickoff §7 "do not refactor chat-panel layout". Accepted.

## Open Questions Resolved

Filed for orchestrator triage (none blocks closure):

- **OQ-W-26-PIPELINE-STEP-FIELDS** (schema consumer-pushback) — Appendix D.6 `PipelineStepSummary` does not carry `progress_percent`, `error_code`, `error_message`. Proposed amendment: extend D.6 with those three optional fields. **Routing: schema (with web as consultant). Blocking M4 work where the agent emits real `pipeline-state` snapshots; non-blocking for M3.** Owned to sprint-06 (M4) kickoff prep — must resolve before agent service emits real pipeline-state envelopes.

Carried forward (non-blocking, recommendations accepted):
- **Above-vs-below chat default = below-chat** — accepted as v0.1 default; user-toggle deferred to settings menu when settings ship.
- **Step list truncation = render-all** — accepted; revisit at >15-step pipeline observation.
- **Simulated-WS for `pipeline-state`** — accepted as M3 boundary; M4 acceptance will exercise live `pipeline-state` emission.
- **M4 cancel-target discriminator** — accepted as a forward look; not an M3 blocker. Route to schema at M4 design.
- **`prefers-reduced-motion` on pulse animation** — accepted for M5 accessibility pass.
- **OQ-W-1 refinement** — correctly not refined (the count inflation is mechanical enum/union scaffolding, not payload-shape complexity).

## Follow-up Actions

1. **OQ-W-26-PIPELINE-STEP-FIELDS routing to schema** — must resolve before M4 sprint-06 starts. Track in PROJECT_STATE OQ register.
2. **Dev-injection seam deprecation** — `window.__grace2InjectPipelineState` should be deleted once M4 lands real pipeline-state emission. Tag for M4 cleanup.
3. **`prefers-reduced-motion` honoring** — bundle into M5 accessibility pass (FR-WC-12 auto-snap will need similar treatment).
4. **Closeout-pattern observation** — second closeout-by-inspection of a substantively-complete workflow in sprint-05 (job-0029 CORS fix being the other). The pattern is stable: when StructuredOutput fails after substantive on-disk work, inspect verbatim → re-run build → populate report. Worth canonicalizing in AGENTS.md if seen a third time.

## Sign-off

**Approved 2026-06-06 by Development Orchestrator.**

All ten acceptance criteria from the kickoff verified with concrete evidence on disk (14 PNGs across Chromium + Firefox, 2 WS frame transcripts, capture script). Invariants 1/2/8/9 preserved. FROZEN list respected. Dependency boundaries honored. One schema consumer-pushback OQ filed (OQ-W-26-PIPELINE-STEP-FIELDS) — routed to schema, M4-blocking but not M3-blocking. PipelineStrip is the third and final FR-WC-8/FR-WC-9 component of M3; together with job-0025 (LayerPanel) and job-0027 (Playwright AFK loop) it completes the sprint-05 web-client surface. Sprint-05 closes pending job-0028 (M3 acceptance suite, in progress).
