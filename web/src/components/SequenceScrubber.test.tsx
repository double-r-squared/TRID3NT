// GRACE-2 web — SequenceScrubber unit tests (sequential-layer-grouping).
//
// The scrubber is the bottom-center map overlay that steps the active
// sequential group's frames (slider + LEFT/RIGHT + play/pause). It is pure
// presentation — all frame state + the step callback come in as props. It
// portals to document.body so its fixed bottom-center placement resolves
// against the viewport (not the LayerPanel's stacking context).

import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { render, screen, fireEvent, cleanup, act } from "@testing-library/react";
import { SequenceScrubber, wrapIndex } from "./SequenceScrubber";

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

  it("falls back to viewport bottom-center when aoiRect is absent (item 3)", () => {
    renderScrubber();
    const el = screen.getByTestId("grace2-sequence-scrubber");
    expect(el).toHaveStyle({ left: "50%", bottom: "24px" });
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

  it("CLAMPS the AOI-pinned top so the scrubber clears the chat sheet", () => {
    stubMobile();
    // AOI bottom at 580 -> unclamped top would be 592 (past the 600 viewport,
    // inside the chat sheet). The clamp must pull it up to clear the sheet.
    renderScrubber({ aoiRect: { left: 100, top: 400, right: 300, bottom: 580 } });
    const el = screen.getByTestId("grace2-sequence-scrubber");
    const top = parseFloat(el.style.top);
    // maxTop = 600 - 116 (clearance) - 44 (approx height) = 440. The scrubber's
    // top is clamped to <= maxTop, well above the chat sheet band.
    expect(top).toBeLessThanOrEqual(440);
    // It is centered on the AOI horizontally regardless.
    expect(el.style.left).toBe("200px");
  });

  it("does NOT clamp when the AOI sits high enough (top already clears the sheet)", () => {
    stubMobile();
    // AOI bottom at 100 -> top = 112, far above maxTop(440): unchanged.
    renderScrubber({ aoiRect: { left: 100, top: 40, right: 300, bottom: 100 } });
    const el = screen.getByTestId("grace2-sequence-scrubber");
    expect(parseFloat(el.style.top)).toBe(112);
  });

  it("AOI-less fallback anchors ABOVE the chat sheet on mobile (not bottom:24)", () => {
    stubMobile();
    renderScrubber(); // no aoiRect
    const el = screen.getByTestId("grace2-sequence-scrubber");
    // Mobile fallback lifts the bottom anchor by the sheet clearance (116px),
    // so the scrubber sits above the composer instead of behind it.
    expect(el.style.bottom).toBe("116px");
  });
});
