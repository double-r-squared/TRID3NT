// GRACE-2 web — Map.tsx feature-click/tap-to-inspect tests (F74b).
//
// Verifies the click/tap-to-inspect handler the agent advertises but which had
// no implementation before this feature:
//   1. A click that queryRenderedFeatures returns a hit for opens the popup
//      with the feature's name / designation / IUCN attributes.
//   2. A TAP (the same MapLibre `click` event fires on touch) with a hit opens
//      the popup → mobile path works.
//   3. A click with NO hit dismisses any open popup.
//   4. The popup is dismissable via its X button.
//   5. queryRenderedFeatures is restricted to the rendered vector layers.
//   6. Hover sets the canvas cursor to "pointer" over a hittable feature.
//   7. The pure property-extraction helpers (humanize / stringify / build).
//
// maplibre-gl is mocked (no WebGL in happy-dom). This mock — unlike the one in
// Map.test.tsx — actually REGISTERS event handlers (on/off) and implements
// queryRenderedFeatures + getCanvas().style so the click/cursor paths run.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, act, screen, fireEvent } from "@testing-library/react";
import {
  MapView,
  buildFeaturePopupData,
  humanizePropertyKey,
  stringifyPropertyValue,
  type SessionStateSubscriber,
} from "./Map";

// --- MapLibre mock with real event registration + queryRenderedFeatures ---- //

type Listener = (e: unknown) => void;

interface FeatureInspectMapMock {
  addSource: ReturnType<typeof vi.fn>;
  addLayer: ReturnType<typeof vi.fn>;
  removeLayer: ReturnType<typeof vi.fn>;
  removeSource: ReturnType<typeof vi.fn>;
  setPaintProperty: ReturnType<typeof vi.fn>;
  setLayoutProperty: ReturnType<typeof vi.fn>;
  moveLayer: ReturnType<typeof vi.fn>;
  fitBounds: ReturnType<typeof vi.fn>;
  getLayer: ReturnType<typeof vi.fn>;
  getSource: ReturnType<typeof vi.fn>;
  isStyleLoaded: ReturnType<typeof vi.fn>;
  queryRenderedFeatures: ReturnType<typeof vi.fn>;
  remove: ReturnType<typeof vi.fn>;
  on: (ev: string, h: Listener) => void;
  off: (ev: string, h: Listener) => void;
  once: ReturnType<typeof vi.fn>;
  project: ReturnType<typeof vi.fn>;
  getCanvas: () => { clientWidth: number; clientHeight: number; style: { cursor: string } };
  getStyle: ReturnType<typeof vi.fn>;
  _emit: (ev: string, e: unknown) => void;
  _canvasStyle: { cursor: string };
  _addedLayers: Set<string>;
  _addedSources: Set<string>;
}

let lastMapMock: FeatureInspectMapMock | null = null;

vi.mock("maplibre-gl", () => {
  class MockMap {
    _addedLayers = new Set<string>(["qgis-basemap", "osm-fallback-basemap"]);
    _addedSources = new Set<string>(["qgis-wms", "osm-fallback"]);
    _listeners = new Map<string, Listener[]>();
    _canvasStyle = { cursor: "" };

    addSource = vi.fn((id: string) => {
      this._addedSources.add(id);
    });
    addLayer = vi.fn((def: { id: string }) => {
      this._addedLayers.add(def.id);
    });
    removeLayer = vi.fn((id: string) => this._addedLayers.delete(id));
    removeSource = vi.fn((id: string) => this._addedSources.delete(id));
    setPaintProperty = vi.fn();
    setLayoutProperty = vi.fn();
    moveLayer = vi.fn();
    fitBounds = vi.fn();
    getLayer = vi.fn((id: string) => (this._addedLayers.has(id) ? { id } : null));
    getSource = vi.fn((id: string) =>
      this._addedSources.has(id) ? { type: "geojson", setData: vi.fn() } : null,
    );
    isStyleLoaded = vi.fn().mockReturnValue(true);
    queryRenderedFeatures = vi.fn(() => [] as unknown[]);
    remove = vi.fn();
    touchZoomRotate = { disableRotation: vi.fn() };
    keyboard = { disableRotation: vi.fn() };
    addControl = vi.fn();

    on = (ev: string, h: Listener): void => {
      const arr = this._listeners.get(ev) ?? [];
      arr.push(h);
      this._listeners.set(ev, arr);
    };
    off = (ev: string, h: Listener): void => {
      const arr = this._listeners.get(ev);
      if (arr) this._listeners.set(ev, arr.filter((x) => x !== h));
    };
    once = vi.fn();
    _emit = (ev: string, e: unknown): void => {
      (this._listeners.get(ev) ?? []).forEach((h) => h(e));
    };

    project = vi.fn((ll: [number, number]) => ({ x: (ll[0] + 180) * 2, y: (90 - ll[1]) * 2 }));
    getCanvas = (): { clientWidth: number; clientHeight: number; style: { cursor: string } } => ({
      clientWidth: 1024,
      clientHeight: 768,
      style: this._canvasStyle,
    });
    getStyle = vi.fn(() => ({
      layers: Array.from(this._addedLayers).map((id) => ({ id })),
      sources: {},
    }));

    constructor() {
      lastMapMock = this as unknown as FeatureInspectMapMock;
    }
  }

  return {
    default: { Map: MockMap, NavigationControl: class {} },
    Map: MockMap,
    NavigationControl: class {},
  };
});

vi.mock("maplibre-gl/dist/maplibre-gl.css", () => ({}));

// Make a vector layer "rendered" so the click handler's queryableLayerIds()
// includes it: push session-state with a polygon vector layer (inline GeoJSON
// so no fetch is needed) and let the async addVectorLayer register it.
interface WireSessionState {
  loaded_layers?: Array<Record<string, unknown>>;
}
type SessionSubscriber = (p: WireSessionState) => void;

function makeSessionBus() {
  const subs: SessionSubscriber[] = [];
  return {
    push: (p: WireSessionState) => subs.forEach((s) => s(p)),
    subscribe: (cb: SessionSubscriber) => {
      subs.push(cb);
      return () => subs.splice(subs.indexOf(cb), 1);
    },
  };
}

function wdpaInlineLayer(id = "wdpa-big-cypress") {
  return {
    layer_id: id,
    name: id,
    layer_type: "vector",
    uri: "gs://unused-because-inline",
    visible: true,
    opacity: 1,
    style_preset: "wdpa_polygon",
    inline_geojson: {
      type: "FeatureCollection",
      features: [
        {
          type: "Feature",
          geometry: {
            type: "Polygon",
            coordinates: [[[-81, 26], [-81.1, 26], [-81.1, 26.1], [-81, 26.1], [-81, 26]]],
          },
          properties: {
            name_eng: "Big Cypress National Preserve",
            desig_eng: "National Preserve",
            iucn_cat: "II",
            status_yr: 1974,
          },
        },
      ],
    },
  };
}

async function renderWithRenderedVectorLayer(): Promise<FeatureInspectMapMock> {
  const sessionBus = makeSessionBus();
  render(
    <MapView
      subscribeSessionState={
        sessionBus.subscribe as unknown as (cb: SessionStateSubscriber) => () => void
      }
    />,
  );
  await act(async () => {
    sessionBus.push({ loaded_layers: [wdpaInlineLayer()] });
    // addVectorLayer is async (inline path is sync work but wrapped in a promise).
    await Promise.resolve();
    await Promise.resolve();
  });
  return lastMapMock!;
}

// --- Tests ---------------------------------------------------------------- //

describe("MapView — feature click/tap-to-inspect (F74b)", () => {
  beforeEach(() => {
    lastMapMock = null;
  });

  it("opens a popup with name / designation / IUCN when a click hits a vector feature", async () => {
    const m = await renderWithRenderedVectorLayer();
    expect(m._addedLayers.has("wdpa-big-cypress")).toBe(true);

    // The hit feature MapLibre returns from queryRenderedFeatures.
    m.queryRenderedFeatures.mockReturnValue([
      {
        layer: { id: "wdpa-big-cypress", source: "wdpa-big-cypress" },
        properties: {
          name_eng: "Big Cypress National Preserve",
          desig_eng: "National Preserve",
          iucn_cat: "II",
          status_yr: 1974,
        },
      },
    ]);

    act(() => {
      m._emit("click", { point: { x: 400, y: 300 } });
    });

    // queryRenderedFeatures must be restricted to the rendered vector layer.
    const qrfArgs = m.queryRenderedFeatures.mock.calls[0];
    expect((qrfArgs?.[1] as { layers: string[] }).layers).toContain("wdpa-big-cypress");

    // Popup shows the name as title, designation as subtitle, IUCN as a row.
    expect(screen.getByTestId("grace2-feature-popup")).toBeTruthy();
    expect(screen.getByTestId("feature-popup-title").textContent).toBe(
      "Big Cypress National Preserve",
    );
    expect(screen.getByTestId("feature-popup-subtitle").textContent).toBe(
      "National Preserve",
    );
    const attrs = screen.getByTestId("feature-popup-attributes").textContent ?? "";
    expect(attrs).toContain("IUCN Category");
    expect(attrs).toContain("II");
    // Other non-name/desig/iucn props are humanized + shown.
    expect(attrs).toContain("Status Yr");
    expect(attrs).toContain("1974");
  });

  it("opens the popup on a TAP too (MapLibre fires the same `click` from a tap) — mobile path", async () => {
    const m = await renderWithRenderedVectorLayer();
    m.queryRenderedFeatures.mockReturnValue([
      {
        layer: { id: "wdpa-big-cypress", source: "wdpa-big-cypress" },
        properties: { name_eng: "Tapped Reserve", iucn_cat: "Ia" },
      },
    ]);

    // A tap surfaces as a `click` MapMouseEvent in MapLibre once the tap did
    // not pan — emitting it exercises the exact same handler touch users hit.
    act(() => {
      m._emit("click", { point: { x: 120, y: 600 } });
    });

    expect(screen.getByTestId("feature-popup-title").textContent).toBe("Tapped Reserve");
    expect(screen.getByTestId("feature-popup-attributes").textContent).toContain("Ia");
  });

  it("dismisses the popup when a click hits empty map (no feature)", async () => {
    const m = await renderWithRenderedVectorLayer();
    m.queryRenderedFeatures.mockReturnValue([
      { layer: { id: "wdpa-big-cypress", source: "wdpa-big-cypress" }, properties: { name_eng: "X" } },
    ]);
    act(() => {
      m._emit("click", { point: { x: 400, y: 300 } });
    });
    expect(screen.queryByTestId("grace2-feature-popup")).toBeTruthy();

    // Now a click that hits nothing.
    m.queryRenderedFeatures.mockReturnValue([]);
    act(() => {
      m._emit("click", { point: { x: 10, y: 10 } });
    });
    expect(screen.queryByTestId("grace2-feature-popup")).toBeNull();
  });

  it("dismisses the popup via the X button", async () => {
    const m = await renderWithRenderedVectorLayer();
    m.queryRenderedFeatures.mockReturnValue([
      { layer: { id: "wdpa-big-cypress", source: "wdpa-big-cypress" }, properties: { name_eng: "Closeable" } },
    ]);
    act(() => {
      m._emit("click", { point: { x: 400, y: 300 } });
    });
    expect(screen.queryByTestId("grace2-feature-popup")).toBeTruthy();

    act(() => {
      fireEvent.click(screen.getByTestId("feature-popup-close"));
    });
    expect(screen.queryByTestId("grace2-feature-popup")).toBeNull();
  });

  it("sets the canvas cursor to pointer over a hittable feature and clears it otherwise", async () => {
    const m = await renderWithRenderedVectorLayer();

    m.queryRenderedFeatures.mockReturnValue([
      { layer: { id: "wdpa-big-cypress", source: "wdpa-big-cypress" }, properties: {} },
    ]);
    act(() => {
      m._emit("mousemove", { point: { x: 400, y: 300 } });
    });
    expect(m._canvasStyle.cursor).toBe("pointer");

    m.queryRenderedFeatures.mockReturnValue([]);
    act(() => {
      m._emit("mousemove", { point: { x: 5, y: 5 } });
    });
    expect(m._canvasStyle.cursor).toBe("");
  });

  it("does not query when no vector layers are rendered (raster-only map)", () => {
    render(<MapView />);
    const m = lastMapMock!;
    // No vector layers tracked → click is a no-op (and dismisses nothing).
    act(() => {
      m._emit("click", { point: { x: 400, y: 300 } });
    });
    expect(m.queryRenderedFeatures).not.toHaveBeenCalled();
    expect(screen.queryByTestId("grace2-feature-popup")).toBeNull();
  });
});

// --- pure helper unit tests ---------------------------------------------- //

describe("feature-inspect pure helpers", () => {
  it("humanizePropertyKey turns snake/camel keys into Title Case", () => {
    expect(humanizePropertyKey("name_eng")).toBe("Name Eng");
    expect(humanizePropertyKey("iucn_cat")).toBe("Iucn Cat");
    expect(humanizePropertyKey("scientificName")).toBe("Scientific Name");
    expect(humanizePropertyKey("status-yr")).toBe("Status Yr");
  });

  it("stringifyPropertyValue handles strings, numbers, bools, and drops empties", () => {
    expect(stringifyPropertyValue("National Preserve")).toBe("National Preserve");
    expect(stringifyPropertyValue("  ")).toBeNull();
    expect(stringifyPropertyValue(1974)).toBe("1974");
    expect(stringifyPropertyValue(0.123456)).toBe("0.123");
    expect(stringifyPropertyValue(true)).toBe("Yes");
    expect(stringifyPropertyValue(false)).toBe("No");
    expect(stringifyPropertyValue(null)).toBeNull();
    expect(stringifyPropertyValue(undefined)).toBeNull();
    expect(stringifyPropertyValue(NaN)).toBeNull();
  });

  it("buildFeaturePopupData picks name/designation/IUCN and de-noises the rest", () => {
    const data = buildFeaturePopupData(
      {
        name_eng: "Big Cypress National Preserve",
        desig_eng: "National Preserve",
        iucn_cat: "II",
        status: "Designated",
        objectid: 42, // hidden noise key
      },
      { x: 100, y: 100 },
      { layerName: "wdpa-big-cypress" },
    );
    expect(data.title).toBe("Big Cypress National Preserve");
    expect(data.subtitle).toBe("National Preserve");
    // IUCN leads the attribute list.
    expect(data.attributes[0]).toEqual({ label: "IUCN Category", value: "II" });
    const labels = data.attributes.map((a) => a.label);
    expect(labels).toContain("Status");
    // objectid is a hidden noise key — not shown.
    expect(labels).not.toContain("Objectid");
    // name/desig/iucn are not duplicated into the attribute list.
    expect(labels).not.toContain("Name Eng");
    expect(labels).not.toContain("Desig Eng");
  });

  it("buildFeaturePopupData falls back to geometry-kind label when no name is present", () => {
    const data = buildFeaturePopupData(
      { foo: "bar" },
      { x: 1, y: 2 },
      { geomKindLabel: "Polygon" },
    );
    expect(data.title).toBe("Polygon");
    expect(data.attributes.map((a) => a.label)).toContain("Foo");
  });

  it("buildFeaturePopupData gracefully handles null/empty properties", () => {
    const data = buildFeaturePopupData(null, { x: 0, y: 0 }, { layerName: "layer-x" });
    expect(data.title).toBe("layer-x");
    expect(data.attributes).toEqual([]);
  });
});
