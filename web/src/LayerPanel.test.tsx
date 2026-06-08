// GRACE-2 web — LayerPanel unit tests (job-0065, tweak 2).
//
// Verifies:
//   1. LayerPanel returns null (renders nothing) when loaded_layers is empty.
//   2. LayerPanel renders when at least one layer is loaded.
//   3. LayerPanel shows/hides dynamically as layers go 0 → 1 → 0.
//   4. onLayersChange callback fires with the correct layer list.

import { describe, it, expect, vi } from "vitest";
import { render, screen, act } from "@testing-library/react";
import { LayerPanel, createLayerPanelBus } from "./LayerPanel";
import { ProjectLayerSummary, SessionStatePayload } from "./contracts";

// dnd-kit requires pointer events which happy-dom supports but the PointerSensor
// needs a minimum drag distance. Our tests don't exercise drag — just layer
// list rendering — so no special mocking is required here.

function makeLayer(id: string, z_index = 1): ProjectLayerSummary {
  return {
    layer_id: id,
    name: `Layer ${id}`,
    layer_type: "raster",
    uri: `gs://grace-2/runs/${id}/depth.cog.tif`,
    visible: true,
    opacity: 1,
    z_index,
  };
}

function sessionStateWith(layers: ProjectLayerSummary[]): SessionStatePayload {
  return { loaded_layers: layers };
}

describe("LayerPanel — hide-when-empty (tweak 2)", () => {
  it("renders null (panel hidden) when loaded_layers is empty", () => {
    const { container } = render(
      <LayerPanel initialLayers={[]} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders the panel when initialLayers has at least one layer", () => {
    render(<LayerPanel initialLayers={[makeLayer("a")]} />);
    expect(screen.getByTestId("grace2-layer-panel")).toBeInTheDocument();
  });

  it("hides panel after session-state with empty layers pushes through bus", () => {
    const bus: ReturnType<typeof createLayerPanelBus> = createLayerPanelBus();
    // Start with one layer so the panel is visible.
    render(
      <LayerPanel
        initialLayers={[makeLayer("a")]}
        subscribeSessionState={bus.subscribeSessionState}
        subscribeMapCommand={bus.subscribeMapCommand}
      />,
    );
    expect(screen.getByTestId("grace2-layer-panel")).toBeInTheDocument();

    // Push an empty session-state.
    act(() => {
      bus.pushSessionState(sessionStateWith([]));
    });

    expect(screen.queryByTestId("grace2-layer-panel")).toBeNull();
  });

  it("shows panel after session-state with layers pushes through bus from empty", () => {
    const bus: ReturnType<typeof createLayerPanelBus> = createLayerPanelBus();
    // Start empty — panel hidden.
    render(
      <LayerPanel
        initialLayers={[]}
        subscribeSessionState={bus.subscribeSessionState}
        subscribeMapCommand={bus.subscribeMapCommand}
      />,
    );
    expect(screen.queryByTestId("grace2-layer-panel")).toBeNull();

    // Push session-state with a layer.
    act(() => {
      bus.pushSessionState(sessionStateWith([makeLayer("b")]));
    });

    expect(screen.getByTestId("grace2-layer-panel")).toBeInTheDocument();
  });

  it("calls onLayersChange with the current layer list when layers update", () => {
    const bus = createLayerPanelBus();
    const onChange = vi.fn();

    render(
      <LayerPanel
        initialLayers={[makeLayer("x")]}
        subscribeSessionState={bus.subscribeSessionState}
        subscribeMapCommand={bus.subscribeMapCommand}
        onLayersChange={onChange}
      />,
    );

    // onLayersChange called on mount with initial layers.
    expect(onChange).toHaveBeenCalledWith(
      expect.arrayContaining([expect.objectContaining({ layer_id: "x" })]),
    );

    // Push a new session-state.
    act(() => {
      bus.pushSessionState(sessionStateWith([makeLayer("y"), makeLayer("z", 2)]));
    });

    const lastCall = (onChange.mock.calls[onChange.mock.calls.length - 1] as [ProjectLayerSummary[]])[0];
    expect(lastCall.map((l) => l.layer_id).sort()).toEqual(["y", "z"]);
  });
});
