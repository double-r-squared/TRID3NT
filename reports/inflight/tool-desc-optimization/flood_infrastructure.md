# Tool-description optimization -- flood_infrastructure (6 tools)

**Branch:** `agent/render-honesty-audit`. Docstrings + type annotations only; no logic/return changes.
Same standard + mechanism as `hazard_modeling.md`.

## Verification
All 6: routing block within first 1000 chars; ASCII-clean; no GCP-infra in first-1000; default-in-Literal
clean; `py_compile` clean. Siblings verified registered (incl. `run_model_flood_scenario`, registered via
workflow `AtomicToolMetadata`). Full pytest left for integration.

## Literal lifts / dangling fixes
- `fetch_usace_dams.hazard_potential` -> High/Significant/Low/Undetermined (vs `_validate_hazard_potential`).
- Existing kept: `fetch_usace_levees.layer` (leveed_areas/system_routes/embankments).
- **Dangling fixes:** `fetch_usace_dams` named nonexistent `fetch_usace_nld_levees` -> `fetch_usace_levees`;
  `fetch_usace_levees` named nonexistent `fetch_usace_nid` -> `fetch_usace_dams`.
- Left `str`: NFHL `zone_filter` (list), HIFLD `facility_type` / EPA `facility_program` (server-side alias
  maps -- a Literal would falsely reject valid aliases), `state` (parametric).

## Notes
points vs lines vs polygons disambiguated (usace_dams points / usace_levees lines / nfhl & frs zones).
GCP run-cache purged; public FEMA NFHL / USACE NID+NLD / HIFLD / EPA FRS sources kept.
