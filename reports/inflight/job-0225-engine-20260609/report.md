# job-0225-engine-20260609 — model_flood_scenario v2: real-precip forcing branch

**Specialist:** engine | **Sprint/Stage:** sprint-13 Stage 1 | **Verdict:** PASS (unit/integration; live SFINCS run blocked on this machine — documented)

## Outcome
Added additive `forcing_raster_uri: str | None = None` to `model_flood_scenario` + wrapper `run_model_flood_scenario`.
When set: SKIP Atlas-14 `lookup_precip_return_period`; read the observed precip raster; compute AREA-MEAN accumulated
precip over the model domain via new `compute_precip_area_mean_mm_per_hr(uri, bbox, accumulation_hours)`; convert to a
uniform SFINCS **netamt** rate (mm/hr); build a `pluvial_observed` ForcingSpec whose pre-computed
`precip_magnitude_mm_per_hr` the deck builder emits verbatim as `setup_precip_forcing: magnitude:`.
When None → behavior IDENTICAL to v1 (regression-verified). Locks manifest **OQ-6 → area-mean netamt fallback for v0.1**;
spw upgrade path documented in-code (`sfincs_builder._generate_hydromt_yaml_config` OQ-6 comment) + below.

## Files changed (owned)
- workflows/model_flood_scenario.py — forcing_raster_uri param + compute_precip_area_mean_mm_per_hr + PrecipForcingError + observed branch + ForcingSpec branch + wrapper plumbing + docstrings
- workflows/sfincs_builder.py — forcing plumbing only: ForcingSpec.precip_magnitude_mm_per_hr field; pluvial_observed YAML branch; pluvial_observed positive-magnitude sanity gate
- tests/test_model_flood_scenario_v2.py — new (10 tests)

## Schema-boundary note (NOT my ownership — for schema specialist)
`ForcingSummary.forcing_type` Literal (contracts/envelope.py) lacks `pluvial_observed` (has storm_surge / pluvial_synthetic /
fluvial_synthetic / news_derived / user_supplied). Did NOT amend schema (out of ownership + user-landed). Envelope-side
ForcingSummary uses `pluvial_synthetic` (observed IS pluvial precip, same netamt path) with the distinction in free-form
`parameters` (forcing_mode=area_mean_netamt, forcing_raster_uri, area_mean_mm, precip_magnitude_mm_per_hr) + source + inputs_uri.
ENGINE-internal ForcingSpec.forcing_type IS `pluvial_observed` (engine-owned) and drives the deck branch.
Proposed amendment OQ-225-OBSERVED-FORCING-LITERAL: add `pluvial_observed` to the Literal (additive, low-risk).

## SFINCS container spw support check (read-only, kickoff-required)
Container: deltares/sfincs-cpu:sfincs-v2.3.3 (digest-pinned, services/workers/sfincs/Dockerfile:32). Entrypoint is a thin
GCS-in/run/GCS-out shim — passes deck through unmodified.
- SFINCS binary v2.3.3 DOES support spatially-varying precip (2D NetCDF precip grid via netamprfile / precip_2d.nc in sfincs.inp). Solver side NOT the blocker.
- Deck-BUILD side (hydromt-sfincs) is the gating concern; it is container-only (not in agent .venv → HYDROMT_UNAVAILABLE), so exact spw method signature could not be introspected here. spw step in hydromt-sfincs >=1.1 is setup_precip_forcing_from_grid(precip=<2D dataarray>). Confirming in-container is a follow-up.
- Per kickoff: spw NOT implemented. v0.1 = area-mean netamt. Upgrade location pinned in-code (OQ-6 comment block).

## Evidence
- New v2 suite: `pytest tests/test_model_flood_scenario_v2.py -q` → 10 passed. Covers area-mean (uniform/mixed/nodata/inches), zero-accum guard, empty-raster PRECIP_RASTER_EMPTY, netamt magnitude in deck, None-path deck/ForcingSpec unchanged + Atlas-14 still called, raster-path skips Atlas-14 + builds pluvial_observed + envelope provenance, observed zero/None magnitude → FORCING_OUT_OF_RANGE, unreadable raster → typed failed envelope (PRECIP_RASTER_READ_FAILED in solver_version).
- Regression: `pytest test_model_flood_scenario.py test_model_flood_scenario_v2.py test_postprocess_flood.py test_run_model_flood_scenario_gemini_kwargs.py -q` → 2 failed, 41 passed, 12 skipped. The 2 failures (run_model_flood_scenario_returns_layer_uri, ..._triggers_loaded_layers_emit) are PRE-EXISTING on baseline HEAD (git stash confirmed) — google-cloud-run not importable in venv → publish_layer falls back to gs:// (JOBS_CLIENT_UNAVAILABLE, environment). ZERO new failures.
- Two-run deck distinction (acceptance "COG distinct from Atlas-14"): ATLAS-14 magnitude 12.8058 mm/hr vs OBSERVED magnitude 3.0 mm/hr → distinct; decks differ. The forcing input that determines the COG differs; rendered-COG comparison needs live infra.

## Blockers (machine-specific)
- Live SFINCS run not runnable: docker daemon unreachable + gcloud absent + no GCS write. Two-run "two flood COGs" demonstrated at DECK level (the forcing input), not rendered-COG level — defer rendered comparison to Case 3 acceptance (job-0236, has live infra).
- hydromt-sfincs container-only → spw method signature confirmation deferred to a container-introspection step.

## Open questions
- OQ-225-OBSERVED-FORCING-LITERAL (schema): add pluvial_observed to ForcingSummary.forcing_type Literal.
- OQ-225-EXACT-DOMAIN-WINDOW (engine, future): v0.1 averages over all valid raster cells (fetchers clip ~bbox); refinement = window-read to exact bbox before averaging.
- OQ-6 spw upgrade (deferred per kickoff): swap single-magnitude for setup_precip_forcing_from_grid once spw method confirmed in-container. Upgrade location pinned in-code.
