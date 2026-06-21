// GRACE-2 web — JOB WEB-ANIM (#157.1-.3) integration tests.
//
// The keystone behaviour NATE reported broken on mobile: closing the Layers
// panel KILLED the animation and dropped the scrubber, because the playback
// state + interval + scrubber all lived inside LayerPanel. After the fix:
//   #157.1 — playback (the `playing` flag + the advance interval) lives in the
//            module-level AnimationController and KEEPS RUNNING across a
//            LayerPanel unmount; the controller drives frame visibility via an
//            emitter (Map.tsx in prod) independent of the panel.
//   #157.2 — the scrubber renders WHENEVER a sequence is active on the
//            controller, regardless of whether the Layers panel is open.
//   #157.3 — the scrubber carries its own play/pause button wired to the
//            controller's playing state.
//
// These tests compose the SAME pieces App.tsx wires (LayerPanel as a control +
// an App-owned SequenceScrubber driven by the shared controller), so they
// exercise the real cross-component contract without booting the full App shell
// (WS / auth / map). The App-internal AppSequenceScrubber is mirrored here as a
// tiny harness with identical wiring.

import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, fireEvent, act, cleanup } from "@testing-library/react";
import { useMemo } from "react";
import { LayerPanel } from "./LayerPanel";
import { SequenceScrubber } from "./components/SequenceScrubber";
import {
  AnimationController,
  setAnimationController,
  getAnimationController,
  type AnimTimers,
  type FrameVisibilityEmitter,
} from "./lib/animation_controller";
import { useAnimationState } from "./lib/use_animation_controller";
import { LayerCache, setLayerCache } from "./lib/layer_cache";
import type { ProjectLayerSummary } from "./contracts";

// A controllable fake-timer seam so we can fire the controller's advance tick
// deterministically without real wall-clock time.
let fireTick: () => void = () => {};
function installFakeTimerController(): AnimationController {
  let cb: (() => void) | null = null;
  const timers: AnimTimers = {
    setInterval: (fn) => {
      cb = fn;
      return 1;
    },
    clearInterval: () => {
      cb = null;
    },
  };
  fireTick = () => cb?.();
  const c = new AnimationController({ timers });
  setAnimationController(c);
  return c;
}

const noopBackend = {
  async load() {
    return {};
  },
  async save() {
    /* no-op */
  },
};

beforeEach(() => {
  cleanup();
  setLayerCache(new LayerCache({ backend: noopBackend }));
  try {
    localStorage.clear();
  } catch {
    /* ignore */
  }
});

function makeFrame(hour: number): ProjectLayerSummary {
  const hh = String(hour).padStart(2, "0");
  return {
    layer_id: `run-a-f${hh}`,
    name: `HRRR precip F+${hh}h`,
    layer_type: "raster",
    uri: `s3://grace-2/runs/run-a/precip_f${hh}.cog.tif`,
    visible: true,
    opacity: 1,
    z_index: 1,
    style_preset: "hrrr_precip",
  };
}

const FRAMES = [makeFrame(1), makeFrame(3), makeFrame(6)];

// Mirror of App.tsx's AppSequenceScrubber: render the scrubber from the shared
// controller whenever a group is active, independent of the LayerPanel.
function AppScrubberHarness(): JSX.Element | null {
  const controller = useMemo(() => getAnimationController(), []);
  const anim = useAnimationState(controller);
  const active =
    anim.activeGroupKey != null
      ? anim.groups.find((g) => g.key === anim.activeGroupKey) ?? null
      : null;
  if (!active) return null;
  return (
    <SequenceScrubber
      label={active.label}
      frameLabels={active.frameLabels}
      activeIndex={controller.frameIndexFor(active.key)}
      onStep={(idx) => controller.stepGroupTo(active.key, idx)}
      playing={anim.playing}
      onPlayToggle={() => {
        controller.setActiveGroup(active.key);
        controller.togglePlaying();
      }}
    />
  );
}

describe("JOB WEB-ANIM #157.2 — scrubber renders whenever a sequence animates", () => {
  beforeEach(() => {
    installFakeTimerController();
  });

  it("renders the scrubber when a sequence is active even with the panel CLOSED", () => {
    // Render ONLY the App-owned scrubber harness (no LayerPanel = panel closed),
    // then push a group into the controller as LayerPanel would on mount.
    render(<AppScrubberHarness />);
    // No group yet -> no scrubber.
    expect(screen.queryByTestId("grace2-sequence-scrubber")).toBeNull();
    act(() => {
      getAnimationController().setGroups([
        {
          key: "grp",
          label: "HRRR precip",
          layerIds: ["run-a-f01", "run-a-f03", "run-a-f06"],
          frameLabels: ["F+01h", "F+03h", "F+06h"],
        },
      ]);
    });
    // Now the scrubber appears — panel never mounted.
    expect(screen.getByTestId("grace2-sequence-scrubber")).toBeInTheDocument();
  });

  it("keeps the scrubber mounted after the LayerPanel unmounts (panel close)", () => {
    // Mount the panel (detects + pushes the group) alongside the App scrubber.
    const panel = render(<LayerPanel initialLayers={FRAMES} />);
    render(<AppScrubberHarness />);
    expect(screen.getByTestId("grace2-sequence-scrubber")).toBeInTheDocument();
    // Close the panel (unmount) — the scrubber must stay because it is driven by
    // the controller, not by the panel's lifetime.
    act(() => {
      panel.unmount();
    });
    expect(screen.getByTestId("grace2-sequence-scrubber")).toBeInTheDocument();
  });
});

describe("JOB WEB-ANIM #157.1 — playback survives a LayerPanel unmount", () => {
  beforeEach(() => {
    installFakeTimerController();
  });

  it("keeps PLAYING + advancing frames after the panel unmounts", () => {
    const emitted: number[] = [];
    const emitter: FrameVisibilityEmitter = (_ids, idx) => emitted.push(idx);
    getAnimationController().setEmitter(emitter);

    // Panel mounts, detects the group, seeds the default (last) frame.
    const panel = render(<LayerPanel initialLayers={FRAMES} />);
    const ctrl = getAnimationController();
    expect(ctrl.getActiveGroup()).not.toBeNull();

    // Start from frame 0 and begin playback (as the play button would).
    act(() => {
      ctrl.stepGroupTo(ctrl.getActiveGroup()!.key, 0);
      ctrl.setPlaying(true);
    });
    expect(ctrl.isPlaying()).toBe(true);
    emitted.length = 0;

    // Close the Layers panel — the keystone scenario.
    act(() => {
      panel.unmount();
    });

    // The controller is STILL playing after unmount.
    expect(ctrl.isPlaying()).toBe(true);

    // And frames STILL advance on the next tick(s) — driving the emitter (the
    // map) even though the panel is gone.
    act(() => {
      fireTick(); // 0 -> 1
    });
    expect(ctrl.frameIndexFor(ctrl.getActiveGroup()!.key)).toBe(1);
    expect(emitted).toContain(1);
    act(() => {
      fireTick(); // 1 -> 2
    });
    expect(ctrl.frameIndexFor(ctrl.getActiveGroup()!.key)).toBe(2);
    expect(emitted).toContain(2);
  });
});

describe("JOB WEB-ANIM #157.3 — the scrubber play button toggles playing", () => {
  beforeEach(() => {
    installFakeTimerController();
  });

  it("clicking the scrubber play button flips the controller's playing flag", () => {
    render(<LayerPanel initialLayers={FRAMES} />);
    render(<AppScrubberHarness />);
    const ctrl = getAnimationController();
    expect(ctrl.isPlaying()).toBe(false);
    const playBtn = screen.getByTestId("scrubber-play");
    expect(playBtn).toHaveAttribute("aria-label", "Play sequence");
    act(() => {
      fireEvent.click(playBtn);
    });
    expect(ctrl.isPlaying()).toBe(true);
    // The button reflects the new state.
    expect(screen.getByTestId("scrubber-play")).toHaveAttribute(
      "aria-label",
      "Pause sequence",
    );
    act(() => {
      fireEvent.click(screen.getByTestId("scrubber-play"));
    });
    expect(ctrl.isPlaying()).toBe(false);
  });

  it("the scrubber advancing the controller also advances the panel-driven map", () => {
    // Step via the scrubber slider; the controller records the frame + emits.
    const emitted: number[] = [];
    getAnimationController().setEmitter((_ids, idx) => emitted.push(idx));
    render(<LayerPanel initialLayers={FRAMES} />);
    render(<AppScrubberHarness />);
    emitted.length = 0;
    act(() => {
      fireEvent.change(screen.getByTestId("scrubber-slider"), {
        target: { value: "0" },
      });
    });
    expect(getAnimationController().frameIndexFor(
      getAnimationController().getActiveGroup()!.key,
    )).toBe(0);
    expect(emitted).toContain(0);
  });
});

// --- Item b/c (NATE 2026-06-20) — mobile legend toggle + case-exit clearing --- //
//
// These compose the real pieces App.tsx wires: the MOBILE legend show/hide
// toggle rendered INSIDE the LayerPanel's expanded section (off the chat
// composer), and the AnimationController.reset() App calls on Case exit to clear
// the scrubber (which, on exit, the unmounting LayerPanel can no longer clear).
import {
  MobileLegendToggle,
  legendHasContent,
} from "./components/LayerLegend";

describe("Item b — mobile legend toggle lives INSIDE the Layers section", () => {
  beforeEach(() => {
    installFakeTimerController();
  });

  it("renders the MobileLegendToggle inside the LayerPanel body (not floating)", () => {
    // App passes <MobileLegendToggle/> as LayerPanel's `legendControl` on mobile.
    let hidden = false;
    render(
      <LayerPanel
        initialLayers={FRAMES}
        mobile
        legendControl={
          <MobileLegendToggle hidden={hidden} onToggle={(h) => (hidden = h)} />
        }
      />,
    );
    // The toggle sits in the panel's dedicated legend-control slot.
    const slot = screen.getByTestId("grace2-layer-panel-legend-control");
    const toggle = screen.getByTestId("grace2-mobile-legend-toggle");
    expect(slot.contains(toggle)).toBe(true);
    // It is a child of the Layers panel (in-flow), not portaled to the body root.
    const panel = screen.getByTestId("grace2-layer-panel");
    expect(panel.contains(toggle)).toBe(true);
  });

  it("LayerPanel renders no legend-control slot when none is supplied (desktop)", () => {
    render(<LayerPanel initialLayers={FRAMES} />);
    expect(screen.queryByTestId("grace2-layer-panel-legend-control")).toBeNull();
  });

  it("legendHasContent gates whether App renders the mobile toggle", () => {
    // A raster with a KNOWN preset has a legend => the toggle should render.
    const depth: ProjectLayerSummary = {
      layer_id: "depth-1",
      name: "Max flood depth",
      layer_type: "raster",
      uri: "s3://b/depth.cog.tif",
      visible: true,
      opacity: 1,
      z_index: 1,
      style_preset: "continuous_flood_depth",
    };
    expect(legendHasContent([depth])).toBe(true);
    // No eligible raster legend => no toggle.
    expect(legendHasContent([])).toBe(false);
  });
});

describe("Item c — Case exit clears the scrubber (controller reset)", () => {
  it("after the panel unmounts (Case exit) + reset(), the App scrubber clears", () => {
    installFakeTimerController();
    // Mount the panel (pushes the group) + the App scrubber harness.
    const panel = render(<LayerPanel initialLayers={FRAMES} />);
    render(<AppScrubberHarness />);
    expect(screen.getByTestId("grace2-sequence-scrubber")).toBeInTheDocument();
    // On Case EXIT: the LayerPanel unmounts (the rail shows the Cases list, not
    // CaseView) — so nothing re-pushes the old Case's groups — and App's
    // Case-switch handler resets the shared controller.
    act(() => {
      panel.unmount();
      getAnimationController().reset();
    });
    // The scrubber renders only when a group is active; reset cleared it, and
    // (unlike a panel close alone) nothing re-pushes the group, so the scrubber
    // stays gone — item c: the scrubber clears on Case exit.
    expect(screen.queryByTestId("grace2-sequence-scrubber")).toBeNull();
  });
});
