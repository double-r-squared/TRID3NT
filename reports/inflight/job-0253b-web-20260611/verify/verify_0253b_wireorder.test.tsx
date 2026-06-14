// FRESH adversarial correctness probe for job-0253b (CORRECTNESS lens).
// Independent of the runner's ws.authwireorder.test.tsx — a hostile
// re-derivation that drives the REAL GraceWs against my own fake socket and
// asserts the on-wire frame order + no-wedge-on-getIdToken-failure +
// single-socket-per-connect + double-connect guard.
//
// Run: cd web && npx vitest run src/verify_0253b_wireorder.test.tsx

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { GraceWs, __test_resetSessionHub, clearAnonymousUserId, type WsHandlers } from "./ws";

// ── My own fake WebSocket (distinct from theirs) ───────────────────────── //
class Sock {
  static OPEN = 1;
  static CLOSED = 3;
  static all: Sock[] = [];
  readonly OPEN = 1;
  readonly CLOSED = 3;
  url: string;
  readyState = 0;
  frames: string[] = [];
  private cbs: Record<string, ((ev: unknown) => void)[]> = {};
  constructor(url: string) {
    this.url = url;
    Sock.all.push(this);
  }
  addEventListener(t: string, cb: (ev: unknown) => void): void {
    (this.cbs[t] ??= []).push(cb);
  }
  removeEventListener(): void {}
  send(data: string): void {
    if (this.readyState !== this.OPEN) throw new Error("send on non-open socket");
    this.frames.push(data);
  }
  close(): void {
    this.readyState = this.CLOSED;
  }
  fire(type: string, ev: Record<string, unknown> = {}): void {
    for (const cb of this.cbs[type] ?? []) cb({ type, ...ev });
  }
  async open(): Promise<void> {
    this.readyState = this.OPEN;
    this.fire("open");
    // Let the chained async IIFE (await maybeSendAuthToken → resume) settle.
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  }
  types(): string[] {
    return this.frames.map((f) => (JSON.parse(f) as { type: string }).type);
  }
}

let origWS: typeof WebSocket;
beforeEach(() => {
  Sock.all = [];
  __test_resetSessionHub();
  clearAnonymousUserId();
  origWS = globalThis.WebSocket;
  // @ts-expect-error — install fake
  globalThis.WebSocket = Sock;
});
afterEach(() => {
  globalThis.WebSocket = origWS;
  vi.restoreAllMocks();
});

function mkHandlers(over: Partial<WsHandlers> = {}): WsHandlers {
  return {
    onStatus: vi.fn(),
    onAgentChunk: vi.fn(),
    onPipelineState: vi.fn(),
    onSessionState: vi.fn(),
    onError: vi.fn(),
    ...over,
  } as unknown as WsHandlers;
}

describe("0253b wire order — auth-token strictly first", () => {
  it("real-token path: frame 1 = auth-token, frame 2 = session-resume", async () => {
    const ws = new GraceWs("ws://x", mkHandlers({ idTokenGetter: async () => "JWT-REAL" }));
    ws.connect();
    const s = Sock.all[0];
    await s.open();
    expect(s.types()).toEqual(["auth-token", "session-resume"]);
    // and the auth-token carries the real token, not anonymous:
    const f0 = JSON.parse(s.frames[0]) as { payload: { token: string; anonymous: boolean } };
    expect(f0.payload.token).toBe("JWT-REAL");
    expect(f0.payload.anonymous).toBe(false);
  });

  it("empty-token (anonymous) path: still auth-token THEN session-resume", async () => {
    const ws = new GraceWs("ws://x", mkHandlers({ idTokenGetter: async () => null }));
    ws.connect();
    const s = Sock.all[0];
    await s.open();
    expect(s.types()).toEqual(["auth-token", "session-resume"]);
    const f0 = JSON.parse(s.frames[0]) as { payload: { token: string; anonymous: boolean } };
    expect(f0.payload.token).toBe("");
    expect(f0.payload.anonymous).toBe(true);
  });

  it("getIdToken THROWS: resume still goes out (no wedge), auth-token first", async () => {
    const ws = new GraceWs(
      "ws://x",
      mkHandlers({
        idTokenGetter: async () => {
          throw new Error("firebase exploded");
        },
      }),
    );
    ws.connect();
    const s = Sock.all[0];
    await s.open();
    // maybeSendAuthToken swallows the throw → empty-token auth-token, then resume.
    expect(s.types()).toEqual(["auth-token", "session-resume"]);
  });

  it("getIdToken HANGS: open handler does not wedge; resume blocked until token settles, then both", async () => {
    let release!: (v: string | null) => void;
    const hang = new Promise<string | null>((res) => {
      release = res;
    });
    const ws = new GraceWs("ws://x", mkHandlers({ idTokenGetter: () => hang }));
    ws.connect();
    const s = Sock.all[0];
    s.readyState = s.OPEN;
    s.fire("open");
    await Promise.resolve();
    // While token is pending, NEITHER frame is out (auth-token awaits the token;
    // resume is chained after). This is correct: nothing wedged, nothing leaked.
    expect(s.types()).toEqual([]);
    // Now resolve the token; both frames flush in order.
    release("LATE-JWT");
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    expect(s.types()).toEqual(["auth-token", "session-resume"]);
  });

  it("socket replaced/closed mid-await: resume NOT sent on the stale socket (guard)", async () => {
    let release!: (v: string | null) => void;
    const hang = new Promise<string | null>((res) => {
      release = res;
    });
    const ws = new GraceWs("ws://x", mkHandlers({ idTokenGetter: () => hang }));
    ws.connect();
    const s = Sock.all[0];
    s.readyState = s.OPEN;
    s.fire("open");
    await Promise.resolve();
    // Simulate the socket closing during the token await.
    s.readyState = s.CLOSED;
    release("JWT");
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    // The post-await guard (this.socket !== ws || readyState !== OPEN) blocks
    // BOTH the auth-token (sendEnvelope no-ops on non-open) and the resume.
    expect(s.frames.length).toBe(0);
  });
});

describe("0253b connect() lifecycle — exactly one socket, no double-connect", () => {
  it("a single connect() opens exactly one socket", async () => {
    const ws = new GraceWs("ws://x", mkHandlers({ idTokenGetter: async () => "T" }));
    ws.connect();
    expect(Sock.all.length).toBe(1);
  });

  it("connect() after a 4401 close opens exactly ONE new socket and resets latches", async () => {
    const onAuthExpired = vi.fn();
    const ws = new GraceWs(
      "ws://x",
      mkHandlers({ idTokenGetter: async () => "T", onAuthExpired }),
    );
    ws.connect();
    const s1 = Sock.all[0];
    await s1.open();
    // Server rejects with 4401 → close handler routes to handleAuthFailure.
    // Make the refresh getter return null so it gives up (no auto-reconnect).
    s1.fire("close", { code: 4401 });
    await Promise.resolve();
    await Promise.resolve();
    const afterFail = Sock.all.length;
    // A fresh sign-in calls connect() again → exactly one MORE socket.
    ws.connect();
    expect(Sock.all.length).toBe(afterFail + 1);
    const s2 = Sock.all[Sock.all.length - 1];
    await s2.open();
    // The fresh connection re-sends auth-token first (latches were reset).
    expect(s2.types()).toEqual(["auth-token", "session-resume"]);
  });
});

// Load-bearing regression check is done out-of-band by stashing ws.ts; see the
// panel's command log. This suite fails if auth-token is not strictly first.
