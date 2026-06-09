# Diagnosis: layer-emission chain breaks at the dual-WebSocket boundary

**Job:** job-0159-engine-20260608
**Date:** 2026-06-08

## TL;DR

The web client mounts **two parallel `GraceWs` instances per tab** —
`Chat.tsx:223` (chat panel) and `App.tsx:413` (map+layer panel). The agent
server creates a **per-connection `SessionState` + `PipelineEmitter`** keyed
to the `ServerConnection`, not to the `session_id`
(`services/agent/src/grace2_agent/server.py:1647`,
`services/agent/src/grace2_agent/server.py:1184`). When the user types
"Model flood in Fort Myers" the workflow runs on **Chat's** connection;
`pipeline_emitter.emit_tool_call` correctly hits the `isinstance(result,
LayerURI)` gate, `add_loaded_layer` fires, and a `session-state` envelope
carrying the new layer is sent on **Chat's** wire. App.tsx's `GraceWs`
never receives that envelope because the server-side emitter only writes to
its own bound socket — and Chat.tsx's `onSessionState` handler ignores
`loaded_layers` (it only narrows `current_pipeline` for the cancel button —
`web/src/Chat.tsx:148`). Result: the LayerPanel + Map.tsx subscribers (wired
through App.tsx's bus) never see the layer.

## Walking the chain, hypothesis by hypothesis

### A. Workflow returns dict, not LayerURI — REJECTED

`services/agent/src/grace2_agent/workflows/model_flood_scenario.py:768-779`
returns `LayerURI(...)` when `envelope.layers` is non-empty; the failure
branch (empty layers) returns the envelope as a dict. The 2026-06-08 log shows:

    grace2_agent.workflows.model_flood_scenario model_flood_scenario complete
      envelope_id=01KTN22SPPHGP2MMEGBWBXYVWD run_ids=['01KTN1PFQ3T83ZC1G7ZK8KXW6Y']
      layers=1

`layers=1` → `envelope.layers` truthy → wrapper returns `LayerURI`. The
wrapper is `async def run_model_flood_scenario(...)`; the emitter awaits
the coroutine (`pipeline_emitter.py:524-525`) before the isinstance check.
The dict fallback path was NOT taken.

### B. `isinstance(result, LayerURI)` gate misfires — REJECTED

Both `pipeline_emitter.py:69` and `workflows/model_flood_scenario.py:79`
import `LayerURI` from `grace2_contracts.execution` — same class identity.
Existing unit tests
(`services/agent/tests/test_pipeline_emitter.py:245-280`) prove the gate
fires `add_loaded_layer` end-to-end when a registered tool returns a
`LayerURI` instance. No path between the workflow's
`return LayerURI(...)` and the emitter's
`if isinstance(result, LayerURI): await self.add_loaded_layer(result)`
mutates `result`. (Note: `_invoke_tool_via_emitter` does substitute
`params["project_qgs_uri"]` for case-scoped publishing — only for
`publish_layer`, not the workflow wrapper. The return value is untouched.)

### C. session-state envelope not emitted — REJECTED

`add_loaded_layer` (`pipeline_emitter.py:414-447`) appends a
`ProjectLayerSummary` to `self._loaded_layers` and then awaits
`emit_session_state()` (`pipeline_emitter.py:449-464`) which sends a full
`session-state` envelope with `loaded_layers` populated. The emitter's
debug log line (`pipeline_emitter.py:637-644`) does not show in the agent
log because the agent runs at INFO level, but the contract test transcript
(`test_pipeline_emitter.py:245-280`) demonstrates that the
`session-state` frame is written between `pipeline-state(running)` and
`pipeline-state(complete)`. There is no skip path.

### D. ws layer envelope not reaching App.tsx subscribers — **CONFIRMED**

This is the root cause. The web client has TWO `GraceWs` instances per
tab; the agent server has ONE `SessionState` + emitter per
**ServerConnection**, not per `session_id`. Therefore the session-state
envelope emitted after `add_loaded_layer` only goes out on the wire the
tool ran on — Chat's connection — while App's connection (whose
`onSessionState` handler is the only path that drives
`bus.pushSessionState` → `setLayers` and Map.tsx's WMS source
registration) never receives it.

Specific code citations for the bug:

- `web/src/Chat.tsx:223` creates Chat's `GraceWs`.
- `web/src/App.tsx:413` creates App's `GraceWs`. Both share the same
  `session_id` via localStorage (`web/src/ws.ts:140-156`) but each has
  its own `WebSocket`.
- `services/agent/src/grace2_agent/server.py:1615-1660` — the connection
  handler creates `state = SessionState(session_id=...)` per
  connection; `_ensure_emitter` binds a `PipelineEmitter` whose `_sink`
  closes over the **per-connection** `websocket.send`
  (`server.py:1180-1188`). No connection-broker / fan-out by
  `session_id`.
- The user-message from the Chat panel runs on Chat's connection;
  `_invoke_tool_via_emitter` → `state.emitter.emit_tool_call(...)` →
  `add_loaded_layer(result)` → `emit_session_state()` → `_sink(...)` →
  `websocket.send(...)` → **only Chat's wire**.
- On Chat's wire, `Chat.tsx:231` routes the envelope into
  `dispatchPipeline({ type: "session-state", payload: p })`. The
  reducer at `Chat.tsx:148-151` reads only `current_pipeline` for the
  cancel-button predicate — `loaded_layers` is dropped on the floor.

### E. Map.tsx doesn't render raster from new loaded_layers entry — REJECTED conditionally

`web/src/Map.tsx:608-748` correctly subscribes to bus session-state and
calls `addSource`/`addLayer` for new raster layers (verified by
job-0076's race-condition fix + screenshots). The subscriber path is
sound — it just never receives the envelope under D.

## Fix shape

Surgical web-client fix in `web/src/ws.ts`: introduce a module-level
session-state fan-out hub keyed by `session_id`. When ANY `GraceWs`
instance receives a `session-state` envelope on its wire, it pushes into
the hub; every `GraceWs` instance bound to the same `session_id` invokes
its own `onSessionState` handler. Result: App.tsx's `onSessionState`
handler (which drives the bus → Map + LayerPanel) sees the envelope even
when it was emitted on Chat's connection.

This is the smallest change that lands the contract.
`docs/decisions/layer-emission-contract.md` (ADOPTED 2026-06-07) defines
the agent-side seam (`run_model_flood_scenario` returns `LayerURI`, the
emitter auto-fires session-state) — both already conform. The missing
piece is that the contract assumed ONE WebSocket per session_id; the
sprint-09 split into App+Chat dual-`GraceWs` quietly broke that
assumption. The fan-out hub restores it client-side without changing the
server contract.

## Why this never broke earlier

Sprint-09/sprint-10's end-to-end screenshots (job-0069, job-0070,
job-0074, job-0075, job-0076) were captured by **Playwright scripts that
injected session-state via the dev seam** (`__grace2InjectSessionState`,
`web/src/App.tsx:473`), bypassing the WebSocket entirely. So the
dual-`GraceWs` rendering path was never exercised end-to-end against a
real backend until the user's 2026-06-08 18:58 run.

## Acceptance evidence we will produce

- Unit test in `web/src/ws.test.ts` covering the fan-out hub: spinning up
  two `GraceWs` instances with the same `session_id`, sending a
  `session-state` envelope on one connection, asserting both
  `onSessionState` handlers fire.
- Live end-to-end: orchestrator restarts the agent and reruns "Model
  flood in Fort Myers"; Playwright screenshot shows the flood-depth
  raster on the map and the LayerPanel populated.
