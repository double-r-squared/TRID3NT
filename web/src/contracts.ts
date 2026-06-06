// GRACE-2 web — TS mirror of the M1 subset of the Appendix-A WebSocket
// contracts. The pydantic-v2 schemas live in
// `packages/contracts/schemas/*.json` (job-0013, `grace2-contracts` v0.1.0).
//
// Decision (M1 stub): hand-mirror, not codegen. Rationale below.
//
//   The full set is 35 JSON schemas; M1 only needs 7 payload shapes plus the
//   envelope. A codegen step (json-schema-to-typescript) would introduce a
//   build-time dep, a generation script, and a generated artifact to keep in
//   sync — overhead larger than the surface it covers. When M3 expands the
//   client to load layers, render pipelines, and handle pick-modes (~20+
//   shapes), codegen wins; for M1 hand-mirror is the cleaner choice.
//
//   This file is the single point of contract truth on the web side. Any
//   divergence from `packages/contracts/schemas/ws_*.json` is a bug — every
//   field name and enum literal here matches the pydantic schema verbatim.
//
//   Surfaced as Open Question OQ-W-1 in the report.

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

export interface SessionResumePayload {
  // empty per schema
  [k: string]: never;
}

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

export interface PipelineStep {
  step_id: string;
  name: string;
  tool_name: string;
  state: PipelineStepState;
  progress_percent?: number | null;
  started_at?: string | null;
  completed_at?: string | null;
}

export interface PipelineStatePayload {
  pipeline_id: string;
  steps?: PipelineStep[];
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

// session-state nested types are carried as plain dicts on the wire per the
// schema (`SessionStatePayload` description) — they reference Appendix D.6
// models that the agent serializes. M1 stub only reads top-level structure.
export interface SessionStatePayload {
  chat_history?: unknown[];
  loaded_layers?: unknown[];
  pipeline_history?: unknown[];
  current_pipeline?: unknown | null;
  map_view?: unknown | null;
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
