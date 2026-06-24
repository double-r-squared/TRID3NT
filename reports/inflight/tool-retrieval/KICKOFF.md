# KICKOFF (frozen) - Tool-retrieval: tools-session half (corpus + selection)

URGENT (NATE 2026-06-23). Cross-cutting feature; this kickoff is the TOOLS-SESSION-owned half. The ORCHESTRATOR owns the shadow WIRING that wraps your `retrieve_visible_tools` (server.py:2047 call, the `GRACE2_TOOL_RETRIEVAL` flag, shadow telemetry, recall@k dashboard) - do NOT touch those; coordinate at that edge.

Decision (locked by NATE): mode = `STEP 0 + shadow now, enforce when recall proves out`. Recall@k target >=0.99 per North-Star flow. Cloud stays shadow-only; enforce is offline-path-first. Design = CASE-STABLE, MONOTONIC-GROW (never per-turn-dynamic - it must preserve the cachePoint at the tail of the tool list).

Grounding (verified by the scope, wf w88vnp9lh): all 121 tools are sent every turn (~166 KB, ~41-46k tokens), built once at `server.py:2047 build_tool_declarations(TOOL_REGISTRY)`, never subset. `discover_dataset` (hybrid BM25+dense RRF over `tool_query_corpus.yaml`), `categories.py` PRIMARY/SECONDARY + `HOT_SET_TOOLS` + `AllowedToolSet` all exist but only validate POST-hoc. Reuse 100 percent of this - no new infra.

## STEP 0 (ship first, standalone correctness fix - independent of the rest)
Add to `HOT_SET_TOOLS` (categories.py:557) the tools that must NEVER be retrieved-out: `publish_layer` (survives today ONLY via the auto-widen validator - a latent gap that breaks the instant the catalog is trimmed) + the core analysis surface `compute_zonal_statistics`, `generate_histogram`, `generate_time_series`, `summarize_layer_statistics`. (The 3 meta-tools `list_categories`/`list_tools_in_category`/`discover_dataset` + the composers + `code_exec_request`/`compute_layer_bounds`/`request_spatial_input`/`geocode_location`/`fetch_dem`/`fetch_nws_*` are already in HOT_SET.) Land this alone; it is useful regardless of cutover.

## STEP 1 - the pure selection function (the deliverable the orchestrator wires shadow around)
New module `services/agent/src/grace2_agent/tools/tool_retrieval.py`:
`retrieve_visible_tools(user_text: str, allowed_set, k: int) -> set[str]` =
  HOT_SET core floor  UNION  `tools_for_category()` for every category already opened in this Case's `AllowedToolSet`  UNION  `discover_dataset` top-k RRF results keyed on `user_text` (k default 25, clamp [1,25]).
Properties (assert in tests): deterministic; NO hot-path I/O beyond the cached `discover_dataset` index lookup; the core floor is ALWAYS a subset of the result (CORE-FLOOR test); composes by UNION with the session AllowedToolSet so a tool once visible NEVER leaves within a Case (NEVER-HIDE-MID-TASK test); FAIL-OPEN - any error / empty result returns the FULL registry, logged (fault-injection test). Optimize recall@k, not precision (over-include is cheap; dropping a needed tool is a silent break).

## STEP 7 - corpus backfill (gates trustworthy recall)
Extend `tool_query_corpus.yaml` from 79 to all ~121 registered tools (the ~42 docstring-only tools get 5-10 realistic discovery queries each, same style as existing entries) and prune dead keys (`mongo_query`, `fetch_ogc_layer` if no longer real). Until this lands, the uncovered ~42 tools lean on the core floor + category-gate only - flag any North-Star tool that is corpus-uncovered.

## Coordinate (do NOT build - orchestrator owns)
- `discover_dataset` index WARMING at agent startup + dense encode via `asyncio.to_thread` (no sync-block on the loop). If the warming hook is in tools-owned code, propose it; the orchestrator confirms the startup seam.
- The shadow wiring (server.py:2047, the env flag, telemetry shadow field, recall@k in tool_catalog_http.py + RoutingQualityDashboard) - the orchestrator builds this AROUND your `retrieve_visible_tools`. When STEP 1 lands, write a `[TOOLS] retrieve_visible_tools landed` PROJECT_LOG line so the orchestrator wires shadow.

## Acceptance
- STEP 0 lands (HOT_SET floor extended); STEP 1 `retrieve_visible_tools` + unit tests (core-floor-subset, fail-open, monotonic-no-hide, recall on a synthetic query/expected-tools fixture) green; STEP 7 corpus at 121 (or flag the gap). Full agent suite green. Branch off main, rebase before registering, union registrations. Report `reports/inflight/tool-retrieval/report.md`. The orchestrator wires shadow + measures recall@k on the dashboard; enforce stays OFF until recall >=0.99/flow.
