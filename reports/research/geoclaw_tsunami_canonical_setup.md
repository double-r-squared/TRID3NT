# GeoClaw / Clawpack tsunami inundation: canonical setup, gap analysis, and fix plan

Status: RESEARCH + PLAN (no engine code changed in this pass)
Author: agent research pass, 2026-06-30
Primary sources: the Clawpack GeoClaw examples installed locally at
`/home/nate/clawpack-src/geoclaw/examples/tsunami/*`, a WORKING local proof run at
`/home/nate/geoclaw_proof/tsunami/`, and clawpack.org docs (topo, dtopo).

---

## 0. TL;DR (the one paragraph)

A GeoClaw tsunami is, end to end: (1) a COARSE deep-ocean bathymetry grid where
the sea floor is genuinely NEGATIVE (z < 0), optionally with a NESTED finer
coastal topo file layered on top; (2) an Okada fault placed OFFSHORE over deep
water (tens of km out, fault DEPTH ~ tens of km); (3) `sea_level = 0` so every
cell with B < 0 initializes wet with depth `h = -B`, which is what makes the
domain hold a real water column ("Total mass" large and positive); (4) the Okada
dZ displaces that water column to seed the wave; (5) AMR refines the coast to
tens of metres for run-up. Our pipeline reproduces 1-5 in code, and our OWN
working proof (`/home/nate/geoclaw_proof/tsunami`) confirms the SOLVER and the
synthetic Okada source are correct. The break is ONE link: the bathymetry that
reaches the solver is FLAT ~0 m instead of real negative depth, so the domain
holds almost no water (Total mass 112k vs the proof's 6.2e9), the source has no
column to displace, and nothing inundates. Every other "fix" (domain extension,
offshore-source projection, CRS, topotype-3) is downstream of and dependent on
that one number being negative. Fix the bathymetry sourcing and the rest already
works.

---

## 1. Canonical GeoClaw tsunami setup (from primary sources)

### 1.1 The two reference archetypes

GeoClaw ships two distinct tsunami archetypes, and they answer different
questions. Both matter for us.

- DEEP-OCEAN PROPAGATION + buoy validation -> `examples/tsunami/chile2010`.
  Validates the open-ocean wave against a DART buoy. Big domain, coarse grid,
  ONE coarse global bathymetry file. Does NOT resolve coastal run-up.
- COASTAL RUN-UP / inundation -> `examples/tsunami/eta_init_force_dry` and
  `radial-ocean-island-fgmax`. NESTED topo (coarse ocean + fine shore), fgmax
  monitoring of max depth/speed/arrival, force_dry / variable eta init for land
  below sea level behind dikes.

Our product wants the SECOND (inundation), but our current deck is shaped like a
hybrid of the two. The canonical numbers below are taken verbatim from the
installed examples.

### 1.2 chile2010 `setrun.py` (actual numbers)

Spatial domain (`clawdata.lower/upper`):

| field | value | meaning |
|---|---|---|
| lower[0], upper[0] | -120.0, -60.0 | 60 deg of longitude (~5500 km) |
| lower[1], upper[1] | -60.0, 0.0 | 60 deg of latitude (~6600 km) |
| num_cells | 30 x 30 | base grid => ~2 deg (~200 km) per base cell |

Note the domain is ENORMOUS relative to the source. The fault is at lon -72.668,
lat -35.826; the domain reaches ~48 deg of longitude west of it, i.e. the wave
propagates across the open Pacific to the DART buoy at (-86.392, -17.975).

GeoClaw / geo_data (`setgeo`):

```
geo_data.gravity            = 9.81
geo_data.coordinate_system  = 2          # spherical lat/lon
geo_data.earth_radius       = 6367.5e3
geo_data.sea_level          = 0.0        # still water at z = 0
geo_data.dry_tolerance      = 1.e-3
geo_data.friction_forcing   = True
geo_data.manning_coefficient= 0.025
refinement_data.wave_tolerance = 1.e-1
```

Topography (ONE file, topotype 2):

```
topo_path = etopo10min120W60W60S0S.asc   # ETOPO, 10 arc-minute (~18 km)
topo_data.topofiles.append([2, topo_path])
```

The topo is downloaded from the GeoClaw topo repository
(`http://depts.washington.edu/clawpack/geoclaw/topo/etopo/...`). It is a real
topo-BATHY grid: sea floor negative, land positive. This is the load-bearing
fact -- the ocean cells are genuinely negative.

dtopo / Okada source (`make_dtopo` in `maketopo.py`), a SINGLE subfault:

```
strike = 16.   length = 450.e3   width = 100.e3   depth = 35.e3   # 35 km deep
slip   = 15.   rake   = 104.     dip   = 14.
longitude = -72.668   latitude = -35.826
coordinate_specification = "top center"
# Mw printed ~ 8.8
x = linspace(-77, -67, 100);  y = linspace(-40, -30, 100);  times=[1.]
fault.create_dtopography(x, y, times);  dtopo.write(fname, dtopo_type=3)
dtopo_data.dtopofiles.append([3, dtopo_path]);  dtopo_data.dt_max_dtopo = 0.2
```

AMR:

```
amrdata.amr_levels_max      = 3
amrdata.refinement_ratios_x = [2, 6]      # cumulative factor 12
amrdata.refinement_ratios_y = [2, 6]
amrdata.refinement_ratios_t = [2, 6]
# regions pin level 3 over two coastal/source boxes:
regions.append([3, 3, 0.,    10000., -77,-67,-40,-30])
regions.append([3, 3, 8000., 26000., -90,-80,-30,-15])
gauges.append([32412, -86.392, -17.975, 0., 1.e10])   # DART buoy
```

### 1.3 eta_init_force_dry `setrun.py` (the coastal/nested archetype)

This is the example that matters for INUNDATION. Key differences from chile2010:

- TWO topo files, both topotype 3, layered coarse-then-fine:
  ```
  topofiles.append([3, topodir + '/topo_ocean.tt3'])   # coarse ocean bathy
  topofiles.append([3, topodir + '/topo_shore.tt3'])   # fine nearshore topo
  ```
  GeoClaw builds "a single piecewise-bilinear function from the union of the
  topo files, using the best information available in regions of overlap" and
  "the best information is assumed to come from the topofile with the finest
  resolution that covers a point" (clawpack.org/topo.html). So you supply a
  cheap coarse ocean grid for the deep water PLUS a fine grid only where you
  need run-up; finest wins automatically.
- `sea_level = 0.0`, plus a `set_eta_init` variable-eta surface and a
  `force_dry` list (`force_dry.tend = 7*60.`, reading
  `input_files/force_dry_init.tt3`) so cells that are below the eta level but
  behind a dike start DRY instead of wet.

### 1.4 The canonical doc facts (clawpack.org)

Topography (clawpack.org/topo.html):
- "More than one topo file can be specified that might cover overlapping regions
  at different resolutions." Finest-resolution file wins in overlap (explicit
  preference ordering added in v5.13.0 for ties).
- topotype 1 = x,y,z lines; topotype 2 = 6-line header + column of z; topotype 3
  = 6-line header + ESRI-ASCII rows of z; topotype 4 = NetCDF.
- "sea_level = 0" with "z<0 corresponds to subsurface bathymetry and z>0 to
  topography above sea level." Still-water IC fills `h = max(0, sea_level - B)`,
  so a cell only holds water if B < sea_level. Refinement preserves the
  ocean-at-rest steady state (fine cells average to the coarse value).

dtopo / Okada (clawpack.org/dtopo.html):
- A fault is "a finite set of subfaults, each of which is a planar rectangle"
  in the Okada half-space model; dZ is the static seafloor deformation.
- "the entire water column in each grid cell is then moved vertically by the
  same amount ... creating a disturbance of the sea surface, which in turn
  results in tsunami waves." => if there is NO water column (dry cell), the
  source produces NO wave.
- dtopotype 3 (recommended) = topotype-3-like header with mt time slices.

### 1.5 Synthesized canonical recipe (what "correct" looks like)

1. BATHYMETRY: at least one topo file whose ocean cells are genuinely negative.
   Open-ocean propagation can use a single coarse global grid (ETOPO ~ 18 km is
   fine for chile2010's buoy check). Coastal RUN-UP needs a NESTED finer coastal
   topo on TOP of the coarse ocean (force_dry pattern). Multiple files at
   different resolutions is the NORM, not the exception; finest wins.
2. SOURCE: Okada fault OFFSHORE over deep water, fault DEPTH tens of km
   (chile = 35 km), placed where there is a deep negative-B water column. The
   dtopo grid is authored with `fault.create_dtopography` / `create_dtopo_xy`
   and written dtopotype 3.
3. IC: `sea_level = 0`, `coordinate_system = 2`, `dry_tolerance = 1e-3`. The
   wet area and "Total mass" are entirely determined by how much of the domain
   has B < 0.
4. DOMAIN + AMR: domain large enough to contain source -> coast; coarse base
   grid over the propagation domain; AMR (3+ levels, increasing ratios like
   [2,6]) pinned by `regions` over the coastal AOI to tens of metres for run-up;
   fgmax grid to capture max depth/speed/arrival.

---

## 2. Our setup, mapped to the canonical recipe

Files read: `services/agent/src/grace2_agent/workflows/run_geoclaw.py`,
`.../model_dambreak_geoclaw_scenario.py`,
`services/workers/geoclaw/setrun_builder.py`,
`services/workers/geoclaw/entrypoint.py`,
`services/agent/src/grace2_agent/tools/fetch_topobathy.py`.

What we get RIGHT (verified against the examples):

- `coordinate_system = 2`, `sea_level = 0`, `dry_tolerance = 1e-3`, gravity,
  earth_radius -- all match chile2010 (`setrun_builder.render_setrun_py` /
  `geoclaw.data`).
- Single-subfault Okada via `dtopotools`, dtopotype 3, authored in a generated
  `maketopo.py`, with `coordinate_specification` set and
  `create_dtopo_xy(dx=1/60., buffer_size=2.0)` -- canonical helper usage.
- AMR ratios INCREASING toward the finest level (`_refinement_ratios`), regions
  pinning the finest level over the AOI, an fgmax grid, a coastal gauge -- this
  is the force_dry/radial-ocean-island inundation archetype, done correctly.
- Offshore domain extension (`plan_geoclaw_domain`) and a coarse-base +
  nested-AMR cost plan (`plan_geoclaw_grid`) -- conceptually the right shape.
- Topotype-3 normalization in the worker (`_convert_one_topo_to_topotype3`) and
  reprojection to EPSG:4326 (`reproject_dem_to_4326`) -- both necessary and
  correct in isolation.

The SMOKING GUN -- our own working proof. `/home/nate/geoclaw_proof/tsunami` is
the IDENTICAL setrun_builder deck (same -85.6..-85.4 / 29.9..30.1 domain, same
30x30 base, same synthetic Mw 8.0 Okada at -85.5/30.0 depth 10 km), differing
ONLY in `topo.asc`: the proof's topo is a synthetic linear ramp from -40 m
(offshore) to +15 m (inland). Result:

```
fort.amr:  time t = 0   total mass = 0.622e10   (6.2e9)
           ... mass GROWS each step (wave builds + inundates)
gauge00001: eta column reaches ~0.74 m (a real wave signal)
```

So with REAL negative bathymetry the solver, the synthetic source, the tiny
un-extended domain, and `sea_level=0` ALL work and produce inundation. The proof
isolates the defect to the bathymetry that reaches the solver in production.

---

## 3. Gap analysis -- why production ocean is flat ~0 and the source lands at -0.7 m

### GAP 1 (ROOT CAUSE): the bathymetry pipeline delivers a flat near-zero ocean

The production DEM path is
`model_dambreak_geoclaw_scenario._fetch_topo_for_geoclaw` ->
`fetch_topobathy(domain_bbox)` with `fetch_dem(10m)` as fallback. Two distinct
ways this yields a flat ~0 ocean, both consistent with the reported symptoms
(ocean flat 0, deepest cell only -0.7 m, Total mass 112k):

(a) fetch_dem (3DEP land-only) fallback wins. If `fetch_topobathy` raises for the
   offshore-extended `domain_bbox` (it emits a UTM COG, has had MergeError /
   orientation / reprojection trouble, and `reproject_dem_to_4326` runs right
   after), the composer silently falls back to `fetch_dem(10m)`, which is a LAND
   DEM: ocean is nodata. The worker then runs
   `_convert_one_topo_to_topotype3(..., offshore=True)`, which fills nodata
   cells with `np.nanmin(band)` -- the deepest LAND value, e.g. a coastal marsh
   at ~-0.7 m NAVD88. The entire ocean becomes a FLAT ~-0.7 m sheet. That is
   exactly "ocean samples as flat 0" + "deepest cell -0.7 m" + tiny mass.

(b) fetch_topobathy succeeds but contributes no real bathymetry. The merge
   precedence in `_build_merged_topobathy` is `[ETOPO?, 3DEP land, CUDEM]` with
   LAST winning. ETOPO (real negative ocean) is only added when CUDEM has ZERO
   coverage; for a Gulf AOI CUDEM "has coverage," so ETOPO is NOT used. If the
   selected CUDEM tiles do not actually paint the offshore-EXTENDED domain (the
   domain was grown ~0.1-0.2 deg past the AOI, beyond the staged CUDEM
   footprint), the only source that covers the open-ocean part of the domain is
   3DEP land = nodata offshore -> same nanmin fill -> flat ocean.

Either branch defeats the canonical requirement (ocean cells must be genuinely
negative). The `offshore` nodata-fill heuristic (`nanmin`) is itself the trap:
when the only data is a land DEM, "deepest available" is a near-zero coastal
land value, NOT real bathymetry. The fill manufactures a flat fake ocean that
SILENTLY passes every downstream check (it is "wet" by a hair, so no error
fires).

Canonical contrast: chile2010 and force_dry NEVER fill an unknown ocean from a
land DEM. They start from a topo grid that is bathymetric by construction
(ETOPO / topo_ocean.tt3). The ocean depth is DATA, never a fill value.

### GAP 2: source placement is hostage to GAP 1

`resolve_offshore_source` looks for the deepest cell with `elev < 0`. On a flat
~-0.7 m DEM the "deepest" cell is -0.7 m at the shoreline, so the Okada source is
planted at the waterline over a 0.7 m column instead of offshore over deep water.
The function is correct; it is starved of real bathymetry. With the proof's real
ramp it would have found the -40 m edge. So this is a SYMPTOM of GAP 1, not an
independent bug.

Secondary: fault DEPTH default is 10 km (`_SYNTHETIC_FAULT_DEPTH_KM`) vs
chile2010's 35 km. 10 km is shallow/aggressive for a subduction megathrust; fine
for a demo but worth noting against the canonical number.

### GAP 3: single-grid topo vs nested coarse-ocean + fine-shore

We hand GeoClaw ONE topo file (`topo.asc`, plus optional `extra_topo_files` that
the composer does not populate for tsunami). To keep that single grid tractable
the worker integer-DECIMATES any DEM finer than 2000 cells/axis
(`_GEOCLAW_TOPO_MAX_CELLS_PER_AXIS`). Over a ~0.6 deg offshore-extended domain
that is ~33 m cells -- so even when CUDEM bathymetry IS present we throw away the
nearshore resolution that run-up needs. The canonical pattern is the opposite:
a COARSE ocean file (cheap, big) PLUS a SEPARATE fine coastal file (small,
high-res), with GeoClaw choosing finest-in-overlap. We are not using GeoClaw's
native multi-file resolution machinery at all; we pre-flatten to one grid.

### GAP 4: domain may not reach genuinely deep water

`plan_geoclaw_domain` pads the AOI by `max(span, 0.1 deg)` (~11 km). For a
shallow shelf (the Gulf near Mexico Beach is ~ -5 to -20 m for tens of km) even
a correct bathymetry grid over that pad would be shelf, not deep ocean. chile2010
spans ~5000 km to reach the source over a 4 km abyssal column. Our extension is
right in spirit but likely under-sized for a real deep-water source on a
wide-shelf coast. (Lower priority than GAP 1: a shallow shelf still inundates if
the bathymetry is real and the source sits in it; the proof inundated over a
-40 m edge.)

### GAP 5: honesty gap -- a flat-fake ocean reads as success

`fetch_topobathy` DOES emit a `fallback_warning` when it degrades to land-only
(`bathymetry_present=False`), but `_fetch_topo_for_geoclaw` discards the result
object and keeps only `.uri`, so the warning never reaches the GeoClaw composer
or the user. And the worker's `nanmin` fill turns a land-only DEM into a
plausibly-wet ocean, so the run reports "complete" with a positive (tiny) mass.
This violates the render-chokepoint / honesty-floor norm: a modeled tsunami with
no real bathymetry should be flagged, not silently zero-inundation.

---

## 4. Concrete, prioritized fix plan

Priority is strict: GAP 1 is the whole ballgame. Do P0 first and re-test before
touching anything else -- the proof shows the rest already works once the ocean
is negative.

### P0 -- Guarantee a genuinely-negative ocean reaches the solver

P0.1 Make ETOPO the ALWAYS-ON bathymetric BASE for tsunami, not a CUDEM-absent
   fallback. For an offshore scenario, fetch the seamless global ETOPO 2022 bed
   over the FULL computational domain UNCONDITIONALLY and lay it down as the
   base layer, then paint CUDEM (where present) and 3DEP land on top. ETOPO is
   negative offshore by construction, so the open-ocean part of the domain can
   never be a land-DEM fill. (In `fetch_topobathy`, drop the `if not
   cudem_vsicurl` guard around `_select_etopo_tiles` for the tsunami caller, or
   add a `force_bathy_base=True` parameter the GeoClaw composer passes.)

P0.2 Kill the land-DEM ocean fill for offshore runs. In
   `entrypoint._convert_one_topo_to_topotype3`, the `offshore` branch must NOT
   invent ocean by `nanmin` of a land DEM. Either (a) require a real
   bathymetric source to cover the ocean (P0.1 guarantees this) and fill only
   true warp-corner NaNs, or (b) refuse to fill and emit a typed error when the
   DEM has no cell below, say, -2 m over the offshore region. A flat near-zero
   "ocean" must become a loud failure, never a silent pass.

P0.3 Validate the staged topo before the solve. Add an explicit gate (worker or
   composer) that reads the final `topo.asc` and asserts: fraction of cells with
   B < -2 m exceeds a threshold over the offshore region, and `min(B)` is deeper
   than a few metres. If not, fail with `GEOCLAW_BATHYMETRY_FLAT` (honest typed
   error) rather than running a zero-inundation solve. This is the single check
   that would have caught all 8 reactive symptoms at once.

### P1 -- Source + domain robustness (after P0 proves a real ocean)

P1.1 With real bathymetry, `resolve_offshore_source` will find deep water; keep
   it. Add a guard: if the deepest cell found is shallower than a floor (e.g.
   -10 m), refuse and surface a warning rather than seeding a source over a
   shelf puddle.

P1.2 Reconsider fault depth default (10 km -> align nearer the canonical 35 km
   for a megathrust, or make it scenario/Mw-scaled). Lower priority; it is a
   realism nudge, not a zero-inundation cause.

P1.3 Size `plan_geoclaw_domain` to reach a target offshore DEPTH, not just a
   fixed angular pad: extend seaward until ETOPO shows B below, say, -200 m (or
   cap at a max span). This guarantees a real deep-water column for the source
   on wide-shelf coasts.

### P2 -- Adopt GeoClaw's native nested-topo (correctness + resolution)

P2.1 Stop pre-flattening to one decimated grid. Stage TWO topo files like
   force_dry: a coarse ETOPO ocean grid (`topo_ocean`, whole domain, ~450 m) and
   a fine coastal grid (`topo_shore`, AOI only, CUDEM ~3-10 m), both topotype 3,
   appended coarse-then-fine. The composer already supports `extra_topo_files`
   end to end (`stage_geoclaw_manifest` -> build_spec -> `render_setrun_py`
   topo_lines); wire the tsunami path to populate it instead of merging into one
   COG. Let GeoClaw pick finest-in-overlap. This both removes the decimation
   resolution loss AND removes the heterogeneous-CRS merge that has been a source
   of failures.

### P3 -- Honesty + observability

P3.1 Propagate `TopobathyResult.bathymetry_present` / `.fallback_warning` from
   `_fetch_topo_for_geoclaw` into the composer; if bathymetry is absent or
   global-fallback, narrate it and (for absent) refuse the tsunami run with a
   typed error. Never let a no-bathy tsunami read `status=ok`.

P3.2 Log the staged-topo stats (min/max B, wet fraction, Total mass at t0 from
   `fort.amr`) into the completion manifest so a flat ocean is visible in
   telemetry without re-deriving it at 4am.

---

## 5. Verdict -- is a realistic tsunami inundation demo achievable on open data?

Two honest tiers:

- ACHIEVABLE NOW (demo-grade), with P0+P2: yes. ETOPO 2022 (~450 m, global,
  seamless topo-bathy, negative offshore) as the coarse ocean base GUARANTEES a
  real negative water column and a non-zero, physically-plausible wave -- the
  proof inundated over a synthetic -40 m ramp, and ETOPO gives a real one
  everywhere. For the US coast, layering CUDEM 1/9 arc-second (~3 m, NAVD88) as
  the nested SHORE file gives genuinely good nearshore run-up where it exists.
  That is a legitimate, defensible demo: real global bathymetry + real local
  topobathy, GeoClaw choosing finest-in-overlap exactly as the canonical
  examples do.

- NOT claimable as a VALIDATED hindcast: ETOPO at 450 m is too coarse to resolve
  true run-up heights, and our Okada source is a single synthetic subfault
  (honestly bannered as NON-SITE-SPECIFIC). chile2010 itself validates only an
  offshore DART buoy, not coastal inundation, precisely because coastal run-up
  needs fine nearshore topo and a real inversion. So: a realistic-LOOKING,
  physically-consistent inundation demo is achievable on open data; a
  quantitatively validated coastal run-up is NOT without (a) a real published
  source model and (b) fine nearshore topobathy (CUDEM where available; a
  curated DEM otherwise).

Bottom line: the engine, the deck author, the synthetic source, and the AMR/
fgmax inundation machinery are already correct (the proof run is the evidence).
The ENTIRE production failure is one missing guarantee -- that the ocean cells
handed to GeoClaw are real negative bathymetry, not a land-DEM zero/near-zero
fill. Implement P0 (ETOPO-always base + kill the land-fill + a flat-ocean gate)
and the demo inundates; P2 (native nested topo) makes it look good; P1/P3 make
it robust and honest. No more reactive single-symptom patching: P0.3's
flat-ocean gate is the canonical guardrail that converts "silent zero
inundation" into "loud, named failure at the one place it matters."

---

## Appendix A -- exact source locations (for the implementer)

- Land-DEM ocean fill (GAP 1 / P0.2):
  `services/workers/geoclaw/entrypoint.py` `_convert_one_topo_to_topotype3`,
  the `offshore` branch -> `fill = float(np.nanmin(band)) if offshore ...`.
- ETOPO-only-as-fallback (GAP 1 / P0.1):
  `services/agent/src/grace2_agent/tools/fetch_topobathy.py`
  `_fetch_topobathy_bytes_and_flags`, `if not cudem_vsicurl:` guarding
  `_select_etopo_tiles`; merge precedence in `_build_merged_topobathy`
  (`sources_in_precedence = etopo + land + cudem`).
- Warning dropped (GAP 5 / P3.1):
  `model_dambreak_geoclaw_scenario._fetch_topo_for_geoclaw` keeps only `.uri`.
- Source placement (GAP 2 / P1.1):
  `run_geoclaw.resolve_offshore_source` (`wet = valid & (elev < 0.0)`).
- Domain extent (GAP 4 / P1.3): `run_geoclaw.plan_geoclaw_domain`
  (`pad = max(span_x, span_y, 0.1)`).
- Single-grid decimation (GAP 3 / P2):
  `entrypoint._GEOCLAW_TOPO_MAX_CELLS_PER_AXIS = 2000`; nested-topo plumbing
  already present via `extra_topo_files` in `run_geoclaw.stage_geoclaw_manifest`
  and `setrun_builder.render_setrun_py` topo_lines.
- Fault depth default (P1.2):
  `setrun_builder._SYNTHETIC_FAULT_DEPTH_KM = 10.0` (canonical chile = 35 km).

## Appendix B -- canonical vs ours, side by side

| dimension | chile2010 (deep-ocean) | force_dry (coastal) | OUR production | OUR proof (works) |
|---|---|---|---|---|
| bathymetry | 1x ETOPO 10' (neg ocean) | ocean.tt3 + shore.tt3 nested | 1x merged COG, ocean FLAT ~0 | 1x synthetic ramp -40..+15 |
| ocean cells negative? | YES (data) | YES (data) | NO (land-fill) | YES |
| Total mass t0 | large | large | 112k (tiny) | 6.2e9 |
| source | offshore, depth 35 km | n/a | shoreline -0.7 m (starved) | centroid, depth 10 km, over -40 m |
| sea_level | 0 | 0 | 0 | 0 |
| coord_system | 2 | 2 | 2 | 2 |
| AMR | 3 lvl [2,6] + regions | nested + force_dry + fgmax | cost-planned + regions + fgmax | 2 lvl [2] + region + fgmax |
| inundates? | (buoy) | YES | NO | YES |
