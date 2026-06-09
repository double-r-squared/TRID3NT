# Audit: Auto-zoom diagnosis + zoom-on-area-first UX pattern

**Job ID:** job-0160-engine-20260608, **Sprint:** sprint-12-mega Wave 4.6, **Specialist:** engine/web (Opus)

**Required reads:**
- `services/agent/src/grace2_agent/workflows/model_flood_scenario.py` lines 137-180 (_resolve_bbox)
- `services/agent/src/grace2_agent/pipeline_emitter.py` (emit_map_command per layer-emission-contract memory)
- `web/src/ws.ts` map-command routing
- `web/src/App.tsx` onMapCommand callback wiring (job-0072 work)
- `web/src/Map.tsx` fitBounds handler

### Why

User-reported (after successful end-to-end run): no auto-zoom to Fort Myers occurred. The agent should emit `map-command(zoom-to, bbox=...)` after publishing the layer, but the map stayed at CONUS view.

Plus a new UX request: **"can we do a zoom on area then the compute stuff? this is just more responsive design."**

The user wants the bbox to be resolved + zoom-to fire IMMEDIATELY when the workflow starts (before the 5-min SFINCS compute), so user sees something happen RIGHT AWAY. Then pipeline cards roll, then layer appears.

### Scope

#### Part 1 — Trace current zoom-to behavior

Investigate why `map-command(zoom-to)` isn't reaching the client. Same trace pattern as job-0159 but for map-command path.

#### Part 2 — Fix the auto-zoom (after layer publishes)

Whatever the trace finds.

#### Part 3 — Implement zoom-on-area-first (NEW)

Add an `emit_map_command("zoom-to", bbox)` call IMMEDIATELY after `_resolve_bbox()` succeeds in `model_flood_scenario` — BEFORE any compute starts. So as soon as Gemini calls the tool and we have a bbox, map zooms to Fort Myers. User feedback is instant.

This is a small additive change in `model_flood_scenario.py` after line 320 (`resolved_bbox, geocode_result = _resolve_bbox(...)`).

#### Part 4 — Verify

Live test against running agent — confirm map zooms within ~5 seconds of sending prompt, well before SFINCS completes.

### File ownership

- `services/agent/src/grace2_agent/workflows/model_flood_scenario.py` (zoom-on-area-first emit)
- Whatever file is broken for the post-publish zoom-to
- Tests
- `reports/inflight/job-0160-engine-20260608/`


### FROZEN

All files outside the explicit file-ownership list. Especially: every sibling Wave 4.6 job's exclusive files; `reports/complete/**`.

### Codified lessons (do NOT violate)

1. Geographic-correctness gate (job-0086): pixel-level evidence required.
2. Kickoff-front-loaded design: execute scope, surface OQs.
3. UX language discipline: no internal terms in user-facing surfaces.
4. Pre-commit: `git pull --rebase` before commit.

### Acceptance criteria

- [ ] Deliverables landed per scope
- [ ] Live verification per kickoff
- [ ] No FROZEN edits; single commit prefix; co-author line
- [ ] Returns commit SHA + outcome + headline + evidence + OQs

