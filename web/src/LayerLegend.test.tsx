// GRACE-2 web  -  LayerLegend unit tests.
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

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, within } from "@testing-library/react";
import {
  LayerLegend,
  LEGEND_Z_INDEX,
  MOBILE_LEGEND_PILL_BOTTOM_CSS,
  MOBILE_LEGEND_PILL_CLEARANCE_PX,
  DESKTOP_LEGEND_PILL_BOTTOM_PX,
  MOBILE_SHEET_DOCK_GAP_PX,
  MOBILE_LEGEND_MAX_WIDTH_CSS,
  MOBILE_LEGEND_VIEWPORT_MARGIN_PX,
  mobileLegendMaxHeightCss,
  sheetTopDockBottomPx,
  MobileLegendToggle,
  legendHasContent,
} from "./components/LayerLegend";
import { ProjectLayerSummary } from "./contracts";
import { getStylePreset } from "./lib/style-presets";
// ITEM 5 (NATE 2026-06-22)  -  the legend reads the shared AnimationController to
// know whether the SCRUBBER is showing (so it rails to the right of the bbox,
// vertically). Reset the process-global controller before every test so a group
// set by one test never bleeds into another's snap geometry.
import {
  AnimationController,
  setAnimationController,
  getAnimationController,
} from "./lib/animation_controller";

// LANE D (NATE's DECISION): the AOI-snap / drag / resize / scale / CCW legend
// behavior is now MOBILE-ONLY (on desktop the legend is a static bottom-center
// docked strip - see the "desktop docked legend" block at the end). So the snap
// pipeline tests below run in MOBILE mode: stub window.matchMedia so useIsMobile
// reports mobile by default. Desktop-specific tests (the #157 pill block and the
// desktop-dock block) override matchMedia per-test/per-block. Restored each test.
let _matchMediaOriginal: typeof window.matchMedia | undefined;
function stubMatchMedia(mobile: boolean): void {
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
}
beforeEach(() => {
  setAnimationController(new AnimationController());
  _matchMediaOriginal = window.matchMedia;
  stubMatchMedia(true); // default: mobile (the snap pipeline is mobile-only now)
});
afterEach(() => {
  window.matchMedia = _matchMediaOriginal as typeof window.matchMedia;
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

describe("LayerLegend  -  content contract (preserved)", () => {
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

describe("LayerLegend  -  one key per eligible raster layer", () => {
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

describe("LayerLegend  -  ONE key per sequential group (item 1)", () => {
  it("collapses N frame layers into a single legend key (not N keys)", () => {
    // 3 HRRR forecast frames  -  all same preset, all form a sequential group.
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

describe("LayerLegend  -  AOI-less fallback placement", () => {
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

describe("LayerLegend  -  CCW snapping to AOI sides", () => {
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

  it("a VERTICAL key renders NARROWER than the horizontal AOI width (item 2)", () => {
    // NATE item 2: a vertical (left/right-docked) key is a tall, NARROW bar, not
    // the full AOI-sized width (which made it nearly square). The horizontal key
    // uses barWidth (200); the vertical key must be substantially narrower.
    activateScrubber();
    render(
      <LayerLegend
        layers={[makeLayer({ layer_id: "standalone", name: "Storm surge max" })]}
        anchor={anchor}
        barWidth={barWidth}
      />,
    );
    const key = screen.getByTestId("grace2-layer-legend-key");
    expect(key.getAttribute("data-legend-orientation")).toBe("vertical");
    const w = parseFloat(key.style.width);
    // Narrow: well under both the 200px barWidth and the 140px horizontal min.
    expect(w).toBeLessThan(120);
    expect(w).toBeGreaterThan(0);
  });
});

// --- ITEM 3 + ITEM 4 (NATE 2026-06-23): a VERTICAL key rotates its title to
// read vertically (no truncation) and moves the X (hide) to the BOTTOM, inline
// with the colorbar. The horizontal key is unchanged. ----------------------- //
describe("LayerLegend  -  vertical key: rotated title + bottom X (ITEM 3/4)", () => {
  const anchor = { left: 500, top: 400 };
  const barWidth = 200;

  // Drive scrubber-active so the (only) key rails RIGHT -> vertical orientation.
  function activateScrubber(): void {
    const c = getAnimationController();
    c.setGroups([
      {
        key: "seq-v",
        label: "HRRR precip",
        layerIds: ["f01", "f03", "f06"],
        frameLabels: ["F+01h", "F+03h", "F+06h"],
      },
    ]);
    c.setActiveGroup("seq-v");
  }

  it("ITEM 3: the vertical title rotates to read vertically with NO ellipsis truncation", () => {
    activateScrubber();
    render(
      <LayerLegend
        layers={[makeLayer({ layer_id: "standalone", name: "Storm surge max" })]}
        anchor={anchor}
        barWidth={barWidth}
      />,
    );
    const key = screen.getByTestId("grace2-layer-legend-key");
    expect(key.getAttribute("data-legend-orientation")).toBe("vertical");
    const title = within(key).getByTestId("layer-legend-title");
    // Rotated to read vertically (writing-mode), not laid out horizontally.
    expect(title.style.writingMode).toBe("vertical-rl");
    // NO horizontal-ellipsis clamp (the bug was "Ma..." truncation).
    expect(title.style.whiteSpace).not.toBe("nowrap");
    expect(title.style.textOverflow).not.toBe("ellipsis");
    // The FULL label text is present (not clipped at the DOM level).
    const preset = getStylePreset("continuous_flood_depth");
    expect(title).toHaveTextContent(preset!.label);
  });

  it("ITEM 4: the X (hide) sits at the BOTTOM of the vertical key, inline with the bar", () => {
    activateScrubber();
    render(
      <LayerLegend
        layers={[makeLayer({ layer_id: "standalone", name: "Storm surge max" })]}
        anchor={anchor}
        barWidth={barWidth}
      />,
    );
    const key = screen.getByTestId("grace2-layer-legend-key");
    expect(key.getAttribute("data-legend-orientation")).toBe("vertical");
    // The X lives INSIDE the value-row column (with the bar + labels), at the
    // bottom - not in a top title row.
    const valueRow = within(key).getByTestId("layer-legend-value-row");
    const hide = within(key).getByTestId("layer-legend-hide");
    expect(valueRow.contains(hide)).toBe(true);
    // It is AFTER the min label in DOM order (bottom of the column). The min
    // label and the X are both children of the value-row column.
    const minLabel = within(key).getByTestId("layer-legend-min-label");
    const children = Array.from(valueRow.children);
    const minIdx = children.findIndex((c) => c.contains(minLabel));
    const hideIdx = children.findIndex((c) => c.contains(hide));
    expect(minIdx).toBeGreaterThanOrEqual(0);
    expect(hideIdx).toBeGreaterThan(minIdx);
  });

  it("the HORIZONTAL key keeps its top title row + X (unchanged)", () => {
    // No scrubber -> bottom/horizontal key. Title is in the top row (not rotated)
    // and the X is in that top row too.
    render(<LayerLegend layers={[makeLayer({ layer_id: "h0" })]} anchor={anchor} barWidth={barWidth} />);
    const key = screen.getByTestId("grace2-layer-legend-key");
    expect(key.getAttribute("data-legend-orientation")).toBe("horizontal");
    const title = within(key).getByTestId("layer-legend-title");
    // Horizontal title is NOT rotated and keeps its ellipsis clamp.
    expect(title.style.writingMode === "" || title.style.writingMode === "horizontal-tb").toBe(true);
    expect(title.style.whiteSpace).toBe("nowrap");
    // The X is NOT inside the value row for a horizontal key.
    const valueRow = within(key).getByTestId("layer-legend-value-row");
    const hide = within(key).getByTestId("layer-legend-hide");
    expect(valueRow.contains(hide)).toBe(false);
  });
});

describe("LayerLegend  -  snaps to the TRUE projected AOI rect (aoiRect)", () => {
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
    // (~-274)  -  so a non-negative-ish value here proves the true rect path.
    const top = parseFloat(topKey.style.top);
    expect(top).toBeGreaterThan(0);
    expect(top).toBeLessThan(100);
  });

  it("prefers aoiRect over anchor+width when both are supplied", () => {
    // Same anchor+width, but two DIFFERENT true rects -> the top key must move with
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

    // Taller rect -> top edge is higher (smaller/negative y) -> top key sits higher.
    expect(topTall).toBeLessThan(topShort);
  });

  it("falls back to the anchor+width estimate when aoiRect is absent", () => {
    // No aoiRect -> reconstruct a square-ish rect from anchor + barWidth so the
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

describe("LayerLegend  -  resize", () => {
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

describe("LayerLegend  -  hide toggle", () => {
  it("hides the whole legend and shows a re-open pill", () => {
    render(<LayerLegend layers={[makeLayer()]} />);
    fireEvent.click(screen.getByTestId("layer-legend-hide"));
    expect(screen.queryByTestId("grace2-layer-legend-key")).toBeNull();
    expect(screen.getByTestId("grace2-layer-legend-show")).toBeInTheDocument();
  });

  it("the hide control renders an X icon (NOT the old eye emoji) - item 1", () => {
    render(<LayerLegend layers={[makeLayer()]} />);
    const hide = screen.getByTestId("layer-legend-hide");
    // NATE item 1: the hide affordance is now the shared X icon (an <svg>), not
    // the U+1F441 eye emoji. Assert the glyph is an SVG and not the eye codepoint.
    expect(hide.querySelector("svg")).not.toBeNull();
    expect(hide.textContent ?? "").not.toContain("\u{1F441}");
    // aria-label + click behavior are unchanged (covered by the hide tests below).
    expect(hide.getAttribute("aria-label")).toBe("Hide legend");
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
    // LEGEND v2: there is NO compact/flatten toggle anymore (the key is always
    // the flat two-row card), so no compact-toggle control renders on any key.
    expect(screen.queryAllByTestId("layer-legend-compact-toggle")).toHaveLength(0);
  });
});

// --- JOB WEB-AOI-LEGEND (#157)  -  "Show legend" pill clears the chat composer  //
//
// The collapsed re-open pill must NOT overlap the mobile chat composer (the
// bottom-sheet input form). On mobile it lifts above the composer (safe-area
// inset + clearance); on desktop (no bottom sheet) it keeps the low position.
describe("LayerLegend  -  Show-legend pill position vs mobile composer (#157)", () => {
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
    // constant directly  -  the same convention Chat's SHEET_BOTTOM_OFFSET_CSS uses.)
    expect(MOBILE_LEGEND_PILL_CLEARANCE_PX).toBeGreaterThan(DESKTOP_LEGEND_PILL_BOTTOM_PX);
    expect(MOBILE_LEGEND_PILL_BOTTOM_CSS).toBe(
      `calc(env(safe-area-inset-bottom) + ${MOBILE_LEGEND_PILL_CLEARANCE_PX}px)`,
    );
    expect(MOBILE_LEGEND_PILL_BOTTOM_CSS).toContain("env(safe-area-inset-bottom)");
  });

  it("DESKTOP no longer has an in-legend hide control or floating pill (LANE D)", () => {
    // LANE D (NATE's DECISION): on desktop the legend is a static bottom-center
    // docked strip with NO in-legend hide control - the Show/Hide toggle moved
    // to BottomRowButtons (next to Settings), and the floating bottom-center
    // pill is gone. So neither the per-key hide button nor the floating
    // "show legend" pill render on desktop.
    const restore = mockIsMobile(false);
    try {
      render(<LayerLegend layers={[makeLayer()]} />);
      expect(screen.queryByTestId("layer-legend-hide")).toBeNull();
      expect(screen.queryByTestId("grace2-layer-legend-show")).toBeNull();
      // The docked strip itself renders (the legend content is still shown).
      expect(screen.getByTestId("grace2-layer-legend")).toHaveAttribute(
        "data-legend-docked",
        "desktop",
      );
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
      // The mobile branch sets a calc(env(...)) value; jsdom drops it to ""  -  the
      // key invariant is it is NOT the desktop 24px that overlapped the form.
      expect(pill.style.bottom).not.toBe(`${DESKTOP_LEGEND_PILL_BOTTOM_PX}px`);
    } finally {
      restore();
    }
  });
});

// --- DESKTOP SCRUBBER CLEARANCE (NATE 2026-06-27): the desktop docked legend
// strip (LANE D) must LIFT above the sequence scrubber's footprint while the
// scrubber is active so the scrubber (z51) never paints over the strip (z15).
// With the scrubber INACTIVE the strip stays at the bare DESKTOP_DOCK_BOTTOM_PX
// (16). This is the DESKTOP-ONLY (!isMobile) path; the mobile keys reserve the
// bottom band via excludeBottom and are unaffected. ------------------------- //
describe("LayerLegend  -  desktop docked strip clears the active scrubber", () => {
  /** Stub useIsMobile's media query (max-width:767px) match for one render. */
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

  // Drive the shared AnimationController so the legend's useAnimationState()
  // reports an active group (scrubberActive === true), exactly as the live app.
  function activateScrubber(): void {
    const c = getAnimationController();
    c.setGroups([
      {
        key: "seq-d",
        label: "HRRR precip",
        layerIds: ["f01", "f03", "f06"],
        frameLabels: ["F+01h", "F+03h", "F+06h"],
      },
    ]);
    c.setActiveGroup("seq-d");
  }

  it("with NO scrubber, the desktop strip stays at bottom 16px", () => {
    const restore = mockIsMobile(false);
    try {
      render(<LayerLegend layers={[makeLayer()]} />);
      const strip = screen.getByTestId("grace2-layer-legend");
      expect(strip).toHaveAttribute("data-legend-docked", "desktop");
      expect(strip.style.bottom).toBe("16px");
    } finally {
      restore();
    }
  });

  it("with the scrubber ACTIVE, the desktop strip lifts to bottom 76px (clears the scrubber)", () => {
    const restore = mockIsMobile(false);
    try {
      activateScrubber();
      // A standalone (non-frame) raster so it still emits ONE legend key while a
      // separate sequence group drives the scrubber-active signal.
      render(
        <LayerLegend
          layers={[makeLayer({ layer_id: "standalone", name: "Storm surge max" })]}
        />,
      );
      const strip = screen.getByTestId("grace2-layer-legend");
      expect(strip).toHaveAttribute("data-legend-docked", "desktop");
      // 16 (DESKTOP_DOCK_BOTTOM_PX) + 60 (52 footprint + 8 gap) = 76: the strip's
      // bottom edge sits at the top of the scrubber's reserved band (above its
      // ~66px top), so the z51 scrubber no longer covers the z15 legend.
      expect(strip.style.bottom).toBe("76px");
    } finally {
      restore();
    }
  });
});

// MOBILE SHEET-TOP DOCK (NATE 2026-06-24) - when App threads the chat sheet's
// top-edge Y (sheetTopPx), the mobile legend (colorbar keys + collapsed pill)
// must dock just ABOVE the sheet top - a clean band at the chat-panel top -
// instead of railing the AOI edges / floating over the map. beforeEach already
// stubs mobile=true; jsdom's window.innerHeight defaults to 768.
describe("LayerLegend  -  docks to the chat sheet top on mobile (sheetTopPx)", () => {
  it("sheetTopDockBottomPx computes viewportH - sheetTopPx + gap", () => {
    // window.innerHeight is 768 under jsdom.
    expect(sheetTopDockBottomPx(500)).toBe(768 - 500 + MOBILE_SHEET_DOCK_GAP_PX);
    expect(MOBILE_SHEET_DOCK_GAP_PX).toBeGreaterThan(0);
  });

  it("SNAPS the colorbar KEYS to the AOI bbox edge when the bbox is on screen (NATE 2026-06-26)", () => {
    // SNAP-TO-BBOX FIX (NATE 2026-06-26): when the AOI bbox IS projected on
    // screen the sheet-top band dock is SUPPRESSED and the keys snap to the REAL
    // bbox edges (railing like desktop), NOT a bottom-center band. The single key
    // lands on the BOTTOM edge: absolute top below the bbox bottom (300), with no
    // `bottom` offset and no left:50% band centering.
    render(
      <LayerLegend
        layers={[makeLayer()]}
        aoiRect={{ left: 100, top: 50, right: 400, bottom: 300 }}
        sheetTopPx={500}
      />,
    );
    const key = screen.getByTestId("grace2-layer-legend-key");
    // AOI-edge absolute position: NOT the band's bottom-from-sheet offset.
    expect(key.style.bottom).toBe("");
    expect(key.style.bottom).not.toBe(`${768 - 500 + MOBILE_SHEET_DOCK_GAP_PX}px`);
    // Absolute left (px), NOT the band's left:50% centering.
    expect(key.style.left).not.toBe("50%");
    // Bottom side rail: top just below the bbox bottom edge (300).
    expect(parseFloat(key.style.top)).toBeGreaterThanOrEqual(300);
  });

  it("docks the colorbar KEYS to the sheet-top band ONLY when NO AOI bbox is on screen (fallback)", () => {
    // No aoiRect -> the sheet-top band is the fallback so the keys still clear the
    // composer when no bbox is projected: a bottom-center band just above the sheet.
    render(<LayerLegend layers={[makeLayer()]} sheetTopPx={500} />);
    const key = screen.getByTestId("grace2-layer-legend-key");
    // Docked from the BOTTOM at viewportH - sheetTopPx + gap = 768-500+8 = 276.
    expect(key.style.bottom).toBe(`${768 - 500 + MOBILE_SHEET_DOCK_GAP_PX}px`);
    // Centered via left:50% + translate (the band convention), not an AOI-edge
    // absolute position.
    expect(key.style.left).toBe("50%");
  });

  it("docks the collapsed 'Show legend' PILL to the sheet top", () => {
    render(<LayerLegend layers={[makeLayer()]} sheetTopPx={500} />);
    fireEvent.click(screen.getByTestId("layer-legend-hide"));
    const pill = screen.getByTestId("grace2-layer-legend-show");
    expect(pill.style.bottom).toBe(`${768 - 500 + MOBILE_SHEET_DOCK_GAP_PX}px`);
  });

  it("a HIGHER sheet top (expanded sheet) docks the keys further up the screen", () => {
    // No aoiRect -> the band-dock path (which tracks the sheet top). With a bbox
    // on screen the keys snap to the bbox edge instead (covered above).
    const { rerender } = render(
      <LayerLegend layers={[makeLayer()]} sheetTopPx={700} />,
    );
    // Collapsed: top edge 700 -> bottom = 768-700+8 = 76.
    expect(screen.getByTestId("grace2-layer-legend-key").style.bottom).toBe(
      `${768 - 700 + MOBILE_SHEET_DOCK_GAP_PX}px`,
    );
    // Expanded: top edge rises to 300 -> bottom = 768-300+8 = 476 (higher up).
    rerender(<LayerLegend layers={[makeLayer()]} sheetTopPx={300} />);
    expect(screen.getByTestId("grace2-layer-legend-key").style.bottom).toBe(
      `${768 - 300 + MOBILE_SHEET_DOCK_GAP_PX}px`,
    );
  });
});

// MOBILE VIEWPORT CLAMP (NATE 2026-06-24 live-mobile feedback): "when we get the
// legend back it should stay the size of the window and not span past the window
// on mobile." The docked legend band must be CLAMPED to the viewport - a
// viewport-bounded max-width (so a fixed cardWidth wider than a narrow phone
// SHRINKS instead of overflowing), a capped max-height for the band, and
// scroll-within so nothing bleeds past the window edges. Notch insets respected
// via env(). beforeEach already stubs mobile=true; jsdom innerHeight is 768.
describe("LayerLegend  -  mobile legend clamps to the viewport (never spans past the window)", () => {
  it("MOBILE_LEGEND_MAX_WIDTH_CSS is a viewport-bounded calc() over 100dvw minus insets + margin", () => {
    expect(MOBILE_LEGEND_VIEWPORT_MARGIN_PX).toBeGreaterThan(0);
    // The max-width tracks the visual viewport (100dvw), subtracts the left/right
    // safe-area insets (notch) and a side margin on each edge. This guarantees it
    // is NEVER wider than the window.
    expect(MOBILE_LEGEND_MAX_WIDTH_CSS).toContain("100dvw");
    expect(MOBILE_LEGEND_MAX_WIDTH_CSS).toContain("env(safe-area-inset-left)");
    expect(MOBILE_LEGEND_MAX_WIDTH_CSS).toContain("env(safe-area-inset-right)");
    expect(MOBILE_LEGEND_MAX_WIDTH_CSS).toContain(
      `${MOBILE_LEGEND_VIEWPORT_MARGIN_PX * 2}px`,
    );
  });

  it("mobileLegendMaxHeightCss caps the band height below the docked sheet top (respecting the notch)", () => {
    const bottom = sheetTopDockBottomPx(500)!; // 768-500+8 = 276
    const css = mobileLegendMaxHeightCss(bottom);
    // Height = viewport height minus the docked-bottom offset minus the top inset
    // minus a margin, so the card cannot run off the TOP of the window.
    expect(css).toContain("100dvh");
    expect(css).toContain(`${bottom}px`);
    expect(css).toContain("env(safe-area-inset-top)");
    // Null (sheet top unknown) still returns a window-bounded cap (never unbounded).
    const fallback = mobileLegendMaxHeightCss(null);
    expect(fallback).toContain("100dvh");
    expect(fallback).toContain("env(safe-area-inset-top)");
  });

  it("the docked mobile key carries a viewport-bounded max-width (not a fixed width wider than the viewport)", () => {
    // A wide barWidth would otherwise fix the card at 520px - wider than a phone.
    // No aoiRect -> the sheet-top band path (the clamp lives there); with a bbox on
    // screen the keys snap to the bbox edge with no band clamp (NATE 2026-06-26).
    render(
      <LayerLegend
        layers={[makeLayer()]}
        barWidth={520}
        sheetTopPx={500}
      />,
    );
    const key = screen.getByTestId("grace2-layer-legend-key");
    // The card carries the viewport-bounded max-width clamp so it can never exceed
    // the window, regardless of the fixed cardWidth.
    expect(key.style.maxWidth).toBe(MOBILE_LEGEND_MAX_WIDTH_CSS);
    // And it scrolls within the clamp rather than spilling off either edge.
    expect(key.style.overflowX).toBe("auto");
    expect(key.style.overflowY).toBe("auto");
    expect(key.style.boxSizing).toBe("border-box");
    // Still centered (left:50% + translateX(-50%)) so the clamped card never runs
    // off the left/right edge.
    expect(key.style.left).toBe("50%");
    expect(key.style.transform).toContain("translate");
  });

  it("the docked mobile key carries a viewport-bounded max-height (cannot run off the top)", () => {
    // No aoiRect -> the sheet-top band path carries the max-height clamp (with a
    // bbox on screen the keys snap to the bbox edge, no clamp; NATE 2026-06-26).
    render(<LayerLegend layers={[makeLayer()]} sheetTopPx={500} />);
    const key = screen.getByTestId("grace2-layer-legend-key");
    const bottom = sheetTopDockBottomPx(500)!;
    expect(key.style.maxHeight).toBe(mobileLegendMaxHeightCss(bottom));
  });

  it("does NOT apply the viewport clamp when NOT docked to the sheet top (AOI-snap path unchanged)", () => {
    // No sheetTopPx -> the legacy AOI-snap mobile path. It must keep its absolute
    // sizing (no band clamp injected), so the AOI-edge rail behavior is untouched.
    render(
      <LayerLegend
        layers={[makeLayer()]}
        aoiRect={{ left: 100, top: 100, right: 500, bottom: 200 }}
        barWidth={200}
      />,
    );
    const key = screen.getByTestId("grace2-layer-legend-key");
    expect(key.style.maxWidth).toBe("");
    expect(key.style.maxHeight).toBe("");
    expect(key.style.overflowX).toBe("");
  });

  it("the docked 'Show legend' pill is also viewport-clamped on mobile", () => {
    render(<LayerLegend layers={[makeLayer()]} sheetTopPx={500} />);
    fireEvent.click(screen.getByTestId("layer-legend-hide"));
    const pill = screen.getByTestId("grace2-layer-legend-show");
    expect(pill.style.maxWidth).toBe(MOBILE_LEGEND_MAX_WIDTH_CSS);
  });
});

describe("LayerLegend  -  drag", () => {
  it("moves a key to a free position while dragging, then snaps to a side on release", () => {
    render(
      <LayerLegend layers={[makeLayer()]} anchor={{ left: 400, top: 300 }} barWidth={200} />,
    );
    const key = screen.getByTestId("grace2-layer-legend-key");
    const snappedLeft = key.style.left;
    // Start a drag on the card body (not a control / handle).
    fireEvent.pointerDown(key, { clientX: 410, clientY: 250 });
    fireEvent.pointerMove(window, { clientX: 600, clientY: 100 });
    const dragging = screen.getByTestId("grace2-layer-legend-key");
    // While dragging the key follows the pointer (free position)  -  left changes.
    expect(dragging.style.left).not.toBe(snappedLeft);
    fireEvent.pointerUp(window);
    // On release it SNAPS to a side (free position is dropped -> absolute snapped
    // coords, not the 50%/bottom fallback). SIDE-SNAP: it lands on whichever AOI
    // side it was dropped nearest, which is a settled absolute position.
    const released = screen.getByTestId("grace2-layer-legend-key");
    expect(released.style.left).not.toBe("50%");
    expect(released.style.left.endsWith("px")).toBe(true);
  });

  it("does not start a drag from a control button (the hide eye button)", () => {
    render(
      <LayerLegend layers={[makeLayer()]} anchor={{ left: 400, top: 300 }} barWidth={200} />,
    );
    const key = screen.getByTestId("grace2-layer-legend-key");
    const snappedLeft = key.style.left;
    // LEGEND v2: the hide(eye) button is the only per-key control; it is tagged
    // data-legend-no-drag, so a pointer-down on it must NOT free-drag the card.
    const hide = screen.getByTestId("layer-legend-hide");
    fireEvent.pointerDown(hide, { clientX: 410, clientY: 250 });
    fireEvent.pointerMove(window, { clientX: 600, clientY: 100 });
    expect(screen.getByTestId("grace2-layer-legend-key").style.left).toBe(snappedLeft);
    fireEvent.pointerUp(window);
  });

  it("does not start a drag from the resize handle (it resizes, not free-drags)", () => {
    render(
      <LayerLegend layers={[makeLayer()]} anchor={{ left: 400, top: 300 }} barWidth={200} />,
    );
    const key = screen.getByTestId("grace2-layer-legend-key");
    expect(key.style.width).toBe("200px");
    const handle = within(key).getByTestId("layer-legend-resize");
    // Pointer-down on the resize handle then drag right: this RESIZES (width
    // grows), it does NOT free-drag the card to the pointer position.
    fireEvent.pointerDown(handle, { clientX: 100, clientY: 250 });
    fireEvent.pointerMove(window, { clientX: 260, clientY: 100 });
    const after = screen.getByTestId("grace2-layer-legend-key");
    // Width grew by the drag delta (200 + 160 = 360)  -  the resize gesture ran.
    expect(after.style.width).toBe("360px");
    // The card stayed snapped (absolute top below the AOI bottom edge), not
    // teleported to the pointer's y (100) as a free-drag would.
    expect(parseFloat(after.style.top)).toBeGreaterThanOrEqual(300);
    fireEvent.pointerUp(window);
  });
});

// --- PART C (NATE 2026-06-22): drag the legend to a side -> it SNAPS there with
// the matching orientation (left/right -> vertical, top/bottom -> horizontal).
// The card BODY/EDGE is the drag handle (no dedicated grip icon); the snap +
// reorientation happen on release via legend_snap.nearestSide. -------------- //
describe("LayerLegend  -  drag-to-side snap + reorientation (PART C)", () => {
  // A wide, short AOI rect so the four sides are far apart and a dropped card
  // center maps unambiguously to the nearest edge. jsdom getBoundingClientRect
  // returns zeros, so the dropped card top-left == its center == (move - down).
  const rect = { left: 100, top: 100, right: 500, bottom: 200 };

  function dragCardCenterTo(x: number, y: number): void {
    const key = screen.getByTestId("grace2-layer-legend-key");
    // pointerDown at the origin so offsetX/offsetY are 0 (zeroed bbox), then the
    // move sets the free top-left to exactly (x, y) == the card center.
    fireEvent.pointerDown(key, { clientX: 0, clientY: 0 });
    fireEvent.pointerMove(window, { clientX: x, clientY: y });
    fireEvent.pointerUp(window);
  }

  it("dragging a bottom key to the RIGHT edge snaps it to the right + goes vertical", () => {
    render(<LayerLegend layers={[makeLayer({ layer_id: "s0" })]} aoiRect={rect} />);
    // Default key lands on the BOTTOM (horizontal).
    let key = screen.getByTestId("grace2-layer-legend-key");
    expect(key.getAttribute("data-legend-side")).toBe("bottom");
    expect(key.getAttribute("data-legend-orientation")).toBe("horizontal");
    // Drop the card center near the RIGHT edge (x-right=500, mid-height).
    dragCardCenterTo(490, 150);
    key = screen.getByTestId("grace2-layer-legend-key");
    expect(key.getAttribute("data-legend-side")).toBe("right");
    expect(key.getAttribute("data-legend-orientation")).toBe("vertical");
    // It snapped to an absolute position (not the free drag spot, not the fallback).
    expect(key.style.left).not.toBe("50%");
    // Right side: left = aoi.right(500) + gap. So it sits to the right of the box.
    expect(parseFloat(key.style.left)).toBeGreaterThan(500);
  });

  it("dragging to the TOP edge snaps to the top + stays horizontal", () => {
    render(<LayerLegend layers={[makeLayer({ layer_id: "s1" })]} aoiRect={rect} />);
    // Drop the card center near the TOP edge (y-top=100, mid-width).
    dragCardCenterTo(300, 110);
    const key = screen.getByTestId("grace2-layer-legend-key");
    expect(key.getAttribute("data-legend-side")).toBe("top");
    expect(key.getAttribute("data-legend-orientation")).toBe("horizontal");
  });

  it("dragging to the LEFT edge snaps to the left + goes vertical", () => {
    render(<LayerLegend layers={[makeLayer({ layer_id: "s2" })]} aoiRect={rect} />);
    dragCardCenterTo(110, 150);
    const key = screen.getByTestId("grace2-layer-legend-key");
    expect(key.getAttribute("data-legend-side")).toBe("left");
    expect(key.getAttribute("data-legend-orientation")).toBe("vertical");
  });

  it("the side override persists (the key stays where it was dragged)", () => {
    render(<LayerLegend layers={[makeLayer({ layer_id: "s3" })]} aoiRect={rect} />);
    dragCardCenterTo(490, 150); // -> right
    expect(
      screen.getByTestId("grace2-layer-legend-key").getAttribute("data-legend-side"),
    ).toBe("right");
    // A no-op rerender (same props) must not reset the snapped side.
    dragCardCenterTo(490, 150);
    expect(
      screen.getByTestId("grace2-layer-legend-key").getAttribute("data-legend-side"),
    ).toBe("right");
  });

  it("with NO AOI rect, a drag just clears free (no side override, stays bottom-center)", () => {
    render(<LayerLegend layers={[makeLayer({ layer_id: "s4" })]} />);
    const key = screen.getByTestId("grace2-layer-legend-key");
    // AOI-less fallback bottom-center.
    expect(key.style.left).toBe("50%");
    fireEvent.pointerDown(key, { clientX: 0, clientY: 0 });
    fireEvent.pointerMove(window, { clientX: 400, clientY: 50 });
    fireEvent.pointerUp(window);
    // No AOI to snap to -> back to the bottom-center fallback, no override.
    const after = screen.getByTestId("grace2-layer-legend-key");
    expect(after.style.left).toBe("50%");
    expect(after.getAttribute("data-legend-orientation")).toBe("horizontal");
  });
});

// --- PART C: the card body/edge IS the drag handle (no dedicated grip icon) -- //
describe("LayerLegend  -  body/edge is the drag handle, no grip icon (PART C)", () => {
  it("renders no dedicated drag-grip element (the body is grabbable)", () => {
    render(<LayerLegend layers={[makeLayer()]} aoiRect={{ left: 100, top: 100, right: 300, bottom: 250 }} />);
    // There must be no separate drag-handle/grip testid; the card itself drags.
    expect(screen.queryByTestId("layer-legend-drag-handle")).toBeNull();
    expect(screen.queryByTestId("layer-legend-grip")).toBeNull();
    // The card body carries grab affordance (cursor:grab) so an edge/body grab works.
    const key = screen.getByTestId("grace2-layer-legend-key");
    expect(key.style.cursor).toBe("grab");
  });

  it("a pointer-down on the card body (not a control) starts the drag", () => {
    render(<LayerLegend layers={[makeLayer()]} aoiRect={{ left: 100, top: 100, right: 500, bottom: 200 }} />);
    const key = screen.getByTestId("grace2-layer-legend-key");
    const startLeft = key.style.left;
    fireEvent.pointerDown(key, { clientX: 0, clientY: 0 });
    fireEvent.pointerMove(window, { clientX: 250, clientY: 400 });
    // The card follows the pointer (free position) while dragging from the body.
    expect(screen.getByTestId("grace2-layer-legend-key").style.left).not.toBe(startLeft);
    fireEvent.pointerUp(window);
  });
});

// =====================================================================
// LEGEND v2 (NATE 2026-06-22) - consolidated spec
//   1. minimal FLATTENED two-row key (no collapsible toggle)
//   2. edge/body drag handle, no grip glyph
//   3. drop-zone signals on drag-start at left/right/top
//   4. snap to LEFT/RIGHT/TOP only (bottom excluded)
// =====================================================================

// --- ITEM 1: minimal flattened two-row key, no collapse/expand toggle -------- //
describe("LayerLegend v2  -  flattened two-row key, no collapsible toggle (item 1)", () => {
  it("renders NO compact/flatten collapse toggle (the key is always flat)", () => {
    render(<LayerLegend layers={[makeLayer()]} />);
    // The old collapse/expand toggle is gone; the key is permanently flat.
    expect(screen.queryByTestId("layer-legend-compact-toggle")).toBeNull();
    // And there is no compact data-flag on the card anymore.
    const key = screen.getByTestId("grace2-layer-legend-key");
    expect(key.getAttribute("data-legend-compact")).toBeNull();
  });

  it("always shows title + min/max + bar together (flat key, nothing hidden)", () => {
    render(<LayerLegend layers={[makeLayer()]} />);
    expect(screen.getByTestId("layer-legend-title")).toBeInTheDocument();
    expect(screen.getByTestId("layer-legend-min-label")).toBeInTheDocument();
    expect(screen.getByTestId("layer-legend-max-label")).toBeInTheDocument();
    expect(screen.getByTestId("layer-legend-bar")).toBeInTheDocument();
  });

  it("HORIZONTAL key: min value at the LEFT end, max at the RIGHT end, flanking the bar", () => {
    // Bottom-docked (no scrubber) => horizontal. The value row is [min] bar [max].
    render(
      <LayerLegend
        layers={[makeLayer()]}
        anchor={{ left: 400, top: 300 }}
        barWidth={240}
      />,
    );
    const key = screen.getByTestId("grace2-layer-legend-key");
    expect(key.getAttribute("data-legend-orientation")).toBe("horizontal");
    const row = within(key).getByTestId("layer-legend-value-row");
    // DOM order within the row: min, bar, max  -  i.e. min flanks the LEFT end of
    // the bar and max flanks the RIGHT end.
    const kids = Array.from(row.children);
    const minIdx = kids.findIndex(
      (c) => c.getAttribute("data-testid") === "layer-legend-min-label",
    );
    const barIdx = kids.findIndex(
      (c) => c.getAttribute("data-testid") === "layer-legend-bar",
    );
    const maxIdx = kids.findIndex(
      (c) => c.getAttribute("data-testid") === "layer-legend-max-label",
    );
    expect(minIdx).toBeLessThan(barIdx);
    expect(barIdx).toBeLessThan(maxIdx);
    expect(within(row).getByTestId("layer-legend-min-label")).toHaveTextContent("0 m");
    expect(within(row).getByTestId("layer-legend-max-label")).toHaveTextContent("3.5 m");
  });

  it("VERTICAL key: max value at the TOP, min at the BOTTOM, flanking the bar", () => {
    // Drag a key to the RIGHT edge so it goes vertical, then assert the rotated
    // value placement (max above the bar, min below it).
    const rect = { left: 100, top: 100, right: 500, bottom: 200 };
    render(<LayerLegend layers={[makeLayer({ layer_id: "v0" })]} aoiRect={rect} />);
    const key0 = screen.getByTestId("grace2-layer-legend-key");
    fireEvent.pointerDown(key0, { clientX: 0, clientY: 0 });
    fireEvent.pointerMove(window, { clientX: 490, clientY: 150 }); // near right edge
    fireEvent.pointerUp(window);
    const key = screen.getByTestId("grace2-layer-legend-key");
    expect(key.getAttribute("data-legend-orientation")).toBe("vertical");
    const row = within(key).getByTestId("layer-legend-value-row");
    const kids = Array.from(row.children);
    const maxIdx = kids.findIndex(
      (c) => c.getAttribute("data-testid") === "layer-legend-max-label",
    );
    const barIdx = kids.findIndex(
      (c) => c.getAttribute("data-testid") === "layer-legend-bar",
    );
    const minIdx = kids.findIndex(
      (c) => c.getAttribute("data-testid") === "layer-legend-min-label",
    );
    // Vertical: max is ABOVE the bar (earlier in column flow), min is BELOW it.
    expect(maxIdx).toBeLessThan(barIdx);
    expect(barIdx).toBeLessThan(minIdx);
  });
});

// --- ITEM 2: edge/body drag handle, no dedicated grip glyph ------------------ //
describe("LayerLegend v2  -  edge/body is the drag handle, no grip glyph (item 2)", () => {
  it("renders no drag-grip glyph element; the card body carries cursor:grab", () => {
    render(
      <LayerLegend layers={[makeLayer()]} aoiRect={{ left: 100, top: 100, right: 300, bottom: 250 }} />,
    );
    expect(screen.queryByTestId("layer-legend-drag-handle")).toBeNull();
    expect(screen.queryByTestId("layer-legend-grip")).toBeNull();
    const key = screen.getByTestId("grace2-layer-legend-key");
    expect(key.style.cursor).toBe("grab");
    // The resize handle no longer paints a diagonal grip glyph (just a hit-target).
    const resize = within(key).getByTestId("layer-legend-resize");
    expect(resize.style.backgroundImage === "" || resize.style.backgroundImage === "none").toBe(
      true,
    );
  });

  it("the hide control is excluded from drag (data-legend-no-drag)", () => {
    render(<LayerLegend layers={[makeLayer()]} aoiRect={{ left: 100, top: 100, right: 500, bottom: 200 }} />);
    const hide = screen.getByTestId("layer-legend-hide");
    // The control (or an ancestor up to the card) carries the no-drag marker.
    expect(hide.closest("[data-legend-no-drag]")).not.toBeNull();
  });
});

// --- ITEM 3: drop-zone signals appear on drag-start at left/right/top -------- //
describe("LayerLegend v2  -  drop-zone signals on drag-start (item 3)", () => {
  const rect = { left: 100, top: 100, right: 500, bottom: 300 };

  it("shows NO drop-zone signals when idle (not dragging)", () => {
    render(<LayerLegend layers={[makeLayer({ layer_id: "d0" })]} aoiRect={rect} />);
    expect(screen.queryAllByTestId("layer-legend-dropzone")).toHaveLength(0);
  });

  it("on drag-start renders signals at exactly left/right/top (never bottom)", () => {
    render(<LayerLegend layers={[makeLayer({ layer_id: "d1" })]} aoiRect={rect} />);
    const key = screen.getByTestId("grace2-layer-legend-key");
    fireEvent.pointerDown(key, { clientX: 0, clientY: 0 });
    const zones = screen.getAllByTestId("layer-legend-dropzone");
    expect(zones).toHaveLength(3);
    const sides = zones.map((z) => z.getAttribute("data-legend-dropzone-side")).sort();
    expect(sides).toEqual(["left", "right", "top"]);
    expect(sides).not.toContain("bottom");
    fireEvent.pointerUp(window);
  });

  it("highlights the nearest target as active while dragging toward it", () => {
    render(<LayerLegend layers={[makeLayer({ layer_id: "d2" })]} aoiRect={rect} />);
    const key = screen.getByTestId("grace2-layer-legend-key");
    fireEvent.pointerDown(key, { clientX: 0, clientY: 0 });
    // Drag the card center toward the RIGHT edge (x near 500, mid-height).
    fireEvent.pointerMove(window, { clientX: 495, clientY: 200 });
    const zones = screen.getAllByTestId("layer-legend-dropzone");
    const active = zones.filter((z) => z.getAttribute("data-legend-dropzone-active") === "1");
    // Exactly one is active and it is the RIGHT target.
    expect(active).toHaveLength(1);
    expect(active[0]!.getAttribute("data-legend-dropzone-side")).toBe("right");
    fireEvent.pointerUp(window);
  });

  it("clears all drop-zone signals on release", () => {
    render(<LayerLegend layers={[makeLayer({ layer_id: "d3" })]} aoiRect={rect} />);
    const key = screen.getByTestId("grace2-layer-legend-key");
    fireEvent.pointerDown(key, { clientX: 0, clientY: 0 });
    expect(screen.getAllByTestId("layer-legend-dropzone").length).toBeGreaterThan(0);
    fireEvent.pointerUp(window);
    expect(screen.queryAllByTestId("layer-legend-dropzone")).toHaveLength(0);
  });

  it("renders no drop-zone signals when there is no AOI rect to snap against", () => {
    render(<LayerLegend layers={[makeLayer({ layer_id: "d4" })]} />);
    const key = screen.getByTestId("grace2-layer-legend-key");
    fireEvent.pointerDown(key, { clientX: 0, clientY: 0 });
    // No AOI => nothing to snap to => no signals.
    expect(screen.queryAllByTestId("layer-legend-dropzone")).toHaveLength(0);
    fireEvent.pointerUp(window);
  });
});

// --- ITEM 4: snap to LEFT/RIGHT/TOP only (bottom excluded) ------------------- //
describe("LayerLegend v2  -  bottom-excluded snap (item 4)", () => {
  // Tall, narrow AOI so a drag toward the bottom is unambiguously nearer the
  // bottom edge than left/right/top in raw pixels - proving the EXCLUSION (not
  // just geometry) is what keeps the key off the bottom.
  const rect = { left: 100, top: 100, right: 300, bottom: 600 };

  function dragCardCenterTo(x: number, y: number): void {
    const key = screen.getByTestId("grace2-layer-legend-key");
    fireEvent.pointerDown(key, { clientX: 0, clientY: 0 });
    fireEvent.pointerMove(window, { clientX: x, clientY: y });
    fireEvent.pointerUp(window);
  }

  it("dragging toward the BOTTOM edge snaps to the nearest of left/right/top (never bottom)", () => {
    render(<LayerLegend layers={[makeLayer({ layer_id: "b0" })]} aoiRect={rect} />);
    // Drop the card center just below the bottom edge, slightly right of center:
    // in raw px the bottom edge (y=600) is closest, but bottom is EXCLUDED, so it
    // must snap to the nearest valid side (right, since x is closer to the right).
    dragCardCenterTo(260, 590);
    const key = screen.getByTestId("grace2-layer-legend-key");
    const side = key.getAttribute("data-legend-side");
    expect(side).not.toBe("bottom");
    expect(["left", "right", "top"]).toContain(side);
    expect(side).toBe("right");
    // Right dock => vertical orientation.
    expect(key.getAttribute("data-legend-orientation")).toBe("vertical");
  });

  it("dragging toward the bottom-LEFT snaps to the left (not bottom)", () => {
    render(<LayerLegend layers={[makeLayer({ layer_id: "b1" })]} aoiRect={rect} />);
    dragCardCenterTo(140, 590);
    const key = screen.getByTestId("grace2-layer-legend-key");
    expect(key.getAttribute("data-legend-side")).toBe("left");
  });

  it("an explicit drag toward the TOP still snaps to the top (horizontal)", () => {
    render(<LayerLegend layers={[makeLayer({ layer_id: "b2" })]} aoiRect={rect} />);
    dragCardCenterTo(200, 110);
    const key = screen.getByTestId("grace2-layer-legend-key");
    expect(key.getAttribute("data-legend-side")).toBe("top");
    expect(key.getAttribute("data-legend-orientation")).toBe("horizontal");
  });
});

// FRAME-TRUTH (NATE 2026-06-19)  -  the legend gradient + numeric bounds must
// match what the map actually paints, i.e. the TiTiler rescale + colormap_name
// embedded in the frame layer's XYZ tile-template URL (the SOURCE OF TRUTH),
// falling back to style_preset only when those params are absent / unknown.
describe("LayerLegend  -  TiTiler rescale + colormap from the tile URL (frame truth)", () => {
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
    // the URL rescale drops the unit (arbitrary-layer bounds)  -  assert exact.
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
    // uri is a gs:// pointer  -  neither carries TiTiler params.
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

// --- Item a (Z-HIERARCHY, NATE 2026-06-20)  -  legend renders BELOW chat/layers - //
//
// The legend keys + the collapsed show-pill must paint BEHIND the chat panel
// (z=32) and the Layers/Cases panels (z=20) so they never cover the user's
// controls. (They previously used z=50, which painted OVER the chat  -  the bug.)
describe("LayerLegend  -  z-index below the chat + layers panels (item a)", () => {
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

// --- Item e (ONE LEGEND per flood-depth series)  -  peak folds into the frames -- //
//
// The per-frame depth COGs ("Flood depth step N") AND the max/peak depth layer
// all paint with the SAME colormap + rescale, so they form ONE series and must
// collapse to ONE legend key  -  not one-per-frame + a separate peak key.
describe("LayerLegend  -  one legend per depth series incl. the peak (item e)", () => {
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
    // A velocity raster  -  different colormap + rescale => different series.
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

// --- Item g (ORIENTATION)  -  vertical on left/right, horizontal on top/bottom -- //
describe("LayerLegend  -  orientation flips by docked side (item g)", () => {
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

// --- Item d (SCALE WITH AOI)  -  overlay scales with the AOI on-screen px size -- //
describe("LayerLegend  -  scales the default key width with the AOI px size (item d)", () => {
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

// --- Item f (legend not obscured by the scrubber)  -  bottom-reserve push ------- //
describe("LayerLegend  -  bottom key clears the scrubber footprint (item f)", () => {
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
describe("LayerLegend  -  controlled hide + suppressed floating pill (item b)", () => {
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

// --- LANE D: DESKTOP docked legend strip (NATE's DECISION) -------------------- //
//
// On DESKTOP the legend is a single STATIC bottom-center docked strip: fixed
// size, NO scaling, NO drag, NO resize, NO AOI-snap. The whole snap/drag/resize
// machinery is mobile-only (tested above with the mobile matchMedia stub). These
// tests force DESKTOP (matchMedia mobile=false).
describe("LayerLegend  -  desktop docked strip (LANE D)", () => {
  beforeEach(() => {
    stubMatchMedia(false); // DESKTOP
  });

  it("renders a static bottom-center docked strip (no snap/drag/resize)", () => {
    render(<LayerLegend layers={[makeLayer()]} />);
    const root = screen.getByTestId("grace2-layer-legend");
    expect(root).toHaveAttribute("data-legend-docked", "desktop");
    // The docked strip pins to a fixed bottom; it is NOT an AOI-snapped card.
    expect(root.style.position).toBe("fixed");
    expect(root.style.bottom).toBe("16px");
    // No drag handle / resize handle / drop-zones on desktop.
    expect(screen.queryByTestId("layer-legend-resize")).toBeNull();
    expect(screen.queryByTestId("layer-legend-dropzone")).toBeNull();
    // The key + title still render (content contract preserved).
    expect(screen.getByTestId("grace2-layer-legend-key")).toBeInTheDocument();
    expect(screen.getByTestId("layer-legend-title")).toHaveTextContent(
      "Max flood depth (m)",
    );
  });

  it("the (m) unit uses a non-breaking space so it never wraps from the value", () => {
    render(<LayerLegend layers={[makeLayer()]} />);
    const minLabel = screen.getByTestId("layer-legend-min-label");
    // The label is "<value> <unit>" (NBSP), never a regular space (which
    // could wrap). continuous_flood_depth has unit "m".
    expect(minLabel.textContent).toBe("0\u00a0m"); // NBSP between value + unit
    expect(minLabel.textContent).not.toContain("0 m"); // never a plain ASCII space
    // The gradient bar is the flex element that absorbs slack (flex:1; minWidth:0).
    const bar = screen.getByTestId("layer-legend-bar");
    expect(bar.style.flexGrow).toBe("1");
    // minWidth:0 lets the bar shrink to absorb slack (happy-dom stores "0").
    expect(bar.style.minWidth).toBe("0");
  });

  it("does not render the AOI-snapped multi-card wrapper attributes on desktop", () => {
    render(
      <LayerLegend
        layers={[makeLayer({ layer_id: "a" }), makeLayer({ layer_id: "b" })]}
        aoiRect={{ left: 100, top: 100, right: 500, bottom: 300 }}
      />,
    );
    // Even with an aoiRect supplied, desktop ignores the snap pipeline: the keys
    // carry no per-side snap (all bottom/horizontal) and there is exactly one
    // docked root.
    const roots = screen.getAllByTestId("grace2-layer-legend");
    expect(roots).toHaveLength(1);
    for (const key of screen.getAllByTestId("grace2-layer-legend-key")) {
      expect(key.getAttribute("data-legend-orientation")).toBe("horizontal");
      expect(key.getAttribute("data-legend-side")).toBe("bottom");
    }
  });

  it("renders nothing when hidden + suppressShowPill (desktop pill is in BottomRowButtons)", () => {
    render(<LayerLegend layers={[makeLayer()]} hidden suppressShowPill />);
    expect(screen.queryByTestId("grace2-layer-legend")).toBeNull();
    expect(screen.queryByTestId("grace2-layer-legend-show")).toBeNull();
  });
});
