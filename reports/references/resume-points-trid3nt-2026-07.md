# TRID3NT / GRACE-2 — Resume Points (extensive master list)

Pick-and-choose source list. Bullets are grouped by theme and written
resume-ready (action verb + technology + quantified outcome). Tune tense/person
per application. Everything below is factual to the project as of 2026-07.

## One-line project descriptions (pick one per resume)

- Built TRID3NT, an AI-driven multi-hazard modeling workbench: a conversational agent that fetches geospatial data, runs physics simulations (flood, groundwater, seismic, coastal), and renders results on an interactive map — solo, full-stack, cloud and offline.
- Designed and shipped an LLM agent platform that turns natural-language prompts into end-to-end scientific simulations (SFINCS, MODFLOW 6, SWMM, GeoClaw, SWAN, OpenQuake, Landlab) with live map visualization.
- Sole engineer on a production AI geospatial platform spanning React/MapLibre frontend, Python asyncio agent backend, AWS scale-to-zero infrastructure, and 7 numerical simulation engines.

## AI / LLM engineering

- Architected a tool-calling LLM agent with a catalog of 182 registered tools (data fetchers, geoprocessing, simulation composers) behind a single typed registry with per-tool caching, TTL classes, and telemetry.
- Built a pluggable model-provider seam supporting AWS Bedrock (Claude Sonnet/Haiku, Nova) and any OpenAI-compatible endpoint (Ollama, llama.cpp), enabling the same agent to run against cloud or fully local models.
- Implemented RAG-style tool retrieval (BM25 + dense embeddings, top-K selection) so an 8B local model with a 16k context can operate a 180+-tool catalog that would otherwise exceed its context window by ~2x.
- Diagnosed and fixed a silent context-overflow failure mode in local LLM tool-calling (lazy retrieval index caused fail-open to all 176 schemas -> silent truncation), raising tool-selection accuracy from 35.7% to 57.1% on a routing benchmark.
- Ran a systematic 3-pass audit of the full tool catalog: direct execution of all 176 tools, curated-argument retries, and a per-tool LLM routing benchmark with automated retrieval-vs-model failure attribution.
- Preserved prompt-caching (Bedrock cachePoint) across model swaps as a first-class cost control; built per-model telemetry to A/B cheaper models.
- Designed honest-failure discipline into every tool: typed errors, primary->fallback->honest-error data source chains, and a "model-run honesty floor" so empty results can never masquerade as success.
- Built parameter-confirmation gates so the LLM cannot launch expensive simulations without explicit user sign-off on extracted parameters (release volumes, durations, mesh resolution).
- Implemented NLP parameter extraction from news-article text (contaminant, spill volume with unit conversion, duration, geocoded location) to auto-parameterize groundwater contamination simulations.

## Scientific computing / simulation engines

- Integrated 8 physics engines end-to-end behind one solver abstraction: SFINCS (coastal/pluvial flood), MODFLOW 6 + MF6-GWT (groundwater flow and contaminant transport), PySWMM (urban drainage), GeoClaw (tsunami), SWAN (waves), OpenQuake (seismic hazard), Landlab (landscape evolution), ELMFIRE (wildfire spread), plus Pelicun damage assessment.
- Took ELMFIRE from research spike to live-proven engine in one day: multi-stage container build (rewrote the upstream Dockerfile that shipped compilers, 522MB), verification within 1.2-2.4% of the exact Behave ellipse, a grid-aligned deck builder with hard geotransform asserts (the identified top silent-failure risk), and a composer chain live-proven on real LANDFIRE fuels + real 3DEP terrain (501-acre burn, animated time-of-arrival COGs).
- Built 18 MODFLOW scenario archetypes (sustainable yield, saltwater intrusion via variable-density BUY, MAR/ASR, capture zones via PRT particle tracking, multi-species transport) as data-driven composer tools.
- Covered the full SFINCS scenario space: pluvial, fluvial, coastal surge, compound, wind-driven, infiltration, levee breach, and tsunami forcing.
- Reproduced a published Deltares Hurricane Michael coastal-flood demo (SFINCS quadtree + SnapWave, computed-vs-observed) as the platform's coastal North Star.
- Debugged a GeoClaw tsunami setup to first correct inundation through a 9-fix chain, using mass-conservation diagnostics to distinguish numerical artifacts from real waves.
- Implemented adaptive mesh/grid budgeting: AOI-driven resolution autoscaling with a user-controlled granularity gate before every run.
- Built engine post-processing to visualization: NetCDF/GeoTIFF -> COG with embedded colormaps, time-stepped flood animation frames, quadtree mesh layers, and deck.gl-ready decks.

## Cloud architecture & infrastructure (AWS)

- Led a full GCP-to-AWS platform migration (Cloud Run/Vertex -> EC2/Fargate/Batch/Bedrock) while keeping the product live.
- Designed a scale-to-zero architecture and executed it in 4 gated phases, cutting idle cost ~73% (~$92 -> ~$24/mo) with zero downtime, each phase verified by an automated end-to-end flood simulation smoke test.
- Built per-user agent isolation: CloudFront -> WebSocket broker -> per-session Fargate task provisioning on first connect, with crash isolation proven live.
- Replaced an always-on ALB + Fargate broker with the same container as a systemd unit co-hosted on an existing t3.small, flipping CloudFront origins with instant rollback (~$32/mo saved).
- Implemented an agent-self-reported heartbeat (DynamoDB route rows) that let the idle-reaper Lambda run outside the VPC, eliminating ~$29/mo of Interface VPC endpoints.
- Ran numerical solvers on AWS Batch with Spot-first capacity (capacity-optimized, 20 instance types x 4 AZs), on-demand fallback, and scale-to-zero compute environments.
- Right-sized the agent container from 16GB to 6GB using live peak-RSS telemetry piggybacked on the heartbeat (measured 1.9GB peak on a full simulation turn).
- Built auto-stop/wake EC2 lifecycle for the agent host with a client-side wake overlay, plus an isolated always-on tile server so the map serves 24/7 while compute scales to zero.
- Diagnosed production incidents to root cause: S3 CORS blocking cold-view fetches, WebSocket 30s reconnect storms from browser-invisible PING frames (fixed with server data-frame heartbeats), asyncio event-loop starvation from synchronous GDAL work (fixed with a staged to-thread offload system).
- Everything as code: OpenTofu/Terraform roots for agent isolation, Batch, TiTiler, autostop; CodeBuild image pipelines; custom SSM Run Command deploy documents (org SCP blocked the AWS-managed one).

## Geospatial engineering

- Built 120+ data-fetch tools over public APIs and archives: USGS (earthquakes, NWIS, groundwater, water quality), NOAA (NEXRAD, MRMS, HRRR zarr, GOES/GLM satellite, CO-OPS tides, sea-level-rise), FEMA NFHL, Census/ACS, Sentinel-1/2, Landsat, MODIS, SoilGrids, SNOTEL, OSM/Overpass, and more.
- Served raster layers via TiTiler (dynamic COG XYZ tiles from S3) behind CloudFront; vector layers as FlatGeobuf with server-side densification caps and inline GeoJSON for cold views.
- Implemented raster analytics as tools: hillshade, slope, aspect, colored relief, contours, NDVI, zonal statistics, terrain profiles, cross-sections, blended composites, canopy height (ML model).
- Built CV-based vectorization workflows (NDWI water-body digitization from Sentinel-2) and field-boundary analytics joining simulation plumes to agricultural parcels (fiboa/Fields-of-the-World).
- Processed satellite animation products: GOES fire-temperature and GeoColor loops, GLM lightning group-energy-density accumulation onto fixed grids from raw 20-second LCFA granules on AWS Open Data.
- Wrote a curated public-data-source catalog (STAC/OGC/ArcGIS REST tiers) with a generic OGC adapter (WMS/WCS/WFS/ArcGIS REST query construction).

## Full-stack web

- Built the SPA in React + TypeScript + MapLibre GL with deck.gl visualization, terra-draw spatial input (AOI drawing, pick mode), time-series animation scrubber, draggable legends, and per-case layer durability across reconnects.
- Designed the WebSocket protocol (typed envelopes, pydantic contracts shared Python<->TypeScript) with outbound frame queueing that survives 60-90s cold-provision waits and mid-wake reconnects.
- Implemented client resilience: capped-jitter reconnect, durable localStorage auth mirrors, cold case-view snapshots served without any backend compute, honest loading states gated on durable signals.
- Shipped continuous deploys via Vercel (frontend) and container pipelines (backend), with Playwright end-to-end verification of the authed app after every web deploy.
- Cognito authentication with an ephemeral judge-access code gate (demo code -> ephemeral user -> auto-cleanup) for zero-friction reviewer access.

## Offline / edge build (TRID3NT Local)

- Ported the entire cloud platform to run 100% locally: same agent + web UI against MinIO (S3 API), local TiTiler, FilePersistence, and local solver execution (native mf6, docker SFINCS/GeoClaw/SWAN, subprocess SWMM/Landlab/OpenQuake).
- Proved LLM-driven simulation end-to-end on an 8GB consumer GPU (qwen3:8b, 16k context): the local model itself invoked and parameterized MODFLOW and SFINCS runs with zero nudges, rendering depth animations in the local UI.
- Benchmarked local model tiers (3B/8B/9B) for structured tool-calling reliability and settled a model matrix (3B narrates but cannot call; 8B calls reliably at 16k with thinking-mode disabled).
- Found and fixed an environment-level S3 redirect bug: a global AWS_ENDPOINT_URL (MinIO) silently redirected anonymous public NOAA bucket reads, breaking satellite/weather tools with misleading errors; fixed with endpoint-pinned anonymous clients.
- Swept the full 182-tool catalog against the local stack in three resumable passes (direct execution, curated args, chained layer inputs): 137+ tools proven end-to-end, API-key-gated sources earmarked, every real failure root-caused and fixed rather than skipped.
- Built an LLM tool-routing benchmark from tool docstrings (172 prompts, case-seeded, verdicts scored by a judge harness) plus a retrieval-vs-model failure splitter that replays failed prompts through the tool-retrieval layer; proved 100% of routing failures were model misses, not retrieval misses, redirecting effort from RAG tuning to a model upgrade.
- Designed an LLM self-improvement loop: failed-then-corrected tool calls distilled into "lessons" (JSONL store, LRU-capped), retrieved by BM25 into a token-budgeted system-prompt appendix behind a dark-launch flag, then A/B-benchmarked against the dark baseline on the full routing sweep.
- Ran a controlled 4-cell A/B (dark baseline / lessons-on / bigger 9B quant / gated lessons) with flip analysis showing run variance dwarfed every treatment effect - the disciplined negative result that redirected effort from prompt tweaks to interaction design; caught two silent-capability-loss incidents on the way (chat-template context overflow silently truncating tool schemas; an unarmed env flag making a treatment arm secretly identical to control).
- Defined and measured "usable coverage" (tool reachable in <= 2 user turns): 87% for an 8B model on a 183-tool catalog vs 24-26% cold single-prompt accuracy - the metric that reflects what users actually experience.
- Hardened tool dispatch against small-model failure modes: placeholder handle resolution (models passing 'LayerURI_from_fetch_dem' instead of real handles resolve deterministically to the producing tool's registered output when unambiguous) plus derived-argument defaults, turning a 0-for-7 demo flow into 5-for-5.

## QGIS plugin (TRID3NT for QGIS)

- Built a QGIS plugin that embeds the agent chat dock inside QGIS: streamed agent layers materialize natively (TiTiler XYZ rasters, /vsicurl/ FlatGeobuf vectors), map canvas or selected polygon becomes the area-of-interest, and the pre-run resolution gate renders as real Qt cards.
- Wrote a dependency-free RFC 6455 WebSocket client in pure stdlib (QGIS's bundled Python ships no WS library): handshake, masking, fragmentation, ping/pong, capped-jitter reconnect with session resume, all unit-tested against a scripted stub server.
- Caught a shipped Qt crash class with a real-Qt headless test tier (offscreen QgsApplication + fake iface): a pyqtSignal named `event` shadowed the C++ virtual QObject.event(), qFatal-aborting the host app on first connect - invisible to pure-python tests, reproduced on first run of the real-Qt driver.
- Diagnosed a cross-run test-pollution bug byte-by-byte at the wire level (identical JSON, one client rejected): a stub-server user id persisted into the real QSettings profile and poisoned live handshakes; fixed with a read-side ULID guard plus save/restore hygiene in the test driver.
- Native QGIS Temporal Controller animation: frame-sequence rasters (flood-depth timesteps) are stamped with per-layer temporal ranges on materialize, so QGIS's built-in scrubber plays simulation animations like the web app - proven live on a SFINCS flood case.
- Styled exports: the case exporter writes QML sidecars from the same TiTiler style translation the web uses (blue depth ramps, transparent nodata), fixing black-raster exports - and uncovered a case-sensitivity bug silently degrading every styled export to the wrong colormap.
- Live pull-and-render proven end-to-end: a prompt typed in the QGIS dock drives the local LLM to fetch real USGS elevation, confirm the resolution gate, publish tiles, and render terrain-styled XYZ over OSM in ~70 seconds, fully offline-capable.
- 85+ plugin tests across pure-python and real-Qt tiers; one-command profile install; dual local/remote-cloud modes sharing one client.

## Reliability, testing, ops

- Maintained a 10,000+ test pytest suite plus vitest/tsc on the frontend; every infrastructure change gated by a scripted end-to-end flood simulation driven through the public product surface (headless WebSocket driver with a small LLM).
- Built an ops watch pipeline: hourly scripted health checks dispatching an LLM anomaly detector, plus a 24/7 EventBridge Lambda watchdog; caught and fixed a Fargate orphan-task leak (37 zombie tasks) with an armed reaper and agent self-idle-exit.
- Ran a 35-finding code audit of the agent service with severity triage; adversarial-review discipline (reviewers re-run acceptance commands rather than trusting reports).
- Cost discipline as process: decommission predecessors, inspect container images pre-push (multi-stage, size budgets), prompt-caching carried across model swaps, cost as a review dimension.
- Wrote a comprehensive MkDocs Material documentation site so other contributors/LLMs can understand and extend the system.

## Process / leadership (solo + AI-orchestrated)

- Operated an AI-assisted delivery pipeline: work decomposed into sprints of frozen-kickoff jobs, executed by specialized agent roles with adversarial review gates, with immutable reports and an append-only project log.
- Practiced verify-then-mutate infrastructure discipline: plan-first applies, one reversible change at a time, rollback documented before every cutover, live smoke test after every step.
- Root-caused live demo incidents under time pressure across the full stack (browser -> CDN -> WebSocket -> agent -> solver -> S3), repeatedly converting "mystery flakiness" into single-line root causes with durable fixes.

## Technology keyword bank (for ATS matching)

Python (asyncio, pydantic, boto3, rasterio, GDAL, geopandas, xarray, numpy),
TypeScript, React, MapLibre GL, deck.gl, Vite, Playwright, vitest,
AWS (Fargate/ECS, Batch, Lambda, EC2, S3, CloudFront, DynamoDB, Cognito,
Bedrock, CodeBuild, SSM, EventBridge, SNS, IAM, VPC endpoints, Spot),
OpenTofu/Terraform, Docker (multi-stage), MinIO, TiTiler, WebSockets,
Ollama / llama.cpp / OpenAI-compatible APIs, RAG / embeddings / BM25,
SFINCS, MODFLOW 6, MF6-GWT, PySWMM, GeoClaw (Clawpack), SWAN, OpenQuake,
Landlab, Pelicun, HydroMT, FloPy, STAC, OGC (WMS/WCS/WFS), ArcGIS REST,
COG/GeoTIFF, FlatGeobuf, NetCDF, Zarr, QGIS/PyQGIS, PyQt/Qt, Vercel, Git.
