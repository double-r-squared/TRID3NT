// GRACE-2 web — SecretsPopup tests (job-0143 Wave 4; job-0151 flatten).
//
// Verifies:
//   1. SecretsPopup renders the wrapped SecretsPanel.
//   2. Close button invokes onClose.
//   3. Backdrop click closes; card click does NOT close.
//   4. Esc keypress closes.
//   5. Single card depth — grace2-secrets-panel has no nested card styling
//      (the panel element must NOT contain a child data-testid ending in "-card").

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { SecretsPopup } from "./components/SecretsPopup";

afterEach(() => cleanup());

const defaultProps = {
  secrets: [],
  caseId: null,
  onSecretAdd: vi.fn(),
  onSecretRevoke: vi.fn(),
  onClose: vi.fn(),
};

describe("SecretsPopup", () => {
  it("renders the SecretsPanel inside the overlay", () => {
    render(<SecretsPopup {...defaultProps} />);
    expect(screen.getByTestId("grace2-secrets-popup")).toBeTruthy();
    expect(screen.getByTestId("grace2-secrets-panel")).toBeTruthy();
  });

  it("close button invokes onClose", () => {
    const onClose = vi.fn();
    render(<SecretsPopup {...defaultProps} onClose={onClose} />);
    fireEvent.click(screen.getByTestId("grace2-secrets-popup-close"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("backdrop click closes; card click does NOT close", () => {
    const onClose = vi.fn();
    render(<SecretsPopup {...defaultProps} onClose={onClose} />);
    fireEvent.click(screen.getByTestId("grace2-secrets-popup"));
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByTestId("grace2-secrets-popup-card"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("Esc keypress closes the popup", () => {
    const onClose = vi.fn();
    render(<SecretsPopup {...defaultProps} onClose={onClose} />);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("single card depth — SecretsPanel contains no nested card element", () => {
    render(<SecretsPopup {...defaultProps} />);
    const panel = screen.getByTestId("grace2-secrets-panel");
    // The panel must not contain any element whose data-testid ends in "-card".
    // Previously SecretsPanel had its own card styling producing a nested card.
    const nestedCard = panel.querySelector("[data-testid$='-card']");
    expect(nestedCard).toBeNull();
  });

  it("popup header reads 'API Keys'", () => {
    render(<SecretsPopup {...defaultProps} />);
    const card = screen.getByTestId("grace2-secrets-popup-card");
    expect(card.querySelector("h2")?.textContent).toBe("API Keys");
  });
});
