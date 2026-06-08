// GRACE-2 web — CaseView tests (job-0143, sprint-12-mega Wave 4).
//
// Verifies:
//   1. CaseView renders the breadcrumb back-arrow + Cases link + Case title.
//   2. Back-arrow click invokes onBack.
//   3. Cases link click also invokes onBack.
//   4. Children render below the breadcrumb.

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { CaseView } from "./components/CaseView";

afterEach(() => cleanup());

describe("CaseView", () => {
  it("renders breadcrumb with title", () => {
    render(<CaseView caseTitle="Hurricane Ian" onBack={vi.fn()} />);
    expect(screen.getByTestId("grace2-case-view")).toBeTruthy();
    expect(screen.getByTestId("grace2-case-view-breadcrumb")).toBeTruthy();
    expect(
      screen.getByTestId("grace2-case-view-title").textContent,
    ).toBe("Hurricane Ian");
  });

  it("back-arrow click invokes onBack", () => {
    const onBack = vi.fn();
    render(<CaseView caseTitle="Test" onBack={onBack} />);
    fireEvent.click(screen.getByTestId("grace2-case-view-back"));
    expect(onBack).toHaveBeenCalledTimes(1);
  });

  it("Cases link click also invokes onBack", () => {
    const onBack = vi.fn();
    render(<CaseView caseTitle="Test" onBack={onBack} />);
    fireEvent.click(screen.getByTestId("grace2-case-view-cases-link"));
    expect(onBack).toHaveBeenCalledTimes(1);
  });

  it("renders children below the breadcrumb", () => {
    render(
      <CaseView caseTitle="Test" onBack={vi.fn()}>
        <div data-testid="grace2-test-child">child node</div>
      </CaseView>,
    );
    expect(screen.getByTestId("grace2-test-child")).toBeTruthy();
  });
});
