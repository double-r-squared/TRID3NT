# TELEMAC substance transport: how professionals do it (research before code)

NATE directive 2026-07-18: research professional practice before building the
oil/substance track. Sources: the TELEMAC v9 engine shipped in our own worker
image (telemac2d.dico keyword docs + sources/telemac2d/oilspill.f - the
authoritative steering-file parser), the TELEMAC-2D user manual (secs 12.1
drogues / 12.3 oil spill), and the professional-practice context below.

## 1. The two professional modeling classes (both exist, both are used)

**A. Dissolved passive tracer (Eulerian, what we run today).**
Professional precedent: USGS rhodamine-dye time-of-travel studies - the
standard technique for river spill-response planning for 50+ years; agencies
literally release dye and fit advection-dispersion. Our TELEMAC tracer path IS
this method, numerically. Used for: dissolved/miscible contaminants (sewage,
chemicals, the dissolved fraction of a fuel), time-of-travel, dilution curves.
Output: concentration field (our raster + animation). LIMIT: no buoyant-slick
behavior.

**B. Lagrangian oil-spill particles (the TELEMAC oil spill module).**
Pedigree: the MIGRHYCAR project (EDF/CEREMA et al., Goeury 2012/2014, Loire
validation cases); same architecture as NOAA's GNOME, the US spill-response
standard (particles + weathering). CONFIRMED COMPILED IN OUR IMAGE
(oilspill.f -> builds/gnu.shared/obj/telemac2d/oilspill.o).
- Activation (v9): `PARTICLE TRANSPORT : 'OIL SPILL'` + `OIL SPILL STEERING
  FILE` (the old `OIL SPILL MODEL` boolean is retired in 9.0 per the dico).
- Oil steering file format (from the oilspill.f reader, line-by-line):
  header; NB_COMPO (unsoluble components) then per-component [fraction, TB];
  NB_HAP (soluble/PAH components) then per-component rows; RHO_OIL (density);
  ETA_OIL (viscosity); VOLDEV (spilled volume m3); TAMB (ambient temp);
  ETAL (spreading-law option; +AREA when constant-area mode).
- Release: via the drogues machinery - `MAXIMUM NUMBER OF DROGUES`,
  `DROGUES INITIAL POSITIONING DATA FILE` (+ FORMAT keyword) in v9 replaces
  the legacy user-Fortran FLOT routine; `PRINTOUT PERIOD FOR DROGUES`.
- Output: ASCII/BINARY DROGUES FILE (TecPlot-style particle positions per
  printout period) - NOT the SELAFIN tracer field; needs a NEW postprocess
  (particle parsing -> animated point layer + binned surface-density COG).
- Physics included: current(+wind) advection, Fay-type spreading, component
  evaporation, dissolution (soluble components feed a TRACER - so the module
  ALSO outputs a dissolved-fraction concentration field!), beaching on banks
  + re-release (routines confirmed in source).

Key insight for NATE's compare-both plan: the oil module's DISSOLVED fraction
lands in a regular tracer -> our existing raster postprocess can read it,
while the slick is particles. So a single oil run can yield BOTH layers:
slick particles (new path) + dissolved plume (existing path). The comparison
demo is natural: tracer-surrogate run vs oil-module run over the same mesh.

## 2. Substance library (MODFLOW-species parity)

Schema per substance (data-driven, like the MODFLOW contaminant lever):
  substance: {class: tracer|oil, label, units,
              tracer: {decay_rate_per_day (TELEMAC tracer decay law)},
              oil: {rho, eta, voldev_default, n_compo, compo[], n_hap, hap[]}}
Presets v1: dye (tracer, no decay), sewage/coliform (tracer, decay ~1/day),
generic-chemical (tracer), diesel (oil), light-crude (oil), heavy-fuel (oil).
Tracer-class substances run the existing path (label + optional decay keyword
`LAW OF TRACERS DEGRADATION`); oil-class runs the module. The LLM picks the
substance from the prompt; the gate card states class + what is/isn't modeled.

## 3. Build plan (revised per this research)

- M1 (code exists, pre-directive): substance LABEL lever on the tracer path +
  wide-river bank sampling + Longview case + harness. Finish + verify.
- M2 DONE - GO (2026-07-18, on the proven Longview mesh): the steering
  file's PRESENCE auto-activates the module in v9 (lecdon_telemac2d.f - no
  PARTICLE TRANSPORT keyword); release = user_fortran OIL_FLOT override
  (default is a hardcoded LT=10000 Loire demo). 100-particle light-crude
  slick: 3.6 km drift in 59 min (~river velocity), Fay spreading 7->206 m
  radius, CORRECT END, TecPlot drogues.txt parsed (60 snapshots). HAP line =
  5 cols (FM TB SOLU KDISS KVOL). Templates versioned at
  services/workers/telemac/oil_templates/. Proof render
  m2_oil_particles_satellite.png - the 41-min cluster crosses Cottonwood
  Island: island HOLES in the ribbon mesh are M3's quality gate.
- M3: particle postprocess (animated points + density COG) + presets +
  comparison demo (same reach, tracer vs oil run side by side).
- M4: tracer decay rates for non-conservative substances (coliform etc.).

## 4. Longview/Columbia case status (RESOLVED 2026-07-18)

Both hangs root-caused + fixed (GRACE-2 6d753fa): (a) giant mainstem NHDArea
polygons -> server simplification + resultRecordCount + clip-to-transect-
envelope before union (bank stage now ~2s); (b) self-intersecting offset
banks wedged gmsh 18 min silent -> hybrid GEOS offset_curve banks + SIGALRM
240s + fail-closed MESH_BANKS_INVALID + banks_debug.npz. Wrong-arm seeding
fixed systemically (OPEN-26, 4981632): composer extracts the watercourse
name, worker re-seeds onto the gnis_name mainstem before the NLDI snap
(witnessed: Longview city-center seed -> comid 24520446, frac=1.00, widths
690-907m). Trace-scale case proven: 3km reach, h=40, real widths 965-1204m,
4990 nodes / 5.3s mesh (data/mesh-proof/columbia/).

## 5. Harness (nailed per NATE)

tests/headless_bk3b_approve_mesh_drive.py extended: case config via env
(E2E_PROMPT/E2E_EXPECT_SUBSTANCE/E2E_MIN_MEAN_WIDTH_M); flow assertions:
preview-layer-before-gate -> gate contract (engine=telemac,
release_point_required, mesh_bbox) -> driver picks an ON-MESH release point
(midpoint of a wireframe segment from the session-state inline GeoJSON) ->
narrow_scope carries {release_lon/lat, coarser rung if est>600s} -> solve ->
layer name contains the substance -> post-run metrics check (bank_source,
mean width sanity). Offline stub validation ALWAYS before live (hard rule).
