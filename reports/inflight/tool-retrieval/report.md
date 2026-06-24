# Report: tool-retrieval (tools-session half) -- STEP 0 + STEP 1 + STEP 7

**Job ID:** tool-retrieval
**Sprint:** (cross-cutting feature; tools-session-owned half)
**Specialist:** Tools / Agent-Config Specialist
**Task:** Per `reports/inflight/tool-retrieval/KICKOFF.md` -- STEP 0 (extend the HOT_SET floor), STEP 1 (the pure `retrieve_visible_tools` selection function), STEP 7 (corpus backfill to 100% registry coverage). The Orchestrator owns the shadow WIRING + index warming around this.
**Status:** ready-for-audit

## Summary
Built the tools-session half of case-stable, monotonic tool-retrieval. STEP 0 extended the never-retrieve-out floor; STEP 1 added `retrieve_visible_tools` (the pure selection function the Orchestrator wraps with shadow telemetry); STEP 7 brought the routing corpus from 80 keys (76 covered) to **126 = 100% full-registry coverage** so recall is trustworthy. All in-seam: no contract/server/adapter edits; `discover_dataset.py` is reused (not modified).

## Changes Made
- **`categories.py` (STEP 0):** `HOT_SET_TOOLS` 12 -> 17. Added `publish_layer` (survived today ONLY via the auto-widen validator -- a latent gap once the catalog is trimmed) + the core analysis surface `compute_zonal_statistics`, `generate_histogram`, `generate_time_series`, `summarize_layer_statistics`.
- **NEW `tools/tool_retrieval.py` (STEP 1):** `retrieve_visible_tools(user_text, allowed_set, k=25) -> set[str]` = HOT_SET core floor UNION the Case's accumulated `AllowedToolSet` UNION `discover_dataset` top-k RRF. Reuses discover_dataset's cached index + `_tokenize` + `_reciprocal_rank_fusion` + `_STOPWORDS`/`_NAME_RANKER_GENERICS` 100%; the 3 sync ranking channels (BM25 + LOCAL-dense + name-substr) MIRROR discover_dataset's inline ranking minus its async Mongo co-occurrence channel.
- **`data/tool_query_corpus.yaml` (STEP 7):** +46 backfilled tools (workflow-generated from each tool's docstring) + 4 follow-up tools that register only via the full startup path (`catalog_search`/`catalog_fetch` restored, `list_qgis_algorithms`/`describe_qgis_algorithm` new) + 3 generic canonical queries on `run_model_flood_scenario`; removed 2 truly-dead keys (`mongo_query`, `fetch_ogc_layer`). Now 126 keys == full registry, every entry >= 5 queries.
- **NEW `tests/test_tool_retrieval.py`:** 40 tests (invariants + recall + STEP-7 coverage enforcement).
- **`tests/test_categories.py`:** updated the two HOT_SET tests for the 12->17 change.
- **`tests/test_validator.py`:** swapped `publish_layer` (now hot-set) for `compute_contours` in the two auto-widen tests.

## Decisions Made
- **Replicate the 3 sync ranking channels in `tool_retrieval.py` rather than refactor `discover_dataset.py`.** Rationale: `discover_dataset` is a LIVE routing hot path with its own tests; an URGENT land should not risk it. I reuse all its primitives (index/tokenizer/RRF/corpus) -- only the channel orchestration is replicated, with a comment pointing to the source-of-truth inline ranking. Alternative (extract a shared `_ranking_channels`) is more DRY but touches the tested live tool; deferred as a safe follow-up.
- **Fail-open to the FULL registry** on any error, a COLD index, or an empty ranking; **floor-only** for an empty/blank query. Over-inclusion is cheap; dropping a needed tool is a silent break (recall optimized over precision, per kickoff).
- **Never build the index on the hot path.** `_discover_topk` reads `discover_dataset._INDEX` LIVE and returns `None` (-> fail-open) when cold, so it never triggers `_get_index()`'s blocking cold model build. The Orchestrator owns startup warming via `asyncio.to_thread` (coordination item below).
- **Skip the Vertex dense backend on the hot path** (its per-query encode is a network call); local sentence-transformers / hashed dense + BM25 are pure-CPU against the cached index.

## Invariants Touched
- **Minimal parameter surface / determinism:** preserves -- pure function, deterministic given a warm index.
- **No sync-blocking on the asyncio loop:** preserves -- no cold build, no network on the hot path (verified by `test_cold_index_never_builds_on_hot_path`).
- **Monotonic allowed-set growth:** preserves + extends -- the result always contains the Case's `AllowedToolSet`, so a once-visible tool never leaves within a Case.

## Open Questions
- **`discover_dataset.py` ranking duplication** (replica vs shared core): tentative -- replicated for blast-radius safety. RECOMMEND a follow-up that extracts a shared `_ranking_channels` so the two paths can never drift. Non-blocking.
- **Hashed-backend routing sensitivity:** on THIS dev venv `rank_bm25` is not installed (BM25 disabled) and dense falls back to the hashed backend, so `model flooding` lands `run_model_flood_scenario` at rank #3 (top-3, test passes). On the prod box (BM25 present) it ranks higher. Flagging the dev-vs-prod backend delta; the Orchestrator's shadow recall@k measurement is the real gate.

## Dependencies and Impacts
- **Coordinate (Orchestrator-owned, do NOT build here):**
  - The shadow WIRING around `retrieve_visible_tools` (server.py:2047 subset of `build_tool_declarations`, the `GRACE2_TOOL_RETRIEVAL` flag, the shadow telemetry field, recall@k in `tool_catalog_http.py` + RoutingQualityDashboard).
  - `discover_dataset` index WARMING at agent startup via `asyncio.to_thread` (must complete before `retrieve_visible_tools` is called, else it fail-opens to the full registry every turn). There is no "is warm?" predicate; warm by `to_thread(discover_dataset._get_index)`.
- **`[TOOLS] retrieve_visible_tools landed` PROJECT_LOG line** written so the Orchestrator can wire shadow.

## Verification
- **Tests:** `test_tool_retrieval.py` (40) + `test_categories.py` (17) + `test_validator.py` + `test_discover_dataset.py` + `test_fetch_glm_lightning.py` -> all pass. **Full agent suite `pytest -m "not live_gemini"` -> 7296 passed, 3 failed, 93 skipped** -- the 3 failures are the PRE-EXISTING `test_granularity_gate.py` `swmm-api`-missing env drift (confirmed pre-existing by stash-rerun), unrelated.
- **Coverage (live):** corpus 126 keys == full registry 126; 0 uncovered, 0 dead keys; every entry >= 5 queries.
- **Recall (live, hashed backend -- the weakest config):** the 11-probe recall fixture passes, including newly-backfilled tools (`run_model_flood_scenario`, `run_swmm_urban_flood`, `fetch_naip`, `run_model_groundwater_contamination_scenario`, `run_seismic_hazard_psha`, `compute_contours`). All 5 discover_dataset canonical-routing queries still route correctly.
- **Regressions found + fixed** (caught by the full suite): (1) the FULL registry is 126 not 122 -- the plain-import gap scan missed `catalog_*` + qgis_discovery tools, so `catalog_*` were wrongly pruned -> restored + the 2 qgis tools backfilled, coverage test switched to the full registry; (2) `model flooding` dropped out of top-3 -> added generic queries to `run_model_flood_scenario`; (3) `publish_layer` now hot-set broke a validator test premise -> swapped for `compute_contours`.
- **Adversarial verification (4-lens refute-by-default panel, 2026-06-23):** 2 lenses REFUTED with real MAJOR issues (both latent -- masked in the live server today but real correctness gaps); both FIXED + regression-tested. The HOT-PATH and REUSE lenses could NOT refute (reuse confirmed the `_discover_topk` replica is BIT-EXACT vs `discover_dataset`'s 3 sync channels across 9 probes; hot-path tripwires fired 0 build/network calls).
  1. **NEVER-HIDE-MID-TASK (major, fixed):** an invalid opened category (registry skew across a deploy) made `AllowedToolSet.as_frozenset()` raise, and the all-or-nothing except collapsed the floor to HOT_SET-only -- silently dropping dispatched/explicit/valid-opened tools. FIX: per-category guard in `categories.py:_build_from_hot_set` (skip only the unknown id) + the snapshot-error path now fail-opens to the FULL registry, never HOT_SET-only. Regression: `test_never_hide_survives_invalid_opened_category` + `test_fail_open_on_snapshot_error_returns_full_registry`.
  2. **Fail-open short by 4 (major, fixed):** `_full_registry_floor` used the 122-tool plain import; the 4 startup-only tools (`catalog_*`, qgis discovery) were dropped on fail-open in a cold process. FIX: `_full_registry_floor` now calls `main._import_tools_registry()` (idempotent, guarded) before snapshotting. Regression: all `test_fail_open_*` now assert the FULL registry + the 4 startup-only tools.
  3. **Hot-path nit (applied):** the dense-backend guard is now a positive allowlist (`sentence_transformers`/`hashed`/`None`) so a future network backend is excluded by default.

## Results: pass. Full suite 7298 passed; only the 3 pre-existing `swmm-api` granularity failures remain (unrelated, confirmed pre-existing).
