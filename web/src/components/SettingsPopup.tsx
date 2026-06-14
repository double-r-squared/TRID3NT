// GRACE-2 web — SettingsPopup (job-0143, sprint-12-mega Wave 4).
//
// Full-screen overlay surfacing user-facing settings:
//   - Account: email or "Anonymous mode" + sign-in CTA, sign-out.
//   - Appearance: light/dark theme toggle.
//   - About: build version + commit SHA.
//
// Centralises auth controls that previously lived in the floating top-right
// identity chip. The chip is removed entirely by job-0143; Settings is the
// only place to view/change identity now.
//
// Render pattern matches AuthGate / Mode2OfferModal: full-viewport dim
// backdrop, centred card, Esc / click-backdrop / X to dismiss.

import { useEffect } from "react";
import type { MapTheme } from "../Map";

export interface SettingsPopupProps {
  /** Email of the signed-in user, or null if anonymous. */
  userEmail: string | null;
  /** Whether the user is signed in with a real Firebase identity. */
  isSignedIn: boolean;
  /** Current theme — drives the visible toggle state. */
  theme: MapTheme;
  /** Toggle theme handler. */
  onToggleTheme: () => void;
  /** Sign-out handler. App.tsx wires the real auth.signOut call. */
  onSignOut: () => void;
  /** Sign-in handler — only invoked when isSignedIn=false. */
  onSignInRequest: () => void;
  /** Close handler. App.tsx clears local visible state. */
  onClose: () => void;
  /**
   * Wave 4.10 C1: optional "View tools catalog" hook. When provided, a
   * link is surfaced under a "Tools" section that opens the ToolsCatalogPopup
   * (mounted by App.tsx). Kept optional so the existing test fixtures that
   * pre-date Wave 4.10 don't need to plumb the prop.
   */
  onOpenToolsCatalog?: () => void;
  /**
   * Wave 4.11 M7: optional "Routing quality" hook. When provided, a link
   * under the Tools section opens the RoutingQualityDashboard (mounted by
   * App.tsx). Surfaces aggregated tool-routing telemetry over the last 30
   * sessions. Kept optional for backwards-compat with older fixtures.
   */
  onOpenRoutingDashboard?: () => void;
}

const overlayStyle: React.CSSProperties = {
  position: "fixed",
  inset: 0,
  background: "rgba(0,0,0,0.55)",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  zIndex: 9_500,
  fontFamily:
    "-apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif",
};

const cardStyle: React.CSSProperties = {
  background: "rgba(20,22,30,0.98)",
  // job-0283 — hairline border joins the modal family (was solid #444).
  border: "1px solid rgba(255,255,255,0.10)",
  borderRadius: 12,
  width: "min(480px, 92vw)",
  maxHeight: "85vh",
  overflowY: "auto",
  color: "#e8eaf0",
  boxShadow: "0 24px 64px rgba(0,0,0,0.55)",
  position: "relative",
  padding: "28px 30px 24px",
};

const closeBtnStyle: React.CSSProperties = {
  position: "absolute",
  top: 12,
  right: 12,
  background: "transparent",
  border: "none",
  color: "#aaa",
  fontSize: 18,
  cursor: "pointer",
  width: 28,
  height: 28,
  borderRadius: 8,
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
};

const headerStyle: React.CSSProperties = {
  fontSize: 20,
  fontWeight: 600,
  margin: "0 0 16px",
  color: "#e8eaf0",
};

const sectionStyle: React.CSSProperties = {
  // job-0283 — hairline section divider (was #333), modal family.
  borderTop: "1px solid rgba(255,255,255,0.08)",
  paddingTop: 14,
  marginTop: 16,
};

const sectionTitleStyle: React.CSSProperties = {
  fontSize: 11,
  textTransform: "uppercase",
  letterSpacing: "0.06em",
  color: "#888",
  marginBottom: 8,
};

const valueStyle: React.CSSProperties = {
  fontSize: 13,
  color: "#cfd3dc",
  lineHeight: 1.55,
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 12,
  flexWrap: "wrap",
};

const buttonStyle: React.CSSProperties = {
  background: "rgba(40,42,52,0.9)",
  // job-0283 — hairline border + 8px radius (modal-family buttons).
  border: "1px solid rgba(255,255,255,0.14)",
  borderRadius: 8,
  color: "#ddd",
  padding: "5px 12px",
  fontSize: 12,
  cursor: "pointer",
  fontFamily: "inherit",
};

const primaryButtonStyle: React.CSSProperties = {
  ...buttonStyle,
  background: "#3b82f6",
  borderColor: "#3b82f6",
  color: "#fff",
  fontWeight: 600,
};

const ctaStyle: React.CSSProperties = {
  fontSize: 11,
  color: "#7aa7ff",
  background: "transparent",
  border: "none",
  cursor: "pointer",
  padding: 0,
  textDecoration: "underline",
  fontFamily: "inherit",
};

/** Build version label. Falls back to "dev" when VITE_BUILD_SHA isn't set. */
function buildSha(): string {
  const v = (import.meta.env.VITE_BUILD_SHA as string | undefined) ?? "";
  if (!v) return "dev";
  // Display the short SHA only (first 7 chars), matching git's default.
  return v.length > 7 ? v.slice(0, 7) : v;
}

export function SettingsPopup({
  userEmail,
  isSignedIn,
  theme,
  onToggleTheme,
  onSignOut,
  onSignInRequest,
  onClose,
  onOpenToolsCatalog,
  onOpenRoutingDashboard,
}: SettingsPopupProps): JSX.Element {
  // Esc-to-close (memory rule "Cancellation is first-class").
  useEffect(() => {
    function onKey(e: KeyboardEvent): void {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      data-testid="grace2-settings-popup"
      role="dialog"
      aria-modal="true"
      aria-label="Settings"
      style={overlayStyle}
      onClick={onClose}
    >
      <div
        data-testid="grace2-settings-popup-card"
        style={cardStyle}
        onClick={(e) => e.stopPropagation()}
      >
        <button
          data-testid="grace2-settings-popup-close"
          aria-label="Close settings"
          onClick={onClose}
          style={closeBtnStyle}
        >
          ✕
        </button>
        <h2 style={headerStyle}>Settings</h2>

        {/* Account section */}
        <div style={sectionStyle}>
          <div style={sectionTitleStyle}>Account</div>
          <div style={valueStyle}>
            <span data-testid="grace2-settings-account-label">
              {isSignedIn && userEmail
                ? userEmail
                : "Anonymous mode"}
            </span>
            {isSignedIn ? (
              <button
                data-testid="grace2-settings-signout"
                onClick={onSignOut}
                style={buttonStyle}
                aria-label="Sign out"
              >
                Sign out
              </button>
            ) : (
              <button
                data-testid="grace2-settings-signin"
                onClick={onSignInRequest}
                style={primaryButtonStyle}
                aria-label="Sign in to save your work"
              >
                Sign in
              </button>
            )}
          </div>
          {!isSignedIn && (
            <div
              data-testid="grace2-settings-account-cta"
              style={{
                fontSize: 11,
                color: "#9aa0ad",
                marginTop: 4,
                lineHeight: 1.5,
              }}
            >
              Sign in to save your work and sync Cases across devices.
            </div>
          )}
        </div>

        {/* Appearance section */}
        <div style={sectionStyle}>
          <div style={sectionTitleStyle}>Appearance</div>
          <div style={valueStyle}>
            <span>Theme</span>
            <button
              data-testid="grace2-settings-theme-toggle"
              onClick={onToggleTheme}
              style={buttonStyle}
              aria-pressed={theme === "dark"}
              aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
            >
              {theme === "dark" ? "Dark" : "Light"}
            </button>
          </div>
        </div>

        {/* Tools section (Wave 4.10 C1 + Wave 4.11 M7) — only when at least
            one tools-area hook is wired. */}
        {(onOpenToolsCatalog || onOpenRoutingDashboard) && (
          <div style={sectionStyle}>
            <div style={sectionTitleStyle}>Tools</div>
            {onOpenToolsCatalog && (
              <div style={valueStyle}>
                <span>Browse the agent's tool catalog</span>
                <button
                  data-testid="grace2-settings-open-tools-catalog"
                  onClick={onOpenToolsCatalog}
                  style={buttonStyle}
                  aria-label="View all tools"
                >
                  View all tools
                </button>
              </div>
            )}
            {onOpenRoutingDashboard && (
              <div style={{ ...valueStyle, marginTop: 10 }}>
                <span>Inspect tool-routing telemetry</span>
                <button
                  data-testid="grace2-settings-open-routing-dashboard"
                  onClick={onOpenRoutingDashboard}
                  style={buttonStyle}
                  aria-label="Routing quality"
                >
                  Routing quality
                </button>
              </div>
            )}
          </div>
        )}

        {/* About section */}
        <div style={sectionStyle}>
          <div style={sectionTitleStyle}>About</div>
          <div style={valueStyle}>
            <span>GRACE-2</span>
            <span
              data-testid="grace2-settings-build-sha"
              style={{ fontFamily: "monospace", fontSize: 12, color: "#aaa" }}
            >
              {buildSha()}
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}

export { ctaStyle };
