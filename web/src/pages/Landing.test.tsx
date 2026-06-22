// GRACE-2 web - landing page content tests.
//
// Pins the load-bearing content of the public landing page: the hero CTA
// targets "/app" (the always-app route), the agent credit is present and names
// the real platform (AWS Bedrock / Anthropic Claude - the product runs on
// AWS, not Google Gemini), the privacy-policy link exists (OAuth consent
// screen prerequisite), and the Resume-session CTA variant renders for
// returning visitors. Also pins the hazard/engine matrix the page now sells.

import { describe, it, expect, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { Landing } from "./Landing";

afterEach(cleanup);

describe("Landing - hero", () => {
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

describe("Landing - hazard matrix", () => {
  it("renders a card for every shipped hazard engine", () => {
    render(<Landing />);
    expect(screen.getAllByTestId("grace2-landing-hazard")).toHaveLength(8);
  });

  it("covers the headline hazards and their engines", () => {
    render(<Landing />);
    expect(screen.getByText(/coastal flood \+ waves/i)).toBeInTheDocument();
    expect(screen.getByText(/urban flood/i)).toBeInTheDocument();
    expect(
      screen.getByText(/groundwater contamination/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/seismic hazard \(PSHA\)/i)).toBeInTheDocument();
    expect(
      screen.getByText(/dam-break \+ shallow water/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/landslide susceptibility/i)).toBeInTheDocument();
    // Engine chips name the real solvers (some names also appear in body
    // copy, so assert presence rather than uniqueness).
    expect(
      screen.getAllByText(/SFINCS \+ SnapWave/).length,
    ).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText(/PySWMM/).length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText(/MODFLOW 6/).length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText(/OpenQuake/).length).toBeGreaterThanOrEqual(1);
  });
});

describe("Landing - how it works", () => {
  it("renders the three-step chat -> model -> map flow", () => {
    render(<Landing />);
    expect(
      screen.getByRole("heading", { level: 3, name: /^Chat$/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { level: 3, name: /^Model$/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { level: 3, name: /^Map$/ }),
    ).toBeInTheDocument();
  });
});

describe("Landing - agent band + footer", () => {
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

  it("credits the AWS stack (Bedrock · EC2 · Batch)", () => {
    render(<Landing />);
    expect(
      screen.getByText(/aws bedrock · amazon ec2 · aws batch/i),
    ).toBeInTheDocument();
  });
});
