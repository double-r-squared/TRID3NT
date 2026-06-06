# Report: Web stub — React+MapLibre CONUS + chat round-trip

**Job ID:** job-0016-web-20260605
**Sprint:** sprint-03
**Specialist:** web
**Task:** Stand up `web/` as a React + Vite + TypeScript app with a MapLibre GL JS CONUS basemap (OSM Tier A, 2D-locked per Decision I) and a minimal chat panel that streams `agent-message-chunk` deltas from the local Appendix-A WebSocket agent (job-0015), with `session-resume`-based auto-reconnect. Mirror the M1 subset of the Appendix-A WebSocket contracts (job-0013). Wire `make run-web` to Vite.
**Status:** ready-for-audit

## Summary

Landed `web/` as a React+Vite+TS app: MapLibre GL JS CONUS basemap (OSM Tier A
with attribution, camera 2D-locked per Decision I), minimal chat panel with
streamed `agent-message-chunk` deltas, exponential-backoff reconnect that
re-issues `session-resume` on every open. The M1 subset of the Appendix-A
WebSocket schemas (envelope + 7 payloads) is hand-mirrored in
`web/src/contracts.ts`; codegen deferred to M3 (OQ-W-1). `make run-web`
launches Vite. Live E2E validated against the running job-0015 agent on the
same host: AC1–AC4 all pass on Chromium and Firefox (Firefox AC4 captured via
Playwright Firefox post-tile-load — see Revision Round 1 below).

## Changes Made

- **Makefile** — `run-web` target: `cd web && npm install && npm run dev --
  --host 127.0.0.1 --port 5173`. Replaces the prior scaffold stub.
- **web/index.html** — bare HTML shell, viewport meta, mounts `#root`.
- **web/package.json**, **web/package-lock.json** — pinned deps:
  `react@18.3.1`, `react-dom@18.3.1`, `maplibre-gl@4.7.1`, `vite@5.4.11`,
  `@vitejs/plugin-react@4.3.4`, `typescript@5.6.3`. No runtime deps beyond
  React + MapLibre.
- **web/vite.config.ts** — React plugin, `server.watch.usePolling=true`
  (Debian inotify limit workaround on this dev substrate), HMR on 5173.
- **web/tsconfig.json** — `strict`, `noUncheckedIndexedAccess`,
  `noFallthroughCasesInSwitch`, `jsx:"react-jsx"`, `target:ES2022`.
- **web/src/main.tsx** — React 18 root mount.
- **web/src/App.tsx** — top-level layout: `<MapView/>` absolute-positioned
  full-screen, `<Chat/>` overlay panel anchored top-right. Holds no state.
- **web/src/Map.tsx** — MapLibre map initialization:
  - OSM raster source from `tile.openstreetmap.org` with the required
    OpenStreetMap attribution string;
  - initial CONUS view (`center:[-95.5,37], zoom:4`);
  - **Decision I camera lock**: constructor `maxPitch:0`,
    `dragRotate:false`, `pitchWithRotate:false`, `touchPitch:false`, plus
    belt-and-suspenders `touchZoomRotate.disableRotation()` and
    `keyboard.disableRotation()`;
  - `NavigationControl({showCompass:false})` for pan/zoom UI only.
- **web/src/contracts.ts** — TS mirror of the M1 subset of
  `packages/contracts/schemas/ws_*.json` (Envelope, UserMessagePayload,
  CancelPayload, SessionResumePayload, AgentMessageChunkPayload,
  PipelineStatePayload, ErrorPayload, SessionStatePayload). Plus
  `newUlid()` (Crockford-base32 26-char stub) and `nowZ()` ISO-8601
  helpers and an `envelope(type, sessionId, payload)` constructor.
- **web/src/ws.ts** — singleton WS client:
  - URL `ws://127.0.0.1:8765/`;
  - session-id persisted in `sessionStorage` so a page reload resumes;
  - exponential backoff 500ms→5s cap with jitter on close/error;
  - on open: re-issues `session-resume` envelope carrying the session id;
  - typed dispatch (`onAgentChunk`, `onPipelineState`, `onSessionState`,
    `onError`, `onStatus`) keyed by message `type`;
  - exposes `status: "connected" | "reconnecting" | "disconnected"` and
    a `sendUserMessage(text, mode)` helper.
- **web/src/Chat.tsx** — chat panel React component:
  - input textarea + Enter / Cmd-Enter to send;
  - status banner driven by `ws.status`;
  - subscribes to `onAgentChunk` and appends `delta` per `message_id`,
    finalizing when `done === true` (handles the empty-delta terminator
    frame the agent emits — see Decisions Made);
  - subscribes to `onPipelineState` to render a single-line pipeline
    state indicator (running / complete);
  - scrollback capped at the visible panel; no client-side numeric
    formatting (Invariant 1 preserved trivially — text is rendered as the
    raw `delta` string concatenation).
- **web/src/vite-env.d.ts** — Vite client types reference.

## Decisions Made

### D-W-1. Hand-mirror, not codegen, for the M1 contracts subset
Mirrored the M1 subset (envelope + 7 payloads) by hand in
`web/src/contracts.ts` rather than running `json-schema-to-typescript`
over all 35 schemas in `packages/contracts/schemas/`.

- **Why now:** M1 needs only 7 shapes; codegen would introduce a build-time
  dep, a generation script, and a generated artifact to keep in sync with
  the schemas — overhead larger than the surface it covers.
- **Why M3 may invert this:** when the client expands to load layers,
  render pipelines, and handle pick-modes (~20+ shapes from Appendix A.4 +
  A.5 + D.6), codegen wins. Surfaced as OQ-W-1.
- **Alternative considered:** `quicktype` (heavier, multi-language) and
  `json-schema-to-typescript` (lighter, but still requires a generation
  pass on every schema change). Both rejected for M1 size of surface.
- **Drift guard:** every field name and enum literal in `contracts.ts`
  matches the pydantic schema verbatim; any divergence is a bug. Surfaced
  in the file's header comment.

### D-W-2. Terminal `done:true` is emitted as an empty-delta frame
The agent (job-0015) ends an `agent-message-chunk` stream by emitting a
final frame carrying `delta:""` and `done:true` (see
`evidence/grace2-ws-transcript.txt` — content frame
`delta:"DONE", done:false` followed by terminator `delta:"", done:true`).
This is consistent with the contract (`done` optional, `delta` required
string with empty allowed) but is not explicitly called out in
`packages/contracts/schemas/ws_agent_message_chunk.json`.

- **Web side:** `Chat.tsx`'s `appendDelta` handles this via
  `existing.done || p.done === true` — the empty terminator marks the
  message complete without injecting an empty character.
- **Decision:** accept as a wire-level contract clarification.
  Surfaced as OQ-W-3 so the orchestrator can decide whether to amend the
  schema description to make the terminator pattern explicit.

### D-W-3. `SessionResumePayload` typed as `Record<string, never>`
The schema for `session-resume` is `payload: {}` literally (see
`packages/contracts/schemas/ws_session_resume.json`: `properties: {}`,
`additionalProperties: false`). Modeled as
`export type SessionResumePayload = Record<string, never>` so any non-empty
assignment is a TS error AND the type reads as "intentionally empty," not
as "indexed by string". (Round 1 follow-up to reviewer's low-severity
docstring note.)

### D-W-4. ULID stub via `crypto.getRandomValues` (web only)
The agent's contracts package uses real ULIDs; the web client only needs
an opaque time-sortable 26-char Crockford-base32 string the agent accepts
as the envelope `id` / `session_id`. `newUlid()` in `contracts.ts` encodes
a 48-bit timestamp + 80-bit random into 26 Crockford chars without pulling
in a `ulid` npm dep. Surfaced as OQ-W-2; a real `ulid` lib is a clean
upgrade if monotonicity guarantees become load-bearing on the client side.

### D-W-5. Vite HMR via polling on Debian
`server.watch.usePolling=true` in `vite.config.ts` — Debian 13 default
inotify watcher limits trip Vite's chokidar on a `node_modules`-heavy
tree. Linux-substrate-specific; not a portability concern (the prod
substrate is the browser, not Vite).

## Invariants Touched

- **Invariant 1 (no LLM-rendered numbers in JSX):** preserved trivially.
  `Chat.tsx` (rendering path) reads agent text directly — the raw
  accumulator of typed `delta` strings — without any numeric formatting,
  client-side computation, or hazard-value parsing. All numeric literals
  in `Chat.tsx` are CSS measurements. `App.tsx` contains no numeric
  content. `Map.tsx` uses MapLibre-internal coordinates only (camera
  config), not domain numbers. Verified by reviewer (`grep` for hazard
  tokens returned no hits in client code).
- **Decision I (camera 2D lock):** preserved and explicitly enforced in
  `Map.tsx` (`maxPitch:0`, `dragRotate:false`, `pitchWithRotate:false`,
  `touchPitch:false`, plus runtime
  `touchZoomRotate.disableRotation()` / `keyboard.disableRotation()`,
  plus `NavigationControl({showCompass:false})`).
- **Decision G (consumer pushback, no client-side workarounds):**
  preserved. D-W-2 (empty-delta terminator) is surfaced as OQ-W-3 for the
  contracts owner to clarify rather than worked around silently on the
  client.

## Open Questions

### OQ-W-1. Codegen vs hand-mirror for M3 contracts
M1 has 7 shapes; hand-mirroring is cleaner. M3 will need ~20+ shapes
(`load-layer`, `render-pipeline`, pick-modes, full Appendix-D models) and
per-job schema bumps will be more frequent. **Tentative recommendation:**
invert at M3 in favor of `json-schema-to-typescript` codegen as a
`web/scripts/gen-contracts.ts` prebuild step, with the generated file
committed and CI-checked against a re-run. Defer the actual decision to
the M3 kickoff so the contracts owner can weigh in on schema cadence.

### OQ-W-2. Real `ulid` npm dep vs `newUlid()` stub
`web/src/contracts.ts` ships a 30-line ULID stub built on
`crypto.getRandomValues`. It preserves the 26-char Crockford base32 ULID
shape the contracts package validates and the agent accepts. **Open:** if
session-id monotonicity or proper ULID timestamp ordering becomes
load-bearing on the client (e.g., for client-side message ordering
without server timestamps), swap in `ulid` from npm (≈4 kB minified). No
action needed for M1.

### OQ-W-3. Schema clarification — `agent-message-chunk` terminator pattern
The agent emits the final frame of an `agent-message-chunk` stream with
`delta: ""` and `done: true`, content frames carrying `done: false`. The
M1 schema description does not make this pattern explicit. **Open:**
should `packages/contracts/schemas/ws_agent_message_chunk.json` be
amended (description field, not the schema itself) to document the
terminator frame pattern? This is a contracts-owner question, not a web
question — the web client handles both possible patterns correctly.

### OQ-W-4. First-token latency 10× over NFR-P-1 budget
`evidence/ws_client-ac1.txt` records first-token latency of **20.04 s**
vs the NFR-P-1 budget of **2.0 s**. AC1 itself does not require NFR-P-1
verification at this job. **Open:** likely cold-start (Vertex AI Gemini
region warm-up plus first WebSocket handshake), but warrants a future
NFR job to characterize warm vs cold latency end-to-end and route to the
NFR-P-1 owner. Not blocking on AC1 — chunks are correctly streamed
token-by-token once the first chunk arrives (≈100 ms per chunk
thereafter per `evidence/cdp-ac1.log`).

### OQ-W-5. Safari spot-check deferred until macOS dev environment
NFR-PO-1 calls for cross-browser support on Chromium, Firefox, and
Safari. This job validated on Chromium and Firefox on Linux. **Open:**
Safari spot-check is deferred until a macOS environment is available
(kickoff explicitly authorizes this deferral). Action: add a Safari
smoke test to the M3 kickoff once macOS access is provisioned.

## Dependencies and Impacts

- **Upstream consumed:** job-0013 (Appendix-A WebSocket schemas, the
  source of truth `contracts.ts` mirrors) and job-0015 (the running local
  agent on `ws://127.0.0.1:8765/` — wire transcript matched exactly).
- **Downstream impact:** M3 will replace this stub with the full layered
  client. Until then, this stub IS the canonical demo for sprint reviews.
  The contracts mirror provides a tested baseline of payload shapes M3
  codegen will need to match.
- **Operational:** `make run-web` requires the agent on port 8765 to
  exercise the chat path; reconnect-on-drop means a temporarily-absent
  agent shows a "reconnecting" banner without crashing.

## Verification

All ACs were re-run live against `make run-agent` + `make run-web` on this
host. Evidence files in
`reports/inflight/job-0016-web-20260605/evidence/`:

| AC | File | What it proves |
| --- | --- | --- |
| AC1 | `chrome-ac1-mid.png`, `chrome-ac1-final.png` | CONUS OSM basemap + chat panel streaming real Gemini reply (token-by-token visible across mid → final). |
| AC1 | `cdp-ac1.log` | Polled char-count from 17 → 596 → 1185 → … → 6666 with `done` flipping to `true` at final — observable token-stream cadence. |
| AC1 | `ws_client-ac1.txt` | 22 raw `agent-message-chunk` frames, terminator `done=True total_chunks=22`, header comment notes 20 s first-token latency vs NFR-P-1 (OQ-W-4). |
| AC1 | `grace2-ws-transcript.txt` | Full envelope-level WS trace: `session-resume` → `session-state` → `user-message` → `pipeline-state running` → `agent-message-chunk{delta:"DONE",done:false}` → `agent-message-chunk{delta:"",done:true}` → `pipeline-state complete`. Confirms D-W-2 terminator pattern. |
| AC2 | `chrome-ac2-disconnected.png`, `chrome-ac2-reconnected.png`, `chrome-ac2-fresh-reply.png` | Three states of the WS lifecycle: agent killed → disconnected banner; agent restarted → connected banner; fresh user message → new agent reply rendered. |
| AC2 | `cdp-ac2.log` | Status transitions `connected → reconnecting (post-kill) → connected (~4 s, poll 4)` then a fresh `user-message` sent and an `agent-message-chunk` chars=37 with `done=true` observed. |
| AC3 | (reviewer-side `grep`) | Confirmed by reviewer: no `gs://`, no hazard-domain numeric tokens, no client-side math in `web/src/`. Invariant 1 preserved. |
| AC4 — Chrome | `chrome-ac1-final.png`, `chrome-initial.png` | CONUS OSM basemap renders with chat panel overlay in Chromium. |
| AC4 — Firefox | `firefox-ac4-tiles.png` (Round 1) | Playwright Firefox driver loads the Vite app, waits for OSM tiles (35 × 200 responses observed), waits for MapLibre canvas to mount, captures CONUS map fully rendered. See `firefox-ac4.log` for the per-tile network log. Replaces the prior `firefox-initial.png` which was captured by `firefox --headless --screenshot` before tile responses arrived. |
| Camera lock | `web/src/Map.tsx` | Decision I observably enforced (reviewer pass). |
| File ownership | `git show --stat HEAD~1` + Round 1 commit | All deltas under `web/**` + root `Makefile`. `node_modules/` not committed (`git ls-files | grep node_modules` → empty). |

## Revision Round 1

Triggered by the reviewer pass on the original submission. Findings
addressed:

### R1-1. report.md populated (was blocking)
The previous `report.md` was the orchestrator's empty template — every
required section blank. This revision populates every AGENTS.md §Required
Files section (Summary, Changes Made, Decisions Made, Invariants Touched,
Open Questions, Dependencies and Impacts, Verification) and lifts the
contracts-approach and Safari-deferral notes out of the in-file
`contracts.ts` comment into proper Open Questions (OQ-W-1 — codegen vs
hand-mirror, OQ-W-2 — ulid stub, OQ-W-3 — terminator schema
clarification, OQ-W-4 — first-token latency cold-start, OQ-W-5 — Safari
deferral). Previous report archived at `.history/report.v1.md`.

### R1-2. AC4 Firefox screenshot now post-tile-load (was blocking)
The original `firefox-initial.png` was captured by
`firefox --headless --screenshot`, which fires synchronously before
MapLibre's tile requests complete. Replaced with `firefox-ac4-tiles.png`
captured via Playwright Firefox driver (`firefox@playwright v1522 /
150.0.2`): the script loads the page, waits for the
`[data-testid="grace2-map"]` canvas + at least 6 OSM `200` responses,
then a 1.5 s network-idle window, then a 0.5 s paint settle. Per-tile log
in `firefox-ac4.log`: 35 tile responses, all 200. Original blank
screenshot retained as `firefox-initial.png` for the audit trail. The
Playwright runner was installed via `npm install --no-save playwright`
(no commit-side dep added; `web/package.json` and `package-lock.json`
unchanged) and the temporary `firefox-mappost.cjs` script was removed
after running — no source-tree pollution.

### R1-3. `SessionResumePayload` typed as `Record<string, never>` (was low)
Per reviewer's low-severity docstring note, `SessionResumePayload`
changed from `interface { [k:string]: never }` (index-signature
semantics) to `type ... = Record<string, never>` with a header comment
pointing at `ws_session_resume.json`. Compile-time effect identical;
documentation intent now matches the wire shape. `ws.ts`'s `{} as
SessionResumePayload` cast still works. `npx tsc --noEmit` clean.

### R1-4. Medium-severity AC1 observations folded into Decisions / Open Questions
The reviewer flagged two AC1 observations that were not defects but
needed routing: the empty-delta terminator pattern (now D-W-2) and the
first-token-latency 10× NFR-P-1 budget overrun (now OQ-W-4). Both are
captured above; no code change needed.

### Files touched in Round 1
- `reports/inflight/job-0016-web-20260605/report.md` — full populate.
- `reports/inflight/job-0016-web-20260605/.history/report.v1.md` — prior
  empty-template report archived.
- `reports/inflight/job-0016-web-20260605/evidence/firefox-ac4-tiles.png`
  — new Firefox post-tile-load screenshot (≈944 kB, 1440×900).
- `reports/inflight/job-0016-web-20260605/evidence/firefox-ac4.log` —
  Playwright Firefox per-tile network log.
- `web/src/contracts.ts` — `SessionResumePayload` typedef tightened
  (D-W-3 / R1-3).

No changes to `Map.tsx`, `Chat.tsx`, `ws.ts`, `App.tsx`, `main.tsx`,
`Makefile`, `package.json`, `package-lock.json`, `vite.config.ts`,
`tsconfig.json`, or `index.html`.
