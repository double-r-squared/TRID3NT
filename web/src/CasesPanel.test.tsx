// GRACE-2 web — CasesPanel + ConfirmationDialog + useCases tests
// (job-0137 + job-0143).
//
// Verifies:
//   1. CasesPanel renders the empty state when cases=[].
//   2. CasesPanel renders one row per CaseSummary with title + bbox + hazard
//      + relative timestamp.
//   3. Active-case highlight: only the row whose id matches activeCaseId
//      gets data-active="true".
//   4. "+ New Case" button calls onCreate.
//   5. Row click calls onSelect with the row's case_id.
//   6. Pencil → inline edit → Enter calls onRename with the new title.
//   7. Archive button calls onArchive with the row's case_id.
//   8. Delete button opens ConfirmationDialog; Confirm calls onDelete; Cancel
//      does NOT call onDelete.
//   9. ConfirmationDialog: Esc cancels; backdrop click cancels.
//      (job-0143: PersistenceChip removed — auth state lives in Settings.)
//  11. useCases: createCase emits case-command(create) with optional title arg.
//  12. useCases: selectCase emits case-command(select, case_id).
//  13. useCases: renameCase emits case-command(rename, case_id, {title}).
//  14. useCases: deleteCase emits case-command(delete, case_id).
//  15. useCases: onCaseList updates cases list and clears in-flight.
//  16. useCases: onCaseOpen with session_state hydrates activeCaseId +
//      activeSession; null clears them.
//  17. useCases: persistenceState transitions
//      anonymous → saved → saving → saved.
//  18. formatRelative pure function: "just now", "5m ago", "2h ago", "3d ago",
//      "Jun 4" (over a week).
//  19. formatBbox pure function: SW corner formatted with hemispheres.

import { describe, it, expect, vi, afterEach } from "vitest";
import {
  render,
  screen,
  fireEvent,
  cleanup,
  act,
} from "@testing-library/react";
import { renderHook } from "@testing-library/react";
import {
  CasesPanel,
  formatRelative,
  formatBbox,
} from "./components/CasesPanel";
import { ConfirmationDialog } from "./components/ConfirmationDialog";
import { useCases } from "./hooks/useCases";
import type {
  CaseListEnvelopePayload,
  CaseOpenEnvelopePayload,
  CaseSessionState,
  CaseSummary,
} from "./contracts";

afterEach(() => cleanup());

// --- Fixtures ----------------------------------------------------------- //

const NOW = new Date("2026-06-08T12:00:00.000Z");

const CASE_FORT_MYERS: CaseSummary = {
  schema_version: "v1",
  case_id: "01ABCDEFGHJKMNPQRSTVWX0001",
  title: "Hurricane Ian — Fort Myers",
  created_at: "2026-06-05T10:00:00.000Z",
  updated_at: "2026-06-08T11:55:00.000Z",
  status: "active",
  bbox: [-82.0, 26.5, -81.7, 26.8],
  primary_hazard: "flood",
  layer_summary: ["layer-1", "layer-2"],
  qgs_project_uri: "gs://grace-2-hazard-prod-qgs/case-1.qgs",
};

const CASE_NORCAL_FIRE: CaseSummary = {
  schema_version: "v1",
  case_id: "01ABCDEFGHJKMNPQRSTVWX0002",
  title: "NorCal fire 2020",
  created_at: "2026-06-01T10:00:00.000Z",
  updated_at: "2026-06-07T10:00:00.000Z",
  status: "active",
  bbox: [-123.5, 38.0, -122.0, 39.5],
  primary_hazard: "wildfire",
  layer_summary: [],
  qgs_project_uri: null,
};

const CASE_OLD_ARCHIVE: CaseSummary = {
  schema_version: "v1",
  case_id: "01ABCDEFGHJKMNPQRSTVWX0003",
  title: "Old archive",
  created_at: "2026-05-01T10:00:00.000Z",
  updated_at: "2026-05-15T10:00:00.000Z",
  status: "archived",
};

// --- CasesPanel render tests ------------------------------------------- //

describe("CasesPanel", () => {
  it("renders the empty state when cases is empty", () => {
    render(
      <CasesPanel
        cases={[]}
        activeCaseId={null}
        onCreate={vi.fn()}
        onSelect={vi.fn()}
        onRename={vi.fn()}
        onArchive={vi.fn()}
        onDelete={vi.fn()}
      />,
    );
    expect(screen.getByTestId("grace2-cases-empty")).toBeTruthy();
    expect(screen.getByTestId("grace2-cases-empty").textContent).toMatch(
      /Start a Case/i,
    );
  });

  it("renders the +New Case button and fires onCreate when clicked", () => {
    const onCreate = vi.fn();
    render(
      <CasesPanel
        cases={[]}
        activeCaseId={null}
        onCreate={onCreate}
        onSelect={vi.fn()}
        onRename={vi.fn()}
        onArchive={vi.fn()}
        onDelete={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId("grace2-cases-new"));
    expect(onCreate).toHaveBeenCalledTimes(1);
  });

  it("renders one row per case with title + hazard + bbox + updated", () => {
    render(
      <CasesPanel
        cases={[CASE_FORT_MYERS, CASE_NORCAL_FIRE]}
        activeCaseId={null}
        onCreate={vi.fn()}
        onSelect={vi.fn()}
        onRename={vi.fn()}
        onArchive={vi.fn()}
        onDelete={vi.fn()}
      />,
    );
    const rows = screen.getAllByTestId("grace2-case-row");
    expect(rows).toHaveLength(2);
    // Titles
    const titles = screen.getAllByTestId("grace2-case-row-title");
    expect(titles.map((n) => n.textContent)).toEqual(
      expect.arrayContaining(["Hurricane Ian — Fort Myers", "NorCal fire 2020"]),
    );
    // Hazards
    const hazards = screen.getAllByTestId("grace2-case-row-hazard");
    expect(hazards.map((n) => n.textContent)).toEqual(
      expect.arrayContaining(["flood", "wildfire"]),
    );
    // Bbox indicator at least exists for Fort Myers row.
    const bbox = screen.getAllByTestId("grace2-case-row-bbox");
    expect(bbox.length).toBeGreaterThan(0);
  });

  it("highlights only the active-case row", () => {
    render(
      <CasesPanel
        cases={[CASE_FORT_MYERS, CASE_NORCAL_FIRE]}
        activeCaseId={CASE_FORT_MYERS.case_id}
        onCreate={vi.fn()}
        onSelect={vi.fn()}
        onRename={vi.fn()}
        onArchive={vi.fn()}
        onDelete={vi.fn()}
      />,
    );
    const activeRows = screen.getAllByTestId("grace2-case-row").filter(
      (r) => r.getAttribute("data-active") === "true",
    );
    expect(activeRows).toHaveLength(1);
    expect(activeRows[0]!.getAttribute("data-case-id")).toBe(
      CASE_FORT_MYERS.case_id,
    );
  });

  it("clicking a row calls onSelect with that row's case_id", () => {
    const onSelect = vi.fn();
    render(
      <CasesPanel
        cases={[CASE_FORT_MYERS, CASE_NORCAL_FIRE]}
        activeCaseId={null}
        onCreate={vi.fn()}
        onSelect={onSelect}
        onRename={vi.fn()}
        onArchive={vi.fn()}
        onDelete={vi.fn()}
      />,
    );
    const rows = screen.getAllByTestId("grace2-case-row");
    const norcalRow = rows.find(
      (r) => r.getAttribute("data-case-id") === CASE_NORCAL_FIRE.case_id,
    )!;
    fireEvent.click(norcalRow);
    expect(onSelect).toHaveBeenCalledWith(CASE_NORCAL_FIRE.case_id);
  });

  it("inline rename via pencil → Enter calls onRename with new title", () => {
    const onRename = vi.fn();
    render(
      <CasesPanel
        cases={[CASE_FORT_MYERS]}
        activeCaseId={null}
        onCreate={vi.fn()}
        onSelect={vi.fn()}
        onRename={onRename}
        onArchive={vi.fn()}
        onDelete={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId("grace2-case-row-rename"));
    const input = screen.getByTestId(
      "grace2-case-row-rename-input",
    ) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "Ian Lee County" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onRename).toHaveBeenCalledWith(
      CASE_FORT_MYERS.case_id,
      "Ian Lee County",
    );
  });

  it("archive button calls onArchive with the row's case_id", () => {
    const onArchive = vi.fn();
    render(
      <CasesPanel
        cases={[CASE_FORT_MYERS]}
        activeCaseId={null}
        onCreate={vi.fn()}
        onSelect={vi.fn()}
        onRename={vi.fn()}
        onArchive={onArchive}
        onDelete={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId("grace2-case-row-archive"));
    expect(onArchive).toHaveBeenCalledWith(CASE_FORT_MYERS.case_id);
  });

  it("delete button opens the confirmation dialog; Cancel does NOT fire onDelete", () => {
    const onDelete = vi.fn();
    render(
      <CasesPanel
        cases={[CASE_FORT_MYERS]}
        activeCaseId={null}
        onCreate={vi.fn()}
        onSelect={vi.fn()}
        onRename={vi.fn()}
        onArchive={vi.fn()}
        onDelete={onDelete}
      />,
    );
    expect(screen.queryByTestId("grace2-case-delete-dialog")).toBeNull();
    fireEvent.click(screen.getByTestId("grace2-case-row-delete"));
    expect(screen.getByTestId("grace2-case-delete-dialog")).toBeTruthy();
    fireEvent.click(screen.getByTestId("grace2-case-delete-dialog-cancel"));
    expect(onDelete).not.toHaveBeenCalled();
  });

  it("delete confirmation Confirm calls onDelete with the row's case_id", () => {
    const onDelete = vi.fn();
    render(
      <CasesPanel
        cases={[CASE_FORT_MYERS]}
        activeCaseId={null}
        onCreate={vi.fn()}
        onSelect={vi.fn()}
        onRename={vi.fn()}
        onArchive={vi.fn()}
        onDelete={onDelete}
      />,
    );
    fireEvent.click(screen.getByTestId("grace2-case-row-delete"));
    fireEvent.click(screen.getByTestId("grace2-case-delete-dialog-confirm"));
    expect(onDelete).toHaveBeenCalledWith(CASE_FORT_MYERS.case_id);
  });

  it("EXCLUDES archived/deleted cases from the rail (job-0266)", () => {
    const CASE_DELETED = {
      ...CASE_NORCAL_FIRE,
      case_id: "01ABCDEFGHJKMNPQRSTVWX0009",
      title: "Deleted case",
      status: "deleted" as const,
    };
    render(
      <CasesPanel
        cases={[CASE_OLD_ARCHIVE, CASE_FORT_MYERS, CASE_NORCAL_FIRE, CASE_DELETED]}
        activeCaseId={null}
        onCreate={vi.fn()}
        onSelect={vi.fn()}
        onRename={vi.fn()}
        onArchive={vi.fn()}
        onDelete={vi.fn()}
      />,
    );
    const rows = screen.getAllByTestId("grace2-case-row");
    // Only the two ACTIVE cases render; archived + deleted are filtered out.
    expect(rows).toHaveLength(2);
    const ids = rows.map((r) => r.getAttribute("data-case-id"));
    expect(ids).not.toContain(CASE_OLD_ARCHIVE.case_id);
    expect(ids).not.toContain(CASE_DELETED.case_id);
    expect(ids).toContain(CASE_FORT_MYERS.case_id);
    expect(ids).toContain(CASE_NORCAL_FIRE.case_id);
  });

  it("sorts the rail most-recently-updated first (job-0266)", () => {
    render(
      <CasesPanel
        cases={[CASE_NORCAL_FIRE, CASE_FORT_MYERS]}
        activeCaseId={null}
        onCreate={vi.fn()}
        onSelect={vi.fn()}
        onRename={vi.fn()}
        onArchive={vi.fn()}
        onDelete={vi.fn()}
      />,
    );
    const rows = screen.getAllByTestId("grace2-case-row");
    // Fort Myers (updated 2026-06-08) above NorCal (updated 2026-06-07).
    expect(rows[0]!.getAttribute("data-case-id")).toBe(CASE_FORT_MYERS.case_id);
    expect(rows[1]!.getAttribute("data-case-id")).toBe(CASE_NORCAL_FIRE.case_id);
  });
});

// --- ConfirmationDialog ------------------------------------------------ //

describe("ConfirmationDialog", () => {
  it("Esc triggers onCancel", () => {
    const onCancel = vi.fn();
    const onConfirm = vi.fn();
    render(
      <ConfirmationDialog
        title="Delete?"
        message="Are you sure?"
        confirmLabel="Delete"
        onConfirm={onConfirm}
        onCancel={onCancel}
      />,
    );
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("Enter triggers onConfirm", () => {
    const onCancel = vi.fn();
    const onConfirm = vi.fn();
    render(
      <ConfirmationDialog
        title="Delete?"
        message="Are you sure?"
        confirmLabel="Delete"
        onConfirm={onConfirm}
        onCancel={onCancel}
      />,
    );
    fireEvent.keyDown(window, { key: "Enter" });
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("backdrop click triggers onCancel; dialog click does not", () => {
    const onCancel = vi.fn();
    render(
      <ConfirmationDialog
        title="Delete?"
        message="Are you sure?"
        confirmLabel="Delete"
        onConfirm={vi.fn()}
        onCancel={onCancel}
      />,
    );
    // Click backdrop
    fireEvent.click(screen.getByTestId("grace2-confirmation-dialog-backdrop"));
    expect(onCancel).toHaveBeenCalledTimes(1);
    // Click dialog body — should NOT bubble to backdrop
    fireEvent.click(screen.getByTestId("grace2-confirmation-dialog"));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });
});

// --- useCases hook ------------------------------------------------------ //

describe("useCases", () => {
  it("createCase emits case-command(create) with no title hint", () => {
    const send = vi.fn();
    const { result } = renderHook(() =>
      useCases({ sendCaseCommand: send, isSignedIn: true }),
    );
    act(() => result.current.createCase());
    expect(send).toHaveBeenCalledWith("create", null, {});
  });

  it("createCase emits case-command(create) WITH title hint when provided", () => {
    const send = vi.fn();
    const { result } = renderHook(() =>
      useCases({ sendCaseCommand: send, isSignedIn: true }),
    );
    act(() => result.current.createCase("Ian"));
    expect(send).toHaveBeenCalledWith("create", null, { title: "Ian" });
  });

  it("selectCase emits case-command(select, case_id)", () => {
    const send = vi.fn();
    const { result } = renderHook(() =>
      useCases({ sendCaseCommand: send, isSignedIn: true }),
    );
    act(() => result.current.selectCase("01CASEID0000000000000XYZAB"));
    expect(send).toHaveBeenCalledWith(
      "select",
      "01CASEID0000000000000XYZAB",
      {},
    );
  });

  it("renameCase emits case-command(rename) with trimmed title", () => {
    const send = vi.fn();
    const { result } = renderHook(() =>
      useCases({ sendCaseCommand: send, isSignedIn: true }),
    );
    act(() => result.current.renameCase("01CASEID0000000000000XYZAB", "  Ian  "));
    expect(send).toHaveBeenCalledWith(
      "rename",
      "01CASEID0000000000000XYZAB",
      { title: "Ian" },
    );
  });

  it("renameCase with empty title does NOT emit", () => {
    const send = vi.fn();
    const { result } = renderHook(() =>
      useCases({ sendCaseCommand: send, isSignedIn: true }),
    );
    act(() => result.current.renameCase("01CASEID0000000000000XYZAB", "   "));
    expect(send).not.toHaveBeenCalled();
  });

  it("archiveCase + deleteCase emit the corresponding case-commands", () => {
    const send = vi.fn();
    const { result } = renderHook(() =>
      useCases({ sendCaseCommand: send, isSignedIn: true }),
    );
    act(() => result.current.archiveCase("01CASEID0000000000000XYZAB"));
    act(() => result.current.deleteCase("01CASEID0000000000000XYZAB"));
    expect(send).toHaveBeenNthCalledWith(
      1,
      "archive",
      "01CASEID0000000000000XYZAB",
      {},
    );
    expect(send).toHaveBeenNthCalledWith(
      2,
      "delete",
      "01CASEID0000000000000XYZAB",
      {},
    );
  });

  it("onCaseList updates cases list", () => {
    const send = vi.fn();
    const { result } = renderHook(() =>
      useCases({ sendCaseCommand: send, isSignedIn: true }),
    );
    expect(result.current.cases).toHaveLength(0);
    act(() => {
      const env: CaseListEnvelopePayload = {
        envelope_type: "case-list",
        cases: [CASE_FORT_MYERS, CASE_NORCAL_FIRE],
      };
      result.current.onCaseList(env);
    });
    expect(result.current.cases).toHaveLength(2);
    expect(result.current.cases[0]!.case_id).toBe(CASE_FORT_MYERS.case_id);
  });

  it("onCaseOpen with session_state hydrates active case + session", () => {
    const send = vi.fn();
    const { result } = renderHook(() =>
      useCases({ sendCaseCommand: send, isSignedIn: true }),
    );
    const session: CaseSessionState = {
      case: CASE_FORT_MYERS,
      chat_history: [],
      loaded_layers: [],
      pipeline_history: [],
      current_pipeline: null,
    };
    act(() => {
      const env: CaseOpenEnvelopePayload = {
        envelope_type: "case-open",
        session_state: session,
      };
      result.current.onCaseOpen(env);
    });
    expect(result.current.activeCaseId).toBe(CASE_FORT_MYERS.case_id);
    expect(result.current.activeSession).not.toBeNull();
    expect(result.current.activeSession!.case.title).toBe(
      "Hurricane Ian — Fort Myers",
    );
  });

  it("onCaseOpen with null session_state clears active case + session", () => {
    const send = vi.fn();
    const { result } = renderHook(() =>
      useCases({ sendCaseCommand: send, isSignedIn: true }),
    );
    const session: CaseSessionState = {
      case: CASE_FORT_MYERS,
    };
    act(() => {
      result.current.onCaseOpen({
        envelope_type: "case-open",
        session_state: session,
      });
    });
    expect(result.current.activeCaseId).toBe(CASE_FORT_MYERS.case_id);
    act(() => {
      result.current.onCaseOpen({
        envelope_type: "case-open",
        session_state: null,
      });
    });
    expect(result.current.activeCaseId).toBeNull();
    expect(result.current.activeSession).toBeNull();
  });

  it("persistenceState=anonymous when isSignedIn=false", () => {
    const send = vi.fn();
    const { result } = renderHook(() =>
      useCases({ sendCaseCommand: send, isSignedIn: false }),
    );
    expect(result.current.persistenceState).toBe("anonymous");
  });

  it("persistenceState transitions: saved → saving (after emit) → saved (after case-list)", () => {
    const send = vi.fn();
    const { result } = renderHook(() =>
      useCases({ sendCaseCommand: send, isSignedIn: true }),
    );
    expect(result.current.persistenceState).toBe("saved");
    act(() => result.current.createCase("Ian"));
    expect(result.current.persistenceState).toBe("saving");
    act(() => {
      result.current.onCaseList({
        envelope_type: "case-list",
        cases: [CASE_FORT_MYERS],
      });
    });
    expect(result.current.persistenceState).toBe("saved");
  });
});

// --- formatRelative pure function -------------------------------------- //

describe("formatRelative", () => {
  it("returns 'just now' for <30s", () => {
    expect(
      formatRelative(new Date(NOW.getTime() - 10_000).toISOString(), NOW),
    ).toBe("just now");
  });
  it("returns Xm ago for minutes", () => {
    expect(
      formatRelative(new Date(NOW.getTime() - 5 * 60_000).toISOString(), NOW),
    ).toBe("5m ago");
  });
  it("returns Xh ago for hours", () => {
    expect(
      formatRelative(
        new Date(NOW.getTime() - 2 * 60 * 60_000).toISOString(),
        NOW,
      ),
    ).toBe("2h ago");
  });
  it("returns Xd ago for days <7", () => {
    expect(
      formatRelative(
        new Date(NOW.getTime() - 3 * 24 * 60 * 60_000).toISOString(),
        NOW,
      ),
    ).toBe("3d ago");
  });
  it("returns a date label for >7 days", () => {
    const old = new Date(NOW.getTime() - 30 * 24 * 60 * 60_000).toISOString();
    const result = formatRelative(old, NOW);
    // Locale-dependent but must NOT be "Xd ago".
    expect(result).not.toMatch(/d ago$/);
  });
});

// --- formatBbox pure function ------------------------------------------ //

describe("formatBbox", () => {
  it("formats SW corner with hemispheres for the Fort Myers bbox", () => {
    expect(formatBbox([-82.0, 26.5, -81.7, 26.8])).toBe("82.0°W 26.5°N");
  });
  it("returns null for null/undefined input", () => {
    expect(formatBbox(null)).toBeNull();
    expect(formatBbox(undefined)).toBeNull();
  });
});
