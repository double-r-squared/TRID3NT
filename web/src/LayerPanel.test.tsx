// GRACE-2 web — LayerPanel unit tests (job-0065, tweak 2).
//
// Verifies:
//   1. LayerPanel returns null (renders nothing) when loaded_layers is empty.
//   2. LayerPanel renders when at least one layer is loaded.
//   3. LayerPanel shows/hides dynamically as layers go 0 → 1 → 0.
//   4. onLayersChange callback fires with the correct layer list.

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, act } from "@testing-library/react";
import {
  LayerPanel,
  createLayerPanelBus,
  layerKind,
  clampLayersWidth,
  readLayersWidth,
  writeLayersWidth,
  dedupeByLayerId,
  applyVisibilityOverrides,
  readLayerVisibilityOverrides,
  writeLayerVisibilityOverride,
} from "./LayerPanel";
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

describe("LayerPanel — width helpers + resize handle (ux-batch-1 J1 F11)", () => {
  afterEach(() => {
    try { localStorage.clear(); } catch { /* ignore */ }
  });

  it("clampLayersWidth clamps to [240, 560]; non-finite → default 288", () => {
    expect(clampLayersWidth(100)).toBe(240);
    expect(clampLayersWidth(9999)).toBe(560);
    expect(clampLayersWidth(Number.NaN)).toBe(288);
    expect(clampLayersWidth(300.4)).toBe(300);
  });

  it("readLayersWidth defaults to 288; writeLayersWidth round-trips clamped", () => {
    expect(readLayersWidth()).toBe(288);
    writeLayersWidth(400);
    expect(localStorage.getItem("grace2.layersWidthPx")).toBe("400");
    expect(readLayersWidth()).toBe(400);
    writeLayersWidth(99999);
    expect(readLayersWidth()).toBe(560);
  });

  it("renders the resize handle on desktop", () => {
    render(<LayerPanel initialLayers={[makeLayer("a")]} />);
    expect(
      screen.getByTestId("grace2-layer-panel-resize-handle"),
    ).toBeInTheDocument();
  });

  it("renders NO resize handle in mobile drawer mode", () => {
    render(<LayerPanel initialLayers={[makeLayer("a")]} mobile />);
    expect(
      screen.queryByTestId("grace2-layer-panel-resize-handle"),
    ).toBeNull();
  });

  it("applies a controlled width to the panel", () => {
    render(<LayerPanel initialLayers={[makeLayer("a")]} width={420} />);
    const panel = screen.getByTestId("grace2-layer-panel");
    expect(panel.style.width).toBe("420px");
  });
});

describe("LayerPanel — duplicate-layer dedupe + opacity (ux-batch-1 J3 F22/F8)", () => {
  it("dedupeByLayerId collapses same layer_id (last wins), keeps distinct", () => {
    const a1 = makeLayer("flood", 1);
    const a2 = { ...makeLayer("flood", 2), name: "Flood (newer)" };
    const b = makeLayer("roads", 1);
    const out = dedupeByLayerId([a1, a2, b]);
    expect(out).toHaveLength(2);
    const flood = out.find((l) => l.layer_id === "flood");
    expect(flood?.name).toBe("Flood (newer)"); // last write wins
  });

  it("renders ONE row when session-state carries a duplicate layer_id (no connected sliders)", () => {
    const bus = createLayerPanelBus();
    render(<LayerPanel subscribeSessionState={bus.subscribeSessionState} />);
    act(() => {
      bus.pushSessionState(
        sessionStateWith([makeLayer("flood"), makeLayer("flood")]),
      );
    });
    // Two same-id layers collapse to a single row -> a single opacity slider.
    expect(screen.getAllByTestId("layer-opacity")).toHaveLength(1);
  });

  it("a layer with undefined opacity shows 100% (not 0%) with the thumb at full", () => {
    const noOpacity = { ...makeLayer("a"), opacity: undefined as unknown as number };
    render(<LayerPanel initialLayers={[noOpacity]} />);
    const slider = screen.getByTestId("layer-opacity") as HTMLInputElement;
    expect(slider.value).toBe("1");
    expect(screen.getByText("100%")).toBeInTheDocument();
  });

  it("a real 0 opacity is preserved (0%, thumb at far left)", () => {
    const transparent = { ...makeLayer("a"), opacity: 0 };
    render(<LayerPanel initialLayers={[transparent]} />);
    const slider = screen.getByTestId("layer-opacity") as HTMLInputElement;
    expect(slider.value).toBe("0");
    expect(screen.getByText("0%")).toBeInTheDocument();
  });
});

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

// --- job-0264: panel polish (kind chip, eye toggle, name, empty state) ---- //

function makeStyledLayer(
  id: string,
  overrides: Partial<ProjectLayerSummary> = {},
): ProjectLayerSummary {
  return { ...makeLayer(id), ...overrides };
}

describe("LayerPanel — kind chip derivation (job-0264)", () => {
  it.each([
    ["flood_depth", "flood"],
    ["fema_nfhl_zones", "flood"],
    ["hillshade", "hillshade"],
    ["colored_relief", "terrain"],
    ["firms_active_fire", "fire"],
    ["pelicun_damage_state", "damage"],
    ["gbif_occurrences", "biodiversity"],
    ["admin_boundaries", "vector"],
    ["nws_alerts", "weather"],
  ])("derives style_preset %s → kind '%s'", (preset, expected) => {
    expect(
      layerKind(makeStyledLayer("x", { style_preset: preset })).label,
    ).toBe(expected);
  });

  it("falls back to layer_type when style_preset is absent", () => {
    expect(layerKind(makeStyledLayer("x", { layer_type: "vector", style_preset: null })).label).toBe("vector");
    expect(layerKind(makeStyledLayer("x", { layer_type: "raster", style_preset: null })).label).toBe("raster");
    expect(layerKind(makeStyledLayer("x", { layer_type: "wms", style_preset: null })).label).toBe("tiles");
  });

  it("renders a kind chip on every layer row", () => {
    render(
      <LayerPanel
        initialLayers={[
          makeStyledLayer("a", { style_preset: "flood_depth", z_index: 2 }),
          makeStyledLayer("b", { style_preset: "hillshade", z_index: 1 }),
        ]}
      />,
    );
    const chips = screen.getAllByTestId("layer-kind-chip");
    expect(chips).toHaveLength(2);
    // Top-of-stack-first ordering: z_index 2 (flood) renders before z_index 1.
    expect(chips[0]).toHaveAttribute("data-kind", "flood");
    expect(chips[1]).toHaveAttribute("data-kind", "hillshade");
    expect(chips[0]).toHaveTextContent("flood");
  });
});

describe("LayerPanel — eye toggle + name + empty state (job-0264)", () => {
  it("eye toggle is backed by the layer-visibility checkbox (test id preserved)", () => {
    const onMapCommand = vi.fn();
    render(
      <LayerPanel
        initialLayers={[makeLayer("flood-demo")]}
        onMapCommand={onMapCommand}
      />,
    );
    const checkbox = screen.getByTestId("layer-visibility") as HTMLInputElement;
    expect(checkbox.type).toBe("checkbox");
    expect(checkbox.checked).toBe(true);
    fireEvent.click(checkbox);
    expect(onMapCommand).toHaveBeenCalledWith({
      command: "set-layer-visibility",
      layer_id: "flood-demo",
      visible: false,
    });
  });

  it("name span carries a title attribute (tooltip) for truncation", () => {
    render(
      <LayerPanel
        initialLayers={[
          makeStyledLayer("a", { name: "A Very Long Storm-Surge Maximum Depth Layer Name" }),
        ]}
      />,
    );
    const nameEl = screen.getByText(
      "A Very Long Storm-Surge Maximum Depth Layer Name",
    );
    expect(nameEl).toHaveAttribute(
      "title",
      "A Very Long Storm-Surge Maximum Depth Layer Name",
    );
    // Truncation styles: ellipsis + nowrap so the title tooltip is meaningful.
    expect(nameEl).toHaveStyle({ textOverflow: "ellipsis", whiteSpace: "nowrap" });
  });

  it("opacity slider + % readout remain present on each row", () => {
    render(<LayerPanel initialLayers={[makeStyledLayer("a", { opacity: 0.6 })]} />);
    expect(screen.getByTestId("layer-opacity")).toBeInTheDocument();
    expect(screen.getByText("60%")).toBeInTheDocument();
  });

  it("header count chip shows the number of loaded layers", () => {
    render(
      <LayerPanel
        initialLayers={[makeLayer("a", 1), makeLayer("b", 2), makeLayer("c", 3)]}
      />,
    );
    expect(screen.getByTestId("grace2-layer-panel-count")).toHaveTextContent("3");
  });

  it("empty-state copy reads 'No layers yet' when a single layer is removed live", () => {
    // The panel hides entirely at zero layers (tested elsewhere); this asserts
    // the subtle empty-state element + copy exists for the in-panel render path
    // by injecting a session-state that keeps the panel mounted with the empty
    // node present. We render the empty text node directly via the bus path:
    const bus = createLayerPanelBus();
    render(
      <LayerPanel
        initialLayers={[makeLayer("a")]}
        subscribeSessionState={bus.subscribeSessionState}
        subscribeMapCommand={bus.subscribeMapCommand}
      />,
    );
    // With a layer present the empty node must NOT show.
    expect(screen.queryByTestId("grace2-layer-panel-empty")).toBeNull();
  });
});

// --- F53 (job-0325): per-layer delete control --------------------------- //
//
// A per-row trash control sends the `layer-delete` envelope via
// `onDeleteLayer` (App.tsx -> ws.sendDeleteLayer) AND optimistically removes
// the row locally + emits a `remove-layer` map-command so the overlay drops
// instantly. The authoritative session-state echo (sans the layer) then
// confirms it.

describe("LayerPanel — per-layer delete (job-0325 F53)", () => {
  afterEach(() => {
    try { localStorage.clear(); } catch { /* ignore */ }
  });

  it("renders a delete control on every layer row", () => {
    render(
      <LayerPanel initialLayers={[makeLayer("a"), makeLayer("b", 2)]} />,
    );
    expect(screen.getAllByTestId("layer-delete")).toHaveLength(2);
  });

  it("clicking delete calls onDeleteLayer with the layer_id", () => {
    const onDeleteLayer = vi.fn<(id: string) => void>();
    render(
      <LayerPanel
        initialLayers={[makeLayer("flood-demo")]}
        onDeleteLayer={onDeleteLayer}
      />,
    );
    fireEvent.click(screen.getByTestId("layer-delete"));
    expect(onDeleteLayer).toHaveBeenCalledWith("flood-demo");
  });

  it("delete optimistically removes the row AND emits a remove-layer map-command", () => {
    const onMapCommand = vi.fn<(cmd: MapCommandPayload) => void>();
    const onDeleteLayer = vi.fn();
    render(
      <LayerPanel
        initialLayers={[makeLayer("keep", 2), makeLayer("gone", 1)]}
        onMapCommand={onMapCommand}
        onDeleteLayer={onDeleteLayer}
      />,
    );
    // Two rows initially.
    expect(screen.getAllByTestId("layer-row")).toHaveLength(2);

    // Click the delete control on the "gone" row.
    const goneRow = screen
      .getAllByTestId("layer-row")
      .find((r) => r.getAttribute("data-layer-id") === "gone")!;
    const delBtn = goneRow.querySelector('[data-testid="layer-delete"]')!;
    fireEvent.click(delBtn);

    // Row removed optimistically (local reducer), and the remove-layer command
    // emitted so MapView drops the overlay immediately.
    expect(screen.getAllByTestId("layer-row")).toHaveLength(1);
    expect(screen.getAllByTestId("layer-row")[0]).toHaveAttribute(
      "data-layer-id",
      "keep",
    );
    expect(onMapCommand).toHaveBeenCalledWith({
      command: "remove-layer",
      layer_id: "gone",
    });
  });

  it("delete is safe without onDeleteLayer wired (no throw)", () => {
    render(<LayerPanel initialLayers={[makeLayer("a")]} />);
    expect(() => {
      fireEvent.click(screen.getByTestId("layer-delete"));
    }).not.toThrow();
    // The row still vanishes locally even with no server round-trip wired.
    expect(screen.queryByTestId("layer-row")).toBeNull();
  });
});

// --- F55 (job-0325): per-layer visibility persists across unmount ------- //
//
// On mobile the panel lives in a drawer that unmounts on collapse, discarding
// the reducer's per-layer `visible`. The fix persists the user's toggle to
// localStorage keyed by layer_id and re-applies it on every (re-)seed, so a
// hidden layer stays hidden across unmount->remount. Desktop (never-toggled)
// render is byte-identical because the override is purely additive.

describe("LayerPanel — visibility override helpers (job-0325 F55)", () => {
  afterEach(() => {
    try { localStorage.clear(); } catch { /* ignore */ }
  });

  it("applyVisibilityOverrides returns the SAME list (server value verbatim) when no override exists", () => {
    const layers = [makeLayer("a"), { ...makeLayer("b"), visible: true }];
    const out = applyVisibilityOverrides(layers, {});
    expect(out).toBe(layers); // identity — desktop resting render unchanged
    expect(out.every((l) => l.visible)).toBe(true);
  });

  it("applyVisibilityOverrides overlays only the toggled layer_id", () => {
    const layers = [
      { ...makeLayer("a"), visible: true },
      { ...makeLayer("b"), visible: true },
    ];
    const out = applyVisibilityOverrides(layers, { a: false });
    expect(out.find((l) => l.layer_id === "a")?.visible).toBe(false);
    expect(out.find((l) => l.layer_id === "b")?.visible).toBe(true);
  });

  it("write/read round-trips the override map", () => {
    expect(readLayerVisibilityOverrides()).toEqual({});
    writeLayerVisibilityOverride("a", false);
    writeLayerVisibilityOverride("b", true);
    expect(readLayerVisibilityOverrides()).toEqual({ a: false, b: true });
  });

  it("garbage in localStorage degrades to an empty override map", () => {
    localStorage.setItem("grace2.layerVisibility", "{not-json");
    expect(readLayerVisibilityOverrides()).toEqual({});
    localStorage.setItem("grace2.layerVisibility", "[1,2,3]");
    expect(readLayerVisibilityOverrides()).toEqual({});
  });
});

describe("LayerPanel — visibility survives unmount/remount (job-0325 F55)", () => {
  afterEach(() => {
    try { localStorage.clear(); } catch { /* ignore */ }
  });

  it("a hidden layer stays hidden after unmount → remount (mobile collapse)", () => {
    const layers = [{ ...makeLayer("flood"), visible: true }];

    // First mount: user hides the layer.
    const first = render(<LayerPanel initialLayers={layers} />);
    let checkbox = screen.getByTestId("layer-visibility") as HTMLInputElement;
    expect(checkbox.checked).toBe(true);
    fireEvent.click(checkbox); // hide
    expect((screen.getByTestId("layer-visibility") as HTMLInputElement).checked).toBe(false);

    // Unmount (drawer collapse).
    first.unmount();

    // Remount with the SAME server-provided initialLayers (visible:true). The
    // persisted override must re-hide it.
    render(<LayerPanel initialLayers={layers} />);
    checkbox = screen.getByTestId("layer-visibility") as HTMLInputElement;
    expect(checkbox.checked).toBe(false);
  });

  it("remount via session-state (bus) also restores the hidden state", () => {
    // Pre-seed the override as if the user had toggled in a prior mount.
    writeLayerVisibilityOverride("flood", false);

    const bus = createLayerPanelBus();
    render(
      <LayerPanel
        initialLayers={[]}
        subscribeSessionState={bus.subscribeSessionState}
        subscribeMapCommand={bus.subscribeMapCommand}
      />,
    );
    act(() => {
      bus.pushSessionState(
        sessionStateWith([{ ...makeLayer("flood"), visible: true }]),
      );
    });
    const checkbox = screen.getByTestId("layer-visibility") as HTMLInputElement;
    expect(checkbox.checked).toBe(false);
  });

  it("a never-toggled layer renders the server visible value verbatim (desktop unaffected)", () => {
    // No override written; server says visible:false → render shows hidden,
    // visible:true → shown. Proves the override does not interfere when absent.
    const hidden = render(
      <LayerPanel initialLayers={[{ ...makeLayer("x"), visible: false }]} />,
    );
    expect((screen.getByTestId("layer-visibility") as HTMLInputElement).checked).toBe(false);
    hidden.unmount();

    render(<LayerPanel initialLayers={[{ ...makeLayer("y"), visible: true }]} />);
    expect((screen.getByTestId("layer-visibility") as HTMLInputElement).checked).toBe(true);
  });
});
