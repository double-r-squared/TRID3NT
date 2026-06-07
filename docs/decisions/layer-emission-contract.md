# Decision: Layer surfacing contract — `session-state.loaded_layers` is canonical; `map-command` is for verbs

**Status:** ADOPTED (2026-06-07)
**Scope:** how the agent surfaces produced layers (flood depth COG, building footprints, hazard polygons, etc.) to the React/MapLibre web client.
**Supersedes:** the implicit "emit `map-command(load-layer)` after each tool produces a layer" pattern hinted in the M3 contract.
**Owner:** orchestrator (architectural seam); web + agent specialists implement.

## Context

After the M5 pipeline produces a flood-depth COG at `gs://grace-2-hazard-prod-runs/<run_id>/flood_depth_peak.tif`, two contract shapes in `packages/contracts/src/grace2_contracts/ws.py` could carry the result to the client:

- `MapCommandPayload(command="load-layer", args=LoadLayerArgs(...))` at `ws.py:420-433` — imperative; each envelope tells the client to perform one action (add a layer, set opacity, zoom). One of 10 verbs in the `MapCommand` Literal (`ws.py:406-417`).
- `SessionStatePayload.loaded_layers: list[ProjectLayerSummary]` at `ws.py:463-484` — declarative snapshot of currently-loaded layers; client reconciles UI to match. Follows the replace-not-reconcile semantic established for `pipeline-state` in Appendix A.7.

## Decision

**`session-state.loaded_layers` is the canonical source of truth for "what layers are currently loaded".** It drives the LayerPanel list and the MapLibre source/layer registrations. Replace-not-reconcile semantics apply: the agent always emits the full current list, not deltas.

**`map-command` is reserved for transient verbs that aren't pure state.** Specifically the camera and animation verbs: `zoom-to`, `set-temporal-config`, `start-animation`, `stop-animation`, `invalidate-tiles`. The layer-CRUD verbs in `MapCommand` (`load-layer`, `remove-layer`, `set-layer-visibility`, `set-layer-opacity`, `set-layer-order`) become DEFERRED — the same state changes flow through `session-state.loaded_layers` instead. Client-local UI toggles (collapse a row, hover-highlight, etc.) stay local; they don't need a contract envelope.

## Rationale

Five reasons, in order of weight:

1. **Reconnect/resume is trivially correct.** A reconnecting client receives the next `session-state` and is instantly caught up. No history replay; no rehydrate command; no race window where a dropped `load-layer` leaves the UI permanently divergent.
2. **The replace-not-reconcile semantic already lives here.** `pipeline-state` enforces it (Appendix A.7); `PipelineEmitter` in `services/agent/src/grace2_agent/pipeline_emitter.py` is structured around it. Extending the same semantic to layer surfacing means one mental model, one emit pattern, one set of tests.
3. **The minimal agent-side fix is one return-type change.** `pipeline_emitter.py:517` already auto-emits a fresh `session-state` when a tool returns a `LayerURI` via `isinstance(result, LayerURI)`. The M5 wrapper `run_model_flood_scenario` currently returns the envelope as a JSON dict, so the branch is missed. Returning `LayerURI` (or `list[LayerURI]`) makes the entire surfacing flow work with no new envelope shape, no new emitter method, no new client handler.
4. **The LayerPanel UX is list-shaped, not action-shaped.** Users browse loaded layers, toggle visibility, reorder. That naturally maps to a list state, not a sequence of verbs.
5. **The verbs that remain in `map-command` are GENUINELY transient.** A `zoom-to` event happens once; a `start-animation` triggers a temporal animator; an `invalidate-tiles` is a one-shot cache bust. These don't have a stable "current state" representation, so the declarative shape doesn't fit them.

## What this binds

- **Agent (sprint-09 work):** `run_model_flood_scenario` and all future layer-producing atomic tools return `LayerURI` (or `list[LayerURI]`) as the typed result, NOT a JSON dict containing the URI inside an envelope. The atomic-tool wrapper may still construct the `AssessmentEnvelope` for the chat-message return / Mongo persistence path, but the tool-call return is the `LayerURI`.
- **Web client (existing — already conformant):** `LayerPanel.tsx` continues to consume `session-state` and reconcile its rendered list. The `case "load-layer"` / `case "remove-layer"` / etc. handlers in `LayerPanel.tsx` are not removed yet but become DEFERRED-DEAD — kept until sprint-10 cleanup so a planned migration path is preserved. New web work does NOT add new `map-command` cases for layer CRUD.
- **Contract (`packages/contracts/src/grace2_contracts/ws.py`):** no schema change required. The `MapCommand` Literal stays at 10 verbs; the layer-CRUD verbs are marked DEFERRED in a docstring update (small additive amendment in sprint-09 if scope permits).
- **PyQGIS worker (sprint-09 new tool):** when the agent invokes the worker to publish a layer, the worker mutates the `.qgs` at `gs://grace-2-hazard-prod-qgs/<project>.qgs`, writes back, and the WMS URL the agent uses to populate `ProjectLayerSummary.uri` is the QGIS Server `MAP=...&LAYERS=<layer-id>` URL.
- **IAM (sprint-09 infra job):** the QGIS Server runtime SA gets `roles/storage.objectViewer` on `grace-2-hazard-prod-runs` (so the .qgs's `/vsigs/<run-bucket>/...` layer reference resolves at WMS render time).

## What this does NOT bind

- The shape of `ProjectLayerSummary` (lives in D.6; if amendments are needed they go through the schema specialist).
- The PyQGIS worker's project-mutation protocol (separate decision; sprint-09 kickoff scope).
- The `MapCommand` verbs marked transient (`zoom-to`, etc.) — those remain in scope as imperative envelopes when needed.

## Cited code

- `packages/contracts/src/grace2_contracts/ws.py:420-433` — `MapCommandPayload`
- `packages/contracts/src/grace2_contracts/ws.py:406-417` — `MapCommand` Literal
- `packages/contracts/src/grace2_contracts/ws.py:463-484` — `SessionStatePayload`
- `services/agent/src/grace2_agent/pipeline_emitter.py:413-440` — `add_loaded_layer`
- `services/agent/src/grace2_agent/pipeline_emitter.py:517` — `isinstance(result, LayerURI)` auto-emit gate
- `services/agent/src/grace2_agent/workflows/model_flood_scenario.py:647` — current dict-return that misses the gate
- `web/src/LayerPanel.tsx:80-130` — client `case "map-command"` handlers (load-layer subset becomes DEFERRED-DEAD)
- `web/src/App.tsx:76` — LayerPanelBus that subscribes to both shapes; no client change required
