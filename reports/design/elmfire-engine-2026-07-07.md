# ELMFIRE Wildfire-Spread Engine - Research Spike + Integration Design

- **Date:** 2026-07-07
- **Author:** research spike (NO CODE written; no processes touched)
- **Status:** DESIGN - awaiting NATE go
- **Engine:** ELMFIRE (Eulerian Level set Model of FIRE spread), Chris Lautenberger / Cloudfire Inc.
- **Primary sources:** https://elmfire.io (docs 2025.0212), https://github.com/lautenberger/elmfire (EPL-2.0, latest release 2025.0526, ~499 commits, actively maintained), Lautenberger 2013, "Wildland fire modeling with an Eulerian level set method and automated calibration," Fire Safety Journal 62:289-298 (https://www.sciencedirect.com/science/article/abs/pii/S0379711213001343)

## Verdict up front

ELMFIRE is the right wildfire-spread engine for TRID3NT. It is a headless Linux
Fortran solver with a first-party Dockerfile, consumes exactly the LANDFIRE
30 m raster stack we already fetch (fbfm40/cbh/cbd live today; cc/ch are a
2-line layer-map extension), takes weather as GeoTIFF stacks we can build from
HRRR + gridMET, and emits GeoTIFF outputs (time-of-arrival, flame length,
spread rate, burn probability) that drop straight into our COG -> TiTiler ->
animation pipeline. It is operationally proven (Pyrecast forecasts, utility
risk work) and fits the existing run_solver Batch/local-docker seam with no
new architecture. The GeoClaw lesson is honored: the canonical known-good runs
are the repo's own tutorials 01 (synthetic, seconds) and 03 (real LANDFIRE
fuels, 60 km x 60 km) - we reproduce those in-container BEFORE wiring any
agent code.

---

## 1. Inputs: what ELMFIRE needs vs what we already have

Authoritative input spec: https://elmfire.io/user_guide/io.html. All GIS
inputs are **GeoTIFF**, and all must share **the same projection, resolution,
and extents** - a projected SRS (cell size in meters; domain corners in
projected coordinates via the `&COMPUTATIONAL_DOMAIN` namelist's `A_SRS` +
cellsize + corner keys). This same-grid requirement makes a deterministic
"input deck builder" (gdalwarp-align everything onto one EPSG:5070 or UTM
30 m grid) the core new build step.

### 1.1 Fuels + topography directory (10 single-band rasters)

| ELMFIRE file | Content / units | Type | Do we have it? |
|---|---|---|---|
| `fbfm40.tif` (FBFM_FILENAME) | Scott and Burgan 40 fuel model codes | Int16 | **YES** - `fetch_landfire_fuels(layer="fbfm40")` (LF2022 CONUS ImageServer at lfps.usgs.gov, verified live in-code 2026-06-08) |
| `cbh.tif` | canopy base height, m x 10 | Int16 | **YES** - `fetch_landfire_fuels` / `fetch_usfs_canopy_fuels` (units already match ELMFIRE's x10 convention) |
| `cbd.tif` | canopy bulk density, kg/m3 x 100 | Int16 | **YES** - same two fetchers (x100 convention matches) |
| `cc.tif` | canopy cover, percent | Int16 | **NEW layer** - add `LF2022_CC_CONUS` to the existing `_LAYER_SERVICE` map in fetch_landfire_fuels.py |
| `ch.tif` | canopy height, m x 10 | Int16 | **NEW layer** - add `LF2022_CH_CONUS` (same ImageServer family) |
| `dem.tif` | elevation, m | Int16 | **YES** - 3DEP / Copernicus DEM fetchers |
| `slp.tif` | slope, degrees | Int16 | **YES** - `compute_slope` (or gdaldem in the deck builder) |
| `asp.tif` | aspect, degrees | Int16 | **YES** - `compute_aspect` (or gdaldem) |
| `adj.tif` | spread-rate adjustment factor | Float32 | **GENERATED** - constant 1.0 raster |
| `phi.tif` | level-set init field | Float32 | **GENERATED** - constant 1.0 (unburned); negative cells = pre-burned, which is ALSO the perimeter-ignition mechanism (see 1.4) |

LANDFIRE availability facts (from our own shipped fetcher, verified live):
CONUS-wide 30 m, LF2022 vintage pinned (LF2023/24/25 ImageServers also
exist), no API key, `exportImage` returns bbox-clipped GeoTIFF. Known
constraints already encoded in the fetcher: CONUS-only at v0.1 (AK/HI/PRVI
mosaics exist, dispatch deferred as OQ-0111-LANDFIRE-REGION-DISPATCH); the
LFPS async geoprocessing `submitJob` route is intercepted by the LFPS web UI
(OQ-0111-LFPS-SUBMITJOB-INTERCEPT) so ImageServer `exportImage` is the
substrate; size clamp 4096 px/axis caps a single fetch at ~123 km at 30 m -
county-size AOIs fit comfortably.

**Projection gap (important):** our fetcher requests `imageSR=4326`, but
ELMFIRE needs a uniform projected grid. Fix inside the new deck builder
(gdalwarp everything to EPSG:5070 at 30 m), or extend the fetcher with an
optional `imageSR=5070` passthrough to skip one resample of categorical
fuels (nearest-neighbor resampling MUST be used for fbfm40 - it is a class
code raster).

### 1.2 Weather directory (5 rasters, Float32, single- or multi-band)

| ELMFIRE file | Content / units | Source mapping |
|---|---|---|
| `ws.tif` | wind speed, **mph at 20 ft** | HRRR `10m_u_wind`/`10m_v_wind` (fetch_hrrr_forecast has both + derived speed); 10 m -> 20 ft via the standard ~0.87 reduction factor; m/s -> mph |
| `wd.tif` | wind direction, degrees | derived from HRRR u/v (new derived variable or computed in the deck builder) |
| `m1.tif` | 1-hr dead fuel moisture, % | estimate from HRRR 2m T + RH (NFDRS-style) or constant scenario value (tutorials use 3%) |
| `m10.tif` | 10-hr dead fuel moisture, % | ditto (4%) or gridMET-informed |
| `m100.tif` | 100-hr dead fuel moisture, % | **gridMET `fm100`** - fetch_gridmet already ships it |
| optional `mlh`/`mlw` | live herbaceous/woody moisture, % | constants (tutorial defaults 30%/60%) at v1 |

Multi-band ("stacked") weather rasters give transient forcing with
`DT_METEOROLOGY` (typically 3600 s) between bands - i.e., an hourly HRRR
forecast becomes one multi-band GeoTIFF per weather variable. Single-band =
constant weather (v1 scenario mode; matches tutorials 01/03).
Gap noted: fetch_hrrr_forecast has no 2m RH variable today; v1 can use
scenario-constant fuel moistures (canonical for "what if" runs), v2 adds RH.

### 1.3 Topography

Covered above - DEM we have; slope/aspect are derived deterministically.
ELMFIRE wants degrees for both, Int16.

### 1.4 Ignition

Two mechanisms (io.html):
- **Point ignitions:** `&SIMULATOR` namelist, `NUM_IGNITIONS` + `X_IGN(i)` /
  `Y_IGN(i)` / `T_IGN(i)` in projected domain coordinates (up to 100). Maps
  directly onto our spatial-input/pick machinery: user clicks a point, we
  transform lon/lat -> domain SRS.
- **Perimeter ignition:** initialize `phi.tif` with negative values inside
  the burning polygon - "fire spread will be initiated from those pixels."
  Pairs with `fetch_nifc_fire_perimeters` / `fetch_firms_active_fire` for
  live-fire replication scenarios.

## 2. How it runs

- **Language/build:** Fortran (54% of repo) + shell + Python. Built with
  gfortran + OpenMPI via `build/linux/make_gnu.sh`, producing
  `elmfire_<VERSION>` and `elmfire_post_<VERSION>` executables. Ubuntu Server
  24.04 is the documented reference platform; apt deps are bc, csvkit,
  gdal-bin, gfortran, git, jq, libopenmpi-dev, openmpi-bin, pigz, python3,
  pip, unzip, wget, zip (https://elmfire.io/getting_started.html).
- **Config:** a single Fortran namelist file `elmfire.data` with groups
  `&INPUTS` (directories + raster names, no .tif suffix), 
  `&COMPUTATIONAL_DOMAIN` (A_SRS, cellsize, corner coords), `&TIME_CONTROL`
  (duration, CFL), `&SIMULATOR` (ignitions), `&OUTPUTS` (dump flags,
  DTDUMP), `&MONTE_CARLO`, `&SPOTTING`. Invocation is
  `elmfire_<VERSION> elmfire.data` (mpirun-wrapped for parallel runs); the
  tutorials drive it through small bash scripts (`01-run.sh`).
- **Deterministic vs Monte Carlo:** deterministic single-fire is the default;
  `&MONTE_CARLO` enables ensembles (https://elmfire.io/user_guide/monte_carlo.html):
  `NUM_ENSEMBLE_MEMBERS` realizations with randomized ignition locations
  (`RANDOM_IGNITIONS`), weather-band selection, and stochastic perturbation
  of 13 raster types (M1/M10/M100, WS/WD, canopy, etc. via uniform PDFs);
  `CALCULATE_BURN_PROBABILITY=.TRUE.` aggregates perimeters into a 3-band
  `burn_probability.tif` (burn prob, passive crown, active crown).
- **Runtime scale:** the level-set solver is much faster than real time -
  tutorial 01 (400x400 cells, 6 h 10 m of fire) runs "in a few seconds";
  tutorial 03 is a real-fuels 60 km x 60 km domain (2000x2000 at 30 m -
  larger than most county AOIs) driven end-to-end by one laptop-scale
  script. A deterministic county run at 30 m is seconds-to-minutes on a
  4-8 vCPU Batch task; cost lives in Monte Carlo (N members x that), which
  is embarrassingly parallel.
- **Containerization:** the repo ships a first-party multi-stage
  `Dockerfile` (Ubuntu 22.04 base, builds via make_gnu.sh, strips build
  tools after) plus `docker-compose.yml` (bash entrypoint, shared volume,
  optional SLURM). GitHub Actions include a "Push image" workflow, but no
  stable public registry path is documented - per our container-hygiene
  norm we build our OWN pinned image from the release tarball anyway
  (SHA-pinned, like the GeoClaw image).
- **Cloudfire caveat:** tutorials 03/04 fetch fuels/weather/ignition via
  gRPC microservices against `worldgen.cloudfire.io` (`fuel_wx_ign.py` in
  the repo's cloudfire/ dir). We must NOT take that external-server
  dependency in production - our own fetchers replace it. It remains useful
  as a reference implementation of the input-deck recipe.

## 3. Outputs -> our pipeline

Per-timestep GeoTIFFs, dump-gated in `&OUTPUTS`, named with ensemble member
+ simulation time (io.html):

| Output | Units | Flag | TRID3NT mapping |
|---|---|---|---|
| Time of arrival | s | DUMP_TIME_OF_ARRIVAL | THE headline layer: one raster encodes the whole spread history; postprocess thresholds it per hour into animation frames (same frames.py machinery as GeoClaw/SFINCS depth animations), plus a static classified ToA COG |
| Flame length | ft | DUMP_FLAME_LENGTH | continuous COG, fire-intensity ramp (convert to m) |
| Fireline intensity | kW/m | DUMP_FLIN | continuous COG |
| Spread rate | ft/min | DUMP_SPREAD_RATE | continuous COG |
| Crown fire occurrence | class | DUMP_CROWN_FIRE | categorical COG |
| Hourly isochrones | shapefile | DUMP_ISOCHRONE_SHAPEFILES | vector -> GeoJSON perimeter rings (inline vector path) |
| Burn probability (MC) | 3-band % | CALCULATE_BURN_PROBABILITY | probability COG (Monte Carlo mode only) |
| Fire size stats / acreage | CSV | DUMP_FIRE_SIZE_STATS etc. | typed numbers for the narration honesty floor (never free-generated) |

All rasters arrive in the domain SRS (EPSG:5070/UTM); postprocess does
gdalwarp -> EPSG:4326 COG -> runs bucket -> LayerURI, identical to
postprocess_geoclaw.py. Outputs are already GeoTIFF, so this is the
LIGHTEST postprocess of any engine we have (no fort.q ASCII parsing, no
NetCDF).

## 4. Minimal known-good test case (GeoClaw lesson applied)

Reproduce, in order, INSIDE our container before any agent wiring:

1. **`tutorials/01-constant-wind`** (`./01-run.sh`) - THE canonical smoke
   test (https://elmfire.io/tutorials/tutorial_01.html). Fully
   self-generating (no network): 400x400 x 30 m flat domain, fuel model 102,
   15 mph constant wind, point ignition, 22,200 s sim. Runs in seconds.
   Expected outputs: time-of-arrival, spread rate, fireline intensity
   GeoTIFFs + hourly isochrone shapefile - a downwind-elongated ellipse.
   Acceptance: outputs exist, are valid GeoTIFFs, ellipse is downwind.
2. **`verification/` cases 01 (elliptical fire shape) + 02 (crown fire)** -
   compare against exact solutions (https://elmfire.io/verification.html);
   run as the container's regression gate.
3. **`tutorials/03-real-fuels`** - first real-LANDFIRE run (60 km x 60 km NE
   of Merced, CA, LF 2.2.0 fuels). Run it once as shipped (Cloudfire fetch)
   to capture a golden input deck, then rebuild the SAME deck from OUR
   fetchers and diff - that diff IS the acceptance test for the
   fetch-side work. (Tutorials 02 transient-wind, 04 fire-potential/MC, and
   05 spotting are the follow-on references for the v2 features.)

## 5. Integration plan

Mirrors the GeoClaw shape exactly (BATCH-PRIMARY: Fortran solver lives only
in the worker image; agent stages inputs + manifest to S3, dispatches via
the generic `run_solver`/`wait_for_completion` seam, postprocesses).

- **`services/workers/elmfire/`** - `Dockerfile`: our own pinned image from
  the 2025.0526 release tarball, modeled on the upstream Dockerfile (Ubuntu
  22/24 slim, gfortran + OpenMPI + gdal-bin, multi-stage, build tools
  stripped; NO Cloudfire/gRPC deps). Unlike GeoClaw there is NO
  compile-at-runtime: binaries are built once at image build (leaner +
  faster cold start). `entrypoint.py`: S3-IN (manifest inputs[] -> rundir)
  -> write `elmfire.data` from the staged build_spec -> run
  `elmfire_<VER>` (mpirun -np N for MC) -> optional `elmfire_post` ->
  upload outputs[] globs + stdout/stderr -> ALWAYS write completion.json
  (exact SFINCS/GeoClaw schema).
- **`workflows/run_elmfire.py`** - deck-spec assembly + staging + solver
  registration (`register_elmfire_solver` -> `SOLVER_WORKFLOW_REGISTRY['elmfire']`,
  plus the pinned in-code registry line). Owns the **input deck builder**:
  fetch fbfm40/cc/ch/cbh/cbd + DEM, derive slp/asp, generate adj/phi,
  build the weather stack from HRRR/gridMET or scenario constants, and
  gdalwarp-align ALL of it onto one EPSG:5070 30 m grid (nearest-neighbor
  for categorical fbfm40; the same-grid requirement is a hard ELMFIRE
  precondition). Typed `ElmfireRunArgs` contract in grace2_contracts.
- **`workflows/postprocess_elmfire.py`** - ToA -> hourly animation frames
  (frames.py) + classified ToA COG; flame length / intensity / spread rate
  COGs; isochrones -> GeoJSON; burn_probability COG (MC); fire-size CSV ->
  typed narration fields. Honesty floor: a "modeled" envelope with empty
  layers never reads status=ok; zero-spread (all-nonburnable AOI) is a
  typed result, not a blank success.
- **`tools/model_fire_spread.py`** - the composer: AOI + ignition +
  scenario weather + duration -> deck builder -> run_solver('elmfire') ->
  wait_for_completion -> postprocess -> LayerURIs. Gates: (a) ignition
  point via the spatial-input/pick machinery (or NIFC perimeter for
  replication runs); (b) the standard user-controlled resolution gate
  (30 m default, coarsen suggestion for big AOIs via the granularity-gate
  pattern); (c) Monte Carlo member count as an explicit cost-gated lever
  (deterministic default). CONUS-only guard with a typed error outside
  LANDFIRE coverage.
- **`fetch_landfire_fuels` extension** - add `cc` + `ch` to
  `_LAYER_SERVICE`/units/styles; optional native-projection passthrough.
- **Execution substrates:** cloud = AWS Batch job def on the existing
  grace2-solvers Spot-first queue (new ECR repo, same IAM pattern as
  GeoClaw; MC ensembles later map onto Batch array jobs, one member per
  task - short idempotent tasks are the Spot-friendly ideal). Offline
  build = the existing `local-docker` LocalSolverSpec lane runs the same
  image via docker run, zero new machinery.

## 6. Effort estimate (jobs) + risks

| Job | Owner | Scope | Size |
|---|---|---|---|
| FIRE-1 container + known-good | engine | Dockerfile + entrypoint + tutorial-01 and verification-01/02 reproduced in-container (evidence: output rasters + ellipse check) | M (2-3 d) |
| FIRE-2 inputs | agent | cc/ch fetcher layers + deck builder (grid alignment, weather stack, elmfire.data writer) + golden-deck diff vs tutorial 03 | M-L (3-4 d) |
| FIRE-3 dispatch + composer | agent | run_elmfire.py + solver registration + model_fire_spread + postprocess + gates + catalog/docstrings | M-L (3-4 d) |
| FIRE-4 infra + deploy | infra | ECR image (pre-push size inspect per hygiene norm) + Batch job def + IAM + deploy; flood-smoke gate after | S-M (1-2 d) |
| FIRE-5 live E2E | testing | historical CA fire replication (NIFC perimeter ignition) + Haiku prod drive; MC burn-probability stretch | M (2 d) |

Total ~2 sprint-weeks single-threaded; FIRE-1 and FIRE-2 parallelize.

**Risks:**
- *Fortran build:* LOW - first-party Dockerfile on Ubuntu 22.04 already does
  it; we pin the release tarball. (GeoClaw was worse: compile-at-runtime.)
- *Same-grid precondition:* MEDIUM - the most likely silent-failure seam
  (mixed extents/SRS); mitigated by a deterministic deck builder that
  asserts identical geotransforms + the golden-deck diff test.
- *LANDFIRE data size/service:* LOW at county scale (2000x2000 Int16 x 10
  rasters is ~80 MB; 30-day cache already in place); the 4096 px clamp caps
  a fetch at ~123 km - tile-and-mosaic only if we later want mega-AOIs.
  ImageServer availability is a single-point upstream (it flaked before:
  LFPS submitJob intercept) - honesty-floor typed errors + cache soften it.
- *Monte Carlo cost on Spot:* MEDIUM - N members x county run; per-member
  runtime is small and tasks are idempotent, so Spot reclaim just re-places
  a member (proven pattern). Cap members via the cost gate; deterministic
  default keeps the everyday path cheap.
- *Weather realism:* v1 scenario-constant weather (canonical for fire-behavior
  what-ifs); transient HRRR stacks and RH-derived dead-fuel moisture are v2
  (fetch_hrrr_forecast needs an RH variable).
- *Coverage:* CONUS-only until LANDFIRE regional dispatch lands; fire
  outside CONUS must fail typed, never hallucinate fuels.
- *20-ft wind convention:* mph at 20 ft, not 10 m m/s - a units trap;
  encode the conversion once in the deck builder with a unit test.

## 7. Alternatives considered

**FlamMap / FARSITE (USFS Missoula Fire Lab):** the institutional standard
for fire-behavior mapping (FARSITE is now a module inside FlamMap 6), and
the same LANDFIRE inputs - but it is distributed as a Windows desktop GUI
application. There is no supported headless Linux CLI, which disqualifies it
for our Linux-container Batch + offline-docker execution model; driving it
would mean Wine or a Windows fleet, both non-starters against the
scale-to-zero architecture. ELMFIRE gives the same input/output ecosystem
(FBFM40, Rothermel surface spread + crown + spotting) in a headless
Linux-native binary.

**Cell2Fire / C2F-W (https://github.com/fire2a/C2F-W):** open-source C++/
Python cell-based (cellular-automata) simulator, actively maintained by the
fire2a group, with a Scott and Burgan fuel-model variant and a QGIS GUI -
the credible runner-up. Reasons to prefer ELMFIRE: level-set front
propagation (continuous perimeters -> clean isochrones/ToA rasters vs
cell-transition artifacts), an operational forecasting pedigree on CONUS
LANDFIRE data (Pyrecast/Cloudfire daily runs), first-party Monte Carlo burn
probability + spotting + suppression, and an input convention that IS the
LANDFIRE stack we already serve. C2F-W's Kitral (Chile) lineage and
research orientation make it a fallback, not the primary. Keep it on the
bench if ELMFIRE's EPL-2.0 or build ever becomes a problem.

## 8. Citations

- ELMFIRE docs (2025.0212): https://elmfire.io/
- Inputs/outputs spec: https://elmfire.io/user_guide/io.html
- Monte Carlo: https://elmfire.io/user_guide/monte_carlo.html
- Tutorial 01 (smoke test): https://elmfire.io/tutorials/tutorial_01.html
- Tutorial 03 (real fuels): https://elmfire.io/tutorials/tutorial_03.html
- Getting started / build: https://elmfire.io/getting_started.html
- Verification: https://elmfire.io/verification.html
- Repo (EPL-2.0, Dockerfile, docker-compose, release 2025.0526 of 2025-05-27; tutorials/ = 01-constant-wind, 02-transient-wind, 03-real-fuels, 04-fire-potential, 05-UMD-spotting): https://github.com/lautenberger/elmfire
- Lautenberger, C. (2013). Wildland fire modeling with an Eulerian level set method and automated calibration. Fire Safety Journal 62:289-298. https://www.sciencedirect.com/science/article/abs/pii/S0379711213001343
- Cell2Fire-W: https://github.com/fire2a/C2F-W ; original: https://github.com/cell2fire/Cell2Fire
- LANDFIRE LF2022 ImageServer substrate: verified live in services/agent/src/grace2_agent/tools/fetch_landfire_fuels.py (2026-06-08) against https://lfps.usgs.gov/arcgis/rest/services/Landfire_LF2022
