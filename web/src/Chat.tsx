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
//
// PER-CASE CHAT STREAMS (job-0266 — "Case = conversation thread"):
//   Every piece of conversational state — messages, tool cards, sandbox
//   cards, charts, errors, arrival-order maps — lives in a per-Case
//   ``StreamState`` keyed by ``case_id`` inside a ref-held ``ChatStreams``
//   map. The VISIBLE stream is selected by the ``activeCaseId`` prop
//   (App.tsx wires it from useCases):
//
//     - Switching Cases swaps the ENTIRE visible stream.
//     - Root view (activeCaseId === null) renders the root stream, which is
//       reset to a clean empty composer whenever the user navigates OUT of
//       a Case (the Case's stream persists server-side AND in the in-memory
//       map for this session).
//     - Streaming envelopes route to the stream of the Case that OWNS the
//       in-flight turn (``ChatStreams.targetKey`` — captured at submit
//       time). An envelope arriving for a non-visible Case buffers into
//       that Case's stream; it is never painted into the visible one.
//     - Typing from root: the server auto-creates a Case (job-0262) and
//       emits ``case-open`` BEFORE the turn dispatches. ``routeCaseOpen``
//       adopts the in-flight root turn into the new Case (targetKey
//       reassignment), clears the root buffer (the typed message is in the
//       rehydrated ``chat_history``), and App's activeCaseId prop flips the
//       visible stream to the new Case — the user sees the thread from
//       turn 1.

import { useCallback, useEffect, useRef, useState } from "react";
import { ConnectionStatus, GraceWs } from "./ws";
import {
  AgentMessageChunkPayload,
  CaseChatMessage as CaseChatMessageWire,
  CaseOpenEnvelopePayload,
  CaseSessionState,
  ErrorPayload,
  PipelineSnapshot,
  PipelineStatePayload,
  PipelineStepSummary,
  ResearchMode,
  SessionStatePayload,
} from "./contracts";
import {
  PipelineCard,
  Spinner,
  formatDuration,
  humanizeStepName,
  prefersReducedMotion,
  useRunningElapsedMs,
} from "./components/PipelineCard";
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

/**
 * ux-batch-1 J9 (F18) — the stream-ordering + merge identity for a pipeline
 * step. Tool steps key by their UNIQUE step_id (a fresh ULID per invocation —
 * pipeline_emitter.add_step), so re-running the SAME tool in a LATER turn is a
 * NEW card with a NEW first-arrival seq that renders AFTER that turn's prompt,
 * instead of collapsing into (and inheriting the position of) the earlier
 * run's card. (That cross-turn collapse was the "new tool card shows up behind
 * the last prompt / old card reused" bug.)
 *
 * The ``llm_generation`` thinking pseudo-step is the ONE step the agent
 * reissues with fresh step_ids mid-turn (a new pipeline_id per generation), so
 * it keys by a STABLE name so all its reissues collapse to a single
 * transitioning indicator at one position. Thinking is filtered out of the
 * interleaved tool stream anyway; this only governs its ordering seq.
 */
export function stepInterleaveKey(step: PipelineStepSummary): string {
  return isThinkingStep(step)
    ? `${THINKING_STEP_NAME}|${step.tool_name}`
    : step.step_id;
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

// Build version shown in the chat header so the user can see at a glance which
// deploy their tab is running (replaces the old "M1 stub" placeholder). Baked
// at build time from VITE_BUILD_SHA (set in the deploy command to the git short
// SHA); falls back to "dev" for local runs. If the header still reads "M1 stub"
// the tab is on a pre-this-change cached bundle and needs a hard refresh.
const BUILD_VERSION: string =
  (import.meta.env.VITE_BUILD_SHA as string | undefined) || "dev";

// ux-batch-1 J1 (F10) — desktop chat-panel width is now USER-DRAGGABLE. The
// user grabs the panel's left border and drags it left/right to size the
// reading column to taste; the chosen width persists to localStorage. This
// replaces the prior two-state large/normal toggle (which was the only sizing
// the chat offered). Mobile is unaffected (the bottom sheet is full viewport).
const CHAT_WIDTH_DEFAULT_PX = 384;
const CHAT_WIDTH_MIN_PX = 320;
// Upper bound — never let the column eat the whole map. Clamped further to the
// viewport at apply time (drag handler) so a narrow window can't be overrun.
const CHAT_WIDTH_MAX_PX = 760;
const LS_CHAT_WIDTH = "grace2.chatWidthPx";

/** Clamp a desired chat width to the allowed [min, max] band. NaN/non-finite
 * inputs fall back to the default. Pure — also used by App.tsx's mirror. */
export function clampChatWidth(px: number): number {
  if (!Number.isFinite(px)) return CHAT_WIDTH_DEFAULT_PX;
  return Math.max(CHAT_WIDTH_MIN_PX, Math.min(CHAT_WIDTH_MAX_PX, Math.round(px)));
}

/** Read the persisted desktop chat width (px). Defaults to the historical
 * ~380px column. localStorage failures / unset / garbage degrade to default. */
export function readChatWidth(): number {
  try {
    const raw = localStorage.getItem(LS_CHAT_WIDTH);
    if (raw === null) return CHAT_WIDTH_DEFAULT_PX;
    return clampChatWidth(Number(raw));
  } catch {
    return CHAT_WIDTH_DEFAULT_PX;
  }
}

/** Persist the desktop chat width (px). Non-fatal on failure. */
export function writeChatWidth(px: number): void {
  try {
    localStorage.setItem(LS_CHAT_WIDTH, String(clampChatWidth(px)));
  } catch {
    /* non-fatal */
  }
}

// --- Chat message shape -------------------------------------------------- //

export interface ChatMessage {
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
  const thinkingKey = stepInterleaveKey(thinking);
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
    const key = stepInterleaveKey(step);
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

// --- Per-Case chat streams (job-0266) ------------------------------------ //
//
// The pure stream-routing core. Exported for unit testing (Chat itself
// cannot mount in happy-dom — it opens a WebSocket — so the per-Case
// behavior is verified through these functions, following the same
// pure-helper pattern as pipelineReducer / buildInterleavedStream).
//
// A ``StreamState`` is the complete conversational view-model of ONE Case
// (or of the Cases root, under the ``ROOT_STREAM_KEY`` sentinel). The
// ``ChatStreams`` container holds every stream touched this session plus
// ``targetKey`` — the key of the stream that OWNS currently-arriving
// streaming envelopes. ``targetKey`` is set at submit time (the Case that
// was visible when the user sent the message) and is re-pointed by
// ``routeCaseOpen`` when the server auto-creates a Case for a root prompt
// (job-0262 adoption). This is the "active case at arrival/submit" routing
// the product shape blesses: late envelopes for a turn the user navigated
// away from buffer into the owning Case's stream, never the visible one.

/** Sentinel stream key for the Cases root (no active Case). */
export const ROOT_STREAM_KEY = "__root__";

export interface StreamState {
  messages: ChatMessage[];
  pipeline: PipelineInlineState;
  charts: ChartPayload[];
  sandboxRequests: CodeExecRequestPayload[];
  sandboxResults: Map<string, CodeExecResultPayload>;
  sandboxDecisions: Map<string, SandboxCardDecision>;
  /** First-arrival seq per code_exec_id (chronological interleave). */
  sandboxSeqs: Map<string, number>;
  /** Monotonic arrival counter for this stream (job-0176 interleave). */
  arrivalSeq: number;
  messageOrder: Map<string, number>;
  stepOrder: Map<string, number>;
  lastError: string | null;
}

export function emptyStreamState(): StreamState {
  return {
    messages: [],
    pipeline: { live: null, history: [], currentPipelineFromSession: null },
    charts: [],
    sandboxRequests: [],
    sandboxResults: new Map(),
    sandboxDecisions: new Map(),
    sandboxSeqs: new Map(),
    arrivalSeq: 0,
    messageOrder: new Map(),
    stepOrder: new Map(),
    lastError: null,
  };
}

export interface ChatStreams {
  /** Every stream touched this session, keyed by case_id / ROOT_STREAM_KEY. */
  streams: Map<string, StreamState>;
  /** Stream key that owns currently-arriving streaming envelopes. */
  targetKey: string;
}

export function createChatStreams(): ChatStreams {
  return { streams: new Map(), targetKey: ROOT_STREAM_KEY };
}

/** Map an active Case id (null = root) to its stream key. */
export function streamKeyFor(caseId: string | null | undefined): string {
  return caseId ?? ROOT_STREAM_KEY;
}

/** Get (lazily creating) the stream for a key. */
export function getStream(cs: ChatStreams, key: string): StreamState {
  let s = cs.streams.get(key);
  if (!s) {
    s = emptyStreamState();
    cs.streams.set(key, s);
  }
  return s;
}

/** Reset the root stream to a clean slate (navigate-out-of-Case rule). */
export function clearRootStream(cs: ChatStreams): void {
  cs.streams.set(ROOT_STREAM_KEY, emptyStreamState());
}

// job-0176 arrival-order recording, per-stream. First-encounter seq is
// sticky; subsequent envelopes update content in place.
function recordMessageSeqIn(s: StreamState, messageId: string): void {
  if (!s.messageOrder.has(messageId)) {
    s.arrivalSeq += 1;
    s.messageOrder.set(messageId, s.arrivalSeq);
  }
}

function recordPipelineStepSeqsIn(
  s: StreamState,
  p: PipelineStatePayload,
): void {
  for (const step of p.steps ?? []) {
    const key = stepInterleaveKey(step);
    if (!s.stepOrder.has(key)) {
      s.arrivalSeq += 1;
      s.stepOrder.set(key, s.arrivalSeq);
    }
  }
}

/** Append the user's submitted message to the visible stream and take turn
 * ownership for it: every streaming envelope that follows belongs to this
 * stream until the next submit (or a job-0262 auto-create adoption). */
export function routeUserMessage(
  cs: ChatStreams,
  visibleKey: string,
  text: string,
): void {
  cs.targetKey = visibleKey;
  const s = getStream(cs, visibleKey);
  const userId = `user-${s.messages.length}`;
  recordMessageSeqIn(s, userId);
  s.messages = [...s.messages, { id: userId, role: "user", text, done: true }];
  s.lastError = null;
}

/** job-0277: resolve the stream that owns an arriving envelope. The agent
 * now stamps `Envelope.case_id` with the turn's pinned Case, so a
 * still-running turn's chunks/cards land in THEIR Case's stream even after
 * the user switches Cases and submit-time routing (`targetKey`) moved on.
 * Untagged envelopes (older builds, root-dispatched turns) keep the
 * submit-time fallback. */
function owningKey(cs: ChatStreams, caseId?: string | null): string {
  return typeof caseId === "string" && caseId.length > 0
    ? caseId
    : cs.targetKey;
}

export function routeAgentChunk(
  cs: ChatStreams,
  p: AgentMessageChunkPayload,
  caseId?: string | null,
): void {
  const s = getStream(cs, owningKey(cs, caseId));
  recordMessageSeqIn(s, p.message_id);
  s.messages = appendDelta(s.messages, p);
}

export function routePipelineState(
  cs: ChatStreams,
  p: PipelineStatePayload,
  caseId?: string | null,
): void {
  const s = getStream(cs, owningKey(cs, caseId));
  recordPipelineStepSeqsIn(s, p);
  s.pipeline = pipelineReducer(s.pipeline, {
    type: "pipeline-state",
    payload: p,
  });
}

export function routeSessionState(
  cs: ChatStreams,
  p: SessionStatePayload,
  caseId?: string | null,
): void {
  const s = getStream(cs, owningKey(cs, caseId));
  s.pipeline = pipelineReducer(s.pipeline, {
    type: "session-state",
    payload: p,
  });
}

export function routeError(
  cs: ChatStreams,
  p: ErrorPayload,
  caseId?: string | null,
): void {
  const s = getStream(cs, owningKey(cs, caseId));
  s.lastError = `${p.error_code}: ${p.message}`;
  // job-0166 Part 1 — force the most-recent running step to failed so the
  // rainbow animation terminates and the user sees a RED card (in the
  // OWNING Case's stream, even if it is not currently visible).
  s.pipeline = pipelineReducer(s.pipeline, {
    type: "error",
    payload: p,
    tool_name: null,
  });
}

export function routeChartEmission(
  cs: ChatStreams,
  p: ChartPayload,
  caseId?: string | null,
): void {
  const s = getStream(cs, owningKey(cs, caseId));
  // De-dupe on chart_id so hub-delivered + direct arrivals don't double-stack.
  if (s.charts.some((c) => c.chart_id === p.chart_id)) return;
  s.charts = [...s.charts, p];
}

export function routeCodeExecRequest(
  cs: ChatStreams,
  p: CodeExecRequestPayload,
  caseId?: string | null,
): void {
  const s = getStream(cs, owningKey(cs, caseId));
  if (s.sandboxRequests.some((r) => r.code_exec_id === p.code_exec_id)) return;
  if (!s.sandboxSeqs.has(p.code_exec_id)) {
    s.arrivalSeq += 1;
    s.sandboxSeqs.set(p.code_exec_id, s.arrivalSeq);
  }
  s.sandboxRequests = [...s.sandboxRequests, p];
}

export function routeCodeExecResult(
  cs: ChatStreams,
  p: CodeExecResultPayload,
  caseId?: string | null,
): void {
  // Route to whichever stream holds the matching REQUEST card — the user
  // may have submitted in another Case since the request arrived, moving
  // targetKey; the result must still resolve the card where it lives.
  let owner: StreamState | null = null;
  for (const s of cs.streams.values()) {
    if (s.sandboxRequests.some((r) => r.code_exec_id === p.code_exec_id)) {
      owner = s;
      break;
    }
  }
  const s = owner ?? getStream(cs, owningKey(cs, caseId));
  const next = new Map(s.sandboxResults);
  next.set(p.code_exec_id, p);
  s.sandboxResults = next;
}

/** Record the user's sandbox gate decision against the stream it lives in. */
export function recordSandboxDecision(
  cs: ChatStreams,
  key: string,
  codeExecId: string,
  decision: SandboxCardDecision,
): void {
  const s = getStream(cs, key);
  const next = new Map(s.sandboxDecisions);
  next.set(codeExecId, decision);
  s.sandboxDecisions = next;
}

/** Extract persisted charts from a case-open session (sprint-13 schema —
 * ``charts`` is not yet on the TS CaseSessionState type; read defensively
 * the same way App.tsx does). */
export function chartsFromSession(session: CaseSessionState): ChartPayload[] {
  const sessionCharts = (session as unknown as { charts?: ChartPayload[] })
    .charts;
  if (!Array.isArray(sessionCharts)) return [];
  return sessionCharts.filter(
    (c) => c && typeof c.chart_id === "string" && !!c.vega_lite_spec,
  );
}

/**
 * Handle a ``case-open`` envelope against the stream map.
 *
 *   - ``session_state === null`` (server couldn't rehydrate): reset the
 *     root stream (App's useCases clears activeCaseId, so the root becomes
 *     visible — it must be clean). Returns null.
 *   - Otherwise: if the in-flight turn was submitted from the ROOT (the
 *     job-0262 auto-create flow), ADOPT it into the opened Case — targetKey
 *     moves to the Case so the streaming envelopes that follow land in its
 *     stream — and clear the root buffer (the typed message is included in
 *     the rehydrated ``chat_history``; job-0262 persists the user turn
 *     BEFORE emitting case-open).
 *   - First open of a Case this session: build its stream from the
 *     rehydrated ``chat_history`` + persisted session charts.
 *   - Re-open of a Case already in the map: keep the in-memory buffer
 *     as-is (it holds everything the user saw — including live tool cards
 *     and anything buffered while they were away — and avoids the
 *     refetch repaint).
 *
 * Returns the opened case_id (or null).
 */
export function routeCaseOpen(
  cs: ChatStreams,
  p: CaseOpenEnvelopePayload,
): string | null {
  const session = p.session_state;
  if (!session) {
    clearRootStream(cs);
    return null;
  }
  const caseId = session.case.case_id;
  if (cs.targetKey === ROOT_STREAM_KEY) {
    // Adoption: a turn submitted from root belongs to the opened Case.
    cs.targetKey = caseId;
    clearRootStream(cs);
  }
  if (!cs.streams.has(caseId)) {
    const s = emptyStreamState();
    replayStreamFromChatHistory(s, session.chat_history ?? []);
    s.charts = chartsFromSession(session);
    cs.streams.set(caseId, s);
  }
  return caseId;
}

/**
 * job-0267 — rebuild a stream from the persisted FULL-stream chat history.
 *
 * The agent now persists three row kinds per turn (interleaved by
 * ``created_at``, which is the array order the server returns):
 *
 *   - ``role="user"`` / ``role="agent"`` → chat bubbles (agent rows carry
 *     the REAL accumulated narration since job-0267 — previously empty);
 *   - ``role="tool"`` + ``tool_card`` → one replayed inline tool card per
 *     dispatched registry tool (terminal state + authoritative job-0264
 *     ``duration_ms``).
 *
 * Tool rows synthesize a single-step ``PipelineStatePayload`` appended to
 * ``s.pipeline.history`` — the exact shape the live ``pipeline-state``
 * envelopes produce — so ``buildInterleavedStream`` renders replayed cards
 * through the SAME PipelineCard path as live ones (green/red tint +
 * duration). Seqs are recorded in a single ordered walk so cards interleave
 * between the bubbles exactly where they happened. Unknown roles (and tool
 * rows without the typed card) are skipped — no surprise rendering.
 */
export function replayStreamFromChatHistory(
  s: StreamState,
  chat: CaseChatMessageWire[],
): void {
  const messages: ChatMessage[] = [];
  const replayed: PipelineStatePayload[] = [];
  for (const m of chat) {
    if (m.role === "user" || m.role === "agent") {
      recordMessageSeqIn(s, m.message_id);
      messages.push({
        id: m.message_id,
        role: m.role,
        text: m.content ?? "",
        done: true,
      });
    } else if (m.role === "tool" && m.tool_card) {
      const card = m.tool_card;
      const snap: PipelineStatePayload = {
        pipeline_id: m.pipeline_id ?? `replay-${m.message_id}`,
        steps: [
          {
            step_id: `replay-${m.message_id}`,
            name: card.label ?? card.tool_name,
            tool_name: card.tool_name,
            state: card.state,
            started_at: card.started_at ?? null,
            duration_ms: card.duration_ms ?? null,
          },
        ],
      };
      recordPipelineStepSeqsIn(s, snap);
      replayed.push(snap);
    }
  }
  s.messages = messages;
  if (replayed.length > 0) {
    s.pipeline = { ...s.pipeline, history: replayed };
  }
}

// --- Mobile bottom sheet (job-0278) --------------------------------------- //
//
// On mobile (<768px, App passes mobile={true} from useIsMobile) the chat
// panel becomes a BOTTOM SHEET pinned to the bottom edge:
//
//   - collapsed: just the drag-handle row + the composer, full width;
//   - expanded:  ~70% viewport height with the full conversation scroll.
//
// PRESENTATION ONLY — the per-Case stream routing (job-0266/0277) is
// untouched: the same StreamState map, the same envelope handlers, the same
// scroll/auto-scroll machinery render inside the sheet. The conversation
// scroll area stays MOUNTED while collapsed (display:none) so stream state,
// scroll position, and auto-scroll behavior survive toggling.
//
// Helpers are exported for unit tests (Chat itself cannot mount in
// happy-dom — it opens a WebSocket — same pure-helper pattern as
// pipelineReducer / buildInterleavedStream).

/** Sheet height when expanded, as a CSS length. */
export const MOBILE_SHEET_EXPANDED_HEIGHT = "70vh";

/** Container style for the mobile bottom sheet (replaces the desktop
 * right-side panel style below the breakpoint).
 *
 * job-0284 — map-centric pass: the sheet is TRANSLUCENT in both states so
 * the map reads through it ("this is a map centric app"). Surface = the
 * job-0283 hairline family gradient, alpha-tuned per state: 0.58 collapsed
 * (mostly the opaque composer card anyway) / 0.68 expanded (enough scrim
 * for #eee message text over a light basemap — ~5.9:1 contrast).
 *
 * NO backdrop-filter here, EVER: a non-none backdrop-filter would make the
 * sheet the containing block for position:fixed descendants — ChartGallery
 * mounts INSIDE this container and must overlay the full viewport, not the
 * sheet (hazard documented by job-0283 at its two removal sites).
 * Translucency is rgba/alpha ONLY. */
export function mobileSheetContainerStyle(
  expanded: boolean,
): React.CSSProperties {
  const alpha = expanded ? 0.68 : 0.58;
  return {
    position: "absolute",
    left: 0,
    right: 0,
    bottom: 0,
    height: expanded ? MOBILE_SHEET_EXPANDED_HEIGHT : "auto",
    background: `linear-gradient(180deg, rgba(26,27,33,${alpha}) 0%, rgba(18,19,24,${alpha}) 100%)`,
    color: "#eee",
    borderRadius: "12px 12px 0 0",
    border: "1px solid rgba(255,255,255,0.10)",
    borderBottom: "none",
    boxShadow: "0 -4px 24px rgba(0,0,0,0.35)",
    display: "flex",
    flexDirection: "column",
    fontFamily: "system-ui, sans-serif",
    fontSize: 13,
    overflow: "hidden",
    // Above panels (z=20) + legend (z=10) + hamburgers (z=30); below the
    // mobile drawer backdrop (z=40) and inline gate cards (z=50).
    zIndex: 32,
  };
}

/** Desktop right-panel container (job-0283 sleekness pass). Surface family =
 * the job-0264 LayerPanel polish: gradient surface, hairline border, 12px
 * radius, soft shadow, backdrop blur — so the chat panel and the left rail
 * read as one family. Exported for unit tests (Chat itself cannot mount in
 * happy-dom — it opens a WebSocket — same pattern as
 * mobileSheetContainerStyle above).
 *
 * ux-batch-1 J1 — ``widthPx`` is the user's dragged column width. The width is
 * still clamped to the viewport (``min(width, 92vw)``) so a wide column can
 * never overrun a narrow desktop window. Position unchanged. */
export function desktopChatContainerStyle(
  widthPx: number = CHAT_WIDTH_DEFAULT_PX,
): React.CSSProperties {
  return {
    position: "absolute",
    right: 16,
    top: 16,
    bottom: 16,
    width: `min(${clampChatWidth(widthPx)}px, 92vw)`,
    background:
      "linear-gradient(180deg, rgba(26,27,33,0.96) 0%, rgba(18,19,24,0.96) 100%)",
    color: "#eee",
    borderRadius: 12,
    border: "1px solid rgba(255,255,255,0.06)",
    boxShadow: "0 8px 32px rgba(0,0,0,0.5)",
    // NO backdropFilter: it would make this panel the containing block for
    // position:fixed descendants — ChartGallery (mounted inside Chat) must
    // overlay the full viewport, not the column. The 0.96-alpha gradient hides
    // blur anyway (caught in the job-0283 screenshot pass).
    display: "flex",
    flexDirection: "column",
    fontFamily: "system-ui, sans-serif",
    fontSize: 13,
    overflow: "hidden",
    // ux-batch-1 J1 — no width transition: the column tracks the drag pointer
    // 1:1 (a transition would make the handle feel laggy/rubbery during a drag).
  };
}

export interface SheetToggleHandleProps {
  expanded: boolean;
  onToggle: () => void;
}

/** Full-width drag-handle row that toggles the sheet. 44px tall — Apple HIG
 * minimum touch target. job-0280: the handle bar is the SINGLE affordance —
 * the redundant chevron arrow under it is gone (user feedback); the whole
 * handle area stays tappable with the same aria labels. */
export function SheetToggleHandle({
  expanded,
  onToggle,
}: SheetToggleHandleProps): JSX.Element {
  return (
    <button
      data-testid="grace2-chat-sheet-toggle"
      aria-label={expanded ? "Collapse chat" : "Expand chat"}
      aria-expanded={expanded}
      onClick={onToggle}
      style={{
        flex: "0 0 auto",
        minHeight: 44,
        width: "100%",
        background: "none",
        border: "none",
        cursor: "pointer",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        padding: 0,
        color: "#888",
        fontFamily: "inherit",
      }}
    >
      <span
        aria-hidden="true"
        style={{
          display: "block",
          width: 40,
          height: 4,
          borderRadius: 2,
          // job-0284 — alpha-white so the bar reads on the translucent
          // sheet over any basemap (was solid #555 on the opaque sheet).
          background: "rgba(255,255,255,0.35)",
        }}
      />
    </button>
  );
}

// --- Collapsed-sheet active-tool strip (job-0280) ------------------------- //
//
// When the mobile sheet is COLLAPSED and a tool is RUNNING in the visible
// stream, a slim live-status strip renders directly ABOVE the composer: the
// running tool's humanized label + elapsed timer — the SAME data the inline
// PipelineCard shows, read from the SAME merged pipeline view-model
// (mergeStepsByStepId over history ∪ live) and the SAME timer hook
// (useRunningElapsedMs) — no forked pipeline logic. It disappears when no
// step is running; tapping it expands the sheet. Desktop never renders it
// (the strip is gated on the `mobile` prop + collapsed state in Chat).

/**
 * The most-recent RUNNING tool step across (history ∪ live), or null.
 *
 * "Most recent" = highest first-arrival seq in `stepOrder` (the job-0176
 * interleave ordering the cards themselves render by). The Gemini
 * `llm_generation` thinking pseudo-step is excluded — the strip is an
 * active-TOOL indicator; thinking has its own ephemeral surface
 * (`feedback_thinking_state_ephemeral`) inside the expanded scroll.
 * Pure helper, exported for unit tests.
 */
export function findRunningToolStep(
  history: PipelineStatePayload[],
  live: PipelineStatePayload | null,
  stepOrder: Map<string, number>,
): PipelineStepSummary | null {
  const merged = mergeStepsByStepId(history, live);
  let best: PipelineStepSummary | null = null;
  let bestSeq = -1;
  for (const step of merged) {
    if (isThinkingStep(step)) continue;
    if (step.state !== "running") continue;
    const seq =
      stepOrder.get(stepInterleaveKey(step)) ??
      Number.MAX_SAFE_INTEGER;
    if (seq >= bestSeq) {
      best = step;
      bestSeq = seq;
    }
  }
  return best;
}

export interface SheetActiveToolStripProps {
  /** The running step to surface (caller resolves via findRunningToolStep). */
  step: PipelineStepSummary;
  /** Tap target — expands the sheet so the user sees the full card. */
  onExpand: () => void;
}

/** Slim live-status strip for the collapsed mobile sheet. Reuses the
 * PipelineCard's humanized label, spinner, and running-elapsed timer. */
export function SheetActiveToolStrip({
  step,
  onExpand,
}: SheetActiveToolStripProps): JSX.Element {
  const reduced = prefersReducedMotion();
  const elapsedMs = useRunningElapsedMs(step);
  // The collapsed-sheet strip only ever shows a RUNNING tool, so the
  // present-tense running label is correct (job-0294 state-aware labels).
  const label = humanizeStepName(step.name, step.state);
  // F42 (job-0321) — the strip only ever surfaces a RUNNING tool, so the
  // label always gets the SAME animated rainbow-gradient treatment the inline
  // PipelineCard uses for running steps (background-clip:text technique). When
  // the user prefers reduced motion we fall back to the solid label color,
  // exactly like PipelineCard. The `grace2-hue-cycle` keyframe is injected
  // globally by PipelineCard's `ensureKeyframes()` side effect (runs on this
  // module's import of './components/PipelineCard'), so no keyframe work here.
  const labelStyle: React.CSSProperties = reduced
    ? { color: "#eee" }
    : {
        backgroundImage:
          "linear-gradient(90deg, #FF6B6B, #FFD93D, #6BCB77, #4D96FF, #B266FF, #FF6B6B)",
        backgroundSize: "300% 100%",
        WebkitBackgroundClip: "text",
        backgroundClip: "text",
        WebkitTextFillColor: "transparent",
        color: "transparent",
        animation: "grace2-hue-cycle 3s linear infinite",
      };
  return (
    <button
      data-testid="grace2-sheet-tool-strip"
      aria-label={`${label} — running. Expand chat`}
      onClick={onExpand}
      style={{
        flex: "0 0 auto",
        display: "flex",
        alignItems: "center",
        gap: 8,
        margin: "0 10px 8px",
        padding: "8px 12px",
        minHeight: 36,
        // job-0284 — its own translucent hairline card: the sheet behind it
        // is now see-through, so the strip carries its own scrim.
        background: "rgba(18,19,24,0.72)",
        border: "1px solid rgba(255,255,255,0.10)",
        borderRadius: 8,
        color: "#eee",
        fontSize: 12,
        lineHeight: "1.4",
        fontFamily: "ui-monospace, 'Cascadia Code', 'Fira Code', monospace",
        cursor: "pointer",
        textAlign: "left",
      }}
    >
      <span
        data-testid="grace2-sheet-tool-strip-label"
        style={{
          flex: 1,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
          ...labelStyle,
        }}
        title={label}
      >
        {label}
      </span>
      <span
        data-testid="grace2-sheet-tool-strip-timer"
        aria-hidden="true"
        style={{
          fontVariantNumeric: "tabular-nums",
          fontSize: 11,
          color: "rgba(255,255,255,0.55)",
          flexShrink: 0,
          minWidth: 30,
          textAlign: "right",
        }}
      >
        {formatDuration(elapsedMs)}
      </span>
      <Spinner reduced={reduced} />
    </button>
  );
}

// --- Props --------------------------------------------------------------- //

export interface ChatProps {
  wsUrl: string;
  /** Called when the user clicks the × close button (job-0068). */
  onClose?: () => void;
  /**
   * job-0266 — the active Case id (null = Cases root). Selects the VISIBLE
   * per-Case chat stream. App.tsx wires this from useCases; switching Cases
   * swaps the entire stream, navigating to root shows the clean root view.
   */
  activeCaseId?: string | null;
  /**
   * job-0278 — mobile presentation flag (App wires useIsMobile). When true
   * the panel renders as the bottom sheet described above. Default false:
   * the desktop right-side panel, pixel-identical to before.
   */
  mobile?: boolean;
  /**
   * job-0253b — re-sign-in reconnect epoch. App bumps this exactly once when a
   * fresh non-anonymous user recovers from the post-4401 auth-expired wedge
   * (closes OQ-0253-CHAT-WS-4401). Threading it into the ws effect's deps makes
   * Chat's own GraceWs instance tear its dead socket down and reconnect, so
   * Chat participates in the recovery alongside App's instance. Default 0;
   * never changes in disabled/dev mode (Firebase off → no authExpired → no
   * bump), so the effect runs exactly once as before.
   */
  authEpoch?: number;
  /**
   * ux-batch-1 J1 (F10) — optional controlled desktop chat width (px). App
   * lifts the width so dependent absolute-positioned chrome (inline-card stack,
   * payload-warning banner) can track the column edge. When provided it seeds
   * and mirrors the internal width; when omitted Chat reads/persists its own
   * width via localStorage. Ignored on mobile.
   */
  width?: number;
  /**
   * ux-batch-1 J1 — fired (with the new px width) whenever the user drags the
   * resize handle or nudges it with the keyboard, so App can mirror it.
   */
  onWidthChange?: (widthPx: number) => void;
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

export function Chat({
  wsUrl,
  onClose,
  activeCaseId = null,
  mobile = false,
  authEpoch = 0,
  width,
  onWidthChange,
}: ChatProps): JSX.Element {
  // job-0278 — mobile bottom-sheet expansion. Collapsed (composer only) by
  // default; presentation-only state, lives and dies with the Chat mount.
  const [sheetExpanded, setSheetExpanded] = useState<boolean>(false);
  // ux-batch-1 J1 (F10) — desktop chat-panel WIDTH is user-draggable (distinct
  // from the mobile sheet height). Persisted to localStorage so reloads
  // remember it. Read lazily so SSR / first paint don't touch localStorage
  // before hydration. Mobile ignores this entirely (full-viewport sheet).
  const [chatWidth, setChatWidth] = useState<number>(() =>
    mobile ? CHAT_WIDTH_DEFAULT_PX : (width ?? readChatWidth()),
  );
  // Mirror an externally-controlled width (App lifts it for dependent offsets +
  // the payload-warning banner). Skipped on mobile.
  useEffect(() => {
    if (!mobile && typeof width === "number") {
      setChatWidth(clampChatWidth(width));
    }
  }, [width, mobile]);
  // Latest width during a drag — onPointerUp persists from here so we don't
  // hammer localStorage on every pointermove.
  const chatWidthRef = useRef<number>(chatWidth);
  chatWidthRef.current = chatWidth;
  // Begin a left-border drag. The panel is anchored right:16, so the column
  // width is (viewportRight - 16) - pointerX; clamped to the allowed band.
  const beginWidthDrag = useCallback(
    (e: React.PointerEvent): void => {
      if (mobile) return;
      e.preventDefault();
      const onMove = (ev: PointerEvent): void => {
        const next = clampChatWidth(window.innerWidth - 16 - ev.clientX);
        chatWidthRef.current = next;
        setChatWidth(next);
        onWidthChange?.(next);
      };
      const onUp = (): void => {
        writeChatWidth(chatWidthRef.current);
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
        document.body.style.userSelect = "";
        document.body.style.cursor = "";
      };
      document.body.style.userSelect = "none";
      document.body.style.cursor = "ew-resize";
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
    },
    [mobile, onWidthChange],
  );
  // Keyboard a11y for the resize separator: arrows nudge the width in 24px
  // steps (wider = ArrowLeft, since the panel grows leftward).
  const nudgeWidth = useCallback(
    (deltaPx: number): void => {
      setChatWidth((prev) => {
        const next = clampChatWidth(prev + deltaPx);
        chatWidthRef.current = next;
        writeChatWidth(next);
        onWidthChange?.(next);
        return next;
      });
    },
    [onWidthChange],
  );
  // job-0266 — PER-CASE CHAT STREAMS. All conversational state (messages,
  // tool cards, charts, sandbox cards, errors, arrival-order maps) lives in
  // per-Case StreamState entries inside a ref-held ChatStreams map; React
  // re-renders are driven by a numeric tick bumped after every routed
  // envelope. The VISIBLE stream is selected by the activeCaseId prop.
  const streamsRef = useRef<ChatStreams>(createChatStreams());
  const [, bumpStreamTick] = useState<number>(0);
  const bump = useCallback(() => bumpStreamTick((n) => n + 1), []);

  const [status, setStatus] = useState<ConnectionStatus>("connecting");
  const [researchMode] = useState<ResearchMode>("research"); // toggle UI lands M3

  // sprint-13 job-0231 — gallery state for the full-viewport chart viewer.
  // UI state, not stream content; closed on stream swap so charts from the
  // outgoing Case don't linger in the overlay.
  const [galleryOpen, setGalleryOpen] = useState<boolean>(false);
  const [galleryCharts, setGalleryCharts] = useState<ChartPayload[]>([]);
  const [galleryInitialIndex, setGalleryInitialIndex] = useState<number>(0);

  const wsRef = useRef<GraceWs | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  // job-0266 — visible stream key + view-model for this render. getStream
  // lazily creates the entry; the ref-map mutation during render is
  // idempotent and safe.
  const visibleKey = streamKeyFor(activeCaseId);
  const visible = getStream(streamsRef.current, visibleKey);

  // job-0266 — navigating OUT of a Case to the root clears the visible
  // chat: the root view is always a clean empty composer (the Case's
  // stream persists server-side and in the in-memory map). Also closes the
  // chart gallery on any stream swap (it showed the outgoing stream's
  // charts).
  const prevVisibleKeyRef = useRef<string>(visibleKey);
  useEffect(() => {
    if (prevVisibleKeyRef.current === visibleKey) return;
    prevVisibleKeyRef.current = visibleKey;
    if (visibleKey === ROOT_STREAM_KEY) {
      clearRootStream(streamsRef.current);
    }
    setGalleryOpen(false);
    bump();
  }, [visibleKey, bump]);

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
    // job-0266 — every handler routes its envelope into the OWNING Case's
    // stream (ChatStreams.targetKey — the Case that was visible at submit
    // time, or the Case adopted by routeCaseOpen on the job-0262
    // auto-create flow) and bumps the render tick. Envelopes for a
    // non-visible Case buffer silently into that Case's stream.
    const ws = new GraceWs(wsUrl, {
      onStatus: (s) => setStatus(s),
      // job-0277: every streaming handler receives the envelope-level
      // case_id (the agent's turn pin) and routes to the OWNING stream;
      // untagged envelopes fall back to submit-time targetKey routing.
      onAgentChunk: (p: AgentMessageChunkPayload, caseId?: string | null) => {
        routeAgentChunk(streamsRef.current, p, caseId);
        bump();
      },
      onPipelineState: (p: PipelineStatePayload, caseId?: string | null) => {
        routePipelineState(streamsRef.current, p, caseId);
        bump();
      },
      onSessionState: (p: SessionStatePayload, caseId?: string | null) => {
        routeSessionState(streamsRef.current, p, caseId);
        bump();
      },
      // job-0266 (supersedes the job-0172 flush-and-rehydrate): case-open
      // creates / reuses the opened Case's stream in the map and handles the
      // job-0262 root-turn adoption. The VISIBLE stream swaps via the
      // activeCaseId prop, which App.tsx updates from the same envelope.
      onCaseOpen: (p: CaseOpenEnvelopePayload) => {
        routeCaseOpen(streamsRef.current, p);
        bump();
      },
      onError: (p: ErrorPayload, caseId?: string | null) => {
        routeError(streamsRef.current, p, caseId);
        bump();
      },
      // sprint-13 job-0231: chart-emission is in SESSION_SCOPED_TYPES, so
      // Chat receives it via the fan-out hub even when it was emitted on
      // App.tsx's connection. routeChartEmission de-dupes on chart_id.
      onChartEmission: (p: ChartPayload, caseId?: string | null) => {
        routeChartEmission(streamsRef.current, p, caseId);
        bump();
      },
      // sprint-13 job-0234: code-exec gate cards, now per-Case.
      onCodeExecRequest: (
        p: CodeExecRequestPayload,
        caseId?: string | null,
      ) => {
        routeCodeExecRequest(streamsRef.current, p, caseId);
        bump();
      },
      onCodeExecResult: (
        p: CodeExecResultPayload,
        caseId?: string | null,
      ) => {
        routeCodeExecResult(streamsRef.current, p, caseId);
        bump();
      },
    });
    wsRef.current = ws;
    ws.connect();
    return () => ws.close();
    // job-0253b — authEpoch bumps on a recovered re-sign-in so Chat's GraceWs
    // closes its dead post-4401 socket and reconnects (OQ-0253-CHAT-WS-4401).
    // Constant in disabled/dev mode → this effect still runs exactly once.
  }, [wsUrl, bump, authEpoch]);

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
      routePipelineState(streamsRef.current, p);
      bump();
    };
    return () => {
      delete window.__grace2InjectPipelineState;
    };
  }, [bump]);

  // job-0166 dev-only seam: inject an error envelope so Playwright can
  // verify Part 1 (running → failed force-transition on LLM_UNAVAILABLE /
  // tool TypeError) without a live agent failure.
  useEffect(() => {
    if (!import.meta.env.DEV) return;
    window.__grace2InjectError = (p) => {
      routeError(streamsRef.current, p);
      bump();
    };
    return () => {
      delete window.__grace2InjectError;
    };
  }, [bump]);

  // job-0266 dev-only seam: drive Chat's per-Case stream map with a
  // case-open without a live agent. The App-level __grace2InjectCaseOpen
  // seam reaches only useCases (App's GraceWs handler); Chat's stream map
  // hangs off Chat's own GraceWs handler, so UI snapshot scripts call BOTH
  // seams to simulate the full envelope fan-out. Per
  // `feedback_playwright_must_drive_live_agent` this seam is INVALID for
  // end-to-end verification; only UI snapshots + unit tests may use it.
  useEffect(() => {
    if (!import.meta.env.DEV) return;
    (window as unknown as Record<string, unknown>).__grace2InjectCaseOpenChat =
      (p: CaseOpenEnvelopePayload) => {
        routeCaseOpen(streamsRef.current, p);
        bump();
      };
    return () => {
      delete (window as unknown as Record<string, unknown>)
        .__grace2InjectCaseOpenChat;
    };
  }, [bump]);

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
      // Drive Chat state first (job-0266: routed to the owning stream).
      routeChartEmission(streamsRef.current, p);
      bump();
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
  }, [bump]);

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
      // job-0266 — same routed path as real envelopes: request + result +
      // decision land in the OWNING (targetKey) stream.
      routeCodeExecRequest(streamsRef.current, request);
      if (result !== undefined) {
        routeCodeExecResult(streamsRef.current, result);
      }
      if (decision !== undefined) {
        recordSandboxDecision(
          streamsRef.current,
          streamsRef.current.targetKey,
          request.code_exec_id,
          decision,
        );
      }
      bump();
    };
    return () => {
      delete (window as unknown as Record<string, unknown>).__grace2InjectCodeExec;
    };
  }, [bump]);

  // Auto-scroll on new content only when the user is already at the bottom.
  // This preserves the user's reading position when they've scrolled up to
  // read history while the stream is still landing new tokens.
  //
  // job-0266 — dependencies are the VISIBLE stream's fields (route* replaces
  // the field identity on every update), so an envelope buffered into a
  // non-visible Case's stream does NOT scroll the visible one. A stream
  // swap (visibleKey change) also re-fires, snapping the newly visible
  // stream to its bottom.
  useEffect(() => {
    if (scrollRef.current && atBottomRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [
    visibleKey,
    visible.messages,
    visible.pipeline,
    visible.charts,
    visible.sandboxRequests,
    visible.sandboxResults,
  ]);

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
  // job-0266 — the decision is recorded against the VISIBLE stream (the
  // card the user clicked lives there).
  function handleSandboxDecide(codeExecId: string, decision: SandboxCardDecision): void {
    recordSandboxDecision(streamsRef.current, visibleKey, codeExecId, decision);
    bump();
    wsRef.current?.sendPayloadConfirmation(
      codeExecId,
      decision === "proceed" ? "proceed" : "cancel",
      null,
    );
  }

  function submit(text: string): void {
    if (!text || !wsRef.current) return;
    // job-0278 — submitting from the collapsed mobile sheet expands it so
    // the user sees the response stream in (presentation only).
    if (mobile && !sheetExpanded) setSheetExpanded(true);
    // job-0266 — the user bubble lands in the VISIBLE stream, which also
    // takes ownership of the turn's streaming envelopes (targetKey).
    routeUserMessage(streamsRef.current, visibleKey, text);
    bump();
    wsRef.current.sendUserMessage(text, researchMode);
  }

  function cancel(): void {
    wsRef.current?.sendCancel("user-cancel");
  }

  // job-0266 — render view-model = the visible Case's stream.
  const messages = visible.messages;
  const pipeline = visible.pipeline;
  const charts = visible.charts;
  const sandboxRequests = visible.sandboxRequests;
  const lastError = visible.lastError;

  const showCancel = shouldShowCancel(pipeline);
  const liveSteps = pipeline.live?.steps ?? [];
  // job-0280 — collapsed-sheet active-tool strip: resolved from the SAME
  // merged pipeline view-model the inline cards render (no forked logic).
  // Null whenever the sheet is expanded / desktop / nothing running.
  const collapsedRunningStep: PipelineStepSummary | null =
    mobile && !sheetExpanded
      ? findRunningToolStep(
          pipeline.history,
          pipeline.live,
          visible.stepOrder,
        )
      : null;
  // Merged send/stop control: in-flight whenever the cancel predicate fires
  // (any running step in the live pipeline, OR a non-null
  // session-state.current_pipeline). Returns to idle on terminal /
  // cancelled pipeline-state per the existing pipelineReducer.
  const inputState: ChatInputState = showCancel ? "in-flight" : "idle";
  const inputDisabled = status !== "connected";

  // job-0278 — desktop panel vs mobile bottom sheet. Every mobile divergence
  // is behind the `mobile` prop; the desktop style lives in the exported
  // desktopChatContainerStyle below (job-0283). ux-batch-1 J1 — the desktop
  // column width is the user-dragged chatWidth (px).
  const containerStyle: React.CSSProperties = mobile
    ? mobileSheetContainerStyle(sheetExpanded)
    : desktopChatContainerStyle(chatWidth);

  return (
    <div
      data-testid="grace2-chat"
      data-stream-key={visibleKey}
      data-sheet-state={mobile ? (sheetExpanded ? "expanded" : "collapsed") : undefined}
      style={containerStyle}
    >
      {/* ux-batch-1 J1 (F10) — desktop left-border resize grab strip. Anchored
          at the panel's left edge; dragging it sizes the column (the panel is
          right-anchored, so dragging left widens). role=separator + arrow-key
          nudge for keyboard a11y. Mobile (full-width sheet) renders nothing. */}
      {!mobile && (
        <div
          data-testid="grace2-chat-resize-handle"
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize chat panel (drag, or use arrow keys)"
          tabIndex={0}
          onPointerDown={beginWidthDrag}
          onKeyDown={(e) => {
            // Panel grows leftward, so ArrowLeft = wider, ArrowRight = narrower.
            if (e.key === "ArrowLeft") { e.preventDefault(); nudgeWidth(24); }
            else if (e.key === "ArrowRight") { e.preventDefault(); nudgeWidth(-24); }
          }}
          style={{
            position: "absolute",
            left: 0,
            top: 0,
            bottom: 0,
            width: 6,
            cursor: "ew-resize",
            zIndex: 6,
            touchAction: "none",
          }}
        />
      )}
      {/* job-0278 — mobile drag-handle / chevron toggle, first child so it
          reads as the sheet's grab area. Desktop renders nothing here. */}
      {mobile && (
        <SheetToggleHandle
          expanded={sheetExpanded}
          onToggle={() => setSheetExpanded((v) => !v)}
        />
      )}
      <header
        style={{
          // job-0283 — desktop gets the family hairline divider + LayerPanel
          // header padding. job-0284 — the mobile divider joins the hairline
          // family too (#333 read as a solid slab line on the now-translucent
          // sheet).
          padding: mobile ? "10px 12px" : "12px 14px",
          borderBottom: mobile
            ? "1px solid rgba(255,255,255,0.08)"
            : "1px solid rgba(255,255,255,0.06)",
          // job-0278 — collapsed mobile sheet shows only handle + composer;
          // the header (and scroll area below) hide but stay mounted.
          display: mobile && !sheetExpanded ? "none" : "flex",
          alignItems: "center",
          gap: 8,
        }}
      >
        <strong style={{ fontSize: 14 }}>GRACE-2</strong>
        <span
          data-testid="grace2-build-version"
          title="build version — tells you which deploy this tab is running"
          style={{ color: "#888", fontSize: 11 }}
        >
          {BUILD_VERSION}
        </span>
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
        {/* ux-batch-1 J1 — the large/normal width TOGGLE was removed in favour
            of a drag-to-resize left border (the grace2-chat-resize-handle
            below). Width now persists as a continuous px value. */}
        {onClose && !mobile && (
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
          // job-0278 — on mobile the composer is in normal flow below the
          // scroll area (not a floating overlay), so the overlay-clearing
          // bottom padding isn't needed. Collapsed sheet hides the scroll
          // area entirely (stays mounted — stream + scroll state survive).
          padding: mobile
            ? "4px 12px 12px 12px"
            : `12px 12px ${inputHeightPx + INPUT_GAP_PX}px 12px`,
          display: mobile && !sheetExpanded ? "none" : "flex",
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
          messageOrder={visible.messageOrder}
          stepOrder={visible.stepOrder}
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
            visible.messageOrder,
            visible.stepOrder,
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
            const sa = visible.sandboxSeqs.get(a.code_exec_id) ?? Number.MAX_SAFE_INTEGER;
            const sb = visible.sandboxSeqs.get(b.code_exec_id) ?? Number.MAX_SAFE_INTEGER;
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
                  result={visible.sandboxResults.get(req.code_exec_id)}
                  decided={visible.sandboxDecisions.get(req.code_exec_id) ?? null}
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
          // job-0278 — hidden while the mobile sheet is collapsed (the
          // scroll area it serves is hidden too).
          display: mobile && !sheetExpanded ? "none" : "flex",
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

      {/* ---- Collapsed-sheet active-tool strip (job-0280) ----                  */}
      {/* Mobile + collapsed + a tool running: slim live-status strip directly    */}
      {/* above the composer (humanized label + elapsed timer, same data as the   */}
      {/* PipelineCard). Tap = expand the sheet. Gone when nothing is running.    */}
      {collapsedRunningStep && (
        <SheetActiveToolStrip
          step={collapsedRunningStep}
          onExpand={() => setSheetExpanded(true)}
        />
      )}

      {/* ---- Overlay input wrapper (job-0144 + job-0153) ----                    */}
      {/* Floats at the bottom of the chat panel; the scroll above has matching   */}
      {/* bottom-padding (driven by onHeightChange) so messages and inline cards  */}
      {/* are never hidden behind it, even when the textarea grows multi-line.    */}
      <div
        data-testid="chat-input-overlay"
        style={
          mobile
            ? {
                // job-0278 — in normal flow on mobile so the collapsed
                // sheet's height is handle + composer. safe-area inset
                // clears the iOS home indicator.
                flex: "0 0 auto",
                padding: "0 10px calc(10px + env(safe-area-inset-bottom)) 10px",
                pointerEvents: "auto",
                zIndex: 3,
              }
            : {
                position: "absolute",
                left: 12,
                right: 12,
                bottom: 12,
                pointerEvents: "auto",
                zIndex: 3,
              }
        }
      >
        {/* job-0266 — keyed by the visible stream so navigating between
            Cases / root remounts the composer with an empty draft ("clean
            empty composer" per the per-Case product shape). */}
        <ChatInput
          key={visibleKey}
          state={inputState}
          onSubmit={submit}
          onCancel={cancel}
          disabled={inputDisabled}
          onHeightChange={handleInputHeightChange}
          /* job-0278 — 16px on mobile prevents the iOS focus auto-zoom;
             desktop keeps the historical 14px default. */
          fontSizePx={mobile ? 16 : 14}
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

  // Second-pass: collapse by (name|tool_name) — but ONLY for the
  // llm_generation thinking pseudo-step, which the agent reissues with a fresh
  // step_id per pipeline_id and which must stay ONE transitioning indicator at
  // its original position. ux-batch-1 J9 (F18): regular TOOL steps are NOT
  // collapsed here — each unique step_id is its own card, so re-running the
  // same tool in a later turn renders as a NEW card (it used to collapse into
  // the earlier run's position, the "card shows up behind the last prompt"
  // bug). Pass-1 (step_id) already collapses a single tool's within-turn
  // running→complete reissues, so non-thinking steps never duplicate here.
  const byThinkingKey = new Map<string, number>(); // thinking key → result idx
  const result: PipelineStepSummary[] = [];
  for (const s of merged) {
    if (!isThinkingStep(s)) {
      result.push(s);
      continue;
    }
    const key = `thinking|${s.tool_name}`;
    const prevIdx = byThinkingKey.get(key);
    if (prevIdx === undefined) {
      byThinkingKey.set(key, result.length);
      result.push(s);
    } else {
      // Latest thinking state wins at the original position.
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
      // stepKey is stepInterleaveKey(step): the unique step_id for tool steps
      // (so a re-run in a later turn is its own card) and a stable
      // ``thinking|<tool>`` key for the llm_generation pseudo-step. Matches what
      // recordPipelineStepSeqs records so the row's position is stable across a
      // single step's pipeline_id reissues + state transitions (ux-batch-1 J9).
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
    const key = stepInterleaveKey(step);
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
