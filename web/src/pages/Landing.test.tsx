// GRACE-2 web — landing page content tests (job-0285).
//
// Pins the load-bearing content of the public landing page: the hero CTA
// targets "/app" (the always-app route), the agent credit is present and names
// the real platform (AWS Bedrock / Anthropic Claude — the product moved off
// Google Gemini), the privacy-policy link exists (OAuth consent screen
// prerequisite), and the Resume-session CTA variant renders for returning
// visitors.

import { describe, it, expect, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { Landing } from "./Landing";

afterEach(cleanup);

describe("Landing — hero", () => {
  it("renders the primary CTA pointing at /app with 'Launch GRACE-2'", () => {
    render(<Landing />);
    const cta = screen.getByTestId("grace2-landing-cta");
    expect(cta).toHaveAttribute("href", "/app");
    expect(cta).toHaveTextContent(/launch grace-2/i);
  });

  it("renders the 'Resume session' CTA variant when hasSession is true", () => {
    render(<Landing hasSession />);
    const cta = screen.getByTestId("grace2-landing-cta");
    expect(cta).toHaveAttribute("href", "/app");
    expect(cta).toHaveTextContent(/resume session/i);
  });

  it("highlights Anthropic Claude on AWS in the hero badge", () => {
    render(<Landing />);
    expect(
      screen.getAllByText(/powered by anthropic claude on aws/i).length,
    ).toBeGreaterThanOrEqual(1);
  });

  it("sets the document title", () => {
    render(<Landing />);
    expect(document.title).toMatch(/GRACE-2/);
    expect(document.title).toMatch(/multi-hazard/i);
  });
});

describe("Landing — features band", () => {
  it("renders five feature cards", () => {
    render(<Landing />);
    expect(screen.getAllByTestId("grace2-landing-feature")).toHaveLength(5);
  });

  it("covers the headline capabilities", () => {
    render(<Landing />);
    expect(
      screen.getByText(/conversational flood modeling/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/real physics solvers/i)).toBeInTheDocument();
    expect(screen.getByText(/damage analytics/i)).toBeInTheDocument();
    expect(
      screen.getByText(/terrain, weather & wildlife/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/per-case workspaces/i)).toBeInTheDocument();
  });
});

describe("Landing — agent band + footer", () => {
  it("renders the Claude agent section", () => {
    render(<Landing />);
    expect(screen.getByText(/the agent is claude\./i)).toBeInTheDocument();
    // Phrase appears in both the band paragraph and the bullet list.
    expect(
      screen.getAllByText(/native function calling/i).length,
    ).toBeGreaterThanOrEqual(1);
  });

  it("links to the privacy policy in the footer", () => {
    render(<Landing />);
    const link = screen.getByTestId("grace2-landing-privacy-link");
    expect(link).toHaveAttribute("href", "/privacy");
  });

  it("credits the stack (AWS Bedrock · Amazon EC2 · QGIS)", () => {
    render(<Landing />);
    expect(
      screen.getByText(/aws bedrock · amazon ec2 · qgis server/i),
    ).toBeInTheDocument();
  });
});
