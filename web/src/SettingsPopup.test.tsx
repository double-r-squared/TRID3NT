// GRACE-2 web — SettingsPopup tests (job-0143, sprint-12-mega Wave 4).
//
// Verifies:
//   1. SettingsPopup renders Account / Appearance / About sections.
//   2. Email is displayed when isSignedIn=true; "Anonymous mode" + Sign-in
//      CTA when isSignedIn=false.
//   3. Sign-out button calls onSignOut.
//   4. Theme toggle calls onToggleTheme.
//   5. Close (X) button calls onClose.
//   6. Click on backdrop closes the popup; click on card does NOT close.
//   7. Esc keypress closes the popup.

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { SettingsPopup } from "./components/SettingsPopup";

afterEach(() => cleanup());

const defaultProps = {
  userEmail: "user@example.com",
  isSignedIn: true,
  theme: "light" as const,
  onToggleTheme: vi.fn(),
  onSignOut: vi.fn(),
  onSignInRequest: vi.fn(),
  onClose: vi.fn(),
};

describe("SettingsPopup", () => {
  it("renders Account / Appearance / About sections", () => {
    render(<SettingsPopup {...defaultProps} />);
    expect(screen.getByTestId("grace2-settings-popup")).toBeTruthy();
    expect(screen.getByText("Account")).toBeTruthy();
    expect(screen.getByText("Appearance")).toBeTruthy();
    expect(screen.getByText("About")).toBeTruthy();
  });

  it("displays the user email when isSignedIn=true", () => {
    render(<SettingsPopup {...defaultProps} userEmail="alice@example.com" isSignedIn={true} />);
    expect(
      screen.getByTestId("grace2-settings-account-label").textContent,
    ).toBe("alice@example.com");
    expect(screen.getByTestId("grace2-settings-signout")).toBeTruthy();
  });

  it("displays Anonymous mode + Sign-in CTA when isSignedIn=false", () => {
    render(
      <SettingsPopup {...defaultProps} userEmail={null} isSignedIn={false} />,
    );
    expect(
      screen.getByTestId("grace2-settings-account-label").textContent,
    ).toBe("Anonymous mode");
    expect(screen.getByTestId("grace2-settings-signin")).toBeTruthy();
    expect(
      screen.getByTestId("grace2-settings-account-cta").textContent,
    ).toMatch(/Sign in to save/);
  });

  it("Sign-out button invokes onSignOut", () => {
    const onSignOut = vi.fn();
    render(<SettingsPopup {...defaultProps} onSignOut={onSignOut} />);
    fireEvent.click(screen.getByTestId("grace2-settings-signout"));
    expect(onSignOut).toHaveBeenCalledTimes(1);
  });

  it("theme toggle invokes onToggleTheme", () => {
    const onToggleTheme = vi.fn();
    render(<SettingsPopup {...defaultProps} onToggleTheme={onToggleTheme} />);
    fireEvent.click(screen.getByTestId("grace2-settings-theme-toggle"));
    expect(onToggleTheme).toHaveBeenCalledTimes(1);
  });

  it("close button invokes onClose", () => {
    const onClose = vi.fn();
    render(<SettingsPopup {...defaultProps} onClose={onClose} />);
    fireEvent.click(screen.getByTestId("grace2-settings-popup-close"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("backdrop click closes; card click does NOT close", () => {
    const onClose = vi.fn();
    render(<SettingsPopup {...defaultProps} onClose={onClose} />);
    // Click the backdrop.
    fireEvent.click(screen.getByTestId("grace2-settings-popup"));
    expect(onClose).toHaveBeenCalledTimes(1);
    // Click the card itself — should NOT bubble.
    fireEvent.click(screen.getByTestId("grace2-settings-popup-card"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("Esc keypress closes the popup", () => {
    const onClose = vi.fn();
    render(<SettingsPopup {...defaultProps} onClose={onClose} />);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("renders a build SHA in the About section", () => {
    render(<SettingsPopup {...defaultProps} />);
    expect(screen.getByTestId("grace2-settings-build-sha").textContent).toBeTruthy();
  });

  // job-0321 F29 — API-key entry bundled inside Settings.
  describe("embedded API Keys section (F29)", () => {
    it("does NOT render the API Keys section when secrets props are absent", () => {
      // Legacy fixtures (defaultProps) don't plumb the secrets props — the
      // section must stay hidden so they render unchanged.
      render(<SettingsPopup {...defaultProps} />);
      expect(screen.queryByTestId("grace2-settings-api-keys")).toBeNull();
      expect(screen.queryByTestId("grace2-secrets-panel")).toBeNull();
    });

    it("renders the embedded SecretsPanel under an 'API Keys' header when wired", () => {
      render(
        <SettingsPopup
          {...defaultProps}
          secrets={[]}
          caseId={null}
          onSecretAdd={vi.fn()}
          onSecretRevoke={vi.fn()}
        />,
      );
      expect(screen.getByTestId("grace2-settings-api-keys")).toBeTruthy();
      expect(screen.getByText("API Keys")).toBeTruthy();
      // The SecretsPanel itself is rendered inline (its own data-testid).
      expect(screen.getByTestId("grace2-secrets-panel")).toBeTruthy();
    });

    it("passes secrets + caseId through to the embedded SecretsPanel", () => {
      const secrets = [
        {
          secret_id: "s1",
          provider: "ebird" as const,
          label: "my-ebird-key",
          is_active: true,
          last_used_at: null,
          vault_ref: "vault://abc",
          case_id: "case-7",
          added_at: "2026-01-01T00:00:00Z",
        },
      ];
      render(
        <SettingsPopup
          {...defaultProps}
          secrets={secrets}
          caseId="case-7"
          onSecretAdd={vi.fn()}
          onSecretRevoke={vi.fn()}
        />,
      );
      // The active secret row surfaces inside the embedded panel.
      expect(screen.getByTestId("grace2-secret-row-s1")).toBeTruthy();
      // The "This Case" scope radio is enabled because a caseId is present.
      const caseRadio = screen.getByTestId(
        "grace2-secret-scope-case",
      ) as HTMLInputElement;
      expect(caseRadio.disabled).toBe(false);
    });

    it("forwards add/revoke callbacks from the embedded panel", () => {
      const onSecretRevoke = vi.fn();
      const secrets = [
        {
          secret_id: "s2",
          provider: "nws" as const,
          label: null,
          is_active: true,
          last_used_at: null,
          vault_ref: "vault://def",
          case_id: null,
          added_at: "2026-01-01T00:00:00Z",
        },
      ];
      render(
        <SettingsPopup
          {...defaultProps}
          secrets={secrets}
          caseId={null}
          onSecretAdd={vi.fn()}
          onSecretRevoke={onSecretRevoke}
        />,
      );
      fireEvent.click(screen.getByTestId("grace2-secret-revoke-s2"));
      expect(onSecretRevoke).toHaveBeenCalledWith("s2");
    });

    it("only requires onSecretAdd AND onSecretRevoke together to show the section", () => {
      // onSecretAdd alone (no revoke) should not render the section — both
      // wires are required so the embedded panel is fully functional.
      render(
        <SettingsPopup
          {...defaultProps}
          secrets={[]}
          caseId={null}
          onSecretAdd={vi.fn()}
        />,
      );
      expect(screen.queryByTestId("grace2-settings-api-keys")).toBeNull();
    });
  });
});
