// GRACE-2 web — lib/wake.ts tests (auto-stop/wake infra, NATE 2026-06-17).
//
// Verifies:
//   - wakeUrl() precedence: VITE_GRACE2_WAKE_URL > PUBLIC_BASE(/wake) > null.
//   - wakeConfigured() reflects wakeUrl() presence.
//   - AgentWaker.wake():
//       * disabled (no endpoint) → no fetch, status "disabled".
//       * sent (2xx)            → POSTs the wake URL once.
//       * debounced             → a second call within the window is skipped.
//       * in-flight guard       → concurrent calls coalesce to one fetch.
//       * error (non-2xx/throw) → status "error", never throws.
//       * resetDebounce()       → an immediate next wake fires (manual tap).
//
// Env is read INSIDE the helpers (not at module-eval); we still resetModules +
// dynamic-import for hygiene so each case re-evaluates against fresh env.

import { describe, it, expect, afterEach, vi } from "vitest";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
  vi.restoreAllMocks();
});

describe("wakeUrl / wakeConfigured", () => {
  it("returns null (disabled) when nothing is configured", async () => {
    const { wakeUrl, wakeConfigured } = await import("./wake");
    expect(wakeUrl()).toBeNull();
    expect(wakeConfigured()).toBe(false);
  });

  it("uses VITE_GRACE2_WAKE_URL verbatim (trailing slashes trimmed)", async () => {
    vi.resetModules();
    vi.stubEnv(
      "VITE_GRACE2_WAKE_URL",
      "https://abc.execute-api.us-west-2.amazonaws.com/wake/",
    );
    const { wakeUrl, wakeConfigured } = await import("./wake");
    expect(wakeUrl()).toBe(
      "https://abc.execute-api.us-west-2.amazonaws.com/wake",
    );
    expect(wakeConfigured()).toBe(true);
  });

  it("derives <public-base>/wake when only PUBLIC_BASE is set", async () => {
    vi.resetModules();
    vi.stubEnv("VITE_GRACE2_PUBLIC_BASE", "https://d123.cloudfront.net");
    const { wakeUrl } = await import("./wake");
    expect(wakeUrl()).toBe("https://d123.cloudfront.net/wake");
  });

  it("VITE_GRACE2_WAKE_URL beats VITE_GRACE2_PUBLIC_BASE", async () => {
    vi.resetModules();
    vi.stubEnv("VITE_GRACE2_PUBLIC_BASE", "https://d123.cloudfront.net");
    vi.stubEnv("VITE_GRACE2_WAKE_URL", "https://explicit.example/wake");
    const { wakeUrl } = await import("./wake");
    expect(wakeUrl()).toBe("https://explicit.example/wake");
  });

  it("treats a whitespace-only VITE_GRACE2_WAKE_URL as unset (falls through)", async () => {
    vi.resetModules();
    vi.stubEnv("VITE_GRACE2_WAKE_URL", "   ");
    vi.stubEnv("VITE_GRACE2_PUBLIC_BASE", "d123.cloudfront.net");
    const { wakeUrl } = await import("./wake");
    expect(wakeUrl()).toBe("https://d123.cloudfront.net/wake");
  });
});

describe("AgentWaker.wake", () => {
  it("is a no-op ('disabled') and issues no fetch when wake is unconfigured", async () => {
    const { AgentWaker } = await import("./wake");
    const fetchFn = vi.fn();
    const waker = new AgentWaker({ fetchFn });
    const res = await waker.wake();
    expect(res).toEqual({ status: "disabled" });
    expect(fetchFn).not.toHaveBeenCalled();
  });

  it("POSTs the wake URL once and returns 'sent' on 2xx", async () => {
    vi.resetModules();
    vi.stubEnv("VITE_GRACE2_WAKE_URL", "https://explicit.example/wake");
    const { AgentWaker } = await import("./wake");
    const fetchFn = vi.fn(async () => ({ ok: true, status: 202 }));
    const waker = new AgentWaker({ fetchFn });
    const res = await waker.wake();
    expect(res).toEqual({ status: "sent" });
    expect(fetchFn).toHaveBeenCalledTimes(1);
    expect(fetchFn).toHaveBeenCalledWith(
      "https://explicit.example/wake",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("debounces a second wake within the window (coalesces reconnect ticks)", async () => {
    vi.resetModules();
    vi.stubEnv("VITE_GRACE2_WAKE_URL", "https://explicit.example/wake");
    const { AgentWaker } = await import("./wake");
    const fetchFn = vi.fn(async () => ({ ok: true, status: 200 }));
    let now = 1_000;
    const waker = new AgentWaker({ fetchFn, now: () => now, debounceMs: 20_000 });

    expect(await waker.wake()).toEqual({ status: "sent" });
    now = 5_000; // still inside the 20s window
    expect(await waker.wake()).toEqual({ status: "debounced" });
    now = 30_000; // past the window
    expect(await waker.wake()).toEqual({ status: "sent" });
    expect(fetchFn).toHaveBeenCalledTimes(2);
  });

  it("coalesces concurrent (in-flight) calls into one fetch", async () => {
    vi.resetModules();
    vi.stubEnv("VITE_GRACE2_WAKE_URL", "https://explicit.example/wake");
    const { AgentWaker } = await import("./wake");
    let resolveFetch: (v: { ok: boolean; status: number }) => void = () => {};
    const fetchFn = vi.fn(
      () =>
        new Promise<{ ok: boolean; status: number }>((r) => {
          resolveFetch = r;
        }),
    );
    const waker = new AgentWaker({ fetchFn });
    const p1 = waker.wake();
    const p2 = waker.wake(); // in flight → debounced
    expect(await p2).toEqual({ status: "debounced" });
    resolveFetch({ ok: true, status: 200 });
    expect(await p1).toEqual({ status: "sent" });
    expect(fetchFn).toHaveBeenCalledTimes(1);
  });

  it("returns 'error' (never throws) on a non-2xx response", async () => {
    vi.resetModules();
    vi.stubEnv("VITE_GRACE2_WAKE_URL", "https://explicit.example/wake");
    const { AgentWaker } = await import("./wake");
    const fetchFn = vi.fn(async () => ({ ok: false, status: 500 }));
    const waker = new AgentWaker({ fetchFn });
    const res = await waker.wake();
    expect(res.status).toBe("error");
  });

  it("returns 'error' (never throws) when fetch rejects", async () => {
    vi.resetModules();
    vi.stubEnv("VITE_GRACE2_WAKE_URL", "https://explicit.example/wake");
    const { AgentWaker } = await import("./wake");
    const fetchFn = vi.fn(async () => {
      throw new Error("network down");
    });
    const waker = new AgentWaker({ fetchFn });
    const res = await waker.wake();
    expect(res.status).toBe("error");
  });

  it("resetDebounce() lets the next wake fire immediately (manual tap)", async () => {
    vi.resetModules();
    vi.stubEnv("VITE_GRACE2_WAKE_URL", "https://explicit.example/wake");
    const { AgentWaker } = await import("./wake");
    const fetchFn = vi.fn(async () => ({ ok: true, status: 200 }));
    let now = 1_000;
    const waker = new AgentWaker({ fetchFn, now: () => now, debounceMs: 20_000 });

    expect(await waker.wake()).toEqual({ status: "sent" });
    // Without reset, an immediate retry is debounced…
    expect(await waker.wake()).toEqual({ status: "debounced" });
    // …a manual tap resets the window so the next wake fires.
    waker.resetDebounce();
    expect(await waker.wake()).toEqual({ status: "sent" });
    expect(fetchFn).toHaveBeenCalledTimes(2);
  });
});
