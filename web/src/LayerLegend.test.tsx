// GRACE-2 web — LayerLegend unit tests.
//
// Covers the interactive AOI-snapping legend (NATE overlay-layout spec
// 2026-06-17), built on top of the original content contract:
//   CONTENT (preserved): one colorbar "key" per continuous-raster layer with a
//     known style_preset; title + min/max range labels; hides when nothing
//     eligible.
//   INTERACTION (new): each key is its own card (data-testid
//     "grace2-layer-legend-key"); keys snap COUNTER-CLOCKWISE to the AOI sides
//     (bottom, right, top, left) when an AOI rect (anchor + barWidth) is
//     present; keys are draggable + resizable; compact/flatten + hide toggles.
//
// The wrapper element (data-testid "grace2-layer-legend") is now a full-bleed,
// click-through container; the positioned/sized card is the KEY element.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, within } from "@testing-library/react";
import {
  LayerLegend,
  LEGEND_Z_INDEX,
  MOBILE_LEGEND_PILL_BOTTOM_CSS,
  MOBILE_LEGEND_PILL_CLEARANCE_PX,
  DESKTOP_LEGEND_PILL_BOTTOM_PX,
  MobileLegendToggle,
  legendHasContent,
} from "./components/LayerLegend";
import { ProjectLayerSummary } from "./contracts";
// ITEM 5 (NATE 2026-06-22)  -  the legend reads the shared AnimationController to
// know whether the SCRUBBER is showing (so it rails to the right of the bbox,
// vertically). Reset the process-global controller before every test so a group
// set by one test never bleeds into another's snap geometry.
import {
  AnimationController,
  setAnimationController,
  getAnimationController,
} from "./lib/animation_controller";

beforeEach(() => {
  setAnimationController(new AnimationController());
});

function makeLayer(overrides: Partial<ProjectLayerSummary> = {}): ProjectLayerSummary {
  return {
    layer_id: "layer-001",
    name: "Test layer",
    layer_type: "raster",
    uri: "gs://grace-2/runs/test/depth.cog.tif",
    visible: true,
    opacity: 1,
    z_index: 1,
    style_preset: "continuous_flood_depth",
    ...overrides,
  };
}

describe("LayerLegend — content contract (preserved)", () => {
  it("renders a key when a raster layer with a known preset is loaded", () => {
    render(<LayerLegend layers={[makeLayer()]} />);
    expect(screen.getByTestId("grace2-layer-legend")).toBeInTheDocument();
    expect(screen.getByTestId("grace2-layer-legend-key")).toBeInTheDocument();
  });

  it("shows the correct title for continuous_flood_depth", () => {
    render(<LayerLegend layers={[makeLayer()]} />);
    expect(screen.getByTestId("layer-legend-title")).toHaveTextContent(
      "Max flood depth (m)",
    );
  });

  it("shows min and max labels", () => {
    render(<LayerLegend layers={[makeLayer()]} />);
    expect(screen.getByTestId("layer-legend-min-label")).toHaveTextContent("0 m");
    expect(screen.getByTestId("layer-legend-max-label")).toHaveTextContent("3.5 m");
  });

  it("renders the gradient bar", () => {
    render(<LayerLegend layers={[makeLayer()]} />);
    expect(screen.getByTestId("layer-legend-bar")).toBeInTheDocument();
  });

  it("hides when no layers are loaded", () => {
    const { container } = render(<LayerLegend layers={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it("hides when the raster layer has no style_preset", () => {
    const { container } = render(
      <LayerLegend layers={[makeLayer({ style_preset: null })]} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("hides when the style_preset is unknown", () => {
    const { container } = render(
      <LayerLegend layers={[makeLayer({ style_preset: "unknown_preset_xyz" })]} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("hides when layers contain only vector layers (no raster)", () => {
    const { container } = render(
      <LayerLegend
        layers={[makeLayer({ layer_type: "vector", style_preset: "continuous_flood_depth" })]}
      />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("an anchor does not override the hide-when-no-preset behavior", () => {
    const { container } = render(
      <LayerLegend
        layers={[makeLayer({ style_preset: null })]}
        anchor={{ left: 100, top: 200 }}
        barWidth={200}
      />,
    );
    expect(container.firstChild).toBeNull();
  });
});

describe("LayerLegend — one key per eligible raster layer", () => {
  it("renders one key for each continuous-raster layer with a known preset", () => {
    const layers: ProjectLayerSummary[] = [
      makeLayer({ layer_id: "a", style_preset: "continuous_flood_depth", z_index: 3 }),
      makeLayer({ layer_id: "b", style_preset: "continuous_flood_depth", z_index: 2 }),
      makeLayer({ layer_id: "c", style_preset: null, z_index: 1 }),
    ];
    render(<LayerLegend layers={layers} />);
    expect(screen.getAllByTestId("grace2-layer-legend-key")).toHaveLength(2);
  });

  it("skips vector + unknown-preset layers when building keys", () => {
    const layers: ProjectLayerSummary[] = [
      makeLayer({ layer_id: "vec", layer_type: "vector" }),
      makeLayer({ layer_id: "ras", style_preset: "continuous_flood_depth" }),
      makeLayer({ layer_id: "bad", style_preset: "nope_xyz" }),
    ];
    render(<LayerLegend layers={layers} />);
    expect(screen.getAllByTestId("grace2-layer-legend-key")).toHaveLength(1);
  });
});

// Helper: build a sequential frame layer (same pattern as LayerPanel.test.tsx makeFrame).
function makeFrameLayer(hour: number, run = "run-a"): ProjectLayerSummary {
  const hh = String(hour).padStart(2, "0");
  return {
    layer_id: `${run}-f${hh}`,
    name: `HRRR precip F+${hh}h`,
    layer_type: "raster",
    uri: `gs://grace-2/runs/${run}/precip_f${hh}.cog.tif`,
    visible: true,
    opacity: 1,
    z_index: 1,
    style_preset: "continuous_flood_depth",
  };
}

describe("LayerLegend — ONE key per sequential group (item 1)", () => {
  it("collapses N frame layers into a single legend key (not N keys)", () => {
    // 3 HRRR forecast frames — all same preset, all form a sequential group.
    const layers = [makeFrameLayer(1), makeFrameLayer(3), makeFrameLayer(6)];
    render(<LayerLegend layers={layers} />);
    // Item 1: exactly ONE key for the whole group, not 3.
    expect(screen.getAllByTestId("grace2-layer-legend-key")).toHaveLength(1);
  });

  it("renders a key for the group's representative preset (same gradient)", () => {
    const layers = [makeFrameLayer(1), makeFrameLayer(3), makeFrameLayer(6)];
    render(<LayerLegend layers={layers} />);
    // The single group key still shows the shared preset's label and min/max.
    expect(screen.getByTestId("layer-legend-title")).toHaveTextContent("Max flood depth (m)");
    expect(screen.getByTestId("layer-legend-min-label")).toHaveTextContent("0 m");
    expect(screen.getByTestId("layer-legend-max-label")).toHaveTextContent("3.5 m");
  });

  it("non-grouped layers still get their own key alongside a group key", () => {
    // One sequential group (2 frames) + one unrelated raster = 2 keys total.
    const grouped1 = makeFrameLayer(1);
    const grouped2 = makeFrameLayer(3);
    const standalone = makeLayer({
      layer_id: "surge",
      name: "Storm surge max",
      style_preset: "continuous_flood_depth",
      z_index: 10,
    });
    render(<LayerLegend layers={[standalone, grouped1, grouped2]} />);
    expect(screen.getAllByTestId("grace2-layer-legend-key")).toHaveLength(2);
  });
});

describe("LayerLegend — AOI-less fallback placement", () => {
  it("places the key bottom-center when no anchor/barWidth is given", () => {
    render(<LayerLegend layers={[makeLayer()]} />);
    const key = screen.getByTestId("grace2-layer-legend-key");
    // Fallback: left:50% + bottom:24 + a translate (no absolute top).
    expect(key.style.left).toBe("50%");
    expect(key.style.bottom).toBe("24px");
    expect(key.style.transform).toContain("translate");
    expect(key.style.top).toBe("");
  });

  it("falls back to bottom-center when anchor is present but barWidth is null", () => {
    // Snapping needs the full AOI rect (anchor + width); width alone is missing.
    render(<LayerLegend layers={[makeLayer()]} anchor={{ left: 412, top: 300 }} />);
    const key = screen.getByTestId("grace2-layer-legend-key");
    expect(key.style.left).toBe("50%");
    expect(key.style.bottom).toBe("24px");
  });

  it("uses the default 320px key width when no barWidth is provided", () => {
    render(<LayerLegend layers={[makeLayer()]} />);
    expect(screen.getByTestId("grace2-layer-legend-key").style.width).toBe("320px");
  });

  it("clamps the default key width to the AOI on-screen width (barWidth)", () => {
    render(
      <LayerLegend
        layers={[makeLayer()]}
        anchor={{ left: 412, top: 300 }}
        barWidth={248}
      />,
    );
    expect(screen.getByTestId("grace2-layer-legend-key").style.width).toBe("248px");
  });

  it("does not change the value range / tick labels when sized by barWidth", () => {
    render(
      <LayerLegend
        layers={[makeLayer()]}
        anchor={{ left: 412, top: 300 }}
        barWidth={180}
      />,
    );
    expect(screen.getByTestId("layer-legend-min-label")).toHaveTextContent("0 m");
    expect(screen.getByTestId("layer-legend-max-label")).toHaveTextContent("3.5 m");
  });
});

describe("LayerLegend — CCW snapping to AOI sides", () => {
  // AOI rect reconstructed from anchor (bottom-edge midpoint) + barWidth.
  const anchor = { left: 500, top: 400 };
  const barWidth = 200;

  function fourKeys(): ProjectLayerSummary[] {
    return [0, 1, 2, 3].map((i) =>
      makeLayer({ layer_id: `k${i}`, z_index: 4 - i }),
    );
  }

  it("assigns sides counter-clockwise: bottom, right, top, left", () => {
    render(
      <LayerLegend layers={fourKeys()} anchor={anchor} barWidth={barWidth} />,
    );
    const keys = screen.getAllByTestId("grace2-layer-legend-key");
    expect(keys.map((k) => k.getAttribute("data-legend-side"))).toEqual([
      "bottom",
      "right",
      "top",
      "left",
    ]);
  });

  it("positions the first (bottom) key below the AOI bottom edge", () => {
    render(
      <LayerLegend layers={[makeLayer({ layer_id: "k0" })]} anchor={anchor} barWidth={barWidth} />,
    );
    const key = screen.getByTestId("grace2-layer-legend-key");
    // Absolute coords (not the 50%/bottom fallback) when an AOI rect exists.
    expect(key.style.left).not.toBe("50%");
    expect(key.style.bottom).toBe("");
    // The bottom key's top is below the AOI bottom edge (400) by the side gap.
    expect(parseFloat(key.style.top)).toBeGreaterThanOrEqual(400);
  });

  it("stacks a 5th key back onto the bottom side without reusing the 1st slot", () => {
    const layers = [0, 1, 2, 3, 4].map((i) => makeLayer({ layer_id: `k${i}` }));
    render(<LayerLegend layers={layers} anchor={anchor} barWidth={barWidth} />);
    const keys = screen.getAllByTestId("grace2-layer-legend-key");
    const sides = keys.map((k) => k.getAttribute("data-legend-side"));
    expect(sides).toEqual(["bottom", "right", "top", "left", "bottom"]);
    // The two bottom keys must not share the same top (they stack).
    const bottomTops = keys
      .filter((k) => k.getAttribute("data-legend-side") === "bottom")
      .map((k) => parseFloat(k.style.top));
    expect(bottomTops[0]).not.toBe(bottomTops[1]);
  });
});

// --- ITEM 5 (NATE 2026-06-22): legend goes vertical on the RIGHT when the
// sequence scrubber is showing (the bottom-center band is occupied by it). ---
describe("LayerLegend  -  scrubber-active right-side vertical rail (ITEM 5)", () => {
  const anchor = { left: 500, top: 400 };
  const barWidth = 200;

  // Build a real sequential group on the shared controller so the legend's
  // useAnimationState() reports an active group (scrubberActive === true).
  function activateScrubber(): void {
    const c = getAnimationController();
    c.setGroups([
      {
        key: "seq-1",
        label: "HRRR precip",
        layerIds: ["f01", "f03", "f06"],
        frameLabels: ["F+01h", "F+03h", "F+06h"],
      },
    ]);
    c.setActiveGroup("seq-1");
  }

  it("with NO scrubber, the first key stays on the BOTTOM (horizontal)", () => {
    render(
      <LayerLegend layers={[makeLayer({ layer_id: "k0" })]} anchor={anchor} barWidth={barWidth} />,
    );
    const key = screen.getByTestId("grace2-layer-legend-key");
    expect(key.getAttribute("data-legend-side")).toBe("bottom");
    expect(key.getAttribute("data-legend-orientation")).toBe("horizontal");
  });

  it("with the scrubber active, the first key rails on the RIGHT (vertical)", () => {
    activateScrubber();
    // A standalone (non-frame) raster so it still produces ONE legend key while a
    // separate sequence group drives the scrubber-active signal.
    render(
      <LayerLegend
        layers={[makeLayer({ layer_id: "standalone", name: "Storm surge max" })]}
        anchor={anchor}
        barWidth={barWidth}
      />,
    );
    const key = screen.getByTestId("grace2-layer-legend-key");
    expect(key.getAttribute("data-legend-side")).toBe("right");
    expect(key.getAttribute("data-legend-orientation")).toBe("vertical");
  });
});

describe("LayerLegend — snaps to the TRUE projected AOI rect (aoiRect)", () => {
  // A deliberately NON-SQUARE AOI rect: width 400, height 100. If the keys snap
  // off the real rect, the TOP key rails just above top=100; if they fell back to
  // the anchor+width square-ish ESTIMATE (height = width = 400) the top key would
  // be ~300px higher. This is the discriminating geometry for the fix.
  const trueRect = { left: 100, top: 100, right: 500, bottom: 200 };
  // The collapsed anchor+width the old path would have used (bottom midpoint +
  // east-west extent). These describe the SAME bottom edge but carry no height.
  const anchor = { left: 300, top: 200 };
  const barWidth = 400;

  function fourKeys(): ProjectLayerSummary[] {
    return [0, 1, 2, 3].map((i) => makeLayer({ layer_id: `tk${i}`, z_index: 4 - i }));
  }

  it("rails the bottom key just below the true bottom edge (200), not a square estimate", () => {
    render(
      <LayerLegend
        layers={[makeLayer({ layer_id: "tk0" })]}
        aoiRect={trueRect}
        anchor={anchor}
        barWidth={barWidth}
      />,
    );
    const key = screen.getByTestId("grace2-layer-legend-key");
    // Absolute coords (not the 50%/bottom fallback) when a rect is present.
    expect(key.style.left).not.toBe("50%");
    expect(key.style.bottom).toBe("");
    // Bottom side: top = bbox.bottom(200) + SIDE_GAP(10) = 210.
    expect(parseFloat(key.style.top)).toBeCloseTo(210, 0);
  });

  it("rails the top key against the SHORT (height=100) edge, proving the true rect is used", () => {
    render(
      <LayerLegend
        layers={fourKeys()}
        aoiRect={trueRect}
        anchor={anchor}
        barWidth={barWidth}
      />,
    );
    const keys = screen.getAllByTestId("grace2-layer-legend-key");
    const topKey = keys.find((k) => k.getAttribute("data-legend-side") === "top")!;
    // Top side off the TRUE rect: top = bbox.top(100) - SIDE_GAP(10) - keyHeight.
    // (keyHeight is the full ~64px stacking height.) This lands NEAR +26, i.e.
    // close to the real top edge (100). The square ESTIMATE (height=400) would put
    // the top edge at bottom-400 = -200, so the top key would sit far negative
    // (~-274) — so a non-negative-ish value here proves the true rect path.
    const top = parseFloat(topKey.style.top);
    expect(top).toBeGreaterThan(0);
    expect(top).toBeLessThan(100);
  });

  it("prefers aoiRect over anchor+width when both are supplied", () => {
    // Same anchor+width, but two DIFFERENT true rects → the top key must move with
    // the rect height, proving aoiRect (not the collapsed scalars) drives snapping.
    const shortRect = { left: 100, top: 100, right: 500, bottom: 200 }; // h=100
    const tallRect = { left: 100, top: -300, right: 500, bottom: 200 }; // h=500

    const { rerender } = render(
      <LayerLegend
        layers={fourKeys()}
        aoiRect={shortRect}
        anchor={anchor}
        barWidth={barWidth}
      />,
    );
    const topShort = parseFloat(
      screen
        .getAllByTestId("grace2-layer-legend-key")
        .find((k) => k.getAttribute("data-legend-side") === "top")!.style.top,
    );

    rerender(
      <LayerLegend
        layers={fourKeys()}
        aoiRect={tallRect}
        anchor={anchor}
        barWidth={barWidth}
      />,
    );
    const topTall = parseFloat(
      screen
        .getAllByTestId("grace2-layer-legend-key")
        .find((k) => k.getAttribute("data-legend-side") === "top")!.style.top,
    );

    // Taller rect → top edge is higher (smaller/negative y) → top key sits higher.
    expect(topTall).toBeLessThan(topShort);
  });

  it("falls back to the anchor+width estimate when aoiRect is absent", () => {
    // No aoiRect → reconstruct a square-ish rect from anchor + barWidth so the
    // legend still snaps (never silently breaks). Bottom key still rails the
    // exact bottom edge (anchor.top = 200).
    render(
      <LayerLegend
        layers={[makeLayer({ layer_id: "fb0" })]}
        anchor={anchor}
        barWidth={barWidth}
      />,
    );
    const key = screen.getByTestId("grace2-layer-legend-key");
    expect(key.style.left).not.toBe("50%");
    expect(parseFloat(key.style.top)).toBeCloseTo(210, 0);
  });
});

describe("LayerLegend — resize", () => {
  it("widens a key when the resize handle is dragged right", () => {
    render(
      <LayerLegend layers={[makeLayer()]} anchor={{ left: 400, top: 300 }} barWidth={200} />,
    );
    const key = screen.getByTestId("grace2-layer-legend-key");
    expect(key.style.width).toBe("200px");
    const handle = within(key).getByTestId("layer-legend-resize");
    fireEvent.pointerDown(handle, { clientX: 100, clientY: 100 });
    fireEvent.pointerMove(window, { clientX: 180, clientY: 100 });
    fireEvent.pointerUp(window);
    // 200 + 80 = 280.
    expect(screen.getByTestId("grace2-layer-legend-key").style.width).toBe("280px");
  });

  it("clamps resize to the min width", () => {
    render(<LayerLegend layers={[makeLayer()]} barWidth={200} />);
    const key = screen.getByTestId("grace2-layer-legend-key");
    const handle = within(key).getByTestId("layer-legend-resize");
    fireEvent.pointerDown(handle, { clientX: 100, clientY: 100 });
    fireEvent.pointerMove(window, { clientX: -500, clientY: 100 });
    fireEvent.pointerUp(window);
    // Clamped to KEY_MIN_WIDTH (140).
    expect(screen.getByTestId("grace2-layer-legend-key").style.width).toBe("140px");
  });
});

describe("LayerLegend — compact / flatten + hide toggles", () => {
  it("flattens a key: hides min/max labels in compact mode", () => {
    render(<LayerLegend layers={[makeLayer()]} />);
    expect(screen.getByTestId("layer-legend-min-label")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("layer-legend-compact-toggle"));
    expect(screen.queryByTestId("layer-legend-min-label")).toBeNull();
    // The gradient bar + title are still present (content preserved).
    expect(screen.getByTestId("layer-legend-bar")).toBeInTheDocument();
    expect(screen.getByTestId("layer-legend-title")).toBeInTheDocument();
    // Marked compact for downstream styling / verification.
    expect(
      screen.getByTestId("grace2-layer-legend-key").getAttribute("data-legend-compact"),
    ).toBe("1");
  });

  it("toggles compact back to full", () => {
    render(<LayerLegend layers={[makeLayer()]} />);
    const toggle = screen.getByTestId("layer-legend-compact-toggle");
    fireEvent.click(toggle);
    expect(screen.queryByTestId("layer-legend-min-label")).toBeNull();
    fireEvent.click(screen.getByTestId("layer-legend-compact-toggle"));
    expect(screen.getByTestId("layer-legend-min-label")).toBeInTheDocument();
  });

  it("hides the whole legend and shows a re-open pill", () => {
    render(<LayerLegend layers={[makeLayer()]} />);
    fireEvent.click(screen.getByTestId("layer-legend-hide"));
    expect(screen.queryByTestId("grace2-layer-legend-key")).toBeNull();
    expect(screen.getByTestId("grace2-layer-legend-show")).toBeInTheDocument();
  });

  it("re-shows the legend when the pill is clicked", () => {
    render(<LayerLegend layers={[makeLayer()]} />);
    fireEvent.click(screen.getByTestId("layer-legend-hide"));
    fireEvent.click(screen.getByTestId("grace2-layer-legend-show"));
    expect(screen.getByTestId("grace2-layer-legend-key")).toBeInTheDocument();
  });

  it("only the first key carries the global hide control", () => {
    const layers = [
      makeLayer({ layer_id: "a" }),
      makeLayer({ layer_id: "b" }),
    ];
    render(<LayerLegend layers={layers} anchor={{ left: 400, top: 300 }} barWidth={200} />);
    // Exactly one hide button across all keys.
    expect(screen.getAllByTestId("layer-legend-hide")).toHaveLength(1);
    // But every key has its own compact toggle.
    expect(screen.getAllByTestId("layer-legend-compact-toggle")).toHaveLength(2);
  });
});

// --- JOB WEB-AOI-LEGEND (#157) — "Show legend" pill clears the chat composer  //
//
// The collapsed re-open pill must NOT overlap the mobile chat composer (the
// bottom-sheet input form). On mobile it lifts above the composer (safe-area
// inset + clearance); on desktop (no bottom sheet) it keeps the low position.
describe("LayerLegend — Show-legend pill position vs mobile composer (#157)", () => {
  /** Mock useIsMobile's media query (max-width:767px) match for one render. */
  function mockIsMobile(mobile: boolean): () => void {
    const original = window.matchMedia;
    window.matchMedia = ((query: string) => ({
      matches: query.includes("max-width") ? mobile : false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })) as unknown as typeof window.matchMedia;
    return () => {
      window.matchMedia = original;
    };
  }

  it("the mobile pill offset references the safe-area inset + a positive clearance", () => {
    // Source-of-truth: a calc() over the device safe-area inset plus a fixed
    // clearance that lifts the pill clear of the bottom-sheet composer. (jsdom's
    // CSSOM drops calc(env(...)) from an inline `bottom`, so we pin the exported
    // constant directly — the same convention Chat's SHEET_BOTTOM_OFFSET_CSS uses.)
    expect(MOBILE_LEGEND_PILL_CLEARANCE_PX).toBeGreaterThan(DESKTOP_LEGEND_PILL_BOTTOM_PX);
    expect(MOBILE_LEGEND_PILL_BOTTOM_CSS).toBe(
      `calc(env(safe-area-inset-bottom) + ${MOBILE_LEGEND_PILL_CLEARANCE_PX}px)`,
    );
    expect(MOBILE_LEGEND_PILL_BOTTOM_CSS).toContain("env(safe-area-inset-bottom)");
  });

  it("keeps the pill at the low bottom-center position on DESKTOP", () => {
    const restore = mockIsMobile(false);
    try {
      render(<LayerLegend layers={[makeLayer()]} />);
      fireEvent.click(screen.getByTestId("layer-legend-hide"));
      const pill = screen.getByTestId("grace2-layer-legend-show");
      // Desktop: original low position (a bare px value jsdom stores fine),
      // no composer to clear.
      expect(pill.style.bottom).toBe(`${DESKTOP_LEGEND_PILL_BOTTOM_PX}px`);
    } finally {
      restore();
    }
  });

  it("does NOT use the bare desktop 24px position on MOBILE (would overlap the composer)", () => {
    const restore = mockIsMobile(true);
    try {
      render(<LayerLegend layers={[makeLayer()]} />);
      fireEvent.click(screen.getByTestId("layer-legend-hide"));
      const pill = screen.getByTestId("grace2-layer-legend-show");
      // The mobile branch sets a calc(env(...)) value; jsdom drops it to "" — the
      // key invariant is it is NOT the desktop 24px that overlapped the form.
      expect(pill.style.bottom).not.toBe(`${DESKTOP_LEGEND_PILL_BOTTOM_PX}px`);
    } finally {
      restore();
    }
  });
});

describe("LayerLegend — drag", () => {
  it("moves a key to a free position while dragging, then snaps back on release", () => {
    render(
      <LayerLegend layers={[makeLayer()]} anchor={{ left: 400, top: 300 }} barWidth={200} />,
    );
    const key = screen.getByTestId("grace2-layer-legend-key");
    const snappedLeft = key.style.left;
    // Start a drag on the card body (not a control / handle).
    fireEvent.pointerDown(key, { clientX: 410, clientY: 250 });
    fireEvent.pointerMove(window, { clientX: 600, clientY: 100 });
    const dragging = screen.getByTestId("grace2-layer-legend-key");
    // While dragging the key follows the pointer (free position) — left changes.
    expect(dragging.style.left).not.toBe(snappedLeft);
    fireEvent.pointerUp(window);
    // On release it snaps back to the CCW-derived position.
    expect(screen.getByTestId("grace2-layer-legend-key").style.left).toBe(snappedLeft);
  });

  it("does not start a drag from a control button", () => {
    render(
      <LayerLegend layers={[makeLayer()]} anchor={{ left: 400, top: 300 }} barWidth={200} />,
    );
    const key = screen.getByTestId("grace2-layer-legend-key");
    const snappedLeft = key.style.left;
    const toggle = screen.getByTestId("layer-legend-compact-toggle");
    // PointerDown on the control then move: the card should NOT follow.
    fireEvent.pointerDown(toggle, { clientX: 410, clientY: 250 });
    fireEvent.pointerMove(window, { clientX: 600, clientY: 100 });
    expect(screen.getByTestId("grace2-layer-legend-key").style.left).toBe(snappedLeft);
    fireEvent.pointerUp(window);
  });
});

// FRAME-TRUTH (NATE 2026-06-19) — the legend gradient + numeric bounds must
// match what the map actually paints, i.e. the TiTiler rescale + colormap_name
// embedded in the frame layer's XYZ tile-template URL (the SOURCE OF TRUTH),
// falling back to style_preset only when those params are absent / unknown.
describe("LayerLegend — TiTiler rescale + colormap from the tile URL (frame truth)", () => {
  // An AWS frame layer whose wms_url is a TiTiler XYZ template carrying the
  // truth as query params (rescale=lo,hi + colormap_name).
  function makeTitilerLayer(
    query: string,
    overrides: Partial<ProjectLayerSummary> = {},
  ): ProjectLayerSummary {
    return makeLayer({
      wms_url: `https://edge.example/cog/tiles/WebMercatorQuad/{z}/{x}/{y}.png?url=s3%3A%2F%2Fb%2Fk.tif${query}`,
      ...overrides,
    });
  }

  it("uses rescale=0,3.5 for the min/max labels (real bounds, not the preset)", () => {
    render(
      <LayerLegend
        layers={[makeTitilerLayer("&rescale=0,3.5&colormap_name=blues")]}
      />,
    );
    // The preset default for continuous_flood_depth is 0..3.5 m WITH a unit;
    // the URL rescale drops the unit (arbitrary-layer bounds) — assert exact.
    expect(screen.getByTestId("layer-legend-min-label").textContent).toBe("0");
    expect(screen.getByTestId("layer-legend-max-label").textContent).toBe("3.5");
  });

  it("renders the parsed-from-URL bounds even when they differ from the preset", () => {
    render(
      <LayerLegend
        layers={[makeTitilerLayer("&rescale=10,250&colormap_name=viridis")]}
      />,
    );
    expect(screen.getByTestId("layer-legend-min-label").textContent).toBe("10");
    expect(screen.getByTestId("layer-legend-max-label").textContent).toBe("250");
  });

  it("renders a blues gradient from colormap_name=blues", () => {
    render(
      <LayerLegend
        layers={[makeTitilerLayer("&rescale=0,3.5&colormap_name=blues")]}
      />,
    );
    const bar = screen.getByTestId("layer-legend-bar");
    // Blues ramp anchors: light #f7fbff -> dark #08519c (see titiler_colormap).
    expect(bar.style.background).toContain("#f7fbff");
    expect(bar.style.background).toContain("#08519c");
    // The flood preset gradient (rgba blues) must NOT be what painted here.
    expect(bar.style.background).not.toContain("rgba(8,48,107");
  });

  it("falls back to the style_preset gradient + bounds when the URL has no params", () => {
    // wms_url is a plain QGIS WMS endpoint (no rescale / colormap_name), and
    // uri is a gs:// pointer — neither carries TiTiler params.
    render(
      <LayerLegend
        layers={[
          makeLayer({
            wms_url: "https://qgis.example/ows/?SERVICE=WMS&LAYERS=depth",
          }),
        ]}
      />,
    );
    // Preset bounds (WITH unit) are preserved.
    expect(screen.getByTestId("layer-legend-min-label")).toHaveTextContent("0 m");
    expect(screen.getByTestId("layer-legend-max-label")).toHaveTextContent("3.5 m");
    // Preset gradient (rgba flood blues) still paints.
    const bar = screen.getByTestId("layer-legend-bar");
    expect(bar.style.background).toContain("rgba(8,48,107");
  });

  it("falls back to the preset gradient for an unknown colormap_name (but uses the URL rescale)", () => {
    render(
      <LayerLegend
        layers={[makeTitilerLayer("&rescale=0,7&colormap_name=nonexistent_cmap")]}
      />,
    );
    // Unknown colormap -> preset gradient fallback (rgba flood blues paints).
    const bar = screen.getByTestId("layer-legend-bar");
    expect(bar.style.background).toContain("rgba(8,48,107");
    expect(bar.style.background).not.toContain("#08519c");
    // The rescale IS valid, so the numeric bounds still come from the URL.
    expect(screen.getByTestId("layer-legend-min-label").textContent).toBe("0");
    expect(screen.getByTestId("layer-legend-max-label").textContent).toBe("7");
  });

  it("parses rescale + colormap from the `uri` field when wms_url lacks them", () => {
    // Some layers carry the TiTiler template in `uri` instead of `wms_url`.
    render(
      <LayerLegend
        layers={[
          makeLayer({
            wms_url: null,
            uri: "https://edge.example/cog/tiles/WebMercatorQuad/{z}/{x}/{y}.png?url=s3%3A%2F%2Fb%2Fk.tif&rescale=0,25&colormap_name=reds",
          }),
        ]}
      />,
    );
    expect(screen.getByTestId("layer-legend-min-label").textContent).toBe("0");
    expect(screen.getByTestId("layer-legend-max-label").textContent).toBe("25");
    // Reds ramp anchors (light #fff5f0 -> dark #a50f15).
    const bar = screen.getByTestId("layer-legend-bar");
    expect(bar.style.background).toContain("#fff5f0");
    expect(bar.style.background).toContain("#a50f15");
  });

  it("reflects the representative frame's rescale + colormap for a sequential group (item 4)", () => {
    // All frames in a group share rescale + colormap; parse from the first.
    function makeFrameTitiler(hour: number): ProjectLayerSummary {
      const hh = String(hour).padStart(2, "0");
      return {
        layer_id: `run-a-f${hh}`,
        name: `HRRR precip F+${hh}h`,
        layer_type: "raster",
        uri: `gs://grace-2/runs/run-a/precip_f${hh}.cog.tif`,
        wms_url: `https://edge.example/cog/tiles/WebMercatorQuad/{z}/{x}/{y}.png?url=s3%3A%2F%2Fb%2Fprecip_f${hh}.tif&rescale=0,100&colormap_name=blues`,
        visible: true,
        opacity: 1,
        z_index: 1,
        style_preset: "continuous_flood_depth",
      };
    }
    const layers = [makeFrameTitiler(1), makeFrameTitiler(3), makeFrameTitiler(6)];
    render(<LayerLegend layers={layers} />);
    // Exactly ONE group key, and its bounds + gradient come from the frames.
    expect(screen.getAllByTestId("grace2-layer-legend-key")).toHaveLength(1);
    expect(screen.getByTestId("layer-legend-min-label").textContent).toBe("0");
    expect(screen.getByTestId("layer-legend-max-label").textContent).toBe("100");
    const bar = screen.getByTestId("layer-legend-bar");
    expect(bar.style.background).toContain("#f7fbff");
  });
});

// --- Item a (Z-HIERARCHY, NATE 2026-06-20) — legend renders BELOW chat/layers - //
//
// The legend keys + the collapsed show-pill must paint BEHIND the chat panel
// (z=32) and the Layers/Cases panels (z=20) so they never cover the user's
// controls. (They previously used z=50, which painted OVER the chat — the bug.)
describe("LayerLegend — z-index below the chat + layers panels (item a)", () => {
  it("the key card z-index is below the chat (32) and layers panels (20)", () => {
    render(<LayerLegend layers={[makeLayer()]} />);
    const key = screen.getByTestId("grace2-layer-legend-key");
    const z = parseInt(key.style.zIndex, 10);
    expect(z).toBe(LEGEND_Z_INDEX);
    expect(z).toBeLessThan(20); // below the Layers/Cases panels
    expect(z).toBeLessThan(32); // below the chat panel
  });

  it("the collapsed show-pill z-index is also below chat + layers", () => {
    render(<LayerLegend layers={[makeLayer()]} />);
    fireEvent.click(screen.getByTestId("layer-legend-hide"));
    const pill = screen.getByTestId("grace2-layer-legend-show");
    const z = parseInt(pill.style.zIndex, 10);
    expect(z).toBe(LEGEND_Z_INDEX);
    expect(z).toBeLessThan(20);
    expect(z).toBeLessThan(32);
  });
});

// --- Item e (ONE LEGEND per flood-depth series) — peak folds into the frames -- //
//
// The per-frame depth COGs ("Flood depth step N") AND the max/peak depth layer
// all paint with the SAME colormap + rescale, so they form ONE series and must
// collapse to ONE legend key — not one-per-frame + a separate peak key.
describe("LayerLegend — one legend per depth series incl. the peak (item e)", () => {
  function depthFrame(hour: number): ProjectLayerSummary {
    const hh = String(hour).padStart(2, "0");
    return makeLayer({
      layer_id: `run-a-depth-f${hh}`,
      name: `Flood depth step ${hour}`,
      wms_url: `https://edge.example/cog/tiles/WebMercatorQuad/{z}/{x}/{y}.png?url=s3%3A%2F%2Fb%2Fdepth_f${hh}.tif&rescale=0,3.5&colormap_name=blues`,
    });
  }
  function peakDepth(): ProjectLayerSummary {
    // SAME colormap + rescale as the frames => same series.
    return makeLayer({
      layer_id: "run-a-depth-peak",
      name: "Max flood depth",
      wms_url:
        "https://edge.example/cog/tiles/WebMercatorQuad/{z}/{x}/{y}.png?url=s3%3A%2F%2Fb%2Fdepth_peak.tif&rescale=0,3.5&colormap_name=blues",
    });
  }

  it("collapses N depth frames + the peak into ONE legend key (series dedup)", () => {
    const layers = [peakDepth(), depthFrame(1), depthFrame(3), depthFrame(6)];
    render(<LayerLegend layers={layers} />);
    // Item e: exactly ONE key for the whole depth series (frames + peak).
    expect(screen.getAllByTestId("grace2-layer-legend-key")).toHaveLength(1);
  });

  it("a layer with a DIFFERENT colormap/scale still gets its own key", () => {
    const depthSeries = [depthFrame(1), depthFrame(3), peakDepth()];
    // A velocity raster — different colormap + rescale => different series.
    const velocity = makeLayer({
      layer_id: "run-a-velocity",
      name: "Flow velocity",
      wms_url:
        "https://edge.example/cog/tiles/WebMercatorQuad/{z}/{x}/{y}.png?url=s3%3A%2F%2Fb%2Fvel.tif&rescale=0,5&colormap_name=viridis",
    });
    render(<LayerLegend layers={[velocity, ...depthSeries]} />);
    // Two series => two keys (depth + velocity).
    expect(screen.getAllByTestId("grace2-layer-legend-key")).toHaveLength(2);
  });
});

// --- Item g (ORIENTATION) — vertical on left/right, horizontal on top/bottom -- //
describe("LayerLegend — orientation flips by docked side (item g)", () => {
  const anchor = { left: 500, top: 400 };
  const barWidth = 200;

  function fourKeys(): ProjectLayerSummary[] {
    // Four DISTINCT preset-only rasters (no URL colormap => not a series), so
    // each gets its own key and lands on its own CCW side.
    return [0, 1, 2, 3].map((i) =>
      makeLayer({ layer_id: `o${i}`, z_index: 4 - i }),
    );
  }

  it("the bottom + top keys are HORIZONTAL; the left + right keys are VERTICAL", () => {
    render(<LayerLegend layers={fourKeys()} anchor={anchor} barWidth={barWidth} />);
    const keys = screen.getAllByTestId("grace2-layer-legend-key");
    const bySide = (s: string) =>
      keys.find((k) => k.getAttribute("data-legend-side") === s)!;
    expect(bySide("bottom").getAttribute("data-legend-orientation")).toBe("horizontal");
    expect(bySide("top").getAttribute("data-legend-orientation")).toBe("horizontal");
    expect(bySide("right").getAttribute("data-legend-orientation")).toBe("vertical");
    expect(bySide("left").getAttribute("data-legend-orientation")).toBe("vertical");
  });

  it("a vertical (left/right) bar uses a to-top gradient; a horizontal one to-right", () => {
    render(<LayerLegend layers={fourKeys()} anchor={anchor} barWidth={barWidth} />);
    const keys = screen.getAllByTestId("grace2-layer-legend-key");
    const bySide = (s: string) =>
      keys.find((k) => k.getAttribute("data-legend-side") === s)!;
    const rightBar = within(bySide("right")).getByTestId("layer-legend-bar");
    const bottomBar = within(bySide("bottom")).getByTestId("layer-legend-bar");
    expect(rightBar.style.background).toContain("to top");
    expect(bottomBar.style.background).toContain("to right");
  });

  it("the AOI-less bottom-center fallback is horizontal", () => {
    render(<LayerLegend layers={[makeLayer()]} />);
    const key = screen.getByTestId("grace2-layer-legend-key");
    expect(key.getAttribute("data-legend-orientation")).toBe("horizontal");
  });
});

// --- Item d (SCALE WITH AOI) — overlay scales with the AOI on-screen px size -- //
describe("LayerLegend — scales the default key width with the AOI px size (item d)", () => {
  it("a tiny on-screen AOI yields a SMALLER default key than a large one", () => {
    // No barWidth override => the default width is sized off STATIC * scale,
    // which tracks the aoiRect's on-screen size (clamped). A tiny rect shrinks it.
    const tiny = { left: 100, top: 100, right: 140, bottom: 140 }; // 40px box
    const huge = { left: 0, top: 0, right: 1200, bottom: 1200 };
    const { rerender } = render(
      <LayerLegend layers={[makeLayer()]} aoiRect={tiny} />,
    );
    const tinyW = parseFloat(screen.getByTestId("grace2-layer-legend-key").style.width);
    rerender(<LayerLegend layers={[makeLayer()]} aoiRect={huge} />);
    const hugeW = parseFloat(screen.getByTestId("grace2-layer-legend-key").style.width);
    expect(tinyW).toBeLessThan(hugeW);
    // Both stay within the usable clamp band (never unusably tiny / huge).
    expect(tinyW).toBeGreaterThanOrEqual(140); // KEY_MIN_WIDTH floor
    expect(hugeW).toBeLessThanOrEqual(520); // KEY_MAX_WIDTH ceiling
  });
});

// --- Item f (legend not obscured by the scrubber) — bottom-reserve push ------- //
describe("LayerLegend — bottom key clears the scrubber footprint (item f)", () => {
  it("pushes the bottom-side key down by the supplied bottomReservePx", () => {
    const rect = { left: 100, top: 100, right: 500, bottom: 200 };
    const { rerender } = render(
      <LayerLegend layers={[makeLayer({ layer_id: "br0" })]} aoiRect={rect} />,
    );
    const baseTop = parseFloat(
      screen.getByTestId("grace2-layer-legend-key").style.top,
    );
    rerender(
      <LayerLegend
        layers={[makeLayer({ layer_id: "br0" })]}
        aoiRect={rect}
        bottomReservePx={60}
      />,
    );
    const reservedTop = parseFloat(
      screen.getByTestId("grace2-layer-legend-key").style.top,
    );
    // The bottom key is pushed DOWN (greater top) by the reserve so it clears
    // the scrubber that pins just below the AOI bottom edge.
    expect(reservedTop).toBeCloseTo(baseTop + 60, 0);
  });
});

// --- Item b (mobile controlled hide + suppressed pill) ------------------------ //
describe("LayerLegend — controlled hide + suppressed floating pill (item b)", () => {
  it("renders nothing for the pill when hidden + suppressShowPill (mobile)", () => {
    render(
      <LayerLegend layers={[makeLayer()]} hidden suppressShowPill />,
    );
    // No floating pill (the in-panel toggle is the only affordance on mobile).
    expect(screen.queryByTestId("grace2-layer-legend-show")).toBeNull();
    // And no keys (hidden).
    expect(screen.queryByTestId("grace2-layer-legend-key")).toBeNull();
  });

  it("honors the controlled `hidden` prop (parent owns the state)", () => {
    const { rerender } = render(
      <LayerLegend layers={[makeLayer()]} hidden={false} suppressShowPill />,
    );
    expect(screen.getByTestId("grace2-layer-legend-key")).toBeInTheDocument();
    rerender(<LayerLegend layers={[makeLayer()]} hidden suppressShowPill />);
    expect(screen.queryByTestId("grace2-layer-legend-key")).toBeNull();
  });

  it("fires onHiddenChange when the per-key hide control is clicked (controlled)", () => {
    const onHiddenChange = vi.fn();
    render(
      <LayerLegend
        layers={[makeLayer()]}
        hidden={false}
        onHiddenChange={onHiddenChange}
      />,
    );
    fireEvent.click(screen.getByTestId("layer-legend-hide"));
    expect(onHiddenChange).toHaveBeenCalledWith(true);
  });
});

// --- legendHasContent + MobileLegendToggle (item b helpers) ------------------- //
describe("legendHasContent helper", () => {
  it("is true when there is an eligible raster legend, false otherwise", () => {
    expect(legendHasContent([makeLayer()])).toBe(true);
    expect(legendHasContent([])).toBe(false);
    expect(legendHasContent([makeLayer({ style_preset: null })])).toBe(false);
  });
});

describe("MobileLegendToggle", () => {
  it("shows 'Hide legend' when visible and toggles to hidden on click", () => {
    const onToggle = vi.fn();
    render(<MobileLegendToggle hidden={false} onToggle={onToggle} />);
    const btn = screen.getByTestId("grace2-mobile-legend-toggle");
    expect(btn).toHaveTextContent("Hide legend");
    expect(btn).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(btn);
    expect(onToggle).toHaveBeenCalledWith(true);
  });

  it("shows 'Show legend' when hidden and toggles to visible on click", () => {
    const onToggle = vi.fn();
    render(<MobileLegendToggle hidden onToggle={onToggle} />);
    const btn = screen.getByTestId("grace2-mobile-legend-toggle");
    expect(btn).toHaveTextContent("Show legend");
    expect(btn).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(btn);
    expect(onToggle).toHaveBeenCalledWith(false);
  });
});
