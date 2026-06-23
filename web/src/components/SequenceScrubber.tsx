// GRACE-2 web — SequenceScrubber (sequential-layer-grouping feature).
//
// A bottom-center overlay that steps the ACTIVE sequential layer group's
// frames. NATE's ask: enumerated temporal raster stacks (e.g. 3 HRRR forecast
// hours F+01h / F+03h / F+06h) collapse into ONE group you can step through.
//
// This component is the map-overlay half of that: a horizontal slider +
// LEFT/RIGHT that drives the SAME visibility toggling the LayerPanel group row
// uses (it never touches the map directly — stepping is "show frame i, hide
// the rest" through the existing LayerPanel visibility callback). It is
// rendered FROM WITHIN LayerPanel (so it shares the panel's frame state) and
// pins itself bottom-center of the AOI bbox when `aoiRect` is provided, or
// falls back to viewport bottom-center otherwise (mirroring the LayerLegend's
// bottom-center fallback placement).
//
// Layout: `▶ < ——●—— > x/N` — a PLAY/PAUSE toggle, prev-arrow, track/slider,
// next-arrow, plus a compact `x/N` readout. The group label and frame label are
// omitted from the scrubber (they show in the LayerPanel group row).
//
// JOB WEB-ANIM (#157.3): NATE wants the play/pause button back ON the scrubber
// (it had been folded into the LayerPanel group header). The auto-advance
// INTERVAL no longer lives here either — the module-level AnimationController
// owns it (so playback survives a panel unmount); this component is now pure
// presentation that just reflects `playing` and toggles it via onPlayToggle.
//
// It is rendered FROM App.tsx (JOB WEB-ANIM #157.2) and appears WHENEVER a
// sequential group is active on the controller — regardless of whether the
// Layers panel is open. Pure presentation: all frame state + callbacks come in
// as props.

import { useCallback, useRef } from "react";
import { createPortal } from "react-dom";
import {
  IconArrowLeft,
  IconArrowRight,
  IconPlay,
  IconPause,
} from "./icons";
import { aoiScaleFactor, type ScreenRect } from "../lib/legend_snap";
import { useIsMobile } from "../hooks/useIsMobile";

// UNIFIED SCALING (NATE 2026-06-22): the scrubber and the LayerLegend now SHARE
// one scaling story so they behave identically as you zoom -> when aoiRect is
// present the scrubber's WIDTH tracks the AOI bbox on-screen width (right-left),
// exactly like the legend's keys (LayerLegend sets each key width = clamped
// barWidth). No extra horizontal padding band that would make the bar narrower
// or wider than the bbox. The inner chrome (font/counter) scales with the shared
// aoiScaleFactor so the whole widget grows/shrinks with the box like the legend.
//
// The bbox-width minimum keeps the buttons tappable on a tiny zoomed-out box.
// (Previously a 220..480 base band + a uniform transform: scale() left visible
// padding on both sides of the bbox, and a hide-below-threshold made the
// scrubber VANISH on zoom-out while the legend stayed -> NATE wanted them
// consistent, so both now persist and both track the bbox width.)
const SCRUBBER_MIN_WIDTH = 200;
// AOI-less fallback width band (no bbox to track -> a sensible fixed band).
const SCRUBBER_FALLBACK_WIDTH = 360;

// MOBILE Z-ORDER (NATE 2026-06-22): on mobile the chat is a bottom sheet at
// zIndex 32 (Chat.tsx mobileSheetContainerStyle). The scrubber must sit
// UNDERNEATH it so it never covers the chat composer. On desktop the chat is a
// right-side panel (not over the bottom-center scrubber), so the scrubber keeps
// its original higher z there.
const SCRUBBER_Z_DESKTOP = 51;
const SCRUBBER_Z_MOBILE = 31; // below the mobile chat sheet (zIndex 32)

// ITEM 6 (NATE 2026-06-22): on MOBILE the chat is a bottom sheet anchored at the
// screen bottom (Chat.tsx mobileSheetContainerStyle, bottom:0). Z-order alone is
// NOT enough  -  NATE reported the scrubber STILL overlapping the composer because
// it was POSITIONED in the same bottom band (aoiRect.bottom+12, or the bottom:24
// fallback). We CLAMP the scrubber so its bottom edge always clears the collapsed
// sheet (composer). This mirrors the legend's MOBILE_SHEET_CLEARANCE_PX (Map.tsx)
// so the two overlays use one clearance story.
//
// BUG 3 (NATE 2026-06-23): the scrubber STILL overlapped the composer on a
// notched device. Root cause - the old mobile clamp computed `top` from
// `window.innerHeight - CLEARANCE - height`, which does NOT include the device
// `safe-area-inset-bottom` (env() is invisible to JS), so on an iPhone the
// reserved band was short by the inset (~34px) and the clamped scrubber dropped
// into the composer. The fix mirrors the legend PILL exactly: when the clamp
// engages on mobile we anchor the scrubber from the BOTTOM with a CSS
// `calc(env(safe-area-inset-bottom) + CLEARANCE)` (SCRUBBER_MOBILE_BOTTOM_CSS),
// so the safe-area inset is reserved by CSS on-device. The value covers the
// collapsed composer card + the sheet's safe-area lift.
export const SCRUBBER_MOBILE_SHEET_CLEARANCE_PX = 116;
// CSS bottom offset for the mobile clamp: the device safe-area inset PLUS the
// collapsed-sheet clearance, so the scrubber's bottom edge sits ABOVE the chat
// composer on notched + non-notched devices alike (env() is 0 on the latter).
// Mirrors LayerLegend.MOBILE_LEGEND_PILL_BOTTOM_CSS / Chat SHEET_BOTTOM_OFFSET.
export const SCRUBBER_MOBILE_BOTTOM_CSS = `calc(env(safe-area-inset-bottom) + ${SCRUBBER_MOBILE_SHEET_CLEARANCE_PX}px)`;
// Approx rendered scrubber height (7px*2 padding + ~26px control row) used to
// turn the "top" clamp into a bottom-edge clamp. A small over-estimate is safe.
const SCRUBBER_APPROX_HEIGHT_PX = 44;

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
  /**
   * TRUE projected AOI screen rectangle {left,top,right,bottom} in absolute
   * map-container coords (= viewport coords since the map fills the viewport).
   * When provided the scrubber pins bottom-center of the AOI bbox (item 3).
   * When absent it falls back to viewport bottom-center.
   */
  aoiRect?: ScreenRect | null;
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
  // intervalMs is retained in the props contract for backward compatibility but
  // is no longer used here — the AnimationController owns the advance interval.
  intervalMs: _intervalMs,
  aoiRect,
}: SequenceScrubberProps): JSX.Element | null {
  const n = frameLabels.length;
  const isMobile = useIsMobile();
  // Hold the latest active index in a ref so prev/next step from the current
  // frame even if the parent re-renders between presses.
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

  if (n === 0) return null;

  // PERSIST ON ZOOM-OUT (NATE 2026-06-22): the scrubber no longer hides below a
  // scale floor. The LayerLegend never vanishes on zoom-out, and NATE wants the
  // two consistent, so the scrubber stays mounted WHENEVER a sequential group is
  // active (the n===0 group-existence gate above is the only gate now). It just
  // shrinks with the bbox like the legend.

  const safeIndex = wrapIndex(activeIndex, n);

  // UNIFIED SCALE (NATE 2026-06-22): use the SAME function + params as the
  // LayerLegend (aoiScaleFactor) so the scrubber and legend "share scaling" and
  // follow the AOI bbox identically. The legend applies this to its inner chrome
  // (font / bar thickness) while its outer width = the clamped bbox width; the
  // scrubber mirrors that exactly below. Returns 1.0 (natural) for a null /
  // degenerate rect, so the AOI-less fallback renders at full size.
  const scale = aoiScaleFactor(aoiRect);

  // MATCH BBOX WIDTH (NATE 2026-06-22): when aoiRect is present the scrubber's
  // rendered width = the AOI bbox on-screen width (right - left), so it spans the
  // bbox exactly like the legend's keys (LayerLegend.defaultWidth = clamped
  // barWidth) instead of a fixed band that left padding on both sides. Clamped to
  // a tappable minimum so the buttons stay usable on a tiny zoomed-out box. With
  // no AOI rect, fall back to a fixed band (viewport-bottom fallback).
  const bboxWidth = aoiRect ? aoiRect.right - aoiRect.left : null;
  const targetWidth =
    typeof bboxWidth === "number" && Number.isFinite(bboxWidth) && bboxWidth > 0
      ? Math.max(SCRUBBER_MIN_WIDTH, bboxWidth)
      : SCRUBBER_FALLBACK_WIDTH;
  const widthStyle: React.CSSProperties = {
    // Explicit width tracks the bbox (no min/max band that adds side padding).
    width: targetWidth,
  };

  // Item 3: Snap the scrubber to the AOI bbox bottom-center when aoiRect is
  // available. The aoiRect coords are map-container-relative which equals
  // viewport coords (map container is position:fixed;inset:0 relative to the
  // app shell). When absent, fall back to viewport bottom-center.
  //
  // No uniform transform: scale() on the container anymore -> the OUTER width
  // tracks the bbox width directly (so the bar spans the bbox with no padding),
  // matching the legend; only the centering translate remains.
  // ITEM 6  -  on mobile, the highest screen-Y the scrubber's TOP may take so its
  // bottom edge still clears the collapsed chat sheet (composer). Portaled to
  // body with position:fixed, so the clamp is against the viewport height.
  // window may be undefined under SSR; guard and skip the clamp then.
  const viewportH =
    typeof window !== "undefined" && Number.isFinite(window.innerHeight)
      ? window.innerHeight
      : null;
  const mobileMaxTop =
    isMobile && viewportH != null
      ? viewportH -
        SCRUBBER_MOBILE_SHEET_CLEARANCE_PX -
        SCRUBBER_APPROX_HEIGHT_PX
      : null;

  let posStyle: React.CSSProperties;
  if (aoiRect) {
    const cx = (aoiRect.left + aoiRect.right) / 2;
    // ITEM 6  -  pin below the AOI bbox bottom edge, but on mobile keep the scrubber
    // ABOVE the chat sheet/composer. Desktop is unaffected (mobileMaxTop is null).
    const top = aoiRect.bottom + 12;
    if (mobileMaxTop != null && top > mobileMaxTop) {
      // BUG 3  -  the natural anchor would drop the scrubber into the composer band.
      // Anchor from the BOTTOM with the safe-area-inclusive clearance (CSS calc),
      // exactly like the legend pill, so the inset is reserved on-device (the old
      // `top = innerHeight - CLEARANCE` clamp silently omitted the safe-area inset
      // and overlapped the composer on notched phones). Still horizontally
      // centered on the AOI box.
      posStyle = {
        position: "fixed",
        left: cx,
        bottom: SCRUBBER_MOBILE_BOTTOM_CSS,
        transform: "translateX(-50%)",
        transformOrigin: "bottom center",
      };
    } else {
      posStyle = {
        position: "fixed",
        left: cx,
        top,
        transform: "translateX(-50%)",
        transformOrigin: "top center",
      };
    }
  } else {
    // No AOI on screen: anchor from the bottom. ITEM 6  -  on mobile lift the
    // anchor by the safe-area-inclusive sheet clearance so the fallback band sits
    // ABOVE the chat sheet/composer (BUG 3: the old fixed bottom:116 px omitted
    // the device safe-area inset and overlapped on notched phones). Desktop keeps
    // the original low bottom-center position.
    posStyle = {
      position: "fixed",
      bottom: isMobile ? SCRUBBER_MOBILE_BOTTOM_CSS : 24,
      left: "50%",
      transform: "translateX(-50%)",
      transformOrigin: "bottom center",
    };
  }

  // Inner-chrome scale shared with the legend: the counter font scales with the
  // AOI like the legend's labels (clamped via aoiScaleFactor).
  const counterFont = Math.round(11 * scale);

  // Portal to document.body so `fixed` positioning resolves against the
  // VIEWPORT, not the LayerPanel's transformed/filtered stacking context (the
  // panel is absolutely positioned + backdrop-filtered — same reason
  // ConfirmationDialog portals). This keeps the scrubber pinned bottom-center
  // of the AOI (or viewport fallback) while still being mounted from within
  // LayerPanel.
  //
  // Layout: `▶ < ——●—— > x/N` — play/pause, prev-arrow, slider/track,
  // next-arrow, then a compact `x/N` counter. The group label + frame label
  // text are omitted (shown in the LayerPanel group row). JOB WEB-ANIM (#157.3):
  // the play/pause button is back on the scrubber, wired to the controller.
  return createPortal(
    <div
      data-testid="grace2-sequence-scrubber"
      role="group"
      aria-label={`${label} sequence scrubber`}
      style={{
        ...posStyle,
        display: "flex",
        alignItems: "center",
        gap: 8,
        padding: "7px 12px",
        // ITEM 6 (NATE 2026-06-23): the x/N counter used to LEAK past the right
        // edge of the pill. The pill has an explicit width; with the buttons +
        // counter all flex-shrink:0 and the slider's old min-width (80), the
        // content min-size could exceed the pill width and push the counter
        // OUTSIDE the rounded bounds. box-sizing + overflow:hidden contains
        // every child WITHIN the pill (on mobile + desktop alike); the slider
        // (flex:1, min-width lowered below) yields so nothing overflows.
        boxSizing: "border-box",
        overflow: "hidden",
        // Joins the panel surface family (matches LayerLegend chrome).
        background: "rgba(17,18,23,0.82)",
        backdropFilter: "blur(6px)",
        WebkitBackdropFilter: "blur(6px)",
        border: "1px solid rgba(255,255,255,0.08)",
        borderRadius: 10,
        boxShadow: "0 2px 12px rgba(0,0,0,0.45)",
        fontFamily: "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
        color: "#e8e8ec",
        // MOBILE Z-ORDER (NATE 2026-06-22): on mobile sit UNDERNEATH the chat
        // bottom sheet (zIndex 32) so the scrubber never covers the composer; on
        // desktop the chat is a side panel, so keep the original higher z.
        zIndex: isMobile ? SCRUBBER_Z_MOBILE : SCRUBBER_Z_DESKTOP,
        // Explicit width tracks the AOI bbox on-screen width (no padding band),
        // so the bar spans the bbox like the legend.
        ...widthStyle,
      }}
    >
      {/* Play / pause toggle (JOB WEB-ANIM #157.3). Drives the shared
          AnimationController's `playing` state (App wires onPlayToggle). */}
      <ScrubButton
        testId="scrubber-play"
        label={playing ? "Pause sequence" : "Play sequence"}
        onClick={onPlayToggle}
        disabled={n <= 1}
      >
        {playing ? <IconPause size={14} /> : <IconPlay size={14} />}
      </ScrubButton>

      {/* Prev arrow */}
      <ScrubButton
        testId="scrubber-prev"
        label="Previous frame"
        onClick={() => stepBy(-1)}
        disabled={n <= 1}
      >
        <IconArrowLeft size={15} />
      </ScrubButton>

      {/* The slider — one detent per frame; dragging steps frames.
          Layout: the track sits between the two arrows. */}
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
          // ITEM 6: a smaller min-width lets the slider YIELD so the buttons +
          // x/N counter always fit WITHIN the pill (no overflow that pushed the
          // counter past the right edge). Still wide enough to grab on a tiny box.
          minWidth: 24,
          height: 16,
          accentColor: "#4aa3ff",
          cursor: "pointer",
        }}
      />

      {/* Next arrow */}
      <ScrubButton
        testId="scrubber-next"
        label="Next frame"
        onClick={() => stepBy(1)}
        disabled={n <= 1}
      >
        <IconArrowRight size={15} />
      </ScrubButton>

      {/* Compact x/N counter — the only text readout on the scrubber (item 4). */}
      <span
        data-testid="scrubber-frame-label"
        style={{
          // Shares the legend's scale story (aoiScaleFactor) so the readout
          // grows/shrinks with the bbox like the legend's labels.
          fontSize: counterFont,
          color: "#9aa1ab",
          fontVariantNumeric: "tabular-nums",
          flexShrink: 0,
          minWidth: 36,
          textAlign: "right",
        }}
      >
        {safeIndex + 1}/{n}
      </span>
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
