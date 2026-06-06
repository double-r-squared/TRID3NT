// GRACE-2 web — PipelineStrip (FR-WC-8, FR-WC-9; Invariant 8).
//
// Renders the ordered step list of the currently executing pipeline AND
// hosts the cancel button (FR-WC-9). The step list comes from the
// `pipeline-state` envelope (A.4) — payload IS itself a snapshot of the
// current pipeline. Each new envelope WHOLESALE REPLACES the local view-
// model per Appendix A.7 ("replace-not-reconcile"); we never diff or merge
// deltas.
//
// CROSS-ENVELOPE CANCEL-BUTTON PREDICATE (FR-WC-9, kickoff step 4):
//
//   The cancel button is visible iff EITHER
//     (a) the last received `pipeline-state` envelope has at least one
//         step in `running` state, OR
//     (b) the last received `session-state` envelope's `current_pipeline`
//         field is non-null.
//
//   These two conditions are on DIFFERENT envelopes:
//     - `pipeline-state` IS the snapshot of the current pipeline's step
//       list (A.4) and arrives whenever the pipeline changes state.
//     - `current_pipeline` is a TOP-LEVEL field on the `session-state`
//       envelope (A.4) carried on connect/resume (Appendix D.6
//       SessionDocument.current_pipeline).
//
//   The predicate is the UNION of both checks: either signal alone is
//   sufficient to show the button. Rationale: on a fresh connect the
//   `session-state` will indicate an in-flight pipeline before any new
//   `pipeline-state` snapshot arrives, and steady-state running is
//   signaled by `pipeline-state` regardless of whether session-state has
//   been re-emitted.
//
// CANCEL EMISSION (FR-WC-9, Invariant 8): clicking emits a `cancel`
// envelope via `ws.ts`'s existing `GraceWs.sendCancel(reason)` function —
// the M1 cancel chain verified end-to-end agent-side at 502 ms in
// job-0015. We REUSE that function rather than constructing a new cancel
// envelope here (kickoff §5 + invariant 8 "first-class cancellation").
//
// STATE COLORS (FR-WC-8):
//   pending   → #9ca3af (gray)
//   running   → #3b82f6 (blue) with a subtle pulse animation
//   complete  → #10b981 (green)
//   failed    → #ef4444 (red) with error_code + error_message inline
//   cancelled → #eab308 (yellow) — distinct visual state from `failed`
//                                   per Invariant 8.
//
// Failed-step expandable logs block contents are deferred to M9 per
// kickoff §3. Only the basic state colors + the error_code/message inline
// render land here.

import { useEffect, useReducer, useRef } from "react";
import {
  PipelineSnapshot,
  PipelineStatePayload,
  PipelineStepState,
  PipelineStepSummary,
  SessionStatePayload,
} from "./contracts";

// --- Subscription wiring ----------------------------------------------- //

export type PipelineStateSubscriber = (p: PipelineStatePayload) => void;
export type SessionStateSubscriber = (p: SessionStatePayload) => void;

export interface PipelineStripBus {
  pushPipelineState: (p: PipelineStatePayload) => void;
  pushSessionState: (p: SessionStatePayload) => void;
}

export interface PipelineStripBusWithSubs extends PipelineStripBus {
  subscribePipelineState: (cb: PipelineStateSubscriber) => () => void;
  subscribeSessionState: (cb: SessionStateSubscriber) => () => void;
}

/** Create a local in-process bus for routing pipeline-state and session-state
 *  envelopes into the strip. Mirrors the LayerPanelBus pattern job-0025
 *  established so a single App-layer wiring style covers both panels. */
export function createPipelineStripBus(): PipelineStripBusWithSubs {
  const pipelineSubs = new Set<PipelineStateSubscriber>();
  const sessionSubs = new Set<SessionStateSubscriber>();
  return {
    pushPipelineState: (p) => pipelineSubs.forEach((s) => s(p)),
    pushSessionState: (p) => sessionSubs.forEach((s) => s(p)),
    subscribePipelineState: (cb) => {
      pipelineSubs.add(cb);
      return () => pipelineSubs.delete(cb);
    },
    subscribeSessionState: (cb) => {
      sessionSubs.add(cb);
      return () => sessionSubs.delete(cb);
    },
  };
}

// --- Reducer + state shape --------------------------------------------- //
//
// The reducer enforces Appendix A.7 replace-not-reconcile semantics:
// `pipeline-state` action wholesale replaces the prior `lastPipelineState`
// view-model with the incoming payload. There is no merge step.
//
// We track two separate view-models:
//   - `lastPipelineState`: from the latest `pipeline-state` envelope (the
//     step list + pipeline_id). Drives the rendered step list and the
//     predicate (a) "any step in running state".
//   - `currentPipeline`: from the latest `session-state.current_pipeline`.
//     Drives predicate (b). Kept as `PipelineSnapshot | null` once the
//     type guard narrows it; the underlying `session-state.current_pipeline`
//     ships as `unknown | null` until job-0025's session-surface types are
//     refined (see contracts.ts).

interface PipelineStripState {
  lastPipelineState: PipelineStatePayload | null;
  currentPipeline: PipelineSnapshot | null;
}

type PipelineStripAction =
  | { type: "pipeline-state"; payload: PipelineStatePayload }
  | { type: "session-state"; payload: SessionStatePayload };

function reducer(
  state: PipelineStripState,
  action: PipelineStripAction,
): PipelineStripState {
  switch (action.type) {
    case "pipeline-state": {
      // REPLACE-NOT-RECONCILE per Appendix A.7. The incoming payload IS the
      // snapshot — never merge with prior state.
      return { ...state, lastPipelineState: action.payload };
    }
    case "session-state": {
      // Narrow `current_pipeline` from `unknown | null` to `PipelineSnapshot
      // | null` with a defensive guard. The session-state shape is
      // job-0025-owned and types the field as `unknown | null`; we never
      // invent fields client-side, just guard.
      const cp = narrowCurrentPipeline(action.payload.current_pipeline);
      return { ...state, currentPipeline: cp };
    }
    default:
      return state;
  }
}

function narrowCurrentPipeline(x: unknown): PipelineSnapshot | null {
  if (x === null || x === undefined) return null;
  if (typeof x !== "object") return null;
  const o = x as Record<string, unknown>;
  if (typeof o.pipeline_id !== "string") return null;
  // `steps` may be absent on a freshly-started snapshot; default to [].
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

// --- Cancel-button visibility predicate -------------------------------- //
//
// Explicit cross-envelope check (kickoff step 4). Named separately for
// testability + so the source comment names which envelope feeds which
// condition.

export function shouldShowCancelButton(state: PipelineStripState): boolean {
  // (a) pipeline-state envelope: any step in `running` state?
  const aRunningFromPipelineState =
    state.lastPipelineState?.steps?.some((s) => s.state === "running") ?? false;
  // (b) session-state envelope: `current_pipeline` non-null?
  const bSessionStateHasCurrent = state.currentPipeline !== null;
  return aRunningFromPipelineState || bSessionStateHasCurrent;
}

// --- State colors (FR-WC-8) -------------------------------------------- //

const STATE_COLOR: Record<PipelineStepState, string> = {
  pending: "#9ca3af",   // gray
  running: "#3b82f6",   // blue (pulses — see CSS keyframes)
  complete: "#10b981",  // green
  failed: "#ef4444",    // red
  cancelled: "#eab308", // yellow (Invariant 8: distinct from `failed`)
};

// --- Component --------------------------------------------------------- //

export interface PipelineStripProps {
  /** Subscribe to incoming `pipeline-state` envelopes. */
  subscribePipelineState?: (cb: PipelineStateSubscriber) => () => void;
  /** Subscribe to incoming `session-state` envelopes (for `current_pipeline`). */
  subscribeSessionState?: (cb: SessionStateSubscriber) => () => void;
  /** Emit a `cancel` envelope. Wired to GraceWs.sendCancel by App.tsx. */
  onCancel?: (reason: string | null) => void;
  /** Initial seed for testing/storybook; bypasses subscribe. */
  initialPipelineState?: PipelineStatePayload | null;
  initialSessionState?: SessionStatePayload | null;
}

export function PipelineStrip({
  subscribePipelineState,
  subscribeSessionState,
  onCancel,
  initialPipelineState,
  initialSessionState,
}: PipelineStripProps): JSX.Element | null {
  const [state, dispatch] = useReducer(reducer, {
    lastPipelineState: initialPipelineState ?? null,
    currentPipeline: initialSessionState
      ? narrowCurrentPipeline(initialSessionState.current_pipeline)
      : null,
  });

  useEffect(() => {
    const unsubs: Array<() => void> = [];
    if (subscribePipelineState) {
      unsubs.push(
        subscribePipelineState((p) =>
          dispatch({ type: "pipeline-state", payload: p }),
        ),
      );
    }
    if (subscribeSessionState) {
      unsubs.push(
        subscribeSessionState((p) =>
          dispatch({ type: "session-state", payload: p }),
        ),
      );
    }
    return () => unsubs.forEach((u) => u());
  }, [subscribePipelineState, subscribeSessionState]);

  // Inject the keyframes once at first render. Local <style> tag scoped via
  // attribute selector so we do not depend on a CSS-in-JS framework.
  useKeyframesOnce();

  const showCancel = shouldShowCancelButton(state);
  const steps: PipelineStepSummary[] = state.lastPipelineState?.steps ?? [];

  // The strip renders even when there is no current pipeline, but in an
  // empty/idle visual. This makes the slot's bottom-of-screen position
  // discoverable and avoids layout flicker when the first pipeline starts.
  const isIdle = !state.lastPipelineState && !state.currentPipeline;

  return (
    <section
      data-testid="grace2-pipeline-strip"
      aria-label="pipeline strip"
      style={{
        position: "absolute",
        left: 312, // LayerPanel width 280 + 16 gap + 16 inset (job-0025 slot)
        right: 412, // Chat width 380 + 16 gap + 16 inset (job-0025 slot)
        bottom: 16,
        background: "rgba(20,20,25,0.92)",
        color: "#eee",
        borderRadius: 8,
        boxShadow: "0 4px 24px rgba(0,0,0,0.4)",
        fontFamily: "system-ui, sans-serif",
        fontSize: 12,
        padding: "10px 12px",
        display: "flex",
        flexDirection: "column",
        gap: 8,
        minHeight: isIdle ? 40 : 64,
      }}
    >
      <header style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <strong style={{ fontSize: 13 }}>Pipeline</strong>
        <span
          data-testid="pipeline-id"
          style={{ color: "#888", fontSize: 10 }}
        >
          {state.lastPipelineState?.pipeline_id ??
            state.currentPipeline?.pipeline_id ??
            "idle"}
        </span>
        <span style={{ flex: 1 }} />
        {showCancel && (
          <button
            data-testid="pipeline-cancel"
            aria-label="cancel pipeline"
            onClick={() => onCancel?.("user-cancel")}
            style={{
              background: "#7f1d1d",
              color: "#fee2e2",
              border: "1px solid #b91c1c",
              borderRadius: 4,
              padding: "4px 10px",
              fontSize: 12,
              cursor: "pointer",
            }}
          >
            Cancel
          </button>
        )}
      </header>
      {isIdle ? (
        <p style={{ color: "#888", margin: 0 }}>
          No pipeline running. Ask the agent to start one.
        </p>
      ) : (
        <ol
          data-testid="pipeline-step-list"
          style={{
            listStyle: "none",
            padding: 0,
            margin: 0,
            display: "flex",
            flexDirection: "row",
            flexWrap: "wrap",
            gap: 6,
          }}
        >
          {steps.length === 0 && (
            <li style={{ color: "#888" }}>(no steps yet)</li>
          )}
          {steps.map((step) => (
            <StepChip key={step.step_id} step={step} />
          ))}
        </ol>
      )}
    </section>
  );
}

// --- Step chip --------------------------------------------------------- //

interface StepChipProps {
  step: PipelineStepSummary;
}

function StepChip({ step }: StepChipProps): JSX.Element {
  const color = STATE_COLOR[step.state];
  const pulseStyle: React.CSSProperties =
    step.state === "running"
      ? {
          // CSS keyframes `grace2-pipeline-pulse` defined in <style> below
          animation: "grace2-pipeline-pulse 1.4s ease-in-out infinite",
        }
      : {};
  return (
    <li
      data-testid="pipeline-step"
      data-step-id={step.step_id}
      data-state={step.state}
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 2,
        background: "#222",
        border: `1px solid ${color}`,
        borderRadius: 6,
        padding: "4px 8px",
        minWidth: 140,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <span
          aria-hidden="true"
          data-testid="pipeline-step-dot"
          style={{
            width: 10,
            height: 10,
            borderRadius: "50%",
            background: color,
            display: "inline-block",
            ...pulseStyle,
          }}
        />
        <span
          style={{
            flex: 1,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            fontWeight: 500,
          }}
          title={step.name}
        >
          {step.name}
        </span>
      </div>
      {typeof step.progress_percent === "number" && (
        <div
          data-testid="pipeline-step-progress"
          style={{ fontSize: 10, color: "#aaa" }}
        >
          {step.progress_percent}%
        </div>
      )}
      {step.state === "failed" && (step.error_code || step.error_message) && (
        // Failed steps: render error_code + message inline. The collapsible
        // expandable logs block contents defer to M9 per kickoff §3.
        <div
          data-testid="pipeline-step-error"
          style={{ fontSize: 10, color: "#fca5a5" }}
        >
          {step.error_code && (
            <code style={{ color: "#fecaca" }}>{step.error_code}</code>
          )}
          {step.error_code && step.error_message && ": "}
          {step.error_message}
        </div>
      )}
    </li>
  );
}

// --- Keyframes injection ---------------------------------------------- //
//
// Inject the `grace2-pipeline-pulse` keyframes once at module load time.
// Using a deduping flag rather than a useEffect so SSR / re-mount cycles do
// not produce stacked rules. Scoped via the `data-grace2-pipeline-strip`
// attribute on the <style> tag so a future cleanup can find it.

let _pulseInjected = false;
function useKeyframesOnce(): void {
  // useRef inside useEffect ensures we only touch the DOM in a browser.
  const ref = useRef(false);
  useEffect(() => {
    if (_pulseInjected || ref.current) return;
    if (typeof document === "undefined") return;
    const style = document.createElement("style");
    style.setAttribute("data-grace2-pipeline-strip", "pulse");
    style.textContent = `
@keyframes grace2-pipeline-pulse {
  0%, 100% { opacity: 1; transform: scale(1); }
  50%      { opacity: 0.55; transform: scale(0.85); }
}
`;
    document.head.appendChild(style);
    _pulseInjected = true;
    ref.current = true;
  }, []);
}
