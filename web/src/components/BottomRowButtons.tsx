// GRACE-2 web — BottomRowButtons (job-0143, sprint-12-mega Wave 4).
//
// The [⚙ Settings] [🔑 Secrets] button row that sits underneath the
// left-rail panel. Each button opens a full-screen popup (handled in
// App.tsx). Styled as subtle rounded pills, dark-theme aware.

export interface BottomRowButtonsProps {
  onOpenSettings: () => void;
  onOpenSecrets: () => void;
}

const rowStyle: React.CSSProperties = {
  position: "absolute",
  left: 12,
  bottom: 12,
  display: "flex",
  flexDirection: "row",
  gap: 6,
  zIndex: 20,
  fontFamily:
    "-apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif",
};

const pillStyle: React.CSSProperties = {
  background: "rgba(20,20,25,0.85)",
  border: "1px solid #444",
  borderRadius: 14,
  color: "#ddd",
  padding: "5px 12px",
  fontSize: 11,
  cursor: "pointer",
  display: "inline-flex",
  alignItems: "center",
  gap: 5,
  fontFamily: "inherit",
};

export function BottomRowButtons({
  onOpenSettings,
  onOpenSecrets,
}: BottomRowButtonsProps): JSX.Element {
  return (
    <div data-testid="grace2-bottom-row-buttons" style={rowStyle}>
      <button
        data-testid="grace2-bottom-row-settings"
        onClick={onOpenSettings}
        style={pillStyle}
        aria-label="Open settings"
      >
        <span aria-hidden="true">⚙</span>
        <span>Settings</span>
      </button>
      <button
        data-testid="grace2-bottom-row-secrets"
        onClick={onOpenSecrets}
        style={pillStyle}
        aria-label="Open API keys"
      >
        <span aria-hidden="true">🔑</span>
        <span>Secrets</span>
      </button>
    </div>
  );
}
