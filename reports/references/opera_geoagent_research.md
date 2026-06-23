# OPERA + GeoAgent Research (scope / reference)

RESEARCH specialist scope doc. NO application code. Targets: (1) NASA/JPL OPERA
data products and how to wire them as GRACE-2 fetch tools, and (2) the GeoAgent
QGIS plugin (opengeos) and which of its tools/patterns GRACE-2 should adopt.

Date: 2026-06-23. Sources are listed inline and collected at the end.

---

## TL;DR

- OPERA products are **NOT on Microsoft Planetary Computer STAC**. They live on
  NASA Earthdata (CMR-STAC) and are split across three DAACs: **PO.DAAC**
  (DSWx), **ASF DAAC** (the Sentinel-1 / SAR-derived line: RTC, CSLC, DISP,
  DIST-S1), and **LP DAAC** (DIST-HLS). **All require an Earthdata Login
  token.** So OPERA does NOT slot into our `tools/_pc_stac.py` SAS-token path;
  it needs a parallel Earthdata-auth fetch helper.
- We already ship an Earthdata-credential pattern: `fetch_firms_active_fire.py`
  resolves a NASA key vault-first (per-Case SSM secret -> env -> fallback) and
  surfaces an AUTH-ERROR -> credential-request card on a missing key. The OPERA
  fetchers should mirror that, swapping the FIRMS MAP_KEY for an Earthdata Login
  token (the `earthaccess` library is the canonical client; it reads
  `EARTHDATA_USERNAME`/`EARTHDATA_PASSWORD` env or a `.netrc`, and can mint a
  bearer token).
- All OPERA rasters are **30 m GeoTIFF (cloud-optimized / COG-readable via
  `/vsicurl/` once the request carries the Earthdata bearer/cookie)** -- so once
  authenticated, the windowed-COG read path is the same shape as `fetch_naip` /
  `compute_ndvi`.
- **GeoAgent (opengeos/GeoAgent, MIT-licensed) already ships a native
  `for_nasa_opera()` adapter** (8 tools, built on `earthaccess` + GDAL) and a
  QGIS adapter. It is the SAME author org as the leafmap/geemap/geoai ecosystem
  our conservation North Star already leans on. MIT means we can read it as a
  reference implementation or vendor pieces with attribution. Its OPERA tool set
  is close to a turnkey blueprint for our OPERA fetchers; its QGIS-adapter
  design (Qt GUI-thread marshalling + confirmation-gated PyQGIS fallback) is
  directly relevant to job-0308 (QGIS-on-AWS) and the "QGIS Processing as
  agentic compute" idea.

---

## (A) NASA OPERA Data Products

### What OPERA is

OPERA (Observational Products for End-Users from Remote Sensing Analysis) is a
NASA/JPL project (est. 2020) that turns Sentinel-1, Sentinel-2, Landsat-8/9
(via HLS), and the upcoming NISAR mission into analysis-ready hazard products,
answering the Satellite Needs Working Group (SNWG). All products are free and
open (Earthdata Login required); all OPERA software is open source.

### Product table

Resolution = 30 m and format = cloud-optimized GeoTIFF for every released L3
raster (the SAR L2 CSLC is complex-valued HDF5). "PC-STAC?" is **No** for every
row -- OPERA is on NASA CMR-STAC / Earthdata Cloud, not Microsoft PC.

| Product | Measures | Sensor(s) | Res | Start / status | DAAC + access/auth | PC-STAC? | Proposed GRACE-2 tool | Demo it strengthens |
|---|---|---|---|---|---|---|---|---|
| **DSWx-HLS** (`OPERA_L3_DSWX-HLS_V1`) | Dynamic surface water extent -- per-pixel open-water / partial-water / cloud classification | HLS = Landsat-8 + Sentinel-2 A/B/C (optical) | 30 m | Apr 2023, in production | **PO.DAAC**, Earthdata Login (token) via CMR-STAC / `earthaccess` | No | `fetch_opera_dswx` | **Flood North Star (top pick)** -- observed-vs-modeled inundation validation |
| **DSWx-S1** (`OPERA_L3_DSWX-S1_V1`) | Surface water extent from SAR (sees through clouds / at night) | Sentinel-1 A/B/C (C-band SAR) | 30 m | Sep 2024, in production | PO.DAAC, Earthdata Login | No | (same `fetch_opera_dswx`, `sensor="s1"`) | Flood North Star -- cloud-penetrating flood map for storm events where optical is occluded |
| **DSWx-NI** | Surface water from NISAR | NISAR L-band SAR | 30 m | ~Jun 2026 (upcoming) | PO.DAAC | No | (future flag) | Flood (future) |
| **DIST-ALERT-HLS** (`OPERA_L3_DIST-ALERT-HLS_V1`) | Land-surface DISTurbance -- vegetation-cover loss vs historical norm, low/high confidence strata, with date-of-disturbance | HLS (Landsat-8/9 + Sentinel-2) | 30 m | Jan 2023, in production | **LP DAAC**, Earthdata Login | No | `fetch_opera_dist` | **Fire / disturbance demo** -- rapid burn-extent + deforestation; complements MTBS (which is annual, US-only) with near-real-time global change |
| **DIST-ANN-HLS** (`OPERA_L3_DIST-ANN-HLS_V1`) | Annual disturbance summary (prior year) | HLS | 30 m | Jan 2023, in production | LP DAAC, Earthdata Login | No | (same tool, `product="ann"`) | Fire / land-change retrospective |
| **DIST-ALERT-S1** (`OPERA_L3_DIST-ALERT-S1_V1`) | Disturbance from SAR (cloud-penetrating; used for 2025 LA wildfire response) | Sentinel-1 SAR | 30 m | Mar 2026 (just released) | **ASF DAAC**, Earthdata Login | No | (same tool, `sensor="s1"`) | Fire response under smoke/cloud |
| **RTC-S1** (`OPERA_L2_RTC-S1_V1`) | Radiometric-Terrain-Corrected SAR backscatter (gamma-0); surface roughness / soil moisture / vegetation | Sentinel-1 SLC | 30 m | Jan 2022, in production | **ASF DAAC**, Earthdata Login | No | `fetch_opera_rtc` | Analysis-ready SAR base for change detection; input to custom flood/disturbance; soil-moisture context for hydrology |
| **RTC-S1-STATIC** (`OPERA_L2_RTC-S1-STATIC_V1`) | Radar-geometry static layers (local incidence angle, etc.) | derived | 30 m | in production | ASF DAAC | No | (helper asset) | Terrain-correction context |
| **CSLC-S1** (`OPERA_L2_CSLC-S1_V1`) | Coregistered Single-Look Complex (phase-preserved SAR for InSAR) -- the building block for displacement | Sentinel-1 SLC | ~15 m x 5 m | May 2016, in production | **ASF DAAC**, Earthdata Login (HDF5, complex) | No | (low priority -- raw InSAR input; prefer DISP) | Subsidence/landslide pipeline ingredient (advanced) |
| **DISP-S1** (`OPERA_L3_DISP-S1_V1`) | Surface DISPlacement (line-of-sight ground motion) -- subsidence, tectonics, landslides | Sentinel-1 (InSAR time series) | 30 m / ~15 m x 5 m | May 2016, in production | **ASF DAAC**, Earthdata Login | No | `fetch_opera_disp` | **Subsidence / landslide** hazard layer; levee/dam settlement; coastal subsidence amplifying flood risk |
| **VLM-S1** | Vertical Land Motion (calibrated absolute) | Sentinel-1 + GNSS | 100 m (TBD) | ~Apr 2028 (upcoming) | ASF DAAC | No | (future) | Subsidence (future, calibrated) |
| **TROPO** | Troposphere zenith radar delays (InSAR correction) | model / radar | 0.07 deg | Jul 2016, in production | ASF DAAC | No | (not user-facing; InSAR aux) | n/a (correction layer) |

Notes:
- "HLS" = Harmonized Landsat Sentinel-2 (NASA's already-coregistered optical
  surface-reflectance product); OPERA consumes it, so DSWx-HLS / DIST-HLS
  inherit the ~2-3 day combined revisit of Landsat-8/9 + Sentinel-2.
- DSWx revisit is "every few days" (combined optical + SAR constellation);
  DIST-ALERT updates at HLS cadence; RTC/CSLC/DISP update as new Sentinel-1
  acquisitions land (~6-12 day repeat per track).
- CSLC is HDF5 complex data, not a paint-on-the-map raster -- it is an
  InSAR-pipeline ingredient. For GRACE-2's "show observed ground motion" need,
  **DISP-S1 is the user-facing product**, not CSLC.

### Access mechanics (the key architectural point)

OPERA does NOT use the Microsoft PC SAS-token endpoint our `_pc_stac.py` is
built around. The OPERA access path is:

1. **Auth:** Earthdata Login (EDL). Either (a) `.netrc` with EDL user/pass, or
   (b) `EARTHDATA_USERNAME` / `EARTHDATA_PASSWORD` env, or (c) an EDL bearer
   token. The `earthaccess` Python library wraps all three (`earthaccess.login()`)
   and also configures GDAL HTTP cookies + (for in-region) S3 credentials.
2. **Search:** either `earthaccess.search_data(short_name="OPERA_L3_DSWX-HLS_V1",
   bounding_box=..., temporal=...)` (preferred; the CMR-STAC wrapper is noted as
   flakier than direct CMR in the community), returning granule asset URLs.
3. **Read:** the GeoTIFF assets are COG-readable via GDAL `/vsicurl/` once the
   request carries the EDL bearer token / cookie -- i.e. the SAME windowed-COG
   read mechanics as `fetch_naip` / `compute_ndvi`, only the auth header differs
   from a PC SAS query string.

Implication for our codebase: add a thin `tools/_earthdata.py` sibling to
`tools/_pc_stac.py` (EDL login + `earthaccess` search + a GDAL env that injects
the bearer cookie instead of a SAS query string). Reuse the existing
`_pc_stac.bbox_pixel_dims` sizing helper as-is. Resolve the EDL token vault-first
exactly like `fetch_firms_active_fire._resolve_map_key` (per-Case SSM secret ->
env -> typed AUTH-ERROR -> credential-request card), since FIRMS already proves
that NASA-credential UX seam.

### Recommended first OPERA tools (in order)

1. **`fetch_opera_dswx` (DSWx-HLS first, then `sensor="s1"`)** -- LEAD. This is
   the single highest-value OPERA product for GRACE-2: it gives an *observed*
   surface-water mask to lay against our SFINCS/SWMM *modeled* inundation. Plugs
   straight into the "computed-vs-observed" overlay the flood North Star already
   wants (NAVD88 rainbow key for modeled, DSWx observed water polygon/mask on
   top). DSWx-S1 adds the cloud-penetrating case (hurricanes, storms) where
   optical DSWx-HLS is occluded -- exactly the conditions our coastal/urban
   flood demos target.
2. **`fetch_opera_dist` (DIST-ALERT-HLS, then `sensor="s1"`)** -- near-real-time,
   global vegetation-disturbance / burn mapping. Fills a real gap: our existing
   fire-extent sources are MTBS (annual, US-only, retrospective) and active-fire
   point detections (FIRMS/VIIRS); DIST gives *change-detected disturbance
   polygons* at 30 m within days, and DIST-S1 sees through smoke/cloud (proven
   on the 2025 LA fires).
3. **`fetch_opera_disp` (DISP-S1)** -- a genuinely new hazard axis for GRACE-2:
   InSAR-derived ground displacement (subsidence, landslide, tectonic). No
   existing GRACE-2 tool produces ground-motion; this opens subsidence/landslide
   demos and adds "is this levee/coast subsiding?" context to flood risk. Lower
   urgency than DSWx/DIST but strategically distinct.

RTC-S1 is worth a `fetch_opera_rtc` later as an analysis-ready SAR base (input
to custom change detection / soil-moisture context), but it is a building block,
not a finished hazard layer -- defer behind the three above. CSLC and TROPO are
InSAR-pipeline internals, not user-facing; skip for v1.

### Cross-reference vs existing GRACE-2 tools (NEW vs have)

| OPERA tool | Status vs current tools |
|---|---|
| `fetch_opera_dswx` (observed flood water) | **NEW.** No GRACE-2 tool produces an observed surface-water mask. `fetch_fema_nfhl_zones` is regulatory floodplain (static), `fetch_cama_flood_discharge` is modeled discharge -- neither is satellite-observed standing water. |
| `fetch_opera_dist` (disturbance/burn) | **NEW source, overlapping intent.** Have `fetch_mtbs_burn_severity` (annual, US), `fetch_firms_active_fire` + `fetch_viirs_day_fire` (active-fire points), `fetch_nifc_fire_perimeters` / `fetch_wfigs_incident` (perimeters). DIST adds 30 m near-real-time global change-detected disturbance polygons -- a different modality. |
| `fetch_opera_disp` (displacement) / `fetch_opera_rtc` (SAR) | **NEW.** GRACE-2 has zero SAR / InSAR tools today (grep confirms no backscatter/displacement fetcher). |
| Earthdata auth helper (`_earthdata.py`) | **PARTIAL have.** `fetch_firms_active_fire` already does NASA-credential vault resolution + credential-request UX; generalize that pattern, swap FIRMS MAP_KEY for the EDL token / `earthaccess` login. |

---

## (B) GeoAgent (opengeos/GeoAgent)

### What it is + architecture

GeoAgent (github.com/opengeos/GeoAgent, **MIT license**) is a shared AI-agent
layer for the opengeos ecosystem -- leafmap, anymap, geemap, geoai, STAC, and
NASA Earthdata -- plus a QGIS plugin (OpenGeoAgent). Same authoring org as the
leafmap/geemap stack our conservation North Star already uses.

Architecture highlights (directly comparable to ours):

- **Built on Strands Agents.** Tools are plain Python functions decorated with
  `@geo_tool`, which converts them into structured Strands tools carrying
  metadata (category, destructive flag, confirmation-required). A
  `GeoToolRegistry` manages tool metadata + filtering (including a "fast-mode"
  subset). This is conceptually our `AtomicToolMetadata` + `register_tool` +
  catalog, but with an explicit destructive/confirmation flag baked into every
  tool's metadata.
- **Closure-binding pattern.** Package adapters are factory functions
  (`for_leafmap(map)`, `for_qgis(iface, project)`, `for_nasa_opera()`,
  `for_vantor()`) that close over live runtime objects (the map widget, the
  QGIS `iface`, an authenticated client) and expose ONLY structured parameters
  across the model boundary. Live objects never enter LLM-visible arguments.
  (We achieve the same separation differently -- tools receive a context, not
  closures -- but the principle of keeping session objects out of tool args is
  shared.)
- **Confirmation hooks.** The agent pauses before destructive / expensive /
  irreversible operations; tiered permission profiles ("Inspect only" ->
  approve-each -> auto-approve). This mirrors our solver-confirm / granularity
  gate, but generalized to a per-tool destructive flag rather than a few
  hand-wired confirm points.
- **Multi-provider** (OpenAI, Anthropic, Bedrock, Gemini, OpenRouter, LiteLLM,
  vLLM, Ollama). Notably includes **Bedrock** -- the same provider GRACE-2 runs
  on -- and Ollama, relevant to our local/offline-build stretch.

### Adoptable tools / patterns (NEW vs have)

| GeoAgent capability | What it does | GRACE-2 status | Adopt? |
|---|---|---|---|
| `for_nasa_opera()` -- 8 OPERA tools: `get_available_datasets`, `get_dataset_info`, `search_opera_data`, `display_footprints`, `display_raster`, `create_mosaic`, `count_water_pixels`, `analyze_categorical_raster` | OPERA discovery + Earthdata granule search (via `earthaccess`) + GDAL VRT mosaic + DSWx water-pixel counting + categorical-raster summary | **NEW** | **YES (highest leverage).** This is a near-complete reference impl for our OPERA fetchers. `search_opera_data` = the `earthaccess` search we need; `count_water_pixels` is exactly the DSWx flood-area readout that feeds our analytical-QA layer; `create_mosaic` (GDAL VRT) handles multi-granule AOIs. MIT -> vendor/port with attribution. |
| QGIS Processing execution + `list/describe algorithms` via the agent | Run any QGIS Processing algorithm from natural language; inspect layers; select-by-expression; attribute tables | **HAVE (partial).** We already ship `qgis_discovery.py` (`list_qgis_algorithms` / `describe_qgis_algorithm`) + a `qgis_process` passthrough. | Pattern-validate. Confirms our Processing-as-agentic-compute direction; their tool naming/granularity is a useful cross-check for job-0308. |
| **Qt GUI-thread marshalling** for QGIS calls | Safely marshals PyQGIS calls onto the QGIS GUI thread; runs remote-COG/STAC reads on background tasks so the UI never blocks | **NEW (relevant to job-0308).** | **YES (study).** Our QGIS-on-AWS worker is headless (no GUI thread), so the exact marshalling differs, but the "heavy reads off the interactive thread, results marshalled back" discipline maps onto our `_ALWAYS_OFFLOAD_SYNC_TOOLS` / `asyncio.to_thread` rule. |
| **Confirmation-gated PyQGIS fallback** | When no dedicated tool fits, the agent generates a PyQGIS script run with `iface`/`project`/`canvas`/`active_layer` in scope, gated by user approval | **NEW (capability gap).** We have `code_exec_tool` (sandboxed Python) + the discovery loop, but no "agent-authored PyQGIS script, confirm, then run in the QGIS context" escape hatch. | **YES (design input for job-0308).** A confirm-gated PyQGIS-script tool against the QGIS-on-AWS worker would be the general escape hatch beyond pre-wired algorithm wrappers -- but must run in our sandboxed/headless worker, NOT a GUI `iface`. |
| STAC mode: read map extent -> search collections -> load remote COG via `/vsicurl/` on a background task | Generic STAC browse-and-load | **HAVE.** `_pc_stac.py` + `discover_dataset` + windowed-COG read across our fetchers. | No port needed; ours is more hardened (SAS signing, typed no-coverage errors). |
| `create_mosaic` (GDAL VRT from multiple raster URLs) | Stitches multi-granule AOIs into one virtual raster | **NEW utility.** Our fetchers mostly read a single best item windowed to bbox. | **MAYBE.** Useful when an AOI spans multiple OPERA tiles/granules (30 m tiles are ~100-200 km, so usually one, but border AOIs span two). Worth a small VRT helper. |
| `count_water_pixels` / `analyze_categorical_raster` | Post-fetch categorical summaries (e.g. flooded-area km2, disturbance class counts) | **HAVE (adjacent).** We have `compute_zonal_statistics` + `analytical_qa` + chart tools. | Pattern overlap -- wire OPERA categorical outputs INTO our existing zonal/QA layer rather than porting these. |
| Multi-provider incl. Bedrock + Ollama; per-tool destructive/confirm metadata | Provider abstraction; destructive flag per tool | **HAVE (Bedrock).** Our `bedrock_adapter.py` is the live path. | The per-tool destructive flag is a small, clean idea worth folding into `AtomicToolMetadata` if we ever expose write/delete tools. |

### Licensing note

GeoAgent is **MIT-licensed** -- permissive. We may read it as a reference,
re-implement its approach freely, or vendor/port portions provided we retain the
MIT copyright notice + attribution. Its dependencies are the standard open
geospatial stack (`earthaccess`, GDAL, Strands Agents, leafmap/geemap) -- all
permissive. No copyleft. The cleanest path: port the `nasa_opera` adapter's
search/read/summarize logic into our tool style (typed errors, vault auth,
`_ALWAYS_OFFLOAD_SYNC_TOOLS`, `publish_layer` rendering) rather than taking a
runtime dependency on GeoAgent (which is Strands-based and would conflict with
our Bedrock-adapter agent loop). Strands Agents itself is also a NEW data point
worth a glance for our agent-framework evolution, but adopting it is out of
scope here.

---

## Wiring summary (what to build, in order)

1. `tools/_earthdata.py` -- EDL login + `earthaccess` granule search + a GDAL
   `/vsicurl/` env that injects the EDL bearer cookie (sibling to `_pc_stac.py`;
   reuse `bbox_pixel_dims`). Vault-first token resolution copied from
   `fetch_firms_active_fire._resolve_map_key`, with the FIRMS MAP_KEY swapped for
   an Earthdata Login token, surfacing the same AUTH-ERROR -> credential-request
   card.
2. `fetch_opera_dswx` (DSWx-HLS, then `sensor="s1"`) -- the flood-validation
   lead; emit an observed-water mask layer + a flooded-area readout feeding
   `analytical_qa`. Port `count_water_pixels` semantics from GeoAgent.
3. `fetch_opera_dist` (DIST-ALERT-HLS, then `sensor="s1"`) -- near-real-time
   disturbance/burn polygons for the fire demo.
4. `fetch_opera_disp` (DISP-S1) -- subsidence/landslide ground-motion layer (new
   hazard axis). Defer `fetch_opera_rtc` (analysis-ready SAR base) behind these.
5. For job-0308: adopt GeoAgent's **confirmation-gated PyQGIS-script escape
   hatch** pattern (adapted to our headless/sandboxed QGIS-on-AWS worker, run
   off the interactive thread) as the general fallback beyond pre-wired
   algorithm wrappers.

---

## Sources

- OPERA Products -- NASA JPL: https://www.jpl.nasa.gov/go/opera/products/
- OPERA DSWx suite -- NASA JPL: https://www.jpl.nasa.gov/go/opera/products/dswx-product-suite/
- OPERA DIST suite -- NASA JPL: https://www.jpl.nasa.gov/go/opera/products/dist-product-suite/
- OPERA RTC product -- NASA JPL: https://www.jpl.nasa.gov/go/opera/products/rtc-product/
- OPERA project -- NASA Earthdata: https://www.earthdata.nasa.gov/data/projects/opera
- OPERA Near-Global DSWx (SNWG) -- NASA Earthdata: https://www.earthdata.nasa.gov/about/nasa-support-snwg/solutions/opera-near-global-dswx
- OPERA L2 products now available from ASF -- NASA Earthdata: https://earthdata.nasa.gov/news/opera-level-2-products-now-available-from-asf-daac
- OPERA -- PO.DAAC Cookbook: https://podaac.github.io/tutorials/quarto_text/OPERA.html
- OPERA GIS Cloud (CMR-STAC / earthaccess) -- PO.DAAC Cookbook: https://podaac.github.io/tutorials/notebooks/datasets/OPERA_GIS_Cloud.html
- OPERA_Applications (DSWx via CMR-STAC notebooks) -- OPERA-Cal-Val: https://github.com/OPERA-Cal-Val/OPERA_Applications
- GeoAgent repo (MIT) -- opengeos: https://github.com/opengeos/GeoAgent
- GeoAgent QGIS plugin doc: https://github.com/opengeos/GeoAgent/blob/main/docs/qgis-plugin.md
- GeoAgent NASA OPERA adapter (source): https://github.com/opengeos/GeoAgent/blob/main/geoagent/tools/nasa_opera.py
- GeoAgent QGIS plugin listing: https://plugins.qgis.org/plugins/geo_agent/
- Microsoft Planetary Computer STAC (confirms OPERA absent): https://planetarycomputer.microsoft.com/catalog
