// GRACE-2 web - ResolutionPickerCard tests (#154 pre-run granularity gate).
//
// Verifies the in-chat mesh-resolution confirm card:
//   1. Renders the suggested-rung metadata (resolution / cells / ETA / vCPUs /
//      compute class / Spot label) from the GranularitySuggestion.
//   2. Picking a finer / coarser rung LIVE-recomputes the displayed cells + ETA
//      client-side (area-invariant scaling off the suggested-rung baseline).
//   3. Confirm UNCHANGED (chosen == suggested) -> decision "proceed", revised null.
//   4. Confirm AFTER override -> decision "narrow_scope", revised
//      { [resolution_param]: chosen }.
//   5. Cancel -> decision "cancel", revised null.
//   6. The card LOCKS + FOLDS after a decision so it cannot be re-answered.
//   7. Pure recompute helpers (area-invariant cells + proportional ETA).

import { describe, expect, it, vi, afterEach } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import {
  ResolutionPickerCard,
  estimateCellsForResolution,
  estimateSolveSecondsForResolution,
} from "./ResolutionPickerCard";
import {
  GranularitySuggestion,
  PayloadWarningEnvelopePayload,
} from "../contracts";

afterEach(() => {
  cleanup();
});

function granularity(
  overrides: Partial<GranularitySuggestion> = {},
): GranularitySuggestion {
  return {
    engine: "swmm",
    resolution_param: "target_resolution_m",
    suggested_resolution_m: 20,
    resolution_choices: [10, 20, 40],
    estimated_active_cells: 40000,
    estimated_solve_seconds: 120,
    vcpus: 8,
    compute_class: "c7i.2xlarge",
    cell_cap: 250000,
    coarsened: false,
    reason: "Balanced resolution for the requested area.",
    spot_label: "c7i.2xlarge (Spot)",
    ...overrides,
  };
}

function warning(
  g: GranularitySuggestion | null,
  overrides: Partial<PayloadWarningEnvelopePayload> = {},
): PayloadWarningEnvelopePayload {
  return {
    envelope_type: "tool-payload-warning",
    warning_id: "W-GRAN-1",
    tool_name: "run_model_flood_scenario",
    tool_args: { target_resolution_m: 20 },
    estimated_mb: 5,
    threshold_mb: 25,
    recommendation: "Confirm the mesh resolution before the solver run.",
    options: ["proceed", "narrow_scope", "cancel"],
    granularity: g,
    ...overrides,
  };
}

describe("ResolutionPickerCard - rendering", () => {
  it("renders the suggested-rung metadata numbers", () => {
    const g = granularity();
    render(
      <ResolutionPickerCard
        warning={warning(g)}
        granularity={g}
        onDecide={vi.fn()}
      />,
    );
    expect(screen.getByTestId("resolution-picker-suggested-m")).toHaveTextContent(
      "20 m",
    );
    // 40000 cells -> "40k"
    expect(screen.getByTestId("resolution-picker-cells")).toHaveTextContent("~40k");
    // 120s -> "~2:00" (>= 90s rolls into m:ss)
    expect(screen.getByTestId("resolution-picker-eta")).toHaveTextContent("~2:00");
    expect(screen.getByTestId("resolution-picker-vcpus")).toHaveTextContent("8");
    expect(
      screen.getByTestId("resolution-picker-compute-class"),
    ).toHaveTextContent("c7i.2xlarge");
    expect(screen.getByTestId("resolution-picker-spot-label")).toHaveTextContent(
      "c7i.2xlarge (Spot)",
    );
  });

  it("omits the Spot label row when spot_label is null", () => {
    const g = granularity({ spot_label: null });
    render(
      <ResolutionPickerCard
        warning={warning(g)}
        granularity={g}
        onDecide={vi.fn()}
      />,
    );
    expect(
      screen.queryByTestId("resolution-picker-spot-label"),
    ).not.toBeInTheDocument();
  });

  it("prefixes the caption with 'Coarsened' when coarsened is true", () => {
    const g = granularity({ coarsened: true, reason: "Area too large at 10 m." });
    render(
      <ResolutionPickerCard
        warning={warning(g)}
        granularity={g}
        onDecide={vi.fn()}
      />,
    );
    expect(screen.getByTestId("resolution-picker-reason")).toHaveTextContent(
      "Coarsened - Area too large at 10 m.",
    );
  });

  it("renders one chip per resolution choice and marks the suggested rung", () => {
    const g = granularity();
    render(
      <ResolutionPickerCard
        warning={warning(g)}
        granularity={g}
        onDecide={vi.fn()}
      />,
    );
    expect(screen.getByTestId("resolution-picker-chip-10")).toBeInTheDocument();
    expect(screen.getByTestId("resolution-picker-chip-20")).toBeInTheDocument();
    expect(screen.getByTestId("resolution-picker-chip-40")).toBeInTheDocument();
    // Default selection is the suggested rung.
    expect(screen.getByTestId("resolution-picker-chip-20")).toHaveAttribute(
      "data-selected",
      "true",
    );
    expect(screen.getByTestId("resolution-picker-chip-20")).toHaveTextContent(
      "(suggested)",
    );
  });
});

describe("ResolutionPickerCard - live recompute on chip change", () => {
  it("live-updates cells + ETA when a finer rung is picked", () => {
    const g = granularity(); // 20 m / 40000 cells / 120s
    render(
      <ResolutionPickerCard
        warning={warning(g)}
        granularity={g}
        onDecide={vi.fn()}
      />,
    );
    // Initial readout at the suggested 20 m rung.
    expect(
      screen.getByTestId("resolution-picker-readout-cells"),
    ).toHaveTextContent("~40k");
    expect(
      screen.getByTestId("resolution-picker-readout-eta"),
    ).toHaveTextContent("~2:00");

    // Pick the finer 10 m rung -> (20/10)^2 = 4x cells = 160000 ("160k") and
    // 4x ETA = 480s ("~8:00").
    fireEvent.click(screen.getByTestId("resolution-picker-chip-10"));
    expect(
      screen.getByTestId("resolution-picker-readout-cells"),
    ).toHaveTextContent("~160k");
    expect(
      screen.getByTestId("resolution-picker-readout-eta"),
    ).toHaveTextContent("~8:00");
  });

  it("live-updates cells + ETA when a coarser rung is picked", () => {
    const g = granularity(); // 20 m / 40000 cells / 120s
    render(
      <ResolutionPickerCard
        warning={warning(g)}
        granularity={g}
        onDecide={vi.fn()}
      />,
    );
    // Pick the coarser 40 m rung -> (20/40)^2 = 0.25x cells = 10000 ("10k") and
    // 0.25x ETA = 30s ("~30s").
    fireEvent.click(screen.getByTestId("resolution-picker-chip-40"));
    expect(
      screen.getByTestId("resolution-picker-readout-cells"),
    ).toHaveTextContent("~10k");
    expect(
      screen.getByTestId("resolution-picker-readout-eta"),
    ).toHaveTextContent("~30s");
  });
});

describe("ResolutionPickerCard - decisions", () => {
  it("Confirm UNCHANGED -> proceed with null revised_args", () => {
    const g = granularity();
    const onDecide = vi.fn();
    render(
      <ResolutionPickerCard
        warning={warning(g)}
        granularity={g}
        onDecide={onDecide}
      />,
    );
    fireEvent.click(screen.getByTestId("resolution-picker-confirm"));
    expect(onDecide).toHaveBeenCalledTimes(1);
    expect(onDecide).toHaveBeenCalledWith("proceed", null);
  });

  it("Confirm AFTER override -> narrow_scope with { resolution_param: chosen }", () => {
    const g = granularity(); // resolution_param = target_resolution_m
    const onDecide = vi.fn();
    render(
      <ResolutionPickerCard
        warning={warning(g)}
        granularity={g}
        onDecide={onDecide}
      />,
    );
    fireEvent.click(screen.getByTestId("resolution-picker-chip-10"));
    fireEvent.click(screen.getByTestId("resolution-picker-confirm"));
    expect(onDecide).toHaveBeenCalledTimes(1);
    expect(onDecide).toHaveBeenCalledWith("narrow_scope", {
      target_resolution_m: 10,
    });
  });

  it("uses the engine-specific resolution_param key for the override", () => {
    const g = granularity({
      engine: "sfincs",
      resolution_param: "grid_resolution_m",
      suggested_resolution_m: 100,
      resolution_choices: [50, 100, 200],
      estimated_active_cells: 50000,
      estimated_solve_seconds: 200,
    });
    const onDecide = vi.fn();
    render(
      <ResolutionPickerCard
        warning={warning(g)}
        granularity={g}
        onDecide={onDecide}
      />,
    );
    fireEvent.click(screen.getByTestId("resolution-picker-chip-50"));
    fireEvent.click(screen.getByTestId("resolution-picker-confirm"));
    expect(onDecide).toHaveBeenCalledWith("narrow_scope", {
      grid_resolution_m: 50,
    });
  });

  it("Cancel -> cancel with null revised_args", () => {
    const g = granularity();
    const onDecide = vi.fn();
    render(
      <ResolutionPickerCard
        warning={warning(g)}
        granularity={g}
        onDecide={onDecide}
      />,
    );
    fireEvent.click(screen.getByTestId("resolution-picker-cancel"));
    expect(onDecide).toHaveBeenCalledTimes(1);
    expect(onDecide).toHaveBeenCalledWith("cancel", null);
  });
});

describe("ResolutionPickerCard - lock + fold after a decision", () => {
  it("folds to the compact summary and cannot be re-answered after Confirm", () => {
    const g = granularity();
    const onDecide = vi.fn();
    render(
      <ResolutionPickerCard
        warning={warning(g)}
        granularity={g}
        onDecide={onDecide}
      />,
    );
    fireEvent.click(screen.getByTestId("resolution-picker-confirm"));
    // Active controls are gone - the card folded.
    expect(
      screen.queryByTestId("resolution-picker-confirm"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("resolution-picker-chips"),
    ).not.toBeInTheDocument();
    // The fold shows the resolved summary.
    const card = screen.getByTestId("resolution-picker-card");
    expect(card).toHaveAttribute("data-resolved", "proceed");
    expect(
      screen.getByTestId("resolution-picker-resolved"),
    ).toHaveTextContent("Mesh resolution confirmed");
    // onDecide fired exactly once (no re-answer possible).
    expect(onDecide).toHaveBeenCalledTimes(1);
  });

  it("seeds the folded state from an externally-recorded resolution", () => {
    const g = granularity();
    render(
      <ResolutionPickerCard
        warning={warning(g)}
        granularity={g}
        onDecide={vi.fn()}
        resolved="narrow_scope"
      />,
    );
    // Mounts already folded (Case switch + return).
    expect(
      screen.queryByTestId("resolution-picker-confirm"),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("resolution-picker-card")).toHaveAttribute(
      "data-resolved",
      "narrow_scope",
    );
    expect(
      screen.getByTestId("resolution-picker-resolved"),
    ).toHaveTextContent("Mesh resolution overridden");
  });
});

describe("ResolutionPickerCard - pure recompute helpers", () => {
  it("estimateCellsForResolution scales by the inverse square of the rung", () => {
    const g = granularity(); // 20 m / 40000 cells
    expect(estimateCellsForResolution(g, 20)).toBe(40000); // suggested -> exact
    expect(estimateCellsForResolution(g, 10)).toBe(160000); // 4x finer
    expect(estimateCellsForResolution(g, 40)).toBe(10000); // 0.25x coarser
  });

  it("estimateSolveSecondsForResolution scales proportionally to the cell ratio", () => {
    const g = granularity(); // 120s baseline at 40000 cells
    expect(estimateSolveSecondsForResolution(g, 20)).toBe(120);
    expect(estimateSolveSecondsForResolution(g, 10)).toBe(480); // 4x cells
    expect(estimateSolveSecondsForResolution(g, 40)).toBe(30); // 0.25x cells
  });
});
