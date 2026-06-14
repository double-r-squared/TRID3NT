# job-0258-web-20260610 — adversarial verification (Fable-5, refute-by-default)

Verifier: independent Fable-5 panel, 2026-06-10. Stance: REFUTE unless every claim
re-derives and re-reproduces from scratch. **Result: CONFIRM.**

## Root cause re-derived independently (from `git show 6b8c7b9`)

1. **Pre-fix LayerPanel stubs — CONFIRMED.** `git show 6b8c7b9^:web/src/LayerPanel.tsx`
   lines ~240-265: `onDragEnd` / `onVisibilityToggle` / `onOpacityChange` each did
   `dispatch({type:"local-*"})` + `console.debug("[LayerPanel] … intent:")` and nothing
   else. Header comment (pre-fix lines 16-19) verbatim: "In M3 (this job), user-side
   clicks emit a local intent log … The console.debug logs document what M4 will wire
   to outbound map-command envelopes." The M4 wiring never happened.
2. **Pre-fix Map.tsx dead-ends all layer-control verbs — CONFIRMED.**
   `git show 6b8c7b9^:web/src/Map.tsx` lines ~950-969: subscription handles only
   `zoom-to`; else-branch is `console.warn("[MapView] MapCommand not yet implemented")`.
3. **Zero moveLayer pre-fix — CONFIRMED.** `git grep -n "moveLayer" 6b8c7b9^ -- web/src`
   returns only `removeLayer` substring matches (and `RemoveLayerCommand`); no true
   `moveLayer` call existed, so stack reorder was structurally impossible.
4. **Fix is root-cause, not symptom patch.** New exported helpers
   (`layerGroupMemberIds`/`applyLayerOpacity`/`applyLayerVisibility`/`applyLayerOrder`)
   are the single shared apply path for BOTH session-state reconciliation and the
   map-command subscription; LayerPanel emits real `MapCommandPayload`s via new
   `onMapCommand` prop; App.tsx:753 wires `onMapCommand={bus.pushMapCommand}` and
   MapView subscribes the same bus (App.tsx:639, bus created at :178). Contract shapes
   in `web/src/contracts.ts` (SetLayerOpacityCommand/SetLayerVisibilityCommand/
   SetLayerOrderCommand, flat fields) match exactly what the Map handler reads.
   Echo back into the panel reducer is idempotent (reducer `map-command` case re-sets
   identical values; no re-emission loop — emission only fires from DOM handlers).
5. **Secondary root cause (idle re-arm) — CONFIRMED in diff.** Pre-fix
   `if (!m.isStyleLoaded()) return;` with a comment claiming "the deferred idle handler
   will retry" while nothing re-armed; fix re-arms `m.once("idle", applyLatest)` in the
   bail path. Regression test is non-vacuous: fires the captured idle callback with
   style still unloaded, asserts addSource NOT called AND a NEW once("idle")
   registration exists, then settles style and asserts the batch applies.

## Proof re-run from scratch (not trusted from report)

- **vitest**: `npx vitest run` in `web/` → **32 files / 522 tests, all green**
  (exactly the claimed counts). New suites inspected for vacuousness: helper units
  assert sublayer coverage (`-outline`, `-clusters`, `-cluster-count`), clamp,
  bottom-first moveLayer order; bus end-to-end renders REAL LayerPanel + MapView on a
  shared `createLayerPanelBus` and a real slider `change` event asserts
  `setPaintProperty("flood-demo","raster-opacity",0.3)` on the maplibre mock. Not vacuous.
- **Playwright live probe re-run** against the RUNNING dev server :5173
  (`node web/tools/playwright_job0258_layer_controls.mjs <verify/evidence>`):
  **6/6 PASS**, identical values — fill-opacity 0.4 → 0.032, raster-opacity 1 → 0.28,
  drag re-stack `["…","job0258-poly","job0258-poly-outline","job0258-raster","job0258-points"]`,
  visibility none → visible round-trip. Fresh screenshots + results.json in
  `verify/evidence/` (PNGs byte-identical to the committed run — deterministic render,
  and my PASS values came from live `page.evaluate` reads, so this corroborates).
- **Safety claims verified by reading the probe source**: `window.WebSocket` replaced
  with an inert fake in `addInitScript` (zero traffic to the live agent on :8765);
  layers injected via `__grace2InjectCaseOpen` only; no chat, no Gemini. The dev seam
  routes through the SAME `useCases_onCaseOpen` handler as the real WS case-open path
  (App.tsx:492 vs :566), and the control wiring under test is independent of how
  layers were loaded. Seam use was explicitly authorized by this job's frozen kickoff
  ("DEV-SEAM check … inject a layer via the existing __grace2Inject seams … Do NOT
  send chat messages") — the no-inject-seam rule applies to agent-rendering
  verification, not this client-only control-path job.
- Working tree == commit 6b8c7b9 for all three fix files (`git diff 6b8c7b9 HEAD --
  web/src/Map.tsx web/src/LayerPanel.tsx web/src/App.tsx` empty), so the re-run
  exercises the committed fix.

## Residual risk (none refuting; all honestly disclosed in the job report)

- Initial paint order is add-order, not z_index (reproduced in my run's beforeOrder:
  async vector adds landed after the sync raster add) — flagged out-of-scope #2 with a
  candidate follow-up; first user reorder corrects it. Minor.
- Panel intents are client-local (no agent persistence across Case reopen) — documented
  M4-deferred scope, matches kickoff.
- Deployed QGIS WMS basemap 500s ("Layer(s) not valid") — pre-existing infra issue,
  flagged for an infra/engine job.
- Narrow race: slider moved before a deferred layer lands on the map → command no-ops
  silently for that instant (panel/map opacity divergence until next session-state
  push). Not the user-observed failure class; minor.

## Verdict

**CONFIRM** — root cause re-derived independently and matches the runner's claim
verbatim; fix addresses the cause (missing wiring), not a symptom; both proofs
reproduce exactly from scratch; tests are substantive; residual issues are minor and
disclosed.
