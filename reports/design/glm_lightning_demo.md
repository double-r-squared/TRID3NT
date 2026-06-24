# GLM Lightning Demo -- Design Doc (GOES-19 Visible + Group Energy Density)

Status: design / scoping. Owner: agent + engine specialists. Reuse target: the
existing `fetch_goes_archive_animation` fire-animation stack (Path B, raw S3
archive). Net-new surface is small and well-bounded.

## 1. The demo in one paragraph

Recreate CIRA/RAMMB SLIDER's "visible + group energy density" loop for a
Gulf-of-Mexico tropical cyclone. Over a multi-hour daytime window we animate a
**grayscale GOES-19 (GOES-East) ABI band-2 visible** base, and bake a
**GLM group-energy-density purple/violet overlay** on top of it: GLM Level-2
LCFA optical-lightning detections, gridded onto the ABI 2-km fixed grid,
accumulated into ~1-minute frames, and displayed in femtojoules (fJ) on a log
purple ramp. Lightning flickers as bright violet-to-white cells over the
grayscale storm, marching with the convection -- the canonical CIRA Gulf
cyclone post. It runs through the same window-snap -> per-frame fetch ->
publish -> scrubber pipeline the fire demo already uses, so Track A's composer
and the web `SequenceScrubber` consume it unchanged.

## 2. Data sources

All sources are AWS Open Data, anonymous (`Config(signature_version=UNSIGNED)`),
no credentials. Both layers come from the **same satellite, GOES-19 (East)** --
this is the Gulf satellite; do NOT pull goes-18 (West).

| Layer | Product | Bucket / key path | Cadence | Units | Access |
|---|---|---|---|---|---|
| Lightning overlay | GLM-L2-LCFA (point) | `s3://noaa-goes19/GLM-L2-LCFA/<YYYY>/<DDD>/<HH>/OR_GLM-L2-LCFA_G19_s<...>_e<...>_c<...>.nc` | ~20 s/file (180/hr, 3/min) | `group_energy` in **Joules** (point), display in **fJ** (gridded) | anonymous S3, NetCDF4 |
| Visible base | ABI-L2-MCMIPC band C02 (or ABI-L2-CMIPC C02) | `s3://noaa-goes19/ABI-L2-MCMIPC/<YYYY>/<DDD>/<HH>/OR_..._G19_s<...>.nc` | ~5 min/CONUS scan | C02 reflectance 0..1 (rendered grayscale 0,1) | anonymous S3, NetCDF4 |

Key-path notes (verified live in research):
- `<DDD>` is zero-padded **day-of-year / Julian day** (001-366); `<HH>` is
  zero-padded UTC hour. The fire stack's `_doy_hour` + `_list_keys_for_prefix`
  + `_key_start_datetime` parse this layout already.
- GLM filename timestamp grammar: `sYYYYDDDHHMMSST` (T = tenths of a second).
  20-s window per file (`s...1800000` start -> `e...1800200` end).
- A 4-hour loop = ~720 LCFA files/satellite (gridded to 240 one-minute frames),
  plus ~48 MCMIPC visible scans (one per 5 min). Handle hour and DOY rollover.
- GLM-L2-LCFA energies are TINY (`group_energy` ~1e-15 to 1e-12 J); the gridded
  display switches to fJ (x1e15) precisely so the numbers are legible.

## 3. HAVE vs GAP (grounded in the current fire tooling)

Anchor file: `services/agent/src/grace2_agent/tools/fetch_goes_archive_animation.py`
(the raw-archive fire-temperature animation). The lightning demo reuses roughly
75% of it. The reuse is the band/grid/overlay/bake/animation spine; the net-new
is the GLM point fetch + the point-to-grid rasterizer.

### HAVE (reuse, little-to-no change)
| Capability | Where it lives | Reuse |
|---|---|---|
| Anonymous S3 archive listing over a UTC window (Julian-day partitions, DOY/hour rollover) | `_list_archive_keys_in_window`, `_doy_hour`, `_hours_in_window`, `_list_keys_for_prefix`, `_key_start_datetime` | as-is, EXCEPT the product prefix is hardcoded to `ABI-L2-MCMIPC` -- must be param/duplicated for `GLM-L2-LCFA` (gotcha below) |
| Shared EPSG:4326 output grid for a bbox | `_grid_for_bbox(bbox)` | as-is -- the GLM rasterizer bins onto THIS exact grid so overlay + base are co-registered |
| Per-band CF-scaled reproject of an ABI band to the grid | `_warp_band_to_physical`, `_read_archive_bands` | as-is for the C02 visible base (single-band grayscale instead of C07/C06/C05 RGB) |
| Per-frame `read_through` cache (independent SHA-256 key per timestamp) | frame loop in `fetch_goes_archive_animation` | as-is (new cache key tuple includes product + accumulation window) |
| RGBA overlay -> alpha-composite over an RGB base | `_bake_fire_over_base(base_rgb, fire_rgba)` | as-is -- swap the fire RGBA input for the purple GED RGBA |
| Multiband / RGBA COG passthrough render (no new style preset) | `publish_layer` `_is_rgba_or_multiband` passthrough | as-is -- the baked frame renders without a new preset |
| Grayscale single-band visible preset | `publish_layer.py:527` `"goes_visible": ("0,1", "gray")` | as-is for any non-baked grayscale path; preset already exists |
| Ordered `list[LayerURI]` shape + name token + window-snap workflow + web scrubber | `model_goes_fire_animation` workflow, `SequenceScrubber` | as-is -- the lightning workflow is a clone with new fetch + new label token |

### GAP (net-new)
| Net-new piece | Why it is new |
|---|---|
| `fetch_glm_lightning` -- GLM-L2-LCFA point fetcher | GLM granules are NetCDF point records (events->groups->flashes), not ABI rasters; reads `group_lat`/`group_lon`/`group_energy`. The MCMIPC band reader cannot consume them. |
| Group-energy-density (GED) rasterizer -- point-to-grid | The GED field does NOT exist in the LCFA file; it must be computed by binning point `group_energy` onto the 2-km grid over a 1-min window. This is the core new algorithm. |
| Purple log ramp RGBA for GED | New colorizer (violet-to-white, log stretch) producing the RGBA the existing `_bake_fire_over_base` baker consumes. |
| Product-prefix parameterization | `_list_archive_keys_in_window` is hardcoded to MCMIPC; needs a GLM-L2-LCFA path (and the GLM granule does NOT carry "MCMIPC" so the in-loop guard must change). |

## 4. The group-energy-density method (the exact CIRA product)

The CIRA overlay you are recreating is `cira_glm_l2_group_energy`
("Group Energy Density"). It is a GRIDDED derived product (canonical
implementation: CIMSS Gridded GLM on Eric Bruning's `glmtools`), NOT a field in
the LCFA file. Our v0.1 builds a faithful, simpler version of it:

- **Grid**: the ABI 2-km fixed grid -- in our stack, the shared EPSG:4326 bbox
  grid from `_grid_for_bbox` (the same grid the C02 base warps onto, so overlay
  and base are pixel-co-registered). GLM's native optical footprint is ~8 km at
  nadir; the 2-km grid is finer than the footprint. The full glmtools approach
  reconstructs each event's true polygon footprint from a corner-point lookup
  and distributes energy by fractional pixel coverage. v0.1 SIMPLIFICATION: bin
  each group's energy into the single grid cell containing its centroid
  (`numpy.add.at` on flattened cell indices). Footprint-spreading is a later
  fidelity upgrade.
- **Accumulation window per frame**: **1 minute** (the operational CIRA
  convention) = three 20-s LCFA files merged per frame. Sum `group_energy` of
  all groups in the window into their grid cells. For a 4-hour loop that is 240
  GED frames. Each visible base scan (~5 min) is reused across ~5 consecutive
  GED frames (or snap each GED frame to the nearest base scan).
- **What is accumulated**: `group_energy` (Joules), summed per cell per minute,
  then converted to **femtojoules** (x1e15) for display.
- **Purple ramp**: the dynamic range spans many orders of magnitude, so display
  on a **logarithmic** stretch -- a violet/magenta-to-white ramp (low = faint
  purple, high = bright white/pink), zero/empty cells fully transparent. Pick a
  fixed fJ ceiling (or log-percentile) so the ramp is stable frame-to-frame and
  the loop does not flicker on auto-scale. Output RGBA -> hand to
  `_bake_fire_over_base` over the grayscale C02 base.

## 5. Ordered build plan (smallest-first)

Each step ties to a file, marks reuse-vs-new, and gives a rough effort. Total
~5-6 days. Build and validate bottom-up; the local-first recipe (section 7)
proves steps 2-4 against real data BEFORE any of this is promoted to tools.

1. **`fetch_glm_lightning` -- GLM-L2-LCFA point fetcher** (S, ~1 day, NEW tool)
   - File: `services/agent/src/grace2_agent/tools/fetch_glm_lightning.py` (new),
     reusing `fetch_goes_satellite` S3 list primitives (`_doy_hour`,
     `_list_keys_for_prefix`, `_key_start_datetime`) and the
     `noaa-goes19 -> goes-19` bucket map (already present in `_SATELLITE_BUCKETS`).
   - Parameterize the product prefix to `GLM-L2-LCFA` (the MCMIPC prefix and the
     `"MCMIPC" in k` guard in `_list_archive_keys_in_window` are hardcoded; copy
     or param it). List the window, open each ~20-s granule with netCDF4/xarray,
     read `group_lat`, `group_lon`, `group_energy` (and `group_quality_flag`),
     return per-window point arrays. Honesty floor: empty window -> typed error.

2. **GED rasterizer -- point-to-grid** (M, ~1.5 days, NEW)
   - File: same new module (a `_group_energy_density(...)` helper) using
     `_grid_for_bbox(bbox)` from `fetch_goes_archive_animation` for the shared
     grid + transform.
   - Bin `group_energy` into grid cells via `numpy.add.at`, summed over the
     1-min window; convert J -> fJ. Returns a `(H, W)` energy array on the
     shared grid. This is the load-bearing net-new algorithm.

3. **Grayscale visible C02 base band** (XS, ~0.5 day, REUSE)
   - File: `fetch_goes_archive_animation.py` -- generalize the band reader to
     read **C02** via the existing `_read_archive_bands` / `_warp_band_to_physical`
     on the same grid, render single-band grayscale (the
     `publish_layer.py:527` `goes_visible` `("0,1","gray")` preset already
     exists). No new render path.

4. **Purple GLM overlay + bake-over-visible** (XS, ~0.5 day, REUSE + thin NEW)
   - File: new module + reuse `_bake_fire_over_base`. New: a purple log-ramp
     colorizer turning the GED `(H,W)` array into RGBA (mirrors
     `_fire_hotspots_rgba`'s role, violet instead of red, transparent zeros);
     then `_bake_fire_over_base(c02_gray_rgb, ged_purple_rgba)` -> baked RGB(A)
     COG. Renders via the existing `_is_rgba_or_multiband` passthrough -- no new
     preset.

5. **`model_glm_lightning_animation` workflow** (M, ~1.5 days, REUSE clone)
   - File: `services/agent/src/grace2_agent/workflows/model_glm_lightning_animation.py`
     (clone of `model_goes_fire_animation.py`).
   - Window-snap -> per-1-min-frame: fetch GLM points + GED-rasterize + read the
     nearest C02 base + bake -> publish -> ordered `list[LayerURI]` with a new
     "GLM Group Energy Density step <N> <ISO> (G19)" name token. Same shape the
     scrubber consumes. No confirm gate (matches the fire workflow).

## 6. Gotchas (carry into every sub-job)

- **GLM is POINTS, not a raster.** The granule is NetCDF4 point records
  (events->groups->flashes); you read `group_*` arrays and BIN them onto the
  grid. You cannot drop it in like an ABI band -- there is no GED field in the
  file; you compute it.
- **GLM lat/lon carry parallax baked in; bin directly, do NOT reproject like an
  ABI band.** GLM centroids are already geolocated (with parallax); feed
  lat/lon straight into the grid binning. Do not run them through
  `_warp_band_to_physical` (that is for the geostationary fixed-grid ABI bands).
- **GOES-19 East, not goes-18 West.** Lightning + C02 base both come from
  `noaa-goes19` (the Gulf satellite). goes-18 is the Pacific/West satellite --
  wrong sector for a Gulf TC.
- **20-s granules, ~15 per visible base frame.** GLM is ~20 s/file (180/hr);
  ABI CONUS is ~5 min/scan. ~15 GLM granules accumulate per visible scan; per
  1-min GED frame it is 3 GLM files. Drive frame timing off the 1-min GED
  window, reuse the nearest C02 scan.
- **Huge dynamic range.** `group_energy` spans ~1e-15 to 1e-12 J. Use a log
  stretch or a fixed fJ ceiling for the purple ramp, fixed across the loop, so
  frames do not flicker on auto-scale.
- **Units.** L2 energy is JOULES; the display product is femtojoules (x1e15).
  Convert at the colorizer.
- **Daytime visible only.** C02 is reflectance -- it goes black at night. For a
  multi-hour loop that crosses night, the visible base degrades to black. Two
  options: (a) restrict the demo window to daylight over the AOI, or (b) at
  night swap the base to an IR band (e.g. C13 longwave window, grayscale,
  inverted so cold cloud-tops are bright) -- a clean fallback since the band
  reader is already general. v0.1: daylight window; IR-base fallback is a
  follow-up.
- **Product prefix is hardcoded.** `_list_archive_keys_in_window` hardcodes
  `ABI-L2-MCMIPC` and guards on `"MCMIPC" in k`; the GLM path needs
  `GLM-L2-LCFA` and a different guard. Param it rather than fork the whole lister.
- **RGBA passthrough, no new preset.** The baked overlay renders through
  `publish_layer`'s `_is_rgba_or_multiband` passthrough -- do not add a style
  preset for it.

## 7. Local-first prototype recipe (validate before promoting to tools)

Per NATE's pattern: prove the grayscale base + GED overlay against REAL data
with a direct-call script first, then promote the proven logic into the tools
above. No agent, no workflow, no S3 listing infra -- just numpy + rasterio +
the existing reproject/bake helpers, run on a known Gulf-TC daylight window.

Recipe (a standalone script, e.g. `scratch/glm_proto.py`, NOT committed as a tool):
1. **Pick a known event window.** A Gulf-Coast TC at a daylight UTC hour (so
   C02 is bright). Note bbox + a 10-30 min start/end window to keep the proto fast.
2. **Pull a few real granules anonymously** with
   `boto3.client("s3", config=Config(signature_version=UNSIGNED))`:
   - one ABI-L2-MCMIPC G19 scan (for C02), and
   - the ~30-90 GLM-L2-LCFA G19 granules covering the window
     (`s3://noaa-goes19/GLM-L2-LCFA/<YYYY>/<DDD>/<HH>/`).
3. **Build the grayscale base.** Reuse `_grid_for_bbox(bbox)` +
   `_warp_band_to_physical(nc, "CMI_C02", ...)` (import from
   `fetch_goes_archive_animation`) to warp C02 onto the grid; stretch to
   grayscale uint8. Confirm the storm is visible.
4. **Build the GED grid.** Read `group_lat/group_lon/group_energy` from each GLM
   granule with netCDF4/xarray; bin into the SAME grid with `numpy.add.at` over
   the 1-min window; convert to fJ; log-stretch -> purple RGBA (transparent zeros).
5. **Bake + write a PNG/COG.** `_bake_fire_over_base(c02_rgb, ged_rgba)`; write a
   PNG locally and eyeball it against the CIRA SLIDER `cira_glm_l2_group_energy`
   overlay for the same time -- purple energy cells should sit over the
   convective towers, marching with the storm.
6. **Iterate the ramp + binning** (fixed fJ ceiling vs log percentile,
   centroid-cell vs footprint-spread) until it matches CIRA, THEN lift the
   proven functions into steps 1-4 above and clone the workflow (step 5).

This closes the loop on the only two genuinely new pieces (the GLM read + the
GED rasterizer/ramp) against real bytes before any tool/workflow wiring, exactly
where the risk is.
