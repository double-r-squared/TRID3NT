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

import { useEffect, useRef, useState } from "react";
import { PipelineStepSummary, PipelineStepState } from "../contracts";

// --- Duration formatting + live ticker (job-0264) ------------------------ //
//
// ELEVATED tool-timer requirement (feedback_pipeline_card_humanized_labels):
//   (a) running cards show a live (mm:ss) ticker next to the spinner so the
//       user can see how long a tool has been running;
//   (b) completed / failed / cancelled cards show the AUTHORITATIVE duration
//       the agent stamped (`step.duration_ms`), so the displayed number is
//       deterministic — the client ticker is purely cosmetic between
//       envelopes.
//
// The "m:ss" format matches the memory spec's label table (e.g. "2:34").
// Hours roll into the minutes field (e.g. 75min → "75:00") — solver runs can
// exceed an hour and a leading-hours field would clutter the inline card.

/** Format whole milliseconds as "m:ss" (minutes uncapped, seconds 00-59). */
export function formatDuration(ms: number): string {
  const totalSeconds = Math.max(0, Math.floor(ms / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${seconds.toString().padStart(2, "0")}`;
}

// Terminal states carry the authoritative duration; running shows a ticker.
const TERMINAL_STATES: ReadonlySet<PipelineStepState> = new Set([
  "complete",
  "failed",
  "cancelled",
]);

/**
 * The live elapsed-ms for a *running* step, ticking once per second.
 *
 * Anchor preference:
 *   1. ``step.started_at`` (server truth) — survives remounts / reconnects so
 *      the ticker reflects real elapsed time, not time-since-this-mount.
 *   2. a local mount timestamp fallback when ``started_at`` is absent (older
 *      agents, or the pending→running frame raced ahead of the stamp).
 *
 * Returns 0 and does not tick for non-running steps (the caller renders the
 * authoritative ``duration_ms`` instead). SSR-safe: ``Date.now`` only.
 *
 * Exported (job-0280) so the mobile collapsed-sheet active-tool strip shows
 * the SAME elapsed value as the card — one timer implementation, no fork.
 */
export function useRunningElapsedMs(step: PipelineStepSummary): number {
  const isRunning = step.state === "running";
  // Resolve the anchor (epoch ms) once per running span. started_at is an
  // ISO-8601 string with a literal Z; Date.parse handles it. NaN (unparseable)
  // falls back to the local mount time.
  const anchorRef = useRef<number | null>(null);
  if (isRunning && anchorRef.current === null) {
    const parsed = step.started_at ? Date.parse(step.started_at) : NaN;
    anchorRef.current = Number.isNaN(parsed) ? Date.now() : parsed;
  }
  if (!isRunning) {
    // Reset so a future re-run (same component instance) re-anchors cleanly.
    anchorRef.current = null;
  }

  const [elapsed, setElapsed] = useState<number>(() =>
    isRunning && anchorRef.current !== null
      ? Math.max(0, Date.now() - anchorRef.current)
      : 0,
  );

  useEffect(() => {
    if (!isRunning) {
      setElapsed(0);
      return;
    }
    const anchor = anchorRef.current ?? Date.now();
    // Tick immediately so the first paint isn't a stale 0, then every second.
    const tick = (): void => setElapsed(Math.max(0, Date.now() - anchor));
    tick();
    const id = window.setInterval(tick, 1000);
    return () => window.clearInterval(id);
    // started_at change re-arms the interval against the new anchor.
  }, [isRunning, step.started_at]);

  return isRunning ? elapsed : 0;
}

// --- Reduced-motion detection (SSR-safe) --------------------------------- //
// Exported (job-0280) for the collapsed-sheet strip's spinner fallback.

export function prefersReducedMotion(): boolean {
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
// Exported (job-0280) so the collapsed-sheet strip reuses the exact spinner.

export function Spinner({ reduced }: { reduced: boolean }): JSX.Element {
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

// --- Humanized step label ------------------------------------------------ //
//
// Memory spec `feedback_pipeline_card_humanized_labels` (job-0173 Part 1):
// the agent emits the Gemini-reasoning step as the internal token
// `llm_generation`. The user-facing surface should read `Thinking…` instead —
// "no internal terms in user-facing surfaces" (codified web-lesson #3 from
// job-0086 et al.). The mapping is keyed on the verbatim emitted `step.name`;
// unknown names pass through unchanged so engineer-named tools continue to
// render their own labels.

const HUMANIZED_STEP_NAMES: Record<string, string> = {
  llm_generation: "Thinking…",
};

export function humanizeStepName(rawName: string): string {
  return HUMANIZED_STEP_NAMES[rawName] ?? rawName;
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
  const isTerminal = TERMINAL_STATES.has(step.state);

  // job-0264 tool timer. While running: a cosmetic live (m:ss) ticker.
  // On a terminal state: the AUTHORITATIVE duration the agent stamped
  // (step.duration_ms). The ticker hook returns 0 for non-running steps.
  const liveElapsedMs = useRunningElapsedMs(step);
  const hasAuthoritativeDuration =
    isTerminal && step.duration_ms !== null && step.duration_ms !== undefined;
  // Timer text precedence: authoritative terminal duration > running ticker.
  // Pending / terminal-without-duration show no timer (nothing to count).
  const timerText: string | null = hasAuthoritativeDuration
    ? formatDuration(step.duration_ms as number)
    : isRunning
      ? formatDuration(liveElapsedMs)
      : null;

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
        title={humanizeStepName(step.name)}
      >
        {humanizeStepName(step.name)}
      </span>
      {timerText !== null && (
        <span
          data-testid="pipeline-card-timer"
          data-authoritative={hasAuthoritativeDuration ? "true" : "false"}
          aria-hidden="true"
          style={{
            fontVariantNumeric: "tabular-nums",
            fontSize: 11,
            // Running: dimmed so the rainbow label stays the focus. Terminal:
            // slightly brighter since the spinner is gone and this is the
            // card's only right-side affordance.
            color: isRunning ? "rgba(255,255,255,0.55)" : "rgba(255,255,255,0.7)",
            flexShrink: 0,
            // Lock min-width so the ticking digits don't jitter the layout.
            minWidth: 30,
            textAlign: "right",
          }}
        >
          {timerText}
        </span>
      )}
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
