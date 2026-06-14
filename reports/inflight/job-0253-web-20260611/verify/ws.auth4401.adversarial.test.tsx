// CORRECTNESS-LENS adversarial tests for job-0253 ws.ts 4401 handling
// (panel-authored). Attacks:
//   1. refresh→reject→refresh LOOP must be bounded at exactly ONE retry even
//      when a fresh token is returned EVERY time and rejected EVERY time.
//   2. error-envelope AUTH_FAILED latch must NOT leak across a fresh connect():
//      after re-connect, a NORMAL close must reconnect again (latch reset).
//   3. a non-4401 close must reach reconnect even immediately after an
//      AUTH_FAILED error that was NOT followed by a close on that socket... is
//      impossible to disentangle once latched — so we verify the documented
//      contract: once AUTH_FAILED is latched on a connection, ALL closes on
//      THAT connection are auth-failures (latch is connection-scoped, reset by
//      connect()).
//   4. count getIdToken invocations to prove the forceRefresh is invoked at
//      most once per failure episode.

import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  GraceWs,
  AUTH_FAILED_CLOSE_CODE,
  __test_resetSessionHub,
  clearAnonymousUserId,
  type WsHandlers,
} from "./ws";
import type { ErrorPayload } from "./contracts";

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

function makeEnvelope(type: string, payload: unknown): string {
  return JSON.stringify({
    type, id: "01ABCDEFGHJKMNPQRSTVWX0001", ts: "2026-06-11T00:00:00.000Z",
    session_id: "01ABCDEFGHJKMNPQRSTVWX0002", payload,
  });
}
function openedSockets(): WebSocket[] {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return ((window as any).__webSockets as WebSocket[] | undefined) ?? [];
}
function lastSocket(): WebSocket | null {
  const s = openedSockets();
  return s.length === 0 ? null : s[s.length - 1]!;
}
function injectClose(ws: WebSocket, code: number): void {
  let ev: Event;
  try { ev = new CloseEvent("close", { code }); }
  catch { ev = new Event("close"); Object.defineProperty(ev, "code", { value: code }); }
  ws.dispatchEvent(ev);
}
function injectMessage(ws: WebSocket, raw: string): void {
  ws.dispatchEvent(new MessageEvent("message", { data: raw }));
}
const AUTH_FAILED_ERR: ErrorPayload = {
  error_code: "AUTH_FAILED", message: "Authentication required", retryable: false,
};

describe("ADVERSARIAL ws.ts 4401 (panel)", () => {
  beforeEach(() => {
    __test_resetSessionHub();
    clearAnonymousUserId();
    try { window.localStorage.clear(); } catch { /* ignore */ }
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (window as any).__webSockets = [];
    vi.useFakeTimers();
  });

  it("BOUND: fresh-token-every-time + reject-every-time terminates after ONE retry (no infinite loop)", async () => {
    const onAuthExpired = vi.fn();
    // Always returns a fresh token — a naive impl would refresh→reconnect→reject
    // forever. The authRefreshAttempted guard must cap it at exactly one retry.
    const idTokenGetter = vi.fn().mockResolvedValue("always.fresh.but.rejected");
    const ws = new GraceWs("ws://localhost:8765", makeHandlers({ onAuthExpired, idTokenGetter }));
    ws.connect();
    let socket = lastSocket();
    if (!socket) { ws.close(); return; }
    const countAtStart = openedSockets().length;

    // Reject up to 6 times; each rejection re-runs the close handler.
    for (let i = 0; i < 6; i++) {
      socket = lastSocket();
      if (!socket) break;
      injectClose(socket, AUTH_FAILED_CLOSE_CODE);
      await vi.runAllTimersAsync();
    }

    // EXACTLY one reconnect happened (countAtStart + 1), then it gave up.
    expect(openedSockets().length).toBe(countAtStart + 1);
    // getIdToken (forceRefresh) called exactly once across the whole episode.
    expect(idTokenGetter).toHaveBeenCalledTimes(1);
    // auth-expired surfaced exactly once.
    expect(onAuthExpired).toHaveBeenCalledOnce();
    ws.close();
  });

  it("LATCH RESET: AUTH_FAILED then a fresh connect() must allow a normal close to reconnect", async () => {
    const onAuthExpired = vi.fn();
    // No fresh token -> first episode ends in auth-expired (latch set, then on
    // connect() it is cleared).
    const idTokenGetter = vi.fn().mockResolvedValue(null);
    const ws = new GraceWs("ws://localhost:8765", makeHandlers({ onAuthExpired, idTokenGetter }));
    ws.connect();
    let socket = lastSocket();
    if (!socket) { ws.close(); return; }

    // Episode 1: AUTH_FAILED error latches, then close -> auth-expired.
    injectMessage(socket, makeEnvelope("error", AUTH_FAILED_ERR));
    injectClose(socket, AUTH_FAILED_CLOSE_CODE);
    await vi.runAllTimersAsync();
    expect(onAuthExpired).toHaveBeenCalledOnce();

    // User re-signs-in and the App calls connect() again (fresh attempt).
    const countBeforeReconnect = openedSockets().length;
    ws.connect();
    await vi.runAllTimersAsync();
    socket = lastSocket();
    expect(socket).not.toBeNull();
    // A new socket was opened by connect().
    expect(openedSockets().length).toBe(countBeforeReconnect + 1);

    // Now a NORMAL transient drop on this fresh connection MUST reconnect —
    // proving the authFailed latch did NOT leak across the connect().
    const countBeforeDrop = openedSockets().length;
    injectClose(socket!, 1006);
    await vi.runAllTimersAsync();
    expect(openedSockets().length).toBeGreaterThan(countBeforeDrop);
    ws.close();
  });

  it("NON-AUTH ERROR does NOT latch: a generic error envelope then a normal close still reconnects", async () => {
    const onAuthExpired = vi.fn();
    const ws = new GraceWs("ws://localhost:8765", makeHandlers({ onAuthExpired }));
    ws.connect();
    const socket = lastSocket();
    if (!socket) { ws.close(); return; }
    const countBefore = openedSockets().length;

    // A non-AUTH error envelope (e.g. a tool error) must not set the latch.
    injectMessage(socket, makeEnvelope("error", {
      error_code: "TOOL_FAILED", message: "boom", retryable: true,
    } as ErrorPayload));
    injectClose(socket, 1006);
    await vi.runAllTimersAsync();

    expect(openedSockets().length).toBeGreaterThan(countBefore);
    expect(onAuthExpired).not.toHaveBeenCalled();
    ws.close();
  });

  it("code-less AUTH_FAILED close-code 4401 is also honoured even WITHOUT a prior error envelope", async () => {
    const onAuthExpired = vi.fn();
    const idTokenGetter = vi.fn().mockResolvedValue(null);
    const ws = new GraceWs("ws://localhost:8765", makeHandlers({ onAuthExpired, idTokenGetter }));
    ws.connect();
    const socket = lastSocket();
    if (!socket) { ws.close(); return; }
    const countBefore = openedSockets().length;
    injectClose(socket, AUTH_FAILED_CLOSE_CODE);
    await vi.runAllTimersAsync();
    expect(openedSockets().length).toBe(countBefore); // no reconnect
    expect(onAuthExpired).toHaveBeenCalledOnce();
    ws.close();
  });

  it("user-initiated close() after auth-failure does NOT fire onAuthExpired or reconnect", async () => {
    const onAuthExpired = vi.fn();
    const idTokenGetter = vi.fn().mockResolvedValue(null);
    const ws = new GraceWs("ws://localhost:8765", makeHandlers({ onAuthExpired, idTokenGetter }));
    ws.connect();
    const socket = lastSocket();
    if (!socket) { ws.close(); return; }
    // Episode: auth-expired surfaces.
    injectClose(socket, AUTH_FAILED_CLOSE_CODE);
    await vi.runAllTimersAsync();
    const expiredCalls = onAuthExpired.mock.calls.length;
    const countBefore = openedSockets().length;
    // User closes the component (unmount). Must be quiet.
    ws.close();
    await vi.runAllTimersAsync();
    expect(onAuthExpired.mock.calls.length).toBe(expiredCalls);
    expect(openedSockets().length).toBe(countBefore);
  });
});
