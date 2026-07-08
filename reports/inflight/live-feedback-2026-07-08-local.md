# Live feedback - NATE 2026-07-08 (local build test session)

Source: NATE first hands-on with the local web build after the overnight
fix batch. Verbatim gripes -> triaged items.

## F1 - No turn visibility (CRITICAL)
"i dont see whats going on after the prompt is sent like i dont see any
narration no cards nothing ... the layers and cards finally showed up
after they were done processing"
-> Nothing streams DURING the turn locally; all emissions arrive at turn
end. Suspects: sync tool work blocking the asyncio loop locally (check
GRACE2_SYNC_TOOL_OFFLOAD actually armed in the serving process), qwen
narration arriving only post-tools (model behavior - mitigate with
immediate tool-start card emission), or web-side buffering.

## F2 - Model selector wrong paradigm
"it should either just be the single model we have or we should be able
to hot swap them like the cloud version"
-> Local selector should list REAL installed Ollama models (query
/api/tags) and hot-swap like cloud, or show exactly the configured model.
The generic "Local model" placeholder entry is not it.

## F3 - Layer stuck on loading loop (CRITICAL)
"its stuck on loading like ITS LOCAL THE DATA EXISTS ON THIS COMPUTER WHY
AM I IN A LOADING LOOP"
-> Layer(s) never finish loading in the panel/map. Investigate the
landcover case end-to-end: what URL the web is polling/fetching, whether
tiles 404, whether the loading state is keyed to something cloud-only.

## F4 - Landcover not visible
"i asked for landcover and im not seeing it ... maybe fetched by the looks
of the cards I cant see it"
-> 'show me landcover over washington' produced cards but no visible
raster. Check fetch_landcover return contract (dict, not bare LayerURI),
auto-publish path for it, and the tile template it emits locally.

## F5 - Auth remnants (paradigm change)
"i see cognito and other stuff here that dont apply anymore like we dont
need a login necessarily since its locally ran ... the paradigm is now one
user like period"
-> LOCAL = single user, NO login surfaces at all. Strip/gate every auth UI
(sign-in, sign-out, Cognito copy, account bits) behind the VITE_DEPLOYMENT
seam. Anonymous single-user session is the only identity.

## F6 - Gate timeouts wrong for local
"the gate shouldnt time out now that we control the llm"
-> Confirmation/resolution gates should NOT expire in local mode (no
Bedrock connection economics). Make gate/confirmation timeouts effectively
infinite locally (env-gated; cloud unchanged).

## F7 - General consistency
"im just seeing issues that existed in the previous version here like
consistency" -> catch-all; revisit known cloud-UX papercuts as they
reproduce locally.

## Status
- Recorded 2026-07-08. Fix lanes dispatched (F1/F3/F4 investigation lane,
  F2/F5 web lane, F6 agent lane).

## F8 - Show thinking tokens (feature, extends F1)
"i want to be able to see the thinking tokens, and maybe make this
optional it just boosts the visibility and makes things more responsive,
this thinking text should be greyed and foldable"
-> Stream delta.reasoning from the Ollama path as a typed thinking-delta
envelope; web renders grey collapsible block (streaming-expanded, folds on
answer); Settings toggle (local mode), /no_think stripped per-turn when on.
Dispatched as an addition to the F1 lane (same streaming seam).
