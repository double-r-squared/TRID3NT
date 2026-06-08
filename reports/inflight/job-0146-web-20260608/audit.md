# Audit: Vector style polish — per-species palette refinement + Pelicun choropleth polish

**Job ID:** job-0146-web-20260608, **Sprint:** sprint-12-mega Wave 4, **Specialist:** web (Sonnet — focused styling)

**Required reads:**
- `web/src/lib/vector_rendering.ts` (Wave 3.5 — VECTOR_PALETTE + paletteColorFor)
- `web/src/Map.tsx` (vector rendering + style_preset honoring)
- Wave 3.5 evidence screenshot `case1_zoomed_bigcypress.png` (current FNV-1a colors — assess if usable or need a curated palette)

### Why

User feedback flagged:
- Pelicun damage layer "looks like a bunch of rectangles" — because assets_uri was `fetch_administrative_boundaries(level='place')` (CDPs are rectangles). The proper fix is upstream (use building footprints) but the SHORT-TERM rendering polish here can make it look less rectangular by varying fill opacity + adding a smooth stroke.
- Per-species discipline screenshots are nice; let's curate the palette for higher contrast + better dark-theme readability vs deterministic hash

### Scope

#### Part 1 — Curated vector palette

Replace the FNV-1a-derived palette in `vector_rendering.ts` with a curated 12-color palette designed for:
- High contrast against the CartoDB DarkMatter basemap
- A11y: color-blind friendly (test with sim8)
- Distinctive even when 6+ layers stacked
- Suggested palette (ColorBrewer Set2 or Set3 variant, adjusted for dark bg):
  - panther/large-mammal: orange #FF7F0E
  - birds: bright cyan #00BFFF
  - reptiles: lime green #ADFF2F
  - marine: aqua #40E0D0
  - plants: pink #FF1493
  - admin-boundary: muted slate #708090
  - fire data: red #FF4444
  - flood data: blue #4477FF
  - roads: muted yellow #FFD700
  - generic fallback: 3 more colors

Style preset registry expanded:
- 'gbif_occurrences' → orange
- 'inaturalist_observations' → bright cyan (the second color)
- 'wdpa_protected_areas' → slate
- 'nws_alerts' → red
- 'mtbs_burn_severity' → red w/ pattern
- 'firms_active_fire' → bright red w/ glow
- 'osm_roads' → muted yellow
- 'pelicun_damage' → choropleth (NOT solid; use ds_mean to color-grade from green to red)

#### Part 2 — Pelicun choropleth gradient

When `style_preset === 'pelicun_damage'` AND a feature has `ds_mean` property:
- Apply a fill-color expression mapping ds_mean (0-1) to a green→yellow→red gradient
- Stroke: subtle outline + slight transparency to soften the rectangular look
- This won't make CDPs less rectangular, but will make the damage GRADIENT more visually meaningful

#### Part 3 — Polygon fill opacity tuning

For all polygon layers:
- Fill opacity reduced to 0.4 (was higher) so basemap labels stay readable underneath
- Stroke 1.5px (was thinner) so polygon edges remain visible against the lower fill opacity

#### Part 4 — Point cluster styling for dense layers

When a point layer has >500 features, use MapLibre clustering:
- Cluster radius 50px
- Cluster point with text showing count
- Individual points at high zoom
- This applies to GBIF / iNat / eBird layers which can be dense

**Tests** (Vitest):
- Curated palette has 12 distinct colors
- `paletteColorFor` deterministic per layer_id
- `presetColorFor` returns curated colors for known presets
- ds_mean → color expression returns correct ramp at 3 sample values
- Polygon fill-opacity tests
- Cluster activation for >500 feature layers

**Live verification** (Playwright):
- Re-capture Case 1 demo with new palette → species clearly distinguishable
- Inject Pelicun damage with varied ds_mean values → choropleth gradient visible (not solid blob)
- 3 screenshots: case1_new_palette.png, pelicun_choropleth.png, polygon_opacity_tuning.png

### File ownership (exclusive)

- `web/src/lib/vector_rendering.ts` — palette + presetColorFor + ds_mean expression (~80 lines)
- `web/src/Map.tsx` — clustering hook (if needed; ~30 lines additive)
- `web/src/lib/vector_rendering.test.ts` (new tests)
- `reports/inflight/job-0146-web-20260608/`


### FROZEN

All files outside the explicit file-ownership list. Especially: every sibling Wave 4 job's exclusive files; `reports/complete/**`.

### Codified lessons (do NOT violate)

1. **Geographic-correctness gate (job-0086)**: pixel-level evidence required.
2. **Kickoff-front-loaded design**: execute scope, surface OQs, don't redesign.
3. **MongoDB MCP persistence (job-0115)**: use Persistence.* — no custom CRUD.
4. **Concurrent web jobs**: App.tsx will be touched by multiple Wave 4 jobs. Pre-commit `git pull --rebase` before commit. Idempotent-append discipline; if conflict, re-apply your specific changes.

### Acceptance criteria

- [ ] All deliverables landed per scope
- [ ] Live Playwright verification per kickoff (screenshots of NEW visual state vs old)
- [ ] No FROZEN edits; single commit prefix `<job-id>:`; co-author line
- [ ] Returns commit SHA + outcome + 1-paragraph headline + evidence + OQs

