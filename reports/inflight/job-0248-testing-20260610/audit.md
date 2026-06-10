# job-0248-testing-20260610 — Stage 3 FINAL round (frozen kickoff)

**Specialist:** testing
**Opened:** 2026-06-10

## Goal
Stage 3 FINAL verification round. Three gating goals, all must PASS for overall PASS:
1. **case2_render_zero_gemini** — Case-2 render proof with ZERO Gemini turns (Case rehydration of round-3 plume layer; fall back to direct WMS GetMap PNG if rehydration predates publish).
2. **p5_impact** — explicit 2-step Pelicun prompt -> ImpactPanel headline numbers.
3. **sandbox_gate_live** — post hot-set fix (commit 5026784: code_exec_request now in HOT_SET_TOOLS); numpy [1,5,9,12] -> SandboxCard -> Proceed -> LOCAL sandbox -> status=ok (mean 6.75, max 12.0) -> narration with REAL numbers.

## What changed since round 4 (job-0247)
- commit 5026784: code_exec_request now in HOT_SET_TOOLS — round-4 OutOfAllowedSetError fixed. Agent ALREADY restarted on it (:8765, 89 tools). DO NOT restart.
- QGIS Server cold-started: round-3 plume layer NOW SERVES (GetCapabilities verified round 4).
- Round-4 p5 lesson: "using existing flood layer" in empty case elicits clarification (correct). Use EXPLICIT prompt.

## Scenarios
- **R** — Case 2 render proof (ZERO Gemini turns). Open round-3 Case (plume published). Expect rehydration repopulates layer panel + WMS overlay renders. Screenshot map + panel; assert bbox Idaho via __grace2GetMap. Fallback: GetMap HTTP fetch -> save PNG.
- **B** — P5 + analysis + charts (FRESH Case, ~5 turns): explicit Pelicun prompt for Fort Myers; structure count; chart; refresh+replay.
- **C** — live sandbox gate (FRESH Case, ~2 turns): numpy mean/max -> SandboxCard -> Proceed -> local sandbox -> real numbers.

## Live-drive rules
NO inject seams (read-only __grace2GetMap OK). <=8 Gemini turns. ~120s between scenarios. 429=STOP. NEVER push. Commit report dir. GCP Bash dangerouslyDisableSandbox:true.

## Verdict gate
Overall PASS = case2_render_zero_gemini + p5_impact + sandbox_gate_live all PASS.
