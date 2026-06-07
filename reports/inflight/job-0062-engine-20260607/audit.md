# Audit: atomic `publish_layer` tool via PyQGIS worker round-trip

**Job ID:** job-0062-engine-20260607, **Sprint:** sprint-09 Stage B (linchpin), **Auditor:** Development Orchestrator, **Status:** assigned

## Task Assignment

**Specialist:** engine

**Prerequisites:**
- `docs/decisions/layer-emission-contract.md` (ADOPTED 2026-06-07)
- **job-0060 (APPROVED, commit edfeb06):** `run_model_flood_scenario` returns LayerURI; PipelineEmitter auto-emit fires.
- **job-0061 (APPROVED, commit 1b2f989):** QGIS Server SA reads runs bucket; live WMS GetMap on runs-bucket COG verified returning a real image.
- **job-0063 (APPROVED, commit 0990d1c):** COG CRS now correctly EPSG:32617.
- job-0040 (APPROVED): SFINCS Cloud Run Job pattern — mirror its Cloud Workflows + execute API pattern for invoking workers.
- job-0041 (APPROVED): `run_solver` + `wait_for_completion` agent-side dispatch pattern — the reference design for the new tool.
- Existing PyQGIS worker substrate: `services/workers/pyqgis/worker.py` + `__main__.py` — already has GCS round-trip, /vsigs/ URL handling, Pub/Sub completion publish, `_apply_style_preset`. **Missing: raster layer add path** (only `_append_memory_polygon_layer` exists).

**SRS references** (narrow file loading only):
- `docs/srs/03-functional-requirements.md` FR-QS-5 (style presets) + FR-QS-6 (PyQGIS worker round-trip) + FR-MP-3 (source of truth — `.qgs` in GCS is canonical) + the new FR-MP-6 Case UX (informs why this matters)
- DO NOT load `docs/SRS_v0.3.md` monolith.

**Required reads:**
- `services/workers/pyqgis/worker.py` lines 268-335 — existing polygon layer add + style preset application
- `services/workers/pyqgis/__main__.py` — CLI contract; you'll need to extend it for the raster add path
- `services/agent/src/grace2_agent/tools/run_solver.py` (if exists, otherwise tools/passthroughs.py for Cloud Run Job execute pattern) — reference for how the agent dispatches to a Cloud Run Job
- `services/agent/src/grace2_agent/workflows/model_flood_scenario.py` — the workflow that calls postprocess_flood and currently emits the LayerURI directly; you'll integrate publish_layer between postprocess_flood + the LayerURI return
- `styles/basemap.qml` — reference QML structure
- `infra/worker.tf` — PyQGIS worker Cloud Run Job config; you'll likely need to invoke it via `gcloud run jobs execute` API or equivalent

### Why this job exists

Job-0058 produces a real flood-depth COG. Job-0060 wires the agent to emit `LayerURI` so PipelineEmitter populates `session-state.loaded_layers`. Job-0061 grants QGIS Server access to read the COG. But the COG's `gs://` URI is not directly renderable in MapLibre — the client needs a **WMS URL** that QGIS Server can serve.

This job closes that loop: a new atomic tool `publish_layer(gs_uri, layer_id, style_preset)` invokes the PyQGIS worker to mutate the canonical `.qgs` project (add the COG as a raster layer with the style preset), writes back to the `-qgs` bucket, and returns the resulting WMS URL. The workflow then puts the WMS URL into the `LayerURI.uri` so the client gets a renderable URL via `session-state.loaded_layers`.

### Scope

1. **Author `styles/continuous_flood_depth.qml`** (engine asset):
   - Continuous color ramp for floats representing water depth in meters
   - Suggested ramp: shallow=light-blue → deep=dark-blue (matplotlib's `Blues` colormap is a good reference)
   - Min=0.0, max=3.5 m (matches the M5 demo's hmax 3.52); use `ColorRampShader` with `INTERPOLATED` mode
   - Nodata transparent
   - Reference: read the existing `styles/basemap.qml` to mirror its QML format; this new file is a `singleBand` raster style not a vector style
   - Note in `styles/README.md` (update the line that says "Empty scaffold...") that `continuous_flood_depth.qml` is now present

2. **Extend the PyQGIS worker** (`services/workers/pyqgis/worker.py` + `__main__.py`):
   - Add a new function `_append_raster_layer(project, raster_uri, layer_id, style_qml_path)` mirroring the existing `_append_memory_polygon_layer` pattern. Uses `QgsRasterLayer(raster_uri, layer_id, "gdal")`; `project.addMapLayer(layer)`; `_apply_style_preset(layer, style_qml_path)`.
   - The `raster_uri` arrives as `/vsigs/<runs-bucket>/<run_id>/flood_depth_peak.tif` (the worker SA needs read on the runs bucket — verify; if missing, surface as OQ and request infra follow-up, but it should be configured similar to job-0061's qgis-server grant; check `infra/buckets.tf` for pyqgis-worker grants).
   - Extend the CLI in `__main__.py` to accept an operation discriminator. Options:
     - **Option A (preferred — minimal disturbance):** add `--op publish-raster` flag; when set, the worker takes additional args `--raster-uri`, `--raster-layer-id`, `--style-preset-name` and calls `_append_raster_layer` instead of `_append_memory_polygon_layer`.
     - **Option B:** new sub-command. Pick whichever is cleaner; document the choice.
   - Existing polygon path stays working (regression-guarded).
   - The Pub/Sub completion envelope payload includes the resulting WMS URL: `<qgis-server>/ogc/wms?MAP=/mnt/qgs/<qgs-key>&LAYERS=<layer_id>` so the caller can return it.

3. **Author new atomic tool `services/agent/src/grace2_agent/tools/publish_layer.py`**:
   - Signature: `publish_layer(layer_uri: str, layer_id: str, style_preset: str = "continuous_flood_depth", project_qgs_uri: str | None = None) → str` (returns the WMS URL)
   - Default `project_qgs_uri` resolves to the canonical session/project `.qgs` URI (start with `gs://grace-2-hazard-prod-qgs/grace2-sample.qgs` as the v0.1 default; the FR-MP-6 Case UX will eventually own per-Case project resolution).
   - Invokes the PyQGIS worker Cloud Run Job via `google.cloud.run_v2.JobsClient` (or `aiplatform`/`gcloud run jobs execute` API equivalent — mirror the SFINCS solver dispatch in `tools/run_solver.py`).
   - Passes the operation discriminator + raster URI + layer ID + style preset via env vars (Cloud Run Job env override) and waits for completion (or follows Pub/Sub completion pattern from job-0041's `wait_for_completion`).
   - Returns the WMS URL on success; raises typed error on failure.
   - Register as `@register_tool(cacheable=False, ttl_class="live-no-cache", source_class="publish_layer")` per FR-CE-8 + FR-DC-6 enumeration — this is a side-effect tool, not a data fetcher.

4. **Integrate into `services/agent/src/grace2_agent/workflows/model_flood_scenario.py`**:
   - After `postprocess_flood(...)` returns and BEFORE `run_model_flood_scenario` returns the LayerURI:
     - Compose a layer_id (e.g., `flood-depth-peak-<run_id>`)
     - Call `publish_layer(layer_uri=cog_gs_uri, layer_id=<id>, style_preset="continuous_flood_depth")` → wms_url
     - Substitute the LayerURI's `uri` field with the wms_url (or carry both gs:// + wms_url if the LayerURI shape supports it — check pydantic shape; if it doesn't carry both, add a `wms_url` field via a small schema additive amendment IF allowed without breaking the contracts test count; if not, leave gs:// as `uri` and the client will rely on `style_preset` + LayerPanel to construct the WMS URL via a known QGIS Server endpoint convention — but PREFER the LayerURI carrying wms_url so the client is dumb)
   - Surface any contract/schema decision as an OQ if it's not 1:1 obvious; don't fabricate fields.

5. **Tests**:
   - Unit tests for `publish_layer` atomic tool with the worker mocked
   - Unit test that `model_flood_scenario` calls `publish_layer` after `postprocess_flood` succeeds
   - Test that the resulting LayerURI carries the WMS URL (the value the client will actually use)
   - Regression: existing polygon worker path still passes (unit-test the new operation discriminator without breaking the old one)
   - Existing 25 model_flood_scenario tests + worker tests must stay green

6. **Live verification**:
   - Copy `reports/complete/job-0058-engine-20260607/evidence/smoke_demo.py` to `reports/inflight/job-0062-engine-20260607/evidence/smoke_demo.py`
   - Run it — the chain should now: produce a flood-depth COG → call publish_layer → publish_layer invokes the worker → worker mutates the .qgs (real GCS write) → returns WMS URL → workflow returns LayerURI with wms_url
   - Capture the full session_state envelope showing `loaded_layers[0].uri` is a real WMS URL
   - Verify the WMS URL renders: `curl '<wms_url>&REQUEST=GetMap&SERVICE=WMS&BBOX=...&FORMAT=image/png&...'` → real PNG, not GDAL error
   - Save the resulting PNG as evidence

7. **Cost discipline**: PyQGIS workers ARE costly Cloud Run Jobs. Keep the worker minimum scaffold — don't over-engineer. The existing worker has minutes-of-life patterns; mirror them.

### File ownership (exclusive)
- `services/workers/pyqgis/worker.py` — new `_append_raster_layer` + dispatch
- `services/workers/pyqgis/__main__.py` — CLI flag extension
- `services/agent/src/grace2_agent/tools/publish_layer.py` (NEW)
- `services/agent/src/grace2_agent/workflows/model_flood_scenario.py` — only the publish_layer integration point after postprocess_flood
- `styles/continuous_flood_depth.qml` (NEW)
- `styles/README.md` — small note that the preset now exists
- `services/agent/tests/test_publish_layer.py` (NEW)
- `services/workers/pyqgis/tests/` — additive tests for the new operation
- `services/agent/tests/test_model_flood_scenario.py` — small additive test verifying publish_layer integration
- `reports/inflight/job-0062-engine-20260607/`

### FROZEN
- `services/agent/src/grace2_agent/workflows/sfincs_builder.py`
- `services/agent/src/grace2_agent/workflows/postprocess_flood.py`
- `services/agent/src/grace2_agent/pipeline_emitter.py`
- `services/agent/pyproject.toml`
- `services/workers/sfincs/**`
- `packages/contracts/**` — if you find LayerURI needs a `wms_url` field added, propose as schema amendment and STOP — don't edit packages/contracts; surface as OQ instead
- `infra/**` — if pyqgis-worker SA needs runs bucket grant, surface as OQ for an infra follow-up (do NOT edit Tofu)
- All other workflows/* and tools/*
- `docs/srs/**` — same as contracts; propose, don't edit

### Acceptance criteria
- [ ] `styles/continuous_flood_depth.qml` authored — continuous singleBand raster ramp 0–3.5 m
- [ ] PyQGIS worker `_append_raster_layer` added; CLI supports the new operation discriminator; existing polygon path still works
- [ ] `tools/publish_layer.py` atomic tool registered (`cacheable=False`); 17+ tools at startup
- [ ] `model_flood_scenario` integrates `publish_layer` between `postprocess_flood` and the LayerURI return
- [ ] LayerURI returned from the workflow carries a real WMS URL (whether via `uri` field directly or via a new `wms_url` field depending on contract — document the decision)
- [ ] Tests pass (existing + new); ≥4 new tests covering the worker + the agent tool + the workflow integration
- [ ] Live evidence: end-to-end run produces a WMS URL that returns a real PNG when curled
- [ ] No edits to FROZEN paths
- [ ] Single commit
