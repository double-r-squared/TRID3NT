# Engine Spike: Delft3D (Deltares open coastal/estuarine modeling suite)

Research spike for evaluating the open-source Delft3D suite (Deltares, Delft NL)
as a GRACE-2 engine - the COUPLED flow + wave + morphology + water-quality suite
whose wave module IS SWAN. Grounded against primary sources (github.com/Deltares/
Delft3D, oss.deltares.nl, the Delft3D-WAVE user manual + WAVE tutorial, the
Deltares container wiki, the Inductiva.AI Delft3D guides) AND against the live
GRACE-2 solver seam plus the two sibling engine spikes that this one is decided
relative to: the SWAN spike (reports/design/engine_spike_swan.md, task #175,
GO_WITH_CAVEATS) and the TELEMAC-2D spike (reports/design/engine_spike_telemac.md).
The GeoClaw / SFINCS-quadtree / SWAN workers are the closest structural analogues
and are cited throughout as the integration template.

ASCII only. No em/en dashes, no unicode arrows; "->" for arrows. Status: design +
verdict only, no code in this doc.

---

## 0. Verdict

**GO_WITH_CAVEATS (scoped narrowly - do NOT adopt the monolith to replace
SFINCS+SWAN).**

Delft3D clears both hard gates - it is GENUINELY open-source (GPL/AGPL/LGPL family,
no commercial license server, the sharp opposite of the TUFLOW NO_GO) and it is
fully HEADLESS/scriptable/containerizable (Deltares ships Docker + Apptainer/
Singularity images; kernels are CLI exes driven by config_d_hydro.xml in v4 / DIMR
in FM). But it does NOT pass as a drop-in replacement for what we already run, and
adopting the whole suite would be redundant weight. The decision is therefore NOT
"GO vs NO_GO on Delft3D" but "WHICH SLICE of Delft3D, if any, earns its place."

The crux, decided in sections 3-4:

1. **Delft3D-WAVE = SWAN.** CONFIRMED in our own SWAN spike (engine_spike_swan.md
   lines 579 to 581): Deltares' WAVE module is a wrapper around the SWAN kernel -
   it literally translates the .mdw master file into SWAN's native .swn deck
   (test.mdw -> test.swn) and runs the SWAN engine. So for WAVE PHYSICS, Delft3D is
   REDUNDANT with the SWAN spike: same 3rd-gen spectral action-balance solver, same
   keyword deck, same Hs/Tp/dir outputs, same A-tier single-node Batch envelope. A
   standalone Delft3D-WAVE buys NOTHING over standalone SWAN and is strictly heavier
   because it ships inside the suite + GUI. Do NOT add Delft3D just for waves.

2. **Delft3D-FLOW overlaps SFINCS, and SFINCS wins for flood.** For pure flood
   inundation extent/depth, SFINCS stays our core: reduced-physics local-inertial
   solver, one cheap Batch solve, already shipped with SnapWave + the
   select_compute_class autoscaler + the COG-to-scrubber postprocess. Delft3D-FLOW
   (full 2D/3D shallow-water) is heavier and slower with NO inundation advantage
   SFINCS lacks. It is not a better flood solver; it is a DIFFERENT class -
   estuarine/coastal circulation + 3D baroclinic transport. We also already have a
   TELEMAC-2D spike as the open unstructured full-SWE engine, narrowing the
   flow-only case further.

3. **The UNIQUE value is morphology + water-quality + online flow-wave coupling.**
   The genuinely additive capabilities nothing in our stack covers are two modules:
   D-Morphology (online sediment transport + bed update: cohesive/non-cohesive
   transport, erosion/deposition, bathymetric evolution, dredging) and DELWAQ
   (water-quality/ecology: nutrients, contaminants, algae, DO, arbitrary
   constituent advection-diffusion-reaction), plus the native ONLINE two-way
   flow<->wave coupling (radiation stress back into circulation within one run) that
   SWAN-alone and SFINCS-alone cannot do. This is the cleanest home for the
   sediment-dye North Star (project_sediment_dye_transport_north_star) after the
   ranking SHELVED HEC-RAS, whose Sediment + WQ modules were the prior home.

The caveats - none fatal, all manageable, and each is the REASON this is
GO_WITH_CAVEATS rather than a clean GO:

1. **Suite overkill - scope narrowly.** Adopting the whole Delft3D suite to do what
   SFINCS+SWAN already do is redundant weight (heaviest worker class alongside
   GeoClaw). If pursued at all, scope to the UNIQUE capability (morphology/sediment
   or DELWAQ) and pick a North-Star use those engines genuinely cannot do before
   committing the XL build.

2. **Coupled-deck composition is the real cost - NOT the cloud.** A valid run needs
   a consistent multi-file deck (master .mdw/.mdf or FM .mdu + grid .grd/_net.nc +
   bathymetry .dep + boundary .bnd + forcing .bct/.bcw/.bc/.ext + an orchestration
   config_d_hydro.xml/DIMR). Hand-templating flat files the way we template a .swn
   is brittle. The MITIGATION is decisive and a HARD prerequisite: Deltares ships a
   first-class Python deck stack (hydrolib-core typed model-file readers/writers,
   dfm_tools, the HydroMT-Delft3DFM plugin - the SAME HydroMT->SFINCS pattern we
   already use) so a worker-side deck_builder MATERIALIZES the file set
   deterministically and the LLM only composes a narrow typed build_spec.

3. **Multi-node MPI + tight FLOW-WAVE coupling is OUTSIDE our single-node Batch
   seam.** Delft3D's headline coastal use is COUPLED FLOW-WAVE on MPI multi-node;
   our seam is single-node only (grep confirms 0 numNodes/nodeProperties in
   solver.py; OMP-thread sizing caps at 48 vCPU). Single-node domain-decomposed
   mpirun is achievable in-container; TRUE multi-node needs NEW Batch multi-node/EFA
   infra - the same B-tier boundary the SWAN spike drew around ADCIRC+SWAN MPI.

4. **Container is access-gated + heavy + two-variant.** The official Deltares image
   is request-gated (software@deltares.nl, MyDeltares/Harbor CLI-secret) and Docker
   is Beta-status, blocking a clean deltares/sfincs-cpu-style digest-pin; community
   images exist but need a redistribution/license review. The image is multi-GB
   (Fortran + OpenMP/MPI + NetCDF/HDF5/METIS). And there are TWO product variants
   with different decks (legacy Delft3D4 structured .grd/config_d_hydro.xml vs
   Delft3D-FM unstructured _net.nc/dflowfm) - pick ONE.

If those are accepted, the SCOPED recommendation is: do NOT replace SFINCS+SWAN
with the Delft3D monolith. If pursued, scope to the unique morphology/DELWAQ wedge
and prove the value FIRST (optionally via the managed Inductiva.AI escape hatch),
self-hosting single-node 2D on the existing Batch seam for the production lane. It
is a B-tier engine on the cloud/AI-drivability ranking. The trigger to REVISIT is
the sediment/dye North Star. The recommended sequencing is the ordered job list in
section 8.

---

## 1. What Delft3D is, and why it clears the hard gate

Delft3D is a professional, fully open-source 3D modeling suite from Deltares (Delft,
NL) for coastal, river, and estuarine environments, with >38,000 cited
developers/users and daily source access. It exists as TWO parallel suites sharing
the same physics heritage:

- **Delft3D 4 Suite** - STRUCTURED grids only (curvilinear/rectangular), now in
  MAINTENANCE mode (no major new functionality since 2011, only tools updates +
  minor bug fixes). Hydrodynamic core = Delft3D-FLOW; wave module = Delft3D-WAVE.
- **Delft3D Flexible Mesh (Delft3D FM / D-HYDRO Suite)** - the envisioned SUCCESSOR
  built on UNSTRUCTURED meshes (triangles, quads, pentagons, hexagons, curvilinear
  cells, 1D networks) with local refinement + easier grid editing. The
  hydrodynamic core is renamed D-Flow FM and the wave module is D-Waves FM.

Both suites live in ONE source tree (github.com/Deltares/Delft3D, "Repository for
Delft3D FM and Delft3D 4 kernels"). The suite is GUI-driven (preprocessing GUI +
QUICKPLOT postprocessing), but the kernels are HEADLESS executables that can be
scripted/containerized.

Modules:
- **D-Flow / Delft3D-FLOW** (D-Flow FM in the FM suite): multi-dimensional 2D/3D
  hydrodynamic engine for non-steady flow + transport from tidal + meteorological
  forcing (tides, currents, water levels).
- **D-Waves / Delft3D-WAVE**: wind-wave propagation, generation, non-linear
  wave-wave interaction + dissipation - its computational kernel IS SWAN.
- **D-Morphology (Delft3D-MOR/SED)**: sediment transport (suspended + bed/total
  load) for an arbitrary number of cohesive + non-cohesive fractions, plus
  bathymetric/morphological evolution + erosion/sedimentation.
- **D-Water Quality / Delft3D-WAQ**: water-quality + ecological modeling (nutrients,
  contaminants, algae/eutrophication) - kernel is DELWAQ; the ecology face is
  Delft3D-ECO (also DELWAQ-based).
- **Delft3D-PART**: Lagrangian particle tracking (mid-field transport, spills).
- **FM-suite additions**: D-Hydrology + D-Real Time Control (D-RTC) alongside the
  hydrodynamics/morphology/waves/water-quality engines.

Headless gate (PASS): kernels are CLI exes (d_hydro / wave in v4; dimr/dflowfm in
FM) orchestrated by a top-level config (config_d_hydro.xml in v4, DIMR config in
FM). No GUI, no display, no dongle at run time - the preprocessing GUI + QUICKPLOT
are CONVENIENCE tools we replace with a deterministic headless deck author
(section 4). Officially containerized (section 6).

LLM-cloud-drivable gate (PASS WITH MITIGATION): every input is a file, but it is a
COUPLED MULTI-FILE deck, not one self-contained ASCII deck. The mitigation
(hydrolib-core/HydroMT-Delft3DFM worker-side deck construction) is the hard
prerequisite that makes it drivable - see section 4.

---

## 2. Licensing: genuinely open GPL family, the opposite of TUFLOW (no blocker)

Delft3D is genuinely open-source with mixed per-component licensing in the GitHub
tree: "Most simulation engines are licensed under AGPL-3.0 or GPL-3.0, several
utility libraries are licensed under LGPL-2.1, and several third-party packages
under their original license." Deltares states full source is available for the
FLOW, MOR, WAVE(SWAN), WAQ/ECO(DELWAQ), and PART engines under GPLv3 conditions.
This is a REAL open license: NO commercial license gate for the source/kernels.

This is the SHARP CONTRAST to TUFLOW (a paid/closed commercial product = NO_GO for
an open self-hosted stack, gated by a node-locked license server with no
Spot/ephemeral/multi-tenant consumption). Delft3D has NONE of that - source +
prebuilt containers are freely pullable.

The only practical friction:
- The Deltares community portal historically required FREE REGISTRATION to download
  prebuilt binaries/installers, and the GUI is part of the (also open-sourced)
  Delft3D 4 GUI distribution.
- The OFFICIAL Deltares container is access-gated (request to software@deltares.nl,
  MyDeltares/Harbor CLI-secret auth) and Docker images are Beta-status - this is a
  CONTAINER-distribution gate, not a SOURCE-license gate. Mitigate by building from
  source into our ECR, evaluating community images (nerdalize 5.0.1 on quay.io,
  lsucrc/delft3d) for redistribution rights, or formally requesting access. Flag a
  redistribution/version-pin review before any ECR push (container-hygiene norm).

GPL handling (same posture as the TELEMAC / cht_sfincs / SWAN GPL isolation): the
Delft3D binary MUST stay arms-length in a dedicated Batch worker image and NEVER
enter the agent venv. The agent only composes a JSON build_spec (mdf/mdu/mor/DELWAQ
inputs) and submits over the Batch + S3 seam; it never imports a Delft3D symbol.

---

## 3. The crux: WAVE = SWAN, FLOW overlaps SFINCS, unique value is MOR + DELWAQ

This is the decision that determines whether and HOW Delft3D earns its place. It is
decided RELATIVE to the two sibling spikes (SWAN #175, TELEMAC) and the shipped
SFINCS+SnapWave path.

**(a) WAVE is REDUNDANT with the SWAN spike.** Delft3D-WAVE is SWAN under the hood -
confirmed in engine_spike_swan.md lines 579 to 581. Deltares describes the waves
module as "Delft3D-WAVE module, including the SWAN kernel," and the WAVE tutorial is
titled "Simulation of short-crested waves with SWAN." Mechanically, WAVE's .mdw
master file is translated into SWAN's native .swn command file (test.mdw ->
test.swn) and the SWAN engine runs it. What WAVE ADDS over standalone SWAN:
ONLINE two-way coupling with Delft3D-FLOW ("online coupling type 3, dynamic
interaction" exchanging data each step through a com-file so currents/water-levels
feed waves and wave radiation-stress feeds flow), a GUI + .mdw abstraction over
SWAN's terse deck, integrated grid/bathymetry handling, and suite integration into
FLOW/MOR/the run-coupler. WAVE can also run "standalone with SWAN" using its own
meteo forcing. NET: for the standalone "show the incoming waves" North-Star ask,
WAVE buys nothing over the SWAN spike and is strictly heavier. The ONLY marginal
value is the native online flow<->wave coupling - which earns its keep ONLY if we
also adopt the flow+morphology side (section 3c), not for waves alone. **Verdict:
the wave module is redundant with the SWAN spike; do not add it just for waves.**

**(b) FLOW OVERLAPS SFINCS, and SFINCS wins for flood.** For pure flood inundation
extent/depth, SFINCS WINS and stays our core: reduced-physics local-inertial
solver, one cheap Batch solve, already shipped with SnapWave + the
select_compute_class autoscaler + the COG-to-scrubber postprocess. Delft3D-FLOW
(full 2D/3D shallow-water, complete momentum balance) is heavier and slower with NO
inundation advantage SFINCS lacks. What FLOW ADDS is physics SFINCS DROPS: true 3D
baroclinic currents (sigma/Z layers), density/salinity/temperature stratification,
full tidal constituents + Coriolis, and a hydrodynamic field clean enough to drive
morphology + water-quality coupling. So Delft3D-FLOW is not a better flood solver
but a DIFFERENT class: an estuarine/coastal circulation + transport engine.
Routing: SFINCS for compound-flood inundation; Delft3D-FLOW only when 3D
currents/density/transport-grade hydrodynamics are needed. We also already have a
TELEMAC-2D spike as our open unstructured full-SWE engine, narrowing the flow-only
case further.

**(c) The UNIQUE value is MOR + DELWAQ + online coupling.** The genuinely additive
capabilities nothing in our stack covers are two modules:
1. **MORPHOLOGY / SEDIMENT TRANSPORT** (online sediment + bed update: cohesive +
   non-cohesive transport, erosion/deposition, bathymetric evolution, dredging).
2. **DELWAQ water-quality / ecology** (nutrients, contaminants, algae, DO,
   arbitrary constituent advection-diffusion-reaction).
This is the cleanest home for the sediment-dye North Star
(project_sediment_dye_transport_north_star): the Baird slide-14 plume is a
sediment-concentration / dye-tracer field down a channel. FLOW+MOR gives real
morphology (the deposit half) and DELWAQ a real reactive-constituent solve (the
chemical-traversal half) - the engineering-grade version of the lightweight
passive-tracer fallback. After the ranking SHELVED HEC-RAS (whose Sediment + WQ
modules were the prior home, see reference_engine_cloud_ai_drivability_ranking),
Delft3D FM is the strongest A-tier OPEN replacement vs HEC-RAS Windows/GUI risk. It
extends the surface-river-groundwater thread: DELWAQ hands off conceptually to the
existing MODFLOW GWF+GWT solute path (run_river_seepage_tool.py, run_modflow_tool
.py), and constituent fields feed the conservation tools (contaminant loading over
WDPA/GBIF/eBird overlays). **This morphology-plus-water-quality wedge is the only
reason to keep Delft3D on the table.**

Crux summary:

| Module / capability   | Already covered by      | Delft3D verdict                     |
|-----------------------|-------------------------|-------------------------------------|
| WAVE (spectral)       | SWAN spike (#175)       | REDUNDANT - do not add for waves    |
| FLOW (2D inundation)  | SFINCS (compound flood) | OVERLAP - SFINCS wins for flood     |
| FLOW (3D baroclinic)  | nothing                 | additive but heavy; niche route     |
| MORPHOLOGY / SEDIMENT | nothing (HEC-RAS shelved)| UNIQUE - the wedge worth pursuing  |
| DELWAQ water-quality  | nothing (HEC-RAS shelved)| UNIQUE - the wedge worth pursuing  |
| online flow<->wave    | nothing (we run offline)| additive ONLY with the MOR side     |

---

## 4. Where it slots + the deck-composition cost (the heart of the engine)

**It slots like SFINCS/GeoClaw/TELEMAC** - a dedicated heavy grace2-delft3d Batch
worker container behind the EXISTING run_solver -> wait_for_completion ->
S3-completion seam, with ZERO new transport code. `_resolve_batch_job_def`
(tools/solver.py) keys off the solver string, so `solver=delft3d` resolves
`GRACE2_AWS_BATCH_JOB_DEF_DELFT3D` -> `SOLVER_BATCH_JOBDEF_REGISTRY` -> the generic
fallback, staying INERT (honest typed error) until NATE flips the env after
`tofu apply`, identical to the SWMM/SWAN/TELEMAC posture. The agent only composes a
JSON build_spec (mdf/mor/DELWAQ inputs); the GPL Delft3D binary lives only in the
worker image (same isolation as cht_sfincs and SWAN).

**The deck composition is the real cost driver - NOT the cloud, and meaningfully
harder than a SWAN .swn or a SFINCS deck.** SWAN (A-tier) and SFINCS are ONE flat
templatable artifact (a single ASCII .swn keyword DSL / a HydroMT-built setup); the
LLM composes a JSON build_spec and a worker renders one file. Delft3D-FM is a
COUPLED MULTI-FILE deck:
- master `.mdu` (D-Flow FM) or `.mdw` (D-Waves) - or `.mdf` (Delft3D-FLOW v4);
- a net/grid (`_net.nc` FM / legacy `.grd` v4, + `.enc` enclosure);
- bathymetry (`.dep` / sample `.xyz`, often converted from XYZ);
- boundary definition `.bnd` + boundary conditions (`.bct` hydro, `.bcw` wave,
  `.bc`/`.bca` FM forcing);
- external forcing `.ext`, observation/cross-section files;
- a top-level orchestration config (`config_d_hydro.xml` in v4, DIMR config in FM)
  that runs the coupled executables;
- and for WAVE, the auto-generated SWAN `.swn` (WAVE renders it from the `.mdw`).

Hand-authoring this as flat templates the way we template a `.swn` is brittle and
NOT recommended (one grid/boundary/forcing mismatch silently mis-runs or fails).

**THE MITIGATION IS DECISIVE and a HARD prerequisite.** Deltares ships a
first-class Python deck-construction stack:
- **hydrolib-core** - typed readers/writers for every `.mdu/.ext/.bc` model file
  (incl. an MDU class).
- **dfm_tools** - pre/post + mesh tooling.
- **HydroMT-Delft3DFM** - a HydroMT plugin that builds a 2D model from a region +
  public data via CLI or Python - EXACTLY the HydroMT->SFINCS pattern we ALREADY
  use.

Recommended architecture (the GeoClawRunArgs / SFINCS-deckbuilder pattern): the LLM
composes a SMALL typed build_spec (bbox, grid res, boundary type+forcing, processes,
sim window, output cadence), and a worker-side `deck_builder.py` uses
hydrolib-core/HydroMT to MATERIALIZE the coupled file set deterministically. That
keeps the LLM surface as narrow and Invariant-1-clean as `run_geoclaw_inundation`,
with the file-set complexity QUARANTINED in the worker (never the agent venv). With
this layer it is A-grade-drivable for a CONSTRAINED 2D scenario menu; WITHOUT it
(raw flat-file templating) it is C/D-grade brittle. The templating layer is a hard
prerequisite, not an optional nicety.

WAVE then auto-generates the SWAN `.swn` from the `.mdw`, so the wave deck is a
near-superset of what the SWAN spike already templates - reinforcing that
standalone WAVE is redundant.

OUTPUT: NetCDF (`.nc`) is the modern output - the WAVE tutorial writes map output at
intervals to `.NC`, FM is NetCDF-native (map/his), and v4 legacy NEFIS `.his/.map`
files are post-processed in QUICKPLOT (Deltares' bundled viz). The postprocess is a
near-clone of `postprocess_geoclaw` / `postprocess_telemac`: read the NetCDF
result IN THE WORKER (regular-grid for D-Flow-FM map output, or barycentric-
interpolate the unstructured nodal fields onto a regular grid - the same primitive
TELEMAC needs), select the peak/last frame, write peak + per-frame COGs, and return
the SAME `(layers, metrics)` shape via the shared style preset (depth reuses
`continuous_flood_depth`; sediment concentration / constituent fields get a new
style key). Honesty floor (render-chokepoint + honesty norm): a Delft3D envelope
that produced an empty/all-zero field NEVER reads `status=ok`; the postprocess
raises a typed error (e.g. `DELFT3D_OUTPUT_EMPTY` / a mass-balance gate read from
the run diagnostics, mirroring SWMM's continuity-error gate) and every narrated
number comes from the typed LayerURI scalars (Invariant 1).

---

## 5. Scope boundary: single-node 2D in, multi-node coupled FLOW-WAVE-MOR out

**FEASIBLE for the single-node 2D case, HARD for the coupled multi-node case it is
known for.** Our Batch seam is SINGLE-NODE only (grep confirms 0
numNodes/nodeProperties in solver.py; OMP-thread sizing via
`AWS_BATCH_COMPUTE_CLASS_SIZING` caps at xlarge=48 vCPU).

- **In scope (v0.1):** a SINGLE-NODE 2D D-Flow FM run via the EXISTING generic
  Batch seam - the `_resolve_batch_job_def` + `SOLVER_BATCH_JOBDEF_REGISTRY` +
  `GRACE2_AWS_BATCH_JOB_DEF_<SOLVER>` ladder adds a new single-node engine cheaply,
  with `OMP_NUM_THREADS` driven off the compute-class bucket. Single-node
  domain-decomposed MPI (`mpirun -np N` inside ONE container, after
  `dflowfm --partition:ndomains=N`) is achievable and dodges AWS Batch multi-node
  job-def work.
- **Out of scope (B-tier slice, explicit):** TRUE multi-node MPI + the tight
  FLOW-WAVE coupling that is Delft3D's HEADLINE coastal use. Inductiva runs FM as
  `mpirun -np 16 dflowfm --autostartstop FlowFM.mdu` (after
  `dflowfm --partition:ndomains=16`), and FLOW-WAVE as a `run_sim.sh` that launches
  `d_hydro.exe & wave.exe` CONCURRENTLY (two coupled processes that must run
  simultaneously). The >7.5M-node production meshes would need NEW infra (Batch
  multi-node parallel job-defs / EFA networking) we do not have - the SAME boundary
  the SWAN spike drew around ADCIRC+SWAN MPI.

Runtime / Spot fit: minutes-to-hours per coastal case (published figures ~3h on
modest cores dropping to ~1h45m at 56 vCPU; 48-core nodes range 41 min to multi-day
for long coupled runs). Long-tail runtimes stress Spot interruption + the agent
wait_for_completion/auto-stop loop - apply the existing granularity gate (#154) to
cap mesh/duration and rely on S3-completion-manifest decoupling so a Spot reclaim or
box-sleep does not lose the run (the pattern every Batch engine uses).

Pick ONE variant for v0.1 - recommend **Delft3D-FM 2D** (the HydroMT plugin +
dfm_tools mesh story align with our existing HydroMT/SFINCS substrate and the
TELEMAC-style unstructured-mesh direction), NOT legacy Delft3D4.

---

## 6. Cloud / Batch + the managed Inductiva.AI escape hatch

**Containerization is officially supported and the natural cloud path.** Deltares
publishes BOTH Docker and Apptainer/Singularity images on a public wiki: Docker is
"recommended for Delft3D computations on a local machine, or on a single node of a
(cloud) computational cluster" (Docker tag = Beta), while Apptainer/Singularity is
"recommended for HPC clusters" and is GA - Singularity exposes the MPI/TCP
networking stack Docker hides, making it the multi-node choice. Coupled flow-wave
is compute-heavy + benefits from hundreds of cores: runs use MPI
(`mpirun -np $procs d_hydro.exe` for FLOW + `wave.exe` for the SWAN side; FM uses
DIMR), Slurm + Intel MPI on HPC, and `--shm-size 4G` shared memory for
parallel/coupled Docker runs. The canonical workflow is prepare-input-locally ->
push to a cloud container -> run -> download results. For a scale-to-zero AWS Batch
island this maps cleanly: package the Apptainer/Docker kernel, stage the deck on
S3, run on Spot with MPI, write NetCDF back to S3.

The image is multi-GB (Fortran kernels + OpenMP/MPI + NetCDF/HDF5/METIS) - heavier
than the deltares/sfincs-cpu base our SFINCS worker thin-layers, on the order of the
GeoClaw worker (our current heaviest). Apply the GeoClaw hygiene (slim base,
--no-install-recommends, no .git, pinned version, .dockerignore) and inspect
size/history before ECR push.

**Inductiva.AI is a CREDIBLE managed ESCAPE HATCH, not the default lane.** It
removes the two hardest self-host burdens (the gated/heavy container + the MPI run
wiring) via a thin Python API
(`inductiva.simulators.Delft3DFM(version=...).run(input_dir=..., commands=[...],
on=MachineGroup(...))`) that provisions a GCP spot machine (e.g. c2d-highcpu-16, up
to ~180 vCPU), runs the literal dflowfm/mpirun/coupled `run_sim.sh` commands, then
`task.wait()/download_outputs()/terminate()` - spot + explicit terminate is
effectively scale-to-zero. It supports BOTH Delft3D4 (FLOW-WAVE coupling, e.g.
version 6.04.00) and Delft3D-FM (2024.03) AND multi-core MPI out of the box - the
exact part our single-node Batch seam CAN'T do.

What Inductiva does NOT fix: the user STILL ships a complete, valid model deck as
`input_dir` (Inductiva RUNS your files, it does not AUTHOR them) - so the
hydrolib-core/HydroMT deck composition (section 4) is required regardless.
Tradeoffs vs self-host: (cost) per-vCPU-hour markup + an external billing
relationship; (dependency) a third-party SaaS + GCP in the critical path, which
CONFLICTS with our AWS-native, scale-to-zero-island, no-new-always-on-dependency
posture and the GCP->AWS migration we just finished; (control) we lose the
S3-completion-manifest envelope, IAM, telemetry, and the `GRACE2_AWS_BATCH_JOB_DEF`
seam every other engine shares.

**Recommendation:** self-host single-node 2D on the existing Batch seam to stay
architecturally consistent + AWS-native; hold Inductiva as the pragmatic escape
hatch specifically for the multi-node coupled FLOW-WAVE workloads our Batch seam
can't reach - a spike/fallback to PROVE the morphology/DELWAQ value first, not the
default. **Tier: B.**

---

## 7. Differentiation + honesty (avoid being a worse SFINCS+SWAN)

Scope Delft3D to the lane the existing engines cannot serve, and say so in the tool
description so the LLM routes correctly:
- USE for MORPHODYNAMICS (sediment transport + bed evolution, erosion/deposition,
  dredging), DELWAQ WATER-QUALITY/ECOLOGY (nutrients, contaminants, algae, DO,
  reactive constituents), 3D BAROCLINIC estuarine circulation, and the ONLINE
  two-way flow<->wave-driven-current morphology runs SWAN-alone + SFINCS-alone
  cannot do.
- DO NOT use for the standalone wave field (that is SWAN - Delft3D-WAVE IS SWAN),
  compound coastal surge+wave inundation (that is SFINCS + SnapWave), fluvial/
  riverine routing on a conforming mesh (that is the TELEMAC spike), urban
  pipe-network drainage (that is SWMM), or tsunami run-up (that is GeoClaw).
- Engine OVERLAP / justification gate: SFINCS (compound flood), GeoClaw
  (surge/dam-break/tsunami run-up), and the researched SWAN/TELEMAC paths already
  cover much coastal/hydraulic ground. Delft3D's distinct adds are 3D baroclinic
  hydrodynamics + sediment/morphology + water-quality/ecology. PICK a North-Star use
  (e.g. the sediment/dye ravine plume or estuarine WAQ) that those engines
  genuinely cannot do BEFORE committing the XL build, or it is redundant weight.
- Honesty floor (render-chokepoint norm): a Delft3D envelope that produced an
  empty/all-zero field NEVER reads `status=ok`; the postprocess raises a typed
  error (mass-balance / volume-error gate read from the run diagnostics, mirroring
  SWMM's continuity-error gate) rather than publishing a silently-wrong layer.
  Every narrated number comes from the typed LayerURI scalars (Invariant 1), never
  free-generated.

---

## 8. Ordered minimal-integration job list (IF the morphology/DELWAQ wedge is GO)

Mirrors the SFINCS / GeoClaw / SWMM / TELEMAC landing sequence. Each job is
single-owner, frozen kickoff, live-evidence-gated. Schema-first, then the
hydrolib-core/HydroMT deck author (the bulk + the hard prerequisite), then worker,
then infra, then agent-chain, then acceptance. This list is gated on PICKING the
morphology-or-DELWAQ North-Star use first (section 7); it is NOT a "adopt the whole
suite" list.

0. **(gate, before any job): pick the North-Star use + prove value via Inductiva
   spike.** Decide morphology/sediment OR DELWAQ water-quality as the concrete
   target (recommend the sediment/dye ravine plume tied to
   project_sediment_dye_transport_north_star), and OPTIONALLY prove the deck +
   value end-to-end via the managed Inductiva.AI escape hatch (section 6) BEFORE
   committing the self-host build. De-risks the Fortran/MPI burden; throwaway if it
   does not pay off.

1. **schema (job D1): Delft3D contracts.** Add
   `packages/contracts/.../delft3d_contracts.py`: `Delft3DBuildSpec`,
   `Delft3DRunArgs` (bbox, variant=delft3d_fm, scenario literal
   morphology/water_quality/hydrodynamics, grid res, boundary type+forcing,
   processes/sediment-fractions or DELWAQ-constituents, sim window, output cadence),
   and a typed `Delft3DLayerURI(LayerURI)` carrying the scenario metric (e.g.
   `max_bed_change_m` / `max_concentration` / `max_depth_m`). Reuse `BBox` + the
   `continuous_flood_depth` preset for depth; add a sediment/constituent style key.
   Template off `geoclaw_contracts.py` / `telemac_contracts.py`. (No solver dep;
   pure pydantic + unit tests.)

2. **engine (job D2): deterministic deck author on hydrolib-core/HydroMT
   (Delft3D-free at author time, fully unit-tested).**
   `services/workers/delft3d/deck_builder.py`: use hydrolib-core / HydroMT-Delft3DFM
   to MATERIALIZE the coupled file set (`.mdu` + `_net.nc` grid + `.dep` bathymetry
   from the existing topobathy COG + `.bnd`/`.bc` boundaries + `.ext` forcing +
   the MOR/DELWAQ input blocks) deterministically from the build_spec. This is the
   `setrun_builder.py` analogue AND the hard prerequisite (section 4) - the bulk of
   the work. Test hard with a synthetic AOI (mirror `test_setrun_builder.py`).

3. **infra (job D3): worker image + Batch job-def + ECR.** Author
   `services/workers/delft3d/Dockerfile` - resolve the container source FIRST
   (build-from-source into ECR, OR a license-cleared community image, OR a formal
   Deltares access request; flag the redistribution/version-pin review per the
   container-hygiene norm) + the Fortran/OpenMP/NetCDF/HDF5/METIS toolchain;
   build-time tiny-grid run smoke; size/history inspect. Build OFF-box via
   `grace2-worker-builder` CodeBuild. Add the `grace2-delft3d` Batch job-def +
   compute-env (Spot, scale-to-zero, xlarge tier) and the
   `GRACE2_AWS_BATCH_JOB_DEF_DELFT3D` env. `entrypoint.py` = GeoClaw/SWAN entrypoint
   clone: stage inputs, run `dimr`/`dflowfm` (single-node, optional `--partition`
   + `mpirun -np N` in one container), glob `*.nc` outputs, ALWAYS write
   `completion.json`, classify diagnostics vs failures, cancel chain. (Infra delta =
   NEW heavy image + ONE job-def + ONE env flip; no `solver.py` transport change.)

4. **engine (job D4): Delft3D postprocess + workflow.**
   `workflows/postprocess_delft3d.py` (read the NetCDF result, rasterize the
   scenario field [bed change / constituent concentration / depth] per frame ->
   peak + per-frame COGs, metrics, publish via the shared/new style preset; honesty
   gate from the run diagnostics) + `workflows/model_*_delft3d_scenario.py` (the
   fetch -> stage build_spec -> Batch-solve -> postprocess chain, the
   `model_dambreak_geoclaw_scenario` analogue) + `workflows/run_delft3d.py` that
   registers `'delft3d'` in `SOLVER_WORKFLOW_REGISTRY` like `register_geoclaw_solver`.

5. **agent (job D5): `run_delft3d_morphology` (or `run_delft3d_water_quality`)
   atomic tool.** The `run_geoclaw_inundation` analogue: validate/coerce args into
   `Delft3DRunArgs`, dispatch the workflow, return `Delft3DLayerURI` or a typed
   error dict. `cacheable=False`/`ttl_class="live-no-cache"`/`source_class=
   "workflow_dispatch"`/`read_only_hint=False`; behind the confirmation hook
   (Invariant 9, a solver run). Add a tool-catalog entry with the section-7 routing
   guidance (Delft3D = morphology/sediment + DELWAQ water-quality; SWAN = standalone
   waves; SFINCS = compound flood; TELEMAC = fluvial mesh).

6. **testing (job D6): live acceptance.** Drive the picked North-Star case
   (sediment/dye ravine plume or estuarine WAQ) end-to-end on Batch, prove the
   NetCDF -> COG -> map render + scrubber showing the morphology/constituent field,
   capture the run wall-clock at a known mesh size, and emit the
   `READINESS_RESULT delft3d PASS run_id=... layers=N metric=...` one-liner (the
   acceptance-driver parity norm).

7. **engine (job D7, later): online flow<->wave coupling / multi-node escape
   hatch.** The B-tier slice (sections 5-6): either wire the in-container
   single-node coupled `d_hydro + wave` (SWAN) run, or formalize the Inductiva.AI
   fallback for the true multi-node coupled FLOW-WAVE workloads our Batch seam can't
   reach. NOT a v0.1 requirement; lands after the standalone morphology/DELWAQ wedge
   is proven.

8. **schema (job D8, user-landed): SRS appendix amendment.** Propose the Appendix-B
   engine-table + Appendix-D tool-registry amendment for Delft3D (specialist
   proposes via report; only NATE lands into `docs/srs/*` then `make srs`). Not a
   code job.

Critical path: D0 (gate) -> D1 -> D2 -> {D3, D4 in parallel} -> D5 -> D6.
D7/D8 trail.

---

## 9. Risks (carried forward into the jobs)

1. **Suite overkill / engine overlap** - the central risk. WAVE=SWAN (redundant),
   FLOW overlaps SFINCS (SFINCS wins for flood). Resolved by scoping STRICTLY to the
   morphology/sediment + DELWAQ water-quality wedge and picking a North-Star use
   those engines genuinely cannot do BEFORE committing the XL build (section 7).
2. **Coupled-deck composition is the real cost** - a valid run needs a consistent
   `.mdu/.mdw` + grid (`_net.nc`/`.grd`) + `.dep` + `.bnd/.bc` + `.ext` set; one
   mismatch silently mis-runs or fails. MUST build on hydrolib-core/HydroMT-Delft3DFM
   in a worker `deck_builder`; do NOT hand-template flat files. Bound the LLM to a
   constrained scenario menu in v0.1 (section 4).
3. **Multi-node MPI + tight FLOW-WAVE coupling** (the flagship coastal use) is
   OUTSIDE our single-node Batch seam. Scope v0.1 to single-node 2D D-Flow FM
   (optionally single-node domain-decomposed mpirun); true multi-node = new Batch
   multi-node/EFA infra OR offload to Inductiva - a B-tier slice, the same boundary
   the SWAN spike drew (section 5).
4. **Official container is ACCESS-GATED + Docker is Beta** - blocks a clean
   ECR-mirror digest-pin like deltares/sfincs-cpu. Mitigate by build-from-source
   into ECR, evaluating community images (nerdalize 5.0.1, lsucrc) for
   license/redistribution, or formally requesting access; flag a
   redistribution/version-pin review before any ECR push (section 2/6).
5. **Heavy multi-GB image** (Fortran + OpenMP/MPI + NetCDF/HDF5/METIS) - our
   heaviest worker class alongside GeoClaw. Apply the GeoClaw hygiene (slim base,
   --no-install-recommends, no .git, pinned version, .dockerignore) and inspect
   size/history before ECR push.
6. **Two product variants** with DIFFERENT decks/run-commands - legacy Delft3D4
   (FLOW+WAVE, config_d_hydro.xml, structured `.grd`) vs Delft3D-FM (dflowfm,
   `_net.nc` unstructured). Pick ONE for v0.1 - recommend Delft3D-FM 2D (HydroMT
   plugin + dfm_tools mesh story align with our HydroMT/SFINCS substrate + the
   TELEMAC unstructured-mesh direction).
7. **Long-tail runtimes** (hours-to-days for large/long coupled coastal meshes)
   stress Spot interruption + the agent wait_for_completion/auto-stop loop. Apply
   the granularity gate (#154) to cap mesh/duration and rely on S3-completion-
   manifest decoupling so a Spot reclaim or box-sleep does not lose the run.

---

## 10. Real pipelines (grounding - this is how practitioners run it)

- **Delft3D-WAVE IS SWAN online-coupled to Delft3D-FLOW** - the standard
  morphodynamics / coastal-engineering wrapper around SWAN. WAVE renders the SWAN
  `.swn` from its `.mdw` and exchanges radiation stress with FLOW each step through
  a com-file (the SWAN spike, section 11, cites this exact pairing).
- **Coupled FLOW-WAVE-MOR morphodynamics** - the canonical Delft3D coastal use:
  waves drive currents, currents drive sediment transport, sediment updates the bed,
  the bed feeds back into flow + waves. Run as `d_hydro.exe & wave.exe` concurrently
  (v4) or DIMR-orchestrated (FM), MPI multi-node on HPC. This is the headline use we
  keep OUT of the single-node v0.1 scope but is the proof Delft3D is THE open
  morphodynamics engine.
- **DELWAQ water-quality / ecology** - FLOW produces the hydrodynamic field; DELWAQ
  advects-diffuses-reacts arbitrary constituents (nutrients, algae, contaminants,
  DO) on it. The engineering-grade home for the sediment/dye North-Star plume and
  the conceptual hand-off to the existing MODFLOW GWF+GWT solute path.
- **Containerized cloud runs** - Deltares Docker (single node) + Apptainer/
  Singularity (HPC multi-node, exposes MPI/TCP); prepare-input-locally -> cloud
  container -> run -> download. ElasticCluster on AWS/Azure/GCP + Azure Batch
  Shipyard schedule these; Inductiva.AI offers it as a MANAGED API (upload deck ->
  versioned container on a chosen machine -> zipped result bundle).
- **The two-suite reality** - Delft3D 4 (structured, maintenance mode) vs Delft3D FM
  (unstructured flexible mesh, the successor) maintained in one Deltares source
  tree; practitioners increasingly target FM for new work, which is why v0.1 should
  too.

---

## 11. Sources (primary)

- github.com/Deltares/Delft3D ("Repository for Delft3D FM and Delft3D 4 kernels";
  per-component AGPL-3.0/GPL-3.0/LGPL-2.1 licensing).
- oss.deltares.nl/web/delft3d (home), /about, /terms-of-use; oss.deltares.nl/web/
  delft3dfm (FM suite).
- deltares.nl product pages: delft3d-4-suite; delft3d-fm-suite/modules/
  d-flow-flexible-mesh.
- Delft3D-WAVE User Manual (content.oss.deltares.nl/delft3d4/Delft3D-WAVE_User_
  Manual.pdf) + WAVE tutorial (wave_um_tutorial.pdf, "Simulation of short-crested
  waves with SWAN", .mdw -> .swn translation, online coupling type 3).
- Deltares container wiki (publicwiki.deltares.nl Delft3DContainers - Docker Beta +
  Apptainer/Singularity GA, --shm-size, mpirun usage) + the DSD-INT 2022
  Singularity-on-HPC slides (Mourits).
- Inductiva.AI Delft3D guides (inductiva.ai/guides/delft3d/tutorials/
  flow-wave-coupling - managed API, dflowfm --partition + mpirun, run_sim.sh
  d_hydro & wave, version 6.04.00 / FM 2024.03, c2d-highcpu-16).
- swanmodel.sourceforge.io (the SWAN kernel under WAVE); oss.deltares.nl forum
  thread on coupling.
- Sibling spikes (cross-checked in-repo): reports/design/engine_spike_swan.md
  (WAVE=SWAN at lines 579-581; the A-tier wave envelope) and
  reports/design/engine_spike_telemac.md (the open unstructured full-SWE engine).
- GRACE-2 live seam (cross-checked in-repo): `tools/solver.py`
  (`_resolve_batch_job_def`, `SOLVER_WORKFLOW_REGISTRY`,
  `SOLVER_BATCH_JOBDEF_REGISTRY`, the `GRACE2_AWS_BATCH_JOB_DEF_<SOLVER>` ladder;
  0 numNodes/nodeProperties = single-node only); `AWS_BATCH_COMPUTE_CLASS_SIZING`;
  `run_river_seepage_tool.py` / `run_modflow_tool.py` (the GWF+GWT solute hand-off);
  `workflows/run_geoclaw.py` (`register_geoclaw_solver`).
