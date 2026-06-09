# Audit: Hide MapLibre nav controls + OSM attribution tag

**Job ID:** job-0152-web-20260608, **Sprint:** sprint-12-mega Wave 4.5, **Specialist:** web (Sonnet small)

**Required reads:**
- `web/src/Map.tsx` (job-0143 — repositioned nav controls)
- MapLibre GL JS docs (NavigationControl, AttributionControl options)

### Why

User direction 2026-06-08: zoom icons still overlay other UI; OSM bottom attribution tag in the way. Just hide both — users zoom via scroll/pinch/keyboard.

### Scope

1. Remove or hide MapLibre `NavigationControl` (zoom +/- and compass buttons)
2. Hide OpenStreetMap attribution tag at bottom — disable via `attributionControl: false` on the Map init
3. Keep keyboard +/- and scroll-zoom + pinch-zoom (default MapLibre interactions — no code change needed)
4. Honest disclosure: attribution removal is technically against OSM tile-use terms; for v0.1 demo OK, but production hosting should re-show it. Surface as OQ.

**Tests**: Map.test.tsx — assert no .maplibregl-ctrl-group OR no .maplibregl-ctrl-attrib rendered

**Live verification**: screenshot showing clean map (no buttons, no attribution tag)

### File ownership (exclusive)

- `web/src/Map.tsx` — minimal config change
- `web/src/Map.test.tsx` — extend
- `reports/inflight/job-0152-web-20260608/`


### FROZEN

All files outside the explicit file-ownership list. Sibling Wave 4.5 files; `reports/complete/**`.

### Codified lessons (do NOT violate)

1. Geographic-correctness gate (job-0086): pixel-level evidence.
2. Kickoff-front-loaded design: execute scope, surface OQs.
3. UX language discipline: no internal terms ("Mode 1/2", "Tier", "OQ-*") in user-facing surfaces.
4. Pre-commit: `git pull --rebase` before commit.

### Acceptance criteria

- [ ] Deliverables landed per scope
- [ ] Live verification per kickoff
- [ ] No FROZEN edits; single commit prefix; co-author line
- [ ] Returns commit SHA + outcome + headline + evidence + OQs

