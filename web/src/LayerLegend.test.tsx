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

import { describe, it, expect } from "vitest";
import { render, screen, fireEvent, within } from "@testing-library/react";
import { LayerLegend } from "./components/LayerLegend";
import { ProjectLayerSummary } from "./contracts";

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
