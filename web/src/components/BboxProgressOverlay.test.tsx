// GRACE-2 web - BboxProgressOverlay render tests (NATE item 1).

import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { BboxProgressOverlay } from "./BboxProgressOverlay";
import type { ScreenRect } from "../lib/legend_snap";

const RECT: ScreenRect = { left: 100, top: 100, right: 300, bottom: 260 };

describe("BboxProgressOverlay", () => {
  it("renders nothing when mode is none", () => {
    render(<BboxProgressOverlay rect={RECT} mode="none" tone="blue" />);
    expect(screen.queryByTestId("grace2-bbox-progress-overlay")).toBeNull();
  });

  it("renders nothing when there is no rect", () => {
    render(<BboxProgressOverlay rect={null} mode="fill" tone="blue" />);
    expect(screen.queryByTestId("grace2-bbox-progress-overlay")).toBeNull();
  });

  it("renders a FILL overlay anchored to the rect", () => {
    render(<BboxProgressOverlay rect={RECT} mode="fill" tone="blue" />);
    const el = screen.getByTestId("grace2-bbox-progress-overlay");
    expect(el.getAttribute("data-mode")).toBe("fill");
    expect(el.style.left).toBe("100px");
    expect(el.style.top).toBe("100px");
    expect(el.style.width).toBe("200px");
    expect(el.style.height).toBe("160px");
  });

  it("renders a blue SCAN border with a sweep when motion is allowed", () => {
    render(
      <BboxProgressOverlay
        rect={RECT}
        mode="scan"
        tone="blue"
        reducedMotionOverride={false}
      />,
    );
    const el = screen.getByTestId("grace2-bbox-progress-overlay");
    expect(el.getAttribute("data-mode")).toBe("scan");
    expect(el.getAttribute("data-tone")).toBe("blue");
    // The sweeping highlight bar is present when not reduced-motion.
    expect(screen.getByTestId("grace2-bbox-progress-sweep")).toBeInTheDocument();
  });

  it("renders a PURPLE scan border for a sim", () => {
    render(<BboxProgressOverlay rect={RECT} mode="scan" tone="purple" />);
    const el = screen.getByTestId("grace2-bbox-progress-overlay");
    expect(el.getAttribute("data-tone")).toBe("purple");
  });

  it("reduced-motion: scan degrades to a static border (no sweep bar)", () => {
    render(
      <BboxProgressOverlay
        rect={RECT}
        mode="scan"
        tone="blue"
        reducedMotionOverride={true}
      />,
    );
    const el = screen.getByTestId("grace2-bbox-progress-overlay");
    expect(el.getAttribute("data-reduced")).toBe("true");
    // No animated sweep bar under reduced motion.
    expect(screen.queryByTestId("grace2-bbox-progress-sweep")).toBeNull();
    // No CSS animation on the static border.
    expect(el.style.animation === "" || el.style.animation === undefined).toBe(true);
  });

  it("reduced-motion: fill degrades to a static tint (no animation)", () => {
    render(
      <BboxProgressOverlay
        rect={RECT}
        mode="fill"
        tone="blue"
        reducedMotionOverride={true}
      />,
    );
    const el = screen.getByTestId("grace2-bbox-progress-overlay");
    expect(el.getAttribute("data-reduced")).toBe("true");
    expect(el.style.animation === "" || el.style.animation === undefined).toBe(true);
  });

  it("never intercepts pointer events", () => {
    render(<BboxProgressOverlay rect={RECT} mode="scan" tone="blue" />);
    const el = screen.getByTestId("grace2-bbox-progress-overlay");
    expect(el.style.pointerEvents).toBe("none");
  });

  it("renders nothing for a degenerate (zero-area) rect", () => {
    render(
      <BboxProgressOverlay
        rect={{ left: 100, top: 100, right: 100, bottom: 100 }}
        mode="fill"
        tone="blue"
      />,
    );
    expect(screen.queryByTestId("grace2-bbox-progress-overlay")).toBeNull();
  });
});
