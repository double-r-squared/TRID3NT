// GRACE-2 web — CasesPanel (job-0137, sprint-12-mega Wave 3 — FR-MP-6).
//
// Left-rail panel of the user's Cases. Renders:
//   - A "+ New Case" button at the top.
//   - One row per CaseSummary: title, bbox indicator, primary_hazard chip,
//     updated_at relative timestamp.
//   - Per-row actions: select (click row), rename (pencil → inline edit),
//     archive, delete (with confirmation modal).
//   - Active-case highlight on the matching row.
//   - Friendly empty state when no Cases exist.
//
// Per-row actions emit through prop callbacks the parent wires into the
// useCases hook (which itself wraps GraceWs.sendCaseCommand). The panel
// itself owns ONLY local UI state (which row is in rename mode, which
// is the pending-delete target).
//
// Invariants:
//   - 1 (determinism boundary): no number computed here; we only render
//     received CaseSummary fields verbatim. The relative timestamp string
//     is a display-only formatting of `updated_at`.
//   - 8 (cancellation is first-class): no destructive action fires without
//     a clear cancel affordance (delete: confirmation modal; rename: Esc).
//   - 9 (no cost theater): no cost / quota / quote field anywhere.
//
// Memory rule "Confirmation before consequence": the delete row action
// opens a ConfirmationDialog before emitting `case-command(delete)`.

import { useEffect, useMemo, useRef, useState } from "react";
import { CaseSummary } from "../contracts";
import { ConfirmationDialog } from "./ConfirmationDialog";
import {
  IconKebab,
  IconRename,
  IconArchive,
  IconDelete,
  IconCheck,
  IconBbox,
  IconAdd,
} from "./icons";

export interface CasesPanelProps {
  /** Left-rail list from the useCases hook. */
  cases: CaseSummary[];
  /** Currently-active Case id, or null when no Case is open. */
  activeCaseId: string | null;

  // Emitters (parent wires these to useCases / GraceWs).
  onCreate: () => void;
  onSelect: (caseId: string) => void;
  onRename: (caseId: string, newTitle: string) => void;
  onArchive: (caseId: string) => void;
  onDelete: (caseId: string) => void;
}

// --- Helpers ------------------------------------------------------------- //

/**
 * Human-friendly relative timestamp. Pure display formatting — no math the
 * caller cares about. Examples: "just now", "5m ago", "2h ago", "3d ago",
 * "Jun 4". `now` is injectable for testability.
 */
export function formatRelative(
  isoTs: string,
  now: Date = new Date(),
): string {
  const t = new Date(isoTs);
  if (Number.isNaN(t.getTime())) return "";
  const deltaMs = now.getTime() - t.getTime();
  if (deltaMs < 30_000) return "just now";
  const mins = Math.floor(deltaMs / 60_000);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  // Older than 7 days → date label.
  return t.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/** Render the bbox compactly: "[-82.0, 26.5, -81.7, 26.8]" → "82.0°W 26.5°N…". */
export function formatBbox(
  bbox: [number, number, number, number] | null | undefined,
): string | null {
  if (!bbox || bbox.length !== 4) return null;
  const [minLon, minLat] = bbox;
  // Compact lon/lat at the SW corner only — fits in the 1-line meta strip.
  const lonStr = `${Math.abs(minLon).toFixed(1)}°${minLon < 0 ? "W" : "E"}`;
  const latStr = `${Math.abs(minLat).toFixed(1)}°${minLat < 0 ? "S" : "N"}`;
  return `${lonStr} ${latStr}`;
}

// --- Sub-components ------------------------------------------------------ //

interface CaseRowProps {
  c: CaseSummary;
  active: boolean;
  onSelect: () => void;
  onRenameSubmit: (next: string) => void;
  onArchive: () => void;
  onRequestDelete: () => void;
}

function CaseRow({
  c,
  active,
  onSelect,
  onRenameSubmit,
  onArchive,
  onRequestDelete,
}: CaseRowProps): JSX.Element {
  const [editing, setEditing] = useState(false);
  const [draftTitle, setDraftTitle] = useState(c.title);
  const [menuOpen, setMenuOpen] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const menuWrapRef = useRef<HTMLDivElement | null>(null);
  const kebabRef = useRef<HTMLButtonElement | null>(null);
  const firstMenuItemRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    if (!editing) setDraftTitle(c.title);
  }, [c.title, editing]);

  useEffect(() => {
    if (editing) inputRef.current?.focus();
  }, [editing]);

  // F57 — overflow menu: close on outside-click and Esc; move focus to the
  // first item when the menu opens (keyboard accessibility). The pointerdown
  // listener fires before click so a tap outside dismisses without also
  // selecting/deselecting the row.
  useEffect(() => {
    if (!menuOpen) return;
    firstMenuItemRef.current?.focus();
    function onPointerDown(ev: PointerEvent): void {
      if (!menuWrapRef.current) return;
      if (!menuWrapRef.current.contains(ev.target as Node)) {
        setMenuOpen(false);
      }
    }
    function onKey(ev: KeyboardEvent): void {
      if (ev.key === "Escape") {
        ev.preventDefault();
        ev.stopPropagation();
        setMenuOpen(false);
        kebabRef.current?.focus();
      }
    }
    window.addEventListener("pointerdown", onPointerDown, true);
    window.addEventListener("keydown", onKey, true);
    return () => {
      window.removeEventListener("pointerdown", onPointerDown, true);
      window.removeEventListener("keydown", onKey, true);
    };
  }, [menuOpen]);

  function startEdit(): void {
    setDraftTitle(c.title);
    setEditing(true);
  }
  function cancelEdit(): void {
    setEditing(false);
    setDraftTitle(c.title);
  }
  function commitEdit(): void {
    const trimmed = draftTitle.trim();
    if (trimmed.length === 0 || trimmed === c.title) {
      cancelEdit();
      return;
    }
    onRenameSubmit(trimmed);
    setEditing(false);
  }

  const bboxStr = formatBbox(c.bbox ?? null);

  return (
    <div
      data-testid="grace2-case-row"
      data-case-id={c.case_id}
      data-active={active ? "true" : "false"}
      style={{
        background: active ? "rgba(59,130,246,0.15)" : "rgba(20,20,25,0.65)",
        border: active ? "1px solid #3b82f6" : "1px solid #333",
        borderRadius: 6,
        padding: 8,
        display: "flex",
        flexDirection: "column",
        gap: 4,
        cursor: editing ? "default" : "pointer",
      }}
      onClick={() => {
        if (!editing) onSelect();
      }}
      role="button"
      aria-pressed={active}
      aria-label={`Case ${c.title}`}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          // job-0330 — the row header must never overflow its column: cap it at
          // the row width and let the title (flex:1, min-width:0) ellipsis-
          // truncate so the kebab (flex-shrink:0) is always visible. Without
          // this, a long title pushed the kebab past the mobile drawer column's
          // overflow:hidden clip in portrait.
          minWidth: 0,
          width: "100%",
        }}
      >
        {editing ? (
          <input
            ref={inputRef}
            data-testid="grace2-case-row-rename-input"
            value={draftTitle}
            onChange={(e) => setDraftTitle(e.target.value)}
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                commitEdit();
              } else if (e.key === "Escape") {
                e.preventDefault();
                cancelEdit();
              }
            }}
            onBlur={() => commitEdit()}
            style={{
              flex: 1,
              background: "#111",
              color: "#eee",
              border: "1px solid #555",
              borderRadius: 4,
              padding: "3px 6px",
              fontSize: 13,
              // job-0166 — form controls don't inherit font-family by default.
              fontFamily: "inherit",
            }}
          />
        ) : (
          <strong
            data-testid="grace2-case-row-title"
            style={{
              flex: 1,
              // job-0330 — a flex item defaults to min-width:auto, which refuses
              // to shrink below its intrinsic content width and so defeats the
              // ellipsis (the title would instead push the kebab out of the
              // clip). min-width:0 lets it shrink and the ellipsis engage.
              minWidth: 0,
              fontSize: 13,
              color: "#eee",
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {c.title}
          </strong>
        )}
        {editing ? (
          // While renaming, the inline-edit mode owns the row — a single
          // commit affordance replaces the overflow menu.
          <button
            data-testid="grace2-case-row-rename-commit"
            aria-label={`Save name for ${c.title}`}
            title="Save"
            onClick={(e) => {
              e.stopPropagation();
              commitEdit();
            }}
            style={iconBtnStyle}
          >
            <IconCheck size={14} />
          </button>
        ) : (
          // F57 — single kebab overflow button → popover menu.
          <div
            ref={menuWrapRef}
            // job-0330 — the kebab must never shrink or be pushed off the row's
            // right edge (the bug: a long title clipped it under the mobile
            // drawer's overflow:hidden in portrait). flex-shrink:0 pins it; the
            // title (flex:1, min-width:0) absorbs all the slack via ellipsis.
            style={{
              position: "relative",
              display: "inline-flex",
              flexShrink: 0,
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <button
              ref={kebabRef}
              data-testid="grace2-case-row-menu-button"
              aria-label={`Actions for ${c.title}`}
              title="Actions"
              aria-haspopup="menu"
              aria-expanded={menuOpen}
              onClick={(e) => {
                e.stopPropagation();
                setMenuOpen((v) => !v);
              }}
              onKeyDown={(e) => {
                if (e.key === "ArrowDown" && !menuOpen) {
                  e.preventDefault();
                  setMenuOpen(true);
                }
              }}
              style={iconBtnStyle}
            >
              <IconKebab size={16} />
            </button>
            {menuOpen && (
              <div
                data-testid="grace2-case-row-menu"
                role="menu"
                aria-label={`Actions for ${c.title}`}
                style={menuStyle}
              >
                <button
                  ref={firstMenuItemRef}
                  data-testid="grace2-case-row-menu-rename"
                  role="menuitem"
                  onClick={(e) => {
                    e.stopPropagation();
                    setMenuOpen(false);
                    startEdit();
                  }}
                  style={menuItemStyle()}
                >
                  <IconRename size={14} />
                  <span>Rename</span>
                </button>
                <button
                  data-testid="grace2-case-row-menu-archive"
                  role="menuitem"
                  onClick={(e) => {
                    e.stopPropagation();
                    setMenuOpen(false);
                    onArchive();
                  }}
                  style={menuItemStyle()}
                >
                  <IconArchive size={14} />
                  <span>Archive</span>
                </button>
                <button
                  data-testid="grace2-case-row-menu-delete"
                  role="menuitem"
                  onClick={(e) => {
                    e.stopPropagation();
                    setMenuOpen(false);
                    onRequestDelete();
                  }}
                  style={menuItemStyle("#f87171")}
                >
                  <IconDelete size={14} />
                  <span>Delete</span>
                </button>
              </div>
            )}
          </div>
        )}
      </div>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          fontSize: 10,
          color: "#999",
        }}
      >
        {c.primary_hazard && (
          <span
            data-testid="grace2-case-row-hazard"
            style={{
              background: "rgba(59,130,246,0.2)",
              border: "1px solid #3b82f6",
              borderRadius: 10,
              padding: "1px 6px",
              color: "#bdd",
              fontSize: 10,
            }}
          >
            {c.primary_hazard}
          </span>
        )}
        {bboxStr && (
          <span
            data-testid="grace2-case-row-bbox"
            title={`bbox: ${(c.bbox ?? []).join(", ")}`}
            style={{
              fontFamily: "monospace",
              display: "inline-flex",
              alignItems: "center",
              gap: 3,
            }}
          >
            <IconBbox size={11} />
            {bboxStr}
          </span>
        )}
        <span
          data-testid="grace2-case-row-updated"
          style={{ marginLeft: "auto" }}
        >
          {formatRelative(c.updated_at)}
        </span>
      </div>
    </div>
  );
}

const iconBtnStyle: React.CSSProperties = {
  background: "transparent",
  border: "none",
  color: "#aaa",
  cursor: "pointer",
  fontSize: 12,
  padding: 2,
  width: 22,
  height: 22,
  // job-0330 — icon buttons (kebab, rename-commit) must hold their declared
  // size and never shrink, so the title's ellipsis (not the control) absorbs a
  // narrow row. Mirrors the flex-shrink:0 on the kebab wrapper.
  flexShrink: 0,
  borderRadius: 4,
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  // job-0166 — buttons need explicit fontFamily so they don't fall back to UA serif.
  fontFamily: "inherit",
};

// F57 — popover menu surface anchored to the kebab button. Right-aligned so it
// doesn't spill past the row's right edge; high z-index so it floats above
// neighbouring rows.
const menuStyle: React.CSSProperties = {
  position: "absolute",
  top: "calc(100% + 4px)",
  right: 0,
  minWidth: 132,
  background: "rgba(28,28,34,0.98)",
  border: "1px solid rgba(255,255,255,0.12)",
  borderRadius: 8,
  padding: 4,
  display: "flex",
  flexDirection: "column",
  gap: 2,
  boxShadow: "0 6px 20px rgba(0,0,0,0.55)",
  zIndex: 50,
};

function menuItemStyle(color = "#ddd"): React.CSSProperties {
  return {
    background: "transparent",
    border: "none",
    color,
    cursor: "pointer",
    fontSize: 12,
    textAlign: "left",
    padding: "6px 8px",
    borderRadius: 5,
    display: "flex",
    alignItems: "center",
    gap: 8,
    width: "100%",
    // job-0166 — buttons need explicit fontFamily.
    fontFamily: "inherit",
  };
}

// --- CasesPanel --------------------------------------------------------- //

export function CasesPanel({
  cases,
  activeCaseId,
  onCreate,
  onSelect,
  onRename,
  onArchive,
  onDelete,
}: CasesPanelProps): JSX.Element {
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);

  // job-0266 — the rail lists ACTIVE Cases only. Archived / deleted Cases
  // are EXCLUDED client-side (the server's case-list may still carry them;
  // the user saw a deleted Case linger in the rail). Sort: most-recently
  // updated first.
  const sortedCases = useMemo(() => {
    return cases
      .filter((c) => c.status === "active")
      .sort((a, b) => b.updated_at.localeCompare(a.updated_at));
  }, [cases]);

  const pendingCase = pendingDeleteId
    ? cases.find((c) => c.case_id === pendingDeleteId) ?? null
    : null;

  return (
    <div
      data-testid="grace2-cases-panel"
      role="region"
      aria-label="Cases"
      style={{
        background: "rgba(15,15,20,0.92)",
        border: "1px solid #333",
        borderRadius: 8,
        padding: 10,
        width: 260,
        color: "#eee",
        fontSize: 12,
        fontFamily:
          "-apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif",
        display: "flex",
        flexDirection: "column",
        gap: 8,
        maxHeight: "calc(100vh - 24px)",
        overflow: "auto",
      }}
    >
      <div
        // job-0284 — testid only so the mobile drawer scope (global.css) can
        // give this header its own floating-card surface; desktop rendering
        // is untouched (attribute carries no style).
        data-testid="grace2-cases-header"
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: 4,
        }}
      >
        <strong style={{ fontSize: 13, color: "#ddd" }}>Cases</strong>
        <button
          data-testid="grace2-cases-new"
          aria-label="Create a new Case"
          title="Create a new Case"
          onClick={onCreate}
          style={{
            background: "#3b82f6",
            color: "#fff",
            border: "none",
            borderRadius: 6,
            // F58 — icon-only; comfortable >=40px touch target.
            width: 40,
            height: 40,
            cursor: "pointer",
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            // job-0166 — buttons need explicit fontFamily.
            fontFamily: "inherit",
          }}
        >
          <IconAdd size={20} weight="bold" />
        </button>
      </div>

      {sortedCases.length === 0 && (
        <div
          data-testid="grace2-cases-empty"
          style={{
            color: "#999",
            background: "rgba(255,255,255,0.03)",
            border: "1px dashed #444",
            borderRadius: 6,
            padding: 12,
            textAlign: "center",
            lineHeight: 1.4,
          }}
        >
          Start a Case to save your work and chat history.
        </div>
      )}

      <div
        data-testid="grace2-cases-list"
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 6,
        }}
      >
        {sortedCases.map((c) => (
          <CaseRow
            key={c.case_id}
            c={c}
            active={c.case_id === activeCaseId}
            onSelect={() => onSelect(c.case_id)}
            onRenameSubmit={(next) => onRename(c.case_id, next)}
            onArchive={() => onArchive(c.case_id)}
            onRequestDelete={() => setPendingDeleteId(c.case_id)}
          />
        ))}
      </div>

      {pendingCase && (
        <ConfirmationDialog
          testId="grace2-case-delete-dialog"
          title="Delete Case?"
          message={`This permanently removes "${pendingCase.title}" from your Cases list. Layers and chat history will no longer be recoverable from the left rail.`}
          confirmLabel="Delete"
          onConfirm={() => {
            onDelete(pendingCase.case_id);
            setPendingDeleteId(null);
          }}
          onCancel={() => setPendingDeleteId(null)}
        />
      )}
    </div>
  );
}
