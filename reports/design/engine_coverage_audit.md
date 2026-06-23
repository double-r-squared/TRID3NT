# GRACE-2 Engine Coverage Audit -- Cross-Engine Synthesis

Date: 2026-06-23
Scope: SWAN, SFINCS, PySWMM/SWMM5, MODFLOW 6 (GWF+GWT), GeoClaw, Landlab, OpenQuake
Method: Read each engine's agent tool, workflow, Batch worker, and contracts; compared the engine's documented capability surface against what GRACE-2 actually exposes to the LLM agent. Spot-verified the load-bearing claims against the live code (mesh_cells hardcode, FloodMetrics.max_velocity_m_s contract field, MODFLOW N_LAYERS/domain constants, GeoClaw topo handoff, SWMM DYNWAVE/INVERT_DEPTH-only, Landlab FlowDirectorD8/np.full broadcast, OpenQuake classical-only job.ini). Read-only.

## Executive summary

GRACE-2 has seven numerical engines wired through one consistent, production-grade pattern: agent tool -> scenario/build-spec composer -> Batch (Spot, scale-to-zero) worker -> rasterize/postprocess -> COG -> publish, with honesty gates (mass-balance, all-dry, no-coverage, convergence-from-log), adaptive-mesh budgets, the #154 granularity gate, and first-class cancellation. That plumbing is uniformly excellent and is NOT where the gaps are.

The gap is **capability surface**: every engine exposes one thin, demo-tuned vertical slice of what it can do. Against NATE's full-coverage goal, estimated coverage of the documented capability surface:

| Engine | Est. coverage | Strongest dimension | Weakest dimension(s) |
|---|---|---|---|
| SFINCS | ~55-60% | grids/mesh (full), run-modes (full) | physics-toggles, output (no velocity/arrival/obs) |
| GeoClaw | ~30% | run-modes (3 families) | output (no fgmax/gauges), grids (broken topo handoff) |
| SWAN | ~30-35% | run-modes (full), spectral grid (full) | physics (3 fixed toggles), output (Hs-only) |
| PySWMM/SWMM5 | ~15-20% | run-modes (2 exec lanes) | the entire node-link network, output (depth-only) |
| MODFLOW 6 | ~12-18% | run-modes (steady+transient) | forcing (2 BCs only), physics (conservative tracer) |
| Landlab | ~12-15% | -- (2 of ~50 components) | components, grids, output (3 scalars) |
| OpenQuake | ~10-15% | -- (classical PSHA only) | everything (1 mode, 1 source, no site model) |

**Most under-covered (highest capability left on the table): OpenQuake, Landlab, MODFLOW 6, SWMM.** These four expose roughly an eighth of their engine. SFINCS is the most complete and should be the reference template for what "good coverage" looks like; GeoClaw and SWAN sit in the middle with solid run-mode breadth but thin output/physics.

The single most important finding is not a coverage gap at all: **GeoClaw's DEM topotype handoff is a correctness blocker** -- a COG `.tif` is staged as `topo.asc` and declared topotype-3 (ESRI ASCII), but the worker entrypoint does no conversion (verified: the staging loop is a bare `_download(input_uri, dest)`; the claimed conversion in the docstring at lines 20-23 does not exist in code). Any non-synthetic GeoClaw run likely reads garbage or fails. Fix this first.

## Coverage matrix (per dimension, all engines)

Legend: full / partial / minimal / absent / BROKEN

| Dimension | SFINCS | GeoClaw | SWAN | SWMM | MODFLOW6 | Landlab | OpenQuake |
|---|---|---|---|---|---|---|---|
| forcing/inputs | partial | partial | partial | minimal | minimal | partial | minimal |
| grids/mesh | full | minimal (BROKEN topo) | partial | minimal | minimal | minimal | minimal |
| physics/algorithms | partial | partial | minimal | minimal | minimal | minimal | minimal |
| run-modes | full | partial | full | partial | partial | minimal | minimal |
| boundary-conditions | partial | minimal | minimal | minimal | minimal | minimal | minimal |
| output-quantities | partial | minimal | minimal | minimal | minimal | minimal | minimal |

The matrix reads top-to-bottom as a maturity gradient: run-modes and grids are where the team has invested (the autoscale/Batch pattern), while **boundary-conditions and output-quantities are the universally weakest columns** -- every engine is minimal or partial on both. That is the cross-engine signal: GRACE-2 systematically under-reads what its solvers compute and under-exposes how the domain is bounded.

## Cross-engine patterns worth building once

These are the abstractions that recur across three or more engines. Building each once and retrofitting amortizes the cost of closing dozens of individual gaps.

### 1. Generic output-quantity publisher (highest leverage)
Every engine computes far more than it publishes, and the postprocess COG/animation plumbing is near-identical across all seven:
- SWAN requests only HSIGN/RTP/DIR and rasterizes Hs only (Tp/Dir are narration scalars; DSPR/SETUP/radiation-stress absent).
- SFINCS leaves `max_velocity_m_s` None despite the contract field existing (verified envelope.py:165) and SFINCS writing vmax natively; arrival-time and his/obs points unsurfaced.
- SWMM reads only node INVERT_DEPTH; FLOODING_LOSSES/PONDED_VOLUME/conduit flow/velocity all discarded though the Output API is already open.
- MODFLOW reads only the FINAL transport timestep (no animation) and never surfaces head/water-table from the already-saved .hds.
- GeoClaw reads only q[0]=h and discards hu/hv/eta; no fgmax/gauges (the canonical tsunami products).
- Landlab writes one field and discards drainage_area/slope/relative_wetness/discharge it already computes.
- OpenQuake requests only the hazard MAP though the classical run already computes curves/UHS.

Build once: a declarative "output quantity" registry per engine (quantity -> reader -> COG/timeseries/scalar emitter) so adding a published field is a one-line registration, not a bespoke postprocess. This single abstraction unblocks ~12 of the ranked gaps below.

### 2. Time-series / animation toggle (steady-vs-transient + frame emission)
The repo already has the scrubber/animation machinery (SFINCS depth frames, SWMM 24-frame depth COGs, SWAN frames). But three engines compute time evolution and throw it away: MODFLOW saves n_transport_steps but reads only the last; Landlab OverlandFlow time-loops internally but emits peak only; GeoClaw writes fort.q frames but the headline use is static peak. Build once: a shared "emit-as-timeseries" capability that any engine's output-publisher can opt into, feeding the existing scrubber. Pairs directly with the prioritized time-series-animation North Star.

### 3. Unified forcing abstraction (data-fetcher -> engine-forcing writer)
Forcing is the most fragmented dimension and has the most dead/half-wired seams:
- SWAN wind: `wind_uri` param + staging + deck WIND block all exist, but no fetcher populates it and the worker never reformats wind.dat -- a non-functional half-seam.
- SFINCS quadtree path silently drops wind/pressure (the regular-grid path emits both -- pure parity work).
- MODFLOW `river_dem_uri` is in the contract and the composer fetches a DEM but never samples per-cell rbot/stage into the adapter.
- GeoClaw `surge_forcing_uri` is staged then discarded (surge degrades to a static sea_level offset).

The pattern: a forcing seam is declared in the contract, half-plumbed, then abandoned before the data actually reaches the solver. Build once: a forcing-source registry (fetcher -> normalized field -> engine-specific writer) with a CI/test assertion that every contract forcing URI actually reaches the deck. This both closes the half-seams and prevents new ones (the same several engines share ERA5/GTSM/CO-OPS/ATCF/MRMS fetchers already).

### 4. Adaptive grid/mesh budget -> resolution lever (generalize the SFINCS pattern)
SFINCS has the mature version: an autoscale ladder (30/50/100/200m) + cell-cap + #154 granularity gate, with resolution as a genuine user lever. The other engines have it inconsistently or not at all:
- SWAN mesh is hardcoded (100,100), never wired to bbox/compute_class even though select_compute_class is already called for vCPU sizing (verified run_swan.py:228,374).
- GeoClaw base_num_cells hardcoded (40,40), only a telemetry proxy (verified).
- MODFLOW grid fixed 40x40 at 50m over a 2km domain (verified gwt_adapter.py:57-59).
- OpenQuake docstrings PROMISE adaptive site-grid coarsening but no such code exists (assemble_build_spec passes spacing straight through -- a latent OOM).

SWMM and SFINCS already share the ladder/cap idiom. Build once: a shared `AdaptiveResolution` helper (bbox-area + compute-class -> resolution, with cell-cap guard and #154 gate) and retrofit SWAN/GeoClaw/MODFLOW/OpenQuake onto it. This is the highest-consistency, lowest-risk generalization because two engines already prove the shape.

### 5. Boundary-condition / structure abstraction (the universally weakest column)
Every engine pins boundaries to one demo configuration: GeoClaw all-extrap; SWAN single-side constant PAR; SWMM single FREE outfall; MODFLOW CHD+optional-RIV only; Landlab default open edges (with a doc/code mismatch -- _run_overland_flow claims to set a watershed outlet but does not). The hydraulic engines (SFINCS weirs, SWMM tidal/timeseries outfalls + pumps/weirs, GeoClaw forced boundaries) share a recurring need: tailwater/stage boundaries and 1D line-structures (levees/seawalls/walls). The compound-flood North Star (SFINCS surge + SWMM pluvial coupled at a tidal outfall) is blocked specifically by this column. Build once: a shared "structure/boundary feature" snapping mechanism (the SWMM flap-gate-orifice and SFINCS thin_dams paths already prove tagged-feature -> grid-edge snapping) plus a common stage/tailwater boundary source.

### 6. Physics-toggle exposure pattern
Every engine pins its physics to demo-correct defaults and exposes near-nothing: SWAN 3 fixed-coefficient booleans + hardcoded GEN3 WESTHUYSEN; SFINCS no advection/alpha/theta/Coriolis/wind-drag knobs; SWMM DYNWAVE-only with all numerics hardcoded; MODFLOW conservative-tracer-only (no sorption/decay/Kd); GeoClaw fixed order/limiter/CFL; OpenQuake fixed truncation_level/discretization. This is lower-urgency (defaults are correct for the demos) but is the long-tail of full coverage. Build once: a convention for surfacing an `advanced_physics` overrides dict per engine (SFINCS already has the shape via snapwave_inp_overrides; SWMM via OPTIONS) so calibration knobs are additive, non-breaking, and consistently named.

## Ranked backlog

Ranked by demo-value x (1/effort). Effort: S = small (plumb existing fields / one postprocess read), M = medium (new param + worker wiring + fetcher), L = large (new run mode / mesh type / data dependency). The GeoClaw topo fix leads because it is a correctness blocker (high value, S effort) -- not a coverage nicety. Tiers below are: (A) correctness + cheap high-value wins, (B) high-value medium builds, (C) medium-value and large strategic builds.

See the structured `ranked_backlog` field for the full machine-readable list; the markdown highlights the ordering rationale.

### Tier A -- do first (correctness + S-effort high-value)
1. **GeoClaw topo conversion (correctness blocker)** -- convert staged COG to AAIGrid/topotype-3 in the entrypoint (rasterio is in the image) before referencing it. Without this, every non-synthetic GeoClaw run is suspect. value high / effort S.
2. **SFINCS max-velocity output** -- populate the existing `max_velocity_m_s` contract field from SFINCS-native vmax; reuse the existing COG plumbing. Headline hazard quantity. value high / effort S.
3. **SFINCS infiltration** -- add constant qinf / SCS-CN (mirror SWMM's existing curve_number pattern); every pluvial run currently loses zero rain and over-predicts. value high / effort S.
4. **SWMM flooding-volume + node-flooding summary** -- read FLOODING_LOSSES/PONDED_VOLUME alongside INVERT_DEPTH; the spike already proves read_volumes works. "How much water / which intersections flood." value high / effort S.
5. **OpenQuake hazard curves + UHS + multi-PoE/multi-IMT** -- pure job.ini + postprocess flip; the classical run already computes these. The canonical PSHA deliverable. value high / effort S.
6. **MODFLOW WEL wells** -- pump-and-treat / injection is the canonical groundwater-management question; trivial FloPy add to the existing GWF model + SSM. value high / effort S.

### Tier B -- high-value medium builds
7. **SWAN adaptive mesh** -- replace hardcoded (100,100) with bbox/compute-class resolution on the shared AdaptiveResolution helper + #154 gate. value high / effort M.
8. **SWAN wind forcing end-to-end** -- close the dead half-seam: extend fetch_era5_reanalysis (mean_wave_period/dir) + add a u/v -> SWAN WIND array writer so wind_uri enables GEN3 growth. value high / effort M.
9. **SWAN period/direction/spread output** -- surface TM01/TM02/DSPR/SETUP/PDIR (already allow-listed) as COG layers, not just narration scalars. Differentiator over SnapWave. value high / effort M.
10. **SFINCS quadtree wind+pressure parity** -- the quadtree/coastal North-Star path drops wind/pressure that the regular-grid path already emits. value high / effort M.
11. **MODFLOW plume time-series animation** -- read all saved transport steps, not just the last; feeds the scrubber. Data is already computed. value high / effort M.
12. **MODFLOW sorption + decay** -- add Kd/half-life via GwtMst/GwtIst; real contaminant Qs (TCE/PFAS) need retardation. value high / effort M.
13. **SWMM hyetograph shape + observed-rain ingestion** -- SCS-II/uniform/triangular/user-timeseries + a gauge/MRMS file; unlocks real-event replays. value high / effort M.
14. **SWMM tidal/timeseries outfall** -- coastal tailwater coupling for the compound-flood North Star. value high / effort M.
15. **Landlab spatially-variable soil fields** -- per-node cohesion/transmissivity/friction from SSURGO/POLARIS + lognormal_spatial recharge instead of np.full uniform broadcast. Biggest realism lever for landslide. value high / effort M.
16. **Landlab OverlandFlow time-series** -- emit per-step depth/discharge frames (chain already time-loops). value high / effort M.
17. **OpenQuake site model (per-site vs30)** -- replace hardcoded vs30=760; dominant site-amplification driver. value high / effort M.
18. **OpenQuake multi-branch GMPE logic tree** -- real epistemic uncertainty + mean/quantile maps. value high / effort M.
19. **OpenQuake disaggregation mode** -- "what earthquake drives my hazard," the standard follow-up to a hazard map. value high / effort M.
20. **GeoClaw fgmax output** -- max depth/speed/arrival-time, THE tsunami hazard product. value high / effort M.

### Tier C -- strategic large builds + medium long-tail
21. **SFINCS spiderweb (.spw) cyclone forcing from ATCF** -- the canonical hurricane-surge driver; needs an ATCF fetcher + spw writer. value high / effort L.
22. **MODFLOW multi-layer geometry + transient STO** -- unblocks vertical migration/aquitards; needs hydrostratigraphy inputs. value high / effort L.
23. **OpenQuake real sources (fault/point + national source-model XML)** -- the difference between a toy and a usable hazard model. value high / effort L.
24. **Landlab landscape-evolution chain** -- Fastscape/StreamPower + diffuser; Landlab's flagship use case (analysis is an open Literal, no contract break). value high / effort L.
25. **GeoClaw real surge module** -- parametric Holland / gridded wind+pressure ATCF; ties to existing GTSM/CO-OPS fetchers. value high / effort L.
26. **SFINCS spatially-varying precip (spw/netampr grid)** -- preserve storm structure vs area-mean. value medium / effort M.
27. **SFINCS solver physics toggles** (advection/alpha/huthresh/Coriolis/wind-drag/zsini). value medium / effort M.
28. **SFINCS obs-point his outputs + arrival-time** -- computed-vs-observed validation (Mexico Beach philosophy). value medium / effort M.
29. **SWAN selectable physics formulations + coefficients** (whitecapping/breaking-gamma/friction/quadruplets). value medium / effort S.
30. **MODFLOW expose dispersivity/gradient/domain-size levers** -- first-order plume-shape knobs, currently module constants. value medium / effort S.
31. **MODFLOW head/water-table output** -- one more postprocess read of the saved .hds. value medium / effort S.
32. **MODFLOW finish river-DEM streambed sampling** -- river_dem_uri is fetched but never sampled into rbot/stage; gaining/losing is demo-flat. value medium / effort M.
33. **MODFLOW additional BCs (DRN/GHB/RCH/EVT)** -- RCH+EVT make it a credible water-budget model. value medium / effort M.
34. **SWMM routing-method + numeric tunables** (KINWAVE/STEADY, ROUTING_STEP, THREADS) -- THREADS=1 caps the resolution ceiling. value medium / effort S.
35. **SWMM data-driven infiltration params** (CN from GCN250, Green-Ampt from STATSGO) -- closes a silent-default honesty gap. value low / effort M.
36. **SWMM pumps/weirs/storage** -- core stormwater vocabulary via the existing tagged-feature snapping. value medium / effort L.
37. **SWMM LID controls** -- marquee "what if permeable pavement" differentiator; substantial, no existing seam. value medium / effort L.
38. **GeoClaw velocity/momentum/eta outputs** -- compute speed from hu/hv/h. value medium / effort M.
39. **GeoClaw gauges** -- point hydrographs for validation; pairs with conversational-analysis charts. value medium / effort M.
40. **GeoClaw tunable base grid + refinement criteria** -- reuse #154 gate. value medium / effort S.
41. **Landlab surface discarded fields** (drainage_area/slope/relative_wetness/native FoS). value medium / effort S.
42. **Landlab flow-routing choice** (D8/Dinf/MFD; PriorityFlood). value medium / effort S.
43. **Landlab boundary control + outlet fix** -- fix the doc/code mismatch where overland claims to set a watershed outlet but does not. value medium / effort S.
44. **OpenQuake adaptive site-grid coarsening** -- implement the OOM-guard the docstrings already promise. value medium / effort S.
45. **OpenQuake parameterize source geometry** (nodal-plane/hypo-depth/magScaleRel/TRT). value medium / effort S.
46. **OpenQuake multi-IMT in one run** (PGA + SA(0.2) + SA(1.0)). value medium / effort S.
47. **SFINCS weir/levee structures + storage cells** -- compound-flood features beyond buildings. value low / effort M.
48. **OpenQuake native risk family** (scenario/classical/event-based risk) -- deliberately routed through Pelicun today; large surface. value medium / effort L.
49. **OpenQuake event-based + scenario modes** -- per-event GMF rasters; feeds animation. value medium / effort L.
50. **SWAN wave-setup + radiation-stress output** -- prerequisite for SWAN->SFINCS one-way wave-setup coupling. value medium / effort L.
51. **SWAN richer boundaries** (multi-segment/TPAR/BOUNDNEST3) + curvilinear/unstructured mesh. value low / effort L.
52. **SWAN HOTFILE restart** -- Spot-reclaim recovery for long nonstationary runs. value low / effort M.
53. **MODFLOW DISV quadtree refinement** -- SFINCS quadtree is an in-repo precedent. value low / effort L.
54. **GeoClaw per-edge BC selection** (wall/extrap/periodic) -- reflecting valley walls / closed basins. value low / effort S.
55. **GeoClaw solver numerics knobs** (order/limiter/cfl/source_split). value low / effort S.
56. **Landlab non-raster grid** (Hex/Network) -- hexagonal hillslope / channel-network transport. value low / effort L.

## Recommended sequencing

1. **Unblock GeoClaw (item 1)** -- it is broken, not merely thin.
2. **Build the generic output-quantity publisher (pattern 1) + time-series toggle (pattern 2)**, then land Tier-A items 2,4,5 and Tier-B items 9,11,16,20 on top of it -- ~9 gaps closed by ~2 abstractions.
3. **Generalize AdaptiveResolution (pattern 4)** off the proven SFINCS shape, retrofitting SWAN/GeoClaw/MODFLOW/OpenQuake (items 7,40,44 + the latent OpenQuake OOM).
4. **Close the forcing half-seams (pattern 3)** -- items 8,10,32 + GeoClaw surge -- with a CI assertion that every contract forcing URI reaches the deck.
5. **Attack the most under-covered engines (OpenQuake, MODFLOW, SWMM, Landlab)** via Tier-B, prioritizing the compound-flood North Star chain (SFINCS infiltration + SWMM tidal outfall, items 3+14) and the hurricane chain (SFINCS spiderweb + quadtree wind, items 10+21).

## Note on scope discipline

These slices are deliberate Phase-1 demo cuts, and the plumbing/honesty discipline behind them is genuinely strong -- this audit is not a critique of the engineering, it is the gap between "demo-complete" and NATE's full-coverage goal. The right move is not to chase 100% per engine but to build the six shared abstractions once (especially the output publisher, the forcing seam, and the resolution lever) so that coverage compounds: each abstraction simultaneously raises the floor across multiple engines and makes the next engine integration land closer to full coverage by construction.