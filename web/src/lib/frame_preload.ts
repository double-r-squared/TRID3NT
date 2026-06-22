// GRACE-2 web - animation frame-tile PRELOAD + hold-until-loaded swap.
//
// ROOT CAUSE (NATE map/loading-UX polish, item 2): stepping a sequential raster
// group (e.g. HRRR forecast hours) showed a BLACK-then-fill flash on each frame
// swap - especially on the FIRST play - because the incoming frame's layer was
// flipped to `visibility:visible` while its raster tiles were still loading, so
// the map painted an empty (black) layer for a beat before the tiles arrived.
//
// The fix has two halves, both implemented here against an injected map adapter
// (so this is pure + unit-testable, no MapLibre import):
//
//   1. PRELOAD / WARM - when a frame group becomes active, warm EVERY frame's
//      tiles by making each frame layer renderable-but-invisible (visible at
//      raster-opacity 0). A `visibility:none` raster layer does NOT fetch tiles
//      in MapLibre, so the only reliable warm is to keep it visible at zero
//      opacity. Warmed tiles mean a later swap is instant.
//
//   2. HOLD-UNTIL-LOADED swap - to show frame i we raise frame i to full opacity
//      FIRST (it is already warmed, so it paints immediately), then - only once
//      its source reports loaded - drop every OTHER frame back to opacity 0. The
//      previous frame stays painted underneath until the new one is ready, so
//      there is never a black gap even on a cold first play.
//
// The opacity dance (vs visibility toggling) is what removes the gap: all frame
// layers stay `visible` (so tiles load), and only `raster-opacity` flips, which
// has no tile-load cost. The single-visible-frame intent is preserved visually
// (exactly one frame at opacity 1; the rest at 0).

/** The minimal map surface the swapper needs - satisfied by a MapLibre map. */
export interface FrameMapAdapter {
  /** True if the layer currently exists on the style. */
  hasLayer(id: string): boolean;
  /** Set a layer's layout visibility ("visible" warms its tiles). */
  setVisibility(id: string, visible: boolean): void;
  /** Set a raster layer's opacity (0 = warmed-but-hidden, 1 = shown). */
  setOpacity(id: string, opacity: number): void;
  /** True once the named source's in-view tiles have loaded. */
  isSourceLoaded(id: string): boolean;
  /** Register a one-shot callback for the next source-data/idle settle. */
  onceSourceSettled(cb: () => void): void;
}

/**
 * Compute the warm set for a frame group: EVERY frame layer is warmed (kept
 * renderable so its tiles load) so any subsequent step is gap-free. Returns the
 * same array (no per-index filtering) because we warm all frames up front; split
 * out as a named function so the policy is explicit + unit-testable.
 */
export function framesToWarm(layerIds: string[]): string[] {
  return layerIds.filter((id) => typeof id === "string" && id.length > 0);
}

/**
 * Drive a gap-free swap to `visibleIndex` over `layerIds` against `map`.
 *
 *   - Warms every frame (visible) so tiles load. Frames that are neither the
 *     target nor the held previous frame are dimmed to opacity 0 immediately.
 *   - Raises the target frame to opacity 1 immediately (it is warmed, so it
 *     paints without a black beat).
 *   - HOLDS the previously-shown frame (`prevTarget`) at opacity 1 underneath
 *     until the target source is loaded, then dims it - so there is no black gap
 *     even on a cold first play. When the target is already loaded (warm cache /
 *     repeated step), the prev frame is dimmed synchronously.
 *
 * Stateless w.r.t. internal storage: the caller threads `prevTarget` (the id the
 * last call raised) back in, and this returns the new target to thread forward.
 *
 * Idempotent + race-tolerant: missing layers are skipped; an out-of-range index
 * is a no-op (returns the prior target unchanged so the hold state is intact).
 */
export function swapFrameWithHold(
  map: FrameMapAdapter,
  layerIds: string[],
  visibleIndex: number,
  prevTarget?: string | null,
): { warmed: string[]; target: string | null } {
  const warmed = framesToWarm(layerIds);

  const inRange =
    Number.isInteger(visibleIndex) &&
    visibleIndex >= 0 &&
    visibleIndex < layerIds.length;
  const target = inRange ? layerIds[visibleIndex] ?? null : null;
  const held = prevTarget ?? null;

  // 1. Warm every frame (visible -> tiles load). Dim every frame that is NOT the
  //    target and NOT the held previous frame straight away, so we never stack
  //    all frames at full opacity. The held frame stays at full opacity until the
  //    target loads (step 3); the target is raised in step 2.
  for (const id of warmed) {
    if (!map.hasLayer(id)) continue;
    map.setVisibility(id, true);
    if (id !== target && id !== held) {
      map.setOpacity(id, 0);
    }
  }

  if (target === null || !map.hasLayer(target)) {
    return { warmed, target: held };
  }

  // 2. Raise the target to full opacity NOW (warmed, so it paints immediately).
  map.setOpacity(target, 1);

  // No held frame, or the held frame IS the target (re-step to same frame):
  // nothing left to hold/dim.
  if (held === null || held === target) {
    return { warmed, target };
  }

  // 3. Dim the held previous frame - but HOLD it until the target's tiles are
  //    loaded so it stays underneath (no black gap). When already loaded, dim
  //    synchronously.
  const dimHeld = (): void => {
    if (map.hasLayer(held)) map.setOpacity(held, 0);
  };

  let loaded = false;
  try {
    loaded = map.isSourceLoaded(target);
  } catch {
    loaded = false;
  }
  if (loaded) {
    dimHeld();
  } else {
    map.onceSourceSettled(() => {
      dimHeld();
    });
  }

  return { warmed, target };
}
