// GRACE-2 web — AnimationController unit tests (JOB WEB-ANIM #157.1).
//
// The keystone fix: the sequence-playback state + the advance interval live in a
// module-level controller (NOT inside LayerPanel/SequenceScrubber), so closing /
// unmounting the panel never stops playback. These tests exercise the controller
// directly with a fake timer seam + a stub frame-visibility emitter.

import { describe, it, expect, vi } from "vitest";
import {
  AnimationController,
  getAnimationController,
  setAnimationController,
  type AnimGroup,
  type AnimTimers,
  type FrameVisibilityEmitter,
} from "./animation_controller";

const GROUP: AnimGroup = {
  key: "grp-1",
  label: "HRRR precip",
  layerIds: ["f01", "f03", "f06"],
  frameLabels: ["F+01h", "F+03h", "F+06h"],
};

/** A controllable fake timer seam: returns a `tick()` to fire the interval. */
function makeFakeTimers(): { timers: AnimTimers; tick: () => void; cleared: () => boolean } {
  let cb: (() => void) | null = null;
  let isCleared = false;
  const timers: AnimTimers = {
    setInterval: (fn) => {
      cb = fn;
      isCleared = false;
      return 1;
    },
    clearInterval: () => {
      cb = null;
      isCleared = true;
    },
  };
  return {
    timers,
    tick: () => cb?.(),
    cleared: () => isCleared,
  };
}

describe("AnimationController — group registration + default frame", () => {
  it("seeds the active group + the LAST frame as default on setGroups", () => {
    const c = new AnimationController();
    c.setGroups([GROUP]);
    expect(c.getActiveGroup()?.key).toBe("grp-1");
    // Default = last frame (the latest forecast hour reads as "current").
    expect(c.frameIndexFor("grp-1")).toBe(2);
  });

  it("clears the active group + stops play when no groups remain", () => {
    const { timers } = makeFakeTimers();
    const c = new AnimationController({ timers });
    c.setGroups([GROUP]);
    c.setPlaying(true);
    c.setGroups([]);
    expect(c.getActiveGroup()).toBeNull();
    expect(c.isPlaying()).toBe(false);
  });

  it("prunes frame state for groups that disappear", () => {
    const c = new AnimationController();
    c.setGroups([GROUP]);
    c.stepGroupTo("grp-1", 0);
    c.setGroups([]); // grp-1 gone
    c.setGroups([GROUP]); // re-formed -> default frame again (last)
    expect(c.frameIndexFor("grp-1")).toBe(2);
  });
});

describe("AnimationController — stepping drives the emitter", () => {
  it("emits show-frame-i / hide-the-rest on stepGroupTo", () => {
    const c = new AnimationController();
    c.setGroups([GROUP]);
    const emitted: Array<{ ids: string[]; idx: number }> = [];
    const emitter: FrameVisibilityEmitter = (ids, idx) =>
      emitted.push({ ids: [...ids], idx });
    c.setEmitter(emitter);
    c.stepGroupTo("grp-1", 1);
    expect(emitted).toEqual([{ ids: ["f01", "f03", "f06"], idx: 1 }]);
    expect(c.frameIndexFor("grp-1")).toBe(1);
  });

  it("advanceActive wraps past the last frame back to 0", () => {
    const c = new AnimationController();
    c.setGroups([GROUP]);
    const seen: number[] = [];
    c.setEmitter((_ids, idx) => seen.push(idx));
    c.stepGroupTo("grp-1", 2); // last frame
    c.advanceActive(1); // wrap -> 0
    expect(c.frameIndexFor("grp-1")).toBe(0);
    expect(seen[seen.length - 1]).toBe(0);
  });
});

describe("AnimationController — auto-play interval (controller-owned)", () => {
  it("arms the interval on play + advances on each tick", () => {
    const { timers, tick } = makeFakeTimers();
    const c = new AnimationController({ timers });
    c.setGroups([GROUP]);
    const seen: number[] = [];
    c.setEmitter((_ids, idx) => seen.push(idx));
    c.stepGroupTo("grp-1", 0); // start at frame 0
    seen.length = 0;
    c.setPlaying(true);
    tick(); // 0 -> 1
    tick(); // 1 -> 2
    tick(); // 2 -> 0 (wrap)
    expect(seen).toEqual([1, 2, 0]);
  });

  it("clears the interval on pause", () => {
    const { timers, cleared } = makeFakeTimers();
    const c = new AnimationController({ timers });
    c.setGroups([GROUP]);
    c.setPlaying(true);
    expect(cleared()).toBe(false);
    c.setPlaying(false);
    expect(cleared()).toBe(true);
  });

  it("does not arm the interval for a single-frame group", () => {
    const { timers, tick } = makeFakeTimers();
    const c = new AnimationController({ timers });
    c.setGroups([{ ...GROUP, layerIds: ["only"], frameLabels: ["F+01h"] }]);
    const seen: number[] = [];
    c.setEmitter((_ids, idx) => seen.push(idx));
    c.setPlaying(true);
    tick(); // no-op — interval was never armed (cb stays null)
    expect(seen).toHaveLength(0);
  });
});

describe("AnimationController — subscription snapshot stability", () => {
  it("returns the SAME snapshot reference until a mutation (useSyncExternalStore safe)", () => {
    const c = new AnimationController();
    c.setGroups([GROUP]);
    const a = c.snapshot();
    const b = c.snapshot();
    expect(a).toBe(b); // stable identity between reads
    c.stepGroupTo("grp-1", 0);
    const d = c.snapshot();
    expect(d).not.toBe(a); // new reference after a change
  });

  it("notifies subscribers on state changes", () => {
    const c = new AnimationController();
    const cb = vi.fn();
    c.subscribe(cb); // immediate invoke (1)
    c.setGroups([GROUP]); // (2)
    c.setPlaying(true); // (3)
    expect(cb.mock.calls.length).toBeGreaterThanOrEqual(3);
  });
});

describe("AnimationController — singleton", () => {
  it("getAnimationController returns a stable instance; setAnimationController replaces it", () => {
    const first = getAnimationController();
    expect(getAnimationController()).toBe(first);
    const replacement = new AnimationController();
    setAnimationController(replacement);
    expect(getAnimationController()).toBe(replacement);
  });
});
