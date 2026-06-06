// GRACE-2 web — Minimal chat panel (FR-WC-7 subset).
//
// Renders the streamed agent reply token-by-token from `agent-message-chunk`
// deltas (Appendix A.4, replace-not-reconcile semantics on `done: true`).
// Multi-line input with Ctrl/Cmd+Enter submit. No markdown for M1 (M3
// adds markdown + tool-call blocks).
//
// The chat is a CONSUMER of frames — every glyph on screen came from the
// agent. No client-side text generation.

import { KeyboardEvent, useEffect, useRef, useState } from "react";
import { ConnectionStatus, GraceWs } from "./ws";
import {
  AgentMessageChunkPayload,
  ErrorPayload,
  PipelineStatePayload,
  ResearchMode,
  SessionStatePayload,
} from "./contracts";

interface ChatMessage {
  id: string;        // message_id from agent-message-chunk (or "user-<n>" for user lines)
  role: "user" | "agent";
  text: string;
  done: boolean;
}

interface ChatProps {
  wsUrl: string;
}

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

export function Chat({ wsUrl }: ChatProps): JSX.Element {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [status, setStatus] = useState<ConnectionStatus>("connecting");
  const [researchMode] = useState<ResearchMode>("research"); // toggle UI lands M3
  const [lastError, setLastError] = useState<string | null>(null);
  const [pipelineSummary, setPipelineSummary] = useState<string | null>(null);
  const wsRef = useRef<GraceWs | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const ws = new GraceWs(wsUrl, {
      onStatus: (s) => setStatus(s),
      onAgentChunk: (p: AgentMessageChunkPayload) => {
        setMessages((prev) => appendDelta(prev, p));
      },
      onPipelineState: (p: PipelineStatePayload) => {
        const states = (p.steps ?? []).map((s) => `${s.name}:${s.state}`).join(", ");
        setPipelineSummary(`${p.pipeline_id} [${states}]`);
      },
      onSessionState: (_p: SessionStatePayload) => {
        // M3 will reconstruct chat/layers/pipeline; M1 just acknowledges.
      },
      onError: (p: ErrorPayload) => {
        setLastError(`${p.error_code}: ${p.message}`);
      },
    });
    wsRef.current = ws;
    ws.connect();
    return () => ws.close();
  }, [wsUrl]);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, pipelineSummary]);

  function submit(): void {
    const text = draft.trim();
    if (!text || !wsRef.current) return;
    setMessages((prev) => [
      ...prev,
      { id: `user-${prev.length}`, role: "user", text, done: true },
    ]);
    wsRef.current.sendUserMessage(text, researchMode);
    setDraft("");
    setLastError(null);
  }

  function onKey(e: KeyboardEvent<HTMLTextAreaElement>): void {
    // Ctrl+Enter (Linux/Windows) or Cmd+Enter (Mac) submits.
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      submit();
    }
  }

  function cancel(): void {
    wsRef.current?.sendCancel("user-cancel");
  }

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
      </header>
      <div
        ref={scrollRef}
        style={{
          flex: 1,
          overflowY: "auto",
          padding: "12px",
          display: "flex",
          flexDirection: "column",
          gap: 10,
        }}
      >
        {messages.length === 0 && (
          <p style={{ color: "#888", margin: 0 }}>
            Ask a question. Ctrl/Cmd+Enter to send.
          </p>
        )}
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
        {pipelineSummary && (
          <div
            data-testid="pipeline-summary"
            style={{ color: "#aaa", fontSize: 11 }}
          >
            pipeline: {pipelineSummary}
          </div>
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
      <footer
        style={{
          padding: 10,
          borderTop: "1px solid #333",
          display: "flex",
          gap: 6,
        }}
      >
        <textarea
          data-testid="chat-input"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKey}
          placeholder="Ctrl/Cmd+Enter to send"
          rows={2}
          style={{
            flex: 1,
            resize: "none",
            background: "#111",
            color: "#eee",
            border: "1px solid #333",
            borderRadius: 4,
            padding: 6,
            fontFamily: "inherit",
            fontSize: 13,
          }}
        />
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <button
            data-testid="chat-send"
            onClick={submit}
            disabled={!draft.trim() || status !== "connected"}
            style={{
              background: "#37a",
              color: "#fff",
              border: 0,
              borderRadius: 4,
              padding: "6px 10px",
              cursor: "pointer",
              fontSize: 12,
            }}
          >
            Send
          </button>
          <button
            data-testid="chat-cancel"
            onClick={cancel}
            disabled={status !== "connected"}
            style={{
              background: "#444",
              color: "#fff",
              border: 0,
              borderRadius: 4,
              padding: "6px 10px",
              cursor: "pointer",
              fontSize: 12,
            }}
          >
            Cancel
          </button>
        </div>
      </footer>
    </div>
  );
}

// Pure helper: apply an agent-message-chunk delta to the message list.
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
