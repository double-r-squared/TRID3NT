# job-0265-agent-20260610 — kickoff (frozen)

## Title
sprint-13.5 PREREQ: cloud sandbox result transport via Cloud Logging.

## Scope
Implement OQ-SANDBOX-3 option (b): after a Cloud Run Job sandbox execution
completes, the agent reads the result envelope back from **Cloud Logging**
(google-cloud-logging client, filter on the execution name + an unambiguous
envelope marker line). The executor prints its envelope to stdout (Cloud Run
logs); the sandbox runtime SA stays objectViewer-only (Invariant 5) — the AGENT'S
identity does the privileged log read. `GRACE2_SANDBOX_LOCAL=1` (the active demo
path) stays untouched.

MCPClient PDEATHSIG (the other prereq half) ALREADY landed in job-0241 — verify
present and document; do not redo.

## Constraints
- NO Gemini/Vertex. NO Playwright. Verification = unit/integration + Gemini-free
  programmatic proofs.
- Do NOT restart the agent on :8765. web/src edits HMR-live.
- Commit only owned files on MAIN. Declare google-cloud-logging in pyproject;
  pip check must stay clean (fsspec/gcsfs 2026.1.0 + storage<4 pins load-bearing).

## Verify
Unit tests with a mocked logging client (found / multi-line / not-found timeout /
malformed). One live read if ADC + a real past sandbox execution exists; else
document.
