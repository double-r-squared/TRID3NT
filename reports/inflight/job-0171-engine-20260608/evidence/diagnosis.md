# job-0171 — diagnosis

**Symptom (user-reported):** even simple single-tool dispatches ("Show me radar over America", "Show me weather alerts across America", Fort Myers flood publish) appear in `LayerPanel` but do not visibly render on the map.

**Method:** drove the live agent (running on `127.0.0.1:8765`) through the live Vite dev client (`localhost:5173`) with Playwright, capturing every WebSocket frame and introspecting `m.getStyle()` after each session-state push (`evidence/diag_radar.mjs`, `evidence/diag_job0171_alerts.mjs`).

---

## H — Hypotheses tested (kickoff list, A–E)

| H | Hypothesis | Verdict | Evidence |
|---|---|---|---|
| A | SESSION_HUB fan-out works for `session-state` but not the `add-layer` envelope | **REJECTED** — there is no separate `add-layer` envelope. Layer surfacing is entirely through `session-state` (Appendix A.7 replace-not-reconcile, also encoded at `pipeline_emitter.py:484-499`). Fan-out IS firing — `radar_diag.json` shows 3 session-state frames received and the NEXRAD layer ID present in the last frame. | `evidence/radar_diag_BEFORE_fix.json:96-220` |
| B | Map.tsx's session-state subscription doesn't re-fire raster add when layers update | **REJECTED** — Map.tsx DID register the raster source on first dispatch. `m.getStyle().layers` contains `nexrad-n0r-conus` (type `raster`, visibility `visible`); the source map contains its entry with the tile URL filled in. | `evidence/radar_diag_BEFORE_fix.json:18-43` |
| C | Raster path in Map.tsx (WMS source) only triggers on initial mount, not on incremental layer adds | **REJECTED** — same evidence as B: addSource/addLayer fired correctly for the incremental dispatch. The idle-retry path lands per job-0076's race fix. | n/a |
| D | Vector vs raster code path divergence — Wave 3.5 job-0139 added vector but maybe regressed raster | **PARTIAL** — raster path was NOT regressed in code-flow terms (sources land correctly), but is broken in URL-composition terms for ANY upstream tool that emits a bare WMS endpoint URI (i.e., violates the `flood-emission-contract.md` "uri MUST carry MAP= and LAYERS=" rule). Vector path is broken for any tool that emits a `gs://` URI (correctly refused by `vector_rendering.ts:113-118`). | `evidence/radar_diag_BEFORE_fix.json:41`, `alerts_diag.json` (console log of `gs://` refusal) |
| E | CartoDB DarkMatter basemap mounted AFTER session-state arrived, clobbering layers | **REJECTED** — the live test ran with the default light theme; CartoDB swap path was never entered. The flood-overlay layer DOES sit at the top of the layer stack after registration. | `evidence/radar_diag_BEFORE_fix.json:5-23` |

The kickoff's framing (envelope reaching LayerPanel but not Map) is partly inverted: the envelope reaches Map.tsx fine, and Map.tsx registers the source/layer correctly. What's broken is the **content of the URLs Map.tsx is asked to register** — they're either malformed (WMS) or refused for invariant-5 reasons (`gs://`).

## Root causes (with file:line citations)

### RC-1 — Malformed WMS tile URL for tools that emit a bare WMS endpoint

`buildWmsTileUrl` in `web/src/Map.tsx:201-203` (pre-fix) blindly prepended `&SERVICE=…` to the `LayerURI.uri`. When the agent tool emits a URI that has no `?` yet (e.g. Iowa State Mesonet's `https://mesonet.agron.iastate.edu/cgi-bin/wms/nexrad/n0r.cgi` from `services/agent/src/grace2_agent/tools/fetch_nexrad_reflectivity.py:305-313`), the resulting URL is

```
https://…/n0r.cgi&SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap&CRS=EPSG:3857&…&LAYERS= ← empty
```

Note the **leading `&` after `.cgi`** — there is no query-string separator at all; `&SERVICE=…` is part of the URL path. AND there is **no `LAYERS=` value** because the upstream tool encoded the product into the path component (`n0r.cgi` vs `n0q.cgi` vs `vil.cgi`) rather than the LAYERS param.

Iowa Mesonet's WMS rejects this with `ServiceException` (LayerNotDefined) when probed directly — verified manually with `curl`. MapLibre silently paints no tiles when the response isn't `image/png`.

Root cause is twofold:
1. **Web-side composer bug** — should use `?` when the URL has no query string yet (fixed in `Map.tsx::buildWmsTileUrl`).
2. **Agent-side contract violation** — `flood-emission-contract.md:36` says `ProjectLayerSummary.uri` MUST carry `MAP=` and `LAYERS=`. `fetch_nexrad_reflectivity.py:175-206` emits only the service base, expecting the consumer to infer LAYERS. This is a producer/consumer contract gap (raised as OQ).

A secondary RC: the agent tool's `_PRODUCT_LAYER_NAME` table (line 111-117) maps `n0r → nexrad-n0r-wmst`, but Iowa Mesonet's actual GetCapabilities (captured in `evidence/iowa_capabilities_audit.txt`) publishes `nexrad-n0r-900913` for EPSG:3857 — there is NO `nexrad-n0r-wmst` layer at all. The shim in this job uses `-900913`, which returned a valid PNG when verified via curl.

### RC-2 — `gs://` vector URIs cannot be fetched by the browser (correct invariant-5 behaviour, but no fallback path exists)

`fetch_nws_alerts_conus` (and the cache-shim family generally) writes its FlatGeobuf output to `gs://grace-2-hazard-prod-cache/cache/dynamic-1h/nws_alerts_conus/<hash>.fgb` and emits that `gs://` URI as `LayerURI.uri` (`fetch_nws_alerts_conus.py:480-512`). `vector_rendering.ts:113-118` correctly refuses to fetch `gs://` from the browser (Invariant 5). `Map.tsx::addVectorLayer` catches the error and logs a warn, but the layer never paints.

Verified: `gsutil`-side bucket is NOT public (`https://storage.googleapis.com/<bucket>/<path>` returns 403 — `evidence/gs_public_probe.txt`), so a naive `gs:// → https://storage.googleapis.com/` rewrite at the client won't work.

Root cause is structural: the cache-shim family emits a `gs://` URI without a public-readable HTTPS counterpart and without a signed-URL fallback. This is an agent + infra problem; raised as OQ-0171-CACHE-GS-VECTOR-URI.

### RC-3 — Fort Myers flood publish path is functional in current live trace

Per job-0167 evidence (`map_layer_registered: true, map_layer_type: "fill"`), the `publish_layer` → QGIS Server WMS path is wired end-to-end and does render. The user's report likely tied this symptom to RC-1 by association (both raster, both appear in LayerPanel, neither renders) — but the raster Fort Myers flood reaches the map via a QGIS Server URL that already carries `?MAP=…&LAYERS=…` (per the contract). Live verification of a fresh flood run was not run in this diagnostic pass because that workflow is ~5 min wall-clock; spot-check against job-0167's existing evidence stands.

## Fix scope landed in this job

| Change | File | Why |
|---|---|---|
| `buildWmsTileUrl(wmsUrl, stylePreset?)` — new defensive separator + LAYERS shim from preset | `web/src/Map.tsx:201-258` | Recovers the malformed-URL family at the consumer; preserves contract for tools that DO emit a complete URL. |
| `STYLE_PRESET_TO_WMS_LAYERS` registry for known Iowa Mesonet products | `web/src/Map.tsx:174-186` | Pins the correct `nexrad-n0r-900913` etc. from live GetCapabilities (`evidence/iowa_capabilities_audit.txt`). |
| Call-site pass `style_preset` into `buildWmsTileUrl` | `web/src/Map.tsx:706` | Wires the shim through. |
| 4 new tests covering: `?` vs `&` separator, LAYERS synthesis from preset, warn on unknown preset, idempotency on pre-formed URL | `web/src/Map.test.tsx:376-426` | Regression coverage. |

## Out-of-scope (raised as OQs, not fixed here)

1. **OQ-0171-WMS-URL-CONTRACT** — `fetch_nexrad_reflectivity` (and any other tool that emits a bare WMS endpoint) violates `flood-emission-contract.md:36`. Route to engine/agent.
2. **OQ-0171-NEXRAD-LAYER-NAME** — the `_PRODUCT_LAYER_NAME` table at `fetch_nexrad_reflectivity.py:111-117` is wrong; Iowa Mesonet has no `-wmst` layers. The shim landed here masks it for now. Engine should update the table to the actually-published name and emit the complete URL.
3. **OQ-0171-CACHE-GS-VECTOR-URI** — `fetch_nws_alerts_conus` and any other Tier-1 fetcher that returns a `LayerURI` over a `gs://` cache URI is unreachable from the browser. The contract needs either (a) a signed-URL emission path, (b) public-read bucket + CORS on the cache bucket (infra), or (c) routing vector layers through QGIS Server's WFS endpoint (engine). Route per the architecture's "Tier B reaches map only via QGIS Server or agent GeoJSON".

These three OQs are the structural fixes for the next sprint; the in-job shim makes radar render today.
