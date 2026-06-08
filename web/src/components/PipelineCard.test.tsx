// GRACE-2 web — PipelineCard unit tests (job-0064).
//
// Verifies:
//   1. pipeline-state arrives → card renders with correct operation name + %.
//   2. Multiple steps → multiple cards (tested via PipelineCard directly).
//   3. Step completion → "done" state (✓ suffix, no percentage).
//   4. Failed / cancelled states render their markers.
//   5. Error code appears on failed steps.

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

// --- Rendering tests ----------------------------------------------------- //

describe("PipelineCard — one-line format", () => {
  it("renders operation name for a running step", () => {
    render(<PipelineCard step={makeStep({ state: "running", progress_percent: 47 })} />);
    const card = screen.getByTestId("pipeline-card");
    expect(card).toBeInTheDocument();
    expect(screen.getByTestId("pipeline-card-name")).toHaveTextContent("fetch_dem");
  });

  it("renders progress % for a running step with progress_percent", () => {
    render(<PipelineCard step={makeStep({ state: "running", progress_percent: 47 })} />);
    expect(screen.getByTestId("pipeline-card-status")).toHaveTextContent("47%");
  });

  it("renders 100% for a running step at 100", () => {
    render(<PipelineCard step={makeStep({ state: "running", progress_percent: 100 })} />);
    expect(screen.getByTestId("pipeline-card-status")).toHaveTextContent("100%");
  });

  it("renders … for a running step with no progress_percent", () => {
    render(<PipelineCard step={makeStep({ state: "running" })} />);
    expect(screen.getByTestId("pipeline-card-status")).toHaveTextContent("…");
  });

  it("renders ✓ (no percentage) for a complete step — done state", () => {
    render(<PipelineCard step={makeStep({ state: "complete", progress_percent: 100 })} />);
    const status = screen.getByTestId("pipeline-card-status");
    expect(status).toHaveTextContent("✓");
    // Must NOT show a percentage in the status cell.
    expect(status.textContent).not.toMatch(/\d+%/);
  });

  it("renders ✗ for a failed step", () => {
    render(<PipelineCard step={makeStep({ state: "failed" })} />);
    expect(screen.getByTestId("pipeline-card-status")).toHaveTextContent("✗");
  });

  it("renders ⊘ for a cancelled step — Invariant 8 distinct from failed", () => {
    render(<PipelineCard step={makeStep({ state: "cancelled" })} />);
    expect(screen.getByTestId("pipeline-card-status")).toHaveTextContent("⊘");
  });

  it("renders 'pending' for a pending step with no progress", () => {
    render(<PipelineCard step={makeStep({ state: "pending" })} />);
    expect(screen.getByTestId("pipeline-card-status")).toHaveTextContent("pending");
  });

  it("renders error_code inline for a failed step", () => {
    render(
      <PipelineCard
        step={makeStep({
          state: "failed",
          error_code: "SOLVER_FAILED",
          error_message: "mesh generation error",
        })}
      />,
    );
    expect(screen.getByTestId("pipeline-card-error")).toHaveTextContent("SOLVER_FAILED");
  });

  it("does not render error block for non-failed steps", () => {
    render(<PipelineCard step={makeStep({ state: "complete" })} />);
    expect(screen.queryByTestId("pipeline-card-error")).toBeNull();
  });
});

// --- Multiple cards stacked (call order test) ----------------------------- //

describe("PipelineCard — multiple cards", () => {
  it("renders multiple cards in the order provided", () => {
    const steps: PipelineStepSummary[] = [
      { step_id: "s1", name: "fetch_dem", tool_name: "t1", state: "complete" },
      { step_id: "s2", name: "build_sfincs_model", tool_name: "t2", state: "running", progress_percent: 47 },
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

    // Verify order: step_id attribute matches expected order.
    expect(cards[0]!.getAttribute("data-step-id")).toBe("s1");
    expect(cards[1]!.getAttribute("data-step-id")).toBe("s2");
    expect(cards[2]!.getAttribute("data-step-id")).toBe("s3");
  });

  it("second card shows correct progress", () => {
    const steps: PipelineStepSummary[] = [
      { step_id: "s1", name: "fetch_dem", tool_name: "t1", state: "complete" },
      { step_id: "s2", name: "build_sfincs_model", tool_name: "t2", state: "running", progress_percent: 47 },
    ];

    render(
      <div>
        {steps.map((s) => (
          <PipelineCard key={s.step_id} step={s} />
        ))}
      </div>,
    );

    // find all status cells
    const statusCells = screen.getAllByTestId("pipeline-card-status");
    expect(statusCells[0]!).toHaveTextContent("✓"); // first step complete
    expect(statusCells[1]!).toHaveTextContent("47%"); // second step 47%
  });

  it("all terminal steps show done markers", () => {
    const steps: PipelineStepSummary[] = [
      { step_id: "s1", name: "fetch_dem", tool_name: "t1", state: "complete" },
      { step_id: "s2", name: "build_sfincs_model", tool_name: "t2", state: "complete" },
      { step_id: "s3", name: "run_sfincs", tool_name: "t3", state: "complete" },
    ];

    render(
      <div>
        {steps.map((s) => (
          <PipelineCard key={s.step_id} step={s} />
        ))}
      </div>,
    );

    const statusCells = screen.getAllByTestId("pipeline-card-status");
    statusCells.forEach((cell) => {
      expect(cell).toHaveTextContent("✓");
      expect(cell.textContent).not.toMatch(/\d+%/);
    });
  });
});

// --- data-state attribute ------------------------------------------------ //

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
