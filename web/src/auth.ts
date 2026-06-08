// GRACE-2 web — Firebase Auth client (job-0123, sprint-12-mega Wave 2).
//
// Implements Appendix H of the SRS (Firebase Authentication as the GRACE-2
// identity provider; Decision P). The client surface is intentionally narrow:
//   - lazy initialization (no Firebase init unless `VITE_FIREBASE_PROJECT_ID`
//     is set, so the existing dev path still boots against a local ws agent
//     with no Firebase project provisioned)
//   - sign-in helpers for Google (popup) and anonymous flows (H.3 landing UX)
//   - sign-out helper
//   - ID-token retrieval for the WebSocket connect handshake (H.5)
//   - subscribe-to-auth-state-changes shim so React can rerender without
//     leaking Firebase types beyond this module
//
// This module never persists user records — that's the agent's job (FR-MP-1
// `Persistence.upsert_user`). The web client is a credential-issuing client
// only, per Invariant 1 (web consumes; no client-side number generation).
//
// Anonymous flow note (H.3): `signInAnonymously` issues a stable Firebase
// `uid` + ID token without credentials. The kickoff describes this as
// "skip and let server fall back to anonymous" — that's slightly different
// from H.3's "always sign in as anonymous on first visit." We implement BOTH
// paths: the UI offers a "Continue as anonymous" button (explicit user
// choice; matches kickoff #3), AND if no auth state is present after a
// short window, ws.ts treats it as anonymous-mode (skip auth-token; matches
// kickoff #4). H.3's "auto-anonymous-on-first-visit" is a Wave 2+ UX
// refinement surfaced as OQ-0123-AUTO-ANON-DEFAULT.

import type { Auth, User as FirebaseUser } from "firebase/auth";

/** Minimal user-facing identity shape exposed to React. Decoupled from Firebase's `User` so the rest of the app stays library-agnostic. */
export interface AuthUser {
  /** Firebase uid; stable across token refreshes and anonymous→authenticated upgrade. */
  uid: string;
  /** Display name (Google: real name; anonymous: null). */
  displayName: string | null;
  /** Email (Google: real email; anonymous: null). */
  email: string | null;
  /** Photo URL (Google: profile picture; anonymous: null). */
  photoURL: string | null;
  /** True for anonymous-sign-in sessions. H.3 anonymous Cases own `owner_user_id = uid` just like authenticated. */
  isAnonymous: boolean;
}

/** Connection status of the Firebase Auth subsystem. */
export type AuthInitStatus =
  | "disabled" // VITE_FIREBASE_PROJECT_ID absent — local dev / anonymous-only mode
  | "initializing"
  | "ready"
  | "failed";

/** Test seam: a fake Firebase Auth object can be injected before `initAuth()` is called. */
let injectedAuth: Auth | null = null;
/** Test seam: prevents the real Firebase SDK from being imported in unit tests. */
export function __setAuthForTesting(fake: Auth | null): void {
  injectedAuth = fake;
  cachedAuth = fake;
  cachedInitStatus = fake ? "ready" : "disabled";
}

let cachedAuth: Auth | null = null;
let cachedInitStatus: AuthInitStatus = "disabled";
let initPromise: Promise<Auth | null> | null = null;

/** Are the required Vite env vars set so Firebase can be initialised? */
export function isFirebaseConfigured(): boolean {
  const projectId = (import.meta.env.VITE_FIREBASE_PROJECT_ID ?? "") as string;
  const apiKey = (import.meta.env.VITE_FIREBASE_API_KEY ?? "") as string;
  return projectId.length > 0 && apiKey.length > 0;
}

/** Current init status (synchronous read). */
export function authStatus(): AuthInitStatus {
  return cachedInitStatus;
}

/**
 * Initialise Firebase Auth lazily.
 *
 * Returns the `Auth` instance on success, `null` on `disabled` (env vars
 * absent — anonymous-only mode). Throws only on configured-but-failed init;
 * callers should let that bubble to the React error boundary.
 *
 * Idempotent: repeated calls return the cached promise.
 */
export async function initAuth(): Promise<Auth | null> {
  if (cachedAuth) return cachedAuth;
  if (injectedAuth) return injectedAuth;
  if (initPromise) return initPromise;

  if (!isFirebaseConfigured()) {
    cachedInitStatus = "disabled";
    return null;
  }

  cachedInitStatus = "initializing";
  initPromise = (async () => {
    try {
      const { initializeApp, getApps } = await import("firebase/app");
      const { getAuth } = await import("firebase/auth");
      const config = {
        apiKey: import.meta.env.VITE_FIREBASE_API_KEY as string,
        authDomain: import.meta.env.VITE_FIREBASE_AUTH_DOMAIN as string,
        projectId: import.meta.env.VITE_FIREBASE_PROJECT_ID as string,
        appId: (import.meta.env.VITE_FIREBASE_APP_ID ?? "") as string,
        messagingSenderId: (import.meta.env.VITE_FIREBASE_MESSAGING_SENDER_ID ??
          "") as string,
      };
      const app = getApps().length > 0 ? getApps()[0] : initializeApp(config);
      const auth = getAuth(app);
      cachedAuth = auth;
      cachedInitStatus = "ready";
      return auth;
    } catch (err) {
      cachedInitStatus = "failed";
      // eslint-disable-next-line no-console
      console.error("[auth] Firebase init failed:", err);
      throw err;
    }
  })();
  return initPromise;
}

/** Map the Firebase `User` object onto the library-agnostic `AuthUser` shape. */
function adaptUser(u: FirebaseUser | null): AuthUser | null {
  if (!u) return null;
  return {
    uid: u.uid,
    displayName: u.displayName,
    email: u.email,
    photoURL: u.photoURL,
    isAnonymous: u.isAnonymous,
  };
}

/**
 * Subscribe to Firebase Auth state changes.
 *
 * Returns an unsubscribe function. If Firebase is `disabled` (env vars
 * absent), the callback is invoked once with `null` and no further updates
 * are delivered — anonymous-only mode is a stable "always signed-out" state
 * from the auth subsystem's view; the ws.ts anonymous fallback handles the
 * "session proceeds anyway" half.
 */
export function onAuthChanged(cb: (u: AuthUser | null) => void): () => void {
  let unsubInner: (() => void) | null = null;
  let cancelled = false;
  initAuth()
    .then(async (auth) => {
      if (cancelled || !auth) {
        if (!cancelled) cb(null);
        return;
      }
      const { onAuthStateChanged } = await import("firebase/auth");
      unsubInner = onAuthStateChanged(auth, (u) => {
        if (!cancelled) cb(adaptUser(u));
      });
    })
    .catch(() => {
      if (!cancelled) cb(null);
    });
  return () => {
    cancelled = true;
    if (unsubInner) unsubInner();
  };
}

/**
 * Retrieve the current user's Firebase ID token (JWT) for the WebSocket
 * handshake (H.5). Returns `null` if the user is not signed in OR Firebase
 * is disabled — ws.ts handles both as "anonymous fallback: skip auth-token,
 * let server fall back to anonymous" per kickoff #4.
 *
 * `forceRefresh: false` is the default; pass `true` to force a fresh JWT
 * (e.g. after the agent emitted an AUTH_TOKEN_EXPIRED close — Wave 2+).
 */
export async function getIdToken(forceRefresh = false): Promise<string | null> {
  const auth = await initAuth();
  if (!auth) return null;
  const user = auth.currentUser;
  if (!user) return null;
  try {
    return await user.getIdToken(forceRefresh);
  } catch (err) {
    // eslint-disable-next-line no-console
    console.warn("[auth] getIdToken failed:", err);
    return null;
  }
}

/** Sign in with Google (popup). Returns the adapted user on success. */
export async function signInWithGoogle(): Promise<AuthUser | null> {
  const auth = await initAuth();
  if (!auth) {
    throw new Error(
      "Firebase not configured (set VITE_FIREBASE_* env vars to enable Google sign-in)",
    );
  }
  const { GoogleAuthProvider, signInWithPopup } = await import("firebase/auth");
  const provider = new GoogleAuthProvider();
  const cred = await signInWithPopup(auth, provider);
  return adaptUser(cred.user);
}

/**
 * Sign in anonymously. Firebase issues a stable `uid` + ID token.
 *
 * Per H.3, anonymous users can own Cases. Per kickoff #4, the anonymous-flow
 * acceptance test uses this path to verify the round-trip without sending an
 * auth-token. We DO sign in anonymously here (which gets an ID token), and
 * ws.ts decides whether to send it on the connect handshake based on the
 * surfaced kickoff semantics (current behaviour: send if available, skip
 * gracefully if not).
 */
export async function signInAnonymous(): Promise<AuthUser | null> {
  const auth = await initAuth();
  if (!auth) {
    // Anonymous-only mode (no Firebase project). ws.ts will skip the
    // auth-token frame; the server's anonymous fallback handles the session.
    return null;
  }
  const { signInAnonymously } = await import("firebase/auth");
  const cred = await signInAnonymously(auth);
  return adaptUser(cred.user);
}

/** Sign out the current user. No-op when Firebase is disabled. */
export async function signOut(): Promise<void> {
  const auth = await initAuth();
  if (!auth) return;
  const { signOut: fbSignOut } = await import("firebase/auth");
  await fbSignOut(auth);
}
