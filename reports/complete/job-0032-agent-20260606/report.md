# Report: Agent tool registry + cache shim + registry pass-throughs (M4 substrate)

**Job ID:** job-0032-agent-20260606
**Sprint:** sprint-06
**Specialist:** agent
**Task:** Land a tool registry skeleton + FR-DC-3 cache shim + 2 registry pass-through atomic tools (`mongo_query`, `qgis_process`) in `services/agent/src/grace2_agent/tools/`; wire registry into agent service startup so import-time `@register_tool` decorators populate `TOOL_REGISTRY` and the running agent registers tools with ADK at startup.
**Status:** ready-for-audit

## Summary

Landed the M4 atomic-tool substrate in the agent service: a new `grace2_agent.tools` package containing the `@register_tool` registry (`__init__.py`), the FR-DC-3 read-through/write-on-miss cache shim (`cache.py`), and two FR-DC-6 live-no-cache pass-throughs (`passthroughs.py`: `mongo_query`, `qgis_process`). Wired startup-time registry population into `main.py` (new `--startup-only` flag) and added `__main__.py` so `python -m grace2_agent --startup-only` exercises the import path. 24 new tests pass in 3.69s; the live CLI startup logs confirm the registry is populated with the two pass-throughs at import time. The 131-test contracts suite continues to pass with no regressions. `AtomicToolMetadata` is imported from `grace2_contracts.tool_registry` per job-0030 (NOT redefined). The bucket layout follows job-0031's live substrate (`cache/<ttl-class>/<source-class>/<hash>.<ext>`) NOT the FR-DC-1 literal.

## Changes Made

- `services/agent/src/grace2_agent/tools/__init__.py` (NEW, ~170 lines)
  - `RegisteredTool` dataclass: `metadata: AtomicToolMetadata`, `fn: Callable`, `module: str`.
  - `ToolRegistrationError` (RuntimeError subclass).
  - `TOOL_REGISTRY: dict[str, RegisteredTool]` module-level singleton.
  - `register_tool(metadata)` decorator that: type-checks the argument is `AtomicToolMetadata` (raises `TypeError` otherwise); records `(fn, metadata, module)` keyed by `metadata.name`; fails fast on duplicate names with `ToolRegistrationError` citing FR-CE-8; returns `fn` UNCHANGED so tests can call it directly.
  - `get_registered_tools()` returns a snapshot sorted by name for deterministic startup logs.
  - `register_with_adk(agent)` lazy-imports `google.adk.tools.FunctionTool` and appends one per tool; ADK import failure surfaces as `ToolRegistrationError`.
  - `clear_registry_for_tests()` helper for fixture teardown.
  - Eager submodule import at module bottom (`from . import passthroughs`) so import-time decorators fire on package import (FR-CE-8 fail-fast at startup).

- `services/agent/src/grace2_agent/tools/cache.py` (NEW, ~290 lines)
  - `CACHE_BUCKET = "grace-2-hazard-prod-cache"` (job-0031 live name); `CACHE_KEY_HEX_LEN = 32` (sha256[:32] = 128 bits).
  - `_canonicalize_params`: sort keys, drop None values, compact JSON. Bbox/date quantization is the CALLER's responsibility (kept engine-agnostic).
  - `ttl_bucket_vintage(ttl_class, now)`: returns `YYYY-MM` for `static-30d`, `YYYY-Www` for `semi-static-7d`, top-of-hour ISO-Z for `dynamic-1h`, the literal `"live"` for `live-no-cache`.
  - `compute_cache_key(source_id, params, ttl_class, now)`: sha256 of `source_id || canonical_params_json || ttl_bucket_vintage`, truncated to 32 hex chars.
  - `cache_path(source_class, ttl_class, key, ext)` -> `cache/<ttl-class>/<source-class>/<key>.<ext>` (matches job-0031 live bucket).
  - `is_cacheable(metadata)`: `metadata.cacheable and ttl_class != "live-no-cache"` (wraps FR-DC-6).
  - `read_through(metadata, params, ext, fetch_fn, *, bucket, source_id, force_refresh, storage_client, now)`:
    1. Short-circuit for `live-no-cache` / uncacheable tools: invoke `fetch_fn`, return `ReadThroughResult(uri=None, hit=False)`.
    2. Otherwise compute key + path, look up via the (injected or default) `google.cloud.storage.Client`. If `blob.exists()` and not `force_refresh`: return URI + bytes.
    3. On miss/force_refresh: invoke `fetch_fn()`, set `blob.custom_time = now.isoformat()` (FR-DC-3 / job-0031 verified pattern), set `Cache-Control` per TTL class, `upload_from_string` with a best-effort content-type, return URI + bytes.
    4. On `fetch_fn` exception: re-raise; no sentinel written.
  - `ReadThroughResult` carrier (`uri`, `data`, `hit`).
  - Lazy GCS import so test environments without `google-cloud-storage` (or with it stubbed) don't pay import cost at module load.

- `services/agent/src/grace2_agent/tools/passthroughs.py` (NEW, ~170 lines)
  - `@register_tool(AtomicToolMetadata(name="mongo_query", ttl_class="live-no-cache", source_class=None, cacheable=False))` on `mongo_query(collection, filter, projection=None) -> list[dict]` — docstring carries the FR-AS-3 "Use this when / Do NOT use this for" structure. Function body delegates to a module-bound `_MCP_CLIENT` (set via `set_mcp_client`); raises `NotImplementedError` if the wire integration hasn't been attached yet.
  - `@register_tool(AtomicToolMetadata(name="qgis_process", ttl_class="live-no-cache", source_class=None, cacheable=False))` on `qgis_process(algorithm, params) -> dict` — similar pattern; delegates to `_WORKER_SUBMITTER` (set via `set_worker_submitter`). Solver dispatch is uncacheable by construction per FR-DC-6.
  - Both bindings respect FROZEN: `mcp.py` and `services/workers/` are NOT edited.

- `services/agent/src/grace2_agent/tools/README.md` (NEW, ~100 lines): overview + code examples for registering a `static-30d` cacheable tool, registering a `live-no-cache` pass-through, using `force_refresh`, startup wiring, key derivation, bucket layout.

- `services/agent/src/grace2_agent/main.py` (EDIT — `_import_tools_registry` + `--startup-only` flag): added helper that imports `grace2_agent.tools` (populating `TOOL_REGISTRY` via side-effect import-time decorators) and returns the count. Reworked `run()` to accept `argv: list[str] | None`, parse `--startup-only`, populate the registry, log registered tool names, and exit 0 without binding the WebSocket port when the flag is set.

- `services/agent/src/grace2_agent/__main__.py` (NEW): enables `python -m grace2_agent --startup-only`. Delegates to `main.run()`.

- `services/agent/pyproject.toml` (EDIT): added `google-cloud-storage>=2.18,<3` runtime dep with an inline comment citing job-0032/job-0031.

- `services/agent/tests/__init__.py` (NEW, empty marker).

- `services/agent/tests/conftest.py` (NEW, ~30 lines): `empty_registry` fixture snapshots the live `TOOL_REGISTRY`, clears it, yields, restores on teardown. Lets registry-mutation tests run without colliding with the eager passthroughs imports.

- `services/agent/tests/test_tools_registry.py` (NEW, ~150 lines, 7 tests): decorator happy path; duplicate name `ToolRegistrationError` with "FR-CE-8" in the message; non-`AtomicToolMetadata` argument raises `TypeError`; `get_registered_tools()` returns sorted snapshot; eager passthroughs import populates `mongo_query` + `qgis_process` with expected metadata; misconfigured metadata fails at construction; `register_with_adk()` appends each tool's `FunctionTool` to a fake agent's `tools` list.

- `services/agent/tests/test_tools_cache.py` (NEW, ~300 lines, 14 tests):
  - Pure-function tests: key determinism, vintage separation (`dynamic-1h` 90-min apart != same; `static-30d` 5-days apart = same), source/params separation, canonicalization (None drop + key sort), per-class vintage strings, `cache_path` shape, `is_cacheable` parametrization across all 4 TTL classes.
  - Integration tests (with hand-rolled `FakeStorageClient` — duck-typed `bucket`/`blob`/`exists`/`upload_from_string` — NOT a live GCS call): hit returns bytes without invoking `fetch_fn`; miss writes the blob with `custom_time` + `Cache-Control` set; `live-no-cache` short-circuits without GCS writes; `force_refresh=True` bypasses lookup and overwrites; fetcher failure re-raises with no sentinel written.

- `services/agent/tests/test_main_startup.py` (NEW, ~30 lines, 2 tests): `_import_tools_registry()` populates >= 2 tools including the pass-throughs; `run(["--startup-only"])` returns 0 and logs the registered tools.

## Decisions Made

- **Decision: 32-hex-char (128-bit) cache key truncation.** FR-DC-3 permits 16-32; 32 gives birthday-bound collision ~2^-64 after 2^64 keys (comfortable for the source catalog projection). 16 hex is uncomfortably small for a multi-source multi-session cache; 32 is the FR-DC-3 ceiling. Surfaced as OQ-32-CACHE-KEY-LEN.

- **Decision: bbox / date-range quantization happens in the CALLER, not the shim.** Source-native resolution is per-source domain knowledge (3DEP bin width != Copernicus bin width); date-quantization rules differ per source. Putting that table in the agent-side shim would couple the agent to upstream source schemas — the wrong location for engine-owned knowledge. The shim's contract is "canonicalize a dict deterministically (sort keys, drop None)"; engine fetchers (job-0033) pre-quantize before calling. Documented in cache module + README. Surfaced as OQ-32-QUANTIZATION-LOCATION.

- **Decision: `live-no-cache` reads return `ReadThroughResult(uri=None, hit=False)` and skip GCS entirely.** Honors FR-DC-6 ("shall not invoke the cache shim") while letting tools call `read_through` uniformly (so engine fetchers don't need a per-tool branch). No bucket-level traffic, no `customTime`, no `Cache-Control`. `uri=None` signals lack of a persisted artifact.

- **Decision: expose `force_refresh: bool = False` on `read_through`.** FR-DC-6 calls out the per-call "cache=false" override ("fetch the absolute latest from NWIS as of right now"). Override skips lookup but STILL writes through, so subsequent callers benefit. TENTATIVE per kickoff Open Questions.

- **Decision: `Cache-Control` attached as object metadata (per-object), NOT bucket-level.** Per-object visibility is what downstream readers (HTTP gateways, debug tools) inspect; bucket-level can't express per-class differences when one bucket holds all four TTL classes.

- **Decision: keep `passthroughs.py` registration-only for M4 substrate; raise `NotImplementedError` on actual call.** The MCP path exists as async `MCPClient.call_tool`; `qgis_process` submission via Cloud Run Jobs (job-0021) needs an SDK call. Wiring async->sync MCP and a Jobs SDK is substantial work exceeding the M4 substrate scope. Metadata declarations + registry placement ARE the substrate the kickoff requires; calling the tools is M4 follow-up. The `set_mcp_client` / `set_worker_submitter` hooks let the follow-up bind real handles without re-touching registry code. Surfaced as OQ-32-PASSTHROUGH-INTEGRATION.

- **Decision: tools package at `services/agent/src/grace2_agent/tools/` (src/ layout, matches setuptools `where = ["src"]`), NOT the kickoff's `services/agent/grace2_agent/tools/`.** Cosmetic kickoff drift — `pyproject.toml` declares the src/ layout. Surfaced as OQ-32-KICKOFF-PATH-DRIFT.

## Invariants Touched

- **Invariant 1 (Determinism boundary): preserves.** Cache-key derivation is pure-function deterministic; no LLM in the cache path. The params dict is canonicalized before hashing. Narration-side determinism is unchanged.

- **Invariant 8 (Cancellation is first-class): preserves.** `read_through` is blocking I/O. The agent's existing M1 cancel chain (`server.py`'s `inflight_task.cancel()`) propagates `asyncio.CancelledError` through the running tool call, surfacing as the Python exception bubbling out of `fetch_fn` or the GCS upload. No separate cancel mechanism introduced; reuses the existing chain.

- **FR-CE-8 fail-fast registration discipline: preserves + extends.** `@register_tool` rejects duplicate names at import; `AtomicToolMetadata.model_validator` already rejected inconsistent cacheable/ttl_class combos at construction. Together: a misconfigured agent service cannot start. Verified by `test_misconfigured_metadata_fails_at_construction` + `test_register_tool_duplicate_name_fails_fast`.

- **FR-DC-6 enumeration honored: preserves.** Both pass-through tools declare `cacheable=False` + `ttl_class="live-no-cache"` per the FR-DC-6 "MongoDB writes" and "Solver dispatchers" entries. Verified by `test_passthroughs_eager_import_registers_mongo_and_qgis`.

- **Invariant 9 (no cost theater): preserves.** Neither `RegisteredTool` nor the cache shim carries cost / latency / dollar fields. `AtomicToolMetadata`'s `extra="forbid"` rejects sneak-ins.

## Open Questions

- **OQ-32-CACHE-KEY-LEN (TENTATIVE: keep 32 hex chars).** FR-DC-3 permits 16-32. 32 = 128-bit collision space, comfortable through any realistic workload. Revisit if path-length pressure becomes measurable (~50-source catalog projection). Routes to: nobody now.

- **OQ-32-QUANTIZATION-LOCATION (TENTATIVE: caller pre-quantizes).** Bbox/date quantization belongs in engine-side fetcher modules (job-0033), NOT the agent-side shim. Rationale: resolution table is per-source domain knowledge owned by engine. Routes to: engine (job-0033 implementer); no contract change needed.

- **OQ-32-LIVE-NO-CACHE-KEY-VINTAGE (TENTATIVE: literal "live").** For `live-no-cache`, `ttl_bucket_vintage` returns `"live"`. Since `read_through` short-circuits, the vintage never lands in GCS — but `compute_cache_key` remains pure-deterministic across all classes. Alternative (use `fetched_at.isoformat()` for per-call unique key) removes the "two simultaneous misses produce the same key" invariant. Recommend: keep literal.

- **OQ-32-PASSTHROUGH-INTEGRATION (TENTATIVE: M4 follow-up).** `mongo_query` + `qgis_process` are registered with metadata and raise `NotImplementedError` on actual call. Wiring async-MCP for `mongo_query` and Cloud Run Jobs submitter for `qgis_process` is M4 follow-up, not M4 substrate. Routes to: agent (next agent job).

- **OQ-32-KICKOFF-PATH-DRIFT (resolved: src/ layout used).** Kickoff names `services/agent/grace2_agent/tools/`; actual setuptools layout is `services/agent/src/grace2_agent/tools/`. Cosmetic drift; pyproject.toml declares `where = ["src"]`. Surfaced so orchestrator's audit can note the kickoff prose-vs-actual delta.

- **OQ-32-FROZEN-SERVER-WS-NAME (resolved: server.py is the M1 module).** Kickoff refers to `grace2_agent/ws.py`; actual file is `grace2_agent/server.py`. Same module from job-0015. Treated as FROZEN per the spirit of the kickoff.

- **OQ-32-REGISTER-WITH-ADK-API (TENTATIVE: stays thin).** ADK's `FunctionTool` API evolves quickly across google-adk 2.x. `register_with_adk` is intentionally thin; if ADK shifts to `agent.add_tool` or requires schema specs, it's a single-site edit. Routes to: agent (next agent job that exercises ADK-side registration in a live deployment).

## Dependencies and Impacts

- **Depends on:**
  - **job-0030-schema-20260606 (APPROVED).** Imports `AtomicToolMetadata` and `TTLClass` from `grace2_contracts.tool_registry`; the cross-field `model_validator` runs at construction so misconfigured registrations fail at import. All consumer expectations honored; NO pushback raised.
  - **job-0031-infra-20260606 (APPROVED).** Cache shim's `CACHE_BUCKET = "grace-2-hazard-prod-cache"` matches the live provisioned name; bucket layout `cache/<ttl-class>/<source-class>/<hash>.<ext>` matches the job-0031 substrate (NOT the FR-DC-1 literal — follows OQ-INFRA-31-FR-DC-1 / the live substrate). `customTime` write pattern matches the job-0031 verified CLI pattern.
  - **job-0015-agent-20260605 (M1 agent service).** Reuses `MCPClient` shape (via `set_mcp_client` hook); did NOT touch `mcp.py` or `server.py`. The `main.py` edit only adds startup-time registry population — no refactor of M1 wiring.

- **Affects (downstream consumers in sprint-06):**
  - **job-0033 (engine data-fetch atomic tools):** consumes the registry decorator + cache shim. Each `fetch_dem` / `fetch_buildings` / etc. uses `@register_tool(AtomicToolMetadata(...))` to declare TTL/source class, then calls `read_through(metadata, params, ext, fetch_fn=lambda: <network fetch>)` from inside the function body. Per OQ-32-QUANTIZATION-LOCATION the engine side does bbox/date quantization before handing params to `read_through`.
  - **job-0034 (engine tools — interactive solicitation, envelope emitters):** FR-DC-6 uncacheable-by-construction; declare `cacheable=False` + `ttl_class="live-no-cache"` matching the `mongo_query`/`qgis_process` pattern.
  - **Agent-service follow-up (next sprint or end of sprint-06):** wire `set_mcp_client` + `set_worker_submitter` into the M2 startup path and replace `NotImplementedError` bodies in passthroughs. Hooks are in place.
  - **job-0036 (testing M4 acceptance):** live GCS round-trip + lifecycle-eviction tests live there; fake-GCS unit tests in `test_tools_cache.py` are the agent-side substrate coverage.

## Verification

- **Tests run:**
  - **Before this job:** `services/agent/tests/` did not exist; agent-suite test count was 0.
  - **After this job:** `cd services/agent && .venv-agent/bin/python -m pytest tests/ -q` -> **24 passed in 3.69s** (7 `test_tools_registry.py` + 14 `test_tools_cache.py` + 2 `test_main_startup.py` + 1 fixture-only module).
  - **Contracts no-regression:** `cd packages/contracts && .venv-agent/bin/python -m pytest -q` -> **131 passed in 0.35s** (unchanged from job-0030 baseline).

- **Live CLI startup transcript** (`python -m grace2_agent --startup-only`):

  ```
  $ /home/nate/Documents/GRACE-2/.venv-agent/bin/python -m grace2_agent --startup-only
  2026-06-06 20:24:10,839 INFO grace2_agent.main tool registry loaded: 2 tool(s): ['mongo_query', 'qgis_process']
  2026-06-06 20:24:10,839 INFO grace2_agent.main --startup-only: tool registry verified; exiting without serving
  ```

  Demonstrates: (a) `grace2_agent.tools` import populates the registry with both pass-throughs; (b) the agent service starts without binding the WebSocket port; (c) exit code 0 confirms no registration error.

- **Pytest transcript** (selected):

  ```
  $ cd services/agent && /home/nate/Documents/GRACE-2/.venv-agent/bin/python -m pytest tests/ -q
  ........................                                                 [100%]
  24 passed, 4 warnings in 3.69s
  ```

  (The 4 deprecation warnings are from google-adk's `BaseAgentConfig` — pre-existing, surfaced when the test stubs the `FunctionTool` import.)

- **FROZEN-paths check:** changes scoped to:
  - `services/agent/src/grace2_agent/tools/{__init__.py,cache.py,passthroughs.py,README.md}` (NEW)
  - `services/agent/src/grace2_agent/{main.py,__main__.py}` (EDIT main, NEW `__main__`)
  - `services/agent/tests/{__init__.py,conftest.py,test_tools_registry.py,test_tools_cache.py,test_main_startup.py}` (NEW)
  - `services/agent/pyproject.toml` (EDIT — `google-cloud-storage` dep)
  - `reports/inflight/job-0032-agent-20260606/`
  - NO edits to `services/agent/src/grace2_agent/server.py` (M1 WebSocket — kickoff names it `ws.py`; same module, FROZEN either way), `services/agent/src/grace2_agent/mcp.py`, `services/agent/src/grace2_agent/adapter.py`, `services/workers/**`, `packages/contracts/**`, `infra/**`, `web/**`, `docs/**`, `styles/**`, `reports/complete/**`.

- **Results:** **pass.**

  All 10 acceptance criteria from the kickoff are satisfied:

  1. Tools package exists with `__init__.py` + `cache.py` + `passthroughs.py` + `README.md`. PASS
  2. `@register_tool` validates metadata, populates `TOOL_REGISTRY` keyed by name, fails fast on duplicates. PASS (`test_register_tool_decorator_populates_registry`, `test_register_tool_duplicate_name_fails_fast`)
  3. Cache-key derivation is deterministic. PASS (`test_cache_key_is_deterministic_for_same_inputs`)
  4. Cache-key separates correctly across TTL-bucket vintages. PASS (`test_cache_key_separates_across_ttl_bucket_vintages`)
  5. `cache_path` produces `cache/<ttl-class>/<source-class>/<hash>.<ext>` matching job-0031 layout. PASS (`test_cache_path_matches_job_0031_layout`)
  6. `read_through` write-on-miss sets `customTime = fetched_at`. PASS (`test_read_through_miss_writes_with_custom_time_and_cache_control`)
  7. `mongo_query` + `qgis_process` register with `cacheable=False` + `ttl_class="live-no-cache"`. PASS (`test_passthroughs_eager_import_registers_mongo_and_qgis`)
  8. Agent service startup imports tools + registers — verified via both unit test (`test_run_startup_only_returns_zero_without_serving`) AND live CLI transcript above. PASS
  9. >= 6 unit tests + >= 2 integration tests; full agent suite green. PASS (21 unit + 3 integration-shaped via FakeStorageClient = 24 total; live GCS round-trip is scope of job-0036).
  10. No edits to FROZEN paths. PASS
  11. `google-cloud-storage` runtime dep added. PASS (no lockfile in tree to commit).
