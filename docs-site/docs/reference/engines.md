# Engines

TRID3NT integrates multiple numerical simulation engines. Each engine has a dedicated AWS Batch
worker container (build + solve + postprocess) and an agent-side composer/workflow tool.

Source: `services/workers/*/`, `services/agent/src/grace2_agent/tools/`, `solver.py`.

---

## Integration levels

| Level | Meaning |
|---|---|
| **Fully offloaded** | Build + solve + postprocess all run in Batch worker; agent is thin orchestrator |
| **Partial** | Solve + postprocess in Batch; build still in-agent OR in-agent fallback only |
| **In-agent fallback** | Worker exists but postprocess fallback runs in-agent if manifest absent |

---

## SFINCS

**What it models:** coastal and pluvial (rainfall-driven) urban flooding. SFINCS (Super-Fast INundation of CoaStlines) is a reduced-complexity 2D shallow-water model from Deltares.

**Version:** v2.3.3

**Variants:**

| Variant | Solver key | Use case |
|---|---|---|
| Coastal quadtree | `sfincs-quadtree` | Hurricane storm surge, quadtree mesh, SnapWave wave coupling |
| Pluvial | `sfincs-build` | Rainfall-driven urban flood, regular grid |
| Legacy deckbuilder | `sfincs-deckbuilder` | Combined build+solve, historical |

**How it is driven:**
- Composer tool: `run_model_flood_scenario` (pluvial) or `run_model_flood_habitat_scenario`
- Workflow: `SOLVER_WORKFLOW_REGISTRY["sfincs"]` -> `grace-2-sfincs-orchestrator`
- Agent composes `job_spec` with AOI bbox, DEM URI, storm/rain forcing, duration, and submits one
  Batch job.
- Worker: hydromt grid generation + SFINCS binary solve + rasterio COG postprocess.

**Offload status:** Fully offloaded (build + solve + postprocess in Batch).

**North Star demo:** Hurricane Michael / Mexico Beach coastal flood -- SFINCS quadtree + SnapWave,
computed-vs-observed, NAVD88 rainbow key. See `reports/design/` for the Mexico Beach reference.

---

## MODFLOW 6

**What it models:** 3D groundwater flow and solute transport (GWT module for contamination plumes).

**Version:** MODFLOW 6 v6.5.0 (USGS binary)

**Archetypes:** 11 scenario archetypes covering:
- Steady-state aquifer flow
- Transient pumping / dewatering
- Contaminant plume (GWT)
- Agricultural field contamination (GWT + fiboa field intersection)
- Salt intrusion
- (and 6 additional archetypes)

**How it is driven:**
- Composer tools: `run_model_groundwater_contamination_scenario`, `run_model_contamination_affected_fields`
- Agent composes `job_spec` with geology, boundary conditions, contamination source location.
- Worker: FloPy model build + mf6 solve + rasterio COG postprocess.

**Offload status:** Fully offloaded (`modflow-build` Batch key; FloPy + mf6 + postprocess in worker).

**Python deps:** `flopy>=3.7,<4`, `numpy>=1.26,<3`, `rasterio>=1.3,<2`

---

## PySWMM (Storm Water Management Model)

**What it models:** urban drainage -- node-link networks, combined sewer, stormwater runoff.
PySWMM wraps the SWMM5 engine from the US EPA.

**How it is driven:**
- Composer tool: `run_swmm_urban_flood`
- Network built by composing QGIS tools (buildings as obstructions, walls as blocked links).
- Worker: PySWMM simulation + postprocess.

**Offload status:** Solve + postprocess in Batch; in-agent fallback if manifest absent.

**North Star:** quasi-2D urban flood with building footprints and flap gates.

---

## GeoClaw

**What it models:** tsunami inundation, dam breaks, and other geophysical flows using adaptive mesh
refinement (AMR) shallow-water equations.

**Version:** GeoClaw 5.14.0

**How it is driven:**
- Workflow: `SOLVER_WORKFLOW_REGISTRY["geoclaw"]` -> `model_dambreak_geoclaw_scenario`
- Worker: Fortran compile-at-runtime + GeoClaw solve + postprocess.

**Offload status:** Solve + postprocess in Batch; in-agent fallback if manifest absent.

**Diagnostic note:** "Total mass at initial time" in solver output is the key diagnostic -- 1e5
order of magnitude = no wave (bad input); 1e9+ = real wave. This was the root cause of 9 fix
cycles before the GeoClaw tsunami demo worked end-to-end (2026-06-30).

---

## OpenQuake Engine

**What it models:** Probabilistic Seismic Hazard Analysis (PSHA) -- ground-motion hazard curves,
uniform hazard spectra, loss estimation.

**License:** AGPL (packaged separately)

**How it is driven:**
- Workflow: `SOLVER_WORKFLOW_REGISTRY["openquake"]` -> `model_seismic_hazard_scenario`
- Composer tool: `run_seismic_hazard_psha`
- Worker: `pip install openquake-engine` at build time.

**Offload status:** Solve + postprocess in Batch; in-agent fallback if manifest absent.

---

## Landlab

**What it models:** Landslide susceptibility and shallow landslide initiation using the Landlab
earth-surface modeling framework.

**How it is driven:**
- Workflow: `SOLVER_WORKFLOW_REGISTRY["landlab"]` -> `model_landslide_scenario`
- Worker: `python:3.12-slim-bookworm` base + Landlab + postprocess.

**Offload status:** Solve + postprocess in Batch; in-agent fallback if manifest absent.

---

## SWAN

**What it models:** Nearshore wave propagation -- significant wave height, peak period, direction.
SWAN is a third-generation spectral wave model from Delft.

**How it is driven:**
- Workflow: `SOLVER_WORKFLOW_REGISTRY["swan"]` -> `model_wave_scenario`
- Worker: custom version-pinned container + postprocess.

**Offload status:** Solve + postprocess in Batch; in-agent fallback if manifest absent.

**Usage context:** Often combined with SFINCS coastal runs (SnapWave coupling within the
sfincs_deckbuilder worker; standalone SWAN for larger-domain wave fields).

---

## Canopy (ML inference)

**What it models:** Satellite/aerial imagery classification and canopy/land-cover detection.
Not a physics-based simulator -- uses ML inference.

**Solver key:** `canopy` (maps to `"aws-batch"` sentinel in `SOLVER_WORKFLOW_REGISTRY`)

**Worker:** `python:3.11-slim-bookworm`

---

## Planned / in-progress engines

| Engine | Status | Notes |
|---|---|---|
| HEC-RAS | Research phase | USACE hydraulics; headless Linux Batch gate required; complement to SFINCS |
| HEC-HMS | Research phase | USACE hydrology; feeds hydrographs to RAS + SFINCS |
| TELEMAC | Backlog | Unstructured mesh coastal/river; Class B explicit tool definition |

---

## Adding a new engine

See the [Contributing](../contributing.md#how-to-add-an-engine) guide for the full checklist.
Quick summary:
1. Create `services/workers/<engine>/` with a Dockerfile and entrypoint that reads `job_spec` and
   writes `publish_manifest.json` + `completion.json`.
2. Add a Batch job definition in `infra/aws-batch/`.
3. Add a solver key to `SOLVER_WORKFLOW_REGISTRY` in `solver.py`.
4. Register a composer tool with `register_tool` in the agent tool registry.
5. Drive a live E2E test with the Haiku model on a small AOI before deploying.
