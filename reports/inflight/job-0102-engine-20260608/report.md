# Report: `fetch_nexrad_reflectivity` atomic tool — Iowa Mesonet NEXRAD WMS passthrough

**Job ID:** job-0102-engine-20260608
**Sprint:** sprint-12-mega Wave 1.5
**Specialist:** engine
**Status:** ready-for-audit

## Summary

Landed `fetch_nexrad_reflectivity` as a WMS-URL passthrough atomic tool wrapping the Iowa State University Mesonet NEXRAD service (no auth, Tier-1 free, ~5 min refresh). The tool composes the per-product WMS service URL (`.../wms/nexrad/{product}.cgi`), encodes the caller's bbox as a `BBOX=` query hint when supplied, and returns a `LayerURI(layer_type="raster", role="context")` the client renders directly. Three products supported: `n0r` (composite reflectivity, default), `n0q` (base reflectivity 0.5° tilt), `vil` (vertically integrated liquid). 14 unit tests + 1 live test (env-guarded; passed against real Iowa Mesonet WMS GetCapabilities) all green; tool registers cleanly in the global registry (41 tools total at agent startup).

## Changes Made

- `services/agent/src/grace2_agent/tools/fetch_nexrad_reflectivity.py` (NEW): `fetch_nexrad_reflectivity(bbox, product) -> LayerURI`, typed errors (`NexradError`/`NexradProductError`/`NexradBboxError`), `_build_wms_url`, `_validate_bbox`.
- `services/agent/src/grace2_agent/tools/__init__.py` (+1 line): import for `@register_tool` side effect.
- `services/agent/src/grace2_agent/main.py` (+2 lines): import in `_import_tools_registry`.
- `services/agent/tests/test_fetch_nexrad_reflectivity.py` (NEW): 14 unit + 1 live env-guarded test.
- `reports/inflight/job-0102-engine-20260608/evidence/nexrad_live.txt` (NEW): live invocation transcript.

## Decisions Made

- Registered `cacheable=False, ttl_class="live-no-cache", source_class=None` (not the kickoff sketch's `cacheable=True` which would fail the existing model_validator). Kickoff body text is explicit ("does NOT cache pixels"); matches `publish_layer` precedent. Surfaced as OQ-0102-CACHEABLE-FLAG-CONTRADICTION.
- Parked `supports_global_query=True` + `estimate_payload_mb=0.1` in module-level `_INTENDED_METADATA_EXTENSIONS` dict + docstring instead of adding to `AtomicToolMetadata` (schema-owned, FROZEN). OQ-0102-METADATA-FIELDS for follow-up.
- `role="context"` (storm-state overlay, not primary hazard product).
- WMS 1.1.1 LonLat axis order for BBOX hint (Iowa Mesonet convention).

## Invariants Touched

- Invariant 1 (Determinism): preserves — no LLM call.
- Invariant 3 (Engine registration not modification): preserves — `@register_tool` only.
- Invariant 4 (Rendering through QGIS Server / WMS): preserves — emits WMS URL.
- Invariant 10 (Minimal parameter surface): preserves — only bbox + product.

## Open Questions

- **OQ-0102-METADATA-FIELDS**: Wave 1.5 kickoffs sketch new `AtomicToolMetadata` fields (`supports_global_query`, `estimate_payload_mb`) that don't exist on the schema model. Engine FROZEN can't land them; needs schema follow-up. Tentative resolution: schema job adds fields with optional defaults; engine sweep migrates parked dicts into constructor args.
- **OQ-0102-CACHEABLE-FLAG-CONTRADICTION**: kickoff sketch `cacheable=True, ttl_class="live-no-cache"` violates the existing model_validator. Body text intent is uncacheable. Tentative: future kickoff template clarifies WMS-passthrough tools use `cacheable=False` per `publish_layer` precedent.
- **OQ-0102-WMS-AXIS-ORDER**: encoded BBOX in WMS 1.1.1 LonLat order (Iowa Mesonet convention). A 1.3.0 client requesting `CRS=EPSG:4326` would interpret axis order differently. Documented; not blocking since canonical web path uses CRS:84.

## Dependencies and Impacts

- Depends on: job-0032 (`@register_tool`), job-0030 (`AtomicToolMetadata`).
- Affects: web (new LayerURI plugs into existing MapLibre WMS overlay); schema (OQ-0102-METADATA-FIELDS).

## Verification

- Unit tests: 14/14 pass; 1 live test env-skipped by default.
- Live test: `GRACE2_TEST_LIVE_NEXRAD=1 pytest test_live_nexrad_endpoint_reachable` PASSED — real GET against `https://mesonet.agron.iastate.edu/cgi-bin/wms/nexrad/n0r.cgi?SERVICE=WMS&REQUEST=GetCapabilities` returned HTTP 200 with WMS-marker body.
- Live invocation transcript (`evidence/nexrad_live.txt`):
  - n0r CONUS → `https://mesonet.agron.iastate.edu/cgi-bin/wms/nexrad/n0r.cgi`, units=dBZ, role=context, layer_id=`nexrad-n0r-conus`, bbox=None
  - n0q Fort Myers `(-82.0, 26.0, -81.0, 27.0)` → `.../n0q.cgi?BBOX=-82.0%2C26.0%2C-81.0%2C27.0`, units=dBZ
  - vil CONUS → `.../vil.cgi`, units=`kg/m^2`
- Geographic-correctness gate: `test_bbox_supplied_returns_scoped_layeruri` asserts parsed BBOX query = `[-82.0, 26.0, -81.0, 27.0]` exactly — a sign-flip would fail.
- Agent startup: `python -m grace2_agent.main --startup-only` reports `tool registry loaded: 41 tool(s)` including `fetch_nexrad_reflectivity`.
- Results: pass.
