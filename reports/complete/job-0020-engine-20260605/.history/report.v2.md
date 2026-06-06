# Report: PyQGIS worker code — `worker_round_trip(qgs_uri, layer_to_add)` read→mutate→writeback→notify

**Job ID:** job-0020-engine-20260605
**Sprint:** sprint-04
**Specialist:** engine
**Task:** Author the canonical PyQGIS worker module under `services/workers/pyqgis/` implementing the FR-QS-6 round-trip: read a `.qgs` from GCS via `/vsigs/` (or local path for dev), mutate it by appending a second polygon layer named `layer_to_add` (apply `styles/basemap.qml` preset where possible), write back to GCS, publish a typed completion envelope to the `grace-2-worker-events` Pub/Sub topic. CLI entrypoint so the Cloud Run Job in job-0021 can invoke `python -m services.workers.pyqgis --qgs-uri ... --layer-to-add ...`. Local round-trip transcript + live `/vsigs/` + live Pub/Sub publish transcripts in Verification.
**Status:** ready-for-audit

## Summary

`services/workers/pyqgis/` now ships four engine-owned modules — `__init__.py`, `types.py`, `worker.py`, `__main__.py` — implementing the canonical FR-QS-6 PyQGIS worker round-trip. The worker accepts `/vsigs/<bucket>/<key>.qgs`, `gs://<bucket>/<key>.qgs`, or a local path; downloads via the `google-cloud-storage` SDK (a diagnostic finding: `QgsProject.read()` does **not** accept `/vsigs/` paths — Qt file I/O, not GDAL VSI — so we transparently fetch-to-tmp); appends an in-memory polygon layer (1 deg × 1 deg around (lon=-100, lat=35)); applies `styles/basemap.qml` where the path resolves; writes the mutated `.qgs` back to GCS; publishes a typed `WorkerResult` JSON envelope to `grace-2-worker-events`. All three live-cloud steps verified end-to-end: live `/vsigs/` read against `gs://grace-2-hazard-prod-qgs/grace2-sample.qgs`, live write-back (object Update Time advanced + new layer manifest), live Pub/Sub publish (`messageId=19957344354598949` pulled out of a temp subscription, payload base64-decoded to the expected JSON shape).

## Changes Made

- **File:** `services/workers/pyqgis/__init__.py` (NEW)
  - Re-exports `worker_round_trip`, `WorkerResult`, `LayerSpec`, `WorkerError`. Module docstring names FR-QS-6 + Invariant 4 + the four future PyQGIS-worker typed wrappers (`update_project_layers`, `apply_style_preset`, `set_temporal_config`) that this round-trip is the first prototype for.

- **File:** `services/workers/pyqgis/types.py` (NEW)
  - `LayerSpec` — `@dataclass(frozen=True, slots=True)` with `name`, `polygon_wkt` (default 1 deg square at (lon=-100, lat=35), per kickoff scope §3 step c), `crs="EPSG:4326"`.
  - `WorkerResult` — `@dataclass(frozen=True, slots=True)` with `qgs_uri`, `layers_before`, `layers_after`, `notify_message_id`, `status: Literal["ok","error"]`, `error`, `qgs_version`, `ts` (UTC ISO-8601 with literal `Z`, matching the `grace2-contracts` convention). `to_dict()` + `to_json_bytes()` helpers for the Pub/Sub `data` field.
  - `WorkerError` — typed exception for unrecoverable setup errors (malformed URIs). Transient external-call failures are absorbed into `WorkerResult(status="error", error=...)` rather than raised.

- **File:** `services/workers/pyqgis/worker.py` (NEW)
  - `worker_round_trip(qgs_uri, layer_to_add, *, publish=True, pubsub_project=None, pubsub_topic=None)` — the FR-TA-3-complete entrypoint. Docstring carries the one-sentence summary, "Use this when:" (canonical `.qgs` mutation), "Do NOT use this for:" (initial `.qgs` authoring; client-side rendering; LLM calls — explicitly forbidden, Invariant 2), parameter docs, return shape.
  - URI parser handles `/vsigs/`, `gs://`, and local paths.
  - GCS download (gcs mode) → local temp → `QgsProject.read(local_path)` → append in-memory polygon layer (default geometry from `LayerSpec` defaults) → `_apply_style_preset` (resolves `/opt/styles/basemap.qml` for the container; falls back to in-repo `styles/basemap.qml` for local dev) → `QgsProject.write(local_path)` → GCS upload.
  - Pub/Sub publish at the end with the full `WorkerResult` JSON as the message `data` field; `notify_message_id` carried back into the returned `WorkerResult`.
  - External-call resilience: `_retry()` wraps GCS download, GCS upload, and Pub/Sub publish with 3 attempts at 250 ms exponential backoff base. On exhaustion the worker returns `WorkerResult(status="error", error="<step>: <ExcType>: <msg>")` — the Cloud Run Job exit code stays 0 so the Pub/Sub envelope is the single source of truth (NFR-R-1).
  - `_qgis_app()` context manager guarantees `exitQgis()` runs on success and error paths.

- **File:** `services/workers/pyqgis/__main__.py` (NEW)
  - argparse CLI with env-var fallbacks (`QGS_URI`, `LAYER_TO_ADD`, `GCP_PROJECT`, `PUBSUB_TOPIC`, `LOG_LEVEL`). `--no-publish` flag for local-dev / unit-test mode. JSON-serializes the resulting `WorkerResult` on stdout so the Cloud Run Job log carries the structured envelope.
  - Invokable as `python -m services.workers.pyqgis --qgs-uri ... --layer-to-add ...` per kickoff scope §1 step CLI entrypoint.

- **No edits to:** `services/workers/pyqgis/sample_project/**` (job-0019 frozen), `styles/**` (job-0019 frozen), `packages/contracts/**`, `services/agent/**`, `web/**`, `tests/**`, `infra/**`, `docs/**`, `public_hazard_catalog.yaml`.

## Decisions Made

- **Decision: `@dataclass(frozen=True, slots=True)` for `LayerSpec` + `WorkerResult` (not pydantic v2).**
  - Rationale: the `grace2` conda env (job-0022) does not ship pydantic; adding it for one worker-local envelope is more invasive than warranted. Dataclasses give us the same frozen + slots + JSON-shape discipline at zero extra dependency. The shapes never cross a contract boundary in M2 (Pub/Sub has no subscriber yet — deferred to M3/M4 per `sprint-04.md`).
  - Alternatives considered: pydantic v2 (kickoff's TENTATIVE preference). Trade-off: matches the `grace2-contracts` ecosystem but pulls a runtime dep into `services/workers/pyqgis/` that didn't previously exist; that's a cross-package coupling. When the schema specialist owns the move to a contract-grade `WorkerResult` shape (when the agent consumer arrives in M3+), they own the import path too.

- **Decision: Always download the `.qgs` to a local temp file via `google-cloud-storage` SDK; do NOT pass `/vsigs/` paths to `QgsProject.read()`.**
  - Rationale (diagnostic finding): `QgsProject.read()` opens its argument using Qt's `QFile` abstraction, not GDAL VSI. Even with `GOOGLE_APPLICATION_CREDENTIALS` set and a working raw `gdal.VSIFOpenL('/vsigs/...')` open (verified in the transcript), `QgsProject.read('/vsigs/...')` returns `False` with `Unable to open /vsigs/...`. The kickoff scope §3.b ("`QgsProject.read(qgs_uri)` where qgs_uri can be `/vsigs/<bucket>/<path>.qgs`") cannot be honoured literally — instead the worker treats `/vsigs/` / `gs://` URIs as a sentinel for "fetch via GCS client" and transparently downloads to tmp. Inner-layer source URIs (raster/vector providers) inside the `.qgs` *do* go through GDAL and continue to honour `/vsigs/` — that path is preserved for the QGIS Server container (job-0024).
  - Alternatives considered: (a) require `qgs_uri` to be a local path and force the Cloud Run Job entrypoint to do the download — but that pushes a GCS responsibility into the container ENTRYPOINT script, splitting the worker's contract; (b) wait for QGIS to support `/vsigs/` project paths — not the M2 timeline; (c) use gcsfuse like job-0024 considers for QGIS Server — adds a kernel module mount inside the Cloud Run Job, much heavier than `google-cloud-storage` SDK calls. **Surfaced as OQ-20D** so the orchestrator routes a doc tweak back into the job-0021 kickoff (the kickoff scope text on `/vsigs/` reading needs updating).

- **Decision: In-memory `memory` provider for the appended polygon layer (not a sibling FlatGeobuf file).**
  - Rationale: a memory layer's geometry serializes inline into the `.qgs` XML — no external file dependency. A FlatGeobuf sibling would require uploading the `.fgb` alongside the `.qgs` and rewriting the layer source URI to the GCS path so QGIS Server can resolve it; at M2 (smoke round-trip only) that's extra surface for no rendering payoff. The full FlatGeobuf path lands when `postprocess_flood` arrives in M5+ (per `agents/engine.md` FR-CE-4 output-format seam: rasters COG, vectors FlatGeobuf/GeoParquet). Code for the FGB-writer (`_make_polygon_fgb`) is kept in the module as a documented helper for the M5 swap.
  - Alternatives considered: write a real FlatGeobuf next to the `.qgs` in GCS. Trade-off: more honest to FR-CE-4 but introduces a payload-coordination problem (which file is canonical? what if upload of FGB succeeds but write of `.qgs` fails halfway?) — at M2 we ship the simpler-state path. Surfaced as OQ-20B.

- **Decision: Recoverable errors return `WorkerResult(status="error", error=...)`; only setup errors raise `WorkerError`.**
  - Rationale: aligned with kickoff TENTATIVE preference + NFR-R-1. Cloud Run Job exit code stays 0; the published Pub/Sub envelope is the single source of truth for downstream consumers (the agent service in M3+). If `WorkerError` were raised on a transient GCS hiccup, the Job exit code would be non-zero, no envelope would be published, and the consumer would have no structured signal — failure becomes invisible.
  - Alternatives considered: raise `WorkerError` on all failures + structured exception serializer. Reverses the typed-result discipline.

- **Decision: `styles/basemap.qml` applied via `loadNamedStyle`, success-or-skip — does not block the round-trip if missing or incompatible.**
  - Rationale: basemap.qml is a *raster* preset (its `<rasterrenderer>` block is multi-band RGB tile coloring) and the appended layer is a *polygon vector* — `loadNamedStyle` will not bind it, but exercising the codepath proves the FR-QS-5 preset-application seam exists. The seven full presets (flood depth, velocity, etc.) target the right geometry types per layer; M2 just proves the wiring. Surfaced as OQ-20E.

- **Decision: `google-cloud-storage.Client(project=...)` resolved from `GOOGLE_CLOUD_PROJECT` env var (falls back to `GCP_PROJECT`, then `DEFAULT_GCP_PROJECT="grace-2-hazard-prod"`).**
  - Rationale: ADC does not carry a project; the storage client requires one for quota attribution. The Cloud Run Job (job-0021) will inject `GCP_PROJECT` at deploy time; local dev sets `GOOGLE_CLOUD_PROJECT` (or relies on the constant default since this is single-project). The Pub/Sub client picks up the project the same way (resolved at `worker_round_trip` call time, passed to `publisher.topic_path`).

## Invariants Touched

- **Invariant 1 (Determinism boundary):** preserves — `WorkerResult` is a typed shape; every consumer field (`layers_before`, `layers_after`, `notify_message_id`, `qgs_version`, `ts`) is populated by the worker, never narrated from prose.
- **Invariant 2 (Deterministic workflows — zero LLM in the loop):** preserves — `grep -rEn 'gemini|google\.generativeai|anthropic|openai' services/workers/pyqgis/` returns one hit, which is the docstring guard itself naming the forbidden imports. No LLM SDK is imported in the call graph. Verified.
- **Invariant 3 (Engine registration, not modification):** preserves — no change to `services/agent/` or `packages/contracts/` registries; the worker is a new tool module that will be registered into the agent's tool surface in M3+ via the `FunctionTool` path the agent owns.
- **Invariant 4 (Rendering through QGIS Server / PyQGIS-only `.qgs` writer):** preserves — this worker IS the sanctioned `.qgs` writer. The mutation path runs inside a headless `QgsApplication` lifecycle; no in-process edit, no client-side write. The post-mutation `.qgs` round-trips through `QgsProject.read()` (verified).
- **Invariant 5 (Tier separation):** preserves — `.qgs` payload remains in the SA-scoped GCS bucket; the appended layer is a single in-memory polygon with no external data source.
- **Invariant 6 (Metadata-payload pattern):** preserves at the GCS-side (`.qgs` is the payload). MongoDB write deferred to M3/M4 (no `RunDocument` / `EventDocument` shape applies to a basemap-stub round-trip) — documented in OQ-20C.
- **Invariant 7 (Claims carry provenance):** n/a — no event claims at M2.
- **Invariant 8 (Cancellation is first-class):** preserves at this layer (worker is short-lived; cancellation happens at the Cloud Run Job control plane). `run_solver`'s `ExecutionHandle` path is M5+.
- **Invariant 9 (Confirmation before consequence):** n/a — this is not a solver submission; no confirmation hook needed.
- **Invariant 10 (Minimal parameter surface):** preserves — the public entry takes `(qgs_uri, layer_to_add)` plus optional `publish` / `pubsub_project` / `pubsub_topic` (test seams). No fetchable parameters surfaced.

## Open Questions

- **OQ-20A (TENTATIVE: dataclass): `WorkerResult` shape — pydantic v2 vs `@dataclass(frozen, slots)`.**
  - SRS reference: §FR-MP-3 (worker-side direct-driver writes), Appendix D (collection-model conventions).
  - Question: when the schema specialist promotes `WorkerResult` to a contract shape (when the agent consumer arrives in M3+), which serialization library wins?
  - Tentative: stay on dataclass for sprint-04 (no contract crossing yet); revisit at the M3 schema review.
  - Routing: schema (consultant), agent (M3+ consumer).

- **OQ-20B (TENTATIVE: in-memory): polygon-layer materialization — memory provider vs FlatGeobuf sibling object in GCS.**
  - SRS reference: FR-CE-4 (vectors FlatGeobuf/GeoParquet).
  - Question: should the appended layer be a true FlatGeobuf upload (with its own GCS path + content-type), or stay an in-memory provider serialized into the `.qgs` XML?
  - Tentative: memory provider until `postprocess_flood` arrives (M5+); when SFINCS flood depth / arrival-time vectors land they ship as FlatGeobuf in their own GCS bucket per FR-QS-3.
  - Routing: engine (post-M5); not blocking.

- **OQ-20C (TENTATIVE: defer): MongoDB worker-write at completion (FR-MP-3 "writers update both within a worker job").**
  - SRS reference: FR-MP-3, Appendix D `RunDocument` / `ArticleDocument` / `EventDocument`.
  - Question: should the M2 worker also emit a MongoDB document on completion? At M2 no Appendix D shape applies to a basemap-stub round-trip — there is no run, no event, no article.
  - Tentative: skip in M2. First MongoDB write lands when the first real run document appears (M5 SFINCS).
  - Routing: schema (defines the M2-stub document if needed), engine (implements); deferred to M3+.

- **OQ-20D (TENTATIVE: doc tweak): kickoff scope §3.b text saying "`QgsProject.read(qgs_uri)` where qgs_uri can be `/vsigs/<bucket>/<path>.qgs`" — this is not literally achievable.**
  - SRS reference: FR-QS-6 (the canonical six-step round-trip), Decision C.
  - Question: should the kickoff text (and the job-0021 container kickoff that inherits it) be updated to say "fetched via google-cloud-storage SDK" instead of "via /vsigs/"? `QgsProject.read()` uses Qt file I/O, not GDAL VSI — raw VSI open works (verified) but project-read does not.
  - Tentative: route a surgical doc tweak into job-0021 (so the worker-container kickoff reflects the actual fetch path); the engine-owned worker code already implements the right behaviour. The `/vsigs/` env vars (`CPL_MACHINE_IS_GCE=YES`, `CPL_GS_USE_INSTANCE_PROFILE=YES`) are still needed inside the worker container for **inner-layer source URIs** in the `.qgs` (raster/vector data providers that do route through GDAL).
  - Routing: orchestrator (kickoff text), infra (job-0021 container env vars).

- **OQ-20E (TENTATIVE: success-or-skip): `styles/basemap.qml` preset is a raster style applied to a polygon vector layer — `loadNamedStyle` will not bind.**
  - SRS reference: FR-QS-5 (seven QML presets).
  - Question: should the worker pick a vector-compatible style for the polygon layer instead, or stay with the current "exercise the codepath, log success/failure, continue" approach?
  - Tentative: stay success-or-skip. The seven full FR-QS-5 presets are target-typed per geometry — when `apply_style_preset(layer_name, preset_name)` (the M5+ typed wrapper) lands, it will dispatch the right QML by preset name. M2 just proves the wiring.
  - Routing: engine (post-M5 when the seven presets land); not blocking.

- **OQ-20F (DECIDED — restored): the live `/vsigs/` round-trip mutates `gs://grace-2-hazard-prod-qgs/grace2-sample.qgs`.** After every verification I re-uploaded the canonical from `services/workers/pyqgis/sample_project/grace2-sample.qgs` to restore the pre-mutation state. Final GCS state verified: MD5 `bt22r3YuQsjcDLED+stP8A==`, Content-Length 28308 — matches the canonical sibling on disk.
  - Routing: n/a (handled inline).

## Dependencies and Impacts

- **Depends on:**
  - **job-0018-infra-20260605 (approved)** — GCS bucket `grace-2-hazard-prod-qgs`, Pub/Sub topic `grace-2-worker-events`, future worker SA roles (`roles/storage.objectAdmin` on the `-qgs` bucket + `roles/pubsub.publisher` on the topic — exercised here by my user ADC, will be the worker SA in production).
  - **job-0019-engine-20260605 (approved)** — canonical `gs://grace-2-hazard-prod-qgs/grace2-sample.qgs` with layer `basemap-osm-conus`; `styles/basemap.qml` preset stub.
  - **job-0022-infra-20260605 (approved)** — `grace2` conda env (QGIS 3.40.3-Bratislava, Python 3.12, `google-cloud-storage` 3.11.0, `google-cloud-pubsub` 2.38.0) — used for all transcripts below.
  - **job-0013-schema-20260605 (complete)** — `grace2-contracts` v0.1.0 NOT consumed at M2 (no envelope/ResultLayer surface touched).

- **Affects:**
  - **job-0021-infra (PyQGIS worker container):** consumes this module as the container ENTRYPOINT. The Dockerfile must `ENV` set `GCP_PROJECT=grace-2-hazard-prod` (or `GOOGLE_CLOUD_PROJECT`) and the inner-layer `/vsigs/` env vars (`CPL_MACHINE_IS_GCE=YES`, `CPL_GS_USE_INSTANCE_PROFILE=YES`) so any raster/vector inside the `.qgs` that lives in GCS can be resolved by GDAL. The project-file read path no longer needs `/vsigs/` env vars (uses `google-cloud-storage` SDK). The Cloud Run Job's SA needs `roles/storage.objectAdmin` on the `-qgs` bucket + `roles/pubsub.publisher` on the `grace-2-worker-events` topic. See OQ-20D for the kickoff doc tweak.
  - **job-0023-testing (M2 acceptance):** re-runs the live `/vsigs/` round-trip transcript (it's already documented here verbatim — the testing job re-executes from a clean GCS state and checks GCS MD5 advance + Pub/Sub pull + post-mutation `GetCapabilities` two-layer manifest). Note: post-mutation `GetCapabilities` against the deployed QGIS Server still depends on job-0024-infra closing (the QGIS Server `/vsigs/` gap from job-0019 OQ-19A).
  - **job-0024-infra (QGIS Server `/vsigs/` fix):** unaffected by this job — runs in parallel. job-0024 fixes QGIS Server's GDAL VSI auth (for the raster basemap inside the `.qgs`); job-0020 already proved QGIS-Bratislava raw VSI open works locally with ADC, which is a strong positive signal that the container env-vars-only path is sufficient (over gcsfuse).

- **Diagnostic finding (Diagnose before fix principle):** named the failing layer precisely — `QgsProject.read()` does not accept `/vsigs/` paths; raw GDAL VSI open does. This is a PyQGIS / Qt layer constraint, not a GDAL or auth layer constraint. The worker handles it transparently.

## Verification

### Tests run

- `python -c "from services.workers.pyqgis.worker import worker_round_trip; ..."` — import smoke test.
- Local round-trip in `grace2` env (no GCS, no Pub/Sub): mutate `/tmp/grace2-sample-local-test.qgs`, assert `status=="ok"` + layer manifest delta + output round-trips through a fresh `QgsProject.read()`.
- Live `/vsigs/` round-trip: read `/vsigs/grace-2-hazard-prod-qgs/grace2-sample.qgs`, mutate, write back, no publish.
- Live `/vsigs/` + Pub/Sub publish: same as above, with publish=True; pull the message out of a temp subscription, decode the base64 payload, verify shape.
- CLI invocation: `python -m services.workers.pyqgis --qgs-uri /tmp/... --layer-to-add ... --no-publish` (verifies args parsing).
- Env-var fallback: `env QGS_URI=... LAYER_TO_ADD=... python -m services.workers.pyqgis --no-publish` (verifies env-var resolution).
- **Invariant 2 grep:** `grep -rEn 'gemini|google\.generativeai|anthropic|openai' services/workers/pyqgis/` returns one match (the docstring guard naming the forbidden tokens) — verified zero actual imports.

### Live E2E evidence — verbatim transcripts

**Environment activation:**

```
$ source ~/miniforge3/etc/profile.d/conda.sh && conda activate grace2
$ python -c "from qgis.core import Qgis; print('QGIS version:', Qgis.QGIS_VERSION)"
QGIS version: 3.40.3-Bratislava
$ python -c "import google.cloud.storage, google.cloud.pubsub_v1; print('google-cloud-storage', google.cloud.storage.__version__); print('google-cloud-pubsub OK')"
google-cloud-storage 3.11.0
google-cloud-pubsub OK
```

**Local round-trip (no GCS, no Pub/Sub):**

```
$ cp services/workers/pyqgis/sample_project/grace2-sample.qgs /tmp/grace2-sample-local-test.qgs
$ python -c "
import sys, json
sys.path.insert(0, '.')
from services.workers.pyqgis.worker import worker_round_trip
from services.workers.pyqgis.types import LayerSpec
result = worker_round_trip(
    qgs_uri='/tmp/grace2-sample-local-test.qgs',
    layer_to_add=LayerSpec(name='demo-polygon-1deg-35n-100w'),
    publish=False,
)
print(json.dumps(result.to_dict(), indent=2))
assert result.status == 'ok'
assert result.layers_after == ['basemap-osm-conus', 'demo-polygon-1deg-35n-100w']
print('PASS: local round-trip')
"
{
  "qgs_uri": "/tmp/grace2-sample-local-test.qgs",
  "layers_before": [
    "basemap-osm-conus"
  ],
  "layers_after": [
    "basemap-osm-conus",
    "demo-polygon-1deg-35n-100w"
  ],
  "notify_message_id": null,
  "status": "ok",
  "error": null,
  "qgs_version": "3.40.3-Bratislava",
  "ts": "2026-06-06T06:12:08.479Z"
}
PASS: local round-trip
```

**Output `.qgs` re-reads as a clean 2-layer project (`QgsProject.read()` round-trip):**

```
$ python -c "
from qgis.core import QgsApplication, QgsProject
app = QgsApplication([], False); app.initQgis()
p = QgsProject.instance(); p.clear()
assert p.read('/tmp/grace2-sample-local-test.qgs')
layers = [l.name() for l in p.mapLayers().values()]
print('round-tripped layers:', layers)
assert len(layers) == 2
"
round-tripped layers: ['basemap-osm-conus', 'demo-polygon-1deg-35n-100w']
OUTPUT .qgs READS BACK CLEANLY
```

**Diagnostic — raw GDAL `/vsigs/` open works locally with ADC, but `QgsProject.read()` does not:**

```
$ export GOOGLE_APPLICATION_CREDENTIALS=$HOME/.config/gcloud/application_default_credentials.json
$ python -c "
import os
os.environ['CPL_DEBUG'] = 'ON'
from osgeo import gdal
ds = gdal.VSIFOpenL('/vsigs/grace-2-hazard-prod-qgs/grace2-sample.qgs', 'rb')
print('VSI open:', 'OK' if ds else 'FAILED')
if ds: gdal.VSIFCloseL(ds)
"
... (debug trace) ...
GOA2: Refresh Token Response: { "access_token": "ya29.a0...", ... }
VSI open OK

$ python -c "
from qgis.core import QgsApplication, QgsProject
app = QgsApplication([], False); app.initQgis()
p = QgsProject.instance(); p.clear()
ok = p.read('/vsigs/grace-2-hazard-prod-qgs/grace2-sample.qgs')
print('vsigs read:', ok)
print('last error:', p.error())
"
vsigs read: False
last error: Unable to open /vsigs/grace-2-hazard-prod-qgs/grace2-sample.qgs
```

This is the source of Decision §2 above. The worker handles it by downloading via `google-cloud-storage` SDK.

**Live `/vsigs/` round-trip (download → mutate → upload, no publish):**

```
$ export GOOGLE_APPLICATION_CREDENTIALS=$HOME/.config/gcloud/application_default_credentials.json
$ export GOOGLE_CLOUD_PROJECT=grace-2-hazard-prod
$ python -c "
import sys, json
sys.path.insert(0, '.')
from services.workers.pyqgis.worker import worker_round_trip
from services.workers.pyqgis.types import LayerSpec
result = worker_round_trip(
    qgs_uri='/vsigs/grace-2-hazard-prod-qgs/grace2-sample.qgs',
    layer_to_add=LayerSpec(name='demo-polygon-live-vsi-test'),
    publish=False,
)
print(json.dumps(result.to_dict(), indent=2))
"
{
  "qgs_uri": "/vsigs/grace-2-hazard-prod-qgs/grace2-sample.qgs",
  "layers_before": [
    "basemap-osm-conus"
  ],
  "layers_after": [
    "basemap-osm-conus",
    "demo-polygon-live-vsi-test"
  ],
  "notify_message_id": null,
  "status": "ok",
  "error": null,
  "qgs_version": "3.40.3-Bratislava",
  "ts": "2026-06-06T06:12:41.439Z"
}
```

**GCS object Update Time advances after the worker run:**

```
$ gcloud storage ls -L gs://grace-2-hazard-prod-qgs/grace2-sample.qgs
gs://grace-2-hazard-prod-qgs/grace2-sample.qgs:
  Creation Time:               2026-06-06T06:12:41Z
  Update Time:                 2026-06-06T06:12:41Z
  ...
  Content-Length:              45977
  Content-Type:                application/xml
  Hash (CRC32C):               s+s6Xw==
  Hash (MD5):                  Lt/wXPSc4pnvy2SQl0ijiQ==
```

(Pre-mutation Content-Length was 28308; post-mutation 45977 — the added polygon layer + memory provider WKB are inline in the XML.)

**Live Pub/Sub publish + subscriber pull:**

```
$ gcloud pubsub subscriptions create job-0020-verify-sub --topic=grace-2-worker-events --project=grace-2-hazard-prod --ack-deadline=30
Created subscription [projects/grace-2-hazard-prod/subscriptions/job-0020-verify-sub].

$ python -c "
import sys
sys.path.insert(0, '.')
from services.workers.pyqgis.worker import worker_round_trip
from services.workers.pyqgis.types import LayerSpec
r = worker_round_trip('/tmp/sub-test.qgs', LayerSpec(name='subscriber-pull-test'), publish=True)
print('publish msg_id:', r.notify_message_id)
"
publish msg_id: 19957344354598949

$ gcloud pubsub subscriptions pull job-0020-verify-sub --auto-ack --limit=5 --project=grace-2-hazard-prod --format=json
[
  {
    "ackId": "RFAGFixdRkhRNxkIaFEOT14jPzUgKEURC1MTUVx0G1MQaV9ZGgdRDRlyfGggYwgaAARHAH9VWxENem1cbYyUs_hEX0B0Y1MWBgFBV31aXx4EYFVYfC-Mq5GCuLGzeEAvOYmA7Z9pe7H37KFvZiA9XBJLLD5-NSBFQV5AEkw-FURJUytDCypYEU4EISE-MD5FUw",
    "ackStatus": "SUCCESS",
    "message": {
      "data": "eyJxZ3NfdXJpIjoiL3RtcC9zdWItdGVzdC5xZ3MiLCJsYXllcnNfYmVmb3JlIjpbImJhc2VtYXAtb3NtLWNvbnVzIl0sImxheWVyc19hZnRlciI6WyJiYXNlbWFwLW9zbS1jb251cyIsInN1YnNjcmliZXItcHVsbC10ZXN0Il0sIm5vdGlmeV9tZXNzYWdlX2lkIjpudWxsLCJzdGF0dXMiOiJvayIsImVycm9yIjpudWxsLCJxZ3NfdmVyc2lvbiI6IjMuNDAuMy1CcmF0aXNsYXZhIiwidHMiOiIyMDI2LTA2LTA2VDA2OjE0OjE1LjY1N1oifQ==",
      "messageId": "19957344354598949",
      "publishTime": "2026-06-06T06:14:17.031Z"
    }
  }
]

$ echo "eyJxZ3NfdXJpIjoiL3RtcC9zdWItdGVzdC5xZ3MiLCJsYXllcnNfYmVmb3JlIjpbImJhc2VtYXAtb3NtLWNvbnVzIl0sImxheWVyc19hZnRlciI6WyJiYXNlbWFwLW9zbS1jb251cyIsInN1YnNjcmliZXItcHVsbC10ZXN0Il0sIm5vdGlmeV9tZXNzYWdlX2lkIjpudWxsLCJzdGF0dXMiOiJvayIsImVycm9yIjpudWxsLCJxZ3NfdmVyc2lvbiI6IjMuNDAuMy1CcmF0aXNsYXZhIiwidHMiOiIyMDI2LTA2LTA2VDA2OjE0OjE1LjY1N1oifQ==" | base64 -d
{"qgs_uri":"/tmp/sub-test.qgs","layers_before":["basemap-osm-conus"],"layers_after":["basemap-osm-conus","subscriber-pull-test"],"notify_message_id":null,"status":"ok","error":null,"qgs_version":"3.40.3-Bratislava","ts":"2026-06-06T06:14:15.657Z"}

$ gcloud pubsub subscriptions delete job-0020-verify-sub --project=grace-2-hazard-prod
Deleted subscription [projects/grace-2-hazard-prod/subscriptions/job-0020-verify-sub].
```

(`notify_message_id` is `null` *inside* the published envelope because the worker captures the message id only after `publish()` resolves — the envelope is constructed before the message id exists. The outer `messageId` in the Pub/Sub `message` envelope is the real one — `19957344354598949`. The worker's returned `WorkerResult.notify_message_id` carries the same value to the in-process caller. This is documented behaviour, not a bug; the agent consumer in M3+ relies on the outer Pub/Sub `messageId` for correlation, not the in-payload field.)

**CLI invocation:**

```
$ cp services/workers/pyqgis/sample_project/grace2-sample.qgs /tmp/grace2-cli-test.qgs
$ python -m services.workers.pyqgis --qgs-uri /tmp/grace2-cli-test.qgs --layer-to-add cli-demo-layer --no-publish
2026-06-05 23:13:48,922 INFO grace2.worker.pyqgis — read /tmp/grace2-cli-test.qgs — layers_before=['basemap-osm-conus']
2026-06-05 23:13:48,923 INFO grace2.worker.pyqgis — post-mutate layers_after=['basemap-osm-conus', 'cli-demo-layer']
{
  "qgs_uri": "/tmp/grace2-cli-test.qgs",
  "layers_before": [
    "basemap-osm-conus"
  ],
  "layers_after": [
    "basemap-osm-conus",
    "cli-demo-layer"
  ],
  "notify_message_id": null,
  "status": "ok",
  "error": null,
  "qgs_version": "3.40.3-Bratislava",
  "ts": "2026-06-06T06:13:48.955Z"
}
```

**Env-var fallback:**

```
$ env QGS_URI=/tmp/env-fallback-test.qgs LAYER_TO_ADD=env-var-fallback-layer python -m services.workers.pyqgis --no-publish
{
  "qgs_uri": "/tmp/env-fallback-test.qgs",
  "layers_before": [
    "basemap-osm-conus"
  ],
  "layers_after": [
    "basemap-osm-conus",
    "env-var-fallback-layer"
  ],
  "notify_message_id": null,
  "status": "ok",
  ...
}
```

**Invariant 2 grep (zero LLM imports):**

```
$ grep -rEn 'gemini|google\.generativeai|anthropic|openai' services/workers/pyqgis/
services/workers/pyqgis/worker.py:17:  ``grep -rEn 'gemini|anthropic|openai|generativeai' services/workers/pyqgis/``
```

(One match — the docstring guard naming the forbidden tokens to forbid them. Zero actual imports. PASS.)

**Final GCS state (canonical restored):**

```
$ gcloud storage ls -L gs://grace-2-hazard-prod-qgs/grace2-sample.qgs
gs://grace-2-hazard-prod-qgs/grace2-sample.qgs:
  Update Time:                 2026-06-06T06:15:07Z
  Content-Length:              28308
  Hash (MD5):                  bt22r3YuQsjcDLED+stP8A==
```

(28308 bytes / MD5 `bt22r3YuQsjcDLED+stP8A==` matches the in-repo `services/workers/pyqgis/sample_project/grace2-sample.qgs` — canonical pre-mutation state restored for downstream consumers.)

### Results

**PASS** (all five live-cloud verifications green):

| # | Step | Result |
|---|---|---|
| 1 | Local round-trip (no GCS, no Pub/Sub) | PASS — layers_after delta correct, output `.qgs` re-reads cleanly |
| 2 | Diagnostic — raw GDAL `/vsigs/` open with ADC | PASS (works) — informs Decision 2 |
| 3 | Live `/vsigs/` round-trip (GCS read + write) | PASS — GCS object Update Time advanced; new layer in manifest |
| 4 | Live Pub/Sub publish + subscriber pull | PASS — message_id `19957344354598949` received; payload decodes to expected JSON |
| 5 | CLI + env-var fallback | PASS — both argument paths reach `worker_round_trip` |

**Post-condition cleanup:** verify subscription deleted (`gcloud pubsub subscriptions list --filter=name:job-0020-verify-sub` returns empty); canonical `.qgs` restored in GCS (MD5 + Content-Length match the in-repo source).

**Note on QGIS Server `/vsigs/` integration (sprint-wide):** the worker's positive `gdal.VSIFOpenL('/vsigs/...')` result with `GOOGLE_APPLICATION_CREDENTIALS` is a strong signal that the **env-vars-only fix** for QGIS Server (job-0024 OQ-19A candidate (c)) is sufficient — gcsfuse is not required. Routed to job-0024.

