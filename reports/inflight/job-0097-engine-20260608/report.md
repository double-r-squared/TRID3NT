# Report: `fetch_roads_osm` atomic tool — OSM Overpass road LineStrings

**Job ID:** job-0097-engine-20260608
**Sprint:** sprint-12-mega Wave 1
**Specialist:** engine
**Task:** NEW `services/agent/src/grace2_agent/tools/fetch_roads_osm.py` per audit.md
**Status:** ready-for-audit

## Summary

Implemented the `fetch_roads_osm` atomic tool exactly to the kickoff spec: POSTs Overpass-QL to `https://overpass-api.de/api/interpreter`, projects each `way` element to a LineString with `osm_id` / `name` / `highway` / `lanes` / `maxspeed` properties, serializes to FlatGeobuf, and routes through the FR-DC-3 `read_through` shim (static-30d, `source_class="osm_roads"`). Typed errors (`OSMInputError` non-retryable, `OSMUpstreamError` retryable for 5xx/429/network; non-retryable for other 4xx) match FR-AS-11. 1-second polite-delay fires before every miss-path request. 22 tests pass (21 unit + 1 live); live capture against the real Overpass endpoint over a small Fort Myers bbox returns 1497 features with the expected Tamiami Trail / US-41 markers and `highway in {primary, motorway}`. Tool registered in `tools/__init__.py` + `main.py`; `--startup-only` reports `35 tool(s)` including `fetch_roads_osm`.

## Changes Made

- **NEW** `services/agent/src/grace2_agent/tools/fetch_roads_osm.py` — typed `(bbox, road_classes=None) -> LayerURI`, `@register_tool(AtomicToolMetadata(name="fetch_roads_osm", ttl_class="static-30d", source_class="osm_roads", cacheable=True))`.
- **APPEND** `services/agent/src/grace2_agent/tools/__init__.py` — 1 line: `from . import fetch_roads_osm  # noqa: E402,F401 — job-0097: registers fetch_roads_osm` (idempotent; sibling Wave 1 jobs 0080-0096+0098 also appended).
- **APPEND** `services/agent/src/grace2_agent/main.py` — 1 line in `_import_tools_registry()`.
- **NEW** `services/agent/tests/test_fetch_roads_osm.py` — 22 tests (registration, validation, QL builder, record extraction, mocked happy path, 504/429/400 HTTP-error mapping, cache miss-then-hit, class-ordering collapses to one key, LayerURI shape, bbox-rounding, live Fort Myers integration env-gated by `GRACE2_TEST_LIVE_OSM=1`).
- **NEW** `reports/inflight/job-0097-engine-20260608/evidence/capture_live.py` — standalone live-invocation harness.
- **NEW** `reports/inflight/job-0097-engine-20260608/evidence/osm_roads_live.txt` — verbatim live capture.

## Decisions Made

- **`time.sleep(1.0)` BEFORE the POST, not after.** Even the first call after agent startup respects Overpass rate-limit etiquette; placing it after would still allow a burst on the impolite-pair case. Tests patch `time.sleep` to no-op so suite stays fast.
- **`road_classes` vocabulary is a 16-value frozenset.** Typos (`'higway'`) fail fast; only road-carriageway tiers + ramps + edge-cases. `cycleway`/`footway`/`rail` belong in a future `fetch_paths_osm`.
- **Empty `road_classes=[]` raises `OSMInputError`, NOT mapped to default.** `None` is the documented sentinel; ambiguity costs the LLM nothing to resolve.
- **Empty Overpass response → valid empty FlatGeobuf (no sentinel, no exception).** Matches sibling-tool pattern; empty IS a valid answer for an ocean bbox.
- **HTTP 4xx non-429 → `retryable=False`; 5xx/429/network → `retryable=True`.** Matches FR-AS-11; 429 is rate-limit, 504 is Overpass overloaded, 400 is a bad query.
- **Sort `road_classes` before cache-key computation.** `["primary","motorway"]` and `["motorway","primary"]` collapse onto the same cache entry.
- **bbox rounded to 6dp for cache key.** Matches `fetch_administrative_boundaries` + `fetch_inaturalist_observations` precedent.

## Invariants Touched

- **1. Determinism boundary**: preserves — pure Python tool returns typed `LayerURI`; no LLM-narrated prose. Tag values come straight from OSM, not from generation.
- **3. Engine registration, not modification**: preserves — added via `@register_tool`; no edits to agent core or dispatch logic.
- **10. Minimal parameter surface**: preserves — only `(bbox, road_classes)` exposed; `road_classes` defaults to major+arterial tier so common case is one-arg.

## Open Questions

- **OQ-0097-OVERPASS-RATE-LIMIT-BACKOFF** (non-blocking): per-tool 1s sleep matches the audit literal but a shared cross-tool rate-limiter would respect upstream budgets more honestly when the LLM chains multiple external-API tools in a turn. Tentative: defer to sprint-13 cross-cutting concern alongside cache shim.
- **OQ-0097-OVERPASS-MIRROR-FALLOVER** (non-blocking): the public `overpass-api.de` mirror returned 504 twice during live-test development. Kumi Systems + CNRS run alternative mirrors; failover on `retryable=True` would harden the live path. Tool's typed `OSMUpstreamError(retryable=True)` is good enough for v0.1; the agent can backoff or surface to user. Tentative: defer to sprint-13.
- **OQ-0097-HIGHWAY-VOCAB-COMPLETENESS** (non-blocking): the 16-value `_VALID_ROAD_CLASSES` set deliberately excludes `cycleway`/`footway`/`path`/`track`/`rail`. If a future job adds non-road OSM ways, it should be a separate tool, not an expansion here.

## Dependencies and Impacts

- Depends on: job-0031 (cache bucket), job-0032 (`read_through` + `register_tool`), job-0084 pattern (`fetch_administrative_boundaries`), job-0088 pattern (`fetch_inaturalist_observations`). All in `complete/`.
- Affects: `agent` (discoverable at startup, no agent-core change); `web` (FGB lands via existing path; QML preset `osm_roads` does not yet exist — follow-up); `infra` (no change; cache bucket already has 30-day lifecycle); `testing` (suite self-contained for sprint-12 acceptance).

## Verification

### Unit + cache + error-mapping tests (22 tests)

```
GRACE2_SKIP_WORKER_SUBMITTER=1 .venv-agent/bin/python -m pytest \
    services/agent/tests/test_fetch_roads_osm.py -v
```
Result: `21 passed, 1 skipped` (the 1 skipped is the live test, env-gated).

### Startup-only registration check

```
GOOGLE_APPLICATION_CREDENTIALS=~/.config/gcloud/application_default_credentials.json \
    GRACE2_SKIP_WORKER_SUBMITTER=1 \
    .venv-agent/bin/python -m grace2_agent.main --startup-only
```
Output: `tool registry loaded: 35 tool(s): [..., 'fetch_roads_osm', ...]`. Count is +1 vs the pre-job baseline.

### Live E2E evidence (Overpass real endpoint, Fort Myers)

```
GRACE2_SKIP_WORKER_SUBMITTER=1 PYTHONPATH=services/agent/src \
    .venv-agent/bin/python reports/inflight/job-0097-engine-20260608/evidence/capture_live.py
```
Evidence file: `reports/inflight/job-0097-engine-20260608/evidence/osm_roads_live.txt`

Verbatim summary:
- bbox: `(-82.0, 26.5, -81.8, 26.7)` (Fort Myers area, ~22 × 22 km)
- road_classes: `('primary', 'motorway')`
- FlatGeobuf size: 382,840 bytes
- feature count: **1497**
- geometry total bbox: `(-82.0071, 26.4929, -81.792, 26.7171)` (per-feature intersection assertion in live test confirms each way crosses the requested bbox)
- CRS: `EPSG:4326`; geometry type: `LineString`
- highway tag distribution: `{motorway, primary}` (matches filter, no leakage)
- columns: `['osm_id', 'name', 'highway', 'lanes', 'maxspeed', 'geometry']` (audit-kickoff verbatim)
- **geographic markers**: `['tamiami', 'us 41']`
- **named Fort Myers roads**: South/North Tamiami Trail, Cleveland Avenue, McGregor Boulevard, Edison Bridge, Colonial Boulevard, Pine Island Road, Cape Coral Bridge, Daniels Parkway — real Fort Myers/Lee County primary roads.

The codified job-0086 lesson is satisfied DIRECTLY: not just round-trip self-consistency, but a verified geographic claim — Tamiami Trail and Edison Bridge ARE the headline primary roads in this exact bbox, and the tool returned them by name. A silent orientation bug would have either swapped them for roads 30 km north in Punta Gorda or produced an empty result; the named-route check catches it.

### Live integration test (also passes)

```
GRACE2_TEST_LIVE_OSM=1 GRACE2_SKIP_WORKER_SUBMITTER=1 \
    .venv-agent/bin/python -m pytest \
    services/agent/tests/test_fetch_roads_osm.py::test_live_fort_myers_returns_primary_and_motorway -v
```
Result: `1 passed in 8.97s`. Asserts every feature intersects bbox, highway-tag subset of `{primary, motorway}`, and an I-75 / US-41 / Tamiami marker is in the named-roads list.

### Acceptance criteria status

- [x] New tool registered + visible at `--startup-only` (count +1; `fetch_roads_osm` in sorted list)
- [x] ≥4 unit tests + 1 live test: 21 unit + 1 live = 22 total
- [x] Live verification captured to `evidence/osm_roads_live.txt`
- [x] Geography correctness check (codified job-0086 lesson): per-feature bbox-intersection + Tamiami/US-41 named-route assertion + highway-tag-subset assertion — in live test AND capture harness
- [x] No FROZEN edits; single commit prefix `job-0097:`; co-author line
- [x] Returns commit SHA + outcome + headline + evidence + OQs

Results: **pass**.
