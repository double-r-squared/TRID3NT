# Report: Layer rendering investigation + fix — diagnose why single-tool layers don't paint on the map

**Job ID:** job-0171-engine-20260608
**Sprint:** sprint-12-mega Wave 4.8
**Specialist:** web (Opus)
**Task:** Trace why single-tool LayerURI envelopes reach LayerPanel but don't render on the map (radar / weather alerts / Fort Myers flood) and restore reliable rendering for BOTH raster (WMS) and vector (FlatGeobuf/GeoJSON) layers.
**Status:** ready-for-audit

## Summary

The kickoff's framing was inverted: the `session-state` → Map.tsx pipe is wired correctly (job-0159 SESSION_HUB fan-out + job-0076 idle-retry both land), and Map.tsx **does** register the raster source on first dispatch. What broke is the **content of the URLs the upstream tools hand Map.tsx**. Two structural producer-side gaps surfaced as one user-visible symptom ("layer appears in panel but not on the map"):

1. **Iowa State Mesonet NEXRAD tile URL is malformed** — `fetch_nexrad_reflectivity` emits a bare WMS service endpoint (no `?`, no `LAYERS=`), violating `docs/decisions/layer-emission-contract.md:36`. `Map.tsx::buildWmsTileUrl` then prepended `&SERVICE=…` producing a URL with no query-string separator and an empty `LAYERS=` param that Iowa Mesonet rejects.
2. **`fetch_nws_alerts_conus` emits a `gs://` URI** — the client correctly refuses (Invariant 5, `vector_rendering.ts:113-118`), but there is no signed-URL or public-HTTPS fallback path, so the vector layer is unreachable from the browser.

Web-side fix landed: `Map.tsx::buildWmsTileUrl` is now defensive (uses `?` when needed, synthesises `LAYERS=` from `style_preset` via the new `STYLE_PRESET_TO_WMS_LAYERS` table mapped from live Iowa Mesonet GetCapabilities). The NEXRAD layer URL the live client now requests is well-formed and returns a valid PNG when probed directly. The `gs://` vector failure is structural (agent + infra) and is surfaced as a contract-pushback OQ — no rendering-path web fix is achievable without infra (public bucket + CORS) or agent (signed-URL emission) work.

Full diagnostic walk-through with file:line citations in `evidence/diagnosis.md`.

## Changes Made

- File: `web/src/Map.tsx`
  - Added `STYLE_PRESET_TO_WMS_LAYERS` registry mapping `style_preset` → Iowa Mesonet EPSG:3857 layer name (verified against live GetCapabilities, see `evidence/iowa_capabilities_audit.txt`).
  - Rewrote `buildWmsTileUrl(wmsUrl, stylePreset?)` to choose `?` vs `&` based on whether the base URL already has a query string, and to synthesise `LAYERS=` from the preset when the URL doesn't already have one. Logs a `console.warn` with `OQ-0171-WMS-URL-CONTRACT` when neither path supplies LAYERS so future regressions are loud.
  - Updated the raster-add call site to pass `layer.style_preset` through.
- File: `web/src/Map.test.tsx`
  - 4 new regression tests: `?` vs `&` separator, LAYERS synthesis from preset, warn-on-missing, idempotency on a pre-formed URL.
- File: `reports/inflight/job-0171-engine-20260608/evidence/`
  - `diagnosis.md` — full hypothesis walk-through with file:line citations.
  - `diag_radar.mjs` — live Playwright reproducer (radar query).
  - `radar_diag_BEFORE_fix.json` / `radar_diag.json` — `m.getStyle()` snapshots showing the pre-fix malformed tile URL and the post-fix well-formed URL.
  - `radar_full_app_BEFORE_fix.png` / `radar_full_app.png` — visual evidence (basemap-only vs. radar-rendered).
  - `radar_map_only_BEFORE_fix.png` / `radar_map_only.png` — map-region screenshots.
  - `alerts_diag.json`, `alerts_full_app.png` — alerts repro with `gs://` refusal captured in console.
  - `iowa_capabilities_audit.txt` — live Iowa Mesonet `GetCapabilities` `<Name>` list confirming `nexrad-n0r-900913` and NOT `nexrad-n0r-wmst`.
  - `gs_public_probe.txt` — confirms the cache bucket is NOT public-readable from anonymous HTTPS (403).

## Decisions Made

- **Decision:** keep the shim CLIENT-SIDE rather than fixing the agent tool's URL.
  - Rationale: web specialist owns `Map.tsx`; the agent + engine fixes are out-of-scope per the kickoff file ownership. The shim restores rendering today and the contract violation is documented as an OQ for the next sprint.
  - Alternatives considered: (a) edit `fetch_nexrad_reflectivity.py` directly — rejected (cross-specialist ownership), (b) leave it broken and only file the OQ — rejected (kickoff says "restore reliable layer rendering").

- **Decision:** map `nexrad_n0r → nexrad-n0r-900913` not `nexrad-n0r-wmst`.
  - Rationale: verified against live `GetCapabilities` (`evidence/iowa_capabilities_audit.txt`); the `-wmst` form doesn't exist on Iowa Mesonet's server. `-900913` is the legacy Web-Mercator EPSG-code suffix Iowa Mesonet uses for EPSG:3857 layers (the projection MapLibre requests).
  - Alternatives considered: ask the engine specialist for the canonical mapping — rejected because the live capabilities are the ground truth and the existing agent table is demonstrably wrong.

- **Decision:** do NOT attempt a `gs:// → https://storage.googleapis.com/` client-side rewrite for vector URIs.
  - Rationale: verified the cache bucket is NOT public-readable (`evidence/gs_public_probe.txt` — anonymous HEAD returns 403). A rewrite would just produce a different 403. The right fix is agent-side signed URLs or infra-side public-read + CORS, both outside web ownership.
  - Alternatives considered: silently rewrite and tolerate 403s — rejected (worse-than-nothing UX), block the job — rejected (the WMS family is fixable today and unblocking that is high-value).

## Invariants Touched

- **1. Determinism boundary:** preserves — the shim composes URLs from received fields (`uri`, `style_preset`) and never invents a coordinate, depth, or count.
- **4. Rendering through QGIS Server:** preserves — this job touches only the Tier B raster path's URL-composition seam; QGIS Server / external WMS still does all rendering.
- **5. Tier separation:** preserves — no `gs://` fetch is ever issued; the `gs://` refusal in `vector_rendering.ts` is preserved as a hard guardrail.

## Open Questions

- **OQ-0171-WMS-URL-CONTRACT** (agent / engine) — `fetch_nexrad_reflectivity.py:175-206` returns a bare WMS endpoint instead of the complete `…?MAP=…&LAYERS=…&CRS=…` URL the producer/consumer contract documented at `docs/decisions/layer-emission-contract.md:36` requires. Web side currently masks this via the shim, but every additional tool that violates this contract will need a new preset entry. Tentative: amend `fetch_nexrad_reflectivity` to build and emit the full WMS GetMap URL, then remove the shim.
- **OQ-0171-NEXRAD-LAYER-NAME** (engine) — `_PRODUCT_LAYER_NAME` in `fetch_nexrad_reflectivity.py:111-117` says `n0r → nexrad-n0r-wmst`. Live `GetCapabilities` confirms NO `-wmst` layer exists. Correct value for EPSG:3857 is `nexrad-n0r-900913`.
- **OQ-0171-CACHE-GS-VECTOR-URI** (agent + infra) — `fetch_nws_alerts_conus` and structurally every cache-shim-backed atomic tool returning a `LayerURI` over a `gs://` URI is unreachable from the browser. The three viable resolutions (signed URLs on emit, public-read bucket + CORS, route vectors via QGIS Server WFS) span agent + infra + engine. Tentative recommendation: emit signed URLs in `add_loaded_layer` (`pipeline_emitter.py:449`) at the boundary so existing tool code stays unchanged.
- **Live verification of the Fort Myers flood publish path** — the kickoff names it as a symptom but prior job-0167 evidence (`map_layer_registered: true, map_layer_type: "fill"`) showed it working. I did not rerun the ~5-min wall-clock workflow in this pass.

## Dependencies and Impacts

- Depends on: job-0159 (SESSION_HUB fan-out — confirmed working in live trace), job-0076 (Map.tsx idle-retry race fix), job-0072 (`map-command` ws routing).
- Affects:
  - **engine** — should update `_PRODUCT_LAYER_NAME` to match live capabilities (OQ-0171-NEXRAD-LAYER-NAME) and emit complete WMS URLs (OQ-0171-WMS-URL-CONTRACT). When that lands, `STYLE_PRESET_TO_WMS_LAYERS` becomes dead code.
  - **agent** — the `gs:// → signed URL` translation belongs at the `add_loaded_layer` boundary in `pipeline_emitter.py` (OQ-0171-CACHE-GS-VECTOR-URI).
  - **infra** — alternative resolution for the `gs://` family is to make the cache bucket public-read with browser-targeted CORS.

## Verification

- Tests run: `npm test -- --run` (full vitest suite) → **345/345 passing**, including 4 new regression tests in `Map.test.tsx`.
- Live E2E evidence:
  - `evidence/radar_full_app_BEFORE_fix.png` (basemap only) vs `evidence/radar_full_app.png` (post-fix render path).
  - `evidence/radar_diag_BEFORE_fix.json` shows the malformed pre-fix tile URL (`…n0r.cgi&SERVICE=…&LAYERS=` empty).
  - `evidence/radar_diag.json` shows the correct post-fix tile URL (`…n0r.cgi?SERVICE=…&LAYERS=nexrad-n0r-900913`).
  - `evidence/alerts_diag.json` captures the `gs://` console refusal verbatim.
  - `evidence/iowa_capabilities_audit.txt` — live `GetCapabilities` proving `-900913` is correct and `-wmst` is wrong.
  - Direct curl probe of the post-fix URL returned a valid PNG.
- Results: **pass** for RC-1 (NEXRAD WMS URL); **qualified** for RC-2 (`gs://` vector URI) — refused at the client per Invariant 5 by design, end-to-end render unreachable without out-of-scope agent/infra changes; structural OQ filed.
