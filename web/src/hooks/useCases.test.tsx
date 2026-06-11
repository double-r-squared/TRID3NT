// job-0273: the auto-create case-open → case-list race.
//
// Observed live (Playwright WS capture, 2026-06-10): the server emits
// case-open 27ms BEFORE the refreshed case-list. With a non-empty rail, the
// tombstone guard saw activeCaseId pointing at a Case not yet in `cases`
// and bounced the user back to root — while Chat's adoption had already
// cleared the root stream, leaving a fully empty chat for the whole turn.
// onCaseOpen now optimistically upserts the envelope's CaseSummary.

import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { CaseSessionState, CaseSummary } from "../contracts";
import { useCases } from "./useCases";

function summary(id: string, title: string): CaseSummary {
  return {
    case_id: id,
    title,
    created_at: "2026-06-11T00:00:00Z",
    updated_at: "2026-06-11T00:00:00Z",
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

describe("useCases auto-create race (job-0273)", () => {
  it("keeps activeCaseId when case-open precedes the refreshed case-list", () => {
    const { result } = renderHook(() =>
      useCases({ sendCaseCommand: noopSend as never, isSignedIn: false }),
    );

    // Rail already holds an older Case (the guard's arming condition).
    act(() => {
      result.current.onCaseList({ cases: [summary("01OLD", "Old Case")] });
    });

    // Auto-create: case-open arrives FIRST, before the refreshed list.
    act(() => {
      result.current.onCaseOpen({
        session_state: session("01NEW", "Fresh Auto Case"),
      });
    });

    // Pre-fix: the tombstone effect bounced this back to null.
    expect(result.current.activeCaseId).toBe("01NEW");
    expect(
      result.current.cases.some((c) => c.case_id === "01NEW"),
    ).toBe(true);

    // The authoritative case-list canonicalizes without disturbing active.
    act(() => {
      result.current.onCaseList({
        cases: [summary("01OLD", "Old Case"), summary("01NEW", "Fresh Auto Case")],
      });
    });
    expect(result.current.activeCaseId).toBe("01NEW");
  });

  it("still clears activeCaseId when the active Case is tombstoned", () => {
    const { result } = renderHook(() =>
      useCases({ sendCaseCommand: noopSend as never, isSignedIn: false }),
    );
    act(() => {
      result.current.onCaseList({ cases: [summary("01A", "A")] });
    });
    act(() => {
      result.current.onCaseOpen({ session_state: session("01A", "A") });
    });
    expect(result.current.activeCaseId).toBe("01A");

    // Delete flow: refreshed list no longer contains the active Case.
    act(() => {
      result.current.onCaseList({ cases: [summary("01B", "B")] });
    });
    expect(result.current.activeCaseId).toBeNull();
  });
});
