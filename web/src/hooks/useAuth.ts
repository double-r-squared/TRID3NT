// GRACE-2 web — useAuth hook (job-0253, sprint-13.5 Stage 1).
//
// A thin React adapter over `../auth` (the Wave 2 / job-0123 Firebase client
// surface). It exists so components can render auth-aware UI without importing
// `firebase/auth` types or calling `onAuthChanged` plumbing by hand.
//
// What it exposes:
//   - `user`     — the library-agnostic `AuthUser | null` (re-rendered on every
//                  Firebase auth-state change).
//   - `status`   — the `AuthInitStatus` ("disabled" | "initializing" | "ready"
//                  | "failed"). "disabled" means VITE_FIREBASE_PROJECT_ID is
//                  absent — the load-bearing dev/tailnet path.
//   - `resolved` — false until the first auth-state callback has fired, so the
//                  guard can avoid a sign-in flash before Firebase reports the
//                  signed-in user on a configured project.
//   - `signInWithGoogle` / `signOut` — pass-throughs to `../auth`, stable
//                  references.
//
// Invariant note (web Domain Discipline): this hook renders identity and emits
// sign-in/out intent only. It computes no user-facing numbers and holds no
// Firebase objects beyond the `AuthUser` projection that `auth.ts` already
// produces. No Firebase types leak across this boundary.

import { useCallback, useEffect, useState } from "react";
import {
  type AuthInitStatus,
  type AuthUser,
  authStatus,
  onAuthChanged,
  signInWithGoogle as authSignInWithGoogle,
  signOut as authSignOut,
} from "../auth";

/** Reactive auth snapshot + intent emitters. No Firebase types cross this seam. */
export interface UseAuthResult {
  /** Current signed-in identity, or null when signed out / Firebase disabled. */
  user: AuthUser | null;
  /** Firebase Auth subsystem status. "disabled" ⇒ env vars absent (dev/tailnet). */
  status: AuthInitStatus;
  /**
   * False until the first `onAuthChanged` callback fires. On a configured
   * project this prevents a sign-in flash before Firebase restores the
   * persisted session; in "disabled" mode it flips true on the synchronous
   * `cb(null)` the auth subsystem delivers immediately.
   */
  resolved: boolean;
  /** Begin the Google popup sign-in flow. Throws when Firebase is disabled. */
  signInWithGoogle: () => Promise<AuthUser | null>;
  /** Sign the current user out. No-op when Firebase is disabled. */
  signOut: () => Promise<void>;
}

/**
 * Subscribe to Firebase auth state and expose a render-friendly snapshot.
 *
 * Mirrors the App.tsx auth subscription that already existed (job-0123) but as
 * a reusable hook so `AuthGuard` (and any future auth-aware component) shares
 * one source of truth. Safe to mount many times — each instance owns its own
 * subscription and unsubscribes on unmount.
 */
export function useAuth(): UseAuthResult {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [status, setStatus] = useState<AuthInitStatus>(() => authStatus());
  const [resolved, setResolved] = useState<boolean>(false);

  useEffect(() => {
    // `onAuthChanged` fires once with the current user (or null) after init,
    // then on every subsequent state change. In "disabled" mode it fires once
    // with null and never again — a stable signed-out snapshot.
    const unsub = onAuthChanged((u) => {
      setUser(u);
      // Read the status synchronously after each callback: `initAuth` has
      // resolved by the time the first callback lands, so the cached status
      // is now accurate ("ready" on a configured project, "disabled" / "failed"
      // otherwise).
      setStatus(authStatus());
      setResolved(true);
    });
    return unsub;
  }, []);

  const signInWithGoogle = useCallback(() => authSignInWithGoogle(), []);
  const signOut = useCallback(() => authSignOut(), []);

  return { user, status, resolved, signInWithGoogle, signOut };
}
