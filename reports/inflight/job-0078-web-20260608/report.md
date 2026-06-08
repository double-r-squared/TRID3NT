# Report: OQ-76-MAP-ALIGNMENT — flood overlay alignment diagnosis + raster-resampling fix

**Job ID:** job-0078-web-20260608
**Sprint:** sprint-11
**Specialist:** web
**Task:** Diagnose why the Fort Myers flood overlay rendered by job-0076 appears
geographically misaligned with the basemap; land the minimum fix; capture
zoom-13 light + dark screenshots that visibly demonstrate alignment.
**Status:** ready-for-audit

---

## Summary

The flood overlay is **geographically aligned with the basemap at every tile
request**. MapLibre constructs flood-layer and basemap-layer WMS tile URLs
with byte-for-byte identical `BBOX=` parameters (60 of 60 flood URLs match a
basemap URL with the same bbox); QGIS Server returns correctly-georeferenced
PNGs for those bboxes (verified by direct curl); MapLibre composites them
correctly (verified by a manual PIL composite matching the rendered output
to ~3% mean pixel diff).

The user's perception of misalignment was driven by MapLibre's default
`raster-resampling: linear` smearing the COG's discrete depth cells across
screen pixels at zoom 13 — the smeared appearance hid the per-cell visual
anchors against the basemap features.

**Fix landed**: added `"raster-resampling": "nearest"` to the flood-layer's
paint properties in `web/src/Map.tsx`. This is a one-property change that
renders each COG cell as a discrete sharp block, making per-cell alignment
with basemap streets/blocks visually verifiable.

**Acceptance**: light + dark zoom-13 screenshots captured (`evidence/aligned_light.png`,
`evidence/aligned_dark.png`). Basemap-only counterparts also captured
(`evidence/aligned_light_basemap_only.png`, `evidence/aligned_dark_basemap_only.png`)
proving the geographic view is unchanged when the flood overlay is hidden —
the only difference between the basemap-only and with-flood pairs is the
overlay sitting in its correct position. Side-by-side composites at
`evidence/alignment_proof_*.png`.

---

## Part 1 — Diagnosis

### Method (Playwright + curl)

Built three Playwright drivers under `evidence/`:

1. `url_capture_driver.py` — captures every WMS GetMap URL fired by
   MapLibre during a session-state injection + jumpTo(zoom 13) round-trip.
   Categorises flood-layer vs basemap-layer URLs and compares their BBOX
   query params.

2. `alignment_probe.py` — captures three screenshots at center
   `[-81.86, 26.63]`, zoom 13: (a) basemap + flood composite, (b) basemap
   only (flood `visibility: none`), (c) flood only (basemap `visibility: none`).
   The three-way decomposition isolates each layer's contribution and lets
   me check alignment by overlaying the basemap-only with the flood-only.

3. `headline_aligned.py` — re-captures the headline screenshots in light +
   dark themes with the fix applied. Same center/zoom/panel-close pattern
   as job-0076's `headline_driver.py` so the comparison is apples-to-apples.

In parallel, direct `curl` requests for the WMS GetMap endpoint at the same
EPSG:3857 bbox for both flood-layer and basemap-layer, plus a cross-check
of the flood layer in its native EPSG:32617 (UTM 17N) to verify the QGIS
Server reprojection 32617→3857 is internally consistent.

### Key findings

| Check | Result | Evidence |
|---|---|---|
| flood/basemap WMS URLs use same BBOX | YES — 60/60 match | `evidence/url_capture.log`, `evidence/url_pairs.json` |
| flood/basemap WMS URLs use same VERSION/CRS | YES — both VERSION=1.3.0, CRS=EPSG:3857 | same |
| flood/basemap WMS URLs use same WIDTH/HEIGHT | YES — 256x256 | same |
| flood/basemap source `bounds:` constraint differs | NO — both `bounds: null` | `m.getStyle()` dump in `url_pairs.json` |
| flood/basemap source `tileSize` differs | NO — both 256 | same |
| Server-side correct at same bbox | YES — manual composite of curl'd EPSG:3857 tiles shows flood inundation aligned to basemap river | `/tmp/composite_3857.png` |
| Server-side EPSG:32617 (native) matches EPSG:3857 (reprojected) | YES — flood pattern visually equivalent in both CRSes | `/tmp/flood_32617.png` vs `/tmp/flood_3857.png` |
| MapLibre compositing matches manual ground truth | YES — synth composite within ~3% mean pixel diff | `evidence/synth_composite.png` |

The five hypotheses the kickoff ranked (URL CRS, MapLibre bounds, tileSize,
WMS axis-order, reprojection edge case) are all rejected by the data. The
actual root cause is presentation: MapLibre's default `linear` (bilinear)
resampling at zoom 13 smears the COG's ~3-10 m UTM cells across the screen's
~21 m/px scale (at lat 26.6), softening cell boundaries enough that the
visual signature of per-cell flood depth gets lost — making the overlay
look like a continuous wash without anchor points to verify alignment.

The dark CartoDB DarkMatter basemap exacerbated the perception because
DarkMatter renders water and land in similar near-black tones (RGB 6-38
verified by pixel sampling), so the eye couldn't anchor against the
river's curve.

Full diagnosis report at `evidence/diagnosis.md` with raw URL evidence,
pixel statistics, and the five-hypothesis ranking.

---

## Part 2 — Fix

### Change (web/src/Map.tsx)

In the session-state apply path, the newly-added raster layer's `paint`
properties now include `"raster-resampling": "nearest"`:

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

This is purely a client-side presentational change. MapLibre still requests
the same tiles at the same BBOXes; QGIS Server still renders them identically.
The only difference is how MapLibre paints the returned tile bytes onto the
canvas: `nearest` shows each source pixel as a discrete sharp block (1:1
mapping at the source's native cell size). Cells smaller than a screen pixel
are sampled at the center; cells larger than a screen pixel show as crisp
blocks.

For a flood-depth COG with ~3-10 m UTM-17N cells viewed at zoom 13
(~21 m/screen-px at lat 26.6), `nearest` produces a clearly pixelated
overlay where each cell sits over a specific street/block — making
per-cell geographic alignment visible.

A block-comment in `Map.tsx` documents the rationale + reference to this
job's evidence.

### Test (web/src/Map.test.tsx)

New regression test asserts that the flood-layer's paint properties
include `"raster-resampling": "nearest"`:

```ts
it("sets raster-resampling: nearest so per-cell alignment is visually verifiable (job-0078)", () => {
  ...
  expect(paint["raster-resampling"]).toBe("nearest");
  expect(paint).toHaveProperty("raster-opacity");
});
```

Test passes; total vitest count went from 72 to 73.

---

## Part 3 — Verification (live E2E evidence)

### Final screenshots

`headline_aligned.py` re-ran with the fix applied. Center `[-81.86, 26.63]`,
zoom 13, panels closed (same view as job-0076's headline shots):

- **`evidence/aligned_light.png`** — light theme. QGIS Server WMS basemap
  visible with the Caloosahatchee River curving across the upper portion.
  Flood overlay (lighter blue tint) sits over the river AND extends to
  street-level inundation throughout downtown.

- **`evidence/aligned_dark.png`** — dark theme. CartoDB DarkMatter basemap
  visible. Flood overlay now shows as discrete pixelated cells (per-cell
  visible because of `raster-resampling: nearest`). The bright-blue
  river-mouth inundation on the LEFT matches the river's location on the
  basemap; the lighter-blue per-cell pattern in the middle of the frame
  shows street-level inundation across the downtown grid.

For each, a matching basemap-only screenshot was also captured
(`aligned_light_basemap_only.png`, `aligned_dark_basemap_only.png`) by
toggling the flood layer's visibility off. The basemap-only and with-flood
pairs are at the IDENTICAL camera state — the only difference is the
overlay's presence. This makes the alignment obvious: features visible in
basemap-only (river, streets) sit in the same screen position in the
with-flood version, with the flood overlay's blue sitting over the
geographically-correct cells.

### Composite proof images

- **`evidence/alignment_proof_four_up.png`** — 4-up grid: light basemap,
  light + flood, dark basemap, dark + flood.

- **`evidence/alignment_proof_side_by_side.png`** — close-up crop of dark
  basemap vs dark + flood, showing the per-cell pixelation aligning to
  the downtown grid.

- **`evidence/alignment_proof_light_side_by_side.png`** — same close-up
  but light theme.

### Network evidence (verification.json)

```json
{
  "center": [-81.86, 26.63],
  "zoom": 13,
  "flood_url_count": 60,
  "basemap_url_count": 111,
  "shared_bbox_count": 60,
  "flood_bbox_count": 60,
  "basemap_bbox_count": 111
}
```

All 60 flood-layer GetMap URLs share their BBOX exactly with a basemap-layer
GetMap URL. The 111 basemap URLs > 60 flood URLs is because the basemap
loaded CONUS-level tiles before the camera zoomed in to Fort Myers.

---

## Changes Made

- **`web/src/Map.tsx`** — Added `"raster-resampling": "nearest"` to the
  paint properties of the dynamically-added flood-layer.

- **`web/src/Map.test.tsx`** — Added one regression test asserting the
  `raster-resampling: nearest` paint property is set on the added layer.

- **`reports/inflight/job-0078-web-20260608/STATE`** — `created` →
  `in-progress` → `ready-for-audit`.

- **`reports/inflight/job-0078-web-20260608/evidence/`** — added diagnosis.md,
  url_capture_driver.py, url_capture.log, url_pairs.json, alignment_probe.py,
  probe_*.png, synth_composite.png, headline_aligned.py, aligned_*.png,
  verification.json, alignment_proof_*.png, four_up.png, capture_zoom13.png.

No edits to FROZEN paths (`App.tsx`, `LayerPanel.tsx`, `ws.ts`, `contracts.ts`,
`style-presets.ts`, services/, packages/, infra/, docs/, styles/).

---

## Decisions Made

- **Decision: fix is `raster-resampling: nearest`, not a URL-construction change.**
  - Rationale: the diagnosis evidence definitively shows MapLibre constructs
    flood-layer and basemap-layer URLs with identical BBOX/VERSION/CRS/SIZE,
    and the server returns correctly-aligned tiles at those bboxes. The
    smearing artifact is at the *rendering* layer, not the request layer.
  - Alternatives considered: (a) force WMS VERSION=1.1.1 — rejected, the
    server returns correct 1.3.0 responses; (b) Add MapLibre `bounds:` —
    rejected, `bounds:` clips the source not re-aligns it; (c) Manually
    re-project the COG client-side — rejected, that's exactly the kind of
    "client-side workaround for a non-bug" the kickoff warns against.

- **Decision: do not override the wire-payload `opacity`.**
  - Rationale: opacity is the agent's contract per `ProjectLayerSummary.opacity`.
    Changing it client-side would mute the flood-depth signal across all
    future jobs. The user's perception was a resampling problem, not an
    opacity problem.

- **Decision: present both basemap-only and basemap+flood screenshots.**
  - Rationale: a basemap-only / with-flood pair at the IDENTICAL camera
    state is the cleanest possible visual proof — the only thing changing
    between the two is the flood overlay's presence.

---

## Invariants Touched

- **Invariant 1 (Determinism boundary):** preserves — no numbers computed
  by the client. `raster-resampling: nearest` is a presentation parameter.
- **Invariant 4 (Rendering through QGIS Server):** preserves — Tier B
  raster visualization still goes through QGIS Server WMS.
- **Invariant 5 (Tier separation):** preserves — no `gs://` URL fetched
  by the browser.
- **Invariant 8 (Cancellation first-class):** untouched.

---

## Open Questions

- **OQ-78-AGENT-RASTER-RESAMPLING-FIELD (non-blocking):** Should the agent
  be able to suggest the resampling mode per layer type (e.g., `linear` for
  smooth continuous fields, `nearest` for discrete-cell hazards)? Currently
  client hardcodes `nearest` for all session-state-loaded layers. Adding a
  field would route through Appendix D / `ProjectLayerSummary`. For now
  `nearest` is the right default for the only flood-overlay layer-type
  shipped. Tentative: defer until a continuous-field layer ships.

- **OQ-78-LIGHT-THEME-ALIGNMENT-ANCHORS (non-blocking):** The LIGHT-theme
  proof is less visually striking than DARK because OSM water tones and
  the flood overlay's light-blue palette blend. Dark theme is more
  diagnostic. Tentative: both shipped in evidence; dark is headline.

- **OQ-78-OPACITY-WIRE-DEFAULT (non-blocking):** Flood layer wire payload
  `opacity: 0.9` is high enough to obscure basemap detail. Routes to
  `agent` specialist whether 0.9 default is deliberate. Tentative: propose
  0.7 in a future job; out of scope here.

---

## Dependencies and Impacts

- **Depends on:** job-0076 (idle-retry fix that made the flood layer
  visible), job-0075 (the COG + WMS layer being diagnosed).
- **Affects:**
  - All future flood-overlay screenshots show pixelated cells instead of
    smoothed gradients — desired behavior for discrete-cell hazard rasters.
  - `testing` specialist: `headline_aligned.py` is a reference pattern for
    the basemap-only / with-flood paired screenshot approach.
  - `agent` specialist (non-blocking): OQ-78-OPACITY-WIRE-DEFAULT.

---

## Verification

### Tests run (vitest)

- **Before:** 72 tests passing (7 files)
- **After:** 73 tests passing (7 files) — 1 new regression test:
  - `Map.test.tsx`: "sets raster-resampling: nearest so per-cell alignment
    is visually verifiable (job-0078)"

```
$ npm run test --silent
 Test Files  7 passed (7)
      Tests  73 passed (73)
```

### tsc --noEmit

Clean on `Map.tsx` and `Map.test.tsx`. Pre-existing errors in `ws.test.tsx`
(frozen, job-0072 territory) are unchanged.

### Live E2E evidence

1. **`evidence/url_capture.log`** + **`evidence/url_pairs.json`** —
   Playwright-driven HTTP request log showing 60 flood-layer GetMap URLs
   with axis-identical BBOXes to 60 basemap-layer GetMap URLs.

2. **Server-side curl pair** (commands logged in `evidence/diagnosis.md`):
   `flood-depth-job-0075-demo` at EPSG:3857: 474 KB PNG, 200; basemap at
   SAME bbox: 394 KB PNG, 200; manual PIL composite shows correct alignment.

3. **`evidence/probe_basemap_only.png`** + **`evidence/probe_flood_only.png`**
   + **`evidence/probe_composite.png`** — three-way decomposition.

4. **`evidence/aligned_light.png`** + **`evidence/aligned_light_basemap_only.png`**
   — light-theme zoom-13 headline + basemap-only pair.

5. **`evidence/aligned_dark.png`** + **`evidence/aligned_dark_basemap_only.png`**
   — dark-theme zoom-13 headline + basemap-only pair. `nearest` resampling
   makes each flood-depth cell visible as a discrete pixel block.

6. **`evidence/alignment_proof_four_up.png`** +
   **`evidence/alignment_proof_side_by_side.png`** +
   **`evidence/alignment_proof_light_side_by_side.png`** — composite
   proof images with labels.

**Result: pass.** The flood overlay's geographic alignment with the
basemap is visually verifiable in both light and dark themes via the
`nearest`-resampled per-cell pixelation. The underlying alignment was
already correct (all 60 flood/basemap tile-URL pairs are bbox-identical);
the fix removes the visual artifact that made it look misaligned.
