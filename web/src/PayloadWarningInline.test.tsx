// GRACE-2 web — PayloadWarningInline unit tests (job-0127).
//
// Verifies the inline chat card:
//   1. Renders tool name + estimated MB + threshold MB.
//   2. Renders one button per advertised option.
//   3. proceed button → onDecide("proceed", null).
//   4. cancel button → onDecide("cancel", null).
//   5. Narrow scope (with alternative_args) → onDecide("narrow_scope", alt).
//   6. Narrow scope (no alternative_args) → opens clarifier; submit dispatches.
//   7. Hard-cap path: when "proceed" not in options, button is not rendered.
//   8. After a decision, buttons disable and "Sent: <decision>" appears.

import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { PayloadWarningInline } from "./components/PayloadWarningInline";
import { PayloadWarningEnvelopePayload } from "./contracts";

function makeWarning(
  partial: Partial<PayloadWarningEnvelopePayload> = {},
): PayloadWarningEnvelopePayload {
  return {
    envelope_type: "tool-payload-warning",
    warning_id: partial.warning_id ?? "01J0000000000000000000WID1",
    tool_name: partial.tool_name ?? "fetch_nexrad_reflectivity",
    tool_args: partial.tool_args ?? {
      bbox: [-82.5, 26.5, -82.0, 27.0],
      bands: ["reflectivity"],
    },
    estimated_mb: partial.estimated_mb ?? 87.3,
    threshold_mb: partial.threshold_mb ?? 25,
    recommendation:
      partial.recommendation ??
      "Consider narrowing bbox to a single county.",
    alternative_args:
      partial.alternative_args === undefined
        ? { bbox: [-82.2, 26.7, -82.1, 26.8] }
        : partial.alternative_args,
    options: partial.options ?? ["proceed", "cancel", "narrow_scope"],
    ttl_seconds: partial.ttl_seconds ?? 300,
  };
}

describe("PayloadWarningInline — header", () => {
  it("renders tool name + estimated MB + threshold MB", () => {
    render(
      <PayloadWarningInline warning={makeWarning()} onDecide={vi.fn()} />,
    );
    expect(screen.getByTestId("payload-warning-tool")).toHaveTextContent(
      "fetch_nexrad_reflectivity",
    );
    expect(
      screen.getByTestId("payload-warning-estimated-mb"),
    ).toHaveTextContent("87.3");
    expect(
      screen.getByTestId("payload-warning-threshold-mb"),
    ).toHaveTextContent("25");
    expect(
      screen.getByTestId("payload-warning-recommendation"),
    ).toHaveTextContent("narrowing bbox");
  });
});

describe("PayloadWarningInline — proceed", () => {
  it("calls onDecide('proceed', null) when Proceed clicked", () => {
    const onDecide = vi.fn();
    render(<PayloadWarningInline warning={makeWarning()} onDecide={onDecide} />);
    fireEvent.click(screen.getByTestId("payload-warning-button-proceed"));
    expect(onDecide).toHaveBeenCalledTimes(1);
    expect(onDecide).toHaveBeenCalledWith("proceed", null);
    expect(screen.getByTestId("payload-warning-sent")).toHaveTextContent(
      "proceed",
    );
  });
});

describe("PayloadWarningInline — cancel", () => {
  it("calls onDecide('cancel', null) when Cancel clicked", () => {
    const onDecide = vi.fn();
    render(<PayloadWarningInline warning={makeWarning()} onDecide={onDecide} />);
    fireEvent.click(screen.getByTestId("payload-warning-button-cancel"));
    expect(onDecide).toHaveBeenCalledWith("cancel", null);
  });
});

describe("PayloadWarningInline — narrow scope with alternative_args", () => {
  it("dispatches alternative_args directly when present", () => {
    const onDecide = vi.fn();
    const alt = { bbox: [-82.2, 26.7, -82.1, 26.8] };
    render(
      <PayloadWarningInline
        warning={makeWarning({ alternative_args: alt })}
        onDecide={onDecide}
      />,
    );
    fireEvent.click(screen.getByTestId("payload-warning-button-narrow_scope"));
    expect(onDecide).toHaveBeenCalledTimes(1);
    expect(onDecide).toHaveBeenCalledWith("narrow_scope", alt);
    // Clarifier dialog NOT opened — alternative was used directly.
    expect(
      screen.queryByTestId("payload-warning-clarifier"),
    ).not.toBeInTheDocument();
  });
});

describe("PayloadWarningInline — narrow scope clarifier (no alternative_args)", () => {
  it("opens the JSON clarifier when no alternative_args", () => {
    const onDecide = vi.fn();
    render(
      <PayloadWarningInline
        warning={makeWarning({ alternative_args: null })}
        onDecide={onDecide}
      />,
    );
    fireEvent.click(screen.getByTestId("payload-warning-button-narrow_scope"));
    // Clarifier appears; onDecide NOT called yet.
    expect(onDecide).not.toHaveBeenCalled();
    expect(screen.getByTestId("payload-warning-clarifier")).toBeInTheDocument();
    const textarea = screen.getByTestId(
      "payload-warning-clarifier-textarea",
    ) as HTMLTextAreaElement;
    // Pre-populated with current tool_args.
    expect(textarea.value).toContain("bbox");
    // User edits the JSON.
    fireEvent.change(textarea, {
      target: { value: '{"bbox":[-82.3,26.6,-82.2,26.7]}' },
    });
    fireEvent.click(screen.getByTestId("payload-warning-clarifier-submit"));
    expect(onDecide).toHaveBeenCalledWith("narrow_scope", {
      bbox: [-82.3, 26.6, -82.2, 26.7],
    });
  });

  it("surfaces a JSON error when clarifier text is malformed", () => {
    const onDecide = vi.fn();
    render(
      <PayloadWarningInline
        warning={makeWarning({ alternative_args: null })}
        onDecide={onDecide}
      />,
    );
    fireEvent.click(screen.getByTestId("payload-warning-button-narrow_scope"));
    const textarea = screen.getByTestId(
      "payload-warning-clarifier-textarea",
    ) as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: "not-json" } });
    fireEvent.click(screen.getByTestId("payload-warning-clarifier-submit"));
    expect(onDecide).not.toHaveBeenCalled();
    expect(
      screen.getByTestId("payload-warning-clarifier-error"),
    ).toHaveTextContent("Invalid JSON");
  });
});

describe("PayloadWarningInline — hard cap", () => {
  it("does not render the Proceed button when options omit it", () => {
    render(
      <PayloadWarningInline
        warning={makeWarning({
          options: ["cancel", "narrow_scope"],
          estimated_mb: 300,
          threshold_mb: 250,
        })}
        onDecide={vi.fn()}
      />,
    );
    expect(
      screen.queryByTestId("payload-warning-button-proceed"),
    ).not.toBeInTheDocument();
    expect(
      screen.getByTestId("payload-warning-button-cancel"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("payload-warning-button-narrow_scope"),
    ).toBeInTheDocument();
  });
});

describe("PayloadWarningInline — post-decision state", () => {
  it("disables all buttons after a decision is sent", () => {
    render(<PayloadWarningInline warning={makeWarning()} onDecide={vi.fn()} />);
    fireEvent.click(screen.getByTestId("payload-warning-button-proceed"));
    const proceedBtn = screen.getByTestId(
      "payload-warning-button-proceed",
    ) as HTMLButtonElement;
    expect(proceedBtn.disabled).toBe(true);
  });
});

describe("PayloadWarningInline — Invariant 9 (no cost theater)", () => {
  it("renders no dollar / latency / quota figure anywhere", () => {
    const { container } = render(
      <PayloadWarningInline warning={makeWarning()} onDecide={vi.fn()} />,
    );
    const text = container.textContent ?? "";
    expect(text).not.toMatch(/\$|USD|cost|quota|latency/i);
  });
});
