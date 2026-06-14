# REGRESSION lens — job-0228-agent-20260609 (Case 2 composer)

Verdict: CONFIRM (no regression attributable to the composer commit).

## Commit scope (git show --stat 0a266d1)
12 files; source touched: ONLY
- workflows/model_groundwater_contamination_scenario.py (NEW, owned)
- workflows/__init__.py (+1 import line, single anchor)
- categories.py (+5 lines: PRIMARY hazard_modeling + SECONDARY news_events, single anchors)
- tests/test_model_groundwater_contamination_scenario.py (NEW, owned)
- tests/fixtures/case2_news_article.txt (NEW, owned)
Commit touches NONE of: run_modflow.py, model_news_event_ingest.py,
aggregate_claims_across_sources.py, model_flood_scenario.py, publish_layer.py,
solver.py, or any of the 14 failing test files / their sources.

## Lens-mandated regression suites — ALL PASS
test_model_groundwater_contamination_scenario + test_run_modflow +
test_aggregate_claims_across_sources + test_multi_turn_loop +
test_tool_retry_on_failure + test_model_nws_flood_event_scenario +
test_categories + test_allowed_set  => 125 passed.
Registry import smoke: 84 tools, composer registered, in both hazard_modeling
and news_events category member lists (tools_for_category confirms).

## test_categories PRIMARY_CATEGORY (lens flag)
test_every_registered_tool_has_a_primary_category PASSES — job-0224 fix is in
place AND now covers the new tool. Not red.

## Full-suite sweep: 51 failed / 4017 passed / 84 skipped / 1 xfailed
ALL 51 are environment/dependency, in files the composer never touched:
- bs4 absent           -> test_web_fetch (5)
- google.cloud.workflows absent -> test_solver (2)
- google.cloud.run_v2 absent    -> test_publish_layer (3), test_model_flood_scenario (2)
- pandas 2.3.3 outside pin       -> test_pandas_pin_regression (1)
- NLCD docstring / fetcher dep churn (uncommitted cross-job data_fetch.py +306/-240)
  -> test_data_fetch, fetch_cama/era5/gridmet/gtsm/hrrr (several)
- pelicun/hazus dep              -> test_run_pelicun_*, pelicun_damage_with_buildings
Verified env-level: bs4 / google.cloud.workflows / google.cloud.run_v2 all
ImportError on the venv directly (source-independent).

The 2 flood-scenario failures match the runner's pre-existing-red claim exactly
(gs:// fallback because run_v2 unimportable). Working tree carries large
uncommitted cross-job changes (data_fetch.py, model_news_event_ingest.py,
aggregate_claims_across_sources.py, etc.) + many untracked new tools/tests —
these are OTHER Stage-2 jobs, not job-0228, and account for the broader reds.

## Conclusion
Composer commit altered no behavior of run_modflow / model_news_event_ingest /
aggregate_claims. No file outside ownership in the commit. Shared-file edits are
surgical single-anchor and import-safe. No regression attributable to job-0228.
