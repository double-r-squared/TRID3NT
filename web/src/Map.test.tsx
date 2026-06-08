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
  on: ReturnType<typeof vi.fn>;
  once: ReturnType<typeof vi.fn>;
  getStyle: ReturnType<typeof vi.fn>;
  _addedLayers: Set<string>;
  _addedSources: Set<string>;
}

// Track the most-recently created mock map instance.
let lastMapMock: MapMock | null = null;

vi.mock("maplibre-gl", () => {
  class MockNavigationControl {}

  class MockMap {
    // The mock tracks added source/layer IDs internally so getLayer/getSource
    // can return realistic answers. Tests can also override these mocks per
    // case if they need different behavior.
    _addedLayers = new Set<string>(["qgis-basemap", "osm-fallback-basemap"]);
    _addedSources = new Set<string>(["qgis-wms", "osm-fallback"]);

    addSource = vi.fn((id: string, _def: unknown) => {
      this._addedSources.add(id);
    });
    addLayer = vi.fn((def: { id: string }, _beforeId?: string) => {
      this._addedLayers.add(def.id);
    });
    removeLayer = vi.fn((id: string) => {
      this._addedLayers.delete(id);
    });
    removeSource = vi.fn((id: string) => {
      this._addedSources.delete(id);
    });
    setPaintProperty = vi.fn();
    setLayoutProperty = vi.fn();
    fitBounds = vi.fn();
    addControl = vi.fn();
    touchZoomRotate = { disableRotation: vi.fn() };
    keyboard = { disableRotation: vi.fn() };
    // isStyleLoaded must return true for the session-state handler to apply.
    isStyleLoaded = vi.fn().mockReturnValue(true);
    // getLayer / getSource consult the internal tracking sets.
    getLayer = vi.fn((id: string) => (this._addedLayers.has(id) ? { id } : null));
    getSource = vi.fn((id: string) => (this._addedSources.has(id) ? { type: "raster" } : null));
    remove = vi.fn();
    // Event handlers — applyLatest attaches `once("idle", ...)` after every
    // session-state push; the mock just no-ops (synchronous apply path is
    // what tests verify).
    on = vi.fn();
    once = vi.fn();
    // getStyle is used by the theme-swap effect to find existing layers.
    getStyle = vi.fn(() => ({
      layers: Array.from(this._addedLayers).map((id) => ({ id })),
      sources: Object.fromEntries(
        Array.from(this._addedSources).map((id) => [id, { type: "raster" }]),
      ),
    }));

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

  it("sets raster-resampling: nearest so per-cell alignment is visually verifiable (job-0078)", () => {
    // job-0078 diagnosis: the default `linear` bilinear resampling smeared
    // flood-depth COG cells across screen pixels at z=13, hiding the per-cell
    // alignment between the flood overlay and the basemap street grid. The
    // user read this as misalignment. The fix is `raster-resampling: nearest`
    // so each source cell shows as a discrete block — making it visually
    // obvious that each flood cell is positioned over the correct street/lot.
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
    expect(m.addLayer).toHaveBeenCalledOnce();
    const [layerDef] = m.addLayer.mock.calls[0] as MockCallArgs;
    const paint = (layerDef as { paint: Record<string, unknown> }).paint;
    expect(paint["raster-resampling"]).toBe("nearest");
    // raster-opacity must still be set (from layer.opacity wire field).
    expect(paint).toHaveProperty("raster-opacity");
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

describe("MapView — buildWmsTileUrl (job-0076 diagnosis)", () => {
  it("produces a tile URL with all WMS GetMap params MapLibre needs", async () => {
    // Reimport to grab the exported helper for direct assertion.
    const { buildWmsTileUrl } = await import("./Map");
    const url = buildWmsTileUrl(
      "https://qgis.example.com/ogc/wms?MAP=/mnt/qgs/x.qgs&LAYERS=flood-demo",
    );
    // The {bbox-epsg-3857} placeholder must be there for MapLibre's raster
    // source to substitute per-tile.
    expect(url).toContain("{bbox-epsg-3857}");
    // All required WMS GetMap params must be present (else QGIS Server 400s
    // and MapLibre paints nothing — the job-0076 hypothesis #1 chain).
    expect(url).toContain("SERVICE=WMS");
    expect(url).toContain("VERSION=1.3.0");
    expect(url).toContain("REQUEST=GetMap");
    expect(url).toContain("CRS=EPSG:3857");
    expect(url).toMatch(/FORMAT=image[/%]2[Ff]png/);
    expect(url).toContain("TRANSPARENT=true");
    expect(url).toContain("WIDTH=256");
    expect(url).toContain("HEIGHT=256");
    // LAYERS= must come from the caller (we only append after the base URL).
    expect(url).toContain("LAYERS=flood-demo");
  });
});

describe("MapView — session-state idle-retry (job-0076 root-cause fix)", () => {
  beforeEach(() => {
    lastMapMock = null;
  });

  it("registers a once('idle', ...) handler when session-state arrives so a not-yet-loaded style re-applies", () => {
    const sessionBus = makeSessionBus();

    render(
      <MapView
        subscribeSessionState={sessionBus.subscribe as (cb: SessionStateSubscriber) => () => void}
      />,
    );

    const m = lastMapMock!;
    // Simulate style NOT loaded at the moment the bus event arrives — the
    // job-0066-through-0075 silent-drop race condition.
    m.isStyleLoaded.mockReturnValue(false);

    act(() => {
      sessionBus.push({
        loaded_layers: [makeWireLayer("flood-demo")],
      });
    });

    // The synchronous apply path is correctly skipped (no addSource yet)...
    expect(m.addSource).not.toHaveBeenCalled();
    // ...but an idle handler IS attached so a later style-load retries apply.
    expect(m.once).toHaveBeenCalled();
    const onceCalls = m.once.mock.calls;
    const idleHandler = onceCalls.find((c) => (c as MockCallArgs)[0] === "idle");
    expect(idleHandler).toBeDefined();

    // Now simulate the style finishing load + the idle handler firing.
    m.isStyleLoaded.mockReturnValue(true);
    (idleHandler as MockCallArgs)[1] && ((idleHandler as MockCallArgs)[1] as () => void)();

    // The apply function reads the ref and now wires the flood layer.
    expect(m.addSource).toHaveBeenCalledOnce();
    const [sourceId] = m.addSource.mock.calls[0] as MockCallArgs;
    expect(sourceId).toBe("flood-demo");
    expect(m.addLayer).toHaveBeenCalledOnce();
  });
});

describe("MapView — dark-theme swap (job-0076 bundled enhancement)", () => {
  beforeEach(() => {
    lastMapMock = null;
  });

  it("applies the light basemap on first mount when theme prop is light (default)", () => {
    render(<MapView />);
    const m = lastMapMock!;
    // The seed style added qgis-basemap; the theme effect saw light theme
    // and did not need to add anything new. Confirm dark basemap is NOT
    // present in the layer set.
    expect(m._addedLayers.has("qgis-basemap")).toBe(true);
    expect(m._addedLayers.has("carto-dark-basemap")).toBe(false);
  });

  it("swaps to CartoDB dark basemap when theme prop = 'dark'", () => {
    const { rerender } = render(<MapView theme="light" />);
    const m = lastMapMock!;
    expect(m._addedLayers.has("carto-dark-basemap")).toBe(false);

    rerender(<MapView theme="dark" />);

    // dark basemap source + layer must have been added; light basemap layer
    // removed (source kept, harmless).
    expect(m.addSource).toHaveBeenCalledWith(
      "carto-dark",
      expect.objectContaining({
        type: "raster",
        tiles: expect.arrayContaining([
          expect.stringContaining("basemaps.cartocdn.com/dark_all"),
        ]),
        attribution: expect.stringContaining("CARTO"),
      }),
    );
    expect(m.addLayer).toHaveBeenCalledWith(
      expect.objectContaining({ id: "carto-dark-basemap", type: "raster" }),
      undefined, // no flood overlays yet → beforeId is undefined (top of stack)
    );
    expect(m.removeLayer).toHaveBeenCalledWith("qgis-basemap");
  });

  it("re-adds the QGIS WMS basemap under a flood overlay when toggling back to light", () => {
    const sessionBus = makeSessionBus();
    const { rerender } = render(
      <MapView
        theme="light"
        subscribeSessionState={sessionBus.subscribe as (cb: SessionStateSubscriber) => () => void}
      />,
    );

    // Push a flood overlay first.
    act(() => {
      sessionBus.push({ loaded_layers: [makeWireLayer("flood-demo")] });
    });

    const m = lastMapMock!;
    expect(m._addedLayers.has("flood-demo")).toBe(true);

    // Toggle to dark.
    rerender(
      <MapView
        theme="dark"
        subscribeSessionState={sessionBus.subscribe as (cb: SessionStateSubscriber) => () => void}
      />,
    );

    // Toggle back to light — the QGIS WMS basemap layer must re-mount
    // UNDER the flood overlay (beforeId = "flood-demo").
    m.addLayer.mockClear();
    rerender(
      <MapView
        theme="light"
        subscribeSessionState={sessionBus.subscribe as (cb: SessionStateSubscriber) => () => void}
      />,
    );

    expect(m.addLayer).toHaveBeenCalledWith(
      expect.objectContaining({ id: "qgis-basemap", source: "qgis-wms" }),
      "flood-demo",
    );
  });
});
