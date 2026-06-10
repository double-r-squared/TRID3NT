# job-0261-engine-20260610 — NWS alerts spill beyond the named state

## Kickoff (frozen)

Live demo "weather alerts for Texas" rendered alerts in surrounding states.

Root cause to verify (orchestrator hypothesis): fetch_nws_alerts_conus
filters by bbox; a state's rectangular bbox overlaps neighbors AND NWS
polygons cross state lines. The NWS API supports precise filtering:
api.weather.gov/alerts/active?area=TX.

Fix directive: add a state-aware path — when the request resolves to a US
state (the agent passes location text or the tool derives state from the
geocode), use area=<STATE_CODE>; keep bbox for non-state areas (then clip
features to the geocoded admin polygon if cheap — fetch_administrative_
boundaries + clip_vector_to_polygon exist, per the project's
clip-to-admin-not-bbox rule). Update the tool docstring so Gemini knows to
pass the state. Tests: area-code mapping, API param construction, bbox
fallback. Live Gemini-free proof: hit the real NWS API once for area=TX and
assert every returned feature's areaDesc/geocode is Texas.

## Constraints

- NO Gemini/Vertex calls. NO Playwright (user is the live gate).
- Do NOT restart the agent on :8765 (user demoing; orchestrator restarts at
  wave end).
- Verification = unit/integration tests + Gemini-free programmatic proofs.
- Commit only owned files on main.

## Owner

engine specialist (Fable 5 fix agent).
