# Scoping Spike: Move NetCDF/.mat -> COG Postprocess Into the Batch Workers

Status: SCOPING ONLY (read + design + estimate; no implementation)
Owner seam: engine + infra (worker), agent (registration thin-out)
Date: 2026-06-23

## 1. Problem and goal

Today the heavy raster postprocess (NetCDF/.mat -> per-frame COG) runs ON THE ALWAYS-ON AGENT BOX after the Batch solve finishes. The Batch worker writes ONLY raw solver output (SFINCS `sfincs_map.nc`, SWAN `swan_out.mat`) plus `completion.json`. The agent then:

1. Downloads the full raw output via sync boto3 (`postprocess_flood._resolve_run_output_to_local`, often 100s of MB; SFINCS quadtree downloaded twice when SnapWave waves run).
2. Runs xarray/scipy `griddata` (up to 8192x8192 per frame for the quadtree face rasterizer) + rasterio COG encode + a re-open CRS verify, SERIALLY, for up to `MAX_FLOOD_FRAMES`=144 depth frames + 144 wave frames + 2 peaks.
3. Uploads each COG back to the runs bucket.
4. Re-downloads each COG in `publish_layer._ensure_raster_has_overviews` (overview probe / rebuild) and AGAIN in `_resolve_titiler_style_params` (band-stats), then mints the TiTiler tile template, registers both faces via `observe_published_layer`, and emits via `add_loaded_layer`.

This is the single biggest reason the agent box cannot shrink to t3.small/micro, and it is a multi-minute single-core tail the user waits through AFTER Batch is already done.

Goal: move steps 1-3 (and the overview build from step 4) into the Batch worker, which already has the raw output local, the geo stack in-image, and a much bigger c7i box to parallelize frames. The agent collapses to: read a thin manifest, build the TiTiler URL, resolve the style preset by KEY, register + persist. This is the scale-to-zero island pattern, engine-agnostic across SFINCS, SWAN, and future raster engines.

## 2. Recommended seam (the cut)

Cut at the `completion.json` / output_uris boundary, just BEFORE `_write_completion` in each worker `main()`. The worker runs the raster postprocess on the LOCAL deck dir output (no S3 download), writes display-ready overview-bearing COGs to the SAME deterministic keys the agent uses today (`flood_depth_peak.tif`, `flood_depth_frame_NN.tif`, `swan_wave_height_peak.tif`, `swan_wave_height_frame_NN.tif`) via the existing `_expand_outputs` upload sweep (the `*.tif` glob already matches for SFINCS; SWAN needs the COGs added to its output globs), and writes a thin typed `publish_manifest.json` alongside `completion.json`. The agent reads the manifest and becomes register-only.

The reusable substrate is a NEW GPL-free package `services/workers/_raster_postprocess/` that holds the lifted, pure numpy/scipy/rasterio/pyproj/xarray code (the frame-select, face-rasterize, orient, COG-encode, CRS-verify tail). It is imported by BOTH worker entrypoints and stays importable by the agent for one transition release (fallback path).

### The worker -> agent manifest contract (`publish_manifest.json`)

Write to `s3://<runs_bucket>/<run_id>/publish_manifest.json`, and ALSO list its URI in `completion.json.output_uris` and add a top-level `completion.json.publish_manifest_uri` pointer so the agent never globs. Concrete shape:

```json
{
  "schema_version": 1,
  "engine": "sfincs_quadtree",
  "run_id": "<run_id>",
  "status": "ok",
  "frame_count": 12,
  "metrics": {
    "max_depth_m": 2.41, "mean_depth_m": 0.63,
    "p95_depth_m": 1.88, "flooded_cell_count": 184213,
    "crs": "EPSG:32616", "units": "meters"
  },
  "layers": [
    {
      "layer_id_stem": "flood-depth-peak",
      "name": "Peak flood depth",
      "layer_type": "raster",
      "role": "primary",
      "style_preset": "continuous_flood_depth",
      "units": "meters",
      "cog_uri": "s3://<runs>/<run_id>/flood_depth_peak.tif",
      "frame_no": null,
      "bbox": [minlon, minlat, maxlon, maxlat],
      "has_overviews": true,
      "band_stats": {"is_categorical": false, "is_rgba": false,
                     "p2": 0.05, "p98": 2.30},
      "metrics": {"max_depth_m": 2.41, "mean_depth_m": 0.63,
                  "p95_depth_m": 1.88, "flooded_cell_count": 184213}
    },
    {
      "layer_id_stem": "flood-depth-frame-01",
      "name": "Flood depth step 1",
      "layer_type": "raster",
      "role": "context",
      "style_preset": "continuous_flood_depth",
      "units": "meters",
      "cog_uri": "s3://<runs>/<run_id>/flood_depth_frame_01.tif",
      "frame_no": 1,
      "bbox": [minlon, minlat, maxlon, maxlat],
      "has_overviews": true,
      "band_stats": {"is_categorical": false, "is_rgba": false}
    }
  ]
}
```

Field-by-field rationale and load-bearing constraints:

- `cog_uri` is a BARE s3:// key, NOT a tile URL. `GRACE2_TILE_SERVER_BASE`/CloudFront base is agent/deploy config; the agent must re-template if the base changes. The worker must never embed a tile URL.
- `style_preset` is a KEY ONLY. The preset -> (rescale, colormap) table (`_TITILER_STYLE_REGISTRY`) STAYS agent-side as the single source of truth shared by all 90+ tools. The worker references `continuous_flood_depth` / `continuous_wave_height` without owning the table.
- `name` MUST be the EXACT web grouping token ("Peak flood depth", "Flood depth step N", "Peak wave height", "Wave height step N"). The web `detectSequentialGroups`/`parseFrameToken` (LayerPanel.tsx) forms the scrubber group CLIENT-SIDE from the NAME token + style_preset + bbox-signature. There is NO server-side temporal-group field. Rename = the scrubber silently never forms (no error).
- `cog_uri` MUST be a DISTINCT key per frame (`flood_depth_frame_NN.tif`), so the TiTiler `url=` -> `_layer_identity_key` is unique and `add_loaded_layer` dedup (pipeline_emitter.py) keeps every frame.
- `band_stats` is the worker's precomputed substitute for the agent's COG re-download in `_resolve_titiler_style_params`. For the known continuous presets (flood/wave) the registry already gives a deterministic rescale, so band_stats is belt-and-suspenders; carry it so the GENERIC-fallback case (`_band1_percentile_rescale`) and the categorical/RGBA passthrough guards never need a COG read. `is_categorical`/`is_rgba` short-circuit those two guards.
- `has_overviews: true` lets the agent skip `_ensure_raster_has_overviews` entirely (the worker guarantees overviews; the SWAN coarse-mesh 768px upsample + the SFINCS COG driver both build them in one pass).
- `metrics` (top-level peak aggregates) replaces the in-process return value `postprocess_flood` gives today and consumed at model_flood_scenario.py:3556 for FloodMetrics. Per-layer `metrics` on each wave layer carries the WaveFieldLayerURI narration scalars (max_hs_m/mean_tp_s/mean_dir_deg/wave_area_km2) so the agent builds the subclass without re-reading the .mat.
- `status` lives in BOTH `completion.json` (so `wait_for_completion` is unchanged) AND the manifest. The honesty floor moves to the worker: a no-flood / no-wave solve (flooded_cell_count==0 / wave_cell_count==0) sets `completion.json.status=error` with the typed code (RUN_OUTPUT_EMPTY / SWAN_OUTPUT_EMPTY) so the agent never registers a status=ok-but-empty layer (Invariant 1 / FR-AS-7). SWAN already has `classify_swan_outcome`; SFINCS currently only checks solver rc and must add the empty-field gate.
- `layer_id_stem` lets the agent mint the final `layer_id` (it appends `-<run_id>`, matching `flood-depth-peak-<run_id>` which downstream Pelicun resolves via the URI registry).

### What stays agent-side (correct as-is, cheap, loop-bound)

- Build the tile URL: `f"{tile_base}/cog/tiles/WebMercatorQuad/{{z}}/{{x}}/{{y}}.png?url={quote(cog_uri)}{style_params}"` (pure string work; tile_base is agent config).
- Apply the style preset by KEY: resolve `style_preset` -> `&rescale/&colormap_name` via the agent-owned `_TITILER_STYLE_REGISTRY`, using manifest `band_stats` for the generic-fallback case (NO COG download).
- Register + persist: `observe_published_layer(layer_id, gcs_uri=cog_uri, wms_url=template)` (NO-OP outside an active dispatch ContextVar, so registration CANNOT move to the worker - it is the seam that lets downstream `*_uri` handles resolve back to the COG; job-0304 proved a missing registration breaks the flood->Pelicun chain), build LayerURI/WaveFieldLayerURI, `emitter.add_loaded_layer` (session-state emit + per-Case durability replay).
- The publish-or-honest-drop gate: if `GRACE2_TILE_SERVER_BASE` is unset -> RASTER_PUBLISH_UNAVAILABLE, layer dropped, metrics still narrate. KEEP this agent-side.
- The vector durable-GeoJSON branch (#165) is separate and untouched.

### What moves to the worker

NetCDF/.mat read, peak/frame selection (incl. `MAX_FLOOD_FRAMES` cap + `_select_frame_time_indices`), quadtree face-rasterize (`_rasterize_face_field` scipy griddata), orient (`_orient_array_for_cog`), NaN-mask, the SWAN 768px nearest upsample, COG+overviews write (`_finalize_cog` / `_write_hs_cog_4326`), the CRS round-trip + |bounds| guards, upload, the empty-field honesty gate, AND the overview build (`_build_cog_with_overviews`) so the agent's `_ensure_raster_has_overviews` becomes a no-op.

## 3. Effort estimate

See `effort_days`. Summary: roughly 11-15 eng-days across worker + agent + infra + tests, SFINCS-first then SWAN. The bulk is the careful lift of the GPL-free postprocess module + parallelization + the manifest contract + the transition fallback; the agent thin-out is small.

## 4. SFINCS-first pilot, then SWAN generalization

See `pilot_plan`.

## 5. Risks and mitigations

See `risks` (GPL isolation, overview parity, idempotency/partial-failure, image-size/build, style-preset ownership, emit-free worker, plus items the maps under-weighted).

## 6. What the maps MISSED or under-weighted

1. Build-context tarball does NOT currently ship `packages/contracts`. The CodeBuild context (`engine_workers_src.tgz`) tars `services/workers` only (infra/aws-codebuild/main.tf, `ls ctx/services/workers`); only the AGENT bundle stages grace2_contracts (scripts/deploy_agent_bundle.sh). The manifest schema therefore should NOT live in `packages/contracts` for the WORKER side (the maps proposed putting it there) unless the worker build context is extended OR the worker writes/reads plain dicts. RECOMMENDATION: define the manifest as a plain JSON dict in the new GPL-free `services/workers/_raster_postprocess/manifest.py` (worker-authored), and add a typed Pydantic mirror in `packages/contracts` consumed ONLY by the agent reader. Two definitions, one schema_version gate. This avoids changing the worker build context.

2. SWAN worker image lacks xarray AND the SWAN postprocess does not need it - but the maps did not flag that the SWAN postprocess uses `scipy.io.loadmat` + rasterio ONLY (both already in the SWAN venv). Confirmed: SWAN Dockerfile installs numpy/scipy/rasterio/boto3. SFINCS image has rasterio/geopandas/xugrid via the cht closure; xarray + `scipy.interpolate.griddata` come transitively but are NOT asserted by the build smoke. The build smoke MUST be extended to `import xarray; from scipy.interpolate import griddata; rasterio COG write; pyproj.Transformer` in one process (the GDAL/PROJ-clash tripwire). This is the single highest build-time risk.

3. The `mesh.geojson` and the `*.nc`/`*.tif` upload sweep already runs in the SFINCS worker. So SFINCS needs NO new upload code for COGs (the `*.tif` glob catches `flood_depth_*.tif`), but SWAN's `DEFAULT_OUTPUT_GLOBS` is an explicit list (no `*.tif`) and MUST add the COG names or the worker COGs never upload.

4. Frame-progress breadcrumb loss (task #168). The nested substep cards (substep(emitter,'postprocess_flood'/'publish_layer'/'postprocess_swan') in model_flood_scenario.py:3175/3251/3407 and model_wave_scenario.py:379/401) currently show the conversion as LIVE cards. Moving conversion into Batch means that live signal disappears unless the worker emits frame-conversion progress (e.g. writes a `progress.json` / appends to completion, or via Batch CloudWatch logs the agent could tail). The maps mention this but it is a real UX regression to design for, not an afterthought - decide explicitly whether to keep a coarse "Converting N frames on the solver" card driven by manifest presence vs per-frame progress.

5. `RunResult.output_uri` is a directory PREFIX (`s3://<bucket>/<run_id>/`), and the agent must now read `publish_manifest.json` under it. The manifest pointer should be an explicit `completion.json.publish_manifest_uri` so the agent never has to construct/guess the key (the maps say "give explicit per-layer keys" but the entry point is the manifest URI itself).

6. Spot-interruption atomicity. A longer worker (solve + heavy parallel postprocess) widens the Spot-reclaim window. `completion.json` is already written LAST (entrypoint.py:2067) - the manifest must be written BEFORE completion.json so that `status=ok` in completion.json implies the manifest + all listed COGs exist. A reclaim mid-postprocess leaves NO completion.json -> the agent's `wait_for_completion` correctly sees the job FAILED/retry, never registers half-written COGs. This ordering is load-bearing and must be a deliberate step.

7. Two SFINCS workers, not one. The combined quadtree+SnapWave path (services/workers/sfincs_deckbuilder) and the regular-grid path (services/workers/sfincs) are SEPARATE images. The shared `_raster_postprocess` module must be COPY'd into BOTH or the regular-grid SFINCS path regresses to raw-NetCDF. (The maps flag this; reinforcing because it doubles the SFINCS surface and the per-image build smoke.)

8. The SWAN path does NOT have a `quadtree` branch but DOES have the `_upsample_for_cog` overview-forcing step (load-bearing for coarse meshes). The SFINCS quadtree path can produce SMALL rasters too (a tiny AOI quadtree could fall below the COG overview threshold) - the worker must apply the same min-dimension overview guarantee on the SFINCS side, OR the agent's `_ensure_raster_has_overviews` no-op assumption breaks for small SFINCS rasters. Verify `_overview_factors` against the smallest expected SFINCS quadtree raster.

9. `MAX_FLOOD_FRAMES` (and NODATA thresholds, `_COG_MIN_DIM_PX`) are agent-process env/constants today and feed the PRE-RUN granularity gate (`_estimate_frame_count` at model_flood_scenario.py:259 imports `MAX_FLOOD_FRAMES`). After the move these must be (a) set in the WORKER Batch job-def env, ideally driven per-run from the build-spec so the #154 granularity gate still controls frame count, AND (b) the agent import must stay valid for the pre-run estimate. Two homes for one knob - keep the build-spec authoritative and pass it through.
