# Audit: Layer-emission chain diagnosis + fix

**Job ID:** job-0159-engine-20260608, **Sprint:** sprint-12-mega Wave 4.6, **Specialist:** engine/agent (Opus — diagnostic + fix)

**Required reads:**
- `services/agent/src/grace2_agent/tools/publish_layer.py`
- `services/agent/src/grace2_agent/pipeline_emitter.py` (lines around 517 — the isinstance(result, LayerURI) gate)
- `services/agent/src/grace2_agent/server.py` `_invoke_tool_via_emitter`
- `services/agent/src/grace2_agent/workflows/model_flood_scenario.py` lines 720-740 (wrapper returns LayerURI vs dict)
- `web/src/ws.ts` (session-state envelope handler)
- `web/src/App.tsx` + `web/src/Map.tsx` (loaded_layers subscription + raster source registration)

### Why

User completed an end-to-end Fort Myers SFINCS run successfully on 2026-06-08 18:58–19:06:
- `publish_layer: execution completed state=CONDITION_SUCCEEDED layer_id=flood-depth-peak-01KTN1PFQ3T83ZC1G7ZK8KXW6Y`
- `model_flood_scenario complete envelope_id=01KTN22SPPHGP2MMEGBWBXYVWD layers=1`

But the web client never showed the layer (no entry in LayerPanel, no raster on map). The layer-emission chain breaks somewhere between server-side success and client-side render.

### Scope — diagnose + fix in one job

#### Part 1 — Trace and identify root cause

Walk the layer-emission chain end-to-end. Hypotheses ranked:

A. **Workflow returns dict, not LayerURI** — `model_flood_scenario.py:721-737` wrapper checks `if envelope.layers: primary = ...; return LayerURI(...)` but falls back to dict on no layers. The recent run had `layers=1` so should return LayerURI — verify by tracing.

B. **pipeline_emitter `isinstance(result, LayerURI)` gate misfires** — line 517 (per layer-emission-contract memory). If the LayerURI passes isinstance check, `add_loaded_layer` should fire; if not, no envelope.

C. **session-state envelope not emitted** — the emit_session_state_update path may not include the new layer.

D. **ws layer envelope not reaching App.tsx subscribers** — client-side subscription bug.

E. **Map.tsx doesn't render raster from new loaded_layers entry** — registration / WMS URL parsing fails silently.

Capture evidence into `reports/inflight/job-0159-engine-20260608/evidence/diagnosis.md` with file:line citations for each rejected/confirmed hypothesis. Use `grep` + code reading, not Playwright (server-side bug).

#### Part 2 — Fix the actual root cause

Based on diagnosis, fix the broken link. Likely surgical 5-30 line change.

#### Part 3 — Verify

Add a unit test for the broken seam. Then live test: prompt agent backend (already running locally) with "Model flood in Fort Myers" → confirm web client renders the layer.

Restart agent backend after fix lands (commit SHA in restart command for evidence).

### File ownership

- `services/agent/src/grace2_agent/tools/publish_layer.py` OR
- `services/agent/src/grace2_agent/pipeline_emitter.py` OR
- `services/agent/src/grace2_agent/workflows/model_flood_scenario.py` OR
- `web/src/ws.ts` OR `web/src/Map.tsx`
- Tests for the fixed seam
- `reports/inflight/job-0159-engine-20260608/`


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

