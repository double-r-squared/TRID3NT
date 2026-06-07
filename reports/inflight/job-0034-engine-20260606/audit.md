# Audit: 2 QGIS discovery atomic tools (list_qgis_algorithms, describe_qgis_algorithm) + qgis_process DI binding

**Job ID:** job-0034-engine-20260606, **Sprint:** sprint-06, **Auditor:** Development Orchestrator, **Status:** assigned

## Task Assignment

**Specialist:** engine

**Prerequisites:**
- **job-0030-schema-20260606 (APPROVED — required):** provides `AtomicToolMetadata`.
- **job-0031-infra-20260606 (APPROVED — required):** provides cache bucket.
- **job-0032-agent-20260606 (APPROVED — required):** provides registry decorator + cache shim + `qgis_process` pass-through stub awaiting `set_worker_submitter(submitter)` DI binding. **Read `reports/complete/job-0032-agent-20260606/report.md`** to absorb the DI seam.
- **job-0021-infra-20260605:** the PyQGIS worker Cloud Run Job (`grace-2-pyqgis-worker`, image `@sha256:fffd7e0f`) is the deployed substrate. `gcloud run jobs execute grace-2-pyqgis-worker` is the existing invocation pattern; this job wraps it for the agent-side tools.

**SRS references** (narrow files only):
- `docs/srs/03-functional-requirements.md` — FR-TA-2 atomic tools (incl. the existing `qgis_process` + `list_qgis_algorithms` + `describe_qgis_algorithm` declarations; this job implements them), FR-AS-9 capability discovery (Level 1a is the discovery loop), §3.9 caching.

### Environment
Live PyQGIS worker Cloud Run Job from job-0021. The worker accepts a JSON payload `{qgs_uri, layer_to_add, ...}` per the existing pattern. For algorithm discovery, the worker needs to handle a new command shape — either:
- **Option A:** extend the worker's command surface to accept `{command: "list_algorithms"}` and `{command: "describe_algorithm", algorithm_id: "..."}` — touches the worker image (FROZEN under `services/workers/**`).
- **Option B:** use the existing worker substrate via a one-shot Python script invocation that does `qgis_process list` / `qgis_process help <alg>` and returns the result — doesn't touch the worker image.

**TENTATIVE recommendation: Option B for this job** — keeps the worker image untouched, scope stays within the agent service. Surface as a Decision Made; if Option B is awkward, surface as a schema-pushback OQ for v0.3.16 to formalize a worker command-surface contract.

### Scope

1. **`services/agent/src/grace2_agent/tools/qgis_discovery.py`** (NEW):
   - `list_qgis_algorithms(category_filter: str | None = None, search_terms: str | None = None) → list[QGISAlgorithmSummary]` — invokes the worker with a `qgis_process list` command via the Cloud Run Jobs API; parses output; returns ranked list (max 50 per FR-TA-2). `ttl_class="static-30d"`, `source_class="qgis_algorithms_catalog"`, `cacheable=True`. The algorithm catalog rarely changes (only on worker image rebuild).
   - `describe_qgis_algorithm(algorithm_id: str) → QGISAlgorithmDescription` — invokes worker with `qgis_process help <algorithm_id>`; parses signature + parameter types + descriptions. Same TTL class (`static-30d`) and source_class.

2. **Bind `set_worker_submitter(submitter)` DI seam** from job-0032's `passthroughs.py`. The submitter is a function that takes a Cloud Run Job payload and returns the execution result. Wire the binding in `main.py` startup. After binding, `qgis_process` body no longer raises `NotImplementedError`.

3. **Worker invocation pattern** — use `google-cloud-run` Python client (or `gcloud run jobs execute` via subprocess as fallback) per the job-0021 verified pattern. Capture worker invocation latency in the report so future jobs (M5+) have a baseline.

4. **Return types `QGISAlgorithmSummary` / `QGISAlgorithmDescription`** — use existing `grace2-contracts` shapes if they exist; otherwise dicts with documented keys (`{algorithm_id, name, category, brief_description}` for summary; `{algorithm_id, name, parameters: [{name, type, description, default?}], outputs: [{name, type, description}]}` for description). Do NOT add new pydantic models (FROZEN packages/contracts).

5. **Tests** in `services/agent/tests/test_qgis_discovery.py`: at least 4 unit tests (happy path for each tool + cache-hit replay + worker submission failure re-raise).

6. **Live evidence** in `evidence/`: a real `list_qgis_algorithms()` call against the deployed worker, capturing the worker invocation latency + first 10 algorithm summaries. A real `describe_qgis_algorithm("native:zonalstatistics")` call (the algorithm the M4 demo will use), capturing the parameter signature.

### File ownership (exclusive)

- `services/agent/src/grace2_agent/tools/qgis_discovery.py` (NEW)
- `services/agent/src/grace2_agent/main.py` — ONLY the `set_worker_submitter` DI binding line(s)
- `services/agent/pyproject.toml` — add `google-cloud-run` Python client if not present
- `services/agent/tests/test_qgis_discovery.py` (NEW)
- `reports/inflight/job-0034-engine-20260606/` — kickoff frozen

### FROZEN — no edits in this job

- `services/agent/src/grace2_agent/tools/{__init__.py,cache.py,passthroughs.py,README.md,data_fetch.py}` (data_fetch.py is job-0033)
- `services/agent/src/grace2_agent/server.py`, `mcp.py`
- `services/workers/**` (Option B per the kickoff decision — do not extend the worker command surface in this job)
- `packages/contracts/**`, `infra/**`, `web/**`, `docs/srs/**`, `docs/SRS_v0.3.md`, `styles/**`, `reports/complete/**`

### Cross-cutting principles in force

- **Invariant 1 (Determinism boundary):** preserves. Algorithm catalog enumeration is deterministic (modulo worker image rebuild — handled by the cache `static-30d` TTL).
- **Invariant 8 (Cancellation is first-class):** preserves. Worker invocation is the standard Cloud Run Jobs path; cancellation routes through `gcloud run jobs executions cancel` or the SDK equivalent per the existing M1 cancel chain.
- **FR-CE-8 fail-fast:** metadata validates at import.
- **FR-AS-9 Level 1a:** this job lands the agent's QGIS algorithm discovery loop substrate. The agent can now ask "what algorithms can do X?" and chain to `qgis_process` invocation for the matched algorithm.
- **Diagnose before fix:** if worker invocation fails (IAM, timeout), capture the gcloud error before changing the submission code.

### Acceptance criteria (reviewer re-runs)

- [ ] `tools/qgis_discovery.py` registers 2 atomic tools via `@register_tool`; `TOOL_REGISTRY` now contains 8 tools total (2 pass-throughs + 4 fetchers from job-0033 + 2 discovery from this job) after `--startup-only` run with all three Stage C jobs landed.
- [ ] Both tools route through `read_through` with `static-30d` / `qgis_algorithms_catalog`.
- [ ] `list_qgis_algorithms()` live call returns ≥ 100 algorithms (deployed worker has native QGIS + GDAL + GRASS providers); first 10 captured in evidence.
- [ ] `describe_qgis_algorithm("native:zonalstatistics")` live call returns parameter signature; captured in evidence.
- [ ] `set_worker_submitter` DI binding wired in `main.py`; `qgis_process` body no longer raises `NotImplementedError` (tested with a tiny mocked submitter).
- [ ] At least 4 unit tests + agent suite green; contracts still 131/131.
- [ ] `python -m grace2_agent --startup-only` exits 0 with `tool registry loaded: 6 tool(s)` if running alone or `8 tool(s)` if running after job-0033.
- [ ] No edits to any FROZEN path listed above (especially not `services/workers/**`).

Surface contestable choices as Open Questions with TENTATIVE tags — at minimum: Option A vs Option B for worker discovery commands (TENTATIVE: B — keeps worker image untouched); `static-30d` vs `semi-static-7d` for algorithm catalog (TENTATIVE: static-30d — catalog only changes on worker image rebuild which is rare); whether parameter parsing should be tolerant of new QGIS versions (TENTATIVE: yes — return raw text for unparsed sections); subprocess vs SDK for Cloud Run invocation (TENTATIVE: SDK — matches the job-0021 pattern).
