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

// ITEM 5 (NATE 2026-06-22): setGroups now AUTO-PLAYS a newly-seen multi-frame
// group (unless prefers-reduced-motion). The existing tests below assert the
// controller's NON-autoplay mechanics (default frame, manual play/pause, prune),
// so they construct the controller with reduced-motion ON to suppress the
// auto-play side effect and keep exercising the underlying primitives. The
// dedicated "ITEM 5" describe block at the bottom verifies the auto-play +
// first-frame-default behavior with reduced-motion OFF.
const REDUCED = { prefersReducedMotion: () => true } as const;

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
  it("seeds the active group + the FIRST frame as default on setGroups (item 5)", () => {
    const c = new AnimationController(REDUCED);
    c.setGroups([GROUP]);
    expect(c.getActiveGroup()?.key).toBe("grp-1");
    // ITEM 5: default = FIRST frame (0), so the animation reads from the start
    // (not a static peak). Reduced-motion suppresses auto-play, so it stays on 0.
    expect(c.frameIndexFor("grp-1")).toBe(0);
  });

  it("clears the active group + stops play when no groups remain", () => {
    const { timers } = makeFakeTimers();
    const c = new AnimationController({ timers, ...REDUCED });
    c.setGroups([GROUP]);
    c.setPlaying(true);
    c.setGroups([]);
    expect(c.getActiveGroup()).toBeNull();
    expect(c.isPlaying()).toBe(false);
  });

  it("prunes frame state for groups that disappear", () => {
    const c = new AnimationController(REDUCED);
    c.setGroups([GROUP]);
    c.stepGroupTo("grp-1", 2);
    c.setGroups([]); // grp-1 gone
    c.setGroups([GROUP]); // re-formed -> default frame again (FIRST, item 5)
    expect(c.frameIndexFor("grp-1")).toBe(0);
  });
});

describe("AnimationController — stepping drives the emitter", () => {
  it("emits show-frame-i / hide-the-rest on stepGroupTo", () => {
    const c = new AnimationController(REDUCED);
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
    const c = new AnimationController(REDUCED);
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
    const c = new AnimationController({ timers, ...REDUCED });
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
    const c = new AnimationController({ timers, ...REDUCED });
    c.setGroups([GROUP]);
    c.setPlaying(true);
    expect(cleared()).toBe(false);
    c.setPlaying(false);
    expect(cleared()).toBe(true);
  });

  it("does not arm the interval for a single-frame group", () => {
    const { timers, tick } = makeFakeTimers();
    const c = new AnimationController({ timers, ...REDUCED });
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
    const c = new AnimationController(REDUCED);
    c.setGroups([GROUP]);
    const a = c.snapshot();
    const b = c.snapshot();
    expect(a).toBe(b); // stable identity between reads
    c.stepGroupTo("grp-1", 1);
    const d = c.snapshot();
    expect(d).not.toBe(a); // new reference after a change
  });

  it("notifies subscribers on state changes", () => {
    const c = new AnimationController(REDUCED);
    const cb = vi.fn();
    c.subscribe(cb); // immediate invoke (1)
    c.setGroups([GROUP]); // (2)
    c.setPlaying(true); // (3)
    expect(cb.mock.calls.length).toBeGreaterThanOrEqual(3);
  });
});

// --- ITEM 5 + AUTOPLAY-OFF (NATE 2026-06-24): default-to-FIRST-frame; auto-play
// is now OPT-IN (autoPlay:true). By DEFAULT a newly-loaded group shows frame 0
// statically and waits for the user to press play. The auto-play assertions
// below construct with { autoPlay: true } to exercise the opt-in path. ------- //
describe("AnimationController - first-frame default + opt-in auto-play", () => {
  it("defaults a newly-loaded group to frame 0 (not the last)", () => {
    const c = new AnimationController({ prefersReducedMotion: () => false });
    c.setGroups([GROUP]);
    expect(c.frameIndexFor("grp-1")).toBe(0);
  });

  it("does NOT auto-play by default (opt-in off) - shows frame 0 paused", () => {
    // AUTOPLAY-OFF: NATE reversed the auto-sweep default; playback is user-driven.
    const { timers } = makeFakeTimers();
    const c = new AnimationController({
      timers,
      prefersReducedMotion: () => false,
      // autoPlay omitted -> defaults to false.
    });
    c.setGroups([GROUP]);
    expect(c.isPlaying()).toBe(false);
    expect(c.frameIndexFor("grp-1")).toBe(0);
    expect(c.getActiveGroup()?.key).toBe("grp-1");
  });

  it("auto-starts playback on a freshly-loaded multi-frame group when autoPlay:true", () => {
    const { timers } = makeFakeTimers();
    const c = new AnimationController({
      timers,
      prefersReducedMotion: () => false,
      autoPlay: true,
    });
    c.setGroups([GROUP]);
    expect(c.isPlaying()).toBe(true);
    expect(c.getActiveGroup()?.key).toBe("grp-1");
  });

  it("emits frame 0 immediately on load (first frame painted before any tick)", () => {
    // Frame 0 is emitted whether or not auto-play is on (default off here).
    const { timers } = makeFakeTimers();
    const c = new AnimationController({
      timers,
      prefersReducedMotion: () => false,
    });
    const seen: number[] = [];
    c.setEmitter((_ids, idx) => seen.push(idx));
    c.setGroups([GROUP]);
    expect(seen[0]).toBe(0); // the first frame is shown on load.
  });

  it("does NOT auto-play under prefers-reduced-motion even with autoPlay:true (stays on frame 0, paused)", () => {
    const { timers } = makeFakeTimers();
    const c = new AnimationController({
      timers,
      prefersReducedMotion: () => true,
      autoPlay: true,
    });
    c.setGroups([GROUP]);
    expect(c.isPlaying()).toBe(false);
    expect(c.frameIndexFor("grp-1")).toBe(0);
  });

  it("does NOT auto-play a single-frame group (even with autoPlay:true)", () => {
    const { timers } = makeFakeTimers();
    const c = new AnimationController({
      timers,
      prefersReducedMotion: () => false,
      autoPlay: true,
    });
    c.setGroups([{ ...GROUP, layerIds: ["only"], frameLabels: ["F+01h"] }]);
    expect(c.isPlaying()).toBe(false);
  });

  it("does NOT restart playback when the same group is re-pushed after a pause (autoPlay:true)", () => {
    const { timers } = makeFakeTimers();
    const c = new AnimationController({
      timers,
      prefersReducedMotion: () => false,
      autoPlay: true,
    });
    c.setGroups([GROUP]); // auto-plays
    expect(c.isPlaying()).toBe(true);
    c.setPlaying(false); // user pauses
    c.setGroups([GROUP]); // re-detect (LayerPanel re-pushes) -> must NOT replay
    expect(c.isPlaying()).toBe(false);
  });

  it("re-auto-plays a group after reset() (a new Case) when autoPlay:true", () => {
    const { timers } = makeFakeTimers();
    const c = new AnimationController({
      timers,
      prefersReducedMotion: () => false,
      autoPlay: true,
    });
    c.setGroups([GROUP]);
    c.setPlaying(false);
    c.reset(); // Case exit clears the auto-played marker
    c.setGroups([GROUP]); // new Case -> auto-play again
    expect(c.isPlaying()).toBe(true);
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

// Item c (CLEAR ON CASE-EXIT, NATE 2026-06-20) — reset() drops ALL playback
// state so the App-level scrubber vanishes when a Case is closed (the LayerPanel
// unmounts on exit, so it never pushes setGroups([]) to clear the controller).
describe("AnimationController — reset() clears all state on case-exit (item c)", () => {
  it("drops groups + active key + frame state + playing, and stops the interval", () => {
    const { timers, tick, cleared } = makeFakeTimers();
    const c = new AnimationController({ timers });
    c.setGroups([GROUP]);
    c.stepGroupTo(GROUP.key, 0);
    c.setPlaying(true);
    expect(c.getActiveGroup()).not.toBeNull();
    expect(c.isPlaying()).toBe(true);

    c.reset();

    // Everything is cleared — the scrubber (which renders only when a group is
    // active) will unmount.
    expect(c.getGroups()).toHaveLength(0);
    expect(c.getActiveGroup()).toBeNull();
    expect(c.isPlaying()).toBe(false);
    expect(cleared()).toBe(true);
    // A post-reset tick is a no-op (no active group to advance).
    tick();
    expect(c.getActiveGroup()).toBeNull();
  });

  it("notifies subscribers so the scrubber re-renders (and disappears) on reset", () => {
    const c = new AnimationController({ timers: makeFakeTimers().timers });
    c.setGroups([GROUP]);
    const cb = vi.fn();
    c.subscribe(cb); // immediate call (1)
    cb.mockClear();
    c.reset();
    expect(cb).toHaveBeenCalled();
    expect(cb.mock.calls.at(-1)![0].activeGroupKey).toBeNull();
  });
});
