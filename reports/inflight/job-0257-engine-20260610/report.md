# job-0257-engine-20260610 — HILLSHADE NO-RENDER: root cause + fix

**Specialist:** engine (Fable-5 critical batch)
**Status:** code fixes LANDED + live-proven; final pixel rendering of cache-bucket rasters is gated on ONE user IAM command (see evidence/USER_UNBLOCK.md — also heals a P0 found during diagnosis: the canonical demo project is currently 500ing project-wide)
**Date:** 2026-06-10

## Symptom

User ran DEM -> compute_hillshade -> publish_layer live (session 01KTQK7RA0Y3GDKS3YH40EXHYH, "show me chicago elevation map (hillshade)" 11:59; "show me the hillshade of seattle" 12:29). Tool cards completed "successfully"; nothing appeared on the map. The flood layer in the same session rendered fine (11:57).

## Root cause — FOUR stacked defects (each verified against live artifacts, not mocks)

### 1. Gemini hallucinates the tail of 32-hex cache keys when echoing gs:// URIs (3/3 occurrences)

/tmp/agent_demo_ready.log:

| run | compute_hillshade cached (cache.py miss-write) | publish_layer was called with |
|---|---|---|
| chicago #1 (log 450 vs 454) | 090a4ff8d9a083f67c0b355caf40241a.tif | 090a4ff8d9a083b28499252309d12999.tif |
| seattle (log 512 vs 515) | 4007d642cb157d11f5db275a50286ae5.tif | 4007d642cb157d22b1113a4b912a2ee3.tif |
| chicago #2 (log 561 vs 564) | 090a4ff8d9a083f67c0b355caf40241a.tif (hit) | 090a4ff8d9a08321a43a7a9437b0e51c.tif |

First ~14 hex chars preserved, remainder invented. Live GCS listing: the hallucinated objects do not exist. (Flood path immune: composer passes the URI programmatically.)

### 2. The failed publish was silently swallowed — false success

Worker: nonexistent raster -> QgsRasterLayer.isValid() false -> WorkerError -> WorkerResult(status="error") (services/workers/pyqgis/worker.py:916-927) — but __main__ exits 0 BY DESIGN (services/workers/pyqgis/__main__.py:41-44,191; "envelope is the source of truth"). publish_layer polls only the Cloud Run execution state and never consumes the envelope (OQ-62-PUBSUB-COMPLETION-POLL) -> CONDITION_SUCCEEDED -> WMS URL returned as success. Live Cloud Logging (grace-2-pyqgis-worker 19:07:10Z/19:33:55Z/19:37:13Z): status:"error", "append_raster_layer: QgsRasterLayer failed to initialize for uri='/vsigs/.../090a4ff8d9a083b28499252309d12999.tif'" for all three. Served .qgs (updateTime 19:04:08Z — BEFORE all three publishes) has zero hillshade layernames.

### 3. compute_hillshade output CRS silently degraded to LOCAL_CS

The agent invokes the conda-env gdaldem via bare subprocess.run with no PROJ_LIB/PROJ_DATA -> proj.db not found -> output CRS degrades from the DEM's EPSG:5070 to LOCAL_CS["NAD83 / Conus Albers"] (epsg=None). Reproduced deterministically (bare env -> LOCAL_CS; PROJ_LIB=<prefix>/share/proj -> EPSG:5070). All pre-fix cache hillshades are CRS-broken; QGIS cannot reproject LOCAL_CS for WMS. Siblings (compute_slope/aspect/colored_relief, clip_raster_*) share the invocation pattern — follow-up job recommended.

### 4. QGIS Server SA has NO read grant on the cache bucket — cache-sourced rasters have NEVER been server-renderable; one such layer now 500s the whole canonical project (P0)

gs://grace-2-hazard-prod-cache IAM: objectAdmin for agent-runtime + pyqgis-worker-runtime, objectViewer for sfincs-runtime — NOTHING for grace-2-qgis-server@ (the job-0061 grant covered only the runs bucket). Any /vsigs/grace-2-hazard-prod-cache/... layer is invalid at render time. Worse: elevation-washington (cache-sourced; published 12:00:58, landed in the .qgs at 12:04:08) + no QGIS_SERVER_IGNORE_BAD_LAYERS means QGIS Server now rejects the ENTIRE grace2-sample.qgs: GetCapabilities AND every GetMap (basemap, flood) return 500 "Layer(s) not valid" (verified 13:10-13:12, evidence/canonical_project_500.xml). The user's live demo map is down until the IAM grant; A/B proof below isolates this exactly.

## Fixes landed (engine/agent ownership; no image rebuild)

services/agent/src/grace2_agent/tools/publish_layer.py:
1. Pre-dispatch GCS validation + deterministic auto-correction (_validate_and_correct_layer_uri): existing object -> pass; missing -> unique >=8-char basename-prefix match in the same directory is substituted (WARNING log); else typed PublishLayerError("LAYER_URI_NOT_FOUND", retryable=True) listing the real objects (feeds the job-0177 retry loop).
2. Post-publish .qgs verification (_verify_layer_in_qgs): CONDITION_SUCCEEDED no longer suffices — the .qgs is read back and must contain <layername>{layer_id}</layername>, else PublishLayerError("WORKER_PUBLISH_NOT_APPLIED"). Closes the exit-0-on-error false-success gap without Pub/Sub or worker rebuild. Both helpers fail-open on missing storage access.
3. set_storage_client DI seam; PublishLayerError gains retryable.

services/agent/src/grace2_agent/tools/compute_hillshade.py:
4. _gdaldem_subprocess_env: wires PROJ_LIB/PROJ_DATA/GDAL_DATA from the resolved binary prefix into the subprocess (setdefault).
5. _ensure_output_crs_matches_dem: post-gdaldem CRS stamp from the source DEM (all 5 style branches incl. swiss_double); never raises.

## Tests

- tests/test_publish_layer.py: 16 pass (auto-correction of the exact demo URI; ambiguity refusal; retryable LAYER_URI_NOT_FOUND with listing; WORKER_PUBLISH_NOT_APPLIED; corrected RASTER_URI inside the dispatched RunJobRequest).
- tests/test_compute_hillshade.py: 17 pass (end-to-end CRS preservation with PROJ vars stripped; stamp unit tests).
- Related sweep: 1743 passed, 1 xfailed.

## Live Gemini-free proof (within policy limits)

1. CRS fix: corrected hillshades regenerated through the real tool (new cache keys 63871724...fe / 6a25fb94...66, EPSG:5070 verified).
2. Hallucination fix, live: publish_layer called with deliberately mangled tail ...63871724ee0db26b9999888877776666.tif against proof project grace2-job0257-proof.qgs -> production log shows auto-correction (16-char prefix) -> worker CONDITION_SUCCEEDED -> post-publish verification passed (evidence/proof_publish_autocorrect.log).
3. False-success fix + render pipeline, live A/B on clean project grace2-job0257-proof2/3.qgs:
   - basemap GetMap -> 200 PNG (evidence/proof2_basemap.png) — server + clean project healthy;
   - full fixed publish path with an EXISTING runs-bucket COG -> layer lands -> GetMap returns NON-BLANK pixels: 3575 opaque px, 264 distinct colors (evidence/proof_pipeline_getmap.png, composite proof_pipeline_composite.png);
   - identical publish from the CACHE bucket -> "Layer(s) not valid" (evidence/cache_bucket_layer_blocked.xml) — isolating defect #4 as the only remaining link for hillshade pixels.
4. Hillshade-specific GetMap pixels therefore require the one-command IAM grant (denied to the agent by the auto-mode classifier — correctly; see evidence/USER_UNBLOCK.md).

## Policy-blocked actions (all attempted, all classifier-denied, all documented in USER_UNBLOCK.md)

- IAM grant of roles/storage.objectViewer on the cache bucket to the QGIS Server SA (THE structural fix; also un-breaks the canonical project).
- Overwrite/delete of the two CRS-broken cache objects (corrected files staged at /tmp/job0257_*_hs_fixed.tif).
- Staging evidence rasters into the runs bucket; republishing the user's three hillshade layer ids into the canonical .qgs.
- QGIS Server service env update (cache strategy + IGNORE_BAD_LAYERS).

## Follow-ups for the orchestrator

1. USER must run the two commands in evidence/USER_UNBLOCK.md (P0 — demo map down).
2. Agent restart on :8765 to load the fixed code (post-demo).
3. Follow-up engine job: apply _gdaldem_subprocess_env pattern to compute_slope/aspect/colored_relief + clip tools.
4. Infra job: land the cache-bucket grant + QGIS_SERVER_IGNORE_BAD_LAYERS in OpenTofu (infra/qgis-server.tf) so it survives re-provisioning.
5. Consider a worker `remove-layer`/`replace-layer` op (addMapLayer appends; same-name republish duplicates rather than replaces).
6. Architectural: URI-passing between LLM turns is fragile — consider layer-handle indirection (LLM passes layer_id, server resolves URI from session-tracked LayerURIs) to retire the hallucination class entirely.
