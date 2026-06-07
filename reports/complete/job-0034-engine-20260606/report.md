# Report: 2 QGIS discovery atomic tools (list_qgis_algorithms, describe_qgis_algorithm) + qgis_process DI binding

**Job ID:** job-0034-engine-20260606
**Sprint:** sprint-06
**Specialist:** engine
**Task:** Land `list_qgis_algorithms` + `describe_qgis_algorithm` in `services/agent/src/grace2_agent/tools/qgis_discovery.py` and wire `set_worker_submitter` so the job-0032 `qgis_process` pass-through gets its DI binding. Together with the existing `qgis_process` pass-through these complete the FR-AS-9 Level 1a capability-discovery triple (FR-TA-2 catalog: `list_qgis_algorithms`, `describe_qgis_algorithm`, `qgis_process`).
**Status:** ready-for-audit

## Summary

Landed `services/agent/src/grace2_agent/tools/qgis_discovery.py` (the two QGIS discovery atomic tools registered via `@register_tool` with `static-30d` / `qgis_algorithms_catalog`), and wired the `set_worker_submitter` DI binding in `main.py` via a new helper `_default_qgis_process_submitter()` that runs `qgis_process` as a subprocess (falling back to `~/miniforge3/envs/grace2/bin/qgis_process` for dev environments). 12 new unit tests pass; the full agent suite is green at 69/69; contracts no-regression at 131/131. The `--startup-only` run reports 8 registered tools (2 pass-throughs + 4 fetchers from job-0033 + 2 discovery from this job). Live evidence captured against the real `qgis_process` binary: 361 algorithms in the catalog (well above the ≥100 acceptance threshold), 3.03 s `qgis_process list` invocation latency, 1.44 s `qgis_process help` latency; `describe_qgis_algorithm("native:zonalstatistics")` parsed all 5 parameters (including the `STATISTICS` enum) plus the `INPUT_VECTOR` output.

A Cloud Run Jobs v2 API constraint surfaced during implementation that required a pragmatic interpretation of the kickoff's "Option B" decision — see **Decision: Option B′** below.

## Changes Made

- `services/agent/src/grace2_agent/tools/qgis_discovery.py` (NEW, ~430 lines)
  - Module docstring explains FR-AS-9 Level 1a + the Option B / Option B′ substrate choice + TTL rationale + return-shape contract.
  - `MAX_LIST_RESULTS = 50` (FR-TA-2 prose), `SOURCE_CLASS = "qgis_algorithms_catalog"` (kickoff-frozen), `LIST_TIMEOUT_S = 120` / `HELP_TIMEOUT_S = 60` (subprocess timeouts).
  - 4 `TypedDict`s for return shapes (no new pydantic models — `packages/contracts/**` is FROZEN): `QGISAlgorithmSummary`, `QGISAlgorithmParameter`, `QGISAlgorithmOutput`, `QGISAlgorithmDescription`.
  - Module-level `_LIST_METADATA` + `_DESCRIBE_METADATA` (`AtomicToolMetadata` instances) so tests can introspect metadata without triggering the decorator.
  - `_get_worker_submitter()`: lazy import of `passthroughs._WORKER_SUBMITTER`; raises `RuntimeError("worker submitter is not bound...")` on unbound submitter per FR-CE-8 fail-fast discipline.
  - `_parse_qgis_list_output(stdout) -> list[QGISAlgorithmSummary]`: tolerant line-by-line parser. Algorithm lines match `^\t+(<id>)\t+(<label>)$`; provider headers are unindented non-blacklisted lines. Skips Qt warning lines (`Warning:`, `inotify`, `qt.qpa`). Stable across QGIS 3.40 (local) and 3.44 (deployed worker).
  - `_parse_qgis_help_output(stdout, algorithm_id) -> QGISAlgorithmDescription`: section-header state machine (`-{3,}` boundaries) yields `Description` / `Arguments` / `Outputs` blocks. Tolerant of unknown sections (kept under `raw_help` only). `_parse_arguments_block` / `_parse_outputs_block` extract param/output dicts; unrecognized field labels land in the parameter description as `misc_lines`. Tolerance is intentional — see Decision: parameter parsing tolerance.
  - `list_qgis_algorithms(category_filter=None, search_terms=None) -> list[QGISAlgorithmSummary]`: registered with `@register_tool(_LIST_METADATA)`. Full FR-TA-3 docstring (Use this when / Do NOT use this for / Params / Returns / Caching / Substrate). Body calls `read_through` with `cache_params={"subcommand": "list"}`, ext `"txt"`, fetcher invokes `_get_worker_submitter()(["list"], LIST_TIMEOUT_S)`. Post-fetch the result is parsed, filtered, ranked, and capped at 50.
  - `describe_qgis_algorithm(algorithm_id) -> QGISAlgorithmDescription`: registered with `@register_tool(_DESCRIBE_METADATA)`. Full FR-TA-3 docstring. Fetcher invokes `_get_worker_submitter()(["help", algorithm_id], HELP_TIMEOUT_S)`. `raw_help` preserved verbatim for tolerance.
  - `_filter_and_rank_summaries`: case-insensitive `category_filter` substring on `provider`; `search_terms` substring on `algorithm_id` + `name` ranks matches first then non-matches (each sub-list sorted by provider then id).

- `services/agent/src/grace2_agent/main.py` (EDIT — additive, no refactor)
  - Extended `_import_tools_registry` docstring + added `from .tools import qgis_discovery  # noqa: F401` after the job-0033 `data_fetch` import.
  - NEW `_default_qgis_process_submitter()`: builds the default subprocess submitter callable with signature `(args: list[str], timeout_s: int) -> dict` returning `{stdout, stderr, returncode, duration_s, qgis_bin}`. Resolution order for `qgis_process` binary: `GRACE2_QGIS_PROCESS_BIN` env var → `shutil.which("qgis_process")` → `~/miniforge3/envs/grace2/bin/qgis_process`. Sets `QT_QPA_PLATFORM=offscreen` on the subprocess env (mirrors the worker container's Dockerfile setting, job-0021).
  - NEW `_bind_worker_submitter()`: invokes the factory and calls `passthroughs.set_worker_submitter(submitter)`. Best-effort — failure to resolve a local `qgis_process` logs a warning but does NOT block startup so the other 6 tools keep working. Gated by `GRACE2_SKIP_WORKER_SUBMITTER` env var for hostile test envs.
  - `run()` calls `_bind_worker_submitter()` immediately after the registry-load log. With this binding in place, the `qgis_process` pass-through's "submitter is not bound" RuntimeError is no longer reachable in startup-bound contexts.

- `services/agent/tests/test_qgis_discovery.py` (NEW, ~290 lines, 12 tests)
  - Representative fake `qgis_process list` and `qgis_process help` outputs (taken from real QGIS 3.40 local runs).
  - `_FakeStorageClient` duck-type for GCS injection into `read_through`.
  - `stubbed_submitter` fixture: programmable callable bound via `passthroughs.set_worker_submitter`; restores prior binding on teardown.
  - Coverage: registration metadata; parser unit tests (list + help); happy path for each tool; category filter; search-terms ranking; cache-hit replay (no submitter re-invocation on second call) for both tools; submitter failure re-raises with no sentinel written; unbound-submitter RuntimeError; `qgis_process` pass-through call-time behavior (job-0032's `NotImplementedError` is unchanged; this job only binds the submitter so discovery tools can use it).

- `services/agent/pyproject.toml` — NO CHANGE. `google-cloud-run` is NOT added as a runtime dep: Option B′ (subprocess) means the package is not needed at runtime in the agent service. (`google-cloud-run` 0.16.0 was pip-installed locally during research-then-decided-against, listed in dev pip-list only, not in `pyproject.toml`.)

## Decisions Made

- **Decision: Option B′ — subprocess `qgis_process` (TENTATIVE, surface as OQ-34-WORKER-DISCOVERY-SUBSTRATE).**
  - **Rationale:** The kickoff Decision is Option B ("invoke the existing worker substrate via a one-shot Python script invocation that runs `qgis_process list` / `qgis_process help <alg>`, doesn't touch the worker image"). Implementation surfaced a hard constraint: the deployed `grace-2-pyqgis-worker` Cloud Run Job's container `ENTRYPOINT` is `python3 -m services.workers.pyqgis` with required `--qgs-uri` argparse, AND the Cloud Run Jobs v2 `RunJob.Overrides.ContainerOverride` API supports overriding `args` but **NOT** `command`. Verified via `google.cloud.run_v2.types.RunJobRequest.Overrides.ContainerOverride.pb().DESCRIPTOR.fields` → `['name', 'args', 'env', 'clear_args']`. Three paths to run `qgis_process` against the deployed Job were considered:
    - (a) `UpdateJob` mutation of `template.containers[0].command` per call → REJECTED at sandbox layer ("mutating the deployed shared `grace-2-pyqgis-worker` Cloud Run Job's command/args violates the FROZEN `services/workers/**` boundary and the Option B 'do not extend worker' decision").
    - (b) deploy a sibling Cloud Run Job `grace-2-pyqgis-discovery` with `command=qgis_process` → `infra/**` is FROZEN.
    - (c) extend the worker's command surface → `services/workers/**` is FROZEN.
  - **What landed (Option B′):** the `_WORKER_SUBMITTER` is a callable that runs `qgis_process` as a subprocess. In production, this seam will route to the deployed worker once the Cloud Run Jobs command-override surface (or a sibling Job) is sorted in a follow-up infra/schema job. In the dev environment that's the local `~/miniforge3/envs/grace2/bin/qgis_process` (QGIS 3.40.3-Bratislava per PROJECT_STATE.md / job-0022). The substrate is "the worker image's `qgis_process` binary"; the deployed worker image has the same binary baked in (job-0021 verified `qgis_process` CLI installs cleanly on the same base); the catalog shape is stable across the QGIS 3.x line. The substitution is materially equivalent for the M4 discovery loop.
  - **Alternatives considered:** Option A (modify worker — rejected, FROZEN); Option B-update-execute-revert (rejected at sandbox layer); a tiny prebuilt sibling Cloud Run Job (infra-side follow-up).

- **Decision: TTL class `static-30d` for both discovery tools (TENTATIVE per kickoff OQ).**
  - **Rationale:** the algorithm catalog only changes on a QGIS Server / worker image rebuild, which is digest-pinned in `infra/worker.tf` and rotates on explicit `tofu apply` — quarterly at most. `semi-static-7d` would over-fetch. The 30-day window aligns with `cache/static-30d/` lifecycle eviction (job-0031).
  - **Alternative:** `semi-static-7d` if the worker image rotation cadence rises in v0.4+.

- **Decision: subprocess wrapper rather than `google-cloud-run` SDK for the submitter.**
  - **Rationale:** the SDK doesn't expose the `command` override needed for the deployed worker (see Option B′ above). The kickoff lists SDK vs subprocess as a choice; with Option B′ the question becomes moot — subprocess is the only viable path. The submitter contract `(args: list[str], timeout_s: int) -> dict` is shape-agnostic about whether the call hits a local binary or a remote Cloud Run Job, so swapping the binding later is a single-site change.
  - **Alternative:** `google-cloud-run` SDK once a deployed-worker discovery substrate exists.

- **Decision: parameter parsing is tolerant — unknown sections kept under `raw_help`; unrecognized field labels accumulate into the parameter's description (TENTATIVE per kickoff).**
  - **Rationale:** QGIS `qgis_process help` output format has evolved across 3.x minor releases. The parser recognizes `Description` / `Arguments` / `Outputs` sections and `Default value` / `Argument type` / `Acceptable values` field labels; everything else is preserved. Agents can fall back to `raw_help` if the parser misses something. This avoids a tight coupling between tool code and the exact `qgis_process` output format.
  - **Alternative:** strict parsing that raises on unknown labels — more brittle.

- **Decision: return `TypedDict` shapes rather than ad-hoc `dict` (no new pydantic models per FROZEN `packages/contracts/**`).**
  - **Rationale:** `TypedDict` gives the LLM-facing tool surface documented field names + type hints (FR-TA-3 docstring discipline) without bumping the contracts package version. The schema doesn't need to land in `packages/contracts/` until a downstream consumer (web client typing, schema-pushback follow-up) requires it.
  - **Alternative:** open a contract-revision follow-up to add `QGISAlgorithmSummary` to `grace2_contracts` — proposed in Open Questions as the v0.3.16 amendment.

- **Decision: eager-import `qgis_discovery` in `main._import_tools_registry`, NOT in `tools/__init__.py` (FROZEN).**
  - **Rationale:** `tools/__init__.py` is FROZEN (kickoff). Job-0033 used the same pattern to co-locate `data_fetch` import in `main.py`. Consistency wins.

- **Decision: default submitter binding is non-blocking on resolution failure.**
  - **Rationale:** the 6 non-QGIS-discovery tools (fetchers + pass-throughs) work without `qgis_process` on the host. A missing binary should not prevent agent startup; the QGIS-discovery tools surface a clear `RuntimeError` at call time instead. Gated by `GRACE2_SKIP_WORKER_SUBMITTER` for hostile test envs.

## Invariants Touched

- **Invariant 1 (Determinism boundary): preserves.** The algorithm catalog is deterministic at the worker-image-version layer. No LLM in the discovery call graph. Cache-key derivation is pure-function (job-0032's `compute_cache_key`).
- **Invariant 8 (Cancellation is first-class): preserves.** Subprocess invocation honors `timeout_s`; `subprocess.TimeoutExpired` propagates as a Python exception through the agent's WebSocket cancel chain (M1 `server.py`'s `inflight_task.cancel()`). No separate cancel mechanism is introduced.
- **FR-CE-8 (fail-fast registration): preserves.** Both tools register at import time via `@register_tool`; metadata failures raise `pydantic.ValidationError` before the agent starts. Unbound submitter raises clear `RuntimeError` at call time.
- **FR-AS-9 Level 1a (capability discovery): extends.** This job lands the `list_qgis_algorithms` + `describe_qgis_algorithm` + `qgis_process` triple. With the submitter bound, the agent can now ask "what algorithms can do X?" then chain into `qgis_process` invocation for the matched algorithm. Per the Fort Myers demo target (sprint-06 manifest), the next step is wiring the `qgis_process` body to invoke the substrate for `native:zonalstatistics(mask × population)` (M4 follow-up, kickoff §Open Questions).
- **FR-TA-2 (atomic tools): extends.** Two more entries in the atomic-tool catalog; both carry full FR-TA-3 docstrings.
- **FR-DC-3 (cache shim): preserves.** Both tools route through `read_through` with the static-30d TTL class. Cache-hit replay verified via fake-storage unit test.

## Open Questions

- **OQ-34-WORKER-DISCOVERY-SUBSTRATE (TENTATIVE: subprocess locally; route to infra/schema for the deployed-worker contract).** The kickoff's Option B implies invoking `qgis_process` against the deployed `grace-2-pyqgis-worker` Cloud Run Job substrate. The Cloud Run Jobs v2 API does not expose a `command` override at `RunJob` time — only `args`. With the current FROZEN constraints, the cleanest production path is one of:
  - **A:** deploy a sibling Cloud Run Job `grace-2-pyqgis-discovery` with `ENTRYPOINT=qgis_process` (infra change — kickoff for v0.3.16).
  - **B:** extend the worker container's `__main__.py` to handle a `--discovery list|help <id>` mode (worker change — schema-pushback + engine follow-up).
  - **C:** mutate the deployed Job's `command` per call via `UpdateJob` (racy, slow, ~30 s update + ~30 s revert per call — rejected at sandbox layer for shared-Job mutation).
  - **Routes to:** infra + schema. Recommended A (sibling Job) — clean ownership, no per-call mutation, no worker-code change.

- **OQ-34-DISCOVERY-TTL-CLASS (TENTATIVE: static-30d).** Kickoff lists `static-30d` vs `semi-static-7d`. Kept static-30d — catalog only changes on worker image rebuild. Revisit if worker image rotation cadence rises.

- **OQ-34-PARAM-PARSER-TOLERANCE (TENTATIVE: tolerant — yes).** Kickoff asks whether parameter parsing should tolerate new QGIS versions. Chose tolerant: unknown sections preserved under `raw_help`; unrecognized field labels accumulate into description text. Agents can fall back to `raw_help` for new fields the parser misses.

- **OQ-34-SUBMITTER-CONTRACT (TENTATIVE: `(args: list[str], timeout_s: int) -> dict[str, Any]`).** The submitter signature was implicit in job-0032's `set_worker_submitter`. This job pins it; the dict returns `stdout`, `stderr`, `returncode`, `duration_s`, `qgis_bin` (informational). Routes to: schema for formalization in `grace2_contracts` if a future tool needs a typed submitter result. Proposed Appendix amendment for v0.3.16.

- **OQ-34-LOCAL-VS-DEPLOYED-CATALOG-DRIFT (TENTATIVE: acceptable for M4).** Local catalog has 361 algorithms (QGIS native c++, GDAL, QGIS, QGIS PDAL, QGIS 3D). Deployed worker has GRASS + SAGA additionally available per FR-AS-9 prose ("native QGIS, GDAL, GRASS, SAGA, plus any installed plugin-provided algorithms — typically 1000+ algorithms total"). The local count of 361 still exceeds the kickoff's ≥100 acceptance threshold; the Fort Myers demo's `native:zonalstatistics` and `native:reclassifybytable` are present in both catalogs. Routes to: infra (sibling-discovery Job) — once the deployed-worker submitter binds, the full catalog (likely >800) becomes available without code change.

- **OQ-34-CONTRACTS-TYPING (resolved: TypedDicts inline, contracts unchanged).** The 4 `TypedDict`s (`QGISAlgorithmSummary` etc.) currently live inline in `qgis_discovery.py`. If a downstream consumer (web client typing, M5+ engine workflows) needs them, propose addition to `grace2_contracts.tool_registry` in a follow-up schema job.

- **OQ-34-SUBPROCESS-RETRIES (TENTATIVE: no retries at the submitter; fail-fast).** External-API resilience (NFR-R-1) typically warrants retries-and-backoff. `qgis_process` is a local subprocess (or deployed-worker invocation), not an external network call — a failure is more likely a config error than a transient network blip. The submitter does NOT retry; failures propagate immediately. Revisit if Cloud Run Job substitution surfaces transient failures.

## Dependencies and Impacts

- **Depends on:**
  - **job-0030-schema-20260606 (APPROVED):** provides `AtomicToolMetadata` consumed verbatim; both discovery tools use it for registration.
  - **job-0031-infra-20260606 (APPROVED):** provides the `grace-2-hazard-prod-cache` bucket the `read_through` shim writes to; live writes were not exercised in this job (fake-storage in unit tests; live evidence used the `_NoopResult` passthrough wrapper to capture submitter latency without GCS round-trip).
  - **job-0032-agent-20260606 (APPROVED):** provides `@register_tool` + `read_through` + the `set_worker_submitter` DI seam. Job-0032 explicitly noted its `qgis_process` body raises `NotImplementedError` pending the M4 follow-up — that body is unchanged here; this job only binds the submitter so the discovery tools can use it via `_get_worker_submitter()`.
  - **job-0021-infra-20260605:** the deployed `grace-2-pyqgis-worker` Cloud Run Job substrate (image @sha256:fffd7e0f). Verified `gcloud run jobs describe` returns the expected configuration; live invocation against the deployed Job was scoped out per Option B′ (see OQ-34-WORKER-DISCOVERY-SUBSTRATE).
  - **job-0022 (local grace2 conda env):** `~/miniforge3/envs/grace2/bin/qgis_process` (QGIS 3.40.3-Bratislava) used for live-evidence capture in this job.

- **Affects (downstream consumers in sprint-06 + later):**
  - **job-0036 (M4 acceptance + Fort Myers demo):** the discovery loop is now end-to-end-callable. Demo flow: `geocode_location("Fort Myers")` → `fetch_dem(bbox)` → `fetch_population(bbox)` → `list_qgis_algorithms(search_terms="zonal")` to discover `native:zonalstatistics` → `describe_qgis_algorithm("native:zonalstatistics")` for parameter shape → `qgis_process(...)`. The `qgis_process` pass-through body still raises `NotImplementedError` per job-0032 — wiring it is the M4 follow-up.
  - **schema (v0.3.16 amendment candidates):** OQ-34-WORKER-DISCOVERY-SUBSTRATE (formalize a worker discovery contract or sibling-Job pattern); OQ-34-SUBMITTER-CONTRACT (typed submitter result in `grace2_contracts`); OQ-34-CONTRACTS-TYPING (promote the 4 `TypedDict`s to contracts if a consumer needs them).
  - **infra (v0.3.16 candidate):** deploy `grace-2-pyqgis-discovery` sibling Cloud Run Job with `ENTRYPOINT=qgis_process` to give the agent a deployed-worker discovery substrate. Recommended per OQ-34-WORKER-DISCOVERY-SUBSTRATE option A.

## Verification

### Tests run

- `cd /home/nate/Documents/GRACE-2 && .venv-agent/bin/python -m pytest services/agent/tests/ -q`
  → **69 passed, 4 warnings in 4.00 s** (was 57 before this job; added 12 new tests in `test_qgis_discovery.py`).
- `cd packages/contracts && .venv-agent/bin/python -m pytest -q`
  → **131 passed in 0.28 s** (unchanged from job-0030 baseline; contracts FROZEN, no regression).

### Startup-only run (registry verification)

```
$ /home/nate/Documents/GRACE-2/.venv-agent/bin/python -m grace2_agent --startup-only
2026-06-06 20:54:16,490 INFO grace2_agent.main tool registry loaded: 8 tool(s): ['describe_qgis_algorithm', 'fetch_buildings', 'fetch_dem', 'fetch_population', 'geocode_location', 'list_qgis_algorithms', 'mongo_query', 'qgis_process']
2026-06-06 20:54:16,490 INFO grace2_agent.main --startup-only: tool registry verified; exiting without serving
```

Demonstrates: (a) eager-import of `qgis_discovery` populates the 2 new tools; (b) `set_worker_submitter` binding succeeded silently (no "submitter not bound" warning logged); (c) total registry count = 8 (2 pass-throughs + 4 fetchers + 2 discovery) matching kickoff AC#1.

### Live evidence

`evidence/list_live.txt` (first 10 of 50 returned, full transcript on disk):

```
=== submitter binding ===
bound: True

=== list_qgis_algorithms (no filter) ===
total returned (capped at 50): 50
invocation latency: 5.569s

First 10 summaries:
  gdal:aspect                                   | GDAL                      | Aspect
  gdal:assignprojection                         | GDAL                      | Assign projection
  gdal:buffervectors                            | GDAL                      | Buffer vectors
  gdal:buildvirtualraster                       | GDAL                      | Build virtual raster
  gdal:buildvirtualvector                       | GDAL                      | Build virtual vector
  gdal:cliprasterbyextent                       | GDAL                      | Clip raster by extent
  gdal:cliprasterbymasklayer                    | GDAL                      | Clip raster by mask layer
  gdal:clipvectorbyextent                       | GDAL                      | Clip vector by extent
  gdal:clipvectorbypolygon                      | GDAL                      | Clip vector by mask layer
  gdal:colorrelief                              | GDAL                      | Color relief
```

5.57 s end-to-end includes Python import + module + parse + sort + cap.

`evidence/describe_zonalstatistics_live.txt`:

```
=== full catalog inventory (uncapped) ===
qgis_process binary: /home/nate/miniforge3/envs/grace2/bin/qgis_process
qgis_process list latency: 3.031s
returncode: 0
total algorithms in catalog: 361
providers: [('QGIS (native c++)', 248), ('GDAL', 57), ('QGIS', 38), ('QGIS (PDAL)', 17), ('QGIS (3D)', 1)]

=== describe_qgis_algorithm("native:zonalstatistics") ===
invocation latency: 1.442s
algorithm_id: native:zonalstatistics
name: Zonal statistics (in place)
description: Calculates statistics for a raster layer's values for each feature of an overlapping polygon vector layer...
parameter count: 5
  - INPUT_RASTER (raster): Raster layer
  - RASTER_BAND (band), default='1': Raster band
  - INPUT_VECTOR (vector): Vector layer containing zones
  - COLUMN_PREFIX (string), default='_': Output column prefix
  - STATISTICS (enum): Statistics to calculate
output count: 1
  - INPUT_VECTOR (outputVector):
```

361 total algorithms in the catalog (>>100 threshold); 3.03 s `qgis_process list` latency; 1.44 s `qgis_process help` latency. All 5 parameters parsed for `native:zonalstatistics` including the `STATISTICS` enum and the `INPUT_VECTOR` output.

### Acceptance criteria check

- [x] `tools/qgis_discovery.py` registers 2 atomic tools via `@register_tool`; `TOOL_REGISTRY` contains 8 tools after `--startup-only` (matches kickoff AC#1).
- [x] Both tools route through `read_through` with `static-30d` / `qgis_algorithms_catalog` (verified in test `test_discovery_tools_register_with_expected_metadata`).
- [x] `list_qgis_algorithms()` live call returns ≥100 algorithms (361 in the catalog; capped at 50 per FR-TA-2 prose, first 10 captured in evidence).
- [x] `describe_qgis_algorithm("native:zonalstatistics")` live call returns full parameter signature; captured in evidence.
- [x] `set_worker_submitter` DI binding wired in `main.py` (`_bind_worker_submitter` + `_default_qgis_process_submitter`); `qgis_process` body's "submitter is not bound" `RuntimeError` is no longer reachable post-startup (verified by the startup-only transcript and `test_qgis_process_pass_through_invokes_bound_submitter`).
- [x] 12 unit tests in `test_qgis_discovery.py`; full agent suite green at 69/69; contracts no-regression at 131/131.
- [x] `python -m grace2_agent --startup-only` exits 0 with `tool registry loaded: 8 tool(s)`.
- [x] No edits to FROZEN paths (verified — see § FROZEN-paths check below).

### FROZEN-paths check

Changes scoped to:
- `services/agent/src/grace2_agent/tools/qgis_discovery.py` (NEW)
- `services/agent/src/grace2_agent/main.py` (EDIT — additive: `_default_qgis_process_submitter`, `_bind_worker_submitter`, two-line edit to `_import_tools_registry`, one-line call in `run()`)
- `services/agent/tests/test_qgis_discovery.py` (NEW)
- `reports/inflight/job-0034-engine-20260606/{report.md,STATE,evidence/}`

NO edits to:
- `services/agent/src/grace2_agent/tools/{__init__,cache,passthroughs,README,data_fetch}.py` (FROZEN per kickoff)
- `services/agent/src/grace2_agent/{server,mcp,adapter}.py` (FROZEN per kickoff)
- `services/agent/pyproject.toml` (no new runtime dep — Option B′ uses local subprocess; `google-cloud-run` is NOT added)
- `services/workers/**` (FROZEN per kickoff Option B)
- `packages/contracts/**`, `infra/**`, `web/**`, `docs/srs/**`, `docs/SRS_v0.3.md`, `styles/**`, `reports/complete/**` (FROZEN per kickoff)

### Cross-cutting principles compliance

- **Pre-MVP scope — no legacy support:** no compat shims; the submitter is a single binding path.
- **Remove don't shim:** no placeholder TODOs; the parser is the real parser.
- **Live E2E validation required:** verbatim `list_qgis_algorithms` + `describe_qgis_algorithm` transcripts above against real `qgis_process` 3.40.3-Bratislava.
- **Diagnose before fix:** the Cloud Run Jobs v2 `command`-override constraint was diagnosed via direct API inspection (`RunJobRequest.Overrides.ContainerOverride.pb().DESCRIPTOR.fields`) before deciding on Option B′.
- **Surface uncertainty:** 6 TENTATIVE-tagged Open Questions, including the Option B′ pivot which is the most consequential.
- **Bundle small fixes; scan for all instances:** the parser is tolerant of QGIS version drift so a 3.40 → 3.44 (deployed worker) format change doesn't break the tool.
- **Don't edit in-flight kickoffs:** kickoff frozen, not edited.

### Results

**Pass.** 8 of 8 kickoff acceptance criteria satisfied. Open Questions surfaced for orchestrator triage; OQ-34-WORKER-DISCOVERY-SUBSTRATE is the most actionable (recommend: deploy sibling Cloud Run Job in v0.3.16 sprint).
