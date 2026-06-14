## 2. System Overview

### 2.1 Architectural decisions

**Decision A: Web frontend, not QGIS Desktop plugin**
v0.1 ships as a web application. The AI is the abstraction over plugin management, layer styling, and processing toolbox operations — features that would otherwise require QGIS Desktop UI. Users do not need to install software.

**Decision B: QGIS Server as the rendering backend**
QGIS Server (Cloud Run) renders the same `.qgs` projects and QML styles that QGIS Desktop would, but serves them as OGC web services (WMS/WMTS/WFS) to the web client. This preserves the QGIS rendering engine and ecosystem without requiring users to interact with QGIS Desktop's UI.

**Decision C: PyQGIS workers for project manipulation**
Project file mutations (adding layers, changing styles, configuring temporal properties) are performed by short-lived PyQGIS worker jobs (Cloud Run Jobs). The agent invokes these as tools; they read the `.qgs` from GCS, mutate it, write back.

**Decision D: GeoAgent (opengeos) is a reference, not a dependency**
Patterns and ideas from the opengeos/GeoAgent project — tool registration discipline, docstring conventions, confirmation hook approach — inform the design. No code is copied or vendored. The generation loop is raw google-genai SDK (not ADK), and the tool-registration conventions are project-defined; ADK is a transitive dependency only with zero direct callers in the agent service (see Decision E and FR-AS-1).

**Decision E: Google Cloud throughout**
All infrastructure runs on Google Cloud: raw google-genai SDK (Gemini 2.5/3) for the agent generation loop — ADK is a transitive dependency only; `register_with_adk` has zero callers and the generation loop is `client.models.generate_content_stream` per `adapter.py` (FR-AS-1) — Cloud Run for QGIS Server and worker jobs, Cloud Workflows for multi-step orchestration, GCS for artifacts. MongoDB Atlas runs as a managed service alongside (multi-cloud-deployable, in practice deployed in a GCP region adjacent to the agent).

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
│  (Cloud Run, raw google-genai)  │  │   (Cloud Run)            │
│  ┌───────────────────────────┐  │  │   - Renders .qgs proj    │
│  │ Gemini 3                  │  │  │   - Serves WMS/WMTS/WFS  │
│  └───────────────────────────┘  │  │   - QML styles           │
│  ┌───────────────────────────┐  │  │   - WMS-T temporal       │
│  │ Tool registry             │  │  └─────────────┬────────────┘
│  │  - registered tools       │  │                │
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

