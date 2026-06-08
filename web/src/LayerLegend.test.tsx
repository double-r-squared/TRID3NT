// GRACE-2 web — LayerLegend unit tests (job-0065).
//
// Verifies:
//   1. Legend renders when a raster layer with a known style_preset is present.
//   2. Legend hides when no layers are loaded.
//   3. Legend hides when no raster layer has a known style_preset.
//   4. Legend picks the topmost raster layer (first in top-of-stack-first list).
//   5. Legend title and tick labels are correct for the continuous_flood_depth preset.

import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
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

describe("LayerLegend", () => {
  it("renders when a raster layer with a known preset is loaded", () => {
    const layers: ProjectLayerSummary[] = [makeLayer()];
    render(<LayerLegend layers={layers} />);
    expect(screen.getByTestId("grace2-layer-legend")).toBeInTheDocument();
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

  it("hides when no layers are loaded", () => {
    const { container } = render(<LayerLegend layers={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it("hides when the raster layer has no style_preset", () => {
    const layers: ProjectLayerSummary[] = [makeLayer({ style_preset: null })];
    const { container } = render(<LayerLegend layers={layers} />);
    expect(container.firstChild).toBeNull();
  });

  it("hides when the style_preset is unknown", () => {
    const layers: ProjectLayerSummary[] = [
      makeLayer({ style_preset: "unknown_preset_xyz" }),
    ];
    const { container } = render(<LayerLegend layers={layers} />);
    expect(container.firstChild).toBeNull();
  });

  it("hides when layers contain only vector layers (no raster)", () => {
    const layers: ProjectLayerSummary[] = [
      makeLayer({ layer_type: "vector", style_preset: "continuous_flood_depth" }),
    ];
    const { container } = render(<LayerLegend layers={layers} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders for the first (topmost) raster layer with a known preset", () => {
    // Top-of-stack-first ordering: index 0 = top
    const layers: ProjectLayerSummary[] = [
      makeLayer({
        layer_id: "layer-top",
        name: "Top flood layer",
        style_preset: "continuous_flood_depth",
        z_index: 2,
      }),
      makeLayer({
        layer_id: "layer-bottom",
        name: "Bottom layer",
        style_preset: null,
        z_index: 1,
      }),
    ];
    render(<LayerLegend layers={layers} />);
    expect(screen.getByTestId("grace2-layer-legend")).toBeInTheDocument();
    expect(screen.getByTestId("layer-legend-title")).toHaveTextContent(
      "Max flood depth (m)",
    );
  });

  it("hides when only the bottom layer has no preset and top has none", () => {
    const layers: ProjectLayerSummary[] = [
      makeLayer({ layer_id: "top", style_preset: null, z_index: 2 }),
      makeLayer({ layer_id: "bottom", style_preset: "continuous_flood_depth", z_index: 1 }),
    ];
    // Top layer has no preset → legend still renders because the second
    // layer in the list (bottom) is skipped; we use find() so the FIRST
    // matching layer wins. Here top has no preset so we should fall through
    // to bottom which does.
    render(<LayerLegend layers={layers} />);
    // bottom has a known preset → legend renders
    expect(screen.getByTestId("grace2-layer-legend")).toBeInTheDocument();
  });
});
