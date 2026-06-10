# job-0268 — agent: turn-start Case binding (cross-Case persistence contamination)

**Specialist:** agent (orchestrator-direct per the user's standing authorization)
**Opened:** 2026-06-10. **Source:** job-0267 adversarial verifier, severity MAJOR
(CONFIRM-with-finding; probes A+B in
`reports/inflight/job-0267-agent-20260610/verify/test_adversarial_job0267.py`).

## Defect

Every turn-scoped persistence site read `state.active_case_id` at WRITE time.
A `case-command(select)` arriving mid-stream (the web client sends no cancel on
rail clicks; the select is handled inline while the turn runs as a background
task) re-aims in-flight writes: Case A's narration row and `role="tool"` cards
persist into Case B's chat collection, permanently. The window is minutes-long
for SFINCS-class tools. The mechanism pre-dates job-0267 but that job amplified
the user-visible impact (real narration + new tool-card rows now replay in the
wrong Case).

Wider blast radius found at fix time — the same write-time read also drives:
- `publish_layer` per-Case `.qgs` routing (`ensure_case_qgs` + `case_id` param)
- `_persist_case_loaded_layers` (layer attribution on the CaseSummary)
- chart persistence `doc_id`
- the D.6 session heartbeat's `project_ids` registration

## Fix shape (the verifier's recommended option 1; option 2 rejected)

Pin the Case once at turn start: `SessionState.current_turn_case_id`, set by
`_prepare_user_turn` AFTER the auto-create-from-root hand-off and before the
first write. All turn-scoped writes resolve through `_turn_case_id(state)`
(pin, falling back to live `active_case_id` for un-prepared callers — tests,
legacy paths). The dispatch wrappers (`_dispatch_gemini_and_persist`,
`_dispatch_tool_and_persist`) and `_invoke_tool_via_emitter` capture the
binding at task/dispatch entry and pass it explicitly (`case_id=` kwarg) so a
cancel-and-redispatch (new turn re-pins while the old turn's finally-persist
is pending) cannot cross-paint either.

Option 2 (cancel `inflight_task` on case-command select/create) is REJECTED as
product-hostile: per-Case streams (job-0266) deliberately let a long solve
continue in its owning Case while the user browses others — the web client
already buffers envelopes by owning stream (`targetKey` pinned at submit).
Killing a minutes-long SFINCS run on a rail click would be a regression.

## Acceptance

1. Inverted probes A+B pass as regression tests
   (`services/agent/tests/test_case_binding_job0268.py`): narration + tool
   card stay in the owning Case across a mid-stream/mid-dispatch switch.
2. Cancel-and-redispatch race test: a new turn re-pinning to Case B does not
   steal the old turn's narration from Case A.
3. Auto-create hand-off unchanged (job-0267 probe D semantics): root prompt →
   user + tool + agent rows all in the auto-created Case.
4. Original job-0267 probes A+B now FAIL (they assert the bug) — expected;
   C/D/E still pass.
5. Full agent suite: no new failures beyond the 5 proven pre-existing
   (3x test_data_fetch docstring-tier, 2x test_model_flood_scenario live-GCS).
