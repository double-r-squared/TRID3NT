# Software Requirements Specification

## Hazard Modeling Agent — A Web-Based AI Workbench for Multi-Hazard Modeling

**Version:** 0.3.22
**Status:** Draft
**Authors:** Nathaniel J Almanza
**Last updated:** 2026-06-08
**Supersedes:** v0.2 (2026-06-04)

---

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

## 2. System Overview

### 2.1 Architectural decisions

**Decision A: Web frontend, not QGIS Desktop plugin**
v0.1 ships as a web application. The AI is the abstraction over plugin management, layer styling, and processing toolbox operations — features that would otherwise require QGIS Desktop UI. Users do not need to install software.

**Decision B: QGIS Server as the rendering backend**
QGIS Server (Cloud Run) renders the same `.qgs` projects and QML styles that QGIS Desktop would, but serves them as OGC web services (WMS/WMTS/WFS) to the web client. This preserves the QGIS rendering engine and ecosystem without requiring users to interact with QGIS Desktop's UI.

**Decision C: PyQGIS workers for project manipulation**
Project file mutations (adding layers, changing styles, configuring temporal properties) are performed by short-lived PyQGIS worker jobs (Cloud Run Jobs). The agent invokes these as tools; they read the `.qgs` from GCS, mutate it, write back.

**Decision D: GeoAgent (opengeos) is a reference, not a dependency**
Patterns and ideas from the opengeos/GeoAgent project — tool registration discipline, docstring conventions, confirmation hook approach — inform the design. No code is copied or vendored. ADK is a different framework and its patterns govern.

**Decision E: Google Cloud throughout**
All infrastructure runs on Google Cloud: Agent Builder/ADK for the agent, Cloud Run for QGIS Server and worker jobs, Cloud Workflows for multi-step orchestration, GCS for artifacts. MongoDB Atlas runs as a managed service alongside (multi-cloud-deployable, in practice deployed in a GCP region adjacent to the agent).

**Decision F: MongoDB Atlas as the durable knowledge layer**
News articles, extracted event metadata, model run catalog, and embeddings for semantic search all live in MongoDB Atlas. Atlas Vector Search enables "find historical events similar to this one" as an agent capability. The agent accesses MongoDB via MongoDB's MCP server.

**Decision G: Two-layer tool architecture**
The agent's tool inventory is organized into two layers: deterministic workflows (named functions that implement common end-to-end patterns) and atomic tools (single-purpose operations). The LLM invokes workflows directly when a user request matches a known pattern, or composes atomic tools for novel requests. Intent classification is not a separate phase; it is implicit in the LLM's choice of which workflow or tool to call. See §3.3.

**Decision H: Determinism boundary**
The LLM plans and narrates. It does not produce numerical model output. Numbers in user-facing summaries come from structured tool results, not LLM generation.

**Decision I: 2D visualization only in v0.1**
Pan, zoom, layer management, opacity, and time-series scrubbing are implemented. 3D capabilities (pitch/bearing, terrain, building extrusions) are deferred.

**Decision J: Engine selection follows tractability of integration**
Engines wrap solvers using either plugin-backed (existing QGIS plugins or Processing algorithms) or Python-shim (direct wrapper around a solver's Python API) integration modes. Engine selection for each version prioritizes solvers where one mode or the other is genuinely easy to integrate. Solvers requiring substantial new tooling are deferred — not architecturally excluded, but staged behind easier wins. The agent itself is intended to grow this catalog over time via solver-feasibility research (see FR-AS-9). See §2.3 for the current catalog and integration modes.

**Decision K: User supplies intent and irreducible inputs; the agent fetches, derives, or defaults everything else**
The user's role is to express what they want and provide inputs that no data source can supply — typically a location (pin or bbox), a time window (sometimes implicit), and the choice of which path to take when genuinely ambiguous. Wind fields, weather, fuels, topography, river bathymetry, Manning's coefficients, return-period precipitation, and so on are not user inputs; the agent fetches them from authoritative public sources or defaults them sensibly. Workflows shall be designed so that the parameter surface exposed to the user is minimal; everything else is the workflow's job to resolve. The system supports user override of defaults (e.g., "what if winds were 50% stronger?") but never forces the user to think about parameters they shouldn't need to know exist. See FR-AS-12.

**Decision L: Vector embedding model**
Google's `text-embedding-005` is the standard embedding model across the system. Used for event-similarity search, article search, and run-similarity search. Default dimension is 768; smaller dimensions (256, 128) may be used for specific indexes if a recall-vs-cost trade-off justifies it. Embedding model is captured per-document in an `embedding_model` field so future model swaps are tractable without re-embedding the full corpus.

**Decision M: Authoritative agency feeds are first-class sources alongside news; multi-source claim aggregation with provenance is the default extraction mode**
For hazard event sourcing, government agency feeds (NWS, USGS, NHC, etc.) are first-class sources alongside news media. Every numerical claim about an event is captured per-source in a `NumericClaim` object and grouped into a `ClaimSet` with a computed consensus value, aggregation method, and confidence level. Two operational modes — research (v0.1 default) and deep research (v0.2+) — share the same schema and architecture, differing only in source breadth and aggregation depth. See §3.4 (FR-HEP), Appendix C (`ClaimSet`, `NumericClaim`), and FR-WC-15 (user-facing toggle).

**Decision N: Impact post-processing is a separate tool class from engines** *(Forward-looking — not in M1 / not in sprint-03; first member targeted post-M5)*
Engines wrap solvers and emit an `AssessmentEnvelope` (hazard footprint). Impact post-processors are a distinct tool class that consume an `AssessmentEnvelope` and emit an `ImpactEnvelope` (building-level damage, loss, casualty estimates). The first member is Pelicun (NHERI SimCenter, FEMA P-58 / HAZUS fragility-based assessment), introduced as a forward-looking capability targeted after M5. Keeping engines and post-processors as separate classes means impact-modeling tractability is judged independently of engine-integration tractability, and new post-processors can be added without touching engine code. The symmetric guarantee from §2.3 applies: new post-processing tools added in future versions do not require changes to the agent core, only registration of their workflows and atomic tools. See §2.3 (post-processing tool classes), Appendix B (`ImpactEnvelope`), FR-CE-5 through FR-CE-7, and Milestone M5.5.

**Decision O: Cache-mediated atomic-tool data fetching with a four-class TTL taxonomy** *(Forward-looking — not in M1 / not in sprint-03; binding from M4 when the first data-fetching atomic tools register.)*
Every atomic tool that hits an external public API (USGS 3DEP / NWIS / earthquake catalog, NLCD, NHDPlus HR, NOAA Atlas 14, NHC ATCF, NOAA CO-OPS, Microsoft Building Footprints, OSM Overpass, NewsAPI, GDELT, api.weather.gov, NOAA Storm Events DB, USFS WHP, FEMA NFHL, etc.) wraps a single shared cache shim before its network call. Cached artifacts live in a dedicated GCS prefix (`gs://<bucket>/cache/<source-class>/<hash>.<ext>` — same convention as Appendix B's worked DEM / footprints examples). Cache keys are content-addressed: derived from `source_id` + canonicalized query parameters + the TTL-bucket vintage, so two callers asking for the same input deduplicate without coordination. Each tool registers exactly one of four TTL classes at tool-definition time: `static-30d` (terrain, year-stamped landcover, NHDPlus reach geometry, NOAA Atlas 14 curves, building footprint snapshots), `semi-static-7d` (post-season ATCF best-track, USGS quake catalog historical, FEMA NFHL periodic releases), `dynamic-1h` (active NHC advisories, NWIS recent windows, CO-OPS tide gauges, news search, GDELT queries, NIFC active incidents), or `live-no-cache` (read-through with immediate expiry; reserved for tools whose contract demands "right now" freshness — uncacheable by construction also covers interactive tools like `request_spatial_input`, MongoDB MCP writes, and WebSocket emitters). Per-call overrides are allowed when response metadata changes the classification (e.g. an active NHC storm shifts the same ATCF source from `static-30d` to `dynamic-1h`). The shim enforces read-through / write-on-miss / lifecycle eviction at the bucket level; agent-facing tools never see cache semantics. See §3.9 for the full architecture, OQ-5 (now closed by Decision O — see §3.9 / FR-CE-8), and FR-CE-1 / FR-CE-4 for related storage conventions.

### 2.2 Component diagram

```
┌──────────────────────────────────────────────────────────────────┐
│                      Web Browser                                 │
│  ┌────────────────────┐   ┌─────────────────────────────────┐    │
│  │  Chat panel        │   │   Map (MapLibre GL JS)          │    │
│  │  - message stream  │   │   - WMS/WMTS tiles              │    │
│  │  - tool calls      │   │   - vector overlays (GeoJSON)   │    │
│  │  - pipeline strip  │   │   - layer toggle list           │    │
│  │  - cancel button   │   │   - opacity sliders             │    │
│  │                    │   │   - time scrubber               │    │
│  └─────────┬──────────┘   └──────────────┬──────────────────┘    │
│            │                             │                       │
└────────────┼─────────────────────────────┼───────────────────────┘
             │ WebSocket                   │ HTTPS (WMS/WFS)
             ▼                             ▼
┌─────────────────────────────────┐  ┌──────────────────────────┐
│  Agent Service                  │  │   QGIS Server            │
│  (Cloud Run, Agent Builder/ADK) │  │   (Cloud Run)            │
│  ┌───────────────────────────┐  │  │   - Renders .qgs proj    │
│  │ Gemini 3                  │  │  │   - Serves WMS/WMTS/WFS  │
│  └───────────────────────────┘  │  │   - QML styles           │
│  ┌───────────────────────────┐  │  │   - WMS-T temporal       │
│  │ Tool registry             │  │  └─────────────┬────────────┘
│  │  - native ADK tools       │  │                │
│  │  - MongoDB MCP tools      │  │                │ reads
│  │  - hazard modeling tools  │  │                ▼
│  └──────┬────────────────────┘  │       ┌──────────────┐
│         │                       │       │  GCS Bucket  │
│         │                       │       │  - .qgs proj │
│         │                       │       │  - COGs      │
│         │                       │       │  - FlatGeobuf│
│         │                       │       │  - QML styles│
└─────────┼───────────────────────┘       └──────┬───────┘
          │                                      ▲
          │ invokes (via Cloud Workflows)        │ writes
          ▼                                      │
┌─────────────────────────────────────┐          │
│   Worker Pool (Cloud Run Jobs)      │──────────┘
│   - PyQGIS workers                  │
│     (project mutation, qgis_process)│
│   - SFINCS solver containers        │
│   - News fetchers, event extractors │
└─────────────────────────────────────┘
          │
          ▼
   ┌──────────────────┐
   │  MongoDB Atlas   │
   │  - News corpus   │
   │  - Events        │
   │  - Run catalog   │
   │  - Embeddings    │
   │  - Vector search │
   │  ── MCP server ──┼─── Agent connects here for DB tools
   └──────────────────┘
```

### 2.3 Engine catalog

> **Status: tentative.** The catalog below reflects current judgment about which solvers are tractable for which versions. Both the engine list and the integration-mode designations are subject to revision as the team learns more about specific libraries, plugin maturity, and integration cost. The framing (tractability-driven selection, plugin-backed and shim-backed as parallel modes) is the stable part.

**Engine selection principle.** An engine wraps a solver that produces georeferenced output (raster or vector geometries). The wrapping uses one of two integration modes:

- **Plugin-backed** — invoked through an existing QGIS plugin or Processing algorithm. Setup, execution, or post-processing leverages pre-built tooling.
- **Python shim** — wraps a solver's Python API (or a CLI) directly. No QGIS plugin exists or is needed; a minimal in-project wrapper handles the integration.

Some engines may use a hybrid — for example, a QGIS plugin for setup but a Python shim for execution. The user-facing experience is identical regardless of mode; only the implementation differs.

Engines are selected for each version based on **tractability**: a solver makes the cut when either path (plugin-backed or shim) is genuinely easy to integrate. Solvers that would require building new QGIS plugins, custom 3D-to-2D projection pipelines, full CFD orchestration, or other substantial tooling work are deferred — not because they're architecturally excluded, but because easier wins ship first.

**v0.1 catalog:**

| Engine | Solver | Integration mode | Notes |
|---|---|---|---|
| **Flood (2D depth-averaged)** | SFINCS | Python shim via HydroMT | Mature, COG output, well-documented |

**Deferred engines** (catalogued by current best estimate of integration mode; subject to research):

| Engine | Solver | Likely mode | Target |
|---|---|---|---|
| Groundwater flooding | MODFLOW-6 + FloPy | Python shim | v0.2 |
| Wildfire spread | Cell2Fire or GisFIRE-SpreadSimulation | Hybrid: plugin for setup, shim for execution | v0.2 |
| Seismic shaking | OpenQuake | Python shim | v0.3 |
| Surface contaminant transport | OpenDrift on flood velocity field | Python shim | v0.3 |
| Tsunami inundation | GeoClaw or ANUGA | Python shim | TBD — pending use case |
| Air dispersion | HYSPLIT | Python shim | TBD |
| Landslide susceptibility | r.slope.stability (GRASS) | Plugin-backed via QGIS Processing | TBD |
| TELEMAC-MASCARET suite *(Forward-looking — not in M1 / not in sprint-03)* | openTELEMAC-MASCARET (TELEMAC-2D/3D, TOMAWAC, ARTEMIS, GAIA, MASCARET) | Python shim | v0.3 |
| Distributed hydrology *(Forward-looking — not in M1)* | pywatershed (USGS reimplementation of PRMS) | Python shim | v0.2 |
| Integrated surface–subsurface hydrology *(Forward-looking — not in M1)* | ParFlow (3D variably-saturated Richards' equation) | Python shim | v0.3 |
| Urban drainage *(Forward-looking — not in M1)* | EPA SWMM via PySWMM | Python shim | v0.2 |
| Lumped/semi-distributed rainfall-runoff *(Forward-looking — not in M1)* | HEC-HMS (USACE) | Python shim via QGIS-HMS / hechmsio | v0.2 |
| Coupled atmosphere–fire *(Forward-looking — not in M1)* | OpenWFM / WRF-SFIRE via wrfxpy | Python shim (out-of-process; HPC orchestration) | experimental / v0.3 |
| Wildfire spread + fuels *(Forward-looking — not in M1)* | QUIC-Fire + FastFuels + pyretechnics + duet-tools (fuels-to-fire stack) | Python shim | v0.3 |
| ML-accelerated wildfire *(Forward-looking — not in M1)* | PyTorchFire (differentiable cellular automaton on GPU) | Python shim | experimental |
| WRF diagnostics *(Forward-looking — not in M1)* | wrf-python (NCAR) | Python shim — diagnostic/interpolation library consumed by atmospheric workflows | v0.2 |

Engines requiring substantial new tooling — full 3D CFD flooding (OpenFOAM-class), coupled fire-atmosphere (WRF-Fire), 3D ocean simulation, multiphase debris flow CFD — are not on the current roadmap. The architecture supports them in principle but the integration cost is currently disproportionate to other wins available. The agent may later evaluate these for inclusion (see FR-AS-9). For clarity: mesh-based shallow-water suites with mature Python driver tooling and out-of-process invocation (TELEMAC-MASCARET being the concrete example — see the Deferred engines row above, target v0.3) are roadmap-included and distinct from this OpenFOAM-class set; '3D ocean simulation' here refers to full 3D Navier–Stokes coastal CFD with custom orchestration, not TELEMAC-3D's RANS-averaged shallow-water 3D mode.

**Post-processing tool classes** *(Forward-looking — not in M1 / not in sprint-03; first member targeted post-M5.)* Engines emit `AssessmentEnvelope`; post-processing tool classes consume one and emit a different envelope. They are catalogued separately so the engine list above remains engines-only and post-processing capability is enumerated explicitly rather than smuggled into the engine catalog. Post-processors do not produce georeferenced solver output; they emit an `ImpactEnvelope` with building-level damage, loss, downtime, and casualty summaries (see Appendix B for the schema).

| Tool class | Tool | Integration mode | Target | Notes |
|---|---|---|---|---|
| Impact (fragility-based damage/loss) | Pelicun (NHERI SimCenter) | Python shim | post-M5 (see M5.5) | FEMA P-58 / HAZUS fragility libraries via the bundled Damage and Loss Model Library; consumes an `AssessmentEnvelope`, emits an `ImpactEnvelope`; BSD 3-Clause |

Future post-processing classes (regional-resilience indices, business-interruption models, network-cascading impact tools) follow the same contract: consume an envelope, emit an envelope, register a workflow.

**Common contract.** All engines share `(location, forcing) → AssessmentEnvelope` (see Appendix B for the envelope schema). New engines added in future versions do not require changes to the agent core, only registration of their workflows and atomic tools.

**Post-processing tool-class contract.** *(Forward-looking — not in M1 / not in sprint-03; first member targeted post-M5.)* In parallel with engines, the architecture admits a second tool class — **impact post-processors** — that share the contract `AssessmentEnvelope → ImpactEnvelope` (see Appendix B for both envelope schemas). A post-processor consumes a previously produced assessment envelope and emits a sibling envelope carrying building-level damage states, loss, downtime, and casualty estimates. Post-processors do not produce georeferenced solver output; they are composable downstream of any modeled or discovered envelope. Pelicun is the first member (see Decision N). Adding a new post-processor does not require changes to engine code, and engines remain unaware of which post-processors (if any) consume their output. The symmetric guarantee applies: new post-processing tools added in future versions do not require changes to the agent core, only registration of their workflows and atomic tools. The data-dependency invariant (Pelicun is never invoked before a simulation result exists) is enforced at the orchestration layer by FR-CE-6; the agent invokes the impact-post-processing workflow (`run_pelicun_impact`, defined in FR-TA-1) only when a real `AssessmentEnvelope` is available — either freshly produced or fetched from MongoDB.

---

## 3. Functional Requirements

### 3.1 Web Client (FR-WC)

**FR-WC-1: Browser support**
The web client shall run in current versions of Chrome, Firefox, Safari, and Edge. No installation required.

**FR-WC-2: Map rendering**
The map shall be rendered using MapLibre GL JS, displaying:
- Tier A base map (see §3.6 FR-DT-1) loaded on page load
- Tier A hillshade overlay (off by default, agent-enableable)
- Tier B WMS raster tiles served by QGIS Server (results only — loaded on demand)
- Tier B WMS-T temporal raster tiles when temporal data is loaded
- Tier B vector overlays as GeoJSON for lightweight features like affected buildings or hurricane tracks (loaded directly from agent, not through QGIS Server)

**FR-WC-3: 2D navigation**
Pan and zoom shall be supported via mouse, scroll wheel, and touch gestures. Rotation, pitch, and bearing are disabled in v0.1 (the camera is locked to a top-down 2D view).

**FR-WC-4: Layer panel**
A panel beside the map shall display the current layer list, ordered by Z-index. Per layer:
- Visibility toggle (checkbox)
- Opacity slider (0–100%)
- Layer name and source attribution
Layer reordering via drag-and-drop is in scope for v0.1.

**FR-WC-5: Time scrubber**
For loaded layers with temporal configuration, a time scrubber component shall be displayed:
- Horizontal slider with the temporal range
- Current timestamp readout
- Play / pause / step controls
- Playback speed selector (0.5x, 1x, 2x, 5x, 10x)
- Automatic show/hide based on whether any loaded layer is temporal
The scrubber issues WMS-T requests by updating the `TIME` parameter on tile URLs.

**FR-WC-6: Identify popover**
Clicking a feature or raster pixel shall display a popover with the value(s). Raster identification uses QGIS Server's WMS `GetFeatureInfo` endpoint. Vector identification reads attributes directly from the loaded GeoJSON.

**FR-WC-7: Chat panel**
The chat panel shall provide:
- Markdown-rendered message stream (user, agent, tool results)
- Multi-line text input with Cmd/Ctrl+Enter submit
- Streaming response display (token-by-token from Gemini)
- Collapsible blocks for tool calls (showing tool name, parameters, results)
- Visual styling that integrates with the map (side panel, collapsible)

**FR-WC-8: Pipeline strip**
A status strip in the chat panel (or above the map, configurable) shall display the current pipeline as ordered steps with states (pending, running, complete, failed, cancelled). Each step shows:
- Step name (workflow or tool name)
- Progress indicator (spinner or percentage when reported)
- Click-to-expand for full logs and tool output

**FR-WC-9: Job cancellation**
The user shall be able to cancel a running pipeline via a cancel button on the pipeline strip. Cancellation shall:
- Send a cancel signal to the agent over WebSocket
- The agent shall interrupt LLM generation
- The agent shall call `terminate` on in-flight Cloud Workflows executions
- Mark current and pending steps as cancelled
- Leave already-loaded layers in place

**FR-WC-10: Session persistence**
Sessions shall persist via:
- Server-side: session record in MongoDB tied to a session ID
- Client-side: session ID in the URL or local storage
Reloading the browser shall restore the session: chat history, loaded layers, pipeline history.

**FR-WC-11: Shareable session links**
A shareable URL shall allow another user to view a read-only snapshot of a session, including the current map state and chat history.

**FR-WC-12: Location auto-snap**
On receipt of a `location-resolved` message (see Appendix A), the client shall smoothly animate the map view to the resolved bbox, applying padding rules per the `granularity` field:
- `country`: bbox + ~10% padding
- `region`, `state`: bbox sized to fill approximately 60% of the viewport
- `city`: bbox + ~20% padding
- `facility`: bbox of approximately 5 km radius around the point
- `bbox`: bbox + ~10% padding

The animation shall use MapLibre's `flyTo({essential: true, duration: 1500})` and respect the user's `prefers-reduced-motion` setting (instant jump when reduced motion is preferred).

A subtle, dismissible "Showing: {label}" overlay shall display the current resolved location label.

The client shall not re-snap to the same `resolved_id` within a 30-second window. If the user has manually panned, zoomed, or otherwise navigated since the last snap, the client shall suppress subsequent snaps to the same `resolved_id` (the user's manual navigation takes precedence). New `resolved_id` values from genuinely new locations always snap.

**FR-WC-13: Spatial input pick-mode**
On receipt of a `spatial-input-request` message, the client shall:
1. Animate to `suggested_view.bbox` if provided
2. Load `reference_layers` as temporary overlays (visible only during this request)
3. Switch the cursor to pick-mode (crosshair for `point`, marquee for `bbox`)
4. Display a banner over the map with the request `title` and `description`
5. Provide a "Cancel" affordance that sends a cancellation response

On user pick (click for point, drag for bbox), send a `spatial-input-response` with the chosen geometry. After response or cancellation, remove the temporary reference layers, restore the normal cursor, and dismiss the banner.

**FR-WC-14: Disambiguation pick-mode**
On receipt of a `disambiguation-request` message, the client shall:
1. Animate to a viewport that encompasses all candidate bboxes
2. Render each candidate as a numbered marker on the map at its bbox center
3. Display the candidates as a selectable list in the chat panel
4. Allow selection by clicking either a marker or a list entry
5. Provide a "Cancel" affordance

Selection sends a `disambiguation-response` with the chosen `candidate_id`. After response or cancellation, remove the candidate markers.

**FR-WC-15: Research mode toggle**
A toggle in the chat panel allows the user to select between "Research" (default) and "Deep Research" for hazard event queries. The toggle controls the hazard event pipeline's source breadth and aggregation depth (see FR-HEP-3 vs FR-HEP-4). The toggle state shall be persisted per session.

In v0.1, selecting "Deep Research" displays a tooltip indicating the feature is forthcoming and the pipeline proceeds in research mode. The toggle is visible and selectable but visually marked (subtle indicator, e.g., "Coming soon" subtext) to signal the feature is documented but not yet operational. This surfaces the capability while being honest about v0.1 scope.

### 3.2 Agent Service (FR-AS)

**FR-AS-1: Framework and model**
The agent shall be built on Google Cloud Agent Builder using the Agent Development Kit (ADK). The model shall be Gemini 3. No multi-provider abstraction is required in v0.1.

**FR-AS-2: Deployment**
The agent shall be deployed via Agent Engine to Cloud Run, exposing a WebSocket endpoint for clients. The deployment unit is a containerized Python application.

**FR-AS-3: Tool registry**
Tools shall be registered using ADK's `FunctionTool` for native Python tools and ADK's MCP client for MCP-served tools. Each tool's docstring shall include:
- One-sentence summary
- "Use this when:" bullet list of trigger conditions
- "Do NOT use this for:" bullet list of incorrect uses
- Parameter and return-type descriptions

This metadata is what Gemini uses to select tools.

**FR-AS-4: MongoDB MCP integration**
The agent shall connect to MongoDB's MCP server for database access. The MCP server provides tools for:
- Document queries (find news, events, runs by various filters)
- Vector search (semantic search over event embeddings)
- Insert operations (record new runs, store fetched articles)
- Aggregation pipelines (analytics over the run catalog)

The MongoDB MCP server is consumed as-is in v0.1; custom domain-specific MCP tools may be added in later versions if needed.

**FR-AS-5: WebSocket protocol**
The agent shall communicate with the web client over a single WebSocket connection using a discriminated message envelope. The complete protocol specification — envelope structure, message types, payload schemas, streaming semantics, and confirmation flow — is defined in **Appendix A: WebSocket Protocol**.

**FR-AS-6: Cancellation propagation**
A `cancel` message shall interrupt the LLM generation, send termination signals to in-flight Cloud Workflows executions, and return a `pipeline_state` reflecting cancellation. Cancellation shall complete within 30 seconds.

**FR-AS-7: Determinism boundary**
The agent shall never write numerical model output to its narrative response. Numbers in user-facing summaries (depths, areas, counts, durations) shall be sourced from the structured `AssessmentEnvelope` (see Appendix B) and tool result schemas. Tool result schemas shall include the typed metrics fields necessary for narrative generation. **(Forward-looking extension — applies once impact post-processing ships, post-M5.)** Damage-state counts, expected loss ratios, repair-cost statistics, downtime estimates, collapse and unsafe-placard probabilities, and casualty estimates shall be sourced from the structured `ImpactEnvelope` (see Appendix B.6c) and never generated by the LLM. The same rule applies: the LLM reads typed metrics; it does not invent them.

**FR-AS-8: Confirmation hooks**
Destructive, expensive, or otherwise irreversible operations shall pause the agent and require user confirmation. v0.1 confirmation triggers:
- Any solver execution (resource implication)
- Any operation that writes to MongoDB beyond the agent's own session records
- **(Forward-looking — not in M1 / not in sprint-03; applies once impact post-processing ships, post-M5.)** Any impact post-processing execution — Pelicun and any future tool of its class — parallel to solver execution. Pelicun consumes minutes of compute and produces durable artifacts; it is classed as solver-grade paid compute and requires explicit confirmation under this requirement (no cost-incurring run is silently initiated).

Cost-estimation-based triggers are deferred indefinitely until the system can produce cent-level precise estimates; surfacing approximate costs to the user is worse than not surfacing them at all.

**FR-AS-9: Capability discovery (leveled)**
The agent's tool inventory grows over time across capability discovery levels. Higher levels require more human involvement and are deferred to later versions. (These "levels" are independent of the tool layering in FR-TA.)

**Level 1a: QGIS algorithm discovery (in scope for v0.1).**
The agent can enumerate and describe the QGIS Processing algorithms available in the QGIS Server container (native QGIS, GDAL, GRASS, SAGA, plus any installed plugin-provided algorithms — typically 1000+ algorithms total). The agent uses this to handle queries that don't match a pre-wired tool, by discovering an appropriate algorithm at runtime and invoking it via the existing `qgis_process` tool. No human approval required; the agent operates entirely within the existing container. Implemented via tools `list_qgis_algorithms` and `describe_qgis_algorithm` (see FR-TA-2).

**Level 1b: Public hazard layer discovery (in scope for v0.1).**
The agent can search a curated catalog of authoritative public hazard layers (USFS Wildfire Hazard Potential, FEMA NFHL flood zones, USGS National Seismic Hazard Map, USDA Wildfire Risk to Communities, NOAA SLOSH outputs, etc.) and add matching layers to the user's project on demand. Used when the user's query is best answered by surfacing existing authoritative data rather than running a solver ("show me areas in Washington at risk of wildfire"). No human approval required for catalog entries already in the curated registry; web-driven discovery of non-registry layers is deferred (see Level 2b). Implemented via tools `hazard_catalog_search`, `fetch_public_hazard_layer`, and `summarize_layer_in_bbox` (see FR-TA-2), and the `show_hazard_layer` workflow (see FR-TA-1).

**Level 2a: QGIS plugin discovery (tentative, deferred to v0.2+).**
The agent could query the QGIS plugin repository to identify plugins that would expand the algorithm catalog (e.g., `qgis-modflow-plugin`, `qgis2fds`), and propose installation as a human-approved step. Installation is gated by confirmation hooks per FR-AS-8 and may have security implications worth assessing before implementation. Not designed in detail at v0.1 stage.

**Level 2b: Open hazard-layer discovery (tentative, deferred to v0.2+).**
Extending Level 1b to web-driven discovery of layers not in the curated catalog (academic projects, regional agency datasets, etc.). Quality and authoritativeness vary; results would be flagged accordingly and surfaced to the user with explicit caveats. Not designed in detail at v0.1 stage.

**Level 3: Solver feasibility research (tentative, deferred to post-v0.1).**
For hazards not covered by available QGIS algorithms, plugins, or public layers, the agent could evaluate candidate open-source solvers from the broader ecosystem (Cell2Fire, OpenQuake, etc.), assess integration mode (plugin-backed vs Python shim), output format compatibility, tooling maturity, and estimated integration effort, then return a structured feasibility report. The agent does not autonomously integrate new solvers — that remains a human decision and implementation task — but the research output informs roadmap decisions about which engines to add next. Concrete tool signature, scoring criteria, and output schema are to be designed when this work is actually picked up.

**FR-AS-10: User input solicitation (spatial and disambiguation)**
The agent shall request additional input from the user when extracted information lacks the precision required for the requested operation. Two distinct interaction patterns are supported:

- **Spatial input**: when a point or bbox is required but not extractable from available sources (e.g., "spill at an unnamed factory near the Mississippi"), the agent invokes `request_spatial_input` (see FR-TA-2). The client switches the map into pick-mode and the user clicks a point or drags a bbox.
- **Disambiguation**: when multiple plausible candidates exist for an extracted entity (e.g., "Springfield" with multiple US cities of that name), the agent invokes `request_disambiguation` (see FR-TA-2). The client presents candidates as a list and/or markers; the user picks one.

Both interactions are blocking: the calling tool waits for the response or a timeout. Timeouts are recoverable — they emit a typed error code (`SPATIAL_INPUT_TIMEOUT`, `DISAMBIGUATION_TIMEOUT`) which the calling workflow handles gracefully (typically by aborting the pipeline with a user-visible explanation).

The agent shall ask for input only when precision is genuinely needed for the task. Decision driven by `EventLocation.precision_class` (see Appendix C) and analogous classification at the tool level. Asking for input that the agent could reasonably infer is anti-pattern; failing to ask when modeling needs a precise location is also anti-pattern.

Recorded inputs are stored in the run's MongoDB document (`user_spatial_inputs` field) for reproducibility and audit.

**FR-AS-11: Ambiguity handling via clarification**
When the user's request is genuinely ambiguous and the choice between paths would substantially change the response, the agent shall invoke `request_clarification` (see FR-TA-2) rather than guessing. Common ambiguity cases:

- **Modeling vs discovery**: "wildfire in California" could mean "run a fire simulation" or "show existing risk maps" — different paths, different results
- **Hazard type**: a query referencing multiple co-occurring hazards where the user's intent isn't clear
- **Forcing-source choice**: when multiple plausible forcing sources exist for the same scenario

`request_clarification` presents the user with 2-4 substantively different options and blocks until the user picks one. Sparing use: the agent shall not invoke clarification when the request is unambiguous or when context makes the right path obvious.

This requirement replaces the previous notion of a separate intent classification phase (FR-TA-1 in prior versions). The LLM's choice of which workflow or tool to invoke *is* the intent classification; clarification handles only genuinely ambiguous cases.

**FR-AS-12: Default-by-fetch policy for workflow inputs**
Workflows shall fetch authoritative data for any parameter that has an authoritative public source, rather than requiring user input. The user-supplied parameter surface for any workflow shall be limited to:
- Spatial extent (bbox or point) — when not extractable from other sources
- Time window — when not implied by referenced events or sensible defaults
- High-level intent the agent cannot infer

Examples of parameters that shall **not** appear in workflow signatures as user-supplied:
- Wind fields (fetched from HRRR / ERA5 / RAP)
- Weather variables — temperature, humidity, precipitation (fetched from HRRR / ERA5)
- Fuel models for wildfire (fetched from LANDFIRE)
- Topography / DEM (fetched from USGS 3DEP)
- River bathymetry and gauge data (fetched from USGS NWIS / NHDPlus HR)
- Manning's roughness coefficients (derived from landcover via standard tables)
- Return-period precipitation (looked up from NOAA Atlas 14)
- Hurricane tracks (fetched from NHC ATCF by name or ID)

The system supports *override* of defaulted parameters through follow-up requests ("what if winds were 50% stronger") but never *requires* the user to supply them up front. Workflow docstrings shall explicitly enumerate what is fetched vs. what the user is expected to provide.

This requirement applies to every workflow, including those added in future versions. New workflows that demand user-supplied wind, fuel, or other fetchable parameters shall be considered failing FR-AS-12 review.

### 3.3 Tool Architecture (FR-TA)

The agent's tool inventory has two layers: deterministic **workflows** that implement common end-to-end patterns, and **atomic tools** that perform single-purpose operations. The LLM selects from both layers when responding to a user message — workflows for common requests, atomic tools (possibly composed) for novel or precise requests. Intent classification is not a separate phase; the LLM's choice of which tool to invoke is the classification.

**FR-TA-1: Workflows**
A workflow is a deterministic Python function exposing a stable signature and returning an `AssessmentEnvelope` or `ImpactEnvelope` (see Appendix B for both schemas). Workflows compose atomic tools in a tested sequence and are independently unit-testable without LLM calls. v0.1 workflows:

*Modeling workflows (`envelope_type: "modeled"`):*
- `run_storm_surge_flood(bbox, storm_track) → AssessmentEnvelope`
- `run_pluvial_flood(bbox, precip_event) → AssessmentEnvelope`
- `run_fluvial_flood(bbox, upstream_hydrograph) → AssessmentEnvelope`
- `model_news_event(news_query) → AssessmentEnvelope` (dispatches to one of the above based on event extraction)

*TELEMAC-MASCARET modeling workflows (`envelope_type: "modeled"`):* **(Forward-looking — not in M1 / not in sprint-03; first engine target post-MVP / v0.2+; targeted v0.3 alongside the TELEMAC engine addition — see §2.3 and Milestone M11.)** These workflows dispatch into the appropriate TELEMAC sub-solver based on intent — TELEMAC-2D for depth-averaged 2D shallow-water hydrodynamics (city-scale coastal storm-surge inundation, river flooding, dam-break), TELEMAC-3D for RANS-averaged 3D shallow-water hydrodynamics with vertical stratification (estuarine compound flooding, salinity/temperature stratification), TOMAWAC for spectral wind-wave propagation, ARTEMIS for phase-resolving harbor agitation, GAIA for unified sediment transport and morphodynamic evolution, MASCARET for 1D Saint-Venant river routing over long reaches. The engine common contract in §2.3 applies — adding these requires only workflow + atomic-tool registration, not engine-core changes.
- `run_coastal_storm_surge_telemac(bbox, storm_track, mesh_config?) → AssessmentEnvelope` — TELEMAC-2D unstructured-mesh storm-surge inundation; higher-fidelity complement to `run_storm_surge_flood` (SFINCS). Confirmation-gated per FR-AS-8 (paid compute, MPI multi-rank).
- `run_coupled_surge_wave(bbox, storm_track, wave_forcing?) → AssessmentEnvelope` — TELEMAC-2D coupled with TOMAWAC for wave setup / radiation-stress contribution to total water level.
- `run_river_hydraulics_mascaret(reach, hydrograph) → AssessmentEnvelope` — 1D Saint-Venant river routing over long reaches; faster than 2D where 1D is sufficient.
- `run_sediment_transport_gaia(bbox, hydrodynamics_run_id) → AssessmentEnvelope` — GAIA sediment / morphodynamic post-processing on a prior TELEMAC-2D/3D `solver_run_id` (analogous shape to the OpenDrift-on-flood-velocity pattern in the §2.3 deferred catalog). The data-dependency precondition (referenced hydrodynamics run exists and is complete) is enforced at the orchestration layer per FR-CE-6.

*Discovery workflow (`envelope_type: "discovered"`):*
- `show_hazard_layer(topic, location) → AssessmentEnvelope` — searches the curated public hazard catalog, fetches matching layers, adds them to the project, computes summary statistics from the displayed area, returns an envelope with no `solver_run_ids` and discovery-specific provenance

*Impact post-processing workflows (`envelope_type: "impact"`):* **(Forward-looking — not in M1 / not in sprint-03; first member targeted post-M5; see Milestone M5.5.)**
- `run_pelicun_impact(source_run_id | assessment_envelope, fragility_source?) → ImpactEnvelope` — accepts either a `solver_run_id` (the workflow resolves the envelope from MongoDB) or an in-memory `AssessmentEnvelope`; runs Pelicun with the chosen fragility/consequence library (HAZUS-EQ, HAZUS-HU, HAZUS-FL, FEMA-P58, or user-supplied — see OQ-8); returns an `ImpactEnvelope` with building-level damage states, loss, downtime, and casualty metrics. The data-dependency precondition (a referenced solver run exists and is complete) is enforced at the orchestration layer per FR-CE-6. Confirmation-gated per FR-AS-8 (paid compute).

The LLM selects the right workflow based on tool docstrings (FR-TA-3 metadata discipline). When the choice is genuinely ambiguous, the LLM invokes `request_clarification` (FR-AS-11) rather than guessing.

**FR-TA-2: Atomic tools**
Each atomic tool is a single-purpose function callable directly by the LLM (for novel queries) or by workflows. v0.1 atomic tools by category:

*Public hazard layer discovery:*
- `hazard_catalog_search(topic, location, source_filter?)` — searches the curated public hazard catalog (see §3.5.5 below) for layers matching a topic (wildfire, flood, seismic, etc.) and an optional location filter; returns ranked `CatalogEntry` objects with title, agency, format, coverage, and access URL
- `fetch_public_hazard_layer(catalog_entry_id, bbox?)` — adds a discovered layer to the current project, either as a remote WMS reference (when the source serves WMS, e.g., FEMA NFHL, USDA Wildfire Risk to Communities) or by downloading a clip of the source raster/vector to GCS and adding it via QGIS Server; returns a `ResultLayer` suitable for inclusion in `AssessmentEnvelope.layers`
- `summarize_layer_in_bbox(layer_uri, bbox, summary_type)` — computes summary statistics from a discovered layer over a target bbox (median value, area in each category, percentile distribution); used to populate the discovery envelope's metrics field without running a solver

*Data fetch:*
- `fetch_dem(bbox, resolution, source)` — USGS 3DEP, Copernicus DEM
- `fetch_landcover(bbox, source)` — NLCD, ESA WorldCover (via STAC)
- `fetch_river_geometry(bbox, source)` — NHDPlus HR
- `lookup_precip_return_period(location, return_period_years, duration_hours)` — NOAA Atlas 14
- `fetch_streamflow(gauge_id, start, end)` — USGS NWIS
- `fetch_hurricane_track(storm_name_or_id, source)` — NHC ATCF
- `fetch_tide_gauge(station_id, start, end)` — NOAA CO-OPS
- `fetch_buildings(bbox, source)` — Microsoft Building Footprints, OSM
- `fetch_critical_infrastructure(bbox, categories)` — OSM via Overpass

*Hazard event sourcing — news media (research mode):*
- `search_news(query, time_range, sources)` — wraps NewsAPI / GDELT / RSS; in research mode returns 1-2 top-relevance articles, in deep research mode returns up to 15
- `fetch_news_article(url)` — full article retrieval; canonical text extraction
- `extract_event_metadata(article_text, existing_event?) → EventMetadata` — structured extraction via Gemini; if `existing_event` is provided, new claims are merged into existing claim sets rather than replacing them

*Hazard event sourcing — authoritative agency feeds (research mode):*
- `fetch_nws_event(query_or_id, time_range)` — pulls NWS bulletins (flood warnings, storm reports, severe weather alerts) from api.weather.gov; returns structured records mappable to `NumericClaim` with `source_type: "agency"`
- `fetch_storm_events_db(time_range, bbox, event_type)` — queries NOAA Storm Events Database for historical events; returns records with damages, casualties, observed values
- `fetch_streamflow(gauge_id, start, end)` — USGS NWIS gauge readings (also used as a hazard event source when river-flood-related) — *already listed under data fetch*
- `fetch_hurricane_track(storm_name_or_id, source)` — NHC ATCF tracks (also used as a hazard event source) — *already listed under data fetch*

*Hazard event sourcing — generic web (research mode):*
- `web_fetch(url, options?)` — generic web fetch with sensible defaults (timeout, content extraction via readability heuristics, robots.txt respect, no JavaScript rendering); used when the agent encounters a URL not covered by structured-source tools. Options include `extract_content: bool` for body extraction vs raw HTML, and `timeout_seconds: int`.

*Hazard event aggregation:*
- `aggregate_claims_across_sources(claims: list[NumericClaim], strategy: str = "research") → ClaimSet` — computes consensus per FR-HEP-6. Strategy `"research"` applies the simple rules from FR-HEP-3; strategy `"deep_research"` applies authority-weighted consensus with outlier detection per FR-HEP-4. Returns a populated `ClaimSet` with `consensus_value`, `consensus_method`, and `consensus_confidence`.

*Hazard event sourcing — deferred to v0.2+ (with relevant hazard's engine):*
- `fetch_nifc_incidents(time_range, bbox)` — NIFC active and historical wildfire incidents
- `fetch_usgs_earthquake(time_range, bbox, min_magnitude?)` — USGS earthquake catalog
- `web_fetch_browser(url, wait_for?)` — headless browser fetch for JavaScript-heavy sites; deferred until a use case demands it

*Geocoding:*
- `geocode_event_location(metadata)` — place name → bbox

*MongoDB (via MCP):*
- Document queries, vector search, inserts — exposed by MongoDB's MCP server
- The agent uses these to: search prior events, store extracted events, record runs, find similar past scenarios

*QGIS operations (PyQGIS workers):*
- `qgis_process(algorithm, params)` — generic Processing algorithm wrapper
- `list_qgis_algorithms(category_filter, search_terms)` — enumerate Processing algorithms available in the QGIS Server container, with brief summaries; supports filtering by category (`hydrology`, `terrain`, `vector_general`, etc.) and free-text search; returns at most ~50 results per call to keep responses focused
- `describe_qgis_algorithm(algorithm_id)` — return full signature, parameter types, descriptions, and example usage for a specific algorithm; the agent uses this after `list_qgis_algorithms` to learn how to invoke a candidate algorithm via `qgis_process`
- Typed wrappers: `clip_to_basin`, `delineate_watershed`, `generate_mannings_grid`, `intersect_with_layer`, `reproject_layer`
- `update_project_layers(project_id, layers)` — add/remove/reorder layers in the `.qgs`
- `apply_style_preset(project_id, layer, preset)` — switch QML style
- `set_temporal_config(project_id, layer, start, end, step)` — configure WMS-T

The `list_qgis_algorithms` / `describe_qgis_algorithm` / `qgis_process` triple implements the QGIS algorithm discovery described in FR-AS-9 (capability discovery Level 1a); the agent uses this loop to handle queries that do not match a pre-wired typed wrapper.

*Model setup and execution:*
- `build_sfincs_model(dem, landcover, forcing, options) → ModelSetup` — wraps HydroMT
- `run_solver(solver, model_setup_uri, compute_class) → ExecutionHandle` — submits to Cloud Workflows
- `wait_for_completion(handle) → RunResult`
- `postprocess_flood(run_uri, outputs) → list[LayerURI]`

*Client control (via WebSocket message):*
- `zoom_to(bbox)` — instruct client to pan/zoom
- `set_layer_opacity(name, opacity)` — instruct client to update opacity
- `start_animation(layer)` — instruct client to start temporal playback

*User input solicitation (interactive, blocking):*
- `request_spatial_input(mode, title, description, suggested_view, reference_layers?) → Geometry` — the agent uses this when it needs the user to pick a point or bbox before continuing; emits a `spatial-input-request` over WebSocket and blocks until a `spatial-input-response` arrives or the request times out
- `request_disambiguation(title, description, candidates) → str` — the agent uses this when multiple plausible candidates exist for an extracted entity (typically a location); emits a `disambiguation-request` and blocks until the user picks one
- `request_clarification(question, options) → str` — the agent uses this when the user's request is genuinely ambiguous between substantively different paths (e.g., modeling vs discovery); emits a `clarification-request` and blocks until the user picks one of 2-4 substantively different options (see FR-AS-11)
- All three tools must be invoked from within a workflow or as part of a clearly-scoped task; the LLM is expected to use them sparingly and only when the precision required exceeds what was extracted (see FR-AS-10 and FR-AS-11)

*Location-resolved emission (side effect, not a tool):*
- The tools `geocode_location`, `extract_event_metadata`, and any workflow that determines a bbox shall emit a `location-resolved` message as a side effect, so the client auto-snaps the map to relevant locations. This is not a separate agent action; it is built-in behavior of resolution-producing tools.

*Deferred atomic-tool utilities (gating / hydro-conditioning / forcing-prep) — Forward-looking, not in M1 / not in sprint-03; first members register alongside the engines they prepare for.*
These are not workflow engines (they do not produce a georeferenced solver output of their own per the §2.3 Engine selection principle) and therefore do not belong in the §2.3 Deferred engines table. They are utility libraries that prepare inputs for, or gate execution of, downstream engines, and they register as atomic tools.
- `pysheds_condition_dem(dem_uri, fill_pits=True, breach_depressions=True) → LayerURI` — pure-Python DEM hydro-conditioning (pit-fill, breach, flow-direction, flow-accumulation, watershed delineation) using the pysheds library (GPLv3+; out-of-process invocation only — see NFR-L posture). Output feeds `pywatershed`, SFINCS pre-processing, and any flow-routing pipeline. Forward-looking; first registration target is the v0.2 hydrology engine work.
- `wrfxpy_prepare_forcing(domain, time_window, forcing_source) → ForcingBundle` — wrfxpy-mediated WRF/WRF-SFIRE domain setup and forcing preparation (GRIB ingest, namelist generation, HPC job orchestration). Treated as forcing-prep for the OpenWFM engine row (§2.3 deferred engines); not a hazard solver in its own right. Forward-looking; experimental.

*Deferred atomic-tool utilities (conservation / biodiversity) — Forward-looking, not in M1 / not in sprint-03; conservation/biodiversity engine class is forward-looking pending OQ-11 resolution. See OQ-11 in §6.*
Conservation/biodiversity tools consume biotic, abiotic, and connectivity inputs to produce species-distribution rasters, ecosystem-service rasters, or connectivity surfaces. They sit alongside (not inside) the hazard engine catalog. Whether the SRS models these as a new `hazard_type` literal, a parallel `analysis_type` discriminator, workflow-composition-only (using existing atomic tools), or a peer post-processor tool-class family is OQ-11.
- `run_maxent_sdm(occurrences, predictor_layers, output_bbox) → LayerURI` — Maxent species distribution modeling (presence-only, maximum entropy). Output is a continuous habitat-suitability raster.
- `run_invest_ecosystem_service(model_name, inputs) → LayerURI | dict` — InVEST (Integrated Valuation of Ecosystem Services and Tradeoffs) suite of models (water yield, sediment retention, carbon, pollination, coastal vulnerability, habitat quality, etc.). Output varies by sub-model.
- `run_circuitscape_connectivity(habitat_layer, source_targets) → LayerURI` — Circuitscape current-flow connectivity / corridor mapping. Output is a connectivity raster.

**FR-TA-3: Tool metadata discipline**
Every tool docstring shall include the structured "Use this when / Do NOT use this for" sections described in FR-AS-3. Sloppy metadata produces sloppy agent behavior.

### 3.4 Hazard Event Pipeline (FR-HEP)

The hazard event pipeline locates and synthesizes evidence about real-world hazard events from authoritative agency feeds, news media, and (in future versions) other signals. Two operational modes balance breadth against depth: **research mode** ships in v0.1 with a focused source set; **deep research mode** is a v0.2+ expansion that broadens coverage and adds multi-source convergence. Both modes share the same architecture, the same data schemas (Appendix C, including `ClaimSet`), and the same downstream consumers — they differ only in source breadth and aggregation depth.

**FR-HEP-1: Event-driven query handling**
The agent shall recognize prompts referencing recent or specific events (e.g., "model the flooding from Hurricane Ian," "show me what the latest typhoon in the Philippines could do") and route them through the hazard event pipeline.

**FR-HEP-2: Source-authority tiers**
Sources are classified into six authority tiers used by claim aggregation. Higher tiers carry more weight in consensus computation:

| Tier | Source type | Examples |
|---|---|---|
| 1 | Authoritative agency, direct measurement | USGS NWIS gauge readings, NHC official advisories, NWS observed values |
| 2 | Authoritative agency, derived or estimated | NWS forecast bulletins, USGS preliminary assessments, NIFC incident summaries |
| 3 | Major news, primary reporting | AP, Reuters, NYT, WaPo with direct sourcing |
| 4 | Regional news, primary reporting | Regional dailies, local TV websites |
| 5 | Aggregator / secondary reporting | News aggregators, secondary articles |
| 6 | Social / community | Twitter, Facebook, Nextdoor (deferred to v0.2+) |

Source tier is recorded per claim in `NumericClaim.source_type` (see Appendix C). The tier assignment is data-driven (from a curated source-to-tier mapping), not LLM-judged.

**FR-HEP-3: Research mode (v0.1 default)**
Research mode performs one sweep of a focused, vetted source set per query:

- 1-2 articles from NewsAPI (or equivalent), selected by relevance
- NHC ATCF when the event is hurricane- or tropical-storm-related
- NWS bulletins (api.weather.gov) when the event is flood, storm, or severe-weather-related
- USGS NWIS readings when river or gauge data is implicated
- Generic `web_fetch` as a fallback when a URL is referenced that is not in the structured-source set

Latency target: ~10-30 seconds per query. The agent emits standard pipeline progress messages throughout.

Aggregation in research mode is simple by design:
- If an agency-tier claim (Tier 1 or 2) exists, use its value as `consensus_value` with `consensus_method: "latest_authoritative"` and `consensus_confidence: "high"`
- If only news claims exist and there are multiple, take the median with `consensus_method: "median"` and confidence "medium"
- Single source → `consensus_method: "single_source"`, confidence "low" or "medium" depending on source tier
- No automated outlier detection in research mode (sample is too small to be statistically meaningful); deferred to deep research

**FR-HEP-4: Deep research mode (v0.2+, tentative)**
Deep research mode expands sweep breadth and aggregation depth:

- 5-15 articles from NewsAPI + GDELT + hyperlocal sources via `web_fetch`
- All applicable agency feeds for the relevant hazard type (NIFC for fire, USGS earthquakes for seismic, NOAA Storm Events DB for historical, state DOT / DEM for evacuation and damage data)
- Multi-pass extraction with cross-source validation
- Authority-weighted consensus, statistical outlier detection (claims >2σ from median flagged), time-aware convergence (early estimates separated from refined assessments)
- Optional refinement passes where the agent identifies gaps and fetches additional sources

Latency target: 1-5 minutes per query. The mode is exposed via the FR-WC-15 toggle but selecting it in v0.1 displays a notice that the feature is forthcoming and proceeds in research mode.

Deep research mode does not require schema changes — the `ClaimSet` machinery already supports arbitrary claim counts. v0.2 work focuses on additional source integrations, aggregation logic, and validation.

**FR-HEP-5: Event metadata schema**
Hazard event metadata shall be represented as an `EventMetadata` document, including the `ClaimSet`-wrapped numerical fields for multi-source provenance. The complete schema — including supporting types, the discriminated intensity union, claim and claim-set types, production and consumption patterns, and known open choices — is defined in **Appendix C: EventMetadata Schema**.

**FR-HEP-6: Multi-source claim aggregation**
Numerical evidence about an event is captured as `NumericClaim` objects (one per source per quantity) grouped into `ClaimSet` containers. The agent invokes `aggregate_claims_across_sources(claims) → ClaimSet` (see FR-TA-2) to compute consensus per quantity. Aggregation method is selected based on the claim set's characteristics (single source, multiple agency, mixed-tier, etc.) per FR-HEP-3 rules in research mode; FR-HEP-4 rules in deep research mode.

When the agent narrates an event ("Hurricane Ian had peak sustained winds of 140 kt"), the value cited shall be the `consensus_value` from the relevant claim set, and provenance shall be available for the user to inspect via the source list per FR-HEP-7.

**FR-HEP-7: Forcing reconstruction**
The pipeline shall map extracted event metadata to model forcing using consensus values from the relevant claim sets:
- Hurricane name → NHC ATCF track lookup → SFINCS storm surge boundary
- Rainfall event → NOAA gauge data or radar QPE → SFINCS pluvial forcing
- Dam failure → user-supplied breach hydrograph (per FR-AS-10 spatial input solicitation)

When metadata is insufficient (no consensus value, conflicting agency claims, missing required fields), the agent shall ask the user for clarification (FR-AS-11) rather than fabricating values.

**FR-HEP-8: Provenance and corpus management**
- Every model run or discovery operation derived from a hazard event shall record source article IDs in `AssessmentEnvelope.provenance` and the event ID in `provenance.event_id`
- Fetched articles shall be stored in MongoDB per the metadata-payload pattern (see §3.7 FR-MP); the article document is authoritative, with optional GCS payload for full HTML when retained
- Agency feed responses are stored similarly when they carry article-like content (NWS bulletins, USGS preliminary reports); transient API responses (single-value gauge readings, etc.) are referenced by URL but not necessarily archived in full
- Extracted event documents reference source articles by ID and may reference derived forcing data files in GCS
- User-facing summaries shall cite source articles and agency feeds by URL and publication/reporting date; the user can drill into the claim set to see all contributing sources for any narrated number

**FR-HEP-9: Similar event retrieval**
Using MongoDB Atlas Vector Search, the agent shall be able to find historical events similar to a query event ("show me past hurricanes that took a similar track and intensity"). Results may be used to inform model setup or to provide narrative context. The similarity search operates on the `events.embedding` field (see Appendix D.5).

### 3.5 QGIS Server and Project Management (FR-QS)

**FR-QS-1: QGIS Server deployment**
QGIS Server shall be deployed as a Cloud Run service, with autoscaling based on request rate. The container shall be based on the official `qgis/qgis-server` image, with additional QML styles and processing-algorithm-providing plugins (GRASS, SAGA, plus any hazard-relevant Processing plugins) baked in. The container shall additionally expose the `qgis_process` CLI to PyQGIS workers so that the agent's QGIS algorithm discovery (FR-AS-9, capability discovery Level 1a) can enumerate and invoke available Processing algorithms at runtime.

**FR-QS-2: Project file storage**
`.qgs` project files shall be stored in a GCS bucket as the canonical, authoritative source of project content. QGIS Server reads projects directly from GCS (via GDAL's `/vsigs/` virtual filesystem or signed URLs). A corresponding metadata document in MongoDB indexes each project for queryability; see §3.7 (FR-MP) for the pattern.

**FR-QS-3: Layer data storage**
- Rasters shall be Cloud Optimized GeoTIFF (COG) on GCS
- Vectors shall be FlatGeobuf or GeoParquet on GCS
- QGIS Server reads these directly via GDAL's `/vsigs/` virtual filesystem, streaming chunks as needed

**FR-QS-4: WMS-T temporal configuration**
For time-varying layers, the agent's `set_temporal_config` tool shall configure the temporal properties in the `.qgs` project such that QGIS Server serves WMS-T requests. The web client shall scrub through time by adjusting the `TIME` parameter on tile URLs.

**FR-QS-5: Style preset library**
A bundled library of QML style presets shall be applied by name:
- Flood depth (sequential blue ramp, 0–5m+)
- Flood velocity (sequential)
- Flood arrival time (sequential)
- Continuous DEM (terrain)
- Categorical landcover
- Hurricane track (line + impact buffer)
- Affected buildings (graduated by depth)

Style presets live in the QGIS Server container and in source control.

**FR-QS-6: PyQGIS worker pattern**
Project mutations (add layer, remove layer, change style, reproject, etc.) shall be performed by short-lived PyQGIS worker jobs (Cloud Run Jobs):
1. Worker pulls `.qgs` from GCS
2. Loads via PyQGIS (`QgsApplication`, `QgsProject.read()`)
3. Mutates project
4. Writes back to GCS
5. Notifies agent on completion
6. Agent emits `project_updated` to clients; clients invalidate tile cache

### 3.5.5 Public Hazard Catalog (FR-PHC)

**FR-PHC-1: Curated catalog of public hazard layers**
The project shall maintain a curated catalog of authoritative public hazard layers (`public_hazard_catalog.yaml` at the repository root). The catalog enables the discovery workflow (FR-TA-1 `show_hazard_layer`) and the discovery tools (FR-TA-2 `hazard_catalog_search`, `fetch_public_hazard_layer`).

**FR-PHC-2: Entry schema**
Each catalog entry shall include at minimum:
- `id`: stable identifier (e.g., `usfs-wildfire-hazard-potential`)
- `title`: human-readable name
- `agency`: source organization (e.g., "USFS", "FEMA", "USGS")
- `topic`: list of relevant hazard topics (e.g., `["wildfire", "fire_risk"]`)
- `coverage`: geographic scope (e.g., "CONUS", "California", "Global")
- `format`: data format (`wms`, `wmts`, `raster_cog`, `vector_fgb`, etc.)
- `access`: URL or service endpoint
- `style_preset`: default QML style preset to apply
- `license`: license text or URL
- `description`: brief description of what the layer represents
- `last_verified`: date the catalog entry was last verified working

**FR-PHC-3: Curation discipline**
Only authoritative sources (recognized government agencies, established academic centers, well-documented commercial data providers) appear in the catalog. The catalog is curated by hand in v0.1; web-driven or LLM-driven catalog expansion is deferred (see FR-AS-9 Level 2b).

**FR-PHC-4: Initial catalog scope (v0.1)**
v0.1 ships with curated entries covering at least:
- USFS Wildfire Hazard Potential
- USDA Wildfire Risk to Communities
- FEMA National Flood Hazard Layer (NFHL)
- FEMA National Risk Index
- USGS National Seismic Hazard Map
- USGS Landslide Hazards Program datasets
- NOAA SLOSH storm surge MOM/MOH outputs
- NOAA Sea Level Rise Viewer rasters

Coverage expands as additional engines and topics come online in later versions.

### 3.6 Data Tiers (FR-DT)

The system distinguishes two categories of geospatial data with different lifecycles, sources, and serving infrastructure. The agent never touches Tier A; the map never directly touches Tier B raw storage.

**FR-DT-1: Tier A — Browse data (ambient)**
Always-on global context data served by public tile providers. Stateless, anonymous, free or low-cost. Loaded by the map client directly from public CDNs without involving the agent or your infrastructure.

v0.1 Tier A sources:
- **Basemap**: OpenStreetMap raster tiles (`https://tile.openstreetmap.org/{z}/{x}/{y}.png`) with attribution. No API key, no account, no setup. Acceptable for v0.1 given the project has no users and traffic is limited to development and demo. The OSM tile usage policy restricts heavy production traffic; the basemap source shall be swappable to a managed or self-hosted provider when usage grows. Candidates documented for the swap: MapTiler (managed, free tier), Protomaps PMTiles (self-hosted on GCS).
- **Hillshade**: AWS Terrain Tiles (Terrarium-encoded). Disabled by default at the initial CONUS view; the agent may enable it when zooming to a specific AOI.

**FR-DT-2: Tier B — Solver and result data**
On-demand, AOI-specific data produced or fetched by the agent's pipeline. Stored in GCS, served via QGIS Server (WMS/WFS) for visualization or read directly by solver containers as input.

Tier B categories:
- *Solver inputs*: precise DEMs (USGS 3DEP, Copernicus), land cover (NLCD, ESA WorldCover), river geometry (NHDPlus HR), hurricane tracks (NHC ATCF), buildings (Microsoft Footprints, OSM)
- *Solver outputs*: flood depth, velocity, arrival time rasters; flood extent vectors; affected building tables
- *Cached fetches*: data sources keyed by `(source, bbox, resolution)` reused across runs to avoid re-fetch

Tier B is sparse — it exists only for areas the agent has analyzed. The map shows Tier A everywhere; Tier B overlays appear only where results exist.

**FR-DT-3: Initial map state**
On page load, the map shall display:
- Tier A basemap only
- Initial view bounding box: continental US (approximately lat 24-50, lon -125 to -66)
- Centered around the geographic center of CONUS at a zoom level showing the whole country
- No hillshade overlay
- No Tier B layers
- No agent activity until the user submits a query

**FR-DT-4: Geolocation**
The client shall not request browser geolocation in v0.1. The initial view is fixed at CONUS; users navigate via pan/zoom or via agent prompts ("show me Tampa").

**FR-DT-5: Tier separation rules**
- Agent tools shall never produce Tier A data
- The map client shall never read GCS directly; all Tier B data reaches the map via QGIS Server WMS/WFS endpoints or via GeoJSON payloads served by the agent
- Tier A providers may be swapped without affecting the agent or QGIS Server
- Tier B storage may be cleaned up (TTL policy) without affecting the basemap experience

**FR-DT-6: Overlap resolution**
Some datasets (DEMs, land cover, buildings) exist in both tiers conceptually. The rule:
- If the user is browsing for context, it stays in Tier A (basemap-level precision)
- If the agent needs the data for analysis or wants to show it precisely as a result, it moves into Tier B (full precision, via QGIS Server)
- The agent decides which based on the query; users do not toggle between them

### 3.7 Metadata-Payload Pattern (FR-MP)

The system applies a consistent storage pattern across project files, model runs, and news/event corpora: MongoDB holds metadata; GCS holds payload; the URI in the metadata document is the key to the GCS object.

**FR-MP-1: Storage roles**
- **MongoDB** holds metadata: searchable attributes, session associations, timestamps, embeddings, and URI references to payload data
- **GCS** holds payload: canonical files (`.qgs`, COGs, FlatGeobufs, optionally large text content), keyed by URI
- The URI stored in MongoDB is the key used to fetch the corresponding GCS object

**FR-MP-2: Read pattern**
All reads of payload data shall start with a MongoDB query that resolves to one or more URIs, then proceed to read the payload from GCS via those URIs. The system shall not enumerate GCS buckets to find objects; discovery is always via MongoDB.

**FR-MP-3: Source of truth**
- For data consumed directly by QGIS Server, PyQGIS workers, or solver containers (the canonical `.qgs` file, COGs, FlatGeobufs), the **GCS file is authoritative**; the MongoDB metadata is a derived index that can be rebuilt by scanning the bucket
- For data only the application uses (session ownership, tags, timestamps, hazard type tagging, embeddings), **MongoDB is authoritative**
- Writers shall update both stores atomically within a worker job; readers shall trust the source-of-truth designation per data category

**FR-MP-4: Independent lifecycle**
- TTL or archival policies on MongoDB documents shall not delete GCS payloads automatically; payload cleanup uses GCS lifecycle policies
- GCS lifecycle changes (e.g., move to coldline) shall not require MongoDB updates; metadata documents remain valid pointers regardless of underlying storage class
- Either store may be scaled or replaced independently without affecting the other's role

**FR-MP-5: Categories using this pattern**

The table below summarizes the five-collection structure. Full collection schemas — field definitions, indexes (including Atlas Vector Search), TTL policies, and design rationale — are defined in **Appendix D: MongoDB Collection Schemas**.

| Category | MongoDB collection | GCS payload | Source of truth |
|---|---|---|---|
| **Projects** | `projects` — session, hazard, bbox, layer summary, `qgs_uri` | `.qgs` file (XML) | GCS file |
| **Model runs and discoveries** | `runs` — status, embedded `AssessmentEnvelope`, metrics, provenance, user spatial inputs | COGs, FlatGeobufs via `assessment.layers[].uri` | Embedded `assessment` document |
| **Impact runs** *(forward-looking — not in M1 / not in sprint-03; targeted post-M5)* | `runs` (same collection) with `run_type: "impact"`; embeds an `ImpactEnvelope` (Appendix B.6c) in the `assessment` field as a discriminated envelope blob | FlatGeobuf / GeoParquet per-building damage and loss outputs via `assessment.layers[].uri` | Embedded envelope document |
| **News articles** | `articles` — URL, title, dates, embedding, optional `html_uri` | Full HTML if retained | MongoDB document |
| **Events** | `events` — `EventMetadata` documents (Appendix C) with embeddings | Forcing data files if derived | MongoDB document |
| **Sessions** | `sessions` — chat history, project IDs, pipeline history, current map state | (none) | MongoDB |

**FR-MP-6: Case UX (forward-looking; v0.2+)**

"Case" is the user-facing name for a `projects` document (FR-MP-5). The Case UX flow shall bind these behaviors:

- **Landing state.** On first visit, the user sees a two-pane shell: **Cases** in the left panel (list of the user's existing projects); **Chat** in the right panel. If no Cases exist yet, the left panel is hidden until a Case is created. Both side panels have a collapse toggle so the user can maximize the map.
- **Case creation.** The first agent prompt in a session that begins outside any Case implicitly creates a new Case (a new `projects` document) and binds the session to it. The Case's metadata captures the initial bbox/hazard/intent the agent infers.
- **In-Case state.** Within a Case, the left panel switches from "Cases list" view to "Case detail" view (the loaded layers list with visibility/opacity/order controls, per the layer-emission-contract in `docs/decisions/layer-emission-contract.md`). A "back to Cases" nav element returns to the list view.
- **Persistence.** When `model_flood_scenario` (or any layer-producing workflow) returns layers, the layers are saved into the Case's `projects` document `layer_summary` field, persisted at sprint-09's `publish_layer` tool exit. Chat history is persisted into the bound `sessions` document.
- **Resume.** Re-opening a Case rehydrates: chat history loads into the chat panel; layers re-register against QGIS Server (the published `.qgs` URI is the canonical source-of-truth per FR-MP-3); the agent receives the prior conversation context so it knows what the project is about.
- **Out-of-Case context.** Navigating back to the Cases list resets the chat to a fresh agent context (no prior conversation memory). The user clearly signals "leaving this project" by navigating; the chat panel UI shall reflect the context change (e.g., empty state with "select a Case or start a new one").

Implementation refinement is deferred to its target sprint; the contract above pins intent. The Case identifier maps 1:1 to the `projects._id` ULID; UI labels say "Case", schema and code say "Project" (FR-MP-5 nomenclature stays canonical).

**v0.3.22 amendment (sprint-12-mega Wave 1, job-0099).** The Case-persistence envelopes that back FR-MP-6 are locked in `packages/contracts/src/grace2_contracts/case.py`: `CaseSummary` (the left-rail entity, denormalized from `projects`), `CaseChatMessage` (a persisted single chat exchange carrying per-turn `layer_emissions` + `map_command_emissions` so rehydration can replay the layer-binding sequence deterministically — Invariant 1), `CaseSessionState` (the rehydration envelope returned on Case open), and the Appendix A.4/A.3 lifecycle envelopes `case-list` (server → client left-rail list), `case-open` (server → client rehydrate), and `case-command` (client → server `create` / `select` / `rename` / `archive` / `delete`, with a closed-enum command discriminator mirroring the `map-command` one-umbrella-type pattern). All envelopes are pydantic v2 `GraceModel` subclasses, carry `schema_version: "v1"`, carry no cost field (Invariant 9), and route cancellation through the existing A.3 `cancel` message (Invariant 8 — no `case-command` cancellation variant). The `case_id` is the `projects._id` ULID 1:1; FR-MP-5 nomenclature stays canonical in storage. Wave 2 Case UX work (agent + web) consumes this envelope shape as the gating contract.

### 3.8 Cloud Execution (FR-CE)

**FR-CE-1: Solver containerization**
SFINCS shall be packaged as a Docker container suitable for Cloud Run Jobs execution. The container reads input from GCS, runs the solver, writes outputs as COG to GCS, and emits a completion event.

**FR-CE-2: Job orchestration**
Multi-step model runs shall be orchestrated by Cloud Workflows. The agent submits a workflow execution; Cloud Workflows handles retries, parallelism, and step-level error handling.

**FR-CE-3: Worker compute classes**
The `run_solver` tool shall accept a `compute_class` parameter mapping to Cloud Run resource configurations:
- `small`: 2 vCPU, 4GB, for tests and tiny domains
- `medium`: 4 vCPU, 8GB, for typical event runs
- `large`: 8 vCPU, 32GB, for high-resolution or large-domain runs

**FR-CE-4: Output format**
All raster outputs shall be COG. All vector outputs shall be FlatGeobuf or GeoParquet. All outputs include CRS, units, and provenance metadata.

**FR-CE-5: Impact post-processing as a registered Cloud Workflow** *(Forward-looking — not in M1 / not in sprint-03; targeted post-M5, see Milestone M5.5.)*
Impact post-processing tools (Pelicun and any future member of the tool class defined in Decision N) shall be packaged as Docker containers and dispatched as Cloud Run Jobs orchestrated by Cloud Workflows, in the same pattern as the SFINCS solver (per FR-CE-1, FR-CE-2). The Pelicun container reads its inputs from GCS, runs the assessment, writes per-building damage and loss results as FlatGeobuf or GeoParquet to GCS (per FR-CE-4), and emits a completion event. Workers run on Cloud Run Jobs with no minimum instances (per NFR-C-2) so no idle cost is added. The workflow shall be deterministic — driven by `run_pelicun_impact`, not by an atomic-tool reasoning loop — to satisfy NFR-C-3.

**FR-CE-6: AssessmentEnvelope precondition** *(Forward-looking — not in M1 / not in sprint-03; targeted post-M5.)*
Impact post-processing shall not be dispatched until a referenced `solver_run_id` is in `complete` status with a persisted `AssessmentEnvelope`. The Cloud Workflow resolves the envelope by `run_id` from MongoDB (per FR-MP-2: read pattern always via MongoDB) before invoking the container. This makes the data-dependency invariant load-bearing at the orchestration layer: Pelicun is never invoked before a simulation result exists. If the referenced run is `pending`, `running`, `failed`, or `cancelled`, the workflow shall return a typed error (`IMPACT_PRECONDITION_NOT_MET`) rather than dispatch the job.

**FR-CE-7: Cancellation conformance** *(Forward-looking — not in M1 / not in sprint-03; targeted post-M5.)*
Impact post-processing jobs shall honor the same 30-second cancellation contract as solvers (per FR-AS-6 and NFR-R-3). A `cancel` message shall signal the in-flight Cloud Workflows execution, propagate to the Pelicun Cloud Run Job, and complete within 30 seconds. Cancellation routes through the same UI cancel button as solver runs (per FR-WC-9); no new UI surface is required. Single-asset and small-portfolio Pelicun runs fit comfortably inside this budget; large regional runs that cannot complete cancellation in 30 seconds shall be decomposed into chunked sub-jobs or staged behind a longer-running cancellation token in a later version (not in v0.1 scope).

**FR-CE-8: Atomic-tool data fetches go through the cache shim** *(Forward-looking — binding from M4 when the first data-fetching atomic tools register; see Decision O.)*
Every atomic tool that issues a network call to an external public data source shall route through the shared cache shim defined in §3.9. The shim handles read-through, write-on-miss, content-addressed key derivation, and lifecycle eviction. Each atomic tool declares one of four TTL classes (`static-30d`, `semi-static-7d`, `dynamic-1h`, `live-no-cache`) at tool-definition time per FR-DC-2. Cached artifacts persist in a dedicated bucket prefix (`gs://<bucket>/cache/<source-class>/<hash>.<ext>`); the shim is the sole writer of that prefix. Tools that compute purely from already-cached inputs may read through the shim without writing new entries. Interactive tools (`request_spatial_input`, `request_disambiguation`, `request_clarification`), envelope emitters, and MongoDB writes are uncacheable by construction and shall not invoke the shim. See §3.9 for the architecture, Appendix E for plugin-side cache implications.

---

### 3.9 Data Caching (FR-DC) *(Forward-looking — not in M1 / not in sprint-03; binding from M4 when the first data-fetching atomic tools register.)*

**Status note.** Decision O establishes cache-mediated atomic-tool data fetching as the binding architecture from M4 forward. This section formalizes the bucket layout, TTL taxonomy, key derivation, write semantics, eviction policy, and the uncacheable-by-construction enumeration. FR-DC requirements are forward-looking for v0.1 scope but become load-bearing for every atomic tool that touches an external API once M4 starts emitting real data fetches.

**FR-DC-1: Cache bucket layout.**
Cached artifacts live under `gs://<project-bucket>/cache/<ttl-class>/<source-class>/<hash>.<ext>` — **per-TTL-class prefix nesting per job-0031 live substrate** (the v0.3.15 prose originally said `gs://<bucket>/cache/<source-class>/<hash>.<ext>` flat; the live `grace-2-hazard-prod-cache` bucket implements per-TTL-class nesting so GCS Object Lifecycle Management rules can scope to a TTL prefix without requiring a separate rule per source — `cache/static-30d/*` gets 30-day eviction, etc. This scales cleanly past the 100-rule GCS cap that per-source rules would burn through by source ~80). `<ttl-class>` is one of the four FR-DC-2 values (`static-30d` / `semi-static-7d` / `dynamic-1h` / `live-no-cache`). `<source-class>` is a stable identifier per atomic tool (`dem`, `landcover`, `buildings`, `nwis_iv`, `atcf`, `mrms_qpe`, `precipitation_atlas14`, etc.); `<hash>` is the content-addressed key per FR-DC-3; `<ext>` is the source-appropriate format (`tif` for COG rasters, `fgb` for FlatGeobuf, `json` for JSON payloads, `nc` for NetCDF, `grib2` for GRIB2, etc.). The bucket is shared across atomic tools and across sessions so the dedup guarantee (FR-DC-4) holds across users. **Appendix B worked-example paths** (`gs://bucket/cache/dem/<hash>.tif`, `gs://bucket/cache/buildings/ms_<hash>.fgb`) **are aliases for legibility** — actual on-disk paths nest under the TTL class first; downstream readers can use either form because the cache shim normalizes.

**FR-DC-2: Four TTL classes registered per tool.**
Each atomic tool declares one of four TTL classes at tool-definition time (typically as a decorator argument or registration metadata):

- `static-30d` — terrain (3DEP DEM tiles, Copernicus DEM), year-stamped landcover snapshots (NLCD, ESA WorldCover), NHDPlus HR reach geometry, NOAA Atlas 14 return-period curves, building footprint snapshots (Microsoft ML Building Footprints, OSM Overpass extracts), curated public hazard rasters that publish on annual or slower cadences. 30-day default; longer-lived sources can pin to 90 days via the same registration machinery without changing the shim.
- `semi-static-7d` — post-season ATCF best-track records (storms older than 14 days), USGS earthquake catalog historical queries, NOAA Storm Events DB historical queries, FEMA NFHL periodic releases, USDA WRC layers, wildland fire hazard potential rasters where the source updates on weekly-to-monthly cadence.
- `dynamic-1h` — active NHC advisories (storm currently in basin), NWIS streamflow recent windows (last 24 h), NOAA CO-OPS tide gauges, MRMS 2-minute QPE accumulations, NWS active bulletins, NIFC active wildfire incidents, news search results, GDELT queries, api.weather.gov queries for current conditions.
- `live-no-cache` — read-through with immediate expiry; reserved for atomic tools whose contract demands "right now" freshness. Encoded as `ttl_class: "none"` with `expires_at = fetched_at`; the lifecycle policy purges immediately and every read misses. The uncacheable-by-construction enumeration in FR-DC-6 lists the tools that always declare this class.

Per-call TTL overrides are permitted when response metadata changes the classification at fetch time. Concrete example: `fetch_hurricane_track("IAN", source="atcf")` defaults to `static-30d` because Hurricane Ian is closed; a call for an active storm in the current basin returns `dynamic-1h` because the ATCF response carries an `is_active` marker the shim inspects before write. The override is a property of the response, not the caller; the per-tool default is the lower bound on freshness.

**FR-DC-3: Read-through / write-on-miss with content-addressed keys.**
The shim wraps each atomic tool's network call. On invocation:

1. Compute the cache key as `sha256(source_id || canonicalized_params || ttl_bucket_vintage)` truncated to a stable hex prefix (16–32 hex characters). `canonicalized_params` is a deterministic serialization of all parameters that affect the response (bbox rounded to source-native resolution, date ranges quantized to the TTL bucket, query string keys sorted, optional fields omitted when at default). `ttl_bucket_vintage` is the current TTL-class window boundary (e.g. `2026-06-06T16:00:00Z` for `dynamic-1h`, `2026-W23` for `semi-static-7d`, `2026-06` for `static-30d`), so two calls in the same bucket hit the same cache entry and a bucket boundary forces a refresh.
2. Look up `gs://<bucket>/cache/<source-class>/<hash>.<ext>`. If present and not past `expires_at`, return the cached artifact (URI plus metadata).
3. On miss: invoke the external API; on success, atomically write the response to the cache key, attach an object-level `Cache-Control` header reflecting the TTL class, and return.
4. On API failure: do not write a sentinel; surface the error verbatim so the agent surface (per FR-AS-11) can decide whether to retry, clarify, or fall back.

The shim is the sole writer of the `cache/` prefix; atomic tools never write there directly.

**FR-DC-4: Deduplication guarantee.**
Two atomic-tool invocations that produce the same cache key shall share the same artifact regardless of session, user, or invocation order. This holds because keys are content-addressed (FR-DC-3) and the bucket is shared (FR-DC-1). The shim does not maintain a write lock — last-writer-wins on simultaneous misses is acceptable since both writers produce byte-identical artifacts (the key derivation already factored in everything that would differ).

**FR-DC-5: Lifecycle eviction at the bucket level.**
The `cache/` prefix uses GCS Object Lifecycle Management rules tied to the TTL classes: objects under `cache/<ttl-class>/` inherit a `daysSinceCustomTime > N` deletion rule where N is the TTL-class day count (30, 7, 1, or 0). The shim sets `customTime = fetched_at` on every write so the lifecycle policy can evict without the shim tracking individual TTLs at read time. Eviction is asynchronous; a slightly stale read between the bucket boundary and the lifecycle pass is acceptable (the next write through that key replaces it). Bucket versioning is off for the `cache/` prefix to keep storage cost flat. **`cache/live-no-cache/` lifecycle rule is intentionally a no-op** — GCS rejects `daysSinceCustomTime=0` so the live rule's `days` field resolves to null and never purges; this is acceptable because FR-DC-6 enumerates `live-no-cache` tools as uncacheable-by-construction (the shim short-circuits before any GCS write for that class, so nothing should ever land at `cache/live-no-cache/*` in practice — the lifecycle rule exists as defense in depth, not as the load-bearing enforcement layer).

**FR-DC-6: Uncacheable-by-construction enumeration.**
The following atomic-tool classes shall not invoke the cache shim and shall always declare `ttl_class: "none"` (or omit the registration entirely if the tool produces no fetchable artifact):

- Interactive solicitation tools: `request_spatial_input`, `request_disambiguation`, `request_clarification` (user-driven, session-bound, no GCS artifact).
- Envelope emitters writing to the WebSocket: `agent-message-chunk` streams, `pipeline-state` snapshots, `session-state` updates, `tool-call` and `tool-result` notifications, `cancel` and `clarification-request`.
- MongoDB MCP writes (sessions, runs, projects, events, layers, sessionrecords inserts/updates) — durable knowledge layer per Decision F lives in Atlas, not in the cache bucket.
- Solver dispatchers and their result fetches (`run_sfincs_solver`, `run_pelicun_impact`, etc.) — solver outputs persist under `gs://<bucket>/runs/<run_id>/` per FR-CE-4, not under `cache/`.
- One-shot diagnostic calls the user explicitly opts in to ("fetch the absolute latest from NWIS as of right now") via a per-call `cache=false` override.

Anything not on this list and not explicitly declared as one of `static-30d` / `semi-static-7d` / `dynamic-1h` at registration time shall fail tool-registration validation (per FR-AS-3): the cache class is a required property of every external-API atomic tool.

---

### 3.10 Failure Recovery (FR-FR) *(Forward-looking — v0.3.19 amendment. The deny/retry/chat gate envelopes + agent-side max-turns cap are forward-looking; multi-agent pre-verifier and scientific-output verification are explicitly deferred.)*

**Status.** v0.1 currently relies on (a) hard cancel via Invariant 8 (verified at 850 ms in job-0041), (b) hard solver timeouts (Cloud Run Job `--task-timeout=1800`), (c) fail-fast on upstream errors (atomic tools raise typed errors per FR-AS-11; cache shim never writes sentinels per FR-DC-3), and (d) Invariant 7 validation gates that fail closed on silent-wrong-answer modes (job-0042 verified LIVE on real production data — NLCD palette encoding caught before bad Manning's defaults could produce a misleading flood map). These four mechanisms catch the catastrophic failure modes. What v0.1 does NOT have: bounded automatic recovery, explicit agent-side max-turns cap, scientific-output verification, multi-agent pre-verification. §3.10 formalizes the next layer with a deliberately minimal substrate, deferring the larger subsystem buildouts until after the working M5 / M6 demo.

**FR-FR-1: Deny / Retry / Chat recovery gate — minimal envelope substrate** *(Forward-looking — Appendix A amendment lands at the next schema sprint; web-client implementation follows the existing `request_clarification` modal pattern.)*

When an atomic tool fails with a **recoverable** error class (see FR-FR-2 routing table below), the agent shall surface a `recovery-choice` envelope rather than narrating the failure or silently retrying. The user-facing UX is a small modal (mirrors the §F.3 popup discipline — out of band of chat envelope) with three actions:

- **Deny** — record the user's decision; mark the pipeline step `failed` with the upstream error preserved; agent narrates the failure honestly in chat and considers next-steps without retrying.
- **Retry** — re-attempt the failed atomic-tool call exactly as before. Cache shim discipline still applies (FR-DC-3 read-through, no sentinel). Useful for transient 5xx, network blips, and the kind of upstream flakiness that's likely to resolve in seconds.
- **Chat** — replace the modal with a focused single-line text input; user types guidance (e.g., "try the WCS endpoint instead of WMS"); the typed text becomes a focused `user-message` payload that the agent uses to decide the next action. This is the "nudge it in the right direction" surface the LLM benefits from when retry alone won't work.

Envelope shape (NEW Appendix A amendment when this lands — sprint slot TBD):

```jsonc
{
  "type": "recovery-choice",
  "id": "01K…", "ts": "…Z", "session_id": "01K…",
  "payload": {
    "request_id": "01K…",
    "failed_step_id": "01K…",
    "error_code": "UPSTREAM_API_ERROR",      // Appendix A.6 SCREAMING_SNAKE_CASE
    "error_message": "USGS 3DEP returned HTTP 503 — service unavailable",
    "context": "fetching DEM at Fort Myers bbox for flood scenario",
    "options": ["deny", "retry", "chat"],
    "ttl_seconds": 300
  }
}
```

Response (client → agent):

```jsonc
{
  "type": "recovery-choice-response",
  "id": "01K…", "ts": "…Z", "session_id": "01K…",
  "payload": {
    "request_id": "01K…",
    "choice": "retry",                       // or "deny" or "chat"
    "chat_text": null                        // populated only when choice == "chat"
  }
}
```

**FR-FR-2: Per-error-code routing table.** Not every failure surfaces the gate. The agent classifies the failed step's `error_code` (Appendix A.6) at the time of `PipelineEmitter.mark_failed` and either gates the user OR fails closed without prompting.

| Error class | Examples | Recovery behavior |
|---|---|---|
| **Transient upstream** | `UPSTREAM_API_ERROR` (5xx), `NETWORK_TIMEOUT`, `RATE_LIMITED` | Gate the user with deny/retry/chat. Retry is genuinely likely to succeed. |
| **Recoverable-with-context** | `GEOCODE_NO_MATCH`, `BBOX_INVALID`, `INSUFFICIENT_INPUT` | Gate the user; chat path is the high-value option (the LLM needs more info). |
| **Substrate integrity (FAIL CLOSED — DO NOT GATE)** | `LULC_MAPPING_MISMATCH` (Invariant 7), `SCHEMA_VALIDATION_FAILED`, `CACHE_CORRUPTION_DETECTED`, `IAM_PERMISSION_DENIED` | NO gate. Surface as honest failure in chat with explicit operator-action narration. Retrying would be incorrect (Invariant 7) or futile (IAM). |
| **User-initiated stop** | `USER_CANCELLED` (Invariant 8) | NO gate. User already decided. |
| **Cost / budget overrun** | `SESSION_TOKEN_BUDGET_EXCEEDED` (future, see FR-FR-4) | NO gate; agent halts the session. User can start a new session. |

The classification table is data, not code — when a new error code lands (e.g., job-0041's `SOLVER_FAILED`, `SOLVER_DISPATCH_FAILED`, `SOLVER_TIMEOUT`), it gets classified at registration time per the same FR-CE-8 fail-fast discipline used for atomic-tool metadata.

**FR-FR-3: Agent-side max-turns cap — cheap insurance.** Until the multi-agent verifier (deferred per below) lands, the agent service shall pin an explicit `MAX_TURNS_PER_SESSION` cap (TENTATIVE default: 25) in its ADK configuration. On the (25+1)th turn the agent service emits a final `session-state` with `status: "max_turns_reached"`, sends a closing `agent-message` summarizing what's been done, and refuses further tool calls in this session. Cheap insurance against runaway LLM tool-call chains. The user can start a new session to continue. **Targeted landing: sprint-08 small task — single config line in `services/agent/src/grace2_agent/main.py` + a new envelope status enum value.**

**FR-FR-4: Per-session token budget — deferred** *(forward-looking; no v0.1 implementation)*. Separate from Invariant 9's no-cost-theater discipline (which forbids surfacing dollar estimates), an internal session-token-budget would fail closed before a runaway LLM eats unbounded Vertex AI tokens. Decision affects the new `SESSION_TOKEN_BUDGET_EXCEEDED` error code, the agent service's token accounting, and the `session-state` envelope's `status` enum. Out of v0.1 scope; revisit when production observability surfaces realistic budgets.

**FR-FR-5: Multi-agent pre-verifier — deferred indefinitely** *(forward-looking)*. The eventual subsystem: a **planner** agent decomposes the user query into a tool-call plan; an **executor** agent runs the plan with the existing atomic-tool surface; a **verifier** agent inspects the executor's intermediate outputs against expected shapes / sanity ranges / cross-source consistency before letting the chain proceed. Catches the "faceplant" class of failures (executor goes off the rails because of bad LLM judgment) without involving the user. Requires: ADK multi-agent orchestration (Decision E may need amendment); new MongoDB collections for planner/verifier conversation state (Decision F); new envelopes for inter-agent dispatch (Appendix A); careful Invariant 1 + 2 discipline because verifier judgment IS LLM-mediated. **Status: DEFERRED — slot at M6+ or whenever post-MVP review surfaces this as the bottleneck. User direction 2026-06-07: "we should slot a minimal version and then later add the multi agent/user guided workflow."**

**FR-FR-6: Scientific output verification — deferred** *(forward-looking; post-M5 work)*. The next layer above FR-FR-5: cross-check OUTPUT (flood depth, building damage, etc.) against external ground truth — historical observations, FEMA NFHL overlays, USGS gauge records during the modeled event, etc. Requires its own milestone given the research-grade nature of the comparison. Out of v0.1; revisit after the multi-agent verifier matures.

**Open Questions from §3.10.**

- **OQ-FR-1 (TENTATIVE, near-term):** `MAX_TURNS_PER_SESSION` default value. 25 is a round number; whatever ADK's internal default is (likely lower) should be compared. Revisit after a few sprints of production-like usage.
- **OQ-FR-2 (TENTATIVE, sprint-08+):** Should the deny/retry/chat gate honor a max-gates-per-session cap so the user isn't gated to death? Probably yes (e.g., 5 gates per session, then degrade to fail-closed); pin at first web-client integration.
- **OQ-FR-3 (TENTATIVE, deferred):** Should `retry` on a `RATE_LIMITED` failure automatically wait for the upstream's stated Retry-After header before the next attempt? Probably yes; lands when the bounded-retry-with-backoff infrastructure ships.
- **OQ-FR-4 (TENTATIVE, deferred):** Session-token-budget default (FR-FR-4 above).
- **OQ-FR-5 (TENTATIVE, deferred):** Multi-agent pre-verifier architecture (FR-FR-5 above).
- **OQ-FR-6 (TENTATIVE, post-M5):** Scientific output verification approach (FR-FR-6 above).

---

## 4. Non-Functional Requirements

### 4.1 Performance (NFR-P)

- **NFR-P-1**: Chat round-trip (user message to first streamed token) shall be under 2 seconds for typical queries
- **NFR-P-2**: WebSocket latency between client and agent shall be <300ms p95 from US West Coast
- **NFR-P-3**: WMS tile response from QGIS Server shall be <1 second p95 for typical layer combinations
- **NFR-P-4**: A small-domain flood model (≤200 km² at 30m resolution) shall complete end-to-end in under 15 minutes from user prompt to map display
- **NFR-P-5**: Time scrubbing shall feel responsive (≤500ms between user drag and new tiles displayed) for typical temporal layers
- **NFR-P-6**: Vector search over the event corpus shall return results in <2 seconds for corpora up to 100k events

### 4.2 Reliability (NFR-R)

- **NFR-R-1**: A failed tool execution shall surface as a failure in the pipeline strip with diagnostic logs accessible; the session shall remain usable
- **NFR-R-2**: WebSocket disconnection shall trigger automatic reconnection with state recovery from the agent
- **NFR-R-3**: Solver job cancellation shall complete within 30 seconds of user request
- **NFR-R-4**: QGIS Server instances shall be stateless and replaceable; loss of an instance shall not impact in-flight sessions beyond a brief tile-loading delay

### 4.3 Portability (NFR-PO)

- **NFR-PO-1**: Web client shall run on current Chrome, Firefox, Safari, Edge
- **NFR-PO-2**: Mobile-responsive layout is in scope; specifically-optimized mobile UX is deferred
- **NFR-PO-3**: All cloud infrastructure shall be deployable via infrastructure-as-code (Terraform or equivalent) to support reproducible environments

### 4.4 Security (NFR-S)

- **NFR-S-1**: WebSocket connections shall use WSS with TLS 1.2+
- **NFR-S-2**: Google Cloud credentials shall be managed via service accounts and Workload Identity; never embedded in client code
- **NFR-S-3**: MongoDB connection strings shall be stored in Google Secret Manager
- **NFR-S-4**: User-supplied URLs (for news fetching) shall be validated against a domain allowlist or sandboxed appropriately
- **NFR-S-5**: GCS bucket access shall be scoped by service account; no public buckets except for shared snapshot assets

### 4.5 Infrastructure budget (NFR-C)

These targets cover infrastructure spend on the deployment side; they are *not* per-run cost figures surfaced to users. User-facing cost estimation is deferred indefinitely (see FR-AS-8).

- **NFR-C-1**: Cloud idle cost (no active sessions) shall remain under $100/month for the default deployment footprint, including:
  - Cloud Run minimum instances for agent and QGIS Server
  - MongoDB Atlas (M10 cluster or smaller)
  - GCS storage (≤100GB)
  - Cloud Workflows base
- **NFR-C-2**: Solver workers shall use Cloud Run Jobs with no minimum instances (scale to zero between jobs)
- **NFR-C-3**: LLM token usage shall be minimized via deterministic workflows; common queries shall not require full atomic-tool reasoning loops

### 4.6 Licensing (NFR-L)

- **NFR-L-1**: The project repository shall include an OSI-approved open source license file at the repository root, detectable by GitHub's license detection
- **NFR-L-2**: All third-party dependencies shall be tracked and license-compatible
- **NFR-L-3**: News-derived model runs shall cite source articles in user-facing output

---

## 5. Out-of-Scope (v0.1) — Deferred to Future Versions

Items deferred to future versions are not categorically excluded; they are simply staged behind work that's currently more tractable. See §2.3 for the engine-selection principle (Decision J in §2.1).

| Feature | Notes |
|---|---|
| 3D visualization | Pitch/bearing camera, terrain (DEM-derived), 3D building extrusions colored by impact. MapLibre supports all of these natively; deferred for v0.1 to reduce scope. Likely v0.2 alongside additional hazards. |
| Additional engines | Groundwater (MODFLOW), wildfire, seismic, contaminant transport — see engine catalog §2.3 for tentative integration modes and target versions |
| Solver feasibility research as agent capability | Level 1a of capability discovery (QGIS algorithm enumeration) and Level 1b (public hazard layer discovery) ship in v0.1; Levels 2 and 3 (plugin discovery, open layer discovery, external solver research) are tentative and deferred — see FR-AS-9 |
| Multi-hazard chaining | E.g., wildfire → debris flow, earthquake → tsunami, storm → spill |
| Multi-user collaboration | Shared sessions, project sharing, real-time co-presence |
| Custom domain MCP server | v0.1 uses MongoDB's off-the-shelf MCP; custom hazard-domain MCP tools could improve agent ergonomics later |
| Drawing / annotation tools | User-drawn AOIs, points of interest, annotations |
| Print and export reports | PDF reports of model runs with embedded maps and metrics |
| QGIS Desktop plugin distribution | A complementary desktop experience for power users |
| Mobile-specific UX | Mobile-responsive works; mobile-optimized doesn't |
| Multi-LLM provider abstraction | v0.1 uses Gemini 3 exclusively; abstraction would only matter for non-Google deployments |
| Solvers requiring substantial new tooling | Full 3D CFD flooding, coupled fire-atmosphere, 3D ocean simulation, multiphase debris flow — supported in principle by the architecture but not on the current roadmap |

---

## 6. Open Questions

1. **Agent deployment target**: Cloud Run with WebSocket, or Vertex AI Agent Engine? Both work with ADK. Agent Engine is more managed but may have WebSocket limitations. Needs verification before M2.
2. **MongoDB MCP server hosting**: self-hosted alongside the agent (Cloud Run sidecar), or use MongoDB's hosted MCP endpoint (if/when available)? Sidecar gives most control. Collection schemas are defined in Appendix D; FR-AS-4 covers the agent's connection to MCP.
3. **News API selection**: NewsAPI (cheap, broad, paid tiers), GDELT (free, massive, complex), or a mix? Probably mix — GDELT for global event discovery, NewsAPI for targeted article fetching.
4. **HydroMT integration depth**: full reliance for SFINCS setup, or custom config builders? HydroMT is powerful but adds a heavy dependency.
5. **Forcing data caching** *(Closed by Decision O in v0.3.15 — see §2.1 Decision O, §3.9 / FR-DC-1..6, and FR-CE-8. Numbering retained for historical traceability per the v0.3.10 dual-lineage convention.)*: DEMs and landcover for the same bbox shouldn't be re-fetched per run. GCS-backed cache keyed by `(source, bbox, resolution)` is the working assumption; concrete cache-key conventions and eviction policy to be designed during M4 / M5.
6. **Pre-baked demo scenarios**: should specific high-impact historical events (Hurricane Ian, etc.) have pre-computed results available for instant demo, or always run live? Live is more impressive when it works; pre-baked is safer for demos.
7. **Vector embedding dimension**: `text-embedding-005` defaults to 768-dim but supports configurable down to 128. Verify recall trade-off on a small corpus before locking the Atlas Vector Search index config. The model itself is decided (see Decision L in §2.1).
8. **Fragility and consequence-curve sourcing for Pelicun** *(forward-looking — not blocking M1 / sprint-03; decide before M5.5)*: choose between HAZUS Hurricane/Flood damage functions (coarser, building-level, regional-scale appropriate), FEMA P-58 component fragilities (component-level, building-specific, much richer but slower), Pelicun's bundled DLML defaults, or user-supplied YAML/CSV with explicit provenance. Decision affects `ImpactEnvelope.fragility_source` values, output resolution, runtime, and the per-claim citation discipline required under Decision M. Bundled defaults are likely v0.1 with an override mechanism deferred. See Appendix B.6c and §2.3 post-processing tool classes.
9. **Mesh-generation toolchain for TELEMAC-MASCARET** *(forward-looking — not blocking M1 / sprint-03; first engine target post-MVP / v0.2+; decide before Milestone M11)*: pick between GMSH (open, scriptable, mature Python bindings, general-purpose), OceanMesh2D (mature for coastal / storm-surge meshing, MATLAB-origin with a Python port), BlueKenue (NRC Canada, free, GUI-centric, weaker headless story), or in-suite Telemac preprocessors. Decision affects automation depth, Python-shim ergonomics, license posture, and runtime; same shape as OQ-8 (choose between named alternatives, decide before the milestone). See §2.3 Deferred engines (TELEMAC-MASCARET row), FR-TA-1 TELEMAC modeling workflows, and Milestone M11.
11. **Conservation / biodiversity domain SRS position** *(forward-looking — not blocking M1 / sprint-03; first member registers when the v0.2+ conservation atomic tools land)*: the conservation/biodiversity domain (Maxent species distribution, InVEST ecosystem services, Circuitscape connectivity, biomod2/ecospat) does not naturally fit the hazard-modeling framing of the rest of the SRS. Four candidate positions: **(a)** add a new `hazard_type` literal (e.g. `habitat_loss`, `connectivity_break`) so existing engine/workflow/envelope machinery applies verbatim — terse but conflates "hazard footprint" with "habitat suitability"; **(b)** introduce a parallel `analysis_type: Literal["hazard", "conservation"]` discriminator on the envelope so the conservation analyses ship their own envelope variant with distinct mandatory fields — clean separation but doubles the schema surface; **(c)** workflow-composition only: register conservation engines as Python-shim atomic tools (see FR-TA-2 conservation utilities) and let users compose them ad hoc without first-class envelope support — minimal SRS churn but limits agent-side discovery; **(d)** treat conservation as a peer post-processor family next to Pelicun (Decision N): conservation tools consume an `AssessmentEnvelope` (e.g. a flood depth raster) and emit a `ConservationImpactEnvelope` carrying species-suitability change, ecosystem-service loss, or connectivity drop — strongest fit when the conservation outputs are downstream of a hazard, weakest when the conservation question is hazard-independent. Decision affects FR-TA-1 workflow naming, Appendix B envelope variants, Appendix D `run_type` literal, FR-MP-5 row, and the §2.3 catalog layout. Same shape as OQ-8 / OQ-9 (choose between named alternatives, decide before the first conservation engine ships). *(OQ-10 reserved — multi-agent specialization timing was considered for v0.3.15 but the question was deferred to v0.2+ without a numbered slot here; if it returns it will pick up this number to preserve continuity.)*

---

## 7. Milestones

Milestones describe deliverable scope, not schedule. They are sequenced by dependency, not by calendar time. Effort estimation is intentionally omitted from this document — it varies too much with team size, parallelization, and unknowns to be useful here, and tying milestones to dates makes the SRS go stale quickly.

| Milestone | Deliverable | Depends on |
|---|---|---|
| **M1: Foundation** | GCP project, ADK skeleton, hello-world Gemini agent. MongoDB Atlas cluster provisioned with MongoDB MCP server connection verified. Basic WebSocket protocol with a web stub. | — |
| **M2: QGIS Server in cloud** | QGIS Server container running on Cloud Run, serving a sample `.qgs` with a basemap layer. PyQGIS worker prototype reads/writes a project from GCS. | M1 |
| **M3: Web client skeleton** | React app with MapLibre map displaying QGIS Server tiles. Chat panel, layer toggle, pipeline strip components. WebSocket to agent. | M1, M2 |
| **M4: First tools** | DEM fetch, layer load, MongoDB MCP queries wired into the agent. Gemini successfully calls them and returns results. QGIS algorithm discovery (Level 1a per FR-AS-9) operational. | M1, M2 |
| **M5: Hardcoded flood demo** | One specific historical event modeled end-to-end with mostly hardcoded inputs. A storm-surge workflow function. SFINCS containerized and runs as a Cloud Run Job. The `AssessmentEnvelope` produced by this milestone is the canonical input to M5.5. | M4 |
| **M5.5: Impact post-processing (Pelicun) v0** *(Forward-looking — not in M1 / not in sprint-03)* | Pelicun packaged as a Docker container and dispatched as a Cloud Run Job orchestrated by Cloud Workflows (per FR-CE-5). Fragility curves bundled from the Damage and Loss Model Library (HAZUS Flood v6.1 for the M5 demo); per-building damage states and loss summaries written as FlatGeobuf to GCS. `run_pelicun_impact` workflow consumes the M5 `AssessmentEnvelope` and emits an `ImpactEnvelope` (Appendix B.6c). AssessmentEnvelope precondition enforced (FR-CE-6); 30-second cancellation conformance verified (FR-CE-7); confirmation gating wired (FR-AS-8). | M5 |
| **M11: TELEMAC-MASCARET engine v0 (coastal / river hydrodynamics)** *(Forward-looking — not in M1 / not in sprint-03; first engine target post-MVP / v0.2+; v0.3 roadmap)* | openTELEMAC-MASCARET packaged as a Docker container (simvia/opentelemac base image, MPI-enabled binaries) and dispatched as a Cloud Run Job orchestrated by Cloud Workflows under the same pattern as SFINCS (per FR-CE-1, FR-CE-2, FR-CE-3). Suite covers TELEMAC-2D (depth-averaged shallow-water on unstructured meshes, city-scale coastal storm surge and river flooding), TELEMAC-3D (RANS shallow-water 3D with vertical stratification for estuarine compound flooding), TOMAWAC (spectral wind-wave propagation), ARTEMIS (phase-resolving harbor agitation), GAIA (unified sediment transport / morphodynamics, supersedes SISYPHE in v8p2+), and MASCARET (1D Saint-Venant river routing). Mesh-generation toolchain selected and packaged per OQ-9. One TELEMAC workflow operational end-to-end on a real event (likely `run_coastal_storm_surge_telemac`); grace-2-side Selafin I/O via HermesPy; consortium `telemac2d.py` / `telemac3d.py` / `tomawac.py` / `gaia.py` driver scripts invoked from the workflow (Python-shim pattern per §2.3); TelApy step-level API deferred until the consortium's ISO_C_BINDING migration ships in a stable release. `AssessmentEnvelope` emission conforming to Appendix B; new hazard subtype values (e.g. `coastal_storm_surge_telemac`, `river_1d_mascaret`) registered in a follow-up Appendix B.4 amendment when the workflows actually land. License posture: TELEMAC runs as a separate Cloud Run Job process; GRACE-2 itself stays MIT (NFR-L); the GPL/LGPL boundary is the Docker image, with no source linkage from grace-2 against TELEMAC or BIEF. | M5, M6, M7 |
| **M6: Generalized flood workflows** | Parameterized location, three workflows (storm surge, pluvial, fluvial) operational with arbitrary locations. User-input solicitation (FR-AS-10) integrated for cases requiring spatial input. | M5 |
| **M7: Hazard event pipeline (research mode)** | NewsAPI integration, NWS / NHC / USGS NWIS agency feeds wired up, `extract_event_metadata` with claim-set production, `aggregate_claims_across_sources` with research-mode rules. End-to-end demo: real-event prompt → claim aggregation → model run. MongoDB corpus storage and Atlas Vector Search operational. | M6 |
| **M8: Public hazard layer discovery** | Curated `public_hazard_catalog.yaml` populated with the v0.1 entries from FR-PHC-4. `show_hazard_layer` workflow operational. Discovery envelope rendering in the UI. | M3, M4 |
| **M9: Time scrubbing and pipeline UX** | WMS-T temporal layer support, time scrubber component, pipeline strip with progress and cancellation. Location auto-snap (FR-WC-12) and pick-modes (FR-WC-13/14) integrated. | M3, M6 |
| **M10: Polish and v0.1 release** | Documentation, demo videos, deployment instructions, repository hygiene (license, README, etc.). | all above |

Milestones M4, M5, M6, M7, M8 can be parallelized to a meaningful degree given multiple contributors; M3 and M9 share UI surface and may also overlap. The dependency column captures hard prerequisites only. M5.5 (impact post-processing) parallelizes with M6 and M7 once M5 lands — it consumes the M5 envelope and is independent of the generalized flood workflows and the hazard event pipeline.

---

## 8. Document History

| Version | Date | Notes |
|---|---|---|
| 0.1 | 2026-06-04 | Initial draft (custom Qt UI assumed) |
| 0.2 | 2026-06-04 | Pivoted to QGIS plugin development; tiered tool architecture; news pipeline as core feature; engine catalog with MODFLOW |
| 0.3 | 2026-06-04 | Pivoted to web architecture: Google Cloud Agent Builder + Gemini 3 + ADK; MongoDB Atlas with MCP integration; QGIS Server as rendering backend; MapLibre GL JS web client; 2D-only for v0.1; GeoAgent is reference only, no code copied; added explicit Tier A (browse) / Tier B (solver) data separation with CONUS-only initial view; OSM tiles direct as v0.1 basemap; added metadata-payload pattern codifying MongoDB-as-index / GCS-as-payload split across projects, runs, news, and events |
| 0.3.1 | 2026-06-04 | Added Appendix A: WebSocket Protocol with concrete message schemas |
| 0.3.2 | 2026-06-04 | Added Appendix B: AssessmentEnvelope Schema with hazard subtype pattern; updated FR-TA-2, FR-AS-7 to reference it |
| 0.3.3 | 2026-06-04 | Revised engine catalog (§2.3) to reflect tractability-driven selection with plugin-backed and Python-shim integration modes; added Decision J in §2.1; added FR-AS-9 for solver feasibility research as a tentative future agent capability; updated §5 framing to clarify deferrals are roadmap-staged, not categorical exclusions |
| 0.3.4 | 2026-06-04 | Restructured FR-AS-9 into a three-tier capability discovery framework; promoted Tier 1 (QGIS algorithm discovery via `list_qgis_algorithms` and `describe_qgis_algorithm`) into v0.1 scope; updated FR-QS-1 to require `qgis_process` access in the QGIS Server container; FR-TA-3 now lists the discovery tools |
| 0.3.5 | 2026-06-04 | Added Appendix C: EventMetadata Schema with discriminated intensity union; updated FR-NP-2 to reference it |
| 0.3.6 | 2026-06-04 | Added public hazard layer discovery as a peer capability to modeling: new §3.5.5 with FR-PHC requirements and a curated public hazard catalog; new Tier 1b in FR-AS-9 (and renamed Tier 2 plugin discovery to Tier 2a, added Tier 2b for open layer discovery); new Tier 2 workflow `show_hazard_layer`; new Tier 3 tools `hazard_catalog_search`, `fetch_public_hazard_layer`, `summarize_layer_in_bbox`; FR-TA-1 intent classifier now includes a discovery/modeling response mode; Appendix B updated with `envelope_type` discriminator and a worked discovery example; §1.1 purpose and §1.2 scope expanded to include discovery |
| 0.3.7 | 2026-06-04 | Added user input solicitation (FR-AS-10) and location auto-snap (FR-WC-12, FR-WC-13, FR-WC-14): new WebSocket messages `location-resolved`, `spatial-input-request`/`response`, `disambiguation-request`/`response`; new tools `request_spatial_input` and `request_disambiguation` in FR-TA-3; `EventLocation` extended with `granularity` and `precision_class` fields in Appendix C; new error codes for input timeouts and cancellations |
| 0.3.8 | 2026-06-04 | Added Appendix D: MongoDB Collection Schemas with full Pydantic models for `projects`, `runs`, `articles`, `events`, `sessions`; declared indexes including three Atlas Vector Search indexes; resolved the `session-state` TBD in Appendix A; updated FR-MP-5 to reference Appendix D |
| 0.3.9 | 2026-06-04 | Removed all user-facing cost estimation: dropped `estimated_cost_usd`/`actual_cost_usd` fields from `runs` schema (Appendix D), removed `estimated_cost_usd` from confirmation-request message (Appendix A), removed cost-threshold trigger from FR-AS-8 confirmation hooks, clarified NFR-C as infrastructure budget only. Cost surfacing deferred indefinitely until cent-precise estimation is achievable. |
| 0.3.10 | 2026-06-04 | Goal/spec alignment pass. **Tier 1 dissolved**: removed FR-TA-1 intent classifier; renamed FR-TA-2 → FR-TA-1 (Workflows), FR-TA-3 → FR-TA-2 (Atomic tools), FR-TA-4 → FR-TA-3 (Metadata discipline); intent classification is now implicit in the LLM's tool selection. **New FR-AS-11 (ambiguity handling)**: agent invokes `request_clarification` when paths are substantively different and ambiguous; new tool added to FR-TA-2; new `clarification-request`/`clarification-response` messages in Appendix A; new `CLARIFICATION_TIMEOUT` error. **New Decision K + FR-AS-12**: user supplies intent and irreducible inputs only; workflows fetch authoritative data rather than requiring user-supplied wind, weather, fuels, etc.; default-by-fetch policy applies to every workflow. **New Decision L**: `text-embedding-005` standardized as the embedding model. **FR-AS-9 renamed** Tier 1a/1b/2a/2b/3 → Level 1a/1b/2a/2b/3 to disambiguate from removed FR-TA tier framework. **Open questions cleanup**: closed OQ #5 (FlatGeobuf chosen); removed OQ #9 (migration trigger noted, not a blocker); renumbered remaining OQs. **Wording fixes**: §1.2 clarified wildfire modeling deferred but wildfire discovery in scope; §1.3 definitions updated for two-layer architecture; Decision G rewritten to drop "Three tiers" framing; inline tier references throughout document rewritten as workflows/atomic tools. |
| 0.3.11 | 2026-06-04 | Reframed news pipeline as the Hazard Event Pipeline (FR-NP → FR-HEP) covering authoritative agency feeds + news media + generic web fetch. **Two operational modes**: research mode (v0.1 default, focused source set, simple aggregation) and deep research mode (v0.2+, broader sources and convergence). **Multi-source claim aggregation** introduced via new `NumericClaim` and `ClaimSet` types in Appendix C — every numerical intensity field is now a `ClaimSet` with consensus value, method, confidence, and per-source provenance. Source-authority tiering (6 tiers from agency direct-measurement to social) defined in FR-HEP-2; used by aggregation. **New tools** added to FR-TA-2: `fetch_nws_event`, `fetch_storm_events_db`, `web_fetch`, `aggregate_claims_across_sources`. Deferred to v0.2+: `fetch_nifc_incidents`, `fetch_usgs_earthquake`, `web_fetch_browser`. **New FR-WC-15**: research/deep research mode toggle in the UI. **New Decision M**: authoritative agency feeds as first-class sources alongside news; claim aggregation with provenance as default. Updated §1.1 purpose and §1.2 scope to reflect broader event sourcing. Worked examples in Appendix C updated to show claim-set shape. |
| 0.3.12 | 2026-06-04 | Removed time/effort estimates from milestones (§7). Milestones now describe scope and dependencies, not schedule. Added an explicit M8 for public hazard layer discovery (was implicit in earlier milestones), reflecting it as a parallel track to flood-modeling work rather than blocking. |
| 0.3.13 | 2026-06-05 | Introduced impact post-processing as a forward-looking second tool class alongside engines (all additions explicitly deferred post-M5 so in-flight sprint-03 work is not disturbed). **New Decision N**: engines emit `AssessmentEnvelope`, post-processors consume one and emit an `ImpactEnvelope`. Pelicun (NHERI SimCenter, FEMA P-58 / HAZUS via the bundled Damage and Loss Model Library) is the first member. **§2.3**: post-processing tool-class contract added next to the engine common contract; new "Post-processing tool classes" table with one Pelicun row. **FR-CE-5/6/7**: Pelicun runs as a Cloud Run Job orchestrated by Cloud Workflows with an `AssessmentEnvelope` precondition enforced via MongoDB lookup and 30-second cancellation conformance (FR-AS-6 / NFR-R-3). **FR-TA-1**: return-type widened to `AssessmentEnvelope` or `ImpactEnvelope`; new impact-post-processing workflow group with `run_pelicun_impact`. **FR-AS-7 / FR-AS-8**: extended to source narrative numbers from `ImpactEnvelope` and to confirmation-gate any impact post-processing execution. **Appendix B.6c / B.6d**: full `ImpactEnvelope` Pydantic shape (sibling type with its own `envelope_type: Literal["impact"]`; `AssessmentEnvelope.envelope_type` is unchanged), supporting types, and a worked Hurricane Ian Pelicun example. B.7 design-rationale bullets extended to acknowledge the impact case. **Appendix D.3**: `RunDocument.run_type` literal extended with `"impact"`; comment clarified that "modeled"/"discovered" mirror `AssessmentEnvelope.envelope_type` and "impact" mirrors `ImpactEnvelope.envelope_type`. **FR-MP-5**: new Impact runs row co-located in the `runs` collection. **Milestones**: new M5.5 ("Impact post-processing (Pelicun) v0") inserted after M5, parallelizes with M6/M7. **OQ-8**: fragility/consequence-curve sourcing (HAZUS vs FEMA P-58 vs bundled vs user-supplied). |
| 0.3.14 | 2026-06-05 | Added openTELEMAC-MASCARET as a forward-looking multi-solver hydrodynamic engine (all additions explicitly deferred post-MVP / v0.2+ so in-flight M1 / sprint-03 work is not disturbed; same discipline as the 0.3.13 Pelicun amendment). **§2.3**: one new row in the Deferred engines table for the TELEMAC-MASCARET suite — Solver column lists openTELEMAC-MASCARET (TELEMAC-2D/3D, TOMAWAC, ARTEMIS, GAIA, MASCARET) on one line matching the cadence of surrounding rows; Likely mode is **Python shim** (HermesPy for Selafin I/O grace-2-side; consortium `telemac2d.py` / `telemac3d.py` / `tomawac.py` driver scripts invoked from the workflow), conforming to the §2.3 Engine selection principle which defines only Plugin-backed or Python shim modes; Target v0.3. Containerization and MPI-binary packaging are FR-CE-1 execution details, not an integration mode, and live in the M11 milestone description (mirroring how SFINCS is 'Python shim via HydroMT' in the v0.1 catalog despite running in a Cloud Run Job). **§2.3 substantial-new-tooling paragraph**: clarifying sentence added — mesh-based shallow-water suites with mature Python driver tooling and out-of-process invocation (TELEMAC-MASCARET) are roadmap-included and distinct from the OpenFOAM-class indefinitely-deferred set; '3D ocean simulation' in that paragraph refers to full 3D Navier–Stokes coastal CFD, not TELEMAC-3D's RANS shallow-water 3D mode. **FR-TA-1**: new forward-looking TELEMAC modeling-workflow group appended after `model_news_event` (i.e. inside the `envelope_type: "modeled"` subsection, preserving envelope_type grouping modeled → discovered → impact) with `run_coastal_storm_surge_telemac` (TELEMAC-2D unstructured-mesh higher-fidelity complement to SFINCS), `run_coupled_surge_wave` (TELEMAC-2D + TOMAWAC), `run_river_hydraulics_mascaret` (1D Saint-Venant), `run_sediment_transport_gaia` (GAIA on a prior TELEMAC `solver_run_id`); engine common contract preserved (workflow + atomic-tool registration only, no engine-core changes). **Milestones**: new M11 ("TELEMAC-MASCARET engine v0 (coastal / river hydrodynamics)") inserted after M5.5 to cluster the two forward-looking engine/post-processor rows together (mirrors the M5.5 half-step convention) — M10 (Polish and v0.1 release; dependency 'all above') remains the last row of the v0.1 milestone block; M11 is a parallel-track v0.3 deliverable depending on M5/M6/M7; deliverable includes container packaging (simvia/opentelemac base image, MPI-enabled binaries) under FR-CE-1/2/3, mesh-toolchain selection per OQ-9, one end-to-end TELEMAC workflow on a real event, hazard subtype registration deferred to a follow-up Appendix B.4 amendment when the workflows actually land. **OQ-9**: mesh-generation toolchain selection (GMSH vs OceanMesh2D vs BlueKenue vs in-suite Telemac preprocessors), mirroring OQ-8's named-alternatives shape. **License posture**: TELEMAC is GPL v3 (main modules) + LGPL v3 (BIEF); GRACE-2 stays MIT (NFR-L) because the integration is out-of-process — TELEMAC binaries inside a separate Docker image, invoked as a separate Cloud Run Job, communicating only via GCS file artifacts and Cloud Workflows step transitions, with no grace-2 source linked against TELEMAC or BIEF. **Known follow-ups (not in this amendment)**: when TELEMAC workflows actually land, Appendix B.4 hazard subtypes will gain entries like `coastal_storm_surge_telemac` / `coupled_surge_wave_telemac` / `river_1d_mascaret` / `sediment_gaia`; FR-HEP-7 storm-surge forcing reconstruction will need a solver-agnostic phrasing or a TELEMAC sibling entry; the §5 Out-of-Scope row referencing '3D ocean simulation' is consistent with the §2.3 clarification (TELEMAC excluded from that row by construction). |
| 0.3.21 | 2026-06-07 | Forward-looking **FR-MP-6 Case UX** amendment (per user direction 2026-06-07 — "Add this to the SRS and we can discuss refining the idea when we get to it"). "Case" is the user-facing name for a `projects` document (FR-MP-5). Pins the binding UX flow: (a) **landing** = two-pane shell, Cases list left + Chat right; left panel hidden until first Case exists; both panels collapsible for max-map view. (b) **Case creation** = first agent prompt in a session outside any Case implicitly creates a `projects` document and binds the session. (c) **In-Case state** = left panel switches from Cases list to Case detail (loaded layers list with visibility/opacity/order per the layer-emission-contract decision); "back to Cases" nav element exposed. (d) **Persistence** = layer-producing workflow outputs save into the Case's `layer_summary` (sprint-09's `publish_layer` tool persists at exit); chat history persists into the bound `sessions` document. (e) **Resume** = re-opening a Case rehydrates chat + re-registers layers + restores agent context. (f) **Out-of-Case context** = navigating back to Cases list resets chat to a fresh agent context with no prior conversation memory; UI clearly reflects the context change. Implementation refinement deferred to the target sprint; the contract above pins intent only. UI label "Case" ↔ schema name "Project" (FR-MP-5 nomenclature stays canonical). No schema change in this amendment; consumers of FR-MP-6 will propose D.x amendments at implementation time. |
| 0.3.22 | 2026-06-08 | New **Appendix I: LLM Tool Harness Conventions** (per user direction 2026-06-08 — "we can now tighten the harness"). Codifies five conventions binding from sprint-12-mega Wave 4.7 forward (job-0164 engine sweep + job-0165 this appendix) for every `@register_tool`-registered atomic tool (FR-TA-2) and workflow exposure (FR-TA-1). **§I.1 Param naming**: full unabbreviated English words (`return_period_years`, `duration_hours`); the two v0.1 abbreviation suffixes `_yr` / `_hr` retain bounded backward-compat aliases (`return_period_yr` / `duration_hr` as `int \| None = None`, normalized at top of body) until v0.2; new tools ship with full-word names from the first commit. **§I.2 `**_extra_ignored` absorb policy**: every registered function ends with `**_extra_ignored: Any` so frontier-LLM-invented kwargs (`run_name`, `scenario_id`, `description`, `mode`, …) do not raise `TypeError`; absorb is *input*-side noise tolerance — `extra="forbid"` on schema models (Appendices A–D) unchanged; Invariants 1 / 7 / 10 preserved. **§I.3 Docstring discipline**: dedicated `Examples:` doctest block at end of every tool docstring; no inline `key="value"` substrings in prose `Use this when:` / `Do NOT use this for:` / `Params:` sections (Gemini-3 re-emits inline-prose key=value substrings as call arguments — empirically observed root cause of `forcing=` / `rainfall_event=` failures). Extends FR-AS-3 / FR-TA-3 docstring discipline (does not amend either FR). **§I.4 Normalization layer**: NEW `services/agent/src/grace2_agent/tool_arg_normalizer.py` (sibling job-0164 in code) with `normalize_args(tool_name, raw_args) → (normalized, dropped_unknowns)`; wired into `server.py:_invoke_tool_via_emitter` before `entry.fn(**params)`; performs alias map (tool-name-keyed) + bounded fuzzy match (camelCase→snake_case, edit distance ≤ 1, single-candidate only) + per-tool string-form parser (e.g. `forcing="atlas14_100yr"` → `return_period_years=100`; reference impl in `run_model_flood_scenario` body) + drop-and-log unknown keys. Never raises on unknown kwargs; never silently fabricates values; per-tool counters emitted to structured logs. **§I.5 Per-tool tests**: cross-cutting parametrized conformance test (every registered tool accepts invented kwargs without `TypeError`; docstring conforms to §I.3 sectioning; signature ends with `**_extra_ignored`) + property-based fuzz on the normalizer with 0–5 random unknown keys per call. Owned by `testing`; CI-gating on PRs touching `services/agent/src/grace2_agent/{tools,workflows}/*.py` or the normalizer. **§I.6 Decision F (harness conventions) — adopted 2026-06-08**: records the adoption with empirical-signal rationale (strict signature binding was the #1 pre-application-logic failure across ~57 atomic tools in sprint-12-mega) and alternatives considered (strict + re-prompt; pydantic-model-per-tool; normalizer without `**_extra_ignored`). Forward path pins v0.2 alias retirement (`_yr`/`_hr` aliases removed; full-word names sole accepted form). **Cross-references**: §2.1 Decision F (harness conventions row — appendix I is the body); FR-AS-3 + FR-TA-3 extended by §I.3; Invariants 1 / 7 / 10 preserved verbatim; AGENTS.md pre-MVP "no legacy support" honored (the §I.1 alias is the only bounded backward-compat layer); engine sweep job-0164 is the implementation companion; this appendix is the conventions surface. **Files touched**: NEW `docs/srs/I-llm-tool-harness.md`; `docs/srs/INDEX.md` Appendix I row added; `Makefile` `SRS_PARTS` list extended. No schema changes (Appendices A–D unchanged); no FR amendments (FR-AS-3 / FR-TA-3 extended-not-amended via the convention layer). |
| 0.3.20 | 2026-06-07 | Focused housekeeping pass — reconciles 3 critical carry-forwards before sprint-08 catalog work begins (deferred items remain for a later pass). **§F.1 WorldPop prose alignment** (OQ-37-*): live ecosystem delivered neither STAC nor 100m product (WorldPop Hub STAC returns 404; 4 GB 100m server returns HTTP 200 not 206 for Range so /vsicurl/ windowed reads fail) — prose now correctly states Tier 4 region-download with 50 MB 1km Aggregated COG per country via direct REST; vintage default `worldpop_2020`; units **people-per-1km-cell** semantics clarified for downstream zonal-stats consumers (OQ-37-WORLDPOP-COG-CRS-AND-UNITS). **§F.1 NLCD prose alignment** (OQ-39-NLCD-TIER-DEVIATION + OQ-42-NLCD-WMS-PALETTE-ENCODING + OQ-44 fix): NLCD via MRLC is **Tier 2 OGC WCS 1.0.0 GetCoverage for canonical class integers** (model inputs), not Tier 3 direct HTTPS as v0.3.16 implied. WMS GetMap returns palette-encoded indices (silent-wrong-answer mode caught live by Invariant 7 gate in job-0042) — WMS retained as the visualization-only path; WCS is the canonical-bytes path. **§3.9 FR-DC-1 bucket layout clarification** (OQ-INFRA-31-FR-DC-1): per-TTL-class prefix nesting (`cache/<ttl-class>/<source-class>/<hash>.<ext>`) per job-0031 live substrate is the actual on-disk layout — scales past GCS 100-rule cap that per-source rules would burn through. Appendix B worked-example paths kept as aliases; cache shim normalizes. **§3.9 FR-DC-5 live-no-cache lifecycle clarification** (OQ-INFRA-31-LIVE-NO-CACHE-LIFECYCLE-NOOP): the `cache/live-no-cache/` lifecycle rule is intentionally a no-op (GCS rejects `daysSinceCustomTime=0`); acceptable because FR-DC-6 enumerates these tools as uncacheable-by-construction; rule exists as defense-in-depth, not load-bearing enforcement. **Deferred to a later pass** (intentionally NOT bundled here to keep this amendment focused): OQ-W-26 TTL-literal naming reconciliation; OQ-33-GEOCODED-LOCATION-CONTRACT-PROMOTION (sprint-08 schema scope); OQ-35-WIRE-PAYLOAD-ERROR-FIELDS-VISIBILITY; OQ-36-CACHE-REGRESSION-FAKE-FIDELITY; OQ-41-COMPUTE-CLASS-NAMING (sprint-08 schema scope); OQ-42-* + OQ-43-* + OQ-44-MANNING-MAPPING-CSV-COMMENT-WMS-REF (smaller code-side fixes). Sprint-08 catalog seed (job-0046) reads this updated prose for canonical "how to use" metadata. |
| 0.3.19 | 2026-06-07 | New §3.10 Failure Recovery (FR-FR) — deny/retry/chat gate substrate + max-turns cap + explicit deferrals for the larger subsystem buildouts. Per user direction 2026-06-07 ("keep amendments short so we aren't detoured too much; defer larger sub system buildouts that are nice to have until we have a working demo"). **Status note** documents what v0.1 already has (Invariant 8 cancel chain at 850 ms verified job-0041; hard solver timeouts; fail-fast typed upstream errors per FR-AS-11 + FR-DC-3; Invariant 7 validation gates fail-closed — verified LIVE in production job-0042 NLCD palette case). **FR-FR-1**: deny/retry/chat recovery-choice envelope substrate — when atomic tool fails with a recoverable error class, agent surfaces a 3-button modal (mirrors §F.3 popup discipline — out of band of chat envelope per Decision F): Deny (record decision, mark failed, narrate honestly), Retry (re-attempt; cache shim discipline still applies), Chat (focused single-line input becomes user-message payload to nudge agent). NEW Appendix A envelopes `recovery-choice` + `recovery-choice-response` (schema lands at next schema sprint slot). **FR-FR-2**: per-error-code routing table classifies failures into transient-upstream (gate), recoverable-with-context (gate, chat path high-value), substrate-integrity / FAIL CLOSED (no gate — LULC_MAPPING_MISMATCH, SCHEMA_VALIDATION_FAILED, IAM_PERMISSION_DENIED, etc.; honest narration only), user-initiated (no gate), cost/budget (no gate, halt session). **FR-FR-3**: agent-side `MAX_TURNS_PER_SESSION` cap — cheap insurance against runaway LLM tool-call chains, TENTATIVE default 25 (OQ-FR-1); single config line in services/agent main.py + new `session-state.status="max_turns_reached"` enum value. **Targeted landing: sprint-08 small task.** **FR-FR-4** (DEFERRED): per-session token budget — internal accounting fails closed before Vertex AI tokens run away; separate from Invariant 9 no-cost-theater discipline (which forbids surfacing dollar estimates). Out of v0.1. **FR-FR-5** (DEFERRED INDEFINITELY): multi-agent pre-verifier — planner / executor / verifier subsystem that catches "faceplant" failures before user involvement; mitigates the bulk of the failure modes the deny/retry/chat gate exists for. Slot at M6+ post-MVP or whenever production review surfaces it as the bottleneck. Per user direction: "we will implement multi agent system that will hopefully mitigate this faceplant type of problem before the user needs to get involved we should slot a minimal version and then later add the multi agent/user guided workflow." **FR-FR-6** (DEFERRED): scientific output verification (cross-check flood depth against historical observations / FEMA NFHL overlays / USGS gauge records during modeled event); post-M5 milestone-level work given research-grade nature. **6 OQ-FR-* surfaced**, none v0.1-blocking. v0.3.17+ housekeeping carry-forward pile NOT bundled — explicit user direction to keep this amendment short. Sprint-07 close pass remains the planned housekeeping landing point. |
| 0.3.18 | 2026-06-07 | New §F.1.2 Trust model for source discovery — three-mode framing that resolves OQ-AT-2 by rescoping it. **Mode 1 — Catalog-mediated (PRIMARY, v0.2+ sprint-08 binding):** curated `public_data_source_catalog.yaml` (and successor MongoDB `catalog_entries` collection) is the single source of truth for vetted endpoints. Each entry is research-driven + labeled at curator time with id/name/description/url(s)/access_tier (per §F.1.1)/ttl_class/source_class/credential_tier (per §F.1)/license/citation/vintage/last_verified/status + "how to use" metadata (invocation examples + parameter constraints + known quirks — e.g. "WorldPop returns 200 not 206 for Range requests; use region-download tier; specify country in params.iso3"). New atomic tools `catalog_search(topic, location?, source_filter?) → list[CatalogEntry]` + `catalog_fetch(entry_id, params) → LayerURI | dict` (generic dispatcher over the entry's access_tier, routes through FR-DC cache shim). Existing hardcoded tools (`fetch_dem`/`fetch_landcover`/`fetch_population`/`geocode_location`) coexist as friendly per-domain shortcuts for canonical sources; catalog covers the long tail. **Mode 2 — Offer-to-add on `.gov` and `.edu` (v0.2+, narrow growth path):** when agent encounters a candidate URL not in catalog AND on `.gov`/`.edu`, it does NOT autonomously fetch — instead performs a conformity probe (HEAD + STAC root + OGC GetCapabilities + COG headers + TLS cert org), emits a new `offer-catalog-addition` envelope (Appendix A amendment for sprint-08) with probe findings + suggested entry shape; client renders a dedicated review modal (mirrors §F.3 popup pattern; out of band of chat envelope per Decision F discipline); user accepts/rejects/edits; on accept the entry lands with `status: "user_proposed_pending_curator_review"` until curator review flips to `active`. Bounded growth path with mandatory user surfacing and audit log (`catalog_audit_log` MongoDB collection per Decision F). Why `.gov` + `.edu`: registry-controlled TLD policing (DotGov / EDUCAUSE) bounds the autonomous-probing surface; conformity probe + cross-confirmation against existing catalog catches false positives (press releases vs structured data, deprecated endpoints). **Mode 3 — Anything else (DEFERRED INDEFINITELY per user direction 2026-06-07):** non-.gov/non-.edu URLs (general .com, .org, country TLDs, IPs) shall NOT be probed and shall NOT trigger offer-to-add; agent narrates "candidate found at <url>; doesn't meet v0.1 trustworthiness criteria — review manually via curator CLI if appropriate." Revisit when Decision M provenance discipline + §F.3 user-identity + cross-confirmation signal mature. **4-axis trustworthiness model** = curator-side validation criteria for Mode 1 hand-curated AND Mode 2 user-accepted adds before status flips to `active`: domain provenance (TLD + cert org subject), protocol conformity (matches a known geospatial standard), metadata sufficiency (declared license + citation + vintage), cross-confirmation (referenced by existing catalog + SRS + vetted aggregator like NASA CMR / Microsoft Planetary Computer / USGS ScienceBase). All 4 ideal; ≥2 of 4 + curator override = minimum bar. **SSRF guardrails (infra-side, NFR-S; binding all modes):** Cloud Run VPC connector egress allowlist + private-IP block (10/8, 172.16/12, 192.168/16, 127/8, 169.254.169.254 GCE metadata) + DNS rebinding defense (re-resolve at fetch; fail closed on mismatch) + max response size (100 MB probe / 4 GB cataloged Tier-4) + per-domain rate limit + audit log. **OQ-AT-2 closed by rescoping**; **new OQ-AT-3** captures the wider Mode 3 trustworthiness question (deferred indefinitely). **Sprint-08 scope:** Mode 1 (catalog substrate + Sonnet-driven 30–60-entry seed + atomic tools + generic Tier-2 OGC adapter + SSRF guardrails) is headline; Mode 2 (offer-to-add) is fast-follow within sprint-08 if scope permits, sprint-09 otherwise. v0.3.17+ housekeeping carry-forwards (OQ-W-26, OQ-INFRA-31-FR-DC-1, OQ-INFRA-31-LIVE-NO-CACHE-LIFECYCLE-NOOP, OQ-33-GEOCODED-LOCATION-CONTRACT-PROMOTION, OQ-35-WIRE-PAYLOAD-ERROR-FIELDS-VISIBILITY, OQ-36-CACHE-REGRESSION-FAKE-FIDELITY, OQ-37-* WorldPop prose alignment) intentionally NOT bundled — planned sprint-07 close pass. |
| 0.3.17 | 2026-06-07 | New §F.1.1 Access-pattern tiering — the "data stores in the wild" problem. Articulates the orthogonal axis to §F.1 credential tiering: every data-fetch atomic tool is implemented against one of four access patterns chosen at implementation time after live verification of the upstream provider. Tier 1 = STAC catalog with COG backend + HTTP Range (byte-window, single-stage cache key). Tier 2 = OGC service WMS/WMTS/WCS/WFS (query-rendered, layer reference IS the URL, cache shim bypassed — the §F.2 Discovery-First lane lives here). Tier 3 = direct HTTPS file with HTTP Range support (byte-window via `/vsicurl/`, single-stage cache). Tier 4 = region/country download + local clip (full-file fetch then windowed clip, two-stage cache per OQ-37-COUNTRY-FILE-CACHING-STRATEGY — country file at `cache/<ttl-class>/<source>/_regions/<region>.<ext>` shared across all clips inside that region). Tier choice is engineer-curated at implementation time; runtime fallback between tiers DEFERRED INDEFINITELY (OQ-AT-1). **Forward-looking — Agent-mediated data-source discovery and adaptation (v0.2+):** the broader principle is that a hazard-modeling agent encounters data wherever it lives across arbitrary provider conventions; the architecture must accommodate that variety, not assume a uniform interface. The v0.2+ capability (OQ-AT-2) mirrors the FR-AS-9 solver-discovery pattern applied to data sources: agent encounters a previously-uncatalogued source via user query or web research, probes its access pattern (Accept-Ranges? STAC root? OGC GetCapabilities?), records the discovered tier in a dynamic source registry, and constructs a one-shot fetch routed through the same cache shim. Requires agent-side autonomous probing discipline (timeout + cancellation), dynamic tool registration (current `@register_tool` is import-time), a MongoDB dynamic-source-registry collection (new per Decision F), SSRF guardrails (NFR-S concern: agent-initiated outbound to user-controlled URLs), and user-facing provenance surfacing (Decision M). All deferred to v0.2+. **Forward-looking schema note:** `AtomicToolMetadata.access_tier` field bump deferred; for v0.1 the tier is documentation-discipline per FR-AS-3 / FR-TA-3 docstrings only. OQ-AT-1 (runtime fallback) + OQ-AT-2 (agent-mediated discovery) added as the §F.1.1 open questions; both forward-looking, both same shape as OQ-8 / OQ-9 / OQ-11 (decide before capability ships). Surfacing rationale: post-job-0037 (sprint-07 Stage A) live-verification discoveries — the WorldPop v0.3.16 §F.1 prose implied STAC + Range-readable; the live ecosystem delivered neither (no STAC catalog exists; HTTP server returns 200 not 206 for Range requests). The architectural lesson is broader than WorldPop — the agent's data-source-handling capability must be flexible across the variety it will actually encounter. v0.3.17 codifies that principle while explicitly deferring the agent-mediated discovery implementation. Carry-forward v0.3.17+ housekeeping pile (OQ-W-26 TTL-literal naming, OQ-INFRA-31-FR-DC-1 bucket layout, OQ-INFRA-31-LIVE-NO-CACHE-LIFECYCLE-NOOP, OQ-33-GEOCODED-LOCATION-CONTRACT-PROMOTION, OQ-35-WIRE-PAYLOAD-ERROR-FIELDS-VISIBILITY, OQ-36-CACHE-REGRESSION-FAKE-FIDELITY, plus new OQ-37-* and the §F.1 WorldPop-prose alignment) intentionally NOT bundled here — kept for the planned sprint-07-close housekeeping pass. |
| 0.3.16 | 2026-06-06 | New Appendix F (Data-Source Tiering + Discovery-First Lane + Deferred Secrets UX). **§F.1 Data-source tiering**: three tiers by credential requirement (Tier 1 key-free public — 3DEP, NLCD, ESA WorldCover, NHDPlus HR, NOAA Atlas 14, WorldPop, MS Open Maps, OSM, NHC ATCF, CO-OPS, NEXRAD, MRMS, NWIS, FEMA NFHL, USFS WHP, USDA WRC; Tier 2 key-required free — US Census ACS, NewsAPI, api.weather.gov keyed, Earthdata; Tier 3 paid commercial — Mapbox, PRISM 800m, premium news). Tier-1-preference rule for atomic-tool defaults: when more than one source can answer, prefer Tier 1; Tier-2 / Tier-3 opt-in via explicit parameter. **WorldPop is the new `fetch_population` Tier-1 default** (reverses job-0033's prior ACS-as-default preference per OQ-36-CENSUS-API-KEY-REQUIRED — the no-friction default better serves new users; ACS opt-in remains for tract-level precision). **§F.2 Discovery-First lane**: formalizes the routing rule that catalog-driven `show_hazard_layer` is preferred over solver Modeling when a curated catalog entry exists for the user query; lists the 8 Tier-1 catalog candidates (FEMA NFHL, USFS WHP, USDA WRC, NOAA SLOSH, USGS NSHM, NOAA Storm Events DB, USGS Earthquake Catalog, NOAA CDO); the `hazard_catalog_search` / `fetch_public_hazard_layer` / `summarize_layer_in_bbox` atomic tools (already FR-TA-2 scoped, M4-deferred) recommended for landing alongside M5 / sprint-07 SFINCS or as a fast-follow mini-sprint. **§F.3 Deferred Secrets UX**: documents the forward-looking `request_secret` atomic tool + `secret-request` / `secret-response` envelope amendments + dedicated pop-up modal (NOT inline chat input — wire-level isolation: secret never transits the chat envelope to MongoDB per Decision F) + per-user Secret Manager namespacing + Cloud Function secret-receiver out-of-band of the WebSocket. **DEFERRED INDEFINITELY** pending explicit user direction; requires M6+ user-identity machinery as prerequisite. Until §F.3 lands, Tier-2 keys are deployment-scope (operator-provisioned via OpenTofu + Secret Manager). Surfacing rationale: post-sprint-06 demo discovered Census ACS now requires an API key (OQ-36-CENSUS-API-KEY-REQUIRED); this appendix formalizes the deferred / immediate paths so the design question doesn't get reinvented later. Appendix E layout from v0.3.15 already added; Appendix F joins the INDEX + Makefile SRS_PARTS. Outstanding v0.3.16+ housekeeping carries (OQ-W-26 TTL-literal naming, OQ-INFRA-31-FR-DC-1 bucket layout, OQ-INFRA-31-LIVE-NO-CACHE-LIFECYCLE-NOOP, OQ-33-GEOCODED-LOCATION-CONTRACT-PROMOTION, OQ-35-WIRE-PAYLOAD-ERROR-FIELDS-VISIBILITY, OQ-36-CACHE-REGRESSION-FAKE-FIDELITY) intentionally NOT bundled here — kept for a focused housekeeping pass. |
| 0.3.15 | 2026-06-06 | Forward-looking expansion synthesizing data-fetching / engines / plugins / conservation / caching research into the SRS (all additions deferred or scoped for M4+ so in-flight M1–M3 work is undisturbed; same discipline as the 0.3.13 Pelicun and 0.3.14 TELEMAC amendments). **New Decision O** (§2.1): cache-mediated atomic-tool data fetching with four TTL classes (`static-30d` / `semi-static-7d` / `dynamic-1h` / `live-no-cache`), content-addressed keys derived from `source_id + canonicalized_params + ttl_bucket_vintage`, GCS-backed shared cache bucket at `gs://<bucket>/cache/<source-class>/<hash>.<ext>` matching the Appendix B convention, GCS Object Lifecycle Management eviction; binding from M4 forward. **New §3.9 Data Caching (FR-DC-1 through FR-DC-6)**: bucket layout (FR-DC-1), four TTL classes with per-call response-metadata overrides (FR-DC-2), read-through / write-on-miss semantics with content-addressed key derivation and shim-as-sole-writer (FR-DC-3), deduplication guarantee across sessions / users / invocation order (FR-DC-4), lifecycle eviction tied to `customTime` (FR-DC-5), and the uncacheable-by-construction enumeration covering interactive solicitation tools, envelope emitters, MongoDB MCP writes, solver dispatchers, and explicit `cache=false` overrides (FR-DC-6). **New FR-CE-8**: every atomic tool that issues a network call to an external public data source shall route through the shared cache shim; the cache class is a required property validated at tool-registration time. **§2.3 Deferred engines table**: **8 new engine rows** — pywatershed (v0.2 distributed hydrology — USGS PRMS reimplementation), ParFlow (v0.3 integrated surface–subsurface hydrology), EPA SWMM via PySWMM (v0.2 urban drainage), HEC-HMS (v0.2 lumped/semi-distributed rainfall-runoff), OpenWFM / WRF-SFIRE (experimental coupled atmosphere–fire), QUIC-Fire + FastFuels + pyretechnics + duet-tools fuels-to-fire stack (v0.3 wildfire spread + fuels), PyTorchFire (experimental GPU/differentiable wildfire), wrf-python (v0.2 WRF diagnostics). **§3.3 FR-TA-2 deferred atomic-tool utilities — two new categories**: hydro-conditioning / forcing-prep utilities (`pysheds_condition_dem` GPLv3+ out-of-process, `wrfxpy_prepare_forcing` MIT) explicitly NOT engines per the §2.3 Engine selection principle (both self-declared as gating/forcing-prep, not workflow engines that produce georeferenced solver output); and conservation / biodiversity utilities (`run_maxent_sdm`, `run_invest_ecosystem_service`, `run_circuitscape_connectivity`) provisionally placed in the post-processing tool-class layer pending OQ-11 resolution. **OQ-5 closed**: marked inline as closed by Decision O with cross-reference to §3.9 / FR-CE-8; original numbering retained for historical traceability per the v0.3.10 dual-lineage convention (rather than renumber OQ-6/7/8/9). **New OQ-11**: conservation / biodiversity domain SRS position — four candidate positions (new `hazard_type` literal, parallel `analysis_type` discriminator, workflow-composition only, peer post-processor family next to Pelicun) with explicit trade-offs. OQ-10 reserved with explanatory parenthetical (multi-agent specialization timing was considered for v0.3.15 but the question itself was deferred per user direction; if it returns it picks up the OQ-10 slot to preserve continuity). **New Appendix E**: forward-looking QGIS plugins inventory cataloguing the plugin-side counterparts of the deferred engines (Q4TS for TELEMAC pre/post, QGIS-HMS for HEC-HMS, FREEWAT / QSWATMOD / iMOD / APEXMOD for MODFLOW, THYRSIS for hydrogeology, QSSI / Animove / Maxent-QGIS / LinkScape / Circuitscape-QGIS / lecos for conservation/movement ecology) with integration-mode hints (in-image bake at QGIS Server build time vs out-of-process invocation via Python-shim Cloud Run Jobs vs daemon delegation for plugins backed by running services). Appendix E is added to the Makefile `srs` target's `SRS_PARTS` list and to `docs/srs/INDEX.md` so the regenerated monolith includes it. **License posture (unchanged)**: GRACE-2 stays MIT (NFR-L) for every addition above; GPLv3+ libraries (pysheds, OpenWFM, QUIC-Fire) and LGPL libraries (ParFlow) are invoked out-of-process via Python-shim Cloud Run Jobs, no in-tree linkage. **Decision P (multi-agent specialization migration path)** was considered but **DROPPED per user direction 2026-06-06** — defer to v0.2+ when single-agent topology actually hinders; the corresponding OQ-10 was also dropped. **Verdict-fix lineage**: this amendment incorporates the 9 verdict fixes from the first v0.3.15 workflow run's adversarial-verify (workflow `wodu3a4xm`) — §3.9 complete-not-truncated, OQ-5 explicit closure, OQ-11 added, §8 row added, engine count reconciled to 8 (after moving pysheds + wrfxpy out per the verdict's "not an engine" finding), pysheds + wrfxpy reclassified, bucket-naming harmonized to Appendix B convention, Appendix E created (not just referenced), and the conservation closing paragraph softened to "provisionally placed pending OQ-11 resolution". |

---

## Appendix A: WebSocket Protocol

> **Status: preemptive.** This appendix is a working specification drafted before implementation. Concrete schemas, message types, field names, and conventions are subject to revision once implementation surfaces real constraints (ADK behavior, Gemini streaming semantics, MapLibre client needs, MongoDB MCP responses, etc.). Treat as the starting point, not the contract — changes flow back into this appendix as they're learned.

### A.1 Envelope

All messages share a common envelope. JSON-encoded over a single WebSocket connection per session.

```typescript
{
  type: string,         // discriminator, kebab-case (see A.3, A.4)
  id: string,           // ULID, unique per message
  ts: string,           // ISO 8601 UTC timestamp when sent
  session_id: string,   // current session ULID
  payload: object       // type-specific fields
}
```

**Conventions:**
- `type` uses kebab-case (e.g., `tool-call-start`, not `tool_call_start` or `toolCallStart`)
- `id` is a ULID (sortable by time, URL-safe, 26 characters)
- `ts` is ISO 8601 with `Z` suffix (UTC)
- `session_id` is required on every message; absent or mismatched session IDs cause the connection to close with an auth error
- `payload` is always an object, even when empty (`{}`)

### A.2 Encoding and transport

- WebSocket over WSS (TLS 1.2+) in production; WS allowed for local dev
- Text frames carrying JSON; binary frames are not used in v0.1
- One message per frame; messages are not chunked across frames
- Maximum message size: 1 MB (large payloads use storage URIs instead)

### A.3 Client → Agent messages

#### `user-message`
User-submitted text input.

```json
{
  "type": "user-message",
  "payload": {
    "text": "Model the flooding from Hurricane Ian in Fort Myers"
  }
}
```

#### `cancel`
Cancel the in-flight pipeline.

```json
{
  "type": "cancel",
  "payload": {
    "reason": "user-requested"
  }
}
```

`reason` is optional. The agent acknowledges via a `pipeline-state` message reflecting cancelled steps.

#### `confirm-response`
User response to a `confirmation-request`.

```json
{
  "type": "confirm-response",
  "payload": {
    "request_id": "01HX...",
    "approved": true
  }
}
```

#### `session-resume`
Resume an existing session by ID.

```json
{
  "type": "session-resume",
  "payload": {}
}
```

The session ID is in the envelope. The agent responds with a `session-state` message.

### A.4 Agent → Client messages

#### `agent-message-chunk`
A streamed token (or token group) from the LLM.

```json
{
  "type": "agent-message-chunk",
  "payload": {
    "message_id": "01HX...",
    "delta": "Fort Myers ",
    "done": false
  }
}
```

- `message_id` groups chunks belonging to one logical message
- `delta` is the new content since the last chunk (not accumulated)
- `done: true` indicates the message is complete; no further chunks for this `message_id`

#### `tool-call-start`
A tool invocation has begun.

```json
{
  "type": "tool-call-start",
  "payload": {
    "call_id": "01HX...",
    "step_id": "01HX...",
    "tool_name": "fetch_dem",
    "tool_category": "data-fetch",
    "params": { /* sanitized parameters */ }
  }
}
```

`params` may be sanitized to omit sensitive fields. The client uses `call_id` to correlate with later `tool-call-progress`, `tool-call-complete`, and `tool-call-failed` messages.

#### `tool-call-progress`
Optional progress update for an in-flight tool call.

```json
{
  "type": "tool-call-progress",
  "payload": {
    "call_id": "01HX...",
    "percent": 47,
    "status": "Downloading DEM tile 3/8"
  }
}
```

Either `percent` (0-100 integer), `status` (string), or both. Tools opt into emitting progress; not every tool emits.

#### `tool-call-complete`
A tool finished successfully.

```json
{
  "type": "tool-call-complete",
  "payload": {
    "call_id": "01HX...",
    "result_summary": "Fetched DEM: 487 MB, 10m resolution, 2.4 km²",
    "result_uri": "gs://bucket/path/dem_abc123.tif",
    "metrics": { /* tool-specific fields */ }
  }
}
```

- `result_summary` is a human-readable one-liner for chat display
- `result_uri` is optional; present when the result is a stored artifact
- `metrics` is tool-specific structured data (e.g., flood depth tool returns `{ max_depth_m, flooded_area_km2 }`)

Full result bodies are not transmitted; they live in GCS or MongoDB and are referenced by URI.

#### `tool-call-failed`
A tool errored out.

```json
{
  "type": "tool-call-failed",
  "payload": {
    "call_id": "01HX...",
    "error_code": "DEM_SOURCE_UNAVAILABLE",
    "message": "USGS 3DEP returned 503; retry suggested",
    "retryable": true
  }
}
```

- `error_code` is an enum-like string (defined per tool category in a future appendix)
- `message` is human-readable, surfaced in chat
- `retryable: true` indicates the agent may automatically retry; the user may also be offered manual retry

#### `pipeline-state`
Full snapshot of the current pipeline. Emitted on any state change.

```json
{
  "type": "pipeline-state",
  "payload": {
    "pipeline_id": "01HX...",
    "steps": [
      {
        "step_id": "01HX...",
        "name": "Geocode location",
        "tool_name": "geocode_location",
        "state": "complete",
        "started_at": "2026-06-04T20:14:01Z",
        "completed_at": "2026-06-04T20:14:02Z"
      },
      {
        "step_id": "01HX...",
        "name": "Fetch DEM",
        "tool_name": "fetch_dem",
        "state": "running",
        "started_at": "2026-06-04T20:14:02Z",
        "progress_percent": 47
      }
    ]
  }
}
```

`state` values: `pending`, `running`, `complete`, `failed`, `cancelled`.

The full snapshot replaces the client's pipeline view on each message. Deltas are not used.

#### `map-command`
Instruct the client to modify the map. One umbrella type with a `command` discriminator inside `payload`.

```json
{
  "type": "map-command",
  "payload": {
    "command": "load-layer",
    "args": { /* command-specific */ }
  }
}
```

v0.1 commands:

| `command` | `args` |
|---|---|
| `load-layer` | `{ layer_id, wms_url, style_preset, temporal?: { start, end, step_seconds } }` |
| `remove-layer` | `{ layer_id }` |
| `set-layer-visibility` | `{ layer_id, visible: boolean }` |
| `set-layer-opacity` | `{ layer_id, opacity: 0..1 }` |
| `set-layer-order` | `{ layer_ids: string[] }` (ordered, top to bottom) |
| `zoom-to` | `{ bbox: [minLon, minLat, maxLon, maxLat] }` |
| `set-temporal-config` | `{ layer_id, start, end, step_seconds, current? }` |
| `start-animation` | `{ layer_id, speed?: 0.5\|1\|2\|5\|10 }` |
| `stop-animation` | `{ layer_id }` |
| `invalidate-tiles` | `{ layer_id? }` (omit `layer_id` to invalidate all) |

#### `confirmation-request`
The agent needs user approval before proceeding.

```json
{
  "type": "confirmation-request",
  "payload": {
    "request_id": "01HX...",
    "title": "Run SFINCS simulation",
    "description": "Will run a flood simulation on a 180 km² domain. Estimated runtime ~8 minutes.",
    "estimated_duration_seconds": 480,
    "default_timeout_seconds": 60
  }
}
```

Cost figures are intentionally omitted from confirmation requests until the system can produce cent-level accurate estimates. Surfacing approximate or potentially-wrong cost numbers to users is worse than not showing any.

If no `confirm-response` arrives within `default_timeout_seconds`, the agent shall treat the request as denied and proceed accordingly (e.g., cancel the pending operation).

#### `session-state`
Sent automatically on connection and on `session-resume`. Lets the client reconstruct the session.

```json
{
  "type": "session-state",
  "payload": {
    "chat_history": [ /* list[ChatMessage] — see Appendix D.6 */ ],
    "loaded_layers": [ /* list[ProjectLayerSummary] — see Appendix D.2 */ ],
    "pipeline_history": [ /* list[PipelineSnapshot] — see Appendix D.6 */ ],
    "current_pipeline": null,
    "map_view": { /* MapView — see Appendix D.6 */ }
  }
}
```

The exact schemas of `chat_history`, `loaded_layers`, `pipeline_history`, and `map_view` are defined in **Appendix D.6** (the `sessions` collection schema). The wire form is the JSON serialization of those Pydantic models.

#### `error`
Global error not tied to a specific tool call (auth, rate limit, internal).

```json
{
  "type": "error",
  "payload": {
    "error_code": "RATE_LIMITED",
    "message": "Too many requests. Retry in 30s.",
    "retryable": true,
    "retry_after_seconds": 30
  }
}
```

#### `location-resolved`
Emitted whenever the agent identifies a meaningful location during a query — extracted from news, parsed from a user prompt, returned from geocoding, or selected via disambiguation. The client auto-snaps the map to the resolved bbox to give the user spatial context without manual navigation.

```json
{
  "type": "location-resolved",
  "payload": {
    "resolved_id": "01HX...",
    "label": "Fort Myers, Florida",
    "bbox": [-82.10, 26.40, -81.60, 26.90],
    "granularity": "city",
    "source": "news_extraction",
    "animate": true
  }
}
```

- `resolved_id`: unique per resolution; clients deduplicate by ID within a session
- `label`: human-readable, displayed as a subtle "Showing: ..." overlay
- `bbox`: target extent in EPSG:4326
- `granularity`: `country | region | state | city | facility | bbox`; drives client-side padding rules
- `source`: `news_extraction | user_prompt | disambiguation | geocoding | tool_result`
- `animate`: `true` for smooth `flyTo`, `false` for instant jump (used for rapid sequences)

Client behavior:
- Apply padding based on `granularity` (country ~10%, state to fill ~60% of viewport, city +20%, facility ~5km radius)
- Animate using MapLibre `flyTo({essential: true, duration: 1500})` with `prefers-reduced-motion` respected
- Display a dismissible "Showing: {label}" overlay
- Suppress re-snap to the same `resolved_id` if the user has manually navigated since the last snap (don't fight the user)
- Suppress redundant duplicate snaps to recently-resolved IDs within a 30-second window

#### `spatial-input-request`
Agent needs the user to specify a spatial geometry (a point or a bbox) before continuing. The map switches into pick-mode for the duration of the request.

```json
{
  "type": "spatial-input-request",
  "payload": {
    "request_id": "01HX...",
    "mode": "point",
    "title": "Where exactly is the factory?",
    "description": "The article mentions a chemical spill at a factory near the Mississippi River in Cancer Alley, Louisiana, but doesn't name the facility. Drop a pin at the spill site so the model can use the right location.",
    "suggested_view": {
      "bbox": [-91.3, 30.2, -90.5, 30.8],
      "zoom": 11
    },
    "reference_layers": [
      {
        "layer_id": "epa_facilities",
        "wms_url": "https://qgis-server/wms?MAP=ref&LAYERS=epa_facilities",
        "style_preset": "facilities-points"
      }
    ],
    "default_timeout_seconds": 300
  }
}
```

- `mode`: `point` or `bbox` (polygon mode is deferred to a later version)
- `suggested_view`: where the client zooms the map to make the picking easier
- `reference_layers`: optional helper layers shown only during this request (e.g., facility locations to help the user find a candidate)
- `default_timeout_seconds`: if no response arrives in time, the agent treats it as cancelled (300s default; spatial picks take time)

If no `spatial-input-response` arrives within `default_timeout_seconds`, the agent treats the request as cancelled and aborts the pending operation.

#### `disambiguation-request`
Agent has multiple plausible candidates for an extracted entity (typically a location) and needs the user to pick one. Distinct from `spatial-input-request` because the candidates are already enumerated.

```json
{
  "type": "disambiguation-request",
  "payload": {
    "request_id": "01HX...",
    "title": "Which Springfield?",
    "description": "The article mentions 'Springfield' but there are several. Pick one.",
    "candidates": [
      {
        "id": "springfield-il",
        "label": "Springfield, Illinois",
        "bbox": [-89.78, 39.70, -89.55, 39.85],
        "context": "Capital of Illinois"
      },
      {
        "id": "springfield-mo",
        "label": "Springfield, Missouri",
        "bbox": [-93.42, 37.10, -93.18, 37.30],
        "context": "Largest city in southwest Missouri"
      }
    ],
    "default_timeout_seconds": 120
  }
}
```

The client may render candidates as a list, as markers on the map at each candidate's bbox center, or both. Selection sends `disambiguation-response`. Timeout: 120s default (faster decision than spatial picking).

#### `clarification-request`
Agent needs the user to choose between substantively different response paths and can't infer the right one from context. Distinct from `disambiguation-request` because the options aren't a list of equivalent candidates — they're different *paths* the agent could take. See FR-AS-11.

```json
{
  "type": "clarification-request",
  "payload": {
    "request_id": "01HX...",
    "question": "Are you looking for existing wildfire risk maps in Washington, or do you want to simulate a specific fire scenario?",
    "options": [
      {
        "id": "discovery",
        "label": "Show existing risk maps",
        "description": "Display USFS wildfire hazard potential and USDA risk-to-communities layers for the area."
      },
      {
        "id": "modeling",
        "label": "Simulate a fire",
        "description": "Run a wildfire spread simulation. I'll need an ignition point and weather inputs."
      }
    ],
    "default_timeout_seconds": 60
  }
}
```

Options are 2-4 substantively different paths. The `description` field is required (not optional) — it shows the user what each path will produce. Timeout: 60s default (typical path choice).

#### `recovery-choice`
*(sprint-08 amendment, FR-FR-1 — landed by job-0045-schema-20260607.)*

Agent emits when an atomic-tool step fails with a *recoverable* error class (per FR-FR-2 routing). The web client renders a small out-of-chat modal (mirrors §F.3 popup discipline) offering deny / retry / chat actions. Substrate-integrity / user-initiated / budget-overrun error codes fail closed without gating.

```json
{
  "type": "recovery-choice",
  "payload": {
    "request_id": "01HX...",
    "failed_step_id": "01HX...",
    "error_code": "UPSTREAM_API_ERROR",
    "error_message": "USGS 3DEP returned HTTP 503 — service unavailable",
    "context": "fetching DEM at Fort Myers bbox for flood scenario",
    "options": ["deny", "retry", "chat"],
    "ttl_seconds": 300
  }
}
```

- `error_code` is `SCREAMING_SNAKE_CASE` per §A.6 (open set; shape-validated)
- `error_message` and `context` are each capped at 512 chars
- `options` is a non-empty subset of `["deny", "retry", "chat"]`; the routing table per FR-FR-2 may narrow it (e.g. omit `"retry"` for `GEOCODE_NO_MATCH` where retry is futile)
- `ttl_seconds` defaults to 300; on expiry the gate becomes a typed failure

#### `offer-catalog-addition`
*(sprint-08 amendment, §F.1.2 Mode 2 — landed by job-0045-schema-20260607.)*

Agent encountered a candidate `.gov` / `.edu` URL during research or user-query interpretation, performed a conformity probe per §F.1.2 Mode 2, and is offering to add it to the catalog. The web client renders a dedicated review modal (popup, focus-trapped, separate from chat envelope) showing the URL + probe findings + the suggested catalog entry. User accepts, rejects, or edits before accepting.

```json
{
  "type": "offer-catalog-addition",
  "payload": {
    "request_id": "01HX...",
    "url": "https://hazards.fema.gov/nfhlv2/services/public/NFHL/MapServer/WFSServer",
    "discovered_via": "user-query",
    "probe_findings": {
      "tls_cert_org": "U.S. Department of Homeland Security",
      "access_tier_inferred": 2,
      "supports_range_requests": false,
      "stac_root_found": false,
      "ogc_capabilities_found": true,
      "license_observed": "Public domain (US Federal)",
      "content_type": "application/xml",
      "last_modified_header": "Wed, 01 Jun 2026 12:00:00 GMT"
    },
    "suggested_catalog_entry": {
      "id": "femanflp-discharge-stations",
      "name": "FEMA NFHL discharge stations",
      "description": "Discharge stations from the FEMA NFHL WFS feed.",
      "urls": ["https://hazards.fema.gov/nfhlv2/services/public/NFHL/MapServer/WFSServer"],
      "access_tier": 2,
      "credential_tier": 1,
      "ttl_class": "semi-static-7d",
      "source_class": "flood_zone",
      "license_claim": "Public domain (US Federal)",
      "how_to_use": "OGC WFS GetFeature; bbox in EPSG:4326; layer NFHL:DischargeStations"
    },
    "ttl_seconds": 600
  }
}
```

- `discovered_via` is a closed `Literal`: `"user-query"` / `"web-research"` / `"catalog-cross-reference"` / `"other"`
- `probe_findings` sub-fields are all optional (probe may not be able to determine every axis)
- `suggested_catalog_entry` is a permissive draft (fields the agent could infer from the probe); the user may edit any field; agent service round-trips an accepted draft through the full `CatalogEntry` model before writing to `catalog_entries` (D.11)
- `license_claim` (not `license`) signals the probe's *observation* vs. the curator-attested value
- `ttl_seconds` defaults to 600 (review modals get more time than retry gates because the user is reading + sanity-checking provenance)

### A.4b Client → Agent (user input responses)

#### `spatial-input-response`
User has picked a geometry in response to a `spatial-input-request`.

```json
{
  "type": "spatial-input-response",
  "payload": {
    "request_id": "01HX...",
    "geometry_type": "point",
    "coordinates": [-91.087, 30.435]
  }
}
```

For `mode: point`, `coordinates` is `[lon, lat]`. For `mode: bbox`, `coordinates` is `[minLon, minLat, maxLon, maxLat]`.

The user may also send a cancellation in place of a geometry:

```json
{
  "type": "spatial-input-response",
  "payload": {
    "request_id": "01HX...",
    "cancelled": true
  }
}
```

The agent then aborts the pending operation gracefully.

#### `disambiguation-response`
User has chosen a candidate.

```json
{
  "type": "disambiguation-response",
  "payload": {
    "request_id": "01HX...",
    "candidate_id": "springfield-mo"
  }
}
```

Cancellation is the same pattern: `cancelled: true` instead of `candidate_id`.

#### `clarification-response`
User has chosen one of the clarification options.

```json
{
  "type": "clarification-response",
  "payload": {
    "request_id": "01HX...",
    "option_id": "discovery"
  }
}
```

Cancellation: `cancelled: true` instead of `option_id`. The agent then aborts the pending operation.

#### `recovery-choice-response`
*(sprint-08 amendment, FR-FR-1 — landed by job-0045-schema-20260607.)*

User has picked one of the three actions OR cancelled the modal.

```json
{
  "type": "recovery-choice-response",
  "payload": {
    "request_id": "01HX...",
    "choice": "chat",
    "chat_text": "try the WCS endpoint instead of WMS"
  }
}
```

- `choice` is `"deny"` / `"retry"` / `"chat"` or `null` when cancelled
- `chat_text` is populated only when `choice == "chat"`; carries the focused single-line nudge the user typed; capped at 4096 chars
- Cancellation: `cancelled: true` instead of `choice` (mirrors the existing A.4b response shapes)

#### `catalog-addition-response`
*(sprint-08 amendment, §F.1.2 Mode 2 — landed by job-0045-schema-20260607.)*

User has accepted / rejected the offered catalog addition. On accept, the agent writes the entry to `catalog_entries` (D.11) with `status: "user_proposed_pending_curator_review"` and logs to `catalog_audit_log` (D.12) with `event_type: "user_proposed"`. Reject events are also audited.

```json
{
  "type": "catalog-addition-response",
  "payload": {
    "request_id": "01HX...",
    "decision": "accept",
    "edited_catalog_entry": {
      "id": "femanflp-discharge-stations",
      "name": "FEMA NFHL Discharge Stations (curator-edited)",
      "urls": ["https://hazards.fema.gov/nfhlv2/services/public/NFHL/MapServer/WFSServer"],
      "access_tier": 2,
      "credential_tier": 1,
      "ttl_class": "semi-static-7d",
      "source_class": "flood_zone",
      "license_claim": "Public domain (US Federal)",
      "how_to_use": "OGC WFS GetFeature; bbox in EPSG:4326; layer NFHL:DischargeStations"
    }
  }
}
```

- `decision` is `"accept"` / `"reject"` or `null` when cancelled
- `edited_catalog_entry` (same permissive shape as the offer's `suggested_catalog_entry`) is populated only when the user edited any field; when None on accept the agent writes the original `suggested_catalog_entry`
- `reject_reason` is populated only when `decision == "reject"`; optional; capped at 512 chars
- Cancellation: `cancelled: true` instead of `decision`

### A.5 Connection lifecycle

1. **Connect**: client opens WSS connection to the agent's endpoint with a session token (cookie or query parameter; auth scheme defined in a future round)
2. **Authenticate**: agent validates the session; on failure, closes with code 4401 (unauthorized)
3. **Initial state**: agent sends `session-state` with current context
4. **Active**: client and agent exchange messages per the schemas above
5. **Disconnect**: either side may close the connection; agent persists session state to MongoDB on close
6. **Reconnect / resume**: client reconnects with the same session token; receives a fresh `session-state`; in-flight pipelines continue uninterrupted

### A.6 Error codes (initial)

Codes use `SCREAMING_SNAKE_CASE`. The list will grow as tools and failure modes are added.

| Code | Meaning |
|---|---|
| `AUTH_FAILED` | Session token invalid or expired |
| `RATE_LIMITED` | Client exceeded request rate limit |
| `INTERNAL_ERROR` | Unexpected agent-side failure |
| `LLM_UNAVAILABLE` | Gemini API failed or rate-limited |
| `TOOL_NOT_FOUND` | LLM called a tool that doesn't exist |
| `TOOL_PARAMS_INVALID` | LLM called a tool with invalid parameters |
| `TOOL_TIMEOUT` | Tool exceeded its time budget |
| `DEM_SOURCE_UNAVAILABLE` | DEM data source returned an error |
| `SOLVER_FAILED` | Solver container exited non-zero |
| `CONFIRMATION_TIMEOUT` | User did not respond to confirmation in time |
| `SPATIAL_INPUT_TIMEOUT` | User did not respond to a spatial-input-request in time |
| `DISAMBIGUATION_TIMEOUT` | User did not respond to a disambiguation-request in time |
| `CLARIFICATION_TIMEOUT` | User did not respond to a clarification-request in time |
| `USER_INPUT_CANCELLED` | User explicitly cancelled a spatial input, disambiguation, or clarification request |
| `CANCELLED` | Operation was cancelled by user |

### A.7 Design rationale

- **Discriminated envelope, single connection**: simpler than multiple channels; the `type` discriminator handles routing client-side
- **Streaming deltas, not accumulated text**: matches Gemini's native streaming output; lets the client render incrementally without recomputing
- **Snapshot pipeline state, not deltas**: pipelines are small (typically 5-15 steps); replace-not-reconcile is simpler client-side and avoids ordering bugs
- **Full results stay in storage; messages carry summaries**: keeps message sizes bounded regardless of result size; aligns with the metadata-payload pattern (§3.7)
- **`map-command` as one type with internal discriminator**: ten near-identical map operations as sibling top-level types would create churn; a single type with `command` inside is cleaner
- **Confirmation has a default timeout**: avoids zombie sessions if the user closes the tab mid-confirmation
- **ULIDs everywhere**: time-sortable, URL-safe, no central coordination; better than UUIDs for log correlation
- **Location auto-snap as side-effect of resolution, not explicit tool calls**: `location-resolved` is emitted from inside resolution tools, not invoked separately by the LLM. The map follows the agent's understanding without the LLM having to think about navigation.
- **Spatial input and disambiguation as distinct messages**: different UX patterns — "draw something" vs "pick from a list" — deserve distinct types rather than overloading one
- **Reference layers in spatial input requests**: helping the user find the right pin location is a key UX win; the agent passes through reference data without it being a separate map-command

---

## Appendix B: AssessmentEnvelope Schema

> **Status: preemptive.** This appendix is a working specification drafted before implementation. Concrete schemas, field names, types, and conventions are subject to revision once implementation surfaces real constraints (SFINCS output structure, ADK serialization behavior, MongoDB MCP query patterns, etc.). Treat as the starting point, not the contract — changes flow back into this appendix as they're learned.

### B.1 Purpose

The `AssessmentEnvelope` is the system's central output structure — what every hazard engine produces, what the agent's narrative reads from, what gets persisted in the `runs` collection, what feeds the UI's layer loading. A single, consistent shape across in-memory (Pydantic), wire (JSON over WebSocket), and storage (MongoDB documents).

### B.2 Top-level structure

```python
class AssessmentEnvelope(BaseModel):
    schema_version: Literal["v1"] = "v1"

    # Identity
    envelope_id: str                  # ULID
    project_id: str                   # ULID, links to projects collection
    session_id: str                   # ULID, links to sessions collection

    # Mode discriminator
    envelope_type: Literal["modeled", "discovered"]
    # "modeled": produced by a solver-backed workflow; solver_run_ids populated
    # "discovered": produced by show_hazard_layer; solver_run_ids is empty; metrics
    #               come from summarize_layer_in_bbox over existing public data

    # Classification
    hazard_type: Literal["flood", "groundwater", "wildfire", "seismic", "spill"]
    workflow_name: str                # e.g., "run_storm_surge_flood", "show_hazard_layer"

    # Spatial and temporal extent
    bbox: tuple[float, float, float, float]   # [minLon, minLat, maxLon, maxLat]
    crs: str = "EPSG:4326"                    # bbox CRS; always 4326 in v0.1
    time_range: TimeRange | None              # event time; None for synthetic / discovery

    # Forcing summary (modeled only; None for discovered)
    forcing: ForcingSummary | None

    # Catalog reference (discovered only; None for modeled)
    catalog_entries: list[CatalogReference] | None

    # Outputs
    layers: list[ResultLayer]                 # all renderable result layers
    metrics: BaseMetrics                      # empty base; subtype payloads carry real metrics

    # Provenance
    provenance: Provenance

    # Lifecycle
    created_at: datetime
    completed_at: datetime
    solver_run_ids: list[str]                 # ULIDs of solver runs; empty list for discovered

    # Subtype payloads (discriminator: hazard_type)
    flood: FloodPayload | None = None
    groundwater: GroundwaterPayload | None = None  # v0.2+
    wildfire: WildfirePayload | None = None         # v0.2+
    seismic: SeismicPayload | None = None           # v0.3+
    spill: SpillPayload | None = None               # v0.3+
```

For a given envelope, exactly one subtype field is populated; the rest are `None`. The populated one matches `hazard_type`. The `envelope_type` field is independent of `hazard_type` and indicates how the envelope was produced (modeling vs discovery).

### B.3 Supporting types

```python
class TimeRange(BaseModel):
    start: datetime                   # UTC
    end: datetime                     # UTC

class ForcingSummary(BaseModel):
    forcing_type: Literal[
        "storm_surge",
        "pluvial_synthetic",
        "fluvial_synthetic",
        "news_derived",
        "user_supplied"
    ]
    source: str                       # human-readable, e.g., "NHC ATCF, Hurricane Ian"
    parameters: dict                  # forcing-specific; validated per workflow
    inputs_uri: str | None            # GCS URI to forcing data file, if any

class ResultLayer(BaseModel):
    layer_id: str                     # stable ID; used in map-command messages
    name: str                         # human-readable display name
    layer_type: Literal["raster", "vector"]
    uri: str                          # gs://... canonical location
    style_preset: str                 # references the QML preset library
    temporal: TemporalConfig | None   # present iff layer is time-varying
    role: Literal["primary", "context", "input"]
    units: str | None                 # e.g., "meters", "m/s", or None for categorical

class TemporalConfig(BaseModel):
    start: datetime
    end: datetime
    step_seconds: int

class DataSource(BaseModel):
    name: str                         # e.g., "USGS 3DEP"
    uri: str                          # the actual data file used
    accessed_at: datetime

class Provenance(BaseModel):
    data_sources: list[DataSource]
    article_ids: list[str]            # MongoDB article IDs, if news-derived
    event_id: str | None              # MongoDB event ID, if news-derived

class CatalogReference(BaseModel):
    catalog_entry_id: str             # references public_hazard_catalog.yaml entry
    title: str                        # denormalized for narrative use
    agency: str                       # denormalized for narrative use
    access_url: str                   # the URL fetched for this layer
    license: str                      # license text or URL

class BaseMetrics(BaseModel):
    pass                              # subtype payloads carry the real fields
```

### B.4 Flood subtype (v0.1)

```python
class FloodPayload(BaseModel):
    metrics: FloodMetrics

class FloodMetrics(BaseMetrics):
    # Spatial extent of impact
    flooded_area_km2: float

    # Depth statistics, computed over flooded cells only
    max_depth_m: float
    mean_depth_m: float
    p95_depth_m: float                # 95th percentile

    # Velocity, if the run computed it
    max_velocity_m_s: float | None

    # Affected assets, optional based on which fetchers ran
    affected_buildings_count: int | None
    affected_buildings_by_depth: dict[str, int] | None
    # e.g., {"0-0.5m": 412, "0.5-1m": 251, "1-2m": 132, "2m+": 52}

    affected_critical_facilities: list[CriticalFacility] | None
    population_exposed: int | None

    # Solver provenance
    solver_version: str               # e.g., "sfincs-v2.0.4"
    grid_resolution_m: float
    simulation_duration_hours: int

class CriticalFacility(BaseModel):
    name: str
    category: Literal["school", "hospital", "fire_station", "police", "other"]
    coordinates: tuple[float, float]  # [lon, lat], EPSG:4326
    max_depth_m: float
```

Future hazard subtypes (`GroundwaterPayload`, `WildfirePayload`, etc.) follow the same pattern: a payload type with a metrics field, hazard-specific.

### B.5 Wire form

`AssessmentEnvelope.model_dump(mode="json")` produces the canonical wire form. Same shape across:
- Workflow function return values
- `tool-call-complete.metrics` field in the WebSocket protocol (Appendix A)
- `runs.assessment` field in the MongoDB `runs` collection (schema in a later round)
- Context provided to the LLM when generating narrative

### B.6 Example: modeled flood envelope (v0.1)

```json
{
  "schema_version": "v1",
  "envelope_id": "01HX...",
  "project_id": "01HX...",
  "session_id": "01HX...",
  "envelope_type": "modeled",
  "hazard_type": "flood",
  "workflow_name": "run_storm_surge_flood",
  "bbox": [-82.10, 26.40, -81.60, 26.90],
  "crs": "EPSG:4326",
  "time_range": {
    "start": "2022-09-28T00:00:00Z",
    "end": "2022-09-30T00:00:00Z"
  },
  "forcing": {
    "forcing_type": "storm_surge",
    "source": "NHC ATCF, Hurricane Ian (AL092022)",
    "parameters": {
      "storm_id": "AL092022",
      "max_winds_kt": 140,
      "saffir_simpson": 4
    },
    "inputs_uri": "gs://bucket/forcings/al092022_track.fgb"
  },
  "catalog_entries": null,
  "layers": [
    {
      "layer_id": "max_depth",
      "name": "Maximum flood depth",
      "layer_type": "raster",
      "uri": "gs://bucket/runs/01HX.../max_depth.tif",
      "style_preset": "depth-blue",
      "temporal": null,
      "role": "primary",
      "units": "meters"
    },
    {
      "layer_id": "depth_temporal",
      "name": "Flood depth over time",
      "layer_type": "raster",
      "uri": "gs://bucket/runs/01HX.../depth_temporal.tif",
      "style_preset": "depth-blue",
      "temporal": {
        "start": "2022-09-28T00:00:00Z",
        "end": "2022-09-30T00:00:00Z",
        "step_seconds": 3600
      },
      "role": "primary",
      "units": "meters"
    },
    {
      "layer_id": "affected_buildings",
      "name": "Affected buildings",
      "layer_type": "vector",
      "uri": "gs://bucket/runs/01HX.../affected_buildings.fgb",
      "style_preset": "buildings-graduated",
      "temporal": null,
      "role": "primary",
      "units": null
    }
  ],
  "metrics": {},
  "provenance": {
    "data_sources": [
      {
        "name": "USGS 3DEP",
        "uri": "gs://bucket/cache/dem/3dep_10m_<hash>.tif",
        "accessed_at": "2026-06-04T20:14:01Z"
      },
      {
        "name": "NHC ATCF",
        "uri": "gs://bucket/forcings/al092022_track.fgb",
        "accessed_at": "2026-06-04T20:14:05Z"
      },
      {
        "name": "Microsoft Building Footprints",
        "uri": "gs://bucket/cache/buildings/ms_<hash>.fgb",
        "accessed_at": "2026-06-04T20:14:08Z"
      }
    ],
    "article_ids": ["01HX...", "01HX..."],
    "event_id": "01HX..."
  },
  "created_at": "2026-06-04T20:14:00Z",
  "completed_at": "2026-06-04T20:22:38Z",
  "solver_run_ids": ["01HX..."],
  "flood": {
    "metrics": {
      "flooded_area_km2": 12.4,
      "max_depth_m": 4.2,
      "mean_depth_m": 0.6,
      "p95_depth_m": 2.1,
      "max_velocity_m_s": 2.8,
      "affected_buildings_count": 847,
      "affected_buildings_by_depth": {
        "0-0.5m": 412,
        "0.5-1m": 251,
        "1-2m": 132,
        "2m+": 52
      },
      "affected_critical_facilities": [
        {
          "name": "Lee Memorial Hospital",
          "category": "hospital",
          "coordinates": [-81.87, 26.65],
          "max_depth_m": 0.4
        }
      ],
      "population_exposed": 11200,
      "solver_version": "sfincs-v2.0.4",
      "grid_resolution_m": 10.0,
      "simulation_duration_hours": 48
    }
  }
}
```

### B.6b Example: discovered wildfire envelope (v0.1)

A discovery envelope produced by `show_hazard_layer("wildfire", "Washington state")`. Note `envelope_type: "discovered"`, empty `solver_run_ids`, `forcing: null`, populated `catalog_entries`, and metrics derived from spatial summary rather than simulation.

```json
{
  "schema_version": "v1",
  "envelope_id": "01HX...",
  "project_id": "01HX...",
  "session_id": "01HX...",
  "envelope_type": "discovered",
  "hazard_type": "wildfire",
  "workflow_name": "show_hazard_layer",
  "bbox": [-124.85, 45.54, -116.92, 49.00],
  "crs": "EPSG:4326",
  "time_range": null,
  "forcing": null,
  "catalog_entries": [
    {
      "catalog_entry_id": "usfs-wildfire-hazard-potential",
      "title": "Wildfire Hazard Potential",
      "agency": "USFS",
      "access_url": "https://wildfire.cr.usgs.gov/.../whp.tif",
      "license": "Public domain (US Government work)"
    }
  ],
  "layers": [
    {
      "layer_id": "whp_washington",
      "name": "Wildfire Hazard Potential",
      "layer_type": "raster",
      "uri": "gs://bucket/discoveries/01HX.../whp_wa_clip.tif",
      "style_preset": "wildfire-hazard-potential",
      "temporal": null,
      "role": "primary",
      "units": null
    }
  ],
  "metrics": {},
  "provenance": {
    "data_sources": [
      {
        "name": "USFS Wildfire Hazard Potential",
        "uri": "gs://bucket/discoveries/01HX.../whp_wa_clip.tif",
        "accessed_at": "2026-06-04T20:14:01Z"
      }
    ],
    "article_ids": [],
    "event_id": null
  },
  "created_at": "2026-06-04T20:14:00Z",
  "completed_at": "2026-06-04T20:14:08Z",
  "solver_run_ids": [],
  "wildfire": {
    "discovery_summary": {
      "total_area_km2": 184850.0,
      "area_by_class_km2": {
        "very_low": 42100.0,
        "low": 58200.0,
        "moderate": 47350.0,
        "high": 26400.0,
        "very_high": 10800.0
      },
      "high_or_very_high_pct": 0.201
    }
  }
}
```

Note: `wildfire` subtype payload is defined for v0.2 but the discovery envelope's summary fields are simple enough to define earlier as a `DiscoverySummary`-style payload. Exact subtype schema for discovery-derived wildfire data is to be finalized when the wildfire engine lands; for v0.1 the discovery payload is a permissive `dict` validated at the workflow layer.

### B.6c ImpactEnvelope (post-processing)

> **(Forward-looking — not in M1 / not in sprint-03; first member (Pelicun) targeted post-M5, see Milestone M5.5.)**

The `ImpactEnvelope` is a sibling structure to `AssessmentEnvelope`, produced by the impact post-processing tool class (Decision N). It shares the envelope plumbing — `schema_version`, `project_id`, `session_id`, `bbox`, `crs`, `time_range`, `layers`, `provenance`, lifecycle fields — and adds fields that describe the upstream envelope it was derived from, the fragility/consequence library it used, and the building-level metrics it produced. On `ImpactEnvelope`, `envelope_type` takes the literal value `"impact"` as a parallel discriminator on this sibling type — readers that switch on `envelope_type` get a third arm rather than a new top-level type to dispatch on. `AssessmentEnvelope.envelope_type` (`Literal["modeled", "discovered"]`) is **not** modified by this amendment; the two literal sets are unioned only at the call site of any reader that handles both envelope types.

`hazard_type` is inherited from the parent `AssessmentEnvelope` (an impact envelope derived from a flood run carries `hazard_type: "flood"`); no new `"impact"` hazard-type value is introduced.

```python
class ImpactEnvelope(BaseModel):
    schema_version: Literal["v1"] = "v1"

    # Identity
    envelope_id: str                       # ULID
    project_id: str                        # ULID
    session_id: str                        # ULID
    envelope_uri: str                      # canonical URI for citation by narrative

    # Mode discriminator (parallel to AssessmentEnvelope.envelope_type;
    # readers handling both types union the literals at the call site)
    envelope_type: Literal["impact"] = "impact"

    # Lineage (binds damage/loss claims to the upstream hazard footprint)
    parent_envelope_id: str                # ULID of the source AssessmentEnvelope
    source_envelope_uri: str               # URI of the source AssessmentEnvelope
    parent_solver_run_ids: list[str]       # ULIDs of solver runs that produced the parent

    # Classification (inherited from parent)
    hazard_type: Literal["flood", "groundwater", "wildfire", "seismic", "spill"]
    workflow_name: str                     # e.g., "run_pelicun_impact"
    tool_name: Literal["pelicun"]          # extensible as new post-processors land
    tool_version: str                      # e.g., "3.9.0"

    # Spatial and temporal extent (typically copied from the parent envelope)
    bbox: tuple[float, float, float, float]
    crs: str = "EPSG:4326"
    time_range: TimeRange | None

    # Inputs to the impact run
    asset_inventory_ref: str               # URI or content hash of building/asset inventory
    hazard_intensity_measure: HazardIntensityMeasure
    monte_carlo_samples: int               # e.g., 10_000

    # Fragility / consequence provenance (per OQ-8 and Decision M citation discipline)
    fragility_source: Literal[
        "hazus_eq", "hazus_hu", "hazus_fl",
        "fema_p58", "bundled", "user_supplied"
    ]
    fragility_provenance: FragilityProvenance

    # Outputs (renderable layers — e.g., per-building damage states as FlatGeobuf)
    layers: list[ResultLayer]

    # Structured metrics — every number the narrative cites lives here
    impact: ImpactPayload

    # Provenance
    provenance: Provenance

    # Lifecycle
    created_at: datetime
    completed_at: datetime
    compute_duration_s: float              # informs cancellation-budget classification
```

**Supporting types:**

```python
class HazardIntensityMeasure(BaseModel):
    kind: Literal[
        "flood_depth_m",
        "pga_g",
        "sa_t1_g",
        "peak_drift_ratio",
        "floor_acceleration_g",
        "wind_3s_gust_mph"
    ]
    sampling_method: str                   # how the IM was sampled from the parent envelope

class FragilityProvenance(BaseModel):
    library: Literal[
        "HAZUS_EQ", "HAZUS_HU", "HAZUS_FL",
        "FEMA_P58", "USER"
    ]
    library_version: str                   # e.g., "HAZUS_FL_v6.1", "FEMA_P58_2nd"
    dlml_commit: str | None                # Damage and Loss Model Library commit hash, if bundled
    notes: str | None

class ImpactPayload(BaseModel):
    metrics: ImpactMetrics

class ImpactMetrics(BaseMetrics):
    # Damage state distribution (HAZUS DS0..DS4, or per-component for P-58)
    damage_state_distribution: dict[str, DamageStateStats]

    # Loss metrics (USD)
    repair_cost_usd: DistributionStats
    repair_cost_ratio: DistributionStats           # fraction of replacement cost

    # Downtime (days)
    repair_time_days: DistributionStats

    # Casualties (counts of people, by HAZUS severity 1-4)
    injuries_by_severity: dict[Literal["sev1", "sev2", "sev3", "sev4"], DistributionStats]
    fatalities: DistributionStats

    # Building-level safety indicators (dimensionless probabilities)
    collapse_probability: float
    unsafe_placard_probability: float

    # Run configuration
    pelicun_version: str                   # denormalized from tool_version for narrative use

class DamageStateStats(BaseModel):
    realization_count: int
    probability_mass: float                # dimensionless [0, 1]

class DistributionStats(BaseModel):
    mean: float
    median: float
    p10: float
    p50: float
    p90: float
```

`tool_name` is currently a `Literal["pelicun"]`; it widens as new post-processors are added (e.g., regional resilience indices, business-interruption tools).

### B.6d Example: Hurricane Ian Pelicun ImpactEnvelope (forward-looking — not in M1 / not in sprint-03)

Derived from the modeled flood envelope in B.6 (Hurricane Ian storm surge over Fort Myers). Note `envelope_type: "impact"`, `parent_envelope_id` pointing at the source `AssessmentEnvelope`, `hazard_type: "flood"` inherited from the parent, and `fragility_source: "hazus_fl"` for HAZUS Flood v6.1.

```json
{
  "schema_version": "v1",
  "envelope_id": "01HY...",
  "project_id": "01HX...",
  "session_id": "01HX...",
  "envelope_uri": "gs://bucket/impacts/01HY.../envelope.json",
  "envelope_type": "impact",
  "parent_envelope_id": "01HX...",
  "source_envelope_uri": "gs://bucket/runs/01HX.../envelope.json",
  "parent_solver_run_ids": ["01HX..."],
  "hazard_type": "flood",
  "workflow_name": "run_pelicun_impact",
  "tool_name": "pelicun",
  "tool_version": "3.9.0",
  "bbox": [-82.10, 26.40, -81.60, 26.90],
  "crs": "EPSG:4326",
  "time_range": {
    "start": "2022-09-28T00:00:00Z",
    "end": "2022-09-30T00:00:00Z"
  },
  "asset_inventory_ref": "gs://bucket/cache/buildings/ms_<hash>.fgb",
  "hazard_intensity_measure": {
    "kind": "flood_depth_m",
    "sampling_method": "per-building point sample from max_depth raster"
  },
  "monte_carlo_samples": 10000,
  "fragility_source": "hazus_fl",
  "fragility_provenance": {
    "library": "HAZUS_FL",
    "library_version": "HAZUS_FL_v6.1",
    "dlml_commit": "a1b2c3d",
    "notes": "bundled DLML defaults; no user override"
  },
  "layers": [
    {
      "layer_id": "building_damage_states",
      "name": "Per-building damage state (most likely)",
      "layer_type": "vector",
      "uri": "gs://bucket/impacts/01HY.../building_ds.fgb",
      "style_preset": "damage-state-graduated",
      "temporal": null,
      "role": "primary",
      "units": null
    }
  ],
  "impact": {
    "metrics": {
      "damage_state_distribution": {
        "DS0": {"realization_count": 2410, "probability_mass": 0.241},
        "DS1": {"realization_count": 3120, "probability_mass": 0.312},
        "DS2": {"realization_count": 2180, "probability_mass": 0.218},
        "DS3": {"realization_count": 1490, "probability_mass": 0.149},
        "DS4": {"realization_count": 800,  "probability_mass": 0.080}
      },
      "repair_cost_usd": {
        "mean": 184500000.0, "median": 172000000.0,
        "p10": 121000000.0, "p50": 172000000.0, "p90": 268000000.0
      },
      "repair_cost_ratio": {
        "mean": 0.27, "median": 0.24, "p10": 0.14, "p50": 0.24, "p90": 0.42
      },
      "repair_time_days": {
        "mean": 142.0, "median": 118.0, "p10": 60.0, "p50": 118.0, "p90": 260.0
      },
      "injuries_by_severity": {
        "sev1": {"mean": 84.0, "median": 76.0, "p10": 41.0, "p50": 76.0, "p90": 140.0},
        "sev2": {"mean": 22.0, "median": 19.0, "p10": 9.0,  "p50": 19.0, "p90": 41.0},
        "sev3": {"mean": 7.0,  "median": 6.0,  "p10": 2.0,  "p50": 6.0,  "p90": 14.0},
        "sev4": {"mean": 3.0,  "median": 2.0,  "p10": 0.0,  "p50": 2.0,  "p90": 7.0}
      },
      "fatalities": {
        "mean": 3.0, "median": 2.0, "p10": 0.0, "p50": 2.0, "p90": 7.0
      },
      "collapse_probability": 0.018,
      "unsafe_placard_probability": 0.229,
      "pelicun_version": "3.9.0"
    }
  },
  "provenance": {
    "data_sources": [
      {
        "name": "HAZUS Flood v6.1 (bundled via Pelicun DLML)",
        "uri": "pelicun://dlml/HAZUS_FL_v6.1",
        "accessed_at": "2026-06-04T20:23:01Z"
      },
      {
        "name": "Microsoft Building Footprints",
        "uri": "gs://bucket/cache/buildings/ms_<hash>.fgb",
        "accessed_at": "2026-06-04T20:23:02Z"
      }
    ],
    "article_ids": ["01HX...", "01HX..."],
    "event_id": "01HX..."
  },
  "created_at": "2026-06-04T20:23:00Z",
  "completed_at": "2026-06-04T20:24:18Z",
  "compute_duration_s": 78.0
}
```

**Design rationale (forward-looking):**
- **Sibling, not extension.** `ImpactEnvelope` is a separate top-level type, not a new subtype of `AssessmentEnvelope`. It has its own `envelope_type` field pinned to `Literal["impact"]`; `AssessmentEnvelope.envelope_type` keeps its existing `Literal["modeled", "discovered"]` and is not extended by this amendment. Engines and post-processors emit semantically different artifacts (hazard footprint vs. building-level damage/loss) and a single envelope conflating both would force every reader to handle every field combination. The shared plumbing (`bbox`, `crs`, `layers`, `provenance`, lifecycle) is duplicated by design; the duplication is cheaper than a discriminated mega-envelope.
- **`envelope_type: "impact"` is a parallel discriminator value on the sibling class.** Readers that switch on `envelope_type` get a third arm rather than dispatching on a different top-level type. `AssessmentEnvelope`'s `Literal["modeled", "discovered"]` is not modified; code paths that handle both envelope shapes union the two literal sets at the call site (`Literal["modeled", "discovered", "impact"]`).
- **Lineage by `parent_envelope_id` + `parent_solver_run_ids`, not by overloading `solver_run_ids`.** Reusing `AssessmentEnvelope.solver_run_ids` for impact lineage would conflate "runs that produced this envelope" with "runs that produced this envelope's parent". A dedicated lineage field keeps the semantics unambiguous.
- **`hazard_type` inherited from parent, no `"impact"` hazard value.** Impact is computed against a hazard footprint; the hazard remains what it was (flood, seismic, etc.). The `hazard_type` literal in B.2 does not need extension.
- **`forcing` and `catalog_entries` are absent on `ImpactEnvelope`.** Impact envelopes do not carry their own forcing summary (the parent does) and do not reference public catalogs (the parent or its provenance does). The corresponding bullets in B.7 are amended to acknowledge the impact case explicitly.
- **`fragility_provenance` is first-class.** Per Decision M (source-authority tiers and citation discipline), every numerical claim cites its source. Damage and loss numbers must be traceable to the fragility/consequence library that produced them; the field is required, not optional.
- **Confirmation gating lives in FR-AS-8, not in the schema.** The envelope itself is data; gating is workflow-layer policy — any cost-incurring run requires explicit confirmation.

### B.7 Design rationale

- **`envelope_type` discriminator**: modeling and discovery produce semantically different artifacts but share the same shape downstream (UI rendering, narrative generation, storage). The discriminator makes the distinction explicit without forking the schema.
- **`forcing` is None for discovery, and absent on `ImpactEnvelope`**: there's no boundary condition to summarize on a discovery envelope (the catalog entry serves that role), and impact envelopes do not carry their own forcing summary — the parent `AssessmentEnvelope` does.
- **`catalog_entries` is None for modeling, and absent on `ImpactEnvelope`**: solver outputs aren't catalog-sourced, even when they read public data as inputs (those go in `provenance.data_sources` instead); impact envelopes inherit catalog provenance from their parent envelope and do not duplicate it.
- **`solver_run_ids` empty for discovery**: distinguishes computational artifacts from referential ones; supports queries like "which envelopes required actual compute?"
- **Discriminator + optional subtype payloads**: hazard-specific fields stay typed; the base stays clean; one envelope works for all hazards.
- **All metrics structured, none free-text**: every number the narrative cites lives in a typed field. The LLM reads them; it cannot invent them.
- **Layers as first-class objects, not bare URIs**: units, style preset, role, temporal config travel with the layer so the UI knows how to render and the agent knows how to describe.
- **Provenance is structured**: data sources, article IDs, event IDs as separate queryable fields, not free-text.
- **Optional fields stay optional, not absent**: `population_exposed: None` rather than omitting; keeps the schema shape stable.
- **Schema versioning from day one**: `schema_version: "v1"` as the first field; old documents stay readable when the schema evolves.
- **The base `metrics` field is empty**: forward-compatible slot; real metrics live in the subtype payload (`flood.metrics`, etc.).
- **`bbox` always EPSG:4326**: one CRS for cross-system communication; display and storage CRSes may differ but the envelope is canonical.
- **Times as UTC datetimes**: Pydantic handles conversion; storage is ISO 8601 with `Z`.
- **`solver_run_ids` as a list**: anticipates ensemble runs (averaging multiple SFINCS runs for uncertainty); single-element list is the common case for modeled envelopes; empty for discovered.

### B.8 Known open choices

- **Critical facility vocabulary**: `school, hospital, fire_station, police, other` covers v0.1; may extend (water treatment, power substations) when relevant data sources are wired.
- **Affected-buildings depth bins**: `0-0.5m, 0.5-1m, 1-2m, 2m+` is one reasonable bucketing; FEMA HAZUS uses different bins. Worth aligning to a downstream standard before locking.
- **Population source**: WorldPop vs. GHSL vs. LandScan — each has different licensing and accuracy. Pick during M5.
- **Base `metrics` field**: currently empty `BaseMetrics()`. Could drop entirely since subtypes carry their own. Kept for forward compatibility.
- **Discovery subtype schema**: for v0.1, discovery payloads use a permissive `dict` validated at the workflow layer. As discovered-data summaries become more common, formalize a `DiscoverySummary` subtype per hazard.

---

## Appendix C: EventMetadata Schema

> **Status: preemptive.** This appendix is a working specification drafted before implementation. Concrete schemas, field names, types, and event-type vocabulary are subject to revision once implementation surfaces real constraints (Gemini structured-output behavior, news article variability, downstream forcing-reconstruction needs, etc.). Treat as the starting point, not the contract — changes flow back into this appendix as they're learned.

### C.1 Purpose

`EventMetadata` is the structured representation of a real-world hazard event extracted from news content. It is produced by the `extract_event_metadata` tool (FR-TA-3), stored as a document in the MongoDB `events` collection, and consumed by `model_news_event` and related dispatcher logic to map an event to model forcing.

The schema is designed so that:
- Gemini can populate it via structured output mode (the schema becomes the JSON-mode response schema)
- Pydantic validation catches malformed extraction
- The dispatcher can match on `event_type` and read the corresponding typed intensity payload
- MongoDB Atlas Vector Search can index the `embedding` field for similar-event retrieval

### C.2 Top-level structure

```python
class EventMetadata(BaseModel):
    schema_version: Literal["v1"] = "v1"

    # Identity
    event_id: str                     # ULID

    # Classification
    event_type: Literal[
        "hurricane",
        "tropical_storm",
        "atmospheric_river",
        "intense_rainfall",
        "dam_failure",
        "levee_failure",
        "storm_surge",
        "river_flood",
        "flash_flood",
        "other"
    ]
    confidence: float                 # 0..1, extractor's self-reported confidence

    # Identity within the event domain, when available
    canonical_name: str | None        # e.g., "Hurricane Ian", "Cyclone Yaas"
    canonical_id: str | None          # e.g., "AL092022" for ATCF storms

    # Location
    location: EventLocation

    # Time
    time_range: TimeRange
    time_classification: Literal["past", "ongoing", "forecast"]

    # Intensity (discriminated by event_type)
    intensity: IntensityIndicators

    # Source attribution
    provenance: EventProvenance

    # Search support (populated separately from extraction)
    embedding: list[float] | None
    embedding_model: str | None       # e.g., "text-embedding-005"

    # Lifecycle
    extracted_at: datetime
    extractor_version: str            # for reproducibility
```

### C.3 Supporting types

```python
class EventLocation(BaseModel):
    # Validators enforce: at least one of bbox or place_name is required
    bbox: tuple[float, float, float, float] | None  # [minLon, minLat, maxLon, maxLat]
    place_name: str | None                          # e.g., "Fort Myers, FL"
    admin_unit: AdminUnit | None                    # parsed administrative context
    geocoded: bool = False                          # True iff bbox came from geocoding

    # Granularity for client-side auto-snap and padding decisions
    granularity: Literal[
        "country", "region", "state", "city", "facility", "bbox"
    ] | None = None

    # Modeling-readiness assessment for the news pipeline / dispatcher
    precision_class: Literal[
        "point_known",        # specific named facility, point coordinates available
        "polygon_known",      # specific neighborhood with defined boundaries
        "bbox_sufficient",    # admin area where bbox is enough for the modeling task
        "imprecise",          # "near the river" — needs user spatial input
        "ambiguous",          # "Springfield" — needs disambiguation
    ] | None = None

class AdminUnit(BaseModel):
    country: str | None               # ISO 3166-1 alpha-2, e.g., "US"
    region: str | None                # state/province, e.g., "FL"
    locality: str | None              # city/town

class TimeRange(BaseModel):
    start: datetime                   # UTC
    end: datetime                     # UTC

class EventProvenance(BaseModel):
    article_ids: list[str]            # MongoDB article IDs that contributed
    primary_article_id: str           # the "main" article when synthesizing across many
    extraction_notes: str | None      # free-text caveats from the extractor

# --- Claim-set types for multi-source numerical evidence ---

class NumericClaim(BaseModel):
    """A single numerical claim from a single source, with provenance."""
    value: float
    unit: str                         # canonical unit string, e.g., "kt", "mb", "ft", "inches"
    source_type: Literal[
        "agency",                     # tier 1 or 2: NWS, USGS, NHC, etc.
        "major_news",                 # tier 3: AP, Reuters, NYT, WaPo with direct sourcing
        "regional_news",              # tier 4: regional dailies, local TV websites
        "aggregator",                 # tier 5: news aggregators, secondary reporting
        "social",                     # tier 6: social/community (deferred to v0.2+)
        "other",
    ]
    source_id: str                    # article_id or agency feed entry id
    source_url: str
    observation_time: datetime | None # when the value was actually measured/observed, if known
    reporting_time: datetime          # when the source reported it
    confidence: float | None          # source's stated confidence, if any (0..1)
    outlier_flag: bool = False        # set by aggregation logic; True if flagged as outlier

class ClaimSet(BaseModel):
    """A set of numerical claims for one quantity across sources, with consensus."""
    claims: list[NumericClaim]
    consensus_value: float | None
    consensus_unit: str | None
    consensus_method: Literal[
        "single_source",              # only one claim, no aggregation
        "median",                     # median across non-outlier claims
        "authority_weighted",         # weighted by source_type tier
        "latest_authoritative",       # most recent agency claim
        "agent_synthesized",          # LLM-reasoned consensus (deep research mode)
    ] | None
    consensus_confidence: Literal["high", "medium", "low"] | None
    notes: str | None                 # agent commentary if relevant
```

### C.4 Intensity indicators (discriminated by `event_type`)

Every numerical field across the intensity types is a `ClaimSet`, not a bare number. This holds in both research mode (1-3 claims per set) and deep research mode (5-15 claims per set). Non-numerical fields (landfall location names, breach type enums, etc.) remain as scalar values.

```python
class IntensityIndicators(BaseModel):
    # Discriminated union: exactly one field populated based on event_type
    hurricane: HurricaneIntensity | None = None
    tropical_storm: TropicalStormIntensity | None = None
    atmospheric_river: AtmosphericRiverIntensity | None = None
    rainfall: RainfallIntensity | None = None
    dam_failure: DamFailureIntensity | None = None
    storm_surge: StormSurgeIntensity | None = None
    river_flood: RiverFloodIntensity | None = None
    flash_flood: FlashFloodIntensity | None = None
    generic: GenericIntensity | None = None  # fallback for "other"


class HurricaneIntensity(BaseModel):
    saffir_simpson: ClaimSet | None   # 1..5
    max_winds_kt: ClaimSet | None     # sustained winds at peak
    min_central_pressure_mb: ClaimSet | None
    landfall_location: str | None     # non-numeric; not claim-set wrapped

class TropicalStormIntensity(BaseModel):
    max_winds_kt: ClaimSet | None
    landfall_location: str | None

class AtmosphericRiverIntensity(BaseModel):
    ar_category: ClaimSet | None      # 1..5 (Ralph et al. scale)
    ivt_kg_m_s: ClaimSet | None       # integrated vapor transport

class RainfallIntensity(BaseModel):
    total_inches: ClaimSet | None
    duration_hours: ClaimSet | None
    peak_hourly_inches: ClaimSet | None
    return_period_years: ClaimSet | None

class DamFailureIntensity(BaseModel):
    dam_name: str | None              # non-numeric
    reservoir_volume_acre_feet: ClaimSet | None
    breach_type: Literal["overtopping", "piping", "structural", "unknown"] | None

class StormSurgeIntensity(BaseModel):
    peak_surge_ft: ClaimSet | None
    associated_storm: str | None      # non-numeric

class RiverFloodIntensity(BaseModel):
    river_name: str | None            # non-numeric
    peak_stage_ft: ClaimSet | None
    flood_stage_ft: ClaimSet | None   # official flood-stage threshold
    gauge_id: str | None              # USGS NWIS gauge if extractable

class FlashFloodIntensity(BaseModel):
    duration_hours: ClaimSet | None
    cause: Literal["thunderstorm", "training_storms", "dam_break", "unknown"] | None

class GenericIntensity(BaseModel):
    description: str                  # free-text fallback
    severity: Literal["minor", "moderate", "major", "catastrophic"] | None
```

### C.5 Example: hurricane

```json
{
  "schema_version": "v1",
  "event_id": "01HXEVT...",
  "event_type": "hurricane",
  "confidence": 0.92,
  "canonical_name": "Hurricane Ian",
  "canonical_id": "AL092022",
  "location": {
    "bbox": [-82.30, 26.20, -81.40, 27.10],
    "place_name": "Fort Myers, FL",
    "admin_unit": {
      "country": "US",
      "region": "FL",
      "locality": "Fort Myers"
    },
    "geocoded": true
  },
  "time_range": {
    "start": "2022-09-28T18:00:00Z",
    "end": "2022-09-30T06:00:00Z"
  },
  "time_classification": "past",
  "intensity": {
    "hurricane": {
      "saffir_simpson": {
        "claims": [
          {
            "value": 4,
            "unit": "category",
            "source_type": "agency",
            "source_id": "nhc-al092022-final",
            "source_url": "https://nhc.noaa.gov/data/tcr/AL092022_Ian.pdf",
            "observation_time": "2022-09-28T19:05:00Z",
            "reporting_time": "2023-04-03T00:00:00Z",
            "confidence": null,
            "outlier_flag": false
          }
        ],
        "consensus_value": 4,
        "consensus_unit": "category",
        "consensus_method": "single_source",
        "consensus_confidence": "high",
        "notes": "NHC Tropical Cyclone Report (final post-storm assessment)"
      },
      "max_winds_kt": {
        "claims": [
          {
            "value": 140,
            "unit": "kt",
            "source_type": "agency",
            "source_id": "nhc-al092022-final",
            "source_url": "https://nhc.noaa.gov/data/tcr/AL092022_Ian.pdf",
            "observation_time": "2022-09-28T19:05:00Z",
            "reporting_time": "2023-04-03T00:00:00Z",
            "confidence": null,
            "outlier_flag": false
          },
          {
            "value": 150,
            "unit": "kt",
            "source_type": "major_news",
            "source_id": "01HXART...A",
            "source_url": "https://example.com/ian-coverage",
            "observation_time": null,
            "reporting_time": "2022-09-28T20:30:00Z",
            "confidence": null,
            "outlier_flag": false
          }
        ],
        "consensus_value": 140,
        "consensus_unit": "kt",
        "consensus_method": "latest_authoritative",
        "consensus_confidence": "high",
        "notes": "NHC final assessment (140 kt) preferred over preliminary news reports (150 kt at landfall)"
      },
      "min_central_pressure_mb": {
        "claims": [
          {
            "value": 940,
            "unit": "mb",
            "source_type": "agency",
            "source_id": "nhc-al092022-final",
            "source_url": "https://nhc.noaa.gov/data/tcr/AL092022_Ian.pdf",
            "observation_time": "2022-09-28T18:35:00Z",
            "reporting_time": "2023-04-03T00:00:00Z",
            "confidence": null,
            "outlier_flag": false
          }
        ],
        "consensus_value": 940,
        "consensus_unit": "mb",
        "consensus_method": "single_source",
        "consensus_confidence": "high",
        "notes": null
      },
      "landfall_location": "Cayo Costa, FL"
    },
    "tropical_storm": null,
    "atmospheric_river": null,
    "rainfall": null,
    "dam_failure": null,
    "storm_surge": null,
    "river_flood": null,
    "flash_flood": null,
    "generic": null
  },
  "provenance": {
    "article_ids": ["01HXART...A", "01HXART...B", "01HXART...C"],
    "primary_article_id": "01HXART...A",
    "extraction_notes": null
  },
  "embedding": [0.0231, -0.1843, "..."],
  "embedding_model": "text-embedding-005",
  "extracted_at": "2026-06-04T20:13:55Z",
  "extractor_version": "v1.0.0"
}
```

### C.6 Example: atmospheric river (no canonical ID)

```json
{
  "schema_version": "v1",
  "event_id": "01HXEVT...",
  "event_type": "atmospheric_river",
  "confidence": 0.81,
  "canonical_name": null,
  "canonical_id": null,
  "location": {
    "bbox": [-124.0, 36.5, -121.5, 39.0],
    "place_name": "San Francisco Bay Area, CA",
    "admin_unit": {
      "country": "US",
      "region": "CA",
      "locality": null
    },
    "geocoded": true
  },
  "time_range": {
    "start": "2024-02-04T00:00:00Z",
    "end": "2024-02-06T00:00:00Z"
  },
  "time_classification": "past",
  "intensity": {
    "hurricane": null,
    "tropical_storm": null,
    "atmospheric_river": {
      "ar_category": {
        "claims": [
          {
            "value": 4,
            "unit": "category",
            "source_type": "regional_news",
            "source_id": "01HXART...",
            "source_url": "https://example-regional.com/ar-feb-2024",
            "observation_time": null,
            "reporting_time": "2024-02-05T14:00:00Z",
            "confidence": null,
            "outlier_flag": false
          }
        ],
        "consensus_value": 4,
        "consensus_unit": "category",
        "consensus_method": "single_source",
        "consensus_confidence": "medium",
        "notes": "AR category inferred from CW3E classification mentioned in article; no direct CW3E source fetched in research mode"
      },
      "ivt_kg_m_s": {
        "claims": [
          {
            "value": 850.0,
            "unit": "kg/m/s",
            "source_type": "regional_news",
            "source_id": "01HXART...",
            "source_url": "https://example-regional.com/ar-feb-2024",
            "observation_time": null,
            "reporting_time": "2024-02-05T14:00:00Z",
            "confidence": null,
            "outlier_flag": false
          }
        ],
        "consensus_value": 850.0,
        "consensus_unit": "kg/m/s",
        "consensus_method": "single_source",
        "consensus_confidence": "low",
        "notes": "Single regional-news source; deep research mode would query CW3E directly"
      }
    },
    "rainfall": null,
    "dam_failure": null,
    "storm_surge": null,
    "river_flood": null,
    "flash_flood": null,
    "generic": null
  },
  "provenance": {
    "article_ids": ["01HXART..."],
    "primary_article_id": "01HXART...",
    "extraction_notes": "AR category inferred from CW3E classification mentioned in article"
  },
  "embedding": [0.0188, -0.0921, "..."],
  "embedding_model": "text-embedding-005",
  "extracted_at": "2026-06-04T20:13:55Z",
  "extractor_version": "v1.0.0"
}
```

### C.7 Production and consumption

**Production** — by `extract_event_metadata(article_text)`:
1. Gemini is invoked in structured-output mode with the `EventMetadata` schema attached
2. Gemini returns a populated object
3. The tool validates via Pydantic; on validation failure, the article is flagged for re-extraction or human review
4. The tool computes `embedding` via `text-embedding-005` over a canonical text representation (canonical name + event type + location + time + key intensity values)
5. The tool assigns `event_id` (ULID), `extracted_at`, and `extractor_version`
6. The document is upserted into the `events` collection per the metadata-payload pattern (§3.7)

**Consumption** — by `model_news_event(event_id)`:
1. Read the event document from MongoDB
2. Match on `event_type` to dispatch to the appropriate forcing-reconstruction logic:
   - `hurricane`, `tropical_storm` → fetch NHC ATCF track → `run_storm_surge_flood`
   - `intense_rainfall`, `atmospheric_river` → reconstruct precipitation event → `run_pluvial_flood`
   - `river_flood` → fetch USGS NWIS stage data → `run_fluvial_flood`
   - `dam_failure`, `levee_failure` → user-supplied or LLM-estimated breach hydrograph → `run_fluvial_flood`
   - `storm_surge` → fetch associated storm track → `run_storm_surge_flood`
   - `flash_flood`, `other` → ask user for clarification rather than dispatch
3. The returned `AssessmentEnvelope.provenance.event_id` references this event; `provenance.article_ids` is copied from the event's provenance

### C.8 Design rationale

- **Discriminated intensity mirrors `AssessmentEnvelope`**: same pattern across the system; reduces cognitive load and code duplication.
- **`confidence` as a first-class field**: extractor's self-assessment is preserved; the dispatcher can ask the user for confirmation when low (e.g., < 0.6).
- **`canonical_id` optional**: storms have ATCF IDs; atmospheric rivers don't; dam failures don't. The field is there when applicable, `None` otherwise.
- **Location allows bbox, place_name, or both**: real-world articles vary; the schema reflects all realistic cases including ambiguous ones requiring user clarification.
- **`time_classification` drives data-source selection**: past events use historical archives (NHC ATCF archive, USGS NWIS history); forecast events use forecast products (NHC active advisories); ongoing events may mix both.
- **`embedding` is optional in the schema**: population happens after extraction; the schema accommodates the just-extracted state and the fully-indexed state.
- **`extractor_version` captured**: when extraction logic changes, old documents stay attributable to their producer. Reproducibility for the news pipeline.
- **`extraction_notes` lets the extractor flag uncertainty in text**: free-text escape hatch for "I inferred X from Y" annotations that don't fit structured fields.
- **Provenance distinguishes primary from contributing articles**: if multiple articles feed one extraction, the primary is the canonical one for citation.
- **`generic` intensity handles "other" events**: hazards happen that don't fit pre-defined categories; the system degrades gracefully rather than failing extraction.

### C.9 Known open choices

- **Embedding dimension**: `text-embedding-005` is 768-dim by default but supports configurable down to 128. Trade off recall vs. index size.
- **Event type taxonomy growth**: the list of 10 covers v0.1 hazards (flood-adjacent); expands when wildfire, seismic, and contaminant transport engines land.
- **`canonical_id` namespacing**: for storms, ATCF IDs work; no agreed-upon ID system for floods, ARs, rainfall. May need a `canonical_id_scheme` field later.
- **Multi-event articles**: one article might describe multiple distinct events (e.g., a season summary). Schema currently assumes one event per extraction call; the agent may need to extract multiple from one article and produce N documents.
- **Confidence calibration**: `confidence` is currently the model's self-report. Could leave as-is or post-process via calibration.
- **Re-extraction policy**: when extractor_version changes, do existing documents get re-extracted automatically, on-demand, or never?

---

## Appendix D: MongoDB Collection Schemas

> **Status: preemptive.** This appendix is a working specification drafted before implementation. Concrete schemas, field names, indexes, and storage strategies are subject to revision once implementation surfaces real constraints (MongoDB MCP query patterns, Atlas Vector Search performance, actual document sizes, real query patterns under load, etc.). Treat as the starting point, not the contract — changes flow back into this appendix as they're learned.

### D.1 Overview

Five collections in MongoDB Atlas, each with a Pydantic schema that maps directly to BSON documents. The collections instantiate the metadata-payload pattern (§3.7 FR-MP): some are pure metadata indexes over GCS payloads (`projects`); some embed full data alongside metadata (`runs` embeds `AssessmentEnvelope`); some are authoritative documents with no GCS payload by default (`events`, `articles`, `sessions`).

| Collection | Purpose | Source of truth | GCS payload |
|---|---|---|---|
| `projects` | Index over `.qgs` files | GCS for `.qgs`; Mongo for ownership/classification | `.qgs` |
| `runs` | Every solver execution or discovery operation | Embedded `assessment` document | COGs, vectors via `assessment.layers[].uri` |
| `articles` | News article corpus | Mongo document | Optional `html_uri` for long HTML |
| `events` | Extracted `EventMetadata` documents | Mongo document | Optional forcing data referenced from event |
| `sessions` | Chat sessions, state, history | Mongo document | None |

Schemas are defined as Pydantic models for use in application code; the BSON representation is `model.model_dump(mode="json")` with ULIDs as `_id`. Connection from the agent is via the MongoDB MCP server (Decision F); internal worker services may use direct PyMongo for performance.

### D.2 Collection: `projects`

Metadata index over `.qgs` project files in GCS. Rebuildable from GCS bucket walks if Mongo is lost.

```python
class ProjectDocument(BaseModel):
    schema_version: Literal["v1"] = "v1"

    # Identity (_id is the project_id used everywhere)
    _id: str                          # ULID

    # Ownership
    session_id: str                   # owning session

    # Storage pointer
    qgs_uri: str                      # gs://.../project_<id>.qgs (canonical)

    # Display metadata
    name: str                         # human-readable, e.g., "Hurricane Ian flood analysis"
    description: str | None

    # Spatial
    bbox: tuple[float, float, float, float] | None  # current project extent (EPSG:4326)

    # Classification
    hazard_types: list[str]           # all hazards represented in current layers

    # Layer index (denormalized from .qgs for queries; .qgs is authoritative)
    layers: list[ProjectLayerSummary]

    # Lifecycle
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None       # soft delete

class ProjectLayerSummary(BaseModel):
    layer_id: str
    name: str
    layer_type: Literal["raster", "vector"]
    uri: str
    style_preset: str
    visible: bool
    role: Literal["primary", "context", "input"]
    temporal: bool                    # has WMS-T config
```

**Indexes:**
```
{ session_id: 1, updated_at: -1 }         // "show this session's recent projects"
{ deleted_at: 1 } (sparse)                 // efficient exclusion of soft-deleted
{ hazard_types: 1, updated_at: -1 }       // "find recent flood projects"
2dsphere on bbox                           // spatial queries (optional, lazy-created)
```

### D.3 Collection: `runs`

Every solver execution or discovery operation. Embeds the full `AssessmentEnvelope` (when complete) alongside denormalized top-level fields for indexing.

```python
class RunDocument(BaseModel):
    schema_version: Literal["v1"] = "v1"

    # Identity
    _id: str                          # ULID; this is the solver_run_id
    project_id: str
    session_id: str

    # Status lifecycle: pending → running → complete | failed | cancelled
    status: Literal["pending", "running", "complete", "failed", "cancelled"]
    started_at: datetime | None
    completed_at: datetime | None
    duration_seconds: float | None

    # Type discriminator: "modeled" and "discovered" mirror AssessmentEnvelope.envelope_type;
    # "impact" mirrors ImpactEnvelope.envelope_type (Appendix B.6c).
    # Forward-looking (not in M1 / not in sprint-03): "impact" is added post-M5 once Pelicun lands;
    # when run_type == "impact", the `assessment` field carries an `ImpactEnvelope` (Appendix B.6c)
    # rather than an `AssessmentEnvelope`. See FR-MP-5 and Decision N.
    run_type: Literal["modeled", "discovered", "impact"]
    hazard_type: str                  # denormalized from envelope
    workflow_name: str                # denormalized from envelope

    # Spatial (denormalized for queries)
    bbox: tuple[float, float, float, float]

    # Event time (denormalized when applicable)
    event_time_start: datetime | None
    event_time_end: datetime | None

    # Canonical assessment — full AssessmentEnvelope as dict
    # None until status == "complete"
    assessment: dict | None

    # Embedding over a text representation of the envelope (for "similar runs")
    embedding: list[float] | None
    embedding_model: str | None

    # Failure details (when status == "failed")
    error_code: str | None
    error_message: str | None

    # Cancellation
    cancellation_reason: str | None
    cancelled_at: datetime | None

    # User-provided spatial inputs (FR-AS-10)
    user_spatial_inputs: list[UserSpatialInput]

    # Provenance shortcuts (denormalized from assessment.provenance)
    event_id: str | None              # if news-derived
    article_ids: list[str]            # if news-derived

class UserSpatialInput(BaseModel):
    request_id: str                   # the WebSocket request that solicited this input
    geometry_type: Literal["point", "bbox"]
    coordinates: list[float]          # [lon, lat] for point; [minLon, minLat, maxLon, maxLat] for bbox
    prompt_title: str                 # the title shown to the user
    submitted_at: datetime
```

**Indexes:**
```
{ session_id: 1, started_at: -1 }                            // session's run history
{ project_id: 1, started_at: -1 }                            // project's run history
{ status: 1, started_at: -1 }                                 // partial: status in ["pending","running"]
{ hazard_type: 1, started_at: -1 }                            // "recent flood runs"
{ run_type: 1, hazard_type: 1, completed_at: -1 }            // "recent modeled wildfire runs"
{ event_id: 1 } (sparse)                                      // runs derived from a specific event
2dsphere on bbox                                              // spatial run queries
```

**Atlas Vector Search index:**
```yaml
name: runs_embedding_vsi
type: vectorSearch
fields:
  - { type: vector, path: embedding, numDimensions: 768, similarity: cosine }
  - { type: filter, path: hazard_type }
  - { type: filter, path: run_type }
```

### D.4 Collection: `articles`

Fetched news article corpus. Text inlined for v0.1; large HTML may move to GCS via the optional `html_uri`.

```python
class ArticleDocument(BaseModel):
    schema_version: Literal["v1"] = "v1"

    # Identity
    _id: str                          # ULID

    # Source
    url: str                          # canonical URL
    url_hash: str                     # SHA-256 of normalized URL for dedup
    title: str
    publisher: str | None             # extracted from URL or metadata
    author: str | None

    # Content
    text: str                         # extracted article text (cleaned)
    text_length: int                  # character count
    html_uri: str | None              # GCS URI if full HTML retained

    # Time
    published_at: datetime | None     # article publication time
    fetched_at: datetime              # when this system fetched it

    # Search support
    embedding: list[float] | None
    embedding_model: str | None

    # Extraction lifecycle
    extraction_status: Literal["pending", "extracted", "failed", "no_events"]
    extracted_event_ids: list[str]    # events derived from this article (may be 0..N)
    last_processed_at: datetime | None
```

**Indexes:**
```
{ url_hash: 1 } (unique)                          // dedup on URL
{ fetched_at: -1 }                                 // recently fetched
{ published_at: -1 } (sparse)                     // recently published
{ publisher: 1, published_at: -1 } (sparse)       // recent from a source
{ extraction_status: 1, fetched_at: -1 }          // find articles to process
```

**Atlas Vector Search index:**
```yaml
name: articles_embedding_vsi
type: vectorSearch
fields:
  - { type: vector, path: embedding, numDimensions: 768, similarity: cosine }
  - { type: filter, path: extraction_status }
```

### D.5 Collection: `events`

`EventMetadata` documents (full schema in Appendix C). The document is authoritative. The collection schema *is* the `EventMetadata` schema; no wrapper needed.

```python
class EventDocument(EventMetadata):
    # All fields inherited from EventMetadata (Appendix C)
    pass
```

**Indexes:**
```
{ event_type: 1, "time_range.start": -1 }                   // recent events of a type
{ canonical_id: 1 } (sparse, unique)                         // storm lookup by ATCF ID
{ canonical_name: 1 } (sparse)                               // storm lookup by name
{ "location.admin_unit.region": 1, "time_range.start": -1 } // events by state
{ extracted_at: -1 }                                         // recently extracted
{ "provenance.article_ids": 1 }                              // find events derived from an article
2dsphere on location.bbox                                    // spatial event queries
```

**Atlas Vector Search index:**
```yaml
name: events_embedding_vsi
type: vectorSearch
fields:
  - { type: vector, path: embedding, numDimensions: 768, similarity: cosine }
  - { type: filter, path: event_type }
  - { type: filter, path: time_classification }
```

### D.6 Collection: `sessions`

Chat session state. Holds the full session: ownership, chat history, current map state, pipeline history. Read on resume; written incrementally during the session. TTL-driven cleanup.

```python
class SessionDocument(BaseModel):
    schema_version: Literal["v1"] = "v1"

    # Identity
    _id: str                          # ULID; this is the session_id

    # Ownership (anonymous in v0.1; user_id added later)
    client_fingerprint: str | None    # opaque client identifier (cookie-derived)

    # Lifecycle
    created_at: datetime
    last_active_at: datetime
    expires_at: datetime              # used for TTL cleanup; updated on each interaction

    # Conversation
    chat_history: list[ChatMessage]   # bounded; oldest truncated when > max (default 200 messages)
    project_ids: list[str]            # projects created in this session
    pipeline_history: list[PipelineSnapshot]  # bounded; recent pipelines (default last 20)
    current_pipeline: PipelineSnapshot | None

    # Current map state (mirrors what the client shows)
    loaded_layers: list[ProjectLayerSummary]   # current layers
    map_view: MapView                          # current center/zoom/bbox

class ChatMessage(BaseModel):
    message_id: str                   # ULID; matches the WebSocket message ID for agent messages
    role: Literal["user", "agent"]
    content: str                      # for agent messages, the final accumulated text after streaming
    tool_calls: list[ToolCallSummary] # for agent messages; empty list for user
    created_at: datetime

class ToolCallSummary(BaseModel):
    call_id: str
    tool_name: str
    state: Literal["complete", "failed", "cancelled"]
    result_summary: str | None
    result_uri: str | None
    error_code: str | None
    started_at: datetime
    completed_at: datetime | None

class PipelineSnapshot(BaseModel):
    pipeline_id: str
    started_at: datetime
    completed_at: datetime | None
    final_state: Literal["complete", "failed", "cancelled"] | None
    steps: list[PipelineStepSummary]

class PipelineStepSummary(BaseModel):
    step_id: str
    name: str
    tool_name: str
    state: Literal["pending", "running", "complete", "failed", "cancelled"]
    started_at: datetime | None
    completed_at: datetime | None
    progress_percent: int | None     # 0..100; workflow-attributed, never LLM-estimated
    error_code: str | None           # SCREAMING_SNAKE_CASE per Appendix A.6; present only when state == "failed"
    error_message: str | None        # short human-readable; capped at 512 chars to discourage stack-trace leakage

class MapView(BaseModel):
    center: tuple[float, float]       # [lon, lat]
    zoom: float
    bbox: tuple[float, float, float, float]
```

**Indexes:**
```
{ last_active_at: -1 }                                       // recently active sessions
{ expires_at: 1 }                                            // TTL cleanup driver
{ client_fingerprint: 1, last_active_at: -1 } (sparse)      // a client's sessions
```

**TTL configuration:** documents are eligible for auto-deletion 30 days after `expires_at`. Active sessions update `expires_at` on each interaction (sliding-window expiry). Inactive sessions naturally age out.

**PipelineStepSummary progress + error fields (additive, all optional).** Three optional fields support the M3 PipelineStrip render and M4's real `pipeline-state` emission (Appendix A.4 `pipeline-state` payload). All three default to `None` and never appear on a healthy `pending` / `complete` step.

- `progress_percent: int | None` — integer in `[0, 100]` (pydantic `Field(ge=0, le=100)`). Populated by the workflow when it can reasonably attribute progress (solver chunk N of M, n-of-M dataset rows processed). Never an LLM estimate (Invariant 1: determinism boundary). Tightening to required when `state == "running"` is a future amendment; for v0.1 the field stays optional everywhere.
- `error_code: str | None` — `SCREAMING_SNAKE_CASE` literal aligned with the Appendix A.6 error-code convention; populated only when `state == "failed"`. The set of valid codes is **open** per A.6 (every workflow may register its own); the schema validates shape, not membership.
- `error_message: str | None` — short human-readable explanation accompanying `error_code`. Free text, capped at 512 characters by `Field(max_length=512)` to discourage stack-trace leakage through the WebSocket envelope.

No cost / dollar / duration-estimate field is added anywhere (Invariant 9: no cost theater). The web client's `PipelineStepSummary` mirror already carries the three fields as optional from job-0026; this amendment lands them in the canonical schema, closing OQ-W-26-PIPELINE-STEP-FIELDS.

**AtomicToolMetadata (collateral, not a collection document).** A separate pydantic model defined in `grace2_contracts.tool_registry` carries the FR-DC-2 TTL-class declaration every external-API atomic tool registers at definition time (`name` / `ttl_class` / `source_class` / `cacheable`, with a cross-field `model_validator` enforcing the FR-DC-6 consistency rule). It is not persisted to MongoDB — it lives in the agent service's tool registry — but the schema is owned alongside the Appendix D collection schemas so the contract surface is single-sourced. See §3.9 for the cache architecture this metadata feeds.

### D.7 Cross-cutting decisions

- **Schema versioning per collection**: every document has `schema_version: Literal["v1"] = "v1"` as the first field. Migrations bump independently per collection.
- **ULIDs as `_id`**: consistent with Appendix A. Time-sortable, URL-safe, no central coordination.
- **Embedding storage strategy**: same model (`text-embedding-005`, 768-dim) across collections; text representation varies:
  - `runs.embedding`: text rep over envelope fields (hazard + location + metrics + provenance)
  - `articles.embedding`: article text truncated to first ~8000 tokens
  - `events.embedding`: canonical event description (name + type + location + time + intensity)
- **Soft deletes**: only `projects` supports soft delete (via `deleted_at`). `runs`, `events`, `articles` are append-and-modify-once. `sessions` are TTL-cleaned.
- **Run status terminal states**: `complete`, `failed`, `cancelled` are terminal; no transitions out.
- **`assessment` as `dict` not nested Pydantic model**: trades schema validation at the document level for forward compatibility (envelope schema changes don't require migrations). Validation happens at API boundaries (in the agent service, before write).
- **Cross-collection references as raw string IDs**: not `DBRef`. Validators check existence on write where needed.
- **MCP access vs direct PyMongo**: agent reads/writes through MongoDB MCP server (Decision F); worker services write results directly with PyMongo for throughput.
- **No cost fields on runs**: cost-tracking and cost-estimation are deferred indefinitely. Surfacing approximate cost figures to users is worse than not surfacing them; cents-precise tracking is not currently achievable.

### D.8 Storage sizing (v0.1 baseline)

Rough per-document sizes:
- `projects`: ~5 KB
- `runs`: ~50–200 KB (varies with layer count and embedded envelope size)
- `articles`: ~20–100 KB (text + embedding)
- `events`: ~10 KB
- `sessions`: variable, up to ~1 MB for very long sessions

A reasonable v0.1 baseline (1000 articles, 200 events, 100 runs, 50 sessions, 50 projects) fits within an Atlas M10 cluster (10 GB storage). Atlas Vector Search indexes are billed separately; three indexes (runs, articles, events) is the minimum useful set.

If infrastructure budget is constrained in early v0.1, dropping the `runs` vector index is the cheapest cut — "similar past runs" is a nice-to-have, not load-bearing.

### D.9 Design rationale

- **Five collections, not one**: each has distinct query patterns and lifecycle policies. Mongo's $lookup makes joins workable, but separate collections give cleaner indexes and TTLs.
- **Embedding the envelope in `runs.assessment` instead of normalizing into separate collections**: a run is naturally self-contained (one envelope, one set of metrics, one set of layers). Normalizing into a `layers` collection or `metrics` collection would multiply joins without adding query power.
- **Denormalized top-level fields on `runs`**: `hazard_type`, `bbox`, `event_time_start/end` are copied from the embedded envelope so indexes work without needing computed indexes over `assessment.*`. Storage cost is negligible (<100 bytes per run); query benefit is large.
- **TTL only on `sessions`**: long-running sessions naturally expire; runs and events are reference data that should persist indefinitely (or be archived deliberately, not auto-pruned).
- **Anonymous session ownership via `client_fingerprint`**: v0.1 has no user accounts. A cookie-derived opaque identifier lets returning clients see their prior sessions without authentication; adding real user IDs later replaces this field cleanly.
- **`UserSpatialInput` typed and stored on the run**: reproducibility and audit. If the model run depends on user-placed pin coordinates, future viewers of the run need to see where the pin was.
- **Vector search filters in addition to vector field**: filtering by `hazard_type` or `event_type` makes vector queries faster and more relevant; Atlas Vector Search supports this natively at index creation.
- **No cost tracking fields**: cost estimation is deferred indefinitely; tracking actual costs per run is a feature waiting on that decision.

### D.10 Known open choices

- **Article text storage**: inline by default; `html_uri` for very long content. Threshold for switching (size, character count) TBD.
- **Run embedding text representation**: what string actually gets embedded? Could be deterministic from envelope fields or LLM-summarized. Affects similarity quality; decide during M7.
- **Session TTL value**: 30 days is a guess. Real number depends on usage patterns. Adjustable per environment.
- **Anonymous client fingerprint mechanism**: cookie-based vs IP-based vs fully ephemeral (per-tab). Affects whether returning users see their prior sessions. Likely cookie-based in v0.1.
- **Index review cadence**: indexes will need pruning or addition as real query patterns emerge. Schedule a review after M7 when news pipeline is operational and query patterns are observable.
- **Vector index dimension choices**: `text-embedding-005` defaults to 768; smaller dimensions (256, 128) trade recall for index size/cost. Verify on a small corpus before committing.
- **Whether to extend soft delete to `runs`**: useful for "I made a mistake, let me delete this run from my history" but adds complexity. Currently no.

### D.11 Collection: `catalog_entries` *(sprint-08 amendment — landed by job-0045-schema-20260607)*

The Mode 1 curated data-source catalog (§F.1.2). Each document is a `CatalogEntry` (FR-PHC-2 binding shape — see Appendix F §F.1.2 Mode 1). The collection schema *is* the `CatalogEntry` schema; no wrapper fields are added.

```python
class CatalogEntryDocument(CatalogEntry):
    # All fields inherited from CatalogEntry (FR-PHC-2 + §F.1.2 Mode 1):
    #   schema_version: Literal["v1"]
    #   id: str                          # stable identifier; the Mongo _id
    #   name: str
    #   description: str
    #   urls: list[str]                  # primary URL + alternative mirrors
    #   access_tier: Literal[1, 2, 3, 4]  # §F.1.1
    #   credential_tier: Literal[1, 2, 3] # §F.1
    #   ttl_class: Literal["static-30d", "semi-static-7d", "dynamic-1h", "live-no-cache"]
    #   source_class: str                # FR-DC-1 bucket-prefix
    #   license: str
    #   citation: str
    #   vintage: str | None
    #   last_verified: datetime
    #   status: Literal["active", "deprecated", "user_proposed_pending_curator_review"]
    #   how_to_use: str                  # invocation examples + quirks
    #   api_key_secret_ref: str | None   # required when credential_tier >= 2
    pass
```

The Mongo `_id` is the entry `id` (a free-form stable string identifier curated at entry-creation time, e.g. `"usgs-3dep-dem-1m"`, `"worldpop-1km-aggregated"`); the write path sets `_id = id` at insert time. No `_id` alias on the model — `CatalogEntry` stays a single shape across wire / YAML / Mongo, and the entry `id` is not a ULID.

**Indexes:**
```
{ source_class: 1 }                                  // catalog_search by domain
{ status: 1, source_class: 1 }                       // active-only by source (the common query path)
```

**TTL configuration:** none. Catalog entries are durable until a curator deprecates them (the `status` lifecycle does the soft-delete work).

**Status lifecycle:**
- `active`: curator-vetted; `catalog_search` returns this entry.
- `deprecated`: curator-removed; retained for audit / historical run-provenance lookups but excluded from active search results.
- `user_proposed_pending_curator_review`: a §F.1.2 Mode 2 user-accepted `offer-catalog-addition` entry; included in `catalog_search` results but surfaced as provisional until a curator flips it to `active`.

**Cross-field rule** (enforced by the `CatalogEntry` model validator): when `credential_tier == 1`, `api_key_secret_ref` must be `None`; when `credential_tier >= 2`, `api_key_secret_ref` is required (non-empty string — typically the Secret Manager resource path).

### D.12 Collection: `catalog_audit_log` *(sprint-08 amendment — landed by job-0045-schema-20260607)*

Append-only audit trail for the catalog. Every catalog mutation lands one document here. Mode 2 user-proposed entries produce a `user_proposed` event at acceptance; curator-side approval / rejection produce a `curator_approved` / `curator_rejected` event against the same `entry_id`. Decision M (claim provenance) requires this trail to be inspectable: the catalog query path may surface user-proposed entries as provisional, and downstream `RunDocument` references to a catalog entry can be resolved back through this collection to recover the proposal + review context.

```python
class CatalogAuditLogDocument(BaseModel):
    schema_version: Literal["v1"] = "v1"

    # Identity
    _id: str                          # ULID; the audit-event id

    # Subject
    entry_id: str                     # references CatalogEntry.id

    # Origin (optional — populated when the event happened inside a session
    # or when user identity is available; v0.1 leaves user_id None since
    # identity machinery is not yet wired)
    session_id: str | None
    user_id: str | None

    # Event
    event_type: Literal[
        "add",                        # curator added a new entry directly (Mode 1)
        "update",                     # curator edited an existing entry's metadata
        "deprecate",                  # curator flipped status to "deprecated"
        "user_proposed",              # Mode 2 user accepted an offer-catalog-addition
        "curator_approved",           # curator flipped a user-proposed entry to "active"
        "curator_rejected",           # curator removed a user-proposed entry
    ]
    event_payload: dict               # shape varies by event_type; see below

    # Time
    timestamp: datetime
```

**`event_payload` shape (varies by `event_type`):**
- `add` / `update`: the diff (`{ "fields_changed": [...], "before": {...}, "after": {...} }`).
- `deprecate`: the curator note (`{ "note": "..." }`).
- `user_proposed`: conformity-probe findings + the originating `offer-catalog-addition` request id (`{ "probe_findings": {...}, "request_id": "01HX..." }`).
- `curator_approved`: the curator note + reviewing-curator identifier (post-M6+).
- `curator_rejected`: the curator note + rejection reason.

**Indexes:**
```
{ entry_id: 1, timestamp: -1 }                       // audit-trail-for-an-entry query path
```

**TTL configuration:** none. Audit-log entries are durable indefinitely per Decision M (claim provenance must survive across all retention windows).

## Appendix E: QGIS Plugins Inventory

> *(Forward-looking — not in M1 / not in sprint-03; binding when the deferred engines in §2.3 and the conservation atomic-tool utilities in §3.3 FR-TA-2 start landing. v0.3.15 amendment.)*

**Purpose.** This appendix catalogs the QGIS plugins that are the natural plugin-side counterparts to the deferred engines (§2.3) and the deferred atomic-tool utilities (§3.3 FR-TA-2 conservation / biodiversity subsection). It is informational: a plugin appearing here does NOT bind the architecture to invoke it through QGIS Processing. The engine-selection principle (§2.3) gives equal status to plugin-backed and Python-shim integration modes; in most cases the cleanest GRACE-2 integration is the underlying Python library called directly from a worker, with the QGIS plugin retained as a reference for users who want a desktop-QGIS workflow against the same data.

**Integration-mode hint per plugin** uses three categories:

- **`yes-bake-into-image`** — the plugin is a pure-Python QGIS Processing-provider that runs cleanly headless under `QT_QPA_PLATFORM=offscreen`, has no external solver dependencies, is lightweight, and registers algorithms callable via `qgis_process` from the existing PyQGIS worker image. The plugin can be baked into the worker image (or, where rendering is relevant, into the QGIS Server image — see the §2.2 component diagram) and reached through the existing `qgis_process` atomic tool.
- **`adapt-via-python`** — the plugin wraps an underlying Python library that is itself pip-installable and has a richer programmatic API than the plugin exposes. Cleanest GRACE-2 path is to install the underlying library system-wide in the worker image and write a thin atomic-tool wrapper; the QGIS plugin is retained as a reference for users in QGIS Desktop. Avoids per-plugin venv sandboxing patterns and surface-area limitations of the plugin GUI.
- **`out-of-process`** — the plugin (or its underlying solver) is invoked as a separate Docker image or external service, communicating via GCS file artifacts and Cloud Workflows step transitions per FR-CE-1/2/3. Used when the solver is GPL/LGPL and the MIT license posture (NFR-L) requires linkage isolation, or when the solver brings heavy runtime dependencies (e.g. Julia for Circuitscape.jl, MPI binaries for TELEMAC).

### E.1 Plugin-side counterparts for §2.3 Deferred engines

| Plugin | Wraps engine | QGIS version | Integration mode | Notes |
|---|---|---|---|---|
| **Q4TS** | open-TELEMAC (TELEMAC-2D/3D + SALOME). 2D/3D shallow-water hydrodynamic solver developed by EDF. | QGIS 3.34.11 to 4.10.16 (latest v0.13.0). | `out-of-process` | TELEMAC binaries are GPLv3 + LGPLv3; out-of-process containerization is the established license-isolation pattern (see Decision N notes in the v0.3.14 amendment). The plugin is useful for desktop pre/post but the worker invokes TELEMAC via consortium driver scripts (`telemac2d.py`, `telemac3d.py`, `tomawac.py`) inside a separate `simvia/opentelemac`-style Docker image. See OQ-9 for the mesh-generation toolchain selection. |
| **QGIS-HMS** | HEC-HMS (USACE rainfall-runoff). No first-class QGIS plugin exists; the common QGIS workflow for HEC-HMS is terrain-prep via QGIS Processing + HEC-HMS desktop client. | n/a (terrain-prep workflow, not a single plugin) | `adapt-via-python` | HEC-HMS itself is public-domain (US Government). Recommended path: terrain-prep atomic tools in the worker (via `qgis_process` calls to native QGIS algorithms — already in the v0.1 atomic-tool surface) feed into a `run_hec_hms_simulation` atomic tool that invokes the `hechmsio` Python wrapper or the HEC-HMS command-line interface in a Cloud Run Job. |
| **FREEWAT** | MODFLOW family: MODFLOW-2005 + MODFLOW-OWHM v1 (flow); MT3DMS (transport); UCODE_2014 (calibration). | QGIS 3.x. | `out-of-process` | First of three MODFLOW-family plugins (see also QSWATMOD, iMOD, APEXMOD below). MODFLOW version fragmentation is real: FREEWAT pins MODFLOW-2005-derived solvers, QSWATMOD/APEXMOD pin MODFLOW-NWT, iMOD targets MODFLOW 6. The groundwater worker image bakes all three MODFLOW binaries; choice of which family to invoke is a per-call atomic-tool parameter. |
| **QSWATMOD (QSWATMOD2)** | SWAT-MODFLOW coupler. SWAT 2012 + MODFLOW-NWT (Newton formulation built on MODFLOW-2005 v1.11). Optional RT3D-Salt. | QGIS 3.x. | `out-of-process` | SWAT + MODFLOW-NWT coupler for combined surface + groundwater modeling. The plugin is useful for desktop QA; the worker invokes the SWAT + MODFLOW-NWT binaries directly. |
| **iMOD (imodqgis)** | Deltares iMOD groundwater suite targeting MODFLOW 6 (unstructured grids, DIS/DISV). Companion components: iMOD Python (modelbuilder), iMOD Coupler (xmipy). | QGIS 3.x. | `adapt-via-python` | iMOD Python is the canonical programmatic API for MODFLOW 6 model construction. Worker calls iMOD Python directly; the QGIS plugin is the desktop counterpart. |
| **APEXMOD** | APEX (Agricultural Policy/Environmental eXtender) + MODFLOW + RT3D-Salt. Couples agroecosystem surface model with groundwater + solute transport. | QGIS 3.x. | `out-of-process` | APEX is a USDA-ARS public-domain agricultural model. Worker invokes APEX + MODFLOW + RT3D binaries from a dedicated container. |
| **THYRSIS** | Hydrogeological flow and contaminant transport modeling (QGIS-3 plugin). Coupled groundwater + transport for site-specific contamination scenarios. | QGIS 3.x. | `yes-bake-into-image` | Genuine Processing-provider plugin; runs headless via `qgis_process`. Lightweight, baked into the PyQGIS worker image. Useful when the user asks for site-scale contaminant transport without standing up MODFLOW or iMOD. |

### E.2 Plugin-side counterparts for §3.3 FR-TA-2 conservation / biodiversity utilities

The conservation/biodiversity domain SRS position is OQ-11 (provisional placement in the post-processing tool-class layer). Per-plugin integration modes:

| Plugin | Purpose | Underlying library | Integration mode | Notes |
|---|---|---|---|---|
| **Maxent (QMaxent / Maxent Model / QSDM)** | Species Distribution Modeling. QMaxent is the modern path: wraps the `elapid` Python library for MaxEnt SDM with spatial cross-validation, jackknife variable importance, habitat suitability projection. | `elapid`, `maxnet` (pip-installable) | `adapt-via-python` | QMaxent uses a per-plugin venv to install `elapid`, awkward for a baked container. Cleaner: pre-install `elapid` system-wide in the worker image and back the `run_maxent_sdm` atomic tool (FR-TA-2 conservation subsection) with it directly. The legacy Java MaxEnt jar is also CLI-callable as a fallback. |
| **inVEST (NatCap)** | 18 ecosystem-service models from Stanford's Natural Capital Project (carbon, water yield, sediment, pollination, coastal protection, etc.). | `natcap.invest` (pip-installable; CLI + Python API; `invest run`, `invest list`, `invest validate`, `invest getspec`, `invest serve`, `invest export-py`; headless mode `-l` / `-y` / `-n`). | `adapt-via-python` | No official QGIS plugin. Pip-install `natcap.invest` in the worker image and call via subprocess or the Python API. Each model takes a datastack JSON. The `invest serve` Flask API could power an internal microservice if regional-scale workflow latency justifies it. |
| **Circuitscape-QGIS** (processing-circuitscape by alexbruy; landscape_connectivity by gdomrib) | Circuit-theory-based landscape connectivity. Random-walk / electrical-analog model of gene flow / animal movement across heterogeneous landscapes. Industry standard for connectivity planning. | Circuitscape.jl (Julia). | `out-of-process` | Two-layer bake: (1) Julia + Circuitscape.jl in a dedicated `connectivity-worker` container (~500 MB image overhead from Julia); (2) optionally the QGIS plugin as a desktop reference. Worker calls Circuitscape.jl via `julia -e 'using Circuitscape; compute(...)'` with INI configs; gives access to Omniscape.jl (omnidirectional connectivity) that the plugin does not wrap. Backs the `run_circuitscape_connectivity` atomic tool (FR-TA-2 conservation subsection). |
| **LinkScape (renamed TerraLink, effective 2026-03-18)** | Ecological corridor optimization and habitat connectivity using graph-based HDFM algorithms (Most Connectivity, Largest Network) for both raster and vector inputs. | Python + numpy + networkx (likely). | `yes-bake-into-image` | Install TerraLink (the LinkScape name is being retired). Headless-capable Processing provider; lightweight pure-Python plugin baked into the worker image. Complements Circuitscape: TerraLink for corridor-graph optimization, Circuitscape for current-flow connectivity. |
| **Animove (sextante_animove)** | Animal home-range analysis: MCP (Minimum Convex Polygon), Kernel Density UDs (href, LSCV, Scott, Silverman bandwidths), randomized home ranges and paths. | numpy + scipy (Processing provider). | `yes-bake-into-image` | Genuine Processing-provider plugin; baked into the worker image. Maintenance status is older (last meaningful update around QGIS 3.x port) — verify it loads against the current QGIS LTR before binding to it; if stale, the modern replacement is Trajectools / MovingPandas (see below). |
| **Trajectools (built on MovingPandas)** | Modern movement-analysis: split by gap, smoothing, speed, stops, trajectory simplification, etc. | MovingPandas (pip-installable, built on GeoPandas with Dask / Holoviz support). | `yes-bake-into-image` | This is the modern replacement for AniMove for trajectory-style data. Bake the plugin into the worker image; MovingPandas is also pip-installable system-wide if a richer API is needed beyond what Trajectools exposes through Processing. |
| **LecoS (Landscape Ecology Statistics)** | FRAGSTATS-style landscape metrics on classified rasters: 16+ class and landscape metrics (patch density, edge density, fragmentation indices, etc.). BatchOverlay tool computes metrics per polygon. | numpy + scipy.ndimage (Processing provider). | `yes-bake-into-image` | Classic Processing-provider plugin. Lightweight, headless-friendly, runs in `qgis_process`. Canonical landscape-metrics engine for GRACE-2 biodiversity reports. Verify against current QGIS LTR. |

### E.3 Movement-ecology and climate-overlay plugins (forward-looking, no engine binding yet)

| Plugin | Purpose | Underlying library | Integration mode | Notes |
|---|---|---|---|---|
| **QSSI (QGIS Summer Simmer Index)** | Thermal-stress / bioclimatic comfort index (SSI = f(T, RH)) from temperature + humidity rasters. Published 2026 in Int. J. Biometeorology. | Pure raster algebra. | `adapt-via-python` | Formula is trivial; we do not need the plugin. Reimplement as a single Processing algorithm or one-shot script in the worker. Useful as a built-in "thermal comfort" map layer in GRACE-2's future climate-overlay tooling (not in v0.1 scope). |
| **MoveBank** (no first-class QGIS plugin; integration is "download CSV/shapefile from Movebank, load into QGIS, use Temporal Controller") | Access to movebank.org animal tracking database (1,600+ species, MPI Animal Behavior). | `pymovebank` (REST API client) or direct HTTP; CSV / shapefile export is the common path. | `adapt-via-python` | Treat Movebank as a data SOURCE, not a plugin. A worker-side connector authenticates against movebank.org, pulls tracks, and writes them as FlatGeobuf layers per FR-CE-4. Visualization is the native QGIS Temporal Controller (3.14+), no plugin layer needed. |

### E.4 Image-bake strategy (three-tier)

The component diagram in §2.2 has three container surfaces that can host QGIS-plugin code; this appendix is opinionated about which surface each plugin belongs to.

- **Tier 1 — QGIS Server (FCGI, render-only).** Keep minimal. The QGIS Server image serves WMS / WMTS / WFS for the web-client basemap and project layers (per Decision B and FR-QS). Bake *only* rendering-relevant plugins — typically none of the engine wrappers in this appendix. LecoS and TerraLink could be optionally baked here if server-side computed landscape-metric tiles are wanted; the cleaner path is to compute in a worker and serve the resulting raster from GCS via QGIS Server. Image bloat at this tier directly impacts cold-start latency (NFR-P-1 region) and rendering throughput; keep it lean.
- **Tier 2 — PyQGIS worker (Cloud Run Jobs, project mutation + `qgis_process`).** This is the natural home for `yes-bake-into-image` plugins from §E.1 / §E.2 / §E.3 above. The image already ships PyQGIS + native QGIS Processing under `QT_QPA_PLATFORM=offscreen` (per Decision C and the job-0021 substrate); adding Processing-provider plugins is mostly an `apt-get` or `pip install` line in the Dockerfile. Plugins that bring heavy non-Python dependencies (Julia, Java, MPI) belong in Tier 3, not here.
- **Tier 3 — Dedicated solver containers (Cloud Run Jobs orchestrated by Cloud Workflows).** The `out-of-process` plugins from §E.1 / §E.2 — TELEMAC, MODFLOW family, Circuitscape.jl — each get their own image, isolated for license posture (NFR-L: GPL / LGPL solvers communicate only via GCS file artifacts, no in-tree linkage) and for runtime-dependency isolation (Julia and MPI runtimes do not pollute the PyQGIS worker image). The atomic-tool surface stays uniform: the worker invokes the dedicated container via Cloud Workflows per FR-CE-1 / FR-CE-2; the atomic tool returns a `LayerURI` or `ExecutionHandle` regardless of whether the underlying solver lives in Tier 2 or Tier 3.

### E.5 What this appendix does NOT do

- It does not bind the architecture to invoke any plugin. The Engine selection principle (§2.3) and the atomic-tool surface (§3.3 FR-TA-2) are the binding contracts; this appendix informs the per-plugin choice between plugin-backed Processing invocation and direct Python-library invocation.
- It does not register any tool. Tool registration is a code-level act per FR-AS-3 and FR-TA-3 docstring discipline; this appendix catalogs candidates.
- It does not commit to any plugin's continued maintenance. Plugins flagged with stale maintenance (Animove around QGIS 3.x) carry a "verify against current QGIS LTR" note; if a verify fails when the corresponding engine actually lands, the integration switches to `adapt-via-python` using the plugin's underlying library.

---

## Appendix F: Data-Source Tiering, Discovery-First Lane, Deferred Secrets UX

> *(Forward-looking — v0.3.16 amendment. Tier-1 sources are operationally
> active from M4+; Tier-2 lands per-source as a user-provisioned key is
> wired (e.g. Census ACS after sprint-07); Tier-3 deferred indefinitely.
> The Discovery-First lane is operationally available when the public-
> hazard-catalog tools land (FR-PHC). The `request_secret` UX in §F.3 is
> deferred indefinitely until explicit user direction.)*

### F.1 Data-source tiering

The atomic-tool data fetchers in §3.3 FR-TA-2 draw from many public APIs. They are tiered by credential requirement so the agent has a default ordering when more than one source can answer the same question.

| Tier | Description | Routing rule | Examples |
|---|---|---|---|
| **1** — key-free public | No credential required; data is open. | Default for every atomic-tool fetcher when more than one source is available. | USGS 3DEP (DEM), **NLCD / MRLC (landcover — Tier 2 OGC WCS GetCoverage per §F.1.1; v0.3.18 amendment originally claimed STAC, live verification in job-0037 + job-0044 resolved to WCS as the canonical-bytes path)**, ESA WorldCover (STAC), NHDPlus HR (river geometry), NOAA Atlas 14 PFDS (precipitation frequency), **WorldPop (Tier 4 region-download per §F.1.1 — 50 MB 1km Aggregated COG per country via direct REST; v0.3.16 prose implied STAC + 100m but live ecosystem delivered neither — WorldPop Hub STAC returns 404 and the 4 GB 100m product's server returns HTTP 200 not 206 for Range requests so /vsicurl/ windowed reads don't work)**, Microsoft Open Maps Building Footprints, OSM Overpass, NOAA NHC ATCF (hurricane tracks), NOAA CO-OPS (tide gauges), NEXRAD Level II (Unidata S3), MRMS QPE (NOAA Open Data), USGS NWIS (streamflow), FEMA NFHL (flood zones), USFS Wildfire Hazard Potential, USDA Wildfire Risk to Communities. |
| **2** — key-required, free | Requires a one-time per-deployment (M4+) or per-user (deferred, see §F.3) provisioned API key, but the data itself is free. | Opt-in. Either the deployment ops sets the key once in Secret Manager, OR (post-§F.3) the user provisions per-user. | **US Census ACS B01003 (population, tract-level — Tier-2 alternative to WorldPop)**, NewsAPI free tier, NOAA api.weather.gov keyed endpoints, NASA Earthdata Login (for some GES DISC products), USGS EarthExplorer (some collections). |
| **3** — paid / commercial | Requires a paid subscription or per-request billing. | Opt-in only with explicit user consent + a future cost-surfacing follow-up (Invariant 9 currently bans cost-theater; expansion to "user-acknowledged paid sources" is a separate decision). | Mapbox geocoding (paid tier), PRISM 800 m subscription tier, premium news APIs (Reuters, Bloomberg). |

**Tier-1 preference rule.** When an atomic tool offers more than one source for the same data class, the Tier-1 source is the default and the Tier-2 / Tier-3 source is opt-in via an explicit parameter (e.g. `dataset="acs_2022"`, `source="mapbox"`). The per-tool docstring (FR-TA-3 metadata discipline) names the Tier-1 default and lists the available alternatives + their tier.

**WorldPop as the `fetch_population` Tier-1 default.** Updates the v0.1 default: `fetch_population(bbox)` defaults to WorldPop (Tier 1, no key). Live-verified substrate per job-0037: **1km Aggregated COG via direct WorldPop REST + country-level download + local windowed clip** (NOT 100m, NOT STAC — both substrates implied by v0.3.16 prose were unavailable). Vintage default `worldpop_2020` (R2018A/R2020 tree). Units are **people per 1km cell** — downstream zonal-stats consumers must aggregate per cell, not assume per-pixel-area normalization. `fetch_population(bbox, dataset="acs_2022")` opts into the Census ACS B01003 tract-level Tier-2 source when the deployment has a Census key provisioned in Secret Manager. Job-0033's prior preference (ACS as default, WorldPop deferred) is reversed by this amendment — the no-friction default better serves new users; ACS opt-in better serves precision use cases.

**Per-deployment vs per-user provisioning of Tier-2 keys.** Until §F.3 lands, Tier-2 keys live at deployment scope in Secret Manager (one Census key per deployment, shared by all users). When §F.3 lands, Tier-2 keys can be provisioned per-user; deployment-scope provisioning remains as a fallback for ops-managed sources.

### F.1.1 Access pattern tiering — the "data stores in the wild" problem *(Forward-looking — v0.3.17 amendment. v0.1 operates within the 4-tier happy-path enumeration below; agent-mediated discovery + adaptation to uncatalogued patterns is a v0.2+ capability and is captured as OQ-AT-2 below.)*

**The principle.** A hazard-modeling agent in operation will encounter geospatial data **wherever it lives** — across an arbitrary variety of provider conventions, access patterns, and protocols. The architecture must accommodate that variety rather than assume a uniform interface. The §F.1 credential tiering is one axis (which providers need a key); access pattern tiering is the **orthogonal axis** (how do we actually retrieve bytes once we know the source).

**v0.1 happy-path enumeration — four access tiers.** Every data-fetch atomic tool is implemented against exactly one tier, chosen at implementation time by the engineer based on a live verification of the upstream provider. The tier is recorded in the tool's FR-TA-3 docstring "Access pattern" line and (forward-looking — see schema note at end of §F.1.1) in an `AtomicToolMetadata.access_tier` field when the schema gains it.

| Tier | Pattern | Byte-shape per call | Cache discipline | Example providers (v0.1) |
|---|---|---|---|---|
| **1** — STAC + COG | STAC catalog (`/api/stac/v1/`) with Cloud-Optimized GeoTIFF backend; bbox-aware item query; HTTP Range supported | byte-window (≤ MB per fetch) | single-stage cache key `(source, bbox-quantized, vintage)` per FR-DC-3 | NASA / USGS via Microsoft Planetary Computer (some collections); STAC-hosted ESA WorldCover; future NASA Earthdata STAC collections |
| **2** — OGC service (WMS/WMTS/**WCS**/WFS) | Provider hosts a query-rendering OR coverage-service interface; layer reference IS the URL for visualization-only WMS, OR the canonical bytes for model inputs come from WCS GetCoverage | per-tile render OR per-bbox coverage (varies) | for visualization-only WMS: **layer not cached to GCS** — URL is the cached reference; QGIS Server proxies / re-renders downstream. For canonical-bytes WCS used by model inputs: same single-stage `(source, bbox-quantized, vintage)` cache key as Tier 1/3 since the returned bytes are canonical raster data. | FEMA NFHL (Flood zones via WMS), USFS Wildfire Hazard Potential (WMS), NOAA SLOSH outputs (WMS), USGS National Seismic Hazard Map (WMS) — the §F.2 Discovery-First lane sources. **NLCD via MRLC GeoServer WCS 1.0.0 GetCoverage** for canonical NLCD class integers (per job-0044 hotfix — WMS GetMap returns palette-encoded indices not canonical bytes; WCS is the model-input substrate). |
| **3** — Direct HTTPS file with Range support | Provider exposes raw GeoTIFF / FlatGeobuf / NetCDF URLs; HTTP server honors Range requests (returns `206 Partial Content`); GDAL `/vsicurl/` windowed reads work | byte-window (≤ MB) | same as Tier 1 — single-stage `(source, bbox-quantized, vintage)` | USGS 3DEP DEM tiles, NHDPlus HR FlatGeobuf, some NOAA Atlas 14 endpoints, MS Building Footprints PMTiles |
| **4** — Region download + local clip | Provider exposes only whole-region / whole-country file URLs; no Range support OR no bbox-aware index; full-file download required followed by local windowed clip | full-file (MB–GB) per region, then byte-window per clip | **two-stage cache** per OQ-37-COUNTRY-FILE-CACHING-STRATEGY: country file at `cache/static-30d/<source>/_regions/<region>.<ext>` (downloaded once, shared across all clips inside that region); per-call clip at `cache/static-30d/<source>/<hash>.<ext>` | WorldPop (job-0037 substrate; 50 MB 1km Aggregated per country via direct REST — STAC not available, /vsicurl/ Range requests not honored), some legacy USGS products, older NOAA gridded archives |

**Tier-selection discipline (v0.1).**

- The tier is chosen at tool-implementation time, NOT at runtime. A tool is implemented against one specific tier; runtime fallback between tiers is **deferred indefinitely** (OQ-AT-1 below).
- The tier choice requires live verification of the provider — not just "does the provider claim to publish STAC." Specifically: a STAC catalog must be live AND its backend must support HTTP Range AND the COG headers must be valid. Otherwise the source falls to Tier 3 (if direct HTTPS with Range) or Tier 4 (if not).
- The tier is recorded in the tool's docstring per FR-AS-3 / FR-TA-3 metadata discipline. When the `AtomicToolMetadata.access_tier` schema field lands (forward-looking — see note at end of §F.1.1), the tier ALSO populates that field and is validated at registration per FR-CE-8.
- Tier 2 (OGC services) is structurally different: the cache shim is bypassed; the layer reference IS the URL; QGIS Server is the rendering substrate. This is the §F.2 Discovery-First lane's primary access pattern and lives outside the FR-DC cache architecture for layer bytes. (Metadata about the OGC source IS still cached per FR-DC-2 `semi-static-7d` — capability lists, layer indices, etc.)

**Forward-looking — Agent-mediated data-source discovery and adaptation (v0.2+).**

The v0.1 enumeration is exhaustive for the providers we anticipate registering tools against. But the design principle is broader: **the agent shall be able to handle an arbitrary new data source it encounters at runtime** — surfaced via user request, web research per FR-AS-9 capability discovery, or a §F.2 catalog amendment — and characterize its access pattern without engineer intervention.

The forward-looking capability mirrors FR-AS-9's solver discovery pattern, applied to data sources:

- **Discovery** (Level 1b analog): user query mentions a data source not in any registered atomic tool. Agent searches the web / official catalogs, finds the provider's access documentation.
- **Characterization**: agent probes the source to determine its tier — `HEAD` request to check for `Accept-Ranges: bytes`; `GET` to a STAC root URL to check for `/api/stac/v1/`; check for OGC `GetCapabilities` response shape. The agent records the discovered tier in a dynamic source registry.
- **Adaptation**: agent constructs a one-shot fetch using the discovered tier — Tier 1 STAC item query, Tier 2 WMS GetMap, Tier 3 `/vsicurl/` windowed read, or Tier 4 region-download fallback. The fetch routes through the same cache shim per FR-CE-8; the dynamic source gets an auto-registered `AtomicToolMetadata` (or equivalent) with the discovered tier + TTL class.

This capability requires several things the v0.1 architecture does NOT yet have:
- Agent-side autonomous network probing discipline (timeout, retry, error classification — needs Invariant 8 cancellation hooks).
- Dynamic tool registration at runtime (current `@register_tool` decorator runs at import; the FR-CE-8 fail-fast validation assumes startup-time discovery).
- A dynamic source registry that survives session restarts (likely a new MongoDB collection per Decision F).
- User-facing surfacing of "the agent discovered a new source" so the user can verify provenance per Decision M (claim provenance).

**Status: DEFERRED to v0.2+.** OQ-AT-2 below captures the question. v0.1 operates within the 4-tier happy-path enumeration with engineer-curated, implementation-time tier selection.

**Forward-looking schema note.** `AtomicToolMetadata` (`grace2_contracts.tool_registry`) does NOT currently carry an `access_tier` field. The forward-looking schema bump that adds it is intentionally deferred — for v0.1 the tier is recorded in the tool's docstring per FR-AS-3 / FR-TA-3. The schema bump lands in a future schema sprint when (a) downstream consumers actually need to introspect the tier (e.g., a tool-router that picks differently based on tier), or (b) the v0.2+ agent-mediated discovery capability above starts auto-populating the field for newly-characterized sources. Until then, the access tier is documentation-discipline, not enforced.

**Open Questions from §F.1.1.**

- **OQ-AT-1: Runtime fallback between access tiers** *(TENTATIVE: defer indefinitely)*. Should an atomic tool whose primary tier (e.g., Tier 1 STAC) goes down attempt a secondary tier (e.g., Tier 3 direct HTTPS) automatically? Adds latency + complexity to every fetch path; v0.1 prefers fail-fast `UPSTREAM_API_ERROR` so the agent's FR-AS-11 clarification surface decides next steps. Revisit if upstream reliability becomes a load-bearing problem (post-M9).
- **OQ-AT-2: Agent-mediated data-source discovery and adaptation** *(TENTATIVE: v0.2+)*. The forward-looking capability described above. Decision affects: agent capability surface (new FR-AS-* requirements), MongoDB schema (new dynamic-source-registry collection), Cloud Workflows orchestration (autonomous probing has timeout + retry needs that look like a mini-workflow), security posture (NFR-S — agent-initiated outbound requests to URLs from user queries are an attack surface; needs SSRF guardrails). Same shape as OQ-8 / OQ-9 / OQ-11 (forward-looking, decide before the capability ships).

---

### F.2 Discovery-First lane (public hazard catalog)

For many user queries the right answer is to surface an authoritative pre-computed hazard layer rather than to run a solver. This is the Discovery-First lane — already scoped in §3.5.5 FR-PHC and FR-AS-9 Level 1b, formalized as a v0.1 design principle here.

**Routing rule.** When the user asks "show me X" and a curated catalog entry exists for X, the agent invokes the discovery workflow (`show_hazard_layer` per FR-TA-1) which uses `hazard_catalog_search` + `fetch_public_hazard_layer` + `summarize_layer_in_bbox` (FR-TA-2) to retrieve and characterize the layer. No solver runs; no API key needed; layer renders through QGIS Server within seconds.

**Catalog candidates** (Tier-1 per §F.1, key-free):

| Source | Hazard domain | Tier | Forward-looking workflow |
|---|---|---|---|
| FEMA NFHL | Flood zones (SFHA, BFE) | 1 | `show_hazard_layer(topic="flood_zone", location=…)` |
| USFS Wildfire Hazard Potential | Wildfire | 1 | `show_hazard_layer(topic="wildfire_risk", location=…)` |
| USDA Wildfire Risk to Communities | Wildfire (community-scale) | 1 | `show_hazard_layer(topic="wildfire_community", location=…)` |
| NOAA SLOSH | Storm-surge inundation | 1 | `show_hazard_layer(topic="storm_surge", location=…)` |
| USGS National Seismic Hazard Map | Seismic shaking | 1 | `show_hazard_layer(topic="seismic", location=…)` |
| NOAA Storm Events DB | Historical event records | 1 | `hazard_catalog_search(query="historical events", bbox=…)` |
| USGS Earthquake Catalog | Historical seismic events | 1 | `hazard_catalog_search(query="quakes since 2000", bbox=…)` |
| NOAA Climate Data Online | Gridded climate products | 1 | `show_hazard_layer(topic="climate", location=…)` |

Cache class per FR-DC-2: `static-30d` for the layer geometries (catalog re-issues are annual or slower); `semi-static-7d` for the catalog index when the source publishes weekly.

**Modeling vs Discovery — agent intent.** Discovery answers "where is the risk?" Modeling answers "how bad will it be under conditions X?" Both are valid; the agent's tool selection (per Decision G, two-layer architecture; the LLM's choice of which workflow or atomic tool to invoke is the classification) picks. A combined response is also valid — e.g. discover the FEMA SFHA overlay for context, then model a specific event over it. The agent shall prefer Discovery when the user query maps cleanly to an existing catalog entry (faster, no solver cost), and offer Modeling as a follow-up ("would you also like to model a specific scenario?") when appropriate.

**Implementation status.** `hazard_catalog_search` / `fetch_public_hazard_layer` / `summarize_layer_in_bbox` were defined in FR-TA-2 but deferred from M4 (sprint-06). Recommended landing alongside M5 (sprint-07) SFINCS, or as a fast-follow mini-sprint after M5 — landing the Discovery atomic tools requires only the public-hazard-catalog content (`public_hazard_catalog.yaml`, currently NOT YET CREATED — engine owner) plus straightforward HTTP / vector-tile retrieval. No new substrate.

### F.1.2 Trust model for source discovery — three-mode framing *(v0.3.18 amendment; binding for v0.2+ catalog substrate, sprint-08 scope.)*

**The principle (refined from §F.1.1 OQ-AT-2).** The "data stores in the wild" capability (§F.1.1) requires a **trust model** that bounds where the agent can fetch from. Naïve autonomous discovery (probe any URL the agent encounters) creates an SSRF attack surface, provenance ambiguity, and license-attribution risk. Naïve catalog-only operation (no agent-mediated growth) means the catalog calcifies — new authoritative data products dropping don't reach users until an engineer manually curates them. The architecture splits the trust surface into three explicit modes, with the broad uncertain-source case deferred until the discipline matures.

**Mode 1 — Catalog-mediated (PRIMARY; v0.1 binding for sprint-08).** The curated `public_data_source_catalog.yaml` (and its MongoDB-collection successor `catalog_entries` per Decision F) is the single source of truth for vetted endpoints. Every entry is **research-driven and labeled** at curator time with:

- `id`, `name`, `description` — identification
- `url(s)` — primary endpoint + alternative mirrors when they exist
- `access_tier` per §F.1.1 (STAC, OGC service, HTTPS+Range, region-download)
- `ttl_class` per FR-DC-2
- `source_class` per FR-DC-1
- `credential_tier` per §F.1 (key-free, key-required, paid)
- `license`, `citation`, `vintage`, `last_verified`
- `status` — `active`, `deprecated`, `user_proposed_pending_curator_review` (see Mode 2)
- **"How to use" metadata** — invocation examples, parameter constraints, known quirks (e.g., "WorldPop returns HTTP 200 not 206 for Range requests — use region-download tier; specify country in `params.iso3`"). This labeling is the difference between a sterile URL list and an actionable catalog.

Atomic tools that consume the catalog (sprint-08 scope, FR-TA-2 additions):

- `catalog_search(topic, location?, source_filter?) → list[CatalogEntry]` — agent queries the catalog by domain (terrain, hydrology, weather, building, population, landcover, hazard, etc.) + optional spatial + filter. Returns ranked matches.
- `catalog_fetch(entry_id, params) → LayerURI | dict` — generic fetcher that dispatches to the entry's `access_tier` (Tier 1 STAC query / Tier 2 OGC WMS GetMap / Tier 3 `/vsicurl/` windowed read / Tier 4 region download + clip). Cache shim discipline per FR-DC-3 / FR-CE-8 applies; entry's `ttl_class` + `source_class` populate the cache key.

The existing hardcoded atomic tools (`fetch_dem`, `fetch_landcover`, `fetch_population`, `geocode_location`) coexist with catalog-driven access. Hardcoded tools remain the **friendly per-domain shortcuts** for the canonical sources (3DEP for DEM, NLCD for landcover); the catalog covers the long tail (state GIS portals, regional gauge networks, alternative providers). At engineer discretion, hardcoded tools may later be reimplemented as catalog-driven syntactic sugar (post-v0.1 consolidation).

**Mode 2 — Offer-to-add on `.gov` and `.edu` (v0.2+; bounded growth path).** When the agent encounters a candidate URL during research or user-query interpretation that is (a) not in the catalog AND (b) hosted on `.gov` or `.edu`, the agent shall NOT autonomously fetch. Instead:

1. The agent performs a **conformity probe** — HEAD request (check `Accept-Ranges`, content-type, TLS cert org subject), STAC root check (`<base>/api/stac/v1/` or `<base>/stac/catalog.json`), OGC `GetCapabilities` check, COG header inspection. Each probe respects the SSRF guardrails below.
2. The agent emits an `offer-catalog-addition` envelope (NEW Appendix A amendment — sprint-08 schema scope):
   ```jsonc
   {
     "type": "offer-catalog-addition",
     "id": "01K…", "ts": "…Z", "session_id": "01K…",
     "payload": {
       "request_id": "01K…",
       "url": "https://example.gov/data/foo",
       "discovered_via": "user-query | web-research | …",
       "probe_findings": {
         "tls_cert_org": "U.S. Department of …",
         "access_tier_inferred": 1,                 // §F.1.1 tier
         "supports_range_requests": true,
         "stac_root_found": false,
         "ogc_capabilities_found": true,
         "license_observed": "Public domain (U.S. Federal data)",
         "content_type": "application/json",
         "last_modified_header": "…"
       },
       "suggested_catalog_entry": {
         "id": "femanflp-discharge-…",
         "name": "FEMA NFHL discharge stations",
         "access_tier": 2,
         "ttl_class": "semi-static-7d",
         "source_class": "flood_zone",
         "credential_tier": 1,
         "license_claim": "Public domain (US Federal)",
         "how_to_use": "OGC WFS GetFeature; bbox in EPSG:4326; … "
       },
       "ttl_seconds": 600
     }
   }
   ```
3. The client renders a **dedicated review modal** (mirrors §F.3 secret-form pattern — popup, focus-trapped, separate from chat envelope) showing the URL + probe findings + the suggested catalog entry. The user accepts, rejects, or edits.
4. On accept, the agent writes the entry to the catalog with `status: "user_proposed_pending_curator_review"`; the catalog query then includes this entry but a curator review is required (out-of-band) to flip status to `active`. This keeps the growth path bounded — entries that fail curator review are removed without ever having been part of the "active" surface.
5. On reject, the agent falls back to an alternative cataloged source for the user's query OR surfaces failure ("I couldn't find an active source for your query; the candidate I found at <url> was declined").
6. All offer-to-add events are recorded in an audit log (MongoDB collection `catalog_audit_log` per Decision F): URL, user, classification, probe findings, accept/reject, eventual curator-review outcome. Provenance per Decision M.

**Why `.gov` and `.edu`?** Both have registry-controlled policing (DotGov Registry / EDUCAUSE) sufficient to bound the agent's autonomous-probing surface. False positives (bad data on a `.gov` URL — press releases vs. structured data, deprecated endpoints, contractor content) are caught by the conformity probe — the OGC GetCapabilities check rejects a press release outright. Cross-confirmation with the existing catalog is the additional defense: if a `.gov` URL is wildly inconsistent with the catalog's other entries for the same domain (license, vintage, format conventions), the user's review modal surfaces that mismatch.

**Mode 3 — Anything else: DEFERRED INDEFINITELY (per user direction 2026-06-07).** Non-`.gov`/non-`.edu` URLs the agent encounters (general `.com`, `.org`, country TLDs, IP addresses, etc.) shall NOT be probed and shall NOT trigger an `offer-catalog-addition` flow. When the agent encounters such a URL during research or query interpretation, it narrates:

> "I found a candidate source at `<url>` that may be relevant, but it doesn't meet the v0.1 trustworthiness criteria for autonomous use. You can review it manually and add it to the catalog via the curator CLI if it's appropriate."

This is the muddier case the trust signal is hardest to read on — `.org` includes both OpenStreetMap and questionable advocacy sites; `.com` includes Microsoft Planetary Computer (excellent) and arbitrary commercial sites. The curator-only path keeps these sources reachable but **requires explicit human curation** rather than agent judgment. Revisit when:

- Decision M provenance discipline is fully operational across the agent
- User-identity machinery from §F.3 lands (per-user catalog adds become accountable)
- A more nuanced trustworthiness signal is implementable (cross-confirmation against NASA CMR + Microsoft Planetary Computer + USGS ScienceBase + academic citation graph)

OQ-AT-3 below captures the question.

**Curator-side validation criteria (applies to Mode 1 hand-curated AND Mode 2 user-accepted adds before they flip to `status: "active"`):** four orthogonal axes — domain provenance (TLD policing + cert org subject), protocol conformity (response matches a known geospatial standard), metadata sufficiency (declared license + citation + vintage), cross-confirmation (entry referenced by ≥1 of: existing catalog entries, SRS prose, vetted external aggregator like NASA CMR / Microsoft Planetary Computer / USGS ScienceBase). All four ideally; ≥2 of 4 + curator override is the minimum bar.

**SSRF guardrails (infra-side, NFR-S concern; binding for all modes):**

- Egress allowlist enforcement at the agent-service VPC perimeter (Cloud Run egress through VPC connector with explicit egress targets per Decision E).
- Private IP block: 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 127.0.0.0/8, 169.254.169.254/32 (GCE metadata) — agent egress to any of these returns a typed error per FR-AS-11.
- DNS rebinding defense: re-resolve domain at fetch time; fail closed if resolved IP differs from probe-time resolution OR enters a blocked range.
- Max response size: 100 MB default for probe responses; 4 GB default for cataloged Tier-4 region-download fetches.
- Per-domain rate limit: 10 requests/min default; per-entry override allowed.
- All outbound network operations audit-logged.

**Open Questions from §F.1.2.**

- **OQ-AT-1 (carried from §F.1.1)** — Runtime fallback between access tiers within a single catalog entry. Deferred. Out of scope for v0.2+ catalog substrate.
- **OQ-AT-2 (rescoped from §F.1.1)** — Agent-mediated data-source discovery. **Rescoped:** the original "agent crawls any URL" framing is replaced by Mode 2 (bounded to `.gov`/`.edu` with mandatory user surfacing + conformity probe + curator review pipeline). The narrower Mode 2 lands in sprint-08. Closed-by-rescoping at v0.3.18.
- **OQ-AT-3 (NEW)** — Mode 3 / wider trustworthiness signal. The deferred path: how do we eventually permit autonomous probing of non-`.gov`/`.edu` sources without the SSRF / provenance / license-attribution exposure? Requires the three prerequisites listed under Mode 3 above. Defer indefinitely until the prerequisites mature. Forward-looking marker pattern matching OQ-8 / OQ-9 / OQ-11.

**Sprint-08 scope implication.** Land Mode 1 (catalog substrate + Sonnet-driven 30–60-entry seed YAML + `catalog_search` / `catalog_fetch` atomic tools + generic Tier-2 OGC adapter + SSRF guardrails) as the headline. Mode 2 (offer-to-add on `.gov`/`.edu`) is a fast-follow within sprint-08 if scope permits, or sprint-09 if SSRF guardrails take longer than expected. Mode 3 remains deferred.

---

### F.3 Deferred Secrets UX (`request_secret` envelope) — pop-up form, NOT inline chat

**Status:** deferred indefinitely until explicit user direction. NOT in v0.1, NOT in v0.2. Requires M6+ user-identity machinery as prerequisite (per-user Secret Manager namespacing depends on user identity). This appendix documents the architecture so it does not get reinvented when the time comes.

**Design intent.** When an atomic tool needs a Tier-2 / Tier-3 credential that the deployment has not provisioned, the agent should be able to ask the user to provide one *without the secret ever transiting the WebSocket chat envelope* (which is logged to MongoDB per Decision F, where credentials must never land).

**Architecture sketch:**

1. The atomic tool catches a "missing credential" upstream error (e.g. Census ACS returning HTTP 200 with the "Missing Key" HTML body) and invokes the (NEW, forward-looking) `request_secret(secret_name, signup_url, description) → secret_handle` atomic tool.
2. The agent emits a (NEW, forward-looking) `secret-request` envelope through the existing FR-AS-7 WebSocket emission seam. Appendix A amendment when this lands. Payload shape:
   ```jsonc
   {
     "type": "secret-request",
     "id": "01K…",
     "ts": "…Z",
     "session_id": "01K…",
     "payload": {
       "request_id": "01K…",
       "secret_name": "census_acs_api_key",
       "signup_url": "https://api.census.gov/data/key_signup.html",
       "description": "Census ACS B01003 population queries require a free API key. Click the link, sign up by email, then paste the key into the pop-up below.",
       "ttl_seconds": 1800
     }
   }
   ```
3. The web client renders a **dedicated pop-up modal** (NOT an inline chat input; see "Pop-up over inline" below). The pop-up shows the signup link as a clickable URL that opens in a new tab + a password-field input + a "Submit" button + a "Cancel" button. The modal is focus-trapped and dismissable; the chat scrollback stays visible behind it but is not interactive while the modal is open.
4. The user clicks the signup URL in a new tab, completes the signup flow off-app (typically email verification → key arrives in inbox), returns to the GRACE-2 tab, and pastes the key into the password field.
5. The pop-up form POSTs the secret directly to a small "secret-receiver" Cloud Function (NEW, forward-looking infra surface) over HTTPS. **The POST is a separate HTTP request, NOT the WebSocket.** The Cloud Function writes the secret to per-user Secret Manager at the path `users/<user_id>/secrets/<secret_name>` and returns a confirmation token.
6. The client emits a `secret-response` envelope (NEW, forward-looking) carrying the confirmation token (NOT the secret value):
   ```jsonc
   {
     "type": "secret-response",
     "id": "01K…",
     "ts": "…Z",
     "session_id": "01K…",
     "payload": {
       "request_id": "01K…",
       "status": "stored",   // or "cancelled" or "timeout"
       "confirmation_token": "..."
     }
   }
   ```
7. The agent receives `secret-response`, retries the atomic tool — which now reads the secret from Secret Manager via the agent-runtime SA's ADC and proceeds normally.

**Pop-up over inline — deliberate choice.** Two reasons the pop-up modal is preferred over a styled inline chat input:

- **Wire-level isolation.** The chat envelope (`user-message` etc.) is logged to MongoDB per Decision F. Routing the secret through a separate HTTPS POST (not the WebSocket) keeps the secret out of every chat-history persistence path. An inline chat input that "looks like a password field" would still send the typed value through the chat envelope unless the client implements special-case routing — which is more code and more subtle bug surface than just rendering a separate modal.
- **User context discrimination.** Visual separation reinforces to the user that they are typing into a credential surface, not a conversation. Reduces the chance of accidentally typing the key into the main chat box. Accessibility per FR-WC-4 (focus-trap, dismissable, screen-reader announces "secret input modal").

**Prerequisites that must land before §F.3 is implementable:**

- M6+ user-identity machinery (anonymous-session-scoped secrets are an option for v0.1 but the long-term value of the secrets UX is tied to per-user persistence, which means user accounts).
- A small `secret-receiver` Cloud Function with the appropriate IAM bindings (writes to `users/<user_id>/secrets/*` paths in Secret Manager; reads only from a CSRF-protected origin).
- Per-user Secret Manager namespacing convention (`users/<user_id>/secrets/<secret_name>`; lifecycle policies; rotation discipline).
- Appendix A amendment to add the `secret-request` and `secret-response` envelope shapes.
- Web client pop-up modal component (NEW; lives in `web/src/SecretRequestModal.tsx` or similar).
- Cancel + timeout behavior — the agent must handle `status: "cancelled"` and `status: "timeout"` from the client by emitting a "tool failed" step in the pipeline-state per Appendix A.7 and the D.6 `PipelineStepSummary` error fields landed in job-0030.

**Out of scope for §F.3 even when it lands.** The agent does NOT attempt to drive the signup flow itself (headless browser puppetry against third-party signup forms is brittle, breaks on captchas / email verification / ToS acceptance, and is v0.2+ at the earliest — see §5 Out-of-Scope deferred capabilities). The user is in the loop for the off-app signup step; the agent only handles surfacing the link, collecting the resulting key, and retrying the failed tool.

**Until §F.3 lands.** Tier-2 keys are deployment-scope: one key per deployment, provisioned by the operator via OpenTofu + Secret Manager (mirrors the job-0014 + job-0031 patterns). Atomic tools read from Secret Manager via the agent-runtime SA's ADC. When a Tier-2 fetch fails with "missing key" pre-§F.3, the failure surfaces as an `UPSTREAM_API_ERROR` per the FR-AS-11 clarification surface; the agent narrates the operator-action needed ("the deployment doesn't have a Census API key provisioned; the operator needs to provision one — here is the signup link: …") rather than soliciting input from the user directly. This is the M4 / sprint-06 fallback behavior already in place.

---

## Appendix H: Authentication and Users

> *(Forward-looking — v0.3.22 amendment. **Decoupled appendix** per user direction 2026-06-08 ("decoupled enough to be an appendix"). v0.1 operates with anonymous-friendly UX; Firebase Auth integration lands in Wave 2 of sprint-12-mega and downstream M6+ identity work. This appendix pins the identity-provider choice, the user→Case ownership rule, the anonymous→authenticated upgrade flow, the tier-claim discipline, the session-validation contract, the secrets scoping rule, and the architectural decision behind the choice. Implementation refinements (Wave 2 schema fields, agent verification middleware, web UX, Identity Platform IaC) land in the binding sprints; this appendix pins the contract.)*

**Where Appendix H sits.** Appendix H is **decoupled** from the operational MongoDB substrate (Appendix D), the WebSocket protocol (Appendix A), and the data-source / secrets UX appendix (Appendix F §F.3). Identity is a transverse concern — every persisted document carries an `owner_user_id`, every WebSocket connect verifies a Firebase ID token, every per-user secret namespace keys off the `users._id`. Rather than scattering identity rules across A / D / F, this appendix is the single authoritative reference.

**Naming convention.** UI label "**User**"; storage collection name `users` (per the FR-MP-5 / FR-MP-6 nomenclature pattern where UI labels can diverge from storage labels — e.g. "Case" ↔ `projects`). The two are interchangeable in narrative; code says `User` / `UserDocument`.

---

### H.1 Identity provider choice — Firebase Authentication (managed SaaS, GCP-native)

**Selection.** GRACE-2 uses **Firebase Authentication** as the identity provider, with **GCP Identity Platform** as the enterprise-SKU upgrade path. Firebase Auth + Identity Platform is the same product family — Identity Platform is the renamed/extended SKU that adds enterprise SLA, multi-tenancy, customer-managed encryption keys (CMEK), SAML/OIDC enterprise SSO, and audit logs to the base Firebase Auth surface. A v0.1 Firebase Auth project can be upgraded to Identity Platform without re-keying users, re-issuing tokens, or changing client SDK calls — the upgrade is a project-level configuration flip in GCP console.

**What Firebase Auth provides (v0.1 surface).**

- **Email/password sign-in** — primary v0.1 authenticated mode.
- **OAuth sign-in providers** — Google, GitHub, Microsoft, Apple (zero-config; managed by Firebase).
- **Anonymous sign-in** — Firebase issues a stable anonymous user ID + ID token without requiring credentials. v0.1 landing page UX uses this so users can begin a Case without an account (see H.3).
- **Email-link sign-in** (magic-link) — passwordless flow; useful for the anonymous→authenticated upgrade.
- **ID tokens (JWT)** — short-lived (1 hour) signed JWTs the client passes to the agent on WebSocket connect; the agent verifies them via the `firebase_admin` Python SDK (see H.5).
- **Custom claims** — arbitrary key/value pairs the backend writes onto a user's ID token via the Admin SDK (`firebase_admin.auth.set_custom_user_claims`); used for tier gating (see H.4).
- **Account linking** — `linkWithCredential(...)` on the client takes an anonymous-session user and binds it to an email/OAuth credential without losing the original `uid`; this preserves Case ownership through the upgrade (see H.3).

**Why Firebase Auth over alternatives.**

1. **GCP-native, no separate identity vendor.** Decision E (§2.1) pins GRACE-2 on Google Cloud throughout; Firebase Auth lives inside the same GCP project, billing account, and IAM surface as Cloud Run / Workflows / Atlas-managed-by-MongoDB-via-GCP. No additional vendor onboarding, no separate billing setup, no separate SDK key management. Alternatives (Auth0, AWS Cognito) would each introduce a separate vendor, a separate auth-server domain, and cross-cloud network paths (Cognito particularly: AWS → GCP bridge).
2. **Anonymous-friendly by construction.** Firebase Auth's anonymous-sign-in + `linkWithCredential` upgrade flow is the cleanest fit for the v0.1 landing-page UX where users start a Case without an account (Memory rule: "inform user before persistent action" — anonymous → upgrade prompt at first save/share). Auth0 and Cognito both support anonymous flows but with more friction (Cognito requires Identity Pool; Auth0 requires custom JWT issuer).
3. **Custom-claims for tier scoping without contract changes.** Identity Platform's custom-claims surface is the native primitive for free/pro/enterprise tier gating (H.4). Tier checks run on the agent side against the verified JWT — no separate "billing" service required for v0.1.
4. **Scales to Identity Platform enterprise SKU without re-keying.** Future enterprise customers who need SLA, CMEK, SAML SSO, or audit logs get those by flipping the GCP project from Firebase Auth → Identity Platform. No data migration, no token re-issuance, no contract change. Auth0 / Cognito both require sales engagement + tier upgrades for equivalent surfaces, and Auth0's enterprise tier is significantly more expensive at v0.2 scale.
5. **Decoupled from compute.** Firebase Auth is a managed service; outage on Firebase Auth does not crash the agent, the QGIS Server, or the SFINCS engine. Only login + token-issuance is affected; existing sessions with valid ID tokens continue until token expiry (1 hour default).

**Why not the alternatives (one-line rationale each).**

- **Auth0** — separate vendor, separate billing, separate token-issuer domain; identity becomes a cross-cloud dependency. Strong product, wrong cloud-vendor seam for GCP-pinned GRACE-2.
- **AWS Cognito (via cross-cloud bridge)** — adds AWS-account onboarding, Cognito Identity Pool complexity, IAM federation across clouds; identity becomes the most operationally complex thing in the stack.
- **Custom OIDC server (Keycloak / Dex / Ory)** — self-hosted; SLA, key rotation, abuse mitigation, brute-force protection, SMTP-for-password-reset all become GRACE-2's problem. Indefensible v0.1 scope creep.
- **No-auth-at-v0.1** — feasible only with NO persistent UX; the moment Cases land (sprint-12-mega), single-owner persistence demands a stable user identity. Anonymous Firebase users satisfy this at zero credential friction.

### H.2 User → Case ownership

**Ownership rule.** Every Case (a `projects` document per FR-MP-5 / FR-MP-6) carries an `owner_user_id` field — a ULID that points at a `users._id`. The owner is set at Case creation time (FR-MP-6) and is **immutable for v0.1** — ownership transfer is a Wave 2+ feature (deferred; see "Future" below).

**Enforcement seam.** The MongoDB MCP layer (FR-MP-1 — agent's persistence interface) enforces the ownership filter at query time: `list_cases_for_user(user_id)` returns only `projects` where `owner_user_id == user_id`, and `get_case(case_id, requesting_user_id)` returns the document only if its `owner_user_id` matches. The enforcement lives in the MCP server's tool implementations, not in the agent's prompt — a misbehaving LLM cannot accidentally return another user's Case because the underlying tool refuses the query.

**Cascade scope.** A Case's `owner_user_id` cascades to every artifact rooted at that Case: the `sessions` document(s) bound to the Case (D.6), the `runs` documents produced inside it (D.3), the `events` documents (D.4) authored from a Case-scoped Hazard Event Pipeline invocation, and the `layers` documents (D.2 ProjectLayerSummary subdocuments). The ownership query in MCP is a single field check at the Case root; descendant documents inherit by reference.

**Anonymous Case ownership.** Anonymous users (per H.3) have a stable Firebase `uid` and a `users._id` ULID just like authenticated users — they can own Cases. The H.3 upgrade flow preserves the `uid` so anonymous-Case ownership survives the upgrade.

**Collaborators (v0.1 deferred).** Multi-owner / shared Cases via an explicit `case_collaborators[]` list are **deferred** — v0.1 is single-owner. Stakeholder discussion: pre-MVP scope (AGENTS.md "Pre-MVP scope, no legacy support") + Decision K ("user supplies intent and irreducible inputs") together argue that the single-owner shape is the simpler intermediate; collaboration is a v0.2+ shape change. When collaboration lands, the `projects` schema gains a `case_collaborators: list[CaseCollaboratorEntry]` field where each entry pairs a `user_id` with a permission level (read / write / admin); the MCP enforcement layer changes from a single-field equality to a membership check. Adopting this shape is additive — single-owner Cases remain valid because an empty `case_collaborators[]` is the v0.1 default.

**Future capabilities (deferred from v0.1, recorded for traceability):**

- Ownership transfer (e.g. "transfer this Case to user@example.com") — requires a confirmation modal pattern (FR-AS-8) and a notification to the receiving user.
- Shared Cases with per-collaborator permissions (above).
- Case "publish" mode — Case made public/read-only-link-shareable without account-to-account binding; requires Decision M provenance discipline + a public-Case audit log.
- Organization-scoped Cases (Identity Platform enterprise SKU) — a Case belongs to an org rather than a user; orgs have their own admin / member / billing model.

### H.3 Anonymous → authenticated upgrade

**Landing UX rule.** A user visiting GRACE-2 without any prior session is immediately signed in as a **Firebase anonymous user** — no login wall, no friction. They can:

- Chat with the agent
- Create a Case (becomes `owner_user_id = <anonymous-uid>`)
- Run modeled / discovered / impact workflows inside the Case
- Save layers into the Case's `layer_summary` (sprint-09 `publish_layer` substrate)

**Upgrade trigger.** The UX presents an inline "Save your account" / "Sign in to keep this Case" prompt at any of these moments:

1. First time the user attempts to **share** a Case (when sharing lands per H.2 deferred list).
2. First time the user attempts a **destructive** action that benefits from named-attribution (e.g. publishing a Case to a public hazard-event reference).
3. After a user explicitly clicks a "Sign in" / "Create account" UI affordance.
4. On Case re-open after a long absence (TTL threshold; surfaces the "keep this account around" prompt before the anonymous session expires — Firebase default anonymous-session expiry is provider-configurable, currently set to "never expire" for v0.1 but a 30-day-since-last-active expiry is a reasonable Wave 2+ default).

**The upgrade flow** uses Firebase's `linkWithCredential(...)` client SDK call:

1. Client UI prompts user for an email + password (or OAuth provider button).
2. Client calls `auth.currentUser.linkWithCredential(emailAuthProvider.credential(email, password))` (or the OAuth equivalent).
3. Firebase Auth atomically links the credential to the existing anonymous `uid` — no `uid` change.
4. Client emits a new `user-authenticated` envelope (Appendix A amendment in the Wave 2 schema sprint) to the agent so the agent can update its in-session state from "anonymous" → "authenticated".
5. Agent updates the corresponding `UserDocument` to set the credential metadata (email, provider, linked-at timestamp); the `_id` (ULID) and `firebase_uid` are unchanged.

**Memory rule satisfaction.** The user is informed before the persistent-account binding lands: the upgrade is always user-initiated (clicking a "Sign in" button, accepting the inline prompt), never silent. This satisfies the orchestrator-codified rule "user is informed before persistent action."

**Failure modes.**

- **Email already exists for a different account** — `linkWithCredential` rejects with `credential-already-in-use`; UI surfaces "this email is already registered; sign in to that account instead, or use a different email." Anonymous Case stays bound to anonymous `uid`; no data loss.
- **OAuth-provider conflict** (same email registered with different provider) — surfaces "this email is registered with <provider>; sign in with that provider instead." User can then sign in with the existing account and manually move the anonymous Case (Wave 2 feature: anonymous-Case import on first authenticated sign-in within N hours of the anonymous session).
- **Network failure mid-upgrade** — `linkWithCredential` is atomic; either succeeds entirely or leaves the anonymous user unchanged.

### H.4 Custom claims for tier gating (free / pro / enterprise)

**Claim shape.** Each user's Firebase ID token carries a `tier` custom claim, set via Identity Platform Admin SDK:

```
{
  "tier": "free" | "pro" | "enterprise"
}
```

**v0.1 default.** All users are `tier: "free"` at provisioning time. Tier upgrade machinery is **deferred** until the v0.2+ commercial track lands; v0.1 has no paid plan, no payment integration, and no tier-bumping admin UI.

**Why custom claims and not a Mongo field.** Tier is read by the agent on every WebSocket connect to gate which workflows / atomic tools the user can invoke. Reading from the verified JWT is O(1) on the agent side — no Mongo query, no cache invalidation race. The Mongo `UserDocument.tier` field is the durable mirror (write-on-tier-change, read-as-truth-on-token-mint); the JWT claim is the operational read-path.

**Gating discipline.** Tier-gated workflows / atomic tools enumerate their required tier in their FR-AS-3 / FR-TA-3 docstring metadata. The agent's tool-routing layer checks the request user's tier claim against the tool's required tier at dispatch time; mismatch returns a `TIER_INSUFFICIENT` error (new Appendix A.6 SCREAMING_SNAKE_CASE code, lands when the first tier-gated tool lands). v0.1: zero tier-gated tools — every workflow / atomic tool is free-tier-accessible. The machinery is in place; the gates are not yet armed.

**Why this matters now (even with no paid tier).** Pinning the tier claim shape now means a v0.2+ pro-tier flip does not require a JWT-shape change, a client-SDK upgrade, or a contract revision. The cost of adding `"tier": "free"` to v0.1 tokens is zero; the cost of retrofitting the claim later would be a coordinated agent + web + admin-tooling change.

**Future enterprise expansion (Identity Platform SKU).** The enterprise SKU surfaces additional claims:

- `organization_id` — for Cases that belong to an org rather than a user (H.2 future).
- `roles[]` — for enterprise role-based access control (admin / analyst / viewer within an org).
- `permissions[]` — for fine-grained capability flags.

None of these are v0.1 scope; they land as Identity Platform-SKU upgrades when the first enterprise customer lands.

### H.5 Session validation — agent-side token verification

**Connection flow.** Per Appendix A.5 (Connection Lifecycle), the WebSocket connect handshake carries the Firebase ID token as a connection-level credential (proposed mechanism: `Sec-WebSocket-Protocol` subprotocol header, or `Authorization: Bearer <id_token>` upgrade header — exact mechanism is a Wave 2 schema decision; this appendix pins **that** verification happens, not **how**). The agent's connection-acceptor:

1. Reads the ID token from the connect frame.
2. Calls `firebase_admin.auth.verify_id_token(id_token, check_revoked=True)` — the Admin SDK validates signature against Firebase's rotating JWKS, checks expiry, checks revocation list, and returns the decoded claims (including `uid`, `email`, `tier`).
3. Resolves the Firebase `uid` to the corresponding `UserDocument._id` via the `Persistence.get_user_by_firebase_uid(firebase_uid)` interface (lands in job-0115; the FR-MP-1 Persistence contract). If no `UserDocument` exists for the `uid`, the resolver creates one (auto-provision on first authenticated connect) with default fields (`tier="free"`, anonymous-flag mirrored from the JWT claim, provider metadata from the JWT claims).
4. Binds the resolved `User._id` into the agent's session context as the active user; every subsequent tool call, MCP query, and Case binding flows through that user.

**Token refresh.** Firebase ID tokens expire after 1 hour. The client SDK automatically refreshes them via the refresh token (handled by `firebase/auth` SDK transparently). When the agent's connection-acceptor receives a refreshed token mid-session (proposed mechanism: `token-refresh` envelope in Appendix A amendment; deferred to Wave 2 schema sprint), it re-runs `verify_id_token` and updates the in-session JWT cache. If the refresh fails (token expired and refresh token revoked), the agent closes the WebSocket with the `AUTH_TOKEN_EXPIRED` error code (new Appendix A.6 SCREAMING_SNAKE_CASE code).

**Revocation.** Firebase Auth supports user-token revocation via the Admin SDK (`auth.revoke_refresh_tokens(uid)`). On revocation, the next agent-side `verify_id_token` call with `check_revoked=True` fails; agent closes the session. v0.1 use cases: account deletion (covered below), security incident (operator-initiated mass revocation).

**Account deletion.** When a user requests account deletion, the agent (or an admin tool) calls `firebase_admin.auth.delete_user(uid)` to remove the Firebase Auth record AND marks the corresponding `UserDocument` as `deleted_at: <timestamp>` (soft-delete tombstone). The owned Cases retain `owner_user_id` pointing at the deleted user (preserved for audit trail); the MCP enforcement layer treats a soft-deleted user's Cases as inaccessible by anyone (no inheritance to a "next owner"; the Cases are tombstoned, recoverable only by admin tool).

**Why verification is agent-side, not MongoDB-side.** Decision F (MongoDB Atlas as durable knowledge layer) pins Mongo as durable storage; the operational read-path verification of credentials lives in the agent. MongoDB Atlas does support its own user/role/connection model for the database itself (D.3 IAM rules), but that is for the **agent's worker connection** to Atlas, not for end-user identity. The agent is the gate; Mongo is the substrate. (Equivalently: Atlas authentication is "is the agent allowed to talk to the database?"; Firebase Auth is "is the human allowed to use the agent?".)

### H.6 Secrets scoping — per-user vs per-Case vs deployment

**Scope hierarchy.** The `SecretRecord` schema (Appendix F §F.3 deferred substrate, lands when the per-user secrets UX lands) carries two scope fields:

- `user_id: ULIDStr` — the `User._id` the secret belongs to. **Required.**
- `case_id: ULIDStr | None` — the `Case._id` the secret is scoped to, OR `None` for user-wide secrets that cross Cases.

**Per-Case secrets** (`case_id` set) — used when the user provisions a credential that's only relevant inside one Case. Examples (Wave 2+):

- A user provides an API key for a private data source only for analysis in a specific Case (e.g. a research-collaboration NDA-bound data feed).
- A user provides an SFTP path with credentials for a specific dataset uploaded for one Case.

**Per-user secrets** (`case_id = None`) — used when the credential is the user's general-purpose key, applicable across all of the user's Cases. v0.1 Tier-2 examples (per Appendix F §F.1):

- **eBird API key** — user provisions once, used across all conservation/biodiversity Cases.
- **IUCN Red List API key** — same pattern.
- **Movebank credentials** — same pattern.
- **Census ACS key** (if the user prefers their own provisioning instead of deployment-scope) — same pattern.
- Other Tier-2 keys that a user might want personally scoped (NewsAPI, Earthdata Login, NOAA api.weather.gov keyed endpoints).

**Deployment-scope secrets (existing v0.1 substrate).** Until §F.3 lands and per-user secrets are operationally provisioned, Tier-2 keys are deployment-scope (operator-provisioned via OpenTofu + Secret Manager — one Census key per deployment, shared by all users). Deployment-scope provisioning remains as a fallback even after per-user provisioning lands; per-user provisioning is the preferred path when a user has their own key.

**Storage substrate** (deferred per §F.3; Identity-Platform-prerequisite). Per-user secrets live in **GCP Secret Manager** with the secret name `users/<user_id>/secrets/<secret_name>` or `users/<user_id>/cases/<case_id>/secrets/<secret_name>` (per-Case). The `users/` prefix gives a clean IAM boundary: a per-user Secret Manager binding scopes who can read; admin-tool reads are auditable; cross-user reads are forbidden by IAM (not just by application logic).

**Wire-level isolation (preserves Decision F discipline).** Per §F.3 the secret never transits the chat envelope to MongoDB. The agent receives a `secret-response` envelope from the client (out-of-band of the chat WebSocket per F.3 design), the secret value goes directly into Secret Manager via the Cloud Function secret-receiver, and only the `secret_name` reference (e.g. `ebird_api_key`) appears in any persisted document. The agent reads the secret value at tool-invocation time via Secret Manager IAM-scoped read.

**Storage in `UserDocument`.** The `UserDocument` does **not** store the secret values themselves — only the `secret_names` list (which secrets this user has provisioned, scoped per-user or per-Case). This list is metadata; the values live exclusively in Secret Manager.

**Why secrets care about Auth.** Without H.1's stable user identity, per-user secrets cannot exist — anonymous-session secrets would be re-prompted every session and have no durable identity to bind to. The §F.3 deferred-indefinitely status is exactly because §F.3 depends on H.1 + H.5 to be operationally landed; this appendix unblocks the per-user secrets substrate at the architectural level.

### H.7 Decision P — Firebase Authentication over alternatives *(numbered as the next available Decision letter after A–O)*

**Note on numbering.** The job-0116 kickoff text referenced "Decision E" as a placeholder; Decision E is already taken by "Google Cloud throughout" (§2.1). The next available Decision letter is **P** (A–O are claimed). This amendment records the Auth decision as **Decision P**; the §2.1 Decisions list gains this row when the user lands the amendment.

**Decision P: Firebase Authentication (Identity Platform) as the GRACE-2 identity provider.** *(Forward-looking — not in M1 / not in sprint-03; binding from Wave 2 of sprint-12-mega when the first authenticated-user flows land. Same discipline as Decisions N / O — deferred until the relevant capability lands.)*

GRACE-2 uses Firebase Authentication as the v0.1 identity provider, with GCP Identity Platform as the enterprise-SKU upgrade path. Selection rationale: GCP-native (Decision E alignment), anonymous-friendly with `linkWithCredential` upgrade preserving Case ownership through anonymous → authenticated transitions (H.3), custom-claims surface for tier gating without contract revision (H.4), scales to enterprise SKU (SLA / CMEK / SAML / audit logs) without re-keying users or re-issuing tokens, and decoupled from compute so identity-vendor outages don't crash the agent. Alternatives considered and rejected: Auth0 (cross-cloud vendor seam — wrong for GCP-pinned GRACE-2), AWS Cognito via cross-cloud bridge (operationally complex — IAM federation overhead), self-hosted OIDC (Keycloak / Dex / Ory — SLA + abuse-mitigation + key rotation become GRACE-2's problem, indefensible v0.1 scope), no-auth-at-v0.1 (infeasible once Cases require single-owner persistence at sprint-12-mega Wave 2). The user→Case ownership rule (H.2), the anonymous-upgrade flow (H.3), the tier-claim discipline (H.4), the agent-side session validation (H.5), and the per-user secrets scoping (H.6) all derive from this selection.

**Cross-references.**

- Decision E (GCP throughout) — Firebase Auth is the GCP-native realization of identity.
- Decision F (MongoDB Atlas durable knowledge layer) — `UserDocument` (Wave 2 D.x amendment) is the durable mirror of Firebase-managed identity; Firebase remains the authority for credentials.
- Decision K (user supplies intent and irreducible inputs) — user identity is an irreducible input; Firebase Auth provides it with minimum credential friction (anonymous default).
- Decision M (multi-source claim aggregation with provenance) — provenance attribution at the Case level requires a stable owner identity, which Firebase Auth supplies.
- Invariant 9 (no cost theater) — no cost field anywhere on Auth envelopes; tier is a capability claim, not a cost surface.
- Appendix A.5 (Connection Lifecycle) — Wave 2 amendment adds the ID-token-on-connect handshake.
- Appendix A.6 — Wave 2 amendment adds `AUTH_TOKEN_EXPIRED`, `AUTH_TOKEN_INVALID`, `TIER_INSUFFICIENT` SCREAMING_SNAKE_CASE error codes.
- Appendix D — Wave 2 amendment adds `users` collection (D.x) with `UserDocument` shape (`_id`, `firebase_uid`, `email`, `provider`, `tier`, `is_anonymous`, `created_at`, `last_seen_at`, `deleted_at`, `secret_names`).
- Appendix F §F.3 (Deferred Secrets UX) — H.6's per-user secret scoping is the architectural prerequisite §F.3 calls "M6+ user-identity machinery."

---
## Appendix I: LLM Tool Harness Conventions

> *(Decision F harness conventions, adopted 2026-06-08 per user direction "we can now tighten the harness." Binding from sprint-12-mega Wave 4.7 forward — see job-0164 (engine sweep) and job-0165 (this appendix). The conventions formalize the lessons of ~57 atomic tools shipped through sprint-12 against a Gemini-3 frontier LLM that routinely invents kwargs, abbreviation variants, and natural-language parameter strings.)*

**Purpose.** This appendix codifies five conventions that every `@register_tool`-registered atomic tool and workflow in `services/agent/src/grace2_agent/{tools,workflows}/*.py` must conform to, plus the centralized normalization layer that backstops them. Together they harden the agent harness against the empirically observed failure mode where a frontier LLM emits well-intentioned but invented arguments — `run_name`, `scenario_id`, `description`, `rainfall_event="atlas14_100yr"`, `return_period_years` when the tool defines `return_period_yr`, `durationHours` when the tool defines `duration_hours` — and Python's strict signature-binding rejects every one with a `TypeError: unexpected keyword argument`. The conventions trade a small amount of per-tool boilerplate for end-to-end resilience: the harness absorbs noise, normalizes legitimate variants, fails loud only on substantive ambiguity, and surfaces unknown kwargs to logs without blocking the call.

**Scope.** These conventions apply to **every** function decorated with `@register_tool` — atomic tools (FR-TA-2), workflow exposures (FR-TA-1), pass-throughs to MongoDB MCP, and dispatchers (e.g. `run_solver`, `catalog_fetch`). They are out of scope for: schema models (Appendices A–D — those are pydantic v2 contracts, not tool signatures), client-side rendering, infra provisioning, and tests (which exercise the conventions but do not author them).

**Relationship to other contracts.** This appendix is a **convention layer**, not a schema. It does not introduce new Appendix A messages, Appendix B/C/D fields, or Appendix F catalog entries. It documents the discipline that the `agent` specialist enforces in code and that `engine` specialists conform to when registering tools. Tool-result shapes remain governed by Appendices A–D (e.g. `LayerURI` for layer-emitting tools, `AssessmentEnvelope` for workflows). Invariants 1 (determinism boundary) and 7 (claims carry provenance) are preserved verbatim — the harness silently absorbs *input* noise but never silently fabricates *output* values.

### I.1 Parameter naming convention — full words, with bounded backward-compat aliases

**Rule.** Parameter names use full unabbreviated English words separated by `_`. The two specific abbreviations historically embedded in the v0.1 atomic-tool surface are renamed and an alias is retained for backward-compatibility during the v0.1 transition.

- `_yr` (year, years) → `_years` (e.g. `return_period_yr` → `return_period_years`)
- `_hr` (hour, hours) → `_hours` (e.g. `duration_hr` → `duration_hours`)

**Why.** Gemini-3 (the v0.1 LLM per FR-AS-1) generates `return_period_years` and `duration_hours` by default; the abbreviated forms read as cryptic to both the model and to humans inspecting tool docstrings via `tool-call-start` envelopes (A.4). Standardizing on full words eliminates the most common observed `unexpected keyword argument` failure class without per-call normalization.

**Alias discipline.** For each renamed parameter, the old name is retained as `<old>: <type> | None = None` and normalized at the top of the function body. The alias never participates in the public docstring `Params:` section (it is implementation detail), but is retained until v0.2 to honor in-flight scripts and prompt-cached LLM examples that learned the v0.1 names. Example pattern (verbatim from `run_model_flood_scenario`):

```python
async def run_model_flood_scenario(
    ...,
    return_period_years: int = 100,
    duration_hours: int = 24,
    # Backward-compat aliases for legacy short forms
    return_period_yr: int | None = None,
    duration_hr: int | None = None,
    **_extra_ignored: Any,
) -> LayerURI | dict[str, Any]:
    """..."""
    effective_return_period = (
        return_period_yr if return_period_yr is not None else return_period_years
    )
    effective_duration = duration_hr if duration_hr is not None else duration_hours
    ...
```

**Forward extension.** Any future abbreviation hits this rule by construction: a new tool author writes the full-word form from the start. Aliases are only introduced for renames of *already-shipped* parameters, never for new ones.

**Pluralization.** The full-word form is plural when the underlying quantity is a count or a span (`years`, `hours`, `meters`, `kilometers`). Singular forms (`year`, `hour`) are reserved for the rare case where a single discrete unit is meant (e.g. a calendar year, not a duration). Default to plural; if uncertain, prefer plural.

**Non-goals.** This rule does *not* mandate fully verbose names where the abbreviation is universally understood and not a unit-bearing suffix (e.g. `bbox` stays `bbox`, not `bounding_box`; `dem` stays `dem`, not `digital_elevation_model`; `crs` stays `crs`, not `coordinate_reference_system`). The renames target unit-suffix abbreviations that the LLM has no prior reason to expect.

### I.2 `**_extra_ignored` absorb-and-log policy

**Rule.** Every `@register_tool`-registered function shall accept `**_extra_ignored: Any` as its final parameter, after all positional-or-keyword parameters and any backward-compat aliases.

**Rationale.** Strict Python signature binding rejects unknown keyword arguments with `TypeError`. Frontier LLMs routinely emit kwargs that look reasonable from the prompt or examples but do not exist on the target tool — `run_name`, `scenario_id`, `description`, `notes`, `mode`, `version`, `priority`. With strict binding, each invented kwarg fails the entire call; absorb-and-log accepts the call, ignores the noise, and surfaces the kwargs to structured logs for harness telemetry.

**Naming.** The parameter name is exactly `_extra_ignored` (underscore prefix marks it as deliberately unused per PEP 8 convention; the name `_extra_ignored` is the project convention so harness tooling and reviewers can grep for it). Do not rename it per-tool.

**Type annotation.** `**_extra_ignored: Any`. The `Any` annotation is deliberate — the values are not validated and not consumed.

**Logging.** When the function body receives a non-empty `_extra_ignored`, the harness shall log at `INFO` level a structured record `{tool_name, ignored_keys, session_id?}` so the operations side can monitor which kwargs the LLM is inventing most often. Logging is the responsibility of the centralized normalizer (§I.4) when it executes before the tool body; tools themselves do not need to add a log line. (Per §I.4, the normalizer logs only the *unknown-after-normalization* residue, not the raw input keys — keys consumed by alias normalization are not "ignored.")

**Interaction with `extra="forbid"` on pydantic models.** The absorb policy applies to **tool signatures only**, not to pydantic models. Schema models (Appendices A–D) retain `extra="forbid"` per the `grace2-contracts` v0.1.0 discipline — wire shapes are strict, tool signatures are permissive. The two layers serve different purposes: schemas enforce wire-level integrity (Invariant 7); tool signatures protect against LLM-side noise.

**Reference pattern.** `services/agent/src/grace2_agent/workflows/model_flood_scenario.py` `run_model_flood_scenario` is the canonical example (already in production, sprint-07 substrate); job-0164 propagates the pattern across the remaining ~57 functions.

### I.3 Docstring discipline — no inline param-syntax-looking example strings

**Rule.** Docstrings shall not embed example values that *look like* valid Python keyword arguments inside the prose. Specifically:

- Do NOT write inline as prose: `... pass forcing="atlas14_100yr" to invoke ...`
- Do NOT write inline as prose: `... use return_period_years=100 for ...`

Such strings are visually indistinguishable from real parameter definitions when Gemini-3 reads the docstring via the ADK FunctionTool surface — the model frequently re-emits them verbatim as call arguments, even when no parameter by that name exists on the tool. This is the empirically observed root cause of invented kwargs like `forcing` and `rainfall_event` on the flood-scenario workflow.

**Format.** Per-tool docstrings shall conform to the following sectioned format (already mandated in part by FR-AS-3 / FR-TA-3 — this appendix tightens it):

```
<one-sentence summary on a single line>

Use this when: <natural-language usage triggers, with example USER PROMPTS in
quotes — these are demonstrations of *what the user might say*, not example
parameter values>. Multiple sentences allowed.

Do NOT use this for: <complementary exclusions>.

Params:
    <param_name>: <one-line description>. Default <default>.
    ...

Returns:
    <one-line description of the success return type>.

    <Optional block for failure / partial-failure shape.>

Examples:
    >>> result = await <tool_name>(<keyword>=<value>, <keyword>=<value>)
    >>> <follow-up call illustrating composition>

<Optional FR-XX / Appendix-X anchor notes.>
```

**The `Examples:` block.** All call-shaped examples — anything that looks like Python syntax for calling the tool — live exclusively under a dedicated `Examples:` block at the end of the docstring. The block uses doctest-style `>>>` prefixes so the LLM has an unambiguous signal that the lines are demonstration code, not prose. Free-text "use this when" examples (in the `Use this when:` block) are framed as user prompts in quotes, never as parameter expressions.

**Why this works.** Gemini-3 treats prose `key=value` substrings as if they were function-signature examples and re-emits them. Quoted user prompts (`"model the flood from a 100-year storm in Fort Myers, FL"`) are read as natural-language utterances. A dedicated `Examples:` block isolates call syntax to one labeled region the model can parse without confusion.

**Migration discipline.** Existing tool docstrings that embed inline `key="value"` substrings shall be rewritten under this convention. The mechanical pass is part of job-0164's Part 4. New tools (sprint-12-mega Wave 5 onward) shall conform from the first commit.

**Cross-reference to FR-AS-3 / FR-TA-3.** Those FRs mandate the existence of docstring metadata (one-sentence summary, "Use this when:", "Do NOT use this for:", param + return descriptions). This appendix specifies the **discipline of example placement** within that structure — the prior FRs did not anticipate the inline-`key=value`-as-prose hazard.

### I.4 Normalization layer — `tool_arg_normalizer.py`

**Rule.** All tool invocations dispatched by the agent's `_invoke_tool_via_emitter` (or equivalent ADK FunctionTool callsite in `services/agent/src/grace2_agent/server.py`) shall route through a centralized normalizer **before** the underlying `entry.fn(**params)` call.

**Module location.** `services/agent/src/grace2_agent/tool_arg_normalizer.py` (sibling to `server.py`, owned by `agent` specialist; job-0164 lands the initial implementation in code).

**Surface.** A single entry point:

```python
def normalize_args(
    tool_name: str,
    raw_args: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Returns (normalized_args, dropped_unknown_args).

    normalized_args is suitable for `entry.fn(**normalized_args)`.
    dropped_unknown_args is logged at INFO; callers should not
    inject it back into the call.
    """
```

**Behaviors (in order).**

1. **Alias map** — a tool-name-keyed registry of `{old_param: new_param}` mappings. Maintained alongside the §I.1 backward-compat aliases; when an old name appears in `raw_args`, the value is moved to the new name (if the new name is absent) and the old key is removed. Conflicts (both old and new keys supplied with different values) are logged at WARNING and the new-name value wins.
2. **Fuzzy match** — for keys that exactly match neither a real param nor a known alias, attempt a bounded Levenshtein / camelCase-to-snake_case normalization (e.g. `durationHours` → `duration_hours`, `returnPeriodYears` → `return_period_years`, `bbx` → `bbox` only if edit distance ≤ 1 and the candidate is unambiguous). Multiple ambiguous candidates → no rewrite; key falls through to the absorb-policy bucket. Fuzzy match is conservative by default; the matcher has a hard cap of one rewrite per call key.
3. **String-form parsing** — for parameters with documented string-form shorthands (e.g. `forcing="atlas14_100yr"` parsing to `return_period_years=100` per `run_model_flood_scenario`), apply per-tool string parsers registered alongside the alias map. The reference implementation is in `run_model_flood_scenario` body (job-0042 substrate); the centralized normalizer hoists this into a reusable layer.
4. **Drop-and-collect unknowns** — any key remaining after steps 1–3 that does not match a real parameter name on the target function shall be collected into `dropped_unknown_args` (the second tuple member), logged at INFO with `{tool_name, dropped_keys, session_id}`, and absent from `normalized_args`. The `**_extra_ignored` absorb at the function level (§I.2) is the safety net for any kwargs that escape the normalizer (e.g. when the registry has not yet seen the tool).

**Where it wires.** `services/agent/src/grace2_agent/server.py` in `_invoke_tool_via_emitter` (or the renamed equivalent) shall call `normalized, dropped = normalize_args(tool_name, raw_args)` immediately before `await entry.fn(**normalized)`. Dropped kwargs are logged via the existing structured logger; they are not transmitted on the `tool-call-start` or `tool-call-complete` envelope (they are harness-internal telemetry, not user-visible).

**Failure handling.** The normalizer never raises on unknown keys (that is the whole point). It MAY raise on internal-consistency errors (registry malformation, mismatched alias map). Raised errors are routed through the agent's existing error envelope (A.6 `error` message, code `TOOL_ARG_NORMALIZER_FAILED` — a new code to be added to A.6 in a sibling schema amendment).

**Telemetry.** Per-tool counters of `{normalized_count, dropped_count, fuzzy_rewrite_count}` are emitted to the agent's structured-log stream so operations can spot tools that disproportionately attract invented kwargs — those are candidates for docstring tightening or signature redesign.

**Out of scope for the normalizer.** It does **not** validate types (pydantic does that at the schema layer where applicable). It does **not** auto-fix substantively wrong values (e.g. a negative `return_period_years`). It does **not** silently fabricate defaults — defaults remain the responsibility of the tool's function signature. It is a *naming-noise* shim, not a *value-validation* shim.

### I.5 Per-tool tests — cross-cutting fuzz

**Rule.** Every `@register_tool`-registered function shall have a cross-cutting test that exercises the §I.2 absorb policy and §I.1 alias acceptance.

**Test surface (recommended).** A single parametrized test file `tests/test_tool_harness_conventions.py` (owned by `testing`; out of scope for this appendix to author) that iterates over the live registry and asserts, for each tool:

1. The function accepts an empty kwargs dict via the absorb policy (i.e. calling with `**{"run_name": "smoke"}` does not raise `TypeError`).
2. Each documented alias resolves to its new-name parameter without error (when applicable per §I.1).
3. The docstring conforms to the §I.3 sectioned format — `Use this when:`, `Do NOT use this for:`, `Params:`, `Returns:`, `Examples:` sections exist; no inline `key="value"` prose in `Use this when:` / `Do NOT use this for:` / `Params:` regions.
4. The signature ends with `**_extra_ignored: Any`.

**Fuzz layer (recommended).** A property-based test that feeds tools a kwargs dict including 0–5 randomly generated unknown keys (alphanumeric, length ≤ 12, plus snake_case noise) and asserts the call returns without raising signature-binding `TypeError`. Coverage target: every tool returned by the registry, run with a fixed seed for reproducibility.

**CI gate.** The per-tool conformance tests run on every PR touching `services/agent/src/grace2_agent/{tools,workflows}/*.py` and gate merge. The fuzz tests run on every PR touching the normalizer or any registered tool.

**Test ownership.** `testing` specialist authors the test file and CI hooks; `engine` and `agent` specialists ensure their tools pass.

### I.6 Decision F (harness conventions) — adopted 2026-06-08

**Decision.** The five conventions §I.1–§I.5 above are adopted as the LLM tool-harness discipline for GRACE-2 v0.1 forward, binding from sprint-12-mega Wave 4.7. The decision is recorded here (Appendix I) and in §2.1 Decisions (Decision F slot — already pinned to "harness conventions" by orchestrator update; this appendix is the canonical reference for the convention body).

**Rationale.**

- **Empirical signal.** Across ~57 atomic tools and ~5 workflows shipped through sprint-12-mega, the single most-common reason an LLM tool call fails before any application logic runs is signature-binding mismatch — invented kwargs, abbreviation variants, natural-language string-form parameter values. Whack-a-mole patching of individual tools has been the pattern through sprint-12; centralized conventions stop the cycle.
- **No backward-compat for *new* shapes.** Per the AGENTS.md pre-MVP cross-cutting principle, no backward-compat shims for new shapes; the §I.1 aliases are the **only** backward-compat layer, and they are bounded to already-shipped parameter names (`_yr` / `_hr`) being renamed. New tools (sprint-12 Wave 5 forward) ship with full-word names from the first commit and do not get aliases.
- **Convention, not schema.** This appendix does not introduce new wire fields or pydantic models; it tightens *implementation discipline*. Schemas (Appendices A–D) remain strict (`extra="forbid"`); tool signatures become permissive (`**_extra_ignored`). The two layers serve different purposes.
- **Forward-looking surface.** As new tools land — Pelicun (M5.5), TELEMAC (M11), conservation utilities (OQ-11 pending), Tier-2 fetchers, Mode-2 catalog adds — the conventions apply uniformly without further amendment.

**Alternatives considered.**

- **Strict signature with whitelist auto-strip** — reject the call early on unknown kwargs and let the agent re-prompt. Rejected: re-prompting is expensive in turns and tokens; the LLM frequently re-invents the same kwargs the second time.
- **Pydantic models in every signature** — define a model per tool, use `model_construct` with `extra="allow"`. Rejected: too heavy for atomic tools; pydantic v2's per-tool overhead dwarfs the call body for simple fetchers; harms readability and makes ADK FunctionTool registration less direct.
- **Pure normalizer with no `**_extra_ignored`** — rely on the centralized normalizer alone to strip unknown kwargs. Rejected: the normalizer cannot know about every tool at all times (registry races during reload), and `**_extra_ignored` is a one-line per-tool safety net with no downside. Belt-and-suspenders.

**Forward path.**

- **v0.2 alias retirement.** When v0.2 ships, the §I.1 backward-compat aliases (`return_period_yr`, `duration_hr`) are removed; the full-word names are the only accepted form. Doctring `Use this when:` / `Examples:` blocks updated to reference only full-word names.
- **Normalizer evolution.** The normalizer's fuzzy-match cap may relax as telemetry shows which rewrites are safe. The string-form parser registry grows per-tool as new shorthand patterns emerge from production usage.
- **Convention propagation to non-tool surfaces.** If the agent later exposes resource-typed handles (MCP resources, tool-class sub-types), the absorb policy may extend to those surfaces under a sibling appendix.

**Cross-references.**

- §2.1 Decision F (harness conventions row) — this appendix is the body.
- FR-AS-3 (atomic-tool metadata discipline) — extended by §I.3.
- FR-TA-3 (tool-docstring discipline) — extended by §I.3.
- Invariant 1 (determinism boundary) — preserved; harness absorbs *input* noise but never silently fabricates *output* values.
- Invariant 7 (claims carry provenance) — preserved; tool result shapes are unchanged.
- Invariant 10 (minimal parameter surface) — preserved and reinforced; full-word names + no inline example syntax = smaller cognitive parameter surface.
- AGENTS.md pre-MVP "no legacy support" — honored; the only backward-compat is the bounded §I.1 alias set for already-shipped parameter names.
- Job-0164 (engine sweep) — the implementation companion to this appendix; lands `**_extra_ignored`, renames, docstring fixes, and wires the normalizer.
- Job-0165 (this appendix) — authors the appendix itself.

---

