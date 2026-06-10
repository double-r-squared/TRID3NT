# Root cause — solver-confirm Proceed dropped ("unknown/closed warning_id")

## Symptom (live, run-2)
- Gate emitted: `03:14:03,412 solver-confirm gate emitted ... warning_id=01KTRGCNWKG8BY7MZK34Q5S5QW`
- Web sent confirmation with the SAME warning_id (WS frame, verified):
  `{"type":"tool-payload-confirmation","payload":{"warning_id":"01KTRGCNWKG8BY7MZK34Q5S5QW","decision":"proceed"}}`
- Server rejected it: `03:14:05,260 WARNING tool-payload-confirmation for unknown/closed warning_id=01KTRGCNWKG8BY7MZK34Q5S5QW`
- The gate future was STILL PENDING at snapshot time (no timeout, no cancel) => the lookup `state.pending_payload_warnings.get(conf.warning_id)` returned None for a key that IS present in SOME state's dict.

## Root cause — per-connection SessionState split across multiple live WS connections
- `pending_payload_warnings` is a field on the per-connection `SessionState`, created in `handler()` at server.py:3008 (`state = SessionState(session_id=session_id)`), one per WS connection.
- The web client (`web/src/ws.ts`) opens MULTIPLE WebSocket connections for one browser session (React StrictMode double-mount + reconnect + Playwright tap). Agent log shows **4 `connection open` events** at 03:11:19-20 for session 01KTRG7P...DNCN (and 4 more at 02:59:47 for the prior session).
- The session_id guard (server.py:3009) only checks the *string* matches; each connection still has its OWN `SessionState` object with its OWN empty `pending_payload_warnings` dict.
- The gate future is registered in connection-A's dict (the one running the agent loop / inflight_task). The `tool-payload-confirmation` is sent by the web over `this.socket` — the most-recently-opened connection (B), whose handler has a different `SessionState` with an empty dict. `get()` => None => "unknown/closed".

## Why unit tests passed
- `test_solver_confirm_gate.py` drives the gate + confirmation through a SINGLE in-process state/seam. It cannot reproduce the multi-connection split — that only manifests with a real browser opening >1 WS connection.

## Scope / blast radius
- This severs the resume leg for EVERY future-based gate keyed on per-connection state: solver-confirm (Case 2 MODFLOW) AND the code-exec/sandbox gate (job-0233) share the identical `pending_payload_warnings` seam. Any Proceed/Cancel will drop whenever the confirmation lands on a different connection than the one holding the open gate.

## Suggested fix direction (for the agent/web specialist — NOT applied here)
Either (a) make `pending_payload_warnings` keyed at the per-session (not per-connection) level — a session-scoped registry the inbound handler resolves regardless of which connection delivered the confirmation; or (b) have the web client guarantee a single canonical WS connection per session (close stale sockets; suppress StrictMode double-open) so the gate + confirmation always share one SessionState. (a) is the robust fix; (b) is necessary regardless for correctness of inflight_task ownership.
