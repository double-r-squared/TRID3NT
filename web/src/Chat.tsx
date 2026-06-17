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
  CredentialRequestPayload,
  ErrorPayload,
  PayloadConfirmationDecision,
  PayloadWarningEnvelopePayload,
  PipelineSnapshot,
  PipelineStatePayload,
  PipelineStepSummary,
  RegionCandidate,
  RegionChoiceRequestPayload,
  ResearchMode,
  SessionStatePayload,
  SolveProgressPayload,
} from "./contracts";
import { regionChoiceBus } from "./lib/region_choice_bus";
import {
  PipelineCard,
  Spinner,
  formatDuration,
  humanizeStepName,
  prefersReducedMotion,
  useRunningElapsedMs,
} from "./components/PipelineCard";
import { ChatInput, ChatInputState } from "./components/ChatInput";
import { IconChevronRight, IconSandbox } from "./components/icons";
import { AgentMessage } from "./components/AgentMessage";
import { UserBubble } from "./components/UserBubble";
import { ScrollToBottom } from "./components/ScrollToBottom";
import { ThinkingIndicator } from "./components/ThinkingIndicator";
import { ChartStack, type ChartPayload } from "./components/ChartStack";
import { ChartGallery } from "./components/ChartGallery";
import { SandboxCard, type CodeExecRequestPayload, type CodeExecResultPayload, type SandboxCardDecision } from "./components/SandboxCard";
import { CredentialCard } from "./components/CredentialCard";
import { RegionPickerCard } from "./components/RegionPickerCard";
import { PayloadWarningInline } from "./components/PayloadWarningInline";

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

// --- Live big-sim solve-progress → step matching (NATE 2026-06-17) -------- //
//
// The `solve-progress` envelope carries a `run_id` + `solver` family, NOT a
// `step_id`. To paint the readout on the right card we match a solve-progress
// payload to the currently-RUNNING heavy-solver step. The set below is the
// step `name` / `tool_name` vocabulary that maps to an external heavy solve
// (SFINCS / MODFLOW / Pelicun on the AWS Batch substrate). Any other tool step
// never carries a readout (matchSolveForStep returns null), so a fetch/clip
// card stays clean.
const SOLVER_STEP_NAMES: ReadonlySet<string> = new Set([
  "run_model_flood_scenario",
  "run_model_flood_habitat_scenario",
  "run_model_nws_flood_event_scenario",
  "run_model_groundwater_contamination_scenario",
  "run_modflow_job",
  "run_pelicun_damage_assessment",
  "run_solver",
  "wait_for_completion",
]);

/** True when the step is a heavy external solver that can carry a live readout. */
export function isSolverStep(step: PipelineStepSummary): boolean {
  return (
    SOLVER_STEP_NAMES.has(step.name) || SOLVER_STEP_NAMES.has(step.tool_name)
  );
}

/**
 * Pick the live solve-progress payload to render on a step's card, or null.
 *
 * Only RUNNING solver steps get a readout. When the stream has tracked solve
 * runs, we prefer a payload whose `solver` family is named in the step's
 * humanized label vocabulary (so an SFINCS readout doesn't paint onto a
 * concurrent MODFLOW card); absent a family match we fall back to the single
 * tracked run (the common case — one heavy solve at a time). With multiple
 * untyped runs and no family hint we decline rather than guess (return null).
 */
export function matchSolveForStep(
  step: PipelineStepSummary,
  solveProgress: Map<string, SolveProgressPayload> | undefined | null,
): SolveProgressPayload | null {
  if (!solveProgress || solveProgress.size === 0) return null;
  if (step.state !== "running" || !isSolverStep(step)) return null;
  const runs = [...solveProgress.values()];
  // Family hint from the step name: SFINCS/flood, MODFLOW/groundwater, Pelicun.
  const name = `${step.name} ${step.tool_name}`.toLowerCase();
  const familyHints: Array<[string[], string[]]> = [
    [["flood", "sfincs"], ["sfincs"]],
    [["groundwater", "modflow"], ["modflow"]],
    [["pelicun", "damage"], ["pelicun"]],
  ];
  for (const [stepKeywords, solverKeywords] of familyHints) {
    if (stepKeywords.some((k) => name.includes(k))) {
      const m = runs.find((r) =>
        solverKeywords.some((sk) => r.solver.toLowerCase().includes(sk)),
      );
      if (m) return m;
    }
  }
  // No family match: the single-run common case is unambiguous; otherwise
  // decline rather than paint a possibly-wrong run onto this card.
  return runs.length === 1 ? runs[0]! : null;
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

// --- Chat opacity (F56, job-0322) ---------------------------------------- //
//
// The chat surface translucency is now a USER preference (a Settings control,
// not a per-Case setting) so the user can dial how much of the map reads
// through the chat panel. Chat.tsx OWNS the shared persist key + the tier
// model; Group D's SettingsPopup IMPORTS readChatOpacity / writeChatOpacity
// and calls them — keep these exports stable + side-effect-free.
//
// PER-USER persistence (one localStorage key, NOT keyed by case_id). Three
// tiers map to per-surface alpha bands. The historical (pre-F56) alphas were
// ~0.96 desktop / ~0.58 mobile-collapsed / ~0.68 mobile-expanded; user
// feedback wanted the DEFAULT (medium) MORE opaque / frosted than that, so
// the medium band sits ABOVE the old values:
//
//   ┌────────┬──────────┬──────────────────┬────────────────┐
//   │ tier   │ desktop  │ mobile collapsed │ mobile expanded│
//   ├────────┼──────────┼──────────────────┼────────────────┤
//   │ low    │ 0.80     │ 0.55             │ 0.62           │  (most see-through)
//   │ medium │ 0.99     │ 0.78             │ 0.86           │  ← DEFAULT (frosted)
//   │ high   │ 1.00     │ 0.94             │ 0.97           │  (most opaque)
//   └────────┴──────────┴──────────────────┴────────────────┘
//
// "low" preserves the map-centric translucent feel; "medium" (default) is a
// frosted scrim the user reads comfortably over any basemap; "high" is a
// near-solid panel. Mobile alphas stay BELOW desktop so the bottom sheet
// keeps the see-through character even at "high".
export type ChatOpacityTier = "low" | "medium" | "high";

export const CHAT_OPACITY_TIERS: ChatOpacityTier[] = ["low", "medium", "high"];

/** Default opacity tier — MEDIUM (frosted), per F56. */
export const CHAT_OPACITY_DEFAULT: ChatOpacityTier = "medium";

/** Shared persist key. SettingsPopup writes via writeChatOpacity, Chat reads
 * via readChatOpacity — both go through this single per-user key. */
export const LS_CHAT_OPACITY = "grace2.chatOpacityTier";

/**
 * F56 reactivity bus (job-0322 fix) — a plain ``localStorage.setItem`` does
 * NOT fire the ``storage`` event in the SAME tab (the spec only fires it in
 * OTHER tabs/windows), so SettingsPopup writing the tier could never reach the
 * mounted Chat live. ``writeChatOpacity`` therefore ALSO dispatches this custom
 * window event after persisting; Chat subscribes (addEventListener) and
 * re-reads + re-applies the alpha bands to BOTH the desktop container and the
 * mobile bottom-sheet INSTANTLY — no reload, no remount. The event carries the
 * new tier in ``detail`` so subscribers can update without re-reading
 * localStorage, but Chat re-reads (single source of truth) regardless. */
export const CHAT_OPACITY_CHANGED_EVENT = "grace2:chat-opacity-changed";

/** Per-surface alpha bands per tier. The three surfaces the chat paints:
 *  desktop right-panel gradient, mobile bottom-sheet COLLAPSED gradient,
 *  mobile bottom-sheet EXPANDED gradient. Documented mapping table above. */
export interface ChatOpacityAlphas {
  desktop: number;
  mobileCollapsed: number;
  mobileExpanded: number;
}

const CHAT_OPACITY_BANDS: Record<ChatOpacityTier, ChatOpacityAlphas> = {
  low: { desktop: 0.8, mobileCollapsed: 0.55, mobileExpanded: 0.62 },
  medium: { desktop: 0.99, mobileCollapsed: 0.78, mobileExpanded: 0.86 },
  high: { desktop: 1.0, mobileCollapsed: 0.94, mobileExpanded: 0.97 },
};

/** Normalize an arbitrary value to a valid tier; junk/unset → MEDIUM. Pure. */
export function clampChatOpacityTier(value: unknown): ChatOpacityTier {
  return value === "low" || value === "medium" || value === "high"
    ? value
    : CHAT_OPACITY_DEFAULT;
}

/** Resolve a tier to its per-surface alpha band. Pure; exported for tests. */
export function chatOpacityAlphas(tier: ChatOpacityTier): ChatOpacityAlphas {
  return CHAT_OPACITY_BANDS[clampChatOpacityTier(tier)];
}

/** Read the persisted chat-opacity tier (per-user). Unset / garbage /
 * localStorage failure → MEDIUM. Side-effect-free. */
export function readChatOpacity(): ChatOpacityTier {
  try {
    return clampChatOpacityTier(localStorage.getItem(LS_CHAT_OPACITY));
  } catch {
    return CHAT_OPACITY_DEFAULT;
  }
}

/** Persist the chat-opacity tier (per-user). Non-fatal on failure. An
 * out-of-range value is normalized to MEDIUM before writing. Also dispatches
 * ``CHAT_OPACITY_CHANGED_EVENT`` on ``window`` so a mounted Chat in the SAME
 * tab re-applies the new alpha LIVE (the ``storage`` event only fires in OTHER
 * tabs, so it cannot drive same-tab reactivity — see the event doc above). */
export function writeChatOpacity(tier: ChatOpacityTier): void {
  const normalized = clampChatOpacityTier(tier);
  try {
    localStorage.setItem(LS_CHAT_OPACITY, normalized);
  } catch {
    /* non-fatal */
  }
  // Notify same-tab subscribers (Chat). Guarded: window may be absent in SSR /
  // non-DOM test contexts, and CustomEvent may be undefined in older runtimes.
  try {
    if (typeof window !== "undefined" && typeof CustomEvent === "function") {
      window.dispatchEvent(
        new CustomEvent<ChatOpacityTier>(CHAT_OPACITY_CHANGED_EVENT, {
          detail: normalized,
        }),
      );
    }
  } catch {
    /* non-fatal */
  }
}

// --- Mobile sheet bottom clearance (F61, job-0330) ----------------------- //
//
// F61 — the sheet must clear the iPhone's naturally-curved corners + home
// indicator. We lift the WHOLE sheet container off the bottom edge by the
// device's safe-area inset PLUS a few extra px so the rounded composer never
// kisses the curved glass / gets clipped by the home-indicator pill. This is
// applied as the container's bottom OFFSET (so the sheet floats up) AND the
// composer keeps its own inner safe-area padding (belt-and-suspenders for the
// keyboard accessory). The drag-resize clamp (clampSheetHeight, vh-based) is
// unaffected — the offset is a fixed pixel lift, not part of the height band.
export const SHEET_BOTTOM_EXTRA_PX = 10;

/** F61 — the sheet container's bottom offset: the device safe-area inset plus
 * a few extra px so the sheet floats clear of the curved corners / home
 * indicator. Used as `bottom` on the mobile container. A CSS `calc()` string
 * so it resolves on-device (env() is 0 on non-notched screens, degrading to a
 * small constant lift). Exported for unit tests. */
export const SHEET_BOTTOM_OFFSET_CSS = `calc(env(safe-area-inset-bottom) + ${SHEET_BOTTOM_EXTRA_PX}px)`;

// --- Mobile sheet height (F44, job-0322) --------------------------------- //
//
// The mobile bottom-sheet's EXPANDED height is now user-DRAGGABLE: the user
// grabs the handle and drags vertically to size the sheet, persisted to
// localStorage. This mirrors the desktop drag-to-resize width model
// (clampChatWidth / readChatWidth / writeChatWidth) but for the sheet's
// height, expressed as a fraction of the viewport (vh) so it tracks across
// device rotations / different screens. The handle ALSO still tap-to-folds —
// drag vs tap is distinguished by a movement threshold (see
// isSheetDragGesture) so a clean tap collapses the sheet as before.
const SHEET_HEIGHT_DEFAULT_VH = 70; // historical MOBILE_SHEET_EXPANDED_HEIGHT
const SHEET_HEIGHT_MIN_VH = 30; // never smaller than ~a few cards + composer
const SHEET_HEIGHT_MAX_VH = 92; // leave a sliver of map above the sheet
const LS_SHEET_HEIGHT = "grace2.chatSheetHeightVh";

/** Clamp a desired sheet height (vh) to the allowed [min, max] band. NaN /
 * non-finite inputs fall back to the default. Pure — exported for tests. */
export function clampSheetHeight(vh: number): number {
  if (!Number.isFinite(vh)) return SHEET_HEIGHT_DEFAULT_VH;
  return Math.max(
    SHEET_HEIGHT_MIN_VH,
    Math.min(SHEET_HEIGHT_MAX_VH, Math.round(vh)),
  );
}

/** Read the persisted mobile sheet height (vh). Unset / garbage /
 * localStorage failure → the historical 70vh default. */
export function readSheetHeight(): number {
  try {
    const raw = localStorage.getItem(LS_SHEET_HEIGHT);
    if (raw === null) return SHEET_HEIGHT_DEFAULT_VH;
    return clampSheetHeight(Number(raw));
  } catch {
    return SHEET_HEIGHT_DEFAULT_VH;
  }
}

/** Persist the mobile sheet height (vh). Non-fatal on failure. */
export function writeSheetHeight(vh: number): void {
  try {
    localStorage.setItem(LS_SHEET_HEIGHT, String(clampSheetHeight(vh)));
  } catch {
    /* non-fatal */
  }
}

// F44 — distinguish a vertical DRAG (resize) from a TAP (collapse toggle) on
// the sheet handle by the pointer's total travel. A movement at or beyond this
// many CSS pixels in EITHER axis is treated as a drag (it resized the sheet);
// anything smaller is a tap (it toggles collapse). Keeps tap-to-fold working
// without a separate chevron.
export const SHEET_DRAG_THRESHOLD_PX = 6;

/** True iff a pointer gesture that travelled (dx, dy) px counts as a DRAG
 * (vs a tap). Uses the max-axis travel so a mostly-vertical resize and a
 * mostly-horizontal stray both register. Pure — exported for tests. */
export function isSheetDragGesture(dx: number, dy: number): boolean {
  return Math.max(Math.abs(dx), Math.abs(dy)) >= SHEET_DRAG_THRESHOLD_PX;
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
  // Credential-request cards (SRS §F.3 amendment). A keyed tool paused on a
  // missing/invalid credential; the user saves the key via the existing
  // secret-add path then signals retry via credential-provided.
  credentialRequests: CredentialRequestPayload[];
  /** Resolved state per request_id once the user saves / declines. */
  credentialResolved: Map<string, "saved" | "declined">;
  /** First-arrival seq per credential request_id (chronological interleave). */
  credentialSeqs: Map<string, number>;
  // Large-payload warning cards (FIX 2, NATE 2026-06-17). The agent's payload
  // estimator projected a response over the warning threshold (>25 MB) and
  // paused the tool; the user answers Proceed / Cancel (/ Narrow scope) inline.
  // Mirrors the credential-card family exactly: the warning is an in-chat card
  // interleaved at its arrival position, NOT a separate banner "hat".
  payloadWarnings: PayloadWarningEnvelopePayload[];
  /** Resolved decision per warning_id once the user answers (Proceed/Cancel/…). */
  payloadResolved: Map<string, PayloadConfirmationDecision>;
  /** First-arrival seq per warning_id (chronological interleave). */
  payloadSeqs: Map<string, number>;
  // Region-disambiguation picker cards (state-bbox-fallback narrowing). A
  // `geocode_location` result snapped to a whole-state bbox and the agent is
  // offering a narrower county pick; the user picks a region (in the card list
  // OR on the synced map choropleth) or keeps the whole-state default. Mirrors
  // the credential-card family exactly: the picker is an in-chat card
  // interleaved at its arrival position.
  regionChoices: RegionChoiceRequestPayload[];
  /** Resolved choice + picked region per request_id once the user answers. */
  regionResolved: Map<string, { choice: "region" | "whole_state"; regionId: string | null }>;
  /** First-arrival seq per region-choice request_id (chronological interleave). */
  regionSeqs: Map<string, number>;
  // Live big-sim solve-progress (NATE 2026-06-17), keyed by run_id. The agent
  // streams `solve-progress` envelopes while a heavy solver burns wall-clock;
  // the latest payload per run_id is matched to the currently-running solver
  // step and rendered inline on its PipelineCard. Replace-in-place per run_id
  // (each new envelope supersedes the prior for that run). Never cleared on
  // completion explicitly — the readout only paints while the step is RUNNING,
  // so a stale entry simply stops surfacing once the step settles.
  solveProgress: Map<string, SolveProgressPayload>;
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
    credentialRequests: [],
    credentialResolved: new Map(),
    credentialSeqs: new Map(),
    payloadWarnings: [],
    payloadResolved: new Map(),
    payloadSeqs: new Map(),
    regionChoices: [],
    regionResolved: new Map(),
    regionSeqs: new Map(),
    solveProgress: new Map(),
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

/** NATE 2026-06-17 — live big-sim readout. Store the latest solve-progress for
 * a run_id in the OWNING stream (replace-in-place per run). Rendered inline on
 * the running solver step's PipelineCard via matchSolveForStep. A fresh Map is
 * assigned so React's referential-equality bump sees the change. */
export function routeSolveProgress(
  cs: ChatStreams,
  p: SolveProgressPayload,
  caseId?: string | null,
): void {
  const s = getStream(cs, owningKey(cs, caseId));
  const next = new Map(s.solveProgress);
  next.set(p.run_id, p);
  s.solveProgress = next;
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

/**
 * Route a `credential-request` envelope (SRS §F.3 amendment) into the OWNING
 * stream — the Case whose keyed tool dispatch paused. credential-request is
 * session-scoped (ws.ts SESSION_SCOPED_TYPES) so it fans out to Chat's
 * GraceWs even when the paused tool ran on App.tsx's connection. De-duped on
 * request_id so a duplicate fan-out emit doesn't stack a second card.
 */
export function routeCredentialRequest(
  cs: ChatStreams,
  p: CredentialRequestPayload,
  caseId?: string | null,
): void {
  const s = getStream(cs, owningKey(cs, caseId));
  if (s.credentialRequests.some((r) => r.request_id === p.request_id)) return;
  if (!s.credentialSeqs.has(p.request_id)) {
    s.arrivalSeq += 1;
    s.credentialSeqs.set(p.request_id, s.arrivalSeq);
  }
  s.credentialRequests = [...s.credentialRequests, p];
}

/**
 * Record the user's resolution of a credential prompt against the stream the
 * card lives in. "saved" = key persisted + agent signalled to retry;
 * "declined" = user skipped (agent narrates honestly + abandons the tool).
 */
export function recordCredentialResolved(
  cs: ChatStreams,
  key: string,
  requestId: string,
  resolved: "saved" | "declined",
): void {
  const s = getStream(cs, key);
  const next = new Map(s.credentialResolved);
  next.set(requestId, resolved);
  s.credentialResolved = next;
}

/**
 * FIX 2 (NATE 2026-06-17) — route a `tool-payload-warning` envelope into the
 * OWNING stream so it renders as an IN-CHAT card interleaved at its arrival
 * position (NOT the old App-level banner "hat"). Mirrors routeCredentialRequest
 * exactly: tool-payload-warning is session-scoped (ws.ts SESSION_SCOPED_TYPES)
 * so it fans out to Chat's GraceWs even when the paused tool ran on App.tsx's
 * connection. De-duped on warning_id so a duplicate fan-out emit doesn't stack
 * a second card.
 */
export function routePayloadWarning(
  cs: ChatStreams,
  p: PayloadWarningEnvelopePayload,
  caseId?: string | null,
): void {
  const s = getStream(cs, owningKey(cs, caseId));
  if (s.payloadWarnings.some((w) => w.warning_id === p.warning_id)) return;
  if (!s.payloadSeqs.has(p.warning_id)) {
    s.arrivalSeq += 1;
    s.payloadSeqs.set(p.warning_id, s.arrivalSeq);
  }
  s.payloadWarnings = [...s.payloadWarnings, p];
}

/**
 * Record the user's resolution of a payload-warning card against the stream the
 * card lives in. The decision (proceed / cancel / narrow_scope) is sent to the
 * agent via GraceWs.sendPayloadConfirmation by the caller; this only marks the
 * card resolved so it disables its actions + reads as answered in place.
 */
export function recordPayloadResolved(
  cs: ChatStreams,
  key: string,
  warningId: string,
  decision: PayloadConfirmationDecision,
): void {
  const s = getStream(cs, key);
  const next = new Map(s.payloadResolved);
  next.set(warningId, decision);
  s.payloadResolved = next;
}

/**
 * Route a `region-choice-request` envelope (state-bbox-fallback narrowing) into
 * the OWNING stream — the Case whose geocode tool paused. region-choice-request
 * is session-scoped (ws.ts SESSION_SCOPED_TYPES) so it fans out to Chat's
 * GraceWs even when the paused geocode ran on App.tsx's connection. De-duped on
 * request_id so a duplicate fan-out emit doesn't stack a second card. Mirrors
 * routeCredentialRequest exactly.
 */
export function routeRegionChoice(
  cs: ChatStreams,
  p: RegionChoiceRequestPayload,
  caseId?: string | null,
): void {
  const s = getStream(cs, owningKey(cs, caseId));
  if (s.regionChoices.some((r) => r.request_id === p.request_id)) return;
  if (!s.regionSeqs.has(p.request_id)) {
    s.arrivalSeq += 1;
    s.regionSeqs.set(p.request_id, s.arrivalSeq);
  }
  s.regionChoices = [...s.regionChoices, p];
}

/**
 * Record the user's resolution of a region-choice prompt against the stream the
 * card lives in. "region" = narrowed to a sub-region (regionId set);
 * "whole_state" = kept the honest whole-state default (regionId null). The
 * reply (region-choice-provided) is sent to the agent by the caller; this only
 * marks the card resolved so it folds to its compact answered state in place.
 */
export function recordRegionResolved(
  cs: ChatStreams,
  key: string,
  requestId: string,
  choice: "region" | "whole_state",
  regionId: string | null,
): void {
  const s = getStream(cs, key);
  const next = new Map(s.regionResolved);
  next.set(requestId, { choice, regionId });
  s.regionResolved = next;
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
 * Translucency is rgba/alpha ONLY.
 *
 * F44 (job-0322) — ``heightVh`` is the user's dragged sheet height (in vh).
 * Defaults to the historical 70vh. Ignored while collapsed (the sheet hugs
 * its content height — handle + composer). Clamped to the allowed band.
 *
 * F56 (job-0322) — ``opacityTier`` selects the per-surface translucency
 * band (low / medium / high). Default MEDIUM = a frosted scrim (more opaque
 * than the pre-F56 0.58/0.68 alphas). Mobile bands stay below desktop so the
 * sheet keeps its map-reads-through character. */
export function mobileSheetContainerStyle(
  expanded: boolean,
  heightVh: number = SHEET_HEIGHT_DEFAULT_VH,
  opacityTier: ChatOpacityTier = CHAT_OPACITY_DEFAULT,
): React.CSSProperties {
  const bands = chatOpacityAlphas(opacityTier);
  const alpha = expanded ? bands.mobileExpanded : bands.mobileCollapsed;
  // F81 (NATE 2026-06-17) — in LOW/MEDIUM opacity, fade the background to
  // transparent over the last ~26px so the panel's hard bottom edge dissolves
  // into the map (no visible bottom border); the rest of the surface keeps its
  // tier alpha. HIGH stays a uniform solid scrim. The composer/text are child
  // elements (not the background), so they remain fully opaque/readable.
  const fadeBottomBorder = opacityTier !== "high";
  const background = fadeBottomBorder
    ? `linear-gradient(180deg, rgba(26,27,33,${alpha}) 0%, rgba(18,19,24,${alpha}) calc(100% - 26px), rgba(18,19,24,0) 100%)`
    : `linear-gradient(180deg, rgba(26,27,33,${alpha}) 0%, rgba(18,19,24,${alpha}) 100%)`;
  return {
    position: "absolute",
    left: 0,
    right: 0,
    // F61 (job-0330) — float the sheet up off the bottom edge by the device
    // safe-area inset + a few extra px so it clears the iPhone's curved
    // corners / home indicator. env() is 0 on non-notched screens, so this
    // degrades to a small constant lift. The vh height band (clampSheetHeight)
    // is unaffected by this fixed-px offset, so the drag-resize clamp stays
    // intact.
    bottom: SHEET_BOTTOM_OFFSET_CSS,
    height: expanded ? `${clampSheetHeight(heightVh)}vh` : "auto",
    background,
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
 * never overrun a narrow desktop window. Position unchanged.
 *
 * F56 (job-0322) — ``opacityTier`` selects the desktop translucency band
 * (low / medium / high). Default MEDIUM = 0.99 alpha, slightly MORE opaque
 * than the pre-F56 fixed 0.96. "high" pins it fully opaque (1.0); "low"
 * (0.8) lets the map read through the column. */
export function desktopChatContainerStyle(
  widthPx: number = CHAT_WIDTH_DEFAULT_PX,
  opacityTier: ChatOpacityTier = CHAT_OPACITY_DEFAULT,
): React.CSSProperties {
  const alpha = chatOpacityAlphas(opacityTier).desktop;
  return {
    position: "absolute",
    right: 16,
    top: 16,
    bottom: 16,
    width: `min(${clampChatWidth(widthPx)}px, 92vw)`,
    background: `linear-gradient(180deg, rgba(26,27,33,${alpha}) 0%, rgba(18,19,24,${alpha}) 100%)`,
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
  /**
   * F44 (job-0322) — fired DURING a vertical drag of the handle with the
   * desired new sheet height (vh). The handle measures the pointer's
   * absolute Y against the viewport (sheet is bottom-anchored, so a higher
   * pointer = taller sheet) and reports the clamped vh. Optional: when
   * omitted the handle is tap-only (the legacy behaviour). The caller
   * applies it to the expanded sheet height.
   */
  onResize?: (heightVh: number) => void;
  /** F44 — fired once when a drag GESTURE ends (pointer up) with the final
   * height (vh) so the caller can persist it. Not fired for a pure tap. */
  onResizeEnd?: (heightVh: number) => void;
}

/** Full-width drag-handle row. F44 (job-0322; job-0325 real-iOS fix): the
 * handle is BOTH a tap-to-fold toggle AND a vertical drag-to-resize grip. A
 * gesture that travels < SHEET_DRAG_THRESHOLD_PX is a TAP (toggles collapse,
 * the legacy behaviour); a larger vertical travel RESIZES the sheet (onResize
 * / onResizeEnd report the clamped vh, derived from the pointer's distance
 * above the viewport bottom).
 *
 * WHY THE PRIOR (pointer-only) FIX FAILED ON REAL iOS (job-0325):
 *   React 18 attaches its `touchstart`/`touchmove` delegation listeners as
 *   PASSIVE at the document root, so a `preventDefault()` inside a React
 *   `onTouchMove` is silently ignored — iOS Safari keeps scrolling the page /
 *   bouncing rubber-band under the finger and the height never updates. And
 *   `touch-action:none` alone is honoured inconsistently by older iOS Safari
 *   on a `<button>`; Safari's Pointer Events on a button can also drop the
 *   continuous `pointermove` stream mid-drag. So the sheet "wasn't draggable".
 *
 *   THE FIX: attach NATIVE, NON-PASSIVE `touchstart`/`touchmove`/`touchend`/
 *   `touchcancel` listeners directly on the handle DOM node (via a ref +
 *   addEventListener({ passive:false })) and `preventDefault()` inside
 *   touchmove. That guarantees iOS hands us the raw vertical pan and the
 *   height tracks the finger LIVE. The React Pointer handlers stay for
 *   desktop / Android / pen (and the vitest suite, which fires pointer
 *   events). A shared gesture engine drives both paths; an `activeInput`
 *   guard stops the synthetic pointer events iOS also fires from
 *   double-driving the same physical drag.
 *
 * 44px tall — Apple HIG minimum touch target. job-0280: the handle bar is
 * the SINGLE affordance — the redundant chevron arrow under it is gone; the
 * whole handle area stays tappable with the same aria labels. */
export function SheetToggleHandle({
  expanded,
  onToggle,
  onResize,
  onResizeEnd,
}: SheetToggleHandleProps): JSX.Element {
  // The handle DOM node — native non-passive touch listeners attach here so we
  // can preventDefault() the vertical pan (React's passive listeners can't).
  const handleRef = useRef<HTMLButtonElement | null>(null);

  // Drag bookkeeping for the active gesture. `dragged` flips true the moment
  // the gesture crosses the movement threshold; if it never flips, gesture-end
  // is a TAP and toggles. `lastVh` holds the latest clamped height so
  // onResizeEnd can persist it. `input` records which event family OWNS the
  // gesture so iOS — which fires BOTH touch and synthetic pointer events for
  // one finger — can't double-drive it (the first family to fire down wins;
  // the other family's events are ignored until the gesture ends).
  const gesture = useRef<{
    startX: number;
    startY: number;
    dragged: boolean;
    lastVh: number;
    input: "pointer" | "touch";
  } | null>(null);

  // F44 — pointer Y → sheet height (vh). The sheet is bottom-anchored, so the
  // visible height is (viewportBottom - clientY); convert to vh and clamp.
  const heightVhForPointer = useCallback((clientY: number): number => {
    const vph = window.innerHeight || 1;
    const px = Math.max(0, vph - clientY);
    return clampSheetHeight((px / vph) * 100);
  }, []);

  // --- Shared gesture engine (pointer + native touch both call these) ----- //

  // Begin a gesture. `input` is the event family; a gesture already owned by a
  // different family is left alone (iOS dual-fires touch + pointer).
  const beginGesture = useCallback(
    (input: "pointer" | "touch", clientX: number, clientY: number): void => {
      if (gesture.current && gesture.current.input !== input) return;
      gesture.current = {
        startX: clientX,
        startY: clientY,
        dragged: false,
        lastVh: heightVhForPointer(clientY),
        input,
      };
    },
    [heightVhForPointer],
  );

  // Advance a gesture. Returns true once the gesture has crossed the drag
  // threshold (caller uses it to know whether to preventDefault the scroll).
  const moveGesture = useCallback(
    (input: "pointer" | "touch", clientX: number, clientY: number): boolean => {
      const g = gesture.current;
      if (!g || g.input !== input) return false;
      const dx = clientX - g.startX;
      const dy = clientY - g.startY;
      if (!g.dragged && isSheetDragGesture(dx, dy)) {
        g.dragged = true;
      }
      if (g.dragged) {
        const vh = heightVhForPointer(clientY);
        g.lastVh = vh;
        onResize?.(vh);
      }
      return g.dragged;
    },
    [heightVhForPointer, onResize],
  );

  // End a gesture: a drag persists (onResizeEnd), a tap toggles (onToggle).
  const finishGesture = useCallback(
    (input: "pointer" | "touch"): void => {
      const g = gesture.current;
      if (!g || g.input !== input) return;
      gesture.current = null;
      if (g.dragged) {
        // A drag resized the sheet — persist, do NOT toggle.
        onResizeEnd?.(g.lastVh);
      } else {
        // A tap (no threshold-crossing travel) toggles collapse.
        onToggle();
      }
    },
    [onResizeEnd, onToggle],
  );

  // --- React Pointer path (desktop / Android / pen + vitest) -------------- //

  const onPointerDown = useCallback(
    (e: React.PointerEvent<HTMLButtonElement>): void => {
      // Only left-button / touch / pen initiate a gesture.
      if (e.button !== undefined && e.button > 0) return;
      // If a NATIVE touch gesture is already in flight (iOS), ignore the
      // synthetic pointer twin so we don't double-drive the same finger.
      if (gesture.current && gesture.current.input === "touch") return;
      beginGesture("pointer", e.clientX, e.clientY);
      try {
        e.currentTarget.setPointerCapture(e.pointerId);
      } catch {
        /* setPointerCapture unsupported (happy-dom) — non-fatal */
      }
    },
    [beginGesture],
  );

  const onPointerMove = useCallback(
    (e: React.PointerEvent<HTMLButtonElement>): void => {
      moveGesture("pointer", e.clientX, e.clientY);
    },
    [moveGesture],
  );

  const endGesture = useCallback(
    (e: React.PointerEvent<HTMLButtonElement>): void => {
      try {
        e.currentTarget.releasePointerCapture(e.pointerId);
      } catch {
        /* non-fatal */
      }
      finishGesture("pointer");
    },
    [finishGesture],
  );

  // --- Native NON-PASSIVE touch path (the real-iOS fix, job-0325) --------- //
  //
  // Attached imperatively so we can pass { passive:false } and preventDefault
  // the vertical pan inside touchmove — the ONLY way to stop iOS Safari from
  // scrolling / rubber-banding the page under a drag started on the handle.
  // React's JSX onTouch* handlers can't do this (its root listeners are
  // passive). Re-binds when the engine callbacks change so the latest
  // onResize/onToggle closures are used.
  useEffect(() => {
    const el = handleRef.current;
    if (!el) return;

    const onTouchStart = (e: TouchEvent): void => {
      if (e.touches.length !== 1) return; // ignore multi-touch / pinch
      const t = e.touches[0]!;
      beginGesture("touch", t.clientX, t.clientY);
    };
    const onTouchMove = (e: TouchEvent): void => {
      const t = e.touches[0];
      if (!t) return;
      const dragging = moveGesture("touch", t.clientX, t.clientY);
      // Once this is a real drag, OWN the gesture: stop the page from
      // scrolling / bouncing under the finger so the sheet tracks it live.
      if (dragging && e.cancelable) e.preventDefault();
    };
    const onTouchEnd = (): void => {
      finishGesture("touch");
    };

    // passive:false is REQUIRED for preventDefault to take effect on iOS.
    el.addEventListener("touchstart", onTouchStart, { passive: false });
    el.addEventListener("touchmove", onTouchMove, { passive: false });
    el.addEventListener("touchend", onTouchEnd, { passive: false });
    el.addEventListener("touchcancel", onTouchEnd, { passive: false });
    return () => {
      el.removeEventListener("touchstart", onTouchStart);
      el.removeEventListener("touchmove", onTouchMove);
      el.removeEventListener("touchend", onTouchEnd);
      el.removeEventListener("touchcancel", onTouchEnd);
    };
  }, [beginGesture, moveGesture, finishGesture]);

  return (
    <button
      ref={handleRef}
      data-testid="grace2-chat-sheet-toggle"
      aria-label={expanded ? "Collapse chat" : "Expand chat"}
      aria-expanded={expanded}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={endGesture}
      onPointerCancel={endGesture}
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
        // F44 — let us own the vertical pan (drag-to-resize) instead of the
        // browser scrolling the page under the gesture. `touch-action:none`
        // is the hint; the NATIVE non-passive touchmove preventDefault
        // (attached above) is the belt to this suspenders — together they
        // stop iOS Safari from scrolling / rubber-banding under the drag.
        touchAction: "none",
        WebkitUserSelect: "none",
        userSelect: "none",
        // iOS Safari: kill the grey tap-flash + callout on the grab handle.
        WebkitTapHighlightColor: "transparent",
        WebkitTouchCallout: "none",
      } as React.CSSProperties}
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

// --- Collapsed-sheet sandbox strip (F66, job-0330) ------------------------ //
//
// F66 — when the mobile sheet is COLLAPSED and a Python sandbox (code_exec)
// is RUNNING in the visible stream, a strip surfaces it in the active-strip
// area the SAME WAY a tool does (SheetActiveToolStrip) — BUT with a
// PULSATING-BLUE animation instead of the F42 rainbow gradient, so the user
// can tell at a glance "this is the sandbox running, not a regular tool". The
// strips STACK: if more than one tool/sandbox is active, we render the full
// stack (newest at the bottom, chronological). Reuses the existing
// active-tool-strip plumbing (findRunningToolStep / SheetActiveToolStrip) and
// adds the sandbox variant alongside it.
//
// "Running sandbox" mirrors SandboxCard's own state machine: the user has
// approved the gate (decided === "proceed") AND no result has landed yet.
// Pending (un-decided) and cancelled / completed sandboxes do NOT show a strip
// (they're not actively burning compute).

// The pulsating-blue keyframe is NOT one of PipelineCard's two global
// keyframes (grace2-hue-cycle / grace2-spin), so we inject our own on first
// use. Idempotent + SSR-safe, same pattern as PipelineCard.ensureKeyframes().
export const SANDBOX_PULSE_KEYFRAMES_ID = "grace2-sandbox-pulse-keyframes";
export const SANDBOX_PULSE_ANIMATION = "grace2-pulse-blue 1.6s ease-in-out infinite";

function ensureSandboxPulseKeyframe(): void {
  if (typeof document === "undefined") return;
  if (document.getElementById(SANDBOX_PULSE_KEYFRAMES_ID)) return;
  const style = document.createElement("style");
  style.id = SANDBOX_PULSE_KEYFRAMES_ID;
  // A distinct keyframe from the rainbow hue-cycle: a calm opacity + glow
  // breathe in blue. prefers-reduced-motion is honoured per-render (the strip
  // holds steady), so the keyframe itself stays unconditional.
  style.textContent = `
@keyframes grace2-pulse-blue {
  0%, 100% { opacity: 0.55; }
  50%      { opacity: 1; }
}
`;
  document.head.appendChild(style);
}
// Inject on module import so the strip's first render is already animated.
ensureSandboxPulseKeyframe();

/** True iff this sandbox request is in the RUNNING state — the user approved
 * the gate (decided === "proceed") and no result has arrived yet. Mirrors
 * SandboxCard's own `isRunning` derivation. Pure; exported for unit tests. */
export function isSandboxRunning(
  req: CodeExecRequestPayload,
  results: Map<string, CodeExecResultPayload>,
  decisions: Map<string, SandboxCardDecision>,
): boolean {
  return (
    decisions.get(req.code_exec_id) === "proceed" &&
    !results.has(req.code_exec_id)
  );
}

/** All RUNNING sandbox requests in the visible stream, ordered by first-arrival
 * seq (oldest first — same chronological order the strips stack in). Pure;
 * exported for unit tests. */
export function findRunningSandboxes(
  requests: CodeExecRequestPayload[],
  results: Map<string, CodeExecResultPayload>,
  decisions: Map<string, SandboxCardDecision>,
  sandboxSeqs: Map<string, number>,
): CodeExecRequestPayload[] {
  return requests
    .filter((r) => isSandboxRunning(r, results, decisions))
    .sort((a, b) => {
      const sa = sandboxSeqs.get(a.code_exec_id) ?? Number.MAX_SAFE_INTEGER;
      const sb = sandboxSeqs.get(b.code_exec_id) ?? Number.MAX_SAFE_INTEGER;
      return sa - sb;
    });
}

// One entry in the collapsed-sheet active-strip STACK. Either a running tool
// step (rainbow) or a running sandbox (pulsating-blue). Carries its arrival
// seq so the stack renders in chronological order across BOTH kinds.
export type ActiveStripItem =
  | { kind: "tool"; seq: number; step: PipelineStepSummary }
  | { kind: "sandbox"; seq: number; request: CodeExecRequestPayload };

/**
 * F66 — build the full STACK of active strips for the collapsed mobile sheet:
 * every RUNNING tool step AND every RUNNING sandbox, interleaved by
 * first-arrival seq (chronological). When more than one is active the caller
 * renders them all (a stack), not just the most recent. Pure; exported for
 * unit tests.
 *
 * Tools come from the merged pipeline view-model (the SAME source the inline
 * cards + SheetActiveToolStrip use); sandboxes from the per-stream sandbox
 * state maps (the SAME source SandboxCard uses). Thinking is excluded (it has
 * its own ephemeral surface).
 */
export function buildActiveStripStack(
  history: PipelineStatePayload[],
  live: PipelineStatePayload | null,
  stepOrder: Map<string, number>,
  sandboxRequests: CodeExecRequestPayload[],
  sandboxResults: Map<string, CodeExecResultPayload>,
  sandboxDecisions: Map<string, SandboxCardDecision>,
  sandboxSeqs: Map<string, number>,
): ActiveStripItem[] {
  const out: ActiveStripItem[] = [];
  for (const step of mergeStepsByStepId(history, live)) {
    if (isThinkingStep(step)) continue;
    if (step.state !== "running") continue;
    const seq =
      stepOrder.get(stepInterleaveKey(step)) ?? Number.MAX_SAFE_INTEGER;
    out.push({ kind: "tool", seq, step });
  }
  for (const req of findRunningSandboxes(
    sandboxRequests,
    sandboxResults,
    sandboxDecisions,
    sandboxSeqs,
  )) {
    const seq = sandboxSeqs.get(req.code_exec_id) ?? Number.MAX_SAFE_INTEGER;
    out.push({ kind: "sandbox", seq, request: req });
  }
  out.sort((a, b) => a.seq - b.seq);
  return out;
}

export interface SheetActiveSandboxStripProps {
  /** The running sandbox request to surface. */
  request: CodeExecRequestPayload;
  /** Tap target — expands the sheet so the user sees the full SandboxCard. */
  onExpand: () => void;
}

/** Slim live-status strip for a RUNNING Python sandbox on the collapsed mobile
 * sheet (F66). Structurally identical to SheetActiveToolStrip (so they stack
 * uniformly) but styled with a PULSATING-BLUE animation instead of the F42
 * rainbow — a distinct cue that this is the sandbox, not a regular tool. Honors
 * prefers-reduced-motion: holds steady (full opacity, no pulse). */
export function SheetActiveSandboxStrip({
  request,
  onExpand,
}: SheetActiveSandboxStripProps): JSX.Element {
  const reduced = prefersReducedMotion();
  const label = "Running Python sandbox";
  // F66 — pulsating-blue: a calm opacity breathe on the label (distinct from
  // the rainbow hue-cycle). prefers-reduced-motion → hold steady.
  const labelStyle: React.CSSProperties = reduced
    ? { color: "#a5b4fc" }
    : { color: "#a5b4fc", animation: SANDBOX_PULSE_ANIMATION };
  return (
    <button
      data-testid="grace2-sheet-sandbox-strip"
      data-code-exec-id={request.code_exec_id}
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
        // F66 — its own translucent BLUE-tinted hairline card so it reads as
        // the sandbox variant (vs the neutral tool strip), over the
        // see-through sheet on any basemap.
        background: "rgba(30,33,68,0.72)",
        border: "1px solid rgba(99,102,241,0.45)",
        borderRadius: 8,
        color: "#e5e7eb",
        fontSize: 12,
        lineHeight: "1.4",
        fontFamily: "ui-monospace, 'Cascadia Code', 'Fira Code', monospace",
        cursor: "pointer",
        textAlign: "left",
      }}
    >
      <span
        aria-hidden="true"
        style={{ color: "#818cf8", display: "inline-flex", flexShrink: 0 }}
      >
        <IconSandbox size={14} weight="bold" />
      </span>
      <span
        data-testid="grace2-sheet-sandbox-strip-label"
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
        data-testid="grace2-sheet-sandbox-strip-pulse"
        aria-hidden="true"
        style={{
          width: 8,
          height: 8,
          borderRadius: 4,
          background: "#6366f1",
          flexShrink: 0,
          // The pulse dot breathes too (steady when reduced motion).
          animation: reduced ? undefined : SANDBOX_PULSE_ANIMATION,
        }}
      />
    </button>
  );
}

/** F45b / F66 — render the FULL stack of active strips (tools + sandboxes) for
 * the collapsed mobile sheet. Used both above the composer AND, in the
 * collapsed handle row, as the middle fill. Empty stack → renders nothing. */
export function SheetActiveStripStack({
  items,
  onExpand,
}: {
  items: ActiveStripItem[];
  onExpand: () => void;
}): JSX.Element | null {
  if (items.length === 0) return null;
  return (
    <div
      data-testid="grace2-sheet-strip-stack"
      style={{
        flex: 1,
        minWidth: 0,
        display: "flex",
        flexDirection: "column",
        gap: 6,
      }}
    >
      {items.map((item) =>
        item.kind === "tool" ? (
          <SheetActiveToolStrip
            key={`tool-${item.step.step_id}`}
            step={item.step}
            onExpand={onExpand}
          />
        ) : (
          <SheetActiveSandboxStrip
            key={`sandbox-${item.request.code_exec_id}`}
            request={item.request}
            onExpand={onExpand}
          />
        ),
      )}
    </div>
  );
}

// --- Mobile sheet header row (F45 refined / F45b, job-0330) --------------- //
//
// F45 REFINED — the mobile sheet HANDLE row is ONE line with THREE zones:
//   LEFT  = (GRACE-2 + build version)
//   CENTER= the grabber rectangle (the F44 drag handle / tap-to-fold)
//   RIGHT = (connection status)
// Visual:  (grace + version)   < grabber rectangle >   (● status)
//
// F45b — collapsed vs expanded:
//   - EXPANDED  → the full labeled three-zone row (labels LEFT, grabber
//     CENTER, status RIGHT).
//   - COLLAPSED → JUST the plain grabber rectangle snapped to the TOP of the
//     sheet (NO grace/version/connection labels).
//   - COLLAPSED + tools/sandbox active → the active-strip STACK fills the
//     area in between (the middle) instead of the labels (F66).
//
// The grabber (SheetToggleHandle) is unchanged — it stays the drag affordance,
// so F44 drag-to-resize + tap-to-fold keep working exactly as before. This row
// only arranges it among the surrounding label/strip zones.

export interface MobileSheetHeaderRowProps {
  expanded: boolean;
  status: ConnectionStatus;
  /** F44 — grabber callbacks, threaded straight to SheetToggleHandle. */
  onToggle: () => void;
  onResize?: (heightVh: number) => void;
  onResizeEnd?: (heightVh: number) => void;
  /** F66 — active-strip stack (running tools + sandboxes). Rendered as the
   * middle fill ONLY while collapsed; ignored while expanded (the expanded
   * scroll shows the full inline cards). */
  activeStrips: ActiveStripItem[];
  /** Tap target for the active strips — expands the sheet. */
  onExpandFromStrip: () => void;
}

export function MobileSheetHeaderRow({
  expanded,
  status,
  onToggle,
  onResize,
  onResizeEnd,
  activeStrips,
  onExpandFromStrip,
}: MobileSheetHeaderRowProps): JSX.Element {
  // The grabber: the SINGLE drag affordance. When EXPANDED it sits in the
  // CENTER zone (flex:1) between the label zones; when COLLAPSED it spans the
  // full width at the TOP. Either way it's the same SheetToggleHandle, so the
  // F44 gesture engine (native touch + pointer) is untouched.
  const grabber = (
    <SheetToggleHandle
      expanded={expanded}
      onToggle={onToggle}
      onResize={onResize}
      onResizeEnd={onResizeEnd}
    />
  );

  if (expanded) {
    // F45 refined — the labeled three-zone row. LEFT labels, CENTER grabber,
    // RIGHT status. The two label zones flex-basis 0 / flex:1 so the grabber
    // stays visually centered regardless of label widths.
    return (
      <div
        data-testid="grace2-sheet-header-row"
        data-sheet-row-state="expanded"
        style={{
          flex: "0 0 auto",
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "6px 12px 8px",
          borderBottom: "1px solid rgba(255,255,255,0.08)",
        }}
      >
        {/* LEFT zone — product label + build version (F45). */}
        <span
          data-testid="grace2-chat-tab-left"
          style={{
            flex: 1,
            minWidth: 0,
            display: "inline-flex",
            alignItems: "baseline",
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
        </span>
        {/* CENTER zone — the grabber rectangle (drag handle). */}
        <div
          data-testid="grace2-sheet-grabber-zone"
          style={{ flex: "0 0 auto", display: "flex", width: 56 }}
        >
          {grabber}
        </div>
        {/* RIGHT zone — connection status (F45). */}
        <span
          data-testid="connection-status"
          title={`WebSocket ${STATUS_LABEL[status]}`}
          style={{
            flex: 1,
            minWidth: 0,
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "flex-end",
            gap: 6,
            fontSize: 11,
            color: STATUS_COLOR[status],
            marginLeft: "auto",
          }}
        >
          <span
            style={{
              width: 8,
              height: 8,
              borderRadius: 4,
              background: STATUS_COLOR[status],
              display: "inline-block",
              flexShrink: 0,
            }}
          />
          {STATUS_LABEL[status]}
        </span>
      </div>
    );
  }

  // COLLAPSED (F45b) — JUST the grabber at the TOP (no labels). When tools /
  // sandbox are active, the strip stack fills the middle area BELOW the
  // grabber (instead of the labels). No grace/version/connection chrome here.
  return (
    <div
      data-testid="grace2-sheet-header-row"
      data-sheet-row-state="collapsed"
      style={{ flex: "0 0 auto", display: "flex", flexDirection: "column" }}
    >
      {grabber}
      {activeStrips.length > 0 && (
        <div
          data-testid="grace2-sheet-collapsed-strips"
          style={{ display: "flex", flexDirection: "column" }}
        >
          <SheetActiveStripStack
            items={activeStrips}
            onExpand={onExpandFromStrip}
          />
        </div>
      )}
    </div>
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
  // F44 (job-0322) — user-draggable EXPANDED sheet height (vh). Persisted to
  // localStorage (per-user). Read lazily so first paint doesn't touch
  // localStorage before hydration. Mobile-only; desktop ignores it. The
  // handle drag updates this live; onResizeEnd persists it.
  const [sheetHeightVh, setSheetHeightVh] = useState<number>(() =>
    mobile ? readSheetHeight() : SHEET_HEIGHT_DEFAULT_VH,
  );
  // Latest height during a drag — onResizeEnd persists from here so we don't
  // hammer localStorage on every pointermove.
  const sheetHeightRef = useRef<number>(sheetHeightVh);
  sheetHeightRef.current = sheetHeightVh;
  const handleSheetResize = useCallback((vh: number): void => {
    sheetHeightRef.current = vh;
    setSheetHeightVh(vh);
  }, []);
  const handleSheetResizeEnd = useCallback((vh: number): void => {
    sheetHeightRef.current = vh;
    setSheetHeightVh(vh);
    writeSheetHeight(vh);
  }, []);
  // F56 (job-0322; reactive fix) — per-user chat-opacity tier. Read lazily from
  // localStorage (the shared key Chat.tsx owns; SettingsPopup writes it).
  // Default MEDIUM. SAME-TAB REACTIVITY: SettingsPopup's writeChatOpacity
  // dispatches CHAT_OPACITY_CHANGED_EVENT on window after persisting (a plain
  // localStorage write does NOT fire the `storage` event in the same tab), so
  // we subscribe to it here and re-read + re-apply the alpha bands LIVE to BOTH
  // the desktop container and the mobile sheet — no reload, no remount. We also
  // listen to the native `storage` event for the cross-tab case (a Settings
  // change in another tab/window).
  const [opacityTier, setOpacityTier] = useState<ChatOpacityTier>(() =>
    readChatOpacity(),
  );
  useEffect(() => {
    // Same-tab: custom event from writeChatOpacity. Re-read from the shared key
    // (single source of truth) rather than trusting the event detail.
    const onOpacityChanged = (): void => setOpacityTier(readChatOpacity());
    // Cross-tab: the browser fires `storage` only in OTHER tabs. Ignore writes
    // to unrelated keys so we don't thrash on every localStorage mutation.
    const onStorage = (e: StorageEvent): void => {
      if (e.key === null || e.key === LS_CHAT_OPACITY) {
        setOpacityTier(readChatOpacity());
      }
    };
    window.addEventListener(CHAT_OPACITY_CHANGED_EVENT, onOpacityChanged);
    window.addEventListener("storage", onStorage);
    return () => {
      window.removeEventListener(CHAT_OPACITY_CHANGED_EVENT, onOpacityChanged);
      window.removeEventListener("storage", onStorage);
    };
  }, []);
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

  // Region-disambiguation picker ↔ map choropleth sync. The bus is the shared
  // hover/selection state between the in-chat candidate list (here) and the
  // map county choropleth (Map.tsx). We mirror the bus-synced hovered/selected
  // ids into React state so a MAP hover/tap re-renders the matching list row.
  const [regionHoveredId, setRegionHoveredId] = useState<string | null>(null);
  const [regionSelectedId, setRegionSelectedId] = useState<string | null>(null);

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
      // NATE 2026-06-17: live big-sim readout. solve-progress is session-scoped
      // (ws.ts SESSION_SCOPED_TYPES) so Chat receives it via the fan-out hub
      // even when the solver step ran on App.tsx's connection. We store it per
      // run_id in the owning stream; the running solver card matches + renders.
      onSolveProgress: (p: SolveProgressPayload, caseId?: string | null) => {
        routeSolveProgress(streamsRef.current, p, caseId);
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
      // SRS §F.3 amendment: a keyed tool paused on a missing/invalid
      // credential. credential-request is session-scoped so Chat receives it
      // via the fan-out hub even when the paused tool ran on App.tsx's
      // connection. We render an inline CredentialCard in the owning stream.
      onCredentialRequest: (p: CredentialRequestPayload) => {
        routeCredentialRequest(streamsRef.current, p);
        bump();
      },
      // FIX 2 (NATE 2026-06-17): the large-payload warning is now an IN-CHAT
      // card, not the App-level banner "hat". tool-payload-warning is
      // session-scoped (ws.ts SESSION_SCOPED_TYPES) so Chat's GraceWs receives
      // it via the fan-out hub even when the paused tool ran on App.tsx's
      // connection. We render an inline PayloadWarningInline in the owning
      // stream, interleaved at its arrival position.
      onPayloadWarning: (p: PayloadWarningEnvelopePayload) => {
        routePayloadWarning(streamsRef.current, p);
        bump();
      },
      // Region-disambiguation request (state-bbox-fallback narrowing): a
      // geocode snapped to a whole-state bbox and the agent is offering a
      // narrower county pick. region-choice-request is session-scoped (ws.ts
      // SESSION_SCOPED_TYPES) so Chat's GraceWs receives it via the fan-out hub
      // even when the paused geocode ran on App.tsx's connection. We render the
      // inline RegionPickerCard in the owning stream AND publish the request to
      // the region-choice bus so Map.tsx paints the synced county choropleth.
      onRegionChoiceRequest: (p: RegionChoiceRequestPayload) => {
        routeRegionChoice(streamsRef.current, p);
        regionChoiceBus.setRequest(p);
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

  // SRS §F.3 amendment: dev-only seam for credential-request injection so
  // Playwright UI snapshots / unit harnesses can render a CredentialCard
  // without a live keyed-tool failure. Same routed path as the real envelope.
  // Guarded behind import.meta.env.DEV so it's stripped from production.
  useEffect(() => {
    if (!import.meta.env.DEV) return;
    (window as unknown as Record<string, unknown>).__grace2InjectCredentialRequest =
      (p: CredentialRequestPayload) => {
        routeCredentialRequest(streamsRef.current, p);
        bump();
      };
    return () => {
      delete (window as unknown as Record<string, unknown>)
        .__grace2InjectCredentialRequest;
    };
  }, [bump]);

  // FIX 2 (NATE 2026-06-17): dev-only seam for tool-payload-warning injection so
  // Playwright UI snapshots / unit harnesses can render the in-chat
  // PayloadWarningInline card without a live large-payload dispatch. Same routed
  // path as the real envelope. Guarded behind import.meta.env.DEV so it's
  // stripped from production.
  useEffect(() => {
    if (!import.meta.env.DEV) return;
    (window as unknown as Record<string, unknown>).__grace2InjectPayloadWarningChat =
      (p: PayloadWarningEnvelopePayload) => {
        routePayloadWarning(streamsRef.current, p);
        bump();
      };
    return () => {
      delete (window as unknown as Record<string, unknown>)
        .__grace2InjectPayloadWarningChat;
    };
  }, [bump]);

  // Dev-only seam: inject a region-choice-request so Playwright UI snapshots /
  // unit harnesses can render the RegionPickerCard + drive the synced map
  // choropleth without a live state-bbox-fallback geocode. Same routed path as
  // the real envelope (routes the card AND publishes to the region-choice bus).
  // Guarded behind import.meta.env.DEV so it's stripped from production.
  useEffect(() => {
    if (!import.meta.env.DEV) return;
    (window as unknown as Record<string, unknown>).__grace2InjectRegionChoice =
      (p: RegionChoiceRequestPayload) => {
        routeRegionChoice(streamsRef.current, p);
        regionChoiceBus.setRequest(p);
        bump();
      };
    return () => {
      delete (window as unknown as Record<string, unknown>)
        .__grace2InjectRegionChoice;
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
    visible.credentialRequests,
    visible.credentialResolved,
    visible.payloadWarnings,
    visible.payloadResolved,
    visible.regionChoices,
    visible.regionResolved,
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

  // SRS §F.3 amendment — credential-request resolution.
  //
  // Save: route the raw key through the EXISTING secret-add path (the only
  // envelope that ever carries key material — Decision F), then signal the
  // agent to retry the paused tool via credential-provided (echoing the
  // request_id). The fresh secrets-list the server emits after secret-add is
  // picked up by App.tsx's onSecretsList handler, so the saved key surfaces in
  // Settings -> API Keys without any extra round-trip here.
  //
  // The key value is handed straight to the WS and never persisted in Chat
  // state; CredentialCard clears its own input immediately after Save.
  function handleCredentialSave(
    req: CredentialRequestPayload,
    keyValue: string,
  ): void {
    recordCredentialResolved(
      streamsRef.current,
      visibleKey,
      req.request_id,
      "saved",
    );
    bump();
    const ws = wsRef.current;
    if (!ws) return;
    // 1) Persist the key to the user's vault (user-wide scope: the
    //    credential is a provider key, not Case-scoped data).
    ws.sendSecretAdd({
      provider: req.provider_id,
      case_id: null,
      label: req.provider_label,
      key_value: keyValue,
    });
    // 2) Signal the agent to retry the exact paused tool. No key material on
    //    this envelope — secret-add already carried it.
    ws.sendCredentialProvided({
      request_id: req.request_id,
      secret_id: null,
      provided: true,
    });
  }

  // Decline: emit credential-provided with provided=false so the agent
  // narrates honestly + abandons the paused tool (no silent dead-end).
  function handleCredentialDecline(req: CredentialRequestPayload): void {
    recordCredentialResolved(
      streamsRef.current,
      visibleKey,
      req.request_id,
      "declined",
    );
    bump();
    wsRef.current?.sendCredentialProvided({
      request_id: req.request_id,
      secret_id: null,
      provided: false,
    });
  }

  // FIX 2 (NATE 2026-06-17) — large-payload warning resolution. The card now
  // lives IN the chat stream (not the App banner "hat"); the accept/cancel
  // wiring to the agent is unchanged — sendPayloadConfirmation echoes the
  // warning_id + decision (+ revised args for narrow_scope) back so the agent
  // resumes / cancels / re-dispatches the paused tool. We record the decision
  // against the VISIBLE stream so the card folds to its answered state in place.
  function handlePayloadDecide(
    warning: PayloadWarningEnvelopePayload,
    decision: PayloadConfirmationDecision,
    revised: Record<string, unknown> | null,
  ): void {
    recordPayloadResolved(
      streamsRef.current,
      visibleKey,
      warning.warning_id,
      decision,
    );
    bump();
    wsRef.current?.sendPayloadConfirmation(warning.warning_id, decision, revised);
  }

  // Region-disambiguation picker resolution (state-bbox-fallback narrowing).
  //
  // The card list AND the map county choropleth are synced through the
  // region-choice bus. These handlers are the single reply path both surfaces
  // funnel through (a card-row click and a map polygon tap both end up calling
  // handleRegionPick), so the agent re-resolves by region_id exactly once per
  // pick — no forked logic.

  /** Relay a hover (card row OR map polygon) to the bus so both surfaces
   * highlight in lockstep. */
  const handleRegionHover = useCallback((regionId: string | null) => {
    regionChoiceBus.setHovered(regionId);
  }, []);

  /** Resolve a region pick: record it (folds the card to compact), echo the
   * selection to the bus, clear the active request (cleans up the choropleth),
   * and send `region-choice-provided` (choice="region") echoing the request_id
   * + the candidate's region_id + bbox. Used for BOTH a card-row click and a
   * map polygon tap. */
  const handleRegionPick = useCallback(
    (req: RegionChoiceRequestPayload, candidate: RegionCandidate): void => {
      recordRegionResolved(
        streamsRef.current,
        visibleKey,
        req.request_id,
        "region",
        candidate.region_id,
      );
      bump();
      regionChoiceBus.clearRequest(req.request_id);
      wsRef.current?.sendRegionChoiceProvided({
        request_id: req.request_id,
        choice: "region",
        selected_region_id: candidate.region_id,
        selected_bbox: candidate.bbox,
      });
    },
    [visibleKey, bump],
  );

  /** Keep the honest whole-state default: record it, clear the active request
   * (cleans up the choropleth), and send `region-choice-provided`
   * (choice="whole_state"). This IS the decline path (Invariant 8). */
  const handleRegionWholeState = useCallback(
    (req: RegionChoiceRequestPayload): void => {
      recordRegionResolved(
        streamsRef.current,
        visibleKey,
        req.request_id,
        "whole_state",
        null,
      );
      bump();
      regionChoiceBus.clearRequest(req.request_id);
      wsRef.current?.sendRegionChoiceProvided({
        request_id: req.request_id,
        choice: "whole_state",
      });
    },
    [visibleKey, bump],
  );

  // Subscribe to the region-choice bus: mirror the synced hovered/selected ids
  // into state (so a MAP hover/tap re-renders the matching card row), and relay
  // a MAP TAP through the SAME reply path as a card-row click. The bus carries
  // only ids; we resolve the id back to its candidate against the active
  // request before sending the reply, so a stale tap (request already cleared /
  // unknown id) is a safe no-op.
  useEffect(() => {
    const unsubState = regionChoiceBus.subscribe((st) => {
      setRegionHoveredId(st.hoveredRegionId);
      setRegionSelectedId(st.selectedRegionId);
    });
    const unsubPick = regionChoiceBus.subscribePick((regionId) => {
      const req = regionChoiceBus.getState().request;
      if (!req) return;
      const candidate = req.candidates.find((c) => c.region_id === regionId);
      if (!candidate) return;
      handleRegionPick(req, candidate);
    });
    return () => {
      unsubState();
      unsubPick();
    };
  }, [handleRegionPick]);

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
  const credentialRequests = visible.credentialRequests;
  const payloadWarnings = visible.payloadWarnings;
  const lastError = visible.lastError;

  const showCancel = shouldShowCancel(pipeline);
  const liveSteps = pipeline.live?.steps ?? [];
  // job-0280 / F66 (job-0330) — collapsed-sheet active-strip STACK: every
  // RUNNING tool step (rainbow) AND every RUNNING sandbox (pulsating-blue),
  // interleaved by first-arrival seq. Resolved from the SAME merged pipeline
  // view-model the inline cards render + the SAME sandbox state maps the
  // SandboxCard reads (no forked logic). Empty whenever the sheet is
  // expanded / desktop / nothing running. F45b: this fills the middle of the
  // COLLAPSED handle row.
  const collapsedActiveStrips: ActiveStripItem[] =
    mobile && !sheetExpanded
      ? buildActiveStripStack(
          pipeline.history,
          pipeline.live,
          visible.stepOrder,
          visible.sandboxRequests,
          visible.sandboxResults,
          visible.sandboxDecisions,
          visible.sandboxSeqs,
        )
      : [];
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
    ? mobileSheetContainerStyle(sheetExpanded, sheetHeightVh, opacityTier)
    : desktopChatContainerStyle(chatWidth, opacityTier);

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
      {/* F45 refined / F45b (job-0330) — MOBILE three-zone handle row.
          EXPANDED → (GRACE-2 + version) LEFT, grabber CENTER, status RIGHT.
          COLLAPSED → just the grabber at the TOP (no labels); when tools /
          sandbox are running, the active-strip stack fills the middle.
          The grabber stays the F44 drag affordance. Desktop renders the
          classic <header> below instead. */}
      {mobile && (
        <MobileSheetHeaderRow
          expanded={sheetExpanded}
          status={status}
          onToggle={() => setSheetExpanded((v) => !v)}
          // F44 — drag the handle to resize the EXPANDED sheet. A resize
          // gesture only makes sense while expanded; when collapsed a small
          // tap still toggles open (drag-vs-tap threshold inside the handle).
          onResize={sheetExpanded ? handleSheetResize : undefined}
          onResizeEnd={sheetExpanded ? handleSheetResizeEnd : undefined}
          activeStrips={collapsedActiveStrips}
          onExpandFromStrip={() => setSheetExpanded(true)}
        />
      )}
      {/* DESKTOP header — classic F45 row: 'GRACE-2' + version LEFT, the
          connection status RIGHT, the collapse control at the far right.
          (Mobile uses MobileSheetHeaderRow above instead.) */}
      {!mobile && (
        <header
          data-testid="grace2-chat-header"
          style={{
            // job-0283 — desktop family hairline divider + LayerPanel header
            // padding.
            padding: "12px 14px",
            borderBottom: "1px solid rgba(255,255,255,0.06)",
            // F45 — 'GRACE-2' + version on the LEFT (grace2-chat-tab-left),
            // connection status pushed RIGHT by the flex:1 spacer.
            display: "flex",
            alignItems: "center",
            gap: 8,
          }}
        >
          {/* F45 LEFT group — product label + build version. */}
          <span
            data-testid="grace2-chat-tab-left"
            style={{ display: "inline-flex", alignItems: "baseline", gap: 8 }}
          >
            <strong style={{ fontSize: 14 }}>GRACE-2</strong>
            <span
              data-testid="grace2-build-version"
              title="build version — tells you which deploy this tab is running"
              style={{ color: "#888", fontSize: 11 }}
            >
              {BUILD_VERSION}
            </span>
          </span>
          {/* Spacer — pushes the connection status to the RIGHT edge (F45). */}
          <span style={{ flex: 1 }} />
          {/* F45 RIGHT group — connection-status indicator. */}
          <span
            data-testid="connection-status"
            title={`WebSocket ${STATUS_LABEL[status]}`}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
              fontSize: 11,
              color: STATUS_COLOR[status],
              marginLeft: "auto",
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
          {/* ux-batch-1 J1 — the large/normal width TOGGLE was removed in
              favour of a drag-to-resize left border (the
              grace2-chat-resize-handle above). */}
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
                lineHeight: 1,
                padding: "0 4px",
                display: "flex",
                alignItems: "center",
                fontFamily: "system-ui, sans-serif",
              }}
            >
              {/* job-0162: chevron-right ("collapse panel" idiom) replaces ×  */}
              {/* ("close" idiom) — collapsing must NEVER imply destruction of  */}
              {/* the chat history. job-0325: raw '›' glyph → IconChevronRight  */}
              {/* (icon module is the single source of truth for glyphs).       */}
              <IconChevronRight size={18} title="Collapse chat panel" />
            </button>
          )}
        </header>
      )}

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
          solveProgress={visible.solveProgress}
          messageOrder={visible.messageOrder}
          stepOrder={visible.stepOrder}
          credentialRequests={credentialRequests}
          credentialSeqs={visible.credentialSeqs}
          credentialResolved={visible.credentialResolved}
          onCredentialSave={handleCredentialSave}
          onCredentialDecline={handleCredentialDecline}
          payloadWarnings={payloadWarnings}
          payloadSeqs={visible.payloadSeqs}
          payloadResolved={visible.payloadResolved}
          onPayloadDecide={handlePayloadDecide}
          regionChoices={visible.regionChoices}
          regionSeqs={visible.regionSeqs}
          regionResolved={visible.regionResolved}
          regionHoveredId={regionHoveredId}
          regionSelectedId={regionSelectedId}
          onRegionHover={handleRegionHover}
          onRegionPick={handleRegionPick}
          onRegionWholeState={handleRegionWholeState}
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

        {/* SRS §F.3 amendment (NATE 2026-06-17): credential prompts no longer
            render here as a trailing section. They now INTERLEAVE inline in the
            InterleavedChatStream above (kind: "credential"), sorted by
            first-arrival seq alongside chat bubbles + tool cards, so the card
            sits at its natural chat slot and the narration resumes after it. */}

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

      {/* F45b / F66 (job-0330) — the collapsed-sheet active-strip STACK now
          renders INSIDE the collapsed handle row (MobileSheetHeaderRow above)
          as the middle fill, not in a separate slab above the composer. This
          keeps the collapsed sheet = grabber (top) + strips (middle) +
          composer (bottom). */}

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
                // sheet's height is handle + composer. F61 (job-0330): the
                // sheet CONTAINER now floats up by the safe-area inset
                // (SHEET_BOTTOM_OFFSET_CSS), so the composer only needs its
                // own constant bottom padding here — no double-counted
                // env(safe-area-inset-bottom). A small extra keeps the
                // textarea off the rounded sheet edge.
                flex: "0 0 auto",
                padding: "0 10px 12px 10px",
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
    }
  | {
      // SRS §F.3 amendment (NATE 2026-06-17): a just-in-time credential prompt
      // INTERLEAVED into the chat scroll at its first-arrival seq — exactly
      // like a tool card. It renders the full key-entry form while pending and
      // folds to a compact tool-card-style summary once resolved, so the
      // agent's subsequent narration flows AFTER it (no break-out, no
      // bottom-of-scroll detachment). Carries the request + resolution; the
      // onSave / onDecline callbacks are supplied by InterleavedChatStream
      // (kept off this pure view-model so buildInterleavedStream stays pure).
      kind: "credential";
      seq: number;
      requestId: string;
      request: CredentialRequestPayload;
      resolved: "saved" | "declined" | null;
    }
  | {
      // FIX 2 (NATE 2026-06-17): a large-payload warning INTERLEAVED into the
      // chat scroll at its first-arrival seq — exactly like a tool/credential
      // card. It renders the PayloadWarningInline card (Proceed / Cancel /
      // Narrow scope per the agent's options; Cancel rightmost) at its natural
      // chat slot so the narration that follows the user's answer flows AFTER
      // it. Replaces the old App-level banner "hat". The >250MB hard-block
      // (no "proceed" option) is preserved by PayloadWarningInline itself
      // (overHardCap → no Proceed button). Carries the warning + its resolution;
      // the onDecide callback is supplied by InterleavedChatStream (kept off
      // this pure view-model so buildInterleavedStream stays pure).
      kind: "payload-warning";
      seq: number;
      warningId: string;
      warning: PayloadWarningEnvelopePayload;
      resolved: PayloadConfirmationDecision | null;
    }
  | {
      // Region-disambiguation picker INTERLEAVED into the chat scroll at its
      // first-arrival seq — exactly like a tool / credential / payload-warning
      // card. It renders the RegionPickerCard (honest prompt + scrollable
      // candidate-county list + "Use whole state" default) at its natural chat
      // slot so the narration that follows the user's answer flows AFTER it.
      // The candidate list is SYNCED with the map county choropleth via the
      // region-choice bus (hover/select in either surface highlights the
      // other). Carries the request + its resolution; the hover/pick/whole-state
      // callbacks are supplied by InterleavedChatStream (kept off this pure
      // view-model so buildInterleavedStream stays pure).
      kind: "region-choice";
      seq: number;
      requestId: string;
      request: RegionChoiceRequestPayload;
      resolved: { choice: "region" | "whole_state"; regionId: string | null } | null;
    };

export function buildInterleavedStream(
  messages: ChatMessage[],
  history: PipelineStatePayload[],
  live: PipelineStatePayload | null,
  messageOrder: Map<string, number>,
  stepOrder: Map<string, number>,
  // SRS §F.3 — optional credential inputs so credential prompts interleave at
  // their first-arrival seq alongside messages + tool cards. Defaulted so
  // existing callers / tests that don't pass them keep working unchanged.
  credentialRequests: CredentialRequestPayload[] = [],
  credentialSeqs: Map<string, number> = new Map(),
  credentialResolved: Map<string, "saved" | "declined"> = new Map(),
  // FIX 2 — optional large-payload warning inputs so warning cards interleave
  // at their first-arrival seq too. Defaulted so existing callers / tests keep
  // working unchanged.
  payloadWarnings: PayloadWarningEnvelopePayload[] = [],
  payloadSeqs: Map<string, number> = new Map(),
  payloadResolved: Map<string, PayloadConfirmationDecision> = new Map(),
  // Region-disambiguation picker inputs so picker cards interleave at their
  // first-arrival seq too. Defaulted so existing callers / tests keep working.
  regionChoices: RegionChoiceRequestPayload[] = [],
  regionSeqs: Map<string, number> = new Map(),
  regionResolved: Map<
    string,
    { choice: "region" | "whole_state"; regionId: string | null }
  > = new Map(),
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
  // Credential prompts (SRS §F.3) — seq from credentialSeqs (first-arrival),
  // so the card lands at its natural chat slot between the narration that
  // preceded it and the narration that resumes after it (NATE 2026-06-17).
  for (const cReq of credentialRequests) {
    const seq =
      credentialSeqs.get(cReq.request_id) ?? Number.MAX_SAFE_INTEGER;
    out.push({
      kind: "credential",
      seq,
      requestId: cReq.request_id,
      request: cReq,
      resolved: credentialResolved.get(cReq.request_id) ?? null,
    });
  }
  // Large-payload warnings (FIX 2) — seq from payloadSeqs (first-arrival), so
  // the card lands at its natural chat slot between the narration that preceded
  // the paused tool and the narration that resumes after the user answers.
  for (const w of payloadWarnings) {
    const seq = payloadSeqs.get(w.warning_id) ?? Number.MAX_SAFE_INTEGER;
    out.push({
      kind: "payload-warning",
      seq,
      warningId: w.warning_id,
      warning: w,
      resolved: payloadResolved.get(w.warning_id) ?? null,
    });
  }
  // Region-disambiguation pickers — seq from regionSeqs (first-arrival), so the
  // card lands at its natural chat slot between the narration that preceded the
  // paused geocode and the narration that resumes after the user picks.
  for (const rc of regionChoices) {
    const seq = regionSeqs.get(rc.request_id) ?? Number.MAX_SAFE_INTEGER;
    out.push({
      kind: "region-choice",
      seq,
      requestId: rc.request_id,
      request: rc,
      resolved: regionResolved.get(rc.request_id) ?? null,
    });
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
  // NATE 2026-06-17: live big-sim solve-progress keyed by run_id. Threaded to
  // each running solver step's PipelineCard via matchSolveForStep.
  solveProgress: Map<string, SolveProgressPayload>;
  messageOrder: Map<string, number>;
  stepOrder: Map<string, number>;
  // SRS §F.3 amendment (NATE 2026-06-17): credential prompts interleave INLINE
  // in this stream at their first-arrival seq, exactly like tool cards. The
  // callbacks are component-bound (WS side effects live in Chat) so they ride
  // on the props rather than the pure stream view-model.
  credentialRequests: CredentialRequestPayload[];
  credentialSeqs: Map<string, number>;
  credentialResolved: Map<string, "saved" | "declined">;
  onCredentialSave: (req: CredentialRequestPayload, keyValue: string) => void;
  onCredentialDecline: (req: CredentialRequestPayload) => void;
  // FIX 2 (NATE 2026-06-17): large-payload warnings interleave INLINE in this
  // stream at their first-arrival seq, exactly like tool / credential cards.
  // The onDecide callback is component-bound (WS side effect lives in Chat) so
  // it rides on the props rather than the pure stream view-model.
  payloadWarnings: PayloadWarningEnvelopePayload[];
  payloadSeqs: Map<string, number>;
  payloadResolved: Map<string, PayloadConfirmationDecision>;
  onPayloadDecide: (
    warning: PayloadWarningEnvelopePayload,
    decision: PayloadConfirmationDecision,
    revised: Record<string, unknown> | null,
  ) => void;
  // Region-disambiguation pickers interleave INLINE in this stream at their
  // first-arrival seq, exactly like tool / credential / payload-warning cards.
  // The candidate list is SYNCED with the map county choropleth via the
  // region-choice bus; the bus-synced hover/selection ids ride on the props so
  // a map hover highlights the matching list row. The callbacks are
  // component-bound (WS + bus side effects live in Chat) so they ride on the
  // props rather than the pure stream view-model.
  regionChoices: RegionChoiceRequestPayload[];
  regionSeqs: Map<string, number>;
  regionResolved: Map<
    string,
    { choice: "region" | "whole_state"; regionId: string | null }
  >;
  /** Bus-synced hover id (card row OR map polygon). null = none. */
  regionHoveredId: string | null;
  /** Bus-synced pre-reply selection id (card row OR map polygon). null = none. */
  regionSelectedId: string | null;
  onRegionHover: (regionId: string | null) => void;
  onRegionPick: (req: RegionChoiceRequestPayload, candidate: RegionCandidate) => void;
  onRegionWholeState: (req: RegionChoiceRequestPayload) => void;
}

function InterleavedChatStream({
  messages,
  history,
  live,
  solveProgress,
  messageOrder,
  stepOrder,
  credentialRequests,
  credentialSeqs,
  credentialResolved,
  onCredentialSave,
  onCredentialDecline,
  payloadWarnings,
  payloadSeqs,
  payloadResolved,
  onPayloadDecide,
  regionChoices,
  regionSeqs,
  regionResolved,
  regionHoveredId,
  regionSelectedId,
  onRegionHover,
  onRegionPick,
  onRegionWholeState,
}: InterleavedChatStreamProps): JSX.Element | null {
  const stream = buildInterleavedStream(
    messages,
    history,
    live,
    messageOrder,
    stepOrder,
    credentialRequests,
    credentialSeqs,
    credentialResolved,
    payloadWarnings,
    payloadSeqs,
    payloadResolved,
    regionChoices,
    regionSeqs,
    regionResolved,
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
        if (entry.kind === "credential") {
          return (
            <CredentialCard
              key={entry.requestId}
              request={entry.request}
              resolved={entry.resolved}
              onSave={(keyValue) => onCredentialSave(entry.request, keyValue)}
              onDecline={() => onCredentialDecline(entry.request)}
            />
          );
        }
        if (entry.kind === "payload-warning") {
          // FIX 2 — the large-payload warning renders inline in the chat
          // scroll (Proceed / Cancel / Narrow scope; Cancel rightmost per the
          // existing button-order convention). The >250MB hard-block (no
          // "proceed" option) is preserved by PayloadWarningInline (overHardCap
          // hides Proceed). `resolved` keeps the card answered across a remount
          // (Case switch + return).
          return (
            <PayloadWarningInline
              key={entry.warningId}
              warning={entry.warning}
              resolved={entry.resolved}
              onDecide={(decision, revised) =>
                onPayloadDecide(entry.warning, decision, revised)
              }
            />
          );
        }
        if (entry.kind === "region-choice") {
          // Region-disambiguation picker renders inline in the chat scroll. The
          // candidate list is SYNCED with the map county choropleth via the
          // region-choice bus: the bus-synced hover/selection ids highlight the
          // matching list row (a MAP hover/tap reflects here), and hovering /
          // picking a row reports back through onRegionHover / onRegionPick so
          // the polygon highlights / commits in lockstep. `resolved` folds the
          // card to its compact answered state across a remount (Case switch +
          // return).
          return (
            <RegionPickerCard
              key={entry.requestId}
              request={entry.request}
              resolved={entry.resolved?.choice ?? null}
              resolvedRegionId={entry.resolved?.regionId ?? null}
              hoveredRegionId={regionHoveredId}
              selectedRegionId={regionSelectedId}
              onHoverRegion={onRegionHover}
              onPickRegion={(candidate) => onRegionPick(entry.request, candidate)}
              onUseWholeState={() => onRegionWholeState(entry.request)}
            />
          );
        }
        // tool — NATE 2026-06-17: thread the matched live solve-progress so a
        // running heavy-solver card surfaces its inline readout. Non-solver /
        // non-running steps get null (no readout).
        return (
          <PipelineCard
            key={entry.stepKey}
            step={entry.step}
            solve={matchSolveForStep(entry.step, solveProgress)}
          />
        );
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
