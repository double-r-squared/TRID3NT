# job-0263-agent-20260610 — LAYER-HANDLE INDIRECTION (URI registry)

**Specialist:** agent (Fable-5 fix agent)
**Status:** LANDED — 29 new tests + full-suite regression green; :8765 restart pending (orchestrator)
**Date:** 2026-06-10

## What shipped

A session-scoped URI registry (`services/agent/src/grace2_agent/uri_registry.py`,
new) plus dispatch-seam wiring that retires the LLM-URI-mangling
incident class architecturally instead of by prompt patching.

### The five live incidents this kills (real logged values, each replayed in tests)

| # | Incident | Evidence | Mangled value | Real value |
|---|---|---|---|---|
| I1 | runs/ prefix mangle | job-0253 agent_restart_0253.log:475 | `gs://…-runs/runs/01KTS5W9…/flood_depth_peak.tif` | `gs://…-runs/01KTS5W9…/flood_depth_peak.tif` |
| I2 | layer_id-as-basename | same call (assets_uri) | `…/usace_nsi/usace-nsi--81.9126-26.5476--81.7511-26.6892.fgb` | `…/usace_nsi/852a6cc379b18c865bf9d99ec1acaa35.fgb` |
| I3 | hash-tail hallucination ×3 | job-0257 report (3/3 publishes) | `090a4ff8d9a083b28499252309d12999.tif` etc. | `090a4ff8d9a083f67c0b355caf40241a.tif` etc. |
| I4 | WMS-URL-as-hazard | job-0255 agent_log_p5_turn.txt:170 | `https://…/ogc/wms?…&LAYERS=flood-depth-peak-01KTS8H8…` as hazard_raster_uri | `gs://…-runs/01KTS8H8…/flood_depth_peak.tif` |
| I5 | invented cache hash | same call (assets_uri) | `…/usace_nsi/20240516140505.fgb` | `…/usace_nsi/852a6cc…fgb` |

## Mechanism

**Registration** — `_invoke_tool_via_emitter` (server.py) walks every tool
result (LayerURI models, nested envelope dicts, bare gs:// strings, WMS
URLs) and records `handle -> exact URI`, handle = `layer_id` (minted
`uri:<basename>` keys for bare strings). A `ContextVar` observation hook
(`observe_published_layer`, called inside `publish_layer` after the
job-0257 validation/auto-correction) captures composer-internal publishes,
so the registry knows BOTH faces of a published layer — the gs:// COG and
the WMS display URL — even though the flood composer's envelope only
carries the WMS URL. The registry is module-level keyed by `session_id`
(the job-0259 `_SESSION_ACTIVE_CASE` pattern): survives reconnects, shared
across the web client's sibling sockets. Case-context sync
(`_sync_case_context`) seeds it from persisted Case layers so prior-session
layers resolve after reopen.

**Resolution** — at dispatch, after `normalize_args`, every param in
`RESOLVABLE_URI_PARAMS` (hazard_raster_uri, assets_uri, layer_uri,
value_raster_uri, zone_layer_uri, zone_input_uri, forcing_raster_uri,
damage_layer_uri, flood_layer_uri, raster_uri, vector_uri, polygon_uri,
dem_uri, landcover_uri, value_layer_uri, source_layer_uri, hazard_uri,
model_setup_uri — destinations and server-owned `project_qgs_uri`
deliberately excluded) resolves:

1. **handle given** -> substitute the registered URI (the steady state);
2. **exact known URI** -> pass verbatim; a known WMS *display* URL passed
   where a data URI belongs maps back to the handle's gs:// face (I4);
3. **close mangle** -> substitute + WARNING: layer_id-as-basename (I2),
   exact-basename with shared-path-segment tie-break across multiple runs
   (I1), basename-stem hash-prefix >=12 chars, longest-prefix-unique (I3),
   unique same-directory candidate (I5). Ambiguity NEVER guesses — falls to
   branch 4;
4. **unknown + no match** -> typed retryable `URI_HANDLE_UNRESOLVED`
   (UriResolutionError) whose message LISTS the real handles (most recent
   5 + producing tool) so Gemini self-corrects via the job-0177 retry loop
   instead of re-inventing. Strict rejection applies only inside
   GRACE-managed buckets (`grace-2-hazard-prod-*`, env-overridable);
   foreign-bucket URIs fail open (user-supplied data).

**Surfacing** — the Gemini loop attaches `layer_handles` +
`layer_handles_note` to the function_response whenever a dispatch registers
new layer handles. `/vsigs/` <-> `gs://` normalized throughout.

**Prompt + docstrings** — SYSTEM_PROMPT's job-0252/0255 URI clauses are
replaced by a single "Layer-handle indirection" contract (pass layer_id
handles, never raw gs:// paths; URI_HANDLE_UNRESOLVED lists valid handles;
WMS display URL is never a data URI). Param docs updated on the headline
consumers: run_pelicun_damage_assessment (hazard_raster_uri, assets_uri),
publish_layer (layer_uri), analytical_qa (layer_uri x2, value_layer_uri,
zone_layer_uri).

## Files changed

- `services/agent/src/grace2_agent/uri_registry.py` — NEW (registry, 4-branch resolver, fuzzy matcher, ContextVar hook, session store)
- `services/agent/src/grace2_agent/server.py` — +46 lines: import; resolve-before-dispatch + activate/deactivate around invoke + register-after-result in `_invoke_tool_via_emitter`; `layer_handles` summary augmentation in the Gemini loop; registry seeding in `_sync_case_context`
- `services/agent/src/grace2_agent/adapter.py` — SYSTEM_PROMPT layer-handle contract (replaces the two prompt-patch clauses)
- `services/agent/src/grace2_agent/tools/publish_layer.py` — `observe_published_layer` call at step 8 (post-validation, both faces) + layer_uri docstring
- `services/agent/src/grace2_agent/tools/run_pelicun_damage_assessment.py` — hazard_raster_uri / assets_uri docstrings
- `services/agent/src/grace2_agent/tools/analytical_qa.py` — layer_uri / value_layer_uri / zone_layer_uri docstrings
- `services/agent/tests/test_uri_registry.py` — NEW, 29 tests

## Verification (Gemini-free, per policy)

- `tests/test_uri_registry.py`: **29/29 pass** — registration shapes
  (LayerURI model, dict pair, nested envelope, WMS-in-uri-slot merge,
  /vsigs normalization, pathological results never raise); all 4 branches
  (exact pass, handle substitution, fuzzy+WARNING, typed error w/ handle
  inventory, empty-registry "run the producing tool first", foreign-bucket
  fail-open, ambiguous-hash refusal, wms-only-face refusal); cross-session
  isolation (+ same-object-on-reconnect); all 5 incidents with verbatim
  logged values (I1 with TWO flood runs registered — tie-break picks the
  right ULID; I3 parametrized over all three logged mangles with both
  cities' rasters registered; I5 plus its two-fetch ambiguous variant);
  end-to-end seam tests through the real `_invoke_tool_via_emitter`
  (produce -> mangled consume -> tool body receives the real .fgb; branch-4
  error summarized by `summarize_tool_result` as
  `{status:error, error_code:URI_HANDLE_UNRESOLVED, retryable:true}` with
  the handle named in `message`).
- Seam-adjacent regression batch (payload-warning flow, publish_layer,
  system-prompt, multi-turn loop, tool-retry, pelicun, analytical QA,
  solver-confirm, tool-not-found, impact-envelope emission, case layer
  write path, case context reset): **152 passed, 1 skipped**.
- Full agent sweep: **4275 passed, 72 skipped, 1 xfailed, 5 failed — all 5
  pre-existing and owned by concurrent jobs**, proven by stashing this
  job's publish_layer edits and reproducing identically:
  - 3x `test_data_fetch.py` docstring-tier asserts — a concurrent job's
    in-flight docstring rewrite of `data_fetch.py` (dirty in worktree).
  - 2x `test_model_flood_scenario.py` — job-0257's live-GCS
    `_validate_and_correct_layer_uri` gate 404s the test's random run_id
    against the REAL runs bucket (environment-dependent since d933622;
    fails at HEAD with this job's edits stashed).

## Notes / follow-ups

1. Agent on :8765 still runs pre-fix code — orchestrator restart at
   end-of-wave activates this + the SYSTEM_PROMPT change (Gemini cache
   rebuild on restart picks up the new prompt + docstrings).
2. `compute_zonal_statistics.py` docstring update skipped — file was dirty
   from a concurrent job; the param names are covered by the resolver
   regardless. Candidate one-liner for a later polish job.
3. The two `test_model_flood_scenario` failures deserve a testing-job fix:
   patch `_validate_and_correct_layer_uri` (or inject a fake storage
   client) so the suite is hermetic again.
4. Registry is additive within a session (no reset on Case switch) —
   handles embed run-ULIDs/layer_ids so cross-case collisions are
   non-existent in practice; documented in the module docstring.
