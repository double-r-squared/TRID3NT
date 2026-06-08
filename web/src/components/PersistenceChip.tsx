// GRACE-2 web — PersistenceChip (job-0137, sprint-12-mega Wave 3 — FR-MP-6).
//
// Small status indicator that surfaces whether the current Case session is
// being persisted to MongoDB and whether the user can actually persist work
// (signed in via Firebase Auth) or is in the anonymous dev path. Sits near
// the AuthPanel so the user can correlate persistence state with their auth
// state at a glance.
//
// States (closed enum):
//   - "saved"             — all in-flight case-commands have round-tripped
//                           (the default at rest).
//   - "saving"            — one or more case-commands awaiting server ack;
//                           CasesPanel sets this when it emits, useCases
//                           clears it on the next case-list / case-open frame.
//   - "anonymous"         — no signed-in user; persistence is best-effort
//                           against the session_id placeholder, surfaces
//                           "Sign in to save" explicitly.
//   - "disconnected"      — WS disconnected; persistence definitively not
//                           happening. Used in future once we wire status
//                           through here (placeholder for now).
//
// Invariant 9 (no cost theater): no cost / quota / quote field. Surface only
// the persistence state.

export type PersistenceState = "saved" | "saving" | "anonymous" | "disconnected";

const STATE_LABEL: Record<PersistenceState, string> = {
  saved: "Saved",
  saving: "Saving…",
  anonymous: "Sign in to save",
  disconnected: "Offline",
};

const STATE_COLOR: Record<PersistenceState, string> = {
  saved: "#22c55e",       // green
  saving: "#eab308",      // amber
  anonymous: "#9ca3af",   // gray
  disconnected: "#ef4444", // red
};

const STATE_GLYPH: Record<PersistenceState, string> = {
  saved: "●",
  saving: "◐",
  anonymous: "○",
  disconnected: "✕",
};

export interface PersistenceChipProps {
  /** Current persistence state — driven by App.tsx from auth + useCases. */
  state: PersistenceState;
  /** Optional override of the test id; defaults to "grace2-persistence-chip". */
  testId?: string;
}

export function PersistenceChip({
  state,
  testId = "grace2-persistence-chip",
}: PersistenceChipProps): JSX.Element {
  const color = STATE_COLOR[state];
  const label = STATE_LABEL[state];
  const glyph = STATE_GLYPH[state];
  return (
    <div
      data-testid={testId}
      data-state={state}
      title={label}
      aria-label={`Persistence: ${label}`}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        background: "rgba(20,20,25,0.85)",
        border: `1px solid ${color}`,
        borderRadius: 12,
        padding: "3px 9px",
        fontSize: 11,
        color: "#ddd",
        fontFamily:
          "-apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif",
        whiteSpace: "nowrap",
      }}
    >
      <span style={{ color, fontSize: 10 }}>{glyph}</span>
      <span data-testid={`${testId}-label`}>{label}</span>
    </div>
  );
}
