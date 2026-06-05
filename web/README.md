# web/ — React + MapLibre web client

**Owner:** `web` specialist.

The browser application (SRS v0.3 Decision A, FR-WC-*). A React single-page app
with a MapLibre GL JS map, chat panel, layer panel, time scrubber, identify
popover, pipeline strip + cancel UI, and the spatial-input / disambiguation
pick-modes. It talks to the agent service over the Appendix-A WebSocket protocol
and renders Tier B map data exclusively through QGIS Server (WMS/WMTS/WFS) or
agent-served GeoJSON — never by reading GCS directly (Invariant 5).

Tier A basemap is a swappable public provider (OSM direct in v0.1; documented
MapTiler / Protomaps swap path — FR-DT-1, FR-DT-5).

Empty scaffold until `job-0016` lands the CONUS map + chat round-trip stub.
