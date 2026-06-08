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
});
