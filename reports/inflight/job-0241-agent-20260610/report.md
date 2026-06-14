# job-0241 report — Stage 3 fix wave (orchestrator-direct)

**Executed:** 2026-06-10, orchestrator-direct per the AFK directive. Commit `e712ca6`.
**Design authority:** `stage-3-fixwave-design-20260610/design.md` (00fcf1a) — applied with one documented deviation.
**Verdict:** PASS (code + tests); live re-verification pending (one bundled Gemini session, dispatched separately after 429 cooldown).

## What landed

| Bug | Fix | Verification |
|---|---|---|
| 1. Confirmation-gate bypass (critical) | `SOLVER_CONFIRM_TOOLS` + `_gate_on_solver_confirm` in the server dispatch path (mirrors the proven code-exec gate; pure extraction builds the confirm card off-thread; emits the existing `tool-payload-warning` card; `CONFIRMATION_TIMEOUT`/`USER_INPUT_CANCELLED` fail closed; `SolverConfirmationCancelledError` typed non-retryable). Wrapper `confirmed: bool = False` replaces the hardcoded `True`; the dispatch site **strips LLM-supplied `confirmed`** so Gemini cannot self-approve (a hole the design didn't call out — closed here). | 8 new tests in `test_solver_confirm_gate.py` driving the gate through the dispatch seam: approve / cancel / timeout / extraction-fallthrough / strip / wrapper-default / registration / source-level wiring assertion |
| 2. Plume non-render | venv synced (`pip install -e .` → fsspec[gcs] + gcsfs present); `_upload_cog` classifies ImportError as ERROR-level deploy defect; `_dispatch_publish_layer` non-gs:// skip is a loud WARNING | import check + existing postprocess tests |
| 3. flopy undeclared | `flopy>=3.9,<4` pinned in pyproject | declared; 3.10.0 satisfies |
| 4. MCP sidecar leak | `setsid` + `PR_SET_PDEATHSIG` preexec + process-group SIGTERM→SIGKILL in `close()` | code review (43-orphan incident documented inline) |
| 5. bubble_count anomaly | test-harness bug (polling stopped before narration stream) — fix folded into the re-verify session's harness instructions, not app code | n/a |

## Design deviation (documented)

Considered injecting a `confirmation_hook` into the composer (single extraction, zero duplication) but rejected it: the hook would have to survive `normalize_args`' signature sweep and appear in the Gemini FunctionDeclaration as a callable-typed param — both plumbing risks. The design doc's dispatch-layer gate re-runs the pure extraction (~seconds, geocode cached); chosen as the lower-risk path. Single-extraction refactor is a candidate cleanup for sprint-14.

## Test results

- `test_solver_confirm_gate.py`: 8/8.
- Adjacent suites (composer, code-exec, sandbox, persistence sessions, translator, mode2-audit): 82 passed.
- `test_run_modflow.py` + full-suite re-run: after venv sync completes (in flight at report time; result appended below).

## Carry-overs

- OQ-FIXWAVE-FLOOD-GATE: `run_model_flood_scenario` / `run_model_flood_habitat_scenario` remain ungated (no confirm-envelope builders yet) — sprint-13.5/14 follow-up, same `SOLVER_CONFIRM_TOOLS` extension point.
- OQ-FIXWAVE-SFINCS-FALLBACK: `postprocess_flood` hard-fails (`COG_UPLOAD_FAILED`) without fsspec rather than falling back — acceptable (cloud-only path) but noted.
- OQ-0228-CONFIRM-ENVELOPE-CHOICE: gate reuses `tool-payload-warning`/`tool-payload-confirmation` (estimated_mb=0 parameter gate) — schema owner to bless or replace with a dedicated pair in 13.5.

## Re-verification plan (next dispatch)

ONE bundled live session after 429 cooldown: Case 2 (gate fires → approve → gs:// plume renders over Idaho) → 0237 scenario (Pelicun + count + chart + reload-replay) → 0238 scenario (sandbox gate). 0236's MRMS/SFINCS legs stay PARTIAL pending an actual CONUS flood warning (slow-tail noted; checked opportunistically at each future live session).
