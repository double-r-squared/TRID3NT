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
  Envelope,
  ErrorPayload,
  MapCommandPayload,
  PipelineStatePayload,
  ResearchMode,
  SessionResumePayload,
  SessionStatePayload,
  UserMessagePayload,
  envelope,
  newUlid,
} from "./contracts";

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
}

const SESSION_KEY = "grace2.session_id";

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
    this.handlers.onStatus("disconnected");
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
    switch (env.type) {
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
      default:
        // Ignores tool-call-*, location-resolved, and the pick-mode requests.
        // Logging only.
        // eslint-disable-next-line no-console
        console.debug("[ws] unhandled frame type:", env.type);
    }
  }

  private sendEnvelope<P>(env: Envelope<P>): void {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) return;
    this.socket.send(JSON.stringify(env));
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
