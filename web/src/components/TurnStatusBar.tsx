// GRACE-2 web - TurnStatusBar (NATE 2026-06-29: "never leave the user in the
// dark").
//
// A single PERSISTENT status surface pinned at the bottom of the chat scroll
// (just below the ephemeral ThinkingIndicator) that makes the agent's CURRENT
// activity ALWAYS legible for the WHOLE in-flight turn. The problem it solves:
// the ThinkingIndicator vanishes the instant the first tool card lands or text
// streams, so during a long DEM / data fetch or an off-box solve the user was
// "left in the dark - no idea if anything is loading at all." This bar STAYS up
// for the entire turn and NAMES what is happening:
//
//   - "working"     -> a turn is in flight (fetching data / running a tool):
//                      blue spinner + the running tool's humanized label (so a
//                      long fetch reads as WORKING, not frozen).
//   - "simulating"  -> a heavy off-box solver step (role=compute / SFINCS /
//                      MODFLOW / Pelicun on AWS Batch) is RUNNING: violet
//                      spinner + a live m:ss elapsed ticker so the user knows
//                      the sim is ALIVE and roughly how long it has been going.
//   - "awaiting"    -> the turn PAUSED for the user (an unanswered confirm gate
//                      - mesh-resolution / spatial-input / credential / region):
//                      amber pulse + "Action needed" so the stop reads as a
//                      PROMPT awaiting input, NOT a silent dead-stop.
//   - "hidden"      -> idle (renders nothing).
//
// This is a PRESENTATION-ONLY surface over EXISTING emitted signals (the
// pipeline view-model + the unanswered-gate maps Chat already tracks). It adds
// no backend wiring. The pure deriver `deriveTurnStatus` lives in Chat.tsx
// (alongside deriveComposerPhase / isThinkingActive) so it is unit-testable
// without mounting Chat; this file owns the render half + the status type.

import type { CSSProperties } from "react";
import type { PipelineStepSummary } from "../contracts";
import { Spinner, formatDuration, prefersReducedMotion, useRunningElapsedMs } from "./PipelineCard";

// --- Status type (shared with Chat's deriver) ---------------------------- //

export type TurnStatusKind = "hidden" | "working" | "simulating" | "awaiting";

export type TurnStatus =
  | { kind: "hidden" }
  | { kind: "working"; label: string }
  | { kind: "simulating"; label: string; solverStep: PipelineStepSummary }
  | { kind: "awaiting"; label: string };

// Per-kind accent + surface treatment. Mirrors the chat's existing palette:
// blue for active work (BboxProgressOverlay grid), compute-violet for the
// off-box solver card (PipelineCard COMPUTE_ACCENT), amber for the confirm
// gates (ResolutionPickerCard / payload-warning lineage).
interface KindVisual {
  accent: string;
  bg: string;
}
const KIND_VISUAL: Record<Exclude<TurnStatusKind, "hidden">, KindVisual> = {
  working: { accent: "#4aa3ff", bg: "rgba(74,163,255,0.10)" },
  simulating: { accent: "#b9a4ff", bg: "rgba(124,92,255,0.14)" },
  awaiting: { accent: "#eab308", bg: "rgba(234,179,8,0.15)" },
};

// A stable placeholder step so the elapsed hook can be called unconditionally
// (hooks rule) even when the status is not "simulating". `pending` => the hook
// returns 0 and never starts a timer.
const EMPTY_STEP: PipelineStepSummary = {
  step_id: "__turn-status-empty__",
  name: "",
  tool_name: "",
  state: "pending",
};

// Keyframes (mounted once, id-guarded) - a gentle attention pulse for the
// "awaiting" gate so the bar reads as a PROMPT, not a frozen line.
const KEYFRAMES_ID = "grace2-turn-status-keyframes";
function ensureKeyframes(): void {
  if (typeof document === "undefined") return;
  if (document.getElementById(KEYFRAMES_ID)) return;
  const style = document.createElement("style");
  style.id = KEYFRAMES_ID;
  style.textContent = `
@keyframes grace2-turn-status-pulse {
  0%   { opacity: 0.55; }
  50%  { opacity: 1.00; }
  100% { opacity: 0.55; }
}
`;
  document.head.appendChild(style);
}
ensureKeyframes();

export interface TurnStatusBarProps {
  status: TurnStatus;
}

export function TurnStatusBar({ status }: TurnStatusBarProps): JSX.Element | null {
  const reduced = prefersReducedMotion();
  // Hooks must run unconditionally: feed the solver step when simulating, else
  // the placeholder (the hook returns 0 for a non-running step).
  const solverStep = status.kind === "simulating" ? status.solverStep : EMPTY_STEP;
  const elapsedMs = useRunningElapsedMs(solverStep);

  if (status.kind === "hidden") return null;

  const visual = KIND_VISUAL[status.kind];
  const awaiting = status.kind === "awaiting";
  const simulating = status.kind === "simulating";

  const containerStyle: CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 8,
    padding: "7px 11px",
    borderRadius: 8,
    background: visual.bg,
    border: `1px solid ${visual.accent}55`,
    borderLeft: `3px solid ${visual.accent}`,
    color: visual.accent,
    fontSize: 12,
    fontWeight: 600,
    lineHeight: 1.35,
    fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, sans-serif",
    // The awaiting gate gently pulses so the paused-for-you state stands out.
    animation:
      awaiting && !reduced
        ? "grace2-turn-status-pulse 1.6s ease-in-out infinite"
        : undefined,
  };

  return (
    <div
      data-testid="turn-status-bar"
      data-status-kind={status.kind}
      role="status"
      aria-live="polite"
      style={containerStyle}
    >
      <span
        aria-hidden="true"
        style={{ display: "inline-flex", alignItems: "center", color: visual.accent, flexShrink: 0 }}
      >
        {awaiting ? (
          // A filled dot (not a spinner) - the agent is WAITING on the user, not
          // doing work, so a spinning glyph would mislead.
          <span
            style={{
              width: 9,
              height: 9,
              borderRadius: "50%",
              background: visual.accent,
              display: "inline-block",
            }}
          />
        ) : (
          <Spinner reduced={reduced} />
        )}
      </span>
      <span data-testid="turn-status-label" style={{ flex: 1, minWidth: 0 }}>
        {status.label}
      </span>
      {simulating && (
        <span
          data-testid="turn-status-elapsed"
          style={{
            flexShrink: 0,
            fontVariantNumeric: "tabular-nums",
            color: "#e5e7eb",
            fontWeight: 600,
          }}
        >
          {formatDuration(elapsedMs)}
        </span>
      )}
    </div>
  );
}
