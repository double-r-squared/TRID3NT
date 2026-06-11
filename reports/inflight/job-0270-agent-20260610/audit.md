# job-0270 — colored-relief chain-to-pixels: validator auto-widen + publish discipline

**Specialist:** agent (Fable runner, MAX effort, per the user's standing
critical-batch authorization). **Opened:** 2026-06-10, from the third live
occurrence of the "computed but invisible" failure during user demo testing.

## Live failure (frozen kickoff context)

User prompt: "Compute a colored relief map for Boulder, Colorado." The Gemini
agent (raw google-genai SDK loop, `_stream_gemini_reply` in
`services/agent/src/grace2_agent/server.py`) fetched the DEM and computed the
relief, but the chain to visible pixels broke two ways:

1. **VALIDATOR DETOURS** — `compute_colored_relief`, `compute_hillshade`, and
   `publish_layer` are not in the hot set, so the post-hoc validator
   (`validate_function_call` in `categories.py`) rejected Gemini's FIRST call
   to each with `OutOfAllowedSetError`, even though these are REAL registered
   tools (in `TOOL_REGISTRY`). Gemini then guessed category names
   ('terrain_analysis', 'raster') before finding 'terrain_elevation', burning
   2-4 iterations per turn. Evidence: /tmp/agent_demo7.log ~67-141 and
   /tmp/agent_demo8.log ~76-128. Same failure mode previously hit by
   job-0247 (code_exec_request) and job-0261 (fetch_nws_event) — each fixed
   by hot-set addition; this is the structural fix.

2. **PUBLISH OMISSION** — in /tmp/agent_demo8.log (18:37-18:38), after
   `compute_colored_relief` returned successfully (iter=7), Gemini terminated
   the turn with text only (iter=8 "loop terminal") — it NEVER called
   `publish_layer`. A computed raster is invisible until `publish_layer` adds
   it to the QGIS Server project. The user saw nothing on the map.

## Directives

- **FIX A** — `validate_function_call` auto-widens the `AllowedToolSet` for
  names present in the live `TOOL_REGISTRY` (WARNING log, dispatch proceeds,
  monotonic growth via the existing explicit-tools mechanism). Names NOT in
  the registry still raise `OutOfAllowedSetError` (hallucination guard,
  unweakened).
- **FIX B** — (1) SYSTEM_PROMPT gains an imperative "Publish-to-map
  discipline" clause: layer-producing results are not visible until
  `publish_layer` runs; never claim display without a WMS URL this turn.
  (2) The `layer_handles_note` attached to function_responses says explicitly
  the layer is NOT on the map yet and instructs calling `publish_layer` with
  the handle.

## Constraints

- No agent restarts (orchestrator handles), no docs/srs edits (propose in
  report), no remote pushes, no live Gemini calls (evidence is unit/sim).
- Acceptance: FIX A unit tests (auto-widen + persist + guard intact), note
  text proven in the function_response payload, full agent suite with ZERO
  new failures (only the 5 proven-pre-existing allowed), commit local-only
  with surgical `git add`.
