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

  // job-0284 — mobile single back affordance: the "Cases" breadcrumb link
  // IS the back button; the ← arrow is NOT rendered ("cases should be the
  // back button, no need for another one").
  it("mobile: exactly ONE back affordance — the Cases link (no ← arrow)", () => {
    const onBack = vi.fn();
    render(<CaseView caseTitle="Test" onBack={onBack} mobile />);
    expect(screen.queryByTestId("grace2-case-view-back")).toBeNull();
    const casesLink = screen.getByTestId("grace2-case-view-cases-link");
    expect(casesLink.textContent).toBe("Cases");
    fireEvent.click(casesLink);
    expect(onBack).toHaveBeenCalledTimes(1);
  });

  it("desktop (default) keeps BOTH the ← arrow and the Cases link", () => {
    render(<CaseView caseTitle="Test" onBack={vi.fn()} />);
    expect(screen.getByTestId("grace2-case-view-back")).toBeTruthy();
    expect(screen.getByTestId("grace2-case-view-cases-link")).toBeTruthy();
  });

  // job-0350 — a long case title must TRUNCATE with an ellipsis, not hard-clip.
  // The flexbox-ellipsis gotcha: text-overflow:ellipsis only engages on a flex
  // child when min-width:0 lets it shrink below content size. Lock both the
  // title span styles AND the container's overflow guard.
  it("long case title truncates with ellipsis (flex min-width:0 set)", () => {
    render(
      <CaseView
        caseTitle="NWS Severe Weather Alerts State Florida and surrounding counties"
        onBack={vi.fn()}
        mobile
      />,
    );
    const title = screen.getByTestId("grace2-case-view-title");
    expect(title.style.overflow).toBe("hidden");
    expect(title.style.textOverflow).toBe("ellipsis");
    expect(title.style.whiteSpace).toBe("nowrap");
    // THE fix — without min-width:0 the flex child won't shrink and ellipsis
    // never engages (the reported breadcrumb cutoff).
    expect(title.style.minWidth).toBe("0");
    // full title preserved as a hover tooltip even when visually truncated.
    expect(title.getAttribute("title")).toContain("Florida");
    const crumb = screen.getByTestId("grace2-case-view-breadcrumb");
    expect(crumb.style.overflow).toBe("hidden");
    expect(crumb.style.minWidth).toBe("0");
  });
});
