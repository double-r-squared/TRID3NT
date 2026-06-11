// GRACE-2 web — MobileDrawer (job-0278, mobile-friendly UI).
//
// Mobile (<768px) replacement for the desktop left rail: a slide-in drawer
// hidden by default, opened via the top-left ☰ button (44px touch target),
// rendered as a full-height left-anchored overlay with its own backdrop.
// Tapping the backdrop closes it; App.tsx also closes it when a Case row is
// selected. The drawer hosts the SAME children the desktop rail shows
// (CasesPanel at root, CaseView + LayerPanel inside a Case) plus the
// Settings/Secrets pills folded into its footer.
//
// Mounted ONLY when useIsMobile() is true — desktop rendering is untouched.
//
// The `grace2-mobile-touch` class scopes the global.css touch-target bump
// (min 44px on Case-row / breadcrumb / pill buttons) to this surface.

export interface MobileDrawerButtonProps {
  onClick: () => void;
  /** Mirrors drawer visibility for aria-expanded. */
  open: boolean;
}

/**
 * Top-left ☰ opener. 44x44 — Apple HIG minimum touch target. z-index 30
 * matches the desktop hamburgers (above panels z=20 / legend z=10, below
 * the drawer backdrop z=40 so the open drawer covers it).
 */
export function MobileDrawerButton({
  onClick,
  open,
}: MobileDrawerButtonProps): JSX.Element {
  return (
    <button
      data-testid="grace2-mobile-drawer-button"
      aria-label="Open cases and layers"
      aria-expanded={open}
      aria-controls="grace2-mobile-drawer"
      onClick={onClick}
      style={{
        position: "absolute",
        top: 12,
        left: 12,
        width: 44,
        height: 44,
        background: "rgba(20,20,25,0.85)",
        border: "1px solid #444",
        borderRadius: 8,
        color: "#ccc",
        padding: 0,
        cursor: "pointer",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        fontSize: 20,
        zIndex: 30,
        lineHeight: 1,
      }}
    >
      ☰
    </button>
  );
}

export interface MobileDrawerProps {
  open: boolean;
  /** Backdrop tap / programmatic close (Case selected, layer-panel ×). */
  onClose: () => void;
  children: React.ReactNode;
}

/**
 * The drawer surface itself. Returns null when closed (nothing in the DOM —
 * the map stays full-screen underneath). When open: dimmed backdrop over
 * the whole viewport (tap = close) + a full-height panel from the left
 * edge, width min(320px, 85vw).
 */
export function MobileDrawer({
  open,
  onClose,
  children,
}: MobileDrawerProps): JSX.Element | null {
  if (!open) return null;
  return (
    <>
      <div
        data-testid="grace2-mobile-drawer-backdrop"
        aria-hidden="true"
        onClick={onClose}
        style={{
          position: "absolute",
          inset: 0,
          background: "rgba(0,0,0,0.45)",
          zIndex: 40,
        }}
      />
      <div
        id="grace2-mobile-drawer"
        data-testid="grace2-mobile-drawer"
        className="grace2-mobile-touch"
        role="dialog"
        aria-label="Cases and layers"
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          bottom: 0,
          width: "min(320px, 85vw)",
          background: "rgba(15,15,20,0.97)",
          borderRight: "1px solid #333",
          boxShadow: "4px 0 24px rgba(0,0,0,0.5)",
          zIndex: 41,
          display: "flex",
          flexDirection: "column",
          gap: 8,
          padding: 12,
          boxSizing: "border-box",
          overflow: "hidden",
        }}
      >
        {children}
      </div>
    </>
  );
}
