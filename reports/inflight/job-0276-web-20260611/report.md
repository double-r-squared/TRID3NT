# job-0276 — save-gate modal trap (the "can't get back into the Case" bug)

**Reproduced Gemini-free** (web/tools/probe_case_reentry_repro.mjs): deleting
a Case as an anonymous user stacked the "Sign in to save" gate ON TOP of the
delete ConfirmationDialog; the gate re-armed on EVERY anonymous
create/rename/archive/delete. An unnoticed gate = full-page click shield
(aria-modal backdrop) — rail clicks died silently with no envelope sent,
matching the user's live symptom; a page refresh "fixed" it by resetting
React state.

**Fixes:**
1. `useSaveGate` remembers "Continue anyway" in sessionStorage — the
   disclaimer shows once per browser session, then never re-traps. Dismiss
   (without acceptance) still re-arms.
2. Delete is no longer save-gated (it has its own ConfirmationDialog;
   "sign in to save" is the wrong prompt for deletion).

**Evidence:** probe verdict selects_sent=5 / case_opens=7 / errors=0 across
create→out→re-enter→rapid-cycle→delete→re-enter; vitest 586 passed (2 new
acceptance tests; sessionStorage cleanup added to the existing suite).
