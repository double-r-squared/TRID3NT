# job-0268 report — turn-start Case binding (cross-Case persistence contamination)

**State:** DONE (orchestrator-direct, Fable xhigh, per the user's standing
authorization for critical fixes). **Source:** job-0267 adversarial verifier,
severity MAJOR.

## What changed (`services/agent/src/grace2_agent/server.py`)

1. **`SessionState.current_turn_case_id`** — the turn's Case binding, pinned
   by `_prepare_user_turn` AFTER the auto-create-from-root hand-off and before
   the first write.
2. **`_turn_case_id(state)`** — resolution helper: pin first, live
   `active_case_id` as fallback for un-prepared callers (tests/legacy). The
   fallback IS the pre-fix behavior, so nothing that worked stops working.
3. **Explicit `case_id=` threading** — `_persist_chat_turn`,
   `_persist_tool_card`, and `_persist_case_loaded_layers` accept an explicit
   target; `_dispatch_gemini_and_persist` / `_dispatch_tool_and_persist`
   capture the binding at task entry (immune to the cancel-and-redispatch
   re-pin race); `_invoke_tool_via_emitter` captures once up front and drives
   the per-Case `.qgs` routing (`ensure_case_qgs` + `publish_layer` `case_id`
   param), the tool-card persist, and layer attribution from that capture.
4. **Chart persistence** keys `doc_id` by the turn's Case, not the visible one.
5. **D.6 heartbeat** registers the turn's Case in `project_ids`.

**Rejected alternative** (verifier's option 2): cancelling `inflight_task` on
`case-command(select|create)`. Product-hostile under per-Case streams
(job-0266) — a rail click would kill a minutes-long SFINCS solve. The web
client already routes envelopes by owning stream; persistence now matches.

## Evidence

- `services/agent/tests/test_case_binding_job0268.py` — 4/4 pass:
  - inverted probe A (narration stays in owning Case across mid-stream switch)
  - inverted probe B (tool card stays in owning Case across mid-dispatch switch)
  - cancel-and-redispatch race (new turn re-pins to B; old narration still → A)
  - auto-create hand-off (probe D semantics preserved; pin follows the hand-off)
- Original job-0267 expected-bug probes: **A+B now FAIL** (they assert the
  contamination; it no longer reproduces), C/D/E still pass — exactly the
  inversion the probe file's own docstring calls for.
- Persistence-adjacent files (`test_full_stream_persistence_job0267`,
  `test_case_lifecycle*`, `test_persistence*`): 52 passed, 1 skipped.
- Full agent suite: 4292 passed / 5 failed / 72 skipped — the exact 5
  proven-pre-existing failures (3x test_data_fetch docstring-tier from
  uncommitted Wave 4.10 working-tree edits, 2x test_model_flood_scenario
  live-GCS guardrail). Zero new failures. Log: /tmp/job0268_full_suite.log.

## Companion one-liner (separate commit)

`web/src/LayerPanel.tsx` opacity-slider thumb clipped top+bottom
(user-reported): the `<input type="range">` had `height: 4` inside an
`overflow: hidden` row, so the ~14-16px native thumb clipped. Box raised to
16px (track still renders thin). LayerPanel vitest 29/29.
