// GRACE-2 web — SecretsPopup (job-0143 Wave 4; job-0151 flatten).
//
// Full-screen overlay for API-key management. job-0151: flattened the
// card-within-card nesting — SecretsPanel's own card chrome was removed so
// this overlay is the single card surface, matching SettingsPopup in width
// and close-X position.

import { useEffect } from "react";
import { SecretsPanel } from "./SecretsPanel";
import type { ProviderID, SecretRecord } from "../contracts";

export interface SecretsPopupProps {
  /** Current list of secrets — same shape SecretsPanel consumes. */
  secrets: SecretRecord[];
  /** Active case id for scoping (null = user-wide only). */
  caseId: string | null;
  onSecretAdd: (payload: {
    provider: ProviderID;
    case_id: string | null;
    label: string | null;
    key_value: string;
  }) => void;
  onSecretRevoke: (secretId: string) => void;
  /** Dismiss handler. */
  onClose: () => void;
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
  border: "1px solid #444",
  borderRadius: 12,
  // Width matches SettingsPopup for visual parity.
  width: "min(480px, 92vw)",
  maxHeight: "85vh",
  overflowY: "auto",
  color: "#e8eaf0",
  boxShadow: "0 24px 64px rgba(0,0,0,0.55)",
  position: "relative",
  // Padding matches SettingsPopup: 28px top (for close-X), 30px sides, 24px bottom.
  padding: "28px 30px 24px",
};

const headerStyle: React.CSSProperties = {
  fontSize: 20,
  fontWeight: 600,
  margin: "0 0 16px",
  color: "#e8eaf0",
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
  borderRadius: 6,
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
};

export function SecretsPopup({
  secrets,
  caseId,
  onSecretAdd,
  onSecretRevoke,
  onClose,
}: SecretsPopupProps): JSX.Element {
  useEffect(() => {
    function onKey(e: KeyboardEvent): void {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      data-testid="grace2-secrets-popup"
      role="dialog"
      aria-modal="true"
      aria-label="API keys"
      style={overlayStyle}
      onClick={onClose}
    >
      <div
        data-testid="grace2-secrets-popup-card"
        style={cardStyle}
        onClick={(e) => e.stopPropagation()}
      >
        <button
          data-testid="grace2-secrets-popup-close"
          aria-label="Close API keys"
          onClick={onClose}
          style={closeBtnStyle}
        >
          ✕
        </button>
        <h2 style={headerStyle}>API Keys</h2>
        <SecretsPanel
          secrets={secrets}
          caseId={caseId}
          onSecretAdd={onSecretAdd}
          onSecretRevoke={onSecretRevoke}
        />
      </div>
    </div>
  );
}
