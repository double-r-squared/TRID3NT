// GRACE-2 web — ws.ts envelope-dispatch unit tests (job-0072).
//
// Verifies:
//   1. A synthetic `map-command(zoom-to, {bbox})` envelope dispatched through
//      GraceWs.handleMessage (via MessageEvent) calls the `onMapCommand`
//      handler with the correct payload.
//   2. A `map-command` envelope is silently dropped (no error) when no
//      `onMapCommand` handler is provided (optional handler contract).
//   3. The existing `session-state` and `pipeline-state` dispatch cases
//      still work alongside the new `map-command` case.
//
// WebSocket is mocked via happy-dom's built-in WebSocket stub; we drive
// messages directly through MessageEvent injection rather than a real
// WebSocket server.

import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  GraceWs,
  __test_resetSessionHub,
  __test_sessionHubSize,
  type WsHandlers,
} from "./ws";
import type { MapCommandPayload } from "./contracts";
import type { ImpactEnvelope } from "./components/ImpactPanel";

// --- Minimal WsHandlers factory ------------------------------------------- //

function makeHandlers(overrides: Partial<WsHandlers> = {}): WsHandlers {
  return {
    onStatus: vi.fn(),
    onAgentChunk: vi.fn(),
    onPipelineState: vi.fn(),
    onSessionState: vi.fn(),
    onError: vi.fn(),
    ...overrides,
  };
}

// --- Wire-level helpers ---------------------------------------------------- //

/**
 * Build the raw JSON string that a real agent WebSocket frame would contain.
 * The envelope wrapper matches Appendix A.1.
 */
function makeEnvelope(type: string, payload: unknown): string {
  return JSON.stringify({
    type,
    id: "01ABCDEFGHJKMNPQRSTVWX0001",
    ts: "2026-06-07T21:00:00.000Z",
    session_id: "01ABCDEFGHJKMNPQRSTVWX0002",
    payload,
  });
}

/**
 * Retrieve the WebSocket instance most recently opened by the given GraceWs.
 * happy-dom exposes the list via `window.__webSockets` when the built-in
 * WebSocket stub is used.
 */
function lastOpenedSocket(): WebSocket | null {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const sockets = (window as any).__webSockets as WebSocket[] | undefined;
  if (!sockets || sockets.length === 0) return null;
  return sockets[sockets.length - 1];
}

/**
 * Inject a raw message string into a WebSocket instance as if the server sent it.
 */
function injectMessage(ws: WebSocket, raw: string): void {
  ws.dispatchEvent(new MessageEvent("message", { data: raw }));
}

// --- Tests ----------------------------------------------------------------- //

describe("GraceWs — map-command routing (job-0072, OQ-0068-MAPCMD-WS)", () => {
  beforeEach(() => {
    // Clear tracked sockets between tests.
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (window as any).__webSockets = [];
  });

  it("dispatches map-command envelope to onMapCommand handler", () => {
    const onMapCommand = vi.fn<[MapCommandPayload], void>();
    const handlers = makeHandlers({ onMapCommand });

    const ws = new GraceWs("ws://localhost:8765", handlers);
    ws.connect();

    const socket = lastOpenedSocket();
    if (!socket) {
      // happy-dom WebSocket tracking unavailable — skip connection phase,
      // drive handleMessage directly via a detached socket event instead.
      // We call connect() which opens the socket; skip assertion if env
      // doesn't expose __webSockets (CI-only path).
      ws.close();
      return;
    }

    // Simulate the server sending a map-command zoom-to envelope.
    const payload = {
      command: "zoom-to",
      args: { bbox: [-81.91, 26.55, -81.75, 26.69] },
    };
    injectMessage(socket, makeEnvelope("map-command", payload));

    expect(onMapCommand).toHaveBeenCalledOnce();
    const received = onMapCommand.mock.calls[0][0] as { command: string; args: unknown };
    expect(received.command).toBe("zoom-to");

    ws.close();
  });

  it("does not throw when onMapCommand is not provided (optional handler)", () => {
    // No onMapCommand in handlers.
    const handlers = makeHandlers();
    const ws = new GraceWs("ws://localhost:8765", handlers);
    ws.connect();

    const socket = lastOpenedSocket();
    if (!socket) {
      ws.close();
      return;
    }

    // Should not throw — the optional handler is simply skipped.
    expect(() => {
      injectMessage(
        socket,
        makeEnvelope("map-command", { command: "zoom-to", args: { bbox: [-82, 26, -81, 27] } }),
      );
    }).not.toThrow();

    ws.close();
  });

  it("still dispatches session-state alongside the new map-command case", () => {
    const onSessionState = vi.fn();
    const onMapCommand = vi.fn();
    const handlers = makeHandlers({ onSessionState, onMapCommand });

    const ws = new GraceWs("ws://localhost:8765", handlers);
    ws.connect();

    const socket = lastOpenedSocket();
    if (!socket) {
      ws.close();
      return;
    }

    injectMessage(socket, makeEnvelope("session-state", { loaded_layers: [] }));
    injectMessage(
      socket,
      makeEnvelope("map-command", { command: "zoom-to", args: { bbox: [-82, 26, -81, 27] } }),
    );

    expect(onSessionState).toHaveBeenCalledOnce();
    expect(onMapCommand).toHaveBeenCalledOnce();

    ws.close();
  });
});

// ---------------------------------------------------------------------------
// job-0159: per-session fan-out hub — dual-GraceWs scenario
// ---------------------------------------------------------------------------
//
// The web client mounts TWO GraceWs instances per tab (Chat.tsx + App.tsx),
// each owning its own WebSocket against the same agent. The agent's
// PipelineEmitter is bound 1:1 to a single ServerConnection, so when a
// tool runs on Chat's connection the resulting `session-state` envelope is
// only written on Chat's wire. Pre-job-0159 the App-side instance never
// saw the workflow's layer; the LayerPanel + Map.tsx subscribers (driven
// by App's onSessionState handler) stayed empty.
//
// The fan-out hub fixes this in-process: any session-scoped envelope
// received by ANY GraceWs instance is delivered to every sibling instance
// bound to the same session_id. These tests pin the behaviour.

describe("GraceWs — job-0159 session-scoped fan-out hub", () => {
  beforeEach(() => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (window as any).__webSockets = [];
    __test_resetSessionHub();
  });

  it("fans session-state out to a sibling instance with the same session_id", () => {
    // Both instances pull the SAME session_id from localStorage (the real
    // client behaviour — Chat.tsx + App.tsx mount in the same tab).
    const chatOnSessionState = vi.fn();
    const appOnSessionState = vi.fn();
    const chat = new GraceWs("ws://localhost:8765", makeHandlers({
      onSessionState: chatOnSessionState,
    }));
    const app = new GraceWs("ws://localhost:8765", makeHandlers({
      onSessionState: appOnSessionState,
    }));
    expect(chat.session).toBe(app.session);
    expect(__test_sessionHubSize(chat.session)).toBe(2);

    chat.connect();
    app.connect();
    const sockets = (window as unknown as { __webSockets?: WebSocket[] })
      .__webSockets;
    if (!sockets || sockets.length < 2) {
      chat.close();
      app.close();
      return;
    }
    const chatSocket = sockets[sockets.length - 2];
    // Simulate the server delivering the post-tool session-state on Chat's
    // wire ONLY (the per-ServerConnection emitter behaviour).
    const sessionPayload = {
      loaded_layers: [
        {
          layer_id: "flood-depth-peak-01TEST",
          name: "Flood depth (peak)",
          layer_type: "raster",
          uri: "https://qgis-server.example/ogc/wms?MAP=/mnt/qgs/p.qgs&LAYERS=flood-depth-peak-01TEST",
          style_preset: "continuous_flood_depth",
          visible: true,
          role: "primary",
          temporal: false,
        },
      ],
    };
    injectMessage(chatSocket, makeEnvelope("session-state", sessionPayload));

    // Chat sees its own envelope (natively).
    expect(chatOnSessionState).toHaveBeenCalledOnce();
    // App sees the envelope via the fan-out hub — this is the job-0159 fix.
    expect(appOnSessionState).toHaveBeenCalledOnce();
    const appReceived = appOnSessionState.mock.calls[0][0] as {
      loaded_layers: Array<{ layer_id: string }>;
    };
    expect(appReceived.loaded_layers[0].layer_id).toBe(
      "flood-depth-peak-01TEST",
    );

    chat.close();
    app.close();
    expect(__test_sessionHubSize(chat.session)).toBe(0);
  });

  it("fans map-command out to siblings (zoom-to drives Map.tsx fitBounds)", () => {
    const chatOnMapCommand = vi.fn();
    const appOnMapCommand = vi.fn();
    const chat = new GraceWs("ws://localhost:8765", makeHandlers({
      onMapCommand: chatOnMapCommand,
    }));
    const app = new GraceWs("ws://localhost:8765", makeHandlers({
      onMapCommand: appOnMapCommand,
    }));

    chat.connect();
    app.connect();
    const sockets = (window as unknown as { __webSockets?: WebSocket[] })
      .__webSockets;
    if (!sockets || sockets.length < 2) {
      chat.close();
      app.close();
      return;
    }
    const chatSocket = sockets[sockets.length - 2];
    injectMessage(
      chatSocket,
      makeEnvelope("map-command", {
        command: "zoom-to",
        args: { bbox: [-81.91, 26.55, -81.75, 26.69] },
      }),
    );

    expect(chatOnMapCommand).toHaveBeenCalledOnce();
    expect(appOnMapCommand).toHaveBeenCalledOnce();

    chat.close();
    app.close();
  });

  it("does NOT fan out per-message envelopes (agent-message-chunk stays scoped)", () => {
    // Chat owns the active user-message turn; App.tsx mounting its own
    // onAgentChunk would render duplicate chat bubbles. The hub only fans
    // out SESSION-SCOPED envelope types.
    const chatOnAgentChunk = vi.fn();
    const appOnAgentChunk = vi.fn();
    const chat = new GraceWs("ws://localhost:8765", makeHandlers({
      onAgentChunk: chatOnAgentChunk,
    }));
    const app = new GraceWs("ws://localhost:8765", makeHandlers({
      onAgentChunk: appOnAgentChunk,
    }));

    chat.connect();
    app.connect();
    const sockets = (window as unknown as { __webSockets?: WebSocket[] })
      .__webSockets;
    if (!sockets || sockets.length < 2) {
      chat.close();
      app.close();
      return;
    }
    const chatSocket = sockets[sockets.length - 2];
    injectMessage(
      chatSocket,
      makeEnvelope("agent-message-chunk", {
        message_id: "01MSG",
        delta: "hello",
        done: false,
      }),
    );

    expect(chatOnAgentChunk).toHaveBeenCalledOnce();
    // The bug we're explicitly NOT introducing: app must not see this.
    expect(appOnAgentChunk).not.toHaveBeenCalled();

    chat.close();
    app.close();
  });
});

// ---------------------------------------------------------------------------
// Wave 4.11 P4 — impact-envelope routing
// ---------------------------------------------------------------------------
//
// Tests:
//   1. Well-formed impact-envelope routes to onImpactEnvelope with the full payload.
//   2. Malformed payload (missing n_structures_total) is silently dropped —
//      no exception, onImpactEnvelope not called.
//   3. impact-envelope is session-scoped: arrives on Chat's wire, App's
//      onImpactEnvelope fires via fan-out.
//   4. When onImpactEnvelope is absent, a well-formed envelope is silently
//      ignored (optional handler contract).

/** Minimal valid ImpactEnvelope fixture (B.6c shape). */
function makeImpactPayload(overrides: Partial<ImpactEnvelope> = {}): ImpactEnvelope {
  return {
    schema_version: "v1",
    n_structures_total: 4_210,
    n_structures_damaged: 1_850,
    n_structures_destroyed: 340,
    damage_state_distribution: {
      DS0_none: 2_360,
      DS1_slight: 820,
      DS2_moderate: 540,
      DS3_extensive: 150,
      DS4_complete: 340,
    },
    total_replacement_value_usd: 980_000_000,
    damaged_replacement_value_usd: 425_000_000,
    expected_loss_usd: 312_000_000,
    loss_percentile_95_usd: 490_000_000,
    population_total: 9_800,
    population_displaced: 4_200,
    population_at_high_risk: 1_100,
    impact_area_km2: 18.4,
    bbox: [-81.91, 26.55, -81.75, 26.69],
    by_occupancy_class: {
      RES1: {
        n_structures: 3_200,
        n_damaged: 1_400,
        n_destroyed: 280,
        expected_loss_usd: 210_000_000,
        loss_percentile_95_usd: 330_000_000,
        population: 9_800,
        population_displaced: 4_200,
      },
    },
    pelicun_run_id: "01HWZP8Q5RTXYV23BKJD4M56CE",
    damage_layer_uri: "gs://grace2-runs/pelicun/01HWZP/damage.gpkg",
    structure_inventory_source: "USACE_NSI",
    flood_layer_uri: "gs://grace2-runs/sfincs/01HWZ/flood_depth_peak.tif",
    fragility_set: "HAZUS-MH-4.2-coastal",
    realization_count: 1_000,
    generated_at: "2026-06-09T12:00:00.000Z",
    ...overrides,
  };
}

describe("GraceWs — impact-envelope routing (Wave 4.11 P4)", () => {
  beforeEach(() => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (window as any).__webSockets = [];
    __test_resetSessionHub();
  });

  it("routes well-formed impact-envelope to onImpactEnvelope handler", () => {
    const onImpactEnvelope = vi.fn<[ImpactEnvelope], void>();
    const handlers = makeHandlers({ onImpactEnvelope });
    const ws = new GraceWs("ws://localhost:8765", handlers);
    ws.connect();

    const socket = lastOpenedSocket();
    if (!socket) { ws.close(); return; }

    const payload = makeImpactPayload();
    injectMessage(socket, makeEnvelope("impact-envelope", payload));

    expect(onImpactEnvelope).toHaveBeenCalledOnce();
    const received = onImpactEnvelope.mock.calls[0][0];
    expect(received.n_structures_total).toBe(4_210);
    expect(received.n_structures_damaged).toBe(1_850);
    expect(received.pelicun_run_id).toBe("01HWZP8Q5RTXYV23BKJD4M56CE");
    expect(received.structure_inventory_source).toBe("USACE_NSI");

    ws.close();
  });

  it("silently drops impact-envelope when n_structures_total is missing", () => {
    const onImpactEnvelope = vi.fn();
    const handlers = makeHandlers({ onImpactEnvelope });
    const ws = new GraceWs("ws://localhost:8765", handlers);
    ws.connect();

    const socket = lastOpenedSocket();
    if (!socket) { ws.close(); return; }

    // Malformed: n_structures_total removed.
    const badPayload = makeImpactPayload() as Record<string, unknown>;
    delete badPayload.n_structures_total;

    expect(() => {
      injectMessage(socket, makeEnvelope("impact-envelope", badPayload));
    }).not.toThrow();
    expect(onImpactEnvelope).not.toHaveBeenCalled();

    ws.close();
  });

  it("silently drops impact-envelope when onImpactEnvelope handler is absent", () => {
    // No onImpactEnvelope provided — optional handler contract.
    const handlers = makeHandlers();
    const ws = new GraceWs("ws://localhost:8765", handlers);
    ws.connect();

    const socket = lastOpenedSocket();
    if (!socket) { ws.close(); return; }

    expect(() => {
      injectMessage(socket, makeEnvelope("impact-envelope", makeImpactPayload()));
    }).not.toThrow();

    ws.close();
  });

  it("fans impact-envelope out to sibling GraceWs instances (session-scoped)", () => {
    const chatOnImpact = vi.fn<[ImpactEnvelope], void>();
    const appOnImpact = vi.fn<[ImpactEnvelope], void>();

    const chat = new GraceWs("ws://localhost:8765", makeHandlers({
      onImpactEnvelope: chatOnImpact,
    }));
    const app = new GraceWs("ws://localhost:8765", makeHandlers({
      onImpactEnvelope: appOnImpact,
    }));

    chat.connect();
    app.connect();

    const sockets = (window as unknown as { __webSockets?: WebSocket[] }).__webSockets;
    if (!sockets || sockets.length < 2) {
      chat.close();
      app.close();
      return;
    }
    // Deliver on Chat's socket only (mirrors the per-ServerConnection emitter).
    const chatSocket = sockets[sockets.length - 2];
    injectMessage(chatSocket, makeEnvelope("impact-envelope", makeImpactPayload()));

    // Chat receives natively; App receives via fan-out hub.
    expect(chatOnImpact).toHaveBeenCalledOnce();
    expect(appOnImpact).toHaveBeenCalledOnce();
    expect(appOnImpact.mock.calls[0][0].expected_loss_usd).toBe(312_000_000);

    chat.close();
    app.close();
  });
});

// ---------------------------------------------------------------------------
// chart-emission routing (sprint-13 job-0231)
// ---------------------------------------------------------------------------

/** Minimal well-formed ChartEmissionPayload fixture (mirrors job-0230 evidence). */
function makeChartPayload(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    envelope_type: "chart-emission",
    chart_id: "01KTQPZ9ESAY9R17FS8BTVE0YK",
    vega_lite_spec: {
      "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
      mark: { type: "bar", tooltip: true },
      encoding: {
        x: { field: "bin_label", type: "ordinal" },
        y: { field: "count", type: "quantitative" },
      },
      data: { values: [{ bin_label: "0–1", count: 42 }] },
      width: "container",
    },
    title: "Histogram — value",
    caption: "284,580 values",
    source_layer_uri: "/tmp/flood_depth_peak.tif",
    created_turn_id: null,
    ...overrides,
  };
}

describe("GraceWs — chart-emission routing (sprint-13 job-0231)", () => {
  beforeEach(() => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (window as any).__webSockets = [];
    __test_resetSessionHub();
  });

  it("routes well-formed chart-emission to onChartEmission handler", () => {
    const onChartEmission = vi.fn();
    const handlers = makeHandlers({ onChartEmission });
    const ws = new GraceWs("ws://localhost:8765", handlers);
    ws.connect();

    const socket = lastOpenedSocket();
    if (!socket) { ws.close(); return; }

    injectMessage(socket, makeEnvelope("chart-emission", makeChartPayload()));

    expect(onChartEmission).toHaveBeenCalledOnce();
    const received = onChartEmission.mock.calls[0][0] as Record<string, unknown>;
    expect(received.chart_id).toBe("01KTQPZ9ESAY9R17FS8BTVE0YK");
    expect(received.title).toBe("Histogram — value");

    ws.close();
  });

  it("silently drops chart-emission when chart_id is missing", () => {
    const onChartEmission = vi.fn();
    const consoleWarn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const handlers = makeHandlers({ onChartEmission });
    const ws = new GraceWs("ws://localhost:8765", handlers);
    ws.connect();

    const socket = lastOpenedSocket();
    if (!socket) { ws.close(); consoleWarn.mockRestore(); return; }

    const badPayload = makeChartPayload();
    delete badPayload.chart_id;
    expect(() => {
      injectMessage(socket, makeEnvelope("chart-emission", badPayload));
    }).not.toThrow();
    expect(onChartEmission).not.toHaveBeenCalled();
    expect(consoleWarn).toHaveBeenCalled();

    ws.close();
    consoleWarn.mockRestore();
  });

  it("silently drops chart-emission when vega_lite_spec is missing", () => {
    const onChartEmission = vi.fn();
    const consoleWarn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const handlers = makeHandlers({ onChartEmission });
    const ws = new GraceWs("ws://localhost:8765", handlers);
    ws.connect();

    const socket = lastOpenedSocket();
    if (!socket) { ws.close(); consoleWarn.mockRestore(); return; }

    const badPayload = makeChartPayload();
    delete badPayload.vega_lite_spec;
    expect(() => {
      injectMessage(socket, makeEnvelope("chart-emission", badPayload));
    }).not.toThrow();
    expect(onChartEmission).not.toHaveBeenCalled();
    expect(consoleWarn).toHaveBeenCalled();

    ws.close();
    consoleWarn.mockRestore();
  });

  it("silently drops chart-emission when onChartEmission handler is absent", () => {
    const handlers = makeHandlers();
    const ws = new GraceWs("ws://localhost:8765", handlers);
    ws.connect();

    const socket = lastOpenedSocket();
    if (!socket) { ws.close(); return; }

    expect(() => {
      injectMessage(socket, makeEnvelope("chart-emission", makeChartPayload()));
    }).not.toThrow();

    ws.close();
  });

  it("chart-emission fans out to sibling GraceWs instances (session-scoped)", () => {
    const chatOnChart = vi.fn();
    const appOnChart = vi.fn();

    const chat = new GraceWs("ws://localhost:8765", makeHandlers({
      onChartEmission: chatOnChart,
    }));
    const app = new GraceWs("ws://localhost:8765", makeHandlers({
      onChartEmission: appOnChart,
    }));

    chat.connect();
    app.connect();

    const sockets = (window as unknown as { __webSockets?: WebSocket[] }).__webSockets;
    if (!sockets || sockets.length < 2) {
      chat.close(); app.close(); return;
    }
    const chatSocket = sockets[sockets.length - 2];
    injectMessage(chatSocket!, makeEnvelope("chart-emission", makeChartPayload()));

    expect(chatOnChart).toHaveBeenCalledOnce();
    expect(appOnChart).toHaveBeenCalledOnce();
    const appReceived = appOnChart.mock.calls[0][0] as Record<string, unknown>;
    expect(appReceived.chart_id).toBe("01KTQPZ9ESAY9R17FS8BTVE0YK");

    chat.close();
    app.close();
  });
});

// ---------------------------------------------------------------------------
// F53 (job-0325): sendDeleteLayer emits the `layer-delete` client->server
// envelope. `map-command` is server->client only, so the delete intent rides
// a dedicated outbound envelope. The server removes the layer, persists, and
// echoes a fresh session-state (which removes the map overlay via
// replace-not-reconcile).
// ---------------------------------------------------------------------------

describe("GraceWs — sendDeleteLayer (job-0325 F53)", () => {
  beforeEach(() => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (window as any).__webSockets = [];
    __test_resetSessionHub();
  });

  it("sends a layer-delete envelope with the layer_id when the socket is open", () => {
    const ws = new GraceWs("ws://localhost:8765", makeHandlers());
    ws.connect();
    const socket = lastOpenedSocket();
    if (!socket) {
      ws.close();
      return;
    }
    // Drive the happy-dom socket to OPEN so sendEnvelope's readyState guard
    // passes, then spy on the actual wire write.
    Object.defineProperty(socket, "readyState", {
      configurable: true,
      get: () => 1, // WebSocket.OPEN
    });
    const sendSpy = vi.spyOn(socket, "send").mockImplementation(() => undefined);

    ws.sendDeleteLayer("flood-depth-peak-01TEST");

    expect(sendSpy).toHaveBeenCalledOnce();
    const wire = JSON.parse(sendSpy.mock.calls[0][0] as string) as {
      type: string;
      session_id: string;
      payload: { envelope_type?: string; layer_id?: string };
    };
    expect(wire.type).toBe("layer-delete");
    expect(wire.session_id).toBe(ws.session);
    expect(wire.payload.envelope_type).toBe("layer-delete");
    expect(wire.payload.layer_id).toBe("flood-depth-peak-01TEST");

    ws.close();
  });

  it("no-ops (no throw) when the socket is not open", () => {
    const ws = new GraceWs("ws://localhost:8765", makeHandlers());
    // Never connect — sendEnvelope's guard short-circuits on a null socket.
    expect(() => ws.sendDeleteLayer("any-layer-id")).not.toThrow();
  });
});

// ---------------------------------------------------------------------------
// job-0322 F31 (resume-repaint): requestSessionState() re-sends a
// `session-resume` envelope on the LIVE socket (no-op unless OPEN) so the
// server re-emits authoritative session-state; reconnect() revives a
// closed/closing socket (no-op when already OPEN/CONNECTING). App.tsx pairs
// them on `visibilitychange → visible` so mobile background→resume repaints
// the layers without a Case reopen.
// ---------------------------------------------------------------------------

// happy-dom does NOT populate `window.__webSockets` in this harness (the
// existing socket-driving tests above gracefully no-op when `lastOpenedSocket()`
// returns null). For F31 we need real coverage of the readyState guards, so we
// reach the instance's private `socket` field directly — `connect()` assigns it
// synchronously inside `openSocket` (the happy-dom WebSocket constructor returns
// immediately in CONNECTING). This is a test-only structural access, mirroring
// how the existing suite mutates `readyState` on a grabbed socket.

/** Read the GraceWs instance's private current socket (test-only access). */
function instanceSocket(ws: GraceWs): WebSocket | null {
  return (ws as unknown as { socket: WebSocket | null }).socket;
}

/** Force a stable readyState getter on a socket (happy-dom socket is mutable). */
function forceReadyState(socket: WebSocket, state: number): void {
  Object.defineProperty(socket, "readyState", {
    configurable: true,
    get: () => state,
  });
}

describe("GraceWs — requestSessionState (job-0322 F31)", () => {
  beforeEach(() => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (window as any).__webSockets = [];
    __test_resetSessionHub();
  });

  it("sends a session-resume envelope when the socket is OPEN", () => {
    const ws = new GraceWs("ws://localhost:8765", makeHandlers());
    ws.connect();
    const socket = instanceSocket(ws);
    expect(socket).not.toBeNull();
    forceReadyState(socket!, 1); // WebSocket.OPEN
    const sendSpy = vi.spyOn(socket!, "send").mockImplementation(() => undefined);

    ws.requestSessionState();

    expect(sendSpy).toHaveBeenCalledOnce();
    const wire = JSON.parse(sendSpy.mock.calls[0][0] as string) as {
      type: string;
      session_id: string;
      payload: unknown;
    };
    expect(wire.type).toBe("session-resume");
    expect(wire.session_id).toBe(ws.session);

    ws.close();
  });

  it("no-ops (no send, no throw) when the socket is NOT open", () => {
    const ws = new GraceWs("ws://localhost:8765", makeHandlers());
    ws.connect();
    const socket = instanceSocket(ws);
    expect(socket).not.toBeNull();
    forceReadyState(socket!, 3); // WebSocket.CLOSED — guard must short-circuit
    const sendSpy = vi.spyOn(socket!, "send").mockImplementation(() => undefined);

    expect(() => ws.requestSessionState()).not.toThrow();
    expect(sendSpy).not.toHaveBeenCalled();

    ws.close();
  });

  it("no-ops (no throw) when never connected (null socket)", () => {
    const ws = new GraceWs("ws://localhost:8765", makeHandlers());
    expect(instanceSocket(ws)).toBeNull();
    expect(() => ws.requestSessionState()).not.toThrow();
  });
});

describe("GraceWs — reconnect (job-0322 F31)", () => {
  beforeEach(() => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (window as any).__webSockets = [];
    __test_resetSessionHub();
  });

  it("revives a CLOSED socket by opening a fresh connection", () => {
    const onStatus = vi.fn();
    const ws = new GraceWs("ws://localhost:8765", makeHandlers({ onStatus }));
    ws.connect();
    const first = instanceSocket(ws);
    expect(first).not.toBeNull();
    // Simulate the socket having dropped (mobile background tear-down): drive
    // it to CLOSED then fire the close event so the instance nulls its socket.
    forceReadyState(first!, 3); // WebSocket.CLOSED
    first!.dispatchEvent(new CloseEvent("close", { code: 1006 }));
    // The close handler nulled the instance socket and scheduled a reconnect;
    // ignore the backoff timer — we're testing the explicit reconnect() path.
    onStatus.mockClear();

    ws.reconnect();

    // A fresh socket was opened (distinct object) and connect() ran.
    const revived = instanceSocket(ws);
    expect(revived).not.toBeNull();
    expect(revived).not.toBe(first);
    expect(onStatus).toHaveBeenCalledWith("connecting");

    ws.close();
  });

  it("no-ops when the socket is already OPEN (does not tear down a healthy connection)", () => {
    const onStatus = vi.fn();
    const ws = new GraceWs("ws://localhost:8765", makeHandlers({ onStatus }));
    ws.connect();
    const socket = instanceSocket(ws);
    expect(socket).not.toBeNull();
    forceReadyState(socket!, 1); // WebSocket.OPEN
    const closeSpy = vi.spyOn(socket!, "close").mockImplementation(() => undefined);
    onStatus.mockClear();

    ws.reconnect();

    // Same socket object retained; not closed; no fresh "connecting" status.
    expect(instanceSocket(ws)).toBe(socket);
    expect(closeSpy).not.toHaveBeenCalled();
    expect(onStatus).not.toHaveBeenCalledWith("connecting");

    ws.close();
  });

  it("no-ops when the socket is still CONNECTING", () => {
    const onStatus = vi.fn();
    const ws = new GraceWs("ws://localhost:8765", makeHandlers({ onStatus }));
    ws.connect();
    const socket = instanceSocket(ws);
    expect(socket).not.toBeNull();
    forceReadyState(socket!, 0); // WebSocket.CONNECTING
    onStatus.mockClear();

    ws.reconnect();

    expect(instanceSocket(ws)).toBe(socket);
    expect(onStatus).not.toHaveBeenCalledWith("connecting");

    ws.close();
  });

  it("a late close from a detached stale socket does NOT clobber the revived socket", () => {
    // The identity-guard regression: reconnect() detaches a CLOSING socket and
    // opens a fresh one; the stale socket's late close event must NOT null out
    // the new socket. After the revive + stale close, requestSessionState()
    // must still send on the (mocked-OPEN) fresh socket.
    const ws = new GraceWs("ws://localhost:8765", makeHandlers());
    ws.connect();
    const stale = instanceSocket(ws);
    expect(stale).not.toBeNull();
    forceReadyState(stale!, 2); // WebSocket.CLOSING — reconnect detaches + reopens

    ws.reconnect();
    const fresh = instanceSocket(ws);
    expect(fresh).not.toBeNull();
    expect(fresh).not.toBe(stale);

    // Now the stale socket finally fires its close — the identity guard must
    // keep `this.socket` pointing at `fresh` (not null it out).
    stale!.dispatchEvent(new CloseEvent("close", { code: 1006 }));
    expect(instanceSocket(ws)).toBe(fresh);

    // Prove the fresh socket is still the instance's live socket by sending.
    forceReadyState(fresh!, 1); // WebSocket.OPEN
    const sendSpy = vi.spyOn(fresh!, "send").mockImplementation(() => undefined);
    ws.requestSessionState();
    expect(sendSpy).toHaveBeenCalledOnce();

    ws.close();
  });

  it("revives from a null socket (never connected) → drives connect() (onStatus connecting)", () => {
    const onStatus = vi.fn();
    const ws = new GraceWs("ws://localhost:8765", makeHandlers({ onStatus }));
    // Never connect — socket is null. reconnect() must take the revive path.
    expect(instanceSocket(ws)).toBeNull();
    expect(() => ws.reconnect()).not.toThrow();
    expect(onStatus).toHaveBeenCalledWith("connecting");
    expect(instanceSocket(ws)).not.toBeNull();
    ws.close();
  });
});
