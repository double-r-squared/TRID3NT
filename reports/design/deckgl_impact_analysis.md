# deck.gl Impact Analysis — Decision-Ready Document

Status: DECISION-READY. Authored 2026-06-21 (sprint-17 engine sub-sprint). Synthesizes 8
read-only research lanes (map-core, draw-pick, animation, durability/cold-view,
controls-legend, mobile/perf+bundle, tests, threed-design) into one blast-radius map, risk
register, 3D-mode design, gated wave plan, and a do-NOT-do list.

Companion / upstream: `reports/design/deckgl_visualization_framework.md` (the proposal this
analysis pressure-tests). This document is the GO/NO-GO + execution plan.

---

## 1. Executive summary + recommendation

deck.gl earns its keep on exactly four surfaces GRACE-2 cannot do well today: true 3D
(extruded buildings, terrain relief, depth-as-elevation flood), smooth interpolated
animation, declarative mesh/vector rendering, and a single collapsed layer model. The
coastal-flood North Star (flood animates in, 3D on, buildings extruded, on mobile AND
desktop) is the forcing function.

The integration is **viable but NOT a drop-in**. Three structural facts dominate every lane:

1. **The camera is hard-locked 2D** at `web/src/Map.tsx:2278-2281` (`maxPitch:0`,
   `dragRotate:false`, `pitchWithRotate:false`, `touchPitch:false`) and double-locked at
   `:2290-2291` (`touchZoomRotate.disableRotation()` + `keyboard.disableRotation()`). The
   in-code comment says the double-lock exists so a future change can't silently re-enable
   rotation — i.e. the codebase actively resists this change. No 3D pixel renders until this
   is relaxed at runtime. **Confirmed against HEAD.**
2. **Every imperative render call site is bound to MapLibre string-id APIs** —
   `setPaintProperty` / `setLayoutProperty` / `moveLayer` / `m.getLayer()` / `addSource` /
   `removeSource`. deck layers have no MapLibre source/layer, so these calls *no-op* on deck
   content (`applyLayerOpacity` early-returns at `Map.tsx:1225` on `!m.getLayer(layerId)`).
   Controls, eviction, z-order, animation, and cold-view all flow through these call sites.
3. **No mobile gating substrate exists.** `web/src/hooks/useIsMobile.ts` is a single 768px
   width media query — no DPR cap, no GPU-tier probe, no max-feature ceiling, no
   `prefers-reduced-motion` in the map/animation path. The North Star is mobile-first; deck
   at native DPR with terrain + extruded buildings + a 24-frame animation is a GPU cliff.

The good news: the right seams already exist or are cheap to add. `layer_cache.ts` is
renderer-agnostic and needs **zero** change. `animation_controller.ts` is renderer-agnostic
via the injected `FrameVisibilityEmitter` (`animation_controller.ts:62`) and needs **zero**
change. `getActiveMap()` (`Map.tsx:230-234`) has zero external consumers (grep-confirmed),
so a sibling `getActiveDeckOverlay()` seam is free. The bus/contract layer
(`contracts.ts:377-399`, `LayerPanel.tsx` -> `Map.tsx:2970-3002`) is target-agnostic and
must stay unchanged — fork only at the executor.

**Recommendation: GO-WITH-SPIKE.**

Ship **Phase 0 (MapLibre-native 3D toggle, NO deck.gl dependency)** first — it relaxes the
pitch lock behind a runtime toggle, adds `setTerrain` from a TiTiler terrain-RGB DEM, and
extrudes buildings via MapLibre `fill-extrusion`. That alone satisfies *most* of the coastal
acceptance (terrain + standing extruded buildings + draped 2D flood animation) with no new
rendering library, no peer-dependency risk, and a fully reversible toggle. Then gate the
deck.gl overlay itself behind a **Phase 1 spike** that proves the overlaid `MapboxOverlay`
attaches to maplibre-gl `4.7.1`, renders the real coastal Case mesh + a 3D flood depth
surface, and holds frame rate on mobile. The spike is the go/no-go for Phases 2-4. Do not
commit deck.gl to the bundle until the spike passes on a real phone.

Why not plain GO: the deck.gl/maplibre-gl `4.7.1` peer-compat is officially unverified
(deck `MapboxOverlay` declares `mapbox-gl`, not `maplibre-gl`), the mobile gating substrate
is net-new and load-bearing, and three contracts (durability/cold-view, draw/pick, the test
fleet) break hard if deck is wired naively. Why not HOLD: Phase 0 delivers real North-Star
value with near-zero structural risk and is independently shippable, and the seams that
make the deck path safe are already proven (cache + controller are pure; bus is agnostic).

---

## 2. Blast-radius map (every subsystem deck.gl touches)

| Subsystem | Files / anchors | What deck.gl touches |
|---|---|---|
| Map instance + reconcile | `Map.tsx:2271-2321` (create effect), `2338-2659` (`applyLatest`), `230-234` (`getActiveMap`) | Overlay attach/detach must join the single create-effect lifecycle; deck layers must NOT route through the `addedSourceIds` reconcile. |
| Camera lock | `Map.tsx:2278-2281`, `2290-2291` | Hard 2D lock must become a runtime-toggleable mode. Single most load-bearing change. |
| Dark-theme swap | `Map.tsx:2705-2756` | `firstFloodLayer` scan over `getStyle().layers` mis-targets if deck runs interleaved; terrain/extrusion state destroyed by `setStyle` on theme toggle. |
| Z-order / drag-reorder | `Map.tsx:1283-1295` (`applyLayerOrder`->`moveLayer`), `2629-2658`, `2989-2999`, `1195-1209` (`layerGroupMemberIds`) | `moveLayer` cannot order deck layers; cross-plane stacking (MapLibre raster above deck layer) is unrepresentable in overlaid mode. |
| Controls (opacity/visibility) | `Map.tsx:1218-1271` (`applyLayerOpacity`/`Visibility`), `2970-3002` (bus handler) | All `setPaint/Layout` calls no-op on deck; must fork executor by render-target. Bus + contract stay unchanged. |
| Pelicun choropleth | `vector_rendering.ts:402` (`buildDsMeanExpression`), `Map.tsx:992,1001`, `vector_rendering.ts:218-260` | MapLibre `['interpolate',...]` expression is unconsumable by deck `getFillColor` JS accessors. |
| Animation / scrubber | `animation_controller.ts:62` (emitter, agnostic), `Map.tsx:3017-3037` (the ONE MapLibre coupling), `LayerPanel.tsx:366-436` (NAME-based group detect), `SequenceScrubber.tsx`, `LayerLegend.tsx:261-269` | Re-point the single emitter; preserve N-length AnimGroup for play-gate; swap per-layer `{visible}` cache writes for one `{frameIndex}`. |
| Durability / cache | `layer_cache.ts` (agnostic, ZERO change), `Map.tsx:2171` (`addedSourceIds`), `2446-2472` (eviction), `2489-2627` (add branch) | Eviction loop + add branch + apply* are MapLibre-blind to deck; must fork removal/add/apply by backend. |
| Cold-view (#165) | `App.tsx:1094-1148`, `case_view.ts:127-169`, feeds shared reconcile | Cold box-off open hits no deck branch -> paints NOTHING for deck layers, with no agent to recover. Highest-stakes surface. |
| Draw / pick | `draw_controller.ts:97-159`, `SpatialDrawSurface.tsx:124-208,533-576`, `Map.tsx:3198-3336,3518-3554,3679-3687` | Pointer interception (overlaid canvas), bbox-drag geometry under pitch, `queryRenderedFeatures` blind to deck features. |
| Legend / colormap | `LayerLegend.tsx:218-228,278` (raster-gated), `titiler_colormap.ts:82,131` (URL-only) | No legend for any deck 3D layer unless the TiTiler raster twin survives or explicit colormap is threaded. |
| Mobile / perf | `useIsMobile.ts:20-81` (width-only), no DPR/GPU/reduced-motion gate | Net-new gating substrate required before 3D-on-mobile. |
| Bundle / build / deps | `package.json:11-39`, `package-lock.json:3942-3970` (maplibre-gl `4.7.1` exact), `vite.config.ts:30-45` (no `manualChunks`), `tsconfig.json` (strict) | +250-450KB gz into the App chunk; unofficial peer; strict `tsc --noEmit` gate; happy-dom has no WebGL. |
| Tests | `test-setup.ts` (no shared mock), 6 hand-rolled `MockMap` files, `Map.test.tsx:1304` (asserts `addControl` NOT called), `playwright.config.ts:26-50` (no specs yet) | Fleet-wide failure if overlay constructs real WebGL at mount; the `addControl`-not-called assertion directly contradicts overlay-as-control. |
| 3D-mode surface | `App.tsx:2012-2013` (SettingsPopup, toggle home), `contracts.ts:305-310` (`MapView.pitch` hardcoded 0), `case_zoom.ts:26-65` (bbox-only replay) | Toggle has no home; pitch/bearing not persisted; no terrain/building/height data source exists. |

---

## 3. Risk register (sorted blocker -> high -> medium -> low)

### Top blockers (call-outs)

The brief flagged six likely blockers; all are confirmed, plus two more from the durability
and tests lanes:

- **Pitch-lock relaxation** — global-by-construction, codebase actively resists it. Nothing
  3D renders until fixed. (`Map.tsx:2278-2281,2290-2291`)
- **Durability / cold-view contract** — eviction loop, add branch, and cold replay are all
  MapLibre-blind to deck; deck layers either leak across Cases (under-eviction) or paint
  NOTHING on a box-off cold open with no agent to recover. (`Map.tsx:2446-2472`, `2531-2542`,
  `App.tsx:1094-1148`)
- **Animation re-point** — the emitter hard-assumes N MapLibre layers; deck frames no-op.
  (`Map.tsx:3017-3037`)
- **terra-draw / pick + pitch** — pointer interception (overlaid canvas) and bbox-drag
  geometry skew under pitch corrupt the exact coastal AOI selection path.
  (`SpatialDrawSurface.tsx:176-194`, `draw_controller.ts:107`)
- **Mobile GPU** — no DPR/GPU/feature gating; native-DPR deck 3D on a phone risks GL OOM on
  the mobile-first demo. (`useIsMobile.ts:47-81`)
- **Test surface** — 6 hand-rolled MockMaps, no deck mock, `Map.test.tsx:1304` asserts
  `addControl` was NOT called; naive attach throws fleet-wide.
- **Pelicun MapLibre-expression incompatibility** — `buildDsMeanExpression` returns a
  MapLibre expression array deck cannot consume. (`vector_rendering.ts:402`)
- **Controls executor lock** — every opacity/visibility/order control no-ops on deck; the
  exact regression job-0258 (#107) fixed, reintroduced. (`Map.tsx:1225,1268,1283`)

### Full table

| What breaks | Where | Severity | Mitigation |
|---|---|---|---|
| Camera hard-locked 2D; no runtime setter for pitch/rotate exists; nothing 3D renders, and a flag-only toggle leaves 2D tilted | `Map.tsx:2278-2281,2290-2291` | blocker | Add `threeDEnabled` prop + dedicated effect calling ALL six runtime setters (`setMaxPitch`, `dragRotate`/`touchPitch`/`pitchWithRotate` enable, `touchZoomRotate`/`keyboard` enableRotation) and `easeTo({pitch:0,bearing:0})` on OFF; keep ctor at `maxPitch:0` so 2D default is byte-identical. |
| Eviction seatbelt structurally blind to deck: `getSource`/`removeLayer`/`removeSource`/`layerGroupMemberIds` all no-op -> deck layers NEVER torn down -> leak across Cases | `Map.tsx:2446-2472,1195-1209` | blocker | Make `addedSourceIds` a `{layerId->'maplibre'|'deck'}` registry (or sibling `deckLayerIds`); fork the REMOVAL action only; keep cache `allowsEvict` as the single arbiter; rebuild deck array from `cache.layersFor()` and `setProps` each reconcile (declarative removal). |
| Reconnect re-add skip: `addedSourceIds.has(id)` conflates "tracked" with "mounted"; a tracked-but-absent deck layer is skipped -> stays invisible after reconnect | `Map.tsx:2531-2542,2545-2627` | blocker | Add a deck add branch; make the skip check actual presence in the live deck array, not membership in `addedSourceIds`; rebuild full deck array from cache each reconcile (idempotent). |
| Cold-view (#165) box-off open hits no deck branch -> paints NOTHING for the North-Star layers, no agent to recover | `App.tsx:1094-1148`, `case_view.ts:127-169`, `Map.tsx:2489-2627` | blocker | Cold snapshot `loaded_layers` must carry render-backend tag + geometry URL + style params; cold-path deck branch builds identically to live; explicit cold-path test (box off, open coastal Case, assert deck flood paints from snapshot alone). Aligns with #165 persist-vector-GeoJSON direction. |
| Animation emitter hard-assumes N MapLibre layer ids + `setLayoutProperty('visibility')`; deck frames no-op | `Map.tsx:3017-3037` | blocker | DO NOT touch `animation_controller.ts` (pure). Re-point ONLY the emitter at `Map.tsx:3019`; branch on layer ownership: deck frames -> `overlay.setProps` time prop / data URL; keep MapLibre branch for legacy raster-COG-per-frame stacks. |
| Group detection collapse: if frames become ONE deck layer, `detectSequentialGroups` returns 0 groups -> scrubber never renders, play-gate (`layerIds.length>1`) never arms | `LayerPanel.tsx:366-436`, `animation_controller.ts:303-307` | blocker | Keep N `ProjectLayerSummary` rows as frame metadata even when rendered through one deck layer (lowest risk), OR synthesize an AnimGroup with N SYNTHETIC frame ids (length N, not 1) so the play-gate stays armed. |
| Controls dead: `applyLayerOpacity/Visibility/Order` all bind to `setPaint/Layout`/`moveLayer` on member ids; deck layers no-op | `Map.tsx:1225,1268,1283`, `2970-3002` | blocker | Render-target discriminator per layer; fork the three apply* helpers (MapLibre branch byte-identical, deck branch mutates spec + `setProps`); KEEP bus + contract unchanged; cache write-through stays (keyed by `layer_id`). |
| Extruded buildings: NO footprint-with-height source AND no extrusion render path (web emits flat `fill`+`-outline` only); invisible at pitch 0 | `Map.tsx:988-1032`, `vector_rendering.ts:459` | blocker | Phase the work: (1) engine footprint fetcher (Overture/OSM/MS Buildings) carrying height or `num_stories*3m`; (2) extrusion branch keyed on a `buildings` preset via MapLibre `fill-extrusion` (Phase-0, no deck) OR deck `PolygonLayer(extruded:true)`; add `buildingHeightFor()` accessor; default constant height when absent. |
| Mobile GPU blowout: no DPR/GPU/feature gating; deck at native DPR + terrain + extrusion + 24-frame anim on a phone risks GL OOM | `useIsMobile.ts:47-81`, `Map.tsx:2273-2283` | blocker | Ship gating BEFORE 3D-on-mobile: DPR cap (`useDevicePixels: min(dpr,1.5)` mobile), max-feature ceiling for extrusion (reuse `vector_density` cap `Map.tsx:271-281` + `CLUSTER_THRESHOLD` `:880`), WebGL2 capability probe gating 3D availability, 2D fallback so it degrades not crashes. |
| Test fleet: no shared MapLibre/deck mock; overlay-at-mount touches WebGL -> all 6 Map.*.test files throw at render; `Map.test.tsx:1304` asserts `addControl` NOT called | `Map.test.tsx:73-159,1301-1305`, `test-setup.ts` | blocker | Global `vi.mock('@deck.gl/mapbox')` + `@deck.gl/layers` in `test-setup.ts`; consolidate the 6 MockMaps into `test/__mocks__/maplibre-gl.ts`; update `:1304` to assert NOT a `NavigationControl` rather than "never called". |
| Dark-theme swap mis-targets `firstFloodLayer` if deck runs interleaved (deck injects synthetic ids into `style.layers`) | `Map.tsx:2717-2756` | high | Attach OVERLAID (`interleaved:false`) so deck never enters `style.layers`; if interleaved ever required, give deck ids a known prefix and exclude it in the `firstFloodLayer` scan. |
| Terrain state destroyed by theme toggle: `applyTheme` `setStyle`/removeLayer/addLayer wipes `setTerrain` raster-dem (and interleaved deck layers) | `Map.tsx:2705-2754` | high | Make terrain registration idempotent and RE-ASSERT it after `applyTheme`/`setStyle`; prefer OVERLAID deck (control, not style state, survives `setStyle`). |
| Z-order durability collapse across renderer boundary: `moveLayer` can't order individual deck layers; cross-backend zIndex unrepresentable; reorder reverts on reconnect | `Map.tsx:2629-2658,1283-1295,2989-2999` | high | Decide stacking contract up front: (a) deck composites at one fixed band, zIndex reorders WITHIN each backend (documented regression for mixed stacks — acceptable for coastal North Star where flood+buildings ARE top), or (b) interleaved per-layer `beforeId`. Partition the flat `layer_ids` list into MapLibre sublist (`moveLayer`) + deck sublist (array order). |
| Per-layer override re-apply on (re)add hard-wired to MapLibre paint/layout; persisted opacity/visible read from cache but NEVER applied to deck -> reverts on reconnect | `Map.tsx:2497-2540,1218-1271` | high | (Re)construct deck layers FROM cache override at reconcile (`opacity`/`visible` from `getOverride`), then `setProps` whole array; add deck branches to apply* so map-command + reconcile both reach deck; verify `visible:false` survives forced reconnect. |
| Per-frame cache override assumes per-layer-id visibility; one deck layer has no per-frame id -> scrubbed frame lost on reconnect (snaps to default last-frame) | `Map.tsx:3030`, `LayerPanel.tsx:975-978` | high | Replace N per-layer `{visible}` writes with ONE per-Case `{frameIndex}` override keyed by deck layer id; on reconcile read frameIndex back and `setProps`. |
| Play-gate requires `layerIds.length>1`; single-deck-layer group (length 1) -> play/pause silently does nothing | `animation_controller.ts:304-316` | high | Synthetic AnimGroup MUST carry N frame entries in `layerIds` (parallel to `frameLabels`), not a single deck id. |
| Pelicun choropleth fill-color is a MapLibre `['interpolate',...]` expression deck cannot consume | `vector_rendering.ts:402`, `Map.tsx:992,1001` | high | Add sibling JS interpolator `dsMeanColorAccessor(feature)->[r,g,b]` porting the SAME 3-stop ramp (`2DC937`/`E7B416`/`CC3232`, slate `708090` fallback); keep `buildDsMeanExpression` for MapLibre; drive `getElevation` by `ds_mean` for 3D; fold opacity multipliers into alpha. |
| Pointer interception by a standalone overlaid DeckGL `<canvas>` over `map.getCanvas()` -> kills terra-draw + pick-mode + `queryRenderedFeatures` clicks | `draw_controller.ts:107`, `SpatialDrawSurface.tsx:164,195-197` | high | Mandate `MapboxOverlay`-via-`addControl` (no extra DOM canvas) OR `interleaved:false` + deck `pickable:false`; never overlay an interactive deck canvas above MapLibre while a spatial-input request is active; add a guard/test asserting `map.getCanvas()` is the single pointer target. |
| bbox-drag pick + AOI rectangle geometrically wrong under pitch>0 (screen rect -> trapezoid) -> AOI sent to solver != AOI drawn; corrupts the coastal AOI path | `SpatialDrawSurface.tsx:176-194,635-642`, `Map.tsx:2278-2281` | high | Force `pitch=0`/`bearing=0` while ANY spatial-input request is active regardless of global 3D mode; snapshot+restore on unmount; draw/pick is a 2D-only sub-mode (documented). |
| Per-frame animation emitter no-op on deck -> the literal flood/wave animate-in acceptance does not advance | `Map.tsx:3019-3031` | high | Same target-discriminator fork; for smooth 3D drive a single deck layer with a time/depth prop (controller stays pure). |
| 3D flood/wave DEPTH has no depth geometry — flood is flat raster COG + visibility-flip; "depth in 3D" cannot come from current path | `Map.tsx:2601-2624` | high | Tier-1 (cheap): 3D ON for terrain + buildings only, flood stays draped 2D raster wash (satisfies most North Star). Tier-2 (real depth): deck layer reading depth COG as elevation (TerrainLayer/ColumnLayer) + repoint emitter to swap deck data per frame. |
| Bundle bloat: no `manualChunks`; static deck import lands in ~1.5MB App chunk; +250-450KB gz | `vite.config.ts:30-45`, `Map.tsx:2273` | high | Add `manualChunks` splitting `@deck.gl/*`/`@luma.gl/*`/`@loaders.gl/*`/`@math.gl/*` into own async chunk; dynamic-import overlay only when 3D toggled ON (mirror `await import('vega-embed')`); import granular subpackages only; CI bundle budget. |
| Peer mismatch: `MapboxOverlay` declares `mapbox-gl` peer, not `maplibre-gl`; maplibre-gl `4.7.1` pinned exact; deck minor bump can silently break the handshake | `package.json:14`, `package-lock.json:3942-3970` | high | Pin deck (9.x line) + `@luma.gl/*` lock-step to exact versions verified against `4.7.1`; thin compat smoke test against the REAL maplibre instance; `overrides` if npm refuses peer graph; do NOT let install dedupe/bump maplibre-gl (also breaks terra-draw adapter peer `5655-5662`). |
| Pitch unlock re-enables touch-pitch/rotate on mobile (no perf guard); pitched frustum multiplies tiles/features | `Map.tsx:2278-2281` | high | Lift locks ONLY in 3D mode; clamp `maxPitch` (60); consider disabling free rotation on mobile; couple with DPR cap + feature ceiling. |
| Interleaved GL-context collision: shared context conflicts with terra-draw, getCanvas picking, raster repaint; context-loss blanks the map | `Map.tsx:2273-2283`, `draw_controller.ts:106-107` | high | Prefer `interleaved:false` first cut (deck owns separate canvas, can't corrupt GL state); add `webglcontextlost` handler reverting to 2D; test deck+terra-draw co-existence (draw an AOI with overlay live). |
| No Playwright specs exist; WebGL/extrusion/animation North Star cannot be unit-tested in happy-dom | `playwright.config.ts:26-27` | high | Stand up a spec under `tests/m3/playwright`: load coastal Case, toggle 3D, assert deck canvas present + non-blank, drive scrubber, run under a mobile device descriptor + a `reducedMotion:'reduce'` project. |
| Overlay attach/detach not in the create-effect lifecycle -> GPU leak + double-attach on StrictMode/dev double-invoke | `Map.tsx:2271-2321` | medium | Store overlay in `useRef`; `addControl` after `m.once('load')`; cleanup `removeControl` + `overlay.finalize()` BEFORE `m.remove()`; guard with existing `if (map.current) return`; expose `getActiveDeckOverlay()` sibling to `getActiveMap()`. |
| Idle-starvation: animating deck calls `triggerRepaint` continuously -> MapLibre `idle` may never fire -> idle-gated reconcile fallback delayed; legend rAF on `render` thrashes every deck frame | `Map.tsx:2358-2361,2684-2691,3092-3106` | medium | `applyLatest` synchronous path (sticky `mapStyleReady` latch `486-497`) covers it — verify no batch depends solely on `idle` (theme `2713`, AOI-clear `2929-2933`, dense-vector `2565-2573`); gate legend recompute on actual camera-transform change, or move off `render` to `move`/`zoom`/`moveend`. |
| queryRenderedFeatures blind to deck features + parallax offset under pitch (`e.lngLat` = ground point, not building top) | `Map.tsx:3220,3329,3518,3539`, `SpatialDrawSurface.tsx:159-162` | medium | Keep picking MapLibre-only for v1; deck `pickable:false`; force `pitch=0` during pick so `e.lngLat`==visual point; if deck must be clickable later, route `deck.onClick`/`pickObject` and merge. |
| Legend pipeline raster-only + URL-only: no legend for a pure-deck 3D flood; scrubber-reserve goes false if detection collapses | `LayerLegend.tsx:278,218-228`, `titiler_colormap.ts:131` | medium | Keep legend driven by the TiTiler raster twin (it stays on TiTiler per plan); only if depth surface is pure-deck, thread explicit `{colormapName,rescale}` onto the deck spec and relax the `:278` raster gate behind a "has explicit colormap" check; reuse `getColormapStops`. |
| `layerGroupMemberIds` member model (`-clusters`/`-cluster-count`/`-outline`) assumed by opacity+visibility+eviction; deck is a single layer | `Map.tsx:1195-1209` | medium | Return `[]` for deck-targeted ids; add a parallel deck removal path wired into the same eviction gate + Case-switch clear so deck honors per-Case lifecycle. |
| `syncFrameVisibilityLocal` + `initializedGroupsRef` collapse target layer_ids that no longer exist; panel radio-dot drifts from deck's actual frame | `LayerPanel.tsx:964-999,1008-1022` | medium | For deck-owned groups make the panel mirror READ-ONLY w.r.t. visibility (radio-dot from `frameIndexFor`); stop dispatching `local-visibility`/`writeLayerVisibilityOverride` for synthetic ids; collapse becomes no-op. |
| `setDraggability` save/restore desync: adapter caches `dragRotate` at construction; if 3D enables it before DrawController constructs, it re-enables rotate after every gesture | adapter ctor, `SpatialDrawSurface.tsx:178,193,203` | medium | Construct DrawController only AFTER forcing `dragRotate` disabled (pitch-lock mitigation does this); restore PRE-pick `dragPan` state, not unconditional enable; centralize camera-interaction toggles behind one helper. |
| Idempotent-reconcile tests assert exact add/removeSource counts; deck adds a 2nd surface -> "no duplicate" assertions break | `Map.test.tsx:393-448,535-584` | medium | Keep reconcile renderer-agnostic (desired-layer list -> dispatch to adapter); unit-test the reconcile DECISION as a pure function; track deck overlay's layer set in the shared mock. |
| Feature-inspect tests assume `map.on('click')`+`queryRenderedFeatures`; deck features invisible to it; mobile TAP test is the North-Star surface | `Map.featureInspect.test.tsx:215-280` | medium | Decide picking ownership; if deck owns 3D picking add a parallel pick path + tests; keep MapLibre picking for raster/2D vector; add a routing test (deck->overlay pick, MapLibre->qRF). |
| Camera-lock has ZERO regression guard (no test asserts `maxPitch:0`/`dragRotate:false`); a 3D toggle could silently break the 2D guarantee | `Map.tsx:2278-2281` (no test) | medium | Add characterization tests reading `lastMapMock._constructorOptions` asserting `maxPitch:0`+`dragRotate:false` in default/2D, and `maxPitch>0` only when 3D flag on. |
| Animation-emitter tests assert `setLayoutProperty('visibility')`; 3D deck frames move to `overlay.setProps` -> tests assert wrong API | `Map.test.tsx:2850-2924` | high | Add a `FrameRenderer` adapter interface (MapLibre impl = `setLayoutProperty`, deck impl = `setProps`); split into 2D-path and 3D-path tests; controller stub tests unaffected. |
| Pitch/bearing not persisted; case-reopen replay is bbox-only; 3D state lost on reopen/reconnect | `contracts.ts:305-310`, `case_zoom.ts:26-65` | medium | Persist 3D-mode as client UI state in localStorage (theme precedent `App.tsx:128-129`); if per-Case tilt wanted, extend zoom-to replay to carry pitch+bearing. |
| 3D toggle has no home + must be responsive; naive placement overlaps scrubber (z 51)/legend/mobile drawer | `App.tsx:2012-2013,1381-1397` | medium | Mirror theme toggle into SettingsPopup (responsive on both surfaces) for v1, OR a floating top-right control with z-index between frost (15) and scrubber (51), suppressed during draw. |
| Legend/scrubber/AOI anchoring assume top-down camera; under pitch the bbox projects to a trapezoid -> anchors drift/jitter | `Map.tsx:1744-1906,3046-3120`, `SequenceScrubber.tsx:144-161` | high | When 3D on, fall back to viewport-bottom-center scrubber/legend placement (already exists), or recompute anchor from the projected trapezoid's lowest screen point; cheap path: force `aoiRect=null` while tilted. |
| Terrain source does not exist (no `setTerrain`/raster-dem/terrain-RGB anywhere) | `Map.tsx` (zero hits) | high | Stand up DEM COG -> terrain-RGB on existing TiTiler (XYZ template pass-through), add raster-dem source + `setTerrain` behind 3D toggle. |
| Tree-shaking: `import from 'deck.gl'` umbrella pulls all layer modules (not all sideEffect-free) | `vite.config.ts`, `package.json` | medium | Import granular subpackages only; verify with bundle visualizer no aggregation/mesh/json layers pulled; keep deck in its own `manualChunks` bucket. |
| Strict `tsc --noEmit` gate: deck/luma generic typings can fail strict tsconfig -> blocks ALL deploys | `package.json:8`, `tsconfig.json` | medium | Validate `tsc --noEmit` against pinned deck in a branch first; wrap deck in a typed adapter module; isolate type fights in one file; do NOT relax global strictness. |
| Mobile: no DPR/feature/reduced-motion gating -> deck + terra-draw share GPU, draw gesture degrades, gesture mis-classified | `Map.tsx:3321-3334`, `SpatialDrawSurface.tsx:180-185` | low | While spatial-input active on mobile, suppress deck 3D layers or cap DPR/disable extrusion; pause feature-inspect mousemove handler; pitch-lock lets you fully drop deck during draw. |
| pmtiles dead-branch test asserts inert pass-through; 3D buildings may revive pmtiles -> false-positive guard | `Map.vectorTiles.test.tsx:200-235` | low | When buildings/pmtiles wired, update test to assert live load path; add deck-extrusion unit test on the pure layer-spec builder. |
| No `prefers-reduced-motion` anywhere in animation path -> continuous deck flood ignores OS reduce, drains battery, off-tab keeps GPU hot | `animation_controller.ts:69-87,309` | medium | Add `useReducedMotion` (matchMedia); render flood at final state (or slow cross-fade) when set; drive deck anim off rAF gated by `document.visibilityState`. |
| Default-frame seeding shows LAST frame on first sight -> flood appears at peak before play (undercuts animate-in) | `animation_controller.ts:196-200` | low | Per-group seed policy: flood/wave "animate-in" groups seed `frameIndex=0`; forecast-hour groups keep last-frame; one-line branch in `setGroups`. |
| `deleteLayer` trash + Case-switch `evictCase` only fire MapLibre removal -> trashed deck layer stays painted | `App.tsx:1289-1298,882-906`, `Map.tsx:2446-2472` | high | Same backend-dispatched removal; rebuild deck array from `cache.layersFor()` + `setProps` each reconcile so trashed/evicted layer disappears declaratively. |
| Discrete 1100ms setInterval cadence vs deck continuous interpolation -> flood-in looks like a slideshow not a smooth wave | `animation_controller.ts:121-128,303-316` | medium | Keep controller as the discrete FRAME-INDEX clock; let the deck emitter interpolate BETWEEN indices on its own rAF (lerp `currentTime` i->i+1 over `intervalMs`); do NOT move interpolation into the controller. |
| SSR/test fragility: happy-dom no WebGL; static deck import at Map module top throws under tests | `vite.config.ts` (test), `Map.tsx:2273` | low | Dynamic-import overlay inside the 3D-toggle effect (not module top), or vitest mock/alias `@deck.gl/mapbox`; keep deck behind runtime capability+toggle guard. |

Counts: **9 blockers, 17 high, 12 medium, 6 low** (44 distinct breakage points across 8 lanes).

---

## 4. 3D MODE design + COASTAL FLOOD acceptance

### 4.1 The 3D-mode surface (mobile + desktop)

- **Toggle home:** mirror the theme toggle into `SettingsPopup` (`App.tsx:2012-2013`,
  `toggleTheme` at `:650`) — it is already responsive across mobile and desktop and avoids
  z-order collisions with the scrubber (z 51), legend, and mobile drawer. A single
  `threeDEnabled` boolean threads from App -> MapView alongside the existing `theme` prop.
  Optional later: a floating top-right map control with z-index between frost (15) and
  scrubber (51), suppressed while a spatial-input draw surface is active.
- **Persistence:** 3D-mode is client UI state in localStorage (theme precedent
  `App.tsx:128-129`), NOT per-Case server state for v1, so it survives reload globally.
- **Camera contract:** ON = `setMaxPitch(60)` + enable `dragRotate`/`touchPitch`/
  `pitchWithRotate` + `touchZoomRotate.enableRotation()` + `keyboard.enableRotation()`.
  OFF = inverse + `easeTo({pitch:0,bearing:0})`. Constructor stays `maxPitch:0` so default
  is byte-identical to today.
- **Mutual exclusion with editing:** 3D auto-disables (pitch re-locked) while a
  spatial-input request is active (`spatialRequest != null`, `Map.tsx:2268`) and while
  terra-draw is engaged; restored on submit/cancel. Draw/pick is a documented 2D-only
  sub-mode. This sidesteps the bbox-skew + gesture-collision + GPU-contention breakages in
  one move.
- **Mobile gating (required before 3D-on-mobile):** DPR cap (`min(dpr,1.5)`),
  max-feature ceiling for extruded buildings (reuse `vector_density` cap `Map.tsx:271-281`),
  WebGL2 capability probe gating 3D availability (not width alone), `prefers-reduced-motion`
  short-circuit, 2D fallback on low-end.

### 4.2 Terrain + buildings (Phase-0, MapLibre-native, NO deck dep)

- **Terrain:** DEM COG -> terrain-RGB on the existing TiTiler (XYZ template pass-through via
  `buildWmsTileUrl` `Map.tsx:400`), `addSource` raster-dem + `m.setTerrain` behind the
  toggle. Terrain registration is idempotent and RE-ASSERTED after `applyTheme`/`setStyle`
  (`Map.tsx:2705-2754`) so a theme toggle does not drop relief.
- **Buildings:** new engine footprint fetcher (Overture / OSM / MS Buildings) carrying
  height or `num_stories*3m` fallback, surfaced with a `buildings` style preset; a
  `fill-extrusion` branch keyed on the preset (`fill-extrusion-height` from the height prop);
  `buildingHeightFor()` accessor in `vector_rendering.ts`; constant flat-roof height when the
  attribute is absent so the demo always shows mass.

### 4.3 COASTAL FLOOD acceptance criteria

Tiered so Phase 0 delivers most of it with zero deck dependency:

**Tier 1 (Phase 0, MapLibre-native):**
1. 3D toggle ON raises pitch; terrain relief visible; toggle OFF returns to byte-identical
   2D (regression-guarded by a `_constructorOptions` characterization test).
2. Coastal buildings render EXTRUDED (height from footprint source) and stand above the map.
3. The flood animates IN over standing buildings as a draped 2D raster wash, driven by the
   unchanged `AnimationController` + scrubber; scrubber falls back to viewport-bottom-center
   while tilted.
4. Theme toggle does NOT drop terrain/extrusion.
5. Draw/pick forces 2D; AOI bbox accuracy unchanged (regression test feeding the solver).
6. Works on mobile AND desktop without GL OOM (DPR-capped, feature-ceiling'd).
7. `prefers-reduced-motion: reduce` shows the flood at final state, no auto-run.

**Tier 2 (Phases 1-4, deck.gl):**
8. The deck `MapboxOverlay` (overlaid) attaches to maplibre-gl `4.7.1` and renders the real
   coastal Case quadtree mesh + a 3D flood DEPTH surface (depth COG as elevation).
9. The flood DEPTH animates IN smoothly (deck rAF interpolation between controller frame
   indices), the surface physically rising in z.
10. Controls (opacity/visibility/reorder), per-Case durability, reconnect, cold box-off
    view, and the legend all work for deck layers identically to MapLibre layers.
11. Playwright e2e (chromium + a mobile device descriptor + a reduced-motion project)
    proves a non-blank deck canvas, scrubber drive, and 3D toggle end-to-end.

---

## 5. Wave plan (ordered, gated, file-disjoint where possible)

Cross-cutting seam established FIRST so renderer-agnostic logic stays in pure libs (model =
`vector_rendering.ts` / `layer_cache.ts`, both already pure and unit-tested without
maplibre). The bus + contract (`contracts.ts:377-399`, `Map.tsx:2970-3002`) stay UNCHANGED in
every job; fork only at the executor.

**Regression gate (applies to EVERY job, re-run by an adversarial reviewer):** existing
durability + reconnect + cold-view tests pass; draw/pick AOI bbox accuracy unchanged;
opacity/visibility/reorder controls unchanged for MapLibre layers; raster COG / TiTiler path
byte-identical; full vitest fleet green; `tsc --noEmit` green; 2D-default camera lock
characterization test green.

| # | Job | Files (disjoint where possible) | Gate |
|---|---|---|---|
| J0 | Render-adapter seam + 2D-lock characterization tests (NO behavior change): extract a `RenderTarget` discriminator + `FrameRenderer` interface; add tests reading `_constructorOptions` asserting `maxPitch:0`/`dragRotate:false`; consolidate the 6 MockMaps into `test/__mocks__/maplibre-gl.ts`; add global deck mock stub to `test-setup.ts` | new `lib/render_adapter.ts`, `test/__mocks__/maplibre-gl.ts`, `test-setup.ts`, `*.test.tsx` (test-only) | Fleet green; zero runtime behavior change. |
| J1 | Phase 0a: runtime camera unlock behind `threeDEnabled` prop + SettingsPopup toggle + localStorage persistence; draw/pick forces 2D | `Map.tsx` (camera effect), `App.tsx` (toggle + LS), `SpatialDrawSurface.tsx` (force-2D-while-active) | 2D default byte-identical; toggle ON raises pitch; OFF resets; draw forces 2D; AOI accuracy unchanged. |
| J2 | Phase 0b: TiTiler terrain-RGB DEM source + `setTerrain` + re-assert after theme swap | infra (TiTiler terrain-rgb endpoint), `Map.tsx` (terrain register + applyTheme re-assert) | Relief visible in 3D; theme toggle keeps terrain; 2D unaffected. |
| J3 | Phase 0c: engine building-footprint fetcher (height/num_stories) + `buildings` preset + MapLibre `fill-extrusion` branch + `buildingHeightFor()` | engine fetcher (agent-side), `vector_rendering.ts` (height accessor), `Map.tsx:988-1032` (extrusion branch) | Extruded buildings render in 3D; flat-fill in 2D; cap-honesty tag respected. |
| J4 | Phase 0d: mobile gating substrate (DPR cap, WebGL2 capability probe, feature ceiling, `useReducedMotion`, rAF visibility-gating) + scrubber/legend bottom-center fallback while tilted | new `hooks/useReducedMotion.ts`, `useIsMobile.ts` (capability probe), `Map.tsx` (anchor fallback), `animation_controller.ts` (reduced-motion gate via emitter — controller stays pure) | No GL OOM on a real phone; reduced-motion shows final frame; tilted scrubber stable. **Tier-1 acceptance closes here.** |
| J5 | Phase 1 SPIKE (flag-gated, throwaway-OK): pin deck 9.x + luma lock-step against maplibre `4.7.1`; `manualChunks` + dynamic-import; attach OVERLAID `MapboxOverlay` via `addControl` in the create-effect; render ONE real coastal Case mesh + a deck flood depth surface; measure FPS on desktop AND mobile; compat smoke test against real maplibre | `package.json`/`lock`, `vite.config.ts`, `Map.tsx` (overlay lifecycle + `getActiveDeckOverlay`), `lib/render_adapter.ts` | **GO/NO-GO for J6-J9.** Overlay attaches; mesh + 3D flood render; FPS acceptable on mobile; peer graph clean; bundle in own chunk. |
| J6 | Phase 2: migrate vector + mesh path to deck `GeoJsonLayer`/`PolygonLayer` reusing `vector_rendering.ts` accessors; port `buildDsMeanExpression` -> JS `dsMeanColorAccessor` + `getElevation`; fork eviction/add/apply* by backend; rebuild deck array from `cache.layersFor()` each reconcile | `vector_rendering.ts` (JS accessor), `Map.tsx` (backend-forked eviction/add/apply*), `layer_cache.ts` (ZERO change — verify) | Controls + durability + reconnect work for deck vector layers; cold-view paints; raster path untouched. |
| J7 | Phase 2b: durability/cold-view backend discriminator — render-backend tag on `ProjectLayerSummary`/wire + cold snapshot `loaded_layers`; cold-path deck branch; deck-backend cases in `layer_cache.test.ts`-adjacent reconcile tests | `contracts.ts` (backend field), `case_view.ts`, `App.tsx:1094-1148` (cold replay), `*.test.ts` | Box-off cold open of the coastal Case paints the deck flood from snapshot alone (explicit test). |
| J8 | Phase 3: animation migration — re-point the emitter at `Map.tsx:3019` to a deck `setProps` path; swap N per-layer `{visible}` cache writes for one `{frameIndex}`; preserve N-length AnimGroup (play-gate); deck rAF interpolation between indices; per-group seed policy (flood = frame 0) | `Map.tsx:3017-3037` (emitter), `LayerPanel.tsx` (deck-group read-only mirror), `animation_controller.ts` (seed policy — pure) | Flood DEPTH animates in smoothly; play/pause works; scrubbed frame survives reconnect; legend key stable. |
| J9 | Phase 4: 3D data layers — depth-as-elevation flood surface, decide interleaved mode ONLY where depth-sorting matters; legend source for pure-deck layers; deck picking path (if needed) | `Map.tsx` (depth layer), `LayerLegend.tsx` (relax raster gate behind explicit-colormap), `lib/render_adapter.ts` | **Tier-2 acceptance closes here.** Playwright e2e (chromium + mobile + reduced-motion) green. |

Disjointness notes: J1/J2/J3/J4 touch overlapping regions of `Map.tsx` (camera, terrain,
extrusion, anchors) so they are SEQUENCED, not parallel, within Phase 0 — but J0 (test-only)
and the engine fetcher half of J3 are parallelizable. J6 and J7 split the durability work by
file (Map.tsx executor vs contracts/case_view/App cold-path) and can overlap once J5 passes.

---

## 6. Do NOT do

- **Do NOT go Kepler.gl / headless** as the framework. The seam this analysis builds is a
  thin `MapboxOverlay` driven by `setProps` off the existing session-state bus; a separate
  full app shell discards the entire reconcile/durability/cache investment.
- **Do NOT rip out TiTiler raster.** Raster COGs are the right tool for big rasters and the
  legend pipeline reads colormap/rescale off TiTiler URLs (`titiler_colormap.ts:131`,
  `LayerLegend.tsx:218-228`). Keep raster + basemap MapLibre-native UNDERNEATH the deck
  overlay; deck earns its keep on vector/mesh/3D/animation only.
- **Do NOT big-bang migrate.** Every job is flag-gated and independently shippable; Phase 0
  ships real value with no deck dependency, and the deck overlay is gated behind a J5 spike
  go/no-go on a real phone.
- **Do NOT route deck layers through the existing `addedSourceIds` reconcile / `moveLayer` /
  `setPaint/Layout` call sites.** They are MapLibre-blind; deck content would be torn down,
  leaked, or silently dropped. Fork at the executor; rebuild the deck array declaratively
  from `cache.layersFor()`.
- **Do NOT touch `animation_controller.ts` or `layer_cache.ts` for renderer changes.** Both
  are pure and renderer-agnostic; the controller is driven via the injected
  `FrameVisibilityEmitter` seam. Re-point the emitter, not the controller.
- **Do NOT fork the bus/contract** (`contracts.ts:377-399`, `Map.tsx:2970-3002`,
  `LayerPanel.tsx`). It is target-agnostic; forking ripples into App.tsx + ws.ts for zero
  benefit.
- **Do NOT enable interleaved `MapboxOverlay` for the first cut.** Overlaid (`interleaved:
  false`) avoids `style.layers` injection (theme-swap `firstFloodLayer` collision), GL-context
  corruption, terra-draw pointer interception, and `setStyle` wipe. Reserve interleaved for
  J9 ONLY where deck-vs-MapLibre depth-sorting is genuinely required.
- **Do NOT use the `deck.gl` umbrella import** or `@deck.gl/aggregation-layers` /
  `mesh-layers` omnibus — pulls the whole graph. Import granular subpackages only.
- **Do NOT enable 3D on mobile before the gating substrate (J4) lands.** Native-DPR deck 3D
  on a phone is a GL OOM cliff on the mobile-first demo.
- **Do NOT relax global tsconfig strictness** to ingest deck/luma types; isolate them in a
  typed adapter module.
