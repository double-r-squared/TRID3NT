# Wave 4.10 — Stage 1 Endpoints + Audit + Core Fixes

**Date**: 2026-06-09
**Workflow**: `wf_ebcd6267-607` (3.66M tokens, 31 agents, ~174 min)
**Status**: 24 of 26 specialist jobs landed; B-rev + B-sys blocked by adversarial-verify; fixes in flight at `wf_2f6aed7c-929`

## A-track endpoints (14/14 PASS, all with live smoke tests)

| Job | Tool | Live smoke result |
|---|---|---|
| A1 | `fetch_fema_nfhl_zones` | Fort Myers tile → 154 flood-zone polygons across 6 designations (A, AE, AH, VE, X, OPEN WATER) → 3.0 MB FlatGeobuf |
| A2 | `fetch_hrrr_forecast` | Fort Myers bbox, 2m temp, fcst_hr=1 → 13×13 grid, 22.6-26.9 °C (physically plausible for FL in June) |
| A3 | `fetch_noaa_nwm_streamflow` | Caloosahatchee bbox, analysis_assim → 16 NHDPlus reaches joined |
| A4 | `fetch_usace_levees` | New Orleans bbox → 16 polygons, ~1.38 MB FlatGeobuf |
| A5 | `fetch_usace_dams` | Fort Myers bbox → 7 NID points, ~7.2 KB |
| A6 | `fetch_usace_nsi` | Fort Myers Beach → structure inventory with occupancy + replacement value |
| A7 | `fetch_asos_metar` | Fort Myers/Naples → 11 stations, 69 observations from 3 hours |
| A8 | `fetch_gridmet` | Riverside County CA fm100 over 3 days via NKN THREDDS OPeNDAP |
| A9 | `fetch_noaa_coops_tides` | Fort Myers station 8725520 water_level on Hurricane Ian landfall day |
| A10 | `fetch_noaa_slr_scenarios` | Fort Myers coastal → 1ft+2ft+3ft scenarios, 3 dissolved COGs |
| A11 | pfdf unlock + 3 sub-tools | NLDI snap+navigate DM (COMID 16754658), STATSGO KFFACT, 3DEP extra paths |
| A12 | `fetch_raws_weather` | Utah fire belt → 4 RAWS stations via IEM RAWS obhistory API |
| A13 | `fetch_hrrr_smoke` | NorCal MASSDEN@8m AGL → 63×9 array, fcst_hr=1 |
| A14 | `fetch_usfs_canopy_fuels` | San Diego CBH → 5.9 MB GeoTIFF, 46,741 valid pixels |

All 14 follow the 6-point description-audit pattern (when-to-use / when-NOT / parameter constraints / returns / cross-tool deps + audit sections). Word counts 269-681 (above 300 ceiling for complex tools, justified per kickoff).

## B-track audit + corpus (4/4 PASS)

| Job | Scope | Result |
|---|---|---|
| B1 | Tier-1 description audit (15 tools) | min/median/max word count: 269 / 313 / 458 |
| B2 | Tier-2 description audit (20 tools) | 269 / 398 / 681 across 13 tools (rest reused B1 audit baselines) |
| B3 | Tier-3 compute + workflow (22 tools) | 420 / 500 / 1223 (outlier: `run_pelicun_damage_assessment` at 1223 words — load-bearing existing detail, not trimmed) |
| B4 | Synthetic example-query corpus | 67 tools × 5-8 queries = 445 queries at `services/agent/src/grace2_agent/data/tool_query_corpus.yaml` |

## Core fixes (5/5 specialist PASS; 2 blocked by adversarial)

| Job | Outcome |
|---|---|
| B-tel | Tool-call telemetry writer landed at `services/agent/src/grace2_agent/telemetry.py` writing JSON-lines to `/tmp/grace2_tool_call_telemetry.jsonl` (configurable via `GRACE2_TELEMETRY_PATH`). Wired into multi-turn loop post-summarize_tool_result. Harvests `cached_content_token_count` from `usage_metadata` when available. |
| **B-rev** (BLOCKED) | TOOL_NOT_FOUND refactor → `ToolNotFoundError` typed exception + raise instead of return-None in `_invoke_tool_via_emitter`. **Adversarial 1/3 confirm**: missed 2nd caller (`_dispatch_tool_and_peel` manual /invoke directive path); inflated test-count claim. Fix dispatched. |
| **B-sys** (BLOCKED) | SYSTEM_PROMPT amended with named-tool follow-on dispatch + geographic-clipping pattern sections. **Adversarial 2/2 confirm**: worked-example contract drift (`fetch_administrative_boundaries(name=)` doesn't match real signature); live-verify not actually run. Fix dispatched. |
| harness-refine | `eval_routing_live.py` refinements: per-anchor `watchdog_seconds`, `wait_for_agent_idle()` helper, classified error envelopes, `--anchor` CLI flag for Bayesian-adaptive selection, harness_version field. |
| B-env-inv | Identified the 2-per-anchor `error` envelopes as `maybeSendAuthToken` sending wrong field names (`token`/`anonymous` mismatch). Fixed in the same job. Backend hygiene win. |

## Adversarial-verify panel results

**B-rev** (TOOL_NOT_FOUND): 1 confirm, 3 refute → **BLOCK**
- ✗ Correctness (high): Regression on `/invoke` directive path. `_invoke_tool_via_emitter` has 2 callers — Gemini loop has handler, but `_dispatch_tool_and_peel` (manual debug surface) was not audited.
- ✗ Regression (high): Same finding via separate grep verification.
- ✗ Contract (high): Inflated test-count claim ("51/51" vs actual `pytest --collect-only`).
- ✓ Live-verify (high): In-process live verify of Gemini loop path confirms TOOL_NOT_FOUND envelope round-trips correctly.

**B-sys** (system-prompt amendment): 2 confirm, 2 refute → **BLOCK**
- ✓ Correctness (high): Pure text-only amendment, no code paths.
- ✓ Regression (high): `pytest tests/test_system_prompt.py tests/test_agent_routing.py` → 17 passed.
- ✗ Contract (high): Worked example `fetch_administrative_boundaries(name="Miami-Dade County, FL")` — real signature doesn't accept `name=`.
- ✗ Live-verify (medium): Lens explicitly mandated A2 anchor live re-run — verifier could not actually execute it.

## What this validates about the adversarial-verify pattern

The 4-lens panel caught real issues a single critique would have missed:
- The B-rev correctness/regression refute found a regression on a different caller (the /invoke directive) that specialist + live-verify-only would have missed
- The B-sys contract refute caught spec drift between prompt example and actual tool signature — only the contract lens reads both
- Both jobs PASSED specialist self-report; both BLOCKED on independent skeptical review

This is the pattern working exactly as designed. Cost: ~600K Opus on the 8 adversarial verifiers across both jobs. Saved: shipping B-rev with a regression on a tested-but-unaudited code path, and shipping B-sys with a prompt that Gemini would parse but the tool would reject.

## Token spend summary

- Stage 0 baseline: 144K
- Stage 1 main: 3.66M
- Sprint-12 cumulative: 21.26M

## Files changed (summary)

- 14 new tool modules + 14 test modules in `services/agent/src/grace2_agent/tools/` and `services/agent/tests/`
- `services/agent/src/grace2_agent/tools/__init__.py` extended with 14 imports/registrations
- All 57 existing tool docstrings rewritten to 6-point audit pattern
- New `services/agent/src/grace2_agent/data/tool_query_corpus.yaml` (445 synthetic queries × 67 tools)
- New `services/agent/src/grace2_agent/telemetry.py` + wired into `server.py` multi-turn loop
- `services/agent/src/grace2_agent/adapter.py` SYSTEM_PROMPT amended (pending fix)
- `services/agent/src/grace2_agent/server.py` TOOL_NOT_FOUND refactor (pending fix)
- `services/agent/tests/eval_routing_live.py` refinements
- AuthGate field-name fix in `web/src/...` (B-env-inv)

## Next

1. Fix workflow `wf_2f6aed7c-929` lands → re-adversarial-verify B-rev + B-sys
2. If both clear (≥3-of-4 confirm) → dispatch Stage 2 architecture foundation (B5 categories + post-hoc validator → B6 CachedContent integration → B7 discover_dataset + B10 thought_signature + B11 OpenAPI + B12 annotations + B13 arg_normalizer aliases)
3. Stage 2 architecture jobs all gate through adversarial-verify panels (B5, B6, B10 explicitly required per the wave plan)
