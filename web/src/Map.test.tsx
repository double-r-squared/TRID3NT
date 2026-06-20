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
  moveLayer: ReturnType<typeof vi.fn>;
  fitBounds: ReturnType<typeof vi.fn>;
  addControl: ReturnType<typeof vi.fn>;
  touchZoomRotate: { disableRotation: ReturnType<typeof vi.fn> };
  keyboard: { disableRotation: ReturnType<typeof vi.fn> };
  getLayer: ReturnType<typeof vi.fn>;
  getSource: ReturnType<typeof vi.fn>;
  isStyleLoaded: ReturnType<typeof vi.fn>;
  remove: ReturnType<typeof vi.fn>;
  on: ReturnType<typeof vi.fn>;
  off: ReturnType<typeof vi.fn>;
  once: ReturnType<typeof vi.fn>;
  // job-0321 (F43) — legend-anchor projection.
  project: ReturnType<typeof vi.fn>;
  getCanvas: ReturnType<typeof vi.fn>;
  getStyle: ReturnType<typeof vi.fn>;
  _addedLayers: Set<string>;
  _addedSources: Set<string>;
  _sourceSetData: Map<string, ReturnType<typeof vi.fn>>;
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

    // Per-source setData stubs so the analysis-extent replace branch
    // (getSource(...).setData) behaves like a real GeoJSONSource. Raster
    // sources never call setData, so a default no-op stub is harmless there.
    _sourceSetData = new Map<string, ReturnType<typeof vi.fn>>();

    addSource = vi.fn((id: string, _def: unknown) => {
      this._addedSources.add(id);
      this._sourceSetData.set(id, vi.fn());
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
    // job-0258: layer re-stacking (set-layer-order map-command).
    moveLayer = vi.fn();
    fitBounds = vi.fn();
    addControl = vi.fn();
    touchZoomRotate = { disableRotation: vi.fn() };
    keyboard = { disableRotation: vi.fn() };
    // isStyleLoaded must return true for the session-state handler to apply.
    isStyleLoaded = vi.fn().mockReturnValue(true);
    // getLayer / getSource consult the internal tracking sets.
    getLayer = vi.fn((id: string) => (this._addedLayers.has(id) ? { id } : null));
    getSource = vi.fn((id: string) =>
      this._addedSources.has(id)
        ? { type: "geojson", setData: this._sourceSetData.get(id) ?? vi.fn() }
        : null,
    );
    remove = vi.fn();
    // Event handlers — applyLatest attaches `once("idle", ...)` after every
    // session-state push; the mock just no-ops (synchronous apply path is
    // what tests verify).
    on = vi.fn();
    off = vi.fn();
    once = vi.fn();
    // job-0321 (F43) — the legend-anchor projection effect calls project() to
    // map bbox corners to screen space and getCanvas() to read the viewport
    // size for the off-screen test. Deterministic stubs so the anchor lands
    // on-screen for the tests that exercise it.
    project = vi.fn((lngLat: [number, number]) => ({
      x: (lngLat[0] + 180) * 2, // arbitrary but monotonic; keeps anchor on-screen
      y: (90 - lngLat[1]) * 2,
    }));
    getCanvas = vi.fn(() => ({ clientWidth: 1024, clientHeight: 768 }));
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

describe("MapView — per-Case layer DURABILITY across WS reconnect (job-0357)", () => {
  beforeEach(() => {
    lastMapMock = null;
  });

  // The `replace_layers` flag App.tsx stamps onto every session-state it
  // pushes onto the LayerPanel bus is a client-only field; the test
  // `WireSessionState` type doesn't carry it, so we widen the push at the
  // call site. The agent never sends it on the wire.
  type DurableSession = WireSessionState & { replace_layers?: boolean };
  const pushDurable = (
    bus: ReturnType<typeof makeSessionBus>,
    p: DurableSession,
  ): void => bus.push(p as unknown as WireSessionState);

  it("KEEPS rendered layers when a reconnect delivers an EMPTY snapshot (replace_layers:false)", () => {
    // The bug: a bare WS reconnect briefly replayed an empty/absent
    // session-state, and the reconcile loop tore every overlay off the map
    // until an explicit case-open. A reconnect snapshot (received while the
    // socket is not yet `connected`) is stamped replace_layers:false by
    // App.tsx, so it must NOT remove durable layers.
    const sessionBus = makeSessionBus();
    render(
      <MapView
        subscribeSessionState={
          sessionBus.subscribe as (cb: SessionStateSubscriber) => () => void
        }
      />,
    );

    // Connected: an authoritative snapshot adds the Case's layer.
    act(() => {
      pushDurable(sessionBus, {
        loaded_layers: [makeWireLayer("flood-demo")],
        replace_layers: true,
      });
    });
    const m = lastMapMock!;
    expect(m.addSource).toHaveBeenCalledWith("flood-demo", expect.anything());
    m.getLayer.mockReturnValue({ id: "flood-demo" });
    m.getSource.mockReturnValue({ type: "raster" });
    m.removeLayer.mockClear();
    m.removeSource.mockClear();

    // Reconnect window: a transient EMPTY snapshot arrives (socket not
    // `connected`). It must NOT wipe the durable overlay.
    act(() => {
      pushDurable(sessionBus, { loaded_layers: [], replace_layers: false });
    });
    expect(m.removeLayer).not.toHaveBeenCalled();
    expect(m.removeSource).not.toHaveBeenCalled();
  });

  it("reconnect resume snapshot RECONCILES without duplicate sources (idempotent — REQ 4)", () => {
    // The agent's resume replay carries the FULL persisted layer set. A
    // re-delivered snapshot with the same layer must update-in-place (paint
    // props), never re-add the source.
    const sessionBus = makeSessionBus();
    render(
      <MapView
        subscribeSessionState={
          sessionBus.subscribe as (cb: SessionStateSubscriber) => () => void
        }
      />,
    );
    act(() => {
      pushDurable(sessionBus, {
        loaded_layers: [makeWireLayer("flood-demo")],
        replace_layers: true,
      });
    });
    const m = lastMapMock!;
    expect(m.addSource).toHaveBeenCalledTimes(1);
    m.getLayer.mockReturnValue({ id: "flood-demo" });

    // Resume snapshot (non-authoritative top-up) re-delivers the SAME layer.
    m.addSource.mockClear();
    m.addLayer.mockClear();
    act(() => {
      pushDurable(sessionBus, {
        loaded_layers: [makeWireLayer("flood-demo")],
        replace_layers: false,
      });
    });
    // No duplicate source/layer — the existing slot is reconciled in place.
    expect(m.addSource).not.toHaveBeenCalled();
    expect(m.addLayer).not.toHaveBeenCalled();
  });

  it("non-authoritative reconnect snapshot still ADDS a newly-rendered layer (top-up)", () => {
    // A reconnect resume that carries a layer the client doesn't have yet must
    // still register it — additive reconcile adds, it only declines to remove.
    const sessionBus = makeSessionBus();
    render(
      <MapView
        subscribeSessionState={
          sessionBus.subscribe as (cb: SessionStateSubscriber) => () => void
        }
      />,
    );
    act(() => {
      pushDurable(sessionBus, {
        loaded_layers: [makeWireLayer("flood-demo")],
        replace_layers: false,
      });
    });
    const m = lastMapMock!;
    expect(m.addSource).toHaveBeenCalledWith("flood-demo", expect.anything());
  });

  it("a Case SWITCH (replace_layers:true) STILL clears the prior Case's layers", () => {
    // The fresh-slate behavior on an explicit Case switch must be preserved:
    // an authoritative replace removes overlays absent from the new set.
    const sessionBus = makeSessionBus();
    render(
      <MapView
        subscribeSessionState={
          sessionBus.subscribe as (cb: SessionStateSubscriber) => () => void
        }
      />,
    );
    // Case A.
    act(() => {
      pushDurable(sessionBus, {
        loaded_layers: [makeWireLayer("case-a-layer")],
        replace_layers: true,
      });
    });
    const m = lastMapMock!;
    m.getLayer.mockReturnValue({ id: "case-a-layer" });
    m.getSource.mockReturnValue({ type: "raster" });
    m.removeLayer.mockClear();
    m.removeSource.mockClear();

    // Switch to Case B (authoritative replace, different layer set).
    act(() => {
      pushDurable(sessionBus, {
        loaded_layers: [makeWireLayer("case-b-layer")],
        replace_layers: true,
      });
    });
    expect(m.removeLayer).toHaveBeenCalledWith("case-a-layer");
    expect(m.removeSource).toHaveBeenCalledWith("case-a-layer");
  });

  it("an authoritative empty snapshot (Case EXIT, replace_layers:true) clears everything", () => {
    const sessionBus = makeSessionBus();
    render(
      <MapView
        subscribeSessionState={
          sessionBus.subscribe as (cb: SessionStateSubscriber) => () => void
        }
      />,
    );
    act(() => {
      pushDurable(sessionBus, {
        loaded_layers: [makeWireLayer("flood-demo")],
        replace_layers: true,
      });
    });
    const m = lastMapMock!;
    m.getLayer.mockReturnValue({ id: "flood-demo" });
    m.getSource.mockReturnValue({ type: "raster" });
    m.removeLayer.mockClear();
    m.removeSource.mockClear();

    // Case exit: authoritative empty snapshot.
    act(() => {
      pushDurable(sessionBus, { loaded_layers: [], replace_layers: true });
    });
    expect(m.removeLayer).toHaveBeenCalledWith("flood-demo");
    expect(m.removeSource).toHaveBeenCalledWith("flood-demo");
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

  it("ALSO draws the analysis-extent rectangle from the same zoom-to bbox (job-0294)", () => {
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
    // job-0321 (F40) — OUTLINE-ONLY: the extent source + the dashed line layer
    // are added alongside fitBounds. The translucent fill layer is NO LONGER
    // added (it tinted the layers beneath the AOI).
    const addedSourceIds = m.addSource.mock.calls.map((c) => c[0]);
    expect(addedSourceIds).toContain("grace2-analysis-extent");
    const addedLayerIds = m.addLayer.mock.calls.map(
      (c) => (c[0] as { id: string }).id,
    );
    expect(addedLayerIds).not.toContain("grace2-analysis-extent-fill");
    expect(addedLayerIds).toContain("grace2-analysis-extent-line");
  });

  it("clears the analysis-extent rectangle on a clear-analysis-extent command (ux-batch-1 F14)", () => {
    const mapCmdBus = makeMapCmdBus();
    render(
      <MapView
        subscribeMapCommand={mapCmdBus.subscribe as MapCommandSubscribeFunc}
      />,
    );
    const m = lastMapMock!;

    // Draw an extent first (via zoom-to), then clear it.
    act(() => {
      mapCmdBus.push({
        command: "zoom-to",
        args: { bbox: [-81.91, 26.55, -81.75, 26.69] },
      });
    });
    expect(
      m.addSource.mock.calls.map((c) => c[0]),
    ).toContain("grace2-analysis-extent");

    act(() => {
      mapCmdBus.push({ command: "clear-analysis-extent" } as never);
    });

    // job-0321 (F40) — OUTLINE-ONLY: only the dashed line layer + the source are
    // removed now (the fill is never added, so the clear's existence-guarded
    // fill removal is a no-op — the guard stays for stale-style cleanup).
    const removedLayerIds = m.removeLayer.mock.calls.map((c) => c[0]);
    expect(removedLayerIds).not.toContain("grace2-analysis-extent-fill");
    expect(removedLayerIds).toContain("grace2-analysis-extent-line");
    expect(m.removeSource.mock.calls.map((c) => c[0])).toContain(
      "grace2-analysis-extent",
    );
  });

  // --- AWS-migration hardening regression tests (bbox track) ------------- //
  // These cover the real live failure mode the prior mocks hid: the dashed
  // rectangle was wired but silently never rendered because a throw from
  // drawAnalysisExtent was swallowed by a bare `catch {}`. The fix re-schedules
  // the draw on the next idle (bounded) and re-asserts on moveend after the
  // camera flight. Driven through the map-command path (not by calling
  // drawAnalysisExtent directly) so the handler itself is exercised.

  it("re-schedules the extent draw on once('idle') when drawAnalysisExtent throws (no silent swallow)", () => {
    const mapCmdBus = makeMapCmdBus();
    render(
      <MapView
        subscribeMapCommand={mapCmdBus.subscribe as MapCommandSubscribeFunc}
      />,
    );
    const m = lastMapMock!;

    // Make the FIRST addLayer throw (the live mid-style-mutation race). The
    // throw must NOT be swallowed permanently — the handler must arm a retry.
    m.addLayer.mockImplementationOnce(() => {
      throw new Error("Style is not done loading");
    });

    act(() => {
      mapCmdBus.push({
        command: "zoom-to",
        args: { bbox: [-105.3, 39.95, -105.2, 40.05] },
      });
    });

    // A once('idle', ...) retry must have been armed (the old code swallowed
    // and armed nothing). moveend is also armed; assert idle specifically.
    const idleRetry = m.once.mock.calls.find(
      (c) => (c as MockCallArgs)[0] === "idle",
    ) as MockCallArgs | undefined;
    expect(idleRetry).toBeDefined();

    // On the retry, addLayer no longer throws — the dashed outline lands.
    // job-0321 (F40) — OUTLINE-ONLY: only the line layer is (re-)added.
    m.addLayer.mockClear();
    act(() => {
      (idleRetry![1] as () => void)();
    });
    const retriedLayerIds = m.addLayer.mock.calls.map(
      (c) => (c[0] as { id: string }).id,
    );
    expect(retriedLayerIds).not.toContain("grace2-analysis-extent-fill");
    expect(retriedLayerIds).toContain("grace2-analysis-extent-line");
  });

  it("re-asserts the extent on the moveend after the camera flight settles", () => {
    const mapCmdBus = makeMapCmdBus();
    render(
      <MapView
        subscribeMapCommand={mapCmdBus.subscribe as MapCommandSubscribeFunc}
      />,
    );
    const m = lastMapMock!;

    act(() => {
      mapCmdBus.push({
        command: "zoom-to",
        args: { bbox: [-81.91, 26.55, -81.75, 26.69] },
      });
    });

    // A moveend redraw must be armed (covers the source-add churn during the
    // 1200ms fitBounds animation).
    const moveend = m.once.mock.calls.find(
      (c) => (c as MockCallArgs)[0] === "moveend",
    ) as MockCallArgs | undefined;
    expect(moveend).toBeDefined();

    // Running it is idempotent: the source already exists so it setData-replaces
    // and re-asserts (heals) any missing layers — never double-adds the source.
    m.addSource.mockClear();
    act(() => {
      (moveend![1] as () => void)();
    });
    expect(
      m.addSource.mock.calls.filter(
        (c) => (c as MockCallArgs)[0] === "grace2-analysis-extent",
      ).length,
    ).toBe(0);
    // The extent source + the dashed outline are present after the flight.
    // job-0321 (F40) — OUTLINE-ONLY: the fill layer is never added.
    expect(m._addedSources.has("grace2-analysis-extent")).toBe(true);
    expect(m._addedLayers.has("grace2-analysis-extent-fill")).toBe(false);
    expect(m._addedLayers.has("grace2-analysis-extent-line")).toBe(true);
  });

  it("defers the extent draw to once('idle') when the style is not yet loaded (case-reopen replay race)", () => {
    const mapCmdBus = makeMapCmdBus();
    render(
      <MapView
        subscribeMapCommand={mapCmdBus.subscribe as MapCommandSubscribeFunc}
      />,
    );
    const m = lastMapMock!;
    // Style not loaded when the zoom-to arrives (replay before first load).
    m.isStyleLoaded.mockReturnValue(false);

    act(() => {
      mapCmdBus.push({
        command: "zoom-to",
        args: { bbox: [-105.3, 39.95, -105.2, 40.05] },
      });
    });

    // Nothing drawn synchronously; an idle deferral is armed.
    const addedSourceIds = m.addSource.mock.calls.map((c) => c[0]);
    expect(addedSourceIds).not.toContain("grace2-analysis-extent");
    const idleDefer = m.once.mock.calls.find(
      (c) => (c as MockCallArgs)[0] === "idle",
    ) as MockCallArgs | undefined;
    expect(idleDefer).toBeDefined();

    // Style finishes loading; the deferred draw lands the extent.
    m.isStyleLoaded.mockReturnValue(true);
    act(() => {
      (idleDefer![1] as () => void)();
    });
    expect(
      m.addSource.mock.calls.map((c) => c[0]),
    ).toContain("grace2-analysis-extent");
    const layerIds = m.addLayer.mock.calls.map(
      (c) => (c[0] as { id: string }).id,
    );
    // job-0321 (F40) — OUTLINE-ONLY: dashed line lands, no fill.
    expect(layerIds).not.toContain("grace2-analysis-extent-fill");
    expect(layerIds).toContain("grace2-analysis-extent-line");
  });

  it("REPLACES the extent (setData, no second source add) on a second zoom-to with a new bbox", () => {
    const mapCmdBus = makeMapCmdBus();
    render(
      <MapView
        subscribeMapCommand={mapCmdBus.subscribe as MapCommandSubscribeFunc}
      />,
    );
    const m = lastMapMock!;

    act(() => {
      mapCmdBus.push({
        command: "zoom-to",
        args: { bbox: [-81.91, 26.55, -81.75, 26.69] },
      });
    });
    // First zoom-to added the source once.
    expect(
      m.addSource.mock.calls.filter(
        (c) => (c as MockCallArgs)[0] === "grace2-analysis-extent",
      ).length,
    ).toBe(1);

    m.addSource.mockClear();
    const setData = m._sourceSetData.get("grace2-analysis-extent")!;

    act(() => {
      mapCmdBus.push({
        command: "zoom-to",
        args: { bbox: [-122.5, 37.7, -122.3, 37.85] },
      });
    });

    // No second source add — the existing source's data is swapped (one extent
    // at a time, v0.1). setData carries the NEW bbox's first corner.
    expect(
      m.addSource.mock.calls.filter(
        (c) => (c as MockCallArgs)[0] === "grace2-analysis-extent",
      ).length,
    ).toBe(0);
    expect(setData).toHaveBeenCalled();
    const swapped = setData.mock.calls[setData.mock.calls.length - 1]![0] as {
      geometry: { coordinates: number[][][] };
    };
    expect(swapped.geometry.coordinates[0]![0]).toEqual([-122.5, 37.7]);
  });

  it("self-heals a half-built extent: re-adds a missing line layer when the source already exists", () => {
    const mapCmdBus = makeMapCmdBus();
    render(
      <MapView
        subscribeMapCommand={mapCmdBus.subscribe as MapCommandSubscribeFunc}
      />,
    );
    const m = lastMapMock!;

    act(() => {
      mapCmdBus.push({
        command: "zoom-to",
        args: { bbox: [-81.91, 26.55, -81.75, 26.69] },
      });
    });

    // Simulate a half-built prior attempt: the source + fill survived but the
    // line layer is gone (a throw between the two addLayer calls).
    m._addedLayers.delete("grace2-analysis-extent-line");
    m.addLayer.mockClear();

    act(() => {
      mapCmdBus.push({
        command: "zoom-to",
        args: { bbox: [-81.91, 26.55, -81.75, 26.69] },
      });
    });

    // Only the MISSING line layer is re-added; the present fill is not re-added.
    const reAdded = m.addLayer.mock.calls.map(
      (c) => (c[0] as { id: string }).id,
    );
    expect(reAdded).toContain("grace2-analysis-extent-line");
    expect(reAdded).not.toContain("grace2-analysis-extent-fill");
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

  // job-0171: regression coverage for the malformed-URL family the live
  // "Show me radar over America" repro surfaced
  // (reports/inflight/job-0171-engine-20260608/evidence/radar_diag.json:41).
  it("uses '?' as the separator when the base URL has no query string", async () => {
    const { buildWmsTileUrl } = await import("./Map");
    const url = buildWmsTileUrl(
      "https://mesonet.agron.iastate.edu/cgi-bin/wms/nexrad/n0r.cgi",
      "nexrad_n0r",
    );
    // The first param-separator after the .cgi must be '?', not '&'. The
    // pre-job-0171 code produced `…n0r.cgi&SERVICE=…` which is malformed.
    expect(url).toMatch(/n0r\.cgi\?SERVICE=WMS/);
  });

  it("synthesises LAYERS= from style_preset when the base URL lacks one", async () => {
    const { buildWmsTileUrl } = await import("./Map");
    const url = buildWmsTileUrl(
      "https://mesonet.agron.iastate.edu/cgi-bin/wms/nexrad/n0r.cgi",
      "nexrad_n0r",
    );
    // The preset map shim recovers the EPSG:3857-compatible LAYERS value
    // from `nexrad_n0r`. The Iowa Mesonet WMS exposes Web-Mercator-projected
    // tiles under the legacy `-900913` suffix (EPSG:3857 in its older
    // EPSG-code form). See evidence/iowa_capabilities_audit.txt.
    expect(url).toContain("LAYERS=nexrad-n0r-900913");
  });

  it("warns when a WMS URL has neither LAYERS= nor a known style_preset", async () => {
    const { buildWmsTileUrl } = await import("./Map");
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    buildWmsTileUrl("https://example.com/wms", "unknown_preset_xyz");
    expect(warnSpy).toHaveBeenCalled();
    const msg = String(warnSpy.mock.calls[0]?.[0] ?? "");
    expect(msg).toMatch(/OQ-0171-WMS-URL-CONTRACT/);
    warnSpy.mockRestore();
  });

  it("uses '&' as the separator when the base URL already has a query string", async () => {
    const { buildWmsTileUrl } = await import("./Map");
    const url = buildWmsTileUrl(
      "https://qgis.example.com/ogc/wms?MAP=/mnt/qgs/x.qgs&LAYERS=flood-demo",
    );
    // Existing QGIS Server pattern must still produce a single '?' followed
    // by '&'-separated params.
    const qmCount = (url.match(/\?/g) ?? []).length;
    expect(qmCount).toBe(1);
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

  it("re-arms once('idle') when the idle callback fires while style is STILL not loaded (job-0258 live-probe finding)", () => {
    // Live repro: with theme=dark, applyTheme mutates the style inside the
    // same idle dispatch that runs applyLatest, so isStyleLoaded() is false
    // again when applyLatest fires. Pre-fix, applyLatest bailed WITHOUT
    // re-arming and the layer batch was silently dropped until the next
    // session-state push.
    const sessionBus = makeSessionBus();

    render(
      <MapView
        subscribeSessionState={sessionBus.subscribe as (cb: SessionStateSubscriber) => () => void}
      />,
    );

    const m = lastMapMock!;
    m.isStyleLoaded.mockReturnValue(false);

    act(() => {
      sessionBus.push({ loaded_layers: [makeWireLayer("flood-demo")] });
    });

    const idleCallsBefore = m.once.mock.calls.filter((c) => (c as MockCallArgs)[0] === "idle");
    expect(idleCallsBefore.length).toBeGreaterThan(0);
    const firstIdle = idleCallsBefore[idleCallsBefore.length - 1] as MockCallArgs;

    // Fire the idle callback while the style is STILL not loaded.
    act(() => {
      (firstIdle[1] as () => void)();
    });
    expect(m.addSource).not.toHaveBeenCalled();
    // The fix: a NEW once('idle') registration must exist.
    const idleCallsAfter = m.once.mock.calls.filter((c) => (c as MockCallArgs)[0] === "idle");
    expect(idleCallsAfter.length).toBeGreaterThan(idleCallsBefore.length);

    // Style settles → the re-armed callback applies the batch.
    m.isStyleLoaded.mockReturnValue(true);
    const rearmed = idleCallsAfter[idleCallsAfter.length - 1] as MockCallArgs;
    act(() => {
      (rearmed[1] as () => void)();
    });
    expect(m.addSource).toHaveBeenCalledOnce();
    expect((m.addSource.mock.calls[0] as MockCallArgs)[0]).toBe("flood-demo");
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

  // --- F84 — Case-switch / Case-exit must drop POLYGON (WDPA) vectors ------- //
  //
  // ROOT-CAUSE REGRESSION: a polygon vector (e.g. WDPA protected areas) is
  // painted by registerVectorOnMap as TWO MapLibre layers per geojson source —
  // `${id}` (fill) + `${id}-outline` (line). The pre-F84 reconcile removed only
  // `${id}` then called removeSource(id), which THROWS in real MapLibre because
  // `${id}-outline` still references the source; the uncaught throw aborted the
  // whole removal loop and the polygon persisted across Cases (the bug). The fix
  // removes EVERY group member before the source.

  it("F84: removes BOTH the fill AND the -outline sublayer (+source) when a polygon vector drops from session-state", async () => {
    const fc = {
      type: "FeatureCollection",
      features: [
        {
          type: "Feature",
          geometry: {
            type: "Polygon",
            coordinates: [[[-81.0, 26.0], [-81.1, 26.0], [-81.1, 26.1], [-81.0, 26.1], [-81.0, 26.0]]],
          },
          properties: { name_eng: "Big Cypress National Preserve", desig_eng: "National Preserve" },
        },
      ],
    };
    vi.spyOn(global, "fetch").mockResolvedValue(makeFetchResponse(fc));

    const sessionBus = makeSessionBus();
    render(<MapView subscribeSessionState={sessionBus.subscribe as (cb: SessionStateSubscriber) => () => void} />);

    act(() => {
      sessionBus.push({
        loaded_layers: [
          makeWireVectorLayer("wdpa-big-cypress", "https://ex.com/wdpa.geojson", { style_preset: "wdpa_polygon" }),
        ],
      });
    });
    await new Promise((r) => setTimeout(r, 0));

    const m = lastMapMock!;
    // Both group members are on the map: the fill (`id`) AND the line (`id-outline`).
    expect(m._addedLayers.has("wdpa-big-cypress")).toBe(true);
    expect(m._addedLayers.has("wdpa-big-cypress-outline")).toBe(true);

    // Case switch / exit: the WDPA layer is no longer in the new set.
    act(() => {
      sessionBus.push({ loaded_layers: [] });
    });

    // EVERY group member is removed (not just the fill) so the source can go.
    expect(m.removeLayer).toHaveBeenCalledWith("wdpa-big-cypress");
    expect(m.removeLayer).toHaveBeenCalledWith("wdpa-big-cypress-outline");
    expect(m.removeSource).toHaveBeenCalledWith("wdpa-big-cypress");
    // Nothing belonging to the layer is left painted on the map.
    expect(m._addedLayers.has("wdpa-big-cypress")).toBe(false);
    expect(m._addedLayers.has("wdpa-big-cypress-outline")).toBe(false);
    expect(m._addedSources.has("wdpa-big-cypress")).toBe(false);
  });

  it("F84: removeLayer(outline) runs BEFORE removeSource (real MapLibre rejects removing a referenced source)", async () => {
    const fc = {
      type: "FeatureCollection",
      features: [
        {
          type: "Feature",
          geometry: {
            type: "Polygon",
            coordinates: [[[-81.0, 26.0], [-81.1, 26.0], [-81.1, 26.1], [-81.0, 26.1], [-81.0, 26.0]]],
          },
          properties: { name_eng: "Reserve" },
        },
      ],
    };
    vi.spyOn(global, "fetch").mockResolvedValue(makeFetchResponse(fc));

    const sessionBus = makeSessionBus();
    render(<MapView subscribeSessionState={sessionBus.subscribe as (cb: SessionStateSubscriber) => () => void} />);

    act(() => {
      sessionBus.push({
        loaded_layers: [makeWireVectorLayer("wdpa", "https://ex.com/wdpa.geojson", { style_preset: "wdpa_polygon" })],
      });
    });
    await new Promise((r) => setTimeout(r, 0));

    const m = lastMapMock!;
    // Make removeSource model real MapLibre: throw if any layer of this source
    // still exists. With the fix, both layers are removed first, so this never
    // throws; pre-fix the outline lingered and this would throw → abort.
    const order: string[] = [];
    m.removeLayer.mockImplementation((id: string) => {
      order.push(`layer:${id}`);
      m._addedLayers.delete(id);
    });
    m.removeSource.mockImplementation((id: string) => {
      // A real source remove fails while ANY layer of the group still exists.
      if (m._addedLayers.has(id) || m._addedLayers.has(`${id}-outline`)) {
        throw new Error("Source can't be removed while layer is using it");
      }
      order.push(`source:${id}`);
      m._addedSources.delete(id);
    });

    act(() => {
      sessionBus.push({ loaded_layers: [] });
    });

    // Both layers removed, THEN the source — and the source removal succeeded
    // (it appears in `order`), proving no throw aborted the loop.
    expect(order).toContain("layer:wdpa");
    expect(order).toContain("layer:wdpa-outline");
    expect(order).toContain("source:wdpa");
    expect(order.indexOf("source:wdpa")).toBeGreaterThan(order.indexOf("layer:wdpa-outline"));
    expect(m._addedSources.has("wdpa")).toBe(false);
  });

  it("F84: an EMPTY loaded_layers set removes ALL overlays — raster AND polygon vector (fresh slate)", async () => {
    const fc = {
      type: "FeatureCollection",
      features: [
        {
          type: "Feature",
          geometry: {
            type: "Polygon",
            coordinates: [[[-81.0, 26.0], [-81.1, 26.0], [-81.1, 26.1], [-81.0, 26.1], [-81.0, 26.0]]],
          },
          properties: { name_eng: "Reserve" },
        },
      ],
    };
    vi.spyOn(global, "fetch").mockResolvedValue(makeFetchResponse(fc));

    const sessionBus = makeSessionBus();
    render(<MapView subscribeSessionState={sessionBus.subscribe as (cb: SessionStateSubscriber) => () => void} />);

    act(() => {
      sessionBus.push({
        loaded_layers: [
          { layer_id: "flood-demo", name: "Flood depth", layer_type: "raster", uri: "https://qgis.example.com/wms?LAYERS=flood-demo", visible: true },
          makeWireVectorLayer("wdpa", "https://ex.com/wdpa.geojson", { style_preset: "wdpa_polygon" }),
        ],
      });
    });
    await new Promise((r) => setTimeout(r, 0));

    const m = lastMapMock!;
    expect(m._addedLayers.has("flood-demo")).toBe(true);
    expect(m._addedLayers.has("wdpa")).toBe(true);
    expect(m._addedLayers.has("wdpa-outline")).toBe(true);

    // Exiting to Cases root pushes loaded_layers:[] (App.tsx null-branch).
    act(() => {
      sessionBus.push({ loaded_layers: [] });
    });

    // The raster overlay AND every vector group member + source are gone. The
    // basemap layers (qgis-basemap / osm-fallback-basemap) are NOT tracked in
    // addedSourceIds, so they remain — the map keeps its basemap, just no data.
    expect(m._addedLayers.has("flood-demo")).toBe(false);
    expect(m._addedSources.has("flood-demo")).toBe(false);
    expect(m._addedLayers.has("wdpa")).toBe(false);
    expect(m._addedLayers.has("wdpa-outline")).toBe(false);
    expect(m._addedSources.has("wdpa")).toBe(false);
    // Basemap survives the fresh slate.
    expect(m._addedLayers.has("qgis-basemap")).toBe(true);
  });

  it("F84: switching Cases removes the previous Case's polygon vector while adding the new Case's layer", async () => {
    const polyFc = {
      type: "FeatureCollection",
      features: [
        {
          type: "Feature",
          geometry: {
            type: "Polygon",
            coordinates: [[[-81.0, 26.0], [-81.1, 26.0], [-81.1, 26.1], [-81.0, 26.1], [-81.0, 26.0]]],
          },
          properties: { name_eng: "Old Case Reserve" },
        },
      ],
    };
    const ptFc = {
      type: "FeatureCollection",
      features: [{ type: "Feature", geometry: { type: "Point", coordinates: [-100, 40] }, properties: {} }],
    };
    vi.spyOn(global, "fetch")
      .mockResolvedValueOnce(makeFetchResponse(polyFc))
      .mockResolvedValueOnce(makeFetchResponse(ptFc));

    const sessionBus = makeSessionBus();
    render(<MapView subscribeSessionState={sessionBus.subscribe as (cb: SessionStateSubscriber) => () => void} />);

    // Case A: a WDPA polygon vector.
    act(() => {
      sessionBus.push({
        loaded_layers: [makeWireVectorLayer("wdpa-old", "https://ex.com/old.geojson", { style_preset: "wdpa_polygon" })],
      });
    });
    await new Promise((r) => setTimeout(r, 0));

    const m = lastMapMock!;
    expect(m._addedLayers.has("wdpa-old")).toBe(true);
    expect(m._addedLayers.has("wdpa-old-outline")).toBe(true);

    // Switch to Case B (replace-not-reconcile): a different vector, no wdpa-old.
    act(() => {
      sessionBus.push({
        loaded_layers: [makeWireVectorLayer("species-new", "https://ex.com/new.geojson")],
      });
    });
    await new Promise((r) => setTimeout(r, 0));

    // Old Case's polygon (fill + outline + source) is gone; new Case's layer in.
    expect(m.removeLayer).toHaveBeenCalledWith("wdpa-old");
    expect(m.removeLayer).toHaveBeenCalledWith("wdpa-old-outline");
    expect(m.removeSource).toHaveBeenCalledWith("wdpa-old");
    expect(m._addedLayers.has("wdpa-old")).toBe(false);
    expect(m._addedLayers.has("wdpa-old-outline")).toBe(false);
    expect(m._addedSources.has("wdpa-old")).toBe(false);
    expect(m._addedLayers.has("species-new")).toBe(true);
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

// --- inline GeoJSON path (job-0175) ------------------------------------ //

describe("addVectorLayer — inline GeoJSON (job-0175)", () => {
  function makeMockMap() {
    return {
      addSource: vi.fn(),
      addLayer: vi.fn(),
      isStyleLoaded: vi.fn().mockReturnValue(true),
    } as unknown as Parameters<typeof addVectorLayer>[0];
  }

  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders from inline_geojson without calling fetch (gs:// uri bypassed)", async () => {
    const fc = {
      type: "FeatureCollection",
      features: [
        {
          type: "Feature",
          geometry: {
            type: "Polygon",
            coordinates: [[[-100, 30], [-99, 30], [-99, 31], [-100, 31], [-100, 30]]],
          },
          properties: { event: "Flood Warning" },
        },
      ],
    };
    const fetchSpy = vi.spyOn(global, "fetch").mockResolvedValue(makeFetchResponse({}));

    const m = makeMockMap();
    const fetchGen = { current: new Map<string, number>([["nws-conus-all", 1]]) };
    const geomKinds = { current: new Map() };
    const addedIds = { current: new Set<string>(["nws-conus-all"]) };

    await addVectorLayer(
      m,
      {
        layer_id: "nws-conus-all",
        uri: "gs://grace-2-hazard-prod-cache/cache/dynamic-1h/nws_alerts_conus/abc.fgb",
        style_preset: "nws_alerts",
        inline_geojson: fc,
      },
      1,
      fetchGen,
      geomKinds,
      addedIds,
    );

    expect(fetchSpy).not.toHaveBeenCalled();
    const addSource = (m as unknown as { addSource: ReturnType<typeof vi.fn> }).addSource;
    expect(addSource).toHaveBeenCalled();
    const sourceArgs = addSource.mock.calls[0]!;
    expect(sourceArgs[0]).toBe("nws-conus-all");
    const sourceDef = sourceArgs[1] as { type: string; data: { type: string } };
    expect(sourceDef.type).toBe("geojson");
    expect(sourceDef.data.type).toBe("FeatureCollection");
    const addLayer = (m as unknown as { addLayer: ReturnType<typeof vi.fn> }).addLayer;
    expect(addLayer).toHaveBeenCalled();
    const layerDef = addLayer.mock.calls.find((c) => ((c as MockCallArgs)[0] as { id: string }).id === "nws-conus-all")?.[0] as { type: string };
    expect(layerDef.type).toBe("fill");
  });

  it("falls back to URI fetch when inline_geojson is absent", async () => {
    const fc = {
      type: "FeatureCollection",
      features: [
        { type: "Feature", geometry: { type: "Point", coordinates: [-81, 26] }, properties: {} },
      ],
    };
    const fetchSpy = vi.spyOn(global, "fetch").mockResolvedValue(makeFetchResponse(fc));

    const m = makeMockMap();
    const fetchGen = { current: new Map<string, number>([["lyr-https", 1]]) };
    const geomKinds = { current: new Map() };
    const addedIds = { current: new Set<string>(["lyr-https"]) };

    await addVectorLayer(
      m,
      { layer_id: "lyr-https", uri: "https://example.com/data.geojson" },
      1,
      fetchGen,
      geomKinds,
      addedIds,
    );

    expect(fetchSpy).toHaveBeenCalledWith("https://example.com/data.geojson");
    const addSource = (m as unknown as { addSource: ReturnType<typeof vi.fn> }).addSource;
    expect(addSource).toHaveBeenCalled();
  });

  it("logs + releases slot when inline_geojson is malformed", async () => {
    const fetchSpy = vi.spyOn(global, "fetch").mockResolvedValue(makeFetchResponse({}));
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});

    const m = makeMockMap();
    const fetchGen = { current: new Map<string, number>([["bad-inline", 1]]) };
    const geomKinds = { current: new Map() };
    const addedIds = { current: new Set<string>(["bad-inline"]) };

    await addVectorLayer(
      m,
      {
        layer_id: "bad-inline",
        uri: "gs://bucket/key.fgb",
        inline_geojson: { not_a: "feature_collection" },
      },
      1,
      fetchGen,
      geomKinds,
      addedIds,
    );

    expect(fetchSpy).not.toHaveBeenCalled();
    expect((m as unknown as { addSource: ReturnType<typeof vi.fn> }).addSource).not.toHaveBeenCalled();
    expect(warnSpy).toHaveBeenCalled();
    expect(addedIds.current.has("bad-inline")).toBe(false);
    warnSpy.mockRestore();
  });
});

// --- job-0258: layer-control wiring (LAYER CONTROLS DEAD root-cause fix) --- //
//
// Root cause: LayerPanel's user controls dispatched ONLY to its local reducer
// ("M3 local intent" stubs) and Map.tsx had (a) no handler for the
// set-layer-opacity / set-layer-visibility / set-layer-order map-commands and
// (b) no moveLayer call anywhere. These tests pin the new contract:
//   1. The exported apply helpers address the whole MapLibre layer group.
//   2. MapView applies the three layer-control map-commands from the bus.
//   3. END-TO-END: a LayerPanel slider change reaches the MapLibre instance
//      when both components share the App bus (the exact wiring App.tsx uses).

import {
  applyLayerOpacity,
  applyLayerVisibility,
  applyLayerOrder,
  layerGroupMemberIds,
  drawAnalysisExtent,
  clearAnalysisExtent,
  computeBboxBottomAnchor,
} from "./Map";
import { fireEvent, screen } from "@testing-library/react";
import { LayerPanel, createLayerPanelBus } from "./LayerPanel";
import type { ProjectLayerSummary, SessionStatePayload, MapCommandPayload } from "./contracts";

/** Minimal fake map for the pure helpers — existence-set driven. */
function makeHelperMap(existing: string[]) {
  const layers = new Set(existing);
  return {
    getLayer: vi.fn((id: string) => (layers.has(id) ? { id } : undefined)),
    setPaintProperty: vi.fn(),
    setLayoutProperty: vi.fn(),
    moveLayer: vi.fn(),
  } as unknown as import("maplibre-gl").Map & {
    setPaintProperty: ReturnType<typeof vi.fn>;
    setLayoutProperty: ReturnType<typeof vi.fn>;
    moveLayer: ReturnType<typeof vi.fn>;
  };
}

describe("layer-control helpers (job-0258)", () => {
  it("layerGroupMemberIds returns existing members bottom-to-top", () => {
    const m = makeHelperMap(["pts", "pts-clusters", "pts-cluster-count"]);
    expect(layerGroupMemberIds(m, "pts")).toEqual([
      "pts-clusters",
      "pts-cluster-count",
      "pts",
    ]);
    const m2 = makeHelperMap(["poly", "poly-outline"]);
    expect(layerGroupMemberIds(m2, "poly")).toEqual(["poly", "poly-outline"]);
    const m3 = makeHelperMap(["raster-1"]);
    expect(layerGroupMemberIds(m3, "raster-1")).toEqual(["raster-1"]);
  });

  it("applyLayerOpacity raster fallback sets raster-opacity", () => {
    const m = makeHelperMap(["flood-demo"]);
    applyLayerOpacity(m, "flood-demo", 0.25, undefined, null);
    expect(m.setPaintProperty).toHaveBeenCalledWith("flood-demo", "raster-opacity", 0.25);
  });

  it("applyLayerOpacity polygon covers fill AND the -outline sublayer", () => {
    const m = makeHelperMap(["poly", "poly-outline"]);
    applyLayerOpacity(m, "poly", 0.5, "polygon", null);
    // 0.5 × POLYGON_FILL_OPACITY (0.4) = 0.2
    expect(m.setPaintProperty).toHaveBeenCalledWith("poly", "fill-opacity", 0.2);
    expect(m.setPaintProperty).toHaveBeenCalledWith("poly-outline", "line-opacity", 0.5 * 0.6);
  });

  it("applyLayerOpacity dense-point covers cluster sublayers", () => {
    const m = makeHelperMap(["pts", "pts-clusters", "pts-cluster-count"]);
    applyLayerOpacity(m, "pts", 0.6, "point", null);
    expect(m.setPaintProperty).toHaveBeenCalledWith("pts", "circle-opacity", 0.6);
    expect(m.setPaintProperty).toHaveBeenCalledWith("pts", "circle-stroke-opacity", 0.6);
    expect(m.setPaintProperty).toHaveBeenCalledWith("pts-clusters", "circle-opacity", 0.6 * 0.85);
    expect(m.setPaintProperty).toHaveBeenCalledWith("pts-cluster-count", "text-opacity", 0.6);
  });

  it("applyLayerOpacity no-ops when the base layer is absent (mid-fetch race)", () => {
    const m = makeHelperMap([]);
    applyLayerOpacity(m, "ghost", 0.5, undefined, null);
    expect(m.setPaintProperty).not.toHaveBeenCalled();
  });

  it("applyLayerVisibility flips layout visibility on every group member", () => {
    const m = makeHelperMap(["poly", "poly-outline"]);
    applyLayerVisibility(m, "poly", false);
    expect(m.setLayoutProperty).toHaveBeenCalledWith("poly", "visibility", "none");
    expect(m.setLayoutProperty).toHaveBeenCalledWith("poly-outline", "visibility", "none");
    applyLayerVisibility(m, "poly", true);
    expect(m.setLayoutProperty).toHaveBeenCalledWith("poly", "visibility", "visible");
    expect(m.setLayoutProperty).toHaveBeenCalledWith("poly-outline", "visibility", "visible");
  });

  it("applyLayerOrder moves groups bottom-first so first id ends up on top", () => {
    const m = makeHelperMap(["a", "b", "b-outline"]);
    // Panel order (top-first): a above b.
    applyLayerOrder(m, ["a", "b"]);
    // moveLayer(id) pulls to top — so b's group moves first, a last.
    expect(m.moveLayer.mock.calls.map((c) => c[0])).toEqual(["b", "b-outline", "a"]);
  });
});

// --- analysis-extent rectangle (job-0294) -------------------------------- //

/** Fake map that tracks geojson source add + setData and layer adds.
 *  AWS-migration hardening (bbox track): drawAnalysisExtent now existence-
 *  guards each addLayer via getLayer, so the fake must track layers too. */
function makeExtentMap() {
  const sources = new Map<string, { setData: ReturnType<typeof vi.fn> }>();
  const layers = new Set<string>();
  return {
    sources,
    layers,
    getSource: vi.fn((id: string) => sources.get(id)),
    getLayer: vi.fn((id: string) => (layers.has(id) ? { id } : undefined)),
    addSource: vi.fn((id: string) => {
      sources.set(id, { setData: vi.fn() });
    }),
    addLayer: vi.fn((def: { id: string }) => {
      layers.add(def.id);
    }),
    removeLayer: vi.fn((id: string) => {
      layers.delete(id);
    }),
    removeSource: vi.fn((id: string) => {
      sources.delete(id);
    }),
  } as unknown as import("maplibre-gl").Map & {
    sources: Map<string, { setData: ReturnType<typeof vi.fn> }>;
    layers: Set<string>;
    getSource: ReturnType<typeof vi.fn>;
    getLayer: ReturnType<typeof vi.fn>;
    addSource: ReturnType<typeof vi.fn>;
    addLayer: ReturnType<typeof vi.fn>;
    removeLayer: ReturnType<typeof vi.fn>;
    removeSource: ReturnType<typeof vi.fn>;
  };
}

describe("drawAnalysisExtent (job-0294)", () => {
  const BBOX: [number, number, number, number] = [-105.3, 39.95, -105.2, 40.05];

  it("adds the geojson source + a dashed line layer ONLY (no fill) on first call (job-0321 F40)", () => {
    const m = makeExtentMap();
    drawAnalysisExtent(m, BBOX);
    expect(m.addSource).toHaveBeenCalledOnce();
    expect(m.addSource.mock.calls[0]![0]).toBe("grace2-analysis-extent");
    // job-0321 (F40) — OUTLINE-ONLY: the translucent fill layer is NOT added;
    // only the dashed outline. (The fill tinted everything beneath the AOI.)
    const layerIds = m.addLayer.mock.calls.map((c) => (c[0] as { id: string }).id);
    expect(layerIds).toEqual(["grace2-analysis-extent-line"]);
    // The outline is a dashed accent line.
    const lineSpec = m.addLayer.mock.calls[0]![0] as {
      type: string;
      paint: Record<string, unknown>;
    };
    expect(lineSpec.type).toBe("line");
    expect(lineSpec.paint["line-dasharray"]).toEqual([3, 2]);
  });

  it("builds a closed polygon ring from the bbox corners (no computed numbers)", () => {
    const m = makeExtentMap();
    drawAnalysisExtent(m, BBOX);
    const sourceSpec = m.addSource.mock.calls[0]![1] as {
      data: { geometry: { coordinates: number[][][] } };
    };
    const ring = sourceSpec.data.geometry.coordinates[0]!;
    const [minLon, minLat, maxLon, maxLat] = BBOX;
    expect(ring).toEqual([
      [minLon, minLat],
      [maxLon, minLat],
      [maxLon, maxLat],
      [minLon, maxLat],
      [minLon, minLat],
    ]);
  });

  it("REPLACES on a second bbox via setData (one extent at a time, v0.1)", () => {
    const m = makeExtentMap();
    drawAnalysisExtent(m, BBOX);
    expect(m.addSource).toHaveBeenCalledOnce();
    // job-0321 (F40) — OUTLINE-ONLY: a single (line) layer is added now.
    expect(m.addLayer).toHaveBeenCalledTimes(1);

    const NEXT: [number, number, number, number] = [-122.5, 37.7, -122.3, 37.85];
    drawAnalysisExtent(m, NEXT);
    // No second source / layer adds — the existing source's data is swapped.
    expect(m.addSource).toHaveBeenCalledOnce();
    expect(m.addLayer).toHaveBeenCalledTimes(1);
    const src = m.sources.get("grace2-analysis-extent")!;
    expect(src.setData).toHaveBeenCalledOnce();
    const swapped = src.setData.mock.calls[0]![0] as {
      geometry: { coordinates: number[][][] };
    };
    expect(swapped.geometry.coordinates[0]![0]).toEqual([NEXT[0], NEXT[1]]);
  });

  it("self-heals a half-built extent: source present but line layer missing → re-adds the line", () => {
    const m = makeExtentMap();
    drawAnalysisExtent(m, BBOX);
    // Simulate a prior attempt that threw mid-mutation: the source survived but
    // the dashed line layer did not. (The live failure mode the old
    // early-return-on-source-exists could never recover from.) job-0321 (F40):
    // only the line layer exists in the outline-only world, so deleting it is
    // the half-built case.
    m.layers.delete("grace2-analysis-extent-line");
    m.addLayer.mockClear();

    drawAnalysisExtent(m, BBOX);

    // setData replaced the data; the MISSING line layer was re-added.
    const src = m.sources.get("grace2-analysis-extent")!;
    expect(src.setData).toHaveBeenCalled();
    const reAdded = m.addLayer.mock.calls.map((c) => (c[0] as { id: string }).id);
    expect(reAdded).toEqual(["grace2-analysis-extent-line"]);
  });

  it("never adds the translucent fill layer (job-0321 F40 outline-only)", () => {
    const m = makeExtentMap();
    drawAnalysisExtent(m, BBOX);
    const layerIds = m.addLayer.mock.calls.map((c) => (c[0] as { id: string }).id);
    expect(layerIds).not.toContain("grace2-analysis-extent-fill");
    expect(m.layers.has("grace2-analysis-extent-fill")).toBe(false);
  });

  it("is idempotent when the line layer already exists (no-op re-add, data swapped)", () => {
    const m = makeExtentMap();
    drawAnalysisExtent(m, BBOX);
    m.addLayer.mockClear();
    drawAnalysisExtent(m, BBOX);
    // Source + line layer intact → no addLayer, just a setData replace.
    expect(m.addLayer).not.toHaveBeenCalled();
    expect(m.sources.get("grace2-analysis-extent")!.setData).toHaveBeenCalled();
  });
});

describe("clearAnalysisExtent (ux-batch-1 F14)", () => {
  const BBOX: [number, number, number, number] = [-105.3, 39.95, -105.2, 40.05];

  it("removes the line layer then the source (inverse of drawAnalysisExtent)", () => {
    const m = makeExtentMap();
    drawAnalysisExtent(m, BBOX);
    // job-0321 (F40) — OUTLINE-ONLY: the fill is never drawn; only the line.
    expect(m.layers.has("grace2-analysis-extent-fill")).toBe(false);
    expect(m.layers.has("grace2-analysis-extent-line")).toBe(true);
    expect(m.sources.has("grace2-analysis-extent")).toBe(true);

    clearAnalysisExtent(m);

    // The fill removal guard stays (stale-style cleanup) but is a no-op here.
    expect(m.removeLayer).not.toHaveBeenCalledWith("grace2-analysis-extent-fill");
    expect(m.removeLayer).toHaveBeenCalledWith("grace2-analysis-extent-line");
    expect(m.removeSource).toHaveBeenCalledWith("grace2-analysis-extent");
    expect(m.layers.size).toBe(0);
    expect(m.sources.size).toBe(0);
  });

  it("still tears down a STALE fill left over from a previous (filled) style (guard kept)", () => {
    // job-0321 (F40) — the fill LAYER ID constant + the clearAnalysisExtent
    // removal guard are KEPT so a fill drawn by an older app version (before the
    // outline-only switch) lingering in a persisted/restyled map still clears.
    const m = makeExtentMap();
    drawAnalysisExtent(m, BBOX); // adds source + line only
    m.layers.add("grace2-analysis-extent-fill"); // simulate a stale fill

    clearAnalysisExtent(m);

    expect(m.removeLayer).toHaveBeenCalledWith("grace2-analysis-extent-fill");
    expect(m.removeLayer).toHaveBeenCalledWith("grace2-analysis-extent-line");
    expect(m.removeSource).toHaveBeenCalledWith("grace2-analysis-extent");
  });

  it("removes layers BEFORE the source (MapLibre rejects removing a referenced source)", () => {
    const m = makeExtentMap();
    drawAnalysisExtent(m, BBOX);
    const order: string[] = [];
    m.removeLayer.mockImplementation((id: string) => {
      order.push(`layer:${id}`);
      m.layers.delete(id);
    });
    m.removeSource.mockImplementation((id: string) => {
      order.push(`source:${id}`);
      m.sources.delete(id);
    });

    clearAnalysisExtent(m);

    expect(order[order.length - 1]).toBe("source:grace2-analysis-extent");
    expect(order.slice(0, -1).every((s) => s.startsWith("layer:"))).toBe(true);
  });

  it("is a no-op when no extent exists (idempotent, partial-state tolerant)", () => {
    const m = makeExtentMap();
    clearAnalysisExtent(m);
    expect(m.removeLayer).not.toHaveBeenCalled();
    expect(m.removeSource).not.toHaveBeenCalled();
  });

  it("clears a half-built extent: source present, line layer missing (job-0321 F40)", () => {
    const m = makeExtentMap();
    drawAnalysisExtent(m, BBOX);
    m.layers.delete("grace2-analysis-extent-line"); // simulate partial build
    m.removeLayer.mockClear();

    clearAnalysisExtent(m);

    // Outline-only: no fill was ever drawn and the line is gone, so neither
    // layer-removal fires; the source is still removed (partial-state tolerant).
    expect(m.removeLayer).not.toHaveBeenCalledWith("grace2-analysis-extent-fill");
    expect(m.removeLayer).not.toHaveBeenCalledWith("grace2-analysis-extent-line");
    expect(m.removeSource).toHaveBeenCalledWith("grace2-analysis-extent");
  });
});

// --- job-0321 (F43) — legend bbox anchor projection --------------------- //

describe("computeBboxBottomAnchor (job-0321 F43)", () => {
  const BBOX: [number, number, number, number] = [-100, 30, -90, 40];

  /** Map stub exposing project() + getCanvas() for the anchor helper. */
  function makeProjectMap(opts?: {
    project?: (ll: [number, number]) => { x: number; y: number };
    canvas?: { clientWidth: number; clientHeight: number } | null;
  }) {
    const project =
      opts?.project ??
      ((ll: [number, number]) => ({ x: (ll[0] + 180) * 4, y: (90 - ll[1]) * 4 }));
    const canvas =
      opts && "canvas" in opts ? opts.canvas : { clientWidth: 1000, clientHeight: 800 };
    return {
      project: vi.fn(project),
      getCanvas: vi.fn(() => canvas),
    } as unknown as import("maplibre-gl").Map;
  }

  it("returns the bottom-edge MIDPOINT x and the LOWER (max-y) of the two bottom corners", () => {
    // bl = project([minLon, minLat]) ; br = project([maxLon, minLat]).
    // With the default linear stub both corners share the same y (minLat),
    // so top = that y and left = midpoint of the two x's.
    const m = makeProjectMap();
    const anchor = computeBboxBottomAnchor(m, BBOX);
    const bl = { x: (-100 + 180) * 4, y: (90 - 30) * 4 }; // 320, 240
    const br = { x: (-90 + 180) * 4, y: (90 - 30) * 4 }; // 360, 240
    expect(anchor).toEqual({ left: (bl.x + br.x) / 2, top: Math.max(bl.y, br.y) });
  });

  it("anchors at the LOWER corner when the two bottom corners project to different y (skew)", () => {
    const m = makeProjectMap({
      project: (ll) =>
        ll[0] === -100 ? { x: 100, y: 500 } : { x: 200, y: 560 },
    });
    const anchor = computeBboxBottomAnchor(m, BBOX);
    expect(anchor).toEqual({ left: 150, top: 560 });
  });

  it("returns null when the projected midpoint is OFF-SCREEN (legend falls back)", () => {
    // Canvas is 1000×800; force the midpoint x beyond the right edge.
    const m = makeProjectMap({
      project: () => ({ x: 5000, y: 400 }),
      canvas: { clientWidth: 1000, clientHeight: 800 },
    });
    expect(computeBboxBottomAnchor(m, BBOX)).toBeNull();
  });

  it("returns null when the projected midpoint is ABOVE the top edge (off-screen)", () => {
    const m = makeProjectMap({
      project: () => ({ x: 400, y: -50 }),
      canvas: { clientWidth: 1000, clientHeight: 800 },
    });
    expect(computeBboxBottomAnchor(m, BBOX)).toBeNull();
  });

  it("still returns an anchor when the canvas size is unknown (no off-screen test)", () => {
    const m = makeProjectMap({
      project: () => ({ x: 9999, y: 9999 }),
      canvas: null,
    });
    // No canvas → cannot run the off-screen test → returns the raw anchor.
    expect(computeBboxBottomAnchor(m, BBOX)).toEqual({ left: 9999, top: 9999 });
  });

  it("returns null (never throws) when project() throws", () => {
    const m = {
      project: vi.fn(() => {
        throw new Error("map removed");
      }),
      getCanvas: vi.fn(() => null),
    } as unknown as import("maplibre-gl").Map;
    expect(computeBboxBottomAnchor(m, BBOX)).toBeNull();
  });
});

describe("MapView — legend lives inside the map container (job-0321 F43)", () => {
  beforeEach(() => {
    lastMapMock = null;
  });

  it("renders the legend INSIDE the grace2-map container when a raster layer with a known preset loads", () => {
    const sessionBus = makeSessionBus();
    const { getByTestId } = render(
      <MapView
        subscribeSessionState={
          sessionBus.subscribe as (cb: SessionStateSubscriber) => () => void
        }
      />,
    );

    act(() => {
      sessionBus.push({
        loaded_layers: [
          {
            layer_id: "flood-1",
            name: "Max flood depth",
            layer_type: "raster",
            uri: "https://qgis.example.com/wms?LAYERS=flood-1",
            visible: true,
            style_preset: "continuous_flood_depth",
          },
        ],
      });
    });

    const mapEl = getByTestId("grace2-map");
    const legend = getByTestId("grace2-layer-legend");
    // The legend is a DESCENDANT of the map container (so it can anchor to the
    // AOI box) — previously it lived in App.tsx as a map sibling.
    expect(mapEl.contains(legend)).toBe(true);
  });

  it("hides the legend when no loaded layer has a known preset", () => {
    const sessionBus = makeSessionBus();
    const { queryByTestId } = render(
      <MapView
        subscribeSessionState={
          sessionBus.subscribe as (cb: SessionStateSubscriber) => () => void
        }
      />,
    );

    act(() => {
      sessionBus.push({
        loaded_layers: [
          {
            layer_id: "pts-1",
            name: "Observations",
            layer_type: "vector",
            uri: "https://example.com/pts.geojson",
            visible: true,
          },
        ],
      });
    });

    expect(queryByTestId("grace2-layer-legend")).toBeNull();
  });

  it("orders legend layers top-of-stack-first (z_index desc) so the topmost preset wins", () => {
    const sessionBus = makeSessionBus();
    const { getByTestId } = render(
      <MapView
        subscribeSessionState={
          sessionBus.subscribe as (cb: SessionStateSubscriber) => () => void
        }
      />,
    );

    act(() => {
      sessionBus.push({
        loaded_layers: [
          // Emitted bottom-first; the higher z_index (top) carries the preset
          // the legend must pick. With z_index-desc ordering the legend renders.
          {
            layer_id: "bottom",
            name: "Bottom",
            layer_type: "raster",
            uri: "https://qgis.example.com/wms?LAYERS=bottom",
            visible: true,
            style_preset: null,
            z_index: 1,
          },
          {
            layer_id: "top",
            name: "Top flood",
            layer_type: "raster",
            uri: "https://qgis.example.com/wms?LAYERS=top",
            visible: true,
            style_preset: "continuous_flood_depth",
            z_index: 2,
          },
        ] as never,
      });
    });

    // The topmost (z_index 2) layer has the preset → legend shows its title.
    expect(getByTestId("layer-legend-title")).toHaveTextContent(
      "Max flood depth (m)",
    );
  });
});

describe("MapView — onAoiScreenRectChange lift (scrubber AOI snap)", () => {
  beforeEach(() => {
    lastMapMock = null;
  });

  // The rect recompute is rAF-throttled (schedule()); happy-dom provides rAF,
  // so we flush a frame (+ microtasks) after the act() that sets aoiBbox.
  async function flushRaf(): Promise<void> {
    await act(async () => {
      await new Promise<void>((resolve) => {
        if (typeof requestAnimationFrame === "function") {
          requestAnimationFrame(() => resolve());
        } else {
          setTimeout(resolve, 0);
        }
      });
    });
  }

  it("fires onAoiScreenRectChange with the projected rect after a zoom-to sets the AOI bbox", async () => {
    const mapCmdBus = makeMapCmdBus();
    const onRect = vi.fn();
    render(
      <MapView
        subscribeMapCommand={mapCmdBus.subscribe as MapCommandSubscribeFunc}
        onAoiScreenRectChange={onRect}
      />,
    );
    // Initial: no AOI → null (the guard fires once for the initial null state
    // only if it changes; since the ref starts null and legendRect starts null,
    // no spurious null call is expected — assert nothing fired yet).
    expect(onRect).not.toHaveBeenCalled();

    act(() => {
      mapCmdBus.push({
        command: "zoom-to",
        args: { bbox: [-100, 30, -90, 40] },
      });
    });
    await flushRaf();

    // The mock project() maps [lon,lat] -> { x:(lon+180)*2, y:(90-lat)*2 }, so
    // the min/max box over the four corners is left/right from lon, top/bottom
    // from lat. lon in [-100,-90] -> x in [160,180]; lat in [30,40] -> y in
    // [100,120]. Assert the callback got that rect.
    expect(onRect).toHaveBeenCalled();
    const last = onRect.mock.calls[onRect.mock.calls.length - 1]![0];
    expect(last).toEqual({ left: 160, top: 100, right: 180, bottom: 120 });
  });

  it("only fires again when the rect actually CHANGES (change-detection guard)", async () => {
    const mapCmdBus = makeMapCmdBus();
    const onRect = vi.fn();
    render(
      <MapView
        subscribeMapCommand={mapCmdBus.subscribe as MapCommandSubscribeFunc}
        onAoiScreenRectChange={onRect}
      />,
    );

    act(() => {
      mapCmdBus.push({ command: "zoom-to", args: { bbox: [-100, 30, -90, 40] } });
    });
    await flushRaf();
    const callsAfterFirst = onRect.mock.calls.length;
    expect(callsAfterFirst).toBeGreaterThanOrEqual(1);

    // Re-push the SAME bbox: the rect is identical → the guard suppresses it.
    act(() => {
      mapCmdBus.push({ command: "zoom-to", args: { bbox: [-100, 30, -90, 40] } });
    });
    await flushRaf();
    expect(onRect.mock.calls.length).toBe(callsAfterFirst);

    // A DIFFERENT bbox → a fresh call with the new rect.
    act(() => {
      mapCmdBus.push({ command: "zoom-to", args: { bbox: [-80, 20, -70, 25] } });
    });
    await flushRaf();
    expect(onRect.mock.calls.length).toBe(callsAfterFirst + 1);
    const last = onRect.mock.calls[onRect.mock.calls.length - 1]![0];
    // lon [-80,-70] -> x [200,220]; lat [20,25] -> y [130,140].
    expect(last).toEqual({ left: 200, top: 130, right: 220, bottom: 140 });
  });
});

describe("MapView — map-command layer controls (job-0258)", () => {
  beforeEach(() => {
    lastMapMock = null;
  });

  function renderWithLayers(ids: string[]) {
    const sessionBus = makeSessionBus();
    const cmdBus = makeMapCmdBus();
    render(
      <MapView
        subscribeSessionState={sessionBus.subscribe as (cb: SessionStateSubscriber) => () => void}
        subscribeMapCommand={cmdBus.subscribe as unknown as MapCommandSubscribeFunc}
      />,
    );
    act(() => {
      sessionBus.push({ loaded_layers: ids.map((id) => makeWireLayer(id)) });
    });
    return { sessionBus, cmdBus, m: lastMapMock! };
  }

  it("set-layer-opacity reaches setPaintProperty(raster-opacity) on the map", () => {
    const { cmdBus, m } = renderWithLayers(["flood-demo"]);
    m.setPaintProperty.mockClear();

    act(() => {
      cmdBus.push({ command: "set-layer-opacity", layer_id: "flood-demo", opacity: 0.3 } as unknown as ZoomToCommand);
    });

    expect(m.setPaintProperty).toHaveBeenCalledWith("flood-demo", "raster-opacity", 0.3);
  });

  it("set-layer-opacity clamps out-of-range values to 0..1", () => {
    const { cmdBus, m } = renderWithLayers(["flood-demo"]);
    m.setPaintProperty.mockClear();

    act(() => {
      cmdBus.push({ command: "set-layer-opacity", layer_id: "flood-demo", opacity: 7 } as unknown as ZoomToCommand);
    });

    expect(m.setPaintProperty).toHaveBeenCalledWith("flood-demo", "raster-opacity", 1);
  });

  it("set-layer-visibility reaches setLayoutProperty(visibility) on the map", () => {
    const { cmdBus, m } = renderWithLayers(["flood-demo"]);
    m.setLayoutProperty.mockClear();

    act(() => {
      cmdBus.push({ command: "set-layer-visibility", layer_id: "flood-demo", visible: false } as unknown as ZoomToCommand);
    });

    expect(m.setLayoutProperty).toHaveBeenCalledWith("flood-demo", "visibility", "none");
  });

  it("set-layer-order re-stacks via moveLayer, bottom-first", () => {
    const { cmdBus, m } = renderWithLayers(["layer-a", "layer-b"]);
    m.moveLayer.mockClear();

    // Panel/top-first order: layer-a on top of layer-b.
    act(() => {
      cmdBus.push({ command: "set-layer-order", layer_ids: ["layer-a", "layer-b"] } as unknown as ZoomToCommand);
    });

    expect(m.moveLayer.mock.calls.map((c: MockCallArgs) => c[0])).toEqual(["layer-b", "layer-a"]);
  });
});

describe("LayerPanel ↔ MapView end-to-end over the App bus (job-0258)", () => {
  beforeEach(() => {
    lastMapMock = null;
  });

  function makePanelLayer(id: string, z = 1): ProjectLayerSummary {
    return {
      layer_id: id,
      name: `Layer ${id}`,
      layer_type: "raster",
      uri: `https://qgis.example.com/wms?LAYERS=${id}`,
      visible: true,
      opacity: 1,
      z_index: z,
    };
  }

  /** Mirrors App.tsx wiring: shared bus + onMapCommand={bus.pushMapCommand}. */
  function renderShell(layers: ProjectLayerSummary[]) {
    const bus = createLayerPanelBus();
    render(
      <>
        <MapView
          subscribeSessionState={bus.subscribeSessionState as unknown as (cb: SessionStateSubscriber) => () => void}
          subscribeMapCommand={bus.subscribeMapCommand as unknown as MapCommandSubscribeFunc}
        />
        <LayerPanel
          subscribeSessionState={bus.subscribeSessionState}
          subscribeMapCommand={bus.subscribeMapCommand}
          initialLayers={layers}
          onMapCommand={bus.pushMapCommand as (cmd: MapCommandPayload) => void}
        />
      </>,
    );
    act(() => {
      bus.pushSessionState({ loaded_layers: layers } as SessionStatePayload);
    });
    return { bus, m: lastMapMock! };
  }

  it("moving the opacity slider updates the MapLibre paint property", () => {
    const { m } = renderShell([makePanelLayer("flood-demo")]);
    m.setPaintProperty.mockClear();

    const slider = screen.getByTestId("layer-opacity");
    fireEvent.change(slider, { target: { value: "0.3" } });

    expect(m.setPaintProperty).toHaveBeenCalledWith("flood-demo", "raster-opacity", 0.3);
  });

  it("toggling the visibility checkbox updates the MapLibre layout property", () => {
    const { m } = renderShell([makePanelLayer("flood-demo")]);
    m.setLayoutProperty.mockClear();

    const checkbox = screen.getByTestId("layer-visibility");
    fireEvent.click(checkbox); // initial visible:true → toggles off

    expect(m.setLayoutProperty).toHaveBeenCalledWith("flood-demo", "visibility", "none");
  });

  it("a set-layer-order push (drag-reorder emission path) re-stacks the map", () => {
    const { bus, m } = renderShell([
      makePanelLayer("layer-a", 2),
      makePanelLayer("layer-b", 1),
    ]);
    m.moveLayer.mockClear();

    // Same payload LayerPanel.onDragEnd emits after the user drags layer-b
    // above layer-a (top-first list). jsdom cannot synthesize the dnd-kit
    // pointer drag reliably; the Playwright evidence run covers the real
    // mouse drag. This pins the bus→map half of the path.
    act(() => {
      bus.pushMapCommand({ command: "set-layer-order", layer_ids: ["layer-b", "layer-a"] });
    });

    expect(m.moveLayer.mock.calls.map((c: MockCallArgs) => c[0])).toEqual(["layer-a", "layer-b"]);
  });
});
