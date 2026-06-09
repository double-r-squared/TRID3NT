# Diagnosis: Vector polygon non-render (job-0175)

## Symptom (from Wave 4.8 job-0174)

"Show me weather alerts across America" → 240s elapsed, agent narrated success,
but `settled_with_overlay: false`, `new_layers: []`. Layer never reached the
map. Same pattern for protected areas in Fort Myers.

NEXRAD raster path (test 2) PASS. So raster path works; vector polygon path
is broken.

## Root cause: `gs://` URI guardrail trips on every vector layer

Trace of the LayerURI for a vector fetcher (NWS alerts conus, as representative):

1. `services/agent/src/grace2_agent/tools/fetch_nws_alerts_conus.py:504-512` —
   the tool returns
   ```python
   LayerURI(
     layer_id=..., name=..., layer_type="vector",
     uri=result.uri,        # <-- gs://grace-2-hazard-prod-cache/cache/dynamic-1h/nws_alerts_conus/<hash>.fgb
     style_preset="nws_alerts", role="primary", units=None,
   )
   ```
   where `result.uri` is built by `tools/cache.py:215`:
   ```python
   def _gs_uri(bucket, path) -> str:
       return f"gs://{bucket}/{path}"
   ```

2. `services/agent/src/grace2_agent/pipeline_emitter.py:480-507` —
   `add_loaded_layer` constructs a `ProjectLayerSummary` setting
   `uri=layer.uri` verbatim (the `gs://...fgb` value passes through).
   The summary is serialized over the WebSocket as part of `session-state`.

3. `web/src/Map.tsx:751-763` — `MapView`'s session-state effect dispatches on
   `layerType === "vector" || layerType === "geojson"` and calls
   `addVectorLayer(m, layer, ...)`.

4. `web/src/Map.tsx:322-335` (inside `addVectorLayer`) → calls
   `fetchVectorAsGeoJson(layer.uri)`.

5. `web/src/lib/vector_rendering.ts:113-119` — the **Invariant 5 guardrail**:
   ```js
   if (uri.startsWith("gs://")) {
     throw new Error(
       `[vector_rendering] refusing to fetch gs:// URL from client (invariant 5): ${uri}`,
     );
   }
   ```
   The error is caught at `Map.tsx:326-335` and logged as
   `[MapView] vector fetch failed for ${layer.layer_id}: <error>`; the
   layer slot is then released. No source is ever added. No visible map
   change. The LayerPanel still shows the entry because LayerPanel reads
   from `session-state.loaded_layers` directly, NOT from the map's style.

## Why this hits every vector fetcher, not just NWS

Every cacheable vector fetcher invokes `read_through(...)` and stamps the
returned `result.uri` (a `gs://` URL) into `LayerURI.uri`. Same code path
in: `fetch_wdpa_protected_areas`, `fetch_nws_event`, `fetch_nws_alerts_conus`,
`fetch_roads_osm`, `fetch_mtbs_burn_severity`, `fetch_firms_active_fire`,
`fetch_nifc_fire_perimeters`, `fetch_inaturalist_observations`,
`fetch_gbif_occurrences`, `fetch_movebank_tracks`, `fetch_ebird_observations`,
`fetch_iucn_red_list_range`, `fetch_storm_events_db`,
`fetch_administrative_boundaries`, etc. — i.e. 14+ tools. NEXRAD works only
because it's `layer_type="raster"` and goes through `buildWmsTileUrl` which
talks to a HTTPS WMS endpoint, not to GCS.

## Why the architecture trips here

The cache bucket has `public_access_prevention = "enforced"`
(`infra/buckets.tf:45,74,103`), so `https://storage.googleapis.com/...` URLs
won't work either. The agent service is a pure WebSocket server
(`websockets.asyncio.server.serve`, `server.py:40,2101`) with NO HTTP
endpoints, so there's no in-process proxy.

For raster, this is handled by `publish_layer` → QGIS Server (Cloud Run)
which has `roles/storage.objectViewer` on the cache bucket and serves WMS
tiles from `/vsigs/` paths inside the `.qgs`. No equivalent path exists for
vector — there is no `publish_vector_layer` tool, and the kickoff for
job-0139 (which added vector rendering) assumed the client could fetch the
FGB directly.

## Fix strategy (chosen)

Add **inline GeoJSON** to the vector layer payload at the
`pipeline_emitter.add_loaded_layer` seam:

1. In `add_loaded_layer`, when `layer.layer_type == "vector"`, fetch the
   bytes from GCS (the agent service runs with ADC and has `storage.objects.get`
   on the cache bucket — same path used by `read_through` and
   `worker_runs_viewer`), convert FGB → GeoJSON (or pass GeoJSON through),
   stash `geojson` in a side-map keyed by `layer_id`.
2. In `emit_session_state`, after `model_dump`'ing the `ProjectLayerSummary`,
   merge the `inline_geojson` field into the wire dict for vector layers
   that have it. This keeps `ProjectLayerSummary` pydantic-strict
   (`extra="forbid"`) while extending the wire shape additively.
3. In `Map.tsx` / `vector_rendering.ts`, prefer `inline_geojson` over `uri`:
   when the field is present, skip the fetch entirely and construct the
   FeatureCollection from the inlined object.

This keeps the strict client-side Invariant-5 guardrail intact (the client
literally never fetches `gs://`); the agent honestly owns the GCS read; and
it generalizes to **every** vector fetcher with one change.

### Why not other approaches

- **Add a vector-WFS path through QGIS Server**: would require `publish_vector_layer`
  + a `.qgs` mutation per layer + worker-side QML/style baking. Scope creep
  outside this job; out of web ownership.
- **Add an HTTP cache proxy to the agent server**: agent specialist work;
  cross-cutting infra concern (CORS, auth on the WS port).
- **Use signed URLs**: every layer would get a short-lived signed URL stamped
  into `uri`. Plumbing for signing key + lifetime config + URL rotation on
  reconnect; more moving parts than inline.

Inline GeoJSON is the smallest possible change to make every vector tool
render. Cost: the wire payload grows by the GeoJSON size; for a typical NWS
alerts CONUS sweep (~200KB) or Big Cypress WDPA (~50KB) this is well under
the 5MB FR-WC payload soft limit. Larger collections (1000+ GBIF points)
are flagged via `tool-payload-warning` already.

### Risk: payload size

Documented as OQ-0175-INLINE-PAYLOAD-SIZE. For v0.1 demo the sizes are
modest; a future server-rendered vector tile path (WFS via QGIS Server)
would be the right v0.2 substrate. Recorded for the next sprint.
