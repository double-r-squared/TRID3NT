// GRACE-2 web — AuthGuard three-mode-matrix tests (job-0253, sprint-13.5).
//
// Verifies the load-bearing behavior of the production auth gate:
//
//   MODE 1 — Firebase DISABLED  → children render UNCHANGED (transparent
//            pass-through; pixel-identical dev/tailnet path). No guard chrome,
//            no sign-in surface, no sign-out affordance.
//   MODE 2 — Firebase ENABLED + signed-out  → minimal Google sign-in surface;
//            children NOT rendered. Also the auth-expired variant (4401)
//            surfaces the same surface with an "expired" note.
//   MODE 3 — Firebase ENABLED + signed-in   → children render + a "Sign out"
//            affordance (lives here, not in the dirty SettingsPopup.tsx).
//
// `../auth` is fully mocked so no real Firebase SDK is imported and the auth
// state is fully controllable. `useAuth` reads `onAuthChanged` + `authStatus`
// from `../auth`, so mocking that module drives the hook deterministically.

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, cleanup, act, fireEvent } from "@testing-library/react";
import type { AuthUser } from "../auth";

// ── Mock the auth module. The mock holds a single subscriber + a current
//    user so tests can flip auth state and re-render the guard. ──
let currentUser: AuthUser | null = null;
let authSubscriber: ((u: AuthUser | null) => void) | null = null;
const signInMock = vi.fn<() => Promise<void>>();
const signOutMock = vi.fn<() => Promise<void>>();

vi.mock("../auth", () => ({
  // useAuth reads status synchronously after each onAuthChanged callback.
  authStatus: () => (mockConfigured ? "ready" : "disabled"),
  isFirebaseConfigured: () => mockConfigured,
  onAuthChanged: (cb: (u: AuthUser | null) => void) => {
    authSubscriber = cb;
    // Fire once with the current user, mirroring the real subscription.
    cb(currentUser);
    return () => {
      authSubscriber = null;
    };
  },
  // GCP→AWS: Cognito Hosted UI redirect (email/password). useAuth now reads
  // the generic `signIn`. The redirect navigates away; tests just assert the
  // intent fired.
  signIn: () => signInMock(),
  signOut: () => signOutMock(),
}));

// Drives both isFirebaseConfigured() (via the mock) and is passed explicitly
// to the guard via forceConfigured where a test wants to be unambiguous.
let mockConfigured = false;

// Import AFTER the mock is registered.
import { AuthGuard } from "./AuthGuard";

const CHILD = <div data-testid="app-children">APP BODY</div>;

const GOOGLE_USER: AuthUser = {
  uid: "firebase-uid-123",
  displayName: "Test User",
  email: "test@example.com",
  photoURL: null,
  isAnonymous: false,
};

const ANON_USER: AuthUser = {
  uid: "anon-uid-999",
  displayName: null,
  email: null,
  photoURL: null,
  isAnonymous: true,
};

function setAuthState(user: AuthUser | null): void {
  currentUser = user;
  act(() => {
    authSubscriber?.(user);
  });
}

beforeEach(() => {
  currentUser = null;
  authSubscriber = null;
  mockConfigured = false;
  signInMock.mockReset();
  signInMock.mockResolvedValue(undefined);
  signOutMock.mockReset();
  signOutMock.mockResolvedValue(undefined);
});

afterEach(() => {
  cleanup();
});

// ───────────────────────────── MODE 1: disabled ─────────────────────────── //

describe("AuthGuard — MODE 1: Firebase disabled (pass-through)", () => {
  it("renders children unchanged when Firebase is disabled", () => {
    mockConfigured = false;
    render(<AuthGuard forceConfigured={false}>{CHILD}</AuthGuard>);
    expect(screen.getByTestId("app-children")).toHaveTextContent("APP BODY");
  });

  it("renders NO guard chrome (no sign-in surface, no sign-out, no pending frame)", () => {
    mockConfigured = false;
    render(<AuthGuard forceConfigured={false}>{CHILD}</AuthGuard>);
    expect(screen.queryByTestId("grace2-auth-guard-signin")).toBeNull();
    expect(screen.queryByTestId("grace2-auth-guard-signout")).toBeNull();
    expect(screen.queryByTestId("grace2-auth-guard-pending")).toBeNull();
  });

  it("is a transparent wrapper — output is exactly the children (snapshot-stable)", () => {
    mockConfigured = false;
    const { container } = render(
      <AuthGuard forceConfigured={false}>{CHILD}</AuthGuard>,
    );
    // The guard adds no wrapper element in disabled mode: the only node is the
    // child div itself.
    expect(container.innerHTML).toBe(
      '<div data-testid="app-children">APP BODY</div>',
    );
  });

  it("ignores authExpired in disabled mode (still passes children through)", () => {
    mockConfigured = false;
    render(
      <AuthGuard forceConfigured={false} authExpired>
        {CHILD}
      </AuthGuard>,
    );
    expect(screen.getByTestId("app-children")).toBeInTheDocument();
    expect(screen.queryByTestId("grace2-auth-guard-signin")).toBeNull();
  });
});

// ──────────────────────── MODE 2: enabled + signed-out ──────────────────── //

describe("AuthGuard — MODE 2: Firebase enabled + signed-out (sign-in surface)", () => {
  it("renders the minimal Cognito sign-in surface; children NOT rendered", () => {
    mockConfigured = true;
    currentUser = null;
    render(<AuthGuard forceConfigured={true}>{CHILD}</AuthGuard>);
    expect(screen.getByTestId("grace2-auth-guard-signin")).toBeInTheDocument();
    expect(screen.getByTestId("grace2-auth-guard-wordmark")).toHaveTextContent(
      "GRACE-2",
    );
    const signInBtn = screen.getByTestId("grace2-auth-guard-signin-btn");
    expect(signInBtn).toBeInTheDocument();
    expect(signInBtn).toHaveTextContent(/sign in/i);
    expect(screen.getByTestId("grace2-auth-guard-privacy")).toHaveAttribute(
      "href",
      "/privacy",
    );
    expect(screen.queryByTestId("app-children")).toBeNull();
  });

  it("offers NO anonymous option on the prod surface (Decision 6)", () => {
    mockConfigured = true;
    currentUser = null;
    render(<AuthGuard forceConfigured={true}>{CHILD}</AuthGuard>);
    // No anonymous CTA text anywhere on the surface.
    expect(screen.queryByText(/anonymous/i)).toBeNull();
    expect(screen.queryByText(/without saving/i)).toBeNull();
  });

  it("treats an anonymous user as signed-out (Decision 6 — anon is dev-only)", () => {
    mockConfigured = true;
    currentUser = ANON_USER;
    render(<AuthGuard forceConfigured={true}>{CHILD}</AuthGuard>);
    expect(screen.getByTestId("grace2-auth-guard-signin")).toBeInTheDocument();
    expect(screen.queryByTestId("app-children")).toBeNull();
  });

  it("clicking 'Sign in / Sign up' invokes the auth signIn helper (Hosted UI redirect)", () => {
    mockConfigured = true;
    currentUser = null;
    render(<AuthGuard forceConfigured={true}>{CHILD}</AuthGuard>);
    fireEvent.click(screen.getByTestId("grace2-auth-guard-signin-btn"));
    expect(signInMock).toHaveBeenCalledOnce();
  });

  it("a successful sign-in flips the surface to children (auth-state change)", () => {
    mockConfigured = true;
    currentUser = null;
    render(<AuthGuard forceConfigured={true}>{CHILD}</AuthGuard>);
    expect(screen.getByTestId("grace2-auth-guard-signin")).toBeInTheDocument();
    // Simulate Firebase reporting the signed-in user.
    setAuthState(GOOGLE_USER);
    expect(screen.getByTestId("app-children")).toBeInTheDocument();
    expect(screen.queryByTestId("grace2-auth-guard-signin")).toBeNull();
  });

  it("auth-expired (4401) shows the sign-in surface with the expired note", () => {
    mockConfigured = true;
    currentUser = GOOGLE_USER; // signed in, but token rejected by the gate
    render(
      <AuthGuard forceConfigured={true} authExpired>
        {CHILD}
      </AuthGuard>,
    );
    expect(screen.getByTestId("grace2-auth-guard-signin")).toBeInTheDocument();
    expect(screen.getByTestId("grace2-auth-guard-expired")).toHaveTextContent(
      /session expired/i,
    );
    expect(screen.queryByTestId("app-children")).toBeNull();
  });
});

// ──────────────────────── MODE 3: enabled + signed-in ───────────────────── //

describe("AuthGuard — MODE 3: Firebase enabled + signed-in (children + sign-out)", () => {
  it("renders children when a non-anonymous user is signed in", () => {
    mockConfigured = true;
    currentUser = GOOGLE_USER;
    render(<AuthGuard forceConfigured={true}>{CHILD}</AuthGuard>);
    expect(screen.getByTestId("app-children")).toHaveTextContent("APP BODY");
    expect(screen.queryByTestId("grace2-auth-guard-signin")).toBeNull();
  });

  it("renders NO sign-out affordance in the guard (ux-batch-1 F12: Sign out lives in Settings)", () => {
    // The fixed top-right sign-out control was removed; sign-out now lives only
    // in the Settings page (SettingsPopup.tsx, wired to App.tsx handleSignOut).
    // MODE 3 is a transparent pass-through once signed-in.
    mockConfigured = true;
    currentUser = GOOGLE_USER;
    render(<AuthGuard forceConfigured={true}>{CHILD}</AuthGuard>);
    expect(screen.queryByTestId("grace2-auth-guard-signout")).toBeNull();
    expect(screen.getByTestId("app-children")).toBeInTheDocument();
  });
});
