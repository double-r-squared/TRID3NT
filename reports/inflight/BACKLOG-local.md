# TRID3NT Local - deferred backlog (do when we don't have much else running)

Slow-day / parallel-when-idle items NATE has explicitly deferred. Not bugs (those live in BUGLIST-local.md), not the active build. Pull from here when a lane is free.

| id | item | why deferred | detail |
|----|------|--------------|--------|
| BK-1 | Base maps in Settings + fresh-slate startup (QGIS plugin) | NATE 2026-07-14: "do TELEMAC first, this can come later when we don't have much to do" | TWO linked UX changes, match the cloud. (a) FRESH SLATE ON STARTUP: stop auto-opening the last case on connect (that auto-open is what leaked the Chattanooga AOI, F46); boot to just the map / no case, and the FIRST chat message auto-creates a case (cloud behavior). (b) BASE-MAP SELECTOR in Settings (apply-on-Save, item-4 pattern like the AOI/thinking toggles): dropdown of XYZ presets - ESRI World Imagery (satellite), CartoDB Dark Matter (dark), OSM (current default) - plus an optional STATE-LINES overlay (TIGER). Self-contained via QGIS native XYZ tiles (our dock already adds OSM that way in ensure_basemap) - NO external plugin dependency needed (evaluated QuickMapServices; XYZ presets are simpler + offline-friendlier; re-confirm QMS isn't strictly better when starting). After it works, re-run the groundwater/dye demo for reproducibility on the improved base maps. |

## Accumulated observations to fold in when the relevant lane runs (from live drives, not yet scheduled)
- MODFLOW/tracer concentration is unphysical + model-dependent (~16-31 g/L; swings 1000x with the model-chosen release_rate 0.1 vs 100 kg/s). Needs a sanity clamp/normalization. [groundwater realism lane]
- MODFLOW transport (run_modflow_job) rebuilds its own flow field instead of reusing a prior pumping-well flow field in the same case - wasteful double-solve + plume doesn't feel the well's capture (part of why the GW plume renders as a straight pencil). [groundwater realism lane]
- No-follow-up-offers system line (F41) still leaks on SOME turns (e.g. a T1 "Would you like to proceed with publishing...") - not fully effective; harden. [agent prompt lane]
- MODFLOW/GWT plume time series exists in modflow_mesh.nc (366 steps) but is only surfaced as a single peak raster on the map - not played as an animation. [viz lane]
