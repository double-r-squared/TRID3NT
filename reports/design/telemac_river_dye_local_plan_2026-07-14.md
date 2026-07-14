# TELEMAC-2D River-Dye (surface tracer transport) - LOCAL feasibility + plan

De-risk + design pass for adding TELEMAC-2D as a LOCAL engine on the TRID3NT
stack to model a SURFACE-water dye/tracer spill moving downstream along a river
channel (the Baird "dye down the channel" visual; NATE North Star activated
2026-07-14, memory `project_sediment_dye_transport_north_star`).

Scope: feasibility + plan ONLY. No product code was wired; no agent tool or
server.py touched; the running trid3nt agent was not restarted; no user cases
touched. All TELEMAC work ran in an ISOLATED micromamba env
(`/home/nate/telemac-spike`), never the agent venv.

ASCII only. No em/en dashes. Grounded against `engine_spike_telemac.md`
(GO_WITH_CAVEATS) and `spike_dye_release_transport.md` (TELEMAC-2D TRACER is the
recommended engine), and against the live local solver seam
(`vendor/services/workers/geoclaw` + `run_geoclaw.py` local-docker spec).

---

## 0. Bottom line

- **INSTALL: clean GO.** conda-forge `opentelemac` v9.0.0 installs cleanly and
  ships a PRECOMPILED `telemac2d` solver binary + the full Python tooling
  (SELAFIN reader, converters). The launcher `telemac2d.py <cas>` runs headless,
  and DAMOCLES parses a full TRACER steering file. This is the #1 unknown and it
  is retired: the engine installs and runs on this Linux box.

- **MINIMAL TRACER RUN: BLOCKED at mesh authoring (the predicted risk #1),
  NOT at the physics.** The solver reads a synthetic channel mesh and parses the
  tracer block, but a geometry hand-authored via the Python HERMES writer
  (`TelemacFile`) has a boundary/IPOBO encoding that TELEMAC's own preprocessor
  (`stbtel`) AND the solver's boundary builder (`elebd_`) reject. So there is NO
  honest "tracer moving downstream" solve screenshot from this session - producing
  one would have required a real mesh generator (Gmsh) + `stbtel` boundary
  computation, which is exactly the "mesh is the cost" work the spike flagged and
  is beyond a de-risk budget. The rendered figure at
  `docs/proof/telemac_tracer_minimal.png` HONESTLY shows the AUTHORED case (mesh +
  bed elevation + inflow/outflow/wall BCs + dye source), labelled as solve-blocked
  - it is NOT a solve output.

- **Two real install-level findings** (both matter for the worker image):
  1. The conda build's `IDENTIFY_ENDIAN_TYPE` returns error -1 on ANY
     pre-existing EMPTY output file, aborting `OPEN_MESH`. The launcher/worker
     MUST ensure the results file does not pre-exist as a 0-byte placeholder
     (delete-before-run). This bit both `telemac2d` and `stbtel`.
  2. Hand-rolling the SERAFIN geometry + boundary via `TelemacFile.add_mesh` /
     `add_bnd` is NOT solver-valid even when the mesh is geometrically perfect.
     The production path is Gmsh (with tagged boundary physical groups) ->
     `stbtel`, never a hand-authored HERMES write.

- **LOCAL vs BATCH: local box is fine for demo-scale runs.** 8 cores, 15 GB RAM;
  serial v0.1 needs a fraction of that. TELEMAC runs headless in the SAME
  local-docker seam GeoClaw/SFINCS already use. Batch is the cloud lane, not a
  local requirement.

---

## 1. Install feasibility (the #1 unknown) - RESULT: GO

### 1.1 What was tried, in order

1. **conda-forge `opentelemac` (RECOMMENDED path from the spike) - WORKED.**
   No conda/mamba existed on the box, so a standalone `micromamba` (v2.8.1, single
   18 MB binary) was fetched and an isolated env created:
   `micromamba create -n telemac -c conda-forge opentelemac`.
   - Package: `opentelemac v9.0.0` (GPL-3), py3.12, pulls openmpi 5.x, metis,
     gfortran/gcc 13, scipy, matplotlib, mpi4py.
   - Env size on disk: **1.8 GB**. Wall time: ~2m40s of linking after downloads
     (first download of the full closure is ~1 GB; budget ~5-8 min cold).
   - `telemac2d.py` lands on PATH; `$HOMETEL` and `$SYSTELCFG` auto-configured;
     `USETELCFG=gnu.shared`. A **precompiled** `telemac2d` binary ships in
     `builds/gnu.shared/bin/` - NO per-solve compile (BETTER than GeoClaw, which
     compiles xgeoclaw each run).
   - `telemac2d.py <cas>` runs; DAMOCLES parsed a full steering file including
     `NUMBER OF TRACERS`, `NAMES OF TRACERS`, `PRESCRIBED TRACERS VALUES`,
     `COEFFICIENT FOR DIFFUSION OF TRACERS` and echoed
     "LECDON: FINITE VOLUME SCHEME ON THE TRACER 1".
   - The env can also RECOMPILE from source in ~4 min (8 jobs) via
     `compile_telemac.py -c gnu.shared.debug` - useful if a Dockerfile wants a
     pinned rebuild. (Note: `compile_telemac.py --clean` WIPES `builds/`; a full
     recompile restores it.)

2. **gmsh in the SAME conda create - FAILED / avoid.** Adding `gmsh python-gmsh`
   to the create pulled a CUDA `viskores` (cuda12.9) dependency, ballooning the
   download to 1 GB+ and corrupting on extraction. Do NOT co-install conda `gmsh`.
   Use the self-contained **PyPI `gmsh` wheel** instead (pip install gmsh, ~30 MB,
   no CUDA) when the mesh generator is needed.

3. **Prebuilt Docker image / source build:** not needed - conda-forge is the
   clean win. (An OS `/tmp` tmpfs is only 7.8 GB and filled up mid-install; the
   mamba root must live on the 135 GB disk, not the scratchpad tmpfs.)

### 1.2 Verdict

Install is a clean GO. Version = **opentelemac v9.0.0** (conda-forge, GPL-3,
precompiled telemac2d, Python SELAFIN tooling in the same env). Cost = 1.8 GB
env, ~5-8 min cold create. This is the worker-image base for the plan below.

---

## 2. Minimal tracer run (the #2 unknown) - RESULT: physics-ready, mesh-blocked

### 2.1 What ran

The conda package ships NO example/validation cases, so a synthetic rectangular
channel was authored from scratch (200 m x 20 m, 61x13 structured grid split into
1440 triangles / 793 nodes, gentle downstream bed slope), with:
- a `.cas` steering file (`t2d_channel.cas`) with 1 TRACER, inflow prescribed
  flowrate 5 m3/s, outflow prescribed stage 0.5 m, Manning friction, and the dye
  prescribed at the inflow (100 mg/L);
- a SERAFIN geometry `.slf` and a boundary `.cli` written via the TELEMAC Python
  `TelemacFile` API (`add_mesh` / `add_bnd` / `write`).

The solver:
- opened all files, ran DAMOCLES, parsed the full tracer block;
- read the mesh: "NUMBER OF ELEMENTS 1440 / NUMBER OF POINTS 793 / TYPE OF ELEMENT
  TRIANGLE"; printed MXPTEL mesh stats.

### 2.2 Where it blocked, and the honest root cause

The run then **segfaults in `elebd_`** (BIEF boundary-navigation builder, called
from `inbief_` in `telemac2d_init`) - an out-of-bounds integer index while
building the boundary contour. Fixes attempted and their outcomes:

| Attempt | Result |
|---------|--------|
| Empty pre-created RESULTS file blocking `OPEN_MESH` (error -1) | REAL bug, FIXED by deleting the 0-byte output before run (see 0.finding-1) |
| IPOBO corrected to boundary-rank ordering 1..144 (add_bnd wrote node-id+1) | necessary, but not sufficient |
| Boundary as BND_SEGMENT (ndp=2) vs BND_POINT | SERAFIN geometry always stores IPOBO/points; no effect |
| KNOLG local->global identity (parallel path, mpirun -np 1) | no effect |
| Boundary contour winding reversed (CW vs CCW) | no effect |
| Debug rebuild for a source line | no separate debug .so produced; still segfaults |

The mesh is provably valid by every geometric check: 144 boundary edges forming a
SINGLE closed cycle, boundary node set == ring set, every consecutive ring pair a
real boundary edge, all triangles positive-area, IPOBO ranks 1..144 that invert to
clean 1-based NBOR, a clean 1-based `.cli` (no zeros), and TELEMAC's OWN Python
`_hermes` reader reads it back correctly.

The decisive diagnostic came from running the mesh through TELEMAC's OWN
preprocessor `stbtel` (MESH GENERATOR = SELAFIN): it reported
**"1 POINTS CANCELLED" (793 -> 792)** and repeated
**"ERROR ON BOUNDARY NODE: IT BELONGS TO 1 BOUNDARY SEGMENT(S)"**. So TELEMAC's
boundary reconstruction disagrees with the hand-authored IPOBO/boundary even
though there are NO coincident coordinates. CONCLUSION: the Python `TelemacFile`
writer does not produce a solver-valid boundary encoding for a from-scratch mesh -
this is the concrete realisation of the spike's "mesh is the cost / risk #1", now
with hard evidence that even `stbtel` rejects the hand-rolled boundary.

### 2.3 What this means for the plan

Do NOT hand-author SERAFIN geometry+boundary. The canonical, tested path is:
**Gmsh (channel-following triangulation with the inflow/outflow/wall boundary as
tagged PHYSICAL GROUPS) -> SERAFIN via TELEMAC's gmsh path -> `stbtel` computes
IPOBO + writes the `.cli` from the tagged boundary.** This is the bulk of the
engine work and MUST be proven on a real solve before the tool is wired. It is a
bounded, known task (Gmsh + stbtel are both in the conda env's dependency reach),
just not a de-risk-budget task.

Artifacts kept for the next engineer: `/home/nate/telemac-spike/build_channel.py`,
`t2d_channel.cas`, `t2d_min.cas`, `stb.cas`, and the isolated `telemac` env.

---

## 3. River-dye demo plan: place -> mesh -> solve -> render

Natural-language target: "simulate a dye spill in the river at <place>". Every
stage maps to an existing TRID3NT seam or names the new glue.

| Stage | How | Seam / new glue |
|-------|-----|-----------------|
| place -> AOI + reach | geocode -> bbox; fetch channel centerline | EXISTING `fetch_river_geometry`, `extract_stream_network`, `fetch_nhdplus_nldi_navigate` |
| terrain | DEM over AOI, interpolate onto mesh nodes | EXISTING `fetch_dem` (Copernicus fallback) + rasterio sampling |
| **mesh authoring** (THE hard part) | Gmsh: triangulate the channel + floodplain, refine along centerline; tag inflow/outflow/wall as PHYSICAL GROUPS; convert to SERAFIN; `stbtel` computes IPOBO + `.cli` | NEW glue: `telemac_mesh_builder` (PyPI `gmsh` wheel + stbtel). This is the dominant cost. NO hand-rolled HERMES writes. |
| `.cas` steering + tracer source | render `KEYWORD : value` template + `NUMBER OF TRACERS`/`NAMES OF TRACERS`/tracer BC or `SOURCES FILE` at the spill point + `COEFFICIENT FOR DIFFUSION OF TRACERS` | NEW deterministic deck author (the `setrun_builder.py` analogue); TELEMAC-free at author time |
| solve | `telemac2d.py <cas>` in a docker image via the local-docker backend; DELETE the empty result file before run (finding-1) | EXISTING `LocalSolverSpec` + `SOLVER_WORKFLOW_REGISTRY` + `register_local_solver_spec` (clone `geoclaw_local_spec` in `run_geoclaw.py`; new `trid3nt-local/telemac:latest` image + `GRACE2_TELEMAC_IMAGE`) |
| completion | worker writes `completion.json` + output URIs to MinIO/S3 | EXISTING S3-completion envelope (byte-identical to GeoClaw `entrypoint.py`) |
| results -> layers | read result `.slf` TRACER band per frame, barycentric-rasterize onto EPSG:4326 -> per-frame concentration COGs | NEW `postprocess_telemac` tracer branch (clone `postprocess_geoclaw` + MODFLOW-GWT `continuous_plume_concentration` style + `max_concentration_mgl`) |
| render + animate | per-frame COGs collapse into the bottom-center scrubber; OR publish the `.slf` directly as an MDAL mesh layer in QGIS | EXISTING temporal-group scrubber + MDAL mesh render (SFINCS/MODFLOW meshes already materialize; `.slf` is natively MDAL-readable, proof `56-mdal-mesh-render.png`) |

Honest hard parts (ranked): (1) **mesh authoring from real river geometry** -
Gmsh channel-conforming mesh with correctly TAGGED boundary groups + stbtel; this
is where the risk lives and is proven above to be non-trivial. (2) dispersion
coefficient is a modelling assumption (label concentrations as
model-with-assumed-dispersion, honesty floor). (3) establishing steady flow before
the dye front is visible (ramp inflow / initial conditions).

MDAL note: TELEMAC `.slf` is natively MDAL-readable, so the QGIS render path can
publish the mesh+tracer directly (a strong "show your work" differentiator) in
ADDITION to the raster-COG scrubber - reuses the existing MDAL mesh materialize.

---

## 4. Effort + phasing

| Phase | Work | Rough effort | Risk |
|-------|------|-------------|------|
| P0 mesh proof (unblock #2) | Gmsh channel mesh with tagged boundary -> stbtel -> `.cli` -> a REAL local telemac2d TRACER solve writing a time-varying TRACER `.slf`; render frames | 1-2 days | the crux; must land before anything else. Gmsh physical-group tagging + stbtel is the unknown to retire. |
| P1 deck author + mesh builder | `telemac_mesh_builder` (Gmsh+stbtel) + `.cas`/tracer/`SOURCES` deck author, deterministic + unit-tested (TELEMAC-free) | 2-3 days | medium |
| P2 worker image + local seam | `services/workers/telemac/Dockerfile` (micromamba + opentelemac v9.0.0 pin + PyPI gmsh; delete-empty-result fix; build-time mesh+tracer smoke) + clone `geoclaw_local_spec` -> `telemac_local_spec` + `GRACE2_TELEMAC_IMAGE` | 1-2 days | low - the seam is proven; image hygiene per the container norm |
| P3 postprocess + render | `postprocess_telemac` tracer branch -> per-frame concentration COGs + MDAL `.slf` layer + typed `TelemacTracerLayerURI` | 1-2 days | low - clones GeoClaw/MODFLOW postprocess |
| P4 composer + tool + acceptance | `model_river_dye_release_scenario` composer + `run_telemac` registration + a HAIKU-driven live case (per the local test protocol) | 1-2 days | low |

Total ~1.5-2 weeks, gated on P0. The dye/tracer physics itself is a SMALL delta
on the TELEMAC-2D hydraulics integration (tracer is native, DAMOCLES already
parses it) - the whole cost is the mesh, exactly as the spike predicted.

### Local vs Batch

- **Local box (8 cores, 15 GB RAM) runs demo-scale TELEMAC comfortably.** Serial
  v0.1 uses a fraction of RAM; the precompiled binary means no compile cost. Run
  it in the SAME `GRACE2_SOLVER_BACKEND=local-docker` seam as GeoClaw/SFINCS
  (`trid3nt-local/telemac:latest`, `--network host`, MinIO creds injected).
- **Batch is the cloud lane, not a local requirement.** Large channel meshes /
  many tracer frames coarsen via the existing #154 granularity gate + the
  adaptive-mesh budget pattern (element-size ladder), re-anchored to P0's first
  real solve wall-clock. The tear-down Batch path is only needed at cloud scale.

---

## 5. Sources / artifacts

- In-repo: `reports/design/engine_spike_telemac.md`,
  `reports/design/spike_dye_release_transport.md`, memory
  `project_sediment_dye_transport_north_star`.
- Local seam: `vendor/services/agent/src/grace2_agent/workflows/run_geoclaw.py`
  (`geoclaw_local_spec` local-docker template), `.../tools/solver.py`
  (`LocalSolverSpec`, `SOLVER_WORKFLOW_REGISTRY`, `register_local_solver_spec`),
  `vendor/services/workers/geoclaw/{Dockerfile,entrypoint.py,setrun_builder.py}`.
- TELEMAC (isolated env `/home/nate/telemac-spike`): opentelemac v9.0.0,
  `telemac2d.py`, `stbtel.py`, `data_manip.extraction.telemac_file.TelemacFile`,
  `data_manip.extraction.parser_gmsh` (the gmsh->SELAFIN reader; note its IPOBO is
  a stub, confirming stbtel is the boundary-computing step).
- Proof figure (HONEST authored-case, NOT a solve):
  `/home/nate/Documents/trid3nt-local/docs/proof/telemac_tracer_minimal.png`.
- Working scratch: `build_channel.py`, `t2d_channel.cas`, `t2d_min.cas`,
  `stb.cas` under `/home/nate/telemac-spike`.
