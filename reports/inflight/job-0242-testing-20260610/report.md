# job-0242 report — Stage 3 re-verify bundle (testing specialist)

**Executed:** 2026-06-10, live one-session re-verify of the job-0241 fix wave (commit `e712ca6`).
**Agent under test:** PID 3005727 on :8765, 89 tools, gemini-2.5-pro — NOT restarted (as instructed).
**Overall verdict: PARTIAL.** `case2_gate` PASS (the fix EMITS the gate correctly), but `case2_plume` FAIL — the **Proceed click is dropped server-side** by a pre-existing multi-WS-connection state-split bug, so MODFLOW never runs. Because PASS requires BOTH gate AND plume, overall is PARTIAL. Scenarios B (analysis+P5) and the live leg of C (sandbox gate) were BLOCKED by the same class of issue plus a harness-navigation crash; the C egress leg was proven directly (Gemini-free) and PASSES.

Gemini turns used: ~2 (one cold-cache first turn that reached the gate; the abandoned run-1 burned the cache-build). No 429s.

---

## Per-scenario verdicts

| sub-scenario | verdict | evidence |
|---|---|---|
| **case2_gate** | **PASS** | Gate emitted at +165.7s, `gate_before_dispatch=true`, tool `run_modflow_job`, full content (contaminant=trichloroethylene, Twin Falls Idaho, demo-aquifer caveat K=0.0001/phi=0.3). `evidence/A03_confirmation_gate.png`, `findings.json` scenarios.A.ordering + gate_info, agent log `solver-confirm gate emitted`. |
| **case2_plume** | **FAIL** | Proceed sent the matching `warning_id` but server logged `tool-payload-confirmation for unknown/closed warning_id=...`; solve never ran; `plume.materialized=false`, map stayed over the upper-midwest, not Idaho. Root cause: per-connection `SessionState` split — see below. `evidence/ROOT_CAUSE_warning_id_dropped.md`, `agent_log_run2.log`. |
| **p5_impact** | **BLOCKED** | Never reached — harness crashed at Scenario B navigation (the unresolved stuck gate left the UI without a reachable `grace2-cases-new`). No P5 evidence this run. |
| **analysis_count** | **BLOCKED** | Never reached (depends on B1 Pelicun panel). |
| **chart_emission** | **BLOCKED** | Never reached. |
| **chart_replay** | **BLOCKED** | Never reached. |
| **sandbox_gate** | **PARTIAL** | Egress leg PASS (proven directly, Gemini-free): `run_sandbox_local` on a urllib script returned `status="blocked"` with the allowlist guard message — `evidence/sandbox_egress_blocked.json`. The interactive code_exec gate was NOT exercised live (B crashed first); and it shares the SAME `pending_payload_warnings` seam, so its Proceed would drop identically until the fix lands. |

---

## case2_gate — the fix proof (PASS)

The job-0241 confirmation gate is REAL and correct. In the prior failed run (job-0235) `gate_before_dispatch` was `false` and MODFLOW dispatched at +170s with no gate. This run:

- `tool-payload-warning` WS frame at t=+164.6s carrying `warning_id`, `tool_name=run_modflow_job`, derived `tool_args` (release_rate_kg_s~3.07, total_mass_kg~66320, aquifer_k_ms=0.0001, porosity=0.3, geocoded spill latlon 42.556,-114.470), and the recommendation string with the demo-aquifer caveat.
- NO `run_modflow`/`run_model_groundwater` dispatch frame precedes it (`modflow_dispatch_rel_ms=null` — the solve never started because Proceed was dropped). Ordering assert: gate strictly before any dispatch = **true**.
- Card renders inline in chat with Proceed anyway / Cancel buttons (`A03_confirmation_gate.png`).

## case2_plume — the new blocker (FAIL): multi-WS-connection state-split

The gate emits but the resume leg is severed. Full trace in `evidence/ROOT_CAUSE_warning_id_dropped.md`. Summary:

- Gate registered its asyncio.Future in the per-connection `SessionState.pending_payload_warnings` (server.py:2017), keyed by `warning_id`.
- The web client (`web/src/ws.ts`) opens **multiple** WebSocket connections per browser session (StrictMode double-mount + reconnect/backoff). Agent log shows **4 `connection open`** events for this session at 03:11:19-20.
- Each connection's `handler()` builds its OWN `SessionState` (server.py:3008); the session_id guard (3009) only compares the *string*, so the dicts are independent objects.
- `sendPayloadConfirmation` rides `this.socket` (ws.ts:855 — the latest connection). The agent loop / gate runs on an *earlier* connection. So the confirmation lands on a `SessionState` whose `pending_payload_warnings` is empty -> `get()` returns None -> `tool-payload-confirmation for unknown/closed warning_id` (server.py:3177).
- The gate future stayed pending (no timeout/cancel in the log window) — confirming it was alive in connection-A's dict while the lookup happened in connection-B's dict.

**Blast radius:** every future-based gate keyed on per-connection state — solver-confirm (Case 2) AND the code_exec/sandbox gate (job-0233) — drops Proceed/Cancel whenever the reply arrives on a different connection. This is why I did NOT spend further Gemini turns re-running B/C live: the outcome is deterministic until the seam is fixed.

**Why unit tests passed:** `test_solver_confirm_gate.py` drives gate+confirmation through ONE in-process state. The split only manifests with a real browser opening >1 WS connection — exactly the "inject seams hid agent->web bugs" lesson.

**Suggested fix direction (for agent/web specialist, NOT applied here):** make `pending_payload_warnings` a per-*session* registry the inbound handler resolves regardless of delivering connection; and/or have the web client guarantee a single canonical WS per session (close stale sockets, suppress StrictMode double-open). The per-session registry is the robust fix.

## sandbox_gate egress leg (PASS, Gemini-free)

`GRACE2_SANDBOX_LOCAL=1 run_sandbox_local("urllib.urlopen('https://example.com')")` ->
`{"status":"blocked","error":"network egress to 'example.com' blocked by sandbox guard (allowlist: googleapis.com, google.internal, mongodb.net, localhost, 127.0.0.1, ::1)"}`. Evidence `sandbox_egress_blocked.json`.

---

## Harness notes

- Driver: `web/tools/stage3_reverify_job0242.mjs` (live, no `__grace2Inject*`; read-only `__grace2GetMap`).
- Fixed the two known prior-harness bugs: (1) narration/terminal-settle now waits on sustained WS-frame quiescence (`QUIESCE_MS=20s`) instead of a momentary input-enabled window — the prior `+166s` false-idle is closed; (2) the cold-cache patient-guard retained.
- Run-1 was abandoned (preserved under `evidence/_abandoned_run1/`): its idle heuristic still tripped between sequential discovery tool calls before the quiescence fix; no app conclusion drawn from it.
- Run-2 crashed at Scenario B navigation AFTER recording the decisive Scenario A evidence — the stuck open gate (unresolvable due to the bug) left the case view without a reachable new-case control. This is downstream of the case2_plume bug, not an independent harness defect.

## Carry-overs / open questions

- **OQ-0242-WS-STATE-SPLIT (blocker):** per-connection `pending_payload_warnings` cannot be resolved across the web client's multiple WS connections — fix before any gated solver/sandbox flow can complete E2E. Owner: agent + web.
- B (P5 ImpactPanel, analytical count, chart emission, chart replay) and the live sandbox gate remain UNVERIFIED — re-run after OQ-0242-WS-STATE-SPLIT lands (one bundled session).
- 0236 MRMS/SFINCS legs remain PARTIAL pending a live CONUS flood warning (unchanged).
