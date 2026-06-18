# HEC Stack (HEC-HMS + HEC-RAS) — Combined Recommendation

**Authored 2026-06-17** from two research workflows: HEC-RAS (`wz3j2yphl`) + HEC-HMS
(`wvbzo0m0j`). NATE's ask: add the industry-standard USACE engines because SFINCS material
is thin and HEC unlocks QGIS tooling. Verdict below.

## The one-sentence verdict
Both engines are **compute-feasible headless on Linux/AWS Batch today and license-clean**;
for **both**, the real constraint is **model authoring from scratch** (GUI-recommended), so
the honest v0.1 is **templated decks + safe-knob parameter-sweep**, not arbitrary-AOI
autonomous authoring. They slot into our existing engine-agnostic seam with ~zero contract
changes — this would be the 4th-engine proof (after SFINCS, MODFLOW, PySWMM).

## The two engines

### HEC-HMS — the hydrology front-end (recommend FIRST)
- **What it is:** rainfall → watershed runoff → a discharge **hydrograph** Q(t). Output is a
  HEC-DSS **timeseries, not a raster** — so it is a *forcing producer*, never a `publish_layer`
  engine.
- **Headless:** the cleaner of the pair. Pure Java, **official Linux distribution** (bundled
  JRE), official `hms -s script.jython` non-interactive compute (no X11). A compute-only
  container skips mpich/TauDEM entirely.
- **The payoff — it closes the gap map's #1 item:** an HMS hydrograph *is* exactly the
  discharge boundary forcing SFINCS wants (`setup_river_inflow` → `setup_discharge_forcing`)
  and the HEC-RAS upstream BC. It replaces today's crude single-Atlas-14-depth uniform
  `netamt` lump with physically-based runoff (SCS-CN/Green-Ampt loss + unit-hydrograph routing
  + baseflow).
- **Idaho rain-on-snow (Case 3):** HMS Temperature-Index snowmelt + SWE (USACE-validated at
  Willow Creek, Idaho) is the *only* engine in our stack that addresses the rain-on-snow gap
  the flood-pipeline critique flagged.
- **Integration:** reuses `LocalSolverSpec` `exec_kind="exec"` (the MODFLOW path). Net-new: a
  DSS↔pandas bridge (`hecdss`, vendor `libhecdss.so`), a hydrograph schema subtype, a Vega-Lite
  chart viz, and `build_hms_model` (template + safe-knob mutation + GIS delineation/CN).
- **Risks:** headless-in-Docker is feasible-but-not-yet-proven (Swing app → `-Djava.awt.headless`
  / xvfb fallback); the HMS→SFINCS leg is gated on a hydromt-sfincs 1.2.2 pandas≥2.0 bug
  (`set_forcing_1d`); high-res domestic curve numbers want a net-new gSSURGO HSG fetcher.

### HEC-RAS — the hydraulics engine (riverine / regulatory)
- **Compute: proven now.** USACE native x64 Linux binaries (6.6), public domain, runs at
  Windows parity on AWS, FEMA-FFRD runs it in-cloud. 1:1 with our SFINCS container pattern.
- **The caveat:** 2D mesh + terrain authoring is **Windows-.NET (RasMapper) bound in 6.x** →
  no autonomous new-2D-model-from-DEM headless. v0.1 = param-sweep over a pre-authored template.
  Full headless authoring arrives with **HEC-RAS 2025/7.0** (C#.NET, Docker/S3-native; beta now).
- **Role:** riverine / FEMA-regulatory / dam-break / levee-breach / 1D + coupled 1D-2D / 2D
  overbank / hydraulic structures. **NOT coastal** (open-ocean surge/tide/wave aren't in its
  equations) — that stays SFINCS.

### The QGIS unlock (the headline — and it helps SFINCS NOW)
Independent of whether HEC-RAS ships: **MDAL / QgsMeshLayer (QGIS core) reads HEC-RAS HDF *and*
SFINCS NetCDF / TELEMAC SELAFIN / TUFLOW natively** = one engine-agnostic mesh-viz path.
`native:meshrasterize` gives a zero-plugin per-frame → COG → existing-scrubber animation that
works across HEC-RAS and SFINCS output. `rashdf`/`ras-commander` are pure-Python cross-platform
for result extraction. (RiverGIS is a dead end for us — GUI/PostGIS-only, 2018.)

## The 4-engine role split
- **SFINCS** — coastal / compound, large-scale, fast, probabilistic, wave-driven (the workhorse).
- **PySWMM** — urban drainage + quasi-2D around buildings (the urban North Star).
- **HEC-RAS** — riverine / regulatory / dam-break / levee / 1D+2D detail.
- **HEC-HMS** — the hydrology *upstream* stage feeding all of the above with discharge.
- **MODFLOW** — groundwater.

## Recommended sequencing
**HMS first, then HEC-RAS.** HMS is the easier headless lift, it unblocks SFINCS fluvial
coupling (gap #1) and the Idaho demo, and it replaces the crude lumped rainfall — value that
lands *before* RAS. HMS + RAS together then form the integrated USACE riverine/compound stack.
Both sit behind the two North Stars (urban PySWMM + coastal SFINCS) and the locked
tool-integration order unless you want one pulled forward.

## Phased plans (each starts with a headless P0 spike on a stock USACE sample deck)
- **HMS:** P0 smoke spike (tiny basin → hydrograph headless) → P1 DSS bridge + LocalSolverSpec
  → P2 `build_hms_model` (template+safe-knob) → P3 GIS parameterization (delineation + CN) →
  P4 HMS→SFINCS coupling → P5 hydrograph postprocessor + schema + chart → P6 Idaho rain-on-snow.
- **HEC-RAS:** P0 compute spike (USACE Muncie 2D test deck, terrain pre-baked → prove
  compute+IO+completion on Batch) → P1 wire the seam → P2 postprocessor (rashdf→COG, then MDAL
  mesh→WMS-T) → P3 v0.1 param-sweep authoring → P4 decide new-mesh-from-DEM fork → P5 SRS/catalog.

## Decisions for NATE (the load-bearing ones)
1. **Sequencing / commitment** — add the stack, HMS-first? Or parallel / RAS-first / pause behind
   the North Stars?
2. **Authoring scope for v0.1 (both engines)** — templated decks + safe-knob parameter-sweep
   (recommended), or full autonomous any-AOI authoring (much bigger; for RAS needs a Windows
   GeomMesh micro-worker that fights the all-Linux scale-to-zero/cost norm, or waiting for 7.0)?
3. **Licensing sign-off** — OK to bake the unmodified official USACE Linux binaries (HMS + RAS)
   into our ECR image? Public domain + freely redistributable, but T&C = no-modify/no-decompile
   and "copies remain the property of HEC." Only NATE can make this IP call.
4. (Default, not blocking) First HMS demo: a simple CONUS fluvial basin to prove the chain, then
   Idaho rain-on-snow.
