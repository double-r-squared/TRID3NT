// GRACE-2 web - BboxProgressOverlay (NATE map/loading-UX polish, item 1).
//
// A loading animation anchored to the projected AOI bbox screen rectangle (the
// SAME `aoiScreenRect` the LayerLegend + SequenceScrubber pin against, lifted
// from Map.tsx via onAoiScreenRectChange). It communicates "the map is working"
// without a separate spinner chrome, and degrades honestly:
//
//   - mode "fill"  -> a FILL-GRID SHIMMER inside the bbox (futuristic waving
//     fill). Used on the FIRST layer fetch when nothing is on the map yet, so
//     covering the box is fine.
//   - mode "scan"  -> a SCAN-BORDER: a bright segment sweeping AROUND the bbox
//     edge. Used for subsequent loads / connecting / a running sim, so it never
//     covers existing layers. `tone` picks blue (loading / connecting) or purple
//     (a long-running sim - matches the sim pipeline-card color).
//
// The state DECISION lives in lib/bbox_progress.resolveBboxProgress (pure +
// unit-tested); this component is the render half: given a rect + mode + tone it
// paints the right animation. It is purely presentational - no signals logic.
//
// prefers-reduced-motion: the sweeping/shimmering motion is replaced by a
// SUBTLE STATIC state (a faint static fill tint for "fill", a faint static
// border for "scan"), so the cue still reads without motion.
//
// pointer-events:none throughout - the overlay never intercepts map gestures.

import { useEffect } from "react";
import type { ScreenRect } from "../lib/legend_snap";
import type { BboxProgressMode, BboxProgressTone } from "../lib/bbox_progress";
import { prefersReducedMotion } from "./PipelineCard";

export interface BboxProgressOverlayProps {
  /** The projected AOI bbox screen rectangle, or null when there is no AOI. */
  rect: ScreenRect | null;
  /** Which animation to paint ("none" renders nothing). */
  mode: BboxProgressMode;
  /** Scan-border tone (ignored for "fill"). */
  tone: BboxProgressTone;
  /**
   * Test seam: force the reduced-motion branch on/off. Undefined (default)
   * consults the live `prefers-reduced-motion` media query.
   */
  reducedMotionOverride?: boolean;
}

// Tone -> CSS color. Blue mirrors the bbox-pick accent (#3b82f6 family); purple
// mirrors the sim-in-progress pipeline-card color (#a855f7 family).
const TONE_COLOR: Record<BboxProgressTone, string> = {
  blue: "#4aa3ff",
  purple: "#a855f7",
};

// Keyframes are injected once at module-eval (idempotent, id-guarded), mirroring
// App.tsx's ensureAppSpinKeyframes pattern. SSR/test-safe (no-op without document).
const KEYFRAMES_ID = "grace2-bbox-progress-keyframes";
function ensureKeyframes(): void {
  if (typeof document === "undefined") return;
  if (document.getElementById(KEYFRAMES_ID)) return;
  const style = document.createElement("style");
  style.id = KEYFRAMES_ID;
  style.textContent = `
@keyframes grace2-bbox-fill-shimmer {
  0%   { background-position: 0% 0%, 0 0, 0 0; opacity: 0.35; }
  50%  { opacity: 0.6; }
  100% { background-position: 0% 200%, 0 0, 0 0; opacity: 0.35; }
}
@keyframes grace2-bbox-scan-sweep {
  0%   { transform: translateX(-100%); }
  100% { transform: translateX(100%); }
}
@keyframes grace2-bbox-border-pulse {
  0%   { opacity: 0.45; }
  50%  { opacity: 0.9; }
  100% { opacity: 0.45; }
}
`;
  document.head.appendChild(style);
}
ensureKeyframes();

export function BboxProgressOverlay({
  rect,
  mode,
  tone,
  reducedMotionOverride,
}: BboxProgressOverlayProps): JSX.Element | null {
  // Re-assert the keyframes if a hot-reload / late mount dropped the style node.
  useEffect(() => {
    ensureKeyframes();
  }, []);

  if (mode === "none" || !rect) return null;

  const width = rect.right - rect.left;
  const height = rect.bottom - rect.top;
  if (!(width > 0) || !(height > 0)) return null;

  const reduced =
    reducedMotionOverride !== undefined
      ? reducedMotionOverride
      : prefersReducedMotion();
  const color = TONE_COLOR[tone];

  // The anchored frame: absolutely positioned over the bbox extent. position is
  // relative to the map container (which fills the viewport), matching the
  // legend/scrubber anchoring convention. Never intercepts pointer events.
  const frameStyle: React.CSSProperties = {
    position: "absolute",
    left: rect.left,
    top: rect.top,
    width,
    height,
    pointerEvents: "none",
    boxSizing: "border-box",
    // Below the legend/scrubber (z 51) + panels, above the map overlays.
    zIndex: 12,
    overflow: "hidden",
    borderRadius: 4,
  };

  if (mode === "fill") {
    // FILL-GRID SHIMMER: a faint grid lattice + a vertical sheen that waves
    // through the box. Reduced-motion -> a static faint tint + grid (no wave).
    const gridColor = "rgba(74,163,255,0.18)";
    const fillStyle: React.CSSProperties = reduced
      ? {
          ...frameStyle,
          // Static: a faint tint + grid so the "filling" cue still reads.
          background: `
            linear-gradient(${gridColor} 1px, transparent 1px) 0 0 / 100% 22px,
            linear-gradient(90deg, ${gridColor} 1px, transparent 1px) 0 0 / 22px 100%,
            rgba(74,163,255,0.06)`,
          opacity: 0.5,
          border: `1px solid rgba(74,163,255,0.30)`,
        }
      : {
          ...frameStyle,
          background: `
            linear-gradient(180deg, rgba(74,163,255,0.0) 0%, rgba(74,163,255,0.22) 50%, rgba(74,163,255,0.0) 100%) 0 0 / 100% 200%,
            linear-gradient(${gridColor} 1px, transparent 1px) 0 0 / 100% 22px,
            linear-gradient(90deg, ${gridColor} 1px, transparent 1px) 0 0 / 22px 100%`,
          border: `1px solid rgba(74,163,255,0.30)`,
          animation: "grace2-bbox-fill-shimmer 2.2s ease-in-out infinite",
        };
    return (
      <div
        data-testid="grace2-bbox-progress-overlay"
        data-mode="fill"
        data-reduced={reduced ? "true" : "false"}
        aria-hidden
        style={fillStyle}
      />
    );
  }

  // mode === "scan": a bright segment that sweeps AROUND the bbox edge (never
  // covers the interior, so existing layers stay visible). We draw a pulsing
  // border + a single sweeping highlight bar that travels along the top edge
  // (the most legible sweep on a rectangle); the pulsing border carries the
  // around-the-edge feel without obscuring the box. Reduced-motion -> a faint
  // static border only.
  const scanBorderStyle: React.CSSProperties = reduced
    ? {
        ...frameStyle,
        border: `1.5px solid ${color}`,
        opacity: 0.5,
      }
    : {
        ...frameStyle,
        border: `1.5px solid ${color}`,
        animation: "grace2-bbox-border-pulse 1.6s ease-in-out infinite",
        boxShadow: `0 0 8px ${color}55`,
      };

  return (
    <div
      data-testid="grace2-bbox-progress-overlay"
      data-mode="scan"
      data-tone={tone}
      data-reduced={reduced ? "true" : "false"}
      aria-hidden
      style={scanBorderStyle}
    >
      {!reduced ? (
        // The sweeping highlight bar travels left->right across the box, clipped
        // to the frame so it reads as a scan line along the top edge band.
        <div
          data-testid="grace2-bbox-progress-sweep"
          aria-hidden
          style={{
            position: "absolute",
            top: 0,
            left: 0,
            height: "100%",
            width: "40%",
            background: `linear-gradient(90deg, transparent 0%, ${color}33 50%, transparent 100%)`,
            animation: "grace2-bbox-scan-sweep 1.8s linear infinite",
            pointerEvents: "none",
          }}
        />
      ) : null}
    </div>
  );
}
