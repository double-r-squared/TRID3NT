# job-0271 — terrain tools emit real COGs (orchestrator-direct Fable, hot fix)

**Defect:** gdaldem writes flat strip-organized GTiffs (no tiling, no
overviews; 1788 strips for a city-scale relief). QGIS Server rendering one
over /vsigs/ issues a range request per strip — slower than the 60s WMS
gateway limit, and the likely trigger of the cold-load layer poisoning the
job-0270 verifier isolated. Flood products never hit this because
postprocess_flood COG-ifies its outputs.

**Fix:** shared `_translate_to_cog` (compute_hillshade.py) — gdal_translate
-of COG -co COMPRESS=DEFLATE, non-fatal fallback to flat bytes — wired into
all four terrain tools (hillshade both styles, colored_relief, slope, aspect).

**Evidence:** live Gemini-free run on the real Boulder DEM: output now
tiled=True block=512 overviews=[2,4] EPSG:5070, 354KB vs 9.3MB flat. Terrain
+ publish test files 72 passed / 4 skipped. Commit in the job-0271 message.

**Companion env work (user-run):** CPL_VSIL_CURL_NON_CACHED removed from the
QGIS service after it pushed flat-GTiff renders past the gateway timeout
(GDAL_DISABLE_READDIR_ON_OPEN=EMPTY_DIR retained); 12 stale flat cache
artifacts purged (enumerated, user-approved). Propose mirroring the env
state into infra/qgis-server.tf at next infra job.
