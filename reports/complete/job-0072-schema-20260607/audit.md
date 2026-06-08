# Audit: Schema D.2 amendment bundle — LayerURI / ProjectLayerSummary alignment + ws.ts map-command routing

**Job ID:** job-0072-schema-20260607, **Sprint:** sprint-10 Stage 1, **Auditor:** Development Orchestrator, **Status:** assigned

## Task Assignment

**Specialist:** schema

**Prerequisites:**
- jobs 0060/0062/0065/0068 — surfaced 5 carry-forward schema OQs that need to close in one bundled amendment:
  - **OQ-62-LAYERURI-URI-FIELD** — `LayerURI.uri` carries WMS URL not gs://; need formal `wms_url` field
  - **OQ-0068-URI** — `web/src/contracts.ts` uses `source_url`; Python emits `uri`; bridge via local cast today
  - **OQ-0068-MAPCMD-WS** — `web/src/ws.ts` doesn't route `map-command` envelopes to the handler in production (dev-injection seam works; live agent zoom-to wouldn't reach client)
  - **OQ-0068-ZIDX** — `opacity` / `z_index` not in `ProjectLayerSummary`; client uses `?? 1` fallback
  - **OQ-W-65-STYLE-PRESET** — `style_preset?: str | None` added to client `contracts.ts` as a stopgap by job-0065; needs formal schema addition

Plus `LayerURI.bbox` was added by job-0068 directly to execution.py; needs JSON Schema re-export + client alignment.

**SRS references** (narrow file loading only):
- `docs/srs/D-mongodb-collection-schemas.md` (or wherever D.2 lives — find via grep)
- `docs/srs/A-websocket-protocol.md` for the MapCommand routing context
- DO NOT load `docs/SRS_v0.3.md` monolith.

### Why this job exists

Multiple sprints of incremental layer-emission work have left field drift across three contracts: Python pydantic (`packages/contracts/src/grace2_contracts/`), regenerated JSON Schema (`packages/contracts/schemas/`), and the TypeScript client (`web/src/contracts.ts`). The client also has a production routing gap (`web/src/ws.ts` ignores `map-command`). This job aligns all three at once.

### Scope

#### Part 1 — Python pydantic + JSON Schema additions

In `packages/contracts/src/grace2_contracts/`:

1. **`LayerURI`** (in `execution.py`): the `bbox` field added by job-0068 stays. Optionally add a brief docstring noting the bbox is used by `pipeline_emitter.emit_map_command("zoom-to")` (just documentation; no schema change).

2. **`ProjectLayerSummary`** (likely in `collections.py` per the OQ-0068-ZIDX trace): add the following optional fields:
   - `wms_url: str | None = None` — the QGIS Server WMS URL the client uses for MapLibre source registration; preserves the gs:// `uri` as the underlying file pointer. Closes OQ-62-LAYERURI-URI-FIELD.
   - `style_preset: str | None = None` — formal addition for the client-side legend matching. Closes OQ-W-65-STYLE-PRESET.
   - `opacity: float | None = None` — `0.0` to `1.0`; client falls back to `1.0` if missing. Closes OQ-0068-ZIDX (opacity half).
   - `z_index: int | None = None` — used for MapLibre layer-order arbitration. Closes OQ-0068-ZIDX (z_index half).

3. **Field-name reconciliation** for OQ-0068-URI: the Python `ProjectLayerSummary.uri` and client `ProjectLayerSummary.source_url` need to agree. Pick `uri` (Python is canonical; the client's `source_url` was a typo lineage from job-0025 that's been propagated). Update the client TypeScript contract in Part 3.

4. **JSON Schema re-export**: `make json-schemas` (or whatever the contracts repo's export target is); confirm `packages/contracts/schemas/*.json` updates idempotently. The generated `ws_session_state.json` and `project_layer_summary.json` (if separate) should now carry the new optional fields.

#### Part 2 — Tests

- Pydantic tests: assert all 4 new optional fields default to `None`; assert non-default values round-trip cleanly; assert backward compatibility (an old document missing these fields still parses)
- JSON Schema re-export idempotency test (the existing test setup should still work; just re-run)

#### Part 3 — Client TypeScript contract + ws.ts routing

In `web/src/contracts.ts`:

1. Rename `source_url` → `uri` on `ProjectLayerSummary` (or add `uri` as primary + keep `source_url` as deprecated alias for one sprint). Closes OQ-0068-URI.
2. Add the 4 new optional fields: `wms_url?: string | null`, `style_preset?: string | null` (already present from job-0065's stopgap; reaffirm), `opacity?: number | null`, `z_index?: number | null`. Remove job-0065's "OQ annotation" comment now that the schema is formal.

In `web/src/ws.ts`:

3. Find the WebSocket envelope-dispatch logic (likely a `dispatch(envelope)` or `onMessage` handler). Add a case for `"map-command"` that pushes the envelope to a subscriber list (mirror the existing `session-state` / `pipeline-state` dispatch). Wire it through to `App.tsx`'s LayerPanelBus so `pushMapCommand` flows for both dev-injection AND real production envelopes. Closes OQ-0068-MAPCMD-WS.

#### Part 4 — Tests for client side

- Vitest: a unit test that drives a synthetic `map-command(zoom-to)` envelope through the ws.ts dispatch and asserts the subscriber receives it
- Type-check: `tsc --noEmit` should be clean

### Live verification

Run the existing test suites:
- Contracts: `PYTHONPATH=packages/contracts/src .venv-agent/bin/python -m pytest packages/contracts/tests/ -q` — must be 142+/142+
- Agent: `PYTHONPATH=services/agent/src:packages/contracts/src .venv-agent/bin/python -m pytest services/agent/tests/ -q` — 180+/180+
- Web: `cd web && npm run test` — 60+/60+

### File ownership (exclusive)

- `packages/contracts/src/grace2_contracts/execution.py` (LayerURI docstring touch-up only)
- `packages/contracts/src/grace2_contracts/collections.py` (ProjectLayerSummary new fields)
- `packages/contracts/schemas/*.json` (regenerated)
- `packages/contracts/tests/` (additive tests)
- `web/src/contracts.ts` (field rename + new fields)
- `web/src/ws.ts` (map-command routing)
- `web/src/*.test.tsx` (additive Vitest)
- `reports/inflight/job-0072-schema-20260607/`

### FROZEN

- All services/, infra/, docs/srs/, styles/
- All web/src/*.tsx except *.test.tsx (no UI changes here — just routing + types)
- `reports/complete/**`

### Acceptance criteria

- [ ] 4 new optional fields on `ProjectLayerSummary` (Python + JSON Schema + client TypeScript)
- [ ] `source_url` → `uri` reconciled on the client
- [ ] `web/src/ws.ts` routes `map-command` envelopes to the subscriber list
- [ ] All test suites pass (contracts 142+, agent 180+, web 60+)
- [ ] Single commit; no FROZEN edits
- [ ] No SRS edits in this job — the D.2 prose alignment in `docs/srs/` is the orchestrator-direct housekeeping pass that lands separately
