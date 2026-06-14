# job-0257 — Fable-5 adversarial verification (refute-by-default)

**Verdict: REFUTE (severity: major).** Defects #2/#3/#4 confirmed and properly
fixed/escalated; tests substantive (33 pass, re-run); live proofs reproduce.
But the HEADLINE root cause (#1, "Gemini hallucinates URI tails") is
**mechanistically misattributed**, its fix is a consumer-side symptom patch,
and the original user-observed failure provably still occurs on multiple paths.

## What I re-derived: defect #1's true root cause is the AGENT, not Gemini

Gemini never received the URI tail. `compute_hillshade` returns a `LayerURI`
**object** (not a dict). `summarize_tool_result` → `_coerce_to_summary_value`
(services/agent/src/grace2_agent/adapter.py, repr branch: `s = repr(value);
if len(s) > 200: s = s[:200] + "…"`) serializes it as a repr **clipped at 200
chars**. Reconstructed exactly (LayerURI repr = 316 chars; `uri=` cache key
starts at repr index 186):

```
"result": "LayerURI(layer_id='hillshade-5be7232a30eaeeb4e7f30c17e8799a56-standard',
 name='Hillshade (Standard)', layer_type='raster',
 uri='gs://grace-2-hazard-prod-cache/cache/static-30d/hillshade/090a4ff8d9a083…"
```

Exactly **14 hex chars** of the 32-hex cache key survive (200 − 186 = 14).
All three demo "hallucinations" diverge at **exactly char 14**:
- chicago #1: `090a4ff8d9a083|f67c…` → published `090a4ff8d9a083|b284…`
- seattle:    `4007d642cb157d|11f5…` → published `4007d642cb157d|22b1…`
- chicago #2: `090a4ff8d9a083|f67c…` → published `090a4ff8d9a083|21a4…`

The runner observed "first ~14 hex chars preserved" three times and never
asked why exactly 14. Deterministic truncation + stochastic completion — the
agent handed Gemini a cut-off string ending in "…" and Gemini completed it.
Commit d933622 does **not** touch adapter.py; the producer-side defect is
fully intact. The flood path is immune only because the composer passes the
URI programmatically (as the runner noted without drawing the inference).

## Why this makes the #1 fix a symptom patch (original failure recurs)

1. **Undefended consumers:** every tool that accepts a `gs://` URI echoed from
   a `LayerURI`-object function_response (clip_raster_to_polygon/bbox,
   compute_zonal_statistics, colored-relief→hillshade chains, the Pelicun
   chain) still receives the truncated URI; only `publish_layer` got a guard.
   This is the same class behind jobs 0252/0253/0255 "URI discipline" failures.
2. **Guard already degraded in prod (re-ran their proof from scratch):** QGIS
   wrote `63871724….tif.aux.xml` next to the proof raster; the LCP tie-break
   (`scored[0][0] > scored[1][0]`) now refuses correction. The runner's exact
   proof mangle today raises `LAYER_URI_NOT_FOUND` instead of auto-correcting
   (verified live against GCS). Recovery exists via the retry loop, but the
   error message is itself clipped to 500 chars in `summarize_tool_result`.
3. **Cache-hit CRS recurrence mislabeled "optional":** `_ensure_output_crs_…`
   runs only inside the cache-miss fetch_fn. Default-params Chicago/Seattle
   requests cache-HIT the pre-fix LOCAL_CS objects (keys 090a4ff8…/4007d642…,
   verified still broken-keyed in GCS). Post-IAM-grant with
   `QGIS_SERVER_IGNORE_BAD_LAYERS=1`, that layer is silently dropped →
   success card + nothing on the map = the EXACT original symptom. The
   overwrite commands exist in USER_UNBLOCK.md but as "Optional cleanup" —
   they are REQUIRED for the user's two demo prompts.

**The missed root fix is one line of altitude:** stop truncating `uri` in the
function_response (serialize LayerURI via `model_dump` in
`summarize_tool_result` instead of the 200-char repr clip). The report's
follow-up #6 ("layer-handle indirection") still frames it as LLM fragility.

## What I confirmed (re-run from scratch, live)

- Log lines 446/450/454, 508/512/515, 558/561/564: real cache-write keys vs
  mangled publish keys — match runner's table exactly.
- Live GCS: all 3 hallucinated objects absent; real keys present.
- Worker exit-0-on-error: worker.py ~916-927 `WorkerResult(status="error")`;
  __main__.py docstring + `return 0` — as cited. Post-publish .qgs
  verification genuinely closes the false-success class.
- gdaldem CRS A/B reproduced from scratch with a synthetic EPSG:5070 DEM:
  bare env → `LOCAL_CS` (epsg=None); PROJ_LIB set → EPSG:5070. Deterministic.
- Cache-bucket IAM (live): no grant for grace-2-qgis-server SA — defect real.
- Canonical project STILL 500s live ("Layer(s) not valid"); served .qgs has
  zero hillshade layernames, contains elevation-washington — as claimed.
- Tests: 33/33 pass; assertions substantive (exact demo URI auto-correct,
  ambiguity refusal, retryable listing, corrected RASTER_URI in
  RunJobRequest, WORKER_PUBLISH_NOT_APPLIED).
- proof3 GetMap re-run live: HTTP 200, 3503 opaque px, 1253 colors —
  non-blank reproduces. proof.qgs (cache-sourced) 500s — IAM counterfactual
  reproduces.
- Running agent (:8765, pid 3275803, started 13:02) loaded the fixed code
  (file mtimes 12:48 precede process start).

## Verdict rationale

Instruction test: "REFUTE if the fix is a symptom patch (would the ORIGINAL
user-observed failure still occur in any path?)" — it would, on at least
three paths (undefended URI consumers; degraded tie-break; cache-hit CRS
recurrence after the IAM grant). The runner's PARTIAL verdict honestly gated
on the IAM grant, and defects #2/#3/#4 are genuine root-cause work — but
defect #1, the headline of the four, is misdiagnosed in mechanism and patched
downstream of the actual bug, which sits unfixed in adapter.py.

**Required follow-up:** agent job to exempt `LayerURI` (and `uri` fields
generally) from the repr/char clipping in `summarize_tool_result`; promote
the USER_UNBLOCK "optional" cache overwrites to required; fix the `.aux.xml`
tie-break (filter non-`.tif` siblings or strip extensions before LCP).
