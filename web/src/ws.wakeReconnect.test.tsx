// GRACE-2 web — ws.ts wake-on-reconnect wiring tests (auto-stop/wake infra,
// NATE 2026-06-17).
//
// Verifies the GraceWs reconnect loop drives the wake path:
//   1. When a socket drops and the close handler schedules a reconnect, the
//      injected AgentWaker.wake() is called (the box may be stopped → ask the
//      wake Lambda to StartInstances) AND `onWakeNeeded(attempt)` fires with an
//      incrementing attempt counter.
//   2. A failing / slow wake never wedges the reconnect loop (a fresh socket is
//      still scheduled).
//   3. A successful (re)open resets the attempt counter so the NEXT drop starts
//      from attempt 1 again.
//   4. With NO wake endpoint configured (dev/LAN), the wake POST is a no-op
//      (the AgentWaker short-circuits to "disabled") but `onWakeNeeded` STILL
//      fires (so the UI threshold logic is exercised) — though App.tsx gates the
//      overlay on wakeConfigured() so it never shows in dev.

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  GraceWs,
  __test_resetSessionHub,
  type WsHandlers,
} from "./ws";
import { AgentWaker } from "./lib/wake";

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

function instanceSocket(ws: GraceWs): WebSocket | null {
  return (ws as unknown as { socket: WebSocket | null }).socket;
}

function forceReadyState(socket: WebSocket, state: number): void {
  Object.defineProperty(socket, "readyState", {
    configurable: true,
    get: () => state,
  });
}

describe("GraceWs — wake-on-reconnect (auto-stop/wake)", () => {
  beforeEach(() => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (window as any).__webSockets = [];
    __test_resetSessionHub();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.runOnlyPendingTimers();
    vi.useRealTimers();
    vi.unstubAllEnvs();
    vi.resetModules();
    vi.restoreAllMocks();
  });

  function dropSocket(ws: GraceWs): void {
    const s = instanceSocket(ws);
    expect(s).not.toBeNull();
    forceReadyState(s!, 3); // CLOSED
    s!.dispatchEvent(new CloseEvent("close", { code: 1006 }));
  }

  it("fires AgentWaker.wake() and onWakeNeeded(attempt) when a reconnect is scheduled", async () => {
    vi.stubEnv("VITE_GRACE2_WAKE_URL", "https://explicit.example/wake");
    const fetchFn = vi.fn(async () => ({ ok: true, status: 200 }));
    const waker = new AgentWaker({ fetchFn });
    const onWakeNeeded = vi.fn();

    const ws = new GraceWs(
      "ws://localhost:8765",
      makeHandlers({ onWakeNeeded }),
      { waker },
    );
    ws.connect();
    dropSocket(ws);

    // The close handler scheduled a reconnect → wake fired + handler notified.
    expect(onWakeNeeded).toHaveBeenCalledTimes(1);
    expect(onWakeNeeded).toHaveBeenLastCalledWith(1);
    // The wake POST is async (we didn't await); flush the microtask queue.
    await Promise.resolve();
    await Promise.resolve();
    expect(fetchFn).toHaveBeenCalledTimes(1);
    expect(fetchFn).toHaveBeenCalledWith(
      "https://explicit.example/wake",
      expect.objectContaining({ method: "POST" }),
    );

    ws.close();
  });

  it("increments the attempt counter across consecutive failed reconnects", () => {
    vi.stubEnv("VITE_GRACE2_WAKE_URL", "https://explicit.example/wake");
    // Debounce so only the FIRST wake POSTs, but onWakeNeeded fires every time.
    const fetchFn = vi.fn(async () => ({ ok: true, status: 200 }));
    const waker = new AgentWaker({ fetchFn });
    const onWakeNeeded = vi.fn();

    const ws = new GraceWs(
      "ws://localhost:8765",
      makeHandlers({ onWakeNeeded }),
      { waker },
    );
    ws.connect();

    dropSocket(ws);
    expect(onWakeNeeded).toHaveBeenLastCalledWith(1);

    // Advance the backoff so the scheduled reconnect opens a fresh socket,
    // then drop THAT one too.
    vi.advanceTimersByTime(600);
    dropSocket(ws);
    expect(onWakeNeeded).toHaveBeenLastCalledWith(2);

    ws.close();
  });

  it("does not wedge the reconnect loop when the wake fetch rejects", () => {
    vi.stubEnv("VITE_GRACE2_WAKE_URL", "https://explicit.example/wake");
    const fetchFn = vi.fn(async () => {
      throw new Error("wake endpoint down");
    });
    const waker = new AgentWaker({ fetchFn });
    const ws = new GraceWs(
      "ws://localhost:8765",
      makeHandlers(),
      { waker },
    );
    ws.connect();
    const first = instanceSocket(ws);
    dropSocket(ws);

    // Backoff fires → a brand-new socket is opened despite the wake failure.
    vi.advanceTimersByTime(600);
    const revived = instanceSocket(ws);
    expect(revived).not.toBeNull();
    expect(revived).not.toBe(first);

    ws.close();
  });

  it("resets the attempt counter after a successful (re)open", () => {
    vi.stubEnv("VITE_GRACE2_WAKE_URL", "https://explicit.example/wake");
    const fetchFn = vi.fn(async () => ({ ok: true, status: 200 }));
    const waker = new AgentWaker({ fetchFn });
    const onWakeNeeded = vi.fn();
    const ws = new GraceWs(
      "ws://localhost:8765",
      makeHandlers({ onWakeNeeded }),
      { waker },
    );
    ws.connect();

    dropSocket(ws);
    expect(onWakeNeeded).toHaveBeenLastCalledWith(1);

    // Let the backoff reconnect, then mark the fresh socket OPEN (fire 'open').
    vi.advanceTimersByTime(600);
    const revived = instanceSocket(ws);
    expect(revived).not.toBeNull();
    forceReadyState(revived!, 1); // OPEN
    revived!.dispatchEvent(new Event("open"));

    // A subsequent drop starts the attempt counter over at 1.
    dropSocket(ws);
    expect(onWakeNeeded).toHaveBeenLastCalledWith(1);

    ws.close();
  });

  it("onWakeNeeded still fires with no wake endpoint, but no fetch is issued (dev/LAN)", async () => {
    // No VITE_GRACE2_WAKE_URL / PUBLIC_BASE → AgentWaker short-circuits.
    const fetchFn = vi.fn();
    const waker = new AgentWaker({ fetchFn });
    const onWakeNeeded = vi.fn();
    const ws = new GraceWs(
      "ws://localhost:8765",
      makeHandlers({ onWakeNeeded }),
      { waker },
    );
    ws.connect();
    dropSocket(ws);

    expect(onWakeNeeded).toHaveBeenCalledTimes(1);
    await Promise.resolve();
    expect(fetchFn).not.toHaveBeenCalled();

    ws.close();
  });
});
