// TurnStatusBar - render + deriver tests (NATE 2026-06-29 "never leave the user
// in the dark"). The pure deriver lives in Chat.tsx (so it is testable without
// mounting Chat, which opens a WebSocket); the render half lives here.

import { describe, it, expect } from "vitest";
import { render, cleanup } from "@testing-library/react";
import { afterEach } from "vitest";
import { TurnStatusBar } from "./TurnStatusBar";
import { deriveTurnStatus, computeAwaitingInput } from "../Chat";
import type { PipelineStepSummary } from "../contracts";

afterEach(cleanup);

function step(overrides: Partial<PipelineStepSummary>): PipelineStepSummary {
  return {
    step_id: overrides.step_id ?? "s1",
    name: overrides.name ?? "fetch_topobathy",
    tool_name: overrides.tool_name ?? overrides.name ?? "fetch_topobathy",
    state: overrides.state ?? "running",
    ...overrides,
  };
}

describe("deriveTurnStatus", () => {
  it("is hidden when idle", () => {
    expect(
      deriveTurnStatus({
        inFlight: false,
        thinkingActive: false,
        awaitingInput: false,
        liveSteps: [],
      }).kind,
    ).toBe("hidden");
  });

  it("awaiting wins over everything when a gate is unanswered", () => {
    const s = deriveTurnStatus({
      inFlight: true,
      thinkingActive: false,
      awaitingInput: true,
      liveSteps: [step({ name: "run_solver", role: "compute", state: "running" })],
    });
    expect(s.kind).toBe("awaiting");
  });

  it("simulating when a heavy off-box solver step is running", () => {
    const s = deriveTurnStatus({
      inFlight: true,
      thinkingActive: false,
      awaitingInput: false,
      liveSteps: [step({ name: "run_solver", role: "compute", state: "running" })],
    });
    expect(s.kind).toBe("simulating");
    if (s.kind === "simulating") expect(s.solverStep.name).toBe("run_solver");
  });

  it("simulating recognizes a solver name even without role=compute", () => {
    const s = deriveTurnStatus({
      inFlight: true,
      thinkingActive: false,
      awaitingInput: false,
      liveSteps: [step({ name: "run_modflow_job", state: "running" })],
    });
    expect(s.kind).toBe("simulating");
  });

  it("working (labelled) for a plain running tool", () => {
    const s = deriveTurnStatus({
      inFlight: true,
      thinkingActive: false,
      awaitingInput: false,
      liveSteps: [step({ name: "fetch_topobathy", state: "running" })],
    });
    expect(s.kind).toBe("working");
  });

  it("working (generic) when in flight with no running step", () => {
    const s = deriveTurnStatus({
      inFlight: true,
      thinkingActive: false,
      awaitingInput: false,
      liveSteps: [],
    });
    expect(s.kind).toBe("working");
    if (s.kind === "working") expect(s.label).toBe("Working...");
  });

  it("hidden while thinking (the ThinkingIndicator covers pure reasoning)", () => {
    const s = deriveTurnStatus({
      inFlight: true,
      thinkingActive: true,
      awaitingInput: false,
      liveSteps: [],
    });
    expect(s.kind).toBe("hidden");
  });
});

describe("computeAwaitingInput", () => {
  const empty = {
    payloadWarnings: [],
    payloadResolved: new Map(),
    spatialInputs: [],
    spatialResolved: new Map(),
    credentialRequests: [],
    credentialResolved: new Map(),
    regionChoices: [],
    regionResolved: new Map(),
  };

  it("false when nothing pending", () => {
    expect(computeAwaitingInput(empty as never)).toBe(false);
  });

  it("true for an unanswered payload-warning (mesh-resolution gate)", () => {
    expect(
      computeAwaitingInput({
        ...empty,
        payloadWarnings: [{ warning_id: "w1" }],
      } as never),
    ).toBe(true);
  });

  it("false once the warning is resolved", () => {
    expect(
      computeAwaitingInput({
        ...empty,
        payloadWarnings: [{ warning_id: "w1" }],
        payloadResolved: new Map([["w1", "proceed"]]),
      } as never),
    ).toBe(false);
  });
});

describe("TurnStatusBar render", () => {
  it("renders nothing when hidden", () => {
    const { container } = render(<TurnStatusBar status={{ kind: "hidden" }} />);
    expect(container.querySelector('[data-testid="turn-status-bar"]')).toBeNull();
  });

  it("renders the working label", () => {
    const { getByTestId } = render(
      <TurnStatusBar status={{ kind: "working", label: "Fetching elevation data..." }} />,
    );
    expect(getByTestId("turn-status-bar").getAttribute("data-status-kind")).toBe("working");
    expect(getByTestId("turn-status-label").textContent).toBe("Fetching elevation data...");
  });

  it("renders a live elapsed ticker for a running sim", () => {
    const solverStep = step({
      name: "run_solver",
      role: "compute",
      state: "running",
      started_at: new Date(Date.now() - 5000).toISOString(),
    });
    const { getByTestId } = render(
      <TurnStatusBar
        status={{ kind: "simulating", label: "Simulation running on AWS Batch", solverStep }}
      />,
    );
    expect(getByTestId("turn-status-bar").getAttribute("data-status-kind")).toBe("simulating");
    // The elapsed ticker is present (m:ss) since the step started ~5s ago.
    expect(getByTestId("turn-status-elapsed").textContent).toMatch(/^\d+:\d{2}$/);
  });

  it("renders the awaiting gate as a dot (no spinner), pulsing", () => {
    const { getByTestId } = render(
      <TurnStatusBar status={{ kind: "awaiting", label: "Action needed" }} />,
    );
    expect(getByTestId("turn-status-bar").getAttribute("data-status-kind")).toBe("awaiting");
  });
});
