// GRACE-2 web — ConfirmationDialog (job-0137, sprint-12-mega Wave 3).
//
// Small reusable modal that gates a destructive action behind explicit user
// confirmation. Used by CasesPanel's delete button (FR-MP-6) — the SRS Memory
// "Confirmation before consequence" invariant binds here: deleting a Case
// soft-deletes it server-side (Persistence.delete_case), so we want the user
// to actively acknowledge the action before we emit `case-command(delete)`.
//
// Styled to match the PayloadWarningInline / SecretsPanel dark-theme overlay
// aesthetic. Renders as a centered, dimmed-backdrop modal. Esc / backdrop
// click triggers Cancel; Enter triggers Confirm.
//
// Invariant 9 (no cost theater): no cost / quota / quote field on the dialog.
// Pure user-facing confirmation surface.

import { useEffect, useRef } from "react";

export interface ConfirmationDialogProps {
  /** Title rendered at the top of the modal. Short — e.g. "Delete Case?". */
  title: string;
  /** Body text below the title. May span multiple lines. */
  message: string;
  /** Label for the destructive confirm button. e.g. "Delete". */
  confirmLabel: string;
  /** Label for the safe cancel button. Defaults to "Cancel". */
  cancelLabel?: string;
  /** Called when the user clicks Confirm (or presses Enter). */
  onConfirm: () => void;
  /** Called when the user clicks Cancel / backdrop / presses Esc. */
  onCancel: () => void;
  /** Optional test id; defaults to "grace2-confirmation-dialog". */
  testId?: string;
}

export function ConfirmationDialog({
  title,
  message,
  confirmLabel,
  cancelLabel = "Cancel",
  onConfirm,
  onCancel,
  testId = "grace2-confirmation-dialog",
}: ConfirmationDialogProps): JSX.Element {
  const confirmRef = useRef<HTMLButtonElement | null>(null);

  // Focus the confirm button on mount + bind Esc / Enter.
  useEffect(() => {
    confirmRef.current?.focus();
    function onKey(ev: KeyboardEvent): void {
      if (ev.key === "Escape") {
        ev.preventDefault();
        onCancel();
      } else if (ev.key === "Enter") {
        ev.preventDefault();
        onConfirm();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onConfirm, onCancel]);

  return (
    <div
      data-testid={`${testId}-backdrop`}
      onClick={onCancel}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.55)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 2000,
      }}
    >
      <div
        data-testid={testId}
        role="dialog"
        aria-modal="true"
        aria-labelledby={`${testId}-title`}
        onClick={(e) => e.stopPropagation()}
        style={{
          background: "rgba(25,25,30,0.98)",
          border: "1px solid #444",
          borderRadius: 8,
          padding: 20,
          width: 360,
          maxWidth: "90vw",
          color: "#eee",
          fontSize: 13,
          display: "flex",
          flexDirection: "column",
          gap: 12,
          boxShadow: "0 8px 24px rgba(0,0,0,0.6)",
        }}
      >
        <strong
          id={`${testId}-title`}
          style={{ fontSize: 15, color: "#f88" }}
        >
          {title}
        </strong>
        <div
          data-testid={`${testId}-message`}
          style={{ color: "#ccc", lineHeight: 1.5 }}
        >
          {message}
        </div>
        <div
          style={{
            display: "flex",
            gap: 8,
            justifyContent: "flex-end",
            marginTop: 4,
          }}
        >
          <button
            data-testid={`${testId}-cancel`}
            onClick={onCancel}
            style={btnStyle("#6b7280")}
          >
            {cancelLabel}
          </button>
          <button
            ref={confirmRef}
            data-testid={`${testId}-confirm`}
            onClick={onConfirm}
            style={btnStyle("#ef4444")}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

function btnStyle(color: string): React.CSSProperties {
  return {
    background: color,
    color: "#000",
    border: "none",
    borderRadius: 4,
    padding: "6px 14px",
    fontSize: 12,
    fontWeight: 600,
    cursor: "pointer",
  };
}
