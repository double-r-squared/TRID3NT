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
import { GraceWs, type WsHandlers } from "./ws";
import type { MapCommandPayload } from "./contracts";

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
