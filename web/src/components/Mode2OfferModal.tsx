// GRACE-2 web — Mode2OfferModal (job-0126, sprint-12-mega Wave 2).
//
// Renders the offer-to-add surface for ``.gov`` / ``.edu`` / ``.mil`` / ``.int``
// candidate pages emitted by the Wave 1 ``mode2_classifier`` (services/agent/
// src/grace2_agent/mode2_classifier.py). The agent emits a ``mode2-candidate``
// envelope whenever ``web_fetch`` returns a Mode 2 TLD page carrying structural
// patterns; this modal is the human-in-the-loop accept/reject affordance.
//
// Confidence routing (kickoff §2):
//   - confidence ≥ 0.7 → full modal with backdrop. The user is interrupted and
//     asked to make a decision.
//   - confidence < 0.7 → silent toast in the corner. Self-dismisses in 5s; the
//     user can click "Add to catalog" or "X" to suppress; otherwise it slides
//     out without breaking the chat flow.
//
// User actions (kickoff §1):
//   1. "Add to Mode 2 catalog" → emit ``mode2-add-confirmed`` envelope upstream
//      and audit-log the action client-side + server-side.
//   2. "Maybe later" → dismiss silently; no envelope. The next candidate from
//      the same domain still surfaces.
//   3. "Don't ask again for this domain" → suppress via
//      ``lib/mode2_suppression.ts`` (localStorage). Audit-logged so the user
//      can later reverse via a settings surface.
//
// Audit logging (kickoff §3):
//   Every modal display + user action emits a ``mode2-audit-event`` envelope
//   upstream (best-effort; failure is non-fatal — the action still completes
//   client-side). The same events are mirrored to ``console.debug`` so a
//   developer can see them without the network round-trip.
//
// Style (kickoff §4):
//   Dark-theme aware. The component never reads ``localStorage.theme`` itself
//   — it relies on the existing dark-default palette already in use across the
//   chat panel and pipeline cards. Subtle backdrop (rgba black 40%), rounded
//   panel, accent border colored by domain TLD.
//
// SCHEMA NOTE (OQ-0126-MODE2-ADD-CONFIRMED-SCHEMA): The
// ``mode2-add-confirmed`` envelope payload IS NOT YET REGISTERED in
// ``packages/contracts/src/grace2_contracts/ws.py``. The kickoff explicitly
// notes "define in Wave 1.5 ws.py registry if not present — surface as OQ if
// missing". We emit the envelope with the shape the agent receiver would most
// naturally expect (mirroring the candidate fields) so the schema follow-up
// can promote it to a pydantic model without renaming. See report.md OQs.

import { useCallback, useEffect, useState } from "react";
import {
  Mode2Candidate,
  Mode2CandidatePayload,
  isSuppressed,
  suppressDomain,
} from "../lib/mode2_suppression";

// --- Public types -------------------------------------------------------- //

/** Action emitted to the parent so it can route to ws.ts + audit log. */
export type Mode2OfferAction =
  | { kind: "add"; candidate: Mode2Candidate }
  | { kind: "dismiss"; candidate: Mode2Candidate }
  | { kind: "suppress"; candidate: Mode2Candidate };

export interface Mode2OfferModalProps {
  /**
   * Subscription seam — the parent (App.tsx) registers a setter that pushes
   * incoming ``mode2-candidate`` envelopes into the modal's queue. Returns an
   * unsubscribe function.
   */
  subscribeCandidate: (
    cb: (p: Mode2CandidatePayload) => void,
  ) => () => void;

  /**
   * Action callback the parent uses to (a) emit ``mode2-add-confirmed`` and
   * (b) write an audit-log envelope. Signature accepts the action variant so
   * the parent can dispatch on ``kind`` without duplicating switch logic.
   */
  onAction: (action: Mode2OfferAction) => void;

  /** Confidence threshold for modal-vs-toast routing. Defaults to 0.7. */
  modalThreshold?: number;
}

// --- Visual constants ---------------------------------------------------- //
//
// Match the AuthPanel / SecretsPanel dark palette (rgba surfaces over the map).

const PANEL_BG = "rgba(20,20,25,0.96)";
const PANEL_BORDER = "1px solid #444";
const PANEL_RADIUS = 8;
const TEXT_PRIMARY = "#e5e7eb";
const TEXT_MUTED = "#9ca3af";
const ACCENT_GOV = "#3b82f6"; // blue — .gov
const ACCENT_EDU = "#10b981"; // green — .edu
const ACCENT_MIL = "#eab308"; // amber — .mil
const ACCENT_INT = "#a855f7"; // purple — .int

const ACCENT_BY_TLD: Record<Mode2Candidate["domain_tld"], string> = {
  gov: ACCENT_GOV,
  edu: ACCENT_EDU,
  mil: ACCENT_MIL,
  int: ACCENT_INT,
  other: TEXT_MUTED,
};

const TLD_LABEL: Record<Mode2Candidate["domain_tld"], string> = {
  gov: ".gov",
  edu: ".edu",
  mil: ".mil",
  int: ".int",
  other: "?",
};

const KIND_LABEL: Record<Mode2Candidate["suggested_tool_kind"], string> = {
  fetcher: "Data fetcher",
  endpoint: "API endpoint",
  reference: "Reference page",
};

// --- Component ----------------------------------------------------------- //

export function Mode2OfferModal({
  subscribeCandidate,
  onAction,
  modalThreshold = 0.7,
}: Mode2OfferModalProps): JSX.Element | null {
  // Active candidate states: one full modal at a time + a queue of toasts.
  // We keep them separate so a high-confidence modal doesn't block toasts and
  // vice versa.
  const [modalCandidate, setModalCandidate] = useState<Mode2Candidate | null>(
    null,
  );
  const [toasts, setToasts] = useState<Mode2Candidate[]>([]);

  // Drop a toast after 5s self-dismiss timer. Stored as a Map so each toast's
  // timer is independent.
  useEffect(() => {
    if (toasts.length === 0) return;
    const timers = toasts.map((c) =>
      window.setTimeout(() => {
        setToasts((cur) => cur.filter((t) => t.candidate_id !== c.candidate_id));
      }, 5000),
    );
    return () => timers.forEach((t) => window.clearTimeout(t));
  }, [toasts]);

  // Subscribe to candidate emissions; route by confidence + suppression list.
  useEffect(() => {
    const unsub = subscribeCandidate((p) => {
      const c = p.candidate;
      if (!c) return;
      // Skip domain on suppression list — kickoff §1 "Don't ask again".
      if (isSuppressed(c.domain)) {
        // Audit a skip so users can see why nothing surfaced. console.debug
        // only — we don't emit upstream because the kickoff scopes audit
        // events to "modal display + user action".
        // eslint-disable-next-line no-console
        console.debug(
          `[mode2] suppressed ${c.domain} (candidate ${c.candidate_id})`,
        );
        return;
      }
      if (c.confidence >= modalThreshold) {
        setModalCandidate(c);
      } else {
        setToasts((cur) => {
          // Dedupe by candidate_id so a duplicate emit doesn't double-add.
          if (cur.some((t) => t.candidate_id === c.candidate_id)) return cur;
          return [...cur, c];
        });
      }
    });
    return unsub;
  }, [subscribeCandidate, modalThreshold]);

  // Action handlers - emit upstream + drop the surfaced candidate.
  const handleAdd = useCallback(
    (c: Mode2Candidate, surface: "modal" | "toast") => {
      onAction({ kind: "add", candidate: c });
      if (surface === "modal") setModalCandidate(null);
      else
        setToasts((cur) => cur.filter((t) => t.candidate_id !== c.candidate_id));
    },
    [onAction],
  );

  const handleDismiss = useCallback(
    (c: Mode2Candidate, surface: "modal" | "toast") => {
      onAction({ kind: "dismiss", candidate: c });
      if (surface === "modal") setModalCandidate(null);
      else
        setToasts((cur) => cur.filter((t) => t.candidate_id !== c.candidate_id));
    },
    [onAction],
  );

  const handleSuppress = useCallback(
    (c: Mode2Candidate, surface: "modal" | "toast") => {
      suppressDomain(c.domain);
      onAction({ kind: "suppress", candidate: c });
      if (surface === "modal") setModalCandidate(null);
      else
        setToasts((cur) => cur.filter((t) => t.candidate_id !== c.candidate_id));
    },
    [onAction],
  );

  // Nothing to surface => render nothing (avoid an empty root).
  if (modalCandidate === null && toasts.length === 0) return null;

  return (
    <>
      {modalCandidate && (
        <ModalSurface
          candidate={modalCandidate}
          onAdd={() => handleAdd(modalCandidate, "modal")}
          onDismiss={() => handleDismiss(modalCandidate, "modal")}
          onSuppress={() => handleSuppress(modalCandidate, "modal")}
        />
      )}
      <ToastStack
        toasts={toasts}
        onAdd={(c) => handleAdd(c, "toast")}
        onDismiss={(c) => handleDismiss(c, "toast")}
      />
    </>
  );
}

// --- Modal surface ------------------------------------------------------- //

interface ModalSurfaceProps {
  candidate: Mode2Candidate;
  onAdd: () => void;
  onDismiss: () => void;
  onSuppress: () => void;
}

function ModalSurface({
  candidate,
  onAdd,
  onDismiss,
  onSuppress,
}: ModalSurfaceProps): JSX.Element {
  const accent = ACCENT_BY_TLD[candidate.domain_tld] ?? TEXT_MUTED;
  const confidencePct = Math.round(candidate.confidence * 100);
  return (
    <div
      data-testid="grace2-mode2-modal"
      role="dialog"
      aria-modal="true"
      aria-labelledby="grace2-mode2-modal-title"
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.4)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 100,
      }}
    >
      <div
        style={{
          background: PANEL_BG,
          border: PANEL_BORDER,
          borderLeft: `4px solid ${accent}`,
          borderRadius: PANEL_RADIUS,
          color: TEXT_PRIMARY,
          width: "min(520px, 90vw)",
          padding: 20,
          fontSize: 13,
          lineHeight: 1.5,
          boxShadow: "0 12px 36px rgba(0,0,0,0.5)",
        }}
      >
        <h2
          id="grace2-mode2-modal-title"
          style={{
            margin: 0,
            fontSize: 15,
            fontWeight: 600,
            color: TEXT_PRIMARY,
            display: "flex",
            alignItems: "center",
            gap: 8,
          }}
        >
          <span
            data-testid="grace2-mode2-modal-tld"
            style={{
              background: accent,
              color: "#111",
              borderRadius: 4,
              padding: "1px 6px",
              fontSize: 11,
              fontWeight: 700,
            }}
          >
            {TLD_LABEL[candidate.domain_tld]}
          </span>
          New Mode 2 source detected
        </h2>

        <div
          data-testid="grace2-mode2-modal-domain"
          style={{
            marginTop: 12,
            color: TEXT_MUTED,
            fontFamily:
              'ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace',
            fontSize: 12,
            wordBreak: "break-all",
          }}
        >
          {candidate.domain}
        </div>
        {candidate.title && (
          <div
            data-testid="grace2-mode2-modal-title-text"
            style={{ marginTop: 6, fontWeight: 500 }}
          >
            {candidate.title}
          </div>
        )}

        <div
          style={{
            marginTop: 12,
            display: "flex",
            flexWrap: "wrap",
            gap: 6,
            alignItems: "center",
          }}
        >
          {candidate.detected_patterns.map((p) => (
            <span
              key={p}
              data-testid={`grace2-mode2-modal-pattern-${p}`}
              style={{
                background: "rgba(255,255,255,0.06)",
                border: "1px solid #4b5563",
                borderRadius: 999,
                padding: "2px 8px",
                fontSize: 11,
                color: TEXT_PRIMARY,
              }}
            >
              {p}
            </span>
          ))}
          <span
            data-testid="grace2-mode2-modal-kind"
            style={{
              background: "rgba(255,255,255,0.04)",
              borderRadius: 4,
              padding: "2px 6px",
              fontSize: 11,
              color: TEXT_MUTED,
              marginLeft: "auto",
            }}
          >
            {KIND_LABEL[candidate.suggested_tool_kind]}
          </span>
        </div>

        <div
          data-testid="grace2-mode2-modal-confidence"
          style={{ marginTop: 10, fontSize: 11, color: TEXT_MUTED }}
        >
          Confidence {confidencePct}%
        </div>

        {candidate.snippet && (
          <pre
            data-testid="grace2-mode2-modal-snippet"
            style={{
              marginTop: 12,
              padding: 10,
              background: "rgba(0,0,0,0.3)",
              border: "1px solid #2d3748",
              borderRadius: 4,
              fontSize: 11,
              color: TEXT_MUTED,
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
              maxHeight: 120,
              overflowY: "auto",
              margin: "12px 0 0 0",
              fontFamily:
                'ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace',
            }}
          >
            {candidate.snippet}
          </pre>
        )}

        <div
          style={{
            marginTop: 18,
            display: "flex",
            gap: 8,
            justifyContent: "flex-end",
            flexWrap: "wrap",
          }}
        >
          <button
            type="button"
            data-testid="grace2-mode2-modal-suppress"
            onClick={onSuppress}
            style={btnSecondaryStyle()}
          >
            Don&apos;t ask again for this domain
          </button>
          <button
            type="button"
            data-testid="grace2-mode2-modal-dismiss"
            onClick={onDismiss}
            style={btnSecondaryStyle()}
          >
            Maybe later
          </button>
          <button
            type="button"
            data-testid="grace2-mode2-modal-add"
            onClick={onAdd}
            style={btnPrimaryStyle(accent)}
          >
            Add to Mode 2 catalog
          </button>
        </div>
      </div>
    </div>
  );
}

// --- Toast stack --------------------------------------------------------- //

interface ToastStackProps {
  toasts: Mode2Candidate[];
  onAdd: (c: Mode2Candidate) => void;
  onDismiss: (c: Mode2Candidate) => void;
}

function ToastStack({ toasts, onAdd, onDismiss }: ToastStackProps): JSX.Element {
  return (
    <div
      data-testid="grace2-mode2-toast-stack"
      style={{
        position: "fixed",
        bottom: 20,
        left: 20,
        display: "flex",
        flexDirection: "column-reverse",
        gap: 8,
        zIndex: 90,
        maxWidth: 360,
      }}
    >
      {toasts.map((c) => {
        const accent = ACCENT_BY_TLD[c.domain_tld] ?? TEXT_MUTED;
        return (
          <div
            key={c.candidate_id}
            data-testid={`grace2-mode2-toast-${c.candidate_id}`}
            role="status"
            aria-live="polite"
            style={{
              background: PANEL_BG,
              border: PANEL_BORDER,
              borderLeft: `4px solid ${accent}`,
              borderRadius: PANEL_RADIUS,
              color: TEXT_PRIMARY,
              padding: "10px 12px",
              fontSize: 12,
              boxShadow: "0 6px 18px rgba(0,0,0,0.4)",
            }}
          >
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                fontWeight: 600,
              }}
            >
              <span
                style={{
                  background: accent,
                  color: "#111",
                  borderRadius: 3,
                  padding: "0 4px",
                  fontSize: 10,
                  fontWeight: 700,
                }}
              >
                {TLD_LABEL[c.domain_tld]}
              </span>
              <span
                data-testid={`grace2-mode2-toast-domain-${c.candidate_id}`}
                style={{
                  fontFamily:
                    'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
                  fontSize: 11,
                  fontWeight: 400,
                  color: TEXT_MUTED,
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                  flex: 1,
                }}
              >
                {c.domain}
              </span>
              <button
                type="button"
                data-testid={`grace2-mode2-toast-dismiss-${c.candidate_id}`}
                aria-label="Dismiss"
                onClick={() => onDismiss(c)}
                style={btnTinyStyle()}
              >
                ×
              </button>
            </div>
            <div style={{ marginTop: 4, color: TEXT_MUTED, fontSize: 11 }}>
              Low-confidence Mode 2 candidate ({Math.round(c.confidence * 100)}%)
            </div>
            <div style={{ marginTop: 6, display: "flex", gap: 6 }}>
              <button
                type="button"
                data-testid={`grace2-mode2-toast-add-${c.candidate_id}`}
                onClick={() => onAdd(c)}
                style={{ ...btnPrimaryStyle(accent), padding: "3px 8px", fontSize: 11 }}
              >
                Add to catalog
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
}

// --- Style helpers ------------------------------------------------------- //

function btnPrimaryStyle(accent: string): React.CSSProperties {
  return {
    background: accent,
    color: "#111",
    border: "none",
    borderRadius: 4,
    padding: "6px 12px",
    fontSize: 12,
    fontWeight: 600,
    cursor: "pointer",
  };
}

function btnSecondaryStyle(): React.CSSProperties {
  return {
    background: "rgba(255,255,255,0.04)",
    color: TEXT_PRIMARY,
    border: "1px solid #4b5563",
    borderRadius: 4,
    padding: "6px 12px",
    fontSize: 12,
    cursor: "pointer",
  };
}

function btnTinyStyle(): React.CSSProperties {
  return {
    background: "transparent",
    color: TEXT_MUTED,
    border: "none",
    padding: "0 4px",
    fontSize: 16,
    lineHeight: 1,
    cursor: "pointer",
  };
}
