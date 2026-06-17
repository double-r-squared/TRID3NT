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
  SHEET_DRAG_THRESHOLD_PX,
  SheetActiveToolStrip,
  SheetToggleHandle,
  clampSheetHeight,
  findRunningToolStep,
  isSheetDragGesture,
  mobileSheetContainerStyle,
  readSheetHeight,
  stepInterleaveKey,
  writeSheetHeight,
} from "./Chat";
import type {
  PipelineStatePayload,
  PipelineStepState,
  PipelineStepSummary,
} from "./contracts";

afterEach(() => cleanup());

// F44 — a TAP is a pointerdown + pointerup at the SAME spot (no
// threshold-crossing travel). The handle distinguishes it from a drag.
function tap(el: Element): void {
  fireEvent.pointerDown(el, { clientX: 100, clientY: 500, pointerId: 1 });
  fireEvent.pointerUp(el, { clientX: 100, clientY: 500, pointerId: 1 });
}

// F44 — a vertical DRAG: pointerdown, a move past the threshold, pointerup.
// clientY decreasing = pointer moving UP = taller sheet (bottom-anchored).
function dragVertical(el: Element, fromY: number, toY: number): void {
  fireEvent.pointerDown(el, { clientX: 100, clientY: fromY, pointerId: 1 });
  fireEvent.pointerMove(el, { clientX: 100, clientY: toY, pointerId: 1 });
  fireEvent.pointerUp(el, { clientX: 100, clientY: toY, pointerId: 1 });
}

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

  // job-0284 / F56 — translucent-surface pins: the sheet keeps a
  // linear-gradient surface with the hairline family border in both states.
  // F56 made the alpha a per-user opacity tier; the LOW tier preserves the
  // original map-reads-through legibility window (0.55–0.7).
  it("job-0284: translucent family gradient + hairline border in both states", () => {
    for (const expanded of [false, true]) {
      const s = mobileSheetContainerStyle(expanded);
      const bg = String(s.background);
      expect(bg).toContain("linear-gradient");
      const alphas = [...bg.matchAll(/rgba\(\d+,\d+,\d+,(0?\.\d+|1)\)/g)].map(
        (m) => Number(m[1]),
      );
      expect(alphas.length).toBeGreaterThan(0);
      expect(s.border).toBe("1px solid rgba(255,255,255,0.10)");
      expect(s.borderBottom).toBe("none");
    }
  });

  it("F56: LOW opacity tier keeps the map-reads-through window (0.55–0.7)", () => {
    for (const expanded of [false, true]) {
      const s = mobileSheetContainerStyle(expanded, 70, "low");
      const alphas = [
        ...String(s.background).matchAll(/rgba\(\d+,\d+,\d+,(0?\.\d+|1)\)/g),
      ].map((m) => Number(m[1]));
      for (const a of alphas) {
        expect(a).toBeGreaterThanOrEqual(0.55);
        expect(a).toBeLessThanOrEqual(0.7);
      }
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

  it("fires onToggle on a TAP (pointer down+up with no travel)", () => {
    const onToggle = vi.fn();
    render(<SheetToggleHandle expanded={false} onToggle={onToggle} />);
    tap(screen.getByTestId("grace2-chat-sheet-toggle"));
    expect(onToggle).toHaveBeenCalledTimes(1);
  });
});

// --- F44 (job-0322): drag-to-resize + tap-to-fold on the sheet handle ----- //

describe("clampSheetHeight (F44)", () => {
  it("clamps to the [30, 92] vh band and rounds", () => {
    expect(clampSheetHeight(10)).toBe(30);
    expect(clampSheetHeight(999)).toBe(92);
    expect(clampSheetHeight(55.4)).toBe(55);
    expect(clampSheetHeight(55.6)).toBe(56);
  });

  it("passes through an in-band value untouched", () => {
    expect(clampSheetHeight(70)).toBe(70);
    expect(clampSheetHeight(30)).toBe(30);
    expect(clampSheetHeight(92)).toBe(92);
  });

  it("falls back to the 70vh default on non-finite input", () => {
    expect(clampSheetHeight(Number.NaN)).toBe(70);
    expect(clampSheetHeight(Infinity)).toBe(70);
    expect(clampSheetHeight(-Infinity)).toBe(70);
  });
});

describe("readSheetHeight / writeSheetHeight (F44)", () => {
  afterEach(() => {
    try {
      localStorage.clear();
    } catch {
      /* ignore */
    }
  });

  it("defaults to 70vh when nothing is persisted", () => {
    expect(readSheetHeight()).toBe(70);
  });

  it("round-trips a clamped height through localStorage", () => {
    writeSheetHeight(55);
    expect(localStorage.getItem("grace2.chatSheetHeightVh")).toBe("55");
    expect(readSheetHeight()).toBe(55);
  });

  it("persists clamped (out-of-band writes stored at the boundary)", () => {
    writeSheetHeight(9999);
    expect(readSheetHeight()).toBe(92);
    writeSheetHeight(1);
    expect(readSheetHeight()).toBe(30);
  });

  it("garbage in storage degrades to the default", () => {
    localStorage.setItem("grace2.chatSheetHeightVh", "not-a-number");
    expect(readSheetHeight()).toBe(70);
  });
});

describe("isSheetDragGesture (F44 — tap-vs-drag threshold)", () => {
  it("a sub-threshold gesture in EITHER axis is a TAP (not a drag)", () => {
    expect(isSheetDragGesture(0, 0)).toBe(false);
    expect(isSheetDragGesture(SHEET_DRAG_THRESHOLD_PX - 1, 0)).toBe(false);
    expect(isSheetDragGesture(0, SHEET_DRAG_THRESHOLD_PX - 1)).toBe(false);
    expect(isSheetDragGesture(-2, 3)).toBe(false);
  });

  it("a gesture at/over the threshold in either axis is a DRAG", () => {
    expect(isSheetDragGesture(0, SHEET_DRAG_THRESHOLD_PX)).toBe(true);
    expect(isSheetDragGesture(SHEET_DRAG_THRESHOLD_PX, 0)).toBe(true);
    expect(isSheetDragGesture(0, -SHEET_DRAG_THRESHOLD_PX)).toBe(true);
    expect(isSheetDragGesture(100, 100)).toBe(true);
  });
});

describe("SheetToggleHandle — drag-to-resize vs tap-to-fold (F44)", () => {
  // happy-dom reports window.innerHeight (defaults to 768) so the vh math is
  // deterministic: height = (innerHeight - clientY) / innerHeight * 100.
  const VPH = window.innerHeight || 768;

  it("a clean TAP toggles (onToggle) and never resizes", () => {
    const onToggle = vi.fn();
    const onResize = vi.fn();
    const onResizeEnd = vi.fn();
    render(
      <SheetToggleHandle
        expanded={true}
        onToggle={onToggle}
        onResize={onResize}
        onResizeEnd={onResizeEnd}
      />,
    );
    tap(screen.getByTestId("grace2-chat-sheet-toggle"));
    expect(onToggle).toHaveBeenCalledTimes(1);
    expect(onResize).not.toHaveBeenCalled();
    expect(onResizeEnd).not.toHaveBeenCalled();
  });

  it("a vertical DRAG resizes (onResize + onResizeEnd) and never toggles", () => {
    const onToggle = vi.fn();
    const onResize = vi.fn();
    const onResizeEnd = vi.fn();
    render(
      <SheetToggleHandle
        expanded={true}
        onToggle={onToggle}
        onResize={onResize}
        onResizeEnd={onResizeEnd}
      />,
    );
    // Pointer down low (short sheet), drag UP to a high Y (tall sheet).
    const targetY = Math.round(VPH * 0.2); // 80% of the viewport tall
    dragVertical(
      screen.getByTestId("grace2-chat-sheet-toggle"),
      Math.round(VPH * 0.5),
      targetY,
    );
    expect(onToggle).not.toHaveBeenCalled();
    expect(onResize).toHaveBeenCalled();
    expect(onResizeEnd).toHaveBeenCalledTimes(1);
    // The reported height is the clamped vh of the final pointer position.
    const expectedVh = clampSheetHeight(((VPH - targetY) / VPH) * 100);
    expect(onResizeEnd).toHaveBeenLastCalledWith(expectedVh);
  });

  it("a higher pointer (smaller clientY) → a TALLER sheet (bottom-anchored)", () => {
    const onResize = vi.fn();
    render(
      <SheetToggleHandle
        expanded={true}
        onToggle={vi.fn()}
        onResize={onResize}
        onResizeEnd={vi.fn()}
      />,
    );
    const handle = screen.getByTestId("grace2-chat-sheet-toggle");
    fireEvent.pointerDown(handle, {
      clientX: 100,
      clientY: Math.round(VPH * 0.6),
      pointerId: 1,
    });
    fireEvent.pointerMove(handle, {
      clientX: 100,
      clientY: Math.round(VPH * 0.5),
      pointerId: 1,
    });
    fireEvent.pointerMove(handle, {
      clientX: 100,
      clientY: Math.round(VPH * 0.2),
      pointerId: 1,
    });
    fireEvent.pointerUp(handle, {
      clientX: 100,
      clientY: Math.round(VPH * 0.2),
      pointerId: 1,
    });
    const calls = onResize.mock.calls.map((c) => c[0] as number);
    // Monotonically taller as the pointer rises.
    expect(calls[calls.length - 1]).toBeGreaterThan(calls[0]!);
  });

  it("touchAction:'none' on the grip so the browser yields the vertical pan", () => {
    render(<SheetToggleHandle expanded={true} onToggle={vi.fn()} />);
    expect(
      screen.getByTestId("grace2-chat-sheet-toggle").style.touchAction,
    ).toBe("none");
  });

  it("tap still folds when no resize callbacks are wired (collapsed handle)", () => {
    const onToggle = vi.fn();
    render(<SheetToggleHandle expanded={false} onToggle={onToggle} />);
    tap(screen.getByTestId("grace2-chat-sheet-toggle"));
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
    tap(screen.getByTestId("grace2-chat-sheet-toggle"));
    expect(screen.getByTestId("sheet")).toHaveAttribute(
      "data-sheet-state",
      "expanded",
    );
    expect(screen.getByTestId("sheet").style.height).toBe("70vh");
    expect(screen.getByTestId("sheet-scroll").style.display).toBe("flex");
    tap(screen.getByTestId("grace2-chat-sheet-toggle"));
    expect(screen.getByTestId("sheet")).toHaveAttribute(
      "data-sheet-state",
      "collapsed",
    );
    expect(screen.getByTestId("sheet-scroll").style.display).toBe("none");
    // Still mounted — content was hidden, not destroyed.
    expect(screen.getByTestId("sheet-scroll")).toBeTruthy();
  });
});

describe("drag-resize → container height (F44 Chat wiring shape)", () => {
  // Mirrors how Chat.tsx threads the handle's drag callbacks into the
  // expanded-sheet height: a state vh, applied to mobileSheetContainerStyle,
  // updated live by onResize. Starts EXPANDED so the resize callbacks are
  // wired (Chat only wires them while expanded).
  function ResizeHarness(): JSX.Element {
    const [heightVh, setHeightVh] = useState(70);
    return (
      <div
        data-testid="sheet"
        style={mobileSheetContainerStyle(true, heightVh, "medium")}
      >
        <SheetToggleHandle
          expanded={true}
          onToggle={() => undefined}
          onResize={(vh) => setHeightVh(vh)}
          onResizeEnd={(vh) => setHeightVh(vh)}
        />
      </div>
    );
  }

  it("dragging the handle changes the expanded sheet height", () => {
    const VPH = window.innerHeight || 768;
    render(<ResizeHarness />);
    expect(screen.getByTestId("sheet").style.height).toBe("70vh");
    // Drag UP to ~85% of the viewport.
    const targetY = Math.round(VPH * 0.15);
    dragVertical(
      screen.getByTestId("grace2-chat-sheet-toggle"),
      Math.round(VPH * 0.5),
      targetY,
    );
    const expectedVh = clampSheetHeight(((VPH - targetY) / VPH) * 100);
    expect(screen.getByTestId("sheet").style.height).toBe(`${expectedVh}vh`);
    // It moved (and stayed in band).
    expect(expectedVh).toBeGreaterThan(70);
    expect(expectedVh).toBeLessThanOrEqual(92);
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

// --- F42 (job-0321): collapsed-sheet strip rainbow running animation ------- //
//
// The strip only ever shows a RUNNING tool, so its label always gets the SAME
// animated rainbow-gradient treatment the inline PipelineCard uses for running
// steps — UNLESS the user prefers reduced motion, in which case it falls back
// to the solid label color, exactly like PipelineCard.

/** Force `prefers-reduced-motion: reduce` to the given value for the duration
 *  of a render. Restores the original matchMedia afterwards. */
function mockReducedMotion(reduce: boolean): () => void {
  const original = window.matchMedia;
  window.matchMedia = ((query: string) => ({
    matches: query.includes("prefers-reduced-motion") ? reduce : false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })) as unknown as typeof window.matchMedia;
  return () => {
    window.matchMedia = original;
  };
}

describe("SheetActiveToolStrip — F42 rainbow running animation", () => {
  it("applies the animated rainbow gradient to the label when motion is allowed", () => {
    const restore = mockReducedMotion(false);
    try {
      render(
        <SheetActiveToolStrip
          step={step({ state: "running" })}
          onExpand={vi.fn()}
        />,
      );
      const labelEl = screen.getByTestId(
        "grace2-sheet-tool-strip-label",
      ) as HTMLElement;
      // SAME gradient treatment PipelineCard uses for running steps.
      expect(labelEl.style.backgroundImage).toBe(
        "linear-gradient(90deg, #FF6B6B, #FFD93D, #6BCB77, #4D96FF, #B266FF, #FF6B6B)",
      );
      expect(labelEl.style.backgroundSize).toBe("300% 100%");
      expect(labelEl.style.animation).toBe("grace2-hue-cycle 3s linear infinite");
      // background-clip:text technique — text is painted by the gradient.
      // (The vendor-prefixed -webkit-text-fill-color:transparent twin is set
      // in the component for Safari; happy-dom's CSSOM doesn't reflect that
      // unknown vendor property, so we assert the standard color:transparent.)
      expect(labelEl.style.color).toBe("transparent");
      expect(labelEl.style.backgroundClip).toBe("text");
      // Layout style is preserved (single-line ellipsis truncation).
      expect(labelEl.style.whiteSpace).toBe("nowrap");
      expect(labelEl.style.textOverflow).toBe("ellipsis");
    } finally {
      restore();
    }
  });

  it("falls back to a solid color with NO animation when reduced motion is preferred", () => {
    const restore = mockReducedMotion(true);
    try {
      render(
        <SheetActiveToolStrip
          step={step({ state: "running" })}
          onExpand={vi.fn()}
        />,
      );
      const labelEl = screen.getByTestId(
        "grace2-sheet-tool-strip-label",
      ) as HTMLElement;
      expect(labelEl.style.color).toBe("#eee");
      expect(labelEl.style.animation).toBe("");
      expect(labelEl.style.backgroundImage).toBe("");
      // Layout style still intact.
      expect(labelEl.style.whiteSpace).toBe("nowrap");
    } finally {
      restore();
    }
  });
});
