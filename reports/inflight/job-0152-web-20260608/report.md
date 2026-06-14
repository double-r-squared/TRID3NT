# Report: Hide MapLibre nav controls + OSM attribution tag

**Job ID:** job-0152-web-20260608
**Sprint:** sprint-12-mega Wave 4.5
**Specialist:** web (Sonnet 4.6)
**Task:** Remove/hide MapLibre NavigationControl (zoom +/- and compass buttons) and hide OSM bottom attribution tag by setting attributionControl: false on Map init.
**Status:** ready-for-audit

## Summary

Two minimal config changes to `web/src/Map.tsx`: `attributionControl` changed from `{ compact: false }` to `false`, and the `m.addControl(new maplibregl.NavigationControl(...))` call removed. Scroll-zoom, pinch-zoom, and keyboard +/- remain functional (MapLibre defaults). Two new unit tests added to `Map.test.tsx` asserting the controls are gone.

## Changes Made

- File: `web/src/Map.tsx`
  - Changed `attributionControl: { compact: false }` to `attributionControl: false` on Map constructor options (disables the OSM attribution tag)
  - Removed `m.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right")` (removes zoom +/- buttons)
  - Updated comment block to explain the job-0152 rationale

- File: `web/src/Map.test.tsx`
  - Added `_constructorOptions: Record<string, unknown>` field to `MapMock` interface
  - Updated `MockMap` constructor to capture and store options in `_constructorOptions`
  - Added new `describe` block `MapView — nav controls + attribution hidden (job-0152)` with two tests:
    1. `does not call addControl (no NavigationControl injected)` — asserts `addControl` never called
    2. `initialises the map with attributionControl: false` — asserts constructor option set correctly

## Decisions Made

- Decision: Remove `NavigationControl` entirely rather than hide via CSS.
  - Rationale: Cleaner — no dead DOM elements; consistent with "Remove don't shim" AGENTS.md principle; keyboard+scroll+pinch zoom remain accessible via MapLibre defaults.
  - Alternatives considered: CSS `display: none` (rejected — addControl still called; hidden DOM; harder to assert in tests).

- Decision: Use `attributionControl: false` constructor option rather than CSS override.
  - Rationale: MapLibre-sanctioned approach; CSS override fragile against upstream style changes.
  - Alternatives considered: CSS `.maplibregl-ctrl-attrib { display: none }` — rejected for same reasons.

## Invariants Touched

- Determinism boundary: preserves — no user-facing numbers involved.
- Rendering through QGIS Server: preserves — basemap/layer wiring unchanged.
- Tier separation: preserves — no Tier A/B data logic touched.
- Cancellation is first-class: preserves — not touched.

## Open Questions

- OQ-0152-OSM-ATTRIBUTION: `attributionControl: false` removes the OSM attribution tag, which is technically against OSM tile-use terms (https://wiki.openstreetmap.org/wiki/Tile_usage_policy). For v0.1 internal demo this is acceptable per kickoff. Production hosting must re-enable attribution or provide a custom attribution element. Proposed: track as a follow-up job before any public deployment. TENTATIVE.

## Dependencies and Impacts

- Depends on: job-0143 (repositioned NavigationControl to top-right; this job removes it entirely)
- Affects: no downstream specialists — pure UI config change; no contract or data implications

## Verification

- Tests run: `cd web && npm test -- src/Map.test.tsx`
- All 30 Map.test.tsx tests pass including both new job-0152 tests:
  - `MapView — nav controls + attribution hidden (job-0152) > does not call addControl` PASS
  - `MapView — nav controls + attribution hidden (job-0152) > initialises the map with attributionControl: false` PASS
- Pre-existing failure in `src/SecretsPanel.test.tsx` (Tier-2 regex) is unrelated to this job's scope.
- Live E2E screenshot: qualified — the visual "clean map without buttons" screenshot is the responsibility of the Wave 4.5 gated Playwright verification job that follows in the pipeline. Unit test assertions for `addControl` not called and `attributionControl: false` are the structural verification for this job.
- Results: pass (unit tests), qualified (live screenshot deferred to downstream Playwright job per Wave 4.5 structure)
