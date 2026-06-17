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
  // duration_ms (job-0264, ELEVATED tool-timer requirement): authoritative
  // wall-clock elapsed time the agent stamps on the TERMINAL transition
  // (complete / failed / cancelled), derived deterministically from
  // completed_at - started_at. `None` for pending/running — PipelineCard
  // shows a cosmetic live ticker until this lands, then locks to this value.
  // Never fabricated client-side. Mirrors PipelineStep.duration_ms (ws.py).
  duration_ms?: number | null;
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
  uri: string;             // gs://... GCS file pointer (COG / FlatGeobuf / GeoParquet)
  wms_url?: string | null; // QGIS Server WMS endpoint for MapLibre tile registration (job-0072, OQ-62-LAYERURI-URI-FIELD)
  attribution?: string | null;  // displayed in the LayerPanel row
  visible: boolean;        // initial visibility from the project state
  opacity: number;         // 0..1, clamped on render
  z_index: number;         // integer; lower draws first (bottom of stack)
  temporal?: TemporalConfig | null; // null for non-temporal layers
  // style_preset formally defined in D.2 via job-0072 (closes OQ-W-65-STYLE-PRESET).
  // Optional here because older documents may omit it; UI hides legend affordance gracefully.
  style_preset?: string | null;
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

// --- Per-Case secrets (job-0125, sprint-12-mega Wave 2) ----------------- //
//
// Mirrors packages/contracts/src/grace2_contracts/secrets.py (the canonical
// pydantic shapes). Closed Literal vocabulary; broadening the set requires
// the corresponding SRS §F.3 amendment + secrets.py update.
//
// Wire shapes:
//   - secrets-list (server -> client): list of SecretRecord
//   - secret-add (client -> server): { provider, case_id, label, key_value }
//   - secret-revoke (client -> server): { secret_id }
//
// Decision F: `key_value` is transient on the wire; the server writes it to
// the vault on receipt and never echoes it back. The web client clears the
// key from local React state immediately after submit.
// Invariant 9 (no cost theater): no cost / quota / usage-count field.

export type ProviderID =
  | "ebird"
  | "iucn_red_list"
  | "movebank"
  | "nws"
  | "openweathermap"
  | "openai"
  | "anthropic"
  | "google_genai"
  | "mapbox"
  | "maptiler";

export interface SecretRecord {
  schema_version?: "v1";
  secret_id: string;        // ULID
  provider: ProviderID;
  case_id?: string | null;  // null = user-level (M6+ identity required)
  vault_ref: string;        // opaque vault URI (never the key value)
  label?: string | null;
  added_at: string;         // ISO-8601 Z
  last_used_at?: string | null;
  is_active: boolean;
}

export interface SecretsListPayload {
  envelope_type?: "secrets-list";
  secrets: SecretRecord[];
}

export interface SecretAddPayload {
  envelope_type?: "secret-add";
  provider: ProviderID;
  case_id?: string | null;
  label?: string | null;
  key_value: string;        // transient — cleared after submit
}

export interface SecretRevokePayload {
  envelope_type?: "secret-revoke";
  secret_id: string;
}

// --- Case persistence envelopes (job-0137, sprint-12-mega Wave 3 — FR-MP-6) //
//
// Mirrors packages/contracts/src/grace2_contracts/case.py (the canonical
// pydantic shapes). The Case is the user-facing left-rail entity; the storage
// model name "project" stays canonical, but the wire envelopes use "case".
//
// Wire shapes:
//   - case-list (server -> client): list of CaseSummary; emitted on connect
//     and after every successful case-command.
//   - case-open (server -> client): CaseSessionState | null; emitted on
//     case-command(create|select) when the rehydration succeeds; null when
//     the server cannot rehydrate (archived/deleted between list+select).
//   - case-command (client -> server): one of create / select / rename /
//     archive / delete; carries optional case_id (REQUIRED for every command
//     except create) and an args dict (e.g. { title: "..." } for rename and
//     create-hint).
//
// Invariant 9 (no cost theater): no cost / quota / quote field anywhere.
// Invariant 8 (cancellation is first-class): no cancel field on case-command;
// cancellation flows through the standard `cancel` envelope (A.3).

export type CaseStatus = "active" | "archived" | "deleted";

export interface CaseSummary {
  schema_version?: "v1";
  case_id: string;          // ULID; maps 1:1 to projects._id (FR-MP-6)
  title: string;
  created_at: string;       // ISO-8601 UTC Z
  updated_at: string;       // ISO-8601 UTC Z
  status: CaseStatus;
  bbox?: [number, number, number, number] | null; // [minLon, minLat, maxLon, maxLat]
  primary_hazard?: string | null;
  layer_summary?: string[]; // flat list of layer_ids
  // job-0172 Part B: per-Case ``ProjectLayerSummary`` snapshots persisted
  // server-side so a Case re-open rehydrates ``loaded_layers``. The web
  // client reads ``CaseSessionState.loaded_layers`` on case-open rather
  // than this field; it's exposed on the summary for forward compatibility.
  loaded_layer_summaries?: ProjectLayerSummary[];
  qgs_project_uri?: string | null;
}

// ToolCardRecord — job-0267 (full-stream persistence): the replayable
// terminal record of ONE tool dispatch inside a Case turn. Mirrors
// `grace2_contracts.case.ToolCardRecord`. The live tool cards render from
// `pipeline-state` envelopes (wire-only); this record is their persisted
// twin so a Case reopen re-renders the inline cards. `state` is a CLOSED
// two-value enum — cancelled dispatches persist nothing (Invariant 8) and
// pending/running are live-wire-only states.
export interface ToolCardRecord {
  schema_version?: "v1";
  tool_name: string;
  state: "complete" | "failed";
  started_at?: string | null;
  duration_ms?: number | null;
  label?: string | null;
}

// CaseChatMessage — one persisted chat exchange in a Case session. The
// rehydration replay reconstructs the chat panel from a list of these.
// `map_command_emissions` is kept as `unknown[]` here because the agent
// validates each entry against the MapCommandPayload union before write; the
// web side replays them through the existing map-command dispatch path.
// job-0267: `role` gains "tool" — one row per dispatched registry tool,
// interleaved with user/agent turns by `created_at`; the typed payload is
// `tool_card` (content carries the same record as a JSON string).
export interface CaseChatMessage {
  schema_version?: "v1";
  message_id: string;
  case_id: string;
  role: "user" | "agent" | "system" | "tool";
  content: string;
  pipeline_id?: string | null;
  tool_card?: ToolCardRecord | null; // set IFF role === "tool" (job-0267)
  layer_emissions?: string[];
  map_command_emissions?: MapCommandPayload[]; // typed-loose union; agent validates
  created_at: string;
}

// CaseSessionState — the rehydration envelope returned when a user opens
// a Case. Mirrors the server-side CaseSessionState from case.py: the client
// rebuilds chat from chat_history, the LayerPanel from loaded_layers, and
// the map jumps to the Case bbox.
export interface CaseSessionState {
  schema_version?: "v1";
  case: CaseSummary;
  chat_history?: CaseChatMessage[];
  loaded_layers?: ProjectLayerSummary[];
  pipeline_history?: PipelineSnapshot[];
  current_pipeline?: PipelineSnapshot | null;
}

export interface CaseListEnvelopePayload {
  envelope_type?: "case-list";
  cases: CaseSummary[];
}

export interface CaseOpenEnvelopePayload {
  envelope_type?: "case-open";
  session_state: CaseSessionState | null;
}

export type CaseCommand =
  | "create"
  | "select"
  | "deselect" // job-0269: client navigated out of the Case to the Cases root
  | "rename"
  | "archive"
  | "delete";

export interface CaseCommandEnvelopePayload {
  envelope_type?: "case-command";
  command: CaseCommand;
  case_id?: string | null;
  args?: Record<string, unknown>;
}

// --- Tool payload-warning envelopes (job-0127, sprint-12-mega Wave 2) ---- //
//
// Mirrors packages/contracts/src/grace2_contracts/payload_warning.py.
// Agent emits `tool-payload-warning` before dispatching a tool whose
// estimated response payload exceeds the warning threshold (default 25 MB).
// Client renders an inline chat card with [Proceed] [Cancel] [Narrow scope]
// buttons. The user's selection rides back on `tool-payload-confirmation`.
//
// Invariant 9 (no cost theater): `estimated_mb` is a payload-size estimate,
// not a dollar / latency / quota figure. No cost field anywhere.

export type PayloadWarningOption = "proceed" | "cancel" | "narrow_scope";

export interface PayloadWarningEnvelopePayload {
  envelope_type?: "tool-payload-warning";
  warning_id: string;
  tool_name: string;
  tool_args: Record<string, unknown>;
  estimated_mb: number;
  threshold_mb: number;
  recommendation: string;
  alternative_args?: Record<string, unknown> | null;
  options: PayloadWarningOption[];
  ttl_seconds?: number;
}

export type PayloadConfirmationDecision = "proceed" | "cancel" | "narrow_scope";

export interface PayloadConfirmationEnvelopePayload {
  envelope_type?: "tool-payload-confirmation";
  warning_id: string;
  decision: PayloadConfirmationDecision;
  revised_args?: Record<string, unknown> | null;
}

// --- Layer-delete envelope (job-0325 F53) ------------------------------- //
//
// Client -> server: the user clicked the per-row delete control in the
// LayerPanel. The server drops the layer from the session's loaded_layers,
// persists the post-deletion list AUTHORITATIVELY (replace semantics, NOT the
// union merge used for loaded-layer adds — a union would resurrect the deleted
// layer on the next turn / Case reopen), and emits a fresh `session-state`
// without the layer. Map.tsx then removes the overlay via replace-not-reconcile
// (Appendix A.7), and the agent's loaded-layers awareness (build_layers_present_note)
// stops listing it because it is gone from both the in-memory emitter and the
// persisted summaries.
//
// This is a NEW direction from the inbound server->client `map-command`
// `remove-layer` discriminant (RemoveLayerCommand above): `map-command` is
// outbound-only today, so reusing that discriminant would overload the
// direction semantics. A dedicated `layer-delete` envelope keeps client->server
// intent distinct from the server->client map mutations.
export interface LayerDeletePayload {
  envelope_type?: "layer-delete";
  layer_id: string;
}

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
