// GRACE-2 web - bbox progress-animation STATE MACHINE + settings persistence.
//
// NATE map/loading-UX polish (2026-06-22), item 1. A loading animation anchored
// to the projected AOI bbox screen rectangle communicates "the map is working":
//
//   - FIRST layer fetch (geolocated, bbox present, NO layers yet)  -> a FILL-GRID
//     SHIMMER inside the bbox. OK to cover the box (nothing is there yet).
//   - SUBSEQUENT layer loads (>=1 layer already on the map) -> a SCAN-BORDER (a
//     line sweeping the bbox EDGE) so it never covers the existing layers.
//   - CASE LOADING (bbox known, layers incoming) -> the loading animation (scan
//     when layers exist, fill when empty), same as the two rules above.
//   - CONNECTING (WS connecting / reconnecting) -> a SCAN-BORDER, ALWAYS ON,
//     exempt from the user toggle (it is a transport-health cue, not chrome).
//   - LONG-RUNNING SIM -> a PURPLE scan-border (purple = the sim pipeline-card
//     color) so a multi-minute solve reads as in-progress on the map.
//
// This module is PURE (no React / MapLibre / DOM beyond the localStorage seam):
// `resolveBboxProgress` maps the live signals -> a render descriptor, and the
// settings helpers read/write the persisted enable flag. Everything is trivially
// unit-testable.

/** The visual mode the overlay paints, or "none" when nothing animates. */
export type BboxProgressMode = "none" | "fill" | "scan";

/** A scan-border color family. "blue" = normal loading, "purple" = a sim. */
export type BboxProgressTone = "blue" | "purple";

/** The live signals the overlay state machine reads (all owned by App). */
export interface BboxProgressSignals {
  /** Whether a geolocated AOI bbox is projected on screen (the anchor). */
  hasBbox: boolean;
  /** Count of layers currently on the map for the active Case. */
  layerCount: number;
  /** True while a Case's layers are loading (App's `layersLoading`). */
  layersLoading: boolean;
  /** True while the WS is connecting / reconnecting (transport not healthy). */
  connecting: boolean;
  /** True while a long-running simulation pipeline is in progress. */
  simRunning: boolean;
  /** The user's persisted enable flag for the loading animations. */
  animationsEnabled: boolean;
  /**
   * LANE E (3D): true when MapLibre 3D terrain is enabled. In 3D the camera is
   * pitched/rotated so the AXIS-ALIGNED 2D DOM overlay no longer traces the
   * tilted AOI box; the in-map line-layer pulse-glow (terrain_3d.ts) takes over
   * instead. So 3D suppresses the 2D overlay (mode "none") for the loading
   * states. Optional (defaults false) so existing callers / tests are unchanged.
   */
  terrain3d?: boolean;
  /**
   * LANE B #4 (no-replay): true when the active Case + bbox are UNCHANGED since
   * the last paint AND layers are already present, i.e. a re-enter / same-bbox
   * switch where nothing genuinely new is being fetched. When set, the
   * subsequent-load loading visual (fill / scan) is suppressed so the loading
   * shimmer does NOT replay over already-rendered layers. The CONNECTING cue and
   * a running SIM still show (they are real in-progress signals, not replays).
   * Optional (defaults false) so existing callers / tests are unchanged.
   */
  suppressLoadingReplay?: boolean;
}

/** The render descriptor `resolveBboxProgress` returns. */
export interface BboxProgressState {
  /** Which animation to paint ("none" hides the overlay entirely). */
  mode: BboxProgressMode;
  /** Scan-border tone (only meaningful when mode === "scan"). */
  tone: BboxProgressTone;
  /**
   * True when this state is exempt from the user enable toggle (the CONNECTING
   * scan border is always on). Surfaced so the caller / tests can assert it.
   */
  toggleExempt: boolean;
}

const NONE: BboxProgressState = { mode: "none", tone: "blue", toggleExempt: false };

/**
 * Resolve the live signals into a single render descriptor. Priority order
 * (highest first), matching NATE's spec:
 *
 *   1. No bbox anchor                -> nothing to anchor to -> none.
 *   2. CONNECTING (WS not healthy)   -> SCAN, blue, ALWAYS ON (toggle-exempt).
 *   3. animations disabled by user   -> none (only the connecting cue survives,
 *                                       and it was already handled above).
 *   4. LONG-RUNNING SIM              -> SCAN, PURPLE (sim-in-progress color).
 *   5. LOADING + layers already exist-> SCAN, blue (must not cover layers).
 *   6. LOADING + no layers yet       -> FILL shimmer (first fetch; ok to cover).
 *   7. otherwise                     -> none.
 *
 * The connecting cue is checked BEFORE the enable toggle so it is never
 * suppressed (it is a transport-health signal, not decorative chrome). Every
 * other state is gated on `animationsEnabled`.
 */
export function resolveBboxProgress(s: BboxProgressSignals): BboxProgressState {
  // 1. Nothing to anchor against.
  if (!s.hasBbox) return NONE;

  // LANE E (3D): in 3D terrain mode the 2D DOM overlay can only be axis-aligned
  //    so it floats off the tilted/pitched AOI box. Suppress the 2D overlay for
  //    the CONNECTING / SIM / LOADING states; the in-map line-layer pulse-glow
  //    (terrain_3d.ts) carries the "working" cue instead. Checked first so 3D
  //    never paints a misaligned scan, including the toggle-exempt connecting
  //    one. (When 3D is off this branch is a no-op and 2D behaves as before.)
  if (s.terrain3d) return NONE;

  // 2. Connecting / reconnecting: a blue scan border, ALWAYS ON. Highest
  //    priority after the anchor gate so a transport drop is always visible.
  if (s.connecting) {
    return { mode: "scan", tone: "blue", toggleExempt: true };
  }

  // 3. The user turned the loading animations off: every remaining state below
  //    is decorative loading chrome, so suppress them. (The connecting border
  //    above already returned, so it stays on regardless.)
  if (!s.animationsEnabled) return NONE;

  // 4. A long-running sim takes the PURPLE scan border (matches the
  //    sim-in-progress pipeline-card color) so a multi-minute solve reads as
  //    in-progress on the map, even when no new layers are loading.
  if (s.simRunning) {
    return { mode: "scan", tone: "purple", toggleExempt: false };
  }

  // 5. Loading a Case / a layer. LANE C: NATE wants the GRID-FILL shimmer as the
  //    desktop loading visual (not the sweeping scan line), so emit FILL for the
  //    loading state regardless of layerCount. The fill grid lattice is faint /
  //    translucent so it does not hide existing layers. LANE B #4: but when the
  //    active Case + bbox are unchanged and layers are already present (a
  //    re-enter / same-bbox switch, no genuine new fetch), suppress the replay so
  //    the shimmer does NOT re-arm over already-rendered layers.
  if (s.layersLoading) {
    if (s.suppressLoadingReplay && s.layerCount > 0) return NONE;
    return { mode: "fill", tone: "blue", toggleExempt: false };
  }

  // 6. Idle.
  return NONE;
}

// --- Settings persistence (localStorage, like the other web settings) ----- //

/**
 * localStorage key for the bbox loading-animation enable flag. DEFAULT ON: an
 * absent / unparseable value reads as enabled, so a fresh user sees the
 * animations (NATE's default). Mirrors the LS_THEME / chat-opacity persistence
 * pattern (a single per-user key, read-with-default, write-through).
 */
export const LS_BBOX_ANIM = "grace2.bboxLoadingAnimations";

/** Read the persisted enable flag. Default ON (absent / bad value -> true). */
export function readBboxAnimationsEnabled(): boolean {
  try {
    const v = localStorage.getItem(LS_BBOX_ANIM);
    // Only the explicit string "false" disables it; anything else (incl. null)
    // is the default-ON behavior.
    return v !== "false";
  } catch {
    return true;
  }
}

/** Persist the enable flag. */
export function writeBboxAnimationsEnabled(enabled: boolean): void {
  try {
    localStorage.setItem(LS_BBOX_ANIM, enabled ? "true" : "false");
  } catch {
    /* storage unavailable (private mode / SSR) - non-fatal */
  }
}

/**
 * Derive the long-running-sim signal from a session-state `current_pipeline`
 * snapshot. A pipeline is "running" iff it exists, has NOT terminated
 * (`final_state` is null/undefined), and carries at least one step still in the
 * `running` state. Tolerant of loose/undefined shapes (the contract types the
 * field loosely on some envelopes) - any parse miss reads as not-running.
 */
export function isPipelineRunning(currentPipeline: unknown): boolean {
  if (!currentPipeline || typeof currentPipeline !== "object") return false;
  const p = currentPipeline as {
    final_state?: unknown;
    steps?: unknown;
  };
  // A terminated pipeline (complete / failed / cancelled) is never "running".
  if (p.final_state) return false;
  if (!Array.isArray(p.steps)) return false;
  return p.steps.some(
    (st) =>
      st != null &&
      typeof st === "object" &&
      (st as { state?: unknown }).state === "running",
  );
}
