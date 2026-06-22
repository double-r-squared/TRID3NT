# Engine Spike: SWAN (third-generation spectral nearshore wave model)

Research spike (task #175) for adding the open-source SWAN (Simulating WAves
Nearshore) spectral wind-wave model to GRACE-2 as a DEDICATED nearshore wave
engine alongside the SnapWave path SFINCS already carries. Grounded against
primary sources (swanmodel.sourceforge.io official features/license/online docs,
the TU Delft SWAN page, the Delft3D-WAVE manual, the SWAN+ADCIRC and COAWST
literature) AND against the live GRACE-2 solver/forcing seam (the SFINCS+SnapWave
quadtree path and the GeoClaw worker are the closest structural analogues and are
cited throughout as the integration template).

ASCII only. No em/en dashes, no unicode arrows; "->" for arrows. Status: design +
verdict only, no code in this doc.

---

## 0. Verdict

**GO_WITH_CAVEATS.**

SWAN passes both hard gates cleanly. It is FREE and GPL (no registration, no fee,
no per-seat key, no license server) - the sharp opposite of the TUFLOW NO_GO -
so there is NO licensing blocker to running it on AWS Batch and redistributing it
in a container. It is fully HEADLESS and text-driven: one ASCII command file
(`.swn` -> copied to `INPUT`) plus a handful of input grids, run via
`./swanrun -input <case> [-omp n]`, no GUI, no display, no dongle. And it is
LLM-cloud-drivable: the command file is a flat, well-specified keyword DSL
(`CGRID`, `INPGRID`/`READINP`, `GEN3`, `BOUNDSPEC`/`BOUNDNEST3`, `BLOCK`/`TABLE`,
`COMPUTE`) the model can template from a prompt exactly the way it composes a
GeoClaw setrun or an OpenQuake job_ini - arguably EASIER than the live
SFINCS+SnapWave quadtree deck (which had to be hand-authored from the SFINCS
Fortran source contract because hydromt-sfincs ships no quadtree/snapwave author).

This is the engine for the literal request behind the Mexico Beach / Hurricane
Michael North Star: NATE wants to see the INCOMING WAVES coming onshore with
defensible nearshore wave heights, periods, and direction - not just a wave-setup
contribution folded silently into the inundation. SWAN is the canonical
open-source model for exactly that, and the SWAN+ADCIRC pairing is the production
FEMA/USACE coastal-hazard workflow for the entire US Gulf and Atlantic coast (the
same coast Mexico Beach sits on).

The caveats - none fatal, all manageable, and each is the REASON this is
GO_WITH_CAVEATS rather than a clean GO:

1. **Use-case OVERLAP with the already-shipped SnapWave path.** SFINCS+SnapWave
   already does fast offshore-to-nearshore wave transformation for compound-flood
   setup, IN-MODEL, at near-SFINCS cost (one combined Batch solve). SWAN must
   justify itself on what SnapWave CANNOT do - full 2D directional spectra,
   nonstationary wind-sea growth, swell partitioning, engineering-grade wave
   heights/periods for overtopping inputs or buoy validation - and NOT duplicate
   the fast coupled path. This is the crux and it is decided in section 3: SWAN is
   ADDITIVE higher fidelity, not redundant, IF scoped to the wave-climate /
   defensible-wave-field lane and NOT pitched as a cheaper compound-flood solver.

2. **SFINCS does NOT ingest SWAN output directly.** There is NO `wave` member in
   the surge-forcing seam today and no SWAN->SFINCS conversion step. Coupling is a
   NEW one-way offline step: run SWAN -> take Hs/Tp/dir or the radiation-stress
   tensor (Sxx/Sxy/Syy) -> convert the radiation-stress gradient into a wave-setup
   water-level offset (or a gridded body force) -> inject it through the SAME
   forcing seam (section 4). SWAN can also stand fully ALONE as a wave-field engine
   (its own Hs/Tp/dir COG layers) with no SFINCS coupling at all - the v0.1
   recommendation.

3. **The offshore wave boundary is a partial data gap.** SWAN needs an offshore
   boundary - parametric (Hs, Tp, mean dir, spread) or full 2D spectra. We already
   fetch ERA5 significant wave height AND ERA5 10 m winds (bed + wind are free,
   reused from `fetch_topobathy` + `fetch_era5_reanalysis`), but we are MISSING the
   period + direction (+ spread) that complete the parametric boundary, and we have
   no WW3/ERA5-2D-spectra fetcher for true spectral boundaries. The cheap fix is a
   small extension of the existing ERA5 fetcher (section 5); true nested spectra
   are a later, larger fetcher.

4. **Fortran build hardening + honesty gate in the worker.** The container needs a
   gfortran/OpenMP/NetCDF-Fortran toolchain (heavier than a pure-pip worker, same
   class as a SFINCS/GeoClaw image) and an entrypoint that classifies SWAN's
   nonfatal `Errfile`/`PRINT`-file warnings versus real failures so `completion.json`
   status is honest (mirroring the MODFLOW list-file convergence guard / SWMM
   continuity-error gate). Spot reclaim of a long nonstationary run wants HOTFILE
   checkpoint/restart wired into the entrypoint + cancel chain.

If those four are accepted, SWAN is the highest-value open wave engine to add for
the coastal North Star and the natural front-end for defensible nearshore wave
fields. It is A-tier on the cloud/AI-drivability ranking. The recommended
sequencing is the ordered job list in section 8.

---

## 1. What SWAN is, why it matters for Mexico Beach / Hurricane Michael

SWAN (Simulating WAves Nearshore) is a third-generation, phase-AVERAGED SPECTRAL
wind-wave model from TU Delft, in use at 1000+ institutes worldwide and the
canonical open-source nearshore wave model. Its governing equation is the spectral
ACTION BALANCE equation, which evolves the 2D wave-action density spectrum
N(x,y,sigma,theta) over geographic space (x,y), relative frequency (sigma), and
direction (theta), with source/sink terms for generation, dissipation, and
nonlinear redistribution; action density (not energy density) is conserved in the
presence of ambient currents. SWAN was explicitly built as a SHALLOW-WATER
extension of the deep-water third-generation models (WAM / WAVEWATCH III), adding
the nearshore physics: depth-induced breaking, triad interactions, bottom friction.

Capabilities (from the official features page):
- Wave propagation, shoaling, and depth/current REFRACTION.
- Wave GENERATION by wind (deep-water 3rd-gen source terms).
- WHITECAPPING dissipation (deep water); BOTTOM FRICTION and DEPTH-INDUCED
  BREAKING (shallow water).
- QUADRUPLET (four-wave) interactions in deep water + TRIAD (three-wave)
  interactions in shallow water - both supported.
- Wave-induced SET-UP (radiation-stress gradients; SWAN computes setup on its own
  grid, but full 2D circulation coupling is normally driven by an external
  hydrodynamic model).
- DIFFRACTION (phase-decoupled approximation), plus transmission through and
  reflection against obstacles (breakwaters etc.).
- Frequency shifting due to currents and time-varying depth.
- Extra dissipation: aquatic vegetation, turbulent flow, viscous fluid mud, ice.
- GRID FLEXIBILITY: regular (rectilinear), curvilinear, AND unstructured
  triangular mesh; nesting; laboratory flume up to GLOBAL (spherical coords).
- STATIONARY (default, short wave-residence-time domains) and NONSTATIONARY modes;
  Cartesian or spherical coordinates; user-defined spectral grid (>=3 directional
  bins per quadrant, >=4 frequencies).

Why it matters for the North Star: NATE's coastal North Star replicates the
Deltares Hurricane-Michael / Mexico-Beach demo, and the explicit ask is the
"incoming waves onshore" - a defensible nearshore WAVE FIELD (heights, periods,
direction), not only the inundation envelope. SnapWave gives us a fast wave-SETUP
contribution folded inside the surge solve, but it does NOT give an
engineering-grade, validatable wave climate. SWAN does. The SWAN+ADCIRC pairing is
the production FEMA coastal flood-hazard + USACE storm-surge workflow across the
ENTIRE US Gulf and Atlantic coast (validated to typical errors <0.3 m on
production meshes exceeding 7.5M nodes) - i.e. the established real pipeline for
exactly this coastline. Adding SWAN lets GRACE-2 SHOW the waves coming in, with
numbers a coastal engineer would accept, which is the differentiated "wow" the
North Star demo is reaching for.

Headless gate (PASS):
- One non-interactive command file: a single ASCII `.swn` (copied to the file
  literally named `INPUT`), a sequence of keyword commands. `swan.edt` ships the
  full command set as an editing template.
- Run from one line: `chmod +rx ./swanrun` then `./swanrun -input <casename>
  [-omp n | -mpi n]` (casename = command file minus the `.swn`); e.g.
  `./swanrun -input f31har01 -omp 4 > swanout &` runs 4 OpenMP threads, redirected
  log, auto-sets `OMP_NUM_THREADS`. No GUI, no display, no prompt.
- Pure Fortran, GPL, no Windows DLL, no dongle, no license server - clears the
  half of the gate that gated TUFLOW.

LLM-cloud-drivable gate (PASS): every input is a templatable ASCII file
(section 6). The one genuinely hard-for-the-LLM piece is the 2D boundary SPECTRUM
file (not free text), which is a data-staging step, not a deck-text step.

---

## 2. Licensing: FREE GPL, the opposite of TUFLOW (no blocker)

SWAN is FREE and open-source under the GNU GPL (the license page: redistributable
and modifiable "under the terms of the GNU General Public License ... either
version 3 of the License, or (at your option) any later version," distributed
"WITHOUT ANY WARRANTY," Copyright (c) 1993-2024 TU Delft). NO gate: no
registration, no fee, no per-seat key - just download from SourceForge
(swanmodel.sourceforge.io) or the TU Delft mirror. The only obligation is GPL
attribution / retaining copyright notices and citing the software origin.

This is the SHARP CONTRAST to TUFLOW, whose WIBU CodeMeter node-locked / Network
licensing was the structural reason for its NO_GO (an always-on license daemon,
no Spot/ephemeral consumption, no multi-tenant resale). SWAN has NONE of that: GPL,
no licensing-server dependency, fully container-friendly and redistributable, so
there is NO licensing blocker to running it on AWS Batch.

GPL handling (same posture as the TELEMAC/cht_sfincs GPL isolation): SWAN MUST
stay arms-length in a dedicated Batch worker image and NEVER enter the agent venv.
The agent only composes a JSON build_spec and submits over the Batch + S3 seam; it
never imports a SWAN symbol. (Caution for the implementer: 'SWAN(TM)' /
swan-soft.com is an UNRELATED commercial product, NOT this wave model - do not
confuse them, and do not pull a dependency from there.)

---

## 3. SWAN vs the SnapWave we already have (the crux: additive, NOT redundant)

This is the decision that determines whether SWAN earns its place. SnapWave is
what we run TODAY: Deltares' FAST, simplified directional wave solver coupled
INSIDE SFINCS on the quadtree path. It is not even agent code - the agent only
emits a `snapwave` params block into the deck-build spec
(`model_flood_scenario.py` snapwave block: Herbers infragravity path
`use_herbers=1`, `gamma=0.8`, `gammaig=1.0`, `dtheta=15`, `fw0=0.01`, `igwaves=1`),
and the cht_sfincs deck-builder WORKER authors the `snapwave.bnd/bhs/btp/bwd/bds`
files and solves them in-line with the surge run (single Batch submit,
`run_sfincs_quadtree`). SnapWave trades full spectral physics for STATIONARY,
phase-averaged, ray/energy-balance wave propagation that runs at near-SFINCS cost
(one combined solve) and yields `hm0`/`hm0ig` fields (`postprocess_waves.py`).

SWAN is the FULL third-generation SPECTRAL model: a 2D frequency-direction
spectrum, wind input, whitecapping, quadruplet + triad nonlinear interactions,
depth-induced breaking, bottom friction - far higher fidelity for nearshore
transformation, but a SEPARATE, slower (minutes-to-hours for a real grid),
well-established engine that runs OUTSIDE SFINCS.

| Dimension              | SnapWave (today, in SFINCS)      | SWAN (proposed, standalone)         |
|------------------------|----------------------------------|-------------------------------------|
| Physics                | stationary phase-avg ray/energy  | full 3rd-gen 2D spectrum            |
| Wind-sea growth        | no (boundary-derived)            | yes (GEN3 wind input)               |
| Swell partitioning     | no                               | yes                                 |
| Nonstationary growth   | no (stationary)                  | yes (COMPUTE NONSTAT)               |
| Outputs                | hm0 / hm0ig setup contribution   | Hs, Tp/Tm01/Tm02, dir, spread, Sxx  |
| Coupling to SFINCS     | IN-MODEL (one solve)             | external one-way (new step)         |
| Cost                   | near-SFINCS (~tens of seconds)   | minutes (stationary) to hours       |
| Best for               | fast compound-flood setup demo   | defensible nearshore wave climate   |

When to use which:
- SnapWave for the FAST coupled compound-flood / run-up North-Star demo where wave
  SETUP is a contributor and we want one combined cheap solve. It WINS DECISIVELY
  on cost/speed for the wave-setup-into-inundation use case.
- SWAN when the user needs DEFENSIBLE nearshore wave heights / periods / spectra
  (engineering-grade wave climate, overtopping inputs, the literal "show the
  incoming waves" North-Star ask, or validation against buoys) where SnapWave's
  simplifications are not enough. It WINS on physics fidelity (full spectra,
  nonstationary growth, wind-sea + swell, nesting).

Verdict on the crux: **additive_higher_fidelity, NOT redundant** - provided SWAN
is SCOPED to the wave-climate / defensible-wave-field lane and pitched in its tool
description that way (so the LLM routes SnapWave for fast compound-flood setup and
SWAN for the defensible wave field), and NOT sold as a cheaper compound-flood
solver. SWAN is higher fidelity at materially higher cost and added orchestration
(a second solve + a coupling step SnapWave does not need); that cost is the price
of the fidelity, and it is justified ONLY on the cases SnapWave cannot serve.

---

## 4. The SFINCS coupling path (one-way, new step - or stand alone)

SFINCS does NOT ingest SWAN output directly; coupling needs a NEW conversion step
that does not exist today. Two stances, v0.1 takes the first:

**Stance A (v0.1, RECOMMENDED): SWAN standalone wave-field engine.** SWAN runs on
its own, produces Hs/Tp/dir COG layers over the AOI, and the map paints the
incoming wave field directly. No SFINCS coupling, no new forcing member - this is
the literal "show the waves coming onshore" deliverable and the lowest-risk first
landing. It reuses the same bed DEM the SFINCS/SnapWave quadtree consumes and the
ERA5 wind we already fetch, so it is almost entirely additive plumbing.

**Stance B (later): one-way SWAN -> setup -> SFINCS.** To drive SFINCS FROM SWAN,
couple via wave SETUP / radiation-stress gradient. Today the only wave path is the
IN-MODEL SnapWave coupling (the agent never materializes a wave boundary;
SnapWave's boundary is derived from the same surge water-level forcing via the
Herbers parameterization). The surge-forcing seam
(`workflows/sfincs_forcing_adapter.py` `build_surge_forcing`) only materializes
waterlevel (bzs), discharge (dis), wind and pressure members into `surge_forcing`
- there is NO `wave` member; and the deck-build spec's forcing block in
`model_flood_scenario.py` carries `surge_forcing` straight to the worker which
writes bzs/dis + the snapwave.* files. To couple from SWAN you would:

1. Run SWAN; take its output (Hs/Tp/mean-dir, or the Sxx/Sxy/Syy radiation-stress
   tensor).
2. Convert the radiation-stress GRADIENT into either a static water-level OFFSET
   (wave setup added to the bzs boundary) or a spatially-varying body force.
3. Inject it through the SAME forcing seam: a NEW
   `wave_setup_forcing_from_swan(...)` member in `sfincs_forcing_adapter.py`
   emitting either an offset on the existing `WaterlevelForcing` (the `offset`
   field ALREADY exists - `waterlevel_forcing_from_fgb` accepts `offset` and
   writes it into the forcing dict) or a new gridded forcing the worker writes.

So: SWAN drives SFINCS via a COUPLING STEP (offline one-way SWAN -> setup ->
SFINCS), NOT a direct ingest. The seam to extend is
`sfincs_forcing_adapter.build_surge_forcing` + the build_spec forcing block in
`model_flood_scenario.py`, owned downstream by the cht_sfincs worker. Stance B is
a follow-up job, not a v0.1 requirement.

---

## 5. Data dependencies (what we have vs. new fetchers; the ERA5/CDS tie-in)

ALREADY HAVE (free reuse, no new fetch):
- **Bathymetry / bed.** `fetch_topobathy.py` merges NOAA NCEI CUDEM 1/9 arc-sec
  topobathy + USGS 3DEP land into a continuous NAVD88 positive-up DEM on a UTM
  grid - exactly the bed SWAN needs for depth-induced shoaling / breaking, and the
  SAME DEM the SFINCS/SnapWave quadtree consumes, so SWAN reuses it with NO new
  fetch (it feeds SWAN's `INPGRID BOTTOM` / `READINP BOTTOM`).
- **Wind field.** ERA5 10 m u/v + derived `10m_wind_speed`
  (`fetch_era5_reanalysis.py`) gives SWAN's wind input (`INPGRID WIND` /
  `READINP WIND`, with `GEN3` enabled).
- **Partial wave boundary.** ERA5 significant wave height
  (`significant_height_of_combined_wind_waves_and_swell`, units m) is ALREADY
  fetched via the CDS path - Hs is present.

NEW DATA NEEDED:
- **(a) The rest of the parametric boundary: period + direction (+ spread).** We
  have Hs but NOT the Tp / mean-direction / spread triple, and NOT a 2D spectrum.
  CHEAP FIX: extend `fetch_era5_reanalysis` to add the ERA5 `mean_wave_period` and
  `mean_wave_direction` CDS variables - the SAME CDS retrieve path, just add to
  `_CDS_VARIABLES` (the fetcher already carries Hs there; period+direction are two
  more entries on the identical code path, plus a `_UNITS` line each). With
  Hs + Tp + mean-dir we have a usable parametric `BOUNDSPEC` (JONSWAP/PM) along the
  offshore segment. This is the LLM-easy boundary path.
- **(b) Optional: true 2D spectra (higher fidelity boundary).** For nested
  spectral boundaries SWAN reads `BOUNDNEST3` from WAVEWATCH III (also
  `BOUNDNEST2` from WAM). We have NO WW3 fetcher and NO ERA5-2D-spectra (CDS
  spectral 2D wave product) fetcher - a NEW, larger data_fetch dependency
  (a wavespectra/dnora-style staging tool against a reliable public spectral
  source). Defer to a later job; parametric covers v0.1.
- **(c) The SWAN computational grid spec** is DERIVED from the existing topobathy +
  AOI like the SFINCS grid - not a new dependency.

Net: bed + wind are FREE (reuse `fetch_topobathy` + `fetch_era5_reanalysis`); the
genuine new dependency for v0.1 is the cheap ERA5 period+direction extension; true
2D spectra are an optional later fetcher.

---

## 6. The `.swn` command-file build_spec templating (the heart of the engine)

A SWAN run is driven by ONE ASCII command file (the `.swn`, copied to `INPUT`) - a
sequence of keyword commands, plus separate ASCII/binary input arrays for the
grids. This is MORE templatable than the live SFINCS+SnapWave quadtree deck (which
had to be hand-authored from the SFINCS Fortran source contract because
hydromt-sfincs 1.2.2 ships no `setup_snapwave` and no quadtree author). The deck
author renders the command file deterministically from the build_spec (the
GeoClaw `setrun_builder.py` / OpenQuake `job_ini.py` analogue). Canonical keyword
blocks for a v0.1 nearshore wave-field run (all confirmed from the official user
manual):

- `PROJECT` / `SET` - project + run constants.
- `MODE STATIONARY | NONSTATIONARY` + `COORD CARTESIAN | SPHERICAL` - run mode +
  coordinates. v0.1 stationary for a storm-peak wave field; nonstationary for a
  time-series hurricane wave evolution.
- `CGRID ... CIRCLE <ndir> <flow> <fhigh> <nfreq>` + `READGRID` - the computational
  spatial + spectral grid (x,y, freq, theta; >=3 dir bins/quadrant, >=4 freqs).
- `INPGRID BOTTOM ...` + `READINP BOTTOM 1. 'bottom.dat' ...` - the bed (from the
  existing `fetch_topobathy` DEM, sampled onto the SWAN input grid).
- `INPGRID WIND ...` + `READINP WIND ...` - the wind field (from ERA5 10 m u/v).
- (optional) `INPGRID WLEVEL ...` / `INPGRID CURRENT ...` - water level / currents
  if coupling to a surge field later.
- `GEN3` + `BREAKING` + `FRICTION` (+ `TRIAD`) - physics toggles (3rd-gen wind
  generation + whitecapping, depth-induced breaking, bottom friction, triads).
  Friction reuses the existing NLCD/seabed substrate posture where applicable.
- `BOUND SHAPE JONSWAP` + `BOUNDSPEC SIDE <side> CONSTANT|VARIABLE PAR <hs> <per>
  <dir> <dd>` - the PARAMETRIC offshore boundary (Hs, period, peak dir, spread)
  from the ERA5-derived triple (section 5a); OR a `TPAR` file (ASCII rows of ISO
  time, Hs, period, peak dir, spread) for time-varying 1-point boundaries; OR
  `BOUNDNEST3 WW3` for true nested spectra (section 5b).
- `BLOCK 'COMPGRID' ... HSIGN TPS PDIR DSPR SETUP ...` (gridded fields) and/or
  `TABLE` (point time series) and/or `SPECOUT` (1D/2D spectra) - the OUTPUT. The
  fields we rasterize: `HSIGN` (Hs), `RTP`/`TPS`/`PER`/`TM01`/`TM02` (periods),
  `DIR`/`PDIR` (directions), `DSPR` (spreading), `SETUP` (wave setup).
- `COMPUTE STATIONARY` or `COMPUTE NONSTAT [tbegin] [dt] [tend]` - runs it.

OUTPUT formats: ASCII, Matlab `.mat`, NetCDF `.nc`, and `.vtk` - so results drop
straight into the COG/vector pipeline. SWAN also writes a `PRINT` file with
iteration/convergence/timing diagnostics (the honesty-gate input, section 7).

build_spec (a `SwanBuildSpec` pydantic model, the `GeoClawBuildSpec` /
`SfincsQuadtreeBuildSpec` analogue) carries: `bbox`, `mode`
(`stationary`/`nonstationary`), spectral grid (ndir, nfreq, flow, fhigh),
`sim_duration_s` + `time_step_s` (nonstationary), physics toggles + friction,
boundary spec (parametric Hs/Tp/dir/spread OR a spectra URI), wind grid URI, bed
DEM URI, and the requested output quantities. The worker maps it onto the command
file + input arrays. As with GeoClaw, the build_spec author is DETERMINISTIC and
SWAN-FREE (testable with no solver installed); only the worker's run step touches
the GPL binary. A maintained pydantic wrapper exists (rompy `SwanConfig`/`SwanGrid`
+ `wavespectra` for the boundary forcing) the deckbuilder MAY drive, mirroring the
`setrun_builder.py` / `job_ini.py` pattern - but a plain keyword template is
sufficient for the regular/curvilinear-grid case.

---

## 7. SWAN output -> publish_layer postprocess

The output `BLOCK` (NetCDF `.nc` recommended) carries the gridded wave fields
(`HSIGN`, `TPS`/period, `PDIR`/dir, `DSPR`, `SETUP`) per output frame. The
postprocess is a near-clone of `postprocess_waves.py` / `postprocess_geoclaw.py`
- the SnapWave postprocess already rasterizes `hm0`/`hm0ig` to COGs, and SWAN's
field set is the higher-fidelity superset:

1. Read the SWAN NetCDF output IN THE WORKER (or with a slim reader; if NetCDF is
   used the field arrays are already regular-grid, so no unstructured rasterization
   is needed for the regular-grid path - simpler than the TELEMAC SELAFIN case).
   For the unstructured-mesh path, barycentric-interpolate nodal fields onto a
   regular grid (the same primitive TELEMAC needs).
2. Select the PEAK frame (max Hs) for a stationary or hurricane-peak run; write the
   PEAK + up to `MAX_*_FRAMES` per-frame COGs for Hs (and optionally Tp / dir),
   upload to `runs/<run_id>/`.
3. Return the SAME `(layers, metrics)` shape `postprocess_waves` /
   `postprocess_geoclaw` return: `layers[0]` peak `SwanWaveLayerURI` +
   `layers[1:]` per-frame; metrics `max_hs_m`, peak period, mean direction. REUSE
   a wave-height style preset (Hs is the same physical quantity SnapWave's `hm0`
   emits) - no new web contract; the scrubber + LayerPanel + legend consume it
   unchanged (the Phase-1 temporal-frame path).

Honesty floor (render-chokepoint + honesty norm): a SWAN envelope that produced an
empty / all-zero wave field NEVER reads `status=ok`. The worker entrypoint
classifies SWAN's `Errfile` / `PRINT`-file warnings (nonfatal iteration warnings
vs real failures) and the postprocess raises a typed error (e.g.
`SWAN_OUTPUT_EMPTY` or a non-convergence gate read from the PRINT file, mirroring
SWMM's continuity-error gate and MODFLOW's list-file convergence guard) rather than
publishing a silently-wrong layer. Every narrated number comes from the typed
`SwanWaveLayerURI` scalars (Invariant 1), never free-generated.

---

## 8. Cloud / Batch + AI drivability + tier

**Cloud/Batch (A-tier, same envelope as SFINCS/MODFLOW/GeoClaw).** SWAN is
Fortran-90, builds headless on Linux with a one-line `make ser|omp|mpi`
(`make config` first - needs Perl - then the target), producing `swan.exe` +
`swanrun`. Deltares and the community ship working Docker images (an acrosby
SWAN-MPI container; the COAWST/SWAN tree). Critically it offers a SHARED-MEMORY
OpenMP build that maps 1:1 onto GRACE-2's existing single-node Batch sizing ladder
(`AWS_BATCH_COMPUTE_CLASS_SIZING` -> `OMP_NUM_THREADS`): NO multinode/MPI is
needed for the regional AOIs GRACE-2 targets, which matters because
`SOLVER_BATCH_JOBDEF_REGISTRY` has NO MPI/numNodes/nodeProperties path today. SWAN
slots into the existing `run_solver -> submit_job(containerOverrides:
command/env OMP_NUM_THREADS, resourceRequirements VCPU/MEMORY) -> entrypoint writes
completion.json -> S3-poll` envelope used verbatim by SFINCS/MODFLOW/GeoClaw.
Runtime/Spot fit: stationary mode is seconds-to-minutes; nonstationary
(hurricane time-series) runs longer but bounded, and SWAN supports HOTFILE
restart so a Spot reclaim is recoverable. Only friction is the Fortran toolchain in
the image and choosing OMP-only to avoid the multinode gap.

The AWS Batch wiring is already GENERIC: `_resolve_batch_job_def` (`solver.py`)
resolves `GRACE2_AWS_BATCH_JOB_DEF_SWAN` (per-solver env, uppercased) ->
`SOLVER_BATCH_JOBDEF_REGISTRY['swan']` -> the generic `GRACE2_AWS_BATCH_JOB_DEF`
fallback, staying INERT (honest typed error) until NATE flips the env after
`tofu apply` registers the job-def - exactly the SWMM/SFINCS-quadtree posture.
`_run_solver_aws_batch` submits the SWAN container to that job-def over the same
submit_job/describe_jobs/S3-completion seam.

**AI drivability (EASIER-or-equal to a SFINCS deck).** SWAN's INPUT is a single
documented keyword command file - a flat, well-specified, LLM-friendly DSL the
model can template from a prompt the same way it composes solver decks. It is
actually MORE templatable than the live SFINCS+SnapWave quadtree path, where the
quadtree netcdf + snapwave_* keywords had to be HAND-AUTHORED from the SFINCS
Fortran source contract (no `setup_snapwave`, no quadtree author in
hydromt-sfincs 1.2.2). A maintained pydantic wrapper (rompy + wavespectra) can
drive it if desired. The one genuinely hard-for-the-LLM piece is the 2D boundary
SPECTRUM file - a data-staging step, not a deck-text step; parametric `BOUNDSPEC`
is LLM-easy.

**Cost / scale.** More expensive per cell than SnapWave (a full 3rd-gen spectral
solve over geographic x freq x direction bins vs SnapWave's stationary
phase-averaged energy solve - the literature reports nearshore transformation in
~90s for SnapWave where comparable spectral/phase-resolving runs take
minutes-to-hours), but cheap on Spot for the AOIs GRACE-2 runs: a stationary
regional SWAN run is minutes on an 8-16 vCPU Spot box; nonstationary hurricane
hindcasts scale with the time series but stay in the standard/large compute_class
buckets. UNSTRUCTURED triangular-mesh mode keeps cost feasible by concentrating
resolution nearshore and coarsening offshore. No GPU needed (the gpu sizing bucket
is irrelevant). Spot fit is good given HOTFILE restart.

**Tier: A.** Container/CLI + a pyww3/rompy wrapper, headless GPL Fortran, OMP-only
single-node Batch fit, LLM-templatable keyword DSL - the canonical
engine-cloud-drivability ranking already lists SWAN as A-tier. The one B-tier item
(tight ADCIRC+SWAN MPI/multinode coupling, the model's headline storm-surge use)
is EXPLICITLY out of scope; keep SWAN STANDALONE (one-way nested forcing) to stay
A-tier.

---

## 9. Ordered minimal-integration job list (IF GO)

Mirrors the SFINCS / GeoClaw / SWMM landing sequence. Each job is single-owner,
frozen kickoff, live-evidence-gated. Schema-first, then data, then worker, then
infra, then agent-chain, then acceptance.

1. **schema (job S1): SWAN contracts.** Add
   `packages/contracts/.../swan_contracts.py`: `SwanBuildSpec`, `SwanRunArgs`
   (bbox, mode literal stationary/nonstationary, spectral grid ndir/nfreq/flow/
   fhigh, sim_duration_s, time_step_s, physics toggles + friction, boundary spec
   [parametric Hs/Tp/dir/spread OR spectra URI], wind/bed URIs, output quantities),
   and `SwanWaveLayerURI(LayerURI)` carrying `max_hs_m`/peak-period/mean-direction.
   Reuse `BBox` + a wave-height style preset. Template off `geoclaw_contracts.py`.
   (No solver dep; pure pydantic + unit tests.)

2. **engine (job S2): ERA5 wave-boundary fetcher extension.** Extend
   `fetch_era5_reanalysis.py` to add the `mean_wave_period` + `mean_wave_direction`
   CDS variables (add to `_CDS_VARIABLES` + the `_UNITS` map, identical code path
   to the existing Hs variable). This completes the parametric offshore boundary
   (Hs + Tp + mean-dir). Unit-test the new variable path against the existing Hs
   test. (Cheap; the section-5a fix; unblocks a real boundary.)

3. **engine (job S3): deterministic deck author (SWAN-free, fully unit-tested).**
   `services/workers/swan/deck_builder.py`: render the `.swn` command file from the
   build_spec (the keyword template, section 6), sample the `fetch_topobathy` DEM
   onto the SWAN bottom input grid, write the wind input grid from ERA5 u/v, and
   author the `BOUNDSPEC` parametric boundary (or stage a TPAR/spectra file).
   Deterministic, no GPL import at author time. This is the `setrun_builder.py`
   analogue - test it hard with a synthetic AOI (mirror `test_setrun_builder.py`).

4. **infra (job S4): worker image + Batch job-def + ECR.** Author
   `services/workers/swan/Dockerfile` (Debian/Ubuntu base + gfortran/libgomp +
   optional NetCDF-Fortran; `make config` + `make omp`; build-time tiny-grid run
   smoke; container-hygiene inspect) + `entrypoint.py` (a GeoClaw/SFINCS entrypoint
   clone: stage inputs, copy `.swn` -> `INPUT`, run `./swanrun -input <case>
   -omp $OMP_NUM_THREADS`, glob `*.nc` outputs, ALWAYS write `completion.json`,
   classify Errfile/PRINT warnings vs failures, cancel via docker kill / os.killpg,
   optional HOTFILE restart). Build OFF-box via `grace2-worker-builder` CodeBuild.
   Add the `grace2-swan` Batch job-def + compute-env wiring (Spot, scale-to-zero).
   Provide the `GRACE2_AWS_BATCH_JOB_DEF_SWAN` env. (Infra delta = NEW container
   image + ONE job-def + ONE env flip; no `solver.py` transport change.)

5. **engine (job S5): SWAN postprocess + workflow.**
   `workflows/postprocess_swan.py` (read the SWAN NetCDF output, select peak Hs ->
   peak + per-frame Hs [and optional Tp/dir] COGs, metrics, publish via the shared
   wave-height style preset; honesty gate from the PRINT file) +
   `workflows/model_*_swan_scenario.py` (the fetch -> stage build_spec -> Batch-
   solve -> postprocess chain, the `model_dambreak_geoclaw_scenario` /
   `model_flood_scenario` analogue) + `workflows/run_swan.py` that registers
   `'swan'` in `SOLVER_WORKFLOW_REGISTRY` exactly like `register_geoclaw_solver`
   (`SOLVER_WORKFLOW_REGISTRY.setdefault`).

6. **agent (job S6): `run_swan_waves` atomic tool.** The `run_geoclaw_inundation`
   analogue: validate/coerce args into `SwanRunArgs`, dispatch the workflow, return
   `SwanWaveLayerURI` or a typed error dict. `cacheable=False` /
   `ttl_class="live-no-cache"` / `source_class="workflow_dispatch"` /
   `read_only_hint=False`; behind the confirmation hook (Invariant 9, a solver
   run). Add a tool-catalog entry with the section-3 routing guidance (SWAN =
   defensible nearshore wave field; SnapWave/SFINCS = fast compound-flood setup).

7. **testing (job S7): live acceptance.** Drive a real Mexico Beach / Hurricane
   Michael wave-field case end-to-end on Batch (the North-Star coastline), prove
   the SWAN NetCDF -> Hs-COG -> map render + scrubber showing the incoming wave
   field, capture the run wall-clock at a known grid size, and emit the
   `READINESS_RESULT swan PASS run_id=... layers=N metric=max_hs_m=...` one-liner
   (the acceptance-driver parity norm).

8. **engine (job S8, later): SWAN -> SFINCS one-way setup coupling.** The Stance-B
   follow-up (section 4): a `wave_setup_forcing_from_swan(...)` member in
   `sfincs_forcing_adapter.py` that converts SWAN radiation-stress / setup into a
   bzs offset (reusing the existing `WaterlevelForcing` `offset` field) or a
   gridded forcing, plus the build_spec forcing-block wiring. NOT a v0.1
   requirement; lands after standalone SWAN is proven.

9. **engine (job S9, later): true 2D-spectra boundary fetcher.** A WW3 / ERA5-2D-
   spectra (`BOUNDNEST3`) staging tool (wavespectra/dnora-style) for nested
   spectral boundaries, replacing the parametric `BOUNDSPEC` for high-fidelity
   cases. Deferred; parametric covers the North-Star demo.

10. **schema (job S10, user-landed): SRS appendix amendment.** Propose the
    Appendix-B engine-table + Appendix-D tool-registry amendment for SWAN
    (specialist proposes via report; only NATE lands into `docs/srs/*` then
    `make srs`). Not a code job.

Critical path: S1 -> {S2, S3 in parallel} -> S4 -> S5 -> S6 -> S7. S8/S9/S10 trail.

---

## 10. Risks (carried forward into the jobs)

1. **Use-case overlap with SnapWave** - the central risk; resolved by scoping SWAN
   to the defensible-wave-field lane and routing it in the tool description so it
   does not duplicate the fast coupled compound-flood path (section 3).
2. **Boundary 2D-spectra acquisition** - the same offshore-forcing gate SnapWave
   has. Parametric `BOUNDSPEC` from ERA5 Hs/Tp/dir is LLM-easy (S2); true nested
   `BOUNDNEST3` 2D spectra need a new wavespectra/dnora staging tool + a reliable
   public spectral source (S9, deferred).
3. **Unstructured triangular-mesh generation** is a NEW mesh-tooling dependency
   (Triangle/OceanMesh2D/Gmsh) GRACE-2 does not have today; the regular/curvilinear
   grid path avoids it (and is what v0.1 uses) but is coarser/costlier. This
   mirrors the TELEMAC #174 / adaptive-mesh-budget concern - GENERALIZE mesh tooling
   with TELEMAC rather than one-off it here.
4. **Tight ADCIRC+SWAN coupling** (the model's headline storm-surge use) is
   MPI/multinode and B-tier - EXPLICITLY out of scope for the single-node OpenMP
   Batch envelope; keep SWAN standalone (one-way nested forcing) to stay A-tier.
5. **Fortran build hardening** in the container image (compiler, NetCDF, OMP flags)
   plus an entrypoint that classifies SWAN's nonfatal Errfile/PRINT warnings vs
   real failures (analogous to the MODFLOW list-file convergence guard) so
   `completion.json` status is honest.
6. **Spot reclaim of a long nonstationary run** wastes compute unless HOTFILE
   checkpoint/restart is wired into the entrypoint + cancel chain.

---

## 11. Real pipelines (grounding - this is how practitioners run it)

- **SWAN+ADCIRC (tightly/dynamically coupled):** the canonical FEMA coastal
  flood-hazard + USACE storm-surge workflow across the US Gulf and Atlantic coast.
  SWAN runs on the SAME unstructured ADCIRC mesh; ADCIRC passes water levels/
  currents to SWAN and SWAN passes radiation-stress gradients back, so wave setup
  is computed physics-consistently INSIDE the surge simulation. Validated to
  typical errors <0.3 m; production meshes exceed 7.5M nodes. (The MPI/multinode
  variant we keep out of scope, but it is the proof SWAN is the established engine
  for this coast.)
- **Delft3D-WAVE IS SWAN under the hood** ("simulates evolution of random
  short-crested wind waves based on SWAN"), online-coupled to Delft3D-FLOW - the
  standard morphodynamics/coastal-engineering wrapper around SWAN.
- **Offshore-to-nearshore boundary cascade:** SWAN (or Delft3D-WAVE/SWAN) supplies
  offshore-to-nearshore wave conditions that force higher-resolution surf-zone
  models - XBeach (dune erosion/overtopping) and SFINCS (compound flooding). A
  common pattern builds an XBeach transect lookup-table at the SFINCS boundary,
  decoupling cost - directly relevant to GRACE-2's SFINCS coastal North Star (SWAN
  becomes the wave-forcing front-end).
- **Large-scale wave boundary nesting:** SWAN takes offshore boundary spectra from
  global/regional 3rd-gen models - `BOUNDNEST3` reads WAVEWATCH III spectra
  directly (`BOUNDNEST2` for WAM) - and is wind-forced from reanalysis/forecast
  (ERA5, HRRR/GFS) - the standard nearshore dynamical-downscaling hindcast/forecast
  chain.
- **Coupled community frameworks:** COAWST (USGS: ROMS+SWAN+sediment via the Model
  Coupling Toolkit) and BMI/ESMF-coupling forks - SWAN already has a precedent as a
  BMI-wrapped component, aligning with the multi-engine BMI abstraction in the
  engine roadmap.

---

## 12. Sources (primary)

- swanmodel.sourceforge.io - home, features (features.htm), license (license.htm),
  online docs (swantech node2 action-balance; swanuse nodes 9/11/27/32/50 for
  command file, boundary, output; swanimp nodes 7/8/12/20 for build/run), the
  swanuse.pdf / swantech.pdf manuals, swan.edt command template.
- sourceforge.net/projects/swanmodel + the TU Delft SWAN page
  (tudelft.nl ... environmental-fluid-mechanics/research/swan).
- Delft3D-WAVE User Manual (content.oss.deltares.nl) - "WAVE module is SWAN."
- SWAN+ADCIRC coupling: ICCE proceedings (icce-ojs-tamu) + ScienceDirect
  (S0029801823004286, production-mesh validation).
- COAWST/SWAN tree (code.usgs.gov/coawstmodel/COAWST/SWAN); openearth chenopis.
- Docker: acrosby SWAN-MPI container; COAWST/SWAN tree as a build reference.
- GRACE-2 live seam (cross-checked in-repo): `tools/solver.py`
  (`_resolve_batch_job_def`, `SOLVER_WORKFLOW_REGISTRY`,
  `SOLVER_BATCH_JOBDEF_REGISTRY`, the `GRACE2_AWS_BATCH_JOB_DEF_<SOLVER>` ladder);
  `workflows/model_flood_scenario.py` (the snapwave params block + forcing block);
  `workflows/sfincs_forcing_adapter.py` (`build_surge_forcing`,
  `waterlevel_forcing_from_fgb` with the `offset` field);
  `tools/fetch_era5_reanalysis.py` (`_CDS_VARIABLES` incl. Hs + the 10 m wind
  derivation); `workflows/run_geoclaw.py` (`register_geoclaw_solver`).
