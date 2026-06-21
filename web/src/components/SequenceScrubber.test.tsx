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

// Item d (SCALE WITH AOI, NATE 2026-06-20) — the scrubber's footprint scales
// with the AOI bbox's on-screen size so it doesn't dwarf a tiny zoomed-out box.
describe("SequenceScrubber — scales with the AOI on-screen size (item d)", () => {
  it("a tiny on-screen AOI yields a SMALLER min/max width than a large one", () => {
    const tiny = { left: 100, top: 100, right: 140, bottom: 140 }; // 40px box
    const huge = { left: 0, top: 0, right: 1400, bottom: 1400 };
    const { rerender } = renderScrubber({ aoiRect: tiny });
    const tinyEl = screen.getByTestId("grace2-sequence-scrubber");
    const tinyMin = parseFloat(tinyEl.style.minWidth);
    const tinyMax = parseFloat(tinyEl.style.maxWidth);
    rerender(
      <SequenceScrubber
        label="HRRR precip"
        frameLabels={FRAMES}
        activeIndex={0}
        onStep={() => {}}
        playing={false}
        onPlayToggle={() => {}}
        aoiRect={huge}
      />,
    );
    const hugeEl = screen.getByTestId("grace2-sequence-scrubber");
    expect(tinyMin).toBeLessThan(parseFloat(hugeEl.style.minWidth));
    expect(tinyMax).toBeLessThan(parseFloat(hugeEl.style.maxWidth));
    // Stays within a usable band (clamped — never absurdly tiny / huge).
    expect(tinyMin).toBeGreaterThan(100);
    expect(parseFloat(hugeEl.style.maxWidth)).toBeLessThan(900);
  });

  it("uses the natural (1.0) footprint when there is no AOI rect", () => {
    renderScrubber();
    const el = screen.getByTestId("grace2-sequence-scrubber");
    // Natural base sizes (scale 1.0): minWidth 220, maxWidth 480.
    expect(el.style.minWidth).toBe("220px");
    expect(el.style.maxWidth).toBe("480px");
  });
});
