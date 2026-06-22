// GRACE-2 web — sequence animation controller (panel-independent playback).
//
// ROOT CAUSE this fixes (NATE live mobile test 2026-06-20): the frame-advance
// playback for a sequential layer group (the `playing` flag + the interval that
// advances the visible frame) used to live INSIDE LayerPanel / SequenceScrubber.
// On mobile the LayerPanel lives in a MobileDrawer that UNMOUNTS when collapsed,
// and even on desktop the panel can be closed — either way the React state and
// the interval were torn down, so closing the Layers panel KILLED the animation
// and dropped the scrubber.
//
// FIX: lift the playback state + the advance interval into THIS module-level
// singleton (mirroring the getLayerCache() pattern in lib/layer_cache.ts). The
// controller holds {activeGroupKey, frameIndex (per group), playing}, runs the
// advance interval at module scope (survives any component unmount), and drives
// per-frame map visibility through an injected emitter (App wires this to the
// LayerPanelBus.pushMapCommand, which Map.tsx already subscribes to — Map.tsx
// stays mounted, so frames keep advancing on the map while the panel is closed).
//
// LayerPanel + SequenceScrubber become CONTROLS over this controller:
//   - LayerPanel pushes its detected sequential groups in (setGroups) and is the
//     source of frame-stepping intents.
//   - SequenceScrubber reads {playing, frameIndex} and toggles play.
//   - App subscribes to render the scrubber WHENEVER a sequence is animating
//     (active group present), regardless of whether the Layers panel is open.
//
// This module is PURE w.r.t. React / MapLibre (no imports of either). The only
// side effects are the setInterval (injectable via a timer seam for tests) and
// the emitter callback. Unit-testable with fake timers + a stub emitter.

/** Minimal shape of a sequential group the controller needs to advance frames. */
export interface AnimGroup {
  /** Stable key (matches SequentialGroup.key in LayerPanel). */
  key: string;
  /** Human label (for the scrubber). */
  label: string;
  /** Member layer ids in series order (ascending frame value). */
  layerIds: string[];
  /** Per-frame short labels, parallel to layerIds. */
  frameLabels: string[];
}

/** A read-only snapshot of controller state for subscribers. */
export interface AnimState {
  /** All known groups (latest pushed by LayerPanel). */
  groups: AnimGroup[];
  /** The group the scrubber drives + the play interval advances. null = none. */
  activeGroupKey: string | null;
  /** Per-group active frame index (groupKey -> frameIndex). */
  frameByGroup: Record<string, number>;
  /** Whether the active group is auto-advancing. */
  playing: boolean;
}

/**
 * Emit one frame's visibility intent: show `visibleLayerId`, hide the rest of
 * the group's members. The controller calls this whenever the active frame
 * changes (step / auto-advance). App wires it to bus.pushMapCommand so Map.tsx
 * (always mounted) flips the MapLibre layer visibility — independent of the
 * LayerPanel's lifetime. layerIds is the FULL group member list; visibleIndex
 * is which one should be visible.
 */
export type FrameVisibilityEmitter = (
  layerIds: string[],
  visibleIndex: number,
) => void;

/** Injectable timer seam so tests can drive the interval deterministically. */
export interface AnimTimers {
  setInterval(cb: () => void, ms: number): number;
  clearInterval(id: number): void;
}

const defaultTimers: AnimTimers = {
  setInterval: (cb, ms) =>
    typeof window !== "undefined"
      ? window.setInterval(cb, ms)
      : (setInterval(cb, ms) as unknown as number),
  clearInterval: (id) =>
    typeof window !== "undefined"
      ? window.clearInterval(id)
      : clearInterval(id as unknown as ReturnType<typeof setInterval>),
};

export interface AnimControllerOptions {
  /** Auto-advance cadence in ms while playing. Default 1100 (matches scrubber). */
  intervalMs?: number;
  /** Timer seam (tests inject a fake). Default = window.setInterval. */
  timers?: AnimTimers;
  /**
   * ITEM 5 (NATE 2026-06-22) - reduced-motion seam. When this returns true the
   * controller does NOT auto-start playback on a newly-seen group (the user
   * prefers no motion), though manual play still works. Default consults the
   * `prefers-reduced-motion` media query; tests inject a deterministic stub.
   */
  prefersReducedMotion?: () => boolean;
}

/** SSR/test-safe default reduced-motion probe (mirrors PipelineCard's). */
function defaultPrefersReducedMotion(): boolean {
  if (typeof window === "undefined" || !window.matchMedia) return false;
  try {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return false;
  }
}

function clampIndex(i: number, n: number): number {
  if (n <= 0) return 0;
  return Math.max(0, Math.min(n - 1, i));
}

/** Wrap `i` into [0, n) so auto-advance loops cleanly past the last frame. */
function wrap(i: number, n: number): number {
  if (n <= 0) return 0;
  return ((i % n) + n) % n;
}

/**
 * Module-level playback controller for sequential layer groups. Holds the
 * play/frame state and runs the advance interval OUTSIDE the React tree so the
 * animation survives a LayerPanel unmount (the keystone fix). Drives map frame
 * visibility through the injected emitter.
 */
export class AnimationController {
  private groups: AnimGroup[] = [];
  private activeGroupKey: string | null = null;
  private frameByGroup: Record<string, number> = {};
  private playing = false;

  private readonly subs = new Set<(s: AnimState) => void>();
  private emitter: FrameVisibilityEmitter | null = null;
  // Cached snapshot — kept STABLE (same reference) between mutations so
  // useSyncExternalStore's Object.is comparison does not loop. Invalidated to
  // null on every state change; rebuilt lazily on the next snapshot() read.
  private cachedSnapshot: AnimState | null = null;

  private readonly intervalMs: number;
  private readonly timers: AnimTimers;
  private timerId: number | null = null;
  // ITEM 5 - reduced-motion probe (auto-play suppressed when it returns true).
  private readonly prefersReducedMotion: () => boolean;
  // ITEM 5 - groups we have already auto-played, so re-pushing the same group
  // set (LayerPanel re-detects on every session-state frame) does not restart
  // playback after the user paused it. Keyed by group key.
  private autoPlayedKeys = new Set<string>();

  constructor(opts: AnimControllerOptions = {}) {
    this.intervalMs = Math.max(50, opts.intervalMs ?? 1100);
    this.timers = opts.timers ?? defaultTimers;
    this.prefersReducedMotion =
      opts.prefersReducedMotion ?? defaultPrefersReducedMotion;
  }

  // --- emitter wiring --------------------------------------------------- //

  /**
   * Register the frame-visibility emitter (App wires it to bus.pushMapCommand).
   * Returns an unregister fn. Only one emitter is active at a time — a re-register
   * replaces the prior one (App re-runs the effect when the bus identity changes).
   */
  setEmitter(emitter: FrameVisibilityEmitter | null): () => void {
    this.emitter = emitter;
    return () => {
      if (this.emitter === emitter) this.emitter = null;
    };
  }

  // --- subscription ----------------------------------------------------- //

  /** Subscribe to state changes. Immediately invoked with the current state. */
  subscribe(cb: (s: AnimState) => void): () => void {
    this.subs.add(cb);
    cb(this.snapshot());
    return () => {
      this.subs.delete(cb);
    };
  }

  /**
   * Current snapshot of state. The SAME object reference is returned across
   * repeated calls until a mutation invalidates it — required by
   * useSyncExternalStore (Object.is identity, else it loops forever).
   */
  snapshot(): AnimState {
    if (this.cachedSnapshot === null) {
      this.cachedSnapshot = {
        groups: this.groups,
        activeGroupKey: this.activeGroupKey,
        frameByGroup: { ...this.frameByGroup },
        playing: this.playing,
      };
    }
    return this.cachedSnapshot;
  }

  private notify(): void {
    this.cachedSnapshot = null; // invalidate so the next snapshot() rebuilds.
    const s = this.snapshot();
    for (const cb of this.subs) cb(s);
  }

  // --- group registration (LayerPanel pushes detected groups) ----------- //

  /**
   * Replace the known group set (LayerPanel calls this whenever its detected
   * sequential groups change). Keeps the active key valid (defaults to the
   * first group), prunes frame indices for vanished groups, and stops playback
   * when no groups remain.
   *
   * ITEM 5 (NATE 2026-06-22): a newly-loaded animation group now defaults its
   * frame to the FIRST frame (index 0, not the last) AND auto-starts playback,
   * so the animation reads as an animation (a sweep from the start) instead of a
   * static peak. Auto-play is suppressed under prefers-reduced-motion, and is
   * done at most ONCE per group key (autoPlayedKeys) so a re-push of the same
   * group set after the user paused does not restart playback.
   */
  setGroups(groups: AnimGroup[]): void {
    this.groups = groups;

    // Prune frame state for groups that no longer exist. Also forget their
    // auto-play marker so a genuinely NEW group of the same key later re-plays.
    const live = new Set(groups.map((g) => g.key));
    for (const k of Object.keys(this.frameByGroup)) {
      if (!live.has(k)) delete this.frameByGroup[k];
    }
    for (const k of [...this.autoPlayedKeys]) {
      if (!live.has(k)) this.autoPlayedKeys.delete(k);
    }

    // Seed a default frame index (FIRST frame) for any newly-seen group, and
    // collect the newly-seen multi-frame group keys for auto-play below.
    const newlySeen: string[] = [];
    for (const g of groups) {
      if (this.frameByGroup[g.key] === undefined) {
        this.frameByGroup[g.key] = 0; // ITEM 5: default to the FIRST frame.
        if (g.layerIds.length > 1) newlySeen.push(g.key);
      }
    }

    if (groups.length === 0) {
      if (this.activeGroupKey !== null) this.activeGroupKey = null;
      if (this.playing) this.setPlaying(false); // also clears the interval
      this.notify();
      return;
    }

    // Keep activeGroupKey valid; default to the first group.
    const first = groups[0];
    if (
      first &&
      (!this.activeGroupKey || !groups.some((g) => g.key === this.activeGroupKey))
    ) {
      this.activeGroupKey = first.key;
    }

    // ITEM 5: auto-start playback on a freshly-loaded multi-frame group so it
    // animates on load (not a static peak). Make the new group active first so
    // the interval drives the right frames. Skip under reduced-motion and skip
    // groups already auto-played this lifetime. Also emit frame 0 so the map
    // shows the first frame immediately even before the first interval tick.
    if (newlySeen.length > 0) {
      const autoKey =
        this.activeGroupKey && newlySeen.includes(this.activeGroupKey)
          ? this.activeGroupKey
          : newlySeen[0]!;
      this.activeGroupKey = autoKey;
      const g = this.groups.find((gr) => gr.key === autoKey);
      if (g) this.emitFrame(g, 0); // show the first frame now.
      if (!this.prefersReducedMotion()) {
        this.autoPlayedKeys.add(autoKey);
        // setPlaying arms the interval (syncInterval) and notifies.
        this.setPlaying(true);
        return;
      }
      // Reduced motion: still mark it seen so we don't re-attempt, but leave it
      // paused on frame 0 (a static first frame, not the peak).
      this.autoPlayedKeys.add(autoKey);
    }

    this.notify();
  }

  // --- queries ---------------------------------------------------------- //

  getGroups(): AnimGroup[] {
    return this.groups;
  }

  getActiveGroup(): AnimGroup | null {
    if (this.activeGroupKey === null) return null;
    return this.groups.find((g) => g.key === this.activeGroupKey) ?? null;
  }

  isPlaying(): boolean {
    return this.playing;
  }

  /** Resolved active frame index for a group key (default = FIRST frame). */
  frameIndexFor(key: string): number {
    const g = this.groups.find((gr) => gr.key === key);
    if (!g) return 0;
    const raw = this.frameByGroup[key];
    // ITEM 5 (NATE 2026-06-22): default to the FIRST frame (0), not the last,
    // so a group seen before its frame index is recorded reads from the start.
    const idx = typeof raw === "number" ? raw : 0;
    return clampIndex(idx, g.layerIds.length);
  }

  // --- commands (controls call these) ----------------------------------- //

  /** Make a group the scrubber/playback target. */
  setActiveGroup(key: string | null): void {
    if (this.activeGroupKey === key) return;
    this.activeGroupKey = key;
    this.notify();
  }

  /**
   * Step a group to frame `index`. Sets the active group to this group, records
   * the frame, and emits the frame-visibility intent (show frame i, hide the
   * rest) so the map updates even if the panel is closed.
   */
  stepGroupTo(key: string, index: number): void {
    const g = this.groups.find((gr) => gr.key === key);
    if (!g) return;
    const clamped = clampIndex(index, g.layerIds.length);
    this.activeGroupKey = key;
    this.frameByGroup[key] = clamped;
    this.emitFrame(g, clamped);
    this.notify();
  }

  /** Advance the ACTIVE group by `delta` frames (wraps). No-op if none active. */
  advanceActive(delta: number): void {
    const g = this.getActiveGroup();
    if (!g) return;
    const cur = this.frameIndexFor(g.key);
    const next = wrap(cur + delta, g.layerIds.length);
    this.frameByGroup[g.key] = next;
    this.emitFrame(g, next);
    this.notify();
  }

  /** Set the playing flag (arms/clears the advance interval). */
  setPlaying(playing: boolean): void {
    if (this.playing === playing) {
      // Still ensure the interval matches the desired state (idempotent).
      this.syncInterval();
      return;
    }
    this.playing = playing;
    this.syncInterval();
    this.notify();
  }

  /** Toggle playing. */
  togglePlaying(): void {
    this.setPlaying(!this.playing);
  }

  // --- internals -------------------------------------------------------- //

  private emitFrame(g: AnimGroup, visibleIndex: number): void {
    if (this.emitter) this.emitter(g.layerIds, visibleIndex);
  }

  /** Arm the advance interval when playing + a multi-frame group is active. */
  private syncInterval(): void {
    const active = this.getActiveGroup();
    const shouldRun =
      this.playing && active !== null && active.layerIds.length > 1;
    if (shouldRun && this.timerId === null) {
      this.timerId = this.timers.setInterval(() => {
        this.advanceActive(1);
      }, this.intervalMs);
    } else if (!shouldRun && this.timerId !== null) {
      this.timers.clearInterval(this.timerId);
      this.timerId = null;
    }
  }

  /**
   * Item c (NATE 2026-06-20) — fully clear playback state. Used on CASE-EXIT /
   * CASE-SWITCH: when a Case is closed the LayerPanel unmounts (the left rail
   * shows the Cases list, not CaseView), so it never pushes `setGroups([])` to
   * clear the controller — the old Case's groups would linger and the
   * App-level scrubber would keep rendering for a Case you've left. App calls
   * this to drop ALL groups + the active key + frame state + stop playback (and
   * tear the interval down), so the scrubber vanishes on Case exit. The new
   * Case's LayerPanel re-pushes its own groups on mount.
   */
  reset(): void {
    this.groups = [];
    this.activeGroupKey = null;
    this.frameByGroup = {};
    this.playing = false;
    this.autoPlayedKeys.clear(); // ITEM 5: a new Case may re-auto-play its groups.
    this.dispose(); // stop the advance interval
    this.notify();
  }

  /** Tear the interval down (tests / explicit reset). */
  dispose(): void {
    if (this.timerId !== null) {
      this.timers.clearInterval(this.timerId);
      this.timerId = null;
    }
  }
}

// --- Shared singleton --------------------------------------------------- //
//
// Process-global so the panel-independent playback survives ANY component
// unmount: LayerPanel pushes groups + steps, SequenceScrubber + App subscribe.
// Created lazily on first access (mirrors getLayerCache) so a test can replace
// it before App mounts.

let shared: AnimationController | null = null;

/** The process-global AnimationController. Lazily created with defaults. */
export function getAnimationController(): AnimationController {
  if (shared === null) shared = new AnimationController();
  return shared;
}

/** Replace the process-global AnimationController (tests / explicit re-init). */
export function setAnimationController(c: AnimationController): void {
  shared?.dispose();
  shared = c;
}
