# Audit: Sample .qgs + styles/basemap.qml preset stub authored and uploaded to GCS

**Job ID:** job-0019-engine-20260605
**Sprint:** sprint-04
**Auditor:** Development Orchestrator
**Status:** assigned

## Task Assignment

**Specialist:** engine
**Prerequisites:** job-0018-infra-20260605 (must be `approved`) — read its audit for the deployed QGIS Server URL, the bucket names (`grace-2-hazard-prod-qgs`/`-cog`/`-fgb`), the worker SA name for future binding, the `styles/` bake mechanism inside the QGIS Server image, and the pinned `qgis/qgis-server` image tag. job-0013-schema-20260605 (complete) — `grace2-contracts` v0.1.0 is in force but this job does NOT consume contracts (no `AssessmentEnvelope`/`ResultLayer` shapes touched in M2; first engine consumption lands in M5+). job-0022-infra-20260605 (must be `approved` before local PyQGIS work — provides the recreated `grace2` conda env on this Debian 13 box).
**SRS references:** FR-QS-2 (`.qgs` in GCS, canonical at M2); FR-QS-5 (QML style preset library — first preset stub `styles/basemap.qml` lands as the basemap preset, infra bakes the same file into the QGIS Server container at image build time); FR-DT-1 (Tier A basemap — OSM as the v0.1 reference); FR-MP-1 (`.qgs` in GCS as canonical payload); §2.3 (engine common contract substrate); Decision B (QGIS Server as rendering backend); Decision C (PyQGIS workers for project manipulation — substrate); Invariant 4 (Rendering through QGIS Server). This job does NOT introduce an FR-TA-1 workflow (modeling workflows deferred to M5/M6). No LLM in any code path lands in this job (Invariant 2).

### Environment

Dev + prod substrate Linux (Debian 13, `Linux maturin 6.12.74+deb13+1-amd64`, x86_64). Container builds `linux/amd64`-only. Consume live cloud substrate from `PROJECT_STATE.md` + the job-0018 audit: GCS bucket `grace-2-hazard-prod-qgs` for canonical `.qgs` upload, deployed QGIS Server URL for verification. Local PyQGIS work runs in the `grace2` conda env recreated by job-0022 (QGIS 3.40.3-Bratislava on Debian 13). `python3-venv` is unavailable on Debian 13; `virtualenv` is the working substitute if a venv is needed for non-PyQGIS helpers. `gcloud auth login`/`application-default login` already present from M1.

### Scope

1. **Author the canonical sample `.qgs`** at `services/workers/pyqgis/sample_project/grace2-sample.qgs`:
   - CRS: EPSG:4326 (`AssessmentEnvelope.bbox` convention).
   - Extent: CONUS (roughly `-125, 24, -66, 50`).
   - Single basemap reference layer named `osm-basemap` (the QML preset stub matches this exact name). TENTATIVE source choice (surface as OQ): OSM XYZ tile reference (`https://tile.openstreetmap.org/{z}/{x}/{y}.png`) embedded as an XYZ tiles layer in the project — matches FR-DT-1 v0.1 Tier A basemap source exactly, zero new infra. Alternative: a static hand-authored CONUS-polygon FlatGeobuf committed alongside (no network calls from QGIS Server at render time; smaller blast radius). Pick OSM XYZ tentatively; surface the OSM-tile-usage-policy implication that QGIS Server proxies OSM tiles (trivial at M2 smoke scale).
   - Project must round-trip cleanly: `QgsProject.read()` then `QgsProject.write()` produces an identical (or whitespace-equivalent) file. Verify locally in the `grace2` env with a small transcript.
   - Hand-author via QGIS Desktop in the `grace2` env or via PyQGIS scripting — surface the authoring path in `sample_project/README.md`.
2. **Author `styles/basemap.qml`**:
   - QML preset stub matching the `osm-basemap` layer name (so `apply_style_preset("osm-basemap", "basemap")` resolves at worker time in job-0020).
   - Minimal renderer (single-symbol or raster default) — this is a STUB; the seven full FR-QS-5 presets (flood depth, velocity, arrival time, DEM, landcover, hurricane track, affected buildings) land in later milestones. State the stub status in the file's `<comment>` block.
   - Infra bakes this file into the QGIS Server image via the `COPY styles/ /opt/styles/` step in `infra/qgis-server/Dockerfile` (job-0018 mechanism). After authoring, rebuild + redeploy the QGIS Server image via `make qgis-server-build && make qgis-server-push && make qgis-server-deploy` — or document the trigger as a follow-up for job-0021 (surface as OQ).
3. **Upload the canonical `.qgs` to GCS**: `gcloud storage cp services/workers/pyqgis/sample_project/grace2-sample.qgs gs://grace-2-hazard-prod-qgs/grace2-sample.qgs`. Verify with `gcloud storage ls -L gs://grace-2-hazard-prod-qgs/grace2-sample.qgs`.
4. **Document provenance** in `services/workers/pyqgis/sample_project/README.md`:
   - How the `.qgs` was authored (QGIS Desktop in `grace2` env vs PyQGIS script).
   - Layer manifest (just the one `osm-basemap` layer at M2).
   - How to regenerate from scratch (`make` target if added — otherwise documented commands).
   - Note that this `.qgs` is the canonical M2 sample and is consumed by job-0020 worker + job-0023 acceptance.
5. **Verify live against deployed QGIS Server** (the substrate from job-0018):
   - `curl -sf "<qgis-server-url>/ogc/?MAP=/vsigs/grace-2-hazard-prod-qgs/grace2-sample.qgs&SERVICE=WMS&REQUEST=GetCapabilities"` returns XML naming the `osm-basemap` layer. Verbatim transcript in report.
   - `curl -sf "<qgis-server-url>/ogc/?MAP=/vsigs/grace-2-hazard-prod-qgs/grace2-sample.qgs&SERVICE=WMS&REQUEST=GetMap&LAYERS=osm-basemap&BBOX=-125,24,-66,50&CRS=EPSG:4326&WIDTH=512&HEIGHT=256&FORMAT=image/png" -o /tmp/sample-getmap.png` returns a non-blank PNG (basic file-size or pixel-variance check). Attach the PNG path to the report (acceptance test in job-0023 re-runs and saves to `tests/m2/artifacts/sample-getmap.png`).
6. **Open Questions to surface (TENTATIVE-tagged):**
   - Basemap source: OSM XYZ tile reference vs static CONUS-polygon FlatGeobuf vs MapTiler/Protomaps reference. TENTATIVE: OSM XYZ (matches FR-DT-1 + zero new infra). Note OSM tile-usage-policy implication.
   - Authoring path: QGIS Desktop GUI vs PyQGIS script vs hand-edit XML. TENTATIVE: QGIS Desktop in `grace2` env, snapshot via `QgsProject.write()` for round-trip cleanliness.
   - QGIS Server image rebuild trigger after `styles/basemap.qml` commit: re-run `make qgis-server-build` here, or pass to job-0021 along with worker container build. TENTATIVE: rebuild here (one image-rebuild-per-styles-change discipline; pin trigger now before more presets land).

### File ownership (exclusive)

- `services/workers/pyqgis/sample_project/grace2-sample.qgs`
- `services/workers/pyqgis/sample_project/README.md`
- `services/workers/pyqgis/sample_project/` (directory creation)
- `styles/basemap.qml`
- May upload to `gs://grace-2-hazard-prod-qgs/grace2-sample.qgs` (write to GCS object, no bucket-IAM change)
- May trigger `make qgis-server-build && make qgis-server-push && make qgis-server-deploy` to bake the new `styles/basemap.qml` into the image (operational, not file-edit)

**FROZEN (do NOT edit):** `packages/contracts/**`, `services/agent/**`, `services/workers/pyqgis/worker.py` and `services/workers/pyqgis/types.py` (those are job-0020's), `services/workers/pyqgis/__init__.py` (job-0020), `web/**`, `tests/**`, `infra/**` (job-0018 and 0022 own infra changes this sprint), `docs/SRS_v0.3.md`, `public_hazard_catalog.yaml`.

### Cross-cutting principles in force
*Bundle small fixes; scan for all instances* — when this job touches a known class of issue (e.g., a missing label on a labeled resource), sweep the whole sprint scope for similar instances and surface in the report.

Cite by name from AGENTS.md § "Cross-cutting principles":
- **Pre-MVP scope — no legacy support.** No backward-compat shims, no "support both raster and XYZ" branches in the `.qgs` — pick one source and ship.
- **Remove don't shim.** No placeholder `<!-- TODO: real basemap -->` blocks in the QML or the `.qgs`.
- **Live E2E validation required.** Verbatim `curl` GetCapabilities + GetMap transcripts; attach the `/tmp/sample-getmap.png` artifact (non-blank verified by file size or `python -c "from PIL import Image; ..."`).
- **Diagnose before fix.** Tile-rendering failures: name the failing layer (QGIS Server image vs `.qgs` content vs QML vs `/vsigs/` access vs OSM upstream).
- **Surface uncertainty.** Every contestable choice → Open Question with TENTATIVE tag.
- **Don't edit in-flight kickoffs.** Frozen.
- **Engine: typed results only, narration metrics in fields.** Does not apply at this job (no envelope returned), but the discipline carries — this `.qgs` and QML are authored artifacts (FR-QS-5), not generated; both live in source control under engine ownership.
- **Engine: PyQGIS-worker is the only `.qgs` writer.** This job AUTHORS the canonical `.qgs` (one-time provenance from QGIS Desktop / PyQGIS scripting in the `grace2` env) and uploads it to GCS as the starting state. Subsequent mutations (job-0020) go through the worker. Document the authoring-vs-mutation distinction in `sample_project/README.md`.

### Acceptance criteria (reviewer re-runs)

- `services/workers/pyqgis/sample_project/grace2-sample.qgs` exists; in the `grace2` conda env, `python -c "from qgis.core import QgsProject; p = QgsProject(); assert p.read('services/workers/pyqgis/sample_project/grace2-sample.qgs'); assert len(p.mapLayers()) == 1; print(list(p.mapLayers().keys()))"` prints a list naming `osm-basemap` (or the layer ID resolving to that name).
- `styles/basemap.qml` exists and parses as valid QML (`xmllint --noout styles/basemap.qml` returns 0).
- The layer-name in `styles/basemap.qml`'s `<layerStyle>` (or equivalent) matches the layer name in the `.qgs`.
- `gcloud storage ls -L gs://grace-2-hazard-prod-qgs/grace2-sample.qgs` returns metadata with non-zero size; `md5Hash` matches local file.
- `curl -sf "<qgis-server-url>/ogc/?MAP=/vsigs/grace-2-hazard-prod-qgs/grace2-sample.qgs&SERVICE=WMS&REQUEST=GetCapabilities"` returns XML containing `<Name>osm-basemap</Name>` (or the layer name chosen).
- `curl -sf "<qgis-server-url>/ogc/?MAP=/vsigs/grace-2-hazard-prod-qgs/grace2-sample.qgs&SERVICE=WMS&REQUEST=GetMap&LAYERS=osm-basemap&BBOX=-125,24,-66,50&CRS=EPSG:4326&WIDTH=512&HEIGHT=256&FORMAT=image/png" -o /tmp/sample-getmap.png` produces a non-blank PNG (size > 1KB, pixel variance > 0 via `python -c "from PIL import Image; import statistics; im=Image.open('/tmp/sample-getmap.png').convert('L'); print(statistics.stdev(im.getdata()))"`).
- `services/workers/pyqgis/sample_project/README.md` documents authoring path + regeneration steps.
- All Open Questions surfaced with TENTATIVE tags + SRS references.

Surface contestable choices as Open Questions with TENTATIVE tags.

## Assessment

## Invariant Check

## Dependency Check

## Decisions Validated

## Open Questions Resolved

## Follow-up Actions

## Sign-off
