// GRACE-2 web — Chat panel with TRULY INTERLEAVED inline pipeline cards
// (FR-WC-7, FR-WC-8, FR-WC-9; job-0176 interleave refactor).
//
// Renders the streamed agent reply token-by-token from `agent-message-chunk`
// deltas (Appendix A.4, replace-not-reconcile semantics on `done: true`).
// Multi-line input with Ctrl/Cmd+Enter submit. No markdown for M1 (M3
// adds markdown + tool-call blocks).
//
// PIPELINE CARDS INLINE — INTERLEAVED (job-0176, supersedes job-0064/0162):
//   Pipeline step cards are now interleaved INLINE in the conversation scroll
//   in actual arrival order alongside agent text bubbles, NOT collected into
//   a separate strip / stack at the bottom of the panel. The user-visible
//   pattern (per memory `feedback_chat_tool_interleave`):
//
//     [user]    "Show me protected areas in Fort Myers"
//     [agent]   "I'm locating the area..."
//     [tool]    Locating area [Nominatim] (0:01) ✓
//     [agent]   "Now fetching protected areas..."
//     [tool]    Fetching protected areas [WDPA] (0:08) ✓
//     [agent]   "I've added 2 protected areas (...)."
//
//   Implementation: every received envelope advances a single ``arrivalSeq``
//   monotonic counter; the FIRST time a ``message_id`` (agent) or a logical
//   step key (``name|tool_name`` — same collapsing key the legacy
//   ``mergeStepsByStepId`` used) is seen, we record ``seq`` against it. The
//   rendered stream is the union (user msgs + agent msgs + merged tool
//   steps) sorted by ``seq``. Subsequent envelopes for the same message_id
//   / step_key update content + state in place — the stream position is
//   fixed at first-arrival. This gives a stable chronological scroll that
//   matches how the agent + tools actually unfolded.
//
//   One card per unique step_key (collapsed across pipeline_ids per the
//   server's per-tool start_pipeline pattern + the llm_generation reissue
//   edge case from job-0166 Part 3), transitioning through pending →
//   running → complete / failed / cancelled. Visual states are driven by
//   PipelineCard per `feedback_pipeline_card_visual_states` + humanized
//   labels per `feedback_pipeline_card_humanized_labels`.
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
  CaseOpenEnvelopePayload,
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
import { ThinkingIndicator } from "./components/ThinkingIndicator";
import { ChartStack, type ChartPayload } from "./components/ChartStack";
import { ChartGallery } from "./components/ChartGallery";
import { SandboxCard, type CodeExecRequestPayload, type CodeExecResultPayload, type SandboxCardDecision } from "./components/SandboxCard";

// wave-4-10 thinking-state — the agent emits the Gemini "thinking" phase as
// a pipeline-state step keyed on this raw ``name`` (`llm_generation` per
// agent/runtime/llm.py + Appendix D.6). The web side treats it as a
// SPECIAL CASE per `feedback_thinking_state_ephemeral`: filtered out of the
// interleaved tool-card stream and rendered as a separate ephemeral
// indicator pinned to the bottom of the chat scroll. Other tools dispatch
// through the normal interleaved path with their visual-state lifecycle.
export const THINKING_STEP_NAME = "llm_generation";

/** True iff this pipeline step is the Gemini "thinking" phase. */
export function isThinkingStep(step: PipelineStepSummary): boolean {
  return step.name === THINKING_STEP_NAME;
}

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

export interface PipelineInlineState {
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
    }
  // job-0172 Part A — case-open is replace-not-reconcile applied to the
  // inline pipeline view-model. Drop the live + history snapshots that
  // belonged to the previously-active Case so the panel reflects the
  // newly-opened Case from a clean slate. Persisted PipelineRecords for
  // this Case will surface again via ``session-state.pipeline_history``
  // on the next hydration; on a brand-new Case the inline strip stays
  // empty until the user issues the first prompt.
  | { type: "case-open" };

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

export function pipelineReducer(
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
    case "case-open": {
      // job-0172 Part A — replace-not-reconcile on Case switch.
      return {
        live: null,
        history: [],
        currentPipelineFromSession: null,
      };
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
      //
      // job-0173 Part 2 — additionally force ChatInput back to idle so the
      // user can send a new prompt after a Gemini failure / agent crash /
      // dispatch TypeError. The cancel predicate (shouldShowCancel) reads
      // (a) live.steps.some(running) and (b) currentPipelineFromSession !==
      // null; rewriting the running step to failed kills (a) but the
      // session.current_pipeline lingers on the error path because the
      // agent never gets to emit a terminal session-state. We clear (b)
      // here, AND if after the force-flip no live step is still running we
      // move the live snapshot to history so the inline render keeps the
      // failed-state card visible without a residual "in-flight" pipeline.
      const flipped = forceMostRecentRunningToFailed(
        state,
        action.payload,
        action.tool_name ?? null,
      );
      const liveStillRunning =
        flipped.live?.steps?.some((s) => s.state === "running") ?? false;
      let nextHistory = flipped.history;
      let nextLive = flipped.live;
      if (!liveStillRunning && flipped.live !== null) {
        nextHistory = [...flipped.history, flipped.live];
        nextLive = null;
      }
      return {
        ...flipped,
        live: nextLive,
        history: nextHistory,
        currentPipelineFromSession: null,
      };
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

// --- Thinking-indicator active predicate (wave-4-10) -------------------- //
//
// The ephemeral "Thinking…" indicator is shown when the Gemini reasoning
// phase is in flight AND no real content has arrived yet that would replace
// it. Per memory `feedback_thinking_state_ephemeral`, the indicator
// vanishes the moment ANY of:
//
//   (a) The first agent text chunk after this thinking turn streams in
//       (a non-empty in-flight or finalized agent message renders the text
//       bubble and the indicator's job is done).
//   (b) The first non-thinking tool card lands (the agent decided to call
//       a tool — the tool card itself is the "I am working" affordance).
//   (c) The thinking pipeline-state transitions to a terminal state
//       (complete / failed / cancelled). On success the indicator just
//       disappears (no green confirmation card). On failure the error
//       envelope path replaces it with the red failure surface.
//
// Active iff a Gemini "llm_generation" step exists in pending OR running
// state across (live ∪ history) AND there is no non-thinking tool card and
// no agent text bubble that came AFTER it was recorded in arrivalSeq.
//
// Implementation: we look at every merged step (history + live) for the
// thinking step (mergeStepsByStepId already collapses the per-pipeline
// reissue). If found in pending/running, we then check whether any
// non-thinking tool step OR any agent text bubble was recorded with a
// seq >= the thinking step's seq. If so → the indicator has been replaced
// by the real content and should hide.
//
// On terminal thinking state, return false. On a fresh thinking that hasn't
// been superseded by anything, return true.

export function isThinkingActive(
  messages: ChatMessage[],
  history: PipelineStatePayload[],
  live: PipelineStatePayload | null,
  messageOrder: Map<string, number>,
  stepOrder: Map<string, number>,
): boolean {
  // Find the most-recent thinking step across (history ∪ live). Use the
  // merge result so the per-pipeline reissue collapses (matches the
  // interleaved-stream filter — single source of truth for "current
  // thinking step").
  const merged = mergeStepsByStepId(history, live);
  const thinking = merged.find(isThinkingStep);
  if (!thinking) return false;
  // Terminal thinking → indicator gone.
  if (
    thinking.state === "complete" ||
    thinking.state === "failed" ||
    thinking.state === "cancelled"
  ) {
    return false;
  }
  // Look up the thinking step's first-arrival seq. If we never recorded it
  // (defensive — should not happen because recordPipelineStepSeqs records
  // every step name|tool_name), treat as not-yet-superseded so we still
  // show the indicator while a fresh thinking is in flight.
  const thinkingKey = `${thinking.name}|${thinking.tool_name}`;
  const thinkingSeq = stepOrder.get(thinkingKey) ?? Number.MAX_SAFE_INTEGER;

  // Has any agent text bubble arrived at or after this thinking seq AND
  // contains content? An empty bubble (no text yet, just allocated) does
  // NOT count — the bubble must have at least one character of streamed
  // delta. (The agent typically emits "I'm working on X…" BEFORE the
  // llm_generation card, but on a fresh turn the bubble may be allocated
  // with empty text first; only when text arrives does the indicator's
  // job finish.)
  for (const m of messages) {
    if (m.role !== "agent") continue;
    if (m.text.length === 0) continue;
    const seq = messageOrder.get(m.id) ?? Number.MAX_SAFE_INTEGER;
    if (seq >= thinkingSeq) return false;
  }

  // Has any NON-thinking tool card landed at or after this thinking seq?
  // (A tool card is the "agent is doing real work" affordance — once one
  // appears the abstract "thinking" cue is redundant.)
  for (const step of merged) {
    if (isThinkingStep(step)) continue;
    const key = `${step.name}|${step.tool_name}`;
    const seq = stepOrder.get(key) ?? Number.MAX_SAFE_INTEGER;
    if (seq >= thinkingSeq) return false;
  }

  return true;
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

  // sprint-13 job-0231: chart-emission state in Chat.
  // Charts accumulate per session; Case switch resets to [] via the case-open handler.
  // Gallery state for the full-viewport chart viewer.
  const [charts, setCharts] = useState<ChartPayload[]>([]);
  const [galleryOpen, setGalleryOpen] = useState<boolean>(false);
  const [galleryCharts, setGalleryCharts] = useState<ChartPayload[]>([]);
  const [galleryInitialIndex, setGalleryInitialIndex] = useState<number>(0);

  // sprint-13 job-0234: sandbox code-exec cards.
  //
  // sandboxRequests: ordered list of code-exec-request payloads, in arrival
  // order. Each request gets a SandboxCard rendered inline in the chat scroll.
  //
  // sandboxResults: keyed by code_exec_id — the result arrives asynchronously
  // after the user approves and the sandbox runs. The card looks up its result
  // here to transition from RUNNING → RESULT state.
  //
  // sandboxDecisions: keyed by code_exec_id — the user's decision (proceed /
  // cancel). Locks the gate buttons after click; drives RUNNING state when
  // "proceed" and no result yet.
  //
  // sandboxSeqMap: first-arrival seq for each code_exec_id so the SandboxCard
  // interleaves chronologically in the unified chat stream. Keyed by
  // code_exec_id.
  //
  // Case switch resets all sandbox state to empty (replace-not-reconcile).
  const [sandboxRequests, setSandboxRequests] = useState<CodeExecRequestPayload[]>([]);
  const [sandboxResults, setSandboxResults] = useState<Map<string, CodeExecResultPayload>>(new Map());
  const [sandboxDecisions, setSandboxDecisions] = useState<Map<string, SandboxCardDecision>>(new Map());
  // Arrival seqs for sandbox cards — stored in a plain Map ref (not state)
  // so the sort in the render pass stays stable across re-renders without
  // triggering extra React cycles.
  const sandboxSeqRef = useRef<Map<string, number>>(new Map());

  // job-0176 — arrival-order tracking for chronological interleave.
  // ``messageOrder`` is keyed on ``message_id`` (user_id for user lines);
  // ``stepOrder`` is keyed on the step-collapse key (``name|tool_name`` —
  // matches mergeStepsByStepId's second-pass dedupe so the llm_generation
  // reissue edge case from job-0166 Part 3 stays a single card at its
  // original slot). First-encounter seq is sticky; subsequent envelopes for
  // the same key update content/state IN PLACE without moving the row.
  const arrivalSeqRef = useRef<number>(0);
  const messageOrderRef = useRef<Map<string, number>>(new Map());
  const stepOrderRef = useRef<Map<string, number>>(new Map());
  // Trigger-only state for re-renders when we update the refs above (refs
  // by themselves don't fire React updates; the messages / pipeline state
  // updates do fire them, so we don't actually need a separate signal —
  // updates that follow envelope arrivals always touch one of the existing
  // states. Keeping a numeric tick as belt-and-suspenders for the case-open
  // reset which would otherwise leave stale order maps + no other state
  // change to flush them).
  const [, bumpStreamTick] = useState<number>(0);

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

  // job-0176 — record first-arrival seq for a message_id; updates in place
  // afterwards. Called from onAgentChunk + submit() + rehydrate.
  const recordMessageSeq = useCallback((messageId: string) => {
    if (!messageOrderRef.current.has(messageId)) {
      arrivalSeqRef.current += 1;
      messageOrderRef.current.set(messageId, arrivalSeqRef.current);
    }
  }, []);

  // job-0176 — record first-arrival seq for every (name|tool_name) step key
  // encountered in a pipeline-state snapshot. Matches mergeStepsByStepId's
  // collapse key so the interleave anchors at the same point as the merged
  // card.
  const recordPipelineStepSeqs = useCallback((p: PipelineStatePayload) => {
    const steps = p.steps ?? [];
    for (const s of steps) {
      const key = `${s.name}|${s.tool_name}`;
      if (!stepOrderRef.current.has(key)) {
        arrivalSeqRef.current += 1;
        stepOrderRef.current.set(key, arrivalSeqRef.current);
      }
    }
  }, []);

  useEffect(() => {
    const ws = new GraceWs(wsUrl, {
      onStatus: (s) => setStatus(s),
      onAgentChunk: (p: AgentMessageChunkPayload) => {
        recordMessageSeq(p.message_id);
        setMessages((prev) => appendDelta(prev, p));
      },
      onPipelineState: (p: PipelineStatePayload) => {
        recordPipelineStepSeqs(p);
        dispatchPipeline({ type: "pipeline-state", payload: p });
      },
      onSessionState: (p: SessionStatePayload) => {
        dispatchPipeline({ type: "session-state", payload: p });
      },
      // job-0172 Part A: case-open is replace-not-reconcile applied
      // CLIENT-SIDE. When a Case opens we FLUSH the chat panel's local
      // message buffer + pipeline view-model THEN hydrate from
      // ``session_state.chat_history`` so the Chat panel reflects the
      // newly-opened Case rather than stale messages from the prior one.
      // ``session_state === null`` (server couldn't rehydrate) also clears,
      // giving the user a clean empty state per Appendix A.7 discipline.
      onCaseOpen: (p: CaseOpenEnvelopePayload) => {
        const rehydrated = rehydrateMessagesFromCaseOpen(p);
        // job-0176 — reset arrival-order maps + counter so the new Case's
        // stream starts from seq=1; then re-record seq for every replayed
        // chat message in encounter order so rehydrated history retains
        // its original chronology.
        arrivalSeqRef.current = 0;
        messageOrderRef.current = new Map();
        stepOrderRef.current = new Map();
        for (const m of rehydrated) {
          recordMessageSeq(m.id);
        }
        setMessages(rehydrated);
        // Reset the inline pipeline state: the live snapshot belonged to
        // the OUTGOING Case (if any). The next pipeline-state envelope for
        // THIS Case will repopulate it.
        dispatchPipeline({ type: "case-open" });
        setLastError(null);
        // sprint-13 job-0231: Case switch resets charts in Chat too
        // (replace-not-reconcile applied client-side). Rehydration from
        // session.charts is handled in App.tsx and arrives via
        // onChartEmission fan-out on reconnect; we start from a clean
        // slate so stale charts from the prior Case don't linger.
        setCharts([]);
        setGalleryOpen(false);
        // sprint-13 job-0234: Case switch resets sandbox state too.
        setSandboxRequests([]);
        setSandboxResults(new Map());
        setSandboxDecisions(new Map());
        sandboxSeqRef.current = new Map();
        bumpStreamTick((n) => n + 1);
      },
      onError: (p: ErrorPayload) => {
        setLastError(`${p.error_code}: ${p.message}`);
        // job-0166 Part 1 — force the most-recent running step to failed so
        // the rainbow animation terminates and the user sees a RED card.
        // Sender envelope shape does not currently include tool_name; pass
        // null and rely on the most-recent-running fallback.
        dispatchPipeline({ type: "error", payload: p, tool_name: null });
      },
      // sprint-13 job-0231: chart-emission routing in Chat's own GraceWs
      // instance. Because chart-emission is in SESSION_SCOPED_TYPES, Chat
      // receives it via the fan-out hub even when it was emitted on
      // App.tsx's connection. De-dupe on chart_id so both hub-delivered and
      // direct arrivals don't double-stack.
      onChartEmission: (p: ChartPayload) => {
        setCharts((prev) => {
          if (prev.some((c) => c.chart_id === p.chart_id)) return prev;
          return [...prev, p];
        });
      },
      // sprint-13 job-0234: code-exec-request — render a SandboxCard gate
      // card inline in the chat. De-dupe on code_exec_id so reconnect fan-out
      // doesn't double-add the same request.
      onCodeExecRequest: (p: CodeExecRequestPayload) => {
        setSandboxRequests((prev) => {
          if (prev.some((r) => r.code_exec_id === p.code_exec_id)) return prev;
          // Record arrival seq for chronological interleave.
          if (!sandboxSeqRef.current.has(p.code_exec_id)) {
            arrivalSeqRef.current += 1;
            sandboxSeqRef.current.set(p.code_exec_id, arrivalSeqRef.current);
          }
          return [...prev, p];
        });
      },
      // sprint-13 job-0234: code-exec-result — update the matching SandboxCard
      // from RUNNING → RESULT state (keyed by code_exec_id).
      onCodeExecResult: (p: CodeExecResultPayload) => {
        setSandboxResults((prev) => {
          const next = new Map(prev);
          next.set(p.code_exec_id, p);
          return next;
        });
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
  //
  // job-0176 — injected pipeline-states must also bump arrival-order seqs
  // for new step keys so dev-injected cards interleave at the right slot.
  // Per `feedback_playwright_must_drive_live_agent` this seam is INVALID
  // for end-to-end verification; only unit tests + component-state
  // Playwright tests may use it.
  useEffect(() => {
    if (!import.meta.env.DEV) return;
    window.__grace2InjectPipelineState = (p) => {
      recordPipelineStepSeqs(p);
      dispatchPipeline({ type: "pipeline-state", payload: p });
    };
    return () => {
      delete window.__grace2InjectPipelineState;
    };
  }, [recordPipelineStepSeqs]);

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

  // sprint-13 job-0231: chart injection dev seam for Playwright snapshots.
  // App.tsx owns the primary __grace2InjectChartEmission window seam.
  // Chat.tsx subscribes to a parallel seam __grace2InjectChartEmissionChat
  // so Playwright can directly inject into the Chat component's own chart
  // state. In production only the real GraceWs onChartEmission handler is
  // active; the window seam is guarded behind import.meta.env.DEV.
  //
  // The window seam approach is used instead of the SESSION_SCOPED_TYPES
  // hub fan-out because the hub fan-out only works for real WebSocket
  // messages — the window injection bypasses the WS layer entirely (which
  // is the whole point for UI snapshot tests without a live agent).
  useEffect(() => {
    if (!import.meta.env.DEV) return;
    // Subscribe to the shared window seam defined in App.tsx.
    // App.tsx registers __grace2InjectChartEmission to call App's own
    // setCharts. We ALSO need Chat's setCharts to be called. We achieve
    // this by registering a SECOND seam __grace2InjectChartEmissionChat
    // that Chat.tsx owns. Playwright scripts call both seams (or just the
    // shared one via the multi-dispatch wrapper below).
    //
    // Alternatively: override __grace2InjectChartEmission in Chat to
    // also drive Chat's local state. We do this carefully: wrap the
    // existing App seam so both App and Chat state update together.
    const prev = (window as unknown as Record<string, unknown>).__grace2InjectChartEmission as ((p: ChartPayload) => void) | undefined;
    const combined = (p: ChartPayload) => {
      // Drive Chat state first.
      setCharts((prevCharts) => {
        if (prevCharts.some((c) => c.chart_id === p.chart_id)) return prevCharts;
        return [...prevCharts, p];
      });
      // Then call App's handler if it exists.
      prev?.(p);
    };
    (window as unknown as Record<string, unknown>).__grace2InjectChartEmission = combined;
    return () => {
      // Restore App's original seam on cleanup.
      if (typeof prev === "function") {
        (window as unknown as Record<string, unknown>).__grace2InjectChartEmission = prev;
      } else {
        delete (window as unknown as Record<string, unknown>).__grace2InjectChartEmission;
      }
    };
  }, []);

  // sprint-13 job-0234: dev seam for code-exec injection.
  // Playwright UI-only snapshot tests (UI seam PERMITTED per
  // `feedback_bundle_ui_verification_with_existing_queries`) can call:
  //   window.__grace2InjectCodeExec({ request: {...}, result?: {...} })
  // to insert a SandboxCard without a live agent connection.
  // Guards behind import.meta.env.DEV so it's stripped in production builds.
  useEffect(() => {
    if (!import.meta.env.DEV) return;
    (window as unknown as Record<string, unknown>).__grace2InjectCodeExec = (args: {
      request: CodeExecRequestPayload;
      result?: CodeExecResultPayload;
      decision?: SandboxCardDecision;
    }) => {
      const { request, result, decision } = args;
      setSandboxRequests((prev) => {
        if (prev.some((r) => r.code_exec_id === request.code_exec_id)) return prev;
        if (!sandboxSeqRef.current.has(request.code_exec_id)) {
          arrivalSeqRef.current += 1;
          sandboxSeqRef.current.set(request.code_exec_id, arrivalSeqRef.current);
        }
        return [...prev, request];
      });
      if (result !== undefined) {
        setSandboxResults((prev) => {
          const next = new Map(prev);
          next.set(result.code_exec_id, result);
          return next;
        });
      }
      if (decision !== undefined) {
        setSandboxDecisions((prev) => {
          const next = new Map(prev);
          next.set(request.code_exec_id, decision);
          return next;
        });
      }
    };
    return () => {
      delete (window as unknown as Record<string, unknown>).__grace2InjectCodeExec;
    };
  }, []);

  // Auto-scroll on new content only when the user is already at the bottom.
  // This preserves the user's reading position when they've scrolled up to
  // read history while the stream is still landing new tokens.
  useEffect(() => {
    if (scrollRef.current && atBottomRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, pipeline, charts, sandboxRequests, sandboxResults]);

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

  // sprint-13 job-0234: sandbox gate decision handler.
  // Wired to SandboxCard.onDecide; reuses sendPayloadConfirmation with the
  // code_exec_id as warning_id per the job-0233 confirm-gate seam design.
  function handleSandboxDecide(codeExecId: string, decision: SandboxCardDecision): void {
    setSandboxDecisions((prev) => {
      const next = new Map(prev);
      next.set(codeExecId, decision);
      return next;
    });
    wsRef.current?.sendPayloadConfirmation(
      codeExecId,
      decision === "proceed" ? "proceed" : "cancel",
      null,
    );
  }

  function submit(text: string): void {
    if (!text || !wsRef.current) return;
    setMessages((prev) => {
      const userId = `user-${prev.length}`;
      // job-0176 — record arrival seq for the user bubble so it interleaves
      // chronologically with subsequent agent text + tool cards.
      recordMessageSeq(userId);
      return [
        ...prev,
        { id: userId, role: "user", text, done: true },
      ];
    });
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

        {/* job-0176 — single chronological stream. Tool cards interleave   */}
        {/* in-line with user + agent bubbles, sorted by first-arrival     */}
        {/* seq. Tool steps reuse the (name|tool_name) collapse key so the */}
        {/* llm_generation reissue edge case (job-0166 Part 3) stays as a  */}
        {/* single transitioning card pinned to its original chat slot.    */}
        {/* wave-4-10 — the Gemini "Thinking…" pseudo-step is filtered out  */}
        {/* of this stream and rendered as the separate ephemeral          */}
        {/* ThinkingIndicator at the BOTTOM of the scroll (below). It      */}
        {/* vanishes the moment a real agent text bubble or non-thinking   */}
        {/* tool card arrives.                                              */}
        <InterleavedChatStream
          messages={messages}
          history={pipeline.history}
          live={pipeline.live}
          messageOrder={messageOrderRef.current}
          stepOrder={stepOrderRef.current}
        />

        {/* wave-4-10 ephemeral Thinking indicator — italic muted-gray     */}
        {/* "Thinking…" with subtle opacity pulse. NO card chrome. Always  */}
        {/* the last child of the scroll container so it visually pins to  */}
        {/* the bottom regardless of when the llm_generation step arrived. */}
        {/* Hides on first agent text chunk / first non-thinking tool /    */}
        {/* terminal thinking state. See `feedback_thinking_state_ephemeral`. */}
        <ThinkingIndicator
          active={isThinkingActive(
            messages,
            pipeline.history,
            pipeline.live,
            messageOrderRef.current,
            stepOrderRef.current,
          )}
        />

        {/* sprint-13 job-0231: inline chart stacks. Charts group by
            created_turn_id; singletons (null turn_id) render alone.
            Stacks appear after the interleaved tool/message stream because
            they arrive on a separate envelope type that doesn't carry
            an arrivalSeq (chart-emission is not interleaved with
            pipeline-state — it's a distinct session-scoped envelope that
            arrives asynchronously). We render them as a trailing section
            below the message stream. Each stack is independently clickable
            to open the ChartGallery overlay. */}
        {charts.length > 0 && (
          <div
            data-testid="chart-stack-section"
            style={{ display: "flex", flexDirection: "column", gap: 12, paddingTop: 4 }}
          >
            {buildChartStacks(charts).map((stack) => (
              <ChartStack
                key={stack[0]!.chart_id}
                charts={stack}
                onOpenGallery={(stackCharts, idx) => {
                  setGalleryCharts(stackCharts);
                  setGalleryInitialIndex(idx);
                  setGalleryOpen(true);
                }}
              />
            ))}
          </div>
        )}

        {/* sprint-13 job-0234: sandbox code-exec cards.
            Rendered sorted by arrival seq so they interleave chronologically
            with the rest of the chat stream. Each SandboxCard handles its own
            REQUEST → RUNNING → RESULT state machine driven by the three
            sandbox state maps. The onDecide callback is wired to
            sendPayloadConfirmation (reusing the existing payload-warning gate
            seam with code_exec_id as warning_id per job-0233 design). */}
        {sandboxRequests.length > 0 && (() => {
          // Sort by arrival seq for stable chronological display.
          const sorted = [...sandboxRequests].sort((a, b) => {
            const sa = sandboxSeqRef.current.get(a.code_exec_id) ?? Number.MAX_SAFE_INTEGER;
            const sb = sandboxSeqRef.current.get(b.code_exec_id) ?? Number.MAX_SAFE_INTEGER;
            return sa - sb;
          });
          return (
            <div
              data-testid="sandbox-cards-section"
              style={{ display: "flex", flexDirection: "column", gap: 10 }}
            >
              {sorted.map((req) => (
                <SandboxCard
                  key={req.code_exec_id}
                  request={req}
                  result={sandboxResults.get(req.code_exec_id)}
                  decided={sandboxDecisions.get(req.code_exec_id) ?? null}
                  onDecide={(d) => handleSandboxDecide(req.code_exec_id, d)}
                />
              ))}
            </div>
          );
        })()}

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

      {/* sprint-13 job-0231: ChartGallery full-viewport overlay.
          Rendered inside the Chat panel so it is scoped to this mount
          (Chat is kept mounted across collapse). z-index 10_000 from
          ChartGallery overlays the full viewport — intentional, as the
          chart gallery is a primary focus surface. */}
      {galleryOpen && galleryCharts.length > 0 && (
        <ChartGallery
          charts={galleryCharts}
          initialIndex={galleryInitialIndex}
          onClose={() => setGalleryOpen(false)}
        />
      )}
    </div>
  );
}

// --- Pipeline merge (job-0162) ------------------------------------------- //
//
// merge every snapshot (history + live) by step_id and render ONE
// card per step in encounter order. Each tool dispatch on the agent side
// creates a fresh pipeline_id (server.py per-tool start_pipeline +
// close_pipeline); without merging, a turn that dispatches N tools renders
// N separate "groups" — and a tool that transitions pending → running →
// complete renders as a stale running card above the completed one. We
// dedupe by step_id (unique across pipelines per ULID semantics) and prefer
// the latest snapshot of each.
//
// job-0176 — this function still produces the merged-step list; the
// rendering surface moved from PipelineCardStack to the InterleavedChatStream
// below. The PipelineCardStack export is preserved for tests that pin its
// data-testid; in production it is no longer mounted by Chat.
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

// Preserved for completeness + legacy tests; not mounted by Chat post job-0176.
// Exported so future tests can pin its data-testid without rewiring.
export function PipelineCardStack({
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

// --- Interleaved chat stream (job-0176) ---------------------------------- //
//
// Renders user bubbles, agent text bubbles, AND merged pipeline tool cards
// in a single sorted-by-first-arrival list. Each row carries a stable key
// (``message_id`` for chat rows, ``step_id`` for tool rows) so React's
// reconciliation preserves each card's identity across re-renders even as
// new envelopes arrive between existing rows. (A new step's first
// pipeline-state will land at the END of the current scroll because its
// arrivalSeq is the latest; thereafter that card's position is sticky.)
//
// Stream-entry construction is pure: messages + merged steps + order maps
// in, sorted list of stream-entry view-models out. Exported as
// ``buildInterleavedStream`` for unit testing.

export type InterleavedEntry =
  | { kind: "user-message"; seq: number; id: string; text: string }
  | {
      kind: "agent-message";
      seq: number;
      id: string;
      text: string;
      done: boolean;
    }
  | {
      kind: "tool";
      seq: number;
      // step_key (``name|tool_name``) is the React key; matches what we
      // record in stepOrder so the row is stable across pipeline_id
      // reissues (job-0166 Part 3).
      stepKey: string;
      step: PipelineStepSummary;
    };

export function buildInterleavedStream(
  messages: ChatMessage[],
  history: PipelineStatePayload[],
  live: PipelineStatePayload | null,
  messageOrder: Map<string, number>,
  stepOrder: Map<string, number>,
): InterleavedEntry[] {
  const out: InterleavedEntry[] = [];
  // Messages — seq comes from messageOrder; absent → fall back to a large
  // sentinel so it sorts AFTER recorded rows (defensive — every message
  // gets recorded via recordMessageSeq today, but this keeps render
  // deterministic if recording was missed).
  for (const m of messages) {
    const seq = messageOrder.get(m.id) ?? Number.MAX_SAFE_INTEGER;
    if (m.role === "user") {
      out.push({ kind: "user-message", seq, id: m.id, text: m.text });
    } else {
      out.push({
        kind: "agent-message",
        seq,
        id: m.id,
        text: m.text,
        done: m.done,
      });
    }
  }
  // Tool cards — feed mergeStepsByStepId then look up seq via the
  // (name|tool_name) collapse key. The collapse key matches what
  // recordPipelineStepSeqs records, so the rendered position is sticky
  // across pipeline_id reissues + state transitions.
  //
  // wave-4-10 thinking-state: the Gemini "llm_generation" step is special-
  // cased — it does NOT interleave as a tool card. It renders as a separate
  // ephemeral indicator pinned to the bottom of the chat scroll (no box, no
  // green tint, vanishes on first agent text / first non-thinking tool /
  // terminal success). See `feedback_thinking_state_ephemeral`. We filter
  // it here so the interleaved stream contains only actionable tool cards.
  const mergedSteps = mergeStepsByStepId(history, live);
  for (const step of mergedSteps) {
    if (isThinkingStep(step)) continue;
    const key = `${step.name}|${step.tool_name}`;
    const seq = stepOrder.get(key) ?? Number.MAX_SAFE_INTEGER;
    out.push({ kind: "tool", seq, stepKey: key, step });
  }
  // Stable sort by seq; ties broken by insertion order (preserved by the
  // standard ``Array.prototype.sort`` in V8/spidermonkey/JSC since
  // ES2019). Insertion order here is: messages first then tools, so a
  // tool row that arrived in the SAME tick as a message bubble will land
  // just after it — which is the correct visual chronology since chat
  // bubbles are rendered first when they share a tick (the message
  // arrives in agent-message-chunk; the tool comes a moment later when
  // the agent emits its pipeline-state).
  out.sort((a, b) => a.seq - b.seq);
  return out;
}

interface InterleavedChatStreamProps {
  messages: ChatMessage[];
  history: PipelineStatePayload[];
  live: PipelineStatePayload | null;
  messageOrder: Map<string, number>;
  stepOrder: Map<string, number>;
}

function InterleavedChatStream({
  messages,
  history,
  live,
  messageOrder,
  stepOrder,
}: InterleavedChatStreamProps): JSX.Element | null {
  const stream = buildInterleavedStream(
    messages,
    history,
    live,
    messageOrder,
    stepOrder,
  );
  if (stream.length === 0) return null;
  return (
    <div
      data-testid="chat-stream"
      style={{
        display: "flex",
        flexDirection: "column",
        // job-0162 memory spec: 12-16px gap between stacked rows; preserved
        // here for the unified stream so tool cards and bubbles read with
        // the same visual rhythm.
        gap: 14,
      }}
    >
      {stream.map((entry) => {
        if (entry.kind === "user-message") {
          return <UserBubble key={entry.id} text={entry.text} />;
        }
        if (entry.kind === "agent-message") {
          return (
            <AgentMessage
              key={entry.id}
              text={entry.text}
              done={entry.done}
            />
          );
        }
        // tool
        return <PipelineCard key={entry.stepKey} step={entry.step} />;
      })}
    </div>
  );
}

// --- Pure helpers -------------------------------------------------------- //

// Apply an agent-message-chunk delta to the message list.
// `agent-message-chunk.delta` is incremental per A.4 (not accumulated); we
// append by `message_id` and finalize on `done: true`.
/**
 * job-0172 Part A — convert a ``case-open`` payload's ``chat_history`` into
 * the local ``ChatMessage[]`` view-model. Server-side ``CaseChatMessage``
 * carries ``{message_id, role, content, ...}``; the local shape carries
 * ``{id, role, text, done}``. We mark every replayed message as ``done:
 * true`` because they're persisted turns (no in-flight streaming). The
 * server's ``role`` may be ``"agent"``, ``"user"``, or ``"system"``; the
 * local view only renders ``"agent"`` / ``"user"``, so system messages are
 * filtered (no surprise rendering of internal scaffolding). Returns ``[]``
 * for a brand-new Case OR when ``session_state`` is null (server couldn't
 * rehydrate) so the panel cleanly resets either way.
 */
export function rehydrateMessagesFromCaseOpen(
  p: CaseOpenEnvelopePayload,
): ChatMessage[] {
  const session = p.session_state;
  if (!session) return [];
  const chat = session.chat_history ?? [];
  const out: ChatMessage[] = [];
  for (const m of chat) {
    if (m.role !== "agent" && m.role !== "user") continue;
    out.push({
      id: m.message_id,
      role: m.role,
      text: m.content ?? "",
      done: true,
    });
  }
  return out;
}

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

// --- Chart stack grouping (sprint-13 job-0231) ------------------------------ //
//
// Groups a flat list of ChartPayload items into stacks keyed on
// ``created_turn_id``. Charts with the same non-null ``created_turn_id`` form
// one stack. Charts with ``created_turn_id === null`` are each their own
// singleton stack (they arrived independently, not as a batch). The grouping
// order preserves the original arrival order of the first chart in each group.
//
// Exported for unit testing; not used outside Chat.tsx otherwise.

export function buildChartStacks(charts: ChartPayload[]): ChartPayload[][] {
  const order: string[] = [];         // insertion order of group keys
  const groups = new Map<string, ChartPayload[]>();

  for (const c of charts) {
    // Singletons key on chart_id so each occupies its own slot.
    const key = c.created_turn_id ?? `__singleton__${c.chart_id}`;
    if (!groups.has(key)) {
      order.push(key);
      groups.set(key, []);
    }
    groups.get(key)!.push(c);
  }

  return order.map((k) => groups.get(k)!);
}
