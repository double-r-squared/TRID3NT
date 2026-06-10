# Kickoff (frozen)

You are the engine specialist. Job job-0224-engine-20260609 — analytical Q&A tool set (sprint-13 Stage 1).

## Common rules (GRACE-2 sprint-13 Stage 1)
Working dir: /home/nate/Documents/GRACE-2
Read first: agents/AGENTS.md, your specialist file in agents/, reports/sprints/sprint-13-manifest.md (your job scope), reports/PROJECT_STATE.md.
FIRST ACTION: mkdir -p reports/inflight/<job-id>/ ; write audit.md containing this kickoff prompt verbatim under a "# Kickoff (frozen)" header; write STATE file containing "RUNNING".
- NO Gemini/Vertex generate_content calls of any kind. This job needs none. Hard rule.
- NEVER git push. Commit locally at job end: git add <ONLY your owned files> && git commit -m "<job-id>: <short title>". On index.lock conflict wait 5s, retry up to 5x.
- Stay inside your file ownership. Registration touchpoints (tools/__init__.py, catalog.py, categories.py, contracts __init__.py) only where your kickoff explicitly grants them.
- Python venv: services/agent/.venv (pip install missing deps there as needed). Contracts tests: packages/contracts. Web: npx vitest in web/.
- Environment facts: docker daemon NOT reachable on this machine (socket permission denied); gcloud NOT installed; tofu IS installed (validate with -backend=false only, no plan/apply). Do not burn time fighting these — design around them and document.
- Report honestly. If acceptance can only partially be met on this machine, verdict=PARTIAL with exact blocker documented — never fake success.
- AT JOB END: write reports/inflight/<job-id>/report.md (outcome, evidence, open questions) and set STATE to "READY_FOR_AUDIT".
Return StructuredOutput.

## Scope (manifest path corrected to repo convention: atomic tools live in services/agent/src/grace2_agent/tools/)
services/agent/src/grace2_agent/tools/analytical_qa.py (NEW), all 3 tools, following the existing atomic-tool pattern (read compute_zonal_statistics.py as the closest analog — metadata, ttl_class, registration, error envelopes):
1. summarize_layer_statistics(layer_uri) — raster: min/max/mean/sum/count/distribution(10-bin histogram); vector: feature_count + attribute_summary per numeric property.
2. count_features_above_threshold(layer_uri, property, threshold) — returns count, total, property, threshold.
3. aggregate_property_within_zone(value_layer_uri, zone_layer_uri, property, agg in sum|mean|max) — returns value, agg, n_features.
All read GCS via the same rasterio/geopandas helpers existing tools use. ttl_class: match compute_zonal_statistics convention (deterministic per-layer, cacheable).
Registration: tools/__init__.py + catalog.py + categories.py PRIMARY_CATEGORY (data_analysis or closest existing category — check the 12-category list).
ALSO FIX (granted, 2 lines): the pre-existing red test test_categories.py::test_every_registered_tool_has_a_primary_category — add compute_impact_envelope + postprocess_pelicun to PRIMARY_CATEGORY under damage_assessment.
Tests: services/agent/tests/test_analytical_qa.py with synthetic in-memory/temp-file rasters + vectors (no network).

## File ownership
tools/analytical_qa.py, tests/test_analytical_qa.py, registration lines in tools/__init__.py + catalog.py + categories.py.
