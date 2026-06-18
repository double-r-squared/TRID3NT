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
  layoutKeysCcw,
  nearestSide,
  rectFromAnchorAndWidth,
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
