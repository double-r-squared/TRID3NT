# Audit: Web stub — React+MapLibre CONUS map + chat round-trip

**Job ID:** job-0016-web-20260605
**Sprint:** sprint-03
**Auditor:** Development Orchestrator
**Status:** assigned

## Task Assignment

**Specialist:** web
**Prerequisites:** job-0013 (message shapes) and job-0015 (the running local agent). Read both reports first.
**SRS references:** FR-WC-1/2/3/7 (subset), FR-DT-1/3/4, NFR-PO-1, M1 ("basic WebSocket protocol with a web stub" — NOT the full M3 client).

### Scope

1. **React + Vite app** in `web/`: MapLibre GL JS map, OSM Tier A basemap with attribution (FR-DT-1), initial CONUS view (lat 24–50, lon -125–-66), camera locked 2D (`maxPitch: 0`, rotation disabled — Decision I), pan/zoom. No layer panel, no scrubber, no pick-modes — those are M3/M9.
2. **Minimal chat box**: text input (Cmd/Ctrl+Enter), message stream rendering streamed `agent-message-chunk` deltas (append by `message_id`, finalize on `done`), connection status indicator, reconnect with `session-resume` on drop (NFR-R-2 basic).
3. Frames validated against the job-0013 JSON Schemas (plain TS types generated or hand-mirrored — surface your approach; gaps → consumer-pushback motion, never client-side workarounds).
4. `make run-web` serves it locally.

### File ownership (exclusive)
`web/**`, Makefile `run-web` target. NOT `packages/contracts/`, `services/`.

### Cross-cutting principles in force
*Live E2E validation required*, *surface uncertainty*, *no legacy support pre-MVP* (no Qt anything; no 3D scaffolding per Decision I).

### Acceptance criteria (reviewer re-runs)
- `make run-agent` + `make run-web`: browser shows CONUS OSM map; typing a message streams a real Gemini reply token-by-token — **screenshot + WS transcript** in report
- Kill the agent: status flips to disconnected, no crash; restart: reconnects and a fresh message works — transcript
- No `gs://` fetch, no computed numbers anywhere in client code (reviewer inspects)
- Browser check on at least Chrome + Safari (NFR-PO-1 spot check; full matrix is later)

## Assessment

## Invariant Check

## Dependency Check

## Decisions Validated

## Open Questions Resolved

## Follow-up Actions

## Sign-off
