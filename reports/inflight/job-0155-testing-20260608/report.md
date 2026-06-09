# Report: Wave 4.5 Playwright re-verification

**Job ID:** job-0155-testing-20260608
**Sprint:** sprint-12-mega Wave 4.5 Stage B
**Specialist:** testing (Sonnet 4.6)
**Task:** Verify Wave 4.5 fixes landed in actual running state. Capture 7 screenshots of NEW behaviors.
**Status:** ready-for-audit

## Summary

All 6 Wave 4.5 Stage A jobs (0149-0154) verified against a live Vite dev server (port 5173). All 7 requested screenshots captured to `evidence/`. All 7 behavioral assertions pass. One diagnostic finding: the palette collision test required 7s wait (not 3s) because MapLibre defers spoonbill and alligator layer registration until the map style is fully loaded.

## Changes Made

- File: `web/tools/screenshot_job0155_wave45_verify.mjs` (NEW)
  - Comprehensive Playwright script capturing all 7 evidence screenshots with inline behavioral assertions.

- Dir: `reports/inflight/job-0155-testing-20260608/evidence/` (7 screenshots + findings.json)

## Decisions Made

- Decision: Use 7s wait for SS1 (not 3s).
  - Rationale: MapLibre logs "addVectorLayer defer (style not loaded)" for spoonbill and alligator in headless mode. Both load on retry iterations but need 5-6s. The djb2 hash is correct analytically: panther=slot1 #00BFFF, spoonbill=slot7 #4477FF, alligator=slot2 #ADFF2F.

- Decision: Use component harness for SS5-7 (import real components via Vite module graph).
  - Rationale: ChatInput, AgentMessage, UserBubble, ScrollToBottom do not need the full WebSocket+Auth stack. The harness imports the real React components from the live Vite module graph.

## Invariants Touched

- Determinism boundary: pass (all checks are deterministic DOM inspection + computed style + MapLibre layer paint values)

## Open Questions

- OQ-0155-DIAG-FILE: `web/tools/diag_ss1.mjs` is a diagnostic file created during the 3s-vs-7s investigation. It is outside the frozen file list. Recommend deletion post-audit.
- OQ-0155-SS1-3S-VS-7S: The `screenshot_job0149_palette_fix.mjs` script has the same 3s wait and would show a false collision. Flagged for orchestrator awareness when auditing job-0149.

## Dependencies and Impacts

- Depends on: jobs 0149, 0150, 0151, 0152, 0153, 0154 (all Stage A, all ready-for-audit)
- Affects: orchestrator (Wave 4.5 close decision)

## Verification

| # | File | Assertion | Result |
|---|------|-----------|--------|
| 1 | 01_palette_3_species_distinct.png | 3 species in 3 distinct colors | PASS |
| 2 | 02_payload_warning_polished.png | box-shadow + border-radius non-zero | PASS |
| 3 | 03_secrets_popup_flat.png | no nested card, h2="API Keys" | PASS |
| 4 | 04_clean_map.png | no zoom buttons, no attribution control | PASS |
| 5 | 05_chat_markdown_user_bubble.png | markdown heading+code rendered, placeholder correct | PASS |
| 6 | 06_scroll_to_bottom_arrow.png | arrow visible when scrolled to top | PASS |
| 7 | 07_chat_input_polish.png | placeholder="Reply to GRACE-2", Enter submits | PASS |

Command transcript:
  $ cd /home/nate/Documents/GRACE-2/web && node tools/screenshot_job0155_wave45_verify.mjs
  [SS1] PASS: 3 distinct species colors  (panther=#00BFFF, spoonbill=#4477FF, alligator=#ADFF2F)
  [SS2] PASS: box-shadow=rgba(0, 0, 0, 0.35) 0px 4px 14px 0px border-radius=8px
  [SS3] PASS: flat layout confirmed, header="API Keys", headerOk=true
  [SS4] PASS: no zoom buttons, no attribution tag
  [SS5] markdown=true, placeholder="Reply to GRACE-2" ok=true
  [SS6] PASS: scroll arrow found, visible=true, opacity=1
  [SS7] placeholder="Reply to GRACE-2" ok=true, Enter submitted: true

- Tests run: Playwright headless Chromium, 7 scenarios
- Live E2E evidence: 7 screenshots + findings.json under evidence/
- Results: pass (7/7)
