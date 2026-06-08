// GRACE-2 web — Chat panel with inline pipeline cards (FR-WC-7, FR-WC-8, FR-WC-9).
//
// Renders the streamed agent reply token-by-token from `agent-message-chunk`
// deltas (Appendix A.4, replace-not-reconcile semantics on `done: true`).
// Multi-line input with Ctrl/Cmd+Enter submit. No markdown for M1 (M3
// adds markdown + tool-call blocks).
//
// PIPELINE CARDS INLINE (job-0064):
//   Pipeline step cards are rendered at the bottom of the conversation stream
//   while a pipeline is in flight. On completion they scroll into history and
//   show a terminal visual (✓/✗/⊘) without the percentage. Cards are stacked
//   in the order steps appear in the `pipeline-state` snapshot (snapshot order
//   == call order per Appendix A.7 replace-not-reconcile: the server always
//   sends the full ordered step list).
//
// CANCEL PREDICATE (FR-WC-9, Invariant 8):
//   Cancel button enabled iff:
//     (a) last pipeline-state has at least one step in `running` state, OR
//     (b) last session-state.current_pipeline is non-null.
//   These are on different envelopes — union of both conditions.
//
// The Chat panel creates its own GraceWs and handles ALL envelope types:
// agent-message-chunk, pipeline-state, session-state, and error.
//
// The chat is a CONSUMER of frames — every glyph on screen came from the
// agent. No client-side text generation.

import { useEffect, useReducer, useRef, useState } from "react";
import { ConnectionStatus, GraceWs } from "./ws";
import {
  AgentMessageChunkPayload,
  ErrorPayload,
  PipelineSnapshot,
  PipelineStatePayload,
  PipelineStepSummary,
  ResearchMode,
  SessionStatePayload,
} from "./contracts";
import { PipelineCard } from "./components/PipelineCard";
import { ChatInput, ChatInputState } from "./components/ChatInput";

// --- Chat message shape -------------------------------------------------- //

interface ChatMessage {
  id: string;        // message_id from agent-message-chunk (or "user-<n>" for user lines)
  role: "user" | "agent";
  text: string;
  done: boolean;
}

// --- Pipeline inline state ----------------------------------------------- //
//
// Tracks the replace-not-reconcile pipeline view-model inside Chat.
// Appendix A.7: each new `pipeline-state` envelope WHOLESALE REPLACES the
// prior view. Never merge or diff deltas.
//
// `history` accumulates completed snapshots so they remain visible in the
// chat history after the pipeline terminates.

interface PipelineInlineState {
  // The current live snapshot (null = no pipeline active).
  live: PipelineStatePayload | null;
  // Snapshots that have reached a terminal state (all steps terminal).
  // Appended when a live snapshot transitions to terminal; live resets to null.
  history: PipelineStatePayload[];
  // From session-state.current_pipeline — used for the cancel predicate (b).
  currentPipelineFromSession: PipelineSnapshot | null;
}

type PipelineAction =
  | { type: "pipeline-state"; payload: PipelineStatePayload }
  | { type: "session-state"; payload: SessionStatePayload };

function narrowCurrentPipeline(x: unknown): PipelineSnapshot | null {
  if (x === null || x === undefined) return null;
  if (typeof x !== "object") return null;
  const o = x as Record<string, unknown>;
  if (typeof o.pipeline_id !== "string") return null;
  const steps = Array.isArray(o.steps) ? (o.steps as PipelineStepSummary[]) : [];
  return {
    pipeline_id: o.pipeline_id,
    started_at: typeof o.started_at === "string" ? o.started_at : null,
    completed_at: typeof o.completed_at === "string" ? o.completed_at : null,
    final_state:
      o.final_state === "complete" ||
      o.final_state === "failed" ||
      o.final_state === "cancelled"
        ? o.final_state
        : null,
    steps,
  };
}

function pipelineReducer(
  state: PipelineInlineState,
  action: PipelineAction,
): PipelineInlineState {
  switch (action.type) {
    case "pipeline-state": {
      // REPLACE-NOT-RECONCILE (Appendix A.7).
      const steps = action.payload.steps ?? [];
      // Terminal = every step in a terminal state (and at least one step).
      const isTerminal =
        steps.length > 0 &&
        steps.every(
          (s) =>
            s.state === "complete" ||
            s.state === "failed" ||
            s.state === "cancelled",
        );

      // If this is a different pipeline than the live one, archive live first.
      const prevLive = state.live;
      const isDifferentPipeline =
        prevLive !== null &&
        prevLive.pipeline_id !== action.payload.pipeline_id;

      let history = state.history;
      if (isDifferentPipeline && prevLive !== null) {
        history = [...history, prevLive];
      }

      if (isTerminal) {
        // Terminal snapshot → move to history, clear live.
        return {
          ...state,
          live: null,
          history: [...history, action.payload],
          currentPipelineFromSession: null,
        };
      }

      return { ...state, live: action.payload, history };
    }
    case "session-state": {
      const cp = narrowCurrentPipeline(action.payload.current_pipeline);
      return { ...state, currentPipelineFromSession: cp };
    }
    default:
      return state;
  }
}

// Export for testing.
export function shouldShowCancel(state: PipelineInlineState): boolean {
  // (a) pipeline-state: any step running?
  const aRunning = state.live?.steps?.some((s) => s.state === "running") ?? false;
  // (b) session-state: current_pipeline non-null?
  const bSession = state.currentPipelineFromSession !== null;
  return aRunning || bSession;
}

// --- Props --------------------------------------------------------------- //

export interface ChatProps {
  wsUrl: string;
  /** Called when the user clicks the × close button (job-0068). */
  onClose?: () => void;
}

// --- Connection status display ------------------------------------------- //

const STATUS_LABEL: Record<ConnectionStatus, string> = {
  connecting: "connecting",
  connected: "connected",
  disconnected: "disconnected",
  reconnecting: "reconnecting",
};

const STATUS_COLOR: Record<ConnectionStatus, string> = {
  connecting: "#aa8",
  connected: "#5a5",
  disconnected: "#c33",
  reconnecting: "#d80",
};

// --- Component ----------------------------------------------------------- //

export function Chat({ wsUrl, onClose }: ChatProps): JSX.Element {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [pipeline, dispatchPipeline] = useReducer(pipelineReducer, {
    live: null,
    history: [],
    currentPipelineFromSession: null,
  });
  const [status, setStatus] = useState<ConnectionStatus>("connecting");
  const [researchMode] = useState<ResearchMode>("research"); // toggle UI lands M3
  const [lastError, setLastError] = useState<string | null>(null);

  const wsRef = useRef<GraceWs | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const ws = new GraceWs(wsUrl, {
      onStatus: (s) => setStatus(s),
      onAgentChunk: (p: AgentMessageChunkPayload) => {
        setMessages((prev) => appendDelta(prev, p));
      },
      onPipelineState: (p: PipelineStatePayload) => {
        dispatchPipeline({ type: "pipeline-state", payload: p });
      },
      onSessionState: (p: SessionStatePayload) => {
        dispatchPipeline({ type: "session-state", payload: p });
      },
      onError: (p: ErrorPayload) => {
        setLastError(`${p.error_code}: ${p.message}`);
      },
    });
    wsRef.current = ws;
    ws.connect();
    return () => ws.close();
  }, [wsUrl]);

  // Dev-only seam: expose pipeline-state injection so the browser console /
  // Playwright scripts can drive the inline cards without a live agent.
  // Registered here (inside Chat) so it dispatches directly to the same
  // dispatchPipeline function that the live WS uses.
  useEffect(() => {
    if (!import.meta.env.DEV) return;
    window.__grace2InjectPipelineState = (p) =>
      dispatchPipeline({ type: "pipeline-state", payload: p });
    return () => {
      delete window.__grace2InjectPipelineState;
    };
  }, []);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, pipeline]);

  function submit(text: string): void {
    if (!text || !wsRef.current) return;
    setMessages((prev) => [
      ...prev,
      { id: `user-${prev.length}`, role: "user", text, done: true },
    ]);
    wsRef.current.sendUserMessage(text, researchMode);
    setLastError(null);
  }

  function cancel(): void {
    wsRef.current?.sendCancel("user-cancel");
  }

  const showCancel = shouldShowCancel(pipeline);
  const liveSteps = pipeline.live?.steps ?? [];
  // Merged send/stop control: in-flight whenever the cancel predicate fires
  // (any running step in the live pipeline, OR a non-null
  // session-state.current_pipeline). Returns to idle on terminal /
  // cancelled pipeline-state per the existing pipelineReducer.
  const inputState: ChatInputState = showCancel ? "in-flight" : "idle";
  const inputDisabled = status !== "connected";

  return (
    <div
      data-testid="grace2-chat"
      style={{
        position: "absolute",
        right: 16,
        top: 16,
        bottom: 16,
        width: 380,
        background: "rgba(20,20,25,0.92)",
        color: "#eee",
        borderRadius: 8,
        boxShadow: "0 4px 24px rgba(0,0,0,0.4)",
        display: "flex",
        flexDirection: "column",
        fontFamily: "system-ui, sans-serif",
        fontSize: 13,
        overflow: "hidden",
      }}
    >
      <header
        style={{
          padding: "10px 12px",
          borderBottom: "1px solid #333",
          display: "flex",
          alignItems: "center",
          gap: 8,
        }}
      >
        <strong style={{ fontSize: 14 }}>GRACE-2</strong>
        <span style={{ color: "#888", fontSize: 11 }}>M1 stub</span>
        <span style={{ flex: 1 }} />
        <span
          data-testid="connection-status"
          title={`WebSocket ${STATUS_LABEL[status]}`}
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            fontSize: 11,
            color: STATUS_COLOR[status],
          }}
        >
          <span
            style={{
              width: 8,
              height: 8,
              borderRadius: 4,
              background: STATUS_COLOR[status],
              display: "inline-block",
            }}
          />
          {STATUS_LABEL[status]}
        </span>
        {onClose && (
          <button
            data-testid="grace2-chat-close"
            aria-label="Close chat panel"
            onClick={onClose}
            style={{
              background: "none",
              border: "none",
              color: "#888",
              cursor: "pointer",
              fontSize: 16,
              lineHeight: 1,
              padding: "0 2px",
              display: "flex",
              alignItems: "center",
            }}
          >
            ×
          </button>
        )}
      </header>

      {/* ---- Scrollable conversation area ----                                   */}
      {/* job-0144: bottom-padding leaves room for the overlay ChatInput so       */}
      {/* messages aren't hidden behind it. The input grows from ~68px → ~40vh;   */}
      {/* the padding here is sized for the idle state and the input floats over  */}
      {/* the bottom of the scroll when it grows (Kickoff Part 4 — overlay,       */}
      {/* don't displace content).                                                */}
      <div
        ref={scrollRef}
        style={{
          flex: 1,
          overflowY: "auto",
          padding: "12px 12px 88px 12px",
          display: "flex",
          flexDirection: "column",
          gap: 10,
        }}
      >
        {messages.length === 0 &&
          liveSteps.length === 0 &&
          pipeline.history.length === 0 && (
            <p style={{ color: "#888", margin: 0 }}>
              Ask a question. Ctrl/Cmd+Enter to send.
            </p>
          )}

        {/* Chat messages */}
        {messages.map((m) => (
          <div
            key={m.id}
            data-role={m.role}
            data-done={m.done ? "true" : "false"}
            style={{
              alignSelf: m.role === "user" ? "flex-end" : "flex-start",
              maxWidth: "85%",
              background: m.role === "user" ? "#264" : "#222",
              padding: "8px 10px",
              borderRadius: 6,
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
            }}
          >
            {m.text}
            {!m.done && m.role === "agent" && (
              <span style={{ color: "#888" }}> ▌</span>
            )}
          </div>
        ))}

        {/* Historical pipeline snapshots (terminal) — scroll into history. */}
        {pipeline.history.map((snapshot) => (
          <PipelineStepGroup
            key={snapshot.pipeline_id}
            pipelineId={snapshot.pipeline_id}
            steps={snapshot.steps ?? []}
            isLive={false}
          />
        ))}

        {/* Live pipeline steps — stays at bottom while in flight. */}
        {pipeline.live !== null && liveSteps.length > 0 && (
          <PipelineStepGroup
            pipelineId={pipeline.live.pipeline_id}
            steps={liveSteps}
            isLive={true}
          />
        )}

        {lastError && (
          <div
            data-testid="ws-error"
            style={{
              color: "#f88",
              fontSize: 12,
              border: "1px solid #533",
              padding: 6,
              borderRadius: 4,
            }}
          >
            error: {lastError}
          </div>
        )}
      </div>

      {/* ---- Overlay input wrapper (job-0144) ----                              */}
      {/* Floats at the bottom of the chat panel; the scroll above has matching   */}
      {/* bottom-padding so messages aren't hidden behind it. The merged          */}
      {/* send/stop control lives inside ChatInput — there is no separate         */}
      {/* displayed Cancel button per Kickoff Part 6.                             */}
      <div
        data-testid="chat-input-overlay"
        style={{
          position: "absolute",
          left: 12,
          right: 12,
          bottom: 12,
          pointerEvents: "auto",
        }}
      >
        <ChatInput
          state={inputState}
          onSubmit={submit}
          onCancel={cancel}
          disabled={inputDisabled}
        />
      </div>
    </div>
  );
}

// --- Pipeline step group ------------------------------------------------- //
//
// Renders a labelled group of PipelineCards for one snapshot.

interface PipelineStepGroupProps {
  pipelineId: string;
  steps: PipelineStepSummary[];
  isLive: boolean;
}

function PipelineStepGroup({
  pipelineId,
  steps,
  isLive,
}: PipelineStepGroupProps): JSX.Element {
  return (
    <div
      data-testid="pipeline-step-group"
      data-pipeline-id={pipelineId}
      data-live={isLive ? "true" : "false"}
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 3,
        padding: "6px 0",
        borderTop: "1px solid #2a2a2a",
      }}
    >
      <div
        style={{
          fontSize: 10,
          color: "#555",
          letterSpacing: "0.05em",
          textTransform: "uppercase",
          paddingLeft: 6,
          marginBottom: 2,
        }}
      >
        {isLive ? "running" : "completed"} · {pipelineId.slice(-8)}
      </div>
      {steps.map((step) => (
        <PipelineCard key={step.step_id} step={step} />
      ))}
    </div>
  );
}

// --- Pure helpers -------------------------------------------------------- //

// Apply an agent-message-chunk delta to the message list.
// `agent-message-chunk.delta` is incremental per A.4 (not accumulated); we
// append by `message_id` and finalize on `done: true`.
function appendDelta(
  prev: ChatMessage[],
  p: AgentMessageChunkPayload,
): ChatMessage[] {
  const idx = prev.findIndex((m) => m.id === p.message_id);
  if (idx === -1) {
    return [
      ...prev,
      {
        id: p.message_id,
        role: "agent",
        text: p.delta,
        done: p.done === true,
      },
    ];
  }
  const existing = prev[idx]!;
  const updated: ChatMessage = {
    ...existing,
    text: existing.text + p.delta,
    done: existing.done || p.done === true,
  };
  const next = prev.slice();
  next[idx] = updated;
  return next;
}
