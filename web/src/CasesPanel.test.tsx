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
import { MobileDrawer } from "./components/MobileDrawer";
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

  it("inline rename via kebab → Rename → Enter calls onRename with new title", () => {
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
    fireEvent.click(screen.getByTestId("grace2-case-row-menu-button"));
    fireEvent.click(screen.getByTestId("grace2-case-row-menu-rename"));
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

  it("inline rename commit via the check button calls onRename", () => {
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
    fireEvent.click(screen.getByTestId("grace2-case-row-menu-button"));
    fireEvent.click(screen.getByTestId("grace2-case-row-menu-rename"));
    const input = screen.getByTestId(
      "grace2-case-row-rename-input",
    ) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "Lee County" } });
    fireEvent.click(screen.getByTestId("grace2-case-row-rename-commit"));
    expect(onRename).toHaveBeenCalledWith(CASE_FORT_MYERS.case_id, "Lee County");
  });

  it("kebab → Archive calls onArchive with the row's case_id", () => {
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
    fireEvent.click(screen.getByTestId("grace2-case-row-menu-button"));
    fireEvent.click(screen.getByTestId("grace2-case-row-menu-archive"));
    expect(onArchive).toHaveBeenCalledWith(CASE_FORT_MYERS.case_id);
  });

  it("kebab → Delete opens the confirmation dialog; Cancel does NOT fire onDelete", () => {
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
    fireEvent.click(screen.getByTestId("grace2-case-row-menu-button"));
    fireEvent.click(screen.getByTestId("grace2-case-row-menu-delete"));
    expect(screen.getByTestId("grace2-case-delete-dialog")).toBeTruthy();
    fireEvent.click(screen.getByTestId("grace2-case-delete-dialog-cancel"));
    expect(onDelete).not.toHaveBeenCalled();
  });

  it("kebab → Delete → Confirm calls onDelete with the row's case_id", () => {
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
    fireEvent.click(screen.getByTestId("grace2-case-row-menu-button"));
    fireEvent.click(screen.getByTestId("grace2-case-row-menu-delete"));
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

  // job-0322 F52 — CasesPanel is mounted as a child of MobileDrawer on mobile.
  // The drawer's tap-to-dismiss guard (e.target === e.currentTarget on the
  // column) must NOT swallow Case-row selection or the delete dialog: those
  // events have e.target on a CasesPanel descendant, so they reach their own
  // handlers and the drawer stays open until App explicitly closes it.
  describe("inside MobileDrawer (F52 tap-dismiss coexistence)", () => {
    it("selecting a row still calls onSelect (drawer onClose NOT triggered)", () => {
      const onSelect = vi.fn();
      const onClose = vi.fn();
      render(
        <MobileDrawer open={true} onClose={onClose}>
          <CasesPanel
            cases={[CASE_FORT_MYERS, CASE_NORCAL_FIRE]}
            activeCaseId={null}
            onCreate={vi.fn()}
            onSelect={onSelect}
            onRename={vi.fn()}
            onArchive={vi.fn()}
            onDelete={vi.fn()}
          />
        </MobileDrawer>,
      );
      const rows = screen.getAllByTestId("grace2-case-row");
      const norcalRow = rows.find(
        (r) => r.getAttribute("data-case-id") === CASE_NORCAL_FIRE.case_id,
      )!;
      fireEvent.click(norcalRow);
      expect(onSelect).toHaveBeenCalledWith(CASE_NORCAL_FIRE.case_id);
      // The drawer's column guard must NOT have fired onClose for a row tap.
      expect(onClose).not.toHaveBeenCalled();
    });

    it("opening + confirming the delete dialog still works inside the drawer", () => {
      const onDelete = vi.fn();
      const onClose = vi.fn();
      render(
        <MobileDrawer open={true} onClose={onClose}>
          <CasesPanel
            cases={[CASE_FORT_MYERS]}
            activeCaseId={null}
            onCreate={vi.fn()}
            onSelect={vi.fn()}
            onRename={vi.fn()}
            onArchive={vi.fn()}
            onDelete={onDelete}
          />
        </MobileDrawer>,
      );
      // Open the dialog via the kebab menu — the menu interaction bubbles to
      // the column but its e.target is a CasesPanel descendant, so the drawer
      // does not close.
      fireEvent.click(screen.getByTestId("grace2-case-row-menu-button"));
      fireEvent.click(screen.getByTestId("grace2-case-row-menu-delete"));
      expect(screen.getByTestId("grace2-case-delete-dialog")).toBeTruthy();
      expect(onClose).not.toHaveBeenCalled();
      // The dialog's own fixed backdrop cancel path must still work: clicking
      // the dialog BODY stops propagation, then Confirm fires onDelete.
      fireEvent.click(screen.getByTestId("grace2-case-delete-dialog"));
      expect(onClose).not.toHaveBeenCalled();
      fireEvent.click(screen.getByTestId("grace2-case-delete-dialog-confirm"));
      expect(onDelete).toHaveBeenCalledWith(CASE_FORT_MYERS.case_id);
      expect(onClose).not.toHaveBeenCalled();
    });

    it("a tap on the backdrop DOES close the drawer (backdrop owns close)", () => {
      // job-0329 — the F52 model changed: the old `target === currentTarget`
      // guard on the drawer COLUMN was replaced by the pointer-events
      // fall-through design. The transparent column is `pointerEvents: "none"`,
      // so empty-gutter taps pass THROUGH to the full-screen invisible backdrop
      // (z=40, onClick=onClose) which now owns dismiss. (We assert on the
      // backdrop directly because happy-dom does NOT honor `pointer-events:
      // none` for synthetic clicks, so a click dispatched at the column would
      // not realistically fall through in jsdom/happy-dom.)
      const onSelect = vi.fn();
      const onClose = vi.fn();
      render(
        <MobileDrawer open={true} onClose={onClose}>
          <CasesPanel
            cases={[CASE_FORT_MYERS]}
            activeCaseId={null}
            onCreate={vi.fn()}
            onSelect={onSelect}
            onRename={vi.fn()}
            onArchive={vi.fn()}
            onDelete={vi.fn()}
          />
        </MobileDrawer>,
      );

      // The backdrop is the close affordance now — clicking it fires onClose
      // and never touches CasesPanel handlers.
      fireEvent.click(screen.getByTestId("grace2-mobile-drawer-backdrop"));
      expect(onClose).toHaveBeenCalledTimes(1);
      expect(onSelect).not.toHaveBeenCalled();
    });

    it("encodes the pointer-events fall-through design (column none, backdrop owns onClick)", () => {
      // The NEW model relies on a specific pointer-events layout: the column is
      // click-transparent (so gutter taps reach the backdrop) and carries NO
      // onClick of its own; the backdrop is the only element with the close
      // handler. We assert that contract structurally so a regression that
      // re-adds an onClick to the column (or makes it `pointer-events: auto`)
      // is caught even though happy-dom can't simulate the fall-through.
      const onClose = vi.fn();
      render(
        <MobileDrawer open={true} onClose={onClose}>
          <CasesPanel
            cases={[CASE_FORT_MYERS]}
            activeCaseId={null}
            onCreate={vi.fn()}
            onSelect={vi.fn()}
            onRename={vi.fn()}
            onArchive={vi.fn()}
            onDelete={vi.fn()}
          />
        </MobileDrawer>,
      );

      const column = screen.getByTestId("grace2-mobile-drawer");
      const backdrop = screen.getByTestId("grace2-mobile-drawer-backdrop");

      // The column is click-transparent (pointer-events: none) so gutter taps
      // fall through to the backdrop. The backdrop sits BELOW the column on the
      // z-axis (z=40 vs z=41), which is only reachable because the column does
      // not intercept the click — encode both halves of that contract.
      expect(column.style.pointerEvents).toBe("none");
      expect(Number(backdrop.style.zIndex)).toBeLessThan(
        Number(column.style.zIndex),
      );

      // The column carries NO onClick of its own — a click dispatched directly
      // on it must NOT close the drawer (regression guard against re-adding the
      // old `target === currentTarget` column handler).
      fireEvent.click(column);
      expect(onClose).not.toHaveBeenCalled();

      // The backdrop is the sole close owner.
      fireEvent.click(backdrop);
      expect(onClose).toHaveBeenCalledTimes(1);
    });
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

  // --- F57 kebab overflow menu ---------------------------------------- //
  describe("F57 kebab overflow menu", () => {
    function renderOneRow(overrides: Partial<Record<string, () => void>> = {}) {
      const handlers = {
        onCreate: vi.fn(),
        onSelect: vi.fn(),
        onRename: vi.fn(),
        onArchive: vi.fn(),
        onDelete: vi.fn(),
        ...overrides,
      };
      render(
        <CasesPanel
          cases={[CASE_FORT_MYERS]}
          activeCaseId={null}
          {...(handlers as Required<typeof handlers>)}
        />,
      );
      return handlers;
    }

    it("the kebab button opens the menu with Rename / Archive / Delete items", () => {
      renderOneRow();
      // Menu is closed initially.
      expect(screen.queryByTestId("grace2-case-row-menu")).toBeNull();
      const kebab = screen.getByTestId("grace2-case-row-menu-button");
      expect(kebab.getAttribute("aria-haspopup")).toBe("menu");
      expect(kebab.getAttribute("aria-expanded")).toBe("false");
      fireEvent.click(kebab);
      const menu = screen.getByTestId("grace2-case-row-menu");
      expect(menu.getAttribute("role")).toBe("menu");
      expect(kebab.getAttribute("aria-expanded")).toBe("true");
      expect(screen.getByTestId("grace2-case-row-menu-rename")).toBeTruthy();
      expect(screen.getByTestId("grace2-case-row-menu-archive")).toBeTruthy();
      expect(screen.getByTestId("grace2-case-row-menu-delete")).toBeTruthy();
      // All three items expose the proper menuitem role.
      expect(
        screen
          .getByTestId("grace2-case-row-menu-delete")
          .getAttribute("role"),
      ).toBe("menuitem");
    });

    it("opening / using the kebab does NOT select the row (stopPropagation)", () => {
      const onSelect = vi.fn();
      renderOneRow({ onSelect });
      fireEvent.click(screen.getByTestId("grace2-case-row-menu-button"));
      expect(onSelect).not.toHaveBeenCalled();
      fireEvent.click(screen.getByTestId("grace2-case-row-menu-archive"));
      // Archive ran, but the row was never selected.
      expect(onSelect).not.toHaveBeenCalled();
    });

    it("clicking outside the menu closes it (outside-click)", () => {
      renderOneRow();
      fireEvent.click(screen.getByTestId("grace2-case-row-menu-button"));
      expect(screen.getByTestId("grace2-case-row-menu")).toBeTruthy();
      // pointerdown on the panel region (outside the menu wrapper) dismisses.
      fireEvent.pointerDown(screen.getByTestId("grace2-cases-panel"));
      expect(screen.queryByTestId("grace2-case-row-menu")).toBeNull();
    });

    it("pressing Esc closes the menu", () => {
      renderOneRow();
      fireEvent.click(screen.getByTestId("grace2-case-row-menu-button"));
      expect(screen.getByTestId("grace2-case-row-menu")).toBeTruthy();
      fireEvent.keyDown(window, { key: "Escape" });
      expect(screen.queryByTestId("grace2-case-row-menu")).toBeNull();
    });

    it("selecting a menu item closes the menu", () => {
      renderOneRow();
      fireEvent.click(screen.getByTestId("grace2-case-row-menu-button"));
      fireEvent.click(screen.getByTestId("grace2-case-row-menu-archive"));
      expect(screen.queryByTestId("grace2-case-row-menu")).toBeNull();
    });
  });

  // --- job-0330 mobile portrait clip fix ------------------------------- //
  // The mobile-drawer column is min(320px,85vw) with overflow:hidden. The bug:
  // a fixed-width / shrink-wrapped panel let a long Case title (whiteSpace:
  // nowrap, flex:1) expand the row past the column width, pushing the kebab
  // under the clip in PORTRAIT (landscape's wider 85vw masked it). The fix
  // makes the title ellipsis-truncate (flex:1 + min-width:0) and pins the kebab
  // (flex-shrink:0) so it is always visible inside the row.
  describe("job-0330 — row never clips the kebab (mobile portrait)", () => {
    const LONG_TITLE_CASE: CaseSummary = {
      ...CASE_FORT_MYERS,
      case_id: "01ABCDEFGHJKMNPQRSTVWX00AA",
      title:
        "Hurricane Ian catastrophic storm-surge inundation across Lee County and the barrier islands",
    };

    function renderRow(c: CaseSummary = LONG_TITLE_CASE) {
      render(
        <CasesPanel
          cases={[c]}
          activeCaseId={null}
          onCreate={vi.fn()}
          onSelect={vi.fn()}
          onRename={vi.fn()}
          onArchive={vi.fn()}
          onDelete={vi.fn()}
        />,
      );
    }

    it("the title ellipsis-truncates (flex:1 + min-width:0, not nowrap overflow)", () => {
      renderRow();
      const title = screen.getByTestId("grace2-case-row-title");
      // flex:1 lets it grow/shrink; min-width:0 is the load-bearing bit — a
      // flex item defaults to min-width:auto and refuses to shrink below its
      // content width, which defeats the ellipsis and pushes the kebab out.
      expect(title.style.minWidth).toBe("0");
      expect(title.style.flex).toMatch(/^1\b/);
      expect(title.style.overflow).toBe("hidden");
      expect(title.style.textOverflow).toBe("ellipsis");
      expect(title.style.whiteSpace).toBe("nowrap");
    });

    it("the row header is width-capped (100% + min-width:0) so it can't overflow the column", () => {
      renderRow();
      // The header is the flex container holding [title | kebab]. It must be
      // capped at the row width with min-width:0 so the title's ellipsis
      // engages instead of the row growing past the drawer's overflow:hidden.
      const title = screen.getByTestId("grace2-case-row-title");
      const header = title.parentElement as HTMLElement;
      expect(header.style.display).toBe("flex");
      expect(header.style.width).toBe("100%");
      expect(header.style.minWidth).toBe("0");
    });

    it("the kebab wrapper is flex-shrink:0 (pinned, never pushed off the clip)", () => {
      renderRow();
      const kebab = screen.getByTestId("grace2-case-row-menu-button");
      const wrapper = kebab.parentElement as HTMLElement;
      expect(wrapper.style.flexShrink).toBe("0");
      // It still anchors its popover (position:relative) under the button.
      expect(wrapper.style.position).toBe("relative");
    });

    it("the kebab stays in the DOM and openable even with a very long title", () => {
      renderRow();
      // The kebab is present (not clipped out of existence) and still opens its
      // menu — the actual user-facing guarantee.
      const kebab = screen.getByTestId("grace2-case-row-menu-button");
      expect(kebab).toBeTruthy();
      fireEvent.click(kebab);
      expect(screen.getByTestId("grace2-case-row-menu")).toBeTruthy();
    });
  });

  // job-0330 — inside the mobile drawer the CasesPanel must fill the column
  // (full width), NOT shrink-wrap. A `fit-content` hugger let the row expand to
  // the (nowrap) title's intrinsic width and clip the kebab. We assert the
  // panel renders at the drawer column's full width here.
  describe("job-0330 — CasesPanel fills the mobile drawer column", () => {
    it("the panel gets the mobile-touch scope (width override) inside the drawer", () => {
      render(
        <MobileDrawer open={true} onClose={vi.fn()}>
          {/* mirror App.tsx's full-width hugger (NOT fit-content) */}
          <div style={{ width: "100%", pointerEvents: "auto" }}>
            <CasesPanel
              cases={[CASE_FORT_MYERS]}
              activeCaseId={null}
              onCreate={vi.fn()}
              onSelect={vi.fn()}
              onRename={vi.fn()}
              onArchive={vi.fn()}
              onDelete={vi.fn()}
            />
          </div>
        </MobileDrawer>,
      );
      const panel = screen.getByTestId("grace2-cases-panel");
      const hugger = panel.parentElement as HTMLElement;
      // The hugger must be full-width (NOT fit-content): a shrink-wrap hugger
      // would let a long (nowrap) Case title widen the row past the column.
      // job-0337: the panel itself is now a FIXED 288px (global.css
      // .grace2-mobile-touch override) rather than width:auto — see the
      // job-0337 describe block below for the fixed-width contract.
      expect(hugger.style.width).toBe("100%");
      // The drawer applies the touch scope whose CSS pins the panel width —
      // assert the panel rides inside that scope.
      const drawer = screen.getByTestId("grace2-mobile-drawer");
      expect(drawer.className).toContain("grace2-mobile-touch");
      expect(drawer.contains(panel)).toBe(true);
    });
  });

  // --- job-0337 — fixed Cases-panel width (== LayerPanel) + larger header --
  // job-0335 set the panel root to width:100% but global.css still forced
  // width:auto !important inside the mobile drawer, so the panel sized to
  // content / varied with viewport (the "dynamically sized + cuts off" report).
  // The fix pins the panel to a FIXED 288px (== LAYERS_WIDTH_DEFAULT_PX, the
  // LayerPanel mobile column width) on every surface so it never grows with a
  // long title nor varies with the viewport; box-sizing:border-box + max-width
  // keep it inside narrow drawer columns. The "Cases" header is also enlarged
  // so it reads as a section title.
  describe("job-0337 — fixed width + larger header", () => {
    const LONG_TITLE_CASE: CaseSummary = {
      ...CASE_FORT_MYERS,
      title:
        "Hurricane Ian catastrophic storm-surge inundation across Lee County and the barrier islands and the gulf shoreline",
    };

    function renderPanel(c: CaseSummary = CASE_FORT_MYERS) {
      render(
        <CasesPanel
          cases={[c]}
          activeCaseId={null}
          onCreate={vi.fn()}
          onSelect={vi.fn()}
          onRename={vi.fn()}
          onArchive={vi.fn()}
          onDelete={vi.fn()}
        />,
      );
    }

    it("the panel root declares a FIXED 288px width (== LayerPanel column), box-sized", () => {
      // The inline root width is the fixed, non-content/non-viewport-driven
      // base. (The desktop-rail override pins it to 280 to match CaseView; the
      // mobile-touch override pins it to 288 — both via global.css !important —
      // but the inline base is what guarantees it is never `auto`/fit-content
      // when no scope class is present.)
      renderPanel();
      const panel = screen.getByTestId("grace2-cases-panel");
      expect(panel.style.width).toBe("288px");
      expect(panel.style.maxWidth).toBe("100%");
      expect(panel.style.boxSizing).toBe("border-box");
    });

    it("the fixed width does NOT grow with a very long Case title", () => {
      // Same fixed 288px regardless of title length — the panel must never
      // shrink-wrap or expand to content. The long title is absorbed by the
      // row title's ellipsis (asserted in the job-0330 block above).
      renderPanel(LONG_TITLE_CASE);
      const panel = screen.getByTestId("grace2-cases-panel");
      expect(panel.style.width).toBe("288px");
    });

    it("the 'Cases' header label uses a larger, bold section-title font", () => {
      renderPanel();
      const header = screen.getByTestId("grace2-cases-header-label");
      expect(header.textContent).toBe("Cases");
      // Larger than the previous 13px body size so it reads as a heading.
      expect(parseInt(header.style.fontSize, 10)).toBeGreaterThanOrEqual(17);
      // Still bold.
      expect(header.style.fontWeight).toBe("700");
    });

    it("a long title still ellipsis-truncates within the fixed-width panel", () => {
      // Re-assert the title contract holds alongside the fixed width (so a
      // future width change can't silently drop the ellipsis path).
      renderPanel(LONG_TITLE_CASE);
      const title = screen.getByTestId("grace2-case-row-title");
      expect(title.style.minWidth).toBe("0");
      expect(title.style.flex).toMatch(/^1\b/);
      expect(title.style.overflow).toBe("hidden");
      expect(title.style.textOverflow).toBe("ellipsis");
      expect(title.style.whiteSpace).toBe("nowrap");
      // And the kebab is still pinned (flex-shrink:0) → never pushed out.
      const kebab = screen.getByTestId("grace2-case-row-menu-button");
      const wrapper = kebab.parentElement as HTMLElement;
      expect(wrapper.style.flexShrink).toBe("0");
    });
  });

  it("the +New Case button is icon-only (no text label) but keeps its aria-label", () => {
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
    const newBtn = screen.getByTestId("grace2-cases-new");
    expect(newBtn.getAttribute("aria-label")).toBe("Create a new Case");
    // No visible text content — the plus icon is the only child (an SVG).
    expect(newBtn.textContent).toBe("");
    expect(newBtn.querySelector("svg")).not.toBeNull();
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
