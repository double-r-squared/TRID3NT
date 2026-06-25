// GRACE-2 web — SequenceScrubber unit tests (sequential-layer-grouping).
//
// The scrubber is the bottom-center map overlay that steps the active
// sequential group's frames (slider + LEFT/RIGHT + play/pause). It is pure
// presentation — all frame state + the step callback come in as props. It
// portals to document.body so its fixed bottom-center placement resolves
// against the viewport (not the LayerPanel's stacking context).

import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { render, screen, fireEvent, cleanup, act } from "@testing-library/react";
import {
  SequenceScrubber,
  wrapIndex,
  SCRUBBER_MOBILE_BOTTOM_CSS,
  SCRUBBER_MOBILE_SHEET_CLEARANCE_PX,
} from "./SequenceScrubber";

afterEach(() => {
  cleanup();
});

describe("wrapIndex", () => {
  it("wraps within [0, n) with positive + negative inputs", () => {
    expect(wrapIndex(0, 3)).toBe(0);
    expect(wrapIndex(3, 3)).toBe(0); // wrap past end
    expect(wrapIndex(-1, 3)).toBe(2); // wrap before start
    expect(wrapIndex(4, 3)).toBe(1);
  });

  it("returns 0 for an empty series", () => {
    expect(wrapIndex(2, 0)).toBe(0);
  });
});

const FRAMES = ["F+01h", "F+03h", "F+06h"];

function renderScrubber(overrides: Partial<React.ComponentProps<typeof SequenceScrubber>> = {}) {
  const onStep = vi.fn();
  const onPlayToggle = vi.fn();
  const utils = render(
    <SequenceScrubber
      label="HRRR precip"
      frameLabels={FRAMES}
      activeIndex={0}
      onStep={onStep}
      playing={false}
      onPlayToggle={onPlayToggle}
      {...overrides}
    />,
  );
  return { onStep, onPlayToggle, ...utils };
}

describe("SequenceScrubber — render + controls", () => {
  it("renders the compact x/N counter (no group label, no full frame label)", () => {
    renderScrubber({ activeIndex: 1 });
    // Item 4: only x/N shown in the scrubber, not the group label or frame label text.
    expect(screen.getByTestId("scrubber-frame-label")).toHaveTextContent("2/3");
    // No group-label element in the scrubber.
    expect(screen.queryByTestId("scrubber-group-label")).toBeNull();
  });

  it("portals to document.body (escapes the panel stacking context)", () => {
    renderScrubber();
    const el = screen.getByTestId("grace2-sequence-scrubber");
    expect(document.body.contains(el)).toBe(true);
    expect(el).toHaveStyle({ position: "fixed" });
  });

  it("NEXT arrow steps forward (wrapping at the end)", () => {
    const { onStep } = renderScrubber({ activeIndex: 2 });
    fireEvent.click(screen.getByTestId("scrubber-next"));
    expect(onStep).toHaveBeenCalledWith(0); // wraps 2 -> 0
  });

  it("PREV arrow steps backward (wrapping at the start)", () => {
    const { onStep } = renderScrubber({ activeIndex: 0 });
    fireEvent.click(screen.getByTestId("scrubber-prev"));
    expect(onStep).toHaveBeenCalledWith(2); // wraps 0 -> 2
  });

  it("the slider steps to the dragged index", () => {
    const { onStep } = renderScrubber({ activeIndex: 0 });
    fireEvent.change(screen.getByTestId("scrubber-slider"), {
      target: { value: "2" },
    });
    expect(onStep).toHaveBeenCalledWith(2);
  });

  it("renders a play/pause button on the scrubber (JOB WEB-ANIM #157.3)", () => {
    renderScrubber({ playing: false });
    const play = screen.getByTestId("scrubber-play");
    expect(play).toBeInTheDocument();
    expect(play).toHaveAttribute("aria-label", "Play sequence");
  });

  it("the play button shows PAUSE state + fires onPlayToggle when clicked", () => {
    const { onPlayToggle } = renderScrubber({ playing: true });
    const play = screen.getByTestId("scrubber-play");
    expect(play).toHaveAttribute("aria-label", "Pause sequence");
    fireEvent.click(play);
    expect(onPlayToggle).toHaveBeenCalledTimes(1);
  });

  it("disables the play button for a single-frame series", () => {
    renderScrubber({ frameLabels: ["F+01h"], activeIndex: 0 });
    expect(screen.getByTestId("scrubber-play")).toBeDisabled();
  });

  it("renders nothing for an empty frame list", () => {
    render(
      <SequenceScrubber
        label="x"
        frameLabels={[]}
        activeIndex={0}
        onStep={() => {}}
        playing={false}
        onPlayToggle={() => {}}
      />,
    );
    expect(screen.queryByTestId("grace2-sequence-scrubber")).toBeNull();
  });

  it("disables prev/next controls for a single-frame series", () => {
    renderScrubber({ frameLabels: ["F+01h"], activeIndex: 0 });
    expect(screen.getByTestId("scrubber-next")).toBeDisabled();
    expect(screen.getByTestId("scrubber-prev")).toBeDisabled();
  });

  it("snaps bottom-center to aoiRect when provided (item 3)", () => {
    renderScrubber({
      aoiRect: { left: 100, top: 50, right: 700, bottom: 400 },
    });
    const el = screen.getByTestId("grace2-sequence-scrubber");
    // Center of rect is (100+700)/2 = 400; top = bottom(400) + 12 = 412.
    expect(el).toHaveStyle({ left: "400px", top: "412px" });
  });

  it("falls back to the gutter bottom-center when aoiRect is absent (item 3 + gutter clamp)", () => {
    // GUTTER CLAMP (NATE 2026-06-24): with no AOI the DESKTOP fallback now centers
    // in the OPEN map gutter rather than at a bare viewport 50%, so it never runs
    // under the side panels. With no panels open the gutter is the full viewport,
    // so the center is viewport/2 (a px value, not "50%"). Anchored bottom: 24.
    renderScrubber();
    const el = screen.getByTestId("grace2-sequence-scrubber");
    const expectedCx = Math.round(window.innerWidth / 2);
    expect(el).toHaveStyle({ left: `${expectedCx}px`, bottom: "24px" });
    expect(el.style.transform).toBe("translateX(-50%)");
  });

  it("clamps its width + center to the open gutter so it never runs under the side panels (NATE 2026-06-24)", () => {
    // A LEFT rail (288) + a RIGHT chat panel (400) leave an open gutter; the
    // scrubber must sit centered within it and never extend past either panel.
    const leftPanelWidthPx = 288;
    const chatWidthPx = 400;
    renderScrubber({ leftPanelWidthPx, chatWidthPx, chatCollapsed: false });
    const el = screen.getByTestId("grace2-sequence-scrubber");
    const margin = 12;
    const gutterLeft = leftPanelWidthPx + margin;
    const gutterRight = window.innerWidth - chatWidthPx - margin;
    const width = Number.parseFloat(el.style.width);
    const cx = Number.parseFloat(el.style.left);
    // The pill stays fully inside the open gutter on both edges.
    expect(cx - width / 2).toBeGreaterThanOrEqual(gutterLeft - 0.5);
    expect(cx + width / 2).toBeLessThanOrEqual(gutterRight + 0.5);
  });

  it("clamps the AOI-pinned scrubber below the fold to the viewport bottom (3D pitch fix, NATE 2026-06-24)", () => {
    // 3D-CLAMP: under a steep terrain pitch the projected AOI bottom edge lands
    // BELOW the viewport, so the AOI-bottom anchor would render the pill off
    // screen. When aoiRect.bottom+12 exceeds the desktop max-top the scrubber
    // anchors from the viewport bottom instead (stays visible in 3D).
    const belowFold = window.innerHeight + 500; // AOI bottom way past the viewport
    renderScrubber({
      aoiRect: { left: 100, top: 50, right: 700, bottom: belowFold },
    });
    const el = screen.getByTestId("grace2-sequence-scrubber");
    // Anchored from the bottom (visible), NOT a top that pushes it off screen.
    expect(el.style.bottom).toBe("24px");
    expect(el.style.top).toBe("");
  });

  it("centers under the AOI box with NO uniform scale transform (NATE 2026-06-22)", () => {
    // UNIFIED SCALING: the scrubber no longer applies a transform: scale() to the
    // whole container (that left side padding vs the bbox). It centers under the
    // box and tracks the bbox WIDTH directly (like the legend), so the transform
    // is just the centering translate.
    renderScrubber({ aoiRect: { left: 100, top: 50, right: 700, bottom: 400 } });
    const el = screen.getByTestId("grace2-sequence-scrubber");
    expect(el.style.transform).toBe("translateX(-50%)");
    expect(el.style.transform).not.toContain("scale(");
    // Anchored under the box edge -> origin is the TOP so it grows downward.
    expect(el.style.transformOrigin).toBe("top center");
  });
});

// JOB WEB-ANIM (#157.1) — the auto-advance INTERVAL no longer lives in the
// scrubber; the module-level AnimationController owns it (so playback survives a
// panel unmount). The scrubber must therefore NOT run its own timer, otherwise
// frames would advance twice as fast (controller tick + scrubber tick). These
// tests pin that: even with `playing`, the scrubber never auto-steps on its own.
describe("SequenceScrubber — no internal auto-advance (controller owns the timer)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.runOnlyPendingTimers();
    vi.useRealTimers();
  });

  it("does NOT auto-advance on its own while playing (controller drives it)", () => {
    const onStep = vi.fn();
    render(
      <SequenceScrubber
        label="HRRR precip"
        frameLabels={FRAMES}
        activeIndex={0}
        onStep={onStep}
        playing
        onPlayToggle={() => {}}
        intervalMs={1000}
      />,
    );
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    // No internal interval — the scrubber reflects state, it doesn't advance it.
    expect(onStep).not.toHaveBeenCalled();
  });

  it("does not auto-advance when paused either", () => {
    const onStep = vi.fn();
    render(
      <SequenceScrubber
        label="HRRR precip"
        frameLabels={FRAMES}
        activeIndex={0}
        onStep={onStep}
        playing={false}
        onPlayToggle={() => {}}
        intervalMs={1000}
      />,
    );
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(onStep).not.toHaveBeenCalled();
  });
});

// UNIFIED SCALING (NATE 2026-06-22) - the scrubber and the LayerLegend now share
// one scaling story: the scrubber's rendered WIDTH tracks the AOI bbox on-screen
// width (right - left), exactly like the legend's keys (LayerLegend sets each
// key width = clamped barWidth), with NO padding band; and it PERSISTS on
// zoom-out (no hide-below-threshold) like the legend does. These tests pin both.
describe("SequenceScrubber - width tracks the AOI bbox + persists on zoom-out", () => {
  it("sets its width to the AOI bbox on-screen width (right - left)", () => {
    // 600px-wide bbox -> the scrubber spans it (no narrowing/widening padding).
    renderScrubber({ aoiRect: { left: 100, top: 50, right: 700, bottom: 400 } });
    const el = screen.getByTestId("grace2-sequence-scrubber");
    expect(el.style.width).toBe("600px");
    // No min/max band that would add side padding vs the bbox.
    expect(el.style.minWidth).toBe("");
    expect(el.style.maxWidth).toBe("");
  });

  it("PERSISTS (stays mounted) on a TINY zoomed-out bbox, like the legend", () => {
    // A 12px-wide box used to HIDE the scrubber (scrubberVisibleForAoi). The
    // legend never vanished; NATE wanted them consistent, so the scrubber now
    // stays mounted and just clamps its width to the tappable minimum.
    renderScrubber({ aoiRect: { left: 500, top: 500, right: 512, bottom: 540 } });
    const el = screen.getByTestId("grace2-sequence-scrubber");
    expect(el).toBeInTheDocument();
    // Clamped to the tappable minimum (200px) so the buttons stay usable.
    expect(el.style.width).toBe("200px");
  });

  it("clamps to the tappable minimum width but never below it", () => {
    // A sub-minimum bbox (120px) -> floored at the 200px tappable minimum.
    renderScrubber({ aoiRect: { left: 0, top: 0, right: 120, bottom: 200 } });
    const el = screen.getByTestId("grace2-sequence-scrubber");
    expect(el.style.width).toBe("200px");
  });

  it("re-sizes when the projected bbox changes (pan/zoom recompute)", () => {
    const { rerender } = renderScrubber({
      aoiRect: { left: 0, top: 0, right: 300, bottom: 200 }, // 300px wide
    });
    expect(screen.getByTestId("grace2-sequence-scrubber").style.width).toBe("300px");
    // A subsequent map move re-projects a wider bbox -> the widget widens to match.
    rerender(
      <SequenceScrubber
        label="HRRR precip"
        frameLabels={FRAMES}
        activeIndex={0}
        onStep={() => {}}
        playing={false}
        onPlayToggle={() => {}}
        aoiRect={{ left: 0, top: 0, right: 480, bottom: 300 }} // 480px wide
      />,
    );
    expect(screen.getByTestId("grace2-sequence-scrubber").style.width).toBe("480px");
  });

  it("uses the AOI-less fallback width when there is no AOI rect", () => {
    renderScrubber();
    const el = screen.getByTestId("grace2-sequence-scrubber");
    expect(el.style.width).toBe("360px");
  });
});

// MOBILE Z-ORDER (NATE 2026-06-22) - on mobile the chat is a bottom sheet at
// zIndex 32; the scrubber must sit UNDERNEATH it so it never covers the composer.
// On desktop the chat is a side panel, so the scrubber keeps its higher z (51).
describe("SequenceScrubber - z-order vs the mobile chat sheet", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders BELOW the mobile chat sheet (z < 32) when the viewport is mobile", () => {
    // useIsMobile reads window.matchMedia -> stub it to report mobile.
    vi.stubGlobal(
      "matchMedia",
      vi.fn().mockReturnValue({
        matches: true,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
      }),
    );
    renderScrubber();
    const el = screen.getByTestId("grace2-sequence-scrubber");
    // The mobile chat bottom sheet is zIndex 32 (Chat.tsx); the scrubber must be
    // below it so the composer is never covered.
    expect(Number(el.style.zIndex)).toBeLessThan(32);
    expect(el.style.zIndex).toBe("31");
  });

  it("keeps the original higher z on desktop (matchMedia reports non-mobile)", () => {
    vi.stubGlobal(
      "matchMedia",
      vi.fn().mockReturnValue({
        matches: false,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
      }),
    );
    renderScrubber();
    const el = screen.getByTestId("grace2-sequence-scrubber");
    expect(el.style.zIndex).toBe("51");
  });
});

// ITEM 6 (NATE 2026-06-23) - the x/N counter must stay CONTAINED within the
// pill bounds (it used to leak past the right edge on both mobile + desktop).
describe("SequenceScrubber - x/N counter contained within the pill (ITEM 6)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("clips overflow so children stay within the rounded pill (box-sizing + overflow:hidden)", () => {
    renderScrubber({ aoiRect: { left: 100, top: 50, right: 700, bottom: 400 } });
    const el = screen.getByTestId("grace2-sequence-scrubber");
    // The pill contains its content; box-sizing + overflow:hidden keep the x/N
    // counter (and the buttons) WITHIN the rounded bounds.
    expect(el.style.overflow).toBe("hidden");
    expect(el.style.boxSizing).toBe("border-box");
  });

  it("the x/N counter is a child of the pill (not floated outside it)", () => {
    renderScrubber({ activeIndex: 1 });
    const el = screen.getByTestId("grace2-sequence-scrubber");
    const counter = screen.getByTestId("scrubber-frame-label");
    // The counter is INSIDE the pill element, so overflow:hidden contains it.
    expect(el.contains(counter)).toBe(true);
    expect(counter).toHaveTextContent("2/3");
  });

  it("contains the counter on a tiny (min-width-clamped) pill too", () => {
    // A tiny box clamps the pill to 200px; the slider yields (min-width 24) so
    // the buttons + counter still fit within the clipped pill.
    renderScrubber({ aoiRect: { left: 500, top: 500, right: 512, bottom: 540 } });
    const el = screen.getByTestId("grace2-sequence-scrubber");
    expect(el.style.width).toBe("200px");
    expect(el.style.overflow).toBe("hidden");
    expect(el.contains(screen.getByTestId("scrubber-frame-label"))).toBe(true);
  });
});

// ITEM 7 (NATE 2026-06-23) - the AOI-anchored scrubber's mount/visibility on
// zoom-out must be IDENTICAL on mobile and desktop. The product decision (NATE
// 2026-06-22) is that NEITHER platform hides on zoom-out (the old
// scrubberVisibleForAoi hide was retired); these tests pin that parity so the
// scrubber does not "disappear at a zoom distance" on one platform but not the
// other. Only POSITION/z-index differ by platform, never whether it mounts.
describe("SequenceScrubber - zoom-out visibility parity mobile vs desktop (ITEM 7)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function stubPlatform(mobile: boolean): void {
    vi.stubGlobal(
      "matchMedia",
      vi.fn().mockReturnValue({
        matches: mobile,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
      }),
    );
  }

  const tiny: React.ComponentProps<typeof SequenceScrubber>["aoiRect"] = {
    left: 500,
    top: 500,
    right: 512,
    bottom: 540,
  };

  it("stays MOUNTED on a tiny zoomed-out AOI on DESKTOP", () => {
    stubPlatform(false);
    renderScrubber({ aoiRect: tiny });
    expect(screen.getByTestId("grace2-sequence-scrubber")).toBeInTheDocument();
  });

  it("stays MOUNTED on the SAME tiny zoomed-out AOI on MOBILE (same as desktop)", () => {
    stubPlatform(true);
    renderScrubber({ aoiRect: tiny });
    expect(screen.getByTestId("grace2-sequence-scrubber")).toBeInTheDocument();
  });

  it("stays MOUNTED with NO aoiRect (off-screen) on BOTH platforms", () => {
    stubPlatform(false);
    const a = renderScrubber();
    expect(screen.getByTestId("grace2-sequence-scrubber")).toBeInTheDocument();
    a.unmount();
    stubPlatform(true);
    renderScrubber();
    expect(screen.getByTestId("grace2-sequence-scrubber")).toBeInTheDocument();
  });
});

// ITEM 6 (NATE 2026-06-22)  -  on mobile the scrubber must sit UNDER the chat
// bottom-sheet in POSITION too (not just z-order), so it never covers the
// composer even when the AOI sits low on screen.
describe("SequenceScrubber - mobile vertical clamp above the chat sheet (ITEM 6)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function stubMobile(): void {
    vi.stubGlobal(
      "matchMedia",
      vi.fn().mockReturnValue({
        matches: true,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
      }),
    );
    // A short viewport so an AOI near the bottom would otherwise drop the
    // scrubber into the chat sheet band.
    vi.stubGlobal("innerHeight", 600);
  }

  it("the mobile bottom-offset constant reserves the safe-area inset + a positive clearance (BUG 3)", () => {
    // Source-of-truth: a calc() over the device safe-area inset PLUS the fixed
    // sheet clearance, mirroring LayerLegend.MOBILE_LEGEND_PILL_BOTTOM_CSS. The
    // old clamp computed `top = innerHeight - CLEARANCE`, which OMITTED the
    // device safe-area inset (env() is invisible to JS), so on a notched phone
    // the reserved band fell short by the inset and overlapped the composer.
    // (jsdom's CSSOM drops calc(env(...)) from an inline `bottom`, so we pin the
    // exported constant directly  -  the same convention the legend pill uses.)
    expect(SCRUBBER_MOBILE_SHEET_CLEARANCE_PX).toBeGreaterThan(24);
    expect(SCRUBBER_MOBILE_BOTTOM_CSS).toBe(
      `calc(env(safe-area-inset-bottom) + ${SCRUBBER_MOBILE_SHEET_CLEARANCE_PX}px)`,
    );
    expect(SCRUBBER_MOBILE_BOTTOM_CSS).toContain("env(safe-area-inset-bottom)");
  });

  it("CLAMPS a low AOI to a BOTTOM anchor (drops the top anchor) so it clears the sheet (BUG 3)", () => {
    stubMobile();
    // AOI bottom at 580 -> unclamped top would be 592 (past the 600 viewport,
    // inside the chat sheet). The clamp must re-anchor the scrubber from the
    // BOTTOM (safe-area-inclusive) instead of a top clamped against innerHeight.
    renderScrubber({ aoiRect: { left: 100, top: 400, right: 300, bottom: 580 } });
    const el = screen.getByTestId("grace2-sequence-scrubber");
    // No top anchor anymore when clamped: it switches to a bottom anchor.
    expect(el.style.top).toBe("");
    // The bottom branch sets a calc(env(...)) value; jsdom drops it to "" - the
    // key invariant is it is NOT the bare desktop 24px nor the AOI's low top.
    expect(el.style.bottom).not.toBe("24px");
    // It is centered on the AOI horizontally regardless.
    expect(el.style.left).toBe("200px");
  });

  it("does NOT clamp when the AOI sits high enough (top already clears the sheet)", () => {
    stubMobile();
    // AOI bottom at 100 -> top = 112, far above maxTop(440): unchanged (top anchor).
    renderScrubber({ aoiRect: { left: 100, top: 40, right: 300, bottom: 100 } });
    const el = screen.getByTestId("grace2-sequence-scrubber");
    expect(parseFloat(el.style.top)).toBe(112);
    // High AOI keeps a TOP anchor (no bottom override).
    expect(el.style.bottom).toBe("");
  });

  it("AOI-less fallback anchors ABOVE the chat sheet on mobile (NOT the bare desktop 24px) (BUG 3)", () => {
    stubMobile();
    renderScrubber(); // no aoiRect
    const el = screen.getByTestId("grace2-sequence-scrubber");
    // Mobile fallback lifts the bottom anchor by the safe-area inset PLUS the
    // sheet clearance via CSS calc. jsdom drops calc(env(...)) to "" - the key
    // invariant is it is NOT the desktop 24px that sat behind the composer.
    expect(el.style.bottom).not.toBe("24px");
  });
});

// SCRUBBER DOCK RULE (NATE 2026-06-24, REVISES 18cc0da's always-dock) - on
// MOBILE the scrubber DEFAULTS to snapping the AOI bbox (width = the AOI
// on-screen width, centered, clamped so it can't pass the chat composer). It
// DOCKS to the chat-sheet top at FULL window width ONLY when the projected AOI
// is too small to be usable (zoomed out) OR there is no AOI on screen. The
// docked state is STABLE (anchored to sheetTopPx, not the live aoiRect) so it
// does not jitter; it tracks the sheet as it expands/collapses; and a hysteresis
// band keeps it from flip-flopping at the threshold.
describe("SequenceScrubber - mobile DOCKS when the AOI is too small (sheetTopPx)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function stubMobile(innerHeight: number): void {
    vi.stubGlobal(
      "matchMedia",
      vi.fn().mockReturnValue({
        matches: true,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
      }),
    );
    vi.stubGlobal("innerHeight", innerHeight);
  }

  it("DOCKS a SMALL (zoomed-out) AOI to the sheet top at full width - not centered on the AOI", () => {
    stubMobile(800);
    // A 120px-wide AOI is below the dock-enter threshold -> dock full width.
    // sheetTopPx=500 -> bottom = 800 - 500 + 8 = 308 (stable, off the sheet top).
    renderScrubber({
      aoiRect: { left: 100, top: 60, right: 220, bottom: 200 },
      sheetTopPx: 500,
    });
    const el = screen.getByTestId("grace2-sequence-scrubber");
    expect(el.style.bottom).toBe("308px");
    // No top anchor (docked from the bottom). Full-window docked mode is surfaced
    // via data-dock-mode (jsdom drops the calc(100vw - env(...)) width to "").
    expect(el.style.top).toBe("");
    expect(el.getAttribute("data-dock-mode")).toBe("docked");
    // NOT centered on the AOI box (left is a window-center calc, not 160px).
    expect(el.style.left).not.toBe("160px");
  });

  it("the docked anchor is STABLE: it does NOT move as the AOI reprojects (sheetTopPx only)", () => {
    stubMobile(800);
    // First a small AOI -> docks at bottom 308.
    const { rerender } = renderScrubber({
      aoiRect: { left: 100, top: 60, right: 180, bottom: 200 }, // 80px wide -> docked
      sheetTopPx: 500,
    });
    const bottom1 = screen.getByTestId("grace2-sequence-scrubber").style.bottom;
    expect(bottom1).toBe("308px");
    // A heartbeat reprojects the SAME small AOI a little differently. Because the
    // docked anchor is keyed to sheetTopPx (not the aoiRect), the bottom is
    // unchanged - no per-frame jitter.
    rerender(
      <SequenceScrubber
        label="HRRR precip"
        frameLabels={FRAMES}
        activeIndex={0}
        onStep={vi.fn()}
        playing={false}
        onPlayToggle={vi.fn()}
        aoiRect={{ left: 130, top: 90, right: 205, bottom: 240 }} // jittered, still ~75px
        sheetTopPx={500}
      />,
    );
    expect(screen.getByTestId("grace2-sequence-scrubber").style.bottom).toBe(
      "308px",
    );
  });

  it("tracks the sheet: a HIGHER sheet top (expanded) lifts the docked scrubber further up", () => {
    stubMobile(800);
    // Collapsed sheet: top edge at 700, small AOI -> docked -> bottom = 108.
    const { rerender } = renderScrubber({
      aoiRect: { left: 100, top: 60, right: 200, bottom: 200 }, // 100px wide -> docked
      sheetTopPx: 700,
    });
    expect(screen.getByTestId("grace2-sequence-scrubber").style.bottom).toBe(
      "108px",
    );
    // Expanded sheet: top edge rises to 300 -> bottom = 800 - 300 + 8 = 508.
    rerender(
      <SequenceScrubber
        label="HRRR precip"
        frameLabels={FRAMES}
        activeIndex={0}
        onStep={vi.fn()}
        playing={false}
        onPlayToggle={vi.fn()}
        aoiRect={{ left: 100, top: 60, right: 200, bottom: 200 }}
        sheetTopPx={300}
      />,
    );
    expect(screen.getByTestId("grace2-sequence-scrubber").style.bottom).toBe(
      "508px",
    );
  });

  it("DOCKS the AOI-less case to the sheet top at full width", () => {
    stubMobile(800);
    renderScrubber({ sheetTopPx: 500 }); // no aoiRect -> always docked
    const el = screen.getByTestId("grace2-sequence-scrubber");
    expect(el.style.bottom).toBe("308px");
    expect(el.getAttribute("data-dock-mode")).toBe("docked");
  });
});

// SCRUBBER DOCK RULE - the SNAP half (NATE 2026-06-24): a WIDE-enough AOI snaps
// the scrubber to the bbox (width = on-screen width, centered) instead of
// docking, with the "can't pass the text box" clamp keeping it above the chat
// sheet top. Hysteresis: once snapped, it stays snapped until the bbox shrinks
// below the enter threshold; once docked, it stays docked until it grows past
// the (wider) exit threshold.
describe("SequenceScrubber - mobile SNAPS to a wide-enough AOI bbox (dock rule)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function stubMobile(innerHeight: number): void {
    vi.stubGlobal(
      "matchMedia",
      vi.fn().mockReturnValue({
        matches: true,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
      }),
    );
    vi.stubGlobal("innerHeight", innerHeight);
  }

  it("SNAPS (centered on the AOI, bbox width) when the AOI is wide enough, with a high sheet top clearing the composer", () => {
    stubMobile(800);
    // A 400px-wide AOI (>= exit threshold) high on screen (bottom 200) with the
    // sheet collapsed low (top 760) -> snaps to the AOI: centered, width = 400.
    renderScrubber({
      aoiRect: { left: 100, top: 60, right: 500, bottom: 200 },
      sheetTopPx: 760,
    });
    const el = screen.getByTestId("grace2-sequence-scrubber");
    // Centered on the AOI box (center 300), top-anchored just below the box.
    expect(el.style.left).toBe("300px");
    expect(el.style.width).toBe("400px");
    expect(parseFloat(el.style.top)).toBe(212); // bottom(200) + 12
  });

  it("'can't pass the text box': a wide AOI low on screen clamps its BOTTOM above the sheet top (still snapped/centered)", () => {
    stubMobile(800);
    // A 400px-wide AOI whose bottom (700) would push the pill into the composer;
    // sheet top at 500. The snapped pill clamps its bottom to 800-500+8=308 while
    // staying centered on the AOI (left 300) and at the bbox width (400).
    renderScrubber({
      aoiRect: { left: 100, top: 400, right: 500, bottom: 700 },
      sheetTopPx: 500,
    });
    const el = screen.getByTestId("grace2-sequence-scrubber");
    expect(el.style.left).toBe("300px");
    expect(el.style.width).toBe("400px");
    expect(el.style.bottom).toBe("308px");
    expect(el.style.top).toBe(""); // bottom-anchored (clamped), not a low top
  });

  it("HYSTERESIS: once snapped it stays snapped through the dead band (240 -> 210), and docks only below 200", () => {
    stubMobile(800);
    // Start wide (400) -> snapped.
    const { rerender } = renderScrubber({
      aoiRect: { left: 100, top: 60, right: 500, bottom: 200 }, // 400 wide
      sheetTopPx: 760,
    });
    expect(screen.getByTestId("grace2-sequence-scrubber").style.width).toBe("400px");
    const wide = (w: number) => (
      <SequenceScrubber
        label="HRRR precip"
        frameLabels={FRAMES}
        activeIndex={0}
        onStep={vi.fn()}
        playing={false}
        onPlayToggle={vi.fn()}
        aoiRect={{ left: 100, top: 60, right: 100 + w, bottom: 200 }}
        sheetTopPx={760}
      />
    );
    // Shrink into the dead band (210px, between enter=200 and exit=240): the
    // latch keeps it SNAPPED (width clamps to the 200px tappable minimum, but it
    // is NOT docked - still a numeric px width, not a 100vw calc).
    rerender(wide(210));
    const mid = screen.getByTestId("grace2-sequence-scrubber");
    expect(mid.style.width).toBe("210px");
    expect(mid.getAttribute("data-dock-mode")).toBe("snapped");
    // Shrink below the enter threshold (180px) -> now it docks (full-width).
    rerender(wide(180));
    const docked = screen.getByTestId("grace2-sequence-scrubber");
    expect(docked.getAttribute("data-dock-mode")).toBe("docked");
  });
});
