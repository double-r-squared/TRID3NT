# styles/ — QML style presets

**Owner (content):** `engine` specialist. **Baked into the QGIS Server image:**
`infra`.

QGIS QML style presets (SRS v0.3 FR-QS-5) — e.g. the flood-depth ramp — applied
to `.qgs` project layers by PyQGIS worker jobs and rendered by QGIS Server.

The preset *content* (ramps, classes, labeling) is authored by `engine`; `infra`
bakes these files into the `qgis/qgis-server` container image so QGIS Server can
serve styled WMS/WMTS. The web client never styles map data itself (Invariant 4).

Empty scaffold until `engine` authors the v0.1 presets.
