// GRACE-2 web — SequenceScrubber (sequential-layer-grouping feature).
//
// A bottom-center overlay that steps the ACTIVE sequential layer group's
// frames. NATE's ask: enumerated temporal raster stacks (e.g. 3 HRRR forecast
// hours F+01h / F+03h / F+06h) collapse into ONE group you can step through.
//
// This component is the map-overlay half of that: a horizontal slider +
// LEFT/RIGHT + play/pause that drives the SAME visibility toggling the
// LayerPanel group row uses (it never touches the map directly — stepping is
// "show frame i, hide the rest" through the existing LayerPanel visibility
// callback). It is rendered FROM WITHIN LayerPanel (so it shares the panel's
// frame state) and pins itself bottom-center of the viewport, mirroring the
// LayerLegend's bottom-center fallback placement.
//
// It only appears when a sequential group is active (LayerPanel returns null
// otherwise — see SequentialGroup detection in LayerPanel.tsx). Pure
// presentation: all frame state + the step callback come in as props.

import { useCallback, useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import {
  IconArrowLeft,
  IconArrowRight,
  IconPlay,
  IconPause,
} from "./icons";

export interface SequenceScrubberProps {
  /** Short group label, e.g. the shared source/tool ("HRRR forecast"). */
  label: string;
  /** Per-frame short labels in series order, e.g. ["F+01h","F+03h","F+06h"]. */
  frameLabels: string[];
  /** Active frame index (0-based) into `frameLabels`. */
  activeIndex: number;
  /** Step to an absolute frame index (clamped by the owner). */
  onStep: (index: number) => void;
  /** Whether the scrubber is auto-advancing. */
  playing: boolean;
  /** Toggle play/pause. */
  onPlayToggle: () => void;
  /** Auto-advance cadence in ms while playing. Default 1100. */
  intervalMs?: number;
}

/** Clamp `i` into [0, n) with wraparound so the scrubber loops cleanly. */
export function wrapIndex(i: number, n: number): number {
  if (n <= 0) return 0;
  return ((i % n) + n) % n;
}

export function SequenceScrubber({
  label,
  frameLabels,
  activeIndex,
  onStep,
  playing,
  onPlayToggle,
  intervalMs = 1100,
}: SequenceScrubberProps): JSX.Element | null {
  const n = frameLabels.length;
  // Hold the latest active index in a ref so the play interval always advances
  // from the current frame without re-arming the timer every step.
  const activeRef = useRef(activeIndex);
  activeRef.current = activeIndex;
  const onStepRef = useRef(onStep);
  onStepRef.current = onStep;

  const stepBy = useCallback(
    (delta: number): void => {
      onStepRef.current(wrapIndex(activeRef.current + delta, n));
    },
    [n],
  );

  // Auto-advance while playing. Re-arms only when play state / length /
  // cadence changes — never on every frame (the ref keeps it current).
  useEffect(() => {
    if (!playing || n <= 1) return;
    const id = window.setInterval(() => stepBy(1), intervalMs);
    return () => window.clearInterval(id);
  }, [playing, n, intervalMs, stepBy]);

  if (n === 0) return null;

  const safeIndex = wrapIndex(activeIndex, n);
  const frameLabel = frameLabels[safeIndex] ?? "";

  // Portal to document.body so `fixed` bottom-center resolves against the
  // VIEWPORT, not the LayerPanel's transformed/filtered stacking context (the
  // panel is absolutely positioned + backdrop-filtered — same reason
  // ConfirmationDialog portals). This keeps the scrubber pinned bottom-center
  // of the screen while still being mounted from within LayerPanel.
  return createPortal(
    <div
      data-testid="grace2-sequence-scrubber"
      role="group"
      aria-label={`${label} sequence scrubber`}
      style={{
        position: "fixed",
        bottom: 24,
        left: "50%",
        transform: "translateX(-50%)",
        display: "flex",
        alignItems: "center",
        gap: 10,
        padding: "8px 12px",
        // Joins the panel surface family (matches LayerLegend chrome).
        background: "rgba(17,18,23,0.82)",
        backdropFilter: "blur(6px)",
        WebkitBackdropFilter: "blur(6px)",
        border: "1px solid rgba(255,255,255,0.08)",
        borderRadius: 10,
        boxShadow: "0 2px 12px rgba(0,0,0,0.45)",
        fontFamily: "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
        color: "#e8e8ec",
        // The slider/buttons are interactive, but the chrome lets nothing else
        // through (it's a control surface, unlike the legend).
        zIndex: 11,
        minWidth: 320,
        maxWidth: 560,
      }}
    >
      {/* Group label + frame readout. */}
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          minWidth: 0,
          flexShrink: 0,
          lineHeight: 1.15,
        }}
      >
        <span
          data-testid="scrubber-group-label"
          title={label}
          style={{
            fontSize: 11,
            fontWeight: 600,
            color: "#cfd4db",
            maxWidth: 150,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {label}
        </span>
        <span
          data-testid="scrubber-frame-label"
          style={{
            fontSize: 10.5,
            color: "#9aa1ab",
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {frameLabel} ({safeIndex + 1}/{n})
        </span>
      </div>

      <ScrubButton
        testId="scrubber-prev"
        label="Previous frame"
        onClick={() => stepBy(-1)}
        disabled={n <= 1}
      >
        <IconArrowLeft size={15} />
      </ScrubButton>

      <ScrubButton
        testId="scrubber-play"
        label={playing ? "Pause sequence" : "Play sequence"}
        onClick={onPlayToggle}
        disabled={n <= 1}
      >
        {playing ? <IconPause size={15} /> : <IconPlay size={15} />}
      </ScrubButton>

      <ScrubButton
        testId="scrubber-next"
        label="Next frame"
        onClick={() => stepBy(1)}
        disabled={n <= 1}
      >
        <IconArrowRight size={15} />
      </ScrubButton>

      {/* The slider — one detent per frame; dragging steps frames. */}
      <input
        type="range"
        min={0}
        max={Math.max(0, n - 1)}
        step={1}
        value={safeIndex}
        onChange={(e) => onStep(wrapIndex(Number(e.target.value), n))}
        aria-label={`${label} frame`}
        data-testid="scrubber-slider"
        style={{
          flex: 1,
          minWidth: 120,
          height: 16,
          accentColor: "#4aa3ff",
          cursor: "pointer",
        }}
      />
    </div>,
    document.body,
  );
}

interface ScrubButtonProps {
  testId: string;
  label: string;
  onClick: () => void;
  disabled?: boolean;
  children: React.ReactNode;
}

function ScrubButton({
  testId,
  label,
  onClick,
  disabled,
  children,
}: ScrubButtonProps): JSX.Element {
  return (
    <button
      type="button"
      data-testid={testId}
      aria-label={label}
      title={label}
      onClick={onClick}
      disabled={disabled}
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        width: 26,
        height: 26,
        flexShrink: 0,
        padding: 0,
        background: "rgba(255,255,255,0.06)",
        border: "1px solid rgba(255,255,255,0.08)",
        borderRadius: 7,
        color: disabled ? "#5a626d" : "#cfd4db",
        cursor: disabled ? "default" : "pointer",
        transition: "color 120ms ease, background 120ms ease",
      }}
    >
      {children}
    </button>
  );
}
