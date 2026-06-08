// GRACE-2 web — PayloadWarningInline (job-0127, sprint-12-mega Wave 2).
//
// Inline chat card the user sees when the agent's payload estimator
// projects a response larger than the warning threshold (default 25 MB).
// Shows: tool name, projected MB, threshold MB, the agent's recommendation,
// and one button per advertised option (Proceed / Cancel / Narrow scope).
//
// When the user picks "Narrow scope" and the warning carried
// `alternative_args`, the card shows a brief one-line summary of what the
// agent will narrow to and dispatches with those args. If `alternative_args`
// is absent, the card opens a tiny clarifier dialog (an editable JSON
// textarea seeded with the original tool_args) so the user can hand-edit
// before confirming.
//
// The card's onDecide callback is wired by Chat.tsx into
// GraceWs.sendPayloadConfirmation(). After a decision is made the card
// disables its buttons and shows a small "Sent: <decision>" footer so the
// user can see their choice was registered.
//
// Invariant 9 (no cost theater): the card surfaces ONLY the payload MB +
// the threshold MB. No dollar / latency / quota figure.

import { useMemo, useState } from "react";
import {
  PayloadConfirmationDecision,
  PayloadWarningEnvelopePayload,
  PayloadWarningOption,
} from "../contracts";

const OPTION_LABEL: Record<PayloadWarningOption, string> = {
  proceed: "Proceed",
  cancel: "Cancel",
  narrow_scope: "Narrow scope",
};

const OPTION_COLOR: Record<PayloadWarningOption, string> = {
  proceed: "#3b82f6",
  cancel: "#6b7280",
  narrow_scope: "#eab308",
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

  return (
    <div
      data-testid="payload-warning-inline"
      data-warning-id={warning.warning_id}
      style={{
        border: `1px solid ${overHardCap ? "#ef4444" : "#eab308"}`,
        borderLeft: `4px solid ${overHardCap ? "#ef4444" : "#eab308"}`,
        background: "rgba(40,30,20,0.85)",
        color: "#eee",
        padding: 10,
        borderRadius: 6,
        fontSize: 12,
        display: "flex",
        flexDirection: "column",
        gap: 6,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <strong style={{ fontSize: 13, color: overHardCap ? "#ef4444" : "#eab308" }}>
          {overHardCap ? "Large payload — hard cap" : "Large payload"}
        </strong>
        <span
          data-testid="payload-warning-tool"
          style={{ color: "#aaa", fontSize: 11, fontFamily: "monospace" }}
        >
          {warning.tool_name}
        </span>
      </div>
      <div style={{ display: "flex", gap: 12, fontSize: 11, color: "#ccc" }}>
        <span data-testid="payload-warning-estimated-mb">
          Estimated: <strong>{warning.estimated_mb.toFixed(1)} MB</strong>
        </span>
        <span data-testid="payload-warning-threshold-mb">
          Threshold: <strong>{warning.threshold_mb.toFixed(0)} MB</strong>
        </span>
      </div>
      <div
        data-testid="payload-warning-recommendation"
        style={{ color: "#ddd", lineHeight: 1.4 }}
      >
        {warning.recommendation}
      </div>

      {showClarifier && (
        <div
          data-testid="payload-warning-clarifier"
          style={{ display: "flex", flexDirection: "column", gap: 4 }}
        >
          <label style={{ color: "#aaa", fontSize: 11 }}>
            Revised args (JSON object):
          </label>
          <textarea
            data-testid="payload-warning-clarifier-textarea"
            value={editedJson}
            onChange={(e) => setEditedJson(e.target.value)}
            rows={5}
            style={{
              background: "#111",
              color: "#eee",
              border: "1px solid #333",
              borderRadius: 4,
              padding: 6,
              fontFamily: "monospace",
              fontSize: 11,
            }}
          />
          {jsonError && (
            <div
              data-testid="payload-warning-clarifier-error"
              style={{ color: "#f88", fontSize: 11 }}
            >
              {jsonError}
            </div>
          )}
          <div style={{ display: "flex", gap: 6 }}>
            <button
              data-testid="payload-warning-clarifier-submit"
              onClick={handleClarifierSubmit}
              disabled={sent !== null}
              style={btnStyle("#eab308", sent !== null)}
            >
              Submit revised args
            </button>
            <button
              data-testid="payload-warning-clarifier-cancel"
              onClick={() => {
                setShowClarifier(false);
                setJsonError(null);
              }}
              disabled={sent !== null}
              style={btnStyle("#6b7280", sent !== null)}
            >
              Back
            </button>
          </div>
        </div>
      )}

      {!showClarifier && (
        <div
          data-testid="payload-warning-actions"
          style={{ display: "flex", gap: 6, flexWrap: "wrap" }}
        >
          {warning.options.map((opt) => {
            const handler =
              opt === "proceed"
                ? handleProceed
                : opt === "cancel"
                  ? handleCancel
                  : handleNarrow;
            return (
              <button
                key={opt}
                data-testid={`payload-warning-button-${opt}`}
                onClick={handler}
                disabled={sent !== null}
                style={btnStyle(OPTION_COLOR[opt], sent !== null)}
              >
                {OPTION_LABEL[opt]}
              </button>
            );
          })}
        </div>
      )}

      {sent !== null && (
        <div
          data-testid="payload-warning-sent"
          style={{ color: "#888", fontSize: 11 }}
        >
          Sent: <strong>{sent}</strong>
        </div>
      )}
    </div>
  );
}

function btnStyle(color: string, disabled: boolean): React.CSSProperties {
  return {
    background: disabled ? "#222" : color,
    color: disabled ? "#666" : "#000",
    border: "none",
    borderRadius: 4,
    padding: "5px 10px",
    fontSize: 11,
    fontWeight: 600,
    cursor: disabled ? "default" : "pointer",
  };
}
