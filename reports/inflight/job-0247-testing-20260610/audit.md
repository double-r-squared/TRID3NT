# job-0247-testing-20260610 — KICKOFF (verbatim)

You are the testing specialist. Job job-0247-testing-20260610 — Stage 3 re-verify ROUND 4: scenarios B + C after the context-carryover fix (commit 74fc0d6).

## Working dir
/home/nate/Documents/GRACE-2
FIRST: mkdir -p reports/inflight/job-0247-testing-20260610/evidence; audit.md (kickoff verbatim); STATE RUNNING.

## What changed since round 3 (job-0245 — read its report.md first)
- commit 74fc0d6: Case create/select now CLEARS the per-connection LLM conversation (state.chat_history + turn_count) — the round-3 mis-route (every post-switch prompt re-routed to the Twin Falls groundwater composer) is fixed and unit-pinned. Each scenario in a FRESH Case now genuinely starts clean.
- Agent restarted on the fix: :8765, 89 tools, env carries GOOGLE_APPLICATION_CREDENTIALS (vsigs PROVEN Gemini-free) + GRACE2_SANDBOX_LOCAL=1. Do NOT restart.
- The Case-2 render leg is USER-GATED (QGIS Server project-cache env change needs user approval — reports/inflight/job-0245-testing-20260610/USER_UNBLOCK.md). NOT in scope this round, EXCEPT one Gemini-free opportunistic check (below).
- Harness to adapt: web/tools/stage3_reverify_round3_job0245.mjs.

## LIVE-DRIVE RULES (unchanged)
NO inject seams (read-only __grace2GetMap OK). <=9 Gemini turns. ~120s between scenarios. 429 = STOP. NEVER push. Commit report dir. GCP Bash: dangerouslyDisableSandbox:true.

## Scenario B — flood + P5 + analysis + charts (FRESH Case, ~6 turns)
1. NEW Case -> "Model flood damage for Fort Myers using the existing flood layer."
   ASSERT FIRST: the prompt routes to a FLOOD/damage chain (any Twin-Falls/groundwater gate appearing = context-carryover REGRESSION, capture + FAIL that assert).
   EXPECT: Pelicun/impact chain -> ImpactPanel slides out with headline numbers [P5 EVIDENCE]. Screenshot.
   NOTE: with vsigs fixed, a fresh SFINCS deck-build should also work if the chain goes that way; either path (existing-layer Pelicun or fresh model) is acceptable — record which ran.
2. "How many structures are impacted above damage state 2?" -> narrated count consistent with the panel.
3. "Show me the damage distribution as a chart." -> chart-emission -> inline ChartStack -> click -> gallery. Screenshots.
4. Browser refresh + reselect the Case -> charts replay. Screenshot.
If the loop stalls (>240s zero progress): capture agent log + WS frames, mark BLOCKED, continue to C.

## Scenario C — local sandbox gate (FRESH Case, ~2-3 turns)
NEW Case -> "Run a quick Python computation: compute the mean and max of the numpy array [1, 5, 9, 12] and print both." (self-contained — no layer dependency, removes any excuse to route elsewhere)
EXPECT: code_exec_request -> SandboxCard with verbatim code + Proceed/Cancel (screenshot) -> Proceed (registry fix) -> LOCAL sandbox executes -> status=ok card -> narration with the real numbers. WS ordering assert: code-exec-request BEFORE any sandbox spawn.

## Gemini-free opportunistic check (zero turns)
curl the QGIS GetCapabilities (see USER_UNBLOCK.md for the URL) and grep for plume-concentration — if the service cold-started since round 3, the layer may already serve; record either way as evidence/qgis_cache_check.txt.

## Verdict
per_scenario: context_isolation / p5_impact / analysis_count / chart_emission / chart_replay / sandbox_gate_live / qgis_cache_opportunistic. Overall PASS = context_isolation + p5_impact + sandbox_gate_live PASS. report.md; STATE READY_FOR_AUDIT; commit.
Return StructuredOutput.
