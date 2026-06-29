# Tool-description optimization -- news_events (4 tools)

**Branch:** `agent/render-honesty-audit`. Docstrings + type annotations only; no logic/return changes.
Same standard + mechanism as `hazard_modeling.md`.

## Verification
All 4: routing block within first 1000 chars; ASCII-clean; no GCP-infra in first-1000; default-in-Literal
clean; `py_compile` clean. Siblings verified registered. Full pytest left for integration.

## Literal lifts / dangling fixes
- `fetch_openfema_disasters.incident_type` -> 27 values, programmatically verified set-equal to
  `VALID_INCIDENT_TYPES` (default None in-set; `Literal | None` is precedented + survives
  `adapter._simplify_annotation`).
- Existing kept: `web_fetch.extract` (full_html/main_text/json/metadata).
- **Dangling fixes (aggregate_claims_across_sources module docstring):** `search_news` /
  `fetch_news_article` -> `web_fetch`; `geocode_event_location` -> `geocode_location`.
- Left `str`/`list`: `fetch_storm_events_db.event_types` (NCEI categories, list), `claim_targets`
  (closed set but `list[Literal]` unverified locally -- validator enforces), `state`/`state_code` (parametric).

## Notes
Data-only tools (web_fetch, aggregate_claims_across_sources) flagged NOT-a-layer. web_fetch GCS cache
mentions purged. Public NOAA NCEI Storm Events + OpenFEMA + Census TIGERweb sources kept.

## Flag for Orchestrator (out of docstring scope)
`fetch_storm_events_db.py:856` builds a user-visible layer NAME with a runtime f-string em-dash
(` f" -- {...}"` would be the ASCII fix) -- a CODE change, left untouched. Worth an ASCII follow-up.
