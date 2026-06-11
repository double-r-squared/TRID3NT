# job-0270 report — validator auto-widen for real tools + publish-to-map discipline

**State:** DONE (Fable runner, MAX effort). **Source:** third live occurrence
of "Compute a colored relief map for Boulder, Colorado" producing no visible
pixels (/tmp/agent_demo7.log, /tmp/agent_demo8.log).

## FIX A — auto-widen the allowed set for REAL registry tools

`services/agent/src/grace2_agent/categories.py` — `validate_function_call`:

- When the called name is in the allowed-set snapshot → pass (unchanged).
- **NEW:** when the name is NOT in the snapshot but IS in the live
  `TOOL_REGISTRY` → auto-widen via `allowed.add_tools((call_name,))` (the
  existing explicit-tools growth mechanism, same monotonic semantics as
  category open / dispatch stickiness), log
  `WARNING allowed-set auto-widen tool=<name> (was outside hot set)`, and
  return — the dispatch proceeds on the FIRST call.
- When the name exists nowhere in the registry → raise
  `OutOfAllowedSetError` exactly as before. **The hallucination guard is
  unweakened** — `OUT_OF_ALLOWED_SET` / `retryable=False` envelope semantics,
  hot-set hint, and the server's circuit-breaker failure accounting are all
  unchanged for non-tools.
- Registry visibility uses the same local-import seam
  `_list_tools_in_category_impl` already uses (`from .tools import
  TOOL_REGISTRY` inside the function — no circular import, no signature
  change; the server call site `validate_function_call(call.name,
  state.allowed_tool_set)` is untouched).

Rationale recorded in the docstring: Gemini sees the FULL catalog via
CachedContent (Option A), so a registry-valid call is correct routing, not a
hallucination. job-0247 (code_exec_request) and job-0261 (fetch_nws_event)
were point-fixes of this same failure mode via hot-set additions; this is the
structural fix — no more per-tool hot-set chasing for first-call rejections.

## FIX B — deterministic publish step in the LLM contract

1. `services/agent/src/grace2_agent/adapter.py` SYSTEM_PROMPT — new
   **"Publish-to-map discipline (CRITICAL — job-0270, live finding)"** clause
   (inserted between Layer-handle indirection and Always-narrate, matching
   the existing clause style): a layer handle / gs:// raster is storage, not
   pixels; SEE/show/map/visualize requests MUST finish with
   `publish_layer(layer_uri=<handle>, layer_id=<descriptive-id>)` + a
   one-line summary; NEVER claim display unless publish_layer returned a WMS
   URL THIS turn; tools whose function_response already carries a `wms_url`
   (e.g. the flood composer) are exempt — they published internally.
2. `services/agent/src/grace2_agent/server.py` — `layer_handles_note`
   strengthened: "A layer is NOT visible on the user's map until
   publish_layer(layer_uri=<handle>, layer_id=<descriptive-id>) has run for
   it — if the user asked to see this layer, call publish_layer with the
   handle before finishing." + the original handle-discipline text (pass
   handles for *_uri params; never construct gs:// paths). Phrased so it
   stays truthful even on publish_layer's own function_response (which also
   drains announcements).

## Evidence (all Gemini-free, per quota discipline)

- `tests/test_validator.py` (rewritten for the new semantics, 15 tests):
  auto-widen for compute_colored_relief / compute_hillshade / publish_layer;
  session persistence + monotonic growth (hot set ⊆ widened set,
  cumulative); WARNING log asserted via caplog; explicit-tools mechanism
  asserted; non-existent names still raise with unchanged error_code /
  retryable / hint; rejected names do NOT pollute the set.
- `tests/test_post_hoc_routing.py` (rewritten, 6 tests): first-call dispatch
  for real tools yields ok envelopes (no OUT_OF_ALLOWED_SET detour);
  hallucinated-name → structured envelope → list_tools_in_category recovery
  loop preserved; category-open fan-out + sticky-after-dispatch preserved.
- `tests/test_publish_discipline_job0270.py` (new, 3 tests): drives
  `_stream_gemini_reply` end-to-end with a fake Gemini —
  (1) FIRST call to compute_colored_relief dispatches in exactly 2 turns
  (call + terminal narration), ok envelope, widened set persists on
  `state.allowed_tool_set`; (2) hallucinated name never reaches dispatch and
  surfaces `OUT_OF_ALLOWED_SET` in the function_response payload; (3) the
  function_response for a layer-producing tool carries `layer_handles` +
  the strengthened note ("NOT visible on the user's map",
  "publish_layer(layer_uri=<handle>", gs:// prohibition).
- `tests/test_system_prompt.py` (+4 tests): publish-discipline section
  present; invisible-until-published sentence; never-claim-display sentence;
  Always-narrate header survives the splice (guards the insertion point —
  this caught a real near-miss during development where the splice initially
  dropped the A1 header).
- Live-shaped log demo (script, no Gemini): three auto-widen WARNINGs fire
  for the exact demo7/demo8 tools, hallucinated name still raises, set grows
  10 → 13 monotonically.
- **Full agent suite** (`.venv/bin/python -m pytest tests/ -q
  --ignore=tests/live`): **5 failed, 4315 passed, 72 skipped, 1 xfailed**
  (log: /tmp/job0270_full_suite.log). The 5 failures are exactly the
  proven-pre-existing set: 3x test_data_fetch docstring-tier
  (fetch_landcover / fetch_river_geometry / lookup_precip_return_period) +
  2x test_model_flood_scenario (returns_layer_uri / loaded_layers_emit).
  ZERO new failures.

## Proposed SRS amendment (user lands; not edited here)

Appendix on the Wave 4.10 allowed-set architecture (the section documenting
`validate_function_call` / OUT_OF_ALLOWED_SET in the agent-loop appendix —
B/D family): amend the validator contract from "out-of-set calls are
rejected with OUT_OF_ALLOWED_SET" to "out-of-set calls to REGISTERED tools
auto-widen the allowed set (WARNING-logged) and dispatch; only names absent
from the tool registry are rejected with OUT_OF_ALLOWED_SET (hallucination
guard)". Also note the function_response `layer_handles_note` now carries
the publish_layer instruction, and SYSTEM_PROMPT carries the
publish-to-map discipline clause.

## Risks / notes for the orchestrator

- The allowed set no longer throttles which REAL tools Gemini can invoke —
  it is now purely (a) a hallucination guard and (b) telemetry on hot-set
  misses (the WARNING log). If the hot set was ever doing cost/safety
  gating, that gating is gone for registry tools; the solver-confirm gate,
  payload-warning gate, and circuit breaker are unaffected and remain the
  real safety boundaries.
- `eval_routing_live.py` / dynamic-hot-set tuning that counts
  OUT_OF_ALLOWED_SET bounces will see the bounce rate drop to ~0 for real
  tools; the auto-widen WARNING is the new signal for hot-set tuning.
- The agent process must be RESTARTED to pick up these changes
  (orchestrator-owned, per kickoff).
- Prompt-clause exemption ("function_response already contains a wms_url")
  matches the flood composer's behavior; if a future tool returns a wms_url
  WITHOUT publishing, the clause would wrongly excuse it — keep the
  layer-emission contract (wms_url implies published) intact.
