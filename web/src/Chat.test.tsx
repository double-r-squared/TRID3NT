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
import {
  shouldShowCancel,
  mergeStepsByStepId,
  forceMostRecentRunningToFailed,
  pipelineReducer,
  PipelineInlineState,
} from "./Chat";
import {
  ErrorPayload,
  PipelineStatePayload,
  PipelineStepSummary,
} from "./contracts";

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

  // job-0166 Part 3 — same (name, tool_name) across two different step_ids
  // collapses to a single card so the user sees one transitioning llm_generation
  // card, not a stale running card stacked above a completed one.
  it("collapses two cards sharing (name, tool_name) but different step_ids to a single most-recent card", () => {
    const stalePipe: PipelineStatePayload = {
      pipeline_id: "pipe-A",
      steps: [
        {
          step_id: "step-llm-1",
          name: "llm_generation",
          tool_name: "gemini_generate",
          state: "running",
        },
      ],
    };
    const completePipe: PipelineStatePayload = {
      pipeline_id: "pipe-B",
      steps: [
        {
          step_id: "step-llm-2",
          name: "llm_generation",
          tool_name: "gemini_generate",
          state: "complete",
        },
      ],
    };
    const merged = mergeStepsByStepId([stalePipe, completePipe], null);
    expect(merged).toHaveLength(1);
    expect(merged[0]!.state).toBe("complete");
    // First-encountered position is preserved (the original stale step's slot).
    expect(merged[0]!.step_id).toBe("step-llm-2");
  });

  it("does NOT collapse distinct tools — only matching (name, tool_name) pairs", () => {
    const llm: PipelineStatePayload = {
      pipeline_id: "pipe-A",
      steps: [
        {
          step_id: "step-llm",
          name: "llm_generation",
          tool_name: "gemini_generate",
          state: "complete",
        },
      ],
    };
    const fetchDem: PipelineStatePayload = {
      pipeline_id: "pipe-B",
      steps: [
        {
          step_id: "step-fetch",
          name: "fetch_dem",
          tool_name: "fetch_dem",
          state: "running",
        },
      ],
    };
    const merged = mergeStepsByStepId([llm, fetchDem], null);
    expect(merged).toHaveLength(2);
    expect(merged[0]!.name).toBe("llm_generation");
    expect(merged[1]!.name).toBe("fetch_dem");
  });
});

// --- forceMostRecentRunningToFailed (job-0166 Part 1) --------------------- //
//
// When an `error` envelope arrives without an accompanying terminal
// pipeline-state (LLM_UNAVAILABLE / tool TypeError on the agent side),
// the client must force the most-recent running step to `failed` so the
// rainbow-animated "running" card transitions to the RED no-animation
// terminal state with the typed error_code chip.

describe("forceMostRecentRunningToFailed", () => {
  const ERR: ErrorPayload = {
    error_code: "LLM_UNAVAILABLE",
    message: "Gemini generation failed: 500",
    retryable: true,
  };

  it("flips the live running step to failed with error fields attached", () => {
    const live: PipelineStatePayload = {
      pipeline_id: "pipe-A",
      steps: [makeStep("step-llm", "running", 30)],
    };
    const next = forceMostRecentRunningToFailed(
      { live, history: [], currentPipelineFromSession: null },
      ERR,
      null,
    );
    expect(next.live).not.toBeNull();
    const s = next.live!.steps![0]!;
    expect(s.state).toBe("failed");
    expect(s.error_code).toBe("LLM_UNAVAILABLE");
    expect(s.error_message).toBe("Gemini generation failed: 500");
  });

  it("flips a history step to failed when no live snapshot has a running step", () => {
    const archived: PipelineStatePayload = {
      pipeline_id: "pipe-A",
      steps: [makeStep("step-llm", "running")],
    };
    const next = forceMostRecentRunningToFailed(
      { live: null, history: [archived], currentPipelineFromSession: null },
      ERR,
      null,
    );
    expect(next.history[0]!.steps![0]!.state).toBe("failed");
  });

  it("prefers the most-recent running step when multiple are running", () => {
    const snapA: PipelineStatePayload = {
      pipeline_id: "pipe-A",
      steps: [makeStep("step-1", "running")],
    };
    const snapB: PipelineStatePayload = {
      pipeline_id: "pipe-B",
      steps: [makeStep("step-2", "running")],
    };
    const next = forceMostRecentRunningToFailed(
      { live: snapB, history: [snapA], currentPipelineFromSession: null },
      ERR,
      null,
    );
    // The live snapshot's step is the most-recent → it gets flipped.
    expect(next.live!.steps![0]!.state).toBe("failed");
    // The archived running step is left alone (it belongs to a prior turn).
    expect(next.history[0]!.steps![0]!.state).toBe("running");
  });

  it("matches by tool_name when supplied", () => {
    const snap: PipelineStatePayload = {
      pipeline_id: "pipe-A",
      steps: [
        {
          step_id: "step-a",
          name: "fetch_dem",
          tool_name: "fetch_dem",
          state: "running",
        },
        {
          step_id: "step-b",
          name: "publish_layer",
          tool_name: "publish_layer",
          state: "running",
        },
      ],
    };
    const next = forceMostRecentRunningToFailed(
      { live: snap, history: [], currentPipelineFromSession: null },
      ERR,
      "fetch_dem",
    );
    const steps = next.live!.steps!;
    expect(steps.find((s) => s.step_id === "step-a")!.state).toBe("failed");
    expect(steps.find((s) => s.step_id === "step-b")!.state).toBe("running");
  });

  it("is a no-op when no running step exists anywhere", () => {
    const snap: PipelineStatePayload = {
      pipeline_id: "pipe-A",
      steps: [makeStep("step-1", "complete")],
    };
    const state = {
      live: snap,
      history: [],
      currentPipelineFromSession: null,
    };
    const next = forceMostRecentRunningToFailed(state, ERR, null);
    expect(next).toEqual(state);
  });

  it("does NOT flip already-terminal steps to failed", () => {
    const snap: PipelineStatePayload = {
      pipeline_id: "pipe-A",
      steps: [
        makeStep("step-done", "complete"),
        makeStep("step-cancelled", "cancelled"),
      ],
    };
    const next = forceMostRecentRunningToFailed(
      { live: snap, history: [], currentPipelineFromSession: null },
      ERR,
      null,
    );
    expect(next.live!.steps![0]!.state).toBe("complete");
    expect(next.live!.steps![1]!.state).toBe("cancelled");
  });
});

// --- pipelineReducer error → ChatInput idle (job-0173 Part 2) ----------- //
//
// Kickoff: when an `error` envelope arrives (Gemini failure, agent crash +
// reconnect, dispatch TypeError, etc.), force-transition ChatInput state
// back to `idle` so the user can send a new prompt. The cancel predicate
// reads (a) live.steps.some(running) and (b) currentPipelineFromSession.
// After error: both must be false.

describe("pipelineReducer — error → ChatInput force-idle (job-0173 Part 2)", () => {
  const ERR: ErrorPayload = {
    error_code: "LLM_UNAVAILABLE",
    message: "Gemini generation failed: 500",
  } as ErrorPayload;

  it("clears currentPipelineFromSession on error so shouldShowCancel returns false", () => {
    const live: PipelineStatePayload = {
      pipeline_id: "pipe-A",
      steps: [makeStep("s1", "running", 30)],
    };
    const state: PipelineInlineState = {
      live,
      history: [],
      currentPipelineFromSession: {
        pipeline_id: "pipe-A",
        steps: [makeStep("s1", "running")],
        started_at: null,
        completed_at: null,
        final_state: null,
      },
    };
    const next = pipelineReducer(state, {
      type: "error",
      payload: ERR,
      tool_name: null,
    });
    expect(next.currentPipelineFromSession).toBeNull();
    expect(shouldShowCancel(next)).toBe(false);
  });

  it("moves the live snapshot to history when no step is still running after the flip", () => {
    const live: PipelineStatePayload = {
      pipeline_id: "pipe-A",
      steps: [
        makeStep("s1", "complete"),
        makeStep("s2", "running", 50),
      ],
    };
    const state: PipelineInlineState = {
      live,
      history: [],
      currentPipelineFromSession: null,
    };
    const next = pipelineReducer(state, {
      type: "error",
      payload: ERR,
      tool_name: null,
    });
    // live should be null (moved to history); history should contain the
    // rewritten snapshot with s2 → failed.
    expect(next.live).toBeNull();
    expect(next.history).toHaveLength(1);
    const movedSteps = next.history[0]!.steps!;
    expect(movedSteps.find((s) => s.step_id === "s2")!.state).toBe("failed");
    expect(shouldShowCancel(next)).toBe(false);
  });

  it("leaves live in place when a sibling step is still running (multi-step pipeline)", () => {
    // Two running steps; error flips only the most-recent → the other is still
    // running, so live should stay live and the cancel button still shows.
    const live: PipelineStatePayload = {
      pipeline_id: "pipe-A",
      steps: [
        makeStep("s1", "running", 20),
        makeStep("s2", "running", 80),
      ],
    };
    const state: PipelineInlineState = {
      live,
      history: [],
      currentPipelineFromSession: null,
    };
    const next = pipelineReducer(state, {
      type: "error",
      payload: ERR,
      tool_name: null,
    });
    // One step was flipped to failed; the other remains running → live stays.
    expect(next.live).not.toBeNull();
    const failed = next.live!.steps!.filter((s) => s.state === "failed");
    const running = next.live!.steps!.filter((s) => s.state === "running");
    expect(failed).toHaveLength(1);
    expect(running).toHaveLength(1);
    // Cancel button is still appropriate (a sibling tool truly is still running).
    expect(shouldShowCancel(next)).toBe(true);
  });

  it("end-to-end: live running + session current_pipeline → after error, idle", () => {
    // The canonical bug pattern from the kickoff: dispatch fails, session-state
    // never gets a terminal update, current_pipeline lingers; the live running
    // step is the only step. After error the ChatInput must return to idle.
    const live: PipelineStatePayload = {
      pipeline_id: "pipe-A",
      steps: [makeStep("only-step", "running", 10)],
    };
    const state: PipelineInlineState = {
      live,
      history: [],
      currentPipelineFromSession: {
        pipeline_id: "pipe-A",
        steps: live.steps!,
        started_at: null,
        completed_at: null,
        final_state: null,
      },
    };
    const next = pipelineReducer(state, {
      type: "error",
      payload: ERR,
      tool_name: null,
    });
    expect(shouldShowCancel(next)).toBe(false); // ChatInput renders idle (up-arrow)
  });
});
