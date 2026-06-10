# Kickoff (frozen)

**Job:** job-0241-agent-20260610 — Stage 3 fix wave (orchestrator-direct per AFK directive 2026-06-10)
**Author:** orchestrator (Fable 5 main loop), user-authorized standing ("take jobs into your own hands"). Independent re-verification: one bundled live session (testing agent) + artifact panel.
**Design authority:** reports/inflight/stage-3-fixwave-design-20260610/design.md (read-only diagnosis, commit 00fcf1a) — applying its diffs with orchestrator judgment.

## Scope (from the design)

1. **Bug 1 (critical, gate bypass)**: server.py — `SOLVER_CONFIRM_TOOLS` + `_gate_on_solver_confirm` (modeled on `_gate_on_code_exec`) + `SolverConfirmationCancelledError`; wrapper `run_model_groundwater_contamination_scenario` gains `confirmed: bool = False` passthrough (drops hardcoded `confirmed=True`).
2. **Bug 2 (plume non-render)**: venv sync (`pip install -e .` → fsspec[gcs]/gcsfs present); postprocess_modflow.py hardening (loud WARNING on non-gs:// publish skip; ImportError classified distinctly in `_upload_cog`).
3. **Bug 3**: `flopy>=3.9,<4` pinned in pyproject.
4. **Bug 4**: mcp.py sidecar lifecycle — setsid + PR_SET_PDEATHSIG preexec + process-group kill in close().
5. **Test-gap closure**: new dispatch-path test driving the registered wrapper through `_invoke_tool_via_emitter` proving the gate fires (approve + cancel + timeout paths) — the exact gap that let Bug 1 ship.
6. Re-verify plan: ONE bundled live session post-restart (Case 2 gate→approve→gs:// plume render; then 0237 Pelicun+count+chart+reload; then 0238 sandbox prompt) after 429 cooldown. 0236 MRMS/SFINCS legs stay PARTIAL pending a real CONUS flood warning (slow-tail noted).

## File ownership
server.py, workflows/model_groundwater_contamination_scenario.py (wrapper only), workflows/postprocess_modflow.py (hardening only), pyproject.toml, mcp.py, tests/test_solver_confirm_gate.py (new), venv sync. NO Gemini in this job (the re-verify session is a separate dispatch).

## Acceptance
- Dispatch-path test: gate fires through `_invoke_tool_via_emitter` for the wrapper; approve → composer runs with confirmed=True; cancel/timeout → typed error, no solver dispatch.
- fsspec/gcsfs import green in venv; flopy declared.
- Targeted suites green (gate tests, composer tests, sandbox runner, persistence, mode2, server case handlers); no new failures.
- Agent restarts clean on the runbook; 89+ tools.
