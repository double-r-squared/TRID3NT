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
  SheetActiveToolStrip,
  SheetToggleHandle,
  findRunningToolStep,
  mobileSheetContainerStyle,
  stepInterleaveKey,
} from "./Chat";
import type {
  PipelineStatePayload,
  PipelineStepState,
  PipelineStepSummary,
} from "./contracts";

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
    // job-0284 — 12px joins the design-family panel radius (was 14).
    expect(s.borderRadius).toBe("12px 12px 0 0");
    // Above panels (20) + hamburgers (30); below drawer backdrop (40).
    expect(s.zIndex).toBe(32);
  });

  // job-0284 — translucent-surface pins: the sheet must let the map read
  // through in BOTH states (map-centric app), with the hairline family
  // border. Alpha stays inside the 0.55–0.7 legibility window.
  it("job-0284: translucent family gradient in both states (map reads through)", () => {
    for (const expanded of [false, true]) {
      const s = mobileSheetContainerStyle(expanded);
      const bg = String(s.background);
      expect(bg).toContain("linear-gradient");
      const alphas = [...bg.matchAll(/rgba\(\d+,\d+,\d+,(0\.\d+)\)/g)].map(
        (m) => Number(m[1]),
      );
      expect(alphas.length).toBeGreaterThan(0);
      for (const a of alphas) {
        expect(a).toBeGreaterThanOrEqual(0.55);
        expect(a).toBeLessThanOrEqual(0.7);
      }
      expect(s.border).toBe("1px solid rgba(255,255,255,0.10)");
      expect(s.borderBottom).toBe("none");
    }
  });

  it("job-0284: NO backdrop-filter — the sheet hosts position:fixed children (ChartGallery)", () => {
    // A non-none backdrop-filter would make the sheet the containing block
    // for position:fixed descendants, trapping ChartGallery inside the
    // sheet instead of overlaying the viewport (job-0283 hazard).
    for (const expanded of [false, true]) {
      const s = mobileSheetContainerStyle(expanded) as Record<string, unknown>;
      expect(s.backdropFilter).toBeUndefined();
      expect(s.WebkitBackdropFilter).toBeUndefined();
      expect(s.filter).toBeUndefined();
      expect(s.transform).toBeUndefined();
      expect(s.willChange).toBeUndefined();
    }
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

  it("job-0280: the chevron arrow is GONE — the bar is the single affordance", () => {
    for (const expanded of [false, true]) {
      const { unmount } = render(
        <SheetToggleHandle expanded={expanded} onToggle={vi.fn()} />,
      );
      const handle = screen.getByTestId("grace2-chat-sheet-toggle");
      // No chevron glyph in either direction…
      expect(handle.textContent ?? "").not.toMatch(/[⌃⌄]/);
      // …exactly one child: the handle bar.
      expect(handle.children.length).toBe(1);
      // The whole handle area stays tappable at the HIG minimum.
      expect(handle.style.minHeight).toBe("44px");
      unmount();
    }
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

// --- Collapsed-sheet active-tool strip (job-0280) -------------------------- //

function step(
  over: Partial<PipelineStepSummary> & { state: PipelineStepState },
): PipelineStepSummary {
  return {
    step_id: over.step_id ?? "step-1",
    name: over.name ?? "fetch_3dep_dem",
    tool_name: over.tool_name ?? "fetch_3dep_dem",
    ...over,
  };
}

function snap(
  pipelineId: string,
  steps: PipelineStepSummary[],
): PipelineStatePayload {
  return { pipeline_id: pipelineId, steps };
}

/** stepOrder map keyed the way Chat records seqs (ux-batch-1 J9):
 *  stepInterleaveKey — step_id for tool steps, `llm_generation|<tool>` for the
 *  thinking pseudo-step. */
function orderOf(entries: Array<[PipelineStepSummary, number]>): Map<string, number> {
  const m = new Map<string, number>();
  for (const [s, seq] of entries) m.set(stepInterleaveKey(s), seq);
  return m;
}

describe("findRunningToolStep", () => {
  it("returns null with no pipeline content at all", () => {
    expect(findRunningToolStep([], null, new Map())).toBeNull();
  });

  it("returns null when every step is terminal (strip hides)", () => {
    const done = step({ step_id: "a", state: "complete" });
    const failed = step({
      step_id: "b",
      name: "compute_slope",
      tool_name: "compute_slope",
      state: "failed",
    });
    expect(
      findRunningToolStep(
        [snap("p1", [done, failed])],
        null,
        orderOf([[done, 1], [failed, 2]]),
      ),
    ).toBeNull();
  });

  it("returns the running step from the live snapshot", () => {
    const running = step({ state: "running" });
    expect(
      findRunningToolStep([], snap("p1", [running]), orderOf([[running, 1]])),
    ).toEqual(running);
  });

  it("excludes the llm_generation thinking pseudo-step", () => {
    const thinking = step({
      step_id: "t",
      name: "llm_generation",
      tool_name: "llm_generation",
      state: "running",
    });
    expect(
      findRunningToolStep([], snap("p1", [thinking]), orderOf([[thinking, 1]])),
    ).toBeNull();
  });

  it("prefers the MOST-RECENT running step by arrival seq", () => {
    const older = step({ step_id: "a", state: "running" });
    const newer = step({
      step_id: "b",
      name: "publish_layer",
      tool_name: "publish_layer",
      state: "running",
    });
    const found = findRunningToolStep(
      [snap("p1", [older])],
      snap("p2", [newer]),
      orderOf([[older, 1], [newer, 2]]),
    );
    expect(found?.step_id).toBe("b");
  });

  it("collapses a SINGLE invocation's running→complete by step_id (pass-1)", () => {
    // One tool invocation has ONE step_id (pipeline_emitter.add_step); its
    // running snapshot archives to history and the complete arrives in live
    // under the SAME step_id. mergeStepsByStepId pass-1 keeps only the terminal
    // card → no running step → strip hides.
    const running = step({ step_id: "a", state: "running" });
    const complete = step({ step_id: "a", state: "complete" });
    expect(
      findRunningToolStep(
        [snap("p1", [running])],
        snap("p2", [complete]),
        orderOf([[running, 1]]),
      ),
    ).toBeNull();
  });

  it("does NOT collapse two DISTINCT step_ids of the same tool — surfaces the running one (J9/F18)", () => {
    // ux-batch-1 J9: two step_ids = two invocations = two cards. The old
    // cross-step_id (name|tool_name) collapse hid a genuinely-running second
    // invocation behind a completed first one (the F18 ordering bug). A
    // still-running distinct step_id must now be surfaced.
    const runningA = step({ step_id: "a", state: "running" });
    const completeB = step({ step_id: "b", state: "complete" });
    const found = findRunningToolStep(
      [snap("p1", [runningA])],
      snap("p2", [completeB]),
      orderOf([[runningA, 1], [completeB, 2]]),
    );
    expect(found?.step_id).toBe("a");
  });
});

describe("SheetActiveToolStrip", () => {
  it("renders the humanized label, a m:ss timer, and a spinner", () => {
    const started = new Date(Date.now() - 65_000).toISOString();
    render(
      <SheetActiveToolStrip
        step={step({ state: "running", started_at: started })}
        onExpand={vi.fn()}
      />,
    );
    const strip = screen.getByTestId("grace2-sheet-tool-strip");
    expect(strip).toBeTruthy();
    // job-0294 — an unmapped tool name title-cases (never raw snake_case);
    // the strip only shows running tools, so the present-tense "…" suffix
    // applies.
    expect(
      screen.getByTestId("grace2-sheet-tool-strip-label").textContent,
    ).toBe("Fetch 3dep Dem…");
    // Anchored on started_at (~65s ago) → a ticking 1:0x, never 0:00.
    expect(
      screen.getByTestId("grace2-sheet-tool-strip-timer").textContent,
    ).toMatch(/^1:0\d$/);
    expect(screen.getByTestId("pipeline-card-indicator")).toBeTruthy();
  });

  it("tap expands the sheet (fires onExpand)", () => {
    const onExpand = vi.fn();
    render(
      <SheetActiveToolStrip
        step={step({ state: "running" })}
        onExpand={onExpand}
      />,
    );
    fireEvent.click(screen.getByTestId("grace2-sheet-tool-strip"));
    expect(onExpand).toHaveBeenCalledTimes(1);
  });

  it("Chat wiring shape: strip renders while a step runs, hides when terminal", () => {
    // Mirrors Chat.tsx: strip mounts IFF findRunningToolStep is non-null on
    // the collapsed sheet's pipeline view-model.
    function StripHarness({
      live,
      order,
    }: {
      live: PipelineStatePayload | null;
      order: Map<string, number>;
    }): JSX.Element {
      const running = findRunningToolStep([], live, order);
      return (
        <div>
          {running && (
            <SheetActiveToolStrip step={running} onExpand={() => undefined} />
          )}
          <textarea data-testid="composer" />
        </div>
      );
    }
    const running = step({ state: "running" });
    const order = orderOf([[running, 1]]);
    const { rerender } = render(
      <StripHarness live={snap("p1", [running])} order={order} />,
    );
    expect(screen.getByTestId("grace2-sheet-tool-strip")).toBeTruthy();

    // Same logical step transitions to complete → strip disappears.
    rerender(
      <StripHarness
        live={snap("p1", [step({ state: "complete", duration_ms: 4200 })])}
        order={order}
      />,
    );
    expect(screen.queryByTestId("grace2-sheet-tool-strip")).toBeNull();
    // Composer (the strip's anchor) is untouched either way.
    expect(screen.getByTestId("composer")).toBeTruthy();
  });
});
