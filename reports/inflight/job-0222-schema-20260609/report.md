# Report: MODFLOW groundwater contracts — MODFLOWRunArgs + PlumeLayerURI

**Job ID:** job-0222-schema-20260609
**Sprint:** sprint-13 (Stage 1)
**Specialist:** schema
**Task:** New modflow_contracts.py — MODFLOWRunArgs (forcing parameters) + PlumeLayerURI (extends LayerURI with two plume scalars); export both from the package __init__.py; tests.
**Status:** ready-for-audit

## Summary
Authored packages/contracts/src/grace2_contracts/modflow_contracts.py with two
pydantic-v2 models for the Case 2 groundwater-contamination demo path:
MODFLOWRunArgs (agent-confirmed MODFLOW 6 + MF6-GWT forcing parameters) and
PlumeLayerURI (a LayerURI subclass carrying narration scalars max_concentration_mgl
+ plume_area_km2). Both exported from the package __init__.py (surgical diff).
33 new tests; full contracts suite 329/329 green; live JSON round-trip verified
idempotent for both shapes.

## Changes Made
- NEW packages/contracts/src/grace2_contracts/modflow_contracts.py
  - MODFLOWRunArgs(GraceModel): schema_version Literal["v1"]; spill_location_latlon
    tuple[float,float] (lat,lon) with field_validator (lat[-90,90], lon[-180,180]);
    contaminant str min_length=1; release_rate_kg_s gt 0; duration_days gt 0;
    aquifer_k_ms default 1e-4 gt 0; porosity default 0.3 gt 0 le 1. OQ-3 defaults as
    module constants DEFAULT_AQUIFER_K_MS / DEFAULT_POROSITY.
  - PlumeLayerURI(LayerURI): inherits all 9 LayerURI fields; adds max_concentration_mgl
    ge 0 + plume_area_km2 ge 0 (required, no defaults).
- NEW packages/contracts/tests/test_modflow_contracts.py — 33 tests.
- packages/contracts/src/grace2_contracts/__init__.py (surgical): module added to
  `from . import (...)`; `from .modflow_contracts import MODFLOWRunArgs, PlumeLayerURI`;
  __all__ += modflow_contracts, MODFLOWRunArgs, PlumeLayerURI.

## Decisions Made
- Base class GraceModel not bare BaseModel: inherits extra="forbid"/validate_assignment/
  Z-datetime like every GRACE-2 contract. Bare BaseModel would silently accept unknown keys.
- spill_location_latlon is (lat,lon) point, NOT bbox lon-first ordering; each component
  range-validated; documented to prevent downstream swap.
- OQ-3 defaults surfaced as named constants for the Case 2 composer to narrate as demo values.
- Plume scalars required (always computable post-run); ge 0 so below-detection (0/0) representable.

## Invariants Touched
- 1 Determinism boundary: EXTENDS — plume scalars typed so agent narrates from data.
- 3 Engine registration not modification: PRESERVES — no groundwater field in shared LayerURI/envelope base.
- 9 Confirmation before consequence / no cost theater: PRESERVES — no cost field on MODFLOWRunArgs.
- Output-format vocabulary unchanged (COG raster / FlatGeobuf-GeoParquet vector).

## Open Questions
1. export_schemas.py (NOT in my file ownership) has a hard-coded _EXPORTS list and does not
   auto-discover, so modflow_run_args.json / plume_layer_uri.json are NOT emitted.
   test_export_schemas.py still passes (spot-checks known stems only). Non-blocking;
   add 2 lines to _EXPORTS in a follow-up if the web client needs the JSON Schemas.
2. OQ-3 demo defaults (K=1e-4, porosity=0.3) are TENTATIVE per manifest; encoded as
   overridable defaults + constants; composer (job-0228) must narrate them as demo values.
3. contaminant is a free str (open vocab); MF6-GWT adapter (job-0221) maps name to transport
   params — push back via AGENTS.md motion if a structured contaminant record is needed.
   No SRS amendment proposed: these contracts are net-new (sprint-13 §2.3 / OQ-9), not in Appendices A-D.

## Dependencies and Impacts
- Depends on: execution.LayerURI, common.GraceModel.
- Affects: engine job-0221 (consumes MODFLOWRunArgs, produces PlumeLayerURI); agent job-0227
  (run_modflow_job), job-0228 (Case 2 composer assembles MODFLOWRunArgs).

## Verification
- Tests: pytest tests/test_modflow_contracts.py -> 33 passed; full suite -> 329 passed
  (296 prior + 33 new), 0 regressions. Runner services/agent/.venv (pydantic v2).
- Live E2E (real JSON serialize->deserialize->re-serialize, idempotent):
  - MODFLOWRunArgs minimal-args applies OQ-3 defaults (aquifer_k_ms=0.0001, porosity=0.3);
    spill_location_latlon serializes to [46.6,-116.0] and round-trips to a tuple;
    re-serialize byte-identical (idempotent True).
  - PlumeLayerURI carries inherited LayerURI fields + max_concentration_mgl=18.4 +
    plume_area_km2=4.7; re-serialize byte-identical (idempotent True);
    isinstance(plume,LayerURI)=True, issubclass=True.
  - Top-level import clean; PlumeLayerURI.model_fields = 9 inherited + 2 added;
    both importable from grace2_contracts and present in __all__.
- Results: pass. No Gemini/Vertex calls. No SRS edits. No git push.
