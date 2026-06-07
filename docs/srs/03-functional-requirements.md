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
Cached artifacts live under `gs://<project-bucket>/cache/<source-class>/<hash>.<ext>` — same convention as the worked examples in Appendix B (`gs://bucket/cache/dem/<hash>.tif`, `gs://bucket/cache/buildings/ms_<hash>.fgb`). `<source-class>` is a stable identifier per atomic tool (`dem`, `landcover`, `buildings`, `nwis_iv`, `atcf`, `mrms_qpe`, `precipitation_atlas14`, etc.); `<hash>` is the content-addressed key per FR-DC-3; `<ext>` is the source-appropriate format (`tif` for COG rasters, `fgb` for FlatGeobuf, `json` for JSON payloads, `nc` for NetCDF, `grib2` for GRIB2, etc.). The bucket is shared across atomic tools and across sessions so the dedup guarantee (FR-DC-4) holds across users.

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
The `cache/` prefix uses GCS Object Lifecycle Management rules tied to the TTL classes: objects under `cache/<source-class>/` inherit a `daysSinceCustomTime > N` deletion rule where N is the TTL-class day count (30, 7, 1, or 0). The shim sets `customTime = fetched_at` on every write so the lifecycle policy can evict without the shim tracking individual TTLs at read time. Eviction is asynchronous; a slightly stale read between the bucket boundary and the lifecycle pass is acceptable (the next write through that key replaces it). Bucket versioning is off for the `cache/` prefix to keep storage cost flat.

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

