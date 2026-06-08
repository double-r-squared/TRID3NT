# Report: Schema D.2 amendment bundle — LayerURI / ProjectLayerSummary alignment + ws.ts map-command routing

**Job ID:** job-0072-schema-20260607
**Sprint:** sprint-10 Stage 1
**Specialist:** schema
**Task:** Add 4 optional fields to ProjectLayerSummary (Python + JSON Schema + TypeScript), rename source_url → uri in client contracts, and wire ws.ts map-command dispatch to LayerPanelBus. Closes OQ-62-LAYERURI-URI-FIELD, OQ-W-65-STYLE-PRESET, OQ-0068-URI, OQ-0068-MAPCMD-WS, OQ-0068-ZIDX.
**Status:** ready-for-audit

## Summary

Added 3 new optional fields (`wms_url`, `opacity`, `z_index`) to `ProjectLayerSummary` in Python and regenerated 4 JSON Schemas; `style_preset` was already required on the Python model. Renamed `source_url` → `uri` in `web/src/contracts.ts` (closing OQ-0068-URI). Added `onMapCommand?:` handler and a `"map-command"` dispatch case to `web/src/ws.ts` (closing OQ-0068-MAPCMD-WS). Additive Python and Vitest tests pass; all three suites pass at or above baseline.

## Changes Made

- **`packages/contracts/src/grace2_contracts/collections.py`**
  - Added docstring to `ProjectLayerSummary` explaining the new fields.
  - Added 3 new optional fields: `wms_url: str | None = None`, `opacity: float | None = None`, `z_index: int | None = None`.
  - `style_preset` was already required (`str`) — no change to the Python model for that field.

- **`packages/contracts/schemas/project_document.json`** — regenerated; now includes `wms_url`, `opacity`, `z_index` in the `ProjectLayerSummary` `$defs`.
- **`packages/contracts/schemas/session_document.json`** — regenerated; same `ProjectLayerSummary` update propagated.
- **`packages/contracts/schemas/layer_uri.json`** — regenerated (bbox field from job-0068 was missing from the export; now captured).
- **`packages/contracts/schemas/ws_session_state.json`** — regenerated; references updated `$defs`.

- **`packages/contracts/tests/test_collections.py`** — 3 additive tests:
  1. `test_project_layer_summary_new_optional_fields_default_to_none` — all 3 new fields default to None.
  2. `test_project_layer_summary_new_optional_fields_roundtrip_non_default` — non-None values round-trip through JSON.
  3. `test_project_layer_summary_backward_compat_missing_new_fields` — documents missing new fields still parse.

- **`web/src/contracts.ts`**
  - `ProjectLayerSummary`: renamed `source_url` → `uri` (required `string`); added `wms_url?: string | null`; formalized `style_preset?: string | null` by removing the OQ annotation comment.
  - `opacity: number` and `z_index: number` remain required — see Decisions.

- **`web/src/ws.ts`**
  - Added `MapCommandPayload` to imports.
  - Added `onMapCommand?: (p: MapCommandPayload) => void` to `WsHandlers` (optional so App.tsx and Chat.tsx need no changes).
  - Added `"map-command"` case in `handleMessage` switch.

- **`web/src/LayerPanel.test.tsx`** — added `uri` field to `makeLayer` helper (tsc clean after rename).
- **`web/src/LayerLegend.test.tsx`** — added `uri` field to `makeLayer` helper (same reason).
- **`web/src/ws.test.tsx`** — new file; 3 Vitest tests for map-command dispatch.

## Decisions Made

- **Keep `opacity`/`z_index` required in TypeScript interface.**
  - Rationale: `LayerPanel.tsx` (FROZEN) does arithmetic on these fields. Making them optional would produce `tsc` errors. Python optional default handles storage evolution server-side; the agent always populates these fields when emitting to the client.

- **`onMapCommand` optional in `WsHandlers`, not mandatory.**
  - Rationale: App.tsx is FROZEN. Optional handler allows the interface to be extended without modifying App.tsx. Production routing is now possible; App.tsx wiring is a follow-up.

- **`source_url` fully replaced by `uri` (no deprecated alias).**
  - Per AGENTS.md "Remove don't shim" — pre-MVP, no legacy users. Map.tsx already used a local `WireLayerSummary` with `uri`.

## Invariants Touched

- **Metadata-payload pattern (invariant 6):** `wms_url` (client-facing endpoint) and `uri` (GCS pointer) are now distinct fields. Correctly preserves the metadata/payload split.
- **Rendering through QGIS Server (invariant 4):** `wms_url` is the WMS endpoint for MapLibre tile registration; the client never reads COGs. No violation.
- **Tier separation (invariant 5):** `uri` (gs://) not exposed to browser; `wms_url` (public QGIS Server) is. No violation.

## Open Questions

- **OQ-72-APP-MAPCMD-WIRING (new, non-blocking):** App.tsx is FROZEN so `onMapCommand` handler is wired in ws.ts but App.tsx does not pass `(p) => bus.pushMapCommand(p)`. Production map-command routing needs a one-line change in App.tsx in the next web job. TENTATIVE: `onMapCommand: (p) => bus.pushMapCommand(p as MapCommandPayload)`.

- **OQ-72-LAYERURI-WMS-FIELD (carry-forward, non-blocking):** `LayerURI` does not have a `wms_url` field. The pipeline emitter translates externally. For full field-for-field alignment, `LayerURI` should carry `wms_url: str | None = None`. Proposed for the next schema job.

## Dependencies and Impacts

- Depends on: job-0068 (bbox on LayerURI, pipeline_emitter writes ProjectLayerSummary), job-0065 (style_preset stopgap that this job formalizes)
- Affects:
  - `web` agent: App.tsx should add `onMapCommand` handler in next web job (OQ-72-APP-MAPCMD-WIRING).
  - `agent` specialist: `pipeline_emitter.add_loaded_layer` should populate `wms_url` when available.

## Verification

### Python contracts
```
PYTHONPATH=packages/contracts/src .venv-agent/bin/python -m pytest packages/contracts/tests/ -q
145 passed in 0.41s  (baseline 142; +3 new tests)
```

### Agent service
```
PYTHONPATH=services/agent/src:packages/contracts/src .venv-agent/bin/python -m pytest services/agent/tests/ -q
180 passed, 1 skipped  (same as baseline)
```
Note: 3 apparent failures in the working tree are job-0071 in-progress dirt (test files modified by job-0071 against service code that is not yet committed). Verified by stashing services/agent/ changes and re-running: 180 passed, 1 skipped.

### Web
```
cd web && npm run test
63 passed  (baseline 60; +3 new ws.test.tsx tests)
```

### TypeScript check
```
cd web && npx tsc --noEmit
clean (exit code 0)
```

### JSON Schema idempotency
```
PYTHONPATH=packages/contracts/src python -m grace2_contracts.export_schemas
43 files written; second run produces no git diff changes (idempotent).
```

### Live E2E evidence

**Pydantic round-trip (schema agent DoD):** `test_project_layer_summary_new_optional_fields_roundtrip_non_default` constructs a `ProjectLayerSummary` with all new fields, calls `model_dump(mode="json")`, serializes via `json.dumps`, calls `model_validate(json.loads(...))`, and asserts the second dump is byte-identical to the first. Passes in 145/145 run.

**WebSocket frame injection:** The ws.test.tsx test `dispatches map-command envelope to onMapCommand handler` creates a `GraceWs` with an `onMapCommand` spy, calls `ws.connect()`, injects a raw JSON envelope string via `ws.dispatchEvent(new MessageEvent("message", { data: raw }))` through happy-dom's built-in WebSocket implementation, and asserts `onMapCommand` was called once with `command === "zoom-to"`. Confirmed passing in verbose mode via `npx vitest run --reporter=verbose`.

- Tests run: contracts (145/145), agent (180/180 excluding job-0071 dirt), web (63/63)
- Live E2E evidence: pydantic round-trip + ws.ts MessageEvent injection — see above
- Results: pass
