# How Real Flood-Modeling Pipelines Work — Reference + GRACE-2 Gap Map

**Authored 2026-06-17** by research workflow `wf_495d379c` (8 agents, primary-source grounded).
Purpose (NATE directive): ground every GRACE-2 flood pipeline in how practitioners actually
build these, so we mirror canonical practice instead of guessing. Consult before building each
engine/demo. Domains covered: Urban pluvial / stormwater 2D flood modeling (rain-on-the-ci, Coastal storm-surge + wave-runup flood inundation modeling (, Riverine / fluvial + compound (rain+river+coast) flood model, Domain D — the shared data + preprocessing + mesh/grid-gener, Flood model postprocessing, animation/visualization, and val.

---

## PART 1 — REFERENCE ARCHITECTURE (canonical practice)

I have all five domain reports. This is a synthesis task — no codebase exploration needed; I'll produce the reference architecture directly from the provided reports.

# GRACE-2 Reference Architecture: Flood-Modeling Pipelines

*A practitioner-grounded synthesis of how real flood modelers build urban-pluvial, coastal-surge, and riverine/compound pipelines — written to replace reactive engine-picking with a generalizable, decision-bearing stage model.*

---

## 0. The One Idea That Generalizes Everything

Real flood modeling is **not one solver** — it is a **nested chain of models with deliberate handoffs**, where the hard work is *data engineering* (terrain → parameters → discretization → forcing) and *honest validation*, not the solve itself. Three facts recur across all five domains and should anchor GRACE-2's design:

1. **Hydraulic engines consume a flow/water-level boundary; they rarely generate it.** HEC-RAS, LISFLOOD-FP, MIKE, and *SFINCS itself* are forced — surge comes from ADCIRC/GTSM, river flow from HEC-HMS/Bulletin-17C/StreamStats, waves from WW3→SnapWave, rain from an Atlas-14 hyetograph. **Skipping the forcing/hydrology stage is the #1 conceptual error in the field.** (SFINCS "is NOT a self-contained surge model" — [sfincs.readthedocs.io](https://sfincs.readthedocs.io/en/latest/overview.html); "the #1 conceptual error" — [FEMA Hydrologic guidance](https://www.fema.gov/sites/default/files/2020-02/Hydrologic_Rainfall_Runoff_Analysis_Feb_2019.pdf).)

2. **"Mesh generation" is two completely different jobs.** For raster/grid engines (SFINCS, LISFLOOD-FP, most HEC-RAS) it is ~90% standard composable GIS (GDAL/QGIS/rasterize) plus one packaged subgrid routine — **do not build a bespoke mesher.** For unstructured engines (Delft3D-FM, ADCIRC, TELEMAC, SCHISM) it is genuinely bespoke, judgment-heavy, and time-dominant ([OCSMesh](https://github.com/noaa-ocs-modeling/OCSMesh); [HydroMT-SFINCS](https://deltares.github.io/hydromt_sfincs/latest/)).

3. **Validation is computed-vs-observed, metric-reported, observation-type-specific — and it is the credibility gate, not decoration.** Gauges → NSE/RMSE/PBIAS/KGE; high-water marks → peak-WSE scatter + RMSE/bias; extent → CSI/hit-rate/FAR against SAR. A "modeled" envelope with empty or unvalidated inundation must never read `status=ok`.

The unifying modern abstraction is the **model-builder**: Deltares' **HydroMT** encodes the entire pipeline as a reproducible YAML recipe over a data catalog ([deltares.github.io/hydromt](https://deltares.github.io/hydromt/stable/)). GRACE-2's agentic `fetch → condition → discretize → force → solve → postprocess → validate → publish` tool chain *is HydroMT, conversationally* — and should mirror its step order and reuse `hydromt_sfincs` directly rather than reinvent it.

---

## 1. The Canonical Stage Pipeline (generalizes across urban / coastal / riverine)

The ordered stages below are the **common spine**. Each flood type instantiates the same stages with type-specific tools and one or two extra stages (coastal adds wave-transformation + runup + JPM-OS return-period aggregation; riverine adds an explicit hydrology→hydraulics handoff; urban adds drainage-network + 1D/2D coupling).

| # | Stage | What happens | The single key decision |
|---|-------|--------------|-------------------------|
| **0** | **Scope, datum & acceptance criteria** | Pick the *question* (design/capacity vs hazard-mapping vs forecast vs screening), the return period(s), the vertical datum (NAVD88 in US — fix it *first*), and what "good enough" means. This decision drives every later choice. | Regulatory return-period vs event-hindcast vs operational-forecast vs rapid-screening — **and is wave-setup/runup/the-pipe-network/the-river-boundary a first-order driver here?** |
| **1** | **Data acquisition & AOI** | Assemble terrain/bathy, land cover, soils, stream network, gauges, footprints, and the design-rainfall/DDF source for the AOI polygon. | Gauged vs ungauged; DTM-vs-DSM; which flooding mechanism(s) govern. |
| **2** | **DEM / topo-bathy conditioning** | Merge a seamless elevation surface (priority-stack with nodata fallback), **harmonize vertical datums before merge**, hydro-condition (fill/breach/burn) where a flow-direction model needs it, and burn/enforce channels + structures LiDAR can't see. | Fill vs breach vs burn; topo-bathy datum harmonization; is channel bathymetry even present (LiDAR doesn't penetrate water). |
| **3** | **Mesh / grid discretization** | Structured grid + subgrid + quadtree (raster engines) **or** flexible unstructured mesh (full-physics engines). Subgrid decouples coarse compute resolution from fine elevation resolution. | Structured-subgrid (composable GIS) vs unstructured-mesh (bespoke); compute-res vs subgrid-res; cell size + convergence test. |
| **4** | **Forcing / boundary conditions** | Attach the drivers: rainfall hyetograph (rain-on-grid), discharge hydrograph (upstream BC), tide+surge water-level (downstream/offshore BC), wave condition, wind+pressure field. **This is where the handoffs live.** | Which drivers; how to combine them (and *joint/coincident* probability for compound — never naive marginal superposition); BC type + placement; hydrograph shaping to avoid numerical shock. |
| **5** | **Solve** | Run the engine; verify Courant/stability + mass-balance/continuity as a *hard QA gate*. | Reduced-physics (local-inertial, seconds–minutes) vs full-SWE (hours–days, HPC); advection on/off; time step / adaptive dt. |
| **6** | **Postprocess** | Collapse time → **max-depth grid** (WSE − fine DEM, clipped to depth>0) + secondary hazard rasters (velocity, D·V, arrival time, duration). Polygonize extent with an area filter. Package as COG. | Depth (h, ground-referenced) vs water-level (zs, datum-referenced) — different legends; WSE-minus-*fine*-DEM; wet/dry threshold. |
| **7** | **Visualize / animate** | Ship **both** the static max-depth still (the regulatory headline) **and** the per-frame depth animation with a scrubber. Fixed color ramp across frames; mask cells < ~0.05 m; datum-stated legend. | Max still vs animation (produce both); fixed vs auto-stretch color range (**fix it**); depth ramp vs datum-referenced water-level ramp vs hazard-class ramp. |
| **8** | **Validate** | Computed-vs-observed against whatever observation exists, with explicit metrics, reported with context. | Which observation (gauge hydrograph / HWM scatter / extent CSI); thresholds; report metric limitations. |

---

## 2. Tool-at-Each-Stage Matrix (open / headless emphasized)

Tools marked **★** are open-source *and* fully headless — the natural fits for GRACE-2's always-on-agent + external-Batch architecture.

| Stage | Universal substrate | Urban pluvial | Coastal surge+wave | Riverine / compound |
|-------|--------------------|--------------|--------------------|---------------------|
| **0 Scope/datum** | NOAA **VDatum** ★ (datum xform); QGIS/ArcGIS | local stormwater manuals; ARR | FEMA Coastal Guidance; CO-OPS tidal datums | FEMA G&S; UK EA standards |
| **1 Data acq.** | **GDAL** ★, **HydroMT data catalog** ★ | NOAA **Atlas-14/PFDS**, NLCD, SSURGO, OSM/MS footprints | **NCEI CUDEM** (1/9″ topobathy), GEBCO, Digital Coast lidar, NDBC | USGS **NWIS/StreamStats**, NHDPlus HR, **MERIT-Hydro**, 3DEP |
| **2 DEM/bathy cond.** | **WhiteboxTools** ★ (fill/breach/burn), GRASS/SAGA via **qgis_process** ★, **RichDEM/TauDEM/PCRaster** ★, **FABDEM** (bare-earth) | hydro-enforce culverts/curbs; building hole/raise/roughness | VDatum-unified topo↔bathy merge; burn dunes/levees | hydro-flatten; burn channel bathymetry (GRWL width + bankfull-depth power law) |
| **3 Mesh/grid** | **gdal_rasterize/resample** ★ | **PCSWMM 2D** mesh (commercial); HEC-RAS RAS Mapper | **HydroMT-SFINCS subgrid+quadtree** ★; SMS/**OceanMesh2D**/**Gmsh**/**OCSMesh** ★ (unstructured) | HEC-RAS breaklines/refinement; **HydroMT-SFINCS** ★ |
| **4 Forcing/BC** | **HydroMT** ★ recipe writers | Atlas-14 → hyetograph (nested/alt-block); SWMM rain gages | **GTSM/GLOSSIS** (surge BC), ADCIRC, CO-OPS gauge; **WW3→SWAN/SnapWave** ★ (waves); Holland spiderweb / ERA5 wind | **HEC-HMS** ★ (design storm→hydrograph→**HEC-DSS**); **CaMa-Flood** ★ + GTSM for compound |
| **5 Solve** | — | **EPA-SWMM5** ★, **HEC-RAS 2D**, TUFLOW, **Iber-SWMM** ★ | **SFINCS+SnapWave** ★, ADCIRC+SWAN ★, Delft3D-FM, XBeach ★, GeoClaw ★ | **HEC-RAS** ★, **LISFLOOD-FP** ★, **SFINCS** ★, TELEMAC-2D ★ |
| **6 Postprocess** | **GDAL** ★ (raster-calc, polygonize, COG), **xarray/xugrid** ★, **rio-cogeo** ★ | RAS Mapper; QGIS raster-calc | **HydroMT-SFINCS** ★ (hmax) | RAS Mapper; numpy `.max(dim='time')` ★ |
| **7 Visualize/animate** | **TiTiler/rio-tiler** ★ (dynamic rescale+colormap), QGIS Temporal Controller + **Crayfish** ★, **matplotlib FuncAnimation→ffmpeg** ★ | RAS Mapper animation toolbar | SFINCS FuncAnimation mp4; per-frame COG + web scrubber | RAS Mapper; per-frame COG scrubber |
| **8 Validate** | **pandas/numpy** ★ metric calcs; **Sentinel-1 SAR** extent | HWM survey, pipe/stream gauges, VGI/CCTV depths | **CO-OPS** time series, **USGS HWM/STN**, SAR | USGS gauge hydrographs, HWMs, SAR; cross-check vs Bulletin-17C curve |

**The roughness tool is the same everywhere:** a pure raster-reclass of land cover (NLCD/WorldCover/CORINE) through a published Manning's-n lookup (Kalyanapu et al. 2009; HEC-RAS manual), folded into subgrid tables. GRACE-2 should ship one swappable `roughness_from_landcover` tool, not per-engine bespoke logic.

---

## 3. Engine-Selection Decision Guide

**Read the question first, then route.** The driving variables are: *(a)* what the user is asking (design/capacity vs depth-around-buildings vs return-period mapping vs forecast vs large-area screening), *(b)* flood type, *(c)* fidelity need, *(d)* whether a first-order driver (wave setup/IG, pipe network, river boundary, morphology) is present.

### Urban pluvial — the four overland options (the crux)

| User's question | Overland representation | Engine | Open + headless? |
|---|---|---|---|
| "Do my pipes surcharge / how much volume escapes the node?" | **1D only** (no surface topology) | **EPA-SWMM5** | ★ Fully open, PD; CLI + **pyswmm/swmm-toolkit** |
| "Where does it pond, on a budget, with the pipe network?" | **Quasi-2D** (storage-node + overland-link grid, dual-linked to pipes) | PCSWMM 2D / InfoWorks ICM | SWMM5 engine open; meshing layer commercial |
| **"What is the depth around buildings / which structures flood?"** | **True-2D SWE coupled to 1D SWMM** (dual drainage) | **HEC-RAS 2D** (+SWMM import), TUFLOW(+1D), **Iber-SWMM** | HEC-RAS free (Windows-centric headless); **Iber-SWMM** free solver |
| "Large-area / forecast / compound coastal-pluvial screening" | **Reduced-complexity rain-on-grid** | **SFINCS**, LISFLOOD-FP | ★ Both open, GPL, fully headless |

> **This directly answers the open GRACE-2 sub-question — "how to render 2D depth around buildings when SWMM5 lacks a 2D solver."** The practitioner answer is **dual drainage**: couple SWMM(1D) to a true-2D engine (HEC-RAS 2D / Iber-SWMM / SFINCS rain-on-grid). **Bare SWMM cannot answer it.** ([PCSWMM dual drainage](https://www.pcswmm.com/application/72316/dual-drainage-system-design); [Iber-SWMM](https://ascelibrary.org/doi/10.1061/%28ASCE%29HY.1943-7900.0000037).)

### Coastal surge + wave

| AOI + need | Engine | When | Open + headless? |
|---|---|---|---|
| Rapid/regional compound (tide+surge+setup+IG+rain+river), many scenarios, 100s of km | **SFINCS + SnapWave** | The reduced-physics default; SnapWave gives XBeach-quality nearshore IG+setup for seconds of compute (R²~0.96 vs 280 XBeach cases) — **must be forced** by external surge+wave BC | ★ Open (Deltares), HydroMT-driven |
| US regulatory return-period BFEs, hurricane coast, wave-surge feedback | **ADCIRC + SWAN** (+ JPM-OS) | Full-physics, HPC, 1–8M+ node meshes, ~100–300 synthetic storms | ★ ADCIRC (registered) + SWAN open; HPC/MPI |
| Need morphology / sediment / salinity / 3D / global flexible mesh | **Delft3D-FM (+D-Waves)** | European/global full-physics; underpins GTSM | Core open; GUI mesh build semi-manual |
| Beach/dune face governs — runup, overwash, overtopping | **XBeach** (Surfbeat / non-hydrostatic) | Per-transect where IG swash dominates (mild slopes/reefs) | ★ Open; expensive in 2D → use SnapWave instead of running everywhere |
| Tsunami / AMR shallow-water inundation | **GeoClaw** | Structured patch-AMR, NOAA-validated | ★ Open (Clawpack) |
| Operational real-time surge watch/warning | **SLOSH / P-Surge** | NOAA ensemble — *use its products, don't re-run* | NWS operational; outputs public |
| Tide+surge **boundary anywhere on Earth** | **GTSM / GLOSSIS** | The offshore BC supplier for SFINCS in data-poor regions | Forcing data source, not re-run |

### Riverine / fluvial / compound

| AOI + need | Engine | When | Open + headless? |
|---|---|---|---|
| Confined channel, FEMA floodway/encroachment, multi-profile WSE | **HEC-RAS 1D steady** | Classic NFIP; one-directional flow, storage not dominant | Free; Windows-centric headless |
| Complex/urban/braided floodplain, levee/dam breach, rain-on-grid watershed | **HEC-RAS 2D / coupled / rain-on-grid** | Merges hydrology+hydraulics (InFRM/Risk MAP) | Free; awkward headless |
| Large-domain / continental / global, cost-sensitive | **LISFLOOD-FP** | Bates-2010 local-inertial + subgrid channels; behind Fathom global maps; **invalid for fast/supercritical** | ★ Open, Linux-native, GPU, batch-ideal |
| Compound (fluvial+pluvial+tidal+surge+wave), many fast runs | **SFINCS** | Reduced-physics workhorse; loose-couple CaMa-Flood (upstream) + GTSM (downstream) + rain-on-grid | ★ Open, GPL, most automation-friendly |
| High-fidelity dam-break / FE / cloud scale-out | **TELEMAC-2D**, Iber | Free FE SWE | ★ Open, Linux-HPC-native |
| **Hydrology that produces the flow BC** | **HEC-HMS** | Design storm → losses → unit hydrograph → routing → DSS for HEC-RAS | Free; Jython/CLI; pair HEC-SSP (Bulletin-17C) |

### General routing heuristics
- **Reduced-physics (local-inertial) is valid only for gradually-varied, subcritical flow.** For dam-break / flash-flood / supercritical / wave-dominated, switch to full-momentum SWE (or SFINCS-SSWE which adds advection). This is a hard physics boundary, not a preference.
- The **Environment Agency / Defra 2D benchmark** (SC120002, Tests 1–8B) is the field's shared correctness gate: full-SWE packages agree closely; simplified/diffusive schemes diverge where momentum and flow direction matter. **Scheme choice is a defensible, testable decision — not a wash.** ([gov.uk benchmark](https://www.gov.uk/government/publications/benchmarking-the-latest-generation-of-2d-hydraulic-flood-modelling-packages)).

---

## 4. Human-in-the-Loop Decision Points (modeler decides vs automatable)

The field's auto/medium/strict "interactivity dial" maps cleanly onto these. **Defaults are automatable; the choices below should be agent-surfaced because they materially change the answer and the field genuinely disagrees.**

### Modeler MUST decide (surface these; biasing them silently is malpractice)

| Decision | Why it's not automatable | Stage |
|---|---|---|
| **The question itself** (design/capacity vs depth-around-buildings vs return-period vs forecast) | Drives the *entire* toolchain — engine, whether a pipe network/river BC/wave step is needed, single-event vs continuous | 0 |
| **Vertical datum** (NAVD88 vs MSL vs MLLW) | Mixing them causes >1 m silent errors; location-dependent | 0/2 |
| **Building representation** (hole/block vs raised ~0.3 m vs high-roughness 0.3–10 vs porosity) | No neutral choice — each gives materially different extent/depth/velocity; **active research frontier, no field consensus** | 2/4 |
| **Hydrology method** (Bulletin-17C statistical vs StreamStats regression vs HEC-HMS rainfall-runoff) | Gauge length + stationarity + land-use change decide; this is the load-bearing riverine branch | 4 |
| **Design-storm construction** (Atlas-14 nested vs SCS Type II vs alternating-block; duration; peak position; single-event vs continuous) | Antecedent moisture & GI performance need continuous; Type II is now archaic | 4 |
| **Compound combination** (joint/coincident probability vs envelope-of-independent vs — never — naive marginal superposition) | Drivers are statistically dependent; superposition mis-states risk | 4 |
| **Reduced vs full physics** | Physics validity boundary (subcritical-only for local-inertial) + cost tradeoff | 5 |
| **Calibration targets & roughness strategy** | Manning's n is the dominant knob with no firm theoretical basis; over-tuning it to mask terrain/BC error is a documented trap | 8 |

### Automatable (with surfaced defaults + an honesty/QA gate)

- DEM merge + priority-stack + nodata fallback; reproject to UTM; datum *transform* (the *choice* of target datum is human; the arithmetic is automated via VDatum).
- Roughness rasterization (land cover → Manning's n lookup).
- Infiltration parameter rasterization (CN/Green-Ampt from soil + land cover).
- Subgrid-table generation; quadtree refinement; grid masking.
- Max-of-timesteps + secondary hazard rasters; extent polygonize + area filter; COG packaging; dynamic tiling.
- Animation rendering (with **fixed** color range + hmin mask).
- Metric computation (NSE/RMSE/PBIAS/KGE/CSI) — once the modeler picks the observation.
- **Mass-balance / continuity + Courant checks** — automated *gates*, not choices (run always; fail loudly).

> **Mesh resolution and infiltration method are budget-aware adaptive parameters**, not free choices: cell size 2–5 m urban (with convergence test), CN-calibrate-volume-before-peaks (InFRM), areal reduction over large basins. These are the canonical instance of GRACE-2's adaptive-mesh budget pattern — SFINCS subgrid is the same lever as the coastal quadtree.

---

## 5. Sub-Optimal-Path Pitfalls ("Shooting in the Dark" Failure Modes)

The cross-domain failure modes, ranked by how often they silently corrupt a "successful-looking" run:

### Tier 1 — Silent corruption (the run looks fine; the answer is wrong)
1. **Skipping the hydrology / forcing stage** — treating HEC-RAS/LISFLOOD/SFINCS as if they compute river flow from rainfall. The forcing must come from a separate hydrology step (or explicit rain-on-grid). *The #1 conceptual error.*
2. **Forgetting SFINCS is a forced engine** — running it without an external GTSM/ADCIRC/gauge surge boundary + offshore wave condition yields a meaningless run.
3. **Vertical-datum mismatch** — subtracting a DEM in one datum from a WSE in another (e.g. NGVD29 vs NAVD88, ~1.7–2.4 ft), or merging chart-datum bathy with orthometric topo without VDatum. Silently corrupts the whole depth grid. *The field's top recurring postprocessing bug.*
4. **Using a DSM as bare-earth terrain** (raw Copernicus GLO-30 / SRTM) — buildings/vegetation counted as topography. Fix: FABDEM or LiDAR DTM.
5. **Naive marginal superposition in compound settings** — rain-1% + river-1% + surge-1% ≠ the 1% compound event. Use joint/coincident probability (NCHRP-2013), time-aligned at the confluence.
6. **LiDAR DEM missing channel bathymetry / sub-surface conveyance** — culverts, bridge openings, curb-and-gutter, channel underwater. Rain-on-grid silently mis-routes unless hydro-enforced.

### Tier 2 — Wrong tool / wrong physics for the question
7. **Bare 1D SWMM for a surface-flooding question** — it has no surface topology; it reports node surcharge/overflow, *not* depth between buildings. Needs a 2D/quasi-2D overland domain.
8. **Reduced-physics on fast/supercritical flow** — LISFLOOD-FP / SFINCS-LIE on dam-break/flash-flood violates the advection-neglecting assumption. Switch to full-momentum SWE / SFINCS-SSWE.
9. **Adding wave setup *after* the surge run** instead of letting radiation stress feed back (FEMA itself moved away from this); ignoring infragravity on mild-slope/reef coasts (IG swash *dominates* runup there).
10. **Running XBeach over a whole region** (intractable) when SnapWave suffices — or using SFINCS where morphology/3D salinity genuinely needs Delft3D-FM.
11. **Mis-representing buildings without acknowledging it changes the answer** — hole/block → larger/deeper/faster; high-roughness → smaller/shallower/slower. No neutral default.

### Tier 3 — QA / numerics / cartography
12. **Ignoring mass-balance/continuity error** — direct-rainfall models with millions of thin-film cells leak volume. A top QA failure. (Pair with the SGS-off wet/dry-depth trap: 0.002 m vs ~0.0002 m without subgrid sampling balloons mass-balance error.)
13. **Numerical shock** — inflow hydrograph whose initial flow doesn't match the model IC, or a rising limb too steep (100→10,000 cfs in a few steps).
14. **Mesh too coarse / no convergence test** — assuming 2–5 m is fine without demonstrating it; coarse cells near channels/curbs under-generate runoff.
15. **Calibrating only roughness while the real error is terrain/bathymetry or boundary timing** — over-tuning Manning's n to an unphysical value to force a HWM match.
16. **Auto-stretching the color ramp per-extent or per-frame** — the legend lies as you pan or scrub. Pin to fixed, physically meaningful breaks. Build the depth grid at *fine DEM* resolution, not coarse model-grid resolution.
17. **Reporting a bare CSI number** — CSI inflates for large/flat floods and penalizes under- vs over-prediction asymmetrically (Stephens & Bates). Report with flood-size/terrain/resolution context. Reach-scale calibrated 2D ≈ CSI 0.7–0.8; global ≈ 0.39.
18. **Non-reproducible ad-hoc preprocessing** — no data catalog, no recipe → silent source/version/datum drift. The HydroMT YAML-recipe pattern exists precisely to prevent this.
19. **Treating an empty/near-empty "modeled" inundation as success** — declaring a flood map with no HWM/gauge/extent sanity check. *This is the operational analog of the validation discipline and reinforces GRACE-2's render-honesty floor.*

---

## 6. Direct Implications for GRACE-2 (condensed)

1. **Encode the pipeline as a decision-bearing graph mirroring Stages 0–8, not a hard-wired engine.** Reuse `hydromt_sfincs` directly; mirror its canonical recipe order (`grid.create_from_region → elevation.create([{merit_hydro, zmin:0.001},{gebco}]) → mask.create_active/boundary → subgrid.create(datasets_rgh=[{lulc, reclass_table}]) → forcing → write`).
2. **Make these first-class agent-surfaced decisions:** overland-representation (4 urban options), building-representation (hole/raise/roughness/porosity), hydrology-method (3 branches), compound-combination (joint-probability, never naive superposition), reduced-vs-full physics.
3. **Close the two structural gaps the research exposes:** (a) a **hydrology / design-storm + handoff stage** (Atlas-14 DDF → hyetograph → losses → unit hydrograph → discharge BC; or Bulletin-17C/StreamStats); (b) a **hydro-conditioning + topobathy-merge seam** (WhiteboxTools / qgis_process fill-breach-burn + VDatum-aware merge). The dropped `setup_river_inflow` and "River-coupled seepage" notes flag exactly this gap.
4. **Treat forcing as first-class data-fetch tools:** `fetch_surge_boundary` (GTSM/GLOSSIS/CO-OPS/ADCIRC), `fetch_offshore_waves` (WW3/NDBC → SnapWave), `fetch_design_storm` (Atlas-14 nested), `fetch_river_discharge` (CaMa-Flood/NWIS) — with the primary→fallback→honest-error norm.
5. **Build validation as a real pipeline stage** — three validator tools: gauge-hydrograph (NSE/RMSE/PBIAS/KGE), HWM-scatter (peak-WSE + 1:1 line + RMSE/bias), extent-CSI (vs Sentinel-1 SAR, binarize 0.15–0.3 m, common-resolution resample). Report *with context*. This *is* the North-Star "computed-vs-observed graph + rasters."
6. **Extend the render-honesty floor to carry validation metrics**, mask cells < 0.05 m, fix the color range across animation frames, and label coastal products "water level (m − NAVD88)" vs overland "flood depth [m]".
7. **Don't build a bespoke mesher** — for the SFINCS/LISFLOOD-FP/HEC-RAS-grid engines GRACE-2 targets, discretization is composable GIS + one packaged subgrid routine. Defer unstructured meshing (OceanMesh2D/Gmsh/OCSMesh) until a Delft3D-FM/TELEMAC/SCHISM case demands it — consistent with the adaptive-mesh budget roadmap.

**Key source anchors:** [HydroMT-SFINCS](https://deltares.github.io/hydromt_sfincs/latest/) · [SFINCS docs](https://sfincs.readthedocs.io/en/latest/) · [SFINCS GitHub](https://github.com/Deltares/SFINCS) · [EA 2D benchmark](https://www.gov.uk/government/publications/benchmarking-the-latest-generation-of-2d-hydraulic-flood-modelling-packages) · [FEMA Hydrologic/Rainfall-Runoff](https://www.fema.gov/sites/default/files/2020-02/Hydrologic_Rainfall_Runoff_Analysis_Feb_2019.pdf) · [InFRM 2D Watershed](https://webapps.usgs.gov/infrm/pubs/211203_HUC8_2D_Watershed_Modeling_Recommendations.pdf) · [NOAA VDatum](https://vdatum.noaa.gov/docs/datums.html) · [NCEI CUDEM](https://www.ncei.noaa.gov/products/coastal-elevation-models) · [FABDEM](https://www.fathom.global/academic-papers/a-30-m-global-map-of-elevation-with-forests-and-buildings-removed/) · [HEC-RAS Mapper dynamic mapping](https://www.hec.usace.army.mil/confluence/rasdocs/r2dum/latest/viewing-2d-or-1d-2d-output-using-hec-ras-mapper/dynamic-mapping) · [SFINCS global validation / CSI](https://nhess.copernicus.org/articles/23/823/2023/) · [Moriasi NSE thresholds](https://swat.tamu.edu/media/1312/moriasimodeleval.pdf) · [Stephens & Bates CSI critique](https://onlinelibrary.wiley.com/doi/10.1002/hyp.9979) · [PCSWMM dual drainage](https://www.pcswmm.com/application/72316/dual-drainage-system-design) · [OCSMesh](https://github.com/noaa-ocs-modeling/OCSMesh).

---

## PART 2 — GRACE-2 GAP MAP (where we match / diverge)

I now have comprehensive grounding across all stages. Producing the gap analysis.

---

# GRACE-2 Flood-Pipeline Gap Analysis vs Reference Architecture

Grounded in the live repo. Headline: GRACE-2 has a **production-grade pluvial rain-on-grid SFINCS pipeline with excellent honesty/QA scaffolding**, but it instantiates only ~5 of the reference architecture's 9 canonical stages. The 4 structurally-missing stages — **(2) DEM/topobathy conditioning, (3) subgrid discretization, (4) forcing/boundary-condition handoffs beyond uniform rain, (8) computed-vs-observed validation** — are exactly the ones the reference report flags as "where the hard work and the credibility live." The forcing fetchers (`fetch_gtsm_tide_surge`, `fetch_noaa_nwm_streamflow`, `fetch_noaa_coops_tides`, `fetch_gcn250_curve_numbers`, `fetch_cama_flood_discharge`) **already exist as data tools but are not wired into the SFINCS deck builder** — they are dead-ends with respect to the engine.

---

## 1. Per-Stage Mapping Table

| # | Reference best-practice stage | What GRACE-2 has today (file:line) | Gap | Canonical workflow to adopt |
|---|---|---|---|---|
| **0** | Scope, datum & acceptance criteria | Implicit. Workflow signature takes `return_period_yr`, `duration_hr`, `compute_class` (`model_flood_scenario.py:604-615`). DEM datum *is* known: `fetch_dem` documents "meters above NAVD88" (`data_fetch.py:383`). | No explicit "what question / which mechanism governs / is this regulatory vs forecast vs screening" routing. No datum-set-first step; NAVD88 is assumed silently. No acceptance-criteria capture. | Add a Stage-0 decision node (agent-surfaced): question type → return period(s) → vertical datum (default NAVD88, **stated**) → "is surge/wave/pipe-network/river-BC a first-order driver here?" This selects the pipeline shape downstream. |
| **1** | Data acquisition & AOI | Strong. `fetch_dem` (3DEP, `data_fetch.py:339`), `fetch_landcover` (NLCD), `fetch_river_geometry` (NHDPlus HR), `lookup_precip_return_period` (Atlas-14), plus ~93 fetchers incl. `fetch_gtsm_tide_surge`, `fetch_noaa_nwm_streamflow`, `fetch_usgs_nwis_gauges`, `fetch_gcn250_curve_numbers`, `fetch_statsgo_soils`, `fetch_noaa_coops_tides`, `fetch_usace_levees/dams/nsi`, `fetch_fema_nfhl_zones`, `fetch_cama_flood_discharge`, `fetch_3dep_extra`. | 3DEP is CONUS-only and land-only — no bathymetry, no FABDEM/Copernicus global fallback (`data_fetch.py:365-367` flags both as "future"). No DSM-vs-DTM guard. No HydroMT data-catalog abstraction (sources are ad-hoc per-tool, not a reproducible recipe). | Keep the fetcher breadth (it's a HydroMT data catalog, conversationally). Add `fetch_dem(source="copernicus"|"fabdem")` global + bare-earth fallback per the data-source-fallback norm. Add a CUDEM/GEBCO topobathy fetcher for coastal. |
| **2** | **DEM / topo-bathy conditioning** | **Absent.** No fill/breach/burn; no datum harmonization for topo+bathy merge; no channel/structure burning. The only DEM handling is `setup_dep` ingest of the raw 3DEP COG (`sfincs_builder.py:1526-1527`) + an adaptive active-mask elevation window (`_compute_active_mask_bounds`, `sfincs_builder.py:845`). `river_geometry_uri` is fetched then **dropped** (`sfincs_builder.py:1553-1568`). | This is reference Tier-1 silent-corruption territory: LiDAR misses channel bathymetry/culverts; raw DSM-as-terrain; no VDatum merge. The dropped river geometry is exactly the "rain-on-grid mis-routes without hydro-enforcement" failure mode. | Add a conditioning seam using **WhiteboxTools / qgis_process** (the `qgis_process` pass-through already exists, `passthroughs.py:194`): fill/breach depressions, burn the NHDPlus channel + USACE levees into the DEM. Add VDatum-aware topo↔bathy merge for coastal (NOAA VDatum). This is "composable GIS," not a bespoke build — exactly what the reference says to reuse. |
| **3** | **Mesh / grid discretization** | Structured grid only: `setup_grid_from_region` with `res` + UTM-default CRS (`sfincs_builder.py:1511-1515`), `setup_mask_active` (`:1541`). Adaptive resolution ladder + cell-cap perf model is excellent (`autoscale_grid_resolution`, `compute_cell_cap`, `estimate_solve_seconds`, `sfincs_builder.py:1103-1409`). | **No `setup_subgrid`.** The reference's single most-emphasized "one packaged routine" — subgrid decouples coarse compute-res from fine elevation-res and is what makes SFINCS credible at speed. Without it the model runs at one resolution; the SGS-off wet/dry mass-balance trap (reference pitfall #12) applies. No convergence test. | Add `setup_subgrid(datasets_dep=[...], datasets_rgh=[...])` to the HydroMT recipe (it's a one-line HydroMT call; `hydromt_sfincs==1.2.2` is already installed). Build the depth grid in postprocess at *fine DEM* resolution, not the coarse grid res (reference pitfall #16). |
| **4** | **Forcing / boundary conditions** | Uniform rain only. `setup_precip_forcing` emits a single constant `magnitude` mm/hr (`sfincs_builder.py:1599-1637`) from Atlas-14 depth÷duration OR an observed-raster **area-mean** (`compute_precip_area_mean_mm_per_hr`, `model_flood_scenario.py:468`). `setup_river_inflow` **deliberately not emitted** (`sfincs_builder.py:1553-1568`, pandas-3 upstream bug + scope). No surge/tide/wave/wind BC. | The reference's #1 and #2 conceptual errors live here: SFINCS is a *forced* engine and "skipping forcing is the #1 error." Uniform-magnitude is a flat hyetograph (no nested/alternating-block shaping → reference pitfall #13 numerical-shock-adjacent). The surge/discharge/tide fetchers exist but feed nothing. Compound combination is impossible. | Make forcing first-class on `ForcingSpec` (`sfincs_builder.py:223`): add `setup_waterlevel_forcing` (GTSM/CO-OPS → downstream BC), `setup_discharge_forcing`/re-enable `setup_river_inflow` (NWM/NWIS/CaMa → upstream BC), `setup_wind`/`setup_pressure` (ERA5/Holland), and a **spw** spatially-varying precip path (the documented OQ-6 upgrade, `sfincs_builder.py:1585-1598`). Shape the hyetograph (Atlas-14 nested) instead of flat magnitude. Surface compound-combination (joint-probability) as a decision. |
| **5** | Solve | Strong. `run_solver(solver, ...)` (`solver.py:8`), AWS Batch per-job autoscale backend (`solver.py:282-325`), local-docker + Cloud Workflows backends, cancellation-first, live solve-progress telemetry (`_drive_live_solve_progress`, `model_flood_scenario.py:186`). Only `solver="sfincs"` registered (`solver.py:10-11`); MODFLOW dispatched directly. | No Courant/CFL or **mass-balance/continuity QA gate** as a hard fail-loud check (reference pitfall #12 — "a top QA failure"). No reduced-vs-full-physics (SSWE/advection) switch — invalid silently for supercritical/dam-break flow (reference pitfall #8). No PySWMM/HEC-RAS-2D engine. | Add a post-solve mass-balance + Courant gate that fails the envelope loudly (reuse the Invariant-7 honesty pattern). Expose advection-on (SFINCS-SSWE) as a decision for fast flow. Register additional solvers behind the same `run_solver` seam. |
| **6** | Postprocess | Strong. `postprocess_flood` → peak-depth COG via `hmax`→`zsmax-zb`→`zs.max(time)-zb` fallback (`_select_peak_depth`, `postprocess_flood.py:469`), <0.05 m masked (`NODATA_DEPTH_M`, `:69`), CRS-verified COG (`_write_verified_cog`, `:340`), per-frame depth COGs for animation (`_extract_depth_frames`, `:552`), max/mean/p95 metrics. | No secondary hazard rasters (velocity, **D·V**, arrival time, duration). No extent polygonize + area-filter (reference Stage-6 + the river→shapefile area-threshold note). Depth is built at model-grid res, not fine-DEM res (pitfall #16). Depth-vs-water-level distinction not carried into the product (peak depth only). | Add velocity/D·V/arrival-time/duration COGs + an extent polygonize-with-area-filter step (`qgis_process` native:polygonize). Compute depth = WSE − *fine* DEM. Tag products depth (ground-ref) vs water-level (datum-ref). |
| **7** | Visualize / animate | Strong, recently shipped. Per-frame COGs + `SequenceScrubber.tsx` + `LayerLegend.tsx` (unit-labeled, min/max, `LayerLegend.tsx:531-537`) + legend snap (`legend_snap.ts`). **Fixed color ramp**: `_resolve_titiler_style_params` pins `continuous_flood_depth → rescale=0,3, colormap ylgnbu` (`publish_layer.py:434`) — matches reference best practice (fix the ramp, don't auto-stretch per-frame, pitfall #16). | Legend has a **unit** but **no datum label** ("water level m − NAVD88" vs "flood depth m" — reference Implication #6). No static-still + animation pairing as an explicit deliverable contract (both produced, but not framed as "regulatory headline + scrubber"). | Add datum-aware legend labels (depth vs water-level). Confirm the fixed `0,3` rescale is applied to *every* frame COG via the same preset (verify the per-frame publish path inherits the preset). |
| **8** | **Validate** | **Absent.** No NSE/RMSE/PBIAS/KGE gauge validator, no HWM peak-WSE scatter, no extent-CSI-vs-SAR. The gauge/tide fetchers exist (`fetch_usgs_nwis_gauges`, `fetch_noaa_coops_tides`) but nothing consumes them as observations. The render-honesty floor (`publish_layer._resolve_titiler_style_params`; empty-layer envelope never reads ok) is the *only* honesty gate. | This is the credibility gate per the reference, and the North-Star "computed-vs-observed graph + rasters" requirement (project memory: SFINCS/PySWMM North Stars both demand it). A "modeled" flood with no observation check is reference pitfall #19. | Build three validator tools: gauge-hydrograph (NSE/RMSE/PBIAS/KGE vs NWIS/CO-OPS), HWM-scatter (peak-WSE 1:1 + RMSE/bias vs USGS STN), extent-CSI (vs Sentinel-1 SAR, binarize 0.15–0.3 m). Report *with context* (flood size / resolution). Fold the metric into the envelope so the honesty floor can read it. |

---

## 2. The 5–10 Highest-Leverage Changes to Stop "Shooting in the Dark"

Ranked by leverage. Each is concrete and code-located.

1. **Wire the forcing fetchers into the SFINCS deck (close the #1 conceptual error).** Today `setup_river_inflow` is dropped (`sfincs_builder.py:1553-1568`) and surge/tide/wave/wind have no path at all, while `fetch_gtsm_tide_surge`, `fetch_noaa_nwm_streamflow`, `fetch_noaa_coops_tides`, `fetch_cama_flood_discharge` produce hydrographs nobody consumes. Extend `ForcingSpec` (`sfincs_builder.py:223-269`) with `waterlevel`/`discharge`/`wind`/`pressure`/`wave` members and add the matching `setup_*_forcing` emissions in `_generate_hydromt_yaml_config` (`sfincs_builder.py:1409`). This single change converts SFINCS from "uniform-rain toy" to "the reduced-physics compound workhorse" the reference describes. (Note the pandas-3 `set_forcing_1d` upstream bug at `sfincs.py:1858` must be patched or pinned — already diagnosed in-code.)

2. **Add `setup_subgrid` to the HydroMT recipe.** The reference's most-emphasized single routine is missing from `_generate_hydromt_yaml_config` (the component list runs `setup_dep → setup_mask_active → setup_manning_roughness → setup_precip_forcing` with no subgrid, `sfincs_builder.py:1526-1637`). `hydromt_sfincs==1.2.2` is installed and ships `setup_subgrid`. This decouples compute-res from elevation-res and closes the SGS-off mass-balance trap (pitfall #12). It also generalizes the adaptive-mesh-budget pattern already built in `autoscale_grid_resolution`.

3. **Add a DEM hydro-conditioning + channel-burn seam via the existing `qgis_process` pass-through.** Right now the river geometry is fetched and discarded. Insert a conditioning step (WhiteboxTools fill/breach + burn NHDPlus channel + USACE levees) before `setup_dep`, invoked through `passthroughs.qgis_process` (`passthroughs.py:194`) — composable GIS, no bespoke build. Closes pitfalls #6 (missing channel conveyance) and feeds Stage 2.

4. **Build the validation stage (3 tools) and fold the metric into the envelope.** No validator exists. Add `validate_flood_gauge` (NSE/RMSE/PBIAS/KGE vs `fetch_usgs_nwis_gauges`/`fetch_noaa_coops_tides`), `validate_flood_hwm` (peak-WSE scatter), `validate_flood_extent` (CSI vs Sentinel-1). Thread the result through `FloodMetrics` so the render-honesty floor (`publish_layer.py`) can refuse `status=ok` on an unvalidated/empty modeled envelope — extending the existing Invariant-7 machinery. This *is* the North-Star "computed-vs-observed" deliverable.

5. **Add a hard mass-balance / Courant QA gate after the solve.** `model_flood_scenario.py` checks `run_result.status != "complete"` (`:1126`) but never inspects continuity error. Add a fail-loud gate in postprocess/workflow that reads SFINCS's mass-balance output and converts a leaking run into a typed failed envelope (reuse `_build_failed_envelope`, `model_flood_scenario.py:299`). Reference calls this "a top QA failure."

6. **Shape the hyetograph instead of emitting a flat magnitude.** `setup_precip_forcing` currently emits one constant `magnitude` mm/hr (`sfincs_builder.py:1610-1637`). Replace with an Atlas-14 nested/alternating-block `timeseries` (the HydroMT signature accepts `timeseries=`, noted at `:1470-1476`). Removes the "flat storm" unrealism and avoids shock on the rising limb.

7. **Surface the genuinely-non-automatable decisions through AskUserQuestion (the interactivity dial).** None of the reference's "modeler-must-decide" choices are surfaced: overland representation (4 urban options), building representation (hole/raise/roughness), hydrology method, compound combination, reduced-vs-full physics. Add these as agent-surfaced multi-select decisions (project memory already wants multiSelect pickers) gated at Stage 0/4. Biasing them silently is the reference's "malpractice" line.

8. **Add datum-aware legend labels + tag depth vs water-level products.** `LayerLegend.tsx:531-537` shows a unit but no datum. Add "flood depth [m]" (ground-ref) vs "water level [m − NAVD88]" (datum-ref) labels driven by a product-kind flag on the layer. Cheap, high-credibility, and directly the coastal North-Star "water-level (m − NAVD88) rainbow key" requirement.

9. **Build the depth grid at fine-DEM resolution in postprocess.** `_select_peak_depth` computes `zsmax − zb` at model-grid resolution (`postprocess_flood.py:479-482`). Reference pitfall #16: re-sample WSE onto the fine DEM before subtracting so the depth product isn't coarse. One change in `_extract_peak_depth_geotiff`.

10. **Encode the pipeline as the explicit Stage 0–8 graph mirroring HydroMT's recipe order.** `model_flood_scenario` is a good straight-line composition but bakes the pluvial-only shape (`model_flood_scenario.py:604`). Refactor toward a stage-graph whose shape is selected by the Stage-0 mechanism decision, so coastal/riverine/urban reuse the same spine with type-specific Stage-3/4 nodes — the reference's "decision-bearing graph, not a hard-wired engine."

---

## 3. North-Star Reference Workflows, Stage by Stage

### Urban — PySWMM quasi-2D (Note: PySWMM is absent from the codebase today — `grep` finds zero `pyswmm`/`swmm` references in the agent.)

The reference is unambiguous on the open GRACE-2 sub-question ("how to render 2D depth around buildings when SWMM5 lacks a 2D solver"): **bare SWMM cannot answer it — you need dual drainage (couple SWMM-1D to a true-2D overland domain).** "Quasi-2D" (storage-node + overland-link grid dual-linked to pipes) is the budget option; for real depth-around-buildings the practitioner answer is HEC-RAS-2D / Iber-SWMM / **or SFINCS rain-on-grid coupled to the SWMM network**. GRACE-2's leverage: the SFINCS rain-on-grid pipeline already exists — couple it to PySWMM rather than build a new 2D solver.

| Stage | Urban PySWMM workflow |
|---|---|
| 0 Scope/datum | Question = "where does it pond / which structures flood around buildings?" → this *requires* a 2D overland domain (not bare 1D). Datum NAVD88. Single-event design storm (or continuous if GI/antecedent-moisture matters). |
| 1 Data | DEM (3DEP `fetch_dem`), building footprints (OSM Overpass per project memory — `fetch_buildings` MS path is broken), NLCD (`fetch_landcover`), soils (`fetch_statsgo_soils`/`fetch_gcn250_curve_numbers`), pipe network (user-supplied or OSM), Atlas-14 (`lookup_precip_return_period`). |
| 2 Conditioning | Hydro-enforce curbs/culverts; building representation decision (hole/block vs raise +0.3 m vs high-roughness) — **agent-surfaced, no neutral default**. Burn the storm-drain inlets. |
| 3 Discretize | 2–5 m overland grid (convergence-tested) for the SFINCS rain-on-grid side; SWMM nodes/links from the pipe network. |
| 4 Forcing | Atlas-14 **nested hyetograph** → SWMM rain gages (1D pipes) AND SFINCS `setup_precip_forcing` (2D overland); CN/Green-Ampt infiltration from soils (currently no `setup_cn`/infiltration in the deck — gap). **Dual-drainage coupling:** SWMM node surcharge ↔ SFINCS overland exchange. |
| 5 Solve | EPA-SWMM5 via PySWMM (headless) for the network; SFINCS for overland (existing AWS Batch backend, `solver.py:282`). Register `solver="pyswmm"` behind `run_solver`. Mass-balance gate on both. |
| 6 Postprocess | Max depth around buildings (WSE − fine DEM), node flood volume, surcharge, secondary D·V. Extent polygonize + small-lake area filter. |
| 7 Visualize | Static max-depth still (headline) + per-frame depth animation (existing `SequenceScrubber` + per-frame COGs). Fixed `0,3` ramp. Depth legend "[m]". |
| 8 Validate | Pipe/stream gauges, HWM survey, VGI/CCTV depths; report metric + context. |

### Coastal — SFINCS + SnapWave (SnapWave is referenced in `solver.py` but not wired.)

The reference: SFINCS is the reduced-physics compound default but **must be forced** by external surge + wave BC; SnapWave gives XBeach-quality nearshore IG+setup for seconds of compute. This is the closest North Star to GRACE-2's current engine — it reuses the entire pluvial pipeline plus surge/wave forcing + topobathy.

| Stage | Coastal SFINCS+SnapWave workflow |
|---|---|
| 0 Scope/datum | Question = coastal compound flood (tide+surge+setup+IG+rain). **Datum harmonization is first-order** (chart-datum bathy vs NAVD88 topo). Is wave setup/IG a first-order driver (mild slope/reef → yes). |
| 1 Data | **Topobathy** (NCEI CUDEM / GEBCO — *gap*: 3DEP is land-only, `data_fetch.py:365-367`), surge BC (`fetch_gtsm_tide_surge` — exists), tide (`fetch_noaa_coops_tides` — exists), waves (WW3/NDBC → SnapWave — *gap*: no wave fetcher), SLR (`fetch_noaa_slr_scenarios` — exists), wind/pressure (`fetch_era5_reanalysis` — exists). |
| 2 Conditioning | **VDatum-aware topo↔bathy merge** (NOAA VDatum) — the reference's top recurring postprocessing bug if skipped. Burn dunes/levees (`fetch_usace_levees`). |
| 3 Discretize | SFINCS subgrid + **quadtree** refinement near the coast (`setup_subgrid` + quadtree — *gap*: neither emitted). The adaptive-cell-cap perf model (`autoscale_grid_resolution`) already generalizes here. |
| 4 Forcing | `setup_waterlevel_forcing` (GTSM/CO-OPS offshore BC — *gap*), `setup_wind`/`setup_pressure` (ERA5/Holland spiderweb — *gap*), SnapWave coupling for incident+IG waves → wave forces / 2m-contour run-up (*gap*: SnapWave not wired in `solver.py`), `setup_precip_forcing` rain (exists). **Compound combination = joint probability, never naive superposition** (agent-surfaced decision). |
| 5 Solve | SFINCS+SnapWave on AWS Batch (existing backend). Mass-balance + Courant gate. |
| 6 Postprocess | **Water-level (zs, datum-ref)** AND depth (h, ground-ref) — different legends. Run-up line, wave-force raster, arrival time. |
| 7 Visualize | "water level (m − NAVD88)" rainbow key (project memory: the exact Mexico-Beach North-Star requirement) + computed-vs-observed graph. Per-frame water-level animation. |
| 8 Validate | CO-OPS time series (NSE/RMSE), USGS HWM/STN scatter, Sentinel-1 SAR extent CSI — the "computed-vs-observed graph + rasters" deliverable. |

---

## 4. What GRACE-2 Already Does Well (keep it)

1. **The honesty floor is genuinely best-in-class and matches reference Implications #5/#6 and pitfall #19.** The NLCD-vintage Manning's validation gate (`validate_nlcd_vintage_against_mapping`, `sfincs_builder.py:409-475`) converts HydroMT's silent-default-fill into a typed `LULC_MAPPING_MISMATCH` failed envelope — this is exactly the reference's "no silent wrong answers." Empty/failed modeled envelopes never read `status=ok`; `_build_failed_envelope` threads the error code (`model_flood_scenario.py:299-367`).

2. **It already uses HydroMT-SFINCS directly** (`hydromt_sfincs==1.2.2` installed; `build_sfincs_model` wraps `SfincsModel` with a programmatically-generated YAML recipe, `sfincs_builder.py:1409`). This is precisely the reference's #1 implication ("reuse `hydromt_sfincs`, mirror its recipe order, don't reinvent"). The recipe just needs *more stages*, not a rewrite.

3. **Roughness is already the one swappable land-cover reclass the reference prescribes.** `manning_mapping.csv` (version-pinned NLCD→Manning's with proper citations: Chow 1959, USGS WSP 2339, Liu & DeGroote 2010) + `setup_manning_roughness` (`sfincs_builder.py:1547-1551`). This is the "one swappable `roughness_from_landcover` tool, not per-engine bespoke logic" recommendation, done.

4. **The fixed color ramp is correct** — `continuous_flood_depth → rescale=0,3` (`publish_layer.py:434`), and sub-0.05 m masking (`NODATA_DEPTH_M`, `postprocess_flood.py:69`). This is reference pitfall #16 avoided by construction (no per-frame auto-stretch).

5. **CRS/orientation rigor in postprocess.** `_write_verified_cog` round-trips and asserts the CRS tag + geographic/projected coordinate-magnitude consistency before upload (`postprocess_flood.py:340,444-454`), and orientation guards (`_orient_array_for_cog`, `:265`). This closes the silent-mistag bug class.

6. **The adaptive-mesh budget pattern is already built** — `autoscale_grid_resolution` + `compute_cell_cap` + `estimate_solve_seconds` + a resolution ladder (`sfincs_builder.py:1103-1409`). The reference calls mesh-resolution a "budget-aware adaptive parameter, not a free choice"; GRACE-2 has the perf-model machinery to generalize this to subgrid + coastal quadtree + future unstructured meshes.

7. **Solve infrastructure matches the architecture** — scale-to-zero AWS Batch per-job autoscale, cancellation-first, live solve telemetry (`solver.py:282-325`; `_drive_live_solve_progress`, `model_flood_scenario.py:186`). The `run_solver(solver, ...)` seam (`solver.py:8`) is the right place to register PySWMM/HEC-RAS-2D behind one interface.

8. **The QGIS Processing pass-through already exists** (`passthroughs.qgis_process`, `passthroughs.py:194` + `list_qgis_algorithms`/`describe_qgis_algorithm`, `qgis_discovery.py`). This is the ready-made substrate for the missing Stage-2 conditioning (fill/breach/burn) and Stage-6 polygonize — "composable GIS, don't build a bespoke mesher," already plumbed.

9. **Time-series animation substrate is shipped and engine-agnostic** — per-frame depth COGs with even subsampling (`_extract_depth_frames`/`_select_frame_time_indices`, `postprocess_flood.py:493-570`) + `SequenceScrubber.tsx` + `LayerLegend.tsx` + legend-snap. Both North Stars need this; it's done and reusable.

---

### One-line bottom line
GRACE-2 is a **correctly-architected, honesty-first, single-stage-deep pluvial SFINCS pipeline**; the work to reach the reference architecture is **adding the four missing middle stages (conditioning, subgrid, real forcing/BC handoffs, validation) onto the HydroMT recipe and `run_solver` seam it already has** — wiring up forcing fetchers that already exist, not green-fielding an engine.

Key files: `services/agent/src/grace2_agent/workflows/sfincs_builder.py` (recipe — add subgrid + forcing), `workflows/model_flood_scenario.py` (stage graph), `workflows/postprocess_flood.py` (secondary hazards + fine-res depth), `tools/solver.py` (register engines), `tools/passthroughs.py` (conditioning via qgis_process), `tools/publish_layer.py` (fixed ramp — keep; add datum tags), `web/src/components/LayerLegend.tsx` (datum labels). No validation tools exist yet — net-new.

---

## PART 3 — COMPLETENESS CRITIQUE (low-confidence / follow-ups)

Confirmed: zero validation metric machinery, single datum mention (NAVD88 assumed, no VDatum/MLLW), bathy/global DEM both flagged "future." The gap analysis's mappings are accurate, not hand-wavy. Now I have enough grounding to deliver the completeness critique.

# Completeness Critique: GRACE-2 Flood-Pipeline Reference + Gap Analysis

I spot-verified the gap analysis against the live repo. Its file-line mappings hold up: no `setup_subgrid`, `setup_river_inflow` deliberately suppressed, only `solver="sfincs"` registered, zero `pyswmm`/`snapwave` references, no NSE/KGE/CSI machinery, single `NAVD88` mention with no VDatum/MLLW path, bathy + global DEM both literally commented "future." **The GRACE-2 mapping is not hand-wavy — it is the strongest part of the synthesis.** The weaknesses are almost entirely in the *reference architecture*: domain coverage gaps, contested-as-settled claims, and citation-to-claim mismatches. Below, prioritized.

---

## A. Domains / engines the reference under-covers or omits (HIGH priority)

1. **Snowmelt / rain-on-snow forcing is entirely absent.** For Idaho (the explicitly-planned Demo Case 3), the Pacific NW, and much of the mountain-West riverine domain, the design event is frequently rain-on-snow, not Atlas-14 rainfall. Atlas-14 DDF → hyetograph silently assumes the forcing is liquid precip. This is a missing *forcing mechanism*, not a missing engine, and it directly undermines a committed demo. **Follow-up: is SNODAS/SNOTEL → energy-balance or temperature-index melt a required Stage-4 branch, and which engines/tools cover it headless?**

2. **Continuous vs event simulation is mentioned but never operationalized.** The reference repeatedly says "continuous if antecedent moisture / GI matters" but gives no engine guidance, no warm-up/spin-up stage, and no antecedent-soil-moisture data source. SFINCS and the urban dual-drainage path are framed single-event throughout. The Stage pipeline has no spin-up/initial-condition stage at all — yet IC error is its own pitfall (#13). **Follow-up: what is the canonical continuous-simulation workflow (warm-up period, AMC sourcing, long-term-balance engines like HEC-HMS continuous / SWMM continuous), and does it need a new Stage?**

3. **Levee/dam-breach and infrastructure-failure flooding is named only as a HEC-RAS feature, never as a domain with its own forcing/decisions.** Breach timing, breach-width growth, and fragility are first-order, highly contested modeling choices (the reference's own memory notes USACE NLD/NID). This is arguably a fourth flood *type* alongside urban/coastal/riverine. **Follow-up: is breach/failure flooding a distinct pipeline shape, and what's the canonical breach-parameterization stage?**

4. **Ice-jam, debris-flow / post-wildfire (pfdf), and tsunami are mentioned in passing or only via GeoClaw, with no pipeline.** Post-wildfire debris flow (USGS pfdf is in the endpoint-inventory memory) is a real GRACE-2 ambition (fire engines roadmap) and has a *completely different* forcing and hazard model. The reference treats GeoClaw/tsunami as a one-liner. **Follow-up (medium): do compound fire→flood and debris-flow warrant their own stage-graph, or are they out of v0.1 scope by decision?**

5. **Groundwater / seepage coupling is absent from the reference entirely**, despite MODFLOW being a live, wired GRACE-2 engine (the gap analysis notes MODFLOW is dispatched directly, bypassing `run_solver`) and despite the project memory's explicit "river-coupled seepage" North Star. The reference's "compound" treatment is surface-water-only. **Follow-up (medium): how do surface-water and groundwater pipelines hand off (RIV/SFR/DRN coupling), and where does MODFLOW sit in the stage spine?**

---

## B. Claims asserted as settled that are actually contested in the field (HIGH — flag as low-confidence)

These should be re-tagged in the synthesis from "best practice" to "defensible-but-contested":

6. **"SFINCS-SSWE adds advection → use it for supercritical/dam-break."** Treat as LOW-CONFIDENCE. SFINCS's stated design space is gradually-varied subcritical compound flooding; whether its simplified-SWE momentum mode is genuinely validated for true dam-break/supercritical regimes (vs. just "better than LIE") is not established by the cited overview page, and the EA benchmark (cited for scheme divergence) does *not* test SFINCS-SSWE. The reference presents this as a clean switch; it is closer to a research claim. **Follow-up: primary-source the validated envelope of SFINCS-SSWE for supercritical flow.**

7. **"SnapWave gives XBeach-quality nearshore IG+setup … R²~0.96 vs 280 XBeach cases."** Treat as LOW-CONFIDENCE as stated. This specific number is uncited in the architecture and the surrounding source anchors. A single aggregate R² across a calibration set is not "XBeach-quality" across all morphologies (reefs vs dissipative beaches vs barred profiles differ sharply), and SnapWave is phase-averaged — it cannot reproduce individual-wave overtopping XBeach-nonhydrostatic captures. **Follow-up: locate the primary SnapWave validation paper and bound where it does/doesn't match XBeach.**

8. **"FEMA moved away from adding wave setup after the surge run."** Treat as LOW-CONFIDENCE. The reference asserts a coupled radiation-stress feedback as current FEMA practice, but FEMA coastal guidance is region-split (Pacific vs Atlantic/Gulf vs Great Lakes) and the older WHAFIS / runup-added-post-hoc methods remain in active regulatory use in places. The blanket "FEMA itself moved away" overstates a heterogeneous picture. **Follow-up: cite the specific FEMA guidance version and region that mandates coupled setup.**

9. **CSI benchmark numbers ("reach-scale 0.7–0.8; global 0.39").** The 0.39 is sourced (NHESS 2023) but "0.7–0.8 for calibrated reach-scale 2D" is presented bare; this is highly dependent on flood magnitude, terrain flatness, and the very binarization-threshold the reference elsewhere says inflates CSI. Internally inconsistent to cite CSI ranges as targets two paragraphs after citing Stephens & Bates on why CSI ranges aren't comparable. **Flag: don't use these as acceptance thresholds.**

10. **"Type II is now archaic."** Overstated. SCS Type II is still the default in large swaths of US state/local stormwater regulation and NRCS TR-55. "Archaic per the research frontier" ≠ "not what the regulator requires" — and Stage 0 says the *regulatory* requirement governs. The reference contradicts its own Stage-0 primacy here. **Flag as opinion, not consensus.**

---

## C. Stages where the "canonical workflow" is genuinely contested (MEDIUM)

11. **Building representation** — the reference *correctly* flags this as no-consensus / active frontier, then the gap analysis lists it as an "agent-surfaced decision." Good. But neither states **what the safe default is when the user won't choose**, nor whether porosity/holes interact with the *subgrid* method (they do — subgrid + building-holes is its own unresolved interaction). **Follow-up: subgrid × building-representation interaction; safe default + uncertainty band.**

12. **Compound joint-probability method is named but not chosen.** "Joint/coincident probability (NCHRP-2013), never naive superposition" is correct as a *prohibition*, but the *positive* method is itself contested: full JPM-OS vs copula vs response-surface vs scenario-of-record vs the simpler "dependent-but-conditioned" Defra/EA approach. The reference collapses a live methodological debate into one citation. **Follow-up: enumerate the 3-4 competing compound-probability methods and their data/compute cost, so the agent can route.**

13. **Mesh convergence test is prescribed but undefined.** "2–5 m urban with convergence test" — there is no field-standard convergence protocol for 2D flood depth (unlike CFD). Citing "do a convergence test" without a method is hand-wavy in the *reference*, not just GRACE-2. **Follow-up: is there any published convergence-test protocol for 2D inundation, or is this practitioner-judgment?**

14. **Hydrology branch (Bulletin-17C vs StreamStats vs HEC-HMS) is presented as a clean 3-way choice** but real practice often *chains* them (regression for ungauged + 17C at the gauge + HMS for the design storm) and the choice of which to trust where is the actual skill. The decision table flattens this.

---

## D. Mappings/recommendations in the synthesis that are thin or over-confident (MEDIUM)

15. **"setup_subgrid is a one-line HydroMT call."** Over-confident. Subgrid generation needs `datasets_dep` AND `datasets_rgh` with correct datum/CRS alignment and is memory/time-heavy on large AOIs; calling it "one-line" undersells the integration with the existing adaptive-cell-cap perf model (subgrid changes the cost curve the perf model assumes). The recommendation to add it is right; the effort estimate is hand-wavy. **Verify against `hydromt_sfincs==1.2.2` actual signature + memory profile before scoping.**

16. **"Couple SFINCS rain-on-grid to PySWMM" as the depth-around-buildings answer is asserted but not demonstrated to exist as a standard coupling.** The reference's *own* dual-drainage citations are PCSWMM (commercial) and Iber-SWMM — neither is SFINCS↔SWMM. A SFINCS-overland ↔ SWMM-network two-way coupling is not a recognized off-the-shelf pattern (SFINCS has no native SWMM inlet exchange). The gap analysis presents "couple the existing SFINCS pipeline to PySWMM" as low-effort leverage; it may be net-new research, not glue. **Flag HIGH: this is the answer to the headline open sub-question and it rests on an unestablished coupling. Follow-up: does any published SFINCS↔SWMM (or SFINCS-as-2D-for-SWMM-spill) coupling exist, or is HEC-RAS-2D/Iber-SWMM the only proven dual-drainage route?**

17. **Mass-balance "hard fail-loud gate" assumes SFINCS emits a continuity-error diagnostic the workflow can read.** The recommendation (#5 in the gap analysis) doesn't verify SFINCS actually writes a mass-balance number to a parseable output, or what threshold = "leaking." Asserted as reusable Invariant-7 machinery without confirming the input exists. **Verify SFINCS's actual mass-balance output artifact + a defensible threshold.**

18. **Fine-DEM-resolution depth grid recommendation (#9) ignores the cost it reintroduces.** The whole point of subgrid is to *not* compute at fine res; producing the depth product at fine-DEM res means resampling a coarse WSE onto a fine grid, which can manufacture artifacts at the wet/dry edge (the very SGS sampling problem). Right intent, but the reference states it as unambiguously correct when it's a tradeoff. **Flag: depth-downscaling method needs specification, not just "use fine DEM."**

19. **"HEC-RAS free; Windows-centric headless / awkward headless"** is repeated as if HEC-RAS is a viable Batch engine. For GRACE-2's Linux-AWS-Batch architecture this is closer to "effectively unavailable headless" — the reference soft-pedals a hard infra incompatibility that matters enormously for the urban depth-around-buildings route (HEC-RAS-2D is the *recommended* engine there). **Flag: HEC-RAS-2D-on-Linux-Batch feasibility is a blocker for the recommended urban path and should be its own go/no-go.**

---

## E. Citation-quality flags (verify before trusting)

20. Several pivotal quotes are attributed to plausible-but-unverified URLs: the **"#1 conceptual error"** phrasing attributed to a FEMA rainfall-runoff PDF, and **"SFINCS is NOT a self-contained surge model"** attributed to the readthedocs overview. These are the synthesis's two most-repeated anchor claims. Both should be quote-verified against the actual source text — paraphrase-as-direct-quote is the likeliest error mode in a multi-report synthesis. **Follow-up: confirm both verbatim.**

21. **VDatum "1.7–2.4 ft NGVD29↔NAVD88" and "chart-datum bathy"** numbers are location-specific (the offset is negative in some regions, ~0 in others) but presented as a general magnitude. Fine as illustration, mislead­ing as a constant. **Flag minor.**

22. **No source given for "Kalyanapu et al. 2009" Manning's-n table being the field standard** vs. the many competing LULC→n tables (the repo uses Chow 1959 / USGS WSP 2339 / Liu & DeGroote — *not* Kalyanapu). The reference prescribes one table the codebase doesn't use and doesn't reconcile. **Flag: reconcile prescribed vs in-use roughness table.**

---

## Prioritized follow-up research questions (run next)

**P0 — blocks a committed demo or the headline open question:**
1. Does a proven **SFINCS↔SWMM (or SFINCS-as-2D-overland-for-SWMM)** coupling exist, or is HEC-RAS-2D / Iber-SWMM the only validated dual-drainage route? (item 16) — *this is the depth-around-buildings answer the whole effort hinges on.*
2. **Snow / rain-on-snow** Stage-4 forcing for the Idaho demo + mountain-West riverine — required branch and headless tooling. (item 1)
3. **HEC-RAS-2D on Linux/AWS-Batch** feasibility — go/no-go for the recommended urban engine. (item 19)

**P1 — corrects contested-as-settled claims (re-tag as low-confidence now):**
4. Verbatim-confirm the two anchor quotes (FEMA "#1 error", SFINCS "not self-contained"). (item 20)
5. SnapWave validation envelope + the "R²~0.96 / XBeach-quality" claim's provenance and limits. (item 7)
6. SFINCS-SSWE validated supercritical/dam-break envelope. (item 6)
7. Competing **compound joint-probability methods** (JPM-OS / copula / response-surface / EA-dependent) with data + compute cost, so the agent can route. (item 12)
8. FEMA coupled-wave-setup current practice by region — qualify the blanket claim. (item 8)

**P2 — fills structural domain gaps:**
9. Continuous-simulation workflow + spin-up/IC stage + antecedent-moisture sourcing. (item 2)
10. Levee/dam-breach as a distinct pipeline shape + breach-parameterization stage. (item 3)
11. Surface↔groundwater (MODFLOW RIV/SFR) coupling and where MODFLOW sits in the spine. (item 5)
12. SFINCS mass-balance output artifact + defensible continuity-error threshold. (item 17)

**P3 — methodological precision:**
13. Published 2D-inundation convergence-test protocol (or confirm it's judgment). (item 13)
14. Subgrid × building-representation interaction + safe default. (item 11)
15. Depth-downscaling-to-fine-DEM method without manufacturing wet/dry-edge artifacts. (item 18)
16. Reconcile prescribed (Kalyanapu) vs in-use (Chow/WSP-2339/Liu) roughness tables. (item 22)

---

## Low-confidence items to flag in the synthesis (summary)
- SFINCS-SSWE for supercritical flow (B6) · SnapWave "XBeach-quality / R²~0.96" (B7) · "FEMA moved away from post-hoc setup" (B8) · CSI 0.7–0.8 target (B9) · "Type II archaic" vs regulatory primacy (B10) · "setup_subgrid is one-line" (D15) · "couple SFINCS↔PySWMM is glue" (D16) · "build depth at fine-DEM res, unambiguously" (D18) · HEC-RAS headless viability (D19) · the two paraphrased anchor quotes (E20).

## What is solid (don't re-research)
The nested-chain / "engines are forced, not generators" thesis; the 0–8 stage spine; the structured-vs-unstructured mesh split; "don't build a bespoke mesher"; the honesty-floor / validation-as-credibility-gate framing; **and the entire GRACE-2 per-stage mapping** — every file-line claim I spot-checked (`setup_subgrid` absent, `setup_river_inflow` suppressed, single solver registered, no validation tools, NAVD88-assumed, bathy/global-DEM "future") verified against the repo.
