// GRACE-2 web — legend snap geometry (draggable + resizable AOI-snapping keys).
//
// NATE's overlay-layout spec: the gradient legend "keys" are draggable, and on
// release they AUTO-SNAP to the nearest side of the AOI bounding box. Multiple
// keys arrange COUNTER-CLOCKWISE by stack order — 1st key bottom, 2nd right,
// 3rd top, 4th left — and stack (offset along the side) when more than one key
// lands on the same side so they never overlap.
//
// This module is PURE geometry only (Invariant 1: the client renders, it never
// computes geography). Every input is already in screen/pixel space — the AOI
// rectangle is the bbox already projected by Map.tsx; the key sizes are pixel
// box sizes. Nothing here touches MapLibre, React, or the DOM, so it is
// trivially and exhaustively unit-testable.

/** A screen-space rectangle in CSS pixels (origin = map container top-left). */
export interface ScreenRect {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

/** The four AOI sides, in COUNTER-CLOCKWISE stack order (NATE's spec). */
export type AoiSide = "bottom" | "right" | "top" | "left";

/**
 * CCW side order by key stack index:
 *   index 0 -> bottom, 1 -> right, 2 -> top, 3 -> left, then it wraps
 *   (index 4 -> bottom again, stacking a second key on the bottom side, etc.).
 */
export const CCW_SIDES: readonly AoiSide[] = ["bottom", "right", "top", "left"];

/** Pixel gap kept between a key and the AOI edge it snaps against. */
export const SIDE_GAP_PX = 10;

/** Pixel gap between two keys stacked on the same side. */
export const STACK_GAP_PX = 8;

/** A key's pixel footprint (the legend card box) used for snap math. */
export interface KeySize {
  width: number;
  height: number;
}

/** The result of snapping: a key's top-left screen position + the side it took. */
export interface SnapResult {
  left: number;
  top: number;
  side: AoiSide;
}

/**
 * Returns the CCW side for a key by its stack index (wrapping every 4).
 * index 0 -> bottom, 1 -> right, 2 -> top, 3 -> left, 4 -> bottom, ...
 */
export function sideForIndex(index: number): AoiSide {
  const n = CCW_SIDES.length;
  const i = ((index % n) + n) % n; // safe modulo, handles negatives too
  // `i` is always in-range so the lookup is defined; the fallback satisfies
  // noUncheckedIndexedAccess and can never actually be hit.
  return CCW_SIDES[i] ?? "bottom";
}

/**
 * How many keys come BEFORE `index` that share the same side (used to stack).
 * Because sides repeat every 4, the keys on a side are index, index+4, ... —
 * so the stack position is floor(index / 4).
 */
export function stackPositionForIndex(index: number): number {
  return Math.floor(Math.max(0, index) / CCW_SIDES.length);
}

/**
 * Computes the snapped top-left position for a single key.
 *
 * The key is laid against `side` of the AOI rect, centered along that side,
 * then offset by `stackPos` so multiple keys on the same side march away from
 * the AOI center (stacking outward) without overlapping. The cross-axis offset
 * (e.g. how far below the bottom edge) is `SIDE_GAP_PX` plus the cumulative
 * height/width of the keys already stacked on that side.
 *
 * `priorExtentOnSide` is the total pixel extent (height for top/bottom sides,
 * width for left/right sides) already consumed by keys stacked closer to the
 * AOI on this same side — the caller accumulates it so keys of differing sizes
 * still never overlap.
 */
export function snapKeyToSide(
  aoi: ScreenRect,
  side: AoiSide,
  size: KeySize,
  stackPos: number,
  priorExtentOnSide: number,
): SnapResult {
  const cx = (aoi.left + aoi.right) / 2;
  const cy = (aoi.top + aoi.bottom) / 2;

  // Cross-axis distance from the AOI edge: a base gap, plus everything already
  // stacked on this side, plus a per-stack gap for each prior key.
  const crossOffset = SIDE_GAP_PX + priorExtentOnSide + stackPos * STACK_GAP_PX;

  switch (side) {
    case "bottom": {
      const left = cx - size.width / 2;
      const top = aoi.bottom + crossOffset;
      return { left, top, side };
    }
    case "top": {
      const left = cx - size.width / 2;
      const top = aoi.top - crossOffset - size.height;
      return { left, top, side };
    }
    case "right": {
      const left = aoi.right + crossOffset;
      const top = cy - size.height / 2;
      return { left, top, side };
    }
    case "left": {
      const left = aoi.left - crossOffset - size.width;
      const top = cy - size.height / 2;
      return { left, top, side };
    }
    default: {
      // Exhaustive — unreachable, but keep TS happy and degrade gracefully.
      return { left: cx, top: cy, side: "bottom" };
    }
  }
}

/**
 * Lays out a full ordered list of keys against the AOI rect by the CCW rule.
 * `order` is the key stack order (index 0 = first key = bottom side). Keys are
 * assigned sides by `sideForIndex`, stacked by `stackPositionForIndex`, and
 * the cross-axis extent already used on a side is accumulated so heterogeneous
 * key sizes never overlap.
 *
 * `sideStartOffset` (ITEM 5, NATE 2026-06-22) shifts the CCW side assignment by
 * a whole-number of sides. The default 0 keeps the canonical order (key 0 ->
 * bottom). Passing 1 starts the first key on the RIGHT side instead (key 0 ->
 * right, 1 -> top, ...): used to vacate the bottom-center band for the sequence
 * scrubber and rail the first key VERTICALLY down the right edge of the bbox.
 * The stack position is unchanged (it still groups every 4th key per side), so
 * heterogeneous sizes never overlap regardless of the offset.
 *
 * Returns one SnapResult per input key, in the same order.
 */
export function layoutKeysCcw(
  aoi: ScreenRect,
  sizes: KeySize[],
  sideStartOffset = 0,
): SnapResult[] {
  // Track cumulative cross-axis extent per side so each new key on a side sits
  // beyond the ones already there.
  const usedExtent: Record<AoiSide, number> = {
    bottom: 0,
    right: 0,
    top: 0,
    left: 0,
  };
  return sizes.map((size, index) => {
    const side = sideForIndex(index + sideStartOffset);
    const stackPos = stackPositionForIndex(index);
    const prior = usedExtent[side];
    const result = snapKeyToSide(aoi, side, size, stackPos, prior);
    // Bottom/top consume vertical extent; left/right consume horizontal.
    const consumed = side === "bottom" || side === "top" ? size.height : size.width;
    usedExtent[side] = prior + consumed;
    return result;
  });
}

/**
 * Picks the AOI side nearest to a free-dragged key's CENTER point. Used on drag
 * release to choose which side the key snaps back to. Distance is the
 * perpendicular gap from the point to each edge line, clamped so a point well
 * inside or outside still maps to the closest edge.
 */
export function nearestSide(aoi: ScreenRect, point: { x: number; y: number }): AoiSide {
  const dLeft = Math.abs(point.x - aoi.left);
  const dRight = Math.abs(point.x - aoi.right);
  const dTop = Math.abs(point.y - aoi.top);
  const dBottom = Math.abs(point.y - aoi.bottom);
  const min = Math.min(dLeft, dRight, dTop, dBottom);
  // Tie-break order matches CCW priority: bottom, right, top, left.
  if (min === dBottom) return "bottom";
  if (min === dRight) return "right";
  if (min === dTop) return "top";
  return "left";
}

/**
 * Item d (NATE 2026-06-20) — derive a SCALE FACTOR for the AOI-anchored overlays
 * (legend keys + scrubber) from the AOI bbox's ON-SCREEN size, so that when the
 * map is zoomed out and the bbox is tiny the overlays shrink with it (instead of
 * dwarfing the box in fixed screen-px), and when zoomed in they grow — both
 * clamped so they never become unusably tiny or absurdly huge.
 *
 * The factor is the AOI's smaller on-screen dimension (min of width/height — the
 * limiting axis the overlay must not overwhelm) divided by a REFERENCE size at
 * which the overlays render at their natural 1.0 scale. It is then clamped to
 * [min, max].
 *
 * Pure pixel math (Invariant 1). Returns 1.0 (the natural scale) when there is
 * no rect, so an AOI-less fallback renders at full size.
 */
export interface AoiScaleOptions {
  /** On-screen px at which the overlay renders at scale 1.0. Default 360. */
  referencePx?: number;
  /** Smallest allowed scale (never unusably tiny). Default 0.6. */
  min?: number;
  /** Largest allowed scale (never absurdly huge). Default 1.6. */
  max?: number;
}

export const DEFAULT_AOI_SCALE_REFERENCE_PX = 360;
export const DEFAULT_AOI_SCALE_MIN = 0.6;
export const DEFAULT_AOI_SCALE_MAX = 1.6;

export function aoiScaleFactor(
  rect: ScreenRect | null | undefined,
  opts: AoiScaleOptions = {},
): number {
  const reference = opts.referencePx ?? DEFAULT_AOI_SCALE_REFERENCE_PX;
  const min = opts.min ?? DEFAULT_AOI_SCALE_MIN;
  const max = opts.max ?? DEFAULT_AOI_SCALE_MAX;
  if (!rect) return 1;
  const w = Math.abs(rect.right - rect.left);
  const h = Math.abs(rect.bottom - rect.top);
  // The limiting axis: the overlay must not overwhelm the SMALLER on-screen
  // extent of the AOI box. Guard against a degenerate (zero-area) rect.
  const limiting = Math.min(w, h);
  if (!Number.isFinite(limiting) || limiting <= 0 || reference <= 0) return 1;
  const raw = limiting / reference;
  return Math.max(min, Math.min(raw, max));
}

// UNIFIED SCRUBBER+LEGEND SCALING (NATE 2026-06-22): the scrubber and the
// LayerLegend now SHARE one scaling story - both consume `aoiScaleFactor` above
// (the legend already did) and both track the AOI bbox on-screen WIDTH for their
// rendered size, and NEITHER hides on zoom-out. The old scrubber-only helpers
// (scrubberScaleForAoi + scrubberVisibleForAoi, with a separate reference/clamp
// band and a hide-below-threshold) are RETIRED: their separate scale band made
// the scrubber narrower/wider than the bbox, and the hide-below floor made it
// VANISH on zoom-out while the legend persisted - the exact inconsistency NATE
// asked to remove. The scrubber now matches the bbox width directly and uses
// aoiScaleFactor for its inner chrome, so the two are consistent by construction.

/**
 * FALLBACK ESTIMATOR — only used when the TRUE projected AOI rect is unavailable.
 *
 * Reconstructs an *approximate* AOI ScreenRect from `anchor` (the bbox
 * BOTTOM-edge midpoint {left, top}) and `barWidth` (the bbox on-screen EAST-WEST
 * extent in px). The bottom edge is known exactly from those two; the AOI HEIGHT
 * is NOT carried by anchor+width, so the top edge is ESTIMATED by assuming a
 * square-ish box (height = width) UNLESS an explicit height is supplied. Because
 * the height is a guess, top/left snapping off this rect is only approximate for
 * non-square or skewed AOIs.
 *
 * Map.tsx now threads the real {left,top,right,bottom} rect (computeBboxScreenRect
 * — min/max over all four projected corners) straight into LayerLegend, which
 * snaps off THAT when present. This estimator is retained ONLY as the fallback
 * for when the true rect is absent (off-screen / not yet projected) and for unit
 * tests that exercise the anchor+width reconstruction path.
 *
 * Returns null when there is no anchor or no positive width (no AOI on screen),
 * so the caller can fall back to the static bottom-center placement.
 */
export function rectFromAnchorAndWidth(
  anchor: { left: number; top: number } | null | undefined,
  barWidth: number | null | undefined,
  estimatedHeight?: number | null,
): ScreenRect | null {
  if (!anchor) return null;
  const w =
    typeof barWidth === "number" && Number.isFinite(barWidth) && barWidth > 0
      ? barWidth
      : null;
  if (w == null) return null;
  const half = w / 2;
  const h =
    typeof estimatedHeight === "number" &&
    Number.isFinite(estimatedHeight) &&
    estimatedHeight > 0
      ? estimatedHeight
      : w; // square-ish fallback when the AOI height isn't provided.
  return {
    left: anchor.left - half,
    right: anchor.left + half,
    bottom: anchor.top,
    top: anchor.top - h,
  };
}
