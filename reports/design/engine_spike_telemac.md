# Engine Spike: TELEMAC-2D (open unstructured-mesh hydraulics)

Research spike for adding the open-source TELEMAC-2D shallow-water hydraulics
engine to GRACE-2 as the SECOND unstructured-mesh engine (after SFINCS quadtree).
Grounded against primary sources (opentelemac.org official docs/wiki/manuals,
conda-forge, the published case-study literature) AND against the live GRACE-2
solver seam (the GeoClaw worker is the closest structural analogue and is cited
throughout as the integration template).

ASCII only. No em/en dashes, no unicode arrows. Status: design + verdict only,
no code in this doc.

---

## 0. Verdict

**GO_WITH_CAVEATS.**

TELEMAC-2D passes the hard gate cleanly: it runs fully HEADLESS on Linux on a
Batch container (no GUI, no Windows-only license server, free GPL software), and
the run is LLM-cloud-drivable from a templatable build_spec (a plain-text `.cas`
steering file + an unstructured `.slf` mesh + a `.cli` boundary file, all
machine-authorable). It is a strong physics step UP from SFINCS/GeoClaw: a true
2D finite-element/finite-volume solver of the full nonlinear shallow-water
equations on an UNSTRUCTURED triangular mesh, with native rainfall-runoff
(`RAIN OR EVAPORATION`), Strickler/Manning/Chezy friction, and liquid-boundary
hydrographs - the canonical research-grade open hydraulics engine (EDF R&D /
the TELEMAC-MASCARET consortium).

The caveats - none fatal, all manageable, and each is the REASON this is
GO_WITH_CAVEATS rather than a clean GO:

1. **The mesh is the cost.** Unlike GeoClaw (structured topo grid authored from
   a DEM in-worker) and SFINCS-regular, TELEMAC needs a genuine unstructured
   triangular mesh PLUS a matching boundary-condition file. This is the same
   class of work the SFINCS quadtree deck-builder already does, but the mesh
   generator (Gmsh, open, Python-API-drivable) is a NEW heavy dependency and the
   `.slf` geometry + `.cli` boundary authoring is the bulk of the build effort.
   This is exactly the "2nd unstructured-mesh engine" trigger in the
   adaptive-mesh-budget memo - the moment to GENERALIZE the element-cap pattern,
   not pre-abstract it (see section 5).

2. **GPL licensing.** TELEMAC-MASCARET is GPL (v2/v3). It MUST stay arms-length
   in a dedicated Batch worker image and NEVER enter the agent venv - identical
   to the cht_sfincs GPL-isolation seam already in `solver.py`
   (`SFINCS_DECKBUILDER_SOLVER`). The agent only composes a build_spec JSON and
   submits over the Batch + S3 seam; it never imports a TELEMAC symbol.

3. **Container weight + MPI.** The honest install is the conda-forge
   `opentelemac` package (Linux-only; ships the compiled solvers + the Python
   `pytel`/`telapy` scripts + the SELAFIN tooling) on a micromamba base. This is
   heavier than a pure-pip worker but LIGHTER and far more reproducible than the
   GeoClaw compile-at-runtime approach (no per-solve gfortran build). Open MPI is
   bundled; v0.1 runs SERIAL (`--ncsize` unset / 1) to dodge the MPI-domain-split
   complexity, scaling to parallel later via the existing compute-class ladder.

4. **Overlap with existing engines.** TELEMAC-2D's riverine/pluvial/estuarine
   coverage OVERLAPS SFINCS (compound coastal) and SWMM (urban pipe network). The
   honest differentiation is: TELEMAC is the engine for FLUVIAL / RIVERINE flood
   routing on a conforming unstructured mesh (channel + floodplain), tidal/
   estuarine hydrodynamics, and dam-break on a fitted mesh - where SFINCS's
   structured/quadtree grid is too coarse to resolve channel geometry and SWMM's
   pipe-network abstraction does not apply. Scope it to that lane to avoid being
   a worse SFINCS.

If those four are accepted, TELEMAC is the highest-value open engine to add for
fluvial hydraulics and the natural carrier for generalizing the mesh-budget
pattern. The recommended sequencing is the ordered job list in section 6.

---

## 1. What TELEMAC-2D is, and why it clears the hard gate

TELEMAC-2D is the 2D hydrodynamics module of the open TELEMAC-MASCARET system
(EDF R&D, since 1987; open-sourced 2010; GPL). It solves the depth-averaged
free-surface shallow-water (Saint-Venant) equations by finite-element /
finite-volume methods on an UNSTRUCTURED triangular mesh, producing water depth,
free-surface elevation, and depth-averaged velocity at every mesh node and time
step. It is the open-source peer of the premium engines surveyed in the TUFLOW
spike, and unlike TUFLOW it is free and headless-on-Linux with no license server.

Headless gate (PASS):
- Runs from one command, fully non-interactive: `telemac2d.py <case>.cas`
  (official execution docs + flussplan/jamal919 Docker READMEs). No display, no
  GUI, no interactive prompt. The `telemac2d.py` launcher does
  split -> generate-exe -> run -> merge; flags `--split / -x / --run / --merge`
  segment those steps, and `--ncsize=N` selects MPI rank count.
- A programmatic Python API (`TelApy`, EDF) drives a run from inside Python
  (`set_case` / `init` / `run_all_time_steps` / `get_state` / `finalize`),
  useful if we ever want in-worker coupling - but v0.1 should use the simpler
  `telemac2d.py <cas>` subprocess shim (mirrors how the GeoClaw worker shells out
  to `make .output` rather than embedding Clawpack).
- Pure software, GPL, no Windows DLL, no dongle - clears the
  "no Windows-only license server" half of the gate that gated TUFLOW.

LLM-cloud-drivable gate (PASS): every input is a templatable plain-text or
machine-authorable file (section 4). No GUI step is REQUIRED at run time - the
only GUI tools in the ecosystem (BlueKenue, QGIS plugins) are CONVENIENCE
pre-processors we replace with a deterministic, headless Gmsh-based mesh author.

---

## 2. Container-build approach: conda-forge package on micromamba (NOT compile-at-runtime)

Three candidate paths were evaluated against the container-hygiene norm
(pre-push bare-bones inspect; minimal base; scale-to-zero so paid per solve):

**A. conda-forge `opentelemac` on a micromamba base  <- RECOMMENDED.**
`conda install -c conda-forge opentelemac` installs the COMPILED TELEMAC solvers
(telemac2d et al.), Open MPI/METIS/HDF5, AND the full Python tooling
(`pytel`/`telapy`, the SELAFIN reader, the converters) in one pinned, reproducible
step on Linux. The conda environment auto-configures `$HOMETEL`/`$SYSTELCFG` and
puts `telemac2d.py` on PATH. This is the cleanest GRACE-2 fit: ONE pinned package
gives both the headless solver AND the Python postprocess library in the SAME
image, so the worker can shell out to `telemac2d.py <cas>` AND read the `.slf`
result with `data_manip.formats.selafin.Selafin` without a second toolchain.
Compared to GeoClaw's compile-at-runtime image, there is NO per-solve gfortran
build (faster cold solve), and the version pin is a single conda spec (the
digest-discipline analogue). Pin the `opentelemac` build + Python 3.11 + an
explicit Gmsh pin; verify-at-build-time with a tiny mesh smoke (mirror the
GeoClaw Dockerfile's build-time `make`-free smoke that renders a deck).

**B. Prebuilt third-party Docker image (flussplan/docker-telemac,
jamal919/telemac).** Debian-based, Open MPI + METIS + HDF5 + MED, install at
`/opt/telemac-mascaret/<version>/`, run via `telemac2d.py <cas> --ncsize=N`.
Useful as a SPIKE reference to prove a run end-to-end fast, but for production we
do NOT depend on a third-party image we do not control (supply-chain + the
container-hygiene "inspect before push" norm). Mirror it to our ECR only if
conda-forge proves problematic.

**C. Compile from source (the GeoClaw pattern: gfortran + `compile_telemac.py`).**
Highest control, but it reintroduces exactly the heavy compile-toolchain image
GeoClaw justified only because Clawpack compiles a per-deck executable. TELEMAC
ships a precompiled conda package, so source-compile is unjustified weight here.
Reject for v0.1.

Decision: path A. Single-stage micromamba image (multi-stage venv-copy does not
apply to a conda env). Keep it lean with `--no-install-recommends` equivalents,
`conda clean -afy`, a `.dockerignore`, and a build-time import+mesh smoke. Build
OFF the agent box via the existing `grace2-worker-builder` CodeBuild project
(autostop is blind to docker builds) with `WORKER_DIR=services/workers/telemac`
+ a new ECR repo - the off-box-CodeBuild norm.

Worker entrypoint is a near-copy of `services/workers/geoclaw/entrypoint.py`: the
object-store-in -> build-deck -> run -> object-store-out envelope is
solver-agnostic and copied verbatim; only the deck-author step (Gmsh mesh + `.cas`
+ `.cli`) and the solver invocation (`telemac2d.py <cas>`) + the output globs
(`*.slf` results) differ. Scheme-aware S3/GCS, `completion.json` schema, stdout/
stderr upload - all identical to GeoClaw/SWMM.

---

## 3. Where it plugs into the existing solver seam (zero new transport)

TELEMAC reuses the EXISTING run_solver -> wait_for_completion -> S3-completion
seam with NO new transport code, exactly like SWMM/GeoClaw/MODFLOW did:

- **Per-solver job-def routing** is already generic. `_resolve_batch_job_def`
  (`solver.py`) keys off the solver string, so `solver="telemac2d"` resolves
  `GRACE2_AWS_BATCH_JOB_DEF_TELEMAC2D` -> `SOLVER_BATCH_JOBDEF_REGISTRY` -> the
  generic fallback, and stays INERT (honest typed error) until NATE flips the env
  after `tofu apply` registers the job-def. No `solver.py` change needed beyond
  optionally seeding the registry key, mirroring `SFINCS_QUADTREE_SOLVER`.
- **Compute-class sizing** reuses `AWS_BATCH_COMPUTE_CLASS_SIZING`
  (small/standard/large/xlarge). Serial v0.1 maps `OMP`/`--ncsize` off the chosen
  bucket's `omp_threads`/`vcpus`; the same vertical ladder
  (`select_compute_class`) the SFINCS mesh autoscaler walks applies once the
  element count is estimated (section 5).
- **Completion manifest** is byte-identical to GeoClaw's (`run_id`, `status`,
  `exit_code`, stdout/stderr URIs, `output_uris`, started/finished, `error`).
  `wait_for_completion` polls the same `completion.json`.
- **Agent-side tool** `run_telemac_hydraulics` mirrors `run_geoclaw_inundation`:
  `cacheable=False`, `ttl_class="live-no-cache"`, `source_class=
  "workflow_dispatch"`, behind the server confirmation hook (Invariant 9, a
  solver run). It returns a typed `TelemacDepthLayerURI` (subclass of `LayerURI`)
  so the `add_loaded_layer` emitter gate fires and the map paints.

---

## 4. The `.cas` + mesh build_spec templating (the heart of the engine)

A TELEMAC-2D run requires, at minimum, three files - all machine-authorable from
a build_spec, no GUI:

1. **Steering file `<case>.cas`** - plain-text `KEYWORD : value` pairs. This is a
   pure template (mirrors how `setrun_builder.py` renders GeoClaw's `setrun.py`).
   The deck author renders it deterministically from the build_spec. Canonical
   keywords for a v0.1 fluvial/pluvial run (all confirmed from the official user
   manual / steering-file docs):
   - `GEOMETRY FILE : mesh.slf`            (the unstructured mesh, item 2)
   - `BOUNDARY CONDITIONS FILE : mesh.cli` (the boundary file, item 3)
   - `RESULTS FILE : result.slf`           (the SELAFIN output we postprocess)
   - `TIME STEP : <dt>` + `NUMBER OF TIME STEPS : <n>`
   - `GRAPHIC PRINTOUT PERIOD : <k>` (controls output-frame cadence -> the
     scrubber frame count, same lever as GeoClaw `output_frames`)
   - `VARIABLES FOR GRAPHIC PRINTOUTS : 'U,V,H,S,B'` (velocity, water DEPTH H,
     free surface S, bottom B - H is the field we rasterize to depth COG)
   - `LAW OF BOTTOM FRICTION : <n>` + `FRICTION COEFFICIENT : <k>`
     (Strickler/Manning/Chezy - reuse the existing NLCD->Manning-n substrate
     table `load_manning_mapping`, the SAME table SFINCS/SWMM already use)
   - `INITIAL CONDITIONS : 'CONSTANT DEPTH'` / `'ZERO ELEVATION'` etc.
   - `RAIN OR EVAPORATION : YES` + `RAIN OR EVAPORATION IN MM PER DAY : <r>` +
     `DURATION OF RAIN OR EVAPORATION IN HOURS : <h>` (native pluvial forcing,
     v6.2+; fed from the existing precip fetchers - MRMS/HRRR/return-period)
   - `PRESCRIBED FLOWRATES` / `PRESCRIBED ELEVATIONS` (liquid-boundary
     hydrographs for riverine inflow / tidal outflow - from the NWM/gauge/tide
     fetchers already in the catalog)

2. **Geometry file `mesh.slf`** - a SELAFIN-format UNSTRUCTURED triangular mesh
   carrying node x/y + bottom elevation. This is the NEW heavy step. Headless
   author path (no BlueKenue, no QGIS GUI):
   - Generate a triangular mesh over the AOI polygon with **Gmsh** (open, BSD-ish,
     full Python API - `gmsh.model.occ` / `gmsh.model.mesh.generate(2)`).
     Element size is the budget lever (section 5); refine along the channel
     centerline (from the existing NHDPlus/OSM-waterway fetchers).
   - Interpolate bottom elevation onto mesh nodes from the AOI DEM (the same
     topobathy/3DEP COG the other engines stage), via rasterio sampling.
   - Convert Gmsh `.msh` -> SELAFIN `.slf` using the TELEMAC-shipped
     `converter.py` / pputils (both ship with the conda package), or write SELAFIN
     directly with the `data_manip.formats.selafin.Selafin` writer in-worker.
   This is the deck-build analogue of the cht_sfincs quadtree authoring - and it
   is the reason this engine, not SFINCS-regular, is the right place to generalize
   the mesh budget.

3. **Boundary conditions file `mesh.cli`** - one ASCII line per boundary node
   tagging it as solid wall / prescribed-flow / prescribed-elevation / free. The
   deck author derives boundary-node tags from the mesh boundary + the build_spec
   inflow/outflow segments (channel-inlet node string -> prescribed flow;
   downstream/ocean node string -> prescribed elevation). Deterministic, no GUI.

build_spec (a `TelemacBuildSpec` pydantic model, the GeoClaw `GeoClawBuildSpec`
analogue) carries: `bbox`, `scenario` (`fluvial`/`pluvial`/`tidal`/`dam_break`),
target mesh element size / count, `sim_duration_s`, `time_step_s`, friction law +
coefficient (or NLCD-derived), rain forcing, and the inflow/outflow boundary
segments + hydrograph URIs. The worker maps it onto the three files above. As with
GeoClaw, the build_spec author is deterministic and TELEMAC-free (testable with no
solver installed); only the worker's run step touches the GPL binary.

---

## 5. SELAFIN -> publish_layer postprocess

The output `result.slf` is a SELAFIN/Serafin file: an unstructured mesh
(node x/y + triangle connectivity `IKLE`) with a value per node per output frame,
including `WATER DEPTH` (H), free surface (S), and velocity (U,V). The postprocess
is a near-clone of `postprocess_geoclaw` (which already rasterizes irregular AMR
patches onto a regular EPSG:4326 grid) - the ONLY new primitive is "rasterize an
unstructured triangular field" instead of "rasterize AMR patches":

1. Read `result.slf` with `data_manip.formats.selafin.Selafin` (ships with the
   conda package): `meshx`, `meshy`, `ikle2` (connectivity), and the per-frame
   `WATER DEPTH` value array. Run IN THE WORKER (the SELAFIN reader is part of the
   GPL closure, so it stays arms-length; the worker emits regular-grid GeoTIFF/COG
   the agent postprocess then handles) OR ship a slim MIT SELAFIN reader into the
   agent venv. RECOMMENDED: rasterize in the worker (keep the GPL reader in the
   worker image), upload per-frame depth COGs, and let the agent-side
   `postprocess_telemac` do the metric extraction + publish from the COGs - this
   keeps the agent venv GPL-clean.
2. Rasterize each frame's nodal depth onto a regular grid over the AOI bbox by
   barycentric interpolation across the `IKLE` triangles (matplotlib
   `tri.LinearTriInterpolator`, or scipy griddata) - dry cells (H<=tol) -> NaN.
   This is the unstructured analogue of `rasterize_frame_to_grid`.
3. Select the PEAK frame (max total wet depth), write the PEAK + up to
   `MAX_FLOOD_FRAMES` per-frame depth COGs, upload to `runs/<run_id>/`.
4. Return the SAME `(layers, metrics)` shape `postprocess_flood`/
   `postprocess_geoclaw` return: `layers[0]` peak `TelemacDepthLayerURI` +
   `layers[1:]` per-frame; metrics `max_depth_m`, `flooded_area_km2`,
   `max_inundation_m`. REUSE the shared `continuous_flood_depth` style preset
   (TELEMAC depth is the same physical quantity SFINCS/SWMM/GeoClaw emit) - NO new
   publish_layer style key, NO new web contract. The scrubber + LayerPanel + legend
   consume it unchanged (the Phase-1 temporal-frame path).

Optional value-add: publish the mesh itself as a vector layer (the existing
"view the computational mesh" feature #156 / SFINCS quadtree mesh #160) - the
SELAFIN triangle connectivity -> GeoJSON is a near-free addition and a strong
"show your work" differentiator for an unstructured engine.

---

## 6. Adaptive-mesh budget tie-in (the generalization trigger)

The adaptive-mesh-budget memo names this exact moment: "generalize when the 2nd
unstructured-mesh engine (TELEMAC/watershed) lands. Don't pre-abstract." TELEMAC
is that engine. Today there are TWO independent budget fits:
`sfincs_builder` (structured/quadtree) and `swmm_mesh_builder`
(`SWMM_RES_LADDER` + `estimate_swmm_solve_seconds` re-fit to a live anchor). The
TELEMAC tie-in:

- The budget LEVER for TELEMAC is Gmsh target element SIZE (or element COUNT), not
  a structured cell resolution. The pattern is identical: estimate solve seconds
  from element count, walk an element-size ladder UP (coarsen) until the estimate
  fits a wall-clock budget, surface the suggestion through the existing #154
  granularity-confirmation gate (user-controlled resolution - ALWAYS ask, show the
  autoscaler suggestion, allow override), and stamp the autoscale provenance
  (`estimated_active_cells`/elements, `vcpus`, `estimated_solve_seconds`,
  `coarsened`) onto `model_setup.parameters['autoscale']` so the live solve card
  + solve telemetry read it (the SAME `_extract_solve_autoscale` /
  `_emit_flood_solve_telemetry` path SFINCS uses).
- Recommended SHAPE of the generalization: extract the common contract
  (`estimate_solve_seconds(elements) -> s`, `cap_elements(budget) -> n`,
  `autoscale(target, budget) -> AutoscaleResult{elements, coarsened, ...}`) that
  SFINCS/SWMM/TELEMAC each implement with their OWN perf fit (a/p exponent
  re-fit to a LIVE anchor - the SWMM lesson: the synthetic spike under-coarsened
  16x, so TELEMAC's fit MUST be anchored to a real first-live solve, not a toy).
  Do NOT force a single fit across engines; share the LADDER-WALK + the
  provenance-stamping + the granularity-gate plumbing, keep the per-engine
  perf coefficients separate. This is "generalize the pattern, not the constants."
- Element-count is ALSO the natural payload-warning input: a huge requested mesh
  -> the existing `estimate_payload_mb` / tool-payload-warning envelope can warn
  before a multi-hour solve, reusing the #154 granularity block UI.

---

## 7. Differentiation + honesty (avoid being a worse SFINCS)

Scope TELEMAC to the lane the structured engines cannot serve, and say so in the
tool description so the LLM routes correctly:
- USE for FLUVIAL/RIVERINE flood routing where channel + floodplain geometry must
  be resolved on a conforming mesh; TIDAL/ESTUARINE hydrodynamics; dam-break on a
  fitted mesh; pluvial on complex terrain where an unstructured mesh beats a
  uniform grid.
- DO NOT use for compound coastal surge+wave (that is SFINCS - it has SnapWave and
  quadtree), urban pipe-network drainage (that is SWMM), tsunami run-up from a
  seafloor source (that is GeoClaw's dtopo path), or simple bbox pluvial where
  SFINCS-regular is cheaper.
- Honesty floor (render-chokepoint norm): a TELEMAC envelope that produced an
  empty/all-dry result NEVER reads `status=ok`; the postprocess raises a typed
  error (e.g. `TELEMAC_OUTPUT_EMPTY` / a mass-balance/volume-error gate read from
  the run listing, mirroring SWMM's continuity-error gate) rather than publishing
  a silently-wrong layer. Every narrated number comes from the typed
  `TelemacDepthLayerURI` scalars (Invariant 1), never free-generated.

---

## 8. Ordered minimal-integration job list (IF GO)

Mirrors the GeoClaw/SWMM landing sequence. Each job is single-owner, frozen
kickoff, live-evidence-gated. Schema-first, then worker, then infra, then
agent-chain, then acceptance.

1. **schema (job T1): TELEMAC contracts.** Add
   `packages/contracts/.../telemac_contracts.py`: `TelemacBuildSpec`,
   `TelemacRunArgs` (bbox, scenario literal fluvial/pluvial/tidal/dam_break,
   sim_duration_s, time_step_s, friction law+coef, rain forcing, inflow/outflow
   boundary segments + hydrograph URIs, target mesh element size), and
   `TelemacDepthLayerURI(LayerURI)` carrying `max_depth_m`/`flooded_area_km2`/
   `max_inundation_m` + echoed scenario. Reuse `BBox`, `continuous_flood_depth`
   preset. Template off `geoclaw_contracts.py`. (No solver dep; pure pydantic +
   unit tests.)

2. **engine (job T2): deterministic deck author (TELEMAC-free, fully unit-tested).**
   `services/workers/telemac/deck_builder.py`: render `<case>.cas` from build_spec
   (the keyword template, section 4), generate the Gmsh triangular mesh over the
   AOI + interpolate DEM bottom elevation, write the `.slf` geometry + the `.cli`
   boundary file with derived boundary tags. Deterministic, no GPL import at author
   time (Gmsh is permissive; the SELAFIN WRITE can use a slim writer). This is the
   `setrun_builder.py` analogue and the bulk of the work - test it hard with a
   synthetic AOI (mirror `test_setrun_builder.py`).

3. **engine (job T3): mesh-budget autoscaler + generalization.** Add the TELEMAC
   element-size ladder + `estimate_telemac_solve_seconds` (placeholder fit, to be
   re-anchored to T7's first live solve) and refactor the SHARED ladder-walk /
   provenance-stamp / granularity-gate plumbing out of SFINCS/SWMM into a common
   helper (section 6). Wire `autoscale` provenance onto `model_setup.parameters`.

4. **infra (job T4): worker image + Batch job-def + ECR.** Author
   `services/workers/telemac/Dockerfile` (micromamba + conda-forge `opentelemac`
   pin + Gmsh pin + boto3/rasterio; build-time import+mesh smoke; container-hygiene
   inspect). Build OFF-box via `grace2-worker-builder` CodeBuild. Add the Batch
   job-def + compute-env wiring in `infra/aws-batch` (Spot, scale-to-zero, the
   xlarge tier for big meshes). Provide the `GRACE2_AWS_BATCH_JOB_DEF_TELEMAC2D`
   env. Entrypoint = GeoClaw entrypoint clone with the deck-author + `telemac2d.py`
   run + `*.slf` output globs.

5. **engine (job T5): SELAFIN postprocess + workflow.**
   `workflows/postprocess_telemac.py` (read `result.slf`, barycentric-rasterize
   `WATER DEPTH` per frame -> peak + per-frame depth COGs, metrics, publish via
   the shared style preset) + `workflows/model_*_telemac_scenario.py` (the
   fetch -> stage build_spec -> Batch-solve -> postprocess chain, the
   `model_dambreak_geoclaw_scenario` analogue) + `workflows/run_telemac.py`.
   Optional: publish the mesh as a GeoJSON vector layer (#156/#160 tie-in).

6. **agent (job T6): `run_telemac_hydraulics` atomic tool.** The
   `run_geoclaw_inundation` analogue: validate/coerce args into `TelemacRunArgs`,
   dispatch the workflow, return `TelemacDepthLayerURI` or a typed error dict.
   `cacheable=False`/`live-no-cache`/`workflow_dispatch`; behind the confirmation
   hook. Add a tool-catalog entry with the section-7 routing guidance.

7. **testing (job T7): live acceptance + budget re-anchor.** Drive a real fluvial
   case end-to-end on Batch (a published, gauged reach - e.g. a Severn-style
   floodplain or a small NHDPlus reach with NWM inflow), prove the SELAFIN ->
   depth-COG -> map render + scrubber, capture the run wall-clock at a known
   element count, and RE-ANCHOR `estimate_telemac_solve_seconds` to that live
   number (the SWMM lesson). Emit the `READINESS_RESULT telemac2d PASS run_id=...
   layers=N metric=max_depth_m=...` one-liner (the acceptance-driver parity norm).

8. **schema (job T8, user-landed): SRS appendix amendment.** Propose the
   Appendix-B engine-table + Appendix-D tool-registry amendment for TELEMAC-2D
   (specialist proposes via report; only NATE lands into `docs/srs/*` then
   `make srs`). Not a code job - a documentation follow-up.

Critical path: T1 -> T2 -> {T3, T4 in parallel} -> T5 -> T6 -> T7. T8 trails.

---

## 9. Sources (primary)

- opentelemac.org - Installation on Linux (wiki), Download, Execution docs,
  steering_file docs, TELEMAC-2D user manuals (v7p0 / wiki), FAQ.
- conda-forge `opentelemac` (anaconda.org/conda-forge/opentelemac) + the official
  "openTELEMAC is available on conda-forge [linux users]" announcement.
- Docker: flussplan/docker-telemac (Debian + Open MPI/METIS/HDF5/MED,
  /opt/telemac-mascaret), jamal919/telemac (Ubuntu 20.04 + Open MPI + HDF5).
- SELAFIN Python: `data_manip.formats.selafin.Selafin` (pytel), the OpenTelemac
  parserSELAFIN/converter scripts, OpenDrift's SELAFIN reader.
- TelApy Python API (EDF) + the v8p2 telapy/telemac2d notebooks.
- Mesh: Gmsh (gmsh.info, Python API); "QGIS as a pre- and post-processor for
  TELEMAC: mesh generation" (BAW); pputils / Gmsh->SELAFIN conversion forum +
  converter.py.
- Keywords: TELEMAC-2D user manual (RAIN OR EVAPORATION v6.2+, LAW OF BOTTOM
  FRICTION, FRICTION COEFFICIENT, VARIABLES FOR GRAPHIC PRINTOUTS); CRAN telemac
  rainfall-runoff vignette.
- Case studies (real practitioner pipelines): Severn 1D/2D inundation evaluation
  (ScienceDirect, 2002); De Ijzermonding tidal inlet; Brague river flood 2015 HPC
  study (T&F 2025); Mekong Delta TELEMAC-2D inundation; Gironde estuary calibration
  via TelApy+ADAO (ScienceDirect/arXiv).
