// GRACE-2 web — BottomRowButtons (job-0143, sprint-12-mega Wave 4).
//
// The [⚙ Settings] button row that sits underneath the left-rail panel.
// Settings opens a full-screen popup (handled in App.tsx). Styled as a
// subtle rounded pill, dark-theme aware.
//
// job-0321 F29: the standalone [Secrets] pill is RETIRED — API-key
// management now lives INSIDE the Settings popup (SettingsPopup's embedded
// SecretsPanel). The `onOpenSecrets` prop is kept OPTIONAL for backwards
// compatibility, but the Secrets pill only renders when it is supplied.

import { IconSettings, IconKey } from "./icons";

export interface BottomRowButtonsProps {
  onOpenSettings: () => void;
  /**
   * job-0321 F29 — OPTIONAL now. Secrets moved inside Settings, so callers
   * no longer wire this. When omitted, the Secrets pill is not rendered.
   */
  onOpenSecrets?: () => void;
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

// job-0284 — the inline (mobile drawer footer) pills float directly over the
// map now that the drawer surface is transparent: translucent hairline-card
// family (matches the floating desktop pills minus the blur — kept
// rgba/alpha-only like the rest of the mobile pass).
const inlinePillStyle: React.CSSProperties = {
  background: "rgba(18,19,24,0.85)",
  border: "1px solid rgba(255,255,255,0.10)",
  borderRadius: 999,
  boxShadow: "0 2px 12px rgba(0,0,0,0.25)",
  color: "#cfd4db",
  padding: "6px 14px",
  fontSize: 12,
  cursor: "pointer",
  display: "inline-flex",
  alignItems: "center",
  gap: 6,
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
        <IconSettings size={14} />
        <span>Settings</span>
      </button>
      {/* job-0321 F29 — the standalone Secrets pill is retired (API keys now
          live inside Settings). Rendered ONLY for legacy callers that still
          pass `onOpenSecrets`; new callers omit it. */}
      {onOpenSecrets && (
        <button
          data-testid="grace2-bottom-row-secrets"
          onClick={onOpenSecrets}
          style={pillStyle}
          aria-label="Open API keys"
        >
          <IconKey size={14} />
          <span>Secrets</span>
        </button>
      )}
    </div>
  );
}
