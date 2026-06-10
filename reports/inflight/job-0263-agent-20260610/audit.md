# job-0263-agent-20260610 — kickoff (frozen)

LAYER-HANDLE INDIRECTION: kill the entire LLM-URI-mangling class (5 live
incidents: invented cache paths, WMS-URL-as-hazard, hash-tail hallucination
x3, NSI path "not found" in the user's Tampa run, runs/ prefix mangle).

## Rules
- Working dir /home/nate/Documents/GRACE-2, branch main.
- NO Gemini/Vertex calls. NO Playwright (user is the live gate).
  Verification = unit/integration tests + Gemini-free programmatic proofs.
- Do NOT restart the agent on :8765 (user demoing; orchestrator restarts at
  the end).
- Quality over tokens.

## Design (from kickoff)
A session-scoped URI registry on the server: every tool result containing
gs:// URIs (LayerURI.uri, source COGs, FGB outputs, run outputs) gets
recorded as (handle -> exact URI), where handle is the layer_id or a short
stable key surfaced to Gemini in the function_response. URI-consuming tool
params (hazard_raster_uri, assets_uri, layer_uri, value_raster_uri,
zone_layer_uri, forcing_raster_uri, damage_layer_uri...) RESOLVE through
the registry at dispatch:
1. exact URI known -> pass;
2. handle/layer_id given -> substitute the registered URI;
3. unknown gs:// that closely matches a registered one (same basename or
   same hash-prefix>=12 chars) -> substitute + log WARNING;
4. unknown + no match -> typed error TELLING Gemini which handles exist.

Wire into the dispatch path (tool_arg_normalizer or
_invoke_tool_via_emitter — pick the cleanest seam; the registry lives next
to _PENDING_CONFIRMATIONS, session-scoped, survives reconnects). Update
SYSTEM_PROMPT ("pass layer_id handles, never raw gs:// paths") + tool
docstrings for the main consumers.

## Verify (from kickoff)
Unit tests: registration on result, all 4 resolution branches,
cross-session isolation, the 5 historical incident shapes each resolving
correctly (use the real logged values from
reports/inflight/job-0253/0255/0257 evidence). This is THE architectural
fix — be thorough.
