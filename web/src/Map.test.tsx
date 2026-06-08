// GRACE-2 web — Map.tsx subscription + WMS source wiring tests (job-0068).
//
// Verifies:
//   1. subscribeSessionState populates raster sources from loaded_layers.
//   2. A.7 replace-not-reconcile: sources removed when layer drops from list.
//   3. subscribeMapCommand zoom-to calls fitBounds on the MapLibre instance.
//
// maplibre-gl is mocked because happy-dom / jsdom cannot run WebGL. The
// mock captures addSource / addLayer / removeLayer / removeSource /
// setPaintProperty / setLayoutProperty / fitBounds calls so we can assert
// the correct MapLibre API calls without a real canvas.
//
// The subscribeSessionState / subscribeMapCommand are passed as stub bus
// functions (the same pattern used in LayerPanel.test.tsx).
//
// Type note: The agent wire format uses `uri` (not `source_url`), and the
// map-command bus carries zoom-to which is not in the frozen contracts.ts
// union. Tests use `as unknown as <T>` where needed — this is correct since
// we are testing the wire path, not the TS type system.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, act } from "@testing-library/react";
import { MapView, type MapCommandSubscribeFunc, type SessionStateSubscriber } from "./Map";

// --- MapLibre mock -------------------------------------------------------- //
// Capture all relevant method calls for assertion.

type MockCallArgs = unknown[];
interface MapMock {
  addSource: ReturnType<typeof vi.fn>;
  addLayer: ReturnType<typeof vi.fn>;
  removeLayer: ReturnType<typeof vi.fn>;
  removeSource: ReturnType<typeof vi.fn>;
  setPaintProperty: ReturnType<typeof vi.fn>;
  setLayoutProperty: ReturnType<typeof vi.fn>;
  fitBounds: ReturnType<typeof vi.fn>;
  addControl: ReturnType<typeof vi.fn>;
  touchZoomRotate: { disableRotation: ReturnType<typeof vi.fn> };
  keyboard: { disableRotation: ReturnType<typeof vi.fn> };
  getLayer: ReturnType<typeof vi.fn>;
  getSource: ReturnType<typeof vi.fn>;
  isStyleLoaded: ReturnType<typeof vi.fn>;
  remove: ReturnType<typeof vi.fn>;
}

// Track the most-recently created mock map instance.
let lastMapMock: MapMock | null = null;

vi.mock("maplibre-gl", () => {
  class MockNavigationControl {}

  class MockMap {
    addSource = vi.fn();
    addLayer = vi.fn();
    removeLayer = vi.fn();
    removeSource = vi.fn();
    setPaintProperty = vi.fn();
    setLayoutProperty = vi.fn();
    fitBounds = vi.fn();
    addControl = vi.fn();
    touchZoomRotate = { disableRotation: vi.fn() };
    keyboard = { disableRotation: vi.fn() };
    // isStyleLoaded must return true for the session-state handler to apply.
    isStyleLoaded = vi.fn().mockReturnValue(true);
    // getLayer / getSource return null unless we've "added" them via addSource.
    getLayer = vi.fn().mockReturnValue(null);
    getSource = vi.fn().mockReturnValue(null);
    remove = vi.fn();

    constructor() {
      lastMapMock = this as unknown as MapMock;
    }
  }

  return {
    default: {
      Map: MockMap,
      NavigationControl: MockNavigationControl,
    },
    Map: MockMap,
    NavigationControl: MockNavigationControl,
  };
});

vi.mock("maplibre-gl/dist/maplibre-gl.css", () => ({}));

// --- Wire types for test injection --------------------------------------- //
// The agent sends `uri` (not `source_url`) on the wire (Python model field).
// Tests inject the actual wire format; Map.tsx reads `uri` via WireLayerSummary.
interface WireSessionState {
  loaded_layers?: Array<{
    layer_id: string;
    name: string;
    layer_type: string;
    uri: string;
    visible?: boolean;
    opacity?: number;
  }>;
}

interface ZoomToCommand {
  command: "zoom-to";
  args: { bbox: number[] };
}

// --- Test bus factory ----------------------------------------------------- //

type SessionSubscriber = (p: WireSessionState) => void;
type MapCmdSubscriber = (p: ZoomToCommand | { command: string; args?: unknown }) => void;

function makeSessionBus() {
  const subs: SessionSubscriber[] = [];
  const push = (p: WireSessionState) => subs.forEach((s) => s(p));
  const subscribe = (cb: SessionSubscriber) => {
    subs.push(cb);
    return () => { subs.splice(subs.indexOf(cb), 1); };
  };
  return { push, subscribe };
}

function makeMapCmdBus() {
  const subs: MapCmdSubscriber[] = [];
  const push = (p: ZoomToCommand | { command: string; args?: unknown }) => subs.forEach((s) => s(p));
  const subscribe = (cb: MapCmdSubscriber) => {
    subs.push(cb);
    return () => { subs.splice(subs.indexOf(cb), 1); };
  };
  return { push, subscribe };
}

function makeWireLayer(id: string, uri = `https://qgis.example.com/wms?LAYERS=${id}`) {
  return { layer_id: id, name: id, layer_type: "raster", uri, visible: true };
}

// --- Tests ---------------------------------------------------------------- //

describe("MapView — session-state WMS source wiring (job-0068 change 4)", () => {
  beforeEach(() => {
    lastMapMock = null;
  });

  it("adds a raster source+layer when session-state arrives with a loaded layer", () => {
    const sessionBus = makeSessionBus();

    render(
      <MapView
        subscribeSessionState={sessionBus.subscribe as (cb: SessionStateSubscriber) => () => void}
      />,
    );

    act(() => {
      sessionBus.push({
        loaded_layers: [makeWireLayer("flood-demo")],
      });
    });

    const m = lastMapMock!;
    expect(m.addSource).toHaveBeenCalledOnce();
    const [sourceId, sourceDef] = m.addSource.mock.calls[0] as MockCallArgs;
    expect(sourceId).toBe("flood-demo");
    expect((sourceDef as { type: string }).type).toBe("raster");
    // Tile URL must contain the WMS placeholder that MapLibre substitutes.
    const tiles = (sourceDef as { tiles: string[] }).tiles;
    expect(tiles[0]).toContain("{bbox-epsg-3857}");

    expect(m.addLayer).toHaveBeenCalledOnce();
    const [layerDef] = m.addLayer.mock.calls[0] as MockCallArgs;
    expect((layerDef as { id: string }).id).toBe("flood-demo");
    expect((layerDef as { type: string }).type).toBe("raster");
  });

  it("removes source+layer when layer disappears from session-state (A.7 reconcile)", () => {
    const sessionBus = makeSessionBus();

    render(
      <MapView
        subscribeSessionState={sessionBus.subscribe as (cb: SessionStateSubscriber) => () => void}
      />,
    );

    // Add the layer first.
    act(() => {
      sessionBus.push({ loaded_layers: [makeWireLayer("flood-demo")] });
    });

    const m = lastMapMock!;
    // Simulate that the layer is now "known" to MapLibre.
    m.getLayer.mockReturnValue({ id: "flood-demo" });
    m.getSource.mockReturnValue({ type: "raster" });

    // Now push session-state with the layer removed.
    act(() => {
      sessionBus.push({ loaded_layers: [] });
    });

    expect(m.removeLayer).toHaveBeenCalledWith("flood-demo");
    expect(m.removeSource).toHaveBeenCalledWith("flood-demo");
  });

  it("updates opacity via setPaintProperty when layer already added", () => {
    const sessionBus = makeSessionBus();

    render(
      <MapView
        subscribeSessionState={sessionBus.subscribe as (cb: SessionStateSubscriber) => () => void}
      />,
    );

    // First push — layer added.
    act(() => {
      sessionBus.push({
        loaded_layers: [{ ...makeWireLayer("flood-demo"), opacity: 1 }],
      });
    });

    const m = lastMapMock!;
    // Layer is now "known" to MapLibre.
    m.getLayer.mockReturnValue({ id: "flood-demo" });

    // Second push — opacity changed.
    act(() => {
      sessionBus.push({
        loaded_layers: [{ ...makeWireLayer("flood-demo"), opacity: 0.5 }],
      });
    });

    expect(m.setPaintProperty).toHaveBeenCalledWith("flood-demo", "raster-opacity", 0.5);
  });
});

describe("MapView — map-command zoom-to handler (job-0068 change 5 client side)", () => {
  beforeEach(() => {
    lastMapMock = null;
  });

  it("calls fitBounds with the bbox from zoom-to map-command", () => {
    const mapCmdBus = makeMapCmdBus();

    render(
      <MapView
        subscribeMapCommand={mapCmdBus.subscribe as MapCommandSubscribeFunc}
      />,
    );

    act(() => {
      mapCmdBus.push({
        command: "zoom-to",
        args: { bbox: [-81.91, 26.55, -81.75, 26.69] },
      });
    });

    const m = lastMapMock!;
    expect(m.fitBounds).toHaveBeenCalledOnce();
    const [bounds, opts] = m.fitBounds.mock.calls[0] as MockCallArgs;
    expect(bounds).toEqual([[-81.91, 26.55], [-81.75, 26.69]]);
    expect((opts as { padding: number }).padding).toBe(40);
    expect((opts as { duration: number }).duration).toBe(1200);
  });

  it("warns (not throws) for unrecognised map-commands", () => {
    const mapCmdBus = makeMapCmdBus();
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});

    render(
      <MapView
        subscribeMapCommand={mapCmdBus.subscribe as MapCommandSubscribeFunc}
      />,
    );

    act(() => {
      mapCmdBus.push({ command: "invalidate-tiles", args: {} });
    });

    expect(warnSpy).toHaveBeenCalledWith(
      expect.stringContaining("MapCommand not yet implemented"),
      "invalidate-tiles",
    );
    warnSpy.mockRestore();
  });
});
