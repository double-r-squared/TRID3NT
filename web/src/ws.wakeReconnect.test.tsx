// GRACE-2 web — ws.ts reconnect / wake-signal wiring tests.
//
// sleep/wake STAGE 2 (NATE 2026-06-18) — NEVER AUTO-WAKE. The reconnect loop no
// longer POSTs the wake endpoint. This file verifies the STAGE-2 contract:
//   1. When a socket drops and the close handler schedules a reconnect,
//      `onWakeNeeded(attempt)` fires with an incrementing attempt counter — but
//      NO wake POST is issued (the box is woken ONLY by an explicit user tap).
//   2. The attempt counter increments across consecutive failed reconnects and
//      resets after a successful (re)open.
//   3. The reconnect backoff still revives a fresh socket (the loop is intact).
//   4. `reportWakeState()` delegates a REPORT-ONLY GET to the injected waker
//      (asleep detection) and NEVER POSTs / wakes the box.

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

describe("GraceWs — reconnect signal + report-only wake (sleep/wake STAGE 2)", () => {
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

  it("fires onWakeNeeded(attempt) on a scheduled reconnect but NEVER auto-POSTs wake", async () => {
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

    // The close handler scheduled a reconnect → the UI signal fired…
    expect(onWakeNeeded).toHaveBeenCalledTimes(1);
    expect(onWakeNeeded).toHaveBeenLastCalledWith(1);
    // …but NO wake POST was issued (never auto-wake). Flush microtasks to be sure.
    await Promise.resolve();
    await Promise.resolve();
    expect(fetchFn).not.toHaveBeenCalled();

    ws.close();
  });

  it("increments the attempt counter across consecutive failed reconnects (no POST)", () => {
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

    // Advance the backoff so the scheduled reconnect opens a fresh socket,
    // then drop THAT one too.
    vi.advanceTimersByTime(600);
    dropSocket(ws);
    expect(onWakeNeeded).toHaveBeenLastCalledWith(2);

    // Still no wake POST across either reconnect.
    expect(fetchFn).not.toHaveBeenCalled();

    ws.close();
  });

  it("still revives a fresh socket on the backoff reconnect", () => {
    vi.stubEnv("VITE_GRACE2_WAKE_URL", "https://explicit.example/wake");
    const fetchFn = vi.fn(async () => ({ ok: true, status: 200 }));
    const waker = new AgentWaker({ fetchFn });
    const ws = new GraceWs(
      "ws://localhost:8765",
      makeHandlers(),
      { waker },
    );
    ws.connect();
    const first = instanceSocket(ws);
    dropSocket(ws);

    // Backoff fires → a brand-new socket is opened.
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

  it("reportWakeState() delegates a REPORT-ONLY GET (asleep detection; never POSTs)", async () => {
    vi.stubEnv("VITE_GRACE2_WAKE_URL", "https://explicit.example/wake");
    const fetchFn = vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ state: "stopped" }),
    }));
    const waker = new AgentWaker({ fetchFn });
    const ws = new GraceWs("ws://localhost:8765", makeHandlers(), { waker });

    const state = await ws.reportWakeState();
    expect(state).toBe("stopped");
    expect(fetchFn).toHaveBeenCalledTimes(1);
    expect(fetchFn).toHaveBeenCalledWith(
      "https://explicit.example/wake",
      expect.objectContaining({ method: "GET" }),
    );
    // The probe must NEVER POST.
    expect(fetchFn).not.toHaveBeenCalledWith(
      "https://explicit.example/wake",
      expect.objectContaining({ method: "POST" }),
    );

    ws.close();
  });
});
