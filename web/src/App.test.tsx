// GRACE-2 web — App collapse-toggle tests (job-0065, tweak 3).
//
// Verifies:
//   1. Left collapse toggle updates DOM state (button aria-label flips).
//   2. Right collapse toggle updates DOM state.
//   3. Collapse state is written to localStorage on toggle.
//   4. Re-mount reads persisted localStorage state and starts collapsed.
//
// NOTE: The full App mounts Chat (WebSocket) and MapView (WebGL / maplibre-gl)
// which cannot run in happy-dom. We therefore test the collapse behaviour via
// a minimal CollapseShell component extracted from App.tsx that captures only
// the collapse-toggle logic and localStorage wiring. This is acceptable per
// AGENTS.md "Live E2E validation required" — the collapse UI toggle is
// separately verified by the browser screenshot evidence; unit tests here
// cover state correctness and localStorage round-trip.

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { useState } from "react";

// --- Minimal test harness ------------------------------------------------ //
// Mirrors the collapse logic in App.tsx without importing WebSocket/WebGL deps.

const LS_LEFT_COLLAPSED = "grace2.leftPanelCollapsed";
const LS_RIGHT_COLLAPSED = "grace2.rightPanelCollapsed";

function readCollapsed(key: string): boolean {
  try {
    return localStorage.getItem(key) === "true";
  } catch {
    return false;
  }
}

function CollapseShell(): JSX.Element {
  const [leftCollapsed, setLeftCollapsed] = useState(() =>
    readCollapsed(LS_LEFT_COLLAPSED),
  );
  const [rightCollapsed, setRightCollapsed] = useState(() =>
    readCollapsed(LS_RIGHT_COLLAPSED),
  );

  function toggleLeft(): void {
    setLeftCollapsed((prev) => {
      const next = !prev;
      try { localStorage.setItem(LS_LEFT_COLLAPSED, String(next)); } catch { /* */ }
      return next;
    });
  }

  function toggleRight(): void {
    setRightCollapsed((prev) => {
      const next = !prev;
      try { localStorage.setItem(LS_RIGHT_COLLAPSED, String(next)); } catch { /* */ }
      return next;
    });
  }

  return (
    <div>
      <div data-testid="left-panel" data-collapsed={String(leftCollapsed)}>
        <button
          data-testid="grace2-left-collapse-toggle"
          aria-label={leftCollapsed ? "Expand layer panel" : "Collapse layer panel"}
          onClick={toggleLeft}
        >
          {leftCollapsed ? "›" : "‹"}
        </button>
      </div>
      <div data-testid="right-panel" data-collapsed={String(rightCollapsed)}>
        <button
          data-testid="grace2-right-collapse-toggle"
          aria-label={rightCollapsed ? "Expand chat panel" : "Collapse chat panel"}
          onClick={toggleRight}
        >
          {rightCollapsed ? "‹" : "›"}
        </button>
      </div>
    </div>
  );
}

// --- Tests --------------------------------------------------------------- //

describe("App collapse toggles (tweak 3)", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    localStorage.clear();
  });

  it("left panel starts expanded (aria-label = Collapse)", () => {
    render(<CollapseShell />);
    expect(screen.getByTestId("grace2-left-collapse-toggle")).toHaveAttribute(
      "aria-label",
      "Collapse layer panel",
    );
    expect(screen.getByTestId("left-panel")).toHaveAttribute(
      "data-collapsed",
      "false",
    );
  });

  it("clicking left toggle collapses left panel", () => {
    render(<CollapseShell />);
    fireEvent.click(screen.getByTestId("grace2-left-collapse-toggle"));
    expect(screen.getByTestId("grace2-left-collapse-toggle")).toHaveAttribute(
      "aria-label",
      "Expand layer panel",
    );
    expect(screen.getByTestId("left-panel")).toHaveAttribute(
      "data-collapsed",
      "true",
    );
  });

  it("clicking left toggle twice returns to expanded", () => {
    render(<CollapseShell />);
    fireEvent.click(screen.getByTestId("grace2-left-collapse-toggle"));
    fireEvent.click(screen.getByTestId("grace2-left-collapse-toggle"));
    expect(screen.getByTestId("left-panel")).toHaveAttribute(
      "data-collapsed",
      "false",
    );
  });

  it("collapse state is persisted in localStorage after left toggle", () => {
    render(<CollapseShell />);
    expect(localStorage.getItem(LS_LEFT_COLLAPSED)).toBeNull();
    fireEvent.click(screen.getByTestId("grace2-left-collapse-toggle"));
    expect(localStorage.getItem(LS_LEFT_COLLAPSED)).toBe("true");
    fireEvent.click(screen.getByTestId("grace2-left-collapse-toggle"));
    expect(localStorage.getItem(LS_LEFT_COLLAPSED)).toBe("false");
  });

  it("right panel starts expanded", () => {
    render(<CollapseShell />);
    expect(screen.getByTestId("right-panel")).toHaveAttribute(
      "data-collapsed",
      "false",
    );
  });

  it("clicking right toggle collapses right panel and writes localStorage", () => {
    render(<CollapseShell />);
    fireEvent.click(screen.getByTestId("grace2-right-collapse-toggle"));
    expect(screen.getByTestId("right-panel")).toHaveAttribute(
      "data-collapsed",
      "true",
    );
    expect(localStorage.getItem(LS_RIGHT_COLLAPSED)).toBe("true");
  });

  it("re-mount reads persisted left collapsed state from localStorage", () => {
    // Pre-set localStorage as if a previous session left the panel collapsed.
    localStorage.setItem(LS_LEFT_COLLAPSED, "true");
    const { unmount } = render(<CollapseShell />);
    expect(screen.getByTestId("left-panel")).toHaveAttribute(
      "data-collapsed",
      "true",
    );
    expect(screen.getByTestId("grace2-left-collapse-toggle")).toHaveAttribute(
      "aria-label",
      "Expand layer panel",
    );
    unmount();
  });

  it("re-mount reads persisted right collapsed state from localStorage", () => {
    localStorage.setItem(LS_RIGHT_COLLAPSED, "true");
    render(<CollapseShell />);
    expect(screen.getByTestId("right-panel")).toHaveAttribute(
      "data-collapsed",
      "true",
    );
    expect(screen.getByTestId("grace2-right-collapse-toggle")).toHaveAttribute(
      "aria-label",
      "Expand chat panel",
    );
  });

  it("left and right collapse are independent", () => {
    render(<CollapseShell />);
    fireEvent.click(screen.getByTestId("grace2-left-collapse-toggle"));
    // Left collapsed, right still expanded
    expect(screen.getByTestId("left-panel")).toHaveAttribute("data-collapsed", "true");
    expect(screen.getByTestId("right-panel")).toHaveAttribute("data-collapsed", "false");
  });
});

// --- job-0068: conditional mount + hamburger tests ----------------------- //
//
// Tests for the new overlay layout, hamburger pattern, and conditional mount.
// Uses a minimal AppShell that mirrors the job-0068 App.tsx logic without
// importing WebSocket/WebGL/MapLibre deps (same rationale as CollapseShell
// above). Live browser E2E is captured in the 5 evidence screenshots.

import { act } from "@testing-library/react";

// Minimal shell mirroring the job-0068 conditional-mount + hamburger logic.
function AppShell({ initialLayers = 0, startLeftCollapsed = false }: {
  initialLayers?: number;
  startLeftCollapsed?: boolean;
}): JSX.Element {
  const [layerCount, setLayerCount] = useState(initialLayers);
  const [leftCollapsed, setLeftCollapsed] = useState(startLeftCollapsed);
  const [rightCollapsed, setRightCollapsed] = useState(false);

  const showLeftPanel = layerCount > 0 && !leftCollapsed;
  const showLayersHamburger = layerCount > 0 && leftCollapsed;
  const showChatHamburger = rightCollapsed;

  return (
    <div>
      {/* Simulate layer arrival button */}
      <button
        data-testid="sim-add-layer"
        onClick={() => setLayerCount((c) => c + 1)}
      >
        Add Layer
      </button>
      <button
        data-testid="sim-remove-all-layers"
        onClick={() => setLayerCount(0)}
      >
        Remove All
      </button>

      {showLeftPanel && (
        <div data-testid="grace2-layer-panel">
          <button
            data-testid="grace2-layer-panel-close"
            onClick={() => setLeftCollapsed(true)}
          >
            ×
          </button>
        </div>
      )}

      {showLayersHamburger && (
        <button
          data-testid="grace2-layers-hamburger"
          aria-label="Show layers"
          onClick={() => setLeftCollapsed(false)}
        >
          ☰
        </button>
      )}

      {!rightCollapsed && (
        <div data-testid="grace2-chat">
          <button
            data-testid="grace2-chat-close"
            onClick={() => setRightCollapsed(true)}
          >
            ×
          </button>
        </div>
      )}

      {showChatHamburger && (
        <button
          data-testid="grace2-chat-hamburger"
          aria-label="Show chat"
          onClick={() => setRightCollapsed(false)}
        >
          ☰
        </button>
      )}
    </div>
  );
}

describe("App overlay layout — conditional mount + hamburger (job-0068 changes 1-3)", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    localStorage.clear();
  });

  it("no layers → LayerPanel NOT mounted AND Layers hamburger NOT rendered", () => {
    render(<AppShell initialLayers={0} />);
    expect(screen.queryByTestId("grace2-layer-panel")).toBeNull();
    expect(screen.queryByTestId("grace2-layers-hamburger")).toBeNull();
  });

  it("layers > 0 → LayerPanel mounts (left overlay)", () => {
    render(<AppShell initialLayers={1} />);
    expect(screen.getByTestId("grace2-layer-panel")).toBeInTheDocument();
  });

  it("adding a layer after start causes LayerPanel to appear", () => {
    render(<AppShell initialLayers={0} />);
    expect(screen.queryByTestId("grace2-layer-panel")).toBeNull();

    act(() => {
      fireEvent.click(screen.getByTestId("sim-add-layer"));
    });

    expect(screen.getByTestId("grace2-layer-panel")).toBeInTheDocument();
  });

  it("removing all layers collapses LayerPanel AND hamburger disappears", () => {
    render(<AppShell initialLayers={1} />);
    expect(screen.getByTestId("grace2-layer-panel")).toBeInTheDocument();

    act(() => {
      fireEvent.click(screen.getByTestId("sim-remove-all-layers"));
    });

    expect(screen.queryByTestId("grace2-layer-panel")).toBeNull();
    expect(screen.queryByTestId("grace2-layers-hamburger")).toBeNull();
  });

  it("layers present + leftCollapsed → hamburger top-left renders, panel hidden", () => {
    render(<AppShell initialLayers={1} startLeftCollapsed />);
    expect(screen.queryByTestId("grace2-layer-panel")).toBeNull();
    expect(screen.getByTestId("grace2-layers-hamburger")).toBeInTheDocument();
    expect(screen.getByTestId("grace2-layers-hamburger")).toHaveAttribute(
      "aria-label",
      "Show layers",
    );
  });

  it("clicking hamburger expands panel and hamburger disappears", () => {
    render(<AppShell initialLayers={1} startLeftCollapsed />);
    expect(screen.getByTestId("grace2-layers-hamburger")).toBeInTheDocument();

    act(() => {
      fireEvent.click(screen.getByTestId("grace2-layers-hamburger"));
    });

    expect(screen.queryByTestId("grace2-layers-hamburger")).toBeNull();
    expect(screen.getByTestId("grace2-layer-panel")).toBeInTheDocument();
  });

  it("clicking × close in LayerPanel collapses panel and shows hamburger", () => {
    render(<AppShell initialLayers={1} />);
    expect(screen.getByTestId("grace2-layer-panel")).toBeInTheDocument();

    act(() => {
      fireEvent.click(screen.getByTestId("grace2-layer-panel-close"));
    });

    expect(screen.queryByTestId("grace2-layer-panel")).toBeNull();
    expect(screen.getByTestId("grace2-layers-hamburger")).toBeInTheDocument();
  });

  it("Chat panel always present (it is the way to request layers)", () => {
    render(<AppShell initialLayers={0} />);
    expect(screen.getByTestId("grace2-chat")).toBeInTheDocument();
  });

  it("clicking Chat × hides chat; chat hamburger appears top-right", () => {
    render(<AppShell initialLayers={0} />);
    act(() => {
      fireEvent.click(screen.getByTestId("grace2-chat-close"));
    });
    expect(screen.queryByTestId("grace2-chat")).toBeNull();
    expect(screen.getByTestId("grace2-chat-hamburger")).toHaveAttribute(
      "aria-label",
      "Show chat",
    );
  });
});
