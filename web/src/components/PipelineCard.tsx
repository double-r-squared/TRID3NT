// GRACE-2 web — PipelineCard (FR-WC-8; Invariant 8).
//
// One-line card rendered inline in the chat stream for each pipeline step.
// Format: "<operation> <pct>%" while running/pending, "<operation> ✓" on
// completion, "<operation> ✗" on failure, "<operation> ⊘" on cancel.
//
// Subtle visual: left border in the state color + a subtle progress-fill
// background gradient so the card reads as a meter without dominating the
// chat text. Small font (11px), muted palette — information density without
// visual weight.
//
// This component receives a plain PipelineStepSummary prop (no subscription
// logic here). The caller (Chat.tsx) owns the replace-not-reconcile
// semantics and passes the current snapshot of each step.
//
// STATE COLORS (FR-WC-8):
//   pending   → #6b7280 (gray)
//   running   → #3b82f6 (blue)  — left border pulses
//   complete  → #10b981 (green)
//   failed    → #ef4444 (red)
//   cancelled → #eab308 (yellow)  — Invariant 8: distinct from failed

import { PipelineStepSummary, PipelineStepState } from "../contracts";

// --- State colors -------------------------------------------------------- //

const STATE_COLOR: Record<PipelineStepState, string> = {
  pending: "#6b7280",
  running: "#3b82f6",
  complete: "#10b981",
  failed: "#ef4444",
  cancelled: "#eab308",
};

const STATE_SUFFIX: Record<PipelineStepState, string> = {
  pending: "",
  running: "",
  complete: "✓",
  failed: "✗",
  cancelled: "⊘",
};

// --- Component ----------------------------------------------------------- //

export interface PipelineCardProps {
  step: PipelineStepSummary;
}

export function PipelineCard({ step }: PipelineCardProps): JSX.Element {
  const color = STATE_COLOR[step.state];
  const suffix = STATE_SUFFIX[step.state];

  // For running/pending steps with a progress_percent, render: "name 47%"
  // For terminal steps: "name ✓" / "name ✗" / "name ⊘"
  // For pending with no progress: "name …"
  const isTerminal =
    step.state === "complete" ||
    step.state === "failed" ||
    step.state === "cancelled";

  let rightText: string;
  if (isTerminal) {
    rightText = suffix;
  } else if (typeof step.progress_percent === "number") {
    rightText = `${step.progress_percent}%`;
  } else if (step.state === "running") {
    rightText = "…";
  } else {
    rightText = "pending";
  }

  // Progress fill: only on running steps with a known percent. Fills the
  // background using a gradient so the card itself acts as a progress bar.
  const progressFill =
    step.state === "running" && typeof step.progress_percent === "number"
      ? `linear-gradient(90deg, rgba(59,130,246,0.12) ${step.progress_percent}%, transparent ${step.progress_percent}%)`
      : undefined;

  return (
    <div
      data-testid="pipeline-card"
      data-step-id={step.step_id}
      data-state={step.state}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 6,
        fontSize: 11,
        lineHeight: "1.4",
        padding: "3px 8px 3px 6px",
        borderLeft: `3px solid ${color}`,
        borderRadius: "0 3px 3px 0",
        background: progressFill ?? "rgba(255,255,255,0.03)",
        color: "#bbb",
        fontFamily: "ui-monospace, 'Cascadia Code', 'Fira Code', monospace",
        position: "relative",
        overflow: "hidden",
      }}
    >
      <span
        data-testid="pipeline-card-name"
        style={{
          flex: 1,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
          color: isTerminal ? (step.state === "complete" ? "#6ee7b7" : "#bbb") : "#bbb",
        }}
        title={step.name}
      >
        {step.name}
      </span>
      <span
        data-testid="pipeline-card-status"
        style={{
          color: color,
          fontWeight: isTerminal ? 600 : 400,
          flexShrink: 0,
        }}
      >
        {rightText}
      </span>
      {step.state === "failed" && (step.error_code || step.error_message) && (
        <span
          data-testid="pipeline-card-error"
          style={{ color: "#fca5a5", fontSize: 10, marginLeft: 4 }}
          title={step.error_message ?? undefined}
        >
          {step.error_code ?? "error"}
        </span>
      )}
    </div>
  );
}
