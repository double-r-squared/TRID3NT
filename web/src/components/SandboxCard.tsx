// GRACE-2 web — SandboxCard (sprint-13, job-0234).
//
// Chat-inline card for the Python sandbox code-exec lifecycle, wired to the
// ``code-exec-request`` / ``code-exec-result`` envelopes from job-0233.
//
// STATES
// ------
// REQUEST  — show the exact python_code the agent wants to execute (monospace,
//            scrollable, syntax-dimmed), the rationale caption, and the gate
//            buttons: Proceed (primary) + Cancel (muted, rightmost). This is the
//            hard confirm gate (Invariant 9 — running arbitrary code is a
//            consequential action). Cancel rightmost per the
//            ``feedback_payload_warning_ux_redesign`` button-order memory.
//            Decision rides back on the EXISTING ``tool-payload-confirmation``
//            envelope with the code_exec_id as warning_id (NO new reply type).
//
// RUNNING  — same ephemeral treatment as other in-flight pipeline cards:
//            rainbow-gradient spinner. No buttons.
//
// RESULT   — status chip (ok=green / error=red / timeout=amber / blocked=red),
//            stdout tail (collapsible, hidden by default), result descriptor
//            rendered inline (scalar → plain text; dict/json → pretty JSON
//            capped at 40 lines; chart → note: handled by the chart-emission
//            envelope separately; too_large → marker), truncated=true marker,
//            Save button (downloads the result JSON).
//
// CONFIRM-WIRING
// --------------
// Proceed/Cancel call ``onDecide`` which the parent (Chat.tsx) wires to
// GraceWs.sendPayloadConfirmation(code_exec_id, decision) — same method the
// PayloadWarningInline uses. No new reply type invented.
//
// Invariant 1 (Determinism): displayed numbers come from the result descriptor
// the sandbox computed; the SandboxCard never fabricates them.
// Invariant 9 (No cost theater): no dollar/quota field. duration_s is latency.
//
// This component is a pure presentation surface — all state and side effects
// live in the parent (Chat.tsx).

import { useState } from "react";
import { IconSandbox, IconWarning, IconChevronRight, IconArrowRight } from "./icons";

// ---------------------------------------------------------------------------
// Wire shapes (mirrors sandbox_contracts.py — hand-mirrored, no codegen).
// ---------------------------------------------------------------------------

export type CodeExecStatus = "ok" | "error" | "timeout" | "blocked";

/** Mirrors CodeExecRequestPayload from packages/contracts/.../sandbox_contracts.py */
export interface CodeExecRequestPayload {
  envelope_type: "code-exec-request";
  code_exec_id: string;
  python_code: string;
  /**
   * {var_name: layer_uri} the sandbox will pre-open. A value may be a single URI
   * string (one handle) OR an ordered list of frame URIs (an animation sequence
   * pre-opened as a list of handles) — the ADDITIVE multi-frame extension.
   */
  layer_refs: Record<string, string | string[]>;
  rationale?: string | null;
}

/** Mirrors CodeExecResultPayload from packages/contracts/.../sandbox_contracts.py */
export interface CodeExecResultPayload {
  envelope_type: "code-exec-result";
  code_exec_id: string;
  status: CodeExecStatus;
  stdout_tail: string;
  stderr_tail: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  result: Record<string, any> | null;
  truncated: boolean;
  duration_s: number;
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export type SandboxCardDecision = "proceed" | "cancel";

export interface SandboxCardProps {
  /**
   * The code-exec-request payload. The card always renders this (the code +
   * rationale + layer refs are shown in both REQUEST and RESULT states so
   * the user can cross-check what was approved).
   */
  request: CodeExecRequestPayload;
  /**
   * When present, the card switches to RESULT state showing the outcome.
   * While absent AND decided === "proceed", the card shows RUNNING state.
   */
  result?: CodeExecResultPayload;
  /**
   * The decision the user made (set when the user clicks Proceed or Cancel).
   * Null until the user decides; locks the gate buttons after click.
   */
  decided: SandboxCardDecision | null;
  /**
   * Called when the user clicks Proceed or Cancel. The parent wires this to
   * GraceWs.sendPayloadConfirmation(code_exec_id, decision).
   */
  onDecide: (decision: SandboxCardDecision) => void;
}

// ---------------------------------------------------------------------------
// Status chip colors / labels
// ---------------------------------------------------------------------------

const STATUS_BG: Record<CodeExecStatus, string> = {
  ok:      "rgba(16,185,129,0.18)",
  error:   "rgba(239,68,68,0.18)",
  timeout: "rgba(234,179,8,0.18)",
  blocked: "rgba(239,68,68,0.18)",
};
const STATUS_COLOR: Record<CodeExecStatus, string> = {
  ok:      "#10b981",
  error:   "#ef4444",
  timeout: "#eab308",
  blocked: "#ef4444",
};
const STATUS_LABEL: Record<CodeExecStatus, string> = {
  ok:      "ok",
  error:   "error",
  timeout: "timeout",
  blocked: "blocked",
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Render the result descriptor into a displayable string, capped at lines. */
function renderResultDescriptor(
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  result: Record<string, any>,
  maxLines = 40,
): { text: string; capped: boolean } {
  const kind = result["kind"];
  // Chart results: the inline PNG is rendered by ResultDescriptorView (the
  // <img> path). This text helper is only reached for the JSON/Save fallback, so
  // keep an honest note here — the PNG path never relies on this string.
  if (kind === "chart") {
    return { text: "(figure rendered above)", capped: false };
  }
  // too_large → honest marker
  if (kind === "too_large") {
    const originalBytes: number | undefined = result["original_bytes"] as number | undefined;
    const note = originalBytes
      ? `Result too large to display (${(originalBytes / 1024).toFixed(0)} KiB)`
      : "Result too large to display";
    return { text: note, capped: false };
  }
  // Scalar / json / dataframe: pretty-print the value field (or whole dict)
  let raw: string;
  try {
    const value = "value" in result ? result["value"] : result;
    raw = JSON.stringify(value, null, 2);
  } catch {
    raw = String(result);
  }
  const lines = raw.split("\n");
  if (lines.length > maxLines) {
    return {
      text: lines.slice(0, maxLines).join("\n") + "\n… (truncated)",
      capped: true,
    };
  }
  return { text: raw, capped: false };
}

/** Download a JSON blob as a file. */
function downloadJson(data: unknown, filename: string): void {
  try {
    const blob = new Blob([JSON.stringify(data, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  } catch {
    // ignore
  }
}

// ---------------------------------------------------------------------------
// Style constants
// ---------------------------------------------------------------------------

const MONO_FONT =
  'ui-monospace, SFMono-Regular, Menlo, Consolas, "Courier New", monospace';

const CARD_STYLE: React.CSSProperties = {
  background: "rgba(16,18,24,0.96)",
  border: "1px solid rgba(255,255,255,0.07)",
  borderLeft: "3px solid #6366f1", // indigo accent — distinct from warning/danger
  borderRadius: 8,
  boxShadow: "0 4px 14px rgba(0,0,0,0.35)",
  color: "#e5e7eb",
  padding: "10px 12px",
  display: "flex",
  flexDirection: "column",
  gap: 8,
  fontSize: 12,
  lineHeight: 1.45,
  fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, sans-serif",
  width: "100%",
  boxSizing: "border-box",
};

const HEADER_STYLE: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
};

const TITLE_STYLE: React.CSSProperties = {
  fontSize: 13,
  fontWeight: 600,
  color: "#f3f4f6",
  flex: 1,
};

const CODE_BLOCK_STYLE: React.CSSProperties = {
  background: "rgba(0,0,0,0.45)",
  border: "1px solid #2a2d35",
  borderRadius: 6,
  padding: "8px 10px",
  fontFamily: MONO_FONT,
  fontSize: 11,
  color: "#c8d0e0",
  overflowX: "auto",
  overflowY: "auto",
  maxHeight: 200,
  whiteSpace: "pre",
  lineHeight: 1.5,
  // syntax-dim: soften pure white so code doesn't glare
  opacity: 0.9,
};

const ACTION_ROW_STYLE: React.CSSProperties = {
  display: "flex",
  gap: 6,
  flexWrap: "wrap",
  marginTop: 2,
};

function btnStyle(
  tone: "primary" | "secondary" | "muted",
  disabled: boolean,
): React.CSSProperties {
  const base: React.CSSProperties = {
    border: "1px solid transparent",
    borderRadius: 6,
    padding: "5px 10px",
    fontSize: 12,
    fontWeight: 600,
    cursor: disabled ? "default" : "pointer",
    fontFamily: "inherit",
    lineHeight: 1.2,
    transition: "background 0.12s ease, border-color 0.12s ease",
  };
  if (disabled) {
    return { ...base, background: "rgba(255,255,255,0.04)", color: "#555", borderColor: "#333" };
  }
  if (tone === "primary") {
    return { ...base, background: "#6366f1", color: "#f9fafb", borderColor: "#6366f1" };
  }
  if (tone === "secondary") {
    return { ...base, background: "rgba(255,255,255,0.05)", color: "#e5e7eb", borderColor: "#3f3f46" };
  }
  // muted
  return { ...base, background: "transparent", color: "#9ca3af", borderColor: "transparent", fontWeight: 500 };
}

// ---------------------------------------------------------------------------
// RUNNING sub-component (ephemeral treatment)
// ---------------------------------------------------------------------------

function RunningIndicator(): JSX.Element {
  return (
    <div
      data-testid="sandbox-card-running"
      style={{ display: "flex", alignItems: "center", gap: 8 }}
    >
      <span
        aria-hidden="true"
        style={{
          display: "inline-block",
          width: 12,
          height: 12,
          borderRadius: "50%",
          border: "2px solid #6366f1",
          borderTopColor: "transparent",
          animation: "sandbox-spin 0.8s linear infinite",
        }}
      />
      <span style={{ color: "#a5b4fc", fontSize: 12, fontStyle: "italic" }}>
        Running Python sandbox…
      </span>
      <style>{`
        @keyframes sandbox-spin {
          to { transform: rotate(360deg); }
        }
        @media (prefers-reduced-motion: reduce) {
          .sandbox-spin { animation: none; }
        }
      `}</style>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function SandboxCard({
  request,
  result,
  decided,
  onDecide,
}: SandboxCardProps): JSX.Element {
  // Collapsible stdout / stderr (default collapsed to keep card tidy)
  const [stdoutOpen, setStdoutOpen] = useState(false);
  const [stderrOpen, setStderrOpen] = useState(false);

  // Determine current state
  const isRunning = decided === "proceed" && result === undefined;
  const isCancelled = decided === "cancel";
  const hasResult = result !== undefined;

  // Gate buttons (REQUEST state only)
  function handleProceed(): void {
    onDecide("proceed");
  }
  function handleCancel(): void {
    onDecide("cancel");
  }

  const hasLayerRefs =
    request.layer_refs && Object.keys(request.layer_refs).length > 0;

  return (
    <div
      data-testid="sandbox-card"
      data-code-exec-id={request.code_exec_id}
      style={CARD_STYLE}
      role="region"
      aria-label="Python sandbox code execution"
    >
      {/* Header */}
      <div style={HEADER_STYLE}>
        <span aria-hidden="true" style={{ color: "#6366f1", lineHeight: 1.2, flexShrink: 0, display: "inline-flex" }}>
          <IconSandbox size={14} weight="bold" />
        </span>
        <strong
          data-testid="sandbox-card-title"
          style={TITLE_STYLE}
        >
          {hasResult
            ? "Python sandbox result"
            : isCancelled
              ? "Python sandbox cancelled"
              : isRunning
                ? "Running Python sandbox"
                : "Python sandbox — confirm execution"}
        </strong>
        {/* Status chip for result state */}
        {hasResult && result && (
          <span
            data-testid="sandbox-card-status-chip"
            data-status={result.status}
            style={{
              background: STATUS_BG[result.status],
              color: STATUS_COLOR[result.status],
              border: `1px solid ${STATUS_COLOR[result.status]}`,
              borderRadius: 12,
              padding: "1px 8px",
              fontSize: 11,
              fontWeight: 600,
              lineHeight: 1.5,
              flexShrink: 0,
            }}
          >
            {STATUS_LABEL[result.status]}
          </span>
        )}
      </div>

      {/* Rationale caption (shown when present in any state) */}
      {request.rationale && (
        <div
          data-testid="sandbox-card-rationale"
          style={{ color: "#9ca3af", fontSize: 11, lineHeight: 1.4 }}
        >
          {request.rationale}
        </div>
      )}

      {/* Code block (always visible — user confirmed what they're approving) */}
      <div>
        <div
          style={{
            fontSize: 10,
            color: "#6b7280",
            marginBottom: 4,
            fontFamily: "inherit",
            textTransform: "uppercase",
            letterSpacing: "0.05em",
          }}
        >
          Python code
        </div>
        <pre
          data-testid="sandbox-card-code"
          style={CODE_BLOCK_STYLE}
        >
          {request.python_code}
        </pre>
      </div>

      {/* Layer refs (if any) */}
      {hasLayerRefs && (
        <div
          data-testid="sandbox-card-layer-refs"
          style={{ display: "flex", flexDirection: "column", gap: 2 }}
        >
          <div style={{ fontSize: 10, color: "#6b7280", textTransform: "uppercase", letterSpacing: "0.05em" }}>
            Layers
          </div>
          {Object.entries(request.layer_refs).map(([varName, ref]) => {
            // A ref is either a single URI string or an ordered list of frame
            // URIs (multi-frame extension). Show the count + first URI for a list.
            const isList = Array.isArray(ref);
            const display = isList
              ? `${(ref as string[]).length} frames${(ref as string[])[0] ? ` — ${(ref as string[])[0]}` : ""}`
              : (ref as string);
            return (
              <div key={varName} style={{ display: "flex", gap: 6, fontSize: 11 }}>
                <span style={{ fontFamily: MONO_FONT, color: "#93c5fd" }}>{varName}</span>
                <span style={{ color: "#4b5563", display: "inline-flex", alignItems: "center" }}>
                  <IconArrowRight size={11} />
                </span>
                <span style={{ color: "#6b7280", wordBreak: "break-all" }}>{display}</span>
              </div>
            );
          })}
        </div>
      )}

      {/* Running indicator */}
      {isRunning && <RunningIndicator />}

      {/* Cancelled note */}
      {isCancelled && !hasResult && (
        <div
          data-testid="sandbox-card-cancelled-note"
          style={{ color: "#6b7280", fontSize: 11, fontStyle: "italic" }}
        >
          Execution cancelled by user.
        </div>
      )}

      {/* RESULT content */}
      {hasResult && result && (
        <div
          data-testid="sandbox-card-result-section"
          style={{ display: "flex", flexDirection: "column", gap: 6 }}
        >
          {/* Duration */}
          {result.duration_s > 0 && (
            <div
              data-testid="sandbox-card-duration"
              style={{ color: "#6b7280", fontSize: 11 }}
            >
              Duration: {result.duration_s.toFixed(2)}s
            </div>
          )}

          {/* Truncated marker */}
          {result.truncated && (
            <div
              data-testid="sandbox-card-truncated"
              style={{
                background: "rgba(234,179,8,0.12)",
                border: "1px solid rgba(234,179,8,0.3)",
                borderRadius: 4,
                padding: "3px 8px",
                fontSize: 11,
                color: "#eab308",
                display: "flex",
                alignItems: "center",
                gap: 5,
              }}
            >
              <IconWarning size={12} />
              Output was truncated — some data may be missing.
            </div>
          )}

          {/* Result descriptor */}
          {result.result !== null && (
            <div
              data-testid="sandbox-card-result-descriptor"
              style={{ display: "flex", flexDirection: "column", gap: 4 }}
            >
              <div style={{ fontSize: 10, color: "#6b7280", textTransform: "uppercase", letterSpacing: "0.05em" }}>
                Result
              </div>
              <ResultDescriptorView descriptor={result.result} />
            </div>
          )}

          {/* Stdout (collapsible) */}
          {result.stdout_tail && (
            <CollapsibleSection
              label="stdout"
              content={result.stdout_tail}
              open={stdoutOpen}
              onToggle={() => setStdoutOpen((v) => !v)}
              testId="sandbox-card-stdout"
            />
          )}

          {/* Stderr (collapsible) */}
          {result.stderr_tail && (
            <CollapsibleSection
              label="stderr"
              content={result.stderr_tail}
              open={stderrOpen}
              onToggle={() => setStderrOpen((v) => !v)}
              testId="sandbox-card-stderr"
            />
          )}

          {/* Save button */}
          <div style={{ marginTop: 2 }}>
            <button
              type="button"
              data-testid="sandbox-card-save-button"
              onClick={() =>
                downloadJson(
                  { request, result },
                  `code-exec-${request.code_exec_id}.json`,
                )
              }
              style={btnStyle("secondary", false)}
            >
              Save result JSON
            </button>
          </div>
        </div>
      )}

      {/* Gate buttons (REQUEST state only — not yet decided) */}
      {decided === null && !hasResult && (
        <div
          data-testid="sandbox-card-actions"
          style={ACTION_ROW_STYLE}
        >
          {/* Proceed is primary; Cancel is muted and rightmost per memory */}
          <button
            type="button"
            data-testid="sandbox-card-proceed"
            onClick={handleProceed}
            style={btnStyle("primary", false)}
          >
            Proceed
          </button>
          <button
            type="button"
            data-testid="sandbox-card-cancel"
            onClick={handleCancel}
            style={btnStyle("muted", false)}
          >
            Cancel
          </button>
        </div>
      )}

      {/* Post-decision footer (gate sent) */}
      {decided !== null && !hasResult && (
        <div
          data-testid="sandbox-card-decision-footer"
          style={{ color: "#6b7280", fontSize: 11 }}
        >
          Decision sent: <strong style={{ color: decided === "proceed" ? "#a5b4fc" : "#9ca3af" }}>{decided}</strong>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Result descriptor sub-component
// ---------------------------------------------------------------------------

function ResultDescriptorView({
  descriptor,
}: {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  descriptor: Record<string, any>;
}): JSX.Element {
  const kind = descriptor["kind"];

  if (kind === "chart") {
    // The sandbox executor renders a matplotlib Figure to PNG and carries it
    // inline as ``png_base64`` (capped at the executor's MAX_FIGURE_PNG_BYTES;
    // ``png_truncated`` is true when the PNG was dropped for being over-cap).
    // Render the figure directly in the card (capped to card width) when the
    // PNG is present; fall back to the honest "dropped" note only when it isn't.
    const pngBase64: string | undefined = descriptor["png_base64"] as
      | string
      | undefined;
    const pngTruncated = Boolean(descriptor["png_truncated"]);
    const title: string | undefined = descriptor["title"] as string | undefined;
    if (pngBase64 && !pngTruncated) {
      return (
        <div
          data-testid="sandbox-result-chart-image"
          style={{ display: "flex", flexDirection: "column", gap: 4 }}
        >
          <img
            src={`data:image/png;base64,${pngBase64}`}
            alt={title || "Sandbox figure"}
            style={{
              maxWidth: "100%",
              height: "auto",
              borderRadius: 6,
              border: "1px solid #1d2233",
              background: "#fff",
              display: "block",
            }}
          />
          {title && (
            <div style={{ color: "#9ca3af", fontSize: 11, textAlign: "center" }}>
              {title}
            </div>
          )}
        </div>
      );
    }
    // PNG absent (too large to inline, or a render error) — honest note.
    return (
      <div
        data-testid="sandbox-result-chart-note"
        style={{ color: "#a5b4fc", fontSize: 11, fontStyle: "italic" }}
      >
        {pngTruncated
          ? "Figure too large to display inline (it exceeded the size cap)."
          : "Figure was produced but could not be rendered."}
      </div>
    );
  }

  if (kind === "too_large") {
    const originalBytes: number | undefined = descriptor["original_bytes"] as number | undefined;
    return (
      <div
        data-testid="sandbox-result-too-large"
        style={{ color: "#f87171", fontSize: 11 }}
      >
        {originalBytes
          ? `Result too large to display (${(originalBytes / 1024).toFixed(0)} KiB)`
          : "Result too large to display"}
      </div>
    );
  }

  // Scalar: just show the value inline (plain text, not code block)
  if (kind === "json") {
    const value = descriptor["value"];
    const isScalar =
      typeof value === "number" ||
      typeof value === "boolean" ||
      typeof value === "string";
    if (isScalar) {
      return (
        <span
          data-testid="sandbox-result-scalar"
          style={{ color: "#d1fae5", fontSize: 13, fontWeight: 600 }}
        >
          {String(value)}
        </span>
      );
    }
  }

  // Default: pretty JSON in a code block (capped)
  const { text, capped } = renderResultDescriptor(descriptor);
  return (
    <div>
      <pre
        data-testid="sandbox-result-json"
        style={{
          ...CODE_BLOCK_STYLE,
          maxHeight: 160,
          background: "rgba(0,0,0,0.3)",
          borderColor: "#1d2233",
        }}
      >
        {text}
      </pre>
      {capped && (
        <div style={{ color: "#6b7280", fontSize: 10, marginTop: 2 }}>
          (output capped at 40 lines)
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Collapsible section (stdout / stderr)
// ---------------------------------------------------------------------------

interface CollapsibleSectionProps {
  label: string;
  content: string;
  open: boolean;
  onToggle: () => void;
  testId: string;
}

function CollapsibleSection({
  label,
  content,
  open,
  onToggle,
  testId,
}: CollapsibleSectionProps): JSX.Element {
  return (
    <div data-testid={testId} style={{ display: "flex", flexDirection: "column", gap: 3 }}>
      <button
        type="button"
        data-testid={`${testId}-toggle`}
        onClick={onToggle}
        style={{
          background: "none",
          border: "none",
          cursor: "pointer",
          color: "#9ca3af",
          fontSize: 11,
          display: "flex",
          alignItems: "center",
          gap: 4,
          padding: 0,
          fontFamily: "inherit",
          textAlign: "left",
        }}
        aria-expanded={open}
      >
        <span aria-hidden="true" style={{ display: "inline-flex", transition: "transform 0.15s", transform: open ? "rotate(90deg)" : "none" }}>
          <IconChevronRight size={12} />
        </span>
        {label}
        <span style={{ color: "#4b5563" }}>({content.length} chars)</span>
      </button>
      {open && (
        <pre
          data-testid={`${testId}-content`}
          style={{
            ...CODE_BLOCK_STYLE,
            maxHeight: 140,
            background: "rgba(0,0,0,0.3)",
            borderColor: "#1d2233",
            color: label === "stderr" ? "#fca5a5" : "#c8d0e0",
          }}
        >
          {content}
        </pre>
      )}
    </div>
  );
}
