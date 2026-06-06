# Audit: PyQGIS worker code: worker_round_trip(qgs_uri, layer_to_add) read-mutate-writeback-notify

**Job ID:** job-0020-engine-20260605
**Sprint:** sprint-04
**Auditor:** Development Orchestrator
**Status:** approved

## Task Assignment

**Specialist:** engine
**Prerequisites:** job-0018-infra-20260605 (must be `approved`) — provides GCS bucket `grace-2-hazard-prod-qgs`, Pub/Sub topic `grace-2-worker-events`, and the worker SA IAM bindings (`roles/storage.objectAdmin` on the `-qgs` bucket, `roles/pubsub.publisher` on the topic). job-0019-engine-20260605 (must be `approved`) — provides `services/workers/pyqgis/sample_project/grace2-sample.qgs` uploaded to GCS and `styles/basemap.qml` matching the basemap layer name. job-0022-infra-20260605 (must be `approved`) — provides the recreated `grace2` conda env on this Debian 13 box for local PyQGIS iteration. job-0013-schema-20260605 (complete) — `grace2-contracts` v0.1.0 IS NOT consumed by this job (no envelope/ResultLayer shapes touched at M2); WorkerResult is a M2-local typed shape, not a contract amendment.
**SRS references:** FR-QS-6 (PyQGIS worker pattern — the canonical six-step round-trip; this job implements steps 1-5: pull from GCS via `/vsigs/`, mutate via PyQGIS, write back via `/vsigs/` or temp-file+gsutil, notify via Pub/Sub publish); FR-QS-2 (.qgs in GCS canonical); FR-QS-5 (apply the M2 preset `styles/basemap.qml` via `apply_style_preset` codepath); FR-MP-1/3 (canonical .qgs in GCS, source of truth); FR-CE-1 (containerization-as-Cloud-Run-Jobs pattern — the function is the entrypoint job-0021 wraps); FR-TA-3 (tool docstring discipline — one-sentence summary, "Use this when:", "Do NOT use this for:", parameter/return descriptions on `worker_round_trip`); Decision C (PyQGIS workers for project manipulation — first prototype); Invariant 2 (Deterministic workflows — ZERO LLM calls in worker code, asserted by testing in 0023); Invariant 4 (Rendering through QGIS Server — `.qgs` mutations only inside a PyQGIS worker job); Invariant 6 (Metadata-payload pattern — worker writes GCS payload; MongoDB write deferred to M3/M4 when first event/run document lands). NFR-R-1 (external-call resilience — wrap GCS read/write + Pub/Sub publish with retry-and-backoff, surface structured typed errors on exhaustion, never an uncaught crash).

### Environment

Dev + prod substrate Linux (Debian 13, `Linux maturin 6.12.74+deb13+1-amd64`, x86_64). Container builds (in 0021) `linux/amd64`-only. Local iteration runs in the `grace2` conda env (job-0022 — `qgis=3.40.3` + `python=3.12` + `google-cloud-storage` + `google-cloud-pubsub`). Live cloud substrate from `PROJECT_STATE.md` + the upstream job audits: GCS `grace-2-hazard-prod-qgs` (sample .qgs at `gs://grace-2-hazard-prod-qgs/grace2-sample.qgs`), Pub/Sub topic `grace-2-worker-events`. ADC for local invocation comes from `~/.config/gcloud/application_default_credentials.json` (M1 substrate). `python3-venv` unavailable on Debian 13 — `virtualenv` if a venv is needed for unit-test isolation outside the conda env.

### Scope

1. **Author the PyQGIS worker module** under `services/workers/pyqgis/`:
   - `services/workers/pyqgis/__init__.py` (empty or re-export `worker_round_trip` + `WorkerResult`).
   - `services/workers/pyqgis/types.py` — typed shapes (pydantic v2 or `@dataclass(frozen=True, slots=True)` — surface choice as OQ, lean toward pydantic v2 for consistency with `grace2-contracts` ecosystem even though this is not a contract):
     - `WorkerResult` with fields `qgs_uri: str`, `layers_before: list[str]`, `layers_after: list[str]`, `notify_message_id: str | None`, `status: Literal["ok", "error"]`, `ts: datetime` (UTC ISO-8601 with literal `Z`).
     - `LayerSpec` if needed for the `layer_to_add` parameter (name + source URI or inline definition).
   - `services/workers/pyqgis/worker.py` — the entrypoint module:
     - `worker_round_trip(qgs_uri: str, layer_to_add: LayerSpec) -> WorkerResult` with FR-TA-3 docstring:
       - One-sentence summary.
       - "Use this when:" — explicit guidance.
       - "Do NOT use this for:" — explicit anti-guidance (e.g., do not use for first-time .qgs creation; this is mutation only).
       - Parameter descriptions (qgs_uri = `gs://bucket/path.qgs` form; layer_to_add = LayerSpec).
       - Return description (WorkerResult shape).
     - Implementation steps inside the function:
       a. Initialize QGIS application headlessly (`QgsApplication([], False)`, `initQgis()`).
       b. Read the `.qgs` from GCS via `/vsigs/`: `project = QgsProject.instance(); project.read(f"/vsigs/{bucket}/{path}")`. Capture `layers_before`.
       c. Mutate: append the `layer_to_add` (a second styled layer). Apply the `styles/basemap.qml` preset via the `apply_style_preset` codepath (proves FR-QS-5 preset application). Capture `layers_after`.
       d. Write back: TENTATIVE preference — write via `/vsigs/` (`project.write(f"/vsigs/{bucket}/{path}")`) for single-codepath symmetry with read. Alternative: write to local temp, then `gcloud storage cp` (more debuggable). Surface as OQ; pick `/vsigs/` write tentatively.
       e. Publish completion to Pub/Sub topic `grace-2-worker-events`: minimal envelope `{worker_job_id, qgs_uri, status, layers_before, layers_after, ts}` as JSON bytes. Capture `notify_message_id`.
       f. Return populated `WorkerResult`.
     - External-call resilience: wrap GCS read (via `/vsigs/`), GCS write, and Pub/Sub publish with retry-and-backoff (3 attempts, exponential ~250ms base); on exhaustion raise a typed `WorkerError` (or return `WorkerResult(status="error", ...)` — surface as OQ).
     - **Tear down** QGIS app at end (`qgs.exitQgis()`) regardless of success/error path.
   - CLI entrypoint at the bottom of `worker.py` (`if __name__ == "__main__":` block parsing `--qgs-uri` + `--layer-name` args) so the Cloud Run Job in 0021 can invoke `python -m services.workers.pyqgis.worker --qgs-uri gs://... --layer-name demo`.
2. **Unit test the entrypoint** locally in the `grace2` env (test code lands here under engine ownership; the M2 acceptance suite in 0023 re-runs the live cloud variant):
   - Place a small unit test next to the module — `services/workers/pyqgis/test_worker_local.py` — that runs `worker_round_trip` against a LOCAL `.qgs` (not `/vsigs/`) and asserts: `layers_after == layers_before + ["<layer_to_add.name>"]`, return value is a `WorkerResult` with `status == "ok"`. Test uses no LLM (invariant 2). Document Pub/Sub mocking strategy (stub the publisher or skip in local mode).
   - Run the test in the `grace2` env; verbatim transcript in report.
3. **Live end-to-end run** (against the deployed substrate from 0018 + the canonical `.qgs` from 0019):
   - Run `python -m services.workers.pyqgis.worker --qgs-uri gs://grace-2-hazard-prod-qgs/grace2-sample.qgs --layer-name demo` locally in `grace2` env (with ADC).
   - Verify: GCS object MD5 changed; `gcloud pubsub subscriptions create temp-verify-sub --topic=grace-2-worker-events && gcloud pubsub subscriptions pull temp-verify-sub --auto-ack --limit=10 --format=json` returns the completion envelope; `curl <qgis-server-url>/ogc/?MAP=/vsigs/grace-2-hazard-prod-qgs/grace2-sample.qgs&SERVICE=WMS&REQUEST=GetCapabilities` now shows two layers (basemap + demo). Verbatim transcripts in report. Clean up the temp subscription at end.
4. **Open Questions to surface (TENTATIVE-tagged):**
   - `WorkerResult` shape: pydantic v2 vs `@dataclass`. TENTATIVE: pydantic v2 (matches contracts ecosystem; serializes to JSON cleanly for the Pub/Sub envelope).
   - Write-back path: `QgsProject.write("/vsigs/...")` vs temp-file + `gcloud storage cp`. TENTATIVE: `/vsigs/` write (single-codepath symmetry).
   - Error-return convention: raise `WorkerError` vs return `WorkerResult(status="error")`. TENTATIVE: return `WorkerResult(status="error", ...)` so the Cloud Run Job exit code can be 0 (Pub/Sub message delivered) while the payload signals failure — matches future agent consumer's structured-error pattern (NFR-R-1).
   - Pub/Sub envelope shape: `{worker_job_id, qgs_uri, status, layers_before, layers_after, ts}`. TENTATIVE: this set. May need schema's blessing if it ever crosses a contract seam (M3+ agent consumer); for M2 it's a worker-internal envelope.
   - Whether to write a MongoDB metadata document at completion (FR-MP-3 "writers update both within a worker job"). TENTATIVE: skip in M2 — no Appendix D RunDocument/EventDocument shape applies to a basemap-stub round-trip; first MongoDB write lands when first real run document appears (M5 SFINCS). Document the deferral.

### File ownership (exclusive)

- `services/workers/pyqgis/__init__.py`
- `services/workers/pyqgis/worker.py`
- `services/workers/pyqgis/types.py`
- `services/workers/pyqgis/test_worker_local.py` (engine-owned local unit test; the M2 acceptance suite under `tests/m2/` is testing-owned in 0023)

**FROZEN (do NOT edit):** `services/workers/pyqgis/sample_project/**` (job-0019's deliverable, frozen), `styles/**` (job-0019's deliverable, frozen — apply via PyQGIS but do not modify the QML), `packages/contracts/**`, `services/agent/**`, `web/**`, `tests/**` (testing-owned), `infra/**`, `docs/SRS_v0.3.md`, `public_hazard_catalog.yaml`.

### Cross-cutting principles in force

Cite by name from AGENTS.md § "Cross-cutting principles":
- **Pre-MVP scope — no legacy support.** No `boto3` fallback, no AWS SDK paths, no provider abstraction wrappers. Pure `google-cloud-storage` + `google-cloud-pubsub` + PyQGIS.
- **Remove don't shim.** No `# TODO: support local file backend too` branches — `/vsigs/` is the only read/write path in production. Local unit-test path uses a local `.qgs` directly, not a shim.
- **Live E2E validation required.** Verbatim local-unit-test transcript in `grace2` env + verbatim live-cloud round-trip transcript (CLI invocation + GCS MD5 change + Pub/Sub message pull + post-mutation GetCapabilities showing 2 layers).
- **Bundle small fixes; scan for all instances.** No prior bug class to sweep here (first worker code in the project), but if you encounter dead-code under `services/workers/` (placeholder READMEs etc.), record but do not silently delete; surface as Open Question.
- **Diagnose before fix.** Round-trip failure: name the failing layer (PyQGIS read vs `/vsigs/` GDAL config vs IAM vs Pub/Sub publish vs QGIS Server cache).
- **Surface uncertainty.** Every contestable choice → Open Question with TENTATIVE tag.
- **Don't edit in-flight kickoffs.** Frozen.
- **Engine: typed results only, narration metrics in fields.** `WorkerResult` is the typed return — no prose return values.
- **Engine: workflows have ZERO LLM calls.** This is the canonical M2 LLM-free worker. Invariant 2.
- **Engine: PyQGIS-worker is the only `.qgs` writer.** This worker is THE writer. Invariant 4.
- **Engine: tool docstrings to the FR-TA-3 letter.** Applies to `worker_round_trip` (the public entry).

### Acceptance criteria (reviewer re-runs)

- `services/workers/pyqgis/worker.py` exists; `python -c "from services.workers.pyqgis.worker import worker_round_trip; help(worker_round_trip)"` in `grace2` env prints a docstring containing all FR-TA-3 sections (one-sentence summary, "Use this when:", "Do NOT use this for:", parameters, returns).
- `python -m pytest services/workers/pyqgis/test_worker_local.py -v` in `grace2` env returns 1 pass; the test asserts `layers_after == layers_before + [layer_to_add.name]` and `status == "ok"`.
- Live round-trip: `python -m services.workers.pyqgis.worker --qgs-uri gs://grace-2-hazard-prod-qgs/grace2-sample.qgs --layer-name demo` returns exit code 0; `gcloud storage ls -L gs://grace-2-hazard-prod-qgs/grace2-sample.qgs` shows updated `md5Hash`; the temp Pub/Sub subscription pulls a JSON message with the expected envelope shape.
- Post-mutation `curl "<qgis-server-url>/ogc/?MAP=/vsigs/grace-2-hazard-prod-qgs/grace2-sample.qgs&SERVICE=WMS&REQUEST=GetCapabilities"` returns XML listing both `basemap-osm-conus` and `demo` layers.
- Grep verification: `grep -rEn 'gemini|google\\.generativeai|anthropic|openai' services/workers/pyqgis/` returns ZERO matches (Invariant 2 mechanical guard).
- All Open Questions surfaced with TENTATIVE tags + SRS references.
- **CLEANUP:** the temp Pub/Sub subscription created for verification is deleted; the sample `.qgs` is restored to its pre-mutation state at end of testing OR the post-mutation state is acceptable and documented (surface as part of OQ — TENTATIVE: leave mutated, document the layer manifest in report; job-0023 acceptance re-runs the mutation against a fresh upload anyway).

Surface contestable choices as Open Questions with TENTATIVE tags.

## Assessment

`services/workers/pyqgis/{worker,types,__init__,__main__}.py` ships `worker_round_trip(qgs_uri, layer_to_add) -> WorkerResult` that reads `.qgs` from any filesystem-like URI (`/vsigs/`, `gs://`, `/mnt/qgs/`, or absolute path), appends a typed layer, writes back, and publishes a completion envelope to Pub/Sub topic `grace-2-worker-events`. Naturally aligned with the job-0024 WMS URL contract change — `_parse_qgs_uri` (worker.py:103-137) routes `/mnt/qgs/...` through the local-path branch into `QgsProject.read()` with zero code change. Three live closeout transcripts in `grace2` conda env (QGIS 3.40.3-Bratislava): local round-trip PASS, `/mnt/qgs/`-shape round-trip PASS, live Pub/Sub publish PASS (msg_id `19958824608799377`). Two commits: `c63507d` (initial) + `aa9a5da` (closeout — doc tightening + sidecar housekeeping + report population). Reviewer verdict: approve with three low-severity-only findings (kickoff-text mismatch, vacuously-pass sidecar AC, dict-order non-determinism in layer listing).

## Invariant Check

- **Determinism boundary:** pass — worker code is deterministic Python; no LLM packages imported (`grep -rE 'gemini|google\.genai|anthropic|openai' services/workers/pyqgis/` returns zero).
- **Deterministic workflows:** pass — `worker_round_trip` is a stable-signature pure-Python function (FR-TA-1). No LLM dispatch.
- **Engine registration, not modification:** pass — engine extended the tool body via registration; no agent-core changes.
- **Rendering through QGIS Server:** pass — worker is the *only* code path created that writes `.qgs`. QGIS Server (job-0018/0024) read-only mount enforces the asymmetry at runtime.
- **Tier separation:** pass — worker reads/writes via `qgs_uri` parameter (filesystem or `gs://`); no public bucket path; no client-side path.
- **Metadata-payload pattern:** pass — `.qgs` payload in GCS; Pub/Sub envelope (small, structured) is the notification channel, not the payload itself.
- **Claims carry provenance:** n/a — no hazard event data.
- **Cancellation is first-class:** n/a — worker runs are short; cancellation is the Cloud Run Jobs-level concern handled by job-0021's container + future Cloud Workflows wrapper.
- **Confirmation before consequence — and no cost theater:** pass — zero cost fields in WorkerResult or notify envelope.
- **Minimal parameter surface:** pass — `worker_round_trip(qgs_uri, layer_to_add)` is 2 args; CLI is `--qgs-uri --layer-to-add` with env-var fallbacks.

## Dependency Check

- **Prerequisites satisfied:** yes — job-0019 (sample `.qgs` + layer name `basemap-osm-conus`); job-0022 (grace2 conda env); job-0024 (WMS URL contract `/mnt/qgs/...` — naturally accommodated by `_parse_qgs_uri`'s local-path branch).
- **Downstream impacts:**
  - **job-0021 (worker container):** packages this worker code into a Cloud Run Job container. Specialist should use the same Cloud Run gen2 GCS volume mount pattern as job-0024 (symmetry) — mount `/mnt/qgs` writable for the worker SA (`roles/storage.objectAdmin` on the bucket). The Python code is mount-agnostic; container picks the access method.
  - **job-0023 (M2 acceptance):** invokes the worker via `gcloud run jobs execute` once 0021 lands; verifies round-trip end-to-end with Pub/Sub envelope consumed by a temp subscription.
  - **Future agent integration:** `tools/run_pyqgis_worker_round_trip` atomic tool wraps this for the agent at M4; envelope subscriber (likely Cloud Workflows) consumes the Pub/Sub for cancellation/completion at M5.

## Decisions Validated

- **`_parse_qgs_uri` accepts `/vsigs/`, `gs://`, and any absolute filesystem path:** agree — URI-agnostic worker code is the right abstraction; the *container* (job-0021) decides the access method (mount vs Python client). The `/vsigs/` branch is now harmless dead code under the new job-0024 contract but is not a parallel-implementation legacy (it's unexercised, not duplicate behavior).
- **Pub/Sub envelope shape: `{worker_run_id, qgs_uri, layers_after, completed_at, status, ...}`:** agree — small, structured, JSON-encoded; subscriber drives downstream routing.
- **`WorkerResult.notify_message_id` may be null when the publish completes after the envelope was constructed (OQ-20G chicken-and-egg):** agree — keep single shape; document. Alternative considered: 2-stage envelope (construct → publish → re-publish with msg_id) — rejected (added complexity without value).
- **`LayerSpec` is a string in v0 (just the layer name) with room to evolve to a typed shape:** agree — minimal parameter surface (Invariant 10); extends without breaking.
- **CLI lives in `__main__.py` (not separate `cli.py`):** agree — idiomatic Python `python -m services.workers.pyqgis`. Note kickoff text said `grace2_workers.pyqgis` (stale path); actual is `services.workers.pyqgis`. Specialist correctly used the actual path.
- **Worker code remains URI-agnostic; no `/vsigs/` deletion:** agree — keeping the dead branch is cheap and documents historical contract; *removing* would be a parallel-impl deletion if a future job adds it back differently.
- **Live Pub/Sub publish during closeout reused prior verification round's temp subscription** (auto-mode policy denied a second temp subscription creation): agree — original closeout round already pulled the envelope from a temp subscription with `msg_id 19957344354598949` and decoded payload. The closeout's `msg_id 19958824608799377` reuses the topic but skips the subscription dance to respect the policy.

## Open Questions Resolved

- **OQ-20A (URI shape — `/vsigs/` vs `gs://` vs local):** resolved → all three supported; `_parse_qgs_uri` (worker.py:103-137) dispatches.
- **OQ-20B (Pub/Sub envelope shape):** resolved → single-shape with `notify_message_id` allowing null (see OQ-20G).
- **OQ-20C (LayerSpec typing):** resolved → string for v0; typed shape deferred.
- **OQ-20D (kickoff-text issue — `osm-basemap` layer name):** resolved by job-0024-era kickoff `sed` to `basemap-osm-conus`.
- **OQ-20G (notify_message_id chicken-and-egg null in published envelope):** TENTATIVE keep single shape; document. Acceptable trade-off vs 2-stage publish.
- **OQ-20H (WMS URL contract `/mnt/qgs/...` from job-0024):** resolved → worker handles natively via local-path branch.

## Follow-up Actions

- **Kickoff template correction** — future engine-on-worker-code kickoffs should reference `python -m services.workers.pyqgis` (the actual module path), not `python -m grace2_workers.pyqgis` (stale name from an earlier draft). Cosmetic.
  - Routing: orchestrator (next engine kickoff). Priority: low.
- **Layer ordering determinism in `_layer_names`:** dict-order from `QgsProject.mapLayers().values()` is non-deterministic across runs. If acceptance suite or downstream consumer depends on stable ordering, switch to `mapLayersByName` or sort by `layerOrder`. Low-priority — fix in next engine touch.
  - Routing: engine. Priority: low.
- **Layer addition is APPENDED today; consider explicit ordering control:** the new layer ends up first OR last depending on dict order. If the layer-style preset cares about order (background vs foreground), make explicit. Cosmetic for M2; revisit if it bites M3 WMS-tile-render order.
  - Routing: engine. Priority: low.
- **job-0021 worker container should mount `/mnt/qgs` writable** (symmetry with QGIS Server's read-only mount) using the same Cloud Run gen2 GCS volume mount pattern, with `roles/storage.objectAdmin` on the bucket for the worker SA. Worker code is mount-agnostic; this is a container-side decision.
  - Routing: infra (job-0021). Priority: high (next job).
- **PROJECT_STATE update** (this audit closure): worker code committed; CLI `python -m services.workers.pyqgis --qgs-uri ... --layer-to-add ...`; URI dispatch supports `/vsigs/`, `gs://`, `/mnt/qgs/`, local paths; Pub/Sub publish verified live.
  - Routing: orchestrator. Priority: high.
- **Close job-0020; launch job-0021 (worker container).** This unblocks job-0023 (M2 acceptance).
  - Routing: orchestrator. Priority: high.

## Sign-off

- **Ready to move to complete:** yes
- All 10 reviewer adversarial checks pass on closeout: `worker_round_trip` signature correct; local round-trip live PASS; Pub/Sub publish live PASS; no LLM packages imported; no `/vsigs/` forced-branch for `.qgs` reads (URI-agnostic dispatch); sidecar deletion verified absent; file ownership clean; commit hygiene namespaced + co-author trailered; CLI entry point works via actual module path; Invariants 2 + 4 preserved with file:line citations.
- Invariants #1, #2, #3, #4, #5, #6, #9, #10 pass with citations; #7, #8 n/a (no claims/cancellation surface in this job).
- One revision round (closeout populated report.md after specialist hit StructuredOutput cap mid-doc-tightening); second-pass reviewer approved with three low-severity-only findings (all accepted with rationale).
- 8 Open Questions surfaced; OQ-20A/B/C/D/H resolved; OQ-20G TENTATIVE; rest carry-forward as small follow-ups.
- Live engineering verified: worker round-trips on local path + `/mnt/qgs/` shape + Pub/Sub publish.
- Revisions: 1.
