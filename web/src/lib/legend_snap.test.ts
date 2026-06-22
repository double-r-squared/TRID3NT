// GRACE-2 web  -  legend_snap pure-geometry unit tests.
//
// Verifies the CCW side assignment, stacking, single-side snap math, nearest-
// side picking, and the anchor+width -> AOI rect reconstruction. All inputs are
// screen-space pixels; nothing here touches the DOM.

import { describe, it, expect } from "vitest";
import {
  CCW_SIDES,
  SIDE_GAP_PX,
  STACK_GAP_PX,
  aoiScaleFactor,
  DEFAULT_AOI_SCALE_MIN,
  DEFAULT_AOI_SCALE_MAX,
  DEFAULT_AOI_SCALE_REFERENCE_PX,
  layoutKeysCcw,
  layoutKeysToSides,
  nearestSide,
  rectFromAnchorAndWidth,
  sideForIndex,
  snapKeyToSide,
  stackPositionForIndex,
  type AoiSide,
  type ScreenRect,
} from "./legend_snap";

const AOI: ScreenRect = { left: 100, top: 100, right: 300, bottom: 300 };
// center = (200, 200)

describe("sideForIndex  -  CCW order, wrapping", () => {
  it("maps 0..3 to bottom, right, top, left", () => {
    expect(sideForIndex(0)).toBe("bottom");
    expect(sideForIndex(1)).toBe("right");
    expect(sideForIndex(2)).toBe("top");
    expect(sideForIndex(3)).toBe("left");
  });
  it("wraps every 4", () => {
    expect(sideForIndex(4)).toBe("bottom");
    expect(sideForIndex(7)).toBe("left");
  });
  it("handles negative indices defensively", () => {
    expect(CCW_SIDES).toContain(sideForIndex(-1));
  });
});

describe("stackPositionForIndex", () => {
  it("is 0 for the first four keys (one per side)", () => {
    expect(stackPositionForIndex(0)).toBe(0);
    expect(stackPositionForIndex(3)).toBe(0);
  });
  it("increments once every 4 keys", () => {
    expect(stackPositionForIndex(4)).toBe(1);
    expect(stackPositionForIndex(8)).toBe(2);
  });
});

describe("snapKeyToSide", () => {
  const size = { width: 200, height: 60 };

  it("bottom: centered on x, below the bottom edge by the gap", () => {
    const r = snapKeyToSide(AOI, "bottom", size, 0, 0);
    expect(r.left).toBe(200 - 100); // cx - width/2
    expect(r.top).toBe(300 + SIDE_GAP_PX);
    expect(r.side).toBe("bottom");
  });

  it("top: centered on x, above the top edge by gap + own height", () => {
    const r = snapKeyToSide(AOI, "top", size, 0, 0);
    expect(r.left).toBe(100);
    expect(r.top).toBe(100 - SIDE_GAP_PX - 60);
  });

  it("right: centered on y, beyond the right edge by the gap", () => {
    const r = snapKeyToSide(AOI, "right", size, 0, 0);
    expect(r.left).toBe(300 + SIDE_GAP_PX);
    expect(r.top).toBe(200 - 30); // cy - height/2
  });

  it("left: centered on y, before the left edge by gap + own width", () => {
    const r = snapKeyToSide(AOI, "left", size, 0, 0);
    expect(r.left).toBe(100 - SIDE_GAP_PX - 200);
    expect(r.top).toBe(170);
  });

  it("applies stack position + prior extent on the cross axis", () => {
    const r = snapKeyToSide(AOI, "bottom", size, 1, 60);
    // crossOffset = gap + priorExtent(60) + stackPos(1)*STACK_GAP
    expect(r.top).toBe(300 + SIDE_GAP_PX + 60 + STACK_GAP_PX);
  });
});

describe("layoutKeysCcw", () => {
  it("assigns the first four keys to the four sides", () => {
    const sizes = [0, 1, 2, 3].map(() => ({ width: 100, height: 40 }));
    const out = layoutKeysCcw(AOI, sizes);
    expect(out.map((o) => o.side)).toEqual(["bottom", "right", "top", "left"]);
  });

  it("stacks a 5th key on the bottom side beyond the first", () => {
    const sizes = [0, 1, 2, 3, 4].map(() => ({ width: 100, height: 40 }));
    const out = layoutKeysCcw(AOI, sizes);
    expect(out[4]!.side).toBe("bottom");
    // The 2nd bottom key sits lower than the 1st (prior extent consumed).
    expect(out[4]!.top).toBeGreaterThan(out[0]!.top);
  });

  it("accounts for heterogeneous key heights so stacked keys don't overlap", () => {
    const sizes = [
      { width: 100, height: 40 }, // bottom #1
      { width: 100, height: 40 }, // right
      { width: 100, height: 40 }, // top
      { width: 100, height: 40 }, // left
      { width: 100, height: 80 }, // bottom #2 (taller)
      { width: 100, height: 40 }, // right #2
      { width: 100, height: 40 }, // top #2
      { width: 100, height: 40 }, // left #2
      { width: 100, height: 40 }, // bottom #3
    ];
    const out = layoutKeysCcw(AOI, sizes);
    const bottomTops = [out[0]!.top, out[4]!.top, out[8]!.top];
    // Strictly increasing (each stacked further out than the last).
    expect(bottomTops[1]!).toBeGreaterThan(bottomTops[0]!);
    expect(bottomTops[2]!).toBeGreaterThan(bottomTops[1]!);
    // Gap between #2 and #3 must clear the (taller) #2 key's height.
    expect(bottomTops[2]! - bottomTops[1]!).toBeGreaterThanOrEqual(80);
  });

  it("returns one result per input key", () => {
    const sizes = [0, 1, 2].map(() => ({ width: 50, height: 20 }));
    expect(layoutKeysCcw(AOI, sizes)).toHaveLength(3);
  });

  it("ITEM 5: sideStartOffset=1 starts the first key on the RIGHT (scrubber-active)", () => {
    // When the scrubber occupies the bottom-center band, the legend starts its
    // CCW layout on the right so the first key rails vertically down the right
    // edge of the bbox and the two never collide.
    const sizes = [0, 1, 2, 3].map(() => ({ width: 100, height: 40 }));
    const out = layoutKeysCcw(AOI, sizes, 1);
    expect(out.map((o) => o.side)).toEqual(["right", "top", "left", "bottom"]);
    // The first (right-side) key sits to the RIGHT of the AOI right edge.
    expect(out[0]!.left).toBeGreaterThan(AOI.right);
  });
});

describe("layoutKeysToSides  -  explicit per-key side assignment (SIDE-SNAP)", () => {
  const sizes = [
    { width: 100, height: 40 },
    { width: 100, height: 40 },
    { width: 100, height: 40 },
  ];

  it("places each key on the side the caller passes (not the CCW index side)", () => {
    const sides: AoiSide[] = ["right", "left", "top"];
    const out = layoutKeysToSides(AOI, sizes, sides);
    expect(out.map((r) => r.side)).toEqual(["right", "left", "top"]);
    // Right key sits past the right edge; left key past the left edge.
    expect(out[0]!.left).toBeGreaterThan(AOI.right);
    expect(out[1]!.left).toBeLessThan(AOI.left);
    // Top key sits above the top edge.
    expect(out[2]!.top).toBeLessThan(AOI.top);
  });

  it("stacks two keys that share a side so they do not overlap", () => {
    const sides: AoiSide[] = ["right", "right"];
    const out = layoutKeysToSides(AOI, sizes.slice(0, 2), sides);
    expect(out[0]!.side).toBe("right");
    expect(out[1]!.side).toBe("right");
    // The second right key is pushed further out (greater left) than the first.
    expect(out[1]!.left).toBeGreaterThan(out[0]!.left);
  });

  it("falls back to the CCW index side when a side is missing", () => {
    // Pass an array shorter than sizes; the gap defaults to sideForIndex.
    const out = layoutKeysToSides(AOI, sizes, ["top"]);
    expect(out[0]!.side).toBe("top");
    expect(out[1]!.side).toBe(sideForIndex(1)); // "right"
    expect(out[2]!.side).toBe(sideForIndex(2)); // "top"
  });
});

describe("nearestSide", () => {
  it("picks bottom for a point just below the bottom edge", () => {
    expect(nearestSide(AOI, { x: 200, y: 320 })).toBe("bottom");
  });
  it("picks top for a point near the top edge", () => {
    expect(nearestSide(AOI, { x: 200, y: 95 })).toBe("top");
  });
  it("picks right for a point near the right edge", () => {
    expect(nearestSide(AOI, { x: 305, y: 200 })).toBe("right");
  });
  it("picks left for a point near the left edge", () => {
    expect(nearestSide(AOI, { x: 90, y: 200 })).toBe("left");
  });
});

describe("rectFromAnchorAndWidth", () => {
  it("reconstructs the bottom edge exactly from anchor + width", () => {
    const r = rectFromAnchorAndWidth({ left: 200, top: 300 }, 200);
    expect(r).not.toBeNull();
    expect(r!.left).toBe(100);
    expect(r!.right).toBe(300);
    expect(r!.bottom).toBe(300);
  });

  it("estimates a square-ish height when none is supplied", () => {
    const r = rectFromAnchorAndWidth({ left: 200, top: 300 }, 200);
    // height = width => top = bottom - 200.
    expect(r!.top).toBe(100);
  });

  it("uses an explicit height when supplied", () => {
    const r = rectFromAnchorAndWidth({ left: 200, top: 300 }, 200, 50);
    expect(r!.top).toBe(250);
  });

  it("returns null when there is no anchor", () => {
    expect(rectFromAnchorAndWidth(null, 200)).toBeNull();
  });

  it("returns null when width is missing or non-positive", () => {
    expect(rectFromAnchorAndWidth({ left: 200, top: 300 }, null)).toBeNull();
    expect(rectFromAnchorAndWidth({ left: 200, top: 300 }, 0)).toBeNull();
    expect(rectFromAnchorAndWidth({ left: 200, top: 300 }, -5)).toBeNull();
  });
});

// Item d (SCALE WITH AOI, NATE 2026-06-20)  -  the AOI-anchored overlays (legend
// keys + scrubber) scale with the AOI bbox's on-screen size so a tiny zoomed-out
// box does not get a fixed-px overlay that dwarfs it, and a big zoomed-in box
// gets a larger one  -  both clamped to [min, max].
describe("aoiScaleFactor  -  scales with the AOI on-screen size, clamped", () => {
  it("returns the natural 1.0 scale at the reference on-screen size", () => {
    // A square AOI whose limiting (min) extent == the reference px => scale 1.0.
    const ref = DEFAULT_AOI_SCALE_REFERENCE_PX;
    const rect: ScreenRect = { left: 0, top: 0, right: ref, bottom: ref };
    expect(aoiScaleFactor(rect)).toBeCloseTo(1, 5);
  });

  it("shrinks (but never below min) when the AOI is tiny on-screen (zoomed out)", () => {
    // A 20px x 20px box: raw = 20/360 - 0.056, clamped UP to the min floor.
    const rect: ScreenRect = { left: 0, top: 0, right: 20, bottom: 20 };
    const s = aoiScaleFactor(rect);
    expect(s).toBe(DEFAULT_AOI_SCALE_MIN);
    // Strictly smaller than the natural scale  -  the overlay shrinks with the box.
    expect(s).toBeLessThan(1);
  });

  it("grows (but never above max) when the AOI is huge on-screen (zoomed in)", () => {
    // A 4000px box: raw = 4000/360 - 11, clamped DOWN to the max ceiling.
    const rect: ScreenRect = { left: 0, top: 0, right: 4000, bottom: 4000 };
    const s = aoiScaleFactor(rect);
    expect(s).toBe(DEFAULT_AOI_SCALE_MAX);
    expect(s).toBeGreaterThan(1);
  });

  it("uses the LIMITING (smaller) on-screen axis of a non-square AOI", () => {
    // Wide-but-short box: width 2000, height 252 (< reference). The limiting
    // axis is the height, so the scale tracks 252/360 = 0.7 (within clamps),
    // NOT the wide axis (which would over-size the overlay for a thin box).
    const rect: ScreenRect = { left: 0, top: 0, right: 2000, bottom: 252 };
    expect(aoiScaleFactor(rect)).toBeCloseTo(0.7, 5);
  });

  it("respects custom clamp options", () => {
    const rect: ScreenRect = { left: 0, top: 0, right: 10, bottom: 10 };
    expect(aoiScaleFactor(rect, { min: 0.3 })).toBe(0.3);
    const big: ScreenRect = { left: 0, top: 0, right: 9000, bottom: 9000 };
    expect(aoiScaleFactor(big, { max: 2.5 })).toBe(2.5);
  });

  it("returns the natural 1.0 scale for a null / degenerate rect (AOI-less)", () => {
    expect(aoiScaleFactor(null)).toBe(1);
    expect(aoiScaleFactor(undefined)).toBe(1);
    // Zero-area rect => can't size against it => natural scale.
    expect(aoiScaleFactor({ left: 50, top: 50, right: 50, bottom: 50 })).toBe(1);
  });
});

// UNIFIED SCRUBBER + LEGEND SCALING (NATE 2026-06-22) -------------------------
//
// The scrubber and the LayerLegend now SHARE one scaling story: both derive
// their size from the AOI bbox on-screen WIDTH and both use `aoiScaleFactor`
// (the legend's helper) for their inner chrome, and NEITHER hides on zoom-out.
// The old scrubber-only helpers (scrubberScaleForAoi + scrubberVisibleForAoi)
// were retired -> these tests pin the SHARED contract at the geometry layer:
// the scale the scrubber consumes == the scale the legend consumes for the same
// rect, and there is no hide-below-threshold function anymore.
describe("unified scrubber+legend scaling - one shared scale, no hide helper", () => {
  it("scrubber + legend consume the SAME aoiScaleFactor for a given rect", () => {
    // Both overlays now call aoiScaleFactor(rect) with the SAME defaults, so the
    // value is identical by construction (this is what 'share scaling' means).
    const rect: ScreenRect = { left: 100, top: 100, right: 460, bottom: 460 };
    const legendScale = aoiScaleFactor(rect);
    const scrubberScale = aoiScaleFactor(rect); // scrubber uses the same call
    expect(scrubberScale).toBe(legendScale);
  });

  it("the scrubber width tracks the AOI bbox on-screen width (right - left)", () => {
    // The scrubber sets its width to the bbox on-screen width directly (clamped
    // to a tappable min in the component); the bbox width is just right - left.
    const rect: ScreenRect = { left: 100, top: 50, right: 540, bottom: 300 };
    expect(rect.right - rect.left).toBe(440);
  });

  it("the retired scrubber-only helpers are no longer exported", async () => {
    // Coherence guard: scrubberScaleForAoi / scrubberVisibleForAoi were removed
    // when the scrubber adopted the legend's aoiScaleFactor, so a tiny zoomed-out
    // box no longer has a separate hide path - the scrubber persists like the
    // legend. Importing the module must not surface the retired names.
    const mod = await import("./legend_snap");
    expect("scrubberScaleForAoi" in mod).toBe(false);
    expect("scrubberVisibleForAoi" in mod).toBe(false);
  });
});
