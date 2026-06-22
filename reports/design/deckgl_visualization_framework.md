# Design Snapshot: deck.gl as the GRACE-2 Visualization Framework

Status: PROPOSAL / decision pending. Authored 2026-06-21 during the sprint-17 engine
sub-sprint. Intent: snapshot where a deck.gl overlay fits into the current web app so we
can land an implementation decision once the new engine runs (geoclaw / openquake /
landlab / river-seepage + the coastal SnapWave waves) are on the board and we can see the
real visualization load they impose. NATE owns the go/no-go.

This document changes NO code. It is a map + a recommendation + a phased plan.

---

## 1. Why this is on the table

Three threads converged:
- We want a real 3D mode (see terrain relief, water depth, buildings standing up) for
  more detail than a flat raster wash gives.
- Our visualization is rendered three different ways today (raster tiles, ad-hoc client
  GeoJSON, and a frame-swap animation) and several of our roadmap items (the 100k-cell
  quadtree mesh #156, time-stepped wave/flood animation, large vector, 3D) strain that
  split.
- The question "could Kepler.gl simplify this by being a framework" — which points at the
  right idea (a GPU visualization framework) but the wrong product.

## 2. The decision in one paragraph

Keep MapLibre as the base map and our entire UI + case/layer/WS pipeline. Do NOT adopt
Kepler.gl (it is a whole Redux application, not a map component; using it "headless" means
fighting its store, its side-panel UI, and its dataset model the whole way). Instead adopt
**deck.gl** — the GPU layer engine Kepler is built on — as a `MapboxOverlay` that sits on
top of our existing MapLibre map. deck.gl interoperates with MapLibre natively (shared
camera, optional interleaved rendering), so we keep everything we have and gain a single
GPU layer model for the things MapLibre does poorly: dense vector / mesh, 3D extruded
surfaces, and smooth time animation. Raster COGs stay on TiTiler (the right tool for big
rasters). This is additive and incremental, not a rewrite.

Separately and sooner: a basic 3D mode is achievable on MapLibre alone (setTerrain +
fill-extrusion + relaxing the pitch lock) without any framework change. That is a distinct,
cheaper win and need not wait on the deck.gl decision.

## 3. Current rendering architecture (grounded snapshot)

Stack today (`web/package.json`): `maplibre-gl@4.7.1`, `terra-draw`, `flatgeobuf`,
`vega/vega-lite`. No deck.gl, no Kepler, no three.js, no raw WebGL. Adding deck.gl is
greenfield.

Map instance: created once at `web/src/Map.tsx:2273`; shared via a React ref
(`Map.tsx:2165`) and a module singleton `getActiveMap()` (`Map.tsx:230`). The camera is
hard-locked 2D: `maxPitch:0, dragRotate:false, pitchWithRotate:false, touchPitch:false`
(`Map.tsx:2278-2281`) plus rotation disabled. No setTerrain / raster-dem / fill-extrusion /
sky / globe anywhere.

How a LayerURI becomes a visible layer (the pipeline any renderer must satisfy):
1. WS frame `session-state` / `map-command` routed in `ws.ts:1472-1497` to App handlers.
2. A layer-panel bus (`LayerPanel.tsx:1837`) fans map commands into `<MapView>`.
3. One reconcile effect (`applyLatest`, `Map.tsx:2338`) diffs `loaded_layers` and branches
   by `layer_type` into vector-tile (`Map.tsx:2545`), vector/geojson (`Map.tsx:2575`), or
   raster (`Map.tsx:2588`).
4. LayerPanel opacity/visibility/reorder dispatch through the bus into three shared helpers:
   `applyLayerOpacity` (`Map.tsx:1218`), `applyLayerVisibility` (`Map.tsx:1263`),
   `applyLayerOrder` -> `moveLayer` (`Map.tsx:1283`).
5. Per-Case durability via the `LayerCache` "seatbelt" (`web/src/lib/layer_cache.ts`):
   gates eviction (`Map.tsx:2447`), persists per-layer view overrides to IndexedDB,
   clears on authoritative Case switch, survives reconnect (additive snapshots).

Per-concern state:
- Raster (flood/wave COG): MapLibre-native. Add at `Map.tsx:2601-2624`; tile URL built by
  `buildWmsTileUrl` (`Map.tsx:400`, passes TiTiler XYZ templates through untouched);
  colormap/rescale parsed for the legend only (`lib/titiler_colormap.ts`) — the client
  never recolors a raster. This is clean and worth keeping.
- Vector (inline/fetched GeoJSON): MapLibre-native. `registerVectorOnMap` (`Map.tsx:866`)
  adds one geojson source (clustering above 500 features) + per-geometry paint layers
  (circle / line / fill + a separate `-outline`). Style derivation lives in the
  renderer-agnostic `web/src/lib/vector_rendering.ts` (`resolveVectorColor:459`,
  `buildDsMeanExpression:402` for the Pelicun choropleth).
- Vector tiles (dense, F94): `registerVectorTileLayer` (`Map.tsx:1061`). A `pmtiles://`
  branch exists (`Map.tsx:1091`) but NO pmtiles protocol is registered anywhere — that
  path is currently dead/aspirational.
- Mesh (#156): not a distinct layer type — a vector polygon/line whose `style_preset`
  contains "mesh", drawn faint via the same fill+outline path (`Map.tsx:1006-1028`). Dense
  inline GeoJSON is fully materialized in memory (`vector_rendering.ts:164`) — the known
  lag source.
- Animation: ad-hoc. Frames are independent already-registered layers detected by NAME
  (`detectSequentialGroups`, `LayerPanel.tsx:366`); a renderer-agnostic controller
  (`animation_controller.ts`) drives a `setInterval`; the on-map swap is a per-frame
  `visibility` toggle via an emitter registered at `Map.tsx:3017-3037`. This is the weakest
  part for scale.
- 3D / terrain: none exists (2D lock).
- Mobile/perf: `useIsMobile` gates only legend placement + popup sizing — no renderer/DPR/
  layer-count gating; no `prefers-reduced-motion` in the map; no tile-cache tuning. The
  `layer_cache.ts` is a view-state durability cache, NOT a tile/geometry cache.

## 4. Where deck.gl plugs in

Single cleanest attach point: after the instance is created at `Map.tsx:2273`,
`m.addControl(new MapboxOverlay({ interleaved?: ..., layers: [] }))`, and drive
`overlay.setProps({ layers })` from the SAME reconcile loop (`Map.tsx:2489-2627`) that
today calls `addSource`/`addLayer`.

| Concern | Native today? | Current entry point | deck.gl action |
|---|---|---|---|
| Overlay attach | MapLibre-native | instance `Map.tsx:2273` | `addControl(new MapboxOverlay)`; relax pitch lock `:2278` for 3D |
| Raster COG | MapLibre-native (clean) | `Map.tsx:2601-2624`; URL `:400` | KEEP on TiTiler/MapLibre; or deck `TileLayer`/`BitmapLayer` reusing `buildWmsTileUrl` |
| Vector GeoJSON | MapLibre-native | `registerVectorOnMap` `Map.tsx:866` | deck `GeoJsonLayer`; reuse `resolveVectorColor`/`buildDsMeanExpression` as accessors |
| Vector tiles | native; pmtiles dead | `registerVectorTileLayer` `Map.tsx:1061` | deck `MVTLayer`; pmtiles via deck loader |
| Mesh (#156) | ad-hoc preset-vector | `Map.tsx:1006-1028` | folds into the vector deck layer (wireframe accessors) |
| Animation | ad-hoc visibility toggle | emitter `Map.tsx:3017-3037` | repoint emitter to swap deck layer props/URL; controller unchanged |
| 3D / terrain | none | n/a | greenfield: deck `TerrainLayer` / extruded `GeoJsonLayer`, or MapLibre setTerrain |
| Controls (opacity/vis/order) | MapLibre-native | helpers `Map.tsx:1218/1263/1283` | reimplement the 3 helpers against deck `setProps`; bus contract unchanged |
| Per-Case durability/cache | renderer-agnostic | `layer_cache.ts`; gate `Map.tsx:2447` | unchanged |

Carry over unchanged (renderer-agnostic): `vector_rendering.ts` (style derivation),
`titiler_colormap.ts` (legend), `animation_controller.ts`, the layer-panel bus, and
`layer_cache.ts`.

## 5. What it buys us (mapped to real roadmap)

- 3D flood/wave depth: extrude + color the water surface so depth reads in 3D, instead of
  a flat raster overlay.
- The quadtree mesh (#156): render 100k+ cells as GPU polygons/wireframe smoothly, instead
  of fully materialized MapLibre GeoJSON that lags.
- Time animation (scrubber): one layer with a time attribute -> smooth interpolated
  playback instead of N-layer visibility flips.
- Buildings: 3D extrusions from the footprints we already fetch.
- Large vector: deck `MVTLayer` for the dense path the dead pmtiles branch was reaching for.
- Synergy with the data-island (#165): its per-Case persisted vector GeoJSON is deck.gl's
  native input — #165 and deck.gl reinforce each other, and (per the persistence add-on to
  #168) the same read-only cold-serve path feeds the overlay with the box off.

## 6. Trade-offs and risks (honest)

- Not all-or-nothing: raster stays on TiTiler (right tool for big rasters). deck.gl earns
  its keep on vector / mesh / 3D / animation. We run MapLibre raster + basemap underneath a
  deck overlay on top.
- Medium investment: adds a rendering layer + migrates the vector path off the current
  many-MapLibre-layers-per-logical-layer model. Short-term complexity up; long-term it
  collapses three ad-hoc mechanisms into one layer model (the simplification).
- Mobile/perf is a live risk (NATE tests on mobile): deck.gl is GPU-heavy and we currently
  do NO device gating. A deck adoption must add DPR cap + layer-count/feature gating +
  reduced-motion in the animation path (none exist today).
- The 2D pitch lock (`Map.tsx:2278-2281`) must be deliberately relaxed for any 3D; that
  interacts with terra-draw and our gesture handling — verify draw + pick still behave.
- Interleaved vs overlaid mode is a real choice: overlaid is simpler (deck draws over
  MapLibre) but can't depth-sort deck 3D against MapLibre layers; interleaved is correct for
  mixed 3D but more finicky. Decide per use case.

## 7. Phased adoption (each phase flag-gated, independently shippable)

- Phase 0 (separable, cheap, no deck.gl): 3D mode on MapLibre — setTerrain from a DEM COG
  terrain-RGB (TiTiler), fill-extrusion buildings, relax pitch lock behind a "3D" toggle.
  Proves the 3D appetite and gesture interactions.
- Phase 1 (spike, de-risk): add `MapboxOverlay` behind a flag; render ONE real Case's
  quadtree mesh + a 3D flood surface via deck; measure perf on desktop AND mobile. Go/no-go
  gate for the rest.
- Phase 2: migrate the vector + mesh path to a deck `GeoJsonLayer`/`MVTLayer`, reusing
  `vector_rendering.ts` accessors; keep raster + basemap MapLibre-native underneath.
- Phase 3: move animation to a deck time-attribute layer; repoint the controller emitter.
- Phase 4: 3D data layers (extruded depth, point clouds, mesh wireframe) + decide
  interleaved mode where depth-sorting matters.

## 8. Open decisions to resolve when the engine runs land

1. Overlaid vs interleaved MapboxOverlay (depends on how much true 3D depth-sorting we need).
2. Which layers migrate first (recommend mesh + 3D flood, the highest-pain items).
3. Keep raster on TiTiler (recommended) vs unify as deck `TileLayer`.
4. Mobile gating policy (DPR cap, max features, reduced-motion) — required before shipping.
5. Whether to relax the 2D pitch lock globally or only in a "3D mode" toggle.
6. Bundle-size budget (deck.gl + the layer modules we use) vs current bundle.

## 9. Acceptance for committing (when we decide)

- Phase-1 spike renders a real Case's mesh + 3D flood at acceptable FPS on a mid mobile
  device, behind a flag, with draw/pick/scrubber still working.
- The control wiring (opacity/visibility/order) and per-Case durability behave identically
  through the deck path (the bus + `layer_cache.ts` contracts hold).
- Raster overlays and the basemap are unaffected (deck sits on top).
- Cold-view (box-off) still paints (deck consumes #165 persisted GeoJSON read-only).

## 10. Related work

- #156 computational mesh (the first dense-vector pain point deck.gl targets).
- #165 data-island self-serving / persisted vector GeoJSON (deck.gl's native input).
- #168 sub-step visibility persistence (same read-only cold-serve rails).
- project_3d_terrain_viz (the MapLibre-native Phase 0 3D).
- project_timeseries_animation_and_overlay_layout (the animation Phase 3 targets).
