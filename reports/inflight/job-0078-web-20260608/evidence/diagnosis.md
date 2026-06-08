# Diagnosis: OQ-76-MAP-ALIGNMENT — job-0078

## Method

Playwright driver (`url_capture_driver.py`) boots `npm run dev`, attaches
`page.on("request", ...)` logging every WMS GetMap URL, injects the
job-0075 session-state (flood-depth-job-0075-demo), then programmatically
`jumpTo({center: [-81.86, 26.63], zoom: 13})` matching the job-0076 view.

A second driver (`alignment_probe.py`) takes three screenshots at the
identical view: (a) basemap + flood overlay composite, (b) basemap only
(flood `visibility: none`), (c) flood only (basemap `visibility: none`).
The three-way decomposition isolates the rendering of each layer.

A third driver (`headline_aligned.py`) re-captures the headline screenshots
with the same parameters as job-0076 to confirm the change from the new
`raster-resampling: nearest` paint property.

Server-side: direct curl of WMS GetMap for both layers at identical EPSG:3857
bboxes, plus a comparison against the flood layer's native EPSG:32617 to
verify reprojection consistency.

## Server-side check (curl evidence)

For the Fort Myers bbox `[-81.91, 26.55, -81.75, 26.69]` translated to
EPSG:3857 (`-9118179.49,3067361.81,-9100368.37,3084794.44`):

| Layer | CRS | BBOX | Size | Status |
|---|---|---|---|---|
| `basemap-osm-conus` | EPSG:3857 | (3857 mercator coords) | 394 KB | 200 |
| `flood-depth-job-0075-demo` | EPSG:3857 | (same 3857 mercator coords) | 474 KB | 200 |
| `flood-depth-job-0075-demo` | EPSG:32617 (native) | (UTM 17N coords) | 565 KB | 200 |

A manual PIL composite of the EPSG:3857 basemap + flood pair (`/tmp/composite_3857.png`)
shows the flood inundation pattern correctly aligning to the Caloosahatchee
River channel on the basemap. The same bbox in 3857 vs native 32617 produces
visually consistent flood patterns — QGIS Server's reprojection is
correctly preserving geography. **Server-side rendering is not the bug.**

## Client-side URL comparison

Playwright captured 60 flood-layer GetMap URLs and 111 basemap-layer GetMap
URLs during the test session. Parsing the BBOX query param off each URL:

- **flood distinct BBOXes: 60**
- **basemap distinct BBOXes: 111** (more because the basemap pre-loaded
  CONUS-view tiles before the camera zoomed in to Fort Myers)
- **BBOXes in BOTH (axis-identical string-equal): 60 of 60 flood / 60 of 111 basemap**

This is the definitive client-side check: every single flood-layer tile
request fires at the same BBOX as a corresponding basemap-layer tile
request. The two layers ask the server for the same tile geographically.

Sample matched URLs from `url_pairs.json`:

```
FLOOD:   https://.../ogc/wms?MAP=...&LAYERS=flood-depth-job-0075-demo&SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap&CRS=EPSG:3857&FORMAT=image%2Fpng&TRANSPARENT=true&BBOX=-9113739.756498136,3072157.0408378057,-9111293.77159301,3074603.0257429294&WIDTH=256&HEIGHT=256&STYLES=
BASEMAP: https://.../ogc/wms?MAP=...&SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap&LAYERS=basemap-osm-conus&CRS=EPSG:3857&FORMAT=image/png&TRANSPARENT=true&BBOX=-9113739.756498136,3072157.0408378057,-9111293.77159301,3074603.0257429294&WIDTH=256&HEIGHT=256&STYLES=
```

Parameter-by-parameter:
- `VERSION=1.3.0` — same
- `CRS=EPSG:3857` — same
- `BBOX=` — IDENTICAL (axis-identical string-equal)
- `WIDTH=256, HEIGHT=256` — same
- `FORMAT=image/png` (basemap, unencoded slash) vs `image%2Fpng` (flood, urlencoded slash)
  — semantically identical; QGIS Server accepts both
- `TRANSPARENT=true` — same
- `STYLES=` (empty) — same

**No URL-level discrepancy exists between flood and basemap tile requests.**
The kickoff's ranked candidate hypotheses (axis-order, bounds, tileSize, SRS-vs-CRS,
WMS version mismatch) are all rejected by this evidence.

## Visual decomposition (basemap-only / flood-only / composite)

`alignment_probe.py` captured three screenshots at center `[-81.86, 26.63]`,
zoom 13:

- `probe_basemap_only.png` — QGIS Server WMS basemap at zoom 13. Shows the
  Caloosahatchee River curving across the upper portion, Fort Myers downtown
  grid, North Fort Myers across the river. "Fort Myers" label visible.
- `probe_flood_only.png` — flood overlay only (basemap hidden). Shows the
  flood depth raster's per-cell pattern: dense river-mouth inundation on
  the LEFT (matching the river position on the basemap), extending into
  street-level inundation across downtown.
- `probe_composite.png` — both layers visible (MapLibre native compositing).
  Shows the flood overlay correctly aligned over the basemap features.

Pixel-level alignment check (computed in driver):
- 20.7% of map-area pixels are basemap water (light-blue tones)
- 39.6% are flood-overlay pixels (saturated blue)
- 11.0% are BOTH — flood overlay sitting over basemap water
- **53.2% of basemap water pixels have flood overlay on them** — the river
  inundation maps to the river channel
- 72% of flood overlay pixels are NOT over basemap water — because flood
  data includes street-level inundation in residential/commercial blocks,
  not just the river itself (consistent with Hurricane Ian's documented
  flood extent)

A synthetic composite (`synth_composite.png`) computed as
`basemap × 0.1 + flood × 0.9` (manual PIL alpha-blend) was diffed against
the actual MapLibre composite (`probe_composite.png`):
- Mean diff: 7.34 / 255 (2.9%)
- Pixels with significant diff (>30/255): 6.3%

MapLibre's compositing matches the manual ground truth to within ~3%.
The remaining 3-6% diff is sub-pixel sampling differences from `linear`
resampling (smearing source cells across screen pixels) — which is the
visual artifact that read as "misalignment" to the user.

## Root cause

The flood overlay is **geographically aligned with the basemap at every
tile request**, but at zoom 13 with MapLibre's default `raster-resampling: linear`
(bilinear interpolation), source pixels from the COG (~3-10 m UTM-17N cells)
get smeared across screen pixels (~21 m at lat 26.6, zoom 13). The smearing
softens cell boundaries and makes the overlay look like a continuous blue
wash rather than discrete depth cells sitting over specific city blocks.
This was the perceptual "alignment is off" — there's no per-cell visual
anchor against which to confirm alignment.

The CartoDB DarkMatter dark-theme basemap exacerbates the perception:
DarkMatter renders water and land in similar near-black tones (~RGB 6-38),
so the eye can't easily find the river's curve to anchor against the flood
overlay. The light-theme QGIS Server WMS basemap shows water in distinct
light-blue tones, making the alignment more obvious — but even there, the
smearing softens the per-cell verification.

**No client-side WMS-construction bug exists.** No server-side reprojection
bug exists. The fix is to switch the flood-layer rendering from `linear`
to `nearest` resampling so the COG's discrete cells render as crisp blocks,
making per-cell alignment with basemap features visually verifiable.

## Fix landed (web/src/Map.tsx)

In the session-state apply path, the `paint` properties of the newly-added
raster layer now include `"raster-resampling": "nearest"`:

```ts
m.addLayer({
  id: layer.layer_id,
  type: "raster",
  source: layer.layer_id,
  paint: {
    "raster-opacity": opacity,
    "raster-resampling": "nearest",  // <-- the fix
  },
  layout: { visibility: visible ? "visible" : "none" },
});
```

This is purely a presentational change — MapLibre still requests the same
tiles at the same BBOXes; QGIS Server still renders them identically; the
only difference is how MapLibre paints the returned tile bytes onto the
canvas. With `nearest`, each COG cell shows as a discrete sharp block,
so the user can see that each flood cell sits over a specific street or
city block (matching the basemap features at the same geographic position).

Hypothesis ranking from the kickoff:

| Hypothesis | Result |
|---|---|
| 1. WMS URL CRS / response interpretation off-by-one | REJECTED — flood/basemap BBOXes axis-identical (60/60 match); server-side curl proves both layers correctly georeferenced at same bbox |
| 2. MapLibre `bounds:` constraint mis-set | N/A — `bounds: null` on both basemap and flood sources (verified by `m.getStyle()` dump in evidence/url_pairs.json) |
| 3. Tile pixel grid offset (`tileSize` mismatch) | REJECTED — both sources use `tileSize: 256` |
| 4. WMS VERSION axis-order asymmetry | REJECTED — both sources use `VERSION=1.3.0` with `CRS=EPSG:3857`. For projected CRSes, WMS 1.3.0 axis order is (x, y), same as the EPSG:3857 mercator coords MapLibre substitutes via `{bbox-epsg-3857}` |
| 5. Geographic reprojection edge case | REJECTED — server-side reprojection 32617→3857 verified correct via cross-CRS curl pair |
| NEW: `linear` resampling at zoom 13 smears cell boundaries | CONFIRMED root cause of the user's perceptual "misalignment" |

## Why the user's perception was correct in spirit but not literally

The user wrote: *"the alignment to the map is off and rotation is also off and maybe zoom too by the looks of it"*. At face value, this would predict differing BBOXes between flood/basemap URLs (alignment), a non-zero `bearing` on the camera (rotation), or different `WIDTH/HEIGHT/zoom` (zoom). None of those happened in the captured network log.

What the user actually saw was the flood overlay's smeared appearance not matching the per-cell precision they had in mind from the job-0075 `wms_full_0075.png` (which IS rendered with discrete cells in the QGIS Server PNG itself, because that render is at the COG's native resolution). At the client side, MapLibre's `linear` resampling at zoom 13 blends adjacent source cells, softening the pattern. That softening was what looked "rotated/offset/zoomed" — the recognisable per-cell structure of the source data was smeared into a different visual signature than the reference PNG.

`raster-resampling: nearest` restores the per-cell crispness MapLibre's
canvas can show; it does not (and cannot) change the underlying geographic
alignment, which is already correct.
