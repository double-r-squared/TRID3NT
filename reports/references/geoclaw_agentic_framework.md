# GeoClaw - agentic geophysical-flow (tsunami / storm-surge) framework (NATE reference, 2026-06-26)

> Source: NATE's external-AI framework, archived for the engine-coverage track. GeoClaw = the
> 4th domain specialist (after MODFLOW/SFINCS/OpenQuake), same interpret->fetch->build->run->
> map->report pipeline. Python library: `clawpack.geoclaw` (the flopy/hydromt_sfincs analog).

## GRACE-2 bridge note
GeoClaw is ALREADY partially wired here (like OpenQuake): `services/agent/.../workflows/run_geoclaw.py`,
a live `grace2-geoclaw` AWS Batch job-def + ECR image (built via the grace2-worker-builder
CodeBuild + build_engines.sh), and it is in the engine ranking [[reference_engine_cloud_ai_drivability_ranking]].
So the job is assess -> prove LOCALLY (clawpack.geoclaw, pip-installable, the flopy analog) ->
fix gaps -> deploy -> NATE Haiku prod-test, mirroring [[project_engine_scenario_coverage_track]]
and the OpenQuake playbook just completed. Note: SFINCS already has a (waterlevel-seam) tsunami
archetype being added in the SFINCS coverage build; GeoClaw is the HIGH-FIDELITY tsunami path
(AMR + Okada source + run-up), complementary not duplicate.

## Canonical question
"Tsunami inundation extent, max flow depth, and arrival time for a M9.0 Cascadia Subduction Zone
earthquake at Seaside, Oregon" - the classic NOAA-approved GeoClaw inundation benchmark
(seismic source -> ocean propagation -> high-res run-up).

## Data (public, automatable)
- **Bathymetry/topography (nested DEMs):** coarse ocean (1 arc-min) + regional (15 arc-sec) +
  local (1/3 arc-sec). GEBCO, SRTM, NOAA Coastal Relief Model, USGS 3DEP, NOAA CUDEM. clawpack
  has fetch/stitch scripts (ETOPO/SRTM/NOAA).
- **Earthquake source:** fault geometry + slip (M9.0 Cascadia). USGS ShakeMap/ComCat, SRCMOD
  finite-fault, OR Okada parameters -> `clawpack.geoclaw.dtopotools` generates the seafloor
  deformation (agent can synthesize the dtopo, no pre-stored slip file needed).
- **Tide/initial surface (optional):** FES2014 / TPXO via pyTMD.
- **Manning roughness:** NLCD / ESA WorldCover / constant (same as SFINCS).
- **Validation:** tide-gauge records (NOAA Tides & Currents, IOC) for past events (1964 Alaska,
  2011 Tohoku).
- **Exposure (optional):** WorldPop + OSM building footprints.

## Outputs (all map-based)
Max inundation depth map; arrival-time map (time to first wave > threshold); velocity/momentum-flux
(damage); gauge time series (buoys/harbour); cross-shore transects; scenario-comparison maps
(SLR, slip variants).

## Agentic pipeline
1. Interpret: event type (tsunami/surge/overland), location, scenario (M9.0 Cascadia or custom
   fault), outputs (depth/arrival/impact).
2. Fetch: nested bathy/topo (topotools), fault params / dtopo (dtopotools Okada), roughness, tide.
3. Build: agent writes `setrun.py` + `setplot.py` (AMR refinement regions, gauge + FGMAX regions,
   BCs, output control) from a tsunami-inundation template; `dtopotools.Fault.create_dtopography`
   for the deformation.
4. Run: `make .output` / `python run_geoclaw.py` -> parallel AMR -> Fortran binary output.
5. Post-process + map: clawpack.visclaw / matplotlib -> max-inundation (fgmax grids), arrival-time,
   gauge plots; overlay footprints/basemap; population exposure = depth x WorldPop.
6. Report: e.g. "M9.0 Cascadia inundates 65% of Seaside; max depth 8 m at beachfront; first wave
   12 min post-quake; hospital outside inundation; evacuation routes overtopped within 20 min."

## Archetype templates (geoclaw)
- `tsunami_inundation_scenario` (specified source -> inundation + arrival time)
- `storm_surge_coastal_flood`
- `probabilistic_tsunami_hazard` (multiple sources / logic tree)
- `debris_flow_overland` (GeoClaw multi-layer)

```yaml
id: tsunami_inundation
model: geoclaw
required_inputs: [bathymetry_grids, topography_grids, fault_parameters_or_deformation]
optional_inputs: [manning_grid, tide_offset]
outputs: [max_depth_map, arrival_time_map, gauge_timeseries]
visualizations: [inundation_map_basemap, arrival_time_map, gauge_plots]
```

## Four-engine summary (now)
| Feature | MODFLOW | SFINCS | OpenQuake | GeoClaw |
|---|---|---|---|---|
| Python lib | flopy | hydromt_sfincs | openquake.engine | clawpack.geoclaw |
| Domain | groundwater | surface flood | earthquake | tsunami/surge |
| Key outputs | head, depletion | flood depth/hazard | PGA/loss/damage | inundation depth, arrival time |
| Agent difficulty | moderate | easy | easy | moderate (AMR setup) |

All four: open-source Python, map-based answers, public data, the same interpret->fetch->build->
run->map->report pipeline. GeoClaw is the AMR-setup-heavy one (the setrun.py refinement + the
Okada dtopo are the build complexity).
