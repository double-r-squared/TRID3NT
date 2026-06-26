// ACTIVE-CASE RESTORE (NATE 2026-06-26) - persist + restore the open Case id.
//
// THE BUG: on reload (felt most on mobile) the app dropped to the Cases LIST
// instead of staying in the open Case, because nothing persisted the active
// Case id client-side: useCases inited activeCaseId to null every load, and the
// server's reconnect path (_handle_session_resume) re-emits session-state +
// case-list but NEVER a case-open. So the open Case was forgotten on reload.
//
// THE FIX (this file locks in): useCases mirrors EVERY active-Case transition
// to localStorage (LS_ACTIVE_CASE) and SEEDS activeCaseId lazily from it on
// mount, exposing the seed as `restoredActiveCaseId` so App can dispatch one
// selectCase(restored) after the socket is wired. A stale / deleted persisted
// id self-heals via the existing archived/deleted reconcile + tombstones on the
// next authoritative case-list.

import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { CaseSessionState, CaseSummary } from "../contracts";
import { LS_ACTIVE_CASE, useCases } from "./useCases";

function summary(id: string, title: string): CaseSummary {
  return {
    case_id: id,
    title,
    created_at: "2026-06-26T00:00:00Z",
    updated_at: "2026-06-26T00:00:00Z",
    status: "active",
  } as CaseSummary;
}

function session(id: string, title: string): CaseSessionState {
  return {
    case: summary(id, title),
    chat_history: [],
    loaded_layers: [],
  } as unknown as CaseSessionState;
}

const noopSend = () => {};

beforeEach(() => {
  try {
    localStorage.clear();
  } catch {
    /* ignore */
  }
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("useCases active-Case persistence (NATE 2026-06-26)", () => {
  it("seeds activeCaseId from localStorage and exposes it as restoredActiveCaseId", () => {
    localStorage.setItem(LS_ACTIVE_CASE, "01RESTORED");

    const { result } = renderHook(() =>
      useCases({ sendCaseCommand: noopSend as never, isSignedIn: true }),
    );

    // The persisted id is the initial active Case AND the restore seed App reads.
    expect(result.current.activeCaseId).toBe("01RESTORED");
    expect(result.current.restoredActiveCaseId).toBe("01RESTORED");
  });

  it("starts at null with restoredActiveCaseId null when nothing is persisted", () => {
    const { result } = renderHook(() =>
      useCases({ sendCaseCommand: noopSend as never, isSignedIn: true }),
    );

    expect(result.current.activeCaseId).toBeNull();
    expect(result.current.restoredActiveCaseId).toBeNull();
  });

  it("mirrors selectCase + onCaseOpen transitions into localStorage", () => {
    const { result } = renderHook(() =>
      useCases({ sendCaseCommand: noopSend as never, isSignedIn: true }),
    );

    // selectCase sets the active id locally -> persisted immediately.
    act(() => {
      result.current.selectCase("01SELECTED");
    });
    expect(result.current.activeCaseId).toBe("01SELECTED");
    expect(localStorage.getItem(LS_ACTIVE_CASE)).toBe("01SELECTED");

    // A live case-open for a different Case re-stamps the mirror.
    act(() => {
      result.current.onCaseOpen({ session_state: session("01OPENED", "Opened") });
    });
    expect(result.current.activeCaseId).toBe("01OPENED");
    expect(localStorage.getItem(LS_ACTIVE_CASE)).toBe("01OPENED");
  });

  it("clearActive (exit-to-root) removes the persisted id so reload lands on the list", () => {
    const { result } = renderHook(() =>
      useCases({ sendCaseCommand: noopSend as never, isSignedIn: true }),
    );

    act(() => {
      result.current.selectCase("01OPEN");
    });
    expect(localStorage.getItem(LS_ACTIVE_CASE)).toBe("01OPEN");

    act(() => {
      result.current.clearActive();
    });
    expect(result.current.activeCaseId).toBeNull();
    expect(localStorage.getItem(LS_ACTIVE_CASE)).toBeNull();
  });

  it("clearActive on delete-of-active removes the persisted id (App's delete-active path)", () => {
    // deleteCase alone only drops the Case from the rail; App calls clearActive
    // when the deleted Case is the active one (the exit-to-root path). That
    // clearActive is what removes the persisted id so a reload lands on the list.
    const { result } = renderHook(() =>
      useCases({ sendCaseCommand: noopSend as never, isSignedIn: true }),
    );

    act(() => {
      result.current.onCaseList({ cases: [summary("01DOOMED", "Doomed")] });
      result.current.onCaseOpen({ session_state: session("01DOOMED", "Doomed") });
    });
    expect(localStorage.getItem(LS_ACTIVE_CASE)).toBe("01DOOMED");

    act(() => {
      result.current.deleteCase("01DOOMED");
      result.current.clearActive();
    });
    expect(result.current.activeCaseId).toBeNull();
    expect(localStorage.getItem(LS_ACTIVE_CASE)).toBeNull();
  });

  it("a stale persisted id self-heals when an authoritative case-list omits it", () => {
    // Reload restored a Case that has since been deleted server-side.
    localStorage.setItem(LS_ACTIVE_CASE, "01GONE");

    const { result } = renderHook(() =>
      useCases({ sendCaseCommand: noopSend as never, isSignedIn: true }),
    );
    expect(result.current.activeCaseId).toBe("01GONE");

    // The next authoritative case-list (cold fetch / live) does NOT contain it.
    act(() => {
      result.current.onCaseList({ cases: [summary("01LIVE", "Live Case")] }, true);
    });

    // The archived/deleted reconcile effect clears the active state, and the
    // mirror removes the dead id so the next reload lands on the list.
    expect(result.current.activeCaseId).toBeNull();
    expect(localStorage.getItem(LS_ACTIVE_CASE)).toBeNull();
  });

  it("tolerates localStorage being unavailable (no throw on read or write)", () => {
    // Simulate private-mode / quota: getItem + setItem throw.
    const getSpy = vi
      .spyOn(Storage.prototype, "getItem")
      .mockImplementation(() => {
        throw new Error("storage disabled");
      });
    const setSpy = vi
      .spyOn(Storage.prototype, "setItem")
      .mockImplementation(() => {
        throw new Error("storage disabled");
      });

    const { result } = renderHook(() =>
      useCases({ sendCaseCommand: noopSend as never, isSignedIn: true }),
    );
    // Read failure degrades to no restore (null), not a crash.
    expect(result.current.activeCaseId).toBeNull();
    expect(result.current.restoredActiveCaseId).toBeNull();

    // Write failure (the mirror effect) must not throw either.
    expect(() => {
      act(() => {
        result.current.selectCase("01ANY");
      });
    }).not.toThrow();
    expect(result.current.activeCaseId).toBe("01ANY");

    getSpy.mockRestore();
    setSpy.mockRestore();
  });
});
