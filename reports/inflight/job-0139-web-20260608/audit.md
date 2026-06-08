# Audit: Map.tsx vector layer rendering (resolves OQ-PAY-MAP-VECTOR-UNSUPPORTED)

**Job ID:** job-0139-web-20260608, **Sprint:** sprint-12-mega Wave 3.5, **Specialist:** web (CRITICAL — blocks Case 1 demo headline)

**Required reads:**
- `web/src/Map.tsx` (raster-only; lines 300-350 — addSource(type=raster) only)
- `packages/contracts/src/grace2_contracts/execution.py` (LayerURI shape — `layer_type: "raster" | "vector"`)
- `reports/inflight/job-0078-web-20260608/evidence/diagnosis.md` (raster-resampling discipline reference)
- Memory: `feedback_orchestrator_drives_ui_verification` (codified lesson — pixel-level evidence required)

### Why this job exists

Playwright UI capture agent (2026-06-08) surfaced **OQ-PAY-MAP-VECTOR-UNSUPPORTED**: `Map.tsx` only handles raster WMS layers. Vector layers added to `loaded_layers` (GBIF points, WDPA polygons, NWS alerts, OSM roads, MTBS burn perimeters, FIRMS active fire, eBird, IUCN ranges, Movebank tracks — 12+ Wave 1/1.5/2 fetchers all return `layer_type='vector'`) show in `LayerPanel` but never render on the map.

This blocks the Case 1 demo headline (per-species colored points + WDPA polygon overlay) AND every vector-emitting tool we've built so far. CRITICAL.

### Scope

Extend `web/src/Map.tsx` to handle vector `LayerURI`s in addition to existing raster handling.

1. **Detect layer_type**: when processing `loaded_layers`, branch on `layer.layer_type`:
   - 'raster': existing path (WMS via `addSource(type='raster')` + `addLayer(type='raster')`)
   - 'vector': new vector path (below)
2. **Vector source registration**:
   - For each vector layer:
     - If `uri` points at `gs://...fgb` or `https://...fgb` (FlatGeobuf): use `addSource(type='vector', data: uri)` — MapLibre supports FlatGeobuf via the `vector-tile-protocol-fgb` extension OR fetch the FGB, parse to GeoJSON in-browser, use `addSource(type='geojson', data: parsed)`
     - For v0.1 keep it simple: fetch the FGB via `fetch()` + convert to GeoJSON using the `flatgeobuf` npm package (add to `web/package.json` deps)
     - If `uri` is a direct GeoJSON URL or inline data, use `addSource(type='geojson')` directly
3. **Vector layer rendering**:
   - For each vector source, add an appropriate map layer based on geometry type (auto-detect from first feature):
     - Point geometry → `addLayer(type='circle', paint: {circle-radius, circle-color, circle-stroke})` — use `layer.style_preset` to pick color + radius if present
     - LineString → `addLayer(type='line', paint: {line-color, line-width})`
     - Polygon → `addLayer(type='fill', paint: {fill-color, fill-opacity, fill-outline-color})`
   - For per-species discipline: each vector layer gets a deterministic color from a palette (e.g. hash(layer_id) → color from a 12-color palette); if `layer.style_preset` exists, prefer that
4. **Z-order**: vector layers above raster overlays, below labels (use beforeId='waterway-label' or similar)
5. **Cleanup on remove**: when a layer is removed from `loaded_layers`, remove both source and layer cleanly
6. **Bbox handling**: if layer carries `bbox`, contribute to fitBounds computation on layer-added events (matches existing raster behavior)

**Tests** (Vitest with happy-dom):
- Mock fetch returning a GeoJSON FeatureCollection with point features → addSource + addLayer(type='circle') called
- Polygon features → addLayer(type='fill')
- LineString → addLayer(type='line')
- Multiple vector layers → multiple sources + layers (one per layer)
- Style preset present → preset color used
- No preset → deterministic palette color
- Removal: source and layer both removed
- Existing raster tests still pass

**Live verification** (Playwright):
- Inject session-state with the same Case 1 mock as the Playwright capture agent used: flood raster + 3 species (panther/spoonbill/alligator) + WDPA polygon
- Verify each species renders as DIFFERENTLY-COLORED point clusters on the map
- WDPA polygon renders as fill+outline
- Screenshot proves the vector rendering: species points visible AT their actual coordinates within Big Cypress bbox (geographic-correctness gate per codified job-0086 lesson)

### File ownership (exclusive)

- `web/src/Map.tsx` — vector rendering extension (~150 lines additive)
- `web/src/Map.test.tsx` — vector tests
- `web/src/lib/vector_rendering.ts` (NEW — fetch+parse+style helpers)
- `web/package.json` — add flatgeobuf dep if needed
- `reports/inflight/job-0139-web-20260608/`


### FROZEN

All files outside the explicit file-ownership list. Especially: every sibling Wave 3/3.5 job's exclusive files; `reports/complete/**`.

### Codified lessons (do NOT violate)

1. Geographic-correctness gate (job-0086): pixel-level evidence required.
2. Kickoff-front-loaded design: execute scope, surface OQs, don't redesign.
3. MongoDB MCP persistence (job-0115): use Persistence.* — no custom CRUD.

### Acceptance criteria

- [ ] Deliverables landed per scope
- [ ] Live verification per kickoff
- [ ] No FROZEN edits; single commit prefix `<job-id>:`; co-author line
- [ ] Returns commit SHA + outcome + 1-paragraph headline + evidence + OQs

