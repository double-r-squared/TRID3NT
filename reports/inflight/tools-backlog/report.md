# Report: tools-backlog (tools-work branch)

**Branch:** `tools-work` (off `main` e3c3dda, isolated worktree)
**Specialist:** Tools / Agent-Config Specialist
**Task:** Pull the deferred-tool backlog (`reports/inflight/tools-backlog/KICKOFF.md`) top-down, in-seam, per the prototype-local-first -> promote -> test -> land-additive gate.

---

## TIER 1 #1 + #2 -- NOAA SLR raster siblings (LANDED)

`fetch_noaa_slr_confidence` + `fetch_noaa_slr_marsh` -- the `conf_*` / `marsh_*` siblings named in `fetch_noaa_slr_scenarios.py` (L77-78 / L732-734).

### What was built
- **NEW `tools/_noaa_slr_raster.py`** (shared internal, NOT a registered tool): the common NOAA OCM `dc_slr` MapServer `export` data path -> a georeferenced 4-band RGBA COG. Typed errors (`NOAASLRRasterInputError/UpstreamError/EmptyError`), bbox/res_deg validation, `export_slr_raster_cog_bytes`, `estimate_payload_mb_for`.
- **NEW `tools/fetch_noaa_slr_confidence.py`**: SLR mapping-confidence raster (whole-foot levels 0..10, `conf_<N>ft`). Style preset `noaa_slr_confidence`.
- **NEW `tools/fetch_noaa_slr_marsh.py`**: SLR marsh-migration raster (0..10 ft, 0.5-ft steps, `marsh_<NNN>`). Style preset `noaa_slr_marsh`; secondary-listed to `conservation_ecology`.
- **Registration (additive):** `tools/__init__.py` (2 imports) + `categories.py` (2 primary `coastal` + 1 secondary `conservation_ecology`) + `tool_query_corpus.yaml` (2 x 6 queries).
- **NEW `tests/test_fetch_noaa_slr_siblings.py`** (15 tests).

### Design notes
- Cloned the SLR-scenarios SCAFFOLDING (metadata / typed errors / cache shim / NOAA endpoint pattern) but the data path differs: scenarios is a VECTOR feature query; conf/marsh are SYMBOLIZED RASTER products, so the fetch is a `MapServer/export` of the rendered RGBA PNG georeferenced to a COG (the GLM transparent-overlay pattern). publish_layer's RGBA/multiband passthrough renders the baked symbology directly -- **no `publish_layer.py` edit, no new style-registry row** (fully in-seam).
- Honesty floor: a successful-but-fully-transparent export (no SLR coverage at that level) returns a valid transparent COG (empty overlay) + logs -- never fabricated; HTTP/parse failures raise the typed upstream error.
- Resolution is a user lever (`res_deg`, default ~50 m; finer opt-in, payload-warning-coupled).

### Verification (live, real NOAA + real S3)
- `TOOL_REGISTRY["fetch_noaa_slr_confidence"].fn(bbox=(-82.2,26.2,-81.5,26.9), slr_ft=3.0)` -> `LayerURI` -> COG `s3://grace2-hazard-cache-226996537797/cache/static-30d/noaa_slr_confidence/108dd983....tif`, **4-band uint8 EPSG:4326 1401x1400, 218 KB**; PNG `/tmp/slr_siblings_proto/conf_3ft.png` shows the real Fort Myers/Naples confidence map (blue high / orange low / transparent dry).
- `fetch_noaa_slr_marsh(... slr_ft=3.0)` -> COG `.../noaa_slr_marsh/1602bda3....tif`, 4-band EPSG:4326 1401x1400, 3.5 MB; `/tmp/slr_siblings_proto/marsh_300.png` shows the marsh-migration classes.
- Service mapping verified: `conf_3ft`, `marsh_300`/`marsh_050`/`marsh_1000`.
- Tests: `test_fetch_noaa_slr_siblings.py` 15 passed; `test_categories.py` + `test_tool_retrieval.py` (coverage gates) pass. **Full agent suite `-m "not live_gemini"` -> 7434 passed, 3 failed** -- the 3 failures are the PRE-EXISTING `test_granularity_gate.py` `swmm-api`-missing env drift (unrelated, confirmed pre-existing earlier).

### Status: LANDED on `tools-work` (additive). Orchestrator integrates -> `main`; box deploy is the Orchestrator-gated batch.

---

## TIER 1 #5 -- mongo_query stale-doc cleanup (LANDED, doc-only)
- `tools/README.md`: removed the dead `mongo_query` "canonical pass-through example" (Mongo torn down for DynamoDB 2026-06-16); the live-no-cache example is now `qgis_process`.
- `publish_layer.py`: de-Mongo'd the stale `MongoDB` / `RunDocument` doc drift (persistence is DynamoDB; `observe_published_layer` surfaces the published layer). Left the legitimate `gs://` dual-scheme refs (the GCP-dormant reversible seam).
- No behavior change.

## TIER 1 #3 -- QML preset batch (PARTIAL LANDED + slope/aspect FLAGGED to NATE)
- **Landed (clean colormap wins):** added `impervious_surface_pct` (0-100% -> `reds`) + `population_density` (people/pixel -> `magma`) to `_TITILER_STYLE_REGISTRY`, replacing the generic `continuous_dem` placeholder on `compute_impervious_surface` + `data_fetch` population. Verified: `impervious_surface_pct` -> `&rescale=0,100&colormap_name=reds`, `population_density` -> `&rescale=0,250&colormap_name=magma`. Colormap names are from the already-proven registry set.
- **DEFERRED -- ships with the styling UI (NATE 2026-06-24):** `compute_slope` / `compute_aspect` are single-band terrain rasters whose `source_class` cache URLs (`slope`/`aspect`) ALWAYS match the **F51 terrain-token passthrough** in `_resolve_titiler_style_params`, so they render GRAYSCALE regardless of preset name -- a DELIBERATE decision pinned by `test_publish_layer_titiler_style_resolver_f51.py:485-488` + `test_publish_layer_style_inference.py:13-14`. Giving slope/aspect a colormap reverses that tested passthrough behavior (a render-chokepoint change, not cosmetic). Per NATE: the slope-angle (`ylorrd 0,60`) + cyclic aspect (`hsv 0,360`) colormaps + their legends will ship TOGETHER with the styling UI as a tools+web bundle -- the note is recorded in `compute_slope.py` / `compute_aspect.py` / the `publish_layer.py` registry. **Hillshade SHOULD stay grayscale (shaded relief) -- no change wanted.**
- **NWS (`fetch_nws_event`, `nws_alerts`):** that is a VECTOR layer -- its color is the web's deterministic palette (`web/src/lib/vector_rendering.ts`), NOT a TiTiler colormap -> out of the tools seam (web concern), left as-is.
- Full agent suite 7438 passed (3 pre-existing swmm-api failures unrelated).

## Remaining backlog (NATE's start-now list) -- queued next
- TIER 1 #4 `fetch_usace_dams` authoritative-NID upgrade + reserved filter knobs.
- TIER 2 #7/#8 `compute_home_range_kde` + `compute_movement_trajectory` (glue over `fetch_movebank_tracks`).
- TIER 3 CPU pull-forwards #17: `run_deepforest_tree_crown` (CPU clone of canopy) + NDWI-only `digitize_water_body` split.
- DEFERRED (tools+web bundle): #3 slope/aspect colormaps + legends ship with the styling UI (note recorded in-code, NATE 2026-06-24).
