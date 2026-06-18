// GRACE-2 web — WakeOverlay tests (auto-stop/wake UIUX, NATE 2026-06-17).
//
// Verifies the "Wake up agent" overlay phase machine + interactions:
//   - "hidden"  → renders nothing.
//   - "asleep"  → tap-to-wake rectangle; click / Enter / Space fire onWake.
//   - "waking"  → "Waking up…" status + upward-wave; NOT a button; tap is inert.
//   - fade-out  → flipping to "hidden" keeps the node briefly mounted (opacity
//                 transition) then unmounts.
//   - prefers-reduced-motion → no animation declared on the rect / wave; the
//                 overlay still communicates state via text.

import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, fireEvent, cleanup, act } from "@testing-library/react";
import { WakeOverlay, WAKE_FADE_MS } from "./WakeOverlay";

function mockReducedMotion(reduced: boolean): void {
  // happy-dom has no matchMedia by default; install a minimal stub.
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    configurable: true,
    value: (query: string) => ({
      matches: reduced && query.includes("prefers-reduced-motion"),
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
      onchange: null,
    }),
  });
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("WakeOverlay — phase rendering", () => {
  it("renders nothing in the 'hidden' phase", () => {
    mockReducedMotion(false);
    render(<WakeOverlay phase="hidden" onWake={() => {}} />);
    expect(screen.queryByTestId("wake-overlay")).toBeNull();
  });

  it("shows the tap-to-wake rectangle in 'asleep'", () => {
    mockReducedMotion(false);
    render(<WakeOverlay phase="asleep" onWake={() => {}} />);
    const overlay = screen.getByTestId("wake-overlay");
    expect(overlay).toHaveAttribute("data-phase", "asleep");
    expect(screen.getByText("Wake up agent")).toBeInTheDocument();
    const rect = screen.getByTestId("wake-overlay-rect");
    expect(rect).toHaveAttribute("role", "button");
    expect(rect).toHaveAttribute("tabindex", "0");
  });

  it("shows the 'Waking up…' status + upward-wave in 'waking'", () => {
    mockReducedMotion(false);
    render(<WakeOverlay phase="waking" onWake={() => {}} />);
    expect(screen.getByTestId("wake-overlay")).toHaveAttribute(
      "data-phase",
      "waking",
    );
    // Status surface, not a button.
    const rect = screen.getByTestId("wake-overlay-rect");
    expect(rect).toHaveAttribute("role", "status");
    expect(screen.getByText(/Waking up/)).toBeInTheDocument();
    expect(screen.getByTestId("wake-upward-wave")).toBeInTheDocument();
  });
});

describe("WakeOverlay — interactions", () => {
  it("fires onWake on click in 'asleep'", () => {
    mockReducedMotion(false);
    const onWake = vi.fn();
    render(<WakeOverlay phase="asleep" onWake={onWake} />);
    fireEvent.click(screen.getByTestId("wake-overlay-rect"));
    expect(onWake).toHaveBeenCalledTimes(1);
  });

  it("fires onWake on Enter and Space (keyboard)", () => {
    mockReducedMotion(false);
    const onWake = vi.fn();
    render(<WakeOverlay phase="asleep" onWake={onWake} />);
    const rect = screen.getByTestId("wake-overlay-rect");
    fireEvent.keyDown(rect, { key: "Enter" });
    fireEvent.keyDown(rect, { key: " " });
    expect(onWake).toHaveBeenCalledTimes(2);
  });

  it("does NOT fire onWake when tapped in 'waking' (already waking)", () => {
    mockReducedMotion(false);
    const onWake = vi.fn();
    render(<WakeOverlay phase="waking" onWake={onWake} />);
    fireEvent.click(screen.getByTestId("wake-overlay-rect"));
    expect(onWake).not.toHaveBeenCalled();
  });
});

describe("WakeOverlay — fade-out on 'hidden'", () => {
  it("keeps the node mounted (opacity 0) through the fade then unmounts", () => {
    mockReducedMotion(false);
    vi.useFakeTimers();
    const { rerender } = render(
      <WakeOverlay phase="waking" onWake={() => {}} />,
    );
    expect(screen.getByTestId("wake-overlay")).toBeInTheDocument();

    // Flip to hidden — overlay stays mounted at opacity 0 during the fade.
    rerender(<WakeOverlay phase="hidden" onWake={() => {}} />);
    const fading = screen.getByTestId("wake-overlay");
    expect(fading).toHaveStyle({ opacity: "0" });

    // After the fade duration it unmounts.
    act(() => {
      vi.advanceTimersByTime(WAKE_FADE_MS + 10);
    });
    expect(screen.queryByTestId("wake-overlay")).toBeNull();
  });
});

describe("WakeOverlay — prefers-reduced-motion", () => {
  it("declares no animation on the rect or wave but still shows state text", () => {
    mockReducedMotion(true);
    render(<WakeOverlay phase="waking" onWake={() => {}} />);
    const rect = screen.getByTestId("wake-overlay-rect");
    // No shimmer animation under reduced motion.
    expect(rect.style.animation === "" || rect.style.animation == null).toBe(
      true,
    );
    const wave = screen.getByTestId("wake-upward-wave");
    const bars = wave.querySelectorAll("span");
    bars.forEach((b) => {
      expect((b as HTMLElement).style.animation === "").toBe(true);
    });
    // State is still communicated.
    expect(screen.getByText(/Waking up/)).toBeInTheDocument();
  });

  it("unmounts immediately on 'hidden' under reduced motion (no lingering layer)", () => {
    mockReducedMotion(true);
    const { rerender } = render(
      <WakeOverlay phase="asleep" onWake={() => {}} />,
    );
    expect(screen.getByTestId("wake-overlay")).toBeInTheDocument();
    rerender(<WakeOverlay phase="hidden" onWake={() => {}} />);
    expect(screen.queryByTestId("wake-overlay")).toBeNull();
  });
});
