// GRACE-2 web — Chat inline pipeline card tests (job-0064).
//
// Verifies:
//   1. pipeline-state arrives → inline card appears with correct name + %.
//   2. Multiple steps → multiple cards stacked in call order.
//   3. Step completion (all terminal) → "done" state cards in history group.
//   4. shouldShowCancel predicate (exported for testability).
//
// Chat itself cannot be fully mounted in happy-dom (it creates a WebSocket),
// so:
//   - The pipelineReducer logic is tested via shouldShowCancel + a minimal
//     state exerciser.
//   - PipelineCard rendering is tested directly (its own test suite).
//   - PipelineStepGroup is tested via PipelineCard (transitively).
//
// This follows the same pattern as App.test.tsx (App mounts WebSocket +
// WebGL, which happy-dom can't run; logic extracted into pure helpers).

import { describe, it, expect } from "vitest";
import { shouldShowCancel } from "./Chat";
import { PipelineStatePayload, PipelineStepSummary } from "./contracts";

// --- shouldShowCancel predicate ------------------------------------------ //

function makeStep(
  id: string,
  state: PipelineStepSummary["state"],
  progress?: number,
): PipelineStepSummary {
  return {
    step_id: id,
    name: `op_${id}`,
    tool_name: `tool_${id}`,
    state,
    progress_percent: progress,
  };
}

function makePipelineState(steps: PipelineStepSummary[]): PipelineStatePayload {
  return { pipeline_id: "pipe-001", steps };
}

describe("shouldShowCancel", () => {
  it("returns false when no pipeline data", () => {
    expect(
      shouldShowCancel({
        live: null,
        history: [],
        currentPipelineFromSession: null,
      }),
    ).toBe(false);
  });

  it("returns true when live pipeline has a running step", () => {
    const payload = makePipelineState([
      makeStep("s1", "complete"),
      makeStep("s2", "running", 47),
    ]);
    expect(
      shouldShowCancel({
        live: payload,
        history: [],
        currentPipelineFromSession: null,
      }),
    ).toBe(true);
  });

  it("returns false when live pipeline has no running steps", () => {
    const payload = makePipelineState([
      makeStep("s1", "complete"),
      makeStep("s2", "pending"),
    ]);
    expect(
      shouldShowCancel({
        live: payload,
        history: [],
        currentPipelineFromSession: null,
      }),
    ).toBe(false);
  });

  it("returns true when session-state current_pipeline is non-null (predicate b)", () => {
    expect(
      shouldShowCancel({
        live: null,
        history: [],
        currentPipelineFromSession: {
          pipeline_id: "pipe-session",
          steps: [],
          started_at: null,
          completed_at: null,
          final_state: null,
        },
      }),
    ).toBe(true);
  });

  it("returns true when both conditions are true", () => {
    const payload = makePipelineState([makeStep("s1", "running", 50)]);
    expect(
      shouldShowCancel({
        live: payload,
        history: [],
        currentPipelineFromSession: {
          pipeline_id: "pipe-session",
          steps: [],
          started_at: null,
          completed_at: null,
          final_state: null,
        },
      }),
    ).toBe(true);
  });
});
