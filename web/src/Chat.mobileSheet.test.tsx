// GRACE-2 web — mobile bottom-sheet tests (job-0278, mobile-friendly UI).
//
// Chat itself cannot mount in happy-dom (it opens a WebSocket), so — per the
// established pure-helper pattern (pipelineReducer / buildInterleavedStream)
// — these tests pin the EXPORTED sheet primitives Chat composes:
//
//   - mobileSheetContainerStyle(expanded): bottom-pinned, full-width,
//     70vh when expanded / content-height when collapsed;
//   - SheetToggleHandle: 44px toggle with aria-expanded, fires onToggle;
//   - a stateful harness covering the collapsed → expanded → collapsed
//     cycle exactly the way Chat wires it (handle + conditional scroll
//     visibility via display, content kept mounted).

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { useState } from "react";
import {
  MOBILE_SHEET_EXPANDED_HEIGHT,
  SheetToggleHandle,
  mobileSheetContainerStyle,
} from "./Chat";

afterEach(() => cleanup());

describe("mobileSheetContainerStyle", () => {
  it("pins the sheet to the bottom edge, full width", () => {
    for (const expanded of [false, true]) {
      const s = mobileSheetContainerStyle(expanded);
      expect(s.position).toBe("absolute");
      expect(s.left).toBe(0);
      expect(s.right).toBe(0);
      expect(s.bottom).toBe(0);
      expect(s.display).toBe("flex");
      expect(s.flexDirection).toBe("column");
    }
  });

  it("collapsed = content height (composer only); expanded = 70vh", () => {
    expect(mobileSheetContainerStyle(false).height).toBe("auto");
    expect(mobileSheetContainerStyle(true).height).toBe(
      MOBILE_SHEET_EXPANDED_HEIGHT,
    );
    expect(MOBILE_SHEET_EXPANDED_HEIGHT).toBe("70vh");
  });

  it("rounds only the TOP corners (sheet idiom) and layers above panels", () => {
    const s = mobileSheetContainerStyle(false);
    expect(s.borderRadius).toBe("14px 14px 0 0");
    // Above panels (20) + hamburgers (30); below drawer backdrop (40).
    expect(s.zIndex).toBe(32);
  });
});

describe("SheetToggleHandle", () => {
  it("renders a >=44px full-width handle with aria-expanded state", () => {
    render(<SheetToggleHandle expanded={false} onToggle={vi.fn()} />);
    const handle = screen.getByTestId("grace2-chat-sheet-toggle");
    expect(handle).toHaveAttribute("aria-expanded", "false");
    expect(handle).toHaveAttribute("aria-label", "Expand chat");
    expect(handle.style.minHeight).toBe("44px");
    expect(handle.style.width).toBe("100%");
  });

  it("flips label + aria when expanded", () => {
    render(<SheetToggleHandle expanded={true} onToggle={vi.fn()} />);
    const handle = screen.getByTestId("grace2-chat-sheet-toggle");
    expect(handle).toHaveAttribute("aria-expanded", "true");
    expect(handle).toHaveAttribute("aria-label", "Collapse chat");
  });

  it("fires onToggle on click", () => {
    const onToggle = vi.fn();
    render(<SheetToggleHandle expanded={false} onToggle={onToggle} />);
    fireEvent.click(screen.getByTestId("grace2-chat-sheet-toggle"));
    expect(onToggle).toHaveBeenCalledTimes(1);
  });
});

describe("bottom-sheet toggle cycle (Chat wiring shape)", () => {
  // Mirrors how Chat.tsx composes the primitives: container style driven by
  // expansion state, handle toggles it, conversation area hides via
  // display:none while STAYING MOUNTED (stream + scroll state survive).
  function SheetHarness(): JSX.Element {
    const [expanded, setExpanded] = useState(false);
    return (
      <div
        data-testid="sheet"
        data-sheet-state={expanded ? "expanded" : "collapsed"}
        style={mobileSheetContainerStyle(expanded)}
      >
        <SheetToggleHandle
          expanded={expanded}
          onToggle={() => setExpanded((v) => !v)}
        />
        <div
          data-testid="sheet-scroll"
          style={{ display: expanded ? "flex" : "none" }}
        >
          conversation
        </div>
        <textarea data-testid="sheet-composer" />
      </div>
    );
  }

  it("starts collapsed: composer visible, conversation hidden but mounted", () => {
    render(<SheetHarness />);
    expect(screen.getByTestId("sheet")).toHaveAttribute(
      "data-sheet-state",
      "collapsed",
    );
    expect(screen.getByTestId("sheet").style.height).toBe("auto");
    expect(screen.getByTestId("sheet-scroll").style.display).toBe("none");
    expect(screen.getByTestId("sheet-composer")).toBeTruthy();
  });

  it("handle expands to 70vh and reveals the conversation; second tap collapses", () => {
    render(<SheetHarness />);
    fireEvent.click(screen.getByTestId("grace2-chat-sheet-toggle"));
    expect(screen.getByTestId("sheet")).toHaveAttribute(
      "data-sheet-state",
      "expanded",
    );
    expect(screen.getByTestId("sheet").style.height).toBe("70vh");
    expect(screen.getByTestId("sheet-scroll").style.display).toBe("flex");
    fireEvent.click(screen.getByTestId("grace2-chat-sheet-toggle"));
    expect(screen.getByTestId("sheet")).toHaveAttribute(
      "data-sheet-state",
      "collapsed",
    );
    expect(screen.getByTestId("sheet-scroll").style.display).toBe("none");
    // Still mounted — content was hidden, not destroyed.
    expect(screen.getByTestId("sheet-scroll")).toBeTruthy();
  });
});
