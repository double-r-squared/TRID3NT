# Live demo crib sheet — 2026-06-10 build (commit 76f6aab)

**URL:** http://localhost:5173 (agent live on :8765, demo build with all 13 fixes)

## Recommended flow (each step live-proven at the link level)

1. **Groundwater (Case 2)** — new Case, paste the article from
   `services/agent/tests/fixtures/case2_news_article.txt` + "Model the groundwater
   contamination from this spill."
   → confirmation card with derived params (TCE, Twin Falls, 3.07 kg/s, demo-aquifer
   caveat) → **Proceed** → local MODFLOW (~1 min) → plume layer over Idaho.
2. **Flood + damage (P5)** — new Case: "Run a flood damage assessment for Fort Myers
   with Pelicun: model the flood first, then use the returned flood depth layer with
   the NSI building inventory."
   → NEW: flood confirm card (location/return period) → Proceed → SFINCS cloud solve
   (~10 min, watch the progress envelopes) → flood layer renders → Pelicun (the WMS
   reverse-map guard makes the hazard handoff robust) → ImpactPanel.
   Follow-ups: "How many structures are impacted above damage state 2?" /
   "Show me the damage distribution as a chart." (chart → click → gallery; refresh
   + reselect Case → charts replay).
3. **Sandbox** — same or new Case: "Run a quick Python computation: compute the mean
   and max of the numpy array [1, 5, 9, 12] and print both."
   → SandboxCard with the verbatim code → Proceed → result (mean 6.75 / max 12).
4. **Weather degrade (honest behavior)** — "Show me active flood warnings in Idaho,
   then model the flood." → if no warnings exist, the agent says so and lists what IS
   active (no fabricated layers).

## Things to know

- **Every solver now confirms first** (MODFLOW, SFINCS flood/habitat, sandbox code).
  Cancel is honored and narrated.
- New layers may take a few minutes to appear via WMS until you run the one-liner in
  `reports/inflight/job-0245-testing-20260610/USER_UNBLOCK.md` (or the server cold-starts).
- Gemini quota: space heavy asks a few minutes apart; on a 429 just wait ~10 min.
- If the agent ever needs a restart: the exact command block is in
  `reports/inflight/stage-3-prep-20260609/restart_runbook.md` (add
  `GOOGLE_APPLICATION_CREDENTIALS=~/.config/gcloud/application_default_credentials.json`
  and `GRACE2_SANDBOX_LOCAL=1`, as the current process has).
