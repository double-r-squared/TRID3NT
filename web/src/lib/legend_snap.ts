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
 * Returns one SnapResult per input key, in the same order.
 */
export function layoutKeysCcw(aoi: ScreenRect, sizes: KeySize[]): SnapResult[] {
  // Track cumulative cross-axis extent per side so each new key on a side sits
  // beyond the ones already there.
  const usedExtent: Record<AoiSide, number> = {
    bottom: 0,
    right: 0,
    top: 0,
    left: 0,
  };
  return sizes.map((size, index) => {
    const side = sideForIndex(index);
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

/**
 * Scrubber UNIFORM SCALE = AOI bbox on-screen px width / reference (NATE 2026-06-21).
 *
 * NATE's refinement (supersedes the old width-floor approach): "I don't want the
 * LENGTH affected, I want the WHOLE scale to stay the same relative to the bbox;
 * when I zoom out the scrubber becomes large and intrusive." The old
 * scrubberWidthForAoi only stretched the TRACK and floored at 200px, so a tiny
 * zoomed-out box still got a 200px-wide bar with full-size buttons/font — huge
 * relative to the box.
 *
 * Instead, derive a single SCALE FACTOR from the AOI bbox's on-screen EAST-WEST
 * pixel width relative to a REFERENCE width (the width at which the scrubber
 * renders at its natural 1.0). The caller applies this as a uniform
 * `transform: scale(s)` (transform-origin bottom center) so the ENTIRE widget —
 * buttons, handle, font, track — shrinks/grows together with the box on-screen.
 *
 * Clamped to [min, max] so the widget never becomes unusably tiny or absurdly
 * large. Returns the natural 1.0 when there is no rect / the rect is degenerate
 * (zero or negative on-screen width), so an AOI-less fallback renders at full
 * size. Pure pixel math (Invariant 1).
 */
export interface ScrubberScaleOptions {
  /** On-screen px width at which the scrubber renders at scale 1.0. Default 480. */
  referencePx?: number;
  /** Smallest allowed scale (never unusably tiny). Default 0.7. */
  min?: number;
  /** Largest allowed scale (never absurdly large/intrusive). Default 1.15. */
  max?: number;
  /**
   * Scale at/below which the scrubber HIDES entirely instead of rendering an
   * unusably tiny widget (NATE 2026-06-21). Default = `min` (0.7).
   */
  hideBelowScale?: number;
}

/** On-screen px width at which the scrubber renders at its natural 1.0 scale. */
export const DEFAULT_SCRUBBER_SCALE_REFERENCE_PX = 480;
/**
 * Smallest VISIBLE scrubber scale. At this scale the buttons are ~18px (still
 * tappable); below it the widget HIDES rather than shrink into uselessness, so
 * this doubles as the hide threshold (DEFAULT_SCRUBBER_HIDE_BELOW_SCALE).
 */
export const DEFAULT_SCRUBBER_SCALE_MIN = 0.7;
/** Largest scrubber scale (never intrusive when zoomed in). */
export const DEFAULT_SCRUBBER_SCALE_MAX = 1.15;
/**
 * Scale threshold below which the scrubber is HIDDEN (NATE 2026-06-21: "when it
 * gets to that point let's just hide it so we have a threshold of when it's
 * visible"). Equal to the min visible scale so a shown scrubber is always >= it.
 */
export const DEFAULT_SCRUBBER_HIDE_BELOW_SCALE = DEFAULT_SCRUBBER_SCALE_MIN;

export function scrubberScaleForAoi(
  rect: ScreenRect | null | undefined,
  opts: ScrubberScaleOptions = {},
): number {
  const reference = opts.referencePx ?? DEFAULT_SCRUBBER_SCALE_REFERENCE_PX;
  const min = opts.min ?? DEFAULT_SCRUBBER_SCALE_MIN;
  const max = opts.max ?? DEFAULT_SCRUBBER_SCALE_MAX;
  if (!rect) return 1;
  // The bbox on-screen EAST-WEST extent: right - left of the projected rect.
  const w = rect.right - rect.left;
  if (!Number.isFinite(w) || w <= 0 || reference <= 0) return 1;
  const raw = w / reference;
  // Clamp to [min, max]. If the band is degenerate (min > max) the floor wins.
  return Math.max(min, Math.min(raw, max));
}

/**
 * Whether the AOI-anchored scrubber should be VISIBLE at the current AOI
 * on-screen size (NATE 2026-06-21). When the bbox is zoomed out so far that the
 * scrubber would scale below `hideBelowScale` (the unusably-tiny floor), HIDE it
 * entirely; it reappears as soon as the user zooms back in past the threshold.
 *
 * Returns true when there is NO AOI rect (the viewport-bottom fallback scrubber
 * is always shown) or the rect is degenerate (no usable on-screen width -> fall
 * back to showing it), and otherwise true only when the AOI's on-screen width is
 * large enough that the scaled widget stays usable. Pure pixel math.
 */
export function scrubberVisibleForAoi(
  rect: ScreenRect | null | undefined,
  opts: ScrubberScaleOptions = {},
): boolean {
  if (!rect) return true;
  const reference = opts.referencePx ?? DEFAULT_SCRUBBER_SCALE_REFERENCE_PX;
  const hideBelow =
    opts.hideBelowScale ?? opts.min ?? DEFAULT_SCRUBBER_HIDE_BELOW_SCALE;
  const w = rect.right - rect.left;
  if (!Number.isFinite(w) || w <= 0 || reference <= 0) return true;
  return w / reference >= hideBelow;
}

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
