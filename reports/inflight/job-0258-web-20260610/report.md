# job-0258-web-20260610 — LAYER CONTROLS DEAD: report

**Verdict: FIXED** (root-cause fix + secondary root-cause fix, live-verified)

## Root cause (primary)

The LayerPanel's user controls were **never wired to the map** — they were M3
"local intent" stubs that the M4 wiring job never closed:

- `web/src/LayerPanel.tsx` (pre-fix lines 240–265): `onDragEnd`,
  `onVisibilityToggle`, and `onOpacityChange` dispatched ONLY to the panel's
  local `useReducer` and logged `console.debug("[LayerPanel] … intent:")`.
  The header comment said it outright: *"In M3 (this job), user-side clicks
  emit a local intent log … The console.debug logs document what M4 will wire
  to outbound map-command envelopes."* That wiring never happened.
- `web/src/Map.tsx` (pre-fix lines 950–969): the map-command subscription
  handled ONLY `zoom-to`; every other verb (including `set-layer-opacity`,
  `set-layer-visibility`, `set-layer-order`) fell into
  `console.warn("[MapView] MapCommand not yet implemented")`.
- `grep -rn "moveLayer" web/src` (pre-fix): **zero hits** — stack reordering
  was structurally impossible, even for agent-driven `set-layer-order`.
- The only paint/layout update path was the session-state reconciliation loop
  (`applyLatest`, pre-fix Map.tsx:760–784), which fires solely on
  agent/session pushes — never on panel interaction.

So the slider moved, the row reordered, the percentage label updated — and
the MapLibre instance never heard about any of it.

## Root cause (secondary — found live by the probe)

`applyLatest`'s deferral was a one-shot: the subscriber registers exactly one
`m.once("idle", applyLatest)` per session-state push, and `applyLatest` bailed
at `if (!m.isStyleLoaded()) return;` **without re-arming** (the comment
claimed "the deferred idle handler will retry" — nothing retried). Live repro:
with `theme=dark`, `applyTheme` (registered earlier) mutates the style inside
the SAME idle dispatch, so `isStyleLoaded()` is false again when `applyLatest`
runs → the entire layer batch silently dropped until the next push. This is
the same "layers don't show up" failure family seen in the demo. Fixed by
re-arming `m.once("idle", applyLatest)` in the bail path (idempotent —
replace-not-reconcile diff against `addedSourceIds`).

## Fix

1. **`web/src/Map.tsx`** — new exported helpers `layerGroupMemberIds` /
   `applyLayerOpacity` / `applyLayerVisibility` / `applyLayerOrder` that
   address the WHOLE MapLibre layer group per logical layer (`-outline`,
   `-clusters`, `-cluster-count` sublayers included — the old inline update
   branch missed them). The map-command subscription now applies
   `set-layer-opacity` (clamped, per-geometry paint keys, Pelicun/polygon
   multipliers), `set-layer-visibility`, and `set-layer-order`
   (`moveLayer` bottom-first so the first id paints on top; basemap stays
   below). The session-state update branch was refactored onto the same
   helpers. New `layerStylePresets` ref records `style_preset` per layer for
   the command path. Plus the idle re-arm fix above.
2. **`web/src/LayerPanel.tsx`** — new optional `onMapCommand` prop; the three
   user handlers now emit real `MapCommandPayload`s alongside the local
   dispatch (`set-layer-opacity` clamped, `set-layer-visibility`,
   `set-layer-order` top-first).
3. **`web/src/App.tsx`** — passes `onMapCommand={bus.pushMapCommand}` so
   panel intents fan out through the shared bus to MapView (the echo back
   into the panel's own reducer is an idempotent re-set).

## Evidence

- **vitest** (`web/src/Map.test.tsx`, `web/src/LayerPanel.test.tsx`):
  32 files / 522 tests green. New suites:
  - helper unit tests (group membership, sublayer coverage, clamp, no-op on
    absent layer, bottom-first moveLayer order)
  - MapView map-command application tests (opacity/clamp/visibility/order)
  - **end-to-end over the App bus**: LayerPanel + MapView share
    `createLayerPanelBus`; firing a real `change` event on the slider asserts
    `setPaintProperty("flood-demo","raster-opacity",0.3)` on the maplibre mock
  - LayerPanel emission tests + idle re-arm regression test
- **Playwright live probe** (`web/tools/playwright_job0258_layer_controls.mjs`
  against the RUNNING dev server :5173, real DOM events, dev-seam
  `__grace2InjectCaseOpen` only — zero chat messages, zero Gemini calls,
  `window.WebSocket` stubbed inert so the live agent on :8765 saw zero
  traffic): **6/6 checks PASS** (`evidence/results.json`):
  - real mouse click on opacity slider → `fill-opacity` 0.4 → 0.032
    (`evidence/01_before_opacity.png` / `02_after_opacity.png` — solid magenta
    polygon vs nearly transparent)
  - raster slider → `raster-opacity` 1 → 0.28 (the flood-COG demo path)
  - real mouse drag on the dnd-kit handle → `getStyle().layers` order changed
    to exactly the panel's top-first order (`03_after_reorder.png`)
  - visibility checkbox → layout visibility `none` → `visible`
    (`04_after_hide.png`)

## Out-of-scope findings (flagged, not fixed here)

1. **Deployed QGIS Server WMS basemap is DOWN**: every GetMap for
   `basemap-osm-conus` returns 500 `<ServerException>Layer(s) not valid</ServerException>`
   at ~2.5 s/request (curl-verified). The light-theme demo basemap is
   currently blank because of this; it also delays `idle` events long enough
   to starve deferred layer application. Needs an infra/engine job
   (`.qgs` layer validity / upstream OSM proxy on the QGIS Server container).
2. **Initial paint order is add-order, not z_index**: the session-state add
   loop paints in array order, and async vector adds can land after sync
   raster adds, so the initial map stack can disagree with the panel's
   z_index-sorted view until the first user reorder (which now fixes it).
   Candidate follow-up: apply `applyLayerOrder` after batch reconciliation.
3. **Panel intents are client-local**: the agent/session is not informed of
   user opacity/visibility/order changes (no persistence across Case
   reopen). This matches the documented M4-deferred agent-intent work.

## Files changed

- `web/src/Map.tsx` (helpers + map-command application + idle re-arm)
- `web/src/LayerPanel.tsx` (onMapCommand emissions)
- `web/src/App.tsx` (bus wiring)
- `web/src/Map.test.tsx`, `web/src/LayerPanel.test.tsx` (new suites)
- `web/tools/playwright_job0258_layer_controls.mjs` (live probe)
- `reports/inflight/job-0258-web-20260610/{audit.md,report.md,STATE,evidence/*}`
