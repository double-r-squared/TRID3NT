// GRACE-2 web — TS mirror of the Appendix-A WebSocket contracts.
// The pydantic-v2 schemas live in
// `packages/contracts/schemas/*.json` (job-0013, `grace2-contracts` v0.1.0).
//
// Decision (M1 stub / M3 web skeleton): hand-mirror, not codegen. Rationale:
//
//   The full set is 35 JSON schemas; we mirror only the payload shapes the
//   client actually consumes. M1 lands 6 envelopes; M3 (this job) adds the
//   session-state + map-command surface scoped to the FIVE M3-active sub-
//   discriminants per the job-0025 kickoff §6 (zoom-to / set-temporal-config
//   / start-animation / stop-animation / invalidate-tiles are explicitly
//   deferred to M4–M5). Aggregate target ~12–14 payload types after this
//   job; the codegen-promotion trigger remains ~20 (see OQ-W-1 from
//   job-0016). If we exceed 18 here, surface a refined OQ-W-1.
//
//   This file is the single point of contract truth on the web side. Any
//   divergence from `packages/contracts/schemas/ws_*.json` is a bug — every
//   field name and enum literal here matches the pydantic schema verbatim.
//
//   Pipeline-domain types (PipelineSnapshot beyond M1 step shape, etc.) are
//   reserved for job-0026; this file deliberately leaves them out.
//
//   job-0026 update: pipeline surface formalized below. The M1-era
//   `PipelineStep` (a M1-only stub from job-0016) is renamed to the canonical
//   Appendix D.6 name `PipelineStepSummary`. `PipelineSnapshot` (Appendix D.6)
//   is added to enable `session-state.current_pipeline` and `pipeline_history`
//   reconstruction. The pydantic D.6 `PipelineStepSummary` does NOT carry
//   `progress_percent`, `error_code`, or `error_message`; the FR-WC-8
//   acceptance criteria require them for the running-progress and failed-
//   step renders. The fields are added here as `?` optionals with a
//   consumer-pushback OQ filed against schema (`OQ-W-26-PIPELINE-STEP-FIELDS`,
//   see report.md). Until schema lands the Appendix D.6 amendment, the agent
//   service in M4 will need to either (a) carry the fields out-of-band on
//   `tool-call-failed` (already in A.4) and have us correlate by step_id, or
//   (b) extend D.6 — see the OQ for the recommendation.

// --- A.1 Envelope -------------------------------------------------------- //

export interface Envelope<P> {
  type: string;          // kebab-case discriminator
  id: string;            // ULID
  ts: string;            // ISO-8601 with literal Z suffix
  session_id: string;    // ULID
  payload: P;
}

// --- A.3 client -> agent payloads --------------------------------------- //

export type ResearchMode = "research" | "deep_research";

export interface UserMessagePayload {
  text: string;
  research_mode?: ResearchMode; // default "research" (FR-WC-15 toggle carrier; A1 amendment)
}

export interface CancelPayload {
  reason?: string | null;
}

// `session-resume` carries `payload: {}` literally on the wire (see
// ws_session_resume.json: `properties: {}`, `additionalProperties: false`).
// Modeled as `Record<string, never>` so any non-empty assignment is a TS
// error AND the docstring matches the wire shape (the prior interface form
// with `[k: string]: never` was index-signature semantics — same compile-
// time effect but reads as "indexed by string", which is misleading here).
export type SessionResumePayload = Record<string, never>;

// --- A.4 agent -> client payloads --------------------------------------- //

export interface AgentMessageChunkPayload {
  message_id: string;
  delta: string;
  done?: boolean; // terminal frame is `done: true`
}

export type PipelineStepState =
  | "pending"
  | "running"
  | "complete"
  | "failed"
  | "cancelled";

// PipelineStepSummary — canonical name per Appendix D.6 (`collections.py`).
//
// The pydantic D.6 model carries:
//   step_id, name, tool_name, state, started_at?, completed_at?
//
// The `pipeline-state` envelope (A.4) `PipelineStep` model carries
// additionally `progress_percent?`. The FR-WC-8 acceptance also wants
// `error_code` + `error_message` on failed steps (currently only on the
// distinct `tool-call-failed` envelope, A.4). Per the kickoff "DO NOT parse
// out of strings, DO NOT invent fields client-side": these fields are
// modeled as `?` optionals here and the gap is filed as
// OQ-W-26-PIPELINE-STEP-FIELDS (schema consumer pushback) — proposed
// resolution: extend Appendix D.6 PipelineStepSummary with
// `progress_percent?: int (0..100) | None`, `error_code?: str | None`,
// `error_message?: str | None` so both the wire envelope and the persisted
// snapshot align. Until the amendment lands, the client renders whatever
// the agent populates; absent fields simply hide their UI affordance
// (no fabrication).
export interface PipelineStepSummary {
  step_id: string;
  name: string;
  tool_name: string;
  state: PipelineStepState;
  progress_percent?: number | null;
  started_at?: string | null;
  completed_at?: string | null;
  // Below: consumer-pushback fields (OQ-W-26-PIPELINE-STEP-FIELDS). Optional
  // here so the M3 client can render a failed step's reason if the agent
  // populates them either directly on the step or after the proposed D.6
  // amendment lands. Never fabricated client-side.
  error_code?: string | null;
  error_message?: string | null;
}

// PipelineSnapshot — Appendix D.6 (`collections.py` PipelineSnapshot). Carried
// inline as `session-state.current_pipeline` and as entries in
// `session-state.pipeline_history`. The `pipeline-state` (A.4) envelope is the
// same shape minus `final_state`/`completed_at` (those are set only when the
// pipeline terminates and the snapshot moves to history).
export interface PipelineSnapshot {
  pipeline_id: string;
  started_at?: string | null;
  completed_at?: string | null;
  final_state?: "complete" | "failed" | "cancelled" | null;
  steps: PipelineStepSummary[];
  // Future: `run_id?: string | null;` per kickoff D.6 hint. The D.6 model
  // does not currently carry `run_id`; if the engine wants it, file under
  // the OQ above. Not added here speculatively.
}

// PipelineStatePayload — Appendix A.4 `pipeline-state` envelope. Replace-not-
// reconcile per Appendix A.7: each new envelope wholesale replaces the local
// view-model; never merge/diff. The payload IS the snapshot; the optional
// `steps` field in the M1 stub is replaced here by the canonical D.6 shape:
// `steps` is a list of `PipelineStepSummary`, defaulting to empty.
export interface PipelineStatePayload {
  pipeline_id: string;
  steps?: PipelineStepSummary[];
}

export type ErrorCode =
  | "AUTH_FAILED"
  | "RATE_LIMITED"
  | "INTERNAL_ERROR"
  | "LLM_UNAVAILABLE"
  | "TOOL_NOT_FOUND"
  | "TOOL_PARAMS_INVALID"
  | "TOOL_TIMEOUT"
  | "DEM_SOURCE_UNAVAILABLE"
  | "SOLVER_FAILED"
  | "CONFIRMATION_TIMEOUT"
  | "SPATIAL_INPUT_TIMEOUT"
  | "DISAMBIGUATION_TIMEOUT"
  | "CLARIFICATION_TIMEOUT"
  | "USER_INPUT_CANCELLED"
  | "CANCELLED";

export interface ErrorPayload {
  error_code: ErrorCode;
  message: string;
  retryable?: boolean;
  retry_after_seconds?: number | null;
}

// --- Appendix D.2: ProjectLayerSummary --------------------------------- //
//
// A row in `session-state.loaded_layers`. The agent serializes the worker /
// QGIS Server project's authoritative layer list into this shape and pushes
// it on connect / reconnect. The client reads it; it never invents one.
//
// `layer_type` is open-enum-ish (raster | vector | wms | wmts | geojson);
// surface as Open Question if a new value appears at runtime. The web side
// renders all known values; unknown values render the row but disable the
// type-specific affordances.

export type ProjectLayerType =
  | "raster"
  | "vector"
  | "wms"
  | "wmts"
  | "geojson";

export interface ProjectLayerSummary {
  layer_id: string;        // ULID assigned by the agent / worker
  name: string;            // human-readable, e.g. "Storm-surge max" or "Basemap"
  layer_type: ProjectLayerType;
  source_url?: string | null;   // WMS endpoint or GeoJSON URL — never gs:// (Invariant 5)
  attribution?: string | null;  // displayed in the LayerPanel row
  visible: boolean;        // initial visibility from the project state
  opacity: number;         // 0..1, clamped on render
  z_index: number;         // integer; lower draws first (bottom of stack)
  temporal?: TemporalConfig | null; // null for non-temporal layers
}

// Appendix D.6 temporal block (subset web reads). Driven by WMS TIME param.
export interface TemporalConfig {
  start: string;           // ISO-8601 UTC with Z
  end: string;             // ISO-8601 UTC with Z
  step_seconds: number;    // animation cadence (FR-QS-4)
}

// --- Appendix D.6: MapView --------------------------------------------- //
//
// The persisted camera position on session-state. The web client applies it
// on session-resume (instant jump if it matches current; flyTo otherwise).

export interface MapView {
  center: [number, number]; // [lng, lat] in EPSG:4326
  zoom: number;
  bearing?: number;         // always 0 in v0.1 (Decision I: 2D camera lock)
  pitch?: number;           // always 0 in v0.1 (Decision I)
}

// --- A.4 session-state -------------------------------------------------- //
//
// Replaces the M1 stub's `unknown[]` placeholders with the real list-of-
// `ProjectLayerSummary` shape this job consumes. `chat_history` and
// `pipeline_history` stay as `unknown[]` here — chat is M1's domain and
// pipeline-history reconstruction is job-0026's domain (it will refine).
// `current_pipeline` likewise typed-loose until job-0026 lands.

export interface SessionStatePayload {
  chat_history?: unknown[];
  loaded_layers?: ProjectLayerSummary[];
  pipeline_history?: unknown[];
  current_pipeline?: unknown | null;
  map_view?: MapView | null;
}

// --- A.4 map-command --------------------------------------------------- //
//
// One envelope type (`map-command`) carries an internal `command`
// discriminator. The kickoff (job-0025 audit.md §6) explicitly scopes M3 to
// the FIVE active sub-discriminants — load-layer / remove-layer /
// set-layer-visibility / set-layer-opacity / set-layer-order — and
// explicitly states that the other five (zoom-to / set-temporal-config /
// start-animation / stop-animation / invalidate-tiles) are deferred to
// M4–M5 and NOT mirrored here. Round-1 revision: dropping the 5 deferred
// shapes (they were mirrored speculatively in the v1 ship and flagged as a
// scope-drift blocker by the reviewer).

export interface LoadLayerCommand {
  command: "load-layer";
  layer: ProjectLayerSummary;
  position?: "top" | "bottom" | number; // integer z-index slot
}

export interface RemoveLayerCommand {
  command: "remove-layer";
  layer_id: string;
}

export interface SetLayerVisibilityCommand {
  command: "set-layer-visibility";
  layer_id: string;
  visible: boolean;
}

export interface SetLayerOpacityCommand {
  command: "set-layer-opacity";
  layer_id: string;
  opacity: number; // 0..1
}

export interface SetLayerOrderCommand {
  command: "set-layer-order";
  layer_ids: string[]; // full ordered list, top-of-stack first
}

export type MapCommandPayload =
  | LoadLayerCommand
  | RemoveLayerCommand
  | SetLayerVisibilityCommand
  | SetLayerOpacityCommand
  | SetLayerOrderCommand;

// --- Outbound message constructors -------------------------------------- //

/** Generate a fresh ULID-like 26-char Crockford base32 id.
 *
 * Stub substitute for `python-ulid`. The agent's contracts package uses real
 * ULIDs; the web client only needs an opaque time-sortable string the agent
 * accepts as the envelope `id` / `session_id`. We use crypto.randomUUID()'s
 * entropy folded into Crockford base32 — preserves the 26-char ULID shape
 * the contracts package validates. A real ULID library is a clean upgrade
 * (surfaced as OQ-W-2).
 */
export function newUlid(): string {
  const crockford = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";
  // 48-bit timestamp ms (10 chars) + 80-bit randomness (16 chars) = 26 chars.
  const ms = Date.now();
  let timeHex = ms.toString(16).padStart(12, "0");
  let out = "";
  // encode the 48-bit timestamp into 10 Crockford chars
  let n = BigInt("0x" + timeHex);
  for (let i = 9; i >= 0; i--) {
    out = crockford[Number(n & 31n)] + out;
    n >>= 5n;
  }
  // 16 random Crockford chars (80 bits)
  const rnd = new Uint8Array(10);
  crypto.getRandomValues(rnd);
  let randHex = "";
  for (const b of rnd) randHex += b.toString(16).padStart(2, "0");
  let r = BigInt("0x" + randHex);
  let rs = "";
  for (let i = 15; i >= 0; i--) {
    rs = crockford[Number(r & 31n)] + rs;
    r >>= 5n;
  }
  void timeHex;
  return out + rs;
}

export function nowZ(): string {
  // ISO-8601 with literal Z suffix — matches the contracts UTCDatetime
  // serializer (`.replace("+00:00", "Z")`-style; Date#toISOString already
  // emits Z).
  return new Date().toISOString();
}

export function envelope<P>(
  type: string,
  sessionId: string,
  payload: P,
): Envelope<P> {
  return {
    type,
    id: newUlid(),
    ts: nowZ(),
    session_id: sessionId,
    payload,
  };
}
