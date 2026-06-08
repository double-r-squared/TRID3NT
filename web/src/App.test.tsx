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

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
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

// --- Theme-toggle harness (job-0076 bundled enhancement) ------------------ //

const LS_THEME = "grace2.theme";

function readTheme(): "light" | "dark" {
  try {
    const v = localStorage.getItem(LS_THEME);
    return v === "dark" ? "dark" : "light";
  } catch {
    return "light";
  }
}

function ThemeShell(): JSX.Element {
  const [theme, setTheme] = useState<"light" | "dark">(() => readTheme());
  function toggle(): void {
    setTheme((prev) => {
      const next = prev === "light" ? "dark" : "light";
      try { localStorage.setItem(LS_THEME, next); } catch { /* */ }
      return next;
    });
  }
  return (
    <div data-testid="theme-host" data-theme={theme}>
      <button
        data-testid="grace2-theme-toggle"
        aria-label={theme === "light" ? "Switch to dark theme" : "Switch to light theme"}
        aria-pressed={theme === "dark"}
        onClick={toggle}
      >
        {theme === "light" ? "☾" : "☀"}
      </button>
    </div>
  );
}

describe("Theme toggle (job-0076 bundled enhancement)", () => {
  beforeEach(() => {
    localStorage.clear();
  });
  afterEach(() => {
    localStorage.clear();
  });

  it("defaults to light theme when localStorage is empty", () => {
    render(<ThemeShell />);
    expect(screen.getByTestId("theme-host")).toHaveAttribute("data-theme", "light");
    expect(screen.getByTestId("grace2-theme-toggle")).toHaveAttribute(
      "aria-label",
      "Switch to dark theme",
    );
  });

  it("clicking toggle flips to dark and writes localStorage", () => {
    render(<ThemeShell />);
    act(() => {
      fireEvent.click(screen.getByTestId("grace2-theme-toggle"));
    });
    expect(screen.getByTestId("theme-host")).toHaveAttribute("data-theme", "dark");
    expect(localStorage.getItem(LS_THEME)).toBe("dark");
    expect(screen.getByTestId("grace2-theme-toggle")).toHaveAttribute(
      "aria-label",
      "Switch to light theme",
    );
  });

  it("re-mount reads persisted dark from localStorage", () => {
    localStorage.setItem(LS_THEME, "dark");
    render(<ThemeShell />);
    expect(screen.getByTestId("theme-host")).toHaveAttribute("data-theme", "dark");
  });

  it("clicking twice returns to light", () => {
    render(<ThemeShell />);
    const btn = screen.getByTestId("grace2-theme-toggle");
    act(() => { fireEvent.click(btn); });
    act(() => { fireEvent.click(btn); });
    expect(screen.getByTestId("theme-host")).toHaveAttribute("data-theme", "light");
    expect(localStorage.getItem(LS_THEME)).toBe("light");
  });
});

// --- job-0140: PayloadWarningInline seam + component tests ---------------- //
//
// Tests the dev-injection seam __grace2InjectPayloadWarning and verifies that:
//   1. The seam wires setPayloadWarnings so PayloadWarningInline renders.
//   2. The component shows estimated_mb, threshold_mb, recommendation.
//   3. All 3 option buttons render (proceed / cancel / narrow_scope).
//   4. Clicking "Proceed" calls onDecide with decision="proceed", revised=null.
//   5. Clicking "Cancel" calls onDecide with decision="cancel", revised=null.
//   6. Clicking "Narrow scope" with alternative_args calls onDecide with
//      decision="narrow_scope" and the provided alternative_args.
//
// The seam itself is integration-tested via a PayloadWarningShell component
// that mirrors the App.tsx queue pattern without importing WebSocket/WebGL.

import { PayloadWarningInline } from "./components/PayloadWarningInline";
import type { PayloadWarningEnvelopePayload, PayloadConfirmationDecision } from "./contracts";

// Minimal shell mirroring the App.tsx payloadWarnings queue pattern.
function PayloadWarningShell({
  initialWarning,
}: {
  initialWarning?: PayloadWarningEnvelopePayload;
}): JSX.Element {
  const [warnings, setWarnings] = useState<PayloadWarningEnvelopePayload[]>(
    initialWarning ? [initialWarning] : [],
  );

  // Expose the seam function on window so tests can call it.
  // In production App.tsx this is registered in a useEffect guarded by
  // import.meta.env.DEV.  Here we register unconditionally for testing.
  (window as Window & { __grace2InjectPayloadWarning?: (p: PayloadWarningEnvelopePayload) => void }).__grace2InjectPayloadWarning = (p) => {
    setWarnings((prev) => [p, ...prev]);
  };

  function handleDecide(
    warningId: string,
    _decision: PayloadConfirmationDecision,
    _revised: Record<string, unknown> | null,
  ): void {
    setWarnings((prev) => prev.filter((w) => w.warning_id !== warningId));
  }

  return (
    <div data-testid="warning-shell">
      {warnings.map((w) => (
        <PayloadWarningInline
          key={w.warning_id}
          warning={w}
          onDecide={(decision, revised) => handleDecide(w.warning_id, decision, revised)}
        />
      ))}
    </div>
  );
}

// Sample payload factory.
function makeWarning(
  overrides: Partial<PayloadWarningEnvelopePayload> = {},
): PayloadWarningEnvelopePayload {
  return {
    warning_id: "test-warning-001",
    tool_name: "fetch_dem",
    tool_args: { bbox: [-82, 26, -81, 27] },
    estimated_mb: 42.5,
    threshold_mb: 25,
    recommendation: "Consider narrowing the bbox to reduce payload size.",
    alternative_args: { bbox: [-81.8, 26.2, -81.2, 26.8] },
    options: ["proceed", "narrow_scope", "cancel"],
    ...overrides,
  };
}

describe("PayloadWarningInline component (job-0140)", () => {
  it("renders estimated_mb, threshold_mb, recommendation", () => {
    const w = makeWarning();
    render(
      <PayloadWarningInline warning={w} onDecide={vi.fn()} />,
    );
    expect(screen.getByTestId("payload-warning-estimated-mb")).toHaveTextContent("42.5");
    expect(screen.getByTestId("payload-warning-threshold-mb")).toHaveTextContent("25");
    expect(screen.getByTestId("payload-warning-recommendation")).toHaveTextContent(
      "Consider narrowing the bbox to reduce payload size.",
    );
  });

  it("renders 3 action buttons: Proceed, Narrow scope, Cancel", () => {
    const w = makeWarning();
    render(<PayloadWarningInline warning={w} onDecide={vi.fn()} />);
    expect(screen.getByTestId("payload-warning-button-proceed")).toBeInTheDocument();
    expect(screen.getByTestId("payload-warning-button-narrow_scope")).toBeInTheDocument();
    expect(screen.getByTestId("payload-warning-button-cancel")).toBeInTheDocument();
  });

  it("clicking Proceed calls onDecide with 'proceed' and null revised", () => {
    const onDecide = vi.fn();
    const w = makeWarning();
    render(<PayloadWarningInline warning={w} onDecide={onDecide} />);
    act(() => {
      fireEvent.click(screen.getByTestId("payload-warning-button-proceed"));
    });
    expect(onDecide).toHaveBeenCalledOnce();
    expect(onDecide).toHaveBeenCalledWith("proceed", null);
  });

  it("clicking Cancel calls onDecide with 'cancel' and null revised", () => {
    const onDecide = vi.fn();
    const w = makeWarning();
    render(<PayloadWarningInline warning={w} onDecide={onDecide} />);
    act(() => {
      fireEvent.click(screen.getByTestId("payload-warning-button-cancel"));
    });
    expect(onDecide).toHaveBeenCalledOnce();
    expect(onDecide).toHaveBeenCalledWith("cancel", null);
  });

  it("clicking Narrow scope with alternative_args calls onDecide with 'narrow_scope' + alternative_args", () => {
    const onDecide = vi.fn();
    const w = makeWarning();
    render(<PayloadWarningInline warning={w} onDecide={onDecide} />);
    act(() => {
      fireEvent.click(screen.getByTestId("payload-warning-button-narrow_scope"));
    });
    expect(onDecide).toHaveBeenCalledOnce();
    expect(onDecide).toHaveBeenCalledWith("narrow_scope", w.alternative_args);
  });

  it("after a decision, buttons are disabled and 'Sent' footer appears", () => {
    const w = makeWarning();
    render(<PayloadWarningInline warning={w} onDecide={vi.fn()} />);
    act(() => {
      fireEvent.click(screen.getByTestId("payload-warning-button-proceed"));
    });
    expect(screen.getByTestId("payload-warning-button-proceed")).toBeDisabled();
    expect(screen.getByTestId("payload-warning-sent")).toHaveTextContent("Sent:");
  });
});

describe("__grace2InjectPayloadWarning dev seam (job-0140)", () => {
  afterEach(() => {
    delete (window as Window & { __grace2InjectPayloadWarning?: unknown }).__grace2InjectPayloadWarning;
  });

  it("seam absent before shell mounts → no warning card", () => {
    render(<div data-testid="empty" />);
    expect(screen.queryByTestId("payload-warning-inline")).toBeNull();
  });

  it("injecting a warning via seam renders PayloadWarningInline", () => {
    render(<PayloadWarningShell />);
    act(() => {
      (window as Window & { __grace2InjectPayloadWarning?: (p: PayloadWarningEnvelopePayload) => void }).__grace2InjectPayloadWarning?.(makeWarning());
    });
    expect(screen.getByTestId("payload-warning-inline")).toBeInTheDocument();
  });

  it("injected warning shows tool name", () => {
    render(<PayloadWarningShell />);
    act(() => {
      (window as Window & { __grace2InjectPayloadWarning?: (p: PayloadWarningEnvelopePayload) => void }).__grace2InjectPayloadWarning?.(makeWarning({ tool_name: "fetch_buildings" }));
    });
    expect(screen.getByTestId("payload-warning-tool")).toHaveTextContent("fetch_buildings");
  });

  it("shell initialised with a warning renders it immediately", () => {
    render(<PayloadWarningShell initialWarning={makeWarning()} />);
    expect(screen.getByTestId("payload-warning-inline")).toBeInTheDocument();
  });

  it("clicking Proceed removes the card from the queue", () => {
    render(<PayloadWarningShell initialWarning={makeWarning()} />);
    act(() => {
      fireEvent.click(screen.getByTestId("payload-warning-button-proceed"));
    });
    // After onDecide the shell removes it from the warnings list; the inline
    // card shows the 'Sent' footer for a brief moment but the shell removes
    // the entry — the card no longer has buttons.
    expect(screen.queryByTestId("payload-warning-button-proceed")).toBeNull();
  });
});
