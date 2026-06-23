# SWAN + SnapWave: authoritative setup rules (primary-source reference)

Purpose: ground GRACE-2's SWAN deck/bottom/boundary and its SnapWave coupling in
the PRIMARY documentation, so we can (a) FIX the live all-dry no-op SWAN bug
(stationary deck, `swanrun -input swan_run`, exit 0 in ~32 ms, "SWAN is preparing
computation / Normal end of run WAVE", NO `swan_out.mat`) and (b) validate that
our parametric SnapWave offshore boundary is correct.

Sources (both public, open):
- SOURCE 1 -- SWAN User Manual, Cycle III version 41.51 (TU Delft / SourceForge).
  Online HTML: https://swanmodel.sourceforge.io/online_doc/swanuse/swanuse.html
  Command-reference nodes cited below:
    - node23 (Start-up: PROJECT / SET / MODE / COORDINATES)
    - node26 (Input grids and data: INPGRID / READINP)
    - node27 (Boundary and initial conditions: BOUND SHAPE / BOUNDSPEC)
    - node32 (Write or plot computed quantities: BLOCK)
    - node34 (Lock-up: COMPUTE / STOP)
    - node49 (the annotated command file, swan.edt)
- SOURCE 2 -- Roelvink, van Ormondt, Reyns, van der Lugt (2025), "SnapWave: fast,
  implicit wave transformation from offshore to nearshore", Geosci. Model Dev.
  18:9469-9495, doi:10.5194/gmd-18-9469-2025.
  HTML: https://gmd.copernicus.org/articles/18/9469/2025/
  Plus the SFINCS docs (https://sfincs.readthedocs.io/en/latest/developments.html)
  for the SnapWave<->SFINCS coupling field.

All quotes below are the manual's / paper's own wording (verbatim where marked
with quotation marks). ASCII hyphens only.

--------------------------------------------------------------------------------
## 1. SWAN bottom sign + exception + DEPMIN + active/dry rules -- and the likely cause of the all-dry no-op

### 1.1 Bottom / depth SIGN CONVENTION (positive DOWNWARD)

SWAN's bottom input is a BOTTOM LEVEL that is **positive downward** relative to a
datum -- i.e. positive value = water depth below still water, negative value =
land above datum.

- node26 (READINP / INPGRID BOTTOM), verbatim:
  > "bottom level positive downward relative to an arbitrary horizontal datum level"

- The `[fac]` factor on READINP scales (and can flip the sign of) the file values:
  > "SWAN multiplies all values that are read from file by [fac]. For instance if
  > the bottom levels are given in unit decimeter, one should make [fac]=0.1 to
  > obtain levels in m. To change sign of bottom level use a negative value of
  > [fac]." (node26)
  Default `[fac]=1`.

Consequence: a cell is WET when its bottom level (depth) is POSITIVE; a cell is
LAND/dry when its bottom level is <= 0 (or below the depmin threshold; see 1.3).
So a DEM that is positive-UP elevation (NAVD88: land > 0, seabed < 0) must be
NEGATED (depth = -elevation) before being written as the SWAN bottom -- OR read
with `[fac]=-1`.

### 1.2 EXCEPTION value ([excval]) -- marking nodata / inactive cells

A wrong exception value silently turns real sea cells into ignored cells.

- INPGRID ... EXCEPTION marks cells to skip:
  > "certain points inside the given grid that are to be ignored during the
  > computation can be identified by means of an exception value" (node26)
- The exception value is given WITH the INPGRID command (`INPGRID BOTTOM ...
  EXCEPTION [excval]`), and on READINP it must be pre-scaled by `[fac]`:
  > "[excval] exception value; required if the option EXCEPTION is used. Note: if
  > [fac] != 1 (see command READINP), [excval] must be given as [fac] times the
  > exception value." (node26)
- There is NO documented numeric DEFAULT exception value for the bottom input --
  EXCEPTION is opt-in. (The default exception value the manual mentions, e.g.
  significant wave height -9, is for OUTPUT, not for bottom input.) So a bottom
  input that happens to contain the sentinel SWAN treats as "ignore" only if you
  declared that sentinel with `INPGRID BOTTOM ... EXCEPTION`.

PITFALL for us: SWAN's exception marker for an INPUT grid lives on the INPGRID
line (`... EXCEPTION [excval]`), NOT on SET. `SET ... EXCEPTION [excval]` controls
the OUTPUT exception/fill value (the value written for points with no result), a
DIFFERENT mechanism. So writing a `-999` "exception" only on SET does NOT cause
input cells to be ignored -- it only changes the output fill.

### 1.3 SET [depmin] -- the dry threshold

- node23 (SET), verbatim:
  > "[depmin] threshold depth (in m). In the computation any positive depth
  > smaller than [depmin] is made equal to [depmin]." Default **[depmin] = 0.05 m**.
- [level] (a space- and time-constant water-level offset, default 0 m) is ADDED to
  the still-water depth, so `effective depth = bottom_level + level`. Points whose
  effective depth is <= 0 are land; positive depths below [depmin] are clamped UP
  to [depmin] (they stay wet but shallow).

So a point is treated as WET/active when `bottom_level + [level] > 0`; it is
LAND/dry when `<= 0`; positive-but-tiny depths are floored to [depmin]. A
positive-down bottom that is accidentally negated (land-positive) makes the WHOLE
sea negative -> every cell dry.

### 1.4 Active vs dry, and ending normally with NO output

SWAN runs only on ACTIVE (wet) points. If the bottom makes every point dry/land
(all depths <= 0, or every cell flagged by a mis-set EXCEPTION value), the
computational grid has ZERO active points. SWAN then "prepares computation",
performs no iteration, writes no gridded result, and still prints "Normal end of
run" -- it exits 0 with no `swan_out.mat`. The manual frames the wet/dry decision
through the depmin threshold (node23) and the EXCEPTION/ignore mechanism (node26);
it does not document a separate hard error for an all-dry grid, which is exactly
why an all-dry grid LOOKS like a clean run.

### 1.5 SPECIFIC LIKELY CAUSE of our all-dry no-op

The symptom -- exit 0 in ~32 ms, "preparing computation / Normal end of run WAVE",
no `swan_out.mat` -- is the canonical signature of a grid with NO active (wet)
points. Given the sign convention above, the most probable causes, in order:

1. BOTTOM SIGN INVERTED. The bottom got written/read with the wrong sign so the
   sea is negative (land) everywhere. Our entrypoint negates positive-up DEM
   elevation to positive-down depth (`depth = -elevation`), which is CORRECT in
   principle (see Section 4) -- but if the DEM is already positive-down, or the
   negation is dropped/double-applied, every sea cell goes <= 0 and the grid is
   all-dry.
2. BOTTOM-GRID ROW ORDER vs [idla]. `[idla]=1` (our value) means SWAN reads the
   map starting in the UPPER-LEFT (north-west) corner, top row first (see 2.x and
   Section 4). If the bottom file is written south-row-first, the bathymetry is
   flipped north<->south. That does not by itself make a grid all-dry, but it puts
   the deep water where land should be and the boundary side over land -- which can
   strand the forced boundary on dry cells so no energy enters (a different but
   adjacent failure: boundary on land -> flat zero field).
3. EXCEPTION mis-set. If a real depth value coincides with a declared INPGRID
   EXCEPTION value, those cells are ignored. (Not our current path -- we do not set
   INPGRID ... EXCEPTION -- but worth ruling out if added later.)
4. FLAT DEMO FALLBACK NOT TRIGGERED. The deck's flat-10 m demo bathymetry (all
   depth +10) is always wet; if a run is all-dry, the real DEM path (not the demo)
   is in play -- i.e. the sampled depths are the culprit, confirming (1)/(2).

Fastest diagnostic: dump min/max of the rendered `bottom.bot`. If max <= 0 (no
positive depths) the grid is all-land -> sign bug (cause 1). If there ARE positive
depths but the forced boundary side sits over the negative band, it is the
row-order/orientation bug (cause 2).

--------------------------------------------------------------------------------
## 2. The canonical SWAN STATIONARY deck (command order + key statements)

A SWAN run is one ASCII command file. For a STATIONARY 2-D nearshore field over a
regular spherical (lat/lon) grid with a parametric offshore boundary and a binary
.mat block output, the canonical order + the load-bearing rules are:

```
PROJECT 'name' 'run'
SET LEVEL 0.0 NOR 90.0 DEPMIN 0.05 ... NAUTICAL      $ depmin default 0.05; NAUTICAL last
MODE STATIONARY TWODIMENSIONAL
COORDINATES SPHERICAL
CGRID REGULAR xpc ypc alpc xlenc ylenc mxc myc CIRCLE ndir flow fhigh nfreq
INPGRID BOTTOM REGULAR xpinp ypinp alpinp mxinp myinp dxinp dyinp [EXCEPTION excval]
READINP BOTTOM [fac] 'bottom.bot' [idla] [nhedf] FREE
$ optional: INPGRID WIND ... / READINP WIND ...
GEN3 ...                                              $ physics
FRICTION / BREAKING / TRIAD ...
BOUND SHAPE JONSWAP PEAK DSPR DEGREES
BOUNDSPEC SIDE <N|S|E|W> CONSTANT PAR [hs] [per] [dir] [dd]
BLOCK 'COMPGRID' NOHEADER 'swan_out.mat' LAYOUT 3 HSIGN RTP DIR
COMPUTE                                               $ STATIONARY: bare COMPUTE, no options
STOP
```

Key authoritative statements:

- COMPUTE in stationary mode -- node34 (Lock-up), verbatim:
  > "If the SWAN mode is stationary (see command MODE), then only the command
  > COMPUTE should be given here (no options!)."
  The `COMPUTE STATIONARY [time]` form is for a stationary step INSIDE a MODE
  NONSTATIONARY run; in a MODE STATIONARY deck the bare `COMPUTE` is required.
  (Our deck_builder already emits the bare `COMPUTE` for stationary -- CORRECT.)

- BLOCK output / .mat -- node32, verbatim:
  > "if the user specifies the extension of the output file as '.mat', a binary
  > MATLAB file will be generated."
  > NOHEADER: "the output should be written to a file without header lines."
  > LAYOUT [idla]: "the user can prescribe the lay-out of the output to file with
  > the value of [idla]." Recommended idla for a generated BINARY MATLAB file is
  > **3** (idla 4 is recommended for an ASCII file post-processed by MATLAB).
  'COMPGRID' = the automatically-defined computational-grid frame.
  Output quantities (node32): HSIGN "significant wave height (in m)"; RTP "peak
  period (in s) of the variance density spectrum"; DIR "mean wave direction".

- READINP [idla] -- node26, verbatim:
  > "=1: SWAN reads the map from left to right starting in the upper-left-hand
  > corner of the map (it is assumed that the x-axis of the grid is pointing to the
  > right and the y-axis upwards). A new line in the map should start on a new line
  > in the file."
  So with idla=1 the FIRST data row is the NORTHERNMOST (top, high-y) row; rows go
  north -> south. idla=3 starts in the LOWER-left (south) corner.

### 2.x BOUNDSPEC / boundary forcing basics

- node27 (BOUND SHAPE), verbatim/paraphrase:
  > BOUND SHAPE sets the spectral shape: JONSWAP (default, [gamma] default 3.3),
  > PM, GAUSS, TMA, BIN; characteristic period PEAK (default) or MEAN; directional
  > distribution cos^m(theta - theta_peak): POWER [dd] = m (default 2) or DEGREES
  > [dd] = directional standard deviation in degrees (default 30).
- node27 (BOUNDSPEC SIDE ... CONSTANT PAR), syntax + parameters:
  > `BOUNDSPEC SIDE <N|S|E|W|NW|NE|SW|SE> CONSTANT PAR [hs] [per] [dir] [dd]`
  > [hs] significant wave height (m); [per] characteristic period (s, peak or mean
  > per BOUND SHAPE); [dir] peak wave direction (deg); [dd] directional spreading
  > coefficient. "The boundary is assumed to be a straight line."
  Diagonal corners (NW/NE/SW/SE) are STRUCTURED-GRID only.
- Direction convention (node27 / SET): with NAUTICAL set, directions are nautical
  (degrees, direction the waves COME FROM, clockwise from North). Under the default
  Cartesian convention the positive x-axis points East unless [nor] redefines it.
  Only the INCOMING wave components of the boundary spectrum are used by SWAN, so
  the forced side must contain WET/active cells for any energy to enter the domain.

--------------------------------------------------------------------------------
## 3. SnapWave: offshore -> nearshore boundary setup + SFINCS coupling

(All from Roelvink et al. 2025, GMD 18:9469; section numbers as in the paper.)

### 3.1 What SnapWave does

SnapWave is a fast STATIONARY phase-averaged wave solver on an UNSTRUCTURED /
quadtree grid that transforms an offshore wave condition to the nearshore by
solving the wave-energy balance IMPLICITLY:
- "The combined propagation, refraction and dissipation are solved implicitly for
  each point" (Sec 2.4). It discretizes the energy balance per node + directional
  bin with an UPWIND scheme that backtraces to upwind points, sweeping grid points
  in ordered directions until convergence (Sec 2.2-2.4).
- Refraction is driven by depth gradients: `Ctheta = sigma/sinh(2kh) * [dh/dx
  sin(theta) - dh/dy cos(theta)]` (Eq. 12, Sec 2.2).

### 3.2 Boundary INPUTS it needs + WHERE points go

- Inputs per boundary: significant wave height Hm0, peak period Tp, mean wave
  direction, and (optional) directional spreading. Example (Sec 3.2 circular
  island test): "Hm0 wave height of 2 m, a peak period of 15 s and directional
  spreading of 20deg."
- Placement: boundary points sit on the SEAWARD (offshore) edge of the domain, in
  water deep enough to avoid depth-induced breaking at the boundary. The paper
  imposes "Uniform boundary conditions ... on the offshore boundary and Neumann
  boundary conditions (no longshore gradient) at the lateral boundaries" (Sec 3.1).
  At scale it pulls the offshore condition from ERA5 grid points "typically 50-100
  km offshore" (Sec 6) and interpolates onto the seaward boundary nodes.
- Wetting/drying: "points ... [are made] inactive that have depth less than 1.1
  times hmin, set to 0.1 m by default" (Sec 2.4). So a boundary point on a cell
  shallower than ~0.11 m is INACTIVE -> contributes no forcing. (The SnapWave
  analogue of SWAN's all-dry trap: a seaward boundary placed over land/too-shallow
  cells silently produces no nearshore field.)

### 3.3 Coupling to SFINCS

- SnapWave is "a fast nearshore wave solver coupled with SFINCS, to resolve wave
  setup in inundation modelling" (Sec 1) and runs ON the SFINCS grid (including the
  QUADTREE mesh) -- it is the integrated wave solver inside SFINCS, not a separate
  grid. (SFINCS docs, developments.html: SnapWave operates on the SFINCS grid
  incl. quadtree; computes wave setup via radiation-stress gradients and provides
  Hm0 + mean wave period to SFINCS.)
- Net effect: SnapWave supplies the wave-force / wave-setup contribution (radiation
  stress gradients -> wave setup), with the nearshore Hm0 field as its primary
  product. Wave setup is "in the order of 2-3 cm or about 10-20% of local wave
  height" but materially changes inundation.

### 3.4 Setup gotchas relevant to a CORRECT parametric boundary

1. Boundary points MUST sit on the seaward/offshore edge, on cells deep enough to
   stay active (depth > 1.1*hmin, hmin default 0.1 m). A point on land/too-shallow
   is dropped -> no forcing (the live `hm0 stays flat 0` failure mode our code
   comments already cite).
2. Single representative period: SnapWave uses one characteristic period; for mixed
   swell + wind-sea "use ... Tm-1,0 that best represents the mean group velocity"
   (Sec 5.5). Our parametric Tp from a steepness relation is acceptable for a
   single-peak design storm.
3. Direction: the mean wave direction is the propagation direction in the solver's
   convention; the offshore boundary direction must be expressed in the SAME
   convention the SnapWave boundary API (`add_point(wd=...)`) expects. The GMD
   paper does NOT pin a nautical-vs-cartesian sign for the boundary `wd`; this must
   be validated against the cht_sfincs `snapwave.boundary_conditions.add_point`
   implementation, NOT assumed (see Section 4, finding S4).

--------------------------------------------------------------------------------
## 4. Where GRACE-2 code AGREES / DISAGREES with the docs + recommended corrections

Files inspected (read-only):
- services/workers/swan/deck_builder.py
- services/workers/swan/entrypoint.py
- services/agent/.../workflows/model_wave_scenario.py::_fetch_bathy_for_swan
- services/agent/.../workflows/model_flood_scenario.py
  (_parametric_wave_hs_m + _synthesize_parametric_wave_boundary, ~lines 726-857)

### AGREES (correct against the manual/paper)

- A1. Stationary COMPUTE. deck_builder emits a BARE `COMPUTE` for MODE STATIONARY
  (deck_builder.py ~L520), matching node34 "only the command COMPUTE should be
  given here (no options!)". CORRECT. (The module docstring's claim that
  `COMPUTE STATIONARY` "no-ops" is consistent with the manual's intent.)
- A2. Bottom sign at the SAMPLER. entrypoint `_build_depth_fn` returns
  `-elevation` (positive-up NAVD88 DEM -> positive-DOWN SWAN depth), matching
  node26 "bottom level positive downward". With READINP `[fac]=1.0` (deck_builder
  ~L451) the values reach SWAN unscaled -> a +5 m seabed reads as depth +5.
  CORRECT in direction. (Belt-and-braces alternative: write positive-up and read
  with `[fac]=-1`; do NOT do both -- pick one.)
- A3. BLOCK / .mat. `BLOCK 'COMPGRID' NOHEADER 'swan_out.mat' LAYOUT 3 HSIGN RTP
  DIR` (deck_builder ~L491) matches node32: .mat extension -> binary MATLAB, LAYOUT
  3 recommended for a binary MATLAB file, COMPGRID = computational-grid frame,
  HSIGN/RTP/DIR are valid quantities. CORRECT.
- A4. BOUNDSPEC. `BOUND SHAPE JONSWAP PEAK DSPR DEGREES` + `BOUNDSPEC SIDE <side>
  CONSTANT PAR hs per dir dd` (deck_builder ~L478-484) matches node27 syntax and
  the DEGREES directional-spread form. CORRECT in syntax.
- A5. DEPMIN. `SET ... DEPMIN 0.05 ...` (deck_builder ~L421) uses the manual
  default (node23). CORRECT.

### DISAGREES / RISK (recommended corrections)

- D1 (HIGH -- the prime all-dry suspect: BOTTOM ROW ORDER vs idla=1).
  deck_builder `render_bottom_input` writes rows with `j` ASCENDING in latitude
  (`lat = ypc + j*dy`, j=0..ny), i.e. SOUTH row first, NORTH row last
  (deck_builder.py ~L563-571). But READINP uses `[idla]=1` (deck_builder ~L451),
  and node26 defines idla=1 as "starting in the UPPER-left-hand corner" -- i.e.
  the FIRST file row must be the NORTHERNMOST. Our file is therefore flipped
  north<->south relative to what SWAN assumes. The code comment in
  `render_bottom_input` ("idla=1 (SW corner, ... rows of constant y ascending)")
  is WRONG: idla=1 is the NW/upper-left corner, not SW.
  FIX (choose ONE):
    (a) emit rows NORTH-first (iterate `lat = ypc + (inp_my - j)*dy`, i.e. j from
        top), keeping `[idla]=1`; OR
    (b) keep south-first rows and change READINP to `[idla]=3` (lower-left/SW
        corner). Per node26, idla=3 = "lower-left-hand corner".
  Effect of leaving it: the bathymetry is mirrored N<->S, so the deep-water band
  and the forced boundary side land on the wrong edge -- which can strand the
  BOUNDSPEC side over land (no incoming energy) and yields a flat/empty field even
  when some cells are wet. This is the single most likely structural cause of an
  empty `swan_out.mat`.

- D2 (MED -- EXCEPTION lives on the wrong command).
  deck_builder sets the exception ONLY via `SET ... EXCEPTION -999.0` (~L421) and
  the entrypoint masks output equal to -999. But node26 makes clear the INPUT-grid
  ignore mechanism is `INPGRID BOTTOM ... EXCEPTION [excval]`; SET's EXCEPTION is
  the OUTPUT fill value. We do NOT currently flag DEM nodata as an input exception,
  so DEM nodata cells are sampled by the depth_fn fallback (returns +10 m -> wet)
  rather than being marked land. That is SAFE for the all-dry bug (it adds wet
  cells, not dry), but it means nodata becomes spurious 10 m sea. RECOMMENDED: if
  precise land masking matters later, add `INPGRID BOTTOM ... EXCEPTION <sentinel>`
  and write that sentinel for true-land/nodata cells, rather than relying on the
  +10 m fallback. NOT the cause of the current no-op.

- D3 (LOW/INFO -- depth_fn fallback masks an all-land DEM).
  entrypoint `_build_depth_fn` and deck_builder `_depth` BOTH return +10 m on any
  sampling failure / out-of-bounds / nodata / non-finite. So a genuinely bad or
  all-land DEM would be PAPERED OVER as flat 10 m sea (always wet) rather than
  producing an honest empty result. This means: if a REAL run is all-dry, the DEM
  is being sampled SUCCESSFULLY and returning <= 0 depths -- pointing back to D1
  (orientation) or a sign double-negation, not to a missing DEM. RECOMMENDED: log
  min/max/active-cell-count of the rendered bottom (and refuse to launch SWAN when
  zero positive-depth cells exist) so the honesty gate catches all-dry BEFORE the
  32 ms no-op, with a typed `SWAN_BOTTOM_ALL_DRY` error.

- D4 (MED -- SnapWave boundary direction convention UNVALIDATED).
  model_flood_scenario `_synthesize_parametric_wave_boundary` computes
  `wd = degrees(atan2(dx, dy)) % 360` where (dx,dy) point FROM the boundary point
  TOWARD the AOI centre (~L821-827) -- i.e. the direction waves TRAVEL (a
  "going-to" azimuth, clockwise from north). SnapWave / cht_sfincs `add_point(wd)`
  may expect the direction waves COME FROM (the meteorological/nautical "coming
  from" convention is the common one for wave boundaries). The GMD paper does not
  pin this. RECOMMENDED: confirm against the cht_sfincs
  `snapwave.boundary_conditions.add_point` source which sense `wd` uses; if it is
  "coming from", our azimuth is 180deg off and waves would be forced offshore (or
  the boundary mis-refracts). This is a correctness risk for the SnapWave field,
  independent of the SWAN bug.

- D5 (LOW -- SnapWave boundary point depth not checked).
  `_synthesize_parametric_wave_boundary` places one point per bbox-edge midpoint
  inset 2% (~L790-800) but does NOT verify the chosen point sits on a cell deeper
  than 1.1*hmin (0.11 m). Per GMD Sec 2.4 an inactive (too-shallow) boundary point
  contributes no forcing -> the documented `hm0 stays flat 0` failure. The worker
  "derives the seaward edge from whichever point sits offshore", which mitigates
  this, but a fully-enclosed or shallow AOI could still strand all four points.
  RECOMMENDED: keep the existing seaward-edge derivation; optionally assert at
  least one boundary point lands on an active/deep cell, else raise a typed error
  rather than producing a flat-zero field.

### Priority for the live all-dry SWAN bug

1. D1 (bottom row order vs idla=1) -- fix first; most likely structural cause.
2. Add the D3 pre-launch all-dry guard (log bottom min/max + active count; fail
   honestly) so any residual sign/orientation issue surfaces as a typed error, not
   a silent 32 ms "Normal end of run".
3. Re-confirm A2 sign end-to-end on the real DEM (dump a few sampled depths; assert
   sea cells are POSITIVE) to rule out a double-negation.

For the SnapWave boundary: D4 (direction convention) is the load-bearing
correctness check; D5 (boundary-point depth) is the robustness follow-up.

--------------------------------------------------------------------------------
## 5. Workshop 2025 (Roelvink) - practical SFINCS+SnapWave setup

New primary source (resolves the OPEN RISK D4 above):
- SOURCE 3 -- D. Roelvink, M. van Ormondt, J. Reyns, M. van der Lugt, T. Leijnse
  (2025), "SnapWave: a fast wave component in coastal model systems", 18th Waves
  Workshop 2025 presentation (IHE Delft / Deltares).
  PDF: https://www.waveworkshop.org/18thWaves/Presentations/E1-Waves%20Workshop%202025%20Roelvink.pdf
  (42-page slide deck; slides are rasterised images, so text was read from the
  rendered slides, not extracted.) Companion paper = the GMD article already cited
  as SOURCE 2 (doi:10.5194/gmd-18-9469-2025).
- SOURCE 4 (CODE EVIDENCE, on this machine) -- the live cht_sfincs package and the
  SnapWave<->SFINCS coupling Fortran:
    - services/workers/sfincs_quadtree_spike/.venv/.../cht_sfincs/snapwave.py
      (the Python boundary writer behind add_point / write_boundary_*).
    - /tmp/sfincs_snapwave.f90 (the SFINCS-side SnapWave coupling module that
      consumes the boundary direction and writes the output direction).
    - Live decks: services/workers/sfincs_quadtree_spike/deck_cht/snapwave.{bnd,bwd,bhs}
      and services/workers/sfincs_snapwave_spike/deck/snapwave.{bnd,bwd}.

### 5.1 The DIRECTION-CONVENTION VERDICT (the load-bearing finding)

VERDICT: SnapWave's boundary `wd` is **nautical "coming FROM", degrees clockwise
from North**. GRACE-2's `_synthesize_parametric_wave_boundary` emits the OPPOSITE
sense (a "going-to" azimuth toward the AOI centre), so **our `wd` is exactly 180
degrees WRONG on every boundary edge.** Three independent lines of evidence, all
agreeing:

EVIDENCE 1 -- the cht_sfincs Python layer is a PASS-THROUGH (so it does NOT define
the convention). `SnapWaveBoundaryConditions.add_point(x, y, hs, tp, wd, ds)` and
`set_timeseries_uniform(...)` store `wd` verbatim into the point time series, and
`write_boundary_conditions_timeseries` writes that same `wd` to `snapwave.bwd`
unchanged (cht_sfincs/snapwave.py lines ~821-905 and ~1040-1056; the `.bwd` write
is a plain fixed-width dump with NO trig, NO 270-minus, NO sign flip). Therefore
whatever number GRACE-2 puts in `wd` is the number the SnapWave solver reads -- the
convention is defined entirely by the SOLVER, not by the Python API. (This is why
the GMD paper could not settle it and why reading the Python alone is insufficient.)

EVIDENCE 2 -- the SnapWave<->SFINCS Fortran states the convention in its own
comments, for BOTH the input (wind) and the output (mean wave direction):
- Input side, /tmp/sfincs_snapwave.f90 line 398 (the wind-direction ingest, the
  same directional convention SnapWave uses for all directional inputs):
    `snapwave_u10dir(nm) = u10dir / 180.0 * pi`
    `! from nautical coming from in degrees to cartesian going to in radians`
  i.e. SnapWave's EXTERNAL directional inputs are nautical "coming from" in degrees,
  and the solver converts them to an internal CARTESIAN "going to" angle in radians
  (`theta`/`thetam`).
- Output side, line 551:
    `snapwave_mean_direction = modulo(270.0 - thetam * 180 / pi + 360.0, 360.0)`
  i.e. the internal cartesian-going-to `thetam` (radians) is mapped BACK to a
  nautical "coming from" degree via `dir = 270 - theta_deg`. Inverting that gives
  the input mapping `theta_deg = 270 - wd`, the standard oceanographic
  nautical-coming-from <-> cartesian-going-to transform. So the boundary `wd` that
  feeds `update_boundary_conditions` (line 543) is unambiguously nautical
  "coming from", degrees clockwise from North.
  (`update_boundary_conditions` itself lives in SnapWave's internal
  snapwave_boundaries.f90, which is not vendored on this machine, but the symmetric
  output mapping at line 551 plus the input comment at line 398 pin the convention
  beyond doubt.)

EVIDENCE 3 -- the workshop slides show the convention on axis labels and a
controlled test:
- "Circular island, sweeping process" slide: the four directional-spectrum panels
  have the x-axis labelled "direction (oN)" (degrees North). The WEST boundary
  point (located at x<0, y=0, where the incident sea must arrive FROM the west and
  travel east toward the island) has its energy peak at ~270 oN. 270 oN = "coming
  from the west" = nautical coming-from. (A "going-to" reading of 270 would be waves
  travelling west, i.e. AWAY from the island -- contradicted by the figure.)
- "Wave direction" slide (Dutch coast Hm0 run): the colour bar is "Mean wave
  direction (deg N)" with field values ~250-290 for a North-Sea sea state arriving
  from the W/WNW -- again nautical coming-from.
- LIVE DECK CROSS-CHECK: both working spike decks force `wd = 270.000` on a
  Gulf-of-Mexico boundary (sfincs_quadtree_spike/deck_cht/snapwave.bwd and
  sfincs_snapwave_spike/deck/snapwave.bwd). 270 nautical-coming-from = waves from
  the west, a physically sensible offshore incident condition; the same 270 read as
  "going-to" would send waves offshore. The decks were authored to the
  nautical-coming-from convention.

QUANTIFIED ERROR (numerically verified against the actual GRACE-2 formula
`wd = degrees(atan2(dx, dy)) % 360`, with `(dx, dy) = (cx - x, cy - y)` pointing
FROM the boundary point TOWARD the centre):

| boundary edge | physically-correct nautical wd (FROM) | GRACE-2 wd (going-to) | error |
|---------------|---------------------------------------|-----------------------|-------|
| South edge    | 180 (waves come from the south)       | 0                     | 180   |
| North edge    | 0 / 360                               | 180                   | 180   |
| West edge     | 270 (waves come from the west)        | 90                    | 180   |
| East edge     | 90                                    | 270                   | 180   |

The error is a CONSTANT 180 degrees on every edge. Consequence in the solver: every
incident boundary spectrum is aimed back OFFSHORE (and the sweep is seeded from the
wrong "mean wave direction"), so refraction and shoaling develop on the wrong side
and the nearshore Hm0 over the AOI is wrong (in the worst case near-zero where the
mis-aimed energy immediately leaves the domain). This is a real correctness bug for
the SnapWave field, independent of the SWAN all-dry bug.

### 5.2 Boundary-point DEPTH + PLACEMENT rule (confirms/quantifies D5)

- WHERE: boundary points sit on the SEAWARD/offshore edge of the SnapWave grid, on
  the open-boundary (mask == 2) cells. The workshop "NL example" and "Netherlands
  coastal model" slides show the offshore condition taken from ERA5 grid points (the
  red/green dots) along the seaward polygon and interpolated onto the boundary; the
  "Main conclusions" slide states the offshore points are "typically 50-100 km
  offshore". The grid "can be arbitrarily cut out of [an] unstructured mesh", and a
  large-to-local nesting (kms -> 40 m -> 5 m on Coast3D; 800 -> 100 m on the NL run)
  is the normal pattern.
- DEPTH (the active-cell threshold): SnapWave inactivates any cell with depth below
  1.1 * hmin, hmin = 0.1 m by default (GMD Sec 2.4, already cited in Section 3.2).
  So a boundary point must land on a cell with **water depth > ~0.11 m** to
  contribute any forcing; a too-shallow point is silently dropped -> flat-zero hm0
  (the documented failure our code comment already cites). In practice the
  workshop/paper place the boundary in genuine offshore water (tens of metres on the
  ERA5 take-off points), well clear of the breaking zone, so depth-induced breaking
  does not corrupt the boundary value -- a much stronger requirement than the bare
  0.11 m floor. Treat 0.11 m as the HARD floor and "deep enough to not break at the
  boundary" as the practical rule.

### 5.3 Coupling into SFINCS + the 2-3 most useful practical facts for Mexico Beach

How SnapWave couples (workshop + Sec 3.3 above): SnapWave runs ON the SFINCS
(quadtree) grid as the integrated stationary wave solver, sharing the SFINCS water
levels and depths; it solves the directionally-resolved wave-energy balance (XBeach
form) implicitly via the ordered 4-direction sweep, and returns the nearshore Hm0,
Tp and mean wave direction plus the wave-force / radiation-stress contribution that
SFINCS turns into wave setup. The "New developments" slide notes the coupling is now
"robust ... (water levels) including prediction of IG waves" (todo: current
refraction) -- so IG/setup are in, current refraction is not yet.

Most useful practical facts for making OUR Mexico Beach (Hurricane Michael) SFINCS
+ SnapWave waves render correctly:
1. DIRECTION IS NAUTICAL COMING-FROM (5.1). Force the offshore `wd` as the compass
   bearing the swell ARRIVES FROM. For the Michael / Mexico Beach panhandle coast
   that is a SOUTH-to-SSE sea (roughly 160-200 oN), NOT a going-to azimuth. With the
   current code our boundary points the waves offshore -- fix per 5.4.
2. PUT THE BOUNDARY IN REAL OFFSHORE WATER, ONE SEAWARD EDGE, NOT FOUR. The
   workshop forces only the seaward polygon (ERA5 dots along the offshore edge) with
   Neumann (no-longshore-gradient) lateral boundaries; it does not ring the domain.
   Our 4-edge-midpoint scatter only works because the worker "derives the seaward
   edge from whichever point sits offshore" -- it is fragile. Ensure at least one
   point lands on a mask==2 cell with depth >> 0.11 m (tens of metres), seaward of
   the surf zone.
3. SINGLE REPRESENTATIVE Hm0/Tp/spreading IS ENOUGH. The slides drive whole coasts
   from one offshore Hm0/Tp/dir/spread per boundary point (directional spreading is
   "important but frequency can be parameterized"; ds ~ 20 deg in the circular-island
   test, our 30 deg is fine for a storm sea). Our parametric single-peak Hs/Tp design
   storm is the right shape; only the DIRECTION is wrong.

### 5.4 EXACT recommended change to _synthesize_parametric_wave_boundary

(Specification only -- do NOT edit here; this is for the agent specialist.)
File: services/agent/src/grace2_agent/workflows/model_flood_scenario.py, in
`_synthesize_parametric_wave_boundary`, the per-point loop (~lines 821-837).

CURRENT (going-to toward centre -- 180 deg wrong):
```
dx = cx - float(x)
dy = cy - float(y)
wd = (math.degrees(math.atan2(dx, dy))) % 360.0
```
FIX (nautical "coming FROM": the azimuth FROM the AOI centre OUT TO the boundary
point, i.e. the bearing the waves arrive from = reverse the vector):
```
# Nautical "coming-from" azimuth (deg clockwise from N): bearing from the AOI
# centre toward the offshore boundary point, which is the direction the incident
# waves TRAVEL FROM. SnapWave / snapwave.bwd expects nautical coming-from
# (sfincs_snapwave.f90: input "nautical coming from", output dir = 270 - theta).
dx = float(x) - cx
dy = float(y) - cy
wd = (math.degrees(math.atan2(dx, dy))) % 360.0
```
Equivalently, keep the old vector and add 180: `wd = (old_wd + 180.0) % 360.0`.
Either form turns every edge from the wrong (going-to) value into the correct
nautical coming-from value (S edge 0->180, W edge 90->270, etc. per the table in
5.1). Also update the two misleading comments that assert the going-to azimuth "is
the convention SnapWave's add_point wd expects" (the module docstring ~L768 and the
inline comment ~L823) -- it is the OPPOSITE.

Robustness follow-up (D5, now quantified): after building the points, assert at
least one boundary point lands on a SnapWave active/open cell with depth > 1.1*hmin
(0.11 m) and ideally well offshore; if none do, raise a typed error rather than
emitting a flat-zero hm0 field. (This is worker-side, since the agent does no GIS;
specify it as a guard in the deck-build worker that consumes `snapwave_boundary`.)
