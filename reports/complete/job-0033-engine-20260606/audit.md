# Audit: 4 data-fetch atomic tools (fetch_dem, fetch_buildings, fetch_population, geocode_location) + mongo_query DI binding

**Job ID:** job-0033-engine-20260606, **Sprint:** sprint-06, **Auditor:** Development Orchestrator, **Status:** approved

## Task Assignment

**Specialist:** engine

**Prerequisites:**
- **job-0030-schema-20260606 (APPROVED — required):** provides `AtomicToolMetadata` (4-class TTL `Literal`, `source_class`, `cacheable`, `model_validator`). Tools register with this model.
- **job-0031-infra-20260606 (APPROVED — required):** provides live `gs://grace-2-hazard-prod-cache/` with the 4 lifecycle rules. Cache writes land at `cache/<ttl-class>/<source-class>/<hash>.<ext>`.
- **job-0032-agent-20260606 (APPROVED — required):** provides `services/agent/src/grace2_agent/tools/{__init__.py,cache.py,passthroughs.py}` — the registry decorator, the FR-DC-3 cache shim (`read_through`), and the `mongo_query` / `qgis_process` pass-through stubs awaiting DI wiring. **Read `reports/complete/job-0032-agent-20260606/report.md` end-to-end** so you absorb the `set_mcp_client(client)` DI seam you must bind in this job.

**SRS references** (narrow files only):
- `docs/srs/03-functional-requirements.md` — FR-TA-2 (atomic tools surface), FR-AS-3 (registration discipline), §3.9 FR-DC-2/3/4/6 (caching semantics), FR-DT-1..6 (data tier separation), FR-CE-8.
- `docs/srs/02-system-overview.md` — Decision G (two-layer architecture), Decision K (default-by-fetch).

### Environment
Linux Debian dev host. Live cache bucket from job-0031. ADC via gcloud. The 4 fetch tools target external public APIs:
- **USGS 3DEP DEM** — use `py3dep` Python wrapper; returns COG to `cache/static-30d/dem/<hash>.tif`.
- **Microsoft Building Footprints** — FlatGeobuf bbox query via the MS Open Maps STAC catalog; returns FlatGeobuf to `cache/static-30d/buildings/<hash>.fgb`.
- **US Census ACS B01003_001E (total population, tract-level)** — REST query via `requests`; returns GeoJSON FeatureCollection to `cache/static-30d/population/<hash>.json`. Fallback: WorldPop COG via STAC if Census API is unavailable; document fallback choice as a Decision Made.
- **Nominatim** (OSM-hosted) — geocoding to bbox + canonical name. `dynamic-1h` per FR-DC-2 (active-state-ish). Honor User-Agent header per Nominatim usage policy.

### Scope

1. **`services/agent/src/grace2_agent/tools/data_fetch.py`** (NEW) — implement 4 atomic tools using `@register_tool(AtomicToolMetadata(...))`:
   - `fetch_dem(bbox: tuple[float, float, float, float], resolution_m: int = 10) → LayerURI` — `ttl_class="static-30d"`, `source_class="dem"`, `cacheable=True`. Quantize bbox to source-native resolution before cache-key derivation per OQ-32-QUANTIZATION-LOCATION (engine-side, not shim-side).
   - `fetch_buildings(bbox: tuple[float, float, float, float], source: str = "msft") → LayerURI` — `ttl_class="static-30d"`, `source_class="buildings"`.
   - `fetch_population(bbox: tuple[float, float, float, float], dataset: str = "acs_2022") → LayerURI` — `ttl_class="static-30d"`, `source_class="population"`. Decision: ACS vs WorldPop default — pick ACS (CONUS scope per Decision I); WorldPop as opt-in `dataset="worldpop"`.
   - `geocode_location(query: str) → GeocodedLocation` — `ttl_class="dynamic-1h"`, `source_class="geocode"`. Returns `{name, bbox, latitude, longitude, source}`. Emit a `location-resolved` side-effect message per FR-AS-7 (the kickoff for that pattern is FR-TA-2 §"Location-resolved emission").

2. **Each tool integrates with `read_through`** from `tools/cache.py` per FR-CE-8. Pattern:
   ```python
   def fetch_dem(bbox, resolution_m=10):
       metadata = TOOL_REGISTRY["fetch_dem"].metadata
       quantized_bbox = round_bbox_to_resolution(bbox, resolution_m)
       uri, content = read_through(
           metadata=metadata,
           params={"bbox": quantized_bbox, "resolution_m": resolution_m},
           ext="tif",
           fetch_fn=lambda: _fetch_3dep_dem(quantized_bbox, resolution_m),
       )
       return LayerURI(uri=uri, ...)
   ```

3. **Bind `set_mcp_client(client)` DI seam from job-0032's `passthroughs.py`.** The MCP client itself is created by the existing M1 agent startup (`grace2_agent/mcp.py`). Wire the binding in `main.py` startup (after the existing MCP init) so `mongo_query` body works end-to-end (not `NotImplementedError`).

4. **`LayerURI` / `GeocodedLocation` return types** — use existing `grace2-contracts` shapes where they exist. If `LayerURI` is not yet a pydantic model in `grace2-contracts`, surface as an OQ and use a simple dict for now; do NOT introduce a new pydantic model in this job (FROZEN packages/contracts).

5. **`location-resolved` emission for `geocode_location`** — emit through the existing M1 WebSocket emission seam (`server.py` has the path). Don't introduce a new emission mechanism. If the seam is awkward to access from inside a tool, surface as an OQ for job-0035 to address (job-0035 owns real envelope emission this sprint).

6. **Tests** in `services/agent/tests/test_data_fetch.py`: at least 6 unit tests (one happy-path per tool + bbox quantization determinism + cache integration with mocked GCS + mocked external API failures re-raise correctly).

7. **Live evidence** in `evidence/`: for at least one tool (recommend `fetch_dem` over a small Florida bbox), capture the GCS object listing after a successful fetch (`gcloud storage ls gs://grace-2-hazard-prod-cache/cache/static-30d/dem/`) and the same object's `customTime` (from `gcloud storage objects describe`). For `geocode_location`, capture a live call against Nominatim with the query `"Fort Myers, FL"` and the returned bbox + name.

### File ownership (exclusive)

- `services/agent/src/grace2_agent/tools/data_fetch.py` (NEW)
- `services/agent/src/grace2_agent/main.py` — ONLY the `set_mcp_client` DI binding line(s); do not refactor unrelated startup code
- `services/agent/pyproject.toml` — add `py3dep`, `requests` (likely already there from M1) runtime deps
- `services/agent/tests/test_data_fetch.py` (NEW)
- `reports/inflight/job-0033-engine-20260606/` — kickoff frozen, report + evidence here

### FROZEN — no edits in this job

- `services/agent/src/grace2_agent/tools/{__init__.py,cache.py,passthroughs.py,README.md}` (job-0032 — registry, shim, pass-through stubs)
- `services/agent/src/grace2_agent/server.py`, `services/agent/src/grace2_agent/mcp.py` (M1 + job-0032 — do not refactor)
- `services/agent/src/grace2_agent/tools/qgis_discovery.py` (job-0034 owns; do not touch even if you would otherwise want a "while we're at it" fix)
- `packages/contracts/**`, `services/workers/**`, `infra/**`, `web/**`, `docs/srs/**`, `docs/SRS_v0.3.md`, `styles/**`, `reports/complete/**`

### Cross-cutting principles in force

- **Invariant 1 (Determinism boundary):** preserves. Each fetch tool returns a deterministic URI + byte payload; no LLM in the data path.
- **Invariant 5 (Tier separation):** preserves. Fetched artifacts go to the cache bucket via the agent-runtime SA; no `gs://` URIs leak to the client (client only sees `LayerURI` references that resolve through QGIS Server).
- **FR-CE-8 fail-fast:** if registration metadata is wrong (`cacheable=True` + `live-no-cache`, or missing `source_class` when cacheable), the model_validator job-0030 landed will reject at import time.
- **FR-DC-6 honor:** all 4 tools are cacheable; not on the uncacheable enumeration.
- **Diagnose before fix:** if an external API call fails, capture the request/response before changing the tool logic. Re-raise per the cache shim's documented contract (FR-AS-11 surface decides).
- **Bundle small fixes:** if you discover the existing `mcp.py` exposes the MCP client at a slightly different name than your DI binding expects, fix the binding code (not `mcp.py`).
- **Remove don't shim:** if there are placeholder fetch-stub functions from M1 scaffolding, replace; do not wrap.

### Acceptance criteria (reviewer re-runs)

- [ ] `services/agent/src/grace2_agent/tools/data_fetch.py` registers 4 atomic tools via `@register_tool`; `TOOL_REGISTRY` now contains 6 tools total (2 pass-throughs + 4 fetchers) after `--startup-only` run.
- [ ] Each fetch tool routes through `read_through` from `tools/cache.py` per FR-CE-8 — verified by grep.
- [ ] `fetch_dem`, `fetch_buildings`, `fetch_population` declare `ttl_class="static-30d"`; `geocode_location` declares `ttl_class="dynamic-1h"`.
- [ ] `geocode_location("Fort Myers, FL")` live call returns bbox + canonical name; captured in evidence.
- [ ] `fetch_dem` over a small Florida bbox writes to `gs://grace-2-hazard-prod-cache/cache/static-30d/dem/<hash>.tif` with `customTime` set; verified via `gcloud storage objects describe` capture.
- [ ] `set_mcp_client` DI binding wired in `main.py`; `mongo_query` body no longer raises `NotImplementedError` (tested with a tiny mocked MCP client).
- [ ] At least 6 unit tests + the agent suite green; contracts suite still 131/131.
- [ ] `python -m grace2_agent --startup-only` exits 0 with `tool registry loaded: 6 tool(s)` log line.
- [ ] No edits to any FROZEN path listed above.

Surface contestable choices as Open Questions with TENTATIVE tags — at minimum: ACS vs WorldPop default for `fetch_population`; Nominatim vs Mapbox default for `geocode_location` (TENTATIVE: Nominatim — free; Mapbox needs an API key which is M5+); LayerURI shape (existing pydantic vs dict); bbox quantization rules per source (3DEP 10m vs MS Buildings native tile boundaries); error_code values to register for fetch failures (`UPSTREAM_API_ERROR`, `BBOX_INVALID`, etc.); whether to support per-call `cache=False` override (yes — pass through to `read_through(force_refresh=True)`).

## Assessment

**Verdict:** approved (with one critical follow-up — see below).

Four data-fetch atomic tools land in `services/agent/src/grace2_agent/tools/data_fetch.py` (~620 lines), each `@register_tool(AtomicToolMetadata(...))` and each routing through `read_through` from job-0032's cache shim per FR-CE-8. Per-source bbox quantization (3DEP at `resolution_m`, MS Buildings 10m, US Census 100m) ensures dedup (FR-DC-4) at the engine boundary, correctly off-loaded from the shim (OQ-32-QUANTIZATION-LOCATION resolution confirmed). `fetch_dem` produces COGs via `py3dep` + `rioxarray`; `fetch_buildings` pulls FlatGeobuf from MS Planetary Computer STAC; `fetch_population` queries US Census ACS B01003; `geocode_location` hits Nominatim with the required User-Agent header.

Tool registry now reports `tool registry loaded: 8 tool(s)` on `--startup-only` — confirms both job-0033 and job-0034 (running concurrently) registered cleanly without name collision. Tests: 22 new + 35 prior agent suite = **57/57 green** (then 69/69 after job-0035 lands its 11 emitter tests). Contracts still 131/131 (no regression).

Live evidence is solid: `gcloud storage ls gs://grace-2-hazard-prod-cache/cache/static-30d/dem/` shows the cached DEM object; `gcloud storage objects describe ...` evidence captures the `customTime` (which is where the critical follow-up surfaces — see below). `geocode_location("Fort Myers, FL")` returns the expected bbox + canonical name + Nominatim source attribution.

DI binding: `set_mcp_client` wired in `main.py._bind_mcp_client` helper; `mongo_query` body no longer raises `NotImplementedError`. Specialist correctly extended `main.py` only at the addition site, not refactoring unrelated startup code.

**CRITICAL FOLLOW-UP — OQ-33-CACHE-CUSTOMTIME-TYPE-BUG.** The specialist surfaced (and worked around) a real bug in job-0032's `cache.py:337–338`:

```python
fetched_at = (now or datetime.now(timezone.utc)).isoformat()  # str
blob.custom_time = fetched_at                                 # SDK wants datetime, NOT str
```

The real `google.cloud.storage` SDK rejects string assignment to `blob.custom_time`; only `FakeStorageClient.FakeBlob` (used in job-0032 unit tests) accepts anything. **Production cache writes are broken without this fix.** The live `fetch_dem` evidence run in job-0033 only succeeded because the specialist used a 1-time monkey-patch on `Blob.custom_time.setter` to work around it. The 2-line fix is straightforward (`fetched_at` becomes datetime; `.isoformat()` moves to the log line only) and lands as an orchestrator-direct hotfix in this audit's closeout (see Follow-up Actions §1).

The bug-discovery pattern is a quiet success of the live-evidence discipline — unit tests with stubbed GCS missed it; the live `gcloud storage describe` step caught it. Future cache-side tests should include a real (or higher-fidelity-fake) `google.cloud.storage` blob type-check.

## Invariant Check

- **Invariant 1 (Determinism boundary):** preserved. Each fetcher returns a deterministic URI + bytes; no LLM in the data path. Bbox quantization is pure-function.
- **Invariant 5 (Tier separation):** preserved. Fetched artifacts go to the cache bucket via the `agent-runtime` SA; no `gs://` URIs leak to the client. The `LayerURI` wrapper is the client-facing reference.
- **FR-CE-8 fail-fast registration:** verified at import — duplicate-name registration would fail; misconfigured metadata would fail at construction via job-0030's `model_validator`.
- **FR-DC-6 honor:** all 4 tools are cacheable (none on the uncacheable enumeration).

## Dependency Check

- **job-0030** — `AtomicToolMetadata` consumed correctly; 4-class TTL Literal used verbatim.
- **job-0031** — cache bucket layout consumed (`cache/<ttl-class>/<source-class>/<hash>.<ext>`); live evidence confirms writes land at the right prefix.
- **job-0032** — `read_through` API consumed correctly; cache key derivation off-loaded to the shim; per-source quantization correctly stays engine-side.
- **job-0015** — M1 MCP path consumed by `set_mcp_client` DI binding; `mongo_query` is now operational.

## Decisions Validated

All 6 decisions reviewed and accepted:

1. **ACS as `fetch_population` default; WorldPop deferred** — correct per Decision I CONUS scope + no-API-key + Fort Myers demo alignment.
2. **Nominatim as `geocode_location` default; Mapbox deferred** — usage-policy compliance via descriptive User-Agent (env-overridable); `dynamic-1h` TTL natural throttling; `limit=1` keeps Nominatim happy.
3. **`LayerURI` from contracts for the 3 layer-producing tools; `geocode_location` returns a plain dict** — `GeocodedLocation` model doesn't exist in `grace2_contracts` and `packages/contracts/**` is FROZEN; routed as OQ-33-GEOCODED-LOCATION-CONTRACT-PROMOTION for a later schema amendment.
4. **Per-source bbox quantization grids** (3DEP `resolution_m`, MS Buildings 10m, US Census 100m) — engine-side per OQ-32-QUANTIZATION-LOCATION; documented in `round_bbox_to_resolution`.
5. **Error codes registered**: `UPSTREAM_API_ERROR` (retryable) and `BBOX_INVALID` (not retryable). Job-0035 owns the full A.6 enumeration registry.
6. **Eager `data_fetch` import in `main._import_tools_registry`** (not `tools/__init__.py` which is FROZEN) — correct boundary preservation.

## Open Questions Resolved

Filed for triage (none blocks closure):

- **OQ-33-CACHE-CUSTOMTIME-TYPE-BUG** — see Follow-up §1. Critical; orchestrator-direct hotfix bundled here.
- **OQ-33-GEOCODED-LOCATION-CONTRACT-PROMOTION** — promote dict → pydantic in `grace2_contracts.geocoding`. Bundle into v0.3.16+ housekeeping.
- **OQ-33-QUANTIZATION-GRID-DOCS** — document per-source grids in agent.md or a dedicated reference. Minor; not blocking.
- **OQ-33-CENSUS-STATE-FIPS-HEURISTIC** — current implementation uses a state-bbox heuristic; TIGER PIP would be more robust. Follow-up.
- **OQ-33-A6-ERROR-CODE-REGISTRY** — job-0035 owns; already captured there.
- **OQ-33-POPULATION-QML-PRESET** — population layer needs a QML style for rendering at M5+ render stage.
- **OQ-33-LOCATION-RESOLVED-EMISSION-SEAM** — `geocode_location` should emit `location-resolved` per FR-AS-7; job-0035 owns this emission seam.
- **OQ-33-MS-BUILDINGS-PMTILES-MATERIALIZATION** — PMTiles materialization for large-bbox queries; deferred to M5.

## Follow-up Actions

1. **CRITICAL: cache.py customTime hotfix** — apply 2-line fix to `services/agent/src/grace2_agent/tools/cache.py:337–338`: `fetched_at` becomes datetime, `.isoformat()` moves to the log line only. Orchestrator-direct hotfix bundled in this audit's commit. Add a regression test in job-0036 (M4 acceptance) that uses a higher-fidelity GCS fake (or real GCS) to catch type errors of this shape going forward.
2. **Unblock job-0036 (M4 acceptance)** — Stage C two of three approved; job-0034 audit completes the gate.
3. **Three v0.3.16+ housekeeping carries** — bundle with the prior carries (OQ-W-26 TTL-literal, OQ-INFRA-31-FR-DC-1 bucket layout, OQ-INFRA-31-LIVE-NO-CACHE-LIFECYCLE-NOOP).

## Sign-off

**Approved 2026-06-06 by Development Orchestrator.**

All 9 acceptance criteria met. Live `fetch_dem` GCS write + `geocode_location("Fort Myers, FL")` evidence captured. 8 tools registered on startup (verified). 22 new tests + 57-total agent suite green. FROZEN paths untouched (verified via diff: only `data_fetch.py` NEW + `main.py` additive + `pyproject.toml` deps + tests). Critical cache.py bug caught by the specialist's live-evidence discipline; hotfix bundled at audit closeout.

Sprint-06 Stage C two of three complete.
