# Audit: Mid-sprint Playwright verification — final UI state checkpoint

**Job ID:** job-0142-testing-20260608, **Sprint:** sprint-12-mega Wave 3.5 (Stage B — gated on Stage A fix jobs)
**Specialist:** testing (Opus — heavy diagnostic + multi-flow verification)

## Why this job exists

Earlier Playwright capture (orchestrator-delegated, pre-Wave-3.5) surfaced critical OQ-PAY-MAP-VECTOR-UNSUPPORTED — Map.tsx was raster-only, so the Case 1 species points + WDPA polygons + Pelicun damage points never rendered on the actual map, only in the LayerPanel. Wave 3.5 Stage A (jobs 0138-0141) lands the fixes: Auth-as-page replaces AuthPanel, Map.tsx vector rendering unblocks every vector LayerURI, payload-warning gets a dev injection seam, MRMS evidence audit re-captures the real cache artifacts.

**This job verifies the final running state with all fixes applied** — a comprehensive checkpoint batch BEFORE Wave 4 closes the sprint. User-facing screenshots that prove the sprint deliverables actually work, not just exist.

## Required reads

- All Wave 3.5 Stage A landed code:
  - `web/src/components/AuthGate.tsx` (job-0138)
  - `web/src/Map.tsx` (job-0139 — vector rendering)
  - `web/src/App.tsx` (job-0140 — payload-warning seam)
- Wave 3 deliverables:
  - `web/src/components/CasesPanel.tsx` (job-0137)
  - `web/src/App.tsx` (job-0137 — case state machine + chat-replay rehydration)
- Wave 2 components (now verified in real running state):
  - `web/src/components/SecretsPanel.tsx` (job-0125)
  - `web/src/components/Mode2OfferModal.tsx` (job-0126)
  - `web/src/components/PayloadWarningInline.tsx` (job-0127)
- Reference probe scripts:
  - `/tmp/grace2_zoomout_probe.py`
  - `/tmp/grace2_wave2_capture.py`
- Memory: `feedback_orchestrator_drives_ui_verification`, `feedback_geographic_clipping_pattern`

## Scope — 7 verification scenarios

Capture all 7 scenarios to `reports/inflight/job-0142-testing-20260608/evidence/`. Use `.venv-agent/bin/python` (has playwright). Reuse the running vite dev server at port 5177 (verify it's up first; restart if not).

### Scenario 1: Auth gate (full-screen, no anonymous flag set)

Clear local-storage (`grace2_anonymous_accepted` flag deleted). Load page.
- Capture: `01_auth_gate_dark.png` — full-viewport AuthGate, "Sign in with Google" + "Continue without saving (anonymous)" buttons visible, branding centered
- Verify: NO map visible, NO chat visible — gate covers the app

### Scenario 2: Anonymous flow → app loads

From scenario 1, click "Continue without saving". Wait for transition.
- Capture: `02_anonymous_app_loaded.png` — main app visible (map + chat + panels)
- Verify: AuthGate replaced by main app; persistence chip near top-right shows "Anonymous" state

### Scenario 3: Cases list + chat replay rehydration

Inject a synthetic `case-list` envelope with 3 fake Cases (different bboxes + hazards). Then inject a `case-open` envelope for one Case with: 4 chat messages (mix of user + agent), 2 loaded_layers (1 raster + 1 vector point layer), map_view at the Case bbox.
- Capture: `03_cases_panel_populated.png` — CasesPanel left rail showing 3 Cases, one highlighted as active
- Capture: `04_chat_replay_with_layers.png` — chat showing all 4 messages in order; layers visible on map; map centered on case bbox

### Scenario 4: Case 1 demo headline — the actual headline

Inject session-state representing a real Case 1 run output:
- bbox = Big Cypress `(-81.5, 25.7, -80.7, 26.5)`
- 1 flood depth raster layer (use the job-0086 Y-flip-fixed Fort Myers flood as a STAND-IN if no Big Cypress flood is cached yet — the test is whether VECTOR layers render, the raster is just context)
- 3 species vector layers (Florida panther, Roseate spoonbill, American alligator) — use mock FlatGeobuf with synthetic-but-plausible point clusters across Big Cypress bbox; each with `style_preset` carrying a distinct color
- 1 WDPA polygon layer (mock fill+outline) covering Big Cypress Preserve area

z11 dark theme.
- Capture: `05_case1_z11_dark_with_vectors.png` — **THE headline** — all 3 species visible as DIFFERENT-COLORED point clusters on the map + WDPA polygon overlay visible + flood depth raster underneath
- Capture: `06_case1_z11_dark_basemap_only.png` — same camera, all overlay layers hidden (alignment proof per codified job-0086 lesson)

**Geographic-correctness gate**: verify that species points fall INSIDE the Big Cypress bbox (not bleeding into ocean), WDPA polygon roughly matches the Big Cypress Preserve outline, no rotation/offset.

### Scenario 5: Pelicun damage choropleth

Inject a mock Pelicun output: vector layer with ~20 building-footprint polygons over Fort Myers, each with `ds_mean` property [0-1]. Use a colormap (green→red) in style_preset.
- Capture: `07_pelicun_damage_z13_dark.png` — damage choropleth visible on Fort Myers basemap, color-graded
- Verify: damage colors visible AS POLYGONS on the map (not just in LayerPanel)

### Scenario 6: Wave 2 UI components in real state

- Inject a `secrets-list` envelope with 2 fake secrets (eBird + IUCN). Capture: `08_secrets_panel_populated.png`
- Inject a `mode2-candidate` envelope at confidence 0.9. Capture: `09_mode2_modal.png`
- Use the NEW `__grace2InjectPayloadWarning` seam (from job-0140) to inject a payload-warning envelope (estimated_mb=150, threshold_mb=25, tool="fetch_goes_satellite"). Capture: `10_payload_warning_inline.png`

### Scenario 7: Visual continuity check

Verify dark/light theme toggle still works post-changes. Capture `11_light_theme_with_vectors.png` showing the Case 1 vectors in light theme.

## Acceptance criteria

- [ ] 11 screenshots captured in evidence/ — each shows the right thing
- [ ] Scenario 4's species points appear as DIFFERENT colors on the actual map (not just LayerPanel)
- [ ] Scenario 4's WDPA polygon renders on the actual map
- [ ] Scenario 5's Pelicun damage colors visible on actual map
- [ ] AuthGate full-screen state captured + anonymous transition works
- [ ] Cases list + chat replay verified
- [ ] All Wave 2 UI surfaces captured in their final running state
- [ ] Geographic-correctness gate: every overlay sits where it geographically should
- [ ] No FROZEN edits
- [ ] Single commit prefix `job-0142:`; co-author line
- [ ] Surface ANY remaining OQs as `OQ-0142-*`

## Honest disclosure

If Wave 3.5 Stage A fixes didn't all land cleanly (e.g. Map.tsx vector rendering partial, or AuthGate stub-only), surface as OQ-0142-INCOMPLETE-STAGE-A and capture what CAN be captured — don't fabricate.

## File ownership (exclusive)

- `reports/inflight/job-0142-testing-20260608/`

## FROZEN

- All implementation files (Wave 3.5 Stage A is the last code-touching wave; this is verification only)

### Codified lessons (do NOT violate)

1. Geographic-correctness gate (job-0086): pixel-level evidence; verify content placement against actual geography
2. Per-species layer discipline: each species must have its OWN color in the map, not a blended single layer

### Acceptance footer

- Returns commit SHA + outcome + manifest of 11 evidence files + 1-sentence captions + OQs surfaced
