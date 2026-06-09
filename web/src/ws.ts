// GRACE-2 web — WebSocket client with reconnect + session-resume.
//
// Talks the Appendix-A protocol against the agent service from job-0015.
// Default endpoint is the local dev agent at ws://localhost:8765.
//
// Reconnect strategy (NFR-R-2 basic for M1):
//   - On open, send `session-resume` carrying the persisted `session_id`
//     (envelope-level; payload is empty per A.3).
//   - On close, schedule a reconnect with capped exponential backoff.
//   - The session_id is generated once and persisted in localStorage so
//     reload preserves the session. M3 will use it to rebuild chat / layers /
//     pipeline from the returned `session-state`; M1 only reuses the id.
//
// State callbacks let the React layer render connection status and dispatch
// incoming frames without coupling to MapLibre or the chat panel.

import {
  AgentMessageChunkPayload,
  CancelPayload,
  CaseCommand,
  CaseCommandEnvelopePayload,
  CaseListEnvelopePayload,
  CaseOpenEnvelopePayload,
  Envelope,
  ErrorPayload,
  MapCommandPayload,
  PayloadConfirmationDecision,
  PayloadConfirmationEnvelopePayload,
  PayloadWarningEnvelopePayload,
  PipelineStatePayload,
  ProviderID,
  ResearchMode,
  SecretAddPayload,
  SecretRevokePayload,
  SecretsListPayload,
  SessionResumePayload,
  SessionStatePayload,
  UserMessagePayload,
  envelope,
  newUlid,
} from "./contracts";
import { getIdToken } from "./auth";
// Wire-shape mirrors for the server's source-suggestion candidate envelopes.
// Server-internal envelope_type names (`mode2-candidate`, etc.) are preserved
// on the wire; UI text never references them (translated by
// SourceSuggestionInline). job-0145 renamed the local TS module from
// mode2_suppression → source_suggestion_suppression and the type aliases;
// envelope_type literals and method names on the wire are unchanged so the
// server contract is not affected.
import {
  SourceAddConfirmedPayload as Mode2AddConfirmedPayload,
  SourceAuditEventPayload as Mode2AuditEventPayload,
  SourceCandidatePayload as Mode2CandidatePayload,
  SourceSuggestedKind as Mode2SuggestedKind,
} from "./lib/source_suggestion_suppression";

/**
 * Wire shape for the `auth-token` envelope (job-0123, sprint-12-mega Wave 2).
 *
 * The agent service consumes this after the connect handshake to bind the
 * Firebase `uid` → `UserDocument` (per SRS Appendix H.5). The payload is
 * intentionally narrow — Wave 2 schema (job-0122) will land the canonical
 * pydantic shape in `packages/contracts`; until then this mirrors what the
 * agent verifier reads:
 *   - `id_token`: Firebase ID JWT (1h lifetime)
 *   - `provider`: best-effort signal for telemetry (firebase | anonymous)
 *
 * H.5 names the connect-frame mechanism as a Wave 2 schema decision; we
 * implement the envelope-after-connect path because the WebSocket handshake
 * subprotocol surface is awkward for a long JWT (chrome rejects oversize
 * headers). Surfaced as OQ-0123-AUTH-TOKEN-HANDSHAKE-VS-ENVELOPE.
 */
export interface AuthTokenPayload {
  id_token: string;
  provider: "firebase" | "anonymous";
  /**
   * job-0172 Part C — sticky anonymous user_id hint. When ``id_token`` is
   * empty (anonymous fallback) the agent consults this field; if it carries
   * a ULID matching an existing anonymous ``UserDocument``, the same User
   * is re-bound and the user's Cases stay reachable. Ignored entirely when
   * ``id_token`` verifies (the JWT is the credential). The server-side
   * shape on ``AuthTokenEnvelope`` uses the name ``token`` not ``id_token``
   * because that is the pydantic field; the wire envelope translation in
   * ``maybeSendAuthToken`` below converts.
   */
  anonymous_user_id?: string | null;
}

/**
 * Token retrieval seam — injectable so unit tests don't need a real Firebase
 * Auth subsystem. Defaults to `getIdToken()` from `./auth`.
 */
export type IdTokenGetter = () => Promise<string | null>;

export type ConnectionStatus =
  | "connecting"
  | "connected"
  | "disconnected"
  | "reconnecting";

export interface WsHandlers {
  onStatus: (s: ConnectionStatus) => void;
  onAgentChunk: (p: AgentMessageChunkPayload) => void;
  onPipelineState: (p: PipelineStatePayload) => void;
  onSessionState: (p: SessionStatePayload) => void;
  onError: (p: ErrorPayload) => void;
  // OQ-0068-MAPCMD-WS: production routing for map-command envelopes (job-0072).
  // Optional so existing callers (App.tsx, Chat.tsx) need no change; callers that
  // own a LayerPanelBus should pass `onMapCommand: (p) => bus.pushMapCommand(p)`.
  onMapCommand?: (p: MapCommandPayload) => void;
  /**
   * Per-Case secrets list (job-0125, sprint-12-mega Wave 2 — SRS §F.3).
   * Optional so existing callers don't need to change; SecretsPanel mount
   * paths wire this to push payloads into a SecretsBus subscription.
   */
  onSecretsList?: (p: SecretsListPayload) => void;
  /**
   * Tool payload-warning envelope (job-0127, sprint-12-mega Wave 2). Optional
   * so chat-only callers can ignore. Chat.tsx mounts the inline warning card
   * by subscribing here and emits the matching `tool-payload-confirmation`
   * via {@link GraceWs.sendPayloadConfirmation}.
   */
  onPayloadWarning?: (p: PayloadWarningEnvelopePayload) => void;
  /**
   * Mode 2 candidate envelope (job-0126, sprint-12-mega Wave 2). Optional so
   * existing callers (Chat.tsx) don't need to change. App.tsx wires this into
   * the Mode2OfferModal subscription bus.
   */
  onMode2Candidate?: (p: Mode2CandidatePayload) => void;
  /**
   * Case-list envelope (job-0137, sprint-12-mega Wave 3 — FR-MP-6). Optional
   * so chat-only callers can ignore. CasesPanel mount wires this to refresh
   * the left-rail list.
   */
  onCaseList?: (p: CaseListEnvelopePayload) => void;
  /**
   * Case-open envelope (job-0137, sprint-12-mega Wave 3 — FR-MP-6). Optional
   * so chat-only callers can ignore. App.tsx wires this to drive Case state
   * machine: hydrate chat + loaded_layers + map_view on open; clear cleanly
   * when session_state is null.
   */
  onCaseOpen?: (p: CaseOpenEnvelopePayload) => void;
  /**
   * Auth-token retriever (job-0123). Optional — when absent we fall back to
   * `getIdToken()` from `./auth` directly. Injected by tests to avoid
   * dynamic-importing Firebase.
   */
  idTokenGetter?: IdTokenGetter;
  /**
   * job-0172 Part C — auth-ack handler. Fires once per WebSocket connect
   * after the server has either verified the Firebase ID token OR fallen
   * through to the H.3 anonymous fallback. Optional so existing callers
   * don't need to opt in; ws.ts always persists the sticky anonymous
   * user_id internally regardless. Consumers (App.tsx) can use it to
   * drive auth-aware UI without a separate round-trip.
   */
  onAuthAck?: (p: AuthAckPayload) => void;
}

const SESSION_KEY = "grace2.session_id";
// job-0172 Part C — sticky anonymous user_id. The server's H.3 anonymous
// fallback mints a fresh ULID on every connect; without a client-side cache,
// reconnects (browser refresh, WS drop + reconnect) orphan the user's Cases
// because the new connection binds to a different user_id. We persist the
// auth-ack's user_id when ``is_anonymous=true`` and replay it on the next
// auth-token envelope as a hint; the agent re-binds the same User record.
//
// Cleared by ``clearAnonymousUserId()`` after a real sign-in lands (the
// authenticated identity takes over and the anonymous hint is moot).
const ANONYMOUS_USER_ID_KEY = "grace2.anonymous_user_id";

function loadOrCreateSessionId(): string {
  try {
    const cached = window.localStorage.getItem(SESSION_KEY);
    if (cached && cached.length === 26) return cached;
  } catch {
    // localStorage may be disabled (privacy mode)
  }
  const id = newUlid();
  try {
    window.localStorage.setItem(SESSION_KEY, id);
  } catch {
    // ignore
  }
  return id;
}

/** job-0172 Part C — read the persisted anonymous user_id hint, if any. */
export function readAnonymousUserId(): string | null {
  try {
    const v = window.localStorage.getItem(ANONYMOUS_USER_ID_KEY);
    if (v && v.length === 26) return v;
    return null;
  } catch {
    return null;
  }
}

/** job-0172 Part C — store the assigned anonymous user_id hint. */
export function writeAnonymousUserId(userId: string): void {
  try {
    if (userId && userId.length === 26) {
      window.localStorage.setItem(ANONYMOUS_USER_ID_KEY, userId);
    }
  } catch {
    // ignore
  }
}

/** job-0172 Part C — wipe the cached anonymous user_id (e.g. on sign-in). */
export function clearAnonymousUserId(): void {
  try {
    window.localStorage.removeItem(ANONYMOUS_USER_ID_KEY);
  } catch {
    // ignore
  }
}

/**
 * job-0172 Part C — Wire shape for ``auth-ack`` (server -> client).
 *
 * The agent sends this exactly once after WebSocket connect — either after
 * verifying a Firebase ID token OR after the H.3 anonymous fallback. We
 * read ``is_anonymous`` + ``user_id`` to persist the sticky anonymous
 * identity (the server mints a fresh anonymous user every connect
 * otherwise, orphaning the user's Cases on every refresh).
 *
 * The full ack shape lives in ``packages/contracts/.../auth.py``
 * (``AuthAckEnvelope``); this is the minimal subset ws.ts needs to drive
 * the persistence side-effect. Extra fields (``firebase_uid``, ``tier``)
 * are passed through to ``onAuthAck`` consumers but not used by ws.ts
 * itself.
 */
export interface AuthAckPayload {
  user_id: string;
  firebase_uid?: string | null;
  is_anonymous: boolean;
  tier?: "free" | "pro" | "enterprise";
}

// ---------------------------------------------------------------------------
// job-0159: per-session fan-out hub for envelopes that drive shared UI state.
//
// Problem this solves: the agent's `PipelineEmitter` is bound 1:1 to a single
// `ServerConnection` (see services/agent/src/grace2_agent/server.py:1180-1188).
// When the user types a message, the tool runs on the WebSocket that
// delivered the `user-message` — and the resulting `session-state`,
// `map-command`, `case-list`, `case-open`, `secrets-list`, `mode2-candidate`,
// and `tool-payload-warning` envelopes go out ONLY on that wire. But the
// web client mounts TWO `GraceWs` instances per tab — Chat.tsx (chat panel)
// and App.tsx (map + layer panel + secrets + cases) — each with its own
// connection. Pre-job-0159 the App-side instance never saw the workflow's
// session-state, so the flood-depth raster never reached MapLibre even
// though `add_loaded_layer` had fired server-side.
//
// Fix: keep one socket each, but fan out the SESSION-SCOPED envelope types
// in-process across all `GraceWs` instances that share the same
// `session_id`. Message-level envelopes (`agent-message-chunk`,
// `pipeline-state`, `error`) are NOT fanned out — those follow the
// user-message that originated them and routing them across instances
// would duplicate chat messages and pipeline cards.
//
// The hub is a passive event bus; subscribers are existing `GraceWs`
// instances. Registration is automatic in the constructor; unregistration
// is automatic in `close()`. Listeners deliver to their bound handlers
// only — there is no observer-of-observers pattern.
// ---------------------------------------------------------------------------

/** Envelope types that carry session-scoped state and therefore need fan-out. */
const SESSION_SCOPED_TYPES = new Set<string>([
  "session-state",
  "map-command",
  "case-list",
  "case-open",
  "secrets-list",
  "mode2-candidate",
  "tool-payload-warning",
]);

const SESSION_HUB: Map<string, Set<GraceWs>> = new Map();

function hubRegister(ws: GraceWs, sessionId: string): void {
  let set = SESSION_HUB.get(sessionId);
  if (!set) {
    set = new Set();
    SESSION_HUB.set(sessionId, set);
  }
  set.add(ws);
}

function hubUnregister(ws: GraceWs, sessionId: string): void {
  const set = SESSION_HUB.get(sessionId);
  if (!set) return;
  set.delete(ws);
  if (set.size === 0) SESSION_HUB.delete(sessionId);
}

function hubBroadcast(
  fromWs: GraceWs,
  sessionId: string,
  envType: string,
  payload: unknown,
): void {
  const set = SESSION_HUB.get(sessionId);
  if (!set) return;
  for (const peer of set) {
    if (peer === fromWs) continue;
    peer.deliverFannedOut(envType, payload);
  }
}

// Exposed for tests (Vitest). Production code does not call these directly.
export function __test_resetSessionHub(): void {
  SESSION_HUB.clear();
}
export function __test_sessionHubSize(sessionId: string): number {
  return SESSION_HUB.get(sessionId)?.size ?? 0;
}

export class GraceWs {
  private url: string;
  private handlers: WsHandlers;
  private socket: WebSocket | null = null;
  private sessionId: string;
  private backoffMs = 500;
  private readonly maxBackoffMs = 5000;
  private reconnectTimer: number | null = null;
  private closedByUser = false;

  constructor(url: string, handlers: WsHandlers) {
    this.url = url;
    this.handlers = handlers;
    this.sessionId = loadOrCreateSessionId();
    // job-0159: register with the per-session fan-out hub so envelopes
    // received by sibling GraceWs instances (e.g. App's instance when the
    // tool ran on Chat's instance) are still delivered to OUR handlers.
    hubRegister(this, this.sessionId);
  }

  /** Current session ULID; survives page reload via localStorage. */
  get session(): string {
    return this.sessionId;
  }

  connect(): void {
    this.closedByUser = false;
    this.openSocket("connecting");
  }

  close(): void {
    this.closedByUser = true;
    if (this.reconnectTimer !== null) {
      window.clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.socket) {
      try {
        this.socket.close();
      } catch {
        // ignore
      }
      this.socket = null;
    }
    // job-0159: drop our hub registration so a re-mount doesn't leak.
    hubUnregister(this, this.sessionId);
    this.handlers.onStatus("disconnected");
  }

  /**
   * job-0159: deliver a session-scoped envelope that originated on a
   * SIBLING `GraceWs` instance for the same `session_id`. Called by the
   * fan-out hub; never invoked directly. Routes through the same handler
   * fan-out as a natively-received envelope so subscribers can't tell the
   * difference, which is the whole point.
   */
  deliverFannedOut(envType: string, payload: unknown): void {
    this.dispatchEnvelope(envType, payload);
  }

  sendUserMessage(text: string, researchMode: ResearchMode = "research"): void {
    const payload: UserMessagePayload = {
      text,
      research_mode: researchMode,
    };
    const env: Envelope<UserMessagePayload> = envelope(
      "user-message",
      this.sessionId,
      payload,
    );
    this.sendEnvelope(env);
  }

  sendCancel(reason: string | null = null): void {
    const payload: CancelPayload = { reason };
    const env: Envelope<CancelPayload> = envelope(
      "cancel",
      this.sessionId,
      payload,
    );
    this.sendEnvelope(env);
  }

  /**
   * Emit a `secret-add` envelope (job-0125 / SRS §F.3).
   *
   * Carries the transient `key_value` to the agent service; the server
   * writes the key to the vault on receipt and clears the field before
   * any logging / persistence. The web client does NOT echo or persist
   * the key value anywhere — SecretsPanel clears its form state
   * immediately after calling this method.
   */
  sendSecretAdd(args: {
    provider: ProviderID;
    case_id: string | null;
    label: string | null;
    key_value: string;
  }): void {
    const payload: SecretAddPayload = {
      envelope_type: "secret-add",
      provider: args.provider,
      case_id: args.case_id,
      label: args.label,
      key_value: args.key_value,
    };
    const env: Envelope<SecretAddPayload> = envelope(
      "secret-add",
      this.sessionId,
      payload,
    );
    this.sendEnvelope(env);
  }

  /**
   * Emit a `secret-revoke` envelope (job-0125 / SRS §F.3).
   *
   * Soft-revoke — the server flips `is_active=False` on the matching
   * SecretRecord but does NOT delete the vault entry (audit-trail
   * preservation). The response is a fresh `secrets-list` envelope.
   */
  sendSecretRevoke(secretId: string): void {
    const payload: SecretRevokePayload = {
      envelope_type: "secret-revoke",
      secret_id: secretId,
    };
    const env: Envelope<SecretRevokePayload> = envelope(
      "secret-revoke",
      this.sessionId,
      payload,
    );
    this.sendEnvelope(env);
  }

  /**
   * Emit a `tool-payload-confirmation` envelope (job-0127, sprint-12-mega Wave 2).
   *
   * Returns the user's decision on the inline payload-warning card to the
   * agent's paused dispatch coroutine. `decision="narrow_scope"` REQUIRES
   * `revisedArgs` (a dict — may be the agent's `alternative_args` echoed back
   * or a user-edited variant). `proceed` and `cancel` MUST NOT carry
   * `revisedArgs` — the contract validator on the agent side rejects them.
   */
  sendPayloadConfirmation(
    warningId: string,
    decision: PayloadConfirmationDecision,
    revisedArgs: Record<string, unknown> | null = null,
  ): void {
    const payload: PayloadConfirmationEnvelopePayload = {
      envelope_type: "tool-payload-confirmation",
      warning_id: warningId,
      decision,
      revised_args: decision === "narrow_scope" ? revisedArgs ?? {} : null,
    };
    const env: Envelope<PayloadConfirmationEnvelopePayload> = envelope(
      "tool-payload-confirmation",
      this.sessionId,
      payload,
    );
    this.sendEnvelope(env);
  }

  /**
   * Emit a `mode2-add-confirmed` envelope (job-0126, sprint-12-mega Wave 2).
   *
   * Sent when the user clicks "Add to Mode 2 catalog" on Mode2OfferModal.
   * The agent-side receiver shape is NOT YET REGISTERED in
   * packages/contracts/.../ws.py (kickoff §1 explicitly notes "define in
   * Wave 1.5 ws.py registry if not present — surface as OQ if missing");
   * tracked as OQ-0126-MODE2-ADD-CONFIRMED-SCHEMA. The payload mirrors the
   * minimal subset of `Mode2Candidate` the server needs to (a) correlate to
   * the originating audit-log entry by candidate_id and (b) hand off to the
   * heavier `offer-catalog-addition` flow (sprint-08).
   */
  sendMode2AddConfirmed(args: {
    candidate_id: string;
    url: string;
    domain: string;
    suggested_tool_kind: Mode2SuggestedKind;
  }): void {
    const payload: Mode2AddConfirmedPayload = {
      envelope_type: "mode2-add-confirmed",
      candidate_id: args.candidate_id,
      url: args.url,
      domain: args.domain,
      suggested_tool_kind: args.suggested_tool_kind,
    };
    const env: Envelope<Mode2AddConfirmedPayload> = envelope(
      "mode2-add-confirmed",
      this.sessionId,
      payload,
    );
    this.sendEnvelope(env);
  }

  /**
   * Emit a `case-command` envelope (job-0137, sprint-12-mega Wave 3 — FR-MP-6).
   *
   * Sent when the user creates / selects / renames / archives / deletes a
   * Case via CasesPanel. `case_id` is REQUIRED for every command except
   * `create` (the server generates the ULID on create). `args` is
   * command-specific:
   *
   *   - create:  optional { title: "..." } hint (defaults to "Untitled Case"
   *              server-side).
   *   - rename:  required { title: "<new title>" }.
   *   - select / archive / delete: ignored (empty {} is fine).
   *
   * The server response is `case-open` (create / select) or `case-list`
   * (rename / archive / delete) — both arrive on the existing handlers above.
   *
   * Invariant 9 (no cost theater): no cost / quota / quote field. Invariant 8
   * (cancellation): cancellation of an in-flight tool flows through the
   * existing `cancel` envelope, not a case-command.
   */
  sendCaseCommand(
    command: CaseCommand,
    caseId: string | null = null,
    args: Record<string, unknown> = {},
  ): void {
    const payload: CaseCommandEnvelopePayload = {
      envelope_type: "case-command",
      command,
      case_id: caseId,
      args,
    };
    const env: Envelope<CaseCommandEnvelopePayload> = envelope(
      "case-command",
      this.sessionId,
      payload,
    );
    this.sendEnvelope(env);
  }

  /**
   * Emit a `mode2-audit-event` envelope (job-0126, sprint-12-mega Wave 2).
   *
   * Fired on every Mode2OfferModal display + user action so the server
   * audit-log captures the full lifecycle (display-modal, display-toast,
   * add, dismiss, suppress). Server-side persistence is
   * OQ-0126-AUDIT-PERSISTENCE — the agent's default-branch
   * console.debug suffices until schema promotes it.
   */
  sendMode2AuditEvent(payload: Mode2AuditEventPayload): void {
    const full: Mode2AuditEventPayload = {
      envelope_type: "mode2-audit-event",
      ...payload,
    };
    const env: Envelope<Mode2AuditEventPayload> = envelope(
      "mode2-audit-event",
      this.sessionId,
      full,
    );
    this.sendEnvelope(env);
  }

  private openSocket(initialStatus: ConnectionStatus): void {
    this.handlers.onStatus(initialStatus);
    let ws: WebSocket;
    try {
      ws = new WebSocket(this.url);
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.socket = ws;
    ws.addEventListener("open", () => {
      this.backoffMs = 500;
      this.handlers.onStatus("connected");
      // Resume the session (envelope carries the persisted id; payload empty).
      const resume: Envelope<SessionResumePayload> = envelope(
        "session-resume",
        this.sessionId,
        {} as SessionResumePayload,
      );
      this.sendEnvelope(resume);
      // Send the Firebase ID token if available (job-0123, SRS Appendix H.5).
      // If no token (Firebase disabled, signed-out, or fetch fails), we skip
      // the auth-token envelope and let the agent fall back to anonymous —
      // kickoff §4: "skip and let server fall back to anonymous."
      void this.maybeSendAuthToken();
    });
    ws.addEventListener("message", (ev) => this.handleMessage(ev.data));
    ws.addEventListener("close", () => {
      this.socket = null;
      if (this.closedByUser) return;
      this.scheduleReconnect();
    });
    ws.addEventListener("error", () => {
      // close will follow; let close handler schedule the reconnect.
    });
  }

  private handleMessage(raw: unknown): void {
    if (typeof raw !== "string") return;
    let parsed: unknown;
    try {
      parsed = JSON.parse(raw);
    } catch {
      return;
    }
    if (!parsed || typeof parsed !== "object") return;
    const env = parsed as { type?: unknown; payload?: unknown };
    if (typeof env.type !== "string" || typeof env.payload !== "object") return;
    const payload = env.payload as Record<string, unknown>;
    // job-0159: fan out session-scoped envelope types to sibling GraceWs
    // instances bound to the same session_id BEFORE dispatching locally.
    // Order doesn't matter for correctness (both deliveries are synchronous
    // and independent) but fanning out first keeps the cross-instance
    // arrival close in time to the local arrival, which is friendlier to
    // any UI ordering assumptions downstream.
    if (SESSION_SCOPED_TYPES.has(env.type)) {
      hubBroadcast(this, this.sessionId, env.type, payload);
    }
    this.dispatchEnvelope(env.type, payload);
  }

  /**
   * Dispatch a parsed envelope to the bound handlers. Extracted from
   * `handleMessage` so the job-0159 hub fan-out can deliver an envelope
   * received by a sibling instance through the same routing logic.
   */
  private dispatchEnvelope(envType: string, rawPayload: unknown): void {
    if (!rawPayload || typeof rawPayload !== "object") return;
    const payload = rawPayload as Record<string, unknown>;
    switch (envType) {
      case "agent-message-chunk":
        this.handlers.onAgentChunk(payload as unknown as AgentMessageChunkPayload);
        break;
      case "pipeline-state":
        this.handlers.onPipelineState(
          payload as unknown as PipelineStatePayload,
        );
        break;
      case "session-state":
        this.handlers.onSessionState(
          payload as unknown as SessionStatePayload,
        );
        break;
      case "error":
        this.handlers.onError(payload as unknown as ErrorPayload);
        break;
      case "map-command":
        // OQ-0068-MAPCMD-WS: production routing for map-command envelopes (job-0072).
        // Callers that own a LayerPanelBus pass `onMapCommand: (p) => bus.pushMapCommand(p)`.
        if (this.handlers.onMapCommand) {
          this.handlers.onMapCommand(payload as unknown as MapCommandPayload);
        }
        break;
      case "secrets-list":
        // job-0125: server -> client secrets list (§F.3). Optional handler so
        // chat-only callers can ignore. SecretsPanel mount wires it via the
        // SecretsBus subscription.
        if (this.handlers.onSecretsList) {
          this.handlers.onSecretsList(
            payload as unknown as SecretsListPayload,
          );
        }
        break;
      case "mode2-candidate":
        // job-0126: Mode 2 candidate envelope from the Wave 1 classifier
        // (services/agent/src/grace2_agent/mode2_classifier.py). App.tsx
        // wires this into the Mode2OfferModal subscription bus when mounted.
        if (this.handlers.onMode2Candidate) {
          this.handlers.onMode2Candidate(
            payload as unknown as Mode2CandidatePayload,
          );
        }
        break;
      case "case-list":
        // job-0137: FR-MP-6 Case left-rail refresh. CasesPanel subscribes
        // through App.tsx's useCases hook. Server emits on connect and after
        // every successful case-command (create / rename / archive / delete).
        if (this.handlers.onCaseList) {
          this.handlers.onCaseList(
            payload as unknown as CaseListEnvelopePayload,
          );
        }
        break;
      case "case-open":
        // job-0137: FR-MP-6 Case rehydration. App.tsx hydrates chat history,
        // loaded_layers, and map_view from session_state; null = empty state
        // (server couldn't rehydrate — Case archived/deleted between list+select).
        if (this.handlers.onCaseOpen) {
          this.handlers.onCaseOpen(
            payload as unknown as CaseOpenEnvelopePayload,
          );
        }
        break;
      case "tool-payload-warning":
        // job-0127: Tool payload-warning envelope. Chat.tsx subscribes and
        // renders an inline PayloadWarningInline card with the proceed /
        // cancel / narrow-scope options the agent advertised. The user's
        // decision rides back via sendPayloadConfirmation().
        if (this.handlers.onPayloadWarning) {
          this.handlers.onPayloadWarning(
            payload as unknown as PayloadWarningEnvelopePayload,
          );
        }
        break;
      case "auth-ack": {
        // job-0172 Part C — server's auth-ack confirms the resolved identity.
        // When ``is_anonymous=true`` we cache the assigned user_id so the
        // next reconnect can replay it as a hint and re-bind the same User.
        // When ``is_anonymous=false`` (real sign-in) we clear the cached
        // hint — the authenticated identity supersedes anything anonymous.
        const ack = payload as unknown as AuthAckPayload;
        if (ack && typeof ack.user_id === "string") {
          if (ack.is_anonymous === true) {
            writeAnonymousUserId(ack.user_id);
          } else {
            clearAnonymousUserId();
          }
        }
        if (this.handlers.onAuthAck) {
          this.handlers.onAuthAck(ack);
        }
        break;
      }
      default:
        // Ignores tool-call-*, location-resolved, and the pick-mode requests.
        // Logging only.
        // eslint-disable-next-line no-console
        console.debug("[ws] unhandled frame type:", envType);
    }
  }

  private sendEnvelope<P>(env: Envelope<P>): void {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) return;
    this.socket.send(JSON.stringify(env));
  }

  /**
   * Fetch the Firebase ID token (if any) and emit the `auth-token` envelope.
   *
   * Job-0123 / SRS H.5: when a token is available, the agent's
   * connection-acceptor verifies it via `firebase_admin.auth.verify_id_token`
   * and binds the resolved User to the session. When no token is available
   * (Firebase disabled / signed out / fetch failed), we skip the envelope
   * entirely — the agent's anonymous fallback handles the session.
   */
  private async maybeSendAuthToken(): Promise<void> {
    const getter = this.handlers.idTokenGetter ?? getIdToken;
    let token: string | null = null;
    try {
      token = await getter();
    } catch {
      // Treat any error as no-token (anonymous fallback). The Firebase SDK
      // can throw on network errors, expired refresh tokens, etc.
      token = null;
    }
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) return;
    // job-0172 Part C — always send the auth-token envelope (even with an
    // empty ``id_token``) so the agent receives the sticky
    // ``anonymous_user_id`` hint and re-binds the same anonymous User on
    // reconnect. Previously we returned early when ``token`` was null and
    // relied on the agent's implicit-anonymous fallback (which mints a
    // FRESH user_id every connect — the bug this part fixes).
    const stickyHint = token ? null : readAnonymousUserId();
    const payload: AuthTokenPayload = {
      id_token: token ?? "",
      provider: token ? "firebase" : "anonymous",
      anonymous_user_id: stickyHint,
    };
    const env: Envelope<AuthTokenPayload> = envelope(
      "auth-token",
      this.sessionId,
      payload,
    );
    this.sendEnvelope(env);
  }

  private scheduleReconnect(): void {
    this.handlers.onStatus("reconnecting");
    const delay = this.backoffMs;
    this.backoffMs = Math.min(this.backoffMs * 2, this.maxBackoffMs);
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null;
      this.openSocket("connecting");
    }, delay);
  }
}
