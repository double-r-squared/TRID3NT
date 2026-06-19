// GRACE-2 web — useCases hook (job-0137, sprint-12-mega Wave 3 — FR-MP-6).
//
// Encapsulates the Case state machine (FR-MP-6 left-rail + chat-replay
// rehydration) so App.tsx stays focused on layout + WS wiring. The hook:
//
//   1. Tracks `cases` (left-rail list) — updated on every `case-list` frame.
//   2. Tracks `activeCaseId` (the open Case) — updated on `case-open`.
//   3. Tracks `activeSession` (rehydration envelope) — chat + layers + map.
//   4. Tracks `persistenceState` — drives PersistenceChip:
//        - "saved"      at rest
//        - "saving"     while a case-command is in flight
//        - "anonymous"  when no signed-in user
//   5. Exposes typed emitters: createCase / selectCase / renameCase /
//      archiveCase / deleteCase — each calls the GraceWs.sendCaseCommand
//      seam.
//
// The hook does NOT own the WS itself. App.tsx passes a stable
// `sendCaseCommand` callback (bound to `wsRef.current.sendCaseCommand`) plus
// the GraceWs event handlers (onCaseList / onCaseOpen) wired through its
// existing GraceWs instance. This keeps the hook free of WebSocket lifecycle
// and Firebase Auth — both are App.tsx's responsibility.
//
// Invariants honored:
//   - 1 (determinism boundary): the hook displays / forwards received Case
//     envelopes verbatim — no number / id / chat / layer is fabricated here.
//   - 8 (cancellation is first-class): there is no "cancel case-command"
//     surface; in-flight tool cancellation flows through the existing
//     `cancel` envelope on Chat.tsx. The hook only optimistically marks
//     `saving` until the next case-list / case-open frame.
//   - 9 (no cost theater): no cost / quota / quote field anywhere.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  CaseCommand,
  CaseListEnvelopePayload,
  CaseOpenEnvelopePayload,
  CaseSessionState,
  CaseSummary,
} from "../contracts";
/**
 * Closed enum of persistence states used by the Cases lifecycle. Previously
 * lived on `components/PersistenceChip.tsx`; that floating chip was removed
 * in job-0143 (auth controls live in Settings now). The type stays here
 * because `useCases` drives it and downstream consumers (App.tsx, future
 * status surfaces) read it as a closed-vocabulary signal.
 *
 *   - "saved"        — no in-flight case-command, signed-in user.
 *   - "saving"       — one or more case-commands awaiting server ack.
 *   - "anonymous"    — no signed-in user; persistence is best-effort.
 *   - "disconnected" — WS dropped (reserved; not currently emitted).
 */
export type PersistenceState =
  | "saved"
  | "saving"
  | "anonymous"
  | "disconnected";

/** Bound emitter for the `case-command` envelope. Matches GraceWs.sendCaseCommand. */
export type CaseCommandEmitter = (
  command: CaseCommand,
  caseId: string | null,
  args: Record<string, unknown>,
) => void;

export interface UseCasesOptions {
  /** Bound emitter the hook uses to dispatch `case-command` envelopes. */
  sendCaseCommand: CaseCommandEmitter;
  /**
   * Whether a real (non-anonymous) user is signed in. When false, the
   * persistence chip surfaces "Sign in to save" but the hook still operates
   * — the agent's anonymous fallback handles the session-id placeholder so
   * dev / unauthenticated flows still work end-to-end (sprint-12-mega Wave 2
   * persistence track default per kickoff §5).
   */
  isSignedIn: boolean;
}

export interface UseCasesReturn {
  /** Left-rail list from the most recent `case-list` envelope. */
  cases: CaseSummary[];
  /** ULID of the currently-open Case, or null when no Case is open. */
  activeCaseId: string | null;
  /** The most recent rehydration envelope (chat + layers + map). */
  activeSession: CaseSessionState | null;
  /** Drives PersistenceChip. */
  persistenceState: PersistenceState;

  // --- Envelope handlers (App.tsx wires these into GraceWs handlers) ---- //
  /**
   * Reconcile a case-list frame into the left rail.
   *
   * `isAuthoritative` distinguishes the SOURCE of an EMPTY list:
   *   - false (DEFAULT - the live WS path): an empty incoming list is a
   *     NON-authoritative keepalive/heartbeat blip; keep the current rail
   *     (the flicker fix). A non-empty list always replaces.
   *   - true (the /case-list cold FETCH path): an empty list is a GENUINE
   *     zero-cases answer and clears the rail (so deleting the last case
   *     followed by an authoritative empty list correctly empties it).
   */
  onCaseList: (
    payload: CaseListEnvelopePayload,
    isAuthoritative?: boolean,
  ) => void;
  onCaseOpen: (payload: CaseOpenEnvelopePayload) => void;

  // --- Emitters --------------------------------------------------------- //
  /** Create a new Case with optional title hint; defaults server-side to "Untitled Case". */
  createCase: (title?: string | null) => void;
  /** Open / hydrate an existing Case by id. */
  selectCase: (caseId: string) => void;
  /** Rename a Case (in-place title edit from CasesPanel). */
  renameCase: (caseId: string, newTitle: string) => void;
  /** Archive a Case (soft, reversible). */
  archiveCase: (caseId: string) => void;
  /** Delete a Case (soft-delete; CasesPanel confirms before calling). */
  deleteCase: (caseId: string) => void;
  /** Clear active Case locally — used when archive/delete targets the active Case. */
  clearActive: () => void;
}

/**
 * Track Case lifecycle + emit case-command envelopes.
 *
 * Stable callback identity for emitters is preserved via useCallback so
 * downstream subscribers (CasesPanel, App.tsx jumpTo wiring) don't re-render
 * on every parent render.
 */
export function useCases(opts: UseCasesOptions): UseCasesReturn {
  const { sendCaseCommand, isSignedIn } = opts;

  const [cases, setCases] = useState<CaseSummary[]>([]);
  const [activeCaseId, setActiveCaseId] = useState<string | null>(null);
  const [activeSession, setActiveSession] = useState<CaseSessionState | null>(
    null,
  );
  // Optimistic in-flight count: how many case-commands have been emitted
  // without a corresponding case-list / case-open reply. The reply clears
  // the counter back to zero. We keep this as a ref + state pair: the ref
  // is for the increment/decrement arithmetic (synchronous), the state is
  // for re-render triggering on transitions.
  const inFlightRef = useRef(0);
  const [inFlight, setInFlight] = useState(0);

  function bumpInFlight(delta: number): void {
    inFlightRef.current = Math.max(0, inFlightRef.current + delta);
    setInFlight(inFlightRef.current);
  }

  function settle(): void {
    inFlightRef.current = 0;
    setInFlight(0);
  }

  // --- Envelope handlers -------------------------------------------------- //
  const onCaseList = useCallback(
    (payload: CaseListEnvelopePayload, isAuthoritative = false) => {
      // CLIENT FLICKER FIX (per-Case layer DURABILITY) - the server re-ships a
      // full case-list on every resume INCLUDING the 25s keepalive heartbeat. A
      // heartbeat (or a reconnect mid-flight) can momentarily carry an EMPTY /
      // stale list; a wholesale `setCases(payload.cases ?? [])` then blanked the
      // left rail and refilled on the next good frame -> the flicker (and, since
      // the active-Case tombstone guard reads `cases`, a transient empty list
      // could race-clear the open Case). Reconcile instead: an EMPTY incoming
      // list while we already hold cases is treated as a non-authoritative pong
      // (keep what we have); a NON-empty list is authoritative and replaces
      // (covers genuine create / rename / archive / delete refreshes).
      //
      // LAST-CASE EDGE FIX (sleep/wake STAGE 2) - the empty-keep rule above is
      // correct for a WS keepalive blip but WRONG for the /case-list cold FETCH:
      // when the cold fetch returns an empty list it is the GENUINE truth (the
      // user has zero cases, e.g. just deleted the last one), so it MUST clear
      // the rail. `isAuthoritative` carries that source distinction: only an
      // authoritative empty list replaces; a non-authoritative (live-WS) empty
      // list keeps the current rail (preserving the flicker fix).
      const incoming = payload.cases ?? [];
      setCases((prev) =>
        incoming.length === 0 && prev.length > 0 && !isAuthoritative
          ? prev
          : incoming,
      );
      settle();
    },
    [],
  );

  const onCaseOpen = useCallback((payload: CaseOpenEnvelopePayload) => {
    const session = payload.session_state ?? null;
    // job-0273: optimistically upsert the opened Case into the rail list.
    // The auto-create flow emits case-open BEFORE the refreshed case-list
    // (observed live: 27ms apart). With a non-empty rail, the tombstone
    // guard below saw activeCaseId pointing at a Case that was not yet in
    // `cases` and bounced the user back to root — while Chat's adoption had
    // already cleared the root stream, leaving a fully EMPTY chat for the
    // whole turn. The envelope carries the full CaseSummary; the case-list
    // frame that follows canonicalizes.
    if (session) {
      setCases((prev) =>
        prev.some((c) => c.case_id === session.case.case_id)
          ? prev
          : [...prev, session.case],
      );
    }
    setActiveSession(session);
    setActiveCaseId(session?.case.case_id ?? null);
    settle();
  }, []);

  // --- Emitters ---------------------------------------------------------- //
  const createCase = useCallback(
    (title: string | null = null) => {
      const args: Record<string, unknown> =
        title && title.trim().length > 0 ? { title: title.trim() } : {};
      bumpInFlight(+1);
      sendCaseCommand("create", null, args);
    },
    [sendCaseCommand],
  );

  const selectCase = useCallback(
    (caseId: string) => {
      // sleep/wake STAGE 2 (NATE 2026-06-19) - ALWAYS set the active Case
      // LOCALLY, not only via the server's case-open reply. When the agent box
      // is asleep the WS `select` below merely QUEUES (ws.ts sendOrQueue) and no
      // `case-open` ever round-trips, so without this the App cold-load effect
      // (keyed on activeCaseId) would never arm and the Case never paints. The
      // local set is IDEMPOTENT with the live case-open reply (which sets the
      // same id + the rehydrated session); when the box is up the reply simply
      // re-affirms it. We do NOT clear activeSession here - a queued select that
      // never lands must leave any cold-loaded session in place.
      setActiveCaseId(caseId);
      bumpInFlight(+1);
      sendCaseCommand("select", caseId, {});
    },
    [sendCaseCommand],
  );

  const renameCase = useCallback(
    (caseId: string, newTitle: string) => {
      const trimmed = newTitle.trim();
      if (trimmed.length === 0) return; // server rejects empty; preempt
      // Optimistic: patch local cases list immediately so the row reflects
      // the new title without waiting for the server round-trip. The
      // case-list frame that follows will canonicalize.
      setCases((prev) =>
        prev.map((c) =>
          c.case_id === caseId ? { ...c, title: trimmed } : c,
        ),
      );
      bumpInFlight(+1);
      sendCaseCommand("rename", caseId, { title: trimmed });
    },
    [sendCaseCommand],
  );

  const archiveCase = useCallback(
    (caseId: string) => {
      bumpInFlight(+1);
      sendCaseCommand("archive", caseId, {});
    },
    [sendCaseCommand],
  );

  const deleteCase = useCallback(
    (caseId: string) => {
      // LAST-CASE LIVE FIX (box-off batch) - OPTIMISTICALLY drop the deleted
      // Case from the local rail immediately, instead of waiting on an
      // authoritative case-list to clear it. On the LIVE (connected) path the
      // server's follow-up empty case-list is NON-authoritative by design (the
      // flicker fix keeps a non-empty rail on an empty keepalive blip), so
      // deleting the user's LAST Case would otherwise leave it lingering in the
      // rail until a cold authoritative fetch. Removing it here covers the
      // last-Case case AND every non-last delete cleanly; the case-list frame
      // that follows canonicalizes. This is symmetric with the optimistic
      // rename patch above and does NOT touch the empty-keep keepalive rule.
      setCases((prev) => prev.filter((c) => c.case_id !== caseId));
      bumpInFlight(+1);
      sendCaseCommand("delete", caseId, {});
    },
    [sendCaseCommand],
  );

  const clearActive = useCallback(() => {
    setActiveCaseId(null);
    setActiveSession(null);
    // job-0269: tell the SERVER the client left the Case. Without this the
    // session-scoped active Case kept pointing at the last-opened Case, so
    // prompts from the root view skipped auto-create and dispatched into the
    // stale Case (live 2026-06-10: terrain prompt landed in the flood Case).
    sendCaseCommand("deselect", null, {});
  }, [sendCaseCommand]);

  // If the active Case was archived/deleted, clear local active state so the
  // map / chat reset cleanly. (case-list frame is the source of truth.) We
  // skip this check when `cases` is empty — case-open can arrive before
  // case-list (or independently of it in unit tests / dev injection), and
  // an empty list MUST NOT race-clear the active Case. The case-list frame
  // that follows will canonicalize on its own.
  useEffect(() => {
    if (activeCaseId === null) return;
    if (cases.length === 0) return;
    const found = cases.find((c) => c.case_id === activeCaseId);
    if (!found || found.status !== "active") {
      setActiveCaseId(null);
      setActiveSession(null);
    }
  }, [cases, activeCaseId]);

  // --- Persistence state derivation -------------------------------------- //
  const persistenceState: PersistenceState = useMemo(() => {
    if (!isSignedIn) return "anonymous";
    if (inFlight > 0) return "saving";
    return "saved";
  }, [isSignedIn, inFlight]);

  return {
    cases,
    activeCaseId,
    activeSession,
    persistenceState,
    onCaseList,
    onCaseOpen,
    createCase,
    selectCase,
    renameCase,
    archiveCase,
    deleteCase,
    clearActive,
  };
}
