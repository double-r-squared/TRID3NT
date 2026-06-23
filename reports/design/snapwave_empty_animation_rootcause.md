# SnapWave wave-height animation: empty + static root-cause spike

Status: READ-ONLY root-cause spike (no code modified)
Symptom owner: NATE (mobile, live)
Run under analysis: case 01KVSTBCG3 / run 01KVSTC80FCYFVVP56VN7QJY5M
  (s3://grace2-hazard-runs-226996537797/01KVSTC80F.../)

## The symptom (two distinct defects, one layer)

The SFINCS + SnapWave coastal case publishes a WAVE-HEIGHT animation that
(a) is almost empty -- ~0.8% of AOI cells carry wave data (a thin scattered
nearshore band, max ~4 m), and (b) does not change frame-to-frame ("literally
nothing happening"). The FLOOD-DEPTH animation on the SAME publish path renders
fine: ~40% coverage, clearly fills inland, visibly evolves. So the publish /
rasterize path is faithful; the defect is upstream in the WAVE SOLVE.

These are TWO independent symptoms and they have (largely) different causes.
Keep them separate.

## What the live run actually contains (ground truth)

Two independent investigations downloaded and inspected the real artifacts of
run 01KVSTC80F (the 467 MB sfincs_map.nc, sfincs.stdout, and the published
COGs). They agree on every number:

- hm0 IS a genuine time-varying NetCDF variable: dims (time=289,
  nmesh2d_face=298879). So the postprocess time-index selection is CORRECT --
  it is not pulling the same timestep. (postprocess_waves.py does a real
  per-frame isel(time=t_idx).)
- The field does not change over time: only ~2 faces have temporal std
  > 0.01 m; active-cell count is frozen at ~1536 every hour; mean is identical
  to 5 dp across t0 / t144 / t288. The SOURCE field is stationary.
- Coverage is intrinsically tiny: only ~1,552 faces (0.52% of mesh; ~1.15% of
  the 134,800 SnapWave-active cells) ever have hm0 > 0.05 m. hm0 max pins at
  exactly 4.000 m == the agent's wave-Hs ceiling clamp.
- The SnapWave COMPUTATIONAL mask is NOT a thin strip: snapwavemsk active =
  134,800 cells (45.1% of mesh), comparable to the flow mask (137,163, 45.9%).
  The wave energy simply never propagated INTO that active domain.
- Published COGs match NATE's measurements exactly: wave COG = 0.75-0.76%
  valid pixels (vmax 4.0); depth COG = 40.6% valid. wave frame01 vs frame72:
  ~41 of 335,699 px change (max 0.05 m) -> visually identical.

## Defect 1 -- WHY THE FIELD IS NEARLY EMPTY (the headline; sparse + the deeper static cause)

The smoking gun is in sfincs.stdout. SnapWave prints, repeatedly (98 lines):

  ERROR SnapWave - depth at boundary input point 661738.9 3314426.2 dropped
    below 5 m: 1.79 ... Please specify input in deeper water.
  ERROR SnapWave - depth at boundary input point 652370.8 3321742.4 dropped
    below 5 m: 0.10 ... Please specify input in deeper water.

The two incident-wave boundary input points sit in 1.79 m and 0.10 m of water
(0.10 m is essentially a dry/land cell). SnapWave breaks/dissipates the incident
wave AT the boundary and clamps the input depth to 5 m for stability with a
warning -- so almost NO energy propagates into the 134,800 active cells. The
result is hm0 ~= 0 across ~98.85% of the SnapWave-active domain. This is the
sparse field.

Why the boundary landed in shallow water -- WRONG EDGE for this AOI:

- Domain is EPSG:32616 (Mexico Beach FL). The Gulf is to the SOUTH (south-edge
  mean zb ~= -8.15 m; the deepest cells are in the SW corner, zb down to
  -15.3 m). The genuinely-seaward, deep-water edge is the SOUTH/SW edge.
- But the snapwavemsk==2 boundary cells (98 of them) ended up on the EAST edge
  (x ~= 658,650-658,716), where mean zb ~= -0.99 m (nearshore) and zb ranges up
  to +1.99 m (land). The two retained synthesized points (the N and E bbox-edge
  midpoints) sit in 0.10 m and 1.79 m. The W edge midpoint WOULD have been deep
  (~ -10 m) but was not retained.

The agent synthesizes the boundary by laying ONE point per bbox EDGE MIDPOINT,
inset only 2% (model_flood_scenario.py:_synthesize_parametric_wave_boundary,
lines 792-805), with NO bathymetric awareness. The deck worker's
derive_seaward_open_boundary_polygon then keeps whichever of those points sits
"offshore of the active extent" -- and here it kept the two SHALLOWEST (the
N=0.1 m and E=1.8 m edges), not the deep SW/S edge. So the offshore forcing is
degenerate and the wave field is born nearly empty.

This is the SnapWave analogue of the standalone-SWAN bathy bug NATE previously
fixed: the boundary forcing lands on shallow/intertidal/land cells, so the field
is flat. Reference doc D5 / Sec 5.2 (reports/references/swan_snapwave_correct_setup.md)
predicted exactly this: the boundary MUST sit in depth >> the surf zone, ideally
tens of metres.

RULED OUT for the sparseness:
- The "physically-expected thin nearshore footprint" hypothesis is contradicted
  by the live data: the SnapWave-active mask is 45% of the mesh (NOT a thin
  1.5-cell strip), and the stdout explicitly reports the boundary energy dying at
  the input points. The field is empty because the forcing is degenerate, not
  because nearshore waves naturally only occupy 0.8%.
- The bathy-sign / UTM-vs-lon/lat / CRS mismatch (the standalone-SWAN defect) is
  NOT present on this path. The deck samples topobathy in the grid CRS and z is
  positive-up; snapwave_depth = zs - snapwave_z gives correct positive depth in
  water. No double-negation.
- The 180-deg boundary-direction bug is ALREADY FIXED and correct
  (model_flood_scenario.py:836-838: wd = atan2(dx,dy) with (dx,dy) =
  point-minus-centre = the nautical "coming-from" seaward bearing). A correct
  direction on a boundary stranded in 1 m of water still produces a dead field.
- The postprocess / rasterize path (postprocess_waves.py, cog.py,
  sfincs_reader.py) is faithful and shared with the working depth layer. NO
  change needed there. The 0.05 m NODATA floor is identical to depth's.

## Defect 2 -- WHY EVERY FRAME LOOKS IDENTICAL (the no-animation symptom)

Even if the field were full, the animation would be near-static, for a separate
reason: the deck NEVER sets the SnapWave coupling cadence keyword `dtwave`.

- build_deck() step 8 (entrypoint.py:1534-1546) sets v.snapwave=True and the
  nine snapwave_* knobs via snapwave_inp_overrides(), but NEVER sets v.dtwave.
- snapwave_inp_overrides() (entrypoint.py:444-470) lists gamma/gammaig/gammax/
  dtheta/hmin/fw0/crit/igwaves/nrsweeps/use_herbers -- and OMITS dtwave.
- So SFINCS falls back to its built-in default dtwave = 3600.0 s (SnapWave is
  re-solved only ONCE PER HOUR).
- Meanwhile the map-output cadence is FINE: the coastal path threads
  output_dt_s = max(60, interval*60) = 300 s into the deck
  (model_flood_scenario.py:2870-2874), so v.dtout = 300 s
  (entrypoint.py:1509-1512).
- Arithmetic: 3600 / 300 = 12 consecutive output frames carry the BYTE-IDENTICAL
  hm0 field. (Identical published TRIPLETS were also observed -- consistent with
  this hourly-resolve / fine-output mismatch plus any per-frame duplication in
  the wave postprocessor.)
- The proven spike deck DID set dtwave = 1800.0
  (services/workers/sfincs_quadtree_spike/deck_cht/sfincs.inp:42). The
  production worker dropped it. Confirmed: `dtwave` appears in the repo ONLY in
  that spike .inp -- nowhere in the production worker or agent.

Compounding the staticness: the incident wave boundary is CONSTANT-in-time. The
agent emits ONE uniform Hs/Tp/wd per offshore point (no storm envelope), and the
worker calls add_point(hs,tp,wd,ds) uniform-in-time (entrypoint.py:1518-1530).
So even at the hourly re-solves the only thing that can move hm0 is the surge
changing nearshore depth -- a weak effect over offshore depths of tens of metres.

NOTE on causal ordering: in THIS run, Defect 1 dominates -- because the boundary
energy is dead, there is essentially nothing to animate regardless of dtwave.
But Defect 1 and Defect 2 are independent: fixing only the boundary placement
would surface a field that still updates only hourly (a stepped, choppy
animation); fixing only dtwave would animate a still-near-empty field. BOTH must
be fixed for a real, filled, evolving wave animation.

## Ranked fix plan

1. (PRIMARY -- fixes the empty field, Defect 1) Depth-aware offshore boundary
   placement. In model_flood_scenario._synthesize_parametric_wave_boundary
   (lines 760-867), stop laying one point per 2%-inset bbox edge midpoint.
   Instead sample the topobathy DEM and place the offshore point(s) on the
   genuinely-seaward side -- the edge / cells with the deepest (most-negative)
   mean zb, target depth >> 5 m (ideally >= 10 m). Push the candidate seaward
   along the offshore bearing until the sampled depth clears a min-depth gate;
   drop edges whose seaward extent never reaches deep water. If NO candidate
   point lands deeper than ~5 m, raise a typed error rather than running a
   flat-zero wave field. For this AOI that selects the SOUTH/SW (Gulf) edge,
   not the east/north nearshore edges.

2. (PRIMARY -- fixes the no-animation, Defect 2) Set `dtwave` in the deck. In
   build_deck() step 8 add `v.dtwave = out_dt` (or min(out_dt, 600.0)) and add
   `snapwave_dtwave` to snapwave_inp_overrides() + the build_spec so the agent
   can pin it. This makes SnapWave re-solve every output frame instead of
   hourly. Verify by diffing two consecutive wave_height_frame_*.tif from a
   re-run -- they must no longer be byte-identical.

3. (SECONDARY -- depth-aware worker selection) Verify / fix
   derive_seaward_open_boundary_polygon in the deck-build worker so it selects
   the DEEPEST domain edge (not the nearest-to-incident-points edge). The
   worker chose the two SHALLOWEST of the four synthesized points; the
   selection criterion should be max-depth, well seaward of the surf zone.

4. (SECONDARY -- realism / visible time-variation) Make the incident wave
   boundary time-varying: ramp Hs/Tp on the same raised-cosine storm envelope
   the surge forcing uses, so hm0 grows and recedes over the storm independent
   of the weak surge-depth coupling. Otherwise even a correctly-placed, finely
   re-solved field will barely move.

5. (HONESTY GATE -- prevents the silent failure recurring) Add a worker-side
   gate: if SnapWave emits the "depth at boundary ... below 5 m" warning, OR the
   resulting hm0 field covers < a few % of the SnapWave-active cells, FAIL or
   FLAG the wave layer. A "modeled" wave envelope at 0.5% coverage must not read
   status=ok (render-honesty floor).

6. (COSMETIC) Investigate/remove any identical-triplet frame duplication in the
   wave postprocessor so adjacent published frames reflect distinct timesteps.
   Lower priority -- once dtwave is fine and the field fills, this is minor.

NOT needed: any bathy-sign / UTM / CRS change, any further direction (wd) fix,
or any change to postprocess_waves.py / cog.py / sfincs_reader.py -- those paths
are correct and shared with the working depth layer.

## Effort

Medium. Two focused agent-side code edits carry the fix:
- Defect 2 (dtwave) is a near one-liner in the worker + a knob in
  snapwave_inp_overrides / build_spec: ~0.5 day incl. test.
- Defect 1 (depth-aware boundary placement) is the real work: sample the DEM in
  _synthesize_parametric_wave_boundary, add a min-depth seaward search, and
  align derive_seaward_open_boundary_polygon to pick the deepest edge:
  ~1-1.5 days incl. unit tests and a re-run.
- Honesty gate + storm-envelope ramp + triplet cleanup are smaller follow-ons:
  ~1 day total.
Plus one live Batch re-run of the Mexico Beach coastal case to confirm hm0 fills
the active mask AND consecutive frames differ. No rendering / infra changes.

## Key code references

- services/workers/sfincs_deckbuilder/entrypoint.py
  - :444-470  snapwave_inp_overrides -- OMITS dtwave
  - :1378-1454  SFINCS + SnapWave mask build (zmin/zmax defaults -1000/+2.0)
  - :1484-1494  seaward-boundary repair warnings (hm0 may stay flat)
  - :1509-1512  v.dtout = out_dt (fine 300 s)
  - :1518-1530  add_point -- uniform-in-time boundary
  - :1534-1546  step 8 SnapWave keywords -- v.dtwave NEVER set
- services/agent/src/grace2_agent/workflows/model_flood_scenario.py
  - :760-867  _synthesize_parametric_wave_boundary -- 2%-inset edge-midpoint
    placement (no bathy awareness); :836-838 the (correct) 180-deg wd fix
  - :2870-2874  output_dt_s threading -> 300 s coastal cadence
- services/workers/sfincs_quadtree_spike/deck_cht/sfincs.inp:42  dtwave=1800.0
  (the ONLY dtwave in the repo -- the proven spike had it; production dropped it)
- reports/references/swan_snapwave_correct_setup.md  (D5 / Sec 5.2: boundary
  must sit in deep water)
- Live: s3://grace2-hazard-runs-226996537797/01KVSTC80F.../sfincs.stdout
  (98 "depth at boundary input point ... below 5 m" errors), sfincs_map.nc
  (hm0 stationary, 0.52% coverage, snapwavemsk 45% active).