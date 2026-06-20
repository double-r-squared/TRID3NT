// GRACE-2 web - ResolutionPickerCard (#154 pre-run granularity gate, sprint-16).
//
// The IN-CHAT confirmation card the user sees when the agent pauses a heavy
// solver run (SWMM / SFINCS) to confirm the mesh GRANULARITY before the burn.
// It is an OPTIONAL enrichment on a `tool-payload-warning` envelope: when the
// envelope carries a `granularity` (GranularitySuggestion), Chat.tsx renders
// THIS card instead of the generic PayloadWarningInline. When `granularity` is
// absent the existing card renders unchanged (full back-compat).
//
// LAYOUT (built on the InlineChatCard primitive - same chrome as the
// payload-warning / source-suggestion cards):
//   - Title: "Confirm mesh resolution"
//   - Metadata row: suggested resolution (m), estimated active cells, estimated
//     solve time, vCPUs, compute class, Spot label (omitted when null).
//   - Caption: the agent's `reason`, prefixed "Coarsened -" when the suggestion
//     coarsened the user's request (Invariant 1 - honest about the adjustment).
//   - OVERRIDE control: a CHOICE-CHIP row over `resolution_choices` (NOT a
//     slider - discrete published rungs). Picking a rung LIVE-recomputes the
//     displayed cells + ETA client-side from the chosen rung, by area-invariant
//     scaling off the suggested rung's authoritative numbers:
//         cells ~= round(estimated_active_cells * (suggested/chosen)^2)
//         eta   ~= estimated_solve_seconds * (cells_chosen / cells_suggested)
//     (finer rung -> more cells -> longer; coarser -> fewer -> shorter). These
//     are labelled ESTIMATES - the authoritative numbers come from the agent's
//     suggestion; the client never claims its recompute is exact.
//   - Actions: Confirm (primary) + Cancel (muted, RIGHTMOST per the project
//     button-order convention).
//       * chosen rung == suggested  -> decision "proceed",       revised null
//       * chosen rung != suggested  -> decision "narrow_scope",  revised
//                                       { [resolution_param]: chosen }
//       * Cancel                    -> decision "cancel",        revised null
//
// LOCK + FOLD: once the user decides, the card locks (no re-answer) and folds to
// a compact one-line summary - same active->resolved pattern as SpatialInputCard
// / SandboxCard / PayloadWarningInline. `resolved` (externally recorded in the
// per-Case stream) seeds the decided state so the fold survives a remount
// (Case switch + return).
//
// CONFIRM WIRING: the decision rides back on the EXISTING
// `tool-payload-confirmation` envelope via the SAME onDecide signature the
// PayloadWarningInline uses - Chat.tsx wires it to handlePayloadDecide ->
// GraceWs.sendPayloadConfirmation(warning_id, decision, revised). No new WS
// type, no new StreamState field, no new route helper.
//
// Invariant 9 (no cost theater): cells / seconds / vCPUs / Spot label are
// capacity + capability descriptors, NOT dollar figures. No dollar field.
//
// No raw glyphs / emoji - every icon comes from the shared icons module.

import { useState } from "react";
import {
  GranularitySuggestion,
  PayloadConfirmationDecision,
  PayloadWarningEnvelopePayload,
} from "../contracts";
import { InlineChatCard, InlineChatCardAction } from "./InlineChatCard";
import {
  formatCellCount,
  formatEta,
} from "./PipelineCard";
import { IconGrid, IconCheck, IconChevronDown, IconChevronRight } from "./icons";

const ACCENT = "#eab308"; // amber - same family as the payload-warning card

// Resolved (answered) fold tint - amber, matching the payload-warning fold so
// the two confirm-gate cards read as the same lineage.
const compactStyle: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  alignItems: "stretch",
  gap: 6,
  fontSize: 12,
  lineHeight: 1.4,
  padding: "8px 10px",
  borderRadius: 6,
  background: "rgba(234,179,8,0.18)",
  boxShadow: "0 1px 3px rgba(0,0,0,0.25)",
  color: "#e5e7eb",
  fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, sans-serif",
  width: "100%",
  boxSizing: "border-box",
};

const RESOLVED_SUMMARY: Record<PayloadConfirmationDecision, string> = {
  proceed: "Mesh resolution confirmed",
  narrow_scope: "Mesh resolution overridden",
  cancel: "Mesh-resolution gate cancelled",
};

// --- Client-side area-invariant recompute -------------------------------- //
//
// Cell count scales with the inverse square of cell edge length: halving the
// resolution (finer) quadruples the cells over the same area. ETA scales with
// the cell ratio (more cells -> proportionally longer). Both are ESTIMATES off
// the agent's authoritative suggested-rung numbers.

/** Estimated active cells for `chosen` from the suggested-rung baseline. */
export function estimateCellsForResolution(
  g: GranularitySuggestion,
  chosen: number,
): number {
  if (chosen <= 0 || g.suggested_resolution_m <= 0) {
    return g.estimated_active_cells;
  }
  const ratio = g.suggested_resolution_m / chosen;
  return Math.round(g.estimated_active_cells * ratio * ratio);
}

/** Estimated solve seconds for `chosen`, scaled by the cell ratio. */
export function estimateSolveSecondsForResolution(
  g: GranularitySuggestion,
  chosen: number,
): number {
  const baseCells = g.estimated_active_cells;
  if (baseCells <= 0) return g.estimated_solve_seconds;
  const cells = estimateCellsForResolution(g, chosen);
  return g.estimated_solve_seconds * (cells / baseCells);
}

export interface ResolutionPickerCardProps {
  /** The originating tool-payload-warning envelope (carries `granularity`). */
  warning: PayloadWarningEnvelopePayload;
  /** The granularity suggestion (the caller already null-checked it). */
  granularity: GranularitySuggestion;
  /**
   * Called when the user confirms or cancels. The caller wires this into
   * GraceWs.sendPayloadConfirmation(warning.warning_id, decision, revised) via
   * the EXISTING handlePayloadDecide path. `revised` is null for proceed/cancel;
   * for narrow_scope it is { [resolution_param]: chosen }.
   */
  onDecide: (
    decision: PayloadConfirmationDecision,
    revised: Record<string, unknown> | null,
  ) => void;
  /**
   * Externally-recorded resolution (held in the per-Case stream's
   * payloadResolved map) so the card stays answered across a remount. Seeds the
   * internal `decided` state. Undefined / null = unanswered.
   */
  resolved?: PayloadConfirmationDecision | null;
}

export function ResolutionPickerCard({
  warning,
  granularity,
  onDecide,
  resolved = null,
}: ResolutionPickerCardProps): JSX.Element {
  const g = granularity;

  // The chip selection. Default to the suggested rung. If the suggested rung is
  // not among the published choices (defensive), fall back to the first choice
  // so a chip is always selected.
  const initialChoice =
    g.resolution_choices.includes(g.suggested_resolution_m)
      ? g.suggested_resolution_m
      : g.resolution_choices[0] ?? g.suggested_resolution_m;
  const [chosen, setChosen] = useState<number>(initialChoice);

  // Lock + fold once decided. Seed from the externally-recorded resolution.
  const [decided, setDecided] = useState<PayloadConfirmationDecision | null>(
    resolved,
  );
  const [expanded, setExpanded] = useState<boolean>(false);

  function decide(
    decision: PayloadConfirmationDecision,
    revised: Record<string, unknown> | null,
  ): void {
    if (decided !== null) return; // already answered - cannot re-answer
    setDecided(decision);
    onDecide(decision, revised);
  }

  function handleConfirm(): void {
    if (chosen === g.suggested_resolution_m) {
      // Unchanged from the suggestion -> proceed with the suggested rung.
      decide("proceed", null);
    } else {
      // Overridden -> narrow_scope carrying the chosen rung on the engine's
      // resolution param.
      decide("narrow_scope", { [g.resolution_param]: chosen });
    }
  }
  function handleCancel(): void {
    decide("cancel", null);
  }

  // --- Folded (resolved) compact card ------------------------------------ //
  if (decided !== null) {
    return (
      <div
        data-testid="resolution-picker-card"
        data-resolved={decided}
        data-variant="compact"
        role="status"
        aria-label={RESOLVED_SUMMARY[decided]}
        style={compactStyle}
      >
        <div
          style={{ display: "flex", alignItems: "center", gap: 8, width: "100%" }}
        >
          <span
            aria-hidden="true"
            style={{ display: "inline-flex", alignItems: "center", flexShrink: 0 }}
          >
            <IconCheck size={13} color={ACCENT} />
          </span>
          <span
            data-testid="resolution-picker-resolved"
            style={{
              flex: 1,
              minWidth: 0,
              color: ACCENT,
              fontWeight: 600,
              fontSize: 12,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
            title={RESOLVED_SUMMARY[decided]}
          >
            {RESOLVED_SUMMARY[decided]}
          </span>
          <button
            type="button"
            data-testid="resolution-picker-expand"
            aria-label={expanded ? "Collapse details" : "Show details"}
            aria-expanded={expanded}
            onClick={() => setExpanded((v) => !v)}
            style={{
              background: "transparent",
              border: "none",
              padding: 2,
              margin: 0,
              cursor: "pointer",
              display: "inline-flex",
              alignItems: "center",
              color: "#9ca3af",
              flexShrink: 0,
            }}
          >
            {expanded ? (
              <IconChevronDown size={13} color="#9ca3af" />
            ) : (
              <IconChevronRight size={13} color="#9ca3af" />
            )}
          </button>
        </div>
        {expanded && (
          <div
            data-testid="resolution-picker-detail"
            style={{
              width: "100%",
              marginTop: 6,
              paddingTop: 6,
              borderTop: "1px solid rgba(255,255,255,0.08)",
              color: "#d1d5db",
              fontSize: 11,
              lineHeight: 1.5,
            }}
          >
            <div style={{ wordBreak: "break-word" }}>
              {g.engine.toUpperCase()} mesh ·{" "}
              <strong style={{ color: "#e5e7eb" }}>{chosen} m</strong> ·{" "}
              ~{formatCellCount(estimateCellsForResolution(g, chosen))} cells (est)
            </div>
          </div>
        )}
      </div>
    );
  }

  // --- Active (pending) prompt ------------------------------------------- //

  // Live-recomputed numbers for the chosen rung.
  const chosenCells = estimateCellsForResolution(g, chosen);
  const chosenSeconds = estimateSolveSecondsForResolution(g, chosen);
  const isSuggested = chosen === g.suggested_resolution_m;

  const body = (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {/* Metadata row - authoritative suggested-rung descriptors. */}
      <div
        data-testid="resolution-picker-metadata"
        style={{
          display: "flex",
          gap: 12,
          fontSize: 11,
          color: "#9ca3af",
          flexWrap: "wrap",
        }}
      >
        <span data-testid="resolution-picker-suggested-m">
          Suggested:{" "}
          <strong style={{ color: "#e5e7eb" }}>
            {g.suggested_resolution_m} m
          </strong>
        </span>
        <span data-testid="resolution-picker-cells">
          Cells:{" "}
          <strong style={{ color: "#e5e7eb" }}>
            ~{formatCellCount(g.estimated_active_cells)}
          </strong>
        </span>
        <span data-testid="resolution-picker-eta">
          Solve:{" "}
          <strong style={{ color: "#e5e7eb" }}>
            {formatEta(g.estimated_solve_seconds)}
          </strong>
        </span>
        <span data-testid="resolution-picker-vcpus">
          vCPUs:{" "}
          <strong style={{ color: "#e5e7eb" }}>{g.vcpus}</strong>
        </span>
        <span data-testid="resolution-picker-compute-class">
          Compute:{" "}
          <strong style={{ color: "#e5e7eb" }}>{g.compute_class}</strong>
        </span>
        {g.spot_label && (
          <span data-testid="resolution-picker-spot-label">
            Spot:{" "}
            <strong style={{ color: "#e5e7eb" }}>{g.spot_label}</strong>
          </span>
        )}
      </div>

      {/* Caption - the agent's reason (coarsened-prefixed when applicable). */}
      <div
        data-testid="resolution-picker-reason"
        style={{ color: "#d1d5db", lineHeight: 1.45 }}
      >
        {g.coarsened ? `Coarsened - ${g.reason}` : g.reason}
      </div>

      {/* Override control - choice chips over the published rungs. */}
      <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
        <div
          style={{
            fontSize: 10,
            color: "#6b7280",
            textTransform: "uppercase",
            letterSpacing: "0.05em",
          }}
        >
          Resolution
        </div>
        <div
          data-testid="resolution-picker-chips"
          role="radiogroup"
          aria-label="Mesh resolution"
          style={{ display: "flex", gap: 6, flexWrap: "wrap" }}
        >
          {g.resolution_choices.map((rung) => {
            const selected = rung === chosen;
            const suggested = rung === g.suggested_resolution_m;
            return (
              <button
                key={rung}
                type="button"
                role="radio"
                aria-checked={selected}
                data-testid={`resolution-picker-chip-${rung}`}
                data-selected={selected ? "true" : "false"}
                onClick={() => setChosen(rung)}
                style={{
                  border: `1px solid ${selected ? ACCENT : "#3f3f46"}`,
                  borderRadius: 14,
                  padding: "3px 10px",
                  fontSize: 12,
                  fontWeight: selected ? 700 : 500,
                  cursor: "pointer",
                  fontFamily: "inherit",
                  lineHeight: 1.3,
                  background: selected ? "rgba(234,179,8,0.18)" : "transparent",
                  color: selected ? ACCENT : "#d1d5db",
                  fontVariantNumeric: "tabular-nums",
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 4,
                }}
              >
                {rung} m
                {suggested && (
                  <span
                    aria-hidden="true"
                    style={{ color: "#9ca3af", fontSize: 10, fontWeight: 500 }}
                  >
                    (suggested)
                  </span>
                )}
              </button>
            );
          })}
        </div>

        {/* Live recompute readout for the chosen rung (tabular-nums). */}
        <div
          data-testid="resolution-picker-readout"
          style={{
            fontSize: 11,
            color: "#9ca3af",
            fontVariantNumeric: "tabular-nums",
          }}
        >
          At <strong style={{ color: "#e5e7eb" }}>{chosen} m</strong>:{" "}
          <strong
            data-testid="resolution-picker-readout-cells"
            style={{ color: "#e5e7eb" }}
          >
            ~{formatCellCount(chosenCells)}
          </strong>{" "}
          cells ·{" "}
          <strong
            data-testid="resolution-picker-readout-eta"
            style={{ color: "#e5e7eb" }}
          >
            {formatEta(chosenSeconds)}
          </strong>{" "}
          <span style={{ color: "#6b7280" }}>
            ({isSuggested ? "suggested rung" : "estimated"})
          </span>
        </div>
      </div>
    </div>
  );

  const actions: InlineChatCardAction[] = [
    {
      label: "Confirm",
      onClick: handleConfirm,
      tone: "primary",
      testId: "resolution-picker-confirm",
    },
    {
      label: "Cancel",
      onClick: handleCancel,
      tone: "muted",
      testId: "resolution-picker-cancel",
    },
  ];

  return (
    <InlineChatCard
      variant="warning"
      title="Confirm mesh resolution"
      body={body}
      actions={actions}
      icon={<IconGrid size={14} color={ACCENT} />}
      testId="resolution-picker-card"
      ariaLabel="Confirm mesh resolution"
      extraAttrs={{
        "data-warning-id": warning.warning_id,
        "data-engine": g.engine,
      }}
    />
  );
}
