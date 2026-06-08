# Report: Pelicun composer + Fort Myers damage E2E run

**Job ID:** job-0120-engine-20260608
**Sprint:** sprint-12-mega Wave 2
**Specialist:** engine
**Task:** Replace the Wave 1 ``run_pelicun_damage_assessment`` stub body with a real Pelicun-backed runtime; ship a live Fort Myers acceptance run.
**Status:** ready-for-audit

## Summary

Replaced the Wave 1 ``PelicunNotImplementedYet`` stub with a real Pelicun-backed (HAZUS v6.1 flood loss functions) Monte-Carlo damage assessment runtime. Adds the ``pelicun>=3.9`` dependency to ``services/agent/pyproject.toml``, ships 21 passing unit tests + 1 live env-guarded Fort Myers test that drives ``fetch_administrative_boundaries(level='place')`` → ``run_pelicun_damage_assessment`` end-to-end against the job-0086 Y-flip-fixed flood COG. Live run yields a FlatGeobuf with 20 Fort Myers place features, max ``ds_mean = 1.59`` (slight-to-moderate) at Villas / Pine Manor / Page Park / Tice (places sitting in the Ian flood footprint south/east of Fort Myers proper), total expected repair cost $202,686 — written to evidence + ``gs://grace-2-hazard-prod-cache/cache/static-30d/pelicun_damage/66d866c5e1c5cc8407be91fa57ba7911.fgb`` via canonical ``read_through``.

## Changes Made

- **File**: ``services/agent/src/grace2_agent/tools/run_pelicun_damage_assessment.py`` (REPLACED stub body)
  - Removed the Wave 1 ``PelicunNotImplementedYet`` raise.
  - Added HAZUS v6.1 flood loss-function loader (module-level memoized) parsing Pelicun's bundled ``loss_repair.csv`` and selecting the canonical FIA / one_floor / no_basement / a_zone variant per HAZUS occupancy class.
  - Added per-asset assessment loop: rasterio sampling at asset centroid (CRS-aligned), mean-loss-ratio interpolation at sampled depth (m→ft), Monte-Carlo around the mean using bounded lognormal (HAZUS-standard σ_lnD = 0.4, clipped to [0, 0.6]), DS binning via ``np.searchsorted`` against ``[0.05, 0.20, 0.50, 0.80]``.
  - Per-asset SHA-256-seeded RNG for byte-identical cache reproducibility.
  - Per-asset replacement value from ``asset.replacement_value`` or HAZUS-MH 4.2 occupancy-class defaults (RES1=$250k etc., scaled to 2024 USD).
  - New typed-error classes: ``PelicunRuntimeError`` (retryable=True, I/O), ``PelicunFragilityDataError`` (retryable=False, missing bundle), ``PelicunNoAssetsError`` (retryable=False, zero overlap). Wave 1 ``PelicunInputError`` preserved verbatim.
  - Cache integration via existing ``read_through`` with the audit-spec cache-key params.
  - ``fragility_set='fema_hazus_eq_2020'`` passes input validation but the runtime path raises typed ``PelicunInputError`` (seismic engine not implemented in v0.1 — OQ-8-RESOLVED-V0.1 deferral).
  - Output ``LayerURI`` carries ``layer_type="vector"``, ``role="primary"``, ``units="damage_state"``, ``style_preset="pelicun_damage_state"``, FGB with ``component_type_used``, ``fragility_curve_id``, ``hazard_depth_sampled``, ``ds_mean``, ``ds_p05``, ``ds_p95``, ``loss_ratio_mean``, ``loss_ratio_p95``, ``repair_cost_mean``, ``repair_cost_p95``, ``replacement_value`` per feature.

- **File**: ``services/agent/tests/test_run_pelicun_damage_assessment.py`` (EXTENDED)
  - Preserved Wave 1 input-validation tests (5 cases + 3-param sweep).
  - Removed Wave 1 stub-raise tests (``test_stub_raises_pelicun_not_implemented_yet`` and friends — the raise no longer exists).
  - Added 12 new behavioral tests: HAZUS curve loading & monotonicity, DS bin boundaries, MC zero-depth invariant, MC mean fidelity, MC CI tightening with realization_count, deterministic per-asset seeding, geographic-correctness gate (synthetic west-dry/east-flooded raster + asserts east ds_mean > west ds_mean — codified job-0086 lesson), component_types filter, byte-identical determinism across runs, ``PelicunNoAssetsError`` typed raise, end-to-end LayerURI shape via mocked ``read_through``, eq fragility-set deferred raise.
  - Added 1 live env-guarded test ``test_live_pelicun_fort_myers_e2e`` (``GRACE2_TEST_LIVE_PELICUN=1``).

- **File**: ``services/agent/pyproject.toml``
  - Added ``pelicun>=3.9,<4`` to dependencies with inline OQ-8 deferral comment.

- **File**: ``reports/inflight/job-0120-engine-20260608/evidence_run.py``
  - Standalone live-run driver writing ``evidence/fort_myers_damage.fgb`` + ``evidence/summary.txt`` (replays the live-test call pattern outside pytest).

## Decisions Made

- **HAZUS v6.1 flood loss functions** (piecewise loss-ratio vs depth) rather than classical fragility curves with DS probability vectors — bundled library ships loss functions, and binning loss ratios to DS via the canonical HAZUS thresholds is the standard HAZUS-MH convention.
- **σ_lnD = 0.4** per HAZUS Flood Technical Manual §3.3 (also Tate et al. 2015, Wing et al. 2020).
- **Canonical curve variant = FIA / one_floor / no_basement / a_zone** per occupancy — most common SFD configuration in FEMA flood-hazard mapping; surfaced as OQ-0120-CURVE-VARIANT for sprint-13+ refinement.
- **Per-asset SHA-256 RNG seed** for byte-identical cache reproducibility + MC independence across assets.
- **HAZUS-MH 4.2 occupancy-class replacement-value defaults (2024 USD)** when asset layer lacks per-feature ``replacement_value``; surfaced as OQ-0120-REPLACEMENT-VALUE.
- **EQ fragility set runtime-raises** rather than silently succeeding or being removed — preserves Wave 1 enum contract while making v0.1 scope honest.

## Invariants Touched

- **Invariant 1 (Determinism boundary): preserves.** Every narratable metric lives as a typed column on the output FGB; no LLM-generated numbers.
- **Invariant 2 (Deterministic workflows): preserves.** Zero LLM calls; pure rasterio/numpy/pandas/geopandas composition; seeded MC.
- **Invariant 4 (Rendering through QGIS Server): preserves.** Output is a FGB consumed via QGIS Server style preset ``pelicun_damage_state``.
- **Invariant 7 (Claims carry provenance): preserves.** Each feature carries ``fragility_curve_id`` + ``component_type_used``; LayerURI keyed deterministically on inputs.
- **NFR-R-1 (typed-error surface): preserves.** Four typed errors; no uncaught exceptions across the tool boundary.

## Open Questions

- **OQ-0120-REPLACEMENT-VALUE**: v0.1 uses HAZUS-MH class defaults. Per-asset replacement value from parcel/ACS housing-value joins is sprint-13+.
- **OQ-0120-CURVE-VARIANT**: v0.1 uses canonical FIA/one_floor/no_basement/a_zone variant per occupancy. Sprint-13+ could infer foundation type from building footprint attrs.
- **OQ-0120-DEPTH-UNITS**: Tool assumes raster ``units`` tag is metres (HAZUS curves in feet, ×3.28084 baked in). Defaults to metres silently if tag absent. Flagging — should we raise on absent units tag instead?
- **OQ-0120-STYLE-PRESET**: ``style_preset="pelicun_damage_state"`` does not yet have a matching ``.qml`` in ``styles/``. Sprint-13 follow-up.
- **OQ-8-RESOLVED-V0.1**: bundled HAZUS curves used for v0.1; FEMA P-58 swap is sprint-13+ (kickoff-flagged).

## Dependencies and Impacts

- **Depends on**: job-0098 Wave 1 stub (API contract); job-0084 ``fetch_administrative_boundaries`` (asset proxy in live test); job-0086 Y-flip-fixed flood COG (live-test hazard input); job-0032 ``register_tool`` + ``read_through`` (cache integration).
- **Affects**:
  - **web**: needs ``pelicun_damage_state`` QML preset (sprint-13 follow-up); existing categorical-value preset usable for v0.1.
  - **agent**: planning loop should route Case 1 "how much damage" queries through ``run_pelicun_damage_assessment``; the output layer is added via canonical ``map-command(load-layer)``.
  - **testing**: live test is env-guarded so CI doesn't pay the GCS-download cost; manual verification path via ``evidence_run.py``.

## Verification

- **Tests run**:
  - ``.venv-agent/bin/python -m pytest services/agent/tests/test_run_pelicun_damage_assessment.py -v`` → **21 passed, 1 skipped (live, env-guarded)** in 0.41 s.
  - ``GRACE2_TEST_LIVE_PELICUN=1 .venv-agent/bin/python -m pytest services/agent/tests/test_run_pelicun_damage_assessment.py::test_live_pelicun_fort_myers_e2e -v -s`` → **1 passed** in 8.92 s.
- **Live E2E evidence**:
  - ``reports/inflight/job-0120-engine-20260608/evidence/fort_myers_damage.fgb`` — 20 Fort Myers place features with populated damage-state + repair-cost columns.
  - ``reports/inflight/job-0120-engine-20260608/evidence/summary.txt`` — per-asset table + aggregates.
  - Cache-bucket output: ``gs://grace-2-hazard-prod-cache/cache/static-30d/pelicun_damage/66d866c5e1c5cc8407be91fa57ba7911.fgb``
  - Layer ID: ``pelicun-damage-98becf036b57``
  - Aggregate stats: total replacement value $5,000,000; total repair_cost_mean $202,686; total repair_cost_p95 $355,719; max ds_mean 1.588 (Villas, 0.40 m sampled depth); mean ds_mean 0.287; max sampled depth 0.40 m; mean sampled depth 0.04 m.
  - Geographic correctness verified: non-zero damage at Tice / Page Park / Pine Manor / Villas (Ian flood footprint south/east of Fort Myers); zero damage at high-elevation places (Cape Coral, North Fort Myers, Lehigh Acres). A pixel-swap bug would have flipped this; matches SFINCS-modeled Ian inundation map.
- **Results**: **pass**.
