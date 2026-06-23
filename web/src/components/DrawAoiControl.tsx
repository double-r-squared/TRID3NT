// GRACE-2 web - DrawAoiControl (NATE map/loading-UX polish, item 4).
//
// A PERSISTENT (always-on) map control that arms the bbox rectangle-draw on
// demand. The drawn box STAGES as the analysis extent for the NEXT prompt -
// non-destructive, available ANYTIME (unlike the #170 AoiPickerCard, which only
// appears during case-create, or the agent-requested spatial-input surface).
// Nothing runs until the user actually prompts.
//
// Flow:
//   - Idle: a single round control button (the bbox/selection icon).
//   - Tap it -> ARM the drag-rectangle gesture (attachBboxDrag). The cursor goes
//     crosshair; the user drags a rectangle on the live map.
//   - On release -> the staged bbox is recorded on the aoiStageBus (read by Chat
//     on the next send) and a styled rectangle is painted on the map.
//   - A staged box surfaces a "Clear" affordance; clearing removes the staged
//     bbox + the on-map rectangle. Re-tapping the button re-arms a fresh draw
//     (replacing the staged box).
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
import type { Map as MapLibreMap } from "maplibre-gl";
import { IconBbox, IconClose } from "./icons";
import {
  attachBboxDrag,
  drawPickBbox,
  ensurePickLayers,
  clearPickLayers,
  type BBox,
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

const armedBtn: React.CSSProperties = {
  ...baseBtn,
  // Active/armed accent (bbox-pick blue) so the user sees draw mode is live.
  color: "#fff",
  background: "rgba(59,130,246,0.92)",
  borderColor: "rgba(59,130,246,0.92)",
};

const clearBtn: React.CSSProperties = {
  ...baseBtn,
  width: 30,
  height: 30,
};

export function DrawAoiControl({
  map,
  stateOverride,
  chatWidthPx,
  chatCollapsed,
  mobile,
}: DrawAoiControlProps): JSX.Element {
  // Subscribe to the staged-AOI bus (unless a test override is supplied).
  const { armed, bbox } = useBusState(stateOverride);

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
            ? "Drag a rectangle on the map to set the analysis extent"
            : "Draw the analysis extent for your next prompt"
        }
        onClick={armed ? onClear : onArm}
        style={armed ? armedBtn : baseBtn}
      >
        <IconBbox size={18} />
      </button>
      {hasStaged && !armed ? (
        <button
          type="button"
          data-testid="grace2-draw-aoi-clear"
          aria-label="Clear staged analysis extent"
          title="Clear the staged analysis extent"
          onClick={onClear}
          style={clearBtn}
        >
          <IconClose size={14} />
        </button>
      ) : null}
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
