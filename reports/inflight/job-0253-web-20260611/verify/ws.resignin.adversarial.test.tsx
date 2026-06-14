// CORRECTNESS-LENS probe: does a 4401 episode WEDGE the socket such that a
// subsequent re-sign-in cannot reconnect WITHOUT an explicit connect()?
//
// This models the App.tsx reality: the App-level GraceWs is created in a
// useEffect whose deps do NOT include authUser/authExpired. After
// handleAuthFailure gives up the socket, nothing calls connect() again on a
// successful re-sign-in (onAuthChanged only flips authExpired=false; it never
// touches wsRef). We PROVE here that without an explicit connect(), the ws
// instance is terminal — there is no auto-reconnect path post-auth-failure.

import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  GraceWs, AUTH_FAILED_CLOSE_CODE, __test_resetSessionHub,
  clearAnonymousUserId, type WsHandlers,
} from "./ws";

function makeHandlers(overrides: Partial<WsHandlers> = {}): WsHandlers {
  return {
    onStatus: vi.fn(), onAgentChunk: vi.fn(), onPipelineState: vi.fn(),
    onSessionState: vi.fn(), onError: vi.fn(), ...overrides,
  };
}
function openedSockets(): WebSocket[] {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return ((window as any).__webSockets as WebSocket[] | undefined) ?? [];
}
function lastSocket(): WebSocket | null {
  const s = openedSockets(); return s.length === 0 ? null : s[s.length - 1]!;
}
function injectClose(ws: WebSocket, code: number): void {
  let ev: Event;
  try { ev = new CloseEvent("close", { code }); }
  catch { ev = new Event("close"); Object.defineProperty(ev, "code", { value: code }); }
  ws.dispatchEvent(ev);
}

describe("ADVERSARIAL: post-4401 wedge / re-sign-in reconnect", () => {
  beforeEach(() => {
    __test_resetSessionHub();
    clearAnonymousUserId();
    try { window.localStorage.clear(); } catch { /* ignore */ }
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (window as any).__webSockets = [];
    vi.useFakeTimers();
  });

  it("after auth-expired, NO socket opens until an explicit connect() (proves the App must re-connect itself)", async () => {
    const onAuthExpired = vi.fn();
    const idTokenGetter = vi.fn().mockResolvedValue(null);
    const ws = new GraceWs("ws://localhost:8765", makeHandlers({ onAuthExpired, idTokenGetter }));
    ws.connect();
    const socket = lastSocket();
    if (!socket) { ws.close(); return; }
    injectClose(socket, AUTH_FAILED_CLOSE_CODE);
    await vi.runAllTimersAsync();
    expect(onAuthExpired).toHaveBeenCalledOnce();
    const countAfterExpiry = openedSockets().length;

    // Simulate "time passes, user re-signs-in" WITHOUT anyone calling connect().
    // Advance all timers generously — there must be NO scheduled reconnect.
    await vi.advanceTimersByTimeAsync(60_000);
    expect(openedSockets().length).toBe(countAfterExpiry); // wedged: no auto-reconnect

    // Only an explicit connect() (which App.tsx does NOT call on re-sign-in)
    // re-opens the socket.
    ws.connect();
    await vi.runAllTimersAsync();
    expect(openedSockets().length).toBe(countAfterExpiry + 1);
    ws.close();
  });
});
