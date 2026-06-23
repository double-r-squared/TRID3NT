// GRACE-2 web - DrawAoiControl (NATE map/loading-UX polish, item 4).
//
// A PERSISTENT (always-on) map control that arms the bbox rectangle-draw on
// demand. The drawn box STAGES as the analysis extent for the NEXT prompt -
// non-destructive, available ANYTIME (unlike the #170 AoiPickerCard, which only
// appears during case-create, or the agent-requested spatial-input surface).
// Nothing runs until the user actually prompts.
//
// Flow (NATE 2026-06-22, items 5 + 6 - the single-button two-path model):
//   - Idle: a single round control button (the bbox/selection icon). Tap -> ARM.
//   - ARMED (drawing): the SAME button's glyph becomes a RED X (cancel). The
//     cursor goes crosshair; the user drags a rectangle on the live map. Tapping
//     the red X cancels the in-progress draw (back to idle). There is NO separate
//     underneath-X anymore - the button itself is the cancel control.
//   - On release -> the staged bbox is recorded on the aoiStageBus and a styled
//     rectangle is painted on the map; the button reverts to the draw icon.
//   - SET (a box exists, not drawing): a "+" affordance appears at the BOTTOM-
//     CENTER, just under the box's bottom edge, to CONFIRM/finalize the AOI
//     (onConfirm). Re-tapping the draw button re-arms a fresh draw (replacing the
//     staged box), which doubles as the RESET path.
//
// NO-CLOBBER (NATE): the gesture is armed ONLY by an explicit tap on this
// control (never an ambient free-draw), and it draws onto the dedicated bbox-pick
// source (lib/bbox_draw) - it never touches a loaded data layer or an LLM-set
// AOI. The camera is not moved.
//
// This component owns ONLY its control chrome + the staged-rectangle plumbing; it
// reuses lib/bbox_draw (attachBboxDrag / drawPickBbox / ensurePickLayers /
// clearPickLayers) so the gesture is byte-identical to the AoiPickerCard's.

import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { Map as MapLibreMap } from "maplibre-gl";
import { IconBbox, IconClose, IconAdd } from "./icons";
import {
  attachBboxDrag,
  drawPickBbox,
  ensurePickLayers,
  clearPickLayers,
  projectBboxScreenRect,
  type BBox,
  type BboxScreenRect,
} from "../lib/bbox_draw";
import { aoiStageBus, type AoiStageBusState } from "../lib/aoi_stage_bus";

export interface DrawAoiControlProps {
  /** The live MapLibre instance (null while the map is mounting). */
  map: MapLibreMap | null;
  /**
   * Test seam: force the armed/bbox state instead of subscribing to the bus.
   * Undefined (default) subscribes to the shared aoiStageBus.
   */
  stateOverride?: AoiStageBusState;
  /**
   * NATE FIX 2 - the desktop chat panel's current dragged width (px). The
   * control rails to the LEFT of the chat panel's left edge and tracks it as
   * the panel resizes. Ignored on mobile / when collapsed (see below). Undefined
   * keeps the legacy fixed top-right placement (so existing callers / fixtures
   * that drive the control directly are unaffected).
   */
  chatWidthPx?: number;
  /**
   * NATE FIX 2 - whether the desktop chat panel is COLLAPSED (hidden, replaced
   * by the top-right chat-expand hamburger). When collapsed the control tucks
   * UNDER that hamburger instead of railing the (absent) panel's left edge.
   */
  chatCollapsed?: boolean;
  /**
   * NATE FIX 2 - mobile chrome. On mobile the chat is a BOTTOM sheet (no
   * top-right panel to clear), so the control keeps its plain top-right
   * placement. Default false (desktop).
   */
  mobile?: boolean;
  /**
   * NATE 2026-06-22 (item 6) - CONFIRM the staged AOI. The "+" affordance pinned
   * to the bottom-center of a SET (staged, not-being-drawn) box calls this with
   * the staged bbox to FINALIZE it as the analysis extent (App wires it to a
   * `zoom-to` map-command so the drawn box becomes the persistent AOI rectangle +
   * the camera fits it). Optional: when omitted (legacy callers / fixtures) the
   * "+" still renders and confirming just clears the staged pick overlay.
   */
  onConfirm?: (bbox: BBox) => void;
}

// FIX 2 geometry (mirrors the App.tsx chat panel + hamburger constants):
//   - desktop chat panel: top:16, right:16, width: min(chatWidthPx, 92vw).
//   - chat-expand hamburger (collapsed): top:12, right:12, 40x40.
// The control button is 38px wide. We rail it just LEFT of the panel's left
// edge, at the panel's top; when collapsed, UNDER the hamburger.
const CHAT_PANEL_RIGHT_PX = 16; // desktopChatContainerStyle.right
const CHAT_PANEL_TOP_PX = 16; // desktopChatContainerStyle.top
const CHAT_HAMBURGER_RIGHT_PX = 12; // App hamburgerBtnStyle right (chat)
const CHAT_HAMBURGER_TOP_PX = 12; // App hamburgerBtnStyle top
const CHAT_HAMBURGER_SIZE_PX = 40; // App hamburgerBtnStyle width/height
const CONTROL_GAP_PX = 8; // gap between the control and the panel/hamburger

/**
 * FIX 2 (pure, exported for tests) - the control wrapper's absolute position.
 * Three placements:
 *   - mobile: plain top-right (the chat is a bottom sheet; nothing to clear).
 *   - desktop + collapsed: UNDER the top-right chat-expand hamburger, aligned to
 *     its right edge.
 *   - desktop + expanded: at the chat panel's TOP, railed to the LEFT of the
 *     panel's left edge (tracks chatWidthPx as the panel resizes). The panel's
 *     left edge is `CHAT_PANEL_RIGHT_PX + width` from the viewport's right edge,
 *     so the control sits one gap further right-anchored out: `... + gap`.
 */
export function drawAoiControlPosition(opts: {
  chatWidthPx?: number;
  chatCollapsed?: boolean;
  mobile?: boolean;
}): { top: number; right: number } {
  const { chatWidthPx, chatCollapsed, mobile } = opts;
  if (mobile) {
    return { top: CHAT_HAMBURGER_TOP_PX, right: CHAT_HAMBURGER_RIGHT_PX };
  }
  if (chatCollapsed || chatWidthPx === undefined) {
    // Tuck under the chat-expand hamburger (collapsed), aligned to its right.
    return {
      top: CHAT_HAMBURGER_TOP_PX + CHAT_HAMBURGER_SIZE_PX + CONTROL_GAP_PX,
      right: CHAT_HAMBURGER_RIGHT_PX,
    };
  }
  // Expanded: rail to the LEFT of the panel's left edge, at the panel's top.
  return {
    top: CHAT_PANEL_TOP_PX,
    right: CHAT_PANEL_RIGHT_PX + chatWidthPx + CONTROL_GAP_PX,
  };
}

function controlWrapStyle(pos: {
  top: number;
  right: number;
}): React.CSSProperties {
  return {
    position: "absolute",
    top: pos.top,
    right: pos.right,
    zIndex: 20,
    display: "flex",
    flexDirection: "column",
    alignItems: "flex-end",
    gap: 8,
    pointerEvents: "none", // the wrapper is transparent; buttons re-enable.
  };
}

const baseBtn: React.CSSProperties = {
  pointerEvents: "auto",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  width: 38,
  height: 38,
  padding: 0,
  background: "rgba(17,18,23,0.82)",
  backdropFilter: "blur(6px)",
  WebkitBackdropFilter: "blur(6px)",
  border: "1px solid rgba(255,255,255,0.10)",
  borderRadius: 9,
  boxShadow: "0 2px 12px rgba(0,0,0,0.45)",
  color: "#cfd4db",
  cursor: "pointer",
  transition: "color 120ms ease, background 120ms ease, border-color 120ms ease",
};

// NATE 2026-06-22 (item 5): while ARMED (drawing) the button's own glyph turns
// into a RED X (cancel). Red fill so it reads unmistakably as the "stop / cancel
// the draw" control - the user taps it to abort the in-progress rectangle.
const armedBtn: React.CSSProperties = {
  ...baseBtn,
  color: "#fff",
  background: "rgba(220,38,38,0.92)", // red (cancel).
  borderColor: "rgba(220,38,38,0.92)",
};

// NATE 2026-06-22 (item 6): the "+" CONFIRM control pinned to the box bottom-
// center. A small green-accented icon button (styled like the X but for the
// opposite action - confirm vs cancel). pointer-events re-enabled (it portals
// out of the wrapper, so it sets its own).
const confirmBtn: React.CSSProperties = {
  ...baseBtn,
  pointerEvents: "auto",
  width: 32,
  height: 32,
  color: "#fff",
  background: "rgba(34,197,94,0.95)", // green (confirm).
  borderColor: "rgba(34,197,94,0.95)",
};

// Gap (px) between the box's bottom edge and the "+" confirm control, mirroring
// the AoiPickerCard DrawControls anchor.
const CONFIRM_CONTROL_GAP_PX = 10;

export function DrawAoiControl({
  map,
  stateOverride,
  chatWidthPx,
  chatCollapsed,
  mobile,
  onConfirm,
}: DrawAoiControlProps): JSX.Element {
  // Subscribe to the staged-AOI bus (unless a test override is supplied).
  const { armed, bbox } = useBusState(stateOverride);

  // NATE 2026-06-22 (item 6): the staged box's projected on-screen rect, so the
  // "+" confirm control can pin to its BOTTOM-CENTER and follow the camera (same
  // pattern as the legend / scrubber / AoiPickerCard DrawControls). Only tracked
  // while a box is SET (staged) and NOT being drawn (armed) - the "+" must NOT
  // appear mid-draw, only once a box exists.
  const [confirmRect, setConfirmRect] = useState<BboxScreenRect | null>(null);

  // FIX 2 - track the chat panel: rail to the left of its left edge (expanded),
  // under the chat-expand hamburger (collapsed), or plain top-right (mobile).
  const wrapStyle = controlWrapStyle(
    drawAoiControlPosition({ chatWidthPx, chatCollapsed, mobile }),
  );

  // Keep the map in a ref so the draw effect reads the current instance without
  // re-arming the gesture on every render.
  const mapRef = useRef<MapLibreMap | null>(map);
  mapRef.current = map;

  // --- DRAW mode: arm the drag-rectangle gesture only while `armed`. -------- //
  // Mirrors AoiPickerCard's gesture exactly (NO-CLOBBER: armed only by the tap).
  useEffect(() => {
    const m = map;
    if (!m || !armed) return undefined;
    ensurePickLayers(m);
    // Re-paint any already-staged rectangle when re-entering draw mode.
    if (bbox) drawPickBbox(m, bbox);
    const detach = attachBboxDrag(m, {
      onProgress: (b) => drawPickBbox(m, b),
      onComplete: (b: BBox) => {
        // Stage the completed bbox (disarms via the bus) + paint it.
        drawPickBbox(m, b);
        aoiStageBus.setBbox(b);
      },
    });
    return () => {
      detach();
    };
    // `bbox` intentionally omitted: re-arming on every staged-box change would
    // detach the in-flight gesture. The initial repaint above covers re-entry.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, armed]);

  // --- Keep the staged rectangle painted while a bbox is staged but not armed.
  // (When armed, the draw effect above handles painting.) When neither armed nor
  // a staged bbox exists, clear the pick layers so the map is clean.
  useEffect(() => {
    const m = map;
    if (!m) return;
    if (bbox && !armed) {
      ensurePickLayers(m);
      drawPickBbox(m, bbox);
    } else if (!bbox && !armed) {
      clearPickLayers(m);
    }
  }, [map, armed, bbox]);

  // NATE 2026-06-22 (item 6): track the staged box's bottom-center anchor for the
  // "+" confirm control. Re-projected on every camera move so the "+" stays glued
  // to the box. Active ONLY when a box is staged AND not being drawn.
  useEffect(() => {
    const m = map;
    if (!m || armed || !bbox) {
      setConfirmRect(null);
      return undefined;
    }
    let rafId: number | null = null;
    let disposed = false;
    const recompute = (): void => {
      rafId = null;
      if (disposed) return;
      setConfirmRect(projectBboxScreenRect(m, bbox));
    };
    const schedule = (): void => {
      if (rafId != null) return;
      if (typeof requestAnimationFrame === "function") {
        rafId = requestAnimationFrame(recompute);
      } else {
        recompute();
      }
    };
    schedule();
    try {
      m.on("move", schedule);
      m.on("zoom", schedule);
      m.on("render", schedule);
    } catch {
      /* map mid-teardown - the initial projection above still anchors it */
    }
    return () => {
      disposed = true;
      if (rafId != null && typeof cancelAnimationFrame === "function") {
        cancelAnimationFrame(rafId);
      }
      try {
        m.off("move", schedule);
        m.off("zoom", schedule);
        m.off("render", schedule);
      } catch {
        /* map torn down */
      }
    };
  }, [map, armed, bbox]);

  const onArm = useCallback(() => {
    // Re-arming with a staged box replaces it: clear the staged box first so the
    // new draw starts fresh (the gesture repaints as the user drags).
    aoiStageBus.setArmed(true);
  }, []);

  const onClear = useCallback(() => {
    const m = mapRef.current;
    if (m) {
      try {
        clearPickLayers(m);
      } catch {
        /* map torn down */
      }
    }
    aoiStageBus.clear();
  }, []);

  // NATE 2026-06-22 (item 6): CONFIRM the staged AOI. Hand the staged bbox to the
  // caller (App wires it to a `zoom-to` map-command so the drawn box becomes the
  // persistent analysis-extent rectangle + the camera fits it), then clear the
  // staged pick overlay (the box is now finalized as the AOI, not a pending draw).
  const onConfirmClick = useCallback(() => {
    const b = aoiStageBus.getState().bbox ?? bbox;
    if (b && onConfirm) onConfirm(b);
    onClear();
  }, [bbox, onConfirm, onClear]);

  const hasStaged = bbox !== null;

  return (
    <div data-testid="grace2-draw-aoi-control" style={wrapStyle}>
      <button
        type="button"
        data-testid="grace2-draw-aoi-button"
        aria-label={armed ? "Cancel AOI draw" : "Draw analysis extent"}
        aria-pressed={armed}
        title={
          armed
            ? "Cancel the in-progress AOI draw"
            : "Draw the analysis extent for your next prompt"
        }
        onClick={armed ? onClear : onArm}
        style={armed ? armedBtn : baseBtn}
      >
        {/* NATE 2026-06-22 (item 5): the button's OWN glyph toggles - the draw/
            bbox icon when idle, a RED X (cancel) while drawing. No separate
            underneath-X. */}
        {armed ? <IconClose size={18} /> : <IconBbox size={18} />}
      </button>

      {/* NATE 2026-06-22 (item 6): the "+" CONFIRM control. Appears ONLY once a
          box is SET (staged, not being drawn). When the box projects on-screen
          (confirmRect) the "+" pins to the BOTTOM-CENTER just under the box's
          bottom edge and tracks the camera; if the box is off-screen / not yet
          projected it falls back to viewport bottom-center (mirrors the
          AoiPickerCard DrawControls null-rect fallback) so the confirm is never
          unreachable. Portaled to document.body so its fixed coords resolve
          against the viewport. Clicking it finalizes the AOI. */}
      {hasStaged && !armed
        ? createPortal(
            <button
              type="button"
              data-testid="grace2-draw-aoi-confirm"
              aria-label="Confirm analysis extent"
              title="Confirm this analysis extent"
              onClick={onConfirmClick}
              style={{
                ...confirmBtn,
                position: "fixed",
                ...(confirmRect
                  ? {
                      left: (confirmRect.left + confirmRect.right) / 2,
                      top: confirmRect.bottom + CONFIRM_CONTROL_GAP_PX,
                    }
                  : { left: "50%", bottom: 96 }),
                transform: "translateX(-50%)",
                zIndex: 21,
              }}
            >
              <IconAdd size={16} />
            </button>,
            document.body,
            "grace2-draw-aoi-confirm",
          )
        : null}
    </div>
  );
}

// --- bus subscription hook (with a test override) ------------------------- //

function useBusState(override?: AoiStageBusState): AoiStageBusState {
  const [state, setState] = useState<AoiStageBusState>(
    override ?? aoiStageBus.getState(),
  );
  useEffect(() => {
    if (override !== undefined) {
      setState(override);
      return undefined;
    }
    return aoiStageBus.subscribe(setState);
  }, [override]);
  return override ?? state;
}
