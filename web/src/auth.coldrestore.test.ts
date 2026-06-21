// GRACE-2 web — COLD-BOOT CLIENT-SIDE session restore (cases-blank-box-off fix,
// NATE 2026-06-20).
//
// THE BUG: the Cases rail (and cold case-view) were BLANK box-off (agent box
// asleep) for a SIGNED-IN user until the box woke and the user re-signed in.
// ROOT CAUSE: the ENTIRE Cognito token set lived in sessionStorage, which a
// fresh tab / browser restart / evicted-tab clears. So a cold boot looked SIGNED
// OUT (isSignedIn=false -> coldListIdentity="anon"), and the serverless
// /case-list fetch went out TOKENLESS -> the Lambda's authoritative-EMPTY answer
// -> blank rail.
//
// THE FIX (auth.ts): the long-lived REFRESH token is now mirrored to
// localStorage (durable). On a cold boot initAuth() reads it and mints a fresh
// ID token via the Cognito refresh_token grant (a direct POST to /oauth2/token
// — NO agent / WebSocket involvement), restoring the signed-in session
// CLIENT-SIDE. getIdToken() then returns a usable token, so the cold case-list /
// case-view fetches authenticate box-off exactly as box-on.
//
// These tests drive the REAL auth.ts module (fresh per test via resetModules so
// the `initialized` latch is clean), seed localStorage with a durable refresh
// token, mock fetch for the /oauth2/token refresh, and assert the session is
// restored with a usable token WITHOUT any agent/WS round-trip — and that
// sign-out clears the durable token so a cold boot then reads signed-out.

import {
  describe,
  it,
  expect,
  beforeEach,
  afterEach,
  vi,
} from "vitest";

const LS_REFRESH = "grace2_cognito_refresh";
const SS_TOKENS = "grace2_cognito_tokens";

// Cognito config the module reads from import.meta.env. All three gate
// isFirebaseConfigured() -> true so the restore path is exercised.
function stubCognitoEnv(): void {
  vi.stubEnv("VITE_COGNITO_USER_POOL_ID", "us-west-2_pool123");
  vi.stubEnv("VITE_COGNITO_CLIENT_ID", "client123");
  vi.stubEnv("VITE_COGNITO_DOMAIN", "grace2-auth.auth.us-west-2.amazoncognito.com");
  vi.stubEnv("VITE_COGNITO_REGION", "us-west-2");
  vi.stubEnv("VITE_COGNITO_REDIRECT_URI", "https://app.example/");
}

/** Build an UNSIGNED JWT with the given claims (auth.ts decodes claims only;
 *  the agent verifies the real signature against JWKS — irrelevant here). */
function makeJwt(claims: Record<string, unknown>): string {
  const enc = (o: unknown) =>
    Buffer.from(JSON.stringify(o))
      .toString("base64")
      .replace(/\+/g, "-")
      .replace(/\//g, "_")
      .replace(/=+$/, "");
  return `${enc({ alg: "none", typ: "JWT" })}.${enc(claims)}.`;
}

const FUTURE_EXP = Math.floor(Date.now() / 1000) + 3600; // 1h ahead
const FRESH_ID_TOKEN = makeJwt({
  sub: "cognito-sub-restored",
  email: "nate@example.com",
  name: "Nate",
  exp: FUTURE_EXP,
});

beforeEach(() => {
  vi.resetModules();
  stubCognitoEnv();
  try {
    localStorage.clear();
    sessionStorage.clear();
  } catch {
    /* ignore */
  }
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
  try {
    localStorage.clear();
    sessionStorage.clear();
  } catch {
    /* ignore */
  }
});

describe("auth.ts — cold-boot CLIENT-SIDE session restore (box-off cases fix)", () => {
  it("restores a signed-in session + usable token from the DURABLE localStorage refresh token", async () => {
    // Cold boot: ONLY the durable refresh token survives (sessionStorage empty,
    // as a fresh tab / restart leaves it). No agent / WS involved.
    localStorage.setItem(LS_REFRESH, "durable-refresh-xyz");

    // Cognito /oauth2/token refresh grant mints a fresh ID token.
    const fetchMock = vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ id_token: FRESH_ID_TOKEN, access_token: "acc" }),
    }));
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);

    const auth = await import("./auth");

    // Drive the async restore to completion first (initAuth mints the ID token
    // via the refresh grant), THEN read the resolved identity.
    await auth.initAuth();

    // The user resolves to the restored (non-anonymous) identity...
    const seen: (import("./auth").AuthUser | null)[] = [];
    const unsub = auth.onAuthChanged((u) => {
      seen.push(u);
    });
    // onAuthChanged fires cachedUser after initAuth's microtask settles.
    await Promise.resolve();
    await Promise.resolve();

    const resolved = seen[seen.length - 1];
    expect(resolved).not.toBeNull();
    expect(resolved?.uid).toBe("cognito-sub-restored");
    expect(resolved?.isAnonymous).toBe(false);
    expect(auth.authStatus()).toBe("ready");

    // ...and getIdToken returns the freshly minted (usable) ID token, so the
    // cold case-list / case-view fetches authenticate box-off.
    const token = await auth.getIdToken();
    expect(token).toBe(FRESH_ID_TOKEN);

    // The refresh grant hit Cognito's /oauth2/token directly (NO agent / WS).
    expect(fetchMock).toHaveBeenCalled();
    const url = String((fetchMock.mock.calls[0] as unknown[])[0]);
    expect(url).toContain("/oauth2/token");
    unsub();
  });

  it("a successful sign-in token exchange MIRRORS the refresh token to localStorage (so a later cold boot restores)", async () => {
    // Simulate the OAuth /callback exchange: a ?code= -> /oauth2/token POST that
    // returns id + refresh tokens. handleRedirectCallback persists them.
    const fetchMock = vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({
        id_token: FRESH_ID_TOKEN,
        access_token: "acc",
        refresh_token: "fresh-refresh-from-exchange",
      }),
    }));
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);

    // PKCE verifier must be present for the exchange to proceed.
    sessionStorage.setItem("grace2_cognito_pkce_verifier", "verifier123");
    // happy-dom: place a ?code= on the URL the callback reads.
    window.history.replaceState({}, "", "/?code=authcode123");

    const auth = await import("./auth");
    const user = await auth.handleRedirectCallback();
    expect(user?.uid).toBe("cognito-sub-restored");

    // The DURABLE refresh token is now in localStorage -> a future cold boot
    // (different module instance) can restore the session.
    expect(localStorage.getItem(LS_REFRESH)).toBe("fresh-refresh-from-exchange");
    // The live ID token set is also in sessionStorage (in-tab fast path).
    expect(sessionStorage.getItem(SS_TOKENS)).toBeTruthy();
  });

  it("a plain box-ON reload of a LIVE session (refresh token only in sessionStorage) SEEDS the durable localStorage mirror", async () => {
    // The pre-existing-session case NATE hit live: signed in BEFORE the durable
    // mirror landed, so the refresh token lives ONLY in the sessionStorage token
    // set; localStorage holds nothing. A normal box-on reload must mirror it to
    // localStorage so the NEXT box-off cold boot can restore — WITHOUT a fresh
    // sign-in. (Regression guard for the live-token branch skipping storeTokens.)
    sessionStorage.setItem(
      SS_TOKENS,
      JSON.stringify({
        idToken: FRESH_ID_TOKEN,
        accessToken: "acc",
        refreshToken: "session-only-refresh",
        expiresAt: FUTURE_EXP * 1000,
      }),
    );
    expect(localStorage.getItem(LS_REFRESH)).toBeNull(); // not seeded yet

    // No network: the live ID token is used as-is (no refresh grant fired).
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);

    const auth = await import("./auth");
    await auth.initAuth();

    // The durable mirror is now seeded from the sessionStorage refresh token.
    expect(localStorage.getItem(LS_REFRESH)).toBe("session-only-refresh");
    // And no refresh grant was needed (the live ID token sufficed).
    expect(fetchMock).not.toHaveBeenCalled();
    // Identity resolved signed-in from the live token.
    const token = await auth.getIdToken();
    expect(token).toBe(FRESH_ID_TOKEN);
  });

  it("sign-out CLEARS the durable refresh token (a subsequent cold boot reads signed-out)", async () => {
    localStorage.setItem(LS_REFRESH, "durable-refresh-xyz");
    sessionStorage.setItem(
      SS_TOKENS,
      JSON.stringify({
        idToken: FRESH_ID_TOKEN,
        accessToken: "acc",
        refreshToken: "durable-refresh-xyz",
        expiresAt: FUTURE_EXP * 1000,
      }),
    );
    // signOut redirects via window.location.assign; stub it so the test does not
    // actually navigate.
    const assignSpy = vi
      .spyOn(window.location, "assign")
      .mockImplementation(() => {});

    const auth = await import("./auth");
    await auth.initAuth();
    await auth.signOut();

    // Durable refresh removed -> a cold boot would find nothing to restore.
    expect(localStorage.getItem(LS_REFRESH)).toBeNull();
    expect(sessionStorage.getItem(SS_TOKENS)).toBeNull();
    assignSpy.mockRestore();
  });

  it("a REVOKED refresh token (refresh 4xx) clears durable state -> signed-out cold boot, no agent dependency", async () => {
    localStorage.setItem(LS_REFRESH, "revoked-refresh");
    const fetchMock = vi.fn(async () => ({
      ok: false,
      status: 400,
      json: async () => ({ error: "invalid_grant" }),
    }));
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);

    const auth = await import("./auth");
    await auth.initAuth();

    expect(await auth.getIdToken()).toBeNull();
    // Durable state cleared so we do not retry a dead refresh forever.
    expect(localStorage.getItem(LS_REFRESH)).toBeNull();
  });

  it("NO durable refresh token -> signed-out cold boot (anon), no fetch attempted", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);

    const auth = await import("./auth");
    let resolved: import("./auth").AuthUser | null | undefined = undefined;
    const unsub = auth.onAuthChanged((u) => {
      resolved = u;
    });
    await auth.initAuth();
    await Promise.resolve();

    expect(resolved).toBeNull();
    expect(await auth.getIdToken()).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
    unsub();
  });
});
