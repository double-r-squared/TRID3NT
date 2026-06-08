// GRACE-2 web — AuthPanel + ws.ts auth-token tests (job-0123).
//
// Verifies:
//   1. AuthPanel renders Sign-in + Continue-as-anonymous buttons when no user.
//   2. AuthPanel renders display name + Sign-out when user present.
//   3. AuthPanel renders the anonymous-flag suffix for anonymous users.
//   4. AuthPanel click → onSignInAnonymous fires (no auth-token round-trip).
//   5. ws.ts emits an `auth-token` envelope on connect when a token is
//      returned by the injected idTokenGetter.
//   6. ws.ts SKIPS the `auth-token` envelope when the getter returns null —
//      the anonymous fallback path the kickoff calls out.
//
// All Firebase SDK paths are stubbed via the AuthPanel prop seams and the
// ws.ts `idTokenGetter` handler, so no real Firebase init is triggered (the
// SDK is dynamically imported in auth.ts; happy-dom doesn't need it).

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup, act } from "@testing-library/react";
import { AuthPanel } from "./components/AuthPanel";
import { AuthUser } from "./auth";
import { GraceWs } from "./ws";

// --- Module-level stubs for WebSocket ----------------------------------- //

interface FakeSocket {
  readyState: number;
  sent: string[];
  listeners: Record<string, ((ev: unknown) => void)[]>;
  triggerOpen(): void;
  triggerClose(): void;
  triggerMessage(data: unknown): void;
}

function installFakeWebSocket(): { factory: () => FakeSocket; lastSocket: () => FakeSocket | null } {
  let lastSocket: FakeSocket | null = null;
  class FakeWS {
    static OPEN = 1;
    static CONNECTING = 0;
    static CLOSED = 3;
    readyState = FakeWS.CONNECTING;
    sent: string[] = [];
    listeners: Record<string, ((ev: unknown) => void)[]> = {};
    constructor(_url: string) {
      lastSocket = this as unknown as FakeSocket;
    }
    addEventListener(type: string, cb: (ev: unknown) => void): void {
      (this.listeners[type] ??= []).push(cb);
    }
    send(data: string): void {
      this.sent.push(data);
    }
    close(): void {
      this.readyState = FakeWS.CLOSED;
      (this.listeners["close"] ?? []).forEach((cb) => cb({}));
    }
    triggerOpen(): void {
      this.readyState = FakeWS.OPEN;
      (this.listeners["open"] ?? []).forEach((cb) => cb({}));
    }
    triggerClose(): void {
      this.readyState = FakeWS.CLOSED;
      (this.listeners["close"] ?? []).forEach((cb) => cb({}));
    }
    triggerMessage(data: unknown): void {
      (this.listeners["message"] ?? []).forEach((cb) =>
        cb({ data: typeof data === "string" ? data : JSON.stringify(data) }),
      );
    }
  }
  // @ts-expect-error replacing the global for the test only
  globalThis.WebSocket = FakeWS;
  return {
    factory: () => lastSocket as FakeSocket,
    lastSocket: () => lastSocket,
  };
}

// --- AuthPanel rendering tests ------------------------------------------ //

describe("AuthPanel — signed-out", () => {
  afterEach(() => cleanup());

  it("renders Sign-in and Continue-as-anonymous buttons", () => {
    const subscribe = (cb: (u: AuthUser | null) => void) => {
      cb(null);
      return () => {};
    };
    render(<AuthPanel subscribeAuth={subscribe} />);
    expect(screen.getByTestId("grace2-auth-panel")).toHaveAttribute(
      "data-auth-state",
      "signed-out",
    );
    expect(screen.getByTestId("grace2-auth-google")).toBeInTheDocument();
    expect(screen.getByTestId("grace2-auth-anonymous")).toBeInTheDocument();
  });

  it("calls onSignInAnonymous when the anonymous button is clicked", async () => {
    const subscribe = (cb: (u: AuthUser | null) => void) => {
      cb(null);
      return () => {};
    };
    const onAnon = vi.fn().mockResolvedValue({
      uid: "anon-uid-1",
      displayName: null,
      email: null,
      photoURL: null,
      isAnonymous: true,
    });
    render(
      <AuthPanel
        subscribeAuth={subscribe}
        onSignInAnonymous={onAnon}
      />,
    );
    fireEvent.click(screen.getByTestId("grace2-auth-anonymous"));
    await waitFor(() => expect(onAnon).toHaveBeenCalledTimes(1));
  });
});

describe("AuthPanel — signed-in", () => {
  afterEach(() => cleanup());

  it("renders the display name and Sign-out button for a Google user", () => {
    const user: AuthUser = {
      uid: "google-uid-1",
      displayName: "Ada Lovelace",
      email: "ada@example.com",
      photoURL: "https://example.com/a.png",
      isAnonymous: false,
    };
    const subscribe = (cb: (u: AuthUser | null) => void) => {
      cb(user);
      return () => {};
    };
    render(<AuthPanel subscribeAuth={subscribe} />);
    expect(screen.getByTestId("grace2-auth-panel")).toHaveAttribute(
      "data-auth-state",
      "signed-in",
    );
    expect(screen.getByTestId("grace2-auth-username")).toHaveTextContent(
      "Ada Lovelace",
    );
    expect(screen.getByTestId("grace2-auth-signout")).toBeInTheDocument();
    expect(screen.getByTestId("grace2-auth-avatar")).toBeInTheDocument();
  });

  it("annotates anonymous users with an (anon) suffix and initials avatar", () => {
    const user: AuthUser = {
      uid: "anon-uid-2",
      displayName: null,
      email: null,
      photoURL: null,
      isAnonymous: true,
    };
    const subscribe = (cb: (u: AuthUser | null) => void) => {
      cb(user);
      return () => {};
    };
    render(<AuthPanel subscribeAuth={subscribe} />);
    expect(screen.getByTestId("grace2-auth-username")).toHaveTextContent("anon");
    expect(
      screen.getByTestId("grace2-auth-avatar-initials"),
    ).toBeInTheDocument();
  });

  it("rerenders when the auth subscription emits a new user (App.tsx subscription path)", () => {
    let emit: ((u: AuthUser | null) => void) | null = null;
    const subscribe = (cb: (u: AuthUser | null) => void) => {
      emit = cb;
      cb(null);
      return () => {};
    };
    render(<AuthPanel subscribeAuth={subscribe} />);
    expect(screen.getByTestId("grace2-auth-panel")).toHaveAttribute(
      "data-auth-state",
      "signed-out",
    );
    // Simulate a sign-in event.
    act(() => {
      emit?.({
        uid: "u",
        displayName: "Grace Hopper",
        email: "grace@example.com",
        photoURL: null,
        isAnonymous: false,
      });
    });
    expect(screen.getByTestId("grace2-auth-panel")).toHaveAttribute(
      "data-auth-state",
      "signed-in",
    );
    expect(screen.getByTestId("grace2-auth-username")).toHaveTextContent(
      "Grace Hopper",
    );
  });
});

// --- ws.ts auth-token emission tests ------------------------------------ //

describe("ws.ts — auth-token envelope", () => {
  let originalWS: typeof WebSocket;
  beforeEach(() => {
    originalWS = globalThis.WebSocket;
  });
  afterEach(() => {
    globalThis.WebSocket = originalWS;
  });

  it("emits an `auth-token` envelope on connect when a token is available", async () => {
    const fake = installFakeWebSocket();
    const handlers = {
      onStatus: vi.fn(),
      onAgentChunk: vi.fn(),
      onPipelineState: vi.fn(),
      onSessionState: vi.fn(),
      onError: vi.fn(),
      idTokenGetter: vi.fn().mockResolvedValue("fake-jwt-token-xyz"),
    };
    const ws = new GraceWs("ws://test", handlers);
    ws.connect();
    const sock = fake.factory();
    sock.triggerOpen();
    // session-resume is sent synchronously; auth-token is sent on the
    // resolved getIdToken() promise.
    await waitFor(() => {
      const types = sock.sent.map((s) => JSON.parse(s).type);
      expect(types).toContain("auth-token");
    });
    const authTokenEnv = sock.sent
      .map((s) => JSON.parse(s))
      .find((e: { type: string }) => e.type === "auth-token");
    expect(authTokenEnv.payload).toEqual({
      id_token: "fake-jwt-token-xyz",
      provider: "firebase",
    });
    expect(handlers.idTokenGetter).toHaveBeenCalledTimes(1);
    ws.close();
  });

  it("SKIPS the `auth-token` envelope when the getter returns null (anonymous fallback)", async () => {
    const fake = installFakeWebSocket();
    const handlers = {
      onStatus: vi.fn(),
      onAgentChunk: vi.fn(),
      onPipelineState: vi.fn(),
      onSessionState: vi.fn(),
      onError: vi.fn(),
      idTokenGetter: vi.fn().mockResolvedValue(null),
    };
    const ws = new GraceWs("ws://test", handlers);
    ws.connect();
    const sock = fake.factory();
    sock.triggerOpen();
    // Allow the getIdToken promise microtask to flush.
    await new Promise((r) => setTimeout(r, 0));
    await new Promise((r) => setTimeout(r, 0));
    const types = sock.sent.map((s) => JSON.parse(s).type);
    expect(types).not.toContain("auth-token");
    // session-resume must still be sent.
    expect(types).toContain("session-resume");
    expect(handlers.idTokenGetter).toHaveBeenCalledTimes(1);
    ws.close();
  });

  it("SKIPS the `auth-token` envelope when the getter throws (resilience)", async () => {
    const fake = installFakeWebSocket();
    const handlers = {
      onStatus: vi.fn(),
      onAgentChunk: vi.fn(),
      onPipelineState: vi.fn(),
      onSessionState: vi.fn(),
      onError: vi.fn(),
      idTokenGetter: vi.fn().mockRejectedValue(new Error("network down")),
    };
    const ws = new GraceWs("ws://test", handlers);
    ws.connect();
    const sock = fake.factory();
    sock.triggerOpen();
    await new Promise((r) => setTimeout(r, 0));
    await new Promise((r) => setTimeout(r, 0));
    const types = sock.sent.map((s) => JSON.parse(s).type);
    expect(types).not.toContain("auth-token");
    expect(types).toContain("session-resume");
    ws.close();
  });
});
