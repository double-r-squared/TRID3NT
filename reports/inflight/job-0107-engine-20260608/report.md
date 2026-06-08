# Report: clip_vector_to_polygon utility atomic tool

**Job ID:** job-0107-engine-20260608
**Sprint:** sprint-12-mega Wave 1.5
**Specialist:** engine
**Task:** New atomic tool `clip_vector_to_polygon(vector_uri, polygon_uri, feature_filter?, keep_partial)` -> `LayerURI` — sibling to `clip_raster_to_polygon` (job-0106). Enables the "in [place]" geographic-clipping pattern for vector layers (e.g. clip nationwide GBIF occurrences to a TIGER FL state polygon).
**Status:** ready-for-audit

## Summary

Implemented `clip_vector_to_polygon` as a deterministic, cacheable atomic tool that reads a vector layer (points/lines/polygons) and a polygon-mask layer, optionally filters the polygon source by attribute (`feature_filter`), dissolves to a single mask geometry, reprojects the polygon to the vector CRS if mismatched, and clips with either `intersects` (`keep_partial=True`) or `within` (`keep_partial=False`) semantics. The result is written as a FlatGeobuf and routed through `read_through` under `cache/static-30d/clip_vector_polygon/<key>.fgb`. 9 unit tests + 1 env-gated live test verify the contract; the live test clips 500 white-tailed-deer GBIF occurrences (FL+GA+AL+SC bbox) to a TIGER FL state polygon and asserts every surviving point falls inside FL's real-world bbox.

## Changes Made

- **NEW** `services/agent/src/grace2_agent/tools/clip_vector_to_polygon.py` (~411 lines): tool body + `ClipVectorError` typed exception + `AtomicToolMetadata(name="clip_vector_to_polygon", ttl_class="static-30d", source_class="clip_vector_polygon", cacheable=True)`.
- **APPENDED** `services/agent/src/grace2_agent/tools/__init__.py` — 1 line: eager `from . import clip_vector_to_polygon` for FR-CE-8 fail-fast registration.
- **APPENDED** `services/agent/src/grace2_agent/main.py` — 2 lines: registration import inside `_import_tools_registry()` so startup-only verification covers the new tool.
- **NEW** `services/agent/tests/test_clip_vector_to_polygon.py` (~430 lines): 9 unit tests + 1 env-gated live test.

## Decisions Made

- **keep_partial uses intersects/within rather than intersection-truncation** to preserve original geometry (and any precomputed CRS-dependent attributes like area_m2).
- **Vector CRS is authoritative** for reprojection direction — only the polygon mask is reprojected when CRSes mismatch.
- **unary_union dissolve after filter** when multiple polygon features remain — matches user intent for filters that produce multi-row results.
- **Default style_preset="affected_buildings"** as a generic vector preset already in the v0.1 FR-QS-5 preset set (TENTATIVE).
- **TTL class static-30d** — both inputs are themselves static-30d cached layers, so the clip is static.

## Invariants Touched

- Invariant 1 (Determinism boundary): preserves — no LLM calls.
- Invariant 2 (Deterministic workflows): preserves — pure Python composition of geopandas/shapely.
- Invariant 10 (Minimal parameter surface): preserves — only intent + irreducible inputs.
- FR-CE-8 (cacheable + fail-fast registration): honors.
- FR-DC-3/4 (cache key + deduplication): honors.
- NFR-R-1 (resilience): preserves — all failures surface as typed `ClipVectorError(error_code, message)`.
- FR-TA-3 (docstring discipline): preserves.
- CRS hygiene (engine.md): preserves.
- Geographic-correctness gate (codified lesson from job-0086): preserves — live test verifies output points fall inside FL's real-world bbox.

## Open Questions

- **OQ-0107-DEFAULT-STYLE-PRESET (non-blocking, TENTATIVE):** default `style_preset="affected_buildings"` is a generic placeholder; could be revisited if a `"clipped_features"` preset is added.
- **OQ-0107-LARGE-VECTOR-MEMORY (non-blocking, TENTATIVE):** both URIs are loaded fully into memory via `gpd.read_file`. Nationwide-scale clips (millions of features) may need out-of-core (pyogrio bbox-pre-filter or DuckDB Spatial). Live test ran <100MB.
- **OQ-0107-MIXED-GEOMETRY-VECTOR (resolved as hard error):** mixed-type vectors raise `UNSUPPORTED_GEOMETRY` — conservative; revisit if a real layer ships mixed kinds.

## Dependencies and Impacts

- Depends on: cache shim (job-0032), `LayerURI` contract, `register_tool` (job-0032). No blocking deps.
- Affects: `agent` (new tool in TOOL_REGISTRY); enables the "X in [place]" composition pattern paired with `fetch_administrative_boundaries` and `fetch_gbif_occurrences`/`fetch_inaturalist_observations`/`fetch_wdpa_protected_areas` for Case-1 species/protected-area overlays.

## Verification

### Unit tests (9 unit + 1 env-gated live)
```
$ .venv-agent/bin/python -m pytest services/agent/tests/test_clip_vector_to_polygon.py -v
... 9 passed, 1 skipped in 0.29s
```

### Startup-time registration
```
$ GRACE2_AGENT_SKIP_MCP_SMOKE=1 GRACE2_SKIP_WORKER_SUBMITTER=1 .venv-agent/bin/python -m grace2_agent.main --startup-only
... tool registry loaded: 40 tool(s): [..., 'clip_vector_to_polygon', ...]
```
40 tools registered including `clip_vector_to_polygon`.

### Live geographic-correctness test (env-gated by GRACE2_TEST_LIVE_CLIPV=1)
```
$ GOOGLE_APPLICATION_CREDENTIALS=~/.config/gcloud/application_default_credentials.json \
  GOOGLE_CLOUD_PROJECT=grace-2-hazard-prod \
  GRACE2_TEST_LIVE_CLIPV=1 \
  .venv-agent/bin/python -m pytest services/agent/tests/test_clip_vector_to_polygon.py::test_live_clip_gbif_panther_to_florida -v -s
... LIVE clip: 500 input panther points -> 161 after clip-to-FL
... PASSED in 10.08s
```

The live test does the codified job-0086 geographic-correctness gate:
1. Fetches TIGER 2024 state polygons over SE-US bbox.
2. Fetches 500 white-tailed deer (Odocoileus virginianus) GBIF occurrences over (-90, 24, -78, 36) — spans FL+GA+AL+SC+parts of NC/TN.
3. Calls `clip_vector_to_polygon` with `feature_filter={"STUSPS": "FL"}`.
4. Asserts output count (161) < input count (500).
5. Asserts every surviving point inside FL's real-world bbox (-87.6, 24.4, -80.0, 31.0) — pure-geometry verification.

### Results: PASS
- 9/9 unit tests pass; live test passes with GCP creds.
- FR-CE-8 fail-fast registration verified.
- Geographic-correctness gate satisfied.
- No FROZEN file edits beyond the 1-line registration appends.
