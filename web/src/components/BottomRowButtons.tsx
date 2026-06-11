// GRACE-2 web — BottomRowButtons (job-0143, sprint-12-mega Wave 4).
//
// The [⚙ Settings] [🔑 Secrets] button row that sits underneath the
// left-rail panel. Each button opens a full-screen popup (handled in
// App.tsx). Styled as subtle rounded pills, dark-theme aware.

export interface BottomRowButtonsProps {
  onOpenSettings: () => void;
  onOpenSecrets: () => void;
  /**
   * job-0278 — "floating" (default) is the desktop absolute bottom-left
   * placement, unchanged. "inline" renders the same pills in normal flow so
   * the mobile drawer can fold them into its footer.
   */
  variant?: "floating" | "inline";
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

const inlineRowStyle: React.CSSProperties = {
  display: "flex",
  flexDirection: "row",
  gap: 6,
  fontFamily:
    "-apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif",
};

// job-0280 drawer-footer rendering — kept byte-identical for the "inline"
// (mobile) variant: job-0280's mobile surfaces are the reference, not the
// target, of the job-0283 desktop sleekness pass.
const inlinePillStyle: React.CSSProperties = {
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

// job-0283 — desktop sleekness: the floating pills join the rail's surface
// family (hairline border, full-pill radius, blur) and step up to the 12px
// meta type size for legibility. Visual only; same controls, ids, handlers.
const floatingPillStyle: React.CSSProperties = {
  background: "rgba(18,19,24,0.92)",
  border: "1px solid rgba(255,255,255,0.08)",
  borderRadius: 999,
  boxShadow: "0 2px 12px rgba(0,0,0,0.35)",
  backdropFilter: "blur(6px)",
  WebkitBackdropFilter: "blur(6px)",
  color: "#cfd4db",
  padding: "6px 14px",
  fontSize: 12,
  cursor: "pointer",
  display: "inline-flex",
  alignItems: "center",
  gap: 6,
  fontFamily: "inherit",
};

export function BottomRowButtons({
  onOpenSettings,
  onOpenSecrets,
  variant = "floating",
}: BottomRowButtonsProps): JSX.Element {
  const pillStyle =
    variant === "inline" ? inlinePillStyle : floatingPillStyle;
  return (
    <div
      data-testid="grace2-bottom-row-buttons"
      data-variant={variant}
      style={variant === "inline" ? inlineRowStyle : rowStyle}
    >
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
