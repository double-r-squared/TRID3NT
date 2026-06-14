// FRESH adversarial probe for job-0253b authEpoch reconnect (CORRECTNESS lens).
// Reproduces App.tsx's onAuthChanged→authEpoch logic + the two ws effects
// VERBATIM (App.tsx:245-262, 577, 593, 936) and drives the REAL GraceWs against
// a fake socket. Attacks:
//   * 4401 → recovery → exactly ONE new socket PER instance (App + Chat)
//   * a SECOND 4401+recovery cycle bumps epoch AGAIN (not a one-shot)
//   * rapid double non-anon sign-in (no intervening expiry) → NO double-connect
//   * disabled/dev mode (null-only) → epoch stays 0, no reconnect machinery
//
// Run: cd web && npx vitest run src/verify_0253b_epoch.test.tsx

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { GraceWs, __test_resetSessionHub, clearAnonymousUserId, type WsHandlers } from "./ws";

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
  send(d: string): void {
    if (this.readyState === this.OPEN) this.frames.push(d);
  }
  close(): void {
    this.readyState = this.CLOSED;
  }
  fire(type: string, ev: Record<string, unknown> = {}): void {
    for (const cb of this.cbs[type] ?? []) cb({ type, ...ev });
  }
}

let origWS: typeof WebSocket;
beforeEach(() => {
  Sock.all = [];
  __test_resetSessionHub();
  clearAnonymousUserId();
  origWS = globalThis.WebSocket;
  // @ts-expect-error fake
  globalThis.WebSocket = Sock;
});
afterEach(() => {
  globalThis.WebSocket = origWS;
});

function mkH(over: Partial<WsHandlers> = {}): WsHandlers {
  return {
    onStatus: vi.fn(),
    onAgentChunk: vi.fn(),
    onPipelineState: vi.fn(),
    onSessionState: vi.fn(),
    onError: vi.fn(),
    ...over,
  } as unknown as WsHandlers;
}

// ── A faithful reproduction of App.tsx's epoch state machine ─────────────── //
// Mirrors App.tsx:231,245-262 exactly. `onAuthExpired` (App.tsx:577) sets
// authExpired=true. A non-anon sign-in bumps epoch iff authExpired was true,
// then clears it (App.tsx:256-258). Both App and Chat ws effects key on epoch
// (App.tsx:593 + Chat dep). We model two GraceWs instances and reconnect both
// whenever epoch changes — the effect-dep behavior.
class AppModel {
  authExpired = false;
  epoch = 0;
  appWs: GraceWs | null = null;
  chatWs: GraceWs | null = null;
  lastEpochRun = -1;

  // The onAuthExpired handler wired into BOTH instances (App.tsx:577).
  onAuthExpired = (): void => {
    this.authExpired = true;
  };

  // App.tsx onAuthChanged callback (App.tsx:249-260), verbatim logic.
  onAuthChanged(u: { isAnonymous: boolean } | null): void {
    if (u && !u.isAnonymous) {
      if (this.authExpired) this.epoch += 1; // App.tsx:257
      this.authExpired = false; // App.tsx:258
    }
    this.runEffects(); // epoch change re-runs both ws effects
  }

  // Both ws effects re-run when epoch changes (App.tsx:593 dep, Chat dep).
  runEffects(): void {
    if (this.epoch === this.lastEpochRun) return; // dep unchanged → no re-run
    this.lastEpochRun = this.epoch;
    // cleanup old (effect cleanup: ws.close()), then new instance + connect()
    this.appWs?.close();
    this.chatWs?.close();
    this.appWs = new GraceWs("ws://app", mkH({ onAuthExpired: this.onAuthExpired }));
    this.chatWs = new GraceWs("ws://chat", mkH({ onAuthExpired: this.onAuthExpired }));
    this.appWs.connect();
    this.chatWs.connect();
  }
}

describe("0253b authEpoch — recovery reconnect lifecycle", () => {
  it("mount opens App+Chat sockets once; a 4401+recovery reconnects BOTH (one each)", () => {
    const app = new AppModel();
    app.runEffects(); // initial mount (epoch 0)
    expect(Sock.all.length).toBe(2); // App + Chat

    // Agent rejects both with 4401 → onAuthExpired sets authExpired.
    app.onAuthExpired();
    expect(app.authExpired).toBe(true);

    // A fresh non-anon user lands → epoch bumps 0→1 → both effects re-run.
    app.onAuthChanged({ isAnonymous: false });
    expect(app.epoch).toBe(1);
    // Exactly +2 sockets (one per instance), not +1, not +4.
    expect(Sock.all.length).toBe(4);
  });

  it("a SECOND 4401+recovery cycle bumps epoch AGAIN (not a one-shot)", () => {
    const app = new AppModel();
    app.runEffects();
    // Cycle 1
    app.onAuthExpired();
    app.onAuthChanged({ isAnonymous: false });
    expect(app.epoch).toBe(1);
    const afterCycle1 = Sock.all.length;
    // Cycle 2 — expire again, recover again.
    app.onAuthExpired();
    expect(app.authExpired).toBe(true);
    app.onAuthChanged({ isAnonymous: false });
    expect(app.epoch).toBe(2); // bumped AGAIN — proves not a one-shot
    expect(Sock.all.length).toBe(afterCycle1 + 2); // +2 more (App+Chat)
  });

  it("rapid double non-anon sign-in with NO intervening expiry → NO double-connect", () => {
    const app = new AppModel();
    app.runEffects();
    app.onAuthExpired();
    app.onAuthChanged({ isAnonymous: false }); // recovery → epoch 1
    expect(app.epoch).toBe(1);
    const afterRecovery = Sock.all.length;
    // A SECOND non-anon delivery arrives without an intervening expiry
    // (authExpired was cleared by the first). Must NOT bump epoch again.
    app.onAuthChanged({ isAnonymous: false });
    expect(app.epoch).toBe(1); // unchanged
    expect(Sock.all.length).toBe(afterRecovery); // no new sockets
  });

  it("anonymous sign-in never bumps epoch (only non-anon recovery does)", () => {
    const app = new AppModel();
    app.runEffects();
    app.onAuthExpired();
    // An anonymous user lands — does NOT clear authExpired, does NOT bump.
    app.onAuthChanged({ isAnonymous: true });
    expect(app.epoch).toBe(0);
    expect(app.authExpired).toBe(true); // still expired
    expect(Sock.all.length).toBe(2); // no reconnect
  });

  it("disabled/dev mode (null-only onAuthChanged): epoch stays 0, no reconnect", () => {
    const app = new AppModel();
    app.runEffects();
    const atMount = Sock.all.length;
    // Disabled mode delivers null exactly once and never a non-anon user.
    app.onAuthChanged(null);
    app.onAuthChanged(null);
    expect(app.epoch).toBe(0);
    expect(app.authExpired).toBe(false);
    expect(Sock.all.length).toBe(atMount); // structurally dead — no reconnect
  });
});
