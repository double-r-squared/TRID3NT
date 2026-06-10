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
  buildInterleavedStream,
  InterleavedEntry,
  isThinkingActive,
  isThinkingStep,
  THINKING_STEP_NAME,
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

// --- buildInterleavedStream (job-0176) ---------------------------------- //
//
// The interleave refactor's pure helper. Verifies arrival-order sorting,
// that tool cards land between agent text bubbles at their natural slot,
// and that re-ordered tool snapshots keep their first-arrival position.

describe("buildInterleavedStream (job-0176 — chronological interleave)", () => {
  it("returns an empty list when nothing has arrived yet", () => {
    expect(
      buildInterleavedStream([], [], null, new Map(), new Map()),
    ).toEqual([]);
  });

  it("orders [user → agent → tool → agent] from seq 1..4 as the user sees it", () => {
    // The canonical kickoff scenario:
    //   1. user prompt
    //   2. agent narration "I'm locating the area..."
    //   3. geocode_location tool card
    //   4. agent narration "I've added the location."
    const messageOrder = new Map<string, number>([
      ["user-0", 1],
      ["msg-pre", 2],
      ["msg-post", 4],
    ]);
    const stepOrder = new Map<string, number>([["geocode_location|geocode_location", 3]]);
    const messages = [
      { id: "user-0", role: "user" as const, text: "Show me Fort Myers", done: true },
      { id: "msg-pre", role: "agent" as const, text: "I'm locating...", done: true },
      { id: "msg-post", role: "agent" as const, text: "Added.", done: true },
    ];
    const toolSnap: PipelineStatePayload = {
      pipeline_id: "pipe-A",
      steps: [
        {
          step_id: "step-geo",
          name: "geocode_location",
          tool_name: "geocode_location",
          state: "complete",
        },
      ],
    };
    const stream = buildInterleavedStream(
      messages,
      [toolSnap],
      null,
      messageOrder,
      stepOrder,
    );
    expect(stream.map((e: InterleavedEntry) => e.kind)).toEqual([
      "user-message",
      "agent-message",
      "tool",
      "agent-message",
    ]);
    expect(stream.map((e: InterleavedEntry) => e.seq)).toEqual([1, 2, 3, 4]);
  });

  it("places a NEW tool card AT THE END when its first-arrival seq is the latest", () => {
    // User has scrolled and an agent message arrived (seq=1); then a tool
    // dispatches (seq=2) → tool card should land AFTER the agent bubble.
    const messageOrder = new Map<string, number>([["msg-1", 1]]);
    const stepOrder = new Map<string, number>([["fetch_dem|fetch_dem", 2]]);
    const messages = [
      { id: "msg-1", role: "agent" as const, text: "Working...", done: false },
    ];
    const snap: PipelineStatePayload = {
      pipeline_id: "pipe-A",
      steps: [
        {
          step_id: "step-dem",
          name: "fetch_dem",
          tool_name: "fetch_dem",
          state: "running",
          progress_percent: 25,
        },
      ],
    };
    const stream = buildInterleavedStream(
      messages,
      [],
      snap,
      messageOrder,
      stepOrder,
    );
    expect(stream).toHaveLength(2);
    expect(stream[0]!.kind).toBe("agent-message");
    expect(stream[1]!.kind).toBe("tool");
  });

  it("does NOT move a tool card when later snapshots change its state (sticky slot)", () => {
    // Tool first arrives at seq=2, then transitions running → complete.
    // The card must remain at slot 2 (between msg-1 seq=1 and msg-2 seq=3),
    // never jump to the bottom on completion.
    const messageOrder = new Map<string, number>([
      ["msg-1", 1],
      ["msg-2", 3],
    ]);
    const stepOrder = new Map<string, number>([["fetch_dem|fetch_dem", 2]]);
    const messages = [
      { id: "msg-1", role: "agent" as const, text: "Pre", done: true },
      { id: "msg-2", role: "agent" as const, text: "Post", done: true },
    ];
    // Two snapshots — running then complete; merge picks the complete state.
    const running: PipelineStatePayload = {
      pipeline_id: "pipe-A",
      steps: [
        {
          step_id: "step-dem",
          name: "fetch_dem",
          tool_name: "fetch_dem",
          state: "running",
        },
      ],
    };
    const complete: PipelineStatePayload = {
      pipeline_id: "pipe-A",
      steps: [
        {
          step_id: "step-dem",
          name: "fetch_dem",
          tool_name: "fetch_dem",
          state: "complete",
        },
      ],
    };
    const stream = buildInterleavedStream(
      messages,
      [running, complete],
      null,
      messageOrder,
      stepOrder,
    );
    expect(stream.map((e: InterleavedEntry) => e.kind)).toEqual([
      "agent-message",
      "tool",
      "agent-message",
    ]);
    // Tool card is in the COMPLETE state at its sticky slot.
    const tool = stream[1]! as Extract<InterleavedEntry, { kind: "tool" }>;
    expect(tool.step.state).toBe("complete");
  });

  it("interleaves multiple tool dispatches between multiple agent narrations", () => {
    // Pattern from kickoff:
    //   msg-1 → tool-A (geocode) → msg-2 → tool-B (WDPA) → msg-3
    const messageOrder = new Map<string, number>([
      ["user-0", 1],
      ["msg-1", 2],
      ["msg-2", 4],
      ["msg-3", 6],
    ]);
    const stepOrder = new Map<string, number>([
      ["geocode_location|geocode_location", 3],
      ["fetch_wdpa_protected_areas|fetch_wdpa_protected_areas", 5],
    ]);
    const messages = [
      { id: "user-0", role: "user" as const, text: "Q", done: true },
      { id: "msg-1", role: "agent" as const, text: "Locating...", done: true },
      { id: "msg-2", role: "agent" as const, text: "Fetching WDPA...", done: true },
      { id: "msg-3", role: "agent" as const, text: "Added 2.", done: true },
    ];
    const snap: PipelineStatePayload = {
      pipeline_id: "pipe-A",
      steps: [
        {
          step_id: "step-geo",
          name: "geocode_location",
          tool_name: "geocode_location",
          state: "complete",
        },
        {
          step_id: "step-wdpa",
          name: "fetch_wdpa_protected_areas",
          tool_name: "fetch_wdpa_protected_areas",
          state: "complete",
        },
      ],
    };
    const stream = buildInterleavedStream(
      messages,
      [snap],
      null,
      messageOrder,
      stepOrder,
    );
    expect(stream.map((e: InterleavedEntry) => e.kind)).toEqual([
      "user-message",
      "agent-message",
      "tool",
      "agent-message",
      "tool",
      "agent-message",
    ]);
  });

  it("falls back to MAX_SAFE_INTEGER and renders deterministically when seq is missing", () => {
    // Belt-and-suspenders: a message or step without a recorded seq sorts
    // AFTER everything that has one (per the function's contract).
    const messageOrder = new Map<string, number>([["msg-1", 1]]);
    const stepOrder = new Map<string, number>(); // empty
    const messages = [
      { id: "msg-1", role: "agent" as const, text: "Hi", done: true },
    ];
    const snap: PipelineStatePayload = {
      pipeline_id: "pipe-A",
      steps: [
        {
          step_id: "step-x",
          name: "unknown",
          tool_name: "unknown",
          state: "complete",
        },
      ],
    };
    const stream = buildInterleavedStream(
      messages,
      [],
      snap,
      messageOrder,
      stepOrder,
    );
    expect(stream).toHaveLength(2);
    expect(stream[0]!.kind).toBe("agent-message");
    expect(stream[1]!.kind).toBe("tool");
  });

  // --- wave-4-10 thinking-state filtering -------------------------------- //
  //
  // The Gemini "llm_generation" step is special-cased OUT of the interleaved
  // stream — it renders as a separate ephemeral indicator (ThinkingIndicator)
  // pinned to the bottom of the chat scroll, not as a tool card. Other tool
  // dispatches continue to interleave as normal.

  it("filters thinking-shaped steps (llm_generation) out of the interleaved stream", () => {
    const messageOrder = new Map<string, number>([["user-0", 1]]);
    const stepOrder = new Map<string, number>([
      [`${THINKING_STEP_NAME}|gemini_generate`, 2],
      ["fetch_dem|fetch_dem", 3],
    ]);
    const messages = [
      { id: "user-0", role: "user" as const, text: "Hi", done: true },
    ];
    const snap: PipelineStatePayload = {
      pipeline_id: "pipe-A",
      steps: [
        {
          step_id: "step-llm",
          name: THINKING_STEP_NAME,
          tool_name: "gemini_generate",
          state: "running",
        },
        {
          step_id: "step-dem",
          name: "fetch_dem",
          tool_name: "fetch_dem",
          state: "running",
        },
      ],
    };
    const stream = buildInterleavedStream(
      messages,
      [],
      snap,
      messageOrder,
      stepOrder,
    );
    // user-message + fetch_dem tool card; the llm_generation card is filtered.
    expect(stream.map((e: InterleavedEntry) => e.kind)).toEqual([
      "user-message",
      "tool",
    ]);
    const tool = stream[1]! as Extract<InterleavedEntry, { kind: "tool" }>;
    expect(tool.step.name).toBe("fetch_dem");
  });
});

// --- isThinkingActive predicate (wave-4-10) ----------------------------- //
//
// Drives the visibility of the ephemeral ThinkingIndicator pinned to the
// bottom of the chat scroll. Per `feedback_thinking_state_ephemeral`:
//   - Active when a Gemini llm_generation step exists in pending/running
//     and no real content has superseded it (no agent text bubble, no
//     non-thinking tool card recorded with seq >= thinking seq).
//   - Inactive on terminal thinking state (complete / failed / cancelled).
//   - Inactive when an agent text chunk arrives (the bubble replaces it).
//   - Inactive when a non-thinking tool card lands (the tool card itself
//     is the "agent is working" affordance).

describe("isThinkingActive (wave-4-10 thinking-state)", () => {
  it("isThinkingStep returns true for the llm_generation step name only", () => {
    expect(
      isThinkingStep({
        step_id: "s1",
        name: THINKING_STEP_NAME,
        tool_name: "gemini_generate",
        state: "running",
      }),
    ).toBe(true);
    expect(
      isThinkingStep({
        step_id: "s2",
        name: "fetch_dem",
        tool_name: "fetch_dem",
        state: "running",
      }),
    ).toBe(false);
  });

  it("returns false when no thinking step exists in history or live", () => {
    expect(
      isThinkingActive([], [], null, new Map(), new Map()),
    ).toBe(false);
  });

  it("returns true when a running thinking step exists and nothing has superseded it", () => {
    const stepOrder = new Map<string, number>([
      [`${THINKING_STEP_NAME}|gemini_generate`, 1],
    ]);
    const live: PipelineStatePayload = {
      pipeline_id: "pipe-A",
      steps: [
        {
          step_id: "step-llm",
          name: THINKING_STEP_NAME,
          tool_name: "gemini_generate",
          state: "running",
        },
      ],
    };
    expect(
      isThinkingActive([], [], live, new Map(), stepOrder),
    ).toBe(true);
  });

  it("returns true when a pending thinking step exists (not yet started)", () => {
    const stepOrder = new Map<string, number>([
      [`${THINKING_STEP_NAME}|gemini_generate`, 1],
    ]);
    const live: PipelineStatePayload = {
      pipeline_id: "pipe-A",
      steps: [
        {
          step_id: "step-llm",
          name: THINKING_STEP_NAME,
          tool_name: "gemini_generate",
          state: "pending",
        },
      ],
    };
    expect(
      isThinkingActive([], [], live, new Map(), stepOrder),
    ).toBe(true);
  });

  it("returns false the moment the thinking step transitions to COMPLETE", () => {
    const stepOrder = new Map<string, number>([
      [`${THINKING_STEP_NAME}|gemini_generate`, 1],
    ]);
    const live: PipelineStatePayload = {
      pipeline_id: "pipe-A",
      steps: [
        {
          step_id: "step-llm",
          name: THINKING_STEP_NAME,
          tool_name: "gemini_generate",
          state: "complete",
        },
      ],
    };
    expect(
      isThinkingActive([], [], live, new Map(), stepOrder),
    ).toBe(false);
  });

  it("returns false on terminal FAILED thinking state", () => {
    const stepOrder = new Map<string, number>([
      [`${THINKING_STEP_NAME}|gemini_generate`, 1],
    ]);
    const live: PipelineStatePayload = {
      pipeline_id: "pipe-A",
      steps: [
        {
          step_id: "step-llm",
          name: THINKING_STEP_NAME,
          tool_name: "gemini_generate",
          state: "failed",
        },
      ],
    };
    expect(
      isThinkingActive([], [], live, new Map(), stepOrder),
    ).toBe(false);
  });

  it("returns false on terminal CANCELLED thinking state", () => {
    const stepOrder = new Map<string, number>([
      [`${THINKING_STEP_NAME}|gemini_generate`, 1],
    ]);
    const live: PipelineStatePayload = {
      pipeline_id: "pipe-A",
      steps: [
        {
          step_id: "step-llm",
          name: THINKING_STEP_NAME,
          tool_name: "gemini_generate",
          state: "cancelled",
        },
      ],
    };
    expect(
      isThinkingActive([], [], live, new Map(), stepOrder),
    ).toBe(false);
  });

  it("returns false when an agent text bubble with content streams in after thinking", () => {
    // Thinking arrives at seq=1; agent text bubble arrives at seq=2 with
    // content — the bubble replaces the indicator.
    const messageOrder = new Map<string, number>([["msg-1", 2]]);
    const stepOrder = new Map<string, number>([
      [`${THINKING_STEP_NAME}|gemini_generate`, 1],
    ]);
    const messages = [
      {
        id: "msg-1",
        role: "agent" as const,
        text: "I'm working on it.",
        done: false,
      },
    ];
    const live: PipelineStatePayload = {
      pipeline_id: "pipe-A",
      steps: [
        {
          step_id: "step-llm",
          name: THINKING_STEP_NAME,
          tool_name: "gemini_generate",
          state: "running",
        },
      ],
    };
    expect(
      isThinkingActive(messages, [], live, messageOrder, stepOrder),
    ).toBe(false);
  });

  it("stays active when an EMPTY agent text bubble has been allocated but no content streamed yet", () => {
    // Defensive: the bubble may exist in the messages list with an empty
    // string (placeholder before deltas arrive). It does NOT replace the
    // indicator until at least one character of text has streamed.
    const messageOrder = new Map<string, number>([["msg-1", 2]]);
    const stepOrder = new Map<string, number>([
      [`${THINKING_STEP_NAME}|gemini_generate`, 1],
    ]);
    const messages = [
      { id: "msg-1", role: "agent" as const, text: "", done: false },
    ];
    const live: PipelineStatePayload = {
      pipeline_id: "pipe-A",
      steps: [
        {
          step_id: "step-llm",
          name: THINKING_STEP_NAME,
          tool_name: "gemini_generate",
          state: "running",
        },
      ],
    };
    expect(
      isThinkingActive(messages, [], live, messageOrder, stepOrder),
    ).toBe(true);
  });

  it("returns false when a non-thinking tool card lands after thinking", () => {
    // Thinking arrives at seq=1; fetch_dem tool card arrives at seq=2 → the
    // tool card is the "agent is doing real work" affordance, hide indicator.
    const stepOrder = new Map<string, number>([
      [`${THINKING_STEP_NAME}|gemini_generate`, 1],
      ["fetch_dem|fetch_dem", 2],
    ]);
    const live: PipelineStatePayload = {
      pipeline_id: "pipe-A",
      steps: [
        {
          step_id: "step-llm",
          name: THINKING_STEP_NAME,
          tool_name: "gemini_generate",
          state: "running",
        },
        {
          step_id: "step-dem",
          name: "fetch_dem",
          tool_name: "fetch_dem",
          state: "running",
        },
      ],
    };
    expect(
      isThinkingActive([], [], live, new Map(), stepOrder),
    ).toBe(false);
  });

  it("user-message bubble does NOT hide the indicator (only agent text counts)", () => {
    // The user's message arrives BEFORE thinking — user-message at seq=1,
    // thinking at seq=2. Even if it were at seq=3 the predicate ignores
    // user-role messages: only agent text bubbles count as superseding
    // content.
    const messageOrder = new Map<string, number>([["user-0", 3]]);
    const stepOrder = new Map<string, number>([
      [`${THINKING_STEP_NAME}|gemini_generate`, 2],
    ]);
    const messages = [
      { id: "user-0", role: "user" as const, text: "Q", done: true },
    ];
    const live: PipelineStatePayload = {
      pipeline_id: "pipe-A",
      steps: [
        {
          step_id: "step-llm",
          name: THINKING_STEP_NAME,
          tool_name: "gemini_generate",
          state: "running",
        },
      ],
    };
    expect(
      isThinkingActive(messages, [], live, messageOrder, stepOrder),
    ).toBe(true);
  });
});
