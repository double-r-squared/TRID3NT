// GRACE-2 web — MobileDrawer tests (job-0278, mobile-friendly UI).
//
// Pins the drawer's open/close contract:
//   - closed → NOTHING in the DOM (map stays unobstructed);
//   - open → backdrop + panel + children render;
//   - backdrop tap closes; clicks inside the panel do NOT close;
//   - touch-target class + a11y wiring on the ☰ opener (44px, aria-expanded).
//
// The drawer is a pure presentational shell — App.tsx owns the open state —
// so a small stateful harness exercises the full open → close cycle the way
// App wires it.

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { useState } from "react";
import { MobileDrawer, MobileDrawerButton } from "./MobileDrawer";

afterEach(() => cleanup());

describe("MobileDrawerButton", () => {
  it("renders a 44px touch target with a11y wiring", () => {
    render(<MobileDrawerButton open={false} onClick={vi.fn()} />);
    const btn = screen.getByTestId("grace2-mobile-drawer-button");
    expect(btn).toHaveAttribute("aria-label", "Open cases and layers");
    expect(btn).toHaveAttribute("aria-expanded", "false");
    expect(btn).toHaveAttribute("aria-controls", "grace2-mobile-drawer");
    expect(btn.style.width).toBe("44px");
    expect(btn.style.height).toBe("44px");
  });

  it("invokes onClick", () => {
    const onClick = vi.fn();
    render(<MobileDrawerButton open={false} onClick={onClick} />);
    fireEvent.click(screen.getByTestId("grace2-mobile-drawer-button"));
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});

describe("MobileDrawer", () => {
  it("renders nothing when closed", () => {
    render(
      <MobileDrawer open={false} onClose={vi.fn()}>
        <span data-testid="drawer-child" />
      </MobileDrawer>,
    );
    expect(screen.queryByTestId("grace2-mobile-drawer")).toBeNull();
    expect(screen.queryByTestId("grace2-mobile-drawer-backdrop")).toBeNull();
    expect(screen.queryByTestId("drawer-child")).toBeNull();
  });

  it("renders backdrop + panel + children when open", () => {
    render(
      <MobileDrawer open={true} onClose={vi.fn()}>
        <span data-testid="drawer-child" />
      </MobileDrawer>,
    );
    expect(screen.getByTestId("grace2-mobile-drawer-backdrop")).toBeTruthy();
    const drawer = screen.getByTestId("grace2-mobile-drawer");
    expect(drawer).toBeTruthy();
    expect(screen.getByTestId("drawer-child")).toBeTruthy();
    // Touch-target CSS scope (global.css bump applies only inside this class).
    expect(drawer.className).toContain("grace2-mobile-touch");
    expect(drawer).toHaveAttribute("role", "dialog");
  });

  // job-0284 — map-centric pass: the drawer has NO panel surface (children
  // float as their own translucent cards over the map) and the backdrop is
  // an INVISIBLE full-screen hit area (no dim — the map stays visible).
  it("job-0284: transparent backdrop + surfaceless panel (components float)", () => {
    render(
      <MobileDrawer open={true} onClose={vi.fn()}>
        <span />
      </MobileDrawer>,
    );
    const backdrop = screen.getByTestId("grace2-mobile-drawer-backdrop");
    expect(backdrop.style.background).toBe("transparent");
    const drawer = screen.getByTestId("grace2-mobile-drawer");
    expect(drawer.style.background).toBe("transparent");
    expect(drawer.style.border).toBe("");
    expect(drawer.style.boxShadow).toBe("");
  });

  it("job-0284: NO backdrop-filter — the drawer hosts position:fixed children (ConfirmationDialog)", () => {
    // A non-none backdrop-filter would make the drawer the containing block
    // for position:fixed descendants, trapping CasesPanel's delete
    // ConfirmationDialog inside the 320px column instead of centering it on
    // the viewport (job-0283 hazard). Translucency must stay rgba/alpha-only.
    render(
      <MobileDrawer open={true} onClose={vi.fn()}>
        <span />
      </MobileDrawer>,
    );
    const drawer = screen.getByTestId("grace2-mobile-drawer");
    expect(drawer.style.backdropFilter || "").toBe("");
    expect(drawer.style.filter || "").toBe("");
    expect(drawer.style.transform || "").toBe("");
    expect(drawer.style.willChange || "").toBe("");
  });

  it("backdrop tap calls onClose; taps inside the panel do not", () => {
    const onClose = vi.fn();
    render(
      <MobileDrawer open={true} onClose={onClose}>
        <button data-testid="inner-button">inner</button>
      </MobileDrawer>,
    );
    fireEvent.click(screen.getByTestId("inner-button"));
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("grace2-mobile-drawer-backdrop"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  // job-0322 F52 — the transparent 320px column sits ABOVE the z=40 backdrop,
  // so gutter taps (inside the column, outside a floating card) used to land
  // on the column, which had no close handler, and the drawer stayed open.
  // The column now closes when tapped DIRECTLY (e.target === e.currentTarget).
  it("F52: a tap on the drawer-column gutter calls onClose", () => {
    const onClose = vi.fn();
    render(
      <MobileDrawer open={true} onClose={onClose}>
        <button data-testid="inner-button">inner</button>
      </MobileDrawer>,
    );
    // A direct tap on the column itself (the empty gutter) — target IS the
    // column element, so the guard matches and onClose fires.
    fireEvent.click(screen.getByTestId("grace2-mobile-drawer"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("F52: a tap on a child interactive card does NOT call onClose by itself", () => {
    const onClose = vi.fn();
    const onInner = vi.fn();
    render(
      <MobileDrawer open={true} onClose={onClose}>
        {/* A floating card surface with a nested interactive element, the way
            CasesPanel mounts its Case rows / row buttons inside the column. */}
        <div data-testid="floating-card">
          <button data-testid="card-button" onClick={onInner}>
            select
          </button>
        </div>
      </MobileDrawer>,
    );
    // Tap the card surface (a child of the column) — e.target is the card,
    // not the column, so the guard rejects it and the drawer stays open.
    fireEvent.click(screen.getByTestId("floating-card"));
    expect(onClose).not.toHaveBeenCalled();
    // Tap the nested button — its click bubbles to the column, but e.target is
    // the button, so onClose still does not fire; the inner handler runs.
    fireEvent.click(screen.getByTestId("card-button"));
    expect(onInner).toHaveBeenCalledTimes(1);
    expect(onClose).not.toHaveBeenCalled();
  });

  it("full open → close cycle through a stateful parent (App wiring shape)", () => {
    function Harness(): JSX.Element {
      const [open, setOpen] = useState(false);
      return (
        <>
          {!open && (
            <MobileDrawerButton open={open} onClick={() => setOpen(true)} />
          )}
          <MobileDrawer open={open} onClose={() => setOpen(false)}>
            <button
              data-testid="fake-case-row"
              onClick={() => setOpen(false)}
            >
              Case row
            </button>
          </MobileDrawer>
        </>
      );
    }
    render(<Harness />);
    // Hidden by default.
    expect(screen.queryByTestId("grace2-mobile-drawer")).toBeNull();
    // ☰ opens.
    fireEvent.click(screen.getByTestId("grace2-mobile-drawer-button"));
    expect(screen.getByTestId("grace2-mobile-drawer")).toBeTruthy();
    expect(screen.queryByTestId("grace2-mobile-drawer-button")).toBeNull();
    // Selecting a Case (App closes the drawer in onSelect) dismisses it.
    fireEvent.click(screen.getByTestId("fake-case-row"));
    expect(screen.queryByTestId("grace2-mobile-drawer")).toBeNull();
    // Re-open then dismiss via backdrop.
    fireEvent.click(screen.getByTestId("grace2-mobile-drawer-button"));
    fireEvent.click(screen.getByTestId("grace2-mobile-drawer-backdrop"));
    expect(screen.queryByTestId("grace2-mobile-drawer")).toBeNull();
  });
});
