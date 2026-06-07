# styles/ — QML style presets

**Owner (content):** `engine` specialist. **Baked into the QGIS Server image:**
`infra`.

QGIS QML style presets (SRS v0.3 FR-QS-5) — e.g. the flood-depth ramp — applied
to `.qgs` project layers by PyQGIS worker jobs and rendered by QGIS Server.

The preset *content* (ramps, classes, labeling) is authored by `engine`; `infra`
bakes these files into the `qgis/qgis-server` container image so QGIS Server can
serve styled WMS/WMTS. The web client never styles map data itself (Invariant 4).

**Preset inventory (v0.1):**
- `basemap.qml` — M2 basemap stub (multibandcolor OSM raster, opacity tweak). Proves the apply_style_preset codepath.
- `continuous_flood_depth.qml` — singleBandPseudoColor Blues ramp 0–3.5 m for flood-depth COG layers (job-0062). Applied by the PyQGIS worker `_append_raster_layer` to SFINCS hmax output. Nodata transparent; 0 m entry alpha=0 so dry cells are invisible.

The six remaining FR-QS-5 presets (flood velocity, flood arrival time, continuous DEM, categorical landcover, hurricane track, affected buildings) are deferred to their respective milestone sprints.
