# Audit: Web stub — React+MapLibre CONUS map + chat round-trip

**Job ID:** job-0016-web-20260605
**Sprint:** sprint-03
**Auditor:** Development Orchestrator
**Status:** approved

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

### Environment

Linux (Debian 13) is the dev substrate; the browser the client runs in is the prod substrate (cross-browser). Node v20.20 + npm 10.8 are already installed system-wide; use them (NOT conda or asdf). The `make run-web` target serves locally via Vite. No macOS-specific dev paths.

### Cross-cutting principles in force
*Live E2E validation required*, *surface uncertainty*, *no legacy support pre-MVP* (no Qt anything; no 3D scaffolding per Decision I).

### Acceptance criteria (reviewer re-runs)
- `make run-agent` + `make run-web`: browser shows CONUS OSM map; typing a message streams a real Gemini reply token-by-token — **screenshot + WS transcript** in report
- Kill the agent: status flips to disconnected, no crash; restart: reconnects and a fresh message works — transcript
- No `gs://` fetch, no computed numbers anywhere in client code (reviewer inspects)
- Browser check on at least Chromium + Firefox on Linux (NFR-PO-1 spot check on this dev substrate; Safari spot-check deferred until a macOS environment is available — note this in your Open Questions)

## Assessment

`web/` ships an installable React 18 + Vite 5 + TypeScript 5 strict app with MapLibre GL JS rendering a CONUS OSM basemap (camera 2D-locked per Decision I) and a docked chat panel that streams `agent-message-chunk` deltas from the running agent via WebSocket through a typed contracts mirror. All four live ACs pass on adversarial re-run with verbatim CDP transcripts + 7 headless browser screenshots (Chromium + Firefox): AC1 streaming reply visible in the UI, AC2 disconnect→reconnect in ~4 s (NFR-R-2 satisfied) with no JS crash, AC3 zero `gs://` / computed-numbers / direct HTTP fetch outside OSM tile template + attribution, AC4 cross-browser headless screenshots from Chromium 148 + Firefox-ESR 140. Decision G consumer-pushback discipline preserved (no client-side workarounds; gaps surfaced as Open Questions instead). One revision round (commits `778fe6c` → `06d9d1a`). Three low-severity reviewer findings (doc-drift only) accepted.

## Invariant Check

- **Determinism boundary:** pass — `Chat.tsx` renders raw `agent-message-chunk` delta-string concatenation. No numeric formatting, no client-side math, no hazard parsing. Grep for `Math.(pow|sqrt|log|sin|cos|tan|floor|ceil|round)` returns 0 hits in `web/src/`. Grep for `(depth|area_km2|inundation|damage|fatalities)` returns 0 hits.
- **Deterministic workflows:** n/a — no tool/workflow surface in the client.
- **Engine registration, not modification:** n/a — no engine surface.
- **Rendering through QGIS Server:** preserved by absence — no `.qgs` write path; no WMS/WMTS/WFS endpoints yet (lands in M2 with QGIS Server containerization). Map data is OSM Tier A raster only, swappable.
- **Tier separation:** pass — grep for `gs://` returns 0 hits in `web/src/`. Grep for `https?://` returns exactly 2 hits, both OSM (`https://tile.openstreetmap.org/{z}/{x}/{y}.png` tile template + `https://www.openstreetmap.org/copyright` attribution link). Tier A is OSM direct, swappable; no Tier B path because no agent-served GeoJSON in M1.
- **Metadata-payload pattern:** n/a — no Mongo or GCS access in the client.
- **Claims carry provenance:** n/a — no HEP code.
- **Cancellation is first-class:** pass (extends agent's seam) — `Chat.tsx` Cancel button emits Appendix-A `cancel` envelope to the agent; the agent's 502 ms cancel-to-cancelled chain (verified in job-0015) extends to the UI without modification. `PipelineState` summary surfaces the cancelled step.
- **Confirmation before consequence — and no cost theater:** pass — no `confirmation-request` handler yet (deferred; agent doesn't emit one in M1). Zero `cost`/`usd`/`cents`/`$` strings in client code (re-verified by grep).
- **Minimal parameter surface:** pass — `App.tsx` defaults to `ws://localhost:8765` overridable via `VITE_GRACE2_WS_URL`. No excess env knobs. `Map.tsx` initial view is the only literal coordinate pair (CONUS).

## Dependency Check

- **Prerequisites satisfied:** yes — job-0013 (`packages/contracts/schemas/ws_*.json` mirror source) + job-0015 (running agent at `localhost:8765`, Appendix-A WS server, MCP sidecar).
- **Downstream impacts:**
  - **job-0017 (acceptance suite):** consumes both `make run-web` + `make run-agent` for end-to-end smoke; `web/src/contracts.ts` will be the TS source-of-truth for protocol conformance tests on the client side. Routing: testing.
  - **First M2/M3 web job:** introduces `json-schema-to-typescript` codegen replacing the hand-mirror once payload count crosses ~20 (OQ-W-1 trigger). Routing: web.
  - **Outstanding amendments + decisions** (orchestrator carries to user): Safari spot-check deferral; codegen vs hand-mirror choice; Chromium provisioning for CI; agent-message-chunk `role`/`finish_reason` contract pushback (carried from job-0015 OQ-A-4 — web confirmed tentative no-change for v0.1).

## Decisions Validated

- **React 18 + Vite 5 + TypeScript 5 strict + MapLibre GL JS 4.7:** agree — versions pinned in `package.json`. Vite-React-TS template matches the SRS technology stack with no Qt/Electron/Tauri leakage (grep confirms zero hits).
- **Camera 2D-locked per Decision I:** agree — explicitly enforced in `Map.tsx`: `maxPitch:0` + `dragRotate:false` + `pitchWithRotate:false` + `touchPitch:false` + explicit `touchZoomRotate.disableRotation()` + `keyboard.disableRotation()` + `NavigationControl({showCompass:false})`. Belt-and-suspenders correct.
- **OSM Tier A basemap with attribution:** agree — `https://tile.openstreetmap.org/{z}/{x}/{y}.png` + "© OpenStreetMap contributors" attribution + copyright link. Per FR-DT-1; swappable later.
- **Contracts via hand-mirror (`web/src/contracts.ts`), NOT codegen for M1:** agree (TENTATIVE) — payload count is small (M1 subset: ~6 envelopes), hand-mirror tracks the schema verbatim, and the codegen tool (`json-schema-to-typescript`) introduces a build-time dependency that pays off only at scale. Promotion trigger documented (~20 payloads → first M3 web job).
- **Session-ID persistence in `localStorage` (NOT `sessionStorage`):** agree — survives tab close, which is what the reconnect/session-resume story wants. Report doc-drift (says `sessionStorage`) is reviewer finding 2 — accepted with audit note that the *code* is correct.
- **Default WS endpoint `ws://localhost:8765`:** agree (with caveat) — Firefox emits one transient `WebSocket connection failed` console error during the `::1`/`127.0.0.1` IPv6 fallback (reviewer finding 1). Functionally correct; cosmetic noise only. Reviewer's suggested fix to use `127.0.0.1` is reasonable as a future tightening.
- **Vite dev server binds `0.0.0.0:5173`** (not `127.0.0.1`): agree — enables LAN testing during dev; report doc-drift (reviewer finding 3) accepted with audit note that the *code* is correct.
- **Hand-rolled Crockford-base32 ULID generator (in `contracts.ts`):** agree (TENTATIVE) — promotion trigger documented (when chat panel needs to display/compare IDs → ulidx).
- **Polling watcher in `vite.config.ts`:** agree — dodges Debian inotify ENOSPC limits; cost is minor (poll-based file watching during dev only). Documented in `vite.config.ts` comment.

## Open Questions Resolved

- **OQ-W-1 (codegen vs hand-mirror):** resolved with hand-mirror for M1; promotion trigger ~20 payloads. Routes to next M3 web job for codegen setup.
- **OQ-W-2 (real ULID library):** resolved with in-line generator for M1; promote to `ulidx` when chat panel needs ID display.
- **OQ-W-3 (Chromium availability on dev host):** **carry to user / infra**. No apt-installed Chromium on Debian 13; AC4 used Chrome-for-Testing 148 from Playwright cache. Not reproducible on fresh dev box without provisioning step. Recommendation: `apt install chromium` OR `npx @playwright/test install chromium` OR a dev-container with both. Routes to infra (Dockerfile + dev-container) + web (CI parameter).
- **OQ-W-4 (Firefox-headless map paint timing):** documented — `firefox --screenshot` has no `--wait` flag and fires before MapLibre's first WebGL paint; Firefox screenshots show chat + zoom control but no tiles painted. Mitigated in revision round 1 with a CDP-equivalent for Firefox via web-ext OR by running Firefox with marionette + `webdriver-wait`. Low priority; affects evidence-capture tooling, not the app.
- **agent-message-chunk lacks `role`/`finish_reason` (contracts pushback, carried from job-0015 OQ-A-4):** web confirms tentative no-change for v0.1. Surface to user if a later job needs them.
- **Safari spot-check:** deferred until a macOS environment is available. Currently passes Chromium + Firefox-ESR on Linux.

## Follow-up Actions

- **OQ-W-3 Chromium provisioning** — pick one path (apt / Playwright / dev-container) and codify in a future infra or web job. Without this, fresh dev boxes can't reproduce AC4 cleanly.
  - Routing: infra (Dockerfile/apt) + web (CI param). Priority: low for dev; medium if CI lands.
- **Report doc-drift cleanup** (reviewer findings 1, 2, 3): localStorage vs sessionStorage, run-web flags, WS endpoint string. Cosmetic only; the *code* is correct. Acceptable to roll into next web job's report.
  - Routing: web (next job). Priority: low.
- **Safari spot-check on Hot-fresh macOS environment** when available. NFR-PO-1 spot check full matrix is a later milestone.
  - Routing: web (M3 or later). Priority: low.
- **First M2/M3 web job:** introduce `json-schema-to-typescript` codegen replacing hand-mirror once payload count crosses ~20.
  - Routing: web. Priority: future-sprint.
- **PROJECT_STATE update** (this audit closure): web stub at `localhost:5173` works end-to-end against the agent; `make run-web` runs Vite dev; cross-browser parity on Chromium + Firefox-ESR Linux; codegen vs hand-mirror trigger documented for M3.
  - Routing: orchestrator. Priority: high.
- **Close job-0016 and launch job-0017 (M1 acceptance).** This is the M1 capstone — re-runs every exit criterion from the sprint manifest as the formal acceptance record.
  - Routing: orchestrator. Priority: high.

## Sign-off

- **Ready to move to complete:** yes
- All twelve adversarial checks pass on live re-run (AC1 streaming, AC2 disconnect+reconnect in ~4 s, AC3 no gs://+no-computed-numbers, AC4 Chromium+Firefox screenshots, Decision I camera lock, Invariant 1 in JSX, contracts integration, no Qt/Tauri/Electron leakage, file ownership clean, commit hygiene, OQ specificity, Linux env in transcripts).
- Invariants #1, #5, #8, #9, #10 pass; #2, #3, #4, #6, #7 correctly n/a (no engine/workflow/QGIS surface in a client stub).
- One revision round (commits 778fe6c → 06d9d1a) addressed initial findings (report content + AC4 Firefox post-tile screenshot + contracts tightening); second review approved with three low-severity-only doc-drift findings (all accepted with rationale that the *code* is correct).
- 7 headless browser screenshots + 4 transcript files captured under `reports/inflight/job-0016-web-20260605/evidence/`.
- Real end-to-end: browser ↔ agent ↔ Vertex AI Gemini 2.5-pro ↔ MongoDB MCP ↔ Atlas Flex cluster all verified live in the AC1/AC2 round-trip.
- Revisions: 1.
