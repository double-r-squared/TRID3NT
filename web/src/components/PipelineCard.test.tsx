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
import { PipelineCard, formatDuration } from "./PipelineCard";
import { PipelineStepSummary } from "../contracts";

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
  it("renders the step name for every state", () => {
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
      expect(screen.getByTestId("pipeline-card-name")).toHaveTextContent(
        `op_${state}`,
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

  it("does NOT render legacy glyphs ✓ ✗ ⊘ or '…' in any state", () => {
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
      expect(text).not.toMatch(/✓|✗|⊘|…/);
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

describe("PipelineCard — humanized step labels", () => {
  it("renders 'llm_generation' as 'Thinking…' for the user", () => {
    render(<PipelineCard step={makeStep({ state: "running", name: "llm_generation" })} />);
    const label = screen.getByTestId("pipeline-card-name");
    expect(label).toHaveTextContent("Thinking…");
    expect(label.textContent).not.toContain("llm_generation");
  });

  it("uses the humanized label as the tooltip title too (no internal term leaks)", () => {
    render(<PipelineCard step={makeStep({ state: "complete", name: "llm_generation" })} />);
    const label = screen.getByTestId("pipeline-card-name");
    expect(label.getAttribute("title")).toBe("Thinking…");
  });

  it("preserves engineer-named tool labels (passthrough for unknown names)", () => {
    render(<PipelineCard step={makeStep({ state: "complete", name: "fetch_dem" })} />);
    expect(screen.getByTestId("pipeline-card-name")).toHaveTextContent("fetch_dem");
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
