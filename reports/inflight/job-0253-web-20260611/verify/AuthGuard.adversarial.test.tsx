// CORRECTNESS-LENS adversarial tests for job-0253 AuthGuard (panel-authored,
// NOT the runner's tests). Attacks the guard matrix:
//   - disabled mode must be byte-identical to an UNGUARDED render (diffed
//     against a render I produce myself, multi-node children — not a
//     hardcoded string).
//   - enabled + anonymous user must NOT pass (Decision 6), with multi-node
//     children to prove no wrapper element leaks.
//   - useAuth must unsubscribe on unmount (no listener leak across remounts).

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, cleanup, act } from "@testing-library/react";
import type { AuthUser } from "../auth";

let currentUser: AuthUser | null = null;
let authSubscriber: ((u: AuthUser | null) => void) | null = null;
let subscribeCount = 0;
let unsubscribeCount = 0;
let mockConfigured = false;

vi.mock("../auth", () => ({
  authStatus: () => (mockConfigured ? "ready" : "disabled"),
  isFirebaseConfigured: () => mockConfigured,
  onAuthChanged: (cb: (u: AuthUser | null) => void) => {
    subscribeCount += 1;
    authSubscriber = cb;
    cb(currentUser);
    return () => {
      unsubscribeCount += 1;
      authSubscriber = null;
    };
  },
  signInWithGoogle: () => Promise.resolve(null),
  signOut: () => Promise.resolve(),
}));

import { AuthGuard } from "./AuthGuard";

const ANON_USER: AuthUser = {
  uid: "anon-1", displayName: null, email: null, photoURL: null, isAnonymous: true,
};
const GOOGLE_USER: AuthUser = {
  uid: "g-1", displayName: "G", email: "g@x.com", photoURL: null, isAnonymous: false,
};

// Multi-node children so a wrapper element (even a fragment-less <div>) would
// show up as added DOM. Includes attributes + nested structure.
const MULTI_CHILD = (
  <>
    <header data-testid="hdr" className="top">H</header>
    <main id="m">
      <span>x</span>
      <button type="button">click</button>
    </main>
  </>
);

beforeEach(() => {
  currentUser = null;
  authSubscriber = null;
  subscribeCount = 0;
  unsubscribeCount = 0;
  mockConfigured = false;
});
afterEach(() => cleanup());

describe("ADVERSARIAL: disabled mode is byte-identical to unguarded", () => {
  it("guarded innerHTML === unguarded innerHTML (multi-node, diffed by me)", () => {
    mockConfigured = false;
    // Unguarded baseline I produce myself.
    const unguarded = render(<>{MULTI_CHILD}</>);
    const baseline = unguarded.container.innerHTML;
    cleanup();
    // Guarded, disabled.
    const guarded = render(
      <AuthGuard forceConfigured={false}>{MULTI_CHILD}</AuthGuard>,
    );
    expect(guarded.container.innerHTML).toBe(baseline);
    // And there is no fixed-position sign-out / overlay node anywhere.
    expect(guarded.container.querySelector('[data-testid^="grace2-auth-guard"]')).toBeNull();
  });

  it("disabled mode does NOT subscribe to auth in a way that adds DOM, and child count is unchanged", () => {
    mockConfigured = false;
    const { container } = render(
      <AuthGuard forceConfigured={false}>{MULTI_CHILD}</AuthGuard>,
    );
    // top-level children of the render container: header + main, nothing else.
    expect(container.children.length).toBe(2);
    expect(container.children[0].tagName).toBe("HEADER");
    expect(container.children[1].tagName).toBe("MAIN");
  });
});

describe("ADVERSARIAL: enabled + anonymous must NOT pass the guard (Decision 6)", () => {
  it("anonymous user → sign-in surface, children absent, no wrapper leak", () => {
    mockConfigured = true;
    currentUser = ANON_USER;
    const { container, queryByTestId } = render(
      <AuthGuard forceConfigured={true}>{MULTI_CHILD}</AuthGuard>,
    );
    expect(queryByTestId("grace2-auth-guard-signin")).not.toBeNull();
    // The app children must be GONE — not merely hidden behind an overlay.
    expect(container.querySelector('[data-testid="hdr"]')).toBeNull();
    expect(container.querySelector("#m")).toBeNull();
  });

  it("anonymous user with authExpired also stays on sign-in (no children flash)", () => {
    mockConfigured = true;
    currentUser = ANON_USER;
    const { queryByTestId } = render(
      <AuthGuard forceConfigured={true} authExpired>{MULTI_CHILD}</AuthGuard>,
    );
    expect(queryByTestId("grace2-auth-guard-signin")).not.toBeNull();
    expect(queryByTestId("hdr")).toBeNull();
  });

  it("anonymous → upgrade to google flips to children (auth-state change, in place)", () => {
    mockConfigured = true;
    currentUser = ANON_USER;
    const { queryByTestId } = render(
      <AuthGuard forceConfigured={true}>{MULTI_CHILD}</AuthGuard>,
    );
    expect(queryByTestId("grace2-auth-guard-signin")).not.toBeNull();
    act(() => { authSubscriber?.(GOOGLE_USER); });
    expect(queryByTestId("hdr")).not.toBeNull();
    expect(queryByTestId("grace2-auth-guard-signin")).toBeNull();
  });
});

describe("ADVERSARIAL: useAuth subscription lifecycle (no listener leak)", () => {
  it("unsubscribes exactly once on unmount; no net leaked listeners across remounts", () => {
    mockConfigured = true;
    currentUser = GOOGLE_USER;
    const a = render(<AuthGuard forceConfigured={true}>{MULTI_CHILD}</AuthGuard>);
    expect(subscribeCount).toBe(1);
    expect(unsubscribeCount).toBe(0);
    a.unmount();
    expect(unsubscribeCount).toBe(1);
    // Remount: a fresh subscription, the old one already torn down.
    const b = render(<AuthGuard forceConfigured={true}>{MULTI_CHILD}</AuthGuard>);
    expect(subscribeCount).toBe(2);
    b.unmount();
    expect(unsubscribeCount).toBe(2);
    // Net leaked = subscribes - unsubscribes = 0.
    expect(subscribeCount - unsubscribeCount).toBe(0);
  });
});
