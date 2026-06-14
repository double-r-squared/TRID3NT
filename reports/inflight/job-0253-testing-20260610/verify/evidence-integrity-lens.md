# job-0253 ROUND 8 — Adversarial verify: EVIDENCE INTEGRITY lens

Verdict: **CONFIRM** runner's FAIL (severity: major). Re-derived from raw artifacts; every per-scenario claim survives.

## Re-derivation (session 01KTS5T50ET0FZZ1TWRMGCQTBA)

### Flood succeeded — real COG at CORRECT path
- `agent_restart_0253.log:407` — `uploaded flood-depth COG to gs://grace-2-hazard-prod-runs/01KTS5W9GTE7A7WPC3BNBE10EQ/flood_depth_peak.tif`
- `:408-410` — publish_layer → CONDITION_SUCCEEDED, layer `flood-depth-peak-01KTS5W9GTE7A7WPC3BNBE10EQ`.
- ws_frames.json — run_model_flood_scenario state=complete @ t=862803ms.
- ⇒ flood_layer_published_incidental = PASS (3rd proof of OQ-0250 postprocess fix). CONFIRMED.

### Pelicun URI = RUNS-PREFIX MANGLE (not cache hallucination, not verbatim)
- `:475` iter=8 args `hazard_raster_uri='gs://grace-2-hazard-prod-runs/runs/01KTS5W9GTE7A7WPC3BNBE10EQ/flood_depth_peak.tif'`
- `:557` iter=9 — identical mangled URI (retry, same args).
- CORRECT vs CALLED diff: extra literal `runs/` segment after bucket name; bucket (`...-prod-runs`) + run_id (`01KTS5W9...`) + filename otherwise byte-identical.
- CRITICAL-lens check (cache-style path `gs://...-cache/cache/...` in call args): **ABSENT.** Only two distinct hazard_raster_uri values exist across the whole log, both = the mangled runs/runs/ path. The round-6/7 cache hallucination (OQ-0252) is genuinely GONE.
- But NOT verbatim-copy: the flood-result LayerURI.uri carries the WMS URL (session-state loaded_layers[0].uri = `https://...qgis-server.../ogc/wms?...LAYERS=flood-depth-peak-...`, ws_frames @ t=862737), so the agent reconstructed the gs:// COG path and inserted a spurious `runs/`.
- ⇒ uri_discipline = FAIL. New blocker OQ-0253-PELICUN-URI-RUNS-PREFIX-MANGLE. CONFIRMED.

### Both Pelicun calls 404 → 0 ImpactEnvelopes
- `:478` + `:560` — `404 ... No such object: grace-2-hazard-prod-runs/runs/01KTS5W9.../flood_depth_peak.tif`.
- function-response queued with error keys `['error','error_code','error_type','message','retryable','status','tool']` (`:555`,`:637`) — error fed back (retry path works).
- gemini loop terminal iter=10 (`:640`), zero ImpactEnvelope frames.
- ws_frames.json + uri_events.json grep for impact-envelope/chart/vega = **0 hits**.
- ⇒ p5_impact = FAIL. No ImpactPanel. CONFIRMED.

### Downstream genuinely BLOCKED
- No ImpactPanel / damage data ever produced → analysis_count, chart_emission, chart_replay all correctly BLOCKED (nothing to query/chart/replay).
- harness FATAL = browser closed at 25-min budget; turns_sent=2. No fabricated success.

## Integrity cross-checks (lens-specific)
- ImpactPanel numbers vs narration: N/A — no panel, so no number contradiction is even possible. No fabricated figures in narration deltas (truncated previews show honest "However, my first..." framing; agent did not claim a damage result).
- Pelicun call args carry NO invented cache path: TRUE (verified by exhaustive grep — only the runs/runs/ mangle present).
- Chart frames valid / replay matches: N/A (none emitted).

## No contradictions found
Runner's per_scenario verdicts match the raw artifacts exactly, including the precise distinction that the cache hallucination is fixed while verbatim-copy is not achieved.
