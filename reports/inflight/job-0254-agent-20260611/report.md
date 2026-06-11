# Report: LayerURI emission seam + degraded-path gs:// leak closure (RESCOPED per Decision 11)

**Job ID:** job-0254-agent-20260611
**Sprint:** sprint-13.5 Stage 2
**Specialist:** agent
**Task:** Close the publish-failure degraded-path gs:// leak in `model_flood_scenario.py`; introduce `layer_uri_emit.py` as the single emission seam (raster-gs:// guardrail + dormant `SIGNED_URLS` scaffold); wire every `add_loaded_layer` construction site through it; full test list; byte-identical emissions when `SIGNED_URLS` absent.
**Status:** ready-for-audit
**Commit:** `31332cd` (9 files, +620/−29)

*(Placed by the orchestrator from the runner's returned content — the runner's direct report.md write was blocked by the harness; content verbatim.)*

## Summary
Closed the only client-reaching raw-`gs://` LayerURI leak (the publish-FAILURE degraded path in the flood composer) by switching it from "fall back to the raw gs:// COG uri" to **drop-and-narrate**. Introduced `layer_uri_emit.py` as the single emission seam that every `add_loaded_layer` call site now routes through, with a guardrail that turns the §1 fix into an invariant (renderable raster + raw `gs://` → dropped; vector inline-GeoJSON `gs://` and WMS-URL rasters pass untouched) plus the dormant `SIGNED_URLS` env scaffold (default false; `true` → logged WARNING no-op referencing Decision 11). Behavior is byte-identical when `SIGNED_URLS` is absent. Full agent suite returns exactly the 5 proven pre-existing failures.

## Emission-site inventory (§2 — inventoried first)
LayerURI *construction* sites (~70 across `tools/`, `workflows/`) are distinct from client-bound *emission* sites — the places a `LayerURI` actually reaches `PipelineEmitter.add_loaded_layer`. Only two emission sites exist in production code (every other `add_loaded_layer` hit is a docstring/comment):

| # | Site (file:line) | What flows through | Wired through seam? |
|---|---|---|---|
| 1 | `pipeline_emitter.py:857-862` (`isinstance(result, LayerURI)` gate in `emit_tool_call`) | every tool/workflow returning a `LayerURI` incl. the flood wrapper (`model_flood_scenario.py:1029`) + all ~70 fetch/compute/clip tools | Yes — `emit_layer_uri(result)`; skip `add_loaded_layer` on `None` |
| 2 | `server.py:3200-3214` (job-0272 `publish_layer` wrap site) | bare WMS `http(s)` string wrapped into a `LayerURI` | Yes — `emit_layer_uri(LayerURI(...))`; skip on `None` |

Notes: the §1 flood site is upstream of #1 (it removes the gs:// layer from the envelope, so the wrapper returns a dict and #1 never fires); `model_groundwater_contamination_scenario.py` returns `PlumeLayerURI` (different type, not caught by the `LayerURI` gate — out of scope, flagged below); vector tools' `gs://` LayerURIs rely on the job-0175 inline path and must pass untouched (they do).

## Degraded-path design chosen (§1)
Chosen: **drop-and-narrate** (kickoff's preferred option). On `PublishLayerError` the composer no longer appends the raw-`gs://` `lyr`. Envelope stays a success envelope (metrics/provenance/forcing survive) → narration truthful, job-0177 retry loop has a real result; zero renderable layers reach the client; wrapper returns the envelope dict (not a LayerURI) so site #1 never fires. The "non-renderable marker" option was rejected because `LayerURI` (execution.py:113) has no such field and contracts are out of scope — proposed as an Open Question.

## Seam pass/block matrix (§2 guardrail)
`emit_layer_uri(layer) -> LayerURI | None`:

| layer_type | uri scheme | result | rationale |
|---|---|---|---|
| raster | `gs://…` | DROP (`None`) + WARNING | MapLibre can't fetch gs://; the leak class |
| raster | `http(s)://…` (WMS) | PASS (identity) | renderable WMS URL |
| vector | `gs://…` | PASS (identity) | job-0175 inline path; browser never fetches gs:// |
| vector | `https://…` | PASS (identity) | renderable directly |
| raster | `/vsigs/…`, local | PASS | not the leak class; no over-block |

`SIGNED_URLS`: default false (`0/no/""/off` falsy); `true/1/yes` truthy → WARNING ("…no direct-fetch surface exists … see Decision 11") + identity (same object; guardrail still drops raster-gs://).

## Byte-identical-emission proof (§3)
`test_emit_byte_identical_with_seam_for_passing_layers`: seam-routed WMS raster `session-state.loaded_layers` dict `==` direct-`add_loaded_layer` dict. `test_emit_byte_identical_under_signed_urls_true`: payload identical absent vs `=true`. Both pass.

## Changes Made
- `src/grace2_agent/layer_uri_emit.py` (NEW): the seam (`emit_layer_uri` + `signed_urls_enabled` + `SIGNED_URLS_ENV`), loud docstring → Decision 11 + scout Architecture A.
- `src/grace2_agent/pipeline_emitter.py`: import seam; route the isinstance gate through it.
- `src/grace2_agent/server.py`: import seam; route the job-0272 publish wrap site through it (no touch to job-0255 proxy hunks).
- `src/grace2_agent/workflows/model_flood_scenario.py`: §1 drop-on-publish-failure (comment + warning log updated).
- `tests/test_layer_uri_emit.py` (NEW): matrix, SIGNED_URLS parsing, byte-identity, drop-WARNING.
- `tests/test_pipeline_emitter.py`: WMS-raster funnel test + `test_emit_tool_call_drops_raster_gs_uri` + 2 byte-identity tests (direct-add_loaded_layer tests untouched).
- `tests/test_model_flood_scenario.py`: rewrote Test 27 to drop-behavior; added wrapper-returns-truthful-dict test; fixed happy-path test to patch publish→WMS.
- `tests/test_zoom_to_emission_job_0160.py`: fixed bbox-on-LayerURI test to patch publish→WMS.

## The 2 flood pre-existing failures — exact BEFORE/AFTER (root cause unchanged: publish fails on GCS in test env; the leak is gone)
- `test_run_model_flood_scenario_returns_layer_uri` — BEFORE: `must return a WMS URL in LayerURI.uri … got 'gs://…/flood_depth_peak.tif'`. AFTER: `must return LayerURI on success (not 'dict') … isinstance({…'layers': []…}, LayerURI)`.
- `test_run_model_flood_scenario_triggers_loaded_layers_emit` — BEFORE: `add_loaded_layer called with wrong URI … 'gs://…/flood_depth_peak.tif'`. AFTER: `add_loaded_layer must be called once on success; called 0 time(s) … assert 0 == 1`.

## Verification
- Targeted: layer_uri_emit + pipeline_emitter → 47 passed; zoom/v2/smoke → 14 passed; job-0175 + job-0272/0259 site tests pass.
- Full: `5 failed, 4414 passed, 72 skipped, 1 xfailed` — the exact sanctioned 5 (baseline was 5 failed, 4387 passed).
- Live import-smoke guardrail matrix verbatim (no Gemini/Vertex; dev agent not restarted).

## Invariants Touched
Determinism boundary (preserves); Metadata-payload (strengthens — no dead-row announcement); Engine-registration-not-modification (preserves — seam hazard-agnostic); Rendering-through-QGIS-Server (strengthens — only WMS rasters reach client).

## Decisions Made
- Seam at the two call sites, not inside `add_loaded_layer` — minimal + preserves emitter-mechanics unit tests; alternatives (guardrail inside add_loaded_layer; sanitize-not-drop) rejected.
- Drop-and-narrate for §1.
- Fixed 2 tests (`test_workflow_happy_path…`, `…wrapper_includes_bbox…`) that had been silently passing only because the old gs:// fallback masked a test-env GCS publish failure — patched them to `publish_layer`→WMS (honest happy-path fix; NOT the 2 sanctioned GCS failures, which remain unpatched and still fail).

## Open Questions
- (non-blocking, TENTATIVE) `LayerURI` non-renderable marker — a future schema field (`renderable`/`status`) could surface publish-failure layers as explicitly-broken rows with metrics instead of dropping them. Route to schema post-13.5 if product wants that.
- (non-blocking) `PlumeLayerURI` emission path does not route through this seam (kickoff targets `LayerURI`); flag for the panel if plume publish-failure can leak gs://.

## Dependencies and Impacts
Depends on job-0251/0252, job-0255 (commit d2c2d56 — rebased on, no touch to its files), Decision 11 + Manifest Correction 2. `mint_signed_url` stays deployed-and-dormant; job-0257 deploys with `SIGNED_URLS` absent/false — scaffold ready.

## Hard-constraint compliance
No Gemini/Vertex; dev agent not restarted; only owned files touched (nothing in infra/ or web/); no contract amendments; staged only my files (never `-A`); commit `31332cd` ends with the Co-Authored-By trailer.
