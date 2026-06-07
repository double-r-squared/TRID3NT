# Audit: Agent tool registry + cache shim + registry pass-throughs (M4 substrate)

**Job ID:** job-0032-agent-20260606, **Sprint:** sprint-06, **Auditor:** Development Orchestrator, **Status:** assigned

## Task Assignment

**Specialist:** agent

**Prerequisites:**
- **job-0030-schema-20260606 (APPROVED — required)** — provides `AtomicToolMetadata` in `grace2_contracts.tool_registry` (4-class TTL `Literal`, `source_class`, `cacheable`, `model_validator` cross-field rule) and the extended `PipelineStepSummary` fields. **Read `reports/complete/job-0030-schema-20260606/report.md` end-to-end before starting** so you absorb the model shape, the placement decision (`tool_registry.py`), and the open-set `error_code` discipline.
- **job-0031-infra-20260606 (APPROVED — required)** — provides the live `gs://grace-2-hazard-prod-cache/` bucket with 4 lifecycle rules + bucket-scoped objectAdmin for `agent-runtime@grace-2-hazard-prod` SA. **Read `reports/complete/job-0031-infra-20260606/report.md` end-to-end before starting** so you absorb the actual bucket layout (`cache/<ttl-class>/<source-class>/<hash>.<ext>` — note the TTL-class prefix nesting, NOT the FR-DC-1 literal prose; OQ-INFRA-31-FR-DC-1 is the schema-pushback) and the `customTime` write pattern (FR-DC-3).
- job-0015-agent-20260605 (M1 agent service — `services/agent/grace2_agent/` runs Gemini 2.5-pro on Vertex AI; `GraceWs` WebSocket server; MCP stdio sidecar). This job extends `services/agent/grace2_agent/tools/` with a registry skeleton; the existing agent service consumes it.
- v0.3.15 SRS amendment at commit `e435d8a` (Decision O + §3.9 FR-DC-1..6 + FR-CE-8 + new FR-TA-2 atomic-tool utilities subsection).

**SRS references** (load narrow files only per cost-discipline rule):
- `docs/srs/03-functional-requirements.md` — FR-AS-3 (ADK FunctionTool registration discipline), FR-TA-2 (atomic tools surface), §3.9 FR-DC-1..6 (caching architecture), FR-CE-8 (atomic-tool routing through the cache shim)
- `docs/srs/02-system-overview.md` — Decision O (cache-mediated data fetching), Decision G (two-layer tool architecture)
- DO NOT load `docs/SRS_v0.3.md` (regenerated 3058-line monolith)

### Environment
Linux Debian dev host. `services/agent/grace2_agent/` is the agent service (Cloud Run + WebSocket per OQ-1 resolution). Existing structure: `grace2_agent/ws.py`, `grace2_agent/main.py`, `grace2_agent/mcp.py`, etc. This job adds `grace2_agent/tools/` (new package). `grace2-contracts` v0.1.1+ is installed editable in the test venv at `.venv-agent/` and provides `AtomicToolMetadata`. The live cache bucket from job-0031 is reachable via `agent-runtime` SA in deployed contexts; for local dev, the test venv reaches it via ADC.

### Scope

1. **`services/agent/grace2_agent/tools/__init__.py`** (NEW) — tool registry skeleton. Define:
   - `register_tool(metadata: AtomicToolMetadata)` decorator that:
     - Validates the `AtomicToolMetadata` (pydantic auto-validates on construction; this just wraps the registration)
     - Stores the decorated function + metadata in a module-level `TOOL_REGISTRY: dict[str, RegisteredTool]` keyed by `metadata.name`
     - Returns the original function unchanged (so it's still callable directly in tests)
     - Raises a clear error if a tool with the same name is already registered (fail-fast at import per FR-CE-8)
   - `get_registered_tools() → list[RegisteredTool]` — for the agent service's startup-time tool registration with ADK
   - `RegisteredTool` (small dataclass or pydantic model): `metadata: AtomicToolMetadata` + `fn: Callable` + `module: str` (for diagnostics)

2. **`services/agent/grace2_agent/tools/cache.py`** (NEW) — the FR-DC-3 cache shim. Implementation responsibilities:
   - `compute_cache_key(source_id: str, params: dict, ttl_class: str) → str` — content-addressed key per FR-DC-3:
     - `canonicalized_params`: sort dict keys, omit None/default values, round bbox floats to source-native resolution if a `bbox_resolution` hint is passed, quantize date ranges to the TTL bucket boundary
     - `ttl_bucket_vintage`: current TTL-class window boundary string (e.g. `2026-06-07T03:00:00Z` for `dynamic-1h`, `2026-W23` for `semi-static-7d`, `2026-06` for `static-30d`)
     - Final key: `sha256(source_id + canonical_params_json + ttl_bucket_vintage).hexdigest()[:32]` (32-hex-char truncation per FR-DC-3 "stable hex prefix")
   - `cache_path(source_class: str, ttl_class: str, key: str, ext: str) → str` — produces `cache/<ttl-class>/<source-class>/<key>.<ext>` per the job-0031 bucket layout (NOT the FR-DC-1 literal; we follow the live substrate)
   - `read_through(metadata: AtomicToolMetadata, params: dict, ext: str, fetch_fn: Callable[[], bytes]) → tuple[str, bytes]`:
     1. Compute cache key + cache path
     2. Look up `gs://grace-2-hazard-prod-cache/<cache_path>` (use `google-cloud-storage` Python client with ADC)
     3. If present + not past `expires_at` per the bucket's lifecycle (since lifecycle handles eviction, presence = valid for `static-30d`/`semi-static-7d`/`dynamic-1h`; for `live-no-cache` always treat as miss): return the GCS URI + bytes
     4. On miss: invoke `fetch_fn()`, write to GCS with `customTime = datetime.now(UTC).isoformat()` per FR-DC-3 / job-0031's verified pattern, attach `Cache-Control` metadata reflecting the TTL class, return the GCS URI + bytes
     5. On `fetch_fn` failure: do NOT write a sentinel; re-raise so the agent surface (FR-AS-11) can decide
   - `is_cacheable(metadata: AtomicToolMetadata) → bool` — wraps the FR-DC-6 enumeration check (`metadata.cacheable and metadata.ttl_class != "live-no-cache"`)
   - **Deduplication (FR-DC-4):** the content-addressed key guarantees two callers asking for the same input produce the same cache path; no explicit lock needed (last-writer-wins on simultaneous misses produces byte-identical artifacts because the key already factored everything that would differ). Document this in a docstring.

3. **`services/agent/grace2_agent/tools/passthroughs.py`** (NEW) — register two registry-pass-through atomic tools:
   - `mongo_query(collection: str, filter: dict, projection: dict | None = None) → list[dict]` — wraps the existing MongoDB MCP path from job-0015 (`grace2_agent/mcp.py`). `AtomicToolMetadata(name="mongo_query", ttl_class="live-no-cache", cacheable=False, source_class="mongodb")` — Atlas writes/reads are uncacheable per FR-DC-6 (durable knowledge layer per Decision F).
   - `qgis_process(algorithm: str, params: dict) → dict` — wraps the existing PyQGIS worker invocation path (Cloud Run Job submission). `AtomicToolMetadata(name="qgis_process", ttl_class="live-no-cache", cacheable=False, source_class="qgis_process")` — solver dispatch is uncacheable per FR-DC-6 (solver outputs persist under `gs://<bucket>/runs/<run_id>/` per FR-CE-4).

4. **Wire registry into agent service startup.** Edit `services/agent/grace2_agent/main.py` (or wherever the ADK app is initialized) to import the `tools` package on startup. The import-time `@register_tool` decorators populate `TOOL_REGISTRY`; on startup, iterate `get_registered_tools()` and register each with the ADK `Agent` instance. Surfacing import errors at startup (FR-CE-8 fail-fast).

5. **Unit tests** in `services/agent/tests/test_tools_registry.py` + `services/agent/tests/test_tools_cache.py`:
   - At minimum 6 unit tests covering: register-decorator happy path; duplicate-name rejection; cache-key determinism (same inputs → same key); cache-key separation (different `ttl_bucket_vintage` → different key); `is_cacheable` for each of the 4 TTL classes; `cache_path` shape matches `cache/<ttl-class>/<source-class>/<hash>.<ext>`
   - Plus at least 2 integration tests using `google-cloud-storage` fake/stub (NOT live GCS — those land in job-0036): a read-through hit on a pre-seeded GCS fake; a write-on-miss writing with `customTime` set

6. **Documentation.** A short `services/agent/grace2_agent/tools/README.md` describing the registry pattern + cache shim API for future atomic-tool authors (job-0033, job-0034). One-paragraph overview + 2-3 short code examples (how to register a tool with `static-30d` class; how to use `read_through` from a fetcher; how `live-no-cache` tools skip the shim).

### File ownership (exclusive)

- `services/agent/grace2_agent/tools/__init__.py` (NEW package init + registry)
- `services/agent/grace2_agent/tools/cache.py` (NEW — shim)
- `services/agent/grace2_agent/tools/passthroughs.py` (NEW — registry pass-throughs)
- `services/agent/grace2_agent/tools/README.md` (NEW)
- `services/agent/grace2_agent/main.py` — startup-side ADK tool registration ONLY; do not refactor unrelated startup code
- `services/agent/tests/test_tools_registry.py` (NEW)
- `services/agent/tests/test_tools_cache.py` (NEW)
- `services/agent/pyproject.toml` — add `google-cloud-storage` runtime dep if not present
- `reports/inflight/job-0032-agent-20260606/` — kickoff frozen; report + evidence land here

### FROZEN — no edits in this job

- `services/agent/grace2_agent/ws.py` (M1 WebSocket — owned by job-0015; cancel chain is the reuse target, not the edit target)
- `services/agent/grace2_agent/mcp.py` (M1 MongoDB MCP path — `mongo_query` consumes it; do not refactor it)
- `services/workers/**` (engine-owned; the `qgis_process` pass-through invokes via the existing Cloud Run Jobs path job-0021 established — do not modify the worker side)
- `packages/contracts/**` (schema-owned; `AtomicToolMetadata` is imported from `grace2_contracts.tool_registry`, NOT redefined here)
- `infra/**` (job-0031 owns the cache bucket; this job consumes it via ADC)
- `web/**`, `docs/srs/**`, `docs/SRS_v0.3.md`, `styles/**`, `reports/complete/**`
- The agent service's existing tool wiring for M1 (`agent-message-chunk` streaming, `session-resume` handling) — do not refactor

### Cross-cutting principles in force

- **Invariant 1 (Determinism boundary):** preserves. Cache-key derivation is pure-function deterministic; no LLM in the cache path.
- **Invariant 8 (Cancellation is first-class):** preserves. `read_through` is a blocking I/O call; long fetches should be cancellable via the existing M1 cancel chain (the agent's WebSocket cancel path interrupts the running tool call). DO NOT introduce a separate cancellation mechanism — reuse the existing one.
- **FR-CE-8 fail-fast registration discipline:** every atomic tool that hits an external API REGISTERS its `AtomicToolMetadata`; misconfigured tools (e.g. cacheable=True + ttl_class="live-no-cache") fail at import time per the model_validator job-0030 landed.
- **FR-DC-6 enumeration honored:** the two pass-through tools (`mongo_query`, `qgis_process`) declare `cacheable=False` + `ttl_class="live-no-cache"` per the FR-DC-6 enumeration (MongoDB MCP writes + solver dispatchers are explicitly uncacheable-by-construction).
- **Diagnose before fix:** if a cache write fails (IAM, network), capture the GCS error before changing the shim logic.
- **Bundle small fixes:** if `services/agent/grace2_agent/main.py` has any drift between declared startup imports and actual ADK initialization discovered while editing, fix it here.
- **Remove don't shim:** if M1 has a placeholder tool-registration stub, replace it; do not wrap.

### Acceptance criteria (reviewer re-runs)

- [ ] `services/agent/grace2_agent/tools/` package exists with `__init__.py` (registry) + `cache.py` (shim) + `passthroughs.py` (mongo_query + qgis_process) + `README.md`.
- [ ] `@register_tool(metadata)` decorator: validates metadata, populates `TOOL_REGISTRY` keyed by name, fails fast on duplicate names.
- [ ] Cache-key derivation is deterministic across runs (`compute_cache_key(...) == compute_cache_key(...)` for same inputs).
- [ ] Cache-key separates correctly across TTL-bucket vintages (`dynamic-1h` keys for the SAME params 90 minutes apart produce DIFFERENT keys; `static-30d` keys 5 days apart produce the SAME key).
- [ ] `cache_path` produces `cache/<ttl-class>/<source-class>/<hash>.<ext>` matching the job-0031 bucket layout (NOT the FR-DC-1 literal prose).
- [ ] `read_through` write-on-miss sets `customTime = fetched_at` per FR-DC-3 / job-0031 verified pattern.
- [ ] `mongo_query` + `qgis_process` register cleanly with `cacheable=False` + `ttl_class="live-no-cache"`; the registry validates them.
- [ ] Agent service startup imports `tools` package and registers with ADK; verified via a startup-log capture (deploy to a local dev container or capture `python -m grace2_agent --startup-only` output if such a flag exists, OR a unit test that loads the package and asserts `TOOL_REGISTRY` is populated with at least the 2 pass-throughs).
- [ ] At least 6 unit tests + 2 integration tests; full agent-service test suite green.
- [ ] No edits to FROZEN paths.
- [ ] `services/agent/pyproject.toml` includes `google-cloud-storage` runtime dep (commit the lockfile too if `uv.lock` or similar is in tree).

Surface contestable choices as Open Questions with TENTATIVE tags — at minimum: cache key truncation length (32 hex chars is the kickoff value — TENTATIVE; longer reduces collision probability), TTL-bucket-vintage canonicalization for `live-no-cache` (currently treated as always-miss; alternative: use `fetched_at` itself which makes the key unique per call), how to handle `Cache-Control` header attachment (object metadata vs GCS bucket-level — pick object metadata for per-object visibility), whether `read_through` should expose an `force_refresh` parameter for the FR-DC-6 `cache=false` override case (TENTATIVE: yes; document in README).
