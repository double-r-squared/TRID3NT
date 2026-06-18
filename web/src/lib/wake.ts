// GRACE-2 web — agent-box wake-up client (auto-stop/wake infra, NATE 2026-06-17).
//
// The always-on AGENT box (EC2 t3.large running grace2-agent WS :8765 +
// catalog HTTP :8766 + titiler :8080, fronted by CloudFront) is now eligible
// to be STOPPED by an idle-check Lambda after N consecutive zero-connection
// polls. A stopped box answers neither the WebSocket nor any HTTP endpoint,
// so the browser cannot reach the agent until the instance is started again.
//
// This module is the WEB side of the wake contract:
//   - `wakeUrl()` derives the API-Gateway HTTP endpoint that fronts the
//     StartInstances "wake" Lambda. Precedence (most specific wins), mirroring
//     lib/public_base.ts:
//         VITE_GRACE2_WAKE_URL  >  VITE_GRACE2_PUBLIC_BASE(/wake)  >  null
//     When NOTHING is configured, `wakeUrl()` returns null and `wakeAgent()`
//     is a no-op — dev / localhost / LAN builds (where the box is never
//     auto-stopped) behave exactly as before.
//   - `wakeAgent()` fires a single POST to that endpoint to ask the Lambda to
//     StartInstances. It is FIRE-AND-FORGET (the WS reconnect loop owns the
//     retry that actually re-establishes the connection) and DEBOUNCED so a
//     burst of reconnect ticks coalesces into one StartInstances request.
//
// The web NEVER calls EC2 directly and holds NO AWS credentials — the wake
// endpoint is a least-privilege API-Gateway → Lambda that the infra root
// (infra/aws-autostop) provisions. This module performs no work beyond reading
// `import.meta.env` / `import.meta.env.VITE_GRACE2_PUBLIC_BASE` and issuing a
// `fetch`; it is pure + unit-testable (the fetch + clock are injectable).

import { normalizePublicBase } from "./public_base";

/**
 * Read `VITE_GRACE2_PUBLIC_BASE` (build-time), normalised to an origin with no
 * trailing slash. null when unset/blank. Local copy of public_base.ts's private
 * helper so the two seams stay decoupled (public_base owns WS/HTTP; this owns
 * the wake endpoint).
 */
function publicBase(): string | null {
  const raw =
    (import.meta.env.VITE_GRACE2_PUBLIC_BASE as string | undefined) ?? null;
  return normalizePublicBase(raw);
}

/**
 * Canonical wake-endpoint URL, or null when wake is not configured.
 *
 * Precedence:
 *   1. `VITE_GRACE2_WAKE_URL` — an explicit full URL to the API-Gateway wake
 *      endpoint (e.g. "https://abc123.execute-api.us-west-2.amazonaws.com/wake").
 *      Used verbatim (trailing slashes trimmed). This is the production path:
 *      the autostop API-Gateway is a SEPARATE origin from the CloudFront edge,
 *      so it must be supplied explicitly.
 *   2. `VITE_GRACE2_PUBLIC_BASE` + "/wake" — a convenience for a future world
 *      where the wake route is folded behind the same edge as the agent.
 *   3. null — nothing configured; wake is disabled (dev/LAN; the box is never
 *      auto-stopped there).
 */
export function wakeUrl(): string | null {
  const explicit =
    (import.meta.env.VITE_GRACE2_WAKE_URL as string | undefined) ?? null;
  if (explicit != null && explicit.trim() !== "") {
    return explicit.trim().replace(/\/+$/, "");
  }

  const base = publicBase();
  if (base) return `${base}/wake`;

  return null;
}

/** True iff a wake endpoint is configured — UI gates the "Wake up agent"
 *  overlay on this so dev/LAN never shows it (the box can't be stopped there). */
export function wakeConfigured(): boolean {
  return wakeUrl() !== null;
}

/** Outcome of a `wakeAgent()` call. */
export type WakeResult =
  | { status: "sent" } // POST issued and accepted (2xx) — Lambda asked to start the box
  | { status: "debounced" } // a recent wake is still within the debounce window; skipped
  | { status: "disabled" } // no wake endpoint configured (dev/LAN)
  | { status: "error"; error: unknown }; // POST failed (network / non-2xx)

/** Minimal fetch signature so tests can inject without DOM `fetch`. */
export type FetchLike = (
  input: string,
  init?: { method?: string; headers?: Record<string, string>; body?: string; signal?: AbortSignal },
) => Promise<{ ok: boolean; status: number }>;

/** Injectable clock for deterministic debounce tests. */
export type NowFn = () => number;

/**
 * Default debounce window. A stopped EC2 box takes ~1-2 min to boot the agent;
 * the WS reconnect loop ticks far more often than that (capped 5s backoff), so
 * without a debounce every tick would POST StartInstances. One request per
 * window is plenty — StartInstances is idempotent server-side, but we avoid the
 * churn (and the API-Gateway cost) regardless.
 */
export const WAKE_DEBOUNCE_MS = 20_000;

/**
 * A small stateful waker. Holds the last-attempt timestamp + an in-flight guard
 * so concurrent/rapid calls coalesce. Construct one per app session (App.tsx
 * holds a singleton via a ref); the module-level `wakeAgent` uses a shared
 * default instance for callers that don't need their own (ws.ts).
 */
export class AgentWaker {
  // -Infinity (not 0) so the FIRST-EVER wake always passes the debounce window
  // check (`now - lastAttempt < debounceMs`) regardless of the wall-clock /
  // injected-clock origin. `resetDebounce()` restores this sentinel.
  private lastAttemptMs = Number.NEGATIVE_INFINITY;
  private inFlight = false;
  private readonly fetchFn: FetchLike;
  private readonly now: NowFn;
  private readonly debounceMs: number;

  constructor(opts?: { fetchFn?: FetchLike; now?: NowFn; debounceMs?: number }) {
    this.fetchFn =
      opts?.fetchFn ??
      ((input, init) =>
        // Cast through the DOM fetch; the structural return type matches what
        // we read (`ok`, `status`).
        (globalThis.fetch as unknown as FetchLike)(input, init));
    this.now = opts?.now ?? (() => Date.now());
    this.debounceMs = opts?.debounceMs ?? WAKE_DEBOUNCE_MS;
  }

  /**
   * Ask the wake Lambda to start the agent box. Fire-and-forget + debounced.
   *
   * Returns:
   *   - `disabled`  when no wake endpoint is configured (dev/LAN).
   *   - `debounced` when a wake was attempted within the debounce window OR a
   *     wake is currently in flight (coalesces a burst of reconnect ticks).
   *   - `sent`      when the POST returned 2xx.
   *   - `error`     when the POST threw or returned non-2xx.
   *
   * Never throws — the WS reconnect loop must not be wedged by a wake failure.
   */
  async wake(): Promise<WakeResult> {
    const url = wakeUrl();
    if (url === null) return { status: "disabled" };

    const t = this.now();
    if (this.inFlight || t - this.lastAttemptMs < this.debounceMs) {
      return { status: "debounced" };
    }
    this.lastAttemptMs = t;
    this.inFlight = true;
    try {
      const resp = await this.fetchFn(url, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: "{}",
      });
      if (resp.ok) return { status: "sent" };
      return { status: "error", error: new Error(`wake POST ${resp.status}`) };
    } catch (error) {
      return { status: "error", error };
    } finally {
      this.inFlight = false;
    }
  }

  /** Reset debounce state so the next `wake()` fires immediately. The wake-up
   *  overlay calls this on an explicit user TAP so a manual "Wake up agent"
   *  press is never silently swallowed by a recent automatic attempt. */
  resetDebounce(): void {
    this.lastAttemptMs = Number.NEGATIVE_INFINITY;
  }
}

// Shared default waker for callers (ws.ts reconnect loop) that don't manage
// their own instance. App.tsx constructs its own so the overlay's explicit-tap
// `resetDebounce()` and the reconnect loop share state when wired together.
const defaultWaker = new AgentWaker();

/** Convenience: wake via the shared default `AgentWaker`. */
export function wakeAgent(): Promise<WakeResult> {
  return defaultWaker.wake();
}
