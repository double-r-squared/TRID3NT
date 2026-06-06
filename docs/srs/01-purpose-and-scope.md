## 1. Purpose and Scope

### 1.1 Purpose

A web-based natural-language interface for environmental hazard analysis. Users describe a hazard scenario in plain language — including by reference to real-world news events — and the system responds in one of two modes:

- **Modeling**: select appropriate data sources and solvers, build and execute physics-based simulations, and visualize the results
- **Discovery**: locate existing authoritative public hazard layers (USFS wildfire hazard potential, FEMA flood zones, USGS seismic hazard, etc.) and display them on the map

The agent chooses the mode based on the user's intent — modeling for hypothetical or specific event scenarios that demand simulation; discovery for "show me what's known about hazard X in region Y" queries that already have authoritative answers. In both modes, the AI is the primary interface; users do not need to configure GIS software, find data, or run solvers manually.

### 1.2 Scope (v0.1 MVP)

**In scope:**
- Web client built on MapLibre GL JS, served from a browser
- Agent built on Google Cloud Agent Builder using the Agent Development Kit (ADK)
- Gemini 3 as the LLM
- MongoDB Atlas as the data layer (news corpus, extracted events, run catalog, vector search)
- MongoDB MCP server integration as the agent's database access path
- QGIS Server as the map rendering backend
- PyQGIS workers (Cloud Run Jobs) for project file manipulation and processing operations
- One hazard fully supported end-to-end via modeling: **flood** (storm surge, pluvial, fluvial)
- **Public hazard layer discovery**: a curated catalog of authoritative published hazard maps (wildfire hazard potential, FEMA flood zones, USGS seismic hazard, USDA risk indices, etc.) that the agent can locate and display without running a solver
- News retrieval and event extraction → forcing data → model run → map
- **Hazard event sourcing from authoritative agency feeds** (NWS, NHC, USGS NWIS) in addition to news media; multi-source claim aggregation with provenance recorded per claim
- **Research mode and deep research mode** as a user-toggled capability: research mode (default) performs one focused sweep of vetted sources; deep research mode (architecture in place, full implementation deferred to v0.2+) broadens source pool and adds convergence logic
- Two-layer tool architecture: deterministic workflows backed by atomic tools, with the LLM directly selecting the appropriate workflow per turn
- 2D visualization only — pan, zoom, layer toggle, opacity, time series scrubbing
- Cloud-native execution on Google Cloud (Cloud Run, Workflows, GCS)

**Out of scope (deferred to v0.2+):**
- 3D visualization (pitch/bearing, terrain, extrusions) — see §5
- Additional hazard *modeling* (groundwater via MODFLOW, wildfire spread, seismic shaking, oil/contaminant spill). Note that wildfire, seismic, and flood *discovery* (showing existing authoritative public layers) ships in v0.1; only the modeling-side workflows for these hazards are deferred.
- **Deep research mode** for hazard event sourcing — architecture and schema (claim sets) ship in v0.1, but the broader source pool, full convergence logic, and additional agency integrations (NIFC, USGS earthquakes, state DOT/DEM, hyperlocal news, headless web fetching) are deferred to v0.2+
- **Social-tier event sourcing** — Twitter / Facebook / Nextdoor as signal sources for hazard events; deferred to v0.2+ alongside deep research mode
- Custom drawing or annotation by users
- Multi-hazard chaining and cascade modeling
- Mobile-optimized UX (works on mobile but not specially optimized)
- Multi-user collaboration on a single session
- Native desktop or QGIS Desktop plugin distribution

### 1.3 Definitions

| Term | Definition |
|---|---|
| **Agent** | The Gemini-powered orchestration service that plans tool calls and produces narrative output |
| **ADK** | Google's Agent Development Kit — the Python framework for building Gemini-powered agents |
| **Engine** | A hazard-specific module that wraps one or more physics solvers, exposing a stable interface |
| **Solver** | An external physics-based simulator (SFINCS, etc.) that produces georeferenced output |
| **Tool** | A typed function exposed to the LLM, with JSON schema and docstring metadata, registered via ADK's `FunctionTool` or as an MCP tool |
| **Workflow** | A deterministic Python function orchestrating atomic tools to fulfill a hazard query pattern; the LLM invokes workflows as tools |
| **Atomic tool** | A single-purpose tool (data fetch, processing operation, model setup step, etc.) callable directly by the LLM or composed inside workflows |
| **Forcing** | Boundary conditions and inputs that drive a physics simulation |
| **COG** | Cloud Optimized GeoTIFF — the standard raster output format |
| **MCP** | Model Context Protocol — the open standard for connecting LLM agents to external tools and data sources |
| **WMS / WMTS / WFS** | OGC standards for web map services (rendered tiles, tile-cached, feature data) |
| **`.qgs` project** | A QGIS project file defining layers, styles, extent, CRS, and temporal configuration |

---

