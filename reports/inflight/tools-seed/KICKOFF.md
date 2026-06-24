# SEED KICKOFF - Tools / Agent-Config Specialist (first job)

START HERE. Read `agents/tools-specialist.md` (your charter: seam, protocol, escalation, current-state handoff) first, then this.

You can be directed SOLO by NATE for any standalone tool. This seed is your first concrete job so you hit the ground running.

## JOB: `fetch_glm_lightning` + group-energy-density (GED) grid - a GLM lightning DATA fetcher

A peer of the GOES fetchers (`fetch_goes_satellite` / `fetch_goes_archive_animation`). Fetches GOES GLM optical-lightning, bins GROUP energy onto a fixed grid, returns a raster LayerURI. This is a DATA tool - it lives entirely in the seam, no engine/contract/wiring coupling, so land it solo.

### Validated prototype (reuse it - already proven local-first this session)
- `/tmp/glm_proto/glm_proto.py` - bins GLM `group_energy` (Joules) onto a 2 km grid via `numpy.add.at`, over the Florida tropical-cyclone scene: bucket `noaa-goes19` (GOES-East), product `GLM-L2-LCFA`, path `GLM-L2-LCFA/2025/250/18/`, bbox `(-83.5, 25.5, -79.5, 31.5)`, anonymous S3 (`botocore UNSIGNED`).
- `/tmp/glm_glow/` - the gradient-glow STYLIZATION on top (a sandbox-edit SNIPPET, NOT part of this tool; it lands later via the python-sandbox-staging seam the Orchestrator is building). Do NOT bake a "glow tool".

### Scope (promote the prototype to a registered tool)
- New `services/agent/src/grace2_agent/tools/fetch_glm_lightning.py`: params bbox + time window + satellite (`goes-19` East default / `goes-18` West) + accumulation window; lists `GLM-L2-LCFA` granules (~20s each) in window, bins GROUP energy density onto the ~2 km ABI grid (J -> a sensible unit; the prototype logs the recipe), emits a raster COG LayerURI through `publish_layer` with a purple-ramp style preset. Optionally a multi-frame (per ~1-min accumulation) animation matching the GOES `step <N>` frame contract so the scrubber animates it.
- `AtomicToolMetadata` + `@register_tool`; cache shim (`read_through`); `estimate_payload_mb`; `supports_global_query` as appropriate; source-fallback + honest typed error (no detections in window -> explicit empty, not fabricated). Register: `tools/__init__.py` + `categories.py` (a weather/atmosphere or fire-adjacent primary) + `data/tool_query_corpus.yaml`.

### Acceptance (local-first, before any prod)
1. `TOOL_REGISTRY["fetch_glm_lightning"].fn(...)` runs against the REAL `noaa-goes19` scene above and returns a LayerURI; surface a PNG (purple GED over the bbox) - should match `/tmp/glm_proto/ged_over_visible.png`.
2. Unit tests (binning correctness on synthetic events, empty-window honesty, registration/category/corpus coverage). Full agent suite green.
3. Report `reports/inflight/tools-seed/report.md` with the live PNG + test summary.
4. Land to `main` (additive registration union; rebase on latest main first). Tell the Orchestrator via a `[TOOLS] fetch_glm_lightning landed` PROJECT_LOG line. The Orchestrator batches the box deploy after.

### Next in your queue after this
- The python-sandbox-edit SNIPPETS (glow, isolate-X) as the input-staging seam lands (coordinate with the Orchestrator on that edge).
- Backlog atomic tools / fetchers / compute tools as NATE directs.
