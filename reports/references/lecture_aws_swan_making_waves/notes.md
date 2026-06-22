# Reference: "Making Waves - Wave Modeling with SWAN" (Australian Water School)

Source: Australian Water School (AWS) webinar/video "Making Waves: Wave Modeling with
SWAN". Archived 2026-06-22 (NATE) as a primary practitioner reference for the coastal
wave-modeling pipeline. The Baird coastal lecture (reports/references/lecture_baird_coastal/)
is a sibling archive. These videos are the source of the coastal lecture notes driving the
SWAN spike (reports/design/engine_spike_swan.md) and the compound-flood direction.

Note: "AWS" here = Australian Water School, NOT Amazon Web Services.

The presenters categorize wave models by HOW MUCH PHYSICS they resolve, which dictates each
model's ROLE in a coastal modeling pipeline (regional -> nearshore -> structure-scale). This
is the canonical practitioner pipeline we should mirror (see feedback: research-real-pipelines-first).

## The wave-model resolution hierarchy

### 1. Spectral wave models -- e.g. SWAN, WaveWatch III (WW3)  [~32:21-33:52]
- FUNCTION: solve the wave-energy / action-balance equation to simulate the "sea state"
  (general wave heights, periods, directions) over LARGE REGIONAL areas.
- PIPELINE POSITION: the STARTING POINT. Regional assessment + they PROVIDE THE BOUNDARY
  CONDITIONS for the finer, phase-resolving models downstream. [~33:55-34:39]
- REQUIRED DATA: wind fields, bathymetry (depth), and initial/boundary wave spectra.
  [~38:52-40:09]

### 2. Wave-group resolving models -- e.g. XBeach  [~34:10-34:40]
- FUNCTION: simulate individual GROUPS of waves; how wave energy moves and interacts
  closer to shore (infragravity, runup, dune erosion, overwash).
- PIPELINE POSITION: the NEARSHORE component; takes the SPECTRAL model's output as its
  offshore boundary input.
- REQUIRED DATA: nearshore bathymetry + offshore wave boundary conditions FROM the
  spectral model.

### 3. Phase-resolving models -- e.g. MIKE 21 Boussinesq  [~34:45-35:19]
- FUNCTION: calculate INDIVIDUAL waves; complex small-scale scenarios -- waves up narrow
  channels, around specific structures, harbours, diffraction.
- PIPELINE POSITION: high-fidelity studies of specific structures / complex geometries
  where standard spectral assumptions fail. [~33:45-33:52]
- REQUIRED DATA: high-resolution bathymetry + specific boundary wave inputs.

## Supporting tools + supplemental modeling
- NOMOGRAPHS [~19:25-21:28, 37:32-37:48]: graphical wave-height/period estimates from wind
  speed + fetch. A simple "reality check" / sanity bound on model results. (Cheap analytic
  utility -- a great LLM-callable pre-flight + sanity tool.)
- HYDRODYNAMIC MODELS -- e.g. Delft3D, TUFLOW [~49:00-51:14]: simulate water movement
  (flooding/tides); COUPLED with wave models. In complex scenarios run ITERATIVELY: run
  the hydrodynamic model, pause, run the wave model, FEED RESULTS BACK, repeat.
- OVERTOPPING MODELS -- e.g. EuroTop [~46:07-46:33]: compute the water VOLUME passing OVER
  a structure once the nearshore wave energy is known. (Empirical -- the EurOtop manual
  formulas; small, deterministic.)

## Map to the GRACE-2 stack (where each fits)

| Pipeline tier | Lecture example | GRACE-2 status | Role for us |
|---|---|---|---|
| Spectral (regional sea state) | SWAN, WaveWatch III | SWAN = GO_WITH_CAVEATS spike (engine_spike_swan.md); WW3 = the 2D-spectra boundary source (SWAN spike S9) | The wave BOUNDARY producer; SWAN is the next wave engine to build |
| Fast in-hydro wave setup | (SnapWave - Deltares) | HAVE (SFINCS quadtree path) | Cheap wave setup folded into SFINCS inundation; the near-term "show waves" path |
| Hydrodynamic (flood/tide) | Delft3D, TUFLOW | HAVE SFINCS; TUFLOW = NO_GO (licensing, engine_spike_tuflow); Delft3D = open alt | Our SFINCS is the hydro core; the lecture's "run hydro, pause, run wave, feed back" iterative coupling IS the SWAN->SFINCS coupling (SWAN spike S8) |
| Wave-group nearshore | XBeach | CANDIDATE (new) | Open-source nearshore runup/overwash/dune erosion; takes SWAN output; the natural NEXT spike after SWAN |
| Phase-resolving structure-scale | MIKE 21 Boussinesq | CANDIDATE (likely NO_GO) | MIKE is commercial/licensed (TUFLOW-class gate); prefer an open Boussinesq (e.g. FUNWAVE/Celeris) if we ever need structure-scale |
| Overtopping | EuroTop | CANDIDATE (easy) | Empirical EurOtop formulas -> overtopping volume/rate; a small deterministic POST-PROCESSOR on nearshore wave output, not a solver |
| Reality check | Nomographs | CANDIDATE (trivial) | Wind+fetch -> Hs/Tp analytic estimate; a cheap LLM-callable sanity/pre-flight tool |

## Roadmap implications (possible additions, for later - NATE "refine and extend")
1. This VALIDATES the SWAN spike's positioning: spectral = the regional starting point that
   FEEDS the nearshore models. It also validates SWAN's S8/S9 (SWAN->SFINCS iterative
   coupling + WW3/ERA5 2D-spectra boundary) as the right shape.
2. NEW engine candidates surfaced, in rough build-ease order:
   - NOMOGRAPHS (trivial): a pure-analytic wind+fetch -> Hs/Tp tool; reality-check + a
     zero-cost wave-boundary estimate when no spectral run is warranted.
   - EurOtop OVERTOPPING (easy): empirical post-processor on nearshore wave + a structure
     crest level -> overtopping rate/volume; pairs with coastal-defense / levee scenarios.
   - XBeach (medium, open-source): the wave-group NEARSHORE engine - runup, overwash, dune
     erosion - the natural next spike AFTER SWAN; consumes SWAN's offshore boundary.
   - WaveWatch III (medium): global spectral -> the real 2D-spectra boundary for SWAN
     (replaces the parametric ERA5 boundary; SWAN spike S9).
   - MIKE 21 Boussinesq (gated): commercial/licensed -> likely NO_GO like TUFLOW; if
     structure-scale phase-resolving is ever needed, evaluate an OPEN Boussinesq instead.
3. The "run hydro -> pause -> run wave -> feed back" ITERATIVE coupling is a general pattern
   our run_solver/Batch seam can express as a multi-job workflow (SFINCS <-> SWAN), and
   generalizes the SWAN spike's one-way S8 coupling to a two-way loop for compound cases.

Relates to: [[reference_engine_cloud_ai_drivability_ranking]], reports/design/engine_spike_swan.md,
[[project_baird_coastal_lecture_oceanmesh2d]], [[project_sfincs_north_star_demo]].
