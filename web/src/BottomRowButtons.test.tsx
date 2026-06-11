// GRACE-2 web — BottomRowButtons tests (job-0143, sprint-12-mega Wave 4).

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { BottomRowButtons } from "./components/BottomRowButtons";

afterEach(() => cleanup());

describe("BottomRowButtons", () => {
  it("renders Settings + Secrets buttons", () => {
    render(
      <BottomRowButtons
        onOpenSettings={vi.fn()}
        onOpenSecrets={vi.fn()}
      />,
    );
    expect(screen.getByTestId("grace2-bottom-row-buttons")).toBeTruthy();
    expect(screen.getByTestId("grace2-bottom-row-settings")).toBeTruthy();
    expect(screen.getByTestId("grace2-bottom-row-secrets")).toBeTruthy();
  });

  it("Settings button invokes onOpenSettings", () => {
    const onOpenSettings = vi.fn();
    render(
      <BottomRowButtons
        onOpenSettings={onOpenSettings}
        onOpenSecrets={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId("grace2-bottom-row-settings"));
    expect(onOpenSettings).toHaveBeenCalledTimes(1);
  });

  it("Secrets button invokes onOpenSecrets", () => {
    const onOpenSecrets = vi.fn();
    render(
      <BottomRowButtons
        onOpenSettings={vi.fn()}
        onOpenSecrets={onOpenSecrets}
      />,
    );
    fireEvent.click(screen.getByTestId("grace2-bottom-row-secrets"));
    expect(onOpenSecrets).toHaveBeenCalledTimes(1);
  });

  // job-0278 — mobile drawer footer variant.
  it("defaults to the floating (absolute bottom-left) variant", () => {
    render(
      <BottomRowButtons onOpenSettings={vi.fn()} onOpenSecrets={vi.fn()} />,
    );
    const row = screen.getByTestId("grace2-bottom-row-buttons");
    expect(row).toHaveAttribute("data-variant", "floating");
    expect(row.style.position).toBe("absolute");
  });

  it("inline variant renders in normal flow (mobile drawer footer)", () => {
    render(
      <BottomRowButtons
        onOpenSettings={vi.fn()}
        onOpenSecrets={vi.fn()}
        variant="inline"
      />,
    );
    const row = screen.getByTestId("grace2-bottom-row-buttons");
    expect(row).toHaveAttribute("data-variant", "inline");
    expect(row.style.position).toBe("");
    // Both pills still present + wired.
    expect(screen.getByTestId("grace2-bottom-row-settings")).toBeTruthy();
    expect(screen.getByTestId("grace2-bottom-row-secrets")).toBeTruthy();
  });

  // job-0283 — desktop sleekness pass: the floating pills moved to the
  // panel surface family; the inline (mobile drawer footer) pills keep the
  // job-0280 reference rendering byte-identical.
  it("floating variant pills use the desktop family (full-pill radius + hairline border)", () => {
    render(
      <BottomRowButtons onOpenSettings={vi.fn()} onOpenSecrets={vi.fn()} />,
    );
    const pill = screen.getByTestId("grace2-bottom-row-settings");
    expect(pill.style.borderRadius).toBe("999px");
    expect(pill.style.border.replace(/\s/g, "")).toContain(
      "rgba(255,255,255,0.08)",
    );
  });

  it("inline variant pills keep the job-0280 mobile rendering (radius 14, #444 border)", () => {
    render(
      <BottomRowButtons
        onOpenSettings={vi.fn()}
        onOpenSecrets={vi.fn()}
        variant="inline"
      />,
    );
    const pill = screen.getByTestId("grace2-bottom-row-settings");
    expect(pill.style.borderRadius).toBe("14px");
    expect(pill.style.border).toContain("#444");
  });
});
