// GRACE-2 web — top-level shell.
//
// job-0143 layout (sprint-12-mega Wave 4):
//
//   +-----------------------------------------------------------+
//   |  [☰ Layers] (TL hamburger, when left hidden)              |
//   |                                            [☰ Chat] (TR)  |
//   |                                                           |
//   |   Left rail (CasesPanel when no active Case,              |
//   |    CaseView with breadcrumb + LayerPanel when one is      |
//   |    selected)                                              |
//   |                                                           |
//   |   ...                                                     |
//   |                                                           |
//   |   [⚙ Settings] [🔑 Secrets]   ← bottom-row pills          |
//   |                       Map (full bleed)                    |
//   |                                       [LayerLegend] (BC)  |
//   +-----------------------------------------------------------+
//
// Restructure from job-0137 / Wave 3:
//   - When no Case is active, the left rail shows CasesPanel ONLY (no
//     LayerPanel — layers are a per-Case construct).
//   - When a Case is active, the left rail shows CaseView (breadcrumb +
//     LayerPanel embedded). Clicking the breadcrumb arrow deselects the
//     Case and returns to the Cases list; the map resets to CONUS.
//   - The [Settings] [Secrets] bottom-row pills replace the previous
//     bottom-left 🔑 key icon. Each opens a full-screen overlay popup.
//   - The top-right identity chip (auth/sign-out) is REMOVED; auth lives
//     in the Settings popup now.
//   - Anonymous "Sign in to save" copy is now triggered only at save
//     attempts via useSaveGate, not blanket-rendered.
//   - MapLibre navigation controls move to TOP-LEFT (under the
//     leftCollapsed hamburger) — Map.tsx owns the addControl call.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { MapView, type MapCommandSubscribeFunc, type MapTheme } from "./Map";
import { Chat, readChatWidth } from "./Chat";
import { LayerPanel, createLayerPanelBus, readLayersWidth } from "./LayerPanel";
import { LayerLegend } from "./components/LayerLegend";
import {
  AuthGate,
  clearAnonymousAccepted,
  readAnonymousAccepted,
} from "./components/AuthGate";
import { AuthGuard } from "./components/AuthGuard";
import { CasesPanel } from "./components/CasesPanel";
import { CaseView } from "./components/CaseView";
import { SettingsPopup } from "./components/SettingsPopup";
import { SecretsPopup } from "./components/SecretsPopup";
import { ToolsCatalogPopup } from "./components/ToolsCatalogPopup";
import {
  RoutingQualityDashboard,
  type RoutingDashboardSummary,
} from "./components/RoutingQualityDashboard";
import {
  ImpactPanel,
  type ImpactEnvelope,
} from "./components/ImpactPanel";
import type { ChartPayload } from "./components/ChartStack";
import { BottomRowButtons } from "./components/BottomRowButtons";
import { SaveGateModal } from "./components/SaveGateModal";
import {
  SourceSuggestionAction,
  SourceSuggestionInline,
} from "./components/SourceSuggestionInline";
import { PayloadWarningInline } from "./components/PayloadWarningInline";
import {
  AuthUser,
  onAuthChanged,
  signOut as authSignOut,
  signIn as authSignIn,
  handleRedirectCallback,
} from "./auth";
import { GraceWs } from "./ws";
import { SourceCandidatePayload } from "./lib/source_suggestion_suppression";
import { extractLastZoomTo } from "./lib/case_zoom";
import { useCases } from "./hooks/useCases";
import { useIsMobile } from "./hooks/useIsMobile";
import { useSaveGate } from "./hooks/useSaveGate";
import {
  MobileDrawer,
  MobileDrawerButton,
} from "./components/MobileDrawer";
import {
  CaseListEnvelopePayload,
  CaseOpenEnvelopePayload,
  MapCommandPayload,
  PayloadConfirmationDecision,
  PayloadWarningEnvelopePayload,
  PipelineStatePayload,
  ProjectLayerSummary,
  ProviderID,
  SecretRecord,
  SecretsListPayload,
  SessionStatePayload,
} from "./contracts";

// localStorage keys for panel collapse state (job-0065).
const LS_LEFT_COLLAPSED = "grace2.leftPanelCollapsed";
const LS_RIGHT_COLLAPSED = "grace2.rightPanelCollapsed";
// localStorage key for map theme (job-0076).
const LS_THEME = "grace2.theme";

function readTheme(): MapTheme {
  try {
    const v = localStorage.getItem(LS_THEME);
    return v === "dark" ? "dark" : "light";
  } catch {
    return "light";
  }
}

function readCollapsed(key: string): boolean {
  try {
    return localStorage.getItem(key) === "true";
  } catch {
    return false;
  }
}

// WebSocket endpoint — local agent (job-0015) on port 8765.
// Override at build time with VITE_GRACE2_WS_URL. job-0275: the default now
// derives the host from the page's own hostname (same pattern as the tool
// catalog HTTP endpoint), so the SAME dev build works from localhost, the
// LAN, or a tailnet — phones hitting http://<host>:5173 reach the agent at
// ws://<host>:8765 instead of dialing their own localhost.
const WS_URL: string =
  (import.meta.env.VITE_GRACE2_WS_URL as string | undefined) ??
  (typeof window !== "undefined" && window.location?.hostname
    ? `ws://${window.location.hostname}:8765`
    : "ws://localhost:8765");

declare global {
  interface Window {
    __grace2InjectSessionState?: (p: SessionStatePayload) => void;
    __grace2InjectMapCommand?: (p: MapCommandPayload) => void;
    /** Dev seam for pipeline-state; wired by Chat.tsx via its GraceWs handler. */
    __grace2InjectPipelineState?: (p: PipelineStatePayload) => void;
    /** Dev seam for error (job-0166); wired by Chat.tsx via its GraceWs handler. */
    __grace2InjectError?: (p: import("./contracts").ErrorPayload) => void;
    /** Dev seam for secrets-list (job-0125); wired by App.tsx GraceWs handler. */
    __grace2InjectSecretsList?: (p: SecretsListPayload) => void;
    /** Dev seam for source-suggestion (job-0126 → renamed job-0145); wired by App.tsx GraceWs handler. */
    __grace2InjectSourceSuggestion?: (p: SourceCandidatePayload) => void;
    /** Dev seam for case-list (job-0137); wired by App.tsx GraceWs handler. */
    __grace2InjectCaseList?: (p: CaseListEnvelopePayload) => void;
    /** Dev seam for case-open (job-0137); wired by App.tsx GraceWs handler. */
    __grace2InjectCaseOpen?: (p: CaseOpenEnvelopePayload) => void;
    /** Dev seam for payload-warning (job-0140); wired by App.tsx GraceWs handler. */
    __grace2InjectPayloadWarning?: (p: PayloadWarningEnvelopePayload) => void;
    /**
     * Dev seam for ImpactEnvelope panel (Wave 4.11 P4). Tests + Playwright
     * UI-driver pass a full ImpactEnvelope to surface the side panel.
     */
    __grace2InjectImpactEnvelope?: (p: ImpactEnvelope | null) => void;
    /**
     * Dev seam for chart-emission (sprint-13 job-0231). Playwright / tests
     * inject a ChartPayload to surface the inline stacked preview + gallery
     * without driving a live agent. Mirrors __grace2InjectImpactEnvelope.
     */
    __grace2InjectChartEmission?: (p: ChartPayload) => void;
    /**
     * Dev seam to reset charts (sprint-13 job-0231). Lets Playwright clear
     * the accumulated chart list between test scenarios.
     */
    __grace2ClearCharts?: () => void;
  }
}

// Shared hamburger button style (job-0068). Same-side-as-panel per user direction.
// z-index 30 so it renders above panels (z=20) and legend (z=10).
// job-0283 — desktop sleekness: hairline border + 10px radius + blur so the
// hamburgers sit in the same surface family as the rail panels/pills.
// Desktop-only (mobile uses MobileDrawerButton).
const hamburgerBtnStyle: React.CSSProperties = {
  position: "absolute",
  background: "rgba(18,19,24,0.92)",
  border: "1px solid rgba(255,255,255,0.08)",
  borderRadius: 10,
  boxShadow: "0 2px 12px rgba(0,0,0,0.35)",
  backdropFilter: "blur(6px)",
  WebkitBackdropFilter: "blur(6px)",
  color: "#cfd4db",
  width: 40,
  height: 40,
  padding: 0,
  cursor: "pointer",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  fontSize: 18,
  zIndex: 30,
  lineHeight: 1,
  top: 12,
};

export function App(): JSX.Element {
  const bus = useMemo(() => createLayerPanelBus(), []);

  // job-0278 — mobile layout (<768px). EVERY mobile divergence below is
  // guarded by this flag so desktop renders pixel-identical to before.
  const isMobile = useIsMobile();
  // Mobile-only: slide-in drawer (replaces the desktop left rail). Hidden
  // by default; deliberately NOT persisted to localStorage — the drawer is
  // an overlay, and the desktop collapse keys keep their own semantics.
  const [mobileDrawerOpen, setMobileDrawerOpen] = useState<boolean>(false);

  // Collapse toggles — initialised from localStorage so reloads remember
  // the user's preference.
  const [leftCollapsed, setLeftCollapsed] = useState<boolean>(() =>
    readCollapsed(LS_LEFT_COLLAPSED),
  );
  const [rightCollapsed, setRightCollapsed] = useState<boolean>(() =>
    readCollapsed(LS_RIGHT_COLLAPSED),
  );
  // ux-batch-1 J1 (F10) — App mirrors the user-dragged chat width so dependent
  // chrome (and the F16 payload-warning banner) can track the chat column edge.
  // Chat owns persistence; App seeds from the same localStorage value and
  // updates via Chat's onWidthChange. Initial read matches Chat's own init.
  const [chatWidth, setChatWidth] = useState<number>(() => readChatWidth());
  // ux-batch-1 J1 (F11) — App mirrors the user-dragged Layers-panel width so
  // the desktop pointer-events wrapper can grow with the panel (else clicks on
  // a widened panel fall through to the map). LayerPanel owns persistence.
  const [layersWidth, setLayersWidth] = useState<number>(() => readLayersWidth());

  // Layers lifted here from session-state so:
  //   (a) LayerLegend can read the list
  //   (b) we can gate the left panel conditional mount on layers.length > 0
  const [layers, setLayers] = useState<ProjectLayerSummary[]>([]);

  // Map theme (job-0076).
  const [theme, setTheme] = useState<MapTheme>(() => readTheme());

  // Auth state (job-0123, sprint-12-mega Wave 2).
  const [authUser, setAuthUser] = useState<AuthUser | null>(null);
  const [authResolved, setAuthResolved] = useState<boolean>(false);
  // job-0253 (sprint-13.5) — auth-expired latch from ws.ts (close 4401 /
  // AUTH_FAILED, after the one-shot forceRefresh retry failed). Drops a
  // signed-in user to the AuthGuard sign-in surface. Cleared whenever a fresh
  // signed-in user arrives (re-sign-in succeeded).
  const [authExpired, setAuthExpired] = useState<boolean>(false);
  // job-0253b — re-sign-in reconnect epoch. handleAuthFailure's give-up branch
  // (ws.ts:1032-1035) leaves BOTH GraceWs sockets terminally dead (no
  // reconnect is scheduled — correct; we must not hammer the gate). Nothing
  // reconnects them later on its own: the App ws effect's deps are otherwise
  // stable and Chat keys on [wsUrl, bump]. So after a successful re-sign-in the
  // guard would render children over dead sockets until a full page reload.
  // We bump `authEpoch` exactly when a fresh non-anonymous user lands WHILE we
  // were auth-expired; `authEpoch` is threaded into both ws effects' deps, so
  // each effect tears its dead socket down (cleanup → ws.close()) and opens a
  // fresh one (new GraceWs + connect(), which resets the auth latches at
  // ws.ts:424-427) — exactly once per recovery, never in disabled/dev mode
  // (Firebase disabled → onAuthChanged only ever fires null → authExpired is
  // never set → this branch is unreachable, so authEpoch stays 0 forever).
  const [authEpoch, setAuthEpoch] = useState<number>(0);
  const authExpiredRef = useRef<boolean>(false);
  authExpiredRef.current = authExpired;

  // GCP→AWS migration — Cognito Hosted UI OAuth /callback handler. On boot, if
  // the URL carries a `?code=` (the authorization-code returned by the Hosted
  // UI), exchange it for tokens via auth.ts, then strip the query so a reload
  // doesn't re-trigger the exchange. onAuthChanged (below) flips authUser once
  // the token set lands. No-op when there is no code / Cognito is disabled, so
  // the dev/tailnet pass-through path is untouched.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    if (!params.has("code")) return;
    void (async () => {
      try {
        await handleRedirectCallback();
      } catch {
        // Exchange failures drop to the sign-in surface on next render.
      } finally {
        // Strip ?code (+ ?state) from the URL regardless of outcome.
        const url = new URL(window.location.href);
        url.searchParams.delete("code");
        url.searchParams.delete("state");
        window.history.replaceState(
          {},
          document.title,
          url.pathname + url.search + url.hash,
        );
      }
    })();
  }, []);

  useEffect(() => {
    const unsub = onAuthChanged((u) => {
      setAuthUser(u);
      setAuthResolved(true);
      // A real (non-anonymous) sign-in clears any prior auth-expired state and,
      // if we WERE auth-expired (the dead-socket wedge), bumps authEpoch so
      // both ws effects reconnect. The ref read avoids re-subscribing on every
      // authExpired flip.
      if (u && !u.isAnonymous) {
        if (authExpiredRef.current) setAuthEpoch((n) => n + 1);
        setAuthExpired(false);
      }
    });
    return unsub;
  }, []);

  // job-0138: anonymous-accepted flag.
  const [anonymousAccepted, setAnonymousAccepted] = useState<boolean>(() =>
    readAnonymousAccepted(),
  );
  const [upgradeToast, setUpgradeToast] = useState<string | null>(null);
  const prevSignedInRef = useRef<boolean>(false);
  useEffect(() => {
    const nowSignedIn = !!authUser && !authUser.isAnonymous;
    const wasSignedIn = prevSignedInRef.current;
    prevSignedInRef.current = nowSignedIn;
    if (nowSignedIn && !wasSignedIn && anonymousAccepted) {
      clearAnonymousAccepted();
      setAnonymousAccepted(false);
      setUpgradeToast("Welcome back — your Cases will now sync");
      const t = setTimeout(() => setUpgradeToast(null), 4500);
      return () => clearTimeout(t);
    }
    return undefined;
  }, [authUser, anonymousAccepted]);

  // Gate render rule: show the app only when authenticated OR anonymous-accepted.
  const appShouldRender: boolean =
    authResolved &&
    ((!!authUser && !authUser.isAnonymous) || anonymousAccepted);

  // AuthGate handlers.
  const handleAnonymousAccept = useCallback(() => {
    setAnonymousAccepted(true);
  }, []);
  const handleSignOut = useCallback(async () => {
    try {
      await authSignOut();
    } catch {
      // non-fatal
    }
    clearAnonymousAccepted();
    setAnonymousAccepted(false);
  }, []);

  // Sign-in handler routed through Settings + SaveGate. Redirects to the
  // Cognito Hosted UI (email/password); the browser navigates away and the
  // /callback effect below completes the round-trip on return.
  const handleSignInRequest = useCallback(() => {
    void (async () => {
      try {
        await authSignIn();
      } catch {
        // Sign-in errors surface on the gate surface; nothing to do here.
      }
    })();
  }, []);

  // Secrets state (job-0125).
  const [secrets, setSecrets] = useState<SecretRecord[]>([]);
  const wsRef = useRef<GraceWs | null>(null);

  // Settings + Secrets popup visibility (job-0143).
  const [settingsOpen, setSettingsOpen] = useState<boolean>(false);
  const [secretsOpen, setSecretsOpen] = useState<boolean>(false);
  // Wave 4.10 C1: tools-catalog popup visibility.
  const [toolsCatalogOpen, setToolsCatalogOpen] = useState<boolean>(false);
  // Wave 4.11 M7: routing-quality dashboard visibility.
  const [routingDashOpen, setRoutingDashOpen] = useState<boolean>(false);
  // Wave 4.11 M7: optional inject seam for Playwright snapshots. When the
  // window-attached fixture is present we mount the dashboard with the
  // pre-fetched summary so the visual smoke test renders without driving a
  // live agent. Production code never touches this — guarded behind a
  // global flag set only in the dev-tools harness.
  const [routingDashInjected, setRoutingDashInjected] =
    useState<RoutingDashboardSummary | null>(null);
  useEffect(() => {
    interface InjectWindow {
      __grace2InjectTelemetryFixture?: RoutingDashboardSummary;
    }
    const w = window as unknown as InjectWindow;
    if (w.__grace2InjectTelemetryFixture) {
      setRoutingDashInjected(w.__grace2InjectTelemetryFixture);
      setRoutingDashOpen(true);
    }
  }, []);
  // Wave 4.11 P4: ImpactEnvelope side panel. Populated when a
  // ``compute_impact_envelope`` tool result arrives carrying
  // ``raw_envelope.n_structures_total`` (the ImpactEnvelope shape from B.6c).
  const [impactEnvelope, setImpactEnvelope] = useState<ImpactEnvelope | null>(
    null,
  );

  // sprint-13 job-0231: chart-emission accumulates per session in App.tsx's
  // GraceWs connection so the session-scoped hub fan-out reaches Chat.tsx.
  // Charts are actually rendered in Chat.tsx; App.tsx only holds the state
  // for reset-on-Case-switch (replace-not-reconcile) and the dev seam.
  const [charts, setCharts] = useState<ChartPayload[]>([]);

  const handleChartEmission = useCallback((p: ChartPayload) => {
    setCharts((prev) => {
      // De-duplicate on chart_id so re-emits from the same tool don't stack.
      if (prev.some((c) => c.chart_id === p.chart_id)) return prev;
      return [...prev, p];
    });
  }, []);

  // job-0137 Cases UX shell + job-0143 save-gate wiring.
  const sendCaseCommand = useCallback(
    (
      command: Parameters<GraceWs["sendCaseCommand"]>[0],
      caseId: string | null,
      args: Record<string, unknown>,
    ) => {
      wsRef.current?.sendCaseCommand(command, caseId, args);
    },
    [],
  );
  const isSignedIn = !!authUser && !authUser.isAnonymous;
  const {
    cases,
    activeCaseId,
    activeSession,
    onCaseList: useCases_onCaseList,
    onCaseOpen: useCases_onCaseOpen,
    createCase,
    selectCase,
    renameCase,
    archiveCase,
    deleteCase,
    clearActive,
  } = useCases({ sendCaseCommand, isSignedIn });

  // job-0143: gate save-triggering Case actions for anonymous users.
  const saveGate = useSaveGate({
    isSignedIn,
    onSignInRequest: handleSignInRequest,
  });

  const onCreateGated = useMemo(
    () => saveGate.gateAction(() => createCase(), "Create a new Case"),
    [saveGate, createCase],
  );
  const onRenameGated = useCallback(
    (caseId: string, newTitle: string) => {
      saveGate.gateAction(
        () => renameCase(caseId, newTitle),
        "Rename Case",
      )();
    },
    [saveGate, renameCase],
  );
  const onArchiveGated = useCallback(
    (caseId: string) => {
      saveGate.gateAction(
        () => archiveCase(caseId),
        "Archive Case",
      )();
    },
    [saveGate, archiveCase],
  );
  // job-0276: delete is NOT save-gated. It already has its own
  // ConfirmationDialog, and stacking the "Sign in to save" gate on top of
  // the delete confirm was live-reproduced as a click-eating modal trap
  // ("can't get back into the Case"). Deleting work is also not a
  // save-upsell moment.
  const onDeleteGated = useCallback(
    (caseId: string) => {
      deleteCase(caseId);
    },
    [deleteCase],
  );

  // currentCaseId for the SecretsPopup scope.
  const currentCaseId: string | null = activeCaseId;

  // job-0127: payload-warning gates.
  //
  // job-0266 — confirmations are keyed by Case: each warning is tagged with
  // the Case that was active when it arrived (null = root), and only the
  // warnings belonging to the CURRENTLY visible Case render. A warning for
  // a non-visible Case buffers until the user returns to that Case — it is
  // never painted into another Case's view.
  const [payloadWarnings, setPayloadWarnings] = useState<
    Array<{ warning: PayloadWarningEnvelopePayload; caseId: string | null }>
  >([]);
  // Latest activeCaseId for the once-bound WS handler closures. Assigned
  // during render so a warning arriving any time after a Case switch tags
  // with the up-to-date Case.
  const activeCaseIdRef = useRef<string | null>(null);
  activeCaseIdRef.current = activeCaseId;
  const handlePayloadWarning = useCallback(
    (p: PayloadWarningEnvelopePayload) => {
      setPayloadWarnings((prev) => [
        { warning: p, caseId: activeCaseIdRef.current },
        ...prev,
      ]);
    },
    [],
  );
  const handlePayloadWarningDecide = useCallback(
    (
      warningId: string,
      decision: PayloadConfirmationDecision,
      revised: Record<string, unknown> | null,
    ) => {
      wsRef.current?.sendPayloadConfirmation(warningId, decision, revised);
      setPayloadWarnings((prev) =>
        prev.filter((t) => t.warning.warning_id !== warningId),
      );
    },
    [],
  );

  // job-0126 (renamed job-0145): source-suggestion candidate fan-out. Server
  // wire envelope_type is still `mode2-candidate` (internal); UI translates.
  const sourceSuggestionSubscribersRef = useRef<
    Set<(p: SourceCandidatePayload) => void>
  >(new Set());
  const subscribeSourceSuggestion = useCallback(
    (cb: (p: SourceCandidatePayload) => void) => {
      sourceSuggestionSubscribersRef.current.add(cb);
      return () => {
        sourceSuggestionSubscribersRef.current.delete(cb);
      };
    },
    [],
  );
  const fanoutSourceSuggestion = useCallback(
    (p: SourceCandidatePayload) => {
      sourceSuggestionSubscribersRef.current.forEach((cb) => {
        try {
          cb(p);
        } catch {
          // eslint-disable-next-line no-console
          console.error("[source-suggestion] subscriber threw");
        }
      });
    },
    [],
  );

  const handleSourceSuggestionAction = useCallback(
    (action: SourceSuggestionAction) => {
      const ws = wsRef.current;
      const c = action.candidate;
      if (action.kind === "add") {
        ws?.sendMode2AddConfirmed({
          candidate_id: c.candidate_id,
          url: c.url,
          domain: c.domain,
          suggested_tool_kind: c.suggested_tool_kind,
        });
      }
      ws?.sendMode2AuditEvent({
        candidate_id: c.candidate_id,
        domain: c.domain,
        action: action.kind,
        confidence: c.confidence,
        surface: "inline",
      });
      // eslint-disable-next-line no-console
      console.debug(
        `[source-suggestion-audit] ${action.kind} surface=inline domain=${c.domain} candidate=${c.candidate_id}`,
      );
    },
    [],
  );

  function toggleTheme(): void {
    setTheme((prev) => {
      const next: MapTheme = prev === "light" ? "dark" : "light";
      try { localStorage.setItem(LS_THEME, next); } catch { /* non-fatal */ }
      return next;
    });
  }

  function collapseLeft(): void {
    setLeftCollapsed(true);
    try { localStorage.setItem(LS_LEFT_COLLAPSED, "true"); } catch { /* non-fatal */ }
  }

  function expandLeft(): void {
    setLeftCollapsed(false);
    try { localStorage.setItem(LS_LEFT_COLLAPSED, "false"); } catch { /* non-fatal */ }
  }

  function collapseRight(): void {
    setRightCollapsed(true);
    try { localStorage.setItem(LS_RIGHT_COLLAPSED, "true"); } catch { /* non-fatal */ }
  }

  function expandRight(): void {
    setRightCollapsed(false);
    try { localStorage.setItem(LS_RIGHT_COLLAPSED, "false"); } catch { /* non-fatal */ }
  }

  // job-0143: clicking the breadcrumb arrow deselects the active Case.
  const handleCaseBack = useCallback(() => {
    clearActive();
  }, [clearActive]);

  // Mount a GraceWs that routes session-state, map-command, AND secrets-list.
  useEffect(() => {
    const ws = new GraceWs(WS_URL, {
      onStatus: () => { /* status on Chat panel */ },
      onAgentChunk: () => { /* Chat owns rendering */ },
      onPipelineState: () => { /* Chat owns rendering */ },
      onSessionState: (p) => bus.pushSessionState(p),
      onMapCommand: (p) => bus.pushMapCommand(p),
      onSecretsList: (p) => setSecrets(p.secrets ?? []),
      onMode2Candidate: (p) => fanoutSourceSuggestion(p),
      // job-0266 — tags the warning with the Case active at arrival.
      onPayloadWarning: (p) => handlePayloadWarning(p),
      onCaseList: (p: CaseListEnvelopePayload) => useCases_onCaseList(p),
      onCaseOpen: (p: CaseOpenEnvelopePayload) => useCases_onCaseOpen(p),
      onError: () => { /* Chat owns rendering */ },
      // job-0253 (sprint-13.5): the agent's prod auth gate rejected us
      // (4401 / AUTH_FAILED) and the one-shot token refresh also failed.
      // Drop to the AuthGuard sign-in surface. No-op when Firebase is
      // disabled (the gate never engages in dev/tailnet mode).
      onAuthExpired: () => setAuthExpired(true),
      // Wave 4.11 P4: surface ImpactPanel when agent emits impact-envelope.
      onImpactEnvelope: (p) => setImpactEnvelope(p),
      // sprint-13 job-0231: accumulate chart-emission payloads per session.
      onChartEmission: (p) => handleChartEmission(p),
    });
    wsRef.current = ws;
    ws.connect();
    return () => {
      wsRef.current = null;
      ws.close();
    };
    // job-0253b — authEpoch is bumped on a recovered re-sign-in (see the
    // onAuthChanged effect above); re-running this effect closes the dead
    // post-4401 socket and opens a fresh one. In disabled/dev mode authEpoch
    // never changes, so this effect runs exactly once as before.
  }, [bus, fanoutSourceSuggestion, useCases_onCaseList, useCases_onCaseOpen, handleChartEmission, handlePayloadWarning, authEpoch]);

  // job-0137: Case rehydration replay.
  useEffect(() => {
    // sprint-13 job-0231: Case switch resets charts (replace-not-reconcile
    // client-side rule). Charts for the new Case replay via
    // activeSession.charts below; on null (no active Case) we clear.
    setCharts([]);
    // M5.5: the ImpactPanel is per-Case ephemeral state. Without this reset
    // the slide-out from the previous Case bled into the next Case on switch
    // (same client-side replace-not-reconcile gap as charts). It re-populates
    // when the new Case's agent emits a fresh impact-envelope.
    setImpactEnvelope(null);

    if (activeSession === null) {
      bus.pushSessionState({
        loaded_layers: [],
        chat_history: [],
        pipeline_history: [],
        current_pipeline: null,
        map_view: null,
      });
      // ux-batch-1 (F14): exiting a Case must reset client map state, not just
      // the panels. The analysis-extent (AOI) rectangle is drawn directly on
      // the map by Map.tsx and is NOT part of loaded_layers, so clearing
      // session-state above does not remove it. Emit an explicit clear so the
      // prior Case's AOI outline does not linger on the root/new-case map.
      bus.pushMapCommand({
        command: "clear-analysis-extent",
      } as unknown as MapCommandPayload);
      return;
    }
    bus.pushSessionState({
      loaded_layers: activeSession.loaded_layers ?? [],
      chat_history: activeSession.chat_history ?? [],
      pipeline_history: activeSession.pipeline_history ?? [],
      current_pipeline: activeSession.current_pipeline ?? null,
      map_view: null,
    });
    const bbox = activeSession.case.bbox;
    if (bbox && bbox.length === 4) {
      bus.pushMapCommand({
        command: "zoom-to",
        args: { bbox },
      } as unknown as MapCommandPayload);
    } else {
      // job-0280 — Case-open snap-to-location. `CaseSummary.bbox` is null in
      // practice today, so fall back to replaying the LAST `zoom-to` the
      // Case's persisted turns emitted (CaseChatMessage.map_command_emissions
      // in the rehydrated chat_history) through the SAME bus → Map.tsx
      // fitBounds path. No zoom-to anywhere in history → leave the camera
      // alone (root/new Cases unchanged).
      const replay = extractLastZoomTo(activeSession.chat_history);
      if (replay) {
        bus.pushMapCommand(replay as unknown as MapCommandPayload);
      } else {
        // ux-batch-1 (F14): this Case has no AOI of its own (no bbox, no
        // zoom-to in history). Clear any extent left over from the previously
        // viewed Case so switching into a no-AOI Case doesn't inherit a stale
        // rectangle. (A Case WITH an AOI replaces the extent via zoom-to.)
        bus.pushMapCommand({
          command: "clear-analysis-extent",
        } as unknown as MapCommandPayload);
      }
    }
    // Rehydrate charts from session. ``activeSession.charts`` is the
    // append-only array persisted via SessionChartRecord (sprint-13 schema).
    // When the field is absent (older sessions) or empty, charts stays [].
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const sessionCharts = (activeSession as any).charts as ChartPayload[] | undefined;
    if (Array.isArray(sessionCharts) && sessionCharts.length > 0) {
      setCharts(sessionCharts.filter(
        (c) => c && typeof c.chart_id === "string" && c.vega_lite_spec,
      ));
    }
  }, [activeSession, bus]);

  // Lift layers from session-state.
  useEffect(() => {
    const unsub = bus.subscribeSessionState((p) => {
      setLayers(p.loaded_layers ?? []);
    });
    return unsub;
  }, [bus]);

  // Dev-only debug seam.
  useEffect(() => {
    if (!import.meta.env.DEV) return;
    window.__grace2InjectSessionState = (p) => bus.pushSessionState(p);
    window.__grace2InjectMapCommand = (p) => bus.pushMapCommand(p);
    window.__grace2InjectSecretsList = (p) => setSecrets(p.secrets ?? []);
    window.__grace2InjectSourceSuggestion = (p) => fanoutSourceSuggestion(p);
    window.__grace2InjectCaseList = (p) => useCases_onCaseList(p);
    window.__grace2InjectCaseOpen = (p) => useCases_onCaseOpen(p);
    window.__grace2InjectPayloadWarning = (p) => handlePayloadWarning(p);
    window.__grace2InjectImpactEnvelope = (p) => setImpactEnvelope(p);
    // sprint-13 job-0231: chart injection seam for Playwright snapshots.
    // App.tsx owns the window seam; Chat.tsx receives the fan-out via
    // its own GraceWs onChartEmission handler (SESSION_SCOPED_TYPES hub).
    window.__grace2InjectChartEmission = (p) => handleChartEmission(p);
    window.__grace2ClearCharts = () => {
      // App.tsx chart state is the authoritative reset source. Charts in
      // Chat.tsx are reset separately via its own case-open handler.
      setCharts([]);
    };
    // Expose the current chart count for Playwright introspection.
    (window as unknown as Record<string, unknown>).__grace2ChartCount = () => charts.length;
    return () => {
      delete window.__grace2InjectSessionState;
      delete window.__grace2InjectMapCommand;
      delete window.__grace2InjectSecretsList;
      delete window.__grace2InjectSourceSuggestion;
      delete window.__grace2InjectCaseList;
      delete window.__grace2InjectCaseOpen;
      delete window.__grace2InjectPayloadWarning;
      delete window.__grace2InjectImpactEnvelope;
      delete window.__grace2InjectChartEmission;
      delete window.__grace2ClearCharts;
    };
  }, [bus, fanoutSourceSuggestion, useCases_onCaseList, useCases_onCaseOpen, handleChartEmission, handlePayloadWarning]);

  // job-0125: bridge SecretsPanel callbacks to the active GraceWs.
  function handleSecretAdd(payload: {
    provider: ProviderID;
    case_id: string | null;
    label: string | null;
    key_value: string;
  }): void {
    if (!wsRef.current) return;
    wsRef.current.sendSecretAdd(payload);
  }

  function handleSecretRevoke(secretId: string): void {
    if (!wsRef.current) return;
    wsRef.current.sendSecretRevoke(secretId);
  }

  const showLayersHamburger = leftCollapsed;
  const showChatHamburger = rightCollapsed;

  // job-0138: AuthGate full-screen gating (the anonymous-accept gate). job-0253
  // wraps it in AuthGuard: when Firebase is DISABLED (dev/tailnet — every
  // current session), AuthGuard is a transparent pass-through and this renders
  // exactly as before. When Firebase is ENABLED + signed-out (production),
  // AuthGuard renders its own Google-only sign-in surface and the anonymous
  // gate below is never reached (Decision 6 — no anonymous in prod).
  if (!appShouldRender) {
    return (
      <AuthGuard authExpired={authExpired}>
        <AuthGate onAnonymousAccept={handleAnonymousAccept} />
      </AuthGuard>
    );
  }

  // job-0143: derive the active Case object for the breadcrumb title.
  const activeCase = activeCaseId
    ? cases.find((c) => c.case_id === activeCaseId) ?? null
    : null;

  // job-0266 — only the warnings belonging to the visible Case (or the
  // root, for warnings tagged null) render; the rest buffer per-Case.
  const visiblePayloadWarnings = payloadWarnings.filter(
    (t) => t.caseId === activeCaseId,
  );

  // job-0253 — AuthGuard wraps the app shell. DISABLED (dev/tailnet) ⇒
  // transparent pass-through, pixel-identical render. ENABLED + signed-in ⇒
  // children render + a minimal "Sign out" affordance. ENABLED + expired ⇒
  // back to the sign-in surface.
  return (
    <AuthGuard authExpired={authExpired}>
    <div
      data-testid="grace2-app-shell"
      style={{
        position: "fixed",
        inset: 0,
      }}
    >
      {/* Full-bleed map — first in DOM so panels render above it. */}
      <MapView
        subscribeSessionState={bus.subscribeSessionState}
        subscribeMapCommand={bus.subscribeMapCommand as MapCommandSubscribeFunc}
        theme={theme}
      />

      {/* LayerLegend — bottom-center absolute; z-index 10. job-0278: on
          mobile it rides in a zero-height offset wrapper so it clears the
          bottom-sheet composer (~126px collapsed); the expanded sheet
          (z=32) simply covers it. */}
      {isMobile ? (
        <div
          data-testid="grace2-mobile-legend-offset"
          style={{
            position: "absolute",
            left: 0,
            right: 0,
            bottom: 116,
            height: 0,
            pointerEvents: "none",
            zIndex: 10,
          }}
        >
          <LayerLegend layers={layers} />
        </div>
      ) : (
        <LayerLegend layers={layers} />
      )}

      {/* Left rail (job-0143):
            - No active Case → CasesPanel only (list view).
            - Active Case → CaseView (breadcrumb + LayerPanel children).
          job-0278: desktop only — on mobile the SAME content rides in the
          slide-in MobileDrawer below. */}
      {!isMobile && !leftCollapsed && activeCaseId === null && (
        <div
          data-testid="grace2-left-rail"
          data-mode="cases-list"
          /* job-0283 — scopes the desktop sleekness CSS (global.css) to the
             desktop rail only; the mobile drawer renders these components
             without this class and stays pixel-identical to job-0280. */
          className="grace2-desktop-rail"
          style={{
            position: "absolute",
            top: 12,
            left: 12,
            zIndex: 20,
            maxHeight: "calc(100vh - 80px)",
          }}
        >
          <CasesPanel
            cases={cases}
            activeCaseId={activeCaseId}
            onCreate={onCreateGated}
            onSelect={selectCase}
            onRename={onRenameGated}
            onArchive={onArchiveGated}
            onDelete={onDeleteGated}
          />
        </div>
      )}
      {!isMobile && !leftCollapsed && activeCaseId !== null && (
        <>
          {/* Breadcrumb at the canonical top-left position. z-index 22 so
              it sits ABOVE the LayerPanel wrapper (z=20) — the panel is
              repositioned below the breadcrumb via top/left wrapper css. */}
          <div
            data-testid="grace2-left-rail"
            data-mode="case-view"
            /* job-0283 — same desktop-only sleekness scope as cases-list mode. */
            className="grace2-desktop-rail"
            style={{
              position: "absolute",
              top: 12,
              left: 12,
              zIndex: 22,
              width: 280,
            }}
          >
            <CaseView
              caseTitle={activeCase?.title ?? "Case"}
              onBack={handleCaseBack}
            />
            {layers.length === 0 && (
              <div
                data-testid="grace2-case-view-empty-layers"
                style={{
                  marginTop: 8,
                  background: "rgba(15,15,20,0.92)",
                  border: "1px dashed #444",
                  borderRadius: 8,
                  padding: 12,
                  color: "#999",
                  fontSize: 12,
                  textAlign: "center",
                  lineHeight: 1.4,
                  width: 280,
                  boxSizing: "border-box",
                }}
              >
                No layers loaded yet. Ask the assistant to add data.
              </div>
            )}
          </div>
          {/* LayerPanel — its own absolute positioning at left:16, top:16.
              We mount it directly so MapLibre rendering picks up its
              effects; the visual placement below the breadcrumb is
              achieved by leaving room above (top:64 used by no wrapper —
              LayerPanel itself spans the column). The breadcrumb at
              z-index 22 sits above LayerPanel's chrome at z-index 20. */}
          {layers.length > 0 && (
            <div
              data-testid="grace2-case-view-layer-panel-wrap"
              style={{ position: "absolute", top: 64, left: 0, right: 0, bottom: 60, zIndex: 20, pointerEvents: "none" }}
            >
              {/* job-0173 Part 3 — confine the pointer-events:auto region to
                  the LayerPanel column only. The prior implementation made the
                  inner div full-bleed (width:100%, height:100%) with
                  pointerEvents:"auto", which blocked map drag/pan everywhere
                  inside the (top:64 → bottom:60, left:0 → right:0) zone —
                  i.e. virtually the entire map. LayerPanel is absolutely
                  positioned at left:16 / top:16 / bottom:16 / width:280
                  relative to this wrapper, so a 280px-wide column from the
                  left edge is the exact click target. Outside that column,
                  map pan/drag passes through (parent pointerEvents:"none"). */}
              <div
                style={{
                  pointerEvents: "auto",
                  position: "absolute",
                  left: 0,
                  top: 0,
                  bottom: 0,
                  // ux-batch-1 J1 (F11): track the dragged panel width so the
                  // click target (incl. the right-edge resize handle) always
                  // covers the panel. left:16 offset + panel + 16 right pad.
                  width: layersWidth + 16 + 16,
                }}
              >
                <LayerPanel
                  subscribeSessionState={bus.subscribeSessionState}
                  subscribeMapCommand={bus.subscribeMapCommand}
                  initialLayers={layers}
                  onClose={collapseLeft}
                  width={layersWidth}
                  onWidthChange={setLayersWidth}
                  /* job-0258: user layer-control intents (opacity slider /
                     visibility checkbox / drag-reorder) flow through the bus
                     so MapView applies them to the live MapLibre instance.
                     Without this the panel controls were dead in the demo. */
                  onMapCommand={bus.pushMapCommand}
                />
              </div>
            </div>
          )}
        </>
      )}

      {/* job-0143: Bottom-row Settings + Secrets pills. Hidden when the
          left rail is collapsed (they belong to the rail). job-0278: on
          mobile they fold into the drawer footer instead — the floating
          pills would collide with the bottom-sheet composer. */}
      {!isMobile && !leftCollapsed && (
        <BottomRowButtons
          onOpenSettings={() => setSettingsOpen(true)}
          onOpenSecrets={() => setSecretsOpen(true)}
        />
      )}

      {/* Right panel — Chat stays MOUNTED across collapse so its internal       */}
      {/* state (messages, pipeline history, lastError) is preserved. job-0162: */}
      {/* clicking the chevron-collapse button no longer destroys chat content. */}
      {/* Visually hidden via display:none + aria-hidden when collapsed.        */}
      <div
        data-testid="grace2-chat-mount"
        aria-hidden={rightCollapsed && !isMobile}
        style={{
          // job-0278 — on mobile the chat is always present as the bottom
          // sheet (its own collapsed state IS the minimized form); the
          // desktop right-collapse toggle doesn't apply.
          display: rightCollapsed && !isMobile ? "none" : "contents",
        }}
      >
        {/* job-0266 — activeCaseId selects Chat's visible per-Case stream:
            switching Cases swaps the entire stream; null (root) shows the
            clean empty composer. */}
        <Chat
          wsUrl={WS_URL}
          onClose={collapseRight}
          activeCaseId={activeCaseId}
          mobile={isMobile}
          authEpoch={authEpoch}
          width={chatWidth}
          onWidthChange={setChatWidth}
        />
      </div>

      {/* Layers hamburger — top-LEFT. (Desktop; mobile uses the drawer ☰.) */}
      {!isMobile && showLayersHamburger && (
        <button
          data-testid="grace2-layers-hamburger"
          aria-label="Show layers"
          aria-expanded={false}
          aria-controls="grace2-layer-panel"
          onClick={expandLeft}
          style={{ ...hamburgerBtnStyle, left: 12 }}
        >
          ☰
        </button>
      )}

      {/* Chat hamburger — top-RIGHT. (Desktop only — the mobile sheet is
          always mounted with its own toggle handle.) */}
      {!isMobile && showChatHamburger && (
        <button
          data-testid="grace2-chat-hamburger"
          aria-label="Show chat"
          aria-expanded={false}
          onClick={expandRight}
          style={{ ...hamburgerBtnStyle, right: 12 }}
        >
          ☰
        </button>
      )}

      {/* job-0278 — mobile ☰ opener (top-left, 44px touch target). Hidden
          while the drawer is open (the drawer overlays it anyway). */}
      {isMobile && !mobileDrawerOpen && (
        <MobileDrawerButton
          open={mobileDrawerOpen}
          onClick={() => setMobileDrawerOpen(true)}
        />
      )}

      {/* job-0278 — mobile slide-in drawer. Hosts the SAME left-rail
          content as desktop (CasesPanel at root; CaseView + LayerPanel
          inside a Case) plus the Settings/Secrets pills in its footer.
          Tapping a Case row or the backdrop closes it. */}
      {isMobile && (
        <MobileDrawer
          open={mobileDrawerOpen}
          onClose={() => setMobileDrawerOpen(false)}
        >
          {activeCaseId === null ? (
            <div style={{ flex: 1, minHeight: 0, overflowY: "auto" }}>
              <CasesPanel
                cases={cases}
                activeCaseId={activeCaseId}
                onCreate={onCreateGated}
                onSelect={(caseId) => {
                  selectCase(caseId);
                  setMobileDrawerOpen(false);
                }}
                onRename={onRenameGated}
                onArchive={onArchiveGated}
                onDelete={onDeleteGated}
              />
            </div>
          ) : (
            <>
              {/* job-0284 — mobile: the "Cases" breadcrumb link is the
                  SINGLE back affordance (no ← arrow). */}
              <CaseView
                caseTitle={activeCase?.title ?? "Case"}
                onBack={handleCaseBack}
                mobile
              />
              {layers.length === 0 ? (
                <div
                  data-testid="grace2-case-view-empty-layers"
                  style={{
                    // job-0284 — floats as a translucent hairline card over
                    // the map (the drawer panel surface is gone).
                    background: "rgba(18,19,24,0.72)",
                    border: "1px dashed rgba(255,255,255,0.18)",
                    borderRadius: 10,
                    padding: 12,
                    color: "#a8b0bb",
                    fontSize: 12,
                    textAlign: "center",
                    lineHeight: 1.4,
                    boxSizing: "border-box",
                  }}
                >
                  No layers loaded yet. Ask the assistant to add data.
                </div>
              ) : (
                <div style={{ position: "relative", flex: 1, minHeight: 0 }}>
                  {/* LayerPanel positions itself absolutely (left:16 /
                      top:16 / bottom:16 / width:288) relative to this
                      wrapper — it fills the drawer column. */}
                  <LayerPanel
                    subscribeSessionState={bus.subscribeSessionState}
                    subscribeMapCommand={bus.subscribeMapCommand}
                    initialLayers={layers}
                    onClose={() => setMobileDrawerOpen(false)}
                    onMapCommand={bus.pushMapCommand}
                    mobile
                  />
                </div>
              )}
            </>
          )}
          <div style={{ flex: "0 0 auto", paddingTop: 8 }}>
            <BottomRowButtons
              variant="inline"
              onOpenSettings={() => {
                setMobileDrawerOpen(false);
                setSettingsOpen(true);
              }}
              onOpenSecrets={() => {
                setMobileDrawerOpen(false);
                setSecretsOpen(true);
              }}
            />
          </div>
        </MobileDrawer>
      )}

      {/* Upgrade toast (job-0138 kickoff item 6). Renders below the chat
          hamburger so it doesn't collide with adjacent UI. */}
      {upgradeToast && (
        <div
          data-testid="grace2-upgrade-toast"
          role="status"
          style={{
            position: "absolute",
            top: 56,
            // job-0278 — mobile: anchored near the right edge (the desktop
            // offsets assume the 380px side panel / hamburger, which don't
            // exist on phones and would push the toast off a 390px screen).
            right: isMobile ? 12 : rightCollapsed ? 60 : 380,
            background: "rgba(20,40,60,0.95)",
            border: "1px solid #3b82f6",
            borderRadius: 6,
            color: "#dde6f5",
            padding: "8px 12px",
            fontSize: 12,
            zIndex: 35,
            maxWidth: 280,
          }}
        >
          {upgradeToast}
        </div>
      )}

      {/* hidden marker so tests can assert App subscribes to auth changes */}
      <span
        data-testid="grace2-app-auth-state"
        data-auth-uid={authUser?.uid ?? ""}
        data-auth-anonymous={authUser?.isAnonymous ? "true" : "false"}
        style={{ display: "none" }}
      />
      {/* hidden marker so tests can assert App tracks active Case state */}
      <span
        data-testid="grace2-app-case-state"
        data-active-case-id={activeCaseId ?? ""}
        data-cases-count={String(cases.length)}
        style={{ display: "none" }}
      />

      {/* job-0145: Inline chat cards (payload-warnings + source suggestions)
          stack as a single column anchored over the chat panel — they
          visually sit IN the chat scroll while being mounted at App level
          (Chat owns its own GraceWs). Width matches chat message width
          (chat panel is 380px; cards use 340px with padding). Both surfaces
          use the InlineChatCard primitive for consistent visual language.
          When the chat panel is collapsed, cards still surface so a
          large-payload gate or new source suggestion isn't silently dropped. */}
      <div
        data-testid="inline-chat-card-stack"
        style={{
          position: "absolute",
          // job-0278 — mobile: full-width column with 12px gutters (the
          // desktop 340px column anchored to the chat panel would clip on
          // a 390px screen).
          right: isMobile ? 12 : rightCollapsed ? 16 : 32,
          left: isMobile ? 12 : undefined,
          top: isMobile ? 64 : rightCollapsed ? 64 : 70,
          width: isMobile ? undefined : 340,
          display: "flex",
          flexDirection: "column",
          gap: 8,
          zIndex: 50,
          maxHeight: "calc(100vh - 96px)",
          overflowY: "auto",
          // Wrapper is click-through to the map when there's nothing inside;
          // inner column re-enables pointer events so cards are interactive.
          pointerEvents: "none",
        }}
      >
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 8,
            pointerEvents: "auto",
          }}
        >
          {/* ux-batch-1 J7 (F16): payload-warning gates moved OUT of this
              scrolling top-right stack into the pinned opaque banner "hat"
              below (grace2-payload-warning-banner). Source suggestions stay
              here. */}
          {/* Source-suggestion inline card (job-0145, replaces Mode2OfferModal).
              Listens for candidate envelopes from the server; UI text never
              references the server-internal envelope name. Returns null when
              no candidate is active. */}
          <SourceSuggestionInline
            subscribeCandidate={subscribeSourceSuggestion}
            onAction={handleSourceSuggestionAction}
          />
        </div>
      </div>

      {/* ux-batch-1 J7 (F16) — payload-warning BANNER "hat". The gate is
          pinned as an opaque banner over the TOP of the chat column (not in
          the scroll, so it can never scroll out of view), aligned to the chat
          width, and disappears the moment the user answers it. Mounted at App
          level because the warning arrives on App's GraceWs. Desktop: hugs the
          chat panel (right:16, width = chatWidth). Mobile / chat collapsed:
          falls back to a top, near-full-width banner so it's never lost. */}
      {visiblePayloadWarnings.length > 0 && (
        <div
          data-testid="grace2-payload-warning-banner"
          style={{
            position: "absolute",
            top: isMobile ? 12 : 16,
            right: isMobile ? 12 : 16,
            left: isMobile ? 12 : undefined,
            width: isMobile
              ? undefined
              : rightCollapsed
                ? 360
                : `min(${chatWidth}px, 92vw)`,
            display: "flex",
            flexDirection: "column",
            gap: 8,
            zIndex: 60,
            // Opaque so it reads as a solid banner, not a see-through overlay
            // (F16: "should also not be transparent").
            background: "#15171f",
            border: "1px solid rgba(255,255,255,0.10)",
            borderRadius: 12,
            boxShadow: "0 8px 32px rgba(0,0,0,0.55)",
            padding: 8,
            pointerEvents: "auto",
            // Bound the banner height if many gates stack; rare, but keep it
            // from overrunning the viewport.
            maxHeight: "calc(100vh - 32px)",
            overflowY: "auto",
          }}
        >
          {/* Newest first so a fresh gate sits at the top of the banner.
              job-0266: filtered to the visible Case's warnings only. */}
          {visiblePayloadWarnings.map(({ warning: w }) => (
            <PayloadWarningInline
              key={w.warning_id}
              warning={w}
              onDecide={(decision, revised) =>
                handlePayloadWarningDecide(w.warning_id, decision, revised)
              }
            />
          ))}
        </div>
      )}
      {/* Legacy data-testid hook (job-0127 → job-0145): keep the
          `payload-warning-stack` test id reachable so existing App / e2e
          tests continue to find the column. Mounted only when at least one
          warning is active, mirroring the prior conditional-render. */}
      {visiblePayloadWarnings.length > 0 && (
        <span
          data-testid="payload-warning-stack"
          aria-hidden="true"
          style={{ display: "none" }}
        />
      )}

      {/* job-0143: Settings popup (full-screen overlay). */}
      {settingsOpen && (
        <SettingsPopup
          userEmail={authUser?.email ?? null}
          isSignedIn={isSignedIn}
          theme={theme}
          onToggleTheme={toggleTheme}
          onSignOut={() => {
            void handleSignOut();
            setSettingsOpen(false);
          }}
          onSignInRequest={() => {
            handleSignInRequest();
            setSettingsOpen(false);
          }}
          onClose={() => setSettingsOpen(false)}
          onOpenToolsCatalog={() => {
            setSettingsOpen(false);
            setToolsCatalogOpen(true);
          }}
          onOpenRoutingDashboard={() => {
            setSettingsOpen(false);
            setRoutingDashOpen(true);
          }}
        />
      )}

      {/* Wave 4.10 C1: Tools catalog popup (full-screen overlay). */}
      {toolsCatalogOpen && (
        <ToolsCatalogPopup onClose={() => setToolsCatalogOpen(false)} />
      )}

      {/* Wave 4.11 M7: Routing-quality dashboard (full-screen overlay). */}
      {routingDashOpen && (
        <RoutingQualityDashboard
          onClose={() => {
            setRoutingDashOpen(false);
            setRoutingDashInjected(null);
          }}
          initialSummary={routingDashInjected}
        />
      )}

      {/* Wave 4.11 P4: ImpactEnvelope side panel. Surfaces whenever a
          compute_impact_envelope tool result has populated impactEnvelope. */}
      {impactEnvelope && (
        <ImpactPanel
          envelope={impactEnvelope}
          caseName={activeCase?.title ?? null}
          onClose={() => setImpactEnvelope(null)}
        />
      )}

      {/* job-0143: Secrets popup (full-screen overlay). */}
      {secretsOpen && (
        <SecretsPopup
          secrets={secrets}
          caseId={currentCaseId}
          onSecretAdd={handleSecretAdd}
          onSecretRevoke={handleSecretRevoke}
          onClose={() => setSecretsOpen(false)}
        />
      )}

      {/* job-0143: SaveGateModal — appears only when an anonymous user
          attempts a save-triggering action. */}
      {saveGate.isOpen && (
        <SaveGateModal
          pendingKind={saveGate.pendingKind}
          onSignIn={saveGate.requestSignIn}
          onContinueAnyway={saveGate.confirmContinue}
          onDismiss={saveGate.dismiss}
        />
      )}
    </div>
    </AuthGuard>
  );
}
