// GRACE-2 web — SequenceScrubber unit tests (sequential-layer-grouping).
//
// STATIC SCRUBBER (NATE 2026-06-26): the scrubber is a STATIC bottom-of-screen
// pill (play/pause + prev/next + slider + x/N). It no longer snaps to the AOI
// bbox or docks to the chat sheet - "fighting all the movement around is killing
// me, put it at the bottom of the screen." It is pure presentation: all frame
// state + the step callback come in as props. It portals to document.body so its
// fixed bottom-center placement resolves against the viewport (not the
// LayerPanel's stacking context). The ONLY position input is the DESKTOP
// side-panel geometry (so it centers in the open map gutter, never under a side
// panel) - a stable shift only on a panel toggle, never per animation frame.

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

function renderScrubber(
  overrides: Partial<React.ComponentProps<typeof SequenceScrubber>> = {},
) {
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
    expect(screen.getByTestId("scrubber-frame-label")).toHaveTextContent("2/3");
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

  it("renders a play/pause button on the scrubber", () => {
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
});

// STATIC POSITION (NATE 2026-06-26): the scrubber is pinned at the BOTTOM of the
// screen - bottom-center, anchored from the bottom, centered with translateX.
// No AOI-bbox snap, no dock latch, no width-tracks-bbox. The slider HANDLE
// position tracks the live frame index (so it moves during autoplay).
describe("SequenceScrubber — static bottom placement", () => {
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

  it("desktop: anchors from the viewport BOTTOM (24px), centered, no top anchor", () => {
    stubPlatform(false);
    renderScrubber();
    const el = screen.getByTestId("grace2-sequence-scrubber");
    expect(el.style.bottom).toBe("24px");
    expect(el.style.top).toBe(""); // never a top anchor - always bottom-pinned
    expect(el.style.transform).toBe("translateX(-50%)");
  });

  it("desktop: does NOT track an AOI bbox - the same pill regardless of any prop churn", () => {
    stubPlatform(false);
    // Re-rendering with different active frames must not move the pill (static).
    const { rerender } = renderScrubber({ activeIndex: 0 });
    const bottom0 = screen.getByTestId("grace2-sequence-scrubber").style.bottom;
    rerender(
      <SequenceScrubber
        label="HRRR precip"
        frameLabels={FRAMES}
        activeIndex={2}
        onStep={() => {}}
        playing
        onPlayToggle={() => {}}
      />,
    );
    expect(screen.getByTestId("grace2-sequence-scrubber").style.bottom).toBe(
      bottom0,
    );
  });

  it("the slider value tracks the live frame index (handle moves on autoplay)", () => {
    stubPlatform(false);
    const { rerender } = renderScrubber({ activeIndex: 0 });
    const slider = screen.getByTestId("scrubber-slider") as HTMLInputElement;
    expect(slider.value).toBe("0");
    // Controller advances a frame (autoplay tick) -> parent re-renders with the
    // new activeIndex -> the controlled slider thumb moves.
    rerender(
      <SequenceScrubber
        label="HRRR precip"
        frameLabels={FRAMES}
        activeIndex={1}
        onStep={() => {}}
        playing
        onPlayToggle={() => {}}
      />,
    );
    expect((screen.getByTestId("scrubber-slider") as HTMLInputElement).value).toBe(
      "1",
    );
    expect(screen.getByTestId("scrubber-frame-label")).toHaveTextContent("2/3");
  });
});

// MOBILE Z-ORDER + bottom clearance (NATE 2026-06-22/26): on mobile the chat is a
// bottom sheet at zIndex 32; the scrubber sits UNDERNEATH it (z 31) and anchors
// above the composer via the safe-area-inclusive bottom offset.
describe("SequenceScrubber — mobile placement vs the chat sheet", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function stubMobile(mobile: boolean): void {
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

  it("the mobile bottom-offset constant reserves the safe-area inset + a positive clearance", () => {
    expect(SCRUBBER_MOBILE_SHEET_CLEARANCE_PX).toBeGreaterThan(24);
    expect(SCRUBBER_MOBILE_BOTTOM_CSS).toBe(
      `calc(env(safe-area-inset-bottom) + ${SCRUBBER_MOBILE_SHEET_CLEARANCE_PX}px)`,
    );
    expect(SCRUBBER_MOBILE_BOTTOM_CSS).toContain("env(safe-area-inset-bottom)");
  });

  it("renders BELOW the mobile chat sheet (z < 32) when the viewport is mobile", () => {
    stubMobile(true);
    renderScrubber();
    const el = screen.getByTestId("grace2-sequence-scrubber");
    expect(Number(el.style.zIndex)).toBeLessThan(32);
    expect(el.style.zIndex).toBe("31");
    // Anchored above the composer: NOT the bare desktop 24px (jsdom drops the
    // calc(env(...)) bottom to "", so we assert it is not the desktop value).
    expect(el.style.bottom).not.toBe("24px");
    expect(el.style.top).toBe("");
  });

  it("keeps the original higher z on desktop (matchMedia reports non-mobile)", () => {
    stubMobile(false);
    renderScrubber();
    const el = screen.getByTestId("grace2-sequence-scrubber");
    expect(el.style.zIndex).toBe("51");
  });
});

// DESKTOP GUTTER CENTERING (the one stable position input): the static pill
// centers in the OPEN map gutter (between the left rail and the right chat
// panel) so it never sits under a side panel. This changes only on a panel
// toggle, never per animation frame.
describe("SequenceScrubber — desktop gutter centering", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function stubDesktop(): void {
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
  }

  it("centers in the viewport when no panels are open (full-width gutter)", () => {
    stubDesktop();
    renderScrubber();
    const el = screen.getByTestId("grace2-sequence-scrubber");
    const expectedCx = Math.round(window.innerWidth / 2);
    expect(el.style.left).toBe(`${expectedCx}px`);
    expect(el.style.transform).toBe("translateX(-50%)");
  });

  it("clamps its center to the open gutter so it never runs under the side panels", () => {
    stubDesktop();
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

  it("uses a fixed comfortable width (not bbox-tracked), clamped to the gutter", () => {
    stubDesktop();
    renderScrubber(); // no panels -> full gutter, width caps at the default band.
    const el = screen.getByTestId("grace2-sequence-scrubber");
    // Default band is 420px (no AOI bbox tracking).
    expect(el.style.width).toBe("420px");
  });
});

// The auto-advance INTERVAL lives in the module-level AnimationController, never
// in the scrubber. The scrubber must NOT run its own timer, otherwise frames
// would advance twice as fast (controller tick + scrubber tick).
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

// The x/N counter must stay CONTAINED within the pill bounds (box-sizing +
// overflow:hidden) so it never leaks past the right edge.
describe("SequenceScrubber — x/N counter contained within the pill", () => {
  it("clips overflow so children stay within the rounded pill", () => {
    renderScrubber();
    const el = screen.getByTestId("grace2-sequence-scrubber");
    expect(el.style.overflow).toBe("hidden");
    expect(el.style.boxSizing).toBe("border-box");
  });

  it("the x/N counter is a child of the pill (not floated outside it)", () => {
    renderScrubber({ activeIndex: 1 });
    const el = screen.getByTestId("grace2-sequence-scrubber");
    const counter = screen.getByTestId("scrubber-frame-label");
    expect(el.contains(counter)).toBe(true);
    expect(counter).toHaveTextContent("2/3");
  });
});
