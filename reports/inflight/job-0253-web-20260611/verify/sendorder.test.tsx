// CONTRACT-lens probe (job-0253 verify): what is the ACTUAL on-wire envelope
// send order on connect? The agent's AUTH_REQUIRED gate (server.py:4060-4063)
// rejects the connection if the FIRST non-auth-token envelope arrives before a
// valid auth-token. ws.ts:674-684 sends session-resume THEN auth-token. This
// probe captures the real ordering deterministically.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { GraceWs, __test_resetSessionHub, clearAnonymousUserId, type WsHandlers } from "../../../../../web/src/ws";

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

function openedSockets(): WebSocket[] {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return ((window as any).__webSockets as WebSocket[] | undefined) ?? [];
}
function lastOpenedSocket(): WebSocket | null {
  const s = openedSockets();
  return s.length === 0 ? null : s[s.length - 1]!;
}

describe("CONTRACT probe — connect envelope send order", () => {
  beforeEach(() => {
    __test_resetSessionHub();
    clearAnonymousUserId();
    try { window.localStorage.clear(); } catch { /* ignore */ }
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (window as any).__webSockets = [];
  });

  it("captures the literal ordering of envelope.type values on connect", async () => {
    // A real token is available so the auth-token branch sends a non-empty token.
    const idTokenGetter = vi.fn().mockResolvedValue("real.jwt.token");
    const ws = new GraceWs("ws://localhost:8765", makeHandlers({ idTokenGetter }));
    ws.connect();
    const socket = lastOpenedSocket();
    expect(socket).not.toBeNull();
    const sendSpy = vi.spyOn(socket!, "send");

    // Fire the "open" handler — this triggers the session-resume + auth-token sends.
    socket!.dispatchEvent(new Event("open"));
    // Let the async maybeSendAuthToken (await getter()) settle.
    await Promise.resolve();
    await Promise.resolve();
    await new Promise((r) => setTimeout(r, 0));

    const types = sendSpy.mock.calls.map((c) => {
      try { return JSON.parse(String(c[0])).type as string; } catch { return "<unparseable>"; }
    });
    // Emit the captured order for the verdict.
    // eslint-disable-next-line no-console
    console.log("CONTRACT-PROBE send order:", JSON.stringify(types));
    expect(types.length).toBeGreaterThan(0);

    const resumeIdx = types.indexOf("session-resume");
    const authIdx = types.indexOf("auth-token");
    // eslint-disable-next-line no-console
    console.log(`CONTRACT-PROBE session-resume@${resumeIdx} auth-token@${authIdx}`);
    // Record both for the verdict — do NOT assert a particular order here; the
    // probe's purpose is to OBSERVE.
    expect(resumeIdx).toBeGreaterThanOrEqual(0);
    expect(authIdx).toBeGreaterThanOrEqual(0);
    ws.close();
  });
});
