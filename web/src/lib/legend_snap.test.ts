// GRACE-2 web — legend_snap pure-geometry unit tests.
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
  nearestSide,
  rectFromAnchorAndWidth,
  scrubberScaleForAoi,
  DEFAULT_SCRUBBER_SCALE_MIN,
  DEFAULT_SCRUBBER_SCALE_MAX,
  DEFAULT_SCRUBBER_SCALE_REFERENCE_PX,
  sideForIndex,
  snapKeyToSide,
  stackPositionForIndex,
  type ScreenRect,
} from "./legend_snap";

const AOI: ScreenRect = { left: 100, top: 100, right: 300, bottom: 300 };
// center = (200, 200)

describe("sideForIndex — CCW order, wrapping", () => {
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

// Item d (SCALE WITH AOI, NATE 2026-06-20) — the AOI-anchored overlays (legend
// keys + scrubber) scale with the AOI bbox's on-screen size so a tiny zoomed-out
// box does not get a fixed-px overlay that dwarfs it, and a big zoomed-in box
// gets a larger one — both clamped to [min, max].
describe("aoiScaleFactor — scales with the AOI on-screen size, clamped", () => {
  it("returns the natural 1.0 scale at the reference on-screen size", () => {
    // A square AOI whose limiting (min) extent == the reference px => scale 1.0.
    const ref = DEFAULT_AOI_SCALE_REFERENCE_PX;
    const rect: ScreenRect = { left: 0, top: 0, right: ref, bottom: ref };
    expect(aoiScaleFactor(rect)).toBeCloseTo(1, 5);
  });

  it("shrinks (but never below min) when the AOI is tiny on-screen (zoomed out)", () => {
    // A 20px x 20px box: raw = 20/360 ≈ 0.056, clamped UP to the min floor.
    const rect: ScreenRect = { left: 0, top: 0, right: 20, bottom: 20 };
    const s = aoiScaleFactor(rect);
    expect(s).toBe(DEFAULT_AOI_SCALE_MIN);
    // Strictly smaller than the natural scale — the overlay shrinks with the box.
    expect(s).toBeLessThan(1);
  });

  it("grows (but never above max) when the AOI is huge on-screen (zoomed in)", () => {
    // A 4000px box: raw = 4000/360 ≈ 11, clamped DOWN to the max ceiling.
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

// scrubberScaleForAoi (NATE 2026-06-21) — the time scrubber's UNIFORM SCALE
// FACTOR = AOI bbox on-screen EAST-WEST width / reference, clamped to [min, max].
// The caller applies it as a single `transform: scale(s)` so the WHOLE widget
// (buttons, handle, font, track) shrinks/grows together with the box on-screen.
// Returns the natural 1.0 when there is no rect / it's degenerate. NATE: "I don't
// want the LENGTH affected, I want the whole scale to stay the same relative to
// the bbox; when I zoom out the scrubber becomes large and intrusive."
describe("scrubberScaleForAoi — uniform scale == bbox width / reference (clamped)", () => {
  it("returns the natural 1.0 scale at the reference on-screen width", () => {
    const ref = DEFAULT_SCRUBBER_SCALE_REFERENCE_PX;
    const rect: ScreenRect = { left: 0, top: 0, right: ref, bottom: 300 };
    expect(scrubberScaleForAoi(rect)).toBeCloseTo(1, 5);
  });

  it("a SMALL (zoomed-out) bbox -> a small scale WELL BELOW 1 (clamped at min)", () => {
    // 12px-wide box -> raw 12/480 = 0.025 -> clamped UP to the min floor.
    const tiny: ScreenRect = { left: 500, top: 500, right: 512, bottom: 540 };
    const s = scrubberScaleForAoi(tiny);
    expect(s).toBe(DEFAULT_SCRUBBER_SCALE_MIN);
    // Strictly below the natural scale: the widget shrinks with the box, so it is
    // no longer large/intrusive when zoomed out.
    expect(s).toBeLessThan(1);
  });

  it("a mid-size bbox scales proportionally between min and 1", () => {
    // 240px-wide box -> raw 240/480 = 0.5 (== the default floor here).
    const mid: ScreenRect = { left: 0, top: 0, right: 240, bottom: 200 };
    expect(scrubberScaleForAoi(mid)).toBeCloseTo(0.5, 5);
    // 360px box -> 0.75, strictly between min and 1.
    const bigger: ScreenRect = { left: 0, top: 0, right: 360, bottom: 200 };
    expect(scrubberScaleForAoi(bigger)).toBeCloseTo(0.75, 5);
  });

  it("ignores the bbox HEIGHT — only the on-screen WIDTH drives the scale", () => {
    const wideShort: ScreenRect = { left: 0, top: 0, right: 480, bottom: 40 };
    const wideTall: ScreenRect = { left: 0, top: 0, right: 480, bottom: 1200 };
    expect(scrubberScaleForAoi(wideShort)).toBeCloseTo(1, 5);
    expect(scrubberScaleForAoi(wideTall)).toBeCloseTo(1, 5);
  });

  it("a LARGE (zoomed-in) bbox -> capped at the MAX ceiling", () => {
    // 4000px box -> raw ~8.3 -> clamped DOWN to the max ceiling.
    const huge: ScreenRect = { left: 0, top: 0, right: 4000, bottom: 4000 };
    expect(scrubberScaleForAoi(huge)).toBe(DEFAULT_SCRUBBER_SCALE_MAX);
    expect(DEFAULT_SCRUBBER_SCALE_MAX).toBeGreaterThan(1);
  });

  it("respects custom reference / clamp options", () => {
    // Custom reference: a 240px box at reference 240 -> exactly 1.0.
    const rect: ScreenRect = { left: 0, top: 0, right: 240, bottom: 200 };
    expect(scrubberScaleForAoi(rect, { referencePx: 240 })).toBeCloseTo(1, 5);
    // Custom min floor.
    const tiny: ScreenRect = { left: 0, top: 0, right: 10, bottom: 10 };
    expect(scrubberScaleForAoi(tiny, { min: 0.3 })).toBe(0.3);
    // Custom max ceiling.
    const big: ScreenRect = { left: 0, top: 0, right: 9000, bottom: 9000 };
    expect(scrubberScaleForAoi(big, { max: 2.0 })).toBe(2.0);
  });

  it("returns the natural 1.0 scale for null / undefined / degenerate width", () => {
    expect(scrubberScaleForAoi(null)).toBe(1);
    expect(scrubberScaleForAoi(undefined)).toBe(1);
    // Zero-width rect -> no AOI width to size against -> natural scale.
    expect(scrubberScaleForAoi({ left: 200, top: 0, right: 200, bottom: 300 })).toBe(1);
    // Inverted (negative) width -> degenerate -> natural scale.
    expect(scrubberScaleForAoi({ left: 300, top: 0, right: 100, bottom: 300 })).toBe(1);
  });

  it("default ceiling is greater than the default floor (sane band)", () => {
    expect(DEFAULT_SCRUBBER_SCALE_MAX).toBeGreaterThan(DEFAULT_SCRUBBER_SCALE_MIN);
  });
});
