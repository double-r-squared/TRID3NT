# Wave 4.10 — Stage 2 Independents

**Date**: 2026-06-09
**Workflow**: `wf_6b453149-f18` (440K tokens, 4 agents, ~30 min)
**Verdict**: 4/4 PASS. All landed in parallel with Stage 1 fix workflow per the pacing lesson.

## Headline

**B11 found that 50 of 58 tools were silently falling back to docstring-only FunctionDeclarations.** That's the single biggest hidden-bug discovery of Wave 4.10 so far — likely the root cause of much of the past "Gemini invents kwargs" symptom that drove `tool_arg_normalizer` to exist in the first place.

## B7 — discover_dataset (hybrid BM25 + dense + name-substring)

**Verdict**: PASS, needs adversarial-verify (touches LLM-facing routing surface).

- New tool `services/agent/src/grace2_agent/tools/discover_dataset.py` (~570 LOC). Registered with `supports_global_query=False`, `ttl_class="live-no-cache"`, `cacheable=False`.
- Three retrieval channels fused via **Reciprocal Rank Fusion (k=60)**:
  - **BM25** via `rank_bm25.BM25Okapi`. Whitespace + lowercase + underscore-preserving tokenizer so verbatim tool names survive as single tokens.
  - **Dense** with opportunistic backend selection: sentence-transformers `all-MiniLM-L6-v2` → Vertex `text-embedding-005` → hashed-token 256-dim fallback. Live in dev env via fallback; will pick up the real backend in deployed agent container.
  - **Name-substring boost** with strict generic-word filter (`polygon`, `raster`, `vector`, `clip`, `compute`, `run`, `fetch`, `model`, etc.) so over-boosting doesn't happen. Suffix-stripping catches "flooding"/"flood", "zones"/"zone".
- Index content per tool: tool name (doubled-weight), full audited docstring, all synthetic queries from `tool_query_corpus.yaml`.
- Lazy module-level index cached under `threading.Lock`. First call builds (~ sub-ms for 76 tools); subsequent calls reuse.

Live invocation results:

| Query | Top-3 |
|---|---|
| "weather alerts" | fetch_nws_alerts_conus / fetch_raws_weather / fetch_nws_event |
| "show flood zones" | fetch_fema_nfhl_zones / run_model_flood_habitat_scenario / run_model_flood_scenario |
| "national parks polygons" | fetch_wdpa_protected_areas / fetch_usace_levees / clip_raster_to_polygon |

21 unit tests pass + 97-test wider sweep with no regressions.

## B11 — Gemini OpenAPI subset compliance audit (THE big finding)

**Verdict**: PASS, no adversarial-verify required (mechanical fix).

**Root cause**: tools use `from __future__ import annotations` so all annotations are lazy strings. The genai SDK's `from_callable_with_api_option` calls `typing.get_type_hints()` which resolves them — and **failed silently** on complex types: `tuple[float, 4]`, `Union`, `LayerURI`, `SecretRecord`. On failure it falls back to a **docstring-only declaration with no parameter schema** — so Gemini only knows the tool exists, not what args to pass.

**50 of 58 tools were in this broken state.** This is huge — explains why `tool_arg_normalizer` exists at all (Gemini was inventing kwargs because it had no parameter schema to follow).

Fix is fully centralized in `services/agent/src/grace2_agent/adapter.py`. Added 5 helpers (`_is_union_type`, `_union_args`, `_is_tuple_annotation`, `_simplify_annotation`, `_normalize_callable_for_gemini`) and updated `build_tool_declarations` to normalize each callable before schema generation.

Violation categories resolved:
- **38 tools** with `-> LayerURI` return type → normalized to `-> dict`
- **8 tools** with `bbox: tuple[float, 4] | None` → `list[float] | None`
- **2 tools** with `year_range: tuple[int, 2] | None` → `list[int] | None`
- **1 tool** with `area: str | tuple[...]` → `str`
- **1 tool** with `seed_point: tuple[float, 2] | None` → `list[float] | None`
- **1 tool** with `SecretRecord | None` → `str | None`
- **1 tool** with `float | list[float] | None` → `list[float] | None`

**Final audit**: 57/58 fully compliant + 1 zero-param (list_categories, no schema expected). 76 pytest tests pass.

This change should **measurably improve routing accuracy** at Stage 4 verification — Gemini will now see real parameter schemas for tools where it previously got nothing.

## B12 — Tool annotation metadata

**Verdict**: PASS, no adversarial-verify required (pure metadata).

- `AtomicToolMetadata` gains 4 MCP-standard boolean fields: `read_only_hint`, `open_world_hint`, `destructive_hint`, `idempotent_hint`
- Defaults: `read_only=True`, `open_world=False`, `destructive=False`, `idempotent=True`
- `register_tool()` gains matching optional kwargs; backward-compatible (all existing call sites work)
- **All 70 registered tools annotated** with inline `Annotations:` comment + explicit kwargs where non-default

Annotation distribution: 65 read_only / 49 open_world / **1 destructive** (`publish_layer` — overwrites layer in shared .qgs) / 65 idempotent.

Logic: `fetch_*` + `web_fetch` + `catalog_*` → `open_world=True` (hits external APIs). Compute / clip / extract / discover / aggregate → all defaults. `publish_layer` → `destructive=True`, `idempotent=False`. `run_solver`, `wait_for_completion`, `qgis_process`, `run_pelicun_damage_assessment` → `idempotent=False`.

24 tests pass (17 new + 7 existing).

## B13 — tool_arg_normalizer alias pre-population

**Verdict**: PASS, no adversarial-verify required (mechanical table update).

Pre-populated `_TOOL_SPECIFIC_ALIASES` for all 17 Wave 4.10 tools (the 14 endpoints + 3 pfdf sub-tools).

Per-tool alias counts: 5-20 aliases each. Total **237 aliases across 17 tools**.

Examples:
- `fetch_fema_nfhl_zones`: 12 aliases (bbox / bounding_box / extent / bounds, zone_filter / zones / flood_zones, sfha_only / sfha / special_flood_hazard, ...)
- `fetch_hrrr_forecast`: 18 aliases
- `fetch_noaa_nwm_streamflow`: 17 aliases (configuration / model_run / cfg, datetime / date / time, ...)
- `fetch_gridmet`: 20 aliases (variables / vars / fields / parameter, ...)

237 normalizer tests pass + 28 existing tests unaffected.

## Cumulative Wave 4.10 token spend

| Workflow | Tokens |
|---|---|
| Stage 0 baseline | 145K |
| Stage 1 main | 3.66M |
| Stage 1 fixes | 589K |
| Stage 2 independents | 440K |
| Stage 2 dependents (in flight) | TBD |
| **Wave 4.10 so far** | **~4.83M** |
| Sprint-12 cumulative | 21.85M |

## What's still in flight

`wf_df02b5ad-8bc` — Stage 2 dependents: B5 (12-category registry + post-hoc validator) + B6 (Gemini CachedContent integration) + B10 (thought_signature preservation + parallel-call audit) + 12-verifier adversarial panel (3 jobs × 4 lenses, parallel-across-jobs per pacing memo).

## Halt point

Per user direction 2026-06-09 ("halt work when we reach a sprint end or stage end and are about to handle a new one"): when Stage 2 dependents land, Stage 2 is complete — halt and confirm before Stage 3 dispatch.
