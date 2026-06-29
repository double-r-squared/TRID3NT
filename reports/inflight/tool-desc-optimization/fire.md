# Tool-description optimization -- fire (8 tools)

**Branch:** `agent/render-honesty-audit`. Docstrings + type annotations only; no logic/return changes.
Same standard + mechanism as `hazard_modeling.md`.

## Verification
All 8: routing block within first 1000 chars; ASCII-clean; no GCP-infra in first-1000; default-in-Literal
clean; `py_compile` clean. Siblings verified registered. Full pytest left for integration.

## Literal lifts (verified; defaults in-set)
- `fetch_viirs_day_fire.satellite` -> suomi-npp/noaa-20/noaa-21/all; `.product` -> day_fire.
- `fetch_nifc_fire_perimeters.status` -> active/controlled/out/all.
Existing kept: `fetch_firms_active_fire.source`, `fetch_landfire_fuels.layer` (fbfm40/fbfm13/cbh/cbd),
`fetch_usfs_canopy_fuels.layer` (cbh/cbd). Left `str`: `fetch_goes_active_fire.satellite` (open spellings
via `_normalize_satellite`), `sector` slugs, year ranges.

## Notes
`fetch_wfigs_incident` flagged DATA-ONLY resolver (returns a dict, NOT a LayerURI; feeds its bbox to the
animation tools) -- prevents a false "incident on the map" claim. `fetch_viirs_day_fire`/`fetch_goes_active_fire`
return `list[LayerURI]` animations (frames auto-render on main). Active-fire (points) vs animation (raster)
vs post-fire (mtbs/nifc) disambiguated. GCP run-cache purged; public FIRMS/MTBS/NIFC/LANDFIRE/USFS/GOES sources kept.
