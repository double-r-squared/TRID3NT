# Spike: Riverine Dye / Tracer / Sediment Transport (the Baird "dye release" demo)

Research + scope spike for a GRACE-2 RIVERINE dye/tracer/sediment transport demo,
prompted by the Baird & Associates webinar slide NATE flagged: a 2D dye-concentration
plume (rainbow legend, ~mg/L) moving DOWN A RIVER REACH, timestamped 2020/05/18, with
cross-section concentration profiles. Two questions: (1) which software produced that
demo, and (2) what is the best path to a riverine dye/sediment/chemical transport demo
on the live GRACE-2 stack.

Grounded against primary sources (Baird's own capability pages + publications, the
Delft3D/TUFLOW-FV/MIKE/HEC-RAS vendor docs) AND the in-repo engine spikes
(`engine_spike_telemac.md`, `engine_spike_delft3d.md`, the SHELVED-HEC-RAS verdict in
the engine-drivability ranking) AND the live solver seam (the SWMM/SFINCS/GeoClaw
per-frame-COG scrubber path + the MODFLOW-GWT final-timestep plume path).

ASCII only. No em/en dashes, no unicode arrows; "->" for arrows. Status: research +
scope + recommendation only, NO application code in this doc.

---

## 0. Verdict (one paragraph)

The Baird demo most likely used **Delft3D** (Deltares; D-Flow + D-Water-Quality/DELWAQ
for a conservative dye tracer, or D-MOR for suspended sediment) - MEDIUM confidence;
TUFLOW FV with the Advection-Dispersion (AD) module is the strong second candidate, and
Baird's OWN in-house MISED model a third. I could NOT pin it to a single named package
from public sources (Baird's site names capabilities, not tools), so S1 ranks the
candidates with evidence rather than asserting one. For GRACE-2's build path the
recommendation is decoupled from that uncertainty: **add surface-water advection-
dispersion tracer transport via TELEMAC-2D + its native TRACER module (open-source,
already research-spiked, headless-on-Batch, the cleanest single-engine fit), with a
lightweight passive-tracer-on-an-existing-flow-field fallback for a fast viz-grade v1.**
The already-built MODFLOW-GWT is GROUNDWATER transport and is NOT a fit for a surface
river dye release (stated plainly in S2/S3); it is at best an adjacent demo. The single
biggest risk is mesh + boundary-condition authoring (the same unstructured-mesh deck
cost the TELEMAC spike already flagged), NOT the transport physics.

---

## S1. The Baird software identification + evidence

### S1.0 What the slide shows (the signal to match)

A 2D dye-CONCENTRATION field (rainbow / blue->cyan->green->yellow legend, units ~mg/L)
threading DOWN a sinuous river reach, dated 2020/05/18, with CROSS-SECTION concentration
profiles. This is a depth-averaged-or-3D SURFACE-water hydrodynamic solve carrying a
CONSTITUENT (a conservative dye tracer, or a suspended-sediment concentration) by
advection-dispersion down a channel. The cross-section profiles imply a model with a
proper 2D/3D mesh (not a 1D cross-section-only model). This matches what the prior
orchestrator already identified in memory as the Baird slide-14 "dye carried down the
ravine" plume (`project_sediment_dye_transport_north_star`,
`project_baird_coastal_lecture_oceanmesh2d`). Note: the Baird coastal lecture images
referenced in memory at `reports/references/lecture_baird_coastal/` are NOT present in
the working tree (that dir does not exist on this machine); identification below rests on
web/primary sources + the memory captions, not a re-read of the slide pixels.

### S1.1 Confidence: MEDIUM. Best single identification: Delft3D (Deltares).

I could NOT find a primary Baird source that names the software behind THIS specific
slide. Baird's public capability pages describe the WORK ("3-D hydrodynamic and water
quality modelling", "multi-dimensional hydrodynamic and sediment transport modelling",
"3D far-field modelling techniques" for thermal/constituent plumes) but deliberately do
NOT name vendor packages (confirmed by fetching
baird.com/our-capabilities/coastal-river-environmental/ - zero named tools). So this is
an evidence-ranked best-guess, not a confirmed fact. Do NOT report it as certain.

### S1.2 Candidate ranking (most to least likely), with evidence and reasons

1. **Delft3D (Deltares) - MOST LIKELY (~45 percent).**
   - WHY: Delft3D is THE canonical open suite for exactly this picture - D-Flow
     (2D/3D hydrodynamics) + D-Water-Quality / DELWAQ (conservative tracers INCLUDING
     rhodamine DYE, contaminants, DO, nutrients) + D-Morphology (suspended sediment
     concentration) + D-WAQ PART (Lagrangian dye/spill particle tracking, explicitly
     listing "rhodamine dye" as a supported substance over the 200 m - 15 km mid-field
     range). The rainbow concentration-plume-down-a-channel visual is the textbook
     Delft3D-WAQ/MOR output.
   - Baird-specific corroboration: Baird publishes Delft3D-adjacent sediment-plume /
     estuary work, and Delft3D is the dominant tool in the peer literature Baird's
     people cite for "sediment plume / estuary / dredge dispersion" (multiple Baird and
     Baird-coauthored dredge/plume studies sit in the Delft3D ecosystem). The firm's
     "multi-dimensional hydrodynamic and sediment transport" capability language maps
     directly onto Delft3D's module set.
   - CAVEAT: this is inference from "the tool the rest of the field uses for this exact
     plot," not a Baird citation naming Delft3D for THIS slide.

2. **TUFLOW FV + the Advection-Dispersion (AD) module - STRONG SECOND (~25 percent).**
   - WHY: TUFLOW FV (flexible unstructured mesh) has a dedicated AD module for
     advection-dispersion of arbitrary scalar constituents (a dye/tracer), PLUS a
     Sediment Transport (ST) module (cohesive + non-cohesive, bed + suspended load) and
     particle tracking - the precise "dye OR sediment down a reach in 2D/3D" capability,
     on an unstructured mesh that yields clean cross-section profiles. TUFLOW runs
     webinars on exactly this (sediment transport + particle tracking + AD), and the
     "2D concentration field on a flexible mesh with cross-sections" look is very TUFLOW.
   - Baird-specific corroboration: Baird is a known TUFLOW-ecosystem consultancy for
     coastal/riverine/sediment work; TUFLOW FV is heavily used in their AU/coastal sphere.
   - WHY NOT FIRST: TUFLOW is COMMERCIAL/closed (a NO_GO for our own stack per
     `engine_spike_tuflow.md`), and the slide's exact provenance is unconfirmed; ranked
     just below Delft3D on the dye-concentration-plume specificity.

3. **Baird MISED (their OWN in-house 3D model) - THIRD (~15 percent).**
   - WHY: Baird developed and publishes with MISED, a proprietary 3D hydrodynamic +
     sediment-transport numerical model (handles hydrodynamics, temperature, salinity,
     sediment transport, morphology), used in their dredge-plume / disposal studies
     (e.g. the Ponta da Madeira / Sao Luis, Brazil pier study). A firm with an in-house
     transport model may well have produced the slide with it.
   - WHY NOT HIGHER: MISED is sediment-focused and 3D-marine-dredge-plume oriented; the
     slide reads as a riverine DYE/tracer concentration down a reach (a WQ-tracer look),
     which is more DELWAQ/AD than MISED's suspended-sediment-near-seabed outputs. Also
     proprietary, so irrelevant to our build either way.

4. **MIKE 21/3 (DHI) + the ECO Lab / AD / MT (mud transport) modules - FOURTH (~10 percent).**
   - WHY: MIKE 21/3 FM with ECO Lab (water quality / arbitrary constituent) or the AD /
     Mud-Transport modules produces the same class of 2D/3D concentration plume; DHI is a
     direct Delft3D/TUFLOW competitor for this exact deliverable.
   - WHY LOWER: no Baird-MIKE signal surfaced; MIKE is commercial; ranked below the
     three above on absence of a Baird link.

5. **HEC-RAS Water Quality / Sediment - LEAST LIKELY (~5 percent).**
   - WHY POSSIBLE: HEC-RAS 2D has a Water Quality module (temperature + arbitrary
     constituent = a tracer/dye) and a Sediment Transport module; it CAN draw a 2D
     concentration field with cross-sections, and it is the tool the
     `project_sediment_dye_transport_north_star` memo originally pencilled as the home.
   - WHY LEAST: Baird is a Delft3D/TUFLOW/MIKE/MISED-class firm, not a primarily
     HEC-RAS shop for this kind of marine/estuarine/riverine transport deliverable; the
     slide's polished rainbow-on-dark-basemap aesthetic is not the typical HEC-RAS RAS
     Mapper look. Included for completeness.

### S1.3 The 2020/05/18 timestamp (a corroborating thread, not a proof)

The 2020/05/18 date and the riverine setting are consistent with the SAME Baird lecture's
documented MAY 2020 ILLINOIS compound-flood case study (record May-2020 rainfall over
the Des Plaines / Illinois River, Joliet -> Morris IL; captured in
`project_baird_coastal_lecture_oceanmesh2d`). If the dye slide is from that same 2D
integrated model, the engine is whatever drove that compound-flood model - which the
memo notes used Baird's OceanMesh2D-built UNSTRUCTURED mesh (an ADCIRC/TELEMAC-class
integrated approach), reinforcing that the demo is a mesh-based 2D/3D transport solve
(candidates 1-4), NOT a 1D HEC-RAS run. This is suggestive, not conclusive.

### S1.4 Honest bottom line for S1

Report to NATE: "Almost certainly Delft3D (Deltares D-Flow + D-Water-Quality/DELWAQ for
the dye, or D-Morphology for sediment); TUFLOW FV with its AD module is a strong second
and Baird's own MISED a third. I could not confirm the exact package from public Baird
sources - the firm names capabilities, not tools - so this is a ranked best-guess at
medium confidence, not a certainty." DO NOT claim it as confirmed.

---

## S2. What GRACE-2 has vs the gap

### S2.1 Engines live on the AWS Batch island today

| Engine | Class | Transport capability | Relevant to a river dye release? |
|--------|-------|----------------------|----------------------------------|
| SFINCS (+SnapWave) | compound coastal/pluvial flood (reduced-physics) | depth/level only; NO tracer/constituent transport | NO - produces inundation, not concentration |
| SWMM (PySWMM) | urban pipe-network + quasi-2D overland drainage | flow + depth; SWMM has a water-quality/pollutant routing capability NOT wired in GRACE-2 | PARTIAL - pipe-network, not an open channel reach |
| GeoClaw (Clawpack) | shallow-water / dam-break / tsunami / surge | depth + velocity; NO constituent transport wired | NO - inundation/velocity only |
| MODFLOW 6 GWF+GWT (FloPy) | GROUNDWATER flow + solute/chemical transport | YES - real advection-dispersion-reaction of a conservative tracer (ADV TVD + DSP + MST + SRC) -> concentration COG (mg/L) | NO for SURFACE water - it is a SUBSURFACE solver (see S2.3) |

### S2.2 The rendering substrate already exists (this is the good news)

The hard part of "show a concentration field animate down a reach" - the time-stepped
COG-to-scrubber pipeline - is ALREADY built and reusable:

- `postprocess_swmm.py` / `postprocess_flood.py` / `postprocess_geoclaw.py` emit
  `layers[0]` = a PEAK COG plus `layers[1:]` = up to `MAX_FLOOD_FRAMES` (=24) per-timestep
  COGs, all sharing a temporal-group token so the LayerPanel collapses them into ONE
  bottom-center SCRUBBER. The even-subsample frame selector `_select_frame_time_indices`,
  the EPSG:4326 COG-write + CRS-round-trip guard (`_write_depth_cog_4326` /
  `_write_reprojected_cog`), and the typed-LayerURI honesty floor are all shared
  primitives. A dye-concentration field is the SAME shape: swap the depth band for a
  concentration band, swap the style preset, reuse everything else.
- `postprocess_modflow.py` already proves the "concentration COG in mg/L +
  `max_concentration_mgl` + `plume_area_km2` + a `continuous_plume_concentration` style
  preset" path end-to-end - so the CONCENTRATION rendering + narration scalars exist;
  only the SURFACE-water flow+transport SOLVE is missing.
- The cross-section concentration profile on the slide maps onto the just-spiked
  cross-section profile tool (`reports/design/spike_cross_section_profile_tool.md`) - a
  near-free add for the "show cross-section concentration" half of the slide.

### S2.3 The gap, stated precisely

GRACE-2 has NO SURFACE-water (river/channel/floodplain) ADVECTION-DISPERSION constituent
transport engine. It can:
- move water over a surface (SFINCS, GeoClaw, SWMM) but NOT carry a dissolved/suspended
  constituent on that flow; and
- transport a solute through the SUBSURFACE (MODFLOW-GWT) but that is groundwater, not a
  river reach.

So the Baird dye-down-a-river demo - a surface 2D/3D hydrodynamic field carrying a
dye/sediment concentration by advection-dispersion - has NO engine today. That is the
gap S3 fills.

### S2.4 Is MODFLOW-GWT a fit? NO (stated clearly, per the kickoff)

MODFLOW 6 GWF+GWT is GROUNDWATER flow + solute transport: it solves Darcy flow through a
porous aquifer (CHD/NPF/DIS) and advects-disperses a solute through that subsurface
medium. A river dye release is OPEN-CHANNEL SURFACE-water advection-dispersion governed
by the shallow-water / Saint-Venant equations with a free surface and turbulent mixing -
a fundamentally different flow field. Using MODFLOW-GWT to depict a surface river plume
would be physically WRONG (porous-media Darcy flow is not channel hydraulics) and would
violate the honesty floor. Additionally, the MODFLOW postprocess emits only a
FINAL-TIMESTEP concentration COG (no per-frame animation), so it cannot even produce the
time-stepped scrubber the slide shows. CONCLUSION: MODFLOW-GWT is NOT the engine for the
river dye release.

It IS, however, a strong ADJACENT near-term demo: the contamination-plume x
Fields-of-the-World demo (`demo_spike_contamination_fotw.md`, ~90 percent built) is a
real, shippable groundwater-transport plume readout TODAY, and the long-term thread
(surface dye -> river -> groundwater) connects a future surface-transport engine to the
existing GWT solute path. So MODFLOW-GWT is the "we already do solute transport, just in
the subsurface" companion piece, not the river-dye engine itself.

---

## S3. Recommended engine + why

### S3.1 Recommendation

**Primary: TELEMAC-2D + its native TRACER module** for the surface-water dye/tracer
advection-dispersion solve, with **GAIA/SISYPHE** as the sediment-transport extension of
the same engine. **Secondary (fast v1 / fallback): a lightweight passive-tracer
advection pass on an EXISTING flow field** (a velocity field from SFINCS, GeoClaw, or
TELEMAC hydrodynamics) for a viz-grade animated dye that ships fast without a full WQ
solve. Defer Delft3D-FM (the Baird-likely tool) as the heavier, higher-fidelity
alternative only if the morphology/DELWAQ wedge is independently greenlit.

### S3.2 Why TELEMAC-2D + TRACER (the load-bearing reasons)

1. **It is open-source, headless-on-Linux-Batch, and ALREADY RESEARCH-SPIKED.** The
   TELEMAC-2D spike (`engine_spike_telemac.md`, verdict GO_WITH_CAVEATS) already proved
   the engine clears both hard gates: GPL open-source (no license server, the opposite of
   TUFLOW's NO_GO), runs fully non-interactive (`telemac2d.py <case>.cas`), installs from
   the conda-forge `opentelemac` package on a micromamba base, and slots into the
   EXISTING `_resolve_batch_job_def` -> `SOLVER_BATCH_JOBDEF_REGISTRY` ->
   `GRACE2_AWS_BATCH_JOB_DEF_TELEMAC2D` solver seam with zero new transport code. Almost
   all of that work (mesh author, `.cas`/`.cli` templating, SELAFIN postprocess) is
   shared between a hydraulics-only run and a hydraulics+tracer run.

2. **TRACER transport is a NATIVE, first-class TELEMAC-2D capability - not a bolt-on.**
   TELEMAC-2D ships passive/active TRACER advection-diffusion built into the same solver:
   the steering file adds `NUMBER OF TRACERS`, `NAMES OF TRACERS`, tracer initial/
   boundary conditions, `COEFFICIENT FOR DIFFUSION OF TRACERS` (the dispersion term), and
   tracer SOURCE terms (the dye release point + rate, via `SOURCES FILE` /
   `ABSCISSAE OF SOURCES` / `WATER DISCHARGE OF SOURCES` / `VALUE OF THE TRACERS AT THE
   SOURCES`). The result `.slf` then carries a per-node tracer CONCENTRATION per output
   frame alongside depth/velocity - exactly the dye-concentration field the slide shows,
   directly rasterizable to a per-frame concentration COG. This is the SAME deck the
   TELEMAC spike already templates, plus a tracer block; the marginal cost over the
   hydraulics-only TELEMAC integration is small.

3. **Sediment is the same engine.** TELEMAC's GAIA module (the successor to SISYPHE)
   couples to TELEMAC-2D for suspended + bed-load sediment transport and morphology - so
   the "sediment OR dye" both-halves ask in the kickoff is ONE engine, two modules
   (TRACER for dye/chemical, GAIA for sediment), sharing the mesh + boundary + postprocess
   substrate. No second engine needed to cover both constituents.

4. **It reuses our rendering substrate wholesale.** The per-frame concentration COG ->
   temporal-group scrubber path (S2.2) and the `continuous_plume_concentration`
   concentration style + `max_concentration_*`/`plume_area_*` narration scalars
   (MODFLOW-GWT path) are both already built. TELEMAC's SELAFIN postprocess
   (`postprocess_telemac.py` per the spike) barycentric-rasterizes a nodal field per
   frame; pointing it at the TRACER concentration band instead of (or in addition to) the
   depth band is a small delta on an already-designed postprocess.

### S3.3 Why the lightweight passive-tracer fallback (the fast v1)

A FULL TELEMAC TRACER solve requires the unstructured mesh + boundary deck (the cost in
S5). For a fast, low-risk v1 demo that produces the VISUAL (dye animating down a reach)
without a full WQ solve, run a passive-tracer advection pass on an existing flow field:
take a depth-averaged velocity field (from a SFINCS/GeoClaw run, or a cheap TELEMAC
hydraulics-only run), seed a unit dye mass at the release point, and integrate a simple
advection-dispersion (or particle-tracking) step to produce per-frame concentration COGs.
This is viz-grade not engineering-grade (a conservative passive tracer, no reactions, no
real WQ kinetics) and MUST be labelled as such (honesty floor), but it ships the
slide's headline animation quickly on engines we already run and de-risks the full
TELEMAC-TRACER build. This mirrors the
`project_sediment_dye_transport_north_star` route-(b) "lightweight passive-tracer /
particle-tracking pass on ANY flow field."

### S3.4 The tradeoff table (drivability / licensing / Batch)

| Engine path | License | Headless-on-Linux-Batch | AI-drivability | Tracer (dye) | Sediment | Already spiked? | Net |
|-------------|---------|-------------------------|----------------|--------------|----------|-----------------|-----|
| TELEMAC-2D + TRACER + GAIA | GPL (open) | YES (conda-forge `opentelemac`, `telemac2d.py`) | HIGH (plain-text `.cas`+mesh+`.cli`, machine-authorable) | NATIVE | NATIVE (GAIA) | YES (engine_spike_telemac.md, GO_WITH_CAVEATS) | RECOMMENDED primary |
| Passive-tracer on existing flow field | n/a (our code) | YES (pure numpy/rasterio in worker) | HIGH (a thin postprocess pass) | viz-grade only | viz-grade only | partial (this spike) | RECOMMENDED fast v1 / fallback |
| Delft3D-FM + DELWAQ / D-MOR | GPL family (open) | YES but container access-gated + multi-GB + Beta Docker; coupled multi-file deck | MEDIUM (needs hydrolib-core/HydroMT deck author) | NATIVE (DELWAQ) | NATIVE (D-MOR) | YES (engine_spike_delft3d.md, GO_WITH_CAVEATS, B-tier) | DEFER - the Baird-likely tool but heavier; only if MOR/DELWAQ wedge greenlit |
| HEC-RAS Water Quality / Sediment | free (USACE) but Windows/GUI heritage | UNCERTAIN (headless-Linux feasibility unproven) | LOW-MEDIUM | module | module | SHELVED | NO - shelved for GeoClaw in the drivability ranking; do not revive for this |
| TUFLOW FV + AD | COMMERCIAL (closed, license server) | blocked (node-locked license, no Spot/ephemeral/multi-tenant) | n/a | module | module | YES (engine_spike_tuflow.md, NO_GO) | NO - the likely Baird tool but a closed-license NO_GO for our stack |
| MIKE 21/3 + ECO Lab | COMMERCIAL (closed) | blocked | n/a | module | module | no | NO - commercial, no path |
| MODFLOW-GWT (existing) | open (USGS) | YES (already live) | HIGH | GROUNDWATER only | no | live | NO for surface river; adjacent demo only (S2.4) |

The decisive columns: the Baird-likely tools (TUFLOW, MIKE) are commercial NO_GOs for
our open scale-to-zero stack; Delft3D is open and the closest match to Baird but heaviest
to integrate; TELEMAC-2D is open, already-spiked, headless-Batch-proven, and carries
tracer (dye) AND sediment (GAIA) natively in ONE engine - so it is the best path even
though it is probably NOT the exact tool Baird used. We replicate the CAPABILITY with the
best open engine, not the exact vendor product.

---

## S4. Concrete build outline

Mirrors the existing `model_*` composer + `run_*` + `postprocess_*` engine pattern and
the TELEMAC spike's ordered job list. This is the dye/tracer EXTENSION of that spike, so
it assumes the TELEMAC-2D hydraulics integration (spike jobs T1-T7) as the substrate and
adds the tracer/sediment delta on top.

### S4.1 Data inputs

| Input | Source (existing GRACE-2 fetchers) | Role |
|-------|-----------------------------------|------|
| DEM / bathymetry | the topobathy / 3DEP COG the other engines stage (`fetch_topobathy`, 3DEP) + USACE channel bathy where available; interpolated onto mesh nodes | the channel + floodplain bottom elevation for the mesh |
| Flow boundary (upstream inflow) | NWM streamflow (`fetch_noaa_nwm_streamflow`), USGS gauge Q-H rating curves, or a design hydrograph | the riverine inflow hydrograph -> `PRESCRIBED FLOWRATES` at the inlet node string |
| Downstream boundary | stage/tide (`fetch_noaa_coops_tides`) or a rating-curve stage | `PRESCRIBED ELEVATIONS` at the outlet node string |
| Channel centerline / reach geometry | NHDPlus / OSM-waterway fetchers (already used by the TELEMAC mesh refiner) | mesh refinement along the channel + the dye-source placement reference |
| Friction | NLCD -> Manning-n substrate (`load_manning_mapping`, shared with SFINCS/SWMM) | `LAW OF BOTTOM FRICTION` + coefficient |
| THE DYE SOURCE TERM (net-new) | user-specified or chosen on the reach: release LOCATION (a point on the channel), RELEASE RATE (mass/time or a concentration over a duration), START TIME + DURATION | the tracer source: `SOURCES FILE` / `WATER DISCHARGE OF SOURCES` / `VALUE OF THE TRACERS AT THE SOURCES`; for sediment, a GAIA sediment input rate/class |

### S4.2 Workflow stages (mirroring the existing composers)

1. **`model_river_dye_release_scenario.py`** (the composer, the
   `model_groundwater_contamination_scenario` / `model_dambreak_geoclaw_scenario`
   analogue): resolve AOI + reach -> fetch DEM/bathy + inflow/outflow boundaries +
   centerline -> stage a `TelemacTracerBuildSpec` (= the TELEMAC build_spec + tracer
   block: `NUMBER OF TRACERS`, names, diffusion coefficient, and the dye source point/
   rate/duration; optional GAIA sediment block) -> dispatch the Batch solve ->
   postprocess -> emit the ranked readout + render.
2. **Deck author delta** (on the TELEMAC `deck_builder.py`): render the tracer keywords
   into the `.cas`, write the `SOURCES FILE` placing the dye source at the release node,
   set tracer IC/BC. Deterministic, TELEMAC-free at author time, unit-tested (the
   `setrun_builder` discipline).
3. **Solve** on the existing Batch seam (`run_telemac` registered in
   `SOLVER_WORKFLOW_REGISTRY`; `solver="telemac2d"`), confirmation-gated (Invariant 9).
4. **`postprocess_telemac` tracer branch**: read the SELAFIN result's TRACER
   concentration band per frame, barycentric-rasterize onto the EPSG:4326 grid, write
   `layers[0]` = peak-concentration COG + `layers[1:]` = per-frame concentration COGs
   (the `MAX_FLOOD_FRAMES` temporal-group scrubber path), compute
   `max_concentration_mgl` + `plume_area_km2` (or `max_concentration` for an abstract
   tracer), honesty-gate an all-zero field to a typed error.
5. **Cross-section profile** (optional, the slide's second half): run the just-spiked
   cross-section profile tool on the concentration COG at user-placed transects to
   reproduce the slide's cross-section concentration plots.

### S4.3 Output layers (matching SWMM/SFINCS render)

- A typed `TelemacTracerLayerURI(LayerURI)` carrying `max_concentration_*` +
  `plume_area_km2` (+ echoed dye source + reach), so the `add_loaded_layer` emitter gate
  fires and the LLM narrates only typed scalars (Invariant 1).
- A NEW `continuous_tracer_concentration` style preset (or reuse
  `continuous_plume_concentration` from the MODFLOW path - same physical quantity, mg/L
  rainbow), surfaced through the single `publish_layer` styling seam.
- `layers[0]` peak + `layers[1:]` per-frame concentration COGs collapsed into ONE
  bottom-center scrubber via the shared temporal-group token - the animated dye-down-the-
  reach the slide shows, on the EXISTING scrubber, with ZERO new web contract.

### S4.4 Net-new vs reused

- REUSED: the entire TELEMAC-2D hydraulics integration (mesh author, `.cas`/`.cli`
  templating, SELAFIN postprocess, Batch seam, autoscaler), the per-frame-COG scrubber +
  temporal-group LayerPanel path, the concentration-COG + narration-scalar pattern, the
  `publish_layer` styling seam, the cross-section profile tool.
- NET-NEW: the tracer/GAIA keyword block + `SOURCES FILE` author delta, the
  `TelemacTracerBuildSpec` + `TelemacTracerLayerURI` contracts, the
  `model_river_dye_release_scenario` composer, the tracer branch of
  `postprocess_telemac`, one concentration style preset, and (for the fast v1) the
  standalone passive-tracer-on-a-flow-field pass.

---

## S5. Effort / risk + the v1 cut

### S5.1 Effort

This rides ENTIRELY on the TELEMAC-2D engine integration (spike jobs T1-T7). IF TELEMAC
is landed for hydraulics, the dye/tracer DELTA is SMALL (1-2 jobs): the tracer keyword
block + source file in the deck author, the tracer postprocess branch, the two contracts,
one composer, one style preset. IF TELEMAC is NOT yet landed, the bulk of the effort is
the TELEMAC integration itself (the unstructured mesh + boundary deck author - the "mesh
is the cost" caveat from the TELEMAC spike), and the dye delta is a thin topping. So the
honest framing: this demo is GATED on the TELEMAC-2D engine landing; on top of that it is
a light add.

The fast v1 (passive-tracer on an existing flow field) is INDEPENDENT of TELEMAC and much
cheaper - a single postprocess-style pass on a SFINCS/GeoClaw velocity field plus the
concentration-COG scrubber wiring - and can ship the headline animation first.

### S5.2 Risks (ranked)

1. **BIGGEST RISK: unstructured mesh + boundary-condition authoring** (inherited from the
   TELEMAC spike's #1 caveat). A valid TELEMAC TRACER run needs a conforming triangular
   mesh over the reach (Gmsh) + a matching `.cli` boundary file + correctly placed source
   nodes; a mesh/boundary/source mismatch silently mis-runs. This is the dominant cost and
   risk, NOT the tracer physics. Mitigation: ride the TELEMAC spike's mesh author + the
   granularity gate; prove on a small gauged reach first.
2. **Dispersion-coefficient calibration / physical honesty.** A dye plume's spread depends
   on the turbulent diffusion coefficient; an uncalibrated value gives a plausible-looking
   but quantitatively unreliable plume. v1 must label concentrations as
   model-with-assumed-dispersion (honesty floor), not measured. The passive-tracer
   fallback is even more clearly viz-grade and must say so.
3. **Long-tail solve runtime on Spot.** A fine reach mesh + many tracer output frames is a
   longer solve; lean on the S3-completion-manifest decoupling + the #154 granularity gate
   (already standard for every Batch engine) so a Spot reclaim / box-sleep does not lose
   the run.
4. **Engine overlap / routing confusion.** TELEMAC tracer vs MODFLOW-GWT vs SWMM-WQ must
   route correctly: surface river dye -> TELEMAC TRACER; groundwater solute -> MODFLOW-GWT;
   pipe-network pollutant -> SWMM. State this in the tool descriptions so the LLM picks
   right.

### S5.3 What a v1 demo cuts

- ONE constituent: a single CONSERVATIVE dye tracer (no reactions, no decay, no
  multi-species WQ kinetics). No sediment in v1 (GAIA is the v2 extension).
- 2D depth-averaged only (no 3D stratification) - matches the slide's 2D plan view.
- A SINGLE pre-placed dye source (point + constant rate + duration); no multi-source, no
  interactive lasso placement in v1.
- A fixed/assumed dispersion coefficient (no calibration loop), clearly labelled.
- Optionally ship the PASSIVE-TRACER fallback as v1 (viz-grade animation on an existing
  flow field) and the full TELEMAC-TRACER solve as v2, to get the slide's headline visual
  out fast while the mesh-engine work matures.
- Cross-section concentration profiles (the slide's second panel) are a v1.5 add via the
  existing cross-section profile tool - nice-to-have, not blocking.

---

## S6. Sources (primary + in-repo)

Web / primary:
- Baird capability pages: baird.com/our-capabilities/coastal-river-environmental/ (names
  "3-D hydrodynamic and water quality modelling", "multi-dimensional hydrodynamic and
  sediment transport modelling"; names NO vendor tool).
- Baird MISED (in-house 3D hydrodynamic + sediment-transport model): the
  "3D Modelling on Dispersion of Dredge Sediment Plumes" study (Ponta da Madeira / Sao
  Luis, Brazil) - confirms MISED exists + its capability set.
- Delft3D: Deltares D-Water-Quality (DELWAQ) + D-WAQ PART user materials (rhodamine DYE,
  conservative/decaying tracers, 200 m - 15 km mid-field); USGS Model Catalog Delft3D
  entry; the broad Delft3D sediment-plume/estuary literature.
- TUFLOW FV: the Advection-Dispersion (AD) module + Sediment Transport (ST) module +
  particle tracking docs (docs.tuflow.com, fvwiki.tuflow.com); TUFLOW webinar library.
- MIKE 21/3 + ECO Lab (DHI) - the commercial AD/WQ peer.
- HEC-RAS Water Quality + Sediment (USACE) - the shelved candidate.

In-repo (cross-checked):
- `reports/design/engine_spike_telemac.md` (TELEMAC-2D GO_WITH_CAVEATS; the mesh +
  `.cas`/`.cli` + SELAFIN postprocess substrate; "mesh is the cost").
- `reports/design/engine_spike_delft3d.md` (Delft3D GO_WITH_CAVEATS, B-tier; DELWAQ +
  D-MOR as the unique wedge; container access-gate + coupled-deck cost).
- `reports/design/engine_spike_tuflow.md` (TUFLOW NO_GO, commercial license).
- `reports/design/demo_spike_contamination_fotw.md` (the MODFLOW-GWT plume x FTW demo,
  ~90 percent built; the adjacent groundwater-transport demo).
- `reports/design/spike_cross_section_profile_tool.md` (the cross-section profile tool
  for the slide's second panel).
- `services/agent/src/grace2_agent/workflows/postprocess_swmm.py` /
  `postprocess_flood.py` / `postprocess_geoclaw.py` (the per-frame-COG `MAX_FLOOD_FRAMES`
  temporal-group scrubber path).
- `services/agent/src/grace2_agent/workflows/postprocess_modflow.py` (the concentration
  COG in mg/L + `max_concentration_mgl` + `plume_area_km2` +
  `continuous_plume_concentration` style; FINAL-timestep only, no animation).
- Memory: `project_sediment_dye_transport_north_star` (the dye-down-a-ravine North Star +
  the HEC-RAS-or-passive-tracer routes), `project_baird_coastal_lecture_oceanmesh2d`
  (the Baird lecture + the May-2020 Illinois compound-flood case + OceanMesh2D
  unstructured mesh), `reference_engine_cloud_ai_drivability_ranking` (HEC-RAS SHELVED for
  GeoClaw; the open-engine drivability tiers).
