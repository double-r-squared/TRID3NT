// GRACE-2 web — Chat panel with inline pipeline cards (FR-WC-7, FR-WC-8, FR-WC-9).
//
// Renders the streamed agent reply token-by-token from `agent-message-chunk`
// deltas (Appendix A.4, replace-not-reconcile semantics on `done: true`).
// Multi-line input with Ctrl/Cmd+Enter submit. No markdown for M1 (M3
// adds markdown + tool-call blocks).
//
// PIPELINE CARDS INLINE (job-0064; job-0162 single-card-per-step refactor):
//   Pipeline step cards are rendered inline in the conversation stream — one
//   card per unique `step_id`, transitioning through pending → running →
//   complete / failed / cancelled. The server emits one pipeline_id per tool
//   dispatch (see services/agent/server.py); previously this resulted in a
//   separate "group" per tool with a stale running card stacked above a
//   completed card. job-0162 collapses this by merging every snapshot by
//   step_id across both the live pipeline and historical pipelines, so the
//   user sees one transitioning card per tool dispatch. The visual states are
//   driven by PipelineCard per `feedback_pipeline_card_visual_states`.
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

import { useCallback, useEffect, useReducer, useRef, useState } from "react";
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
import { AgentMessage } from "./components/AgentMessage";
import { UserBubble } from "./components/UserBubble";
import { ScrollToBottom } from "./components/ScrollToBottom";

// job-0153 Part 4 — gap between input wrapper and the last chat message.
// Scroll-area bottom padding = inputHeight + INPUT_GAP_PX.
const INPUT_GAP_PX = 16;
// Default input wrapper height (single-line state) — used until the first
// onHeightChange callback fires from the mounted ChatInput.
const DEFAULT_INPUT_HEIGHT_PX = 68;
// job-0153 Part 3 — bottom-arrow appears when scrollTop is more than this
// many pixels above the bottom of the scroll container.
const SCROLL_BOTTOM_THRESHOLD_PX = 50;

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
  | { type: "session-state"; payload: SessionStatePayload }
  // job-0166 Part 1 — A.6 error envelope arrives without an accompanying
  // pipeline-state(failed) snapshot from the agent in the LLM_UNAVAILABLE /
  // tool-TypeError paths in server.py. The client must force-transition the
  // most-recent running step to failed so the rainbow animation stops and
  // the user sees a terminal RED card.
  | {
      type: "error";
      payload: ErrorPayload;
      tool_name?: string | null;
    };

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
    case "error": {
      // job-0166 Part 1 — find the most-recent running step across (live,
      // history). Preference: a step whose tool_name matches the error's
      // tool_name when supplied (forward-compatible — ErrorPayload doesn't
      // currently carry tool_name, but the agent may surface it as a future
      // amendment); fall back to the latest running step in encounter order.
      //
      // The chosen step is force-transitioned to `failed` with the
      // error_code + message attached so PipelineCard renders the typed RED
      // card with no spinner. Other steps are left alone (a failed tool
      // does not invalidate sibling completed steps in the same pipeline).
      return forceMostRecentRunningToFailed(
        state,
        action.payload,
        action.tool_name ?? null,
      );
    }
    default:
      return state;
  }
}

// --- Error → failed transition (job-0166 Part 1) ------------------------- //
//
// Walk every pipeline snapshot we currently render (history + live) in order;
// the LAST running step encountered (preferring a tool_name match) becomes
// the target. We rewrite the matching step in BOTH live and history so the
// mergeStepsByStepId pass renders the failure regardless of which snapshot
// the step's most-recent state lived in.

function rewriteStep(
  snap: PipelineStatePayload,
  step_id: string,
  next: PipelineStepSummary,
): PipelineStatePayload {
  return {
    ...snap,
    steps: (snap.steps ?? []).map((s) =>
      s.step_id === step_id ? next : s,
    ),
  };
}

export function forceMostRecentRunningToFailed(
  state: PipelineInlineState,
  err: ErrorPayload,
  tool_name: string | null,
): PipelineInlineState {
  // Collect every snapshot in order: history then live.
  const allSnapshots: PipelineStatePayload[] = [...state.history];
  if (state.live) allSnapshots.push(state.live);

  // First pass — tool_name match wins. Scan in reverse to prefer most-recent.
  let targetStepId: string | null = null;
  if (tool_name) {
    outer: for (let i = allSnapshots.length - 1; i >= 0; i--) {
      const snap = allSnapshots[i]!;
      for (let j = (snap.steps?.length ?? 0) - 1; j >= 0; j--) {
        const s = snap.steps![j]!;
        if (s.state === "running" && s.tool_name === tool_name) {
          targetStepId = s.step_id;
          break outer;
        }
      }
    }
  }
  // Second pass — any most-recent running step.
  if (targetStepId === null) {
    outer: for (let i = allSnapshots.length - 1; i >= 0; i--) {
      const snap = allSnapshots[i]!;
      for (let j = (snap.steps?.length ?? 0) - 1; j >= 0; j--) {
        const s = snap.steps![j]!;
        if (s.state === "running") {
          targetStepId = s.step_id;
          break outer;
        }
      }
    }
  }

  // Nothing to flip — leave the world alone.
  if (targetStepId === null) return state;

  // Build the failed replacement carrying the error_code + message so
  // PipelineCard renders the typed RED card with the chip + tooltip.
  const buildFailed = (
    prev: PipelineStepSummary,
  ): PipelineStepSummary => ({
    ...prev,
    state: "failed",
    error_code: err.error_code,
    error_message: err.message,
  });

  // Rewrite every snapshot containing the target step_id (defensive — the
  // step should be in at most one but mergeStepsByStepId tolerates duplicates).
  const nextHistory = state.history.map((snap) => {
    const hit = (snap.steps ?? []).find(
      (s) => s.step_id === targetStepId,
    );
    return hit ? rewriteStep(snap, targetStepId!, buildFailed(hit)) : snap;
  });
  let nextLive = state.live;
  if (nextLive) {
    const hit = (nextLive.steps ?? []).find(
      (s) => s.step_id === targetStepId,
    );
    if (hit) {
      nextLive = rewriteStep(nextLive, targetStepId, buildFailed(hit));
    }
  }
  return { ...state, history: nextHistory, live: nextLive };
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

  // job-0153 Part 4 — dynamic chat-input wrapper height; the scroll area's
  // bottom-padding grows with it so messages aren't clipped by the overlay.
  const [inputHeightPx, setInputHeightPx] = useState<number>(
    DEFAULT_INPUT_HEIGHT_PX,
  );

  // job-0153 Part 3 — visibility of the scroll-to-bottom button. Toggled on
  // every scroll event in the conversation area. Auto-scroll on new content
  // also re-evaluates this.
  const [scrollArrowVisible, setScrollArrowVisible] = useState<boolean>(false);

  // Track whether the user is "at bottom". When at bottom we auto-scroll on
  // new content; when scrolled up we leave the position alone (so the user's
  // reading position isn't disrupted) and surface the scroll-to-bottom arrow.
  const atBottomRef = useRef<boolean>(true);

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
        // job-0166 Part 1 — force the most-recent running step to failed so
        // the rainbow animation terminates and the user sees a RED card.
        // Sender envelope shape does not currently include tool_name; pass
        // null and rely on the most-recent-running fallback.
        dispatchPipeline({ type: "error", payload: p, tool_name: null });
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

  // job-0166 dev-only seam: inject an error envelope so Playwright can
  // verify Part 1 (running → failed force-transition on LLM_UNAVAILABLE /
  // tool TypeError) without a live agent failure.
  useEffect(() => {
    if (!import.meta.env.DEV) return;
    window.__grace2InjectError = (p) => {
      setLastError(`${p.error_code}: ${p.message}`);
      dispatchPipeline({ type: "error", payload: p, tool_name: null });
    };
    return () => {
      delete window.__grace2InjectError;
    };
  }, []);

  // Auto-scroll on new content only when the user is already at the bottom.
  // This preserves the user's reading position when they've scrolled up to
  // read history while the stream is still landing new tokens.
  useEffect(() => {
    if (scrollRef.current && atBottomRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, pipeline]);

  // job-0153 Part 3 — scroll handler. Computes "near bottom" against the
  // current scroll position and toggles the arrow visibility + the
  // atBottomRef latch used by the auto-scroll effect above.
  const handleScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const distanceFromBottom =
      el.scrollHeight - el.scrollTop - el.clientHeight;
    const nearBottom = distanceFromBottom <= SCROLL_BOTTOM_THRESHOLD_PX;
    atBottomRef.current = nearBottom;
    setScrollArrowVisible(!nearBottom);
  }, []);

  const scrollToBottom = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
    atBottomRef.current = true;
    setScrollArrowVisible(false);
  }, []);

  // Stable callback for ChatInput.onHeightChange so it doesn't fire the
  // measure useLayoutEffect on every Chat render.
  const handleInputHeightChange = useCallback((h: number) => {
    setInputHeightPx((prev) => (Math.abs(prev - h) < 0.5 ? prev : h));
  }, []);

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
            aria-label="Collapse chat panel"
            title="Collapse chat panel"
            onClick={onClose}
            style={{
              background: "none",
              border: "none",
              color: "#888",
              cursor: "pointer",
              fontSize: 18,
              lineHeight: 1,
              padding: "0 4px",
              display: "flex",
              alignItems: "center",
              fontFamily: "system-ui, sans-serif",
              fontWeight: 600,
            }}
          >
            {/* job-0162: chevron-right ("collapse panel" idiom) replaces ×    */}
            {/* ("close" idiom) — collapsing must NEVER imply destruction of    */}
            {/* the chat history. The persistence is implemented in App.tsx by */}
            {/* keeping <Chat /> mounted across collapse so its message state  */}
            {/* survives.                                                       */}
            ›
          </button>
        )}
      </header>

      {/* ---- Scrollable conversation area ----                                   */}
      {/* job-0153 Part 4: bottom-padding tracks the actual measured input        */}
      {/* wrapper height (plus a 16px gap) so the floating ChatInput overlay      */}
      {/* never clips the last message, payload-warning card, or source           */}
      {/* suggestion card — even when the textarea grows to ~40vh.                */}
      <div
        ref={scrollRef}
        data-testid="chat-scroll"
        onScroll={handleScroll}
        style={{
          flex: 1,
          overflowY: "auto",
          padding: `12px 12px ${inputHeightPx + INPUT_GAP_PX}px 12px`,
          display: "flex",
          flexDirection: "column",
          gap: 10,
        }}
      >
        {messages.length === 0 &&
          liveSteps.length === 0 &&
          pipeline.history.length === 0 && (
            <p style={{ color: "#888", margin: 0 }}>
              Ask a question. Press Enter to send.
            </p>
          )}

        {/* Chat messages — user as right-aligned grey bubble; agent as          */}
        {/* unaligned markdown block (job-0153 Parts 1 + 2).                      */}
        {messages.map((m) =>
          m.role === "user" ? (
            <UserBubble key={m.id} text={m.text} />
          ) : (
            <AgentMessage key={m.id} text={m.text} done={m.done} />
          ),
        )}

        {/* Pipeline cards — one per unique step_id across all snapshots          */}
        {/* (history + live). Each step transitions through pending → running   */}
        {/* → complete / failed / cancelled. job-0162: no separate "running"    */}
        {/* and "completed" groups; no borderlines; vertical separation is via  */}
        {/* gap below.                                                          */}
        <PipelineCardStack
          history={pipeline.history}
          live={pipeline.live}
        />

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

      {/* ---- Scroll-to-bottom affordance (job-0153 Part 3) ----                 */}
      {/* Floats centered above the chat-input overlay. Shows when the user is    */}
      {/* scrolled up; smooth-scrolls and hides on click; auto-hides when the     */}
      {/* user reaches the bottom (handled by onScroll above).                    */}
      <div
        data-testid="scroll-to-bottom-anchor"
        style={{
          position: "absolute",
          left: 0,
          right: 0,
          bottom: inputHeightPx + INPUT_GAP_PX + 8,
          display: "flex",
          justifyContent: "center",
          pointerEvents: "none",
          zIndex: 2,
        }}
      >
        <div style={{ pointerEvents: scrollArrowVisible ? "auto" : "none" }}>
          <ScrollToBottom
            visible={scrollArrowVisible}
            onClick={scrollToBottom}
          />
        </div>
      </div>

      {/* ---- Overlay input wrapper (job-0144 + job-0153) ----                    */}
      {/* Floats at the bottom of the chat panel; the scroll above has matching   */}
      {/* bottom-padding (driven by onHeightChange) so messages and inline cards  */}
      {/* are never hidden behind it, even when the textarea grows multi-line.    */}
      <div
        data-testid="chat-input-overlay"
        style={{
          position: "absolute",
          left: 12,
          right: 12,
          bottom: 12,
          pointerEvents: "auto",
          zIndex: 3,
        }}
      >
        <ChatInput
          state={inputState}
          onSubmit={submit}
          onCancel={cancel}
          disabled={inputDisabled}
          onHeightChange={handleInputHeightChange}
        />
      </div>
    </div>
  );
}

// --- Pipeline card stack ------------------------------------------------- //
//
// job-0162 — merge every snapshot (history + live) by step_id and render ONE
// card per step in encounter order. Each tool dispatch on the agent side
// creates a fresh pipeline_id (server.py per-tool start_pipeline +
// close_pipeline); without merging, a turn that dispatches N tools renders
// N separate "groups" — and a tool that transitions pending → running →
// complete renders as a stale running card above the completed one. We
// dedupe by step_id (unique across pipelines per ULID semantics) and prefer
// the latest snapshot of each.
//
// Visual treatment is delegated entirely to PipelineCard (state-driven
// background + animated text + spinner per the memory spec).

interface PipelineCardStackProps {
  history: PipelineStatePayload[];
  live: PipelineStatePayload | null;
}

export function mergeStepsByStepId(
  history: PipelineStatePayload[],
  live: PipelineStatePayload | null,
): PipelineStepSummary[] {
  // Walk history in order, then live last (so live wins on tie). Each
  // step_id's most-recently-encountered snapshot is the rendered one; the
  // first-encountered position is the display order (stable across
  // re-renders).
  //
  // job-0166 Part 3 — second-pass dedupe by (name, tool_name). The agent
  // emits the "llm_generation" thinking step on a fresh pipeline_id per
  // user-message; if the wrapping `_invoke_tool_via_emitter` lifecycle
  // races such that a stale running snapshot is archived before the
  // matching complete arrives, the merge by step_id keeps both visible
  // (different step_ids). This second pass collapses any two cards
  // sharing the same (name, tool_name) within a single render to the
  // most-recent one, so the user sees ONE transitioning llm_generation
  // card whose state advances pending → running → complete (or failed /
  // cancelled), never a stale blue rainbow card stacked next to a green
  // completed one.
  const orderedIds: string[] = [];
  const latest = new Map<string, PipelineStepSummary>();
  const consume = (steps: PipelineStepSummary[] | undefined): void => {
    if (!steps) return;
    for (const s of steps) {
      if (!latest.has(s.step_id)) {
        orderedIds.push(s.step_id);
      }
      latest.set(s.step_id, s);
    }
  };
  for (const snap of history) consume(snap.steps);
  if (live) consume(live.steps);

  // First-pass result, in original encounter order.
  const merged = orderedIds.map((id) => latest.get(id)!);

  // Second-pass: collapse by (name|tool_name) — preserves the most-recently
  // encountered card for each pair; preserves first-encounter ORDER of
  // that pair (so the llm_generation card stays at its original position
  // when its step_id is reissued mid-stream).
  const byKey = new Map<string, number>(); // key → index in result
  const result: PipelineStepSummary[] = [];
  for (const s of merged) {
    const key = `${s.name}|${s.tool_name}`;
    const prevIdx = byKey.get(key);
    if (prevIdx === undefined) {
      byKey.set(key, result.length);
      result.push(s);
    } else {
      // Same logical step encountered again with a different step_id —
      // replace in place so the latest state wins at the existing position.
      result[prevIdx] = s;
    }
  }
  return result;
}

function PipelineCardStack({
  history,
  live,
}: PipelineCardStackProps): JSX.Element | null {
  const steps = mergeStepsByStepId(history, live);
  if (steps.length === 0) return null;
  return (
    <div
      data-testid="pipeline-card-stack"
      style={{
        display: "flex",
        flexDirection: "column",
        // job-0162 memory spec: 12-16px vertical gap between stacked cards;
        // no borderlines, no group header, no horizontal dividers.
        gap: 14,
        padding: "4px 0",
      }}
    >
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
