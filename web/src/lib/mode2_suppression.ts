// GRACE-2 web — Mode 2 client-side types + domain suppression (job-0126,
// sprint-12-mega Wave 2).
//
// This module holds:
//   1. The TS mirrors of the Wave 1 ``Mode2Candidate`` /
//      ``Mode2CandidateEnvelope`` shapes (services/agent/src/grace2_agent/
//      mode2_classifier.py) — defined locally rather than in contracts.ts
//      because the canonical pydantic envelope is NOT yet registered in
//      packages/contracts (kickoff §1: "define in Wave 1.5 ws.py registry if
//      not present — surface as OQ if missing"; tracked as
//      OQ-0126-MODE2-ADD-CONFIRMED-SCHEMA).
//   2. The localStorage-backed "Don't ask again for this domain" suppression
//      list that backs Mode2OfferModal's tertiary action.
//
// Why colocate (rather than expand contracts.ts): contracts.ts is the mirror
// of Appendix A and is FROZEN for this job (file ownership). When schema
// promotes ``mode2-candidate`` and ``mode2-add-confirmed`` to canonical
// pydantic models, a follow-up job moves these types into contracts.ts and
// re-exports them here for backwards-compat with callers.

// --- Types: Mode 2 candidate ------------------------------------------- //

/** TLD bucket the Wave 1 classifier reports. */
export type Mode2TLD = "gov" | "edu" | "mil" | "int" | "other";

/**
 * Hint the Wave 1 classifier emits for the suggested catalog entry kind. The
 * modal renders this as a preview chip; the user can revise on the heavier
 * ``offer-catalog-addition`` flow (sprint-08).
 */
export type Mode2SuggestedKind = "fetcher" | "endpoint" | "reference";

/**
 * Wire-shape mirror of ``services/agent/src/grace2_agent/mode2_classifier.py``
 * ``Mode2Candidate``. Field-by-field mirror; if the classifier adds a field,
 * mirror it here (TS will surface unused fields at the consumption site).
 */
export interface Mode2Candidate {
  candidate_id: string;
  url: string;
  domain: string;
  domain_tld: Mode2TLD;
  confidence: number;
  detected_patterns: string[];
  title: string | null;
  suggested_tool_kind: Mode2SuggestedKind;
  snippet: string | null;
}

/**
 * Wire-shape mirror of ``Mode2CandidateEnvelope.to_wire_dict()``. The payload
 * the server emits inside the ``mode2-candidate`` envelope.
 */
export interface Mode2CandidatePayload {
  envelope_type?: "mode2-candidate";
  candidate: Mode2Candidate;
}

/**
 * Wire shape for the ``mode2-add-confirmed`` envelope this client emits when
 * the user clicks "Add to Mode 2 catalog". The agent receiver shape is
 * UNDEFINED in Wave 1.5 (OQ-0126-MODE2-ADD-CONFIRMED-SCHEMA); we emit the
 * candidate-id + domain + URL + suggested_tool_kind so the server can
 * correlate to the originating audit-log entry and hand off to the heavier
 * ``offer-catalog-addition`` flow.
 */
export interface Mode2AddConfirmedPayload {
  envelope_type?: "mode2-add-confirmed";
  candidate_id: string;
  url: string;
  domain: string;
  suggested_tool_kind: Mode2SuggestedKind;
}

/**
 * Client-side audit-event envelope payload. One emitted per modal display
 * ("display-modal" / "display-toast") and per user action ("add" / "dismiss"
 * / "suppress"). Server-side persistence is OQ-0126-AUDIT-PERSISTENCE —
 * the agent need not consume it for v0.1; ``server.py``'s default-branch
 * console.debug suffices until schema promotes it.
 */
export type Mode2AuditAction =
  | "display-modal"
  | "display-toast"
  | "add"
  | "dismiss"
  | "suppress";

export interface Mode2AuditEventPayload {
  envelope_type?: "mode2-audit-event";
  candidate_id: string;
  domain: string;
  action: Mode2AuditAction;
  confidence: number;
  surface: "modal" | "toast";
}

// --- Suppression list (localStorage) ------------------------------------ //
//
// Backs the "Don't ask again for this domain" affordance on Mode2OfferModal.
// Single source of truth for the persistence shape so the modal, the toast,
// and any future settings UI read the same set.
//
// Storage shape: a JSON-serialized array of lowercase host strings under
// ``grace2.mode2_suppressed_domains``. Lowercase normalization happens on
// add and on read so callers can pass mixed-case hosts without surprises.
//
// Why a list and not a per-domain timestamp: the kickoff is "Don't ask again
// for this domain" — a permanent opt-out the user reverses by clearing site
// data or returning through a settings surface. The Wave 1 classifier
// already maintains its own audit log; we don't need parallel time-based
// replay here.
//
// localStorage may be disabled (privacy mode); every read/write is wrapped
// in a try/catch so the modal degrades to "always ask" rather than crashing.

const STORAGE_KEY = "grace2.mode2_suppressed_domains";

function readSuppressionList(): string[] {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter((d): d is string => typeof d === "string")
      .map((d) => d.toLowerCase());
  } catch {
    return [];
  }
}

function writeSuppressionList(domains: string[]): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(domains));
  } catch {
    // localStorage unavailable; silently degrade — the next isSuppressed call
    // will return false (surfacing the modal again is strictly less harmful
    // than throwing).
  }
}

/** Return true if ``domain`` (case-insensitive) is on the suppression list. */
export function isSuppressed(domain: string): boolean {
  const host = domain.toLowerCase();
  return readSuppressionList().includes(host);
}

/** Add ``domain`` to the suppression list. Idempotent — duplicate calls are a no-op. */
export function suppressDomain(domain: string): void {
  const host = domain.toLowerCase();
  const current = readSuppressionList();
  if (current.includes(host)) return;
  writeSuppressionList([...current, host]);
}

/** Remove ``domain`` from the suppression list (settings/reset hook). */
export function unsuppressDomain(domain: string): void {
  const host = domain.toLowerCase();
  const next = readSuppressionList().filter((d) => d !== host);
  writeSuppressionList(next);
}

/** Return the current list of suppressed domains (lowercase, copied). */
export function listSuppressed(): string[] {
  return [...readSuppressionList()];
}

/** Clear all suppressions. Test-only / future settings surface. */
export function clearSuppressions(): void {
  writeSuppressionList([]);
}

/** Exposed for tests so they can isolate the storage key in setup/teardown. */
export const MODE2_SUPPRESSION_STORAGE_KEY = STORAGE_KEY;
