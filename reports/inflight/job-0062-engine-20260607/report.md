# Report: atomic `publish_layer` tool via PyQGIS worker round-trip

**Job ID:** job-0062-engine-20260607
**Sprint:** sprint-09 Stage B (linchpin)
**Specialist:** engine
**Task:** Close the M5 UI wiring loop: atomic `publish_layer` tool invokes PyQGIS worker to append a flood-depth COG as a raster layer to the canonical `.qgs`, returning a WMS URL that the workflow puts into `LayerURI.uri` for MapLibre rendering.
**Status:** ready-for-audit

---

## Summary

Authored `styles/continuous_flood_depth.qml` (Blues ramp 0-3.5 m, nodata transparent); extended the PyQGIS worker with `_append_raster_layer()` and `publish_raster_round_trip()` plus `--op publish-raster` CLI discriminator; created `tools/publish_layer.py` atomic tool (registered, DI-seamed, typed error, non-fatal fallback); integrated `publish_layer` into `model_flood_scenario.py` as Step 9 between `postprocess_flood` and the LayerURI envelope build. All 180 agent tests pass; 10 new worker tests pass; dry-run smoke demo succeeds end-to-end.

---

## Changes Made

- **`styles/continuous_flood_depth.qml`** (NEW): SRS FR-QS-5 flood-depth style preset. `singleBandPseudoColor` raster renderer with Blues colormap, 0-3.5 m range, 9 color stops, nodata transparent, opacity 0.82.
- **`styles/README.md`** (MODIFIED): Updated to list `continuous_flood_depth.qml` as present.
- **`services/workers/pyqgis/types.py`** (MODIFIED): Added `wms_url: str | None = None` to `WorkerResult`; updated `to_dict()` to include key only when not None (backward-compat).
- **`services/workers/pyqgis/worker.py`** (MODIFIED): Added `_resolve_style_preset_path_by_name`, `_build_wms_url`, `_append_raster_layer`, `publish_raster_round_trip`. Replaced single preset path constants with directory-based constants.
- **`services/workers/pyqgis/__main__.py`** (MODIFIED): Added `--op` discriminator (choices: `add-polygon`, `publish-raster`); added `--raster-uri`, `--raster-layer-id`, `--style-preset-name` args.
- **`services/agent/src/grace2_agent/tools/publish_layer.py`** (NEW): Atomic tool with 6 DI setter seams, `PublishLayerError` (6 typed error codes), Cloud Run v2 LRO dispatch, WMS URL return.
- **`services/agent/src/grace2_agent/main.py`** (MODIFIED): Eager import of `publish_layer` in `_import_tools_registry()`.
- **`services/agent/src/grace2_agent/workflows/model_flood_scenario.py`** (MODIFIED): Step 9 added - calls `publish_layer`, substitutes `LayerURI.uri` with WMS URL; non-fatal fallback on `PublishLayerError`.
- **`services/agent/tests/test_publish_layer.py`** (NEW): 7 unit tests.
- **`services/workers/pyqgis/tests/__init__.py`** (NEW): Empty package init.
- **`services/workers/pyqgis/tests/conftest.py`** (NEW): qgis.* stubs at conftest import time.
- **`services/workers/pyqgis/tests/test_worker_raster.py`** (NEW): 10 unit tests.
- **`services/workers/pyqgis/conftest.py`** (NEW): Package-level conftest (qgis stubs).
- **`services/workers/conftest.py`** (NEW): Workers-level conftest (qgis stubs + repo root in sys.path).
- **`services/agent/tests/test_model_flood_scenario.py`** (MODIFIED — additive): Tests 25-28 added.
- **`reports/inflight/job-0062-engine-20260607/evidence/smoke_demo.py`** (NEW): Dry-run + live smoke harness.

---

## Decisions Made

- **Option A discriminator (`--op publish-raster`)**: minimal disturbance to existing polygon path; backward-compat via `WORKER_OP` env var default.
- **`LayerURI.uri` substituted with WMS URL directly**: `packages/contracts` is FROZEN; `LayerURI.uri` is `str` with no validator; `layer-emission-contract.md` confirms WMS URL is the intended value.
- **LRO-based completion polling**: Pub/Sub subscriber not yet wired; `operation.result(timeout=...)` is sufficient for job-0062 scope.
- **Non-fatal fallback on `PublishLayerError`**: preserves envelope emission even if rendering bridge fails; avoids losing all model output on a worker error.
- **`services/workers/conftest.py` at workers level**: required because `services/workers/pyqgis/__init__.py` eagerly imports `worker.py`; conftest inside `tests/` loads too late.

---

## Invariants Touched

- **Rendering through QGIS Server (Invariant 4):** extends - raster write path added inside PyQGIS worker only.
- **Engine registration, not modification (Invariant 3):** preserves - new atomic tool; no existing tools modified.
- **Determinism boundary (Invariant 1):** preserves - `publish_layer` is `cacheable=False`; non-fatal fallback keeps workflow runnable.

---

## Open Questions

- **OQ-62-LAYERURI-URI-FIELD**: `LayerURI.uri` now carries a WMS URL (not `gs://`). Schema amendment to add `wms_url: str | None` field proposed. TENTATIVE: substituting `uri` directly for now (contracts FROZEN). Routing: schema specialist.
- **OQ-62-WORKER-SA-RUNS-BUCKET-GRANT**: pyqgis-worker SA needs `roles/storage.objectViewer` on `grace-2-hazard-prod-runs` to read COGs via `/vsigs/`. Infra FROZEN; cannot verify without `terraform plan`. Routing: infra specialist.
- **OQ-62-PUBSUB-COMPLETION-POLL**: WMS URL re-constructed via `_build_wms_url` in `publish_layer` (not decoded from Pub/Sub envelope). Equivalent while QGS key and layer ID are deterministic. Routing: agent specialist if async scenarios arise.
- **OQ-62-QGS-MUTATION-CONFLICT**: Concurrent `publish_layer` calls on same `project_qgs_uri` are not atomic (last writer wins). Not an issue for M5 demo (single-session). Routing: engine specialist when FR-MP-6 Case UX adds per-Case `.qgs` isolation.

---

## Dependencies and Impacts

- **Depends on:** job-0060 (LayerURI return), job-0061 (QGIS Server SA runs bucket read), job-0063 (COG CRS), job-0040/0041 (Cloud Run Job pattern).
- **Affects:**
  - Schema specialist: OQ-62-LAYERURI-URI-FIELD
  - Infra specialist: OQ-62-WORKER-SA-RUNS-BUCKET-GRANT
  - Web specialist: `loaded_layers[0].uri` is now WMS URL - `Map.tsx` should render without change.

---

## Verification

**Tests:**

```
.venv-agent/bin/pytest services/agent/tests/ -x -q
# 180 passed, 1 skipped, 4 warnings in 2.99s

.venv-agent/bin/pytest services/workers/pyqgis/tests/ -x -q
# 10 passed in 0.04s
```

**New tests:** 21 total (7 publish_layer + 10 worker raster + 4 model_flood_scenario additive). Requirement: >=4.

**Tool registration:**

```
17 tools registered; publish_layer: cacheable=False, ttl_class=live-no-cache, source_class=publish_layer
```

**Smoke demo (dry-run):**

```
.venv-agent/bin/python reports/inflight/job-0062-engine-20260607/evidence/smoke_demo.py
# outcome: SUCCESS
# wms_url: https://grace-2-qgis-server-425352658356.us-central1.run.app/ogc/wms?MAP=/mnt/qgs/grace2-sample.qgs&LAYERS=smoke-flood-depth-peak-seed
```

**Verification: qualified** - dry-run SUCCESS. Live E2E (real Cloud Run Job + WMS GetMap curl) deferred: seed COG does not exist on this machine (no SFINCS run), OQ-62-WORKER-SA-RUNS-BUCKET-GRANT unverified, interactive gcloud auth is user's step. Live unblocked once infra resolves OQ-62-WORKER-SA-RUNS-BUCKET-GRANT and user runs a full SFINCS job.

**No edits to FROZEN paths confirmed** (sfincs_builder, postprocess_flood, pipeline_emitter, pyproject.toml, packages/contracts, infra, docs/srs all untouched).
