# Tool-description optimization -- damage_assessment (6 tools)

**Branch:** `agent/render-honesty-audit`. Docstrings + type annotations only; no logic/return changes.
Same standard + mechanism as `hazard_modeling.md`.

## Verification
All 6: routing block within first 1000 chars; ASCII-clean; no GCP-infra in first-1000; default-in-Literal
clean; `py_compile` clean. Siblings verified registered. Full pytest left for integration.

## Big past-cut rescues
`compute_impact_envelope` Do-NOT moved @1189 -> 509; `analyze_affected_fields` @1122 -> 557;
`fetch_epa_ejscreen` @1058 -> 519 (all were invisible to the router before).

## Literal lifts / dangling fixes
- `fetch_lehd_jobs.segment` -> 11 verified LODES_SEGMENTS values (default total). Dangling fix:
  `fetch_worldpop` (nonexistent) -> `fetch_ghsl_population`.
- Existing kept: `compute_impact_envelope.structure_inventory_source` (USACE_NSI/MS_BUILDINGS).
- Left `str`: `fetch_epa_ejscreen.indicator` + `fetch_cdc_svi` (open/alias-map vocab).

## Notes
Aggregate/scalar tools (compute_impact_envelope, postprocess_pelicun, analyze_affected_fields) flagged
NOT-a-layer in summary + action line; disambiguated from `run_pelicun_damage_assessment` (per-asset map)
and `run_model_contamination_affected_fields` (end-to-end composer). GCP purged (gs:// -> s3://);
public CDC SVI / Census LEHD / EPA EJScreen sources kept.
