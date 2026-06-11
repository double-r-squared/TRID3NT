# job-0270 adversarial verification artifacts (Fable verifier, 2026-06-10/11)

Subject: commit 0bdc8b9 (validator auto-widen + publish-to-map discipline) on top of
job-0269b (`_infer_style_preset`) and job-0269 (terrain gdaldem). Acceptance question:
will the user's next live "Compute a colored relief map for Boulder, Colorado" end
with visible pixels?

## Verdicts

- PART 1 (code attack): **CONFIRM** — auto-widen bypasses no gate (5/5 targeted
  tests pass, `test_gates_after_autowiden_0270.py`), hallucination guard intact,
  no import cycle, SYSTEM_PROMPT additive-only, style inference survives the
  adversarial probes that matter (live Boulder URI → `""`).
- PART 2 (live Gemini-free E2E): **REFUTED** — the decisive GetMap is 100%
  transparent. Root cause is OUTSIDE the verified commits: QGIS Server marks every
  layer whose datasource is `/vsigs/.../colored_relief/27d5f864...tif` "not valid"
  at project load, deterministically, across 3 project reloads (01:36, 02:10,
  02:28 UTC server logs), while the SAME process renders a structurally identical
  RGBA relief from the same bucket directory.
- PART 3 (full suite): **CONFIRM** — 5 failed / 4315 passed / 72 skipped /
  1 xfailed; exactly the 5 proven-pre-existing failures.

## Key artifacts

| file | what it shows |
|---|---|
| `live_publish_0270.log` | real publish of the EXACT cached Boulder relief; `INFERRED_STYLE_PRESET=''` asserted; worker `CONDITION_SUCCEEDED`; WMS URL returned |
| `live_publish_0270b.log` | second publish (forces project reload in the warm server process) |
| `getmap_verify_0270.png` | THE DECISIVE TILE — fully transparent (1 RGBA value, 0% non-transparent, 0 hues) |
| `getmap_lonlat_order.png`, `getmap_3857.png` | axis-order / CRS controls — also blank (request shape is not the cause) |
| `getmap_reload_*.png` | all three Boulder layers still blank AFTER warm-process reload (negative cache is per-path, not per-layer) |
| `getmap_old_broken_layer.png` | negative control: old flood-ramp layer, blank — but NOT because of the ramp (see below) |
| `getmap_flood_control.png` | positive control: flood layer renders (11,585 colors, 69.3% px) |
| `getmap_seattle_relief_control.png` | KEY control: RGBA relief, SAME bucket dir, OLD flood-ramp preset — renders 65.2% px → the flood ramp makes terrain look wrong (uniform blue), it does NOT make it transparent |
| `getmap_elevation_washington.png` | cache-bucket DEM renders (70.0% px) |
| `pixel_stats.txt` | decoded statistics for every tile |
| `getcapabilities_0270.xml` | new layers present; Boulder layers advertise a CRS-bounds fallback extent (invalid-layer signature) |
| `test_gates_after_autowiden_0270.py` | targeted gate-bypass attack tests (5 pass) |
| `full_suite_0270_verify.log` | full agent suite re-run |

## Root-cause evidence chain (Part 2)

1. Raster is healthy: 4-band RGBA uint8 EPSG:5070 COG; opens locally (rasterio);
   opened twice by the pyqgis-worker (which computed real band stats into the
   `.qgs` and the `.aux.xml`); `.qgs` entry well-formed (multibandcolor renderer,
   correct extent — the 0269b style fix DID land in the project).
2. Server log (decisive): `02:28:40 WARNING Server[28]: Warning, Layer(s)
   colored_relief_boulder_colorado_…, colored_relief_boulder_verify_0270_…,
   colored_relief_boulder_verify_0270b_… not valid in project`.
3. Same failure hit `elevation_washington` yesterday 20:45–20:47 (then-first
   /vsigs/ raster in document order) and healed later — the failure mode predates
   and is orthogonal to jobs 0269/0269b/0270.
4. IAM is NOT the cause: `grace-2-qgis-server@` SA has `objectViewer` on the cache
   bucket, and sibling cache-bucket rasters render in the same process.
5. Mechanism (best supported): the first /vsigs/ open(s) of a fresh project load
   fail (cold-start GCS auth/token race), GDAL negative-caches the PATH for the
   process lifetime (min-instances=1 keeps processes alive for days), so every
   later layer/reload re-using that path stays invalid while new paths succeed.
   The Boulder layers sort alphabetically FIRST among /vsigs/ rasters, so they
   take the first-open hit on every cold load.
6. Consequence for the acceptance test: the user's prompt is a cache HIT
   (static-30d, same args → same key `27d5f864…`), so the next live run publishes
   the SAME poisoned path to the SAME warm server → blank map.

## Defects filed

- **HIGH (blocks acceptance, outside subject commits)**: QGIS Server per-path
  layer-open failure as above. Fix directions: retry/validate layer opens in the
  server container (e.g. `QGIS_SERVER_IGNORE_BAD_LAYERS` semantics +
  `CPL_VSIL_CURL_NON_CACHED=/vsigs/grace-2-hazard-prod-cache`, or restart policy),
  or have publish_layer verify a 1-px GetMap and bust the path (new object name)
  on failure.
- **LOW**: `_infer_style_preset` — a flood product whose id/uri contains a terrain
  token (`flood-relief-comparison`, `flood-near-steep-slope`) silently loses the
  flood ramp (terrain tokens win the intersection).
- **LOW**: SYSTEM_PROMPT publish exemption keys on "function_response contains a
  wms_url", but no tool result carries a literal `wms_url` field (flood workflow
  returns it as `LayerURI.uri`); worst case is a redundant publish_layer call.
- **INFO**: job-0269b's causal thesis ("depth pseudocolor clamped the RGBA bands
  → transparent") is contradicted by the Seattle control: the flood ramp renders
  terrain as a visible wrong-looking blue, not transparent. The preset fix is
  still the right rendering change; it just wasn't the invisibility root cause.
