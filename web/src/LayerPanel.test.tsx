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

// --- No-nudge-buttons regression (job-0173 Part 4) ---------------------- //
//
// The ▲/▼ z-order nudge buttons were dropped — drag-and-drop reorder is the
// sole reorder affordance now. The drag handle (data-testid layer-drag-handle)
// must remain so the reorder path is still available.

describe("LayerPanel — no nudge buttons (job-0173 Part 4)", () => {
  it("renders rows without layer-nudge-up / layer-nudge-down buttons", () => {
    render(
      <LayerPanel
        initialLayers={[makeLayer("a", 2), makeLayer("b", 1)]}
      />,
    );
    expect(screen.queryAllByTestId("layer-nudge-up")).toHaveLength(0);
    expect(screen.queryAllByTestId("layer-nudge-down")).toHaveLength(0);
  });

  it("rows do NOT contain ▲ or ▼ glyph characters", () => {
    const { container } = render(
      <LayerPanel initialLayers={[makeLayer("a"), makeLayer("b", 2)]} />,
    );
    const text = container.textContent ?? "";
    expect(text).not.toContain("▲");
    expect(text).not.toContain("▼");
  });

  it("preserves the drag handle on every layer row (reorder still possible)", () => {
    render(<LayerPanel initialLayers={[makeLayer("a"), makeLayer("b", 2)]} />);
    expect(screen.getAllByTestId("layer-drag-handle")).toHaveLength(2);
  });

  it("preserves visibility checkbox + opacity slider (controls unaffected)", () => {
    render(<LayerPanel initialLayers={[makeLayer("a")]} />);
    expect(screen.getByTestId("layer-visibility")).toBeInTheDocument();
    expect(screen.getByTestId("layer-opacity")).toBeInTheDocument();
  });
});

// --- job-0258: user controls emit map-commands (LAYER CONTROLS DEAD fix) --- //
//
// Root cause being pinned: before job-0258 the slider/checkbox handlers only
// dispatched to the panel's local reducer (M3 "intent" stubs) — nothing left
// the component, so the MapLibre instance never changed. These tests assert
// the new outbound `onMapCommand` emission contract that App.tsx wires to
// the shared bus (MapView consumes it; see Map.test.tsx for that half).

import { fireEvent } from "@testing-library/react";
import type { MapCommandPayload } from "./contracts";

describe("LayerPanel — user controls emit map-commands (job-0258)", () => {
  it("opacity slider change emits set-layer-opacity with the new value", () => {
    const onMapCommand = vi.fn<(cmd: MapCommandPayload) => void>();
    render(
      <LayerPanel
        initialLayers={[makeLayer("flood-demo")]}
        onMapCommand={onMapCommand}
      />,
    );

    const slider = screen.getByTestId("layer-opacity");
    fireEvent.change(slider, { target: { value: "0.35" } });

    expect(onMapCommand).toHaveBeenCalledWith({
      command: "set-layer-opacity",
      layer_id: "flood-demo",
      opacity: 0.35,
    });
  });

  it("visibility checkbox toggle emits set-layer-visibility", () => {
    const onMapCommand = vi.fn<(cmd: MapCommandPayload) => void>();
    render(
      <LayerPanel
        initialLayers={[makeLayer("flood-demo")]}
        onMapCommand={onMapCommand}
      />,
    );

    const checkbox = screen.getByTestId("layer-visibility");
    fireEvent.click(checkbox); // visible:true → false

    expect(onMapCommand).toHaveBeenCalledWith({
      command: "set-layer-visibility",
      layer_id: "flood-demo",
      visible: false,
    });
  });

  it("panel state still updates locally alongside the emission (slider %)", () => {
    const onMapCommand = vi.fn<(cmd: MapCommandPayload) => void>();
    render(
      <LayerPanel
        initialLayers={[makeLayer("flood-demo")]}
        onMapCommand={onMapCommand}
      />,
    );

    fireEvent.change(screen.getByTestId("layer-opacity"), {
      target: { value: "0.35" },
    });
    // The % readout reflects the reducer state (35%), proving the local
    // dispatch and the emission both happened from one handler.
    expect(screen.getByText("35%")).toBeTruthy();
  });

  it("emission is optional — controls do not throw without onMapCommand", () => {
    render(<LayerPanel initialLayers={[makeLayer("flood-demo")]} />);
    expect(() => {
      fireEvent.change(screen.getByTestId("layer-opacity"), {
        target: { value: "0.5" },
      });
      fireEvent.click(screen.getByTestId("layer-visibility"));
    }).not.toThrow();
  });
});
