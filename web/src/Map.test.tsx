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
  // job-0152: captures constructor options to assert attributionControl: false
  _constructorOptions: Record<string, unknown>;
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

    // job-0152: capture constructor options so tests can assert attributionControl: false
    _constructorOptions: Record<string, unknown> = {};

    constructor(options: Record<string, unknown> = {}) {
      this._constructorOptions = options;
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
    // job-0139 — vector additions.
    style_preset?: string | null;
    bbox?: [number, number, number, number] | null;
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

// --- job-0152 — NavigationControl + attribution removal tests ----------- //
//
// Asserts that neither the zoom/compass buttons nor the OSM attribution tag
// are injected into the DOM. Both were removed per user direction 2026-06-08:
// users scroll/pinch to zoom; the attribution tag overlays other UI.
//
// See audit.md OQ: attribution removal is technically against OSM tile-use
// terms — production hosting should re-enable it.

describe("MapView — nav controls + attribution hidden (job-0152)", () => {
  beforeEach(() => {
    lastMapMock = null;
  });

  it("does not call addControl (no NavigationControl injected)", () => {
    render(<MapView />);
    const m = lastMapMock!;
    expect(m.addControl).not.toHaveBeenCalled();
  });

  it("initialises the map with attributionControl: false", () => {
    render(<MapView />);
    const m = lastMapMock!;
    expect(m._constructorOptions.attributionControl).toBe(false);
  });
});

// --- job-0139 — vector layer rendering tests ---------------------------- //
//
// Resolves OQ-PAY-MAP-VECTOR-UNSUPPORTED: Map.tsx now branches on
// layer_type. Vector layers go through `addVectorLayer` which fetches GeoJSON
// (or FlatGeobuf-converted-to-GeoJSON), adds a `geojson` source, and adds
// the right paint layer per geometry kind.
//
// The fetch is mocked per test with the desired FeatureCollection. We use
// vi.spyOn(global, 'fetch') because Map.tsx calls the default `fetch` global
// through `fetchVectorAsGeoJson`'s default arg. Tests stub fetch to return
// a Response-shaped object with .json() returning the FC.

import { addVectorLayer } from "./Map";
import { detectGeomKind, paletteColorFor, presetColorFor, resolveVectorColor, VECTOR_PALETTE } from "./lib/vector_rendering";

function makeFetchResponse(body: object): Response {
  return {
    ok: true,
    status: 200,
    statusText: "OK",
    json: async () => body,
    arrayBuffer: async () => new ArrayBuffer(0),
  } as unknown as Response;
}

function makeWireVectorLayer(
  id: string,
  uri: string,
  opts: { style_preset?: string | null; visible?: boolean; opacity?: number } = {},
) {
  return {
    layer_id: id,
    name: id,
    layer_type: "vector",
    uri,
    visible: opts.visible ?? true,
    opacity: opts.opacity ?? 1,
    style_preset: opts.style_preset ?? null,
  };
}

describe("MapView — vector layer rendering (job-0139)", () => {
  beforeEach(() => {
    lastMapMock = null;
    vi.restoreAllMocks();
  });

  it("adds a geojson source + circle layer for point geometry", async () => {
    const fc = {
      type: "FeatureCollection",
      features: [
        { type: "Feature", geometry: { type: "Point", coordinates: [-81.0, 26.0] }, properties: { species: "panther" } },
        { type: "Feature", geometry: { type: "Point", coordinates: [-81.1, 26.1] }, properties: { species: "panther" } },
      ],
    };
    vi.spyOn(global, "fetch").mockResolvedValue(makeFetchResponse(fc));

    const sessionBus = makeSessionBus();
    render(<MapView subscribeSessionState={sessionBus.subscribe as (cb: SessionStateSubscriber) => () => void} />);

    act(() => {
      sessionBus.push({
        loaded_layers: [makeWireVectorLayer("panther-occurrences", "https://example.com/panther.geojson")],
      });
    });

    // Vector path is async — wait a microtask for the fetch to resolve.
    await new Promise((r) => setTimeout(r, 0));

    const m = lastMapMock!;
    // Confirm fetch was called with the layer's uri.
    expect(global.fetch).toHaveBeenCalledWith("https://example.com/panther.geojson");
    // Source registered as geojson with the parsed FeatureCollection.
    const sourceCall = m.addSource.mock.calls.find((c) => (c as MockCallArgs)[0] === "panther-occurrences");
    expect(sourceCall).toBeDefined();
    const sourceDef = (sourceCall as MockCallArgs)[1] as { type: string; data: unknown };
    expect(sourceDef.type).toBe("geojson");
    expect((sourceDef.data as { type: string }).type).toBe("FeatureCollection");

    // Paint layer added with type=circle.
    const layerCall = m.addLayer.mock.calls.find((c) => ((c as MockCallArgs)[0] as { id: string }).id === "panther-occurrences");
    expect(layerCall).toBeDefined();
    const layerDef = (layerCall as MockCallArgs)[0] as { type: string; paint: Record<string, unknown> };
    expect(layerDef.type).toBe("circle");
    expect(layerDef.paint).toHaveProperty("circle-color");
    expect(layerDef.paint).toHaveProperty("circle-radius");
    expect(layerDef.paint).toHaveProperty("circle-stroke-color");
  });

  it("adds a fill layer for polygon geometry (e.g. WDPA protected areas)", async () => {
    const fc = {
      type: "FeatureCollection",
      features: [
        {
          type: "Feature",
          geometry: {
            type: "Polygon",
            coordinates: [[[-81.0, 26.0], [-81.1, 26.0], [-81.1, 26.1], [-81.0, 26.1], [-81.0, 26.0]]],
          },
          properties: { name: "Big Cypress National Preserve" },
        },
      ],
    };
    vi.spyOn(global, "fetch").mockResolvedValue(makeFetchResponse(fc));

    const sessionBus = makeSessionBus();
    render(<MapView subscribeSessionState={sessionBus.subscribe as (cb: SessionStateSubscriber) => () => void} />);

    act(() => {
      sessionBus.push({
        loaded_layers: [
          makeWireVectorLayer("wdpa-big-cypress", "https://example.com/wdpa.geojson", { style_preset: "wdpa_polygon" }),
        ],
      });
    });
    await new Promise((r) => setTimeout(r, 0));

    const m = lastMapMock!;
    const layerCall = m.addLayer.mock.calls.find((c) => ((c as MockCallArgs)[0] as { id: string }).id === "wdpa-big-cypress");
    expect(layerCall).toBeDefined();
    const layerDef = (layerCall as MockCallArgs)[0] as { type: string; paint: Record<string, unknown> };
    expect(layerDef.type).toBe("fill");
    expect(layerDef.paint).toHaveProperty("fill-color");
    expect(layerDef.paint).toHaveProperty("fill-opacity");
    // wdpa_polygon style_preset → slate #708090 per the job-0146 curated palette
    // (WDPA areas are admin-boundary context overlays, not focal layers — slate
    // reads clearly against the dark basemap without competing with species layers).
    expect(layerDef.paint["fill-color"]).toBe("#708090");
  });

  it("adds a line layer for linestring geometry (e.g. OSM roads)", async () => {
    const fc = {
      type: "FeatureCollection",
      features: [
        {
          type: "Feature",
          geometry: { type: "LineString", coordinates: [[-81.0, 26.0], [-81.1, 26.1]] },
          properties: { highway: "primary" },
        },
      ],
    };
    vi.spyOn(global, "fetch").mockResolvedValue(makeFetchResponse(fc));

    const sessionBus = makeSessionBus();
    render(<MapView subscribeSessionState={sessionBus.subscribe as (cb: SessionStateSubscriber) => () => void} />);

    act(() => {
      sessionBus.push({
        loaded_layers: [makeWireVectorLayer("osm-primary-roads", "https://example.com/roads.geojson")],
      });
    });
    await new Promise((r) => setTimeout(r, 0));

    const m = lastMapMock!;
    const layerCall = m.addLayer.mock.calls.find((c) => ((c as MockCallArgs)[0] as { id: string }).id === "osm-primary-roads");
    expect(layerCall).toBeDefined();
    const layerDef = (layerCall as MockCallArgs)[0] as { type: string };
    expect(layerDef.type).toBe("line");
  });

  it("adds multiple vector layers with deterministically-different palette colors", async () => {
    const makeFc = (lng: number) => ({
      type: "FeatureCollection",
      features: [{ type: "Feature", geometry: { type: "Point", coordinates: [lng, 26.0] }, properties: {} }],
    });
    // Sequential fetch responses — vi.spyOn().mockResolvedValueOnce chain.
    const fetchSpy = vi.spyOn(global, "fetch")
      .mockResolvedValueOnce(makeFetchResponse(makeFc(-81.0)))
      .mockResolvedValueOnce(makeFetchResponse(makeFc(-81.1)))
      .mockResolvedValueOnce(makeFetchResponse(makeFc(-81.2)));

    const sessionBus = makeSessionBus();
    render(<MapView subscribeSessionState={sessionBus.subscribe as (cb: SessionStateSubscriber) => () => void} />);

    act(() => {
      sessionBus.push({
        loaded_layers: [
          makeWireVectorLayer("panther-occurrences", "https://ex.com/p.geojson"),
          makeWireVectorLayer("spoonbill-occurrences", "https://ex.com/s.geojson"),
          makeWireVectorLayer("alligator-occurrences", "https://ex.com/a.geojson"),
        ],
      });
    });
    await new Promise((r) => setTimeout(r, 0));
    await new Promise((r) => setTimeout(r, 0));

    expect(fetchSpy).toHaveBeenCalledTimes(3);
    const m = lastMapMock!;
    const calls = ["panther-occurrences", "spoonbill-occurrences", "alligator-occurrences"].map((id) =>
      m.addLayer.mock.calls.find((c) => ((c as MockCallArgs)[0] as { id: string }).id === id),
    );
    expect(calls.every((c) => c !== undefined)).toBe(true);
    const colors = calls.map((c) => {
      const def = (c as MockCallArgs)[0] as { paint: Record<string, unknown> };
      return def.paint["circle-color"] as string;
    });
    // Per-species discipline: each species gets a distinct deterministic colour.
    expect(new Set(colors).size).toBe(3);
    // All from the palette.
    for (const c of colors) {
      expect(VECTOR_PALETTE).toContain(c);
    }
  });

  it("uses style_preset color when present (overrides palette)", async () => {
    const fc = {
      type: "FeatureCollection",
      features: [{ type: "Feature", geometry: { type: "Point", coordinates: [-81.0, 26.0] }, properties: {} }],
    };
    vi.spyOn(global, "fetch").mockResolvedValue(makeFetchResponse(fc));

    const sessionBus = makeSessionBus();
    render(<MapView subscribeSessionState={sessionBus.subscribe as (cb: SessionStateSubscriber) => () => void} />);

    act(() => {
      sessionBus.push({
        loaded_layers: [makeWireVectorLayer("nws-alerts", "https://ex.com/alerts.geojson", { style_preset: "nws_alert" })],
      });
    });
    await new Promise((r) => setTimeout(r, 0));

    const m = lastMapMock!;
    const layerCall = m.addLayer.mock.calls.find((c) => ((c as MockCallArgs)[0] as { id: string }).id === "nws-alerts");
    const def = (layerCall as MockCallArgs)[0] as { paint: Record<string, unknown> };
    // job-0146 curated palette: nws_alert → fire red #FF4444
    expect(def.paint["circle-color"]).toBe("#FF4444");
  });

  it("removes the vector source+layer when it disappears from session-state (A.7)", async () => {
    const fc = {
      type: "FeatureCollection",
      features: [{ type: "Feature", geometry: { type: "Point", coordinates: [-81.0, 26.0] }, properties: {} }],
    };
    vi.spyOn(global, "fetch").mockResolvedValue(makeFetchResponse(fc));

    const sessionBus = makeSessionBus();
    render(<MapView subscribeSessionState={sessionBus.subscribe as (cb: SessionStateSubscriber) => () => void} />);

    act(() => {
      sessionBus.push({
        loaded_layers: [makeWireVectorLayer("panther", "https://ex.com/p.geojson")],
      });
    });
    await new Promise((r) => setTimeout(r, 0));

    const m = lastMapMock!;
    // Confirm layer added.
    expect(m._addedLayers.has("panther")).toBe(true);

    // Now drop it.
    act(() => {
      sessionBus.push({ loaded_layers: [] });
    });

    expect(m.removeLayer).toHaveBeenCalledWith("panther");
    expect(m.removeSource).toHaveBeenCalledWith("panther");
  });

  it("does not break existing raster path when a session-state push has both raster and vector layers", async () => {
    const fc = {
      type: "FeatureCollection",
      features: [{ type: "Feature", geometry: { type: "Point", coordinates: [-81.0, 26.0] }, properties: {} }],
    };
    vi.spyOn(global, "fetch").mockResolvedValue(makeFetchResponse(fc));

    const sessionBus = makeSessionBus();
    render(<MapView subscribeSessionState={sessionBus.subscribe as (cb: SessionStateSubscriber) => () => void} />);

    act(() => {
      sessionBus.push({
        loaded_layers: [
          // Raster — flood depth COG.
          { layer_id: "flood-demo", name: "Flood depth", layer_type: "raster", uri: "https://qgis.example.com/wms?LAYERS=flood-demo", visible: true },
          // Vector — species points.
          makeWireVectorLayer("panther", "https://ex.com/p.geojson"),
        ],
      });
    });
    await new Promise((r) => setTimeout(r, 0));

    const m = lastMapMock!;
    // Raster source uses tiles[] + raster type.
    const floodSourceCall = m.addSource.mock.calls.find((c) => (c as MockCallArgs)[0] === "flood-demo");
    expect(floodSourceCall).toBeDefined();
    expect(((floodSourceCall as MockCallArgs)[1] as { type: string }).type).toBe("raster");
    // Vector source uses geojson type.
    const pantherSourceCall = m.addSource.mock.calls.find((c) => (c as MockCallArgs)[0] === "panther");
    expect(pantherSourceCall).toBeDefined();
    expect(((pantherSourceCall as MockCallArgs)[1] as { type: string }).type).toBe("geojson");
  });
});

// --- vector_rendering helper unit tests ---------------------------------- //

describe("vector_rendering — pure helpers", () => {
  it("detectGeomKind returns 'point' for Point and MultiPoint", () => {
    expect(detectGeomKind({ type: "FeatureCollection", features: [
      { type: "Feature", geometry: { type: "Point", coordinates: [0, 0] }, properties: {} },
    ] })).toBe("point");
    expect(detectGeomKind({ type: "FeatureCollection", features: [
      { type: "Feature", geometry: { type: "MultiPoint", coordinates: [[0, 0], [1, 1]] }, properties: {} },
    ] })).toBe("point");
  });

  it("detectGeomKind returns 'polygon' for Polygon and MultiPolygon", () => {
    expect(detectGeomKind({ type: "FeatureCollection", features: [
      { type: "Feature", geometry: { type: "Polygon", coordinates: [[[0, 0], [1, 0], [1, 1], [0, 0]]] }, properties: {} },
    ] })).toBe("polygon");
  });

  it("detectGeomKind returns 'line' for LineString", () => {
    expect(detectGeomKind({ type: "FeatureCollection", features: [
      { type: "Feature", geometry: { type: "LineString", coordinates: [[0, 0], [1, 1]] }, properties: {} },
    ] })).toBe("line");
  });

  it("detectGeomKind returns 'unknown' for an empty FeatureCollection", () => {
    expect(detectGeomKind({ type: "FeatureCollection", features: [] })).toBe("unknown");
  });

  it("paletteColorFor is deterministic across calls (same id → same colour)", () => {
    expect(paletteColorFor("panther")).toBe(paletteColorFor("panther"));
    expect(paletteColorFor("alligator")).toBe(paletteColorFor("alligator"));
    // (Note: with a 12-colour palette + FNV-1a, distinct IDs may share a
    // colour by birthday-paradox collisions. Determinism is the load-bearing
    // property we test; distinctness is exercised in the multi-layer
    // rendering test above using non-colliding IDs.)
  });

  it("paletteColorFor always returns a colour from VECTOR_PALETTE", () => {
    for (const id of ["a", "panther", "spoonbill", "alligator", "very-long-layer-id-12345"]) {
      expect(VECTOR_PALETTE).toContain(paletteColorFor(id));
    }
  });

  it("presetColorFor maps WDPA to slate and NWS alert to fire red (job-0146 curated palette)", () => {
    // job-0146: WDPA → slate #708090 (admin-boundary context), NWS alert → fire red #FF4444
    expect(presetColorFor("wdpa_polygon")).toBe("#708090");
    expect(presetColorFor("nws_alert")).toBe("#FF4444");
    expect(presetColorFor("totally_unknown")).toBeUndefined();
    expect(presetColorFor(null)).toBeUndefined();
    expect(presetColorFor(undefined)).toBeUndefined();
  });

  it("resolveVectorColor prefers preset over palette", () => {
    // job-0146: WDPA → slate #708090
    expect(resolveVectorColor("panther", "wdpa_polygon")).toBe("#708090");
    expect(resolveVectorColor("panther", null)).toBe(paletteColorFor("panther"));
  });
});

// --- addVectorLayer race-guards (job-0139 — cleanup-on-remove) --------- //

describe("addVectorLayer — race guards", () => {
  function makeMockMap() {
    return {
      addSource: vi.fn(),
      addLayer: vi.fn(),
      isStyleLoaded: vi.fn().mockReturnValue(true),
    } as unknown as Parameters<typeof addVectorLayer>[0];
  }

  it("aborts cleanly when the layer is removed before the fetch resolves (generation guard)", async () => {
    const fc = {
      type: "FeatureCollection",
      features: [{ type: "Feature", geometry: { type: "Point", coordinates: [-81, 26] }, properties: {} }],
    };
    vi.spyOn(global, "fetch").mockResolvedValue(makeFetchResponse(fc));

    const m = makeMockMap();
    const fetchGen = { current: new Map<string, number>([["lyr", 1]]) };
    const geomKinds = { current: new Map() };
    const addedIds = { current: new Set<string>(["lyr"]) };

    // Start the async add with generation=1.
    const promise = addVectorLayer(m, { layer_id: "lyr", uri: "https://ex/g.geojson" }, 1, fetchGen, geomKinds, addedIds);
    // Simulate removal mid-fetch: bump generation.
    fetchGen.current.set("lyr", 2);
    addedIds.current.delete("lyr");

    await promise;
    // No source/layer should have been added because the guard caught it.
    expect((m as unknown as { addSource: ReturnType<typeof vi.fn> }).addSource).not.toHaveBeenCalled();
    expect((m as unknown as { addLayer: ReturnType<typeof vi.fn> }).addLayer).not.toHaveBeenCalled();
  });

  it("logs and exits when fetch throws (no orphan source registered)", async () => {
    vi.spyOn(global, "fetch").mockRejectedValue(new Error("net"));
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});

    const m = makeMockMap();
    const fetchGen = { current: new Map<string, number>([["lyr", 1]]) };
    const geomKinds = { current: new Map() };
    const addedIds = { current: new Set<string>(["lyr"]) };

    await addVectorLayer(m, { layer_id: "lyr", uri: "https://ex/g.geojson" }, 1, fetchGen, geomKinds, addedIds);

    expect((m as unknown as { addSource: ReturnType<typeof vi.fn> }).addSource).not.toHaveBeenCalled();
    expect(warnSpy).toHaveBeenCalled();
    // Slot must be released so a retry can re-register.
    expect(addedIds.current.has("lyr")).toBe(false);
    warnSpy.mockRestore();
  });
});
