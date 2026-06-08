// GRACE-2 web — PayloadWarningInline (job-0127 → restyled in job-0145,
// sprint-12-mega Wave 4).
//
// Inline chat card the user sees when the agent's payload estimator
// projects a response larger than the warning threshold (default 25 MB).
// Shows: tool name, projected MB, threshold MB, the agent's recommendation,
// and one action per advertised option (Proceed / Cancel / Narrow scope).
//
// job-0145 restyle: now sits on top of the common `InlineChatCard`
// primitive so its visual language matches the SourceSuggestionInline and
// any future agent-emitted inline informational cards. Card variant is
// `danger` for the over-hard-cap path (no proceed option) and `warning`
// otherwise.
//
// When the user picks "Narrow scope" and the warning carried
// `alternative_args`, the card dispatches with those args directly. If
// `alternative_args` is absent, the card opens a small clarifier dialog
// (an editable JSON textarea seeded with the original tool_args) so the
// user can hand-edit before confirming.
//
// Invariant 9 (no cost theater): the card surfaces ONLY the payload MB +
// the threshold MB. No dollar / latency / quota figure.
//
// User-facing language discipline: NO surfacing of "Mode 2", "Tier 1/2",
// or "OQ-*". The agent's emission is what it is; this card uses plain
// language ("Large response expected", "Narrow scope").

import { useMemo, useState } from "react";
import {
  PayloadConfirmationDecision,
  PayloadWarningEnvelopePayload,
  PayloadWarningOption,
} from "../contracts";
import { InlineChatCard, InlineChatCardAction } from "./InlineChatCard";

const OPTION_LABEL: Record<PayloadWarningOption, string> = {
  proceed: "Proceed anyway",
  cancel: "Cancel",
  narrow_scope: "Narrow scope",
};

export interface PayloadWarningInlineProps {
  /** The agent-emitted warning payload. */
  warning: PayloadWarningEnvelopePayload;
  /**
   * Called when the user picks an action. The caller wires this into
   * GraceWs.sendPayloadConfirmation(warning.warning_id, decision, revised).
   * `revised` is null for proceed/cancel; for narrow_scope it's the dict
   * the user accepted (either `alternative_args` directly or a hand-edited
   * variant).
   */
  onDecide: (
    decision: PayloadConfirmationDecision,
    revised: Record<string, unknown> | null,
  ) => void;
}

export function PayloadWarningInline({
  warning,
  onDecide,
}: PayloadWarningInlineProps): JSX.Element {
  const [sent, setSent] = useState<PayloadConfirmationDecision | null>(null);
  const [showClarifier, setShowClarifier] = useState(false);
  const [editedJson, setEditedJson] = useState<string>(() =>
    JSON.stringify(
      warning.alternative_args ?? warning.tool_args ?? {},
      null,
      2,
    ),
  );
  const [jsonError, setJsonError] = useState<string | null>(null);

  const overHardCap = useMemo(
    () => !warning.options.includes("proceed"),
    [warning.options],
  );

  function decide(
    decision: PayloadConfirmationDecision,
    revised: Record<string, unknown> | null,
  ): void {
    setSent(decision);
    onDecide(decision, revised);
  }

  function handleProceed(): void {
    decide("proceed", null);
  }
  function handleCancel(): void {
    decide("cancel", null);
  }
  function handleNarrow(): void {
    // Path A: agent advertised `alternative_args` — dispatch with them
    // directly without opening the clarifier.
    if (warning.alternative_args && !showClarifier) {
      decide("narrow_scope", warning.alternative_args);
      return;
    }
    // Path B: open the clarifier so the user can edit args inline.
    setShowClarifier(true);
  }
  function handleClarifierSubmit(): void {
    let parsed: Record<string, unknown>;
    try {
      const obj = JSON.parse(editedJson);
      if (!obj || typeof obj !== "object" || Array.isArray(obj)) {
        setJsonError("Revised args must be a JSON object.");
        return;
      }
      parsed = obj as Record<string, unknown>;
    } catch (err) {
      setJsonError(
        `Invalid JSON: ${err instanceof Error ? err.message : String(err)}`,
      );
      return;
    }
    setJsonError(null);
    decide("narrow_scope", parsed);
  }

  // Translate the option tokens to InlineChatCard actions.
  const actions: InlineChatCardAction[] = warning.options.map((opt) => {
    const handler =
      opt === "proceed"
        ? handleProceed
        : opt === "cancel"
          ? handleCancel
          : handleNarrow;
    const tone: "primary" | "secondary" | "muted" =
      opt === "proceed" && !overHardCap
        ? "secondary" // "proceed" is the not-recommended path; muted
        : opt === "narrow_scope"
          ? "primary"
          : opt === "cancel"
            ? "muted"
            : "secondary";
    return {
      label: OPTION_LABEL[opt],
      onClick: handler,
      tone,
      disabled: sent !== null,
      testId: `payload-warning-button-${opt}`,
    };
  });

  // Body: metrics row + recommendation + (optional) clarifier
  const body = (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <div
        style={{
          display: "flex",
          gap: 12,
          fontSize: 11,
          color: "#9ca3af",
          flexWrap: "wrap",
        }}
      >
        <span
          data-testid="payload-warning-tool"
          style={{
            fontFamily:
              'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
            color: "#d1d5db",
          }}
        >
          {warning.tool_name}
        </span>
        <span data-testid="payload-warning-estimated-mb">
          Estimated:{" "}
          <strong style={{ color: "#e5e7eb" }}>
            {warning.estimated_mb.toFixed(1)} MB
          </strong>
        </span>
        <span data-testid="payload-warning-threshold-mb">
          Threshold:{" "}
          <strong style={{ color: "#e5e7eb" }}>
            {warning.threshold_mb.toFixed(0)} MB
          </strong>
        </span>
      </div>
      <div
        data-testid="payload-warning-recommendation"
        style={{ color: "#d1d5db", lineHeight: 1.45 }}
      >
        {warning.recommendation}
      </div>

      {showClarifier && (
        <div
          data-testid="payload-warning-clarifier"
          style={{ display: "flex", flexDirection: "column", gap: 4 }}
        >
          <label
            style={{ color: "#9ca3af", fontSize: 11 }}
            htmlFor={`payload-warning-clarifier-textarea-${warning.warning_id}`}
          >
            Revised args (JSON object):
          </label>
          <textarea
            id={`payload-warning-clarifier-textarea-${warning.warning_id}`}
            data-testid="payload-warning-clarifier-textarea"
            value={editedJson}
            onChange={(e) => setEditedJson(e.target.value)}
            rows={5}
            style={{
              background: "rgba(0,0,0,0.4)",
              color: "#e5e7eb",
              border: "1px solid #3f3f46",
              borderRadius: 6,
              padding: 8,
              fontFamily:
                'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
              fontSize: 11,
              resize: "vertical",
            }}
          />
          {jsonError && (
            <div
              data-testid="payload-warning-clarifier-error"
              style={{ color: "#fca5a5", fontSize: 11 }}
            >
              {jsonError}
            </div>
          )}
          <div style={{ display: "flex", gap: 6 }}>
            <button
              type="button"
              data-testid="payload-warning-clarifier-submit"
              onClick={handleClarifierSubmit}
              disabled={sent !== null}
              style={clarifierBtnStyle("primary", sent !== null)}
            >
              Submit revised args
            </button>
            <button
              type="button"
              data-testid="payload-warning-clarifier-cancel"
              onClick={() => {
                setShowClarifier(false);
                setJsonError(null);
              }}
              disabled={sent !== null}
              style={clarifierBtnStyle("secondary", sent !== null)}
            >
              Back
            </button>
          </div>
        </div>
      )}
    </div>
  );

  return (
    <div
      data-testid="payload-warning-inline"
      data-warning-id={warning.warning_id}
    >
      <InlineChatCard
        variant={overHardCap ? "danger" : "warning"}
        title={
          overHardCap
            ? "Response too large — cannot proceed"
            : "Large response expected"
        }
        body={body}
        actions={showClarifier ? [] : actions}
        testId="payload-warning-card"
        ariaLabel="Large payload warning"
        footer={
          sent !== null ? (
            <span data-testid="payload-warning-sent">
              Sent: <strong>{sent}</strong>
            </span>
          ) : undefined
        }
      />
    </div>
  );
}

function clarifierBtnStyle(
  tone: "primary" | "secondary",
  disabled: boolean,
): React.CSSProperties {
  if (disabled) {
    return {
      background: "rgba(255,255,255,0.04)",
      color: "#555",
      border: "1px solid #333",
      borderRadius: 6,
      padding: "5px 10px",
      fontSize: 11,
      fontWeight: 600,
      cursor: "default",
      fontFamily: "inherit",
    };
  }
  if (tone === "primary") {
    return {
      background: "#eab308",
      color: "#0b0b0e",
      border: "1px solid #eab308",
      borderRadius: 6,
      padding: "5px 10px",
      fontSize: 11,
      fontWeight: 600,
      cursor: "pointer",
      fontFamily: "inherit",
    };
  }
  return {
    background: "rgba(255,255,255,0.05)",
    color: "#e5e7eb",
    border: "1px solid #3f3f46",
    borderRadius: 6,
    padding: "5px 10px",
    fontSize: 11,
    fontWeight: 600,
    cursor: "pointer",
    fontFamily: "inherit",
  };
}
