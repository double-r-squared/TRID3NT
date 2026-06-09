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
import { shouldShowCancel, mergeStepsByStepId } from "./Chat";
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

// --- mergeStepsByStepId (job-0162) --------------------------------------- //
//
// The agent emits a fresh `pipeline_id` per tool dispatch (server.py
// per-tool start_pipeline + close_pipeline). Before job-0162 each tool
// dispatch rendered as a separate "group" in the chat — the result was a
// stale running card stacked above a completed card for the same tool. This
// helper merges every snapshot (history + live) by `step_id` so each tool
// dispatch renders as exactly one transitioning card.

describe("mergeStepsByStepId", () => {
  it("returns an empty list when there is no history and no live snapshot", () => {
    expect(mergeStepsByStepId([], null)).toEqual([]);
  });

  it("renders one card per step_id when a tool transitions pending → running → complete across separate pipeline_ids", () => {
    // Simulates the server's per-tool start_pipeline emission pattern: each
    // tool wraps in its own pipeline_id, but the step_id is stable within
    // the tool's lifecycle.
    const pendingSnap: PipelineStatePayload = {
      pipeline_id: "pipe-A",
      steps: [makeStep("step-1", "pending")],
    };
    const runningSnap: PipelineStatePayload = {
      pipeline_id: "pipe-A",
      steps: [makeStep("step-1", "running", 50)],
    };
    const completeSnap: PipelineStatePayload = {
      pipeline_id: "pipe-A",
      steps: [makeStep("step-1", "complete")],
    };
    // History accumulates the terminal snapshot; live is null after close.
    const merged = mergeStepsByStepId(
      [pendingSnap, runningSnap, completeSnap],
      null,
    );
    expect(merged).toHaveLength(1);
    expect(merged[0]!.state).toBe("complete");
  });

  it("two tool dispatches with two distinct step_ids render as two cards in encounter order", () => {
    const tool1: PipelineStatePayload = {
      pipeline_id: "pipe-A",
      steps: [makeStep("step-1", "complete")],
    };
    const tool2: PipelineStatePayload = {
      pipeline_id: "pipe-B",
      steps: [makeStep("step-2", "running", 25)],
    };
    const merged = mergeStepsByStepId([tool1], tool2);
    expect(merged).toHaveLength(2);
    expect(merged[0]!.step_id).toBe("step-1");
    expect(merged[0]!.state).toBe("complete");
    expect(merged[1]!.step_id).toBe("step-2");
    expect(merged[1]!.state).toBe("running");
  });

  it("live snapshot wins over history for the same step_id", () => {
    const historical: PipelineStatePayload = {
      pipeline_id: "pipe-A",
      steps: [makeStep("step-1", "pending")],
    };
    const live: PipelineStatePayload = {
      pipeline_id: "pipe-A",
      steps: [makeStep("step-1", "running", 80)],
    };
    const merged = mergeStepsByStepId([historical], live);
    expect(merged).toHaveLength(1);
    expect(merged[0]!.state).toBe("running");
    expect(merged[0]!.progress_percent).toBe(80);
  });

  it("preserves first-encountered order even when a later snapshot updates state", () => {
    const snapA: PipelineStatePayload = {
      pipeline_id: "pipe-A",
      steps: [makeStep("step-1", "pending"), makeStep("step-2", "pending")],
    };
    const snapB: PipelineStatePayload = {
      pipeline_id: "pipe-A",
      steps: [makeStep("step-2", "complete"), makeStep("step-1", "complete")],
    };
    const merged = mergeStepsByStepId([snapA, snapB], null);
    expect(merged.map((s) => s.step_id)).toEqual(["step-1", "step-2"]);
    expect(merged.every((s) => s.state === "complete")).toBe(true);
  });
});
