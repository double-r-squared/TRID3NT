// GRACE-2 web - bbox progress state-machine + settings unit tests (NATE item 1).

import { describe, it, expect, beforeEach } from "vitest";
import {
  resolveBboxProgress,
  readBboxAnimationsEnabled,
  writeBboxAnimationsEnabled,
  isPipelineRunning,
  LS_BBOX_ANIM,
  type BboxProgressSignals,
} from "./bbox_progress";

// A signals baseline: a bbox is on screen, nothing else happening, anim enabled.
const BASE: BboxProgressSignals = {
  hasBbox: true,
  layerCount: 0,
  layersLoading: false,
  connecting: false,
  simRunning: false,
  animationsEnabled: true,
};

describe("resolveBboxProgress - overlay state machine (item 1)", () => {
  it("renders nothing when there is no bbox anchor", () => {
    const s = resolveBboxProgress({ ...BASE, hasBbox: false, layersLoading: true });
    expect(s.mode).toBe("none");
  });

  it("FIRST fetch (loading, no layers yet) -> FILL shimmer", () => {
    const s = resolveBboxProgress({ ...BASE, layersLoading: true, layerCount: 0 });
    expect(s.mode).toBe("fill");
    expect(s.toggleExempt).toBe(false);
  });

  it("SUBSEQUENT load (loading, layers already exist) -> SCAN border (no cover)", () => {
    const s = resolveBboxProgress({ ...BASE, layersLoading: true, layerCount: 3 });
    expect(s.mode).toBe("scan");
    expect(s.tone).toBe("blue");
  });

  it("CONNECTING -> SCAN border, ALWAYS ON (toggle-exempt) even with layers", () => {
    const s = resolveBboxProgress({ ...BASE, connecting: true, layerCount: 2 });
    expect(s.mode).toBe("scan");
    expect(s.tone).toBe("blue");
    expect(s.toggleExempt).toBe(true);
  });

  it("CONNECTING survives the user disabling animations (still scans)", () => {
    const s = resolveBboxProgress({
      ...BASE,
      connecting: true,
      animationsEnabled: false,
    });
    expect(s.mode).toBe("scan");
    expect(s.toggleExempt).toBe(true);
  });

  it("LONG-RUNNING SIM -> PURPLE scan border", () => {
    const s = resolveBboxProgress({ ...BASE, simRunning: true, layerCount: 4 });
    expect(s.mode).toBe("scan");
    expect(s.tone).toBe("purple");
  });

  it("connecting takes priority over a running sim (transport health first)", () => {
    const s = resolveBboxProgress({
      ...BASE,
      connecting: true,
      simRunning: true,
    });
    expect(s.mode).toBe("scan");
    expect(s.tone).toBe("blue"); // connecting (blue), not the purple sim tone
    expect(s.toggleExempt).toBe(true);
  });

  it("DISABLED toggle suppresses fill / scan / sim (but not connecting)", () => {
    expect(
      resolveBboxProgress({
        ...BASE,
        layersLoading: true,
        layerCount: 0,
        animationsEnabled: false,
      }).mode,
    ).toBe("none");
    expect(
      resolveBboxProgress({
        ...BASE,
        simRunning: true,
        animationsEnabled: false,
      }).mode,
    ).toBe("none");
  });

  it("idle (bbox present, nothing loading) -> none", () => {
    expect(resolveBboxProgress({ ...BASE }).mode).toBe("none");
  });
});

describe("bbox-animation settings persistence (default ON)", () => {
  beforeEach(() => {
    try {
      localStorage.clear();
    } catch {
      /* ignore */
    }
  });

  it("defaults to ON when nothing is persisted", () => {
    expect(readBboxAnimationsEnabled()).toBe(true);
  });

  it("persists + reads back false", () => {
    writeBboxAnimationsEnabled(false);
    expect(localStorage.getItem(LS_BBOX_ANIM)).toBe("false");
    expect(readBboxAnimationsEnabled()).toBe(false);
  });

  it("persists + reads back true", () => {
    writeBboxAnimationsEnabled(false);
    writeBboxAnimationsEnabled(true);
    expect(readBboxAnimationsEnabled()).toBe(true);
  });

  it("treats any non-'false' value as enabled (default-ON bias)", () => {
    localStorage.setItem(LS_BBOX_ANIM, "garbage");
    expect(readBboxAnimationsEnabled()).toBe(true);
  });
});

describe("isPipelineRunning - long-running-sim signal", () => {
  it("false for null / non-object", () => {
    expect(isPipelineRunning(null)).toBe(false);
    expect(isPipelineRunning(undefined)).toBe(false);
    expect(isPipelineRunning("x")).toBe(false);
  });

  it("true when a step is running and the pipeline has not terminated", () => {
    expect(
      isPipelineRunning({
        steps: [{ state: "complete" }, { state: "running" }],
      }),
    ).toBe(true);
  });

  it("false when the pipeline has a terminal final_state", () => {
    expect(
      isPipelineRunning({
        final_state: "complete",
        steps: [{ state: "running" }],
      }),
    ).toBe(false);
  });

  it("false when no step is running", () => {
    expect(
      isPipelineRunning({ steps: [{ state: "pending" }, { state: "complete" }] }),
    ).toBe(false);
  });

  it("false when steps is missing / not an array", () => {
    expect(isPipelineRunning({})).toBe(false);
    expect(isPipelineRunning({ steps: "x" })).toBe(false);
  });
});
