# job-0203 — REGRESSION lens verdict: CONFIRM

Reviewer: independent adversarial (REFUTE-by-default). Commit a62ae1c. HEAD=a62ae1c.

## Ownership (git show --stat a62ae1c) — CLEAN
14 paths, all within declared ownership:
- src: persistence.py, mode2_classifier.py, server.py, mcp.py (mcp.py = in-flight grant, documented in report §Ownership notes)
- tests: test_mcp_surface_translator.py (new), test_persistence_sessions.py (new), test_mode2_audit_mcp.py (new), test_mode2_classifier.py (updated)
- reports/inflight/job-0203-*: STATE, audit.md, report.md, evidence/*.py (3)
No stray files outside ownership.

## Owned source == commit, and clean in working tree
persistence.py / server.py / mode2_classifier.py / mcp.py are byte-identical to a62ae1c AND clean in the working tree (`git diff --quiet a62ae1c -- <f>` and `git diff --quiet -- <f>` both pass). So tests run against the editable install exercise EXACTLY the committed job-0203 code.

## Targeted suite (owned + server-adjacent): 147 passed, 1 skipped
`tests/test_mcp_surface_translator.py test_persistence_sessions.py test_mode2_audit_mcp.py test_mode2_classifier.py test_persistence.py test_file_persistence.py test_mongo_mcp_wiring.py test_auth_handshake.py test_server_case_handlers.py test_case_layer_persistence.py test_case_lifecycle.py test_chart_tools.py`
→ 147 passed / 1 skipped. server.py wiring did NOT break auth handshake, case handlers, persist_chat_turn, or job-0230 chart emission (same-day same-file). Log: owned_and_adjacent.log

## Full suite (tests/, --ignore eval_routing_live): 51 failed / 4017 passed / 84 skipped
Log: full_suite_worktree_dirty.log
ALL 51 failures are in 14 NON-owned test files (data_fetch, fetch_cama/era5/gridmet/gtsm/hrrr, model_flood_scenario, pandas_pin, publish_layer, run_pelicun, solver, web_fetch, pelicun_workflow). NONE reference owned modules in their tracebacks.
Root causes (all environment / dirty-tree, NOT job-0203):
- Missing optional deps in this box's venv: xarray, pelicun, netCDF4, bs4, fsspec, google.cloud.workflows, google.cloud.run_v2
- New tool modules (gridmet, fetch_hrrr_*) exist as uncommitted concurrent work; not present at a62ae1c, not pip-installed → ModuleNotFound
- pandas pin version-bound assertion

## Documented pre-existing failure — verified same-way on parent
`test_data_fetch.py::test_fetch_landcover_docstring_records_access_tier` (file:line tests/test_data_fetch.py:572) fails because the WORKING-TREE (uncommitted, 306+/240-) data_fetch.py dropped "Access pattern:" from docstrings.
- `git diff --stat a62ae1c~1 a62ae1c -- data_fetch.py` = EMPTY (untouched across the commit boundary).
- COMMITTED data_fetch.py at a62ae1c STILL has "Access pattern:" (3 matches) → the test PASSES against committed code; only the dirty tree makes it fail.
- Therefore it fails identically regardless of whether job-0203 is applied — cause is the dirty tree, not the commit. The report's framing ("uncommitted working-tree state, untouched by this job") is accurate.

## Mode-2 remove-don't-shim — verified
mode2_classifier.append_audit_log / default_audit_log_path DELETED (hasattr False). Grep-clean: no production caller of the deleted JSONL writer remains.

## Verdict
CONFIRM. No regression attributable to job-0203. The 51 full-suite failures are pre-existing dirty-tree / missing-optional-dep artifacts in non-owned files; the one named acceptable failure is verified to be caused by uncommitted data_fetch.py docstring state untouched across the commit boundary.
