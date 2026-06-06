# Software Requirements Specification

## Hazard Modeling Agent — A Web-Based AI Workbench for Multi-Hazard Modeling

**Version:** 0.3.14
**Status:** Draft
**Authors:** Nathaniel J Almanza
**Last updated:** 2026-06-05
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
5. **Forcing data caching**: DEMs and landcover for the same bbox shouldn't be re-fetched per run. GCS-backed cache keyed by `(source, bbox, resolution)` is the working assumption; concrete cache-key conventions and eviction policy to be designed during M4 / M5.
6. **Pre-baked demo scenarios**: should specific high-impact historical events (Hurricane Ian, etc.) have pre-computed results available for instant demo, or always run live? Live is more impressive when it works; pre-baked is safer for demos.
7. **Vector embedding dimension**: `text-embedding-005` defaults to 768-dim but supports configurable down to 128. Verify recall trade-off on a small corpus before locking the Atlas Vector Search index config. The model itself is decided (see Decision L in §2.1).
8. **Fragility and consequence-curve sourcing for Pelicun** *(forward-looking — not blocking M1 / sprint-03; decide before M5.5)*: choose between HAZUS Hurricane/Flood damage functions (coarser, building-level, regional-scale appropriate), FEMA P-58 component fragilities (component-level, building-specific, much richer but slower), Pelicun's bundled DLML defaults, or user-supplied YAML/CSV with explicit provenance. Decision affects `ImpactEnvelope.fragility_source` values, output resolution, runtime, and the per-claim citation discipline required under Decision M. Bundled defaults are likely v0.1 with an override mechanism deferred. See Appendix B.6c and §2.3 post-processing tool classes.
9. **Mesh-generation toolchain for TELEMAC-MASCARET** *(forward-looking — not blocking M1 / sprint-03; first engine target post-MVP / v0.2+; decide before Milestone M11)*: pick between GMSH (open, scriptable, mature Python bindings, general-purpose), OceanMesh2D (mature for coastal / storm-surge meshing, MATLAB-origin with a Python port), BlueKenue (NRC Canada, free, GUI-centric, weaker headless story), or in-suite Telemac preprocessors. Decision affects automation depth, Python-shim ergonomics, license posture, and runtime; same shape as OQ-8 (choose between named alternatives, decide before the milestone). See §2.3 Deferred engines (TELEMAC-MASCARET row), FR-TA-1 TELEMAC modeling workflows, and Milestone M11.

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

