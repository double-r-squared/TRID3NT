# job-0261-engine-20260610 — report

## Verdict: DONE (root cause CORRECTED from kickoff hypothesis; fix landed with live proof)

## Root cause — verified against the live demo trace, differs from the kickoff hypothesis

The kickoff hypothesized a bbox filter on `fetch_nws_alerts_conus`. **That tool
has never taken a bbox** — it takes no geographic argument at all. The actual
failure chain, straight from the live agent log (`/tmp/agent_demo3.log`, the
process serving the demo on :8765) at 13:42 on 2026-06-10:

1. User: `show me weather alerts in texas` (session `01KTQK7RA0Y3GDKS3YH40EXHYH`).
2. Gemini's FIRST call was **correct**: `fetch_nws_event(area='TX')` — the
   state-scoped tool with NWS's precise server-side `?area=TX` filter.
3. The Wave 4.10 post-hoc validator REJECTED it: `OutOfAllowedSetError` —
   `fetch_nws_event` was not in `HOT_SET_TOOLS` (but `fetch_nws_alerts_conus`
   was).
4. Gemini fell back to the in-hot-set `fetch_nws_alerts_conus()` — the
   **unscoped nationwide sweep** — and 63 alerts rendered over the Texas view,
   spilling far beyond the named state.

This is the exact failure mode job-0247 fixed for `code_exec_request`
(validator rejects the model's correct first-turn call; the fallback is
worse), recurring on the NWS pair. Telemetry
(`/tmp/grace2_tool_call_telemetry.jsonl`) confirms `fetch_nws_alerts_conus`
with identical args-hash served every recent alerts request, including the
20:02 demo hit.

## Fix — defense in depth across four seams

1. **Hot set** (`categories.py`): `fetch_nws_event` added to `HOT_SET_TOOLS`
   (now 10 tools) so the state-scoped call Gemini already makes on the first
   turn dispatches instead of bouncing. Direct fix for the observed trace.
2. **State-aware path on `fetch_nws_alerts_conus`** (kickoff directive): new
   optional `area` param accepting a 2-letter code ("TX") or full state name
   ("Texas", "state of texas"). When present → precise NWS server-side filter
   `?area=TX` (new URL builder arm), per-state cache key, state-labeled
   LayerURI (`nws-TX-actual-all`, "NWS Active Alerts — Texas (TX)").
   Unrecognized areas raise a typed non-retryable `NWSConusInputError` that
   points Gemini at `fetch_nws_event` — never a silent nationwide sweep.
   `area=None` keeps the original CONUS behavior byte-identical.
3. **Full state names on `fetch_nws_event`**: `_canonicalize_area` now
   resolves "Texas"/"state of texas" → `{"kind": "state", "value": "TX"}`
   (previously raised, which is what shoved the agent toward the CONUS tool
   in any session where the validator wasn't the blocker). FIPS and bbox
   paths untouched (bbox→point under-returns; it cannot spill).
4. **Arg normalizer**: per-tool aliases so LLM-invented kwargs (`state`,
   `state_code`, `state_name`, `location`, `region`, and for fetch_nws_event
   `fips`/`county_fips`) all land on `area`.

Shared substrate: new `tools/us_states.py` — `NWS_AREA_CODES` (single source
for both tools; `fetch_nws_event._VALID_STATE_CODES` is now an alias),
`STATE_NAME_TO_CODE` (50 states + DC + 5 territories), `resolve_state_code`,
`state_display_name`. No fuzzy matching by design ("Texa" → None).

Docstrings (Gemini-visible): `fetch_nws_alerts_conus` now leads with "ALWAYS
pass area='TX' when the user names a state" and routes county/bbox scoping to
`fetch_nws_event` + the clip-to-admin composition
(`fetch_administrative_boundaries` + `clip_vector_to_polygon`) per the
project's clip-to-admin-not-bbox rule. In-tool polygon clipping was NOT added
(not "cheap": it would couple fetcher to clipper; the composition tools exist
and the agent already chains them).

## Evidence

- **Unit/integration**: 164 passed / 5 live-gated skips across
  `test_us_states.py` (new, 30 cases), `test_fetch_nws_alerts_conus.py`
  (+11 job-0261 tests: area URL construction, name resolution, end-to-end
  `area="Texas"` sends `?area=TX` upstream + labels layer, cache-key
  separation TX/FL/CONUS with "TX"=="Texas" sharing one key, garbage area
  fails before any fetch), `test_fetch_nws_event.py` (+4: full-name
  canonicalization, "Texas"=="TX" URL identity, city still rejected, bbox
  fallback untouched), `test_categories.py` (hot set = 10; fresh-session
  `validate_function_call("fetch_nws_event")` regression test reproducing
  the live first turn), `test_tool_arg_normalizer.py` (+2 alias tests).
- **Full agent suite**: 4246 passed / 72 skipped; 4 failures
  (`test_data_fetch.py` x2 docstring-tier, `test_model_flood_scenario.py` x2)
  are PRE-EXISTING — they fail identically with this job's src changes
  stashed (verified), caused by other jobs' uncommitted working-tree edits.
- **Live Gemini-free proof** (kickoff acceptance):
  `GRACE2_TEST_LIVE_NWS_CONUS=1 pytest
  tests/test_fetch_nws_alerts_conus.py::test_live_area_tx_every_feature_is_texas`
  → PASSED. Real `api.weather.gov/alerts/active?area=TX&status=actual`
  returned 7 active alerts; every feature's `geocode.UGC` carries a
  TX-prefixed zone/county code.
- **Magnitude comparison** (same live session): unscoped CONUS sweep = 299
  features of which only 7 Texas-relevant (292 would have rendered outside
  the state, as in the demo); `area=TX` = exactly 7, zero non-Texas leakage.
- **Gemini-declaration proof**: `build_tool_declarations(TOOL_REGISTRY)` →
  `fetch_nws_alerts_conus` params `['area', 'event_types', 'status']`,
  description teaches the Texas example; `fetch_nws_event` description
  teaches full state names.

## Not restarted

The agent on :8765 still runs pre-fix code per the kickoff constraint; the
fix goes live at the orchestrator's end-of-wave restart. After restart, the
live-demo prompt takes either healthy path: `fetch_nws_event(area='TX')`
dispatches (hot set), or `fetch_nws_alerts_conus(area='Texas')` scopes
server-side.

## Commit hygiene note

The working tree carried prior uncommitted edits (Wave 4.10/4.11 docstring
rework on both NWS tool files, ~325 lines of endpoint aliases in
tool_arg_normalizer.py). This job's edits are textually layered on that
state, so the owned-file commit necessarily includes those rode-along hunks.
They are test-passing and already running in the live agent.

## Open questions

- OQ-0261-CONUS-NAME: with `area` support, `fetch_nws_alerts_conus` has
  outgrown its name; a rename (e.g. `fetch_nws_alerts`) would help routing
  but breaks telemetry continuity + cached declarations — orchestrator call.
- OQ-0261-MULTI-STATE: NWS accepts comma-separated `?area=TX,OK`; multi-state
  queries currently require two calls or the CONUS sweep. Deferred until a
  demo needs it.
