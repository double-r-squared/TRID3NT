# Audit: Pelicun assets upgrade — use building density (Microsoft footprints) instead of admin polygons

**Job ID:** job-0147-engine-20260608, **Sprint:** sprint-12-mega Wave 4, **Specialist:** engine (Sonnet — focused fix)

**Required reads:**
- `services/agent/src/grace2_agent/tools/run_pelicun_damage_assessment.py` (Wave 2 — current real impl)
- `services/agent/src/grace2_agent/tools/compute_building_density.py` (Wave 1 — Microsoft Building Footprints)
- Wave 3 job-0136 evidence — current Pelicun output uses TIGER places (CDPs), which are rectangles

### Why

User feedback: Pelicun layer "looks like a bunch of rectangles." Root cause: assets_uri was `fetch_administrative_boundaries(level='place')` returning Census Designated Places (CDPs) which are administrative rectangles, not real assets. v0.1 used this as a coarse proxy.

Fix: use `compute_building_density` output OR direct building footprints as assets. This makes the damage choropleth show varying damage across REAL spatial structure, not arbitrary administrative grids.

### Scope

#### Part 1 — Document the assets convention

Update `run_pelicun_damage_assessment` docstring to explicitly call out:
- Preferred `assets_uri`: building footprints (centroid sampling) — use `fetch_administrative_boundaries(level='place')` ONLY as a fallback
- For v0.1: when `compute_building_density` cache exists for the bbox, prefer that

#### Part 2 — Pelicun composer convenience wrapper

NEW file `services/agent/src/grace2_agent/workflows/pelicun_damage_with_buildings.py`:

```python
@register_workflow(name="run_pelicun_with_buildings")
async def run_pelicun_with_buildings(
    hazard_raster_uri: str,
    bbox: tuple[float,float,float,float],
    cell_size_m: float = 100,
    fragility_set: str = "hazus_flood_v6",
    realization_count: int = 100,
) -> LayerURI:
    """Compose: fetch building footprints density grid → use as assets for Pelicun → damage assessment.

    Wraps the proper "buildings as assets" pattern so the agent doesn't have to
    explicitly fetch buildings + then pass to run_pelicun.
    """
    buildings_uri = await compute_building_density(bbox=bbox, cell_size_m=cell_size_m)
    return await run_pelicun_damage_assessment(
        hazard_raster_uri=hazard_raster_uri,
        assets_uri=buildings_uri,
        fragility_set=fragility_set,
        realization_count=realization_count,
    )
```

#### Part 3 — Live re-run for Fort Myers (replaces the rectangular evidence)

Run the new composer against Fort Myers:
- hazard = job-0086 flood COG
- bbox = Fort Myers bbox
- Capture output FlatGeobuf — should be a GRID of damage points (one per 100m cell), not rectangles

Add evidence to `reports/inflight/job-0147-engine-20260608/evidence/fort_myers_buildings_pelicun.fgb` + summary stats.

**Tests** (≥4 unit + 1 live):
- Composer dispatches building_density → Pelicun in order
- Mocked buildings + flood → expected number of damage points (≈ bbox area / cell_size_m²)
- Each damage point carries ds_mean property in [0,1]
- Live (env GRACE2_TEST_LIVE_PELICUN_V2=1): Fort Myers run produces non-rectangular spatial distribution of damage points

### File ownership (exclusive)

- `services/agent/src/grace2_agent/workflows/pelicun_damage_with_buildings.py` (NEW)
- `services/agent/src/grace2_agent/workflows/__init__.py` — register
- `services/agent/src/grace2_agent/tools/run_pelicun_damage_assessment.py` — docstring update only (no signature change)
- `services/agent/tests/workflows/test_pelicun_damage_with_buildings.py` (NEW)
- `reports/inflight/job-0147-engine-20260608/`


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

