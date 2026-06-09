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

import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { PipelineCard } from "./PipelineCard";
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
