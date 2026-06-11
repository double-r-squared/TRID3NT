# job-0254 — LayerURI emission seam + degraded-path gs:// leak closure (RESCOPED per Decision 11) — FROZEN KICKOFF

**Specialist:** agent
**Sprint:** 13.5 Stage 2
**Model:** Opus
**Opened:** 2026-06-11 (dispatch HELD until job-0255 lands — server.py contention)
**Depends on:** job-0251/0252 (DONE); Decision 11 (`reports/sprints/sprint-13-5-decisions.md`) + Manifest Correction 2.

## Why the manifest scope changed (read first)
The original scope ("sign every LayerURI via mint_signed_url") assumed the browser fetches GCS objects. The job-0254 design scout (2026-06-11, file:line inventory) proved no such surface exists: rasters reach the client as QGIS WMS run.app URLs (locked down by job-0255's invoker-only + proxy), vectors are inline GeoJSON (job-0175), charts embed data inline, ImpactPanel shows gs:// as text. `mint_signed_url` stays deployed-and-dormant (panel-verified by the 0251b re-panel). THIS job is the remaining real work.

## Scope

### 1. Close the degraded-path gs:// leak
`services/agent/src/grace2_agent/tools/model_flood_scenario.py:810-819`: when `publish_layer` fails, the composer falls back to emitting the raw `gs://` COG in `LayerURI.uri` — the only place a raw gs:// reaches the client (it never renders; MapLibre can't fetch it; it just paints a broken layer row). Fix: on publish failure, do NOT emit a renderable LayerURI with a gs:// uri. Design options (yours): drop the loaded-layer emission and let the narration/tool-card carry the failure honestly, or emit with an explicit non-renderable marker if the contract supports one (check `packages/contracts` LayerURI — do NOT amend contracts in this job; if a marker field would be needed, prefer the drop-and-narrate option and note the contract idea in your report). The tool result the LLM sees must still tell the truth (publish failed, layer not on map) so the retry-on-failure loop (job-0177 behavior) can act.

### 2. `layer_uri_emit.py` — single emission seam (manifest file ownership, repurposed)
New `services/agent/src/grace2_agent/layer_uri_emit.py`: the one place LayerURIs destined for the client pass through before `add_loaded_layer`/tracking. v0.1 behavior: validation + the dormant `SIGNED_URLS` env scaffold (default "false"; when "true" it currently NO-OPS with a logged WARNING "SIGNED_URLS=true but no direct-fetch surface exists — see Decision 11"; loud docstring pointing at Decision 11 + the scout's Architecture A for the future implementation). Wire the existing emission sites through it: the `server.py:~3198` publish wrap site and the composer emission in `model_flood_scenario.py` (and any other `add_loaded_layer(LayerURI(...))` construction sites you find — inventory first, list them in the report). Behavior today must be byte-identical when `SIGNED_URLS` is absent.
- A guardrail INSIDE the seam: refuse (log + drop or sanitize per §1's design) any `uri` that is a raw `gs://` headed for a renderable raster layer — turning the §1 fix into an invariant rather than a single-site patch. Inline-GeoJSON vector LayerURIs (which legitimately carry gs:// in `uri` while the client renders `inline_geojson`) must pass through UNTOUCHED — do not break job-0175 (`pipeline_emitter.py:719-736` reads the uri server-side).

### 3. Tests
- Degraded path: publish failure → no renderable gs:// LayerURI reaches the emitter; narration/tool result truthful; LLM-visible result preserves the retry contract.
- Seam: all existing emission sites route through `layer_uri_emit`; WMS uris pass; raster gs:// blocked (logged); vector-with-inline-geojson passes untouched; SIGNED_URLS absent → byte-identical emissions (snapshot the envelope payloads before/after); SIGNED_URLS=true → warning logged, no behavior change.
- Full agent suite: only the 5 proven pre-existing failures allowed (3x test_data_fetch docstring-tier, 2x test_model_flood_scenario GCS — NOTE these 2 touch the same composer file you're editing; they fail for GCS/network reasons, pre-existing; you must not make them WORSE or silently fix-and-hide them — report their exact failure text before and after).

## Hard constraints
- NO Gemini/Vertex calls. Do NOT restart the running dev agent.
- Files owned: `layer_uri_emit.py` (new), `model_flood_scenario.py`, `server.py` (emission-site rewiring only — job-0255 just landed a proxy route there; rebase on its commit, do not touch its hunks), `pipeline_emitter.py` ONLY if the seam must hook there (keep minimal), tests. NOTHING in `infra/` or `web/`.
- No contracts amendments (propose in report if needed).
- `git add` only files you touched; never `git add -A`. Commit `job-0254: ...` + `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

## Deliverables
`reports/inflight/job-0254-agent-20260611/{report.md,STATE=IN_REVIEW}`; report includes the emission-site inventory, the degraded-path design chosen, and the seam's pass/block matrix. Panel folds into the Stage-2 close panel with job-0255.
