// GRACE-2 web — PipelineCard unit tests (job-0064; job-0162 visual redesign).
//
// Verifies the job-0162 visual treatment per
// `feedback_pipeline_card_visual_states`:
//
//   - pending  → grey-subdued background, no right-side indicator
//   - running  → spinner indicator + animated gradient text style
//   - complete → green-tinted background, no checkmark / no "completed" text
//   - failed   → red-tinted background, optional error_code chip
//   - cancelled→ yellow-tinted background, distinct from failed
//
// The old "X%" / "✓" / "✗" / "⊘" / "pending" right-side status text and the
// blue left-border accent are gone. Tests are rewritten accordingly.

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, act } from "@testing-library/react";
import {
  PipelineCard,
  formatDuration,
  MIN_RUNNING_DWELL_MS,
} from "./PipelineCard";
import { PipelineStepSummary } from "../contracts";

/** Force `prefers-reduced-motion: reduce` to the given value. Returns a
 *  restore fn. Mirrors the helper used in Chat.mobileSheet.test.tsx. */
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

function makeStep(
  partial: Partial<PipelineStepSummary> & {
    state: PipelineStepSummary["state"];
  },
): PipelineStepSummary {
  return {
    step_id: partial.step_id ?? "step-001",
    name: partial.name ?? "fetch_dem",
    tool_name: partial.tool_name ?? "fetch_dem_tool",
    state: partial.state,
    progress_percent: partial.progress_percent,
    error_code: partial.error_code,
    error_message: partial.error_message,
    // job-0264: thread timer fields through so duration / ticker tests work.
    started_at: partial.started_at,
    completed_at: partial.completed_at,
    duration_ms: partial.duration_ms,
  };
}

// --- Name rendering ------------------------------------------------------- //

describe("PipelineCard — name rendering", () => {
  it("renders a humanized step name for every state (never raw snake_case)", () => {
    const states: PipelineStepSummary["state"][] = [
      "pending",
      "running",
      "complete",
      "failed",
      "cancelled",
    ];
    for (const state of states) {
      const { unmount } = render(
        <PipelineCard step={makeStep({ state, name: `op_${state}` })} />,
      );
      // job-0294: unmapped names Title-Case (with a trailing "…" while
      // active); the raw snake_case is NEVER shown.
      const label = screen.getByTestId("pipeline-card-name");
      expect(label.textContent).not.toContain(`op_${state}`);
      expect(label.textContent).toContain(
        `Op ${state.charAt(0).toUpperCase()}${state.slice(1)}`,
      );
      unmount();
    }
  });
});

// --- Visual state surfaces (job-0162 spec) ------------------------------- //

describe("PipelineCard — job-0162 visual states", () => {
  it("running state shows a spinner indicator", () => {
    render(<PipelineCard step={makeStep({ state: "running" })} />);
    expect(screen.getByTestId("pipeline-card-indicator")).toBeInTheDocument();
  });

  it("non-running states do NOT show a spinner indicator", () => {
    const nonRunning: PipelineStepSummary["state"][] = [
      "pending",
      "complete",
      "failed",
      "cancelled",
    ];
    for (const state of nonRunning) {
      const { unmount } = render(<PipelineCard step={makeStep({ state })} />);
      expect(screen.queryByTestId("pipeline-card-indicator")).toBeNull();
      unmount();
    }
  });

  it("does NOT render legacy terminal-status glyphs ✓ ✗ ⊘ in any state", () => {
    // job-0294: the legacy "…" standalone running-indicator glyph is gone, but
    // a trailing ellipsis inside a present-tense humanized verb ("Fetching
    // DEM…") is intentional UI vocabulary — only the three terminal-status
    // glyphs remain banned.
    const allStates: PipelineStepSummary["state"][] = [
      "pending",
      "running",
      "complete",
      "failed",
      "cancelled",
    ];
    for (const state of allStates) {
      const { container, unmount } = render(
        <PipelineCard step={makeStep({ state })} />,
      );
      const text = container.textContent ?? "";
      expect(text).not.toMatch(/✓|✗|⊘/);
      unmount();
    }
  });

  it("does NOT visually render legacy 'completed' / 'running' / 'pending' text labels", () => {
    // SR-only prefixes (e.g. "completed: ") are allowed for a11y; we filter
    // by checking only nodes that are visually rendered (clip:rect(0,0,0,0)
    // is the visually-hidden pattern).
    const allStates: PipelineStepSummary["state"][] = [
      "pending",
      "running",
      "complete",
      "failed",
      "cancelled",
    ];
    for (const state of allStates) {
      const { container, unmount } = render(
        <PipelineCard step={makeStep({ state, name: "fetch_dem" })} />,
      );
      // Walk the DOM; reject any element with clip:rect(0... (the SR-only
      // marker) before reading its text.
      const visibleText: string[] = [];
      const walker = (el: Element): void => {
        const style = (el as HTMLElement).style;
        const isSrOnly =
          style && style.clip && style.clip.includes("rect(0");
        if (!isSrOnly) {
          for (const child of Array.from(el.childNodes)) {
            if (child.nodeType === Node.TEXT_NODE) {
              visibleText.push(child.textContent ?? "");
            } else if (child.nodeType === Node.ELEMENT_NODE) {
              walker(child as Element);
            }
          }
        }
      };
      walker(container);
      const joined = visibleText.join("");
      expect(joined).not.toMatch(/completed|running|pending/i);
      unmount();
    }
  });

  it("renders error_code chip on failed step", () => {
    render(
      <PipelineCard
        step={makeStep({
          state: "failed",
          error_code: "SOLVER_FAILED",
          error_message: "mesh generation error",
        })}
      />,
    );
    expect(screen.getByTestId("pipeline-card-error")).toHaveTextContent(
      "SOLVER_FAILED",
    );
  });

  it("does NOT render error chip on non-failed states", () => {
    const nonFailed: PipelineStepSummary["state"][] = [
      "pending",
      "running",
      "complete",
      "cancelled",
    ];
    for (const state of nonFailed) {
      const { unmount } = render(
        <PipelineCard
          step={makeStep({
            state,
            error_code: "WHATEVER",
            error_message: "should not show",
          })}
        />,
      );
      expect(screen.queryByTestId("pipeline-card-error")).toBeNull();
      unmount();
    }
  });

  it("running card carries a rainbow-gradient background-image on the name", () => {
    render(<PipelineCard step={makeStep({ state: "running" })} />);
    const name = screen.getByTestId("pipeline-card-name");
    // jsdom resolves inline styles; the rainbow gradient is the only
    // backgroundImage we set, so its presence is a sufficient signal.
    const bg = (name as HTMLElement).style.backgroundImage;
    expect(bg).toContain("linear-gradient");
  });

  it("complete card has a green-tint background", () => {
    render(<PipelineCard step={makeStep({ state: "complete" })} />);
    const card = screen.getByTestId("pipeline-card");
    const bg = (card as HTMLElement).style.background;
    // The 40,200,100 RGB is the green tint per the memory spec.
    expect(bg).toContain("40, 200, 100");
  });

  it("failed card has a red-tint background", () => {
    render(<PipelineCard step={makeStep({ state: "failed" })} />);
    const card = screen.getByTestId("pipeline-card");
    const bg = (card as HTMLElement).style.background;
    expect(bg).toContain("220, 60, 60");
  });
});

// --- data-state attribute (preserved for tests + e2e selectors) ----------- //

describe("PipelineCard — data-state attribute", () => {
  const allStates: PipelineStepSummary["state"][] = [
    "pending",
    "running",
    "complete",
    "failed",
    "cancelled",
  ];

  allStates.forEach((state) => {
    it(`card has data-state='${state}'`, () => {
      render(<PipelineCard step={makeStep({ state })} />);
      expect(screen.getByTestId("pipeline-card")).toHaveAttribute(
        "data-state",
        state,
      );
    });
  });
});

// --- Multiple cards stacked (preserves call order) ----------------------- //

describe("PipelineCard — multiple cards", () => {
  it("renders multiple cards in the order provided", () => {
    const steps: PipelineStepSummary[] = [
      { step_id: "s1", name: "fetch_dem", tool_name: "t1", state: "complete" },
      { step_id: "s2", name: "build_sfincs_model", tool_name: "t2", state: "running" },
      { step_id: "s3", name: "run_sfincs", tool_name: "t3", state: "pending" },
    ];
    const { container } = render(
      <div>
        {steps.map((s) => (
          <PipelineCard key={s.step_id} step={s} />
        ))}
      </div>,
    );
    const cards = container.querySelectorAll("[data-testid='pipeline-card']");
    expect(cards).toHaveLength(3);
    expect(cards[0]!.getAttribute("data-step-id")).toBe("s1");
    expect(cards[1]!.getAttribute("data-step-id")).toBe("s2");
    expect(cards[2]!.getAttribute("data-step-id")).toBe("s3");
  });
});

// --- Humanized step names (job-0173 Part 1) ----------------------------- //

describe("PipelineCard — humanized step labels (job-0173 + job-0294)", () => {
  it("renders 'llm_generation' as 'Thinking…' while running", () => {
    render(<PipelineCard step={makeStep({ state: "running", name: "llm_generation" })} />);
    const label = screen.getByTestId("pipeline-card-name");
    expect(label).toHaveTextContent("Thinking…");
    expect(label.textContent).not.toContain("llm_generation");
  });

  it("uses the humanized label as the tooltip title too (no internal term leaks)", () => {
    render(<PipelineCard step={makeStep({ state: "running", name: "llm_generation" })} />);
    const label = screen.getByTestId("pipeline-card-name");
    expect(label.getAttribute("title")).toBe("Thinking…");
  });

  it("shows the present-tense RUNNING label for a known tool", () => {
    render(<PipelineCard step={makeStep({ state: "running", name: "fetch_dem" })} />);
    const label = screen.getByTestId("pipeline-card-name");
    expect(label).toHaveTextContent("Fetching DEM…");
    expect(label.textContent).not.toContain("fetch_dem");
  });

  it("shows the terminal COMPLETE label for a known tool", () => {
    render(<PipelineCard step={makeStep({ state: "complete", name: "fetch_dem" })} />);
    expect(screen.getByTestId("pipeline-card-name")).toHaveTextContent("Loaded DEM");
  });

  it("uses the running label for pending / failed / cancelled (verb describes the attempt)", () => {
    for (const state of ["pending", "failed", "cancelled"] as const) {
      const { unmount } = render(
        <PipelineCard step={makeStep({ state, name: "run_model_flood_scenario" })} />,
      );
      expect(screen.getByTestId("pipeline-card-name")).toHaveTextContent(
        "Modeling flood [SFINCS]…",
      );
      unmount();
    }
  });

  it("NEVER renders raw snake_case for an unmapped tool — Title-Case fallback", () => {
    render(<PipelineCard step={makeStep({ state: "running", name: "fetch_river_widths" })} />);
    const label = screen.getByTestId("pipeline-card-name");
    expect(label).toHaveTextContent("Fetch River Widths…");
    expect(label.textContent).not.toContain("fetch_river_widths");
  });

  it("unmapped tool COMPLETE drops the trailing ellipsis", () => {
    render(<PipelineCard step={makeStep({ state: "complete", name: "fetch_river_widths" })} />);
    expect(screen.getByTestId("pipeline-card-name")).toHaveTextContent("Fetch River Widths");
  });
});

// --- Tool timer (job-0264) ----------------------------------------------- //
//
// ELEVATED requirement: running cards show a live (m:ss) ticker; terminal
// cards show the AUTHORITATIVE step.duration_ms.

describe("formatDuration (job-0264)", () => {
  it.each([
    [0, "0:00"],
    [1, "0:00"], // sub-second floors to 0:00
    [999, "0:00"],
    [1000, "0:01"],
    [9000, "0:09"],
    [60_000, "1:00"],
    [154_000, "2:34"], // the memory-spec example
    [600_000, "10:00"],
    [3_600_000, "60:00"], // hours roll into minutes (no leading-hours field)
    [-5000, "0:00"], // negative clamps to 0:00
  ])("formats %ims as %s", (ms, expected) => {
    expect(formatDuration(ms)).toBe(expected);
  });
});

describe("PipelineCard — tool timer (job-0264)", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("running card shows a live ticker that advances ~1s/tick", () => {
    vi.useFakeTimers();
    const anchor = new Date("2026-06-10T12:00:00.000Z").getTime();
    vi.setSystemTime(anchor);

    render(
      <PipelineCard
        step={makeStep({
          state: "running",
          started_at: "2026-06-10T12:00:00.000Z",
        })}
      />,
    );

    // First paint anchors at 0:00.
    const timer = screen.getByTestId("pipeline-card-timer");
    expect(timer).toHaveTextContent("0:00");
    expect(timer.getAttribute("data-authoritative")).toBe("false");

    // advanceTimersByTime advances Date.now() under fake timers AND fires the
    // 1s interval — don't also call setSystemTime or the clock double-counts.
    act(() => {
      vi.advanceTimersByTime(3000);
    });
    expect(screen.getByTestId("pipeline-card-timer")).toHaveTextContent("0:03");

    // Advance to 1m05s total (62s more).
    act(() => {
      vi.advanceTimersByTime(62_000);
    });
    expect(screen.getByTestId("pipeline-card-timer")).toHaveTextContent("1:05");
  });

  it("running ticker anchors on started_at (elapsed reflects server time, not mount time)", () => {
    vi.useFakeTimers();
    const started = new Date("2026-06-10T12:00:00.000Z").getTime();
    // Mount 40s AFTER the tool started (simulates a reconnect / late render).
    vi.setSystemTime(started + 40_000);

    render(
      <PipelineCard
        step={makeStep({
          state: "running",
          started_at: "2026-06-10T12:00:00.000Z",
        })}
      />,
    );

    // The ticker should read 0:40, not 0:00 — it counts from started_at.
    expect(screen.getByTestId("pipeline-card-timer")).toHaveTextContent("0:40");
  });

  it("completed card shows the AUTHORITATIVE duration_ms (locked, no ticking)", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-06-10T12:10:00.000Z").getTime());

    render(
      <PipelineCard
        step={makeStep({
          state: "complete",
          started_at: "2026-06-10T12:00:00.000Z",
          completed_at: "2026-06-10T12:02:34.000Z",
          duration_ms: 154_000,
        })}
      />,
    );

    const timer = screen.getByTestId("pipeline-card-timer");
    expect(timer).toHaveTextContent("2:34");
    expect(timer.getAttribute("data-authoritative")).toBe("true");

    // Advancing time does NOT change a terminal card's duration.
    act(() => {
      vi.advanceTimersByTime(10_000);
    });
    expect(screen.getByTestId("pipeline-card-timer")).toHaveTextContent("2:34");
  });

  it("failed card shows its authoritative duration_ms too", () => {
    render(
      <PipelineCard
        step={makeStep({
          state: "failed",
          duration_ms: 5_500,
          error_code: "UPSTREAM_API_ERROR",
        })}
      />,
    );
    const timer = screen.getByTestId("pipeline-card-timer");
    expect(timer).toHaveTextContent("0:05");
    expect(timer.getAttribute("data-authoritative")).toBe("true");
  });

  it("cancelled card shows its authoritative duration_ms", () => {
    render(
      <PipelineCard
        step={makeStep({ state: "cancelled", duration_ms: 12_000 })}
      />,
    );
    expect(screen.getByTestId("pipeline-card-timer")).toHaveTextContent("0:12");
  });

  it("renders 0:00 for a sub-second completed tool (duration_ms == 0, honest not hidden)", () => {
    render(
      <PipelineCard step={makeStep({ state: "complete", duration_ms: 0 })} />,
    );
    expect(screen.getByTestId("pipeline-card-timer")).toHaveTextContent("0:00");
  });

  it("pending card shows NO timer (nothing to count yet)", () => {
    render(<PipelineCard step={makeStep({ state: "pending" })} />);
    expect(screen.queryByTestId("pipeline-card-timer")).toBeNull();
  });

  it("terminal card WITHOUT duration_ms shows no timer (older agent / never fabricated)", () => {
    // No duration_ms field at all → the card must not invent a number.
    render(<PipelineCard step={makeStep({ state: "complete" })} />);
    expect(screen.queryByTestId("pipeline-card-timer")).toBeNull();
  });

  it("running card shows BOTH the ticker and the spinner", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-06-10T12:00:00.000Z").getTime());
    render(
      <PipelineCard
        step={makeStep({ state: "running", started_at: "2026-06-10T12:00:00.000Z" })}
      />,
    );
    expect(screen.getByTestId("pipeline-card-timer")).toBeInTheDocument();
    expect(screen.getByTestId("pipeline-card-indicator")).toBeInTheDocument();
  });
});

// --- Minimum running-state dwell (F70) ----------------------------------- //
//
// A fast-failing tool used to jump straight to the red failed treatment — the
// user "never saw it run". These tests pin that a LIVE pending→failed (or
// running→failed) transition holds the perceivable running treatment for at
// least MIN_RUNNING_DWELL_MS before the card settles to its terminal visual,
// while the logical `data-state` always tells the truth.

describe("PipelineCard — minimum running dwell (F70)", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("MIN_RUNNING_DWELL_MS is a perceivable few-hundred ms", () => {
    // Long enough to be seen, short enough not to feel like an artificial stall.
    expect(MIN_RUNNING_DWELL_MS).toBeGreaterThanOrEqual(300);
    expect(MIN_RUNNING_DWELL_MS).toBeLessThanOrEqual(1000);
  });

  it("a fast-fail (pending→failed) flashes the RUNNING treatment before settling red", () => {
    vi.useFakeTimers();
    const restore = mockReducedMotion(false);
    try {
      const { rerender } = render(
        <PipelineCard step={makeStep({ step_id: "ff", state: "pending" })} />,
      );

      // The tool errors in ~0s: the very next snapshot is already terminal,
      // skipping any explicit running envelope.
      act(() => {
        rerender(
          <PipelineCard
            step={makeStep({
              step_id: "ff",
              state: "failed",
              error_code: "UPSTREAM_API_ERROR",
              duration_ms: 0,
            })}
          />,
        );
      });

      // During the dwell the card PAINTS running: spinner present, rainbow
      // gradient on the label, no error chip yet.
      expect(screen.getByTestId("pipeline-card-indicator")).toBeInTheDocument();
      const name = screen.getByTestId("pipeline-card-name") as HTMLElement;
      expect(name.style.backgroundImage).toContain("linear-gradient");
      expect(screen.queryByTestId("pipeline-card-error")).toBeNull();

      // ...but the LOGICAL state already reports the truth for e2e selectors.
      expect(screen.getByTestId("pipeline-card")).toHaveAttribute(
        "data-state",
        "failed",
      );

      // After the dwell elapses the card settles to the failed treatment:
      // spinner gone (animation terminated), red tint, error chip shown.
      act(() => {
        vi.advanceTimersByTime(MIN_RUNNING_DWELL_MS + 10);
      });
      expect(screen.queryByTestId("pipeline-card-indicator")).toBeNull();
      const card = screen.getByTestId("pipeline-card") as HTMLElement;
      expect(card.style.background).toContain("220, 60, 60");
      expect(screen.getByTestId("pipeline-card-error")).toHaveTextContent(
        "UPSTREAM_API_ERROR",
      );
    } finally {
      restore();
    }
  });

  it("a fast-fail under prefers-reduced-motion still shows a clear running→failed visual (no rainbow)", () => {
    vi.useFakeTimers();
    const restore = mockReducedMotion(true);
    try {
      const { rerender } = render(
        <PipelineCard step={makeStep({ step_id: "rm", state: "pending" })} />,
      );
      act(() => {
        rerender(
          <PipelineCard
            step={makeStep({
              step_id: "rm",
              state: "failed",
              error_code: "TOOL_TIMEOUT",
              duration_ms: 0,
            })}
          />,
        );
      });

      // Running treatment is perceivable: a STATIC dot indicator (not the
      // rainbow gradient) — distinct from the failed state which has none.
      const indicator = screen.getByTestId("pipeline-card-indicator");
      expect(indicator.getAttribute("data-variant")).toBe("static-dot");
      const name = screen.getByTestId("pipeline-card-name") as HTMLElement;
      expect(name.style.backgroundImage).not.toContain("linear-gradient");

      // Settles to failed once the dwell elapses (indicator gone, red tint).
      act(() => {
        vi.advanceTimersByTime(MIN_RUNNING_DWELL_MS + 10);
      });
      expect(screen.queryByTestId("pipeline-card-indicator")).toBeNull();
      expect(
        (screen.getByTestId("pipeline-card") as HTMLElement).style.background,
      ).toContain("220, 60, 60");
    } finally {
      restore();
    }
  });

  it("a running→failed transition holds the running treatment for the remaining dwell", () => {
    vi.useFakeTimers();
    const restore = mockReducedMotion(false);
    try {
      const { rerender } = render(
        <PipelineCard
          step={makeStep({
            step_id: "rf",
            state: "running",
            started_at: "2026-06-10T12:00:00.000Z",
          })}
        />,
      );
      // Visible-running for only 100ms, then fails (< the dwell).
      act(() => {
        vi.advanceTimersByTime(100);
      });
      act(() => {
        rerender(
          <PipelineCard
            step={makeStep({
              step_id: "rf",
              state: "failed",
              error_code: "SOLVER_FAILED",
              duration_ms: 100,
            })}
          />,
        );
      });
      // Still painting running (the remaining dwell hasn't elapsed).
      expect(screen.getByTestId("pipeline-card-indicator")).toBeInTheDocument();

      // After the remaining dwell it settles to failed.
      act(() => {
        vi.advanceTimersByTime(MIN_RUNNING_DWELL_MS);
      });
      expect(screen.queryByTestId("pipeline-card-indicator")).toBeNull();
      expect(screen.getByTestId("pipeline-card-error")).toHaveTextContent(
        "SOLVER_FAILED",
      );
    } finally {
      restore();
    }
  });

  it("a card that has ALREADY been running long enough settles immediately (no extra dwell)", () => {
    vi.useFakeTimers();
    const restore = mockReducedMotion(false);
    try {
      const { rerender } = render(
        <PipelineCard
          step={makeStep({
            step_id: "slow",
            state: "running",
            started_at: "2026-06-10T12:00:00.000Z",
          })}
        />,
      );
      // Run well past the dwell, then complete.
      act(() => {
        vi.advanceTimersByTime(MIN_RUNNING_DWELL_MS + 5_000);
      });
      act(() => {
        rerender(
          <PipelineCard
            step={makeStep({
              step_id: "slow",
              state: "complete",
              duration_ms: 5_450,
            })}
          />,
        );
      });
      // No artificial dwell — settles to complete right away (green tint, no
      // spinner). The rainbow was already visible for seconds.
      expect(screen.queryByTestId("pipeline-card-indicator")).toBeNull();
      expect(
        (screen.getByTestId("pipeline-card") as HTMLElement).style.background,
      ).toContain("40, 200, 100");
    } finally {
      restore();
    }
  });

  it("a card MOUNTED already-terminal (history replay) settles immediately — no feigned running", () => {
    // Replayed / rehydrated steps did not run live in this session, so there is
    // nothing to retroactively "show running" for: paint terminal at once.
    render(
      <PipelineCard
        step={makeStep({
          step_id: "replay",
          state: "failed",
          error_code: "DEM_SOURCE_UNAVAILABLE",
          duration_ms: 0,
        })}
      />,
    );
    expect(screen.queryByTestId("pipeline-card-indicator")).toBeNull();
    expect(
      (screen.getByTestId("pipeline-card") as HTMLElement).style.background,
    ).toContain("220, 60, 60");
    expect(screen.getByTestId("pipeline-card-error")).toHaveTextContent(
      "DEM_SOURCE_UNAVAILABLE",
    );
  });

  it("the authoritative terminal duration_ms is shown immediately, even during the dwell", () => {
    // The TIMER value tracks logical truth (job-0264) regardless of the visual
    // dwell — only the running/failed treatment is deferred, never the number.
    vi.useFakeTimers();
    const { rerender } = render(
      <PipelineCard step={makeStep({ step_id: "td", state: "pending" })} />,
    );
    act(() => {
      rerender(
        <PipelineCard
          step={makeStep({
            step_id: "td",
            state: "failed",
            duration_ms: 0,
            error_code: "TOOL_PARAMS_INVALID",
          })}
        />,
      );
    });
    const timer = screen.getByTestId("pipeline-card-timer");
    expect(timer).toHaveTextContent("0:00");
    expect(timer.getAttribute("data-authoritative")).toBe("true");
  });
});
