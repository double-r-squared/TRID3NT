// GRACE-2 web — PipelineCard (FR-WC-8; Invariant 8; job-0162 visual redesign).
//
// One card per tool dispatch rendered inline in the chat stream. The card
// transitions through lifecycle states via background tint + animated text
// rather than icons / borderline accents. Memory spec
// `feedback_pipeline_card_visual_states` (2026-06-08):
//
//   pending  → grey-subdued background, greyed text, no right-side indicator
//   running  → normal background, rainbow-gradient animated text + spinner
//   success  → full green-tinted background, normal text, no indicator
//   failure  → full red-tinted background, normal text, no indicator
//   cancelled→ full yellow-tinted background (Invariant 8 keeps it distinct
//              from failed; treated as a terminal non-success on success/fail
//              axis — visually closer to failure but with a yellow tint)
//
// Dropped elements (do NOT reintroduce): blue left-edge accent, checkmark on
// success, "..." running indicator, "completed/running/pending" text labels,
// borderlines between stacked steps. Vertical separation is provided by the
// parent stack's 12-16px gap + each card's own drop shadow + rounded corners.
//
// Accessibility:
//   - `aria-live="polite"` on the card so terminal transitions are announced
//   - Visually-hidden text prefix encodes state for screen readers
//   - `prefers-reduced-motion` falls the rainbow gradient + spinner back to a
//     static neutral colour and a static dot respectively
//
// This component receives a plain PipelineStepSummary prop (no subscription
// logic here). The caller (Chat.tsx) owns the replace-not-reconcile semantics
// + the merge-by-step_id dedupe and passes the current snapshot of each step.

import { PipelineStepSummary, PipelineStepState } from "../contracts";

// --- Reduced-motion detection (SSR-safe) --------------------------------- //

function prefersReducedMotion(): boolean {
  if (typeof window === "undefined" || !window.matchMedia) return false;
  try {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return false;
  }
}

// --- State → visual mapping ---------------------------------------------- //
//
// Background tints are layered over the chat panel's (20,20,25,0.92) so the
// tint reads as a state cue without overwhelming the chat. The pending tint
// is a slight darken; the running state restores normal panel bg; success
// and failure carry a more saturated overlay.

interface CardVisual {
  background: string;
  textColor: string;
  // Screen-reader-only state name; rendered inside a visually-hidden span.
  ariaPrefix: string;
}

function cardVisual(state: PipelineStepState): CardVisual {
  switch (state) {
    case "pending":
      return {
        background: "rgba(255,255,255,0.04)",
        textColor: "#777",
        ariaPrefix: "pending: ",
      };
    case "running":
      return {
        background: "rgba(255,255,255,0.08)",
        textColor: "#eee",
        ariaPrefix: "running: ",
      };
    case "complete":
      return {
        background: "rgba(40, 200, 100, 0.18)",
        textColor: "#eee",
        ariaPrefix: "completed: ",
      };
    case "failed":
      return {
        background: "rgba(220, 60, 60, 0.22)",
        textColor: "#eee",
        ariaPrefix: "failed: ",
      };
    case "cancelled":
      return {
        background: "rgba(220, 180, 40, 0.22)",
        textColor: "#eee",
        ariaPrefix: "cancelled: ",
      };
  }
}

// --- Spinner ------------------------------------------------------------- //
//
// 14px circular spinner. Pure SVG so it inherits color via `currentColor` and
// no PNG/font dependency. Animation is a 1s linear rotation; falls back to a
// static dot under `prefers-reduced-motion`.

function Spinner({ reduced }: { reduced: boolean }): JSX.Element {
  if (reduced) {
    return (
      <span
        data-testid="pipeline-card-indicator"
        data-variant="static-dot"
        style={{
          width: 8,
          height: 8,
          borderRadius: 4,
          background: "#bbb",
          display: "inline-block",
          flexShrink: 0,
        }}
      />
    );
  }
  return (
    <span
      data-testid="pipeline-card-indicator"
      data-variant="spinner"
      style={{
        width: 14,
        height: 14,
        display: "inline-block",
        flexShrink: 0,
        animation: "grace2-spin 1s linear infinite",
        transformOrigin: "50% 50%",
      }}
      aria-hidden="true"
    >
      <svg
        viewBox="0 0 14 14"
        width="14"
        height="14"
        style={{ display: "block" }}
      >
        <circle
          cx="7"
          cy="7"
          r="5.5"
          stroke="rgba(255,255,255,0.18)"
          strokeWidth="1.5"
          fill="none"
        />
        <path
          d="M 7 1.5 A 5.5 5.5 0 0 1 12.5 7"
          stroke="#eee"
          strokeWidth="1.5"
          fill="none"
          strokeLinecap="round"
        />
      </svg>
    </span>
  );
}

// --- Card ----------------------------------------------------------------- //

export interface PipelineCardProps {
  step: PipelineStepSummary;
}

export function PipelineCard({ step }: PipelineCardProps): JSX.Element {
  const visual = cardVisual(step.state);
  const reduced = prefersReducedMotion();
  const isRunning = step.state === "running";
  const isFailed = step.state === "failed";

  // The label uses an animated rainbow gradient when running (unless the
  // user prefers reduced motion). Background-clip:text is the gradient
  // technique; the fallback is the visual.textColor.
  const labelStyle: React.CSSProperties = isRunning && !reduced
    ? {
        backgroundImage:
          "linear-gradient(90deg, #FF6B6B, #FFD93D, #6BCB77, #4D96FF, #B266FF, #FF6B6B)",
        backgroundSize: "300% 100%",
        WebkitBackgroundClip: "text",
        backgroundClip: "text",
        WebkitTextFillColor: "transparent",
        color: "transparent",
        animation: "grace2-hue-cycle 3s linear infinite",
      }
    : { color: visual.textColor };

  return (
    <div
      data-testid="pipeline-card"
      data-step-id={step.step_id}
      data-state={step.state}
      aria-live="polite"
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        fontSize: 12,
        lineHeight: "1.4",
        padding: "8px 10px",
        borderRadius: 6,
        background: visual.background,
        boxShadow: "0 1px 3px rgba(0,0,0,0.25)",
        fontFamily: "ui-monospace, 'Cascadia Code', 'Fira Code', monospace",
        position: "relative",
        overflow: "hidden",
        transition: "background-color 200ms ease-in-out",
      }}
    >
      {/* Visually-hidden screen-reader state prefix. */}
      <span
        style={{
          position: "absolute",
          width: 1,
          height: 1,
          padding: 0,
          margin: -1,
          overflow: "hidden",
          clip: "rect(0,0,0,0)",
          whiteSpace: "nowrap",
          border: 0,
        }}
      >
        {visual.ariaPrefix}
      </span>
      <span
        data-testid="pipeline-card-name"
        style={{
          flex: 1,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
          ...labelStyle,
        }}
        title={step.name}
      >
        {step.name}
      </span>
      {isRunning && <Spinner reduced={reduced} />}
      {isFailed && (step.error_code || step.error_message) && (
        <span
          data-testid="pipeline-card-error"
          style={{ color: "#fca5a5", fontSize: 11, marginLeft: 4 }}
          title={step.error_message ?? undefined}
        >
          {step.error_code ?? "error"}
        </span>
      )}
    </div>
  );
}

// --- Keyframes ----------------------------------------------------------- //
//
// Injected once into <head> on first import. CSS modules are not in use
// here, so we mount a global <style> with the two animations the card
// references. `prefers-reduced-motion` is handled per-render (above), not in
// CSS, so the keyframes remain unconditional.

const KEYFRAMES_ID = "grace2-pipeline-card-keyframes";

function ensureKeyframes(): void {
  if (typeof document === "undefined") return;
  if (document.getElementById(KEYFRAMES_ID)) return;
  const style = document.createElement("style");
  style.id = KEYFRAMES_ID;
  style.textContent = `
@keyframes grace2-hue-cycle {
  0%   { background-position:   0% 50%; }
  100% { background-position: 300% 50%; }
}
@keyframes grace2-spin {
  0%   { transform: rotate(0deg); }
  100% { transform: rotate(360deg); }
}
`;
  document.head.appendChild(style);
}

ensureKeyframes();
