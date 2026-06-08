// GRACE-2 web — AuthPanel (job-0123, sprint-12-mega Wave 2).
//
// Floating top-area panel that exposes Firebase Auth state and lets the user
// sign in (Google / anonymous) or sign out. Renders next to the chat
// hamburger (top-right region) — when the chat panel is open the AuthPanel
// sits to the LEFT of where the hamburger would go; when chat is collapsed,
// the AuthPanel sits to the LEFT of the chat hamburger so the two don't
// overlap.
//
// Visual styling matches the existing overlay panels (LayerPanel,
// LayerLegend, hamburger buttons): rgba(20,20,25,0.85) panel background,
// 1px #444 border, 6px radius, dark-theme friendly. Hide-able if Firebase
// is disabled (anonymous-only dev mode without env vars) — surfaces a
// small "anonymous" badge instead of empty buttons.
//
// SRS H.3 implementation: anonymous + Google sign-in available;
// linkWithCredential (anonymous → authenticated upgrade) is deferred to
// the next web job in the Auth/Users track (surfaced as OQ).

import { useEffect, useState } from "react";
import {
  AuthUser,
  authStatus,
  isFirebaseConfigured,
  onAuthChanged,
  signInAnonymous,
  signInWithGoogle,
  signOut,
} from "../auth";

export interface AuthPanelProps {
  /** Position the panel relative to the right edge (px). Default 60 leaves room for the chat hamburger. */
  rightOffset?: number;
  /** Override the auth subscription for tests (returns unsubscribe). */
  subscribeAuth?: (cb: (u: AuthUser | null) => void) => () => void;
  /** Override the sign-in handlers for tests. */
  onSignInGoogle?: () => Promise<AuthUser | null>;
  onSignInAnonymous?: () => Promise<AuthUser | null>;
  onSignOut?: () => Promise<void>;
}

const panelStyle: React.CSSProperties = {
  position: "absolute",
  top: 12,
  background: "rgba(20,20,25,0.85)",
  border: "1px solid #444",
  borderRadius: 6,
  color: "#ccc",
  padding: "8px 10px",
  fontSize: 13,
  zIndex: 30,
  display: "flex",
  flexDirection: "row",
  alignItems: "center",
  gap: 8,
  maxWidth: 320,
};

const buttonStyle: React.CSSProperties = {
  background: "rgba(40,40,50,0.9)",
  border: "1px solid #555",
  borderRadius: 4,
  color: "#ddd",
  padding: "4px 8px",
  cursor: "pointer",
  fontSize: 12,
  fontFamily: "inherit",
  lineHeight: 1.2,
};

const avatarStyle: React.CSSProperties = {
  width: 24,
  height: 24,
  borderRadius: "50%",
  background: "#444",
  objectFit: "cover",
  display: "block",
};

function initials(name: string | null, email: string | null): string {
  const src = name ?? email ?? "?";
  const trimmed = src.trim();
  if (!trimmed) return "?";
  const parts = trimmed.split(/\s+/).filter((p) => p.length > 0);
  if (parts.length >= 2) {
    const a = parts[0]?.[0] ?? "";
    const b = parts[1]?.[0] ?? "";
    return (a + b).toUpperCase();
  }
  return trimmed.slice(0, 2).toUpperCase();
}

export function AuthPanel({
  rightOffset = 60,
  subscribeAuth,
  onSignInGoogle,
  onSignInAnonymous,
  onSignOut,
}: AuthPanelProps): JSX.Element {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [busy, setBusy] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const firebaseConfigured = isFirebaseConfigured();

  useEffect(() => {
    const subscribe = subscribeAuth ?? onAuthChanged;
    const unsub = subscribe((u) => setUser(u));
    return unsub;
  }, [subscribeAuth]);

  async function handleGoogle(): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      const fn = onSignInGoogle ?? signInWithGoogle;
      await fn();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function handleAnonymous(): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      const fn = onSignInAnonymous ?? signInAnonymous;
      const result = await fn();
      // When Firebase is not configured, signInAnonymous resolves null —
      // surface a friendly explanation rather than appearing to do nothing.
      if (!firebaseConfigured && result == null) {
        setError("Anonymous mode (no Firebase project): session proceeds without auth-token.");
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function handleSignOut(): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      const fn = onSignOut ?? signOut;
      await fn();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const style: React.CSSProperties = { ...panelStyle, right: rightOffset };

  if (user) {
    const label =
      user.displayName ??
      user.email ??
      (user.isAnonymous ? "Anonymous user" : `User ${user.uid.slice(0, 6)}`);
    return (
      <div data-testid="grace2-auth-panel" data-auth-state="signed-in" style={style}>
        {user.photoURL ? (
          <img
            data-testid="grace2-auth-avatar"
            src={user.photoURL}
            alt=""
            style={avatarStyle}
          />
        ) : (
          <span
            data-testid="grace2-auth-avatar-initials"
            style={{
              ...avatarStyle,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontSize: 11,
              color: "#ddd",
            }}
          >
            {initials(user.displayName, user.email)}
          </span>
        )}
        <span
          data-testid="grace2-auth-username"
          style={{
            maxWidth: 140,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
          title={label}
        >
          {label}
          {user.isAnonymous && (
            <span style={{ marginLeft: 4, color: "#888", fontSize: 11 }}>
              (anon)
            </span>
          )}
        </span>
        <button
          data-testid="grace2-auth-signout"
          disabled={busy}
          onClick={handleSignOut}
          style={buttonStyle}
          aria-label="Sign out"
        >
          Sign out
        </button>
        {error && (
          <span
            data-testid="grace2-auth-error"
            style={{ color: "#e88", fontSize: 11 }}
            role="alert"
          >
            {error}
          </span>
        )}
      </div>
    );
  }

  // Signed-out view
  const status = authStatus();
  return (
    <div
      data-testid="grace2-auth-panel"
      data-auth-state="signed-out"
      data-auth-status={status}
      style={style}
    >
      <button
        data-testid="grace2-auth-google"
        disabled={busy || !firebaseConfigured}
        onClick={handleGoogle}
        style={buttonStyle}
        aria-label="Sign in with Google"
        title={firebaseConfigured ? "Sign in with Google" : "Google sign-in requires VITE_FIREBASE_* env vars"}
      >
        Sign in with Google
      </button>
      <button
        data-testid="grace2-auth-anonymous"
        disabled={busy}
        onClick={handleAnonymous}
        style={buttonStyle}
        aria-label="Continue as anonymous"
      >
        Continue as anonymous
      </button>
      {error && (
        <span
          data-testid="grace2-auth-error"
          style={{ color: "#e88", fontSize: 11 }}
          role="alert"
        >
          {error}
        </span>
      )}
    </div>
  );
}
